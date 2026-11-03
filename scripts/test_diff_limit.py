"""The "Diff stays reviewable" check lets a PR over the limit through only with an `Oversized:` reason.

The tests run the workflow's own text: the `run:` block of the step "Enforce the limit, unless an
override is documented" in .github/workflows/pr-checks.yml, read from the file, in bash (Git's own
on Windows, not WSL's), with CHANGED, MAX_CHANGED_LINES (the workflow's) and PR_BODY set as the step
sets them. The step that counts the lines needs a pull request and is not run.

    python scripts/test_diff_limit.py    # needs PyYAML
"""
import importlib.util
import os
import subprocess
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(HERE, "..", ".github", "workflows", "pr-checks.yml")
STEP = "Enforce the limit, unless an override is documented"


def load_evidence():
    spec = importlib.util.spec_from_file_location("evidence", os.path.join(HERE, "evidence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASH = load_evidence().bash()


def workflow():
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


def step():
    """The step's run: text, from whichever job holds it."""
    for job in workflow()["jobs"].values():
        for s in job.get("steps", []):
            if s.get("name") == STEP:
                return s["run"]
    raise AssertionError(f"no step named {STEP!r} in pr-checks.yml")


def run(changed, body):
    """The step's exit code and output, for a PR of `changed` lines described by `body`."""
    env = dict(os.environ, CHANGED=str(changed), PR_BODY=body,
               MAX_CHANGED_LINES=str(workflow()["env"]["MAX_CHANGED_LINES"]))
    p = subprocess.run([BASH, "-c", step()], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class DiffLimitTest(unittest.TestCase):
    def test_the_limit_is_400(self):
        self.assertEqual(workflow()["env"]["MAX_CHANGED_LINES"], 400)

    def test_within_the_limit_passes(self):
        for changed in (12, 400):
            with self.subTest(changed=changed):
                code, out = run(changed, "")
                self.assertEqual(code, 0, out)
                self.assertIn(f"{changed} changed lines is within the limit of 400.", out)

    def test_over_the_limit_with_no_oversized_line_fails(self):
        code, out = run(401, "## What was built\n\nA large change, and no reason given.\n")
        self.assertEqual(code, 1, out)
        self.assertIn("::error::401 changed lines exceeds the limit of 400.", out)

    def test_over_the_limit_with_an_oversized_reason_passes_with_a_warning(self):
        body = "## What was built\r\n\r\nOversized: one generated file; splitting it breaks the build.\r\n"
        code, out = run(512, body)
        self.assertEqual(code, 0, out)
        self.assertIn("::warning::512 changed lines exceeds 400, but an 'Oversized:' reason", out)
        self.assertNotIn("::error::", out)

    def test_over_the_limit_with_an_empty_oversized_line_fails(self):
        # GitHub's editor sends CRLF: the carriage return is not a reason either.
        for body in ("Oversized:\n## Why\n", "Oversized:   \n", "Intro\r\nOversized:\r\nmore\r\n"):
            with self.subTest(body=body):
                code, out = run(512, body)
                self.assertEqual(code, 1, out)
                self.assertIn("::error::512 changed lines exceeds the limit of 400.", out)


if __name__ == "__main__":
    unittest.main()
