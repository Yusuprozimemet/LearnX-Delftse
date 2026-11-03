"""A long Bash result keeps its first and last 3,000 characters in the context; the rest is saved.

    python scripts/cap-output.py     # PostToolUse on Bash: prints the replacement output, if any

The hook in the project settings (.claude/settings.json) runs it after every Bash command. Bash
output is most of what the 2k-cap saving in token-usage.py --simulate would cut, since every later
reply re-reads it. So a stdout of more than 8,000 characters
is cut to its head and tail, with one line between them that names the saved file. That file is
the full stdout, written to .claude/tool-output/ (gitignored) before anything is cut.

Four results pass unchanged: one of 8,000 characters or fewer; one of Claude Code's own limit or
more (BASH_MAX_OUTPUT_LENGTH, 30,000 by default), since Claude Code has already saved that output in
full and shows a 2 KB preview, and the hook receives only its first 30,000 characters, so its copy
would not be the full output; one with a separate stderr or an interruption, since a failing
command's error often ends its output and the model needs all of it; and one whose full output
cannot be saved, since a cut without the file would lose the middle for good.

Live, Claude Code's Bash merges stderr into stdout: a failing command comes back with stderr ''
and its error at the end of stdout. So the stderr guard does not fire on it, and its output is cut
like any other. The tail keeps the last 3,000 characters, where the error is. Only an interruption
is left whole.

Bash's tool_response has no exit code, so a separate stderr and `interrupted` stand in for failure. The reply
keeps Bash's own object (stdout, stderr, interrupted, ...) with only stdout replaced: a plain string
in updatedToolOutput is silently ignored.

The settings' command finds this script through CLAUDE_PROJECT_DIR, read in Python, and exits 0
without it: VS Code Copilot reads the same settings, runs the hook for every tool and sets no such
variable (see auditor-guard.py).
"""
import json
import os
import sys
import uuid

LIMIT = 8000
KEEP = 3000
SAVED = os.path.join(".claude", "tool-output")


def ceiling():
    try:
        return int(os.environ.get("BASH_MAX_OUTPUT_LENGTH") or 30000)
    except ValueError:
        return 30000


def cut(stdout, path):
    note = f"[cut {len(stdout) - 2 * KEEP} chars; full output: {path}; use Read with offset/limit or grep]"
    return f"{stdout[:KEEP]}\n{note}\n{stdout[-KEEP:]}"


def handle(data, project):
    resp = data.get("tool_response")
    if not isinstance(resp, dict):
        return None
    stdout = resp.get("stdout") or ""
    if not LIMIT < len(stdout) < ceiling() or resp.get("stderr") or resp.get("interrupted"):
        return None
    folder = os.path.join(project, SAVED)
    path = os.path.abspath(os.path.join(folder, uuid.uuid4().hex + ".txt"))
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as f:
            f.write(stdout.encode("utf-8", "replace"))
    except OSError:
        return None
    text = cut(stdout, path)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": dict(resp, stdout=text)}}


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project:
        return
    out = handle(data, project)
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    main()
