#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "heltec_v4" / "platformio.ini"
R8_PROFILE = ROOT / "variants" / "heltec_v4_r8" / "platformio.ini"
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"
WIFI = ROOT / "examples" / "companion_radio" / "CompanionWiFi.h"
MAIN = ROOT / "examples" / "companion_radio" / "main.cpp"


def ini_section(source: str, name: str) -> str:
    start = source.index(f"[env:{name}]")
    end = source.find("\n[", start + 1)
    return source[start:] if end < 0 else source[start:end]


class HeltecV4WiFiSetupPageTest(unittest.TestCase):
    def test_only_main_v4_oled_full_profiles_enable_the_page(self):
        profile = PROFILE.read_text(encoding="utf-8")
        full_profiles = (
            "heltec_v4_2_v4_3_companion_radio_full_femon",
            "heltec_v4_3_companion_radio_full_femoff",
        )
        for name in full_profiles:
            section = ini_section(profile, name)
            self.assertIn("-D UI_WIFI_SETUP_HOME_PAGE=1", section)
            self.assertIn("-D MESHCORE_REQUIRES_COMPANION_RADIO_FULL=1", section)

        self.assertEqual(profile.count("-D UI_WIFI_SETUP_HOME_PAGE=1"), 2)
        self.assertNotIn(
            "UI_WIFI_SETUP_HOME_PAGE",
            ini_section(profile, "heltec_v4_companion_radio_wifi_femon"),
        )
        self.assertNotIn(
            "UI_WIFI_SETUP_HOME_PAGE",
            ini_section(profile, "heltec_v4_tft_companion_radio_full_femon"),
        )
        self.assertNotIn("UI_WIFI_SETUP_HOME_PAGE", R8_PROFILE.read_text())

    def test_compact_page_fits_128_by_64_without_qr_or_cli_copy(self):
        ui = UI.read_text(encoding="utf-8")
        start = ui.index("static bool drawCompactCompanionWiFiSetupPage")
        end = ui.index("\nstatic void drawCompanionWiFiSetupPage", start)
        compact = ui[start:end]

        self.assertIn("display.width() > 128 || display.height() > 64", compact)
        self.assertIn("display.setTextSize(1);", compact)
        self.assertIn('"SETUP AP ACTIVE" : "SETUP AP INACTIVE"', compact)
        self.assertIn('"HOLD STOP AP" : "HOLD START AP"', compact)
        self.assertIn('snprintf(open_ip, sizeof(open_ip), "OPEN %s", setup_ip)', compact)
        self.assertIn("display.drawTextEllipsized", compact)
        self.assertNotIn("drawQrCode", compact)
        self.assertNotIn("buildWiFiSetupQrPayload", compact)
        self.assertNotIn("start webconfig", compact.lower())

        # Default size-one glyphs are six by eight logical pixels.
        for text in (
            "SETUP AP INACTIVE",
            "WIFI NOT CONFIGURED",
            "OPEN 255.255.255.255",
            "HOLD START AP",
        ):
            self.assertLessEqual(len(text) * 6, 128)
        for y in (20, 31, 42, 53):
            self.assertLessEqual(y + 8, 64)

        page_start = ui.index("static void drawCompanionWiFiSetupPage")
        page_end = ui.index("\n}\n#endif\n\n#include \"icons.h\"", page_start)
        page = ui[page_start:page_end]
        self.assertLess(
            page.index("drawCompactCompanionWiFiSetupPage("),
            page.index("buildWiFiSetupQrPayload("),
        )

    def test_short_click_leaves_page_and_hold_toggles_session_ap(self):
        ui = UI.read_text(encoding="utf-8")
        home_handler_start = ui.index("  bool handleInput(char c) override {")
        home_handler_end = ui.index("\n};", home_handler_start)
        handler = ui[home_handler_start:home_handler_end]

        next_page = handler.index("if (c == KEY_NEXT || c == KEY_RIGHT)")
        setup_action = handler.index(
            "if (c == KEY_ENTER && _page == HomePage::WIFI_SETUP)"
        )
        self.assertLess(next_page, setup_action)
        self.assertIn("_page = (_page + 1) % HomePage::Count;", handler)

        setup_handler = handler[setup_action:]
        self.assertIn("WebConfigServer::getSetupInfo(nullptr, 0, nullptr, 0)", setup_handler)
        self.assertIn("requestCompanionWiFiSetupStop();", setup_handler)
        self.assertIn("requestCompanionWiFiSetup();", setup_handler)
        self.assertNotIn("!isCompanionWiFiConnected()", setup_handler)

        loop = ui[ui.index("void UITask::loop()") : ui.index(
            "char UITask::checkDisplayOn", ui.index("void UITask::loop()")
        )]
        self.assertIn(
            "if (ev == BUTTON_EVENT_CLICK) {\n"
            "    c = checkDisplayOn(KEY_NEXT);",
            loop,
        )
        self.assertIn(": handleLongPress(KEY_ENTER);", loop)

        long_press = ui[ui.index("char UITask::handleLongPress") :]
        long_press = long_press[:long_press.index("\nchar UITask::handleDoubleClick")]
        page_bypass = long_press.index("isWiFiSetupPage()")
        cli_rescue = long_press.index("the_mesh.enterCLIRescue()")
        self.assertLess(page_bypass, cli_rescue)
        self.assertIn("return c;", long_press[page_bypass:cli_rescue])

    def test_deferred_toggle_does_not_change_saved_preferences(self):
        wifi = WIFI.read_text(encoding="utf-8")
        self.assertIn("void requestCompanionWiFiSetupStop();", wifi)
        self.assertIn("current boot session", wifi)

        main = MAIN.read_text(encoding="utf-8")
        requests_start = main.index("void requestCompanionWiFiSetup()")
        requests_end = main.index("bool toggleCompanionWiFi()", requests_start)
        requests = main[requests_start:requests_end]
        self.assertIn("companion_wifi_setup_requested = true;", requests)
        self.assertIn("companion_wifi_setup_stop_requested = true;", requests)
        self.assertNotIn("savePrefs", requests)
        self.assertNotIn("saveEnabled", requests)

        loop = main[main.index("\nvoid loop()") :]
        stop = loop.index("if (companion_wifi_setup_stop_requested)")
        start = loop.index("if (companion_wifi_setup_requested)", stop)
        state_service = loop.index("serviceCompanionWiFiState();", start)
        self.assertLess(stop, start)
        self.assertLess(start, state_service)
        self.assertIn("the_mesh.stopWebConfig();", loop[stop:start])
        self.assertIn("the_mesh.startWebConfig(true, web_reply)", loop[start:state_service])
        self.assertNotIn("savePrefs", loop[stop:state_service])
        self.assertNotIn("saveEnabled", loop[stop:state_service])


if __name__ == "__main__":
    unittest.main()
