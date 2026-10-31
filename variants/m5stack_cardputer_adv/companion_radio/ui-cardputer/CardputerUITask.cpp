#include "CardputerUITask.h"

#include "screen/MainScreen.h"
#include "screen/NewMessageScreen.h"
#include "screen/SettingsScreen.h"
#include "screen/SplashScreen.h"
#include "screen/ToolsScreen.h"
#include "target.h"
#include "helpers.h"
#include "CompanionBluetooth.h"
#include <helpers/CompanionNotificationPolicy.h>
#include <helpers/ui/BluetoothPairingUiPolicy.h>

#include <helpers/RadioPresetConfig.h>
#if MESH_ENABLE_RADIO_PRESETS
#include <helpers/RadioPresets.h>
#endif

void CardputerUITask::begin(DisplayDriver *display, SensorManager *sensors, CompanionNodePrefs *node_prefs,
                            CustomNodePrefs *custom_prefs) {
  _display = static_cast<CardputerDisplay *>(display);
  _sensors = sensors;

  _node_prefs = node_prefs;
  _custom_prefs = custom_prefs;

  if (_display != NULL) {
    _display->setBrightness(_custom_prefs->brightness);
    _display->setTextLevel(_custom_prefs->text_level);
    serviceDisplayPower();
  }
  _top_btn.begin();

  ui_started_at = millis();

  splash_screen = new SplashScreen(this);
  main_screen = new MainScreen(this, &rtc_clock, sensors, node_prefs, custom_prefs);
  settings_screen = new SettingsScreen(this, &rtc_clock, node_prefs, custom_prefs, _board);
  new_message_screen = new NewMessageScreen(this);
  tools_screen = new ToolsScreen(this, &rtc_clock);
  setCurrScreen(splash_screen);
}

void CardputerUITask::showAlert(const char *text, int duration_millis) {
  snprintf(alert_text, sizeof(alert_text), "%s", text);
  alert_expiry = millis() + duration_millis;
  scheduleRefresh();
}

void CardputerUITask::dismissAlert() {
  alert_expiry = 0;
  scheduleRefresh();
}

bool CardputerUITask::isAlertActive() const {
  return millis() < alert_expiry;
}

bool CardputerUITask::isChatVisible() const {
  return _display && _display->isOn() && current_screen == main_screen
      && static_cast<MainScreen *>(main_screen)->getCurrentPage() == MainScreen::CHAT
      && !isPairingPromptActive() && !isAlertActive();
}

void CardputerUITask::notify(UIEventType t) {
  if ((_notification_outputs & mesh::notify::Sound) && !_message_notification_override
      && (t == UIEventType::channelMessage || t == UIEventType::contactMessage)) {
    playSound(SoundType::NewMessage);
  }
}

void CardputerUITask::playSound(SoundType t) {
  if (_node_prefs->buzzer_quiet) {
    return;
  }

  switch (t) {
    case SoundType::Keyboard:
      _board->getSpeaker()->queueTone(4000, 50, 0.2f);
      break;
    case SoundType::NewMessage:
      _board->getSpeaker()->queueTone(3000, 100);
      break;
    case SoundType::DiscoveryResult:
    case SoundType::MessageAck:
      _board->getSpeaker()->queueTone(800, 70, 0.3f);
      break;
  }
}

void CardputerUITask::msgRead(int msgcount) {
  unsynced_msg_count = msgcount;
  if (msgcount == 0) {
    gotoMainScreen();
  }
}

void CardputerUITask::newMsg(uint8_t path_len, const char *from_name, const char *text, int msgcount,
                             int channel_idx, const char *channel_name, int queue_index) {
  unsynced_msg_count = msgcount;
  const bool on_first_page = current_screen == main_screen
      && static_cast<MainScreen *>(main_screen)->getCurrentPage() == MainScreen::FIRST;
  const bool display_off = _display && !_display->isOn();

  // Keep a connected client's screen stable; managed notifications own wake.
  const bool show_preview = (_notification_outputs & mesh::notify::Screen)
      && !_message_notification_screen_override && !hasConnection() && !isPairingPromptActive();
  if (show_preview && (display_off || current_screen == new_message_screen || on_first_page)) {
    static_cast<NewMessageScreen *>(new_message_screen)->newMessage(from_name, text);
    setCurrScreen(new_message_screen);
  }
  if (_display && shouldWakeDisplayForMessage()) {
    _display->wake(mesh::ui::DisplayWake::Message);
  }
  scheduleRefresh();
}

uint8_t CardputerUITask::notificationCapabilities() const {
  // The codec's fixed tones do not implement custom RTTTL notification rules.
  return _display ? mesh::notify::Screen : 0;
}

void CardputerUITask::notificationScreen(int8_t mode) {
  if (!_display) return;
  _display->notificationPower(mode);
  scheduleRefresh();
}

void CardputerUITask::serviceDisplayPower() {
  if (!_display) return;
  if (_display->servicePower(_board->isExternalPowered() || _board->isUsbHostConnected(),
                             hasConnection(), isPairingPromptActive())) {
    scheduleRefresh();
    if (!_display->isOn() && !_board->getSpeaker()->isBusy()) _board->getSpeaker()->sleep();
  }
}

bool CardputerUITask::isPairingPromptActive() const {
  return _display && mesh::ui::shouldDisplayBluetoothPairingPin(
      isBluetoothEnabled(), hasBluetoothConnection(), the_mesh_cp.getBLEPin())
      && mesh::ui::isBluetoothPairingPromptActive(isBluetoothEnabled(), hasBluetoothConnection(),
          pairing_prompt_until, static_cast<uint32_t>(millis()));
}

void CardputerUITask::servicePairingState() {
  const uint32_t now = static_cast<uint32_t>(millis());
  if (_interfaceManager->takePairingRequest() && _display
      && mesh::ui::shouldDisplayBluetoothPairingPin(
          isBluetoothEnabled(), hasBluetoothConnection(), the_mesh_cp.getBLEPin())) {
    pairing_prompt_until = now + 120000U;
    if (pairing_prompt_until == 0) pairing_prompt_until = 1;
    scheduleRefresh();
  }
  if (pairing_prompt_until != 0 && !isPairingPromptActive()) {
    pairing_prompt_until = 0;
    scheduleRefresh();
  }
  serviceDisplayPower();
}

bool CardputerUITask::handlePairingInput(const Keyboard::Event &event) {
  if (!isPairingPromptActive()) return false;
  if (event.key == Keyboard::KEY_ESC) {
    pairing_prompt_until = 0;
    serviceDisplayPower();
    turnDisplayOn();
  }
  return true;
}

bool CardputerUITask::handleNotificationInput() {
  if (!the_mesh_cp.notificationButton()) return false;
  serviceDisplayPower();
  scheduleRefresh();
  return true;
}

void CardputerUITask::renderPairingPrompt() {
  if (!_display || !_display->isOn() || !isPairingPromptActive()) return;
  char pin[16];
  snprintf(pin, sizeof(pin), "%06u", static_cast<unsigned>(the_mesh_cp.getBLEPin()));
  _display->setColor(CardputerDisplay::P_BLACK);
  _display->fillRect(0, 0, _display->width(), _display->height());
  _display->setColor(CardputerDisplay::P_WHITE);
  _display->setTextSize(1);
  _display->drawTextCentered(_display->width() / 2, 22, "Bluetooth pairing PIN");
  _display->setTextSize(2);
  _display->drawTextCentered(_display->width() / 2, 52, pin);
  _display->setTextSize(1);
  _display->drawTextCentered(_display->width() / 2, 94, "Enter PIN on phone");
}

void CardputerUITask::onChannelMessageRecv(uint8_t channel_idx, const uint8_t history_id[32], const char *text) {
  static_cast<MainScreen *>(main_screen)->onChannelMessageRecv(channel_idx, history_id, text);
}

void CardputerUITask::onChannelMessageSent(uint8_t channel_idx, const uint8_t history_id[32], const char *text) {
  static_cast<MainScreen *>(main_screen)->onChannelMessageSent(channel_idx, history_id, text);
  scheduleRefresh();
}

void CardputerUITask::onContactMessageSent(const ContactInfo &contact, const char *text) {
  static_cast<MainScreen *>(main_screen)->onContactMessageSent(contact, text);
  scheduleRefresh();
}

void CardputerUITask::onContactMessageRecv(const ContactInfo &contact, const char *text) {
  ((MainScreen *)main_screen)->onContactMessageRecv(contact, text);
}

void CardputerUITask::onMessageSendAttempt(uint8_t attempt, uint8_t total, MessageSendState state) {
  if (current_screen == main_screen) {
    static_cast<MainScreen *>(main_screen)->onMessageSendAttempt(attempt, total, state);
  }
}

void CardputerUITask::pingRecv(float snr_tx, float snr_rx) {
  ping_deadline = 0;
  char buf[40];
  snprintf(buf, sizeof(buf), "SNR there/back: %.2f/%.2f", snr_tx, snr_rx);
  showAlert(buf, 4000);
}

void CardputerUITask::discoverRecv(const mesh::Identity &id, float snr) {
  if (current_screen == tools_screen) {
    static_cast<ToolsScreen *>(tools_screen)->discoverRecv(id, snr);
  }
}

void CardputerUITask::messageRepeatsRecv(uint16_t count) {
  if (current_screen == main_screen) {
    static_cast<MainScreen *>(main_screen)->messageRepeatsRecv(count);
  }
}

void CardputerUITask::setCurrScreen(CardputerScreen *c) {
  current_screen = c;
  scheduleRefresh();
}

/*
  hardware-agnostic pre-shutdown activity should be done here
*/
void CardputerUITask::shutdown(bool restart) {
  if (!prepareForShutdown()) {
    showAlert("Shutdown blocked: storage not ready", 2000);
    return;
  }
  if (restart) {
    _board->reboot();
  } else {
    if (_display) _display->turnOff();
    radio_driver.powerOff();
    _board->powerOff();
  }
}

void CardputerUITask::loop() {
  servicePairingState();
  _board->getSpeaker()->processQueue();
  if ((!_display || !_display->isOn()) && !_board->getSpeaker()->isBusy()) {
    _board->getSpeaker()->sleep();
  }

  CardputerKeyboard *kb = _board->getKeyboard();
  const Keyboard::Modifiers latched = kb->getModifiers();
  auto event = kb->poll();

  ToolsScreen *tools = static_cast<ToolsScreen *>(tools_screen);
  const bool power_off_input = tools && tools->handlePowerOffInput(event);
  if (event.changed && event.down) {
    if (handleNotificationInput()) {
      kb->setModifiers(latched);  // dismissal must not also arm a shortcut
    } else if (!power_off_input && !handlePairingInput(event)) {
      if (turnDisplayOn()) {
        kb->setModifiers(latched);  // a wake-up press must not flip Shift/Fn
      } else {
        playSound(SoundType::Keyboard);
        if (_keylock.filter(event)) {
          // locked: only Fn+Q gets through
        } else if (!handleShortcut(event) && current_screen) {
          current_screen->handleInput(event);
        }
      }
    }
  }

  if (ping_deadline != 0 && (long)(millis() - ping_deadline) >= 0) {
    ping_deadline = 0;
    showAlert("No answer", 3000);
  }

  if (_top_btn.check() == BUTTON_EVENT_CLICK) {
    if (!handleNotificationInput() && !isPairingPromptActive()
        && !(tools && tools->isPowerOffPending())) {
      turnDisplayOn();
      sendFloodAdvert();
    }
  }
  // A received-message preview must not suspend an armed release/timeout.
  if (tools && current_screen != tools_screen) tools->poll();

  if (current_screen) {
    current_screen->poll();
  }

  if (_display != NULL && _display->isOn()) {
    if (millis() >= next_refresh && current_screen) {
      _display->startFrame();
      int delay_millis = current_screen->render(*_display);
      if (current_screen != main_screen) {  // main screen shows them in its status bar
        drawModifierBadges(*_display, 3, 0);
      }
      if (millis() < alert_expiry) { // render alert popup
        renderAlert();
        next_refresh = alert_expiry; // will need refresh when alert is dismissed
      } else {
        scheduleRefresh(delay_millis);
      }
      renderPairingPrompt();
      _display->endFrame();
    }
  }

#ifdef AUTO_SHUTDOWN_MILLIVOLTS
  if (millis() > next_batt_chck) {
    uint16_t milliVolts = getBattMilliVolts();
    if (milliVolts > 0 && milliVolts < AUTO_SHUTDOWN_MILLIVOLTS) {
      if (_display != NULL) {
        _display->turnOn();
        _display->startFrame();
        _display->setTextSize(2);
        _display->setColor(CardputerDisplay::P_RED);
        _display->drawTextCentered(_display->width() / 2, _display->height() / 2 - 8, "Battery low");
        _display->endFrame();
        delay(2000);
      }
      shutdown();
    }
    next_batt_chck = millis() + 8000;
  }
#endif
}

bool CardputerUITask::turnDisplayOn() {
  if (!_display) return false;
  const bool was_off = !_display->isOn();
  _display->wake(mesh::ui::DisplayWake::Button);
  scheduleRefresh();
  return was_off;
}

bool CardputerUITask::getGPSState() {
  return _node_prefs && _node_prefs->gps_enabled != 0;
}

bool CardputerUITask::toggleGPS() {
  if (!the_mesh_cp.setGpsEnabled(!getGPSState())) {
    showAlert("GPS setting could not be saved", 2000);
    return false;
  }
  scheduleRefresh();
  return true;
}

bool CardputerUITask::toggleBuzzer() {
  const uint8_t previous = _node_prefs->buzzer_quiet;
  _node_prefs->buzzer_quiet = !previous;
  if (!the_mesh_cp.savePrefs()) {
    _node_prefs->buzzer_quiet = previous;
    showAlert("Sound setting could not be saved", 2000);
    return false;
  }
  return true;
}

void CardputerUITask::renderAlert() {
  CardputerDisplay &lcd = *_display;
  lcd.setTextSize(1);
  const int margin = 4, pad = 5;
  const int box_w = lcd.width() - margin * 2;
  const int text_w = box_w - pad * 2;
  const int row = lcd.getRowHeight();

  // Split at the last space that fits when the text needs two lines
  char first[sizeof(alert_text)];
  const char *second = nullptr;
  snprintf(first, sizeof(first), "%s", alert_text);
  if (lcd.getTextWidth(first) > text_w) {
    for (int i = strlen(first) - 1; i > 0; i--) {
      if (first[i] != ' ') continue;
      first[i] = 0;
      if (lcd.getTextWidth(first) <= text_w) {
        second = alert_text + i + 1;
        break;
      }
      first[i] = ' ';
    }
  }

  const int lines = second ? 2 : 1;
  const int box_h = row * lines + pad * 2;
  const int y = (lcd.height() - box_h) / 2;
  lcd.setColor(CardputerDisplay::P_BLACK);
  lcd.fillRect(margin, y, box_w, box_h);
  lcd.setColor(CardputerDisplay::P_WHITE);
  lcd.drawRect(margin, y, box_w, box_h);
  if (second) {
    lcd.drawTextEllipsized(margin + pad, y + pad, text_w, first);
    lcd.drawTextEllipsized(margin + pad, y + pad + row, text_w, second);
  } else {
    lcd.drawTextCentered(lcd.width() / 2, y + pad, first);
  }
}

int CardputerUITask::drawModifierBadges(CardputerDisplay &lcd, int x, int y) {
  lcd.setSmallText();
  const Keyboard::Modifiers m = _board->getKeyboard()->getModifiers();
  const char *labels[2] = { m.fn ? "FN" : nullptr, m.shift ? "CAPS" : nullptr };
  const uint16_t colors[2] = { CardputerDisplay::P_ORANGE, CardputerDisplay::P_YELLOW };
  for (int i = 0; i < 2; i++) {
    if (!labels[i]) continue;
    int w = lcd.getTextWidth(labels[i]) + 4;
    lcd.setColor(colors[i]);
    lcd.fillRect(x, y, w, lcd.getRowHeight());
    lcd.setColor(CardputerDisplay::P_BLACK);
    lcd.drawTextLeftAlign(x + 2, y, labels[i]);
    x += w + 3;
  }
  lcd.setColor(CardputerDisplay::P_GREEN);
  lcd.setTextSize(1);
  return x;
}

void CardputerUITask::toggleBluetooth() {
  // Same checks and saved preference as the CLI's "set bluetooth on|off"
  char reply[96];
  const char *cmd = isBluetoothEnabled() ? "set bluetooth off" : "set bluetooth on";
  handleCompanionBluetoothCommand(cmd, reply, sizeof(reply), CompanionBluetoothCommandSource::Terminal);
  showAlert(strncmp(reply, "OK - ", 5) == 0 ? reply + 5 : reply, 2500);
}

void CardputerUITask::cycleTextSize() {
  if (!_display) return;
  _custom_prefs->text_level = (_display->getTextLevel() + 1) % CardputerDisplay::TEXT_LEVEL_COUNT;
  if (!the_mesh_cp.saveCustomPrefs()) {
    showAlert("Text size could not be saved", 2000);
    return;
  }
  _display->setTextLevel(_custom_prefs->text_level);
  scheduleRefresh();
}

void CardputerUITask::sendFloodAdvert() {
  showAlert(the_mesh_cp.sendAdvert(true) ? "Advert sent" : "Advert failed", 2000);
}

void CardputerUITask::cycleBrightness() {
  if (!_display) return;
  static const uint8_t levels[] = { 32, 96, 160, 255 };
  uint8_t next = levels[0];
  for (uint8_t level : levels) {
    if (level > _custom_prefs->brightness) {
      next = level;
      break;
    }
  }
  _custom_prefs->brightness = next;
  if (!the_mesh_cp.saveCustomPrefs()) {
    showAlert("Brightness could not be saved", 2000);
    return;
  }
  _display->setBrightness(next);
  char buf[24];
  snprintf(buf, sizeof(buf), "Brightness %d%%", next * 100 / 255);
  showAlert(buf, 1500);
}

// Formats "<name> <freq>,<bw>,<sf>,<cr>[,<preamble>]" (same number formatting as the CLI).
static void formatTuple(char *out, size_t cap, const char *name, float freq, float bw, uint8_t sf, uint8_t cr,
                        uint16_t preamble) {
  char f[16], b[16];  // ftoa/ftoa3 return a shared static buffer; copy each before the next call
  snprintf(f, sizeof(f), "%s", StrHelper::ftoa(freq));
  snprintf(b, sizeof(b), "%s", StrHelper::ftoa3(bw));
  int w = snprintf(out, cap, "%s %s,%s,%u,%u", name, f, b, (unsigned)sf, (unsigned)cr);
  if (w > 0 && (size_t)w < cap && preamble) {
    snprintf(out + w, cap - (size_t)w, ",%u", (unsigned)preamble);
  }
}

// slot 1..5 loads and shows that preset; slot 0 shows the active preset, or "custom" and the live radio.
void CardputerUITask::showPresetAlert(uint8_t slot) {
  char buf[80];
#if !MESH_ENABLE_RADIO_PRESETS
  // Built without presets (MESH_ENABLE_RADIO_PRESETS=0): Fn+0 still shows the live radio.
  if (slot != 0) {
    showAlert("Presets not in this build", 2000);
    return;
  }
  formatTuple(buf, sizeof(buf), "custom", _node_prefs->freq, _node_prefs->bw, _node_prefs->sf,
              _node_prefs->cr, 0);
  showAlert(buf, 3000);
#else
  const mesh::RadioPreset *p = nullptr;
  if (slot == 0) {
    uint8_t active = the_mesh_cp.activeRadioPreset();
    if (active != 0) {
      p = the_mesh_cp.radioPresetTable().get(active);
    }
    if (!p) {
      formatTuple(buf, sizeof(buf), "custom", _node_prefs->freq, _node_prefs->bw, _node_prefs->sf,
                  _node_prefs->cr, 0);
      showAlert(buf, 3000);
      return;
    }
  } else {
    p = the_mesh_cp.radioPresetTable().get(slot);
    if (!p) {
      snprintf(buf, sizeof(buf), "Preset %u empty", (unsigned)slot);
      showAlert(buf, 2000);
      return;
    }
    char reply[96];
    if (!the_mesh_cp.loadRadioPreset(slot, reply, sizeof(reply))) {
      showAlert(strncmp(reply, "OK - ", 5) == 0 ? reply + 5 : reply, 3000);
      return;
    }
  }
  formatTuple(buf, sizeof(buf), p->name, p->freq, p->bw, p->sf, p->cr, p->preamble);
  showAlert(buf, 3000);
#endif
}

void CardputerUITask::pingLastRepeater() {
  ContactInfo c;
  if (!the_mesh_cp.lastPingContact(c)) {
    showAlert("Ping a repeater from Contacts first", 3000);
    return;
  }
  if (the_mesh_cp.sendPing(c)) {
    showAlert("Waiting for response...", 4000);
    ping_deadline = millis() + 10000;
  } else {
    showAlert("Ping failed", 2000);
  }
}

void CardputerUITask::openNewestUnread() {
  MainScreen *main = static_cast<MainScreen *>(main_screen);
  if (!main->openNewestUnread()) {
    showAlert("No unread", 1500);
    return;
  }
  gotoMainScreen();
}

bool CardputerUITask::handleShortcut(const Keyboard::Event &e) {
  if (!e.modifiers.fn || e.modifiers.ctrl || e.modifiers.alt || e.modifiers.opt) {
    return false;
  }

  MainScreen *main = static_cast<MainScreen *>(main_screen);
  char buf[48];

  switch (e.key) {
    case Keyboard::KEY_A:  // Advert
      sendFloodAdvert();
      return true;
    case Keyboard::KEY_G:  // GPS
      if (toggleGPS()) showAlert(getGPSState() ? "GPS ON" : "GPS OFF", 1500);
      return true;
    case Keyboard::KEY_B:  // Bluetooth
      toggleBluetooth();
      return true;
    case Keyboard::KEY_M:  // Mute
      if (toggleBuzzer()) showAlert(isBuzzerQuiet() ? "Sound OFF" : "Sound ON", 1500);
      return true;
    case Keyboard::KEY_P:  // Power save
      if (togglePowerSave()) showAlert(powerSaveEnabled() ? "Power save ON" : "Power save OFF", 1500);
      return true;
    case Keyboard::KEY_L:  // Light
      cycleBrightness();
      return true;
    case Keyboard::KEY_D:  // Discover repeaters
      gotoToolsScreen();
      static_cast<ToolsScreen *>(tools_screen)->startDiscover();
      return true;
    case Keyboard::KEY_H:  // Home
      gotoMainScreen();
      main->setCurrentPage(MainScreen::FIRST);
      return true;
    case Keyboard::KEY_C:  // Contacts
      gotoMainScreen();
      main->setCurrentPage(MainScreen::CONTACTS);
      return true;
    case Keyboard::KEY_N:  // chaNnels
      gotoMainScreen();
      main->setCurrentPage(MainScreen::CHANNELS);
      return true;
    case Keyboard::KEY_I:  // Info
      gotoMainScreen();
      main->setCurrentPage(MainScreen::STATS);
      return true;
    case Keyboard::KEY_S:  // Settings
      gotoSettingsScreen();
      return true;
    case Keyboard::KEY_T:  // Tools
      gotoToolsScreen();
      return true;
    case Keyboard::KEY_Q:  // key lock (the only key that passes while locked)
      _keylock.toggle();
      showAlert(_keylock.locked ? "Keys locked" : "Keys unlocked", 1500);
      return true;
    case Keyboard::KEY_0:
      showPresetAlert(0);
      return true;
    case Keyboard::KEY_1:
      showPresetAlert(1);
      return true;
    case Keyboard::KEY_2:
      showPresetAlert(2);
      return true;
    case Keyboard::KEY_3:
      showPresetAlert(3);
      return true;
    case Keyboard::KEY_4:
      showPresetAlert(4);
      return true;
    case Keyboard::KEY_5:
      showPresetAlert(5);
      return true;
    case Keyboard::KEY_Z:  // ping the last repeater pinged from Contacts
      pingLastRepeater();
      return true;
    case Keyboard::KEY_U:  // open newest unread chat
      openNewestUnread();
      return true;
    case Keyboard::KEY_K: {  // Keyboard layout
      CardputerLayout *lay = _board->getLayout();
      if (lay->hasAlternateLayout()) {
        lay->switchLayout();
        snprintf(buf, sizeof(buf), "Layout: %s", lay->getCurrentCode());
        showAlert(buf, 1500);
      } else {
        showAlert("No other layout on SD", 2000);
      }
      return true;
    }
    default:
      return false;
  }
}

bool CardputerUITask::togglePowerSave() {
  char reply[160] = {};
  const char *command = _node_prefs->powersaving_enabled ? "powersaving off" : "powersaving on";
  if (!the_mesh_cp.handleLocalCommand(command, reply) || strncmp(reply, "OK", 2) != 0) {
    showAlert(reply[0] ? reply : "Power save setting failed", 2000);
    return false;
  }
  return true;
}
