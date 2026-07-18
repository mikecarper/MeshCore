#!/usr/bin/env python3
"""Fail when an ESP32 app image (including EndF) exceeds its app partition."""

from __future__ import annotations

import struct
import sys
from pathlib import Path


ENTRY_SIZE = 32
PARTITION_MAGIC = 0x50AA
TYPE_APP = 0x00
SUBTYPE_OTA_0 = 0x10


def app_partition_size(table: bytes) -> tuple[str, int]:
    first_app: tuple[str, int] | None = None
    for offset in range(0, len(table) - ENTRY_SIZE + 1, ENTRY_SIZE):
        entry = table[offset : offset + ENTRY_SIZE]
        magic, part_type, subtype, _address, size = struct.unpack_from("<HBBII", entry)
        if magic != PARTITION_MAGIC:
            continue
        if part_type != TYPE_APP:
            continue
        label = entry[12:28].split(b"\0", 1)[0].decode("ascii", "replace") or "app"
        if first_app is None:
            first_app = (label, size)
        if subtype == SUBTYPE_OTA_0:
            return label, size
    if first_app is not None:
        return first_app
    raise ValueError("partition table has no app partition")


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {Path(sys.argv[0]).name} FIRMWARE.BIN PARTITIONS.BIN", file=sys.stderr)
        return 2

    firmware_path = Path(sys.argv[1])
    partition_path = Path(sys.argv[2])
    try:
        image_size = firmware_path.stat().st_size
        label, limit = app_partition_size(partition_path.read_bytes())
    except (OSError, ValueError) as error:
        print(f"ESP32 app-size check failed: {error}", file=sys.stderr)
        return 2

    if image_size > limit:
        print(
            f"ESP32 app image is {image_size} bytes, exceeding {label} "
            f"({limit} bytes) by {image_size - limit} bytes",
            file=sys.stderr,
        )
        return 1

    print(
        f"ESP32 app image fits {label}: {image_size}/{limit} bytes "
        f"({limit - image_size} bytes free)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
