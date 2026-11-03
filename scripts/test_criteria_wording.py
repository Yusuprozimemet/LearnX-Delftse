"""Accepted wording, allowed closing edits, and the revision/branch boundary."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_evidence import GIT_ENV
import subprocess

_spec = importlib.util.spec_from_file_location("criteria_wording", Path(__file__).with_name("criteria-wording.py"))
wording = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wording)
SPEC = """# Day 07
## Acceptance criteria
- [ ] C07.1 **new** Keep the result stable.
  Red today: the count is two.
  Test: `Example.test_result`.
- [ ] C07.2 **hold** Keep the other result.
## Notes
Original notes.
"""


class WordingTest(unittest.TestCase):
    def test_closing_edits(self):
        after = SPEC.replace("[ ]", "[x]").replace(
            "- [x] C07.2", "  Evidence: passed (#8).\n  broken: count removed → assertion failed (#8).\n- [x] C07.2")
        after = after.replace("Original notes.", "New notes and track table.")
        self.assertEqual(wording.compare(SPEC, after), [])

    def test_statement(self):
        self.assertEqual(wording.compare(SPEC, SPEC.replace("stable", "different")),
                         ["C07.1: statement changed"])

    def test_red_today(self):
        self.assertEqual(wording.compare(SPEC, SPEC.replace("two", "three")),
                         ["C07.1: statement changed"])

    def test_test_field(self):
        self.assertEqual(wording.compare(SPEC, SPEC.replace("Example.test_result", "Example.test_other")),
                         ["C07.1: statement changed"])

    def test_tag(self):
        self.assertEqual(wording.compare(SPEC, SPEC.replace("**new**", "**hold**")),
                         ["C07.1: tag changed"])

    def test_removed(self):
        self.assertEqual(wording.compare(SPEC, SPEC.replace("- [ ] C07.2 **hold** Keep the other result.\n", "")),
                         ["C07.2: removed"])

    def test_added(self):
        after = SPEC.replace("## Notes", "- [ ] C07.3 **hold** Another result.\n## Notes")
        self.assertEqual(wording.compare(SPEC, after), ["C07.3: added"])

    def test_wrapping_and_crlf(self):
        after = SPEC.replace("Keep the result stable.", "Keep the\n   result   stable.")
        self.assertEqual(wording.compare(SPEC, after.replace("\n", "\r\n")), [])


class RevisionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        (self.repo / "specs").mkdir()
        self.path = self.repo / "specs/day-07-example.md"
        self.path.write_text(SPEC, encoding="utf-8")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=GIT_ENV,
                              capture_output=True, text=True, encoding="utf-8", check=True).stdout

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def run_check(self, branch="tooling/example"):
        output = io.StringIO()
        with patch.object(wording, "git", side_effect=self.git), contextlib.redirect_stdout(output):
            status = wording.main(["--base", self.base, "--head", "HEAD", "--branch", branch])
        return status, output.getvalue()

    def test_close_rewording(self):
        self.path.write_text(SPEC.replace("stable", "different"), encoding="utf-8")
        self.commit()
        status, output = self.run_check("day-07/close-the-day")
        self.assertEqual(status, 1, output)
        self.assertIn("specs/day-07-example.md: C07.1: statement changed", output)

    def test_spec_exempt(self):
        self.path.write_text(SPEC.replace("stable", "different"), encoding="utf-8")
        self.commit()
        status, output = self.run_check("day-07/spec-fix-grep")
        self.assertEqual(status, 0, output)
        self.assertIn("spec-change PR exempt", output)

    def test_new_and_deleted(self):
        self.path.rename(self.repo / "specs/day-08-example.md")
        self.commit()
        status, output = self.run_check()
        self.assertEqual(status, 1, output)
        self.assertIn("specs/day-07-example.md: spec deleted", output)
        self.assertIn("specs/day-08-example.md: spec added", output)
        self.assertIn("2 criterion/spec differences", output)

    def test_no_day_touched(self):
        status, output = self.run_check()
        self.assertEqual(status, 0, output)
        self.assertIn("no day spec was touched", output)


if __name__ == "__main__":
    unittest.main()
