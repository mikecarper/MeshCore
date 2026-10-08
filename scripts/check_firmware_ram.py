#!/usr/bin/env python3
"""Gate firmware builds on internal runtime RAM, including enabled startup features.

Uses real linker heap bounds on ARM and the linked ESP-IDF allocator tables on
ESP32. These are capacity checks before dynamic allocation, not hardware soak
results. PSRAM, instruction-only RAM and reserved bootloader arenas never count
as internal heap. See docs/research/firmware_memory_budget.md for the budget policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    "ST7735Display": 25602,
    "SSD1306Display": 4096, "SH1106Display": 4096, "U8g2Display": 4096,
    "SH1107Display": 4096,
    # These drivers render directly or embed their pixel storage in globals.
    "ST7789Display": 0, "ST7789LCDDisplay": 0, "NV3001BDisplay": 0,
    "GxEPDDisplay": 0, "E213Display": 8192, "E290Display": 8192,
}


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
    elif display in DISPLAY_HEAP:
        display_heap = DISPLAY_HEAP[display]
    else:
        raise ValueError(f"add a runtime allocation budget for display {display!r}")
    parts = {}
    combined_rak_ethernet = "RAK4631_COMBINED_ETHERNET" in defines
    if combined_rak_ethernet and (platform != "NRF52_PLATFORM"
                                 or not {"RAK_4631", "ETHERNET_ENABLED", "OTA_RAK_AUTO_STORE"}.issubset(defines)):
        raise ValueError("combined RAK Ethernet budget requires RAK4631 adaptive nRF52 storage")
    if combined_rak_ethernet and display == "SSD1306Display":
        # The fixed 128x64 driver allocates one 1024-byte framebuffer and
        # reuses it after power cycling. Its linked source assertion binds
        # framebuffer + complete driver object + allocator overhead to 2 KiB.
        # The actual linked ARM driver is 156 bytes; it is already static,
        # so including it here again retains a conservative allocation bound.
        display_heap = 2048
    if platform == "NRF52_PLATFORM":
        parts["loop_and_callback_stacks"] = integer(defines, "MESH_NRF52_LOOP_STACK_WORDS", 2048) * 4 + 3072
        parts["core_usb_filesystems_sensors"] = 12288
        if "BLE_PIN_CODE" in defines:
            parts["bluetooth_worker_stacks"] = 5920
        if combined_rak_ethernet and "WITH_RS232_BRIDGE" in defines:
            # The bridge owns a packet/ACK duplicate table as well as its RX
            # frame. Its source assertion bounds the object to 2304 bytes;
            # retain room for allocation metadata and the UART TX semaphore.
            # UART RX/TX arrays are embedded in the already-linked globals.
            parts["uart_bridge_and_driver"] = 2560
    elif platform == "ESP32_PLATFORM":
        parts["core_tasks_usb_filesystems"] = 24576
        wifi = 49152 if "WIFI_SSID" in defines or "WIFI_OTA_SEEDER" in defines else 0
        ble = 32768 if "BLE_PIN_CODE" in defines else 0
        parts["wireless_stacks"] = max(wifi, ble) if "COMPANION_EXCLUSIVE_WIFI_BLE" in defines else wifi + ble
        if "WITH_MQTT_BRIDGE" in defines:
            parts["mqtt_connections_buffers"] = 24576
        if "WITH_RS232_BRIDGE" in defines:
            # The UART bridge is allocated on enable, alongside WiFi/MQTT.
            # RS232Bridge.cpp bounds its object to 4 KiB; reserve another
            # 4 KiB for the SDK UART task, buffers and allocator overhead.
            parts["uart_bridge_and_driver"] = 8192
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
    if combined_rak_ethernet:
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
                 "ota_generic_source_buffers", "ethernet_or_external_ota_workspaces"):
        largest = max(largest, parts.get(name, 0))
    return {"required_heap_bytes": required, "required_contiguous_bytes": largest,
            "components": parts, "display": display, "full_companion": full}


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
