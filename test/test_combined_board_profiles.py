"""Evidence-bound Wio L1 Companion and headless KISS consolidation."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tools/mota")]
import consolidated_profiles as profiles
import motalib

SOURCE = "a" * 40
CASES = (
    dict(target="heltec_v4_r8_kiss_modem", legacy="heltec_v4_r8_tft_kiss_modem",
         key="headlessR8Kiss", capability="modem.headless.heltec_r8",
         marker="meshcore.capability.modem.headless.heltec_r8.v1",
         platform="ESP32_PLATFORM", board="heltec_v4_r8",
         flags=["-DHELTEC_V4_R8=1", "-DMESHCORE_HEADLESS_KISS_COMPAT=1"]),
    dict(target="WioTrackerL1_kiss_modem", legacy="WioTrackerL1Eink_kiss_modem",
         key="headlessWioL1Kiss", capability="modem.headless.wio_l1",
         marker="meshcore.capability.modem.headless.wio_l1.v1",
         platform="NRF52_PLATFORM", board="seeed-wio-tracker-l1",
         flags=["-DWIO_TRACKER_L1=1", "-DMESHCORE_HEADLESS_KISS_COMPAT=1"]),
    dict(target="WioTrackerL1_companion_radio_full", legacy="WioTrackerL1Eink_companion_radio_ble",
         key="unifiedWioL1Display", capability="display.wio_l1.unified",
         marker="meshcore.capability.display.wio_l1_unified.v1",
         platform="NRF52_PLATFORM", board="seeed-wio-tracker-l1",
         flags=["-DWIO_TRACKER_L1=1", "-DWIO_L1_DISPLAY_UNIFIED=1",
                "-DDISPLAY_CLASS=WioL1Display", "-DMAX_CONTACTS=350",
                "-DMAX_GROUP_CHANNELS=40", "-DOFFLINE_QUEUE_SIZE=256"]),
)


def environment(case):
    return dict(board=case["board"], build_flags=case["flags"][:])


def record(case, *, legacy=False):
    target = case["legacy"] if legacy else case["target"]
    manifest = dict(target=target, source_commit=SOURCE, verified=True,
                    build_profile="full", platform=case["platform"],
                    capabilities=["sensor.profile.full"], verification=[])
    if not legacy:
        manifest["capabilities"].append(case["capability"])
        manifest["verification"].append(dict(capability=case["capability"], present=True,
                                             source="packaged application", evidence=case["marker"]))
        if "_companion_radio_" in target:
            for key in ("companion.usb", "companion.bluetooth"):
                manifest["capabilities"].append(key)
                manifest["verification"].append(dict(capability=key, present=True,
                                                     source="linked image", evidence="linked symbol"))
    return dict(manifest=manifest, files=[target + ".bin"])


class CombinedBoardProfileTests(unittest.TestCase):
    def test_each_successor_requires_exact_config_and_source_bound_packaged_evidence(self):
        for case in CASES:
            base = record(case)["manifest"]
            with self.subTest(target=case["target"]):
                self.assertEqual(profiles.runtime_metadata(base, environment(case), SOURCE),
                                 {case["key"]: True, case["key"] + "Source": SOURCE})
            for changes in (dict(verified=False), dict(platform="OTHER"), dict(verification=[]),
                            dict(source_commit="b" * 40), dict(source_commit="a" * 8)):
                with self.subTest(target=case["target"], changes=changes), self.assertRaises(ValueError):
                    profiles.runtime_metadata({**base, **changes}, environment(case), SOURCE)
            for changes in (dict(present=False), dict(present=1), dict(source="linked image"),
                            dict(evidence="driver linked")):
                bad = deepcopy(base)
                bad["verification"][0].update(changes)
                with self.subTest(target=case["target"], changes=changes), self.assertRaises(ValueError):
                    profiles.runtime_metadata(bad, environment(case), SOURCE)

    def test_omission_needs_same_source_build_policy_and_real_packaged_marker(self):
        for case in CASES:
            old, new = record(case, legacy=True), record(case)
            self.assertEqual(profiles.omit_redundant_records([old, new]), [new])
            self.assertEqual(profiles.omit_redundant_records([old]), [old])
            faults = (dict(verified=False), dict(platform="OTHER"), dict(verification=[]),
                      dict(source_commit="b" * 40), dict(source_commit="a" * 8),
                      dict(build_profile="reduced"), dict(capabilities=["sensor.profile.full"]),
                      dict(capabilities=[case["capability"]]),
                      dict(capabilities=[case["capability"], "sensor.profile.reduced"]))
            for changes in faults:
                candidate = deepcopy(new)
                candidate["manifest"].update(changes)
                with self.subTest(target=case["target"], changes=changes):
                    self.assertEqual(profiles.omit_redundant_records([old, candidate]), [old, candidate])

    def test_companion_replacement_keeps_both_actual_transports(self):
        case = CASES[2]
        old, new = record(case, legacy=True), record(case)
        for capability in ("companion.usb", "companion.bluetooth"):
            for changes in (dict(present=False), dict(source="packaged application"), dict(evidence="")):
                bad = deepcopy(new)
                for row in bad["manifest"]["verification"]:
                    if row["capability"] == capability:
                        row.update(changes)
                with self.subTest(capability=capability, changes=changes):
                    self.assertEqual(profiles.omit_redundant_records([old, bad]), [old, bad])

    def test_resumed_wio_ble_profile_retires_only_with_matching_sensor_policy(self):
        old, new = record(CASES[2], legacy=True), record(CASES[2])
        old["manifest"]["build_profile"] = "standard"
        self.assertEqual(profiles.omit_redundant_records([old, new]), [new])
        old["manifest"]["capabilities"] = ["sensor.profile.reduced"]
        self.assertEqual(profiles.omit_redundant_records([old, new]), [old, new])

    def test_configuration_cannot_fold_a_different_board_overridden_define_or_visible_kiss_display(self):
        for case in CASES:
            canonical, legacy = "env:" + case["target"], "env:" + case["legacy"]
            envs = {canonical: environment(case), legacy: environment(case)}
            self.assertEqual(profiles.configuration_replacement(case["legacy"], envs), case["target"])
            for flag in case["flags"]:
                key = flag[2:].split("=", 1)[0]
                for override in ("-U" + key, "-D" + key + "=0"):
                    bad = deepcopy(envs)
                    bad[canonical]["build_flags"].append(override)
                    with self.subTest(target=case["target"], override=override):
                        self.assertIsNone(profiles.configuration_replacement(case["legacy"], bad))
            for name in (canonical, legacy):
                bad = deepcopy(envs)
                bad[name]["board"] = "different-board"
                self.assertIsNone(profiles.configuration_replacement(case["legacy"], bad))
            if case["key"].startswith("headless"):
                bad = deepcopy(envs)
                bad[canonical]["build_flags"].append("-DDISPLAY_CLASS=SomeDisplay")
                self.assertIsNone(profiles.configuration_replacement(case["legacy"], bad))

    def test_only_exact_old_targets_fold_and_update_ids_are_never_aliased(self):
        for case in CASES:
            self.assertEqual(profiles.replacements(case["legacy"]), [(case["target"], case["key"])])
            self.assertEqual(profiles.replacements(case["legacy"] + "_custom"), [])
            self.assertNotEqual(motalib.target_id_for_env(case["target"]),
                                motalib.target_id_for_env(case["legacy"]))
        for target in ("WioTrackerL1_1W_companion_radio_ble", "WioTrackerL1Eink_repeater",
                       "WioTrackerL1Eink_companion_radio_ble_lora_ota",
                       "heltec_v4_r8_tft_repeater", "heltec_v4_r8_tft_sensor"):
            self.assertEqual(profiles.replacements(target), [], target)

    def test_wio_generated_full_resolves_from_ble_and_retains_capacity_guards(self):
        case = CASES[2]
        envs = {"env:" + case["legacy"]: environment(case),
                "env:WioTrackerL1_companion_radio_ble": environment(case)}
        self.assertEqual(profiles.configuration_replacement(case["legacy"], envs), case["target"])
        for key, value in (("MAX_CONTACTS", "349"), ("MAX_GROUP_CHANNELS", "39"),
                           ("OFFLINE_QUEUE_SIZE", "255")):
            bad = deepcopy(envs)
            bad["env:WioTrackerL1_companion_radio_ble"]["build_flags"].append("-D" + key + "=" + value)
            self.assertIsNone(profiles.configuration_replacement(case["legacy"], bad))


if __name__ == "__main__":
    unittest.main()
