"""The token counts move with the replies and only then.

Each test writes a small transcript to a temporary folder and runs token-usage.py's count over it:
a reply is counted once, from its last streamed line, and the day comes from the branch.

    python scripts/test_token_usage.py
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("token_usage", os.path.join(HERE, "token-usage.py"))
tu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tu)


def line(message_id="msg_1", request_id="req_1", output=3, branch="day-43/track-a2-x"):
    """One assistant line, as Claude Code writes it per streamed content block."""
    return json.dumps({
        "type": "assistant",
        "requestId": request_id,
        "timestamp": "2026-10-12T10:00:00Z",
        "gitBranch": branch,
        "message": {
            "id": message_id,
            "model": "claude-haiku-5-5-20260101",
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens": output},
        },
    })


class CountTest(unittest.TestCase):
    def count(self, lines):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return tu.count([path])

    def rows(self, totals):
        return [(key, dict(t)) for key, t in totals.items()]

    def test_a_reply_is_counted_from_its_last_line(self):
        totals = self.count([line(output=3), line(output=120)])
        rows = self.rows(totals)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1]["output"], 120)
        self.assertEqual(rows[0][1]["replies"], 1)

    def test_identical_repeats_count_once(self):
        totals = self.count([line(output=50), line(output=50), line(output=50)])
        rows = self.rows(totals)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1]["output"], 50)
        self.assertEqual(rows[0][1]["replies"], 1)

    def test_different_replies_add_up(self):
        totals = self.count([line(message_id="msg_1", output=10), line(message_id="msg_2", output=20)])
        rows = self.rows(totals)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1]["output"], 30)
        self.assertEqual(rows[0][1]["replies"], 2)

    def test_the_day_comes_from_the_branch(self):
        day43 = self.rows(self.count([line(branch="day-43/track-a2-x")]))
        self.assertEqual(day43[0][0][1], 43)
        main = self.rows(self.count([line(branch="main")]))
        self.assertIsNone(main[0][0][1])

    def test_a_cut_bash_result_and_a_whole_read_land_on_the_reply_row(self):
        cut = "a" * 3000 + "\n[cut 2000 chars; full output: x.txt; use Read with offset/limit or grep]\n"
        with tempfile.TemporaryDirectory() as folder:
            big = os.path.join(folder, "big.md")
            with open(big, "w", encoding="utf-8") as f:
                f.write("x" * 9_000)
            rows = self.rows(self.count([call("t1", "Bash"), result("t1", cut), call("t2", "Read", {"file_path": big}), result("t2", "x")]))
        self.assertEqual(len(rows), 1)
        self.assertEqual({k: rows[0][1][k] for k in tu.TOOL_FIELDS}, dict(bash=1, cut=1, reads=1, whole=1))

    def test_a_request_blob_repeating_a_bash_call_does_not_hide_it(self):
        cut = "a" * 3000 + "\n[cut 2000 chars; full output: x.txt; use Read with offset/limit or grep]\n"
        blob = json.dumps({"type": "api-request-blob", "hash": "h", "sessionId": "s",
                           "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}})
        rows = self.rows(self.count([call("t1", "Bash"), result("t1", cut), blob]))
        self.assertEqual(len(rows), 1)
        self.assertEqual({k: rows[0][1][k] for k in tu.TOOL_FIELDS}, dict(bash=1, cut=1, reads=0, whole=0))

    def test_tool_calls_on_different_dates_go_to_two_rows(self):
        rows = self.rows(self.count([call("t1", "Bash", when="2026-11-05T10:00:00Z"), result("t1", "short"),
                                     call("t2", "Bash", when="2026-11-06T10:00:00Z"), result("t2", "short")]))
        self.assertEqual(sorted((key[0], t["bash"]) for key, t in rows), [("2026-11-05", 1), ("2026-11-06", 1)])


class TranscriptsTest(unittest.TestCase):
    """Only this repository's transcript folders are read: its own and its worktrees'."""

    def test_a_sibling_whose_name_starts_with_this_ones_is_not_read(self):
        root = tempfile.mkdtemp()
        name = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.join(HERE, os.pardir)))
        for folder in (name, name + "--claude-worktrees-a", name + "-pilot", name + "2", "other"):
            os.mkdir(os.path.join(root, folder))
            with open(os.path.join(root, folder, "s.jsonl"), "w", encoding="utf-8") as f:
                f.write(line())
        read = sorted(os.path.basename(os.path.dirname(p)) for p in tu.transcripts(root))
        self.assertEqual(read, [name, name + "--claude-worktrees-a"])


def native(path):
    # The test builds its files with its own \\?\ prefix, not the script's, so it runs on main's code too.
    return "\\\\?\\" + path if os.name == "nt" else path


class LongPathTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, native(self.root))
        name = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.join(HERE, os.pardir)))
        self.project = os.path.join(self.root, name)

    def write(self, path, text):
        os.makedirs(native(os.path.dirname(path)), exist_ok=True)
        with open(native(path), "w", encoding="utf-8") as f:
            f.write(text)

    def test_transcript_past_max_path_is_counted(self):
        # A subagent's transcript in a folder past 260 characters, beside a session at a short path.
        self.write(os.path.join(self.project, "short.jsonl"), line(output=3, branch="main") + "\n")
        folder = os.path.join(self.project, "s" * 200, "subagents")
        self.write(os.path.join(folder, "agent-" + "a" * 100 + ".jsonl"), line(message_id="msg_2", output=7, branch="main") + "\n")
        self.write(os.path.join(folder, "agent-" + "a" * 100 + ".meta.json"), '{"agentType": "implementer"}')
        self.assertGreater(len(folder), 260)
        totals = tu.count(tu.transcripts(self.root))
        self.assertEqual({who: t["output"] for (_, _, _, who), t in totals.items()},
                         {"main": 3, "implementer": 7})


class MergeTest(unittest.TestCase):
    """The rows already written are kept, those from before agents were counted among them."""

    def test_a_row_with_no_agent_keeps_its_count_as_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "token-usage.json")
            model = "claude-haiku-5-5"  # as rows store it, its date taken off
            legacy = dict(date="2026-10-12", day=43, model=model, input=10, cache_write=0, cache_read=0, output=100, replies=4)
            other = dict(legacy, date="2026-10-11", output=40, replies=2)
            with open(out, "w", encoding="utf-8") as f:
                json.dump([legacy, other], f)
            # Still on disk: one reply of the 2026-10-12 total, the main session's.
            project = os.path.join(root, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.join(HERE, os.pardir))))
            os.makedirs(project)
            with open(os.path.join(project, "s.jsonl"), "w", encoding="utf-8") as f:
                f.write(line(output=30) + "\n")
            run = subprocess.run([sys.executable, os.path.join(HERE, "token-usage.py"), "--transcripts",
                                  os.path.join(root, "projects"), "--out", out], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            with open(out, encoding="utf-8") as f:
                rows = {(r["date"], r["agent"]): (r["output"], r["replies"]) for r in json.load(f)}
        self.assertEqual(rows, {("2026-10-11", "unknown"): (40, 2),
                                ("2026-10-12", "main"): (30, 1), ("2026-10-12", "unknown"): (70, 3)})

    def test_a_kept_row_with_no_tool_fields_merges_and_takes_the_counted_ones(self):
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "token-usage.json")
            kept = dict(date="2026-11-05", day=43, model="claude-haiku-5-5", agent="main", input=10, cache_write=0,
                        cache_read=0, output=100, replies=4)
            with open(out, "w", encoding="utf-8") as f:
                json.dump([kept], f)
            project = os.path.join(root, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.join(HERE, os.pardir))))
            os.makedirs(project)
            with open(os.path.join(project, "s.jsonl"), "w", encoding="utf-8") as f:
                f.write(call("t1", "Bash") + "\n" + result("t1", "short") + "\n")
            run = subprocess.run([sys.executable, os.path.join(HERE, "token-usage.py"), "--transcripts",
                                  os.path.join(root, "projects"), "--out", out], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            with open(out, encoding="utf-8") as f:
                rows = {(r["date"], r["agent"]): tuple(r[k] for k in ("bash", "cut", "reads", "whole", "output")) for r in json.load(f)}
        self.assertEqual(rows, {("2026-11-05", "main"): (1, 0, 0, 0, 100)})


def reply(message_id, context, sidechain=False, model=None, when=None):
    """One assistant line whose context (all input tokens, cached or not) is `context`."""
    entry = json.loads(line(message_id=message_id, request_id="req_" + message_id))
    entry["message"]["usage"] = {"input_tokens": 1, "cache_creation_input_tokens": 9,
                                 "cache_read_input_tokens": context - 10, "output_tokens": 5}
    entry["message"]["model"] = model or entry["message"]["model"]
    entry["timestamp"] = when or entry["timestamp"]
    entry["isSidechain"] = sidechain
    return json.dumps(entry)


def tool_result():
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}})


class SimulateTest(unittest.TestCase):
    """What a fresh session past a size, or a cap on each tool result, would have saved."""

    def simulate(self, lines):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return tu.simulate([path])

    def test_a_fresh_session_restarts_from_the_first_replys_context(self):
        # 50k, 150k, 250k, the second streamed twice. Past 100k, the session restarts at 50k twice:
        # it reads 50k, 50k (saved 100k) and 50k + 100k = 150k, over 100k again, so 50k (saved 200k).
        s = self.simulate([reply("1", 50_000), reply("2", 150_000), reply("2", 150_000), reply("3", 250_000)])
        self.assertEqual((s["read"], s["replies"], s["sessions"]), (450_000, 3, 1))
        self.assertEqual(s["cut"], {100_000: 300_000, 200_000: 200_000, 300_000: 0})

    def test_a_tool_result_is_capped_for_every_reply_that_reads_it(self):
        # A tool result adds 10k and is read by two replies; a typed message adds 1k and is not capped.
        s = self.simulate([reply("1", 10_000), tool_result(), reply("2", 20_000), reply("3", 21_000)])
        self.assertEqual(s["cap"], {2_000: 16_000, 5_000: 10_000})

    def test_a_compaction_starts_a_new_segment(self):
        # 250k, compacted to 40k, then 260k: the 260k restarts from 40k, not from 250k.
        s = self.simulate([reply("1", 250_000), reply("2", 40_000), reply("3", 260_000)])
        self.assertEqual(s["cut"][200_000], 220_000)

    def test_sidechain_lines_are_not_the_main_sessions(self):
        s = self.simulate([reply("1", 50_000), reply("2", 900_000, sidechain=True), reply("3", 60_000)])
        self.assertEqual((s["read"], s["replies"]), (110_000, 2))

    def test_a_fresh_session_saves_the_reads_and_pays_one_write_of_its_start(self):
        # Opus 5.5, as above: past 100k, 300k fewer tokens read at $0.20/M saves $0.06, and the two
        # restarts each write their 50k at $8/M instead of reading it: 2 x 50k x $7.80/M = $0.78.
        opus = "claude-opus-5-5"
        s = self.simulate([reply("1", 50_000, model=opus, when="2026-10-12T10:00:00Z"),
                           reply("2", 150_000, model=opus, when="2026-10-13T10:00:00Z"),
                           reply("3", 250_000, model=opus, when="2026-10-14T10:00:00Z")])
        # 449,970 read at $0.20, 3 input at $4, 27 written at $8, 15 output at $20, per million.
        self.assertAlmostEqual(s["cost"], 0.090522, places=9)
        self.assertAlmostEqual(s["cut_cost"][100_000], 0.06 - 0.78, places=9)
        self.assertAlmostEqual(s["cut_cost"][200_000], 0.04 - 0.39, places=9)
        self.assertAlmostEqual(s["cut_cost"][300_000], 0.0, places=9)
        self.assertEqual((s["first"][:10], s["last"][:10]), ("2026-10-12", "2026-10-14"))

    def test_the_report_prints_each_cuts_dollar_saving_and_share(self):
        s = dict(read=1_000, replies=2, sessions=1, first="2026-10-12T10:00:00Z", last="2026-10-14T10:00:00Z",
                 cost=200.0, cut={100_000: 205}, cut_cost={100_000: 30.0}, cap={2_000: 0})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tu.report(s)
        self.assertIn("2026-10-12 to 2026-10-14", out.getvalue())
        self.assertIn("passes 100k: -20.5% tokens, -$30.00 (-15.0% of $200.00)", out.getvalue())


def call(tool_use_id, name, args=None, when="2026-11-05T10:00:00Z", branch="day-43/track-a2-x"):
    # A reply's line, as count() reads it: its usage and model, on a branch of a day.
    block = {"type": "tool_use", "id": tool_use_id, "name": name, "input": args or {}}
    return json.dumps({"type": "assistant", "timestamp": when, "gitBranch": branch, "requestId": "req_" + tool_use_id,
                       "message": {"id": "msg_" + tool_use_id, "model": "claude-haiku-5-5", "usage": {"output_tokens": 1},
                                   "content": [block]}})


def result(tool_use_id, text):
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
    return json.dumps({"type": "user", "timestamp": "2026-11-05T10:00:01Z", "message": {"content": [block]}})


class ToolsTest(unittest.TestCase):
    """Bash results cut by cap-output, and Reads of a file over 8,000 bytes, whole or ranged."""

    def tools(self, lines, since=None):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return tu.tools([path], since)

    def test_a_bash_result_with_the_cut_marker_at_3000_is_counted(self):
        cut = "a" * 3000 + "\n[cut 2000 chars; full output: x.txt; use Read with offset/limit or grep]\n" + "b" * 3000
        s = self.tools([call("t1", "Bash"), result("t1", cut), call("t2", "Bash"), result("t2", "short")])
        self.assertEqual((s["bash"], s["cut"]), (2, 1))

    def test_a_marker_elsewhere_in_a_result_is_not_a_cut(self):
        s = self.tools([call("t1", "Bash"), result("t1", "x" * 10 + "\n[cut 1 chars; full output: y]\n" + "z" * 3000)])
        self.assertEqual((s["bash"], s["cut"]), (1, 0))

    def test_a_read_of_a_file_over_8000_bytes_is_whole_or_ranged(self):
        with tempfile.TemporaryDirectory() as folder:
            big = os.path.join(folder, "big.md")
            small = os.path.join(folder, "small.md")
            with open(big, "w", encoding="utf-8") as f:
                f.write("x" * 9_000)
            with open(small, "w", encoding="utf-8") as f:
                f.write("x" * 100)
            path = os.path.join(folder, "session.jsonl")
            lines = [call("r1", "Read", {"file_path": big}), call("r2", "Read", {"file_path": big, "offset": 1, "limit": 5}),
                     call("r3", "Read", {"file_path": small})]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            s = tu.tools([path])
        self.assertEqual((s["reads"], s["whole"]), (2, 1))

    def test_since_counts_only_the_sessions_started_after_it(self):
        with tempfile.TemporaryDirectory() as folder:
            before = os.path.join(folder, "before.jsonl")
            after = os.path.join(folder, "after.jsonl")
            with open(before, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "timestamp": "2026-10-20T10:00:00Z"}) + "\n" + call("b", "Bash") + "\n" + result("b", "short") + "\n")
            with open(after, "w", encoding="utf-8") as f:
                f.write(call("a", "Bash") + "\n" + result("a", "short") + "\n")
            s = tu.tools([before, after], since=tu.moment("2026-11-01"))
        self.assertEqual(s["bash"], 1)


class CostTest(unittest.TestCase):
    """Each kind of token at its model's list price."""

    def test_each_token_kind_is_priced_at_its_models_rate(self):
        # Opus 5.5: 1,000 input at $4, 1,000 5-minute writes at $5, 2,000 1-hour writes at $8,
        # 10,000 reads at $0.20 and 500 output at $20 per million: $0.037.
        entry = json.loads(line())
        entry["message"]["model"] = "claude-opus-5-5"
        entry["message"]["usage"] = {"input_tokens": 1_000, "cache_creation_input_tokens": 3_000,
                                     "cache_creation": {"ephemeral_5m_input_tokens": 1_000, "ephemeral_1h_input_tokens": 2_000},
                                     "cache_read_input_tokens": 10_000, "output_tokens": 500}
        totals = CountTest.count(self, [json.dumps(entry)])
        self.assertAlmostEqual(list(totals.values())[0]["cost"], 0.037, places=9)

    def test_a_write_with_no_split_is_a_one_hour_write(self):
        self.assertAlmostEqual(tu.cost("claude-opus-5-5", {"cache_creation_input_tokens": 1_000}), 0.008, places=9)

    def test_a_haiku_prompt_past_100k_costs_five_times(self):
        self.assertAlmostEqual(tu.cost("claude-haiku-5-5", {"cache_read_input_tokens": 100_000}), 0.001, places=9)
        self.assertAlmostEqual(tu.cost("claude-haiku-5-5", {"cache_read_input_tokens": 100_001}), 0.00500005, places=9)


def turn(message_id, context, calls=()):
    # A main-session Opus reply that read `context` from the cache, wrote 100 tokens and made these tool calls.
    blocks = [{"type": "tool_use", "id": message_id + str(i), "name": name, "input": args} for i, (name, args) in enumerate(calls)]
    return json.dumps({"type": "assistant", "requestId": "req_" + message_id, "timestamp": "2026-11-02T10:00:00Z",
                       "message": {"id": message_id, "model": "claude-opus-5-5", "content": blocks,
                                   "usage": {"cache_read_input_tokens": context, "output_tokens": 100}}})


class RouteTest(unittest.TestCase):
    """Which replies are mechanical, and what a Haiku operator subagent would take off the main sessions."""

    def test_only_our_scripts_and_gh_or_git_reads_are_mechanical(self):
        for command in ("python scripts/spec-drift.py --out spec-drift.json | tail -3",
                        "cd /c/repo && git checkout -q main && git pull -q && git log --oneline -1",
                        'gh pr checks 12 --watch > c.txt; gh pr checks 12 | grep -E "fail|pending"',
                        "PYTHONDONTWRITEBYTECODE=1 python scripts/break.py --file a.py --find x --replace y -- python t.py",
                        "git diff --stat main...HEAD", "gh api repos/o/r/pulls/3"):
            self.assertTrue(tu.mechanical([("Bash", {"command": command})]), command)
        for command in ("sed -n 1,40p specs/day-01.md", "grep -n def scripts/break.py", "git diff main -- a.py",
                        "git log -p -3", "gh pr diff 3", "gh pr create --body-file b.md", "git commit -F m.txt",
                        "gh api -X PATCH repos/o/r", "python scripts/test_break.py", "python -c 'print(1)'"):
            self.assertFalse(tu.mechanical([("Bash", {"command": command})]), command)
        self.assertFalse(tu.mechanical([]))
        self.assertFalse(tu.mechanical([("Bash", {"command": "git status"}), ("Edit", {})]))

    def test_mechanical_replies_move_to_haiku_with_the_context_they_added(self):
        # Edit at 50k ($0.012), spec-drift at 51k ($0.0122), a typed answer at 55k ($0.013). On Haiku the
        # mechanical reply writes the 20k brief and the 1k it took in ($0.0042) and its output ($0.00005);
        # the 4k it added is not read by the answer ($0.0008 at Opus's read price).
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "session.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join([turn("1", 50_000, [("Edit", {})]),
                                   turn("2", 51_000, [("Bash", {"command": "python scripts/spec-drift.py"})]),
                                   turn("3", 55_000)]) + "\n")
            s = tu.route([path])
        self.assertEqual(s["replies"], {"mechanical": 1, "judgment": 2})
        self.assertAlmostEqual(s["cost"]["mechanical"], 0.0122, places=9)
        self.assertAlmostEqual(s["cost"]["judgment"], 0.025, places=9)
        self.assertAlmostEqual(s["haiku"], 0.00425, places=9)
        self.assertAlmostEqual(s["unread"], 0.0008, places=9)


if __name__ == "__main__":
    unittest.main()
