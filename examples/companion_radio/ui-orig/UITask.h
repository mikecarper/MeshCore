#pragma once

#include <MeshCore.h>
#include <helpers/ui/DisplayDriver.h>
#include <helpers/SensorManager.h>
#include <stddef.h>

#ifdef PIN_BUZZER
  #include <helpers/ui/buzzer.h>
#endif

#include "../AbstractUITask.h"
#include "../NodePrefs.h"

#include "Button.h"

#ifdef HAS_DRV2605
  #include <helpers/ui/DRV2605Vibration.h>
#elif defined(PIN_VIBRATION)
  #include <helpers/ui/GenericVibration.h>
#endif

class UITask : public AbstractUITask {
  DisplayDriver* _display;
  SensorManager* _sensors;
#ifdef PIN_BUZZER
  genericBuzzer buzzer;
#endif
#ifdef HAS_DRV2605
  DRV2605Vibration vibration;
#elif defined(PIN_VIBRATION)
  GenericVibration vibration;
#endif
  uint32_t _next_refresh;
  CompanionNodePrefs* _node_prefs;
  char _version_info[32];
  char _origin[62];
#if UI_SMALL_MESSAGE_FONT == 1
  char _msg[161];
#else
  char _msg[80];
#endif
  char _alert[80];
  int _msgcount;
  bool _need_refresh = true;
  bool _displayWasOn = false;  // Track display state before button press
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  // ANY_PRESS runs once per click: keep the first acknowledged notification
  // until the eventual single/multi-click handler consumes the sequence.
  bool _button_notification_cleared = false;
  bool _shutdown_pending = false;
  bool _shutdown_restart = false;
  uint32_t _shutdown_started_at = 0;
#endif
  unsigned long _pairing_screen_until;
  unsigned long ui_started_at;
  uint32_t _radio_profile_page_started_at = 0;
  bool _dual_radio_enabled_seen = false;
  bool _startup_complete = false;
  uint8_t _radio_profile_display_page = 0;

  // Button handlers
#ifdef PIN_USER_BTN
  Button* _userButton = nullptr;
#endif
#ifdef PIN_USER_BTN_ANA
  Button* _userButtonAnalog = nullptr;
#endif

  void renderCurrScreen();
  void userLedHandler();
  void renderBatteryIndicator(uint16_t batteryMilliVolts);
  
  // Button action handlers
  void handleButtonAnyPress();
  void handleButtonShortPress();
  void handleButtonDoublePress();
  void handleButtonTriplePress();
  void handleButtonQuadruplePress();
  void handleButtonLongPress();
#if defined(PIN_BUZZER) && MESH_GPS_VOICE
  void servicePendingShutdown();
#endif
  bool shouldPlayMessageTone() const;
  bool isPairingScreenActive() const;
  void resetRadioProfileDisplayPage();
  bool showingSecondaryRadioProfilePage();
  void serviceRadioProfileDisplayPage();
  void showPairingPin();
  void finishPairingScreen(bool timed_out);

 
public:

  UITask(mesh::MainBoard* board, MultiSerialInterface* serial) : AbstractUITask(board, serial), _display(NULL), _sensors(NULL) {
      _next_refresh = 0;
      _pairing_screen_until = 0;
      ui_started_at = 0;
  }
  void begin(DisplayDriver* display, SensorManager* sensors, CompanionNodePrefs* node_prefs);

  bool hasDisplay() const { return _display != NULL; }
  bool supportsDisplayRotation() const override {
    return _display != NULL && _display->supportsRotation();
  }
  bool setDisplayRotationDegrees(uint16_t degrees) override {
    if (_display == NULL || !_display->setRotationDegrees(degrees)) return false;
    _next_refresh = millis();
    return true;
  }
  void clearMsgPreview();
  void servicePairingState() override;
  bool isPairingPromptActive() const override {
    return isPairingScreenActive();
  }

  // from AbstractUITask
  void msgRead(int msgcount) override;
  void newMsg(uint8_t path_len, const char* from_name, const char* text,
              int msgcount, int channel_idx = -1,
              const char* channel_name = nullptr,
              int queue_index = -1) override;
  void notify(UIEventType t = UIEventType::none) override;
  uint8_t notificationCapabilities() const override;
  void notificationVibration(bool on) override;
  void notificationMelody(const char* text) override;
  void notificationScreen(int8_t mode) override;
  void loop() override;
#ifdef MESH_BUTTON_AUDIO_HIL
  bool handleButtonAudioTest(const char* command, char* reply, size_t size) override;
#endif

  void shutdown(bool restart = false);
};
