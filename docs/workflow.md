# How the work runs

The rules are in [`CLAUDE.md`](../CLAUDE.md) and [`specs/README.md`](../specs/README.md). This page
draws them: who does what, what triggers what, and how to start.

Three layers have to agree. [`plan.md`](../plan.md) is the goal, and changes rarely. The day specs
in [`specs/`](../specs/) are the steps. The code is where the steps land.

## Who does what

```mermaid
flowchart LR
    M["Maintainer<br/>decides and merges"]
    S["Main session (Opus 5.5)<br/>specs, briefs, review,<br/>commits and PRs"]
    PA["plan-auditor<br/>big picture:<br/>plan ↔ specs ↔ code"]
    SA["spec-auditor<br/>one day:<br/>spec ↔ code"]
    I["implementer (Haiku)<br/>code and tests<br/>from a brief"]
    CI["CI on GitHub<br/>tests, checks,<br/>400-line gate"]
    D["Dashboard<br/>scripts/spec-drift.py"]

    M -- "'start Day N' / 'merged'" --> S
    S -- "calls" --> PA
    S -- "calls" --> SA
    PA -- "findings, read-only" --> S
    SA -- "findings, read-only" --> S
    S -- "brief" --> I
    I -- "diff, never commits" --> S
    S -- "PR" --> CI
    CI -- "green or red" --> M
    S -- "after each merge" --> D
    D -- "where we are" --> M
```

The auditors ([`.claude/agents/`](../.claude/agents/)) only read and report. They never edit
files or open PRs, which is what keeps them independent from the session that writes the code.
The implementer writes each track's code from a brief and never commits; the main session reviews
its diff, runs the checks, breaks the code on purpose, and opens the PR.

Before every push, the main session makes sure each test can fail; some things stop the work
instead of being patched:

```mermaid
flowchart TD
    T{"A test passes before<br/>and after the change.<br/>Has it ever been seen to fail?"}
    T -- no --> B["1. break the code on purpose<br/>2. run it, note what it said<br/>3. revert; never merge it"]
    T -- yes --> C["run the checks CI runs;<br/>read the reports, not exit codes"]
    B --> C
    C --> P["commit, then the PR"]
    P --> W{{"stop: wait for 'merged'"}}
    X["Stop and ask, do not patch:<br/>a frozen test needs an edit,<br/>anything applied needs an edit,<br/>a criterion cannot be met as written"] --> R(["record the departure;<br/>never deviate silently"])
```

## What triggers what

```mermaid
flowchart TD
    Start(["Maintainer: 'start Day N'"]) --> Phase{"First day<br/>of a phase?"}
    Phase -- yes --> PA1["plan-auditor<br/>does this phase still add up<br/>to the plan's outcome?"]
    Phase -- no --> SA
    PA1 --> SA["spec-auditor<br/>run every check on today's code,<br/>try to break each 'hold'"]
    SA --> Fix["Main session: spec-change PR<br/>(plan-change PR if the plan is wrong)"]
    Fix --> W1{{"wait: 'merged'"}}
    W1 --> Track["Main session: one track PR<br/>from fresh main"]
    Track --> W2{{"wait: 'merged'"}}
    W2 --> Dash["refresh dashboard"]
    Dash --> More{"More tracks?"}
    More -- yes --> Track
    More -- no --> Close["closing PR: tick criteria,<br/>Notes, lab notebook"]
    Close --> W3{{"wait: 'merged'"}}
    W3 --> Dash2["refresh dashboard"]
    Dash2 --> Last{"Last day<br/>of the phase?"}
    Last -- yes --> PA2["plan-auditor<br/>phase-end review<br/>(after the last phase:<br/>the three end-of-phase<br/>auditors too)"] --> Stop(["Stop. Report.<br/>Wait for the maintainer."])
    Last -- no --> Stop
```

| Trigger | Runs | Produces |
|---|---|---|
| "start Day N", first day of a phase | plan-auditor, then spec-auditor | findings, then a spec-change or plan-change PR |
| "start Day N", any other day | spec-auditor | findings, then a spec-change PR |
| "merged" | dashboard refresh, then the next track or the closing PR | a PR, then wait |
| "merged" on a phase's last closing PR | dashboard refresh, then the plan-auditor, phase-end review, on fresh `main` | a report; nothing starts until the maintainer says so |
| "merged" on the closing PR of <!-- PROJECT: the last phase, and what ends it --> | dashboard refresh, then the three end-of-phase auditors, independently, on fresh `main` | three reports for the maintainer; nothing is destroyed until they say so |
| any time, by hand | either auditor | for example "use the plan-auditor on Phase 3" |

## Starting, for someone new

1. Clone the repository and open Claude Code in it. `CLAUDE.md` and the two agents load on
   their own; there is nothing to install for them.
2. Read `plan.md`, then `specs/README.md`.
3. Run `python scripts/spec-drift.py --out spec-drift.json`, or open the dashboard (https://yusuprozimemet.github.io/LearnX-Delftse/dashboard/), for the
   current day and the next step.
4. Say "start Day N". The session follows the order above and stops at every PR.

Three things to know: nothing merges without the maintainer; nothing starts after a phase ends
until they say so; an auditor's findings are advice, and the maintainer decides what becomes a PR.
