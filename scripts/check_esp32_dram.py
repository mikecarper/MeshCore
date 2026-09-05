#!/usr/bin/env python3
"""Enforce classic ESP32's static DRAM budget before producing an image.

The 320 KiB DRAM total includes memory available only as runtime heap. Use
the linked image's actual region (including SDK/BT/trace reservations), capped
at Espressif's documented 160 KiB static limit. Keep an additional 8 KiB of
static-region headroom as a project policy; this is not a runtime heap estimate
or a guarantee that every peripheral/configuration will boot.

Run standalone with a GNU linker map, or as a PlatformIO post extra_script.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


STATIC_DRAM_LIMIT = 160 * 1024
MIN_STATIC_DRAM_RESERVE = 8 * 1024
DRAM_START = 0x3FFB0000
DRAM_END = 0x40000000


def parse_reserve(value):
    reserve = int(str(value), 0)
    if reserve < MIN_STATIC_DRAM_RESERVE:
        raise ValueError(
            f"static DRAM reserve must be at least {MIN_STATIC_DRAM_RESERVE} bytes"
        )
    return reserve


def static_dram_usage(map_text):
    """Return region length and occupied span, including alignment and .noinit."""
    try:
        memory, linked = map_text.split("Linker script and memory map", 1)
        memory = memory.split("Memory Configuration", 1)[1]
    except (ValueError, IndexError) as error:
        raise ValueError("missing GNU linker memory configuration") from error

    regions = re.findall(
        r"^dram0_0_seg\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+\S+",
        memory, re.MULTILINE,
    )
    if len(regions) != 1:
        raise ValueError("expected one classic ESP32 dram0_0_seg region")
    origin, length = (int(value, 16) for value in regions[0])
    if length <= 0 or origin < DRAM_START or origin + length > DRAM_END:
        raise ValueError("invalid classic ESP32 DRAM region")

    def symbol(name):
        values = re.findall(
            rf"^\s*(0x[0-9a-fA-F]+)\s+{re.escape(name)}\s*=",
            linked, re.MULTILINE,
        )
        if len(values) != 1:
            raise ValueError(f"expected one {name} definition in linker map")
        return int(values[0], 16)

    data_start = symbol("_data_start")
    data_end = symbol("_data_end")
    bss_start = symbol("_bss_start")
    bss_end = symbol("_bss_end")
    heap_start = symbol("_heap_start")
    if not (origin <= data_start <= data_end <= heap_start
            and origin <= bss_start <= bss_end <= heap_start):
        raise ValueError("inconsistent static DRAM boundaries in linker map")

    # _heap_start follows all internal static sections. Unlike summing .data
    # and .bss sizes, this also accounts for .noinit and linker alignment gaps.
    return length, heap_start - origin


def check_map(map_path, reserve=MIN_STATIC_DRAM_RESERVE):
    try:
        reserve = parse_reserve(reserve)
        region_size, used = static_dram_usage(Path(map_path).read_text())
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ESP32 static DRAM check failed: {error}", file=sys.stderr)
        return 2

    limit = min(region_size, STATIC_DRAM_LIMIT)
    remaining = limit - used
    print(
        f"ESP32 static DRAM: {used:,}/{limit:,} bytes occupied, "
        f"{remaining:,} bytes free; required reserve {reserve:,} bytes "
        f"(linker region {region_size:,}, static ceiling {STATIC_DRAM_LIMIT:,})"
    )
    if remaining < reserve:
        print(
            f"ESP32 static DRAM check failed: reserve short by "
            f"{reserve - remaining:,} bytes. Reduce static tables/buffers or "
            "move suitable storage to checked heap allocations. The 320 KiB "
            "DRAM total and PSRAM do not expand this internal static budget.",
            file=sys.stderr,
        )
        return 1
    return 0


def register_platformio(env):
    # ESP32-S2/S3/C-series chips have different memory maps and limits.
    if str(env.BoardConfig().get("build.mcu", "")).lower() != "esp32":
        return

    reserve = parse_reserve(env.GetProjectOption(
        "custom_esp32_static_dram_reserve", str(MIN_STATIC_DRAM_RESERVE)
    ))
    checked = None

    def check_static_dram(source, target, env):
        nonlocal checked
        map_path = Path(env.subst("$BUILD_DIR/${PROGNAME}.map"))
        elf_path = Path(env.subst("$BUILD_DIR/${PROGNAME}.elf"))
        try:
            # Cached/nobuild uploads must still have the matching build inputs.
            elf_stat = elf_path.stat()
            map_stat = map_path.stat()
        except OSError as error:
            print(f"ESP32 static DRAM check failed: {error}", file=sys.stderr)
            return 2
        signature = (elf_stat.st_mtime_ns, elf_stat.st_size,
                     map_stat.st_mtime_ns, map_stat.st_size)
        if checked == signature:
            return 0
        result = check_map(map_path, reserve)
        if result == 0:
            checked = signature
        return result

    # checkprogsize runs on incremental builds too. The image and action hooks
    # also cover direct image generation, cached merges, and nobuild uploads.
    # Load this script after merge-bin.py: PlatformIO replaces a custom
    # target's executor when declaring it, which discards earlier pre-actions.
    # Alias nodes keep action targets distinct from files in nobuild mode.
    env.AddPreAction(env.Alias("checkprogsize"), check_static_dram)
    env.AddPreAction("$BUILD_DIR/${PROGNAME}.bin", check_static_dram)
    env.AddPreAction(env.Alias("mergebin"), check_static_dram)
    env.AddPreAction(env.Alias("upload"), check_static_dram)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("map", type=Path, help="classic ESP32 firmware.map")
    parser.add_argument(
        "--reserve", default=MIN_STATIC_DRAM_RESERVE, type=parse_reserve,
        help="required static-region headroom in bytes (minimum 8192)",
    )
    args = parser.parse_args()
    return check_map(args.map, args.reserve)


try:
    Import("env")  # noqa: F821 -- PlatformIO/SCons supplies Import
except NameError:
    if __name__ == "__main__":
        raise SystemExit(main())
else:
    register_platformio(env)  # noqa: F821
