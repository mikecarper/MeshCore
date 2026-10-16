#include "MyMesh.h"
#include "target.h"
#include "NotificationSettingsFile.h"

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
  snprintf(reply,size,"Error: programmable alerts unavailable on this compact STM32 build");
  return true;
}
#endif
