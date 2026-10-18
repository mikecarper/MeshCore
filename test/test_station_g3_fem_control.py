"""Run production CLI/FEM paths against GPIOs while TX or sleep owns the mode."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
import test_esp32_gpio_hold as gpio_fixture

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "variants/station_g3_esp32"

FIXTURE = r'''
#include <Arduino.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <helpers/PrefsSaveReplyGuard.h>
#include "LoRaFEMControl.h"
#include "StationG3RadioSlot.h"
struct ESP32Board {
  virtual void onBeforeTransmit() {}
  virtual void onAfterTransmit() {}
};
struct StationG3Board : ESP32Board {
  LoRaFEMControl loRaFEMControl;
  bool setLoRaFemLnaEnabled(bool); bool canControlLoRaFemLna() const;
  bool isLoRaFemLnaEnabled() const;
  bool setLoRaFemPaGainEnabled(bool); bool canControlLoRaFemPaGain() const;
  bool isLoRaFemPaGainEnabled() const;
@BOARD_CALLBACKS@
} board;
@BOARD_METHODS@
struct Radio {
  unsigned calibrations = 0;
  bool isMorseTxActive() const { return false; }
  bool isMorseRxActive() const { return false; }
  void recalibrateNoiseFloor() { ++calibrations; }
};
struct Prefs {
  uint8_t radio_fem_rxgain = 1, radio_fem_txgain = 0, radio_fem_rxgain_override = 0;
};
struct MyMesh {
  Prefs _prefs; Radio radio; Radio* _radio = &radio;
  bool _radio_available = true, save_ok = true;
  unsigned saves = 0;
  bool savePrefs() { ++saves; return save_ok; }
  Radio* getProfileRadio() { return _radio; }
  void recalibrateNoiseFloor() { _radio->recalibrateNoiseFloor(); }
  bool applyAndSaveFemRxGain(bool); bool applyAndSaveFemTxGain(bool);
};
@COMPANION_METHODS@
struct CommonCLI {
  StationG3Board* _board = &board; MyMesh* _callbacks; Prefs* _prefs;
  bool save_ok = true;
  unsigned saves = 0, _prefs_save_failures = 0;
  explicit CommonCLI(MyMesh& mesh) : _callbacks(&mesh), _prefs(&mesh._prefs) {}
  void savePrefs() { ++saves; if (!save_ok) ++_prefs_save_failures; }
  void handleSetCmd(uint32_t, char* command, char* reply) {
    const char* config = command + 4;
@COMMON_BRANCHES@
  }
  void handleCommand(uint32_t sender_timestamp, char* command, char* reply) {
    PrefsSaveReplyGuard save_reply(_prefs_save_failures, reply);
@COMMON_DISPATCH@
    else assert(false && "unexpected non-set command");
  }
};
int main() {
  for (unsigned slot : {1, 2}) for (bool companion : {false, true}) {
    board = StationG3Board();
    for (auto& pad : pads) pad = Pad();
    for (auto& writes : g3_gpio_writes) writes = 0;
    station_g3::slotState().candidate = slot;
    const int lna = station_g3::activePins().lna;
    const int inactive = station_g3::pinsForSlot(slot == 1 ? 2 : 1).lna;
    board.loRaFEMControl.init();
    MyMesh mesh; CommonCLI cli(mesh); char reply[160] = {};
    auto set_rx = [&](bool enabled, bool save_ok = true) {
      mesh.save_ok = cli.save_ok = save_ok;
      bool success;
      if (companion) success = mesh.applyAndSaveFemRxGain(enabled);
      else {
        char command[40];
        strcpy(command, enabled ? "set radio.fem.rxgain on" : "set radio.fem.rxgain off");
        cli.handleCommand(0, command, reply);
        success = !strncmp(reply, "OK", 2);
      }
      assert(success == save_ok);
    };
    auto set_pa = [&](bool enabled, bool save_ok = true) {
      mesh.save_ok = cli.save_ok = save_ok;
      bool success;
      if (companion) success = mesh.applyAndSaveFemTxGain(enabled);
      else {
        char command[40];
        strcpy(command, enabled ? "set radio.fem.txgain on" : "set radio.fem.txgain off");
        cli.handleCommand(0, command, reply);
        success = !strncmp(reply, "OK", 2);
      }
      assert(success == save_ok);
    };
    assert(physical(lna) == HIGH && physical(inactive) == HIGH);
    const unsigned inactive_writes = g3_gpio_writes[inactive];
    board.loRaFEMControl.setRxModeEnable();
    assert(physical(lna) == LOW && physical(inactive) == HIGH);
    set_rx(false); assert(physical(lna) == HIGH);
    set_rx(true); assert(physical(lna) == LOW);
    // The actual board callbacks establish packet TX bypass until retirement.
    board.onBeforeTransmit(); assert(physical(lna) == HIGH);
    set_rx(true);
    assert(physical(lna) == HIGH && "same-value RX preference must retain TX bypass");
    set_rx(false); set_rx(true);
    assert(physical(lna) == HIGH && board.isLoRaFemLnaEnabled());
    board.onAfterTransmit(); assert(physical(lna) == LOW);
    board.onBeforeTransmit(); set_rx(false);
    board.onAfterTransmit(); assert(physical(lna) == HIGH);

    // Companion's actual save-failure helper rolls desired state back; the
    // legacy CommonCLI guard reports uncertainty and leaves its live setting.
    // Neither path may re-enable the LNA during that transaction's TX mode.
    board.onBeforeTransmit(); set_rx(true, false);
    assert(physical(lna) == HIGH && physical(inactive) == HIGH);
    if (companion) {
      assert(!board.isLoRaFemLnaEnabled() && mesh._prefs.radio_fem_rxgain == 0);
    } else {
      assert(board.isLoRaFemLnaEnabled() && mesh._prefs.radio_fem_rxgain == 1);
      assert(strstr(reply, "save unconfirmed; changes may be active"));
    }
    board.onAfterTransmit();
    assert(physical(lna) == (companion ? HIGH : LOW));
    set_rx(true);
    board.onBeforeTransmit(); set_rx(false, false);
    assert(physical(lna) == HIGH);
    if (companion) assert(board.isLoRaFemLnaEnabled() && mesh._prefs.radio_fem_rxgain == 1);
    board.onAfterTransmit(); assert(physical(lna) == (companion ? LOW : HIGH));

    // PA settings still change only at the next TX start, including rollback.
    assert(physical(P_PA1_EN) == LOW);
    board.onBeforeTransmit(); set_pa(true);
    assert(physical(P_PA1_EN) == LOW && board.isLoRaFemPaGainEnabled());
    board.onAfterTransmit(); assert(physical(P_PA1_EN) == LOW);
    board.onBeforeTransmit(); assert(physical(P_PA1_EN) == HIGH);
    set_pa(false, false); assert(physical(P_PA1_EN) == HIGH);
    if (companion) assert(board.isLoRaFemPaGainEnabled() && mesh._prefs.radio_fem_txgain == 1);
    board.onAfterTransmit(); board.onBeforeTransmit();
    assert(physical(P_PA1_EN) == (companion ? HIGH : LOW));
    board.onAfterTransmit();
    assert(g3_gpio_writes[inactive] == inactive_writes);

    // Shutdown overrides both sockets; later setters only cache next-RX gain.
    board.loRaFEMControl.setSleepModeEnable();
    assert(physical(lna) == HIGH && physical(inactive) == HIGH && physical(P_PA1_EN) == LOW);
    const unsigned sleeping_writes = g3_gpio_writes[lna];
    set_rx(false); set_rx(true); set_pa(true);
    assert(physical(lna) == HIGH && physical(inactive) == HIGH && physical(P_PA1_EN) == LOW);
    assert(g3_gpio_writes[lna] == sleeping_writes);
    board.loRaFEMControl.setRxModeEnable();
    assert(physical(lna) == LOW && physical(inactive) == HIGH);
    // A subsequent init also remains non-receiving while adapters publish ON.
    board.loRaFEMControl.init();
    assert(physical(lna) == HIGH && physical(inactive) == HIGH);
    set_rx(true); set_rx(false); set_rx(true);
    assert(physical(lna) == HIGH && physical(inactive) == HIGH);
  }
}
'''


class StationG3FemControlTest(unittest.TestCase):
    def source_fixture(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        board = (VARIANT / "StationG3Board.cpp").read_text()
        board_header = (VARIANT / "StationG3Board.h").read_text()
        dispatch = 'if (memcmp(command, "set ", 4) == 0)'
        self.assertLess(common.index(dispatch), common.index("_board->handleCommand("))
        values = {
            "@COMMON_DISPATCH@": extract_braced(common, dispatch),
            "@COMMON_BRANCHES@": "\nelse ".join(extract_braced(common, signature) for signature in (
                'if (memcmp(config, "radio.fem.rxgain ", 17) == 0)',
                'if (memcmp(config, "radio.fem.txgain ", 17) == 0)')),
            "@COMPANION_METHODS@": "\n".join(extract_braced(companion, signature) for signature in (
                "bool MyMesh::applyAndSaveFemRxGain(", "bool MyMesh::applyAndSaveFemTxGain(")),
            "@BOARD_CALLBACKS@": "\n".join(extract_braced(board_header, signature) for signature in (
                "void onBeforeTransmit()", "void onAfterTransmit()")),
            "@BOARD_METHODS@": "\n".join(extract_braced(board, signature) for signature in (
                "bool StationG3Board::setLoRaFemLnaEnabled(", "bool StationG3Board::canControlLoRaFemLna(",
                "bool StationG3Board::isLoRaFemLnaEnabled(", "bool StationG3Board::setLoRaFemPaGainEnabled(",
                "bool StationG3Board::canControlLoRaFemPaGain(", "bool StationG3Board::isLoRaFemPaGainEnabled(")),
        }
        code = FIXTURE
        for token, value in values.items():
            code = code.replace(token, value)
        return code

    def compile_run(self, *, old_setter=False):
        with tempfile.TemporaryDirectory(prefix="g3-fem-control-") as directory:
            work = Path(directory)
            (work / "driver").mkdir()
            (work / "driver/rtc_io.h").write_text(gpio_fixture.SDK)
            (work / "driver/gpio.h").write_text('#include "rtc_io.h"\n')
            (work / "Arduino.h").write_text(
                '#pragma once\n#include <driver/gpio.h>\n'
                'inline unsigned g3_gpio_writes[64] = {};\n'
                'inline void digitalWrite(int pin, int level) { ++g3_gpio_writes[pin]; gpio_set_level(pin, level); }\n')
            for name in ("LoRaFEMControl.h", "StationG3RadioSlot.h"):
                (work / name).write_text((VARIANT / name).read_text())
            fem = (VARIANT / "LoRaFEMControl.cpp").read_text()
            if old_setter:
                old = fem.replace("if (_mode == Mode::Receive) setRxModeEnable();", "setRxModeEnable();")
                self.assertNotEqual(old, fem)
                fem = old
            (work / "fem.cpp").write_text(fem)
            (work / "test.cpp").write_text(self.source_fixture())
            sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            for macro_lna in (10, 42):
                with self.subTest(compiled_lna=macro_lna, old_setter=old_setter):
                    result = subprocess.run([
                        os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror", *sanitizer,
                        "-DCHIP_S3=1", "-DSOC_RTCIO_INPUT_OUTPUT_SUPPORTED=1", "-DHIGH=1", "-DLOW=0",
                        "-DP_PA1_EN=9", "-DP_PA1_EN_ACTIVE=1", f"-DP_PRIMARY_LNA_EN={macro_lna}",
                        "-DP_PRIMARY_LNA_EN_ACTIVE=0", "-I" + str(work), "-I" + str(ROOT / "src"),
                        str(work / "test.cpp"), str(work / "fem.cpp"), "-o", str(work / "test"),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run([str(work / "test")], capture_output=True, text=True, timeout=15)
                    if old_setter:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("same-value RX preference must retain TX bypass", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_gain_changes_preserve_tx_sleep_and_rollback_modes(self):
        self.compile_run()

    def test_old_immediate_rx_setter_fails_real_gpio_regression(self):
        self.compile_run(old_setter=True)


if __name__ == "__main__":
    unittest.main()
