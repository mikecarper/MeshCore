#!/usr/bin/env python3
"""Keep unavailable nRF52 bootloaders visible in release output."""

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import nrf52_embedded_bootloader as bootloader
from nrf52_bootloader_exceptions import render_bootloader_exceptions
import package_cascade_release as package


class BootloaderExceptionReportTests(unittest.TestCase):
    def receipt(self):
        catalog = bootloader.load_manifest()
        hardware = (
            "Heltec_mesh_solar", "LilyGo_T-Echo_Card", "LilyGo_T_Impulse_Plus",
            "MeshTracker_X1", "Nano_G2_Ultra", "ThinkNode_M4", "ThinkNode_M8",
            "muzi_base_uno", "muzi_base_duo", "wio_wm1110", "Heltec_tower_v2",
        )
        exclusions = []
        for board in hardware:
            profiles = [profile for profile in catalog["unavailableProfiles"]
                        if board in profile["meshcoreHardware"]]
            self.assertEqual(len(profiles), 1, board)
            profile = profiles[0]
            exclusions.append({"target": board + "_repeater", "source_hardware": board,
                               "status": profile["qualification"]["status"],
                               "reason": profile["reason"]})
        return {"release": bootloader.VERSION, "exclusions": exclusions}

    def test_exact_pending_boards_and_retired_profile_are_listed_once(self):
        receipt = self.receipt()
        room = deepcopy(receipt["exclusions"][0])
        room["target"] = "Heltec_mesh_solar_room_server"
        receipt["exclusions"].append(room)
        report = render_bootloader_exceptions(receipt)
        self.assertTrue(report.isascii())
        for name in ("Heltec Mesh Solar", "LilyGo T-Echo Card", "LilyGo T-Impulse Plus",
                     "MeshTracker X1", "Nano G2 Ultra", "ThinkNode M4", "ThinkNode M8",
                     "Muzi Base Uno", "Muzi Base Duo", "Wio WM1110 Dev Kit"):
            with self.subTest(board=name):
                self.assertEqual(report.count("**" + name + ":**"), 1)
        pending, retired = report.split("## Retired profile")
        self.assertIn("Heltec_mesh_solar_repeater", pending)
        self.assertIn("Heltec_mesh_solar_room_server", pending)
        self.assertIn("Heltec MeshTower V2", retired)
        self.assertIn("preserves the installed bootloader", report)
        self.assertIn("not imply that all profiles", report)

    def test_inconsistent_or_invalid_inventory_is_rejected(self):
        self.assertEqual(render_bootloader_exceptions(None), "")
        self.assertEqual(render_bootloader_exceptions({"release": "2.4.12", "exclusions": []}), "")
        for bad in (None, "", [], "2.4.12\n"):
            with self.subTest(version=bad), self.assertRaises(ValueError):
                render_bootloader_exceptions({"release": bad, "exclusions": []})
        receipt = self.receipt()
        for field, value in (("status", "qualified"), ("reason", ""),
                             ("reason", "bad\nline"), ("target", "../other"),
                             ("source_hardware", "bad board")):
            invalid = deepcopy(receipt)
            invalid["exclusions"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                render_bootloader_exceptions(invalid)
        duplicate = deepcopy(receipt)
        duplicate["exclusions"].append(deepcopy(duplicate["exclusions"][0]))
        with self.assertRaisesRegex(ValueError, "invalid"):
            render_bootloader_exceptions(duplicate)
        duplicate["exclusions"][-1]["target"] = "another_target"
        duplicate["exclusions"][-1]["reason"] = "Different qualification."
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            render_bootloader_exceptions(duplicate)

    def test_publication_blockers_fail_closed(self):
        package.require_no_publication_blockers({})
        package.require_no_publication_blockers({"publication_blockers": []})
        for blockers in (None, "failure", [""], [42], ["bad\n\u2603"]):
            with self.subTest(blockers=blockers), self.assertRaisesRegex(ValueError, "invalid"):
                package.require_no_publication_blockers({"publication_blockers": blockers})
        with self.assertRaisesRegex(ValueError, "unresolved.*checks failed"):
            package.require_no_publication_blockers({"publication_blockers": ["checks failed"]})

    def test_local_report_must_match_its_qualified_inventory(self):
        receipt = self.receipt()
        manifest = {"nrf52_bootloader_variants": receipt}
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            report = directory / "NRF52-BOOTLOADER-EXCEPTIONS.md"
            package.validate_local_bootloader_report(directory, manifest)  # Older local releases.
            manifest["nrf52_bootloader_exception_report"] = report.name
            with self.assertRaisesRegex(ValueError, "missing"):
                package.validate_local_bootloader_report(directory, manifest)
            report.write_text(render_bootloader_exceptions(receipt), encoding="ascii")
            package.validate_local_bootloader_report(directory, manifest)
            report.write_text("incomplete", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "disagrees"):
                package.validate_local_bootloader_report(directory, manifest)
            manifest["nrf52_bootloader_exception_report"] = "elsewhere.md"
            with self.assertRaisesRegex(ValueError, "invalid"):
                package.validate_local_bootloader_report(directory, manifest)

    def test_local_package_includes_report_and_link_in_each_group(self):
        source = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs, output = root / "input", root / "output"
            inputs.mkdir()
            local_manifest = {"nrf52_bootloader_variants": self.receipt(), "publication_blockers": []}
            (inputs / "manifest.json").write_text(json.dumps(local_manifest), encoding="ascii")
            image = inputs / "fixture.bin"
            image.write_bytes(b"fixture")
            image_manifest = {"target": "fixture_repeater", "artifact_target": "fixture_repeater",
                              "build_profile": "standard", "platform": "ESP32_PLATFORM"}
            records = [{"manifest": image_manifest, "files": [image]}]
            radio = {"frequency": "910.525", "bandwidth": "62.5", "sf": "7", "cr": "5"}
            arguments = ["package_cascade_release.py", "--input", str(inputs), "--output", str(output),
                         "--local-release", "--version", "1.18.0.2", "--commit", source]
            with patch.object(sys, "argv", arguments), \
                    patch.object(package, "collect_local_release", return_value=(records, [], radio)) as collect, \
                    redirect_stdout(io.StringIO()):
                local_manifest["publication_blockers"] = ["checks failed"]
                (inputs / "manifest.json").write_text(json.dumps(local_manifest), encoding="ascii")
                with self.assertRaisesRegex(ValueError, "unresolved.*checks failed"):
                    package.main()
                collect.assert_not_called()
                self.assertFalse(output.exists())
                local_manifest["publication_blockers"] = []
                (inputs / "manifest.json").write_text(json.dumps(local_manifest), encoding="ascii")
                package.main()
            assets = output / "repeater-room"
            report = assets / "NRF52-BOOTLOADER-EXCEPTIONS.md"
            self.assertEqual(report.read_text(encoding="ascii"), render_bootloader_exceptions(self.receipt()))
            self.assertIn(report.name, (assets / "BUILD-NOTES.txt").read_text(encoding="ascii"))
            self.assertIn(report.name, (output / "repeater-room-notes.md").read_text(encoding="ascii"))
            self.assertIn(hashlib.sha256(report.read_bytes()).hexdigest() + "  " + report.name,
                          (assets / "SHA256SUMS.txt").read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
