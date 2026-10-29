#include <helpers/CLIReplyText.h>
#include "MyMesh.h"
#include "target.h"
#include "NotificationSettingsFile.h"

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
  snprintf(reply, size, "OK");
  return true;
}

#if COMPANION_FEATURE_NOTIFICATIONS
// The UI's STATUS_LED_RGB define is local to its own translation unit.
uint8_t MyMesh::capabilities() const {
  uint8_t bits = _ui ? _ui->notificationCapabilities() : 0;
#if defined(PIN_STATUS_LED) || \
    (defined(PIN_STATUS_LED_R) && defined(PIN_STATUS_LED_G) && defined(PIN_STATUS_LED_B))
  bits |= mesh::notify::Led;
#endif
  for(unsigned pin=0;pin<64;++pin)if(gpioAvailable(pin)) { bits|=mesh::notify::Gpio;break; }
  return bits;
}
bool MyMesh::gpioAvailable(uint8_t pin) const { return board.isUserGpioAvailable(pin); }
void MyMesh::pulse(mesh::notify::Output output,bool on,int8_t pin) {
  if(output==mesh::notify::Gpio && pin>=0 && gpioAvailable(pin)) {
    pinMode(pin,OUTPUT);digitalWrite(pin,on?HIGH:LOW);
  } else if(output==mesh::notify::Vibration) {
    if(_ui)_ui->notificationVibration(on);
  } else if(output==mesh::notify::Led) {
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
void MyMesh::melody(const char* text) { if(_ui)_ui->notificationMelody(text); }
void MyMesh::screen(int8_t mode) { if(_ui)_ui->notificationScreen(mode); }
bool MyMesh::save(const mesh::notify::Settings& settings) {
  return mesh::notify::writeSettings(_store->getPrimaryFS(),settings);
}
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
