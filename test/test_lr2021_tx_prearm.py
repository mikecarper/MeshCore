"""Exercise the W12 TX warm-up using real driver methods and board routing.

The native radio mock models the distinction between host RF-switch GPIOs and
LR2021 DIO masks. No PlatformIO or RF hardware is needed. Failed SPI responses
may still apply a mask, so cleanup must not assume an error left a pin unchanged.
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
    return source[start:end].replace(" override", "")


HARNESS = r'''
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>
#include <helpers/radiolib/LR2021Band.h>
#include <helpers/radiolib/RadioRegulatorState.h>
@POLICY@
#define LR2021_TX_BUSY_TIMEOUT_MS 4UL
#define LR2021_USE_DCDC 0
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_ERR_UNSUPPORTED -99
#define RADIOLIB_ERR_SPI_CMD_TIMEOUT -705
#define RADIOLIB_NC 0xffffffffU
#define RFSWITCH_PIN_FLAG (1UL << 31)
#define RADIOLIB_LRXXXX_DIOx_VAL(pin) ((pin) & ~RFSWITCH_PIN_FLAG)
#define RADIOLIB_LR2021_DIO5 (RFSWITCH_PIN_FLAG | 0)
#define RADIOLIB_LR2021_DIO6 (RFSWITCH_PIN_FLAG | 1)
#define RADIOLIB_LR2021_DIO9 (RFSWITCH_PIN_FLAG | 4)
#define RADIOLIB_LR2021_DIO10 (RFSWITCH_PIN_FLAG | 5)
#define RADIOLIB_LR2021_DIO11 (RFSWITCH_PIN_FLAG | 6)
#define RADIOLIB_LR2021_DIO_SLEEP_PULL_UP 2
#define RADIOLIB_LR2021_DIO_SLEEP_PULL_AUTO 3
#define RADIOLIB_LR2021_DIO_FUNCTION_RF_SWITCH 0x20
#define RADIOLIB_LR2021_TX_TIMEOUT_NONE 0
#define RADIOLIB_LR2021_TX_TEST_MODE_CW 0
#define RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE 0x12
#define RADIOLIB_LR2021_RX_TIMEOUT_INF 0xffffff
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 0
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 0
#define RADIOLIB_IRQ_PREAMBLE_DETECTED 5
#define RADIOLIB_RADIO_MODE_NONE 0
#define RADIOLIB_RADIO_MODE_RX 1
#define RADIOLIB_RADIO_MODE_TX 2
#define LOW 0
#define HIGH 1
#define END_OF_MODE_TABLE {0, {0, 0, 0, 0, 0}}
#define LORA_FREQ 909.5f
#define LORA_BW 500.0f
#define LORA_SF 5
#define LORA_CR 5
#define LORA_TX_POWER 2
using RadioLibTime_t = uint32_t;
struct SPIClass {};
struct Logger { void print(const char*) {} void println(int) {} };
namespace mesh { Logger& usbLoggingPort() { static Logger logger;return logger; } }
struct Hal {
  uint32_t GpioLevelHigh=HIGH;
  uint32_t now=0;
  bool busy=false;
  int delays=0;
  uint32_t millis() { return now; }
  int digitalRead(uint32_t) { return busy; }
  void yield() { ++now; }
  void delay(uint32_t ms) { assert(ms==LR2021_TX_PREARM_MS);now+=ms;++delays; }
};
struct Module {
  static constexpr size_t RFSWITCH_MAX_PINS=5;
  // These are RadioLib's actual Module::OpMode_t values, not synthetic modes.
  enum OpMode_t { MODE_END_OF_TABLE=0, MODE_IDLE, MODE_RX, MODE_TX };
  struct RfSwitchMode_t { uint8_t mode;uint32_t values[RFSWITCH_MAX_PINS]; };
  Hal storage;Hal* hal=&storage;
  uint8_t host_switch=0;
  uint32_t getGpio() { return 13; }
  // DIO-controlled RF switches are NOT host GPIOs. The chip mode selects them.
  void setRfSwitchState(uint8_t mode) { host_switch=mode; }
};
class LR2021 {
public:
  // Exact RadioLib LR2021::OpMode_t declaration (aliases plus HF extensions).
  enum OpMode_t {
    MODE_END_OF_TABLE=Module::MODE_END_OF_TABLE,
    MODE_STBY=Module::MODE_IDLE,
    MODE_RX=Module::MODE_RX,
    MODE_TX=Module::MODE_TX,
    MODE_RX_HF,
    MODE_TX_HF,
  };
  Module* mod;
  float freqMHz=909.5f;
  uint8_t stagedMode=RADIOLIB_RADIO_MODE_NONE,chip_mode=MODE_STBY;
  uint32_t rxTimeout=0xffffff;
  uint8_t masks[7]={};
  bool levels[7]={},latch_masks_on_mode=false;
  int fail_dio=0,fail_count=0,fail_standby_at=0;
  bool fail_arm=false,fail_disarm=false,fail_tx=false,fail_standby=false;
  bool busy_after_tx=false,failed_standby_not_applied=false;
  int tx_calls=0,rx_calls=0,sleep_calls=0,standby_calls=0,function_calls=0;
  int previous_tx_delays=0;
  std::vector<int> writes,clear_attempts;
  explicit LR2021(Module* module):mod(module) {}
  virtual ~LR2021()=default;
  virtual int16_t launchMode() { return -99; }
  virtual int16_t startReceive() { return -99; }
  bool level(uint8_t dio) const { return levels[dio-5]; }
  void applyMode(uint8_t mode) {
    chip_mode=mode;
    for(size_t i=0;i<7;++i) levels[i]=(masks[i] & (1U << (mode-1)))!=0;
  }
  bool offInStandby() const {
    for(uint8_t dio:{9,10,11}) if(masks[dio-5]&1) return false;
    return true;
  }
  virtual int16_t standby() {
    ++standby_calls;
    const bool failed=fail_standby || standby_calls==fail_standby_at;
    if(!failed || !failed_standby_not_applied) applyMode(MODE_STBY);
    return failed ? -71 : 0;
  }
  virtual int16_t sleep() { ++sleep_calls;applyMode(MODE_STBY);return 0; }
  int16_t setTx(uint32_t timeout) {
    assert(timeout==0);++tx_calls;
#if LR2021_TX_PREARM_MS > 0
    assert(chip_mode==MODE_STBY);
    if(!mesh::lr2021::isHighBand(freqMHz)) {
      assert(level(9) && level(10) && level(11));
      assert(mod->hal->delays==previous_tx_delays+1);
      assert(mod->hal->now>=LR2021_TX_PREARM_MS);
    }
#endif
    previous_tx_delays=mod->hal->delays;
    // A failed result does not prove that the TX command was not applied.
    applyMode(mesh::lr2021::isHighBand(freqMHz) ? MODE_TX_HF : MODE_TX);
    mod->hal->busy=busy_after_tx;
    return fail_tx ? -72 : 0;
  }
  int16_t setTxTestMode(uint8_t mode) { assert(mode==0);return setTx(0); }
  int16_t setRx(uint32_t timeout) {
    assert(timeout==rxTimeout);++rx_calls;
#if LR2021_TX_PREARM_MS > 0
    assert(offInStandby());
#endif
    applyMode(mesh::lr2021::isHighBand(freqMHz) ? MODE_RX_HF : MODE_RX);
    return 0;
  }
  int16_t setDioRfSwitchConfig(uint8_t dio,uint8_t mask) {
    assert(dio>=5 && dio<=11);writes.push_back(dio);
    const bool arming=(mask&1)!=0;
    if(!arming && (dio>=9)) clear_attempts.push_back(dio);
    masks[dio-5]=mask;
    if(!latch_masks_on_mode) levels[dio-5]=(mask & (1U << (chip_mode-1)))!=0;
    // Restoring STBY bits must never momentarily deassert an active TX FEM.
    if(chip_mode==MODE_TX && dio>=9) assert(level(dio));
    const bool match=(arming && fail_arm) || (!arming && fail_disarm);
    if(dio==fail_dio && match && fail_count) {
      if(fail_count>0) --fail_count;
      return -73;
    }
    return 0;
  }
  int16_t setDioFunction(uint8_t dio,uint8_t function,uint8_t pull) {
    assert(chip_mode==MODE_STBY); // Semtech permits this in Standby RC only.
    assert(dio>=5 && dio<=11 && function==0x20 && (pull==2 || pull==3));
    ++function_calls;return 0;
  }
  void setRfSwitchTable(const uint32_t (&pins)[5],const Module::RfSwitchMode_t* table) {
    for(size_t i=0;i<5;++i) {
      uint8_t mask=0;
      for(size_t j=0;table[j].mode;++j)
        if(table[j].values[i]==HIGH) mask|=1U<<(table[j].mode-1);
      masks[RADIOLIB_LRXXXX_DIOx_VAL(pins[i])]=mask;
    }
    applyMode(chip_mode);
  }
  int16_t begin(float,float,uint8_t,uint8_t,uint8_t,int8_t,uint16_t,float) {
    std::fill(masks,masks+7,0);applyMode(MODE_STBY);return 0;
  }
  int16_t setRegulatorDCDC() { assert(false); return -1; }
  int16_t setCRC(uint8_t) { return 0; }
  int16_t explicitHeader() { return 0; }
  int16_t clearRxFifo() { return 0; }
  int16_t startReceive(uint32_t timeout,uint32_t,uint32_t,int) {
    rxTimeout=timeout;stagedMode=RADIOLIB_RADIO_MODE_RX;return launchMode();
  }
};
class CustomLR2021:public LR2021 {
  @REGULATOR@
public:
  using LR2021::LR2021;
  uint32_t _rf_switch_pins[Module::RFSWITCH_MAX_PINS]={};
  const Module::RfSwitchMode_t* _rf_switch_table=nullptr;
#if LR2021_TX_PREARM_MS > 0
  bool _tx_standby_armed=false;
  @MASK@
  @PREPARE@
  @FINISH@
  @STANDBY@
  @SLEEP@
#endif
  @RESTORE@
  @TABLE@
  @INIT@
  @RX_MODE@
  @TX_MODE@
  @LAUNCH@
  @MORSE@
  @RECEIVE@
};
@BOARD_PINS@
@BOARD_TABLE@
static void configure(CustomLR2021& radio) {
  radio.setRfSwitchTable(rfswitch_dios,rfswitch_table);
  assert(radio.offInStandby());
  radio.writes.clear();radio.clear_attempts.clear();
}
static void noBias(CustomLR2021& radio) {
  assert(radio.offInStandby());
  if(radio.chip_mode==LR2021::MODE_STBY)
    assert(!radio.level(9) && !radio.level(10) && !radio.level(11));
#if LR2021_TX_PREARM_MS > 0
  assert(!radio._tx_standby_armed);
#endif
}
static int packet(CustomLR2021& radio) {
  radio.stagedMode=RADIOLIB_RADIO_MODE_TX;
  const int result=radio.launchMode();
  assert(radio.stagedMode==RADIOLIB_RADIO_MODE_NONE);
  return result;
}
int main(int argc,char** argv) {
  assert(argc==2);std::string test=argv[1];
  Module module;CustomLR2021 radio(&module);configure(radio);
  if(test.rfind("latched_",0)==0) {
    radio.latch_masks_on_mode=true;test=test.substr(8);
  }
  if(test=="packet" || test=="cw") {
    radio.chip_mode=LR2021::MODE_RX;
    assert((test=="packet" ? packet(radio) : radio.transmitMorseCarrier())==0);
    assert(radio.tx_calls==1 && radio.chip_mode==LR2021::MODE_TX);
    noBias(radio);
#if LR2021_TX_PREARM_MS > 0
    assert(radio.writes==std::vector<int>({9,10,11,9,10,11}));
    assert(module.hal->delays==1 && radio.standby_calls==2);
#else
    assert(radio.writes.empty() && module.hal->delays==0 && radio.standby_calls==0);
#endif
    assert(radio.function_calls==0);
    assert(radio.standby()==0);noBias(radio);
    assert(radio.startReceive()==0 && radio.chip_mode==LR2021::MODE_RX);
    assert(!radio.level(9) && radio.level(11));
  } else if(test=="hf") {
    radio.freqMHz=2440;
    assert(packet(radio)==0 && radio.chip_mode==LR2021::MODE_TX_HF);
    assert(radio.writes.empty() && module.hal->delays==0);noBias(radio);
    assert(radio.mod->host_switch==LR2021::MODE_TX_HF);
    assert(radio.level(5) && !radio.level(6) && !radio.level(9) && !radio.level(11));
    radio.stagedMode=RADIOLIB_RADIO_MODE_RX;
    assert(radio.launchMode()==0 && radio.mod->host_switch==LR2021::MODE_RX_HF);
    assert(!radio.level(5) && radio.level(6) && !radio.level(11));
  }
#if LR2021_TX_PREARM_MS > 0
  else if(test=="tx_fail" || test=="cw_fail" || test=="busy") {
    radio.fail_tx=(test!="busy");radio.busy_after_tx=(test=="busy");
    assert((test=="cw_fail" ? radio.transmitMorseCarrier() : packet(radio))==
      (test=="busy" ? RADIOLIB_ERR_SPI_CMD_TIMEOUT : -72));
    assert(radio.tx_calls==1 && radio.chip_mode==LR2021::MODE_STBY);
    assert(radio.clear_attempts==std::vector<int>({9,10,11}));noBias(radio);
  } else if(test=="standby_fail" || test=="second_standby_fail") {
    if(test=="standby_fail") radio.fail_standby=true;
    else radio.fail_standby_at=2;
    assert(packet(radio)==(radio.fail_standby ? RADIOLIB_ERR_SPI_CMD_TIMEOUT : -71));
    assert(radio.tx_calls==0 && module.hal->delays==0);noBias(radio);
    if(radio.fail_standby) assert(radio.writes.empty());
    else assert(radio.clear_attempts==std::vector<int>({9,10,11}));
  } else if(test.rfind("arm_fail",0)==0 || test.rfind("disarm_fail",0)==0) {
    const bool arm=test.rfind("arm_fail",0)==0;
    radio.fail_dio=std::stoi(test.substr(arm?8:11));
    radio.fail_arm=arm;radio.fail_disarm=!arm;radio.fail_count=1;
    assert(packet(radio)==(arm ? -73 : RADIOLIB_ERR_SPI_CMD_TIMEOUT));
    assert(radio.tx_calls==(arm?0:1));noBias(radio);
    assert(radio.writes.size()>=(arm?6U:9U));
    // A failed response still requires every later pin to be restored.
    assert(radio.clear_attempts[0]==9 && radio.clear_attempts[1]==10 && radio.clear_attempts[2]==11);
    assert(radio.chip_mode==LR2021::MODE_STBY);
  } else if(test=="persistent_cleanup") {
    radio.fail_dio=10;radio.fail_disarm=true;radio.fail_count=-1;
    assert(packet(radio)==RADIOLIB_ERR_SPI_CMD_TIMEOUT);
    assert(radio._tx_standby_armed && radio.clear_attempts.size()==6);
    assert(radio.clear_attempts==std::vector<int>({9,10,11,9,10,11}));
    radio.stagedMode=RADIOLIB_RADIO_MODE_RX;
    assert(radio.launchMode()==RADIOLIB_ERR_SPI_CMD_TIMEOUT && radio.rx_calls==0);
    assert(radio.stagedMode==RADIOLIB_RADIO_MODE_NONE && radio._tx_standby_armed);
    assert(radio.sleep()==RADIOLIB_ERR_SPI_CMD_TIMEOUT && radio.sleep_calls==0);
    radio.fail_count=0;assert(radio.sleep()==0 && radio.sleep_calls==1);noBias(radio);
  } else if(test=="standby" || test=="sleep" || test=="receive" || test=="reset") {
    assert(radio.prepareTxRfSwitch()==0 && radio._tx_standby_armed);
    assert(!radio.offInStandby());
    if(test=="standby") assert(radio.standby()==0);
    if(test=="sleep") assert(radio.sleep()==0 && radio.sleep_calls==1);
    if(test=="receive") {
      assert(radio.startReceive()==0 && radio.rx_calls==1);
      assert(radio.chip_mode==LR2021::MODE_RX && !radio.level(9));
    }
    if(test=="reset") assert(radio.std_init(nullptr));
    noBias(radio);
    assert(radio.clear_attempts.size()>=3);
  } else if(test=="dirty_first_standby_fail" || test=="dirty_final_standby_fail") {
    // A mask can be written successfully while the current DIO output is
    // unchanged until a mode command. A failed mode command may not execute.
    radio.latch_masks_on_mode=true;
    assert(radio.prepareTxRfSwitch()==0 && radio._tx_standby_armed);
    assert(radio.standby_calls==2 && radio.level(9) && radio.level(10) && radio.level(11));
    radio.failed_standby_not_applied=true;
    radio.fail_standby_at=(test=="dirty_first_standby_fail" ? 3 : 4);
    assert(radio.standby()==RADIOLIB_ERR_SPI_CMD_TIMEOUT);
    assert(radio._tx_standby_armed && radio.offInStandby());
    assert(radio.level(9) && radio.level(10) && radio.level(11));
    assert(radio.clear_attempts==std::vector<int>({9,10,11}));
    assert(radio.tx_calls==0 && radio.rx_calls==0 && radio.sleep_calls==0);
    radio.fail_standby_at=0;
    assert(radio.standby()==0);noBias(radio);
    assert(radio.clear_attempts==std::vector<int>({9,10,11,9,10,11}));
  } else if(test=="repeat") {
    for(float frequency:{909.5f,2440.0f,909.5f,2440.0f,909.5f}) {
      radio.freqMHz=frequency;
      for(bool cw:{false,true}) {
        assert((cw ? radio.transmitMorseCarrier() : packet(radio))==0);
        noBias(radio);
        assert(radio.standby()==0);noBias(radio);
        assert(radio.startReceive()==0);noBias(radio);
        assert(radio.chip_mode==(frequency>1500 ? LR2021::MODE_RX_HF : LR2021::MODE_RX));
      }
    }
    assert(radio.tx_calls==10 && module.hal->delays==6);
    assert(radio.function_calls==0);
  }
#endif
  else assert(false);
}
'''


class LR2021TxPrearmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise AssertionError("a host C++17 compiler is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-lr2021-prearm-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binaries = {}
        driver = (ROOT / "src/helpers/radiolib/CustomLR2021.h").read_text()
        board = (ROOT / "variants/meshnology_w12/target.cpp").read_text()
        source = HARNESS.replace("@REGULATOR@", regulator_members(driver))
        for marker, signature in {
            "@MASK@": "int16_t setTxStandbyMask(",
            "@PREPARE@": "int16_t prepareTxRfSwitch(",
            "@FINISH@": "int16_t finishTxRfSwitch(",
            "@STANDBY@": "int16_t standby()",
            "@SLEEP@": "int16_t sleep()",
            "@RESTORE@": "int16_t restoreRfSwitchTable(",
            "@TABLE@": "void setRfSwitchTable(",
            "@INIT@": "bool std_init(",
            "@RX_MODE@": "uint8_t rfSwitchRxMode(",
            "@TX_MODE@": "uint8_t rfSwitchTxMode(",
            "@LAUNCH@": "int16_t launchMode(",
            "@MORSE@": "int16_t transmitMorseCarrier(",
            "@RECEIVE@": "int16_t startReceive()",
        }.items():
            source = source.replace(marker, method(driver, signature))
        source = source.replace("@POLICY@", re.search(
            r"#ifndef LR2021_TX_PREARM_MS\b.*?#endif", driver, re.S).group(0))
        for marker, pattern in {
            "@BOARD_PINS@": r"const uint32_t rfswitch_dios\[\].*?\n};",
            "@BOARD_TABLE@": r"static const Module::RfSwitchMode_t rfswitch_table\[\].*?\n};",
        }.items():
            source = source.replace(marker, re.search(pattern, board, re.S).group(0))
        for name, flags in {"w12": ["-DLR2021_TX_PREARM_MS=1"], "default": [],
                            "disabled": ["-DLR2021_TX_PREARM_MS=0"]}.items():
            cpp = Path(cls.temp.name) / (name + ".cpp")
            binary = Path(cls.temp.name) / (name + (".exe" if os.name == "nt" else ""))
            cpp.write_text(source, encoding="utf-8")
            sanitizers = [] if os.name == "nt" else ["-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", *sanitizers, *flags, "-I", str(ROOT / "src"),
                str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=40)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries[name] = binary

    def run_case(self, case, build="w12"):
        ran = subprocess.run([str(self.binaries[build]), case], capture_output=True,
                             text=True, timeout=10)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_packet_settles_pa_without_retaining_standby_bias(self):
        self.run_case("packet")

    def test_morse_cw_uses_same_preparation_and_cleanup(self):
        self.run_case("cw")

    def test_high_band_does_not_arm_low_frequency_pa(self):
        self.run_case("hf")

    def test_failed_packet_cw_and_busy_timeout_abort_and_disarm(self):
        for case in ["tx_fail", "cw_fail", "busy", "standby_fail", "second_standby_fail"]:
            with self.subTest(case=case):
                self.run_case(case)

    def test_every_arm_and_cleanup_pin_failure_still_cleans_later_pins(self):
        for phase in ["arm_fail", "disarm_fail"]:
            for dio in [9, 10, 11]:
                with self.subTest(phase=phase, dio=dio):
                    self.run_case(phase + str(dio))

    def test_persistent_cleanup_error_blocks_receive_and_sleep_until_recovery(self):
        self.run_case("persistent_cleanup")

    def test_standby_sleep_receive_and_actual_reset_restore_normal_table(self):
        for case in ["standby", "sleep", "receive", "reset"]:
            with self.subTest(case=case):
                self.run_case(case)

    def test_default_and_explicit_optout_keep_existing_tx_behavior(self):
        for build in ["default", "disabled"]:
            for case in ["packet", "cw", "hf"]:
                with self.subTest(build=build, case=case):
                    self.run_case(case, build)

    def test_prearm_and_cleanup_with_mode_latched_dio_outputs(self):
        for case in ["packet", "cw", "tx_fail", "cw_fail", "busy", "second_standby_fail",
                     "persistent_cleanup", "standby", "sleep", "receive", "reset"]:
            with self.subTest(case=case):
                self.run_case("latched_" + case)

    def test_repeated_packet_and_cw_across_lf_hf_switches_on_same_instance(self):
        for case in ["repeat", "latched_repeat"]:
            with self.subTest(case=case):
                self.run_case(case)

    def test_dirty_cleanup_keeps_retry_flag_until_physical_standby_is_confirmed(self):
        for case in ["dirty_first_standby_fail", "dirty_final_standby_fail"]:
            with self.subTest(case=case):
                self.run_case(case)

    def test_only_w12_enables_prearm_and_normal_standby_remains_off(self):
        opted_in = []
        for recipe in sorted((ROOT / "variants").glob("*/platformio.ini")):
            if re.search(r"(?m)^\s*-D\s+LR2021_TX_PREARM_MS=[1-9]\d*\b", recipe.read_text()):
                opted_in.append(recipe.parent.name)
        self.assertEqual(opted_in, ["meshnology_w12"])


if __name__ == "__main__":
    unittest.main()
