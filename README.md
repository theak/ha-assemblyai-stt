# AssemblyAI Speech-to-Text for Home Assistant

Use [AssemblyAI](https://www.assemblyai.com/)'s streaming speech recognition for Assist voice
requests. Audio is streamed to AssemblyAI while you speak, so the text is usually ready the moment
your voice assistant stops listening.

On 123 real voice requests from a Voice PE and a ReSpeaker satellite, Universal-3.6 Pro Realtime
had a 1.1% word error rate and one request that Home Assistant would have handled differently,
compared with 5.2% and nine for Google Cloud's `latest_long` model. Your results will depend on
your voice, microphones and device names.

## Install with HACS

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/theak/ha-assemblyai-stt` with
   type **Integration**.
2. Download **AssemblyAI Speech-to-Text** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → AssemblyAI Speech-to-Text** and enter
   your AssemblyAI API key.
4. **Settings → Voice assistants**, open your assistant and set **Speech-to-text** to
   **AssemblyAI**.

To change the API key later, use **Reconfigure** on the integration.

To install by hand instead, copy `custom_components/assemblyai_stt` into your
`config/custom_components/` folder and continue from step 2.

## Options

Use **Configure** on the integration to change these.

| Option | What it does |
| --- | --- |
| **Model** | **Universal-3.6 Pro Realtime** ($0.45/hour of streaming, the most accurate) or **Universal-Streaming English** ($0.15/hour). A typical request is a few seconds, so either costs well under a cent. |
| **Keyterms** | Optional words to recognize more reliably, one per line, up to 100. **A small, focused list works better than a long one**: in testing, "living room" alone fixed a misheard "turn on all living room", while a 43-word list of every device and room didn't. Start with your room names and the few device names you say most. Free on Universal-3.6 Pro; $0.04/hour extra on Universal-Streaming English. |
| **Fallback speech-to-text** | Another speech-to-text engine you already have (for example Google Cloud) to use when AssemblyAI can't be reached, rejects the request or doesn't answer within 3 seconds. The audio heard so far is passed to it, so you don't have to repeat yourself. |
| **Stop listening when AssemblyAI hears the end of the sentence** | Off by default: the voice assistant listens until it detects silence, as with other engines. On: the text is returned as soon as AssemblyAI decides you've finished, which can be quicker but may cut off a request if you pause mid-sentence. |

## Limits and costs

- AssemblyAI bills streaming by how long the connection is open. The integration opens it when
  your voice assistant starts listening and closes it as soon as the text is final.
- AssemblyAI limits how many streaming sessions an account can start per minute: 5 on a free
  account, 100 or more once billing is set up
  ([rate limits](https://www.assemblyai.com/docs/streaming/rate-limits)). Each voice request is
  one session, so a free account will reject a sixth request within a minute; with a fallback
  engine set, that request goes to the fallback instead.
- Only 16-bit mono PCM audio is accepted, which is what Home Assistant's voice pipeline sends.

## Testing the fallback

The `assemblyai_stt.simulate_failure` action makes the next requests fail before reaching
AssemblyAI. Run it with `count: 1`, then ask your voice assistant something: you should get a
normal answer, a warning in the log, and an `assemblyai_stt_fallback` event.

```yaml
action: assemblyai_stt.simulate_failure
data:
  count: 1
```

Each fallback fires `assemblyai_stt_fallback` with `reason` and `fallback` (the engine used),
so you can count them in an automation.
