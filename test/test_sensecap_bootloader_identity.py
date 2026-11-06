"""SenseCAP Solar P1 keeps an exact identity distinct from deployed XIAOs."""
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/mota"))
import motalib as ml
from test_nrf52_embedded_bootloader import boot_image, candidate, profile
import nrf52_embedded_bootloader as embed


P1_IDENTITY = (0x28860044, "SCAP_DFU")
P1_TARGET = 0xD677B472
P1_HW_ID = b"NRF_BL_28860044_SCAP_DFU".ljust(32, b"\0")


class SensecapBootloaderIdentityTest(unittest.TestCase):
    def image(self, **kwargs):
        return boot_image(v7=True, name="SCAP_DFU", **kwargs)

    def package_options(self, image=None):
        image = self.image() if image is None else image
        return dict(target_id=P1_TARGET, fw_version=embed.BOOT_VERSION,
                    image_size=len(image), payload=image, block_size=1024,
                    image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL,
                    is_full=True, sign_priv=Ed25519PrivateKey.generate(), bootloader=True)

    def test_p1_parses_and_uses_its_generic_exact_identity(self):
        image = self.image()
        identity = ml.validate_bootloader_image(image, target_id=P1_TARGET, signed_hw_id=P1_HW_ID)
        self.assertEqual((identity.board_id, identity.device_name), P1_IDENTITY)
        self.assertEqual((identity.softdevice_family, identity.softdevice_fwid, identity.app_base,
                          identity.layout_abi), (140, 0x123, 0x27000, 1))
        self.assertEqual(ml.bootloader_target_id(*P1_IDENTITY), P1_TARGET)
        self.assertEqual(ml.bootloader_hw_id(*P1_IDENTITY), P1_HW_ID)
        self.assertTrue(ml.bootloader_identity_is_buildable(*P1_IDENTITY))
        self.assertEqual(ml.bootloader_qualified_storage_profiles(*P1_IDENTITY), (0x0E,))
        self.assertEqual(ml.bootloader_qualified_platform_profile(*P1_IDENTITY), (140, 0x123, 0x27000, 1))

    def test_signed_p1_package_builds_parses_and_verifies(self):
        options = self.package_options()
        manifest = ml.build_manifest(**options)
        parsed = ml.parse_container(ml.build_container(manifest, options["payload"]))
        self.assertEqual(parsed.manifest.target_id, P1_TARGET)
        self.assertEqual(parsed.manifest.hw_id, P1_HW_ID)
        self.assertEqual(ml.verify(parsed, expect_pub=ml.ed25519_public_bytes(options["sign_priv"])), [])

    def test_existing_xiao_wire_ids_and_wrappers_remain_unchanged(self):
        for board_id, expected in ((0x28860044, b"XIAO_BL_28860044"),
                                   (0x28860045, b"XIAO_BL_28860045")):
            with self.subTest(board_id=hex(board_id)):
                image = boot_image(v7=True, board_id=board_id)
                self.assertEqual(ml.bootloader_target_id(board_id, "XIAO_DFU"), board_id)
                self.assertEqual(ml.bootloader_hw_id(board_id, "XIAO_DFU"), expected.ljust(32, b"\0"))
                self.assertEqual(ml.parse_xiao_bootloader_identity(image).device_name, "XIAO_DFU")
                self.assertEqual(ml.validate_xiao_bootloader_image(image, board_id).board_id, board_id)
        self.assertIsNone(ml.parse_xiao_bootloader_identity(self.image()))
        with self.assertRaisesRegex(ValueError, "not a deployed XIAO"):
            ml.validate_xiao_bootloader_image(self.image())

    def test_only_the_exact_p1_pair_is_admitted_on_shared_xiao_ids(self):
        for board_id, name in ((0x28860045, "SCAP_DFU"), (0x28860044, "OTHER_DFU"),
                               (0x28860045, "OTHER_DFU"), (0x28860044, "R_SCAP_DFU"),
                               (0x28860044, "SCAP_DFU_V2"), (0x28860044, "SCAP_DFU\0X")):
            with self.subTest(board_id=hex(board_id), name=name):
                image = boot_image(v7=True, board_id=board_id, name=name)
                self.assertIsNone(ml.parse_bootloader_identity(image))
                self.assertFalse(ml.bootloader_identity_is_buildable(board_id, name))
                with self.assertRaises(ValueError):
                    ml.bootloader_hw_id(board_id, name)
                with self.assertRaises(ValueError):
                    ml.bootloader_target_id(board_id, name)
                with self.assertRaises(ValueError):
                    ml.validate_bootloader_image(image)

    def test_cross_board_wire_ids_cannot_be_used_to_sign_p1_or_xiao(self):
        for changes in ({"target_id": 0x28860044}, {"target_id": 0x28860045},
                        {"hw_id": "XIAO_BL_28860044"}, {"hw_id": "XIAO_BL_28860045"}):
            with self.subTest(changes=changes):
                options = self.package_options()
                options.update(changes)
                with self.assertRaises(ValueError):
                    ml.build_manifest(**options)
        options = self.package_options(boot_image(v7=True))
        with self.assertRaisesRegex(ValueError, "target ID"):
            ml.build_manifest(**options)

    def test_p1_rejects_wrong_softdevice_storage_and_corrupt_crc(self):
        for image in (boot_image(name="SCAP_DFU", board_id=0x28860044),
                      self.image(app_base=0x26000), self.image(storage=0x0A)):
            with self.subTest(digest=hashlib.sha256(image).hexdigest()), self.assertRaises(ValueError):
                ml.validate_bootloader_image(image)
        corrupt = bytearray(self.image())
        corrupt[0x300] ^= 1
        self.assertIsNone(ml.parse_bootloader_identity(bytes(corrupt)))

    def test_p1_target_is_disjoint_from_all_generated_application_targets(self):
        header = (ROOT / "src/helpers/ota/OtaTargets.h").read_text()
        application_ids = {int(value, 16) for value in re.findall(r"\{ 0x([0-9a-fA-F]{8}),", header)}
        self.assertTrue(application_ids)
        inventory = ml.audit_bootloader_target_inventory(application_ids)
        self.assertEqual(inventory[P1_TARGET], P1_IDENTITY)
        self.assertEqual(inventory[0x28860044], (0x28860044, "XIAO_DFU"))
        self.assertEqual(inventory[0x28860045], (0x28860045, "XIAO_DFU"))
        with self.assertRaisesRegex(ValueError, "collides with an application"):
            ml.audit_bootloader_target_inventory(application_ids | {P1_TARGET})

    def test_embedding_accepts_p1_normal_asset_and_rejects_recovery_substitution(self):
        data = candidate(self.image(), v7=True)
        item = profile(data, v7=True, name="SCAP_DFU")
        item["id"] = "sensecap_solar_p1"
        image, identity = embed.validate_candidate_zip(data, item)
        self.assertEqual(image, self.image())
        self.assertEqual(identity.device_name, "SCAP_DFU")
        # Even a CRC-valid same-name image cannot replace the pinned normal ZIP.
        different = candidate(self.image(boot_version=0x02040BFF), v7=True)
        with self.assertRaisesRegex(ValueError, "pinned release asset"):
            embed.validate_candidate_zip(different, item)
        recovery = copy.deepcopy(item)
        recovery["files"]["zip"]["url"] = recovery["files"]["zip"]["url"].replace(
            "/releases/download/", "/releases/download/R_")
        with self.assertRaisesRegex(ValueError, "pinned bootloader ZIP metadata"):
            embed.validate_candidate_zip(data, recovery)

    def test_cached_production_2_4_12_p1_asset_if_available(self):
        manifest = json.loads((ROOT / "docs/_data/bootloader_manifest.json").read_text())
        item = next(item for item in manifest["profiles"] if item["id"] == "sensecap_solar_p1")
        asset = item["files"]["zip"]
        self.assertEqual(asset["sha256"], "694fca8e68fb695ac1a5ef701baf26e8d52a7eb188f886496dcb6bc8b6fdc7cb")
        path = ROOT / ".pio/otafix-cache" / (asset["sha256"] + "-" + asset["name"])
        if not path.is_file():
            self.skipTest("published P1 ZIP is not cached; offline fixtures cover validation")
        image, identity = embed.validate_candidate_zip(path.read_bytes(), item)
        self.assertEqual(hashlib.sha256(image).hexdigest(),
                         "d33c035c783e555049d2b41fe2f12367c6a87ee30b041c627d19088a2a6d8855")
        self.assertEqual((identity.board_id, identity.device_name), P1_IDENTITY)


if __name__ == "__main__":
    unittest.main()
