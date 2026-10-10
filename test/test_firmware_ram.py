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

    def test_heap_tables_and_ota_remain_in_runtime_budget(self):
        policy = ram.requirements("ESP32_PLATFORM", {
            "ENABLE_OTA": 1, "OTA_HEAP_CONTEXT": 1,
        }, "GEPRC_Linkflow_900_repeater")
        self.assertEqual(policy["components"]["client_table"], 32 * 320 + 16)
        self.assertEqual(policy["components"]["flood_filter_table"], 63 * 192 + 16)
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

    def test_combined_uart_reserves_heap_beside_mqtt_before_enable(self):
        defines = {"WITH_MQTT_BRIDGE": 1, "WIFI_OTA_SEEDER": 1}
        mqtt = ram.requirements("ESP32_PLATFORM", defines, "v3_repeater")
        combined = ram.requirements("ESP32_PLATFORM", {
            **defines, "WITH_RS232_BRIDGE": "Serial2", "RS232_BRIDGE_MERGED": 1,
        }, "v3_repeater")
        self.assertEqual(combined["required_heap_bytes"] - mqtt["required_heap_bytes"], 2560 + 4096)
        self.assertEqual(combined["components"]["uart_bridge_and_driver"], 2560 + 4096)
        # A selectable alternate port does not create two UART owners.
        alternate = ram.requirements("ESP32_PLATFORM", {
            **defines, "WITH_RS232_BRIDGE": "Serial2", "RS232_BRIDGE_MERGED": 1,
            "WITH_RS232_BRIDGE_ALT": "Serial1",
        }, "v3_repeater")
        self.assertEqual(alternate, combined)

    def test_mqtt_startup_gate_distinguishes_maximum_slot_stack_buffer_capacity(self):
        flags = {"WITH_MQTT_BRIDGE": 1}
        plain = ram.requirements("ESP32_PLATFORM", flags, "v4_companion")
        psram = ram.requirements("ESP32_PLATFORM", {**flags, "BOARD_HAS_PSRAM": 1},
                                 "v4_companion")
        # The potential five-slot allowance is reported separately: additional
        # clients allocate lazily and must pass the production runtime gate.
        self.assertEqual(plain["required_heap_bytes"], psram["required_heap_bytes"])
        self.assertEqual(plain["components"]["mqtt_startup_allowance"], 24576)
        self.assertEqual(psram["components"]["mqtt_startup_allowance"], 24576)
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
            ("cold_start_dma_admission_contiguous_bytes", "kColdStartLargestBytes"),
            ("restart_allocation_allowance_bytes", "kRestartAllowanceBytes"),
            ("restart_dma_admission_contiguous_bytes", "kRestartLargestBytes"),
            ("dma_admission_free_bytes", "kDmaReserveBytes"),
            ("dma_admission_contiguous_bytes", "kDmaLargestBytes"),
            ("tls_internal_admission_free_bytes", "kTlsInternalFreeBytes"),
            ("tls_internal_admission_contiguous_bytes", "kTlsRecordAllocationBytes"),
        ):
            with self.subTest(key=key):
                self.assertIn(f"{name} = {profile[key]};", source)
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
        self.assertEqual(components["uart_bridge_and_driver"], 2560)
        self.assertEqual(components["ethernet_or_external_ota_workspaces"], 11344)
        self.assertNotIn("ota_encoder_workspace", components)
        self.assertNotIn("ota_self_source_scratch", components)
        self.assertEqual(components["display_pixels_and_driver"], 2048)
        self.assertEqual(combined["required_heap_bytes"] - legacy["required_heap_bytes"], 2560 - 2048)
        self.assertEqual(components["loop_and_callback_stacks"], 8192 + 3072)
        self.assertEqual(components["flood_filter_table"], 63 * 192 + 16)
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

    def test_compact_tables_keep_capacity_and_reserve_active_ota_allocations(self):
        for platform, leaf_limit in (("NRF52_PLATFORM", 4096), ("ESP32_PLATFORM", 16384),
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
        self.assertEqual(alias["components"]["flood_filter_table"], 63 * 192 + 16)
        self.assertEqual(alias["components"]["neighbor_history"], 2048 * 9 + 16)
        self.assertEqual(alias["components"]["radio_packet_pool"], 10240)

    def test_combined_rak_oled_bound_does_not_change_other_display_budgets(self):
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
        self.assertEqual(ordinary["components"]["display_pixels_and_driver"], 4096)

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
        self.assertEqual(full["required_heap_bytes"], 209184)
        self.assertEqual(parts["display_pixels_and_driver"], 2048)
        self.assertEqual(parts["uart_bridge_and_driver"], 2560 + 4096)
        self.assertEqual(parts["wireless_stacks"], 49152)
        self.assertEqual(parts["mqtt_startup_allowance"], 24576)
        self.assertEqual(parts["allocation_and_transient_margin"], 16384)
        self.assertEqual(full["required_contiguous_bytes"], 18464)
        for driver, expected in (("SH1106Display", 4096), ("SH1107Display", 4096),
                                 ("ST7735Display", 25602)):
            with self.subTest(driver=driver):
                other = ram.requirements("ESP32_PLATFORM", {
                    **defines, "DISPLAY_CLASS": driver,
                }, "Heltec_v3_repeater_observer_mqtt")
                self.assertEqual(other["components"]["display_pixels_and_driver"], expected)

    def test_source_allocation_guards_reject_growth_at_the_budget_boundary(self):
        # Compile the production guards themselves with exact-size stand-ins.
        # Actual firmware compilation additionally checks the real classes;
        # these negative cases prove future growth cannot silently bypass them.
        sources = (
            ("src/helpers/bridges/RS232Bridge.cpp", "RS232Bridge::RS232Bridge(",
             "struct RS232Bridge { unsigned char bytes[OBJECT_BYTES]; };\n",
             ((2304, 0, True), (2305, 0, False))),
            ("src/helpers/ui/SSD1306Display.cpp", "bool SSD1306Display::i2c_probe(",
             "struct SSD1306Display { unsigned char bytes[OBJECT_BYTES]; "
             "static constexpr unsigned FRAMEBUFFER_BYTES = FRAME_BYTES; };\n",
             ((992, 1024, True), (993, 1024, False), (124, 1025, False))),
        )
        for path, end, stand_in, cases in sources:
            prefix = (ROOT / path).read_text().split(end, 1)[0]
            prefix = "\n".join(line for line in prefix.splitlines()
                               if not line.lstrip().startswith("#include"))
            if path.endswith("RS232Bridge.cpp"):
                prefix += "\n#endif\n"  # WITH_RS232_BRIDGE also encloses the methods below.
            for platform in (("ESP32=1", "ESP32_PLATFORM=1"),
                             ("NRF52_PLATFORM=1", "RAK4631_COMBINED_ETHERNET=1"),
                             ("NRF52_PLATFORM=1",)):
                for size, frame, bounded in cases:
                    with self.subTest(path=path, platform=platform, size=size, frame=frame):
                        result = subprocess.run([
                            "c++", "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                            "-DWITH_RS232_BRIDGE=1", f"-DOBJECT_BYTES={size}",
                            f"-DFRAME_BYTES={frame}", *["-D" + flag for flag in platform],
                        ], input=stand_in + prefix, text=True, capture_output=True)
                        enforced = "ESP32=1" in platform or "RAK4631_COMBINED_ETHERNET=1" in platform
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

    def test_affected_esp32_full_overlay_keeps_documented_capacity_and_transports(self):
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
                self.assertIn("-DMAX_CONTACTS=100", result.stdout)
                if target == "Heltec_v3_companion_radio_full":
                    self.assertNotIn("-DOFFLINE_QUEUE_SIZE=", result.stdout)
                else:
                    self.assertIn("-DOFFLINE_QUEUE_SIZE=224", result.stdout)
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
