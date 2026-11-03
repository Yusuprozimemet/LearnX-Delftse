"""Commit-to-spec lookup against real throwaway Git repositories."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evidence import close_commit, closing_spec
from test_evidence import GIT_ENV


class ClosingSpecTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=GIT_ENV,
                              capture_output=True, text=True, check=True).stdout

    def merge(self, specs=(), subject="Merge pull request #9 from owner/day-07/close-the-day"):
        self.git("switch", "-q", "-c", "close")
        os.makedirs(os.path.join(self.repo, "specs"))
        for name in specs:
            with open(os.path.join(self.repo, "specs", name), "w") as file:
                file.write("# Day 07\n")
        self.git("add", ".")
        self.git("commit", "-q", "--allow-empty", "-m", "close")
        self.git("switch", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", subject, "close")

    def lookup(self):
        # Only route the production Git calls to the fixture; Git itself is real.
        with patch("evidence.git", side_effect=self.git):
            return closing_spec("HEAD")

    def test_one_spec(self):
        self.merge(("day-07-example.md",))
        self.assertEqual(self.lookup(), "specs/day-07-example.md")

    def test_ordinary_merge(self):
        self.merge(subject="Merge pull request #9 from owner/tooling/example")
        self.assertIsNone(self.lookup())

    def test_direct_commit(self):
        self.assertIsNone(self.lookup())  # A root commit has no parents.
        self.git("commit", "-q", "--allow-empty", "-m",
                 "Merge pull request #9 from owner/day-07/close-the-day")
        self.assertIsNone(self.lookup())

    def test_missing_spec(self):
        self.merge()
        with self.assertRaisesRegex(ValueError, "day-07: expected one spec, found 0"):
            self.lookup()

    def test_two_specs(self):
        self.merge(("day-07-a.md", "day-07-b.md"))
        with self.assertRaisesRegex(ValueError, "day-07: expected one spec, found 2"):
            self.lookup()

    def squash(self, subject):
        os.makedirs(os.path.join(self.repo, "specs"), exist_ok=True)
        with open(os.path.join(self.repo, "specs", "day-01-example.md"), "w") as file:
            file.write("# Day 01\n")
        self.git("add", ".")
        self.git("commit", "-q", "--allow-empty", "-m", subject)
        return self.git("rev-parse", "HEAD").strip()

    def test_squash_spec(self):
        self.squash("Day 01 close: tick criteria (#2)")
        self.assertEqual(self.lookup(), "specs/day-01-example.md")

    def test_squash_close_commit(self):
        sha = self.squash("Day 01 close: tick criteria (#2)")
        with patch("evidence.git", side_effect=self.git):
            self.assertEqual(close_commit("01", "main"), sha)

    def test_close_without_pr_number(self):
        self.squash("Day 01 close")
        self.assertIsNone(self.lookup())

    def test_track_squash_is_not_close(self):
        self.squash("Day 01 track A: x (#3)")
        self.assertIsNone(self.lookup())

    def test_tracker_key_squash(self):
        self.squash("ABC-12 Day 01 close: tick criteria (#2)")
        with patch.dict(os.environ, {"JIRA_KEY": "ABC"}):
            self.assertEqual(self.lookup(), "specs/day-01-example.md")

    def test_latest_close_wins(self):
        self.squash("Day 01 close: first (#2)")
        latest = self.squash("Day 01 close: second (#3)")
        with patch("evidence.git", side_effect=self.git):
            self.assertEqual(close_commit("01", "main"), latest)

    def test_merge_with_squash_subject_is_not_close(self):
        self.merge(subject="Day 01 close: wrong branch (#2)")
        self.assertIsNone(self.lookup())
        with patch("evidence.git", side_effect=self.git):
            self.assertIsNone(close_commit("01", "main"))


if __name__ == "__main__":
    unittest.main()
