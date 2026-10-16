"""Exercise the real haptic driver lifecycle with inert I2C/register mocks.

No PlatformIO, device access or board-current measurement. Negative controls
prove the checks reject idle-awake and premature-playback-stop regressions.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "src/helpers/ui/DRV2605Vibration.cpp"

ARDUINO = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <string>
#include <vector>
#define OUTPUT 1
#define LOW 0
#define HIGH 1
struct Event { std::string name; unsigned value; };
extern std::vector<Event> events;
extern uint32_t now_ms;
extern int enable_level;
inline uint32_t millis() { return now_ms; }
inline void pinMode(int pin,int mode) {
  assert(pin==37 && mode==OUTPUT);events.push_back({"enable-mode",1});
}
inline void digitalWrite(int pin,int value) {
  assert(pin==37);enable_level=value;
  events.push_back({"enable",static_cast<unsigned>(value)});
}
inline void delay(unsigned ms) {
  assert(ms==10);events.push_back({"delay",ms});
}
'''

WIRE = "#pragma once\nstruct TwoWire {};\nextern TwoWire Wire;\n"

ADAFRUIT = r'''
#pragma once
#include <Arduino.h>
#include <Wire.h>
#define DRV2605_MODE_INTTRIG 0x00
#define DRV2605_MODE_REALTIME 0x05
#define DRV2605_REG_MODE 0x01
#define DRV2605_REG_FEEDBACK 0x1a
#define DRV2605_REG_GO 0x0c
struct DriverState {
  bool found=true;
  unsigned begins=0,reads=0;
  uint8_t registers[256]={};
};
extern DriverState hardware;
class Adafruit_DRV2605 {
public:
  bool begin(TwoWire* wire) {
    assert(wire==&Wire);
#ifdef PIN_DRV_EN
    assert(enable_level==HIGH);
#endif
    ++hardware.begins;events.push_back({"begin",0});
    if(!hardware.found) return false;
    // The dependency takes the chip out of standby during initialization.
    writeRegister8(DRV2605_REG_MODE,0);
    return true;
  }
  void writeRegister8(uint8_t reg,uint8_t value) {
    hardware.registers[reg]=value;
    events.push_back({"write",static_cast<unsigned>((reg<<8)|value)});
  }
  uint8_t readRegister8(uint8_t reg) {
    assert(reg==DRV2605_REG_GO);++hardware.reads;
    events.push_back({"read",reg});return hardware.registers[reg];
  }
  void selectLibrary(uint8_t library) { writeRegister8(3,library); }
  void setMode(uint8_t mode) { writeRegister8(DRV2605_REG_MODE,mode); }
  void setWaveform(uint8_t slot,uint8_t effect) {
    assert(slot<8);writeRegister8(4+slot,effect);
  }
  void setRealtimeValue(uint8_t value) { writeRegister8(2,value); }
  void go() {
    assert(hardware.registers[DRV2605_REG_MODE]==DRV2605_MODE_INTTRIG);
    writeRegister8(DRV2605_REG_GO,1);
  }
  void stop() { writeRegister8(DRV2605_REG_GO,0); }
};
'''

HARNESS = r'''
#include <helpers/ui/DRV2605Vibration.h>
std::vector<Event> events;
uint32_t now_ms=100;
int enable_level=LOW;
TwoWire Wire;
DriverState hardware;
bool standby() { return hardware.registers[DRV2605_REG_MODE]==0x40; }
void idleLoop(DRV2605Vibration& vibration) {
  const auto count=events.size();
  const auto reads=hardware.reads;
  for(unsigned i=0;i<10;++i) { now_ms+=1000;vibration.loop(); }
  assert(events.size()==count && hardware.reads==reads);
}
void checkSettings() {
  assert(hardware.begins==1); // wake is not a reset/reinitialization
  assert(hardware.registers[3]==1 && hardware.registers[0x16]==0x55);
#ifdef DRV2605_USE_LRA
  assert(hardware.registers[DRV2605_REG_FEEDBACK]==0xb6);
#else
  assert(hardware.registers[DRV2605_REG_FEEDBACK]==0);
#endif
}
void checkEffect(DRV2605Vibration& vibration) {
  assert(vibration.isVibrating() && !standby());
  assert(hardware.registers[DRV2605_REG_MODE]==DRV2605_MODE_INTTRIG);
  assert(hardware.registers[DRV2605_REG_GO]==1);
  assert(hardware.registers[4]==DRV2605_EFFECT && hardware.registers[5]==0);
  assert(hardware.registers[6]==DRV2605_EFFECT && hardware.registers[7]==0);
  checkSettings();
}
int main() {
  DRV2605Vibration vibration;
  // Inert before initialization, including stop/off callbacks.
  vibration.loop();vibration.stop();vibration.pulse(false);
  vibration.pulse(true);vibration.trigger(true);
  assert(events.empty() && !vibration.isVibrating());
  vibration.begin();
  assert(standby() && !vibration.isVibrating());
#ifdef PIN_DRV_EN
  assert(enable_level==HIGH);
#endif
  hardware.registers[0x16]=0x55; // representative retained actuator setting
  checkSettings();idleLoop(vibration);
  vibration.trigger();checkEffect(vibration);
  const auto reads=hardware.reads;
  now_ms+=19;vibration.loop();assert(hardware.reads==reads);
  ++now_ms;vibration.loop();
  assert(hardware.reads==reads+1);checkEffect(vibration);
  // Still active after a long effect: do not guess a fixed playback duration.
  now_ms+=10000;vibration.loop();checkEffect(vibration);
  hardware.registers[DRV2605_REG_GO]=0; // hardware finishes the entire sequence
  now_ms+=20;vibration.loop();
  assert(standby() && !vibration.isVibrating());
  checkSettings();idleLoop(vibration);
  vibration.trigger(true);checkEffect(vibration);
  auto count=events.size();
  vibration.trigger();assert(events.size()==count); // cooldown suppresses wake
  vibration.quiet(true);
  vibration.trigger(true);assert(events.size()==count);
  vibration.stop();assert(standby() && !vibration.isVibrating());
  assert(hardware.registers[DRV2605_REG_GO]==0 && hardware.registers[2]==0);
  vibration.quiet(false);checkSettings();idleLoop(vibration);
  vibration.pulse(true);
  assert(vibration.isVibrating());
  assert(hardware.registers[DRV2605_REG_MODE]==DRV2605_MODE_REALTIME);
  assert(hardware.registers[2]==100 && hardware.registers[DRV2605_REG_GO]==0);
  // GO does not reflect RTP playback. Never let the effect poll stop a pulse.
  idleLoop(vibration);assert(vibration.isVibrating() && !standby());
  vibration.pulse(false);
  assert(standby() && !vibration.isVibrating() && hardware.registers[2]==0);
  idleLoop(vibration);checkSettings();
  // Switching between RTP and an effect must replace tracked state, not let an
  // old effect completion turn off an active pulse.
  vibration.trigger(true);checkEffect(vibration);
  vibration.pulse(true);hardware.registers[DRV2605_REG_GO]=0;
  idleLoop(vibration);assert(vibration.isVibrating() && !standby());
  vibration.trigger(true);checkEffect(vibration);
  hardware.registers[DRV2605_REG_GO]=0;now_ms+=20;vibration.loop();
  assert(standby() && !vibration.isVibrating());
  // Poll scheduling must survive the nRF millis() rollover.
  now_ms=UINT32_MAX-10;vibration.trigger(true);checkEffect(vibration);
  count=hardware.reads;
  now_ms+=19;vibration.loop();assert(hardware.reads==count);
  ++now_ms;vibration.loop();assert(hardware.reads==count+1);
  checkEffect(vibration);
  hardware.registers[DRV2605_REG_GO]=0;now_ms+=20;vibration.loop();
  assert(standby() && !vibration.isVibrating());
  checkSettings();idleLoop(vibration);
  // No rail toggling during successful standby/wake or register resets.
#ifdef PIN_DRV_EN
  unsigned enables=0;
  for(const auto& event:events) if(event.name=="enable") {
    assert(event.value==HIGH);++enables;
  }
  assert(enables==1 && enable_level==HIGH);
#endif
  // Reinitialization failure must discard active state and remain inert.
  vibration.pulse(true);hardware.found=false;vibration.begin();
  assert(!vibration.isVibrating());
#ifdef PIN_DRV_EN
  assert(enable_level==LOW);
#endif
  count=events.size();
  vibration.loop();vibration.trigger(true);vibration.stop();
  vibration.pulse(true);vibration.pulse(false);
  assert(events.size()==count && !vibration.isVibrating());
}
'''


class Drv2605PowerTests(unittest.TestCase):
    def run_driver(self, *, enable=True, lra=True, mutation=None, expected_success=True):
        source = DRIVER.read_text()
        if mutation == "no-initial-standby":
            source = source.replace("drv.setMode(Standby);", "// negative control", 1)
        elif mutation == "no-completion-standby":
            original = extract_braced(source, "void DRV2605Vibration::loop()")
            source = source.replace(original, "void DRV2605Vibration::loop() {}")
        elif mutation == "premature-stop":
            source = source.replace("(drv.readRegister8(DRV2605_REG_GO) & 1) == 0",
                                    "(drv.readRegister8(DRV2605_REG_GO) & 1) != 0")
        elif mutation == "no-pulse-off":
            source = source.replace("if (!on) {", "if (false && !on) {")
        elif mutation == "poll-idle":
            source = source.replace("if (!_ready || !_effect_active) return;",
                                    "if (!_ready) return;")
        elif mutation == "failed-begin-rail-on":
            source = source.replace("digitalWrite(PIN_DRV_EN, LOW);", "// negative control")
        # Compile the actual implementation and header, replacing dependencies
        # only. The include path still resolves the production driver header.
        source = source.replace('#include "DRV2605Vibration.h"',
                                '#include <helpers/ui/DRV2605Vibration.h>')
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++11 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-drv2605-power-") as folder:
            work = Path(folder)
            (work/"Arduino.h").write_text(ARDUINO, encoding="utf-8")
            (work/"Wire.h").write_text(WIRE, encoding="utf-8")
            (work/"Adafruit_DRV2605.h").write_text(ADAFRUIT, encoding="utf-8")
            cpp = work/"case.cpp"
            cpp.write_text(source+"\n"+HARNESS, encoding="utf-8")
            binary = work/("case.exe" if os.name=="nt" else "case")
            flags = ["-DHAS_DRV2605=1"]
            if enable:
                flags.append("-DPIN_DRV_EN=37")
            if lra:
                flags.append("-DDRV2605_USE_LRA=1")
            sanitize = [] if os.name=="nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *sanitize, *flags, "-I", str(work), "-I", str(ROOT/"src"),
                str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if expected_success:
                self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            else:
                self.assertNotEqual(run.returncode, 0, "negative control unexpectedly passed")

    def test_lra_effect_completion_rtp_retention_cooldown_and_failed_begin(self):
        self.run_driver()

    def test_no_optional_enable_pin_or_lra_mode(self):
        self.run_driver(enable=False, lra=False)

    def test_initial_idle_awake_negative_control(self):
        self.run_driver(mutation="no-initial-standby", expected_success=False)

    def test_effect_completion_awake_negative_control(self):
        self.run_driver(mutation="no-completion-standby", expected_success=False)

    def test_stop_before_go_finishes_negative_control(self):
        self.run_driver(mutation="premature-stop", expected_success=False)

    def test_pulse_off_awake_negative_control(self):
        self.run_driver(mutation="no-pulse-off", expected_success=False)

    def test_idle_i2c_polling_negative_control(self):
        self.run_driver(mutation="poll-idle", expected_success=False)

    def test_failed_begin_enable_leak_negative_control(self):
        self.run_driver(mutation="failed-begin-rail-on", expected_success=False)


if __name__ == "__main__":
    unittest.main()
