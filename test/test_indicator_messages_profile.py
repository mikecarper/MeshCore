#!/usr/bin/env python3

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "sensecap_indicator-espnow" / "platformio.ini"
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"


class IndicatorMessagesProfileTest(unittest.TestCase):
    def test_indicator_enables_messages_home_page(self):
        profile = PROFILE.read_text()
        ui = UI.read_text()

        self.assertIn("-D UI_MESSAGES_HOME_PAGE=1", profile)
        self.assertIn("-D UI_COMPACT_MESSAGE_STATUS=1", profile)
        self.assertIn("#if UI_MESSAGES_HOME_PAGE == 1\n    MESSAGES,", ui)
        self.assertIn("_page == HomePage::MESSAGES", ui)

    def test_wifi_setup_is_eighth_without_reordering_existing_pages(self):
        ui = UI.read_text(encoding="utf-8")
        enum_start = ui.index("  enum HomePage {")
        enum_end = ui.index("\n  };", enum_start) + len("\n  };")
        enum_source = ui[enum_start:enum_end].replace(
            "  enum HomePage", "enum HomePage"
        )
        source = f"""
#define UI_MESSAGES_HOME_PAGE 1
#define COMPANION_EXCLUSIVE_WIFI_BLE 1
#define ENV_INCLUDE_GPS 0
#define UI_SENSORS_PAGE 1
#define UI_NO_HIBERNATE 1
#define UI_WIFI_SETUP_HOME_PAGE 1
{enum_source}
static_assert(FIRST == 0, "first");
static_assert(MESSAGES == 1, "messages");
static_assert(RECENT == 2, "recent");
static_assert(RADIO == 3, "radio");
static_assert(TRANSPORT == 4, "transport");
static_assert(ADVERT == 5, "advert");
static_assert(SENSORS == 6, "sensors");
static_assert(WIFI_SETUP == 7, "wifi setup");
static_assert(Count == 8, "count");
int main() {{ return 0; }}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "home_pages"
            compiled = subprocess.run(
                ["c++", "-std=c++11", "-x", "c++", "-", "-o", str(executable)],
                input=source,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)

    def test_full_message_chrome_uses_compact_visuals_only(self):
        ui = UI.read_text(encoding="utf-8")
        touch = (ROOT / "src/helpers/ui/TouchInput.h").read_text(
            encoding="utf-8"
        )
        self.assertIn("makeCompanionMessageChromeLayout", ui)
        self.assertIn("display.setCompactText(layout.compact_text)", ui)
        self.assertIn("display.setCompactText(false)", ui)
        self.assertIn("_start_y >= (height * 3) / 4", touch)
        self.assertIn("_task->renderMessageSummary(display);", ui)

    def test_history_is_not_cleared_when_companion_drains_messages(self):
        ui = UI.read_text()

        self.assertNotIn("MsgPreviewScreen*>(msg_preview)->clear()", ui)
        self.assertIn(
            "companionMessageElapsedMillis(entry->heard_millis)", ui
        )
        self.assertIn("esp_timer_get_time()", ui)
        self.assertIn("history.hasNewerEntryForThread(age)", ui)


if __name__ == "__main__":
    unittest.main()
