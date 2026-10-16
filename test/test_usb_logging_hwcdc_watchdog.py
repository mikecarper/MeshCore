"""Execute real watchdog policy/runtime plus the actual HWCDC recovery backend."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/usb_logging_watchdog"
SIGNATURES = (
    "static bool canAccessEsp32Hwcdc(void*)",
    "static void serviceEsp32HwcdcTxKickExclusive(",
    "static void serviceEsp32HwcdcTxKick()",
    "static void purgeEsp32HwcdcQueues(",
    "bool resetUsbCompanionTransport()",
    "UsbLoggingObservation observeUsbLoggingTransport()",
    "UsbLoggingRecoveryResult recoverUsbLoggingTransport(",
    "bool isUsbLoggingTransportRecoveryPending()",
    "bool takeUsbTerminalSessionReset()",
)


class HwcdcLoggingWatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="meshcore-hwcdc-watchdog-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.folder = Path(cls.directory.name)
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()
        cls.methods = "\n".join(body(source, signature) for signature in SIGNATURES)
        companion = (ROOT / "examples/companion_radio/main.cpp").read_text()
        cls.companion_methods = "\n".join(body(companion, signature) for signature in (
            "static void clearUsbTerminalLine()", "static void cancelUsbSerialOperations()",
            "static void resetUsbTerminalHostSession()", "static void serviceUsbTerminalHostSessionReset()"))
        cls.binaries = {(backport, ota): cls.compile_backend(backport, ota)
                        for backport in (0, 1) for ota in (False, True)}

    @classmethod
    def compile_backend(cls, backport, ota, legacy=False, no_bridge=False):
        suffix = f"{backport}-{int(ota)}-{int(legacy)}-{int(no_bridge)}"
        methods = cls.methods
        if legacy:
            attempted = "    (void)resetUsbCompanionTransport();"
            assert methods.count(attempted) == 1
            methods = methods.replace(attempted,
                                      "    if (!resetUsbCompanionTransport())\n"
                                      "      return UsbLoggingRecoveryResult::Deferred;")
        source = cls.folder / f"backend-{suffix}.cpp"
        service = cls.companion_methods
        if no_bridge:
            bridge = ("  if (!usb_terminal_host_reset_completion_pending\n"
                      "      && mesh::isUsbLoggingTransportRecoveryPending()) {\n"
                      "    resetUsbTerminalHostSession();\n"
                      "    return;\n"
                      "  }\n")
            assert service.count(bridge) == 1
            service = service.replace(bridge, "")
        source.write_text((FIXTURE / "hwcdc_backend.cpp").read_text()
                          .replace("@BACKEND_METHODS@", methods)
                          .replace("@COMPANION_METHODS@", service), encoding="ascii")
        # Copy unchanged bytes so the runtime's quoted OTA include resolves
        # through the isolated dependency fake, not the full firmware graph.
        runtime = cls.folder / "UsbLoggingWatchdog.cpp"
        shutil.copyfile(ROOT / "src/helpers/UsbLoggingWatchdog.cpp", runtime)
        binary = cls.folder / f"backend-{suffix}"
        result = subprocess.run([
            cls.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-sign-compare", *SANITIZER_FLAGS,
            "-DARDUINO", "-DESP32", "-DESP32_PLATFORM", "-DARDUINO_USB_MODE=1",
            "-DARDUINO_USB_CDC_ON_BOOT=1", f"-DMESH_HWCDC_PINNED_TX_BACKPORT={backport}",
            "-DMESH_DEBUG=1",
            *(["-DENABLE_OTA"] if ota else []),
            f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src/helpers'}", f"-I{ROOT / 'src'}",
            str(runtime), str(source), "-o", str(binary),
        ], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return binary

    def run_case(self, binary, scenario, step):
        return subprocess.run([str(binary), scenario, str(step)], capture_output=True,
                              text=True, timeout=10)

    def check_cases(self, scenarios, ota_only=False):
        for (backport, ota), binary in self.binaries.items():
            if ota_only and not ota:
                continue
            for step in (0, 8):
                for scenario in scenarios:
                    with self.subTest(backport=backport, ota=ota, step=step, scenario=scenario):
                        result = self.run_case(binary, scenario, step)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn(f"PASS: HWCDC watchdog {scenario} step={step}", result.stdout)

    def test_zero_capacity_and_stuck_cleanup_preserve_final_deadline(self):
        self.check_cases(("zero", "stuck", "busy"))

    def test_healthy_transport_and_disabled_modes_do_not_reboot(self):
        self.check_cases(("healthy", "off", "auto"))

    def test_recovery_safety_and_failed_journal_remain_authoritative(self):
        self.check_cases(("safe-start", "safe-deadline", "journal-fail", "isr", "logging-off"))

    def test_companion_finishes_pending_cleanup_without_new_host_edge(self):
        self.check_cases(("resume-auto", "resume-off"))

    def test_actual_ota_veto_before_detach_and_at_reboot_deadline(self):
        self.check_cases(("ota-start", "ota-deadline"), ota_only=True)

    def test_legacy_deferred_classification_negative_control(self):
        binary = self.compile_backend(1, True, legacy=True)
        for scenario in ("zero", "stuck"):
            for step in (0, 8):
                with self.subTest(scenario=scenario, step=step):
                    result = self.run_case(binary, scenario, step)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("CHECK: HWCDC stage-2 detach was requested", result.stdout)
                    self.assertIn("HWCDC detach request must count as attempted", result.stderr)

    def test_missing_companion_pending_cleanup_bridge_negative_control(self):
        binary = self.compile_backend(1, True, no_bridge=True)
        for scenario in ("resume-auto", "resume-off"):
            with self.subTest(scenario=scenario):
                result = self.run_case(binary, scenario, 0)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Companion must adopt a pending watchdog cleanup without a host edge",
                              result.stderr)


if __name__ == "__main__":
    unittest.main()
