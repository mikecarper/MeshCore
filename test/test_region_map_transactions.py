#!/usr/bin/env python3
"""Run actual RegionMap load/save with real platform transaction helpers."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/region_map_transactions"


def region_implementation(legacy=False):
    source = (ROOT / "src/helpers/RegionMap.cpp").read_text(encoding="ascii")
    # Retain the exact source helper block between name canonicalization and
    # load(), including its platform guards, rather than reproducing recovery
    # or path validation in the harness.
    helpers_start = source.index("static const char* skip_hash(")
    helpers_end = source.index("bool RegionMap::load(", helpers_start)
    helpers = source[helpers_start:helpers_end]
    if legacy and "static File openWrite(" in helpers:
        helpers = helpers.replace(body(helpers, "static File openWrite("), "")
    signatures = (
        "RegionMap::RegionMap(", "bool RegionMap::is_name_char(",
        "bool RegionMap::load(", "RegionEntry* RegionMap::putRegion(",
        "RegionEntry* RegionMap::findByName(", "RegionEntry* RegionMap::findById(",
        "RegionEntry* RegionMap::getHomeRegion(", "void RegionMap::setHomeRegion(",
        "RegionEntry* RegionMap::getDefaultRegion(", "void RegionMap::setDefaultRegion(",
        "bool RegionMap::wouldCreateCycle(",
    )
    generated = helpers + "\n" + "\n".join(body(source, signature) for signature in signatures)
    generated += "\n" + (FIXTURE / "legacy_writer.inc").read_text(encoding="ascii") if legacy else (
        "\n" + body(source, "bool RegionMap::save("))
    text_helpers = (ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(encoding="ascii")
    return generated + "\n" + body(text_helpers, "void StrHelper::strncpy(")


class RegionMapTransactionTest(unittest.TestCase):
    def run_platform(self, platform, legacy=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a native C++ compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-region-transactions-") as temporary:
            work = Path(temporary)
            (work / "region_under_test.inc").write_text(region_implementation(legacy), encoding="ascii")
            # Relocate the unchanged real transaction header so its quoted
            # platform IdentityStore include resolves to the native adapter.
            # PersistentStoreFormat remains the real production header.
            (work / "contact_transaction_under_test.h").write_text(
                (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="ascii"),
                encoding="ascii")
            (work / "IdentityStore.h").write_text(
                '#include <helpers/IdentityStore.h>\n', encoding="ascii")
            (work / "PersistentStoreFormat.h").write_text(
                '#include <helpers/PersistentStoreFormat.h>\n', encoding="ascii")
            executable = work / "test"
            command = [
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-sign-compare", "-Wno-unused-function",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-D" + platform + "=1", "-I", str(work), "-I", str(FIXTURE / "mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                str(FIXTURE / "test.cpp"), "-o", str(executable),
            ]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            environment = os.environ.copy()
            environment["ASAN_OPTIONS"] = "detect_leaks=1:abort_on_error=1"
            environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
            arguments = [str(executable)] + (["short-write"] if legacy else [])
            result = subprocess.run(arguments, capture_output=True, text=True,
                                    timeout=60, env=environment)
            if legacy:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("FAIL: durable old region/default/home/wildcard policy survived", result.stderr)
                self.assertNotIn("AddressSanitizer", result.stderr)
                self.assertNotIn("runtime error:", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS: actual RegionMap durable transaction and reboot policy", result.stdout)
                self.assertIn("666 short-write offsets and 666 full-storage capacities", result.stdout)

    def test_nrf52_durable_transactions(self):
        self.run_platform("NRF52_PLATFORM")

    def test_stm32_durable_transactions(self):
        self.run_platform("STM32_PLATFORM")

    def test_esp32_durable_transactions(self):
        self.run_platform("ESP32_PLATFORM")

    def test_rp2040_durable_transactions(self):
        self.run_platform("RP2040_PLATFORM")

    def test_old_esp32_truncation_is_detected(self):
        self.run_platform("ESP32_PLATFORM", legacy=True)

    def test_old_rp2040_truncation_is_detected(self):
        self.run_platform("RP2040_PLATFORM", legacy=True)

    def test_old_stm32_removal_is_detected(self):
        self.run_platform("STM32_PLATFORM", legacy=True)


if __name__ == "__main__":
    unittest.main()
