#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "sensecap_indicator-espnow" / "platformio.ini"
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"


class IndicatorMessagesProfileTest(unittest.TestCase):
    def test_indicator_enables_seventh_messages_home_page(self):
        profile = PROFILE.read_text()
        ui = UI.read_text()

        self.assertIn("-D UI_MESSAGES_HOME_PAGE=1", profile)
        self.assertIn("-D UI_COMPACT_MESSAGE_STATUS=1", profile)
        self.assertIn("#if UI_MESSAGES_HOME_PAGE == 1\n    MESSAGES,", ui)
        self.assertIn("_page == HomePage::MESSAGES", ui)

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
