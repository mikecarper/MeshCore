"""Exercise real R8 rotation, atomic saved settings and role redraw hooks."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cpp_source import body
import test_heltec_r8_display_detection as detection
from test_display_power import FS_STUB
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]

DRIVER_TEST = r'''
#include "ST7789LCDDisplay.h"
#include <helpers/ui/ObserverDashboard.h>
void checkGeometry(ST7789LCDDisplay& d,unsigned degrees,bool flipped,bool initialized_panel) {
  const unsigned reg=(DISPLAY_ROTATION+degrees/90+(flipped?2:0))&3;
  assert(d.width()==(reg&1?320:240) && d.height()==(reg&1?240:320));
  const auto& layout=ObserverDashboard::activeLayout(d);
  assert(layout.right_x==(d.width()==240?232:310));
  assert(layout.status_y<d.height() && layout.right_x<d.width());
  if (initialized_panel) assert(last_rotation==reg);
}
int main(int argc,char** argv) {
  assert(argc==2); scenario=argv[1];
  RefCountedDigitalPin rail(40); rail.begin(); rail.claim();
  ST7789LCDDisplay d(&rail);
#ifndef MESHCORE_HELTEC_R8_RUNTIME_ROTATION
  assert(!d.supportsRotation());
  assert(!d.setRotationDegrees(90));
  return 0;
#else
  assert(d.supportsRotation());
  const auto before=events;
  assert(d.setRotationDegrees(90)); checkGeometry(d,90,false,false);
  assert(events==before && rotations==0 && init_calls==0);
  assert(!d.setRotationDegrees(45) && !d.setRotationDegrees(360));
  checkGeometry(d,90,false,false);
  if (scenario=="floating") {
    assert(!d.begin());
    const auto failed_events=events;
    for (unsigned angle:{0,90,180,270}) {
      assert(d.setRotationDegrees(angle)); d.setFlipped(true);
      checkGeometry(d,angle,true,false); d.setFlipped(false);
      checkGeometry(d,angle,false,false); d.turnOn(); assert(!d.begin());
    }
    assert(events==failed_events && rotations==0 && init_calls==0);
    assert(levels[PIN_TFT_RST]==HIGH);
    return 0;
  }
  assert(d.begin()); checkGeometry(d,90,false,true);
  const auto reset_count=resets, init_count=init_calls, spi_count=spi_begins;
  for (bool flipped:{false,true}) {
    d.setFlipped(flipped);
    for (unsigned angle:{0,90,180,270}) {
      assert(d.setRotationDegrees(angle)); checkGeometry(d,angle,flipped,true);
      const auto count=rotations;
      assert(d.setRotationDegrees(angle) && rotations==count);
      assert(resets==reset_count && init_calls==init_count && spi_begins==spi_count);
    }
  }
  d.turnOff(); const auto asleep_draws=draws, asleep_rotations=rotations;
  // Coalesce several saved changes without waking or touching SPI/reset.
  assert(d.setRotationDegrees(90)); d.setFlipped(false);
  assert(d.setRotationDegrees(180)); assert(d.setRotationDegrees(270));
  checkGeometry(d,270,false,false);
  assert(draws==asleep_draws && rotations==asleep_rotations && !d.isOn());
  d.turnOn(); checkGeometry(d,270,false,true);
  assert(rotations==asleep_rotations+1);
  assert(resets==reset_count && init_calls==init_count && spi_begins==spi_count);
  d.turnOff(); d.turnOn();
  assert(rotations==asleep_rotations+1 && resets==reset_count);
  assert(d.setRotationDegrees(0)); checkGeometry(d,0,false,true);
  d.turnOff(); rail.release(); assert(levels[40]==LOW);
#endif
}
'''

SETTINGS_TEST = r'''
#include <cassert>
#include <helpers/ui/DisplayRotationSettings.h>
using namespace mesh::ui;
bool command(const char* text,char* reply,bool supported=true) {
  return handleDisplayRotationCommand(text,reply,160,supported);
}
int main() {
  FakeFS fs; char reply[160];
  assert(!loadDisplayRotationSettings(&fs) && displayRotationDegrees()==0);
  assert(!command("get display.rotation.extra",reply));
  assert(!command("set display.mode on",reply));
  assert(command("get display.rotation",reply) && strstr(reply,"default"));
  for (const char* input:{"set display.rotation 45","set display.rotation -90",
      "set display.rotation 360","set display.rotation 90junk","set display.rotation",
      "set display.rotation 99999999999999999999999999999999","set display.rotation auto"}) {
    assert(command(input,reply) && strstr(reply,"Error:"));
    assert(displayRotationDegrees()==0 && fs.files.empty());
  }
  assert(command("set display.rotation 90",reply,false) && strstr(reply,"unsupported"));
  assert(fs.files.empty());
  for (unsigned angle:{90,180,270,0}) {
    char input[80]; snprintf(input,sizeof(input),"set display.rotation %u",angle);
    assert(command(input,reply) && strstr(reply,"OK"));
    assert(displayRotationDegrees()==angle && fs.files.size()==1);
    assert(fs.exists("/display_rotation") && fs.files["/display_rotation"].size()==6);
    assert(loadDisplayRotationSettings(&fs) && displayRotationDegrees()==angle);
  }
  assert(command("set display.rotation 90",reply));
  const auto original=fs.files["/display_rotation"];
  fs.fail_write=true;
  assert(command("set display.rotation 180",reply) && strstr(reply,"save failed"));
  assert(displayRotationDegrees()==90 && fs.files["/display_rotation"]==original);
  fs.fail_write=false; fs.fail_publish=true;
  assert(command("set display.rotation 180",reply) && strstr(reply,"save failed"));
  assert(displayRotationDegrees()==90 && fs.files["/display_rotation"]==original);
  fs.fail_publish=false;
  assert(loadDisplayRotationSettings(&fs) && displayRotationDegrees()==90);
  // Recover a last complete backup rather than accepting a damaged primary.
  fs.files["/display_rotation.bak"]=original;
  fs.files["/display_rotation"][5]^=1;
  assert(loadDisplayRotationSettings(&fs) && displayRotationDegrees()==90);
  assert(fs.files["/display_rotation"]==original && !fs.exists("/display_rotation.bak"));
  // A valid checksum with an invalid angle/version is still rejected.
  for (bool bad_version:{false,true}) {
    fs.files["/display_rotation"]=original;
    auto& data=fs.files["/display_rotation"];
    if (bad_version) data[2]=2; else data[3]=45;
    data[5]=0; for (unsigned i=0;i<5;++i) data[5]^=data[i];
    assert(!loadDisplayRotationSettings(&fs) && displayRotationDegrees()==0);
  }
  assert(!loadDisplayRotationSettings(nullptr));
  assert(command("set display.rotation 90",reply) && strstr(reply,"save failed"));
}
'''

ROLE_TEST = r'''
#include <cassert>
#include <cstdint>
#define DISPLAY_REDRAW_ON_CHANGE 1
#define DISPLAY_ACTIVITY_DASHBOARD 1
namespace mesh { namespace ui {
unsigned wanted=0;
uint16_t displayRotationDegrees() {return wanted;}
}}
uint32_t now=50;
uint32_t millis() {return now;}
struct Display {
 bool supported=true,accept=true;
 unsigned calls=0,angle=999;
 bool supportsRotation() {return supported;}
 bool setRotationDegrees(uint16_t value) {++calls; if(!accept)return false; angle=value; return true;}
};
class UITask {
public:
 Display* _display;
 uint16_t _rotation_seen=0xffff;
 bool _frame_valid=true,_rows_valid=true;
 uint32_t _next_refresh=1000,_last_refresh=0;
 void applyDisplayRotation();
};
@METHOD@
int main() {
 Display display; UITask task{&display};
 task.applyDisplayRotation(); assert(display.calls==1 && display.angle==0);
 @INVALIDATION@
 task.applyDisplayRotation(); assert(display.calls==1);
 mesh::ui::wanted=90; task._frame_valid=task._rows_valid=true; task._next_refresh=2000;
 task.applyDisplayRotation(); assert(display.calls==2 && display.angle==90);
 @INVALIDATION@
 display.accept=false; mesh::ui::wanted=180; task.applyDisplayRotation();
 assert(task._rotation_seen==90);
 display.accept=true; task.applyDisplayRotation(); assert(task._rotation_seen==180);
 display.supported=false; const auto calls=display.calls; mesh::ui::wanted=270;
 task.applyDisplayRotation(); assert(display.calls==calls);
 task._display=nullptr; task.applyDisplayRotation();
}
'''


class HeltecR8DisplayRotationTests(unittest.TestCase):
    def compile_run(self, directory, main, extra=()):
        cpp=directory/"main.cpp"; binary=directory/"test"
        cpp.write_text(main)
        result=subprocess.run([os.environ.get("CXX","g++"),"-std=c++17","-Wall","-Wextra","-Werror",
            "-Wno-unused-variable",*SANITIZER_FLAGS,"-I"+str(directory),"-I"+str(ROOT/"src"),
            str(cpp),*map(str,extra),"-o",str(binary)],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_all_board_defaults_quarter_turns_flip_sleep_and_absence(self):
        fixture=detection.HeltecR8DisplayDetectionTest()
        with tempfile.TemporaryDirectory(prefix="r8-rotation-") as temporary:
            for default in range(4):
                flags=["-DHELTEC_V4_R8_TFT","-DHELTEC_TFT_DISPLAY_AUTODETECT=1",
                    "-DHELTEC_TFT_RUNTIME_ROTATION=1",f"-DDISPLAY_ROTATION={default}"]
                with patch.object(detection,"MAIN",DRIVER_TEST):
                    binary=fixture.build(Path(temporary)/str(default),flags)
                self.assertIn(b"meshcore.capability.display.rotation.heltec_r8.v1",binary.read_bytes())
                for scenario in ("valid","floating"):
                    with self.subTest(default=default,scenario=scenario):fixture.run_case(binary,scenario)

    def test_rotation_is_scoped_to_enabled_r8_firmware(self):
        fixture=detection.HeltecR8DisplayDetectionTest()
        with tempfile.TemporaryDirectory(prefix="r8-rotation-guard-") as temporary:
            for index,flags in enumerate((["-DHELTEC_V4_R8_TFT"],
                    ["-DHELTEC_V4_R8_TFT","-DHELTEC_TFT_RUNTIME_ROTATION=0"],
                    ["-DHELTEC_LORA_V4_TFT","-DHELTEC_TFT_RUNTIME_ROTATION=1"],
                    ["-DLILYGO_TDECK","-DHELTEC_TFT_RUNTIME_ROTATION=1"])):
                with patch.object(detection,"MAIN",DRIVER_TEST):
                    binary=fixture.build(Path(temporary)/str(index),flags)
                fixture.run_case(binary,"valid")
                self.assertNotIn(b"meshcore.capability.display.rotation.heltec_r8.v1",binary.read_bytes())

    def test_saved_rotation_reboots_validation_and_atomic_failure_recovery(self):
        with tempfile.TemporaryDirectory(prefix="r8-rotation-settings-") as temporary:
            directory=Path(temporary); (directory/"helpers").mkdir()
            (directory/"helpers/IdentityStore.h").write_text(FS_STUB.replace("/display_prefs.tmp","/display_rotation.tmp"))
            self.compile_run(directory,SETTINGS_TEST,[ROOT/"src/helpers/ui/DisplayRotationSettings.cpp"])

    def test_every_infrastructure_role_observes_changes_and_invalidates_its_frame(self):
        with tempfile.TemporaryDirectory(prefix="r8-rotation-ui-") as temporary:
            for role in ("simple_repeater","simple_room_server","simple_sensor"):
                source=(ROOT/"examples"/role/"UITask.cpp").read_text()
                method=body(source,"void UITask::applyDisplayRotation()")
                invalidation=("assert(uint32_t(now-task._last_refresh)>=1000u);" if role=="simple_sensor"
                    else "assert(!task._frame_valid && !task._rows_valid && task._next_refresh==now);")
                main=ROLE_TEST.replace("@METHOD@",method).replace("@INVALIDATION@",invalidation)
                self.compile_run(Path(temporary),main)
                # A setting change while asleep must be observed before power
                # servicing can wake and repaint the display.
                loop=body(source,"void UITask::loop()")
                self.assertLess(loop.index("applyDisplayRotation();"),loop.index("servicePower("))


if __name__ == "__main__":
    unittest.main()
