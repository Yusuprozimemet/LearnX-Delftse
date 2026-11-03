"""The dashboard's numbers move when they should and only then.

Each drift test builds a small git repository, one commit per merge, and runs spec-drift.py's
trajectory over it; the roadmap tests check the run order, the statuses and the next step. Needs
git and numpy; the page tests run docs/dashboard/dashboard.js and need node.

    python scripts/test_spec_drift.py
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
spec = importlib.util.spec_from_file_location("spec_drift", os.path.join(HERE, "spec-drift.py"))
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)
COMPARE = os.path.join(HERE, "..", "kit", "compare-page.py")

PLAN = "# Plan\n\nThe plan names `FooService` and `bar_table`.\n"
DAY = "# Day 01\n\nThe day adds `FooService`, then the rest of the day.\n"
# The source project's file under neutral names: its two tests renamed to another stem,
# its six directory moves, its Verify base, its four services by package, and its infra workflow.
PROJECT = dict(
    renamed=[[296, "OrdersUserDeletedConsumerIT", "UserDeletedConsumerTest"],
             [298, "CircuitBreakerIT", "StockLookupBreakerTest"]],
    moved=[[174, "core/stock", "services/stock-service"], [224, "core/pricing", "services/pricing-service"],
           [296, "core/orders", "services/order-service"], [315, "core", "services/account-service"],
           [416, "services/account-service/docs", "docs/system"],
           [419, "services/account-service/app", "services/account-service"]],
    verify_bases=["core"], package_root="org.example.shop",
    services=dict(auth="account", user="account", profile="account", account="account", stock="stock",
                  catalog="stock", saved="orders", orders="orders", pricing="pricing"),
    infra_workflow="infra.yaml")


def use_project(test, fields):
    """fields as the project file, read as spec-drift.py reads its own, until the test ends."""
    folder = tempfile.mkdtemp(prefix="spec-drift-config-")
    test.addCleanup(shutil.rmtree, folder, True)
    path = os.path.join(folder, "spec-drift.config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fields, f)
    for name, value in sd.project_config(path).items():
        test.addCleanup(setattr, sd, name, getattr(sd, name))
        setattr(sd, name, value)


class Repo:
    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="spec-drift-test-")
        self.git("init", "-q")
        self.snaps = []

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false",
                               *args], cwd=self.path, capture_output=True, text=True, check=True).stdout

    def merge(self, files):
        """One commit, standing in for one merge on main."""
        for name, text in files.items():
            full = os.path.join(self.path, name)
            if text is None:  # deleted, or moved when the new path is in the same merge
                os.remove(full)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"merge {len(self.snaps)}")
        n = len(self.snaps)
        self.snaps.append(dict(sha=self.git("rev-parse", "--short", "HEAD").strip(), date="2026-09-20",
                               pr=n or None, title=f"Day 01 track A: {n}", branch="day-01/track-a-x" if n else ""))
        return self

    def rows(self):
        return self.inside(lambda blobs: sd.trajectory([dict(s) for s in self.snaps], blobs)[0])

    def inside(self, fn):
        """fn(blobs), run in the repository."""
        cwd = os.getcwd()
        os.chdir(self.path)
        try:
            with sd.Blobs() as blobs:
                return fn(blobs)
        finally:
            os.chdir(cwd)

    def close(self):
        shutil.rmtree(self.path, ignore_errors=True)


class SpecDriftTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo().merge({"plan.md": PLAN, "specs/day-01-x.md": DAY, "src/App.java": "class App {}\n"})

    def tearDown(self):
        self.repo.close()

    def test_code_that_names_a_spec_name_narrows_the_gap(self):
        rows = self.repo.merge({"src/Foo.java": "class FooService {}\n"}).rows()
        self.assertIsNone(rows[0]["gap_full"])  # no spec name in the code: no direction to compare
        self.assertLess(rows[1]["gap_full"], 90.0)

    def test_name_coverage_is_the_share_of_spec_names_the_code_has(self):
        rows = self.repo.merge({"src/Foo.java": "class FooService { FooService f; }\n"}).rows()
        self.assertEqual((rows[0]["name_coverage"], rows[1]["name_coverage"]), (0.0, 0.5))  # bar_table not yet
        rows = self.repo.merge({"src/Bar.java": "bar_table b;\n"}).rows()
        self.assertEqual(rows[2]["name_coverage"], 1.0)
        rows = self.repo.merge({"plan.md": "# Plan\n\nThe plan names nothing.\n"}).rows()
        self.assertEqual(rows[3]["name_coverage"], 1.0)  # bar_table, in the code only, is not asked for

    def test_jaccard_counts_a_name_the_specs_dropped_but_the_code_keeps(self):
        rows = self.repo.merge({"src/Foo.java": "class FooService { bar_table b; }\n"}).rows()
        self.assertEqual((rows[1]["name_coverage"], rows[1]["jaccard"]), (1.0, 1.0))
        rows = self.repo.merge({"plan.md": "# Plan\n\nThe plan names nothing.\n"}).rows()
        self.assertEqual((rows[2]["name_coverage"], rows[2]["jaccard"]), (1.0, 0.5))  # bar_table: code only
        self.assertEqual((rows[1]["names_kept"], rows[2]["names_kept"]), (0, 1))

    def test_a_file_under_specs_is_never_code_whatever_its_extension(self):
        # A break manifest copies the spec's names: at #25 one moved the gap with no code changed.
        rows = self.repo.merge({"src/Foo.java": "class FooService {}\n"}).rows()
        rows = self.repo.merge({"specs/breaks/day-01.json": '{"names": ["FooService", "bar_table"]}\n',
                                "specs/notes.py": "bar_table = 1\n"}).rows()
        for field in ("gap_full", "name_coverage", "jaccard", "coverage"):
            self.assertEqual(rows[2][field], rows[1][field], field)
        self.assertEqual(rows[2]["name_coverage"], 0.5)  # bar_table is only under specs/

    def test_a_name_in_a_test_or_a_script_still_counts_as_code(self):
        rows = self.repo.merge({"tests/test_foo.py": "FooService = None\n", "scripts/bar.py": "bar_table = 1\n"}).rows()
        self.assertEqual(rows[1]["name_coverage"], 1.0)

    def test_a_name_only_in_a_test_or_a_script_is_code_but_not_production(self):
        self.addCleanup(sd.CACHE.clear)
        rows = self.repo.merge({"tests/test_foo.py": "FooService = None\n", "scripts/bar.py": "bar_table = 1\n",
                                "experiments/e1/cases.json": '{"names": "FooService bar_table"}\n'}).rows()
        self.assertEqual((rows[1]["name_coverage"], rows[1]["prod_name_coverage"]), (1.0, 0.0))
        self.assertEqual((rows[1]["spec_names"], rows[1]["names_in_code"], rows[1]["names_in_prod"]), (2, 2, 0))
        self.assertEqual(sd.CACHE["code"][self.repo.snaps[1]["sha"]],
                         {"FooService": {"tests/test_foo.py": 1, "experiments/e1/cases.json": 1},
                          "bar_table": {"scripts/bar.py": 1, "experiments/e1/cases.json": 1}})

    def test_a_name_outside_production_is_listed_once_with_each_place_and_path(self):
        rows = self.repo.merge({"tests/test_foo.py": "FooService = None\n", "scripts/foo.py": "FooService = 1\n",
                                "scripts/foo2.py": "FooService = 2\n"}).rows()
        self.assertEqual(rows[1]["names_outside_prod"],
                         {"FooService": {"test": ["tests/test_foo.py"], "tooling": ["scripts/foo.py", "scripts/foo2.py"]}})
        self.assertEqual(rows[1]["names_absent"], ["bar_table"])
        rows = self.repo.merge({"src/Foo.java": "class FooService {}\n"}).rows()
        self.assertEqual((rows[2]["names_outside_prod"], rows[2]["names_absent"]), ({}, ["bar_table"]))
        rows = self.repo.merge({"src/Foo.java": None}).rows()
        self.assertEqual(list(rows[3]["names_outside_prod"]), ["FooService"])
        rows = self.repo.merge({"tests/test_foo.py": None, "scripts/foo.py": None, "scripts/foo2.py": None}).rows()
        self.assertEqual((rows[4]["names_outside_prod"], rows[4]["names_absent"]), ({}, ["FooService", "bar_table"]))

    def test_a_name_in_production_raises_production_coverage_and_its_removal_lowers_it(self):
        self.repo.merge({"tests/test_foo.py": "FooService = None\n"})
        rows = self.repo.merge({"src/Foo.java": "class FooService {}\n"}).rows()
        self.assertEqual([r["prod_name_coverage"] for r in rows], [0.0, 0.0, 0.5])
        self.assertEqual([r["names_in_prod"] for r in rows], [0, 0, 1])
        rows = self.repo.merge({"src/Foo.java": None}).rows()
        self.assertEqual((rows[3]["name_coverage"], rows[3]["prod_name_coverage"]), (0.5, 0.0))

    def test_a_line_is_split_where_it_was_before_the_path_was_kept(self):
        # str.splitlines also ends a line at a form feed; git grep does not, so after a comment the name still counts.
        rows = self.repo.merge({"src/Foo.java": "// note\x0cclass FooService {}\n"}).rows()
        self.assertEqual((rows[1]["name_coverage"], rows[1]["names_in_prod"]), (0.5, 1))

    def test_production_coverage_skips_comments_and_with_no_spec_name_is_not_measured(self):
        rows = self.repo.merge({"src/Foo.java": "// FooService\nclass Foo {} // bar_table\n"}).rows()
        self.assertEqual((rows[1]["prod_name_coverage"], rows[1]["names_in_prod"]), (0.0, 0))
        rows = self.repo.merge({"plan.md": "# Plan\n", "specs/day-01-x.md": "# Day 01\n",
                                "src/Foo.java": "class FooService { bar_table b; }\n"}).rows()
        self.assertEqual((rows[2]["prod_name_coverage"], rows[2]["name_coverage"], rows[2]["spec_names"]), (None, None, 0))

    def test_a_failed_blob_read_names_the_commit_and_the_path(self):
        sha = self.repo.snaps[0]["sha"]

        def read_after_git_dies(blobs):
            self.assertEqual(blobs.read(sha, "no/such/file"), "")  # missing is not a failure
            blobs.proc.kill()
            blobs.proc.wait()
            return blobs.read(sha, "plan.md")
        with self.assertRaises(RuntimeError) as failed:
            self.repo.inside(read_after_git_dies)
        self.assertIn(f"plan.md at {sha}", str(failed.exception))

    def test_a_run_from_the_cache_reads_what_a_fresh_one_does(self):
        # Merge 1 is cached before any spec names BazClient; merge 2's spec does, which shifts
        # every name's place in the vocabulary.
        self.addCleanup(sd.CACHE.clear)
        self.repo.merge({"src/Baz.java": "class BazClient { FooService f; }\n"})
        sd.CACHE.clear()
        self.repo.rows()
        self.repo.merge({"specs/day-01-x.md": DAY + "It calls `BazClient`.\n", "src/Use.java": "BazClient b;\n"})
        calls, real = [], sd.run
        sd.run = lambda *a: calls.append(a) or real(*a)
        try:
            cached = self.repo.rows()
            sd.CACHE.clear()
            fresh_from = len(calls)
            fresh = self.repo.rows()
        finally:
            sd.run = real
        self.assertEqual(cached, fresh)
        self.assertLess(fresh_from, len(calls) - fresh_from)

    def test_a_comment_naming_a_spec_name_is_not_code(self):
        rows = self.repo.merge({"src/Foo.java": "// FooService comes later\n /* FooService */\n",
                                "src/V1.sql": "-- bar_table comes later\nSELECT 1; -- bar_table\n",
                                "run.sh": "# FooService\n"}).rows()
        self.assertIsNone(rows[1]["gap_full"])

    def test_a_later_spec_name_does_not_move_an_earlier_gap(self):
        self.repo.merge({"src/Foo.java": "class FooService { BazClient baz; }\n"})
        before = self.repo.rows()[1]["gap_full"]
        self.repo.merge({"specs/day-01-x.md": DAY + "It calls `BazClient`.\n"})
        self.assertEqual(self.repo.rows()[1]["gap_full"], before)

    def test_a_file_under_kit_is_tooling_and_not_code_the_gap_reads(self):
        self.repo.merge({"src/Foo.java": "class FooService {}\n"})
        rows = self.repo.merge({"kit/page/data.json": '{"names": "FooService bar_table bar_table bar_table"}\n'}).rows()
        self.assertEqual(rows[2]["gap_full"], rows[1]["gap_full"])
        self.assertEqual(rows[2]["work"], dict(spec=0, context=0, prod=0, test=0, tooling=1))

    def test_each_merge_counts_its_own_lines_by_box_and_a_move_only_what_changed(self):
        rows = self.repo.merge({"src/test/FooTest.java": "a\nb\n", "CLAUDE.md": "c\n", "specs/day-01-x.md": DAY + "d\n",
                                "scripts/x.py": "e\n", "package-lock.json": "{}\n", "src/Big.java": "f\ng\nh\ni\n"}).rows()
        self.assertEqual(rows[1]["work"], dict(spec=1, context=1, prod=4, test=2, tooling=1))
        os.remove(os.path.join(self.repo.path, "src", "Big.java"))
        rows = self.repo.merge({"src/test/BigTest.java": "f\ng\nh\ni\nj\n"}).rows()
        self.assertEqual(rows[2]["work"], dict(spec=0, context=0, prod=0, test=1, tooling=0))

    def test_a_file_goes_in_the_first_box_its_path_matches(self):
        for path, box in [("plan.md", "spec"), ("specs/README.md", "spec"), ("specs/day-01-test-harness.md", "spec"),
                          ("CLAUDE.md", "context"), ("backend/app/README.md", "context"), (".claude/agents/x.md", "context"),
                          (".claude/settings.json", "context"), ("docs/workflow.svg", "context"),
                          ("backend/app/src/test/java/x/support/Harness.java", "test"), ("services/job/src/FooIT.java", "test"),
                          ("scripts/test_spec_drift.py", "test"), ("data/tests/conftest.py", "test"), ("web/a.spec.ts", "test"),
                          ("scripts/spec-drift.py", "tooling"), (".github/workflows/ci.yml", "tooling"),
                          ("docs/dashboard/dashboard.js", "tooling"), ("backend/app/src/main/java/x/Latest.java", "prod"),
                          ("app/count.py", "prod"), ("tests/contract/test_word_rule.py", "test"),
                          ("experiments/jev/e1/build.py", "tooling"), ("experiments/jev/e1/test_build.py", "test"),
                          ("docker-compose.yml", "prod"), ("docs/dashboard/token-usage.json", None),
                          ("docs/dashboard/deployment.json", None),
                          ("frontend/package-lock.json", None), ("screenshots/a.png", None)]:
            self.assertEqual(sd.work_kind(path), box, path)
        self.assertEqual(sd.renamed_to("backend/{app => job}/src/X.java"), "backend/job/src/X.java")
        self.assertEqual(sd.renamed_to("src/{ => job}/X.java"), "src/job/X.java")
        self.assertEqual(sd.renamed_to("a.txt => b/a.txt"), "b/a.txt")

    def test_a_pr_records_a_break_in_one_fixed_line_and_nothing_else(self):
        checklist = "- [x] Spec changes only: each check has been seen to fail (see `specs/README.md`)\n"
        for body, state in [("broken: `ISSUER` back → `AuthIT` failed 2 of 8\n", "recorded"),
                            ("- broken: the guard removed -> expected: 401 but was: 200\n", "recorded"),
                            ("broken: none → this track adds no test\nbroken: the sort reversed → 3 red\n", "recorded"),
                            ("broken: none → this track adds no test\n", "none"),
                            # The free text the regexes read before #310 is not the line.
                            ("**Broken on purpose:** `ISSUER` back. It fails 2 of 8.\n", "silent"),
                            ("Seen red, broken: the guard removed → it failed\n", "silent"),
                            ("broken: the guard removed\n", "silent"),
                            ("", "silent"), (None, "silent")]:
            self.assertEqual(sd.break_state(body and body + checklist, 310), state, body)
        self.assertEqual(sd.break_state(checklist, 310), "silent")
        self.addCleanup(setattr, sd, "BREAK_LINE_FROM_PR", sd.BREAK_LINE_FROM_PR)
        sd.BREAK_LINE_FROM_PR = 310  # the source project asked for the line from #310
        self.assertEqual(sd.break_state("broken: the guard removed → 401\n", 309), "not measured")

    def test_a_new_project_reads_the_break_line_from_its_first_pr(self):
        self.assertEqual(sd.BREAK_LINE_FROM_PR, 0)
        self.assertEqual(sd.break_state("broken: the guard removed → 401\n", 1), "recorded")
        self.assertEqual(sd.break_state("No break.\n", 7), "silent")

    def test_ci_counts_each_pushed_commit_once_and_only_pull_request_runs(self):
        run = lambda sha, result, flow="PR checks", event="pull_request": dict(
            headBranch="day-01/track-a-x", headSha=sha, conclusion=result, workflowName=flow, event=event)
        runs = [run("a", "failure"), run("a", "failure", "Backend CI/CD"), run("a", "success", "Dashboard"),
                run("b", "success"), run("b", "cancelled", "Backend CI/CD"), run("c", "failure", event="push")]
        self.assertEqual(sd.ci_pushes(runs), {"day-01/track-a-x": (2, 1, ["Backend CI/CD", "PR checks"])})

    def test_a_gh_list_as_long_as_its_limit_is_refused(self):
        real = sd.run
        try:
            sd.run = lambda *args: "[" + ",".join(["{}"] * n) + "]"
            n = 2
            self.assertEqual(len(sd.gh_list(3, "pr", "list")), 2)
            n = 3
            with self.assertRaisesRegex(SystemExit, "pr list returned its limit of 3"):
                sd.gh_list(3, "pr", "list")
        finally:
            sd.run = real

    def test_a_gh_list_of_a_thousand_is_refused_under_a_larger_limit(self):
        # GitHub gives at most 1,000 for a filtered run list, so 1,000 may be cut whatever the limit.
        real = sd.run
        try:
            sd.run = lambda *args: "[" + ",".join(["{}"] * 1000) + "]"
            with self.assertRaisesRegex(SystemExit, "run list returned 1000"):
                sd.gh_list(5000, "run", "list")
            sd.run = lambda *args: "[" + ",".join(["{}"] * 999) + "]"
            self.assertEqual(len(sd.gh_list(5000, "run", "list")), 999)
        finally:
            sd.run = real

    def test_an_infra_run_whose_artifact_does_not_download_is_skipped(self):
        def fake(*args):
            if args[2] == "list":
                self.assertEqual(args[4], "infra.yaml")
                return json.dumps([dict(databaseId=2, headSha="b" * 40, conclusion="failure",
                                        createdAt="2026-10-08T10:00:00Z", url="u2"),
                                   dict(databaseId=1, headSha="a" * 40, conclusion="success",
                                        createdAt="2026-10-07T10:00:00Z", url="u1")])
            if args[3] == "2":
                raise subprocess.CalledProcessError(1, args)
            with open(os.path.join(args[-1], "infra-summary.json"), "w", encoding="utf-8") as f:
                json.dump({"criteria": {"C32.2": "pass"}}, f)
            return ""
        real = sd.run
        try:
            sd.run = fake
            got = sd.infra("infra.yaml")
        finally:
            sd.run = real
        self.assertEqual([r["sha"] for r in got["runs"]], ["bbbbbbb", "aaaaaaa"])
        self.assertEqual(got["latest"], dict(sha="aaaaaaa", conclusion="success", date="2026-10-07", url="u1",
                                             summary={"criteria": {"C32.2": "pass"}}))

    def test_with_no_github_remote_there_are_no_prs_and_a_note_says_so(self):
        def fake(*args):
            if args[1:3] == ("repo", "view"):
                if remote:
                    return "me"
                raise subprocess.CalledProcessError(1, args, stderr="no git remotes found")
            if args[1:3] == ("pr", "list"):
                return json.dumps([dict(number=1, author=dict(login="me")), dict(number=2, author=dict(login="x"))])
            return "[]"
        real = sd.run
        try:
            sd.run = fake
            remote = False
            self.assertEqual(sd.github(), ([], [], sd.NO_REMOTE))
            remote = True
            self.assertEqual(sd.github(), ([dict(number=1, author=dict(login="me"))], [], None))
        finally:
            sd.run = real
        self.assertTrue(sd.NO_REMOTE.startswith("No GitHub remote"))

    def test_the_deployment_is_its_checks_and_none_without_the_file(self):
        real = sd.PAGE
        sd.PAGE = tempfile.mkdtemp()
        try:
            self.assertIsNone(sd.deployment())
            checks = [dict(criterion="C35.16", name="the site answers", ok=True, detail="200", at="2026-10-10T09:00:00Z"),
                      dict(criterion="C35.17", name="login sets the cookie", ok=False, detail="no Secure",
                           at="2026-10-10T09:01:00Z")]
            with open(os.path.join(sd.PAGE, "deployment.json"), "w", encoding="utf-8") as f:
                json.dump(dict(domain="example.test", checkedAt="2026-10-10T09:01:00Z", checks=checks), f)
            self.assertEqual(sd.deployment()["checks"], checks)
        finally:
            shutil.rmtree(sd.PAGE)
            sd.PAGE = real

    def test_the_data_names_the_commit_versions_and_draws_that_made_it(self):
        p = sd.provenance()
        self.assertRegex(p["commit"], "^[0-9a-f]{40}$")
        self.assertEqual((p["numpy"], p["chance_draws"]), (sd.np.__version__, sd.CHANCE_DRAWS))
        self.assertRegex(p["python"], r"^3\.\d+\.\d+")
        self.assertIsInstance(p["script_edited"], bool)

    def test_before_the_first_commit_the_data_names_no_commit(self):
        # a pilot's first run: git rev-parse HEAD exited 128 in a repository with no commit yet.
        empty = Repo()
        self.addCleanup(empty.close)
        script = os.path.join(empty.path, "spec-drift.py")
        open(script, "w").close()
        self.addCleanup(setattr, sd, "__file__", sd.__file__)
        sd.__file__ = script
        p = empty.inside(lambda blobs: sd.provenance())
        self.assertEqual((p["commit"], p["script_edited"]), (None, True))

    def test_the_spec_rules_and_template_are_not_specs_for_the_vocabulary(self):
        # a pilot: 5 of the 8 names "missing" from its code were the source project's examples there.
        before = self.repo.inside(lambda blobs: sd.trajectory([dict(s) for s in self.repo.snaps], blobs)[2])
        self.repo.merge({"specs/README.md": "Name `AccountDeletionIT` and `savedCount`.\n",
                         "specs/_template.md": "- [ ] C28.1 **new** — `SameSite` is set.\n"})
        rows, _, vocab = self.repo.inside(lambda blobs: sd.trajectory([dict(s) for s in self.repo.snaps], blobs)[:3])
        self.assertEqual(vocab, before)
        self.assertEqual(rows[-1]["name_coverage"], rows[0]["name_coverage"])

    def test_a_day_that_runs_later_is_not_reached_by_a_higher_number(self):
        self.repo.merge({"specs/day-17-x.md": "Day 17 adds `LaterThing`.\n",
                         "specs/day-38-x.md": "Day 38 adds `PlatformThing`.\n"})
        self.repo.merge({"src/P.java": "class PlatformThing { FooService foo; }\n"})
        self.repo.snaps[-1]["branch"] = "day-38/track-a-x"
        by_number = self.repo.rows()[-1]["coverage"]
        rows, files = self.repo.inside(lambda blobs: sd.trajectory([dict(s) for s in self.repo.snaps], blobs,
                                                                   [1, 38, 17])[:2])
        self.assertEqual((by_number, rows[-1]["coverage"]), (0.667, 1.0))  # LaterThing is Day 17's
        self.assertEqual(files, ["plan.md", "specs/day-01-x.md", "specs/day-38-x.md", "specs/day-17-x.md"])
        self.repo.merge({"src/L.java": "class LaterThing {}\n"})
        self.repo.snaps[-1]["branch"] = "day-17/track-a-y"
        rows = self.repo.inside(lambda blobs: sd.trajectory([dict(s) for s in self.repo.snaps], blobs, [1, 38, 17])[0])
        self.assertEqual(rows[-1]["day"], 17)  # after 38 in the run order, though lower in number

    def test_a_wildcard_prefix_is_no_name_the_code_could_miss(self):
        self.assertFalse(sd.code_like("aws_ecs_"))
        self.assertFalse(sd.code_like("check_c34_"))
        self.assertTrue(sd.code_like("aws_ecs_service"))
        self.repo.merge({"src/Foo.java": "class FooService { FooService f; }\n"})
        rows = self.repo.merge({"specs/day-02-y.md": "It writes `aws_ecs_*`.\n", "src/Cloud.java": "aws_ecs_service e;\n"}).rows()
        self.assertEqual((rows[1]["name_coverage"], rows[2]["name_coverage"]), (0.5, 0.5))  # bar_table still missing

    def test_a_name_the_code_had_and_lost_is_retired_and_still_a_miss(self):
        self.repo.merge({"src/Foo.java": "class FooService { }\n"})
        rows = self.repo.merge({"src/Foo.java": None}).rows()
        self.assertEqual([r["names_retired"] for r in rows], [0, 0, 1])  # bar_table was never in the code
        self.assertEqual((rows[1]["name_coverage"], rows[2]["name_coverage"]), (0.5, 0.0))

    def test_code_in_the_spec_proportions_sits_below_chance(self):
        rows = self.repo.merge({"src/Foo.java": "class FooService { FooService f; bar_table b; }\n"}).rows()
        self.assertEqual((rows[0]["gap_full"], rows[0]["gap_chance"]), (None, None))  # no code: no angle
        self.assertEqual(rows[1]["gap_full"], 0.0)  # FooService twice and bar_table once, as the specs
        self.assertGreater(rows[1]["gap_chance"], 10.0)
        lo, hi = rows[1]["gap_chance_band"]
        self.assertTrue(lo <= rows[1]["gap_chance"] <= hi)
        rows = self.repo.merge({"src/Foo.java": "class FooService { bar_table a; bar_table b; }\n"}).rows()
        self.assertGreater(rows[2]["gap_full"], rows[1]["gap_full"])
        self.assertEqual(rows[2]["gap_chance"], rows[1]["gap_chance"])  # the same counts, shuffled

CRITERIA = """- [x] **hold** — Refused: `contract/FooIT` passes. Broken on purpose: the check removed.
      #2: `FooIT.refuses`.
      broken: the guard removed → `expected: 401 but was: 200`
- [x] **new** — Saved in `BarIT`, or a case added to `FooIT`. Red today: no table.
      #3: a case in `FooIT.saves`.
- [ ] **new** — Not started.
"""


class EvidenceTest(unittest.TestCase):
    """A closed day's evidence: what each criterion cites, and whether its tests are still there."""

    def setUp(self):
        # The day's PR is #1, the second commit; later commits stand for later days.
        self.repo = Repo().merge({"plan.md": PLAN}).merge(
            {"src/FooIT.java": "class FooIT { void refuses() {} void saves() {} }\n"})

    def tearDown(self):
        self.repo.close()

    def state(self):
        days = [dict(day=1, status="done", prs=[dict(number=1)], evidence=sd.criteria(CRITERIA))]
        return self.repo.inside(lambda blobs: sd.evidence(days, self.repo.snaps, blobs))[0]["evidence"]

    def test_a_criterion_is_read_for_its_kind_prs_tests_and_break(self):
        hold, new, open_ = sd.criteria(CRITERIA)
        self.assertEqual((hold["kind"], hold["ticked"], hold["prs"], hold["red"]), ("hold", True, [2], True))
        self.assertEqual(hold["tests"], ["FooIT", "FooIT.refuses"])
        self.assertEqual((new["red"], open_["ticked"], open_["prs"]), (False, False, []))
        self.assertTrue(hold["claim"].startswith("Refused: `contract/FooIT` passes."), hold["claim"])
        cut = sd.criteria("- [x] **hold** — " + "x" * 130 + " `a/LongNameIT`\n")[0]["claim"]
        self.assertEqual(cut.count("`"), 0)

    def test_a_criterion_from_day_28_on_carries_its_id_and_an_older_one_none(self):
        tagged, untagged = sd.criteria("- [ ] C28.3 **new** — `FooIT` refuses.\n- [ ] **hold** — C28.3 holds.\n")
        self.assertEqual((tagged["id"], tagged["kind"], tagged["claim"]), ("C28.3", "new", "`FooIT` refuses."))
        self.assertEqual((untagged["id"], untagged["claim"]), (None, "C28.3 holds."))

    def test_a_criterion_is_seen_red_only_by_a_broken_line_with_what_it_reported(self):
        red = lambda text: sd.criteria(f"- [x] C28.1 **hold** — X. #2: `FooIT`.\n      {text}\n")[0]["red"]
        self.assertTrue(red("broken: the guard removed → `FooIT` failed 1 of 3"))
        self.assertTrue(red("broken: the sort reversed -> 31 of 31 red"))
        # A baseline, a break planned but not reported, a `none`, or the free text the regexes read
        # before #310 is not a break seen red.
        for text in ["Red today: the route does not exist.", "broken: the guard removed",
                     "broken: none → the grep is the check",
                     "Broken on purpose in #74: the auth tests reported `Tests run: 49, Failures: 8`.",
                     "Red again with the migration's revoke removed. broken: x → y"]:
            self.assertFalse(red(text), text)

    def test_a_criterion_whose_every_break_cites_the_close_was_first_broken_at_the_close(self):
        crit = sd.criteria("- [x] C28.1 **hold** — X.\n      broken: the guard removed → 1 of 3 (#40)\n"
                           "- [x] C28.2 **new** — Y.\n      broken: a → b (#40)\n      broken: c → d (#38)\n"
                           "- [x] C28.3 **new** — Z.\n      broken: a → b, as #38 showed\n"
                           "- [x] C28.4 **new** — W. #40\n      broken: none → the grep is the check\n")
        self.assertEqual([c["broke_in"] for c in crit], [[40], [38, 40], [38], []])
        day = dict(prs=[dict(number=38, kind="code"), dict(number=40, kind="close")], evidence=crit)
        # C28.2 was also broken in its track; C28.3 in a track; C28.4 records no break to judge.
        self.assertEqual(sd.broken_at_close(day), ["C28.1"])
        self.assertEqual(sd.broken_at_close(dict(day, prs=day["prs"][:1])), [])

    def test_a_spec_with_no_criterion_tagged_predates_the_evidence_format(self):
        self.assertFalse(sd.in_evidence_format(sd.criteria("- [x] Login returns 200.\n- [x] Logout clears it.\n")))
        self.assertTrue(sd.in_evidence_format(sd.criteria("- [x] Login returns 200.\n- [x] **hold** — X.\n")))
        self.assertTrue(sd.in_evidence_format(sd.criteria(CRITERIA)))

    def test_a_name_not_in_the_tree_when_the_day_ended_is_not_evidence(self):
        self.assertEqual(self.state()[1]["tests"], {"FooIT": "present", "FooIT.saves": "present"})

    def test_a_test_that_leaves_the_tree_or_can_be_skipped_is_reported(self):
        self.repo.merge({"src/FooIT.java": "@Disabled class FooIT { void refuses() {} }\n"})
        self.assertEqual(self.state()[1]["tests"], {"FooIT": "skippable", "FooIT.saves": "missing"})
        self.repo.merge({"src/FooIT.java": ""})
        self.repo.git("rm", "-q", "src/FooIT.java")
        self.repo.git("commit", "-q", "-m", "gone")
        self.repo.snaps.append(dict(self.repo.snaps[-1], sha=self.repo.git("rev-parse", "--short", "HEAD").strip()))
        self.assertEqual(set(self.state()[0]["tests"].values()), {"missing"})

    def test_a_python_test_is_found_by_its_class_in_any_test_file(self):
        # a pilot: CliTest lives in tests/test_cli.py, and every criterion's tests read "missing".
        self.repo.merge({"tests/test_cli.py": "import unittest\n\n\nclass CliTest(unittest.TestCase):\n"
                                              "    def test_counts(self):\n        pass\n",
                         "tests/test_rule.py": "class RuleTest:\n    @pytest.mark.skipif(True, reason='x')\n"
                                               "    def test_word(self):\n        pass\n",
                         "src/helper.py": "class HelperTest:\n    pass\n"})
        names = {"CliTest", "CliTest.test_counts", "CliTest.test_gone", "RuleTest.test_word", "HelperTest"}
        state = self.repo.inside(lambda blobs: sd.test_state(names, self.repo.snaps[-1]["sha"], blobs))
        self.assertEqual(state, {"CliTest": "present", "CliTest.test_counts": "present",
                                 "CliTest.test_gone": "missing", "RuleTest.test_word": "skippable",
                                 "HelperTest": "missing"})  # src/helper.py is not a test file

    def test_javascript_skip_markers_and_near_misses(self):
        for marker in ("it.skip(", "test.skip(", "describe.skip(", "xit(", "xdescribe(", "it.todo(", "test.todo("):
            self.assertIsNotNone(sd.SKIPPABLE.search(marker), marker)
        for marker in ("omit.skip(", "mytest.skip(", "exit(", "prefixdescribe.skip("):
            self.assertIsNone(sd.SKIPPABLE.search(marker), marker)

    def test_a_python_test_that_calls_skip_test_is_skippable(self):
        # a pilot: a test can skip itself from its body, as CI on a missing tool does.
        self.repo.merge({"tests/test_cli.py": "import unittest\n\n\nclass CliTest(unittest.TestCase):\n"
                                              "    def test_bash(self):\n        self.skipTest('no bash')\n"})
        state = self.repo.inside(lambda blobs: sd.test_state({"CliTest.test_bash"}, self.repo.snaps[-1]["sha"], blobs))
        self.assertEqual(state, {"CliTest.test_bash": "skippable"})

    def test_a_test_moved_under_a_new_name_is_followed_from_the_merge_it_arrives(self):
        # Day 17: SavedJobCountsUnavailableIT left in #174, and its Test arrived in #180. A helper
        # leaving as a test of its name arrives is not a move: only tests are followed.
        self.repo.merge({"src/Stub.java": "class Stub {}\n"})
        self.repo.git("rm", "-q", "src/FooIT.java", "src/Stub.java")
        self.repo.merge({"src/Other.java": "class Other {}\n"})
        self.repo.merge({"svc/FooTest.java": "class FooTest { void refuses() {} void saves() {} }\n",
                         "svc/StubTest.java": "class StubTest {}\n"})
        self.assertEqual(self.repo.inside(lambda blobs: sd.moves(self.repo.snaps)), [(4, "FooIT", "FooTest")])
        self.assertEqual(self.state()[1]["tests"], {"FooIT": "present", "FooIT.saves": "present"})

    def test_a_class_of_the_same_stem_there_before_the_test_left_is_not_where_it_went(self):
        self.repo.merge({"svc/FooTest.java": "class FooTest { void refuses() {} void saves() {} }\n"})
        self.repo.git("rm", "-q", "src/FooIT.java")
        self.repo.merge({"src/Other.java": "class Other {}\n"})
        self.assertEqual(self.repo.inside(lambda blobs: sd.moves(self.repo.snaps)), [])
        self.assertEqual(set(self.state()[1]["tests"].values()), {"missing"})

    def test_a_name_there_before_the_test_left_and_arriving_again_after_is_followed_to_the_later(self):
        # InternalCallsObservedTest: in matching-service from Day 21, then in application-service
        # in #305, after InternalCallsObservedIT left in #296.
        self.repo.merge({"svc/FooTest.java": "class FooTest { void other() {} }\n"})
        self.repo.git("rm", "-q", "src/FooIT.java")
        self.repo.merge({"src/Other.java": "class Other {}\n"})
        self.repo.merge({"app/FooTest.java": "class FooTest { void refuses() {} void saves() {} }\n"})
        self.assertEqual(self.repo.inside(lambda blobs: sd.moves(self.repo.snaps)), [(4, "FooIT", "FooTest")])
        self.assertEqual(self.state()[1]["tests"], {"FooIT": "present", "FooIT.saves": "present"})

    def test_a_move_to_another_stem_is_followed_only_by_the_rename_table(self):
        self.repo.git("rm", "-q", "src/FooIT.java")
        self.repo.merge({"svc/BarTest.java": "class BarTest { void refuses() {} void saves() {} }\n"})
        self.assertEqual(set(self.state()[1]["tests"].values()), {"missing"})
        use_project(self, dict(renamed=[[2, "FooIT", "BarTest"]]))
        self.assertEqual(self.repo.inside(lambda blobs: sd.moves(self.repo.snaps)), [(2, "FooIT", "BarTest")])
        self.assertEqual(self.state()[1]["tests"], {"FooIT": "present", "FooIT.saves": "present"})

    def test_a_test_run_on_a_system_property_is_run_when_ci_passes_it_and_disabled_is_not(self):
        on = '@EnabledIfSystemProperty(named = "harness.gateway", matches = "true")\n'
        self.repo.merge({"src/FooIT.java": on + "class FooIT { void refuses() {} void saves() {} }\n"})
        self.assertEqual(set(self.state()[1]["tests"].values()), {"skippable"})
        self.repo.merge({".github/workflows/ci.yml": "run: ./build verify -Dharness.gateway=false\n"})
        self.assertEqual(set(self.state()[1]["tests"].values()), {"skippable"})
        self.repo.merge({".github/workflows/ci.yml": "run: ./build verify -pl app -am -Dharness.gateway=true\n"})
        self.assertEqual(set(self.state()[1]["tests"].values()), {"present"})
        self.repo.merge({"src/FooIT.java": on + "@Disabled class FooIT { void refuses() {} void saves() {} }\n"})
        self.assertEqual(set(self.state()[1]["tests"].values()), {"skippable"})

    def test_the_history_counts_a_moved_test_missing_until_it_arrives(self):
        days = [dict(day=1, status="done", prs=[dict(number=1)], evidence=sd.criteria(CRITERIA))]
        self.repo.git("rm", "-q", "src/FooIT.java")
        self.repo.merge({"src/Other.java": "class Other {}\n"})
        self.repo.merge({"svc/FooTest.java": "class FooTest { void refuses() {} void saves() {} }\n"})
        rows = [{} for _ in self.repo.snaps]
        self.repo.inside(lambda blobs: sd.evidence_history(rows, sd.evidence(days, self.repo.snaps, blobs),
                                                           self.repo.snaps, blobs))
        self.assertEqual([r["evidence"] for r in rows][1:], [
            dict(present=3, skippable=0, missing=0, retired=0),
            dict(present=0, skippable=0, missing=3, retired=0),
            dict(present=3, skippable=0, missing=0, retired=0)])


    def test_the_history_starts_when_the_day_ends_and_shows_the_merge_a_test_left(self):
        self.repo.merge({"src/FooIT.java": "class FooIT { void refuses() {} }\n"})
        days = [dict(day=1, status="done", prs=[dict(number=1)], evidence=sd.criteria(CRITERIA))]
        rows = [{} for _ in self.repo.snaps]

        def history(blobs):
            sd.evidence_history(rows, sd.evidence(days, self.repo.snaps, blobs), self.repo.snaps, blobs)
        self.repo.inside(history)
        self.assertEqual([r["evidence"] for r in rows], [
            None,
            dict(present=3, skippable=0, missing=0, retired=0),
            dict(present=2, skippable=0, missing=1, retired=0)])

    def history_after_removal(self, spec):
        """Day 1's tests, then a day 2 merge that removes FooIT and writes day 2's spec, as `spec`."""
        days = [dict(day=1, status="done", prs=[dict(number=1)], evidence=sd.criteria(CRITERIA)),
                dict(day=2, file="specs/day-02-y.md", status="done", prs=[dict(number=2)], evidence=[])]
        self.repo.merge({"src/FooIT.java": None, "specs/day-02-y.md": spec})
        self.repo.snaps[-1]["branch"] = "day-02/track-a-x"
        rows = [{} for _ in self.repo.snaps]
        self.repo.inside(lambda blobs: sd.evidence_history(rows, sd.evidence(days, self.repo.snaps, blobs),
                                                           self.repo.snaps, blobs))
        return rows, days

    def test_a_test_a_later_days_spec_names_as_removed_is_retired_not_missing(self):
        rows, days = self.history_after_removal("# Day 02\n\nIt removes `FooIT`.\n")
        self.assertEqual(rows[-1]["evidence"], dict(present=0, skippable=0, missing=0, retired=3))
        self.assertEqual({v for c in days[0]["evidence"] for v in c["tests"].values()}, {"retired"})

    def test_a_test_gone_that_no_later_spec_names_stays_missing(self):
        rows, days = self.history_after_removal("# Day 02\n\nIt removes the rest.\n")
        self.assertEqual(rows[-1]["evidence"], dict(present=0, skippable=0, missing=3, retired=0))
        self.assertEqual({v for c in days[0]["evidence"] for v in c["tests"].values()}, {"missing"})


REMOVALS = """## Acceptance criteria
- [x] **new** — `grep -rn "OldThing\\|old_table" --include=*.java .`
      returns nothing. Red today: two.

## Verify
```bash
cd core
grep -rn "TODO day-01" */src/ || echo "clean"
grep -rn "kept" app/ | wc -l
```

## Notes
- `grep -rn "NotACheck"` returns nothing, but a note is not a check.
"""


class RemovalTest(unittest.TestCase):
    """A day's greps that must print nothing, run at every merge from the day's end."""

    def setUp(self):
        use_project(self, PROJECT)

    def test_the_checks_are_the_criteria_and_verify_greps_that_expect_nothing(self):
        self.assertEqual(sd.removal_checks(REMOVALS), ['grep -rn "OldThing\\|old_table" --include=*.java .',
                                                       'grep -rn "TODO day-01" */src/'])

    def test_a_basic_regex_alternation_is_an_alternation_and_a_bare_bar_is_literal(self):
        rx, paths, include = sd.grep_rule('grep -rn "OldThing\\|old_table" --include=*.java .')
        self.assertEqual((bool(rx.search("old_table")), paths, include), (True, ["."], "*.java"))
        self.assertFalse(sd.grep_rule('grep -rn "a|b" x/')[0].search("a"))
        self.assertTrue(sd.grep_rule('grep -rnE "a|b" x/')[0].search("a"))
        self.assertIsNone(sd.grep_rule('grep -rn "x" a/ | grep -v b'))

    def test_a_check_counts_lines_in_its_scope_from_the_day_it_ended(self):
        repo = Repo().merge({"core/app/A.java": "OldThing a;\nold_table b;\n", "core/app/B.txt": "OldThing\n",
                             "other/C.java": "OldThing c;\n", "core/app/src/D.java": "",
                             "core/pom.txt": "TODO day-01\n"})
        repo.merge({"core/app/A.java": "class A {}\n"})
        repo.merge({"core/app/A.java": "class A { OldThing back; }\n", "core/app/src/D.java": "// TODO day-01\n"})
        def run(ended_at):
            days = [dict(day=1, ended_at=ended_at, removal_checks=sd.removal_checks(REMOVALS))]
            return [(c["base"], c["hits"]) for c in repo.inside(lambda blobs: sd.removal_history(days, repo.snaps, blobs))]
        try:
            # B.txt is not *.java, other/ is not under core/, and pom.txt is not under */src/.
            self.assertEqual(run(0), [("core", [2, 0, 1]), ("core", [0, 0, 1])])
            self.assertEqual(run(1), [("core", [0, 1]), ("core", [0, 1])])
        finally:
            repo.close()

    def test_a_path_follows_a_move_and_a_parent_of_it_also_reads_where_it_went(self):
        moves = [("backend/jobs", "services/orders-service"), ("backend", "services/account-service")]
        self.assertEqual(sd.follow("backend/jobs/src", moves), ["services/orders-service/src"])
        self.assertEqual(sd.follow("backend/docs", moves), ["services/account-service/docs"])
        self.assertEqual(sd.follow("backend/*/src/main", moves),
                         ["services/orders-service/src/main", "services/account-service/*/src/main"])
        self.assertEqual(sd.follow("backend", moves[:1]), ["services/orders-service", "backend"])
        self.assertEqual(sd.follow("docker-compose.yml", moves), ["docker-compose.yml"])

    def test_the_project_files_moves_follow_moved_docs_on_to_docs_system(self):
        moves = [(old, new) for _, old, new in sd.MOVED]
        self.assertEqual(sd.follow("core/docs", moves), ["docs/system"])
        self.assertEqual(sd.follow("core/docs/api.md", moves), ["docs/system/api.md"])

    def test_the_project_files_moves_follow_a_lifted_app_to_the_service_root(self):
        moves = [(old, new) for _, old, new in sd.MOVED]
        self.assertEqual(sd.follow("core/app/src/main/java", moves), ["services/account-service/src/main/java"])
        self.assertEqual(sd.follow("services/account-service/app/src", moves), ["services/account-service/src"])

    def test_a_check_reads_moved_code_counts_its_files_and_lists_its_lines_on_the_last_merge(self):
        repo = Repo().merge({"core/stock/src/A.java": "class A {}\n", "core/app/src/B.java": "class B {}\n"})
        repo.merge({"core/stock/src/A.java": None, "services/stock-service/src/A.java": "// TODO day-01 here\n"})
        repo.merge({"core/app/src/B.java": None, "services/stock-service/src/A.java": "class A {}\n"})
        def run(moved):
            days = [dict(day=1, ended_at=0, removal_checks=sd.removal_checks(REMOVALS))]
            return repo.inside(lambda blobs: sd.removal_history(days, repo.snaps, blobs, moved))[1]
        try:
            # Merge 2's tree has no core/ left: unfollowed, the check reads nothing and passes.
            still = run([])
            self.assertEqual((still["files"], still["hits"]), ([2, 1, 0], [0, 0, 0]))
            followed = run([(1, "core/stock", "services/stock-service")])
            self.assertEqual((followed["files"], followed["hits"], followed["found"]), ([2, 2, 1], [0, 1, 0], []))
            repo.merge({"services/stock-service/src/A.java": "x\n// TODO day-01 again\n"})
            self.assertEqual(run([(1, "core/stock", "services/stock-service")])["found"],
                             [dict(path="services/stock-service/src/A.java", line=2, text="// TODO day-01 again")])
        finally:
            repo.close()


class HandOffTest(unittest.TestCase):
    """What finished days leave to days not yet run, and whether the receiving spec took it."""

    def test_an_excerpt_ends_at_a_word_and_outside_a_code_span(self):
        self.assertEqual(sd.excerpt("alpha  beta\n gamma", 40), "alpha beta gamma")
        self.assertEqual(sd.excerpt("alpha beta gamma", 12), "alpha beta…")
        self.assertEqual(sd.excerpt("see `foo bar baz` now", 12), "see…")

    def test_the_days_a_sentence_names(self):
        self.assertEqual(sd.days_named("Day 17 adds it; Days 18-19 and Days 19, 21 and 24 build theirs"),
                         {17, 18, 19, 21, 24})
        self.assertEqual(sd.days_named("Days 26–28, or Day 30"), {26, 27, 28, 30})

    def test_a_hand_off_is_picked_up_by_the_spec_that_names_its_day_or_its_file(self):
        repo = Repo().merge({
            "specs/day-01-a.md": "## Notes\n- For Day 03: the key.\n- Day 02's findings for Day 04 are there.\n"
                                 "- Day 05 has nothing from this day.\n",
            "specs/day-02-b.md": "# Day 02\n",
            "specs/day-03-c.md": "# Day 03\n\nFrom Day 01: the key.\n",
            "specs/day-04-d.md": "# Day 04\n\nFrom Day 02.\n",
            "specs/day-05-e.md": "# Day 05\n\nIt reads `Foo`.\n",
            "src/Foo.java": "// Day 05 moves this.\nclass Foo {} // Day 05\n",
            "src/Bar.java": "/* Day 04 deletes this. */\n",
        })
        days = [dict(day=n, file=f"specs/day-0{n}-{c}.md", status="done" if n < 3 else "ready")
                for n, c in zip(range(1, 6), "abcde")]
        try:
            found = repo.inside(lambda blobs: sd.hand_offs(days, [1, 2, 3, 4, 5], repo.snaps[-1]["sha"], blobs))
        finally:
            repo.close()
        self.assertEqual([(h["to"], h["source"], h["picked"]) for h in found], [
            (3, "Day 01 Notes", True),          # in run order, open ones first within a day
            (4, "src/Bar.java:1", False),       # Day 04 never names Bar
            (4, "Day 01 Notes", True),          # points to Day 02, which Day 04 names
            (5, "Day 01 Notes", False),
            (5, "src/Foo.java:1", True)])       # the code line after it is not a comment
        self.assertEqual(found[4]["text"], "Day 05 moves this.")

    def test_a_hand_off_with_an_id_is_picked_up_only_by_a_spec_citing_it(self):
        repo = Repo().merge({
            "specs/day-01-a.md": "## Notes\n- **Hand-off** H01.1 → Day 03: the key.\n"
                                 "- **Hand-off** H01.2 → Day 02: the queue.\n"
                                 "- **Hand-off** H01.3 → the maintainer: branch protection.\n"
                                 "- **Hand-off** H01.4 → the maintainer: the volume.\n",
            "specs/day-02-b.md": "# Day 02\n\nFrom Day 01: nothing cited.\n",
            "specs/day-03-c.md": "# Day 03\n\nFrom Day 01: the key.\n\n## Notes\n- H01.4 done by hand.\n",
            "specs/day-04-d.md": "# Day 04\n\nPicks up H01.1.\n",
            "src/Foo.java": "// Day 01 wrote this.\n",
        })
        days = [dict(day=n, file=f"specs/day-0{n}-{c}.md", status="done" if n < 3 else "ready")
                for n, c in zip(range(1, 5), "abcd")]
        try:
            found = repo.inside(lambda blobs: sd.hand_offs(days, [1, 2, 3, 4], repo.snaps[-1]["sha"], blobs))
        finally:
            repo.close()
        self.assertEqual([(h["to"], h["id"], h["picked"]) for h in found], [
            (3, "H01.1", False),                # Day 03 names Day 01, not the ID; Day 04 cites it
            (None, "H01.2", False),             # Day 02 finished without citing it
            (None, "H01.3", False)])            # no day takes it; H01.4 is cited by Day 03's Notes

    def test_a_day_plan_md_puts_ahead_takes_its_hand_off_before_its_spec_is_written(self):
        # a pilot, order 1, 3, 2, 4, 5: at its #5 H1.1 and H1.2 sat under "No later day picks these
        # up"; at its #10 H1.1 was gone, as Day 3's spec cites its ID.
        repo = Repo().merge({
            "specs/day-01-a.md": "## Notes\n- **Hand-off** H1.1 → Day 2: the stopwords.\n"
                                 "- **Hand-off** H1.2 → Day 3: the exit code.\n",
            "specs/day-03-c.md": "# Day 03\n\nH1.2 is picked up here; H1.1 stays with Day 2.\n",
            "src/Foo.java": "// Day 01 wrote this.\n",
        })
        one = dict(day=1, file="specs/day-01-a.md", status="done")
        three = dict(day=3, file="specs/day-03-c.md", status="done")
        try:
            found = [repo.inside(lambda blobs: sd.hand_offs(days, [1, 3, 2, 4, 5], repo.snaps[-1]["sha"], blobs))
                     for days in ([one], [one, three])]
        finally:
            repo.close()
        self.assertEqual([[(h["to"], h["id"], h["picked"]) for h in f] for f in found], [
            [(3, "H1.2", False), (2, "H1.1", False)],   # at #5: Days 3 and 2 have no spec yet
            [(2, "H1.1", False)]])                      # at #10: Day 3 done; Day 2 has no spec yet

    def test_a_track_a_pr_announces_that_no_branch_names_is_the_next_step(self):
        prs = [dict(number=298, headRefName="day-25/track-e1b-x", body="**E1c** (next). Not Day 21's E1d; `grep -A3`."),
               dict(number=297, headRefName="day-25/track-e1a-x", body="Track E1 splits; B1 stays."),
               dict(number=290, headRefName="day-25/track-b-x", body="B1 here")]
        self.assertEqual(sd.unopened_tracks(prs), [dict(track="E1c", pr=298)])
        self.assertEqual(sd.unopened_tracks(prs + [dict(number=305, headRefName="day-25/track-e1c-y", body="")]), [])
        day = dict(a_day(25), status="active", prs=[dict(kind="spec")], tracks_merged=["A"],
                   tracks_unopened=sd.unopened_tracks(prs))
        self.assertEqual(sd.next_step([day], [], [25], None), "Day 25 Track E1c: announced in #298, no branch names it")

    def test_a_criterion_id_is_not_a_track(self):
        self.assertEqual(sd.unopened_tracks([dict(number=1, headRefName="day-32/track-c1-x", body="Ticks C32.5 and H32.1.")]), [])

    def test_a_day_numbered_name_is_not_a_track(self):
        body = 'It read `C32.x` as "C32", a track C32: wrong.'
        self.assertEqual(sd.unopened_tracks([dict(number=1, headRefName="day-32/track-c1-x", body=body)]), [])

    def test_a_notes_item_that_only_cites_its_days_hand_off_is_not_one(self):
        # a pilot, Day 1: a Note ending "(H1.2)" was listed as H1.2 a second time, to no day.
        repo = Repo().merge({
            "specs/day-01-a.md": "## Notes\n- The condition is in one place, so Day 03 decides it (H01.2).\n"
                                 "- **Hand-off** H01.2 → Day 03: the stop decides it.\n",
            "specs/day-03-c.md": "# Day 03\n\nNothing cited.\n",
            "src/Foo.java": "// Day 01 wrote this.\n",
        })
        days = [dict(day=1, file="specs/day-01-a.md", status="done"), dict(day=3, file="specs/day-03-c.md", status="ready")]
        try:
            found = repo.inside(lambda blobs: sd.hand_offs(days, [1, 3], repo.snaps[-1]["sha"], blobs))
        finally:
            repo.close()
        self.assertEqual([(h["to"], h["id"], h["text"][:12]) for h in found], [(3, "H01.2", "**Hand-off**")])

    def test_a_hand_off_range_names_every_id_in_it(self):
        self.assertEqual(sd.hand_off_ids("H34.1–H34.3 and H35.2-4, H36.7"),
                         {"H34.1", "H34.2", "H34.3", "H35.2", "H35.3", "H35.4", "H36.7"})

    def test_a_hand_off_range_picks_up_each_id_in_it(self):
        repo = Repo().merge({
            "specs/day-01-a.md": "## Notes\n- **Hand-off** H01.2 → the maintainer: the middle one.\n",
            "specs/day-02-b.md": "# Day 02\n\nPicks up H01.1–H01.3.\n",
            "src/App.java": "// Day 01 wrote this.\n",  # git grep fails on a tree with no day in its code
        })
        days = [dict(day=1, file="specs/day-01-a.md", status="done"),
                dict(day=2, file="specs/day-02-b.md", status="ready")]
        try:
            found = repo.inside(lambda blobs: sd.hand_offs(days, [1, 2], repo.snaps[-1]["sha"], blobs))
        finally:
            repo.close()
        self.assertEqual(found, [])  # Day 02's range cites H01.2, so no hand-off is left open

    def test_a_code_comment_is_not_handed_to_the_day_whose_pr_wrote_it(self):
        repo = Repo().merge({
            "specs/day-01-a.md": "# Day 01\n",
            "specs/day-02-b.md": "# Day 02\n",
            "specs/day-03-c.md": "# Day 03\n",
            "src/Foo.java": "// Day 02 finishes this.\n// Day 03 removes this.\n",
        })
        repo.git("commit", "--amend", "-q", "-m", "Merge pull request #7 from o/day-02/track-a-x")
        repo.snaps[-1]["sha"] = repo.git("rev-parse", "--short", "HEAD").strip()
        days = [dict(day=n, file=f"specs/day-0{n}-{c}.md", status="done" if n < 2 else "ready")
                for n, c in zip(range(1, 4), "abc")]
        try:
            found = repo.inside(lambda blobs: sd.hand_offs(days, [1, 2, 3], repo.snaps[-1]["sha"], blobs))
        finally:
            repo.close()
        self.assertEqual([(h["to"], h["source"]) for h in found], [(3, "src/Foo.java:2")])


class ProjectFileTest(unittest.TestCase):
    """spec-drift.config.json: what git cannot tell about a repository, empty in a new one."""

    def test_with_no_file_or_no_field_nothing_is_set_and_verify_reads_the_root(self):
        for fields in (None, {}):
            folder = tempfile.mkdtemp(prefix="spec-drift-config-")
            self.addCleanup(shutil.rmtree, folder, True)
            path = os.path.join(folder, "spec-drift.config.json")
            if fields is not None:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(fields, f)
            c = sd.project_config(path)
            self.assertEqual((c["RENAMED"], c["MOVED"], c["VERIFY_BASES"], c["SERVICE"], c["INFRA_WORKFLOW"]),
                             ({}, [], ("",), {}, ""))

    def test_renames_moves_and_verify_bases_are_the_files(self):
        use_project(self, PROJECT)
        self.assertEqual(sd.RENAMED, {296: ("OrdersUserDeletedConsumerIT", "UserDeletedConsumerTest"),
                                      298: ("CircuitBreakerIT", "StockLookupBreakerTest")})
        self.assertEqual(len(sd.MOVED), 6)
        self.assertEqual(sd.MOVED[3], (315, "core", "services/account-service"))
        self.assertEqual(sd.VERIFY_BASES, ("core", ""))

    def test_boundaries_place_and_cross_the_files_services_under_its_package_root(self):
        root = "src/main/java/org/example/shop"
        repo = Repo().merge({
            f"core/{root}/orders/Order.java": "import org.example.shop.auth.User;\n"
                                               "import org.example.shop.util.Text;\nclass Order {}\n",
            f"core/{root}/util/Text.java": "class Text {}\n",
            f"services/stock-service/{root}/catalog/Item.java": "import static org.example.shop.orders.Order.x;\n"
                                                                 "class Item {}\n",
            f"core/src/test/java/org/example/shop/orders/OrderTest.java": "import org.example.shop.auth.User;\n"})
        self.addCleanup(repo.close)
        def boundaries():
            rows = [{}]
            repo.inside(lambda blobs: sd.boundaries(rows, repo.snaps, blobs))
            return rows[0]["placed"], rows[0]["crossings"]
        use_project(self, PROJECT)
        self.assertEqual(boundaries(), ({"orders": [3, 0], "stock": [0, 2]}, {"orders>account": 1, "stock>orders": 1}))
        use_project(self, {})
        self.assertEqual(boundaries(), ({}, {}))

    def test_the_infra_workflow_is_the_files_and_with_none_gh_is_not_asked(self):
        asked, real = [], sd.run
        self.addCleanup(setattr, sd, "run", real)
        sd.run = lambda *args: asked.append(args) or "[]"
        use_project(self, PROJECT)
        sd.infra(sd.INFRA_WORKFLOW)
        self.assertEqual([a[:5] for a in asked], [("gh", "run", "list", "--workflow", "infra.yaml")])
        use_project(self, {})
        asked.clear()
        self.assertEqual((sd.infra(sd.INFRA_WORKFLOW), asked), (dict(runs=[], latest=None), []))


class OwnerAndCaveatTest(unittest.TestCase):
    """Only the owner's PRs are read (SO-12), and the dashboard says what name coverage cannot see."""

    def test_only_the_owners_prs_are_kept(self):
        listed = [dict(number=1, author=dict(login="Yusuprozimemet")),
                  dict(number=2, author=dict(login="app/dependabot")),
                  dict(number=3)]
        self.assertEqual([p["number"] for p in sd.owners_prs(listed, "Yusuprozimemet")], [1])

    def test_another_authors_pr_is_not_the_next_step(self):
        mine = dict(number=50, title="Day 25 track A", state="OPEN", author=dict(login="Yusuprozimemet"))
        theirs = dict(number=51, title="Bump x", state="OPEN", author=dict(login="app/dependabot"))
        day = a_day(25, kinds=["spec"])
        everyone = sd.next_step([day], [mine, theirs], [25], None)
        mine_only = [p for p in sd.owners_prs([mine, theirs], "Yusuprozimemet") if p["state"] == "OPEN"]
        self.assertIn("#51", everyone)
        self.assertEqual(sd.next_step([day], mine_only, [25], None), "Waiting on #50 Day 25 track A")

    def test_another_authors_body_names_no_track(self):
        theirs = dict(number=51, headRefName="day-25/track-e1a-x", title="x", body="Ticks E1c.",
                      author=dict(login="app/dependabot"))
        self.assertEqual(sd.unopened_tracks([theirs]), [dict(track="E1c", pr=51)])
        self.assertEqual(sd.unopened_tracks(sd.owners_prs([theirs], "Yusuprozimemet")), [])

    def test_the_coverage_caveat_says_tests_and_tooling_count_and_cites_no_measurement(self):
        self.assertIn("Name coverage counts test and tooling files as code.", sd.CAVEATS)
        for c in sd.CAVEATS:  # another project has no such audit, file or figure
            self.assertNotRegex(c, r"\d|\.md\b", c)

    def test_the_project_is_the_repositorys_name_or_with_no_remote_its_folders(self):
        def fake(*args):
            if args[:3] == ("gh", "repo", "view"):
                if remote:
                    return "toy-repo\n"
                raise subprocess.CalledProcessError(1, args, stderr="no git remotes found")
            if args[:2] == ("git", "rev-parse"):
                return "/home/someone/toy-folder\n"
        real = sd.run
        try:
            sd.run = fake
            remote = True
            self.assertEqual(sd.project_name(), "toy-repo")
            remote = False
            self.assertEqual(sd.project_name(), "toy-folder")
        finally:
            sd.run = real


# Boots docs/dashboard/dashboard.js on the data from stdin, with a document that accepts anything,
# and prints the eyebrow and the stats it wrote. The panels drawn after the eyebrow may throw on a
# small fixture; only an error before it fails a test.
BOOT = r"""
const fs = require("fs"), vm = require("vm"), els = {};
const stub = () => new Proxy(function () {}, {
  get: (t, k) => k === Symbol.toPrimitive ? () => "" : k === Symbol.iterator ? [][Symbol.iterator] : k in t ? t[k] : (t[k] = stub()),
  apply: () => stub(), construct: () => stub() });
const document = new Proxy({}, { get: (t, k) => k === "getElementById" ? id => els[id] || (els[id] = stub()) : stub() });
const ctx = vm.createContext({ document, window: {}, DATA: JSON.parse(fs.readFileSync(0, "utf8")) });
let error = null;
try { vm.runInContext(fs.readFileSync(process.argv[1], "utf8") + "\nboot(DATA);", ctx); } catch (e) { error = e.message; }
const text = id => els[id] ? [els[id].textContent, els[id].innerHTML].find(v => typeof v === "string") : null;
const banner = els["demo-banner"];
console.log(JSON.stringify({ eyebrow: text("eyebrow"), stats: text("stats"), error, banner: banner ? { hidden: banner.hidden, text: banner.textContent } : null }));
"""


def page_row(i, pr, kind, gap):
    return dict(i=i, sha=f"{i:07x}", date="2026-01-01", pr=pr, title="t", kind=kind, gap_full=gap)


def page(rows, days=(), order=(), current_day=None):
    """What the page writes for this data: its eyebrow, and the value under a stat's name."""
    data = dict(now=dict(generated="2026-01-01T00:00", head="abc", last_pr=None, current_day=current_day,
                         next_step="x", open_prs=[]), rows=rows, days=list(days), order=list(order))
    out = json.loads(subprocess.run(["node", "-e", BOOT, os.path.join(HERE, "..", "docs", "dashboard", "dashboard.js")],
                                    input=json.dumps(data), capture_output=True, encoding="utf-8", check=True).stdout)
    if out["eyebrow"] is None:
        raise AssertionError(f"the page stopped before the eyebrow: {out['error']}")
    stat = lambda name: out["stats"].split(f">{name}</span>")[1].split('class="v num">')[1].split("<")[0]
    return out["eyebrow"], stat


# Boots the page on a document whose elements keep what is drawn into them, and prints each box's
# HTML, SVG included, the error that stopped the page, if one did, and what went to console.error.
DRAW = r"""
const fs = require("fs"), vm = require("vm"), els = {}, logged = [];
class El {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.kids = []; this.html = ""; this.style = {}; this.dataset = {}; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  appendChild(c) { this.kids.push(c); return c; }
  set innerHTML(h) { this.html = String(h); this.kids = []; }
  get innerHTML() { return this.html + this.kids.map(k => k.out()).join(""); }
  set textContent(t) { this.innerHTML = t; }
  get textContent() { return this.html; }
  insertAdjacentHTML(_, h) { this.kids.push({ out: () => String(h) }); }
  addEventListener() {} querySelectorAll() { return []; }
  querySelector(t) { return this.kids.find(k => k.tag === t) || this.appendChild(new El(t)); }
  out() { return `<${this.tag}${Object.entries(this.attrs).map(([k, v]) => ` ${k}="${v}"`).join("")}>${this.innerHTML}</${this.tag}>`; }
}
const document = { getElementById: id => els[id] || (els[id] = new El("div")), createElementNS: (_, t) => new El(t),
  querySelectorAll: () => [] };
const ctx = vm.createContext({ document, window: {}, matchMedia: () => ({ matches: true }), DATA: JSON.parse(fs.readFileSync(0, "utf8")),
  console: { error: e => logged.push(String(e && e.message || e)) } });
let error = null;
try { vm.runInContext(fs.readFileSync(process.argv[1], "utf8") + "\nboot(DATA);", ctx); } catch (e) { error = e.message; }
console.log(JSON.stringify({ error, logged, panels: Object.fromEntries(Object.entries(els).map(([k, e]) => [k, e.innerHTML])) }));
"""


def page_data(rows, days=(), order=(), **more):
    """The data the page draws: these rows, days and run order, empty elsewhere; `more` replaces fields."""
    data = dict(now=dict(generated="2026-01-01T00:00", head="abc", last_pr=None, current_day=None,
                         next_step="x", open_prs=[]), rows=list(rows), days=list(days), order=list(order),
                files=[], removals=[], hand_offs=[], tokens=[], verification=[], infra=dict(runs=[], latest=None),
                deployment=None, conclusion=dict(reads=[], measured=[]), origin_lines=0, vocab_size=0)
    data.update(more)
    return data


def drawn(rows=(), days=(), order=(), page=None, **more):
    """The error that stopped the page, or None, the HTML each box holds, and what was logged as an
    error; `more` replaces fields of the data. With `page`, a page spec-drift.py --build wrote: its
    inlined script draws its own data, and the other arguments are not used."""
    data = page_data(rows, days, order, **more)
    script = os.path.join(HERE, "..", "docs", "dashboard", "dashboard.js")
    with tempfile.TemporaryDirectory() as tmp:
        if page:
            with open(page, encoding="utf-8") as f:
                html = f.read()
            # build() inlines dashboard.js as the script that defines boot(); fill() escapes "</" as "<\/",
            # which JSON reads back as "/".
            script = os.path.join(tmp, "dashboard.js")
            with open(script, "w", encoding="utf-8") as f:
                f.write(next(s for s in re.findall(r"<script>\n(.*?)</script>", html, re.S) if "function boot(" in s))
            data = json.loads(re.search(r"^window\.SPEC_DRIFT = (.*);$", html, re.M).group(1))
        out = json.loads(subprocess.run(["node", "-e", DRAW, script], input=json.dumps(data),
                                        capture_output=True, encoding="utf-8", check=True).stdout)
    return out["error"], out["panels"], out["logged"]


class OtherGroupsTest(unittest.TestCase):
    def test_groups_other_merges_by_branch_prefix(self):
        rows = [
            dict(pr=1, kind="other", branch="tooling/a"),
            dict(pr=2, kind="other", branch="tooling/b"),
            dict(pr=3, kind="other", branch="tooling/c"),
            dict(pr=4, kind="other", branch="dashboard/a"),
            dict(pr=5, kind="other", branch="dashboard/b"),
            dict(pr=6, kind="other", branch="docs/a"),
            dict(pr=7, kind="other", branch="release"),
            dict(pr=8, kind="code", branch="day-01/track-a-x"),
        ]
        self.assertEqual(sd.other_groups(rows), [
            {"name": "tooling", "count": 3},
            {"name": "dashboard", "count": 2},
            {"name": "docs", "count": 1},
            {"name": "other", "count": 1},
        ])

    def test_groups_cap_the_smallest_prefixes(self):
        rows = [dict(pr=i, kind="other", branch=f"{name}/x")
                for i, name in enumerate(("a", "a", "a", "b", "b", "c", "d", "e", "f"), 1)]
        self.assertEqual(sd.other_groups(rows), [
            {"name": "a", "count": 3},
            {"name": "b", "count": 2},
            {"name": "c", "count": 1},
            {"name": "other", "count": 3},
        ])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_the_total_bar_draws_groups_and_keeps_old_data(self):
        rows = [dict(page_row(0, None, "origin", 50), branch=""),
                dict(page_row(1, 1, "other", 50), branch="tooling/x"),
                dict(page_row(2, 2, "other", 50), branch="dashboard/x"),
                dict(page_row(3, 3, "other", 50), branch="docs/x"),
                dict(page_row(4, 4, "other", 50), branch="release")]
        error, panels, _ = drawn(rows, other_groups=[{"name": "tooling", "count": 3},
                                                     {"name": "dashboard", "count": 2},
                                                     {"name": "<b>x</b>", "count": 1}])
        self.assertIsNone(error)
        legend = panels["pr-total-legend"]
        self.assertIn("tooling · 3", legend)
        self.assertLess(legend.index("tooling · 3"), legend.index("dashboard · 2"))
        self.assertIn("&lt;b&gt;x&lt;/b&gt; · 1", legend)
        error, panels, _ = drawn(rows)
        self.assertIsNone(error)
        self.assertIn("Other · 4", panels["pr-total-legend"])


@unittest.skipUnless(shutil.which("node"), "needs node")
class CriterionPanelsTest(unittest.TestCase):
    ROW = dict(page_row(0, None, "origin", 40), code_churn=0)

    def test_attention_matrix_and_inspector(self):
        row = dict(id="C1", day=1, tag="new", claim="<b>x</b>", spec="specs/day-01.md", track="A", prs=[12], tests=["test_x"], cited=None, test_found=True, seen_red=dict(state=False, lines=["broken line"]), replay=dict(state="current", status=["failed"], why=["reason"], checks=[dict(status="failed", diagnostic="diagnostic")]), replaces=[], replaced_by=[])
        reasons = ["replay_failed", "replay_error", "replay_stale", "evidence_missing", "not_seen_red", "test_not_found", "handoff_unclaimed"]
        labels = ["Replay failed", "Replay error", "Replay stale", "Evidence missing", "Not seen red", "Test not found", "Hand-off unclaimed"]
        attention = [dict(id="C1" if i == 0 else "Day 01", reason=reason, detail=str(i)) for i, reason in enumerate(reasons)]
        error, panels, logged = drawn([self.ROW], criteria_index=[row, dict(row, id="C2", cited=False)], attention=attention)
        self.assertIsNone(error)
        self.assertEqual(logged, [])
        box = panels["attention-list"]
        self.assertLess(box.index("C1"), box.index("Day 01"))
        self.assertEqual([box.index(label) for label in labels], sorted(box.index(label) for label in labels))
        matrix = panels["criteria-matrix"]
        self.assertIn('<td><span title="not measured">–</span></td>', matrix)
        self.assertIn('<td>no</td>', matrix)
        inspector = panels["criterion-inspector"]
        self.assertTrue("specs/day-01.md" in inspector and 'href="../../' not in inspector)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", inspector)
        self.assertNotIn("<b>x</b>", inspector)
        self.assertIn("diagnostic", inspector)

    def test_old_and_empty_data(self):
        error, panels, logged = drawn([self.ROW])
        self.assertIsNone(error)
        self.assertEqual(logged, [])
        self.assertIn("Not in this data", panels["attention-list"])
        self.assertIn("Not in this data", panels["criteria-matrix"])
        _, panels, _ = drawn([self.ROW], criteria_index=[], attention=[])
        self.assertIn("Nothing needs attention", panels["attention-list"])
        self.assertIn("No criteria yet", panels["criteria-matrix"])


class PageProseTest(unittest.TestCase):
    """The words around the panels: the page explains any project's data, not the one it was built on."""

    # What the prose said of the project it was built on: its services and layout, its days, PRs and criteria, its cloud.
    MARKERS = ["monolith", "identity", "job-service", "backend/", "saved_jobs", "SavedJobCounts", "GatewayHarnessIT",
               "infra-ci", "Terraform", "LocalStack", "AWS", "Day 35", "Days 1–4", "Days 38–40", "C35.16", "H28.1",
               "#55", "#298", "#310", "wordtally", "Day 1 does not", "Day 3 tells"]

    def test_the_prose_names_none_of_the_project_it_was_built_on(self):
        with open(os.path.join(HERE, "..", "docs", "dashboard", "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertEqual([m for m in self.MARKERS if m in page], [])

    def test_every_script_it_names_is_in_the_repository(self):
        # The deployment panel named scripts/live-checks.py after it moved out with the source project's data.
        with open(os.path.join(HERE, "..", "docs", "dashboard", "index.html"), encoding="utf-8") as f:
            named = re.findall(r"<code>([\w.-]+/[\w./-]+\.py)</code>", f.read())  # a path, not a name like conftest.py
        self.assertEqual([p for p in named if not os.path.exists(os.path.join(HERE, "..", p))], [])
        self.assertIn("scripts/token-usage.py", named)  # the pattern still finds what the prose names

    def test_no_words_call_the_project_a_migration(self):
        # The lede, the Tokens text and dashboard.js's first line did; "migrations" in a list of production code is files.
        found = []
        for name in ("index.html", "dashboard.js"):
            with open(os.path.join(HERE, "..", "docs", "dashboard", name), encoding="utf-8") as f:
                found += [name for _ in re.findall(r"\bmigration\b", f.read(), re.I)]
        self.assertEqual(found, [])

    def test_a_table_header_is_in_the_body_font(self):
        # th.r shared td.r's mono font, so "Files read" over the removal checks read as code.
        with open(os.path.join(HERE, "..", "docs", "dashboard", "dashboard.css"), encoding="utf-8") as f:
            rules = re.findall(r"([^{}]+)\{([^}]*)\}", f.read())
        mono = [s.strip() for sel, body in rules if "var(--mono)" in body for s in sel.split(",")]
        self.assertIn("td.r", mono)
        self.assertEqual([s for s in mono if re.match(r"th\b", s.split()[-1])], [])


@unittest.skipUnless(shutil.which("node"), "needs node")
class PageTest(unittest.TestCase):
    """The page's own counts, on a new repository: direct commits, flat gaps, days without specs."""

    def test_merged_prs_are_the_merges_that_name_a_pr(self):
        # The kit's main: direct commits, then PR merges. Each commit is a row; only a PR merge counts.
        rows = [page_row(0, None, "origin", 50), page_row(1, None, "origin", 50), page_row(2, 1, "other", 50),
                page_row(3, None, "origin", 50), page_row(4, 2, "other", 50)]
        self.assertEqual(page(rows)[0].split(" · ", 1)[1], "between days · 2 merged PRs")

    def test_the_gap_share_leaves_out_direct_commits_and_is_a_dash_with_no_gap_closed(self):
        # A direct commit closes 10°, a spec PR widens 5°, a code PR closes 10°: the PRs closed 5°,
        # and the spec's share of it is -5 / 5. Counting the direct commit would make it -5 / 15.
        moved = [page_row(0, None, "origin", 50), page_row(1, None, "origin", 40), page_row(2, 1, "spec", 45),
                 page_row(3, 2, "code", 35)]
        self.assertEqual(page(moved)[1]("Share of the gap closed by the spec"), "-100%")
        flat = [page_row(0, None, "origin", 70.8), page_row(1, None, "origin", 70.8), page_row(2, 1, "other", 70.8)]
        self.assertEqual(page(flat)[1]("Share of the gap closed by the spec"), "–")

    def test_the_day_total_is_the_run_order_and_the_cut_days_with_specs(self):
        # Specs for Days 12 and 36 and a cut Day 29; plan.md runs 12, 18, 17, 36 and 40.
        days = [dict(a_day(12), status="active", phase=1, title="t", prs=[]), dict(a_day(36), status="ready"),
                dict(a_day(29), status="cut")]
        eyebrow, _ = page([page_row(0, None, "origin", 50)], days, [12, 18, 17, 36, 40], current_day=12)
        self.assertIn("· Day 12 of 6 ·", eyebrow)

    def test_the_readout_places_the_gap_against_its_shuffled_band_endpoints_inside(self):
        for gap, band, place in ((40, [35, 50], "inside"), (35, [35, 50], "inside"), (50, [35, 50], "inside"),
                                 (21.02, [35.69, 50.92], "below"), (50.01, [35, 50], "above")):
            row = dict(page_row(0, None, "origin", gap), code_churn=0, gap_chance_band=band)
            error, panels, _ = drawn([row])
            self.assertIsNone(error)
            self.assertIn(f"<dd>{place} the shuffled-count reference band</dd>", panels["readout"], (gap, band))
        for gap, band in ((None, [35, 50]), (40, None)):  # no spec name in the code yet; no band
            error, panels, _ = drawn([dict(page_row(0, None, "origin", gap), code_churn=0, gap_chance_band=band)])
            self.assertIn("<dd>comparison unavailable</dd>", panels["readout"], (gap, band))

    def test_the_readout_shows_both_name_shares_with_their_counts_and_the_names_behind_them(self):
        row = dict(page_row(0, None, "origin", 40), code_churn=0, name_coverage=0.75, prod_name_coverage=0.25,
                   spec_names=4, names_in_code=3, names_in_prod=1, names_absent=["gone_name"],
                   names_outside_prod={"test_only": {"test": ["tests/test_a.py"], "tooling": ["scripts/a.py"]},
                                       "other_one": {"other": ["x/<y>.txt"]}})
        error, panels, _ = drawn([row])
        self.assertIsNone(error)
        self.assertIn("<dt>Spec names in code, all places</dt><dd>75.0% <small>3/4</small></dd>", panels["readout"])
        self.assertIn("<dt>Spec names in production</dt><dd>25.0% <small>1/4</small></dd>", panels["readout"])
        self.assertIn('<code>test_only</code> <span class="chip">test</span> <code class="path">tests/test_a.py</code>'
                      ' <span class="chip">tooling</span> <code class="path">scripts/a.py</code>', panels["readout"])
        self.assertIn('<code class="path">x/&lt;y&gt;.txt</code>', panels["readout"])
        self.assertIn("Present only outside production <span class=\"num\">(2)</span>", panels["readout"])
        self.assertIn("Absent from all scanned code <span class=\"num\">(1)</span>", panels["readout"])
        self.assertIn("Test and tool names may legitimately be absent from production.", panels["readout"])
        empty = dict(row, name_coverage=None, prod_name_coverage=None, spec_names=0, names_in_code=0, names_in_prod=0,
                     names_absent=[], names_outside_prod={})
        error, panels, _ = drawn([empty])
        self.assertEqual(panels["readout"].count("<dd>not measured: no extracted spec names</dd>"), 2)
        old = {k: v for k, v in row.items() if k not in ("spec_names", "names_outside_prod", "names_absent")}
        error, panels, _ = drawn([old])  # data from before the counts were kept
        self.assertIsNone(error)
        self.assertIn("<dt>Spec names in code, all places</dt><dd>75.0%</dd>", panels["readout"])
        self.assertNotIn("Present only outside production", panels["readout"])

    def test_a_short_history_draws_every_panel_or_says_why_not(self):
        # The kit's main: direct commits, then PRs on no day branch; a flat gap, no service code,
        # 54 test lines and no production lines, and two specs whose days have no PRs.
        rows = [dict(page_row(i, pr, "other" if pr else "origin", 70.8), work=dict(test=27 * i), code_churn=27)
                for i, pr in enumerate([None, None, 1, 2])]
        days = [dict(a_day(n), status="ready", phase=1, title="t", prs=[]) for n in (12, 36)]
        error, panels, _ = drawn(rows, days, [12, 36])
        self.assertIsNone(error)
        for box in ("deltas", "churn", "heat"):  # what each merge did to the gap; spec and code; corrections
            self.assertIn("<svg", panels[box], box)
        for box, why in (("moved", "No service code yet"), ("test-ratio", "No ratio yet"),
                         ("perday", "No day has a merged pull request yet")):
            self.assertIn(f'<p class="load-error">{why}', panels[box], box)

    def test_a_panel_that_throws_says_so_and_the_panels_after_it_still_draw(self):
        # Verification that is not a list: its panel, fourth of boot()'s twelve, throws.
        rows = [dict(page_row(i, pr, "other" if pr else "origin", 70.8), work=dict(test=27 * i), code_churn=27)
                for i, pr in enumerate([None, None, 1, 2])]
        days = [dict(a_day(n), status="ready", phase=1, title="t", prs=[]) for n in (12, 36)]
        error, panels, logged = drawn(rows, days, [12, 36], verification={"kind": "code"})
        self.assertIsNone(error)
        self.assertEqual(panels["breaks"], '<p class="load-error">This panel failed: V.filter is not a function</p>')
        self.assertEqual(logged, ["V.filter is not a function"])
        for box in ("infra-head", "token-bars", "findings", "tbl", "deltas", "heat"):  # after it in boot(), then setSel()
            self.assertTrue(panels[box], box)
            self.assertNotIn("This panel failed", panels[box], box)

    def test_the_boxes_words_name_no_project(self):
        # The first merge; one service still in place and one part out; a day before the evidence
        # format; no infra summary yet. Then no service code at all.
        row = dict(page_row(0, None, "origin", 50), code_churn=0, placed={"billing": [120, 0], "orders": [30, 40]})
        day = dict(a_day(5), status="done", phase=1, title="t", prs=[], evidence_format=False,
                   evidence=[dict(ticked=True, prs=[], tests={}, claim="c")])
        error, panels, _ = drawn([row], [day], [5])
        self.assertIsNone(error)
        self.assertEqual(panels["placed"].split(": ", 1)[1],
                         "<b>billing</b> in place (120 lines) · <b>orders</b> 40 lines out, 30 still in place</p>")
        self.assertEqual(re.search(r'aria-label="([^"]*)"', panels["moved"]).group(1),
                         "Share of the services' main source lines in their own directories")
        self.assertIn("Starting point: the code and the plan written for it.", panels["readout"])
        self.assertIn('<tr title="Spec written before the evidence format">', panels["evidence"])
        self.assertEqual(panels["infra-head"], '<p class="load-error">No infra summary on main yet: '
                                               'it appears after the infra workflow next runs on main.</p>')
        _, panels, _ = drawn([page_row(0, None, "origin", 50)])
        self.assertIn("No service code yet: the share counts the packages under <code>services</code> in "
                      "<code>spec-drift.config.json</code>, and the code has none.", panels["moved"])

    def test_with_nothing_deployed_the_panel_names_the_file_and_no_script(self):
        # The live check is the project's own; the source project's moved out with its data.
        error, panels, _ = drawn([page_row(0, None, "origin", 50)], deployment=None)
        self.assertIsNone(error)
        self.assertEqual(panels["deployment-head"], '<p class="load-error">Nothing deployed yet: the panel fills when '
                                                    'a live check has written docs/dashboard/deployment.json.</p>')
        self.assertEqual(panels["deployment-checks"], "")

    def test_tokens_with_no_agent_are_drawn_as_unknown_and_not_given_to_the_checking_agents(self):
        # token-usage.py keeps a row from before agents were counted as "unknown"; an older file has no field.
        def tok(agent, output):
            return dict(date="2026-10-12", day=1, model="claude-opus-4-1", agent=agent, input=100,
                        cache_write=0, cache_read=0, output=output, replies=1)
        tokens = [tok("main", 300), tok("implementer", 200), tok("unknown", 300), tok("spec-auditor", 100)]
        tokens.append({k: v for k, v in tok("", 100).items() if k != "agent"})
        error, panels, _ = drawn([page_row(0, None, "origin", 50)], tokens=tokens)
        self.assertIsNone(error)
        bars = re.findall(r'class="hbar-label">([^<]+)<', panels["token-agents"])
        self.assertEqual(bars, ["agent not recorded", "main sessions", "implementer", "spec-auditor"])
        self.assertTrue(re.sub("<[^>]+>", "", panels["token-implementer"]).endswith(
            "the main sessions (specs, review, the breaks on purpose, the pull requests) 30.0% of the output; "
            "tokens with no agent recorded 40.0%; the checking agents the rest."), panels["token-implementer"])

    def test_the_agents_chart_labels_are_text_at_the_page_font_size(self):
        # In an SVG scaled to the screen they shrank with it: about 4 px at 375 px wide.
        tokens = [dict(date="2026-10-12", day=1, model="claude-opus-5-5", agent=a, input=100, cache_write=0,
                       cache_read=0, output=o, replies=1) for a, o in (("main", 300), ("implementer", 200))]
        error, panels, _ = drawn([page_row(0, None, "origin", 50)], tokens=tokens)
        self.assertIsNone(error)
        self.assertNotIn("<svg", panels["token-agents"])
        self.assertEqual(re.findall(r'class="hbar-label">([^<]+)<', panels["token-agents"]), ["main sessions", "implementer"])
        self.assertEqual(re.findall(r'class="hbar-value num">([^<]+)<', panels["token-agents"]), ["300", "200"])

    def test_with_no_tokens_who_spent_them_says_what_is_missing(self):
        error, panels, _ = drawn([page_row(0, None, "origin", 50)], tokens=[])
        self.assertIsNone(error)
        self.assertEqual(panels.get("token-agents"), '<p class="load-error">No agents to show: there are no token counts '
                                                 'to split by agent.</p>')

    def test_the_hooks_table_gives_one_row_per_date_in_date_order_with_the_shares(self):
        def hooks(date, bash, cut, reads, whole):
            return dict(date=date, day=1, model="claude-opus-5-5", agent="main", input=100, cache_write=0,
                        cache_read=0, output=10, replies=1, bash=bash, cut=cut, reads=reads, whole=whole)
        tokens = [hooks("2026-10-13", 8, 1, 2, 0), hooks("2026-10-12", 10, 0, 3, 2)]
        error, panels, _ = drawn([page_row(0, None, "origin", 50)], tokens=tokens)
        self.assertIsNone(error)
        rows = re.findall(r"<tr><td>.*?</tr>", panels["token-hooks"])
        cells = [[re.sub("<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r)] for r in rows]
        self.assertEqual(cells, [["2026-10-12", "10", "0 (0.0%)", "3", "2 (66.7%)"],
                                 ["2026-10-13", "8", "1 (12.5%)", "2", "0 (0.0%)"]])

    def test_the_hooks_table_sums_a_date_over_its_agents(self):
        def hooks(agent, bash, cut, reads, whole):
            return dict(date="2026-10-12", day=1, model="claude-opus-5-5", agent=agent, input=100, cache_write=0,
                        cache_read=0, output=10, replies=1, bash=bash, cut=cut, reads=reads, whole=whole)
        tokens = [hooks("main", 4, 1, 1, 0), hooks("implementer", 6, 0, 2, 1)]
        error, panels, _ = drawn([page_row(0, None, "origin", 50)], tokens=tokens)
        self.assertIsNone(error)
        rows = re.findall(r"<tr><td>.*?</tr>", panels["token-hooks"])
        self.assertEqual([[re.sub("<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r)] for r in rows],
                         [["2026-10-12", "10", "1 (10.0%)", "3", "1 (33.3%)"]])

    def test_with_no_hook_counts_the_hooks_table_says_what_is_missing(self):
        old = dict(date="2026-10-12", day=1, model="claude-opus-5-5", agent="main", input=100, cache_write=0,
                   cache_read=0, output=10, replies=1)  # a row counted before the hooks were
        for tokens in ([old], []):
            error, panels, _ = drawn([page_row(0, None, "origin", 50)], tokens=tokens)
            self.assertIsNone(error)
            self.assertEqual(panels.get("token-hooks"), '<caption><p class="load-error">No hook counts yet: run '
                                                     '<code>python scripts/token-usage.py</code>.</p></caption>')

    def test_with_no_finished_day_the_relabel_table_says_what_is_missing(self):
        # It held only an "All counted" row of dashes.
        days = [dict(a_day(1), status="ready", phase=0, title="t", prs=[])]
        error, panels, _ = drawn([page_row(0, None, "origin", 50)], days, [1])
        self.assertIsNone(error)
        relabel = panels.get("relabel", "")
        self.assertNotIn("All counted", relabel)
        self.assertIn("No finished day yet: a day is counted once its Notes quote", relabel)

    def test_the_name_phases_and_stops_come_from_the_data(self):
        days = [dict(a_day(n), status="ready", phase=p, title="t", prs=[]) for n, p in ((1, 0), (2, 0), (3, 1))]
        meta = dict(project="toy-repo", phases=["Lay the ground", "<name>"], stops=[2])
        _, panels, _ = drawn([page_row(0, None, "origin", 50)], days, [1, 2, 3], meta=meta)
        self.assertTrue(panels["eyebrow"].startswith("toy-repo · "), panels["eyebrow"])
        self.assertIn("<b>0. Lay the ground</b>", panels["roadmap"])
        self.assertIn("<b>1. &lt;name&gt;</b>", panels["roadmap"])  # a heading the template left unfilled
        stopped = re.findall(r'class="tile ready stop" data-day="(\d+)"', panels["roadmap"])
        self.assertEqual(stopped, ["2"])

    def test_a_built_page_draws_the_data_inlined_in_it(self):
        # What --build writes: dashboard.js and the data in one file. fill() writes a title's "</" as "<\/".
        rows = [dict(page_row(i, pr, "other" if pr else "origin", 70.8), work=dict(test=27 * i), code_churn=27)
                for i, pr in enumerate([None, None, 1, 2])]
        now = dict(generated="2026-01-01T00:00", head="abc", last_pr=2, current_day=None, next_step="x",
                   open_prs=[dict(number=3, title="Stop at </script> here", url="u")])
        more = dict(now=now, meta=dict(project="toy-repo", phases=["<name>"], stops=[]))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(sd.build(json.dumps(page_data(rows, **more))))
            built = drawn(page=path)
        self.assertEqual(built, drawn(rows, **more))
        error, panels, logged = built
        self.assertEqual((error, logged), (None, []))
        self.assertTrue(panels["eyebrow"].startswith("toy-repo · "), panels["eyebrow"])
        self.assertEqual([k for k, v in panels.items() if "Stop at &lt;/script&gt; here" in v], ["today"])

    def test_the_break_panel_names_the_first_pr_only_when_there_is_one(self):
        # Two code PRs, one recorded and one silent; the source project also had one before its #310.
        rows = [dict(page_row(i, pr, "other" if pr else "origin", 70.8), work=dict(test=27 * i), code_churn=27)
                for i, pr in enumerate([None, None, 1, 2])]
        pr = lambda n, breaks: dict(number=n, day=None, kind="code", breaks=breaks, pushes=1, failed=0, failed_in=[])
        new = [pr(1, "recorded"), pr(2, "silent")]
        _, panels, _ = drawn(rows, verification=new, meta=dict(break_line_from_pr=0))
        self.assertTrue(panels["breaks"].startswith("<p>1 of 2 code PRs record a break on purpose; 1 silent.</p><ul>"),
                        panels["breaks"])
        self.assertNotIn("not measured", panels["breaks"])
        _, panels, _ = drawn(rows, verification=[pr(309, "not measured")] + new, meta=dict(break_line_from_pr=310))
        self.assertTrue(panels["breaks"].startswith("<p>Since #310, 1 of 2 code PRs"), panels["breaks"])
        self.assertIn("1 code PRs before #310 are not measured.", panels["breaks"])

    def test_service_boundaries_with_no_service_say_so(self):
        # It drew "At start: " and stopped.
        _, panels, _ = drawn([dict(page_row(0, None, "origin", 50), placed={})])
        self.assertEqual(panels["placed"], "<p>At start: no service code.</p>")

    def test_removal_checks_with_none_say_none(self):
        # It drew the header row over an empty body; the panels beside it say "None: ...".
        _, panels, _ = drawn([page_row(0, None, "origin", 50)])
        self.assertEqual(panels["removals"][:21], "<tbody><tr><td>None: ")

    def test_the_spec_text_is_counted_since_the_first_merges_date(self):
        # It said "since Sep 20", the source project's start, whatever the data.
        rows = [dict(page_row(0, None, "origin", 50), date="2026-03-05", added=0, deleted=0),
                dict(page_row(1, 1, "spec", 50), date="2026-03-06", added=4, deleted=1)]
        _, panels, _ = drawn(rows, origin_lines=10)
        self.assertEqual(re.findall(r'"k">Spec text since ([^<]*)<', panels["stats"]), ["Mar 5"])
        self.assertEqual(re.findall(r"% of the ([^<]*) spec's lines", panels["findings"]), ["Mar 5"])

    def test_the_out_of_order_key_names_only_days_that_have_run(self):
        # plan.md runs Day 3 before Day 2: before Day 2's first merge there is no D02 label for a * to mark.
        rows = [page_row(0, None, "origin", 50), dict(page_row(1, 1, "code", 50), day=1)]
        error, panels, _ = drawn(rows, order=[1, 3, 2], out_of_order=[2])
        self.assertIsNone(error)
        self.assertEqual([k for k, v in panels.items() if "out of number order" in v], [])
        self.assertEqual(panels["ooo-key"], "")
        _, panels, _ = drawn(rows + [dict(page_row(2, 2, "code", 50), day=2)], order=[1, 3, 2], out_of_order=[2])
        self.assertIn('<p class="axis-note">* Day run out of number order: D02.</p>', panels["lines"])
        self.assertEqual(panels["ooo-key"], "<b>D02*</b>")

    def test_the_merge_strip_stays_inside_the_plot(self):
        # Each cell was one merge-step wide and centred on its merge: with two merges the end ones ran off the card.
        _, panels, _ = drawn([page_row(0, None, "origin", 50), page_row(1, 1, "spec", 40)])
        cells = [(float(x), float(w)) for x, w in re.findall(r'<rect x="([^"]+)" y="275" width="([^"]+)" height="8"', panels["lines"])]
        self.assertEqual(len(cells), 2)
        self.assertEqual([c for c in cells if c[0] < 44 or c[0] + c[1] > 1000 - 104], [])

    def test_a_copy_with_no_merged_pr_draws_no_nan(self):
        # A fresh copy's only row, as spec-drift.py writes it: one row put every x at NaN (N - 1 = 0),
        # "#Infinity–#-Infinity" over the table, and "NaN% came from", "below Infinity%" in the findings.
        origin = dict(i=0, sha="d1ffa9a", date="2026-10-28", pr=None, title="Start", kind="origin", day=0, added=0,
                      deleted=0, gap_full=None, gap_chance=None, gap_chance_band=None, coverage=None, name_coverage=0.0,
                      jaccard=0.0, names_kept=0, names_retired=0, spec_files={}, code_churn=0,
                      work=dict(spec=0, context=0, prod=0, test=0, tooling=0), evidence=None, placed={}, crossings={})
        error, panels, logged = drawn([origin], origin_lines=236, vocab_size=5)
        self.assertEqual((error, logged), (None, []))
        self.assertEqual({k: re.findall(r"NaN|Infinity|undefined", v) for k, v in panels.items() if re.search(r"NaN|Infinity|undefined", v)}, {})
        self.assertEqual(panels["pr-range"], "0 merges")
        # The headings are claims; with nothing merged they claim nothing.
        self.assertEqual(re.findall(r"<h3>(No [^<]*)</h3>", panels["findings"]), ["No gap has closed yet", "No day reached yet"])

    def test_one_of_a_kind_is_singular(self):
        # "1 days in", "1 spec-change PRs".
        rows = [dict(page_row(0, None, "origin", 50), added=0, deleted=0),
                dict(page_row(1, 1, "spec", 40), added=1, deleted=0, day=1), dict(page_row(2, 2, "code", 30), added=1, deleted=0, day=1)]
        _, panels, _ = drawn(rows, days=[dict(day=1, status="done", prs=[dict(number=1)])], origin_lines=10)
        text = panels["stats"] + panels["findings"]
        self.assertEqual(re.findall(r"\b1 (?:days|spec-change PRs|code PRs)\b", text), [])
        self.assertIn("1 day in,", text)
        self.assertIn("1 spec-change PR ", text)

    @unittest.skipUnless(os.path.exists(COMPARE), "kit/ removed")
    def test_the_source_projects_data_draws_the_boxes_it_drew(self):
        # kit/compare-page.py: the source project's published data through this page, against its saved 53 boxes.
        run = subprocess.run([sys.executable, COMPARE], capture_output=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


def a_report(close, rev, checks, state="clean", **more):
    """An evidence.py --json report of Day 01, checked at close on its spec and breaks file at rev."""
    day = dict(spec=dict(path="specs/day-01-x.md", sha256="0" * 64, state=state, revision=rev), day="01",
               close_sha=close, verify_base_sha=None, checks=checks,
               breaks=dict(path="specs/breaks/day-01.json", sha256=None, state="missing", revision=None))
    return dict(dict(schema=1, runner=dict(revision="r" * 40, dirty=False), generated="2026-10-29T10:00:00+00:00",
                     ref="main", ref_sha=close, days=[day]), **more)


def a_check(kind, status, label="C1.1", diagnostic=None):
    return dict(id=f"day-01-x.md/{label}", kind=kind, criterion="C1.1", sha=None, status=status, label=label,
                diagnostic=diagnostic)


class CriterionIndexTest(unittest.TestCase):
    def test_representative_evidence_and_attention(self):
        repo = Repo()
        self.addCleanup(repo.close)
        files = {"plan.md": PLAN, "tests/test_example.py": "class ExampleTest:\n    def test_ok(self): pass\n"}
        for day in range(1, 4):
            blocks = []
            for number in range(1, 5):
                cid = f"C{day}.{number}"
                test = "MissingTest.test_lost" if cid == "C2.2" else "ExampleTest.test_ok"
                red = "" if cid == "C2.1" else "      broken: guard removed → FAILED (#1)\n"
                evidence = "" if cid == "C1.4" else f"      Evidence: `{test}` (#1).\n"
                replaces = ("      Replaces: C1.1 — was: the old claim\n" if cid == "C2.4" else
                            "      Replaces: C2.4 — was: the old claim\n" if cid == "C3.4" else "")
                blocks.append(f"- [x] {cid} **hold** — Claim {cid}.\n{evidence}{red}{replaces}")
            files[f"specs/day-{day:02d}-x.md"] = (f"# Day {day:02d} — Example\n\n**Phase:** {1 if day < 3 else 2}\n\n"
                "## Tracks\n\n| A | work | C1.1 |\n\n## Acceptance criteria\n\n" + "\n".join(blocks) + "\n## Notes\n")
        repo.merge(files)
        days = []
        for day in range(1, 4):
            path = f"specs/day-{day:02d}-x.md"
            days.append(dict(day=day, phase=1 if day < 3 else 2, file=path, status="done",
                             track_work={"A": "C1.1–C1.3", "B": "C1.4"},
                             evidence=sd.criteria(sd.section(files[path], "Acceptance criteria"))))
        reports = dict(state="loaded", days=[
            dict(day=1, state="current", why=[], checks=[
                dict(criterion="C1.1", status="failed", diagnostic="wrong result"),
                dict(criterion="C1.2", status="error", diagnostic="runner failed"),
                dict(criterion="C1.3", status="passed", diagnostic=None),
                dict(criterion="C1.3", status="not_applicable", diagnostic="checked by command"),
                dict(criterion=None, status="error", diagnostic="runner crashed")]),
            dict(day=2, state="stale", why=["spec changed"], checks=[]),
            dict(day=3, state="not checked")])
        handoffs = [dict(picked=False, text="C3.1 needs follow-up", source="Day 03 Notes")]
        rows, attention = repo.inside(lambda blobs: sd.criterion_index(days, reports, handoffs, repo.snaps, blobs))
        self.assertEqual(len(rows), 12)
        by_id = {row["id"]: row for row in rows}
        self.assertEqual((by_id["C1.1"]["phase"], by_id["C1.1"]["track"]), (1, "A"))
        self.assertEqual((by_id["C1.2"]["track"], by_id["C1.4"]["track"]), ("A", "B"))
        self.assertEqual((by_id["C1.1"]["day"], by_id["C1.1"]["tag"], by_id["C1.1"]["claim"]),
                         (1, "hold", "Claim C1.1."))
        self.assertEqual(by_id["C1.1"]["spec"], "specs/day-01-x.md#acceptance-criteria")
        self.assertEqual(by_id["C1.1"]["replaced_by"], ["C2.4"])
        self.assertEqual(by_id["C3.4"]["replaces"], ["C2.4"])
        self.assertIsNone(by_id["C3.1"]["replay"]["status"])
        self.assertTrue(by_id["C1.3"]["cited"])
        self.assertTrue(by_id["C1.3"]["test_found"])
        self.assertEqual(by_id["C1.3"]["replay"]["status"], ["passed", "not_applicable"])
        self.assertEqual(by_id["C1.3"]["replay"]["why"], ["checked by command"])
        self.assertEqual(by_id["C1.3"]["replay"]["checks"], reports["days"][0]["checks"][2:4])
        self.assertEqual(by_id["C1.3"]["prs"], [1])
        self.assertEqual(by_id["C1.3"]["tests"], ["ExampleTest.test_ok"])
        self.assertEqual(by_id["C1.3"]["seen_red"], dict(state=True, lines=["broken: guard removed → FAILED (#1)"]))
        self.assertIsNone(by_id["C1.4"]["test_found"])
        self.assertFalse(by_id["C2.2"]["test_found"])
        self.assertEqual([(a["id"], a["reason"]) for a in attention], [
            ("C1.1", "replay_failed"), ("C1.2", "replay_error"), ("Day 01", "replay_error"),
            *((f"C2.{n}", "replay_stale") for n in range(1, 5)),
            ("C1.4", "evidence_missing"), *((f"C3.{n}", "evidence_missing") for n in range(1, 5)),
            ("C2.1", "not_seen_red"),
            ("C2.2", "test_not_found"), ("C3.1", "handoff_unclaimed")])
        self.assertEqual([a["detail"] for a in attention if a["id"] == "Day 01"], ["runner crashed"])
        self.assertEqual([a["id"] for a in attention if a["reason"] == "replay_error"
                          and a["id"].startswith("C1.")], ["C1.2"])
        days[0]["evidence_format"] = False
        unknown, _ = repo.inside(lambda blobs: sd.criterion_index(days, dict(state="absent"), [], repo.snaps, blobs))
        self.assertIsNone(unknown[0]["cited"])
        self.assertIsNone(unknown[0]["seen_red"]["state"])
        self.assertEqual(unknown[0]["replay"], dict(state="absent", status=None, why=[], checks=[]))


class EvidenceReportTest(unittest.TestCase):
    """The committed evidence report: read, never replayed, and current only on main's inputs."""

    def setUp(self):
        self.repo = Repo().merge({"specs/day-01-x.md": "# Day 01\n"}).merge({"app/a.py": "x = 1\n"})
        self.addCleanup(self.repo.close)
        self.close = self.repo.git("rev-parse", "HEAD").strip()
        self.days = [dict(day=1, status="done", file="specs/day-01-x.md", prs=[dict(number=1, kind="close")]),
                     dict(day=2, status="done", file="specs/day-02-y.md", prs=[]),
                     dict(day=3, status="active", file="specs/day-03-z.md", prs=[])]
        page = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, page, True)
        self.addCleanup(setattr, sd, "PAGE", sd.PAGE)
        sd.PAGE = page

    def read(self, report):
        if report is not None:
            with open(os.path.join(sd.PAGE, "evidence-checks.json"), "w", encoding="utf-8") as f:
                f.write(report if isinstance(report, str) else json.dumps(report))
        return self.repo.inside(lambda _: sd.evidence_checks(self.days, self.repo.snaps))

    def test_no_report_is_absent_not_a_pass(self):
        self.assertEqual(self.read(None), dict(state="absent"))

    def test_a_report_on_mains_inputs_is_current_and_counted_by_kind_and_status(self):
        checks = [a_check("named_test", "passed"), a_check("named_test", "not_applicable"),
                  a_check("break", "failed", "C1.2 broken: x", "reported 'y'"), a_check("runner", "error", "w", "no worktree")]
        got = self.read(a_report(self.close, self.close, checks))
        self.assertEqual(got["state"], "loaded")
        one, two = got["days"]
        self.assertEqual((one["state"], one["why"]), ("current", []))
        self.assertEqual(one["counts"]["named_test"], dict(passed=1, failed=0, error=0, not_applicable=1))
        self.assertEqual(one["counts"]["break"]["failed"], 1)
        self.assertEqual([c["status"] for c in one["checks"]], ["passed", "not_applicable", "failed", "error"])
        self.assertEqual(one["checks"][1]["criterion"], "C1.1")
        self.assertEqual([p["label"] for p in one["problems"]], ["C1.2 broken: x", "w"])
        self.assertEqual(two, dict(day=2, state="not checked"))  # finished, left out; Day 3 is not finished

    def test_a_report_checked_elsewhere_is_stale(self):
        first = self.repo.snaps[0]["sha"]
        self.assertIn("checked at close", self.read(a_report(first, self.close, []))["days"][0]["why"][0])
        self.assertIn("was edited when checked", self.read(a_report(self.close, self.close, [], state="edited"))["days"][0]["why"][0])
        self.repo.merge({"specs/day-01-x.md": "# Day 01, edited\n"})
        got = self.read(a_report(self.close, self.close, []))["days"][0]
        self.assertEqual(got["state"], "stale")
        self.assertEqual(got["why"], [f"specs/day-01-x.md changed since {self.close[:7]}"])

    def test_a_report_this_cannot_read_is_invalid_with_its_reason(self):
        for report, why in (("{not json", "JSONDecodeError"), (dict(a_report(self.close, self.close, []), schema=2), "schema 2"),
                            (a_report(self.close, self.close, [a_check("verify", "skipped")]), "status 'skipped'"),
                            (dict(schema=1), "KeyError")):
            got = self.read(report)
            self.assertEqual(got["state"], "invalid", why)
            self.assertIn(why, got["diagnostic"])


@unittest.skipUnless(shutil.which("node"), "needs node")
class EvidenceReportPageTest(unittest.TestCase):
    """The page draws the report as spec-drift.py read it, and never a missing observation as a pass."""
    ROW = dict(page_row(0, None, "origin", 40), code_churn=0)

    def test_no_report_and_an_unreadable_one_say_so(self):
        _, panels, _ = drawn([self.ROW])
        self.assertIn("Not checked: no report.", panels["evidence-checks"])
        _, panels, _ = drawn([self.ROW], evidence_checks=dict(state="invalid", diagnostic="ValueError: <b>"))
        self.assertIn("Report unavailable: ValueError: &lt;b&gt;", panels["evidence-checks"])

    def test_each_kind_is_a_share_of_what_ran_and_stale_and_unchecked_days_are_marked(self):
        n = lambda p, f, e, na: dict(passed=p, failed=f, error=e, not_applicable=na)
        ec = dict(state="loaded", generated="2026-10-29T10:00", ref="main", ref_sha="abcdef0123", runner=dict(revision="1234567890"),
                  days=[dict(day=1, state="current", why=[], counts=dict(named_test=n(3, 1, 0, 2), verify=n(0, 0, 0, 1)),
                             problems=[dict(kind="break", status="failed", label="<script>x", diagnostic=None)]),
                        dict(day=2, state="stale", why=["checked at close aaaaaaa, main's is bbbbbbb"], counts={}, problems=[]),
                        dict(day=3, state="not checked")])
        error, panels, _ = drawn([self.ROW], evidence_checks=ec)
        self.assertIsNone(error)
        box = panels["evidence-checks"]
        self.assertIn('<td class="r none">3/4 passed · 2 n/a</td>', box)
        self.assertIn('<td class="r pre">– · 1 n/a</td>', box)  # nothing ran: no share
        self.assertIn('<td class="r pre">none</td>', box)
        self.assertIn("stale</span> checked at close aaaaaaa", box)
        self.assertIn("not checked: the report leaves this day out", box)
        self.assertIn("&lt;script&gt;x", box)
        self.assertNotIn("<script>", box)

    def test_the_report_changes_no_other_panel(self):
        _, without, _ = drawn([self.ROW])
        _, with_it, _ = drawn([self.ROW], evidence_checks=dict(state="invalid", diagnostic="x"))
        self.assertEqual({k: v for k, v in without.items() if k != "evidence-checks"},
                         {k: v for k, v in with_it.items() if k != "evidence-checks"})


def a_day(n, ticked=1, total=1, kinds=(), titles=()):
    return dict(day=n, status="provisional", criteria=dict(ticked=ticked, total=total),
                prs=[dict(kind=k, title=t) for k, t in zip(kinds, list(titles) + [""] * len(kinds))],
                tracks=["A"], tracks_merged=[], track_work={"A": "work"})


class RoadmapTest(unittest.TestCase):
    """Where the dashboard says the work is: the run order plan.md gives, and what counts as done."""

    PLAN = """## Day-by-day specs

Days keep their numbers; they run in this order:

1. The record fix and the platform step (new day specs, numbered from 38).
2. Phase 3: Days 18, 19, 17, 20.
3. Phase 4: the profile endpoint out of Day 24 first, then Days 21, 22, 23, the rest of 24.
4. Phase 5: Days 26, 27, 25, 28. **Stop and evaluate.**
5. Phases 6–7 (Days 29–37): rewritten after the evaluation, or not started.
"""

    def test_a_spec_change_must_touch_a_spec(self):
        self.assertEqual(sd.kind("Spec change: every criterion is new or hold", "specs/criterion-kinds",
                                 ["specs/README.md", "specs/_template.md"]), "other")
        self.assertEqual(sd.kind("Day 16 spec change: checks", "day-16/spec-x", ["specs/day-16-cutover.md"]), "spec")
        self.assertEqual(sd.kind("Plan change: seam first", "plan/course", ["plan.md"]), "spec")
        self.assertEqual(sd.kind("Add spec-drift.py", "tooling/spec-drift-script", []), "other")

    def test_code_is_a_track_and_an_early_day_has_no_exception(self):
        self.assertEqual(sd.kind("Day 01 track A: the harness", "day-01/track-a-harness"), "code")
        self.assertEqual(sd.kind("Day 01: the harness", "day-01/harness"), "other")
        self.assertEqual(sd.kind("Day 02: a fix", "day-02/fix"), "other")

    def test_a_commit_with_no_pr_keeps_its_subject_and_a_repo_without_main_says_so(self):
        repo = Repo().merge({"plan.md": "# Plan\n"})
        self.addCleanup(repo.close)
        repo.git("commit", "--amend", "-q", "-m", "Start the toy project")
        repo.git("branch", "-M", "trunk")
        try:
            said = repo.inside(lambda blobs: sd.snapshots({}))
        except SystemExit as e:
            said = str(e)
        except subprocess.CalledProcessError as e:
            said = f"git exited {e.returncode}"
        self.assertEqual(said, "spec-drift.py reads the branch main, and this repository has none: git branch -m main")
        repo.git("branch", "-M", "main")
        snaps = repo.inside(lambda blobs: sd.snapshots({}))
        self.assertEqual([(s["pr"], s["title"]) for s in snaps], [(None, "Start the toy project")])

    def test_a_squash_commit_reads_the_merged_pr_like_a_merge(self):
        repo = Repo().merge({"plan.md": "# Plan\n"})
        self.addCleanup(repo.close)
        repo.git("branch", "-M", "main")
        repo.git("commit", "--allow-empty", "-q", "-m", "Day 01 track A: thing (#5)")
        prs = {5: dict(number=5, state="MERGED", headRefName="day-01/track-a-thing",
                       title="Day 01 track A: thing")}
        squash = repo.inside(lambda blobs: sd.snapshots(prs))[-1]
        repo.git("checkout", "-q", "-b", "side")
        repo.git("commit", "--allow-empty", "-q", "-m", "side")
        repo.git("checkout", "-q", "main")
        repo.git("merge", "--no-ff", "-q", "side", "-m", "Merge pull request #5 from side")
        merge = repo.inside(lambda blobs: sd.snapshots(prs))[-1]
        self.assertEqual((squash["pr"], squash["title"], squash["branch"]),
                         (5, prs[5]["title"], prs[5]["headRefName"]))
        self.assertEqual(sd.kind(squash["title"], squash["branch"]), sd.kind(merge["title"], merge["branch"]))
        self.assertEqual(sd.kind(squash["title"], squash["branch"]), "code")

    def test_a_squash_subject_without_a_merged_pr_keeps_the_subject(self):
        repo = Repo().merge({"plan.md": "# Plan\n"})
        self.addCleanup(repo.close)
        repo.git("branch", "-M", "main")
        subject = "Day 01 track A: thing (#5)"
        repo.git("commit", "--allow-empty", "-q", "-m", subject)
        for prs in ({}, {5: dict(number=5, state="OPEN", title="other", headRefName="day-01/track-a-thing")}):
            with self.subTest(prs=prs):
                snap = repo.inside(lambda blobs: sd.snapshots(prs))[-1]
                self.assertEqual((snap["pr"], snap["title"], snap["branch"]), (None, subject, ""))

    def test_a_two_parent_commit_with_a_squash_subject_is_not_a_pr(self):
        repo = Repo().merge({"plan.md": "# Plan\n"})
        self.addCleanup(repo.close)
        repo.git("branch", "-M", "main")
        repo.git("checkout", "-q", "-b", "side")
        repo.git("commit", "--allow-empty", "-q", "-m", "side")
        repo.git("checkout", "-q", "main")
        subject = "Day 01 track A: thing (#5)"
        repo.git("merge", "--no-ff", "-q", "side", "-m", subject)
        prs = {5: dict(number=5, state="MERGED", title="other", headRefName="day-01/track-a-thing")}
        snap = repo.inside(lambda blobs: sd.snapshots(prs))[-1]
        self.assertEqual((snap["pr"], snap["title"], snap["branch"]), (None, subject, ""))

    def test_phases_are_the_plans_headings_and_stops_the_days_its_list_marks(self):
        plan = """# Toy plan

### Phase 0 — Lay the ground
### Phase 2 – Build it (later)

## Day-by-day specs

1. Phase 0: Days 3, 1, 2. **Stop and evaluate.**
2. Phase 2: Days 4-6. **Stop and evaluate.**
3. Phase 2: Day 7.

## Course correction after Phase 0

Go on.
"""
        self.assertEqual(sd.phases(plan), ["Lay the ground", "", "Build it (later)"])
        self.assertEqual(sd.stops(plan), [2, 6])  # Day 2's stop, lifted since, is still marked
        self.assertEqual(sd.run_order(plan, list(range(1, 8)))[1], 6)
        self.assertEqual((sd.phases("# Plan\n"), sd.stops("# Plan\n")), ([], []))

    def test_a_course_corrections_entry_naming_the_phase_lifts_its_stop(self):
        # a pilot, Day 4: the stop's decisions went under the template's "Course corrections",
        # and Days 2, 4 and 5 ran under "Day 03 is done: stop".
        plan = ("# Plan\n\n## Course corrections\n\n<!-- A stop's decision, e.g. after Phase 0. -->\n\n{}\n"
                "## Day-by-day specs\n\n1. Phase 0: Days 3, 1. **Stop and evaluate.**\n2. Phase 1: Days 2, 4.\n")
        stop = lambda entry: sd.run_order(plan.format(entry), list(range(1, 5)))[1]
        self.assertEqual([stop(""), stop("- **Phase 0 stop:** go on.\n"), stop("- **Phase 10 stop:** go on.\n")],
                         [1, None, 1])  # the comment's "Phase 0" lifts nothing

    def test_the_plan_templates_list_marks_its_stop_once_filled(self):
        with open(os.path.join(HERE, "..", "plan.md"), encoding="utf-8") as f:
            plan = f.read()
        if not plan.startswith("# <Project> plan"):
            self.skipTest("plan.md is filled in")
        self.assertEqual((sd.phases(plan), sd.stops(plan)), (["<name>", "<name>"], []))  # no day yet
        filled = plan.replace("Days <NN>, <NN>.", "Days 1, 2.").replace("Days <NN>-<NN>.", "Days 3-5.")
        self.assertEqual((sd.stops(filled), sd.run_order(filled, [])), ([2], ([1, 2, 3, 4, 5], 2)))

    def test_the_run_order_is_the_plans(self):
        order, stop = sd.run_order(self.PLAN, list(range(1, 38)))
        self.assertEqual(order[:17], list(range(1, 17)) + [sd.PLATFORM])
        self.assertEqual(order[17:29], [18, 19, 17, 20, 21, 22, 23, 24, 26, 27, 25, 28])
        self.assertEqual(order[29:], list(range(29, 38)))
        self.assertEqual(stop, 28)
        self.assertEqual(sd.run_order(self.PLAN, list(range(1, 40)))[0][16:18], [38, 39])

    def test_a_day_under_10_is_a_day_and_only_a_phase_number_is_not(self):
        plan = "## Day-by-day specs\n\n1. Phase 0: Days 3, 1, 2. **Stop and evaluate.**\n2. Phase 1: Days 4-5.\n"
        self.assertEqual(sd.run_order(plan, [1, 2, 3, 4, 5]), ([3, 1, 2, 4, 5], 2))

    def test_the_source_projects_plan_runs_in_its_order(self):
        # The source project's list as it ended: phase numbers inside words and after "after".
        plan = """## Day-by-day specs

1. The record fix and the platform step (new day specs, numbered from 38).
2. Phase 3: Days 18, 19, 17, 20.
3. Phase 4: Day 41 (the profile endpoint and user id, split out of Day 24) first, then Days 21, 22
   (no dual write), 23, 24.
4. Phase 5: Days 26, 27, 25, 28. **Stop and evaluate.**
5. The cleanup day (course correction after Phase 5), Day 42; done.
6. Phase 7 for the cloud, in this order: Days 32, 33 (the code before the cloud), 36 (the services in
   the cloud), 34, 35; done.
7. Phase 6 (Days 29–31): **cut** after Phase 7 (*Phase 7 outcome*).
8. The close-out day, Day 43: the end-of-Phase-7 audits' findings; done.

## Course correction after Phase 5

The cleanup day first.
"""
        specs = list(range(1, 37)) + list(range(38, 44))  # its specs: no Day 37; Days 29-31 written, then cut
        order, stop = sd.run_order(plan, specs)
        self.assertEqual(order, list(range(1, 17)) + [38, 39, 40, 18, 19, 17, 20, 41, 21, 22, 23, 24, 26, 27, 25, 28,
                                                      42, 32, 33, 36, 34, 35, 43])
        self.assertIsNone(stop)

    def test_the_days_out_of_order_are_those_that_break_the_rising_run(self):
        order = list(range(1, 17)) + [38, 39, 40, 18, 19, 17, 20, 21, 26, 27, 25, 28, sd.PLATFORM]
        self.assertEqual(sd.out_of_order(order), [38, 39, 40, 17, 25])
        self.assertEqual(sd.out_of_order(list(range(1, 10))), [])
        self.assertEqual(sd.out_of_order([]), [])

    def test_a_plan_and_its_specs_parse(self):
        root = tempfile.mkdtemp()
        os.mkdir(os.path.join(root, "specs"))
        for n in range(10, 19):
            open(os.path.join(root, "specs", f"day-{n}-x.md"), "w").close()
        with open(os.path.join(root, "plan.md"), "w", encoding="utf-8") as f:
            f.write("## Day-by-day specs\n\n1. Phase 2: Days 12, 11, 13.\n"
                    "2. Phase 3: Days 14, 15. **Stop and evaluate.**\n3. Phase 4 (Days 16–17): **cut**.\n"
                    "4. Phase 5: Day 18.\n\n## Course correction after Phase 3\n\nGo on.\n")
        with open(os.path.join(root, "plan.md"), encoding="utf-8") as f:
            plan = f.read()
        days = sorted({sd.day_of(n) for n in os.listdir(os.path.join(root, "specs")) if sd.day_of(n)})
        order, stop = sd.run_order(plan, days)
        self.assertEqual(sorted(d for d in order if d not in sd.PLACEHOLDERS), [d for d in days if d not in (16, 17)])
        self.assertIsNone(stop)  # evaluated: the plan has the course correction after Phase 3

    def test_the_cleanup_day_comes_after_the_stop_once_it_is_evaluated(self):
        plan = self.PLAN.replace("5. Phases 6–7 (Days 29–37): rewritten after the evaluation, or not started.",
                                 "5. The cleanup day: its spec is written next, numbered\n"
                                 "   after the last.\n"
                                 "6. Phases 6–7 (Days 29–37).") + "\n## Course correction after Phase 5\n\nCleanup first.\n"
        order, stop = sd.run_order(plan, list(range(1, 39)))
        self.assertIsNone(stop)
        self.assertEqual(order[order.index(28) + 1], sd.CLEANUP)
        days = sd.settle([a_day(28, kinds=["close"]), a_day(29)], [28, sd.CLEANUP, 29])
        self.assertEqual(sd.next_step(days, [], [28, sd.CLEANUP, 29], None, sd.unwritten_lines(plan)),
                         'Day 30: write its day spec (plan.md: "The cleanup day: its spec is written next, numbered after the last.")')
        days[0]["phase"], days[1]["phase"] = 5, 6
        self.assertEqual(sd.unwritten(days, [28, sd.CLEANUP, 29]),
                         dict(day=30, phase=5, title="The cleanup day"))  # the roadmap shows it after Day 28
        self.assertIsNone(sd.unwritten(days, [28, 29]))
        order, _ = sd.run_order(plan, list(range(1, 40)))
        self.assertEqual(order[order.index(28) + 1], 39)  # written: the day takes the placeholder's place
        self.assertEqual(sd.run_order(plan.split("\n## Course correction")[0], list(range(1, 42)))[1], 28)

    def test_a_cut_phase_leaves_the_order_and_the_close_out_day_comes_last(self):
        plan = self.PLAN.replace("5. Phases 6–7 (Days 29–37): rewritten after the evaluation, or not started.",
                                 "5. Phase 7: Days 32-35.\n"
                                 "6. Phase 6 (Days 29–31): **cut** after Phase 7.\n"
                                 "7. The close-out day, numbered after the last.") + "\n## Course correction after Phase 5\n"
        order, stop = sd.run_order(plan, list(range(1, 36)))
        self.assertNotIn(29, order)  # not first, as a day the list does not name would be
        self.assertEqual(order[-2:], [35, sd.CLOSEOUT])
        days = sd.settle([a_day(35, kinds=["close"]), a_day(36)], [35, sd.CLOSEOUT])
        days[0]["phase"] = 7
        self.assertEqual(sd.next_step(days, [], [35, sd.CLOSEOUT], None, sd.unwritten_lines(plan)),
                         'Day 37: write its day spec (plan.md: "The close-out day, numbered after the last.")')
        self.assertEqual(sd.unwritten(days, [35, sd.CLOSEOUT]), dict(day=37, phase=7, title="The close-out day"))
        order, _ = sd.run_order(plan, list(range(1, 36)) + [39])  # after the last named, Day 38
        self.assertEqual(order[-1], 39)  # written: the day takes the placeholder's place
        self.assertEqual(sd.run_order(plan.replace("**cut**", "cut"), list(range(1, 36)))[0][-4:-1], [29, 30, 31])

    def test_a_higher_number_does_not_finish_a_day_that_runs_later(self):
        days = sd.settle([a_day(17), a_day(18, kinds=["code"])], [18, 17])
        self.assertEqual([d["status"] for d in days], ["provisional", "active"])  # 17 waits for 18

    def test_a_planned_day_without_a_spec_is_next_to_write(self):
        plan = "## Day-by-day specs\n\n1. Phase 1: Days 10-12.\n"
        order, _ = sd.run_order(plan, [10, 12])
        self.assertEqual(order, [10, 11, 12])  # plan.md lists Day 11; specs/ has no file for it
        days = sd.settle([a_day(10), a_day(12)], order)
        self.assertEqual(sd.next_step(days, [], order, None),
                         "Day 10: read the spec against the code, then the spec-change PR")  # as before
        days = sd.settle([a_day(10, kinds=["close"]), a_day(12)], order)
        self.assertEqual([d["status"] for d in days], ["done", "provisional"])  # none active: between days
        self.assertEqual(sd.next_step(days, [], order, None),
                         "Day 11: write its day spec (plan.md lists it, specs/ has none)")

    def test_an_unwritten_day_quotes_the_plan_item_it_comes_from(self):
        plan = ("## Day-by-day specs\n\n1. Phase 1: Days 1-2.\n2. A step the plan adds, numbered\n"
                "   from 20.\n3. One more day, numbered after the last.\n")
        self.assertEqual(sd.unwritten_lines(plan), {sd.PLATFORM: "A step the plan adds, numbered from 20.",
                                                    sd.CLEANUP: "One more day, numbered after the last."})
        order, _ = sd.run_order(plan, [1, 2])
        days = sd.settle([a_day(1, kinds=["close"]), a_day(2, kinds=["close"])], order)
        self.assertEqual(sd.next_step(days, [], order, None, sd.unwritten_lines(plan)),
                         'Day 20: write its day spec (plan.md: "A step the plan adds, numbered from 20.")')
        order, _ = sd.run_order(plan, [1, 2, 20])
        days = sd.settle([a_day(1, kinds=["close"]), a_day(2, kinds=["close"]), a_day(20, kinds=["close"])], order)
        self.assertEqual(sd.next_step(days, [], order, None, sd.unwritten_lines(plan)),
                         'Day 21: write its day spec (plan.md: "One more day, numbered after the last.")')

    def test_a_finished_day_with_boxes_open_is_closed_not_done(self):
        days = sd.settle([a_day(1, ticked=0, total=7), a_day(2), a_day(3, kinds=["code"])], [1, 2, 3])
        self.assertEqual([d["status"] for d in days], ["closed", "done", "active"])

    def test_a_split_close_ends_the_day_only_with_its_last_part(self):
        part1 = a_day(43, ticked=1, total=27, kinds=["close"], titles=["Day 43 close, part 1: the ledger"])
        self.assertEqual(sd.settle([part1], [43])[0]["status"], "active")  # #428: boxes still open
        part2 = a_day(43, ticked=27, total=27, kinds=["close", "close"], titles=["Day 43 close, part 1", "Day 43 close, part 2"])
        self.assertEqual(sd.settle([part2], [43])[0]["status"], "done")
        whole = a_day(5, ticked=5, total=7, kinds=["close"], titles=["Day 05: tick the boxes"])
        self.assertEqual(sd.settle([whole], [5])[0]["status"], "closed")  # one close, boxes left open

    def test_the_next_step_is_the_platform_then_the_stop(self):
        days = sd.settle([a_day(16, kinds=["close"]), a_day(17)], [16, sd.PLATFORM, 17])
        lines = {sd.PLATFORM: "New day specs, numbered from 38."}
        self.assertEqual(sd.next_step(days, [], [16, sd.PLATFORM, 17], None, lines),
                         'Day 38: write its day spec (plan.md: "New day specs, numbered from 38.")')
        days = sd.settle([a_day(28, kinds=["close"]), a_day(29)], [28, 29])
        self.assertIn("stop and evaluate", sd.next_step(days, [], [28, 29], 28))

    def test_with_no_day_specs_the_next_step_is_not_that_every_day_is_done(self):
        # The empty starter read "Every day is done." with 0 days.
        self.assertEqual(sd.next_step([], [], [], None),
                         "No day specs yet: fill in plan.md, then write the first day's spec (specs/README.md).")
        self.assertEqual(sd.next_step(sd.settle([a_day(1, kinds=["close"])], [1]), [], [1], None), "Every day is done.")

    def test_numbered_tracks_are_their_own_and_a_split_track_is_its_letter(self):
        tracks_section = """| Track | Owner | Work |
|---|---|---|
| 0 | | Work |
| A1 | | Work |
| A2 | | Work |
| B | | Work |"""
        self.assertEqual(sd.track_names(tracks_section), ["0", "A1", "A2", "B"])
        self.assertEqual(sd.merged_tracks(["day-39/track-0-x", "day-39/track-a1-key"], ["0", "A1", "A2", "B"]),
                         ["0", "A1"])
        self.assertEqual(sd.merged_tracks(["day-15/track-c1-rate", "day-15/track-c2-cors", "day-38/track-c-fix-harness"],
                                          ["0", "A", "B", "C", "D"]),
                         ["C"])
        self.assertEqual(sd.merged_tracks(["day-39/spec-written"], ["0", "A1", "A2", "B"]), [])
        # A track split again past the gate (Day 17's B1 into b1a..b1c): the letter after the digits
        # is a part of that track, not a track of its own.
        self.assertEqual(sd.merged_tracks(["day-17/track-b1a-service-key", "day-17/track-b1c-trusted-callers",
                                           "day-17/track-b2b-internal-client-and-breaker"],
                                          ["0", "A1", "A2", "B1", "B2", "C1"]),
                         ["B1", "B2"])
        # A table that names the letter (Day 20's 0a, 0b) makes it a track of its own.
        self.assertEqual(sd.track_names("| 0a | | Work |\n| 0b | | Work |\n| A | | Work |"), ["0a", "0b", "A"])
        self.assertEqual(sd.merged_tracks(["day-20/track-0a-jobs-db-seams"], ["0a", "0b", "A"]), ["0a"])

    def test_a_branch_that_names_no_day_is_read_by_its_title(self):
        # #321, Day 28's Track D, came from a branch that named no day.
        pr = dict(headRefName="claude/focused-bell-qqwqdx",
                  title="ABC-82 Day 28 track D: stopping-point review, plan changed after Phase 5")
        self.assertEqual(sd.merged_tracks([sd.day_branch(pr)], ["0", "A1", "E", "D"]), ["D"])
        # A day's own branch wins over its title, and a title that names no track changes nothing.
        own = dict(headRefName="day-28/track-e-ABC-84-account-healthcheck", title="Day 28 track D: wrong")
        self.assertEqual(sd.day_branch(own), own["headRefName"])
        other = dict(headRefName="claude/focused-sagan-96xo1w", title="account-service README: pull the image")
        self.assertEqual(sd.day_branch(other), other["headRefName"])


if __name__ == "__main__":
    unittest.main()
