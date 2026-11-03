# LearnX-Delftse plan: the Saywise direction

<!-- plan.md says what and why; the day specs in specs/ say how. It changes rarely, and only in a
plan-change PR. Keep it short. -->

## Verdict: grading what the learner says is the easy half; grading it the same way every time, without breaking Radar's recall format, is the hard half

Today the trainer checks recall: a word is right or wrong, the marks go to Telegram as
`db_`/`dv_`/`dt_` deep links, and `delftse/srs.py` widens or resets one interval per word. The
plan ends with the learner typing and then saying Dutch, each answer graded against the lesson's
own text, and the schedule keeping a mastery per concept. What makes it hard: the grader lives
inside one 1,300-line HTML page, nothing tests the page, and the stored recall format is shared
with LearnX-Radar.

| Hard part | Why | Fix |
|---|---|---|
| **Radar's recall format** | `delftse/srs.py` and `delftse/telegram.py` are vendored from Radar. Radar's `DUTCH_RECALL_ENABLED` path reads positional marks `1`/`0`/`x` (`delivery/telegram_recall.py`) and a recall log of `date`, `reported`, `right`, `wrong` (`storage/dutch_state.py`). Delftse adds the mark `b` and the keys `chapter` and `kind`, and no test pins what the two share | Phase 0 makes it a contract |
| **No tests on today's behaviour** | `tests/test_srs.py` (14 tests) covers the scheduler only. The answer checks (`norm`, `clean`, `dicteeOk`) are inline in `trainer/delftse.html`, and the payload parsers and lesson JSON have no tests | Phase 0 |
| **A grader that agrees with itself** | Any grader the agent writes will pass tests the agent writes. "Close enough" (a typo, a missing article, word order) is a judgement | Phase 1: gold grades labelled by the maintainer, frozen |
| **Private lesson text** | `book1/` and `youtube/` live in the private state repository and never enter this one, in a test fixture either | Gold items and tests use sentences written for them |
| **Speech recognition is not ours** | The Web Speech API exists in Chromium and Safari, not Firefox. It returns text with no punctuation, casing chosen by the engine, and numbers as digits. Chrome sends the audio to Google | Phase 2: the transcript goes through Phase 1's grader; typing stays the fallback |
| **Getting grades to the schedule** | The sync learns outcomes only through Telegram `/start` payloads, at most 64 characters, from a bot that Radar may also consume | Phase 3's first day decides the carrier, inside the contract |

## Decisions made before the work

- **Radar's format is a contract, not a dependency:** Delftse imports nothing from Radar. Tests
  in `tests/contract/` pin the shared format as Radar has it at `be835e5`: the `1`/`0`/`x`
  grammar and its positional meaning, the recall log keys, and the word keys `reps`, `due`,
  `recall_right`, `recall_wrong`. Delftse may add keys and its own payload prefixes. It never
  renames or drops a shared key, never reuses Radar's `dr_`/`rv_`/`lr_` prefixes, and never
  stores a mark Radar's grammar lacks.
- **One grader, in its own file:** `trainer/grade.js`, a pure function with no DOM, which the
  page loads and a pytest test runs under `node`. It replaces the inline `norm`/`clean`/`dicteeOk`
  checks. A grade is `right`, `close` (with the reason, named after the concept it misses) or
  `wrong`. On the wire it stays `1` or `0`, so the contract does not move.
- **"Against the lesson source"** means against the chapter JSON the sync builds from the book:
  its `new_words`, `sentences` and `dialogue`. It does not mean against an LLM's judgement, and
  the page calls no LLM at grading time.
- **Gold grades are the maintainer's:** the agent may draft items, but every label in
  `tests/gold/` is the maintainer's, set before the grader that must match it is written. Once
  merged, a gold file is frozen; a new one may be added.
- **Style check:** `ruff check delftse tests config.py && ruff format --check delftse tests config.py`.
  Today ruff format would rewrite 12 files. That first formatting is its own PR before Day 1, with
  an `Oversized:` line if it needs one, and the repository variable `STYLE_CHECK_COMMAND` is set
  after it merges. The page's JavaScript has no style check.

## Target structure

```
delftse/            sync side (Python): srs.py gains per-concept mastery in Phase 3
trainer/
  delftse.html      the page; calls grade.js and, in Phase 2, the speech adapter
  grade.js          the one grader (Phase 1)
tests/
  contract/         Radar's recall format (Phase 0), frozen
  gold/             the maintainer's graded answers (Phase 1, Phase 2), frozen
  test_*.py         today's behaviour (Phase 0) and each day's checks
```

## Phases

Each phase is a series of <400-line PRs, so CI stays green.

### Phase 0 — Pin today's behaviour
**Goal:** every behaviour the later phases touch is held by a test, and Radar's recall format is a
contract.
- Characterisation tests for what the code does now: the `db_`/`dv_`/`dt_` parsers in
  `delftse/telegram.py`, `record_chapter_recall` and `record_review` with the `x` and `b` marks,
  `build_progress`, and the lesson JSON that `delftse/trainer.py` and `delftse/cloze.py` compose.
- `tests/contract/`: a recall log and word entries written by Delftse parse under Radar's grammar
  and keys. A `b` mark is never stored. Delftse's prefixes are disjoint from Radar's.
- **Done when:** `python -m pytest -q -p no:cacheprovider tests` passes, and each contract test
  has a recorded `broken:` line: a renamed recall key, or a stored `b` mark, fails it.

### Phase 1 — Typed production, graded
**Goal:** the learner types Dutch, and the page grades it against the lesson source the same way
every time.
- The gold set first: `tests/gold/typed.json`, items of lesson text, typed answer and the
  maintainer's grade, covering exact, case and punctuation, a typo, a missing or wrong article,
  word order, a wrong word, and an empty answer.
- `trainer/grade.js`, and a pytest test that runs it over the gold set under `node`, with CI's
  test job given `node`.
- The test, dictee and schrijven tabs grade through it: schrijven asks for a sentence from the
  lesson, not only a free one, and shows the grade and its reason. The deep-link marks stay
  `1`/`0`/`x`/`b`.
- **Done when:** the grader matches every gold label (the test prints the count), the contract
  tests pass unedited, and no inline answer check is left in `trainer/delftse.html`.

### Phase 2 — Voice
**Goal:** the learner can say the answer instead of typing it, in browsers that support it.
- A speech adapter on the browser's `SpeechRecognition` (or `webkitSpeechRecognition`), with
  `lang` set to `nl-NL`. It turns one utterance into one transcript and hands it to
  `grade.js`. No audio is stored or sent by the page itself.
- Feature detection: with no recognizer, or with the permission refused, the microphone button is
  absent and typing works as before.
- `tests/gold/spoken.json`: transcripts as the recognizer returns them (no punctuation, digits
  for numbers), with the maintainer's grades. The grader normalises them.
- **Done when:** the spoken gold set passes. A test with a fake recognizer shows that a
  transcript reaches the same grade as the same text typed, and that the page without one has
  no microphone button. The maintainer's check on a phone is recorded in the day's Notes.

### Phase 3 — Mastery per concept
**Goal:** the schedule tracks how well each concept is produced, not only whether a word was
recalled.
- A concept is a word id from the lesson source, or one of the grammar points that `grade.js`
  names in a `close` reason (an article, verb position, a conjugation). The list is fixed in code,
  not inferred.
- Mastery per concept in `delftse_memory.json` under a new key, moved by production grades
  (`right` up, `close` part-way, `wrong` down), and a due date per concept from it. The herhaling
  list draws on due concepts.
- The carrier from page to sync (first day of the phase): a new Delftse payload prefix within the
  64-character limit, or another route the day's spec argues for. Radar's prefixes, grammar and
  keys stay as the contract pins them.
- **Done when:** `tests/test_srs.py` and the phase's tests show a concept's interval following its
  grades, and the contract and gold tests pass unedited. A memory file from before the phase
  loads and schedules as before.

## Stopping points

- **Day 06 (Phase 1, typed production graded) is a stopping point.** The plan-auditor reads the
  whole plan against the code before anything goes on: whether voice is still worth building, and
  whether the gold set's labels held up in use.
- **Day 13 (Phase 3, mastery per concept) is the end.** The plan-auditor on the whole plan and the
  three end-of-phase auditors run on fresh `main`.

## Constraints

- `tests/contract/` and `tests/gold/` are never edited (`spec-drift.config.json`'s `frozen`). A
  change that seems to need one is a plan-change PR, decided by the maintainer.
- No lesson text, audio or progress from the private state repository in this one, in a test
  fixture either.
- The page keeps working offline from cached lessons, and in a browser with no speech recognition.
- Delftse and Radar must not both consume the shared bot's updates (`delftse/telegram.py`).
  Nothing in this plan adds a second consumer.

## What we are not doing

- **No gestures.** Answers are typed or spoken; swipe or shake input is not graded production.
- **No gym mode.** No timed drills or rep counters; the schedule decides what comes back.
- **No leagues.** One learner, private progress; no rankings, no shared scores.
- **No server-side or LLM grading.** The grader runs in the page, deterministically.

## Course corrections

<!-- Added by plan-change PRs, newest first: what a stopping point or audit found, and what the
maintainer chose. Empty at the start. A stop's decisions go under their own heading,
"## Course correction after Phase N", N the stop item's phase: that heading lifts the stop. An
entry left here lifts it only if it names "Phase N"; one that names only the day does not. -->

## Day-by-day specs

The phases are broken into day specs in [`specs/`](specs/). Read
[`specs/README.md`](specs/README.md) for the workflow. The day counts are estimates; a phase's
first day may move them in a plan-change PR.

1. Phase 0: Days 01-02.
2. Phase 1: Days 03-06. **Stop and evaluate.**
3. Phase 2: Days 07-09.
4. Phase 3: Days 10-13. **Stop and evaluate.**
