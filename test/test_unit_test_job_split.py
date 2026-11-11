"""Verify the CI summary cannot pass while a split test job fails or skips."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
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

    def test_native_diagnostics_are_bounded_and_cannot_hide_test_failures(self):
        source = WORKFLOW.read_text()
        workflow_options = source.split("\njobs:\n", 1)[0]
        self.assertIn("concurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n"
                      "  cancel-in-progress: true", workflow_options)
        core = self.job(source, "test-core")
        steps = dict(re.findall(r"^      - name: ([^\n]+)\n([\s\S]*?)(?=^      - |\Z)",
                                core, re.MULTILINE))
        diagnostic_names = {"Package Test Diagnostics", "Upload Test Results"}
        optional = {name for name, step in steps.items() if "continue-on-error:" in step}
        self.assertEqual(optional, diagnostic_names)
        for name in diagnostic_names:
            with self.subTest(step=name):
                step = steps[name]
                self.assertIn("if: always()", step)
                self.assertIn("continue-on-error: true", step)
                timeout = re.search(r"timeout-minutes: (\d+)", step)
                self.assertIsNotNone(timeout)
                self.assertIn(int(timeout.group(1)), (1, 2))
        upload = steps["Upload Test Results"]
        self.assertIn("path: ${{ runner.temp }}/meshcore-test-results.tar.gz", upload)
        self.assertIn("compression-level: 0", upload)
        self.assertNotIn("path: .pio/build/", upload)
        for name, log in (("Run Unit Tests", "native.log"),
                          ("Verify real GPS NMEA parser and provider with address and undefined sanitizers",
                           "native-gps.log")):
            with self.subTest(step=name):
                step = steps[name]
                self.assertNotIn("continue-on-error", step)
                self.assertIn("set -o pipefail", step)
                self.assertIn("2>&1 | tee .pio/test-results/" + log, step)
                run = re.search(r"^        run: \|\n([\s\S]+)", step, re.MULTILINE)
                self.assertIsNotNone(run)
                script = "\n".join(line[10:] for line in run.group(1).splitlines())
                bash = shutil.which("bash")
                if os.name == "nt":
                    git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
                    if git_bash.is_file():
                        bash = str(git_bash)
                if not bash:
                    self.skipTest("Bash is required to execute the CI pipeline fixture")
                with tempfile.TemporaryDirectory(prefix="native-ci-failure-") as directory:
                    work = Path(directory)
                    retry = work / "tools/ci/pio_retry.sh"
                    retry.parent.mkdir(parents=True)
                    retry.write_text("printf 'fixture test failure\\n' >&2\nexit 17\n")
                    completed = subprocess.run([bash, "-e", "-c", script], cwd=work,
                                               capture_output=True, text=True)
                    self.assertEqual(completed.returncode, 17, completed.stdout + completed.stderr)
                    self.assertEqual((work / ".pio/test-results" / log).read_text(),
                                     "fixture test failure\n")

    def test_actual_diagnostic_packager_keeps_logs_all_native_builds_and_failed_outcomes(self):
        core = self.job(WORKFLOW.read_text(), "test-core")
        package = core[core.index("      - name: Package Test Diagnostics"):
                       core.index("      - name: Upload Test Results")]
        match = re.search(r"^          import json, os, pathlib, tarfile\n([\s\S]*?)^          PYCODE\s*$",
                          package, re.MULTILINE)
        self.assertIsNotNone(match)
        script = "import json, os, pathlib, tarfile\n" + "\n".join(
            line[10:] for line in match.group(1).splitlines()) + "\n"
        self.assertIn("NATIVE_TEST_OUTCOME: ${{ steps.native-tests.outcome }}", package)
        self.assertIn("NATIVE_GPS_OUTCOME: ${{ steps.native-gps.outcome }}", package)
        with tempfile.TemporaryDirectory(prefix="native-ci-diagnostics-") as directory:
            work = Path(directory)
            fixtures = {".pio/test-results/native.log": b"native assertion failed\n"}
            for name in ("native", "native_kiss_modem", "native_ota_channel", "native_gps"):
                fixtures[".pio/build/" + name + "/test/program"] = name.encode()
            for path, content in {**fixtures, ".pio/build/unrelated/program": b"excluded"}.items():
                target = work / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            temporary = work / "runner-temp"
            temporary.mkdir()
            completed = subprocess.run([sys.executable, "-c", script], cwd=work, env={
                **os.environ, "RUNNER_TEMP": str(temporary), "GITHUB_SHA": "fixture-sha",
                "GITHUB_RUN_ID": "17", "NATIVE_TEST_OUTCOME": "failure", "NATIVE_GPS_OUTCOME": "skipped",
            }, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            with tarfile.open(temporary / "meshcore-test-results.tar.gz", "r:gz") as archive:
                self.assertNotIn(".pio/build/unrelated/program", archive.getnames())
                for path, content in fixtures.items():
                    self.assertEqual(archive.extractfile(path).read(), content)
                summary = json.load(archive.extractfile(".pio/test-results/summary.json"))
                self.assertEqual(summary, {"commit": "fixture-sha", "run": "17",
                                          "native": "failure", "native_gps": "skipped"})
            self.assertFalse(list(temporary.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
