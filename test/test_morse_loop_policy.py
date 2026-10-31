#!/usr/bin/env python3
"""Exercise production Morse redraw deferral and fresh voltage cutoff checks."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_morse_tx import compile_run
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
RADIO = """#pragma once
namespace mesh { class Radio { public:
  virtual bool isMorseTxActive() const { return false; }
  virtual bool isMorseRxActive() const { return false; }
  virtual bool isCarrierWaveActive() const { return false; }
}; }
"""

FIXTURE = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <initializer_list>
#include <helpers/MorseLoopPolicy.h>
static uint32_t now_ms=10000;
uint32_t millis() { return now_ms; }
bool millisReached(uint32_t now,uint32_t due) { return int32_t(now-due)>=0; }
static unsigned adc_reads=0,frames=0,shutdowns=0;
void delay(uint32_t) {}
struct TestRadio : mesh::Radio {
  bool tx=false,rx=false,cw=false;
  bool isMorseTxActive() const override { return tx; }
  bool isMorseRxActive() const override { return rx; }
  bool isCarrierWaveActive() const override { return cw; }
} radio_driver;
struct TestBoard { bool external=false; bool isExternalPowered() const { return external; } } board;
struct TestMesh { mesh::Radio* getProfileRadio() { return &radio_driver; } } the_mesh;
namespace UIColor { constexpr int popup_bkg=0,popup_txt=1,warning_txt=2; }
struct Display {
  bool on=true;
  bool isOn() const { return on; }
  bool isEink() const { return false; }
  int width() const { return 128; }
  int height() const { return 64; }
  void startFrame() { ++frames; }
  void endFrame() {}
  void setTextSize(int) {}
  void setColor(int) {}
  void fillRect(int,int,int,int) {}
  void drawRect(int,int,int,int) {}
  void drawTextCentered(int,int,const char*) {}
};
struct Screen { int render(Display&) { ++adc_reads; return 1000; } };
struct StatusBar {
  void update(Display&,const char*,uint16_t,bool,bool,bool) {}
  bool needsRedraw() const { return true; }
  void render(Display&) {}
};
constexpr uint32_t BOOT_SCREEN_MILLIS=3000;
constexpr uint16_t AUTO_SHUTDOWN_MILLIVOLTS=3400;
struct UI {
  Display display; Display* _display=&display;
  Screen screen; Screen* curr=&screen;
  StatusBar _statusBar;
  struct Prefs { const char* node_name="test"; } prefs;
  Prefs* _node_prefs=&prefs;
  uint32_t _next_refresh=0,_alert_expiry=0,ui_started_at=0,next_batt_chck=0;
  uint32_t _last_refresh=0;
  uint16_t _cached_batt_mv=4000,voltage=3350;
  bool _need_refresh=true;
  bool _startup_complete=false;
  char _alert[4]={},_origin[4]={},_msg[4]={};
  bool isButtonGesturePending() { return false; }
  bool isPairingScreenActive() { return false; }
  bool isBuzzerQuiet() { return true; }
  bool getGPSState() { return false; }
  bool isBluetoothEnabled() { return false; }
  void renderPairingBanner() {}
  void serviceRadioProfileDisplayPage() {}
  void applyDisplayFlip() {}
  void renderCurrScreen() { ++adc_reads; }
  uint16_t getBattMilliVolts() { ++adc_reads; return voltage; }
  void shutdown() { ++shutdowns; }
  void redraw() { const bool morse_timing=mesh::morseTimingActive(&radio_driver); (void)morse_timing; @REDRAW@ }
  void cutoff() { @CUTOFF@ }
};
'''


class MorseLoopPolicyTests(unittest.TestCase):
    def test_actual_ui_radio_declarations_cover_each_production_expression(self):
        # Compile only declarations genuinely supplied by the production UI
        # source or its included role/target header. No synthetic global mesh
        # definitions may conceal an undeclared instance (Sensor uses an
        # accessor rather than Companion/Room/Repeater's global the_mesh).
        specs = (
            ("examples/companion_radio/ui-new/UITask.cpp", "examples/companion_radio/MyMesh.h", "MyMesh"),
            ("examples/companion_radio/ui-tiny/UITask.cpp", "examples/companion_radio/MyMesh.h", "MyMesh"),
            ("examples/companion_radio/ui-orig/UITask.cpp", "examples/companion_radio/MyMesh.h", "MyMesh"),
            ("examples/simple_repeater/UITask.cpp", "examples/simple_repeater/MyMesh.h", "MyMesh"),
            ("examples/simple_room_server/UITask.cpp", "examples/simple_room_server/MyMesh.h", "MyMesh"),
            ("examples/simple_sensor/UITask.cpp", "examples/simple_sensor/SensorMesh.h", "SensorMesh"),
        )
        target = (ROOT / "variants/heltec_v4/target.h").read_text(encoding="ascii")
        target_radio = re.search(r"^extern WRAPPER_CLASS radio_driver;", target, re.M).group(0)
        for source_path, header_path, role in specs:
            with self.subTest(ui=source_path):
                source = (ROOT / source_path).read_text(encoding="ascii")
                header = (ROOT / header_path).read_text(encoding="ascii")
                loop = method(source, "void UITask::loop()")
                expression = re.search(r"const bool morse_timing = mesh::morseTimingActive\([^;]+;", loop).group(0)
                self.assertRegex(source, r'#include ["<](?:\.\./)?(?:MyMesh|SensorMesh)\.h[">]')
                declarations = re.findall(r"^extern " + role + r" the_mesh;|^" + role + r"& activeSensorMesh\(\);",
                                          source + "\n" + header, re.M)
                if re.search(r'#include ["<]target\.h[">]', source + "\n" + header):
                    declarations.append(target_radio)
                getter = ""
                if "getProfileRadio(" in expression:
                    getter = method(header, "mesh::Radio* getProfileRadio()").replace(" override", "")
                code = """#include <helpers/MorseLoopPolicy.h>
class TestRadio : public mesh::Radio {};
#define WRAPPER_CLASS TestRadio
class @ROLE@ { public: mesh::Radio* _radio=nullptr; @GETTER@ };
@DECLARATIONS@
bool checkRadioReference() { @EXPRESSION@ return morse_timing; }
""".replace("@ROLE@", role).replace("@GETTER@", getter).replace(
                    "@DECLARATIONS@", "\n".join(dict.fromkeys(declarations))).replace("@EXPRESSION@", expression)
                with tempfile.TemporaryDirectory(prefix="morse-ui-declarations-") as directory:
                    temp = Path(directory)
                    (temp / "Dispatcher.h").write_text(RADIO, encoding="ascii")
                    (temp / "test.cpp").write_text(code, encoding="ascii")
                    result = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                                             "-I", str(temp), "-I", str(ROOT / "src"), str(temp / "test.cpp")],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if role == "SensorMesh":
                        # This exact old global-instance expression must fail,
                        # not silently succeed because a host mock invented it.
                        negative = code.replace("activeSensorMesh().getProfileRadio()", "the_mesh.getProfileRadio()")
                        (temp / "test.cpp").write_text(negative, encoding="ascii")
                        result = subprocess.run(["g++", "-std=c++17", "-fsyntax-only", "-I", str(temp),
                                                 "-I", str(ROOT / "src"), str(temp / "test.cpp")],
                                                capture_output=True, text=True)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("the_mesh", result.stderr)

    def test_actual_common_loop_keeps_gpio_expiry_live_while_background_is_deferred(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        loop = method(source, "void CommonCLI::loop()")
        code = FIXTURE.replace("@REDRAW@", "").replace("@CUTOFF@", "") + r'''
#define MESH_ENABLE_MORSE_RX 0
#define USER_GPIO_CONTROL 1
#include <helpers/RadioPresetConfig.h>
struct UserGpio {
  struct Completion { uint8_t pin=4,state=0; uint32_t request_id=99; };
  unsigned loops=0; bool pending=true;
  void loop() { ++loops; }
  bool takeCompletion(Completion& out) {
    if(!pending) return false;
    pending=false; out={}; return true;
  }
};
struct Callbacks {
  unsigned completed=0;
  mesh::Radio* getProfileRadio() { return &radio_driver; }
  void onUserGpioTimerCompleted(uint8_t pin,uint8_t state,uint32_t id) {
    assert(pin==4 && state==0 && id==99); ++completed;
  }
};
struct CommonCLI {
  Callbacks callbacks; Callbacks* _callbacks=&callbacks;
  UserGpio _user_gpio;
  struct Profiles { unsigned loops=0; void loop() { ++loops; } } _radio_profiles;
  struct Presets { unsigned loops=0; void loop(unsigned long) { ++loops; } } _radio_presets;
  unsigned management=0, tx_services=0, rx_services=0;
  void loopBatteryForwarding(bool = false) {}
  void recoverMediaMeshRegions() {}
  void loopManagement() { ++management; }
  void servicePendingTxPower() { ++tx_services; }
  void servicePendingRxSettings() { ++rx_services; }
  void loop();
};
@LOOP@
int main() {
  for(unsigned mode=0;mode<4;++mode) {
    radio_driver.tx=mode==1; radio_driver.rx=mode==2; radio_driver.cw=mode==3;
    CommonCLI cli; cli.loop();
    assert(cli._user_gpio.loops==1 && cli.callbacks.completed==1);
    const unsigned ordinary=mode==1 || mode==2 ? 0:1;
    assert(cli._radio_profiles.loops==ordinary && cli.management==ordinary);
    assert(cli._radio_presets.loops==ordinary);
    assert(cli.tx_services==ordinary);
    assert(cli.rx_services==ordinary);
    radio_driver.tx=radio_driver.rx=false;
    cli.loop();
    assert(cli._radio_profiles.loops==ordinary+1 && cli.management==ordinary+1);
    assert(cli._radio_presets.loops==ordinary+1);
    assert(cli.tx_services==ordinary+1);
    assert(cli.rx_services==ordinary+1);
    assert(cli._user_gpio.loops==2 && cli.callbacks.completed==1);
  }
}
'''
        compile_run(self, code.replace("@LOOP@", loop), headers={"Dispatcher.h": RADIO})

    def test_only_morse_ownership_defers_work_and_recovery_stays_deferred(self):
        code = FIXTURE.replace("@REDRAW@", "").replace("@CUTOFF@", "") + r'''
int main() {
  assert(!mesh::morseTimingActive(nullptr));
  assert(!mesh::morseTimingActive(&radio_driver));
  radio_driver.cw=true; assert(!mesh::morseTimingActive(&radio_driver));
  radio_driver.tx=true; assert(mesh::morseTimingActive(&radio_driver));
  radio_driver.tx=false; radio_driver.rx=true; assert(mesh::morseTimingActive(&radio_driver));
  radio_driver.rx=false; assert(!mesh::morseTimingActive(&radio_driver));
}
'''
        compile_run(self, code, headers={"Dispatcher.h": RADIO})

    def test_actual_all_six_ui_redraw_blocks_defer_without_consuming_deadlines(self):
        specs = (
            ("examples/companion_radio/ui-new/UITask.cpp",
             "if (!morse_timing && static_cast<int32_t>(millis() - _next_refresh) >= 0"),
            ("examples/companion_radio/ui-tiny/UITask.cpp",
             "if (!morse_timing && _display != NULL && _display->isOn())"),
            ("examples/companion_radio/ui-orig/UITask.cpp",
             "if (!morse_timing && _display != NULL && _display->isOn())"),
            ("examples/simple_repeater/UITask.cpp", "if (!morse_timing && _display->isOn())"),
            ("examples/simple_room_server/UITask.cpp", "if (!morse_timing && _display->isOn())"),
            ("examples/simple_sensor/UITask.cpp", "if (!morse_timing && _display->isOn())"),
        )
        for path, signature in specs:
            with self.subTest(ui=path):
                loop = method((ROOT / path).read_text(encoding="ascii"), "void UITask::loop()")
                self.assertIn("const bool morse_timing = mesh::morseTimingActive(", loop)
                redraw = method(loop, signature)
                self.assertIn("_display->startFrame()", redraw)
                code = FIXTURE.replace("@REDRAW@", redraw).replace("@CUTOFF@", "") + r'''
int main() {
  constexpr bool sensor_timestamps=@SENSOR_TIMESTAMPS@;
  for(bool external : {false,true}) {
    board.external=external;
    for(unsigned mode=0;mode<4;++mode) {
      UI ui; adc_reads=frames=0;
      const uint32_t last_refresh_before=ui._last_refresh;
      radio_driver.tx=mode==1; radio_driver.rx=mode==2; radio_driver.cw=mode==3;
      ui.redraw();
      if(mode==1 || mode==2) {
        if(sensor_timestamps)
          assert(frames==0 && adc_reads==0 && ui._last_refresh==last_refresh_before);
        else assert(frames==0 && adc_reads==0 && ui._next_refresh==0);
        radio_driver.tx=radio_driver.rx=false;
        ui.redraw(); assert(frames==1 && adc_reads==1);
      } else assert(frames==1 && adc_reads==1);
      if(sensor_timestamps) assert(ui._last_refresh==now_ms);
      else assert(ui._next_refresh>now_ms);
    }
  }
}
'''
                code = code.replace("@SENSOR_TIMESTAMPS@",
                                    "true" if path == "examples/simple_sensor/UITask.cpp" else "false")
                compile_run(self, code, flags=("-Wno-unused-variable",), headers={"Dispatcher.h": RADIO})

    def test_actual_full_and_tiny_ui_cutoff_reads_fresh_voltage_even_during_morse(self):
        for variant in ("ui-new", "ui-tiny"):
            with self.subTest(ui=variant):
                source = (ROOT / f"examples/companion_radio/{variant}/UITask.cpp").read_text(encoding="ascii")
                loop = method(source, "void UITask::loop()")
                cutoff = method(loop, "if (static_cast<int32_t>(millis() - next_batt_chck) >= 0)")
                self.assertNotIn("morse_timing", cutoff)
                code = FIXTURE.replace("@REDRAW@", "").replace("@CUTOFF@", cutoff) + r'''
int main() {
  for(bool external : {false,true}) for(unsigned mode=0;mode<4;++mode) {
    board.external=external; now_ms=10000;
    radio_driver.tx=mode==1; radio_driver.rx=mode==2; radio_driver.cw=mode==3;
    UI ui; ui._cached_batt_mv=4000; ui.voltage=3350;
    adc_reads=shutdowns=0;
    ui.cutoff();
    assert(adc_reads==1 && shutdowns==unsigned(!external));
    assert(ui.next_batt_chck==18000);
    ui.cutoff(); assert(adc_reads==1);
    now_ms=18001; ui.voltage=3500;
    ui.cutoff(); assert(adc_reads==2 && shutdowns==unsigned(!external));
  }
}
'''
                compile_run(self, code, headers={"Dispatcher.h": RADIO})


if __name__ == "__main__":
    unittest.main()
