"""Execute generic G3 radio-pin consumers with both boot-selected slots.

The slot selector itself has separate target tests. This fixture deliberately
keeps historical slot-1 build macros while selecting slot 2 at runtime, so a
later SPI reinit or shutdown cannot silently restore the build-time wiring.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body
import test_esp32_gpio_hold as gpio_compiler

ROOT = Path(__file__).resolve().parents[1]
SLOT_API = r'''
#pragma once
#include <cstdint>
namespace station_g3 {
struct RadioPins { uint8_t nss,irq,reset,busy,sck,miso,mosi,lna; };
inline RadioPins slots[]={{11,48,21,47,12,14,13,10},{43,2,44,1,39,41,40,42}};
inline unsigned selected=0;
inline const RadioPins& activePins(){return slots[selected];}
inline bool isRadioPin(uint8_t pin){
  for(const auto& p:slots)
    if(pin==p.nss || pin==p.irq || pin==p.reset || pin==p.busy
       || pin==p.sck || pin==p.miso || pin==p.mosi || pin==p.lna) return true;
  return false;
}
}
'''
PINS = ("P_LORA_NSS=11", "P_LORA_DIO_1=48", "P_LORA_RESET=21",
        "P_LORA_BUSY=47", "P_LORA_SCLK=12", "P_LORA_MISO=14",
        "P_LORA_MOSI=13", "P_PRIMARY_LNA_EN=10", "P_PA1_EN=9")


class StationG3RadioPinsTests(unittest.TestCase):
    def compile_run(self, source, *, station=True, fail=False, files=None,
                    definitions=()):
        with tempfile.TemporaryDirectory(prefix="g3-radio-pins-") as folder:
            work = Path(folder)
            for name, content in (files or {}).items():
                destination = work / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content)
            # The header is deliberately absent on other-board builds, so
            # an accidentally unconditional target include fails compilation.
            if station:
                (work / "StationG3RadioSlot.h").write_text(SLOT_API)
            cpp, binary = work / "test.cpp", work / "test.exe"
            cpp.write_text(source)
            flags = (*PINS, *definitions, *(("STATION_G3_ESP32=1",) if station else ()))
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-I", str(work), "-I", str(ROOT / "src"),
                *("-D" + flag for flag in flags), str(cpp), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            if fail:
                self.assertNotEqual(ran.returncode, 0, "static-pin negative control escaped")
            else:
                self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_sx1262_init_and_recovery_use_selected_spi_without_static_overwrite(self):
        source = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
        extracted = body(source, "bool std_init(SPIClass* spi = NULL)")
        init = "bool std_init(SPIClass* spi = nullptr) " + extracted[extracted.index("{"):]
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#if defined(STATION_G3_ESP32)
#include <StationG3RadioSlot.h>
#endif
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define LORA_FREQ 909.5
#define LORA_BW 500
#define LORA_SF 5
#define LORA_TX_POWER 2
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_SX126X_SYNC_WORD_PRIVATE 0x12
struct SPIClass {
  unsigned calls=0; int sck=-1,miso=-1,mosi=-1;
  void begin(int c,int i,int o){++calls;sck=c;miso=i;mosi=o;}
};
struct Port { void print(const char*){} void println(int){} };
namespace mesh { inline Port& usbLoggingPort(){static Port port;return port;} }
struct Radio {
  uint32_t tcxoDelay=1600,_rxDutyCycleTransitionUs=0;
  int begin(float,float,int,uint8_t,int,int,int,float,bool){return 0;}
  void setCRC(int){}
  @INIT@
};
int main(){Radio radio;SPIClass spi;
#if defined(STATION_G3_ESP32)
 for(unsigned slot=0;slot<2;++slot){
  station_g3::selected=slot;
  const auto& pins=station_g3::activePins();
  // Initial setup and each watchdog/recovery init must use the same slot.
  for(int attempt=0;attempt<3;++attempt){
   const unsigned before=spi.calls;
   assert(radio.std_init(&spi));assert(spi.calls==before+1);
   assert(spi.sck==pins.sck && spi.miso==pins.miso && spi.mosi==pins.mosi);
  }
 }
#else
 assert(radio.std_init(&spi));
 assert(spi.calls==1 && spi.sck==12 && spi.miso==14 && spi.mosi==13);
#endif
 assert(radio._rxDutyCycleTransitionUs==2600);
 const unsigned before=spi.calls;
 assert(radio.std_init(nullptr) && spi.calls==before);
}
'''.replace("@INIT@", init)
        self.compile_run(harness)
        self.compile_run(harness, station=False)
        self.assertIn("spi->begin(pins.sck, pins.miso, pins.mosi)", harness)
        self.compile_run(harness.replace(
            "spi->begin(pins.sck, pins.miso, pins.mosi)",
            "(void)pins; spi->begin(P_LORA_SCLK, P_LORA_MISO, P_LORA_MOSI)"), fail=True)

    def test_base_boot_and_terminal_shutdown_keep_selected_nss(self):
        header = (ROOT / "src/helpers/ESP32Board.h").read_text()
        boot = body(header, "void begin()")
        boot = boot[boot.index("{") + 1:boot.index("// Arduino's early init hook")]
        shutdown = body((ROOT / "src/helpers/ESP32Board.cpp").read_text(),
                        "void ESP32Board::shutdownPeripherals()")
        code = r'''
#include <helpers/ESP32GpioHold.h>
#include <StationG3RadioSlot.h>
#include <cstddef>
#define HIGH 1
static bool powered_off=false;
void delay(unsigned value){assert(value==100);}
struct Radio {void powerOff(){powered_off=true;}} radio_driver;
struct GPS {void stop(){}};
struct Sensors {GPS* getLocationProvider(){return nullptr;} void shutdownGpsTransportForDeepSleep(){}} sensors;
struct Port {void flush(){}} Serial;
class ESP32Board {public:void begin(){@BOOT@} void shutdownPeripherals();};
@SHUTDOWN@
int main(){
 for(unsigned slot=0;slot<2;++slot){
  station_g3::selected=slot;
  const int nss=station_g3::activePins().nss;
  ESP32Board board;
  // A prior deep-sleep hold must be released with OUTPUT/HIGH restored.
  mesh::esp32::holdOutputForDeepSleep(nss,HIGH);
  pads[nss].latch=0;pads[nss].output=false;
  board.begin();assert(!pads[nss].hold && physical(nss)==HIGH);
  powered_off=false;board.shutdownPeripherals();
  assert(powered_off && pads[nss].hold && physical(nss)==HIGH);
  assert(!deep_hold); // Only final terminal sleep may arm global retention.
 }
}
'''.replace("@BOOT@", boot).replace("@SHUTDOWN@", shutdown)
        options = dict(s3=1, defines=(*PINS, "STATION_G3_ESP32=1"),
                       files={"StationG3RadioSlot.h": SLOT_API})
        gpio_compiler.GpioHoldTests.compile_run(self, code, **options)
        # This negative control models either old NSS branch surviving an
        # otherwise successful runtime slot-2 selection.
        gpio_compiler.GpioHoldTests.compile_run(self, code.replace(
            "station_g3::activePins().nss", "P_LORA_NSS", 2),
            helper_mutation=lambda text: text, **options)

    def test_runtime_reset_capability_and_other_board_constexpr_are_preserved(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text()
        start = source.index("#if defined(STATION_G3_ESP32)\n  static bool hasDirectRadioResetPin()")
        block = source[start:source.index("  bool prepareRadioHardReset()", start)]
        harness = r'''
#include <cassert>
#include <cstdint>
#define RADIOLIB_NC (-1)
#if defined(STATION_G3_ESP32)
#include <StationG3RadioSlot.h>
#endif
struct Wrapper {
@BLOCK@
};
int main(){
#if defined(STATION_G3_ESP32)
 for(unsigned slot=0;slot<2;++slot){
  station_g3::selected=slot;
  assert(Wrapper::hasDirectRadioResetPin());
  const auto saved=station_g3::slots[slot].reset;
  station_g3::slots[slot].reset=255;
  assert(!Wrapper::hasDirectRadioResetPin());
  station_g3::slots[slot].reset=saved;
 }
#else
 static_assert(Wrapper::hasDirectRadioResetPin(),"other boards keep compile-time capability");
#endif
}
'''.replace("@BLOCK@", block)
        self.compile_run(harness)
        self.compile_run(harness, station=False)

    def test_user_gpio_reserves_both_slots_independent_of_selected_slot(self):
        harness = r'''
#include <helpers/UserGpioPinPolicy.h>
#include <cassert>
#include <cstdint>
#if defined(STATION_G3_ESP32)
#include <StationG3RadioSlot.h>
#endif
int main(){
#if defined(STATION_G3_ESP32)
 const uint8_t pins[]={11,48,21,47,12,14,13,10,43,2,44,1,39,41,40,42};
 for(unsigned slot=0;slot<2;++slot){
  station_g3::selected=slot;
  for(uint8_t pin:pins) assert(UserGpioPinPolicy::isFirmwareReserved(pin));
  for(uint8_t pin:{uint8_t(22),uint8_t(23),uint8_t(24),uint8_t(25)})
   assert(!UserGpioPinPolicy::isFirmwareReserved(pin));
 }
#else
 assert(UserGpioPinPolicy::isFirmwareReserved(11));
 assert(!UserGpioPinPolicy::isFirmwareReserved(43));
#endif
 assert(UserGpioPinPolicy::isFirmwareReserved(9)); // Shared PA remains reserved.
}
'''
        harness = "#include <initializer_list>\n" + harness
        self.compile_run(harness)
        self.compile_run(harness, station=False)
        policy = (ROOT / "src/helpers/UserGpioPinPolicy.h").read_text()
        self.assertIn("if (station_g3::isRadioPin(pin)) return true;", policy)
        self.compile_run(harness, fail=True, files={
            "helpers/UserGpioPinPolicy.h": policy.replace(
                "if (station_g3::isRadioPin(pin)) return true;", ""),
            "helpers/sensors/EnvironmentI2CConfig.h":
                (ROOT / "src/helpers/sensors/EnvironmentI2CConfig.h").read_text(),
        })


if __name__ == "__main__":
    unittest.main()
