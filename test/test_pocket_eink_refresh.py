#!/usr/bin/env python3
"""Exercise the production Pocket e-paper driver against a recording panel."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/helpers/ui"
FIXTURE = ROOT / "test/fixtures/pocket_eink_refresh"


class PocketEinkRefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "g++"))
        if compiler is None:
            raise unittest.SkipTest("host G++ required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="pocket-eink-refresh-")
        cls.work = Path(cls.temporary.name)
        # Copy the actual driver so its quoted hardware includes resolve to the
        # fixture. The DisplayDriver base and Pocket feature configuration stay
        # production code, while the panel records calls crossing the HW API.
        for name in ("GxEPDDisplay.h", "GxEPDDisplay.cpp"):
            shutil.copyfile(UI / name, cls.work / name)
        cls.binary = cls.work / "refresh-test"
        flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "-g",
                 "-Wno-unused-parameter", "-Wno-sign-compare"]
        if os.name != "nt":
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        command = [compiler, *flags, "-DHELTEC_MESH_POCKET=1", "-DCOMPANION_RADIO_FULL=1",
                   "-DEINK_DISPLAY_MODEL=GxEPD2_213_B74", "-DDISPLAY_ROTATION=3",
                   "-I" + str(cls.work), "-I" + str(FIXTURE), "-I" + str(UI),
                   "-I" + str(ROOT / "src"), str(FIXTURE / "main.cpp"),
                   str(cls.work / "GxEPDDisplay.cpp"), "-o", str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if result.returncode:
            cls.temporary.cleanup()
            raise AssertionError(" ".join(command) + "\n" + result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_backlight_models_hibernate_and_clear_retained_images(self):
        source = self.work / "retain.cpp"
        source.write_text(r"""
#include "GxEPDDisplay.h"
int main() {
  GxEPDDisplay display;
  auto& prefs = mesh::ui::displayPowerPrefs();
  prefs.wake_on_boot = true;
  prefs.battery.seconds = 1;
  test_ms = UINT32_MAX - 400;
  assert(display.begin());
  display.servicePower(false);
  display.retainFrameAtTimeout(true);
  test_ms += 999;
  assert(!display.servicePower(false) && display.isOn());
  ++test_ms;
  assert(display.servicePower(false) && !display.isOn() && display.hasRetainedImage());
  assert(panel::updates(panel::Call::Hibernate) == 1 && panel::updates(panel::Call::Clear) == 0);
  prefs.battery.mode = mesh::ui::DisplayMode::Off;
  display.servicePower(false);
  assert(!display.hasRetainedImage() && panel::updates(panel::Call::Clear) == 1);
  display.servicePower(false);
  assert(panel::updates(panel::Call::Clear) == 1);
  prefs.battery.mode = mesh::ui::DisplayMode::Button;
  display.servicePower(false);
  assert(display.wake(mesh::ui::DisplayWake::Button));
  display.retainFrameAtTimeout(false);
  test_ms += 1000;
  assert(display.servicePower(false) && !display.hasRetainedImage());
  assert(panel::updates(panel::Call::Clear) == 2);
}
""")
        binary = self.work / "retain"
        subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                        "-DDISPLAY_CLASS=GxEPDDisplay", "-DBACKLIGHT_BTN=11",
                        "-DEINK_DISPLAY_MODEL=GxEPD2_213_B74", "-I" + str(self.work),
                        "-I" + str(FIXTURE), "-I" + str(UI), "-I" + str(ROOT / "src"),
                        str(source), str(self.work / "GxEPDDisplay.cpp"),
                        str(UI / "DisplayDriver.cpp"), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)

    def run_case(self, scenario):
        result = subprocess.run([str(self.binary), scenario], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manual_clean_affects_current_frame_even_when_crc_is_unchanged(self):
        self.run_case("manual")

    def test_first_image_is_clean_even_when_gui_applies_its_refresh_settings(self):
        self.run_case("boot")

    def test_unchanged_frames_skip_io_and_do_not_use_partial_refresh_budget(self):
        self.run_case("periodic")

    def test_changing_clean_interval_preserves_a_pending_full_refresh(self):
        self.run_case("interval")

    def test_waking_from_hibernate_restores_panel_history_before_partial_updates(self):
        self.run_case("wake")

    def test_rotation_cleans_the_current_frame_and_keeps_native_dimensions(self):
        self.run_case("rotation")


if __name__ == "__main__":
    unittest.main()
