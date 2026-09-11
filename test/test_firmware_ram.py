#!/usr/bin/env python3
"""Exercise real ELF parsing, per-platform admission and release RAM proof binding."""

import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_firmware_ram as ram
from firmware_elf import FirmwareElf
import firmware_memory_manifest as proof
from audit_esp32_image_ram import EspImage


def elf32(path, symbols, data=b"", address=0x3F400000):
    """Minimal actual ELF32, including absolute linker symbols and loaded data."""
    names = bytearray(b"\0")
    symtab = bytearray(16)
    for name, (value, size) in symbols.items():
        offset = len(names)
        names += name.encode() + b"\0"
        symtab += struct.pack("<IIIBBH", offset, value, size, 0x10, 0, 0xFFF1)
    section_offset = 52
    payload_offset = section_offset + 4 * 40
    string_offset = payload_offset + len(data)
    symbol_offset = string_offset + len(names)
    header = b"\x7fELF\x01\x01\x01" + bytes(9)
    header += struct.pack("<HH5I6H", 2, 40, 1, 0, 0, section_offset, 0,
                          52, 0, 0, 40, 4, 0)
    sections = bytes(40)
    sections += struct.pack("<10I", 0, 1, 2, address, payload_offset, len(data), 0, 0, 4, 0)
    sections += struct.pack("<10I", 0, 3, 0, 0, string_offset, len(names), 0, 0, 1, 0)
    sections += struct.pack("<10I", 0, 2, 0, 0, symbol_offset, len(symtab), 2, 1, 4, 16)
    Path(path).write_bytes(header + sections + data + names + symtab)
    return FirmwareElf(path)


def esp_fixture(path, modern=False, fragmented=False):
    address = 0x3F400000
    caps = [0x804, 0x804, 0x404, 0x803, 0x8804]
    data = bytearray(struct.pack("<I", len(caps)))
    symbols = {"soc_memory_region_count": (address, 4)}
    symbols["soc_memory_regions"] = (address + len(data), len(caps) * (20 if modern else 16))
    for index in range(len(caps)):
        data += struct.pack("<4I", 0x3FFB0000 + index * 0x10000, 0x10000, index, 0)
        if modern:
            data += bytes([int(index == 1), 0, 0, 0])
    symbols["soc_memory_types"] = (address + len(data), len(caps) * (16 if modern else 20))
    for index, cap in enumerate(caps):
        data += struct.pack("<4I", 0, cap, 0, 0)
        if not modern:
            data += bytes([0, int(index == 1), 0, 0])
    symbols["soc_reserved_memory_region_start"] = (address + len(data), 0)
    data += struct.pack("<2I", 0x3FFB0000, 0x3FFB4000)
    if fragmented:
        data += struct.pack("<2I", 0x3FFB8000, 0x3FFBC000)
    symbols["soc_reserved_memory_region_end"] = (address + len(data), 0)
    return elf32(path, symbols, data, address)


class FirmwareRamTest(unittest.TestCase):
    def test_heap_tables_and_ota_remain_in_runtime_budget(self):
        policy = ram.requirements("ESP32_PLATFORM", {
            "ENABLE_OTA": 1, "OTA_HEAP_CONTEXT": 1,
        }, "GEPRC_Linkflow_900_repeater")
        self.assertEqual(policy["components"]["client_table"], 32 * 320 + 16)
        self.assertEqual(policy["components"]["flood_filter_table"], 63 * 200 + 16)
        self.assertEqual(policy["components"]["ota_context"], 16384 + 16)
        self.assertGreaterEqual(policy["required_contiguous_bytes"], 16384 + 16)
        reduced = ram.requirements("STM32_PLATFORM", {
            "MAX_CLIENTS": 2, "FLOOD_PACKET_FILTER_SLOTS": 8,
        }, "wio_repeater")
        self.assertEqual(reduced["components"]["client_table"], 2 * 320 + 16)
        self.assertEqual(reduced["components"]["flood_filter_table"], 8 * 40 + 16)
        companion = ram.requirements("NRF52_PLATFORM", {}, "t114_companion_radio_ble")
        self.assertNotIn("client_table", companion["components"])
        self.assertNotIn("flood_filter_table", companion["components"])
        sensor = ram.requirements("NRF52_PLATFORM", {}, "t114_sensor")
        self.assertEqual(sensor["components"]["client_table"], 32 * 320 + 16)

    def test_browser_terminal_reserves_internal_session_and_psram_aware_scrollback(self):
        defines = {"ENABLE_USB_INTERFACE": 1, "WIFI_SSID": "", "DISPLAY_CLASS": "SSD1306Display"}
        base = ram.requirements("ESP32_PLATFORM", {**defines, "WEBCONFIG_DISABLED": 1}, "v4_companion")
        plain = ram.requirements("ESP32_PLATFORM", defines, "v4_companion")
        psram = ram.requirements("ESP32_PLATFORM", {**defines, "BOARD_HAS_PSRAM": 1}, "v4_companion")
        self.assertEqual(plain["required_heap_bytes"] - base["required_heap_bytes"], 6144)
        self.assertEqual(psram["required_heap_bytes"] - base["required_heap_bytes"], 2048)
        wifi_only = dict(defines)
        del wifi_only["ENABLE_USB_INTERFACE"]
        self.assertEqual(ram.requirements("ESP32_PLATFORM", wifi_only, "v4_companion"), plain)
        repeater = ram.requirements("ESP32_PLATFORM", defines, "v4_repeater")
        self.assertNotIn("browser_terminal_session", repeater["components"])
        for role in ("v4_repeater", "v4_room_server"):
            local = ram.requirements("ESP32_PLATFORM", {**defines, "ADMIN_PASSWORD": "test"}, role)
            self.assertEqual(local["components"]["browser_terminal_session"], 2048)
            self.assertEqual(local["components"]["browser_terminal_scrollback"], 4096)

    def test_longer_display_previews_reserve_heap_and_contiguous_history(self):
        defines = {"DISPLAY_CLASS": "SSD1306Display", "UI_SMALL_MESSAGE_FONT": 0}
        base = ram.requirements("ESP32_PLATFORM", defines, "v4_companion")
        expanded = ram.requirements("ESP32_PLATFORM", {
            **defines, "UI_MSG_PREVIEW_SIZE": 161,
        }, "v4_companion")
        self.assertEqual(expanded["required_heap_bytes"],
                         base["required_heap_bytes"] + 2816)
        self.assertGreaterEqual(expanded["required_contiguous_bytes"], 11008)
        # Shrinking the preview cannot reduce the existing safety budget.
        smaller = ram.requirements("ESP32_PLATFORM", {
            **defines, "UI_MSG_PREVIEW_SIZE": 32,
        }, "v4_companion")
        self.assertEqual(smaller, base)
        default = ram.requirements("ESP32_PLATFORM", {
            "DISPLAY_CLASS": "SSD1306Display",
        }, "v4_companion")
        self.assertEqual(default, expanded)

    def test_published_image_tables_match_elf_and_use_its_own_reservations(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference.elf"
            reference = esp_fixture(path)
            # Build a real ESP image containing the same allocated section;
            # the RTC guard starts the reservation section in the pinned SDK.
            section = reference.sections[1]
            data = reference.section_bytes(section)
            data = data.replace(struct.pack("<2I", 0x3FFB0000, 0x3FFB4000),
                                struct.pack("<2I", 0x3FF81FF0, 0x3FF82000)
                                + struct.pack("<2I", 0x3FFB0000, 0x3FFB4000)
                                + struct.pack("<2I", 0x3FFC0000, 0x3FFC1000))
            # Relocate the complete type table; name pointer words may move,
            # capability and startup flags must still match exactly.
            start = reference.address("soc_reserved_memory_region_start")
            original_read = reference.read
            reference.read = lambda address, size: (struct.pack("<2I", 0x3FF81FF0, 0x3FF82000)
                                                   if address == start and size == 8 else original_read(address, size))
            header = bytearray(24)
            header[0:2] = bytes([0xE9, 1])
            image_path = Path(temp) / "firmware.bin"
            image_path.write_bytes(header + struct.pack("<2I", section[3], len(data)) + data)
            image = EspImage(image_path)
            image.load_layout(reference)
            regions = ram.esp32_heap_regions(image, "esp32")
            self.assertEqual(regions, [(0x3FFB4000, 0x3FFC0000, 0), (0x3FFC1000, 0x3FFD0000, 1)])
            changed = bytearray(image_path.read_bytes())
            # Region table is immutable SDK data. A changed layout must require
            # another matching reference/rebuild, never silently be accepted.
            changed[32 + 8] ^= 1
            image_path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, "signature"):
                EspImage(image_path).load_layout(reference)

    def test_lazy_manual_stage_allocation_failure_and_clear(self):
        source = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        begin = source.index("#if defined(ESP32_PLATFORM) || (defined(NRF52_PLATFORM) && !defined(OTA_SEEDER_ONLY))")
        end = source.index("#endif", begin) + len("#endif")
        branch = source[begin:end]
        code = r'''
#include <cstdlib>
#include <cassert>
#include <cstdint>
bool allow = false;
int allocations = 0, releases = 0;
void* checked_malloc(size_t bytes) {
  assert(bytes == 4096);
  if (!allow) return nullptr;
  ++allocations;
  return std::malloc(bytes);
}
void checked_free(void* ptr) { if (ptr) ++releases; std::free(ptr); }
#define malloc checked_malloc
#define free checked_free
#define OTA_SERVE_BUF_SIZE 4096
struct Context {
@BRANCH@
};
int main() {
  Context context;
  assert(context.serve_buf == nullptr && allocations == 0);
  assert(!context.ensureServeBuffer() && context.serve_buf == nullptr);
  for (int i = 0; i < 64; ++i) {
    allow = true;
    assert(context.ensureServeBuffer());
    auto* original = context.serve_buf;
    assert(context.ensureServeBuffer() && context.serve_buf == original);
    context.serve_buf[4095] = 1;
    context.releaseServeBuffer();
    context.releaseServeBuffer();
    assert(context.serve_buf == nullptr && allocations == releases);
    allow = false;
    assert(!context.ensureServeBuffer());
  }
}
'''.replace("@BRANCH@", branch)
        with tempfile.TemporaryDirectory() as temp:
            for platform in ("NRF52_PLATFORM", "ESP32_PLATFORM"):
                binary = Path(temp) / platform
                subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-D" + platform,
                                "-fsanitize=address,undefined", "-fno-pie", "-no-pie", "-o", str(binary)],
                               input=code, text=True, check=True)
                subprocess.run([str(binary)], check=True)

    def test_t096_release_fails_and_exact_boundary_passes(self):
        flags = {"COMPANION_RADIO_FULL": 1, "DISPLAY_CLASS": "ST7735Display",
                 "BLE_PIN_CODE": 123456, "UI_SMALL_MESSAGE_FONT": 0}
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            path = Path(temp) / "firmware.elf"
            for available, accepted in ((54724, False), (73727, False), (73728, True), (74060, True)):
                with self.subTest(available=available):
                    elf32(path, {"__HeapBase": (0x2003F800 - available, 0), "__HeapLimit": (0x2003F800, 0)})
                    result = ram.check_firmware(path, "NRF52_PLATFORM", "nrf52840", flags,
                                                "Heltec_t096_companion_radio_full_femon")
                    self.assertEqual(result == 0, accepted)

    def test_arm_heap_excludes_stack_softdevice_and_mota_arena(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "firmware.elf"
            elf = elf32(path, {"__HeapBase": (0x20020000, 0), "__HeapLimit": (0x2002F800, 0),
                               "__mota_ram_start__": (0x20030000, 0), "__mota_ram_end__": (0x20040000, 0)})
            self.assertEqual(ram.heap_regions(elf, "NRF52_PLATFORM", "nrf52840"),
                             [(0x20020000, 0x2002F800, 0)])
            for platform, symbols in (
                ("RP2040_PLATFORM", {"__end__": (0x20010000, 0), "__HeapLimit": (0x20040000, 0)}),
                ("STM32_PLATFORM", {"_end": (0x20008000, 0), "_estack": (0x20010000, 0), "_Min_Stack_Size": (4096, 0)}),
            ):
                regions = ram.heap_regions(elf32(path, symbols), platform, "test")
                self.assertEqual(regions[-1][1], 0x20040000 if platform.startswith("RP") else 0x2000F000)
            for symbols in ({"__HeapBase": (0x20020000, 0)},
                            {"__HeapBase": (0x20020000, 0), "__HeapLimit": (0x20010000, 0)}):
                with self.assertRaises(ValueError):
                    ram.heap_regions(elf32(path, symbols), "NRF52_PLATFORM", "nrf52840")

    def test_idf4_idf5_ignore_psram_iram_rtc_and_reservations(self):
        with tempfile.TemporaryDirectory() as temp:
            for modern in (False, True):
                elf = esp_fixture(Path(temp) / "firmware.elf", modern)
                # Classic reclaims its ROM stack; other chips conservatively
                # exclude that region until the hardware ROM table is known.
                self.assertEqual(ram.esp32_heap_regions(elf, "esp32"),
                                 [(0x3FFB4000, 0x3FFC0000, 0), (0x3FFC0000, 0x3FFD0000, 1)])
                self.assertEqual(ram.esp32_heap_regions(elf, "esp32s3"),
                                 [(0x3FFB4000, 0x3FFC0000, 0)])

    def test_fragmented_heap_does_not_pass_large_allocation(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            path = Path(temp) / "firmware.elf"
            esp_fixture(path, modern=True, fragmented=True)
            report = Path(temp) / "report.json"
            ram.check_firmware(path, "ESP32_PLATFORM", "esp32s3", {}, "companion", report)
            result = json.loads(report.read_text())
            self.assertEqual(result["available_internal_bytes"], 32768)
            self.assertEqual(result["largest_internal_region_bytes"], 16384)
            self.assertFalse(result["passed"])
            with self.assertRaisesRegex(ValueError, "overlapping"):
                ram.subtract_regions([(0, 1024, 0), (512, 2048, 1)], [])

    def test_enabled_features_raise_budget_and_unknown_inputs_fail_closed(self):
        base = ram.requirements("ESP32_PLATFORM", {}, "companion")["required_heap_bytes"]
        flags = {"BLE_PIN_CODE": 1, "WIFI_SSID": "test", "WITH_MQTT_BRIDGE": 1}
        both = ram.requirements("ESP32_PLATFORM", flags, "companion")["required_heap_bytes"]
        self.assertEqual(both - base, 32768 + 49152 + 24576 + 6144)
        flags["COMPANION_EXCLUSIVE_WIFI_BLE"] = 1
        self.assertEqual(ram.requirements("ESP32_PLATFORM", flags, "companion")["required_heap_bytes"], both - 32768)
        self.assertGreater(ram.requirements("NRF52_PLATFORM", {"DISPLAY_CLASS": "ST7735Display"}, "companion")["required_heap_bytes"],
                           ram.requirements("NRF52_PLATFORM", {"DISPLAY_CLASS": "SSD1306Display"}, "companion")["required_heap_bytes"])
        self.assertEqual(ram.requirements("ESP32_PLATFORM", {"MESH_MIN_RUNTIME_HEAP": 1}, "companion")["required_heap_bytes"], base)
        for platform, definitions in (("NEW_PLATFORM", {}), ("NRF52_PLATFORM", {"DISPLAY_CLASS": "NewDisplay"}),
                                      ("NRF52_PLATFORM", {"MESH_NRF52_LOOP_STACK_WORDS": "invalid"})):
            with self.assertRaises(ValueError):
                ram.requirements(platform, definitions, "companion")

    def test_queue_sharing_applies_to_qualified_direct_full_builds_only(self):
        source = '#include "src/helpers/ota/OtaMemoryPolicy.h"\n#ifdef OTA_SHARED_COMPANION_QUEUE\nSHARING_ENABLED\n#endif\n'
        for flags, expected in (
            (["NRF52_PLATFORM", "COMPANION_RADIO_FULL", "OTA_SEEDER_ONLY"], True),
            (["NRF52_PLATFORM", "OTA_SEEDER_ONLY"], False),
            (["NRF52_PLATFORM", "COMPANION_RADIO_FULL"], False),
            (["ESP32_PLATFORM", "COMPANION_RADIO_FULL", "OTA_SEEDER_ONLY"], False),
            (["ESP32_PLATFORM", "HELTEC_WIRELESS_PAPER", "COMPANION_RADIO_FULL", "OTA_SEEDER_ONLY"], True),
            (["ESP32_PLATFORM", "HELTEC_WIRELESS_PAPER", "OTA_SEEDER_ONLY"], False),
            (["ESP32_PLATFORM", "HELTEC_WIRELESS_PAPER", "COMPANION_RADIO_FULL"], False),
        ):
            result = subprocess.run(["c++", "-x", "c++", "-E", "-I", str(ROOT),
                                     *("-D" + flag for flag in flags), "-"], input=source,
                                    text=True, capture_output=True, check=True)
            self.assertEqual("SHARING_ENABLED" in result.stdout, expected, flags)

    def test_affected_esp32_full_overlay_keeps_queue_and_both_transports(self):
        for target in ("Heltec_v3_companion_radio_full", "Xiao_C3_companion_radio_full",
                       "heltec_tracker_v2_companion_radio_full_femon",
                       "Heltec_Wireless_Paper_companion_radio_full"):
            result = subprocess.run(["bash", "-c", '''
source build.sh
PIO_ENV_PLATFORM_BY_NAME["$1"]=ESP32_PLATFORM
pio_env_option_contains() { return 0; }
requires_esp32_companion_full_ota_fallback() { return 1; }
apply_companion_radio_full_profile "$1" "$1"
printf '%s\\n' "$PLATFORMIO_BUILD_FLAGS"
''', "test", target], cwd=ROOT, text=True, capture_output=True, check=True)
            if target == "Heltec_Wireless_Paper_companion_radio_full":
                self.assertIn("-DMAX_CONTACTS=350", result.stdout)
                self.assertNotIn("-DMAX_CONTACTS=150", result.stdout)
                self.assertIn("-DOFFLINE_QUEUE_SIZE=256", result.stdout)
                self.assertIn("-DOTA_SHARED_COMPANION_QUEUE=1", result.stdout)
            else:
                self.assertIn("-DMAX_CONTACTS=150", result.stdout)
                self.assertNotIn("-DOFFLINE_QUEUE_SIZE=", result.stdout)
                self.assertNotIn("-DOTA_SHARED_COMPANION_QUEUE", result.stdout)
            self.assertIn("-DWIFI_OTA_SEEDER=1", result.stdout)
            self.assertIn("-DBLE_PIN_CODE=123456", result.stdout)

    def test_missing_and_truncated_elf_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "firmware.elf"
            for data in (b"not an ELF", b"\x7fELF\x02\x01\x01" + bytes(100),
                         b"\x7fELF\x01\x01\x01" + bytes(45)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    FirmwareElf(path)

    def test_stale_failed_and_changed_artifacts_cannot_resume_or_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            stem = directory / "companion-v1.17.1.5-test"
            (directory / "firmware.elf").write_bytes(b"linked image")
            report = dict(schema_version=1, passed=True, available_internal_bytes=80000,
                          required_heap_bytes=73728, largest_internal_region_bytes=80000,
                          required_contiguous_bytes=25602, elf_sha256=proof.digest(directory / "firmware.elf"),
                          target="pio_companion")
            (directory / "firmware.memory.json").write_text(json.dumps(report))
            manifest = Path(str(stem) + ".capabilities.json")
            manifest.write_text(json.dumps({"target": "companion", "artifact_target": "companion"}))
            image = Path(str(stem) + ".uf2")
            image.write_bytes(b"firmware package")
            proof.package_report(directory, stem)
            self.assertTrue(proof.validate_package(stem)["passed"])
            image.write_bytes(b"stale build")
            with self.assertRaisesRegex(ValueError, "changed"):
                proof.validate_package(stem)
            (directory / "firmware.elf").write_bytes(b"other build")
            with self.assertRaisesRegex(ValueError, "different ELF"):
                proof.validate_build(directory)
            report["passed"] = False
            (directory / "firmware.memory.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "failing"):
                proof.validate_build(directory)

    def test_every_resolved_firmware_environment_has_a_policy_and_hook(self):
        # PlatformIO is single-process. CI invokes this separately from builds.
        result = subprocess.run(["pio", "project", "config", "--json-output"], cwd=ROOT,
                                text=True, capture_output=True, check=True)
        checked = 0
        for name, options in json.loads(result.stdout):
            if not name.startswith("env:") or name.startswith("env:native"):
                continue
            options = dict(options)
            flags = " ".join(options.get("build_flags", []))
            platforms = [p for p in ram.SUPPORTED if p in flags]
            if not platforms:
                self.fail(f"{name}: no memory policy")
            self.assertEqual(len(platforms), 1, name)
            self.assertIn("post:scripts/check_firmware_ram.py", options.get("extra_scripts", []), name)
            checked += 1
        self.assertGreater(checked, 700)


if __name__ == "__main__":
    unittest.main()
