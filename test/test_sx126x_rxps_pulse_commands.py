#!/usr/bin/env python3
"""Compile the actual pulse command header against a fault-injecting SPI owner."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/sx126x_rxps_pulse_commands"


class PulseCommandsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            raise unittest.SkipTest("A C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="sx126x-rxps-pulse-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binary = Path(cls.temporary.name) / "commands"
        compiled = subprocess.run([
            compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-fno-pie", "-no-pie", "-I", str(FIXTURE), "-I", str(ROOT / "src"),
            str(FIXTURE / "commands.cpp"), "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)

    def run_case(self, name):
        environment = os.environ.copy()
        environment["ASAN_OPTIONS"] = "detect_leaks=1:abort_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        result = subprocess.run([str(self.binary), name], check=True,
                                capture_output=True, text=True, env=environment)
        self.assertEqual(result.stdout.strip(), "PASS " + name)

    def test_lab_sleep_values_use_real_transition_and_tick_math(self):
        self.run_case("lab_periods")

    def test_positive_tick_and_24_bit_boundaries(self):
        self.run_case("period_boundaries")

    def test_underflow_and_full_uint32_inputs_cannot_wrap_into_valid_periods(self):
        self.run_case("period_overflow")

    def test_irq_read_preserves_flags_and_ignores_bytes_on_failure(self):
        self.run_case("irq")

    def test_standby_has_no_wakeup_nop_and_retains_original_error(self):
        self.run_case("standby")

    def test_rtc_cleanup_preserves_unrelated_event_bits(self):
        self.run_case("rtc_bits")

    def test_rtc_cleanup_stops_at_each_original_spi_failure(self):
        self.run_case("rtc_failures")

    def test_duty_arm_encodes_six_bytes_and_retains_error(self):
        self.run_case("arm")

    def test_invalid_encoded_periods_never_touch_spi(self):
        self.run_case("invalid_arm")

    def test_command_sequence_never_changes_gain_retention_or_patch(self):
        self.run_case("sequence")


if __name__ == "__main__":
    unittest.main()
