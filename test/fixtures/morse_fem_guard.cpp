#include <cassert>
#include <helpers/CLICommandUtils.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

struct Radio {
  bool morse = false, morse_rx = false, agc_pending = false;
  unsigned calibrations = 0;
  bool isMorseTxActive() const { return morse; }
  bool isMorseRxActive() const { return morse_rx; }
  bool isAGCMaintenancePending() const { return agc_pending; }
  void recalibrateNoiseFloor() { ++calibrations; }
};
struct Board {
  bool rx = true, tx = false, supported = true, apply_ok = true;
  unsigned rx_calls = 0, tx_calls = 0;
  bool canControlLoRaFemLna() const { return supported; }
  bool canControlLoRaFemPaGain() const { return supported; }
  bool isLoRaFemLnaEnabled() const { return rx; }
  bool isLoRaFemPaGainEnabled() const { return tx; }
  bool setLoRaFemLnaEnabled(bool value) { ++rx_calls; if (apply_ok) rx = value; return apply_ok; }
  bool setLoRaFemPaGainEnabled(bool value) { ++tx_calls; if (apply_ok) tx = value; return apply_ok; }
} board;
struct Prefs {
  uint8_t radio_fem_rxgain = 1, radio_fem_rxgain_override = 0, radio_fem_txgain = 0;
};
struct MyMesh {
  Prefs _prefs;
  Radio radio;
  Radio* _radio = &radio;
  bool _radio_available = true, save_ok = true;
  unsigned saves = 0;
  MyMesh() { board = Board(); }
  bool savePrefs() { ++saves; return save_ok; }
  bool applyAndSaveFemRxGain(bool);
  bool applyAndSaveFemTxGain(bool);
  Radio* getProfileRadio() const { return _radio; }
  void recalibrateNoiseFloor() { if (_radio) _radio->recalibrateNoiseFloor(); }
};
@COMPANION_METHODS@

struct CommonCLI {
  MyMesh* _callbacks;
  Board* _board = &board;
  Prefs* _prefs;
  unsigned saves = 0;
  explicit CommonCLI(MyMesh& owner) : _callbacks(&owner), _prefs(&owner._prefs) {}
  void savePrefs() { ++saves; }
  void handleSetCmd(const char* config, char* reply) {
    @COMMON_BRANCHES@
  }
};

int main() {
  {
    MyMesh owner; owner.radio.agc_pending = true;
    assert(!owner.applyAndSaveFemRxGain(false));
    assert(board.rx && !board.rx_calls && !owner.saves && !owner.radio.calibrations);
    owner.radio.agc_pending = false;
    assert(owner.applyAndSaveFemRxGain(false));
    assert(!board.rx && owner.saves == 1 && owner.radio.calibrations == 1);
  }
  for (bool receiving : {false, true}) {
   for (bool requested : {false, true}) {
    MyMesh owner; owner.radio.morse = !receiving; owner.radio.morse_rx = receiving;
    assert(!owner.applyAndSaveFemRxGain(requested));
    assert(!owner.applyAndSaveFemTxGain(requested));
    assert(!board.rx_calls && !board.tx_calls && !owner.saves && !owner.radio.calibrations);
    assert(owner._prefs.radio_fem_rxgain == 1 && owner._prefs.radio_fem_rxgain_override == 0);
    assert(owner._prefs.radio_fem_txgain == 0 && board.rx && !board.tx);
    CommonCLI cli(owner); char reply[180];
    for (const char* command : {"radio.fem.rxgain on", "radio.fem.rxgain off",
        "radio.fem.txgain on", "radio.fem.txgain off"}) {
      cli.handleSetCmd(command, reply);
      assert(!strncmp(reply, "Error: stop Morse", 17));
      assert(!board.rx_calls && !board.tx_calls && !owner.saves && !cli.saves);
      assert(owner._prefs.radio_fem_rxgain == 1 && owner._prefs.radio_fem_rxgain_override == 0);
      assert(owner._prefs.radio_fem_txgain == 0 && board.rx && !board.tx);
    }
   }
  }
  {
    MyMesh owner;
    assert(owner.applyAndSaveFemRxGain(false) && owner.applyAndSaveFemTxGain(true));
    assert(!board.rx && board.tx && owner._prefs.radio_fem_rxgain == 0);
    assert(owner._prefs.radio_fem_rxgain_override == 1 && owner._prefs.radio_fem_txgain == 1);
    assert(owner.saves == 2 && owner.radio.calibrations == 1);
  }
  {
    MyMesh owner; owner.save_ok = false;
    assert(!owner.applyAndSaveFemRxGain(false) && !owner.applyAndSaveFemTxGain(true));
    assert(board.rx && !board.tx && owner._prefs.radio_fem_rxgain == 1);
    assert(owner._prefs.radio_fem_rxgain_override == 0 && owner._prefs.radio_fem_txgain == 0);
    assert(owner.saves == 2 && owner.radio.calibrations == 2);
  }
  {
    MyMesh owner; CommonCLI cli(owner); char reply[180];
    cli.handleSetCmd("radio.fem.rxgain off", reply);
    assert(!strncmp(reply, "OK - ", 5) && !board.rx && owner._prefs.radio_fem_rxgain == 0);
    cli.handleSetCmd("radio.fem.txgain on", reply);
    assert(!strncmp(reply, "OK - ", 5) && board.tx && owner._prefs.radio_fem_txgain == 1);
    assert(cli.saves == 2 && owner.radio.calibrations == 1);
  }
}
