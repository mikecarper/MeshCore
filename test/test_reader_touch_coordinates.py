#!/usr/bin/env python3
"""Exercise the real LGFX touch conversion before the rendered reader targets."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

PREAMBLE = r'''
#include <cassert>
#include <string>
#include <vector>
#include <helpers/ui/ReaderNavigationHint.h>
#include <helpers/ui/DisplayTouchCoordinates.h>
ColorVal UIColor::window_bkg=0, UIColor::title_bkg=0, UIColor::title_txt=1;
ColorVal UIColor::primary_txt=1, UIColor::secondary_txt=1, UIColor::warning_txt=1;
ColorVal UIColor::popup_bkg=0, UIColor::popup_txt=1, UIColor::corp_blue=2;
namespace lgfx { namespace v1 { struct touch_point_t { int x,y; }; } }
struct Panel {
  int w=480,h=480,x=0,y=0;
  bool touched=true;
  int width() const { return w; }
  int height() const { return h; }
  int getTouch(lgfx::v1::touch_point_t* point) {
    point->x=x; point->y=y; return touched ? 1 : 0;
  }
};
struct LGFXDisplay : DisplayDriver {
  Panel panel;
  Panel* display=&panel;
  // Retain these fields so this test also compiles the pre-fix implementation.
  int _coordinateScale=3;
  float _outputZoom=1.0f;
  struct Label { int x,y; std::string text; };
  std::vector<Label> labels;
  int cx=0,cy=0,glyph_width=6,divider=-1;
  LGFXDisplay() : DisplayDriver(160,160) {}
  bool isOn() override { return true; }
  void turnOn() override {}
  void turnOff() override {}
  void clear() override { labels.clear(); }
  void startFrame(ColorVal=0) override { clear(); }
  void endFrame() override {}
  void setTextSize(int) override {}
  void setColor(ColorVal) override {}
  void setCursor(int x,int y) override { cx=x; cy=y; }
  uint16_t getTextWidth(const char* text) override { return strlen(text)*glyph_width; }
  void print(const char* text) override { labels.push_back({cx,cy,text}); }
  void fillRect(int x,int y,int w,int h) override {
    assert(x>=0 && y>=0 && x+w<=width() && y+h<=height());
  }
  void drawRect(int x,int y,int w,int h) override {
    fillRect(x,y,w,h); if(h==1 && w==width()) divider=y;
  }
  void drawXbm(int,int,const uint8_t*,int,int) override {}
  bool getTouch(int*,int*) override;
};
'''

SCENARIOS = r'''
using mesh::ui::TouchAction;
TouchAction tap(LGFXDisplay& d,const mesh::ui::TouchNavigationBar& bar,
                int visual_x,int physical_y,bool mirror,int samples=2) {
  // The Indicator's controller X is mirrored; TouchInput applies the board
  // correction. Feed physical 0..479 samples, NOT pre-scaled 0..159 samples.
  d.panel.x=mirror ? d.panel.width()-1-visual_x : visual_x;
  d.panel.y=physical_y;
  mesh::ui::TouchInput input(true,true,70,mirror,false);
  int x=-1,y=-1;
  assert(d.getTouch(&x,&y));
  for(int i=0;i<samples;++i)
    assert(input.update(true,x,y,d.width(),d.height(),false,nullptr,&bar)==TouchAction::None);
  assert(input.update(false,-1,-1,d.width(),d.height(),false,nullptr,&bar)==TouchAction::None);
  return input.update(false,-1,-1,d.width(),d.height(),false,nullptr,&bar);
}
void test_rendered_targets() {
  const TouchAction expected[]={TouchAction::VerticalPrevious,TouchAction::Previous,
      TouchAction::Next,TouchAction::VerticalNext,TouchAction::Select};
  for(int canvas : {320,480}) for(int font : {6,8}) for(bool mirror : {false,true}) {
    LGFXDisplay d;
    d._coordinateScale=canvas/160; d._outputZoom=480.0f/canvas; d.glyph_width=font;
    const auto bar=mesh::ui::makeReaderTouchBar(d,d.height(),d.textLineHeight()+1);
    mesh::ui::drawReaderTouchBar(d,bar);
    assert(d.labels.size()==5 && d.divider==bar.top);
    assert(bar.top+bar.height==d.height());
    // Full rendered row, including every column boundary and final panel pixel.
    for(int py=d.divider*3;py<480;++py) for(int px=0;px<480;++px)
      assert(tap(d,bar,px,py,mirror,1)==expected[px/96]);
    // Actual drawn label centers must activate their own controls too.
    for(int i=0;i<5;++i) {
      const auto& label=d.labels[i];
      int px=(label.x+d.getTextWidth(label.text.c_str())/2)*3;
      int py=(label.y+d.textLineHeight()/2)*3;
      assert(tap(d,bar,px,py,mirror)==expected[i]);
    }
    // Header Exit uses exactly the rendered header height, not a broad zone.
    for(int px=0;px<480;++px) {
      assert(tap(d,bar,px,0,mirror,1)==TouchAction::Select);
      assert(tap(d,bar,px,bar.exit_height*3-1,mirror,1)==TouchAction::Select);
      assert(tap(d,bar,px,bar.exit_height*3,mirror)==
          (px<240 ? TouchAction::Previous : TouchAction::Next));
    }
  }
}
void test_missed_arrow_does_not_exit() {
  LGFXDisplay d;
  const auto bar=mesh::ui::makeReaderTouchBar(d,d.height());
  for(int canvas : {320,480}) {
    d._coordinateScale=canvas/160; d._outputZoom=480.0f/canvas;
    // Reported failure: a tap near 2< falls above the footer and becomes the
    // generic center/select action, which closes the message reader.
    assert(tap(d,bar,144,bar.top*3-1,true)==TouchAction::Previous);
    assert(tap(d,bar,240,bar.top*3-12,true)==TouchAction::Next);
    assert(tap(d,bar,144,bar.top*3,true)==TouchAction::Previous);
    assert(tap(d,bar,432,479,true)==TouchAction::Select);
  }
}
void test_panel_bounds() {
  LGFXDisplay d;
  int x=-1,y=-1;
  for(int bad : {-2,-1,480,481}) {
    d.panel.x=bad; d.panel.y=460; assert(!d.getTouch(&x,&y));
    d.panel.x=144; d.panel.y=bad; assert(!d.getTouch(&x,&y));
  }
  d.panel.x=479; d.panel.y=479; assert(d.getTouch(&x,&y)); assert(x==159 && y==159);
  d.panel.touched=false; assert(!d.getTouch(&x,&y));
  assert(x==-1 && y==-1);
  assert(!d.getTouch(nullptr,&y) && !d.getTouch(&x,nullptr));
  // The shared driver uses the live, rotated output dimensions, including
  // non-square outputs. No stale compile-time zoom can move edge targets.
  d.panel.touched=true; d.panel.w=240; d.panel.h=320;
  d.panel.x=239; d.panel.y=319; assert(d.getTouch(&x,&y));
  assert(x==159 && y==159);
  d.panel.x=120; d.panel.y=160; assert(d.getTouch(&x,&y));
  assert(x==80 && y==80);
  d.panel.w=0; assert(!d.getTouch(&x,&y));
  assert(x==-1 && y==-1);
}
void test_body_gestures_and_chrome_drags() {
  for(int canvas : {320,480}) {
    LGFXDisplay d;
    d._coordinateScale=canvas/160; d._outputZoom=480.0f/canvas;
    const auto bar=mesh::ui::makeReaderTouchBar(d,d.height(),12);
    auto drag=[&](int sx,int sy,int ex,int ey) {
      mesh::ui::TouchInput input(true,true,70,true,false);
      int x,y;
      d.panel.x=479-sx; d.panel.y=sy; assert(d.getTouch(&x,&y));
      input.update(true,x,y,160,160,false,nullptr,&bar);
      d.panel.x=479-ex; d.panel.y=ey; assert(d.getTouch(&x,&y));
      input.update(true,x,y,160,160,false,nullptr,&bar);
      input.update(false,-1,-1,160,160,false,nullptr,&bar);
      auto action=input.update(false,-1,-1,160,160,false,nullptr,&bar);
      assert(input.update(false,-1,-1,160,160,false,nullptr,&bar)==TouchAction::None);
      return action;
    };
    assert(drag(360,220,120,220)==TouchAction::Next);  // swipe left
    assert(drag(120,220,360,220)==TouchAction::Previous);
    assert(drag(240,300,240,120)==TouchAction::VerticalNext); // up = >>
    assert(drag(240,120,240,300)==TouchAction::VerticalPrevious); // down = <<
    // Starting in the body and ending over X/header remains a swipe, not Exit.
    assert(drag(432,200,432,479)==TouchAction::VerticalPrevious);
    assert(drag(240,200,240,0)==TouchAction::VerticalNext);
    assert(drag(120,460,432,460)==TouchAction::None);
    assert(drag(120,12,360,12)==TouchAction::None);
    // Small movement out of a button's row/cell must not select a neighbor.
    assert(drag(144,bar.top*3,144,bar.top*3-3)==TouchAction::None);
    assert(drag(382,460,386,460)==TouchAction::None);
    assert(drag(144,35,144,39)==TouchAction::None);
  }
}
int main(int argc,char** argv) {
  assert(argc==2);
  if(std::string(argv[1])=="targets") test_rendered_targets();
  else if(std::string(argv[1])=="miss") test_missed_arrow_does_not_exit();
  else if(std::string(argv[1])=="gestures") test_body_gestures_and_chrome_drags();
  else test_panel_bounds();
}
'''


class ReaderTouchCoordinatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.executable = Path(cls.temp.name) / "reader_touch_coordinates"
        implementation = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text()
        actual_get_touch = extract_braced(implementation, "bool LGFXDisplay::getTouch(")
        result = subprocess.run(
            ["c++", "-std=c++17", "-O1", "-fsanitize=address,undefined",
             "-fno-pie", "-no-pie", f"-I{ROOT / 'src'}", "-x", "c++", "-",
             "-o", str(cls.executable)],
            input=PREAMBLE + actual_get_touch + SCENARIOS,
            text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def run_scenario(self, scenario):
        result = subprocess.run([str(self.executable), scenario], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_panel_coordinates_match_rendered_footer(self):
        self.run_scenario("targets")

    def test_missed_arrow_never_becomes_exit(self):
        self.run_scenario("miss")

    def test_invalid_panel_points_do_not_round_into_edge_targets(self):
        self.run_scenario("bounds")

    def test_body_swipes_and_chrome_drags(self):
        self.run_scenario("gestures")

    def test_indicator_keeps_vertical_direction_independent_of_mirrored_x(self):
        profile = (ROOT / "variants/sensecap_indicator-espnow/platformio.ini").read_text()
        task = (ROOT / "examples/companion_radio/ui-new/UITask.h").read_text()
        self.assertIn("-D TOUCH_REVERSE_VERTICAL_SWIPE=0", profile)
        self.assertIn("TOUCH_REVERSE_VERTICAL_SWIPE != 0", task)
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        self.assertIn("layout.header_divider_y + 1", source)
        self.assertIn("if (!_display->isOn()) reader_touch_bar = nullptr;", source)
        reader = (ROOT / "examples/companion_radio/ui-new/JohnReaderScreen.h").read_text()
        self.assertIn("makeReaderTouchBar(hint_text, bottom, top - 1)", reader)


class IndicatorTouchOrientationTest(unittest.TestCase):
    def test_actual_board_driver_corrects_bottom_to_top_reports(self):
        generic = extract_braced(
            (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text(),
            "bool LGFXDisplay::getTouch(")
        board = extract_braced(
            (ROOT / "variants/sensecap_indicator-espnow/SCIndicatorDisplay.h").read_text(),
            "bool getTouch(int* x, int* y) override")
        scenarios = r'''
#include <helpers/ui/TouchDebugOverlay.h>
using namespace mesh::ui;
int main() {
  for(int canvas : {320,480}) {
    SCIndicatorDisplay d;
    d._coordinateScale=canvas/160; d._outputZoom=480.0f/canvas;
    const auto bar=makeReaderTouchBar(d,160,readerTouchHeaderHeight(12));
    const auto regions=makeTouchDebugAreas(160,160,70,false,nullptr,&bar);
    assert(bar.exit_height==24 && regions.count==8);
    // Actual Indicator reports BOTH axes opposite to the visible panel.
    // Exercise every physical pixel through the real board + generic drivers,
    // the gesture handler and the region used for green press feedback.
    for(int py=0;py<480;++py) for(int px=0;px<480;++px) {
      d.panel.x=479-px; d.panel.y=479-py;
      int x=-1,y=-1;
      assert(d.getTouch(&x,&y));
      assert(159-x==px/3 && y==py/3);
      const int region=touchDebugAreaAt(regions,159-x,y);
      const int expected=py<72 ? 0 : py<bar.top*3 ? (px<240 ? 1 : 2) : 3+px/96;
      assert(region==expected);
      TouchInput input(true,true,70,true,false);
      input.update(true,x,y,160,160,false,nullptr,&bar);
      input.update(true,x,y,160,160,false,nullptr,&bar);
      input.update(false,-1,-1,160,160,false,nullptr,&bar);
      assert(input.update(false,-1,-1,160,160,false,nullptr,&bar)==regions.areas[expected].action);
    }
    auto drag=[&](int sx,int sy,int ex,int ey) {
      TouchInput input(true,true,70,true,false);
      int x,y;
      d.panel.x=479-sx; d.panel.y=479-sy; assert(d.getTouch(&x,&y));
      input.update(true,x,y,160,160,false,nullptr,&bar);
      d.panel.x=479-ex; d.panel.y=479-ey; assert(d.getTouch(&x,&y));
      input.update(true,x,y,160,160,false,nullptr,&bar);
      input.update(false,-1,-1,160,160,false,nullptr,&bar);
      return input.update(false,-1,-1,160,160,false,nullptr,&bar);
    };
    assert(drag(240,300,240,120)==TouchAction::VerticalNext);
    assert(drag(240,120,240,300)==TouchAction::VerticalPrevious);
    assert(drag(360,220,120,220)==TouchAction::Next);
    assert(drag(120,220,360,220)==TouchAction::Previous);
    for(int bad : {-1,480}) {
      int x=0,y=0;
      d.panel.y=bad; assert(!d.getTouch(&x,&y));
      assert(x==-1 && y==-1);
    }
    int x,y;
    assert(!d.getTouch(nullptr,&y) && !d.getTouch(&x,nullptr));
  }
}
'''
        source = PREAMBLE + generic + "struct SCIndicatorDisplay : LGFXDisplay {\n" + board + "\n};\n" + scenarios
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "indicator_touch_orientation"
            compiled = subprocess.run(["c++", "-std=c++17", "-O1",
                "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                f"-I{ROOT / 'src'}", "-x", "c++", "-", "-o", str(binary)],
                input=source, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            tested = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(tested.returncode, 0, tested.stderr)


if __name__ == "__main__":
    unittest.main()
