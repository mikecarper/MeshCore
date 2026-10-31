#include "CompanionNotificationPolicy.h"

#if MESH_NOTIFY_RTC_SETTINGS
#include <esp_attr.h>
#include <new>
#include <type_traits>
#endif

namespace mesh { namespace notify {
namespace {
#if MESH_NOTIFY_RTC_SETTINGS
// Constant initialization also makes this safe from a global Controller's ctor.
constexpr Settings rtc_settings_defaults{};
RTC_SLOW_ATTR Settings rtc_settings = rtc_settings_defaults;
bool rtc_settings_initialized = false;
static_assert(std::is_trivially_copyable<Settings>::value, "RTC settings must be plain data");
static_assert(std::is_trivially_destructible<Settings>::value, "RTC settings must have no resources");
#endif

void valueText(const Rule& r,uint16_t f,char* out,size_t size) {
  if(!(r.fields&f)) { snprintf(out,size,"inherit");return; }
  if(f==FVibe)formatPulse(r.vibration,out,size);
  else if(f==FLed)formatPulse(r.led,out,size);
  else if(f==FSound)snprintf(out,size,"%s",r.sound[0]?r.sound:"off");
  else if(f==FScreen)snprintf(out,size,"%s",r.screen?"on":"off");
  else if(f==FGpio) {
    if(r.pin<0)snprintf(out,size,"off");
    else { size_t n=snprintf(out,size,"%d:",int(r.pin));if(n<size)formatPulse(r.gpio,out+n,size-n); }
  } else if(f==FRepeat) { if(r.repeat)snprintf(out,size,"%u",unsigned(r.repeat));else snprintf(out,size,"forever"); }
  else if(f==FGap)snprintf(out,size,"%u",unsigned(r.gap));
  else if(f==FStop)snprintf(out,size,"%s",r.stop==Button?"button":r.stop==Connected?"connected":"never");
  else if(f==FRemote)snprintf(out,size,"%s",r.remote?"on":"off");
}
}

#if MESH_NOTIFY_RTC_SETTINGS
Settings& rtcSettings() {
  if(!rtc_settings_initialized) {
    // RTC RAM survives sleep; notification settings are loaded from disk each boot.
    // Construct in place to avoid a settings-sized temporary on the startup stack.
    rtc_settings.~Settings();
    new (static_cast<void*>(&rtc_settings)) Settings();
    rtc_settings_initialized = true;
  }
  return rtc_settings;
}
#endif

bool Controller::validSettings(const Settings& s) const {
  if(s.enabled>1||(s.outputs&~31))return false;
  for(unsigned i=0;i<MAX_RULES;++i) {
    const Rule& r=s.rules[i];
    if(r.used>1)return false;if(!r.used)continue;
    if(r.selector.kind>Channel||r.selector.when>Offline||r.fields>511||r.stop>Never||r.remote>1
        ||!r.gap||r.gap>60000||r.screen<-1||r.screen>1||r.pin<-1||r.pin>63)return false;
    if((r.fields&FRemote)&&(r.selector.kind!=Contact||r.selector.when!=Any))return false;
    const Pulse* patterns[]={&r.vibration,&r.led,&r.gpio};
    for(const Pulse* p:patterns) {
      if(p->count>MAX_PULSES)return false;
      for(unsigned j=0;j<p->count;++j)if(!p->ms[j]||p->ms[j]>60000)return false;
    }
    uint32_t total;
    if(!memchr(r.sound,0,sizeof(r.sound))||!soundDuration(r.sound,total))return false;
    // A pin valid on an older image may now belong to GPS or storage.
    if(r.pin>=0&&!sink.gpioAvailable(r.pin))return false;
    for(unsigned j=0;j<i;++j)if(s.rules[j].used&&s.rules[j].selector==r.selector)return false;
  }
  return true;
}

bool Controller::remoteMessage(const uint8_t* contact,const char* text,uint32_t now,uint32_t timestamp) {
  if(!prefs.enabled||!contact||!text||strncmp(text,"!notify ",8))return false;
  bool allowed=false;
  for(const Rule& r:prefs.rules)if(r.used&&(r.fields&FRemote)&&r.remote
      &&r.selector.kind==Contact&&r.selector.when==Any&&!memcmp(r.selector.key,contact,32))allowed=true;
  if(!allowed)return false;
  uint32_t hash=2166136261UL;
  for(const char* ch=text;*ch;++ch)hash=(hash^uint8_t(*ch))*16777619UL;
  // Signed room delivery and DM retries must not restart the same 15s alert.
  if(timestamp)for(const SeenRemote& seen:remote_seen)
    if(seen.timestamp==timestamp&&seen.hash==hash&&!memcmp(seen.key,contact,32))return true;
  Rule next;
  const char* p=text+8;
  while(*p) {
    const char* end=strchr(p,' ');if(!end)end=p+strlen(p);
    const char* equal=static_cast<const char*>(memchr(p,'=',end-p));
    if(!equal||equal-p>=16||end-equal-1>=96||equal==p)return false;
    char key[16]={},value[96]={};memcpy(key,p,equal-p);memcpy(value,equal+1,end-equal-1);
    uint16_t f=field(key);
    // No pin selection, permission/stop changes, or persistent writes over DM.
    // Unknown or duplicated fields reject the whole notification string.
    if(!f||f==FGpio||f==FRemote||f==FStop||(next.fields&f)||!strcmp(value,"inherit")||!setField(next,f,value))return false;
    p=*end?end+1:end;
    if(*end&&!*p)return false;
  }
  if(!(next.fields&(FVibe|FSound|FLed|FScreen)))return false;
  if(timestamp) {
    SeenRemote& seen=remote_seen[remote_seen_next++%8];
    memcpy(seen.key,contact,32);seen.timestamp=timestamp;seen.hash=hash;
  }
  stop();playing=next;started=now;cycles=0;running=true;remote_playing=true;remote_started=now;
  if(playing.fields&FLed)sink.pulse(Led,false,-1);
  if(playing.fields&FVibe)sink.pulse(Vibration,false,-1);
  startCycle();return true;
}

bool Controller::handle(const char* command,char* reply,size_t size,uint32_t now,bool connected) {
  bool set=!strncmp(command,"set notify.",11),get=!strncmp(command,"get notify.",11);
  bool test=!strncmp(command,"notify.test ",12),del=!strncmp(command,"notify.delete ",14);
  if(!strcmp(command,"notify.stop")) { stop();snprintf(reply,size,"OK - alerts stopped");return true; }
  if(!strcmp(command,"get notify")||!strcmp(command,"get notify.status")) {
    snprintf(reply,size,"> enabled=%s outputs=%u supported=%u active=%s rules=%u",
      prefs.enabled?"on":"off",prefs.outputs,sink.capabilities(),running?"on":"off",MAX_RULES);return true;
  }
  if(!strcmp(command,"get notify.gpio.pins")) {
    size_t n=snprintf(reply,size,"> GPIOs:");
    for(unsigned pin=0;pin<64&&n<size;++pin)if(sink.gpioAvailable(pin))n+=snprintf(reply+n,size-n," %u",pin);
    return true;
  }
  if(!strncmp(command,"get notify.rules",16)) {
    const char* p=command+16;
    if(!*p) {
      size_t n=snprintf(reply,size,"> slots:");
      for(unsigned i=0;i<MAX_RULES&&n<size;++i)if(prefs.rules[i].used)n+=snprintf(reply+n,size-n," %u",i);
      return true;
    }
    uint32_t index;
    if(*p!=' '||!unsignedValue(p+1,index,MAX_RULES-1)||!prefs.rules[index].used)snprintf(reply,size,"Error: rule slot not found");
    else { formatSelector(prefs.rules[index].selector,reply,size); }
    return true;
  }
  if(!set&&!get&&!test&&!del)return false;
  char key[16]={},target[90]={};const char* value=nullptr;
  const char* p=command+(test?12:del?14:11);
  if(set||get) {
    size_t len=strcspn(p," ");
    if(!len||len>=sizeof(key)) { snprintf(reply,size,"Error: unknown notification setting");return true; }
    memcpy(key,p,len);p+=len;
    if(*p==' ')++p;
    if(!strcmp(key,"enabled")||(!*p&&get)||(set&&(!strcmp(p,"on")||!strcmp(p,"off")))) {
      uint16_t f=field(key);
      if(strcmp(key,"enabled") && (f==0||f>FGpio)) { snprintf(reply,size,"Error: unknown notification setting");return true; }
      if(get) { snprintf(reply,size,"> %s",(!strcmp(key,"enabled")?prefs.enabled:bool(prefs.outputs&f))?"on":"off");return true; }
      if(strcmp(p,"on")&&strcmp(p,"off")) { snprintf(reply,size,"Error: use on|off");return true; }
      bool on=!strcmp(p,"on");
      if(on&&f&&!(sink.capabilities()&f)) { snprintf(reply,size,"Error: output unsupported on this build");return true; }
      uint8_t enabled=prefs.enabled,outputs=prefs.outputs;
      if(!strcmp(key,"enabled"))prefs.enabled=on;
      else { prefs.enabled=1;if(on)prefs.outputs|=f;else prefs.outputs&=~f; }
      if(!store.save(prefs)) { prefs.enabled=enabled;prefs.outputs=outputs;snprintf(reply,size,"Error: save failed"); }
      else { stop();snprintf(reply,size,"OK"); }
      return true;
    }
  }
  size_t len=strcspn(p," ");
  if(!len||len>=sizeof(target)) { snprintf(reply,size,"Error: invalid target");return true; }
  memcpy(target,p,len);p+=len;
  if(*p==' ')value=p+1;
  Selector selector;
  if(!parseSelector(target,selector)) { snprintf(reply,size,"Error: use all|contact:64hex|room:64hex|channel:32hex, optional @connected|@disconnected");return true; }
  if(test) {
    if(value) { snprintf(reply,size,"Error: unexpected argument");return true; }
    bool state=selector.when==Any?connected:selector.when==Online;
    bool matched=message(selector.kind==Contact?selector.key:nullptr,selector.kind==Channel?selector.key:nullptr,state,now);
    snprintf(reply,size,"%s",matched?"OK - alert test started":"Error: no enabled matching rule");return true;
  }
  int slot=-1,empty=-1;
  for(unsigned i=0;i<MAX_RULES;++i) {
    if(prefs.rules[i].used&&prefs.rules[i].selector==selector)slot=i;
    if(!prefs.rules[i].used&&empty<0)empty=i;
  }
  if(del) {
    if(value||slot<0) { snprintf(reply,size,"Error: rule not found");return true; }
    Rule previous=prefs.rules[slot];prefs.rules[slot]=Rule();
    if(!store.save(prefs)) { prefs.rules[slot]=previous;snprintf(reply,size,"Error: save failed"); }
    else { stop();snprintf(reply,size,"OK"); }return true;
  }
  uint16_t f=field(key);
  if(!f) { snprintf(reply,size,"Error: unknown notification setting");return true; }
  if(get) {
    if(value)snprintf(reply,size,"Error: unexpected argument");
    else valueText(slot<0?Rule():prefs.rules[slot],f,reply,size);
    return true;
  }
  if(!value||!*value) { snprintf(reply,size,"Error: missing value");return true; }
  if(slot<0)slot=empty;
  if(slot<0) { snprintf(reply,size,"Error: notification rule limit reached");return true; }
  Rule previous=prefs.rules[slot],candidate=previous;
  if(!candidate.used) { candidate=Rule();candidate.used=1;candidate.selector=selector; }
  if(!setField(candidate,f,value)) { snprintf(reply,size,"Error: invalid notification value");return true; }
  if(f<=FGpio && strcmp(value,"off") && strcmp(value,"inherit") && !(sink.capabilities()&f)) {
    snprintf(reply,size,"Error: output unsupported on this build");return true;
  }
  if(f==FGpio&&candidate.pin>=0&&!sink.gpioAvailable(candidate.pin)) {
    snprintf(reply,size,"Error: GPIO reserved or unavailable; use get notify.gpio.pins");return true;
  }
  uint8_t enabled=prefs.enabled;
  prefs.rules[slot]=candidate;prefs.enabled=1;
  if(!store.save(prefs)) { prefs.rules[slot]=previous;prefs.enabled=enabled;snprintf(reply,size,"Error: save failed"); }
  else { stop();snprintf(reply,size,"OK"); }
  return true;
}

} }
