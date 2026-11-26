"""Execute RAM-only controls, cross-TU scanner handoff and UART recovery."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_esp32_usb_sleep import HARNESS, board_method, board_sleep_methods
import test_profile_idle_esp32 as profile_tests

ROOT = Path(__file__).resolve().parents[1]


def compile_run(sources, defines=()):
    with tempfile.TemporaryDirectory(prefix="mesh-esp32-power-controls-") as directory:
        folder = Path(directory)
        (folder / "Arduino.h").write_text("#pragma once\n#include <cstdint>\n#include <cmath>\n")
        (folder / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time();\n")
        files=[]
        for index, source in enumerate(sources):
            path=folder/f"source{index}.cpp"; path.write_text(source); files.append(str(path))
        command=[os.environ.get("CXX","c++"),"-std=c++17","-Wall","-Wextra",
                 "-fsanitize=address,undefined","-fno-sanitize-recover=all","-fno-pie","-no-pie",
                 "-I",str(folder),"-I",str(ROOT/"src"),*defines,*files,"-o",str(folder/"test")]
        built=subprocess.run(command,capture_output=True,text=True)
        if built.returncode: raise AssertionError(built.stderr)
        ran=subprocess.run([str(folder/"test")],capture_output=True,text=True)
        if ran.returncode: raise AssertionError(ran.stdout+ran.stderr)


class PowerTestControlsTest(unittest.TestCase):
    def test_bounds_local_authority_summary_and_reset(self):
        compile_run([r'''
#include <cassert>
#include <cstring>
#include <helpers/esp32/PowerTestControls.h>
using namespace mesh::esp32;
static char reply[160];
static bool command(const char* text,bool local=true) {
  memset(reply,0x5a,sizeof(reply));
  bool handled=handlePowerTestCommand(text,reply,157,local);
  assert(reply[157]==0x5a&&reply[159]==0x5a);
  if(handled) assert(memchr(reply,0,157));
  return handled;
}
int main(){
 assert(command("get powersaving.test"));
 assert(strstr(reply,"sleep=on slice_ms=10 guard_ms=4 min_ms=2 settle_ms=auto"));
 assert(strstr(reply,"RAM only"));
 assert(!command("get powersaving.other"));
 assert(command("set powersaving.test sleep off",false)&&strstr(reply,"local only"));
 assert(manualCpuSleepEnabled());
 assert(command("get powersaving.test",false)&&strstr(reply,"local only"));
 for(const char* invalid:{"slice_ms 1","slice_ms 21","guard_ms 21","guard_ms -1",
       "min_ms 0","min_ms 11","settle_ms 0","settle_ms 51","min_ms 2x",
       "slice_ms 99999999999999999","uart_quiet 4","uart_quiet 301","sleep maybe"}){
   char input[96];snprintf(input,sizeof(input),"set powersaving.test %s",invalid);
   assert(command(input)&&strstr(reply,"Error:"));
 }
 assert(command("set powersaving.test slice_ms 20"));assert(profileSleepSliceMillis()==20);
 assert(command("set powersaving.test guard_ms 0"));assert(profileSleepGuardMicros()==0);
 assert(command("set powersaving.test min_ms 1"));assert(profileSleepMinimumMicros()==1000);
 assert(command("set powersaving.test settle_ms 50"));assert(profileSleepSettleMillis(7)==50);
 assert(command("set powersaving.test sleep off"));assert(!manualCpuSleepEnabled());
 assert(command("set powersaving.test reset"));
 assert(manualCpuSleepEnabled()&&profileSleepSliceMillis()==10&&profileSleepGuardMicros()==4000);
 assert(profileSleepMinimumMicros()==2000&&profileSleepSettleMillis(7)==7);
 assert(command("set powersaving.test uart_quiet 5")&&strstr(reply,"unsupported"));
}
'''.replace("#include <cassert>","#include <cassert>\n#include <initializer_list>")])

    def test_cli_in_another_translation_unit_changes_real_scanner_and_deadline(self):
        setter=r'''
#include <helpers/esp32/PowerTestControls.h>
extern "C" bool change(const char* command){
 char reply[160]={};return mesh::esp32::handlePowerTestCommand(command,reply,sizeof(reply),true)
     && strncmp(reply,"Error:",6)!=0;
}
'''
        scanner=profile_tests.Esp32ProfileIdleTest.fixture()+r'''
#include <helpers/esp32/ProfileIdleSleep.h>
extern "C" bool change(const char*);
struct Mesh {
 RadioLibWrapper& radio;
 bool getDualRadioIdleDelay(uint32_t& out){return radio.getProfileIdleDelay(out);}
};
struct ProfileIdleBoard {unsigned calls=0;uint32_t deadline=0;void sleepUntilMicroseconds(uint32_t at){++calls;deadline=at;}};
int main(){
 RadioLibWrapper w;ready(w);Mesh m{w};ProfileIdleBoard b;
 auto start=w._esp32_profile_visit_started_us;auto visit=w._esp32_profile_idle_visit_us;
 elapsed_us=start+visit-14000;assert(idle(w,10));
 assert(change("set powersaving.test guard_ms 9"));assert(idle(w,5));
 assert(change("set powersaving.test slice_ms 3"));assert(idle(w,3));
 assert(mesh::esp32::tryProfileIdleSleep(m,b,uint32_t(elapsed_us),30));
 assert(b.calls==1&&b.deadline==uint32_t(elapsed_us+3000));
 assert(change("set powersaving.test min_ms 6"));assert(!idle(w,0));
 assert(!mesh::esp32::tryProfileIdleSleep(m,b,uint32_t(elapsed_us),30)&&b.calls==1);
 assert(change("set powersaving.test reset"));assert(idle(w,10));
 assert(change("set powersaving.test settle_ms 50"));
 elapsed_us=start+10000;assert(!idle(w,0));
 assert(change("set powersaving.test settle_ms auto"));assert(idle(w,10));
 assert(change("set powersaving.test sleep off"));
 assert(!mesh::esp32::tryProfileIdleSleep(m,b,uint32_t(elapsed_us),30)&&b.calls==1);
 assert(change("set powersaving.test reset"));
 assert(mesh::esp32::tryProfileIdleSleep(m,b,uint32_t(elapsed_us),30)&&b.calls==2);
}
'''
        compile_run([setter,scanner],["-DESP32_PLATFORM=1"])

    def quiet_harness(self):
        methods=board_sleep_methods()+"\n"+"\n".join(board_method(signature) for signature in (
            "bool isUsbDataConnected() override","bool isUsbHostConnected() override","bool isUsbSleepHeld()",
            "bool handleUartQuiet(","void servicePowerTestControls("))
        code=HARNESS.split("int main()",1)[0]
        code=code.replace("  void end() { driver_initialized = false; }",r'''
  unsigned begins=0,ends=0,flushes=0,fail_begins=0;
  void begin(unsigned baud){assert(baud==115200);++begins;driver_initialized=fail_begins==0;if(fail_begins)--fail_begins;}
  void flush(){++flushes;}
  void end(){++ends;driver_initialized=false;}
''')
        code=code.replace("static bool logging_enabled = false;","static bool logging_enabled = false;\nbool usbDataLocked(){return false;}\nstatic bool text_pending=false;\nbool hasPendingUsbTerminalOutput(){return text_pending;}")
        code=code.replace("  bool inhibit_sleep = false;","  bool inhibit_sleep = false;\n  bool ota=false;bool isOTAUpdateRunning(){return ota;}\n  uint32_t uart_quiet_start_at=0;uint16_t uart_quiet_seconds=0;")
        return "#include <cassert>\n"+code.replace("@METHODS@",methods)

    def test_actual_uart_ack_drain_bounded_quiet_and_sleep_deadline_restore(self):
        compile_run([self.quiet_harness()+r'''
int main(){
 ESP32Board board;char reply[160]={};using mesh::esp32::UartQuietAction;
 mesh::logging_enabled=true;
 assert(!board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 mesh::logging_enabled=false;board.ota=true;
 assert(!board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 board.ota=false;
 assert(board.handleUartQuiet(UartQuietAction::Schedule,10,reply,sizeof(reply)));
 assert(strstr(reply,"100ms"));mock_millis=1099;board.servicePowerTestControls();assert(Serial.ends==0);
 mock_millis=1100;board.servicePowerTestControls(false);assert(Serial.ends==0);
 mock_millis=1150;mesh::text_pending=true;board.servicePowerTestControls();assert(Serial.ends==0);
 mock_millis=1200;mesh::text_pending=false;board.servicePowerTestControls();
 assert(Serial.ends==1&&Serial.flushes==1&&!Serial&&board.uart_quiet_restore_at==11200);
 board.sleepMilliseconds(30000);assert(sleep_calls==1&&timer_us==10000000);
 mock_millis=11199;board.sleepUntilMicroseconds(micros()+10000);
 assert(sleep_calls==1); // recovery is due before the minimum useful sleep
 mock_millis=11200;board.servicePowerTestControls();
 assert(Serial&&Serial.begins==1&&!board.uart_quiet_restore_at);
 board.sleepMilliseconds(10);assert(sleep_calls==1); // normal UART guard restored
 // An acknowledgement which never drains leaves the console available.
 assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 mock_millis+=2000;board.servicePowerTestControls(false);
 assert(Serial&&!board.uart_quiet_start_at&&Serial.ends==1);
 // Reboot is not required to recover: local reset/off restores immediately.
 assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 mock_millis+=100;board.servicePowerTestControls();assert(!Serial);
 assert(board.handleUartQuiet(UartQuietAction::Cancel,0,reply,sizeof(reply)));assert(Serial);
 // Active logging cancels a quiet interval rather than losing its output.
 assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 mock_millis+=100;board.servicePowerTestControls();assert(!Serial);
 mesh::logging_enabled=true;board.servicePowerTestControls();assert(Serial);
 // Wrap-safe recovery, even with manual CPU sleep disabled.
 mesh::logging_enabled=false;mock_millis=UINT32_MAX-50;
 assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 mock_millis+=100;board.servicePowerTestControls();assert(!Serial);
 mesh::esp32::powerTestControls().sleep_enabled=false;
 mock_millis+=5000;board.servicePowerTestControls();assert(Serial);
}
'''],["-DESP32_PLATFORM=1","-DARDUINO_USB_CDC_ON_BOOT=0","-DMESH_USB_LOGGING_AVAILABLE=1"])

    def test_role_hook_failures_keep_session_and_bounded_recovery(self):
        compile_run([self.quiet_harness()+r'''
struct Session {
 bool parked=false,refuse_park=true,refuse_resume=false;
 unsigned parks=0,resumes=0;
};
int main(){
 ESP32Board board;Session session;char reply[160]={};
 using mesh::esp32::UartQuietAction;using mesh::esp32::UartQuietSessionAction;
 mesh::esp32::registerUartQuietSessionControl(&session,
   [](void* owner,UartQuietSessionAction action){
     auto& state=*static_cast<Session*>(owner);
     assert(Serial); // Park precedes end; Resume follows a ready begin.
     if(action==UartQuietSessionAction::Park){
       ++state.parks;assert(Serial.flushes>0);
       if(state.refuse_park)return false;
       state.parked=true;
     }else{
       ++state.resumes;
       if(state.refuse_resume)return false;
       state.parked=false;
     }
     return true;
   });
 assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 assert(board.handleUartQuiet(UartQuietAction::Cancel,0,reply,sizeof(reply)));
 assert(session.parks==0&&session.resumes==0&&Serial.ends==0);
 assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 mock_millis+=100;board.servicePowerTestControls();
 assert(session.parks==1&&!session.parked&&Serial&&Serial.ends==0);
 mock_millis+=1900;board.servicePowerTestControls();
 assert(!board.uart_quiet_start_at&&session.parks==1&&Serial.ends==0);
 session.refuse_park=false;
 assert(board.handleUartQuiet(UartQuietAction::Schedule,5,reply,sizeof(reply)));
 mock_millis+=100;board.servicePowerTestControls(false);assert(session.parks==1);
 board.servicePowerTestControls();assert(session.parked&&!Serial&&Serial.ends==1);
 Serial.fail_begins=1;
 assert(!board.handleUartQuiet(UartQuietAction::Cancel,0,reply,sizeof(reply)));
 assert(session.parked&&session.resumes==0&&!Serial&&board.uart_quiet_restore_at);
 session.refuse_resume=true;mock_millis+=1000;board.servicePowerTestControls();
 assert(session.parked&&Serial&&session.resumes==1&&board.uart_quiet_restore_at);
 session.refuse_resume=false;mock_millis+=1000;board.servicePowerTestControls();
 assert(!session.parked&&Serial&&session.resumes==2&&!board.uart_quiet_restore_at);
}
'''],["-DESP32_PLATFORM=1","-DARDUINO_USB_CDC_ON_BOOT=0","-DMESH_USB_LOGGING_AVAILABLE=1"])

    def test_native_usb_and_extra_uart_quiet_never_park_the_session(self):
        source = self.quiet_harness()+r'''
static unsigned session_actions=0;
int main(){
 ESP32Board board;char reply[160]={};
 mesh::esp32::registerUartQuietSessionControl(nullptr,
   [](void*,mesh::esp32::UartQuietSessionAction){++session_actions;return true;});
 assert(!board.handleUartQuiet(mesh::esp32::UartQuietAction::Schedule,5,reply,sizeof(reply)));
 assert(strstr(reply,"Error:")&&Serial.ends==0);
 board.servicePowerTestControls();assert(Serial.ends==0);
 mesh::esp32::registerUartQuietControl(&board,
   [](void* ptr,mesh::esp32::UartQuietAction action,uint16_t seconds,char* out,size_t size){
     return static_cast<ESP32Board*>(ptr)->handleUartQuiet(action,seconds,out,size);
   });
 mesh::esp32::powerTestControls().guard_ms=15;
 assert(mesh::esp32::handlePowerTestCommand("set powersaving.test reset",reply,sizeof(reply),true));
 assert(mesh::esp32::profileSleepGuardMicros()==4000&&strstr(reply,"uart=unsupported"));
 assert(session_actions==0);
}
'''
        for defines in (("ARDUINO_USB_CDC_ON_BOOT=1",), ("SERIAL_RX=1",), ("SERIAL_TX=1",)):
            with self.subTest(defines=defines):
                compile_run([source],["-DESP32_PLATFORM=1","-DMESH_USB_LOGGING_AVAILABLE=1",
                                      *["-D"+value for value in defines]])


if __name__ == "__main__": unittest.main(verbosity=2)
