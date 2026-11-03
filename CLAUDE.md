# Working in this repository

This repository develops LearnX-Delftse, an audio-first Dutch trainer with spaced repetition, one day spec at a
time, and records what each day turned up. Three layers have to agree: [`plan.md`](plan.md) is the goal and
changes rarely; the day specs in [`specs/`](specs/) are the steps; the code is where the steps
land. The spec rules are in [`specs/README.md`](specs/README.md) and the template in
[`specs/_template.md`](specs/_template.md). This file is the working order and the lessons the
rules do not state. [`docs/workflow.md`](docs/workflow.md) draws it.

**Source of truth, when they disagree:** the code on `main` for what the system does; the merged
spec for what a day promised; `plan.md` for the goal. The dashboard and memory only record
the others: correct them, never the other way round.

## Starting

A person or an agent new to this repository: read `plan.md`, then run
`python scripts/behaviour-index.py` to see what the system does now, then `specs/README.md`; run
`python scripts/spec-drift.py --out spec-drift.json` (it needs `numpy` and the `gh` CLI; or open
the dashboard) for the current day and next step; then ask for it, for example "start Day 12". Nothing merges without the
maintainer, and nothing starts after a phase ends until they say so.

## A day, in order

1. **Audit before writing anything.** On the first day of a phase, run the **plan-auditor**
   agent on the phase first: do the specs still add up to what `plan.md` wants from it, and
   does the code still match their premises? Then, every day, run the **spec-auditor** agent on
   the day: every check run on the code as it is, every `hold` tried to break, every claim
   counted. Both only report. Fix what they find in a **spec-change PR** first (a **plan-change
   PR** when the code shows `plan.md` itself is wrong, which should be rare):
   `day-NN/spec-<slug>`, titled `Day NN spec change: <what was wrong>`. Every criterion is tagged
   `new` (fails today; the spec says how) or `hold` (true today; broken on purpose once).
2. **One PR per track**, each branched from fresh `main` after the previous one merged. Never
   stack branches. `day-NN/track-<letter>-<slug>`, titled `Day NN track X: <what it does>`. A
   Track 0 holds the tests that must pass before and after the change, and lands first.
3. **A closing PR:** `day-NN/close-the-day`. Tick each criterion with its evidence (test or
   command, and the PR it landed in), add Notes to the spec, and update the Day entry and measurement
   table in [`docs/lab-notebook.md`](docs/lab-notebook.md) (the README keeps only its Findings). Run `python scripts/token-usage.py` and commit the refreshed
   `docs/dashboard/token-usage.json`: CI cannot read the transcripts it counts. Then relabel the
   day's defects blind: `python scripts/relabel.py blind specs/day-NN-*.md --out <dir>`, give
   `<dir>/blind.md` to the **defect-rater** agent, save its JSON as `<dir>/labels.json`, run
   `python scripts/relabel.py compare <dir>`, and put its lines in the PR with a decision on each
   disagreement: the tag kept, and why, or changed in the Notes. Commit the break manifest
   `specs/breaks/day-NN.json` with the actual mutation arguments (format and example in
   [`specs/README.md`](specs/README.md#evidence-at-the-close)). After this closing PR merges,
   on fresh `main`, run
   `python scripts/evidence.py specs/day-NN-slug.md --ref main --json evidence.json` and retain
   the report for local runs. CI replays automatically after the merge; check its run in step 5.
   Replay finds a **merged closing commit**; squash-merged closing PRs count too. Keep **missing** evidence/report, **stale**
   inputs or closing commit, and **failed** replay checks separate; resolve each before
   advancing. Execution errors also block advancement; review reasons for inapplicable checks.
4. **After opening any PR, stop and wait for the maintainer to say "merged".** Do not start the
   next step, and do not start a new phase, until told.
5. **After a closing PR's "merged", check evidence before advancing:** run
   `gh run list --workflow evidence.yml --commit <sha>` for its merge commit, then
   `gh run view <run-id> --exit-status`. Require a completed successful run; a failed, missing,
   pending or cancelled run blocks the next step. Inspect its checks and retain its evidence
   artifact. This post-merge gate cannot block its own merge and is not a required status check.
   **After every "merged", refresh the dashboard before the next step:** the merge rebuilds the
   [public one](https://yusuprozimemet.github.io/LearnX-Delftse/dashboard/) by itself (`.github/workflows/dashboard.yml`). On fresh
   `main`, run `python scripts/spec-drift.py` and report the next step it prints. The page on
   GitHub Pages is the only dashboard; nothing else is republished.
6. **When a phase's last closing PR has merged,** after step 5, on fresh `main`: run the
   plan-auditor on the phase that just ended and on the next (at the end of Phase 3,
   Day 13, the three end-of-phase auditors too), report, and stop. They never run inside the
   closing PR: they read what merged.

Tracks may run in a different order from the spec's table when one depends on another; say so in
the PR.

## One session per step

Every reply re-reads the whole conversation, and that re-reading is most of what the project
costs: in jobmatch-microservices, up to Day 17, 64% of the main sessions' context tokens were read after a session had
passed 300k (the Tokens panel on the dashboard, "context per reply"). So a step starts in a fresh
session: after reporting the next step in step 5, suggest the maintainer start a new session for
it (or `/compact` if they keep this one). Nothing is lost: the spec, `spec-drift.json`, this file
and memory carry what the next step needs. `python scripts/token-usage.py --simulate` prints what
a fresh session past 100k, 200k or 300k, or each tool result capped at 2k or 5k tokens, would have
saved on this machine's transcripts.

Output that lands in the main session is re-read by every later reply. Keep it small:

- Test output and CI logs: filter to the failures and the counts (`grep`, `tail`), or have a
  subagent read them and return the summary. Never print a whole log or report.
- Replies are the cost, more than their size: batch independent calls into one reply, and
  checks into one shell command that prints one line per item. Wait for CI with
  `gh pr checks <n> --watch > <file>`, then print only the final `gh pr checks <n>`.
- Read the part of a file you need (`offset`/`limit`, `grep -n`), not the whole file, unless the
  step changes all of it. Diffs: `--stat` first, then the files that matter. A hook enforces the
  first half ("Where things live").

## The auditors

`.claude/agents/plan-auditor.md` and `.claude/agents/spec-auditor.md`. Separate agents with their
own brief and a fresh context, so the session that implements is not the only one checking its
own specs: in this repository that session has read past its own errors more than once.

| When | Run | What it answers |
|---|---|---|
| first day of a phase, before anything else | plan-auditor | Do this phase's specs still deliver what `plan.md` wants, on today's code? |
| every day, before the spec-change PR | spec-auditor | Is every claim and check in this day's spec true, and can each check fail? |
| a phase's last closing PR has merged, on fresh `main` | plan-auditor | What did the phase change that the next one must know? |
| a stopping point `plan.md` names, before going on | plan-auditor, on the whole plan | Is `plan.md` still the right plan, and what has piled up that no day owns? |
| the closing PR of Phase 3 (Day 13, mastery per concept, which ends the plan) has merged, on fresh `main` | security-ops-auditor, agentic-engineering-auditor, architecture-auditor | Can the workflow take more autonomy safely; what does it show about agents; do its gates hold? |
| every close, after the Notes are written | defect-rater, on `relabel.py`'s blind file | Does a reader who did not write the day tag its defects the same way? |
| any time, by hand | either | "use the plan-auditor on Phase 3" |

Both check hand-offs: what earlier days' Notes and test comments defer to a later day. Nothing
else carries them forward, and that is where most gaps have come from.

The three end-of-phase auditors run once, on Opus, on fresh `main` after the closing PR of
Phase 3 (Day 13, mastery per concept, which ends the plan) has merged, beside the plan-auditor on the last phase and on the whole plan. Each runs from a fresh context and reads none of
the others' reports: their disagreement is part of the result.

"Read-only" is enforced as far as a hook can: `.claude/settings.json` runs
`scripts/auditor-guard.py` on every Bash command and every subagent's stop. For the five
auditors with Bash it blocks a git command that changes a repository and a `gh` command that is
not a read, and at the auditor's stop it compares HEAD, the refs, the stash and `git status` with
what they were and sends the auditor back once if they changed; the main session and other agents
pass. It is not a sandbox: it reads the command, not what a program does (`python -c` that writes
a file gets through), and the stop sees only what git shows in this repository. The hooks sit in
the project settings, not the agents' frontmatter: the VS Code extension's Claude Code (2.1.312)
ignored frontmatter hooks. VS Code Copilot reads the same settings, ignores the matcher and sets
no `CLAUDE_PROJECT_DIR`; the hook command reads that variable in Python and passes without it, since a
missing script exits 2 and denied every Copilot tool call (seen in PR #58). An edit to `.claude/` takes effect in the next session, not this one.

Their findings are advice to the maintainer. What becomes a PR is the maintainer's call.

## The implementer

`.claude/agents/implementer.md`, on Haiku (`claude-haiku-5-5`, its `model:` line): the maintainer's
choice, so that code is written by a smaller model and everything else stays with the main session. For each track, the main session
writes the brief (files, changes, tests), sends it, and then does what the implementer does not:
reviews the diff, runs the full checks in "Before pushing", breaks the code on purpose, commits,
and writes the PR. Specs, audits, spec-change PRs and closing PRs stay with the main session. The
implementer never commits or pushes.

A brief that carries the spec's exact expected outputs lets the implementer finish in one pass.
Every track PR shows its new tests failing first (`specs/README.md`). When the tests live under
`tests/`, `python scripts/seen-red.py -- <the test command>` runs the branch's `tests/` on
`main`'s code in a scratch worktree and prints the `seen red: ... → <what it reported>` line for
the PR; exit 1 means they pass there. Otherwise, without a Track 0 or an extra commit, stash the
code change (`git stash push -- <the code's directory>`), run the new tests on `main`'s code,
compare with what the spec says fails today, `git stash pop`, and put what they reported in the
PR. CI's "Track tests seen red" re-runs each `new` criterion's test this way only when the
repository variable `TRACK_RED_RUNNER` is `unittest` (README, setup); unset, it reports "not
configured" and passes.

Re-count any number the implementer reports (lines, tests, entries in a file) before it goes in
a PR: in wordtally, Day 4's report said a list held 40 words; it held 45.

## Before pushing

- Run the check-only style command `ruff check delftse tests config.py && ruff format --check delftse tests config.py`; it must fail on unformatted code and never rewrite files. It covers the Python only; the page's JavaScript has no style check.
- Run the tests as CI does: `python -m pytest -q -p no:cacheprovider tests` (`.github/workflows/tests.yml`). Nothing needs
  building first; the tests need only `pytest`, not `requirements.txt`'s TTS and LLM clients.
- There is no report directory: read pytest's last line, "N passed", and check that N is the
  count the change should give, not only the exit code.
- A test that passes before and after a change proves nothing until it has been seen to fail.
  Break the code on purpose, run it, record what it reported, revert. The break never merges.
  Record it in one line, `broken: <what> → <what it reported>` (`specs/README.md`): the
  dashboard reads no other wording. `scripts/break.py` does one break this way and prints the
  line: one exact replacement, the file's bytes put back, the command run without a shell, a build
  only with `clean`. Hand-run breaks have half-applied, skipped the compile, never started the
  build, and lost unstaged work.
- The PR diff must stay under **400 changed lines** (CI: "Diff stays reviewable"). If it does not,
  split the PR along a line where each part stands alone. An `Oversized:` line in the
  description turns the failure into a warning. <!-- PROJECT: each time Oversized: was used,
  with the PR, its size and why it could not split --> `main` is protected, so the check must
  pass before a merge.
- A grep criterion cannot tell a comment from code: do not name, in a comment, the thing the
  day's grep checks is gone.

## Stop and ask, do not patch

- A change seems to need an edit to `tests/contract/` (Radar's recall format: the `1`/`0`/`x`
  marks, the recall log and word keys) or to a merged gold file in `tests/gold/` (the
  maintainer's grades), expected values or timing (`specs/README.md`). The code under
  `delftse/` and `trainer/` may change around them, and a new gold file may be added. That is
  the signal the spec's premise is wrong.
- A criterion cannot be met as written. Record the departure; do not deviate silently.

## Where things live

- **Data:** no database. Lesson content, audio and the learner's progress live in the private
  LearnX-Delftse-state repository; `.gitignore` keeps them (`book1/`, `youtube/`,
  `trainer/progress/`, `delftse_memory.json`, ...) out of this public one. Never commit them, in
  a test fixture either: a test builds its own data.
- **Pages:** one site, two parts. `.github/workflows/dashboard.yml` publishes the trainer
  (`index.html`, `trainer/`) at the root and the dashboard under `dashboard/`.
- **Measurement:** `python scripts/spec-drift.py --out spec-drift.json` rebuilds the project
  dashboard's data from git and GitHub; `--build` writes the page (`docs/dashboard/`) with it, and until the first day spec exists it shows example data under a banner; `--out` never does.
  Every merge to `main` republishes it to [GitHub Pages](https://yusuprozimemet.github.io/LearnX-Delftse/dashboard/).
- **Whole-file Reads:** a PreToolUse hook (`scripts/read-guard.py`) refuses a Read with no `offset`, `limit` or `pages` of a file over 8,000 bytes, and names its size and line count. Find the part with `grep -n` and read that range; to read all of it on purpose, give the `limit` it names. Edit works after a ranged Read. Images, PDFs and notebooks pass.
- **Long Bash output:** a PostToolUse hook (`scripts/cap-output.py`) cuts stdout over 8,000 characters to its head and tail; the full output is in `.claude/tool-output/` (gitignored). Claude Code merges stderr into stdout, so a failing command's error comes back at the end of stdout with stderr `''`: it is cut, and the tail (last 3,000 characters) keeps it; the stderr guard only catches a separate stderr. An interrupted command is left whole, as is output of 30,000 characters or more, which Claude Code saves itself.

## Pitfalls this repository has already hit

- A small out-of-day behaviour change still needs a seen-red test: use [Short specs](specs/README.md#short-specs) when it qualifies.

What a session learns goes here, not into the next session's prompt: every session reads this
file. <!-- PROJECT: each pitfall this project has hit, one bullet each: what happened and
what to do instead. The bullets below came from the kit's first copy; keep those that hold. -->

On Windows, in Git Bash and PowerShell:

- The working tree may be CRLF (`core.autocrlf`): check `git ls-files --eol`, and expect git to
  rewrite a file it touches (a `git stash pop` does). Exact-string edits from a script fail on
  CRLF: use the Edit tool, or normalise and write the ending back. A test that writes a file
  writes bytes: text mode adds a carriage return and changes every count. `git show <sha>:<file>`
  prints LF: diffed against the CRLF tree, every line differs, so use `diff --strip-trailing-cr`.
- Windows PowerShell `Set-Content` and `Add-Content` can write in the system ANSI codepage,
  turning em dashes and arrows into `?`: write Markdown with `-Encoding utf8` or the Write tool,
  and read the saved file back as UTF-8 before committing.
- A path in JSON has its backslashes escaped: compare the parsed object, not the string.
- Long heredocs break in Git Bash, and PowerShell has none. Write commit messages and PR bodies
  to a scratch file (`git commit -F`, `gh pr create --body-file`); give the maintainer one-line
  commands, with any JSON in a file (`gh api --input <file>`).
- The Write tool will not overwrite a file this session has not read, even one a shell made.

Breaks on purpose:

- Name the exact test, and check that the run failed: an error in the setup proves nothing.
  Clear the language's caches first (Python: `PYTHONDONTWRITEBYTECODE=1`, no `__pycache__`).
- Run the break, then copy its `broken:` line from what `scripts/break.py` printed. A line
  written before the run differed from the report.
- On Windows, `bash` may be WSL's, which never starts the check ("execvpe(/bin/bash) failed"):
  name Git Bash by its full path (`"C:/Program Files/Git/bin/bash.exe" -c "..."`). `break.py`
  refuses such a run rather than record it.
- A check judged by what it prints, not its exit code (`grep -c` exits 1 at 0, a count): pass
  `--expect-stdout <regex>`, its output without the break; break.py refuses if it does not match.
- On a CRLF tree, a `git diff --exit-code` check prints git's CRLF warning, and `break.py`
  reports that warning as the check's line: run `git -c core.safecrlf=false diff ...`.

The dashboard script and the PR checks:

- `scripts/spec-drift.py` reads `main` by name: on a branch it shows `main`. To see a branch's
  effect, clone into a scratch directory, merge the branch with `--no-ff`, and run there. Pass
  `--cache <scratch>/c.json` by hand; `--build` and `--out` do not go together. Its output holds
  an en dash: grep it with `grep -a`.
- In a spec, put in backticks only identifiers, paths and commands; quote literal output. The gap
  reads backticked snake_case and camelCase names as code.
- CI runs `scripts/behaviour-index.py` to check `Replaces:` lines.
- Before pushing: `PR_BRANCH=<branch> PR_TITLE="<title>" python scripts/pr-names.py main HEAD`,
  `git diff --numstat main...HEAD`, and `python scripts/check-links.py` on each Markdown file
  touched. Re-read the PR body's counts and `broken:` lines. Never force-push without the
  maintainer's OK.

## Commits and pull requests

- Commit messages: a subject that says what changed, then why, and what was seen failing.
- PR descriptions follow `.github/pull_request_template.md`: what was built, why this approach
  (including what was broken on purpose and what it reported), contract impact, how to run, and
  the self-check. Spec-change PRs tick the `new`/`hold` line.
- Record mistakes, including your own, in the PR and the day's Notes. The corrections are part of
  what this repository measures.
