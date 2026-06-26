"""Dataclasses for the audio pipeline (vendored from LearnX-Radar learnx/models.py)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DialogueLine:
    speaker: str       # "ALEX" | "MAYA"
    text: str
    unit_number: int   # 0 = vocab (A), 1 = dialogue-by-line (B), 2 = whole dialogue (C)
    # Silence AFTER this line = factor x the line's rendered duration (the Delft repeat
    # pause). Duration is only known after TTS, so the factor rides on the line and is
    # resolved at assembly. 0.0 -> no pause.
    pause_after_factor: float = 0.0


@dataclass
class RenderedSegment:
    line: DialogueLine
    audio_path: str
    duration_ms: int