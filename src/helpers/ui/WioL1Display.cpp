#include "WioL1Display.h"
#include "WioL1DisplayProbe.h"
#include <new>

extern const char meshcore_capability_display_wio_l1_unified[];
const char meshcore_capability_display_wio_l1_unified[] =
    "meshcore.capability.display.wio_l1_unified.v1";
extern const char meshcore_capability_display_autodetect_wio_l1[];
const char meshcore_capability_display_autodetect_wio_l1[] =
    "meshcore.capability.display.autodetect.wio_l1.v1";

WioL1Display::~WioL1Display() {
  if (_constructed == Type::Oled) _storage.oled.~SH1106Display();
  else if (_constructed == Type::Eink) _storage.eink.~GxEPDDisplay();
}

WioL1Display::Type WioL1Display::detect() {
  if (_detected) return _type;
  _detected = true;
  const volatile char* capability = meshcore_capability_display_autodetect_wio_l1;
  (void)*capability;
  const uint8_t addresses[] = {DISPLAY_ADDRESS, DISPLAY_ADDRESS ^ 1};
  Wire.end();
  for (uint8_t address : addresses) {
    if (mesh::ui::wio_l1::probeOled(address)) {
      _type = Type::Oled;
      Wire.begin();
      return _type;
    }
  }
  Wire.begin();
  if (mesh::ui::wio_l1::probeEink()) _type = Type::Eink;
  return _type;
}

bool WioL1Display::begin() {
  if (_started) return _active != nullptr;
  _started = true;
  const volatile char* capability = meshcore_capability_display_wio_l1_unified;
  (void)*capability;
  detect();
  bool ready = false;
  if (_type == Type::Oled) {
    _constructed = Type::Oled;
    auto* oled = new (&_storage.oled) SH1106Display();
    ready = oled->begin();
    if (ready) _active = oled;
  } else if (_type == Type::Eink) {
    _constructed = Type::Eink;
    auto* eink = new (&_storage.eink) GxEPDDisplay();
    ready = eink->begin();
    if (ready) {
      _active = eink;
      eink->forceFullRefresh();
    }
  }
  if (!ready) {
    _type = Type::None;
    return false;
  }
  const ColorVal background = isEink() ? 0xFFFF : 0;
  const ColorVal foreground = isEink() ? 0 : 1;
  UIColor::window_bkg = UIColor::title_bkg = UIColor::popup_bkg = background;
  UIColor::title_txt = UIColor::primary_txt = UIColor::secondary_txt =
      UIColor::warning_txt = UIColor::popup_txt = UIColor::corp_blue = foreground;
  setDimensions(_active->width(), _active->height());
  return true;
}
