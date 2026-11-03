"""Tests for the generated index of criteria in force."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "behaviour-index.py")


class BehaviourIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.specs = self.temp.name

    def write(self, name, body, binary=False):
        path = os.path.join(self.specs, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = body.encode("utf-8")
        if binary:
            data = data.replace(b"\n", b"\r\n")
            with open(path, "wb") as f:
                f.write(data)
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(body)

    def run_index(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "--specs", self.specs, *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_short_spec_is_in_force_after_days_and_in_json(self):
        self.write("day-01-a.md", "## Acceptance criteria\n- [x] C01.1 **hold** — Day.\n")
        self.write("changes/x.md", "## Acceptance criteria\n- [x] S-x.1 **new** — Short. Test: `XTest`\n")
        path = os.path.join(self.specs, "rows.json")
        result = self.run_index("--json", path)
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertLess(result.stdout.index("| 01 |"), result.stdout.index("| - | S-x.1 |"))
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        self.assertEqual((None, "changes/x.md", ["XTest"]),
                         (rows[-1]["day"], rows[-1]["spec"], rows[-1]["tests"]))

    def test_day_replaces_short_criterion(self):
        self.write("changes/x.md", "## Acceptance criteria\n- [x] S-x.1 **new** — Old.\n")
        self.write("day-01-a.md", "## Acceptance criteria\n- [x] C01.1 **new** — New.\n"
                   "      Replaces: S-x.1 — was: Old.\n")
        result = self.run_index()
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("1 criteria in force, 1 replaced, 0 unticked", result.stdout)
        self.assertNotIn("| - | S-x.1 |", result.stdout)

    def test_short_spec_cannot_replace(self):
        self.write("changes/x.md", "## Acceptance criteria\n- [x] S-x.1 **new** — New.\n"
                   "      Replaces: C01.1 — was: Old.\n")
        result = self.run_index()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Replaces: is not allowed in a short spec", result.stdout)

    def test_ticked_is_in_force_and_unticked_is_not(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **new** — First claim. Test: `FirstTest`\n- [ ] C01.2 **hold** — Open claim.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 criteria in force, 0 replaced, 1 unticked", result.stdout)
        self.assertIn("C01.1", result.stdout)
        self.assertNotIn("C01.2", result.stdout)

    def test_later_ticked_replacement_retires_earlier(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **hold** — Old behaviour. Test: `OldTest`\n")
        self.write("day-02-next.md", "## Acceptance criteria\n- [x] C02.1 **new** — New behaviour.\n      Replaces: C01.1 — was: Old behaviour.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 criteria in force, 1 replaced, 0 unticked", result.stdout)
        self.assertNotIn("| 01 | C01.1 |", result.stdout)
        self.assertIn("| 02 | C02.1 |", result.stdout)

    def test_unticked_replacement_retires_nothing(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **hold** — Old behaviour.\n")
        self.write("day-02-next.md", "## Acceptance criteria\n- [ ] C02.1 **new** — Later.\n      Replaces: C01.1 — was: Old behaviour.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 criteria in force, 0 replaced, 1 unticked", result.stdout)
        self.assertIn("| 01 | C01.1 |", result.stdout)

    def test_one_line_can_replace_several_ids(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **hold** — A.\n- [x] C01.2 **hold** — B.\n")
        self.write("day-02-next.md", "## Acceptance criteria\n- [x] C02.1 **new** — C.\n      Replaces: C01.1, C01.2 — was: A and B.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 criteria in force, 2 replaced, 0 unticked", result.stdout)

    def test_unknown_replacement_id_is_a_problem(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **new** — A.\n      Replaces: C09.9 — was: Missing.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown replacement IDs", result.stdout)

    def test_replacement_must_name_earlier_day(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **new** — A.\n      Replaces: C02.1 — was: Later.\n")
        self.write("day-02-next.md", "## Acceptance criteria\n- [x] C02.1 **hold** — B.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Replacement is not from an earlier day", result.stdout)

    def test_criterion_cannot_be_replaced_twice(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **hold** — A.\n")
        self.write("day-02-next.md", "## Acceptance criteria\n- [x] C02.1 **new** — B.\n      Replaces: C01.1 — was: A.\n")
        self.write("day-03-last.md", "## Acceptance criteria\n- [x] C03.1 **new** — C.\n      Replaces: C01.1 — was: A.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Criteria replaced more than once", result.stdout)

    def test_empty_folder_succeeds(self):
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 criteria in force", result.stdout)

    def test_json_matches_table_rows_as_parsed_data(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **new** — Claim. Test: `FirstTest`\n")
        path = os.path.join(self.temp.name, "rows.json")
        result = self.run_index("--json", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        self.assertEqual(rows, [{"day": 1, "id": "C01.1", "tag": "new", "claim": "Claim. Test: `FirstTest`",
                                 "tests": ["FirstTest"], "spec": "day-01-first.md", "state": "in force"}])

    def test_crlf_spec_is_read(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **hold** — Claim.\n", binary=True)
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("C01.1", result.stdout)

    def test_replaces_without_was_is_a_problem(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **hold** — A.\n")
        self.write("day-02-next.md", "## Acceptance criteria\n- [x] C02.1 **new** — B.\n      Replaces: C01.1\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Replaces: lines without an ID", result.stdout)

    def test_list_link_before_criteria_does_not_shift_replaces(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] C01.1 **hold** — A.\n- [x] C01.2 **hold** — B.\n")
        self.write("day-02-next.md", "## Acceptance criteria\n- [rules](README.md)\n"
                   "- [x] C02.1 **new** — C.\n- [x] C02.2 **new** — D.\n      Replaces: C01.2 — was: B.\n")
        result = self.run_index("--json", os.path.join(self.temp.name, "rows.json"))
        self.assertEqual(result.returncode, 0, result.stdout)
        with open(os.path.join(self.temp.name, "rows.json"), encoding="utf-8") as f:
            ids = [row["id"] for row in json.load(f)]
        self.assertEqual(ids, ["C01.1", "C02.1", "C02.2"])

    def test_days_are_ordered_by_number(self):
        self.write("day-99-a.md", "## Acceptance criteria\n- [x] C99.1 **hold** — A.\n")
        self.write("day-100-b.md", "## Acceptance criteria\n- [x] C100.1 **new** — B.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertLess(result.stdout.index("| 99 |"), result.stdout.index("| 100 |"))

    def test_missing_id_is_reported_and_exits_one(self):
        self.write("day-01-first.md", "## Acceptance criteria\n- [x] **new** — No identifier.\n")
        result = self.run_index()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Criteria without IDs", result.stdout)
        self.assertIn("| 01 |  |", result.stdout)


if __name__ == "__main__":
    unittest.main()
