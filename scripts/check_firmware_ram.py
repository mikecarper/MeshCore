#!/usr/bin/env python3
"""Gate firmware builds on internal runtime RAM, including enabled startup features.

Uses real linker heap bounds on ARM and the linked ESP-IDF allocator tables on
ESP32. These are capacity checks before dynamic allocation, not hardware soak
results. PSRAM, instruction-only RAM and reserved bootloader arenas never count
as internal heap. See docs/firmware_memory_budget.md for the budget policy.
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
    companion = bool(re.search(r"companion|comp_radio|comp_.*radio", target, re.I))
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
    if platform == "NRF52_PLATFORM":
        parts["loop_and_callback_stacks"] = integer(defines, "MESH_NRF52_LOOP_STACK_WORDS", 2048) * 4 + 3072
        parts["core_usb_filesystems_sensors"] = 12288
        if "BLE_PIN_CODE" in defines:
            parts["bluetooth_worker_stacks"] = 5920
    elif platform == "ESP32_PLATFORM":
        parts["core_tasks_usb_filesystems"] = 24576
        wifi = 49152 if "WIFI_SSID" in defines or "WIFI_OTA_SEEDER" in defines else 0
        ble = 32768 if "BLE_PIN_CODE" in defines else 0
        parts["wireless_stacks"] = max(wifi, ble) if "COMPANION_EXCLUSIVE_WIFI_BLE" in defines else wifi + ble
        if "WITH_MQTT_BRIDGE" in defines:
            parts["mqtt_connections_buffers"] = 24576
        if "ENABLE_OTA" in defines:
            parts["ota_source_scratch"] = 8192
        if ("WEBCONFIG_DISABLED" not in defines
                and ((companion and "WIFI_SSID" in defines)
                     or (not companion and "ADMIN_PASSWORD" in defines))):
            parts["browser_terminal_session"] = 2048
            if "BOARD_HAS_PSRAM" not in defines:
                # Larger replies grow on demand only while preserving 32 KiB
                # of free internal heap, and shrink after the browser reads.
                parts["browser_terminal_scrollback"] = 4096
        if not companion:
            parts["neighbor_history"] = integer(defines, "MAX_RECENT_REPEATERS", 50) * 12
    else:
        parts["core_filesystems_sensors"] = 8192
    # Packet bytes plus all three queue tables and allocation overhead.
    parts["radio_packet_pool"] = 5120 if companion else 10240
    # These tables moved out of .bss, so linker heap bounds now include their
    # space. Count it as startup allocation instead; C++ assertions bind the
    # per-entry bounds to the production structures.
    if re.search(r"repeater|room_server|sensor", target, re.I) or "COMPANION_MESH_CLOCK_SYNC" in defines:
        parts["client_table"] = integer(defines, "MAX_CLIENTS", 32) * 320 + 16
    if "repeater" in target.lower():
        engine = integer(defines, "MESH_ENABLE_FLOOD_RULE_ENGINE", int(platform != "STM32_PLATFORM"))
        slots = integer(defines, "FLOOD_PACKET_FILTER_SLOTS", 63 if engine else 16)
        parts["flood_filter_table"] = slots * (200 if engine else 40) + 16
    if "ENABLE_OTA" in defines and "OTA_HEAP_CONTEXT" in defines:
        parts["ota_context"] = 16384 + 16
    if display and display != "NullDisplayDriver":
        parts["display_pixels_and_driver"] = display_heap
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
            if extra:
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
    for name in ("client_table", "flood_filter_table", "ota_context"):
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
