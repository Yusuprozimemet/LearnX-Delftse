"""Central config for LearnX-Delftse — self-contained, independent of LearnX-Radar.

Loads `.env` locally (see .env.example); in CI the same names come from GitHub
secrets. Nothing here imports Radar. Defaults that aren't secret (voices, rates,
spaced-repetition spacing) live inline so the app runs with only the keys filled in.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load THIS app's .env (next to config.py), independent of the caller's cwd.
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ModuleNotFoundError:
    pass


def _b(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# --- LLM: NVIDIA NIM primary, optional Groq fallback (both OpenAI-compatible) ----
NVIDIA_API_KEY = _b("NVIDIA_API_KEY")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "meta/llama-3.1-70b-instruct"
GROQ_API_KEY = _b("GROQ_API_KEY")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"

# --- Telegram (the personalization loop). Reusing Radar's bot is fine — same token.
TELEGRAM_BOT_TOKEN = _b("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_USERNAME = _b("TELEGRAM_BOT_USERNAME", "Prakly_notification_bot")
TELEGRAM_CHAT_ID = _b("TELEGRAM_CHAT_ID")
# HMAC secret naming the published review/progress files (review/<token>.json).
REVIEW_TOKEN_SECRET = _b("REVIEW_TOKEN_SECRET", "delftse-dev-secret")

# --- Audio (edge-tts Dutch voices — no API key needed) ---------------------------
DUTCH_VOICE_ALEX = "nl-NL-MaartenNeural"   # speaker A
DUTCH_VOICE_MAYA = "nl-NL-ColetteNeural"   # speaker B
DUTCH_TTS_RATE = "-10%"
DUTCH_DELFT_PAUSE_FACTOR = 1.5             # repeat-pause = 1.5x the sentence duration
DUTCH_DELFT_MIN_PAUSE_MS = 1200            # floor so one-word lines still leave time
SILENCE_BREATH_MS = 150                    # same speaker, consecutive lines
SILENCE_TURN_MS = 450                      # speaker change within a block
SILENCE_UNIT_MS = 1000                     # between blocks (A/B/C)
TTS_SEMAPHORE_LIMIT = 8                    # concurrent edge-tts renders

# --- Spaced repetition (the sync runner's scheduling) ----------------------------
DUTCH_SR_BASE_INTERVAL_DAYS = 1
DUTCH_SR_SPACING_FACTOR = 2.2
DUTCH_CEFR_START = "A2"                     # the Delftse book1 level
DUTCH_REVIEW_MAX = 12                       # max words in a herhaling session

# --- Audio publishing (optional fallback URL for a chapter mp3) -------------------
AUDIO_BASE = _b("AUDIO_BASE")


def require(*names: str) -> None:
    """Fail loudly if a needed secret is missing — called by the task that needs it
    (regeneration needs the LLM key; sync needs the Telegram ones), not at import."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit("Missing required config: " + ", ".join(missing)
                         + " — set them in .env (see .env.example).")