"""scripts/break.py puts the code back as it was, and reports a break only when the check failed.

Each test writes a scratch file and runs break.py over it as a subprocess, with a short Python
command as the check: it fails when the file holds the broken text.

    python scripts/test_break.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "break.py")
LOCK = os.path.join(HERE, "..", ".break-active")


def check(path):
    """A check that fails, with an assertion line, when the file says `broken`."""
    return [sys.executable, "-c",
            f"import sys; t = open({path!r}).read(); "
            "sys.exit('AssertionError: expected ok but was broken') if 'broken' in t else print('1 passed')"]


def load():
    """break.py as a module, for the functions a test calls without a run."""
    spec = importlib.util.spec_from_file_location("break_script", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


class SummaryTest(unittest.TestCase):
    def test_javascript_summaries_precede_failure_details(self):
        script = load()
        for summary in ("Tests: 1 failed, 4 passed, 5 total", "Tests  1 failed | 4 passed (5)"):
            self.assertEqual(script.reported("FAIL detail\n" + summary + "\n"), summary)


class BreakTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.file = os.path.join(self.dir, "Guard.java")
        # CRLF on purpose: a revert in text mode would write LF back.
        self.original = b"class Guard {\r\n    boolean ok = true; // ok\r\n}\r\n"
        with open(self.file, "wb") as f:
            f.write(self.original)

    def run_break(self, find, replace, command=None, extra=(), what="the guard removed", dash=("--",)):
        return subprocess.run([sys.executable, SCRIPT, "--file", self.file, "--find", find, "--replace", replace,
                               "--what", what, *extra, *dash, *(command or check(self.file))],
                              capture_output=True, text=True, encoding="utf-8")

    def contents(self):
        with open(self.file, "rb") as f:
            return f.read()

    def test_a_check_that_fails_gives_the_broken_line_and_the_file_comes_back_byte_for_byte(self):
        run = self.run_break("// ok", "// broken")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "broken: the guard removed → AssertionError: expected ok but was broken")
        self.assertEqual(self.contents(), self.original)
        self.assertFalse(os.path.exists(LOCK))

    def test_a_command_with_options_needs_no_dashes_before_it(self):
        run = self.run_break("// ok", "// broken", dash=())
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "broken: the guard removed → AssertionError: expected ok but was broken")

    def test_an_arrow_in_what_is_refused_before_anything_changes(self):
        # spec-drift.py reads <what> up to the first arrow: a pilot's "if not paths: → if False:".
        for what in ("if not paths: → if False:", "66 -> 67"):
            run = self.run_break("// ok", "// broken", what=what)
            self.assertEqual(run.returncode, 2, what)
            self.assertIn("--what holds an arrow", run.stderr)
            self.assertEqual(self.contents(), self.original)

    def test_a_git_warning_is_not_what_the_check_reported(self):
        # a pilot: git diff --exit-code on Windows ends with git's CRLF warning on stderr.
        diff = [sys.executable, "-c", f"import sys; t = open({self.file!r}).read(); "
                "print(' 1 file changed, 1 insertion(+)') if 'broken' in t else None; "
                "sys.exit(\"warning: in the working copy of 'a.py', LF will be replaced by CRLF\") if 'broken' in t else None"]
        run = self.run_break("// ok", "// broken", command=diff)
        self.assertEqual(run.stdout.strip(), "broken: the guard removed → 1 file changed, 1 insertion(+)")

    def test_a_temporary_path_in_the_report_is_written_tmp(self):
        # a pilot: unittest's repr of a path under the user's temp directory, its start cut.
        full = os.path.join(tempfile.gettempdir(), "tmp2ewplof6", "a.txt")
        said = f"AssertionError: {full!r} != '[27 chars]1\\\\AppData\\\\Local\\\\Temp\\\\tmp2ewplof6\\\\a.txt' in {full}"
        run = self.run_break("// ok", "// broken", command=[sys.executable, "-c",
                             f"import sys; t = open({self.file!r}).read(); sys.exit({said!r}) if 'broken' in t else None"])
        line = run.stdout.strip()
        self.assertNotIn(tempfile.gettempdir(), line)
        self.assertNotIn("tmp2ewplof6", line)
        self.assertEqual(line.count("<tmp>"), 3, line)
        # On Linux the directory is /tmp, which a tempfile name under it also starts with (CI on #20).
        spec = importlib.util.spec_from_file_location("break_script", SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with mock.patch.object(script.tempfile, "gettempdir", return_value="/tmp"):
            self.assertEqual(script.untemp("'/tmp/tmp2ewplof6/a.txt' in /tmp/x, not /tmpfoo"),
                             "'<tmp>/a.txt' in <tmp>/x, not /tmpfoo")

    def test_a_check_that_passes_with_the_break_in_is_reported_as_blind(self):
        run = self.run_break("true", "false")
        self.assertEqual(run.returncode, 1)
        self.assertIn("cannot see this break", run.stdout)
        self.assertNotIn("broken:", run.stdout)
        self.assertEqual(self.contents(), self.original)

    def test_a_find_that_is_missing_or_ambiguous_changes_nothing(self):
        for find in ("absent", "o"):
            run = self.run_break(find, "broken")
            self.assertEqual(run.returncode, 2, find)
            self.assertIn("want exactly once", run.stderr)
            self.assertEqual(self.contents(), self.original)

    def test_a_maven_check_without_clean_is_refused_before_anything_changes(self):
        run = self.run_break("// ok", "// broken", ["../../mvnw", "verify", "-Dtest=GuardIT"])
        self.assertEqual(run.returncode, 2)
        self.assertIn("must clean", run.stderr)
        self.assertEqual(self.contents(), self.original)

    def test_a_command_that_does_not_start_is_not_a_break(self):
        run = self.run_break("// ok", "// broken", ["no-such-command-here"])
        self.assertEqual(run.returncode, 2)
        self.assertIn("did not start", run.stderr)
        self.assertEqual(self.contents(), self.original)
        self.assertFalse(os.path.exists(LOCK))

    def test_a_command_that_starts_but_never_runs_the_check_is_known(self):
        # a pilot, Day 3: `bash` was WSL's, and break.py printed broken: lines for checks never run.
        script = load()
        wsl = "<3>WSL (9) ERROR: CreateProcessCommon:559: execvpe(/bin/bash) failed: No such file or directory\n"
        self.assertEqual(script.not_run(1, wsl), wsl.strip())
        self.assertEqual(script.not_run(127, "x\n"), "exit 127: the shell could not run the program")
        self.assertIsNone(script.not_run(1, "FAILED (failures=1)\n"))

    def test_a_command_that_starts_but_never_runs_the_check_is_refused(self):
        wsl = [sys.executable, "-c", "import sys; sys.exit('<3>WSL (9) ERROR: execvpe(/bin/bash) failed')"]
        run = self.run_break("// ok", "// broken", wsl)
        self.assertEqual((run.returncode, run.stdout), (2, ""))
        self.assertIn("the check did not run", run.stderr)
        self.assertEqual(self.contents(), self.original)

    def test_expect_stdout_judges_the_check_by_its_output(self):
        # a pilot, Days 4 and 5: `grep -c` prints 0 and exits 1 when no test was edited.
        script = load()
        self.assertFalse(script.caught(1, "0\n", "^0$"))
        self.assertTrue(script.caught(1, "3\n", "^0$"))
        self.assertTrue(script.caught(0, "removed=2\n", "removed=0"))
        self.assertEqual((script.caught(1, "", None), script.caught(0, "", None)), (True, False))

    def test_expect_stdout_records_a_count_check(self):
        count = [sys.executable, "-c", f"import sys; n = open({self.file!r}).read().count('broken'); "
                 "print(n); sys.exit(0 if n else 1)"]
        run = self.run_break("// ok", "// broken", count, extra=("--expect-stdout", "^0$"))
        self.assertEqual((run.returncode, run.stdout.strip()), (0, "broken: the guard removed → 1"))
        by_exit = self.run_break("// ok", "// broken", count)
        self.assertEqual(by_exit.returncode, 2)  # by exit code, the unbroken check fails
        self.assertIn("fails without the break", by_exit.stderr)
        blind = [sys.executable, "-c", f"print(open({self.file!r}).read().count('broken'))"]
        self.assertEqual(self.run_break("// ok", "// broken", blind).returncode, 1)  # by exit code, blind

    def test_a_check_that_fails_without_the_break_is_refused_and_nothing_is_recorded(self):
        run = self.run_break("// ok", "// broken", [sys.executable, "-c", "raise RuntimeError('unrelated')"])
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("fails without the break", run.stderr)
        self.assertIn("RuntimeError: unrelated", run.stderr)
        self.assertNotIn("broken:", run.stdout)
        self.assertEqual(self.contents(), self.original)
        self.assertFalse(os.path.exists(LOCK))

    def test_expect_stdout_that_never_matches_without_the_break_is_refused(self):
        run = self.run_break("// ok", "// broken", extra=("--expect-stdout", "never matches"))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("fails without the break", run.stderr)
        self.assertNotIn("broken:", run.stdout)
        self.assertEqual(self.contents(), self.original)
        self.assertFalse(os.path.exists(LOCK))

    def test_the_lock_is_held_during_the_baseline_run(self):
        marker = os.path.join(self.dir, "baseline-ran")
        command = [sys.executable, "-c", f"import os, sys; t = open({self.file!r}).read(); "
                   f"sys.exit('AssertionError: broken') if 'broken' in t else "
                   f"open({marker!r}, 'w').write('locked') if os.path.exists({LOCK!r}) else sys.exit(1)"]
        run = self.run_break("// ok", "// broken", command)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("broken:", run.stdout)
        self.assertTrue(os.path.exists(marker))
        self.assertFalse(os.path.exists(LOCK))

    def test_a_relative_program_is_found_from_cwd(self):
        # `--cwd services/job-service -- ../../mvnw` names the root's mvnw; Windows would look for
        # it from the caller's directory. A bare name is left for PATH.
        spec = importlib.util.spec_from_file_location("break_script", SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        head = script.argv_for(["../../mvnw", "clean"], os.path.join(self.dir, "services", "job-service"))[0]
        self.assertEqual(head, os.path.join(self.dir, "mvnw") + (".cmd" if os.name == "nt" else ""))
        self.assertEqual(script.argv_for(["python", "-V"], self.dir), ["python", "-V"])

    def test_a_second_break_while_one_is_active_is_refused(self):
        with open(LOCK, "w") as f:
            f.write("other")
        try:
            run = self.run_break("// ok", "// broken")
        finally:
            os.remove(LOCK)
        self.assertEqual(run.returncode, 2)
        self.assertIn("another break is active", run.stderr)
        self.assertEqual(self.contents(), self.original)


if __name__ == "__main__":
    unittest.main()
