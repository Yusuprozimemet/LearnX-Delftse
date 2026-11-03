"""Run each test a track's `new` criteria name, on the code before the track and on the track, and
print one line per test.

    python scripts/track-red.py --branch <day-NN/track-x-slug|change/slug> [--base main] [--runner unittest]

A `new` criterion must fail on the code before the day (specs/README.md); a pilot of the kit merged
two tracks without the `seen red:` record. CI runs this on every PR ("Track tests seen red") with `--runner` set to the
repository variable TRACK_RED_RUNNER: empty, the variable unset, is "not configured" and checks
nothing; `unittest`, `pytest`, `maven`, and `npm` select a runner; anything else cannot run. It runs the tests again;
it cannot show the track's author did. What it checks comes from the spec, never the diff (a PR that adds
tests pinning what holds is not a track):

- A day track reads `specs/day-NN-*.md` from the base. A `change/<slug>` reads
  `specs/changes/<slug>.md` from the checked-out head, and owns all its criteria.
  Other branches pass without a check.
- A one-row Tracks table owns every criterion; otherwise a track owns what its Work cell names
  (`C2.1`, `C2.1–C2.3`), and a `new` criterion no row names fails.
- Only a `new` criterion's `Test:` field is read: the backticked names right after it. All `hold`
  passes; a `new` one with no `Test:`, or a name that matches no test here, fails.
- The spec must be readable, or a `new` criterion passes unseen: no Acceptance criteria section,
  none in it, an item at the margin that is not `- [ ] CN.N **new**` or `**hold**`, or a repeated
  id fails, whichever track the branch is. A short spec's IDs must match its slug.
- Each test runs alone on the base (seen-red.py's `on_base`) and here. There it must report
  "FAILED (failures=N)", not `errors=` (an import, not the assertion); here, "OK".

Exit 0 when every test does both, or nothing applies; 1 when any check fails; 2 when it cannot run.
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("seen_red", os.path.join(HERE, "seen-red.py"))
seen_red = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seen_red)
git = seen_red.git

TRACK_BRANCH = re.compile(r"^day-(\d{2})/track-([0-9a-z]+)(?=-|$)")  # pr-names.py's BRANCH_TRACK
CHANGE_BRANCH = re.compile(r"^change/([a-z0-9]+(?:-[a-z0-9]+)*)$")
CRITERION = re.compile(r"^- \[[ xX]\] (C\d+\.\d+|S-[a-z0-9]+(?:-[a-z0-9]+)*\.\d+) \*\*(new|hold)\*\*", re.M)
LIST_ITEM = re.compile(r"^[-*+] .*$", re.M)  # an item at the margin; a criterion's wrap is indented
TEST_FIELD = re.compile(r"Test:\s*((?:`[^`]+`(?:\s*,?\s*(?:and\s+)?(?=`))?)+)")
NAMED = re.compile(r"C(\d+)\.(\d+)(?:\s*[–-]\s*C?\1\.(\d+))?")
ROW = re.compile(r"^\|\s*([0-9A-Za-z]+)\s*\|(.*)\|\s*$", re.M)
RAN = re.compile(r"^Ran (\d+) tests?\b", re.M)
SUMMARY = re.compile(r"^(OK.*|FAILED \((.*)\))$", re.M)
LIST_TESTS = ("import unittest\nwalk = lambda s: [i for t in s for i in (walk(t) if isinstance(t, unittest.TestSuite) "
              "else [t.id()])]\nprint('\\n'.join(walk(unittest.defaultTestLoader.discover('tests', top_level_dir='.'))))")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
RUNNERS = {
    "unittest": lambda name: [sys.executable, "-m", "unittest", name],
    "pytest": lambda name: [sys.executable, "-m", "pytest", name],
    "maven": lambda name: ["mvn", "clean", "test", f"-Dtest={name}"],
    "npm": lambda name: ["npm", "test", "--", name],
}


def runner_command(runner, name):
    return RUNNERS[runner](name)


def runner_result(runner, run):
    if runner == "unittest":
        return result(run)
    output = run.stdout + run.stderr
    if runner == "maven":
        matches = re.findall(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)[^\n]*", output)
        if not matches:
            return 0, "no summary line", 0, 0
        total, failures, errors, skipped = map(int, matches[-1])
        return total - skipped, matches[-1], failures, errors
    if runner == "pytest":
        summaries = re.findall(r"^=+\s*(.*?)\s*=+\s*$", output, re.M)
        line = summaries[-1] if summaries else "no summary line"
        failures = re.search(r"\b(\d+) failed\b", line)
        errors = re.search(r"\b(\d+) errors?\b", line)
        passed = re.search(r"\b(\d+) passed\b", line)
        return sum(int(m.group(1)) for m in (failures, errors, passed) if m), line, int(failures.group(1)) if failures else 0, int(errors.group(1)) if errors else 0
    lines = re.findall(r"^\s*Tests(?::|\s+)\s*(.*)$", output, re.M)
    line = lines[-1] if lines else "no summary line"
    failures = re.search(r"\b(\d+) failed\b", line)
    passed = re.search(r"\b(\d+) passed\b", line)
    return sum(int(m.group(1)) for m in (failures, passed) if m), line, int(failures.group(1)) if failures else 0, 0


def section(text, title):
    m = re.search(rf"^## {title}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def criteria(spec):
    """{id: (tag, the names in its Test: field)}, in the spec's order."""
    body = section(spec, "Acceptance criteria")
    starts = list(CRITERION.finditer(body))
    found = {}
    for m, nxt in zip(starts, starts[1:] + [None]):
        block = body[m.start():nxt.start() if nxt else len(body)]
        block = re.split(r"^- ", block[2:], maxsplit=1, flags=re.M)[0]  # up to any other list item
        field = TEST_FIELD.search(block)
        found[m.group(1)] = (m.group(2), re.findall(r"`([^`]+)`", field.group(1)) if field else [])
    return found


def unread(spec):
    """What in the Acceptance criteria the check cannot read. Each would pass unseen: a `new` line
    the pattern misses is no criterion, and a repeated id keeps only its last tag."""
    if not re.search(r"^## Acceptance criteria\s*$", spec, re.M):
        return ["the spec has no `## Acceptance criteria` section"]
    body = section(spec, "Acceptance criteria")
    problems = [f'cannot read "{line[:40].rstrip()}" as `- [ ] CN.N **new**` or `**hold**`'
                for line in LIST_ITEM.findall(body) if not CRITERION.match(line)]
    ids = [m.group(1) for m in CRITERION.finditer(body)]
    if not ids and not problems:
        problems.append("the Acceptance criteria section lists no criterion")
    return problems + [f"{c} is listed {ids.count(c)} times" for c in dict.fromkeys(ids) if ids.count(c) > 1]


def tracks(spec):
    """{track, lower case: the ids its Work cell names}."""
    rows = {}
    for m in ROW.finditer(section(spec, "Tracks")):
        if m.group(1).lower() == "track":
            continue
        ids = []
        for n in NAMED.finditer(m.group(2)):
            last = int(n.group(3) or n.group(2))
            ids += [f"C{n.group(1)}.{i}" for i in range(int(n.group(2)), last + 1)]
        rows[m.group(1).lower()] = ids
    return rows


def owned(spec, track):
    """The criteria the track owns, {id: (tag, names)}, and the problems in how the spec assigns them."""
    rows, all_ = tracks(spec), criteria(spec)
    if track not in rows:
        return {}, [f"track {track.upper()} is not a row of the spec's Tracks table"]
    if len(rows) == 1:
        return all_, []
    named = {i for ids in rows.values() for i in ids}
    problems = [f"{c} is new and no row of the Tracks table names it" for c, (tag, _) in all_.items()
                if tag == "new" and c not in named]
    return {c: v for c, v in all_.items() if c in rows[track]}, problems


def test_ids():
    return subprocess.run([sys.executable, "-c", LIST_TESTS], capture_output=True, text=True, encoding="utf-8",
                          env=ENV).stdout.split()


def result(run):
    """(tests run, the summary line, failures, errors) of one unittest run."""
    output = run.stdout + run.stderr
    ran, summary = RAN.findall(output), SUMMARY.findall(output)
    line, counts = summary[-1] if summary else ("no summary line", "")
    count = lambda kind: int((re.search(rf"\b{kind}=(\d+)", counts) or [0, 0])[1])
    return int(ran[-1]) if ran else 0, line, count("failures"), count("errors")


def main(args=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--branch", required=True)
    p.add_argument("--base", default="main")
    p.add_argument("--runner", default="unittest")
    a = p.parse_args(args)
    sys.stdout.reconfigure(encoding="utf-8")  # the arrow; a Windows console's default is cp1252
    if not a.runner:
        print("not configured: TRACK_RED_RUNNER is not set, so no track's tests are run; "
              "a Python project with unittest tests sets it to unittest")
        return 0
    if a.runner not in RUNNERS:
        print(f"track-red.py: TRACK_RED_RUNNER `{a.runner}` is not supported; supported runners: {', '.join(RUNNERS)}",
              file=sys.stderr)
        return 2
    os.chdir(git("rev-parse", "--show-toplevel").strip())

    m, change = TRACK_BRANCH.match(a.branch), CHANGE_BRANCH.fullmatch(a.branch)
    if a.branch.startswith("change/") and not change:
        print("FAIL: change branch slug must contain lowercase letters, numbers and single hyphens")
        return 1
    if not m and not change:
        print(f"not applicable: `{a.branch}` is not a track branch (day-NN/track-<x>-<slug>)")
        return 0
    day, track = m.groups() if m else (None, None)
    try:
        base = git("rev-parse", "--short", f"{a.base}^{{commit}}").strip()
        paths = ([f for f in git("ls-tree", "--name-only", base, "specs/").split()
                  if re.fullmatch(rf"specs/day-{day}-.*\.md", f)] if m else [])
    except subprocess.CalledProcessError as e:
        print(f"track-red.py: cannot read {a.base}: {e.stderr.strip()}", file=sys.stderr)
        return 2
    if change:
        slug = change.group(1)
        path = f"specs/changes/{slug}.md"
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            print(f"FAIL: short spec {path} is missing on the head")
            return 1
        ours, problems = criteria(text), unread(text)
        problems += [f"{c} does not belong to short spec {slug}" for c in ours
                     if not re.fullmatch(rf"S-{re.escape(slug)}\.\d+", c)]
        label = f"Short spec {slug}"
    elif len(paths) != 1:
        print(f"FAIL: {len(paths)} specs match specs/day-{day}-*.md on {base}; the check needs one")
        return 1
    else:
        text = git("show", f"{base}:{paths[0]}")
        ours, problems = owned(text, track)
        problems = unread(text) + problems
        label = f"Day {day} track {track.upper()}"
    new = {c: names for c, (tag, names) in ours.items() if tag == "new"}
    if change and not new:
        problems.append(f"short spec {slug} has no new criterion; a behaviour change needs one")
    problems += [f"{c} is new and has no Test: field naming a test in backticks"
                 for c, names in new.items() if not names]
    if not new and not problems:
        subject = f"track {track.upper()} of Day {day}" if m else f"short spec {slug}"
        print(f"not applicable: {subject} owns no new criterion "
              f"(hold: {', '.join(ours) or 'none'})")
        return 0

    ids, runs = test_ids() if a.runner == "unittest" else [], []
    for c, names in new.items():
        for name in names:
            hits = [i for i in ids if i == name or i.endswith("." + name)] if a.runner == "unittest" else [name]
            if len(hits) != 1:
                problems.append(f"{c}: `{name}` matches {len(hits)} tests on the head, not one")
            else:
                runs.append((c, hits[0]))
    commands = [runner_command(a.runner, t) for _, t in runs]
    if os.name == "nt" and a.runner == "npm":
        commands = [["npm.cmd", *command[1:]] for command in commands]
    on_base = seen_red.on_base(base, commands, seen_red.changed_tests(base)) if runs else []
    red = 0
    for (c, t), before, command in zip(runs, on_base, commands):
        here = subprocess.run(seen_red.break_.argv_for(command, os.getcwd()), capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
        ran, line, failures, errors = runner_result(a.runner, before)
        ran_here, line_here, failures_here, errors_here = runner_result(a.runner, here)
        if ran < 1:
            problems.append(f"{c}: {t} ran no test on {base}: {line}")
        elif before.returncode == 0 or errors or not failures:
            why = "it does not see the change" if line.startswith("OK") else "an error, not an assertion"
            problems.append(f"{c}: {t} reports {line} on {base}: {why}")
        elif ran_here < 1 or here.returncode != 0 or failures_here or errors_here:
            problems.append(f"{c}: {t} does not pass on the head: {line_here}")
        else:
            red += 1
            print(f"seen red: {t} ({c}) on {base} → {line}; on the head → OK")
    for problem in problems:
        print(f"FAIL: {problem}")
    print(f"{label}: {red} of {sum(map(len, new.values()))} tests the new criteria "
          f"name fail on {base} by assertion and pass on the head.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
