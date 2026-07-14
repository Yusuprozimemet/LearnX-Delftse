"""Unit tests for delftse/srs.py — the spaced-repetition scheduling logic.

The scheduling rules under test (see srs.py's docstrings):
  * a chapter recall report is the introduction event: '1' -> reps++ (interval
    widens), '0' -> reps reset to 1 (due pulled back to base), 'x'/'b' -> untouched;
  * the latest report for a chapter supersedes the earlier one in the recall log;
  * a dv_ cross-chapter review applies positionally over last_review.ids, once.

Run:  python -m pytest tests/ -q
"""
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from delftse import srs

D0 = date(2026, 1, 1)
WORDS = [{"id": "gracht", "form": "de gracht"},
         {"id": "brug", "form": "de brug"},
         {"id": "voogd", "form": "de voogd"}]


def iso(d: date, days: int = 0) -> str:
    return (d + timedelta(days=days)).isoformat()


def test_config_defaults_assumed_by_these_tests():
    # The exact-day assertions below assume the shipped spacing; if you tune the
    # config, update the expectations here.
    assert config.DUTCH_SR_BASE_INTERVAL_DAYS == 1
    assert config.DUTCH_SR_SPACING_FACTOR == 2.2


def test_interval_widens_with_reps():
    # reps: 1 -> 1 day, 2 -> 2, 3 -> 5, 4 -> 11 (1 * 2.2^(reps-1), rounded)
    assert [srs._interval_days(r) for r in (0, 1, 2, 3, 4)] == [1, 1, 2, 5, 11]


# --- record_chapter_recall --------------------------------------------------------
def test_first_recall_introduces_and_schedules():
    m = srs._default()
    applied = srs.record_chapter_recall(m, 5, "10x", WORDS, when=D0)
    assert applied == 2                                   # 'x' not applied
    g, b = m["words"]["gracht"], m["words"]["brug"]
    assert g["introduced"] == iso(D0) and g["reps"] == 1 and g["due"] == iso(D0, 1)
    assert g["recall_right"] == 1 and g["form"] == "de gracht" and g["chapter"] == 5
    assert b["reps"] == 1 and b["recall_wrong"] == 1 and b["due"] == iso(D0, 1)
    assert "voogd" not in m["words"]                      # 'x' never enters tracking
    assert m["streak"] == 1
    assert m["recall"][-1] == {"chapter": 5, "date": "book1-05", "reported": iso(D0),
                               "right": ["gracht"], "wrong": ["brug"]}


def test_repeated_right_widens_and_wrong_resets():
    m = srs._default()
    srs.record_chapter_recall(m, 5, "1xx", WORDS, when=D0)
    srs.record_chapter_recall(m, 5, "1xx", WORDS, when=D0 + timedelta(days=1))
    e = m["words"]["gracht"]
    assert e["reps"] == 2 and e["due"] == iso(D0, 1 + 2)  # interval widened to 2 days
    srs.record_chapter_recall(m, 5, "1xx", WORDS, when=D0 + timedelta(days=3))
    assert m["words"]["gracht"]["reps"] == 3 and m["words"]["gracht"]["due"] == iso(D0, 3 + 5)
    # a miss resets to the base interval, keeping the introduction date
    srs.record_chapter_recall(m, 5, "0xx", WORDS, when=D0 + timedelta(days=8))
    e = m["words"]["gracht"]
    assert e["reps"] == 1 and e["due"] == iso(D0, 8 + 1) and e["introduced"] == iso(D0)
    assert e["recall_right"] == 3 and e["recall_wrong"] == 1


def test_blank_marks_never_penalize():
    m = srs._default()
    srs.record_chapter_recall(m, 5, "1xx", WORDS, when=D0)
    before = dict(m["words"]["gracht"])
    assert srs.record_chapter_recall(m, 5, "bbb", WORDS, when=D0 + timedelta(days=1)) == 0
    assert m["words"]["gracht"] == before                 # untouched by a blank report


def test_restudy_supersedes_chapter_log_entry():
    m = srs._default()
    srs.record_chapter_recall(m, 5, "10x", WORDS, when=D0)
    srs.record_chapter_recall(m, 7, "1xx", WORDS, when=D0)
    srs.record_chapter_recall(m, 5, "11x", WORDS, when=D0 + timedelta(days=1))
    entries = [r for r in m["recall"] if r["chapter"] == 5]
    assert len(entries) == 1 and entries[0]["right"] == ["gracht", "brug"]
    assert m["streak"] == 2                               # chapters 5 and 7


def test_youtube_chapter_numbers_keep_all_digits():
    m = srs._default()
    srs.record_chapter_recall(m, 101, "1xx", WORDS, when=D0)
    assert m["recall"][-1]["date"] == "book1-101"
    assert m["words"]["gracht"]["chapter"] == 101


def test_short_marks_apply_to_prefix_only():
    m = srs._default()
    # A 1-char marks string over 3 words: only the first word is applied.
    assert srs.record_chapter_recall(m, 5, "1", WORDS, when=D0) == 1
    assert set(m["words"]) == {"gracht"}


# --- record_review (dv_ cross-chapter report) --------------------------------------
def _memory_with_review() -> dict:
    m = srs._default()
    srs.record_chapter_recall(m, 5, "11x", WORDS, when=D0)
    m["last_review"] = {"date": iso(D0, 10), "ids": ["gracht", "brug", "ghost"]}
    return m


def test_review_applies_positionally_once():
    m = _memory_with_review()
    when = D0 + timedelta(days=10)
    applied = srs.record_review(m, iso(D0, 10), "101", when=when)
    assert applied == 2                                   # "ghost" isn't tracked
    assert m["words"]["gracht"]["reps"] == 2              # 1 -> 2, widened
    assert m["words"]["gracht"]["due"] == iso(when, 2)
    assert m["words"]["brug"]["reps"] == 1                # wrong -> reset
    assert m["words"]["brug"]["due"] == iso(when, 1)
    assert m["recall"][-1]["kind"] == "review"
    # a second tap of the same deep link must be a no-op
    assert srs.record_review(m, iso(D0, 10), "101", when=when) == 0


def test_review_rejects_stale_or_unknown_date():
    m = _memory_with_review()
    assert srs.record_review(m, "000000", "11", when=D0) == 0
    assert m["words"]["gracht"]["reps"] == 1              # nothing applied


# --- due_words / build_progress ----------------------------------------------------
def test_due_words_sorted_oldest_first():
    m = srs._default()
    m["words"] = {"a": {"due": iso(D0, 2)}, "b": {"due": iso(D0)},
                  "c": {"due": iso(D0, 5)}, "d": {}}      # d: never scheduled
    assert srs.due_words(m, today=D0 + timedelta(days=2)) == ["b", "a"]
    assert srs.due_words(m, today=D0 + timedelta(days=9)) == ["b", "a", "c"]


def test_build_progress_counts():
    m = srs._default()
    srs.record_chapter_recall(m, 5, "10x", WORDS, when=D0)
    p = srs.build_progress(m)
    assert p["streak"] == 1 and p["words_tracked"] == 2
    assert p["days"] == [{"date": "book1-05", "right": 1, "wrong": 1}]


# --- load_memory robustness --------------------------------------------------------
def test_load_memory_survives_missing_and_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(srs, "MEMORY_FILE", tmp_path / "delftse_memory.json")
    assert srs.load_memory() == srs._default()            # missing file
    srs.MEMORY_FILE.write_text("{not json", encoding="utf-8")
    assert srs.load_memory() == srs._default()            # corrupt file
    srs.MEMORY_FILE.write_text('{"streak": 3}', encoding="utf-8")
    m = srs.load_memory()                                 # partial file: keys backfilled
    assert m["streak"] == 3 and m["words"] == {} and m["version"] == 1


def test_review_token_stable_and_scoped():
    t1, t2 = srs.review_token(12345), srs.review_token(12345)
    assert t1 == t2 and len(t1) == 16 and t1 != srs.review_token(54321)
