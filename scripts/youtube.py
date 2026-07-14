"""Turn a Dutch YouTube video into a Delftse-methode lesson (immersion pipeline).

The video is the source of TEXT, not sound: the lesson audio is regenerated with
edge-tts (two voices, Delft repeat-pauses) like every book chapter. Steps:

  1. yt-dlp fetches the video's Dutch subtitles — manual ("echte") subs when the
     uploader provided them, else YouTube's auto-generated ASR track.
  2. Clean the VTT: strip tags/timestamps and dedupe the rolling repeats of auto subs.
  3. One LLM call shapes the transcript into a book1-style chapter: ~20 rows of the
     speaker's OWN words (punctuation restored, not simplified — immersion), an
     English gloss per row, and a Dutch Q&A section.
  4. Write youtube/<n>_<Title>.md and hand it to scripts/convert.py's convert_one() —
     vocab extraction, audio, cloze, luistertoets and index.json are all reused.

YouTube chapters are numbered 101+ so they never collide with the book (1..43);
delftse.html/telegram.py accept 3-digit chapters since the same change.

Run:  python scripts/youtube.py <youtube-url> [--chapter N] [--out <dir>]
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

import yt_dlp

HERE = Path(__file__).resolve().parent
DELFTSE = HERE.parent
MD_DIR = DELFTSE / "youtube"
YT_CHAPTER_BASE = 101

sys.path.insert(0, str(DELFTSE))
sys.path.insert(0, str(HERE))

from delftse import llm  # noqa: E402
import convert  # noqa: E402  (scripts/convert.py — the shared md -> lesson pipeline)


# --- 1. subtitles via yt-dlp ------------------------------------------------------
def fetch_subtitles(url: str, workdir: Path) -> tuple[dict, Path, bool]:
    """Download the video's Dutch subtitle track as VTT.
    Returns (video info, vtt path, is_auto). Exits with a clear message when the
    video has no Dutch track at all — this pipeline is Dutch-immersion only."""
    probe_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    def dutch_lang(tracks: dict) -> str | None:
        for lang in tracks or {}:
            if lang == "nl" or lang.startswith("nl-"):
                return lang
        return None

    manual = dutch_lang(info.get("subtitles"))
    auto = dutch_lang(info.get("automatic_captions"))
    lang = manual or auto
    if not lang:
        raise SystemExit(
            "This video has no Dutch subtitles (manual or auto). The immersion "
            "pipeline needs Dutch speech — pick a Dutch-language video.")

    dl_opts = {
        "skip_download": True,
        "writesubtitles": bool(manual),
        "writeautomaticsub": not manual,
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt/best",
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        ydl.download([url])
    vtts = sorted(workdir.glob(f"{info['id']}*.vtt"))
    if not vtts:
        raise SystemExit(f"yt-dlp reported a '{lang}' track but wrote no .vtt file.")
    return info, vtts[0], not manual


# --- 2. VTT -> plain transcript ---------------------------------------------------
_TAG = re.compile(r"<[^>]+>")                      # word timings <00:00:01.319><c>..</c>
_CUE_TIME = re.compile(r"-->")

def vtt_to_text(vtt: str) -> str:
    """Cue text only, tags stripped, consecutive duplicates dropped (auto-sub cues
    repeat the previous line as a rolling two-line window)."""
    lines: list[str] = []
    for raw in vtt.splitlines():
        s = raw.strip()
        if (not s or s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE"))
                or _CUE_TIME.search(s) or s.isdigit()):
            continue
        s = _TAG.sub("", s).replace("&nbsp;", " ").replace("&amp;", "&").strip()
        if s and (not lines or s != lines[-1]):
            lines.append(s)
    return " ".join(lines)


# --- 3. transcript -> book1-shaped chapter (one LLM call) --------------------------
MAX_TRANSCRIPT_CHARS = 12000   # keep the prompt well inside the model's context

LESSON_PROMPT = """You prepare a Dutch (Delftse methode) IMMERSION lesson for an A2/B1 \
learner from a YouTube transcript. The transcript may be auto-generated speech \
recognition: casing and punctuation are missing and small recognition errors occur.

Video title: {title}

Transcript:
{transcript}

Return STRICT JSON, no prose:
{{
  "title_nl": "<short Dutch lesson title, max 6 words, no quotes/colons>",
  "rows": [{{"nl": "<Dutch>", "en": "<English translation>"}}],
  "vragen": [{{"q": "<Dutch comprehension question>", "a": "<short Dutch answer>"}}]
}}

Rules:
- 16 to 24 rows. Each row is 1-3 SHORT sentences of the speaker's OWN words, kept in
  the original order. Restore capitalization and punctuation and fix obvious speech
  recognition slips, but DO NOT simplify, paraphrase or "improve" the Dutch — the
  learner must read what the speaker really said (immersion).
- Together the rows form one coherent self-contained passage; skip channel intros,
  outros, sponsor reads and pure filler.
- "en": a natural English translation of that row.
- "vragen": 6 to 8 Dutch comprehension questions about the passage, each with a short
  Dutch answer.
- Output only the JSON object."""


def shape_lesson(video_title: str, transcript: str) -> dict:
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        print(f"[youtube] transcript is long ({len(transcript)} chars) — "
              f"using the first {MAX_TRANSCRIPT_CHARS}")
        cut = transcript[:MAX_TRANSCRIPT_CHARS]
        transcript = cut[:cut.rfind(" ")]
    prompt = LESSON_PROMPT.format(title=video_title, transcript=transcript)
    # The model occasionally emits JSON with unquoted string values; feed the broken
    # output back with a correction instead of failing the whole run.
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(3):
        raw = llm.chat(messages, max_tokens=4000, temperature=0.3)
        try:
            data = llm.parse_json_response(raw)
            if isinstance(data, dict) and data.get("rows"):
                return data
            err = "the JSON object was missing a non-empty 'rows' array"
        except ValueError:
            err = ("it was not valid JSON — every key AND every string value must be "
                   "enclosed in double quotes")
        print(f"[youtube] LLM output invalid ({err}) — asking it to fix (attempt {attempt + 1}/3)")
        messages = [{"role": "user", "content": prompt},
                    {"role": "assistant", "content": raw[:3000]},
                    {"role": "user", "content":
                     f"Your previous output was rejected: {err}. Return the SAME lesson "
                     "again as one STRICT valid JSON object only — no prose, no fences."}]
    raise ValueError("LLM failed to produce valid lesson JSON after 3 attempts")


# --- 4. write the book1-style .md and run the shared converter ---------------------
def _cell(s: str) -> str:
    """A markdown table cell: no pipes (they'd split the row), collapsed whitespace."""
    return re.sub(r"\s+", " ", (s or "").replace("|", "/")).strip()


def write_chapter_md(n: int, shaped: dict, video_url: str, video_title: str) -> Path:
    title = _cell(shaped.get("title_nl") or video_title)
    lines = [f"# {title}", "",
             f"<!-- source: {video_url} -->", "",
             "| Dutch | English |", "|-------|---------|"]
    for r in shaped["rows"]:
        nl, en = _cell(r.get("nl", "")), _cell(r.get("en", ""))
        if nl:
            lines.append(f"| {nl} | {en} |")
    vragen = [v for v in shaped.get("vragen", []) if v.get("q")]
    if vragen:
        lines += ["", "---", f"## Les {n}: {title}", ""]
        for i, v in enumerate(vragen, 1):
            lines += [f"{i}. **{_cell(v['q'])}**", f"   - {_cell(v.get('a', ''))}", ""]
    MD_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w ]", "", title).strip().replace(" ", "_") or "video"
    md_path = MD_DIR / f"{n}_{slug}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


def next_chapter(out_dir: Path) -> int:
    """First free number in the YouTube range (101+), from trainer/index.json."""
    try:
        chapters = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))["chapters"]
        taken = [c["chapter"] for c in chapters if c.get("chapter", 0) >= YT_CHAPTER_BASE]
    except Exception:
        taken = []
    return max(taken, default=YT_CHAPTER_BASE - 1) + 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="YouTube video URL (Dutch-language video)")
    ap.add_argument("--chapter", type=int, default=None,
                    help=f"chapter number (default: next free >= {YT_CHAPTER_BASE})")
    ap.add_argument("--out", default=str(DELFTSE / "trainer"), help="output trainer dir")
    args = ap.parse_args()
    out_dir = Path(args.out).resolve()

    with tempfile.TemporaryDirectory(prefix="delftse-yt-") as tmp:
        print(f"[youtube] fetching Dutch subtitles for {args.url} …")
        info, vtt_path, is_auto = fetch_subtitles(args.url, Path(tmp))
        transcript = vtt_to_text(vtt_path.read_text(encoding="utf-8"))
    kind = "auto-generated (ASR)" if is_auto else "uploader-provided"
    print(f"[youtube] '{info.get('title')}' — {kind} subs, "
          f"{len(transcript.split())} words of transcript")
    if len(transcript.split()) < 40:
        raise SystemExit("Transcript is too short to make a lesson from.")

    n = args.chapter if args.chapter is not None else next_chapter(out_dir)
    print(f"[youtube] shaping into chapter {n} via LLM…")
    shaped = shape_lesson(info.get("title") or "", transcript)
    md_path = write_chapter_md(n, shaped, args.url, info.get("title") or "")
    print(f"[youtube] wrote {md_path.relative_to(DELFTSE)} "
          f"({len(shaped['rows'])} rows, {len(shaped.get('vragen', []))} vragen)")

    summary = convert.convert_one(md_path, out_dir)

    # Provenance on the lesson JSON: which video, and whether the text came from ASR.
    json_path = out_dir / f"delftse-{n}.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["source"] = {"kind": "youtube", "url": args.url,
                         "video_title": info.get("title"), "auto_subs": is_auto}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"[youtube] done: chapter {summary['chapter']} '{summary['title']}' — "
          f"{summary['rows']} rows, {summary['words']} words, {summary['mp3_kb']} KB mp3")


if __name__ == "__main__":
    main()
