"""Execute GPS sync interval policy and both production provider timers."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_gps_upstream_adaptations import ARDUINO, NMEA, RTC
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

MESH = r'''
#pragma once
#include <Arduino.h>
namespace mesh {
class RTCClock {
 uint32_t utc=1790899200U;
public:
 unsigned writes=0;
 uint32_t getCurrentTime(){return utc;}
 void setCurrentTime(uint32_t value){utc=value;++writes;}
};
}
'''

BASE = r'''
#include <cassert>
#include <helpers/GpsPowerPolicy.h>
#include <helpers/sensors/LocationProvider.h>
void LocationProvider::sendSentence(const char*){}
struct Provider:LocationProvider {
 using LocationProvider::periodicTimeSyncIntervalMillis;
 long getLatitude()override{return 0;}
 long getLongitude()override{return 0;}
 long getAltitude()override{return 0;}
 long satellitesCount()override{return 0;}
 bool isValid()override{return false;}
 long getTimestamp()override{return 0;}
 void reset()override{}void begin()override{}void stop()override{}void loop()override{}
 bool isEnabled()override{return true;}
 void applied(){markTimeSyncApplied();stopTimeSync();}
};
'''

PARSER = BASE + r'''
int main(){
 uint16_t hours=77;
 for(const char* value:{"1","01","0001"}){
  assert(mesh::gps::parseSyncIntervalHours(value,hours)&&hours==1);
 }
 for(const char* value:{"336","337","999","4294967296",
                       "999999999999999999999999999999999999999999999999"}){
  assert(mesh::gps::parseSyncIntervalHours(value,hours)&&hours==336);
 }
 assert(mesh::gps::parseSyncIntervalHours("168",hours)&&hours==168);
 for(const char* value:{"","0","000","-1","+1","1.0"," 1","1 ","1h",
                       "999999999999999999999999x","0x10"}){
  hours=77;assert(!mesh::gps::parseSyncIntervalHours(value,hours));assert(hours==77);
 }
 assert(!mesh::gps::parseSyncIntervalHours(nullptr,hours));
 // Position processing keeps its existing seconds semantics and bounds.
 uint32_t seconds=77;
 assert(mesh::gps::parseUpdateInterval("0",seconds)&&seconds==0);
 assert(mesh::gps::updateIntervalMillis(seconds)==1000);
 assert(mesh::gps::parseUpdateInterval("86400",seconds)&&seconds==86400);
 assert(!mesh::gps::parseUpdateInterval("999999",seconds));
}
'''

POWER = BASE + r'''
int main(){
 const uint32_t hour=3600000U;
 Provider gps;now_ms=100;
 assert(gps.getTimeSyncIntervalHours()==0);
 assert(gps.periodicTimeSyncIntervalMillis()==1800000UL);
 gps.setNextWake();assert(gps.getNextWake()==100+24UL*hour);
 gps.setPowerSavingProfile(600,7UL*24UL*3600UL);
 gps.setNextWake();assert(gps.getNextWake()==100+7UL*24UL*hour);
 gps.setNextSleep();assert(gps.getNextSleep()==600100UL);
 gps.setTimeSyncIntervalHours(1);
 assert(gps.getTimeSyncIntervalHours()==1&&gps.periodicTimeSyncIntervalMillis()==hour);
 assert(gps.getNextWake()==100+hour); // Replace an already armed legacy wake.
 gps.setNextSleep();assert(gps.getNextSleep()==600100UL);
 gps.stopTimeSync();gps.setNextWake();assert(gps.getNextWake()==100+hour);
 gps.setTimeSyncIntervalHours(336);
 assert(gps.periodicTimeSyncIntervalMillis()==1209600000UL);
 assert(gps.getNextWake()==100+336UL*hour);
 gps.setNextGPSOn(0);gps.setTimeSyncIntervalHours(1);
 assert(gps.getNextWake()==0); // Changing policy does not start a disabled GPS.

 // Successful time fixes anchor the next scheduled wake; position work cannot
 // postpone the long time-sync interval by repeatedly stopping the receiver.
 now_ms=1000;gps.applied();now_ms=10000;gps.setNextWake();
 assert(gps.getNextWake()==1000+hour);
 now_ms=20000;gps.setNextWake();assert(gps.getNextWake()==1000+hour);
 gps.syncTimeForPowerSavingCycle();assert(!gps.waitingTimeSync());
 now_ms=1000+hour-1;gps.syncTimeForPowerSavingCycle();assert(!gps.waitingTimeSync());
 now_ms=1000+hour;gps.syncTimeForPowerSavingCycle();assert(gps.waitingTimeSync());
 gps.stopTimeSync();
 // An acquisition request without a fix is still paced, including later
 // position wake-ups. It cannot cause every telemetry request to force sync.
 now_ms=1000+hour+5000;gps.syncTimeForPowerSavingCycle();assert(!gps.waitingTimeSync());
 gps.setNextWake();assert(gps.getNextWake()==1000+2UL*hour);
 now_ms=1000+2UL*hour;gps.syncTimeForPowerSavingCycle();assert(gps.waitingTimeSync());
 gps.applied();
 // Direct manual/startup requests always override the configured pace.
 ++now_ms;gps.syncTime();assert(gps.waitingTimeSync());gps.stopTimeSync();
 gps.setTimeSyncIntervalHours(0);
 assert(gps.periodicTimeSyncIntervalMillis()==1800000UL);
 gps.syncTimeForPowerSavingCycle();assert(gps.waitingTimeSync());
 gps.setNextWake();assert(gps.getNextWake()==now_ms+7UL*24UL*hour);

 // Deadline/elapsed arithmetic must remain valid through 32-bit millis wrap.
 Provider wrap;wrap.setTimeSyncIntervalHours(336);
 now_ms=UINT32_MAX-1000U;wrap.applied();
 now_ms=1000;wrap.setNextWake();
 assert(static_cast<uint32_t>(wrap.getNextWake())==
        static_cast<uint32_t>(UINT32_MAX-1000U+336U*hour));
 wrap.syncTimeForPowerSavingCycle();assert(!wrap.waitingTimeSync());
 now_ms=static_cast<uint32_t>(UINT32_MAX-1000U+336U*hour);
 wrap.syncTimeForPowerSavingCycle();assert(wrap.waitingTimeSync());
 // Exhausting the acquisition window cannot leave an immediate retry loop.
 wrap.stopTimeSync();now_ms+=336U*hour;wrap.setNextWake();
 assert(static_cast<uint32_t>(wrap.getNextWake())==now_ms+336U*hour);
 // The deadline's zero sentinel must not lose a real wake across rollover.
 Provider zero;zero.setTimeSyncIntervalHours(1);now_ms=0U-hour;
 zero.setNextWake();assert(zero.getNextWake()==1);
}
'''

PROVIDERS = r'''
#include <cassert>
#include <helpers/sensors/MicroNMEALocationProvider.h>
void LocationProvider::sendSentence(const char*){}
struct Uart:Stream {
 std::deque<char> incoming;
 int available()override{return static_cast<int>(incoming.size());}
 int read()override{char c=incoming.front();incoming.pop_front();return c;}
 void fix(){incoming.push_back('\n');}
};
struct Gnss {
 void flushPVT(){}
 bool getPVT(int){return true;}
 bool getGnssFixOk(int){return true;}
 long getLatitude(int){return 476100000;}
 long getLongitude(int){return -1223300000;}
 long getAltitude(int){return 125000;}
 int getSIV(int){return 8;}
 bool getDateValid(int){return true;}
 bool getTimeValid(int){return true;}
 long getUnixEpoch(int){return 1790942400;}
} ublox_GNSS;
@RAK@
struct NmeaProvider:MicroNMEALocationProvider {
 using MicroNMEALocationProvider::MicroNMEALocationProvider;
 uint32_t lastApplied()const{return _last_time_sync_applied_ms;}
};
struct RakProvider:RAK12500LocationProvider {
 uint32_t lastApplied()const{return _last_time_sync_applied_ms;}
};
struct NmeaFixture {
 Uart uart;mesh::RTCClock clock;NmeaProvider gps;
 NmeaFixture():gps(uart,&clock,-1,-1){}
 void tick(){uart.fix();gps.loop();}
};
struct RakFixture {
 mesh::RTCClock clock;RakProvider gps;
 RakFixture(){gps.setRTCClock(&clock);}
 void tick(){gps.loop();}
};
template<class Fixture>void interval(uint16_t hours,uint32_t start){
 Fixture fixture;
 fixture.gps.setTimeSyncIntervalHours(hours);
 // A receiver has already been serviced before uptime reaches rollover.
 // Advance in gaps below half the target tick range to keep its sample timer
 // meaningful, then request the test's reference sync at the selected start.
 if(start>INT32_MAX){
  for(uint32_t warm:{1U,1073741824U,2147483647U,3221225470U}){
   now_ms=warm;fixture.tick();
  }
 }
 const unsigned before=fixture.clock.writes;
 fixture.gps.syncTime();
 now_ms=start;fixture.tick();
 if(hours&&fixture.gps.waitingTimeSync()){
  const unsigned clears=MicroNMEA::clears;
  fixture.gps.syncTimeForPowerSavingCycle();
  assert(MicroNMEA::clears==clears&&fixture.gps.waitingTimeSync());
 }
 now_ms+=1100;fixture.tick();now_ms+=1100;fixture.tick();
 assert(fixture.clock.writes==before+1&&!fixture.gps.waitingTimeSync());
 const uint32_t applied=fixture.gps.lastApplied();
 const uint32_t interval_ms=hours?static_cast<uint32_t>(hours)*3600000U:1800000U;
 now_ms=applied+interval_ms-1U;fixture.tick();assert(fixture.clock.writes==before+1);
 now_ms+=1100;fixture.tick();assert(fixture.clock.writes==before+2);
 assert(!fixture.gps.waitingTimeSync()&&fixture.gps.consumeTimeSyncApplied());

 // Manual sync remains immediate even shortly after a periodic write.
 now_ms+=1100;fixture.gps.syncTime();assert(fixture.gps.waitingTimeSync());
 fixture.tick();now_ms+=1100;fixture.tick();now_ms+=1100;fixture.tick();
 assert(fixture.clock.writes==before+3&&!fixture.gps.waitingTimeSync());
 // Unrelated position acquisition reuses the last actual sync rather than
 // resetting the clock before the selected interval has elapsed.
 now_ms+=1100;fixture.gps.syncTimeForPowerSavingCycle();
 if(hours){assert(!fixture.gps.waitingTimeSync());fixture.tick();assert(fixture.clock.writes==before+3);}
 else{assert(fixture.gps.waitingTimeSync());}
}
int main(){
 for(uint16_t hours:{0U,1U,336U}){
  interval<NmeaFixture>(hours,1);interval<RakFixture>(hours,1);
  // Host Windows uses the target's 32-bit unsigned long ABI. On a 64-bit
  // Unix host base policy rollover remains covered by the separate harness.
  if(sizeof(unsigned long)==sizeof(uint32_t)){
   interval<NmeaFixture>(hours,UINT32_MAX-3000U);
   interval<RakFixture>(hours,UINT32_MAX-3000U);
  }
 }
}
'''

TELEMETRY = r'''
#include <cassert>
#include <helpers/SensorManager.h>
void LocationProvider::sendSentence(const char*){}
void SensorManager::setTelemetryLocationAccessAvailable(bool){}
struct Provider:LocationProvider {
 long getLatitude()override{return 0;}long getLongitude()override{return 0;}
 long getAltitude()override{return 0;}long satellitesCount()override{return 0;}
 bool isValid()override{return false;}long getTimestamp()override{return 0;}
 void reset()override{}void begin()override{}void stop()override{}void loop()override{}
 bool isEnabled()override{return true;}
 void applied(){markTimeSyncApplied();stopTimeSync();}
};
struct Sensors:SensorManager {
 Provider& gps;explicit Sensors(Provider& p):gps(p){}
 bool telemetryGpsDetected()const override{return true;}
 LocationProvider* getLocationProvider()override{return &gps;}
};
int main(){
 Provider gps;Sensors sensors(gps);gps.setGPSPowerSaving(true);
 gps.setTimeSyncIntervalHours(336);now_ms=1000;gps.applied();
 now_ms=1000+60000;assert(!sensors.requestGpsTelemetryTimeSync(60));
 now_ms=1000+336UL*3600000UL-1;assert(!sensors.requestGpsTelemetryTimeSync(60));
 ++now_ms;assert(sensors.requestGpsTelemetryTimeSync(60));
 gps.applied();gps.setTimeSyncIntervalHours(0);
 now_ms+=59999;assert(!sensors.requestGpsTelemetryTimeSync(60));
 ++now_ms;assert(sensors.requestGpsTelemetryTimeSync(60));
}
'''


class GpsSyncIntervalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, source):
        with tempfile.TemporaryDirectory(prefix="meshcore-gps-sync-") as directory:
            path = Path(directory)
            for name, content in {
                "Arduino.h": ARDUINO, "Mesh.h": MESH, "MicroNMEA.h": NMEA,
                "RTClib.h": RTC, "CayenneLPP.h": "#pragma once\nclass CayenneLPP {};\n",
                "Wire.h": "#pragma once\nclass TwoWire {};\n",
            }.items():
                (path / name).write_text(content, encoding="utf-8")
            cpp = path / "test.cpp"
            cpp.write_text(source, encoding="utf-8")
            binary = path / "test"
            subprocess.run([
                self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-DENV_INCLUDE_GPS=1",
                *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
                "-I", str(path), "-I", str(ROOT / "src"), str(cpp), "-o", str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)

    def test_positive_whole_hours_clamp_without_overflow(self):
        self.compile_run(PARSER)

    def test_power_saving_interval_reconfiguration_and_rollover(self):
        self.compile_run(POWER)

    def test_both_providers_use_hours_keep_legacy_and_allow_manual_sync(self):
        source = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        rak = extract_braced(source, "class RAK12500LocationProvider") + ";"
        self.compile_run(PROVIDERS.replace("@RAK@", rak))

    def test_position_telemetry_cannot_bypass_selected_time_interval(self):
        self.compile_run(TELEMETRY)


if __name__ == "__main__":
    unittest.main()
