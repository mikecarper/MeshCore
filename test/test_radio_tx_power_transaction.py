"""Execute real TX-power admission, retry and unchanged-value behavior."""
from pathlib import Path
import unittest

from test_radio_interrupt_recovery import compile_run
from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <helpers/RadioDirectionPolicy.h>
#include <initializer_list>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define RADIOLIB_ERR_NONE 0
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
using Result = mesh::RadioParamApplyResult;
struct Chip {
  int standby_result = 0, power_result = 0, direct_result = 0;
  int8_t power = 17;
  bool write_before_failure = false;
  unsigned standbys = 0, writes = 0, keys = 0;
  int standby() { ++standbys; return standby_result; }
  int setOutputPower(int8_t requested) {
    ++writes;
    if (!power_result || write_before_failure) power = requested;
    return power_result;
  }
  int transmitDirect() { ++keys; return direct_result; }
  int checkOutputPower(int8_t requested, int8_t*) const {
    return requested >= -9 && requested <= 22 ? 0 : -1;
  }
};
struct RadioLibWrapper {
  Chip chip;
  Chip* _radio = &chip;
  uint8_t state = STATE_RX;
  int8_t _cur_dbm = 17;
  float _cur_freq = 915;
  bool _dbm_valid = true, _dbm_applied = true, _profile_repair_required = false;
  bool _profile_refresh_required = false, _rx_hold_continuous = false;
  bool _rx_ps_armed = true, _cw_active = false, _cw_stopping = false;
  bool _beacon_owned = false, _beacon_restoring = false;
  bool _morse_owned = false, _morse_rx_owned = false;
  bool pending = false, busy = false, packet = false;
  unsigned irq_reads = 0, busy_reads = 0, packet_reads = 0, claims = 0;
  unsigned resumes = 0, carrier_stops = 0;
  bool hasPendingRadioInterrupt() { ++irq_reads; return pending; }
  bool claimRadioInterrupt() { ++claims; bool value = pending; pending = false; return value; }
  bool isChipBusy() { ++busy_reads; return busy; }
  bool isReceivingPacket() { ++packet_reads; return packet; }
  int16_t applyCachedTxPower(int8_t requested) { return chip.setOutputPower(requested); }
  bool stopCarrierWave() { ++carrier_stops; _cw_active = false; return true; }
  void startRecv() { ++resumes; state = STATE_RX; }
  uint8_t beginReconfigure();
  void endReconfigure(bool);
  bool setTxPower(int8_t);
  bool isTxPowerValid(int8_t) const;
  bool isTxPowerApplied(int8_t) const;
  virtual bool checkBeaconOutputPower(float, int8_t) const;
  Result trySetTxPower(int8_t);
};
@METHODS@
int main() {
  // Status needs proof of current normal hardware power, without SPI reads.
  {
    RadioLibWrapper radio;
    assert(radio.isTxPowerApplied(17) && !radio.isTxPowerApplied(2));
    radio.pending = radio.busy = radio.packet = true;
    assert(radio.isTxPowerApplied(17));
    radio._dbm_applied = false;
    assert(!radio.isTxPowerApplied(17));
    radio._dbm_applied = true; radio._dbm_valid = false;
    assert(!radio.isTxPowerApplied(17));
    radio._dbm_valid = true; radio._profile_repair_required = true;
    assert(!radio.isTxPowerApplied(17));
    radio._profile_repair_required = false; radio._cw_active = true;
    assert(!radio.isTxPowerApplied(17));
    radio._cw_active = false; radio._beacon_owned = true;
    assert(!radio.isTxPowerApplied(17));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.irq_reads
           && !radio.busy_reads && !radio.packet_reads && !radio.claims);
  }
  {
    RadioLibWrapper radio; radio.pending = radio.busy = radio.packet = true;
    assert(!radio.isTxPowerValid(23) && radio.trySetTxPower(23) == Result::FAILED);
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.irq_reads && !radio.claims);
  }
  // A temporary receive/TX condition does not become a hardware rejection.
  for (unsigned reason = 0; reason < 4; ++reason) {
    RadioLibWrapper radio;
    radio.pending = reason == 0;
    if (reason == 1) radio.state = STATE_TX_WAIT;
    radio.busy = reason == 2;
    radio.packet = reason == 3;
    assert(radio.trySetTxPower(10) == Result::BUSY);
    assert(!radio.setTxPower(10));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.resumes);
    assert(radio._cur_dbm == 17 && radio.chip.power == 17);
    if (reason == 0) assert(radio.pending && !radio.claims);
    radio.pending = radio.busy = radio.packet = false;
    radio.state = STATE_RX;
    assert(radio.trySetTxPower(10) == Result::APPLIED);
    assert(radio.chip.writes == 1 && radio.chip.standbys == 1 && radio.resumes == 1);
    assert(radio._cur_dbm == 10 && radio._dbm_valid && radio._dbm_applied && radio.chip.power == 10);
    assert(radio.isTxPowerApplied(10) && !radio.isTxPowerApplied(17));
  }
  // App saves can resend the current power while an unread packet owns RX.
  for (unsigned reason = 0; reason < 4; ++reason) {
    RadioLibWrapper radio;
    radio.pending = reason == 0;
    if (reason == 1) radio.state = STATE_TX_WAIT;
    radio.busy = reason == 2;
    radio.packet = reason == 3;
    const auto original_state = radio.state;
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.setTxPower(17));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.resumes);
    assert(!radio.irq_reads && !radio.busy_reads && !radio.packet_reads && !radio.claims);
    assert(radio.state == original_state && radio._rx_ps_armed);
    assert(radio.pending == (reason == 0) && radio.packet == (reason == 3));
  }
  // A matching number without a valid hardware cache must still be applied.
  {
    RadioLibWrapper radio; radio._dbm_valid = false; radio.busy = true;
    assert(radio.trySetTxPower(17) == Result::BUSY && !radio.chip.writes);
    radio.busy = false;
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.chip.writes == 1);
  }
  // A failed earlier transaction cannot be hidden by the unchanged shortcut.
  {
    RadioLibWrapper radio; radio._profile_repair_required = true; radio.busy = true;
    assert(radio.trySetTxPower(17) == Result::BUSY && !radio.chip.writes);
    radio.busy = false;
    assert(radio.trySetTxPower(17) == Result::APPLIED);
    assert(radio.chip.standbys == 1 && radio.chip.writes == 1 && radio.resumes == 1);
  }
  // Beacon and Morse owners remain exclusive even for the cached power.
  for (unsigned owner = 0; owner < 4; ++owner) {
    RadioLibWrapper radio;
    radio._beacon_owned = owner == 0;
    radio._cw_active = owner != 0;
    radio._morse_owned = owner == 1;
    radio._morse_rx_owned = owner == 2;
    radio._cw_stopping = owner == 3;
    assert(radio.trySetTxPower(17) == Result::BUSY && !radio.setTxPower(17));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.chip.keys);
    assert(!radio.resumes && !radio.carrier_stops);
  }
#if MESH_ENABLE_CARRIER_TOOLS
  {
    RadioLibWrapper radio; radio._cw_active = true;
    assert(radio.trySetTxPower(17) == Result::APPLIED);
    assert(radio.chip.writes == 1 && radio.chip.keys == 1 && !radio.resumes);
  }
#endif
  // A failed SPI command may already have changed the physical output power.
  // The old cached request is usable again only after a verified hardware apply.
  {
    RadioLibWrapper radio;
    radio.chip.power_result = -705; radio.chip.write_before_failure = true;
    assert(radio.trySetTxPower(10) == Result::FAILED);
    assert(radio._cur_dbm == 17 && radio.chip.power == 10 && !radio._dbm_applied);
    assert(!radio.isTxPowerApplied(17) && !radio.isTxPowerApplied(10));
    radio.busy = true;
    assert(radio.trySetTxPower(17) == Result::BUSY && radio.chip.writes == 1);
    radio.busy = false; radio.chip.power_result = 0;
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.chip.writes == 2);
    assert(radio.chip.power == 17 && radio._dbm_applied);
    assert(radio.isTxPowerApplied(17));
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.chip.writes == 2);
  }
  // Real command and standby failures remain failures, with the requested cache intact.
  {
    RadioLibWrapper radio; radio.chip.power_result = -705;
    assert(radio.trySetTxPower(10) == Result::FAILED && !radio.setTxPower(10));
    assert(radio._cur_dbm == 17 && radio.chip.power == 17);
    assert(!radio._dbm_applied);
    assert(radio.chip.writes == 2 && radio.resumes == 2);
  }
  {
    RadioLibWrapper radio; radio.chip.standby_result = -705;
    assert(radio.trySetTxPower(10) == Result::FAILED && !radio.setTxPower(10));
    assert(!radio.chip.writes && radio._profile_repair_required && !radio.resumes);
  }
}
'''


class RadioTxPowerTransactionTests(unittest.TestCase):
    def test_pure_family_and_board_limits_preserve_band_clipping(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        validator = method(source, "bool RadioLibWrapper::isTxPowerValid(")
        checker = method(source, "bool RadioLibWrapper::checkBeaconOutputPower(")
        dac = method((ROOT / "src/helpers/radiolib/DacPaSX1276Wrapper.h").read_text(),
                     "bool checkBeaconOutputPower(")
        code = r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/LR2021Band.h>
#define RADIOLIB_ERR_NONE 0
struct Chip {
  mutable unsigned checks = 0;
  int checkOutputPower(int8_t power, int8_t*, bool rfo = false) const {
    ++checks;
#if MESH_RADIO_PROFILE_CHIP == 1276 || MESH_RADIO_PROFILE_CHIP == 1272
    return rfo ? (power >= -4 && power <= 15 ? 0 : -1)
               : ((power >= 2 && power <= 17) || power == 20 ? 0 : -1);
#else
    return power >= -9 && power <= 22 ? 0 : -1;
#endif
  }
};
using SX1276 = Chip;
using SX1272 = Chip;
struct RadioLibWrapper {
  Chip chip;
  Chip* _radio = &chip;
  float _cur_freq = 2440;
  virtual bool checkBeaconOutputPower(float, int8_t) const;
  bool isTxPowerValid(int8_t) const;
};
@CHECKER@
@VALIDATOR@
struct DacWrapper : RadioLibWrapper {
  int8_t _min_dbm = 17, _max_dbm = 30;
  @DAC@
};
int main() {
  RadioLibWrapper radio;
  DacWrapper amplified;
  for (int power = -128; power <= 127; ++power) {
    bool board_ok = true;
#ifdef MIN_LORA_TX_POWER
    board_ok = board_ok && power >= MIN_LORA_TX_POWER;
#endif
#ifdef MAX_LORA_TX_POWER
    board_ok = board_ok && power <= MAX_LORA_TX_POWER;
#endif
#if MESH_RADIO_PROFILE_CHIP == 1121 || MESH_RADIO_PROFILE_CHIP == 2021
    // Ordinary desired power remains valid on a lower-power temporary band.
    const bool chip_ok = true;
#elif MESH_RADIO_PROFILE_CHIP == 1276 || MESH_RADIO_PROFILE_CHIP == 1272
    const bool chip_ok = (power >= -4 && power <= 17) || power == 20;
#else
    const bool chip_ok = power >= -9 && power <= 22;
#endif
    assert(radio.isTxPowerValid(int8_t(power)) == (board_ok && chip_ok));
    assert(amplified.isTxPowerValid(int8_t(power)) ==
#if MESH_RADIO_PROFILE_CHIP == 1121 || MESH_RADIO_PROFILE_CHIP == 2021
           board_ok);
#else
           (board_ok && power >= 17 && power <= 30));
#endif
  }
#if MESH_RADIO_PROFILE_CHIP == 1121 || MESH_RADIO_PROFILE_CHIP == 2021
  assert(radio.chip.checks == 0);
#endif
  assert(amplified.chip.checks == 0);
}
'''
        code = code.replace("@CHECKER@", checker).replace("@VALIDATOR@", validator).replace("@DAC@", dac)
        for family in (1262, 1268, 68, 1276, 1272, 1110, 1121, 2021):
            for limits in ((), ("-DMIN_LORA_TX_POWER=17", "-DMAX_LORA_TX_POWER=30")):
                with self.subTest(family=family, limits=limits):
                    compile_run(code, defines=(f"-DMESH_RADIO_PROFILE_CHIP={family}", *limits))

    def test_actual_busy_retry_noop_ownership_and_failures(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        methods = "\n".join(method(source, signature) for signature in (
            "uint8_t RadioLibWrapper::beginReconfigure(",
            "void RadioLibWrapper::endReconfigure(",
            "bool RadioLibWrapper::setTxPower(",
            "bool RadioLibWrapper::isTxPowerValid(",
            "bool RadioLibWrapper::isTxPowerApplied(",
            "bool RadioLibWrapper::checkBeaconOutputPower(",
            "mesh::RadioParamApplyResult RadioLibWrapper::trySetTxPower("))
        for carrier_tools in (0, 1):
            with self.subTest(carrier_tools=carrier_tools):
                compile_run(HARNESS.replace("@METHODS@", methods), defines=(
                    "-DMESH_ENABLE_TX_BEACONS=1", "-DMESH_ENABLE_MORSE_RX=1",
                    f"-DMESH_ENABLE_CARRIER_TOOLS={carrier_tools}"))


if __name__ == "__main__":
    unittest.main()
