# How the kit is built

[`workflow.md`](workflow.md) draws who does what and what triggers what. This page draws the
parts underneath: the files, the agents, the scripts and the CI that hold a day to the rules in
[`CLAUDE.md`](../CLAUDE.md) and [`specs/README.md`](../specs/README.md). Where the two pages
disagree with the rules, the rules win.

## The parts

Three layers have to agree: `plan.md`, the day specs and the code. Agents read them, scripts
check them, CI runs the scripts on every pull request, and the dashboard measures them after each
merge.

```mermaid
flowchart TB
    subgraph Truth["Three layers that must agree"]
        P["plan.md<br/>the goal, in phases"]
        S["specs/day-NN-*.md<br/>the steps, criteria new / hold"]
        C["code on main"]
        P --> S --> C
    end

    subgraph People["People"]
        M["Maintainer<br/>decides, merges"]
        MS["Main session<br/>specs, briefs, review,<br/>breaks, commits, PRs"]
    end

    subgraph Agents[".claude/agents/"]
        PA[plan-auditor]
        SA[spec-auditor]
        IM[implementer]
        DR[defect-rater]
        EOP["three end-of-phase auditors"]
    end

    subgraph Tools["scripts/"]
        BRK[break.py]
        SR["seen-red.py, track-red.py"]
        EV["evidence.py, close-evidence.py"]
        RL[relabel.py]
        GD[auditor-guard.py]
        SD["spec-drift.py, token-usage.py"]
        CHK["pr-names, invariants,<br/>criteria-wording, behaviour-index,<br/>check-links, service-wiring"]
    end

    subgraph GH["GitHub"]
        PRC[pr-checks.yml]
        EVY[evidence.yml]
        DBY[dashboard.yml]
        PAGES[(GitHub Pages)]
    end

    M -- "start Day N / merged" --> MS
    MS --> PA & SA & IM & DR & EOP
    MS --> BRK & SR & RL
    GD -. guards .- PA & SA & EOP
    MS -- PR --> PRC --> CHK
    PRC -- "green or red" --> M
    M -- merge --> EVY --> EV
    M -- merge --> DBY --> SD --> PAGES
    PAGES -- "where we are" --> M
```

What decides and what only records:

```mermaid
flowchart LR
    subgraph Decides
        CODE["code on main<br/>what the system does"]
        SPEC["merged spec<br/>what a day promised"]
        PLAN["plan.md<br/>the goal"]
    end
    subgraph Records["Records: correct these, never the reverse"]
        DASH[dashboard]
        TRK[tracker, if any]
        MEM[memory]
        LAB[docs/lab-notebook.md]
    end
    CODE --> DASH
    SPEC --> DASH & TRK & LAB
    CODE --> MEM
```

Where each part lives:

```mermaid
flowchart LR
    ROOT((repository))
    ROOT --> CL["CLAUDE.md<br/>working order, pitfalls"]
    ROOT --> PL["plan.md<br/>phases, stopping points"]
    ROOT --> SP["specs/<br/>README.md, _template.md,<br/>_short-template.md,<br/>day-NN-*.md, changes/, breaks/"]
    ROOT --> AG[".claude/<br/>agents/ (seven briefs),<br/>settings.json (hooks)"]
    ROOT --> SC["scripts/<br/>the tools and their tests"]
    ROOT --> GHD[".github/<br/>workflows/, PR template"]
    ROOT --> DOC["docs/<br/>workflow.md, architecture.md,<br/>lab-notebook.md, dashboard/"]
    ROOT --> CFG["spec-drift.config.json<br/>services, frozen, areas, test_count"]
```

## Roles and their limits

Only the main session and the implementer write, and only the main session commits. The others
read from a fresh context and report; the maintainer decides what their findings become.

```mermaid
flowchart TB
    subgraph Writes["May write"]
        MS["Main session<br/>specs, follow-up PRs, review,<br/>breaks, commits, PRs"]
        IM["implementer<br/>code and tests only,<br/>never commits or pushes"]
    end
    subgraph ReadOnly["Read-only, fresh context, own brief"]
        PA["plan-auditor<br/>plan, specs and code"]
        SA["spec-auditor<br/>one day's claims and checks"]
        DR["defect-rater<br/>Read tool only, blind text"]
        E3["security-ops, agentic-engineering,<br/>architecture: after the last phase"]
    end
    MX["Maintainer<br/>the only one who merges"]
    ReadOnly -- advice --> MS
    MS -- PRs --> MX
```

The auditors are held to reading by the hooks in [`.claude/settings.json`](../.claude/settings.json),
which run [`scripts/auditor-guard.py`](../scripts/auditor-guard.py). The guard reads the command,
not what a program does: a `python -c` that writes a file gets through.

```mermaid
sequenceDiagram
    participant A as Auditor
    participant H as Hooks
    participant G as auditor-guard.py
    participant Git as repository

    Note over G: acts only for the five auditors with Bash
    A->>H: Bash "git commit ..." or "gh pr create"
    H->>G: pre (PreToolUse)
    G-->>A: exit 2, blocked
    A->>H: Bash "git log", "gh pr view"
    H->>G: pre
    G-->>A: exit 0, runs
    A->>H: finishes (SubagentStop)
    H->>G: stop
    G->>Git: compare HEAD, refs, stash, status
    alt changed
        G-->>A: exit 2, sent back once
    else unchanged
        G-->>H: passes
    end
```

## Phases

```mermaid
flowchart TD
    A([Phase starts]) --> B["plan-auditor on the phase"]
    B --> D1["Day 1 ... Day N"]
    D1 --> E{"Last closing PR<br/>of the phase merged?"}
    E -- no --> D1
    E -- yes --> F["evidence gate, dashboard refresh"]
    F --> G["plan-auditor on the phase that ended<br/>and on the next"]
    G --> H{"Stopping point<br/>in plan.md?"}
    H -- yes --> I["plan-auditor on the whole plan;<br/>'## Course correction after Phase N'<br/>before going on"]
    H -- no --> J
    I --> J{"Last phase?"}
    J -- yes --> K["three end-of-phase auditors,<br/>each from a fresh context"]
    J -- no --> L([Stop until the maintainer says go on])
    K --> L
```

## A day

```mermaid
flowchart TD
    S(["'start Day N'"]) --> Q{"First day<br/>of a phase?"}
    Q -- yes --> PA[plan-auditor]
    Q -- no --> SA
    PA --> SA["spec-auditor"]
    SA --> SC["spec-change PR<br/>day-NN/spec-&lt;slug&gt;"]
    SC --> W1{{"wait: merged"}}
    W1 --> T0["Track 0, if any:<br/>tests that hold before and after"]
    T0 --> TR["track PR from fresh main<br/>day-NN/track-X-&lt;slug&gt;"]
    TR --> W3{{"wait: merged"}}
    W3 --> DS[refresh dashboard]
    DS --> MORE{more tracks?}
    MORE -- yes --> TR
    MORE -- no --> CL["closing PR<br/>day-NN/close-the-day"]
    CL --> W4{{"wait: merged"}}
    W4 --> GATE["evidence.yml run for the merge commit"]
    GATE -- "failed, missing, pending" --> BLOCK([blocked until resolved])
    GATE -- passed --> DS2["spec-drift.py prints the next step"]
    DS2 --> NEW(["next step in a new session"])
```

Branches never stack: each starts from `main` after the previous one merged.

```mermaid
gitGraph
    commit id: "Day 11 closed"
    branch day-12/spec-fix
    commit id: "spec change"
    checkout main
    merge day-12/spec-fix
    branch day-12/track-a-parser
    commit id: "track A"
    checkout main
    merge day-12/track-a-parser
    branch day-12/track-b-output
    commit id: "track B"
    checkout main
    merge day-12/track-b-output
    branch day-12/close-the-day
    commit id: "ticks, Notes, breaks"
    checkout main
    merge day-12/close-the-day
```

A track, between two "merged":

```mermaid
sequenceDiagram
    actor M as Maintainer
    participant S as Main session
    participant I as implementer
    participant T as scripts
    participant CI as CI

    M->>S: merged
    S->>S: pull main, branch the track
    S->>I: brief: files, changes, tests, expected outputs
    I-->>S: report and diff, no commit
    S->>S: review the diff, re-count its numbers
    S->>T: seen-red.py -- test command
    T-->>S: "seen red: ... on main → ..."
    S->>T: break.py --find ... --replace ... -- check
    T->>T: check passes before the break, then run it with the break
    T-->>S: "broken: what → what it reported"
    S->>T: pr-names.py, diff --numstat, check-links.py
    S->>CI: commit, push, open the PR
    CI-->>M: green or red
    S-->>M: the PR, then stop
```

## A criterion

```mermaid
stateDiagram-v2
    [*] --> Drafted: from _template.md
    Drafted --> Audited: spec-auditor runs the check
    Audited --> Drafted: wrong, or cannot fail: spec-change PR
    Audited --> New: fails on today's code
    Audited --> Hold: true today, must stay true
    New --> SeenRed: fails on the base (seen-red.py)
    SeenRed --> Green: the track lands
    Hold --> Broken: broken on purpose once (break.py)
    Broken --> Green: code put back, the break never merges
    Green --> Ticked: closing PR, with evidence
    Ticked --> Replayed: evidence.py at the closing commit
    Replayed --> InForce: behaviour-index.py lists it
    InForce --> Replaced: a later "Replaces:" line
    Replaced --> [*]
```

Outside the days, a small behaviour change takes a short spec:

```mermaid
flowchart TD
    Q{"What kind of change?"}
    Q -- "part of the plan" --> DAY["day spec<br/>specs/day-NN-slug.md"]
    Q -- "small, one PR, no contract test,<br/>no frozen file, no Replaces:" --> SHORT["short spec<br/>specs/changes/slug.md<br/>on change/slug"]
    DAY --> DAYF["spec-change PR, tracks, closing PR<br/>criteria CNN.M"]
    SHORT --> SHF["spec and code in one PR<br/>criteria S-slug.N"]
    DAYF & SHF --> BI[behaviour-index.py]
```

## Before a push

```mermaid
flowchart TD
    A["implementer's diff"] --> B["main session reviews"]
    B --> C{"a new test?"}
    C -- yes --> D["seen-red.py: the branch's tests<br/>on main's code, in a scratch worktree"]
    D --> D1{"failed there?"}
    D1 -- no --> X["the test does not see the change"]
    D1 -- yes --> E
    C -- "no, a hold" --> E["break.py: one exact replacement,<br/>no shell, bytes put back"]
    E --> F["check passes before the break;<br/>copy its broken: line"]
    F --> G["delete old reports, run the checks,<br/>read the reports, not exit codes"]
    G --> H["pr-names.py, under 400 lines,<br/>check-links.py, re-read the counts"]
    H --> I{"a frozen file needs an edit,<br/>or a criterion cannot be met?"}
    I -- yes --> STOP(["stop and ask, record it"])
    I -- no --> J["commit, PR, wait for 'merged'"]
```

## CI

On every pull request, `pr-checks.yml` runs the scripts; `spec-drift-tests.yml` tests the
dashboard script when it changes.

```mermaid
flowchart LR
    PR((pull request)) --> J1["Description uses the template"]
    PR --> J2["Diff stays reviewable<br/>pr-checks.yml"]
    PR --> J3["Frozen files stay unedited<br/>invariants.py"]
    PR --> J4["Every service is wired<br/>service-wiring.py"]
    PR --> J5["Replaces: lines name real criteria<br/>behaviour-index.py"]
    PR --> J6["Branch, title, commits and keys agree<br/>pr-names.py"]
    PR --> J7["Track tests seen red<br/>track-red.py"]
    PR --> J8["Criteria keep their wording<br/>criteria-wording.py"]
    J1 & J2 & J3 & J4 & J5 & J6 & J7 & J8 --> RESULTS["CI results"]
    RESULTS --> BP{"checks required by<br/>branch protection"}
    BP -- green --> MERGE["the maintainer may merge"]
```

Branch protection must be configured separately; not every CI job shown is required by the
starter configuration. `Track tests seen red` also needs `TRACK_RED_RUNNER=unittest` to run
its checks. The project must add and require its own test workflow; see the
[setup instructions](kit.md#starting-a-project-from-it).

## The close

```mermaid
sequenceDiagram
    participant S as Main session
    participant R as relabel.py
    participant DR as defect-rater
    participant PR as Closing PR

    S->>S: tick criteria, write Notes, lab notebook
    S->>S: token-usage.py, commit token-usage.json
    S->>R: blind specs/day-NN-*.md --out dir
    R-->>S: blind.md, tags stripped
    S->>DR: blind.md
    DR-->>S: labels.json
    S->>R: compare dir
    R-->>S: each disagreement
    S->>PR: a decision on each disagreement
    S->>PR: specs/breaks/day-NN.json
```

After a closing PR merges, including by squash merge, CI replays its evidence. The next session
checks the run for that closing commit before advancing; this gate is procedural, not an automatic
block on starting work:

```mermaid
flowchart TD
    MG([closing PR merged]) --> EVY["evidence.yml<br/>close-evidence.py"]
    MG --> DBY["dashboard.yml"]
    EVY --> E1["the named tests exist under tests/"]
    EVY --> E2["the Verify lines run and print<br/>what their comments say"]
    EVY --> E3["each recorded break, replayed,<br/>makes its check fail"]
    E1 & E2 & E3 --> RES{result}
    RES -- "missing, stale, failed<br/>or an execution error" --> B[blocked]
    RES -- passed --> OK["next step may start"]
    DBY --> SD["spec-drift.py --build"] --> PG[(GitHub Pages)]
```

## The dashboard

[`scripts/spec-drift.py`](../scripts/spec-drift.py) reads git, GitHub and the specs, and builds
the page; the page runs nothing.

```mermaid
flowchart LR
    subgraph Inputs
        GL["main's merge and squash commits<br/>and PR metadata"]
        GHAPI["PRs and their<br/>broken: and seen red: lines"]
        SPECS["specs: criteria,<br/>Defect and Hand-off lines"]
        CFG[spec-drift.config.json]
        TOK[token-usage.json]
        EVJ[evidence-checks.json]
    end
    SDP[spec-drift.py]
    GL & GHAPI & SPECS & CFG & TOK & EVJ --> SDP
    SDP --> PAGE["docs/dashboard/"]
    subgraph Panels
        P1["Where we are"]
        P2["Spec-code name gap,<br/>merge by merge"]
        P3["Where the work went,<br/>pull requests per day"]
        P4["Where the corrections landed,<br/>second-reader agreement"]
        P5["Tokens spent,<br/>who spent them"]
        P6["Evidence behind the ticks,<br/>evidence replayed"]
        P7["Hand-offs to days not yet run"]
    end
    PAGE --> P1 & P2 & P3 & P4 & P5 & P6 & P7
```

## One session per step

Every reply re-reads the whole conversation, so a step starts in a new session; the files carry
what it needs.

```mermaid
flowchart LR
    A["step merged,<br/>gate passed"] --> B["spec-drift.py<br/>prints the next step"]
    B --> N["new session reads CLAUDE.md,<br/>the spec, spec-drift.json, memory"]
    B -. "or keep it" .-> K["/compact"]
    N & K --> D[next step]
```
