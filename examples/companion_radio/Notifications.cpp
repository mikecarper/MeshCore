#include <helpers/CLIReplyText.h>
#include "MyMesh.h"
#include "target.h"
#include "NotificationSettingsFile.h"
#include <helpers/PeripheralOutputs.h>
#include <helpers/RuntimePinClaims.h>

#if defined(WIO_E5_MINI_HEADLESS_COMPANION_UI)
bool MyMesh::handleCompactLedCommand(const char* command, char* reply, size_t size) {
  if (!command || !reply || !size) return false;
  if (strcmp(command, "get leds") == 0) {
    snprintf(reply, size, "> %s", mesh::leds::enabled() ? "on" : "off");
    return true;
  }
  if (strncmp(command, "set leds", 8) != 0
      || (command[8] && command[8] != ' ' && command[8] != '\t')) return false;
  const char* value = command + 8;
  while (*value == ' ' || *value == '\t') ++value;
  if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
    snprintf(reply, size, "Error: usage set leds on|off");
    return true;
  }
  const bool on = strcmp(value, "on") == 0;
  const uint8_t previous = _prefs.leds_quiet;
  _prefs.leds_quiet = !on;
  if (_prefs.leds_quiet != previous) {
    if (!savePrefs()) {
      _prefs.leds_quiet = previous;
      snprintf(reply, size, "Error: LED setting save unconfirmed; reboot may differ");
      return true;
    }
  }
  mesh::leds::setEnabled(on);
  board.applyLedPolicy();
  mesh::leds::applyStatusPolicy();
  snprintf(reply, size, "OK");
  return true;
}
#endif

bool MyMesh::handleAlertCommand(const char* command, char* reply, size_t size) {
  if (!command || !reply || !size) return false;
  const bool get = strncmp(command, "get ", 4) == 0;
  if (!get && strncmp(command, "set ", 4) != 0) return false;
  const char* name = command + 4;
  uint8_t* quiet = nullptr;
  size_t length = 0;
  bool supported = false;
  if (strncmp(name, "buzzer", 6) == 0) {
    length = 6;
    quiet = &_prefs.buzzer_quiet;
#ifdef PIN_BUZZER
    supported = true;
#endif
#if MESH_CUSTOM_PERIPHERALS
    supported = supported || mesh::peripherals::configuredBuzzer();
#endif
  } else if (strncmp(name, "vibration", 9) == 0) {
    length = 9;
    quiet = &_prefs.vibe_quiet;
#if defined(PIN_VIBRATION) || defined(HAS_DRV2605)
    supported = true;
#endif
  } else return false;
  const char* value = name + length;
  if (*value && *value != ' ' && *value != '\t') return false;
  if (!supported) {
    snprintf(reply, size, "Error: %.*s unavailable on this build", int(length), name);
    return true;
  }
  if (get) {
    if (*value) snprintf(reply, size, "Error: usage get %.*s", int(length), name);
    else snprintf(reply, size, "> %s", *quiet ? "off" : "on");
    return true;
  }
  while (*value == ' ' || *value == '\t') ++value;
  if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
    snprintf(reply, size, "Error: usage set %.*s on|off", int(length), name);
    return true;
  }
  const uint8_t previous = *quiet;
  *quiet = strcmp(value, "off") == 0;
  if (*quiet != previous && !savePrefs()) {
    *quiet = previous;
    snprintf(reply, size, "Error: failed to save %.*s setting", int(length), name);
    return true;
  }
  if (_ui) _ui->syncAlertPreferences();
#if MESH_CUSTOM_PERIPHERALS
  mesh::peripherals::setBuzzerQuiet(_prefs.buzzer_quiet);
#endif
#if defined(PIN_BUZZER) || MESH_CUSTOM_PERIPHERALS
  // An explicit enable also serves as a sound check, even when already on.
  // Play only after persistence and live mute state agree. This confirmation
  // is independent of programmable message-alert output masks.
  if (quiet == &_prefs.buzzer_quiet && !*quiet) {
    static const char confirmation[] = "SoundOn:d=16,o=6,b=180:c,e,g";
#if MESH_CUSTOM_PERIPHERALS
    if (mesh::peripherals::configuredBuzzer())
      mesh::peripherals::playNotification(confirmation);
    else
#endif
    if (_ui) _ui->notificationMelody(confirmation);
  }
#endif
  snprintf(reply, size, "OK");
  return true;
}

#if MESH_CUSTOM_PERIPHERALS
void MyMesh::notifyCustomMessage(bool channel) {
  uint8_t outputs = 31;
#if COMPANION_FEATURE_NOTIFICATIONS
  if (_notifications.settings().enabled) outputs = _notifications.settings().outputs;
#endif
  mesh::peripherals::setBuzzerQuiet(_prefs.buzzer_quiet);
  mesh::peripherals::notifyMessage(channel, outputs & mesh::notify::Sound,
      outputs & mesh::notify::Led);
}
#endif

#if COMPANION_FEATURE_NOTIFICATIONS
// The UI's STATUS_LED_RGB define is local to its own translation unit.
uint8_t MyMesh::capabilities() const {
  uint8_t bits = _ui ? _ui->notificationCapabilities() : 0;
#if defined(PIN_STATUS_LED) || \
    (defined(PIN_STATUS_LED_R) && defined(PIN_STATUS_LED_G) && defined(PIN_STATUS_LED_B))
  bits |= mesh::notify::Led;
#endif
#if MESH_CUSTOM_PERIPHERALS
  if (mesh::peripherals::configuredBuzzer()) bits |= mesh::notify::Sound;
  if (mesh::peripherals::configuredNotificationLed()) bits |= mesh::notify::Led;
#endif
  for(unsigned pin=0;pin<64;++pin)if(gpioAvailable(pin)) { bits|=mesh::notify::Gpio;break; }
  return bits;
}
bool MyMesh::gpioAvailable(uint8_t pin) const {
#if MESH_CUSTOM_PERIPHERALS
  return board.isPeripheralOutputAvailable(pin)
      && !mesh::pins::claimedByOther(board.peripheralPinIdentity(pin), mesh::pins::Owner::Notifications);
#else
  return board.isUserGpioAvailable(pin);
#endif
}
void MyMesh::pulse(mesh::notify::Output output,bool on,int8_t pin) {
  if(output==mesh::notify::Gpio && pin>=0 && gpioAvailable(pin)) {
    pinMode(pin,OUTPUT);digitalWrite(pin,on?HIGH:LOW);
  } else if(output==mesh::notify::Vibration) {
    if(_ui)_ui->notificationVibration(on);
  } else if(output==mesh::notify::Led) {
#if MESH_CUSTOM_PERIPHERALS
    mesh::peripherals::notificationLed(on);
#endif
    on = on && mesh::leds::enabled();
#if defined(PIN_STATUS_LED_R) && defined(PIN_STATUS_LED_G) && defined(PIN_STATUS_LED_B)
    analogWrite(PIN_STATUS_LED_R,on?255:0);
    analogWrite(PIN_STATUS_LED_G,on?90:0);
    analogWrite(PIN_STATUS_LED_B,0);
#elif defined(PIN_STATUS_LED)
    pinMode(PIN_STATUS_LED,OUTPUT);
#ifdef LED_STATE_ON
    digitalWrite(PIN_STATUS_LED,on?LED_STATE_ON:!LED_STATE_ON);
#else
    digitalWrite(PIN_STATUS_LED,on?HIGH:LOW);
#endif
#endif
  }
}
void MyMesh::melody(const char* text) {
#if MESH_CUSTOM_PERIPHERALS
  if (mesh::peripherals::configuredBuzzer()) {
    mesh::peripherals::setBuzzerQuiet(_prefs.buzzer_quiet);
    mesh::peripherals::playNotification(text);
    return;
  }
#endif
  if(_ui)_ui->notificationMelody(text);
}
void MyMesh::screen(int8_t mode) { if(_ui)_ui->notificationScreen(mode); }
bool MyMesh::save(const mesh::notify::Settings& settings) {
#if MESH_CUSTOM_PERIPHERALS
  uint64_t next_mask;
  if (!_notifications.validSettings(settings) || !notificationGpioMask(settings, next_mask)) return false;
  const uint64_t previous_mask = mesh::pins::held(mesh::pins::Owner::Notifications);
  if (!mesh::pins::tryClaim(mesh::pins::Owner::Notifications, next_mask)) return false;
  FILESYSTEM* fs = _store->getPrimaryFS();
  if (!mesh::notify::writeSettings(fs, settings)
      || !mesh::notify::verifySettingsFile(fs, "/notify_prefs", &settings)) {
    mesh::pins::release(mesh::pins::Owner::Notifications, next_mask & ~previous_mask);
    return false;
  }
  // The controller keeps its old playing rule until save() succeeds. Stop
  // that rule while its pins are still owned, before retiring old claims.
  _notifications.stop();
  releaseNotificationGpio(previous_mask & ~next_mask);
  mesh::pins::release(mesh::pins::Owner::Notifications, previous_mask & ~next_mask);
  return true;
#else
  return mesh::notify::writeSettings(_store->getPrimaryFS(),settings);
#endif
}
#if MESH_CUSTOM_PERIPHERALS
bool MyMesh::notificationGpioMask(const mesh::notify::Settings& settings, uint64_t& mask) const {
  mask = 0;
  for (const auto& rule : settings.rules) {
    // "inherit" clears FGpio but retains the old pin in the stored rule.
    // An explicit <pin>:off still initializes that pad LOW on alert start.
    if (!rule.used || !(rule.fields & mesh::notify::FGpio) || rule.pin < 0) continue;
    if (!gpioAvailable(uint8_t(rule.pin))) return false;
    const uint64_t bit = mesh::pins::pinMask(board.peripheralPinIdentity(uint8_t(rule.pin)));
    if (!bit) return false;
    mask |= bit;
  }
  return true;
}
void MyMesh::releaseNotificationGpio(uint64_t mask) {
  if (!mask) return;
  for (unsigned pin = 0; pin < 64; ++pin) {
    if (!(mask & mesh::pins::pinMask(board.peripheralPinIdentity(uint8_t(pin))))
        || !board.isPeripheralOutputAvailable(uint8_t(pin))) continue;
    digitalWrite(pin, LOW);
    pinMode(pin, INPUT);
  }
}
bool MyMesh::claimNotificationGpio() {
  const uint64_t previous_mask = mesh::pins::held(mesh::pins::Owner::Notifications);
  uint64_t next_mask;
  if (!notificationGpioMask(_notifications.settings(), next_mask)
      || !mesh::pins::tryClaim(mesh::pins::Owner::Notifications, next_mask)) {
    _notifications.stop();
    releaseNotificationGpio(previous_mask);
    mesh::pins::release(mesh::pins::Owner::Notifications, previous_mask);
    return false;
  }
  releaseNotificationGpio(previous_mask & ~next_mask);
  mesh::pins::release(mesh::pins::Owner::Notifications, previous_mask & ~next_mask);
  return true;
}
#endif
void MyMesh::setNotificationOutputMute(mesh::notify::Output output,bool muted) {
  if(!_notifications.settings().enabled)return;
  char command[40],reply[160];
  snprintf(command,sizeof(command),"set notify.%s %s",
      output==mesh::notify::Sound?"sound":"vibration",muted?"off":"on");
  _notifications.handle(command,reply,sizeof(reply),millis(),_serial->isConnected());
}
bool MyMesh::handleNotificationCommand(const char* command,char* reply,size_t size) {
  if(strncmp(command,"set notify.",11)&&strncmp(command,"get notify",10)
      &&strncmp(command,"notify.",7))return false;
  // Resolve channel slots now and store their secret, so a replaced slot does
  // not inherit a previous channel's VIP/page rule. Full keys also work.
  char normalized[240];
  const char* token=strstr(command,"channel:");
  if(token) {
    const char* value=token+8;const char* end=value;
    while(*end && *end!=' ' && *end!='@')++end;
    if(end-value<4) {
      char digits[4]={};memcpy(digits,value,end-value);uint32_t index;
      ChannelDetails details;
      if(!mesh::notify::unsignedValue(digits,index,MAX_GROUP_CHANNELS-1)
          ||!getChannel(index,details)||!details.name[0]) {
        snprintf(reply,size,"Error: channel slot not found");return true;
      }
      char key[33];mesh::Utils::toHex(key,details.channel.secret,16);
      int prefix=value-command;
      int n=snprintf(normalized,sizeof(normalized),"%.*s%s%s",prefix,command,key,end);
      if(n<0||size_t(n)>=sizeof(normalized)) { snprintf(reply,size,"Error: command too long");return true; }
      command=normalized;
    }
  }
  return _notifications.handle(command,reply,size,millis(),_serial->isConnected());
}

#else
bool MyMesh::handleNotificationCommand(const char* command,char* reply,size_t size) {
  if(strncmp(command,"set notify.",11)&&strncmp(command,"get notify",10)
      &&strncmp(command,"notify.",7))return false;
  snprintf(reply,size,MESH_CLI_REPLY_TEXT("Error: programmable alerts unavailable on this compact STM32 build", "Error: programmable alerts unavailable on compact STM32"));
  return true;
}
#endif
