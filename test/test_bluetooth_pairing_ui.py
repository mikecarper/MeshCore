#!/usr/bin/env python3

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "src" / "helpers" / "ui" / "BluetoothPairingUiPolicy.h"
UI_VARIANTS = (
    ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp",
    ROOT / "examples" / "companion_radio" / "ui-orig" / "UITask.cpp",
    ROOT / "examples" / "companion_radio" / "ui-tiny" / "UITask.cpp",
)


class BluetoothPairingUiTest(unittest.TestCase):
    def test_policy_requires_enabled_disconnected_bluetooth(self):
        translation_unit = r'''
#include <helpers/ui/BluetoothPairingUiPolicy.h>

static_assert(mesh::ui::shouldDisplayBluetoothPairingPin(true, false, 123456),
              "enabled, disconnected BLE should show a nonzero PIN");
static_assert(!mesh::ui::shouldDisplayBluetoothPairingPin(false, false, 123456),
              "disabled BLE must hide its PIN");
static_assert(!mesh::ui::shouldDisplayBluetoothPairingPin(true, true, 123456),
              "connected BLE no longer needs its PIN");
static_assert(!mesh::ui::shouldDisplayBluetoothPairingPin(true, false, 0),
              "zero is the no-PIN sentinel");

static_assert(mesh::ui::isBluetoothPairingPromptActive(
                  true, false, 200, 100),
              "an enabled pending prompt should remain active");
static_assert(!mesh::ui::isBluetoothPairingPromptActive(
                  false, false, 200, 100),
              "disabling BLE must dismiss a pending prompt");
static_assert(!mesh::ui::isBluetoothPairingPromptActive(
                  true, true, 200, 100),
              "connecting BLE must dismiss a pending prompt");
static_assert(!mesh::ui::isBluetoothPairingPromptActive(
                  true, false, 200, 200),
              "an expired prompt is inactive");
static_assert(mesh::ui::isBluetoothPairingPromptActive(
                  true, false, 0x10u, 0xfffffff0u),
              "the deadline comparison must survive millis rollover");
'''
        result = subprocess.run(
            [
                "c++",
                "-std=c++11",
                f"-I{ROOT / 'src'}",
                "-x",
                "c++",
                "-fsyntax-only",
                "-",
            ],
            input=translation_unit,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_companion_display_uses_shared_visibility_policy(self):
        self.assertTrue(POLICY.is_file())
        for ui_path in UI_VARIANTS:
            with self.subTest(ui=ui_path.parent.name):
                source = ui_path.read_text(encoding="utf-8")
                self.assertIn(
                    "#include <helpers/ui/BluetoothPairingUiPolicy.h>", source
                )
                self.assertIn("shouldDisplayBluetoothPairingPin", source)
                self.assertIn("isBluetoothPairingPromptActive", source)
                self.assertIn("if (!isBluetoothEnabled()) return;", source)
                self.assertIn(
                    "if (!isPairingScreenActive()) {\n"
                    "      finishPairingScreen(timed_out);",
                    source,
                )

        full_ui = UI_VARIANTS[0].read_text(encoding="utf-8")
        self.assertNotIn("else if (bluetooth_pin != 0)", full_ui)
        self.assertIn("else if (show_bluetooth_pin) { // BT pin", full_ui)
        self.assertIn("_pairing_screen_until = 0;", full_ui)


if __name__ == "__main__":
    unittest.main()
