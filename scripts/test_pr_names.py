"""scripts/pr-names.py finds a branch, title, commit or description that disagrees with the rest.

The three escapes it was written for are the first tests, under neutral names; the rest take one
rule each away from a pull request named the way this repository names them. The key is `ABC`,
set the way a project sets it, in JIRA_KEY.

    python scripts/test_pr_names.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "pr-names.py")
GENERIC = ("workflow", "tooling", "dashboard", "docs", "specs", "plan", "ci", "change")


def load(key=None):
    """pr-names.py as loaded with JIRA_KEY unset, or set to key."""
    saved = os.environ.pop("JIRA_KEY", None)
    if key is not None:
        os.environ["JIRA_KEY"] = key
    try:
        spec = importlib.util.spec_from_file_location("pr_names", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.pop("JIRA_KEY", None)
        if saved is not None:
            os.environ["JIRA_KEY"] = saved


pr_names = load("ABC")
problems = pr_names.problems

TRACK = ("day-43/track-d2-ABC-166-outward-docs", "Day 43 track D2: the README says where the work ended",
         "Sub-task ABC-166 of ABC-152.", ["ABC-166 Day 43 track D2: the README says where the work ended"])


class EscapesTest(unittest.TestCase):
    def test_a_branch_that_names_part_of_a_track(self):
        found = problems("day-22/track-b-search-client", "Day 22 track B1: the search client and its index", "", [])
        self.assertEqual(["the branch is Day 22 track B, the title Day 22 track B1"], found)

    def test_a_description_with_another_key(self):
        found = problems("day-27/track-a-ABC-5-orders-consumer", "Day 27 track A: the orders consumer",
                         "Closes ABC-6.", ["ABC-5 Day 27 track A: the orders consumer"])
        self.assertEqual(["the description names ABC-6, not ABC-5"], found)

    def test_a_branch_the_tool_named(self):
        found = problems("claude/focused-bell-qqwqdx", "ABC-82 Day 28 track D: stopping-point review", "",
                         ["ABC-82 Day 28 track D: stopping-point review"])
        self.assertEqual(3, len(found), found)
        self.assertIn("is neither day-NN/<slug>", found[0])


class RulesTest(unittest.TestCase):
    def test_names_that_agree_pass(self):
        self.assertEqual([], problems(*TRACK))
        self.assertEqual([], problems("day-35/track-c2a-ABC-137-deploy-job",
                                      "Day 35 track C2a (1 of 2): the deploy job in four service workflows", "", []))
        self.assertEqual([], problems("day-38/track-c-fix-harness-waits-for-ready",
                                      "Day 38 track C fix: the harness waits until the gateway is ready", "", []))
        self.assertEqual([], problems("day-42/close-the-day-ABC-97", "Day 42 close: tick the criteria", "ABC-97", []))
        self.assertEqual([], problems("day-43/cost-ABC-171-c35-22", "Day 35 C35.22: the run cost 0.40 USD", "", []))
        self.assertEqual([], problems("workflow/pr-names", "Workflow: branch names, titles and Jira keys agree", "", []))
        self.assertEqual([], problems("day-43/close-the-day", "Day 43 close, part 2: Notes", "",
                                      ["ABC-168 Day 43 close, part 2: Notes"]))

    def test_track_on_one_side_only(self):
        self.assertIn("the title has no", problems("day-43/track-d2-x", "Day 43: the README", "", [])[0])
        self.assertIn("has no `track-x-`", problems("day-43/readme", "Day 43 track D2: the README", "", [])[0])

    def test_track_of_another_day(self):
        self.assertEqual(["the branch is Day 43 track D2, the title Day 42 track D2"],
                         problems("day-43/track-d2-x", "Day 42 track D2: the README", "", []))

    def test_title_day_on_a_branch_without_one(self):
        self.assertEqual(["the title names Day 43; the branch `docs/readme` names no day"],
                         problems("docs/readme", "Day 43: the README", "", []))

    def test_close_from_another_branch(self):
        self.assertIn("comes from day-43/close-the-day", problems("day-43/notes", "Day 43 close: Notes", "", [])[0])
        self.assertIn("comes from day-43/close-the-day",
                      problems("day-42/close-the-day", "Day 43 close: Notes", "", [])[0])

    def test_a_second_key_in_a_commit(self):
        branch, title, body, subjects = TRACK
        self.assertEqual(["the branch, the title and the commits name 2 Jira keys: ABC-166, ABC-167"],
                         problems(branch, title, body, subjects + ["ABC-167 fix the link"]))

    def test_a_second_key_in_the_title(self):
        branch, title, body, subjects = TRACK
        self.assertEqual(["the branch, the title and the commits name 2 Jira keys: ABC-166, ABC-46"],
                         problems(branch, "ABC-46 " + title, body, subjects))

    def test_a_description_that_names_the_day_and_not_the_sub_task(self):
        branch, title, _, subjects = TRACK
        self.assertEqual(["the description names ABC-152, not ABC-166"],
                         problems(branch, title, "Day issue ABC-152.", subjects))


class KeyTest(unittest.TestCase):
    """JIRA_KEY names the project key; unset or empty, no key is checked."""
    MISMATCH = ("day-27/track-a-ABC-5-x", "Day 27 track A: x", "Closes ABC-6.", ["ABC-5 Day 27 track A: x"])
    CLOSE = ("day-42/close-the-day-XYZ-97", "XYZ-97 Day 43 close: tick the criteria", "XYZ-97", [])

    def test_unset_or_empty_no_key_is_checked(self):
        for module in (load(), load("")):  # an unset CI variable arrives empty
            self.assertEqual([], module.problems(*self.MISMATCH))
            self.assertEqual([], module.problems(*TRACK[:3], TRACK[3] + ["ABC-167 fix the link"]))
            self.assertEqual([], module.problems(*self.CLOSE))
            self.assertEqual("", module.JIRA_KEY)

    def test_a_key_set_in_jira_key_is_the_key_and_another_is_text(self):
        xyz = load("XYZ")
        self.assertEqual(["the description names XYZ-7, not XYZ-166"],
                         xyz.problems("day-43/track-d2-XYZ-166-x", "Day 43 track D2: x", "Closes XYZ-7.",
                                      ["XYZ-166 Day 43 track D2: x"]))
        self.assertEqual(["a Day 43 close comes from day-43/close-the-day, not `day-42/close-the-day-XYZ-97`"],
                         xyz.problems(*self.CLOSE))
        self.assertEqual([], xyz.problems(*self.MISMATCH))
        self.assertEqual([], problems(*self.CLOSE))

    def test_compare_the_names_reads_jira_key_from_the_repository_variable(self):
        with open(os.path.join(HERE, "..", ".github", "workflows", "pr-checks.yml"), encoding="utf-8") as f:
            step = f.read().split("- name: Compare the names", 1)[1].split("run:", 1)[0]
        self.assertIn("JIRA_KEY: ${{ vars.JIRA_KEY }}", step)


class AreasTest(unittest.TestCase):
    """AREAS: the generic ones, then the ones the project file adds (`areas`)."""

    def test_change_branch_is_allowed(self):
        self.assertEqual([], problems("change/double", "Double strings", "", []))

    def test_the_project_file_adds_areas_to_the_generic_ones(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "spec-drift.config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"areas": ["research", "security"]}, f)
        self.assertEqual(("research", "security"), pr_names.project_areas(path))
        self.assertEqual((), pr_names.project_areas(os.path.join(folder, "none.json")))
        self.assertEqual(GENERIC + pr_names.project_areas(), pr_names.AREAS)

        self.addCleanup(setattr, pr_names, "AREAS", pr_names.AREAS)
        pr_names.AREAS = GENERIC
        self.assertIn("is neither day-NN/<slug>", problems("research/x", "Research: x", "", [])[0])
        pr_names.AREAS = GENERIC + pr_names.project_areas(path)
        self.assertEqual([], problems("research/x", "Research: x", "", []))


class CommandTest(unittest.TestCase):
    def test_reads_the_commits_from_git_and_exits_1(self):
        repo = tempfile.mkdtemp()
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "base"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "ABC-9 Day 43 track D2: x"], check=True)
        env = dict(os.environ, PR_BRANCH="day-43/track-d2-ABC-166-x", PR_TITLE="Day 43 track D2: x", PR_BODY="",
                   JIRA_KEY="ABC")
        run = subprocess.run([sys.executable, SCRIPT, "HEAD~1", "HEAD"], cwd=repo, env=env,
                             capture_output=True, text=True)
        self.assertEqual(1, run.returncode, run.stdout + run.stderr)
        self.assertIn("2 Jira keys: ABC-166, ABC-9", run.stdout)
        self.assertIn("1 problems over the branch, the title and 1 commits", run.stdout)

    def test_with_no_jira_key_no_key_is_checked(self):
        repo = tempfile.mkdtemp()
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "base"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "ABC-9 Day 43 track D2: x"], check=True)
        env = dict(os.environ, PR_BRANCH="day-43/track-d2-ABC-166-x", PR_TITLE="Day 43 track D2: x", PR_BODY="")
        env.pop("JIRA_KEY", None)  # whatever the shell running the tests has set
        run = subprocess.run([sys.executable, SCRIPT, "HEAD~1", "HEAD"], cwd=repo, env=env,
                             capture_output=True, text=True)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertIn("0 problems over the branch, the title and 1 commits", run.stdout)


if __name__ == "__main__":
    unittest.main()
