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
    # Full-test reports (dt_): the vocab marks drive SR; R/W/B + wrong words feed the
    # whole-chapter summary that the trainer screen actually shows.
    for chapter, vmarks, right, wrong, blank, wrong_words in inbound["report"]:
        words = _chapter_words(chapter)
        if words:
            applied += srs.record_chapter_recall(memory, chapter, vmarks, words)
        results.append({"label": f"Lesson {chapter}", "right": right, "wrong": wrong,
                        "blank": blank, "wrong_words": wrong_words})
    # Legacy vocab-only recall (db_) and cross-chapter review (dv_) — counts from marks.
    for chapter, marks in inbound["recall"]:
        words = _chapter_words(chapter)
        if not words:
            continue
        applied += srs.record_chapter_recall(memory, chapter, marks, words)
        forms = [w.get("form", w.get("id", "?")) for w in words]
        results.append(_from_marks(f"Lesson {chapter}", forms, marks))
    for date_iso, marks in inbound["review"]:
        ids = (memory.get("last_review") or {}).get("ids", [])
        applied += srs.record_review(memory, date_iso, marks)
        forms = [memory["words"].get(wid, {}).get("form", wid) for wid in ids]
        results.append(_from_marks("Review session", forms, marks))
    if inbound["last_id"]:
        memory["tg_offset"] = inbound["last_id"]
    print(f"[sync] inbound: {len(inbound['report'])} report, {len(inbound['recall'])} recall, "
          f"{len(inbound['review'])} review -> {applied} vocab outcome(s) applied")

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


def _from_marks(label: str, forms: list[str], marks: str) -> dict:
    """Build the unified count/word shape from a positional marks string ('1' right /
    '0' wrong / 'b' blank / 'x' untrained), naming the wrong words from `forms`."""
    wrong_words = [forms[i] for i, m in enumerate(marks) if m == "0" and i < len(forms)]
    return {"label": label, "right": marks.count("1"), "wrong": marks.count("0"),
            "blank": marks.count("b"), "wrong_words": wrong_words}


def _summary(results: list[dict], due: int, tracked: int, streak: int, link: str) -> str:
    """The English Telegram heartbeat: the whole-chapter test score (correct / wrong /
    blank, naming the wrong words) when results came in, or a plain 'nothing new' note
    when the inbox was empty this run."""
    lines = ["<b>📘 Delftse — daily sync</b>"]
    if results:
        for r in results:
            answered = r["right"] + r["wrong"]
            lines.append(f"\n<b>{esc(r['label'])}</b>: {r['right']}/{answered} correct"
                         f" · {r['wrong']} wrong · {r['blank']} blank")
            if r["wrong_words"]:
                shown = ", ".join(esc(w) for w in r["wrong_words"])
                extra = r["wrong"] - len(r["wrong_words"])
                if extra > 0:
                    shown += f" (+{extra} more)"
                lines.append("❌ Wrong: " + shown)
            elif r["wrong"] == 0 and r["blank"] == 0:
                lines.append("✅ All correct!")
            if r["blank"]:
                lines.append(f"⬜ {r['blank']} left blank")
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