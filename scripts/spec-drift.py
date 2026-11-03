"""Measure how the spec and the code move against each other, merge by merge.

Writes one JSON file for the migration dashboard: gap and name coverage per merge on
main, and each day's status, tracks and criteria. Needs git, the gh CLI and numpy.

    python scripts/spec-drift.py --out data.json
    python scripts/spec-drift.py --build dashboard.html  # docs/dashboard/ as one file, data inlined

Gap: angle between spec and code over the code-like names the specs put in backticks, counted in
the specs and in the code (Markdown, data/, screenshots, this dashboard and comment lines left
out: a comment naming a thing is not the thing). Each merge's gap uses only the names the specs had used by then, so a later spec does
not move an earlier point.

The angle needs a reference to be read. Two vectors of counts are never negative, so they sit
well under 90° even when they have nothing to do with each other. Gap chance is the gap to the
same code counts shuffled across the names (the mean and 95% band of CHANCE_DRAWS, seeded): a gap near it says
the code uses the spec's names in no particular proportion. The angle is the secondary reading;
two plain set figures come first. Name coverage: of the names the specs use at a merge, the share
the code has. Jaccard: the names in both, over the names in either (a name the specs have dropped
but the code keeps counts against it). Names kept: that other direction alone, how many names the
specs once used and have dropped that the code still has. Names retired: of the names the specs
use that the code lacks, those it had at an earlier merge.

Boundaries: for the services the project file names, how much of their main source has left the
monolith for services/, and how many imports still cross from one service's packages into
another's. Both are read from the tree, like the gap; neither depends on what a day ticked.

The project file, spec-drift.config.json at the root, holds what git cannot tell about this
repository, every field optional: tests renamed to another stem and directories moved, by PR
(`renamed`, `moved`: [pr, old, new]), the directories a Verify block may cd into (`verify_bases`),
the packages each service takes under `package_root` (`services`), and the infra workflow's file
(`infra_workflow`; none, and gh is not asked for its runs).
"""
import argparse
import collections
import datetime
import fnmatch
import hashlib
import importlib.util
import json
import os
import platform
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile

import numpy as np

TICK = re.compile(r"`([^`\n]+)`")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
WORDS = re.compile(r"[A-Za-z0-9_]+")
COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*|--|<!--|#(\s|!|$))")
COMMENT_TAIL = re.compile(r"/\*.*?\*/|\s(//|--|#)\s.*$")
CRITERION = re.compile(r"^- \[( |x)\]", re.M)
# A criterion's evidence: the PRs it cites and the tests it names (a class, or Class.member).
CITED_PR = re.compile(r"(?<![\w&])#(\d+)\b")
EVIDENCE_TEST = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Test|IT|Tests))(?:\.([a-z][A-Za-z0-9_]*))?\b")
# A break on purpose, in one fixed line (specs/README.md): `broken: <what> → <what it reported>`,
# on its own line in a PR description or under a ticked criterion; `broken: none → <why>` says
# there was none. In the project the kit was copied from, it replaced three regexes over free text (#310), which
# read a break described in other words as none. Nothing earlier was asked for the line, so there
# it is read from PR #310 on (310 below): a PR before it, or a day none of whose PRs reaches it, is
# not measured. A day number would not do: Days 39-41 ran before Day 28. A project whose rules ask
# for the line from its first PR leaves it at 0, and the page names no PR.
BREAK_LINE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?broken:[ \t]*(\S[^\n]*?)[ \t]*(?:→|->)[ \t]*\S", re.M)
BREAK_LINE_FROM_PR = 0  # PROJECT: the first PR asked for the broken: line; 0 for all of them
# A test CI can skip: switched off, or run only on a condition. A condition on a system property
# is met when a workflow passes it as true (GatewayHarnessIT, -Dharness.gateway=true), so that
# test runs. A bare @Disabled stays skippable. In Python, a skip decorator or a skipTest() call.
SKIPPABLE = re.compile(r"@Disabled\b|@(?:Enabled|Disabled)If|@(?:unittest\.|pytest\.mark\.)skip|\.skipTest\(|(?<![\w.])(?:(?:it|test|describe)\.(?:skip|todo)|xit|xdescribe)\(")
PY_CLASS = re.compile(r"^[ \t]*class[ \t]+(\w+)", re.M)
ON_PROPERTY = re.compile(r'@EnabledIfSystemProperty\(\s*named\s*=\s*"([^"]+)"\s*,\s*matches\s*=\s*"true"\s*\)')
CI_PROPERTY = re.compile(r"-D([\w.-]+)=true\b")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "spec-drift.config.json")


def project_config(path=CONFIG_FILE):
    """The project file's values by the names this script reads them under; with no file, or no
    field, nothing is renamed, moved or a service's, Verify reads from the root, and no infra."""
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except FileNotFoundError:
        c = {}
    root = c.get("package_root", "")
    return dict(RENAMED={n: (old, new) for n, old, new in c.get("renamed", [])},
                MOVED=[tuple(m) for m in c.get("moved", [])],
                VERIFY_BASES=(*c.get("verify_bases", []), ""),
                SERVICE=c.get("services", {}),
                OWN_PACKAGE=re.compile(rf"(?:^|/)src/main/java/{re.escape(root.replace('.', '/'))}/([a-z]+)/"),
                IMPORT=re.compile(rf"^import\s+(?:static\s+)?{re.escape(root)}\.([a-z]+)\.", re.M),
                INFRA_WORKFLOW=c.get("infra_workflow", ""))


CONFIG = project_config()
# Tests that moved to a class of another stem, which moves() cannot tell by name: the PR that
# completed the move, and (old, new). Both kept every test method's name.
RENAMED = CONFIG["RENAMED"]
# A removal check: a grep a criterion says prints nothing ("`grep ...` returns nothing", "gives 0"),
# or a Verify line that echoes when it does (`grep ... || echo "clean"`).
GREP_SAYS_NOTHING = re.compile(r"`(grep [^`]+)`[^.]{0,40}?\b(?:returns|prints|finds|gives) (?:nothing|0)\b")
GREP_OR_ECHO = re.compile(r"^(grep\s.+?)\s*\|\|\s*echo\b", re.M)
# Directories that moved, by the merge that moved them, in order. A removal check's paths follow
# them: a grep of core/stock/src/ reads stock-service's code once the PR that moved it has merged.
MOVED = CONFIG["MOVED"]
# Where a Verify block's paths are read from, the root last: the one under which they find most.
VERIFY_BASES = CONFIG["VERIFY_BASES"]
# The most lines of one check's finds on main that the page lists.
FOUND_SHOWN = 10
# "Day 17", "Days 18-19", "Days 19, 21 and 24": the days a sentence names.
DAY_REF = re.compile(r"\bDays? (\d+)((?:\s*(?:[–-]|,|and|or)\s*\d+)*)")
# IDs (specs/README.md): criterion 3 of Day 28 is C28.3, and the
# first hand-off Day 28's Notes leave is H28.1. A spec picks a hand-off up by citing its ID.
CRITERION_ID = re.compile(r"^C(\d+)\.(\d+)\s+")
# A spec may cite a range of them, H34.1–H34.3 or H35.2-4: the second end may leave its day out.
HAND_OFF_LINE = re.compile(r"\s*- \**Hand-off\b")  # a Notes item that is one, not one citing it
HAND_OFF_RANGE = re.compile(r"\bH(\d+)\.(\d+)(?:\s*[–-]\s*(?:H\1\.)?(\d+)\b)?")
# A track a PR names ("**E1c** (next, ...)"), not a grep's -A3 or Day 14's A1. A criterion ID
# (C32.5) is not a track either, so a match followed by ".<digit>" is rejected.
# A track number is one digit, as in a day's track table, so neither C32.5 nor a day-numbered C32 is one.
TRACK_NAMED = re.compile(r"(?<![-\w])(?<!Day \d\d's )(?<!Day \d\d )([A-Z]\d[a-z]?|0[a-z])\b(?!\.\d)")
PHASE_READ = re.compile(r"^\*\*Read [^*\n]*end of Phase (\d+)\.\*\*.*?(?=^\*\*Read |^## |\Z)", re.M | re.S)
# Days plan.md's list names before their specs exist: an item "numbered from N", one "numbered after
# the last", and one numbered after the last that says "close-out".
PLATFORM = "platform"
CLEANUP = "cleanup"
CLOSEOUT = "close-out"
PLACEHOLDERS = (PLATFORM, CLEANUP, CLOSEOUT)
# The target services, by the monolith packages each one takes under the package root. A package
# no service takes is no service's, so depending on it crosses nothing.
SERVICE = CONFIG["SERVICE"]
OWN_PACKAGE = CONFIG["OWN_PACKAGE"]
IMPORT = CONFIG["IMPORT"]
INFRA_WORKFLOW = CONFIG["INFRA_WORKFLOW"]
FINISHED = ("done", "closed")
CHANCE_DRAWS = 1000
# The most gh is asked for: a list that comes back this long may have been cut, and nothing else says so.
PR_LIMIT = 500
RUN_LIMIT = 5000
# GitHub's API returns at most this many for a filtered run list, so a list that long may be cut.
GH_CAP = 1000
# What every figure built on the backticked names cannot see; the dashboard lists these as written.
CAVEATS = [
    "Gap, name coverage and Jaccard see only identifiers the specs put in backticks: a name written "
    "in plain prose is not counted.",
    "They match names, not meaning: prose is not understood, so a spec can name a thing the code has "
    "and still describe it wrongly.",
    "A name a spec says should disappear still counts as shared vocabulary while the code has it.",
    "Name coverage counts test and tooling files as code.",
    "Production name coverage counts only files the path rules call production: a path, not proof "
    "that the file runs in the product. Test and tool names may legitimately be absent from it.",
]
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "docs", "dashboard")
# What git said about each merge, kept between runs: every call cached here names commits only by
# SHA, so its output never changes. Keyed on this script's text too, so a change here starts over.
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, ".spec-drift-cache.json")
CACHE = {}


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def gh_list(limit, *args):
    """A gh list call's JSON, refused when it is as long as the limit: what was cut is not known."""
    items = json.loads(run("gh", *args, "--limit", str(limit)))
    if len(items) >= limit:
        raise SystemExit(f"gh {' '.join(args[:2])} returned its limit of {limit}: some are missing, raise it")
    if len(items) == GH_CAP:
        raise SystemExit(f"gh {' '.join(args[:2])} returned {GH_CAP}, the most GitHub gives for it: some may be missing")
    return items


def at_commits(*args):
    """git with only commit SHAs for revisions: the output is fixed, so it comes from the cache."""
    key = "\0".join(args)
    out = CACHE.setdefault("git", {}).get(key)
    if out is None:
        out = CACHE["git"][key] = run("git", *args)
    return out


def load_cache(path):
    with open(__file__, "rb") as f:
        version = hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        cached = {}
    CACHE.clear()
    CACHE.update(cached if cached.get("version") == version else dict(version=version))


def save_cache(path):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(CACHE, f, separators=(",", ":"))
    os.replace(path + ".tmp", path)


class Blobs:
    """One git cat-file process for every file read, instead of one git show each. Use as
    `with Blobs() as blobs:`, so the process is closed however the block ends."""

    def __enter__(self):
        self.proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return self

    def __exit__(self, *exc):
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except OSError:
                pass
        self.proc.wait()

    def read(self, sha, path):
        try:
            self.proc.stdin.write(f"{sha}:{path}\n".encode())
            self.proc.stdin.flush()
            header = self.proc.stdout.readline().split()
        except (OSError, ValueError) as e:
            raise RuntimeError(f"git cat-file failed reading {path} at {sha}: {e}") from e
        if header[-1:] == [b"missing"]:
            return ""
        if len(header) != 3 or not header[2].isdigit():
            said = b" ".join(header).decode("utf-8", "replace") or "nothing (the process ended)"
            raise RuntimeError(f"git cat-file failed reading {path} at {sha}: it said {said}")
        body = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return body.decode("utf-8", "replace")


def day_of(path):
    m = re.search(r"day-(\d+)", path)
    return int(m.group(1)) if m else None


TITLED_TRACK = re.compile(r"\bDay (\d{2}) track ([0-9A-Za-z]+):")


def day_branch(pr):
    """The branch a PR is read by. One whose branch names no day but whose title names its track
    is read as that track's branch: #321, Day 28's Track D, was opened from
    `claude/focused-bell-qqwqdx`, and the day showed D unmerged. CI runs stay on the real branch."""
    branch = pr["headRefName"]
    m = None if day_of(branch) else TITLED_TRACK.search(pr.get("title") or "")
    return f"day-{m.group(1)}/track-{m.group(2).lower()}-titled" if m else branch


def code_like(tok):
    """Whether a backticked token is a code name. One ending in "_" is a pattern's prefix (`aws_ecs_*`), not a name."""
    if tok.endswith("_"):
        return False
    return bool(re.search(r"[a-z][A-Z]", tok)) or ("_" in tok.strip("_") and tok.lower() == tok)


WORK_KINDS = ("spec", "context", "prod", "test", "tooling")
PLACES = ("prod", "test", "tooling", "other")  # where a code name was found: a work kind, or other for the rest
NOT_WRITTEN = re.compile(r"(^|/)package-lock\.json$|^docs/dashboard/token-usage\.json$|^docs/dashboard/deployment\.json$|^screenshots/")
TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$|(Test|Tests|IT)\.java$"
                       r"|\.(test|spec)\.[jt]sx?$")


def renamed_to(path):
    """numstat writes a moved file as src/{a => b}/X.java or a => b; its lines count where it went."""
    path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", path)
    return path.split(" => ")[-1].replace("//", "/")


def work_kind(path):
    """Which box a changed file's lines go in; the first rule that matches wins. None: generated
    by a tool, not written, so not counted. A path no rule names is prod: the package, and also
    root files such as a config. experiments/ is tooling: E1's cases copy spec text, and as prod
    they raised production name coverage at #30 with no product code changed."""
    if NOT_WRITTEN.search(path):
        return None
    if path == "plan.md" or path.startswith("specs/"):
        return "spec"
    if path.endswith(".md") or path.startswith(".claude/") or (path.startswith("docs/")
                                                               and not path.startswith("docs/dashboard/")):
        return "context"
    if TEST_PATH.search(path):
        return "test"
    if path.startswith(("scripts/", ".github/", "docs/dashboard/", "kit/", "experiments/")):
        return "tooling"
    return "prod"


def place_of(path):
    """Where a code name was found: the file's work_kind, or other for any box not in PLACES."""
    w = work_kind(path)
    return w if w in PLACES else "other"


def broke(text):
    """Whether text has a `broken:` line for a break, not a `broken: none → <why>`."""
    return any(w.lower() != "none" for w in BREAK_LINE.findall(text or ""))


def broke_in(text):
    """The PRs the `broken:` lines in text cite, for the breaks they record (not `none`)."""
    return sorted({int(n) for m in BREAK_LINE.finditer(text or "") if m.group(1).lower() != "none"
                   for n in CITED_PR.findall(text[m.start():].split("\n", 1)[0])})


def broken_at_close(day):
    """A day's ticked criteria whose every break cites its closing PR: the check was first seen
    red at the close, not in the PR that wrote it. Four of the paper's six spec escapes were these
    (docs/paper, Appendix F). A criterion whose break lines cite no PR is not judged."""
    closing = {p["number"] for p in day["prs"] if p["kind"] == "close"}
    return [c["id"] or c["claim"] for c in day["evidence"]
            if c["ticked"] and closing and c["broke_in"] and set(c["broke_in"]) <= closing]


RELABEL = re.compile(r"relabel\.py compare`\)?:?\s*([^`]{0,120})")
RELABEL_FIELD = re.compile(r"\b(stage|cause|made)\s+(\d+)\s+of\s+(\d+)")


def relabel(text):
    """How often the blind defect-rater agreed with the tags the close gave, as the spec's Notes
    quote `relabel.py compare` ("stage 3 of 4, cause 3 of 4, made 4 of 4"), or None where the
    Notes tell it in prose: those days are not counted, not counted as agreeing."""
    for m in RELABEL.finditer(text):
        found = {f: [int(a), int(n)] for f, a, n in RELABEL_FIELD.findall(m.group(1))}
        if found:
            return found
    return None


def break_state(body, number):
    """What a PR description says about breaking the code on purpose: "recorded", "none" (only
    `broken: none → <why>`), "silent", or "not measured" for a PR from before the line."""
    if number < BREAK_LINE_FROM_PR:
        return "not measured"
    if broke(body):
        return "recorded"
    return "none" if BREAK_LINE.search(body or "") else "silent"


def ci_pushes(runs):
    """Per branch: the commits CI ran on for a pull request, and those that failed a workflow."""
    shas, failed = collections.defaultdict(set), collections.defaultdict(set)
    for r in runs:
        if r["event"] == "pull_request":
            shas[r["headBranch"]].add(r["headSha"])
            if r["conclusion"] == "failure":
                failed[r["headBranch"]].add((r["headSha"], r["workflowName"]))
    return {b: (len(s), len({sha for sha, _ in failed[b]}), sorted({w for _, w in failed[b]})) for b, s in shas.items()}


def verification(prs, runs):
    """Every merged PR: whether it recorded a break, and how many of its pushes CI failed."""
    ci, out = ci_pushes(runs), []
    for p in sorted(prs.values(), key=lambda p: p["number"]):
        if p.get("state") == "MERGED":
            pushes, failed, failed_in = ci.get(p["headRefName"], (0, 0, []))
            out.append(dict(number=p["number"], day=day_of(day_branch(p)), kind=kind(p["title"], day_branch(p)),
                            breaks=break_state(p.get("body"), p["number"]), pushes=pushes, failed=failed, failed_in=failed_in))
    return out


def kind(title, branch, files=None):
    """What a merge was. A spec change must touch the spec (a day spec or plan.md) when its files
    are known: #55 changed the spec rules and #59 and #107 the dashboard, none of them a spec."""
    t, b = title.lower(), branch.lower()
    if ("spec change" in t or "plan change" in t or re.match(r"day-\d+/spec", b)
            or b.startswith(("specs/", "plan/"))):
        if files is None or any(f == "plan.md" or day_of(f) for f in files):
            return "spec"
        return "other"
    if "tick" in t or "close" in b:
        return "close"
    if "track" in b or "track" in t:
        return "code"
    return "other"


def other_group(branch):
    """The branch prefix used to split merges that are not day work."""
    if "/" not in branch:
        return "other"
    prefix = branch.split("/", 1)[0].lower()
    return prefix or "other"


def other_groups(rows):
    """Group other pull requests by branch prefix, keeping the largest three visible."""
    counts = collections.Counter(other_group(row["branch"]) for row in rows
                                 if row["pr"] and row["kind"] == "other")
    groups = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(groups) > 4:
        groups = groups[:3] + [("other", sum(count for _, count in groups[3:]))]
    return [dict(name=name, count=count) for name, count in groups]


def angle(x, y):
    """None when either vector is all zeros: it has no direction, so no angle to the other."""
    nx, ny = np.linalg.norm(x), np.linalg.norm(y)
    if nx == 0 or ny == 0:
        return None
    return float(np.degrees(np.arccos(np.clip(x @ y / (nx * ny), -1, 1))))


def deg(a):
    return None if a is None else round(a, 2)


def band(angles):
    """The mean of a set of angles and the 95% interval they fall in, rounded; None if it is empty."""
    if not len(angles):
        return None
    return [round(float(v), 2) for v in (np.mean(angles), *np.percentile(angles, [2.5, 97.5]))]


def angles(X, y):
    """The angle between each row of X and y; the rows with no direction are left out."""
    nx, ny = np.linalg.norm(X, axis=1), np.linalg.norm(y)
    ok = nx > 0
    if ny == 0:
        return np.array([])
    return np.degrees(np.arccos(np.clip(X[ok] @ y / (nx[ok] * ny), -1, 1)))


def chance_angle(x, y, keep):
    """The angle between x and y with y's entries in keep shuffled among themselves, over
    CHANCE_DRAWS shuffles: [mean, 2.5th, 97.5th percentile], or None with no angle. Seeded per
    call, so the same counts give the same chance at every merge."""
    idx, rng = np.flatnonzero(keep), np.random.default_rng(0)
    if len(idx) < 2:
        a = angle(x, y)
        return None if a is None else [round(a, 2)] * 3
    Z = np.tile(y, (CHANCE_DRAWS, 1))
    # sorted first: only the counts matter, not their order
    Z[:, idx] = rng.permuted(np.tile(np.sort(y[idx]), (CHANCE_DRAWS, 1)), axis=1)
    return band(angles(Z, x))


def name_overlap(spec, code, known):
    """Name coverage and Jaccard over the names known at a merge: of the names the specs use, the
    share the code has; and the names in both over the names in either. None with nothing to divide.
    Last, the other direction: how many names the specs once used and have dropped, that the code keeps."""
    named, in_code = (spec > 0) & known, (code > 0) & known
    both, either = (named & in_code).sum(), (named | in_code).sum()
    return (round(float(both / named.sum()), 3) if named.any() else None,
            round(float(both / either), 3) if either else None,
            int((in_code & ~named).sum()))


def code_names(line, index):
    """The vocabulary names on one line of code, or none if the line is a comment."""
    if COMMENT_LINE.match(line):
        return []
    return [w for w in WORDS.findall(COMMENT_TAIL.sub("", line)) if w in index]


def snapshots(prs):
    """Each commit on main's first-parent history, oldest first: a PR's merge or squash, or a commit with no PR."""
    if subprocess.run(["git", "rev-parse", "--verify", "-q", "main"], capture_output=True).returncode:
        sys.exit("spec-drift.py reads the branch main, and this repository has none: git branch -m main")
    out = []
    for line in run("git", "log", "--first-parent", "main", "--reverse",
                    "--format=%h|%ad|%P|%s", "--date=short").splitlines():
        sha, date, parents, subject = line.split("|", 3)
        m = re.match(r"Merge pull request #(\d+)", subject)
        if not m and len(parents.split()) == 1:
            squash = re.search(r" \(#(\d+)\)$", subject)
            if squash and prs.get(int(squash.group(1)), {}).get("state") == "MERGED":
                m = squash
        p = prs.get(int(m.group(1)), {}) if m else {}
        title = p.get("title", subject)
        out.append(dict(sha=sha, date=date, pr=int(m.group(1)) if m else None, title=title,
                        branch=p.get("headRefName", "")))
    return out


def ranker(order):
    """A day's place in the run order: 0 (and plan.md) first, a day the order does not list after it by number."""
    pos = {d: i for i, d in enumerate(d for d in order if d not in PLACEHOLDERS)}
    return lambda d: -1 if not d else pos.get(d, len(pos) + d)


# The rules and the template are about specs, not specs: their examples are the source project's names.
NOT_SPECS = ("specs/README.md", "specs/_template.md")


def trajectory(snaps, blobs, order=()):
    """Gap and coverage per merge. Days compare by their place in the run order, so from
    Day 38 the unbuilt Days 17-37 are not reached; with no order, by number."""
    rank = ranker(order)
    for s in snaps:
        names = at_commits("ls-tree", "-r", "--name-only", s["sha"], "--", "plan.md", "specs").split()
        s["texts"] = {n: blobs.read(s["sha"], n) for n in names if n.endswith(".md") and n not in NOT_SPECS}
    first_seen = {}
    for i, s in enumerate(snaps):
        for t in s["texts"].values():
            for span in TICK.findall(t):
                for tok in IDENT.findall(span):
                    if code_like(tok):
                        first_seen.setdefault(tok, i)
    vocab = sorted(first_seen)
    index = {v: i for i, v in enumerate(vocab)}
    born = np.array([first_seen[v] for v in vocab])
    patterns = "\n".join(vocab) + "\n"

    def spec_counts(texts):
        c = np.zeros(len(vocab))
        for t in texts.values():
            for span in TICK.findall(t):
                for tok in IDENT.findall(span):
                    if tok in index:
                        c[index[tok]] += 1
        return c

    def code_counts(sha):
        """A merge's counts need only the names the specs had used by then (later ones are masked
        by born), and a SHA fixes its history, so counts cached under it stay right. kit/ is the
        starter cleanup's tooling and the source project's data, which a copy deletes: not this project's code.
        specs/ is the spec side, every file in it: its break manifests copy the spec's names, and
        counted as code they narrowed the gap at #25 with no code changed.
        One count vector per place, by the file's work_kind: prod, test, tooling, and other for
        any box else; summed, they are the all-code counts. The cache keeps each name's count per
        path, so a merge's lists can say where a name is; the second return is name -> paths."""
        cached = CACHE.setdefault("code", {}).get(sha)
        if cached is None:
            out = subprocess.run(["git", "grep", "-z", "-I", "-w", "-F", "-f", "-", sha, "--", ".",
                                  ":(exclude)*.md", ":(exclude)data", ":(exclude)screenshots", ":(exclude)docs/dashboard",
                                  ":(exclude)**/package-lock.json", ":(exclude)kit", ":(exclude)specs"], input=patterns,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
            cached = {}
            for match in out.split("\n"):
                where, _, text = match.partition("\0")  # -z: "<sha>:<path>\0<line>"
                path = where.split(":", 1)[-1]
                for line in text.splitlines():  # as the lines were read before the path was kept
                    for tok in code_names(line, index):
                        cached.setdefault(tok, {}).setdefault(path, 0)
                        cached[tok][path] += 1
            CACHE["code"][sha] = cached
        counts = {p: np.zeros(len(vocab)) for p in PLACES}
        for tok, paths in cached.items():
            for path, n in paths.items() if tok in index else ():
                counts[place_of(path)][index[tok]] += n
        return counts, cached

    day, rows, ever = 0, [], np.zeros(len(vocab), dtype=bool)
    for i, s in enumerate(snaps):
        touched = at_commits("diff", "--name-only", snaps[i - 1]["sha"], s["sha"], "--", "plan.md", "specs").split() if i else []
        s["kind"] = "origin" if s["pr"] is None else kind(s["title"], s["branch"], touched)
        m = re.match(r"day-(\d+)/", s["branch"])
        if m and s["kind"] in ("code", "close"):
            day = max(day, int(m.group(1)), key=rank)
        spec, (places, where) = spec_counts(s["texts"]), code_counts(s["sha"])
        code = sum(places.values())
        reached = spec_counts({f: t for f, t in s["texts"].items() if day_of(f) and rank(day_of(f)) <= rank(max(day, 1))})
        added = deleted = 0
        for line in at_commits("diff", "--numstat", snaps[0]["sha"], s["sha"], "--", "plan.md", "specs").splitlines():
            a, d, _ = line.split("\t")
            added, deleted = added + int(a), deleted + int(d)
        spec_files, churn, work = {}, 0, dict.fromkeys(WORK_KINDS, 0)
        if i:
            for line in at_commits("diff", "--numstat", snaps[i - 1]["sha"], s["sha"]).splitlines():
                a, d, f = line.split("\t")
                w = work_kind(renamed_to(f))
                if a != "-" and w:
                    work[w] += int(a) + int(d)
            for line in at_commits("diff", "--numstat", snaps[i - 1]["sha"], s["sha"], "--", "plan.md", "specs").splitlines():
                a, d, f = line.split("\t")
                spec_files[f] = int(a) + int(d)
            for line in at_commits("diff", "--numstat", snaps[i - 1]["sha"], s["sha"], "--", ".", ":(exclude)*.md",
                                ":(exclude)docs/dashboard").splitlines():
                a, d, _ = line.split("\t")
                churn += 0 if a == "-" else int(a) + int(d)
        mask, known = reached > 0, born <= i
        spec_v, code_v = np.log1p(spec) * known, np.log1p(code) * known
        name_coverage, jaccard, names_kept = name_overlap(spec, code, known)
        # A name the code had at an earlier merge and lacks now: `ever` holds only the merges before this one.
        in_code = (code > 0) & known
        names_retired = int(((spec > 0) & known & ~in_code & ever).sum())
        # Production coverage: the same share, of the names in files work_kind calls prod; the
        # counts beside it are its numerator and the all-code one, over spec_names.
        named = (spec > 0) & known
        prod_name_coverage = name_overlap(spec, places["prod"], known)[0]
        # The names behind the two counts' difference, and behind the all-code miss: sorted, each
        # outside-production name with the places it is in and its paths there.
        outside = [vocab[k] for k in np.flatnonzero(named & in_code & (places["prod"] == 0))]
        names_outside_prod = {v: {p: sorted(f for f in where[v] if place_of(f) == p)
                                  for p in PLACES if any(place_of(f) == p for f in where[v])} for v in outside}
        names_absent = [vocab[k] for k in np.flatnonzero(named & ~in_code)]
        ever |= in_code
        chance = chance_angle(spec_v, code_v, known)
        rows.append(dict(i=i, sha=s["sha"], date=s["date"], pr=s["pr"], title=s["title"], branch=s["branch"], kind=s["kind"],
                         day=day if s["pr"] else 0, added=added, deleted=deleted,
                         gap_full=deg(angle(spec_v, code_v)),
                         gap_chance=chance and chance[0], gap_chance_band=chance and chance[1:],
                         coverage=round(float((code[mask] > 0).mean()), 3) if s["pr"] and mask.any() else None,
                         name_coverage=name_coverage, jaccard=jaccard, names_kept=names_kept, names_retired=names_retired,
                         prod_name_coverage=prod_name_coverage, spec_names=int(named.sum()),
                         names_in_code=int((named & in_code).sum()), names_in_prod=int((named & (places["prod"] > 0)).sum()),
                         names_outside_prod=names_outside_prod, names_absent=names_absent,
                         spec_files=spec_files, code_churn=churn, work=work))
    files = sorted({f for s in snaps for f in s["texts"] if f == "plan.md" or day_of(f)},
                   key=lambda f: (rank(day_of(f)), f))
    lines = sum(t.count("\n") for t in snaps[0]["texts"].values())
    return rows, files, len(vocab), lines


def boundaries(rows, snaps, blobs):
    """At each merge, for the project file's services: the lines of main source still in the monolith
    and in the service's own directory under services/, and the imports where one service's
    package reaches into another's ("matching>identity"). Tests are left out, as a test may
    assemble modules; a fully qualified name used without an import is not seen."""
    seen = {}
    for r, s in zip(rows, snaps):
        placed, crossed = {}, collections.Counter()
        for line in at_commits("ls-tree", "-r", s["sha"]).splitlines():
            meta, path = line.split("\t", 1)
            m = OWN_PACKAGE.search(path)
            if not path.endswith(".java") or not m or m.group(1) not in SERVICE:
                continue
            blob = meta.split()[2]
            if blob not in seen:
                text = blobs.read(s["sha"], path)
                seen[blob] = (text.count("\n"), IMPORT.findall(text))
            n, imports = seen[blob]
            owner = SERVICE[m.group(1)]
            placed.setdefault(owner, [0, 0])[path.startswith("services/")] += n
            crossed.update(f"{owner}>{SERVICE[p]}" for p in imports if SERVICE.get(p, owner) != owner)
        r["placed"], r["crossings"] = placed, dict(crossed)


def section(text, heading):
    m = re.search(rf"^## {heading}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


# Every number is a day but a phase's: "Phase 3:", "Phases 6–7", "after Phase 7", "end-of-Phase-7".
UNPHASED = re.compile(r"Phases?[\s-]*\d+(?:\s*[–-]\s*\d+)?")


def plan_items(plan):
    """The numbered items of plan.md's "Day-by-day specs" list, each on one line: an item may wrap mid-phrase."""
    return [" ".join(item.split()) for item in
            re.findall(r"^\d+\.\s+(.*(?:\n {3}.*)*)", section(plan, "Day-by-day specs"), re.M)]


def stops(plan):
    """The days plan.md's list marks **Stop and evaluate.**, each the last day its item names. A stop
    a course correction has lifted is still one: the roadmap shows where the plan stopped."""
    out = []
    for item in plan_items(plan):
        days = re.findall(r"\d+", UNPHASED.sub("", item))
        if "Stop and evaluate" in item and days:
            out.append(int(days[-1]))
    return out


def phases(plan):
    """Each phase's name, by number, from plan.md's "### Phase N — <name>" headings; "" for a number with none."""
    named = {int(n): name.strip() for n, name in re.findall(r"^### Phase (\d+) [—–-] (.+)$", plan, re.M)}
    return [named.get(p, "") for p in range(max(named, default=-1) + 1)]


def lifted(plan, phase):
    """Whether plan.md lifts the stop of Phase `phase`: a "Course correction after Phase N" section,
    or the template's "Course corrections" section naming the phase outside its comment. A pilot
    of the kit put its stop's decisions there, and three days ran under the stop."""
    corrections = re.sub(r"<!--.*?-->", "", section(plan, "Course corrections"), flags=re.S)
    return bool(section(plan, f"Course correction after Phase {phase}")
                or re.search(rf"\bPhase {phase}\b", corrections))


def run_order(plan, spec_days):
    """The order days run in, from plan.md's "Day-by-day specs" list, and the day after which work
    stops to be evaluated. Days the list does not name (the finished ones) come first. A day named
    twice runs at its last mention: Day 24's profile endpoint comes first, the rest of it last.
    "Numbered after the last" is a day not yet written, after every day the list names. A stop is
    lifted once plan.md has the course correction for its phase. An item marked **cut** takes its
    days out of the order: Phase 6's Days 29-31, cut after Phase 7, run nowhere."""
    listed, cut, stop = [], [], None
    lines = plan_items(plan)
    unphased = lambda line: UNPHASED.sub("", line)
    last = max((int(n) for line in lines for n in re.findall(r"\d+", unphased(line))), default=0)
    for line in lines:
        days = []
        for a, b in re.findall(r"(\d+)(?:\s*[–-]\s*(\d+))?", unphased(line)):
            if "numbered from" in line:
                days += sorted(d for d in spec_days if d >= int(a)) or [PLATFORM]
            else:
                days += range(int(a), int(b or a) + 1)
        if "numbered after the last" in line:
            days = sorted(d for d in spec_days if d > last) or [CLOSEOUT if "close-out" in line else CLEANUP]
        if "**cut**" in line:
            cut += days
            continue
        listed += days
        phase = re.search(r"Phase (\d+)", line)
        if "Stop and evaluate" in line and days and not (phase and lifted(plan, phase.group(1))):
            stop = days[-1]
    listed = [d for i, d in enumerate(listed) if d not in listed[i + 1:]]
    return [d for d in sorted(spec_days) if d not in listed + cut] + listed, stop


def out_of_order(order):
    """The days that do not run in numeric order: those outside the longest run of the order that
    rises by number: Days 38-40 ran ahead of Day 17, Day 17 behind Days 18-19, and Day 25 is planned
    behind Days 26-27."""
    days = [d for d in order if d not in PLACEHOLDERS]
    best = []  # best[i]: the longest rising run that ends at days[i]
    for i, d in enumerate(days):
        best.append(max((best[j] for j in range(i) if days[j] < d), key=len, default=[]) + [d])
    keep = set(max(best, key=len, default=[]))
    return [d for d in days if d not in keep]


# A closing PR the gate split, "Day 43 close, part 1": one part of the close, not all of it.
CLOSE_PART = re.compile(r"\bpart \d+\b", re.I)


def closes(day):
    """Whether a day's own closing PR has merged. A part of a split close (#428) ends the day only
    once every box is ticked: until the last part merges, its boxes are still open."""
    c = day["criteria"]
    return any(p["kind"] == "close" and not (CLOSE_PART.search(p.get("title", "")) and c["ticked"] < c["total"])
               for p in day["prs"])


def settle(days, order):
    """Each day's status. A day is finished once a day later in the run order has code merged, or
    its own closing PR has; finished with every box ticked is done, with boxes open is closed
    (Day 05 left two open). The first unfinished day is active."""
    pos = {d: i for i, d in enumerate(order)}
    worked = max((pos[d["day"]] for d in days if d["day"] in pos
                  and any(p["kind"] in ("code", "close") for p in d["prs"])), default=-1)
    for d in days:
        at = pos.get(d["day"], len(order))
        c = d["criteria"]
        if at < worked or (at == worked and closes(d)):
            d["status"] = "done" if c["total"] and c["ticked"] == c["total"] else "closed"
        elif d["status"] == "done":
            d["status"] = "ready"
    by_day = {d["day"]: d for d in days}
    for d in order:
        if d in PLACEHOLDERS or d not in by_day:  # not written yet: no day to mark
            break
        if by_day[d]["status"] not in FINISHED:
            by_day[d]["status"] = "active"
            break
    return days


# A track's name is a letter or a digit, with a number when a day splits one (Day 39's A1, A2),
# or a lowercase letter (Day 20's 0a, 0b).
TRACK_ROW = re.compile(r"^\| *([0-9A-Z][0-9]?[a-z]?) *\|")


def track_names(section_text):
    """The tracks a day's table lists, in its order."""
    return [m.group(1) for line in section_text.splitlines() if (m := TRACK_ROW.match(line))]


def merged_tracks(branches, tracks):
    """The tracks with a merged branch. `track-a1-...` is A1 when the table lists A1; otherwise it
    is part of A, as Day 15's `track-c1`..`c4` were its one track C. A letter after the digits is a
    part of that track: Day 17 split B1 again into `track-b1a`..`b1c`, unless the table lists it
    with that letter, as Day 20 lists 0a and 0b."""
    by_lower = {t.lower(): t for t in tracks}
    merged = set()
    for branch in branches:
        m = re.search(r"/track-([0-9a-z])([0-9]*)([a-z]?)(?:-|$)", branch)
        if m:
            head, number, part = m.groups()
            name = next((by_lower[c] for c in (head + number + part, head + number) if c in by_lower),
                        head.upper())
            merged.add(name)
    return sorted(merged)


def unopened_tracks(day_prs):
    """Tracks a day's PRs name that no branch of the day ever did: #298 announced E1c, and the day
    read as done for a day before #305 opened it. A branch covers the tracks it begins (`track-e1`
    covers E1c) and those that begin it (`track-b` was Day 22's B1). Only letters a branch uses count."""
    branches = {m.group(1) for p in day_prs if (m := re.search(r"/track-([0-9a-z]+?)(?:-|$)", day_branch(p)))}
    found = {}
    for p in sorted(day_prs, key=lambda p: p["number"]):
        for t in TRACK_NAMED.findall(p.get("body") or ""):
            low = t.lower()
            if low[0] in {b[0] for b in branches} and not any(b.startswith(low) or low.startswith(b) for b in branches):
                found.setdefault(t, p["number"])
    return [dict(track=t, pr=n) for t, n in sorted(found.items())]


def excerpt(text, n):
    """text on one line, cut at a word within n characters and never inside a code span."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    if cut.count("`") % 2:
        cut = cut[:cut.rindex("`")].rstrip()
    return cut + "…"


def criteria(section_text):
    """Each criterion under "Acceptance criteria": its kind, whether it is ticked, and the PRs,
    tests and `broken:` lines written into it."""
    out = []
    for block in re.split(r"^(?=- \[[ x]\])", section_text, flags=re.M):
        if not block.startswith("- ["):
            continue
        tag = re.search(r"\*\*(new|hold)\*\*", block)
        body = block[5:].lstrip()
        cid = CRITERION_ID.match(body)
        claim = excerpt(re.sub(r"^(\*\*\w+\*\*\s*—\s*)?", "", body[cid.end() if cid else 0:]), 140)
        out.append(dict(id=f"C{cid.group(1)}.{cid.group(2)}" if cid else None,
                        ticked=block.startswith("- [x]"), kind=tag.group(1) if tag else None,
                        claim=claim, prs=sorted({int(n) for n in CITED_PR.findall(block)}),
                        tests=sorted({c + ("." + m if m else "") for c, m in EVIDENCE_TEST.findall(block)}),
                        red=broke(block), broke_in=broke_in(block), block=block))
    return out


def replacement_lines(block):
    """The IDs before '— was:' on each Replaces line, as used by the behaviour index."""
    short = r"S-[a-z0-9]+(?:-[a-z0-9]+)*\.\d+"
    return [(line, re.findall(rf"\b(?:C\d+\.\d+|{short})\b", line.split("\u2014")[0]))
            for line in re.findall(r"^[ \t]*Replaces:(.*)$", block, re.M)]


def in_evidence_format(crit):
    """Whether a spec was written to the evidence format (#55: every criterion new or hold, every
    check seen to fail), which came in on Day 9. A spec written before it tags no criterion, and
    nothing asked its ticks to cite a PR, name a test or record a break: its zeros are not measured."""
    return any(c["kind"] for c in crit)


def moves(snaps):
    """The test classes that moved under a new name, and the merge each move completed at, as
    (index, old, new). A test moved into a service ends in Test, not IT (the unit-test run takes it there),
    and is rewritten for that service's harness, too much for git to call it a rename: Day 21
    moved PostingShortlistUnavailableIT to matching-service at 27% alike. So a move is told by
    name: a class leaves the tree, and a class of the same stem arrives in that merge or a later
    one. Day 17 deleted SavedJobCountsUnavailableIT in #174, and its Test arrived in #180. Only a
    test moves: StubLlm leaving and StubLlmTest arriving is a helper gone and a test of it. A
    name can arrive more than once (InternalCallsObservedTest in matching-service on Day 21, then
    in application-service in #305, after the IT left in #296): the first arrival at or after
    the test left is where it went. A move to another stem is in RENAMED."""
    at = {s["sha"]: i for i, s in enumerate(snaps)}
    added, deleted, i = collections.defaultdict(list), {}, None
    for line in run("git", "log", "--first-parent", "--diff-merges=first-parent", "--no-renames", "--reverse",
                    "--name-status", "--format=@%h", snaps[-1]["sha"], "--", "*.java", "*.kt").splitlines():
        if line.startswith("@"):
            i = at.get(line[1:])
        elif line[:1] in ("A", "D") and i is not None:
            name = os.path.splitext(os.path.basename(line.split("\t")[-1]))[0]
            if line[0] == "A":
                added[name].append(i)
            else:
                deleted[name] = i
    stem = lambda n: re.sub(r"(?:IT|Tests?)$", "", n)
    out = []
    for old, gone in ((n, k) for n, k in deleted.items() if stem(n) != n):
        new = min(((k, n) for n, ks in added.items() if n != old and stem(n) == stem(old)
                   for k in ks if k >= gone), default=None)
        if new:
            out.append((new[0], old, new[1]))
    pr_at = {s["pr"]: i for i, s in enumerate(snaps) if s.get("pr")}
    return out + [(pr_at[n], old, new) for n, (old, new) in RENAMED.items() if n in pr_at]


def test_state(tests, sha, blobs, moved=None):
    """Where each named test stands at a commit: present, skippable (a class or file CI can skip,
    unless CI meets its condition), or missing. CI runs every test that is present, so a hold
    goes quiet only by its test leaving the tree or being switched off. A class that has left
    the tree is looked for under the name it moved to by then (moved: old to new). A Java or Kotlin
    class is its file's name; a Python test file holds its classes under other names (CliTest in
    tests/test_cli.py), so they are read from it."""
    files, ci = collections.defaultdict(list), set()
    for path in (line.split("\t", 1)[1] for line in at_commits("ls-tree", "-r", sha).splitlines()):
        if path.endswith((".java", ".kt")):
            files[os.path.splitext(os.path.basename(path))[0]].append(path)
        elif path.endswith(".py") and TEST_PATH.search(path):
            for cls in set(PY_CLASS.findall(blobs.read(sha, path))):
                files[cls].append(path)
        elif path.startswith(".github/workflows/"):
            ci.update(CI_PROPERTY.findall(blobs.read(sha, path)))
    met = lambda m: "" if m.group(1) in ci else m.group(0)
    out = {}
    for t in tests:
        cls, _, member = t.partition(".")
        seen = {cls}
        while cls not in files and (moved or {}).get(cls) not in seen | {None}:
            cls = moved[cls]
            seen.add(cls)
        texts = [blobs.read(sha, p) for p in files.get(cls, [])]
        if not any(not member or re.search(rf"\b{member}\b", x) for x in texts):
            out[t] = "missing"
        else:
            out[t] = "skippable" if any(SKIPPABLE.search(ON_PROPERTY.sub(met, x)) for x in texts) else "present"
    return out


def roadmap(snaps, prs, blobs):
    head = snaps[-1]["sha"]
    merged = [p for p in prs.values() if p.get("state") == "MERGED"]
    days = []
    # A day's spec can be renamed (Day 10 was), so its first estimate is looked up by day.
    first_files = {day_of(f): f for f in run("git", "ls-tree", "--name-only", snaps[0]["sha"], "specs/").split()}
    for path in sorted(run("git", "ls-tree", "--name-only", head, "specs/").split()):
        d = day_of(path)
        if not d:
            continue
        text, first = blobs.read(head, path), blobs.read(snaps[0]["sha"], first_files.get(d, path))
        crit = section(text, "Acceptance criteria")
        tracks = track_names(section(text, "Tracks"))
        work = {r.split("|")[1].strip(): r.split("|")[3].strip() for r in section(text, "Tracks").splitlines()
                if TRACK_ROW.match(r)}
        mine = sorted((p for p in merged if day_branch(p).startswith(f"day-{d:02d}/")), key=lambda p: p["number"])
        done_tracks = merged_tracks([day_branch(p) for p in mine], tracks)
        worked = sorted(s["date"] for s in snaps if s["pr"] in {p["number"] for p in mine})
        ticked, total = crit.count("- [x]"), len(CRITERION.findall(crit))
        exp = re.search(r"Expected PRs:\*\* *(\d+)", text)
        exp0 = re.search(r"Expected PRs:\*\* *(\d+)", first)
        phase = re.search(r"\*\*Phase:\*\* *(\d+)", text)
        title = re.search(r"^# Day \d+ — (.+)$", text, re.M)
        ev = criteria(crit)
        status = ("done" if total and ticked == total
                  else "cut" if "**Status:** cut" in text
                  else "provisional" if "**Status:** provisional" in text else "ready")
        days.append(dict(day=d, file=path, title=title.group(1) if title else path, phase=int(phase.group(1)) if phase else None,
                         status=status, expected=int(exp.group(1)) if exp else None,
                         expected_first=int(exp0.group(1)) if exp0 else None,
                         tracks=tracks, track_work=work, tracks_merged=done_tracks,
                         tracks_unopened=unopened_tracks([p for p in prs.values()
                                                          if day_branch(p).startswith(f"day-{d:02d}/")]),
                         track_prs=sum(kind(p["title"], day_branch(p)) == "code" for p in mine),
                         prs=[dict(number=p["number"], kind=kind(p["title"], day_branch(p)), title=p["title"]) for p in mine],
                         criteria=dict(total=total, ticked=ticked, new=crit.count("**new**"), hold=crit.count("**hold**")),
                         worked=[worked[0], worked[-1]] if worked else None,
                         evidence_format=in_evidence_format(ev),
                         break_format=any(p["number"] >= BREAK_LINE_FROM_PR for p in mine),
                         evidence=ev, removal_checks=removal_checks(text), relabel=relabel(text)))
        days[-1]["broken_at_close"] = broken_at_close(days[-1])
    order, stop = run_order(blobs.read(head, "plan.md"), [d["day"] for d in days])
    return evidence(settle(days, order), snaps, blobs), order, stop


def evidence(days, snaps, blobs):
    """Where each finished day's named tests stand on main. A name counts as evidence if it was in
    the tree when the day's last PR merged: a claim can offer a choice (Day 14: one IT "or a case
    added to" another), and a day can rewrite a test it names (Day 11), and neither is a loss."""
    at = {s["pr"]: i for i, s in enumerate(snaps) if s["pr"]}
    named = {t for d in days for c in d["evidence"] for t in c["tests"]}
    now = test_state(named, snaps[-1]["sha"], blobs, {old: new for _, old, new in moves(snaps)})
    for d in days:
        ended = sorted(at[p["number"]] for p in d["prs"] if p["number"] in at)
        if d["status"] not in FINISHED or not ended:
            d["evidence"] = [dict(c, tests={}) for c in d["evidence"]]
            continue
        d["ended_at"] = ended[-1]
        then = test_state({t for c in d["evidence"] for t in c["tests"]}, snaps[ended[-1]]["sha"], blobs)
        for c in d["evidence"]:
            c["tests"] = {t: now[t] for t in c["tests"] if then[t] != "missing"}
    return days


def snap_day(s):
    """The day a merge is read as, as day_branch reads its PR: the day its branch names, or its title's track."""
    return day_of(day_branch(dict(headRefName=s["branch"], title=s["title"])))


def spec_names(text, test):
    """Whether a spec names a test, by its class or its member, as a word."""
    cls, _, member = test.partition(".")
    return any(re.search(rf"\b{w}\b", text) for w in (cls, member) if w)


def evidence_history(rows, days, snaps, blobs):
    """At each merge, where the tests of the days finished by then stand: the check that a hold's
    test has not left the tree or been switched off, merge by merge, as CI cannot see it. A test that
    goes missing in a merge whose day's spec names it is retired, not lost: a later day took it out."""
    moved, head = moves(snaps), snaps[-1]["sha"]
    specs = {d["day"]: blobs.read(head, d["file"]) for d in days if d.get("file")}
    last, retired = {}, set()
    for i, (r, s) in enumerate(zip(rows, snaps)):
        names = {t for d in days if d.get("ended_at", len(snaps)) <= i for c in d["evidence"] for t in c["tests"]}
        states = test_state(names, s["sha"], blobs, {old: new for k, old, new in moved if k <= i})
        spec = specs.get(snap_day(s), "")
        for t, st in states.items():
            if st == "missing" and (last.get(t) == "retired" or (last.get(t) == "present" and spec_names(spec, t))):
                states[t] = "retired"
            last[t] = states[t]
        retired = {t for t, st in states.items() if st == "retired"}
        r["evidence"] = ({k: list(states.values()).count(k) for k in ("present", "skippable", "missing", "retired")}
                         if names else None)
    for d in days:  # the page's list agrees with the last merge's count
        for c in d["evidence"]:
            c["tests"] = {t: "retired" if t in retired else st for t, st in c["tests"].items()}


def removal_checks(text):
    """The greps a day's criteria and Verify say print nothing, each once. Notes and Goal can quote
    a grep without it being a check (Day 08's "TODO day-08"), so they are not read."""
    crit = re.sub(r"\s+", " ", section(text, "Acceptance criteria"))
    verify = "\n".join(re.findall(r"^```bash\n(.*?)^```", section(text, "Verify"), re.M | re.S))
    return list(dict.fromkeys(c.strip() for c in GREP_SAYS_NOTHING.findall(crit) + GREP_OR_ECHO.findall(verify)))


def grep_rule(cmd):
    """A grep as (regex, paths, include glob), or None for one this does not run (a pipe)."""
    try:
        args = shlex.split(cmd)[1:]
    except ValueError:
        return None
    if "|" in args:
        return None
    flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    include = next((a.split("=", 1)[1] for a in args if a.startswith("--include=")), None)
    words = [a for a in args if not a.startswith("-")]
    if not words:
        return None
    pattern = words[0]
    if "E" not in flags:  # basic regex: \| \( \+ ... are the operators, | ( + ... are literal
        pattern = re.sub(r"\\([|(){}+?])|([|(){}+?])", lambda m: m.group(1) or "\\" + m.group(2), pattern)
    return re.compile(pattern, re.I if "i" in flags else 0), words[1:] or ["."], include


def in_scope(path, scope):
    """Whether a tree path is the file, or under the directory, a shell path names; * stays in one level."""
    want = [p for p in scope.split("/") if p not in ("", ".")]
    have = path.split("/")
    return len(have) >= len(want) and all(fnmatch.fnmatchcase(h, w) for h, w in zip(have, want))


def follow(scope, moves):
    """The paths a shell path names after these moves, (old, new) in order. A path inside a moved
    directory goes with it; one that names a parent of it (backend, backend/*/src) keeps reading
    where it was and also reads where the directory went."""
    out = [scope]
    for old, new in moves:
        o, nxt = old.split("/"), []
        for sc in out:
            s = [p for p in sc.split("/") if p not in ("", ".")]
            if s[:len(o)] == o:
                nxt.append("/".join([new] + s[len(o):]))
                continue
            if all(fnmatch.fnmatchcase(a, b) for a, b in zip(o, s)):
                nxt.append("/".join([new] + s[len(o):]))
            nxt.append(sc)
        out = nxt
    return out


def removal_history(days, snaps, blobs, moved=None):
    """Each finished day's removal checks, run at every merge from the day's end: the files each
    reads, the lines it finds, and on the last merge which lines. Paths are read from the Verify
    base under which more of them exist, the first on a tie, and follow the directories that moved
    (MOVED, unless given). A check that reads no files prints
    nothing too, so the files are counted. Blobs are counted once, by id."""
    checks = [dict(day=d["day"], cmd=cmd, since=d["ended_at"], rule=grep_rule(cmd), hits=[], files=[], found=[])
              for d in days if "ended_at" in d for cmd in d["removal_checks"]]
    checks = [c for c in checks if c["rule"]]
    pr_at = {s["pr"]: i for i, s in enumerate(snaps) if s.get("pr")}
    seen = {}
    for i, s in enumerate(snaps):
        live = [c for c in checks if c["since"] <= i]
        if not live:
            continue
        moves = [(old, new) for n, old, new in (MOVED if moved is None else moved) if pr_at.get(n, len(snaps)) <= i]
        scopes_from = lambda b, paths: [f for p in paths for f in follow(posixpath.normpath(posixpath.join(b, p)), moves)]
        tree = [(meta.split()[2], path) for meta, path in (line.split("\t", 1) for line in
                at_commits("ls-tree", "-r", s["sha"]).splitlines()) if meta.split()[1] == "blob"]
        for c in live:
            rx, paths, include = c["rule"]
            if "base" not in c:
                c["base"] = max(VERIFY_BASES, key=lambda b: sum(
                    any(in_scope(f, sc) for _, f in tree) for sc in scopes_from(b, paths)))
            scopes = scopes_from(c["base"], paths)
            n = files = 0
            for oid, path in tree:
                if (any(in_scope(path, sc) for sc in scopes)
                        and (include is None or fnmatch.fnmatchcase(posixpath.basename(path), include))):
                    files += 1
                    if (c["cmd"], oid) not in seen:
                        text = blobs.read(s["sha"], path)
                        seen[c["cmd"], oid] = 0 if "\0" in text else sum(bool(rx.search(x)) for x in text.splitlines())
                    n += seen[c["cmd"], oid]
                    if i == len(snaps) - 1 and seen[c["cmd"], oid]:
                        c["found"] += [dict(path=path, line=k, text=x.strip()[:160]) for k, x in
                                       enumerate(blobs.read(s["sha"], path).splitlines(), 1) if rx.search(x)]
            c["hits"].append(n)
            c["files"].append(files)
            c["found"] = c["found"][:FOUND_SHOWN]
    return [dict(day=c["day"], cmd=c["cmd"], base=c["base"] or ".", since=c["since"], hits=c["hits"],
                 files=c["files"], found=c["found"]) for c in checks]


def days_named(text):
    out = set()
    for m in DAY_REF.finditer(text):
        nums = [int(m.group(1))] + [int(n) for n in re.findall(r"\d+", m.group(2))]
        out.add(nums[0])
        for sep, a, b in zip(re.findall(r"[–-]|,|and|or", m.group(2)), nums, nums[1:]):
            out.update(range(a, b + 1) if sep in "–-" else {b})
    return out


def hand_off_ids(text):
    """Every hand-off ID a text cites, a range (H34.1–H34.3, or H34.1–3) as each ID in it."""
    out = set()
    for day, a, b in HAND_OFF_RANGE.findall(text):
        out.update(f"H{day}.{n}" for n in range(int(a), int(b or a) + 1))
    return out


def written_by_day(sha, path, number):
    """The day whose PR added a line: its first-parent commit's subject names the day's branch."""
    commit = run("git", "blame", "--first-parent", "--porcelain", "-L", f"{number},{number}",
                 sha, "--", path).split()[0]
    return day_of(run("git", "log", "-1", "--format=%s", commit))


def hand_offs(days, order, sha, blobs):
    """What finished days hand to days not yet run, and whether the receiving spec picked it up.
    A Notes item with an ID of its day (H28.1) is picked up only by a spec that cites the ID; with
    no day ahead to take it and no spec citing it, it is listed with `to` None, as no later day
    picks it up. A day is ahead once plan.md puts it in the order and it is not finished, its spec
    written or not: a day writes its own spec, so between days the next one has none. An item
    without one is picked up when the receiving spec names its day, or a finished day the item
    names (Day 39 points to Day 38's findings); a code comment, when the spec names its file. A
    code comment is not handed to the day whose own PR wrote it: a workflow's
    "(Day 35)" written by Day 35."""
    finished = {d["day"] for d in days if d["status"] in FINISHED}
    text = {d["day"]: blobs.read(sha, d["file"]) for d in days}
    ahead = {d: i for i, d in enumerate(order) if d not in finished and d not in PLACEHOLDERS}
    cited = {t: hand_off_ids(text[t]) for t in text}
    out = []
    for d in days:
        if d["day"] not in finished:
            continue
        for item in re.split(r"\n(?=\s*- )", section(text[d["day"]], "Notes")):
            named = days_named(item)
            ids = sorted(i for i in hand_off_ids(item) if int(i[1:].split(".")[0]) == d["day"])
            if ids and not HAND_OFF_LINE.match(item):
                continue  # cites its day's hand-off, "(H1.2)", and is not one
            if ids:
                cites = {t for t in text if t != d["day"] and set(ids) & cited[t]}
                for t in named & set(ahead) - {d["day"]}:
                    out.append(dict(to=t, id=ids[0], source=f"Day {d['day']:02d} Notes", picked=t in cites, text=item))
                if not cites and not named & set(ahead) - {d["day"]}:
                    out.append(dict(to=None, id=ids[0], source=f"Day {d['day']:02d} Notes", picked=False, text=item))
                continue
            for t in named & set(ahead):
                picked = bool(({d["day"]} | named & finished) & days_named(text.get(t, "")))
                out.append(dict(to=t, source=f"Day {d['day']:02d} Notes", picked=picked, text=item))
    listed = run("git", "grep", "-n", "-I", "-E", r"\bDays? [0-9]+\b", sha, "--", ".", ":(exclude)*.md",
                 ":(exclude)docs/dashboard", ":(exclude)data")
    for line in listed.splitlines():
        path, number, code = line.split(":", 3)[1:]
        stem = os.path.basename(path)
        stem = os.path.splitext(stem)[0] if stem.endswith((".java", ".sql", ".ts", ".py")) else stem
        if COMMENT_LINE.match(code):
            named = days_named(code) & set(ahead)
            if named:
                named -= {written_by_day(sha, path, number)}
            for t in named:
                out.append(dict(to=t, source=f"{path}:{number}", picked=stem in text.get(t, ""), text=code))
    for h in out:
        h["text"] = excerpt(re.sub(r"^\s*(- |//|/\*+|\*|--|#|<!--)\s*", "", h["text"]), 160)
    return sorted(out, key=lambda h: (ahead.get(h["to"], len(order)), h["picked"], h["source"]))


def conclusion(notebook):
    """What the lab notebook says at the head of main: its phase reads, and its measurement table."""
    reads = [dict(phase=int(m.group(1)), text=m.group(0).strip()) for m in PHASE_READ.finditer(notebook)]
    rows = [[c.strip() for c in r.strip().strip("|").split("|")]
            for r in section(notebook, "What is being measured").splitlines() if r.startswith("|")]
    return dict(reads=reads, measured=[r for r in rows[2:] if len(r) == 2])


def token_usage():
    """Tokens per date, migration day and model, as scripts/token-usage.py last counted them."""
    path = os.path.join(PAGE, "token-usage.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def deployment():
    """The live checks of the one deployment (the project's own), or None if nothing was deployed."""
    path = os.path.join(PAGE, "deployment.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


EVIDENCE_SCHEMA = 1  # scripts/evidence.py's SCHEMA
EVIDENCE_STATUSES = ("passed", "failed", "error", "not_applicable")


def blob_id(sha, path):
    """The blob a commit holds at path, or None where it has none."""
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", f"{sha}:{path}"], capture_output=True, text=True)
    return r.stdout.strip() or None


def input_drift(record, head, path):
    """Why an input the report read is not path as main has it now, or None if it is the same file.
    Compared by blob, not by the report's hash: that hashes the working tree's bytes, CRLF on Windows."""
    state, rev = record.get("state"), record.get("revision")
    if state == "missing":
        return None if blob_id(head, path) is None else f"{path} was missing when checked"
    if state != "clean" or not rev:
        return f"{path} was {state} when checked"
    return None if blob_id(rev, record.get("path") or path) == blob_id(head, path) else f"{path} changed since {rev[:7]}"


def evidence_checks(days, snaps):
    """The committed report of scripts/evidence.py --json, read, never replayed: each finished day's
    checks counted by kind and status, current if checked at main's close and on main's spec and
    breaks file, stale if not. No report is "absent", one this cannot read "invalid": neither a pass."""
    path = os.path.join(PAGE, "evidence-checks.json")
    if not os.path.exists(path):
        return dict(state="absent")
    head, merged = snaps[-1]["sha"], {s["pr"]: s["sha"] for s in snaps if s["pr"]}
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        if report.get("schema") != EVIDENCE_SCHEMA:
            raise ValueError(f"schema {report.get('schema')!r}, this page reads {EVIDENCE_SCHEMA}")
        by_day = {}
        for d in report["days"]:
            for c in d["checks"]:
                if c["status"] not in EVIDENCE_STATUSES:
                    raise ValueError(f"{c['id']}: status {c['status']!r}")
            by_day[day_of(d["spec"]["path"])] = d
        out = []
        for d in (d for d in days if d["status"] in FINISHED):
            r = by_day.get(d["day"])
            if r is None:
                out.append(dict(day=d["day"], state="not checked"))
                continue
            close = max((p["number"] for p in d["prs"] if p["kind"] == "close"), default=None)
            close = merged.get(close)
            why = [] if close and (r.get("close_sha") or "").startswith(close) else [
                f"checked at close {(r.get('close_sha') or 'none')[:7]}, main's is {close or 'none'}"]
            why += [w for w in (input_drift(r.get("spec") or {}, head, d["file"]),
                                input_drift(r.get("breaks") or {}, head, f"specs/breaks/day-{d['day']:02d}.json")) if w]
            counts = {}
            for c in r["checks"]:
                counts.setdefault(c["kind"], dict.fromkeys(EVIDENCE_STATUSES, 0))[c["status"]] += 1
            out.append(dict(day=d["day"], state="stale" if why else "current", why=why, counts=counts,
                            checks=[{k: c.get(k) for k in ("criterion", "kind", "status", "label", "diagnostic")}
                                    for c in r["checks"]],
                            problems=[{k: c.get(k) for k in ("kind", "status", "label", "diagnostic")}
                                      for c in r["checks"] if c["status"] in ("failed", "error")]))
        return dict(state="loaded", generated=report["generated"], ref=report["ref"], ref_sha=report["ref_sha"],
                    runner=report["runner"], days=out)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return dict(state="invalid", diagnostic=f"{type(e).__name__}: {e}")


def criterion_index(days, reports, handoffs, snaps, blobs):
    """Join recorded evidence without treating an unavailable source as a negative result."""
    names = {t for d in days for c in d["evidence"] for t in evidence_test_names(c["block"])}
    states = test_state(names, snaps[-1]["sha"], blobs, {old: new for _, old, new in moves(snaps)})
    report_days = {d["day"]: d for d in reports.get("days", [])}
    rows, attention = [], []
    def flag(row, reason, detail):
        attention.append(dict(id=row["id"], reason=reason, detail=detail, day=row["day"]))

    for day in days:
        report = report_days.get(day["day"], {})
        for check in report.get("checks", []):
            if check.get("criterion") is None and check["status"] in ("failed", "error"):
                flag(dict(id=f"Day {day['day']:02d}", day=day["day"]), "replay_" + check["status"],
                     check.get("diagnostic") or check.get("label") or "")
        for c in day["evidence"]:
            if not c["id"]:
                continue
            block = c["block"]
            evidence_text = criterion_evidence(block)
            tests = evidence_test_names(block)
            command = re.search(r"`(?:git|python|diff|grep|bash) [^`]+`", evidence_text)
            measured = c["ticked"] and day.get("evidence_format", True)
            cited = bool(CITED_PR.search(evidence_text) and (tests or command)) if measured else None
            red_lines = [line.strip() for line in block.splitlines()
                         if broke(line) or re.search(r"\bseen[- ]red\b", line, re.I)]
            red = bool(red_lines) if measured else None
            found = all(states[t] != "missing" for t in tests) if tests else None
            state = report.get("state", reports["state"] if reports["state"] != "loaded" else "not checked")
            checks = [x for x in report.get("checks", []) if x.get("criterion") == c["id"]]
            replay = dict(state=state, status=[x["status"] for x in checks] or None,
                          why=report.get("why", []) + ([reports["diagnostic"]] if reports.get("diagnostic") else [])
                          + [x["diagnostic"] for x in checks if x.get("diagnostic")], checks=checks)
            track = next((name for name, work in day.get("track_work", {}).items()
                          if c["id"] in track_criteria(work)), None)
            if track is None and len(day.get("tracks", [])) == 1:
                track = day["tracks"][0]
            claim = re.split(r"\n\s*(?:Evidence|Test|broken|Replaces):", block, maxsplit=1, flags=re.I)[0]
            claim = re.sub(r"^- \[[ x]\]\s*\S+\s*(?:\*\*(?:new|hold)\*\*\s*[—-]\s*)?", "", claim)
            row = dict(id=c["id"], day=day["day"], phase=day.get("phase"), tag=c["kind"], claim=" ".join(claim.split()),
                       spec=day["file"] + "#acceptance-criteria", track=track,
                       prs=sorted({int(n) for n in CITED_PR.findall(evidence_text)}), tests=tests,
                       cited=cited, test_found=found, seen_red=dict(state=red, lines=red_lines), replay=replay,
                       replaces=[i for _, ids in replacement_lines(block) for i in ids], replaced_by=[])
            rows.append(row)
            for check in checks:
                if check["status"] in ("failed", "error"):
                    flag(row, "replay_" + check["status"], check.get("diagnostic") or check.get("label") or "")
            if state == 'stale':
                flag(row, "replay_stale", "; ".join(replay["why"]))
            if cited is False:
                flag(row, "evidence_missing", "No evidence and PR cited together")
            if day["status"] in FINISHED and state in ("absent", "invalid", "not checked"):
                flag(row, "evidence_missing", "Replay report: " + state)
            if red is False:
                flag(row, "not_seen_red", "No seen-red or broken line")
            if found is False:
                flag(row, "test_not_found", ", ".join(t for t in tests if states[t] == "missing"))
            for h in handoffs:
                if not h["picked"] and re.search(rf"\b{re.escape(c['id'])}\b", h["text"]):
                    flag(row, "handoff_unclaimed", h["text"])
    by_id = {r["id"]: r for r in rows}
    for row in rows:
        for target in row["replaces"]:
            if target in by_id:
                by_id[target]["replaced_by"].append(row["id"])
    severity = ("replay_failed", "replay_error", "replay_stale", "evidence_missing",
                "not_seen_red", "test_not_found", "handoff_unclaimed")
    attention.sort(key=lambda a: (severity.index(a["reason"]), a["day"], a["id"]))
    for item in attention:
        del item["day"]
    return rows, attention


def criterion_evidence(block):
    match = re.search(r"(?im)^\s*Evidence:\s*(.*(?:\n(?!\s*(?:broken:|Replaces:))\s+[^\n]+)*)", block)
    return match.group(1) if match else ""


def track_criteria(work):
    ids = []
    for match in re.finditer(r"C(\d+)\.(\d+)(?:\s*[–-]\s*C?\1\.(\d+))?", work):
        day, start, end = match.groups()
        ids.extend(f"C{day}.{n}" for n in range(int(start), int(end or start) + 1))
    return ids


def evidence_test_names(block):
    return sorted({c + ("." + m if m else "") for c, m in EVIDENCE_TEST.findall(criterion_evidence(block))})


INFRA_TRIES = 5


def infra_summary(run_id):
    """One infra workflow run's summary (scripts/infra-summary.py), or None if its artifact does not download."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run("gh", "run", "download", str(run_id), "-n", "infra-summary", "-D", tmp)
            with open(os.path.join(tmp, "infra-summary.json"), encoding="utf-8") as f:
                return json.load(f)
        except (subprocess.CalledProcessError, OSError, ValueError):
            return None


def infra(workflow):
    """The infra workflow's runs on main, and the summary of the newest completed run whose artifact
    downloads (artifacts expire, so an old run may have none). With no workflow, none: gh is not asked."""
    if not workflow:
        return dict(runs=[], latest=None)
    try:
        listed = json.loads(run("gh", "run", "list", "--workflow", workflow, "--branch", "main",
                                "--event", "push", "--limit", "30",
                                "--json", "databaseId,headSha,conclusion,createdAt,url"))
    except subprocess.CalledProcessError:
        return dict(runs=[], latest=None)
    runs = [dict(sha=r["headSha"][:7], conclusion=r["conclusion"], date=r["createdAt"][:10], url=r["url"])
            for r in listed]
    latest, tries = None, 0
    for i, r in enumerate(listed):
        if not r["conclusion"]:
            continue  # still running
        if tries == INFRA_TRIES:
            break
        tries += 1
        summary = infra_summary(r["databaseId"])
        if summary is not None:
            latest = dict(runs[i], summary=summary)
            break
    return dict(runs=runs, latest=latest)


def fill(page, data, name):
    # A PR title is written by whoever opens the PR; "</script>" in one must not end the data line.
    data = data.replace("</", "<\\/")
    page, n = re.subn(r"^window\.SPEC_DRIFT = .*;$", lambda _: f"window.SPEC_DRIFT = {data};", page, flags=re.M)
    if n != 1:
        raise SystemExit(f"{name}: expected one window.SPEC_DRIFT line, found {n}")
    return page


def build(data):
    """docs/dashboard/ as a single page: the stylesheet and script inlined, the data filled in."""
    def read(name):
        with open(os.path.join(PAGE, name), encoding="utf-8") as f:
            return f.read()
    page = read("index.html")
    for tag, name, wrap in (('<link rel="stylesheet" href="dashboard.css">', "dashboard.css", "style"),
                            ('<script src="dashboard.js"></script>', "dashboard.js", "script")):
        if page.count(tag) != 1:
            raise SystemExit(f"index.html: expected one {tag}")
        page = page.replace(tag, f"<{wrap}>\n{read(name)}</{wrap}>")
    return fill(page, data, "index.html")


def page_data(real, spec_days):
    """What --build writes: the real data, or with no day specs yet, demo-data.py's example data marked
    "demo": true, which the page shows under a banner. --out never uses this."""
    if spec_days:
        return real
    spec = importlib.util.spec_from_file_location("demo_data", os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo-data.py"))
    demo_data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo_data)
    return dict(demo_data.demo(), demo=True)


def unwritten(days, order):
    """The cleanup or close-out day before its spec exists: its number, and the phase of the day it
    follows in the run order, so the roadmap shows it where it runs."""
    which = next((p for p in (CLEANUP, CLOSEOUT) if p in order), None)
    if which is None:
        return None
    phase = {d["day"]: d.get("phase") for d in days}
    before = [d for d in order[:order.index(which)] if d not in PLACEHOLDERS]
    return dict(day=max(d["day"] for d in days) + 1,
                phase=phase.get(before[-1]) if before else None, title=f"The {which} day")


def unwritten_lines(plan):
    """The plan item each unwritten day comes from, by placeholder, read as run_order() reads it."""
    out = {}
    for line in plan_items(plan):
        if "numbered from" in line:
            out[PLATFORM] = line
        if "numbered after the last" in line:
            out[CLOSEOUT if "close-out" in line else CLEANUP] = line
    return out


def next_step(days, open_prs, order, stop, lines=None):
    """What to do next. An unwritten day quotes the plan item it comes from (`lines`, from unwritten_lines())."""
    if open_prs:
        return "Waiting on " + ", ".join(f"#{p['number']} {p['title']}" for p in open_prs)
    by_day = {d["day"]: d for d in days}
    left = [d for d in order if d in PLACEHOLDERS or d not in by_day or by_day[d]["status"] not in FINISHED]
    if not left:
        return "Every day is done." if days else "No day specs yet: fill in plan.md, then write the first day's spec (specs/README.md)."
    if left[0] in PLACEHOLDERS:
        line = (lines or {}).get(left[0], "")
        first = re.search(r"numbered from (\d+)", line)
        day = int(first.group(1)) if left[0] == PLATFORM and first else unwritten(days, order)["day"]
        return f'Day {day:02d}: write its day spec (plan.md: "{line}")'
    if stop is not None and order.index(left[0]) > order.index(stop):
        return f"Day {stop:02d} is done: stop and evaluate before going on (plan.md)"
    if left[0] not in by_day:
        return f"Day {left[0]:02d}: write its day spec (plan.md lists it, specs/ has none)"
    day = by_day[left[0]]
    if not any(p["kind"] == "spec" for p in day["prs"]):
        return f"Day {day['day']:02d}: read the spec against the code, then the spec-change PR"
    left = [t for t in day["tracks"] if t not in day["tracks_merged"]]
    if left:
        return f"Day {day['day']:02d} Track {left[0]}: {day['track_work'][left[0]]}"
    for t in day.get("tracks_unopened", []):
        return f"Day {day['day']:02d} Track {t['track']}: announced in #{t['pr']}, no branch names it"
    return f"Day {day['day']:02d}: the closing PR"


def provenance():
    """What made these numbers: the script's commit, the versions, and the draws behind each band."""
    dirty = bool(run("git", "status", "--porcelain", "--", os.path.abspath(__file__)).strip())
    head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], capture_output=True, text=True)
    return dict(commit=head.stdout.strip() or None, script_edited=dirty,  # None before the first commit
                python=platform.python_version(), numpy=np.__version__,
                chance_draws=CHANCE_DRAWS)


def owners_prs(listed, owner):
    """Only PRs the repository's owner opened. Anyone can open a PR on a public repository, and its
    body and title would reach the next step and an agent's context through this page (SO-12)."""
    return [p for p in listed if (p.get("author") or {}).get("login") == owner]


NO_REMOTE = "No GitHub remote: read from git only, so there are no PRs and the PR panels are empty."


def github():
    """The owner's PRs and the CI runs, and a note: with no GitHub remote, none of either and the
    note says so, so a new repository gets a page from git alone."""
    try:
        owner = run("gh", "repo", "view", "--json", "owner", "-q", ".owner.login").strip()
    except subprocess.CalledProcessError as e:
        print(f"{NO_REMOTE} (gh repo view: {(e.stderr or '').strip()})", file=sys.stderr)
        return [], [], NO_REMOTE
    listed = owners_prs(gh_list(PR_LIMIT, "pr", "list", "--state", "all", "--json",
                                "number,title,headRefName,state,url,body,author"), owner)
    runs = gh_list(RUN_LIMIT, "run", "list", "--json", "headBranch,headSha,conclusion,event,workflowName")
    return listed, runs, None


def project_name():
    """The repository's name on GitHub; with no remote, its folder's."""
    try:
        return run("gh", "repo", "view", "--json", "name", "-q", ".name").strip()
    except subprocess.CalledProcessError:
        return os.path.basename(run("git", "rev-parse", "--show-toplevel").strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--out", default="spec-drift.json")
    out.add_argument("--build", help="write docs/dashboard/ here as one page, with the data inlined")
    ap.add_argument("--cache", default=CACHE_FILE, help="what earlier runs read from git (default: %(default)s)")
    ap.add_argument("--no-cache", action="store_true", help="read everything from git, and keep nothing")
    args = ap.parse_args()
    if not args.no_cache:
        load_cache(args.cache)
    listed, runs, note = github()
    prs = {p["number"]: p for p in listed}
    open_prs = sorted((p for p in listed if p["state"] == "OPEN"), key=lambda p: p["number"])
    with Blobs() as blobs:
        snaps = snapshots(prs)
        head = snaps[-1]["sha"]
        spec_days = [d for d in map(day_of, run("git", "ls-tree", "--name-only", head, "specs/").split()) if d]
        plan = blobs.read(head, "plan.md")
        rows, files, vocab, lines = trajectory(snaps, blobs, run_order(plan, spec_days)[0])
        days, order, stop = roadmap(snaps, prs, blobs)
        evidence_history(rows, days, snaps, blobs)
        boundaries(rows, snaps, blobs)
        handoffs = hand_offs(days, order, head, blobs)
        checks = evidence_checks(days, snaps)
        index, attention = criterion_index(days, checks, handoffs, snaps, blobs)
        now = dict(generated=datetime.datetime.now().astimezone().isoformat(timespec="minutes"),
                   head=snaps[-1]["sha"], last_pr=snaps[-1]["pr"],
                   current_day=next((d["day"] for d in days if d["status"] == "active"), None),
                   next_step=next_step(days, open_prs, order, stop, unwritten_lines(plan)), stop_after=stop,
                   unwritten=unwritten(days, order),
                   open_prs=[dict(number=p["number"], title=p["title"], url=p["url"]) for p in open_prs])
        real = dict(now=now, rows=rows, files=files, days=days, origin_lines=lines,
                    order=[d for d in order if d not in PLACEHOLDERS],
                    out_of_order=out_of_order(order),
                    removals=removal_history(days, snaps, blobs),
                    hand_offs=handoffs, criteria_index=index, attention=attention,
                    other_groups=other_groups(rows),
                    vocab_size=vocab, tokens=token_usage(),
                    verification=verification(prs, runs),
                    infra=infra(INFRA_WORKFLOW),
                    deployment=deployment(),
                    evidence_checks=checks,
                    conclusion=conclusion(blobs.read(snaps[-1]["sha"], "docs/lab-notebook.md")),
                    meta=dict(caveats=CAVEATS + [note] * bool(note), provenance=provenance(),
                              break_line_from_pr=BREAK_LINE_FROM_PR, project=project_name(),
                              phases=phases(plan), stops=stops(plan)))
    payload = page_data(real, spec_days) if args.build else real
    data = json.dumps(payload, separators=(",", ":"))
    target = args.build or args.out
    if args.build:
        data = build(data)
    with open(target, "w", encoding="utf-8") as f:
        f.write(data)
    if not args.no_cache:
        save_cache(args.cache)
    print(f"{target}: {len(rows)} snapshots, {len(days)} days, next: {now['next_step']}" + (f" ({note})" if note else "")
          + (" (example data: no day specs yet)" if payload.get("demo") else ""))


if __name__ == "__main__":
    main()
