"""Check fork inventory isolation; optionally audit locally downloaded ZIPs."""
import binascii
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from nrf52_fork_bootloaders import reviewed_fork_bootloaders
from nrf52_legacy_bootloaders import legacy_bootloaders_for


class ForkBootloaderInventoryTest(unittest.TestCase):
    def test_profiles_are_disjoint_and_require_the_correct_s140_layout(self):
        records = reviewed_fork_bootloaders()
        self.assertEqual(len(records), 144)
        self.assertEqual(len({r["image_sha256"] for r in records}), len(records))
        profile_pairs = {(r["profiles"][0], h) for r in records for h in r["hardware"]}
        for profile, hardware in profile_pairs:
            for base, fwid in ((0x26000, 0xB6), (0x27000, 0x123)):
                self.assertLessEqual(len(legacy_bootloaders_for(profile, hardware, base, fwid)), 32)
        for record in records:
            profile = record["profiles"][0]
            digest = record["image_sha256"]
            def matching(p, h, base=record["app_base"], fwid=record["softdevice_fwid"]):
                return {r["image_sha256"] for r in legacy_bootloaders_for(p, h, base, fwid)}
            for hardware in record["hardware"]:
                self.assertIn(digest, matching(profile, hardware))
                other_base, other_fwid = ((0x26000, 0xB6) if record["app_base"] == 0x27000
                                          else (0x27000, 0x123))
                self.assertNotIn(digest, matching(profile, hardware, other_base, other_fwid))
                self.assertNotIn(digest, matching(profile, hardware, fwid=other_fwid))
            for other_profile, other_hardware in profile_pairs:
                if other_hardware not in record["hardware"]:
                    self.assertNotIn(digest, matching(other_profile, other_hardware))
                    self.assertNotIn(digest, matching(profile, other_hardware))

    def test_inventory_over_runtime_limit_fails_the_build(self):
        template = next(r for r in reviewed_fork_bootloaders()
                        if r["profiles"] == ["sensecap_solar_p1"])
        records = [{**template, "image_sha256": f"{number:064x}"} for number in range(33)]
        with patch("nrf52_legacy_bootloaders.reviewed_fork_bootloaders", return_value=records):
            with self.assertRaisesRegex(ValueError, "runtime limit"):
                legacy_bootloaders_for("sensecap_solar_p1", "SenseCap_Solar", 0x27000, 0x123)

    @unittest.skipUnless(os.environ.get("OTAFIX_FORK_AUDIT_DIR"), "downloaded release ZIPs not provided")
    def test_downloaded_release_assets_match_installed_fingerprints(self):
        base = Path(os.environ["OTAFIX_FORK_AUDIT_DIR"])
        for record in reviewed_fork_bootloaders():
            owner = record["source_url"].split("/")[3]
            data = (base / owner / record["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), record["artifact_sha256"])
            if record["name"].endswith(".uf2"):
                self.assertEqual(len(data) % 512, 0)
                written = {}
                for pos in range(0, len(data), 512):
                    start, magic, _, address, size, _, _, _ = struct.unpack_from("<8I", data, pos)
                    self.assertEqual((start, magic), (0x0a324655, 0x9e5d5157))
                    self.assertLessEqual(size, 476)
                    self.assertEqual(struct.unpack_from("<I", data, pos + 508)[0], 0x0ab16f30)
                    for index, byte in enumerate(data[pos + 32:pos + 32 + size]):
                        dest = address + index
                        if dest in written:
                            self.assertEqual(written[dest], byte)
                        written[dest] = byte
                image = bytes(written.get(dest, 0xff) for dest in range(0xf4000, 0xfe000))
                self.assertEqual(hashlib.sha256(image).hexdigest(), record["image_sha256"])
                continue
            with zipfile.ZipFile(base / owner / record["name"]) as archive:
                entry = json.loads(archive.read("manifest.json"))["manifest"]["softdevice_bootloader"]
                binary = archive.read(entry["bin_file"])
                packet = archive.read(entry["dat_file"])
                sd_size, bl_size = entry["sd_size"], entry["bl_size"]
                self.assertEqual(len(binary), sd_size + bl_size)
                self.assertLessEqual(bl_size, 0xA000)
                crc = binascii.crc_hqx(binary, 0xffff)
                self.assertEqual(crc, entry["init_packet_data"]["firmware_crc16"])
                self.assertEqual(crc, struct.unpack_from("<H", packet, len(packet) - 2)[0])
                image = binary[sd_size:] + b"\xff" * (0xA000 - bl_size)
                stack, reset = struct.unpack_from("<II", image)
                self.assertTrue(0x20000000 <= stack <= 0x20040000 and stack % 8 == 0)
                self.assertTrue(reset & 1 and 0xF4000 <= (reset & ~1) < 0xFE000)
                self.assertEqual(hashlib.sha256(image).hexdigest(), record["image_sha256"])


if __name__ == "__main__":
    unittest.main()
