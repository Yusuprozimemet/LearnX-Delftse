"""Batch-convert every book1 chapter into the trainer.

Runs in ONE process so config/edge-tts imports and the LLM clients are reused, and
the per-chapter loop is fault-tolerant: a failure (LLM JSON parse, NIM timeout, empty
audio) is retried once, then logged and skipped so the rest still build. Prints a
summary table and writes scripts/batch_report.json.

    python scripts/batch.py            # all chapters
    python scripts/batch.py 6 7 8      # only these chapter numbers
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import convert  # noqa: E402  (adds the app root to sys.path; vendored pipeline only)

BOOK1 = convert.DELFTSE / "book1"
OUT = convert.OUT_DEFAULT


def chapters_to_build(only: set[int]) -> list[Path]:
    paths = [p for p in BOOK1.glob("*.md")]
    paths = [p for p in paths if convert.chapter_number(p) > 0]
    if only:
        paths = [p for p in paths if convert.chapter_number(p) in only]
    return sorted(paths, key=convert.chapter_number)


def main() -> None:
    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    paths = chapters_to_build(only)
    print(f"[batch] {len(paths)} chapter(s) to build -> {OUT}\n")

    ok: list[dict] = []
    failed: list[dict] = []
    for i, md in enumerate(paths, 1):
        n = convert.chapter_number(md)
        print(f"--- [{i}/{len(paths)}] chapter {n}: {md.name} ---")
        last_exc = None
        for attempt in (1, 2):
            try:
                summary = convert.convert_one(md, OUT)
                ok.append(summary)
                last_exc = None
                break
            except Exception as exc:  # keep going — one bad chapter must not stop the run
                last_exc = exc
                print(f"[batch] chapter {n} attempt {attempt} failed: {exc}")
                if attempt == 1:
                    print("[batch] retrying once…")
        if last_exc is not None:
            failed.append({"chapter": n, "file": md.name, "error": str(last_exc)})
            traceback.print_exception(type(last_exc), last_exc, last_exc.__traceback__)
        print()

    ok.sort(key=lambda r: r["chapter"])
    report = {"built": len(ok), "failed": len(failed), "ok": ok, "errors": failed}
    (Path(__file__).resolve().parent / "batch_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"[batch] done: {len(ok)} built, {len(failed)} failed")
    for r in ok:
        print(f"  H{r['chapter']:<2} {r['words']}w {r['cloze']}cl {r['mp3_kb']:>7}KB  {r['title']}")
    for f in failed:
        print(f"  H{f['chapter']:<2} FAILED — {f['error']}")


if __name__ == "__main__":
    main()