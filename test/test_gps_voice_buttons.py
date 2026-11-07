"""Exercise the production speech decoder/player and screenless button actions."""
from pathlib import Path
import cmath
import math
import re
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define OUTPUT 1
extern uint32_t now_ms;
inline uint32_t millis(){return now_ms;}
inline int digitalRead(int){return LOW;}
inline int analogRead(int){return 0;}
inline int pin_states[64]={};
inline unsigned pin_writes[64]={};
inline void digitalWrite(int pin,int value){pin_states[pin]=value;++pin_writes[pin];}
inline void pinMode(int,int){}
'''

RTTTL = r'''
#pragma once
#include <string>
namespace rtttl {
static bool running=false;
static std::string melody;
static unsigned begins=0;
inline void begin(int,const char* song){melody=song;running=true;++begins;}
inline void stop(){running=false;}
inline bool done(){return !running;}
inline bool isPlaying(){return running;}
inline void play(){}
inline void finish(){running=false;}
}
'''

PWM = r'''
#pragma once
struct Sequence { uintptr_t PTR=0;uint32_t CNT=0,REFRESH=0,ENDDELAY=0; };
struct Registers {
 uint32_t INTENCLR=0,SHORTS=0,ENABLE=0,MODE=0,COUNTERTOP=0,PRESCALER=0,
 DECODER=0,LOOP=0,EVENTS_STOPPED=0,EVENTS_LOOPSDONE=0,INTENSET=0;
 Sequence SEQ[2];uint32_t EVENTS_SEQEND[2]={},EVENTS_SEQSTARTED[2]={},TASKS_SEQSTART[2]={};
};
static Registers registers;
#define NRF_PWM3 (&registers)
#define PWM_MODE_UPDOWN_Up 0
#define PWM_PRESCALER_PRESCALER_DIV_1 0
#define PWM_DECODER_LOAD_Common 0
#define PWM_SHORTS_LOOPSDONE_SEQSTART0_Msk 4
#define PWM_INTENSET_SEQEND0_Msk 1
#define PWM_INTENSET_SEQEND1_Msk 2
#define PWM3_IRQn 45
static bool irq_enabled=false;
inline void NVIC_DisableIRQ(int){irq_enabled=false;}
inline void NVIC_EnableIRQ(int){irq_enabled=true;}
inline void NVIC_ClearPendingIRQ(int){}
inline void NVIC_SetPriority(int,int){}
struct HardwarePWM {
 bool owned=false,pin=false,reject=false;
 int bound_pin=-1;
 bool takeOwnership(uint32_t){if(owned||reject)return false;return owned=true;}
 bool addPin(int value){bound_pin=value;return pin=true;}
 void removePin(int){pin=false;bound_pin=-1;}
 bool releaseOwnership(uint32_t){assert(!pin);owned=false;return true;}
};
static HardwarePWM HwPWM3;
'''

PLAYER = r'''
#include <cassert>
#include <helpers/ui/GpsVoice.h>
#define PIN_BUZZER 25
#define MESH_BUTTON_AUDIO_HIL 1
#include <helpers/ui/GpsVoiceData.h>
#include <helpers/ui/nrf52/GpsVoicePlayer.h>
uint32_t now_ms=0;
int main(){
 using namespace mesh::audio;
 VoiceDecoder decoder;
 const uint8_t short_data[]={0x77};
 decoder.begin({short_data,1,1000});
 assert(decoder.next()==11);assert(decoder.next()==41);
 assert(decoder.done()&&decoder.next()==0);
 decoder.begin({nullptr,1,2});assert(decoder.done());
 const uint8_t negative_data[64]={0xff,0xff,0xff,0xff};
 for(auto clip:{gpsOnClip,gpsOffClip,VoiceClip{negative_data,sizeof(negative_data),128},
                VoiceClip{negative_data,sizeof(negative_data),128,16000}}){
   decoder.begin(clip);
   unsigned count=0,nonzero=0;
   while(!decoder.done()){nonzero+=decoder.next()!=0; ++count;}
   assert(count==clip.samples&&nonzero>count/2);
 }
 GpsVoicePlayer player;
 assert(player.gain()==8);
 assert(!player.setGain(0)&&!player.setGain(9));
 assert(player.setGain(2));
 for(auto clip:{gpsOnClip,gpsOffClip,VoiceClip{negative_data,sizeof(negative_data),128},
                VoiceClip{negative_data,sizeof(negative_data),128,16000}}){
   assert(player.start(clip));assert(player.active()&&irq_enabled&&HwPWM3.owned&&HwPWM3.pin);
   assert(!player.setGain(8)&&player.gain()==2);
   assert(registers.COUNTERTOP==500&&registers.SEQ[0].REFRESH==0);
   VoiceDecoder reference;reference.begin(clip);
   VoiceInterpolator reconstruction;reconstruction.begin(clip.sample_rate);
   for(unsigned i=0;i<2;++i){
     auto data=reinterpret_cast<const uint16_t*>(registers.SEQ[i].PTR);
     for(unsigned j=0;j<registers.SEQ[i].CNT;++j){
       assert((data[j]&0x8000)&&(data[j]&0x7fff)<=500);
       int32_t pcm=reconstruction.next(reference)*2;
       if(pcm>32767)pcm=32767;
       if(pcm < -32768)pcm=-32768;
       assert((data[j]&0x7fff)==static_cast<uint16_t>((pcm+32768)*500/65536));
     }
   }
   unsigned seq=0,events=0;
   while(player.playing()){
     assert(++events<1000);
     registers.EVENTS_SEQEND[seq]=1;player.interrupt();seq^=1;
   }
   assert(player.played()==clip.samples&&player.active());
   assert(events==(clip.samples*(32000/clip.sample_rate)+127)/128);
   player.service();assert(!player.active()&&!HwPWM3.owned&&!HwPWM3.pin&&!irq_enabled&&!registers.ENABLE);
   assert(pin_states[PIN_BUZZER]==LOW);
 }
 assert(player.started()==4&&player.completed()==4&&player.cancelled()==0);
 assert(player.setGain(4));
 assert(player.start(gpsOnClip));player.stop();
 assert(player.cancelled()==1&&player.completed()==4);
 HwPWM3.reject=true;assert(!player.start(gpsOnClip));assert(!irq_enabled);
 assert(!player.start({short_data,1,3}));assert(!player.playing());
 assert(!player.start({short_data,1,2,12345}));
}
'''

PLAYER_BINARY = r'''
#include <cassert>
#define PIN_BUZZER 25
#define MESH_BUTTON_AUDIO_HIL 1
#define MESH_GPS_VOICE_BINARY_DRIVE 1
#include <helpers/ui/GpsVoiceData.h>
#include <helpers/ui/nrf52/GpsVoicePlayer.h>
uint32_t now_ms=0;
int main(){
 using namespace mesh::audio;
 GpsVoicePlayer player;
 for(unsigned repetition=0;repetition<2;++repetition){
   assert(player.start(gpsOnClip));
   assert(player.gain()==8&&registers.COUNTERTOP==250&&registers.SEQ[0].REFRESH==1);
   VoiceDecoder decoder;decoder.begin(gpsOnClip);
   VoiceInterpolator interpolator;interpolator.begin();
   bool high=false;unsigned transitions=0;
   for(unsigned n=0;n<2;++n){
     auto values=reinterpret_cast<const uint16_t*>(registers.SEQ[n].PTR);
     for(unsigned i=0;i<registers.SEQ[n].CNT;++i){
       int32_t pcm=interpolator.next(decoder)*8;
       bool old=high;
       if(pcm>1024)high=true;else if(pcm < -1024)high=false;
       transitions+=old!=high;
       assert(values[i]==(0x8000|(high?250:0)));
     }
   }
   assert(transitions>0);
   player.stop();assert(!player.active()&&!registers.ENABLE&&!irq_enabled);
 }
 assert(player.cancelled()==2);
 // Encoded silence is below the hysteresis band and does not chatter.
 const uint8_t silence[64]={};
 assert(player.start({silence,sizeof(silence),128}));
 auto values=reinterpret_cast<const uint16_t*>(registers.SEQ[0].PTR);
 for(unsigned i=0;i<registers.SEQ[0].CNT;++i)assert(values[i]==0x8000);
 player.stop();
}
'''

PLAYER_PDM = r'''
#include <cassert>
#define PIN_BUZZER 25
#define MESH_BUTTON_AUDIO_HIL 1
#define MESH_GPS_VOICE_PDM_DRIVE 1
#include <helpers/ui/GpsVoiceData.h>
#include <helpers/ui/nrf52/GpsVoicePlayer.h>
uint32_t now_ms=0;
int main(){
 using namespace mesh::audio;
 GpsVoicePlayer player;
 assert(player.start(gpsOnClip));
 assert(player.gain()==8&&registers.COUNTERTOP==250&&registers.SEQ[0].REFRESH==0);
 VoiceDecoder decoder;decoder.begin(gpsOnClip);
 VoiceInterpolator interpolator;interpolator.begin();
 int32_t error=0,pcm=0;
 unsigned transitions=0;bool old=false;
 for(unsigned n=0;n<2;++n){
   auto values=reinterpret_cast<const uint16_t*>(registers.SEQ[n].PTR);
   for(unsigned i=0;i<registers.SEQ[n].CNT;++i){
     if(!(i&1))pcm=interpolator.next(decoder)*8;
     if(pcm>32767)pcm=32767;
     if(pcm < -32768)pcm=-32768;
     error+=pcm;bool high=error>=0;error-=high?32767:-32768;
     assert(error>=-32768&&error<=32768);
     assert(values[i]==(0x8000|(high?250:0)));
     transitions+=old!=high;old=high;
   }
 }
 assert(transitions>0);
 unsigned seq=0,events=0;
 while(player.playing()){
   assert(++events<1000);registers.EVENTS_SEQEND[seq]=1;player.interrupt();seq^=1;
 }
 assert(events==(gpsOnClip.samples*8+127)/128);
 assert(player.played()==gpsOnClip.samples);
 player.service();assert(!player.active()&&!registers.ENABLE&&!irq_enabled);
}
'''

BUTTON = r'''
#include <cassert>
#include <string>
#include <initializer_list>
#include <helpers/ui/ButtonVoice.h>
#include "examples/companion_radio/ui-orig/Button.h"
#define ENV_INCLUDE_GPS 1
#define PIN_BUZZER 25
#define MESH_DEBUG_PRINTLN(...) ((void)0)
uint32_t now_ms=0;
enum class UIEventType{ack};
namespace mesh{namespace notify{enum{Sound=1};}}
struct CompanionNodePrefs{uint8_t gps_enabled=0,buzzer_quiet=1;};
struct SensorManager{
 bool enabled=false;
 bool available=true;
 bool setSettingValue(const char*,const char* value){if(!available)return false;enabled=value[0]=='1';return true;}
 const char* getSettingByKey(const char*){return available?(enabled?"1":"0"):nullptr;}
};
static SensorManager sensors;
struct MyMesh{
 CompanionNodePrefs _prefs; bool save_ok=true;unsigned saves=0,adverts=0;
 bool savePrefs(){++saves;return save_ok;}
 template<class T> bool savePreference(T& p,T v){T old=p;p=v;if(savePrefs())return true;p=old;return false;}
 void applyGpsPrefs(){sensors.setSettingValue("gps",_prefs.gps_enabled?"1":"0");}
 bool setGpsEnabled(bool);
 bool advert(){++adverts;return true;}
 void setNotificationOutputMute(int,int){}
};
static MyMesh the_mesh;
struct Buzzer{
 bool muted=true;unsigned spoken=0;bool last=false;
 bool speakGps(bool value){if(!muted){++spoken;last=value;}return true;}
 bool speakButton(ButtonVoicePrompt prompt,bool=false){
   if(prompt==ButtonVoicePrompt::GpsOn||prompt==ButtonVoicePrompt::GpsOff)
     return speakGps(prompt==ButtonVoicePrompt::GpsOn);
   return true;
 }
 bool isQuiet(){return muted;}
 void quiet(bool q){muted=q;}
};
struct UITask{
 SensorManager* _sensors=&sensors;CompanionNodePrefs* _node_prefs=&the_mesh._prefs;
 Buzzer buzzer;char _alert[80]={};bool _need_refresh=false;unsigned acks=0;
 void notify(UIEventType){++acks;}
 void handleButtonDoublePress();void handleButtonTriplePress();void handleButtonQuadruplePress();
};
@METHODS@
int main(){
 UITask ui;Button button(6,HIGH);button.begin();
 unsigned singles=0;
 button.onShortPress([&]{++singles;});
 button.onDoublePress([&]{ui.handleButtonDoublePress();});
 button.onTriplePress([&]{ui.handleButtonTriplePress();});
 button.onQuadruplePress([&]{ui.handleButtonQuadruplePress();});
 auto run=[&](unsigned count){
   assert(button.injectPresses(count));
   assert(!button.injectPresses(count));
   uint32_t start=now_ms;
   while(now_ms-start<3000){now_ms+=10;button.update();}
 };
 run(1);assert(singles==1&&the_mesh.adverts==0&&the_mesh._prefs.gps_enabled==0);
 run(2);assert(the_mesh.adverts==1&&the_mesh._prefs.gps_enabled==0);
 // Multi-click actions do not turn an overlong held press into short clicks.
 unsigned held=0;button.onLongPress([&]{++held;});
 assert(button.injectPresses(1,3500));
 for(unsigned i=0;i<500;++i){now_ms+=10;button.update();}
 assert(held==1&&singles==1&&the_mesh.adverts==1);
 run(3);assert(!ui.buzzer.isQuiet()&&!the_mesh._prefs.buzzer_quiet);
 run(4);assert(sensors.enabled&&the_mesh._prefs.gps_enabled==1&&ui.buzzer.last);
 run(4);assert(!sensors.enabled&&the_mesh._prefs.gps_enabled==0&&!ui.buzzer.last);
 assert(ui.buzzer.spoken==2);
 the_mesh.save_ok=false;
 run(4);assert(!sensors.enabled&&the_mesh._prefs.gps_enabled==0);
 assert(ui.buzzer.spoken==2&&strstr(ui._alert,"failed"));
 the_mesh.save_ok=true;sensors.available=false;
 run(4);assert(ui.buzzer.spoken==2);
}
'''

BUZZER_SEQUENCE = r'''
#include <cassert>
#include <string>
#define PIN_BUZZER 25
#define PIN_BUZZER_EN 37
#define MESH_BUTTON_AUDIO_HIL 1
#define MESH_GPS_VOICE 1
#include <helpers/ui/buzzer.cpp>
uint32_t now_ms=0;
int main(){
 genericBuzzer buzzer;buzzer.begin();buzzer.quiet(false);
 char status[180];
 auto check=[&](const char* expected){buzzer.voiceStatus(status,sizeof(status));assert(strstr(status,expected));};
 auto finish_tone=[&]{
   rtttl::finish();buzzer.loop();assert(buzzer.isPlaying());
   now_ms+=99;buzzer.loop();assert(!registers.ENABLE);
   ++now_ms;buzzer.loop();
 };
 auto finish_speech=[&]{
   unsigned seq=0,events=0;
   while(registers.ENABLE){assert(++events<1000);registers.EVENTS_SEQEND[seq]=1;PWM3_IRQHandler();seq^=1;}
   assert(buzzer.isPlaying());buzzer.loop();assert(!buzzer.isPlaying());
 };
 for(bool enabled:{true,false}){
   assert(buzzer.speakGps(enabled));assert(buzzer.isPlaying());
   assert(rtttl::melody==(enabled?"Startup:d=4,o=5,b=160:16c6,16e6,8g6":"Shutdown:d=4,o=5,b=100:8g5,16e5,16c5"));
   assert(!registers.ENABLE);check("pending=1 tone=1");
   assert(!buzzer.voiceGain(8));
   now_ms+=500;buzzer.loop();assert(!registers.ENABLE);
   finish_tone();assert(registers.ENABLE);check("pending=0 tone=0");
   finish_speech();
 }
 check("started=2 completed=2 cancelled=0");
 // Muting cancels a tone, a queued pause, and active speech without restarting.
 assert(buzzer.speakGps(true));buzzer.quiet(true);assert(!buzzer.isPlaying());
 now_ms+=1000;buzzer.loop();check("started=2 completed=2 cancelled=0");
 auto before=rtttl::begins;assert(buzzer.speakGps(false));assert(rtttl::begins==before);
 buzzer.quiet(false);assert(buzzer.speakGps(false));rtttl::finish();buzzer.loop();
 buzzer.quiet(true);now_ms+=1000;buzzer.loop();assert(!buzzer.isPlaying());
 buzzer.quiet(false);assert(buzzer.speakGps(true));
 buzzer.playNotification("Alert:d=4,o=5,b=120:c");rtttl::finish();
 now_ms+=1000;buzzer.loop();assert(!buzzer.isPlaying());check("started=2 completed=2 cancelled=0");
 assert(buzzer.speakGps(true));finish_tone();buzzer.quiet(true);
 assert(!buzzer.isPlaying());check("started=3 completed=2 cancelled=1");
 buzzer.quiet(false);
 // The pause handles the millisecond counter wrapping and a busy PWM gracefully.
 now_ms=0xfffffff0;assert(buzzer.speakGps(false));finish_tone();assert(registers.ENABLE);
 finish_speech();check("started=4 completed=3 cancelled=1");
 HwPWM3.reject=true;assert(buzzer.speakGps(true));finish_tone();assert(!buzzer.isPlaying());
 check("started=4 completed=3 cancelled=1");
}
'''

BUZZER_TONES = r'''
#include <cassert>
#define PIN_BUZZER 25
#define MESH_GPS_VOICE 0
#include <helpers/ui/buzzer.cpp>
uint32_t now_ms=0;
int main(){
 genericBuzzer buzzer;buzzer.begin();buzzer.quiet(false);
 assert(buzzer.speakGps(true));assert(buzzer.isPlaying());
 assert(rtttl::melody=="Startup:d=4,o=5,b=160:16c6,16e6,8g6");
 rtttl::finish();buzzer.loop();assert(!buzzer.isPlaying());
 assert(buzzer.speakGps(false));
 assert(rtttl::melody=="Shutdown:d=4,o=5,b=100:8g5,16e5,16c5");
 buzzer.quiet(true);assert(!buzzer.isPlaying());
 unsigned before=rtttl::begins;assert(buzzer.speakGps(true));assert(rtttl::begins==before);
 buzzer.quiet(false);
 for(auto prompt:{ButtonVoicePrompt::Ready,ButtonVoicePrompt::NotificationCleared,
                 ButtonVoicePrompt::AdvertQueued,ButtonVoicePrompt::AdvertFailed,
                 ButtonVoicePrompt::SoundOn,ButtonVoicePrompt::SoundOff,
                 ButtonVoicePrompt::ActionFailed,ButtonVoicePrompt::UsbSetup,
                 ButtonVoicePrompt::AlertsSoundVibration,ButtonVoicePrompt::AlertsSoundOnly,
                 ButtonVoicePrompt::AlertsVibrationOnly,ButtonVoicePrompt::AlertsSilent}){
   assert(buzzer.speakButton(prompt));assert(!buzzer.isPlaying()&&rtttl::begins==before);
 }
 assert(buzzer.speakGps(true));before=rtttl::begins;
 assert(!buzzer.speakButton(static_cast<ButtonVoicePrompt>(255)));
 assert(buzzer.isPlaying()&&rtttl::begins==before);
}
'''

class Tests(unittest.TestCase):
    def test_reconstruction_suppresses_audible_sampling_images(self):
        source=(ROOT/'src/helpers/ui/GpsVoice.h').read_text()
        body=source.split('coefficients[4][16] = {',1)[1].split('};',1)[0]
        rows=[[int(value) for value in row.split(',')]
              for row in re.findall(r'\{([^{}]+)\}',body)]
        self.assertEqual(len(rows),4)
        for row in rows:
            self.assertEqual(len(row),16)
            self.assertEqual(sum(row),16384)
            self.assertLess(32768*sum(abs(value) for value in row),2**31)
        taps=[rows[phase][index] for index in range(16) for phase in range(4)]
        def response(frequency):
            value=sum(tap*cmath.exp(-2j*math.pi*frequency*i/32000)
                      for i,tap in enumerate(taps))/65536
            return 20*math.log10(abs(value))
        self.assertAlmostEqual(response(700),0,delta=0.02)
        self.assertLess(response(4700),-65)
        self.assertLess(response(7300),-65)

    def test_wideband_reconstruction_bounds_and_consonant_band(self):
        source=(ROOT/'src/helpers/ui/GpsVoice.h').read_text()
        body=source.split('wide_coefficients[2][16] = {',1)[1].split('};',1)[0]
        rows=[[int(value) for value in row.split(',')]
              for row in re.findall(r'\{([^{}]+)\}',body)]
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertEqual(len(row),16)
            self.assertEqual(sum(row),16384)
            self.assertLess(32768*sum(abs(value) for value in row),2**31)
        taps=[rows[phase][index] for index in range(16) for phase in range(2)]
        def response(frequency):
            value=sum(tap*cmath.exp(-2j*math.pi*frequency*i/32000)
                      for i,tap in enumerate(taps))/32768
            return 20*math.log10(abs(value))
        self.assertAlmostEqual(response(3000),0,delta=0.02)
        self.assertGreater(response(6500),-2)
        self.assertLess(response(10000),-45)

    def build(self, source, *, button=False, rtttl=False):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            (path/'Arduino.h').write_text(ARDUINO)
            (path/'HardwarePWM.h').write_text(PWM)
            (path/'NonBlockingRtttl.h').write_text(RTTTL if rtttl else '#pragma once\n')
            (path/'test.cpp').write_text(source)
            binary=path/'test'
            cmd=['g++','-std=c++17','-Wall','-Wextra','-Werror',
                 '-I'+str(path),'-I'+str(ROOT/'src'),'-I'+str(ROOT),
                 str(path/'test.cpp'),'-o',str(binary)]
            if button:
                cmd+=['-DMESH_BUTTON_AUDIO_HIL=1',str(ROOT/'examples/companion_radio/ui-orig/Button.cpp')]
            subprocess.run(cmd,check=True)
            subprocess.run([str(binary)],check=True)

    def test_decoder_and_dma_lifecycle(self):
        self.build(PLAYER.replace('#include <cassert>','#include <cassert>\n#include <initializer_list>'))

    def test_binary_acoustic_driver_hysteresis_and_reset(self):
        self.build(PLAYER_BINARY)

    def test_density_acoustic_driver_bounds_and_sample_rate(self):
        self.build(PLAYER_PDM)
        source=PLAYER_PDM.replace('gpsOnClip', 'clip')
        source=source.replace('GpsVoicePlayer player;',
                              'GpsVoicePlayer player; auto clip=gpsOnClip; clip.sample_rate=16000;')
        source=source.replace('interpolator.begin();','interpolator.begin(clip.sample_rate);')
        source=source.replace('clip.samples*8','clip.samples*4')
        self.build(source)

    def test_lower_carrier_acoustic_driver_preserves_sample_rate(self):
        source=PLAYER.replace('#define MESH_BUTTON_AUDIO_HIL 1',
                             '#define MESH_BUTTON_AUDIO_HIL 1\n#define MESH_GPS_VOICE_PCM32_DRIVE 1')
        self.build(source.replace('#include <cassert>','#include <cassert>\n#include <initializer_list>'))

    def test_hil_can_compare_the_previous_carrier_without_changing_output_rate(self):
        source=PLAYER.replace('#define MESH_BUTTON_AUDIO_HIL 1',
                             '#define MESH_BUTTON_AUDIO_HIL 1\n#define MESH_GPS_VOICE_PCM32_DRIVE 0')
        source=source.replace('COUNTERTOP==500&&registers.SEQ[0].REFRESH==0',
                              'COUNTERTOP==250&&registers.SEQ[0].REFRESH==1')
        source=source.replace('<=500','<=250').replace('pcm+32768)*500','pcm+32768)*250')
        self.build(source.replace('#include <cassert>','#include <cassert>\n#include <initializer_list>'))

    def test_experimental_drive_flags_do_not_change_non_test_firmware(self):
        source=PLAYER.replace('#define MESH_BUTTON_AUDIO_HIL 1', '@FLAGS@')
        # The lab gain control is absent in non-test firmware. Keep the same
        # waveform assertions at its default gain instead.
        source=source.replace('assert(!player.setGain(0)&&!player.setGain(9));','')
        source=source.replace('assert(player.setGain(2));','')
        source=source.replace('assert(!player.setGain(8)&&player.gain()==2);','')
        source=source.replace('reconstruction.next(reference)*2','reconstruction.next(reference)*8')
        source=source.replace('assert(player.setGain(4));','')
        source=source.replace('#include <cassert>','#include <cassert>\n#include <initializer_list>')
        for flags in (
            '#define MESH_GPS_VOICE_BINARY_DRIVE 1',
            '#define MESH_GPS_VOICE_PDM_DRIVE 1',
            '#define MESH_GPS_VOICE_PCM32_DRIVE 0',
            '#define MESH_GPS_VOICE_BINARY_DRIVE 1\n#define MESH_GPS_VOICE_PDM_DRIVE 1\n#define MESH_GPS_VOICE_PCM32_DRIVE 0',
        ):
            with self.subTest(flags=flags):
                self.build(source.replace('@FLAGS@', flags))

    def test_direction_tones_finish_before_speech_and_cancellation(self):
        self.build(BUZZER_SEQUENCE,rtttl=True)

    def test_direction_tones_without_speech(self):
        self.build(BUZZER_TONES.replace('#include <cassert>',
                                      '#include <cassert>\n#include <initializer_list>'),rtttl=True)

    def assert_voice_policy(self, flags, expected):
        self.build(flags+'#include <helpers/ui/buzzer.h>\n'
                   +f'static_assert(MESH_GPS_VOICE=={expected},"speech gating");\nint main(){{}}\n')

    def test_speech_defaults_to_all_screenless_nrf52_full_companions_only(self):
        eligible = ('T1000_E', 'RAK_WISMESH_TAG', 'MESH_TRACKER_X1',
                    'R1Neo', 'THINKNODE_M3')
        full = '#define NRF52_PLATFORM\n#define COMPANION_RADIO_FULL 1\n#define PIN_BUZZER 25\n'
        for board in eligible:
            for laboratory in ('', '#define MESH_BUTTON_AUDIO_HIL\n'):
                with self.subTest(board=board, laboratory=bool(laboratory)):
                    self.assert_voice_policy(full+f'#define {board}\n'+laboratory, 1)
        # A buzzer on another board is not sufficient: screened boards and all
        # Wio profiles retain their existing tones, even in a Full Companion.
        for board in ('HELTEC_T1', 'MESHTINY', 'NANO_G2_ULTRA',
                      'LILYGO_TECHO_CARD', 'THINKNODE_M1', 'THINKNODE_M8',
                      'THINKNODE_M4',
                      'WIO_TRACKER_L1', 'WIO_TRACKER_L1_1W', 'WIO_TRACKER_L1_EINK',
                      'RAK_4631', 'UNKNOWN_BOARD'):
            with self.subTest(excluded_board=board):
                self.assert_voice_policy(full+f'#define {board}\n', 0)
        self.assert_voice_policy(full, 0)

    def test_r1_neo_qspi_conflict_does_not_enable_voice_and_m4_copied_pins_are_removed(self):
        self.assert_voice_policy('#define NRF52_PLATFORM\n#define COMPANION_RADIO_FULL 1\n'
                                 '#define PIN_BUZZER 3\n#define R1Neo\n#define QSPIFLASH\n', 0)
        m4 = (ROOT / 'variants/thinknode_m4/platformio.ini').read_text()
        active = '\n'.join(line.strip() for line in m4.splitlines()
                           if not line.lstrip().startswith(';'))
        self.assertNotRegex(active, r'-D\s+PIN_BUZZER=23\b')
        self.assertNotRegex(active, r'-D\s+PIN_BUZZER_EN=36\b')

    def test_speech_requires_full_companion_nrf52_and_fitted_buzzer_and_allows_opt_out(self):
        for board in ('T1000_E', 'RAK_WISMESH_TAG', 'MESH_TRACKER_X1',
                      'R1Neo', 'THINKNODE_M3'):
            base = f'#define {board}\n'
            for case, flags in (
                ('no_nrf52', '#define COMPANION_RADIO_FULL 1\n#define PIN_BUZZER 25\n'),
                ('full_disabled', '#define NRF52_PLATFORM\n#define COMPANION_RADIO_FULL 0\n#define PIN_BUZZER 25\n'),
                ('non_companion', '#define NRF52_PLATFORM\n#define PIN_BUZZER 25\n'),
                ('no_buzzer', '#define NRF52_PLATFORM\n#define COMPANION_RADIO_FULL 1\n'),
                ('explicit_opt_out', '#define NRF52_PLATFORM\n#define COMPANION_RADIO_FULL 1\n#define PIN_BUZZER 25\n#define MESH_GPS_VOICE 0\n'),
            ):
                with self.subTest(board=board, excluded_profile=case):
                    self.assert_voice_policy(base+flags, 0)

    def test_unsafe_audio_profiles_retain_tones_and_explicit_voice_override(self):
        full = '#define NRF52_PLATFORM\n#define COMPANION_RADIO_FULL 1\n'
        profiles = (
            # The M4's previous buzzer configuration overlaps its I2C SDA
            # and battery-status LED, not a verified speaker connection.
            '#define THINKNODE_M4\n#define PIN_BUZZER 23\n#define PIN_BUZZER_EN 36\n',
            # R1 Neo's dummy QSPI clock is the same pin as its buzzer.
            '#define R1Neo\n#define PIN_BUZZER 3\n#define QSPIFLASH 1\n',
        )
        for profile in profiles:
            with self.subTest(profile=profile):
                self.assert_voice_policy(full+profile, 0)
                self.assert_voice_policy(full+profile+'#define MESH_GPS_VOICE 1\n', 1)

    def test_buttons_gps_persistence_and_failed_save(self):
        mesh=(ROOT/'examples/companion_radio/MyMesh.cpp').read_text()
        ui=(ROOT/'examples/companion_radio/ui-orig/UITask.cpp').read_text()
        methods=method(mesh,'bool MyMesh::setGpsEnabled(bool enabled)')
        for signature in ('void UITask::handleButtonDoublePress()',
                          'void UITask::handleButtonTriplePress()',
                          'void UITask::handleButtonQuadruplePress()'):
            methods+='\n'+method(ui,signature)
        self.build(BUTTON.replace('@METHODS@',methods),button=True)

if __name__=='__main__':
    unittest.main()
