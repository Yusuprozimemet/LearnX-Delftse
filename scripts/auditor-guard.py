"""The auditors only read: a hook in the project settings (.claude/settings.json) makes it so.

    python scripts/auditor-guard.py pre     # PreToolUse on Bash: exit 2 blocks a writing command
    python scripts/auditor-guard.py stop    # SubagentStop: exit 2 if the repository changed

The hooks run for every Bash command and every subagent's stop; the guard acts only when the
hook's `agent_type` is one of AUDITORS, the five agents whose brief says "Read-only" and that have
Bash, and lets the main session and every other agent through. Hooks in an agent's frontmatter
were the first wiring: the terminal's Claude Code 2.1.170 ran them, the VS Code extension's
2.1.312 ignored them. Settings hooks run on both, inside subagents.

Each brief said "Read-only", and all but the defect-rater had Bash to break it with; one auditor
ran `git checkout main` in a replay clone. `pre` blocks, wherever it points, a git command that
changes a repository (checkout, commit, stash, a new branch, fetch, ...) and a gh command that is
not a read (anything but list, view, status, diff, checks, and `gh api` GETs). It reads the
command, not what a program does: `python -c` with `open(..., "w")` or `subprocess` gets through.
So `stop` compares the repository with what it was before the auditor's first command: HEAD, the
branch, every ref, the stash and `git status`. A change sends the auditor back to put it back and
say so; a second stop lets it end, with the change named to the main session.

The settings' command finds this script through CLAUDE_PROJECT_DIR, read in Python so that no
shell has to expand it, and exits 0 without it: VS Code Copilot reads the same settings, runs the
hook for every tool and sets no such variable, and a script path that resolved to
/scripts/auditor-guard.py exited 2 and denied all of its tool calls. Claude Code always sets it.

The repository is CLAUDE_PROJECT_DIR, else the hook's `cwd`. The snapshot is a file per agent in
the system's temp directory, removed at the stop.
"""
import json
import os
import shlex
import subprocess
import sys
import tempfile

GIT_WRITES = {
    "add", "am", "apply", "branch", "checkout", "cherry-pick", "clean", "commit", "config",
    "fetch", "filter-branch", "gc", "init", "merge", "mv", "notes", "prune", "pull", "push",
    "rebase", "remote", "repack", "replace", "reset", "restore", "revert", "rm", "sparse-checkout",
    "stash", "submodule", "switch", "tag", "update-ref", "worktree",
}
GIT_READ_FORMS = {  # the subcommand's first argument that makes it a read; "" for none at all
    "notes": {"list", "show"}, "remote": {"", "-v", "show", "get-url"},
    "stash": {"list", "show"}, "submodule": {"", "status"}, "worktree": {"list"},
}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
LISTING_WRITE_FLAGS = {  # `git branch` and `git tag` list unless given a name or one of these
    "branch": {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "-u", "--delete", "--move", "--copy",
               "--force", "--set-upstream-to", "--unset-upstream", "--edit-description"},
    "tag": {"-d", "-f", "-a", "-s", "-u", "-m", "-F", "--delete", "--force", "--annotate",
            "--sign", "--local-user", "--message", "--file"},
}
CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
GH_READ_VERBS = {"list", "view", "status", "diff", "checks", "ls"}
GH_FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field", "--input"}
WRAPPERS = {"env", "command", "time", "nohup", "xargs", "sudo", "exec", "then", "do", "else",
            "if", "while", "until", "!", "{", "}"}
SHELLS = {"bash", "sh", "zsh", "powershell", "pwsh"}
AUDITORS = {"plan-auditor", "spec-auditor", "security-ops-auditor", "agentic-engineering-auditor",
            "architecture-auditor"}
SEPARATORS = {";", "|", "||", "&", "&&", "(", ")", ">", ">>", "<", "<<", "|&", ";;"}


def _tokens(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # an unbalanced quote: the words as they stand
        return line.split()


def _segments(command):
    """Each simple command, as tokens, split at newlines and shell operators."""
    for line in command.splitlines():
        segment = []
        for token in _tokens(line):
            if token in SEPARATORS:
                if segment:
                    yield segment
                segment = []
            else:
                segment.append(token)
        if segment:
            yield segment


def _program(token):
    name = os.path.basename(token.lstrip("`$(").replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name


def _git(args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_OPTIONS_WITH_VALUE else 1
    if i >= len(args) or args[i] not in GIT_WRITES:
        return None
    sub, rest = args[i], args[i + 1:]
    if sub in GIT_READ_FORMS and (rest[0] if rest else "") in GIT_READ_FORMS[sub]:
        return None
    if sub in ("branch", "tag"):
        named = rest and not rest[0].startswith("-")
        if not named and not any(a.split("=")[0] in LISTING_WRITE_FLAGS[sub] for a in rest):
            return None
    if sub == "config":
        positional = [a for a in rest if not a.startswith("-")]
        if CONFIG_READ_FLAGS & set(rest) or (len(positional) == 1 and len(rest) == 1):
            return None
    return f"git {sub} changes a repository"


def _gh(args):
    if not args or args[0] in ("--version", "help", "search"):
        return None
    if args[0] == "api":
        method = None
        for i, a in enumerate(args):
            if a in ("-X", "--method") and i + 1 < len(args):
                method = args[i + 1]
            elif a.startswith("--method="):
                method = a.split("=", 1)[1]
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:]
        if method is None:
            method = "POST" if any(a.split("=")[0] in GH_FIELD_FLAGS for a in args) else "GET"
        return None if method.upper() == "GET" else f"gh api {method.upper()} writes to GitHub"
    verb = args[1] if len(args) > 1 else ""
    if verb in GH_READ_VERBS or (args[0] == "auth" and verb == "status"):
        return None
    return f"gh {args[0]} {verb} is not a read".replace("  ", " ")


def blocked(command):
    """Why `command` writes to a repository or GitHub, or None if it only reads."""
    for segment in _segments(command):
        i = 0
        while i < len(segment) and (_program(segment[i]) in WRAPPERS or "=" in segment[i].split("/")[0]
                                    or (i and segment[i].startswith("-"))):
            i += 1
        if i >= len(segment):
            continue
        program, args = _program(segment[i]), segment[i + 1:]
        reason = _git(args) if program == "git" else _gh(args) if program == "gh" else None
        if program in SHELLS or program == "eval":
            for j, arg in enumerate(args):
                if program == "eval" or (j and args[j - 1] in ("-c", "-Command")):
                    reason = reason or blocked(arg)
        if reason:
            return reason
    return None


def _git_out(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout


def state(repo):
    """What an auditor must leave as it found it."""
    return {
        "HEAD": _git_out(repo, "rev-parse", "HEAD").strip(),
        "branch": _git_out(repo, "symbolic-ref", "-q", "--short", "HEAD").strip(),
        "refs": _git_out(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines(),
        "stash": _git_out(repo, "stash", "list").splitlines(),
        "status": _git_out(repo, "status", "--porcelain").splitlines(),
    }


def changes(before, after):
    lines = []
    for key in before:
        if before[key] == after[key]:
            continue
        if isinstance(before[key], str):
            lines.append(f"{key}: {before[key] or '(none)'} -> {after[key] or '(none)'}")
        else:
            gone = [x for x in before[key] if x not in after[key]]
            new = [x for x in after[key] if x not in before[key]]
            lines.append(f"{key}: " + "; ".join([f"+ {x}" for x in new] + [f"- {x}" for x in gone]))
    return lines


def run(mode, data, repo, store):
    """The exit code and the message for one hook call."""
    if data.get("agent_type") not in AUDITORS:
        return 0, ""
    snapshot = os.path.join(store, f"{data.get('agent_id') or 'main'}.json")
    if mode == "pre":
        reason = blocked((data.get("tool_input") or {}).get("command", ""))
        if reason:
            return 2, (f"auditor-guard: blocked, {reason}. Auditors only read; report what you "
                       "would have run instead.")
        if not os.path.exists(snapshot):
            os.makedirs(store, exist_ok=True)
            with open(snapshot, "w", encoding="utf-8") as f:
                json.dump(state(repo), f)
        return 0, ""
    if not os.path.exists(snapshot):
        return 0, ""
    with open(snapshot, encoding="utf-8") as f:
        found = changes(json.load(f), state(repo))
    if not found:
        os.remove(snapshot)
        return 0, ""
    text = f"auditor-guard: {repo} changed during the audit:\n" + "\n".join(found)
    if not data.get("stop_hook_active"):
        return 2, text + "\nPut it back as it was, then say in your report what you changed."
    os.remove(snapshot)
    return 0, json.dumps({"systemMessage": text})


def main():
    data = json.load(sys.stdin)
    repo = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    code, message = run(sys.argv[1], data, repo, os.path.join(tempfile.gettempdir(), "auditor-guard"))
    if message:
        print(message, file=sys.stderr if code else sys.stdout)
    sys.exit(code)


if __name__ == "__main__":
    main()
