#!/usr/bin/env python3

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build.sh"
MAIN = ROOT / "examples" / "companion_radio" / "main.cpp"


class IndicatorRenderProfileTest(unittest.TestCase):
    def test_transport_matrix(self):
        source = r'''
#include <helpers/ui/IndicatorRenderProfile.h>

int main() {
  using mesh::ui::selectIndicatorRenderProfile;
  const auto lora_wifi = selectIndicatorRenderProfile(false, true);
  const auto lora_ble = selectIndicatorRenderProfile(false, false);
  const auto espnow_wifi = selectIndicatorRenderProfile(true, true);
  const auto espnow_ble = selectIndicatorRenderProfile(true, false);
  if (lora_wifi.canvas_size != 480 || lora_wifi.coordinate_scale != 3
      || lora_wifi.output_zoom != 1.0f) return 1;
  if (lora_ble.canvas_size != 480 || lora_ble.coordinate_scale != 3
      || lora_ble.output_zoom != 1.0f) return 2;
  if (espnow_wifi.canvas_size != 480 || espnow_wifi.coordinate_scale != 3
      || espnow_wifi.output_zoom != 1.0f) return 3;
  if (espnow_ble.canvas_size != 320 || espnow_ble.coordinate_scale != 2
      || espnow_ble.output_zoom != 1.5f) return 4;
  if (mesh::ui::selectIndicatorTextScale(480, false) != 1.2f) return 5;
  if (mesh::ui::selectIndicatorTextScale(480, true) != 1.0f) return 6;
  if (mesh::ui::selectIndicatorTextScale(320, false) != 1.0f) return 7;
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "indicator_render_profile"
            compiled = subprocess.run(
                [
                    "c++",
                    "-std=c++11",
                    f"-I{ROOT / 'src'}",
                    "-x",
                    "c++",
                    "-",
                    "-o",
                    str(executable),
                ],
                input=source,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_full_profiles_allocate_native_before_runtime_selection(self):
        build = BUILD.read_text(encoding="utf-8")
        self.assertIn(
            "sensecapindicator-espnow_companion_radio_full|\\\n"
            "    sensecapindicator-lora_companion_radio_full)",
            build,
        )
        self.assertIn("-UUI_ZOOM -DUI_ZOOM=1.0f", build)
        self.assertIn("-UUI_COORD_SCALE -DUI_COORD_SCALE=3", build)
        self.assertIn("-DINDICATOR_TRANSPORT_RENDER_PROFILE=1", build)

    def test_runtime_selection_precedes_bluetooth_start(self):
        main = MAIN.read_text(encoding="utf-8")
        setup = main[main.index("void setup()") : main.index("\nvoid loop()")]
        select = setup.index("selectIndicatorRenderProfile(")
        resize = setup.index("disp->setRenderScale(", select)
        bluetooth = setup.index("startCompanionBluetooth();", resize)
        self.assertLess(select, resize)
        self.assertLess(resize, bluetooth)
        self.assertIn("true,", setup[select:resize])
        self.assertIn("companionTransportWiFiActiveAtBoot()", setup[select:resize])

    def test_canvas_stays_internal_and_can_rollback(self):
        display = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("buffer.setPsram(false);", display)
        self.assertIn("if (!configurePalette()) {\n    buffer.deleteSprite();", display)
        self.assertIn("buffer.deleteSprite();", display)
        self.assertIn("_coordinateScale = previous_scale;", display)
        self.assertIn("_outputZoom = previous_zoom;", display)
        self.assertIn("createRenderBuffer();\n  return false;", display)

    def test_failed_native_startup_retries_safe_canvas(self):
        display = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text(
            encoding="utf-8"
        )
        begin = display[display.index("bool LGFXDisplay::begin()") :]
        begin = begin[: begin.index("\nbool LGFXDisplay::createRenderBuffer()")]
        first_attempt = begin.index("if (createRenderBuffer()) return true;")
        guard = begin.index("INDICATOR_TRANSPORT_RENDER_PROFILE", first_attempt)
        fallback_scale = begin.index("_coordinateScale = 2;", guard)
        fallback_zoom = begin.index("_outputZoom = 1.5f;", fallback_scale)
        retry = begin.index("if (createRenderBuffer()) return true;", fallback_zoom)
        self.assertLess(first_attempt, guard)
        self.assertLess(guard, fallback_scale)
        self.assertLess(fallback_scale, fallback_zoom)
        self.assertLess(fallback_zoom, retry)

    def test_native_font_increase_preserves_compact_chrome(self):
        display = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("selectIndicatorTextScale(", display)
        self.assertIn("static_cast<uint16_t>(renderWidth()), _compactText", display)
        self.assertIn("* profile_scale;", display)


if __name__ == "__main__":
    unittest.main()
