#!/usr/bin/env python3
"""Yellow outlines must describe real touch actions, not a second guessed UI."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_reader_touch_coordinates import PREAMBLE
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

TEST = r'''
#include <helpers/ui/CompanionTransportSelectorLayout.h>
using namespace mesh::ui;
struct DebugDisplay : LGFXDisplay {
  ColorVal color=0;
  std::vector<ColorVal> pixels=std::vector<ColorVal>(160*160,0);
  void setColor(ColorVal c) override { color=c; }
  void fillRect(int x,int y,int w,int h) override {
    LGFXDisplay::fillRect(x,y,w,h);
    for(int py=y;py<y+h;++py) for(int px=x;px<x+w;++px)
      pixels[py*160+px]=color;
  }
};
void verify_actions(int width,int center,bool bottom,
                    const TouchSplitSelector* split,const TouchNavigationBar* reader) {
  const auto regions=makeTouchDebugAreas(width,width,center,bottom,split,reader);
  for(bool mirrored : {false,true}) for(int y=0;y<width;++y) for(int x=0;x<width;++x) {
    TouchAction expected=TouchAction::None;
    int matches=0;
    for(int i=0;i<regions.count;++i) {
      const auto& r=regions.areas[i];
      if(x>=r.x && x<r.x+r.width && y>=r.y && y<r.y+r.height) {
        expected=r.action; ++matches;
      }
    }
    assert(matches<=1);
    TouchInput input(true,true,center,mirrored,false);
    int tx=mirrored ? width-1-x : x;
    input.update(true,tx,y,width,width,bottom,split,reader);
    input.update(true,tx,y,width,width,bottom,split,reader);
    input.update(false,-1,-1,width,width,bottom,split,reader);
    assert(input.update(false,-1,-1,width,width,bottom,split,reader)==expected);
  }
}
int main() {
  // Home: three zones. Transport: choices plus page edges, no inert gaps.
  // Reader: header, two body halves, and all five footer cells.
  for(int width : {137,160,320,480}) {
    for(int center : {34,70,100}) {
      verify_actions(width,center,false,nullptr,nullptr);
      verify_actions(width,center,true,nullptr,nullptr);
    }
    TouchSplitSelector split={2,width/2-4,width/2+2,width/2-4,40,width-50};
    verify_actions(width,70,false,&split,nullptr);
    verify_actions(width,70,true,&split,nullptr);
    for(int center : {34,70,100}) {
      const auto layout=makeCompanionTransportSelectorLayout(width,width,center);
      TouchSplitSelector centered={layout.wifi.x,layout.wifi.width,
          layout.bluetooth.x,layout.bluetooth.width,layout.wifi.y,
          layout.wifi.height,layout.side_nav_width};
      verify_actions(width,center,false,&centered,nullptr);
      verify_actions(width,center,true,&centered,nullptr);
      const auto areas=makeTouchDebugAreas(width,width,center,false,&centered,nullptr);
      assert(areas.count==(center==100 ? 2 : 4));
    }
    TouchNavigationBar bar;
    bar.top=width-24; bar.height=24; bar.exit_height=12;
    verify_actions(width,70,false,nullptr,&bar);
    bar.top-=10; // optional space below the footer remains truthfully marked
    verify_actions(width,70,true,nullptr,&bar);
  }
  for(int canvas : {320,480}) {
    DebugDisplay d;
    d._coordinateScale=canvas/160; d._outputZoom=480.0f/canvas;
    auto bar=makeReaderTouchBar(d,160,10);
    auto regions=makeTouchDebugAreas(160,160,70,false,nullptr,&bar);
    assert(regions.count==8);
    drawTouchDebugAreas(d,regions);
    int dots=0,empty_edges=0;
    for(int y=0;y<160;++y) for(int x=0;x<160;++x) {
      bool perimeter=false;
      for(int i=0;i<regions.count;++i) {
        const auto& r=regions.areas[i];
        if(x>=r.x && x<r.x+r.width && y>=r.y && y<r.y+r.height)
          perimeter |= x==r.x || x==r.x+r.width-1 || y==r.y || y==r.y+r.height-1;
      }
      if(d.pixels[y*160+x]) {
        assert(perimeter && d.pixels[y*160+x]==color_theme::TOUCH_OUTLINE); ++dots;
      } else if(perimeter) ++empty_edges;
    }
    assert(dots>100 && empty_edges>dots); // dotted, not solid or filled
    for(int i=0;i<regions.count;++i) {
      const auto& r=regions.areas[i];
      assert(d.pixels[r.y*160+r.x]==color_theme::TOUCH_OUTLINE);
      assert(d.pixels[(r.y+r.height-1)*160+r.x+r.width-1]==color_theme::TOUCH_OUTLINE);
    }
    for(int active=0;active<regions.count;++active) {
      const auto& target=regions.areas[active];
      const int x=target.x+target.width/2, y=target.y+target.height/2;
      assert(touchDebugAreaAt(regions,x,y)==active);
      drawTouchDebugAreas(d,regions,x,y);
      for(int i=0;i<regions.count;++i) {
        const auto& r=regions.areas[i];
        assert(d.pixels[r.y*160+r.x]==(i==active
            ? color_theme::TOUCH_PRESSED : color_theme::TOUCH_OUTLINE));
      }
      drawTouchDebugAreas(d,regions); // release restores yellow immediately
      assert(d.pixels[target.y*160+target.x]==color_theme::TOUCH_OUTLINE);
    }
    assert(touchDebugAreaAt(regions,-1,-1)==-1);
    assert(touchDebugAreaAt(regions,160,159)==-1);
  }
  // RGB565 yellow used to fall through to palette index 0 (black). Exercise
  // the actual production indexed-colour conversion and palette entry.
  assert(actualRenderColor(color_theme::TOUCH_OUTLINE)==color_theme::INDEX_TOUCH_OUTLINE);
  assert(UI_PALETTE[color_theme::INDEX_TOUCH_OUTLINE]==0xFFFF00);
  assert(actualRenderColor(color_theme::TOUCH_PRESSED)==color_theme::INDEX_TOUCH_PRESSED);
  assert(UI_PALETTE[color_theme::INDEX_TOUCH_PRESSED]==0x00FF00);
}
'''


class TouchDebugOverlayTest(unittest.TestCase):
    def test_outlines_match_every_tap_pixel_and_stay_yellow(self):
        display = (ROOT / "src/helpers/ui/LGFXDisplay.cpp").read_text()
        actual_touch = extract_braced(display, "bool LGFXDisplay::getTouch(")
        actual_color = extract_braced(display, "uint32_t LGFXDisplay::renderColor(").replace(
            "uint32_t LGFXDisplay::renderColor(ColorVal color) const",
            "uint32_t actualRenderColor(ColorVal color)")
        palette = extract_braced(display, "static const uint32_t UI_PALETTE[16]") + ";"
        source = '#define UI_BUFFER_COLOR_DEPTH 4\n#include <helpers/ui/TouchDebugOverlay.h>\n'
        source += PREAMBLE + actual_touch + actual_color + palette + TEST
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "touch_debug_overlay"
            compiled = subprocess.run(["c++", "-std=c++17", "-O1",
                "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                f"-I{ROOT / 'src'}", "-x", "c++", "-", "-o", str(binary)],
                input=source, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            tested = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_overlay_uses_the_same_current_controls_after_content_render(self):
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        loop = extract_braced(source, "void UITask::loop()")
        self.assertIn("getTouchControls(transport_touch_selector, split_transport_selector, reader_touch_bar)", loop)
        self.assertIn("getTouchControls(transport, split, reader)", loop)
        render = loop.index("int delay_millis = curr->render(*_display);")
        outline = loop.index("mesh::ui::drawTouchDebugAreas", render)
        present = loop.index("_display->endFrame();", outline)
        self.assertLess(render, outline)
        self.assertLess(outline, present)
        self.assertIn("if (_touch_debug_enabled && !isPairingScreenActive())", loop[render:outline])
        feedback = extract_braced(loop, "if (_touch_debug_enabled)")
        self.assertIn("mesh::ui::touchDebugAreaAt", feedback)
        profile = (ROOT / "variants/sensecap_indicator-espnow/platformio.ini").read_text()
        self.assertNotIn("UI_TOUCH_DEBUG_OUTLINES", profile)
        self.assertIn("TOUCH_MIRROR_TAP_X_ENABLED ? _display->width() - 1 - touch_x : touch_x", loop)
        self.assertIn("if (debug_area != _touch_debug_area || curr != _touch_debug_screen) _next_refresh = 0;", loop)
        self.assertIn("_touch_debug_screen == curr ? _touch_debug_x : -1", loop)


if __name__ == "__main__":
    unittest.main()
