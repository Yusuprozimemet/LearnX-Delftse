"""Fill-in-the-blanks (cloze) — vendored from LearnX-Radar dutch/cloze.py.

Deterministic, no LLM: the NEW words are blanked out of the example sentences and
dialogue. Matching is exact-form, word-boundary, case-insensitive; an inflected form
the bank doesn't carry is simply not blanked. Trimmed: the markdown render() helper
(PDF/Telegram) isn't needed here — the trainer JSON only uses extract + sentence_blanks.
"""
import re


def match_form(nl: str) -> str:
    """The form searched for in the text: the bank stores nouns with their article
    ("de afspraak"), but in a sentence the noun appears with any (or no) article."""
    return re.sub(r"^(de|het)\s+", "", nl.strip(), flags=re.IGNORECASE)


def extract(new_words: list[dict], sentences: list[dict], dialogue: list[dict]) -> dict:
    """Structured cloze: {"lines": [str, ...], "answers": [str, ...]}.

    Each new word is blanked (as `___ (n)`) at its FIRST occurrence across the example
    sentences then the dialogue. Example sentences are included only when they got a
    blank; the dialogue is all-or-nothing. `answers[n-1]` is blank n."""
    remaining: dict[str, re.Pattern] = {}
    for w in new_words:
        form = match_form(w.get("nl", ""))
        if form:
            remaining[form] = re.compile(rf"\b{re.escape(form)}\b", re.IGNORECASE)

    answers: list[str] = []
    lines: list[str] = []

    def blank(text: str) -> tuple[str, bool]:
        hit = False
        for form, pattern in list(remaining.items()):
            new_text, n = pattern.subn(f"___ ({len(answers) + 1})", text, count=1)
            if n:
                answers.append(form)
                del remaining[form]
                text, hit = new_text, True
        return text, hit

    for s in sentences:
        text, hit = blank(s.get("nl", ""))
        if hit:
            lines.append(text)
    dialogue_lines: list[str] = []
    dialogue_hit = False
    for d in dialogue:
        text, hit = blank(d.get("nl", ""))
        dialogue_hit = dialogue_hit or hit
        dialogue_lines.append(f"{d.get('speaker', '')}: {text}")
    if dialogue_hit:
        lines.extend(dialogue_lines)

    return {"lines": lines, "answers": answers}


def sentence_blanks(words: list[dict], dialogue: list[dict]) -> list[dict]:
    """Per-dialogue-line cloze for the luistertoets: every occurrence of any target
    word is blanked per line, numbering restarting per line. (LearnX-Delftse replaces
    this vocab-only version with a denser content-word dictation in scripts/convert.py,
    but it's kept for parity with the trainer payload contract.)"""
    patterns = []
    for w in words:
        form = match_form(w.get("nl", ""))
        if form:
            patterns.append((form, re.compile(rf"\b{re.escape(form)}\b", re.IGNORECASE)))

    out: list[dict] = []
    for d in dialogue:
        text = d.get("nl", "")
        answers: list[str] = []
        for form, pattern in patterns:
            while True:
                new_text, n = pattern.subn(f"___ ({len(answers) + 1})", text, count=1)
                if not n:
                    break
                answers.append(form)
                text = new_text
        out.append({"speaker": d.get("speaker", ""), "nl": text, "answers": answers,
                    "en": d.get("en", "")})
    return out