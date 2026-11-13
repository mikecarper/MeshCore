#!/usr/bin/env python3
"""Exercise real ELF parsing, per-platform admission and release RAM proof binding."""

import contextlib
import io
import json
from pathlib import Path
import re
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
from test_replay_reset_integration import extract_braced


def elf32(path, symbols, data=b"", address=0x3F400000, *, symbol_sections=None, writable=False,
          symbol_types=None, duplicate=None):
    """Minimal actual ELF32, including absolute linker symbols and loaded data."""
    names = bytearray(b"\0")
    symtab = bytearray(16)
    for name, (value, size) in symbols.items():
        offset = len(names)
        names += name.encode() + b"\0"
        section = (symbol_sections or {}).get(name, 0xFFF1)
        info = 0x10 | (symbol_types or {}).get(name, 0)
        record = struct.pack("<IIIBBH", offset, value, size, info, 0, section)
        symtab += record
        if name == duplicate:
            symtab += record
    section_offset = 52
    payload_offset = section_offset + 4 * 40
    string_offset = payload_offset + len(data)
    symbol_offset = string_offset + len(names)
    header = b"\x7fELF\x01\x01\x01" + bytes(9)
    header += struct.pack("<HH5I6H", 2, 40, 1, 0, 0, section_offset, 0,
                          52, 0, 0, 40, 4, 0)
    sections = bytes(40)
    sections += struct.pack("<10I", 0, 1, 3 if writable else 2, address, payload_offset, len(data), 0, 0, 4, 0)
    sections += struct.pack("<10I", 0, 3, 0, 0, string_offset, len(names), 0, 0, 1, 0)
    sections += struct.pack("<10I", 0, 2, 0, 0, symbol_offset, len(symtab), 2, 1, 4, 16)
    Path(path).write_bytes(header + sections + data + names + symtab)
    return FirmwareElf(path)


def esp_fixture(path, modern=False, fragmented=False, internal_length=0x10000):
    address = 0x3F400000
    caps = [0x804, 0x804, 0x404, 0x803, 0x8804]
    data = bytearray(struct.pack("<I", len(caps)))
    symbols = {"soc_memory_region_count": (address, 4)}
    symbols["soc_memory_regions"] = (address + len(data), len(caps) * (20 if modern else 16))
    for index in range(len(caps)):
        length = internal_length if index == 0 else 0x10000
        data += struct.pack("<4I", 0x3FFB0000 + index * 0x10000, length, index, 0)
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
    def test_scanner_pool_reservation_is_role_and_feature_scoped(self):
        flags = {"MESH_W12_PRAM_EXPERIMENT": 1}
        for platform, board in (("ESP32_PLATFORM", "meshnology_w12"),
                                ("NRF52_PLATFORM", "MeshTracker_X1")):
            for role in ("repeater", "repeater_observer_mqtt", "room_server", "sensor"):
                target = board + "_" + role
                with self.subTest(platform=platform, role=role):
                    active = ram.requirements(platform, flags, target)
                    legacy = ram.requirements(platform, {
                        **flags, "MESH_W12_PRAM_EXPERIMENT": 0,
                    }, target)
                    self.assertEqual(active["components"]["radio_packet_pool"], 12800)
                    self.assertEqual(legacy["components"]["radio_packet_pool"], 10240)
                    self.assertEqual(active["required_heap_bytes"] - legacy["required_heap_bytes"], 2560)
                    self.assertEqual(active["components"]["allocation_and_transient_margin"],
                                     legacy["components"]["allocation_and_transient_margin"])
        for platform, flags, target, expected in (
            ("ESP32_PLATFORM", {"MESH_W12_PRAM_EXPERIMENT": 1}, "other_repeater", 12800),
            ("ESP32_PLATFORM", {"MESHNOLOGY_W12": 1}, "meshnology_w12_repeater", 10240),
            ("NRF52_PLATFORM", {"MESHNOLOGY_W12": 1, "MESH_W12_PRAM_EXPERIMENT": 1},
             "meshnology_w12_repeater", 12800),
            ("ESP32_PLATFORM", {"MESHNOLOGY_W12": 1, "MESH_W12_PRAM_EXPERIMENT": 1},
             "meshnology_w12_companion_radio_full", 6400),
            ("NRF52_PLATFORM", {"MESH_W12_PRAM_EXPERIMENT": 1},
             "MeshTracker_X1_companion_radio_ble", 6400),
            ("NRF52_PLATFORM", {}, "MeshTracker_X1_companion_radio_ble", 5120),
        ):
            with self.subTest(platform=platform, target=target):
                self.assertEqual(ram.requirements(platform, flags, target)["components"]
                                 ["radio_packet_pool"], expected)

    def test_w12_scanner_pool_bounds_real_packet_and_psram_fallbacks(self):
        # Packet contains only fixed-width fields and is the same size on the
        # tested host and ESP32-S3; its ceiling leaves 16 bytes per allocation
        # for all 46 allocations in the worst-case observer pool.
        program = r'''
#include <Packet.h>
static_assert(sizeof(mesh::Packet) <= 328, "Update the W12 packet pool budget");
int main() { return 0; }
'''
        with tempfile.TemporaryDirectory() as temp:
            cpp = Path(temp) / "packet.cpp"
            cpp.write_text(program)
            subprocess.run(["g++", "-std=c++17", "-fsyntax-only", "-I", str(ROOT / "src"),
                            "-DMESH_W12_PRAM_EXPERIMENT=1", "-DMESH_ENABLE_LR2021_AUTO_XTRIM=1",
                            "-DMESH_W12_PRAM_SEQ_TX_LAB=1",
                            "-DWITH_UDP_BRIDGE=1", str(cpp)], check=True, timeout=30)
        self.assertLessEqual(32 * 328 + 4 * 32 * (4 + 1 + 4) + 32 * 8 + 124 + 46 * 16, 12800)
        self.assertLessEqual(16 * 328 + 3 * 16 * (4 + 1 + 4) + 124 + 26 * 16, 6400)
        self.assertIn("new StaticPoolPacketManager(16)",
                      (ROOT / "examples/companion_radio/MyMesh.cpp").read_text())
        for path, constructor in (
            ("examples/simple_repeater/MyMesh.cpp", "createObserverPacketManager(32)"),
            ("examples/simple_room_server/MyMesh.cpp", "createObserverPacketManager(32)"),
            ("examples/simple_sensor/SensorMesh.cpp", "StaticPoolPacketManager(32)"),
        ):
            self.assertIn(constructor, (ROOT / path).read_text())
        flags = {"MESHNOLOGY_W12": 1, "MESH_W12_PRAM_EXPERIMENT": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1, "BOARD_HAS_PSRAM": 1,
                 "CONFIG_IDF_TARGET_ESP32S3": 1, "CONFIG_SPIRAM_USE_MALLOC": 1}
        target = "meshnology_w12_repeater_observer_mqtt"
        fallback = ram.requirements("ESP32_PLATFORM", flags, target)
        required = ram.requirements("ESP32_PLATFORM", {**flags, "MESH_REPEATER_REQUIRE_PSRAM": 1}, target)
        self.assertEqual(fallback["components"]["radio_packet_pool"], 12800)
        self.assertEqual(required["components"]["radio_packet_pool"], 12800)
        for fallback_key, external_key in (("repeater_scope_storage_fallback", "repeater_scope_storage"),
                                            ("repeater_advert_storage_fallback", "repeater_advert_storage")):
            self.assertGreater(fallback["components"][fallback_key], 0)
            self.assertNotIn(fallback_key, required["components"])
            self.assertEqual(fallback["components"][fallback_key],
                             required["external_memory"]["components"][external_key])
        self.assertFalse(required["external_memory"]["internal_fallback"])
        self.assertTrue(required["external_memory"]["enforced_before_radio_startup"])

    def test_w12_scanner_pool_build_guard_rejects_oversized_packet(self):
        source = (ROOT / "src/helpers/StaticPoolPacketManager.cpp").read_text()
        assertion = source.index("static_assert(sizeof(mesh::Packet) <= 328")
        start = source.rfind("#if", 0, assertion)
        end = source.index("#endif", assertion) + len("#endif")
        guard = source[start:end]
        flags = ["ESP32_PLATFORM=1", "MESHNOLOGY_W12=1", "MESH_W12_PRAM_EXPERIMENT=1"]
        with tempfile.TemporaryDirectory() as temp:
            probe = Path(temp) / "packet_guard.cpp"
            probe.write_text("namespace mesh { struct Packet { char bytes[329]; }; }\n" + guard + "\n")
            def compile_probe(defines):
                return subprocess.run(["g++", "-std=c++17", "-fsyntax-only",
                                       *["-D" + value for value in defines], str(probe)],
                                      text=True, capture_output=True, timeout=30)
            self.assertNotEqual(compile_probe(flags).returncode, 0)
            self.assertNotEqual(compile_probe(["NRF52_PLATFORM=1", flags[2]]).returncode, 0)
            self.assertNotEqual(compile_probe([flags[2]]).returncode, 0)
            for other in (flags[:-1], ["NRF52_PLATFORM=1"],
                          [flags[0], "MESH_W12_PRAM_EXPERIMENT=0"]):
                with self.subTest(defines=other):
                    self.assertEqual(compile_probe(other).returncode, 0)
            probe.write_text('#include <Packet.h>\n' + guard + '\n')
            checked = subprocess.run(["g++", "-std=c++17", "-fsyntax-only",
                                      "-I", str(ROOT / "src"),
                                      *["-D" + value for value in flags],
                                      "-DMESH_ENABLE_LR2021_AUTO_XTRIM=1", "-DWITH_UDP_BRIDGE=1",
                                      str(probe)], text=True, capture_output=True, timeout=30)
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_explicit_esp32_contact_cache_adds_reader_allowance(self):
        flags = {"BLE_PIN_CODE": 123456, "DISPLAY_CLASS": "SSD1306Display",
                 "MAX_CONTACTS": 160, "MAX_GROUP_CHANNELS": 8,
                 "OFFLINE_QUEUE_SIZE": 128}
        target = "Heltec_v2_companion_radio_ble"
        for extra in ({}, {"BOARD_HAS_PSRAM": 1}, {"COMPANION_RADIO_FULL": 1}):
            with self.subTest(extra=extra):
                original = ram.requirements("ESP32_PLATFORM", {**flags, **extra}, target)
                disabled = ram.requirements("ESP32_PLATFORM", {
                    **flags, **extra, "MESH_CONTACT_CACHE": 0,
                }, target)
                enabled = ram.requirements("ESP32_PLATFORM", {
                    **flags, **extra, "MESH_CONTACT_CACHE": 1,
                }, target)
                self.assertEqual(original, disabled)
                self.assertNotIn("contact_cache_reader", original["components"])
                self.assertEqual(enabled["components"]["contact_cache_reader"], 6144)
                self.assertEqual(enabled["required_heap_bytes"],
                                 original["required_heap_bytes"] + 6144)
                for name, expected in (("core_tasks_usb_filesystems", 24576),
                                       ("allocation_and_transient_margin", 16384)):
                    self.assertEqual(original["components"][name], expected)
                    self.assertEqual(enabled["components"][name], expected)
                self.assertEqual({key: value for key, value in enabled["components"].items()
                                  if key != "contact_cache_reader"}, original["components"])

    def test_explicit_contact_cache_does_not_change_other_platform_budgets(self):
        for platform in ("NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
            with self.subTest(platform=platform):
                original = ram.requirements(platform, {}, "board_companion_radio_ble")
                enabled = ram.requirements(platform, {"MESH_CONTACT_CACHE": 1},
                                           "board_companion_radio_ble")
                self.assertEqual(enabled, original)

    def test_explicit_contact_cache_elf_rejects_old_capacity_and_enforces_boundary(self):
        flags = {"BLE_PIN_CODE": 123456, "DISPLAY_CLASS": "SSD1306Display",
                 "MAX_CONTACTS": 160, "MAX_GROUP_CHANNELS": 8,
                 "OFFLINE_QUEUE_SIZE": 128}
        target = "Heltec_v2_companion_radio_ble"
        original = ram.requirements("ESP32_PLATFORM", flags, target)
        cached = {**flags, "MESH_CONTACT_CACHE": 1}
        required = ram.requirements("ESP32_PLATFORM", cached, target)["required_heap_bytes"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.elf"
            report = Path(temporary) / "firmware.memory.json"
            for capacity, accepted in ((original["required_heap_bytes"], False),
                                       (required - 1, False), (required, True)):
                with self.subTest(capacity=capacity), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    # Classic ESP32 also reclaims the fixture's 64 KiB ROM-stack
                    # region; its first region excludes a 16 KiB reservation.
                    esp_fixture(path, internal_length=capacity - 0x10000 + 0x4000)
                    self.assertEqual(ram.check_firmware(path, "ESP32_PLATFORM", "esp32",
                                                       flags, target) == 0, True)
                    status = ram.check_firmware(path, "ESP32_PLATFORM", "esp32",
                                                cached, target, report)
                    result = json.loads(report.read_text())
                    self.assertEqual(status == 0, accepted)
                    self.assertEqual(result["passed"], accepted)
                    self.assertEqual(result["available_internal_bytes"], capacity)
                    self.assertEqual(result["required_heap_bytes"], required)
                    self.assertEqual(result["components"]["contact_cache_reader"], 6144)

    def test_pocket_gui_reserves_expanded_history_without_changing_other_boards(self):
        flags = {"DISPLAY_CLASS": "GxEPDDisplay", "HELTEC_MESH_POCKET": 1,
                 "COMPANION_RADIO_FULL": 1, "UI_MSG_PREVIEW_SIZE": 161}
        target = "Mesh_pocket_companion_radio_full"
        enabled = ram.requirements("NRF52_PLATFORM", flags, target)
        self.assertEqual(enabled["components"]["screen_objects_and_history"], 12288)
        self.assertNotIn("expanded_message_previews", enabled["components"])
        self.assertGreaterEqual(enabled["required_contiguous_bytes"], 12288)

        for changed in ({**flags, "UI_POCKET_GUI": 0},
                        {key: value for key, value in flags.items()
                         if key != "HELTEC_MESH_POCKET"}):
            policy = ram.requirements("NRF52_PLATFORM", changed, target)
            self.assertEqual(policy["components"]["screen_objects_and_history"], 8192)
            self.assertEqual(policy["components"]["expanded_message_previews"], 2816)
        larger = ram.requirements("NRF52_PLATFORM", {
            **flags, "MESH_COMPANION_SCREEN_STARTUP_BYTES": 16384}, target)
        self.assertEqual(larger["components"]["screen_objects_and_history"], 16384)

    def test_st7789_budget_matches_production_allocation_peak_and_reuses_buffers(self):
        ui = ROOT / "src/helpers/ui"
        spi = (ui / "ST7789Spi.h").read_text()
        oled = (ui / "OLEDDisplay.cpp").read_text()
        header = (ui / "ST7789Display.h").read_text()
        start = header.index("#if defined(HELTEC_VISION_MASTER_T190)")
        constructors = header[start:header.index("#endif", start) + len("#endif")]
        buffering = (ui / "OLEDDisplay.h").read_text()
        start = buffering.index("#ifndef OLEDDISPLAY_REDUCE_MEMORY")
        buffering = buffering[start:buffering.index("#endif", start) + len("#endif")]
        fixture = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <utility>
#include <vector>
using std::min; using std::max;
size_t live=0, peak=0, largest=0, calls=0;
std::vector<std::pair<void*,size_t>> allocations;
void* track_malloc(size_t size) {
  void* p=std::malloc(size); assert(p); allocations.push_back({p,size});
  live+=size; peak=max(peak,live); largest=max(largest,size); ++calls; return p;
}
void track_free(void* p) {
  auto at=std::find_if(allocations.begin(),allocations.end(),[&](auto& a){return a.first==p;});
  assert(at!=allocations.end()); live-=at->second; allocations.erase(at); std::free(p);
}
#define malloc track_malloc
#define free track_free
#define rtos_malloc track_malloc
#define rtos_free track_free
#define DEBUG_OLEDDISPLAY(...)
#define LOW 0
#define HIGH 1
void yield() {}
enum OLEDDISPLAY_GEOMETRY { GEOMETRY_128_128,GEOMETRY_128_64,GEOMETRY_128_32,
                          GEOMETRY_64_48,GEOMETRY_64_32,GEOMETRY_RAWMODE };
@BUFFERING@
struct Bus {
  unsigned rows=0;
  template<class T> void beginTransaction(T) {}
  void endTransaction() {}
  void transfer(void*,void*,int) { ++rows; }
};
struct Panel {
  uint16_t displayWidth=0,displayHeight=0,displayBufferSize=0,_buffheight=0,_RGB=0xffff;
  uint16_t _lastRGB=0xffff;
  OLEDDISPLAY_GEOMETRY geometry=GEOMETRY_RAWMODE;
  unsigned logBufferSize=0,logBufferFilled=0,logBufferLine=0,logBufferMaxLines=0;
  uint8_t* buffer=nullptr; uint8_t* buffer_back=nullptr; uint8_t* logBuffer=nullptr;
  unsigned BufferOffset=0,_spiSettings=0;
  Bus bus; Bus* _spi=&bus;
  Panel(int*,uint8_t,uint8_t,uint8_t,OLEDDISPLAY_GEOMETRY g,uint16_t w,uint16_t h,
        int=-1,int=-1,int=-1) { setGeometry(g,w,h); }
  bool connect() { _buffheight=(displayHeight+7)/8; return true; }
  void set_CS(int) {}
  void setAddrWindow(uint16_t,uint16_t,uint16_t,uint16_t) {}
@GEOMETRY@
@ALLOCATE@
@FLUSH@
};
int SPI=0,SPI1=0;
constexpr uint8_t PIN_TFT_RST=2,PIN_TFT_DC=12,PIN_TFT_CS=11,PIN_TFT_SDA=41,PIN_TFT_SCL=40;
constexpr uint8_t ST7789_RESET=14,ST7789_RS=15,ST7789_CS=16,ST7789_SDA=13,ST7789_MISO=14,ST7789_SCK=12;
struct DisplayDriver { DisplayDriver(int,int) {} };
struct ST7789Display:DisplayDriver {
  Panel display; bool _isOn=false;
@CONSTRUCTORS@
};
int main() {
  ST7789Display d; Panel& p=d.display;
  assert(p.allocateBuffer());
  std::fill(p.buffer,p.buffer+p.displayBufferSize,0xff);
#ifdef OLEDDISPLAY_DOUBLE_BUFFER
  std::fill(p.buffer_back,p.buffer_back+p.displayBufferSize,0);
#endif
  p.display(); assert(p.bus.rows==p.displayHeight);
  const auto count=calls, persistent=live;
  assert(p.allocateBuffer()); assert(calls==count && live==persistent);
  std::printf("%zu %zu %zu\n",live,peak,largest);
  track_free(p.buffer);
#ifdef OLEDDISPLAY_DOUBLE_BUFFER
  track_free(p.buffer_back);
#endif
  assert(live==0);
}
'''
        for marker, value in {
                "@BUFFERING@": buffering, "@CONSTRUCTORS@": constructors,
                "@GEOMETRY@": extract_braced(spi, "void setGeometry("),
                "@ALLOCATE@": extract_braced(oled, "bool OLEDDisplay::allocateBuffer(").replace(
                    "OLEDDisplay::", ""),
                "@FLUSH@": extract_braced(spi, "void display(void)")}.items():
            fixture = fixture.replace(marker, value)
        with tempfile.TemporaryDirectory(prefix="st7789-ram-") as temporary:
            for board in ({"HELTEC_T114": 1, "T114_DISPLAY_AUTODETECT": 1},
                          {"HELTEC_VISION_MASTER_T190": 1}, {"THINKNODE_M9": 1}):
                for buffers in ({}, {"OLEDDISPLAY_REDUCE_MEMORY": 0},
                                {"OLEDDISPLAY_REDUCE_MEMORY": 1, "OLEDDISPLAY_DOUBLE_BUFFER": 0}):
                    defines = {**board, **buffers, "DISPLAY_CLASS": "ST7789Display"}
                    with self.subTest(defines=defines):
                        binary = Path(temporary) / "allocations"
                        compiled = subprocess.run([
                            "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-x", "c++", "-",
                            *[f"-D{k}={v}" for k,v in {**board, **buffers}.items()],
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                            "-o", str(binary)], input=fixture, text=True, capture_output=True)
                        self.assertEqual(compiled.returncode, 0, compiled.stderr)
                        checked = subprocess.run([str(binary)], text=True, capture_output=True, timeout=15)
                        self.assertEqual(checked.returncode, 0, checked.stderr)
                        persistent, peak, largest = map(int, checked.stdout.split())
                        policy = ram.requirements("NRF52_PLATFORM", defines, "display_companion")
                        self.assertEqual(policy["components"]["display_pixels_and_driver"], peak)
                        self.assertGreater(peak, persistent)  # Scanline is heap, freed after each transfer.
                        self.assertGreaterEqual(policy["required_contiguous_bytes"], largest)

    def test_t114_present_panel_is_budgeted_for_auto_and_legacy_with_exact_admission(self):
        flags = {"DISPLAY_CLASS": "ST7789Display", "HELTEC_T114": 1,
                 "T114_DISPLAY_AUTODETECT": 1, "COMPANION_RADIO_FULL": 1, "BLE_PIN_CODE": 123456}
        target = "Heltec_t114_companion_radio_full"
        policy = ram.requirements("NRF52_PLATFORM", flags, target)
        self.assertEqual(policy["components"]["display_pixels_and_driver"], 8640)
        self.assertEqual(policy["components"]["allocation_and_transient_margin"], 4096)
        legacy = {key:value for key,value in flags.items() if key != "T114_DISPLAY_AUTODETECT"}
        self.assertEqual(ram.requirements("NRF52_PLATFORM", legacy, target), policy)
        required = policy["required_heap_bytes"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "firmware.elf"
            for capacity, accepted in ((required-8640, False), (required-1, False), (required, True)):
                with self.subTest(capacity=capacity), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    elf32(path, {"__HeapBase": (0x2003F800-capacity,0), "__HeapLimit": (0x2003F800,0)})
                    self.assertEqual(ram.check_firmware(path,"NRF52_PLATFORM","nrf52840",flags,target)==0, accepted)

    def test_wio_l1_unified_display_keeps_oled_heap_allowance_on_every_boot(self):
        flags = {"DISPLAY_CLASS": "WioL1Display", "WIO_L1_DISPLAY_UNIFIED": 1,
                 "UI_SMALL_MESSAGE_FONT": 1, "COMPANION_RADIO_FULL": 1, "BLE_PIN_CODE": 123456}
        target = "WioTrackerL1_companion_radio_full"
        policy = ram.requirements("NRF52_PLATFORM", flags, target)
        oled = ram.requirements("NRF52_PLATFORM", {**flags, "DISPLAY_CLASS": "SH1106Display"}, target)
        self.assertEqual(policy["components"], oled["components"])
        self.assertEqual(policy["components"]["display_pixels_and_driver"], 4096)
        self.assertEqual(policy["components"]["allocation_and_transient_margin"], 4096)
        for platform, changed in (("ESP32_PLATFORM", flags),
                                  ("NRF52_PLATFORM", {**flags, "WIO_L1_DISPLAY_UNIFIED": 0})):
            with self.subTest(platform=platform, flags=changed), self.assertRaises(ValueError):
                ram.requirements(platform, changed, target)

    def test_device_encoder_budget_matches_storage_and_role_gates(self):
        matrix = [
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1}, "v4_repeater", True),
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1}, "v4_room_server", True),
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1}, "v4_sensor", True),
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1}, "v4_companion_alias", True),
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1}, "v4_repeater", False),
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1}, "v4_companion_radio_usb", False),
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1, "OTA_SEEDER_ONLY": 1}, "v4_repeater", False),
            ("ESP32_PLATFORM", {"ENABLE_OTA": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 0}, "v4_repeater", False),
            ("ESP32_PLATFORM", {}, "v4_repeater", False),
            ("NRF52_PLATFORM", {"ENABLE_OTA": 1, "OTA_FLASH_STORE": 1}, "rak3401_repeater", False),
            ("NRF52_PLATFORM", {"ENABLE_OTA": 1, "OTA_QSPI_STORE": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1}, "xiao_repeater", True),
            ("NRF52_PLATFORM", {"ENABLE_OTA": 1, "OTA_SD_STORE": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1}, "tower_room_server", True),
            ("NRF52_PLATFORM", {"ENABLE_OTA": 1, "OTA_RAK_AUTO_STORE": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1}, "rak4631_sensor", True),
            ("NRF52_PLATFORM", {"ENABLE_OTA": 1, "OTA_QSPI_STORE": 1}, "xiao_companion_radio_full", False),
            ("STM32_PLATFORM", {"ENABLE_OTA": 1, "OTA_SD_STORE": 1}, "wio_repeater", False),
        ]
        for platform, defines, target, enabled in matrix:
            with self.subTest(platform=platform, defines=defines, target=target):
                components = ram.requirements(platform, defines, target)["components"]
                self.assertEqual("ota_encoder_workspace" in components, enabled)
                if enabled:
                    self.assertEqual(components["ota_encoder_workspace"], 1024 + 16)
                self_source = (platform == "NRF52_PLATFORM" and "ENABLE_OTA" in defines
                               and "OTA_SEEDER_ONLY" not in defines and any(key in defines for key in (
                                   "OTA_QSPI_STORE", "OTA_SD_STORE", "OTA_RAK_AUTO_STORE", "OTA_TOWER_AUTO_STORE")))
                self.assertEqual("ota_self_source_scratch" in components, self_source)
                if self_source:
                    self.assertEqual(components["ota_self_source_scratch"], 4096 + 32)

    def test_device_encoder_hash_override_is_budgeted_and_bounded(self):
        for bits in (7, 8, 9, 10):
            with self.subTest(bits=bits):
                policy = ram.requirements("NRF52_PLATFORM", {
                    "ENABLE_OTA": 1, "OTA_QSPI_STORE": 1,
                    "MESHCORE_OTA_DEVICE_DEFLATE": 1,
                    "MESHCORE_OTA_DEFLATE_HASH_BITS": bits,
                }, "xiao_repeater")
                self.assertEqual(policy["components"]["ota_encoder_workspace"],
                                 (1 << bits) * 2 + 16)
        for bits in (6, 11):
            with self.subTest(bits=bits), self.assertRaisesRegex(ValueError, "DEFLATE hash"):
                ram.requirements("ESP32_PLATFORM", {
                    "ENABLE_OTA": 1, "MESHCORE_OTA_DEFLATE_HASH_BITS": bits,
                    "MESHCORE_OTA_DEVICE_DEFLATE": 1,
                }, "v4_repeater")

    def test_sh1107_framebuffer_is_budgeted(self):
        policy = ram.requirements("NRF52_PLATFORM", {
            "DISPLAY_CLASS": "SH1107Display",
        }, "muzi_base_uno_superIO_companion_radio_ble")
        self.assertEqual(policy["components"]["display_pixels_and_driver"], 4096)

    def test_t096_legacy_ble_rgb332_fits_without_reducing_other_reserves(self):
        flags = {"DISPLAY_CLASS": "ST7735Display", "BLE_PIN_CODE": 123456,
                 "ENABLE_OTA": 1}
        for suffix in ("femon", "femoff"):
            target = "Heltec_t096_companion_radio_ble_" + suffix
            with self.subTest(target=target):
                original = ram.requirements("NRF52_PLATFORM", flags, target)
                compact = ram.requirements("NRF52_PLATFORM", {
                    **flags, "ST7735_BUFFER_COLOR_DEPTH": 8,
                }, target)
                self.assertEqual(original["required_heap_bytes"], 81474)
                self.assertEqual(compact["required_heap_bytes"], 69697)
                self.assertEqual(compact["components"]["display_pixels_and_driver"], 12801 + 1024)
                self.assertGreater(original["required_heap_bytes"], 75396)
                self.assertLess(compact["required_heap_bytes"], 75396)
                for name, value in original["components"].items():
                    if name != "display_pixels_and_driver":
                        self.assertEqual(compact["components"][name], value)
                self.assertEqual(compact["components"]["expanded_message_previews"], 2816)
                self.assertEqual(compact["components"]["ota_generic_source_buffers"], 6176)
                self.assertEqual(compact["components"]["allocation_and_transient_margin"], 4096)

    def test_st7735_color_depth_default_and_invalid_values_fail_closed(self):
        flags = {"DISPLAY_CLASS": "ST7735Display"}
        default = ram.requirements("NRF52_PLATFORM", flags, "other_companion")
        explicit = ram.requirements("NRF52_PLATFORM", {
            **flags, "ST7735_BUFFER_COLOR_DEPTH": 16,
        }, "other_companion")
        self.assertEqual(default, explicit)
        self.assertEqual(default["components"]["display_pixels_and_driver"], 25602)
        for depth in (0, 1, 4, 12, 24, 32):
            with self.subTest(depth=depth), self.assertRaisesRegex(ValueError, "ST7735_BUFFER_COLOR_DEPTH"):
                ram.requirements("NRF52_PLATFORM", {
                    **flags, "ST7735_BUFFER_COLOR_DEPTH": depth,
                }, "other_companion")

    def test_t096_canvas_configuration_does_not_reduce_companion_capacities(self):
        config = (ROOT / "variants/heltec_t096/platformio.ini").read_text()
        self.assertIn("-D ST7735_BUFFER_COLOR_DEPTH=8", config)
        self.assertIn("-D MAX_CONTACTS=350", config)
        self.assertIn("-D MAX_GROUP_CHANNELS=40", config)
        self.assertIn("-D OFFLINE_QUEUE_SIZE=256", config)

    def test_wio_l2_counts_internal_sprite_fallback_and_dma_beside_psram(self):
        flags = {"DISPLAY_CLASS": "WioTrackerL2Display", "UI_ZOOM": 2}
        for target in ("Wio_Tracker_L2_repeater", "Wio_Tracker_L2_companion_radio_ble"):
            with self.subTest(target=target):
                policy = ram.requirements("ESP32_PLATFORM", flags, target)
                self.assertEqual(policy["components"]["display_pixels_and_driver"],
                                 160 * 120 + 1024)
                self.assertEqual(policy["components"]["display_dma_and_bus"], 16384)
                self.assertGreaterEqual(policy["required_contiguous_bytes"], 160 * 120 + 1024)
                psram = ram.requirements("ESP32_PLATFORM", {**flags, "BOARD_HAS_PSRAM": 1}, target)
                self.assertEqual(psram, policy)
                without = ram.requirements("ESP32_PLATFORM", {"UI_ZOOM": 2}, target)
                display_components = (policy["components"]["display_pixels_and_driver"]
                                      + policy["components"]["display_dma_and_bus"]
                                      + policy["components"]["screen_objects_and_history"])
                self.assertEqual(policy["required_heap_bytes"] - without["required_heap_bytes"],
                                 display_components)

    def test_wio_l2_zoom_and_base_color_depth_cannot_erase_actual_sprite_cost(self):
        flags = {"DISPLAY_CLASS": "WioTrackerL2Display"}
        cases = (({}, 320 * 240), ({"UI_ZOOM": 2, "UI_BUFFER_COLOR_DEPTH": 4}, 160 * 120),
                 ({"UI_ZOOM": 2, "UI_COORD_SCALE": 2}, 160 * 120),
                 ({"UI_ZOOM": 2, "UI_BUFFER_COLOR_DEPTH": 16}, 160 * 120 * 2),
                 ({"UI_ZOOM": "1.5f", "UI_BUFFER_COLOR_DEPTH": 24}, 214 * 160 * 3),
                 ({"UI_ZOOM": "0.5", "UI_BUFFER_COLOR_DEPTH": 32}, 640 * 480 * 4))
        for overrides, pixels in cases:
            with self.subTest(overrides=overrides):
                policy = ram.requirements("ESP32_PLATFORM", {**flags, **overrides}, "Wio_Tracker_L2_repeater")
                self.assertEqual(policy["components"]["display_pixels_and_driver"], pixels + 1024)
                self.assertGreaterEqual(policy["required_contiguous_bytes"], pixels + 1024)
        for override in ({"UI_ZOOM": 0}, {"UI_ZOOM": -1}, {"UI_ZOOM": "inf"},
                         {"UI_ZOOM": "nan"}, {"UI_ZOOM": "auto"},
                         {"UI_COORD_SCALE": 0}, {"UI_BUFFER_COLOR_DEPTH": 7}):
            with self.subTest(invalid=override), self.assertRaises(ValueError):
                ram.requirements("ESP32_PLATFORM", {**flags, **override}, "Wio_Tracker_L2_repeater")
        with self.assertRaisesRegex(ValueError, "requires ESP32"):
            ram.requirements("NRF52_PLATFORM", flags, "unsupported_display")

    def test_wio_l2_budget_tracks_real_sprite_and_qspi_geometry(self):
        display = (ROOT / "variants/wio-tracker-l2/WioTrackerL2Display.h").read_text()
        base = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text()
        self.assertIn("WioTrackerL2Display() : LGFXDisplay(320, 240, disp)", display)
        self.assertIn("cfg.panel_width = 240;", display)
        self.assertIn("cfg.panel_height = 320;", display)
        self.assertIn("buffer.setColorDepth(8);", display)
        self.assertIn("buffer.setPsram(true);", display)
        self.assertIn("buffer.createSprite(width(), height());", display)
        self.assertIn("lgfx::Bus_SPI _bus_instance;", display)
        self.assertIn("buffer.setPsram(false);", base)
        self.assertIn("buffer.setColorDepth(UI_BUFFER_COLOR_DEPTH);", base)
        self.assertIn("buffer.createSprite(width() * _coordinateScale,", base)

    def test_heap_tables_and_ota_remain_in_runtime_budget(self):
        policy = ram.requirements("ESP32_PLATFORM", {
            "ENABLE_OTA": 1, "OTA_HEAP_CONTEXT": 1,
        }, "GEPRC_Linkflow_900_repeater")
        self.assertEqual(policy["components"]["client_table"], 32 * 320 + 16)
        self.assertEqual(policy["components"]["flood_filter_table"], 63 * 300 + 16)
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

    def test_s3_psram_tables_reserve_full_internal_fallback_without_cutting_other_owners(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1, "WITH_MQTT_BRIDGE": 1,
                 "DISPLAY_CLASS": "SH1106Display", "ADMIN_PASSWORD": "test",
                 "ENABLE_OTA": 1, "WITH_UDP_BRIDGE": 1, "WITH_RS232_BRIDGE": "Serial2"}
        target = "Station_G3_ESP32_repeater"
        original = ram.requirements("ESP32_PLATFORM", {
            **flags, "MESH_REPEATER_PSRAM_TABLES": 0}, target)
        selected = ram.requirements("ESP32_PLATFORM", flags, target)
        scope = 255 * 36 + 255 * 34 + 32 * 32 + 2 + 16
        advert = 128 * 140 + 16
        self.assertEqual(selected["components"], {
            **original["components"], "repeater_scope_storage_fallback": scope,
            "repeater_advert_storage_fallback": advert})
        self.assertEqual(selected["required_heap_bytes"],
                         original["required_heap_bytes"] + scope + advert)
        self.assertEqual(selected["mqtt"], original["mqtt"])
        self.assertEqual(selected["components"]["allocation_and_transient_margin"], 16384)
        self.assertEqual(selected["components"]["wireless_stacks"], 49152)
        self.assertEqual(ram.requirements("ESP32_PLATFORM", {
            **flags, "MESH_REPEATER_PSRAM_TABLES": 1}, target), selected)

    def test_psram_table_defaults_follow_s3_psram_platform_and_source_role_gates(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1}
        cases = [
            ("ESP32_PLATFORM", {k: v for k, v in flags.items() if k != "CONFIG_IDF_TARGET_ESP32S3"},
             "other_s3_repeater"),
            ("ESP32_PLATFORM", {k: v for k, v in flags.items() if k != "BOARD_HAS_PSRAM"},
             "Station_G3_ESP32_repeater"),
            ("ESP32_PLATFORM", {k: v for k, v in flags.items() if k != "MESH_CLIENT_REPEATER_ONLY"},
             "Station_G3_ESP32_repeater"),
            ("ESP32_PLATFORM", {**flags, "MESH_CLIENT_REPEATER_ONLY": 0},
             "Station_G3_ESP32_repeater"),
            ("ESP32_PLATFORM", {**flags, "MESH_CLIENT_REPEATER_ONLY": 0,
                                "COMPANION_RADIO_FULL": 1},
             "Station_G3_ESP32_companion_radio_full"),
            ("ESP32_PLATFORM", {**flags, "MESH_CLIENT_REPEATER_ONLY": 0},
             "Station_G3_ESP32_room_server"),
        ]
        for mcu in ("ESP32", "ESP32S2", "ESP32C3", "ESP32C6"):
            cases.append(("ESP32_PLATFORM", {
                **flags, "CONFIG_IDF_TARGET_ESP32S3": 0,
                "CONFIG_IDF_TARGET_" + mcu: 1}, "other_repeater"))
        for platform in ("NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
            cases.extend((platform, {**flags, **extra}, "other_repeater")
                         for extra in ({}, {"MESH_REPEATER_PSRAM_TABLES": 1}))
        for platform, defines, target in cases:
            with self.subTest(platform=platform, defines=defines, target=target):
                policy = ram.requirements(platform, defines, target)
                self.assertNotIn("repeater_scope_storage_fallback", policy["components"])
                self.assertNotIn("repeater_advert_storage_fallback", policy["components"])
                self.assertEqual(policy, ram.requirements(platform, {
                    **defines, "MESH_REPEATER_PSRAM_TABLES": 0}, target))
        # The source role, rather than a release filename, selects MyMesh.
        renamed = ram.requirements("ESP32_PLATFORM", flags, "custom_board_forwarder")
        self.assertIn("repeater_scope_storage_fallback", renamed["components"])
        self.assertIn("repeater_advert_storage_fallback", renamed["components"])

    def test_required_psram_is_explicit_and_preserves_every_default_budget(self):
        cases = (
            ("ESP32_PLATFORM", {}, "board_repeater"),
            ("ESP32_PLATFORM", {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                                "MESH_CLIENT_REPEATER_ONLY": 1}, "board_repeater"),
            ("NRF52_PLATFORM", {}, "board_repeater"),
            ("RP2040_PLATFORM", {}, "board_repeater"),
            ("STM32_PLATFORM", {}, "board_repeater"),
        )
        for platform, flags, target in cases:
            with self.subTest(platform=platform, flags=flags):
                original = ram.requirements(platform, flags, target)
                self.assertNotIn("external_memory", original)
                self.assertEqual(original, ram.requirements(platform, {
                    **flags, "MESH_REPEATER_REQUIRE_PSRAM": 0}, target))

    def test_required_psram_moves_only_full_table_allocations_and_metadata(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1, "WITH_MQTT_BRIDGE": 1,
                 "DISPLAY_CLASS": "SSD1306Display", "ADMIN_PASSWORD": "test",
                 "ENABLE_OTA": 1, "WITH_UDP_BRIDGE": 1, "WITH_RS232_BRIDGE": "Serial2"}
        target = "meshnology_w12_repeater_observer_mqtt"
        original = ram.requirements("ESP32_PLATFORM", flags, target)
        selected = ram.requirements("ESP32_PLATFORM", {
            **flags, "MESH_REPEATER_REQUIRE_PSRAM": 1}, target)
        moved = {"repeater_scope_storage": 18892, "repeater_advert_storage": 17936}
        self.assertEqual(selected["external_memory"], {
            "required_heap_bytes": 36828, "required_contiguous_bytes": 18892,
            "components": moved, "enforced_before_radio_startup": True,
            "internal_fallback": False, "capacity_verified_at_build": False})
        self.assertEqual(selected["components"], {
            name: value for name, value in original["components"].items()
            if name not in {"repeater_scope_storage_fallback", "repeater_advert_storage_fallback"}})
        self.assertEqual(selected["required_heap_bytes"], original["required_heap_bytes"] - sum(moved.values()))
        self.assertEqual(selected["mqtt"], original["mqtt"])
        self.assertEqual(selected["components"]["allocation_and_transient_margin"], 16384)
        self.assertEqual(selected["components"]["wireless_stacks"], 49152)
        self.assertEqual(selected["components"]["client_table"], original["components"]["client_table"])
        self.assertEqual(selected["components"]["flood_filter_table"], original["components"]["flood_filter_table"])
        self.assertEqual(selected["components"]["neighbor_history"], original["components"]["neighbor_history"])
        self.assertEqual(selected, ram.requirements("ESP32_PLATFORM", {
            **flags, "MESH_REPEATER_REQUIRE_PSRAM": 1, "MESH_MIN_RUNTIME_HEAP": 1}, target))

    def test_required_psram_preserves_custom_capacities_and_external_contiguous_bound(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1, "CONFIG_SPIRAM_USE_MALLOC": 1}
        for extra in ({"FLOOD_CHANNEL_SCOPE_SLOTS": 7, "FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS": 11,
                       "FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS": 3, "FLOOD_ADVERT_SOURCE_SLOTS": 17},
                      {"MAX_REGION_ENTRIES": 64, "FLOOD_ADVERT_SOURCE_SLOTS": 200}):
            with self.subTest(extra=extra):
                baseline = ram.requirements("ESP32_PLATFORM", {**flags, **extra}, "custom_forwarder")
                selected = ram.requirements("ESP32_PLATFORM", {
                    **flags, **extra, "MESH_REPEATER_REQUIRE_PSRAM": 1}, "custom_forwarder")
                external = selected["external_memory"]
                for name in ("repeater_scope_storage", "repeater_advert_storage"):
                    self.assertEqual(external["components"][name], baseline["components"][name + "_fallback"])
                self.assertEqual(external["required_heap_bytes"], sum(external["components"].values()))
                self.assertEqual(external["required_contiguous_bytes"], max(external["components"].values()))
                self.assertEqual(selected["components"], {
                    name: value for name, value in baseline["components"].items()
                    if not name.endswith("_storage_fallback")})

    def test_required_psram_does_not_move_alternate_advert_storage(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1, "MESH_REPEATER_REQUIRE_PSRAM": 1}
        for extra in ({"PORTABLE_MQTT_OBSERVER": 1}, {"FLOOD_ADVERT_IRAM_WORD_STORAGE": 1},
                      {"FLOOD_ADVERT_RTC_FAST_SLOTS": 16}, {"FLOOD_ADVERT_RTC_SLOW_SLOTS": 50}):
            with self.subTest(extra=extra):
                selected = ram.requirements("ESP32_PLATFORM", {**flags, **extra}, "s3_repeater")
                external = selected["external_memory"]
                self.assertEqual(external["components"], {"repeater_scope_storage": 18892})
                self.assertEqual(external["required_heap_bytes"], 18892)
                self.assertEqual(external["required_contiguous_bytes"], 18892)

    def test_required_psram_rejects_invalid_flags_platforms_and_disabled_tables(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1}
        for value in (-1, 2, "yes", "1.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ram.requirements("ESP32_PLATFORM", {**flags, "MESH_REPEATER_REQUIRE_PSRAM": value}, "s3_repeater")
        invalid = [
            ("ESP32_PLATFORM", {key: value for key, value in flags.items() if key != removed})
            for removed in flags
        ]
        invalid += [("ESP32_PLATFORM", {**flags, **extra}) for extra in (
            {"CONFIG_IDF_TARGET_ESP32S3": 0}, {"MESH_CLIENT_REPEATER_ONLY": 0},
            {"MESH_REPEATER_PSRAM_TABLES": 0}, {"MESH_REPEATER_PSRAM_TABLES": 2},
            {"CONFIG_SPIRAM_USE_MALLOC": 0})]
        invalid += [(platform, flags) for platform in ("NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM")]
        for platform, defines in invalid:
            with self.subTest(platform=platform, flags=defines), self.assertRaises(ValueError):
                ram.requirements(platform, {**defines, "MESH_REPEATER_REQUIRE_PSRAM": 1}, "named_repeater")

    def test_required_psram_elf_retains_exact_internal_gate_and_reports_runtime_external_contract(self):
        flags = {"BOARD_HAS_PSRAM": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
                 "WITH_MQTT_BRIDGE": 1, "MESH_REPEATER_REQUIRE_PSRAM": 1}
        target = "meshnology_w12_repeater_observer_mqtt"
        expected = ram.requirements("ESP32_PLATFORM", {**flags, "CONFIG_IDF_TARGET_ESP32S3": 1}, target)
        required = expected["required_heap_bytes"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.elf"
            report = Path(temporary) / "firmware.memory.json"
            for capacity, accepted in ((required - 1, False), (required, True)):
                with self.subTest(capacity=capacity), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    esp_fixture(path, modern=True, internal_length=capacity + 0x4000)
                    status = ram.check_firmware(path, "ESP32_PLATFORM", "esp32s3", flags, target, report)
                    result = json.loads(report.read_text())
                    self.assertEqual(status == 0, accepted)
                    self.assertEqual(result["passed"], accepted)
                    self.assertEqual(result["available_internal_bytes"], capacity)
                    self.assertEqual(result["required_heap_bytes"], required)
                    self.assertEqual(result["components"], expected["components"])
                    self.assertEqual(result["external_memory"], expected["external_memory"])
                    # Internal admission is separate; the board's mandatory
                    # external allocations still have to pass before radio startup.
                    self.assertFalse(result["external_memory"]["capacity_verified_at_build"])

    def test_required_psram_elf_uses_authoritative_mcu_over_stale_sdk_flags(self):
        flags = {"BOARD_HAS_PSRAM": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
                 "MESH_REPEATER_REQUIRE_PSRAM": 1}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.elf"
            esp_fixture(path, modern=True, internal_length=0x40000)
            with self.assertRaises(ValueError):
                ram.check_firmware(path, "ESP32_PLATFORM", "esp32", {
                    **flags, "CONFIG_IDF_TARGET_ESP32S3": 1}, "named_repeater")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ram.check_firmware(path, "ESP32_PLATFORM", "esp32s3", {
                    **flags, "CONFIG_IDF_TARGET_ESP32S3": 0}, "named_repeater"), 0)

    def test_psram_repeater_table_defaults_cover_opi_and_qspi_families_without_a_g3_marker(self):
        flags = {"BOARD_HAS_PSRAM": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
                 "CONFIG_IDF_TARGET_ESP32S3": 1, "CONFIG_SPIRAM_USE_MALLOC": 1}
        families = (
            ("LilyGo_TDeck_repeater", {"CONFIG_SPIRAM_MODE_OCT": 1,
                                      "CONFIG_SPIRAM_BOOT_INIT": 1}),
            ("Station_G2_repeater", {"CONFIG_SPIRAM_MODE_OCT": 1,
                                    "CONFIG_SPIRAM_BOOT_INIT": 1}),
            ("heltec_v4_repeater", {"CONFIG_SPIRAM_MODE_QUAD": 1}),
            ("SenseCapIndicator-LoRa-N16R2_repeater", {"CONFIG_SPIRAM_MODE_QUAD": 1}),
        )
        for target, extra in families:
            with self.subTest(target=target):
                defines = {**flags, **extra}
                self.assertNotIn("STATION_G3_ESP32", defines)
                selected = ram.requirements("ESP32_PLATFORM", defines, target)
                original = ram.requirements("ESP32_PLATFORM", {
                    **defines, "MESH_REPEATER_PSRAM_TABLES": 0}, target)
                self.assertEqual(selected["components"], {
                    **original["components"], "repeater_scope_storage_fallback": 18892,
                    "repeater_advert_storage_fallback": 17936})
                self.assertEqual(selected["required_heap_bytes"] - original["required_heap_bytes"],
                                 36828)

    def test_explicit_psram_repeater_table_opt_in_and_capacity_overrides_are_fully_budgeted(self):
        flags = {"BOARD_HAS_PSRAM": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
                 "MESH_REPEATER_PSRAM_TABLES": 1}
        cases = (
            ({}, 255, 255, 32, 128),
            ({"FLOOD_CHANNEL_SCOPE_SLOTS": 5, "MAX_REGION_ENTRIES": 2}, 5, 5, 2, 128),
            ({"FLOOD_CHANNEL_SCOPE_SLOTS": 7, "FLOOD_CHANNEL_SCOPE_REQUIRE_SLOTS": 11,
              "FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS": 3, "FLOOD_ADVERT_SOURCE_SLOTS": 17},
             7, 11, 3, 17),
            ({"FLOOD_CHANNEL_SCOPE_SLOTS": 3, "MAX_REGION_ENTRIES": 2,
              "FLOOD_CHANNEL_DIRECT_SCOPE_SLOTS": 9}, 3, 3, 9, 128),
            ({"MAX_REGION_ENTRIES": 64, "FLOOD_ADVERT_SOURCE_SLOTS": 200},
             255, 255, 64, 200),
        )
        for extra, scopes, required, direct, adverts in cases:
            with self.subTest(extra=extra):
                defines = {**flags, **extra}
                selected = ram.requirements("ESP32_PLATFORM", defines, "other_s3_repeater")
                original = ram.requirements("ESP32_PLATFORM", {
                    **defines, "MESH_REPEATER_PSRAM_TABLES": 0}, "other_s3_repeater")
                scope = scopes * 36 + required * 34 + direct * 32 + 2 + 16
                advert = adverts * 140 + 16
                self.assertEqual(selected["components"], {
                    **original["components"], "repeater_scope_storage_fallback": scope,
                    "repeater_advert_storage_fallback": advert})
                self.assertEqual(selected["required_heap_bytes"],
                                 original["required_heap_bytes"] + scope + advert)

    def test_psram_advert_budget_excludes_portable_iram_and_rtc_storage_only(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1}
        target = "Station_G3_ESP32_repeater"
        original = ram.requirements("ESP32_PLATFORM", flags, target)
        advert = original["components"]["repeater_advert_storage_fallback"]
        for extra in ({"PORTABLE_MQTT_OBSERVER": 1}, {"PORTABLE_MQTT_OBSERVER": 0},
                      {"FLOOD_ADVERT_IRAM_WORD_STORAGE": 1},
                      {"FLOOD_ADVERT_RTC_FAST_SLOTS": 16},
                      {"FLOOD_ADVERT_RTC_SLOW_SLOTS": 50},
                      {"FLOOD_ADVERT_RTC_FAST_SLOTS": 50, "FLOOD_ADVERT_RTC_SLOW_SLOTS": 50}):
            with self.subTest(extra=extra):
                policy = ram.requirements("ESP32_PLATFORM", {**flags, **extra}, target)
                self.assertEqual(policy["components"], {
                    k: v for k, v in original["components"].items()
                    if k != "repeater_advert_storage_fallback"})
                self.assertEqual(policy["required_heap_bytes"],
                                 original["required_heap_bytes"] - advert)
        disabled_backends = {"FLOOD_ADVERT_IRAM_WORD_STORAGE": 0,
                             "FLOOD_ADVERT_RTC_FAST_SLOTS": 0, "FLOOD_ADVERT_RTC_SLOW_SLOTS": 0}
        self.assertEqual(ram.requirements("ESP32_PLATFORM", {
            **flags, **disabled_backends}, target), original)

    def test_each_psram_table_fallback_can_set_the_contiguous_allocation_bound(self):
        flags = {"CONFIG_IDF_TARGET_ESP32S3": 1, "BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1, "MAX_CLIENTS": 1,
                 "FLOOD_PACKET_FILTER_SLOTS": 1, "MAX_RECENT_REPEATERS": 0}
        for extra, name in (({}, "repeater_scope_storage_fallback"),
                            ({"FLOOD_ADVERT_SOURCE_SLOTS": 200},
                             "repeater_advert_storage_fallback")):
            with self.subTest(extra=extra):
                policy = ram.requirements("ESP32_PLATFORM", {**flags, **extra}, "g3_repeater")
                self.assertEqual(policy["required_contiguous_bytes"], policy["components"][name])
                self.assertGreater(policy["required_contiguous_bytes"], 16384)

    def test_s3_psram_fallback_elf_gate_rejects_old_budget_and_enforces_exact_total(self):
        flags = {"BOARD_HAS_PSRAM": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1, "WITH_MQTT_BRIDGE": 1}
        target = "heltec_v4_repeater"
        previous = {**flags, "MESH_REPEATER_PSRAM_TABLES": 0}
        old = ram.requirements("ESP32_PLATFORM", previous, target)["required_heap_bytes"]
        required = ram.requirements("ESP32_PLATFORM", {
            **flags, "CONFIG_IDF_TARGET_ESP32S3": 1}, target)["required_heap_bytes"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.elf"
            report = Path(temporary) / "firmware.memory.json"
            for capacity, accepted in ((old, False), (required - 1, False), (required, True)):
                with self.subTest(capacity=capacity), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    esp_fixture(path, modern=True, internal_length=capacity + 0x4000)
                    self.assertEqual(ram.check_firmware(path, "ESP32_PLATFORM", "esp32s3",
                                                       previous, target), 0)
                    status = ram.check_firmware(path, "ESP32_PLATFORM", "esp32s3",
                                                flags, target, report)
                    result = json.loads(report.read_text())
                    self.assertEqual(status == 0, accepted)
                    self.assertEqual(result["passed"], accepted)
                    self.assertEqual(result["available_internal_bytes"], capacity)
                    self.assertEqual(result["required_heap_bytes"], required)
                    self.assertEqual(result["components"]["repeater_scope_storage_fallback"], 18892)
                    self.assertEqual(result["components"]["repeater_advert_storage_fallback"], 17936)

    def test_psram_table_elf_policy_uses_authoritative_mcu_over_missing_or_stale_sdk_macros(self):
        flags = {"BOARD_HAS_PSRAM": 1, "MESH_CLIENT_REPEATER_ONLY": 1}
        target = "board_repeater"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.elf"
            report = Path(temporary) / "firmware.memory.json"
            for mcu, stale_macro in (("esp32s3", None), ("esp32s3", 0),
                                     ("esp32", None), ("esp32", 1)):
                with self.subTest(mcu=mcu, stale_macro=stale_macro), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    defines = dict(flags)
                    if stale_macro is not None:
                        defines["CONFIG_IDF_TARGET_ESP32S3"] = stale_macro
                    expected = ram.requirements("ESP32_PLATFORM", {
                        **defines, "CONFIG_IDF_TARGET_ESP32S3": int(mcu == "esp32s3"),
                        "MESH_RAM_DEFAULT_RECENT_REPEATERS": 256 if mcu == "esp32" else 2048,
                    }, target)
                    capacity = expected["required_heap_bytes"]
                    # Classic ESP32 reclaims the second 64 KiB ROM-stack
                    # region. The S3 excludes it; both exclude the first 16 KiB.
                    length = capacity + 0x4000 - (0x10000 if mcu == "esp32" else 0)
                    esp_fixture(path, modern=True, internal_length=length)
                    status = ram.check_firmware(path, "ESP32_PLATFORM", mcu,
                                                defines, target, report)
                    result = json.loads(report.read_text())
                    self.assertEqual(status, 0)
                    self.assertTrue(result["passed"])
                    self.assertEqual(result["available_internal_bytes"], capacity)
                    self.assertEqual(result["required_heap_bytes"], capacity)
                    self.assertEqual(result["components"], expected["components"])
                    for name in ("repeater_scope_storage_fallback", "repeater_advert_storage_fallback"):
                        self.assertEqual(name in result["components"], mcu == "esp32s3")

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

    def test_room_browser_workspaces_are_bounded_and_only_budgeted_on_eligible_rooms(self):
        flags = {"ADMIN_PASSWORD": "test"}
        enabled = ram.requirements("ESP32_PLATFORM", flags, "v4_room_server")
        disabled = ram.requirements("ESP32_PLATFORM", {**flags, "WEBCONFIG_DISABLED": 1}, "v4_room_server")
        self.assertEqual(enabled["components"]["room_browser_mailbox"], 6656 + 16)
        self.assertEqual(enabled["components"]["room_browser_request_workspace"], 8192 + 2048 + 16)
        self.assertGreaterEqual(enabled["required_contiguous_bytes"], 8192)
        for policy in (disabled, ram.requirements("ESP32_PLATFORM", flags, "v4_repeater"),
                       ram.requirements("ESP32_PLATFORM", flags, "v4_companion"),
                       ram.requirements("NRF52_PLATFORM", flags, "RAK_4631_room_server")):
            self.assertNotIn("room_browser_mailbox", policy["components"])
            self.assertNotIn("room_browser_request_workspace", policy["components"])

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

    def test_unified_v4_display_reserves_retained_oled_and_both_bus_owners(self):
        defines = {"HELTEC_V4_DISPLAY_UNIFIED": 1, "HELTEC_LORA_V4": 1}
        for role in ("heltec_v4_repeater", "heltec_v4_room_server",
                     "heltec_v4_sensor", "heltec_v4_companion_radio_full"):
            with self.subTest(role=role):
                oled = ram.requirements("ESP32_PLATFORM", {
                    **defines, "DISPLAY_CLASS": "SSD1306Display",
                }, role)
                unified = ram.requirements("ESP32_PLATFORM", {
                    **defines, "DISPLAY_CLASS": "HeltecV4Display",
                }, role)
                parts = unified["components"]
                self.assertEqual(parts["display_pixels_and_driver"], 2048)
                self.assertEqual(parts["display_i2c_spi_and_probe"], 4096)
                self.assertEqual(parts["allocation_and_transient_margin"], 16384)
                # No common reserve is reduced to pay for the additional bus.
                self.assertEqual(parts, {
                    **oled["components"], "display_i2c_spi_and_probe": 4096,
                })
                self.assertEqual(unified["required_heap_bytes"],
                                 oled["required_heap_bytes"] + 4096)
                if "companion" in role:
                    # This class can become a small OLED after a saved runtime
                    # setting change, even when it initially selects TFT/off.
                    self.assertEqual(parts["expanded_message_previews"], 2816)
                    self.assertGreaterEqual(unified["required_contiguous_bytes"], 11008)
        for platform, extra in (("NRF52_PLATFORM", defines),
                                ("ESP32_PLATFORM", {})):
            with self.subTest(platform=platform, extra=extra):
                with self.assertRaisesRegex(ValueError, "unified Heltec V4"):
                    ram.requirements(platform, {
                        **extra, "DISPLAY_CLASS": "HeltecV4Display",
                    }, "companion")

    def test_unified_v4_display_source_guard_rejects_growth_at_budget_boundary(self):
        source = (ROOT / "src/helpers/ui/HeltecV4Display.cpp").read_text()
        guard = source.split("// Runtime budget guard:", 1)[1].split(
            "extern const char meshcore_capability_display_heltec_v4_unified", 1)[0]
        # Compile the actual assertions, including negative boundaries. The
        # firmware build substitutes real wrapper/Wire/library definitions.
        declarations = (
            "struct HeltecV4Display { unsigned char bytes[OBJECT_BYTES]; };\n"
            "struct SSD1306Display { static constexpr unsigned FRAMEBUFFER_BYTES = FRAME_BYTES; };\n"
        )
        # The split removes the comment opener but leaves its first line.
        guard = "// Runtime budget guard:" + guard
        for objects, framebuffer, wire, accepted in (
                (992, 1024, 128, True), (993, 1024, 128, False),
                (992, 1025, 128, False), (992, 1024, 129, False)):
            with self.subTest(objects=objects, framebuffer=framebuffer, wire=wire):
                result = subprocess.run([
                    "c++", "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                    f"-DOBJECT_BYTES={objects}", f"-DFRAME_BYTES={framebuffer}",
                    f"-DI2C_BUFFER_LENGTH={wire}",
                ], input=declarations + guard, text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_combined_uart_reserves_heap_beside_mqtt_before_enable(self):
        defines = {"WITH_MQTT_BRIDGE": 1, "WIFI_OTA_SEEDER": 1}
        mqtt = ram.requirements("ESP32_PLATFORM", defines, "v3_repeater")
        combined = ram.requirements("ESP32_PLATFORM", {
            **defines, "WITH_RS232_BRIDGE": "Serial2", "RS232_BRIDGE_MERGED": 1,
        }, "v3_repeater")
        self.assertEqual(combined["required_heap_bytes"] - mqtt["required_heap_bytes"], 3072 + 4096)
        self.assertEqual(combined["components"]["uart_bridge_and_driver"], 3072 + 4096)
        # A selectable alternate port does not create two UART owners.
        alternate = ram.requirements("ESP32_PLATFORM", {
            **defines, "WITH_RS232_BRIDGE": "Serial2", "RS232_BRIDGE_MERGED": 1,
            "WITH_RS232_BRIDGE_ALT": "Serial1",
        }, "v3_repeater")
        self.assertEqual(alternate, combined)

    def test_udp_bridge_budget_counts_enabled_case_alongside_existing_owners(self):
        flags = {"WITH_MQTT_BRIDGE": 1, "WITH_RS232_BRIDGE": "Serial2"}
        previous = ram.requirements("ESP32_PLATFORM", flags, "v3_repeater")
        udp = ram.requirements("ESP32_PLATFORM", {**flags, "WITH_UDP_BRIDGE": 1}, "v3_repeater")
        self.assertEqual(udp["required_heap_bytes"] - previous["required_heap_bytes"], 5120)
        self.assertEqual(udp["components"]["udp_bridge_and_socket"], 3072 + 2048)
        self.assertEqual(udp["components"]["wireless_stacks"], previous["components"]["wireless_stacks"])
        self.assertEqual(udp["components"]["uart_bridge_and_driver"], previous["components"]["uart_bridge_and_driver"])
        # UDP without MQTT must still reserve the shared WiFi stack.
        udp_only = ram.requirements("ESP32_PLATFORM", {"WITH_UDP_BRIDGE": 1}, "v4_repeater")
        self.assertEqual(udp_only["components"]["wireless_stacks"], 49152)
        self.assertEqual(udp_only["components"]["udp_bridge_and_socket"], 5120)

    def test_mqtt_startup_gate_distinguishes_maximum_slot_stack_buffer_capacity(self):
        flags = {"WITH_MQTT_BRIDGE": 1}
        plain = ram.requirements("ESP32_PLATFORM", flags, "v4_companion")
        psram = ram.requirements("ESP32_PLATFORM", {**flags, "BOARD_HAS_PSRAM": 1},
                                 "v4_companion")
        # The potential five-slot allowance is reported separately: additional
        # clients allocate lazily and must pass the production runtime gate.
        self.assertEqual(plain["required_heap_bytes"], psram["required_heap_bytes"])
        self.assertEqual(plain["required_heap_bytes"], 119808)
        self.assertEqual(plain["components"]["mqtt_startup_allowance"], 24576)
        self.assertEqual(psram["components"]["mqtt_startup_allowance"], 24576)
        self.assertEqual(plain["components"]["allocation_and_transient_margin"], 16384)
        self.assertEqual(psram["components"]["allocation_and_transient_margin"], 16384)
        for policy, slots, active, potential in ((plain, 3, 2, 24576), (psram, 6, 5, 48384)):
            with self.subTest(active=active):
                profile = policy["mqtt"]
                self.assertEqual(profile["runtime_slot_count"], slots)
                self.assertEqual(profile["maximum_active_slots"], active)
                self.assertEqual(profile["startup_active_slot_allowance"], 2)
                self.assertEqual(profile["maximum_active_slots_without_psram"], 2)
                self.assertEqual(profile["maximum_slot_stack_buffer_allowance_bytes"], potential)
                self.assertEqual(profile["cold_start_allocation_allowance_bytes"], 16384)
                self.assertEqual(profile["cold_start_dma_admission_free_bytes"], 32768)
                self.assertEqual(profile["cold_start_dma_admission_contiguous_bytes"], 8192)
                self.assertEqual(profile["restart_allocation_allowance_bytes"], 8192)
                self.assertEqual(profile["restart_dma_admission_free_bytes"], 24576)
                self.assertEqual(profile["restart_dma_admission_contiguous_bytes"], 8192)
                self.assertFalse(profile["maximum_runtime_load_qualified"])
                self.assertFalse(profile["physical_validation_performed"])
        ordinary = ram.requirements("ESP32_PLATFORM", {"BOARD_HAS_PSRAM": 1}, "v4_companion")
        self.assertNotIn("mqtt", ordinary)

    def test_mqtt_without_build_time_wifi_credentials_reserves_wifi_stack_exactly_once(self):
        # MQTT can use persisted station credentials without either compile-time
        # WiFi option. Isolate the wireless budget from optional browser sessions.
        base = {"WEBCONFIG_DISABLED": 1}
        for target in ("Tbeam_SX1262_repeater_observer_mqtt", "v4_companion"):
            ordinary = ram.requirements("ESP32_PLATFORM", base, target)
            self.assertEqual(ordinary["components"]["wireless_stacks"], 0)
            mqtt = ram.requirements("ESP32_PLATFORM", {**base, "WITH_MQTT_BRIDGE": 1}, target)
            self.assertEqual(mqtt["components"]["wireless_stacks"], 49152)
            self.assertEqual(mqtt["required_heap_bytes"] - ordinary["required_heap_bytes"],
                             49152 + 24576)
            for extra in ({"WIFI_SSID": ""}, {"WIFI_OTA_SEEDER": 1},
                          {"WIFI_SSID": "saved-at-runtime", "WIFI_OTA_SEEDER": 1}):
                with self.subTest(target=target, extra=extra):
                    combined = ram.requirements("ESP32_PLATFORM", {
                        **base, "WITH_MQTT_BRIDGE": 1, **extra,
                    }, target)
                    self.assertEqual(combined, mqtt)

    def test_mqtt_only_elf_gate_rejects_the_previously_omitted_wifi_allowance(self):
        defines = {"WITH_MQTT_BRIDGE": 1, "WEBCONFIG_DISABLED": 1}
        target = "Tbeam_SX1262_repeater_observer_mqtt"
        policy = ram.requirements("ESP32_PLATFORM", defines, target)
        self.assertEqual(policy["components"]["wireless_stacks"], 49152)
        required = policy["required_heap_bytes"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.elf"
            report = Path(temporary) / "firmware.memory.json"
            # The old WiFi-omitted policy accepted the first linked capacity.
            # The corrected gate must reject it and enforce the exact new bound.
            for capacity, expected in ((required - 49152, False),
                                       (required - 1, False), (required, True)):
                with self.subTest(capacity=capacity), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    esp_fixture(path, modern=True, internal_length=capacity + 0x4000)
                    status = ram.check_firmware(path, "ESP32_PLATFORM", "esp32s3",
                                                defines, target, report)
                    result = json.loads(report.read_text())
                    self.assertEqual(status == 0, expected)
                    self.assertEqual(result["passed"], expected)
                    self.assertEqual(result["available_internal_bytes"], capacity)
                    self.assertEqual(result["required_heap_bytes"], required)

    def test_mqtt_worker_stack_growth_raises_gate_and_contiguous_requirement(self):
        flags = {"WITH_MQTT_BRIDGE": 1, "BOARD_HAS_PSRAM": 1}
        base = ram.requirements("ESP32_PLATFORM", flags, "v4_companion")
        large = ram.requirements("ESP32_PLATFORM", {
            **flags, "MQTT_TASK_STACK_SIZE": 32768,
        }, "v4_companion")
        self.assertEqual(large["required_heap_bytes"] - base["required_heap_bytes"], 24576)
        self.assertEqual(large["required_contiguous_bytes"], 32768)
        self.assertEqual(large["mqtt"]["maximum_slot_stack_buffer_allowance_bytes"], 72960)
        small = ram.requirements("ESP32_PLATFORM", {
            **flags, "MQTT_TASK_STACK_SIZE": 4096, "MQTT_RUNTIME_SLOT_COUNT": 1,
        }, "v4_companion")
        self.assertEqual(small["required_heap_bytes"], base["required_heap_bytes"])
        self.assertEqual(small["mqtt"]["maximum_active_slots"], 1)
        for slots in (0, 7):
            with self.subTest(slots=slots), self.assertRaisesRegex(ValueError, "between 1 and 6"):
                ram.requirements("ESP32_PLATFORM", {
                    **flags, "MQTT_RUNTIME_SLOT_COUNT": slots,
                }, "v4_companion")
        with self.assertRaisesRegex(ValueError, "positive"):
            ram.requirements("ESP32_PLATFORM", {**flags, "MQTT_TASK_STACK_SIZE": 0}, "v4_companion")

    def test_mqtt_report_at_startup_boundary_does_not_qualify_maximum_load(self):
        flags = {"WITH_MQTT_BRIDGE": 1, "BOARD_HAS_PSRAM": 1}
        policy = ram.requirements("ESP32_PLATFORM", flags, "v4_companion")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "firmware.elf"
            report = Path(temp) / "firmware.memory.json"
            for delta, expected in ((-1, False), (0, True)):
                with self.subTest(delta=delta), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    capacity = policy["required_heap_bytes"] + delta
                    esp_fixture(path, modern=True, internal_length=capacity + 0x4000)
                    status = ram.check_firmware(path, "ESP32_PLATFORM", "esp32s3", flags,
                                                "v4_companion", report)
                    result = json.loads(report.read_text())
                    self.assertEqual(status == 0, expected)
                    self.assertEqual(result["passed"], expected)
                    self.assertEqual(result["available_internal_bytes"], capacity)
                    self.assertEqual(result["mqtt"]["maximum_slot_stack_buffer_allowance_bytes"], 48384)
                    self.assertFalse(result["mqtt"]["maximum_runtime_load_qualified"])
                    self.assertFalse(result["mqtt"]["physical_validation_performed"])

    def test_mqtt_report_admission_thresholds_match_production_guard(self):
        source = (ROOT / "src/helpers/MQTTConnectionAdmission.h").read_text()
        profile = ram.requirements("ESP32_PLATFORM", {"WITH_MQTT_BRIDGE": 1}, "v4_companion")["mqtt"]
        for key, name in (
            ("client_task_stack_bytes", "kClientTaskStackBytes"),
            ("client_task_priority", "kClientTaskPriority"),
            ("cold_start_allocation_allowance_bytes", "kColdStartAllowanceBytes"),
            ("restart_allocation_allowance_bytes", "kRestartAllowanceBytes"),
            ("dma_admission_free_bytes", "kDmaReserveBytes"),
            ("publish_contiguous_without_psram_bytes", "kInternalPublishLargestBytes"),
            ("publish_contiguous_with_psram_bytes", "kPsramPublishLargestBytes"),
            ("tls_psram_admission_contiguous_bytes", "kPsramTlsLargestBytes"),
            ("tls_internal_admission_free_bytes", "kTlsInternalFreeBytes"),
            ("tls_record_allocation_bytes", "kTlsRecordAllocationBytes"),
        ):
            with self.subTest(key=key):
                self.assertIn(f"{name} = {profile[key]};", source)
        for psram in (False, True):
            flags = {"WITH_MQTT_BRIDGE": 1}
            if psram:
                flags["BOARD_HAS_PSRAM"] = 1
            actual = ram.requirements("ESP32_PLATFORM", flags, "v4_companion")["mqtt"]
            floor = 4096 if psram else 8000
            self.assertEqual(actual["publish_contiguous_bytes"], floor)
            self.assertEqual(actual["dma_admission_contiguous_bytes"], floor)
            self.assertEqual(actual["cold_start_dma_admission_contiguous_bytes"], max(floor, 8192))
            self.assertEqual(actual["additional_broker_cold_start_dma_admission_contiguous_bytes"], floor + 16384)
            self.assertEqual(actual["restart_dma_admission_contiguous_bytes"], max(floor, 8192))
            self.assertEqual(actual["additional_broker_restart_dma_admission_contiguous_bytes"], floor + 8192)
            self.assertEqual(actual["tls_psram_admission_contiguous_bytes"], 16000)
            self.assertEqual(actual["additional_broker_cold_start_tls_psram_admission_contiguous_bytes"], 32384)
            self.assertEqual(actual["additional_broker_restart_tls_psram_admission_contiguous_bytes"], 24192)
            self.assertEqual(actual["additional_broker_tls_psram_admission_contiguous_bytes"], 16000)
            self.assertEqual(actual["additional_broker_cold_start_tls_internal_admission_contiguous_bytes"], 41792)
            self.assertEqual(actual["additional_broker_restart_tls_internal_admission_contiguous_bytes"], 33600)
            self.assertEqual(actual["tls_internal_admission_contiguous_bytes"], 17408)
            self.assertEqual(actual["additional_broker_tls_internal_admission_contiguous_bytes"], 25408)
        self.assertEqual(profile["cold_start_dma_admission_free_bytes"],
                         profile["dma_admission_free_bytes"] + profile["cold_start_allocation_allowance_bytes"])
        self.assertEqual(profile["restart_dma_admission_free_bytes"],
                         profile["dma_admission_free_bytes"] + profile["restart_allocation_allowance_bytes"])

    def test_combined_rak_budgets_uart_beside_exclusive_ethernet_and_ota(self):
        defines = {
            "RAK_4631": 1, "ETHERNET_ENABLED": 1, "OTA_RAK_AUTO_STORE": 1,
            "ENABLE_OTA": 1, "MESHCORE_OTA_DEVICE_DEFLATE": 1,
            "WITH_RS232_BRIDGE": "Serial2", "DISPLAY_CLASS": "SSD1306Display",
        }
        legacy = ram.requirements("NRF52_PLATFORM", defines,
                                  "RAK_4631_repeater_unified_lora_ota")
        combined = ram.requirements("NRF52_PLATFORM", {
            **defines, "RAK4631_COMBINED_ETHERNET": 1,
        }, "RAK_4631_repeater_unified_lora_ota")
        components = combined["components"]
        self.assertEqual(components["uart_bridge_and_driver"], 3072)
        self.assertEqual(components["ethernet_or_external_ota_workspaces"], 11344)
        self.assertNotIn("ota_encoder_workspace", components)
        self.assertNotIn("ota_self_source_scratch", components)
        self.assertEqual(components["display_pixels_and_driver"], 2048)
        self.assertEqual(combined["required_heap_bytes"] - legacy["required_heap_bytes"], 3072)
        self.assertEqual(components["loop_and_callback_stacks"], 8192 + 3072)
        self.assertEqual(components["flood_filter_table"], 63 * 300 + 16)
        # An alternate port selects the same bridge instead of allocating two.
        alternate = ram.requirements("NRF52_PLATFORM", {
            **defines, "RAK4631_COMBINED_ETHERNET": 1,
            "WITH_RS232_BRIDGE_ALT": "Serial1",
        }, "RAK_4631_repeater_unified_lora_ota")
        self.assertEqual(alternate, combined)

    def test_combined_rak_worker_budget_tracks_stack_and_encoder_overrides(self):
        defines = {
            "RAK_4631": 1, "ETHERNET_ENABLED": 1, "OTA_RAK_AUTO_STORE": 1,
            "RAK4631_COMBINED_ETHERNET": 1, "ENABLE_OTA": 1,
            "MESHCORE_OTA_DEVICE_DEFLATE": 1,
        }
        for words, expected in ((1024, 11344), (1536, 11344), (2048, 11344), (3072, 13120)):
            with self.subTest(words=words):
                policy = ram.requirements("NRF52_PLATFORM", {
                    **defines, "RAK4631_ETHERNET_TASK_STACK_WORDS": words,
                }, "RAK_4631_sensor")
                self.assertEqual(policy["components"]["ethernet_or_external_ota_workspaces"], expected)
                self.assertGreaterEqual(policy["required_contiguous_bytes"], expected)
        no_encoder = ram.requirements("NRF52_PLATFORM", {
            **defines, "MESHCORE_OTA_DEVICE_DEFLATE": 0,
        }, "RAK_4631_sensor")
        self.assertEqual(no_encoder["components"]["ethernet_or_external_ota_workspaces"], 10304)
        for bits in (7, 8, 9, 10):
            with self.subTest(bits=bits):
                policy = ram.requirements("NRF52_PLATFORM", {
                    **defines, "MESHCORE_OTA_DEFLATE_HASH_BITS": bits,
                }, "RAK_4631_sensor")
                self.assertEqual(policy["components"]["ethernet_or_external_ota_workspaces"],
                                 max(4928, (1 << bits) * 2 + 16 + 4096 + 32 + 6176))
        for capacity, expected in ((512, 11344), (1024, 11344), (2048, 11344)):
            with self.subTest(tx_capacity=capacity):
                policy = ram.requirements("NRF52_PLATFORM", {
                    **defines, "ETHERNET_CLI_TX_BUFFER_BYTES": capacity,
                }, "RAK_4631_sensor")
                self.assertEqual(policy["components"]["ethernet_or_external_ota_workspaces"], expected)

    def test_bounded_source_proofs_reserve_real_workspace_without_cutting_other_allowances(self):
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
            for full in (False, True):
                for psram in (False, True):
                    flags = {"ENABLE_OTA": 1, "WIFI_SSID": "test", "BLE_PIN_CODE": 123456,
                             "WITH_MQTT_BRIDGE": 1}
                    if full:
                        flags["COMPANION_RADIO_FULL"] = 1
                    if psram:
                        flags["BOARD_HAS_PSRAM"] = 1
                    with self.subTest(platform=platform, full=full, psram=psram):
                        legacy = ram.requirements(platform, {**flags, "OTA_BOUNDED_SOURCE_PROOFS": 0},
                                                  "board_companion_radio_full")
                        selected = ram.requirements(platform, flags, "board_companion_radio_full")
                        expected = 1536 if platform == "ESP32_PLATFORM" and full and not psram else (
                            16384 if platform == "ESP32_PLATFORM" else 4096)
                        self.assertEqual(selected["components"]["ota_generic_source_buffers"],
                                         expected + 2048 + 32)
                        for name, amount in legacy["components"].items():
                            if name != "ota_generic_source_buffers":
                                self.assertEqual(selected["components"][name], amount)
        for custom in (0, 3, 32768):
            flags = {"ENABLE_OTA": 1, "COMPANION_RADIO_FULL": 1, "OTA_PROOFGEN_SCRATCH": custom}
            extended = ram.requirements("ESP32_PLATFORM", flags, "board_companion_radio_full")
            self.assertEqual(extended["components"]["ota_generic_source_buffers"], custom + 2048 + 32)
            with self.assertRaises(ValueError):
                ram.requirements("ESP32_PLATFORM", {**flags, "OTA_BOUNDED_SOURCE_PROOFS": 1},
                                 "board_companion_radio_full")
        odd_limit = ram.requirements("ESP32_PLATFORM", {
            "ENABLE_OTA": 1, "COMPANION_RADIO_FULL": 1, "OTA_PROOFGEN_SCRATCH": 16383,
        }, "board_companion_radio_full")
        self.assertEqual(odd_limit["components"]["ota_generic_source_buffers"], 1536 + 2048 + 32)
        for invalid in (-1, 2, "invalid"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ram.requirements("ESP32_PLATFORM", {
                    "ENABLE_OTA": 1, "OTA_BOUNDED_SOURCE_PROOFS": invalid,
                }, "board_companion_radio_full")

    def test_compact_tables_keep_capacity_and_reserve_active_ota_allocations(self):
        for platform, leaf_limit in (("NRF52_PLATFORM", 1536), ("ESP32_PLATFORM", 1536),
                                     ("RP2040_PLATFORM", 4096), ("STM32_PLATFORM", 4096)):
            with self.subTest(platform=platform):
                flags = {"ENABLE_OTA": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
                         "MAX_RECENT_REPEATERS": 512}
                compact = ram.requirements(platform, flags, "board_repeater")["components"]
                self.assertEqual(compact["client_table"], 32 * 284 + 16)
                self.assertEqual(compact["ota_generic_source_buffers"], leaf_limit + 2048 + 32)
                full = ram.requirements(platform, {**flags, "MESH_CLIENT_REPEATER_ONLY": 0},
                                        "board_repeater")["components"]
                self.assertEqual(full["client_table"] - compact["client_table"], 36 * 32)
                if platform == "ESP32_PLATFORM":
                    self.assertEqual(compact["neighbor_history"], 512 * 9 + 16)
        sd = ram.requirements("NRF52_PLATFORM", {"ENABLE_OTA": 1, "OTA_SD_STORE": 1},
                              "board_repeater")["components"]
        self.assertEqual(sd["ota_generic_source_buffers"], 8192 + 2048 + 32)
        custom = ram.requirements("NRF52_PLATFORM", {
            "ENABLE_OTA": 1, "OTA_PROOFGEN_SCRATCH": 12000, "OTA_MAX_BLOCK": 1024,
        }, "board_repeater")["components"]
        self.assertEqual(custom["ota_generic_source_buffers"], 12000 + 1024 + 32)
        for default, expected in ((256, 256), (2048, 2048)):
            parts = ram.requirements("ESP32_PLATFORM", {
                "MESH_RAM_DEFAULT_RECENT_REPEATERS": default,
            }, "board_repeater")["components"]
            self.assertEqual(parts["neighbor_history"], expected * 9 + 16)
        disabled = ram.requirements("ESP32_PLATFORM", {"MESH_ENABLE_RECENT_REPEATERS": 0},
                                    "board_repeater")["components"]
        self.assertEqual(disabled["neighbor_history"], 0)
        s3 = ram.requirements("ESP32_PLATFORM", {}, "board_repeater")
        self.assertGreaterEqual(s3["required_contiguous_bytes"], 2048 * 9 + 16)
        alias = ram.requirements("ESP32_PLATFORM", {"MESH_CLIENT_REPEATER_ONLY": 1},
                                 "custom_companion_alias")
        self.assertEqual(alias["components"]["client_table"], 32 * 284 + 16)
        self.assertEqual(alias["components"]["flood_filter_table"], 63 * 300 + 16)
        self.assertEqual(alias["components"]["neighbor_history"], 2048 * 9 + 16)
        self.assertEqual(alias["components"]["radio_packet_pool"], 10240)

    def test_tlora_full_rtc_history_and_terminal_budget(self):
        flags = {"MESH_CLIENT_REPEATER_ONLY": 1, "CONFIG_IDF_TARGET_ESP32": 1,
                 "ESP32_PLATFORM": 1, "LILYGO_TLORA": 1,
                 "MESHCORE_EXPANDED_PARTITION_PROFILE": 1,
                 "MAX_RECENT_REPEATERS": 256, "ADMIN_PASSWORD": "test",
                 "WIFI_SSID": "test"}
        before = ram.requirements("ESP32_PLATFORM", flags, "tlora_repeater")["components"]
        after = ram.requirements("ESP32_PLATFORM", {**flags,
             "MESH_RECENT_REPEATERS_RTC_SLOW": 1,
             "MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY": 2048}, "tlora_repeater")["components"]
        self.assertEqual(before["neighbor_history"], 256 * 9 + 16)
        self.assertNotIn("neighbor_history", after)
        self.assertEqual(before["browser_terminal_scrollback"], 4096)
        self.assertEqual(after["browser_terminal_scrollback"], 2048)
        self.assertEqual(before["flood_filter_table"], after["flood_filter_table"])
        for invalid in ({"MAX_RECENT_REPEATERS": 512}, {"LILYGO_TLORA": 0},
                        {"FLOOD_ADVERT_RTC_SLOW_SLOTS": 1},
                        {"MESHCORE_EXPANDED_PARTITION_PROFILE": 0}):
            value = {**flags, "MESH_RECENT_REPEATERS_RTC_SLOW": 1, **invalid}
            if invalid == {"LILYGO_TLORA": 0}: value.pop("LILYGO_TLORA")
            with self.subTest(value=value), self.assertRaises(ValueError):
                ram.requirements("ESP32_PLATFORM", value, "tlora_repeater")

    def test_wireless_tracker_terminal_budget_retains_flood_rules(self):
        flags = {"MESH_CLIENT_REPEATER_ONLY": 1, "ADMIN_PASSWORD": "test",
                 "WIFI_SSID": "test", "FLOOD_PACKET_FILTER_SLOTS": 63,
                 "MAX_NEIGHBOURS": 254}
        before = ram.requirements("ESP32_PLATFORM", flags, "Heltec_Wireless_Tracker_repeater")
        after = ram.requirements("ESP32_PLATFORM", {**flags,
            "MESH_TERMINAL_INITIAL_OUTPUT_CAPACITY": 2048}, "Heltec_Wireless_Tracker_repeater")
        self.assertEqual(before["components"]["browser_terminal_scrollback"], 4096)
        self.assertEqual(after["components"]["browser_terminal_scrollback"], 2048)
        self.assertEqual(before["components"]["flood_filter_table"], after["components"]["flood_filter_table"])
        self.assertEqual(before["required_heap_bytes"] - after["required_heap_bytes"], 2048)

    def test_qualified_esp32_repeaters_stream_source_proofs_without_cutting_other_budgets(self):
        for psram in (False, True):
            flags = {"ENABLE_OTA": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
                     "WITH_MQTT_BRIDGE": 1, "WIFI_SSID": "test", "MAX_CLIENTS": 32}
            if psram:
                flags["BOARD_HAS_PSRAM"] = 1
            with self.subTest(psram=psram):
                legacy = ram.requirements("ESP32_PLATFORM", {
                    **flags, "OTA_BOUNDED_SOURCE_PROOFS": 0}, "board_repeater")["components"]
                selected = ram.requirements("ESP32_PLATFORM", flags, "board_repeater")["components"]
                expected = 1536
                self.assertEqual(selected["ota_generic_source_buffers"], expected + 2048 + 32)
                for name, amount in legacy.items():
                    if name != "ota_generic_source_buffers":
                        self.assertEqual(selected[name], amount)

    def test_nrf52_repeater_proofs_keep_sensor_profiles_limits_and_other_reserves(self):
        for full_sensors in (False, True):
            for store in ({}, {"OTA_RAK_AUTO_STORE": 1}, {"OTA_SD_STORE": 1}):
                flags = {"ENABLE_OTA": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
                         "ENV_INCLUDE_BME680_BSEC": int(full_sensors), **store}
                for scratch in (None, 4, 4096, 8192, 16383, 16384, 32768):
                    if scratch is not None:
                        flags["OTA_PROOFGEN_SCRATCH"] = scratch
                    with self.subTest(full_sensors=full_sensors, store=store, scratch=scratch):
                        legacy = ram.requirements("NRF52_PLATFORM", {
                            **flags, "OTA_BOUNDED_SOURCE_PROOFS": 0}, "RAK_3401_repeater")
                        selected = ram.requirements("NRF52_PLATFORM", flags, "RAK_3401_repeater")
                        leaf_limit = scratch if scratch is not None else (8192 if "OTA_SD_STORE" in store else 4096)
                        workspace = 1536 if 4 <= leaf_limit <= 16384 else leaf_limit
                        self.assertEqual(selected["components"]["ota_generic_source_buffers"],
                                         workspace + 2048 + 32)
                        for name, amount in legacy["components"].items():
                            if name != "ota_generic_source_buffers":
                                self.assertEqual(selected["components"][name], amount)
                        self.assertEqual(legacy["required_heap_bytes"] - selected["required_heap_bytes"],
                                         leaf_limit - workspace)
                        self.assertEqual(legacy["required_contiguous_bytes"],
                                         selected["required_contiguous_bytes"])

    def test_rak_repeater_compact_rule_and_oled_budgets_keep_every_other_reserve(self):
        flags = {
            "ENABLE_OTA": 1, "OTA_RAK_AUTO_STORE": 1, "OTA_QSPI_STORE": 1,
            "OTA_RAK15002_SD_STORE": 1, "OTA_FLASH_STORE": 1,
            "MESHCORE_OTA_DEVICE_DEFLATE": 1, "MESH_CLIENT_REPEATER_ONLY": 1,
            "DISPLAY_CLASS": "SSD1306Display",
        }
        selected = ram.requirements("NRF52_PLATFORM", flags, "RAK_4631_repeater")
        self.assertEqual(selected["components"], {
            "loop_and_callback_stacks": 11264,
            "core_usb_filesystems_sensors": 12288,
            "sd_fat_volume_and_files": 2048,
            "radio_packet_pool": 10240,
            "client_table": 9104,
            "flood_filter_table": 63 * 300 + 16,
            "ota_generic_source_buffers": 3616,
            "ota_encoder_workspace": 1040,
            "ota_self_source_scratch": 4128,
            "display_pixels_and_driver": 2048,
            "screen_objects_and_history": 2048,
            "allocation_and_transient_margin": 4096,
        })
        self.assertEqual(selected["required_heap_bytes"], 80836)
        self.assertEqual(selected["required_contiguous_bytes"], 18916)
        # Rule compaction and same-driver OLED accounting retain the other
        # reserves. Payload-bound narrowing saves another four bytes per row.
        # Neither a one-byte deficit nor the old failed capacity can bypass
        # the existing real-ELF gate.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.elf"
            for available, expected in ((80835, False), (80836, True), (81668, True)):
                with self.subTest(available=available):
                    elf32(path, {"__HeapBase": (0x20010000, 0),
                                 "__HeapLimit": (0x20010000 + available, 0)})
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        passed = ram.check_firmware(path, "NRF52_PLATFORM", "nrf52840",
                                                    flags, "RAK_4631_repeater") == 0
                    self.assertEqual(passed, expected)

    def test_embedded_rak_oled_counts_only_proven_static_display_once(self):
        flags = {"RAK_4631": 1, "OTA_BOOTLOADER_MIGRATION": 1,
                 "DISPLAY_CLASS": "SSD1306Display", "ENABLE_OTA": 1,
                 "MESH_CLIENT_REPEATER_ONLY": 1}
        base = ram.requirements("NRF52_PLATFORM", flags, "RAK_4631_repeater")
        required = base["required_heap_bytes"]
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "firmware.elf"
            report = Path(temporary) / "memory.json"

            def run(*, available, display=(0x20001000, 168), writable=True,
                    section=1, selected=flags, target="RAK_4631_repeater",
                    kind=1, duplicate=None):
                elf32(image, {"__HeapBase": (0x20020000, 0),
                              "__HeapLimit": (0x20020000 + available, 0),
                              "display": display}, data=bytes(512), address=0x20001000,
                      symbol_sections={"display": section}, writable=writable,
                      symbol_types={"display": kind}, duplicate=duplicate)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    status = ram.check_firmware(image, "NRF52_PLATFORM", "nrf52840",
                                                selected, target, report)
                return status, json.loads(report.read_text())

            status, checked = run(available=required - 168)
            self.assertEqual(status, 0)
            self.assertEqual(checked["required_heap_bytes"], required - 168)
            self.assertEqual(checked["linked_display_driver_bytes"], 168)
            self.assertEqual(checked["components"]["display_pixels_and_driver"], 1880)
            self.assertEqual(checked["components"]["allocation_and_transient_margin"], 4096)
            self.assertEqual(checked["required_contiguous_bytes"], base["required_contiguous_bytes"])
            self.assertEqual(run(available=required - 169)[0], 1)
            for options in ({"writable": False}, {"section": 0xFFF1},
                            {"kind": 2}, {"duplicate": "display"},
                            {"display": (0x20001000, 513)},
                            {"display": (0x20020000, 168)},
                            {"target": "other_repeater"},
                            {"selected": {**flags, "OTA_BOOTLOADER_MIGRATION": 0}}):
                with self.subTest(options=options):
                    status, checked = run(available=required - 1, **options)
                    self.assertEqual(status, 1)
                    self.assertNotIn("linked_display_driver_bytes", checked)
            override = {**flags, "MESH_MIN_RUNTIME_HEAP": required + 128}
            status, checked = run(available=required + 127, selected=override)
            self.assertEqual(status, 1)
            self.assertEqual(checked["required_heap_bytes"], required + 128)

        board = (ROOT / "variants/rak4631/target.cpp").read_text()
        oled = (ROOT / "src/helpers/ui/SSD1306Display.cpp").read_text()
        self.assertIn("DISPLAY_CLASS display;", board)
        self.assertIn("SSD1306Display::FRAMEBUFFER_BYTES == 1024", oled)
        self.assertIn("SSD1306Display::FRAMEBUFFER_BYTES + 32 <= 2048", oled)

    def test_nrf52_oled_bound_matches_same_source_guard_without_changing_other_displays(self):
        defines = {
            "RAK_4631": 1, "ETHERNET_ENABLED": 1, "OTA_RAK_AUTO_STORE": 1,
            "RAK4631_COMBINED_ETHERNET": 1,
        }
        for driver, expected in (("SSD1306Display", 2048), ("SH1106Display", 4096),
                                 ("SH1107Display", 4096), ("ST7735Display", 25602)):
            with self.subTest(driver=driver):
                combined = ram.requirements("NRF52_PLATFORM", {
                    **defines, "DISPLAY_CLASS": driver,
                }, "RAK_4631_repeater")
                self.assertEqual(combined["components"]["display_pixels_and_driver"], expected)
        ordinary = ram.requirements("NRF52_PLATFORM", {
            "DISPLAY_CLASS": "SSD1306Display",
        }, "RAK_4631_repeater")
        self.assertEqual(ordinary["components"]["display_pixels_and_driver"], 2048)

    def test_esp32_fixed_oled_and_uart_bounds_preserve_full_runtime_reserves(self):
        defines = {
            "WIFI_OTA_SEEDER": 1, "WITH_MQTT_BRIDGE": 1,
            "WITH_RS232_BRIDGE": "Serial2", "WITH_ESPNOW_BRIDGE": 1,
            "ENABLE_OTA": 1, "ADMIN_PASSWORD": "test",
            "MESHCORE_OTA_DEVICE_DEFLATE": 1,
            "MESH_CLIENT_REPEATER_ONLY": 1,
            "DISPLAY_CLASS": "SSD1306Display",
        }
        full = ram.requirements("ESP32_PLATFORM", defines, "Heltec_v3_repeater_observer_mqtt")
        parts = full["components"]
        self.assertEqual(full["required_heap_bytes"], 201652)
        self.assertEqual(parts["display_pixels_and_driver"], 2048)
        self.assertEqual(parts["uart_bridge_and_driver"], 3072 + 4096)
        self.assertEqual(parts["wireless_stacks"], 49152)
        self.assertEqual(parts["mqtt_startup_allowance"], 24576)
        self.assertEqual(parts["allocation_and_transient_margin"], 16384)
        self.assertEqual(full["required_contiguous_bytes"], 18916)
        for driver, expected in (("SH1106Display", 2048), ("SH1107Display", 4096),
                                 ("ST7735Display", 25602)):
            with self.subTest(driver=driver):
                other = ram.requirements("ESP32_PLATFORM", {
                    **defines, "DISPLAY_CLASS": driver,
                }, "Heltec_v3_repeater_observer_mqtt")
                self.assertEqual(other["components"]["display_pixels_and_driver"], expected)
                self.assertEqual(other["components"], {
                    **parts, "display_pixels_and_driver": expected,
                })
                self.assertEqual(other["required_contiguous_bytes"], max(18916, expected))

    def test_sh1106_full_mqtt_release_profile_is_linked_in_ci(self):
        workflow = (ROOT / ".github/workflows/run-unit-tests.yml").read_text()
        command = re.search(
            r"bash build\.sh build-firmware Station_G3_ESP32_r2_repeater_observer_mqtt"
            r"(?P<args>(?:\n          [^\n]+)+)", workflow)
        self.assertIsNotNone(command)
        self.assertIn("--full-exact", command["args"])
        self.assertIn("--firmware-version v1.18.0.1-halo-keymind-cascade-dev", command["args"])
        self.assertIn("--require-ota", command["args"])

    def test_source_allocation_guards_reject_growth_at_the_budget_boundary(self):
        # Compile the production guards themselves with exact-size stand-ins.
        # Actual firmware compilation additionally checks the real classes;
        # these negative cases prove future growth cannot silently bypass them.
        sources = (
            ("src/helpers/bridges/RS232Bridge.cpp", "RS232Bridge::RS232Bridge(",
             "struct RS232Bridge { unsigned char bytes[OBJECT_BYTES]; };\n",
             ((2816, 0, True), (2817, 0, False))),
            ("src/helpers/bridges/UDPBridge.cpp", "UDPBridge::UDPBridge(",
             "namespace mesh { namespace bridge { namespace udp {} } }\n"
             "struct UDPBridge { unsigned char bytes[OBJECT_BYTES]; };\n",
             ((3072, 0, True), (3073, 0, False))),
            ("src/helpers/ui/SSD1306Display.cpp", "bool SSD1306Display::i2c_probe(",
             "struct SSD1306Display { unsigned char bytes[OBJECT_BYTES]; "
             "static constexpr unsigned FRAMEBUFFER_BYTES = FRAME_BYTES; };\n",
             ((992, 1024, True), (993, 1024, False), (124, 1025, False))),
            ("src/helpers/ui/SH1106Display.cpp", "bool SH1106Display::i2c_probe(",
             "struct Adafruit_I2CDevice { unsigned char bytes[16]; };\n"
             "struct SH1106Display { unsigned char bytes[OBJECT_BYTES]; "
             "static constexpr unsigned FRAMEBUFFER_BYTES = FRAME_BYTES; };\n",
             ((944, 1024, True), (945, 1024, False), (124, 1025, False))),
        )
        for path, end, stand_in, cases in sources:
            prefix = (ROOT / path).read_text().split(end, 1)[0]
            prefix = "\n".join(line for line in prefix.splitlines()
                               if not line.lstrip().startswith("#include"))
            if path.endswith(("RS232Bridge.cpp", "UDPBridge.cpp")):
                prefix += "\n#endif\n"  # The feature guard also encloses the methods below.
            for platform in (("ESP32=1", "ESP32_PLATFORM=1"),
                             ("NRF52_PLATFORM=1", "RAK4631_COMBINED_ETHERNET=1"),
                             ("NRF52_PLATFORM=1",)):
                for size, frame, bounded in cases:
                    with self.subTest(path=path, platform=platform, size=size, frame=frame):
                        result = subprocess.run([
                            "c++", "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                            "-DWITH_RS232_BRIDGE=1", "-DWITH_UDP_BRIDGE=1", f"-DOBJECT_BYTES={size}",
                            f"-DFRAME_BYTES={frame}", *["-D" + flag for flag in platform],
                        ], input=stand_in + prefix, text=True, capture_output=True)
                        enforced = "ESP32=1" in platform or ("NRF52_PLATFORM=1" in platform
                            and path.endswith("SSD1306Display.cpp")) or (
                            "RAK4631_COMBINED_ETHERNET=1" in platform
                            and not path.endswith(("SH1106Display.cpp", "UDPBridge.cpp")))
                        self.assertEqual(result.returncode == 0, bounded or not enforced,
                                         result.stderr)

    def test_combined_rak_budget_rejects_unmatched_hardware_or_short_task_stack(self):
        defines = {
            "RAK_4631": 1, "ETHERNET_ENABLED": 1, "OTA_RAK_AUTO_STORE": 1,
            "RAK4631_COMBINED_ETHERNET": 1,
        }
        for missing in ("RAK_4631", "ETHERNET_ENABLED", "OTA_RAK_AUTO_STORE"):
            invalid = {name: value for name, value in defines.items() if name != missing}
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "adaptive nRF52 storage"):
                ram.requirements("NRF52_PLATFORM", invalid, "RAK_4631_repeater")
        with self.assertRaisesRegex(ValueError, "adaptive nRF52 storage"):
            ram.requirements("ESP32_PLATFORM", defines, "esp_repeater")
        for words in (0, 512, 1023):
            with self.subTest(words=words), self.assertRaisesRegex(ValueError, "1024 stack words"):
                ram.requirements("NRF52_PLATFORM", {
                    **defines, "RAK4631_ETHERNET_TASK_STACK_WORDS": words,
                }, "RAK_4631_repeater")
        for capacity in (0, 256, 511, 2049):
            with self.subTest(tx_capacity=capacity), self.assertRaisesRegex(ValueError, "512..2048 bytes"):
                ram.requirements("NRF52_PLATFORM", {
                    **defines, "ETHERNET_CLI_TX_BUFFER_BYTES": capacity,
                }, "RAK_4631_repeater")

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
        # The actual buffer methods contain their own platform/owner guards.
        # Compile the complete outer branch rather than its first nested #if.
        end, depth = begin, 0
        for line in source[begin:].splitlines(keepends=True):
            directive = line.lstrip()
            if directive.startswith("#if"):
                depth += 1
            elif directive.startswith("#endif"):
                depth -= 1
            end += len(line)
            if depth == 0:
                break
        else:
            self.fail("unterminated manual-stage buffer platform branch")
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
            for host_kiss, boundary in ((0, 73728), (1, 74530)):
                selected = {**flags, "MESH_ENABLE_HOST_KISS": host_kiss}
                for available, accepted in ((54724, False), (boundary - 1, False),
                                            (boundary, True), (boundary + 332, True)):
                    with self.subTest(host_kiss=host_kiss, available=available):
                        elf32(path, {"__HeapBase": (0x2003F800 - available, 0), "__HeapLimit": (0x2003F800, 0)})
                        result = ram.check_firmware(path, "NRF52_PLATFORM", "nrf52840", selected,
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

    def test_optional_rak_sd_counts_lazy_volume_and_files_without_hiding_source_scratch(self):
        flags = {"ENABLE_OTA": 1, "OTA_SD_STORE": 1}
        base = ram.requirements("NRF52_PLATFORM", flags, "RAK_4631_repeater")
        flags["OTA_RAK15002_SD_STORE"] = 1
        sd = ram.requirements("NRF52_PLATFORM", flags, "RAK_4631_repeater")
        self.assertEqual(sd["required_heap_bytes"] - base["required_heap_bytes"], 2048)
        self.assertEqual(sd["components"]["sd_fat_volume_and_files"], 2048)
        self.assertEqual(sd["components"]["ota_generic_source_buffers"],
                         base["components"]["ota_generic_source_buffers"])

    def test_optional_sd_room_excludes_workspaces_but_companion_counts_worker_additively(self):
        flags = {"ENABLE_OTA": 1, "OTA_SD_STORE": 1, "OTA_RAK15002_SD_STORE": 1,
                 "ETHERNET_ENABLED": 1, "RAK_4631": 1, "OTA_RAK_AUTO_STORE": 1,
                 "MESHCORE_OTA_DEVICE_DEFLATE": 1}
        room = ram.requirements("NRF52_PLATFORM", flags, "RAK_4631_room_server")
        parts = room["components"]
        self.assertIn("ethernet_or_external_ota_workspaces", parts)
        self.assertEqual(parts["ethernet_or_external_ota_workspaces"], 15440)
        self.assertEqual(parts["sd_fat_volume_and_files"], 2048)
        self.assertNotIn("ota_generic_source_buffers", parts)
        large = ram.requirements("NRF52_PLATFORM", {**flags, "RAK4631_ETHERNET_TASK_STACK_WORDS": 4096},
                                 "RAK_4631_room_server")
        self.assertEqual(large["components"]["ethernet_or_external_ota_workspaces"], 17216)
        companion = ram.requirements("NRF52_PLATFORM", {**flags, "OTA_SEEDER_ONLY": 1},
                                     "RAK_4631_companion_radio_full")
        self.assertNotIn("ethernet_or_external_ota_workspaces", companion["components"])
        self.assertIn("ota_generic_source_buffers", companion["components"])
        self.assertEqual(companion["components"]["ethernet_companion_worker"], 4096 + 256)
        without_eth = ram.requirements("NRF52_PLATFORM", {
            **{key: value for key, value in flags.items() if key != "ETHERNET_ENABLED"},
            "OTA_SEEDER_ONLY": 1}, "RAK_4631_companion_radio_full")
        self.assertEqual(companion["required_heap_bytes"] - without_eth["required_heap_bytes"], 4352)
        self.assertEqual(companion["components"]["ota_generic_source_buffers"],
                         without_eth["components"]["ota_generic_source_buffers"])

    def test_companion_rak_worker_budget_covers_legacy_and_stack_overrides(self):
        flags = {"ETHERNET_ENABLED": 1, "ETHERNET_USE_RAK13800": 1}
        base = ram.requirements("NRF52_PLATFORM", {}, "RAK_4631_companion_radio_ethernet")
        for words in (1024, 2048, 8192):
            with self.subTest(words=words):
                worker = ram.requirements("NRF52_PLATFORM", {
                    **flags, "RAK4631_ETHERNET_TASK_STACK_WORDS": words},
                    "RAK_4631_companion_radio_ethernet")
                expected = words * 4 + 256
                self.assertEqual(worker["required_heap_bytes"] - base["required_heap_bytes"], expected)
                self.assertEqual(worker["components"]["ethernet_companion_worker"], expected)
                self.assertGreaterEqual(worker["required_contiguous_bytes"], expected)
        for words in (0, 1023, 65536):
            with self.subTest(words=words), self.assertRaisesRegex(ValueError, "1024..65535"):
                ram.requirements("NRF52_PLATFORM", {
                    **flags, "RAK4631_ETHERNET_TASK_STACK_WORDS": words},
                    "RAK_4631_companion_radio_ethernet")
        for platform, defines, target in (
                ("ESP32_PLATFORM", flags, "esp32_companion_radio_full"),
                ("NRF52_PLATFORM", {"ETHERNET_ENABLED": 1}, "other_companion"),
                ("NRF52_PLATFORM", flags, "RAK_4631_repeater")):
            with self.subTest(platform=platform, defines=defines, target=target):
                self.assertNotIn("ethernet_companion_worker",
                                 ram.requirements(platform, defines, target)["components"])

    def test_only_rak_sd_full_companion_counts_large_name_lookup(self):
        flags = {"ENABLE_OTA": 1, "OTA_SD_STORE": 1, "OTA_RAK15002_SD_STORE": 1,
                 "OTA_TARGET_NAME_FRONT_CODED": 1, "COMPANION_RADIO_FULL": 1, "OTA_SEEDER_ONLY": 1}
        full = ram.requirements("NRF52_PLATFORM", flags, "RAK_4631_companion_radio_full")
        self.assertEqual(full["components"]["ota_target_name_lookup"], 4112)
        for missing in ("OTA_RAK15002_SD_STORE", "OTA_TARGET_NAME_FRONT_CODED"):
            other = ram.requirements("NRF52_PLATFORM", {key: value for key, value in flags.items() if key != missing},
                                     "RAK_4631_companion_radio_full")
            self.assertNotIn("ota_target_name_lookup", other["components"])
        receiver = ram.requirements("NRF52_PLATFORM", {key: value for key, value in flags.items()
                                     if key not in ("COMPANION_RADIO_FULL", "OTA_SEEDER_ONLY")},
                                     "RAK_4631_repeater_unified_lora_ota")
        self.assertNotIn("ota_target_name_lookup", receiver["components"])

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

    def test_affected_esp32_full_overlay_keeps_documented_capacity_and_transports(self):
        for target in ("Heltec_v3_companion_radio_full", "Xiao_C3_companion_radio_full",
                       "Heltec_ct62_companion_radio_full",
                       "heltec_tracker_v2_companion_radio_full_femon",
                       "heltec_tracker_v2_companion_radio_full_femoff",
                       "generic_espnow_companion_radio_full",
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
                self.assertIn("-DMAX_CONTACTS=100", result.stdout)
                if target == "Heltec_v3_companion_radio_full":
                    self.assertNotIn("-DOFFLINE_QUEUE_SIZE=", result.stdout)
                else:
                    self.assertIn("-DOFFLINE_QUEUE_SIZE=224", result.stdout)
                if target in ("Xiao_C3_companion_radio_full", "Heltec_ct62_companion_radio_full",
                              "heltec_tracker_v2_companion_radio_full_femon",
                              "heltec_tracker_v2_companion_radio_full_femoff"):
                    self.assertIn("-DOTA_SHARED_COMPANION_QUEUE=1", result.stdout)
                    self.assertIn("-DONE_KEY_DM_COMPACT_HELD=1", result.stdout)
                else:
                    self.assertNotIn("-DOTA_SHARED_COMPANION_QUEUE", result.stdout)
            if target in ("Heltec_v3_companion_radio_full", "generic_espnow_companion_radio_full"):
                self.assertNotIn("-DONE_KEY_DM_COMPACT_HELD", result.stdout)
            if target == "generic_espnow_companion_radio_full":
                self.assertIn("-DMESH_TERMINAL_INITIAL_OUTPUT_CAPACITY=2048", result.stdout)
            if target == "Heltec_Wireless_Paper_companion_radio_full":
                self.assertIn("-DONE_KEY_DM_COMPACT_HELD=1", result.stdout)
            self.assertNotIn("-DOTA_PRESERVE_UNREAD_ON_BORROW=1", result.stdout)
            if target == "Heltec_ct62_companion_radio_full":
                self.assertIn("-DCOMPANION_RTC_FAST_OFFLINE_FRAMES=32", result.stdout)
            self.assertIn("-DWIFI_OTA_SEEDER=1", result.stdout)
            self.assertIn("-DBLE_PIN_CODE=123456", result.stdout)

    def test_wsl3_full_moves_queue_to_rtc_without_reducing_capacity(self):
        result = subprocess.run(["bash", "-c", '''
source build.sh
PIO_ENV_PLATFORM_BY_NAME["$1"]=ESP32_PLATFORM
pio_env_option_contains() { return 0; }
requires_esp32_companion_full_ota_fallback() { return 1; }
apply_companion_radio_full_profile "$1" "$1"
printf '%s\\n' "$PLATFORMIO_BUILD_FLAGS"
''', "test", "Heltec_WSL3_companion_radio_full"], cwd=ROOT, text=True,
            capture_output=True, check=True)
        self.assertIn("-DCOMPANION_RTC_FAST_OFFLINE_FRAMES=32", result.stdout)
        self.assertIn("-DCOMPANION_RTC_SLOW_OFFLINE_FRAMES=32", result.stdout)
        self.assertNotIn("-DMAX_CONTACTS=100", result.stdout)
        self.assertNotIn("-DOFFLINE_QUEUE_SIZE=", result.stdout)
        self.assertNotIn("-DOTA_SHARED_COMPANION_QUEUE", result.stdout)
        self.assertIn("-DWIFI_OTA_SEEDER=1", result.stdout)
        self.assertIn("-DBLE_PIN_CODE=123456", result.stdout)

    def test_compact_held_dm_is_limited_to_selected_full_layouts(self):
        selected = {
            "Heltec_t1_companion_radio_full": "NRF52_PLATFORM",
            "Heltec_Wireless_Paper_companion_radio_full": "ESP32_PLATFORM",
            "Heltec_Wireless_Tracker_companion_radio_full": "ESP32_PLATFORM",
            "LilyGo_TLora_V2_1_1_6_companion_radio_full": "ESP32_PLATFORM",
            "MKE_s3_companion_radio_full": "ESP32_PLATFORM",
            "nibble_screen_connect_companion_radio_full_": "ESP32_PLATFORM",
            "nibble_zero_connect_companion_radio_full_": "ESP32_PLATFORM",
            "ThinkNode_M5_companion_radio_full": "ESP32_PLATFORM",
        }
        for target, platform in selected.items():
            with self.subTest(target=target):
                result = subprocess.run(["bash", "-c", '''
source build.sh
PIO_ENV_PLATFORM_BY_NAME["$1"]="$2"
pio_env_option_contains() { return 0; }
requires_esp32_companion_full_ota_fallback() { return 1; }
apply_companion_radio_full_profile "$1" "$1"
printf '%s\\n' "$PLATFORMIO_BUILD_FLAGS"
''', "test", target, platform], cwd=ROOT, text=True, capture_output=True, check=True)
                self.assertIn("-DONE_KEY_DM_COMPACT_HELD=1", result.stdout)
                if target in ("Heltec_Wireless_Tracker_companion_radio_full",
                              "nibble_screen_connect_companion_radio_full_",
                              "nibble_zero_connect_companion_radio_full_"):
                    self.assertIn("-DOTA_SHARED_COMPANION_QUEUE=1", result.stdout)
                    self.assertIn("-DOTA_PRESERVE_UNREAD_ON_BORROW=1", result.stdout)
                else:
                    self.assertNotIn("-DOTA_PRESERVE_UNREAD_ON_BORROW=1", result.stdout)

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
