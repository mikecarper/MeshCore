#!/usr/bin/env python3
"""Exercise actual button routing and message filters with a host display."""

from pathlib import Path
from itertools import product
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

PREAMBLE = r'''
#include <Arduino.h>
#include <helpers/ui/UIScreen.h>
#include <helpers/ui/MomentaryButton.h>
#include <helpers/ui/CompanionMessageHistory.h>
#include <helpers/ui/ReaderNavigationHint.h>
#include <helpers/ui/DisplayTextLayout.h>
#include <cassert>
#include <string>
#include <vector>
#define UI_SMALL_MESSAGE_FONT 0
#ifndef UI_BUTTON_READER_HINT
#define UI_BUTTON_READER_HINT 1
#endif
#ifndef UI_MESSAGE_CHANNEL_FOOTER
#define UI_MESSAGE_CHANNEL_FOOTER 0
#endif
#ifndef UI_COMPACT_MESSAGE_STATUS
#define UI_COMPACT_MESSAGE_STATUS 0
#endif
#define UI_MSG_PREVIEW_SIZE 161
#define MAX_GROUP_CHANNELS 4
#define AUTO_OFF_MILLIS 15000
#define MESH_DEBUG_PRINTLN(...)
ColorVal UIColor::window_bkg=0, UIColor::title_bkg=0, UIColor::title_txt=1;
ColorVal UIColor::primary_txt=1, UIColor::secondary_txt=1, UIColor::warning_txt=1;
ColorVal UIColor::popup_bkg=0, UIColor::popup_txt=1, UIColor::corp_blue=2;
uint64_t companionMessageNowMillis() { return millis(); }
uint64_t companionMessageElapsedMillis(uint64_t when) { return millis()-when; }
struct StrHelper {
  static void strncpy(char* out,const char* in,size_t size) { snprintf(out,size,"%s",in); }
};
struct ChannelDetails { char name[32]; };
struct Mesh {
  bool getChannel(int channel,ChannelDetails& details) const {
    const char* names[]={"Public","","Second","Empty"};
    if (channel<0 || channel>=MAX_GROUP_CHANNELS) return false;
    snprintf(details.name,sizeof(details.name),"%s",names[channel]);
    return true;
  }
} the_mesh;
struct Display : DisplayDriver {
  struct Line { int x,y; std::string text; ColorVal color; };
  struct Rect { int x,y,w,h; ColorVal color; };
  std::vector<Line> lines;
  std::vector<Rect> rectangles;
  ColorVal color=1;
  int x=0,y=0;
  bool on=true, compact=false;
#ifdef TEST_INDICATOR_CANVAS
  Display() : DisplayDriver(160,160) {}
  int renderWidth() const override { return TEST_INDICATOR_CANVAS; }
  int renderHeight() const override { return TEST_INDICATOR_CANVAS; }
#else
  Display() : DisplayDriver(128,64) {}
#endif
  bool isOn() override { return on; }
  void turnOn() override { on=true; }
  void turnOff() override { on=false; }
  void clear() override { lines.clear(); rectangles.clear(); }
  void startFrame(ColorVal=0) override { clear(); }
  void endFrame() override {}
  void setTextSize(int) override {}
  void setCompactText(bool value) override { compact=value; }
  void setColor(ColorVal value) override { color=value; }
  void setCursor(int a,int b) override { x=a;y=b; }
  uint16_t getTextWidth(const char* text) override {
#ifdef TEST_INDICATOR_CANVAS
    // Indicator font: 18x24 physical pixels at the ordinary 3x scale;
    // native non-compact text is enlarged 20%. Round bounds outwards.
    return (strlen(text)*(TEST_INDICATOR_CANVAS==480 && !compact ? 72 : 60)+9)/10;
#else
    return strlen(text)*4;
#endif
  }
  void print(const char* text) override {
    assert(x>=0 && x+getTextWidth(text)<=width());
    assert(y>=0 && y+8<=height());
    lines.push_back({x,y,text,color});
  }
  void fillRect(int a,int b,int w,int h) override {
    assert(a>=0 && b>=0 && a+w<=width() && b+h<=height());
  }
  void drawRect(int a,int b,int w,int h) override {
    fillRect(a,b,w,h); rectangles.push_back({a,b,w,h,color});
  }
  void drawXbm(int,int,const uint8_t*,int,int) override {}
  bool contains(const char* text) const {
    for (const auto& line:lines) if (line.text==text) return true;
    return false;
  }
};
struct Screen : UIScreen {
  uint8_t key=0;
  int events=0;
  int render(DisplayDriver&) override { return 0; }
  bool handleInput(char c) override { ++events; key=static_cast<uint8_t>(c); return true; }
};
MomentaryButton user_btn(7,1000,true,true,true);
class UITask {
public:
  DisplayDriver* _display;
  UIScreen* curr=nullptr;
  UIScreen* msg_preview=nullptr;
  UIScreen* home=nullptr;
  UIScreen* john_reader=nullptr;
  uint32_t _auto_off=0,_next_refresh=0;
  int buzzer_changes=0;
  int getMsgCount() const { return 0; }
  explicit UITask(DisplayDriver& display) : _display(&display) {}
  bool isJohnReaderActive() const { return john_reader && curr==john_reader; }
  void gotoHomeScreen() { curr=home; }
  void toggleBuzzer() { ++buzzer_changes; }
  void showJohnReader() { curr=john_reader; }
  void showAlert(const char*,int) {}
  char handleLongPress(char c) { return c; }
  char handleMultiClick(char,bool);
  char handleDoubleClick(char);
  char checkDisplayOn(char);
  void pollButton();
  void routeTouch(mesh::ui::TouchAction action);
  bool isButtonGesturePending() const;
};
'''

SCENARIOS = r'''
void gesture(UITask& task,int count) {
  for (int tap=0;tap<count;++tap) {
    g_mock_pin_levels[7]=LOW; task.pollButton();
    g_mock_millis+=25; task.pollButton();
    g_mock_pin_levels[7]=HIGH; task.pollButton();
    g_mock_millis+=25; task.pollButton();
    if (tap+1<count) { g_mock_millis+=80; task.pollButton(); }
  }
  g_mock_millis+=MOMENTARY_BUTTON_MULTI_CLICK_MS; task.pollButton();
}
int main() {
  resetArduinoMock();
  g_mock_pin_levels[7]=HIGH;
  user_btn.begin(); user_btn.enableQuadrupleClick();
  Display display;
  display.servicePower(false);
  display.wake(mesh::ui::DisplayWake::Button);
  UITask task(display);
  HomeScreen home(&task);
  Screen group;
  MsgPreviewScreen messages(&task);
  task.home=&home; task.msg_preview=task.curr=&messages; task.john_reader=&group;
  messages.addPreview(1,"Alice","public old",0,"Public");
  messages.addPreview(1,"Bob","public new",0,"Public");
  messages.addPreview(1,"Carol","second",2,"Second");
  messages.addPreview(0xFF,"Dan","direct message",-1,nullptr);
  auto expect=[&](const char* header,const char* body) {
    assert(task.curr==&messages);
    display.clear(); messages.render(display);
#if UI_MESSAGE_CHANNEL_FOOTER
    std::string status="Message "; status+=strrchr(header,' ')+1;
    assert(display.contains(status.c_str()) && display.contains(body));
#else
    assert(display.contains(header) && display.contains(body));
#endif
    display.setCompactText(UI_COMPACT_MESSAGE_STATUS);
    const int bottom=display.height()-(UI_MESSAGE_CHANNEL_FOOTER ?
        mesh::ui::makeCompanionMessageChromeLayout(UI_COMPACT_MESSAGE_STATUS).filter_height : 0);
#if UI_READER_TOUCH_BAR
    const auto hint=mesh::ui::makeReaderTouchBar(display,bottom);
    const int exit_height=messages.readerTouchBar()->exit_height;
    assert(exit_height>=24);
    for(const auto& line:display.lines) {
      if(line.text==header) assert(line.y>0 && line.y+8<exit_height);
      if(line.text==body) assert(line.y>=exit_height);
    }
    bool header_divider=false;
    for(const auto& rect:display.rectangles)
      if(rect.y==exit_height-1 && rect.h==1 && rect.w==display.width()) header_divider=true;
    assert(header_divider);
    const char* labels[]={"4<<","2<",">1",">>3","X"};
    for(int cell=0;cell<5;++cell) {
      assert(display.contains(labels[cell]));
      for(const auto& line:display.lines) if(line.text==labels[cell]) {
        assert(line.x>=display.width()*cell/5);
        assert(line.x+display.getTextWidth(labels[cell])<=display.width()*(cell+1)/5);
        assert(line.y>=hint.top && line.y+8<=hint.top+hint.height);
        assert(line.color==UIColor::corp_blue);
      }
    }
    bool divider=false;
    for(const auto& rect:display.rectangles)
      if(rect.x==0 && rect.y==hint.top && rect.w==display.width()
          && rect.h==1 && rect.color==UIColor::corp_blue) divider=true;
    assert(divider);
#if !UI_MESSAGE_CHANNEL_FOOTER
    assert(hint.top+hint.height==display.height());
    assert(!display.contains("<") && !display.contains(">"));
#endif
    for(const auto& line:display.lines)
      if(line.text==body) assert(line.y+8<=hint.top);
#else
    const auto hint=mesh::ui::makeButtonReaderHintLayout(display,display.textLineHeight(),bottom);
    for(int row=0;row<hint.line_count;++row) assert(display.contains(hint.lines[row]));
    for(const auto& line:display.lines) {
      if(line.text==body) assert(line.y+8<=hint.top);
      for(int row=0;row<hint.line_count;++row)
        if(line.text==hint.lines[row]) assert(line.y==hint.top+row*hint.line_height);
    }
#endif
    display.setCompactText(false);
    assert(task.buzzer_changes==0);
  };
  expect("DM 1/1","direct message");
  gesture(task,3); expect("All 1/4","direct message");
  gesture(task,3); expect("Ch 0 1/2","public new");
  gesture(task,1); expect("Ch 0 2/2","public old");
  gesture(task,2); expect("Ch 0 1/2","public new");
  gesture(task,1); expect("Ch 0 2/2","public old");
  gesture(task,4); expect("All 1/4","direct message");
  gesture(task,3); expect("Ch 0 1/2","public new");
  gesture(task,3); expect("Ch 2 1/1","second"); // unused slot 1 is skipped
  gesture(task,3); expect("Ch 3 0/0","No buffered messages");
  gesture(task,3); expect("DM 1/1","direct message");
  gesture(task,3); expect("All 1/4","direct message");
  gesture(task,4); expect("DM 1/1","direct message");
  display.on=false;
  gesture(task,4); // waking consumes the entire gesture
  assert(display.on); expect("DM 1/1","direct message");
  gesture(task,4); expect("Ch 3 0/0","No buffered messages");
  gesture(task,4); expect("Ch 2 1/1","second");
#if UI_READER_TOUCH_BAR
  // Taps use the same freshly rendered rectangles and actual UI action route.
  mesh::ui::TouchInput touch(true,true,70,true,false);
  auto tap=[&](int cell) {
    display.clear(); messages.render(display);
    const auto* bar=messages.readerTouchBar();
    const int visual_x=display.width()*(2*cell+1)/10;
    const int raw_x=display.width()-1-visual_x;
    const int y=bar->top+bar->height/2;
    touch.update(true,raw_x,y,display.width(),display.height(),UI_MESSAGE_CHANNEL_FOOTER,nullptr,bar);
    touch.update(true,raw_x,y,display.width(),display.height(),UI_MESSAGE_CHANNEL_FOOTER,nullptr,bar);
    touch.update(false,-1,-1,display.width(),display.height(),UI_MESSAGE_CHANNEL_FOOTER,nullptr,bar);
    task.routeTouch(touch.update(false,-1,-1,display.width(),display.height(),UI_MESSAGE_CHANNEL_FOOTER,nullptr,bar));
  };
  tap(0); expect("Ch 0 1/2","public new");
  tap(2); expect("Ch 0 2/2","public old");
  tap(1); expect("Ch 0 1/2","public new");
  tap(3); expect("Ch 2 1/1","second");
  display.on=false; tap(4); assert(display.on && task.curr==&messages);
  tap(4); assert(task.curr==&home);
  task.curr=&messages;
  auto body_gesture=[&](int sx,int sy,int ex,int ey) {
    display.clear(); messages.render(display);
    const auto* bar=messages.readerTouchBar();
    touch.update(true,display.width()-1-sx,sy,display.width(),display.height(),false,nullptr,bar);
    touch.update(true,display.width()-1-ex,ey,display.width(),display.height(),false,nullptr,bar);
    touch.update(false,-1,-1,display.width(),display.height(),false,nullptr,bar);
    task.routeTouch(touch.update(false,-1,-1,display.width(),display.height(),false,nullptr,bar));
  };
  tap(0); expect("Ch 0 1/2","public new");
  body_gesture(120,70,120,70); expect("Ch 0 2/2","public old");
  body_gesture(40,70,40,70); expect("Ch 0 1/2","public new");
  body_gesture(120,70,40,70); expect("Ch 0 2/2","public old");
  body_gesture(40,70,120,70); expect("Ch 0 1/2","public new");
  body_gesture(80,100,80,50); expect("Ch 2 1/1","second");
  body_gesture(80,50,80,100); expect("Ch 0 1/2","public new");
  body_gesture(48,messages.readerTouchBar()->top-1,48,messages.readerTouchBar()->top-1);
  expect("Ch 0 1/2","public new"); // just missing 2< cannot close the reader
  body_gesture(80,0,80,0); assert(task.curr==&home);
  task.curr=&messages;
#endif
  // Long press exits, without a delayed tap changing the home screen.
  g_mock_pin_levels[7]=LOW; task.pollButton();
  g_mock_millis+=25; task.pollButton();
  g_mock_millis+=1000; task.pollButton();
  assert(task.curr==&home);
  g_mock_pin_levels[7]=HIGH; task.pollButton();
  g_mock_millis+=25; task.pollButton();
  g_mock_millis+=MOMENTARY_BUTTON_MULTI_CLICK_MS; task.pollButton();
  assert(home.key==0);
#if COMPANION_FEATURE_JOHN
  task.curr=&group;
  gesture(task,1); assert(group.key==KEY_NEXT && group.events==1);
  gesture(task,2); assert(group.key==KEY_PREV && group.events==2);
  gesture(task,3); assert(group.key==KEY_DOWN);
  gesture(task,4); assert(group.key==KEY_UP);
  assert(task.buzzer_changes==0);
#ifdef TEST_INDICATOR_CANVAS
  // Poll real pin waveforms while periodic synchronous RGB work is due.
  // A redraw between clicks must wait, not erase a short press/release from
  // the poller's history. Cover quick and deliberate multi-click rhythms.
  for(int clicks : {3,4}) for(int gap : {80,320,430}) {
    group.events=0; group.key=0;
    uint32_t elapsed=0;
    const uint32_t active_until=(clicks-1)*(70+gap)+70;
    uint32_t redraw_at=35; int redraws=0;
    while(elapsed<active_until+MOMENTARY_BUTTON_MULTI_CLICK_MS+300) {
      g_mock_pin_levels[7]=(elapsed<active_until && elapsed%(70+gap)<70) ? LOW : HIGH;
      task.pollButton();
      if(elapsed>=redraw_at && !task.isButtonGesturePending()) {
        // Simulate a slow synchronous frame, including the time the poller
        // cannot run. With the old unconditional render this loses clicks.
        elapsed+=200; g_mock_millis+=200; redraw_at=elapsed+1000; ++redraws;
      }
      elapsed+=5; g_mock_millis+=5;
    }
    assert(group.events==1 && group.key==(clicks==3 ? KEY_DOWN : KEY_UP));
    assert(redraws>0 && !task.isButtonGesturePending());
  }
#endif
#endif
  task.curr=&home;
  // Exercise the actual home-page navigation, not just a recorded key.
  gesture(task,1); assert(home._page==1 && home.events==1);
  gesture(task,2); assert(home._page==0 && home.events==2);
  gesture(task,2); assert(home._page==HomeScreen::Count-1 && home.events==3);
  gesture(task,1); assert(home._page==0 && home.events==4);
  // No premature single-click while a second press is still possible.
  g_mock_pin_levels[7]=LOW; task.pollButton();
  g_mock_millis+=25; task.pollButton();
  g_mock_pin_levels[7]=HIGH; task.pollButton();
  g_mock_millis+=25; task.pollButton();
  g_mock_millis+=200; task.pollButton();
  assert(home._page==0 && home.events==4);
  g_mock_pin_levels[7]=LOW; task.pollButton();
  g_mock_millis+=25; task.pollButton();
  g_mock_pin_levels[7]=HIGH; task.pollButton();
  g_mock_millis+=25; task.pollButton();
  g_mock_millis+=MOMENTARY_BUTTON_MULTI_CLICK_MS; task.pollButton();
  assert(home._page==HomeScreen::Count-1 && home.events==5);
  gesture(task,3); assert(task.buzzer_changes==1);
  gesture(task,4); assert(task.buzzer_changes==2);
}
'''


class MessageNavigationTest(unittest.TestCase):
    def test_button_events_change_channels_and_keep_other_actions(self):
        source = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        start = source.index(
            "  if (ev == BUTTON_EVENT_CLICK) {\n    c = checkDisplayOn(KEY_NEXT);",
            source.index("void UITask::loop()"))
        button_route = source[start:source.index("  #endif", start)]
        implementation = "\n".join(extract_braced(source, signature) for signature in (
            "char UITask::checkDisplayOn(", "char UITask::handleDoubleClick(",
            "char UITask::handleMultiClick(", "bool UITask::isButtonGesturePending("))
        self.assertIn("millis() >= _next_refresh && curr && !isButtonGesturePending()", source)
        implementation += "\n" + extract_braced(source, "class MsgPreviewScreen :") + ";\n"
        implementation += "void UITask::pollButton() { char c=0; int ev=user_btn.check();\n"
        implementation += button_route + "\nif(c && curr) curr->handleInput(c);\n}\n"
        touch_route = extract_braced(source[source.index("void UITask::loop()"):], "switch (action)")
        implementation += ("void UITask::routeTouch(mesh::ui::TouchAction action) { "
                           "char c=0; bool on_transport_selector=false;\n" + touch_route +
                           "\nif(c && curr) curr->handleInput(c); }\n")
        home = source[source.index("class HomeScreen :"):]
        home_navigation = home[home.index("  bool handleInput(char c) override {"):]
        home_navigation = home_navigation.split("#ifdef COMPANION_EXCLUSIVE_WIFI_BLE", 1)[0]
        home_navigation = home_navigation.replace("{\n", "{\n    Screen::handleInput(c);\n", 1)
        implementation += ("class HomeScreen : public Screen { public: UITask* _task; "
                           "enum HomePage {FIRST,MESSAGES,RECENT,RADIO,Count}; int _page=0; "
                           "HomeScreen(UITask* task):_task(task){}\n" + home_navigation +
                           "return false; } };\n")
        target = (ROOT / "variants/sensecap_indicator-espnow/target.cpp").read_text()
        button = re.search(r"MomentaryButton user_btn\([^;]+;", target).group(0)
        profile = (ROOT / "variants/sensecap_indicator-espnow/platformio.ini").read_text()
        indicator_flags = ["-DHAS_TOUCH=1", "-DPIN_USER_BTN=7"]
        for name in ("UI_BUTTON_READER_HINT", "UI_READER_TOUCH_BAR", "UI_MESSAGE_CHANNEL_FOOTER", "UI_COMPACT_MESSAGE_STATUS",
                     "MOMENTARY_BUTTON_MULTI_CLICK_MS", "UI_DEFER_RENDER_DURING_BUTTON_GESTURE"):
            value = re.search(r"^\s*-D " + name + r"=(\d+)\s*$", profile, re.M)
            self.assertIsNotNone(value, name)
            indicator_flags.append(f"-D{name}={value.group(1)}")
        for enabled, signedness, canvas in product(
                (0, 1), ("-fsigned-char", "-funsigned-char"), (0, 320, 480)):
            with self.subTest(feature=enabled, signedness=signedness, canvas=canvas), tempfile.TemporaryDirectory() as temp:
                preamble = PREAMBLE
                flags = []
                if canvas:
                    preamble = preamble.replace("MomentaryButton user_btn(7,1000,true,true,true);", button)
                    flags = indicator_flags + [f"-DTEST_INDICATOR_CANVAS={canvas}"]
                binary = Path(temp) / "navigation"
                result = subprocess.run([
                    "c++", "-std=c++17", signedness,
                    f"-DCOMPANION_FEATURE_JOHN={enabled}",
                    *flags,
                    "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                    "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                    "-x", "c++", "-", str(ROOT / "src/helpers/ui/MomentaryButton.cpp"),
                    str(ROOT / "src/helpers/ui/DisplayDriver.cpp"),
                    "-o", str(binary),
                ], input=preamble + implementation + SCENARIOS, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([str(binary)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
