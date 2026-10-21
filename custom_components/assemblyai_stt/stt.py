"""AssemblyAI streaming speech-to-text entity, with an optional fallback to another engine."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
import json
import logging
import time
from urllib.parse import urlencode

import aiohttp

from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .config_flow import parse_keyterms
from .const import (
    CONF_FALLBACK,
    CONF_FINISH_ON_END_OF_TURN,
    CONF_KEYTERMS,
    CONF_MODEL,
    CONNECT_TIMEOUT,
    DEFAULT_MODEL,
    DOMAIN,
    EVENT_FALLBACK,
    FINAL_TEXT_TIMEOUT,
    MAX_CHUNK_SECONDS,
    MIN_CHUNK_SECONDS,
    MODELS,
    STREAMING_URL,
)

_LOGGER = logging.getLogger(__name__)

LANGUAGES = ["en", "en-AU", "en-CA", "en-GB", "en-IE", "en-IN", "en-NZ", "en-US", "en-ZA"]
SAMPLE_RATES = {
    stt.AudioSampleRates.SAMPLERATE_8000: 8000,
    stt.AudioSampleRates.SAMPLERATE_16000: 16000,
    stt.AudioSampleRates.SAMPLERATE_44100: 44100,
    stt.AudioSampleRates.SAMPLERATE_48000: 48000,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the speech-to-text entity."""
    async_add_entities([AssemblyAISpeechToText(entry)])


def strip_wav_header(chunk: bytes) -> bytes:
    """Drop a RIFF/WAV header if the audio starts with one (HTTP uploads do; the pipeline sends raw PCM)."""
    if chunk[:4] == b"RIFF" and chunk[8:12] == b"WAVE" and (i := chunk.find(b"data", 12)) != -1:
        return chunk[i + 8 :]
    return chunk


class AssemblyAIError(Exception):
    """AssemblyAI could not produce a transcript."""


class _Session:
    """One streaming session: sends audio, collects final turns."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self.turns: dict[int, str] = {}  # turn_order -> final formatted text
        self.open_turn: int | None = None  # a turn with words that isn't final yet
        self.error: str | None = None
        self.changed = asyncio.Event()

    async def read(self) -> None:
        async for msg in self.ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            data = json.loads(msg.data)
            kind = data.get("type")
            if kind == "Turn":
                order = data.get("turn_order", 0)
                text = (data.get("transcript") or "").strip()
                if data.get("end_of_turn") and data.get("turn_is_formatted", True):
                    if text:
                        self.turns[order] = text
                    if self.open_turn == order:
                        self.open_turn = None
                elif text:
                    self.open_turn = order
            elif kind == "Termination":
                break
            elif kind == "Error" or "error" in data:
                self.error = str(data.get("error") or data)
                break
            self.changed.set()
        if self.ws.close_code not in (None, 1000) and not self.error:
            self.error = f"connection closed ({self.ws.close_code})"
        self.changed.set()

    @property
    def settled(self) -> bool:
        """True when every turn with words has its final text."""
        return self.open_turn is None

    @property
    def text(self) -> str:
        return " ".join(self.turns[k] for k in sorted(self.turns)).strip()


class AssemblyAISpeechToText(stt.SpeechToTextEntity):
    """Streams audio to AssemblyAI while the user speaks."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="AssemblyAI",
            manufacturer="AssemblyAI",
            model=MODELS.get(entry.options.get(CONF_MODEL, DEFAULT_MODEL), entry.options.get(CONF_MODEL)),
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str]:
        return LANGUAGES

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        return list(SAMPLE_RATES)

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> stt.SpeechResult:
        """Stream the audio to AssemblyAI; on failure, pass the recorded audio to the fallback engine."""
        options = self._entry.options
        rate = SAMPLE_RATES[metadata.sample_rate]
        min_bytes = int(rate * 2 * MIN_CHUNK_SECONDS)
        max_bytes = int(rate * 2 * MAX_CHUNK_SECONDS)
        audio = bytearray()  # everything heard, kept for the fallback
        pending = bytearray()  # not yet sent; AssemblyAI wants 50 ms to 1 s per message
        session: _Session | None = None
        reader: asyncio.Task | None = None
        failure: str | None = None
        started = time.monotonic()

        try:
            session, reader = await self._connect(rate, metadata)
        except AssemblyAIError as err:
            failure = str(err)

        async def send(data: bytes) -> None:
            nonlocal failure
            if session is None or failure:
                return
            try:
                await session.ws.send_bytes(data)
            except (aiohttp.ClientError, ConnectionResetError, RuntimeError) as err:
                failure = f"send failed: {err}"

        finished_early = False
        first = True
        async for chunk in stream:
            if first:
                chunk, first = strip_wav_header(chunk), False
            audio += chunk
            pending += chunk
            while len(pending) >= min_bytes:
                await send(bytes(pending[:max_bytes]))
                del pending[:max_bytes]
            if session and session.error and not failure:
                failure = session.error
            if (
                options.get(CONF_FINISH_ON_END_OF_TURN)
                and session
                and not failure
                and session.turns
                and session.settled
            ):
                finished_early = True  # AssemblyAI heard the end of the sentence; stop listening now
                break

        text = ""
        if session and not failure:
            if pending and not finished_early:
                await send(bytes(pending).ljust(min_bytes, b"\0"))
            if not finished_early:
                try:
                    await session.ws.send_str(json.dumps({"type": "ForceEndpoint"}))
                except (aiohttp.ClientError, ConnectionResetError, RuntimeError) as err:
                    failure = f"send failed: {err}"
            deadline = time.monotonic() + FINAL_TEXT_TIMEOUT
            while not failure:
                session.changed.clear()  # clear before checking, so a message arriving now isn't missed
                if session.settled or reader.done():
                    break
                try:
                    await asyncio.wait_for(session.changed.wait(), max(deadline - time.monotonic(), 0))
                except TimeoutError:
                    failure = "timed out waiting for the final text"
            failure = failure or session.error
            text = session.text
        if session:
            self.hass.async_create_background_task(self._close(session, reader), f"{DOMAIN} close session")

        if not failure:
            _LOGGER.debug("Transcribed in %.2f s: %r", time.monotonic() - started, text)
            return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)  # "" when nothing was said
        return await self._fallback(metadata, bytes(audio), failure, text)

    async def _connect(self, rate: int, metadata: stt.SpeechMetadata) -> tuple[_Session, asyncio.Task]:
        options = self._entry.options
        params = {
            "sample_rate": rate,
            "encoding": "pcm_s16le",
            "speech_model": options.get(CONF_MODEL, DEFAULT_MODEL),
            "format_turns": "true",
        }
        if keyterms := parse_keyterms(options.get(CONF_KEYTERMS)):
            params["keyterms_prompt"] = json.dumps(keyterms)
        state = self.hass.data.get(DOMAIN, {})
        if state.get("simulated_failures", 0) > 0:
            state["simulated_failures"] -= 1
            raise AssemblyAIError("simulated failure")
        try:
            ws = await asyncio.wait_for(
                async_get_clientsession(self.hass).ws_connect(
                    f"{STREAMING_URL}?{urlencode(params)}",
                    headers={"Authorization": self._entry.data[CONF_API_KEY]},
                ),
                CONNECT_TIMEOUT,
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                self._entry.async_start_reauth(self.hass)
            raise AssemblyAIError(f"could not connect: HTTP {err.status}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AssemblyAIError(f"could not connect: {err!r}") from err
        session = _Session(ws)
        return session, self.hass.async_create_background_task(session.read(), f"{DOMAIN} read")

    async def _close(self, session: _Session, reader: asyncio.Task | None) -> None:
        """End the session cleanly so it stops counting against the concurrent-session limit."""
        try:
            if not session.ws.closed:
                await session.ws.send_str(json.dumps({"type": "Terminate"}))
            if reader:
                await asyncio.wait_for(asyncio.shield(reader), 5)
        except (aiohttp.ClientError, ConnectionResetError, RuntimeError, TimeoutError):
            pass
        finally:
            if reader and not reader.done():
                reader.cancel()
            await session.ws.close()

    async def _fallback(
        self, metadata: stt.SpeechMetadata, audio: bytes, reason: str, partial_text: str
    ) -> stt.SpeechResult:
        engine_id = self._entry.options.get(CONF_FALLBACK)
        engine = stt.async_get_speech_to_text_entity(self.hass, engine_id) if engine_id else None
        self.hass.bus.async_fire(EVENT_FALLBACK, {"entity_id": self.entity_id, "reason": reason, "fallback": engine_id})
        if engine is None or engine is self or not engine.check_metadata(metadata):
            _LOGGER.warning("AssemblyAI failed (%s) and no usable fallback engine is set", reason)
            if partial_text:  # better than nothing
                return stt.SpeechResult(partial_text, stt.SpeechResultState.SUCCESS)
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
        _LOGGER.warning("AssemblyAI failed (%s); using %s instead", reason, engine_id)

        async def replay() -> AsyncIterator[bytes]:
            for i in range(0, len(audio), 4096):
                yield audio[i : i + 4096]

        return await engine.internal_async_process_audio_stream(metadata, replay())
