"""Host regressions for bounded GPS discovery, query sync and BLE LED opt-out.

Uses the real LocationProvider/SensorManager/MicroNMEA-provider headers and
SensorManager.cpp. The cold-start and sensor-request cases execute extracted
production methods, with only their hardware/library boundaries mocked.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#include <cmath>
#include <deque>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT 0
inline uint32_t now_ms=0;
inline uint32_t delay_count=0;
inline uint32_t millis(){return now_ms;}
inline void delay(uint32_t ms){now_ms+=ms;++delay_count;}
inline void yield(){++now_ms;}
inline void pinMode(int,int){}
inline void digitalWrite(int,int){}
inline int digitalRead(int){return HIGH;}
class Stream {
public:
 virtual ~Stream()=default;
 virtual int available(){return 0;}
 virtual int read(){return -1;}
 virtual size_t write(uint8_t){return 1;}
};
using std::isnan;
'''

MESH = r'''
#pragma once
#include <Arduino.h>
namespace mesh {
class RTCClock {
 uint32_t utc=1790899200U;
public:
 uint32_t getCurrentTime(){return utc;}
 void setCurrentTime(uint32_t value){utc=value;}
};
}
'''

NMEA = r'''
#pragma once
#include <Arduino.h>
class MicroNMEA {
 bool valid=false;
public:
 inline static unsigned clears=0;
 MicroNMEA(char*,size_t){}
 void clear(){++clears;valid=false;}
 bool process(char c){if(c=='\n'){valid=true;return true;}return false;}
 static bool testChecksum(const char*){return true;}
 static void sendSentence(Stream&,const char*){}
 const char* getSentence()const{return "$GPRMC,valid*00";}
 const char* getMessageID()const{return "RMC";}
 bool isValid()const{return valid;}
 long getLatitude()const{return 47610000;}
 long getLongitude()const{return -122330000;}
 bool getAltitude(long& value)const{value=125000;return valid;}
 long getNumSatellites()const{return valid?8:0;}
 uint16_t getYear()const{return valid?2026:0;}
 uint8_t getMonth()const{return valid?10:0;}
 uint8_t getDay()const{return valid?2:0;}
 uint8_t getHour()const{return 12;}
 uint8_t getMinute()const{return 0;}
 uint8_t getSecond()const{return 0;}
};
'''

RTC = r'''
#pragma once
#include <cstdint>
class DateTime {
public:
 DateTime(uint16_t,uint8_t,uint8_t,uint8_t,uint8_t,uint8_t){}
 uint32_t unixtime()const{return 1790942400U;}
};
'''

CAYENNE = r'''
#pragma once
#include <cstddef>
#include <cstdint>
#include <helpers/sensors/LPPDataHelpers.h>
class CayenneLPP {
 uint8_t data[64]={};
 uint8_t size=0;
 void addScalar(uint8_t channel,uint8_t type,float value){
  data[size++]=channel;data[size++]=type;
  size+=LPPData::putFloat(data+size,value,2,LPPData::getMultiplier(type),true);
 }
public:
 unsigned resets=0,gps_count=0;
 explicit CayenneLPP(size_t){}
 void reset(){++resets;size=0;}
 void addVoltage(uint8_t channel,float value){addScalar(channel,LPP_VOLTAGE,value);}
 void addTemperature(uint8_t channel,float value){addScalar(channel,LPP_TEMPERATURE,value);}
 void addGPS(uint8_t channel,float latitude,float longitude,float altitude){
  ++gps_count;data[size++]=channel;data[size++]=LPP_GPS;
  size+=LPPData::putFloat(data+size,latitude,3,LPP_GPS_LAT_LON_MULT,true);
  size+=LPPData::putFloat(data+size,longitude,3,LPP_GPS_LAT_LON_MULT,true);
  size+=LPPData::putFloat(data+size,altitude,3,LPP_GPS_ALT_MULT,true);
 }
 const uint8_t* getBuffer()const{return data;}
 uint8_t getSize()const{return size;}
};
'''

QUERY = r'''
#include <cassert>
#include <limits>
#include <helpers/SensorManager.h>
#include <helpers/sensors/MicroNMEALocationProvider.h>
#include <helpers/sensors/LPPDataHelpers.h>
void LocationProvider::sendSentence(const char*){}
struct Uart:Stream {
 std::deque<char> incoming;
 int available()override{return static_cast<int>(incoming.size());}
 int read()override{if(incoming.empty())return -1;char c=incoming.front();incoming.pop_front();return c;}
 void fix(){incoming.push_back('\n');}
};
struct TestProvider:MicroNMEALocationProvider {
 using MicroNMEALocationProvider::MicroNMEALocationProvider;
 void applied(){markTimeSyncApplied();stopTimeSync();}
 void resetQueryState(){resetTimeSyncRequestState();}
};
struct TestSensors:SensorManager {
 LocationProvider* provider;
 bool detected=true,active=true;
 unsigned queries=0;
 explicit TestSensors(LocationProvider* p):provider(p){}
 LocationProvider* getLocationProvider()override{return provider;}
 bool telemetryGpsDetected()const override{return detected;}
 bool telemetryGpsActive()const override{return active;}
 void telemetryGpsStart()override{active=true;}
 void telemetryGpsStop()override{active=false;}
 void block(bool value){setGpsTelemetryTransportAvailable(!value);}
 bool querySensors(uint8_t,CayenneLPP&)override{++queries;return true;}
};
struct Board {
 int getBattMilliVolts(){return 4200;}
 float getMCUTemperature(){return 25.0f;}
};
uint8_t getTelemetryPermissions(uint8_t perms,int){return perms;}
@CONFIG@
struct SensorRequestHarness {
 TestSensors& sensors;
 CayenneLPP telemetry{64};
 Board board;
 struct {int telemetry_access=0;} _prefs;
 uint8_t reply_data[128]={};
 explicit SensorRequestHarness(TestSensors& s):sensors(s){}
 uint8_t request(uint8_t perms,uint8_t mask=0,uint8_t length=1,
                 size_t reply_capacity=sizeof(reply_data)){
  uint8_t payload[1]={mask};
  size_t payload_len=length;
  const unsigned req_type=3;
@REQUEST@
  return 0;
 }
};
int main(){
 // First acquisition already underway: burst requests must preserve a parsed
 // fix, not call the NMEA provider's clearing force-sync method.
 now_ms=1;Uart uart;mesh::RTCClock clock;
 TestProvider gps(uart,&clock,-1,-1);
 gps.setGPSPowerSaving(true);uart.fix();gps.loop();assert(gps.isValid());
 const unsigned initial_clears=MicroNMEA::clears;
 for(unsigned i=0;i<100;++i){now_ms=i+1;assert(!gps.requestTimeSync(60));}
 assert(MicroNMEA::clears==initial_clears&&gps.isValid());

 // Successful production NMEA sync marks monotonic time and leaves the edge
 // notification available for existing mesh clock consumers.
 now_ms=1100;gps.loop();now_ms=2200;gps.loop();
 assert(!gps.waitingTimeSync());
 assert(gps.consumeTimeSyncApplied());assert(!gps.consumeTimeSyncApplied());
 now_ms=62199;assert(!gps.requestTimeSync(60));
 // A backward/forward RTC correction cannot underflow or defeat the throttle.
 clock.setCurrentTime(1);assert(!gps.requestTimeSync(60));
 clock.setCurrentTime(UINT32_MAX);assert(!gps.requestTimeSync(60));
 now_ms=62200;assert(gps.requestTimeSync(60));
 assert(MicroNMEA::clears==initial_clears+1&&!gps.isValid());
 for(unsigned i=0;i<100;++i){++now_ms;assert(!gps.requestTimeSync(0));}
 assert(MicroNMEA::clears==initial_clears+1);
 // Aborted work is still protected by the last request, even without a fix.
 gps.stopTimeSync();assert(!gps.requestTimeSync(60));
 now_ms=122200;assert(gps.requestTimeSync(60));
 gps.stopTimeSync();assert(gps.requestTimeSync(0)); // explicit zero disables pace only

 // Forced CLI/startup callers retain their previous behavior.
 const unsigned force_clears=MicroNMEA::clears;
 gps.syncTime();assert(MicroNMEA::clears==force_clears+1);

 // millis rollover, including a valid successful sync at zero, is safe.
 TestProvider wrap(uart,&clock,-1,-1);now_ms=UINT32_MAX-1000;wrap.applied();
 now_ms=58998;assert(!wrap.requestTimeSync(60));
 now_ms=58999;assert(wrap.requestTimeSync(60));
 TestProvider zero(uart,&clock,-1,-1);now_ms=0;zero.applied();
 now_ms=59999;assert(!zero.requestTimeSync(60));now_ms=60000;assert(zero.requestTimeSync(60));

 // Clamp huge/negative-converted overrides before multiplication. Signed-long
 // deadline comparisons elsewhere can never see an overflowing long interval.
 TestProvider huge(uart,&clock,-1,-1);now_ms=100;huge.applied();
 now_ms=100+0x7ffffffeU;assert(!huge.requestTimeSync(UINT64_MAX));
 now_ms=100+0x7fffffffU;assert(huge.requestTimeSync(UINT64_MAX));
 huge.stopTimeSync();assert(!huge.requestTimeSync(static_cast<uint64_t>(-1)));

 // Requests denied by transport/presence/mode/null provider do not consume
 // throttle. A powered external rail is not permission to reclaim the UART.
 TestProvider owned(uart,&clock,-1,-1);owned.setGPSPowerSaving(true);owned.stopTimeSync();
 TestSensors sensors(&owned);now_ms=200;sensors.block(true);
 assert(owned.isEnabled());assert(!sensors.requestGpsTelemetryTimeSync(60));
 sensors.block(false);sensors.detected=false;assert(!sensors.requestGpsTelemetryTimeSync(60));
 sensors.detected=true;owned.setGPSPowerSaving(false);assert(!sensors.requestGpsTelemetryTimeSync(60));
 owned.setGPSPowerSaving(true);sensors.provider=nullptr;assert(!sensors.requestGpsTelemetryTimeSync(60));
 sensors.provider=&owned;assert(sensors.requestGpsTelemetryTimeSync(60));
 // A RAK discovery reset intentionally forgets the old singleton's throttle.
 owned.stopTimeSync();owned.resetQueryState();assert(sensors.requestGpsTelemetryTimeSync(60));

 // Execute actual SensorMesh permission and query wiring, including the build
 // override's seconds units. No-location/masked requests cannot stamp a query.
 TestProvider request_gps(uart,&clock,-1,-1);request_gps.setGPSPowerSaving(true);request_gps.stopTimeSync();
 TestSensors request_sensors(&request_gps);SensorRequestHarness requests(request_sensors);
 now_ms=1000;const unsigned before=MicroNMEA::clears;
 requests.request(TELEM_PERM_BASE);requests.request(TELEM_PERM_LOCATION,TELEM_PERM_LOCATION);
 requests.request(TELEM_PERM_LOCATION,0,0);assert(MicroNMEA::clears==before);
 requests.request(TELEM_PERM_LOCATION);assert(MicroNMEA::clears==before+1);
 request_gps.stopTimeSync();
 now_ms=1000+static_cast<uint32_t>(GPS_READ_INTERVAL_SECS)*1000U-1;
 requests.request(TELEM_PERM_LOCATION);assert(MicroNMEA::clears==before+1);
 ++now_ms;requests.request(TELEM_PERM_LOCATION);assert(MicroNMEA::clears==before+2);

 // The production request now bounds complete LPP records to the caller's
 // reply capacity. Exercise real voltage/temperature bytes at both edges of
 // each four-byte record; no partial entry or write beyond the budget is valid.
 for(size_t capacity=4;capacity<=16;++capacity){
  memset(requests.reply_data,0xA5,sizeof(requests.reply_data));
  const uint8_t length=requests.request(TELEM_PERM_BASE,0,1,capacity);
  const size_t payload_size=capacity>=12?8:capacity>=8?4:0;
  const uint8_t* expected=requests.telemetry.getBuffer();
  assert(requests.telemetry.getSize()==8);
  assert(expected[0]==TELEM_CHANNEL_SELF&&expected[1]==LPP_VOLTAGE);
  assert(expected[4]==TELEM_CHANNEL_SELF&&expected[5]==LPP_TEMPERATURE);
  assert(length==4+payload_size&&length<=capacity);
  assert(!memcmp(requests.reply_data+4,expected,payload_size));
  for(size_t offset=length;offset<sizeof(requests.reply_data);++offset){
   assert(requests.reply_data[offset]==0xA5);
  }
 }
}
'''

COLD = r'''
#include <cassert>
#include <Arduino.h>
#define PIN_GPS_TX 8
#define PIN_GPS_RX 7
#define MESH_DEBUG_PRINTLN(...) ((void)0)
struct SerialMock {
 uint32_t start=0,arrival=UINT32_MAX;
 unsigned begins=0,polls=0;
 void setPins(int,int){}
 void begin(int){++begins;start=millis();}
 void end(){}
 int available(){++polls;return arrival!=UINT32_MAX&&static_cast<uint32_t>(millis()-start)>=arrival;}
} Serial1;
struct Location {
 unsigned begins=0,resets=0,stops=0;
 void begin(){++begins;}void reset(){++resets;}void stop(){++stops;}
};
struct EnvironmentSensorManager {
 Location* _location;
 bool gps_serial_transport_blocked=false,gps_serial_transport=false;
 bool gps_serial_open=false;
 bool gps_detected=false,gps_active=false,user_enabled=false;
 unsigned transport_resets=0;
 explicit EnvironmentSensorManager(Location& p):_location(&p){}
 void resetGpsTelemetryTransportState(){++transport_resets;}
 void setGpsTelemetryUserEnabled(bool enabled){user_enabled=enabled;}
 void initBasicGPS();
 void startGpsSerialTransport();
 void stopGpsSerialTransport();
};
@INIT@
int main(){
 for(uint32_t arrival:{0U,1U,250U,2600U,5000U,5001U,UINT32_MAX}){
  now_ms=UINT32_MAX-1000;delay_count=0;Serial1=SerialMock();Serial1.arrival=arrival;
  Location location;EnvironmentSensorManager sensors(location);const uint32_t start=now_ms;
  sensors.initBasicGPS();
#ifdef ENV_SKIP_GPS_DETECT
  assert(sensors.gps_detected&&now_ms==start&&delay_count==0&&Serial1.polls==0);
#else
  const bool found=arrival<=5000;
  const uint32_t elapsed=arrival==0?0:found?((arrival+249)/250)*250:5000;
  assert(sensors.gps_detected==found&&static_cast<uint32_t>(now_ms-start)==elapsed);
  assert(delay_count==elapsed/250&&delay_count<=20);
#endif
  assert(location.begins==1&&location.resets==1&&sensors.transport_resets==1);
  assert(sensors.gps_serial_transport==sensors.gps_detected);
#ifdef PERSISTANT_GPS
  assert(sensors.gps_active==sensors.gps_detected&&sensors.user_enabled==sensors.gps_detected);
  assert(location.stops==static_cast<unsigned>(!sensors.gps_detected));
#else
  assert(!sensors.gps_active&&location.stops==1);
#endif
 }
 Location location;EnvironmentSensorManager blocked(location);
 blocked.gps_serial_transport_blocked=true;delay_count=0;Serial1=SerialMock();
 blocked.initBasicGPS();assert(delay_count==0&&Serial1.begins==0&&Serial1.polls==0);
 assert(location.begins==0&&blocked.transport_resets==0);
}
'''

RAK_UART_PROBE = r'''
#include <cassert>
#include <cstring>
#include <string>
#include <Arduino.h>
#include <helpers/sensors/NmeaSentenceProbe.h>
@TIMEOUT@
@PROBE@
struct Uart : Stream {
 bool continuous_noise=false;
 uint32_t noise_before=0, reads=0;
 std::string bytes;
 int available() override {
   return continuous_noise || noise_before || !bytes.empty();
 }
 int read() override {
   ++reads; ++now_ms;  // A real peripheral clock advances while bytes arrive.
   if (continuous_noise) return '!';
   if (noise_before) { --noise_before; return '!'; }
   if (bytes.empty()) return -1;
   const int value=bytes.front(); bytes.erase(0,1); return value;
 }
 void validSentence() {
   const std::string body="GPRMC,123";
   uint8_t checksum=0;
   for(uint8_t byte:body) checksum^=byte;
   char hex[3]; std::snprintf(hex,sizeof(hex),"%02X",checksum);
   bytes="$"+body+"*"+hex;
 }
};
int main(int argc,char** argv) {
 assert(argc==2);
 Uart uart;
 bool expected=false;
 if(!strcmp(argv[1],"noise")) uart.continuous_noise=true;
 else if(!strcmp(argv[1],"wrap_noise")) {
   now_ms=UINT32_MAX-400U; uart.continuous_noise=true;
 } else if(!strcmp(argv[1],"valid")) {
   uart.validSentence(); expected=true;
 } else if(!strcmp(argv[1],"noisy_valid")) {
   uart.noise_before=600; uart.validSentence(); expected=true;
 } else if(!strcmp(argv[1],"wrap_valid")) {
   now_ms=UINT32_MAX-400U; uart.noise_before=600;
   uart.validSentence(); expected=true;
 } else if(!strcmp(argv[1],"invalid_checksum")) {
   uart.validSentence(); uart.bytes.back()='Z';
 } else if(!strcmp(argv[1],"too_late")) {
   uart.noise_before=RAK_UART_GPS_PROBE_TIMEOUT_MS-2; uart.validSentence();
 } else if(!strcmp(argv[1],"truncated")) {
   uart.bytes="$GPRMC,123*";
 } else assert(!strcmp(argv[1],"empty"));
 const uint32_t started=millis();
 assert(serialHasValidGpsSentence(uart,RAK_UART_GPS_PROBE_TIMEOUT_MS)==expected);
 const uint32_t elapsed=millis()-started;
 if(expected) {
   assert(elapsed<RAK_UART_GPS_PROBE_TIMEOUT_MS && delay_count==0);
 } else {
   assert(elapsed>=RAK_UART_GPS_PROBE_TIMEOUT_MS);
   assert(elapsed<RAK_UART_GPS_PROBE_TIMEOUT_MS+5);
 }
 if(uart.continuous_noise) {
   assert(elapsed==RAK_UART_GPS_PROBE_TIMEOUT_MS && delay_count==0);
   assert(uart.reads==RAK_UART_GPS_PROBE_TIMEOUT_MS);
 }
}
'''


class GpsUpstreamAdaptationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, source, defines=(), extra_sources=(), *, arduino=ARDUINO):
        with tempfile.TemporaryDirectory(prefix="meshcore-gps-upstream-") as directory:
            path = Path(directory)
            for name, text in {
                "Arduino.h": arduino, "Mesh.h": MESH, "MicroNMEA.h": NMEA,
                "RTClib.h": RTC, "CayenneLPP.h": CAYENNE,
                "Wire.h": "#pragma once\nclass TwoWire {};\n",
            }.items():
                (path / name).write_text(text, encoding="utf-8")
            cpp = path / "test.cpp"
            cpp.write_text(source, encoding="utf-8")
            binary = path / "test"
            subprocess.run([
                self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter",
                *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
                *[f"-D{define}" for define in defines],
                "-I", str(path), "-I", str(ROOT / "src"), str(cpp),
                *[str(ROOT / name) for name in extra_sources], "-o", str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)

    def query_harness(self):
        source = (ROOT / "examples/simple_sensor/SensorMesh.cpp").read_text()
        config = re.search(r"#ifndef GPS_READ_INTERVAL_SECS\s*[\s\S]*?#endif", source).group()
        request = extract_braced(source, "if (req_type == REQ_TYPE_GET_TELEMETRY_DATA && payload_len >= 1)")
        return QUERY.replace("@CONFIG@", config).replace("@REQUEST@", request).replace(
            "const unsigned req_type=3;", "const unsigned req_type=3;\n#define REQ_TYPE_GET_TELEMETRY_DATA 3")

    def test_monotonic_query_throttle_permissions_and_ownership(self):
        self.compile_run(self.query_harness(), ("ENV_INCLUDE_GPS=1",),
                         ("src/helpers/SensorManager.cpp",))

    def test_build_override_keeps_seconds_separate_from_position_interval(self):
        self.compile_run(self.query_harness(), ("ENV_INCLUDE_GPS=1", "GPS_READ_INTERVAL_SECS=2"),
                         ("src/helpers/SensorManager.cpp",))

    def test_no_gps_build_keeps_safe_noop(self):
        self.compile_run(r'''
#include <cassert>
#include <helpers/SensorManager.h>
void LocationProvider::sendSentence(const char*){}
int main(){SensorManager sensors;assert(!sensors.requestGpsTelemetryTimeSync(UINT64_MAX));}
''', ("ENV_INCLUDE_GPS=0",), ("src/helpers/SensorManager.cpp",))

    def test_bounded_discovery_fast_cold_absent_bypass_and_uart_owner(self):
        source = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        init = extract_braced(source, "void EnvironmentSensorManager::initBasicGPS()")
        init += "\n" + "\n".join(extract_braced(source, signature) for signature in (
            "void EnvironmentSensorManager::startGpsSerialTransport()",
            "void EnvironmentSensorManager::stopGpsSerialTransport()"))
        for defines in ((), ("ENV_SKIP_GPS_DETECT=1",), ("PERSISTANT_GPS=1",)):
            with self.subTest(defines=defines):
                self.compile_run(COLD.replace("@INIT@", init), defines)

    def test_provider_repeated_begin_stop_preserves_shared_power_owners(self):
        arduino = ARDUINO.replace(
            "inline void digitalWrite(int,int){}\ninline int digitalRead(int){return HIGH;}",
            "inline int pin_values[64]={};\n"
            "inline void digitalWrite(int pin,int value){pin_values[pin]=value;}\n"
            "inline int digitalRead(int pin){return pin_values[pin];}")
        self.compile_run(r'''
#include <cassert>
#include <helpers/sensors/MicroNMEALocationProvider.h>
void LocationProvider::sendSentence(const char*) {}
int main() {
  Stream serial; RefCountedDigitalPin rail(5); rail.begin();
  MicroNMEALocationProvider gps(serial, nullptr, 7, 6, &rail);
  gps.stop(); assert(digitalRead(5)==LOW);
  rail.claim(); // An external owner powers a receiver never begun here.
  for (unsigned attempt=0; attempt<2; ++attempt) {
    digitalWrite(6, HIGH); digitalWrite(7, HIGH);
    gps.stop();
    assert(digitalRead(5)==HIGH && digitalRead(6)==LOW && digitalRead(7)==LOW);
  }
  rail.release(); assert(digitalRead(5)==LOW);
  for (unsigned cycle=0; cycle<300; ++cycle) {
    rail.claim(); // Another peripheral owns the same supply.
    gps.begin(); gps.begin();
    assert(digitalRead(5)==HIGH && digitalRead(6)==HIGH && digitalRead(7)==HIGH);
    gps.stop(); gps.stop();
    assert(digitalRead(5)==HIGH && digitalRead(6)==LOW && digitalRead(7)==LOW);
    rail.release(); assert(digitalRead(5)==LOW);
  }
}
''', arduino=arduino)

    def run_rak_uart_probe(self, *, negative=False):
        source = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        timeout = re.search(r"#ifndef RAK_UART_GPS_PROBE_TIMEOUT_MS\s*[\s\S]*?#endif", source).group()
        probe = extract_braced(source, "static bool serialHasValidGpsSentence(")
        if negative:
            # Reproduce the original continuously nonempty FIFO loop. The
            # process watchdog below bounds this deliberately infinite host
            # control; it does not change the production's existing deadline.
            deadline = "      if (static_cast<uint32_t>(millis() - started) >= timeout_ms) return false;"
            self.assertEqual(probe.count(deadline), 1)
            probe = probe.replace(deadline, "")
        with tempfile.TemporaryDirectory(prefix="meshcore-rak-uart-probe-") as directory:
            work = Path(directory)
            (work / "Arduino.h").write_text(ARDUINO, encoding="ascii")
            cpp, binary = work / "probe.cpp", work / "probe"
            cpp.write_text(RAK_UART_PROBE.replace("@TIMEOUT@", timeout).replace("@PROBE@", probe),
                           encoding="ascii")
            result = subprocess.run([self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter",
                *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
                "-I", str(work), "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if negative:
                with self.assertRaises(subprocess.TimeoutExpired):
                    subprocess.run([str(binary), "noise"], capture_output=True, text=True, timeout=1)
                return
            for scenario in ("noise", "wrap_noise", "valid", "noisy_valid", "wrap_valid",
                             "invalid_checksum", "too_late", "truncated", "empty"):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), scenario], capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rak_uart_probe_deadline_survives_continuous_noise_and_rollover(self):
        self.run_rak_uart_probe()

    def test_rak_original_nonempty_fifo_loop_negative_control(self):
        self.run_rak_uart_probe(negative=True)

    def test_ble_led_optout_preserves_default_and_begin_order(self):
        source = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text()
        begin = extract_braced(source, "bool SerialBLEInterface::begin(")
        block = re.search(r"#ifdef DISABLE_BLE_LED[\s\S]*?#endif", begin).group()
        self.assertLess(begin.index(block), begin.index("Bluefruit.configPrphConn("))
        self.assertLess(begin.index(block), begin.index("if (!Bluefruit.begin())"))
        self.assertLess(begin.index("if (_begin_attempted)"), begin.index(block))
        harness = r'''
#include <cassert>
struct BluefruitMock {
 bool controlled=true;unsigned calls=0;
 void autoConnLed(bool enabled){controlled=enabled;++calls;}
} Bluefruit;
void configure(){
@BLOCK@
}
int main(){
 configure();
#ifdef DISABLE_BLE_LED
 assert(!Bluefruit.controlled&&Bluefruit.calls==1);
#else
 assert(Bluefruit.controlled&&Bluefruit.calls==0);
#endif
 Bluefruit.controlled=false;configure();assert(!Bluefruit.controlled);
}
'''.replace("@BLOCK@", block)
        self.compile_run(harness)
        self.compile_run(harness, ("DISABLE_BLE_LED=1",))

    def test_shared_success_mark_and_rak_discovery_reset_are_wired(self):
        provider = (ROOT / "src/helpers/sensors/MicroNMEALocationProvider.h").read_text()
        rak = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        self.assertIn("markTimeSyncApplied();", provider)
        rak_provider = rak[rak.index("class RAK12500LocationProvider"):rak.index("static RAK12500LocationProvider")]
        self.assertIn("markTimeSyncApplied();", rak_provider)
        self.assertIn("resetTimeSyncRequestState();", rak_provider)
        # Explicit CLI sync remains forced; scheduled power cycles honor the
        # configured hourly cadence, with force-sync retained for legacy0.
        self.assertIn("void syncTime() override { nmea.clear(); LocationProvider::syncTime(); }", provider)
        self.assertIn("_location->syncTimeForPowerSavingCycle();", extract_braced(rak, "void EnvironmentSensorManager::armGpsPowerSavingCycle()"))


if __name__ == "__main__":
    unittest.main()
