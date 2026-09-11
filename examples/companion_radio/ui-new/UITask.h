#pragma once

#include <MeshCore.h>
#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/UIScreen.h>
#include <helpers/SensorManager.h>
#include <helpers/MultiSerialInterface.h>
#include <Arduino.h>
#include <helpers/sensors/LPPDataHelpers.h>
#ifdef HAS_TOUCH
  #include <helpers/ui/TouchInput.h>
#endif

#ifndef LED_STATE_ON
  #define LED_STATE_ON 1
#endif

#ifdef PIN_BUZZER
  #include <helpers/ui/buzzer.h>
#endif
#ifdef PIN_VIBRATION
  #include <helpers/ui/GenericVibration.h>
#endif

#include "../AbstractUITask.h"
#include "../NodePrefs.h"
#include <helpers/CompanionJohnConfig.h>

class UITask : public AbstractUITask {
  DisplayDriver* _display;
  SensorManager* _sensors;
#ifdef PIN_BUZZER
  genericBuzzer buzzer;
#endif
#ifdef PIN_VIBRATION
  GenericVibration vibration;
#endif
  unsigned long _next_refresh;
  unsigned long _pairing_screen_until;
  bool _deferred_msg_preview;
  CompanionNodePrefs* _node_prefs;
  char _alert[80];
  unsigned long _alert_expiry;
  int _msgcount;
  unsigned long ui_started_at, next_batt_chck;
  int next_backlight_btn_check = 0;
#ifdef HAS_TOUCH
  #ifndef TOUCH_CENTER_ZONE_PERCENT
    #define TOUCH_CENTER_ZONE_PERCENT 34
  #endif
  #ifdef TOUCH_REVERSE_SWIPE
  static constexpr bool TOUCH_REVERSE_SWIPE_ENABLED = true;
  #else
  static constexpr bool TOUCH_REVERSE_SWIPE_ENABLED = false;
  #endif
  #ifdef TOUCH_SEPARATE_VERTICAL_SWIPES
  static constexpr bool TOUCH_SEPARATE_VERTICAL_SWIPES_ENABLED = true;
  #else
  static constexpr bool TOUCH_SEPARATE_VERTICAL_SWIPES_ENABLED = false;
  #endif
  #ifdef TOUCH_MIRROR_TAP_X
  static constexpr bool TOUCH_MIRROR_TAP_X_ENABLED = true;
  #else
  static constexpr bool TOUCH_MIRROR_TAP_X_ENABLED = false;
  #endif
  #ifndef TOUCH_REVERSE_VERTICAL_SWIPE
    #define TOUCH_REVERSE_VERTICAL_SWIPE TOUCH_REVERSE_SWIPE_ENABLED
  #endif
  mesh::ui::TouchInput touch_input{
      TOUCH_REVERSE_SWIPE_ENABLED,
      TOUCH_SEPARATE_VERTICAL_SWIPES_ENABLED,
      TOUCH_CENTER_ZONE_PERCENT,
      TOUCH_MIRROR_TAP_X_ENABLED,
      TOUCH_REVERSE_VERTICAL_SWIPE != 0};
  unsigned long next_touch_check = 0;
  void getTouchControls(mesh::ui::TouchSplitSelector& transport,
      const mesh::ui::TouchSplitSelector*& split,
      const mesh::ui::TouchNavigationBar*& reader);
  bool _touch_debug_enabled = false;  // Diagnostic only; reset at every boot.
  int _touch_debug_x = -1, _touch_debug_y = -1, _touch_debug_area = -1;
  UIScreen* _touch_debug_screen = nullptr;
#endif
#ifdef PIN_STATUS_LED
  int led_state = 0;
  int next_led_change = 0;
  int last_led_increment = 0;
#endif

#ifdef PIN_USER_BTN_ANA
  unsigned long _analogue_pin_read_millis = millis();
#endif

  UIScreen* splash;
  UIScreen* home;
  UIScreen* msg_preview = nullptr;
  UIScreen* curr;
#if COMPANION_FEATURE_JOHN
  UIScreen* john_reader = nullptr;
#endif

  void userLedHandler();

  // Button action handlers
  char checkDisplayOn(char c);
  char handleLongPress(char c);
  char handleDoubleClick(char c);
  char handleMultiClick(char c, bool backwards);
  bool isButtonGesturePending() const;

  void setCurrScreen(UIScreen* c);
  bool isPairingScreenActive() const;
  void renderPairingBanner();
  void showPairingPin();
  void finishPairingScreen(bool timed_out);

public:

  UITask(mesh::MainBoard* board, MultiSerialInterface* serial) : AbstractUITask(board, serial), _display(NULL), _sensors(NULL) {
    next_batt_chck = _next_refresh = 0;
    _pairing_screen_until = 0;
    _deferred_msg_preview = false;
    _msgcount = 0;
    ui_started_at = 0;
    curr = NULL;
  }
  void begin(DisplayDriver* display, SensorManager* sensors, CompanionNodePrefs* node_prefs);
  void serviceWiFiToggleButton();
  void servicePairingState() override;
  bool isPairingPromptActive() const override {
    return isPairingScreenActive();
  }

  void gotoHomeScreen() { setCurrScreen(home); }
  void showMessages();
#if COMPANION_FEATURE_JOHN
  bool isJohnReaderActive() const { return john_reader && curr == john_reader; }
  void showJohnReader();
  void closeJohnReader();
#endif
  void showAlert(const char* text, int duration_millis);
  int  getMsgCount() const { return _msgcount; }
  int getPreviewCount() const;
  void renderMessageSummary(DisplayDriver& display) const;
  bool hasDisplay() const { return _display != NULL; }
#ifdef HAS_TOUCH
  bool supportsTouchDebug() const override { return _display != nullptr; }
  bool isTouchDebugEnabled() const override { return _touch_debug_enabled; }
  bool setTouchDebugEnabled(bool enabled) override {
    if (!supportsTouchDebug()) return false;
    _touch_debug_enabled = enabled;
    _touch_debug_x = _touch_debug_y = _touch_debug_area = -1;
    _touch_debug_screen = nullptr;
    _next_refresh = 0;  // Also removes already-drawn outlines when disabling.
    if (enabled) checkDisplayOn(0);  // Wake without selecting or navigating.
    return true;
  }
#endif
  bool supportsDisplayRotation() const override {
    return _display != NULL && _display->supportsRotation();
  }
  bool setDisplayRotationDegrees(uint16_t degrees) override {
    return _display != NULL && _display->setRotationDegrees(degrees);
  }
  bool isButtonPressed() const;

  bool isBuzzerQuiet() { 
#ifdef PIN_BUZZER
    return buzzer.isQuiet();
#else
    return true;
#endif
  }

  void toggleBuzzer();
  bool getGPSState();
  void toggleGPS();


  // from AbstractUITask
  void msgRead(int msgcount) override;
  void syncMessageQueue(int msgcount, int removed_index = -1) override;
  bool supportsInboxModes() const override { return _display != nullptr; }
  void inboxModeChanged() override { _next_refresh = 0; }
  const char* inboxTitle() const;
  void newMsg(uint8_t path_len, const char* from_name, const char* text,
              int msgcount, int channel_idx = -1,
              const char* channel_name = nullptr,
              int queue_index = -1) override;
  void notify(UIEventType t = UIEventType::none) override;
  void loop() override;

  void shutdown(bool restart = false);
};
