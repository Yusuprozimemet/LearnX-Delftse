"""Fail a pull request that edits what the project must not change, and name each file.

The frozen files are the project's safety net: applied migrations, which are never changed once
they have run, and the tests that prove the behaviour a change has to keep. The project file,
spec-drift.config.json at the root, lists them as regular expressions over the path (`frozen`),
read as the base has it, so a pull request cannot unfreeze what it edits; none, and nothing is
frozen. Additions and pure renames within the frozen set pass, so a move stays possible; a
modification, a deletion, a type change, a rename with an edit or a rename to a path no pattern
matches fails. Reads the same three-dot diff as the size check in pr-checks.yml.

The project file's `test_count` rule, also read at the base, counts the tests without running
them: the lines matching `test` in the files whose path matches `files`. The count at head must
not fall below the count where the branch left the base, so a move that hides a test from
discovery, or a deleted test, fails too. No rule, and nothing is counted.

    python scripts/invariants.py <base> <head>
"""
import json
import re
import subprocess
import sys

CONFIG = "spec-drift.config.json"
WORDS = {"M": "modified", "D": "deleted", "T": "type changed"}


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")


def base_config(base):
    """The project file at base; empty with no file."""
    shown = git("show", f"{base}:{CONFIG}")
    return {} if shown.returncode else json.loads(shown.stdout)


def frozen_patterns(config):
    """The project file's `frozen` patterns; none with no field."""
    return tuple(re.compile(p) for p in config.get("frozen", []))


def test_count(rev, rule):
    """How many lines matching the rule's `test` the files matching its `files` hold at rev."""
    files, test = re.compile(rule["files"]), re.compile(rule["test"], re.M)
    paths = git("ls-tree", "-r", "--name-only", rev).stdout.splitlines()
    return sum(len(test.findall(git("show", f"{rev}:{p}").stdout)) for p in paths if files.search(p))


def protected(path, patterns):
    return any(p.search(path) for p in patterns)


def entries(name_status_lines):
    """(code, status, similarity, old path, new path) per line of `git diff --name-status -M`."""
    for line in name_status_lines:
        if not line.strip():
            continue
        fields = line.split("\t")
        code = fields[0]
        new = fields[2] if len(fields) > 2 else fields[1]
        yield code, code[0], int(code[1:] or 100), fields[1], new


def violations(name_status_lines, patterns):
    """One line per protected file that is modified, deleted, retyped, renamed with an edit or
    renamed out of the frozen set."""
    found = []
    for code, status, _, old, new in entries(name_status_lines):
        if status == "R" and protected(old, patterns) and not protected(new, patterns):
            found.append(f"renamed out of the frozen set ({code}): {old} -> {new}")
        elif status == "R" and code != "R100" and protected(old, patterns):
            found.append(f"renamed with an edit ({code}): {old} -> {new}")
        elif status in WORDS and protected(old, patterns):
            found.append(f"{WORDS[status]}: {old}")
    return found


def passed(name_status_lines, patterns):
    """How many protected files the diff adds or moves as they are: the count the OK line prints."""
    count = 0
    for code, status, _, old, new in entries(name_status_lines):
        if status == "A" or status == "C" or code == "R100":
            count += protected(old, patterns) or protected(new, patterns)
    return count


def main(argv):
    if len(argv) != 2:
        print("usage: python scripts/invariants.py <base> <head>", file=sys.stderr)
        return 2
    base, head = argv
    diff = git("diff", "--name-status", "-M", "-l0", f"{base}...{head}")
    if diff.returncode:
        print(diff.stderr, file=sys.stderr)
        return 2
    config = base_config(base)
    patterns, rule = frozen_patterns(config), config.get("test_count")
    if not patterns and not rule:
        print(f"Nothing frozen: {CONFIG} sets no `frozen` patterns.")
        return 0
    lines = diff.stdout.splitlines()
    found = violations(lines, patterns)
    counted = ""
    if rule:
        # Where the branch left the base, as the three-dot diff does: tests the base gained since are not the branch's.
        fork = git("merge-base", base, head).stdout.strip()
        before, after = test_count(fork, rule), test_count(head, rule)
        counted = f", {before} tests before and {after} after"
        if after < before:
            found.append(f"test count dropped: {before} where the branch left the base, {after} at head")
    if found:
        for line in found:
            print(line)
        print("These files are frozen: add a new migration instead of editing an applied one. "
              "A frozen test edit, a move out of the frozen set or a lost test means stop and ask (see CLAUDE.md).")
        return 1
    print(f"OK: no frozen file was modified, deleted or renamed with an edit "
          f"({passed(lines, patterns)} frozen files added or moved unchanged{counted}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
