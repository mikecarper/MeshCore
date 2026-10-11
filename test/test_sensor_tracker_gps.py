"""Execute production GPS ownership and fresh-acquisition paths on the host."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_gps_upstream_adaptations import ARDUINO, MESH, NMEA, RTC, CAYENNE
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <limits>
#include <helpers/SensorManager.h>
#include <helpers/sensors/MicroNMEALocationProvider.h>
void LocationProvider::sendSentence(const char*){}
struct Uart:Stream {
 std::deque<char> incoming;
 bool replenish=false;
 int available()override{return static_cast<int>(incoming.size());}
 int read()override{
  if(incoming.empty())return -1;
  char c=incoming.front();incoming.pop_front();
  if(replenish)incoming.push_back('\n');
  return c;
 }
 void fix(){incoming.push_back('\n');}
};
struct TestProvider:MicroNMEALocationProvider {
 using MicroNMEALocationProvider::MicroNMEALocationProvider;
 void validated(){markTimeSyncApplied();stopTimeSync();}
};
class TestSensors:public SensorManager {
public:
 Uart uart;
 mesh::RTCClock clock;
 TestProvider provider{uart,&clock};
 bool detected=true,active=false,provider_present=true;
 unsigned starts=0,stops=0;
 LocationProvider* getLocationProvider()override{return provider_present?&provider:nullptr;}
 bool telemetryGpsDetected()const override{return detected;}
 bool telemetryGpsActive()const override{return active;}
 void telemetryGpsStart()override{active=true;++starts;}
 void telemetryGpsStop()override{active=false;++stops;}
 void user(bool value){setGpsTelemetryUserEnabled(value);}
 bool userEnabled()const{return isGpsTelemetryUserEnabled();}
 void blocked(bool value){setGpsTelemetryTransportAvailable(!value);}
 void resetTransport(){resetGpsTelemetryTransportState();}
 void hardwareStop(){telemetryGpsStop();}
 void service(){loopGpsTelemetry(millis());}
 void fix(float lat=47.61f,float lon=-122.33f){processGpsTelemetryFix(lat,lon,125,millis());}
 bool query(uint8_t perms=TELEM_PERM_LOCATION){CayenneLPP data(64);return queryGpsTelemetry(perms,data);}
 bool required()const{return gpsTelemetryReceiverRequired(millis());}
 bool pendingMode()const{return isTrackerGpsModeEnabled();}
 void sampleProvider(){
  service();if(active)provider.loop();
  if(active&&provider.isValid())fix();
 }
 void finishClock(){provider.validated();service();}
};
class T1000SensorManager:public TestSensors {
public:
 LocationProvider* _nmea=&provider;
 bool& gps_active=active;
 void start_gps(){telemetryGpsStart();}
 void stop_gps(){telemetryGpsStop();}
 void armGpsPowerSavingCycle(){_nmea->setNextSleep();}
 void setPowerSavingEnabled(bool enabled)override;
};
class EnvironmentSensorManager:public TestSensors {
public:
 LocationProvider* _location=&provider;
 bool& gps_active=active;
 bool& gps_detected=detected;
 void start_gps(){telemetryGpsStart();}
 void stop_gps(){telemetryGpsStop();}
 void armGpsPowerSavingCycle(){_location->setNextSleep();}
 void setPowerSavingEnabled(bool enabled)override;
};
@T1000_SETTER@
@ENV_SETTER@
void freshWindows(){
 now_ms=100;TestSensors s;double lat=0,lon=0;
 s.user(true);assert(s.active&&s.userEnabled());
 s.fix();assert(s.getCachedGpsPosition(lat,lon));
 s.uart.fix();s.provider.loop();assert(s.provider.isValid());
 s.setTrackerGpsModeEnabled(true);
 assert(!s.active&&s.userEnabled()&&!s.provider.getGPSPowerSaving());
 assert(!s.takeTrackerGpsPosition(lat,lon)&&lat==0&&lon==0);
 s.uart.fix(); // old UART bytes queued while receiver was off
 assert(s.beginTrackerGpsAcquisition());
 assert(s.uart.incoming.empty()&&!s.provider.isValid());
 s.sampleProvider();assert(s.isTrackerGpsAcquisitionPending());
 assert(!s.takeTrackerGpsPosition(lat,lon));
 assert(s.query());assert(s.isTrackerGpsAcquisitionPending());
 s.setTelemetryLocationAccessAvailable(true);
 s.setTelemetryLocationAccessAvailable(false);
 assert(s.isTrackerGpsAcquisitionPending());
 s.uart.fix();++now_ms;s.sampleProvider();
 assert(s.active&&s.isTrackerGpsAcquisitionPending());
 assert(!s.takeTrackerGpsPosition(lat,lon));s.finishClock();
 assert(!s.active&&!s.required()&&!s.isTrackerGpsAcquisitionPending());
 assert(s.takeTrackerGpsPosition(lat,lon)&&lat>47&&lon< -122);
 assert(!s.takeTrackerGpsPosition(lat,lon));
 now_ms+=1800000;s.service();assert(!s.active);
 assert(s.beginTrackerGpsAcquisition());
 s.sampleProvider();assert(s.isTrackerGpsAcquisitionPending());
 s.uart.fix();++now_ms;s.sampleProvider();s.finishClock();assert(s.takeTrackerGpsPosition(lat,lon));
 // A long normal gps_interval must not delay the tracker's fresh result.
 assert(s.setSettingValue("gps_interval","86400"));
 assert(s.beginTrackerGpsAcquisition());s.uart.fix();s.provider.loop();
 ++now_ms;s.service();assert(s.active);s.finishClock();
 assert(!s.active&&s.takeTrackerGpsPosition(lat,lon));
 // A ready result is only for the current check, with its own short age bound.
 assert(s.beginTrackerGpsAcquisition());++now_ms;s.fix();s.finishClock();now_ms+=120000;
 assert(!s.takeTrackerGpsPosition(lat,lon));
 s.setTrackerGpsModeEnabled(false);assert(s.active&&s.userEnabled());
}
void timeoutAndInvalid(){
 for(uint32_t started:{500U,UINT32_MAX-60000U}){
  now_ms=started;TestSensors s;double lat=0,lon=0;
  s.setTrackerGpsModeEnabled(true);assert(s.beginTrackerGpsAcquisition());
  s.fix(std::numeric_limits<float>::quiet_NaN(),1);
  s.fix(91,1);s.fix(1,181);s.fix(0,0);
  assert(s.isTrackerGpsAcquisitionPending()&&!s.takeTrackerGpsPosition(lat,lon));
  now_ms=started+60000;assert(s.beginTrackerGpsAcquisition());
  now_ms=started+119999;s.service();assert(s.active);
  now_ms=started+120000;s.service();
  assert(!s.active&&!s.isTrackerGpsAcquisitionPending());
  s.fix();assert(!s.takeTrackerGpsPosition(lat,lon));
  // Callback itself also enforces the deadline, even before the service loop.
  assert(s.beginTrackerGpsAcquisition());now_ms+=120000;s.fix();
  assert(!s.active&&!s.takeTrackerGpsPosition(lat,lon));
 }
}
void ownershipAndRestore(){
 now_ms=1;TestSensors s;double lat=0,lon=0;
 s.powersaving_enabled=true;s.user(true);s.provider.setGPSPowerSaving(true);
 s.setTrackerGpsModeEnabled(true);assert(!s.active);
 s.blocked(true);unsigned starts=s.starts;
 assert(!s.beginTrackerGpsAcquisition());assert(!s.requestGpsTelemetryTimeSync(1));
 s.service();assert(s.starts==starts);
 s.blocked(false);assert(!s.active&&s.starts==starts);
 assert(s.beginTrackerGpsAcquisition());
 s.hardwareStop();s.blocked(true);assert(!s.isTrackerGpsAcquisitionPending());
 assert(!s.takeTrackerGpsPosition(lat,lon));
 s.blocked(false);s.resetTransport();assert(!s.isTrackerGpsAcquisitionPending());
 s.user(false);assert(!s.userEnabled());
 assert(s.beginTrackerGpsAcquisition());s.setTrackerGpsModeEnabled(false);
 assert(!s.active&&!s.userEnabled()&&!s.provider.getGPSPowerSaving());
 s.setTrackerGpsModeEnabled(true);s.user(true);assert(!s.active);
 assert(s.beginTrackerGpsAcquisition());s.setTrackerGpsModeEnabled(false);
 assert(s.active&&s.userEnabled()&&s.provider.getGPSPowerSaving());
 // Failed discovery and providers unable to invalidate a retained fix fail closed.
 s.setTrackerGpsModeEnabled(true);s.detected=false;
 assert(!s.beginTrackerGpsAcquisition());s.detected=true;s.provider_present=false;
 assert(!s.beginTrackerGpsAcquisition());
}
template<class Sensors>void derivedPowerPreference(){
 now_ms=1;Sensors s;s.user(true);s.setTrackerGpsModeEnabled(true);
 s.setPowerSavingEnabled(true);assert(!s.active&&!s.provider.getGPSPowerSaving());
 assert(s.beginTrackerGpsAcquisition());
 s.setPowerSavingEnabled(false);assert(s.active&&!s.provider.getGPSPowerSaving());
 s.user(false);assert(s.active); // current window remains owned by tracker
 s.fix();s.finishClock();assert(!s.active);
 s.setTrackerGpsModeEnabled(false);assert(!s.active&&!s.userEnabled());
 s.user(true);assert(s.active);s.setPowerSavingEnabled(true);
 assert(s.provider.getGPSPowerSaving()); // normal mode still uses its power cycle
}
void fifoBound(){
 Uart uart;MicroNMEALocationProvider provider(uart);
 uart.fix();provider.loop();assert(provider.isValid());
 uart.fix();uart.replenish=true;
 assert(provider.clearPositionFix());
 assert(!provider.isValid()&&uart.available()==1); // new arrival was retained
 uart.replenish=false;provider.loop();assert(provider.isValid());
 for(int i=0;i<1025;++i)uart.fix();
 assert(!provider.clearPositionFix()&&!provider.isValid());
 assert(uart.available()==1025); // fixed cap fails without a partial drain
}
void ordinaryMode(){
 now_ms=10;TestSensors s;assert(!s.beginTrackerGpsAcquisition());
 assert(!s.query(0)&&!s.active);assert(!s.query()&&s.active);
 now_ms+=30001;s.fix();now_ms+=30001;s.fix();
 assert(s.query()&&s.active); // ordinary authorized query retains its 2h hold
}
void coldClockAndTimeoutPosition(){
 now_ms=1;TestSensors s;double lat=0,lon=0;
 s.clock.setCurrentTime(1772323200); // actual nRF cold-start fallback
 s.setTrackerGpsModeEnabled(true);assert(s.beginTrackerGpsAcquisition());
 s.uart.fix();s.sampleProvider();
 assert(s.active&&!s.takeTrackerGpsPosition(lat,lon));
 assert(s.clock.getCurrentTime()==1772323200);
 // Newly parsed samples, spaced by the provider's existing validation period.
 for(int i=0;i<2;++i){now_ms+=1001;s.uart.fix();s.sampleProvider();assert(s.active);}
 now_ms+=1001;s.uart.fix();s.sampleProvider();
 s.service();assert(!s.active&&s.takeTrackerGpsPosition(lat,lon));
 assert(s.clock.getCurrentTime()==1790942400);
 // A consumed old success edge still cannot satisfy a new acquisition.
 assert(s.provider.consumeTimeSyncApplied());
 assert(s.beginTrackerGpsAcquisition());s.uart.fix();s.sampleProvider();
 assert(s.active&&!s.takeTrackerGpsPosition(lat,lon));
 const uint32_t started=now_ms;
 // Retained parser validity without fresh input is not a fresh time sample.
 for(int i=0;i<4;++i){now_ms+=1001;s.sampleProvider();assert(s.active);}
 assert(s.clock.getCurrentTime()==1790942400);
 now_ms=started+120000;assert(s.active);
 assert(s.takeTrackerGpsPosition(lat,lon)&&!s.active); // finalize before sensor loop
 assert(!s.takeTrackerGpsPosition(lat,lon));
 // Another component consuming the edge cannot steal the generation change.
 assert(s.beginTrackerGpsAcquisition());s.uart.fix();s.sampleProvider();
 s.provider.validated();assert(s.provider.consumeTimeSyncApplied());
 s.service();assert(!s.active&&s.takeTrackerGpsPosition(lat,lon));
}
int main(){
 freshWindows();timeoutAndInvalid();ownershipAndRestore();
 derivedPowerPreference<T1000SensorManager>();
 derivedPowerPreference<EnvironmentSensorManager>();fifoBound();ordinaryMode();
 coldClockAndTimeoutPosition();
}
'''

I2C = r'''
#include <cassert>
#include <helpers/sensors/LocationProvider.h>
void LocationProvider::sendSentence(const char*){}
struct Gnss {
 bool available=false,stale=true;unsigned flushes=0;
 void flushPVT(){++flushes;}
 bool getPVT(int){return available;}
 bool getGnssFixOk(int){return available||stale;}
 long getLatitude(int){return 476100000;}
 long getLongitude(int){return -1223300000;}
 long getAltitude(int){return 125000;}
 int getSIV(int){return 8;}
 bool getDateValid(int){return true;}
 bool getTimeValid(int){return true;}
 long getUnixEpoch(int){return 1790942400;}
} ublox_GNSS;
@RAK_PROVIDER@;
int main(){
 now_ms=1;RAK12500LocationProvider provider;
 provider.loop();assert(provider.isValid());
 assert(provider.clearPositionFix());assert(!provider.isValid());
 assert(ublox_GNSS.flushes==1&&ublox_GNSS.stale);
 provider.loop();assert(!provider.isValid());
 ublox_GNSS.available=true;now_ms+=1000;
 provider.loop();assert(provider.isValid()&&provider.getLatitude()==47610000);
 mesh::RTCClock clock;provider.setRTCClock(&clock);
 for(int i=0;i<2;++i){now_ms+=1000;provider.loop();}
 assert(clock.getCurrentTime()==1790942400); // prior window already had >2 valid samples
 clock.setCurrentTime(1772323200);
 assert(provider.clearPositionFix());provider.beginFreshTimeSync();
 const uint32_t generation=provider.getTimeSyncGeneration();
 provider.loop();assert(provider.isValid()&&clock.getCurrentTime()==1772323200);
 now_ms+=1000;provider.loop();assert(clock.getCurrentTime()==1772323200);
 now_ms+=1000;provider.loop();assert(clock.getCurrentTime()==1790942400);
 assert(provider.getTimeSyncGeneration()==generation+1);
 clock.setCurrentTime(1772323200);
 assert(provider.clearPositionFix());provider.beginFreshTimeSync();provider.loop();
 ublox_GNSS.available=false;now_ms+=1000;provider.loop();assert(!provider.isValid());
 now_ms+=1000;provider.loop();assert(clock.getCurrentTime()==1772323200);
 assert(provider.getTimeSyncGeneration()==generation+1);
 ublox_GNSS.available=true;
 for(int i=0;i<3;++i){now_ms+=1000;provider.loop();}
 assert(clock.getCurrentTime()==1790942400);
 assert(provider.getTimeSyncGeneration()==generation+2);
}
'''


class SensorTrackerGpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, source, gps=1, sources=(), manager_source=None,
                    expect_failure=False):
        with tempfile.TemporaryDirectory(prefix="meshcore-tracker-gps-") as directory:
            path = Path(directory)
            for name, value in {
                "Arduino.h": ARDUINO, "Mesh.h": MESH, "MicroNMEA.h": NMEA,
                "RTClib.h": RTC, "CayenneLPP.h": CAYENNE,
                "Wire.h": "#pragma once\nclass TwoWire {};\n",
            }.items():
                (path / name).write_text(value, encoding="ascii")
            cpp, binary = path / "test.cpp", path / "test"
            cpp.write_text(source, encoding="ascii")
            compiled_sources = [str(ROOT / name) for name in sources]
            if manager_source is not None:
                manager = path / "SensorManager.cpp"
                manager.write_text(manager_source, encoding="ascii")
                compiled_sources = [str(manager)]
            subprocess.run([
                self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", f"-DENV_INCLUDE_GPS={gps}",
                *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
                "-I", str(path), "-I", str(ROOT / "src"),
                "-I", str(ROOT / "src/helpers"), str(cpp),
                *compiled_sources, "-o", str(binary),
            ], check=True, timeout=60)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if expect_failure:
                self.assertNotEqual(result.returncode, 0, "negative control escaped the regression")
                self.assertIn("Assertion", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def harness(self):
        t1000 = (ROOT / "variants/t1000-e/target.cpp").read_text()
        environment = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        return HARNESS.replace("@T1000_SETTER@", extract_braced(
            t1000, "void T1000SensorManager::setPowerSavingEnabled(")).replace(
            "@ENV_SETTER@", extract_braced(
                environment, "void EnvironmentSensorManager::setPowerSavingEnabled("))
    def test_fresh_acquisition_time_bound_transport_and_preferences(self):
        self.compile_run(self.harness(), sources=("src/helpers/SensorManager.cpp",))

    def test_missing_freshness_and_timeout_negative_controls(self):
        source = (ROOT / "src/helpers/SensorManager.cpp").read_text()
        for old, replacement in (
            ("location == nullptr || !location->clearPositionFix()", "location == nullptr"),
            ("if (gps_acquiring\n        && static_cast<uint32_t>(now - gps_acquire_started_at) >= GPS_TRACKER_MAX_ACQUIRE_MS)",
             "if (gps_acquiring && false)"),
        ):
            with self.subTest(missing=old):
                self.assertEqual(source.count(old), 1)
                self.compile_run(self.harness(), manager_source=source.replace(old, replacement),
                                 expect_failure=True)

    def test_i2c_discards_library_cached_pvt_before_fresh_poll(self):
        source = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        provider = extract_braced(source, "class RAK12500LocationProvider")
        self.compile_run(I2C.replace("@RAK_PROVIDER@", provider))

    def test_i2c_previous_validated_samples_must_reset_negative_control(self):
        source = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        provider = extract_braced(source, "class RAK12500LocationProvider")
        reset = "void syncTime() override {\n    _valid_time_samples = 0;"
        self.assertEqual(provider.count(reset), 1)
        provider = provider.replace(reset, "void syncTime() override {")
        self.compile_run(I2C.replace("@RAK_PROVIDER@", provider), expect_failure=True)

    def test_no_gps_build_has_no_side_effects(self):
        self.compile_run(r'''
#include <cassert>
#include <helpers/SensorManager.h>
void LocationProvider::sendSentence(const char*){}
int main(){SensorManager s;double lat=5,lon=6;s.setTrackerGpsModeEnabled(true);
 assert(!s.beginTrackerGpsAcquisition()&&!s.isTrackerGpsAcquisitionPending());
 assert(!s.takeTrackerGpsPosition(lat,lon)&&lat==0&&lon==0);}
''', gps=0, sources=("src/helpers/SensorManager.cpp",))


if __name__ == "__main__":
    unittest.main()
