"""WM1110 retirement requires the exact source-bound combined application."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import consolidated_profiles as consolidated


TARGET = "wio_wm1110_companion_radio_full"
LEGACY = "wio_wm1110_companion_radio_ble"
SOURCE = "a" * 40
BOARD = "seeed-xiao-afruitnrf52-nrf52840"
CAPABILITY = "companion.wm1110.combined"
MARKER = "meshcore.capability.companion.wm1110.combined.v1"
COUNTS = dict(MAX_CONTACTS=350, MAX_GROUP_CHANNELS=40, OFFLINE_QUEUE_SIZE=256)
BASE_FLAGS = ["-DWIO_WM1110=1", "-DBLE_PIN_CODE=123456",
              *("-D" + key + "=" + str(value) for key, value in COUNTS.items())]
FULL_FLAGS = [*BASE_FLAGS, "-DCOMPANION_RADIO_FULL=1", "-DENABLE_USB_INTERFACE=1",
              "-DMESH_PRIMARY_UART_PORT=Serial1"]


def configuration(flags):
    return dict(board=BOARD, build_flags=list(flags))


def records():
    old = dict(target=LEGACY, platform="NRF52_PLATFORM", build_profile="standard",
               verified=True, source_commit=SOURCE, capabilities=["sensor.profile.full"],
               verification=[])
    current = dict(target=TARGET, platform="NRF52_PLATFORM", build_profile="full",
                   verified=True, source_commit=SOURCE,
                   capabilities=["sensor.profile.full", CAPABILITY,
                                 "companion.usb", "companion.bluetooth"],
                   verification=[dict(capability=CAPABILITY, present=True,
                                      source="packaged application", evidence=MARKER),
                                 *[dict(capability=key, present=True, source="linked image",
                                        evidence="actual linked transport")
                                   for key in ("companion.usb", "companion.bluetooth")]])
    return dict(manifest=old, files=[LEGACY + ".zip"]), dict(manifest=current, files=[TARGET + ".zip"])


class Wm1110ConsolidationTests(unittest.TestCase):
    def test_exact_full_recipe_and_packaged_marker_produce_source_bound_metadata(self):
        current = records()[1]["manifest"]
        self.assertEqual(consolidated.runtime_metadata(current, configuration(FULL_FLAGS), SOURCE),
                         dict(wm1110CombinedCompanion=True, wm1110CombinedCompanionSource=SOURCE))
        for flag in FULL_FLAGS:
            key = flag[2:].split("=", 1)[0]
            with self.subTest(removed=key), self.assertRaises(ValueError):
                consolidated.runtime_metadata(current, configuration(FULL_FLAGS + ["-U" + key]), SOURCE)
        for key, value in COUNTS.items():
            with self.subTest(reduced=key), self.assertRaises(ValueError):
                consolidated.runtime_metadata(current, configuration(FULL_FLAGS + [f"-D{key}={value - 1}"]), SOURCE)
        for changes in (dict(board="unreviewed"),
                        dict(build_flags=FULL_FLAGS + ["-DMESH_PRIMARY_UART_PORT=Serial"]),
                        dict(build_flags=FULL_FLAGS + ["-DWIO_WM1110=0"])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                consolidated.runtime_metadata(current, {**configuration(FULL_FLAGS), **changes}, SOURCE)

    def test_exact_ble_recipe_has_one_full_successor_with_no_capacity_reduction(self):
        envs = {"env:" + TARGET: configuration(FULL_FLAGS), "env:" + LEGACY: configuration(BASE_FLAGS)}
        self.assertEqual(consolidated.replacements(LEGACY), [(TARGET, "wm1110CombinedCompanion")])
        self.assertEqual(consolidated.configuration_replacement(LEGACY, envs), TARGET)
        for name in (LEGACY + "_custom", LEGACY + "_ps", "wio_wm1110_companion_radio_usb",
                     "other_wm1110_companion_radio_ble", "wio_wm1110_repeater", TARGET):
            with self.subTest(name=name):
                self.assertEqual(consolidated.replacements(name), [])
                self.assertIsNone(consolidated.configuration_replacement(name, envs))
        self.assertIsNone(consolidated.configuration_replacement(LEGACY, {"env:" + LEGACY: envs["env:" + LEGACY]}))
        for key, value in COUNTS.items():
            changed = deepcopy(envs)
            changed["env:" + LEGACY]["build_flags"].append(f"-D{key}={value + 1}")
            with self.subTest(reduced=key):
                self.assertIsNone(consolidated.configuration_replacement(LEGACY, changed))
            changed["env:" + TARGET]["build_flags"].append(f"-D{key}={value + 1}")
            self.assertEqual(consolidated.configuration_replacement(LEGACY, changed), TARGET)
        for which, change in ((TARGET, dict(board="other")), (LEGACY, dict(board="other")),
                              (LEGACY, dict(build_flags=BASE_FLAGS + ["-UWIO_WM1110"]))):
            changed = deepcopy(envs)
            changed["env:" + which].update(change)
            with self.subTest(which=which, change=change):
                self.assertIsNone(consolidated.configuration_replacement(LEGACY, changed))

    def test_only_a_full_same_source_same_sensors_combined_package_retires_old_ble(self):
        old, current = records()
        original = deepcopy([old, current])
        self.assertEqual(consolidated.omit_redundant_records([old, current]), [current])
        self.assertEqual([old, current], original)
        self.assertEqual(consolidated.omit_redundant_records([old]), [old])
        changes = [dict(verified=False), dict(platform="ESP32_PLATFORM"),
                   dict(build_profile="standard"), dict(source_commit="b" * 40),
                   dict(source_commit=None), dict(target=TARGET + "_custom"),
                   dict(capabilities=current["manifest"]["capabilities"] + ["hardware.qualification.pending"]),
                   dict(capabilities=current["manifest"]["capabilities"] + ["sensor.profile.reduced"])]
        for cap in (CAPABILITY, "companion.usb", "companion.bluetooth"):
            changes.append(dict(capabilities=[key for key in current["manifest"]["capabilities"] if key != cap]))
            for change in (dict(present=False), dict(source="config"), dict(evidence="")):
                changes.append(dict(verification=[{**row, **change} if row["capability"] == cap else row
                                                  for row in current["manifest"]["verification"]]))
        changes.append(dict(verification=[{**row, "evidence": MARKER + ".old"}
                                           if row["capability"] == CAPABILITY else row
                                           for row in current["manifest"]["verification"]]))
        for change in changes:
            wrong = {**current, "manifest": {**current["manifest"], **change}}
            with self.subTest(change=change):
                self.assertEqual(consolidated.omit_redundant_records([old, wrong]), [old, wrong])
        for source in (None, "A" * 40, "a" * 39, "b" * 40):
            wrong = {**old, "manifest": {**old["manifest"], "source_commit": source}}
            with self.subTest(old_source=source):
                self.assertEqual(consolidated.omit_redundant_records([wrong, current]), [wrong, current])


if __name__ == "__main__":
    unittest.main()
