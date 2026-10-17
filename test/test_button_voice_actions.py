"""Run production spoken-button mappings, courtesy audio, and UI transactions natively."""
from pathlib import Path
import unittest

import test_gps_voice_buttons as gps_tests
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

PROMPTS = r'''
#include <cassert>
#include <initializer_list>
#define PIN_BUZZER 25
#define PIN_BUZZER_EN 37
#define MESH_BUTTON_AUDIO_HIL 1
#define HAS_DRV2605 1
#define MESH_GPS_VOICE 1
#include <helpers/ui/buzzer.cpp>
uint32_t now_ms=0;
int main(){
 using namespace mesh::audio;
 struct Mapping {ButtonVoicePrompt prompt;VoiceClip clip;const char* tone;};
 const Mapping mappings[]={
   {ButtonVoicePrompt::Ready,readyClip,nullptr},
   {ButtonVoicePrompt::NotificationCleared,notificationClearedClip,nullptr},
   {ButtonVoicePrompt::AdvertQueued,advertQueuedClip,nullptr},
   {ButtonVoicePrompt::AdvertFailed,advertFailedClip,nullptr},
   {ButtonVoicePrompt::SoundOn,soundOnClip,nullptr},
   {ButtonVoicePrompt::SoundOff,soundOffClip,nullptr},
   {ButtonVoicePrompt::GpsOn,gpsOnClip,"Startup:d=4,o=5,b=160:16c6,16e6,8g6"},
   {ButtonVoicePrompt::GpsOff,gpsOffClip,"Shutdown:d=4,o=5,b=100:8g5,16e5,16c5"},
   {ButtonVoicePrompt::ActionFailed,actionFailedClip,nullptr},
   {ButtonVoicePrompt::UsbSetup,usbSetupClip,nullptr},
   {ButtonVoicePrompt::ShuttingDown,shuttingDownClip,"Shutdown:d=4,o=5,b=100:8g5,16e5,16c5"},
   {ButtonVoicePrompt::Restarting,restartingClip,"Shutdown:d=4,o=5,b=100:8g5,16e5,16c5"},
#ifdef HAS_DRV2605
   {ButtonVoicePrompt::AlertsSoundVibration,alertsSoundVibrationClip,nullptr},
   {ButtonVoicePrompt::AlertsSoundOnly,alertsSoundOnlyClip,nullptr},
   {ButtonVoicePrompt::AlertsVibrationOnly,alertsVibrationOnlyClip,nullptr},
   {ButtonVoicePrompt::AlertsSilent,alertsSilentClip,nullptr},
#endif
 };
 genericBuzzer buzzer;buzzer.begin();
 auto finish=[&]{
   assert(!registers.ENABLE);
   rtttl::finish();buzzer.loop();
   now_ms+=99;buzzer.loop();assert(!registers.ENABLE);
   ++now_ms;buzzer.loop();
   assert(HwPWM3.owned&&HwPWM3.pin&&HwPWM3.bound_pin==PIN_BUZZER);
   unsigned sequence=0,events=0;
   while(registers.ENABLE){
     assert(++events<2000);
     registers.EVENTS_SEQEND[sequence]=1;PWM3_IRQHandler();sequence^=1;
   }
   buzzer.loop();assert(!buzzer.isPlaying());
   assert(!HwPWM3.owned&&!HwPWM3.pin&&!irq_enabled);
   assert(pin_states[PIN_BUZZER]==LOW);
 };
 for(const auto& entry:mappings){
   auto actual=buttonVoiceClip(entry.prompt);
   assert(actual.data==entry.clip.data&&actual.bytes==entry.clip.bytes);
   assert(actual.prefix==entry.clip.prefix&&actual.prefix_bytes==entry.clip.prefix_bytes);
   assert(actual.samples==entry.clip.samples&&actual.sample_rate==entry.clip.sample_rate);
   assert(actual.valid()&&actual.bytes+actual.prefix_bytes==(actual.samples+1)/2);
   assert(actual.sample_rate==8000||actual.sample_rate==16000);
   VoiceDecoder decoder;decoder.begin(actual);
   unsigned count=0,nonzero=0;
   while(!decoder.done()){nonzero+=decoder.next()!=0;++count;}
   assert(count==actual.samples&&nonzero>count/2);
   auto started=gps_voice.started(),completed=gps_voice.completed(),tones=rtttl::begins;
   assert(buzzer.speakButton(entry.prompt)&&buzzer.isPlaying());
   if(entry.tone){assert(rtttl::melody==entry.tone&&rtttl::begins==tones+1);}
   else {assert(rtttl::begins==tones&&!rtttl::isPlaying());}
   finish();
   assert(gps_voice.started()==started+1&&gps_voice.completed()==completed+1);
 }
 // Invalid values must reject without preempting a valid tone or active voice.
 const auto invalid=static_cast<ButtonVoicePrompt>(255);
 assert(!buttonVoiceClip(invalid).data);
 assert(buzzer.speakGps(true));
 auto tones=rtttl::begins,started=gps_voice.started();
 assert(!buzzer.speakButton(invalid));
 assert(buzzer.isPlaying()&&rtttl::isPlaying()&&rtttl::begins==tones);
#ifndef HAS_DRV2605
 for(auto prompt:{ButtonVoicePrompt::AlertsSoundVibration,ButtonVoicePrompt::AlertsSoundOnly,
                 ButtonVoicePrompt::AlertsVibrationOnly,ButtonVoicePrompt::AlertsSilent}){
   assert(!buttonVoiceClip(prompt).data&&!buzzer.speakButton(prompt,true));
   assert(buzzer.isPlaying()&&rtttl::isPlaying()&&rtttl::begins==tones);
 }
#endif
 finish();
 assert(gps_voice.started()==started+1);
 assert(buzzer.speakButton(ButtonVoicePrompt::Ready));
 buzzer.loop();now_ms+=100;buzzer.loop();
 assert(registers.ENABLE&&buzzer.isPlaying());
 started=gps_voice.started();
 assert(!buzzer.speakButton(invalid,true));
 assert(registers.ENABLE&&gps_voice.started()==started);
#ifndef HAS_DRV2605
 for(auto prompt:{ButtonVoicePrompt::AlertsSoundVibration,ButtonVoicePrompt::AlertsSoundOnly,
                 ButtonVoicePrompt::AlertsVibrationOnly,ButtonVoicePrompt::AlertsSilent}){
   assert(!buzzer.speakButton(prompt,true));
   assert(registers.ENABLE&&gps_voice.started()==started);
 }
#endif
 unsigned sequence=0,events=0;
 while(registers.ENABLE){
   assert(++events<2000);registers.EVENTS_SEQEND[sequence]=1;
   PWM3_IRQHandler();sequence^=1;
 }
 buzzer.loop();assert(!buzzer.isPlaying());
 // A busy PWM fails the deferred voice start, without leaking ownership.
 HwPWM3.reject=true;
 assert(buzzer.speakButton(ButtonVoicePrompt::Ready));
 buzzer.loop();now_ms+=100;buzzer.loop();
 assert(!buzzer.isPlaying()&&!registers.ENABLE&&!HwPWM3.owned&&!irq_enabled);
}
'''

COURTESY = r'''
#include <cassert>
#define PIN_BUZZER 25
#define PIN_BUZZER_EN 37
#define MESH_BUTTON_AUDIO_HIL 1
#define MESH_GPS_VOICE 1
#include <helpers/ui/buzzer.cpp>
uint32_t now_ms=0;
int main(){
 genericBuzzer buzzer;buzzer.begin();buzzer.quiet(true);
 assert(buzzer.isQuiet()&&pin_states[PIN_BUZZER_EN]==LOW);
 auto before=gps_voice.started();
 assert(buzzer.speakButton(ButtonVoicePrompt::Ready));
 assert(!buzzer.isPlaying()&&gps_voice.started()==before);
 assert(pin_states[PIN_BUZZER_EN]==LOW);
 // The explicit sound-off confirmation is one-shot: it never unmutes alerts.
 assert(buzzer.speakButton(ButtonVoicePrompt::SoundOff,true));
 assert(buzzer.isQuiet()&&buzzer.isPlaying()&&pin_states[PIN_BUZZER_EN]==HIGH);
 buzzer.loop();now_ms+=100;buzzer.loop();
 assert(registers.ENABLE&&gps_voice.started()==before+1);
 unsigned sequence=0,events=0;
 while(registers.ENABLE){
   assert(++events<2000);registers.EVENTS_SEQEND[sequence]=1;
   PWM3_IRQHandler();sequence^=1;
 }
 assert(buzzer.isPlaying()); // IRQ cleanup is deferred to the foreground.
 buzzer.loop();
 assert(buzzer.isQuiet()&&!buzzer.isPlaying()&&pin_states[PIN_BUZZER_EN]==LOW);
 assert(pin_states[PIN_BUZZER]==LOW&&!HwPWM3.owned&&!HwPWM3.pin&&!irq_enabled);
 auto tones=rtttl::begins;
 buzzer.play("normal:d=4,o=5,b=120:c");
 assert(!buzzer.isPlaying()&&rtttl::begins==tones&&pin_states[PIN_BUZZER_EN]==LOW);
 // Every cancellation path releases the courtesy output without changing mute.
 assert(buzzer.speakButton(ButtonVoicePrompt::SoundOff,true));
 buzzer.stop();assert(buzzer.isQuiet()&&!buzzer.isPlaying());
 assert(pin_states[PIN_BUZZER_EN]==LOW&&pin_states[PIN_BUZZER]==LOW);
 assert(buzzer.speakButton(ButtonVoicePrompt::SoundOff,true));
 buzzer.quiet(true);assert(!buzzer.isPlaying()&&pin_states[PIN_BUZZER_EN]==LOW);
 // Muted ordinary notifications must preserve queued and active courtesy voice.
 assert(buzzer.speakButton(ButtonVoicePrompt::SoundOff,true));
 before=gps_voice.started();auto cancelled=gps_voice.cancelled();tones=rtttl::begins;
 buzzer.play("normal:d=4,o=5,b=120:c");
 assert(buzzer.isQuiet()&&buzzer.isPlaying()&&pin_states[PIN_BUZZER_EN]==HIGH);
 assert(!registers.ENABLE&&gps_voice.started()==before&&rtttl::begins==tones);
 buzzer.loop();now_ms+=100;buzzer.loop();
 assert(registers.ENABLE&&gps_voice.started()==before+1);
 buzzer.play("normal:d=4,o=5,b=120:c");
 assert(buzzer.isQuiet()&&buzzer.isPlaying()&&registers.ENABLE);
 assert(gps_voice.cancelled()==cancelled&&rtttl::begins==tones&&pin_states[PIN_BUZZER_EN]==HIGH);
 // Ordinary sound still preempts voice as soon as the user actually unmutes.
 buzzer.quiet(false);buzzer.play("normal:d=4,o=5,b=120:c");
 assert(!registers.ENABLE&&gps_voice.cancelled()==cancelled+1);
 assert(rtttl::isPlaying()&&rtttl::begins==tones+1&&!HwPWM3.owned&&!irq_enabled);
 rtttl::finish();buzzer.loop();assert(!buzzer.isPlaying());buzzer.quiet(true);
 // Starting a courtesy direction tone also respects cancellation in its gap.
 assert(buzzer.speakButton(ButtonVoicePrompt::GpsOff,true));
 assert(buzzer.isQuiet()&&rtttl::isPlaying()&&pin_states[PIN_BUZZER_EN]==HIGH);
 rtttl::finish();buzzer.loop();buzzer.stop();
 now_ms+=1000;buzzer.loop();
 assert(!buzzer.isPlaying()&&pin_states[PIN_BUZZER_EN]==LOW&&!registers.ENABLE);
 // An unavailable player must restore the sleeping amplifier immediately.
 HwPWM3.reject=true;
 assert(buzzer.speakButton(ButtonVoicePrompt::SoundOff,true));
 buzzer.loop();now_ms+=100;buzzer.loop();
 assert(buzzer.isQuiet()&&!buzzer.isPlaying()&&pin_states[PIN_BUZZER_EN]==LOW);
}
'''

LEGACY_ORDINARY_PLAY = r'''
#include <cassert>
#define PIN_BUZZER 25
#define PIN_BUZZER_EN 37
#define MESH_GPS_VOICE 0
#include <helpers/ui/buzzer.cpp>
uint32_t now_ms=0;
int main(){
 genericBuzzer buzzer;buzzer.begin();buzzer.quiet(true);
 assert(buzzer.speakButton(ButtonVoicePrompt::GpsOff,true)&&rtttl::isPlaying());
 auto tones=rtttl::begins;
 // Non-voice builds retain the original cancel-before-quiet behavior.
 buzzer.play("normal:d=4,o=5,b=120:c");
 assert(buzzer.isQuiet()&&!buzzer.isPlaying()&&rtttl::begins==tones);
 assert(pin_states[PIN_BUZZER_EN]==LOW);
 buzzer.quiet(false);assert(buzzer.speakGps(true));tones=rtttl::begins;
 buzzer.play("normal:d=4,o=5,b=120:c");
 assert(!buzzer.isQuiet()&&buzzer.isPlaying()&&rtttl::begins==tones+1);
 assert(rtttl::melody=="normal:d=4,o=5,b=120:c");
}
'''

UI_ACTIONS = r'''
#include <cassert>
#include <algorithm>
#include <string>
#include <vector>
#include <helpers/ui/ButtonVoice.h>
#include "examples/companion_radio/ui-orig/Button.h"
#define PIN_BUZZER 25
#define MESH_GPS_VOICE 1
#define ENV_INCLUDE_GPS 1
#define MESH_DEBUG_PRINTLN(...) ((void)0)
uint32_t now_ms=0;
static std::vector<std::string> events;
enum class UIEventType{contactMessage,channelMessage,ack,roomMessage,newContactMessage,none};
namespace mesh {
 namespace notify {enum {Sound=1};}
 namespace ui {
  enum class DisplayWake {Button};
  inline uint8_t radioProfileDisplayPageCount(bool,uint8_t){return 4;}
 }
}
struct Display {
 bool on=true;unsigned wakes=0;
 bool isOn(){return on;}
 void wake(mesh::ui::DisplayWake){++wakes;on=true;}
};
static uint8_t radioProfileStatusPageCount(Display&,bool){return 2;}
struct CompanionNodePrefs {uint8_t gps_enabled=0,buzzer_quiet=0;};
struct SensorManager {
 bool enabled=false,available=true,reject=false;
 bool setSettingValue(const char*,const char* value){
   events.push_back("gps");
   if(!available||reject)return false;
   enabled=value[0]=='1';return true;
 }
 const char* getSettingByKey(const char*){return available?(enabled?"1":"0"):nullptr;}
};
static SensorManager sensors;
struct MyMesh {
 CompanionNodePrefs _prefs;
 bool save_ok=true,advert_ok=true,notification_active=false,output_muted=false,dual_radio=false;
 unsigned saves=0,adverts=0,notification_buttons=0,mute_calls=0,cli_entries=0;
 std::vector<bool> saved_quiet;
 bool savePrefs(){++saves;events.push_back("save");saved_quiet.push_back(_prefs.buzzer_quiet);return save_ok;}
 template<class T> bool savePreference(T& p,T v){T old=p;p=v;if(savePrefs())return true;p=old;return false;}
 void applyGpsPrefs(){sensors.setSettingValue("gps",_prefs.gps_enabled?"1":"0");}
 bool setGpsEnabled(bool);
 bool advert(){++adverts;events.push_back("advert");return advert_ok;}
 bool notificationButton(){++notification_buttons;bool cleared=notification_active;notification_active=false;return cleared;}
 void setNotificationOutputMute(int,bool muted){++mute_calls;output_muted=muted;events.push_back("mute");}
 bool isDualRadioActive(){return dual_radio;}
 void enterCLIRescue(){++cli_entries;events.push_back("cli");}
};
static MyMesh the_mesh;
struct Buzzer {
 bool muted=false,playing=false,accept=true;
 unsigned plays=0,notification_plays=0,stops=0,notification_stops=0,shutdowns=0;
 std::vector<ButtonVoicePrompt> prompts;
 std::vector<bool> courtesy;
 bool speakButton(ButtonVoicePrompt prompt,bool allowQuiet=false){
   events.push_back("speak");prompts.push_back(prompt);courtesy.push_back(allowQuiet);
   playing=accept&&(!muted||allowQuiet);return accept;
 }
 bool speakGps(bool value){return speakButton(value?ButtonVoicePrompt::GpsOn:ButtonVoicePrompt::GpsOff);}
 void quiet(bool q){muted=q;events.push_back("quiet");}
 bool isQuiet(){return muted;}
 bool isPlaying(){return playing;}
 void stop(){++stops;playing=false;events.push_back("stop");}
 void stopNotification(){++notification_stops;playing=false;events.push_back("notification_stop");}
 void shutdown(){++shutdowns;playing=!muted;}
 void play(const char*){++plays;}
 void playNotification(const char*){++notification_plays;}
 void loop(){}
};
struct Board {
 unsigned offs=0,reboots=0;
 void powerOff(){++offs;events.push_back("off");}
 void reboot(){++reboots;events.push_back("reboot");}
};
struct UITask {
 Display* _display=nullptr;SensorManager* _sensors=&sensors;
 CompanionNodePrefs* _node_prefs=&the_mesh._prefs;
 Buzzer buzzer;Board board;Board* _board=&board;
 char _alert[80]={},_origin[80]={},_msg[80]={};
 bool _need_refresh=false,_displayWasOn=false,_button_notification_cleared=false;
 bool _shutdown_pending=false,_shutdown_restart=false;
 bool _startup_complete=false;
 uint32_t _shutdown_started_at=0,ui_started_at=0;
 uint8_t _radio_profile_display_page=0,_notification_outputs=mesh::notify::Sound;
 bool guard_ok=true,guard_reject_drain=false;unsigned guards=0,preview_clears=0;
 bool prepareForShutdown(){++guards;events.push_back("guard");return guard_ok&&(!guard_reject_drain||guards==1);}
 bool shouldPlayMessageTone()const{return true;}
 bool isPairingScreenActive()const{return false;}
 void resetRadioProfileDisplayPage(){_radio_profile_display_page=0;}
 void clearMsgPreview(){++preview_clears;_origin[0]=_msg[0]=0;}
 void notify(UIEventType);void notificationMelody(const char*);
 void handleButtonAnyPress();void handleButtonShortPress();
 void handleButtonDoublePress();void handleButtonTriplePress();
 void handleButtonQuadruplePress();void handleButtonLongPress();
 void shutdown(bool restart=false);void servicePendingShutdown();
};
@METHODS@
int main(){
 auto reset=[](){the_mesh=MyMesh{};sensors=SensorManager{};events.clear();now_ms=10000;};
 auto last=[](const UITask& ui,ButtonVoicePrompt expected){assert(!ui.buzzer.prompts.empty()&&ui.buzzer.prompts.back()==expected);};
 reset();UITask single;
 single.handleButtonAnyPress();single.handleButtonShortPress();
 last(single,ButtonVoicePrompt::Ready);
 the_mesh.notification_active=true;
 single.handleButtonAnyPress();single.handleButtonAnyPress(); // The first click really acknowledged it.
 single.handleButtonShortPress();last(single,ButtonVoicePrompt::NotificationCleared);
 single.handleButtonAnyPress();single.handleButtonShortPress();last(single,ButtonVoicePrompt::Ready);
 assert(single.buzzer.prompts.size()==3);
 // Multi-click actions consume the acknowledgement latch instead of leaking it to a later single.
 the_mesh.notification_active=true;
 single.handleButtonAnyPress();single.handleButtonDoublePress();last(single,ButtonVoicePrompt::AdvertQueued);
 single.handleButtonAnyPress();single.handleButtonShortPress();last(single,ButtonVoicePrompt::Ready);
 // An attached display retains its existing preview/wake behavior, with no Ready chatter.
 Display display;single._display=&display;single._displayWasOn=true;
 strcpy(single._origin,"sender");strcpy(single._msg,"message");
 auto prompt_count=single.buzzer.prompts.size();
 single.handleButtonAnyPress();single.handleButtonShortPress();
 assert(single.preview_clears==1&&single.buzzer.prompts.size()==prompt_count&&display.wakes==1);

 reset();UITask advert;
 advert.handleButtonDoublePress();last(advert,ButtonVoicePrompt::AdvertQueued);
 assert((events==std::vector<std::string>{"advert","speak"}));
 the_mesh.advert_ok=false;events.clear();
 advert.handleButtonDoublePress();last(advert,ButtonVoicePrompt::AdvertFailed);
 assert(the_mesh.adverts==2&&strstr(advert._alert,"failed"));
 assert((events==std::vector<std::string>{"advert","speak"}));

 reset();UITask mute;
 mute.handleButtonTriplePress();last(mute,ButtonVoicePrompt::SoundOff);
 assert(mute.buzzer.muted&&the_mesh._prefs.buzzer_quiet&&the_mesh.output_muted);
 assert(mute.buzzer.courtesy.back()&&mute.buzzer.playing);
 assert((events==std::vector<std::string>{"save","quiet","mute","speak"}));
 events.clear();mute.handleButtonTriplePress();last(mute,ButtonVoicePrompt::SoundOn);
 assert(!mute.buzzer.muted&&!the_mesh._prefs.buzzer_quiet&&!the_mesh.output_muted);
 assert(mute.buzzer.courtesy.back()&&the_mesh.saves==2&&the_mesh.mute_calls==2);
 // Failed commits preserve runtime quiet, stored preference, and output routing separately.
 for(bool old_quiet:{false,true}){
   mute.buzzer.muted=old_quiet;the_mesh._prefs.buzzer_quiet=old_quiet;
   the_mesh.output_muted=old_quiet;the_mesh.save_ok=false;events.clear();
   auto mutes=the_mesh.mute_calls;
   mute.handleButtonTriplePress();last(mute,ButtonVoicePrompt::ActionFailed);
   assert(mute.buzzer.muted==old_quiet&&the_mesh._prefs.buzzer_quiet==old_quiet);
   assert(the_mesh.output_muted==old_quiet&&the_mesh.mute_calls==mutes);
   assert(mute.buzzer.courtesy.back()&&mute.buzzer.playing&&strstr(mute._alert,"failed"));
   assert(std::find(events.begin(),events.end(),"mute")==events.end());
 }
 // Even an initially inconsistent runtime/preference pair is not rewritten on failure.
 mute.buzzer.muted=false;the_mesh._prefs.buzzer_quiet=1;
 mute.handleButtonTriplePress();assert(!mute.buzzer.muted&&the_mesh._prefs.buzzer_quiet==1);

 reset();UITask gps;
#if ENV_INCLUDE_GPS == 1
 gps.handleButtonQuadruplePress();last(gps,ButtonVoicePrompt::GpsOn);
 assert(sensors.enabled&&the_mesh._prefs.gps_enabled&&the_mesh.saves==1);
 assert((events==std::vector<std::string>{"gps","save","speak"}));
 events.clear();gps.handleButtonQuadruplePress();last(gps,ButtonVoicePrompt::GpsOff);
 assert(!sensors.enabled&&!the_mesh._prefs.gps_enabled&&the_mesh.saves==2);
 the_mesh.save_ok=false;events.clear();
 gps.handleButtonQuadruplePress();last(gps,ButtonVoicePrompt::ActionFailed);
 assert(!sensors.enabled&&!the_mesh._prefs.gps_enabled&&strstr(gps._alert,"failed"));
 assert((events==std::vector<std::string>{"gps","save","gps","speak"}));
 the_mesh.save_ok=true;sensors.reject=true;
 auto saves=the_mesh.saves;gps.handleButtonQuadruplePress();last(gps,ButtonVoicePrompt::ActionFailed);
 assert(the_mesh.saves==saves&&!sensors.enabled&&!the_mesh._prefs.gps_enabled);
 sensors.reject=false;sensors.available=false;
 gps.handleButtonQuadruplePress();last(gps,ButtonVoicePrompt::ActionFailed);
 assert(the_mesh.saves==saves);
#else
 gps.handleButtonQuadruplePress();last(gps,ButtonVoicePrompt::ActionFailed);
 assert(the_mesh.saves==0&&!sensors.enabled&&!the_mesh._prefs.gps_enabled);
#endif
 gps._sensors=nullptr;gps.handleButtonQuadruplePress();last(gps,ButtonVoicePrompt::ActionFailed);

 reset();UITask early;
 now_ms=7999;early.handleButtonLongPress();last(early,ButtonVoicePrompt::UsbSetup);
 assert(the_mesh.cli_entries==1&&early.guards==0&&!early._shutdown_pending);
 assert((events==std::vector<std::string>{"cli","speak"}));
 // Uptime arithmetic is unsigned even when boot began just before clock wrap.
 early.ui_started_at=0xfffffff0;now_ms=7982;
 early.handleButtonLongPress();last(early,ButtonVoicePrompt::UsbSetup);
 assert(the_mesh.cli_entries==2);

 reset();UITask rejected;rejected.guard_ok=false;
 rejected.shutdown();last(rejected,ButtonVoicePrompt::ActionFailed);
 assert(!rejected._shutdown_pending&&!rejected.board.offs&&!rejected.board.reboots);
 rejected.servicePendingShutdown();assert(rejected.guards==1&&!rejected.board.offs);

 reset();UITask shutdown;
 now_ms=8000;shutdown.handleButtonLongPress();last(shutdown,ButtonVoicePrompt::ShuttingDown);
 assert(shutdown._shutdown_pending&&shutdown.guards==1&&!shutdown.board.offs);
 assert((events==std::vector<std::string>{"guard","speak"}));
 // Shutdown callbacks return immediately; draining is foreground service, not a busy wait.
 assert(now_ms==8000);
 auto before=shutdown.buzzer.prompts.size();
 auto adverts=the_mesh.adverts,shutdown_saves=the_mesh.saves;
 shutdown.handleButtonAnyPress();shutdown.handleButtonShortPress();shutdown.handleButtonDoublePress();
 shutdown.handleButtonTriplePress();shutdown.handleButtonQuadruplePress();shutdown.handleButtonLongPress();
 shutdown.shutdown(true);shutdown.notify(UIEventType::ack);shutdown.notificationMelody("notification");
 assert(shutdown.buzzer.prompts.size()==before&&the_mesh.adverts==adverts&&the_mesh.saves==shutdown_saves);
 assert(!shutdown.buzzer.plays&&!shutdown.buzzer.notification_plays&&shutdown.guards==1);
 now_ms+=4999;shutdown.servicePendingShutdown();assert(!shutdown.board.offs&&shutdown._shutdown_pending);
 shutdown.buzzer.playing=false;shutdown.servicePendingShutdown();
 assert(shutdown.board.offs==1&&!shutdown._shutdown_pending&&shutdown.guards==2&&shutdown.buzzer.stops==1);
 shutdown.servicePendingShutdown();assert(shutdown.board.offs==1);

 reset();UITask restart;
 now_ms=0xfffffff0;restart.shutdown(true);last(restart,ButtonVoicePrompt::Restarting);
 now_ms+=4999;restart.servicePendingShutdown();assert(!restart.board.reboots&&restart._shutdown_pending);
 ++now_ms;restart.servicePendingShutdown();
 assert(restart.board.reboots==1&&!restart.board.offs&&!restart._shutdown_pending&&!restart.buzzer.playing);

 reset();UITask drain_rejected;drain_rejected.guard_reject_drain=true;
 drain_rejected.shutdown();drain_rejected.buzzer.playing=false;drain_rejected.servicePendingShutdown();
 last(drain_rejected,ButtonVoicePrompt::ActionFailed);
 assert(!drain_rejected._shutdown_pending&&!drain_rejected.board.offs&&drain_rejected.guards==2);

 // Use the actual click decoder to establish that holding does not turn into a Ready/advert.
 reset();UITask held;now_ms=100;
 Button button(6,HIGH);button.begin();
 static_assert(BUTTON_LONG_PRESS_TIME_MS==3000,"three-second hold");
 button.onAnyPress([&]{held.handleButtonAnyPress();});
 button.onShortPress([&]{held.handleButtonShortPress();});
 button.onDoublePress([&]{held.handleButtonDoublePress();});
 button.onLongPress([&]{held.handleButtonLongPress();});
 assert(button.injectPresses(1,3500));
 for(unsigned i=0;i<500;++i){now_ms+=10;button.update();}
 assert(held.buzzer.prompts.size()==1&&the_mesh.adverts==0&&the_mesh.cli_entries==1);
 last(held,ButtonVoicePrompt::UsbSetup);
}
'''

NOTIFICATION_ACK = r'''
#include <cassert>
#include <initializer_list>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmisleading-indentation"
#include <helpers/CompanionNotificationPolicy.h>
#pragma GCC diagnostic pop
uint32_t now_ms=0;
using namespace mesh::notify;
struct Probe : Sink,Store {
 unsigned stops=0;
 uint8_t capabilities()const override{return Sound;}
 bool gpioAvailable(uint8_t)const override{return false;}
 void pulse(Output,bool,int8_t)override{}
 void melody(const char* value)override{if(!value)++stops;}
 void screen(int8_t)override{}
 bool save(const Settings&)override{return true;}
};
struct MyMesh {
 Controller _notifications;
 explicit MyMesh(Probe& probe):_notifications(probe,probe){}
 @WRAPPER@
};
int main(){
 Probe probe;MyMesh mesh(probe);
 assert(!mesh.notificationButton()&&probe.stops==0);
 auto& settings=mesh._notifications.settings();settings.enabled=1;settings.outputs=Sound;
 auto& rule=settings.rules[0];rule.used=1;
 assert(setField(rule,FSound,"test:d=4,o=5,b=120:c"));
 assert(setField(rule,FStop,"button"));
 assert(mesh._notifications.message(nullptr,nullptr,false,100));
 assert(mesh.notificationButton()&&!mesh._notifications.active()&&probe.stops==1);
 assert(!mesh.notificationButton()&&probe.stops==1);
 // An active alert with another stop policy was not actually cleared by input.
 for(const char* policy:{"connected","never"}){
   assert(setField(rule,FStop,policy));
   assert(mesh._notifications.message(nullptr,nullptr,false,200));
   auto stops=probe.stops;
   assert(!mesh.notificationButton()&&mesh._notifications.active()&&probe.stops==stops);
   mesh._notifications.stop();
 }
}
'''

NOTIFICATION_MELODY = r'''
#include <cassert>
#include <cstdint>
#define PIN_BUZZER 25
#define MESH_GPS_VOICE @VOICE@
uint32_t now_ms=0;
struct Buzzer {
 bool muted=true,playing=true,control_voice=true;unsigned melodies=0,stops=0,notification_stops=0;
 bool isQuiet(){return muted;}
 void playNotification(const char*){++melodies;playing=true;control_voice=false;}
 void stop(){++stops;playing=false;}
 void stopNotification(){++notification_stops;if(!MESH_GPS_VOICE||!control_voice)playing=false;}
};
struct UITask {
 Buzzer buzzer;bool _shutdown_pending=false;
 void notificationMelody(const char*);
};
@METHOD@
int main(){
 UITask ui;
 // A muted programmable alert must not preempt the final courtesy confirmation.
 ui.notificationMelody("ordinary");
#if MESH_GPS_VOICE
 assert(ui.buzzer.melodies==0&&ui.buzzer.playing&&ui.buzzer.stops==0);
#else
 assert(ui.buzzer.melodies==1&&ui.buzzer.playing&&ui.buzzer.stops==0);
#endif
 // Background stop requests retain control speech while quiet.
 ui.notificationMelody(nullptr);
 assert(ui.buzzer.stops==0&&ui.buzzer.notification_stops==1);
#if MESH_GPS_VOICE
 assert(ui.buzzer.playing);
#else
 assert(!ui.buzzer.playing);
#endif
 ui.buzzer.muted=false;ui.notificationMelody("ordinary");
#if MESH_GPS_VOICE
 assert(ui.buzzer.melodies==1&&ui.buzzer.playing);
#else
 assert(ui.buzzer.melodies==2&&ui.buzzer.playing);
#endif
 ui._shutdown_pending=true;
 ui.notificationMelody("ordinary");ui.notificationMelody(nullptr);
#if MESH_GPS_VOICE
 assert(ui.buzzer.melodies==1&&ui.buzzer.stops==0&&ui.buzzer.notification_stops==1&&ui.buzzer.playing);
#else
 assert(ui.buzzer.melodies==3&&ui.buzzer.stops==0&&ui.buzzer.notification_stops==2&&!ui.buzzer.playing);
#endif
}
'''

NOTIFICATION_STOPS = r'''
#include <cassert>
#include <initializer_list>
#define PIN_BUZZER 25
#define PIN_BUZZER_EN 37
#define MESH_BUTTON_AUDIO_HIL 1
#define MESH_GPS_VOICE @VOICE@
#include <helpers/ui/buzzer.cpp>
uint32_t now_ms=0;
int main(){
 genericBuzzer buzzer;buzzer.begin();
 buzzer.playNotification("ordinary:d=4,o=5,b=120:c");
 assert(buzzer.isPlaying()&&rtttl::isPlaying());
 buzzer.stopNotification();assert(!buzzer.isPlaying()&&!rtttl::isPlaying());
 assert(buzzer.speakGps(true)&&rtttl::isPlaying());
 buzzer.stopNotification();
#if MESH_GPS_VOICE
 // A stale notification stop cannot cut off the direction tone or its gap.
 assert(buzzer.isPlaying()&&rtttl::isPlaying()&&!registers.ENABLE);
 auto starts=gps_voice.started(),cancelled=gps_voice.cancelled();
 rtttl::finish();buzzer.loop();
 assert(buzzer.isPlaying()&&!rtttl::isPlaying()&&!registers.ENABLE);
 buzzer.stopNotification();assert(buzzer.isPlaying());
 now_ms+=99;buzzer.loop();buzzer.stopNotification();
 assert(buzzer.isPlaying()&&!registers.ENABLE);
 ++now_ms;buzzer.loop();assert(registers.ENABLE&&gps_voice.started()==starts+1);
 buzzer.stopNotification();
 assert(registers.ENABLE&&HwPWM3.owned&&buzzer.isPlaying()&&gps_voice.cancelled()==cancelled);
 auto drain=[&]{
   unsigned sequence=0,events=0;
   while(registers.ENABLE){
     assert(++events<2000);buzzer.stopNotification();
     assert(registers.ENABLE&&HwPWM3.owned&&gps_voice.cancelled()==cancelled);
     registers.EVENTS_SEQEND[sequence]=1;PWM3_IRQHandler();sequence^=1;
   }
   // Completed DMA still owns its hardware until foreground cleanup.
   assert(buzzer.isPlaying()&&HwPWM3.owned);
   buzzer.stopNotification();assert(HwPWM3.owned&&gps_voice.cancelled()==cancelled);
   buzzer.loop();assert(!buzzer.isPlaying()&&!HwPWM3.owned&&!HwPWM3.pin&&!irq_enabled);
 };
 drain();
 // The same protection covers a courtesy confirmation while alerts are muted.
 buzzer.quiet(true);assert(buzzer.speakButton(ButtonVoicePrompt::ActionFailed,true));
 buzzer.stopNotification();assert(buzzer.isPlaying()&&buzzer.isQuiet());
 buzzer.loop();now_ms+=100;buzzer.loop();assert(registers.ENABLE);
 drain();
 assert(pin_states[PIN_BUZZER_EN]==LOW&&pin_states[PIN_BUZZER]==LOW);
#else
 // Without voice, this API remains an ordinary tone stop.
 assert(!buzzer.isPlaying()&&!rtttl::isPlaying());
 buzzer.quiet(true);
#endif
 buzzer.playNotification("ordinary:d=4,o=5,b=120:c");
 assert(buzzer.isPlaying()&&pin_states[PIN_BUZZER_EN]==HIGH);
 buzzer.stopNotification();
 assert(!buzzer.isPlaying()&&!rtttl::isPlaying()&&buzzer.isQuiet());
 assert(pin_states[PIN_BUZZER_EN]==LOW);
}
'''

X1_ALERT_MODES = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/ui/ButtonVoice.h>
#define PIN_BUZZER 25
#define HAS_DRV2605 1
#define MESH_GPS_VOICE @VOICE@
#define MESH_DEBUG_PRINTLN(...) ((void)0)
uint32_t now_ms=0;
static std::vector<std::string> mode_events;
enum class UIEventType{ack};
namespace mesh{namespace notify{enum{Sound=1,Vibration=2};}}
struct CompanionNodePrefs{uint8_t buzzer_quiet=0,vibe_quiet=0;};
struct MyMesh {
 CompanionNodePrefs _prefs;
 bool save_ok=true,mask_save_ok=true;
 unsigned saves=0,mutes=0;
 bool sound_muted=false,vibration_muted=false;
 std::vector<unsigned> saved_modes;
 bool savePrefs(){
   mode_events.push_back("save");
   ++saves;saved_modes.push_back((_prefs.buzzer_quiet?2:0)|(_prefs.vibe_quiet?1:0));
   return save_ok;
 }
 void setNotificationOutputMute(int output,bool quiet){
   ++mutes;
   mode_events.push_back("mask");
   if(!mask_save_ok)return;
   if(output==mesh::notify::Sound)sound_muted=quiet;
   else if(output==mesh::notify::Vibration)vibration_muted=quiet;
   else assert(false);
 }
};
static MyMesh the_mesh;
struct Buzzer {
 bool muted=false;
 std::vector<ButtonVoicePrompt> prompts;
 std::vector<bool> courtesy;
 void quiet(bool value){muted=value;}
 bool isQuiet(){return muted;}
 bool speakButton(ButtonVoicePrompt prompt,bool allowQuiet=false){
   mode_events.push_back("voice");
   prompts.push_back(prompt);courtesy.push_back(allowQuiet);return true;
 }
};
struct Vibration {
 bool muted=false,active=false;unsigned triggers=0,stops=0,pulses=0;
 void quiet(bool value){muted=value;}
 bool isQuiet(){return muted;}
 void trigger(bool confirmation=false){assert(confirmation);++triggers;}
 void stop(){++stops;active=false;mode_events.push_back("stop");}
 void pulse(bool on){++pulses;active=on;}
};
struct UITask {
 CompanionNodePrefs* _node_prefs=&the_mesh._prefs;
 Buzzer buzzer;Vibration vibration;
 bool _shutdown_pending=false,_button_notification_cleared=false,_need_refresh=false;
 char _alert[80]={};unsigned acknowledgements=0;
 void notify(UIEventType){++acknowledgements;}
 void handleButtonTriplePress();
 void notificationVibration(bool);
};
@METHOD@
@VIBRATION_METHOD@
int main(){
 UITask ui;
 const ButtonVoicePrompt expected[]={
   ButtonVoicePrompt::AlertsSoundVibration,ButtonVoicePrompt::AlertsSoundOnly,
   ButtonVoicePrompt::AlertsVibrationOnly,ButtonVoicePrompt::AlertsSilent,
 };
 unsigned vibration_triggers=0,vibration_stops=0;
 for(unsigned step=1;step<=8;++step){
   const auto mode=step&3;
   ui._button_notification_cleared=true;
   auto saves=the_mesh.saves;
   mode_events.clear();
   ui.vibration.active=true;
   ui.handleButtonTriplePress();
   assert(the_mesh.saves==saves+1&&the_mesh.saved_modes.back()==mode);
   assert(the_mesh._prefs.buzzer_quiet==!!(mode&2));
   assert(the_mesh._prefs.vibe_quiet==!!(mode&1));
   assert(ui.buzzer.muted==!!(mode&2)&&ui.vibration.muted==!!(mode&1));
   assert(the_mesh.sound_muted==!!(mode&2)&&the_mesh.vibration_muted==!!(mode&1));
   if(!(mode&1))++vibration_triggers;
   assert(ui.vibration.triggers==vibration_triggers&&ui._need_refresh);
#if MESH_GPS_VOICE
   if(mode&1){
     ++vibration_stops;assert(!ui.vibration.active);
     assert(mode_events.size()>=2&&mode_events[0]=="save"&&mode_events[1]=="stop");
   }
   assert(ui.vibration.stops==vibration_stops);
   assert(ui.buzzer.prompts.size()==step&&ui.buzzer.prompts.back()==expected[mode]);
   // Selecting silent or vibration-only gets a single final confirmation,
   // without unmuting saved alerts or notification routing.
   assert(ui.buzzer.courtesy.back()&&!ui._button_notification_cleared);
   assert(ui.acknowledgements==0);
#else
   assert(ui.buzzer.prompts.empty());
#endif
 }
#if MESH_GPS_VOICE
 // Each possible persisted mode is restored on failure. Deliberately divergent
 // runtime and routing states are preserved too; a failed save is not a toggle.
 the_mesh.save_ok=false;
 for(unsigned mode=0;mode<4;++mode){
   the_mesh._prefs.buzzer_quiet=!!(mode&2);the_mesh._prefs.vibe_quiet=!!(mode&1);
   ui.buzzer.muted=!(mode&2);ui.vibration.muted=!(mode&1);
   the_mesh.sound_muted=!(mode&2);the_mesh.vibration_muted=!(mode&1);
   const auto old_buzzer=ui.buzzer.muted,old_vibration=ui.vibration.muted;
   const auto old_sound=the_mesh.sound_muted,old_vibe=the_mesh.vibration_muted;
   const auto mutes=the_mesh.mutes,triggers=ui.vibration.triggers,saves=the_mesh.saves;
   const auto stops=ui.vibration.stops;
   mode_events.clear();
   ui.handleButtonTriplePress();
   assert(the_mesh.saves==saves+1&&the_mesh.saved_modes.back()==((mode+1)&3));
   assert(the_mesh._prefs.buzzer_quiet==!!(mode&2)&&the_mesh._prefs.vibe_quiet==!!(mode&1));
   assert(ui.buzzer.muted==old_buzzer&&ui.vibration.muted==old_vibration);
   assert(the_mesh.sound_muted==old_sound&&the_mesh.vibration_muted==old_vibe);
   assert(the_mesh.mutes==mutes&&ui.vibration.triggers==triggers);
   assert(ui.vibration.stops==stops);
   assert((mode_events==std::vector<std::string>{"save","voice"}));
   assert(ui.buzzer.prompts.back()==ButtonVoicePrompt::ActionFailed&&strstr(ui._alert,"failed"));
   assert(ui.buzzer.courtesy.back());
 }
 // A notification-mask write failure cannot undo the committed physical mode.
 the_mesh.save_ok=true;the_mesh.mask_save_ok=false;
 the_mesh._prefs.buzzer_quiet=the_mesh._prefs.vibe_quiet=0;
 the_mesh.sound_muted=the_mesh.vibration_muted=false;
 ui.vibration.active=true;
 ui.handleButtonTriplePress();
 assert(the_mesh._prefs.vibe_quiet==1&&!the_mesh.vibration_muted&&!ui.vibration.active);
 auto pulses=ui.vibration.pulses,stops=ui.vibration.stops;
 ui.notificationVibration(true);
 assert(ui.vibration.pulses==pulses&&ui.vibration.stops==stops+1&&!ui.vibration.active);
 ui.notificationVibration(false);
 assert(ui.vibration.pulses==pulses+1&&!ui.vibration.active);
 the_mesh._prefs.vibe_quiet=0;ui.notificationVibration(true);
 assert(ui.vibration.pulses==pulses+2&&ui.vibration.active);
 ui.notificationVibration(false);assert(!ui.vibration.active);
 ui._node_prefs=nullptr;ui.notificationVibration(true);assert(ui.vibration.active);
 ui._node_prefs=&the_mesh._prefs;
 // Shutdown quiesces multi-click changes instead of preempting final speech.
 ui._shutdown_pending=true;
 const auto saves=the_mesh.saves;
 const auto prompts=ui.buzzer.prompts.size();
 ui.handleButtonTriplePress();
 assert(the_mesh.saves==saves&&ui.buzzer.prompts.size()==prompts);
#else
 (void)expected;
 (void)vibration_stops;
 assert(ui.acknowledgements==4);
 // The opt-out retains its historical driver forwarding despite mute prefs.
 ui._node_prefs->vibe_quiet=1;
 ui.notificationVibration(true);assert(ui.vibration.active);
 ui.notificationVibration(false);assert(!ui.vibration.active);
#endif
}
'''


class Tests(unittest.TestCase):
    build = gps_tests.Tests.build

    def test_every_prompt_uses_its_own_clip_and_preserves_direction_tones(self):
        self.build(PROMPTS, rtttl=True)

    def test_each_new_board_default_uses_its_actual_buzzer_pin_and_releases_pwm(self):
        for board, pin, enable in (
            ('RAK_WISMESH_TAG', 21, None),
            ('MESH_TRACKER_X1', 25, None),
            ('R1Neo', 3, None),
            ('THINKNODE_M3', 23, 36),
        ):
            with self.subTest(board=board):
                source = PROMPTS.replace('#define PIN_BUZZER 25', f'#define PIN_BUZZER {pin}')
                source = source.replace('#define PIN_BUZZER_EN 37',
                                        '' if enable is None else f'#define PIN_BUZZER_EN {enable}')
                source = source.replace('#define MESH_GPS_VOICE 1',
                                        '#define NRF52_PLATFORM\n#define COMPANION_RADIO_FULL 1\n'
                                        +f'#define {board}\n')
                if board != 'MESH_TRACKER_X1':
                    source = source.replace('#define HAS_DRV2605 1', '')
                self.build(source, rtttl=True)

    def test_quiet_courtesy_completion_and_cancellation_release_both_pins(self):
        self.build(COURTESY, rtttl=True)

    def test_muted_x1_mode_announcements_never_unmute_notifications_and_release_pwm(self):
        for prompt in ('AlertsSoundVibration', 'AlertsSoundOnly',
                       'AlertsVibrationOnly', 'AlertsSilent'):
            with self.subTest(prompt=prompt):
                self.build(COURTESY.replace('ButtonVoicePrompt::SoundOff',
                                           'ButtonVoicePrompt::'+prompt)
                           .replace('#define MESH_GPS_VOICE 1',
                                    '#define HAS_DRV2605 1\n#define MESH_GPS_VOICE 1'), rtttl=True)

    def test_x1_alert_modes_report_committed_result_and_legacy_tones_remain(self):
        ui = (ROOT / 'examples/companion_radio/ui-orig/UITask.cpp').read_text()
        triple = method(ui, 'void UITask::handleButtonTriplePress()')
        vibration = method(ui, 'void UITask::notificationVibration(bool on)')
        for voice in (0, 1):
            with self.subTest(voice=voice):
                self.build(X1_ALERT_MODES.replace('@METHOD@', triple)
                           .replace('@VIBRATION_METHOD@', vibration)
                           .replace('@VOICE@', str(voice)))

    def test_voice_disabled_ordinary_play_keeps_its_legacy_preemption(self):
        self.build(LEGACY_ORDINARY_PLAY, rtttl=True)

    def test_notification_cleared_requires_an_actual_button_acknowledgement(self):
        mesh = (ROOT / 'examples/companion_radio/MyMesh.h').read_text()
        wrapper = method(mesh, 'bool notificationButton()')
        self.build(NOTIFICATION_ACK.replace('@WRAPPER@', wrapper))

    def test_muted_programmable_notifications_do_not_cancel_courtesy_audio(self):
        ui = (ROOT / 'examples/companion_radio/ui-orig/UITask.cpp').read_text()
        melody = method(ui, 'void UITask::notificationMelody(const char* text)')
        for voice in (0, 1):
            with self.subTest(voice=voice):
                self.build(NOTIFICATION_MELODY.replace('@METHOD@', melody)
                           .replace('@VOICE@', str(voice)))

    def test_notification_stop_preserves_control_tone_gap_and_dma_but_cancels_alerts(self):
        for voice in (0, 1):
            with self.subTest(voice=voice):
                self.build(NOTIFICATION_STOPS.replace('@VOICE@', str(voice)), rtttl=True)

    def test_button_actions_report_committed_results_and_shutdown_without_blocking(self):
        mesh = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        ui = (ROOT / 'examples/companion_radio/ui-orig/UITask.cpp').read_text()
        methods = method(mesh, 'bool MyMesh::setGpsEnabled(bool enabled)')
        for signature in (
            'void UITask::notify(UIEventType t)',
            'void UITask::notificationMelody(const char* text)',
            'void UITask::handleButtonAnyPress()',
            'void UITask::handleButtonShortPress()',
            'void UITask::handleButtonDoublePress()',
            'void UITask::handleButtonTriplePress()',
            'void UITask::handleButtonQuadruplePress()',
            'void UITask::handleButtonLongPress()',
            'void UITask::shutdown(bool restart)',
            'void UITask::servicePendingShutdown()',
        ):
            methods += '\n' + method(ui, signature)
        self.build(UI_ACTIONS.replace('@METHODS@', methods), button=True)
        self.build(UI_ACTIONS.replace('@METHODS@', methods)
                   .replace('#define ENV_INCLUDE_GPS 1', '#define ENV_INCLUDE_GPS 0'), button=True)
        loop = method(ui, 'void UITask::loop()')
        self.assertLess(loop.index('buzzer.loop()'), loop.index('servicePendingShutdown()'))


if __name__ == '__main__':
    unittest.main()
