"""Execute merged LR1121 band/power and Morse PA selection without hardware."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <algorithm>
constexpr int16_t RADIOLIB_ERR_NONE=0;
constexpr int16_t RADIOLIB_ERR_INVALID_FREQUENCY=-705;
constexpr int8_t LORA_TX_POWER=22;
class LR1121 {
 public:
  float frequency=909.5f;
  int power_calls=0,frequency_calls=0,checks=0,overload_calls=0;
  int16_t power_result=0,frequency_result=0;
  int8_t applied_power=0;
  bool forced_hp=false;
  uint32_t applied_ramp=0;
  virtual ~LR1121()=default;
  virtual int16_t setOutputPower(int8_t power) { return setOutputPower(power,false); }
  int16_t setOutputPower(int8_t power,bool hp,uint32_t ramp=48) {
    ++power_calls;applied_power=power;forced_hp=hp;applied_ramp=ramp;return power_result;
  }
  int16_t checkOutputPower(int8_t power,int8_t* clipped,bool hp) {
    ++checks;
    const int minimum=frequency>1000 ? -18 : hp || power>14 ? -9 : -17;
    const int maximum=frequency>1000 ? 13 : hp || power>14 ? 22 : 14;
    if(clipped) *clipped=std::max(minimum,std::min(maximum,int(power)));
    return power<minimum || power>maximum ? -1 : 0;
  }
  virtual int16_t setFrequency(float value) {
    ++frequency_calls;if(frequency_result==0)frequency=value;return frequency_result;
  }
  int16_t setFrequency(float value,bool,float=4) {
    ++overload_calls;return LR1121::setFrequency(value);
  }
};
class CustomLR1121:public LR1121 {
  int8_t _txPower=0;
  bool _txPowerSet=false;
 public:
  using LR1121::setOutputPower;
  using LR1121::setFrequency;
  @POWER@
  @FREQUENCY@
};
struct Board {
  bool frequency_ok=true;
  bool prepareRadioFrequency(float) { return frequency_ok; }
};
struct CarrierBase {
  virtual ~CarrierBase()=default;
  virtual int16_t prepareMorseCarrier(float)=0;
  virtual bool restoreCarrierWaveModem()=0;
};
class MorseWrapper:public CarrierBase {
 public:
  LR1121* _radio;
  Board* _board;
  bool _dbm_valid=true,_morse_rx_fsk_mode=false;
  int8_t _cur_dbm=22;
  float _cur_freq=909.5f;
  MorseWrapper(LR1121* radio,Board* board):_radio(radio),_board(board) {}
  bool restoreAfterDeepInit() { return true; }
  @CARRIER@
  @RESTORE@
};
int main() {
#ifdef LR1121_FORCE_HP_PA
  const bool expected_hp=true;
#else
  const bool expected_hp=false;
#endif
  CustomLR1121 radio;
  assert(radio.setFrequency(909.5f)==0 && radio.power_calls==0);
  assert(radio.setOutputPower(22)==0 && radio.applied_power==22);
  assert(radio.forced_hp==expected_hp);
  assert(radio.setFrequency(2400.0f)==0 && radio.applied_power==13);
  assert(radio.setFrequency(909.5f)==0 && radio.applied_power==22);
  // A failed explicit power write must not replace the last successful intent.
  radio.power_result=-77;
  assert(radio.setOutputPower(7)==-77);
  radio.power_result=0;
  assert(radio.setFrequency(2400.0f)==0 && radio.applied_power==13);
  assert(radio.setFrequency(909.5f)==0 && radio.applied_power==22);
  // A failed tune must not touch the PA. A failed PA reapply must propagate.
  const int calls=radio.power_calls;
  radio.frequency_result=-78;
  assert(radio.setFrequency(2400.0f)==-78 && radio.power_calls==calls);
  radio.frequency_result=0;radio.power_result=-79;
  assert(radio.setFrequency(2400.0f)==-79);
  radio.power_result=0;
  assert(radio.setFrequency(909.5f)==0 && radio.applied_power==22);
  assert(radio.setOutputPower(2)==0 && radio.forced_hp==expected_hp);
  // The existing explicit calibration/ramp overloads used by Morse stay visible.
  assert(radio.setFrequency(902.1f,true)==0 && radio.overload_calls==1);
  assert(radio.setOutputPower(2,false,304)==0);
  Board board;
  MorseWrapper morse(&radio,&board);
  morse._cur_dbm=2;
  assert(morse.prepareMorseCarrier(909.5f)==0);
  assert(radio.applied_power==2 && radio.forced_hp==expected_hp && radio.applied_ramp==304);
  morse._cur_dbm=22;
  assert(morse.prepareMorseCarrier(2400.0f)==0);
  assert(radio.applied_power==13 && radio.applied_ramp==304);
  assert(morse.restoreCarrierWaveModem());
  assert(radio.frequency==909.5f && radio.applied_power==22 && radio.forced_hp==expected_hp);
  morse._dbm_valid=false;
  assert(morse.prepareMorseCarrier(2400.0f)==0 && radio.applied_power==13);
  assert(morse.restoreCarrierWaveModem() && radio.applied_power==22);
  board.frequency_ok=false;
  const int tunes=radio.frequency_calls;
  assert(morse.prepareMorseCarrier(2400.0f)==RADIOLIB_ERR_INVALID_FREQUENCY);
  assert(radio.frequency_calls==tunes);
  board.frequency_ok=true;radio.frequency_result=-80;
  assert(morse.prepareMorseCarrier(2400.0f)==-80);
  assert(!morse.restoreCarrierWaveModem());
  radio.frequency_result=0;radio.power_result=-81;
  assert(morse.prepareMorseCarrier(2400.0f)==-81);
}
'''


class UpstreamLR1121Test(unittest.TestCase):
    def compile(self, *flags):
        wrapper = (ROOT / "src/helpers/radiolib/CustomLR1121.h").read_text()
        source = HARNESS.replace("@POWER@", method(wrapper, "int16_t setOutputPower(int8_t power)"))
        source = source.replace("@FREQUENCY@", method(wrapper, "int16_t setFrequency(float freq)"))
        carrier = (ROOT / "src/helpers/radiolib/CustomLR1121Wrapper.h").read_text()
        source = source.replace("@CARRIER@", method(carrier, "int16_t prepareMorseCarrier("))
        source = source.replace("@RESTORE@", method(carrier, "bool restoreCarrierWaveModem("))
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="upstream-lr1121-") as directory:
            folder = Path(directory)
            source_file = folder / "test.cpp"
            source_file.write_text(source)
            binary = folder / ("test.exe" if os.name == "nt" else "test")
            sanitizers = [] if os.name == "nt" else [
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            built = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", *sanitizers,
                *flags, str(source_file), "-o", str(binary)
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_band_changes_restore_original_power_and_propagate_failures(self):
        self.compile()

    def test_hp_only_boards_keep_force_high_power(self):
        self.compile("-DLR1121_FORCE_HP_PA=1")

    def test_merge_preserves_morse_switch_and_bounded_companion_ui(self):
        radio = (ROOT / "src/helpers/radiolib/CustomLR1121.h").read_text()
        self.assertIn("LR11x0MorseRfSwitch _morse_rf_switch;", radio)
        self.assertIn("_morse_rf_switch.capture", method(radio, "void setRfSwitchTable("))
        self.assertIn("_morse_rf_switch.restore", method(radio, "int16_t beginMorseReceive("))
        self.assertIn("using LR1121::setOutputPower;", radio)
        self.assertIn("using LR1121::setFrequency;", radio)
        ui = (ROOT / "examples/companion_radio/ui-new/UITask.cpp").read_text()
        self.assertIn("makeLargeCompanionHomeLayout", ui)
        self.assertIn("drawTextCenteredEllipsized", ui)
        self.assertIn("isCompanionWiFiConnected()", ui)
        self.assertIn("shouldDisplayBluetoothPairingPin", ui)
        self.assertNotIn('display.drawTextCentered(display.width() / 2, 32, tmp)', ui)


if __name__ == "__main__":
    unittest.main()
