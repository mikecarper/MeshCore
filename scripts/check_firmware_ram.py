#!/usr/bin/env python3
"""Gate firmware builds on internal RAM capacity and startup allowances.

Uses real linker heap bounds on ARM and the linked ESP-IDF allocator tables on
ESP32. These are capacity checks before dynamic allocation, not hardware soak
results. MQTT uses a startup minimum with separate runtime admission; a passing
build does not qualify its maximum TLS/WSS/outbox load. PSRAM, instruction-only
RAM and reserved bootloader arenas never count as internal heap. See
docs/research/firmware_memory_budget.md for the budget policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path("scripts").resolve()
sys.path.insert(0, str(SCRIPT_DIR))
from firmware_elf import FirmwareElf

SUPPORTED = {"NRF52_PLATFORM", "ESP32_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"}
DISPLAY_HEAP = {
    "": 0, "NullDisplayDriver": 0,
    "SSD1306Display": 4096, "SH1106Display": 4096, "U8g2Display": 4096,
    "SH1107Display": 4096,
    # These drivers render directly or embed their pixel storage in globals.
    "ST7789Display": 0, "ST7789LCDDisplay": 0, "NV3001BDisplay": 0,
    "GxEPDDisplay": 0, "E213Display": 8192, "E290Display": 8192,
}

# Match MQTTBridge's default worker and its explicit SDK client task/buffers.
# The legacy 24 KiB allowance covers two clients with 512 bytes beyond the known
# stacks/buffers. It is a startup minimum, not a bound on TLS/WSS/outbox memory.
MQTT_STARTUP_ALLOWANCE = 24576
MQTT_WORKER_STACK_BYTES = 8192
MQTT_CLIENT_STACK_BYTES = 6144
MQTT_CLIENT_BUFFER_BYTES = 2 * 896


def integer(defines, name, default):
    value = str(defines.get(name, default)).strip().strip("()")
    value = re.sub(r"[uUlL]+$", "", value)
    try:
        result = int(value, 0)
    except ValueError as error:
        raise ValueError(f"memory budget needs an integer {name}, got {value!r}") from error
    if result < 0:
        raise ValueError(f"negative memory budget input {name}")
    return result


def mqtt_requirements(defines):
    psram = "BOARD_HAS_PSRAM" in defines
    slots = integer(defines, "MQTT_RUNTIME_SLOT_COUNT", 6 if psram else 3)
    if not 1 <= slots <= 6:
        raise ValueError("MQTT_RUNTIME_SLOT_COUNT must be between 1 and 6")
    worker_stack = integer(defines, "MQTT_TASK_STACK_SIZE", MQTT_WORKER_STACK_BYTES)
    if worker_stack == 0:
        raise ValueError("MQTT_TASK_STACK_SIZE must be positive")
    # Reducing slots or stack size cannot lower the existing allowance. Extra
    # slots are lazy and each attempt must pass the production admission guard.
    startup = MQTT_STARTUP_ALLOWANCE + max(0, worker_stack - MQTT_WORKER_STACK_BYTES)
    active = min(slots, 5 if psram else 2)
    potential = startup + max(0, active - 2) * (MQTT_CLIENT_STACK_BYTES + MQTT_CLIENT_BUFFER_BYTES)
    return {
        "budget_scope": "startup minimum with runtime connection admission",
        "startup_allowance_bytes": startup,
        "startup_active_slot_allowance": min(slots, 2),
        "runtime_slot_count": slots,
        "maximum_active_slots": active,
        "maximum_active_slots_without_psram": min(slots, 2),
        "worker_stack_bytes": worker_stack,
        "client_task_stack_bytes": MQTT_CLIENT_STACK_BYTES,
        "client_task_priority": 5,
        "client_rx_tx_buffer_bytes": MQTT_CLIENT_BUFFER_BYTES,
        "maximum_slot_stack_buffer_allowance_bytes": potential,
        "cold_start_allocation_allowance_bytes": 16384,
        "cold_start_dma_admission_free_bytes": 32768,
        "cold_start_dma_admission_contiguous_bytes": 8192,
        "restart_allocation_allowance_bytes": 8192,
        "restart_dma_admission_free_bytes": 24576,
        "restart_dma_admission_contiguous_bytes": 8192,
        "dma_admission_free_bytes": 16384,
        "dma_admission_contiguous_bytes": 4096,
        "tls_internal_admission_free_bytes": 61440,
        "tls_internal_admission_contiguous_bytes": 17408,
        "maximum_runtime_load_qualified": False,
        "physical_validation_performed": False,
        "limitations": "Maximum TLS/WSS/outbox workload is not covered by the startup allowance; "
                       "runtime free DMA heap and fragmentation must satisfy connection admission.",
    }


def wio_tracker_l2_display_heap(defines):
    # Both LGFXDisplay::begin()'s internal canvas and Wio's concrete begin()
    # must fit. The latter requests PSRAM, but LovyanGFX 1.2.30 explicitly
    # falls back to internal DMA memory when that allocation fails.
    zoom_text = str(defines.get("UI_ZOOM", 1)).strip().strip("()")
    zoom_text = re.sub(r"[fFlL]$", "", zoom_text)
    try:
        zoom = float(zoom_text)
    except ValueError as error:
        raise ValueError(f"memory budget needs a numeric UI_ZOOM, got {zoom_text!r}") from error
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("Wio Tracker L2 UI_ZOOM must be finite and positive")
    if integer(defines, "UI_COORD_SCALE", 1) == 0:
        raise ValueError("Wio Tracker L2 UI_COORD_SCALE must be positive")
    depth = integer(defines, "UI_BUFFER_COLOR_DEPTH", 8)
    if depth not in (1, 2, 4, 8, 16, 24, 32):
        raise ValueError("unsupported Wio Tracker L2 UI_BUFFER_COLOR_DEPTH")
    # The base constructor divides physical dimensions by zoom*coordinate
    # scale; its render path multiplies by coordinate scale again. Rounding
    # upward bounds both that canvas and Wio's fixed-8bpp logical sprite.
    width = math.ceil(320 / zoom)
    height = math.ceil(240 / zoom)
    row_bytes = (width * max(8, depth) + 7) // 8
    # Include the sprite's extra trailing pixel, up to a 256-entry RGB888
    # palette, alignment and allocation metadata, even with working PSRAM.
    return row_bytes * height + 1024


def requirements(platform, defines, target):
    if platform not in SUPPORTED:
        raise ValueError(f"no memory policy for {platform}")
    qualified_repeater = integer(defines, "MESH_CLIENT_REPEATER_ONLY", 0) != 0
    repeater = qualified_repeater or "repeater" in target.lower()
    companion = not qualified_repeater and bool(re.search(r"companion|comp_radio|comp_.*radio", target, re.I))
    full = "COMPANION_RADIO_FULL" in defines or "companion_radio_full" in target.lower()
    display = str(defines.get("DISPLAY_CLASS", "")).strip('"')
    if display == "SCIndicatorDisplay":
        # Largest supported runtime canvas is 480 square at four bits/pixel;
        # scanout is separately allocated in PSRAM, never included here.
        depth = integer(defines, "UI_BUFFER_COLOR_DEPTH", 8)
        pixels = 480 * 480 if "INDICATOR_TRANSPORT_RENDER_PROFILE" in defines else 320 * 320
        display_heap = (pixels * depth + 7) // 8 + 1024
    elif display == "WioTrackerL2Display":
        if platform != "ESP32_PLATFORM":
            raise ValueError("Wio Tracker L2 display budget requires ESP32")
        display_heap = wio_tracker_l2_display_heap(defines)
    elif display == "ST7735Display":
        depth = integer(defines, "ST7735_BUFFER_COLOR_DEPTH", 16)
        if depth not in (8, 16):
            raise ValueError("ST7735_BUFFER_COLOR_DEPTH must be 8 or 16")
        # TFT_eSprite adds one trailing pixel. The opt-in RGB332 path also
        # source-bounds its complete sprite object plus allocator metadata to
        # 1 KiB. Its 320-byte wire scanline lives on the unchanged loop stack;
        # the existing transient allowance remains additive.
        display_heap = (160 * 80 + 1) * (depth // 8)
        if depth == 8:
            display_heap += 1024
    elif display in DISPLAY_HEAP:
        display_heap = DISPLAY_HEAP[display]
    else:
        raise ValueError(f"add a runtime allocation budget for display {display!r}")
    parts = {}
    if display == "WioTrackerL2Display":
        # The pinned LGFX QSPI bus has two DMA flip buffers. RGB565 conversion
        # requests at most 2048 bytes each (2304 for RGB888); shrinking can
        # briefly retain a third. Reserve 16 KiB for those, queued descriptors
        # (up to 320 rows, including overlap while growing) and SDK bus/touch
        # driver metadata. These allocations coexist with the UI sprite.
        parts["display_dma_and_bus"] = 16384
    mqtt = None
    combined_rak_ethernet = "RAK4631_COMBINED_ETHERNET" in defines
    cooperative_rak_ethernet = not companion and (combined_rak_ethernet or (
        "OTA_RAK15002_SD_STORE" in defines and "ETHERNET_ENABLED" in defines))
    companion_rak_ethernet = (platform == "NRF52_PLATFORM" and companion
        and "ETHERNET_ENABLED" in defines and ("ETHERNET_USE_RAK13800" in defines
            or {"OTA_RAK15002_SD_STORE", "RAK_4631"}.issubset(defines)))
    if combined_rak_ethernet and (platform != "NRF52_PLATFORM"
                                 or not {"RAK_4631", "ETHERNET_ENABLED", "OTA_RAK_AUTO_STORE"}.issubset(defines)):
        raise ValueError("combined RAK Ethernet budget requires RAK4631 adaptive nRF52 storage")
    if (combined_rak_ethernet or platform == "ESP32_PLATFORM") and display == "SSD1306Display":
        # The fixed 128x64 driver allocates one 1024-byte framebuffer and
        # reuses it after power cycling. Its linked source assertion binds
        # framebuffer + complete driver object + allocator overhead to 2 KiB.
        # The driver is already linked as a global (124 bytes on ESP32), so
        # including it again retains a conservative allocation bound. Other
        # display drivers and ordinary non-ESP32 policies are unchanged.
        display_heap = 2048
    if platform == "ESP32_PLATFORM" and display == "SH1106Display":
        # Fixed 128x64 monochrome geometry: one reusable 1024-byte buffer and
        # one BusIO adapter. The local subclass retires the previous adapter
        # before begin/retry; source assertions bound the complete display,
        # adapter and allocation metadata together, without touching reserves.
        display_heap = 2048
    if platform == "NRF52_PLATFORM":
        parts["loop_and_callback_stacks"] = integer(defines, "MESH_NRF52_LOOP_STACK_WORDS", 2048) * 4 + 3072
        parts["core_usb_filesystems_sensors"] = 12288
        if "OTA_RAK15002_SD_STORE" in defines:
            # New optional RAK SD allocates one SdFs volume and separate
            # staging/archive FsFile handles lazily; production bounds the
            # complete objects plus allocation metadata to this allowance.
            parts["sd_fat_volume_and_files"] = 2048
            if full and integer(defines, "OTA_TARGET_NAME_FRONT_CODED", 0):
                # RAK Full uses larger independent Zopfli chunks for its
                # complete name index, with a rare, freed-on-return heap
                # lookup buffer instead of increasing the loop stack.
                parts["ota_target_name_lookup"] = 4096 + 16
        if "BLE_PIN_CODE" in defines:
            parts["bluetooth_worker_stacks"] = 5920
        if cooperative_rak_ethernet and "WITH_RS232_BRIDGE" in defines:
            # The bridge owns a packet/ACK duplicate table, RX/TX frames and
            # heartbeat state. The source assertion bounds the complete object
            # to 2816 bytes; retain room for allocation metadata and the UART TX semaphore.
            # UART RX/TX arrays are embedded in the already-linked globals.
            parts["uart_bridge_and_driver"] = 3072
    elif platform == "ESP32_PLATFORM":
        parts["core_tasks_usb_filesystems"] = 24576
        # MQTT joins with saved runtime credentials even without a compiled SSID
        # or OTA seeder. Count its mandatory WiFi stack once with other WiFi uses.
        wifi = 49152 if any(name in defines for name in (
            "WIFI_SSID", "WIFI_OTA_SEEDER", "WITH_MQTT_BRIDGE")) else 0
        ble = 32768 if "BLE_PIN_CODE" in defines else 0
        parts["wireless_stacks"] = max(wifi, ble) if "COMPANION_EXCLUSIVE_WIFI_BLE" in defines else wifi + ble
        if "WITH_MQTT_BRIDGE" in defines:
            mqtt = mqtt_requirements(defines)
            parts["mqtt_startup_allowance"] = mqtt["startup_allowance_bytes"]
        if "WITH_RS232_BRIDGE" in defines:
            # The UART bridge is allocated on enable, alongside WiFi/MQTT.
            # RS232Bridge.cpp bounds its object to 2816 bytes. Reserve 3072
            # including allocation metadata, plus the unchanged 4 KiB for
            # the SDK UART task, buffers and allocator overhead.
            parts["uart_bridge_and_driver"] = 3072 + 4096
        if "ENABLE_OTA" in defines:
            # Existing own-image/manual-source allowance is independent of
            # the generic folder leaf/output buffers counted below.
            parts["ota_source_scratch"] = 8192
        if ("WEBCONFIG_DISABLED" not in defines
                and ((companion and "WIFI_SSID" in defines)
                     or (not companion and "ADMIN_PASSWORD" in defines))):
            parts["browser_terminal_session"] = 2048
            if "BOARD_HAS_PSRAM" not in defines:
                # Larger replies grow on demand only while preserving 32 KiB
                # of free internal heap, and shrink after the browser reads.
                parts["browser_terminal_scrollback"] = 4096
            if "room_server" in target.lower():
                # The mailbox is exclusive to Room Server callbacks. JSON's
                # real allocator cap and one decoded maximum article coexist;
                # ordinary HTTP bodies/responses use the transient margin.
                parts["room_browser_mailbox"] = 6656 + 16
                parts["room_browser_request_workspace"] = 8192 + 2048 + 16
        if repeater:
            # Match MyMesh's platform defaults. The post-hook supplies the
            # MCU-specific classic ESP32 default before sdkconfig is included.
            default_recent = integer(defines, "MESH_RAM_DEFAULT_RECENT_REPEATERS",
                                     256 if "CONFIG_IDF_TARGET_ESP32" in defines else 2048)
            count = integer(defines, "MAX_RECENT_REPEATERS", default_recent)
            if not integer(defines, "MESH_ENABLE_RECENT_REPEATERS", 1):
                count = integer(defines, "MAX_RECENT_REPEATERS", 0)
            parts["neighbor_history"] = count * 9 + (16 if count else 0)
    else:
        parts["core_filesystems_sensors"] = 8192
    # Packet bytes plus all three queue tables and allocation overhead.
    parts["radio_packet_pool"] = 5120 if companion else 10240
    # These tables moved out of .bss, so linker heap bounds now include their
    # space. Count it as startup allocation instead; C++ assertions bind the
    # per-entry bounds to the production structures.
    if qualified_repeater or re.search(r"repeater|room_server|sensor", target, re.I) or "COMPANION_MESH_CLOCK_SYNC" in defines:
        client_bytes = 284 if integer(defines, "MESH_CLIENT_REPEATER_ONLY", 0) else 320
        parts["client_table"] = integer(defines, "MAX_CLIENTS", 32) * client_bytes + 16
    if repeater:
        engine = integer(defines, "MESH_ENABLE_FLOOD_RULE_ENGINE", int(platform != "STM32_PLATFORM"))
        slots = integer(defines, "FLOOD_PACKET_FILTER_SLOTS", 63 if engine else 16)
        parts["flood_filter_table"] = slots * (192 if engine else 40) + 16
    if "room_server" in target.lower() and integer(
            defines, "MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE", 0):
        # FloodRuleEngine keeps all 31 persisted rules in one startup heap
        # allocation. Its C++ assertion pins Entry to the budgeted 192 bytes.
        parts["room_flood_rule_table"] = 31 * 192 + 16
    if "ENABLE_OTA" in defines and "OTA_HEAP_CONTEXT" in defines:
        parts["ota_context"] = 16384 + 16
    if "ENABLE_OTA" in defines:
        # These are lazy on every platform. Freeing static arrays increases
        # linked heap capacity but cannot erase their active-operation cost.
        # Leaves are sized to the selected source, up to the unchanged limit;
        # output holds only one block. They can overlap self-serving/fetching.
        leaf_limit = integer(defines, "OTA_PROOFGEN_SCRATCH",
                             16384 if platform == "ESP32_PLATFORM" else
                             8192 if "OTA_SD_STORE" in defines else 4096)
        block_size = integer(defines, "OTA_MAX_BLOCK", 2048)
        parts["ota_generic_source_buffers"] = leaf_limit + block_size + 32
    # Match OtaDeflateConfig.h's compile eligibility and build-role override.
    # Adaptive RAK may qualify only at runtime; reserve its worst eligible case.
    # The shared role hook qualifies sources, not environment names. An
    # infrastructure recipe can inherit a Companion-named custom environment.
    device_deflate = ("ENABLE_OTA" in defines and "OTA_SEEDER_ONLY" not in defines
                      and integer(defines, "MESHCORE_OTA_DEVICE_DEFLATE", 0) != 0
                      and (platform == "ESP32_PLATFORM" or
                           (platform == "NRF52_PLATFORM" and any(name in defines for name in
                            ("OTA_QSPI_STORE", "OTA_SD_STORE", "OTA_RAK_AUTO_STORE")))))
    if device_deflate:
        hash_bits = integer(defines, "MESHCORE_OTA_DEFLATE_HASH_BITS", 9)
        if not 7 <= hash_bits <= 10:
            raise ValueError("device DEFLATE hash must have 128..1024 entries")
        # The encoder uses input offsets, not pointers: sizeof(uint16_t) per
        # bucket on every platform. This allocation is freed before radio TX.
        parts["ota_encoder_workspace"] = (1 << hash_bits) * 2 + 16
    if ("ENABLE_OTA" in defines and platform == "NRF52_PLATFORM"
            and "OTA_SEEDER_ONLY" not in defines and any(name in defines for name in (
                "OTA_QSPI_STORE", "OTA_SD_STORE", "OTA_RAK_AUTO_STORE", "OTA_TOWER_AUTO_STORE"))):
        # Own-image leaves/output also exist without a device encoder. The
        # running nRF52840 app is <1 MiB: <=512 2-KiB leaves. Compression needs
        # one block; raw diagnostic serving retains a leaf-sized output.
        parts["ota_self_source_scratch"] = 2 * 2048 + 2 * 16
    if companion_rak_ethernet:
        words = integer(defines, "RAK4631_ETHERNET_TASK_STACK_WORDS", 1024)
        if not 1024 <= words <= 65535:
            raise ValueError("RAK Companion Ethernet task requires 1024..65535 stack words")
        # Companion retains its existing statically allocated frame queue.
        # Its worker can coexist with USB/BLE folder-source buffers, so this
        # stack and TCB allowance is additive, never an exclusive OTA maximum.
        # The source bounds StaticTask_t plus allocator metadata to 256 bytes.
        parts["ethernet_companion_worker"] = words * 4 + 256
    if cooperative_rak_ethernet:
        words = integer(defines, "RAK4631_ETHERNET_TASK_STACK_WORDS", 1024)
        if words < 1024:
            raise ValueError("combined RAK Ethernet task requires at least 1024 stack words")
        tx_capacity = integer(defines, "ETHERNET_CLI_TX_BUFFER_BYTES", 512)
        if not 512 <= tx_capacity <= 2048:
            raise ValueError("combined RAK Ethernet TX queue requires 512..2048 bytes")
        # Ethernet owns the shared bus only after rejecting live/pinned OTA
        # sessions and freeing the external self-source buffers. While it owns
        # that bus, OTA encoder/self-source entry points refuse allocation.
        # On shutdown the main loop deletes the parked worker synchronously
        # and frees its TX queue before releasing ownership, so its stack
        # cannot await idle cleanup
        # while external OTA workspaces are allocated again.
        # UART can remain enabled in either mode, so its budget stays above.
        # The library's function-static DHCP object and SPI/socket globals are
        # already present in the linked image. The bounded TCP TX queue is
        # allocated only while Ethernet is enabled; reserve its capacity plus
        # 64 bytes for allocator overhead alongside the worker stack and TCB.
        external_ota = sum(parts.pop(name, 0) for name in (
            "ota_encoder_workspace", "ota_self_source_scratch", "ota_generic_source_buffers"))
        parts["ethernet_or_external_ota_workspaces"] = max(words * 4 + 256 + tx_capacity + 64,
                                                        external_ota)
    if display and display != "NullDisplayDriver":
        parts["display_pixels_and_driver"] = display_heap
        screen_budget = integer(defines, "MESH_COMPANION_SCREEN_STARTUP_BYTES", 0)
        if companion and screen_budget:
            # A target using this override has a source-side static assertion
            # that covers its concrete startup screens and allocator overhead.
            parts["screen_objects_and_history"] = screen_budget
        else:
            parts["screen_objects_and_history"] = 8192 if companion else 2048
        if companion:
            # ui-new retains 32 previews in one heap allocation. The baseline
            # covers 78 bytes per message; budget larger buffers explicitly,
            # including worst-case 8-byte Entry alignment.
            small_display = display in {
                "SSD1306Display", "SH1106Display", "ST7735Display", "U8g2Display",
            }
            small_font = integer(defines, "UI_SMALL_MESSAGE_FONT", int(small_display))
            default_preview = 161 if small_font else 78
            extra = max(0, integer(defines, "UI_MSG_PREVIEW_SIZE", default_preview) - 78)
            if extra and not screen_budget:
                parts["expanded_message_previews"] = 32 * ((extra + 7) // 8) * 8
    parts["allocation_and_transient_margin"] = 16384 if platform == "ESP32_PLATFORM" else 4096
    required = sum(parts.values())
    if platform == "NRF52_PLATFORM" and full and display == "ST7735Display":
        required = max(required, 73728)
    # A new profile may increase this budget; it cannot override it downward.
    required = max(required, integer(defines, "MESH_MIN_RUNTIME_HEAP", 0))
    largest = max(display_heap, parts["radio_packet_pool"], 8192 if platform == "ESP32_PLATFORM" else 0)
    if "expanded_message_previews" in parts:
        largest = max(largest, 8192 + parts["expanded_message_previews"])
    for name in ("client_table", "flood_filter_table", "room_flood_rule_table", "neighbor_history",
                 "ota_context", "screen_objects_and_history", "ota_encoder_workspace",
                 "ota_generic_source_buffers", "ethernet_or_external_ota_workspaces", "ota_target_name_lookup",
                 "ethernet_companion_worker"):
        largest = max(largest, parts.get(name, 0))
    if "room_browser_mailbox" in parts:
        largest = max(largest, 8192, parts["room_browser_mailbox"])
    if mqtt:
        largest = max(largest, mqtt["worker_stack_bytes"], mqtt["client_task_stack_bytes"])
    result = {"required_heap_bytes": required, "required_contiguous_bytes": largest,
              "components": parts, "display": display, "full_companion": full}
    if mqtt:
        result["mqtt"] = mqtt
    return result


def subtract_regions(regions, reserved):
    result = list(regions)
    for lower, upper in reserved:
        if lower > upper:
            raise ValueError("reversed reserved memory range")
        lower, upper = lower & ~3, (upper + 3) & ~3
        next_regions = []
        for start, end, kind in result:
            if upper <= start or lower >= end:
                next_regions.append((start, end, kind))
            else:
                if start < lower:
                    next_regions.append((start, lower, kind))
                if upper < end:
                    next_regions.append((upper, end, kind))
        result = next_regions
    merged = []
    for start, end, kind in sorted(result):
        if end - start <= 16:
            continue
        if merged and start < merged[-1][1]:
            raise ValueError("overlapping internal heap regions")
        if merged and start == merged[-1][1] and kind == merged[-1][2]:
            merged[-1] = (merged[-1][0], end, kind)
        else:
            merged.append((start, end, kind))
    return merged


def esp32_heap_regions(elf, mcu):
    count = elf.words("soc_memory_region_count")[0]
    size = elf.symbols["soc_memory_regions"][1]
    if not count or count > 256 or size % count:
        raise ValueError("invalid ESP-IDF memory-region table")
    stride = size // count
    if stride not in (16, 20):
        raise ValueError(f"unsupported ESP-IDF memory-region ABI ({stride} bytes)")
    # IDF 4 puts startup_stack/alias flags in each type; current IDF 5 puts
    # startup_stack in each region. Read the actual linked ABI and capabilities.
    type_stride = 20 if stride == 16 else 16
    type_size = elf.symbols["soc_memory_types"][1]
    if not type_size or type_size % type_stride:
        raise ValueError("unsupported ESP-IDF memory-type ABI")
    types = []
    for i in range(type_size // type_stride):
        row = elf.read(elf.address("soc_memory_types") + i * type_stride, type_stride)
        _, a, b, c = struct.unpack_from("<4I", row)
        types.append((a | b | c, bool(row[17]) if type_stride == 20 else False))
    regions = []
    for i in range(count):
        row = elf.read(elf.address("soc_memory_regions") + i * stride, stride)
        start, length, kind, _ = struct.unpack_from("<4I", row)
        if kind >= len(types) or not length or start + length > 0x100000000:
            raise ValueError("invalid ESP-IDF heap region")
        caps, startup = types[kind]
        if stride == 20:
            startup = bool(row[16])
        # MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT, excluding external and RTC RAM.
        if caps & 0x804 != 0x804 or caps & (0x400 | 0x8000):
            continue
        # New chips read a ROM reservation table at boot. Exclude their entire
        # late-reclaimed ROM-stack region so unknown silicon reservations can
        # never inflate the budget. Classic ESP32 has fixed linked reservations.
        if startup and mcu != "esp32":
            continue
        regions.append((start, start + length, kind))
    start = elf.address("soc_reserved_memory_region_start")
    end = elf.address("soc_reserved_memory_region_end")
    if end < start or (end - start) % 8 or end - start > 8192:
        raise ValueError("invalid ESP-IDF reservation table")
    reservations = list(struct.iter_unpack("<2I", elf.read(start, end - start)))
    return subtract_regions(regions, reservations)


def heap_regions(elf, platform, mcu):
    if platform == "ESP32_PLATFORM":
        return esp32_heap_regions(elf, mcu)
    if platform == "NRF52_PLATFORM":
        start, end = elf.address("__HeapBase"), elf.address("__HeapLimit")
    elif platform == "RP2040_PLATFORM":
        start, end = elf.address("__end__"), elf.address("__HeapLimit")
    elif platform == "STM32_PLATFORM":
        start = elf.address("_end")
        end = elf.address("_estack") - elf.address("_Min_Stack_Size")
    else:
        raise ValueError(f"unsupported platform {platform}")
    if not 0x20000000 <= start < end <= 0x30000000:
        raise ValueError("invalid ARM runtime heap boundaries")
    return [(start, end, 0)]


def check_firmware(elf_path, platform, mcu, defines, target, output=None):
    elf = FirmwareElf(elf_path)
    if platform == "ESP32_PLATFORM":
        defines = {**defines, "MESH_RAM_DEFAULT_RECENT_REPEATERS": 256 if str(mcu).lower() == "esp32" else 2048}
    policy = requirements(platform, defines, target)
    regions = heap_regions(elf, platform, mcu)
    available = sum(end - start for start, end, _ in regions)
    largest = max((end - start for start, end, _ in regions), default=0)
    passed = available >= policy["required_heap_bytes"] and largest >= policy["required_contiguous_bytes"]
    report = {"schema_version": 1, "target": target, "platform": platform, "mcu": mcu,
              "elf_sha256": hashlib.sha256(elf.data).hexdigest(), "passed": passed,
              "available_internal_bytes": available, "largest_internal_region_bytes": largest,
              **policy, "regions": [{"start": start, "end": end} for start, end, _ in regions],
              "scope": "Linked capacity before runtime allocation; physical boot/load/soak validation remains required."}
    if output:
        Path(output).write_text(json.dumps(report, indent=2) + "\n")
    print(f"Runtime RAM: {available:,} internal bytes available; {policy['required_heap_bytes']:,} required; "
          f"largest region {largest:,}; {'PASS' if passed else 'FAIL'}")
    if "mqtt" in policy:
        print("MQTT RAM scope: startup minimum with runtime admission; "
              "maximum TLS/WSS/outbox load is not qualified by this build check.")
    if not passed:
        print("Runtime RAM check failed: insufficient heap for enabled features. "
              "Share cold buffers or reduce allocations before publishing this build.", file=sys.stderr)
    return 0 if passed else 1


def register_platformio(env):
    definitions = {}
    for item in env.get("CPPDEFINES", []):
        if isinstance(item, (tuple, list)):
            definitions[str(item[0])] = item[1] if len(item) > 1 else 1
        else:
            definitions[str(item)] = 1
    platforms = SUPPORTED.intersection(definitions)
    if not platforms:
        if env.subst("$PIOENV").startswith("native"):
            return
        raise ValueError("firmware build has no recognized RAM policy platform")
    if len(platforms) != 1:
        raise ValueError("firmware build has conflicting platform definitions")
    platform = next(iter(platforms))
    mcu = str(env.BoardConfig().get("build.mcu", "")).lower()
    target = env.subst("$PIOENV")
    def check(source, target, env):
        path = Path(env.subst("$BUILD_DIR/${PROGNAME}.elf"))
        output = Path(env.subst("$BUILD_DIR/${PROGNAME}.memory.json"))
        try:
            result = check_firmware(path, platform, mcu, definitions,
                                    env.subst("$PIOENV"), output)
            return result
        except (OSError, ValueError, KeyError, struct.error) as error:
            print(f"Runtime RAM check failed: {error}", file=sys.stderr)
            return 2

    for alias in ("checkprogsize", "mergebin", "upload"):
        env.AddPreAction(env.Alias(alias), check)
    for suffix in ("bin", "hex", "uf2", "zip"):
        env.AddPreAction("$BUILD_DIR/${PROGNAME}." + suffix, check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("--platform", required=True, choices=sorted(SUPPORTED))
    parser.add_argument("--mcu", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--defines", type=Path, help="JSON object of resolved build definitions")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        defines = json.loads(args.defines.read_text()) if args.defines else {}
        return check_firmware(args.elf, args.platform, args.mcu, defines, args.target, args.output)
    except (OSError, ValueError, KeyError, struct.error) as error:
        print(f"Runtime RAM check failed: {error}", file=sys.stderr)
        return 2


try:
    Import("env")  # noqa: F821 -- PlatformIO/SCons
except NameError:
    if __name__ == "__main__":
        raise SystemExit(main())
else:
    register_platformio(env)  # noqa: F821
