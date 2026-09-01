#!/usr/bin/env python3

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build.sh"
MAIN = ROOT / "examples" / "companion_radio" / "main.cpp"
MESH = ROOT / "examples" / "companion_radio" / "MyMesh.cpp"
DATA_STORE = ROOT / "examples" / "companion_radio" / "DataStore.cpp"
PROFILE = ROOT / "variants" / "sensecap_indicator-espnow" / "platformio.ini"
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"
INDICATOR_DISPLAY = (
    ROOT / "variants" / "sensecap_indicator-espnow" / "SCIndicatorDisplay.h"
)
DISPLAY_DRIVER = ROOT / "src" / "helpers" / "ui" / "DisplayDriver.h"
LGFX_DISPLAY = ROOT / "src" / "helpers" / "ui" / "LGFXDisplay.h"
LGFX_DISPLAY_CPP = ROOT / "src" / "helpers" / "ui" / "LGFXDisplay.cpp"
WEBCONFIG_HEADER = ROOT / "src" / "helpers" / "esp32" / "WebConfigServer.h"
WEBCONFIG_SOURCE = ROOT / "src" / "helpers" / "esp32" / "WebConfigServer.cpp"
WIFI_QR_PAYLOAD = ROOT / "src" / "helpers" / "ui" / "WiFiSetupQrPayload.h"


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
  if (!mesh::ui::usesNativeIndicatorTypography(160, 160, 480, 480)) return 8;
  if (mesh::ui::usesNativeIndicatorTypography(160, 160, 320, 320)) return 9;
  if (mesh::ui::usesNativeIndicatorTypography(128, 128, 480, 480)) return 10;
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
        self.assertIn("-DUI_WIFI_SETUP_HOME_PAGE=1", build)

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

    def test_full_indicator_constructs_the_160_unit_viewport_explicitly(self):
        display = INDICATOR_DISPLAY.read_text(encoding="utf-8")
        self.assertIn("#if defined(INDICATOR_TRANSPORT_RENDER_PROFILE)", display)
        self.assertIn(
            "SCIndicatorDisplay() : LGFXDisplay(160, 160, 3, 1.0f, disp)",
            display,
        )

        lgfx = LGFX_DISPLAY.read_text(encoding="utf-8")
        self.assertIn(
            "LGFXDisplay(int logical_w, int logical_h, uint8_t coordinate_scale,",
            lgfx,
        )
        self.assertIn("DisplayDriver(logical_w, logical_h)", lgfx)
        self.assertIn("_coordinateScale(coordinate_scale)", lgfx)
        self.assertIn("_outputZoom(output_zoom)", lgfx)

    def test_native_font_increase_preserves_compact_chrome(self):
        display = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("selectIndicatorTextScale(", display)
        self.assertIn("static_cast<uint16_t>(renderWidth()), _compactText", display)
        self.assertIn("* profile_scale;", display)

    def test_indicator_setup_is_a_normal_eighth_home_page(self):
        ui = UI.read_text(encoding="utf-8")
        page_start = ui.index("static void drawCompanionWiFiSetupPage")
        page_end = ui.index("\n}\n#endif\n\n#include \"icons.h\"", page_start) + 2
        page = ui[page_start:page_end]
        self.assertIn("WebConfigServer::getSetupInfo", page)
        self.assertIn('20, "WIFI SETUP"', page)
        self.assertIn("37, setup_ssid", page)
        self.assertIn("static constexpr int qr_size = 105;", page)
        self.assertIn("payload, qr_x, 55, qr_size", page)
        self.assertIn("mesh::ui::buildWiFiSetupQrPayload", page)
        self.assertIn("display.setCompactText(true);", page)
        self.assertGreaterEqual(page.count("display.setCompactText(false);"), 2)
        self.assertNotIn("startFrame", page)
        self.assertNotIn("endFrame", page)

        enum_start = ui.index("  enum HomePage {")
        enum = ui[enum_start : ui.index("\n  };", enum_start)]
        self.assertIn(
            "#if UI_WIFI_SETUP_HOME_PAGE == 1\n    WIFI_SETUP,\n#endif\n    Count",
            enum,
        )
        self.assertIn("_page == HomePage::WIFI_SETUP", ui)
        self.assertIn("if (_page == HomePage::WIFI_SETUP) return 1000;", ui)
        self.assertIn("wifi_setup_active && !_wifi_setup_was_active", ui)

        main = MAIN.read_text(encoding="utf-8")
        loop = main[main.index("\nvoid loop()") :]
        display_dispatch = loop[loop.index("#ifdef DISPLAY_CLASS") :]
        display_dispatch = display_dispatch[: display_dispatch.index("rtc_clock.tick();")]
        self.assertIn("&& UI_WIFI_SETUP_HOME_PAGE != 1", display_dispatch)
        self.assertIn("renderCompanionSetupDisplay();", display_dispatch)
        self.assertIn("#else\n  ui_task.loop();", display_dispatch)

    def test_wifi_qr_payload_is_standard_and_escaped(self):
        setup = WIFI_QR_PAYLOAD.read_text(encoding="utf-8")
        self.assertIn("buildWiFiSetupQrPayload", setup)
        self.assertIn('"WIFI:T:WPA;S:"', setup)
        self.assertIn('"WIFI:T:nopass;S:"', setup)
        self.assertIn('strchr("\\\\;,\\\":", *value)', setup)
        self.assertIn('";P:"', setup)
        self.assertIn('";;"', setup)

    def test_legacy_setup_overlay_is_not_built_for_page_enabled_profile(self):
        main = MAIN.read_text(encoding="utf-8")
        start = main.index("static DisplayDriver* companion_setup_display")
        declaration = main[start - 120 : start + 80]
        self.assertIn("&& UI_WIFI_SETUP_HOME_PAGE != 1", declaration)
        self.assertNotIn("renderIndicatorSetupDisplay", main)
        self.assertNotIn("renderIndicatorSetupQrDisplay", main)

    def test_lgfx_qr_renderer_scales_coordinates_and_keeps_quiet_zone(self):
        driver = DISPLAY_DRIVER.read_text(encoding="utf-8")
        self.assertIn(
            "virtual bool drawQrCode(const char* text, int x, int y, int size)",
            driver,
        )

        header = LGFX_DISPLAY.read_text(encoding="utf-8")
        self.assertIn(
            "bool drawQrCode(const char* text, int x, int y, int size) override;",
            header,
        )
        implementation = LGFX_DISPLAY_CPP.read_text(encoding="utf-8")
        qr = implementation[implementation.index("bool LGFXDisplay::drawQrCode") :]
        qr = qr[: qr.index("\nuint16_t LGFXDisplay::getTextWidth")]
        self.assertIn("lgfx_qrcode_initText", qr)
        self.assertIn("lgfx_qrcode_getModule", qr)
        self.assertIn("ECC_LOW", qr)
        self.assertIn("static constexpr int quiet_zone = 4;", qr)
        self.assertIn("renderColor(UIColor::primary_txt)", qr)
        self.assertIn("renderColor(UIColor::window_bkg)", qr)
        self.assertIn("const int scaled_size = size * _coordinateScale;", qr)
        self.assertIn("x > width() - size", qr)
        self.assertIn("module_size, module_size, dark", qr)
        self.assertNotIn("run_start", qr)
        self.assertNotIn("buffer.qrcode", qr)
        self.assertNotIn("TFT_WHITE", qr)
        self.assertNotIn("TFT_BLACK", qr)

    def test_indicator_uses_short_captive_setup_ssid(self):
        build = BUILD.read_text(encoding="utf-8")
        short_prefix_flag = "-DWEBCONFIG_AP_PREFIX='\\\"MC-Set\\\"'"
        self.assertEqual(build.count(short_prefix_flag), 1)
        full_case_start = build.index(
            "# Both Indicator Full layouts select exactly one secondary"
        )
        full_case = build[full_case_start : build.index(";;", full_case_start)]
        self.assertIn(short_prefix_flag, full_case)

        profile = PROFILE.read_text(encoding="utf-8")
        self.assertNotIn("WEBCONFIG_AP_PREFIX", profile)

        header = WEBCONFIG_HEADER.read_text(encoding="utf-8")
        self.assertIn('#define WEBCONFIG_AP_PREFIX "MeshCore-Setup"', header)
        source = WEBCONFIG_SOURCE.read_text(encoding="utf-8")
        self.assertIn('"%s-%02X%02X"', source)
        self.assertIn("WEBCONFIG_AP_PREFIX, _pub_key[0], _pub_key[1]", source)
        self.assertIn("sizeof(WEBCONFIG_AP_PREFIX) <= 28", source)

    def test_lora_fresh_install_uses_compiled_cascade_defaults(self):
        profile = PROFILE.read_text(encoding="utf-8")
        lora = profile[profile.index("[SenseCapIndicator-LoRa]") :]
        lora = lora[: lora.index("[env:")]
        self.assertIn("-UMESH_PRIMARY_ESPNOW", lora)
        self.assertIn("-D SENSECAP_INDICATOR_LORA", lora)
        self.assertIn("-D USE_SX1262", lora)
        self.assertIn("-<helpers/esp32/ESPNOWRadio.cpp>", lora)

        mesh = MESH.read_text(encoding="utf-8")
        self.assertIn("_prefs.freq = LORA_FREQ;", mesh)
        self.assertIn("_prefs.sf = LORA_SF;", mesh)
        self.assertIn("_prefs.bw = LORA_BW;", mesh)
        self.assertIn("_prefs.cr = LORA_CR;", mesh)
        guarded_rx_delay = (
            "#ifdef DEFAULT_RX_DELAY_BASE\n"
            "  _prefs.rx_delay_base = DEFAULT_RX_DELAY_BASE;\n"
            "#endif"
        )
        self.assertIn(guarded_rx_delay, mesh)

        # Saved preferences load after constructor defaults, so an app-only
        # update remains non-destructive while an erased install uses them.
        begin = mesh[mesh.index("void MyMesh::begin(") :]
        self.assertIn("_store->loadPrefs(_prefs", begin)
        store = DATA_STORE.read_text(encoding="utf-8")
        self.assertIn('if (_fs->exists("/new_prefs"))', store)
        self.assertIn('loadPrefsInt("/new_prefs", prefs', store)

    def test_explicit_usa_cascadia_and_cascade_build_flags(self):
        command = r'''
set -e
source "$1"
PLATFORMIO_BUILD_FLAGS=""
RADIO_FREQ_OVERRIDE=910.525
RADIO_BW_OVERRIDE=62.5
RADIO_SF_OVERRIDE=7
RADIO_CR_OVERRIDE=5
FIRMWARE_PROFILE_OVERRIDE=cascade
apply_radio_overrides
apply_firmware_profile_overrides
printf '%s\n' "$PLATFORMIO_BUILD_FLAGS"
'''
        result = subprocess.run(
            ["bash", "-c", command, "test", str(BUILD)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        flags = result.stdout.split()
        for expected in (
            "-DLORA_FREQ=910.525",
            "-DLORA_BW=62.5",
            "-DLORA_SF=7",
            "-DLORA_CR=5",
            "-DCASCADE_PROFILE=1",
            "-DDEFAULT_RX_DELAY_BASE=2.0f",
        ):
            self.assertIn(expected, flags)

        build = BUILD.read_text(encoding="utf-8")
        self.assertIn(
            "'SenseCapIndicator-LoRa_comp_radio_usb_wifi|"
            "SenseCapIndicator-LoRa_companion_radio_full'",
            build,
        )
        self.assertIn(
            "'SenseCapIndicator-LoRa-N16R2_comp_radio_usb_wifi|"
            "SenseCapIndicator-LoRa-N16R2_companion_radio_full'",
            build,
        )


if __name__ == "__main__":
    unittest.main()
