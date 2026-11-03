"""scripts/read-guard.py refuses a Read of a whole file over 8,000 bytes and passes every other
Read. The settings tests run the hook's command through the shell, with and without
CLAUDE_PROJECT_DIR, as Claude Code and VS Code Copilot would.

    python scripts/test_read_guard.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "read-guard.py")
SETTINGS = os.path.join(HERE, os.pardir, ".claude", "settings.json")
_spec = importlib.util.spec_from_file_location("read_guard", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

# 400 lines of 52 bytes: 20,800 bytes, well past the 8,000 limit.
LONG = "".join(f"line {i:05d} {'x' * 40}\n" for i in range(400))


def hook_input(path, **args):
    return {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Read",
            "tool_use_id": "t1", "tool_input": dict({"file_path": path}, **args)}


class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def write(self, name, text):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        return path

    def test_whole_read_of_a_long_file_is_refused_with_its_size_and_lines(self):
        path = self.write("big.md", LONG)
        reason = guard.refusal(hook_input(path))
        self.assertIsNotNone(reason, "a whole read of 20,800 bytes passed")
        self.assertIn("20,800 bytes, 400 lines", reason)
        self.assertIn("grep -n", reason)
        self.assertIn("limit=401", reason)

    def test_file_at_the_limit_passes(self):
        self.assertIsNone(guard.refusal(hook_input(self.write("edge.txt", "x" * guard.LIMIT))))

    def test_file_just_over_the_limit_is_refused(self):
        self.assertIsNotNone(guard.refusal(hook_input(self.write("over.txt", "x" * (guard.LIMIT + 1)))))

    def test_ranged_reads_pass(self):
        path = self.write("big.md", LONG)
        for args in ({"offset": 10}, {"limit": 50}, {"offset": 1, "limit": 401}, {"pages": "1-2"}):
            self.assertIsNone(guard.refusal(hook_input(path, **args)), args)

    def test_images_pdfs_and_notebooks_pass(self):
        for name in ("shot.PNG", "paper.pdf", "nb.ipynb"):
            self.assertIsNone(guard.refusal(hook_input(self.write(name, LONG))), name)

    def test_missing_file_and_folder_pass(self):
        self.assertIsNone(guard.refusal(hook_input(os.path.join(self.folder, "nope.md"))))
        self.assertIsNone(guard.refusal(hook_input(self.folder)))

    def test_other_tools_pass(self):
        data = dict(hook_input(self.write("big.md", LONG)), tool_name="Grep")
        self.assertIsNone(guard.refusal(data))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.project, "scripts"))
        shutil.copy(SCRIPT, os.path.join(self.project, "scripts", "read-guard.py"))
        self.big = os.path.join(self.project, "big.md")
        with open(self.big, "wb") as f:
            f.write(LONG.encode("utf-8"))
        with open(SETTINGS, encoding="utf-8") as f:
            entries = json.load(f)["hooks"]["PreToolUse"]
        self.command = next(h["command"] for e in entries if e["matcher"] == "Read" for h in e["hooks"])

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def run_command(self, data, project_dir=None):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        if project_dir:
            env["CLAUDE_PROJECT_DIR"] = project_dir
        return subprocess.run(self.command, shell=True, cwd=self.project, env=env,
                              input=json.dumps(data), capture_output=True, text=True)

    def test_settings_refuses_a_whole_long_read(self):
        run = self.run_command(hook_input(self.big), project_dir=self.project)
        self.assertEqual(run.returncode, 2, run.stdout)
        self.assertIn("read-guard:", run.stderr)

    def test_settings_passes_a_ranged_read(self):
        run = self.run_command(hook_input(self.big, offset=1, limit=20), project_dir=self.project)
        self.assertEqual((run.returncode, run.stderr), (0, ""))

    def test_settings_passes_without_project_dir(self):
        run = self.run_command(hook_input(self.big))
        self.assertEqual((run.returncode, run.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
