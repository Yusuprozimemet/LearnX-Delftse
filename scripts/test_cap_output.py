"""scripts/cap-output.py cuts a long Bash result to its head and tail, saves the full output, and passes
the rest. The settings tests run the hook's command through the shell, with and without
CLAUDE_PROJECT_DIR, as Claude Code and VS Code Copilot would.

    python scripts/test_cap_output.py
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
SCRIPT = os.path.join(HERE, "cap-output.py")
SETTINGS = os.path.join(HERE, os.pardir, ".claude", "settings.json")
_spec = importlib.util.spec_from_file_location("cap_output", SCRIPT)
cap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cap)

# 400 lines of 52 characters: 20,800 characters, well past the 8,000 limit.
LONG = "".join(f"line {i:05d} {'x' * 40}\n" for i in range(400))
MARKER = re.compile(r"\n(\[cut (\d+) chars; full output: (.+?); use Read with offset/limit or grep\])\n")


def response(stdout, **extra):
    return dict({"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False,
                 "noOutputExpected": False}, **extra)


def hook_input(stdout, **extra):
    return {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_use_id": "t1", "tool_input": {"command": "x"}, "tool_response": response(stdout, **extra)}


class CutTests(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def test_short_output_passes(self):
        self.assertIsNone(cap.handle(hook_input("short\n"), self.project))

    def test_output_at_the_limit_passes(self):
        self.assertIsNone(cap.handle(hook_input("x" * cap.LIMIT), self.project))

    def test_output_claude_code_already_saved_passes(self):
        # Past 30,000 characters Claude Code saves the output itself and hands the hook only its start.
        out = cap.handle(hook_input("x" * 30000), self.project)
        self.assertTrue(out is None, "cut output Claude Code had already saved")
        self.assertFalse(os.path.exists(os.path.join(self.project, cap.SAVED)))

    def test_output_just_under_claude_codes_limit_is_cut(self):
        self.assertIsNotNone(cap.handle(hook_input("x" * 29999), self.project))

    def test_long_output_keeps_head_and_tail_with_one_line_between(self):
        out = cap.handle(hook_input(LONG), self.project)
        text = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        found = MARKER.search(text)
        self.assertIsNotNone(found, text[:200])
        self.assertEqual(text, f"{LONG[:3000]}\n{found.group(1)}\n{LONG[-3000:]}")
        self.assertEqual(int(found.group(2)), len(LONG) - 6000)

    def test_full_output_is_saved_as_written(self):
        out = cap.handle(hook_input(LONG), self.project)
        text = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        path = MARKER.search(text).group(3)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), LONG.encode("utf-8"))
        self.assertTrue(path.startswith(os.path.join(self.project, ".claude", "tool-output")))

    def test_bash_object_shape_is_kept(self):
        out = cap.handle(hook_input(LONG), self.project)
        reply = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIsInstance(reply, dict)
        self.assertEqual(set(reply), {"stdout", "stderr", "interrupted", "isImage", "noOutputExpected"})
        self.assertEqual(reply["isImage"], False)

    def test_failing_command_with_separate_stderr_is_left_whole(self):
        # Live Bash never sends a separate stderr: see the merged-stderr test below.
        self.assertIsNone(cap.handle(hook_input(LONG, stderr="boom\n"), self.project))

    def test_interrupted_command_is_left_whole(self):
        self.assertIsNone(cap.handle(hook_input(LONG, interrupted=True), self.project))

    def test_live_failing_command_is_cut_and_its_error_kept(self):
        # Claude Code merges stderr into stdout: the error ends stdout and stderr is ''.
        stdout = LONG + "Traceback (most recent call last):\nValueError: boom\n"
        out = cap.handle(hook_input(stdout), self.project)
        self.assertIsNotNone(out, "the merged error passed the stderr guard whole")
        text = out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
        self.assertIsNotNone(MARKER.search(text), text[:200])
        self.assertTrue(text.endswith("ValueError: boom\n"), text[-200:])

    def test_output_that_cannot_be_saved_passes(self):
        not_a_dir = os.path.join(self.project, "file")
        open(not_a_dir, "w").close()
        self.assertIsNone(cap.handle(hook_input(LONG), not_a_dir))

    def test_no_project_dir_writes_nothing(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        run = subprocess.run([sys.executable, SCRIPT], input=json.dumps(hook_input(LONG)),
                             capture_output=True, text=True, env=env)
        self.assertEqual((run.returncode, run.stdout), (0, ""))
        self.assertFalse(os.path.exists(os.path.join(self.project, ".claude")))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.project, "scripts"))
        shutil.copy(SCRIPT, os.path.join(self.project, "scripts", "cap-output.py"))
        with open(SETTINGS, encoding="utf-8") as f:
            entries = json.load(f)["hooks"]["PostToolUse"]
        self.command = next(h["command"] for e in entries if e["matcher"] == "Bash" for h in e["hooks"])

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def run_command(self, data, project_dir=None):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        if project_dir:
            env["CLAUDE_PROJECT_DIR"] = project_dir
        return subprocess.run(self.command, shell=True, cwd=self.project, env=env,
                              input=json.dumps(data), capture_output=True, text=True)

    def test_settings_runs_the_script_on_long_bash_output(self):
        run = self.run_command(hook_input(LONG), project_dir=self.project)
        self.assertEqual(run.returncode, 0, run.stderr)
        reply = json.loads(run.stdout)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIsNotNone(MARKER.search(reply["stdout"]))

    def test_settings_passes_without_project_dir(self):
        run = self.run_command(hook_input(LONG))
        self.assertEqual((run.returncode, run.stdout), (0, ""), run.stderr)

    def test_settings_passes_short_output(self):
        run = self.run_command(hook_input("short\n"), project_dir=self.project)
        self.assertEqual((run.returncode, run.stdout), (0, ""), run.stderr)


if __name__ == "__main__":
    unittest.main()
