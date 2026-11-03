"""scripts/track-red.py passes a track only when each test its `new` criteria name fails on the base
by assertion and passes on the head. The parsing tests read spec text; the rest commit a module, a
test and a day spec to a scratch main, change them in the working tree, and run the script there.

    python scripts/test_track_red.py
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "track-red.py")
_spec = importlib.util.spec_from_file_location("track_red", SCRIPT)
track_red = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(track_red)

TEST = """import unittest
from app import double

class T(unittest.TestCase):
    def test_double(self):
        self.assertEqual(double(2), 4)

    def test_double_a_string(self):
        self.assertEqual(double("ab"), "abab")
"""
SPEC = """# Day 01

## Tracks

| Track | Owner | Work |
|---|---|---|
{rows}

## Acceptance criteria
- [ ] C1.1 **new** — a string doubles. Test: {test}, which replaces `T.test_gone`. Red today: "ab".
- [ ] C1.2 **hold** — an int doubles. Test: `T.test_double`.

## Notes
- Test: `T.test_in_the_notes`
"""
TWO_TRACKS = "| 0 | implementer | the hold C1.2 |\n| A | implementer | C1.1–C1.1 |"


def spec(rows="| A | implementer | `app.py`; the test C1.1 names |", test="`T.test_double_a_string`"):
    return SPEC.format(rows=rows, test=test)


NO_FIELD = spec().replace("Test: `T.test_double_a", "`T.test_double_a")
CAPITALISED = spec().replace("**new**", "**New**")


class RunnerTest(unittest.TestCase):
    def test_more_runner_commands_and_results(self):
        self.assertEqual(track_red.runner_command("pytest", "tests/test_app.py::test_x"), [sys.executable, "-m", "pytest", "tests/test_app.py::test_x"])
        self.assertEqual(track_red.runner_command("maven", "ExampleTest"), ["mvn", "clean", "test", "-Dtest=ExampleTest"])
        self.assertEqual(track_red.runner_command("npm", "tests/example.test.js"), ["npm", "test", "--", "tests/example.test.js"])
        for runner, output in (("pytest", "=== 1 failed, 2 passed ==="), ("maven", "Tests run: 3, Failures: 1, Errors: 0, Skipped: 0"), ("npm", "Tests: 1 failed, 2 passed, 3 total")):
            run = subprocess.CompletedProcess([], 1, output, "")
            self.assertTrue(track_red.runner_result(runner, run)[2], runner)


class ParseTest(unittest.TestCase):
    def test_day_template_names_tests_and_assigns_criteria_to_tracks(self):
        path = os.path.join(os.path.dirname(SCRIPT), "..", "specs", "_template.md")
        with open(path, encoding="utf-8") as f:
            text = f.read().replace("NN", "01")
        self.assertEqual(track_red.unread(text), [])
        self.assertEqual(track_red.criteria(text)["C01.1"],
                         ("new", ["ExampleTest.test_new_behavior"]))
        self.assertEqual(track_red.owned(text, "a"),
                         ({"C01.1": ("new", ["ExampleTest.test_new_behavior"])}, []))
        self.assertEqual(track_red.owned(text, "b"),
                         ({"C01.2": ("hold", [])}, []))

    def test_only_the_test_field_is_read_not_a_name_the_criterion_replaces(self):
        found = track_red.criteria(spec())
        self.assertEqual(found, {"C1.1": ("new", ["T.test_double_a_string"]), "C1.2": ("hold", ["T.test_double"])})
        self.assertEqual(track_red.criteria(NO_FIELD)["C1.1"], ("new", []))
        joined = track_red.criteria(spec(test="`T.a`, `T.b` and\n      `T.c`"))  # commas, "and", a wrap
        self.assertEqual(joined["C1.1"][1], ["T.a", "T.b", "T.c"])

    def test_one_row_owns_every_criterion_and_a_track_not_in_the_table_is_a_problem(self):
        self.assertEqual(track_red.owned(spec(), "a"), (track_red.criteria(spec()), []))
        self.assertEqual(track_red.owned(spec(), "c")[1], ["track C is not a row of the spec's Tracks table"])

    def test_with_several_rows_a_track_owns_what_its_work_cell_names_ranges_included(self):
        self.assertEqual(list(track_red.owned(spec(TWO_TRACKS), "0")[0]), ["C1.2"])
        self.assertEqual(list(track_red.owned(spec(TWO_TRACKS), "a")[0]), ["C1.1"])
        self.assertEqual(track_red.tracks("## Tracks\n| B | x | C3.2-C3.4 and C3.6 |")["b"], ["C3.2", "C3.3", "C3.4", "C3.6"])

    def test_a_new_criterion_no_row_names_is_a_problem_on_a_day_with_several_tracks(self):
        rows = "| A | implementer | C1.2 |\n| B | implementer | docs |"
        self.assertEqual(track_red.owned(spec(rows), "b")[1], ["C1.1 is new and no row of the Tracks table names it"])

    def test_a_criterion_line_the_pattern_misses_is_a_problem_and_a_wrapped_line_is_not(self):
        self.assertEqual(track_red.unread(spec(test="`T.a`, `T.b` and\n      `T.c`")), [])
        for text in (CAPITALISED, spec().replace("C1.1 **new**", "C1.1 new"), spec().replace("- [ ] C1.1", "* [ ] C1.1")):
            problems = track_red.unread(text)
            self.assertEqual(len(problems), 1, text)
            self.assertRegex(problems[0], r'^cannot read "[-*] \[ \] C1\.1 ')

    def test_no_criteria_section_an_empty_one_and_a_repeated_id_are_problems(self):
        self.assertEqual(track_red.unread(spec().replace("## Acceptance criteria", "## Acceptance Criteria")),
                         ["the spec has no `## Acceptance criteria` section"])
        empty = spec().split("## Acceptance criteria")[0] + "## Acceptance criteria\n\n## Notes\n"
        self.assertEqual(track_red.unread(empty), ["the Acceptance criteria section lists no criterion"])
        self.assertEqual(track_red.unread(spec().replace("C1.2 **hold**", "C1.1 **hold**")), ["C1.1 is listed 2 times"])


class TrackRedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "t"]):
            self.git(*args)
        self.write("app.py", "def double(x):\n    return x * 2 if isinstance(x, int) else x\n")
        self.write("tests/__init__.py", "")
        self.write("tests/test_app.py", TEST)

    def tearDown(self):
        subprocess.run(["git", "worktree", "prune"], cwd=self.dir)
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True)

    def write(self, path, text):
        os.makedirs(os.path.dirname(os.path.join(self.dir, path)), exist_ok=True)
        with open(os.path.join(self.dir, path), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def track_red(self, text=None, branch="day-01/track-a-double", fix="return x * 2", runner=None):
        """Commit main with the spec, put the track's fix in the working tree, and run the check."""
        self.write("specs/day-01-double.md", text or spec())
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.write("app.py", f"def double(x):\n    {fix}\n")
        extra = [] if runner is None else ["--runner", runner]
        run = subprocess.run([sys.executable, SCRIPT, "--branch", branch, *extra], cwd=self.dir,
                             capture_output=True, text=True, encoding="utf-8")
        return run.returncode, run.stdout + run.stderr

    def short_red(self, test="T.test_double_a_string", slug="double", tick=" "):
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.write(f"specs/changes/{slug}.md", "## Acceptance criteria\n"
                   f"- [{tick}] S-{slug}.1 **new** — strings double. Test: `{test}`.\n")
        self.write("app.py", "def double(x):\n    return x * 2\n")
        run = subprocess.run([sys.executable, SCRIPT, "--branch", f"change/{slug}"], cwd=self.dir,
                             capture_output=True, text=True, encoding="utf-8")
        return run.returncode, run.stdout + run.stderr

    def test_short_spec_on_head_requires_red_then_green(self):
        code, out = self.short_red()
        self.assertEqual(0, code, out)
        self.assertIn("seen red: tests.test_app.T.test_double_a_string (S-double.1)", out)

    def test_ticked_short_spec_new_test_is_checked(self):
        code, out = self.short_red(tick="x")
        self.assertEqual(0, code, out)
        self.assertIn("seen red: tests.test_app.T.test_double_a_string (S-double.1)", out)
        self.assertIn("1 of 1 tests", out)

    def test_short_spec_test_passing_on_base_is_rejected(self):
        code, out = self.short_red(test="T.test_double")
        self.assertEqual(1, code, out)
        self.assertIn("it does not see the change", out)

    def test_short_spec_missing_or_wrong_id_fails(self):
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        def run():
            return subprocess.run([sys.executable, SCRIPT, "--branch", "change/double"], cwd=self.dir,
                                  capture_output=True, text=True, encoding="utf-8")
        self.assertIn("is missing on the head", run().stdout)
        self.write("specs/changes/double.md", "## Acceptance criteria\n- [ ] S-other.1 **hold** — x\n")
        result = run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("does not belong to short spec double", result.stdout)

    def test_hold_only_short_spec_fails(self):
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.write("specs/changes/double.md", "## Acceptance criteria\n- [ ] S-double.1 **hold** — x\n")
        run = subprocess.run([sys.executable, SCRIPT, "--branch", "change/double"], cwd=self.dir,
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(1, run.returncode, run.stdout)
        self.assertIn("FAIL: short spec double has no new criterion; a behaviour change needs one", run.stdout)

    def test_a_test_red_on_the_base_and_green_on_the_head_passes(self):
        code, out = self.track_red()
        self.assertEqual(code, 0, out)
        self.assertIn("seen red: tests.test_app.T.test_double_a_string (C1.1) on ", out)
        self.assertIn("→ FAILED (failures=1); on the head → OK", out)
        self.assertIn("1 of 1 tests", out)

    def test_unittest_named_as_the_runner_checks_as_with_none_named(self):
        code, out = self.track_red(runner="unittest")
        self.assertEqual(code, 0, out)
        self.assertIn("→ FAILED (failures=1); on the head → OK", out)

    def test_an_empty_runner_is_not_configured_and_runs_nothing(self):
        # CI passes vars.TRACK_RED_RUNNER, empty when the repository has not set it.
        code, out = self.track_red(fix="return x", runner="")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.split(":")[0], "not configured", out)
        self.assertNotIn("seen red", out)

    def test_a_runner_it_does_not_support_is_a_configuration_error(self):
        code, out = self.track_red(runner="unknown")
        self.assertEqual(code, 2, out)
        self.assertIn("TRACK_RED_RUNNER `unknown` is not supported; supported runners: unittest, pytest, maven, npm", out)

    def test_a_branch_that_is_not_a_track_is_not_applicable(self):
        code, out = self.track_red(branch="tooling/word-rule-edge-cases")
        self.assertEqual((code, out.split(":")[0]), (0, "not applicable"), out)

    def test_a_track_that_owns_only_holds_is_not_applicable(self):
        code, out = self.track_red(spec(TWO_TRACKS), branch="day-01/track-0-pin")
        self.assertEqual(code, 0, out)
        self.assertIn("not applicable: track 0 of Day 01 owns no new criterion (hold: C1.2)", out)

    def test_a_new_criterion_with_no_test_field_fails(self):
        code, out = self.track_red(NO_FIELD)
        self.assertEqual(code, 1)
        self.assertIn("FAIL: C1.1 is new and has no Test: field", out)

    def test_a_new_criterion_the_check_cannot_read_fails_not_as_not_applicable(self):
        code, out = self.track_red(CAPITALISED)
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL: cannot read "- [ ] C1.1 **New**', out)
        self.assertNotIn("not applicable", out)

    def test_a_name_that_matches_no_test_on_the_head_fails(self):
        code, out = self.track_red(spec(test="`T.test_gone`"))
        self.assertEqual(code, 1)
        self.assertIn("FAIL: C1.1: `T.test_gone` matches 0 tests on the head, not one", out)

    def test_a_test_that_passes_on_the_base_fails(self):
        code, out = self.track_red(spec(test="`T.test_double`"))
        self.assertEqual(code, 1)
        self.assertRegex(out, r"reports OK on \w+: it does not see the change")

    def test_a_test_that_errors_on_the_base_fails(self):
        self.write("tests/test_more.py", "import unittest\nfrom app import triple\n\nclass M(unittest.TestCase):\n"
                                         "    def test_triple(self):\n        self.assertEqual(triple(1), 3)\n")
        code, out = self.track_red(spec(test="`M.test_triple`"), fix="return x * 2\n\ndef triple(x):\n    return x * 3")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"reports FAILED \(errors=1\) on \w+: an error, not an assertion")

    def test_a_test_that_fails_on_the_head_fails(self):
        code, out = self.track_red(fix="return x")
        self.assertEqual(code, 1)
        self.assertIn("does not pass on the head: FAILED (failures=", out)

    def test_a_test_that_runs_no_test_on_the_base_fails(self):
        # The module skips itself where triple is missing: "Ran 0 tests", "OK (skipped=1)" on the base.
        self.write("tests/test_more.py", "import unittest\nimport app\n\ndef setUpModule():\n"
                                         "    if not hasattr(app, 'triple'):\n        raise unittest.SkipTest('no triple')\n\n"
                                         "class M(unittest.TestCase):\n    def test_triple(self):\n"
                                         "        self.assertEqual(app.triple(1), 3)\n")
        code, out = self.track_red(spec(test="`M.test_triple`"), fix="return x * 2\n\ndef triple(x):\n    return x * 3")
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"FAIL: C1\.1: tests\.test_more\.M\.test_triple ran no test on \w+: OK \(skipped=1\)")
        self.assertIn("0 of 1 tests", out)

    def test_one_red_test_of_two_one_criterion_names_fails_and_says_which(self):
        code, out = self.track_red(spec(test="`T.test_double_a_string`, `T.test_double`"))
        self.assertEqual(code, 1, out)
        self.assertIn("seen red: tests.test_app.T.test_double_a_string (C1.1) on ", out)
        self.assertRegex(out, r"FAIL: C1\.1: tests\.test_app\.T\.test_double reports OK on \w+")
        self.assertIn("1 of 2 tests", out)

    def test_one_red_criterion_of_two_new_ones_fails_and_says_which(self):
        code, out = self.track_red(spec().replace("C1.2 **hold**", "C1.2 **new**"))
        self.assertEqual(code, 1, out)
        self.assertIn("seen red: tests.test_app.T.test_double_a_string (C1.1) on ", out)
        self.assertRegex(out, r"FAIL: C1\.2: tests\.test_app\.T\.test_double reports OK on \w+")
        self.assertIn("1 of 2 tests", out)


if __name__ == "__main__":
    unittest.main()
