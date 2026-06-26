"""Build a learner's cross-chapter review (the 🔁 herhaling tab's data).

For each word DUE today (delftse.srs.due_words), pull a real example sentence + audio
span from the word's own chapter JSON, so the page can drill it with no extra TTS.
The published review/<token>.json is canonical "what's due"; the page caches attempts.
Audio plays from delftse-<chapter>.mp3 (same-origin, so the page fetches it directly).
"""
import json
import re
from datetime import date
from pathlib import Path

from delftse import srs


def _chapter_json(trainer_dir: Path, ch: int, cache: dict) -> dict:
    if ch not in cache:
        p = trainer_dir / f"delftse-{ch}.json"
        try:
            cache[ch] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache[ch] = {}
    return cache[ch]


def _drill_for(cj: dict, form: str) -> dict:
    """A segment containing the word form, with its audio span — what the page
    blanks-and-replays. Prefer the curated block-A example sentence, then any real
    sentence, and among those the most CONCISE (a short example beats a long row)."""
    pat = re.compile(rf"\b{re.escape(form)}\w*", re.IGNORECASE)
    best = None
    for seg in cj.get("segments", []):
        nl = seg.get("nl", "")
        if not pat.search(nl):
            continue
        n = len(nl.split())
        # tier: 0 = block-A sentence, 1 = any sentence (>=3 words), 2 = bare word;
        # then fewer words wins (concise). Lower sorts first.
        tier = 0 if (seg.get("block") == "A" and n >= 3) else (1 if n >= 3 else 2)
        key = (tier, n)
        if best is None or key < best[0]:
            best = (key, seg)
    if not best:
        return {}
    seg = best[1]
    return {"sentence_nl": seg.get("nl", ""), "sentence_en": seg.get("en", ""),
            "audio_url": f"delftse-{cj.get('chapter')}.mp3",
            "start_ms": seg.get("start_ms", 0), "end_ms": seg.get("end_ms", 0)}


def build(memory: dict, trainer_dir: Path, *, today: date | None = None,
          max_items: int = 12) -> dict:
    """A learner's review payload. `ids` is the report contract — a dv_<date>_<marks>
    deep link reports one mark per position over THIS order."""
    today = today or date.today()
    cache: dict = {}
    items: list[dict] = []
    for wid in srs.due_words(memory, today):
        e = memory["words"].get(wid, {})
        ch = e.get("chapter")
        form = e.get("form", wid)
        cj = _chapter_json(trainer_dir, ch, cache)
        nw = next((w for w in cj.get("new_words", []) if w.get("id") == wid), {})
        item = {"id": wid, "nl": nw.get("nl", form), "en": nw.get("en", ""), "form": form}
        item.update(_drill_for(cj, form))
        items.append(item)
        if len(items) >= max_items:
            break
    return {"generated": today.isoformat(), "items": items,
            "ids": [it["id"] for it in items]}