"""Compose the trainer's lesson JSON — vendored from LearnX-Radar dutch/trainer.py.

Pure composition (no I/O, no LLM): the timings come from the audio render and the
translations are looked up by exact Dutch text. Adapted for LearnX-Delftse: audio_url
falls back to config.AUDIO_BASE (the page serves the same-origin mp3 first anyway),
and report.bot is the configured bot username (the recall deep-link target).
"""
from datetime import date

import config
from delftse import cloze

# audio unit numbers -> Delft block letters (see delftse.audio.to_lines):
_BLOCKS = {0: "A", 1: "B", 2: "C"}


def build_payload(lesson, timings: list[dict], audio_filename: str,
                  when: date | None = None) -> dict:
    """The trainer page's lesson JSON: text + translations + cloze + a seek map."""
    en_by_nl: dict[str, str] = {}
    for w in lesson.new_words + lesson.review_words:
        if w.get("nl"):
            en_by_nl[w["nl"]] = w.get("en", "")
    for s in lesson.sentences:
        if s.get("nl"):
            en_by_nl[s["nl"]] = s.get("en", "")
    for d in lesson.dialogue:
        if d.get("nl"):
            en_by_nl[d["nl"]] = d.get("en", "")

    segments = [
        {
            "block": _BLOCKS.get(t.get("unit"), "A"),
            "speaker": t.get("speaker", ""),
            "nl": t.get("text", ""),
            "en": en_by_nl.get(t.get("text", ""), ""),
            "start_ms": t.get("start_ms", 0),
            "end_ms": t.get("end_ms", 0),
        }
        for t in timings
    ]

    def span(block: str) -> dict | None:
        rows = [s for s in segments if s["block"] == block]
        return {"start_ms": rows[0]["start_ms"], "end_ms": rows[-1]["end_ms"]} if rows else None

    audio_url = f"{config.AUDIO_BASE}/{audio_filename}" if config.AUDIO_BASE else audio_filename
    return {
        "date": (when or date.today()).isoformat(),
        "theme": lesson.theme,
        "cefr": lesson.cefr,
        "audio_url": audio_url,
        "new_words": lesson.new_words,
        "sentences": lesson.sentences,
        "dialogue": lesson.dialogue,
        "cloze": cloze.extract(lesson.new_words, lesson.sentences, lesson.dialogue),
        "luistertoets": cloze.sentence_blanks(
            lesson.new_words + lesson.review_words, lesson.dialogue),
        "segments": segments,
        "block_b": span("B"),
        "block_c": span("C"),
        # Recall feedback contract: one {id, form} per word, in this exact order, so the
        # page's db_ deep link can report one positional mark per word and the sync can
        # map positions back to ids (Telegram caps /start payloads at 64 chars).
        "report": {
            "bot": config.TELEGRAM_BOT_USERNAME,
            "words": [
                {"id": w["id"], "form": cloze.match_form(w.get("nl", ""))}
                for w in lesson.new_words + lesson.review_words
            ],
        },
    }