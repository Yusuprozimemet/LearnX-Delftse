"""The frozen files stay frozen, and the moves and additions around them pass.

Each test builds a scratch git repository with a base commit holding the project file's `frozen`
patterns and `test_count` rule, one applied migration, a contract test, the two harness self-tests,
an unfrozen test and an unprotected file, changes it on a branch, and runs scripts/invariants.py over the branch as a subprocess. One more
reads .github/workflows/pr-checks.yml: the job that runs it keeps the name branch protection requires.

    python scripts/test_invariants.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "invariants.py")
WORKFLOW = os.path.join(HERE, "..", ".github", "workflows", "pr-checks.yml")

SERVICE = "services/account-service"
TESTS = SERVICE + "/src/test/java/com/example/core"
MIGRATION = SERVICE + "/src/main/resources/db/migration/V1__init.sql"
CONTRACT = TESTS + "/contract/LoginContractTest.java"
HARNESS = TESTS + "/support/TestHarnessTest.java"
APP_TESTS = TESTS + "/CoreApplicationTests.java"
UNIT_TESTS = TESTS + "/LoginServiceTest.java"
README = SERVICE + "/README.md"
CONFIG = "spec-drift.config.json"

CORE_TESTS = r"^services/account-service/(.+/)?src/test/java/com/example/core/"
FROZEN = [
    r"^services/.+/db/(.+/)?V[^/]*__[^/]*\.sql$",
    CORE_TESTS + r"contract/",
    CORE_TESTS + r"support/TestHarnessTest\.java$",
    CORE_TESTS + r"CoreApplicationTests\.java$",
]
TEST_COUNT = {"files": r"/src/test/java/.+\.java$", "test": r"^\s*void \w+\(\)"}

BASE = {
    MIGRATION: "".join(f"CREATE TABLE users_{i} (id BIGINT PRIMARY KEY);\n" for i in range(10)),
    CONTRACT: "class LoginContractTest {\n    void logsIn() {}\n}\n",
    HARNESS: "class TestHarnessTest {\n    void starts() {}\n}\n",
    APP_TESTS: "class CoreApplicationTests {\n    void loads() {}\n}\n",
    UNIT_TESTS: "class LoginServiceTest {\n    void accepts() {}\n    void refuses() {}\n}\n",
    README: "# account\n",
}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def write(repo, path, text):
    full = os.path.join(repo, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class InvariantsTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.repo = self.folder.name
        git(self.repo, "init", "-q", "-b", "main")
        # Renames are only 100 when git does not rewrite line endings on checkout (Windows).
        git(self.repo, "config", "core.autocrlf", "false")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "commit.gpgsign", "false")
        for path, text in BASE.items():
            write(self.repo, path, text)
        self.commit("the files, nothing frozen")
        git(self.repo, "tag", "unfrozen")
        write(self.repo, CONFIG, json.dumps({"frozen": FROZEN, "test_count": TEST_COUNT}))
        self.commit("base")
        git(self.repo, "checkout", "-q", "-b", "change")

    def tearDown(self):
        self.folder.cleanup()

    def commit(self, message):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)

    def run_script(self, base="main"):
        return subprocess.run(
            [sys.executable, SCRIPT, base, "change"],
            cwd=self.repo, capture_output=True, text=True, encoding="utf-8",
        )

    def test_an_edit_of_an_applied_migration_fails(self):
        write(self.repo, MIGRATION, BASE[MIGRATION] + "ALTER TABLE users_0 ADD COLUMN x INT;\n")
        self.commit("edit V1")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"modified: {MIGRATION}", result.stdout)

    def test_a_deleted_contract_test_fails(self):
        os.remove(os.path.join(self.repo, *CONTRACT.split("/")))
        self.commit("delete the contract test")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"deleted: {CONTRACT}", result.stdout)

    def test_an_edit_of_the_harness_self_test_fails(self):
        write(self.repo, HARNESS, "class TestHarnessTest {\n    void starts() { }\n}\n")
        self.commit("edit the harness self-test")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"modified: {HARNESS}", result.stdout)

    def test_an_edit_of_the_application_tests_fails(self):
        write(self.repo, APP_TESTS, "class CoreApplicationTests {\n    void loads() { }\n}\n")
        self.commit("edit CoreApplicationTests")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"modified: {APP_TESTS}", result.stdout)

    def test_a_new_migration_passes(self):
        added = SERVICE + "/src/main/resources/db/migration/V2__more.sql"
        write(self.repo, added, "CREATE TABLE jobs (id BIGINT PRIMARY KEY);\n")
        self.commit("add V2")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("OK", result.stdout)

    def test_a_pure_rename_of_the_whole_tree_passes(self):
        # Moves the src tree one level down, contents identical: every file is R100.
        os.makedirs(os.path.join(self.repo, SERVICE, "app"))
        os.rename(os.path.join(self.repo, SERVICE, "src"),
                  os.path.join(self.repo, SERVICE, "app", "src"))
        self.commit("move src down one level")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("OK", result.stdout)

    def test_a_rename_with_an_edit_fails_and_names_the_old_path(self):
        moved = SERVICE + "/src/main/resources/db/V1__init.sql"
        os.makedirs(os.path.dirname(os.path.join(self.repo, *moved.split("/"))), exist_ok=True)
        os.rename(os.path.join(self.repo, *MIGRATION.split("/")),
                  os.path.join(self.repo, *moved.split("/")))
        write(self.repo, moved, BASE[MIGRATION].replace("users_0", "accounts_0"))
        self.commit("move V1 and edit it")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("renamed with an edit", result.stdout)
        self.assertIn(MIGRATION, result.stdout)

    def test_a_pure_rename_out_of_the_frozen_set_fails_and_names_both_paths(self):
        # R100, but the new path matches no pattern: the contract test would no longer be frozen.
        moved = TESTS + "/LoginContractTest.java"
        os.rename(os.path.join(self.repo, *CONTRACT.split("/")), os.path.join(self.repo, *moved.split("/")))
        self.commit("move the contract test out of contract/")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"renamed out of the frozen set (R100): {CONTRACT} -> {moved}", result.stdout)

    def test_a_lost_unfrozen_test_fails_with_both_counts(self):
        write(self.repo, UNIT_TESTS, "class LoginServiceTest {\n    void accepts() {}\n}\n")
        self.commit("drop a test")
        result = self.run_script()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("test count dropped: 5 where the branch left the base, 4 at head", result.stdout)

    def test_the_count_is_from_where_the_branch_left_so_tests_the_base_gained_since_do_not_fail_it(self):
        git(self.repo, "checkout", "-q", "main")
        write(self.repo, TESTS + "/LogoutServiceTest.java", "class LogoutServiceTest {\n    void ends() {}\n}\n")
        self.commit("a test on main after the branch")
        git(self.repo, "checkout", "-q", "change")
        write(self.repo, README, "# account, edited\n")
        self.commit("edit the README")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("5 tests before and 5 after", result.stdout)

    def test_with_nothing_frozen_it_says_so_and_passes(self):
        write(self.repo, MIGRATION, BASE[MIGRATION] + "ALTER TABLE users_0 ADD COLUMN x INT;\n")
        self.commit("edit V1")
        result = self.run_script(base="unfrozen")
        self.assertEqual("Nothing frozen: spec-drift.config.json sets no `frozen` patterns.\n", result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_patterns_are_the_bases_so_a_change_cannot_unfreeze_what_it_edits(self):
        write(self.repo, CONFIG, json.dumps({"frozen": []}))
        write(self.repo, MIGRATION, BASE[MIGRATION] + "ALTER TABLE users_0 ADD COLUMN x INT;\n")
        self.commit("unfreeze and edit V1")
        result = self.run_script()
        self.assertIn(f"modified: {MIGRATION}", result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_an_edit_of_an_unprotected_file_passes(self):
        write(self.repo, README, "# account, edited\n")
        self.commit("edit the README")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("OK", result.stdout)


class JobNameTest(unittest.TestCase):
    def test_the_job_that_runs_it_is_named_for_frozen_files(self):
        # Branch protection requires the check by this name, so a rename is a settings change too.
        with open(WORKFLOW, encoding="utf-8") as f:
            jobs = re.split(r"\n  (?=[\w-]+:\n)", f.read().split("\njobs:\n", 1)[1])
        names = [re.search(r"^    name: (.+)$", job, re.M).group(1).strip()
                 for job in jobs if "scripts/invariants.py" in job]
        self.assertEqual(names, ["Frozen files stay unedited"])


if __name__ == "__main__":
    unittest.main()
