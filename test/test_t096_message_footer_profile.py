#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants" / "heltec_t096" / "platformio.ini"
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"


class T096MessageFooterProfileTest(unittest.TestCase):
    def test_t096_disables_noninteractive_message_channel_footer(self):
        profile = PROFILE.read_text(encoding="utf-8")
        base_profile = profile.split("[Heltec_t096]", 1)[1].split(
            "[env:", 1
        )[0]
        build_flags = base_profile.split("build_flags =", 1)[1].split(
            "build_src_filter =", 1
        )[0]
        active_flags = {
            line.strip()
            for line in build_flags.splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        }
        self.assertIn("-D UI_MESSAGE_CHANNEL_FOOTER=0", active_flags)

    def test_other_ui_new_targets_keep_the_footer_by_default(self):
        ui = UI.read_text(encoding="utf-8")
        self.assertIn(
            "#ifndef UI_MESSAGE_CHANNEL_FOOTER\n"
            "  #define UI_MESSAGE_CHANNEL_FOOTER 1\n"
            "#endif",
            ui,
        )
        footer_start = ui.index(
            "  void renderChannelFilter(DisplayDriver& display) const {"
        )
        footer_end = ui.index("\n  }", footer_start)
        footer = ui[footer_start:footer_end]
        enabled, disabled = footer.split("#else", 1)
        disabled = disabled.split("#endif", 1)[0]
        self.assertIn("#if UI_MESSAGE_CHANNEL_FOOTER == 1", enabled)
        self.assertIn("display.fillRect", enabled)
        self.assertIn("display.drawTextCentered", enabled)
        self.assertNotIn("display.", disabled)
        self.assertIn("(void)display;", disabled)


if __name__ == "__main__":
    unittest.main()
