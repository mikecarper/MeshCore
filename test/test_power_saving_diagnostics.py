#!/usr/bin/env python3
"""Execute the real heap-free diagnostics collector and bounded CLI formatter."""

from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PowerSavingDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-power-diag-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary = Path(cls.temp.name) / "diagnostics"
        result = subprocess.run(
            shlex.split(os.environ.get("CXX", "g++"))
            + ["-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
               "-I", str(ROOT / "src"),
               str(ROOT / "test/fixtures/power_saving_diagnostics.cpp"),
               str(ROOT / "test/fixtures/power_saving_diagnostics_other.cpp"),
               "-o", str(cls.binary)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_disabled_has_no_statistics_or_policy_side_effects(self):
        self.run_case("disabled")

    def test_overlapping_reasons_count_union_once(self):
        self.run_case("overlap")

    def test_off_freezes_and_next_on_starts_fresh(self):
        self.run_case("freeze")

    def test_clear_preserves_mode_but_clears_diagnostics(self):
        self.run_case("reset")

    def test_millis_and_micros_wrap_and_short_return_boundary(self):
        self.run_case("wrap")

    def test_veto_cancellation_and_sdk_returns_are_distinct_from_success(self):
        self.run_case("veto_error")

    def test_elapsed_counters_and_transition_counts_saturate(self):
        self.run_case("saturation")

    def test_cli_commands_and_reason_queries(self):
        self.run_case("commands")

    def test_invalid_commands_do_not_change_collector(self):
        self.run_case("strict")

    def test_stale_snapshot_age_and_no_residency_claim(self):
        self.run_case("format")

    def test_all_pages_fit_prefixed_160_byte_protocol_and_tiny_buffers(self):
        self.run_case("capacity")

    def test_header_state_is_shared_across_translation_units(self):
        self.run_case("shared")

    def test_unknown_mask_bits_are_not_exposed_as_real_reasons(self):
        self.run_case("unknown_mask")

    def test_common_cli_routes_without_persisting_preferences(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        self.assertIn('#include "PowerSavingDiagnostics.h"', source)
        self.assertEqual(source.count(
            "if (mesh::powerdiag::handleCommand(command, reply, 157, millis())) return;"), 1)
        handler = (ROOT / "src/helpers/PowerSavingDiagnostics.h").read_text(encoding="ascii")
        self.assertNotIn("savePrefs", handler)
        self.assertNotIn("#include <Arduino", handler)


if __name__ == "__main__":
    unittest.main()
