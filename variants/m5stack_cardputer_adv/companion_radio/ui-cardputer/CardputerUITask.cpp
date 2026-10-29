#include "CardputerUITask.h"

#include "screen/MainScreen.h"
#include "screen/NewMessageScreen.h"
#include "screen/SettingsScreen.h"
#include "screen/SplashScreen.h"
#include "screen/ToolsScreen.h"
#include "target.h"
#include "helpers.h"
#include "CompanionBluetooth.h"

#include <helpers/RadioPresets.h>

void CardputerUITask::begin(DisplayDriver *display, SensorManager *sensors, CompanionNodePrefs *node_prefs,
                            CustomNodePrefs *custom_prefs) {
  _display = static_cast<CardputerDisplay *>(display);
  _sensors = sensors;
  extendAutoOff();

  _node_prefs = node_prefs;
  _custom_prefs = custom_prefs;

  if (_display != NULL) {
    _display->setBrightness(_custom_prefs->brightness);
    _display->setTextLevel(_custom_prefs->text_level);
    _display->turnOn();
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

bool CardputerUITask::isAlertActive() {
  return millis() < alert_expiry;
}

void CardputerUITask::notify(UIEventType t) {
  if (t == UIEventType::channelMessage || t == UIEventType::contactMessage) {
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

  bool onFirstPage =
      (current_screen == main_screen && ((MainScreen *)main_screen)->getCurrentPage() == MainScreen::FIRST);

  bool display_off = (_display != NULL && !_display->isOn());

  if (display_off || current_screen == new_message_screen || onFirstPage) {
    ((NewMessageScreen *)new_message_screen)->newMessage(from_name, text);
    setCurrScreen(new_message_screen);
  }

  if (_display != NULL) {
    if (!_display->isOn() && !hasConnection()) {
      _display->turnOn();
    }
    extendAutoOff();
    scheduleRefresh();
  }
}

void CardputerUITask::onChannelMessageRecv(const mesh::GroupChannel &channel, const char *text) {
  ((MainScreen *)main_screen)->onChannelMessageRecv(channel, text);
}

void CardputerUITask::onContactMessageRecv(const ContactInfo &contact, const char *text) {
  ((MainScreen *)main_screen)->onContactMessageRecv(contact, text);
}

void CardputerUITask::onMessageSendAttempt(uint8_t attempt, uint8_t total, MessageSendState state) {
  extendAutoOff();

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
  if (restart) {
    _board->reboot();
  } else {
    if (_display) _display->turnOff();
    radio_driver.powerOff();
    _board->powerOff();
  }
}

void CardputerUITask::loop() {
  _board->getSpeaker()->processQueue();

  CardputerKeyboard *kb = _board->getKeyboard();
  const Keyboard::Modifiers latched = kb->getModifiers();
  auto event = kb->poll();

  if (event.changed && event.down) {
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

  if (ping_deadline != 0 && (long)(millis() - ping_deadline) >= 0) {
    ping_deadline = 0;
    showAlert("No answer", 3000);
  }

  if (_top_btn.check() == BUTTON_EVENT_CLICK) {
    turnDisplayOn();
    sendFloodAdvert();
  }

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
      _display->endFrame();
    }
#if AUTO_OFF_MILLIS > 0
    if (millis() > auto_off_time) {
      _display->turnOff();
      _board->getSpeaker()->sleep();
    }
#endif
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
  if (_display != NULL) {
    extendAutoOff();
    scheduleRefresh();

    if (!_display->isOn()) {
      _display->turnOn();
      return true;
    }
  }
  return false;
}

bool CardputerUITask::getGPSState() {
  if (_sensors != NULL) {
    int num = _sensors->getNumSettings();
    for (int i = 0; i < num; i++) {
      if (strcmp(_sensors->getSettingName(i), "gps") == 0) {
        return !strcmp(_sensors->getSettingValue(i), "1");
      }
    }
  }
  return false;
}

void CardputerUITask::toggleGPS() {
  if (_sensors != NULL) {
    int num = _sensors->getNumSettings();
    for (int i = 0; i < num; i++) {
      if (strcmp(_sensors->getSettingName(i), "gps") == 0) {
        if (strcmp(_sensors->getSettingValue(i), "1") == 0) {
          _sensors->setSettingValue("gps", "0");
          _node_prefs->gps_enabled = 0;
        } else {
          _sensors->setSettingValue("gps", "1");
          _node_prefs->gps_enabled = 1;
        }
        the_mesh_cp.savePrefs();
        scheduleRefresh();
        break;
      }
    }
  }
}

void CardputerUITask::toggleBuzzer() {
  _node_prefs->buzzer_quiet = !_node_prefs->buzzer_quiet;
  the_mesh_cp.savePrefs();
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
  uint8_t next = (_display->getTextLevel() + 1) % CardputerDisplay::TEXT_LEVEL_COUNT;
  _display->setTextLevel(next);
  _custom_prefs->text_level = next;
  the_mesh_cp.savePrefs();
}

void CardputerUITask::sendFloodAdvert() {
  showAlert(the_mesh_cp.sendAdvert(true) ? "Advert sent" : "Advert failed", 2000);
}

void CardputerUITask::cycleBrightness() {
  static const uint8_t levels[] = { 32, 96, 160, 255 };
  uint8_t next = levels[0];
  for (uint8_t level : levels) {
    if (level > _custom_prefs->brightness) {
      next = level;
      break;
    }
  }
  _custom_prefs->brightness = next;
  _display->setBrightness(next);
  the_mesh_cp.savePrefs();
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
      toggleGPS();
      showAlert(getGPSState() ? "GPS ON" : "GPS OFF", 1500);
      return true;
    case Keyboard::KEY_B:  // Bluetooth
      toggleBluetooth();
      return true;
    case Keyboard::KEY_M:  // Mute
      toggleBuzzer();
      showAlert(isBuzzerQuiet() ? "Sound OFF" : "Sound ON", 1500);
      return true;
    case Keyboard::KEY_P:  // Power save
      togglePowerSave();
      showAlert(powerSaveEnabled() ? "Power save ON" : "Power save OFF", 1500);
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

void CardputerUITask::togglePowerSave() {
  the_mesh_cp.setPowerSaving(!_node_prefs->powersaving_enabled);
}
