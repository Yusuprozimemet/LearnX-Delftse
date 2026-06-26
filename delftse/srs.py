"""Spaced-repetition state for LearnX-Delftse (delftse_memory.json).

Adapted from LearnX-Radar's storage/dutch_state.py, but a FIXED book changes the
model: there is no daily delivery that "introduces" words, so a chapter's recall
report is itself the scheduling event. A word is introduced the first time its
chapter is reported; a correct recall widens its interval, a wrong one resets it.

Shape:
  {"version":1, "streak":N, "last_run":ISO, "tg_offset":int,
   "words": {id: {introduced, reps, last_review, due, form, chapter,
                  recall_right, recall_wrong}},
   "recall": [{chapter, date:"book1-NN", reported, right:[id...], wrong:[id...]}],
   "last_review": {date, ids}}        # for mapping a dv_ report back to ids
"""
import hashlib
import hmac
import json
import os
from datetime import date, timedelta
from pathlib import Path

import config

# State (the SR memory + published files) lives next to the code locally, but in CI the
# data is a separate checkout — DELFTSE_STATE_DIR points the sync there.
STATE_DIR = Path(os.environ.get("DELFTSE_STATE_DIR") or Path(__file__).resolve().parent.parent)
MEMORY_FILE = STATE_DIR / "delftse_memory.json"
RECALL_LOG_KEEP = 200


def _default() -> dict:
    return {"version": 1, "streak": 0, "last_run": None, "tg_offset": 0,
            "words": {}, "recall": [], "last_review": {}}


def load_memory() -> dict:
    if not MEMORY_FILE.exists():
        return _default()
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default()
    if not isinstance(data, dict):
        return _default()
    for k, v in _default().items():
        data.setdefault(k, v)
    return data


def save_memory(memory: dict) -> None:
    MEMORY_FILE.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")


def review_token(chat_id) -> str:
    """Unguessable, stable token naming the published review/progress files (HMAC of
    the chat id under REVIEW_TOKEN_SECRET) — review/<token>.json isn't enumerable."""
    return hmac.new(str(config.REVIEW_TOKEN_SECRET).encode(),
                    str(chat_id).encode(), hashlib.sha256).hexdigest()[:16]


def _interval_days(reps: int) -> int:
    base = config.DUTCH_SR_BASE_INTERVAL_DAYS
    factor = config.DUTCH_SR_SPACING_FACTOR
    return max(1, round(base * (factor ** max(0, reps - 1))))


def record_chapter_recall(memory: dict, chapter: int, marks: str,
                          words: list[dict], when: date | None = None) -> int:
    """Fold a db_<chapter>_<marks> report into the schedule; returns words applied.

    `words` is the chapter JSON's report.words ([{id, form}], the exact order the
    marks index). '1' recalled -> reps++ (interval widens); '0' failed -> reps reset
    to 1, due pulled to base interval; 'x' untrained -> untouched. Latest report for a
    chapter supersedes an earlier one (a re-study)."""
    today = (when or date.today()).isoformat()
    wdict = memory.setdefault("words", {})
    right_ids: list[str] = []
    wrong_ids: list[str] = []
    for w, mark in zip(words, marks, strict=False):
        wid = w.get("id")
        if not wid or mark in ("x", "b"):       # untrained / left blank: don't penalize
            continue
        e = wdict.get(wid) or {"introduced": today, "reps": 0}
        e["form"] = w.get("form", wid)
        e["chapter"] = chapter
        e["last_review"] = today
        if mark == "1":
            e["reps"] = int(e.get("reps", 0)) + 1
            e["recall_right"] = int(e.get("recall_right", 0)) + 1
            right_ids.append(wid)
        else:
            e["reps"] = 1
            e["recall_wrong"] = int(e.get("recall_wrong", 0)) + 1
            wrong_ids.append(wid)
        e["due"] = (date.fromisoformat(today)
                    + timedelta(days=_interval_days(e["reps"]))).isoformat()
        wdict[wid] = e
    if not right_ids and not wrong_ids:
        return 0
    log = [r for r in memory.get("recall", []) if r.get("chapter") != chapter]
    log.append({"chapter": chapter, "date": "book1-%02d" % chapter, "reported": today,
                "right": right_ids, "wrong": wrong_ids})
    memory["recall"] = log[-RECALL_LOG_KEEP:]
    memory["streak"] = len(memory["recall"])          # chapters with a saved result
    memory["last_run"] = today
    return len(right_ids) + len(wrong_ids)


def record_review(memory: dict, date_iso: str, marks: str,
                  when: date | None = None) -> int:
    """Fold a dv_<date>_<marks> cross-chapter review report; positional over
    memory['last_review']['ids'] (the order the last published review used)."""
    last = memory.get("last_review") or {}
    if last.get("date") != date_iso or last.get("reported"):
        return 0
    today = (when or date.today()).isoformat()
    wdict = memory.setdefault("words", {})
    right_ids: list[str] = []
    wrong_ids: list[str] = []
    for wid, mark in zip(last.get("ids", []), marks, strict=False):
        e = wdict.get(wid)
        if e is None or mark in ("x", "b"):     # untrained / left blank: don't penalize
            continue
        if mark == "1":
            e["reps"] = int(e.get("reps", 1)) + 1
            e["recall_right"] = int(e.get("recall_right", 0)) + 1
            right_ids.append(wid)
        else:
            e["reps"] = 1
            e["recall_wrong"] = int(e.get("recall_wrong", 0)) + 1
            wrong_ids.append(wid)
        e["due"] = (date.fromisoformat(today)
                    + timedelta(days=_interval_days(e["reps"]))).isoformat()
    if not right_ids and not wrong_ids:
        return 0
    last["reported"] = True
    memory["recall"].append({"chapter": 0, "date": date_iso, "reported": today,
                             "right": right_ids, "wrong": wrong_ids, "kind": "review"})
    memory["recall"] = memory["recall"][-RECALL_LOG_KEEP:]
    return len(right_ids) + len(wrong_ids)


def due_words(memory: dict, today: date | None = None) -> list[str]:
    """Ids whose review is due on/before today, oldest due first."""
    today_iso = (today or date.today()).isoformat()
    due = [(wid, e.get("due", "")) for wid, e in memory.get("words", {}).items()
           if e.get("due", "") and e["due"] <= today_iso]
    due.sort(key=lambda p: p[1])
    return [wid for wid, _ in due]


def build_progress(memory: dict) -> dict:
    """Cross-device scorecard the trainer fetches as progress/<token>.json. `days` are
    keyed by the page's chapter id ("book1-NN") with right/wrong COUNTS."""
    days = []
    for r in memory.get("recall", []):
        if r.get("date"):
            days.append({"date": r["date"], "right": len(r.get("right", [])),
                         "wrong": len(r.get("wrong", []))})
    return {"streak": memory.get("streak", 0),
            "words_tracked": len(memory.get("words", {})),
            "cefr": config.DUTCH_CEFR_START, "days": days}