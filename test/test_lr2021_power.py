"""Execute LR2021 regulator setup and reset/restoration with inert native calls.

No hardware or PlatformIO. These tests execute extracted production methods,
including their real DIO routing, and inject failures before RX admission.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from radio_regulator_harness import regulator_members


ROOT = Path(__file__).resolve().parents[1]


def method(source, signature):
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HARNESS = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>
#include <algorithm>
#include <helpers/radiolib/LR2021Band.h>
#include <helpers/radiolib/RadioRegulatorState.h>
@POLICY@
constexpr bool ExpectDcdc = @EXPECT_DCDC@;
static_assert((LR2021_USE_DCDC != 0)==ExpectDcdc,"unexpected board regulator default");
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_NC 0xffffffffU
#define RFSWITCH_PIN_FLAG 0x80U
#define RADIOLIB_LRXXXX_DIOx_VAL(pin) ((pin)&0x7fU)
#define RADIOLIB_LR2021_DIO_SLEEP_PULL_UP 1
#define RADIOLIB_LR2021_DIO_SLEEP_PULL_AUTO 0
#define RADIOLIB_LR2021_DIO_FUNCTION_RF_SWITCH 6
#define RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE 0x12
#define LORA_FREQ 909.5f
#define LORA_BW 500.0f
#define LORA_SF 5
#define LORA_CR 5
#define LORA_TX_POWER 2
#define P_LORA_SCLK 11
#define P_LORA_MISO 40
#define P_LORA_MOSI 41
struct SPIClass {
  bool started=false,pins_set=false;
  void setPins(int miso,int sclk,int mosi) {
    assert(miso==40 && sclk==11 && mosi==41);pins_set=true;
  }
  void begin() { assert(pins_set);started=true; }
  void begin(int sclk,int miso,int mosi) {
    assert(sclk==11 && miso==40 && mosi==41);pins_set=started=true;
  }
};
struct Logger { void print(const char*) {} void println(int) {} };
namespace mesh {
  Logger& usbLoggingPort() { static Logger log;return log; }
  struct MorseRx { static constexpr float ReceiverBitRateKbps=4.8f; };
}
struct Hal { int GpioLevelHigh=1; };
struct Module {
  static constexpr size_t RFSWITCH_MAX_PINS=3;
  struct RfSwitchMode_t { uint8_t mode;int values[RFSWITCH_MAX_PINS]; };
  Hal storage;Hal* hal=&storage;
};
class LR2021 {
 public:
  enum { MODE_RX=3,MODE_TX=4,MODE_RX_HF=6,MODE_TX_HF=7,MODE_END_OF_TABLE=0 };
  Module* mod;
  int fail=0,regulator_calls=0;
  bool regulator_on=false;
  float tcxoVoltage=1.6f,frequency=0;
  uint8_t gainModeLf=1,gainModeHf=3,preambleDetLength=4,gain=0;
  uint8_t routing[7]={},pulls[7]={};
  std::vector<int> calls;
  explicit LR2021(Module* module):mod(module) {}
  int16_t step(int value) { calls.push_back(value);return value==fail ? -700-value : 0; }
  void resetNativeState() {
    regulator_on=false;std::fill(routing,routing+7,0);preambleDetLength=4;
  }
  int16_t begin(float freq,float bw,uint8_t sf,uint8_t cr,uint8_t sync,int8_t power,
                uint16_t preamble,float tcxo) {
    assert(freq==LORA_FREQ && bw==500 && sf==5 && cr==5 && sync==0x12);
    assert(power==2 && preamble==16);
    resetNativeState();frequency=freq;tcxoVoltage=tcxo;return step(1);
  }
  int16_t beginGFSK(float freq,float rate,float deviation,float bw,int8_t power,
                   uint16_t preamble,float tcxo) {
    assert(rate==4.8f && deviation==0.6f && bw==4.8f && power==2 && preamble==16);
    assert(tcxo==tcxoVoltage);
    resetNativeState();frequency=freq;return step(1);
  }
  int16_t setRegulatorDCDC() {
    ++regulator_calls;const int16_t result=step(2);if(!result)regulator_on=true;return result;
  }
  int16_t setRegulatorLDO() {
    ++regulator_calls;const int16_t result=step(2);if(!result)regulator_on=false;return result;
  }
  int16_t setCRC(uint8_t value) { assert(value==0 || value==2);return step(3); }
  int16_t explicitHeader() { return step(4); }
  int16_t setRxBoostedGainMode(uint8_t value) { gain=value;return step(5); }
  void setRfSwitchTable(const uint32_t (&)[Module::RFSWITCH_MAX_PINS],const Module::RfSwitchMode_t*) {}
  int16_t setDioFunction(uint8_t dio,uint8_t function,uint8_t pull) {
    assert(dio>=5 && dio<12 && function==6 && (pull==0 || pull==1));
    pulls[dio-5]=pull;return step(6);
  }
  int16_t setDioRfSwitchConfig(uint8_t dio,uint8_t mask) {
    assert(dio>=5 && dio<12);routing[dio-5]=mask;return step(7);
  }
  int16_t setSyncWord(uint8_t*,size_t length) { assert(length==0);return step(8); }
};
class CustomLR2021:public LR2021 {
  @REGULATOR@
  uint32_t _rf_switch_pins[Module::RFSWITCH_MAX_PINS]={};
  const Module::RfSwitchMode_t* _rf_switch_table=nullptr;
  @ROUTING@
 public:
  using LR2021::LR2021;
  @TABLE@
  @INIT@
  @MORSE@
};
struct Board {
  int prepare_calls=0,frequency_calls=0,finish_calls=0;
  bool prepare_ok=true,frequency_ok=true,finish_ok=true;
  bool prepareRadioFrequency(float frequency) { ++frequency_calls;assert(frequency==909.5f);return frequency_ok; }
  bool finishRadioHardReset() { ++finish_calls;return finish_ok; }
};
class Wrapper {
 public:
  CustomLR2021* _radio;
  Board* _board;
  bool _morse_rx_fsk_mode=false;
  bool prepareRadioHardReset() { ++_board->prepare_calls;return _board->prepare_ok; }
  bool restoreAfterDeepInit() { return radioDeepInit(); }
  @DEEP@
  @RESTORE@
};
static const uint32_t pins[]={0x80,0x81,7};
static const Module::RfSwitchMode_t table[]={
  {LR2021::MODE_RX,{1,0,0}}, {LR2021::MODE_TX,{0,1,0}},
  {LR2021::MODE_RX_HF,{1,0,0}}, {LR2021::MODE_TX_HF,{0,1,0}},
  {LR2021::MODE_END_OF_TABLE,{0,0,0}}};
static void configure(CustomLR2021& radio) { radio.setRfSwitchTable(pins,table); }
static void routed(const CustomLR2021& radio) {
  assert(radio.routing[0]==((1U<<2)|(1U<<5)));
  assert(radio.routing[1]==((1U<<3)|(1U<<6)));
  assert(radio.pulls[0]==1 && radio.pulls[1]==0);
}
int main() {
  using mesh::power::RegulatorMode;
  Module selection_module;
  CustomLR2021 selected(&selection_module);configure(selected);
  assert(selected.activeRegulatorMode()==RegulatorMode::Default && selected.calls.empty());
  assert(!selected.configureBootRegulator(static_cast<RegulatorMode>(3)));
  assert(selected.configureBootRegulator(RegulatorMode::Dcdc)==ExpectDcdc);
  assert(selected.configureBootRegulator(RegulatorMode::Ldo));
  assert(selected.calls.empty()); // selecting never touches SPI
  assert(selected.std_init() && !selected.regulator_on);
  assert(selected.activeRegulatorMode()==RegulatorMode::Ldo);
  assert(!selected.configureBootRegulator(RegulatorMode::Default));
  assert(selected.beginMorseReceive(2440.0f)==0 && !selected.regulator_on);
  assert(selected.activeRegulatorMode()==RegulatorMode::Ldo);
  assert(selected.std_init() && !selected.regulator_on); // recovery stays LDO
  selected.fail=ExpectDcdc?2:1;
  assert(!selected.std_init());
  assert(selected.activeRegulatorMode()==RegulatorMode::Default);
  assert(!selected.configureBootRegulator(RegulatorMode::Dcdc));
  selected.fail=0;
  assert(selected.std_init() && !selected.regulator_on);
  assert(selected.activeRegulatorMode()==RegulatorMode::Ldo);
  // Only verified/opted-in layouts reapply DC/DC after a native reset.
  Module module;CustomLR2021 radio(&module);configure(radio);SPIClass spi;
  assert(radio.std_init(&spi) && spi.started && radio.regulator_on==ExpectDcdc);
  assert(radio.regulator_calls==(ExpectDcdc?1:0));
  assert(radio.calls[0]==1 && radio.calls[1]==(ExpectDcdc?2:3));
#ifdef LR2021_TCXO_VOLTAGE
  assert(radio.tcxoVoltage==LR2021_TCXO_VOLTAGE);
#else
  assert(radio.tcxoVoltage==1.6f);
#endif
  routed(radio);
  for(int fault:{1,2,3,4,6,7}) {
    CustomLR2021 failed(&module);configure(failed);failed.fail=fault;
    if(fault==2 && !ExpectDcdc) {
      assert(failed.std_init() && !failed.regulator_on && failed.regulator_calls==0);
      routed(failed);continue;
    }
    assert(!failed.std_init());
    assert(failed.calls.back()==fault);
    assert(failed.regulator_calls==(ExpectDcdc && fault!=1 ? 1 : 0));
    if(fault==2) assert(failed.calls==std::vector<int>({1,2}) && !failed.regulator_on);
  }
  // GFSK initialization resets native regulator state too, for both RF bands.
  for(float frequency:{902.1f,2440.0f}) for(int fault:{0,1,2,3,5,6,7,8}) {
    CustomLR2021 received(&module);configure(received);received.fail=fault;
    const int16_t result=received.beginMorseReceive(frequency);
    const int expected_fault=(fault==2 && !ExpectDcdc)?0:fault;
    assert(result==(expected_fault ? -700-expected_fault : 0));
    assert(received.regulator_calls==(ExpectDcdc && fault!=1 ? 1 : 0));
    if(expected_fault) assert(received.calls.back()==expected_fault);
    if(fault==2 && ExpectDcdc)
      assert(received.calls==std::vector<int>({1,2}) && !received.regulator_on);
    if(!expected_fault) {
      assert(received.regulator_on==ExpectDcdc && received.preambleDetLength==0);
      assert(received.gain==(frequency>1900 ? 3 : 1));routed(received);
    }
  }
  // Execute the actual wrapper restoration: regulator failure keeps recovery pending.
  assert(radio.beginMorseReceive(902.1f)==0 && radio.regulator_calls==(ExpectDcdc?2:0));
  Board board;Wrapper wrapper{&radio,&board};wrapper._morse_rx_fsk_mode=true;
  radio.fail=ExpectDcdc?2:3;
  assert(!wrapper.restoreCarrierWaveModem() && wrapper._morse_rx_fsk_mode);
  assert(board.prepare_calls==1 && board.finish_calls==0);
  radio.fail=0;
  assert(wrapper.restoreCarrierWaveModem() && !wrapper._morse_rx_fsk_mode);
  assert(board.prepare_calls==2 && board.finish_calls==1 && radio.regulator_on==ExpectDcdc);
  assert(radio.frequency==909.5f && radio.regulator_calls==(ExpectDcdc?4:0));routed(radio);
  // Board reset/frequency failures do not proceed into the radio/regulator.
  const int calls=radio.regulator_calls;
  const size_t native_calls=radio.calls.size();
  board.prepare_ok=false;assert(!wrapper.radioDeepInit());
  board.prepare_ok=true;board.frequency_ok=false;assert(!wrapper.radioDeepInit());
  assert(radio.regulator_calls==calls && radio.calls.size()==native_calls);
}
'''


class LR2021PowerTest(unittest.TestCase):
    def run_methods(self, *flags):
        driver = (ROOT / "src/helpers/radiolib/CustomLR2021.h").read_text()
        wrapper = (ROOT / "src/helpers/radiolib/CustomLR2021Wrapper.h").read_text()
        restore = method(wrapper, "bool restoreCarrierWaveModem(").replace(" override", "")
        # The non-FSK branch is unchanged and already covered by CW/PA tests;
        # keep only the real FSK reset/restoration branch in this focused mock.
        branch = method(restore, "if (_morse_rx_fsk_mode)")
        restore = "bool restoreCarrierWaveModem() { " + branch + " return true; }"
        source = HARNESS
        policy = re.search(r"#ifndef LR2021_USE_DCDC\b.*?#endif", driver, re.S)
        self.assertIsNotNone(policy, "LR2021 board policy must remain explicit and default-safe")
        for marker, body in {
            "@POLICY@": policy.group(0),
            "@REGULATOR@": regulator_members(driver),
            "@EXPECT_DCDC@": "true" if "-DLR2021_USE_DCDC=1" in flags else "false",
            "@ROUTING@": method(driver, "int16_t restoreRfSwitchTable("),
            "@TABLE@": method(driver, "void setRfSwitchTable("),
            "@INIT@": method(driver, "bool std_init("),
            "@MORSE@": method(driver, "int16_t beginMorseReceive("),
            "@DEEP@": method(wrapper, "bool radioDeepInit(").replace(" override", ""),
            "@RESTORE@": restore,
        }.items():
            source = source.replace(marker, body)
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-lr2021-power-") as directory:
            cpp = Path(directory) / "test.cpp"
            binary = Path(directory) / ("test.exe" if os.name == "nt" else "test")
            cpp.write_text(source, encoding="utf-8")
            sanitizers = [] if os.name == "nt" else [
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                *sanitizers, *flags, "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_xtal_esp32_init_fsk_routing_failures_and_lora_restore(self):
        self.run_methods("-DLR2021_TCXO_VOLTAGE=0.0f", "-DLR2021_USE_DCDC=1")

    def test_tcxo_nrf52_init_fsk_routing_failures_and_lora_restore(self):
        self.run_methods("-DNRF52_PLATFORM=1", "-DLR2021_TCXO_VOLTAGE=1.6f",
                         "-DLR2021_RX_BOOSTED_GAIN=1", "-DLR2021_USE_DCDC=1")

    def test_default_tcxo_keeps_existing_regulator_and_routing_policy(self):
        self.run_methods()

    def test_explicit_ldo_remains_off_across_lora_gfsk_and_recovery(self):
        self.run_methods("-DNRF52_PLATFORM=1", "-DLR2021_USE_DCDC=0")

    def test_only_schematic_verified_w12_opts_in(self):
        opted_in = []
        for recipe in sorted((ROOT / "variants").glob("*/platformio.ini")):
            if re.search(r"(?m)^\s*-D\s+LR2021_USE_DCDC=1\b", recipe.read_text()):
                opted_in.append(recipe.parent.name)
        self.assertEqual(opted_in, ["meshnology_w12"])


if __name__ == "__main__":
    unittest.main()
