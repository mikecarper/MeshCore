"""Verify the CI summary cannot pass while a split test job fails or skips."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/run-unit-tests.yml"


class UnitTestJobSplit(unittest.TestCase):
    def test_all_split_groups_are_required_and_summary_checks_actual_results(self):
        source = WORKFLOW.read_text()
        jobs = re.findall(r"^  (test-[a-z0-9-]+):\s*$", source, re.MULTILINE)
        self.assertGreaterEqual(len(jobs), 2)
        self.assertEqual(len(set(jobs)), len(jobs))
        summary = re.search(r"^  test:\s*$([\s\S]*?)(?=^  [a-zA-Z0-9_-]+:\s*$|\Z)",
                            source, re.MULTILINE)
        self.assertIsNotNone(summary)
        body = summary.group(1)
        self.assertIn("if: ${{ always() }}", body)
        needs = re.search(r"^    needs:\s*\[([^\]]+)\]", body, re.MULTILINE)
        self.assertIsNotNone(needs)
        self.assertEqual(set(jobs), {name.strip() for name in needs.group(1).split(",")})
        self.assertIn("TEST_GROUP_RESULTS: ${{ toJSON(needs) }}", body)
        match = re.search(r"^          import json, os, sys\n([\s\S]*?)^          PYCODE\s*$",
                          body, re.MULTILINE)
        self.assertIsNotNone(match)
        script = "import json, os, sys\n" + "\n".join(
            line[10:] for line in match.group(1).splitlines()) + "\n"
        for name in jobs:
            group = re.search(r"^  " + re.escape(name) + r":\s*$([\s\S]*?)(?=^  [a-zA-Z0-9_-]+:\s*$|\Z)",
                              source, re.MULTILINE)
            self.assertIsNotNone(group)
            self.assertIn("uses: actions/checkout@v6", group.group(1))
            self.assertIn("uses: ./.github/actions/setup-build-environment", group.group(1))
            self.assertIn("pkg install -e native", group.group(1))
        for result in ("success", "failure", "skipped", "cancelled"):
            statuses = {name: {"result": "success"} for name in jobs}
            statuses[jobs[0]]["result"] = result
            with self.subTest(result=result):
                completed = subprocess.run(
                    [sys.executable, "-c", script],
                    env={**os.environ, "TEST_GROUP_RESULTS": json.dumps(statuses)},
                    capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0 if result == "success" else 1,
                                 completed.stdout + completed.stderr)
                if result != "success":
                    self.assertIn(jobs[0] + ": " + result, completed.stdout)


if __name__ == "__main__":
    unittest.main()
