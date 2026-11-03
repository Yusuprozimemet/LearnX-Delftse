"""A second, blind reading of a day's Defect lines, and how far it agrees with the tags they carry.

    python scripts/relabel.py blind specs/day-44-*.md --out <dir>   # writes <dir>/blind.md, key.json
    python scripts/relabel.py compare <dir>                          # reads <dir>/labels.json too

The session that tags a defect also wrote the code, the spec or the review it is about, and the
paper found that tags then follow the session's own reading (docs/paper, the tagger-bias lesson):
on Days 21-28 a fresh rater, given the text alone, disagreed on 11 of 82 causes and 16 stages,
mostly defects made on one day and found on a later one. So at the close, `blind` strips the
`found:`, `cause:` and `made:` fields from the day's entries, the defect-rater agent labels the
text alone, and `compare` prints each disagreement for the closing PR to decide.

Stages come from the found: values in specs/README.md; a defect made on an earlier day is an
escape whatever found: says. A value the README does not list is reported, not guessed.
"""
import glob
import json
import os
import re
import sys
from collections import Counter

ENTRY = re.compile(r"^- \*\*Defect\*\*\s*[—-]\s*(.*)$")
FIELD = re.compile(r"^(found|cause|made):\s*(.*)$")
BEFORE_CODE = ("auditor", "spec-change pr")
BEFORE_MERGE = ("review", "break on purpose", "self")
ESCAPE = ("close", "dashboard")
CAUSES = ("spec", "implementation", "process", "environment")


def entries(path):
    """Each Defect line in a spec's Notes, with its continuation lines joined."""
    day = int(re.search(r"day-(\d+)", os.path.basename(path)).group(1))
    lines = open(path, encoding="utf-8").read().splitlines()
    rows = []
    for i, line in enumerate(lines):
        m = ENTRY.match(line)
        if not m:
            continue
        buf = [m.group(1)]
        for more in lines[i + 1:]:
            if not more.startswith("  ") or not more.strip():
                break
            buf.append(more.strip())
        parts = [p.strip() for p in " ".join(buf).split(" · ")]
        tags, text = {}, []
        for p in parts:
            f = FIELD.match(p)
            if f and not text and f.group(1) not in tags:
                tags[f.group(1)] = f.group(2)
            else:
                text.append(p)
        rows.append(dict(id=f"D{day}-{len(rows) + 1}", day=day, line=i + 1, text=" · ".join(text),
                         **{k: tags.get(k, "") for k in ("found", "cause", "made")}))
    return rows


def made(row):
    """`this`, `earlier`, `none`, or '' when the entry records none."""
    m = re.fullmatch(r"Day (\d+)", row["made"])
    if m:
        return "this" if int(m.group(1)) == row["day"] else "earlier"
    return "none" if row["made"] == "none" else ""


def stage(row):
    """`before_code`, `before_merge` or `escape`, or '' for a found: value the README does not list."""
    if made(row) == "earlier":
        return "escape"
    f = re.sub(r"^the ", "", row["found"].lower())
    # Older Notes qualify the value: "review (Track A)", "auditor (spec-auditor)", "review (Day 22)".
    f = re.sub(r"^review \((day \d+)\)$", r"\1 review", f)
    f = re.sub(r" \(.*\)$", "", f)
    if f in BEFORE_CODE:
        return "before_code"
    if f in BEFORE_MERGE or re.fullmatch(r"tracks? [0-9a-z, ]+", f):
        return "before_merge"
    if f in ESCAPE or re.fullmatch(r"day \d+ review", f):
        return "escape"
    return ""


def blind(paths, out):
    rows = [r for p in paths for r in entries(p)]
    os.makedirs(out, exist_ok=True)
    json.dump(rows, open(os.path.join(out, "key.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    with open(os.path.join(out, "blind.md"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"### {r['id']} (Day {r['day']})\n{r['text']}\n\n")
    return rows


def compare(key, labels):
    """The closing PR's lines: one count per axis, then one line per disagreement."""
    got = {r["id"]: r for r in labels}
    missing = [r["id"] for r in key if r["id"] not in got]
    if missing:
        raise SystemExit(f"no label for {', '.join(missing)}")
    out, diffs = [], []
    for axis, tag in (("stage", stage), ("cause", lambda r: r["cause"].lower()), ("made", made)):
        pairs = [(r, tag(r), got[r["id"]].get(axis, "")) for r in key]
        tagged = [(r, a, b) for r, a, b in pairs if a and (axis != "cause" or a in CAUSES)]
        agree = sum(a == b for _, a, b in tagged)
        line = f"{axis}: {agree} of {len(tagged)} agree"
        if len(tagged) < len(pairs):
            line += f" ({len(pairs) - len(tagged)} untagged or not a README value, not compared)"
        out.append(line)
        diffs += [f"- {r['id']} {axis}: tagged {a}, rater {b} ({got[r['id']].get('confidence', '?')}):"
                  f" {got[r['id']].get('note', '')}" for r, a, b in tagged if a != b]
    unclear = Counter(axis for r in labels for axis in ("stage", "cause", "made") if r.get(axis) == "unclear")
    if unclear:
        out.append("rater unclear: " + ", ".join(f"{k} {v}" for k, v in sorted(unclear.items())))
    return out + diffs


def main(argv):
    if len(argv) >= 4 and argv[0] == "blind" and argv[-2] == "--out":
        paths = [p for a in argv[1:-2] for p in sorted(glob.glob(a))]
        if not paths:
            raise SystemExit("no spec matches")
        rows = blind(paths, argv[-1])
        print(f"{len(rows)} entries -> {argv[-1]}/blind.md")
    elif len(argv) == 2 and argv[0] == "compare":
        key = json.load(open(os.path.join(argv[1], "key.json"), encoding="utf-8"))
        labels = json.load(open(os.path.join(argv[1], "labels.json"), encoding="utf-8"))
        print("\n".join(compare(key, labels)))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
