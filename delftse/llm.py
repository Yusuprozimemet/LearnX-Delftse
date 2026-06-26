"""LLM client: NVIDIA NIM primary, optional Groq fallback (both OpenAI-compatible).
Vendored from LearnX-Radar learnx/llm.py; reads LearnX-Delftse's own config.

NVIDIA's free NIM endpoints stall intermittently. When GROQ_API_KEY is set, chat()
exhausts NVIDIA's retries then transparently retries on Groq. With no Groq key it's
NVIDIA-only. Clients are built lazily so importing never requires a key.
"""
import json
import logging
import re
import time

from openai import APITimeoutError, OpenAI

import config

log = logging.getLogger(__name__)

_RETRY_COUNT = 3
_RETRY_DELAY_S = 2.0
_TIMEOUT_S = 120.0

# Circuit breaker: once NVIDIA has timed out this many times in a process, stop trying
# it and use Groq for the rest (process-global; a fresh `python` run resets it).
_NVIDIA_TRIP_AFTER = 2
_nvidia_timeouts = 0
_nvidia_tripped = False
_clients: dict[str, OpenAI] = {}


def _client_for(base_url: str, api_key: str) -> OpenAI:
    if base_url not in _clients:
        _clients[base_url] = OpenAI(api_key=api_key, base_url=base_url,
                                    timeout=_TIMEOUT_S, max_retries=0)
    return _clients[base_url]


def _providers() -> list[tuple[str, str, str, str]]:
    nvidia = ("nvidia", config.NVIDIA_BASE_URL, config.NVIDIA_API_KEY, config.NVIDIA_MODEL)
    groq = (("groq", config.GROQ_BASE_URL, config.GROQ_API_KEY, config.GROQ_MODEL)
            if config.GROQ_API_KEY else None)
    if _nvidia_tripped and groq:
        return [groq]
    return [nvidia, groq] if groq else [nvidia]


def chat(messages: list[dict[str, str]], *, temperature: float = 0.7,
         max_tokens: int = 2000) -> str:
    """One chat completion with retry/backoff, falling back across providers."""
    global _nvidia_timeouts, _nvidia_tripped
    errors: list[str] = []
    for label, base_url, api_key, model in _providers():
        client = _client_for(base_url, api_key)
        for attempt in range(_RETRY_COUNT):
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages,  # type: ignore[arg-type]
                    temperature=temperature, max_tokens=max_tokens)
                content = resp.choices[0].message.content
                assert content is not None, "LLM returned empty content"
                if label != "nvidia":
                    log.warning("LLM served by fallback provider: %s", label)
                return content
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status in (400, 401, 403):
                    errors.append(f"{label}: auth/request error ({status}): {exc}")
                    break
                if label == "nvidia" and isinstance(exc, APITimeoutError):
                    _nvidia_timeouts += 1
                    if _nvidia_timeouts >= _NVIDIA_TRIP_AFTER and config.GROQ_API_KEY:
                        _nvidia_tripped = True
                        errors.append(f"{label}: {exc}")
                        break
                if attempt < _RETRY_COUNT - 1:
                    log.warning("LLM call to %s failed (%s), retrying in %.1fs",
                                label, exc, _RETRY_DELAY_S)
                    time.sleep(_RETRY_DELAY_S)
                    continue
                errors.append(f"{label}: {exc}")
    raise RuntimeError("LLM call failed across all providers: " + " | ".join(errors))


def parse_json_response(raw: str) -> object:
    """Best-effort extraction of a JSON value from a (possibly fenced) reply."""
    text = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from response: {raw[:200]}")