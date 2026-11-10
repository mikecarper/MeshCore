"""Exercise the T-Beam 1W's boot, recovery and runtime RXEN table selection."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/lilygo_tbeam_1w"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#define LOW 0
#define HIGH 1
#define RADIOLIB_NC UINT32_MAX
#define RADIOLIB_ERR_NONE 0
#define SX126X_RXEN 21
#ifdef TEST_WITH_TXEN
#define SX126X_TXEN 22
#endif
#define TBEAM_1W 1
#define LORA_TX_POWER 22
#define SX126X_PA_RAMP_TIME 6
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define P_LORA_SCLK 13
#define P_LORA_MISO 12
#define P_LORA_MOSI 11
#define END_OF_MODE_TABLE { Module::MODE_END, {} }
#ifdef SX126X_TXEN
static constexpr uint32_t tx_pin = SX126X_TXEN;
#else
static constexpr uint32_t tx_pin = RADIOLIB_NC;
#endif
struct Module {
  enum { MODE_IDLE, MODE_RX, MODE_TX, MODE_END };
  static constexpr unsigned RFSWITCH_MAX_PINS = 5;
  struct RfSwitchMode_t { unsigned mode; uint32_t values[RFSWITCH_MAX_PINS]; };
  const RfSwitchMode_t* table = nullptr;
  uint32_t pins[RFSWITCH_MAX_PINS] = {};
  unsigned mode = MODE_IDLE;
  unsigned receives = 0;
  void setRfSwitchTable(const uint32_t* next_pins, const RfSwitchMode_t* next) {
    for (unsigned i = 0; i < RFSWITCH_MAX_PINS; ++i) pins[i] = next_pins[i];
    table = next;
  }
  void setRfSwitchState(unsigned next) { mode = next; receives += next == MODE_RX; }
  uint32_t level(unsigned pin) const {
    assert(table);
    for (unsigned i = 0; table[i].mode != MODE_END; ++i) {
      if (table[i].mode != mode) continue;
      for (unsigned j = 0; j < RFSWITCH_MAX_PINS; ++j)
        if (pins[j] == pin) return table[i].values[j];
    }
    assert(false && "mode/pin absent from RF switch table"); return 99;
  }
};
struct RawRadio {
  Module storage;
  Module* mod = &storage;
  void setRfSwitchPins(uint32_t rx, uint32_t tx) {
    static const Module::RfSwitchMode_t standard[] = {
      { Module::MODE_IDLE, { LOW, LOW } },
      { Module::MODE_RX, { HIGH, LOW } },
      { Module::MODE_TX, { LOW, HIGH } }, END_OF_MODE_TABLE,
    };
    const uint32_t pins[5] = { rx, tx, RADIOLIB_NC, RADIOLIB_NC, RADIOLIB_NC };
    mod->setRfSwitchTable(pins, standard);
  }
  @RAW_TABLE_METHOD@
};
struct SPIClass {
  unsigned starts = 0;
  void begin(int, int, int) { ++starts; }
} spi;
// Fault injection wraps the real table selector; boot/recovery retain the
// production call chain while independent checks exercise every table mode.
struct CustomSX1262 : RawRadio {
  bool fail_init = false, fail_table = false;
  int16_t ramp_status = 0;
  unsigned initializes = 0, table_calls = 0, ramp_calls = 0;
  bool std_init(SPIClass* = nullptr) {
    ++initializes;
    mod->setRfSwitchState(Module::MODE_IDLE);
    if (fail_init) return false;
    setRfSwitchPins(SX126X_RXEN, tx_pin);
    return true;
  }
  bool setExternalRxLnaEnabled(bool enabled) {
    ++table_calls;
    return !fail_table && RawRadio::setExternalRxLnaEnabled(enabled);
  }
  int16_t setTxParams(int power, uint8_t ramp) {
    assert(power == LORA_TX_POWER && ramp == SX126X_PA_RAMP_TIME);
    ++ramp_calls; return ramp_status;
  }
} radio;
struct CustomSX1262Wrapper;
struct TBeam1WBoard {
  @BOARD_FIELDS@
  unsigned power_cycles = 0;
  unsigned finishes = 0;
  bool finish_ok = true;
  void powerCycleRadio() { ++power_cycles; }
  bool finishRadioHardReset() { ++finishes; return finish_ok; }
  void attachRadioDriver(CustomSX1262Wrapper*);
  bool setLoRaFemLnaEnabled(bool);
  bool canControlLoRaFemLna() const;
  bool isLoRaFemLnaEnabled() const;
} board;
struct DriverBase { virtual bool radioDeepInit() { return false; } };
struct CustomSX1262Wrapper : DriverBase {
  CustomSX1262* _radio = &radio;
  TBeam1WBoard* _board = &board;
  uint8_t reconfigure = 0;
  bool prepare_ok = true;
  unsigned prepares = 0, runtime_calls = 0;
  uint8_t beginReconfigure() { ++runtime_calls; return reconfigure; }
  void endReconfigure(uint8_t resume) {
    if (resume) _radio->mod->setRfSwitchState(Module::MODE_RX);
  }
  bool prepareRadioHardReset() { ++prepares; return prepare_ok; }
  @RUNTIME_TABLE_METHOD@
  @DEEP_INIT_METHOD@
} radio_driver;
@BOARD_METHODS@
struct Clock { unsigned starts = 0; void begin() { ++starts; } } fallback_clock;
int Wire;
struct RTC { unsigned starts = 0; void begin(int&) { ++starts; } } rtc_clock;
static bool target_peripherals_initialized = false, target_radio_available = false;
@RADIO_INIT@

static void expectRx(bool enabled) {
  radio.mod->setRfSwitchState(Module::MODE_RX);
  assert(radio.mod->level(SX126X_RXEN) == (enabled ? HIGH : LOW));
#ifdef SX126X_TXEN
  assert(radio.mod->level(SX126X_TXEN) == LOW);
#endif
  // Disabling RX gain must never change PA/TX or idle pin levels.
  radio.mod->setRfSwitchState(Module::MODE_TX);
  assert(radio.mod->level(SX126X_RXEN) == LOW);
#ifdef SX126X_TXEN
  assert(radio.mod->level(SX126X_TXEN) == HIGH);
#endif
  radio.mod->setRfSwitchState(Module::MODE_IDLE);
  assert(radio.mod->level(SX126X_RXEN) == LOW);
#ifdef SX126X_TXEN
  assert(radio.mod->level(SX126X_TXEN) == LOW);
#endif
}
int main() {
  assert(!board.isLoRaFemLnaEnabled());
  assert(radio_init());
  assert(target_radio_available && radio.mod->receives == 0);
  assert(radio.table_calls == 1 && radio.ramp_calls == 1);
  assert(spi.starts == 1 && fallback_clock.starts == 1 && rtc_clock.starts == 1);
  expectRx(false);
  // Reapplying the boot default is already synchronized and needs no retune.
  assert(board.setLoRaFemLnaEnabled(false));
  assert(radio_driver.runtime_calls == 0 && radio.table_calls == 1);
  // A saved ON preference is applied through the unchanged runtime setter.
  assert(board.setLoRaFemLnaEnabled(true));
  assert(board.isLoRaFemLnaEnabled()); expectRx(true);
  assert(board.setLoRaFemLnaEnabled(true));
  assert(radio_driver.runtime_calls == 1);
  radio_driver.reconfigure = 2;
  assert(!board.setLoRaFemLnaEnabled(false));
  assert(board.isLoRaFemLnaEnabled()); expectRx(true);
  radio_driver.reconfigure = 1;
  assert(board.setLoRaFemLnaEnabled(false));
  assert(!board.isLoRaFemLnaEnabled()); expectRx(false);
  // Successful and failed full resets retain either requested setting.
  for (bool enabled : {false, true}) {
    radio_driver.reconfigure = 0;
    assert(board.setLoRaFemLnaEnabled(enabled));
    assert(radio_driver.radioDeepInit()); expectRx(enabled);
    const unsigned completed = board.finishes;
    radio.fail_table = true;
    const unsigned receives = radio.mod->receives;
    assert(!radio_driver.radioDeepInit());
    assert(board.finishes == completed && radio.mod->receives == receives);
    assert(board.isLoRaFemLnaEnabled() == enabled);
    radio.fail_table = false;
    assert(radio_driver.radioDeepInit()); expectRx(enabled);
  }
  // A later failed table selection detaches the previously working driver.
  radio.fail_table = true;
  unsigned receives = radio.mod->receives;
  assert(!radio_init() && !target_radio_available);
  const unsigned runtime_before_failure = radio_driver.runtime_calls;
  const unsigned table_before_preference = radio.table_calls;
  assert(board.setLoRaFemLnaEnabled(false));
  assert(!board.isLoRaFemLnaEnabled());
  assert(radio_driver.runtime_calls == runtime_before_failure);
  assert(radio.table_calls == table_before_preference && radio.mod->receives == receives);
  radio.fail_table = false;
  assert(radio_init()); expectRx(false);
  // An unavailable radio can receive an explicit preference before probing.
  board.attachRadioDriver(nullptr);
  assert(board.setLoRaFemLnaEnabled(false));
  target_radio_available = false;
  radio.fail_init = true;
  receives = radio.mod->receives;
  assert(!radio_init() && !target_radio_available);
  assert(radio.mod->receives == receives);
  radio.fail_init = false; radio.fail_table = true;
  const unsigned ramps = radio.ramp_calls;
  assert(!radio_init() && !target_radio_available);
  assert(radio.ramp_calls == ramps && radio.mod->receives == receives);
  // A failed probe must not mark the uninstalled table synchronized.
  assert(board.setLoRaFemLnaEnabled(true));
  radio.fail_table = false;
  assert(radio_init()); expectRx(true);
  assert(board.power_cycles == 4 && spi.starts == 1);
  // Repeat initial/recovery setup with OFF, not merely the fresh boot path.
  board.attachRadioDriver(nullptr);
  assert(board.setLoRaFemLnaEnabled(false));
  target_radio_available = false;
  assert(radio_init()); expectRx(false);
  const unsigned calls = radio.table_calls;
  assert(board.setLoRaFemLnaEnabled(false));
  assert(radio.table_calls == calls);
  radio.ramp_status = -1;
  receives = radio.mod->receives;
  assert(!radio_init() && !target_radio_available);
  assert(radio.mod->receives == receives);
  const unsigned runtime_before_ramp_failure = radio_driver.runtime_calls;
  assert(board.setLoRaFemLnaEnabled(true));
  assert(radio_driver.runtime_calls == runtime_before_ramp_failure);
  radio.ramp_status = 0;
  assert(radio_init()); expectRx(true);
}
'''


class TBeam1WFemTests(unittest.TestCase):
    def test_boot_runtime_and_recovery_keep_requested_rxen_table(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        source = HARNESS
        header = (VARIANT / "TBeam1WBoard.h").read_text()
        fields = []
        for field in ("lna_enabled", "lna_driver_synced", "radio_driver"):
            match = re.search(r"^\s*(?:bool|CustomSX1262Wrapper\*)\s+" + field
                              + r"\s*=\s*[^;]+;", header, re.MULTILINE)
            self.assertIsNotNone(match, field)
            fields.append(match.group().strip())
        board_source = (VARIANT / "TBeam1WBoard.cpp").read_text()
        wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
        raw = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
        replacements = {
            "@BOARD_FIELDS@": "\n".join(fields),
            "@BOARD_METHODS@": "\n".join(body(board_source, signature) for signature in (
                "void TBeam1WBoard::attachRadioDriver(",
                "bool TBeam1WBoard::setLoRaFemLnaEnabled(",
                "bool TBeam1WBoard::canControlLoRaFemLna(",
                "bool TBeam1WBoard::isLoRaFemLnaEnabled(")),
            "@RAW_TABLE_METHOD@": body(raw, "bool setExternalRxLnaEnabled("),
            "@RUNTIME_TABLE_METHOD@": body(wrapper, "bool setExternalRxLnaEnabled("),
            "@DEEP_INIT_METHOD@": body(wrapper, "bool radioDeepInit("),
            "@RADIO_INIT@": body((VARIANT / "target.cpp").read_text(), "bool radio_init("),
        }
        for marker, value in replacements.items():
            source = source.replace(marker, value)
        with tempfile.TemporaryDirectory(prefix="meshcore-tbeam1w-fem-") as temporary:
            directory = Path(temporary)
            cpp, binary = directory / "test.cpp", directory / "test"
            cpp.write_text(source)
            for with_txen in (False, True):
                with self.subTest(with_txen=with_txen):
                    definitions = ["-DTEST_WITH_TXEN=1"] if with_txen else []
                    built = subprocess.run([compiler, "-std=c++11", "-Os", "-Wall", "-Wextra",
                                            "-Werror", *definitions,
                                            str(cpp), "-o", str(binary)],
                                           capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
