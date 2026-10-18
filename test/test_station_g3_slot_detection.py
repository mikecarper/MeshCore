"""Run real G3 probe/boot selection with inert SPI; never contacts hardware."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/station_g3_esp32"


def harness(mutation=None):
    source = (VARIANT / "target.cpp").read_text()
    start = source.index("constexpr uint32_t kProbeTimeoutMs =")
    methods = source[start:source.index(";", start) + 1] + "\n"
    methods += "\n".join(body(source, signature) for signature in (
        "bool waitForProbeReady(", "bool probeRadioSlot(",
        "bool initializeSlot(", "bool radio_init()"))
    if mutation == "accept-empty":
        methods = methods.replace("memcmp(version, RADIOLIB_SX1262_CHIP_TYPE, 6) == 0", "true")
    if mutation == "reboot-loop":
        methods = methods.replace("station_g3::serviceBootRecovery(board, firstNotice);", "return false;")
    if mutation == "no-busy-settle":
        guard = "delayMicroseconds(1);"
        assert guard in methods, "production post-command BUSY guard missing"
        methods = methods.replace(guard, "", 1)
    if mutation == "old-busy-timeout":
        methods = methods.replace("kProbeTimeoutMs = 1000", "kProbeTimeoutMs = 100")
    if mutation == "old-attempt-limit":
        methods = methods.replace("while (!found)", "for (unsigned attempt=0; attempt<3 && !found; ++attempt)", 1)
    return r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <vector>
#include "StationG3RadioSlot.h"
// Negative controls intentionally fail. Do not let Windows' CRT abort dialog
// turn a useful assertion into a hanging GUI process / locked temp executable.
#undef assert
#define assert(condition) do { if(!(condition)) { \
  std::fprintf(stderr,"fixture assertion line %u: %s\n",unsigned(__LINE__),#condition); \
  std::exit(91); } } while(false)
#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define INPUT 2
#define INPUT_PULLDOWN 3
#define MSBFIRST 1
#define SPI_MODE0 0
#define MESH_USB_CONSOLE_COOPERATIVE 1
#define RADIOLIB_SX126X_CMD_SET_STANDBY 0x80
#define RADIOLIB_SX126X_STANDBY_RC 0
#define RADIOLIB_SX126X_CMD_READ_REGISTER 0x1d
#define RADIOLIB_SX126X_REG_VERSION_STRING 0x320
#define RADIOLIB_SX1262_CHIP_TYPE "SX1261"
static unsigned now_ms=0,present=0,working=0,stuck=0,reads=0,recoveries=0,rx=0,remembered=0;
static uint64_t now_us=0,busy_asserts_at[3]={},busy_clears_at[3]={};
static bool busy_scheduled[3]={};
static unsigned settle_guards=0,busy_high_samples=0;
static uint64_t reset_released_at[3]={},startup_busy_us=0;
static unsigned standby_count[3]={},refused_standby_count=0,usb_services=0;
static bool returned=false;
static int levels[64];
static std::vector<unsigned> initializations;
uint32_t millis() { return now_ms; }
void delay(unsigned ms) { now_us+=uint64_t(ms)*1000;now_ms=now_us/1000; }
void delayMicroseconds(unsigned us) { assert(us==1);++settle_guards;now_us+=us;now_ms=now_us/1000; }
void pinMode(int,int) {}
void digitalWrite(int pin,int level) {
  assert(pin>=0 && pin<64);levels[pin]=level;
  for(unsigned slot=1;slot<=2;++slot) {
    if(pin!=station_g3::pinsForSlot(slot).reset) continue;
    if(level==LOW) { busy_scheduled[slot]=false;standby_count[slot]=0; }
    else reset_released_at[slot]=now_us;
  }
}
int digitalRead(int pin) {
  for(unsigned slot=1;slot<=2;++slot) {
    if(pin!=station_g3::pinsForSlot(slot).busy) continue;
    if(stuck&(1<<(slot-1))) return HIGH;
    // Independent reset/command-readiness model. Some supported modules are
    // busy longer at startup, or ignore several initial standby commands.
    if((present&(1<<(slot-1))) && now_us<reset_released_at[slot]+startup_busy_us) return HIGH;
    // Immediately after NSS rises BUSY still reads LOW. It asserts only
    // after the guard interval, then remains HIGH during command handling.
    if(busy_scheduled[slot] && now_us>=busy_asserts_at[slot] && now_us<busy_clears_at[slot]) {
      ++busy_high_samples;return HIGH;
    }
    return LOW;
  }
  assert(false);return 0;
}
struct SPISettings { SPISettings(unsigned hz,int order,int mode) {assert(hz==2000000 && order==MSBFIRST && mode==SPI_MODE0);} };
struct SPI {
  unsigned slot=0,index=0,command=0;
  bool transaction=false;
  void end() { assert(!transaction);slot=0; }
  void begin(int sck,int miso,int mosi) {
    slot=sck==12?1:2;
    const auto& p=station_g3::pinsForSlot(slot);
    assert(sck==p.sck && miso==p.miso && mosi==p.mosi);
  }
  void beginTransaction(SPISettings) {
    assert(slot && !transaction);transaction=true;index=0;command=0;
    assert(levels[11]==HIGH && levels[43]==HIGH);
  }
  uint8_t transfer(uint8_t value) {
    assert(transaction && levels[station_g3::pinsForSlot(slot).nss]==LOW);
    if(index==0) {
      command=value;
      assert(command==0x80 || command==0x1d); // No SetTx or packet writes!
      // Seeing a transient LOW before delayed BUSY assertion is not proof
      // that the previous command has completed. Reject an early read even
      // when the caller just sampled that pre-assertion LOW.
      if(command==0x1d && busy_scheduled[slot])
        assert(now_us>=busy_clears_at[slot] && "ReadRegister before delayed BUSY completed");
    }
    const unsigned offset=index++;
    if(command==0x80) { assert(offset==0 || value==0);return 0x22; }
    if(offset==1) assert(value==3);
    if(offset==2) assert(value==0x20);
    if(offset>=3) assert(value==0);
    if(offset>=4) {
      ++reads;
      if(!(present&(1<<(slot-1)))) return 0xff;
      if(standby_count[slot]<=refused_standby_count) return 0xff;
      return offset-4<6?"SX1261"[offset-4]:0;
    }
    return 0x22;
  }
  void endTransaction() {
    assert(transaction && levels[station_g3::pinsForSlot(slot).nss]==HIGH);
    assert(index==(command==0x80?2:20));transaction=false;
    if(command==0x80 && (present&(1<<(slot-1)))) {
      ++standby_count[slot];
      busy_scheduled[slot]=true;
      busy_asserts_at[slot]=now_us+1;
      busy_clears_at[slot]=now_us+51;
    }
  }
} spi;
struct Module { unsigned csPin=0,irqPin=0,rstPin=0,gpioPin=0; } module;
struct Radio {
  Module* getMod() { return &module; }
  bool std_init(SPI* bus) {
    assert(bus==&spi && spi.slot==station_g3::slotState().candidate);
    const auto& p=station_g3::activePins();
    assert(module.csPin==p.nss && module.irqPin==p.irq && module.rstPin==p.reset && module.gpioPin==p.busy);
    initializations.push_back(spi.slot);levels[p.reset]=HIGH;
    return working&(1<<(spi.slot-1));
  }
} radio;
struct Fem { void setRxModeEnable() { ++rx;assert(station_g3::slotState().active); } };
struct StationG3Board { Fem loRaFEMControl; } board;
struct Clock { void begin() {} template<typename T> void begin(T&) {} } fallback_clock,rtc_clock;
int Wire=0;
const char meshcore_capability_station_g3_auto_slots[]="meshcore.capability.station_g3.auto_slots.v1";
struct Recovery {};
namespace station_g3 {
void rememberActiveSlot(uint8_t slot) { remembered=slot;slotState().last=slot; }
void serviceBootRecovery(StationG3Board&,bool first) { assert(first);++recoveries;throw Recovery(); }
}
namespace mesh { void serviceUsbLoggingPort() {++usb_services;} void serviceUsbTerminalPort() {++usb_services;} }
@METHODS@
void reset(unsigned mask,unsigned successful,unsigned mode,unsigned last,unsigned busy=0) {
  station_g3::slotState()=station_g3::SlotState();
  station_g3::slotState().bootMode=station_g3::slotState().savedMode=mode;
  station_g3::slotState().last=last;
  spi.end();initializations.clear();
  now_ms=reads=recoveries=rx=remembered=0;present=mask;working=successful;stuck=busy;
  now_us=settle_guards=busy_high_samples=0;
  startup_busy_us=refused_standby_count=usb_services=0;
  for(unsigned slot=1;slot<=2;++slot) {
    busy_scheduled[slot]=false;busy_asserts_at[slot]=busy_clears_at[slot]=0;
    reset_released_at[slot]=standby_count[slot]=0;
  }
  for(int& value:levels) value=HIGH;
  returned=false;
}
int main() {
  for(unsigned mask=0;mask<4;++mask) for(unsigned ok=0;ok<4;++ok)
  for(unsigned mode=0;mode<3;++mode) for(unsigned last=0;last<3;++last) {
    reset(mask,ok,mode,last);
    const auto before=station_g3::slotState();
    unsigned expected=before.choose(mask);
    if(expected && !(ok&(1<<(expected-1)))) {
      if(mode==0 && mask==3 && (ok&(1<<((expected==1?2:1)-1)))) expected=expected==1?2:1;
      else expected=0;
    }
    try { returned=radio_init(); } catch(const Recovery&) {}
    assert(station_g3::slotState().detected==mask);
    assert(station_g3::slotState().bootMode==mode && station_g3::slotState().savedMode==mode);
    assert(station_g3::slotState().active==expected);
    if(!expected) { assert(!returned && recoveries==1 && rx==0 && remembered==0); }
    else {
      assert(returned && !recoveries && rx==1 && remembered==expected);
      const unsigned oldreads=reads;
      assert(radio_init()); // runtime recovery stays on selected socket
      assert(reads==oldreads && initializations.back()==expected);
      assert(levels[station_g3::pinsForSlot(expected==1?2:1).reset]==LOW);
    }
    assert(now_ms<=2000); // One shared one-second probe budget per socket.
  }
  reset(3,3,0,0,1);assert(radio_init());
  assert(station_g3::slotState().detected==2 && station_g3::slotState().active==2);
  assert(now_ms<=2000);
  // A present device exercises both the 1-us post-NSS guard and the ensuing
  // real BUSY wait. Neither a no-op delay shim nor an early LOW is enough.
  reset(1,1,0,0);assert(probeRadioSlot(1));
  assert(settle_guards==1 && busy_high_samples==1 && reads==16 && now_ms>=12);
  assert(!spi.transaction && levels[station_g3::pinsForSlot(1).reset]==LOW);
  assert(now_ms<20); // No new delay for a fast, immediately responsive chip.

  for(unsigned slot=1;slot<=2;++slot) for(bool wrap:{false,true}) {
    for(unsigned scenario=0;scenario<5;++scenario) {
      const unsigned bit=1U<<(slot-1);
      reset(scenario==3?0:bit,bit,0,0,scenario==2?bit:0);
      if(wrap) { now_us=uint64_t(UINT32_MAX-100)*1000;now_ms=now_us/1000; }
      if(scenario==0) startup_busy_us=200000;
      if(scenario==1) refused_standby_count=5;
      // Mixed startup BUSY and command refusal must not each get a fresh
      // one-second budget: this chip stays unresponsive after BUSY clears.
      if(scenario==4) { startup_busy_us=800000;refused_standby_count=1000; }
      const uint32_t started=millis();
      const bool found=probeRadioSlot(slot);
      const uint32_t elapsed=uint32_t(millis()-started);
      if(scenario==0) {
        assert(found && "200 ms startup BUSY must be tolerated");
        assert(elapsed>=200 && elapsed<220 && usb_services>0);
      } else if(scenario==1) {
        assert(found && "five ignored startup commands must be retried");
        assert(reads==96 && elapsed>=50 && elapsed<100 && usb_services>0);
      } else {
        assert(!found && elapsed==1000 && usb_services>0);
        if(scenario==2) assert(reads==0); // Stuck BUSY never permits SPI reads.
      }
      assert(!spi.transaction && spi.slot==0);
      assert(levels[station_g3::pinsForSlot(slot).reset]==LOW);
      assert(levels[11]==HIGH && levels[43]==HIGH);
      assert(initializations.empty() && rx==0 && remembered==0);
    }
  }
  // Corrupt policy cannot shift outside its mask or silently become auto.
  station_g3::slotState().bootMode=255;
  assert(station_g3::slotState().choose(3)==0);
}
'''.replace("@METHODS@", methods)


class StationG3DetectionTests(unittest.TestCase):
    def run_case(self, mutation=None):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="g3-slot-detection-") as directory:
            cpp=Path(directory)/"test.cpp"
            binary=Path(directory)/("test.exe" if os.name=="nt" else "test")
            cpp.write_text(harness(mutation),encoding="utf-8")
            sanitizer=[] if os.name=="nt" else ["-fsanitize=address,undefined","-fno-pie","-no-pie"]
            build=subprocess.run([compiler,"-std=c++17","-Wall","-Wextra",*sanitizer,
                "-I",str(VARIANT),str(cpp),"-o",str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            if mutation:
                self.assertNotEqual(run.returncode,0,"negative control unexpectedly passed")
                if mutation=="no-busy-settle":
                    self.assertIn("ReadRegister before delayed BUSY completed",run.stderr)
                elif mutation=="old-busy-timeout":
                    self.assertIn("200 ms startup BUSY must be tolerated",run.stderr)
                elif mutation=="old-attempt-limit":
                    self.assertIn("five ignored startup commands must be retried",run.stderr)
            else:self.assertEqual(run.returncode,0,run.stdout+run.stderr)

    def test_real_probe_selection_matrix_recovery_and_busy_deadlines(self):
        self.run_case()

    def test_empty_bus_cannot_count_as_radio_negative_control(self):
        self.run_case("accept-empty")

    def test_no_radio_must_stay_in_usb_recovery_negative_control(self):
        self.run_case("reboot-loop")

    def test_delayed_busy_requires_post_command_guard_negative_control(self):
        self.run_case("no-busy-settle")

    def test_old_short_busy_budget_rejects_supported_delayed_startup(self):
        self.run_case("old-busy-timeout")

    def test_old_three_attempt_limit_rejects_initial_command_refusals(self):
        self.run_case("old-attempt-limit")


if __name__=="__main__":unittest.main()
