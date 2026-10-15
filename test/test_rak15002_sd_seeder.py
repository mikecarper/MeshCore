#!/usr/bin/env python3
"""Qualify the optional source-only RAK15002 integration without RF or a card.

Compile the actual bus helper and extracted production context/CLI methods;
the separate test_ota_sd_cache suite executes the real FAT cache and protocol.
No PlatformIO invocation is used here. Physical module qualification is still
required before promoting this experimental target to a receiver/release.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_upstream_power_policy_merge import Recipes

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/rak15002_sd_seeder"
CONTEXT = ROOT / "src/helpers/ota/OtaContext.h"
ENV = "env:RAK_4631_repeater_rak15002_sd_seeder_only"
BASE = "env:RAK_4631_repeater_lora_ota_no_external_sensors"
DEFINES = ["NRF52_PLATFORM", "RAK_4631", "OTA_SD_STORE", "OTA_SEEDER_ONLY",
           "OTA_RAK15002_SD_CACHE", "SPI_INTERFACES_COUNT=1", "PIN_SPI_MISO=29",
           "PIN_SPI_SCK=3", "PIN_SPI_MOSI=30", "PIN_SPI1_NSS=12"]


def build(testcase, source, defines=(), files=None, expect_success=True):
    compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        testcase.skipTest("a host C++17 compiler is required")
    with tempfile.TemporaryDirectory(prefix="rak15002-") as folder:
        work = Path(folder)
        (work / "main.cpp").write_text(source, encoding="utf-8")
        for name, content in (files or {}).items():
            (work / name).write_text(content, encoding="utf-8")
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                 "-Wno-unused-parameter", *sanitizer,
                                 *["-D" + value for value in defines],
                                 "-I" + str(work), "-I" + str(ROOT / "src"),
                                 str(work / "main.cpp"), "-o", str(work / "test")],
                                capture_output=True, text=True)
        if not expect_success:
            testcase.assertNotEqual(result.returncode, 0, "unsafe configuration compiled")
            return
        testcase.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run = subprocess.run([str(work / "test")], capture_output=True, text=True)
        testcase.assertEqual(run.returncode, 0, run.stdout + run.stderr)


SPI_MOCK = r'''
#pragma once
#include <cassert>
#include <vector>
#define HIGH 1
#define OUTPUT 1
struct NRF_SPIM_Type {};
inline NRF_SPIM_Type peripheral2, peripheral3;
#define NRF_SPIM2 (&peripheral2)
#define NRF_SPIM3 (&peripheral3)
struct SPIClass {
  NRF_SPIM_Type* peripheral;
  int miso, sck, mosi;
  SPIClass(NRF_SPIM_Type* p, int i, int c, int o) : peripheral(p), miso(i), sck(c), mosi(o) {}
};
inline SPIClass SPI(NRF_SPIM3, 45, 43, 44);
inline SPIClass SPI1(NRF_SPIM2, 9, 10, 11);
struct GpioEvent { int type, pin, value; };
inline std::vector<GpioEvent> events;
inline void digitalWrite(int pin, int value) { events.push_back({0, pin, value}); }
inline void pinMode(int pin, int value) { events.push_back({1, pin, value}); }
'''

BUS_TEST = r'''
#include <helpers/ota/OtaSdSpiBusNrf52.h>
int main() {
  using namespace mesh::ota;
  auto& bus = ota_sd_spi_bus();
  assert(&bus == &ota_sd_spi_bus());
#if defined(OTA_RAK15002_SD_CACHE)
  assert(&bus != &SPI && &bus != &SPI1);
  assert(bus.peripheral == NRF_SPIM2 && bus.miso == 29 && bus.sck == 3 && bus.mosi == 30);
  assert(SPI.peripheral == NRF_SPIM3 && SPI.miso == 45 && SPI.sck == 43 && SPI.mosi == 44);
  static_assert(OTA_SD_CS_PIN == 26, "exact RAK CS");
#else
  assert(&bus == &SPI1);
  static_assert(OTA_SD_CS_PIN == PIN_SPI1_NSS, "unchanged legacy CS");
#endif
  ota_sd_prepare_chip_select();
  assert(events.size() == 2 && events[0].type == 0 && events[0].value == HIGH);
  assert(events[1].type == 1 && events[1].value == OUTPUT);
  assert(events[0].pin == OTA_SD_CS_PIN && events[1].pin == OTA_SD_CS_PIN);
}
'''


class Rak15002SdSeederTests(unittest.TestCase):
    def test_exact_profile_preserves_features_and_bounds_source_only_memory(self):
        recipes = Recipes()
        self.assertEqual(recipes.config.get(ENV, "extends"), BASE)
        flags = recipes.value(ENV, "build_flags")
        for flag in ("OTA_SEEDER_ONLY=1", "OTA_SD_STORE=1", "OTA_RAK15002_SD_CACHE=1",
                     "OTA_SD_CS_PIN=26", "OTA_SD_SCK_MHZ=4", "OTA_SD_CACHE_MAX=16",
                     "OTA_MAX_SERVE=24", "OTA_HEAP_CONTEXT=1"):
            self.assertRegex(flags, r"-D\s*" + re.escape(flag) + r"(?:\s|$)")
        self.assertIn("-UOTA_FLASH_STORE", flags)
        for flag in ("DISPLAY_CLASS=SSD1306Display", "NRF52_POWER_MANAGEMENT",
                     "WITH_RS232_BRIDGE=Serial2"):
            self.assertIn(flag, flags)
        for flag in ("OTA_QSPI_STORE", "OTA_RAK_AUTO_STORE", "OTA_TOWER_AUTO_STORE",
                     "OTA_SD_BOOTLOADER_UPDATE", "COMPANION_RADIO_FULL", "ETHERNET_ENABLED"):
            self.assertNotRegex(flags, r"-D\s*" + flag + r"(?:=|\s|$)")
        libraries = recipes.value(ENV, "lib_deps")
        self.assertIn("SdFat-beta.git#a249cfa", libraries)
        self.assertIn("u-blox", libraries)
        self.assertIn("SSD1306", libraries)
        self.assertNotIn("OTA_RAK15002_SD_CACHE", recipes.value(BASE, "build_flags"))
        self.assertNotIn("SdFat", recipes.value(BASE, "lib_deps"))

    def test_real_bus_keeps_radio_pins_and_cs_safe(self):
        build(self, BUS_TEST, DEFINES, {"SPI.h": SPI_MOCK})

    def test_existing_tower_bus_is_unchanged(self):
        build(self, BUS_TEST, ["PIN_SPI1_NSS=12"], {"SPI.h": SPI_MOCK})

    def test_conflicting_buses_and_unqualified_handoffs_fail_closed(self):
        for conflict in ("ETHERNET_ENABLED=0", "OTA_QSPI_STORE", "OTA_RAK_AUTO_STORE",
                         "OTA_TOWER_AUTO_STORE", "OTA_SD_BOOTLOADER_UPDATE",
                         "OTA_INTERNAL_BOOTLOADER_UPDATE", "OTA_QSPI_BOOTLOADER_UPDATE",
                         "SPI_32MHZ_INTERFACE=1", "OTA_SD_CS_PIN=25"):
            with self.subTest(conflict=conflict):
                build(self, BUS_TEST, [*DEFINES, conflict], {"SPI.h": SPI_MOCK}, False)
        for missing in ("NRF52_PLATFORM", "RAK_4631", "OTA_SD_STORE", "OTA_SEEDER_ONLY"):
            with self.subTest(missing=missing):
                build(self, BUS_TEST, [d for d in DEFINES if d != missing],
                      {"SPI.h": SPI_MOCK}, False)
        build(self, BUS_TEST, [d.replace("SPI_INTERFACES_COUNT=1", "SPI_INTERFACES_COUNT=2")
                              for d in DEFINES], {"SPI.h": SPI_MOCK}, False)

    def test_real_context_storage_ownership_service_preemption_and_apply_refusal(self):
        context = CONTEXT.read_text(encoding="utf-8")
        start = context.index("  OtaManager manager;")
        end = context.index("  SignerAllowlist allow;", start)
        signatures = ("OtaStoreSdNrf52& sdStagingStore()", "bool ensureSdCache()",
                      "bool setSdCacheEnabled(", "bool sdCacheFetching()",
                      "void stopSdCacheFetch()", "void prepareSdCardReset()",
                      "void finishSdCardReset(", "void serviceSdCache(",
                      "bool apply_fetched_impl(", "bool apply_fetched_bootloader(")
        methods = "\n".join(extract_braced(context, sig) for sig in signatures)
        fixture = (FIXTURES / "context.cpp").read_text()
        build(self, fixture.replace("@MEMBERS@", context[start:end]).replace("@METHODS@", methods),
              ["NRF52_PLATFORM", "OTA_SD_STORE", "OTA_SEEDER_ONLY", "OTA_SD_CACHE_MAX=16"])

    def test_actual_rail_policy_leaves_unswitched_module_off_gps_rail(self):
        source = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        method = extract_braced(source, "uint8_t EnvironmentSensorManager::detectWisBlockStorageHolds()")
        harness = r'''
#include <helpers/sensors/WisBlockSharedPowerPolicy.h>
#include <cassert>
class EnvironmentSensorManager { public: uint8_t detectWisBlockStorageHolds(); };
@METHOD@
int main() { EnvironmentSensorManager sensors; assert(sensors.detectWisBlockStorageHolds() == EXPECT_HOLD); }
'''.replace("@METHOD@", method)
        for defines in (["OTA_SD_STORE", "OTA_RAK15002_SD_CACHE", "EXPECT_HOLD=0"],
                        ["OTA_SD_STORE", "EXPECT_HOLD=mesh::gps::ExternalStorage"],
                        ["OTA_QSPI_SHARED_WISBLOCK_SPI", "EXPECT_HOLD=mesh::gps::ExternalStorage"],
                        ["EXPECT_HOLD=0"]):
            with self.subTest(defines=defines):
                build(self, harness, defines)

    def test_sd_cli_cold_context_oom_validation_and_card_actions(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static void formatSdCardElapsed(", "bool CommonCLI::handleSdCardSetCmd(",
            "bool CommonCLI::handleSdCardGetCmd("))
        fixture = (FIXTURES / "cli.cpp").read_text()
        build(self, fixture.replace("@METHODS@", methods), ["NRF52_PLATFORM", "OTA_SD_STORE"])


if __name__ == "__main__":
    unittest.main()
