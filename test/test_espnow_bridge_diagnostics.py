#!/usr/bin/env python3
"""Exercise bounded ESP-NOW diagnostics and actual legacy wire rejection."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]


class ESPNowBridgeDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise RuntimeError("A host C++17 compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="espnow-diagnostics-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        cls.binary = cls.work / "diagnostics"
        base = (ROOT / "src/helpers/bridges/BridgeBase.h").read_text(encoding="ascii")
        magic = re.findall(r"BRIDGE_PACKET_MAGIC\s*=\s*(0x[0-9A-Fa-f]+)\s*;", base)
        if len(magic) != 1:
            raise AssertionError("Expected one production wrapped bridge magic")
        flags = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                  "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        built = subprocess.run([
            cls.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", *flags,
            "-DWRAPPED_BRIDGE_MAGIC=" + magic[0],
            "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
            str(ROOT / "test/fixtures/espnow_bridge_diagnostics.cpp"),
            str(ROOT / "src/Packet.cpp"), "-o", str(cls.binary),
        ], capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, scenario):
        result = subprocess.run([str(self.binary), scenario], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_roundtrip_and_independent_wire_crc(self):
        wire = bytes.fromhex(self.run_case("roundtrip").strip())
        self.assertEqual(wire[:12], bytes.fromhex("FE4D43440101010D01234567"))
        self.assertEqual(int.from_bytes(wire[12:], "big"), zlib.crc32(wire[:12]))

    def test_null_short_and_oversize_inputs_preserve_outputs(self):
        self.run_case("bounds")

    def test_semantics_reject_invalid_metadata_with_valid_crc(self):
        self.run_case("metadata")

    def test_every_single_bit_corruption_is_rejected(self):
        self.run_case("corruption")

    def test_candidates_include_malformed_but_exclude_raw_fragments(self):
        self.run_case("candidate")

    def test_nonce_is_exactly_eight_hex_digits(self):
        self.run_case("nonce")

    def test_actual_legacy_packet_and_reassembler_reject_probe(self):
        self.run_case("legacy")

    def test_fixed_counters_saturate_and_unknown_error_is_safe(self):
        self.assertIn("counter_bytes=132", self.run_case("counters"))

    def test_helper_has_no_allocator_sdk_clock_or_output_dependencies(self):
        source = self.work / "pure.cpp"
        binary = self.work / "pure.o"
        source.write_text('''#include <helpers/bridges/ESPNowBridgeDiagnostics.h>
namespace diag = mesh::bridge::diag;
bool encode(uint8_t* out, size_t len, const diag::Probe& probe) {
  return diag::encodeProbe(out, len, probe);
}
bool decode(const uint8_t* bytes, size_t len, diag::Probe& probe) {
  return diag::decodeProbe(bytes, len, probe);
}
bool nonce(const char* text, uint32_t& value) {
  return diag::parseNonce(text, value);
}
''', encoding="ascii")
        result = subprocess.run([self.compiler, "-std=c++17", "-O2", "-c",
            "-I" + str(ROOT / "src"), str(source), "-o", str(binary)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        nm = shutil.which("nm")
        self.assertIsNotNone(nm, "nm is required for dependency inspection")
        # Hosted runners can briefly stall while loading binutils/plugins.
        # Give inspection the same bounded startup allowance as compilation;
        # the undefined-symbol dependency check below remains mandatory.
        symbols = subprocess.run([nm, "-u", str(binary)], capture_output=True,
                                 text=True, timeout=60, check=True).stdout
        self.assertNotRegex(symbols, r"(?i)malloc|calloc|realloc|free|_Zn[aw]|esp_|Serial|printf|millis|micros|atomic|mutex")


class ESPNowBridgeProductionDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_bridge_recovery import code, COMMON
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise RuntimeError("A host C++17 compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="espnow-production-diagnostics-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        cls.source = cls.work / "production.cpp"
        fixture = (ROOT / "test/fixtures/espnow_bridge_diagnostic_runtime.cpp").read_text(encoding="ascii")
        cls.source.write_text(code() + COMMON + fixture, encoding="ascii")
        cls.binary = cls.build(50200)

    @classmethod
    def build(cls, version, cooperative=1):
        binary = cls.work / ("production-" + str(version) + "-usb-" + str(cooperative))
        result = subprocess.run([
            cls.compiler, "-std=c++17", "-O1", "-Wall", "-Wextra",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
            "-DESP32=1", "-DARDUINO=1",
            "-DMESH_ESP32_USB_CONSOLE_COOPERATIVE=" + str(cooperative),
            "-DWITH_ESPNOW_BRIDGE=1", "-DWITH_RS232_BRIDGE=1",
            "-DESP_IDF_VERSION=" + str(version),
            "-DESP_IDF_VERSION_MAJOR=" + str(version // 10000),
            "-I" + str(ROOT / "src"), str(cls.source), "-o", str(binary),
        ], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return binary

    def run_case(self, scenario, binary=None):
        result = subprocess.run([str(binary or self.binary), scenario],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_receive_probes_never_allocate_echo_or_admit_mesh_in_either_format(self):
        self.run_case("rx-probes")

    def test_probe_sdk_rejection_callback_failure_and_timeout_never_retry(self):
        self.run_case("one-shot")

    def test_probe_and_mesh_queue_order_and_seen_ownership(self):
        self.run_case("ownership")

    def test_existing_admission_decisions_drive_exact_counters(self):
        self.run_case("admission")

    def test_queue_overflow_restart_persistence_and_reset_preserve_accepted_work(self):
        self.run_case("overflow-reset")

    def test_startup_error_stage_and_code_preserve_shared_wifi(self):
        self.run_case("startup")

    def test_saturated_pages_and_status_fit_reserved_reply_capacity(self):
        reply = self.run_case("replies")
        for suffix in ("parse=4294967295", "timeout=4294967295",
                       "big=4294967295", "rxid=FFFFFFFF",
                       "last=channel.conflict/-2147483648"):
            self.assertIn(suffix, reply)

    def test_callbacks_never_print_and_loop_logging_requires_both_gates(self):
        self.run_case("logging")

    def test_uart_logging_has_the_same_owner_gates_and_drops_without_capacity(self):
        self.run_case("logging", self.build(50200, cooperative=0))

    def test_diagnostic_callbacks_support_each_sdk_signature(self):
        for version in (40400, 50500):
            with self.subTest(idf=version):
                binary = self.build(version)
                self.run_case("rx-probes", binary)
                self.run_case("one-shot", binary)


if __name__ == "__main__":
    unittest.main()
