"""Validate the upcoming internal-only X1 bootloader package without release assets."""
from pathlib import Path
import re
import struct
import sys
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/mota"))
import motalib as ml
from test_mota import _generic_bootloader_image, _rewrite_boot_image, _write_boot_continuity


BOARD_ID = 0x28860057
NAME = "X1INT_DFU"
TARGET = 0xCDDA9FF9
HW_ID = b"NRF_BL_28860057_X1INT_DFU".ljust(32, b"\0")
TEST_VERSION = 0x02040D01


class MeshtrackerX1BootloaderIdentityTest(unittest.TestCase):
    def image(self, **kwargs):
        options = dict(board_id=BOARD_ID, device_name=NAME,
                       storage=ml.BOOT_STORAGE_INTERNAL_UPDATE,
                       boot_version=TEST_VERSION, hybrid_arena=True)
        options.update(kwargs)
        image = _generic_bootloader_image(**options)
        return _rewrite_boot_image(image, lambda raw: _write_boot_continuity(
            raw, ml.BOOT_CANDIDATE_MANIFEST_OFFSET, version=TEST_VERSION,
            fwid=0x0123, app_base=0x27000))

    def package_options(self, image=None):
        image = self.image() if image is None else image
        return dict(target_id=TARGET, fw_version=TEST_VERSION, image_size=len(image),
                    payload=image, block_size=1024, image_hash=ml.mh32(image),
                    codec_id=ml.CODEC_FULL, is_full=True,
                    sign_priv=Ed25519PrivateKey.generate(), bootloader=True)

    def test_exact_identity_and_internal_s140_v7_profile(self):
        self.assertEqual(ml.MESH_TRACKER_X1_BOOTLOADER_IDENTITY, (BOARD_ID, NAME))
        self.assertTrue(ml.bootloader_identity_is_buildable(BOARD_ID, NAME))
        self.assertEqual(ml.bootloader_target_id(BOARD_ID, NAME), TARGET)
        self.assertEqual(ml.bootloader_hw_id(BOARD_ID, NAME), HW_ID)
        self.assertEqual(ml.bootloader_qualified_platform_profile(BOARD_ID, NAME),
                         (140, 0x0123, 0x27000, 1))
        self.assertEqual(ml.bootloader_qualified_storage_profiles(BOARD_ID, NAME), (0x0A,))
        identity = ml.validate_bootloader_image(self.image(), TARGET, HW_ID)
        self.assertEqual((identity.board_id, identity.device_name), (BOARD_ID, NAME))

    def test_signed_package_builds_parses_and_verifies(self):
        options = self.package_options()
        manifest = ml.build_manifest(**options)
        parsed = ml.parse_container(ml.build_container(manifest, options["payload"]))
        self.assertEqual(parsed.manifest.target_id, TARGET)
        self.assertEqual(parsed.manifest.hw_id, HW_ID)
        self.assertEqual(parsed.manifest.flags,
                         ml.FLAG_FULL | ml.FLAG_SIGNED | ml.FLAG_BOOTLOADER)
        self.assertEqual(ml.verify(parsed, expect_pub=ml.ed25519_public_bytes(
            options["sign_priv"])), [])

    def test_signing_and_outer_identity_remain_required(self):
        for changes in ({"sign_priv": None}, {"target_id": BOARD_ID},
                        {"target_id": 0xE6F5F03F}, {"hw_id": "NRF_BL_28860057_T1KE_DFU"},
                        {"fw_version": TEST_VERSION + 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                options = self.package_options()
                options.update(changes)
                ml.build_manifest(**options)

    def test_shared_usb_id_and_application_target_are_distinct(self):
        self.assertNotEqual(ml.bootloader_target_id(BOARD_ID, "T1KE_DFU"), TARGET)
        with self.assertRaises(ValueError):
            ml.validate_bootloader_image(self.image(device_name="T1KE_DFU"), TARGET, HW_ID)
        self.assertFalse(ml.bootloader_identity_is_buildable(BOARD_ID, "X1_DFU"))
        with self.assertRaises(ValueError):
            ml.build_manifest(**self.package_options(self.image(device_name="X1_DFU")))
        header = (ROOT / "src/helpers/ota/OtaTargets.h").read_text()
        application_targets = {int(value, 16) for value in re.findall(
            r"\{ 0x([0-9a-fA-F]{8}),", header)}
        self.assertTrue(application_targets)
        self.assertEqual(ml.audit_bootloader_target_inventory(application_targets)[TARGET],
                         (BOARD_ID, NAME))
        with self.assertRaisesRegex(ValueError, "collides with an application"):
            ml.audit_bootloader_target_inventory(application_targets | {TARGET})

    def test_wrong_storage_and_continuity_are_rejected(self):
        for storage in (0x09, 0x0E, 0x1E):
            with self.subTest(storage=storage), self.assertRaises(ValueError):
                ml.validate_bootloader_image(self.image(storage=storage))
        offset = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
        for field, encoding, wrong in ((60, "<H", 141), (62, "<H", 0x00B6),
                                       (64, "<I", 0x26000), (68, "<H", 2)):
            image = _rewrite_boot_image(self.image(), lambda raw: struct.pack_into(
                encoding, raw, offset + field, wrong))
            with self.subTest(field=field), self.assertRaises(ValueError):
                ml.validate_bootloader_image(image)

    def test_internal_profile_requires_the_fixed_64k_arena(self):
        mutations = (
            lambda raw: raw.__setitem__(slice(0x90, 0xA0), b"\xff" * 16),
            lambda raw: struct.pack_into("<I", raw, 0x9C, 32768),
            lambda raw: raw.__setitem__(slice(0xA0, 0xB0), raw[0x90:0xA0]),
            lambda raw: struct.pack_into("<I", raw, 0, 0x20040000),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "64 KiB"):
                ml.validate_bootloader_image(_rewrite_boot_image(self.image(), mutate))

    def test_corrupt_image_is_refused_before_signing(self):
        image = bytearray(self.image())
        image[0x300] ^= 1
        self.assertIsNone(ml.parse_bootloader_identity(bytes(image)))
        with self.assertRaises(ValueError):
            ml.build_manifest(**self.package_options(bytes(image)))


if __name__ == "__main__":
    unittest.main()
