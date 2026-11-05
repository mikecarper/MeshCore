#include "helpers/ui/DisplayDriver.h"
#include "helpers/ui/UsbKeyboardOverlay.h"
#include "helpers/esp32/UsbKeyboardDescriptor.h"
#include <assert.h>
#include <stdio.h>
#include <string>
#include <vector>

ColorVal UIColor::window_bkg = 0;
ColorVal UIColor::title_bkg = 0;
ColorVal UIColor::title_txt = 1;
ColorVal UIColor::primary_txt = 1;
ColorVal UIColor::secondary_txt = 1;
ColorVal UIColor::warning_txt = 1;
ColorVal UIColor::popup_bkg = 0;
ColorVal UIColor::popup_txt = 1;
ColorVal UIColor::corp_blue = 1;

using mesh::ui::UsbKeyboardConsole;

struct FakeDisplay {
  int w, h;
  int font_changes = 0;
  std::vector<std::string> text;
  FakeDisplay(int width, int height) : w(width), h(height) {}
  void setTextSize(int) { ++font_changes; }
  int width() const { return w; }
  int height() const { return h; }
  int textLineHeight() { return 10; }
  void setColor(ColorVal) {}
  void fillRect(int, int y, int, int height) { assert(y >= 0 && y + height <= h); }
  void drawRect(int, int, int, int) {}
  void drawTextEllipsized(int, int, int, const char* s) { text.push_back(s); }
  void drawTextLeftAlign(int, int, const char* s) { text.push_back(s); }
  int getTextWidth(const char* s) { return static_cast<int>(strlen(s)) * 6; }
};

static UsbKeyboardConsole::Action press(UsbKeyboardConsole& c, uint8_t key, uint8_t mod = 0) {
  const uint8_t down[8] = {mod, 0, key, 0, 0, 0, 0, 0};
  const uint8_t up[8] = {};
  auto action = c.report(down, sizeof(down));
  c.report(up, sizeof(up));
  return action;
}

int main() {
  // A composite mouse/keyboard must select the keyboard's own IN endpoint.
  uint8_t descriptor[] = {
      9, 2, 41, 0, 2, 1, 0, 0x80, 50,
      9, 4, 0, 0, 1, 3, 1, 2, 0,
      7, 5, 0x81, 3, 8, 0, 10,
      9, 4, 2, 0, 1, 3, 1, 1, 0,
      7, 5, 0x82, 3, 16, 0, 10,
  };
  mesh::usbkeyboard::BootEndpoint ep;
  assert(mesh::usbkeyboard::findBootKeyboard(descriptor, sizeof(descriptor), ep));
  assert(ep.interface_number == 2 && ep.endpoint == 0x82 && ep.packet_size == 16);
  descriptor[25] = 0;  // A zero-length descriptor must not loop or read past the buffer.
  assert(!mesh::usbkeyboard::findBootKeyboard(descriptor, sizeof(descriptor), ep));
  assert(!mesh::usbkeyboard::findBootKeyboard(nullptr, sizeof(descriptor), ep));

  UsbKeyboardConsole c;
  assert(press(c, 0x04) == UsbKeyboardConsole::Action::None);
  c.connect();
  for (uint8_t k : {0x0a, 0x08, 0x17, 0x2c, 0x17, 0x1b}) press(c, k);
  assert(std::string(c.command()) == "get tx");
  assert(press(c, 0x28) == UsbKeyboardConsole::Action::Submit);
  assert(c.visible());
  assert(press(c, 0x29) == UsbKeyboardConsole::Action::Changed);
  assert(!c.visible() && std::string(c.command()) == "get tx");
  assert(press(c, 0x04) == UsbKeyboardConsole::Action::None);
  assert(press(c, 0x28) == UsbKeyboardConsole::Action::None);
  assert(std::string(c.command()) == "get tx");
  FakeDisplay hidden{320, 170};
  mesh::ui::drawUsbKeyboardOverlay(hidden, c);
  assert(hidden.text.empty() && hidden.font_changes == 0);
  // A combined Escape+Enter report must not submit when reopening.
  const uint8_t reopen[8] = {0, 0, 0x28, 0x29, 0, 0, 0, 0};
  assert(c.report(reopen, sizeof(reopen)) == UsbKeyboardConsole::Action::Changed);
  assert(c.visible() && std::string(c.command()) == "get tx");
  assert(c.report(reopen, sizeof(reopen)) == UsbKeyboardConsole::Action::None);
  const uint8_t release[8] = {};
  c.report(release, sizeof(release));
  // While the display is off or covered, Enter only updates held-key state;
  // it cannot submit immediately when the box becomes visible again.
  const uint8_t held_enter[8] = {0, 0, 0x28, 0, 0, 0, 0, 0};
  c.ignoreReport(held_enter, sizeof(held_enter));
  assert(c.report(held_enter, sizeof(held_enter)) == UsbKeyboardConsole::Action::None);
  c.report(release, sizeof(release));
  // If the blocked report is HID rollover, no key can be trusted until a
  // complete release report arrives.
  const uint8_t blocked_rollover[8] = {0, 0, 1, 1, 1, 1, 1, 1};
  c.ignoreReport(blocked_rollover, sizeof(blocked_rollover));
  assert(c.report(held_enter, sizeof(held_enter)) == UsbKeyboardConsole::Action::None);
  assert(c.report(held_enter, sizeof(held_enter)) == UsbKeyboardConsole::Action::None);
  c.report(release, sizeof(release));
  assert(c.report(blocked_rollover, sizeof(blocked_rollover)) == UsbKeyboardConsole::Action::None);
  assert(c.report(held_enter, sizeof(held_enter)) == UsbKeyboardConsole::Action::None);
  c.report(release, sizeof(release));
  assert(press(c, 0x28) == UsbKeyboardConsole::Action::Submit);
  c.clearCommand();
  press(c, 0x04, 0x02);  // Shift-A
  press(c, 0x39);        // Caps Lock
  press(c, 0x05);        // B
  press(c, 0x06, 0x02);  // shift + Caps = c
  assert(std::string(c.command()) == "ABc");
  press(c, 0x50); press(c, 0x2a);  // left, backspace
  assert(std::string(c.command()) == "Ac");
  press(c, 0x4c);  // delete
  assert(std::string(c.command()) == "A");
  press(c, 0x18, 0x01);  // Ctrl-U
  assert(c.length() == 0);

  const uint8_t down[8] = {0, 0, 4, 0, 0, 0, 0, 0};
  const uint8_t rollover[8] = {0, 0, 1, 1, 1, 1, 1, 1};
  c.report(down, sizeof(down));
  c.report(down, sizeof(down));  // Held key must not type twice.
  c.report(rollover, sizeof(rollover));
  assert(c.length() == 1);
  c.clearCommand();
  c.disconnect(); c.connect();
  assert(c.length() == 0 && c.visible());

  c.append("first\nsecond\n");
  assert(std::string(c.lineFromNewest(1)) == "second");
  assert(mesh::ui::usbKeyboardSecretStart("login secret") == 6);
  assert(mesh::ui::usbKeyboardSecretStart("set wifi.pwd secret") == 4);
  assert(mesh::ui::usbKeyboardSecretStart("password secret") == 9);
  assert(mesh::ui::usbKeyboardSecretStart("get radio") == 9);
  c.clearCommand();
  for (uint8_t k : {0x0f, 0x12, 0x0a, 0x0c, 0x11, 0x2c, 0x16, 0x08, 0x06, 0x15, 0x08, 0x17}) press(c, k);
  c.append("third\nfourth\n");
  FakeDisplay big{320, 170};
  mesh::ui::drawUsbKeyboardOverlay(big, c);
  for (const auto& s : big.text) {
    assert(s.find("SECRET") == std::string::npos);
    assert(s.find("secret") == std::string::npos);
  }
  assert(!big.text.empty());
  FakeDisplay small{128, 64};
  mesh::ui::drawUsbKeyboardOverlay(small, c);
  assert(small.text.size() >= 6);  // Four history rows plus prompt and editor.
  FakeDisplay too_short{128, 48};
  mesh::ui::drawUsbKeyboardOverlay(too_short, c);
  assert(too_short.text.empty());
  c.disconnect();
  assert(!c.visible());
  FakeDisplay detached{320, 170};
  mesh::ui::drawUsbKeyboardOverlay(detached, c);
  assert(detached.text.empty());
  assert(detached.font_changes == 0);
  puts("USB keyboard console: OK");
}
