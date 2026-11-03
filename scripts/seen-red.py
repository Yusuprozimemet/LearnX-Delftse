"""Run the branch's tests on the code as it was before the day, and print the `seen red:` line.

    python scripts/seen-red.py [--base main] -- <command> [args...]

A `new` check must fail on the code before the change (specs/README.md). This makes that run
mechanical: it checks the base out into a scratch git worktree, copies in every file under tests/
that differs from the base in the working tree (committed or not, untracked included), and runs the
command there, as an argument list with no shell. Nothing in the working tree is touched.

It exits 0 only when the command failed there, and prints the record for the PR:
`seen red: <command> on <base> → <what it reported>`. A command that passes on the base exits 1:
the tests do not see the change. Read the reported line: `errors=` alone may be an import error,
not the behaviour the test asserts.

The step was a Note, not a script, and did not carry: a pilot of the kit merged two tracks whose new
tests were never seen red on the code before them.
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("break_", os.path.join(HERE, "break.py"))
break_ = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(break_)
TESTS = "tests"


def git(*args, cwd="."):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def changed_tests(base):
    """The files under tests/ that differ from base in the working tree, deletions left out."""
    tracked = git("diff", "--name-only", "--diff-filter=d", base, "--", TESTS).split()
    untracked = git("ls-files", "--others", "--exclude-standard", "--", TESTS).split()
    return sorted(set(tracked + untracked))


def on_base(base, commands, files):
    """Run each command, in order, in one scratch worktree of base with files copied in from the
    working tree; their results. Run from the repository root. scripts/track-red.py calls it too."""
    scratch = tempfile.mkdtemp()
    tree = os.path.join(scratch, "base")
    try:
        git("worktree", "add", "--detach", tree, base)
        for path in files:
            os.makedirs(os.path.dirname(os.path.join(tree, path)), exist_ok=True)
            shutil.copyfile(path, os.path.join(tree, path))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return [subprocess.run(break_.argv_for(command, tree), cwd=tree, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace") for command in commands]
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tree], capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)


def refuse(message):
    print(f"seen-red.py: {message}", file=sys.stderr)
    return 2


def main(args=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", default="main")
    p.add_argument("command", nargs="+")
    a = p.parse_args(args)
    sys.stdout.reconfigure(encoding="utf-8")  # the arrow; a Windows console's default is cp1252

    root = git("rev-parse", "--show-toplevel").strip()
    os.chdir(root)
    try:
        files = changed_tests(a.base)
    except subprocess.CalledProcessError as e:
        return refuse(f"cannot compare with {a.base}: {e.stderr.strip()}")
    if not files:
        return refuse(f"no file under {TESTS}/ differs from {a.base}: there is no new test to see red")
    try:
        [run] = on_base(a.base, [a.command], files)
    except FileNotFoundError as e:
        return refuse(f"the command did not start: {e}")
    output = run.stdout + run.stderr
    command = " ".join(a.command)
    if run.returncode == 0:
        print(f"The check passed on {a.base}: it does not see the change.\n  last line: {break_.reported(output)}")
        return 1
    print(f"seen red: {command} on {a.base} → {break_.reported(output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
