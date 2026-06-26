"""Convert one Delftse-methode chapter (.md) into the trainer's lesson JSON + MP3.

LearnX-Delftse is a fixed book (book1/*.md), not a daily-generated lesson. This script:

  1. Parse the chapter's `Dutch | English` table  -> the lesson text (dialogue),
     and the `---` Q&A section                     -> the VRAGEN tab data.
  2. One LLM call extracts ~8 key A2 words (id/nl/en/pos), picks an example row per
     word, and tags each table row with a speaker (A/B) so the audio alternates voices.
  3. Run the app's OWN vendored pipeline — delftse.audio (edge-tts MP3 + ms timings)
     and delftse.trainer.build_payload (segments + cloze + luistertoets). No dependency
     on the LearnX-Radar checkout; everything is self-contained here.
  4. Augment the payload with `chapter`, `title`, and `vragen` (and a denser
     content-word luistertoets), then write delftse-<n>.{json,mp3} into the trainer dir
     and refresh index.json.

Run from anywhere:  python scripts/convert.py <chapter.md>  [--out <dir>]
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DELFTSE = HERE.parent
OUT_DEFAULT = DELFTSE / "trainer"

# Make the app root importable (config.py + the delftse/ package live there).
sys.path.insert(0, str(DELFTSE))

import config  # noqa: E402
from delftse import audio as dutch_audio  # noqa: E402
from delftse import llm  # noqa: E402
from delftse import trainer as dutch_trainer  # noqa: E402
from delftse.lesson import DutchLesson  # noqa: E402


# --- parsing the chapter markdown ------------------------------------------------
def chapter_number(md_path: Path) -> int:
    m = re.match(r"(\d+)", md_path.stem)
    return int(m.group(1)) if m else 0


def _sections(text: str) -> list[str]:
    """Split a chapter on `---` rules. Richer chapters (e.g. 41) stack several
    sections: [0] title+main table, [1] Q&A, then optionally a grammar/comparison
    block and the 🔴 New Words / 🟡 New Phrases lists. Splitting lets each parser take
    only its own section instead of swallowing later tables/lists."""
    return re.split(r"(?m)^\s*-{3,}\s*$", text)


def parse_table(text: str) -> list[dict]:
    """Rows of the MAIN `Dutch | English` lesson table -> [{nl, en}]. Only the first
    section (before the first `---`), so a later comparison table can't leak in."""
    rows: list[dict] = []
    for line in _sections(text)[0].splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue
        nl, en = cells[0], cells[1]
        if not nl or nl.lower() == "dutch" or set(nl) <= {"-", ":"}:
            continue
        rows.append({"nl": nl, "en": en})
    return rows


def parse_vragen(text: str) -> list[dict]:
    """The Q&A "Les N" section: a numbered `**question**` then its answer line(s).
    Returns [{q, a}] for the VRAGEN tab. The Q&A isn't always the section right after
    the table (richer chapters slot a vocabulary doc in between), so pick whichever
    section holds the most numbered questions — bounded to it so trailing tables/lists
    don't pollute the last answer."""
    qre = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*\s*$", re.M)
    secs = _sections(text)
    body = max(secs, key=lambda s: len(qre.findall(s)), default="")
    if not qre.findall(body):
        return []
    items: list[dict] = []
    cur: dict | None = None
    ans: list[str] = []
    for line in body.splitlines():
        m = qre.match(line)
        if m:
            if cur:
                cur["a"] = " ".join(ans).strip()
                items.append(cur)
            cur = {"q": m.group(1).strip()}
            ans = []
        elif cur is not None:
            s = line.strip().lstrip("-").strip()
            if s and not s.startswith("#"):
                ans.append(s)
    if cur:
        cur["a"] = " ".join(ans).strip()
        items.append(cur)
    return items


# --- curated vocabulary (🔴 New Words / 🟡 New Phrases) ---------------------------
# Richer chapters (13–42) hand-pick the words/phrases worth learning, each with a gloss
# and a real example sentence from the text — authoritative vocabulary, used instead of
# LLM extraction when present. Two layouts occur in book1:
#   A: inline bullets  `- **Word** 🔴 *gloss; "dutch" (english)*`
#   B: a 4-column table `| Dutch Word | English Translation | Explanation | Example |`
_Q = "\"“”‘’'"                                   # the quote chars (straight + curly)
_RED = re.compile(r"^\s*-\s*\*\*(.+?)\*\*\s*\U0001F534\s*\*(.+?)\*\s*$", re.M)
_YEL = re.compile(r"^\s*-\s*\*\*(.+?)\*\*\s*\U0001F7E1\s*\*(.+?)\*\s*$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
CURATED_CAP = 16                                 # bound block-A audio + dictee length


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower().strip()).strip("-")


def _quotes(s: str) -> list[str]:
    return re.findall(rf"[{_Q}]([^{_Q}]+)[{_Q}]", s)


def _example_from(s: str) -> tuple[str, str]:
    """Pull (dutch, english) from a `"dutch" (english)` snippet."""
    qs = _quotes(s)
    nl_ex = qs[0] if qs else ""
    em = re.search(r"\(([^)]*)\)\s*$", s.strip())
    en_ex = em.group(1) if em else (qs[1] if len(qs) > 1 else "")
    return nl_ex, en_ex


def parse_curated(text: str) -> tuple[list[dict], list[dict]]:
    """Extract (new_words, sentences) from the 🔴/🟡 lists (either layout). Empty when a
    chapter has neither, so the caller falls back to LLM extraction."""
    new_words: list[dict] = []
    sentences: list[dict] = []
    seen: set[str] = set()

    def add(word: str, gloss: str, nl_ex: str, en_ex: str, pos: str) -> None:
        wid = _slug(word)
        if not wid or wid in seen:
            return
        seen.add(wid)
        new_words.append({"id": wid, "nl": word.strip(), "en": gloss.strip(),
                          "pos": pos, "theme": "delftse", "cefr": "A2"})
        if nl_ex:
            sentences.append({"id": wid, "nl": nl_ex.strip(), "en": en_ex.strip()})

    lines = text.splitlines()

    # Layout A — 🔴 inline bullets
    for m in _RED.finditer(text):
        body = m.group(2)
        gloss = re.split(rf"[;{_Q}]", body, maxsplit=1)[0].strip().rstrip(";")
        nl_ex, en_ex = _example_from(body)
        add(m.group(1), gloss, nl_ex, en_ex, "other")
    # Layout A — 🟡 phrases. The example is either inline (`*gloss; "nl" (en)*`, H16)
    # or gloss-only with the example on a following `Example:` line (H41).
    for i, line in enumerate(lines):
        m = _YEL.match(line)
        if not m:
            continue
        body = m.group(2)
        if _quotes(body):
            gloss = re.split(rf"[;{_Q}]", body, maxsplit=1)[0].strip().rstrip(";")
            nl_ex, en_ex = _example_from(body)
        else:
            gloss, nl_ex, en_ex = body.rstrip("."), "", ""
            for j in range(i + 1, min(i + 4, len(lines))):
                qs = _quotes(lines[j])
                if qs:
                    nl_ex, en_ex = qs[0], (qs[1] if len(qs) > 1 else "")
                    break
        add(m.group(1), gloss, nl_ex, en_ex, "phrase")

    # Layout B — a `Dutch Word | … | Example` table (word in col1, gloss col2, ex last)
    in_tbl = False
    for line in lines:
        s = line.strip()
        low = s.lower()
        if s.startswith("|") and "dutch word" in low and "example" in low:
            in_tbl = True
            continue
        if in_tbl:
            if not s.startswith("|"):
                in_tbl = False
                continue
            if set(s) <= set("|-: "):                 # separator row
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 2:
                continue
            bm = _BOLD.search(cells[0])
            word = bm.group(1) if bm else cells[0]
            nl_ex, en_ex = _example_from(cells[-1]) if len(cells) >= 3 else ("", "")
            add(word, cells[1], nl_ex, en_ex, "other")

    # Layout C — header `- **Word 🟥**` (marker INSIDE the bold) then `Definition:` /
    # `Example:` sub-bullets (H17). Tolerate circle/square colour variants.
    marks = "\U0001F534\U0001F7E5\U0001F7E1\U0001F7E8\U0001F7E0\U0001F7E7"
    head = re.compile(rf"^\s*-\s*\*\*(.+?)\*\*\s*$")
    for i, line in enumerate(lines):
        hm = head.match(line)
        if not hm or not re.search(rf"[{marks}]", hm.group(1)):
            continue
        word = re.sub(rf"[{marks}️]", "", hm.group(1)).strip()
        gloss = nl_ex = en_ex = ""
        for j in range(i + 1, min(i + 6, len(lines))):
            sub = lines[j]
            low = sub.lower()
            if head.match(sub) and re.search(rf"[{marks}]", sub):
                break                                   # next word header
            if ("definition" in low or "translation" in low) and not gloss:
                g = re.sub(r"\*\*", "", sub.split(":", 1)[-1])
                gloss = re.split(rf"[;{_Q}]", g, maxsplit=1)[0].strip()
            if "example" in low and not nl_ex:
                nl_ex, en_ex = _example_from(sub)
        add(word, gloss, nl_ex, en_ex, "other")

    if len(new_words) > CURATED_CAP:               # cap, keeping the matching sentences
        new_words = new_words[:CURATED_CAP]
        kept = {w["id"] for w in new_words}
        sentences = [s for s in sentences if s["id"] in kept]
    return new_words, sentences


def _strip_trailing_paren(s: str) -> str:
    """Remove one balanced parenthetical group at the very end (the English gloss),
    leaving any Dutch parentheses earlier in the line intact."""
    s = s.rstrip()
    if not s.endswith(")"):
        return s
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        if s[i] == ")":
            depth += 1
        elif s[i] == "(":
            depth -= 1
            if depth == 0:
                return s[:i].rstrip()
    return s


def title_of(text: str, md_path: Path) -> str:
    """Clean Dutch chapter title. The filename is the book's canonical Dutch heading
    (always Dutch, no gloss); the in-file `# ` heading adds nicer punctuation but
    sometimes a trailing English gloss in `(...)` or is itself English. So: strip the
    trailing gloss off the heading and use it only when it's still the same Dutch
    title (just better-punctuated) — otherwise fall back to the filename."""
    fname = re.sub(r"^\d+_?", "", md_path.stem).replace("_", " ").strip()
    heading = ""
    for line in text.splitlines():
        if line.startswith("# "):
            heading = _strip_trailing_paren(line[2:].strip())
            break
    if heading:
        norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
        if norm(heading).startswith(norm(fname)) or norm(fname).startswith(norm(heading)):
            return heading
    return fname


# --- LLM enrichment (the one generated piece; everything else is deterministic) --
EXTRACT_PROMPT = """You prepare a Dutch (Delftse methode) reading lesson for an A2 learner.

Chapter title: {title}

The lesson text, one numbered row per line (Dutch — English):
{rows}

Return STRICT JSON, no prose:
{{
  "new_words": [
    {{"id": "<lowercase bare noun/verb, no article>", "nl": "<word WITH article for nouns, e.g. 'de melk'>", "en": "<short English gloss>", "pos": "noun|verb|adj|other"}}
  ],
  "example_row": {{"<id>": <row number whose Dutch sentence best uses that word>}},
  "speakers": ["<A or B per row, in order; alternate on turn changes, keep one speaker for narration>"]
}}

Rules:
- Pick the 8 most useful NEW vocabulary words that actually appear in the text.
- Each word must be ONE dictionary headword: a single noun, verb, adjective, or
  adverb. NOT a phrase, NOT a proper noun (no city/street/person names), and NOT a
  pronoun-bound form ("mijn telefoonnummer" -> use "het telefoonnummer").
- "nl" is the citation form: nouns WITH their article (de/het); verbs as the
  infinitive (e.g. "wonen", "komen"); others as the base word.
- "id" must be the matchable bare form (strip de/het/een), lowercase.
- "speakers" must have exactly {n} entries, one per row above.
- Output only the JSON object."""


# --- luistertoets: a dense listening dictation (not just the 8 vocab words) ------
# Function words that stay VISIBLE — articles, pronouns, prepositions, conjunctions,
# copula/auxiliaries, and very common adverbs. Content words (nouns, verbs, adjectives,
# numbers) are the blanks. Words under 4 letters are skipped automatically, so this
# only needs to list the >=4-letter function words.
_STOP = set("""
de het een den der des 't
ik jij gij hij zij wij jullie haar hem hen hun mij jou zich mijn jouw zijn uw onze ons
deze dit dat die wie wat welk welke daar hier waar none
in op aan met voor van naar uit bij tot over onder tussen door tegen zonder binnen buiten
sinds tijdens rond langs vanaf omtrent
en of maar want dus omdat toen terwijl hoewel zodat doordat totdat nadat zoals indien
ben bent is was waren word wordt worden werd heb hebt heeft hebben had hadden
kan kun kunt kunnen kon konden zal zult zullen zou zouden moet moeten moest mag mogen
wil wilt willen wilde laat laten
niet wel ook nog maar even heel zeer erg zo nu toch weer eens soms vaak altijd nooit
meestal graag echt alleen weer dan toen meer veel heel hele bijna pas zelfs juist
hoe wat waar wie welke waarom wanneer hoeveel
ja nee oke hoor dag hallo
""".split())

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+(?:['’][A-Za-zÀ-ÿ]+)?|\d+")


def _is_blank_candidate(w: str) -> bool:
    if w.isdigit():
        return True
    lw = w.lower().strip("'’")
    if lw in _STOP:
        return False
    return len(lw) >= 4


def dense_blanks(sentence: str, cap: int = 3) -> tuple[str, list[str]]:
    """Blank up to `cap` non-adjacent content words in `sentence`, returning the text
    with `___ (n)` markers and the answers in order. Blanks are kept >=2 words apart so
    no two are adjacent — a readable dictation, not a wall of gaps."""
    matches = list(_WORD_RE.finditer(sentence))
    chosen, last = set(), -2
    for i, m in enumerate(matches):
        if len(chosen) >= cap:
            break
        if i - last >= 2 and _is_blank_candidate(m.group()):
            chosen.add(i)
            last = i
    out, answers, pos = [], [], 0
    for i, m in enumerate(matches):
        out.append(sentence[pos:m.start()])
        if i in chosen:
            answers.append(m.group())
            out.append(f"___ ({len(answers)})")
        else:
            out.append(m.group())
        pos = m.end()
    out.append(sentence[pos:])
    return "".join(out), answers


def dense_luistertoets(dialogue: list[dict]) -> list[dict]:
    """Per dialogue line: a content-word dictation. Replaces the vocab-only blanks so
    a long chapter still gives a substantial listening test."""
    out = []
    for d in dialogue:
        text, answers = dense_blanks(d.get("nl", ""))
        out.append({"speaker": d.get("speaker", ""), "nl": text,
                    "answers": answers, "en": d.get("en", "")})
    return out


def enrich(title: str, rows: list[dict]) -> dict:
    numbered = "\n".join(f"{i+1}. {r['nl']} — {r['en']}" for i, r in enumerate(rows))
    prompt = EXTRACT_PROMPT.format(title=title, rows=numbered, n=len(rows))
    raw = llm.chat([{"role": "user", "content": prompt}], max_tokens=1200, temperature=0.3)
    data = llm.parse_json_response(raw)
    if not isinstance(data, dict):
        raise ValueError("LLM did not return a JSON object")
    return data


def build_lesson(title: str, rows: list[dict], enriched: dict,
                 curated: tuple[list[dict], list[dict]] | None = None) -> DutchLesson:
    """Assemble a DutchLesson from the table + vocabulary. When the chapter ships a
    curated 🔴/🟡 list (curated[0] non-empty), that is the authoritative vocabulary;
    otherwise fall back to the LLM's extraction. Speakers always come from the LLM."""
    if curated and curated[0]:
        new_words, sentences = curated
    else:
        new_words = []
        for w in enriched.get("new_words", []):
            if isinstance(w, dict) and w.get("id") and w.get("nl"):
                new_words.append({"id": str(w["id"]).strip(), "nl": w["nl"].strip(),
                                  "en": (w.get("en") or "").strip(),
                                  "pos": w.get("pos", "other"), "theme": "delftse", "cefr": "A2"})
        # Example sentence per word = the table row the LLM chose (1-based), else the
        # first row that contains the word's bare form. Falls back to no sentence.
        ex = enriched.get("example_row", {}) or {}
        sentences = []
        for w in new_words:
            row = None
            idx = ex.get(w["id"])
            if isinstance(idx, int) and 1 <= idx <= len(rows):
                row = rows[idx - 1]
            if row is None:
                pat = re.compile(rf"\b{re.escape(w['id'])}\w*", re.IGNORECASE)
                row = next((r for r in rows if pat.search(r["nl"])), None)
            if row:
                sentences.append({"id": w["id"], "nl": row["nl"], "en": row["en"]})

    speakers = enriched.get("speakers", [])
    dialogue = []
    for i, r in enumerate(rows):
        sp = speakers[i] if i < len(speakers) and speakers[i] in ("A", "B") else ("A" if i % 2 == 0 else "B")
        dialogue.append({"speaker": sp, "nl": r["nl"], "en": r["en"]})

    return DutchLesson(theme="delftse", cefr="A2", new_words=new_words,
                       review_words=[], sentences=sentences, dialogue=dialogue)


# --- index.json (the chapter picker manifest) ------------------------------------
def update_index(out_dir: Path, chapter: int, title: str) -> None:
    idx_path = out_dir / "index.json"
    chapters = []
    if idx_path.exists():
        try:
            chapters = json.loads(idx_path.read_text(encoding="utf-8")).get("chapters", [])
        except Exception:
            chapters = []
    chapters = [c for c in chapters if c.get("chapter") != chapter]
    chapters.append({"chapter": chapter, "title": title,
                     "json": f"delftse-{chapter}.json", "audio": f"delftse-{chapter}.mp3"})
    chapters.sort(key=lambda c: c["chapter"])
    idx_path.write_text(json.dumps({"chapters": chapters}, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def convert_one(md_path: Path, out_dir: Path) -> dict:
    """Convert one chapter .md -> delftse-<n>.{json,mp3} + refresh index.json.
    Returns a small summary dict; raises on hard failure (caller decides recovery)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    text = md_path.read_text(encoding="utf-8")

    n = chapter_number(md_path)
    title = title_of(text, md_path)
    rows = parse_table(text)
    vragen = parse_vragen(text)
    curated = parse_curated(text)
    if not rows:
        raise ValueError(f"No table rows found in {md_path.name}")
    src = f"{len(curated[0])} curated (🔴/🟡)" if curated[0] else "LLM-extracted"
    print(f"[delftse] chapter {n}: '{title}' — {len(rows)} rows, {len(vragen)} vragen, "
          f"vocab: {src}")

    print("[delftse] assigning speakers via LLM…")
    enriched = enrich(title, rows)
    lesson = build_lesson(title, rows, enriched, curated)
    print(f"[delftse] {len(lesson.new_words)} new words, {len(lesson.sentences)} example sentences")

    mp3_path = out_dir / f"delftse-{n}.mp3"
    print(f"[delftse] rendering audio -> {mp3_path.name} (edge-tts)…")
    timings = asyncio.run(dutch_audio.build(lesson, str(mp3_path)))

    payload = dutch_trainer.build_payload(lesson, timings, mp3_path.name)
    # Denser luistertoets than the vocab-only default: a long reading text needs a real
    # content-word dictation, not just blanks on the 8 new words (which are sparse and
    # miss inflected verb forms). Block-C audio is unchanged — only the blanks change.
    payload["luistertoets"] = dense_luistertoets(lesson.dialogue)
    payload["chapter"] = n
    payload["title"] = title
    payload["theme"] = title           # the trainer shows `theme` in its header
    payload["vragen"] = vragen
    payload["date"] = f"book1-{n:02d}"  # a stable id; the page keys results off this

    json_path = out_dir / f"delftse-{n}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    update_index(out_dir, n, title)
    print(f"[delftse] wrote {json_path.name} + updated index.json")
    return {"chapter": n, "title": title, "words": len(lesson.new_words),
            "rows": len(rows), "vragen": len(vragen),
            "cloze": len(payload["cloze"]["answers"]),
            "mp3_kb": round(mp3_path.stat().st_size / 1024, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter", help="path to a book1/*.md chapter")
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="output trainer dir")
    args = ap.parse_args()

    convert_one(Path(args.chapter).resolve(), Path(args.out).resolve())


if __name__ == "__main__":
    main()