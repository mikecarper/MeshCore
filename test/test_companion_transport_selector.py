#!/usr/bin/env python3

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_reader_touch_coordinates import PREAMBLE
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "examples" / "companion_radio" / "ui-new" / "UITask.cpp"


class CompanionTransportSelectorTest(unittest.TestCase):
    def test_centered_choices_fit_and_preserve_full_height_navigation(self):
        actual = extract_braced(UI.read_text(), "static void drawCompanionTransportChoice(")
        generic = extract_braced(
            (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text(),
            "bool LGFXDisplay::getTouch(")
        scenarios = r'''
#include <helpers/ui/CompanionTransportSelectorLayout.h>
using namespace mesh::ui;
struct MeasuredDisplay : LGFXDisplay {
  int size=1,bx=0,by=0,bw=160,bh=160,last_bottom=0;
  void setTextSize(int s) override { size=s; }
  uint16_t getTextWidth(const char* text) override { return strlen(text)*6*size; }
  void print(const char* text) override {
    assert(cx>=bx+2 && cx+getTextWidth(text)<=bx+bw-2);
    assert(cy>=by && cy+8*size<=by+bh);
    assert(cy>=last_bottom);
    last_bottom=cy+8*size;
    LGFXDisplay::print(text);
  }
};
int main() {
  const auto layout=makeCompanionTransportSelectorLayout(160,160,70);
  assert(layout.side_nav_width==24);
  assert(layout.wifi.x==26 && layout.wifi.width==52);
  assert(layout.bluetooth.x==82 && layout.bluetooth.width==52);
  const TouchSplitSelector selector={layout.wifi.x,layout.wifi.width,
      layout.bluetooth.x,layout.bluetooth.width,layout.wifi.y,
      layout.wifi.height,layout.side_nav_width};
  for(bool mirror : {false,true}) for(int y=0;y<160;++y) for(int x=0;x<160;++x) {
    TouchAction expected=TouchAction::None;
    if(x<24) expected=TouchAction::Previous;
    else if(x>=136) expected=TouchAction::Next;
    else if(y>=40 && y<140) {
      if(x>=26 && x<78) expected=TouchAction::SelectLeft;
      if(x>=82 && x<134) expected=TouchAction::SelectRight;
    }
    TouchInput input(true,true,70,mirror,false);
    const int tx=mirror ? 159-x : x;
    input.update(true,tx,y,160,160,false,&selector);
    input.update(false,-1,-1,160,160,false,&selector);
    assert(input.update(false,-1,-1,160,160,false,&selector)==expected);
  }
  for(int canvas : {320,480}) for(bool wifi : {false,true})
      for(bool active : {false,true}) for(bool selected : {false,true}) {
    MeasuredDisplay d;
    d._coordinateScale=canvas/160; d._outputZoom=480.0f/canvas;
    const auto& box=wifi ? layout.wifi : layout.bluetooth;
    d.bx=box.x; d.by=box.y; d.bw=box.width; d.bh=box.height;
    drawCompanionTransportChoice(d,box.x,box.y,box.width,box.height,
        wifi ? "WiFi" : "BLE",active,selected);
    assert(d.labels.size()==(active || selected ? 2 : 1));
  }
  assert(4*6*3<=160-2*layout.side_nav_width); // MODE
  assert(9*6*2<=160-2*layout.side_nav_width); // TAP A BOX
}
'''
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "centered_transport"
            compiled = subprocess.run(["c++", "-std=c++11", "-O1",
                "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                f"-I{ROOT / 'src'}", "-x", "c++", "-", "-o", str(binary)],
                input=PREAMBLE + generic + actual + scenarios,
                text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            tested = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_indicator_layout_has_room_for_large_transport_text(self):
        source = r'''
#include <helpers/ui/CompanionTransportSelectorLayout.h>

int main() {
  const auto layout = mesh::ui::makeCompanionTransportSelectorLayout(160, 160);
  if (!layout.show_title) return 1;
  if (layout.wifi.x != 2 || layout.wifi.width != 76) return 2;
  if (layout.bluetooth.x != 82 || layout.bluetooth.width != 76) return 3;
  if (layout.wifi.y != 40 || layout.wifi.height != 100) return 4;
  if (layout.title_y != 14 || layout.prompt_y != 143) return 5;

  const auto small = mesh::ui::makeCompanionTransportSelectorLayout(128, 64);
  if (small.show_title) return 6;
  if (small.wifi.x != 2 || small.wifi.width != 60) return 7;
  if (small.bluetooth.x != 66 || small.bluetooth.width != 60) return 8;
  if (small.wifi.y != 20 || small.wifi.height != 25) return 9;
  if (small.prompt_y != 53) return 10;
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "transport_layout"
            compile_result = subprocess.run(
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
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)

        choice_glyph_width = 6 * 4
        choice_glyph_height = 8 * 4
        for text in ("Wi", "Fi", "BLE"):
            self.assertLessEqual(len(text) * choice_glyph_width, 76)
        status_glyph_width = 6 * 3
        status_glyph_height = 8 * 3
        for text in ("ON", "NEXT"):
            self.assertLessEqual(len(text) * status_glyph_width, 76)
        self.assertLessEqual(len("MODE") * 6 * 3, 160)
        self.assertLessEqual(len("TAP SIDE") * 6 * 2, 160)
        self.assertLessEqual(14 + 8 * 3, 40)
        wifi_first_row_y = 42
        wifi_second_row_y = 76
        self.assertLessEqual(wifi_first_row_y + choice_glyph_height,
                             wifi_second_row_y)
        self.assertLessEqual(wifi_first_row_y + choice_glyph_height, 140)
        self.assertLessEqual(76 + choice_glyph_height, 140)
        self.assertLessEqual(115 + status_glyph_height, 140)
        self.assertLessEqual(143 + 8 * 2, 160)

    def test_touch_split_selector_distinguishes_taps_from_swipes(self):
        source = r'''
#include <helpers/ui/TouchInput.h>
#include <helpers/ui/CompanionTransportSelectorLayout.h>

using mesh::ui::TouchAction;
using mesh::ui::TouchInput;
using mesh::ui::TouchSplitSelector;

static TouchAction release(TouchInput& input,
                           const TouchSplitSelector* selector) {
  input.update(false, -1, -1, 160, 160, false, selector);
  return input.update(false, -1, -1, 160, 160, false, selector);
}

int main() {
  TouchInput input(true, true, 70, true);
  const auto layout = mesh::ui::makeCompanionTransportSelectorLayout(160, 160);
  const TouchSplitSelector selector{
      layout.wifi.x, layout.wifi.width,
      layout.bluetooth.x, layout.bluetooth.width,
      layout.wifi.y, layout.wifi.height};

  input.update(true, 129, 70, 160, 160, false, &selector);
  input.update(true, 128, 70, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::SelectLeft) return 1;

  input.update(true, 30, 70, 160, 160, false, &selector);
  input.update(true, 31, 70, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::SelectRight) return 2;

  input.update(true, 80, 70, 160, 160, false, &selector);
  input.update(true, 80, 70, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::None) return 3;

  input.update(true, 30, 20, 160, 160, false, &selector);
  input.update(true, 30, 20, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::None) return 4;

  input.update(true, 130, 70, 160, 160, false, &selector);
  input.update(true, 30, 72, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::Previous) return 5;

  input.update(true, 30, 70, 160, 160, false, &selector);
  input.update(true, 130, 68, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::Next) return 6;

  // A quick selector tap may occupy only one 25 ms poll. Its bounded target
  // remains unambiguous after the normal two-sample release debounce.
  input.update(true, 129, 120, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::SelectLeft) return 7;

  input.update(true, 30, 120, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::SelectRight) return 8;

  // The gap, title, and exclusive bottom edge remain inert even for a quick
  // contact, while the last pixel inside the taller box is selectable.
  input.update(true, 80, 120, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::None) return 9;

  input.update(true, 129, layout.wifi.y - 1, 160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::None) return 10;

  input.update(true, 129, layout.wifi.y + layout.wifi.height,
               160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::None) return 11;

  input.update(true, 129, layout.wifi.y + layout.wifi.height - 1,
               160, 160, false, &selector);
  if (release(input, &selector) != TouchAction::SelectLeft) return 12;

  // Ordinary pages keep the two-sample protection against a detached swipe
  // endpoint being interpreted as a tap.
  input.update(true, 30, 70, 160, 160, false, nullptr);
  if (release(input, nullptr) != TouchAction::None) return 13;

  return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "touch_split_selector"
            compile_result = subprocess.run(
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
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)

    def test_exclusive_page_renders_and_persists_two_choices(self):
        source = UI.read_text(encoding="utf-8")
        self.assertIn("#ifdef COMPANION_EXCLUSIVE_WIFI_BLE", source)
        self.assertIn("TRANSPORT,", source)
        self.assertIn('"WiFi", wifi_active, wifi_selected', source)
        self.assertIn('"BLE", !wifi_active, !wifi_selected', source)
        self.assertIn("display.fillRect(x, y, width, height);", source)
        self.assertIn('large_transport_text ? "ON" : "ACTIVE"', source)
        self.assertIn('display.setTextSize(4);', source)
        self.assertIn('display.drawTextCentered(x + width / 2, y + 2, "Wi");', source)
        self.assertIn('display.drawTextCentered(x + width / 2, y + 36, "Fi");', source)
        self.assertIn("const bool show_status_label = height >= 44;", source)
        self.assertIn('active ? (large_transport_text ? "ON" : "ACTIVE")', source)
        self.assertIn('large_transport_text ? "NEXT" : "NEXT BOOT"', source)
        self.assertIn('display.setTextSize(3);', source)
        self.assertIn('display.width() / 2, layout.title_y, "MODE"', source)
        self.assertIn('layout.show_title ? "TAP A BOX" : "tap a box"', source)
        self.assertIn("makeCompanionTransportSelectorLayout", source)

        handler_start = source.index(
            "if (_page == HomePage::TRANSPORT\n"
            "        && (key == KEY_ENTER || key == KEY_UP || key == KEY_DOWN))"
        )
        handler_end = source.index("#else", handler_start)
        handler = source[handler_start:handler_end]
        active = handler.index("const CompanionTransportMode active")
        save = handler.index("selectCompanionTransportMode(requested)")
        error = handler.index('showAlert("Transport save failed"')
        reboot = handler.index("if (requested != active) _task->shutdown(true);")
        self.assertLess(active, save)
        self.assertLess(save, error)
        self.assertLess(error, reboot)

        render_start = source.index(
            "} else if (_page == HomePage::TRANSPORT) {"
        )
        render_end = source.index(
            "} else if (_page == HomePage::BLUETOOTH) {", render_start
        )
        render = source[render_start:render_end]
        self.assertIn("const bool wifi_active = isCompanionWiFiEnabled();", render)
        self.assertIn("const bool wifi_selected = getCompanionTransportMode()", render)
        compact_on = render.index("display.setCompactText(true);")
        choices = render.index("drawCompanionTransportChoice(")
        compact_off = render.index("display.setCompactText(false);")
        self.assertLess(compact_on, choices)
        self.assertLess(choices, compact_off)

    def test_only_split_page_maps_box_taps_to_transport_keys(self):
        source = UI.read_text(encoding="utf-8")
        profile = (
            ROOT / "variants" / "sensecap_indicator-espnow" / "platformio.ini"
        ).read_text(encoding="utf-8")
        self.assertIn("-D TOUCH_MIRROR_TAP_X", profile)
        self.assertIn(
            "curr == home\n"
            "        && static_cast<HomeScreen*>(home)"
            "->isTransportSelectorPage()",
            source,
        )
        self.assertIn("curr == msg_preview && UI_MESSAGE_CHANNEL_FOOTER == 1,", source)
        self.assertIn("split_transport_selector, reader_touch_bar);", source)
        self.assertIn(
            "case mesh::ui::TouchAction::SelectLeft:\n"
            "          c = checkDisplayOn(KEY_UP);",
            source,
        )
        self.assertIn(
            "case mesh::ui::TouchAction::SelectRight:\n"
            "          c = checkDisplayOn(KEY_DOWN);",
            source,
        )
        self.assertIn(
            "if (!on_transport_selector) c = checkDisplayOn(KEY_UP);",
            source,
        )
        self.assertIn(
            "if (!on_transport_selector) c = checkDisplayOn(KEY_DOWN);",
            source,
        )


if __name__ == "__main__":
    unittest.main()
