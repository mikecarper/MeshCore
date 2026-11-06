#!/usr/bin/env python3
"""Exact target, capacity and build-failure contracts for expanders."""

import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from esp32_migration_expanders import (  # noqa: E402
    LEGACY_SLOT_BYTES, TARGET_MARKER, build_expander_candidates,
    evaluate_candidate, expander_environment, overlay_section,
    project_overlay, target_id_for_env, verify_compiled_target,
)
from package_esp32_partition_migration import BOARDS  # noqa: E402


def image_for(target, size=1024):
    header = bytearray(24)
    header[0], header[3] = 0xE9, 0x4F  # ESP32-S3, 16 MiB
    struct.pack_into("<H", header, 12, 9)
    head = bytes(header) + TARGET_MARKER + struct.pack("<I", target_id_for_env(target))
    return head + bytes(max(0, size - len(head)))


class ExpanderCandidateTests(unittest.TestCase):
    def setUp(self):
        self.board = "heltec-v4"
        self.spec = BOARDS[self.board]
        self.target = self.spec["target"]
        self.version = "v1.18.0.1-test"

    def test_known_expander_uses_reviewed_slim_board_and_disables_usb_cdc(self):
        overlay = overlay_section(self.board, self.spec)
        self.assertIn("extends = env:heltec_v4_partition_expander", overlay)
        self.assertIn("-DARDUINO_USB_CDC_ON_BOOT=0", overlay)
        self.assertIn(f"-D MOTA_MIGRATION_TARGET_ID=0x{target_id_for_env(self.target):08x}", overlay)
        self.assertIn("+<../examples/partition_expander/*.cpp>", overlay)
        self.assertNotIn("board_build.flash_mode", overlay)

    def test_other_boards_extend_exact_role_and_exclude_all_role_entrypoints(self):
        spec = BOARDS["tenstar-c3-sx1268-repeater"]
        overlay = overlay_section("tenstar-c3-sx1268-repeater", spec)
        self.assertIn("extends = env:Tenstar_C3_sx1268_repeater", overlay)
        for role in ("simple_repeater", "simple_room_server", "simple_sensor"):
            self.assertIn(f"-<../examples/{role}>", overlay)
        self.assertIn("MESHCORE_MIGRATION_STRICT_4MB=1", overlay)

    def test_classic_esp32_uses_bounded_heap_ota_window(self):
        spec = BOARDS["heltec-v2-repeater"]
        self.assertEqual(0, spec["usb_chip"])
        overlay = overlay_section("heltec-v2-repeater", spec)
        self.assertIn("-D OTA_FETCH_PIPELINE=1", overlay)
        self.assertNotIn("-D OTA_FETCH_PIPELINE=4", overlay)

    def test_reviewed_w12_and_ct62_slim_bases_retain_required_flags(self):
        w12 = overlay_section("meshnology-w12-repeater", BOARDS["meshnology-w12-repeater"])
        self.assertIn("extends = meshnology_w12", w12)
        self.assertIn("-D MESH_ENABLE_LR2021_AUTO_XTRIM=1", w12)
        self.assertNotIn("MESH_W12_PRAM_EXPERIMENT", w12)
        ct62 = overlay_section("heltec-ct62-repeater", BOARDS["heltec-ct62-repeater"])
        self.assertIn("extends = Heltec_ct62", ct62)
        self.assertIn("-D LIGHTWEIGHT_WIFI_OTA=1", ct62)

    def test_overlay_is_unique_and_rejects_bad_names_or_duplicate_boards(self):
        overlay = project_overlay("[platformio]\n", [self.board], BOARDS)
        self.assertIn("[env:" + expander_environment(self.board, self.target) + "]", overlay)
        with self.assertRaises(ValueError):
            project_overlay("", [self.board, self.board], BOARDS)
        with self.assertRaises(ValueError):
            expander_environment("../../bad", self.target)

    def test_compiled_identity_is_required_and_exact(self):
        image = image_for(self.target)
        self.assertEqual(target_id_for_env(self.target), verify_compiled_target(image, self.target))
        for bad in (b"bad", b"\xe9" + TARGET_MARKER,
                    image_for("heltec_v4_room_server"), image + TARGET_MARKER):
            with self.subTest(size=len(bad)), self.assertRaises(ValueError):
                verify_compiled_target(bad, self.target)

    def test_capacity_requires_space_for_lora_metadata_as_well_as_image(self):
        fits = evaluate_candidate(self.board, self.spec, image_for(self.target), self.version)
        self.assertEqual("combined", fits["decision"])
        self.assertEqual(f"0x{target_id_for_env(self.target):08x}", fits["target_id"])
        tight = evaluate_candidate(self.board, self.spec,
                                   image_for(self.target, LEGACY_SLOT_BYTES - 2048), self.version)
        self.assertLess(tight["image_bytes"], LEGACY_SLOT_BYTES)
        self.assertGreater(tight["mota_staging_bytes"], LEGACY_SLOT_BYTES)
        self.assertEqual("oversize", tight["decision"])
        oversized = evaluate_candidate(self.board, self.spec,
                                       image_for(self.target, LEGACY_SLOT_BYTES + 1), self.version)
        self.assertEqual("oversize", oversized["decision"])

    def test_candidate_chip_and_flash_header_must_match_the_board(self):
        image = bytearray(image_for(self.target))
        image[12] = 5  # C3, not S3
        with self.assertRaisesRegex(ValueError, "chip or flash header"):
            evaluate_candidate(self.board, self.spec, bytes(image), self.version)
        image[12] = 9
        image[3] = 0x2f  # 4 MiB, not 16 MiB
        with self.assertRaisesRegex(ValueError, "chip or flash header"):
            evaluate_candidate(self.board, self.spec, bytes(image), self.version)

    def test_only_successful_real_candidate_can_produce_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "platformio.ini").write_text("[platformio]\n", encoding="utf-8")
            build_dir, output = root / "build", root / "proof"
            env = expander_environment(self.board, self.target)
            image_path = build_dir / env / "firmware.bin"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(image_for(self.target))  # stale output
            with mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "pio")):
                with self.assertRaises(subprocess.CalledProcessError):
                    build_expander_candidates([self.board], self.version, 1, root, build_dir, {}, output)
            self.assertFalse(image_path.exists())
            self.assertFalse((output / (self.board + ".json")).exists())

            def successful_build(command, **kwargs):
                self.assertEqual("pio", command[0])
                self.assertTrue(kwargs["check"])
                image_path.write_bytes(image_for(self.target))
            with mock.patch("subprocess.run", side_effect=successful_build):
                build_expander_candidates([self.board], self.version, 1, root, build_dir, {}, output)
            candidate = (output / (self.board + ".bin")).read_bytes()
            evidence = json.loads((output / (self.board + ".json")).read_text())
            self.assertEqual(evaluate_candidate(self.board, self.spec, candidate, self.version), evidence)
            self.assertEqual("combined", evidence["decision"])

    def test_build_refuses_symlinked_output_before_unlink_or_pio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "platformio.ini").write_text("[platformio]\n", encoding="utf-8")
            build = root / "build"
            build.mkdir()
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "firmware.bin"
            sentinel.write_bytes(b"preserve")
            link = build / expander_environment(self.board, self.target)
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks unavailable")
            with mock.patch("subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "unsafe expander build output path"):
                    build_expander_candidates([self.board], self.version, 1,
                                              root, build, {}, root / "proof")
                run.assert_not_called()
            self.assertEqual(b"preserve", sentinel.read_bytes())


if __name__ == "__main__":
    unittest.main()
