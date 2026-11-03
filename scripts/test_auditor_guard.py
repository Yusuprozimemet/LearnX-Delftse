"""scripts/auditor-guard.py blocks an auditor's writing commands and finds what got past it. The
command tests read command text; the stop tests change a scratch repository between the auditor's
first command and its stop; the settings tests check that .claude/settings.json wires both hooks,
that the guard acts for the five auditors, and that it lets the main session and other agents by,
and run the settings' commands through the shell with and without CLAUDE_PROJECT_DIR.

    python scripts/test_auditor_guard.py
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "auditor-guard.py")
AGENTS = os.path.join(HERE, os.pardir, ".claude", "agents")
SETTINGS = os.path.join(HERE, os.pardir, ".claude", "settings.json")
_spec = importlib.util.spec_from_file_location("auditor_guard", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WRITES = [
    "git checkout main", 'git -C "/tmp/a replay" switch -c x', "cd /tmp/replay && git stash",
    'bash -c "git reset --hard HEAD~1"', "git branch new-branch", "git branch -D old", "git tag v1",
    "git config user.name x", "git fetch origin", "git commit -m x", "env FOO=1 git push",
    "echo hi\ngit restore README.md", "gh pr comment 45 --body hi", "gh pr merge 45",
    "gh pr checkout 3", "gh issue create -t x", "gh api -X POST repos/o/r/issues",
    "gh api repos/o/r/issues -f title=t", "gh api --method=PATCH repos/o/r",
]
READS = [
    "git log --oneline -5", "git -C /tmp/x show HEAD:plan.md", "git status --short",
    "git diff --stat main...HEAD", "git branch -a", "git branch --show-current", "git tag -l",
    "git stash list", "git worktree list", "git remote -v", "git config --get user.name",
    "git clone -q . /tmp/replay", "gh pr list --state merged --limit 400", "gh pr view 45",
    "gh auth status", "gh api repos/o/r/branches/main/protection",
    "gh api -X GET repos/o/r/pulls -f state=all", 'grep -rn "git checkout" .claude',
    "python -m unittest discover -s tests -t .", "python scripts/spec-drift.py --out /tmp/d.json",
]


class CommandTest(unittest.TestCase):
    def test_blocks_a_command_that_writes(self):
        for command in WRITES:
            with self.subTest(command=command):
                self.assertIsNotNone(guard.blocked(command))

    def test_lets_a_read_through(self):
        for command in READS:
            with self.subTest(command=command):
                self.assertIsNone(guard.blocked(command))

    def test_the_hook_exits_2_with_the_reason(self):
        def hook(command):
            data = {"hook_event_name": "PreToolUse", "agent_id": "t", "agent_type": "spec-auditor",
                    "tool_name": "Bash", "tool_input": {"command": command}}
            env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo, TMP=self.repo, TEMP=self.repo,
                       TMPDIR=self.repo)  # the snapshot lands in the scratch directory
            return subprocess.run([sys.executable, SCRIPT, "pre"], input=json.dumps(data),
                                  capture_output=True, text=True, env=env)
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        blocked = hook("git checkout main")
        self.assertEqual((blocked.returncode, "git checkout" in blocked.stderr), (2, True))
        self.assertEqual(hook("git log -1").returncode, 0)


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


class StopTest(unittest.TestCase):
    def setUp(self):
        self.repo, self.store = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.addCleanup(shutil.rmtree, self.store, True)
        _git(self.repo, "init", "-q", "-b", "main")
        with open(os.path.join(self.repo, "a.txt"), "wb") as f:
            f.write(b"a\n")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "a")

    def audit(self, change, stop_hook_active=False):
        data = {"agent_id": "t", "agent_type": "plan-auditor", "tool_input": {"command": "git log"}}
        self.assertEqual(guard.run("pre", data, self.repo, self.store), (0, ""))
        change()
        return guard.run("stop", dict(data, stop_hook_active=stop_hook_active), self.repo, self.store)

    def write(self, name, text=b"x\n"):
        with open(os.path.join(self.repo, name), "wb") as f:
            f.write(text)

    def test_a_change_sends_the_auditor_back(self):
        cases = {
            "branch": lambda: _git(self.repo, "checkout", "-q", "-b", "other"),
            "status": lambda: self.write("a.txt"),
            "refs": lambda: _git(self.repo, "branch", "kept"),
            "stash": lambda: (self.write("a.txt"), _git(self.repo, "stash", "-q")),
            "HEAD": lambda: _git(self.repo, "commit", "-q", "--allow-empty", "-m", "b"),
        }
        for key, change in cases.items():
            with self.subTest(changed=key):
                self.setUp()
                code, message = self.audit(change)
                self.assertEqual(code, 2)
                self.assertIn(f"\n{key}: ", message)

    def test_a_scratch_file_left_behind_is_a_change(self):
        code, message = self.audit(lambda: self.write("scratch_test.py"))
        self.assertEqual((code, "?? scratch_test.py" in message), (2, True))

    def test_the_second_stop_ends_and_tells_the_main_session(self):
        self.audit(lambda: self.write("a.txt"))
        code, message = guard.run("stop", {"agent_id": "t", "agent_type": "plan-auditor",
                                           "stop_hook_active": True}, self.repo, self.store)
        self.assertEqual(code, 0)
        self.assertIn("a.txt", json.loads(message)["systemMessage"])
        self.assertEqual(os.listdir(self.store), [])

    def test_an_untouched_repository_stops_clean(self):
        self.assertEqual(self.audit(lambda: None), (0, ""))
        self.assertEqual(os.listdir(self.store), [])

    def test_no_command_run_no_snapshot_stops_clean(self):
        data = {"agent_id": "t", "agent_type": "plan-auditor"}
        self.assertEqual(guard.run("stop", data, self.repo, self.store), (0, ""))


class SettingsTest(unittest.TestCase):
    def test_the_settings_wire_both_hooks(self):
        with open(SETTINGS, encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        def commands(event, matcher=None):
            return [h["command"] for entry in hooks.get(event, []) if entry.get("matcher") == matcher
                    for h in entry["hooks"]]
        self.assertRegex(" ".join(commands("PreToolUse", "Bash")), r'auditor-guard\.py.*" pre$')
        self.assertRegex(" ".join(commands("SubagentStop")), r'auditor-guard\.py.*" stop$')

    def hook(self, event, data, project_dir):
        """Run the settings' command for one event through the shell, from a scratch directory."""
        with open(SETTINGS, encoding="utf-8") as f:
            command = json.load(f)["hooks"][event][0]["hooks"][0]["command"]
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, True)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        env.update(TMP=scratch, TEMP=scratch, TMPDIR=scratch)
        if project_dir is not None:
            env["CLAUDE_PROJECT_DIR"] = project_dir
        command = command.replace("python ", f'"{sys.executable}" ', 1)
        return subprocess.run(command, shell=True, cwd=scratch, env=env, input=json.dumps(data),
                              capture_output=True, text=True)

    def test_the_hooks_pass_outside_claude_code(self):
        # VS Code Copilot reads these settings, ignores the matcher, and sets no CLAUDE_PROJECT_DIR
        data = {"toolName": "run_in_terminal", "toolArgs": {"command": "git status"}}
        for event in ("PreToolUse", "SubagentStop"):
            with self.subTest(event=event):
                self.assertEqual(self.hook(event, data, None).returncode, 0)

    def test_the_hooks_still_block_an_auditor_in_claude_code(self):
        data = {"agent_type": "spec-auditor", "tool_input": {"command": "git checkout main"}}
        done = self.hook("PreToolUse", data, os.path.dirname(HERE))
        self.assertEqual(done.returncode, 2)
        self.assertIn("auditor-guard: blocked", done.stderr)
        empty = tempfile.mkdtemp()  # a project without the guard still fails closed
        self.addCleanup(shutil.rmtree, empty, True)
        self.assertEqual(self.hook("PreToolUse", data, empty).returncode, 2)

    def test_the_guard_acts_for_the_five_auditors(self):
        auditors = set()
        for name in sorted(os.listdir(AGENTS)):
            with open(os.path.join(AGENTS, name), encoding="utf-8") as f:
                front = f.read().split("\n---", 1)[0]
            self.assertNotIn("\nhooks:", front, name)  # VS Code's Claude Code ignores these
            tools = re.search(r"^tools: (.*)$", front, re.M).group(1)
            if "Read-only" in front and "Bash" in tools:
                auditors.add(re.search(r"^name: (.*)$", front, re.M).group(1).strip())
        self.assertEqual((len(auditors), auditors), (5, guard.AUDITORS))

    def test_lets_the_main_session_and_other_agents_by(self):
        store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, store, True)
        for agent_type in (None, "implementer", "general-purpose"):
            with self.subTest(agent_type=agent_type):
                data = {"agent_type": agent_type, "tool_input": {"command": "git checkout main"}}
                self.assertEqual(guard.run("pre", data, store, store), (0, ""))
                self.assertEqual(guard.run("stop", data, store, store), (0, ""))
                self.assertEqual(os.listdir(store), [])


if __name__ == "__main__":
    unittest.main()
