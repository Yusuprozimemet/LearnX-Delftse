"""scripts/evidence.py fails a ticked criterion whose test, Verify line or break does not hold up.

Each test builds a small repository with one day's close merged, writes the day's spec and its
breaks file, and runs evidence.py over it as a subprocess.

    python scripts/test_evidence.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "evidence.py")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
# A check that fails, with an assertion line, when app.txt says `broken`.
CHECK = [sys.executable, "-c",
         "import sys; sys.exit('AssertionError: broken') if 'broken' in open('app.txt').read() else None"]
BREAK = {"what": "the ok removed", "file": "app.txt", "find": "ok", "replace": "broken", "command": CHECK}


def spec(test="CliTest.test_a", verify="echo 0    # 0", report="AssertionError: broken (#2)"):
    return (f"# Day 01\n\n## Acceptance criteria\n"
            f"- [x] C1.1 **new** — Test: `{test}`.\n"
            f"      broken: the ok removed → {report}\n"
            f"- [ ] C1.2 **new** — not ticked: `CliTest.test_never` is not checked.\n\n"
            f"## Verify\n```bash\n{verify}\n```\n")


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.write("tests/test_cli.py",
                   "class CliTest(unittest.TestCase):\n    def test_a(self):\n        pass\n")
        self.write("app.txt", "ok\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.git("switch", "-q", "-c", "day-01/close-the-day")
        self.write("close.txt", "closed\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "close")
        self.git("switch", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge pull request #2 from me/day-01/close-the-day",
                 "day-01/close-the-day")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=GIT_ENV, capture_output=True, text=True,
                              check=True).stdout

    def write(self, path, text):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def run_evidence(self, text, breaks=(BREAK,), *args):
        self.write("specs/day-01-x.md", text)
        self.write("specs/breaks/day-01.json", json.dumps(list(breaks)))
        return subprocess.run([sys.executable, SCRIPT, "specs/day-01-x.md", *args], cwd=self.repo, env=GIT_ENV,
                              capture_output=True, text=True, encoding="utf-8")

    def report(self, text, breaks=(BREAK,)):
        """The run and the report it wrote."""
        run = self.run_evidence(text, breaks, "--json", "report.json")
        with open(os.path.join(self.repo, "report.json"), encoding="utf-8") as f:
            return run, json.load(f)

    def statuses(self, report):
        return {c["id"].split("/", 1)[1]: c["status"] for d in report["days"] for c in d["checks"]}

    def failures(self, run):
        return [l for l in run.stdout.splitlines() if l.startswith("FAIL")]

    def test_evidence_that_holds_passes_and_an_unticked_criterion_is_not_checked(self):
        run = self.run_evidence(spec())
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("all evidence holds", run.stdout)
        self.assertNotIn("test_never", run.stdout)
        self.assertIn("C1.1 broken: the ok removed", run.stdout)

    def test_a_renamed_test_fails(self):
        run = self.run_evidence(spec(test="CliTest.test_renamed"))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(len(self.failures(run)), 1, run.stdout)
        self.assertIn("CliTest.test_renamed → not defined", run.stdout)

    def test_a_test_in_another_class_fails(self):
        run = self.run_evidence(spec(test="OtherTest.test_a"))
        self.assertIn("OtherTest.test_a → not defined", run.stdout)
        self.assertEqual(run.returncode, 1)

    def test_a_verify_line_whose_output_differs_from_its_comment_fails(self):
        run = self.run_evidence(spec(verify="echo 1    # 0"))
        self.assertEqual(run.returncode, 1)
        self.assertIn("wanted a line '0', got '1'", run.stdout)

    def test_a_verify_line_without_a_comment_fails_on_a_nonzero_exit(self):
        run = self.run_evidence(spec(verify="false"))
        self.assertIn("Verify: false → exit 1", run.stdout)

    def test_nothing_and_an_exit_status_in_a_comment_are_both_checked(self):
        self.assertEqual(self.run_evidence(spec(verify="true    # nothing, exit 0")).returncode, 0)
        run = self.run_evidence(spec(verify="echo x; false    # nothing, exit 0"))
        self.assertIn("wanted nothing, got 'x'", run.stdout)

    def test_main_in_a_verify_line_means_the_close_s_first_parent(self):
        run = self.run_evidence(spec(verify=f"git rev-parse main    # {self.base}"))
        self.assertEqual(run.returncode, 0, run.stdout)

    def test_a_break_with_no_recorded_arguments_fails(self):
        run = self.run_evidence(spec(), breaks=())
        self.assertEqual(run.returncode, 1)
        self.assertIn("C1.1 broken: the ok removed → no arguments in", run.stdout)

    def test_a_break_that_reports_something_else_fails(self):
        run = self.run_evidence(spec(report="AssertionError: something else"))
        self.assertEqual(run.returncode, 1)
        self.assertIn("reported 'AssertionError: broken', the spec says 'AssertionError: something else'",
                      run.stdout)

    def test_a_break_the_check_cannot_see_fails(self):
        run = self.run_evidence(spec(), breaks=(dict(BREAK, command=[sys.executable, "-c", "pass"]),))
        self.assertEqual(run.returncode, 1)
        self.assertIn("passed with the break in", run.stdout)

    def test_a_temporary_directory_s_name_does_not_count_as_a_difference(self):
        report = "AssertionError: C:\\Temp\\tmpab12cd34\\a.txt"
        check = [sys.executable, "-c",
                 "import sys; sys.exit('AssertionError: C:\\\\Temp\\\\tmpzz99yy00\\\\a.txt') "
                 "if 'broken' in open('app.txt').read() else None"]
        run = self.run_evidence(spec(report=report), breaks=(dict(BREAK, command=check),))
        self.assertEqual(run.returncode, 0, run.stdout)

    def test_an_arrow_inside_what_was_broken_cannot_run_again(self):
        # break.py refuses it, since spec-drift.py ends <what> at the first arrow: not run, not failed.
        what = "the ok (ok → broken)"
        run = self.run_evidence(spec().replace("the ok removed", what), breaks=(dict(BREAK, what=what),))
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("ERR   C1.1 broken: the ok (ok → broken) → break.py: --what holds an arrow", run.stdout)
        self.assertIn("1 could not run", run.stdout)

    def test_a_report_recorded_across_lines_with_its_backslashes_undoubled_is_the_same(self):
        report = "AssertionError: 'C:\\x\n'"  # as a close pasted it: \\ as \, \n as a new line
        check = [sys.executable, "-c", "import sys; sys.exit(\"AssertionError: 'C:\\\\\\\\x\\\\n'\") "
                 "if 'broken' in open('app.txt').read() else None"]
        run = self.run_evidence(spec(report=report), breaks=(dict(BREAK, command=check),))
        self.assertEqual(run.returncode, 0, run.stdout)
        run = self.run_evidence(spec(report=report.replace("x", "y")), breaks=(dict(BREAK, command=check),))
        self.assertEqual(run.returncode, 1, run.stdout)

    def test_a_bash_command_runs_in_git_s_bash(self):
        check = ["bash", "-c", "grep -qx ok app.txt || { echo 'AssertionError: broken'; exit 1; }"]
        run = self.run_evidence(spec(), breaks=(dict(BREAK, command=check),))
        self.assertEqual(run.returncode, 0, run.stdout)

    def test_a_near_miss_recorded_as_passing_must_pass_and_report_its_last_line(self):
        check = [sys.executable, "-c", "print('removed=0')"]
        near = dict(BREAK, command=check, passes=True)
        run = self.run_evidence(spec(report="removed=0"), breaks=(near,))
        self.assertEqual(run.returncode, 0, run.stdout)
        run = self.run_evidence(spec(), breaks=(dict(BREAK, passes=True),))
        self.assertEqual(run.returncode, 1)
        self.assertIn("failed with the break in: this near miss should pass", run.stdout)

    def test_a_break_recorded_at_a_commit_runs_there_not_at_the_close(self):
        check = [sys.executable, "-c", "import os, sys; sys.exit('AssertionError: broken') if 'broken' in "
                 "open('app.txt').read() and not os.path.exists('close.txt') else None"]
        run = self.run_evidence(spec(), breaks=(dict(BREAK, command=check),))
        self.assertIn("passed with the break in", run.stdout)
        run = self.run_evidence(spec(), breaks=(dict(BREAK, command=check, at=self.base),))
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(len(self.git("worktree", "list").splitlines()), 1)

    def test_a_day_whose_close_has_not_merged_fails(self):
        self.write("specs/day-02-y.md", spec())
        run = subprocess.run([sys.executable, SCRIPT, "specs/day-02-y.md"], cwd=self.repo, env=GIT_ENV,
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 1)
        self.assertIn("no merged day-02/close-the-day on main", run.stdout)

    def test_the_worktree_is_removed(self):
        self.run_evidence(spec())
        self.assertEqual(len(self.git("worktree", "list").splitlines()), 1)

    def test_the_report_holds_the_checks_the_console_prints(self):
        run, report = self.report(spec())
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(self.statuses(report), {"close": "passed", "C1.1/test/CliTest.test_a": "passed",
                                                 "verify/1": "passed", "C1.1/break/the ok removed": "passed"})
        printed = run.stdout.splitlines()[:-1]
        self.assertEqual(len(printed), 4)
        for line, check in zip(printed, report["days"][0]["checks"]):
            self.assertEqual(line, "ok    " + check["label"])
        self.assertEqual(report["summary"]["break"], {"passed": 1, "failed": 0, "error": 0, "not_applicable": 0})

    def test_the_report_writes_each_check_on_a_line_of_its_own(self):
        self.report(spec())
        with open(os.path.join(self.repo, "report.json"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        report = json.loads("\n".join(lines))
        for check in report["days"][0]["checks"]:
            self.assertIn("  " + json.dumps(check, ensure_ascii=False, separators=(",", ":")),
                          [l.rstrip(",") for l in lines])

    def test_the_report_records_the_commits_and_inputs_checked(self):
        close = self.git("rev-parse", "HEAD").strip()
        self.run_evidence(spec())
        self.git("add", "specs")
        self.git("commit", "-q", "-m", "specs")
        _, report = self.report(spec())  # the same text: the files are as committed
        day = report["days"][0]
        self.assertEqual((report["schema"], report["ref"]), (1, "main"))
        self.assertEqual(report["ref_sha"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual((day["close_sha"], day["verify_base_sha"]), (close, self.base))
        self.assertEqual((day["spec"]["state"], day["spec"]["revision"]), ("clean", report["ref_sha"]))
        self.assertEqual(len(day["breaks"]["sha256"]), 64)
        _, report = self.report(spec(verify="true"))
        self.assertEqual((report["days"][0]["spec"]["state"], report["days"][0]["spec"]["revision"]), ("edited", None))

    def test_a_break_recorded_at_a_commit_reports_that_commit(self):
        _, report = self.report(spec(), breaks=(dict(BREAK, at=self.base),))
        check = report["days"][0]["checks"][-1]
        self.assertEqual((check["status"], check["sha"]), ("passed", self.base))

    def test_a_criterion_with_no_named_test_and_no_command_fails(self):
        run, report = self.report(spec().replace("Test: `CliTest.test_a`", "Test: by eye"))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.statuses(report)["C1.1/test"], "failed")
        self.assertIn("C1.1 named test → none, and no command named", run.stdout)

    def test_a_criterion_checked_by_a_command_is_not_applicable_for_a_named_test(self):
        run, report = self.report(spec().replace("`CliTest.test_a`", "`git diff --exit-code main`"))
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(self.statuses(report)["C1.1/test"], "not_applicable")
        self.assertIn("n/a   C1.1 named test → none: checked by `git diff --exit-code main`", run.stdout)

    def test_a_missing_verify_block_fails(self):
        run, report = self.report(spec().split("## Verify")[0])
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.statuses(report)["verify"], "failed")

    def test_a_hold_criterion_with_no_broken_line_fails_and_broken_none_is_not_applicable(self):
        hold = spec().replace("**new**", "**hold**")
        run, report = self.report(hold.replace("      broken: the ok removed → AssertionError: broken (#2)\n", ""))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.statuses(report)["C1.1/break"], "failed")
        run, report = self.report(hold.replace("the ok removed → AssertionError: broken", "none → docs only"))
        self.assertEqual(run.returncode, 0, run.stdout)
        check = report["days"][0]["checks"][-1]
        self.assertEqual((check["status"], check["diagnostic"]), ("not_applicable", "docs only (#2)"))

    def test_a_spec_with_no_ticked_criterion_does_not_pass(self):
        run, report = self.report(spec().replace("- [x]", "- [ ]"))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.statuses(report)["criteria"], "failed")

    def test_a_break_that_cannot_be_run_is_an_error_and_still_leaves_a_report(self):
        run, report = self.report(spec(), breaks=(dict(BREAK, at="0" * 40),))
        self.assertEqual(run.returncode, 1)
        self.assertIn("1 could not run", run.stdout)
        check = report["days"][0]["checks"][-1]
        self.assertEqual(check["status"], "error")
        self.assertTrue(check["diagnostic"].startswith("no worktree at 0000"), check["diagnostic"])
        self.assertIsNone(check["sha"])

    def test_a_command_that_does_not_start_is_an_error_and_a_find_no_longer_there_a_failure(self):
        _, report = self.report(spec(), breaks=(dict(BREAK, command=["no-such-program-x1"]),))
        check = report["days"][0]["checks"][-1]
        self.assertEqual(check["status"], "error", check["diagnostic"])
        self.assertIn("the command did not start", check["diagnostic"])
        _, report = self.report(spec(), breaks=(dict(BREAK, find="gone"),))
        check = report["days"][0]["checks"][-1]
        self.assertEqual((check["status"], check["diagnostic"]),
                         ("failed", f"break.py: --find occurs 0 times in {check['diagnostic'].split(' in ', 1)[1]}"))

    def test_a_spec_that_does_not_exist_fails_in_the_report(self):
        run = subprocess.run([sys.executable, SCRIPT, "specs/day-03-gone.md", "--json", "r.json"], cwd=self.repo,
                             env=GIT_ENV, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 1)
        with open(os.path.join(self.repo, "r.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["days"][0]["checks"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
