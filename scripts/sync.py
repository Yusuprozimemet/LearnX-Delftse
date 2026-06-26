"""The personalization runner: Telegram taps -> spaced repetition -> published files.

One pass (the scheduled cron runs this):
  1. Read pending db_/dv_ deep-link taps from the bot (delftse.telegram.fetch_inbound).
  2. Fold them into delftse_memory.json (delftse.srs): chapter recalls reschedule each
     word; review reports update the cross-chapter schedule.
  3. Republish the learner's review/<token>.json (the 🔁 herhaling tab's due words) and
     progress/<token>.json (the ☁ cross-device scorecard) into trainer/.
  4. Save memory.

Needs the Telegram secrets in .env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
REVIEW_TOKEN_SECRET). Run:  python scripts/sync.py
"""
import html
import json
import os
import sys
from pathlib import Path

DELFTSE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DELFTSE))

import config  # noqa: E402
from delftse import review as review_mod  # noqa: E402
from delftse import srs, telegram  # noqa: E402

# In CI the data is a separate checkout from the code — DELFTSE_STATE_DIR points there
# (defaults to the repo root locally). srs.py reads the same var for the memory file.
STATE_DIR = Path(os.environ.get("DELFTSE_STATE_DIR") or DELFTSE)
TRAINER = STATE_DIR / "trainer"


def _chapter_words(chapter: int) -> list[dict]:
    """The chapter JSON's report.words ([{id, form}]) — the order the marks index."""
    p = TRAINER / f"delftse-{chapter}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("report", {}).get("words", [])
    except (OSError, json.JSONDecodeError):
        return []


def _publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    config.require("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "REVIEW_TOKEN_SECRET")
    token = srs.review_token(config.TELEGRAM_CHAT_ID)
    memory = srs.load_memory()

    inbound = telegram.fetch_inbound()
    applied = 0
    results: list[dict] = []          # one per test submitted this run, for the summary
    for chapter, marks in inbound["recall"]:
        words = _chapter_words(chapter)
        if not words:
            continue
        applied += srs.record_chapter_recall(memory, chapter, marks, words)
        wrong = [w.get("form", w.get("id", "?")) for w, m in zip(words, marks) if m == "0"]
        results.append({"label": f"Lesson {chapter}", "right": marks.count("1"),
                        "wrong": wrong, "skipped": marks.count("x")})
    for date_iso, marks in inbound["review"]:
        ids = (memory.get("last_review") or {}).get("ids", [])
        applied += srs.record_review(memory, date_iso, marks)
        wrong = [memory["words"].get(wid, {}).get("form", wid)
                 for wid, m in zip(ids, marks) if m == "0"]
        results.append({"label": "Review session", "right": marks.count("1"),
                        "wrong": wrong, "skipped": marks.count("x")})
    if inbound["last_id"]:
        memory["tg_offset"] = inbound["last_id"]
    print(f"[sync] inbound: {len(inbound['recall'])} recall, {len(inbound['review'])} review "
          f"-> {applied} word outcome(s) applied")

    # Republish the due-list + scorecard (even with no inbound — due dates move daily).
    payload = review_mod.build(memory, TRAINER, max_items=config.DUTCH_REVIEW_MAX)
    memory["last_review"] = {"date": payload["generated"], "ids": payload["ids"]}
    _publish(TRAINER / "review" / f"{token}.json", payload)
    _publish(TRAINER / "progress" / f"{token}.json", srs.build_progress(memory))
    srs.save_memory(memory)

    due, tracked, streak = len(payload["items"]), len(memory["words"]), memory["streak"]
    print(f"[sync] {due} word(s) due · {tracked} tracked · streak {streak}")
    print(f"[sync] your trainer link: delftse.html?u={token}")

    # Daily heartbeat back to Telegram so the loop is visible — best-effort.
    link = f"{config.TRAINER_URL}?u={token}"
    if telegram.send(_summary(results, due, tracked, streak, link)):
        print("[sync] heartbeat sent to Telegram")


def _summary(results: list[dict], due: int, tracked: int, streak: int, link: str) -> str:
    """The English Telegram heartbeat: a per-test breakdown when results came in,
    or a plain 'nothing new' note when the inbox was empty this run."""
    lines = ["<b>📘 Delftse — daily sync</b>"]
    if results:
        for r in results:
            answered = r["right"] + len(r["wrong"])
            head = f"\n<b>{esc(r['label'])}</b>: {r['right']}/{answered} correct"
            if r["skipped"]:
                head += f" ({r['skipped']} skipped)"
            lines.append(head)
            if r["wrong"]:
                lines.append("❌ Wrong: " + ", ".join(esc(w) for w in r["wrong"]))
            else:
                lines.append("✅ All correct!")
    else:
        lines.append("\nNo new test results since the last sync — "
                     "finish a lesson test and tap <b>Save results</b> to log it.")
    lines.append(f"\n🔁 {due} due to review · 📚 {tracked} words tracked · 🔥 streak {streak}")
    lines.append(f'<a href="{link}">▶️ Open your trainer</a>')
    return "\n".join(lines)


def esc(s: str) -> str:
    """Escape word forms for Telegram HTML parse mode."""
    return html.escape(str(s), quote=False)


if __name__ == "__main__":
    main()