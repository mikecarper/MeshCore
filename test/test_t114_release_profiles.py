"""No-build T114 release selection, packaged evidence and picker metadata checks."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_picker_controls as controls
import package_firmware_picker as offline
import t114_profiles as t114

SOURCE = "a" * 40
FAMILY = "v1.18.0.1.preview1-aaaaaaaa"
TAG = "heltec-t114-v1.18.0.1.preview1"
CANONICAL = "Heltec_t114_repeater"
LEGACY = "Heltec_t114_without_display_repeater"
ROLES = ("repeater", "repeater_bridge_rs232", "room_server",
         "companion_radio_ble", "companion_radio_usb", "companion_radio_full")
OTA_ROLES = tuple(role + "_lora_ota_no_external_sensors" for role in
                  ("repeater", "repeater_bridge_rs232", "room_server"))
DEFINES = dict(HELTEC_T114="1", T114_DISPLAY_AUTODETECT="1", DISPLAY_CLASS="ST7789Display")


def manifest(target=CANONICAL, proof=True):
    return dict(target=target, platform="NRF52_PLATFORM", verified=True,
                build_profile="full", source_commit=SOURCE,
                capabilities=[t114.CAPABILITY] if proof else [],
                verification=[dict(capability=t114.CAPABILITY, source="packaged application",
                                   evidence=t114.MARKER, present=True)] if proof else [])


class T114ReleaseProfileTests(unittest.TestCase):
    def test_only_exact_legacy_role_names_map_to_the_canonical_target(self):
        for role in ROLES + OTA_ROLES + tuple(role + suffix for role in OTA_ROLES for suffix in ("-full", "-reduced", "-full-ota", "-reduced-ota")):
            self.assertEqual(t114.canonical_target("Heltec_t114_without_display_" + role),
                             "Heltec_t114_" + role)
        for target in (CANONICAL, None, "Heltec_t114_without_display_other",
                       LEGACY + "_unknown", "heltec_t096_without_display_repeater"):
            self.assertIsNone(t114.canonical_target(target))

    def test_metadata_needs_resolved_display_flags_packaged_proof_and_source(self):
        self.assertEqual(t114.runtime_metadata(manifest(), DEFINES), dict(
            displayAuto=True, displayController="st7789", displaySource=SOURCE))
        self.assertEqual(t114.runtime_metadata(manifest(proof=False), DEFINES), {},
                         "today's recipes must not relabel an old published manifest")
        for changed in (dict(HELTEC_T114="0"), dict(T114_DISPLAY_AUTODETECT="0"),
                        dict(DISPLAY_CLASS="NullDisplayDriver")):
            with self.subTest(defines=changed), self.assertRaises(ValueError):
                t114.runtime_metadata(manifest(), {**DEFINES, **changed})
        for changed in (dict(platform="ESP32_PLATFORM"), dict(verified=False), dict(verification=[]),
                        dict(target="other_repeater"), dict(source_commit="a" * 8),
                        dict(verification=[dict(capability=t114.CAPABILITY, present=True,
                            source="linked image", evidence=t114.MARKER)]),
                        dict(verification=[dict(capability=t114.CAPABILITY, present=True,
                            source="packaged application", evidence="unrelated display text")])):
            with self.subTest(manifest=changed), self.assertRaises(ValueError):
                t114.runtime_metadata({**manifest(), **changed}, DEFINES)

    def test_real_generator_emits_per_image_controls_and_honors_later_flag_overrides(self):
        with tempfile.TemporaryDirectory(prefix="t114-controls-") as folder:
            stage = Path(folder)
            (stage / "companion").mkdir()
            (stage / "release-plan.json").write_text(json.dumps(dict(source=SOURCE, groups=[
                dict(key="companion", tag=FAMILY)])))
            row = manifest("Heltec_t114_companion_radio_full")
            row.update(platformio_env="sample", files=[row["target"] + "-" + FAMILY + ".uf2"])
            path = stage / "companion/TARGET-MANIFEST.json"
            path.write_text(json.dumps([row]))
            flags = ["-D" + key + "=" + value for key, value in DEFINES.items()]
            config = [["env:sample", [["build_flags", flags]]]]
            generated = controls.generate(stage, config)
            self.assertEqual(generated["familyTag"], FAMILY)
            profile = generated["profiles"][row["target"]]
            self.assertTrue(profile["display"] and profile["displayAuto"])
            self.assertEqual(profile["displaySource"], SOURCE)
            for tail in (["-UT114_DISPLAY_AUTODETECT"], ["-DT114_DISPLAY_AUTODETECT=0"],
                         ["-UDISPLAY_CLASS", "-DDISPLAY_CLASS=NullDisplayDriver"]):
                with self.subTest(tail=tail), self.assertRaises(ValueError):
                    controls.generate(stage, [["env:sample", [["build_flags", flags + tail]]]])
            row["capabilities"] = []; row["verification"] = []
            path.write_text(json.dumps([row]))
            self.assertNotIn("displayAuto", controls.generate(stage, config)["profiles"][row["target"]])

    @unittest.skipIf(os.name == "nt", "local release driver uses POSIX locking")
    def test_resumed_release_drops_aliases_only_with_exact_qualified_profile(self):
        from build_local_release import select_ordinary_full_records
        for role in ROLES + OTA_ROLES:
            canonical = dict(manifest=manifest("Heltec_t114_" + role), files=[])
            legacy = dict(manifest=manifest("Heltec_t114_without_display_" + role, False), files=[])
            before = deepcopy([legacy, canonical])
            for inputs in ([legacy, canonical], [canonical, legacy]):
                self.assertEqual(select_ordinary_full_records(inputs), [canonical])
            self.assertEqual([legacy, canonical], before)
            for changed in (dict(build_profile="standard"), dict(verification=[]),
                            dict(target="Heltec_t114_sensor"), dict(platform="ESP32_PLATFORM")):
                with self.subTest(role=role, changed=changed), self.assertRaisesRegex(ValueError, "automatic-display successor"):
                    t114.omit_legacy_display_records([legacy, dict(manifest={**canonical["manifest"], **changed}, files=[])])
            with self.assertRaisesRegex(ValueError, "automatic-display successor"):
                select_ordinary_full_records([legacy])
        for role in OTA_ROLES:
            for suffix in ("-full", "-reduced"):
                legacy = dict(manifest=manifest("Heltec_t114_without_display_" + role + suffix, False), files=[])
                canonical = dict(manifest=manifest("Heltec_t114_" + role + suffix), files=[])
                self.assertEqual(t114.omit_legacy_display_records([legacy, canonical]), [canonical])
                canonical["manifest"]["target"] += "-ota"
                with self.assertRaises(ValueError):
                    t114.omit_legacy_display_records([legacy, canonical])
        for policy in ("full", "reduced"):
            canonical = dict(manifest=manifest(), files=[])
            canonical["manifest"]["capabilities"].append("sensor.profile." + policy)
            legacy = dict(manifest=manifest(LEGACY, False), files=[])
            legacy["manifest"]["capabilities"].append("sensor.profile." + policy)
            self.assertEqual(t114.omit_legacy_display_records([legacy, canonical]), [canonical])
            legacy["manifest"]["capabilities"] = ["sensor.profile." + ("reduced" if policy == "full" else "full")]
            with self.assertRaises(ValueError):
                t114.omit_legacy_display_records([legacy, canonical])

    def test_capability_checker_requires_the_marker_in_the_uploadable_application(self):
        with tempfile.TemporaryDirectory(prefix="t114-proof-") as folder:
            work = Path(folder)
            elf, app, output = work / "firmware.elf", work / "firmware.bin", work / "capabilities.json"
            elf.write_bytes(t114.MARKER.encode())
            command = [sys.executable, "-B", str(ROOT / "scripts/check_firmware_capabilities.py"),
                "--image", str(elf), "--firmware-bin", str(app), "--output", str(output),
                "--target", CANONICAL, "--platform", "NRF52_PLATFORM", "--build-profile", "full",
                "--expect-application", t114.CAPABILITY + "=" + t114.MARKER]
            app.write_bytes(b"display-capable old firmware without automatic detection")
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse(json.loads(output.read_text())["verified"])
            app.write_bytes(b"prefix\0" + t114.MARKER.encode() + b"\0suffix")
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(t114.qualified_auto_display(json.loads(output.read_text())))

    def test_combined_full_companion_and_uart_replace_only_proven_legacy_functions(self):
        for role, successor, required in (
                ("companion_radio_usb", "companion_radio_full", ("companion.usb", "companion.bluetooth")),
                ("companion_radio_ble", "companion_radio_full", ("companion.usb", "companion.bluetooth")),
                ("repeater_bridge_rs232", "repeater", ("bridge.rs232", "bridge.rs232.merged"))):
            legacy = dict(manifest=manifest("Heltec_t114_without_display_" + role, False), files=[])
            primary = dict(manifest=manifest("Heltec_t114_" + successor), files=[])
            primary["manifest"]["capabilities"].extend(required)
            for capability in required:
                primary["manifest"]["verification"].append(dict(capability=capability, present=True,
                    source="packaged application" if capability.endswith(".merged") else "linked image",
                    evidence=capability))
            self.assertEqual(t114.omit_legacy_display_records([legacy, primary]), [primary])
            primary["manifest"]["verification"].pop()
            with self.assertRaises(ValueError):
                t114.omit_legacy_display_records([legacy, primary])

    @unittest.skipIf(os.name == "nt", "Bash release contracts run on Linux/WSL")
    def test_bulk_prefers_resolved_auto_recipe_but_direct_alias_builds_stay_available(self):
        config = []
        for role in ROLES + OTA_ROLES + ("kiss_modem",):
            for prefix in ("Heltec_t114_", "Heltec_t114_without_display_"):
                flags = ["-D" + key + "=" + value for key, value in DEFINES.items()]
                if role == "kiss_modem": flags = ["-DHELTEC_T114=1"]
                config.append(["env:" + prefix + role, [["build_flags", flags]]])
        program = r'''
set -euo pipefail
source build.sh
pio() { echo 'PlatformIO must not run in this test' >&2; return 99; }
SUPPORTED_PIO_ENVS=()
for role in repeater repeater_bridge_rs232 room_server companion_radio_ble companion_radio_usb companion_radio_full repeater_lora_ota_no_external_sensors repeater_bridge_rs232_lora_ota_no_external_sensors room_server_lora_ota_no_external_sensors kiss_modem; do
  for prefix in Heltec_t114_ Heltec_t114_without_display_; do
    target=$prefix$role
    SUPPORTED_PIO_ENVS+=("$target")
    PIO_ENV_PLATFORM_BY_NAME[$target]=NRF52_PLATFORM
    PIO_ENV_BOARD_BY_NAME[$target]=heltec_t114
  done
done
ALL_PIO_ENVS=("${SUPPORTED_PIO_ENVS[@]}")
PIO_CONFIG_JSON=@CONFIG@
for role in repeater repeater_bridge_rs232 room_server; do
  for prefix in Heltec_t114_ Heltec_t114_without_display_; do
    # Model generated environments that are absent from pio project config.
    PIO_ENV_BUILD_BASE_BY_NAME[${prefix}${role}_lora_ota_no_external_sensors]=${prefix}${role}
  done
done
for role in repeater repeater_bridge_rs232 room_server companion_radio_ble companion_radio_usb companion_radio_full repeater_lora_ota_no_external_sensors repeater_bridge_rs232_lora_ota_no_external_sensors room_server_lora_ota_no_external_sensors; do
  legacy=Heltec_t114_without_display_$role
  canonical=Heltec_t114_$role
  [ "$(get_t114_display_replacement "$legacy")" = "$canonical" ]
  is_redundant_bulk_build_target "$legacy"
  is_known_pio_env "$legacy"
  case "$role" in
    *_lora_ota_no_external_sensors) [ "$(get_pio_build_env "$legacy")" = "${legacy%_lora_ota_no_external_sensors}" ] ;;
    *) [ "$(get_pio_build_env "$legacy")" = "$legacy" ] ;;
  esac
  ! get_t114_display_replacement "$canonical"
done
! is_t114_display_autodetect_build Heltec_t114_kiss_modem
! get_t114_display_replacement Heltec_t114_without_display_kiss_modem
BUILD_PROFILE_FOR_TARGET=full
BUILD_APPLICATION_EXPECTATIONS=() BUILD_EXPECTATIONS=() BUILD_CAPABILITIES=() BUILD_REDUCTIONS=()
declare_build_capability_contract Heltec_t114_repeater NRF52_PLATFORM
[[ " ${BUILD_APPLICATION_EXPECTATIONS[*]} " == *" display.autodetect=meshcore.capability.display.autodetect.t114.v1 "* ]]
PIO_CONFIG_JSON=@OVERRIDDEN@
! get_t114_display_replacement Heltec_t114_without_display_repeater
PIO_CONFIG_JSON=@CONFIG@
PIO_ENV_BOARD_BY_NAME[Heltec_t114_repeater]=other-board
! get_t114_display_replacement Heltec_t114_without_display_repeater
PIO_ENV_BOARD_BY_NAME[Heltec_t114_repeater]=heltec_t114
unset 'PIO_ENV_PLATFORM_BY_NAME[Heltec_t114_repeater]'
! get_t114_display_replacement Heltec_t114_without_display_repeater
'''
        overridden = deepcopy(config)
        next(row for row in overridden if row[0] == "env:" + CANONICAL)[1][0][1].append("-UT114_DISPLAY_AUTODETECT")
        program = program.replace("@CONFIG@", shlex.quote(json.dumps(config)))
        program = program.replace("@OVERRIDDEN@", shlex.quote(json.dumps(overridden)))
        result = subprocess.run(["bash", "-c", program], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_standalone_picker_accepts_exact_scoped_tag_without_renaming_its_asset_family(self):
        entry = dict(familyTag=FAMILY, releaseTag=TAG, source=SOURCE, profiles={})
        release = dict(tag_name=TAG, name=TAG, assets=[])
        html = offline.package([release], entry)
        self.assertIn('"releaseTag": "' + TAG + '"', html)
        self.assertIn('"familyTag": "' + FAMILY + '"', html)
        with self.assertRaisesRegex(ValueError, "match the supplied release family"):
            offline.package([{**release, "tag_name": "other-preview"}], entry)


if __name__ == "__main__":
    unittest.main()
