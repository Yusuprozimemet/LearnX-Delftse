"""A Read of a whole large file is sent back once to read the part it needs.

    python scripts/read-guard.py     # PreToolUse on Read: exits 2 with the reason, or 0

The hook in the project settings (.claude/settings.json) runs it before every Read. A Read stays in
the context and every later reply re-reads it: before this hook, 16 whole-file Reads over 8,000
characters put 164k characters into the kit's main sessions, while CLAUDE.md already said to read
the part a step needs. A rule the model can skip was skipped; this one it cannot.

So a Read with no offset, no limit and no pages, of a file over 8,000 bytes, is refused, and the
reason names the file's size and line count: find the part with grep -n and read it with
offset/limit, or give a limit to read it all on purpose. Edit works after a ranged Read, even on
lines outside the range (tried in the session that wrote this). Images, PDFs and notebooks pass:
they are not read as lines. A path that is not a file passes, so Read reports it.

The settings' command finds this script through CLAUDE_PROJECT_DIR, read in Python, and exits 0
without it: VS Code Copilot reads the same settings, runs the hook for every tool and sets no such
variable (see auditor-guard.py).
"""
import json
import os
import sys

LIMIT = 8000
WHOLE = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf", ".ipynb")


def refusal(data):
    """The reason to refuse this Read, or None to let it run."""
    args = data.get("tool_input") if data.get("tool_name") == "Read" else None
    if not isinstance(args, dict) or any(args.get(k) is not None for k in ("offset", "limit", "pages")):
        return None
    path = args.get("file_path") or ""
    if path.lower().endswith(WHOLE):
        return None
    try:
        size = os.path.getsize(path)
        if size <= LIMIT or not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            lines = f.read().count(b"\n")
    except OSError:
        return None
    return (f"read-guard: {path} is {size:,} bytes, {lines:,} lines. Read the part you need: find it "
            f"with grep -n, then Read with offset and limit. To read all of it, give limit={lines + 1}.")


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = refusal(data)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
