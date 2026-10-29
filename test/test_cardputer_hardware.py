"""Execute bounded Cardputer audio and headless GNSS/environment ownership."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_cardputer_gps import ARDUINO, MESH, RTC, CAYENNE

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/m5stack_cardputer_adv"

I2S = r'''
#pragma once
#include <Arduino.h>
#include <algorithm>
#include <cassert>
#include <deque>
#include <vector>
using esp_err_t=int;
using i2s_mode_t=int;
constexpr int ESP_OK=0,ESP_ERR_TIMEOUT=1,ESP_FAIL=2;
constexpr int I2S_NUM_0=0,I2S_MODE_MASTER=1,I2S_MODE_TX=2,I2S_BITS_PER_SAMPLE_16BIT=16;
constexpr int I2S_CHANNEL_FMT_ONLY_LEFT=0,I2S_COMM_FORMAT_STAND_I2S=0,ESP_INTR_FLAG_LEVEL1=1;
constexpr int I2S_PIN_NO_CHANGE=-1;
struct i2s_config_t {
 int mode,sample_rate,bits_per_sample,channel_format,communication_format,intr_alloc_flags;
 int dma_buf_count,dma_buf_len;bool use_apll,tx_desc_auto_clear;int fixed_mclk;
};
struct i2s_pin_config_t {int bck_io_num,ws_io_num,data_out_num,data_in_num;};
struct WriteReply {int result;size_t limit;};
inline std::deque<WriteReply> replies;
inline std::vector<uint8_t> audio;
inline unsigned installs=0,pins=0,uninstalls=0,writes=0;
inline int install_result=ESP_OK,pin_result=ESP_OK;
inline esp_err_t i2s_driver_install(int,const i2s_config_t* cfg,int,void*) {
 ++installs;assert(cfg->dma_buf_count==4&&cfg->dma_buf_len==256);return install_result;
}
inline esp_err_t i2s_set_pin(int,const i2s_pin_config_t* cfg) {
 ++pins;assert(cfg->bck_io_num==41&&cfg->ws_io_num==43&&cfg->data_out_num==42);return pin_result;
}
inline esp_err_t i2s_driver_uninstall(int){++uninstalls;return ESP_OK;}
inline esp_err_t i2s_write(int,const void* src,size_t size,size_t* written,unsigned wait) {
 ++writes;assert(wait==0&&size>0&&size<=512);
 WriteReply reply{ESP_OK,size};if(!replies.empty()){reply=replies.front();replies.pop_front();}
 *written=std::min(size,reply.limit);
 const auto* bytes=static_cast<const uint8_t*>(src);audio.insert(audio.end(),bytes,bytes+*written);
 return reply.result;
}
'''
WIRE = r'''
#pragma once
#include <Arduino.h>
#include <vector>
struct TwoWire {
 unsigned transactions=0,fail_at=0;uint8_t address=0;
 void beginTransmission(uint8_t addr){address=addr;++transactions;}
 size_t write(uint8_t){return 1;}
 uint8_t endTransmission(bool=true){return fail_at==transactions?2:0;}
 void begin(int=0,int=0,unsigned=100000){}
 void setPins(uint8_t,uint8_t){}
 void setClock(unsigned){}
 void end(){}
};
inline TwoWire Wire,Wire1;
'''
SPEAKER = r'''
#include <cassert>
#include <cmath>
#include <cstring>
#include <limits>
#include <driver/i2s.h>
#include <Wire.h>
#include "hw/CardputerSpeaker.h"
static void reset(uint32_t now=100) {
 now_ms=now;delay_count=0;replies.clear();audio.clear();installs=pins=uninstalls=writes=0;
 install_result=pin_result=ESP_OK;Wire1=TwoWire();
}
static void poll(CardputerSpeaker& speaker) {
 const auto before=writes;speaker.processQueue();assert(writes-before<=1&&delay_count==0);
}
static void finish(CardputerSpeaker& speaker) {
 for(unsigned i=0;i<100&&speaker.isBusy();++i){poll(speaker);++now_ms;}
 assert(!speaker.isBusy());
}
static void normalAndPartial() {
 reset();CardputerSpeaker speaker;speaker.queueTone(1000,100,0.5f);
 assert(speaker.isBusy()&&writes==0&&installs==1&&pins==1);
 now_ms+=1099;poll(speaker);assert(writes==0);
 ++now_ms;replies={{ESP_OK,1},{ESP_ERR_TIMEOUT,3},{ESP_OK,0},{ESP_OK,17},{ESP_OK,511}};
 finish(speaker);assert(audio.size()==3200);
 for(size_t i=0;i<audio.size()/2;++i) {
  int16_t value;std::memcpy(&value,audio.data()+2*i,2);
  const auto expected=int16_t(std::sin(double(i)*2*M_PI*1000/16000)*10000);
  assert(std::abs(int(value)-int(expected))<=2);
 }
 speaker.sleep();assert(uninstalls==1&&!speaker.isBusy());
}
static void replacementAndCancellation() {
 reset();CardputerSpeaker speaker;speaker.playTone(1000,100);
 assert(writes==0);now_ms+=1100;poll(speaker);assert(audio.size()==512);
 speaker.queueTone(2000,1);const size_t boundary=audio.size();poll(speaker);
 assert(audio.size()==boundary+32);now_ms+=64;poll(speaker);assert(!speaker.isBusy());
 int16_t first,second;std::memcpy(&first,audio.data()+boundary,2);std::memcpy(&second,audio.data()+boundary+2,2);
 assert(first==0&&second>7000); // new tone has no unsent old suffix
 speaker.queueTone(3000,100);speaker.sleep();const auto before=writes;poll(speaker);
 assert(!speaker.isBusy()&&writes==before&&uninstalls==1);
 speaker.queueTone(3000,1);assert(installs==2);now_ms+=1100;finish(speaker);
 for(float volume:{0.0f,-1.0f,std::numeric_limits<float>::quiet_NaN()}) {
  speaker.queueTone(4000,20,volume);assert(!speaker.isBusy());
 }
 speaker.queueTone(0,20);assert(!speaker.isBusy());
 speaker.queueTone(9000,20);assert(!speaker.isBusy());
 speaker.queueTone(1000,0);assert(!speaker.isBusy());
}
static void failures() {
 for(int fault=0;fault<4;++fault) {
  reset();CardputerSpeaker speaker;
  if(fault==0)install_result=ESP_FAIL;
  if(fault==1)pin_result=ESP_FAIL;
  if(fault==2)Wire1.fail_at=4;
  speaker.queueTone(1000,100);
  if(fault<3) {
   assert(!speaker.isBusy()&&writes==0&&uninstalls==(fault==0?0U:1U));
  } else {
   now_ms+=1100;replies={{ESP_FAIL,0}};poll(speaker);
   assert(!speaker.isBusy()&&uninstalls==1);
  }
  install_result=pin_result=ESP_OK;Wire1.fail_at=0;
  speaker.queueTone(1000,1);assert(speaker.isBusy());now_ms+=1100;finish(speaker);
 }
 reset();CardputerSpeaker stalled;stalled.queueTone(1000,100);now_ms+=1100;
 replies={{ESP_OK,0}};poll(stalled);assert(stalled.isBusy());
 now_ms+=1000;replies={{ESP_ERR_TIMEOUT,0}};poll(stalled);
 assert(!stalled.isBusy()&&uninstalls==1);
}
static void rollover() {
 reset(UINT32_MAX-500);CardputerSpeaker speaker;speaker.queueTone(1000,1);
 now_ms=598;poll(speaker);assert(writes==0);
 now_ms=599;poll(speaker);assert(writes==1&&speaker.isBusy());
 now_ms=662;poll(speaker);assert(speaker.isBusy());
 now_ms=663;poll(speaker);assert(!speaker.isBusy());
}
int main(){normalAndPartial();replacementAndCancellation();failures();rollover();}
'''
INA260 = r'''
#pragma once
#include <Wire.h>
class Adafruit_INA260 {
public:
 inline static unsigned begins=0,reads=0;
 bool begin(uint8_t addr=0x41,TwoWire* wire=&Wire){++begins;return addr==0x41&&wire==&Wire;}
 float readBusVoltage(){++reads;return 6500.0f;}
 float readCurrent(){return 250.0f;}
 float readPower(){return 1600.0f;}
};
'''
HEADLESS = r'''
#include <cassert>
#include <cstring>
#include <Adafruit_INA260.h>
#include "CardputerSensorManager.h"
#if ENV_INCLUDE_GPS
struct Provider:LocationProvider {
 unsigned starts=0,stops=0,polls=0,resets=0;bool valid=false;
 void begin()override{++starts;}
 void stop()override{++stops;}
 void reset()override{++resets;}
 void loop()override{++polls;}
 bool clearPositionFix()override{valid=false;return true;}
 long getLatitude()override{return 47610000;}
 long getLongitude()override{return -122330000;}
 long getAltitude()override{return 125000;}
 bool isValid()override{return valid;}
 bool isEnabled()override{return true;}
 long satellitesCount()override{return 8;}
 long getTimestamp()override{return 0;}
 void sendSentence(const char* sentence)override {
  Serial1.print(!std::strcmp(sentence,"$PCAS12,65535")?"$PCAS12,65535*1E\r\n":"$PCAS10,0*1C\r\n");
 }
};
#endif
void LocationProvider::sendSentence(const char*){}
static void checkExternal(CardputerSensorManager& manager) {
 uint8_t channels[2]={};assert(manager.getVoltageSensorChannels(channels,2)==1&&channels[0]==2);
 SensorManager::VoltageSensorReading readings[2];assert(manager.queryVoltageSensors(readings,2)==1);
 assert(readings[0].valid&&readings[0].voltage==6.5f);
 CayenneLPP telemetry(64);const auto before=Adafruit_INA260::reads;
 assert(manager.querySensors(TELEM_PERM_ENVIRONMENT,telemetry)&&Adafruit_INA260::reads==before+1);
 assert(telemetry.getSize()>0&&telemetry.gps_count==0);
 assert(manager.querySensors(TELEM_PERM_BASE,telemetry)&&Adafruit_INA260::reads==before+1);
}
int main() {
 now_ms=100;
#if ENV_INCLUDE_GPS
 Provider provider;CardputerSensorManager manager(provider);
#else
 CardputerSensorManager manager;
#endif
 assert(manager.begin()&&Serial1.starts==1&&Adafruit_INA260::begins==1);
 assert(Serial1.output=="$PCAS12,65535*1E\r\n"&&!manager.isGpsPowered());
 assert(Serial1.rx==15&&Serial1.tx==13&&Serial1.baud==115200);
 assert(manager.gpsUsesSerialUart(1)&&manager.gpsSerialTransportMayConflict(1));
 assert(!manager.gpsSerialTransportCanYield(1)&&!manager.setGpsSerialTransportBlocked(1,true));
 checkExternal(manager);
#if ENV_INCLUDE_GPS
 assert(manager.isGPSDetected()&&manager.getNumSettings()==1);
 assert(!std::strcmp(manager.getSettingName(0),"gps")&&manager.getSettingName(1)==nullptr);
 assert(!std::strcmp(manager.getSettingValue(0),"0"));
 assert(provider.starts==0&&provider.resets==0&&provider.polls==0);
 manager.setPowerSavingEnabled(true);
 for(unsigned i=0;i<4;++i){now_ms+=3600000;provider.syncTime();manager.loop();}
 assert(provider.starts==0&&provider.resets==0&&provider.polls==0&&Serial1.starts==1);
 assert(manager.setSettingValue("gps","1")&&manager.isGpsPowered());
 assert(provider.starts==1&&provider.resets==0);
 assert(manager.setSettingValue("gps_interval","2"));provider.valid=true;manager.loop();
 CayenneLPP telemetry(64);assert(manager.querySensors(TELEM_PERM_LOCATION,telemetry));
 assert(telemetry.gps_count==1); // one cache, one GPS field, generic query delegated exactly once
 manager.setSettingValue("gps","0");assert(!manager.isGpsPowered());
 const auto polls=provider.polls;now_ms+=3600000;manager.loop();assert(provider.polls==polls);
#else
 assert(!manager.isGPSDetected()&&manager.getNumSettings()==0&&manager.getLocationProvider()==nullptr);
 assert(manager.getSettingName(0)==nullptr&&manager.getSettingValue(0)==nullptr);
 assert(!manager.setSettingValue("gps","1")&&!manager.setSettingValue("gps_interval","2"));
 manager.setPowerSavingEnabled(true);now_ms+=3600000;manager.loop();
 assert(!manager.isGpsPowered()&&Serial1.output.find("$PCAS10")==std::string::npos);
#endif
 assert(Serial1.output.find("$PCAS12",1)!=std::string::npos);
 manager.shutdownGpsTransportForDeepSleep();assert(Serial1.ends==1&&!manager.isGpsPowered());
}
'''


class CardputerHardwareTests(unittest.TestCase):
    def compile_run(self, code, *, gps=None):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        arduino = ARDUINO.replace("inline uint32_t delay_count=0;", "inline uint32_t delay_count=0;\n#define MESH_DEBUG_PRINTLN(...)\n#define POWERSAVING_DEBUG_PRINTLN(...)")
        arduino = arduino.replace(" void end(){++ends;}", " explicit operator bool()const{return true;}\n void end(){++ends;}")
        lpp = CAYENNE.replace(" void addTemperature", " void addCurrent(uint8_t,float){}\n void addPower(uint8_t,float){}\n void addTemperature")
        with tempfile.TemporaryDirectory(prefix="cardputer-hardware-") as directory:
            work = Path(directory)
            (work / "driver").mkdir()
            headers = {"Arduino.h": arduino, "Mesh.h": MESH, "RTClib.h": RTC, "Wire.h": WIRE,
                       "CayenneLPP.h": lpp, "driver/i2s.h": I2S,
                       "Adafruit_INA260.h": INA260, "main.cpp": code}
            for name, content in headers.items():
                (work / name).write_text(content, encoding="ascii")
            definitions, sources = [], []
            if gps is None:
                sources = [VARIANT / "hw/CardputerSpeaker.cpp"]
            else:
                definitions = [f"-DENV_INCLUDE_GPS={int(gps)}", "-DCARDPUTER_ADV_UI=0",
                    "-DESP32_PLATFORM", "-DENV_INCLUDE_INA260=1", "-DPIN_GPS_TX=15",
                    "-DPIN_GPS_RX=13", "-DGPS_BAUD_RATE=115200"]
                if not gps:
                    # The unchanged generic no-GPS settings methods retain a
                    # local index even when every conditional setting is absent.
                    definitions += ["-Wno-unused-variable"]
                sources = [ROOT / "src/helpers/SensorManager.cpp",
                           ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp",
                           VARIANT / "CardputerSensorManager.cpp"]
            binary = work / ("hardware.exe" if os.name == "nt" else "hardware")
            sanitizer = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", *sanitizer, *definitions,
                "-I", str(work), "-I", str(ROOT / "src"), "-I", str(VARIANT),
                str(work / "main.cpp"), *map(str, sources), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_speaker_nonblocking_partial_replacement_failure_and_wrap(self):
        self.compile_run(SPEAKER)

    def test_headless_without_gps_provider_preserves_environment_and_standby(self):
        self.compile_run(HEADLESS, gps=False)

    def test_headless_with_gps_has_one_owner_cache_and_external_sensors(self):
        self.compile_run(HEADLESS, gps=True)

    def test_all_roles_compile_the_fixed_manager_without_forcing_gps(self):
        config = (VARIANT / "platformio.ini").read_text(encoding="ascii")
        base = config.split("[M5Stack_Cardputer_Adv]", 1)[1].split("[M5Stack_Cardputer_Adv_ui]", 1)[0]
        self.assertIn("+<helpers/sensors/EnvironmentSensorManager.cpp>", base)
        self.assertIn("+<../variants/m5stack_cardputer_adv/CardputerSensorManager.cpp>", base)
        self.assertNotIn("-D ENV_INCLUDE_GPS=1", base)
        target = (VARIANT / "target.cpp").read_text(encoding="ascii")
        self.assertIn("#if ENV_INCLUDE_GPS", target)
        self.assertIn("CardputerSensorManager sensors(nmea);", target)
        self.assertIn("CardputerSensorManager sensors;", target)
        self.assertNotIn("EnvironmentSensorManager sensors", target)


if __name__ == "__main__":
    unittest.main()
