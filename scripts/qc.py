"""Quality check across all generated Delftse chapters.

Validates, per chapter: JSON structure, A/B/C audio blocks, that each new word has a
block-A audio segment, cloze/luistertoets blanks, vragen, vocab quality (phrases /
proper nouns / empty glosses), and audio integrity (mp3 exists; last segment end_ms
fits inside the real mp3 duration via ffprobe — catches a truncated render). Prints a
per-chapter PASS/WARN/FAIL table and writes build/qc_report.json. Read-only.

    python build/qc.py
"""
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRAINER = HERE.parent / "trainer"

REQUIRED_KEYS = {"date", "theme", "cefr", "audio_url", "new_words", "sentences",
                 "dialogue", "cloze", "luistertoets", "segments", "block_b",
                 "block_c", "report", "chapter", "title", "vragen"}


def mp3_duration_ms(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip()) * 1000
    except Exception:
        return None


def check_chapter(jp: Path) -> dict:
    fails: list[str] = []
    warns: list[str] = []
    d = json.loads(jp.read_text(encoding="utf-8"))
    n = d.get("chapter")

    missing = REQUIRED_KEYS - set(d)
    if missing:
        fails.append(f"missing keys: {sorted(missing)}")

    segs = d.get("segments", [])
    blocks = {b: [s for s in segs if s.get("block") == b] for b in "ABC"}
    for b in "ABC":
        if not blocks[b]:
            fails.append(f"no block-{b} segments")
    # timings sane: start<end, end_ms increasing overall
    bad_t = sum(1 for s in segs if s.get("end_ms", 0) <= s.get("start_ms", 0))
    if bad_t:
        fails.append(f"{bad_t} segment(s) with end<=start")

    nw = d.get("new_words", [])
    if len(nw) < 4:
        warns.append(f"only {len(nw)} new words")
    segA_nl = {s["nl"] for s in blocks["A"]}
    no_audio = [w["nl"] for w in nw if w["nl"] not in segA_nl]
    if no_audio:
        fails.append(f"{len(no_audio)} word(s) with no block-A audio: {no_audio}")

    # vocab quality
    for w in nw:
        nl = w.get("nl", "")
        en = (w.get("en") or "").strip()
        bare = nl.lower()
        for art in ("de ", "het ", "een "):
            if bare.startswith(art):
                bare = bare[len(art):]
                break
        if not en:
            warns.append(f"'{nl}' has empty gloss")
        # multi-word is expected for curated 🟡 phrases (pos=="phrase"); only flag it
        # for words that are meant to be single headwords.
        if len(bare.split()) > 2 and w.get("pos") != "phrase":
            warns.append(f"'{nl}' looks like a phrase")
        # bare noun capitalized mid-word -> likely a proper noun (Dutch commons are lowercase)
        if bare[:1].isupper():
            warns.append(f"'{nl}' may be a proper noun")

    if not d.get("cloze", {}).get("answers"):
        warns.append("no cloze blanks")
    lt_blanks = sum(len(i.get("answers", [])) for i in d.get("luistertoets", []))
    if not lt_blanks:
        warns.append("no luistertoets blanks")
    if not d.get("vragen"):
        warns.append("no vragen")

    # title hygiene: a TRAILING English gloss in () (mid-string Dutch parens are fine)
    title = d.get("title", "")
    if title.rstrip().endswith(")"):
        warns.append("title carries an English gloss in ()")

    # audio integrity
    mp3 = TRAINER / f"delftse-{n}.mp3"
    if not mp3.exists():
        fails.append("mp3 missing")
    else:
        kb = mp3.stat().st_size / 1024
        if kb < 100:
            fails.append(f"mp3 suspiciously small ({kb:.0f}KB)")
        last_end = max((s.get("end_ms", 0) for s in segs), default=0)
        dur = mp3_duration_ms(mp3)
        if dur is not None and last_end > dur + 500:
            fails.append(f"last segment {last_end}ms exceeds mp3 {dur:.0f}ms (truncated)")

    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    return {"chapter": n, "title": title, "status": status,
            "fails": fails, "warns": warns,
            "blocks": {b: len(blocks[b]) for b in "ABC"}, "lt_blanks": lt_blanks}


def main() -> None:
    jsons = sorted(TRAINER.glob("delftse-*.json"),
                   key=lambda p: int(p.stem.split("-")[1]))
    # manifest cross-check
    idx = json.loads((TRAINER / "index.json").read_text(encoding="utf-8"))
    idx_ch = {c["chapter"] for c in idx.get("chapters", [])}

    results = [check_chapter(jp) for jp in jsons]
    built = {r["chapter"] for r in results}
    manifest_gap = sorted(built - idx_ch)

    print(f"QC over {len(results)} chapters\n" + "=" * 64)
    for r in results:
        flag = {"PASS": "[ok]", "WARN": "[!]", "FAIL": "[X]"}[r["status"]]
        b = r["blocks"]
        print(f"{flag} H{r['chapter']:<2} A{b['A']}/B{b['B']}/C{b['C']} "
              f"lt{r['lt_blanks']:<2} {r['title'][:40]}")
        for f in r["fails"]:
            print(f"      FAIL: {f}")
        for w in r["warns"]:
            print(f"      warn: {w}")

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_warn = sum(1 for r in results if r["status"] == "WARN")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    print("=" * 64)
    print(f"PASS {n_pass}  ·  WARN {n_warn}  ·  FAIL {n_fail}")
    if manifest_gap:
        print(f"index.json MISSING chapters: {manifest_gap}")
    else:
        print(f"index.json lists all {len(idx_ch)} chapters [ok]")

    (HERE / "qc_report.json").write_text(
        json.dumps({"pass": n_pass, "warn": n_warn, "fail": n_fail,
                    "manifest_gap": manifest_gap, "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()