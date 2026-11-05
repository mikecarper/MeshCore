#!/usr/bin/env python3
"""Run actual warm-maintenance, ownership and driver methods on a host adapter."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/warm_rx_refresh"


def field(source, name):
    match = re.search(r"^\s*[^\n;]+\b" + re.escape(name) + r"\s*=\s*[^;]+;", source, re.MULTILINE)
    if not match:
        raise AssertionError("Missing production field: " + name)
    return match.group(0).strip()


def methods(source, signatures):
    return "\n".join(body(source, signature).replace(" override", "") for signature in signatures)


def lab_timer_fields(source):
    return "#if MESH_WARM_RX_LAB_TIMER\n" + "\n".join(field(source, name) for name in (
        "_warm_rx_lab_timer_active", "_warm_rx_lab_timer_ms")) + "\n#endif"


def lab_timer_methods(source, virtual=False):
    prefix = "virtual " if virtual else ""
    return "#if MESH_WARM_RX_LAB_TIMER\n" + methods(source, (
        prefix + "bool setWarmRxLabInterval(", prefix + "bool getWarmRxLabInterval(",
        prefix + "uint32_t warmRxLabTimerRevision(")) + "\n#endif"


def harness():
    driver = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
    custom = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
    source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
    header = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text()
    dispatcher = (ROOT / "src/Dispatcher.h").read_text()
    dispatch_source = (ROOT / "src/Dispatcher.cpp").read_text()
    driver_methods = methods(driver, (
        "uint8_t defaultProfileSwitchFeatures(", "uint8_t profileSwitchCapabilities(",
        "uint8_t getProfileSwitchFeatures(", "void setProfileSwitchFeatures(",
        "void setProfileSwitchOptimization(", "void beginProfileSwitch(",
        "void endProfileSwitch(", "bool profileSwitchFailed(",
        "bool warmRxRefreshReady(", "void trackWarmRxRefresh(",
        "WarmRxRefresh::Step warmRxRefreshStep(", "int16_t warmRxRefreshError(",
        "uint32_t warmRxRefreshReturnedUs(",
        "int16_t startWarmRxRefreshReceive(", "int16_t restoreTcxoAfterSleep(",
        "int16_t standby()", "int16_t standby(uint8_t", "int16_t sleep()",
        "int16_t sleep(bool", "int16_t reset(bool", "int16_t stageMode(",
        "int16_t setPreambleLength(", "int16_t setCRC(", "int16_t implicitHeader(",
        "int16_t explicitHeader()", "int16_t invertIQ(", "int16_t startReceive()",
        "int16_t startReceiveDutyCycle(",
    ))
    wrapper_methods = methods(source, (
        "uint8_t RadioLibWrapper::beginReconfigure(",
        "void RadioLibWrapper::endReconfigure(", "void RadioLibWrapper::startRecv(",
        "int RadioLibWrapper::recvRaw(",
        "mesh::AGCMaintenanceResult RadioLibWrapper::periodicAGCMaintenance(",
        "mesh::AGCMaintenanceResult RadioLibWrapper::periodicOldAGCMaintenance(",
        "bool RadioLibWrapper::resetAGCIfIdle(",
        "bool RadioLibWrapper::usesAgessamanNoiseFloor(",
    ))
    wrapper_methods = wrapper_methods.replace(
        "void RadioLibWrapper::endReconfigure(bool resume_rx) {",
        "void RadioLibWrapper::endReconfigure(bool resume_rx) { ++reconfigure_ends;", 1)
    custom_methods = methods(custom, (
        "bool supportsWarmRxRefresh(", "bool warmRxRefreshUsesFs(",
        "void beginProfileRetune(", "void endProfileRetune(",
        "WarmRxRefresh::Outcome performWarmRxRefresh(",
    ))
    declarations = "\n".join((
        "uint8_t beginReconfigure();", "void endReconfigure(bool);", "void startRecv();",
        "int recvRaw(uint8_t*, int);",
        "virtual mesh::AGCMaintenanceResult periodicAGCMaintenance(bool);",
        "virtual mesh::AGCMaintenanceResult periodicOldAGCMaintenance();",
        methods(header, ("bool setOldAGCInterval(", "uint32_t getOldAGCInterval(",
                         "uint32_t agcMaintenanceRetryMs(")),
    ))
    wrapper = (FIXTURE / "wrapper.cpp").read_text()
    wrapper = wrapper.replace("@PERIODIC_OLD_FIELD@", field(header, "_old_agc_seconds"))
    wrapper = wrapper.replace("@LAB_TIMER_FIELDS@", lab_timer_fields(header))
    wrapper = wrapper.replace("@LAB_TIMER_METHODS@", lab_timer_methods(header))
    wrapper = wrapper.replace("@BASE_SUPPORT@", body(dispatcher, "virtual bool supportsWarmRxRefresh("))
    wrapper = wrapper.replace("@WRAPPER_DECLARATIONS@", declarations)
    wrapper = wrapper.replace("@CUSTOM_WRAPPER_METHODS@", custom_methods)
    wrapper = wrapper.replace("@WRAPPER_METHODS@", wrapper_methods)
    # Compile the actual scheduling/configuration blocks in a small host owner.
    # Unrelated packet processing in Dispatcher::loop is outside this fixture.
    configure_start = dispatch_source.index("  const unsigned long now = _ms->getMillis();\n  const bool receive_allowed")
    configure_end = dispatch_source.index("  if (!radio_nonrx_timer_armed)", configure_start)
    service_start = dispatch_source.index("  const uint32_t agc_now = _ms->getMillis();")
    service_end = dispatch_source.index("  // check inbound (delayed) queue", service_start)
    dispatch_fixture = (FIXTURE / "dispatcher.cpp").read_text()
    dispatch_fixture = dispatch_fixture.replace("@LAB_TIMER_REVISION@",
        "#if MESH_WARM_RX_LAB_TIMER\n" + field(dispatcher, "_agc_lab_timer_revision") + "\n#endif")
    dispatch_fixture = dispatch_fixture.replace("@CONFIGURE@", dispatch_source[configure_start:configure_end])
    dispatch_fixture = dispatch_fixture.replace("@SERVICE@", dispatch_source[service_start:service_end])
    wake_start = dispatch_source.index("  // Timed MCU sleep must not skip the adaptive maintenance/retry deadline.")
    wake_end = dispatch_source.index("  // Preserve the legacy queue-only query", wake_start)
    dispatch_fixture = dispatch_fixture.replace("@OLD_WAKE@", dispatch_source[wake_start:wake_end])
    dispatch_fixture = dispatch_fixture.replace("@CLOCK_GUARD@", body(dispatch_source, "bool Dispatcher::millisHasNowPassed("))
    app_header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
    dispatch_fixture = dispatch_fixture.replace("@SAVED_INTERVAL@", body(app_header, "int getAGCResetInterval() const").replace(" override", ""))
    wrapper = wrapper.replace("@DISPATCHER@", dispatch_fixture)
    result = (FIXTURE / "radio.cpp").read_text()
    result = result.replace("@DRIVER_MEMBERS@", "\n".join(field(driver, name) for name in (
        "_warm_refresh_step", "_warm_refresh_tracking", "_warm_refresh_returned_us")))
    result = result.replace("@DRIVER_METHODS@", driver_methods)
    result = result.replace("@WRAPPER@", wrapper)
    cases = (FIXTURE / "main.cpp").read_text()
    cases = cases.replace("@SCHEDULER_CASES@", (FIXTURE / "scheduler.cpp").read_text())
    cases = cases.replace("@LAB_TIMER_CASES@", (FIXTURE / "lab_timer.cpp").read_text())
    return result.replace("@SCENARIOS@", cases)


class WarmRxRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("warm RX refresh tests require a host C++ compiler")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-warm-rx-refresh-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        cpp = work / "refresh.cpp"
        cpp.write_text(harness(), encoding="ascii")
        cls.binaries = []
        cls.lab_binaries = []
        for fs, lab in ((0, 0), (1, 0), (0, 1), (1, 1)):
            binary = work / ("refresh-fs" + str(fs) + "-lab" + str(lab))
            flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                     "-DMESH_WARM_RX_REFRESH=1", "-DMC_SX1262_FAST_PROFILE_SWITCH=1",
                     "-DMC_SX1262_FAST_PACKET_CACHE=1", "-DMC_SX1262_FAST_IQ_CACHE=0",
                     "-DMC_SX1262_FAST_MODEM_CACHE=0", "-DMC_SX1262_FAST_FS=" + str(fs),
                     "-DMESH_ENABLE_TX_BEACONS=1", "-DMESH_ENABLE_MORSE_RX=1",
                     "-DMESH_ENABLE_RADIO_DIRECTION=1"]
            flags += ["-DMC_AGESSAMAN_NOISE_FLOOR_EXACT=1"]
            flags += ["-DMESH_WARM_RX_LAB_TIMER=" + str(lab)]
            if sys.platform.startswith("linux"):
                flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, *flags, "-I" + str(ROOT / "src"),
                                    str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries.append(binary)
            if lab:
                cls.lab_binaries.append(binary)

    def run_case(self, scenario, lab_only=False):
        for binary in self.lab_binaries if lab_only else self.binaries:
            with self.subTest(binary=binary.name):
                ran = subprocess.run([str(binary), scenario], capture_output=True,
                                     text=True, timeout=15)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                self.assertIn("PASS: " + scenario, ran.stdout)

    def test_success_issues_only_owned_standby_irq_clear_and_checked_set_rx(self):
        self.run_case("success")

    def test_radio_owner_and_exclusive_policy_guards_issue_no_commands(self):
        self.run_case("guards")

    def test_fifo_and_irq_survive_before_probe_and_standby_races(self):
        self.run_case("ownership")

    def test_unproven_cache_or_features_cannot_fall_back_to_parameter_writes(self):
        self.run_case("caches")

    def test_every_command_failure_revokes_reuse_and_schedules_bounded_repair(self):
        self.run_case("failures")

    def test_no_op_receive_or_unchecked_applied_never_earns_success(self):
        self.run_case("noop")

    def test_legacy_fallback_and_unsupported_defaults_remain_distinct(self):
        self.run_case("legacy")

    def test_existing_dispatcher_scheduler_wrap_and_busy_deferral(self):
        self.run_case("scheduler")

    def test_lab_one_and_four_second_fixed_cadences_wake_and_wrap(self):
        self.run_case("lab-cadence", lab_only=True)

    def test_lab_override_changes_start_future_deadlines_and_off_restores_saved_policy(self):
        self.run_case("lab-changes", lab_only=True)

    def test_lab_busy_retry_stays_250ms_and_primary_zero_preserves_old_timer(self):
        self.run_case("lab-deferral", lab_only=True)

    def test_lab_unsupported_radio_retains_existing_saved_scheduler(self):
        self.run_case("lab-unsupported", lab_only=True)

    def test_feature_defaults_off_and_unsupported_base_retains_safe_defaults(self):
        dispatcher = (ROOT / "src/Dispatcher.h").read_text()
        interface = methods(dispatcher, (
            "virtual bool supportsWarmRxRefresh(", "virtual bool setOldAGCInterval(",
            "virtual uint32_t getOldAGCInterval(", "virtual uint32_t agcMaintenanceRetryMs(",
        ))
        fixture = """
#include <cassert>
#include <helpers/radiolib/WarmRxRefresh.h>
static_assert(MESH_WARM_RX_REFRESH == 0, "warm experiment must default off");
static_assert(MESH_WARM_RX_LAB_TIMER == 0, "warm lab timer must default off");
struct Unsupported { @INTERFACE@ };
int main() {
  Unsupported value;
  assert(!value.supportsWarmRxRefresh());
  assert(!value.setOldAGCInterval(600) && value.getOldAGCInterval() == 0);
  assert(value.agcMaintenanceRetryMs() == 50);
}
""".replace("@INTERFACE@", interface)
        default = re.search(r"#ifndef MESH_WARM_RX_LAB_TIMER\n[^\n]+\n#endif", dispatcher)
        self.assertIsNotNone(default, "lab flag must have an explicit default")
        fixture = default.group() + "\n" + fixture
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        work = Path(self.work.name)
        cpp, binary = work / "defaults.cpp", work / "defaults"
        cpp.write_text(fixture, encoding="ascii")
        built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                "-I" + str(ROOT / "src"), str(cpp), "-o", str(binary)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
