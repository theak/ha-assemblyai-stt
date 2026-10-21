"""Config flow for AssemblyAI Speech-to-Text: the API key, then model, keyterms and fallback."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CHECK_KEY_URL,
    CONF_FALLBACK,
    CONF_FINISH_ON_END_OF_TURN,
    CONF_KEYTERMS,
    CONF_MODEL,
    DEFAULT_MODEL,
    DOMAIN,
    MAX_KEYTERMS,
    MODELS,
)


def parse_keyterms(text: str | None) -> list[str]:
    """One keyterm per line (commas also work); blanks and duplicates are dropped."""
    terms: list[str] = []
    for line in (text or "").replace(",", "\n").splitlines():
        term = line.strip()
        if term and term.lower() not in {t.lower() for t in terms}:
            terms.append(term)
    return terms


async def _check_key(hass: HomeAssistant, api_key: str) -> str | None:
    """Return an error key, or None when AssemblyAI accepts the key."""
    try:
        async with async_get_clientsession(hass).get(
            CHECK_KEY_URL, headers={"Authorization": api_key}, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status in (401, 403):
                return "invalid_auth"
            if resp.status >= 400:
                return "cannot_connect"
    except (aiohttp.ClientError, TimeoutError):
        return "cannot_connect"
    return None


class AssemblyAIConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add AssemblyAI with an API key."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (error := await _check_key(self.hass, user_input[CONF_API_KEY].strip())):
                return self.async_create_entry(
                    title="AssemblyAI",
                    data={CONF_API_KEY: user_input[CONF_API_KEY].strip()},
                    options={CONF_MODEL: DEFAULT_MODEL, CONF_KEYTERMS: "", CONF_FINISH_ON_END_OF_TURN: False},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_API_KEY): selector.TextSelector(selector.TextSelectorConfig(type="password"))}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (error := await _check_key(self.hass, user_input[CONF_API_KEY].strip())):
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: user_input[CONF_API_KEY].strip()}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_API_KEY): selector.TextSelector(selector.TextSelectorConfig(type="password"))}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AssemblyAIOptionsFlow:
        return AssemblyAIOptionsFlow()


class AssemblyAIOptionsFlow(OptionsFlowWithReload):
    """Choose the model, keyterms, fallback engine and when to stop listening."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = dict(self.config_entry.options)
        if user_input is not None:
            current = user_input
            if len(parse_keyterms(user_input.get(CONF_KEYTERMS))) > MAX_KEYTERMS:
                errors[CONF_KEYTERMS] = "too_many_keyterms"
            else:
                return self.async_create_entry(
                    data={
                        CONF_MODEL: user_input[CONF_MODEL],
                        CONF_KEYTERMS: "\n".join(parse_keyterms(user_input.get(CONF_KEYTERMS))),
                        CONF_FALLBACK: user_input.get(CONF_FALLBACK) or None,
                        CONF_FINISH_ON_END_OF_TURN: user_input.get(CONF_FINISH_ON_END_OF_TURN, False),
                    }
                )

        # The fallback can't be this integration's own entity.
        own = [
            e.entity_id
            for e in er.async_entries_for_config_entry(er.async_get(self.hass), self.config_entry.entry_id)
        ]
        fallback = current.get(CONF_FALLBACK)
        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL, default=current.get(CONF_MODEL, DEFAULT_MODEL)): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=k, label=v) for k, v in MODELS.items()],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_KEYTERMS, default=current.get(CONF_KEYTERMS, "")): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Optional(
                    CONF_FALLBACK, description={"suggested_value": fallback} if fallback else None
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="stt", exclude_entities=own)),
                vol.Required(
                    CONF_FINISH_ON_END_OF_TURN, default=current.get(CONF_FINISH_ON_END_OF_TURN, False)
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
