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
    for chapter, marks in inbound["recall"]:
        words = _chapter_words(chapter)
        if words:
            applied += srs.record_chapter_recall(memory, chapter, marks, words)
    for date_iso, marks in inbound["review"]:
        applied += srs.record_review(memory, date_iso, marks)
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

    print(f"[sync] {len(payload['items'])} word(s) due · {len(memory['words'])} tracked "
          f"· streak {memory['streak']}")
    print(f"[sync] your trainer link: delftse.html?u={token}")


if __name__ == "__main__":
    main()