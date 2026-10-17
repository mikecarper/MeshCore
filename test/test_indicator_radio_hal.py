"""Compile the actual Indicator HAL against an error-injectable I2C device."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class IndicatorRadioHalTest(unittest.TestCase):
    def test_port_zero_transactions_fresh_busy_and_shared_irq_errors(self):
        arduino = r'''
#pragma once
#include <cstdint>
#include <cstddef>
constexpr uint32_t RADIOLIB_NC = 0xffffffff;
constexpr int LOW=0, HIGH=1, INPUT_PULLUP=2;
constexpr int PIN_BOARD_SDA=39, PIN_BOARD_SCL=40, P_LORA_BUSY=66;
extern int nativeLevel, nativeWrites, nativeModes;
inline void pinMode(uint32_t, uint32_t) { ++nativeModes; }
inline int digitalRead(uint32_t) { return nativeLevel; }
'''
        buffered = r'''
#pragma once
#include <Arduino.h>
#include <SPI.h>
struct ArduinoHal {
  enum { GpioModeOutput=1, GpioModeInput=0, GpioLevelLow=0, GpioLevelHigh=1,
         GpioInterruptRising=2, GpioInterruptFalling=3 };
  virtual ~ArduinoHal() = default;
  virtual void pinMode(uint32_t, uint32_t) { ++nativeModes; }
  virtual void digitalWrite(uint32_t, uint32_t) { ++nativeWrites; }
  virtual uint32_t digitalRead(uint32_t) { return nativeLevel; }
  virtual void attachInterrupt(uint32_t, void (*)(), uint32_t) {}
  virtual void detachInterrupt(uint32_t) {}
  virtual uint32_t pinToInterrupt(uint32_t pin) { return pin + 100; }
};
struct ESP32BufferedRadioHal : ArduinoHal {
  explicit ESP32BufferedRadioHal(SPIClass&) {}
};
constexpr uint32_t RISING = ArduinoHal::GpioInterruptRising;
'''
        gfx = r'''
#pragma once
#include <cstdint>
#include <cstddef>
namespace lgfx { namespace i2c {
struct Result { bool ok; bool has_value() const { return ok; } };
Result readRegister(uint8_t, uint8_t, uint8_t, uint8_t*, size_t, uint32_t);
Result transactionWrite(uint8_t, uint8_t, const uint8_t*, size_t, uint32_t);
Result init(uint8_t, uint8_t, uint8_t);
} }
'''
        fixture = r'''
#include <cassert>
#include <vector>
#include <algorithm>
#include "@ROOT@/variants/sensecap_indicator-espnow/IndicatorRadioHal.cpp"
int nativeLevel=HIGH, nativeWrites=0, nativeModes=0;
uint8_t registers[8] = {0,0,0xff,0xff,0,0,0xff,0xff};
struct Transfer { bool write; uint8_t reg; size_t size; };
std::vector<Transfer> transfers;
bool failRead=false, failWrite=false, failInit=false;
unsigned callbacks=0, inits=0;
void irq() { ++callbacks; }
namespace lgfx { namespace i2c {
Result readRegister(uint8_t port,uint8_t address,uint8_t reg,uint8_t* data,
                    size_t size,uint32_t frequency) {
  assert(port==0 && address==0x20 && frequency==400000);
  assert((size==1 || size==2) && reg+size<=8);
  transfers.push_back({false,reg,size});
  if(failRead) return {false};
  std::copy(registers+reg,registers+reg+size,data);return {true};
}
Result transactionWrite(uint8_t port,uint8_t address,const uint8_t* data,
                        size_t size,uint32_t frequency) {
  assert(port==0 && address==0x20 && frequency==400000);
  assert((size==2 || size==3) && data[0]+size-1<=8);
  transfers.push_back({true,data[0],size});
  if(failWrite) return {false};
  std::copy(data+1,data+size,registers+data[0]);return {true};
}
Result init(uint8_t port,uint8_t sda,uint8_t scl) {
  assert(port==0 && sda==39 && scl==40);++inits;return {!failInit};
}
} }
void last(bool write,uint8_t reg,size_t size) {
  assert(!transfers.empty());auto t=transfers.back();
  assert(t.write==write && t.reg==reg && t.size==size);
}
int main() {
  SPIClass spi;IndicatorRadioHal hal(spi);
  assert(hal.beginExpander() && transfers.size()==3);
  for(auto t:transfers) assert(!t.write && t.size==2);
  assert(hal.beginExpander() && transfers.size()==3);

  // Low-bank pin updates send only that bank, leaving unrelated Port 1 alone.
  registers[7]=0x5a;
  hal.pinMode(64,ArduinoHal::GpioModeOutput);last(true,6,2);
  assert(registers[6]==0xfe && registers[7]==0x5a);
  registers[3]=0x5a;
  hal.digitalWrite(64,ArduinoHal::GpioLevelLow);last(true,2,2);
  assert(registers[2]==0xfe && registers[3]==0x5a);
  size_t count=transfers.size();hal.digitalWrite(64,0);
  assert(transfers.size()==count); // unchanged latch still needs no write

  // Failed writes must not poison the software shadow or suppress the retry.
  failWrite=true;hal.digitalWrite(64,1);last(true,2,2);
  assert(registers[2]==0xfe);failWrite=false;
  hal.digitalWrite(64,1);assert(registers[2]==0xff);
  failWrite=true;hal.pinMode(65,ArduinoHal::GpioModeOutput);
  assert(registers[6]==0xfe);failWrite=false;
  hal.pinMode(65,ArduinoHal::GpioModeOutput);assert(registers[6]==0xfc);

  // Preserve generic Port 1 support and its original whole-word transactions.
  hal.digitalWrite(72,0);last(true,2,3);assert(registers[3]==0xfe);
  hal.pinMode(72,ArduinoHal::GpioModeOutput);last(true,6,3);
  registers[1]=1;assert(hal.digitalRead(72)==1);last(false,0,2);

  // BUSY must be read over I2C every time, even with expander INT inactive.
  nativeLevel=HIGH;registers[0]=4;
  assert(hal.digitalRead(66)==1);last(false,0,1);
  count=transfers.size();registers[0]=0;
  assert(hal.digitalRead(66)==0 && transfers.size()==count+1);last(false,0,1);
#ifdef MC_SX1262_HIL
  // Only HIL can restore the old two-bank bus-width control in this image.
  hal.hilUsePort0Only=false;
  assert(hal.digitalRead(66)==0);last(false,0,2);
  hal.digitalWrite(64,0);last(true,2,3);
  hal.pinMode(65,ArduinoHal::GpioModeInput);last(true,6,3);
  hal.hilUsePort0Only=true;
  assert(hal.digitalRead(66)==0);last(false,0,1);
  hal.digitalWrite(64,1);last(true,2,2);
  hal.pinMode(65,ArduinoHal::GpioModeOutput);last(true,6,2);
#endif
  failRead=true;assert(hal.digitalRead(66)==1 && hal.digitalRead(67)==0);
  failRead=false;

  // IRQ service still reads BOTH banks, clearing all shared expander sources.
  hal.attachInterrupt(67,irq,ArduinoHal::GpioInterruptRising);last(false,0,2);
  registers[0]=8;nativeLevel=LOW;
  hal.serviceInterrupt();last(false,0,2);assert(callbacks==1);
  hal.serviceInterrupt();assert(callbacks==1);
  registers[0]=0;assert(hal.digitalRead(66)==0);assert(callbacks==1);
  failRead=true;registers[0]=8;hal.serviceInterrupt();assert(callbacks==1);
  failRead=false;hal.serviceInterrupt();assert(callbacks==2);
  hal.detachInterrupt(67);count=transfers.size();hal.serviceInterrupt();
  assert(count==transfers.size());

  // Native and invalid pins do not accidentally address the expander.
  count=transfers.size();hal.digitalWrite(5,1);hal.pinMode(5,1);
  assert(hal.digitalRead(5)==LOW && nativeWrites==1);
  assert(hal.pinToInterrupt(5)==105 && hal.pinToInterrupt(67)==67);
  assert(hal.digitalRead(80)==0);hal.digitalWrite(80,1);hal.pinMode(80,1);
  assert(count==transfers.size());

  // Initial bus recovery remains whole-word and fails closed when unavailable.
  IndicatorRadioHal failed(spi);failRead=true;
  assert(!failed.beginExpander() && inits==1);
  assert(failed.digitalRead(66)==1);failRead=false;
  assert(failed.beginExpander());
}
'''.replace("@ROOT@", str(ROOT))
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "helpers/radiolib").mkdir(parents=True)
            (base / "Arduino.h").write_text(arduino)
            (base / "SPI.h").write_text("#pragma once\nstruct SPIClass {};\n")
            (base / "LovyanGFX.hpp").write_text(gfx)
            (base / "helpers/radiolib/ESP32BufferedRadioHal.h").write_text(buffered)
            source, binary = base / "fixture.cpp", base / "fixture"
            source.write_text(fixture)
            for definitions in ([], ["-DMC_SX1262_HIL"]):
                with self.subTest(definitions=definitions):
                    built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                        "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                        "-fno-omit-frame-pointer", "-no-pie", "-DSENSECAP_INDICATOR_LORA",
                        *definitions, "-I", str(base), str(source), "-o", str(binary)],
                        capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
