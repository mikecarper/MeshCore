#!/usr/bin/env python3
"""Reject nRF52840 UF2 images that overlap the OTAFIX bootloader scratch area."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct


SECTOR_BYTES = 512
PAYLOAD_BYTES = 256
APP_FAMILY = 0xADA52840
SAFE_APP_END = 0xE0000
MAGIC_START = (0x0A324655, 0x9E5D5157)
MAGIC_END = 0x0AB16F30
FAMILY_FLAG = 0x2000


def check_uf2(path: Path, app_end: int = SAFE_APP_END) -> tuple[int, int, int]:
    data = path.read_bytes()
    if not data or len(data) % SECTOR_BYTES:
        raise ValueError(f"{path}: incomplete UF2 sectors")
    count = len(data) // SECTOR_BYTES
    start = None
    for index in range(count):
        offset = index * SECTOR_BYTES
        magic0, magic1, flags, address, size, block_no, blocks, family = struct.unpack_from(
            "<8I", data, offset
        )
        trailer = struct.unpack_from("<I", data, offset + 508)[0]
        if (magic0, magic1) != MAGIC_START or trailer != MAGIC_END:
            raise ValueError(f"{path}: invalid UF2 block {index}")
        if (flags & FAMILY_FLAG) == 0 or flags & 1 or family != APP_FAMILY:
            raise ValueError(f"{path}: wrong nRF52840 application family")
        if size != PAYLOAD_BYTES or block_no != index or blocks != count:
            raise ValueError(f"{path}: inconsistent UF2 block {index}")
        if start is None:
            start = address
        if address != start + index * PAYLOAD_BYTES:
            raise ValueError(f"{path}: noncontiguous UF2 application")
    assert start is not None
    end = start + count * PAYLOAD_BYTES
    if start not in (0x26000, 0x27000):
        raise ValueError(f"{path}: unexpected nRF52840 application base 0x{start:X}")
    if end > app_end:
        raise ValueError(
            f"{path}: application 0x{start:X}..0x{end:X} exceeds the "
            f"OTAFIX-compatible limit 0x{app_end:X}"
        )
    return start, end, count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uf2", type=Path)
    args = parser.parse_args()
    try:
        start, end, blocks = check_uf2(args.uf2)
    except (OSError, ValueError) as error:
        parser.exit(1, f"nRF52 UF2 qualification failed: {error}\n")
    print(f"nRF52 UF2 safe: 0x{start:X}..0x{end:X}, {blocks} blocks")


if __name__ == "__main__":
    main()
