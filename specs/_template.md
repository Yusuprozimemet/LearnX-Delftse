# Day NN — <title>

**Phase:** <n> · **Depends on:** Day <n-1> · **Expected PRs:** <n>

## Goal
<One sentence. What is true at the end of the day that was not true at the start.>

## In scope
- <thing>

## Out of scope
- <thing> — <which day owns it>

## Tracks

| Track | Owner | Work |
|---|---|---|
| A | | CNN.1 |
| B | | CNN.2 |

## Acceptance criteria
- [ ] CNN.1 **new** — <checkable by someone who did not write the code>. Red today: <how it
      fails on the code as it is, e.g. "the grep finds two">.
      Test: `ExampleTest.test_new_behavior`.
      Replaces: <earlier ID, only if this changes behaviour it holds> — was: <old behaviour>
      Evidence: <test> (#n), seen red first on `<sha>`'s code.
- [ ] CNN.2 **hold** — <true before the change and must stay true after it>.
      broken: <what was broken> → <what the check reported>

## Verify
```bash
<command anyone can run; a diff names a fixed SHA, never main>
```

## Notes
- <risks, gotchas, decisions deferred>
- **Defect** — found: <auditor | spec-change PR | review | break on purpose | self | Track X |
  close | dashboard | Day NN review> · cause: <spec | implementation | process | environment> ·
  made: <Day NN | none> · <one problem: what was wrong, and the PR that fixed it>.
- **Rewrite** — Track <X>: <what the main session rewrote in the implementer's code beyond
  review fixes, and why>.
- **Hand-off** HNN.1 → Day <m> | the maintainer: <what this day leaves undone, and why>.
