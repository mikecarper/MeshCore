"""Optional-screen defaults and exact packaged detection evidence."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import optional_display_profiles as displays
import generate_picker_controls as generator
from test_upstream_observer_recipes import Recipes
from test_t114_autodisplay_recipes import defines

SOURCE = "a" * 40
TARGETS = ("heltec_rc32_repeater", "Heltec_Wireless_Tracker_repeater",
           "heltec_tracker_v1_1_repeater_observer_mqtt", "heltec_v4_r8_tft_repeater")


def manifest(spec, target):
    return dict(target=target, platform="ESP32_PLATFORM", verified=True, source_commit=SOURCE,
                capabilities=[displays.CAPABILITY], verification=[dict(capability=displays.CAPABILITY,
                present=True, source="packaged application", evidence=displays.marker(spec))])


class OptionalDisplayProfilesTest(unittest.TestCase):
    def test_optional_hardware_defaults_and_integral_screens_are_separate(self):
        recipes = Recipes()
        for spec, target in zip(displays.SPECS, TARGETS):
            with self.subTest(target=target):
                flags = defines(recipes.value("env:" + target, "build_flags"))
                self.assertEqual(displays.spec_for_defines(flags, recipes.value("env:" + target, "board")), spec)
                if spec["marker"] == "heltec_r8":
                    self.assertEqual(flags["SDCARD_CS"], "3")
        for target in ("Heltec_T190_repeater_", "LilyGo_T-Echo_Card_repeater",
                       "Heltec_t096_repeater", "Heltec_t1_repeater", "heltec_tracker_v2_repeater",
                       "ThinkNode_M9_repeater_", "heltec_v4_repeater", "heltec_v4_tft_repeater",
                       "heltec_rc32_without_display_repeater", "heltec_rc32_kiss_modem"):
            with self.subTest(target=target):
                flags = defines(recipes.value("env:" + target, "build_flags"))
                self.assertIsNone(displays.spec_for_defines(flags))

    def test_exact_flags_application_marker_controller_and_source_are_required(self):
        recipes = Recipes()
        for spec, target in zip(displays.SPECS, TARGETS):
            flags = defines(recipes.value("env:" + target, "build_flags"))
            row = manifest(spec, target)
            with self.subTest(target=target):
                self.assertEqual(displays.runtime_metadata(row, flags, board=spec["board"]), dict(displayAuto=True,
                    displayController=spec["controller"], displaySource=SOURCE))
                old = {**row, "capabilities": []}
                self.assertEqual(displays.runtime_metadata(old, flags), {})
                for changed in ({spec["flag"]: "0"}, {spec["guard"]: "0"}, {"DISPLAY_CLASS": "NullDisplayDriver"}):
                    with self.assertRaises(ValueError):
                        displays.runtime_metadata(row, {**flags, **changed}, board=spec["board"])
                for changed in ({"verified": False}, {"platform": "NRF52_PLATFORM"},
                                {"source_commit": SOURCE[:8]}, {"verification": []}, {"target": "unrelated_repeater"}):
                    with self.assertRaises(ValueError):
                        displays.runtime_metadata({**row, **changed}, flags, board=spec["board"])
                for key, value in (("present", False), ("source", "linked image"), ("evidence", "unrelated")):
                    bad = deepcopy(row)
                    bad["verification"][0][key] = value
                    with self.assertRaises(ValueError):
                        displays.runtime_metadata(bad, flags, board=spec["board"])
                for board in (None, "different_board"):
                    with self.assertRaises(ValueError):
                        displays.runtime_metadata(row, flags, board=board)
                with self.assertRaisesRegex(ValueError, "source must match"):
                    displays.runtime_metadata(row, flags, "b" * 40, board=spec["board"])

    def test_builder_uses_ordered_flags_and_exact_board_without_starting_platformio(self):
        spec = displays.SPECS[0]
        flags = ["-D HELTEC_RC32", "-DDISPLAY_CLASS=NV3001BDisplay", "-D RC32_DISPLAY_AUTODETECT=1"]
        for board, extra, expected in ((spec["board"], [], 0), (spec["board"], ["-U RC32_DISPLAY_AUTODETECT"], 1),
                                      (spec["board"], ["-D RC32_DISPLAY_AUTODETECT=0"], 1), ("other_board", [], 1)):
            config = [["env:sample", [["board", board], ["build_flags", flags + extra]]]]
            result = subprocess.run([sys.executable, str(ROOT / "scripts/optional_display_profiles.py"), "sample"],
                                    input=json.dumps(config), text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, expected, result.stderr)
            if not expected:
                self.assertEqual(result.stdout.strip(), displays.marker(spec))

    def test_real_generator_binds_each_optional_board_to_uploadable_files(self):
        recipes = Recipes()
        for spec, target in zip(displays.SPECS, TARGETS):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                stage = Path(folder)
                (stage / "companion").mkdir()
                family = "v1.18.0.1-dev-aaaaaaaa"
                (stage / "release-plan.json").write_text(json.dumps(dict(source=SOURCE,
                    groups=[dict(key="companion", tag=family)])))
                row = manifest(spec, target)
                row.update(platformio_env="sample", files=[target + "-" + family + suffix for suffix in (".bin", "-merged.bin")])
                (stage / "companion/TARGET-MANIFEST.json").write_text(json.dumps([row]))
                flags = defines(recipes.value("env:" + target, "build_flags"))
                result = generator.generate(stage, [["env:sample", [["board", spec["board"]],
                    ["build_flags", ["-D" + k + "=" + v for k, v in flags.items()]]]]])
                self.assertEqual(result["profiles"][target]["displaySource"], SOURCE)
                self.assertEqual(result["profiles"][target]["displayController"], spec["controller"])
                self.assertTrue(result["profiles"][target]["displayAuto"])

    def test_elf_marker_alone_cannot_qualify_the_downloaded_esp32_application(self):
        spec = displays.SPECS[0]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            elf, app, output = root / "firmware.elf", root / "firmware.bin", root / "capabilities.json"
            elf.write_bytes(displays.marker(spec).encode())
            command = [sys.executable, "-B", str(ROOT / "scripts/check_firmware_capabilities.py"),
                "--image", str(elf), "--firmware-bin", str(app), "--output", str(output),
                "--target", TARGETS[0], "--platform", "ESP32_PLATFORM", "--build-profile", "full",
                "--expect-application", displays.CAPABILITY + "=" + displays.marker(spec)]
            app.write_bytes(b"old display firmware without detection")
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse(json.loads(output.read_text())["verified"])
            app.write_bytes(b"prefix\0" + displays.marker(spec).encode() + b"\0suffix")
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(output.read_text())["verified"])


if __name__ == "__main__":
    unittest.main()
