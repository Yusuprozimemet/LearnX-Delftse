# Agentic workflow kit

> Copied from the kit at `f3b3a10` as its README. Delftse's own README is
> [`../README.md`](../README.md); `kit/` stayed in the kit, so its links go there.

A spec-first way to change a codebase with coding agents, one day spec at a time, and a dashboard
that measures it. It holds the agent briefs (`.claude/agents/`), the spec rules
(`specs/README.md`), the pull request template and CI (`.github/`), the dashboard
(`scripts/spec-drift.py`, `docs/dashboard/`), and the tools and their tests (`scripts/`).
[CLAUDE.md](../CLAUDE.md) gives the working order, [plan.md](../plan.md) the goal,
[docs/workflow.md](workflow.md) draws it, and [docs/architecture.md](architecture.md)
draws the parts underneath: agents, scripts, CI, a day's PRs, a criterion's life, the close and
the dashboard.

## What it does

The kit is a working order and the checks that hold an agent to it, not a generator. A spec
says what a day must make true; the kit's question is whether each claim of "done" can be shown.

- **A plan and day specs.** `plan.md` holds the goal in phases; each day is
  `specs/day-NN-<slug>.md`, with criteria that keep their ID (`C12.3`) and wording outside a
  spec-change PR (CI: "Criteria keep their wording").
- **Every criterion is `new` or `hold`, and every check has been seen to fail.** A `new` check
  fails on the code before the day (`scripts/seen-red.py`, `scripts/track-red.py`); a `hold`
  check is broken on purpose once (`scripts/break.py`), and the PR records what it reported.
- **Evidence replayed after the close.** The closing PR ticks each criterion with its test and
  PR and commits a break manifest; after the merge, `evidence.yml` re-runs the Verify commands and
  the recorded breaks at their commits (`scripts/evidence.py`). The next session checks the run
  before starting the next day and stops for a failed, stale or missing replay; CI cannot block
  the merge it replays.
- **Separate agents with fresh contexts.** A plan-auditor and a spec-auditor read the specs
  against the code before any work, an implementer on a smaller model writes the code, a
  defect-rater relabels the day's defects blind (`scripts/relabel.py`), and three end-of-phase
  auditors judge the whole. Auditors only read: a hook blocks their writing git and `gh`
  commands (`scripts/auditor-guard.py`), but reads commands rather than what programs do and is
  not a sandbox. The main session reviews, breaks, commits and writes the
  PR; the maintainer merges.
- **Small PRs in order.** One PR per track, from fresh `main`, under 400 changed lines; names,
  the template and frozen files checked in CI (`.github/workflows/pr-checks.yml`).
- **A measured record.** Every defect is one `Defect` line (`found:`, `cause:`, `made:`); every
  deferral a `Hand-off` ID a later day must cite. The dashboard (`scripts/spec-drift.py`) draws
  spec and code drift, breaks, evidence, hand-offs no day picked up, and tokens per day
  (`scripts/token-usage.py`).

It comes from a 43-day split of a monolith into services (its record is under `kit/`): 475 defect
entries, 455 of them in the agent's own specs, drafts and tooling. It was then piloted for five
days on a small Python CLI. What that evidence does and does not show is in
[kit/ASSESSMENT.md](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/ASSESSMENT.md): a case study, not a comparison with a simpler workflow.

## Starting a project from it

1. Copy the kit without its history, from the kit's folder:
   `mkdir ../<project> && git archive HEAD | tar -x -C ../<project>` (copying the folder carries
   the kit's `.git`). There, delete `kit/` (`rm -r kit`) and start the history on `main`:
   `git init -b main`. The dashboard reads the branch `main` by name and stops without it.
2. Fill every `<!-- PROJECT: … -->`: `git grep -n "PROJECT:"` lists them (`CLAUDE.md`, the
   agents, `specs/README.md`, the PR template, `docs/workflow.md`, `scripts/spec-drift.py`). Fill
   `plan.md`'s `<name>` and `<NN>`, and `spec-drift.config.json`: every field is empty until the
   project has what it names (services, moved paths, an infra workflow, PR areas, frozen files).
   `test_count` is absent: added as `{"files": "<path regex>", "test": "<line regex>"}`, "Frozen
   files stay unedited" also fails a PR whose count of matching lines falls (`scripts/invariants.py`).
3. Add a workflow that runs the project's own tests: the kit's CI checks the workflow, not the
   code. Require it with the others below.
4. Write each day's spec from [specs/_template.md](../specs/_template.md), as
   `specs/day-NN-<slug>.md`. At a stop (**Stop and evaluate.** in `plan.md`'s list) the next
   step waits until `plan.md` has a `## Course correction after Phase N` section, N the stop
   item's phase.
5. Optional: a tracker key, as a repository variable `JIRA_KEY` (Settings, Secrets and
   variables, Actions, Variables). "Compare the names" in `.github/workflows/pr-checks.yml`
   reads it; unset, no key is checked.
6. Optional: set the repository variable `TRACK_RED_RUNNER` to `unittest`, `pytest`, `maven`, or `npm`. "Track tests seen red" runs each named test on the base and head. Unset, it reports "not configured" and passes; an unknown value fails. Use unittest test names, pytest node IDs, Maven class names, or npm test file paths, respectively. The replay copies changed files under `tests/` only; test files elsewhere must already exist on the base. Runner dependencies must be available in CI. Evidence replay still resolves Python classes under `tests/`.

### Style check setup

Set `STYLE_CHECK_COMMAND` as a repository variable to a check-only command, and fill the matching placeholder in `CLAUDE.md`. For example:

- Python: configure `[tool.ruff]` in `pyproject.toml`, then use `ruff check . && ruff format --check .`.
- JavaScript/TypeScript: `npx eslint . && npx prettier --check .`.
- Java: `mvn spotless:check`, or your Maven lint plugin's `check` goal with its rules file committed.

Put the first formatting of existing code in its own PR with an `Oversized:` line, never in a day's track.

Control activation in a copied kit (CI checks must also be required in branch protection below):

- **Track tests seen red:** active only once the repository variable `TRACK_RED_RUNNER`
  is configured; applies to track PRs with owned `new` criteria and named tests under `tests/`.
- **Frozen files:** active only once `spec-drift.config.json` has `frozen` patterns (empty by
  default); optional `test_count` configures the additional count check.
- **Evidence replay:** active by default after the closing merge, in `evidence.yml`, using
  `scripts/evidence.py` with the break manifest and `--json` report. The next session checks its
  post-merge result before advancing; see [closing instructions](../specs/README.md#evidence-at-the-close).
- **Auditor guard hook:** active by default in Claude Code sessions that load
  `.claude/settings.json`; guards the configured auditors' commands and stop state. It is a
  command guard, not a sandbox (`CLAUDE.md`, "The auditors").
- **Diff limit:** active by default in PR CI; `MAX_CHANGED_LINES=400` in
  `.github/workflows/pr-checks.yml`; an `Oversized:` line in the PR body turns failure into a
  warning, without checking approval.
- **Criteria keep their wording:** active by default in PR CI; criterion wording, tags and
  IDs must match the merge base except on `day-NN/spec-<slug>` spec-change branches.
- **Project behavior tests:** active only once the project adds its own test workflow and
  requires that check in branch protection.

A green "not configured" check is not proof that project behavior was checked.
The post-merge evidence gate, the auditor guard's limits, the `Oversized:` override and the wait for "merged" are procedural and hold only while the maintainer and sessions follow `CLAUDE.md`.

On GitHub, in this order:

- Before the first push, Pages with source "GitHub Actions": every merge to `main` republishes
  the dashboard, and without Pages the first push's Dashboard run fails at "Publish to Pages"
  (404).
- A public repository, or a paid plan for Pages and branch protection on a private one.
- Before Day 1, branch protection on `main`: a pull request before merging, and these checks
  required by name: "Description uses the template", "Diff stays reviewable", "Branch, title,
  commits and Jira keys agree", "Frozen files stay unedited", "Dashboard metric tests gate",
  "Criteria keep their wording", and the project's tests. The settings page offers only checks
  that have already run; the API takes any name. Write the rule to a file (a bash heredoc fails
  in PowerShell):

  ```json
  {"required_status_checks": {"strict": false, "contexts": ["Description uses the template",
    "Diff stays reviewable", "Branch, title, commits and Jira keys agree",
    "Frozen files stay unedited", "Dashboard metric tests gate", "Criteria keep their wording",
    "<the project's tests>"]},
   "enforce_admins": false, "required_pull_request_reviews": null, "restrictions": null}
  ```

  and run `gh api -X PUT repos/<owner>/<repo>/branches/main/protection --input <file>`. Then tick
  "Require a pull request before merging" on the settings page: the API ties it to a review rule.
- "Every service is wired into compose and Prometheus" checks nothing until `services/` exists,
  and passes. A project with no services leaves it out of the required checks, or deletes the job.
- The kit has no workflow for the project's own tests: each project adds one (for example
  `.github/workflows/tests.yml`) before Day 1 and requires its check by name, as above. The
  kit's workflows test only its own tools.
- "Track tests seen red" reports on every pull request; with `TRACK_RED_RUNNER` set (item 6
  above), require it by name too.

Locally: Python with `numpy` (and `pyyaml` for `scripts/test_service_wiring.py`), `node` for the
page tests, and `gh`, logged in.

## A session's prompt

One session per step (`CLAUDE.md`). Replies are what a session costs: in the kit's first copy a
step ran about 80 replies at about 75k context each, 97% of it read from cache, so the number of
replies, not their size, is the lever. The prompt carries only the step, the baseline and the
stop. Lessons go in `CLAUDE.md`'s Pitfalls, where every session reads them; notes only a run of
sessions needs (a checklist, open items) go in a memory file the prompt names by path. In that
copy the prompt went from about 130 lines to 35, to the same effect.

```
Day NN track A: <what>, on `day-NN/track-a-<slug>` from fresh main. Under 400 lines.
Notes: <memory file> (read in parts with grep, not whole).
Baseline (check all in ONE shell command, print one line per item; if anything differs, stop):
- main at <sha>, clean tree
- <the test command>: <N> OK
Token rules: batch independent calls in one reply; pre-push checks as one command; test and CI
output filtered to counts and failures; `gh pr checks <n> --watch > <scratch>/checks.txt`, then
print only the final `gh pr checks <n>`. Report each check, then stop. Don't merge: I merge.
When I say "merged" (I'll /compact first): pull main, run the tests, report the counts and the
next step, give the next session's prompt in this shape, and stop.
```

## `kit/`: the record of the copy

The kit was copied from a finished project and cleaned into this starter. `kit/` keeps that record,
for this repository only:

- [kit/SOURCE.md](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/SOURCE.md): where every file came from, at which commit, with its hash, and
  how it has changed since.
- [kit/CHANGES.md](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/CHANGES.md): the changes proposed and made, each with its decision.
- [kit/v2-manifest.md](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/v2-manifest.md): each file's treatment when the pilot's tooling came
  back into the kit.
- [kit/ASSESSMENT.md](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/ASSESSMENT.md): what the kit's evidence establishes, what it does not,
  and the next steps; the whole-workflow comparison is deferred for token cost (CHANGES item 91).
- [kit/replay/](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/replay/README.md): a frozen replay set of 16 cases for the spec-auditor,
  development and held-out, with a checker and scorer; a reduced baseline of 7 is in
  [results](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/replay/results.md).
- The source project's `CLAUDE.md`, `plan.md`, lab notebook and other data, in a folder of
  `kit/` named after it, and two of its day specs in [kit/examples/](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/examples/), kept as
  references. They are not instructions for this repository. [kit/lessons/](https://github.com/Yusuprozimemet/agentic-workflow-kit/blob/f3b3a10/kit/lessons/): the
  lessons its maintainer kept.
- `kit/compare-page.py`: the source project's data drawn by this page, box by box, against a saved run.
- `kit/starter-check.py`: what a copy starts with. It builds HEAD without `kit/` in a fresh
  folder and checks that the dashboard builds and draws with no remote and that no line names the
  source project. CI runs it on every pull request ("The starter names no project").
