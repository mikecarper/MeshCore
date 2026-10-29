#!/usr/bin/env python3
"""Run actual Cardputer controls, drawing and shared display policy on the host."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from test_cardputer_ui import function

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/m5stack_cardputer_adv"


class CardputerLifecycleTests(unittest.TestCase):
    def test_actual_controls_notifications_power_and_night_drawing(self):
        task = (VARIANT / "companion_radio/ui-cardputer/CardputerUITask.cpp").read_text(encoding="ascii")
        display = (VARIANT / "hw/CardputerDisplay.cpp").read_text(encoding="ascii")
        abstract = (ROOT / "examples/companion_radio/AbstractUITask.h").read_text(encoding="ascii")
        code = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "helpers/ui/ColorTheme.h"
#include "helpers/CompanionNotificationPolicy.h"
#include "helpers/ui/BluetoothPairingUiPolicy.h"
uint32_t fake_now=0;
unsigned long millis() { return fake_now; }
bool night=false;
namespace mesh { namespace ui { bool displayNightActive() { return night; } } }
constexpr uint16_t TFT_BLACK=0,TFT_WHITE=0xFFFF,TFT_DARKGRAY=0x7BEF;
struct LCDMock {
  std::vector<uint16_t> colors;
  std::vector<std::string> text;
  uint16_t current=0;
  unsigned brightness=0,text_level=0,prints=0;
  void setBaseColor(uint16_t c) { colors.push_back(c); }
  bool begin() { return true; }
  void setRotation(int) {}
  void setColorDepth(int) {}
  void setBrightness(uint8_t b) { brightness=b; }
  void setTextColor(uint16_t c) { colors.push_back(c); }
  void setTextColor(uint16_t c,uint16_t b) { colors.push_back(c); colors.push_back(b); }
  template<class F> void setEmojiCallback(F) {}
  void setColor(uint16_t c) { current=c; colors.push_back(c); }
  void clear(uint16_t c) { colors.push_back(c); }
  void startWrite() {}
  void endWrite() {}
  void wakeup() {}
  void sleep() {}
  int textWidth(const char* s) { return strlen(s)*8; }
  int fontHeight() { return 16; }
  void drawRect(int,int,int,int) { colors.push_back(current); }
  void drawRect(int,int,int,int,uint16_t c) { colors.push_back(c); }
  void fillRect(int,int,int,int) { colors.push_back(current); }
  void drawBitmap(int,int,const uint8_t*,int,int,uint16_t c) { colors.push_back(c); }
  void setCursor(int,int) {}
  void print(const char* value) { ++prints; colors.push_back(current); text.emplace_back(value); }
};
namespace lgfx { using LGFXBase=LCDMock; }
struct CardputerDisplay: DisplayDriver {
  enum { TEXT_LEVEL_COUNT=3 };
  enum PaletteColor:uint16_t { P_BLACK=0,P_WHITE=0xFFFF,P_RED=0xF800,P_GREEN=0x07E0,
    P_BLUE=0x001F,P_ORANGE=0xFD20,P_YELLOW=0xFFE0,P_GRAY=0x8410 };
  bool _isOn=false;
  uint16_t _lastColor=0;
  uint8_t _textLevel=1;
  LCDMock LCD;
  CardputerDisplay():DisplayDriver(240,135) {}
  bool begin();
  bool isOn() override { return _isOn; }
  void turnOn() override;
  void turnOff() override;
  void clear() override;
  void startFrame(ColorVal bkg=UIColor::window_bkg) override;
  void endFrame() override;
  void setTextSize(int) override {}
  void setColor(ColorVal) override;
  void setCursor(int,int) override;
  void print(const char*) override;
  void fillRect(int,int,int,int) override;
  void drawRect(int,int,int,int) override;
  void drawXbm(int,int,const uint8_t*,int,int) override;
  uint16_t getTextWidth(const char*) override;
  void setTextLevel(uint8_t level) { _textLevel=level; LCD.text_level=level; }
  uint8_t getTextLevel() const { return _textLevel; }
  void setBrightness(uint8_t level) { LCD.setBrightness(level); }
};
'''
        color_start = display.index("ColorVal UIColor::window_bkg")
        color_end = display.index("\n\nint32_t emoji_draw_callback", color_start)
        code += display[color_start:color_end] + "\n"
        code += function(display, "int32_t emoji_draw_callback(") + "\n"
        for signature in (
            "bool CardputerDisplay::begin(", "void CardputerDisplay::turnOn(",
            "void CardputerDisplay::turnOff(", "void CardputerDisplay::clear(",
            "void CardputerDisplay::startFrame(", "void CardputerDisplay::endFrame(",
            "void CardputerDisplay::setColor(", "void CardputerDisplay::setCursor(",
            "void CardputerDisplay::print(", "void CardputerDisplay::fillRect(",
            "void CardputerDisplay::drawRect(", "void CardputerDisplay::drawXbm(",
            "uint16_t CardputerDisplay::getTextWidth(",
        ):
            code += function(display, signature) + "\n"
        code += r'''
struct Speaker {
  unsigned tones=0,sleeps=0;
  void queueTone(int,int,float=0.5f) { ++tones; }
  bool isBusy() const { return false; }
  void sleep() { ++sleeps; }
};
struct Board {
  bool usb=false;
  Speaker speaker;
  bool isExternalPowered() { return usb; }
  bool isUsbHostConnected() { return usb; }
  Speaker* getSpeaker() { return &speaker; }
};
struct Interfaces {
  bool pending=false,enabled=true,ble=false;
  bool takePairingRequest() { bool result=pending; pending=false; return result; }
};
namespace Keyboard {
enum Key { KEY_ESC,KEY_RETURN,KEY_A };
struct Event { Key key; };
}
struct Prefs { uint8_t gps_enabled=1,buzzer_quiet=0,powersaving_enabled=0; };
struct CustomPrefs { uint8_t text_level=1,brightness=96; };
struct NotificationSink: mesh::notify::Sink {
  CardputerDisplay* display;
  explicit NotificationSink(CardputerDisplay* d):display(d) {}
  uint8_t capabilities() const override { return mesh::notify::Screen; }
  bool gpioAvailable(uint8_t) const override { return false; }
  void pulse(mesh::notify::Output,bool,int8_t) override {}
  void melody(const char*) override {}
  void screen(int8_t mode) override { display->notificationPower(mode); }
};
struct NotificationStore: mesh::notify::Store {
  bool save(const mesh::notify::Settings&) override { return true; }
};
struct Mesh {
  mesh::notify::Controller* notifications=nullptr;
  unsigned button_calls=0;
  bool notificationButton() { ++button_calls; return notifications && notifications->button(); }
  Prefs* prefs=nullptr;
  CustomPrefs* custom=nullptr;
  CustomPrefs committed;
  bool fail=false;
  unsigned saves=0;
  uint32_t getBLEPin() const { return 246810; }
  bool savePrefs() { if(fail) return false; ++saves; return true; }
  bool saveCustomPrefs() {
    if(fail) { *custom=committed; return false; }
    committed=*custom; ++saves; return true;
  }
  bool setGpsEnabled(bool on) {
    if(fail) return false;
    prefs->gps_enabled=on; ++saves; return true;
  }
  bool handleLocalCommand(const char* command,char* reply) {
    if(fail) { strcpy(reply,"Error: save failed"); return true; }
    assert(!strcmp(command,"powersaving on") || !strcmp(command,"powersaving off"));
    prefs->powersaving_enabled=!strcmp(command,"powersaving on"); ++saves;
    strcpy(reply,"OK - powersaving"); return true;
  }
} the_mesh_cp;
struct CardputerScreen {};
struct MainScreen: CardputerScreen {
  enum { FIRST,CHAT };
  int page=FIRST;
  int getCurrentPage() { return page; }
};
struct NewMessageScreen: CardputerScreen {
  unsigned messages=0;
  void newMessage(const char*,const char*) { ++messages; }
};
enum class UIEventType { none,channelMessage,contactMessage,ack };
enum class SoundType { Keyboard,NewMessage,DiscoveryResult,MessageAck };
struct CardputerUITask {
  CardputerDisplay* _display=nullptr;
  Board* _board=nullptr;
  Interfaces* _interfaceManager=nullptr;
  Prefs* _node_prefs=nullptr;
  CustomPrefs* _custom_prefs=nullptr;
  CardputerScreen *main_screen=nullptr,*new_message_screen=nullptr,*current_screen=nullptr;
  bool connected=false,_message_notification_override=false,_message_notification_screen_override=false;
  uint8_t _notification_outputs=31;
  int unsynced_msg_count=0;
  char alert_text[80]={};
  unsigned long alert_expiry=0,next_refresh=0;
  uint32_t pairing_prompt_until=0;
  bool hasConnection() const { return connected || _interfaceManager->ble; }
  bool hasBluetoothConnection() const { return _interfaceManager->ble; }
  bool isBluetoothEnabled() const { return _interfaceManager->enabled; }
  void scheduleRefresh(unsigned ms=0) { next_refresh=millis()+ms; }
  void setCurrScreen(CardputerScreen* screen) { current_screen=screen; scheduleRefresh(); }
  bool shouldWakeDisplayForMessage() const;
  bool isAlertActive() const;
  bool isChatVisible() const;
  bool handleNotificationInput();
  void showAlert(const char*,int);
  void serviceDisplayPower();
  void servicePairingState();
  bool isPairingPromptActive() const;
  void renderPairingPrompt();
  bool handlePairingInput(const Keyboard::Event&);
  bool turnDisplayOn();
  bool getGPSState();
  bool toggleGPS();
  bool toggleBuzzer();
  bool togglePowerSave();
  void cycleBrightness();
  void cycleTextSize();
  void playSound(SoundType);
  void notify(UIEventType);
  uint8_t notificationCapabilities() const;
  void notificationScreen(int8_t);
  void newMsg(uint8_t,const char*,const char*,int,int=-1,const char*=nullptr,int=-1);
};
'''.replace("const char*=", "const char* =")
        code += "bool CardputerUITask::" + function(abstract, "bool shouldWakeDisplayForMessage() const").removeprefix("bool ") + "\n"
        for signature in (
            "void CardputerUITask::showAlert(", "void CardputerUITask::serviceDisplayPower(",
            "bool CardputerUITask::isAlertActive(", "bool CardputerUITask::isChatVisible(",
            "bool CardputerUITask::handleNotificationInput(",
            "void CardputerUITask::servicePairingState(", "bool CardputerUITask::isPairingPromptActive(",
            "void CardputerUITask::renderPairingPrompt(", "bool CardputerUITask::handlePairingInput(",
            "bool CardputerUITask::turnDisplayOn(", "bool CardputerUITask::getGPSState(",
            "bool CardputerUITask::toggleGPS(", "bool CardputerUITask::toggleBuzzer(",
            "bool CardputerUITask::togglePowerSave(", "void CardputerUITask::cycleBrightness(",
            "void CardputerUITask::cycleTextSize(", "void CardputerUITask::playSound(",
            "void CardputerUITask::notify(", "uint8_t CardputerUITask::notificationCapabilities(",
            "void CardputerUITask::notificationScreen(", "void CardputerUITask::newMsg(",
        ):
            code += function(task, signature) + "\n"
        code += r'''
int main() {
  CardputerDisplay display;
  // Every drawing boundary, including implicit glyph and emoji colors, follows night mode.
  night=true; assert(display.begin());
  display.startFrame(0x1234);
  const uint8_t bitmap[]={0xFF};
  for(uint16_t color:{uint16_t(0),uint16_t(0xFFFF),uint16_t(0x07E0),uint16_t(0x1234)}) {
    display.setColor(color); display.print("text"); display.fillRect(0,0,1,1);
    display.drawRect(0,0,1,1); display.drawXbm(0,0,bitmap,1,1);
  }
  emoji_draw_callback(&display.LCD,0,0,0,16); display.clear(); display.endFrame();
  for(auto color:display.LCD.colors) assert(color==0 || color==0xF800);
  night=false; display.setColor(CardputerDisplay::P_GREEN);
  assert(display._lastColor==CardputerDisplay::P_GREEN);
  display.setColor(CardputerDisplay::P_WHITE); assert(display._lastColor==0xFFFF);

  Board board; Interfaces interfaces; Prefs prefs; CustomPrefs custom;
  MainScreen main; NewMessageScreen messages;
  CardputerUITask ui;
  ui._display=&display; ui._board=&board; ui._interfaceManager=&interfaces;
  ui._node_prefs=&prefs; ui._custom_prefs=&custom;
  ui.main_screen=&main; ui.current_screen=&main; ui.new_message_screen=&messages;
  the_mesh_cp.prefs=&prefs; the_mesh_cp.custom=&custom;
  auto& power=mesh::ui::displayPowerPrefs();
  power.battery.mode=mesh::ui::DisplayMode::Automatic; power.battery.seconds=27;
  power.usb.mode=mesh::ui::DisplayMode::Off;
  ui.serviceDisplayPower(); assert(display.isOn());
  fake_now=5000; ui.serviceDisplayPower(); assert(!display.isOn());
  ui.newMsg(0,"peer","message",1); assert(display.isOn() && messages.messages==1);
  fake_now=31999; ui.serviceDisplayPower(); assert(display.isOn());
  fake_now=32000; ui.serviceDisplayPower(); assert(!display.isOn());
  // The first waking key is consumed; subsequent input extends the saved timeout.
  assert(ui.turnDisplayOn()); assert(display.isOn()); assert(!ui.turnDisplayOn());
  fake_now=59000; ui.serviceDisplayPower(); assert(!display.isOn());
  ui.connected=true; ui.current_screen=&main;
  ui.newMsg(0,"peer","online",2); assert(!display.isOn() && messages.messages==1 && ui.current_screen==&main);
  ui.connected=false;
  ui._notification_outputs=mesh::notify::Sound;
  ui.newMsg(0,"peer","screen muted",3); assert(!display.isOn() && messages.messages==1);
  ui._notification_outputs=31; ui._message_notification_screen_override=true;
  ui.newMsg(0,"peer","managed screen",4); assert(!display.isOn() && messages.messages==1);
  ui._message_notification_screen_override=false;
  power.battery.mode=mesh::ui::DisplayMode::Button;
  ui.serviceDisplayPower(); ui.newMsg(0,"peer","button mode",5); assert(!display.isOn());
  assert(ui.turnDisplayOn() && display.isOn());
  board.usb=true; ui.serviceDisplayPower(); assert(!display.isOn());
  ui.newMsg(0,"peer","USB off",6); assert(!display.isOn());
  ui.notificationScreen(1); assert(display.isOn());
  ui.notificationScreen(-1); ui.serviceDisplayPower(); assert(!display.isOn());
  assert(ui.notificationCapabilities()==mesh::notify::Screen);
  unsigned tones=board.speaker.tones;
  ui._notification_outputs=0; ui.notify(UIEventType::contactMessage); assert(board.speaker.tones==tones);
  ui._notification_outputs=mesh::notify::Sound; ui._message_notification_override=true;
  ui.notify(UIEventType::contactMessage); assert(board.speaker.tones==tones);
  ui._message_notification_override=false; prefs.buzzer_quiet=1;
  ui.notify(UIEventType::contactMessage); assert(board.speaker.tones==tones);
  prefs.buzzer_quiet=0; ui.notify(UIEventType::contactMessage); assert(board.speaker.tones==tones+1);

  the_mesh_cp.fail=true;
  assert(!ui.toggleGPS() && prefs.gps_enabled==1);
  assert(!ui.toggleBuzzer() && prefs.buzzer_quiet==0);
  assert(!ui.togglePowerSave() && prefs.powersaving_enabled==0);
  display.setTextLevel(1); display.setBrightness(96);
  ui.cycleTextSize(); assert(custom.text_level==1 && display.getTextLevel()==1);
  ui.cycleBrightness(); assert(custom.brightness==96 && display.LCD.brightness==96);
  the_mesh_cp.fail=false;
  assert(ui.toggleGPS() && prefs.gps_enabled==0);
  assert(ui.toggleBuzzer() && prefs.buzzer_quiet==1);
  assert(ui.togglePowerSave() && prefs.powersaving_enabled==1);
  ui.cycleTextSize(); assert(custom.text_level==2 && display.getTextLevel()==2);
  ui.cycleBrightness(); assert(custom.brightness==160 && display.LCD.brightness==160);
  // Real pairing events wake pairing-only policy, without replacing the chat/draft owner.
  power.usb.mode=mesh::ui::DisplayMode::Pairing;
  ui.current_screen=&main;
  ui.servicePairingState(); assert(!ui.isPairingPromptActive() && !display.isOn());
  interfaces.pending=true; ui.servicePairingState();
  assert(!interfaces.pending && ui.isPairingPromptActive() && display.isOn());
  assert(ui.current_screen==&main && ui.pairing_prompt_until==fake_now+120000);
  display.LCD.text.clear(); ui.renderPairingPrompt();
  unsigned pin_count=0;
  for(const auto& text:display.LCD.text) if(text=="246810") ++pin_count;
  assert(pin_count==1);
  Keyboard::Event enter{Keyboard::KEY_RETURN};
  assert(ui.handlePairingInput(enter) && ui.current_screen==&main && ui.isPairingPromptActive());
  ui.newMsg(0,"peer","during pairing",8); assert(ui.current_screen==&main);
  fake_now=ui.pairing_prompt_until-1; ui.servicePairingState(); assert(ui.isPairingPromptActive());
  ++fake_now; ui.servicePairingState(); assert(!ui.isPairingPromptActive() && !display.isOn());
  assert(ui.current_screen==&main);

  // A saved Off profile stays dark, even when the phone requests a PIN.
  power.usb.mode=mesh::ui::DisplayMode::Off;
  interfaces.pending=true; ui.servicePairingState();
  assert(ui.isPairingPromptActive() && !display.isOn());
  // BLE completion, unlike an unrelated USB client, ends the prompt immediately.
  interfaces.ble=true; ui.servicePairingState();
  assert(!ui.isPairingPromptActive() && ui.pairing_prompt_until==0);
  interfaces.ble=false; power.usb.mode=mesh::ui::DisplayMode::ButtonPairing;
  interfaces.pending=true; ui.servicePairingState(); assert(display.isOn());
  interfaces.enabled=false; ui.servicePairingState();
  assert(!ui.isPairingPromptActive() && !display.isOn());
  interfaces.enabled=true;
  // ESC dismisses the overlay and wakes the retained screen under button-pairing policy.
  interfaces.pending=true; ui.servicePairingState();
  Keyboard::Event escape{Keyboard::KEY_ESC};
  assert(ui.handlePairingInput(escape) && !ui.isPairingPromptActive());
  assert(ui.current_screen==&main && display.isOn());
  assert(!ui.handlePairingInput(enter));
  // Expiry remains bounded across the 32-bit millis rollover.
  fake_now=0xFFFF0000U; interfaces.pending=true; ui.servicePairingState();
  assert(ui.isPairingPromptActive());
  fake_now+=119999U; ui.servicePairingState(); assert(ui.isPairingPromptActive());
  ++fake_now; ui.servicePairingState(); assert(!ui.isPairingPromptActive());
  // Visibility means the matching conversation is actually readable, including overlays.
  ui.current_screen=&main; main.page=MainScreen::CHAT; ui.alert_expiry=0;
  board.usb=false; power.battery.mode=mesh::ui::DisplayMode::On; ui.serviceDisplayPower();
  assert(ui.isChatVisible());
  ui.showAlert("overlay",1000); assert(!ui.isChatVisible());
  ui.alert_expiry=0; ui.current_screen=&messages; assert(!ui.isChatVisible());
  ui.current_screen=&main; display.turnOff(); assert(!ui.isChatVisible());
  display.turnOn(); main.page=MainScreen::FIRST; assert(!ui.isChatVisible());
  main.page=MainScreen::CHAT;
  interfaces.pending=true; ui.servicePairingState(); assert(!ui.isChatVisible());
  ui.handlePairingInput(escape); assert(ui.isChatVisible());
  // Actual shared controller stops infinite screen overrides on a physical-input action.
  NotificationSink sink(&display); NotificationStore store;
  mesh::notify::Controller notifications(sink,store); the_mesh_cp.notifications=&notifications;
  auto& rules=notifications.settings(); rules.enabled=1;
  auto& rule=rules.rules[0]; rule.used=1; rule.fields=mesh::notify::FScreen;
  rule.screen=1; rule.repeat=0; rule.stop=mesh::notify::Button;
  assert(notifications.message(nullptr,nullptr,false,fake_now));
  assert(notifications.active());
  unsigned button_calls=the_mesh_cp.button_calls;
  assert(ui.handleNotificationInput());
  assert(!notifications.active() && the_mesh_cp.button_calls==button_calls+1);
  assert(!ui.handleNotificationInput());
  // Dismissing a notification during a pairing prompt leaves that modal prompt intact.
  assert(notifications.message(nullptr,nullptr,false,fake_now));
  interfaces.pending=true; ui.servicePairingState();
  assert(ui.handleNotificationInput() && ui.isPairingPromptActive());
  ui.handlePairingInput(escape);
  // Failed display initialization leaves every display control and notification safe.
  ui._display=nullptr;
  auto saved=the_mesh_cp.saves;
  ui.cycleTextSize(); ui.cycleBrightness(); ui.serviceDisplayPower(); ui.notificationScreen(1);
  assert(!ui.turnDisplayOn() && ui.notificationCapabilities()==0 && the_mesh_cp.saves==saved);
  interfaces.pending=true; ui.servicePairingState();
  assert(!ui.isPairingPromptActive() && !interfaces.pending);
  ui.newMsg(0,"peer","headless",7);
  std::puts("PASS: Cardputer live controls, notifications, shared power and night drawing");
}
'''
        self.assertIn("if (handleNotificationInput())", task)
        self.assertIn("else if (!power_off_input && !handlePairingInput(event))", task)
        self.assertIn("if (!handleNotificationInput() && !isPairingPromptActive()", task)
        self.assertIn("tools->handlePowerOffInput(event)", task)
        self.assertIn("current_screen != tools_screen) tools->poll()", task)
        self.assertIn("renderPairingPrompt();\n      _display->endFrame();", task)
        with tempfile.TemporaryDirectory(prefix="cardputer-lifecycle-") as tmp:
            work = Path(tmp)
            (work / "Arduino.h").write_text("#pragma once\nunsigned long millis();\n", encoding="ascii")
            source, binary = work / "test.cpp", work / "test"
            source.write_text(code, encoding="ascii")
            compiled = subprocess.run([
                "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-misleading-indentation",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie",
                "-DCARDPUTER_ADV_UI=1", "-I", str(work), "-I", str(ROOT / "src"),
                str(source), str(ROOT / "src/helpers/ui/DisplayDriver.cpp"), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)

    def test_actual_audio_loop_cleanup_waits_for_drain_and_keeps_visible_codec_warm(self):
        task = (VARIANT / "companion_radio/ui-cardputer/CardputerUITask.cpp").read_text(encoding="ascii")
        speaker = (VARIANT / "hw/CardputerSpeaker.h").read_text(encoding="ascii")
        audio_prefix = function(task, "void CardputerUITask::loop(").split("  CardputerKeyboard *kb", 1)[0]
        code = r'''
#include <cassert>
#include <cstdio>
struct Speaker {
  bool tone_pending=false,draining=false,finish_drain=false,initialized=true;
  unsigned polls=0,sleeps=0,shutdowns=0;
  void processQueue() {
    ++polls;
    if(finish_drain) { draining=false; finish_drain=false; }
  }
  void sleep() {
    ++sleeps;
    if(initialized) { initialized=false; ++shutdowns; }
    tone_pending=draining=false;
  }
'''
        code += function(speaker, "bool isBusy() const") + r'''
};
struct Board {
  Speaker speaker;
  Speaker* getSpeaker() { return &speaker; }
  bool isExternalPowered() const { return false; }
  bool isUsbHostConnected() const { return false; }
};
struct Display {
  bool on=false,desired=false;
  bool isOn() const { return on; }
  bool servicePower(bool,bool,bool) { bool changed=on!=desired; on=desired; return changed; }
};
struct CardputerUITask {
  Board* _board;
  Display* _display;
  unsigned refreshes=0;
  bool hasConnection() const { return false; }
  bool isPairingPromptActive() const { return false; }
  void scheduleRefresh() { ++refreshes; }
  void serviceDisplayPower();
  void servicePairingState() { serviceDisplayPower(); }
  void loop();
};
'''
        code += function(task, "void CardputerUITask::serviceDisplayPower(") + "\n"
        code += audio_prefix + "}\n"
        code += r'''
int main() {
  Board board; Display display;
  CardputerUITask ui{&board,&display};
  auto& audio=board.speaker;
  // A sound-only notification plays while the screen is already dark.
  audio.tone_pending=true;
  ui.loop(); assert(audio.polls==1 && audio.sleeps==0 && audio.initialized); // warmup
  ui.loop(); assert(audio.polls==2 && audio.sleeps==0 && audio.initialized); // transmitting
  audio.tone_pending=false; audio.draining=true;
  ui.loop(); assert(audio.polls==3 && audio.sleeps==0 && audio.initialized); // DMA still draining
  audio.finish_drain=true;
  ui.loop(); assert(audio.polls==4 && audio.sleeps==1 && !audio.initialized && audio.shutdowns==1);
  ui.loop(); assert(audio.shutdowns==1); // repeated idle cleanup performs no further hardware shutdown

  // A display timeout must not cancel audio before the post-processQueue idle check.
  display.on=true; display.desired=false;
  audio.initialized=true; audio.tone_pending=true;
  unsigned sleeps=audio.sleeps;
  ui.loop(); assert(!display.on && audio.tone_pending && audio.sleeps==sleeps && audio.initialized);
  audio.tone_pending=false; audio.draining=true;
  ui.loop(); assert(audio.draining && audio.sleeps==sleeps);
  audio.finish_drain=true;
  ui.loop(); assert(!audio.initialized && audio.sleeps==sleeps+1);

  // Headless sound uses the same complete-playback-then-cleanup lifecycle.
  ui._display=nullptr; audio.initialized=true; audio.tone_pending=true;
  sleeps=audio.sleeps; ui.loop(); assert(audio.sleeps==sleeps && audio.tone_pending);
  audio.tone_pending=false; audio.draining=true;
  ui.loop(); assert(audio.sleeps==sleeps && audio.draining);
  audio.finish_drain=true; ui.loop(); assert(audio.sleeps==sleeps+1 && !audio.initialized);

  // Visible idle operation retains the warmed codec for keyboard tones.
  ui._display=&display; display.on=display.desired=true; audio.initialized=true;
  sleeps=audio.sleeps; ui.loop(); assert(audio.sleeps==sleeps && audio.initialized);
  std::puts("PASS: actual UI audio loop waits for DMA drain, powers down dark/headless idle, and stays warm while visible");
}
'''
        with tempfile.TemporaryDirectory(prefix="cardputer-audio-lifecycle-") as tmp:
            source, binary = Path(tmp) / "test.cpp", Path(tmp) / "test"
            source.write_text(code, encoding="ascii")
            compiled = subprocess.run([
                "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie",
                str(source), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)


if __name__ == "__main__":
    unittest.main()
