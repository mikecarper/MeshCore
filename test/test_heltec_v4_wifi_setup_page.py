#!/usr/bin/env python3

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start


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
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run
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

    def test_compact_page_restores_qr_with_a_readable_text_fallback(self):
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
        self.assertIn("mesh::ui::drawWiFiSetupQr(", compact)
        self.assertIn('display, setup_ssid, setup_ip, true, "HOLD STOP"', compact)
        self.assertLess(compact.index("display.fillRect(0, 0,"),
                        compact.index("mesh::ui::drawWiFiSetupQr("))
        self.assertLess(compact.index("mesh::ui::drawWiFiSetupQr("),
                        compact.index('"SETUP AP ACTIVE"'))
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
        home = ui[ui.index("class HomeScreen :") :]
        handler = extract_braced(home, "  bool handleInput(char c) override")
        page_enum = extract_braced(home, "  enum HomePage") + ";"
        navigation = extract_braced(home, "  void movePage(int direction)")
        visible = extract_braced(home, "  bool isPageVisible(uint8_t page) const")
        main = MAIN.read_text()
        requests = "\n".join(extract_braced(main, name) for name in (
            "  void requestCompanionWiFiSetup()", "  void requestCompanionWiFiSetupStop()"))
        # Execute production signed-char navigation and HOLD setup handling.
        # Rendering/radio endpoints are mocked; preferences saves are absent,
        # so an accidental permanent toggle cannot compile in this fixture.
        source = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <helpers/WirelessControl.h>
#include <helpers/WiFiNetworkPowerPolicy.h>
#define UI_WIFI_SETUP_HOME_PAGE 1
#define UI_NO_DISCOVER_SCREEN 1
#define UI_NO_HIBERNATE 1
#define KEY_LEFT 0xB4
#define KEY_RIGHT 0xB7
#define KEY_PREV 0xF2
#define KEY_NEXT 0xF1
#define KEY_ENTER 13
bool companion_wifi_setup_requested = false, companion_wifi_setup_stop_requested = false;
@REQUESTS@
struct WebConfigServer {
  static bool active;
  static bool getSetupInfo(void*, int, void*, int) { return active; }
};
bool WebConfigServer::active = false;
struct Mesh { bool isDualRadioActive() const { return false; } bool advert() { return true; } } the_mesh;
enum class UIEventType { ack };
struct UITask {
  void showAlert(const char*, int) {}
  bool isBluetoothEnabled() { return false; }
  void disableBluetooth() {} void enableBluetooth() {}
  void showMessages() {} void notify(UIEventType) {}
};
struct UIScreen { virtual bool handleInput(char) { return false; } };
struct HomeScreen : UIScreen {
@PAGES@
  int _page = WIFI_SETUP, _radio_status_page = 0;
  UITask* _task;
  explicit HomeScreen(UITask* task) : _task(task) {}
  void resetRadioProfileDisplayPage() {}
@VISIBLE@
@NAVIGATION@
@HANDLER@
};
int main() {
  UITask task; HomeScreen home(&task);
  auto& network_power = mesh::wifi::networkPower();
  network_power.setManaged(true);
  network_power.setSuspended(true);
  assert(network_power.backgroundSuspended());
  assert(!network_power.consumeProbeRequest());
  assert(home.handleInput(static_cast<char>(KEY_NEXT)));
  assert(home._page == HomeScreen::FIRST);
  assert(!companion_wifi_setup_requested && !companion_wifi_setup_stop_requested);
  assert(!network_power.consumeProbeRequest());
  assert(home.handleInput(static_cast<char>(KEY_PREV)));
  assert(home._page == HomeScreen::WIFI_SETUP);
  assert(!network_power.consumeProbeRequest());
  assert(home.handleInput(KEY_ENTER));
  assert(companion_wifi_setup_requested && !companion_wifi_setup_stop_requested);
  assert(network_power.consumeProbeRequest());
  assert(!network_power.consumeProbeRequest());
  // The UI hands off a probe; only the network owner may resume transports.
  assert(network_power.backgroundSuspended());
  WebConfigServer::active = true;
  assert(home.handleInput(KEY_ENTER));
  assert(!companion_wifi_setup_requested && companion_wifi_setup_stop_requested);
  assert(!network_power.consumeProbeRequest());
  assert(home.handleInput(static_cast<char>(KEY_RIGHT)));
  assert(home._page == HomeScreen::FIRST);
  assert(!companion_wifi_setup_requested && companion_wifi_setup_stop_requested);
  assert(!network_power.consumeProbeRequest());
  assert(home.handleInput(static_cast<char>(KEY_LEFT)));
  assert(home._page == HomeScreen::WIFI_SETUP);
  assert(!network_power.consumeProbeRequest());
  requestCompanionWiFiSetupStop();
  assert(!network_power.consumeProbeRequest());
  struct Backend : mesh::wireless::Backend {
    uint8_t available() const override { return mesh::wireless::WiFi; }
    uint8_t enabled() const override { return mesh::wireless::WiFi; }
    uint8_t clients() const override { return 0; }
    mesh::wireless::Result set(uint8_t, bool) override { return mesh::wireless::Result::Done; }
  } backend;
  mesh::wireless::control().begin(backend);
  char reply[160] = {};
  mesh::wireless::control().handle("set wifi off force", reply, sizeof(reply), 0, mesh::wireless::Independent);
  mesh::wireless::control().service(250);
  requestCompanionWiFiSetup();
  assert(!companion_wifi_setup_requested && companion_wifi_setup_stop_requested);
  assert(!network_power.consumeProbeRequest());
  assert(network_power.backgroundSuspended());
}
'''
        source = (source.replace("@REQUESTS@", requests).replace("@PAGES@", page_enum)
                  .replace("@VISIBLE@", visible).replace("@NAVIGATION@", navigation)
                  .replace("@HANDLER@", handler))
        for char_mode in ("-fsigned-char", "-funsigned-char"):
            self.compile_and_run(source, char_mode)

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
        requests = "\n".join(extract_braced(main, signature) for signature in (
            "  void requestCompanionWiFiSetup()", "  void requestCompanionWiFiSetupStop()"))
        # Inspect the exact session request methods, not the unrelated permanent
        # master switch that is now defined between them and toggleCompanionWiFi.
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

    def test_reader_exit_wins_over_startup_rescue_but_home_keeps_rescue(self):
        ui = UI.read_text(encoding="utf-8")
        start = ui.index("char UITask::handleLongPress(char c) {")
        end = ui.index("\nchar UITask::handleDoubleClick", start)
        source = r'''
#include <cassert>
#define UI_WIFI_SETUP_HOME_PAGE 1
#include <cstdint>
unsigned long now = 4000;
unsigned long millis() { return now; }
struct Screen {};
struct HomeScreen : Screen {
  bool setup = false;
  bool isWiFiSetupPage() const { return setup; }
};
struct Mesh {
  int rescue_calls = 0;
  void enterCLIRescue() { ++rescue_calls; }
} the_mesh;
struct UITask {
  Screen* curr;
  Screen* home;
  Screen* msg_preview;
  unsigned long ui_started_at = 0;
  bool _startup_complete = false;
  char handleLongPress(char c);
};
''' + ui[start:end] + r'''
int main() {
  HomeScreen home;
  Screen reader;
  UITask task{&reader, &home, &reader};
  assert(task.handleLongPress('E') == 'E');
  assert(the_mesh.rescue_calls == 0);
  task.curr = &home;
  assert(task.handleLongPress('E') == 0);
  assert(the_mesh.rescue_calls == 1);
  home.setup = true;
  assert(task.handleLongPress('E') == 'E');
  assert(the_mesh.rescue_calls == 1);
  home.setup = false;
  now = 9000;
  assert(task.handleLongPress('E') == 'E');
  task.curr = &reader;
  assert(task.handleLongPress('E') == 'E');
  assert(the_mesh.rescue_calls == 1);
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "reader_hold"
            subprocess.run(
                ["c++", "-std=c++17", "-x", "c++", "-", "-o", str(executable)],
                input=source, text=True, capture_output=True, check=True,
            )
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
