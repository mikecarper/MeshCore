#!/usr/bin/env python3
"""Compile actual pulse state and diagnostics with boundary/fault scenarios."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PulseStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            raise unittest.SkipTest("A C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="rxps-pulse-fsm-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binary = Path(cls.temporary.name) / "fsm"
        compiled = subprocess.run([
            compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-fno-pie", "-no-pie", "-I", str(ROOT / "src"),
            str(ROOT / "test/fixtures/rxps_pulse_fsm.cpp"), "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)

    def run_case(self, name):
        environment = os.environ.copy()
        environment["ASAN_OPTIONS"] = "detect_leaks=1:abort_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        result = subprocess.run([str(self.binary), name],
                                capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "PASS " + name)

    def test_coarse_clock_and_measured_arm_latency_allow_valid_cycles(self):
        self.run_case("clock_uncertainty")

    def test_unconfirmed_attempt_is_silently_discarded_without_success(self):
        self.run_case("quiet_discard")

    def test_complete_witness_requires_checked_receive_return(self):
        self.run_case("success")

    def test_post_arm_low_is_required(self):
        self.run_case("missing_low")

    def test_early_busy_is_not_sleep(self):
        self.run_case("early_high")

    def test_short_busy_cannot_credit_cycle(self):
        self.run_case("short_high")

    def test_exact_witness_boundaries(self):
        self.run_case("witness_boundaries")

    def test_high_span_upper_boundary_and_one_microsecond_overrun(self):
        self.run_case("high_upper_boundary")

    def test_deadline_precedes_late_wake_transition(self):
        self.run_case("late_wake")

    def test_timeout_and_recovery_keep_first_error(self):
        self.run_case("failure_recovery")

    def test_wraparound_uses_elapsed_microseconds(self):
        self.run_case("wrap")

    def test_packet_handoff_preserves_owner_and_defers(self):
        self.run_case("packet")

    def test_cancel_is_idempotent_and_never_credits_success(self):
        self.run_case("cancel")

    def test_abandon_releases_once_and_reports_failed_completion(self):
        self.run_case("abandon")

    def test_configuration_rejects_pending_and_invalid_periods(self):
        self.run_case("configure")

    def test_cadence_changes_cancel_lease_and_suppress_stale_completion(self):
        self.run_case("cadence")

    def test_completed_and_recovery_statistics_stay_separate(self):
        self.run_case("statistics")

    def test_maximum_diagnostics_fit_cli_and_respect_all_capacities(self):
        self.run_case("format")


if __name__ == "__main__":
    unittest.main()
