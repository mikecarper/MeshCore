#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#ifndef MESH_NOTIFY_RTC_SETTINGS
#define MESH_NOTIFY_RTC_SETTINGS 0
#endif
#if MESH_NOTIFY_RTC_SETTINGS != 0 && MESH_NOTIFY_RTC_SETTINGS != 1
#error "MESH_NOTIFY_RTC_SETTINGS must be 0 or 1"
#endif
#if MESH_NOTIFY_RTC_SETTINGS
#if !defined(ESP32_PLATFORM) || !defined(TBEAM_SX1262) || !defined(MESH_RADIO_AUTO_FAMILY) || !MESH_RADIO_AUTO_FAMILY
#error "RTC notification settings require the universal ESP32 T-Beam"
#endif
#include <sdkconfig.h>
#if !defined(CONFIG_IDF_TARGET_ESP32) || !CONFIG_IDF_TARGET_ESP32
#error "RTC notification settings require the classic ESP32"
#endif
#endif

namespace mesh { namespace notify {

constexpr unsigned MAX_RULES = 12, MAX_PULSES = 12, MAX_SOUND = 64;
enum Output : uint8_t { Vibration = 1, Sound = 2, Led = 4, Screen = 8, Gpio = 16 };
enum Stop : uint8_t { Button, Connected, Never };
enum When : uint8_t { Any, Online, Offline };
enum Kind : uint8_t { All, Contact, Channel };
enum Field : uint16_t { FVibe=1, FSound=2, FLed=4, FScreen=8, FGpio=16,
                       FRepeat=32, FGap=64, FStop=128, FRemote=256 };
struct Pulse {
  uint16_t ms[MAX_PULSES] = {};
  uint8_t count = 0;
  uint32_t duration() const { uint32_t n=0; for(unsigned i=0;i<count;++i) n+=ms[i]; return n; }
  bool level(uint32_t elapsed) const {
    for(unsigned i=0;i<count;++i) { if(elapsed<ms[i]) return !(i&1); elapsed-=ms[i]; }
    return false;
  }
};
struct Selector {
  uint8_t key[32] = {};
  Kind kind = All;
  When when = Any;
  bool operator==(const Selector& other) const {
    return kind==other.kind && when==other.when && !memcmp(key,other.key,sizeof(key));
  }
};
struct Rule {
  Selector selector;
  Pulse vibration, led, gpio;
  char sound[MAX_SOUND] = {};
  uint16_t fields = 0, repeat = 1, gap = 500;
  int8_t pin = -1;
  int8_t screen = -1; // -1: normal display policy, 0: off, 1: on during alert
  Stop stop = Button;
  uint8_t used = 0;
  uint8_t remote = 0; // contact-only permission, never a wildcard
};
struct Settings {
  Rule rules[MAX_RULES];
  uint8_t enabled = 0, outputs = 31;
};
#if MESH_NOTIFY_RTC_SETTINGS
Settings& rtcSettings();
#endif

inline bool number(const char*& p, uint32_t& n, uint32_t maximum) {
  if(*p<'0'||*p>'9') return false;
  n=0;
    do { unsigned digit=*p++-'0'; if(digit>maximum || n>(maximum-digit)/10) return false; n=n*10+digit; }
  while(*p>='0'&&*p<='9');
  return n<=maximum;
}
inline bool unsignedValue(const char* p,uint32_t& n,uint32_t maximum) {
  return number(p,n,maximum)&&!*p;
}
inline bool parsePulse(const char* p,Pulse& out) {
  Pulse next;
  if(!strcmp(p,"off")) { out=next; return true; }
  do {
    uint32_t n;
    if(next.count==MAX_PULSES || !number(p,n,60000) || !n) return false;
    next.ms[next.count++]=n;
    if(!*p) { out=next; return true; }
    if(*p++!=',' || !*p) return false;
  } while(true);
}
inline bool durationDenominator(uint32_t n) { return n==1||n==2||n==4||n==8||n==16||n==32; }
// Strict subset matching NonBlockingRTTTL's monophonic parser and C4..B7 table.
// In particular octave 3, B#7, malformed defaults, and zero BPM are unsafe.
inline bool soundDuration(const char* p,uint32_t& total) {
  total=0;
  if(!*p || !strcmp(p,"off")) return true;
  unsigned name=0;
  while(*p && *p!=':') { if(!((*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')||(*p>='0'&&*p<='9')||*p=='_')||++name>12) return false; ++p; }
  if(!name||*p++!=':'||strncmp(p,"d=",2)) return false;
  p+=2; uint32_t d,o,b;
  if(*p=='0'||!number(p,d,32)||!durationDenominator(d)||*p++!=','||strncmp(p,"o=",2)) return false;
  p+=2;
  // The installed driver consumes exactly one octave digit, unlike duration
  // and BPM. Accepting c04 would send octave 0 into its pitch-table index.
  if(*p<'4'||*p>'7')return false;
  o=*p++-'0';
  if(*p++!=','||strncmp(p,"b=",2)) return false;
  p+=2;
  if(!number(p,b,900)||b<25||*p++!=':'||!*p) return false;
  do {
    uint32_t nd=d,no=o;
    if(*p=='0')return false;
    if(*p>='0'&&*p<='9' && (!number(p,nd,32)||!durationDenominator(nd))) return false;
    char note=*p++;
    if((note<'a'||note>'g')&&note!='p') return false;
    if(*p=='#') { if(note!='a'&&note!='c'&&note!='d'&&note!='f'&&note!='g') return false; ++p; }
    bool dot=*p=='.'; if(dot) ++p;
    if(*p>='0'&&*p<='9') { no=*p++-'0';if(no<4||no>7)return false; }
    if(*p=='.') { if(dot) return false; dot=true; ++p; }
    uint32_t ms=(60000/b)*4/nd; if(dot) ms+=ms/2;
    total+=ms;
    if(total>60000) return false;
    if(!*p) return true;
    if(*p++!=','||!*p) return false;
  } while(true);
}
inline int hex(char c) {
  if(c>='0'&&c<='9')return c-'0'; if(c>='a'&&c<='f')return c-'a'+10;
  if(c>='A'&&c<='F')return c-'A'+10; return -1;
}
inline bool parseSelector(const char* text,Selector& out) {
  Selector next; const char* suffix=strchr(text,'@');
  size_t len=suffix?size_t(suffix-text):strlen(text);
  if(suffix) {
    if(!strcmp(suffix,"@connected"))next.when=Online;
    else if(!strcmp(suffix,"@disconnected"))next.when=Offline;
    else return false;
  }
  if(len==3&&!strncmp(text,"all",3)) { out=next; return true; }
  unsigned bytes;
  if(len==72&&!strncmp(text,"contact:",8)) { next.kind=Contact; text+=8; bytes=32; }
  else if(len==69&&!strncmp(text,"room:",5)) { next.kind=Contact; text+=5; bytes=32; }
  else if(len==40&&!strncmp(text,"channel:",8)) { next.kind=Channel; text+=8; bytes=16; }
  else return false;
  for(unsigned i=0;i<bytes;++i) {
    int a=hex(text[i*2]),b=hex(text[i*2+1]); if(a<0||b<0)return false;
    next.key[i]=(a<<4)|b;
  }
  out=next; return true;
}
inline void formatSelector(const Selector& s,char* out,size_t capacity) {
  const char* kind=s.kind==Contact?"contact:":s.kind==Channel?"channel:":"all";
  size_t n=snprintf(out,capacity,"%s",kind);
  unsigned bytes=s.kind==Contact?32:s.kind==Channel?16:0;
  for(unsigned i=0;i<bytes&&n+2<capacity;++i)n+=snprintf(out+n,capacity-n,"%02x",s.key[i]);
  if(n<capacity)snprintf(out+n,capacity-n,"%s",s.when==Online?"@connected":s.when==Offline?"@disconnected":"");
}
inline void formatPulse(const Pulse& p,char* out,size_t capacity) {
  if(!p.count) { snprintf(out,capacity,"off");return; }
  size_t n=0;for(unsigned i=0;i<p.count&&n<capacity;++i)n+=snprintf(out+n,capacity-n,"%s%u",i?",":"",unsigned(p.ms[i]));
}
inline uint16_t field(const char* text) {
  if(!strcmp(text,"vibration"))return FVibe; if(!strcmp(text,"sound"))return FSound;
  if(!strcmp(text,"led"))return FLed; if(!strcmp(text,"screen"))return FScreen;
  if(!strcmp(text,"gpio"))return FGpio; if(!strcmp(text,"repeat"))return FRepeat;
  if(!strcmp(text,"gap"))return FGap; if(!strcmp(text,"stop"))return FStop;
  if(!strcmp(text,"remote"))return FRemote;return 0;
}
inline bool setField(Rule& rule,uint16_t f,const char* value) {
  if(!strcmp(value,"inherit")) { rule.fields &= ~f; return true; }
  uint32_t n;
  if(f==FVibe) { if(!parsePulse(value,rule.vibration))return false; }
  else if(f==FLed) { if(!parsePulse(value,rule.led))return false; }
  else if(f==FSound) {
    uint32_t total;
    if(strlen(value)>=MAX_SOUND||!soundDuration(value,total))return false;
    strcpy(rule.sound,!strcmp(value,"off")?"":value);
  } else if(f==FScreen) {
    if(!strcmp(value,"on"))rule.screen=1;else if(!strcmp(value,"off"))rule.screen=0;else return false;
  } else if(f==FGpio) {
    if(!strcmp(value,"off")) { rule.pin=-1;rule.gpio=Pulse(); }
    else {
      const char* p=value;
      if(!number(p,n,63)||*p++!=':'||!parsePulse(p,rule.gpio))return false;
      rule.pin=n;
    }
  } else if(f==FRepeat) {
    if(!strcmp(value,"forever"))rule.repeat=0;
    else { if(!unsignedValue(value,n,65535)||!n)return false;rule.repeat=n; }
  } else if(f==FGap) {
    if(!unsignedValue(value,n,60000)||!n)return false;rule.gap=n;
  } else if(f==FStop) {
    if(!strcmp(value,"button"))rule.stop=Button;
    else if(!strcmp(value,"connected"))rule.stop=Connected;
    else if(!strcmp(value,"never"))rule.stop=Never;
    else return false;
  } else if(f==FRemote) {
    if(rule.selector.kind!=Contact||rule.selector.when!=Any)return false;
    if(!strcmp(value,"on"))rule.remote=1;else if(!strcmp(value,"off"))rule.remote=0;else return false;
  } else return false;
  rule.fields |= f;return true;
}
inline void overlay(Rule& dst,const Rule& src) {
  if(src.fields&FVibe)dst.vibration=src.vibration;
  if(src.fields&FSound)memcpy(dst.sound,src.sound,sizeof(dst.sound));
  if(src.fields&FLed)dst.led=src.led;
  if(src.fields&FScreen)dst.screen=src.screen;
  if(src.fields&FGpio) { dst.gpio=src.gpio;dst.pin=src.pin; }
  if(src.fields&FRepeat)dst.repeat=src.repeat;
  if(src.fields&FGap)dst.gap=src.gap;
  if(src.fields&FStop)dst.stop=src.stop;
  dst.fields|=src.fields;
}

class Sink {
public:
  virtual ~Sink() = default;
  virtual uint8_t capabilities() const = 0;
  virtual bool gpioAvailable(uint8_t pin) const = 0;
  virtual void pulse(Output output,bool on,int8_t pin) = 0;
  virtual void melody(const char* rtttl) = 0; // nullptr stops playback
  virtual void screen(int8_t mode) = 0; // -1 restores display policy
};
class Store {
public:
  virtual ~Store() = default;
  virtual bool save(const Settings& settings) = 0;
};
class Controller {
  Sink& sink; Store& store;
#if MESH_NOTIFY_RTC_SETTINGS
  Settings& prefs;
#else
  Settings prefs;
#endif
  Rule playing;
  uint32_t started=0,cycle_ms=0;
  uint16_t cycles=0;
  uint8_t levels=0;
  bool running=false,was_connected=false,connection_initialized=false;
  bool remote_playing=false;
  uint32_t remote_started=0;
  struct SeenRemote { uint8_t key[32]={};uint32_t timestamp=0,hash=0; } remote_seen[8];
  uint8_t remote_seen_next=0;
  void drive(uint32_t elapsed) {
    const Output outputs[]={Vibration,Led,Gpio};
    const Pulse* patterns[]={&playing.vibration,&playing.led,&playing.gpio};
    for(unsigned i=0;i<3;++i) {
      bool on=(prefs.outputs&outputs[i])&&patterns[i]->level(elapsed);
      if(on!=bool(levels&outputs[i])) {
        sink.pulse(outputs[i],on,playing.pin);
        if(on)levels|=outputs[i];else levels&=~outputs[i];
      }
    }
  }
  void startCycle(uint32_t elapsed=0) {
    uint32_t sound_ms=0;soundDuration(playing.sound,sound_ms);
    cycle_ms=playing.vibration.duration();
    if(playing.led.duration()>cycle_ms)cycle_ms=playing.led.duration();
    if(playing.gpio.duration()>cycle_ms)cycle_ms=playing.gpio.duration();
    if(sound_ms>cycle_ms)cycle_ms=sound_ms;
    if(!cycle_ms)cycle_ms=1000; // screen-only alert
    drive(elapsed);
    if(elapsed<sound_ms&&(prefs.outputs&Sound)&&playing.sound[0])sink.melody(playing.sound);
    if(!(prefs.outputs&Screen))sink.screen(0);
    else if(playing.screen>=0)sink.screen(playing.screen);
  }
public:
  Controller(Sink& output,Store& storage
#if MESH_NOTIFY_RTC_SETTINGS
    ,Settings& settings
#endif
  ):sink(output),store(storage)
#if MESH_NOTIFY_RTC_SETTINGS
    ,prefs(settings)
#endif
  {}
  Settings& settings() { return prefs; }
  const Settings& settings() const { return prefs; }
  bool active() const { return running; }
  bool ownsLed() const { return running&&(playing.fields&FLed); }
  bool overridesScreen() const { return running&&(!(prefs.outputs&Screen)||(playing.fields&FScreen)); }
  void stop() {
    if(!running)return;
    sink.melody(nullptr);
    sink.pulse(Vibration,false,-1);sink.pulse(Led,false,-1);
    if(playing.pin>=0)sink.pulse(Gpio,false,playing.pin);
    sink.screen(-1);levels=0;running=false;remote_playing=false;
  }
  bool button() { if(!running||playing.stop!=Button)return false;stop();return true; }
  void loop(uint32_t now,bool connected) {
    if(remote_playing&&uint32_t(now-remote_started)>=15000)stop();
    if(running && playing.stop==Connected && connection_initialized && connected&&!was_connected)stop();
    was_connected=connected;connection_initialized=true;
    if(!running)return;
    uint32_t elapsed=now-started;
    // Late loops skip missed pulses rather than blocking or stretching them.
    uint32_t period=cycle_ms+playing.gap;
    uint32_t advances=elapsed/period;
    if(advances) {
      if(playing.repeat && advances>=uint32_t(playing.repeat-cycles)) { stop();return; }
      cycles+=advances;started+=advances*period;elapsed=now-started;
      sink.melody(nullptr);startCycle(elapsed);
    }
    if(playing.repeat && cycles+1>=playing.repeat && elapsed>=cycle_ms) { stop();return; }
    drive(elapsed);
  }
  bool message(const uint8_t* contact,const uint8_t* channel,bool connected,uint32_t now) {
    if(!prefs.enabled)return false;
    Rule next;bool matched=false;
    for(unsigned rank=0;rank<4;++rank)for(const Rule& r:prefs.rules) {
      if(!r.used||!(r.fields&255)||unsigned((r.selector.kind!=All?2:0)+(r.selector.when!=Any?1:0))!=rank)continue;
      if((r.selector.when==Online&&!connected)||(r.selector.when==Offline&&connected))continue;
      if(r.selector.kind==Contact&&(!contact||memcmp(r.selector.key,contact,32)))continue;
      if(r.selector.kind==Channel&&(!channel||memcmp(r.selector.key,channel,16)))continue;
      overlay(next,r);matched=true;
    }
    if(!matched)return false;
    stop();playing=next;started=now;cycles=0;running=true;
    if(playing.fields&FLed)sink.pulse(Led,false,-1);
    if(playing.fields&FVibe)sink.pulse(Vibration,false,-1);
    if(playing.pin>=0)sink.pulse(Gpio,false,playing.pin);
    startCycle();return true;
  }
  bool handle(const char* command,char* reply,size_t capacity,uint32_t now,bool connected);
  bool validSettings(const Settings& candidate) const;
  bool remoteMessage(const uint8_t* contact,const char* text,uint32_t now,uint32_t timestamp=0);
};

} }
