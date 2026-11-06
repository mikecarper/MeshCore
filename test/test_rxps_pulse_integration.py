#!/usr/bin/env python3
"""Compile actual pulse driver, wrapper and admission methods on a host owner."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/rxps_pulse_integration"


def harness():
    driver = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
    wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
    base = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
    interface = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text()
    actual_driver = "\n".join(body(driver, signature) for signature in (
        "void invalidateMorseModemCache(", "uint64_t pulsePacketBudgetUs(",
        "int16_t stagePulseReceive(", "int16_t verifyPulseReceive(",
        "int16_t enterPulseContinuous("))
    actual_wrapper = "\n".join(body(wrapper, signature).replace(" override", "") for signature in (
        "uint32_t defaultAGCPulseSleepUs(", "uint32_t rxPowerSavingTransitionUs(", "static bool pulseActivity(",
        "void pulseFailure(", "bool startAGCPulse(", "int16_t pulseContinuous(",
        "void retireFailedPulse(", "void serviceAGCPulse(", "int startReceiveMode("))
    lease = "\n".join(body(base, signature).replace("RadioLibWrapper::", "") for signature in (
        "void RadioLibWrapper::admitAGCPulse(", "void RadioLibWrapper::validateAGCScanLease(",
        "void RadioLibWrapper::finishAGCScanLease("))
    # Compile the actual scanner entry through its ownership guard. The spy
    # stands in for later timestamp/retune work, not for the guard itself.
    scan_prefix = body(base, "void RadioLibWrapper::serviceProfileScan(")
    scan_prefix = scan_prefix.split("  (void)profileTimestamp(_profiles.enabled());", 1)[0]
    scan_prefix = scan_prefix.replace("RadioLibWrapper::", "") + "  ++scan_spy;\n}"
    admission = body(base, "mesh::AGCMaintenanceResult RadioLibWrapper::periodicAGCMaintenance(")
    admission = admission.replace("RadioLibWrapper::", "")
    sleep_accessors = "\n".join(body(interface, signature).replace(" override", "") for signature in (
        "bool setAGCPulseSleep(", "uint32_t getAGCPulseSleep(", "bool serviceAGCMaintenanceFastLane(", "uint32_t agcMaintenanceRetryMs("))
    return (FIXTURE / "main.cpp").read_text().replace(
        "@DRIVER@", actual_driver).replace("@WRAPPER@", actual_wrapper + "\n" + lease + "\n" + scan_prefix + "\n" + admission + "\n" + sleep_accessors)


class PulseIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            raise unittest.SkipTest("A C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="rxps-pulse-integration-")
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        source = folder / "test.cpp"
        source.write_text(harness(), encoding="ascii")
        cls.binaries = []
        for fs in (0, 1):
            binary = folder / ("integration-fs" + str(fs))
            compiled = subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-fsanitize=address,undefined,float-cast-overflow", "-fno-omit-frame-pointer",
                "-fno-pie", "-no-pie", "-DTEST_FS=" + str(fs),
                "-I", str(FIXTURE), "-I", str(ROOT / "src"), str(source), "-o", str(binary),
            ], capture_output=True, text=True)
            if compiled.returncode:
                raise AssertionError(compiled.stderr)
            cls.binaries.append(binary)

    def run_case(self, name):
        environment = os.environ.copy()
        environment["ASAN_OPTIONS"] = "detect_leaks=1:abort_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        for binary in self.binaries:
            result = subprocess.run([str(binary), name], capture_output=True,
                                    text=True, env=environment)
            self.assertEqual(result.returncode, 0, str(binary) + ": " + result.stderr)
            self.assertEqual(result.stdout.strip(), "PASS " + name)

    def test_fast_lane_defers_slow_work_and_hands_packet_to_dispatcher(self):
        self.run_case("fast_lane")

    def test_full_arm_gpio_rtc_and_verified_rx_sequence(self):
        self.run_case("normal")

    def test_original_error_at_every_actual_command_stops_and_recovers(self):
        self.run_case("faults")

    def test_packet_before_and_during_standby_retains_fifo_and_irq(self):
        self.run_case("packet")

    def test_sleep_sampling_has_no_spi_or_rf_switch_writes(self):
        self.run_case("sleep")

    def test_persistent_failure_and_busy_recovery_are_bounded(self):
        self.run_case("bounded")

    def test_phy_geometry_and_one_tick_sleep_use_encoded_timer_guard(self):
        self.run_case("geometry")

    def test_invalid_or_overflow_geometry_never_arms_duty(self):
        self.run_case("invalid_geometry")

    def test_checked_mode_probe_restores_layout_and_rejects_nonrx(self):
        self.run_case("mode")

    def test_iq_workaround_preserves_gain_retention_patch_and_other_bits(self):
        self.run_case("iq")

    def test_packet_airtime_budget_is_cached_and_capped(self):
        self.run_case("budget")

    def test_actual_periodic_admission_and_single_use_failure_completion(self):
        self.run_case("admission")

    def test_dual_primary_pulse_preserves_visit_clock_and_busy_silence(self):
        self.run_case("dual_normal")

    def test_secondary_fixed_only_retry_does_no_radio_work(self):
        self.run_case("dual_secondary")

    def test_either_generation_enable_or_physical_context_change_cancels_credit(self):
        self.run_case("dual_stale")

    def test_dual_packet_handoff_keeps_fifo_and_requires_same_context(self):
        self.run_case("dual_packet")

    def test_scan_entry_defers_owned_and_packet_leases_before_timestamp_or_retune(self):
        self.run_case("dual_scan_guard")

    def test_dual_failed_hardware_recovery_releases_lease_with_finite_commands(self):
        self.run_case("dual_bounded")

    def test_default_margin_follows_resolved_oscillator_and_rejects_overflow(self):
        self.run_case("margin")


if __name__ == "__main__":
    unittest.main()
