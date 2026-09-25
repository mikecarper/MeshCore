#!/usr/bin/env python3
"""Keep manual UF2 images below the OTAFIX scratch bank on nRF52840."""

from __future__ import annotations

from pathlib import Path
import struct
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_nrf52_uf2 import check_uf2  # noqa: E402


def uf2(start: int, count: int, family: int = 0xADA52840) -> bytes:
    image = bytearray(count * 512)
    for index in range(count):
        offset = index * 512
        struct.pack_into("<8I", image, offset, 0x0A324655, 0x9E5D5157,
                         0x2000, start + index * 256, 256, index, count, family)
        struct.pack_into("<I", image, offset + 508, 0x0AB16F30)
    return bytes(image)


class Nrf52Uf2SlotTest(unittest.TestCase):
    def check(self, data: bytes) -> tuple[int, int, int]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "firmware.uf2"
            path.write_bytes(data)
            return check_uf2(path)

    def test_11717_rak4631_geometry_fits(self) -> None:
        self.assertEqual(self.check(uf2(0x26000, 2117)),
                         (0x26000, 0x26000 + 2117 * 256, 2117))

    def test_11716_rak4631_geometry_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds the OTAFIX-compatible limit"):
            self.check(uf2(0x26000, 3138))

    def test_wrong_family_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "wrong nRF52840 application family"):
            self.check(uf2(0x26000, 1, 0xE48BFF56))

    def test_missing_block_is_rejected(self) -> None:
        data = bytearray(uf2(0x26000, 2))
        struct.pack_into("<I", data, 512 + 12, 0x26300)
        with self.assertRaisesRegex(ValueError, "noncontiguous"):
            self.check(bytes(data))


if __name__ == "__main__":
    unittest.main()
