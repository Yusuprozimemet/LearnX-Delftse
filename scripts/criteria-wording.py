"""Keep accepted day criteria unchanged outside day-NN/spec-<slug> PRs."""
import argparse
import fnmatch
import importlib.util
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("track_red", os.path.join(HERE, "track-red.py"))
track_red = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(track_red)
git = track_red.git
SPEC_BRANCH = re.compile(r"day-\d{2}/spec-[0-9A-Za-z][0-9A-Za-z-]*")


def statements(text):
    text = "\n".join(text.splitlines())
    body = track_red.section(text, "Acceptance criteria")
    starts = list(track_red.CRITERION.finditer(body))
    found = {}
    for match, nxt in zip(starts, starts[1:] + [None]):
        block = body[match.end():nxt.start() if nxt else len(body)]
        statement = re.split(r"Evidence:|broken:", block, maxsplit=1)[0]
        found[match.group(1)] = " ".join(statement.split())
    return found


def compare(before, after):
    """One diagnostic per changed ID; checkbox and evidence are not statements."""
    old, new = track_red.criteria(before), track_red.criteria(after)
    old_text, new_text = statements(before), statements(after)
    changes = []
    for cid in sorted(old.keys() | new.keys()):
        if cid not in old:
            changes.append(f"{cid}: added")
        elif cid not in new:
            changes.append(f"{cid}: removed")
        else:
            fields = []
            if old[cid][0] != new[cid][0]:
                fields.append("tag")
            if old_text[cid] != new_text[cid]:
                fields.append("statement")
            if fields:
                changes.append(f"{cid}: {', '.join(fields)} changed")
    return changes


def check(base, head):
    def specs(ref):
        return {p for p in git("ls-tree", "-r", "--name-only", ref, "--", "specs").splitlines()
                if fnmatch.fnmatchcase(p, "specs/day-*.md") and p.count("/") == 1}

    old, new = specs(base), specs(head)
    touched = set(git("diff", "--name-only", "--no-renames", base, head, "--", "specs").splitlines())
    paths = sorted((old | new) & touched)
    count = 0
    for path in paths:
        if path not in old or path not in new:
            problems = ["spec added" if path not in old else "spec deleted"]
        else:
            before, after = (git("show", f"{ref}:{path}") for ref in (base, head))
            problems = compare(before, after)
            problems += [f"cannot read criteria: {p}" for p in track_red.unread(after)]
        for problem in problems:
            print(f"{path}: {problem}")
        count += len(problems)
    if not paths:
        print("no day spec was touched")
    print(f"{count} criterion/spec differences")
    return int(count != 0)


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--branch", required=True)
    args = parser.parse_args(args)
    if SPEC_BRANCH.fullmatch(args.branch):
        print(f"{args.branch}: spec-change PR exempt")
        return 0
    try:
        return check(args.base, args.head)
    except subprocess.CalledProcessError as error:
        parser.exit(2, f"cannot read revisions: {error.stderr}\n")


if __name__ == "__main__":
    raise SystemExit(main())
