# <Project> plan

<!-- plan.md says what and why; the day specs in specs/ say how. It changes rarely, and only in a
plan-change PR. Keep it short. -->

## Verdict: <one line on what is easy and what is hard>

<Two or three sentences: where the project starts, where it ends, and what makes it hard.>

| Hard part | Why | Fix |
|---|---|---|
| **<risk>** | <what in the code today makes it hard> | <the phase that deals with it> |

## Decisions made before the work

- **<decision>:** <what was chosen, and why>. <!-- e.g. a target platform, decided before Phase N -->

## Target structure

```
<the repository or system layout the project ends at>
```

## Phases

Each phase is a series of <400-line PRs, so CI stays green.

### Phase 0 — <name>
**Goal:** <one sentence>.
- <what the phase delivers>
- **Done when:** <a check someone who did not do the work can run>.

### Phase 1 — <name>
**Goal:** <one sentence>.
- <what the phase delivers>
- **Done when:** <check>.

<!-- add phases as needed -->

## Stopping points

- **Day <NN> (<what it finishes>) is a stopping point.** The plan-auditor reads the whole plan
  against the code before anything goes on.

<!-- Mark each stopping point in the list under "Day-by-day specs" too: its item ends with
**Stop and evaluate.**, and its last day is the stop. That mark is what the dashboard reads: the
roadmap marks the day, and the next step waits there until a "## Course correction after Phase N"
section, N the item's phase, lifts it. -->

## Constraints

- <a rule every day keeps, e.g. the contract tests are never edited>

## What we are not doing

- **No <thing>.** <why>

## Course corrections

<!-- Added by plan-change PRs, newest first: what a stopping point or audit found, and what the
maintainer chose. Empty at the start. A stop's decisions go under their own heading,
"## Course correction after Phase N", N the stop item's phase: that heading lifts the stop. An
entry left here lifts it only if it names "Phase N"; one that names only the day does not. -->

## Day-by-day specs

The phases are broken into day specs in [`specs/`](specs/). Read
[`specs/README.md`](specs/README.md) for the workflow.

<!-- One numbered item for each run of days, in the order they run. A number after "Phase" is the
phase; every other number is a day, and "Days 4-6" is a range. End an item with
**Stop and evaluate.** to make its last day a stopping point. An item marked **cut** takes its
days out of the order. Days whose specs are not written yet: "numbered from N" is a run that
starts at Day N; "numbered after the last" is one day after every day the list names ("close-out"
in the item names it so). Until its spec exists, the next step quotes the item. -->

1. Phase 0: Days <NN>, <NN>. **Stop and evaluate.**
2. Phase 1: Days <NN>-<NN>.
