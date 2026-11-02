#!/usr/bin/env python3
"""Execute the pinned scan method with poisoned locals at its SDK boundary."""
import configparser
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/esp32_wifi_scan"
SPEC = importlib.util.spec_from_file_location("wifi_scan_fix", ROOT / "scripts/esp32_wifi_scan_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
RAW = (FIXTURES / "WiFiScan.cpp").read_text(encoding="utf-8")
PATCHED = FIX.patched_scan_source(RAW)
VERSION = ROOT / "test/fixtures/esp32_file_buffer/esp_arduino_version.h"


def scan_method(source):
    start = source.index("int16_t WiFiScanClass::scanNetworks(")
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError("unclosed scan method")


PREFIX = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "wifi_scan_config.h"
static constexpr int ESP_OK = 0;
static constexpr int WIFI_SCAN_RUNNING = -1, WIFI_SCAN_FAILED = -2;
static constexpr uint32_t WIFI_SCANNING_BIT = 1, WIFI_SCAN_DONE_BIT = 2;
static uint32_t status_bits = 0;
static unsigned enabled_calls = 0, deleted_calls = 0, sdk_calls = 0;
static wifi_scan_config_t captured;
static uint32_t millis() { return 42; }
struct WiFiGenericClass {
    static uint32_t getStatusBits() { return status_bits; }
    static void clearStatusBits(uint32_t bits) { status_bits &= ~bits; }
    static void setStatusBits(uint32_t bits) { status_bits |= bits; }
    static uint32_t waitStatusBits(uint32_t bits, uint32_t) { return status_bits & bits; }
};
struct WiFiMock { void enableSTA(bool enabled) { assert(enabled); ++enabled_calls; } } WiFi;
struct WiFiScanClass {
    static uint32_t _scanTimeout, _scanStarted;
    static bool _scanAsync;
    static uint16_t _scanCount;
    static void scanDelete() { ++deleted_calls; }
    int16_t scanNetworks(bool, bool, bool, uint32_t, uint8_t, const char*, const uint8_t*);
};
uint32_t WiFiScanClass::_scanTimeout = 0, WiFiScanClass::_scanStarted = 0;
bool WiFiScanClass::_scanAsync = false;
uint16_t WiFiScanClass::_scanCount = 0;
static int esp_wifi_scan_start(const wifi_scan_config_t* config, bool block) {
    assert(!block);
    captured = *config;
    ++sdk_calls;
    return ESP_OK;
}
// GCC 11 has no automatic-local poisoning flag. The negative control alone
// models a poisoned prior stack in that case; production initialization stays
// unchanged. All fields interpreted at the SDK boundary are then overwritten
// by the original method or consist of unsigned integers.
static wifi_scan_config_t poisonedScanConfig() {
    wifi_scan_config_t config;
    std::memset(&config, 0xa5, sizeof(config));
    return config;
}
'''

MAIN = r'''
int main() {
    WiFiScanClass scanner;
    const char ssid[] = "filter";
    const uint8_t bssid[] = {1, 2, 3, 4, 5, 6};
    for (bool passive : {false, true}) {
        for (uint32_t dwell : {120u, 200u, 300u}) {
            status_bits = 0;
            assert(scanner.scanNetworks(true, true, passive, dwell, 6, ssid, bssid)
                   == WIFI_SCAN_RUNNING);
            assert(captured.ssid == reinterpret_cast<const uint8_t*>(ssid));
            assert(captured.bssid == bssid && captured.channel == 6 && captured.show_hidden);
            assert(captured.scan_type == (passive ? WIFI_SCAN_TYPE_PASSIVE : WIFI_SCAN_TYPE_ACTIVE));
            assert(WiFiScanClass::_scanTimeout == dwell * 20 && WiFiScanClass::_scanStarted == 42);
            assert((status_bits & WIFI_SCANNING_BIT) && !(status_bits & WIFI_SCAN_DONE_BIT));
            if (captured.home_chan_dwell_time != 0
                || (passive && (captured.scan_time.active.min != 0 || captured.scan_time.active.max != 0))
                || (!passive && captured.scan_time.passive != 0)) {
                std::puts("scan defaults were not zeroed");
                return 1;
            }
            if (passive) assert(captured.scan_time.passive == dwell);
            else assert(captured.scan_time.active.min == 100 && captured.scan_time.active.max == dwell);
        }
    }
    assert(enabled_calls == 6 && deleted_calls == 6 && sdk_calls == 6);
    // An existing scan must return without starting or modifying another scan.
    assert(scanner.scanNetworks(true, false, false, 200, 0, nullptr, nullptr) == WIFI_SCAN_RUNNING);
    assert(sdk_calls == 6 && enabled_calls == 6 && deleted_calls == 6);
    status_bits = 0;
    assert(scanner.scanNetworks(true, false, false, 200, 0, nullptr, nullptr) == WIFI_SCAN_RUNNING);
    assert(!captured.ssid && !captured.bssid && captured.channel == 0 && !captured.show_hidden);
    std::puts("WiFi scan SDK configuration checks passed");
}
'''


class Node:
    def __init__(self, path): self.path = path
    def srcnode(self): return self
    def get_abspath(self): return str(self.path)


class Env(dict):
    def __init__(self, defines, build):
        super().__init__(CPPDEFINES=defines)
        self.build, self.paths, self.middleware = build, [], []
    def subst(self, value):
        assert value == "$BUILD_DIR"
        return str(self.build)
    def File(self, path): return path
    def AppendUnique(self, **kwargs):
        for path in kwargs["CPPPATH"]:
            if path not in self.paths: self.paths.append(path)
    def AddBuildMiddleware(self, callback, pattern): self.middleware.append((callback, pattern))


class WiFiScanFixTest(unittest.TestCase):
    def test_actual_scan_method_zeroes_unused_fields_with_poisoned_negative_control(self):
        compiler = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A host C++11 compiler is required")
        probe = subprocess.run([compiler, "-x", "c++", "-std=c++11",
                                "-ftrivial-auto-var-init=pattern", "-fsyntax-only", "-"],
                               input="int main() {}\n", capture_output=True, text=True, timeout=30)
        poison_flags = ["-ftrivial-auto-var-init=pattern"] if probe.returncode == 0 else []
        with tempfile.TemporaryDirectory(prefix="mesh-wifi-scan-sdk-") as directory:
            temp = Path(directory)
            shutil.copyfile(FIXTURES / "wifi_scan_config.h", temp / "wifi_scan_config.h")
            for source, expected in ((PATCHED, 0), (RAW, 1)):
                with self.subTest(patched=expected == 0):
                    method = scan_method(source)
                    if expected and not poison_flags:
                        method = method.replace(FIX.ORIGINAL_DECLARATION,
                                                "wifi_scan_config_t config = poisonedScanConfig();", 1)
                    generated = temp / "scan.cpp"
                    generated.write_text(PREFIX + "\n#include <initializer_list>\n"
                                         + method + MAIN, encoding="utf-8")
                    binary = temp / "scan.exe"
                    built = subprocess.run([compiler, "-std=c++11", "-O1", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-function", *poison_flags,
                        str(generated), "-o", str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, expected, checked.stdout + checked.stderr)
                    self.assertIn("WiFi scan SDK configuration checks passed" if expected == 0
                                  else "scan defaults were not zeroed", checked.stdout)

    def test_exact_source_pin_transform_idempotence_and_changed_source_rejection(self):
        self.assertEqual(hashlib.sha256((FIXTURES / "WiFiScan.cpp").read_bytes()).hexdigest(),
                         FIX.PINNED_SCAN_SHA256)
        self.assertEqual(hashlib.sha256((FIXTURES / "wifi_scan_config.h").read_bytes()).hexdigest(),
                         "9b25412ea07a0a8fe295d02c9f0ddaee2bc984397483d5d56db7e54b5aacf2ce")
        self.assertEqual(PATCHED, RAW.replace(FIX.ORIGINAL_DECLARATION, FIX.ZEROED_DECLARATION, 1))
        self.assertEqual(FIX.patched_scan_source(RAW.replace("\n", "\r\n")), PATCHED)
        self.assertEqual(FIX.patched_scan_source(PATCHED), PATCHED)
        for changed in (RAW + "\n", PATCHED + "\n", RAW.replace("active.min = 100", "active.min = 50")):
            with self.assertRaises(RuntimeError): FIX.patched_scan_source(changed)
        for version in ((2, 0, 18), (3, 1, 3), (3, 3, 11), (3, 3, 12)):
            with self.assertRaises(RuntimeError): FIX.patched_scan_source(RAW, version)

    def test_private_copy_preserves_shared_framework_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="mesh-wifi-scan-copy-") as directory:
            temp = Path(directory)
            source = temp / "sdk/libraries/WiFi/src/WiFiScan.cpp"
            source.parent.mkdir(parents=True)
            source.write_bytes((FIXTURES / "WiFiScan.cpp").read_bytes())
            header = temp / "sdk/cores/esp32/esp_arduino_version.h"
            header.parent.mkdir(parents=True)
            header.write_bytes(VERSION.read_bytes())
            node = Node(source)
            for defines in ([], ["RP2040_PLATFORM"], [("ESP32_PLATFORM", 0)]):
                env = Env(defines, temp / "untouched")
                self.assertIs(FIX.replace_scan_source(env, node), node)
                self.assertFalse(env.build.exists())
            for index, defines in enumerate((["ESP32_PLATFORM"], [("ESP32", 1)],
                                            ["ESP32_PLATFORM", ("ARDUINO_USB_MODE", 0)],
                                            ["ESP32_PLATFORM", ("ARDUINO_USB_MODE", 1)])):
                env = Env(defines, temp / ("build" + str(index)))
                result = Path(FIX.replace_scan_source(env, node))
                self.assertEqual(result.read_text(encoding="utf-8"), PATCHED)
                self.assertEqual(result, env.build / "patched-esp32-wifi/WiFiScan.cpp")
                self.assertEqual(env.paths, [str(source.parent)])
                self.assertEqual(source.read_bytes(), (FIXTURES / "WiFiScan.cpp").read_bytes())
                self.assertEqual(header.read_bytes(), VERSION.read_bytes())
                before = result.stat().st_mtime_ns
                self.assertEqual(Path(FIX.replace_scan_source(env, node)).stat().st_mtime_ns, before)
            env = Env(["ESP32"], temp / "registration")
            unrelated = Node(temp / "libraries/WiFi/src/Other.cpp")
            self.assertIs(FIX.replace_scan_source(env, unrelated), unrelated)
            FIX.install(env)
            self.assertEqual(env.middleware,
                             [(FIX.replace_scan_source, "*libraries*WiFi*src*WiFiScan.cpp")])

    def test_known_arduino3_is_unchanged_unknown_or_malformed_framework_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="mesh-wifi-scan-version-") as directory:
            temp = Path(directory)
            source = temp / "sdk/libraries/WiFi/src/WiFiScan.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("Arduino 3 source is outside this narrow patch", encoding="utf-8")
            header = temp / "sdk/cores/esp32/esp_arduino_version.h"
            header.parent.mkdir(parents=True)
            env, node = Env(["ESP32_PLATFORM"], temp / "build"), Node(source)
            def version_text(version):
                return "".join(f"#define ESP_ARDUINO_VERSION_{key} {value}\n"
                               for key, value in zip(("MAJOR", "MINOR", "PATCH"), version))
            for version in ((3, 1, 3), (3, 3, 11), (3, 3, 12)):
                header.write_text(version_text(version))
                self.assertEqual(FIX.require_known_framework(source), version)
                self.assertIs(FIX.replace_scan_source(env, node), node)
                self.assertFalse(env.build.exists())
            for changed in ("", version_text((2, 0, 18)), version_text((3, 3, 13)),
                            version_text((2, 0, 17)) + "#define ESP_ARDUINO_VERSION_MAJOR 2\n"):
                header.write_text(changed)
                with self.assertRaises(RuntimeError): FIX.replace_scan_source(env, node)
                self.assertFalse(env.build.exists())
            header.unlink()
            with self.assertRaises(RuntimeError): FIX.replace_scan_source(env, node)
            header.write_bytes(VERSION.read_bytes())
            with self.assertRaises(RuntimeError): FIX.replace_scan_source(env, node)
            self.assertFalse(env.build.exists())

    def test_common_esp32_registration(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "platformio.ini")
        self.assertIn("pre:scripts/esp32_wifi_scan_fix.py", config["esp32_base"]["extra_scripts"].split())


if __name__ == "__main__":
    unittest.main()
