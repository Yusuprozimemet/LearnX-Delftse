"""The lesson dataclass the audio + trainer modules consume (vendored, trimmed —
LearnX-Delftse builds this from a book chapter, not from an LLM curriculum)."""
from dataclasses import dataclass, field


@dataclass
class DutchLesson:
    theme: str
    cefr: str
    new_words: list[dict]                                  # {"id","nl","en",...}
    review_words: list[dict] = field(default_factory=list)
    sentences: list[dict] = field(default_factory=list)    # {"id","nl","en"}
    dialogue: list[dict] = field(default_factory=list)     # {"speaker","nl","en"}
    markdown: str = ""
    summary: str = ""