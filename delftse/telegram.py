"""Read deep-link feedback back from Telegram (adapted from LearnX-Radar).

No webhook: the bot owns incoming /start messages for ~24h. The trainer's buttons
deep-link to t.me/<bot>?start=<payload>; one tap sends that as /start from the owner's
own account. This calls getUpdates, keeps Delftse payloads from the OWNER chat, then
acks the batch (advances the offset) so the next run starts clean.

Delftse payloads (distinct from Radar's dr_/rv_/lr_ on purpose):
  db_<NN>_<marks>      — chapter recall ("Resultaten opslaan"); NN = chapter, marks
                         positional over that chapter's report.words (1/0/x).
  dv_<YYMMDD>_<marks>  — cross-chapter review ("Herhaling opslaan").

NOTE (shared bot): acking drops ALL pending updates, including Radar's. If both apps
consume this same bot, they race — keep only one consumer active (see README).
"""
import base64
import re
from datetime import datetime

import requests

import config

_GET = "https://api.telegram.org/bot{token}/getUpdates"
_SEND = "https://api.telegram.org/bot{token}/sendMessage"
# Chapter is 2 digits for the book (01..43) and 3 for YouTube lessons (101+).
_RECALL = re.compile(r"^/start\s+db_(\d{2,3})_([01xb]+)$")
_REVIEW = re.compile(r"^/start\s+dv_(\d{6})_([01xb]+)$")
# Full-test report: dt_<chap>_<vocabMarks>_<R>-<W>-<B>_<wrongWordsB64> (b64 optional).
_REPORT = re.compile(r"^/start\s+dt_(\d{2,3})_([01xb]+)_(\d+)-(\d+)-(\d+)(?:_([A-Za-z0-9_-]+))?$")


def _b64url_decode(s: str) -> list[str]:
    """Decode the packed wrong-answer list (URL-safe base64 of forms joined by '|')."""
    if not s:
        return []
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return []
    return [w for w in raw.split("|") if w]


def send(text: str) -> bool:
    """Push a short message to the owner chat (the sync runner's daily heartbeat).

    Best-effort: returns False (never raises) if the keys are missing or Telegram
    errors, so a failed notification can't fail the sync. HTML parse mode so links
    render. Disables the link preview to keep the message compact."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return False
    try:
        resp = requests.post(
            _SEND.format(token=config.TELEGRAM_BOT_TOKEN),
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30)
        resp.raise_for_status()
        return True
    except requests.RequestException:
        return False


def _parse_date(yymmdd: str) -> str | None:
    try:
        return datetime.strptime(yymmdd, "%y%m%d").date().isoformat()
    except ValueError:
        return None


def fetch_inbound() -> dict:
    """Collect pending owner taps in one acknowledged batch.

    Returns {"recall": [(chapter_int, marks), ...],
             "report": [(chapter_int, vocab_marks, R, W, B, [wrong_forms]), ...],
             "review": [(date_iso, marks), ...], "last_id": int}.
    One entry per key — the LAST tap in the batch wins (a re-study supersedes)."""
    empty: dict = {"recall": [], "report": [], "review": [], "last_id": 0}
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return empty
    resp = requests.get(_GET.format(token=config.TELEGRAM_BOT_TOKEN),
                        params={"timeout": 0, "allowed_updates": '["message"]'},
                        timeout=30)
    resp.raise_for_status()
    updates = resp.json().get("result", [])
    if not updates:
        return empty

    owner = str(config.TELEGRAM_CHAT_ID)
    recall: dict[int, str] = {}
    report: dict[int, tuple] = {}
    review: dict[str, str] = {}
    for u in updates:
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != owner:
            continue
        text = (msg.get("text") or "").strip()
        if m := _REPORT.match(text):
            report[int(m.group(1))] = (m.group(2), int(m.group(3)), int(m.group(4)),
                                       int(m.group(5)), _b64url_decode(m.group(6) or ""))
        elif m := _RECALL.match(text):
            recall[int(m.group(1))] = m.group(2)
        elif m := _REVIEW.match(text):
            if d := _parse_date(m.group(1)):
                review[d] = m.group(2)

    last_id = max(u.get("update_id", 0) for u in updates)
    # Acknowledge: a confirming call with offset past the newest id drops the batch.
    requests.get(_GET.format(token=config.TELEGRAM_BOT_TOKEN),
                 params={"offset": last_id + 1, "timeout": 0, "limit": 1}, timeout=30)
    return {"recall": sorted(recall.items()),
            "report": [(ch, *vals) for ch, vals in sorted(report.items())],
            "review": sorted(review.items()), "last_id": last_id}