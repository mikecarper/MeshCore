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
    @staticmethod
    def job(source, name):
        match = re.search(r"^  " + re.escape(name) + r":\s*$([\s\S]*?)(?=^  [a-zA-Z0-9_-]+:\s*$|\Z)",
                          source, re.MULTILINE)
        if not match:
            raise AssertionError("Missing workflow job: " + name)
        return match.group(1)

    def assert_gate(self, source, name, dependencies, variable):
        body = self.job(source, name)
        self.assertIn("if: ${{ always() }}", body)
        needs = re.search(r"^    needs:\s*\[([^\]]+)\]", body, re.MULTILINE)
        self.assertIsNotNone(needs)
        self.assertEqual(set(dependencies), {item.strip() for item in needs.group(1).split(",")})
        self.assertIn(variable + ": ${{ toJSON(needs) }}", body)
        self.assertNotIn("continue-on-error", body)
        match = re.search(r"^          import json, os, sys\n([\s\S]*?)^          PYCODE\s*$",
                          body, re.MULTILINE)
        self.assertIsNotNone(match)
        script = "import json, os, sys\n" + "\n".join(
            line[10:] for line in match.group(1).splitlines()) + "\n"
        for dependency in dependencies:
            for result in ("success", "failure", "skipped", "cancelled"):
                statuses = {item: {"result": "success"} for item in dependencies}
                statuses[dependency]["result"] = result
                with self.subTest(gate=name, dependency=dependency, result=result):
                    completed = subprocess.run(
                        [sys.executable, "-c", script],
                        env={**os.environ, variable: json.dumps(statuses)},
                        capture_output=True, text=True)
                    self.assertEqual(completed.returncode, 0 if result == "success" else 1,
                                     completed.stdout + completed.stderr)
                    if result != "success":
                        self.assertIn(dependency + ": " + result, completed.stdout)

    def test_all_split_groups_are_required_and_summary_checks_actual_results(self):
        source = WORKFLOW.read_text()
        jobs = re.findall(r"^  (test-[a-z0-9-]+):\s*$", source, re.MULTILINE)
        self.assertGreaterEqual(len(jobs), 2)
        self.assertEqual(len(set(jobs)), len(jobs))
        self.assert_gate(source, "test", [*jobs, "universal-lora-smoke"], "TEST_GROUP_RESULTS")
        for name in jobs:
            body = self.job(source, name)
            self.assertIn("uses: actions/checkout@v6", body)
            self.assertIn("uses: ./.github/actions/setup-build-environment", body)
            # Only these metadata-only profile assertions need no native libraries.
            if name != "test-build-profiles":
                self.assertIn("pkg install -e native", body)

    def test_profile_assertions_are_split_and_cannot_run_the_full_serial_suite(self):
        source = WORKFLOW.read_text()
        body = self.job(source, "test-build-profiles")
        matrix = re.search(r"^        suite:\s*\[([^\]]+)\]", body, re.MULTILINE)
        self.assertIsNotNone(matrix)
        self.assertEqual([item.strip() for item in matrix.group(1).split(",")],
                         ["esp32-release", "companion-transport", "nrf52-storage"])
        self.assertIn("fail-fast: false", body)
        self.assertIn("max-parallel: 3", body)
        self.assertNotIn("continue-on-error", body)
        self.assertIn('run: bash test/test_build_profiles.sh --suite "${{ matrix.suite }}"', body)
        self.assertIn("python3 -B test/test_build_profile_suites.py -v", body)
        self.assertNotRegex(source, r"(?m)^\s+(?:run: )?bash test/test_build_profiles\.sh\s*$")

    def test_companion_contracts_run_in_three_required_independent_cells(self):
        source = WORKFLOW.read_text()
        body = self.job(source, "test-companion-routing")
        matrix = re.search(r"^        suite:\s*\[([^\]]+)\]", body, re.MULTILINE)
        self.assertIsNotNone(matrix)
        self.assertEqual([item.strip() for item in matrix.group(1).split(",")],
                         ["transport", "preferences", "storage"])
        self.assertIn("fail-fast: false", body)
        self.assertIn("max-parallel: 3", body)
        self.assertNotIn("continue-on-error", body)
        for dependency in ("pip install cryptography", "libssl-dev", "rweather/Crypto@0.4.0",
                           "pkg install -e native", "toolchain-gccarmnoneeabi@1.140201.0"):
            self.assertIn(dependency, body)
        self.assertIn("python3 -B test/test_companion_routing_ci_split.py -v", body)
        self.assertIn('run: bash tools/ci/run_companion_routing_tests.sh "${{ matrix.suite }}"', body)
        original = self.job(source, "test-companion")
        self.assertNotIn("Verify Companion contact and channel TX routing", original)
        self.assertNotIn("run_companion_routing_tests.sh", original)
        self.assertIn("Verify Companion Bluetooth reboot persistence", original)
        self.assertEqual(source.count('run: bash tools/ci/run_companion_routing_tests.sh'), 1)

    def test_universal_gate_requires_contracts_and_all_build_shards(self):
        source = WORKFLOW.read_text()
        self.assert_gate(source, "universal-lora-smoke",
                         ["universal-lora-contracts", "universal-lora-builds"], "UNIVERSAL_RESULTS")
        builds = self.job(source, "universal-lora-builds")
        matrix = re.search(r"^        shard:\s*\[([^\]]+)\]", builds, re.MULTILINE)
        self.assertIsNotNone(matrix)
        self.assertEqual([item.strip() for item in matrix.group(1).split(",")],
                         ["tbeam", "t3s3", "meshadventurer", "e22-tenstar", "muzi"])
        self.assertIn("fail-fast: false", builds)
        self.assertIn("max-parallel: 3", builds)
        self.assertNotIn("continue-on-error", builds)
        self.assertIn("uses: actions/checkout@v6", builds)
        self.assertIn("uses: ./.github/actions/setup-build-environment", builds)
        self.assertIn('run: bash tools/ci/run_universal_lora_smoke.sh "${{ matrix.shard }}"', builds)
        self.assertIn("name: universal-lora-build-logs-${{ matrix.shard }}", builds)
        contracts = self.job(source, "universal-lora-contracts")
        self.assertIn("python3 -B test/test_universal_lora_ci_split.py -v", contracts)
        self.assertEqual(source.count("run: bash tools/ci/run_universal_lora_smoke.sh contracts"), 1)
        self.assertIn("run: bash tools/ci/run_universal_lora_smoke.sh contracts", contracts)


if __name__ == "__main__":
    unittest.main()
