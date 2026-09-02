#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "variants" / "heltec_t096" / "target.cpp"
PROFILE = ROOT / "variants" / "heltec_t096" / "platformio.ini"
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"
DISPLAY = ROOT / "src" / "helpers" / "ui" / "ST7735Display.cpp"
BUTTON = ROOT / "src" / "helpers" / "ui" / "MomentaryButton.cpp"
BUTTON_HEADER = ROOT / "src" / "helpers" / "ui" / "MomentaryButton.h"
MAIN = ROOT / "examples" / "companion_radio" / "main.cpp"
REPEATER_MAIN = ROOT / "examples" / "simple_repeater" / "main.cpp"
ROOM_UI = ROOT / "examples" / "simple_room_server" / "UITask.cpp"
ROOM_MAIN = ROOT / "examples" / "simple_room_server" / "main.cpp"
SENSOR_UI = ROOT / "examples" / "simple_sensor" / "UITask.cpp"
SENSOR_MAIN = ROOT / "examples" / "simple_sensor" / "main.cpp"
NRF52_BOARD = ROOT / "src" / "helpers" / "NRF52Board.cpp"


class T096ButtonWakeProfileTest(unittest.TestCase):
    def test_active_low_user_button_enables_internal_pullup(self):
        target = TARGET.read_text(encoding="utf-8")
        self.assertIn(
            "MomentaryButton user_btn(PIN_USER_BTN, 1000, true, true, true);",
            target,
        )

    def test_multiclick_remains_enabled_for_previous_page_gesture(self):
        target = TARGET.read_text(encoding="utf-8")
        self.assertIn(
            "MomentaryButton user_btn(PIN_USER_BTN, 1000, true, true, true);",
            target,
        )

    def test_nrf52_button_wakes_poller_on_press_and_release(self):
        button = BUTTON.read_text(encoding="utf-8")
        profile = PROFILE.read_text(encoding="utf-8")
        t096_base = profile.split("[Heltec_t096]", 1)[1].split("[env:", 1)[0]
        begin_start = button.index("void MomentaryButton::begin()")
        begin_end = button.index("\n}\n", begin_start)
        begin = button[begin_start:begin_end]

        self.assertIn("-D MOMENTARY_BUTTON_WAKE_FROM_SLEEP=1", t096_base)
        self.assertIn("defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)", button)
        self.assertIn("wakeMomentaryButtonPoller", button)
        self.assertIn(
            "attachInterrupt((uint32_t)_pin, wakeMomentaryButtonPoller, CHANGE)",
            begin,
        )

    def test_pending_gesture_deadlines_prevent_event_sleep(self):
        header = BUTTON_HEADER.read_text(encoding="utf-8")
        main = MAIN.read_text(encoding="utf-8")
        repeater_main = REPEATER_MAIN.read_text(encoding="utf-8")
        room_main = ROOM_MAIN.read_text(encoding="utf-8")
        sensor_main = SENSOR_MAIN.read_text(encoding="utf-8")

        self.assertIn(
            "return _debouncing || _press_active || _pending_click;", header
        )
        self.assertIn("defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)", main)
        self.assertIn("!user_btn.needsPolling()", main)
        self.assertIn("defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)", repeater_main)
        self.assertIn("!user_btn.needsPolling()", repeater_main)
        self.assertIn("defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)", room_main)
        self.assertIn("!user_btn.needsPolling()", room_main)
        self.assertIn("defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)", sensor_main)
        self.assertIn("!user_btn.needsPolling()", sensor_main)

    def test_room_and_sensor_use_event_driven_button_on_t096(self):
        room_ui = ROOM_UI.read_text(encoding="utf-8")
        sensor_ui = SENSOR_UI.read_text(encoding="utf-8")

        for source in (room_ui, sensor_ui):
            self.assertIn('#include "target.h"', source)
            self.assertIn("defined(MOMENTARY_BUTTON_WAKE_FROM_SLEEP)", source)
            self.assertIn("defined(DISPLAY_CLASS)", source)
            self.assertIn("user_btn.begin();", source)
            self.assertIn("user_btn.check();", source)
            self.assertIn("ev != BUTTON_EVENT_NONE", source)
            self.assertIn("digitalRead(PIN_USER_BTN)", source)

    def test_raw_nrf52_event_wait_preserves_pending_gpio_edge(self):
        board = NRF52_BOARD.read_text(encoding="utf-8")
        sleep = board[board.index("void NRF52Board::sleep"):]
        raw_wait = sleep.split("} else {", 1)[1]

        self.assertLess(raw_wait.index("__WFE();"), raw_wait.index("__SEV();"))
        self.assertIn("__WFE();\n    __SEV();\n    __WFE();", raw_wait)

    def test_switch_bounce_is_stabilized_before_counting_clicks(self):
        button = BUTTON.read_text(encoding="utf-8")
        header = BUTTON_HEADER.read_text(encoding="utf-8")

        self.assertIn("#define BUTTON_DEBOUNCE_MS", button)
        self.assertIn("raw_btn == prev", button)
        self.assertIn("now - _candidate_since", button)
        self.assertIn("press_candidate_in_window", button)
        self.assertIn("bool _debouncing;", header)
        self.assertIn("bool _press_active;", header)

    def test_release_only_polling_preserves_long_press(self):
        button = BUTTON.read_text(encoding="utf-8")
        release_start = button.index("// button UP")
        release_end = button.index("down_at = 0;", release_start)
        release = button[release_start:release_end]

        self.assertIn(">= (uint32_t)_long_millis", release)
        self.assertIn("event = BUTTON_EVENT_LONG_PRESS", release)

    def test_last_message_navigation_returns_home_instead_of_noop(self):
        ui = UI.read_text(encoding="utf-8")

        msg_input_start = ui.index("bool handleInput(char c) override", ui.index("class MsgPreviewScreen"))
        msg_input_end = ui.index("\n  }\n};", msg_input_start)
        msg_input = ui[msg_input_start:msg_input_end]

        self.assertIn("if (view_offset + 1 < filteredCount())", msg_input)
        self.assertIn("_task->gotoHomeScreen();", msg_input)

    def test_temporary_wake_diagnostics_are_not_shipped(self):
        ui = UI.read_text(encoding="utf-8")
        display = DISPLAY.read_text(encoding="utf-8")
        button = BUTTON.read_text(encoding="utf-8")

        self.assertNotIn("T096_BUTTON_WAKE_DIAGNOSTICS", ui)
        self.assertNotIn("T096_BUTTON_WAKE_DIAGNOSTICS", display)
        self.assertNotIn("T096_BUTTON_WAKE_DIAGNOSTICS", button)
        self.assertNotIn("T096_WAKE", ui)
        self.assertNotIn("T096_WAKE", display)
        self.assertNotIn("wake-bright", display)


if __name__ == "__main__":
    unittest.main()
