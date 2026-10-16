"""No-build shared-slot release contracts; existing OTA identities stay distinct."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools/mota"))
import station_g3_profiles as slots
import motalib
import generate_picker_controls as controls

SOURCE = "a"*40
CANONICAL = "Station_G3_ESP32_repeater"
LEGACY = "Station_G3_ESP32_r2_repeater"
ROLES = ("repeater", "room_server", "sensor", "repeater_observer_mqtt",
         "room_server_observer_mqtt")
DEFINES = {"STATION_G3_ESP32": "1", "USE_SX1262": "1", "RADIO_CLASS": "CustomSX1262"}


def manifest(target=CANONICAL, proof=True):
    return dict(target=target, platform="ESP32_PLATFORM", verified=True,
                build_profile="full", source_commit=SOURCE,
                capabilities=[slots.CAPABILITY] if proof else [],
                verification=[dict(capability=slots.CAPABILITY, source="packaged application",
                                   evidence=slots.MARKER, present=True)] if proof else [])


class StationG3ReleaseTests(unittest.TestCase):
    def test_exact_known_aliases_preserve_every_role_and_profile(self):
        for role in ROLES:
            self.assertEqual(slots.canonical_target("Station_G3_ESP32_r2_"+role),
                             "Station_G3_ESP32_"+role)
        for value in (CANONICAL, "Station_G2_r2_repeater", "heltec_v4_r2_repeater",
                      LEGACY+"_sx1280", LEGACY+"_lr1121", LEGACY+"_unknown", None):
            self.assertIsNone(slots.canonical_target(value))

    def test_resume_prefers_proven_exact_successor_without_mutation(self):
        for role in ROLES:
            canonical = dict(manifest=manifest("Station_G3_ESP32_"+role), files=[])
            legacy = dict(manifest=manifest("Station_G3_ESP32_r2_"+role, False), files=[])
            original = deepcopy([legacy, canonical])
            for records in ([legacy, canonical], [canonical, legacy]):
                self.assertEqual(slots.omit_redundant_slot_records(records), [canonical])
            self.assertEqual([legacy, canonical], original)
            self.assertEqual(slots.omit_redundant_slot_records([legacy]), [legacy])

    def test_missing_proof_wrong_role_profile_platform_do_not_replace_legacy(self):
        legacy = dict(manifest=manifest(LEGACY, False), files=[])
        for change in (
                dict(verification=[]), dict(capabilities=[]), dict(verified=False),
                dict(platform="NRF52_PLATFORM"), dict(build_profile="standard"),
                dict(target="Station_G3_ESP32_room_server"),
                dict(verification=[dict(capability=slots.CAPABILITY, present=True,
                    source="linked image", evidence=slots.MARKER)]),
                dict(verification=[dict(capability=slots.CAPABILITY, present=True,
                    source="packaged application", evidence="unrelated CLI text")])):
            canonical = dict(manifest={**manifest(), **change}, files=[])
            with self.subTest(change=change):
                self.assertEqual(slots.omit_redundant_slot_records([legacy, canonical]),
                                 [legacy, canonical])

    def test_metadata_requires_linked_board_radio_proof_and_exact_source(self):
        expected = dict(radioSlotAuto=True, radioSlotFamily="sx1262", radioSlotSource=SOURCE)
        self.assertEqual(slots.runtime_metadata(manifest(), DEFINES), expected)
        self.assertEqual(slots.runtime_metadata(manifest(proof=False), DEFINES), {})
        for changed in (dict(RADIO_CLASS="CustomLR1121"), dict(USE_SX1262="0"),
                        dict(STATION_G3_ESP32="0")):
            with self.subTest(defines=changed), self.assertRaises(ValueError):
                slots.runtime_metadata(manifest(), {**DEFINES, **changed})
        for changed in (dict(verification=[]), dict(source_commit="a"*8),
                        dict(target="Station_G2_repeater"), dict(platform="NRF52_PLATFORM")):
            with self.subTest(manifest=changed), self.assertRaises(ValueError):
                slots.runtime_metadata({**manifest(), **changed}, DEFINES)

    def test_real_controls_generator_publishes_only_release_bound_application_proof(self):
        with tempfile.TemporaryDirectory(prefix="g3-slot-controls-") as folder:
            stage=Path(folder)
            (stage/"companion").mkdir()
            plan=dict(source=SOURCE, groups=[dict(key="companion", tag="v2.0.0-dev-aaaaaaaa")])
            (stage/"release-plan.json").write_text(json.dumps(plan))
            row=manifest()
            row["platformio_env"]=CANONICAL
            row["files"]=[CANONICAL+"-full-usb-wifi-v2.0.0-dev-aaaaaaaa.bin"]
            (stage/"companion/TARGET-MANIFEST.json").write_text(json.dumps([row]))
            config=[["env:"+CANONICAL, [["build_flags", [
                "-D"+key+"="+value for key,value in DEFINES.items()]]]]]
            result=controls.generate(stage, config)
            self.assertEqual(result["profiles"][CANONICAL+"-full-usb-wifi"]["radioSlotAuto"], True)
            row["verification"][0]["source"]="linked image"
            (stage/"companion/TARGET-MANIFEST.json").write_text(json.dumps([row]))
            with self.assertRaises(ValueError):
                controls.generate(stage, config)

    def test_capability_checker_rejects_elf_only_marker_and_accepts_packaged_application(self):
        with tempfile.TemporaryDirectory(prefix="g3-slot-proof-") as folder:
            work=Path(folder)
            elf,app,output=work/"firmware.elf",work/"firmware.bin",work/"capabilities.json"
            elf.write_bytes(slots.MARKER.encode())
            command=[sys.executable, "-B", str(ROOT/"scripts/check_firmware_capabilities.py"),
                "--image", str(elf), "--firmware-bin", str(app), "--output", str(output),
                "--target", CANONICAL, "--platform", "ESP32_PLATFORM", "--build-profile", "full",
                "--expect-application", slots.CAPABILITY+"="+slots.MARKER]
            app.write_bytes(b"unrelated CLI strings, no shared-slot implementation")
            failed=subprocess.run(command, text=True, capture_output=True, timeout=20)
            self.assertEqual(failed.returncode, 1, failed.stdout+failed.stderr)
            rejected=json.loads(output.read_text())
            self.assertFalse(rejected["verified"])
            self.assertFalse(slots.qualified_auto_slots(rejected))
            app.write_bytes(b"application prefix\0"+slots.MARKER.encode()+b"\0suffix")
            passed=subprocess.run(command, text=True, capture_output=True, timeout=20)
            self.assertEqual(passed.returncode, 0, passed.stdout+passed.stderr)
            accepted=json.loads(output.read_text())
            self.assertTrue(slots.qualified_auto_slots(accepted))

    @unittest.skipIf(os.name=="nt", "local release driver uses POSIX locking")
    def test_real_local_release_selection_preserves_unmatched_old_records(self):
        from build_local_release import select_ordinary_full_records
        canonical=dict(manifest=manifest(), files=[])
        legacy=dict(manifest=manifest(LEGACY, False), files=[])
        self.assertEqual(select_ordinary_full_records([legacy, canonical]), [canonical])
        self.assertEqual(select_ordinary_full_records([legacy]), [legacy])

    def test_no_ota_identity_alias_or_rehash_was_introduced(self):
        for role in ROLES:
            ordinary, legacy = "Station_G3_ESP32_"+role, "Station_G3_ESP32_r2_"+role
            self.assertNotEqual(motalib.target_id_for_env(ordinary), motalib.target_id_for_env(legacy))
            self.assertNotEqual(motalib.hardware_id_for_env(ordinary), motalib.hardware_id_for_env(legacy))
            for target in (ordinary, legacy):
                expected = int.from_bytes(hashlib.sha256(target.encode()).digest()[:4], "little")
                self.assertEqual(motalib.target_id_for_env(target), expected)

    @unittest.skipIf(os.name=="nt", "Bash contract is exercised on Linux/WSL")
    def test_bulk_skips_only_matching_sx1262_alias_but_direct_build_stays_available(self):
        config=[]
        for role in ROLES:
            for prefix in ("Station_G3_ESP32_", "Station_G3_ESP32_r2_"):
                config.append(["env:"+prefix+role, [["build_flags", [
                    "-DUSE_SX1262=1", "-DRADIO_CLASS=CustomSX1262"]]]])
        program=r'''
set -euo pipefail
source build.sh
pio() { echo 'forbidden PlatformIO call' >&2;return 99; }
SUPPORTED_PIO_ENVS=()
for role in repeater room_server sensor repeater_observer_mqtt room_server_observer_mqtt; do
  for prefix in Station_G3_ESP32_ Station_G3_ESP32_r2_; do
    target=$prefix$role
    SUPPORTED_PIO_ENVS+=("$target")
    PIO_ENV_PLATFORM_BY_NAME[$target]=ESP32_PLATFORM
    PIO_ENV_BOARD_BY_NAME[$target]=station-g3-esp32
  done
done
ALL_PIO_ENVS=("${SUPPORTED_PIO_ENVS[@]}")
PIO_CONFIG_JSON=@CONFIG@
for role in repeater room_server sensor repeater_observer_mqtt room_server_observer_mqtt; do
  legacy=Station_G3_ESP32_r2_$role
  canonical=Station_G3_ESP32_$role
  [ "$(get_station_g3_shared_slot_replacement "$legacy")" = "$canonical" ]
  is_redundant_bulk_build_target "$legacy"
  is_known_pio_env "$legacy"
  [ "$(get_pio_build_env "$legacy")" = "$legacy" ]
  ! get_station_g3_shared_slot_replacement "$canonical"
done
legacy=Station_G3_ESP32_r2_sensor
canonical=Station_G3_ESP32_sensor
PIO_ENV_BOARD_BY_NAME[$canonical]=other-board
! get_station_g3_shared_slot_replacement "$legacy"
PIO_ENV_BOARD_BY_NAME[$canonical]=station-g3-esp32
PIO_ENV_PLATFORM_BY_NAME[$canonical]=NRF52_PLATFORM
! get_station_g3_shared_slot_replacement "$legacy"
PIO_ENV_PLATFORM_BY_NAME[$canonical]=ESP32_PLATFORM
PIO_CONFIG_JSON=@WRONG_RADIO@
! get_station_g3_shared_slot_replacement "$legacy"
unset 'PIO_ENV_PLATFORM_BY_NAME[Station_G3_ESP32_sensor]'
! get_station_g3_shared_slot_replacement "$legacy"
'''
        bad=deepcopy(config)
        next(row for row in bad if row[0]=="env:Station_G3_ESP32_sensor")[1][0][1].extend(
            ["-URADIO_CLASS", "-DRADIO_CLASS=CustomLR1121"])
        program=program.replace("@CONFIG@", shlex.quote(json.dumps(config))).replace(
            "@WRONG_RADIO@", shlex.quote(json.dumps(bad)))
        result=subprocess.run(["bash", "-c", program], cwd=ROOT, text=True,
                              capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__=="__main__":
    unittest.main()
