"""Execute Cardputer GPS policy with real SensorManager and NMEA provider code."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_gps_upstream_adaptations import ARDUINO, MESH, RTC, CAYENNE

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/m5stack_cardputer_adv"
ARDUINO = ARDUINO.replace("#include <deque>", "#include <deque>\n#include <string>\n#include <cstdlib>\n#include <cctype>")
ARDUINO = ARDUINO.replace(" virtual size_t write(uint8_t){return 1;}", r'''
 virtual size_t write(uint8_t){return 1;}
 size_t print(const char* text){size_t n=0;while(*text)n+=write(uint8_t(*text++));return n;}
 size_t print(char c){return write(uint8_t(c));}
''') + r'''
class HardwareSerial:public Stream {
public:
 std::deque<char> incoming;
 std::string output;
 int rx=-1,tx=-1;unsigned baud=0,starts=0,ends=0;
 void setPins(int input,int out){rx=input;tx=out;}
 void begin(unsigned rate){baud=rate;++starts;}
 void end(){++ends;}
 int available()override{return int(incoming.size());}
 int read()override{if(incoming.empty())return -1;char c=incoming.front();incoming.pop_front();return uint8_t(c);}
 size_t write(uint8_t c)override{output+=char(c);return 1;}
 void feed(const std::string& data){for(char c:data)incoming.push_back(c);}
};
inline HardwareSerial Serial1;
'''
HARNESS = r'''
#include <cassert>
#include <cstring>
#include <limits>
#include <string>
#include <MicroNMEA.h>
#include "CardputerSensorManager.h"
#include <helpers/sensors/MicroNMEALocationProvider.h>
void LocationProvider::sendSentence(const char*){}
struct Provider:LocationProvider {
 bool fix=false;long lat=47610000,lon=-122330000,alt=125000;
 unsigned polls=0,starts=0,stops=0,clears=0;
 long getLatitude()override{return lat;}
 long getLongitude()override{return lon;}
 long getAltitude()override{return alt;}
 long satellitesCount()override{return fix?8:0;}
 bool isValid()override{return fix&&lat>=-90000000&&lat<=90000000&&lon>=-180000000&&lon<=180000000;}
 long getTimestamp()override{return 0;}
 void reset()override{}
 void begin()override{++starts;}
 void stop()override{++stops;}
 void loop()override{++polls;}
 // This is what the real pinless MicroNMEA provider reports even in standby.
 bool isEnabled()override{return true;}
 bool clearPositionFix()override{++clears;fix=false;return true;}
 void sendSentence(const char* sentence)override{MicroNMEA::sendSentence(Serial1,sentence);}
 void synced(){markTimeSyncApplied();stopTimeSync();}
};
static unsigned occurrences(const std::string& haystack,const char* needle) {
 unsigned count=0;size_t at=0;
 while((at=haystack.find(needle,at))!=std::string::npos){++count;at+=std::strlen(needle);}
 return count;
}
static void reset(uint32_t time=100) {now_ms=time;delay_count=0;Serial1=HardwareSerial();}
static void setup(CardputerSensorManager& manager,Provider& provider) {
 provider.setPowerSavingProfile(600,1800);assert(manager.begin());
 assert(Serial1.rx==15&&Serial1.tx==13&&Serial1.baud==115200);
 assert(manager.isGPSDetected()&&manager.gpsUsesSerialUart(1)&&!manager.gpsUsesSerialUart(2));
 assert(!manager.isGpsPowered()&&provider.isEnabled());
 assert(Serial1.output=="$PCAS12,65535*1E\r\n");
 assert(!std::strcmp(manager.getSettingValue(0),"0"));
 assert(manager.begin()&&Serial1.starts==1&&delay_count==0);
}
static void bootAndManualOff() {
 reset();Serial1.feed("startup bytes");Provider provider;CardputerSensorManager manager(provider);
 setup(manager,provider);manager.setPowerSavingEnabled(true);
 for(unsigned tick=1;tick<=20;++tick) {
  now_ms=100+tick*3600000U;provider.syncTime();manager.loop();
  assert(!manager.isGpsPowered()&&provider.polls==0);
 }
 assert(occurrences(Serial1.output,"$PCAS10")==0);
 assert(occurrences(Serial1.output,"$PCAS12")>=20&&delay_count==0);
}
static void cycleAndWrap() {
 for(uint32_t start:{100U,UINT32_MAX-599000U,UINT32_MAX-599999U}) {
  reset(start);Provider p;CardputerSensorManager m(p);setup(m,p);
  m.setPowerSavingEnabled(true);assert(m.setSettingValue("gps","1"));
  uint32_t off=p.getNextGPSOff();assert(off==(uint32_t(start+600000U)?uint32_t(start+600000U):1U));
  assert(m.isGpsPowered()&&p.waitingTimeSync()&&!std::strcmp(m.getSettingValue(0),"1"));
  now_ms=off-1;m.loop();assert(m.isGpsPowered());
  now_ms=off;m.loop();assert(!m.isGpsPowered());
  assert(!std::strcmp(m.getSettingValue(0),"1"));
  uint32_t on=p.getNextGPSOn();assert(on==uint32_t(off+1800000U));
  now_ms=on-1;m.loop();assert(!m.isGpsPowered());
  now_ms=on;m.loop();assert(m.isGpsPowered()&&p.clears==2);
  assert(uint32_t(p.getNextGPSOff())==uint32_t(now_ms+600000U));
  assert(delay_count==0&&occurrences(Serial1.output,"$PCAS10,0*1C\r\n")==2);
  p.synced();m.loop();assert(!m.isGpsPowered()); // common early sleep after clock acquisition
 }
}
static void toggles() {
 reset();Provider p;CardputerSensorManager m(p);setup(m,p);
 assert(m.setSettingValue("gps","1")&&m.isGpsPowered());
 m.setPowerSavingEnabled(true);const auto off=p.getNextGPSOff();
 ++now_ms;m.setPowerSavingEnabled(true);assert(p.getNextGPSOff()==off);
 now_ms=uint32_t(off);m.loop();assert(!m.isGpsPowered());
 m.setPowerSavingEnabled(false);assert(m.isGpsPowered()&&!p.getGPSPowerSaving());
 assert(p.getNextGPSOn()==0&&p.getNextGPSOff()==0);
 m.setPowerSavingEnabled(true);assert(p.getNextGPSOff()!=0);
 assert(m.setSettingValue("gps","0")&&!m.isGpsPowered());
 assert(!p.getGPSPowerSaving()&&!p.waitingTimeSync()&&p.getNextGPSOn()==0&&p.getNextGPSOff()==0);
 for(bool save:{false,true,false,true}) {m.setPowerSavingEnabled(save);m.loop();assert(!m.isGpsPowered());}
 assert(m.setSettingValue("gps","1")&&m.isGpsPowered());
 m.shutdownGpsTransportForDeepSleep();assert(!m.isGpsPowered()&&!m.isGPSDetected()&&Serial1.ends==1);
 m.loop();assert(!m.isGpsPowered());
}
static void intervalAndValidity() {
 reset();Provider p;CardputerSensorManager m(p);setup(m,p);
 assert(m.setSettingValue("gps","1"));assert(m.setSettingValue("gps_interval","2"));
 p.fix=true;m.loop();assert(m.node_lat==47.61&&m.node_lon==-122.33&&m.node_altitude==125);
 p.lat=48000000;now_ms+=1999;m.loop();assert(m.node_lat==47.61);
 ++now_ms;m.loop();assert(m.node_lat==48);
 p.lat=91000000;now_ms+=2000;m.loop();assert(m.node_lat==48);
 assert(m.setSettingValue("gps_interval","86400"));p.lat=49000000;m.loop();assert(m.node_lat==49);
 assert(m.setSettingValue("gps_interval","1"));p.lat=50000000;m.loop();assert(m.node_lat==50);
 for(const char* bad:{"","-1","86401","1x"})assert(!m.setSettingValue("gps_interval",bad));
 assert(!m.setSettingValue(nullptr,"1")&&!m.setSettingValue("gps",nullptr));
 assert(!m.setSettingValue("unknown","1"));
}
static void telemetry() {
 reset();Provider p;CardputerSensorManager m(p);setup(m,p);CayenneLPP data(64);
 assert(m.querySensors(TELEM_PERM_BASE,data)&&data.gps_count==0&&!m.isGpsPowered());
 const auto commands=Serial1.output;double lat=0,lon=0;
 m.setTelemetryLocationAccessAvailable(false);m.loop();assert(Serial1.output==commands);
 assert(!m.getCachedGpsPosition(lat,lon));
 assert(m.querySensors(TELEM_PERM_LOCATION,data)&&data.gps_count==0&&m.isGpsPowered());
 assert(!std::strcmp(m.getSettingValue(0),"0")); // telemetry did not change saved intent
 p.fix=true;m.loop();now_ms+=31000;m.loop();
 assert(m.getCachedGpsPosition(lat,lon));
 assert(m.querySensors(TELEM_PERM_LOCATION,data)&&data.gps_count==1);
 assert(m.setSettingValue("gps","0")&&!m.isGpsPowered());
 const auto polls=p.polls;now_ms+=1800000;m.loop();assert(!m.isGpsPowered()&&p.polls==polls);
 data.reset();const auto before=Serial1.output;
 assert(m.querySensors(TELEM_PERM_BASE,data)&&data.gps_count==1&&Serial1.output==before);
 assert(m.querySensors(TELEM_PERM_LOCATION,data)&&data.gps_count==2); // cached, authorized, explicit hold
 assert(m.isGpsPowered());m.setSettingValue("gps","0");
}
static std::string sentence(const char* text) {
 HardwareSerial output;MicroNMEA::sendSentence(output,text);return output.output;
}
static void actualNmeaAndClock() {
 reset(10000);mesh::RTCClock clock;MicroNMEALocationProvider p(Serial1,&clock);
 CardputerSensorManager m(p);p.setPowerSavingProfile(600,1800);assert(m.begin());
 assert(m.setSettingValue("gps","1")&&p.waitingTimeSync());
 for(unsigned second=0;second<4;++second) {
  char rmc[160];std::snprintf(rmc,sizeof(rmc),
   "$GPRMC,1200%02u.00,A,4736.600,N,12219.800,W,0.0,0.0,281026,,,A",second);
  Serial1.feed(sentence("$GPGGA,120000.00,4736.600,N,12219.800,W,1,08,0.9,125.0,M,0.0,M,,"));
  Serial1.feed(sentence(rmc));now_ms+=1100;
  // A real foreground loop drains its bounded 96-byte NMEA batches often.
  // Keep one timestamp per emitted second and never bypass that byte limit.
  for(unsigned poll=0;poll<4&&Serial1.available();++poll)m.loop();
  assert(Serial1.available()==0);
 }
 assert(p.satellitesCount()==8&&p.isValid());
 assert(m.node_lat>47.60&&m.node_lat<47.62&&m.node_lon< -122.32&&m.node_lon> -122.34);
 assert(p.getTimeSyncGeneration()>0&&p.getLastValidTimeSync()==clock.getCurrentTime());
 const double retained=m.node_lat;
 Serial1.feed(sentence("$GPGGA,120005.00,,,,,0,00,99.9,,,,,,"));now_ms+=1100;m.loop();
 assert(!p.isValid()&&p.satellitesCount()==0&&m.node_lat==retained);
 m.setSettingValue("gps","0");const auto generation=p.getTimeSyncGeneration();
 Serial1.feed(sentence("$GPRMC,120010.00,A,4736.600,N,12219.800,W,0.0,0.0,281026,,,A"));
 now_ms+=1100;m.loop();assert(p.getTimeSyncGeneration()==generation&&!m.isGpsPowered());
}
int main(int argc,char** argv) {
 assert(argc==2);const std::string scenario=argv[1];
 if(scenario=="boot")bootAndManualOff();else if(scenario=="cycle")cycleAndWrap();
 else if(scenario=="toggles")toggles();else if(scenario=="interval")intervalAndValidity();
 else if(scenario=="telemetry")telemetry();else if(scenario=="nmea")actualNmeaAndClock();else assert(false);
}
'''


class CardputerGpsTests(unittest.TestCase):
    def test_actual_manager_policy_cache_nmea_and_clock(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a native C++ compiler is required")
        nmea = next((ROOT / ".pio/libdeps").glob("*/MicroNMEA/src/MicroNMEA.cpp"), None)
        self.assertIsNotNone(nmea, "pinned MicroNMEA installed by native tests is required")
        with tempfile.TemporaryDirectory(prefix="cardputer-gps-") as directory:
            work = Path(directory)
            for name, text in {"Arduino.h": ARDUINO, "Mesh.h": MESH, "RTClib.h": RTC,
                               "CayenneLPP.h": CAYENNE, "Wire.h": "#pragma once\nstruct TwoWire {};\n",
                               "main.cpp": HARNESS}.items():
                (work / name).write_text(text, encoding="ascii")
            binary = work / ("gps.exe" if os.name == "nt" else "gps")
            sanitizer = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", *sanitizer, "-DENV_INCLUDE_GPS=1", "-DCARDPUTER_ADV_UI=1",
                "-DPIN_GPS_TX=15", "-DPIN_GPS_RX=13", "-DGPS_BAUD_RATE=115200",
                "-I", str(work), "-I", str(ROOT / "src"), "-I", str(VARIANT),
                "-I", str(nmea.parent), str(work / "main.cpp"), str(nmea),
                str(ROOT / "src/helpers/SensorManager.cpp"),
                str(VARIANT / "CardputerSensorManager.cpp"), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            for scenario in ("boot", "cycle", "toggles", "interval", "telemetry", "nmea"):
                with self.subTest(scenario=scenario):
                    run = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
