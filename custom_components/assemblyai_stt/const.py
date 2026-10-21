"""Constants for AssemblyAI Speech-to-Text."""

DOMAIN = "assemblyai_stt"

CONF_MODEL = "model"
CONF_KEYTERMS = "keyterms"
CONF_FALLBACK = "fallback_stt"
CONF_FINISH_ON_END_OF_TURN = "finish_on_end_of_turn"

# Streaming models, best first. Keyterms are included in the Universal-3.6 Pro price and
# cost extra on Universal-Streaming English.
MODELS = {
    "universal-3-6-pro": "Universal-3.6 Pro Realtime ($0.45/hour)",
    "universal-streaming-english": "Universal-Streaming English ($0.15/hour)",
}
DEFAULT_MODEL = "universal-3-6-pro"
MAX_KEYTERMS = 100

STREAMING_URL = "wss://streaming.assemblyai.com/v3/ws"
# A cheap authenticated call, used to check the API key during setup.
CHECK_KEY_URL = "https://api.assemblyai.com/v2/transcript?limit=1"

# AssemblyAI accepts audio messages of 50 ms to 1 s.
MIN_CHUNK_SECONDS = 0.05
MAX_CHUNK_SECONDS = 1.0

CONNECT_TIMEOUT = 5
# How long to wait for the final text once the audio has ended.
FINAL_TEXT_TIMEOUT = 3

EVENT_FALLBACK = f"{DOMAIN}_fallback"
