"""Count the Claude Code tokens spent on this repository, per date, migration day, model and agent.

Reads the session transcripts Claude Code keeps on this machine (~/.claude/projects/<this
repository's path>, subagents included) and writes totals only, no prompt or reply text, to
docs/dashboard/token-usage.json (tokens, their cost at list prices and the --tools counts per row), which scripts/spec-drift.py puts on the dashboard. The
transcripts live on the maintainer's machine, not in CI, so this runs locally and its output is
committed; the closing PR of each day refreshes it.

    python scripts/token-usage.py
    python scripts/token-usage.py --simulate   # what shorter sessions, capped tool output or a Haiku operator would save
    python scripts/token-usage.py --tools      # Bash results cap-output cut; Reads of files over 8,000 bytes
    python scripts/token-usage.py --tools --since 2026-11-01   # only the sessions that started since then

A reply is counted once, from its last line: a transcript repeats a reply's usage on every content
block it streamed, and the usage is complete only on the last of those lines, so replies are keyed
by message and request id and the last line per key wins. The day is the branch the session was on
(day-17/track-d-... is Day 17); anything else (main, tooling/, docs/) has no day. Claude Code
deletes transcripts after cleanupPeriodDays (30 by default), so an entry already in the file is
never lowered: each count keeps the larger of the file's and the transcripts'.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, os.pardir, "docs", "dashboard", "token-usage.json"))
FIELDS = ("input", "cache_write", "cache_read", "output", "replies")
TOOL_FIELDS = ("bash", "cut", "reads", "whole")
DAY_BRANCH = re.compile(r"^day-(\d+)/")
DATED = re.compile(r"-\d{8}$")
CUTS = (100_000, 200_000, 300_000)
CAPS = (2_000, 5_000)
TOOL_LIMIT = 8_000  # read-guard.py's LIMIT: a whole Read of a file over it is refused
CUT_AT = 3_000  # cap-output.py's KEEP: its cut marker follows the head, this many characters in
# List prices in dollars per million tokens: input, cache write for 5 minutes (1.25x input) and for
# 1 hour (2x), cache read, output. From the claude-api skill's model table (cached 2026-10-06) and its
# prompt-caching notes; a cache read the skill gives no figure for is 0.1x input. A model not here costs 0.
PRICES_CHECKED = "2026-11-02"
PRICES = {
    "claude-fable-5-1": (10, 12.5, 20, 0.25, 50),
    "claude-opus-5-5": (4, 5, 8, 0.20, 20),
    "claude-opus-5": (5, 6.25, 10, 0.50, 25),
    "claude-sonnet-5-5": (2, 2.5, 4, 0.20, 10),
    "claude-sonnet-5": (2, 2.5, 4, 0.20, 10),
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),
    "claude-haiku-4-5": (1, 1.25, 2, 0.10, 5),
}
HAIKU_LONG = 100_000  # a Haiku 5.5 prompt past this many tokens costs five times as much ($0.50 / $2.50)
HAIKU_BRIEF = 20_000  # assumed: an operator subagent's system prompt, tools and brief
# A mechanical reply's Bash calls only run these scripts, read with gh or git, or filter what they print.
SCRIPTS = ("break.py", "seen-red.py", "pr-names.py", "check-links.py", "evidence.py", "spec-drift.py", "relabel.py", "token-usage.py")
GIT_READS = {"status", "log", "diff", "show", "fetch", "pull", "checkout", "switch", "branch", "rev-parse", "ls-files", "remote"}
GIT_DIFFS = {"diff", "show"}  # reviewing a diff is judgment: these, and log -p, are mechanical only with a stat flag
STATS = {"--stat", "--numstat", "--shortstat", "--name-only", "--name-status"}
GH_READS = {"pr view", "pr list", "pr checks", "run list", "run view", "run watch", "issue view", "issue list", "api"}
GH_WRITES = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
FILTERS = {"grep", "tail", "head", "wc", "sort", "uniq", "select-object", "select-string"}  # only after a |


def long_path(path):
    # Windows refuses a path past MAX_PATH (260 characters) unless it is absolute and starts with
    # \\?\: open() raises, and glob skips a folder past the limit without a word. glob keeps the
    # prefix on every path it yields, so prefixing the root reaches every open. UNC paths are left as they are.
    if os.name != "nt" or path.startswith("\\\\"):
        return path
    return "\\\\?\\" + os.path.abspath(path)


def transcripts(root):
    # Claude Code names a project's folder after its path, every character but letters and digits
    # turned into "-"; a worktree of this repository, under its .claude/worktrees/, gets the same name
    # and "--". A sibling folder whose name only starts with this one's is another project.
    name = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.join(HERE, os.pardir))).lower()
    for folder in glob.glob(os.path.join(long_path(root), "*")):
        base = os.path.basename(folder).lower()
        if base == name or base.startswith(name + "--"):
            yield from glob.glob(os.path.join(folder, "**", "*.jsonl"), recursive=True)


def agent(path):
    # A subagent's transcript, subagents/agent-<id>.jsonl, has its type in agent-<id>.meta.json beside it.
    meta = path[:-len(".jsonl")] + ".meta.json"
    if os.path.basename(os.path.dirname(path)) != "subagents":
        return "main"
    try:
        with open(meta, encoding="utf-8") as f:
            return json.load(f).get("agentType") or "unknown"
    except (OSError, ValueError):
        return "unknown"


def reply(entry):
    # The assistant message a line counts as a reply: it has usage and comes from a Claude model.
    msg = entry.get("message") or {}
    if entry.get("type") != "assistant" or not msg.get("usage") or not DATED.sub("", msg.get("model") or "").startswith("claude"):
        return None
    return msg


def row_key(entry, who):
    # The row a reply or a tool call counts toward: its local date, the day of its branch, its model and its agent.
    when = datetime.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    day = DAY_BRANCH.match(entry.get("gitBranch") or "")
    return (when.astimezone().date().isoformat(), int(day.group(1)) if day else None, DATED.sub("", entry["message"].get("model") or ""), who)


def tool_blocks(entry, calls, cut):
    # A line's tool calls by tool_use id, and each Bash result's cut test. A message spread over several
    # lines repeats its blocks: the last line wins, so a call counts once. A Bash result is cut by
    # cap-output.py when its text has the marker at CUT_AT. A request blob repeats earlier calls, so only a reply records one.
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use" and entry.get("type") == "assistant":
            calls[b.get("id")] = (b.get("name"), b.get("input") or {}, entry)
        elif b.get("type") == "tool_result" and calls.get(b.get("tool_use_id"), (None,))[0] == "Bash":
            cut[b.get("tool_use_id")] = tool_text(b.get("content")).find("\n[cut ") == CUT_AT


def read_size(name, args):
    # What a tool call adds to reads and whole: (1, whole) for a Read of a file over TOOL_LIMIT, else (0, 0).
    # A transcript keeps no size, so the file's size on disk now is read.
    if name != "Read" or size(args.get("file_path") or "") <= TOOL_LIMIT:
        return 0, 0
    return 1, int(not any(args.get(k) is not None for k in ("offset", "limit", "pages")))


def cost(model, usage):
    # A reply's list price in dollars. The usage splits its cache writes by lifetime; one with no split
    # is Claude Code's 1-hour write. Haiku 5.5's long-prompt price is per reply, so it is applied here.
    p = PRICES.get(DATED.sub("", model or ""))
    if p is None:
        return 0.0
    write, read = usage.get("cache_creation_input_tokens", 0), usage.get("cache_read_input_tokens", 0)
    hour = (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", write)
    prompt = usage.get("input_tokens", 0) + write + read
    scale = 5 if p is PRICES["claude-haiku-5-5"] and prompt > HAIKU_LONG else 1
    return scale * (usage.get("input_tokens", 0) * p[0] + (write - hour) * p[1] + hour * p[2]
                    + read * p[3] + usage.get("output_tokens", 0) * p[4]) / 1e6


def count(files):
    # The usage is complete only on a reply's last line, so the last line per key overwrites the others.
    replies, totals = {}, collections.defaultdict(lambda: dict.fromkeys(FIELDS + TOOL_FIELDS + ("cost",), 0))
    for path in files:
        who = agent(path)
        calls, cut = {}, {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                tool_blocks(entry, calls, cut)
                msg = reply(entry)
                if msg is not None:
                    replies[(msg.get("id"), entry.get("requestId"))] = (who, entry)
        for tid, (name, args, entry) in calls.items():
            if reply(entry) is None:
                continue
            t = totals[row_key(entry, who)]
            if tid in cut:
                t["bash"] += 1
                t["cut"] += int(cut[tid])
            reads, whole = read_size(name, args)
            t["reads"] += reads
            t["whole"] += whole
    for who, entry in replies.values():
        usage = entry["message"]["usage"]
        t = totals[row_key(entry, who)]
        t["input"] += usage.get("input_tokens", 0)
        t["cache_write"] += usage.get("cache_creation_input_tokens", 0)
        t["cache_read"] += usage.get("cache_read_input_tokens", 0)
        t["output"] += usage.get("output_tokens", 0)
        t["replies"] += 1
        t["cost"] += cost(entry["message"].get("model"), usage)
    return totals


def contexts(path):
    # A session's replies in order: the context each read (every input token, cached or not),
    # whether a tool result came in since the reply before, its model, usage and timestamp. A sidechain line is a subagent's.
    replies, tool = {}, False
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get("message") or {}
            if entry.get("isSidechain"):
                continue
            if entry.get("type") == "user" and isinstance(msg.get("content"), list):
                tool = tool or any(isinstance(b, dict) and b.get("type") == "tool_result" for b in msg["content"])
            elif entry.get("type") == "assistant" and msg.get("usage"):
                u = msg["usage"]
                key = (msg.get("id"), entry.get("requestId"))
                context = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                tool = replies[key][1] if key in replies else tool
                replies[key] = (context, tool, msg.get("model"), u, entry.get("timestamp") or "")
                tool = False
    return list(replies.values())


def simulate(files):
    # Every reply re-reads the whole context, so what a block adds is read once by each later reply.
    # A context that falls by more than 30% was compacted: the session goes on as a new segment.
    # Cut: whenever the context passes the size, start again from the segment's first context.
    # Cap: a tool result that added more than the cap adds only the cap, to that reply and every later one.
    # Both are upper bounds: a fresh session re-reads what it needs, and a capped result may be read again.
    # Cut in dollars: each token no longer read saves the model's cache-read price, and each restart
    # costs one reply at the new session's starting context (the segment's first): written to the
    # 1-hour cache instead of read. No summary or hand-over is counted, and the Haiku long-prompt price is not applied.
    s = dict(read=0, replies=0, sessions=0, cost=0.0, first="", last="",
             cut=dict.fromkeys(CUTS, 0), cut_cost=dict.fromkeys(CUTS, 0.0), cap=dict.fromkeys(CAPS, 0))
    for path in files:
        segments = []
        for context, tool, model, usage, when in contexts(path):
            if not segments or context < 0.7 * segments[-1][-1][0]:
                segments.append([])
            segments[-1].append((context, tool, PRICES.get(DATED.sub("", model or ""), (0,) * 5)))
            s["cost"] += cost(model, usage)
            s["first"] = min(s["first"] or when, when)
            s["last"] = max(s["last"], when)
        s["sessions"] += bool(segments)
        for seg in segments:
            for i, (context, tool, _) in enumerate(seg):
                s["read"] += context
                s["replies"] += 1
                if tool and i:
                    for cap in CAPS:
                        s["cap"][cap] += max(0, context - seg[i - 1][0] - cap) * (len(seg) - i)
            for cut in CUTS:
                saved = 0
                for context, _, p in seg:
                    if context - saved > cut:
                        saved = context - seg[0][0]
                        s["cut_cost"][cut] -= seg[0][0] * (p[2] - p[3]) / 1e6
                    s["cut"][cut] += saved
                    s["cut_cost"][cut] += saved * p[3] / 1e6
    return s


def turns(path):
    # A session's replies in order, as (model, usage, tool calls): the usage from a reply's last line,
    # its tool calls from every line it streamed. A sidechain line is a subagent's.
    replies = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get("message") or {}
            if entry.get("isSidechain") or entry.get("type") != "assistant" or not msg.get("usage"):
                continue
            key = (msg.get("id"), entry.get("requestId"))
            calls = replies[key][2] if key in replies else []
            calls += [(b.get("name"), b.get("input") or {}) for b in msg.get("content") or []
                      if isinstance(b, dict) and b.get("type") == "tool_use"]
            replies[key] = (msg.get("model"), msg["usage"], calls)
    return list(replies.values())


def mechanical_command(command):
    # Every part of the command, split at && || ; | and newlines, with its redirection dropped, is cd or
    # echo, a script of ours, a gh or git read, or, after a |, a filter of what the part before printed.
    # A quoted string is one word, so a | in a grep pattern does not split it.
    masked = re.sub(r"'[^']*'|\"[^\"]*\"", "Q", command)
    for sep, part in zip([""] + re.findall(r"&&|\|\||[;|\n]", masked), re.split(r"&&|\|\||[;|\n]", masked)):
        words = [w for w in re.split(r"\d?>", part)[0].split() if not re.match(r"^\w+=", w)]
        if not words:
            continue
        head, flags = words[0].lower(), set(words)
        if head in ("cd", "echo") or head in FILTERS and sep == "|":
            continue
        if re.match(r"^py(thon3?)?(\.exe)?$", head):
            path = next((w for w in words[1:] if not w.startswith("-")), "")
            if os.path.basename(path.replace("\\", "/")) not in SCRIPTS:
                return False
        elif head == "git":
            rest = [w for i, w in enumerate(words[1:], 1) if not w.startswith("-") and words[i - 1] not in ("-C", "-c")]
            if not rest or rest[0] not in GIT_READS or (rest[0] == "branch" and {"-d", "-D", "-m", "-M"} & flags):
                return False
            if (rest[0] in GIT_DIFFS or rest[0] == "log" and "-p" in flags) and not STATS & flags:
                return False
        elif head == "gh":
            if " ".join(words[1:3]) not in GH_READS and words[1:2] != ["api"] or GH_WRITES & flags:
                return False
        else:
            return False
    return True


def mechanical(calls):
    # A reply is mechanical when it calls a tool and every call is a shell command mechanical_command passes.
    return bool(calls) and all(name in ("Bash", "PowerShell") and mechanical_command(args.get("command") or "")
                               for name, args in calls)


def route(files):
    # The main sessions' replies and their cost by class, and what moving the mechanical ones to a Haiku
    # operator subagent would change. Haiku: each run of them writes the brief once and reads it after,
    # writes what each reply took in since the reply before (its tool results), and the same output.
    # Opus: what a mechanical reply added to the context (its output and tool results, seen at the next
    # reply) is no longer read by the judgment replies after it, until a compaction (as in simulate).
    s = dict(replies=dict(mechanical=0, judgment=0), cost=dict(mechanical=0.0, judgment=0.0), haiku=0.0, unread=0.0)
    for path in files:
        before, run, seg = 0, False, []
        for model, usage, calls in turns(path):
            context = sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            kind = "mechanical" if mechanical(calls) else "judgment"
            s["replies"][kind] += 1
            s["cost"][kind] += cost(model, usage)
            if seg and context < 0.7 * seg[-1][1]:
                seg = []
            if seg and seg[-1][0] == "mechanical":
                seg[-1][2] = max(0, context - seg[-1][1])
            if kind == "judgment":
                s["unread"] += sum(added for k, _, added in seg if k == "mechanical") * PRICES.get(DATED.sub("", model or ""), (0,) * 5)[3] / 1e6
            else:
                s["haiku"] += cost("claude-haiku-5-5", {
                    "cache_read_input_tokens": HAIKU_BRIEF if run else 0,
                    "cache_creation_input_tokens": (0 if run else HAIKU_BRIEF) + max(0, context - before),
                    "output_tokens": usage.get("output_tokens", 0)})
            seg.append([kind, context, 0])
            run, before = kind == "mechanical", context
    return s


def moment(text):
    # A date or a time for --since, read as local time when it has no zone.
    return datetime.datetime.fromisoformat(text).astimezone()


def started(path):
    # A transcript's first timestamp: when its session started, or a subagent's run began.
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                stamp = json.loads(line).get("timestamp")
            except (ValueError, AttributeError):
                continue
            if stamp:
                return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return None


def after(path, since):
    # Whether the transcript started at or after --since; without --since, every transcript counts.
    if since is None:
        return True
    start = started(path)
    return start is not None and start >= since


def size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def tool_text(content):
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content if isinstance(content, str) else ""


def tools(files, since=None):
    s = dict(bash=0, cut=0, reads=0, whole=0)
    for path in files:
        if not after(path, since):
            continue
        calls, cut = {}, {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                tool_blocks(entry, calls, cut)
        s["bash"] += len(cut)
        s["cut"] += sum(cut.values())
        for name, args, _ in calls.values():
            reads, whole = read_size(name, args)
            s["reads"] += reads
            s["whole"] += whole
    return s


def report_tools(s):
    print(f"Bash results: {s['bash']:,}, cut by cap-output: {s['cut']:,} ({s['cut'] / max(s['bash'], 1):.1%})")
    print(f"Reads of files over {TOOL_LIMIT:,} bytes: {s['reads']:,}, whole {s['whole']:,}, ranged {s['reads'] - s['whole']:,}")


def report(s):
    print(f"main sessions:{s['sessions']}, {s['first'][:10]} to {s['last'][:10]}, {s['replies']:,} replies,"
          f" {s['read'] / 1e6:,.1f}M context tokens read, ${s['cost']:,.2f}")
    for cut, saved in s["cut"].items():
        d = s["cut_cost"][cut]
        sign = "-" if d >= 0 else "+"
        print(f"  fresh session whenever the context passes {cut // 1000}k: -{saved / max(s['read'], 1):.1%} tokens,"
              f" {sign}${abs(d):,.2f} ({sign}{abs(d) / max(s['cost'], 1e-9):.1%} of ${s['cost']:,.2f})")
    for cap, saved in s["cap"].items():
        print(f"  each tool result capped at {cap // 1000}k tokens: -{saved / max(s['read'], 1):.1%}")
    print(f"  upper bounds, at the list prices checked {PRICES_CHECKED}: a fresh session re-reads what it needs, and"
          " each restart costs one reply writing its starting context; a capped result may be read again")


def report_route(s):
    n, c = sum(s["replies"].values()), sum(s["cost"].values())
    for kind in ("mechanical", "judgment"):
        print(f"  {kind} replies: {s['replies'][kind]:,} ({s['replies'][kind] / max(n, 1):.1%}),"
              f" ${s['cost'][kind]:,.2f} ({s['cost'][kind] / max(c, 1e-9):.1%} of ${c:,.2f})")
    saved = s["cost"]["mechanical"] + s["unread"] - s["haiku"]
    print(f"  mechanical replies run in a Haiku subagent: -${saved:,.2f} (-{saved / max(c, 1e-9):.1%});"
          f" their own cost ${s['cost']['mechanical']:,.2f} and their output re-read later ${s['unread']:,.2f}, less Haiku ${s['haiku']:,.2f}")
    print(f"  upper bound, at the list prices checked {PRICES_CHECKED}: a {HAIKU_BRIEF // 1000}k Haiku brief per run of"
          " mechanical replies, each with the tool results it took in and the same output; no Opus reply hands the"
          " work over or reads a summary back, and the reply after a mechanical one keeps the same output")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--transcripts", default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--simulate", action="store_true",
                    help="print what fresh sessions or capped tool results would have saved; write nothing")
    ap.add_argument("--tools", action="store_true",
                    help="print the Bash results cap-output cut and the Reads of a file over 8,000 bytes; write nothing")
    ap.add_argument("--since", type=moment, metavar="DATE",
                    help="with --simulate or --tools: count only the sessions started at or after this date or time")
    args = ap.parse_args()
    if args.since and not (args.simulate or args.tools):
        ap.error("--since needs --simulate or --tools: token-usage.json is never written for part of the transcripts")
    if args.tools:
        report_tools(tools(transcripts(args.transcripts), args.since))
        return
    if args.simulate:
        paths = [p for p in transcripts(args.transcripts) if agent(p) == "main" and after(p, args.since)]
        report(simulate(paths))
        report_route(route(paths))
        return
    totals = count(transcripts(args.transcripts))
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            kept = json.load(f)
        for r in (r for r in kept if "agent" in r):
            t = totals[(r["date"], r["day"], r["model"], r["agent"])]
            for k in FIELDS:
                t[k] = max(t[k], r[k])
            for k in TOOL_FIELDS + ("cost",):
                t[k] = max(t[k], r.get(k, 0))
        # A row written before agents were counted holds every agent's tokens: what it has beyond
        # the agents counted for its date, day and model is kept as unknown, not given to one of them.
        for r in (r for r in kept if "agent" not in r):
            counted = [t for key, t in totals.items() if key[:3] == (r["date"], r["day"], r["model"])]
            t = totals[(r["date"], r["day"], r["model"], "unknown")]
            for k in FIELDS:
                t[k] += max(0, r[k] - sum(c[k] for c in counted))
    rows = [dict(date=d, day=n, model=m, agent=a, **dict(t, cost=round(t["cost"], 4))) for (d, n, m, a), t in totals.items()]
    rows.sort(key=lambda r: (r["date"], r["day"] or 0, r["model"], r["agent"]))
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("[\n" + ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n]\n")
    out = sum(r["output"] for r in rows)
    print(f"{args.out}: {len(rows)} rows, {rows[0]['date']} to {rows[-1]['date']}, {out:,} output tokens, "
          f"${sum(r['cost'] for r in rows):,.2f} at the list prices checked {PRICES_CHECKED}")


if __name__ == "__main__":
    main()
