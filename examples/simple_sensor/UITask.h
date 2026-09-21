#pragma once

#include <helpers/ui/DisplayDriver.h>
#include <helpers/CommonCLI.h>

class UITask {
  DisplayDriver* _display;
  unsigned long _next_read, _next_refresh;
  int _prevBtnState;
  NodePrefs* _node_prefs;
  char _version_info[32];
  uint32_t _radio_profile_page_started_at = 0;
  bool _dual_radio_enabled_seen = false;

  void resetRadioProfileDisplayPage();
  bool showingSecondaryRadioProfilePage();
  void renderCurrScreen();
public:
  UITask(DisplayDriver& display) : _display(&display) { _next_read = _next_refresh = 0; }
  void begin(NodePrefs* node_prefs, const char* build_date, const char* firmware_version);

  void loop();
};
