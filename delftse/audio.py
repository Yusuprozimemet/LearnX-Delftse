"""Render a Dutch lesson to one MP3 with edge-tts, returning per-line ms timings.

Vendored + merged from LearnX-Radar's learnx/audio_builder.py and dutch/audio.py,
trimmed to the Delft layout only (the legacy no-pause layout is gone). Block A reads
each new word + its example sentence with a repeat pause; block B reads the dialogue
sentence-by-sentence with repeat pauses; block C reads the dialogue straight through.
Only the Dutch is voiced — it is a listening exercise. `build()` returns the seek map
({speaker, text, unit, start_ms, end_ms} per line) the trainer page uses.
"""
import asyncio
import logging
import os
import shutil
from pathlib import Path

import edge_tts
from pydub import AudioSegment

import config
from delftse.models import DialogueLine, RenderedSegment

log = logging.getLogger(__name__)

# Speaker A -> ALEX voice, B -> MAYA voice (the two Dutch co-host voices).
_SPEAKER = {"A": "ALEX", "B": "MAYA"}
_VOICES = {"ALEX": config.DUTCH_VOICE_ALEX, "MAYA": config.DUTCH_VOICE_MAYA}
_RATES = {"ALEX": config.DUTCH_TTS_RATE, "MAYA": config.DUTCH_TTS_RATE}


def to_lines(lesson) -> list[DialogueLine]:
    """Ordered TTS lines for the Delft A/B/C layout."""
    factor = config.DUTCH_DELFT_PAUSE_FACTOR
    by_id = {s["id"]: s for s in lesson.sentences}
    lines: list[DialogueLine] = []
    # Block A (unit 0) — vocabulary: word -> sentence -> pause -> sentence again.
    for w in lesson.new_words:
        s = by_id.get(w["id"])
        if s and s.get("nl"):
            lines.append(DialogueLine("ALEX", w["nl"], 0))
            lines.append(DialogueLine("MAYA", s["nl"], 0, pause_after_factor=factor))
            lines.append(DialogueLine("MAYA", s["nl"], 0, pause_after_factor=0.5))
        else:
            lines.append(DialogueLine("ALEX", w["nl"], 0, pause_after_factor=factor))
    # Block B (unit 1) — dialogue, sentence by sentence: line -> pause -> line again.
    for d in lesson.dialogue:
        sp = _SPEAKER.get(d["speaker"], "ALEX")
        lines.append(DialogueLine(sp, d["nl"], 1, pause_after_factor=factor))
        lines.append(DialogueLine(sp, d["nl"], 1, pause_after_factor=0.5))
    # Block C (unit 2) — the dialogue straight through.
    for d in lesson.dialogue:
        lines.append(DialogueLine(_SPEAKER.get(d["speaker"], "ALEX"), d["nl"], 2))
    return [ln for ln in lines if ln.text.strip()]


async def _render_segment(line: DialogueLine, out_dir: str, idx: int) -> RenderedSegment:
    voice = _VOICES.get(line.speaker, config.DUTCH_VOICE_ALEX)
    rate = _RATES.get(line.speaker, config.DUTCH_TTS_RATE)
    out_path = str(Path(out_dir) / f"seg_{idx:04d}.mp3")
    await edge_tts.Communicate(line.text, voice, rate=rate).save(out_path)
    if os.path.getsize(out_path) == 0:
        raise RuntimeError(f"TTS produced empty audio for line {idx}: {line.text[:60]}")
    return RenderedSegment(line=line, audio_path=out_path,
                           duration_ms=len(AudioSegment.from_mp3(out_path)))


async def _render_all(lines: list[DialogueLine], tmp_dir: str) -> list[RenderedSegment]:
    sem = asyncio.Semaphore(config.TTS_SEMAPHORE_LIMIT)
    results: list[RenderedSegment | None] = [None] * len(lines)

    async def one(i: int, ln: DialogueLine) -> None:
        async with sem:
            results[i] = await _render_segment(ln, tmp_dir, i)

    await asyncio.gather(*[one(i, ln) for i, ln in enumerate(lines)])
    return [r for r in results if r is not None]


def _pause_after_ms(seg: RenderedSegment) -> int:
    factor = getattr(seg.line, "pause_after_factor", 0.0)
    if factor <= 0:
        return 0
    return max(int(seg.duration_ms * factor), config.DUTCH_DELFT_MIN_PAUSE_MS)


def _gap_ms(prev_speaker, prev_unit, line: DialogueLine) -> int:
    if prev_speaker is None:
        return 0
    if prev_unit != line.unit_number:
        return config.SILENCE_UNIT_MS
    if prev_speaker != line.speaker:
        return config.SILENCE_TURN_MS
    return config.SILENCE_BREATH_MS


def _assemble(segments: list[RenderedSegment], out_path: str, timings_out: list[dict]) -> None:
    full = AudioSegment.empty()
    prev_speaker = prev_unit = None
    after_pause = False
    for seg in segments:
        gap = 0 if after_pause else _gap_ms(prev_speaker, prev_unit, seg.line)
        if gap:
            full += AudioSegment.silent(duration=gap)
        start_ms = len(full)
        full += AudioSegment.from_mp3(seg.audio_path)
        timings_out.append({"speaker": seg.line.speaker, "text": seg.line.text,
                            "unit": seg.line.unit_number,
                            "start_ms": start_ms, "end_ms": len(full)})
        pause = _pause_after_ms(seg)
        if pause:
            full += AudioSegment.silent(duration=pause)
        after_pause = pause > 0
        prev_speaker, prev_unit = seg.line.speaker, seg.line.unit_number
    full.export(out_path, format="mp3")
    log.info("Assembled %d segments -> %s (%.1fs)", len(segments), out_path, len(full) / 1000)


async def build(lesson, out_path: str) -> list[dict]:
    """Render the lesson MP3 to `out_path` (async; call via asyncio.run). Returns the
    per-line timings — the trainer page's seek map."""
    lines = to_lines(lesson)
    if not lines:
        raise ValueError("No Dutch lines to render")
    tmp_dir = Path(out_path).parent / ".tts_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    timings: list[dict] = []
    try:
        segments = await _render_all(lines, str(tmp_dir))
        _assemble(segments, out_path, timings)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return timings