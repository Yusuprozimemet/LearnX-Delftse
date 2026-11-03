"""The demo data draws every panel of the dashboard and has every key the real data has.

The page test boots docs/dashboard/dashboard.js on demo() with test_spec_drift.py's BOOT harness; the
key test reads the keys spec-drift.py's main() writes. Needs node for the page test.

    python scripts/test_demo_data.py
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_spec_drift  # noqa: E402  (its BOOT boots the page on the data it reads from stdin)

spec = importlib.util.spec_from_file_location("demo_data", os.path.join(HERE, "demo-data.py"))
demo_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_data)
DASHBOARD = os.path.join(HERE, "..", "docs", "dashboard", "dashboard.js")
REAL = dict(now=dict(current_day=None), rows=[], days=[])


def boot(data):
    """The page's boot on this data, as test_spec_drift.py's BOOT reports it."""
    return json.loads(subprocess.run(["node", "-e", test_spec_drift.BOOT, DASHBOARD], input=json.dumps(data),
                                     capture_output=True, encoding="utf-8", check=True).stdout)


def keys_at_top(text):
    """The names given as name= in an argument list, up to its closing parenthesis, not inside a nested call."""
    keys, depth = [], 0
    for m in re.finditer(r"[()]|\b(\w+)=(?!=)", text):
        if m.group(0) == "(":
            depth += 1
        elif m.group(0) == ")":
            depth -= 1
            if depth < 0:
                break
        elif depth == 0:
            keys.append(m.group(1))
    return keys


def written_keys():
    """The keys spec-drift.py's main() puts in the real dict it writes, and those of its now and meta."""
    with open(os.path.join(HERE, "spec-drift.py"), encoding="utf-8") as f:
        source = f.read()
    block = source[source.index("real = dict(") + len("real = dict("):]
    # now is built as its own dict, "now = dict(", and meta inside the main call
    nested = {name: keys_at_top(source[m.end():]) for name, m in
              ((n, re.search(rf"\b{n} ?= ?dict\(", source)) for n in ("now", "meta"))}
    return keys_at_top(block), nested


class DemoData(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_the_page_draws_every_panel_on_the_demo_data(self):
        out = json.loads(subprocess.run(["node", "-e", test_spec_drift.BOOT, DASHBOARD],
                                        input=json.dumps(demo_data.demo()), capture_output=True,
                                        encoding="utf-8", check=True).stdout)
        self.assertIsNone(out["error"], "a panel threw on the demo data")
        self.assertIsNotNone(out["eyebrow"])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_no_panel_on_the_demo_data_shows_a_failure_or_a_bad_number(self):
        # The same test kit/starter-check.py applies to the page it builds (its step 4).
        error, panels, logged = test_spec_drift.drawn(**demo_data.demo())
        self.assertIsNone(error, "a panel threw on the demo data")
        self.assertEqual(logged, [], "the demo data logged an error while drawing")
        failed = [box for box, html in panels.items() if re.search(r"This panel failed|NaN|Infinity|undefined", html)]
        self.assertEqual(failed, [], "boxes that drew a failed panel, NaN, Infinity or undefined")

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_every_chart_box_draws_a_data_line_on_the_demo_data(self):
        # lineChart draws each series as a polyline; a box with none shows only its axes, unless its
        # empty: message is what it shows (load-error), which is the box saying there is no data yet.
        with open(DASHBOARD, encoding="utf-8") as f:
            boxes = re.findall(r'lineChart\(document\.getElementById\("([^"]+)"\)', f.read())
        error, panels, logged = test_spec_drift.drawn(**demo_data.demo())
        self.assertIsNone(error, "a panel threw on the demo data")
        bare = [box for box in boxes if "<polyline" not in panels.get(box, "") and "load-error" not in panels.get(box, "")]
        self.assertEqual(bare, [], "chart boxes that drew no data line on the demo data")

    def test_the_banner_shows_on_the_demo_data(self):
        out = boot(dict(demo_data.demo(), demo=True))
        self.assertIsNone(out["error"], "a panel threw on the demo data")
        self.assertFalse(out["banner"]["hidden"])
        self.assertIn("Example data", out["banner"]["text"])

    def test_no_banner_without_the_demo_key(self):
        out = boot(demo_data.demo())
        self.assertIsNone(out["error"], "a panel threw on the data without demo")
        self.assertTrue(out["banner"]["hidden"])

    def test_no_day_specs_puts_the_demo_in_the_page(self):
        page = test_spec_drift.sd.page_data(REAL, [])
        self.assertIs(page.get("demo"), True)
        self.assertEqual({k: v for k, v in page.items() if k != "demo"}, demo_data.demo())

    def test_day_specs_put_the_real_data_in_unchanged(self):
        page = test_spec_drift.sd.page_data(REAL, ["01"])
        self.assertEqual(page, REAL)
        self.assertNotIn("demo", page)

    def test_every_token_row_has_the_hook_counts_with_reads_over_whole_and_bash_over_cut(self):
        for row in demo_data.demo()["tokens"]:
            for k in ("bash", "cut", "reads", "whole"):
                self.assertIn(k, row)
            self.assertGreaterEqual(row["reads"], row["whole"])
            self.assertGreaterEqual(row["bash"], row["cut"])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_the_hooks_table_on_the_demo_data_shows_cut_from_day_four_and_whole_until_it(self):
        error, panels, _ = test_spec_drift.drawn(**demo_data.demo())
        self.assertIsNone(error, "a panel threw on the demo data")
        cells = [re.findall(r'<td class="num">([^<]*)</td>', r) for r in re.findall(r"<tr><td>(.*?)</tr>", panels["token-hooks"])]
        self.assertEqual(len(cells), 6)
        self.assertEqual([c[1].endswith("(0.0%)") for c in cells[:3]], [True] * 3)
        self.assertEqual([c[3] for c in cells[3:]], ["0 (0.0%)"] * 3)

    def test_the_same_dict_comes_out_on_every_call(self):
        self.assertEqual(demo_data.demo(), demo_data.demo())

    def test_it_has_every_key_the_real_data_has(self):
        top, nested = written_keys()
        self.assertGreaterEqual(len(top), 20, "the keys were not found in spec-drift.py")
        data = demo_data.demo()
        self.assertEqual([k for k in top if k not in data], [])
        for name, keys in nested.items():
            self.assertEqual([k for k in keys if k not in data[name]], [], name)

    def test_the_script_prints_the_dict_it_returns(self):
        out = subprocess.run([sys.executable, os.path.join(HERE, "demo-data.py")], capture_output=True,
                             encoding="utf-8", check=True).stdout
        self.assertEqual(json.loads(out), demo_data.demo())


if __name__ == "__main__":
    unittest.main()
