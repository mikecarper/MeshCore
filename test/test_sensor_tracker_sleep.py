"""Execute Sensor tracker sleep gates, real wake arming and radio handoff."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_gps_upstream_adaptations import ARDUINO
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

DEADLINES = r'''
#include <Arduino.h>
#include <cassert>
#define SENSOR_READ_INTERVAL_SECS 60
bool logging=false;
namespace mesh {bool isUsbLoggingWatchdogArmed(){return logging;}}
struct Board {bool usb=false,ota=false,test=false;
 bool isUsbDataConnected()const{return usb;}
 bool isOTAUpdateRunning()const{return ota;}
 bool isRadioTestActive()const{return test;}
};
struct Radio {bool watchdog=false,calibration=false;
 bool isWatchdogObserving()const{return watchdog;}
 bool isCalibratingNoiseFloor()const{return calibration;}
} radio_driver;
struct Cli {bool gpio=false;bool hasActiveUserGpioTimer()const{return gpio;}};
struct Manager {int queued=0;int getOutboundTotal()const{return queued;}};
struct Clock {uint32_t now=100;uint32_t getCurrentTime()const{return now;}};
class SensorMesh {
public:
 Board board;Cli _cli;Manager manager;Manager* _mgr=&manager;Clock rtc;
#if MESH_ENABLE_SENSOR_TRACKER
 struct {bool enabled=true;} tracker;
 bool tracker_pending=false,tracker_acquiring=false,tracker_storage_fault=false;
 unsigned long tracker_awake_until=0;uint32_t tracker_next_check=1900;
#endif
 bool dual=false,ota=false,temp=false,outbound=false;
 bool queue_due=false,retry_due=false,queue_delay_set=false,retry_delay_set=false;
 uint32_t queue_delay=0,retry_delay=0;
 unsigned long next_flood_advert=0,next_local_advert=0,dirty_contacts_expiry=0;
 unsigned long radio_apply_retry_at=0,set_radio_at=0,revert_radio_at=0;
 bool saved_radio_apply_pending=false,temp_radio_applied=false;
 uint32_t last_read_time=100;
 struct Alert {unsigned long send_expiry=0;} alert;
 Alert* alert_tasks[1]={&alert};int num_alert_tasks=0;
 const Clock* getRTCClock()const{return &rtc;}
 bool millisHasNowPassed(unsigned long time)const{return (int32_t)(millis()-time)>=0;}
 bool isDualRadioActive()const{return dual;}
 bool hasPendingOtaApply()const{return ota;}
 bool isAnyTempRadioActive()const{return temp;}
 bool hasOutbound()const{return outbound;}
 bool hasQueuedWorkDue()const{return queue_due;}
 bool hasRetryWorkDue()const{return retry_due;}
 bool getNextQueueWakeDelay(uint32_t& out)const{out=queue_delay;return queue_delay_set;}
 bool getNextRetryWakeDelay(uint32_t& out)const{out=retry_delay;return retry_delay_set;}
 bool isMillisTimerDue(unsigned long timestamp)const;
 uint32_t limitSleepToMillisTimer(unsigned long timestamp,uint32_t sleep_secs)const;
 bool hasPendingWork()const;
 uint32_t getPowerSaveSleepSeconds(uint32_t max_secs)const;
#if MESH_ENABLE_SENSOR_TRACKER
 bool trackerNeedsRadio()const;
#endif
};
@METHODS@
int main(){
 now_ms=1000;SensorMesh s;
 assert(!s.hasPendingWork()&&s.getPowerSaveSleepSeconds(30)==30);
#if MESH_ENABLE_SENSOR_TRACKER
 assert(!s.trackerNeedsRadio());
 s.tracker_next_check=107;assert(s.getPowerSaveSleepSeconds(30)==7);
 s.tracker_next_check=100;assert(s.hasPendingWork()&&s.trackerNeedsRadio());
 assert(s.getPowerSaveSleepSeconds(30)==0);s.tracker_next_check=1900;
 for(bool* pending:{&s.tracker_pending,&s.tracker_acquiring}){
  *pending=true;assert(s.hasPendingWork()&&s.trackerNeedsRadio());
  assert(s.getPowerSaveSleepSeconds(30)==0);*pending=false;
 }
 s.tracker_awake_until=1001;assert(s.hasPendingWork()&&s.trackerNeedsRadio());
 ++now_ms;assert(!s.hasPendingWork()&&!s.trackerNeedsRadio());
 s.tracker_awake_until=0;s.tracker_storage_fault=true;s.tracker_next_check=0;
 assert(!s.hasPendingWork()&&!s.trackerNeedsRadio());
 s.tracker_storage_fault=false;s.tracker_next_check=1900;
 // All standard radio/console/update owners veto RF sleep.
 for(bool* busy:{&s.board.usb,&s.board.ota,&s.board.test,&logging,&s.temp,&s.ota,
                  &s.dual,&s._cli.gpio,&s.outbound}){
  *busy=true;assert(s.trackerNeedsRadio());*busy=false;
 }
 s.manager.queued=1;assert(s.trackerNeedsRadio());s.manager.queued=0;
 s.set_radio_at=1;assert(s.trackerNeedsRadio());s.set_radio_at=0;
 s.revert_radio_at=1;assert(s.trackerNeedsRadio());s.revert_radio_at=0;
 s.saved_radio_apply_pending=true;assert(s.trackerNeedsRadio());
 s.saved_radio_apply_pending=false;
#endif
 for(bool* due:{&s.dual,&s.ota,&s._cli.gpio,&radio_driver.watchdog,
                 &radio_driver.calibration,&s.queue_due,&s.retry_due}){
  *due=true;assert(s.hasPendingWork()&&s.getPowerSaveSleepSeconds(30)==0);*due=false;
 }
 s.queue_delay_set=true;s.queue_delay=2500;assert(s.getPowerSaveSleepSeconds(30)==3);
#if MESH_ENABLE_SENSOR_TRACKER
 assert(s.trackerNeedsRadio());
#endif
 s.queue_delay_set=false;s.retry_delay_set=true;s.retry_delay=1500;
 assert(s.getPowerSaveSleepSeconds(30)==2);s.retry_delay_set=false;
 s.dirty_contacts_expiry=now_ms+1001;assert(s.getPowerSaveSleepSeconds(30)==2);
 s.dirty_contacts_expiry=0;s.last_read_time=45;
 assert(s.getPowerSaveSleepSeconds(30)==5);s.last_read_time=40;
 assert(s.hasPendingWork()&&s.getPowerSaveSleepSeconds(30)==0);
}
'''

TIMER = r'''
#include <Arduino.h>
#include <cassert>
using TimerHandle_t=void*;
bool allocate=true,queue_ok=true;
unsigned period_ms=0,begin_calls=0,queue_calls=0;
constexpr int pdPASS=1;
uint32_t pdMS_TO_TICKS(uint32_t value){return value/10;}
int xTimerChangePeriod(TimerHandle_t handle,uint32_t ticks,int wait){
 assert(handle&&wait==0);++queue_calls;period_ms=ticks*10;return queue_ok?pdPASS:0;
}
class SoftwareTimer {
 TimerHandle_t handle=nullptr;
public:
 TimerHandle_t getHandle(){return handle;}
 void begin(uint32_t ms,void(*callback)(TimerHandle_t),void* id,bool repeat){
  assert(ms==1000&&callback&&id==nullptr&&!repeat);++begin_calls;
  if(allocate)handle=this;
 }
};
static SoftwareTimer tracker_wake_timer;
static void trackerWakeCallback(TimerHandle_t){}
@ARM@
struct Mesh {bool tracker=true;uint32_t sleep_secs=30;
 bool isTrackerModeEnabled()const{return tracker;}
 uint32_t getPowerSaveSleepSeconds(uint32_t max)const{assert(max==30);return sleep_secs;}
 bool millisHasNowPassed(uint32_t deadline)const{return (int32_t)(millis()-deadline)>=0;}
} the_mesh;
struct Board {unsigned sleeps=0,last=0;void sleep(unsigned value){++sleeps;last=value;}} board;
static const unsigned long POWERSAVING_FIRST_SLEEP_SECS=120;
void sleepGate(bool can_power_save){
@GATE@
}
int main(){
#if defined(NRF52_PLATFORM) && MESH_ENABLE_SENSOR_TRACKER
 allocate=false;sleepGate(true);assert(board.sleeps==0&&delay_count==1&&queue_calls==0);
 allocate=true;queue_ok=false;sleepGate(true);
 assert(board.sleeps==0&&delay_count==2&&queue_calls==1);
 queue_ok=true;sleepGate(true);assert(board.sleeps==1&&board.last==0&&period_ms==30000);
 assert(!armTrackerSleepWake(0)&&!armTrackerSleepWake(31));
 unsigned calls=queue_calls;sleepGate(false);assert(queue_calls==calls);
 the_mesh.sleep_secs=0;sleepGate(true);assert(queue_calls==calls&&board.sleeps==1);
 the_mesh.sleep_secs=7;sleepGate(true);assert(period_ms==7000&&board.sleeps==2);
 the_mesh.tracker=false;queue_ok=false;sleepGate(true);assert(board.sleeps==3);
#elif defined(NRF52_PLATFORM)
 sleepGate(true);assert(board.sleeps==1&&board.last==0);
 the_mesh.sleep_secs=0;sleepGate(true);assert(board.sleeps==1);
#else
 now_ms=119999;sleepGate(true);assert(board.sleeps==0);
 now_ms=120000;sleepGate(true);assert(board.sleeps==1&&board.last==30);
 the_mesh.sleep_secs=0;sleepGate(true);assert(board.sleeps==1);
#endif
}
'''

RADIO = r'''
#include <Arduino.h>
#include <cassert>
#define RADIOLIB_ERR_NONE 0
#define STATE_IDLE 0
#define STATE_TX_WAIT 3
struct PhysicalLayer {
 int standby_result=0,sleep_result=0;unsigned standbys=0,sleeps=0;
 int standby(){++standbys;return standby_result;}
 int sleep(){++sleeps;return sleep_result;}
};
class RadioLibWrapper {
public:
 PhysicalLayer layer;PhysicalLayer* _radio=&layer;
 bool _cw_active=false,pending=false,_rx_ps_armed=true,_rx_hold_continuous=true;
 unsigned state=5;
 bool isPacketPendingOrReceiving(){return pending;}
 bool setTrackerSleep(bool sleeping);
};
@RADIO@
struct Ms {uint32_t getMillis(){return millis();}};
struct DispatcherRadio {unsigned begins=0;void begin(){++begins;}bool isInRecvMode(){return true;}};
struct Liveness {unsigned begins=0;uint32_t at=0;void begin(uint32_t now){++begins;at=now;}};
class Dispatcher {
public:
 bool radio_available=true,dispatcher_started=true,radio_nonrx_timer_armed=true;
 bool agc_reset_armed=true,prev_isrecv_mode=false,nonrx_soft_recovery_attempted=true;
 uint32_t radio_nonrx_start=0,next_floor_calib_time=0,armed_agc_reset_interval=5;
 uint32_t rx_watchdog_window_start=0;
 Ms ms;Ms* _ms=&ms;DispatcherRadio radio;DispatcherRadio* _radio=&radio;Liveness radio_liveness;
 void setRadioAvailable(bool available);
};
#define MESH_DEBUG_PRINTLN(...) ((void)0)
@DISPATCHER@
int main(){
 RadioLibWrapper r;r._cw_active=true;assert(!r.setTrackerSleep(true)&&r.layer.standbys==0);
 r._cw_active=false;r.state=STATE_TX_WAIT;
 assert(!r.setTrackerSleep(true)&&r.layer.standbys==0);r.state=5;r.pending=true;
 assert(!r.setTrackerSleep(true)&&r.layer.standbys==0);r.pending=false;
 r.layer.standby_result=1;assert(!r.setTrackerSleep(true)&&r.layer.sleeps==0);
 r.layer.standby_result=0;r.layer.sleep_result=1;assert(!r.setTrackerSleep(true));
 r.layer.sleep_result=0;assert(r.setTrackerSleep(true)&&r.state==STATE_IDLE);
 assert(!r._rx_ps_armed&&!r._rx_hold_continuous);
 unsigned sleeps=r.layer.sleeps;assert(r.setTrackerSleep(false)&&r.layer.sleeps==sleeps);
 Dispatcher d;now_ms=12345;d.setRadioAvailable(false);
 assert(!d.radio_available&&!d.radio_nonrx_timer_armed&&d.radio.begins==0);
 d.setRadioAvailable(true);assert(d.radio_available&&d.radio.begins==1&&d.prev_isrecv_mode);
 assert(d.radio_nonrx_start==12345&&d.next_floor_calib_time==12345);
 assert(!d.agc_reset_armed&&d.armed_agc_reset_interval==0);
#ifdef RADIO_LIVENESS_SOFT_ONLY
 assert(d.rx_watchdog_window_start==12345);
#else
 assert(d.radio_liveness.begins==1&&d.radio_liveness.at==12345&&!d.nonrx_soft_recovery_attempted);
#endif
 d.setRadioAvailable(true);assert(d.radio.begins==1);
 d.setRadioAvailable(false);d.dispatcher_started=false;d.setRadioAvailable(true);
 assert(d.radio.begins==1);
}
'''


class SensorTrackerSleepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, source, defines=()):
        with tempfile.TemporaryDirectory(prefix="meshcore-tracker-sleep-") as directory:
            path = Path(directory)
            (path / "Arduino.h").write_text(ARDUINO, encoding="ascii")
            cpp, binary = path / "test.cpp", path / "test"
            cpp.write_text(source, encoding="ascii")
            result = subprocess.run([
                self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-unused-variable",
                *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
                *[f"-D{define}" for define in defines], "-I", str(path),
                str(cpp), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_deadlines_and_radio_owners_feature_on_and_off(self):
        sensor = (ROOT / "examples/simple_sensor/SensorMesh.cpp").read_text()
        tracker = (ROOT / "examples/simple_sensor/Tracker.cpp").read_text()
        methods = "\n".join(extract_braced(sensor, name) for name in (
            "bool SensorMesh::isMillisTimerDue(", "uint32_t SensorMesh::limitSleepToMillisTimer(",
            "bool SensorMesh::hasPendingWork(", "uint32_t SensorMesh::getPowerSaveSleepSeconds("))
        methods += "\n#if MESH_ENABLE_SENSOR_TRACKER\n" + extract_braced(
            tracker, "bool SensorMesh::trackerNeedsRadio(") + "\n#endif\n"
        for enabled in (0, 1):
            with self.subTest(enabled=enabled):
                self.compile_run(DEADLINES.replace("@METHODS@", methods),
                                 (f"MESH_ENABLE_SENSOR_TRACKER={enabled}",))

    def test_actual_main_timer_allocation_queue_failure_and_platform_paths(self):
        main = (ROOT / "examples/simple_sensor/main.cpp").read_text()
        source = TIMER.replace("@ARM@", extract_braced(
            main, "static bool armTrackerSleepWake(")).replace(
            "@GATE@", extract_braced(main, "if (can_power_save)"))
        for defines in (("NRF52_PLATFORM=1", "MESH_ENABLE_SENSOR_TRACKER=1"),
                        ("NRF52_PLATFORM=1", "MESH_ENABLE_SENSOR_TRACKER=0"),
                        ("ESP32_PLATFORM=1", "MESH_ENABLE_SENSOR_TRACKER=1"),
                        ("RP2040_PLATFORM=1", "MESH_ENABLE_SENSOR_TRACKER=1")):
            with self.subTest(defines=defines):
                self.compile_run(source, defines)

    def test_actual_radio_warm_sleep_and_dispatcher_watchdog_reanchor(self):
        radio = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        source = RADIO.replace("@RADIO@", extract_braced(
            radio, "bool RadioLibWrapper::setTrackerSleep(")).replace(
            "@DISPATCHER@", extract_braced(dispatcher, "void Dispatcher::setRadioAvailable("))
        for defines in ((), ("RADIO_LIVENESS_SOFT_ONLY=1",)):
            with self.subTest(defines=defines):
                self.compile_run(source, defines)

    def test_feature_defaults_and_overrides(self):
        header = (ROOT / "examples/simple_sensor/SensorMesh.h").read_text()
        config = re.search(r"#ifndef MESH_ENABLE_SENSOR_TRACKER[\s\S]*?#endif\n#endif", header).group()
        source = config + "\nstatic_assert(MESH_ENABLE_SENSOR_TRACKER == EXPECTED);int main(){}\n"
        for defines in (("NRF52_PLATFORM=1", "ENV_INCLUDE_GPS=1", "EXPECTED=1"),
                        ("ESP32_PLATFORM=1", "ENV_INCLUDE_GPS=1", "EXPECTED=1"),
                        ("RP2040_PLATFORM=1", "ENV_INCLUDE_GPS=1", "EXPECTED=1"),
                        ("STM32_PLATFORM=1", "ENV_INCLUDE_GPS=1", "EXPECTED=0"),
                        ("NRF52_PLATFORM=1", "ENV_INCLUDE_GPS=0", "EXPECTED=0"),
                        ("STM32_PLATFORM=1", "ENV_INCLUDE_GPS=1", "MESH_ENABLE_SENSOR_TRACKER=1", "EXPECTED=1")):
            with self.subTest(defines=defines):
                self.compile_run(source, defines)


if __name__ == "__main__":
    unittest.main()
