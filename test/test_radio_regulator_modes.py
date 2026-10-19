"""Execute boot selection, regulator errors, Morse resets and radio recovery."""
from pathlib import Path
import unittest
from test_morse_tx import compile_run
from test_radio_receive_contract import method
from radio_regulator_harness import regulator_members

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <initializer_list>
#include <helpers/radiolib/RadioRegulatorState.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_SX126X_SYNC_WORD_PRIVATE 0x12
#define RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE 0x12
#define RADIOLIB_LR2021_LORA_SYNC_WORD_PRIVATE 0x12
#define LORA_FREQ 909.5f
#define LORA_BW 500.0f
#define LORA_SF 5
#define LORA_CR 5
#define LORA_TX_POWER 2
#define MESH_DEBUG_PRINTLN(...) ((void)0)
struct SPIClass {};
struct Module {};
namespace mesh {
struct Logger { void print(const char*) {} void println(int) {} };
inline Logger& usbLoggingPort() { static Logger logger;return logger; }
struct MorseRx { static constexpr float ReceiverBitRateKbps=4.8f; };
namespace lr2021 { inline bool isHighBand(float f) { return f>1900; } }
}
struct Native {
  int fail=0,spi=0,regulator_writes=0,modem_resets=0;
  bool useRegulatorLDO=true,standbyXOSC=true;
  float tcxoVoltage=1.6f;
  uint8_t syncWordLength=4,preambleDetLength=4,gainModeLf=1,gainModeHf=3;
  int step(int phase) { ++spi;return fail==phase ? -100-phase : 0; }
  int nativeReset() { ++modem_resets;useRegulatorLDO=true;return step(1); }
  int16_t begin(float,float,uint8_t,uint8_t,uint8_t,int8_t,uint16_t,float) {
    return nativeReset();
  }
  int16_t begin(float,float,uint8_t,uint8_t,uint8_t,int8_t,uint16_t,float,bool ldo) {
    assert(!standbyXOSC);
    int result=nativeReset();
    if(!result) { ++regulator_writes;result=step(2);if(!result)useRegulatorLDO=ldo; }
    return result;
  }
  virtual int16_t beginFSK(float,float,float,float,int8_t,uint16_t,float,bool ldo) {
    int result=nativeReset();
    if(!result) { ++regulator_writes;result=step(2);if(!result)useRegulatorLDO=ldo; }
    return result;
  }
  int16_t beginGFSK(float,float,float,float,int8_t,uint16_t,float) { return nativeReset(); }
  int16_t setRegulatorDCDC() {
    ++regulator_writes;int result=step(2);if(!result)useRegulatorLDO=false;return result;
  }
  int16_t setRegulatorLDO() {
    ++regulator_writes;int result=step(2);if(!result)useRegulatorLDO=true;return result;
  }
  int16_t applyMeshCoreTcxoDelay() { return step(3); }
  int16_t setCRC(int) { return step(4); }
  int16_t setRxBoostedGainMode(int) { return step(5); }
  int16_t setSyncWord(uint8_t*,size_t) { return step(6); }
  int16_t explicitHeader() { return step(7); }
  int16_t setDioAsRfSwitch(uint8_t,uint8_t,uint8_t,uint8_t,uint8_t,uint8_t,uint8_t,uint8_t) { return step(8); }
  struct Profile { void invalidate() {} };
  struct RfSwitch {
    template<class Callback> int restore(Callback callback) {
      const uint8_t modes[]={0,0,0,0,0,0,0};return callback(0,modes);
    }
  };
};
using @CHIP@=Native;
class Custom:public @CHIP@ {
  @POLICY@
  bool _coldStandby=false,_tcxoWakePending=false,_profilePacketDirty=true,_rx_boosted=false;
  Profile _profileSwitch;
  RfSwitch _morse_rf_switch;
public:
  int restoreRfSwitchTable() { return 0; }
  @INIT@
  @MORSE@
  int init() { return @INIT_CALL@; }
  int morse() { return @MORSE_CALL@; }
};
@WRAPPER@
int main() {
  using mesh::power::RegulatorMode;
  constexpr bool supported_dcdc=@DCDC@;
  for (auto requested:{RegulatorMode::Default,RegulatorMode::Ldo,RegulatorMode::Dcdc}) {
    Custom radio;Wrapper wrapper{&radio};
    assert(wrapper.activeRegulatorMode()==RegulatorMode::Default && radio.spi==0);
    assert(!wrapper.configureBootRegulator(static_cast<RegulatorMode>(3)));
    assert(wrapper.regulatorModes()==radio.regulatorModes());
    const bool supported=requested!=RegulatorMode::Dcdc || supported_dcdc;
    assert(wrapper.configureBootRegulator(requested)==supported);
    assert(radio.spi==0); // boot configuration cannot send commands mid-RX/TX
    if(!supported) continue;
    const auto expected=requested==RegulatorMode::Default ? wrapper.defaultRegulatorMode() : requested;
    assert(radio.init()==0 && wrapper.activeRegulatorMode()==expected);
    assert(radio.useRegulatorLDO==(expected==RegulatorMode::Ldo));
    assert(!wrapper.configureBootRegulator(RegulatorMode::Default));
    // Both transitions reset the native modem; the saved choice survives each.
    assert(radio.morse()==0 && wrapper.activeRegulatorMode()==expected);
    assert(radio.useRegulatorLDO==(expected==RegulatorMode::Ldo));
    assert(radio.init()==0 && wrapper.activeRegulatorMode()==expected);
    assert(radio.useRegulatorLDO==(expected==RegulatorMode::Ldo));
    for(int fault:{1,2}) {
      if(fault==2 && @SKIP_LDO_WRITE@) continue;
      radio.fail=fault;
      assert(radio.morse()!=0);
      assert(wrapper.activeRegulatorMode()==RegulatorMode::Default);
      assert(!wrapper.configureBootRegulator(RegulatorMode::Ldo));
      radio.fail=0;
      assert(radio.init()==0 && wrapper.activeRegulatorMode()==expected);
      assert(radio.useRegulatorLDO==(expected==RegulatorMode::Ldo));
      radio.fail=fault;
      assert(radio.init()!=0 && wrapper.activeRegulatorMode()==RegulatorMode::Default);
      radio.fail=0;
      assert(radio.init()==0 && wrapper.activeRegulatorMode()==expected);
    }
  }
  // A failed first init must lock the boot policy as firmly as a successful one.
  Custom failed;
  assert(failed.configureBootRegulator(RegulatorMode::Ldo));failed.fail=1;
  assert(failed.init()!=0 && !failed.configureBootRegulator(RegulatorMode::Default));
}
'''


class RadioRegulatorTests(unittest.TestCase):
    def exercise(self, chip, flags=(), dcdc=True):
        source = (ROOT / f'src/helpers/radiolib/Custom{chip}.h').read_text()
        if chip == 'LR2021':
            initialize = method(source, 'bool std_init(')
            init_call = 'std_init() ? 0 : -1'
        else:
            initialize = method(source, 'int16_t begin(float')
            init_call = 'begin()'
        if chip == 'SX1262':
            morse = method(source, 'int16_t beginFSK(float')
            morse_call = 'beginFSK()'
        else:
            morse = method(source, 'int16_t beginMorseReceive(')
            morse_call = 'beginMorseReceive(902.1f)'
        wrapper_source = (ROOT / f'src/helpers/radiolib/Custom{chip}Wrapper.h').read_text()
        delegates = '\n'.join(method(wrapper_source, signature).replace(' override', '')
            for signature in ('uint8_t regulatorModes(',
                'mesh::power::RegulatorMode defaultRegulatorMode(',
                'bool configureBootRegulator(', 'mesh::power::RegulatorMode activeRegulatorMode('))
        wrapper = f'using Custom{chip}=Custom;\nstruct Wrapper {{ Custom* _radio;\n{delegates}\n}};'
        code = HARNESS.replace('@WRAPPER@', wrapper)
        for marker, value in {'@CHIP@': chip, '@POLICY@': regulator_members(source),
                '@INIT@': initialize, '@MORSE@': morse, '@INIT_CALL@': init_call,
                '@MORSE_CALL@': morse_call, '@DCDC@': str(dcdc).lower(),
                '@SKIP_LDO_WRITE@': str(chip == 'LR2021' and not dcdc).lower()}.items():
            code = code.replace(marker, value)
        compile_run(self, code, flags=('-Wno-unused-parameter', *flags))

    def test_sx1262_boot_selection_morse_failures_and_recovery(self):
        self.exercise('SX1262')

    def test_sx1262_forced_ldo_recipe_cannot_enable_missing_dcdc_network(self):
        self.exercise('SX1262', ('-DSX126X_USE_REGULATOR_LDO=1',), dcdc=False)

    def test_lr1110_boot_selection_morse_failures_and_recovery(self):
        self.exercise('LR1110')

    def test_lr1121_boot_selection_morse_failures_and_recovery(self):
        self.exercise('LR1121')

    def test_lr2021_verified_layout_boot_selection_morse_failures_and_recovery(self):
        self.exercise('LR2021', ('-DLR2021_USE_DCDC=1',))

    def test_lr2021_unverified_layout_cannot_enable_simo(self):
        self.exercise('LR2021', ('-DLR2021_USE_DCDC=0',), dcdc=False)


if __name__ == '__main__':
    unittest.main()
