#pragma once

#include "CardputerAdvBoard.h"
#include "CardputerDataStore.h"
#include "KeyLock.h"
#include "screen/CardputerScreen.h"
#include "types.h"

#include <AbstractUITask.h>
#include <Arduino.h>
#include <MeshCore.h>
#include <helpers/MultiSerialInterface.h>
#include <helpers/ContactInfo.h>
#include <helpers/SensorManager.h>
#include <helpers/sensors/LPPDataHelpers.h>
#include <helpers/ui/MomentaryButton.h>

#define LONG_PRESS_MILLIS 1200

class CardputerUITask : public AbstractUITask {
  CardputerDisplay *_display;
  SensorManager *_sensors;
  CardputerAdvBoard *_board;
  CompanionNodePrefs *_node_prefs;
  CustomNodePrefs *_custom_prefs;
  unsigned long next_refresh = 0;
  unsigned long auto_off_time;
  bool sleep_enabled = false;
  char alert_text[80];
  unsigned long alert_expiry = 0;
  int unsynced_msg_count = 0;
  unsigned long ui_started_at = 0;
  unsigned long next_batt_chck = 0;
  unsigned long ping_deadline = 0;  // 0 = no Fn+Z ping awaiting a reply
  KeyLock _keylock;

  CardputerScreen *splash_screen;
  CardputerScreen *main_screen;
  CardputerScreen *new_message_screen;
  CardputerScreen *settings_screen;
  CardputerScreen *tools_screen;
  CardputerScreen *current_screen = nullptr;

  // Top button (G0): click sends a flood advert, like the stock Cardputer build.
  MomentaryButton _top_btn;

  // Fn+letter shortcuts; returns true when the key was consumed.
  bool handleShortcut(const Keyboard::Event &e);
  void renderAlert();
  void sendFloodAdvert();
  void cycleBrightness();
  void showPresetAlert(uint8_t slot);
  void pingLastRepeater();
  void openNewestUnread();

  // Returns false if display is already on
  bool turnDisplayOn();
  void setCurrScreen(CardputerScreen *c);

public:
  CardputerUITask(CardputerAdvBoard *board, MultiSerialInterface *serial)
      : AbstractUITask(board, serial), _display(NULL), _sensors(NULL), _board(board),
        _top_btn(PIN_TOP_BUTTON, 1000, true, true) {}

  void begin(DisplayDriver *display, SensorManager *sensors, CompanionNodePrefs *node_prefs,
             CustomNodePrefs *custom_prefs);

  void gotoMainScreen() { setCurrScreen(main_screen); }
  void gotoSettingsScreen() { setCurrScreen(settings_screen); }
  void gotoToolsScreen() { setCurrScreen(tools_screen); }
  // Draws FN / CAPS badges for latched modifiers; returns the next free x.
  int drawModifierBadges(CardputerDisplay &lcd, int x, int y);
  void showAlert(const char *text, int duration_millis);
  void dismissAlert();
  bool isAlertActive();
  inline int getMsgCount() const { return unsynced_msg_count; }
  inline bool hasDisplay() const { return _display != NULL; }
  inline bool isBuzzerQuiet() { return _node_prefs->buzzer_quiet; }
  inline const char* getNodeName() { return _node_prefs->node_name; }
  inline uint16_t getBattMilliVoltsCorrected() { return (float)getBattMilliVolts() * _custom_prefs->battery_correction; }
  void toggleBuzzer();
  void toggleBluetooth();
  void cycleTextSize();
  void togglePowerSave();
  bool getGPSState();
  void toggleGPS();
  inline unsigned long getAutoOffTime() const { return auto_off_time; }
  inline bool powerSaveEnabled() const { return _node_prefs->powersaving_enabled; }
  // Light sleep stops the backlight PWM and I2S audio; keep the CPU awake
  // while the screen is on or a tone is queued.
  bool isAwake() { return (_display && _display->isOn()) || _board->getSpeaker()->isBusy(); }
  void msgRead(int msgcount) override;
  void newMsg(uint8_t path_len, const char *from_name, const char *text, int msgcount, int channel_idx = -1,
              const char *channel_name = nullptr, int queue_index = -1) override;

  void onChannelMessageRecv(const mesh::GroupChannel &channel, const char *text);
  void onContactMessageRecv(const ContactInfo &contact, const char *text);
  void onMessageSendAttempt(uint8_t attempt, uint8_t total, MessageSendState state);

  void pingRecv(float snr_tx, float snr_rx);
  void discoverRecv(const mesh::Identity &id, float snr);
  void messageRepeatsRecv(uint16_t count);
  void notify(UIEventType t = UIEventType::none) override;
  void playSound(SoundType t);
  void loop() override;
  void shutdown(bool restart = false);
  CardputerAdvBoard *getBoard() { return _board; }
  inline void extendAutoOff() { auto_off_time = millis() + AUTO_OFF_MILLIS; }
  inline void scheduleRefresh(unsigned long ms = 0) { next_refresh = millis() + ms; }
};
