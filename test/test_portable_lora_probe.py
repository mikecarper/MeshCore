"""Run the real nRF52 identification transport against scripted SPI frames."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define PINS_COUNT 48
extern uint32_t g_ADigitalPinMap[PINS_COUNT];
uint32_t millis();
void delay(uint32_t);
void delayMicroseconds(uint32_t);
void pinMode(uint32_t,uint32_t);
void digitalWrite(uint32_t,uint32_t);
int digitalRead(uint32_t);
'''

SPI = r'''
#pragma once
#include <cstdint>
#define MSBFIRST 1
#define SPI_MODE0 0
struct SPISettings {
  uint32_t hz, order, mode;
  SPISettings(uint32_t h,uint32_t o,uint32_t m):hz(h),order(o),mode(m){}
};
class SPIClass {
 public:
  void beginTransaction(const SPISettings&);
  uint8_t transfer(uint8_t);
  void endTransaction();
};
'''

HARNESS = r'''
#include <cassert>
#include <array>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>
#include <helpers/radiolib/PortableLoRaChipProbe.h>

using mesh::LoRaRadioFamily;
uint32_t g_ADigitalPinMap[48];
uint32_t clock_ms=0, micros=0;
std::array<unsigned,48> states{};
std::vector<std::array<uint32_t,3>> gpio;
unsigned reset_count=0, current=0, frame_index=0;
int busy_after_frame=-1;
bool active=false,busy=false;
struct Frame {std::vector<uint8_t> out,in;};
std::vector<Frame> frames;
uint32_t millis(){return clock_ms;}
void delay(uint32_t n){clock_ms+=n;}
void delayMicroseconds(uint32_t n){micros+=n;}
void pinMode(uint32_t pin,uint32_t mode) {
  assert(pin<48 && (pin!=43 || mode==INPUT)); gpio.push_back({0,pin,mode});
}
void digitalWrite(uint32_t pin,uint32_t value) {
  assert(pin<48 && (pin==42 || pin==44));
  gpio.push_back({1,pin,value});states[pin]=value;
  if(pin==42 && value==LOW) ++reset_count;
}
int digitalRead(uint32_t pin){assert(pin==43);return busy?HIGH:LOW;}
void SPIClass::beginTransaction(const SPISettings& settings) {
  assert(settings.hz==1000000 && settings.order==MSBFIRST && settings.mode==SPI_MODE0);
  assert(!active && frame_index<frames.size());active=true;current=0;
}
uint8_t SPIClass::transfer(uint8_t out) {
  assert(active && states[44]==LOW && frame_index<frames.size());
  auto& frame=frames[frame_index];assert(current<frame.out.size());
  assert(frame.out[current]==out);
  return frame.in[current++];
}
void SPIClass::endTransaction() {
  assert(active && states[44]==HIGH && current==frames[frame_index].out.size());
  active=false;++frame_index;
  if(int(frame_index)==busy_after_frame) busy=true;
}
void sx(const std::string& signature) {
  Frame f;f.out={0x1d,0x03,0x20,0};f.in={0,0,0,0};
  for(unsigned char c:signature){f.out.push_back(0);f.in.push_back(c);}
  assert(signature.size()==6);frames.push_back(f);
}
void lr(uint8_t device=3,uint8_t status=6,uint8_t major=1,uint8_t minor=4) {
  frames.push_back({{1,1},{0,0}});
  frames.push_back({{0,0,0,0,0},{status,0x22,device,major,minor}});
}
void clear() {
  clock_ms=micros=reset_count=current=frame_index=0;busy_after_frame=-1;
  active=busy=false;frames.clear();gpio.clear();states.fill(0);
}
int main(int argc,char** argv) {
  assert(argc==2);
  for(unsigned i=0;i<48;++i)g_ADigitalPinMap[i]=i;
  SPIClass spi;
  const mesh::PortableLoRaChipProbePins pins{44,42,43};
  const int test=std::stoi(argv[1]);
  if(test==0) {
    sx("SX1261");sx("SX1261");
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::SX126x);
    assert(reset_count==1 && frame_index==2 && clock_ms==11);
  } else if(test==1) {
    sx("\xff\xff\xff\xff\xff\xff");lr();lr();
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::LR1121);
    assert(reset_count==2 && frame_index==5 && clock_ms==22);
  } else if(test==2) {
    for(unsigned device:{0,1,2,0xdf,0xff}) {
      clear();sx("\xff\xff\xff\xff\xff\xff");lr(device);
      assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
      assert(frame_index==3 && reset_count==2);
    }
  } else if(test==3) {
    sx("SX1261");sx("SX1268");lr();lr(3,6,1,5);
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
    assert(frame_index==6 && reset_count==2);
  } else if(test==4) {
    for(unsigned status:{0,0xff,2,8,10}) {
      clear();sx("badbad");lr(3,status);
      assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
      assert(frame_index==3 && reset_count==2);
    }
  } else if(test==5) {
    busy=true;
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
    assert(frame_index==0 && reset_count==1 && clock_ms==261);
  } else if(test==6) {
    sx("badbad");frames.push_back({{1,1},{0,0}});busy_after_frame=2;
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
    assert(frame_index==2 && clock_ms==272 && states[44]==HIGH && !active);
  } else if(test==7) {
    for(auto bad:std::vector<mesh::PortableLoRaChipProbePins>{
        {0xffffffffu,42,43},{44,0xffffffffu,43},{44,42,0xffffffffu},
        {48,42,43},{44,48,43},{44,42,48},{44,44,43},{44,42,42},{44,42,44}}) {
      assert(mesh::probePortableLoRaChip(spi,bad)==LoRaRadioFamily::None);
      assert(gpio.empty() && frame_index==0);
    }
    g_ADigitalPinMap[44]=0xffffffffu;
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
    assert(gpio.empty() && frame_index==0);
  } else if(test==8) {
    clock_ms=0xfffffff0u;const auto initial=clock_ms;busy=true;
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
    assert(uint32_t(clock_ms-initial)==261 && frame_index==0);
  } else if(test==9) {
    mesh::PortableLoRaChipProbeIO io(spi,pins);char a[6];uint8_t b[4];
    assert(!io.readSX126Version(nullptr,6) && !io.readSX126Version(a,5));
    assert(!io.readLR1121Version(nullptr,4) && !io.readLR1121Version(b,3));
    assert(gpio.empty() && frame_index==0);
  } else if(test==10) {
    sx("SX1268");lr();lr();
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::LR1121);
    assert(frame_index==5);
  } else if(test==11) {
    // One valid identifier never chooses a chip without its matching repeat.
    sx("SX1261");sx("badbad");lr(3);lr(2);
    assert(mesh::probePortableLoRaChip(spi,pins)==LoRaRadioFamily::None);
    assert(frame_index==6);
  }
  assert(!active);
  for(auto row:gpio) assert(row[1]==42 || row[1]==43 || row[1]==44);
  if(frame_index) assert(states[44]==HIGH);
}
'''


class PortableLoRaProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++11 compiler is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-portable-lora-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        (work / "Arduino.h").write_text(ARDUINO)
        (work / "SPI.h").write_text(SPI)
        (work / "RadioLib.h").write_text(
            "#pragma once\n#define RADIOLIB_SX126X_CMD_READ_REGISTER 0x1d\n"
            "#define RADIOLIB_SX126X_REG_VERSION_STRING 0x0320\n")
        source = work / "test.cpp"
        source.write_text(HARNESS)
        cls.binary = work / ("test.exe" if os.name == "nt" else "test")
        sanitize = [] if os.name == "nt" else ["-fsanitize=address,undefined",
            "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        result = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
            *sanitize, "-DNRF52_PLATFORM=1", "-DMESH_RADIO_AUTO_LR1121=1",
            "-I" + str(work), "-I" + str(ROOT / "src"), str(source), "-o", str(cls.binary)],
            capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def execute(self, case):
        result = subprocess.run([str(self.binary), str(case)], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_sx1262_repeat_skips_lr_commands(self): self.execute(0)
    def test_lr1121_uses_two_transactions_and_repeated_identity(self): self.execute(1)
    def test_other_lr_chips_and_bootloader_are_rejected(self): self.execute(2)
    def test_mismatching_firmware_identity_is_rejected(self): self.execute(3)
    def test_spi_errors_and_floating_status_are_rejected(self): self.execute(4)
    def test_stuck_busy_is_bounded_without_spi(self): self.execute(5)
    def test_busy_after_command_leaves_nss_deselected(self): self.execute(6)
    def test_invalid_and_unmapped_pins_touch_no_gpio(self): self.execute(7)
    def test_timeout_handles_clock_wrap(self): self.execute(8)
    def test_invalid_read_buffers_touch_no_spi(self): self.execute(9)
    def test_sx1268_signature_never_selects_sx1262(self): self.execute(10)
    def test_one_identifier_is_not_enough(self): self.execute(11)

    def test_rxps_defaults_follow_the_identified_chip_across_translation_units(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        with tempfile.TemporaryDirectory(prefix="meshcore-radio-rxps-") as directory:
            work = Path(directory)
            writer = work / "writer.cpp"
            writer.write_text(r'''
#include <helpers/LoRaRadioFamily.h>
void setFamily(unsigned value) {
  mesh::autoLoRaRxPowerSavingFamily() = value==1 ? mesh::LoRaRadioFamily::SX126x
      : value==2 ? mesh::LoRaRadioFamily::LR1121 : mesh::LoRaRadioFamily::None;
}
const void* writerAddress(){return &mesh::autoLoRaRxPowerSavingFamily();}
''')
            reader = work / "reader.cpp"
            reader.write_text(r'''
#include <helpers/radiolib/RXPowerSaving.h>
bool defaultLevel(uint8_t level,uint8_t sf,float bw,uint32_t* rx,uint32_t* sleep) {
  return calcRxPowerSavingLevel(level,sf,bw,16,rx,sleep);
}
uint32_t defaultTransition(){return rxPowerSavingDefaultTransitionUs();}
float defaultCost(){return rxPowerSavingDefaultCaptureCost();}
const void* readerAddress(){return &mesh::autoLoRaRxPowerSavingFamily();}
''')
            source = work / "main.cpp"
            source.write_text(r'''
#include <cassert>
#include <initializer_list>
#include <helpers/radiolib/RXPowerSaving.h>
void setFamily(unsigned);
const void* writerAddress();const void* readerAddress();
bool defaultLevel(uint8_t,uint8_t,float,uint32_t*,uint32_t*);
uint32_t defaultTransition();float defaultCost();
int main() {
  assert(writerAddress()==readerAddress());
  assert(readerAddress()==&mesh::autoLoRaRxPowerSavingFamily());
  for(unsigned family : {0,1,2,1,0}) {
    setFamily(family);
    const float expected_cost=family==1?6.0f:8.0f;
    const uint32_t expected_transition=family==1?2600:6000;
    assert(defaultTransition()==expected_transition && defaultCost()==expected_cost);
    for(unsigned level=1;level<=10;++level) for(unsigned sf=5;sf<=12;++sf)
      for(float bw : {62.5f,125.0f,250.0f,500.0f}) {
        uint32_t rx=0,sleep=0,expected_rx=0,expected_sleep=0;
        bool expected=calcRxPowerSavingLevel(level,sf,bw,16,&expected_rx,&expected_sleep,
            expected_cost,expected_transition);
        assert(defaultLevel(level,sf,bw,&rx,&sleep)==expected);
        assert(rx==expected_rx && sleep==expected_sleep);
      }
  }
}
''')
            binary = work / ("test.exe" if os.name == "nt" else "test")
            sanitize = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            compiled = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-O2", "-flto", *sanitize, "-DMESH_RADIO_AUTO_LR1121=1", "-DUSE_SX1262=1",
                "-DUSE_LR1121=1", "-DSX126X_DIO3_TCXO_VOLTAGE=3.3",
                "-DLR11X0_DIO3_TCXO_VOLTAGE=3.0", "-I" + str(ROOT / "src"),
                str(source), str(writer), str(reader), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == "__main__":
    unittest.main()
