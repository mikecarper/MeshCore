"""Exercise the production Bluefruit dispatcher fix under ASan and UBSan."""

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/nrf52_ble_disconnect"
spec = spec_from_file_location("nrf52_ble_patcher", ROOT / "scripts/nrf52_ble_cccd_fix.py")
patcher = module_from_spec(spec)
spec.loader.exec_module(patcher)


class Nrf52BleDisconnectTest(unittest.TestCase):
    def setUp(self):
        self.source = (FIXTURE / "bluefruit_dispatch.cpp").read_text()

    def test_patch_is_idempotent_and_preserves_newlines(self):
        fixed = patcher.patched_disconnect_source(self.source)
        self.assertNotEqual(fixed, self.source)
        self.assertEqual(patcher.patched_disconnect_source(fixed), fixed)
        self.assertEqual(patcher.patched_disconnect_source(
            self.source.replace("\n", "\r\n")), fixed)

    def test_changed_duplicate_or_partial_dispatchers_fail_closed(self):
        for old, fixed in patcher.DISCONNECT_PATCHES:
            for malformed in (
                    self.source.replace(old, "changed framework source"),
                    self.source + old,
                    self.source.replace(old, fixed)):
                with self.subTest(marker=old, source=malformed[:30]):
                    with self.assertRaises(RuntimeError):
                        patcher.patched_disconnect_source(malformed)
        fixed_source = patcher.patched_disconnect_source(self.source)
        for _, fixed in patcher.DISCONNECT_PATCHES:
            with self.subTest(fixed_marker=fixed):
                with self.assertRaises(RuntimeError):
                    patcher.patched_disconnect_source(fixed_source + fixed)

    def test_private_bluefruit_pipeline_applies_startup_and_disconnect_fixes(self):
        source = patcher.OLD_BEGIN + " { return true; }\n" + self.source
        fixed = patcher.patched_bluefruit_source(source)
        self.assertIn(patcher.FIXED_BEGIN, fixed)
        self.assertEqual(patcher.patched_bluefruit_source(fixed), fixed)
        for old, new in patcher.DISCONNECT_PATCHES:
            self.assertNotIn(old, fixed)
            self.assertEqual(fixed.count(new), 1)

    def run_dispatcher(self, source):
        with tempfile.TemporaryDirectory(prefix="nrf52-ble-disconnect-") as directory:
            work = Path(directory)
            dispatcher = work / "dispatch.cpp"
            dispatcher.write_text(source)
            binary = work / "dispatch"
            subprocess.run([
                "c++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-fno-pie", "-no-pie", "-I" + str(FIXTURE),
                str(dispatcher), str(FIXTURE / "test.cpp"), "-o", str(binary),
            ], check=True, capture_output=True, text=True)
            environment = os.environ.copy()
            environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
            environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
            return subprocess.run([str(binary)], capture_output=True,
                                  text=True, env=environment)

    def test_original_dispatcher_reproduces_heap_use_after_free(self):
        result = self.run_dispatcher(self.source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AddressSanitizer: heap-use-after-free", result.stderr)
        self.assertIn("BLEConnection::getRole()", result.stderr)

    def test_fixed_dispatcher_preserves_both_roles_and_reconnects(self):
        fixed = patcher.patched_disconnect_source(self.source)
        result = self.run_dispatcher(fixed)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
