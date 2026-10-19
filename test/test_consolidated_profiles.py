"""No-build contracts for evidence-bound firmware choice consolidation."""
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
import consolidated_profiles as consolidated


SOURCE = "a" * 40
CASES = (
    dict(target="PicoW_companion_radio", legacy="PicoW_companion_radio_wifi",
         board="rpipicow", platform="RP2040_PLATFORM", key="combinedCompanion",
         capability="companion.picow.combined",
         marker="meshcore.capability.companion.picow.combined.v1",
         flags=["-DPICOW_COMBINED_COMPANION=1", "-DENABLE_USB_INTERFACE=1",
                "-DENABLE_WIFI_INTERFACE=1", "-DPIO_FRAMEWORK_ARDUINO_ENABLE_BLUETOOTH=1"]),
    dict(target="heltec_v4_repeater", legacy="heltec_v4_expansionkit_repeater",
         board="heltec_v4", platform="ESP32_PLATFORM", key="oledExpansionCompatible",
         capability="hardware.heltec_v4.oled_kit",
         marker="meshcore.capability.hardware.heltec_v4.oled_kit.v1",
         flags=["-DHELTEC_V4_OLED_KIT_COMPAT=1", "-DHELTEC_LORA_V4_OLED=1",
                "-DDISPLAY_CLASS=SSD1306Display", "-DENV_PIN_SDA=4", "-DENV_PIN_SCL=3"]),
    dict(target="heltec_v4_r8_tft_room_server",
         legacy="heltec_v4_r8_tft_portrait_room_server_observer_mqtt",
         board="heltec_v4_r8", platform="ESP32_PLATFORM", key="displayRotation",
         capability="display.rotation.runtime",
         marker="meshcore.capability.display.rotation.heltec_r8.v1",
         flags=["-DHELTEC_TFT_RUNTIME_ROTATION=1", "-DHELTEC_V4_R8_TFT=1",
                "-DDISPLAY_CLASS=ST7789LCDDisplay"]),
    dict(target="heltec_v4_tft_companion_radio_full_femon",
         legacy="heltec_v4_expansionkit_tft_companion_radio_full_femon",
         board="heltec_v4", platform="ESP32_PLATFORM", key="tftExpansionCompatible",
         capability="hardware.heltec_v4.tft_kit",
         marker="meshcore.capability.hardware.heltec_v4.tft_kit.v1",
         flags=["-DHELTEC_V4_TFT_KIT_COMPAT=1", "-DHELTEC_LORA_V4_TFT=1",
                "-DDISPLAY_CLASS=ST7789LCDDisplay"]),
)
ACCESSORY_CASES = (
    dict(base="muzi_base_uno", legacy="muzi_base_uno_superIO", board="muzi_base",
         marker="muzi_superio", roles=("ble", "full"), flag="MUZI_BASE_ACCESSORY_AUTODETECT",
         flags=["-DMUZI_BASE=1", "-DMUZI_BASE_ACCESSORY_AUTODETECT=1", "-DDISPLAY_CLASS=SH1107Display",
                "-DRADIO_CLASS=CustomSX1262", "-DUSE_SX1262=1"]),
    dict(base="muzi_base_duo", legacy="muzi_base_duo_superIO", board="muzi_base",
         marker="muzi_superio", roles=("ble", "full"), flag="MUZI_BASE_ACCESSORY_AUTODETECT",
         flags=["-DMUZI_BASE=1", "-DMUZI_BASE_ACCESSORY_AUTODETECT=1", "-DDISPLAY_CLASS=SH1107Display",
                "-DRADIO_CLASS=CustomLR1121", "-DUSE_LR1121=1"]),
    dict(base="LilyGo_T-Echo-Lite", legacy="LilyGo_T-Echo-Lite_non_shell", board="t-echo",
         marker="techo_lite", roles=("usb", "ble", "full"), flag="TECHO_LITE_DISPLAY_AUTODETECT",
         flags=["-DLILYGO_TECHO_LITE=1", "-DTECHO_LITE_DISPLAY_AUTODETECT=1",
                "-DDISPLAY_CLASS=GxEPDDisplay", "-DEINK_DISPLAY_MODEL=GxEPD2_122_T61"]),
)


def environment(case):
    return dict(board=case["board"], build_flags=list(case["flags"]))


def manifest(case, target=None, proof=True):
    return dict(target=target or case["target"], verified=True,
                platform=case["platform"], source_commit=SOURCE,
                build_profile="full", capabilities=[case["capability"]] if proof else [],
                verification=[dict(capability=case["capability"], present=True,
                                   source="packaged application", evidence=case["marker"])] if proof else [])


def record(case, target=None, proof=True):
    row = manifest(case, target, proof)
    return dict(manifest=row, files=[row["target"] + ".bin"])


class ConsolidatedProfileTests(unittest.TestCase):
    def test_r8_rotation_proof_covers_all_four_supported_roles(self):
        case = next(case for case in CASES if case["key"] == "displayRotation")
        for role in ("repeater", "room_server", "sensor", "companion_radio_full"):
            target = "heltec_v4_r8_tft_" + role
            with self.subTest(target=target):
                self.assertEqual(consolidated.runtime_metadata(manifest(case, target), environment(case), SOURCE),
                                 dict(displayRotation=True, displayRotationSource=SOURCE))
        for role in ("kiss_modem", "partition_expander", "other", "companion_radio_wifi"):
            with self.subTest(role=role), self.assertRaises(ValueError):
                consolidated.runtime_metadata(manifest(case, "heltec_v4_r8_tft_" + role), environment(case), SOURCE)

    def test_original_v4_tft_full_kit_alias_keeps_identical_wiring_and_battery_guard(self):
        from test_upstream_observer_recipes import Recipes
        from test_t114_autodisplay_recipes import defines
        recipes = Recipes()
        canonical = "env:heltec_v4_tft_companion_radio_full_femon"
        legacy = "env:heltec_v4_expansionkit_tft_companion_radio_full_femon"
        flags = defines(recipes.value(canonical, "build_flags"))
        self.assertEqual(flags, defines(recipes.value(legacy, "build_flags")))
        for key, value in dict(HELTEC_V4_TFT_KIT_COMPAT="1", DISPLAY_CLASS="ST7789LCDDisplay",
                               AUTO_SHUTDOWN_MILLIVOLTS="3400", MESHCORE_REQUIRES_COMPANION_RADIO_FULL="1",
                               PIN_BOARD_SDA="4", PIN_BOARD_SCL="3", PIN_TFT_RST="18", PIN_TFT_LEDA_CTL="21",
                               PIN_TFT_CS="15", PIN_TFT_DC="16", PIN_TFT_SCL="17", PIN_TFT_SDA="33").items():
            self.assertEqual(flags.get(key), value, key)
        for option in ("board", "board_build.partitions", "board_build.flash_mode", "build_src_filter", "lib_deps"):
            self.assertEqual(recipes.value(canonical, option), recipes.value(legacy, option), option)

    def test_exact_current_configuration_and_packaged_marker_emit_source_bound_metadata(self):
        for case in CASES:
            with self.subTest(target=case["target"]):
                self.assertEqual(consolidated.runtime_metadata(manifest(case), environment(case), SOURCE),
                                 {case["key"]: True, case["key"] + "Source": SOURCE})
                self.assertEqual(consolidated.runtime_metadata(manifest(case, proof=False),
                                                               environment(case), SOURCE), {},
                                 "Current recipes must not relabel a historical application")

    def test_metadata_rejects_unverified_or_wrong_application_evidence(self):
        for case in CASES:
            base = manifest(case)
            invalid_rows = (
                {"verified": False}, {"platform": "OTHER_PLATFORM"},
                {"target": "other_board_repeater"}, {"verification": []},
                {"verification": [{**base["verification"][0], "present": False}]},
                {"verification": [{**base["verification"][0], "present": 1}]},
                {"verification": [{**base["verification"][0], "source": "linked image"}]},
                {"verification": [{**base["verification"][0], "evidence": "a familiar driver name"}]},
                {"verification": [{**base["verification"][0], "capability": "other.capability"}]},
            )
            for changed in invalid_rows:
                with self.subTest(target=case["target"], changed=changed), self.assertRaises(ValueError):
                    consolidated.runtime_metadata({**base, **changed}, environment(case), SOURCE)

    def test_metadata_requires_full_matching_release_source(self):
        for case in CASES:
            for source in (None, "a" * 8, "g" * 40, "A" * 40, "b" * 40):
                with self.subTest(target=case["target"], source=source), self.assertRaises(ValueError):
                    consolidated.runtime_metadata({**manifest(case), "source_commit": source},
                                                  environment(case), SOURCE)
            for release_source in (None, "a" * 8, "b" * 40):
                with self.subTest(target=case["target"], release_source=release_source), self.assertRaises(ValueError):
                    consolidated.runtime_metadata(manifest(case), environment(case), release_source)

    def test_metadata_rejects_unknown_board_and_later_define_or_undefine_overrides(self):
        for case in CASES:
            with self.subTest(target=case["target"], board="unknown"), self.assertRaises(ValueError):
                consolidated.runtime_metadata(manifest(case), {**environment(case), "board": "unknown"}, SOURCE)
            for flag in case["flags"]:
                key = flag[2:].split("=", 1)[0]
                for override in ("-U" + key, "-D" + key + "=0"):
                    changed = {**environment(case), "build_flags": case["flags"] + [override]}
                    with self.subTest(target=case["target"], override=override), self.assertRaises(ValueError):
                        consolidated.runtime_metadata(manifest(case), changed, SOURCE)

    def test_replacements_are_limited_to_known_exact_legacy_targets(self):
        for case in CASES:
            self.assertIn((case["target"], case["key"]), consolidated.replacements(case["legacy"]))
            self.assertEqual(consolidated.replacements(case["target"]), [])
            self.assertEqual(consolidated.replacements(case["legacy"] + "_custom"), [])
        for target in ("other_PicoW_companion_radio_usb", "heltec_v4_expansionkit_sensor",
                       "heltec_v4_r8_tft_portrait_sensor_observer_mqtt", "other_without_display_repeater"):
            with self.subTest(target=target):
                self.assertEqual(consolidated.replacements(target), [])

    def test_configuration_replacement_requires_both_known_matching_boards(self):
        for case in CASES:
            canonical = "env:" + case["target"]
            legacy = "env:" + case["legacy"]
            envs = {canonical: environment(case), legacy: environment(case)}
            self.assertEqual(consolidated.configuration_replacement(case["legacy"], envs), case["target"])
            for changed in ({legacy: environment(case)}, {canonical: environment(case)},
                            {canonical: environment(case), legacy: {**environment(case), "board": "other"}},
                            {canonical: {**environment(case), "board": "unknown"},
                             legacy: {**environment(case), "board": "unknown"}}):
                with self.subTest(target=case["target"], envs=changed):
                    self.assertIsNone(consolidated.configuration_replacement(case["legacy"], changed))

    def test_configuration_replacement_preserves_candidate_flag_overrides(self):
        for case in CASES:
            for flag in case["flags"]:
                key = flag[2:].split("=", 1)[0]
                for override in ("-U" + key, "-D" + key + "=0"):
                    envs = {"env:" + case["legacy"]: environment(case),
                            "env:" + case["target"]: {**environment(case),
                                                     "build_flags": case["flags"] + [override]}}
                    with self.subTest(target=case["target"], override=override):
                        self.assertIsNone(consolidated.configuration_replacement(case["legacy"], envs))

    def test_omission_requires_matching_profile_policy_and_real_packaged_successor(self):
        for case in CASES:
            legacy = record(case, case["legacy"], proof=False)
            canonical = record(case)
            legacy["manifest"]["capabilities"].append("sensor.profile.full")
            canonical["manifest"]["capabilities"].append("sensor.profile.full")
            before = deepcopy([legacy, canonical])
            for inputs in ([legacy, canonical], [canonical, legacy]):
                with self.subTest(target=case["target"], order=[r["manifest"]["target"] for r in inputs]):
                    self.assertEqual(consolidated.omit_redundant_records(inputs), [canonical])
            self.assertEqual([legacy, canonical], before)
            self.assertEqual(consolidated.omit_redundant_records([legacy]), [legacy])
            for changed in (dict(build_profile="reduced"), dict(verified=False), dict(verification=[]),
                            dict(platform="OTHER_PLATFORM"), dict(target=case["target"] + "_custom"),
                            dict(capabilities=[case["capability"], "sensor.profile.reduced"]),
                            dict(verification=[{**canonical["manifest"]["verification"][0], "source": "linked image"}]),
                            dict(verification=[{**canonical["manifest"]["verification"][0], "evidence": "other marker"}])):
                candidate = {**canonical, "manifest": {**canonical["manifest"], **changed}}
                with self.subTest(target=case["target"], changed=changed):
                    self.assertEqual(consolidated.omit_redundant_records([legacy, candidate]), [legacy, candidate])

    def test_historical_manifests_are_never_relabelled_by_current_recipe_defaults(self):
        for case in CASES:
            legacy, current_name_old_image = record(case, case["legacy"], False), record(case, proof=False)
            inputs = [legacy, current_name_old_image]
            before = deepcopy(inputs)
            self.assertEqual(consolidated.omit_redundant_records(inputs), inputs)
            self.assertEqual(inputs, before)

    def test_rc32_headless_choice_requires_exact_canonical_autodetection_marker(self):
        target, legacy_target = "heltec_rc32_repeater", "heltec_rc32_without_display_repeater"
        canonical = dict(manifest=dict(target=target, platform="ESP32_PLATFORM", verified=True,
            build_profile="full", capabilities=["display.autodetect"], verification=[dict(
                capability="display.autodetect", present=True, source="packaged application",
                evidence="meshcore.capability.display.autodetect.rc32.v1")]), files=[target + ".bin"])
        legacy = dict(manifest=dict(target=legacy_target, platform="ESP32_PLATFORM", verified=True,
                                    build_profile="full", capabilities=[], verification=[]), files=[legacy_target + ".bin"])
        before = deepcopy([legacy, canonical])
        self.assertEqual(consolidated.omit_redundant_records([legacy, canonical]), [canonical])
        self.assertEqual([legacy, canonical], before)
        for changed in (dict(verified=False), dict(platform="NRF52_PLATFORM"), dict(capabilities=[]),
                        dict(verification=[]), dict(build_profile="reduced"),
                        dict(capabilities=["display.autodetect", "sensor.profile.reduced"]),
                        dict(verification=[{**canonical["manifest"]["verification"][0], "source": "linked image"}]),
                        dict(verification=[{**canonical["manifest"]["verification"][0], "present": False}]),
                        dict(verification=[{**canonical["manifest"]["verification"][0],
                                            "evidence": "meshcore.capability.display.autodetect.heltec_r8.v1"}])):
            candidate = {**canonical, "manifest": {**canonical["manifest"], **changed}}
            with self.subTest(changed=changed):
                self.assertEqual(consolidated.omit_redundant_records([legacy, candidate]), [legacy, candidate])
        self.assertEqual(consolidated.omit_redundant_records([legacy]), [legacy])

    def test_rc32_configuration_successor_requires_resolved_auto_display_recipe(self):
        target, legacy = "heltec_rc32_companion_radio_full", "heltec_rc32_without_display_companion_radio_full"
        flags = ["-DRC32_DISPLAY_AUTODETECT=1", "-DHELTEC_RC32=1", "-DDISPLAY_CLASS=NV3001BDisplay"]
        env = dict(board="heltec-rc32", build_flags=flags)
        envs = {"env:" + target: env, "env:" + legacy: env}
        self.assertEqual(consolidated.configuration_replacement(legacy, envs), target)
        for override in ("-URC32_DISPLAY_AUTODETECT", "-DRC32_DISPLAY_AUTODETECT=0",
                         "-UHELTEC_RC32", "-DHELTEC_RC32=0", "-DDISPLAY_CLASS=NullDisplayDriver"):
            changed = {**envs, "env:" + target: {**env, "build_flags": flags + [override]}}
            with self.subTest(override=override):
                self.assertIsNone(consolidated.configuration_replacement(legacy, changed))
        changed = {key: {**value, "board": "unknown"} for key, value in envs.items()}
        self.assertIsNone(consolidated.configuration_replacement(legacy, changed))

    @unittest.skipIf(os.name == "nt", "Bash release contracts run on Linux/WSL")
    def test_bulk_shell_wiring_omits_aliases_preserves_direct_identity_and_demands_application_marker(self):
        config = []
        setup, checks = [], []
        for case in CASES:
            for target in (case["target"], case["legacy"]):
                config.append(["env:" + target, [["board", case["board"]], ["build_flags", case["flags"]]]])
                setup.append("SUPPORTED_PIO_ENVS+=(" + shlex.quote(target) + ")")
                for mapping, value in (("PIO_ENV_PLATFORM_BY_NAME", case["platform"]),
                                       ("PIO_ENV_BOARD_BY_NAME", case["board"]),
                                       ("PIO_ENV_BUILD_BASE_BY_NAME", target)):
                    setup.append(mapping + "[" + shlex.quote(target) + "]=" + shlex.quote(value))
            checks.append("check_choice " + " ".join(shlex.quote(value) for value in (
                case["legacy"], case["target"], case["platform"],
                case["capability"] + "=" + case["marker"])))
        program = r'''
set -euo pipefail
source build.sh
pio() { echo 'PlatformIO must not run in this test' >&2; return 99; }
@SETUP@
ALL_PIO_ENVS=("${SUPPORTED_PIO_ENVS[@]}")
PIO_CONFIG_JSON=@CONFIG@
BUILD_PROFILE_FOR_TARGET=full
check_choice() {
  local legacy=$1 canonical=$2 platform=$3 expected=$4
  [ "$(get_consolidated_hardware_replacement "$legacy")" = "$canonical" ]
  is_redundant_bulk_build_target "$legacy"
  is_known_pio_env "$legacy"
  [ "$(get_pio_build_env "$legacy")" = "$legacy" ]
  ! get_consolidated_hardware_replacement "$canonical"
  BUILD_APPLICATION_EXPECTATIONS=() BUILD_EXPECTATIONS=() BUILD_CAPABILITIES=() BUILD_REDUCTIONS=()
  declare_build_capability_contract "$canonical" "$platform"
  [[ " ${BUILD_APPLICATION_EXPECTATIONS[*]} " == *" $expected "* ]]
}
@CHECKS@
PIO_CONFIG_JSON=@OVERRIDDEN@
@OVERRIDE_CHECKS@
'''
        overridden = deepcopy(config)
        for case in CASES:
            row = next(row for row in overridden if row[0] == "env:" + case["target"])
            row[1][1][1].append("-U" + case["flags"][0][2:].split("=", 1)[0])
        program = program.replace("@SETUP@", "\n".join(setup))
        program = program.replace("@CHECKS@", "\n".join(checks))
        program = program.replace("@CONFIG@", shlex.quote(json.dumps(config)))
        program = program.replace("@OVERRIDDEN@", shlex.quote(json.dumps(overridden)))
        program = program.replace("@OVERRIDE_CHECKS@", "\n".join(
            "! get_consolidated_hardware_replacement " + shlex.quote(case["legacy"]) for case in CASES))
        result = subprocess.run(["bash", "-c", program], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_controls_generator_requires_packaged_proof_from_its_exact_config(self):
        import generate_picker_controls as controls

        family = "v1.18.0.1.preview1-aaaaaaaa"
        with tempfile.TemporaryDirectory(prefix="consolidated-controls-") as folder:
            stage = Path(folder)
            (stage / "companion").mkdir()
            (stage / "release-plan.json").write_text(json.dumps(dict(
                source=SOURCE, groups=[dict(key="companion", tag=family)])))
            path = stage / "companion/TARGET-MANIFEST.json"
            for case in CASES:
                row = manifest(case)
                row.update(platformio_env=case["target"], files=[case["target"] + "-" + family + ".bin"])
                path.write_text(json.dumps([row]))
                config = [["env:" + case["target"], [["board", case["board"]], ["build_flags", case["flags"]]]]]
                profile = controls.generate(stage, config)["profiles"][case["target"]]
                self.assertIs(profile[case["key"]], True)
                self.assertEqual(profile[case["key"] + "Source"], SOURCE)
                changed = deepcopy(config)
                changed[0][1][1][1].append("-U" + case["flags"][0][2:].split("=", 1)[0])
                with self.subTest(target=case["target"], reason="overridden recipe"), self.assertRaises(ValueError):
                    controls.generate(stage, changed)
                row["verification"] = []
                path.write_text(json.dumps([row]))
                with self.subTest(target=case["target"], reason="missing packaged evidence"), self.assertRaises(ValueError):
                    controls.generate(stage, config)
                row["capabilities"] = []
                path.write_text(json.dumps([row]))
                historical = controls.generate(stage, config)["profiles"][case["target"]]
                self.assertNotIn(case["key"], historical)
                self.assertNotIn(case["key"] + "Source", historical)

    def test_generated_accessory_full_targets_require_exact_radio_board_and_optional_display_recipe(self):
        for case in ACCESSORY_CASES:
            canonical = case["base"] + "_companion_radio_full"
            for base_role in ("usb", "ble"):
                canonical_base = case["base"] + "_companion_radio_" + base_role
                legacy_base = case["legacy"] + "_companion_radio_" + base_role
                env = dict(board=case["board"], build_flags=case["flags"])
                envs = {"env:" + canonical_base: env, "env:" + legacy_base: env}
                for role in case["roles"]:
                    target = case["legacy"] + "_companion_radio_" + role
                    # A BLE request needs its real BLE source; generated Full
                    # can resolve either attached-transport source recipe.
                    role_envs = {**envs, "env:" + target: env} if role != "full" else envs
                    with self.subTest(target=target, canonical_base=canonical_base):
                        self.assertEqual(consolidated.configuration_replacement(target, role_envs), canonical)
                    for changed in (
                            {**env, "board": "unknown"},
                            {**env, "build_flags": case["flags"] + ["-U" + case["flag"]]},
                            {**env, "build_flags": case["flags"] + ["-D" + case["flag"] + "=0"]},
                            {**env, "build_flags": case["flags"] + ["-DDISPLAY_CLASS=NullDisplayDriver"]}):
                        invalid = {**role_envs, "env:" + canonical_base: changed}
                        with self.subTest(target=target, changed=changed):
                            self.assertIsNone(consolidated.configuration_replacement(target, invalid))
                if case["base"].startswith("muzi_base_"):
                    other = next(row for row in ACCESSORY_CASES if row["board"] == "muzi_base"
                                 and row["base"] != case["base"])
                    wrong_radio = {**envs, "env:" + canonical_base: dict(board=case["board"], build_flags=other["flags"])}
                    with self.subTest(target=canonical, wrong_radio=other["base"]):
                        self.assertIsNone(consolidated.configuration_replacement(
                            case["legacy"] + "_companion_radio_full", wrong_radio))
                    other_name = other["base"] + "_companion_radio_" + base_role
                    different_hardware = {"env:" + other_name: env, "env:" + legacy_base: env}
                    self.assertIsNone(consolidated.configuration_replacement(
                        case["legacy"] + "_companion_radio_full", different_hardware))

    def test_accessory_full_omission_requires_packaged_display_and_both_linked_transports(self):
        for case in ACCESSORY_CASES:
            canonical_target = case["base"] + "_companion_radio_full"
            display = dict(capability="display.autodetect", present=True, source="packaged application",
                           evidence="meshcore.capability.display.autodetect." + case["marker"] + ".v1")
            transport = [dict(capability=key, present=True, source="linked image", evidence=symbol)
                         for key, symbol in (("companion.usb", "+++MESHCORE-TERM-START"),
                                             ("companion.bluetooth", "Companion: starting Bluetooth"))]
            canonical = dict(manifest=dict(target=canonical_target, platform="NRF52_PLATFORM", verified=True,
                build_profile="full", capabilities=["display.autodetect", "companion.usb", "companion.bluetooth"],
                verification=[display, *transport]), files=[canonical_target + ".uf2"])
            for role in case["roles"]:
                target = case["legacy"] + "_companion_radio_" + role
                legacy = dict(manifest=dict(target=target, platform="NRF52_PLATFORM", verified=True,
                    build_profile="full" if role == "full" else "standard", capabilities=[], verification=[]),
                    files=[target + ".uf2"])
                before = deepcopy([legacy, canonical])
                with self.subTest(target=target):
                    self.assertEqual(consolidated.omit_redundant_records([legacy, canonical]), [canonical])
                    self.assertEqual([legacy, canonical], before)
                    self.assertEqual(consolidated.omit_redundant_records([legacy]), [legacy])
                invalid = [dict(verified=False), dict(platform="ESP32_PLATFORM"), dict(verification=[]),
                           dict(capabilities=["companion.usb", "companion.bluetooth"]),
                           dict(verification=[{**display, "source": "linked image"}, *transport]),
                           dict(verification=[{**display, "evidence": "meshcore.capability.display.autodetect.rc32.v1"}, *transport])]
                for key in ("companion.usb", "companion.bluetooth"):
                    invalid.append(dict(capabilities=[cap for cap in canonical["manifest"]["capabilities"] if cap != key]))
                    for change in (dict(present=False), dict(source="packaged application"), dict(evidence="")):
                        invalid.append(dict(verification=[{**row, **change} if row["capability"] == key else row
                                                          for row in canonical["manifest"]["verification"]]))
                if role == "full":
                    invalid.append(dict(build_profile="standard"))
                if case["base"].startswith("muzi_base_"):
                    other = "muzi_base_duo" if case["base"] == "muzi_base_uno" else "muzi_base_uno"
                    invalid.append(dict(target=other + "_companion_radio_full"))
                for changed in invalid:
                    candidate = {**canonical, "manifest": {**canonical["manifest"], **changed}}
                    with self.subTest(target=target, changed=changed):
                        self.assertEqual(consolidated.omit_redundant_records([legacy, candidate]), [legacy, candidate])


if __name__ == "__main__":
    unittest.main()
