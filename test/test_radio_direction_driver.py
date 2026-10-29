"""Exercise real radio direction, receive cleanup and CAD paths without RF."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import ROOT, method, transition_harness
from test_carrier_wave import production_harness
import test_beacon_radio


class RadioDirectionDriverTests(unittest.TestCase):
    def test_beacon_direction_gate_and_standby_restoration(self):
        code = test_beacon_radio.BeaconRadioTest().source().replace(
            "int main()", "int legacy_main()", 1)
        end = code.rfind("}")
        code = code[:end] + "return 0;\n" + code[end:]
        code = r'''
#define MESH_ENABLE_RADIO_DIRECTION 1
#include <helpers/RadioDirectionPolicy.h>
namespace mesh { namespace radioDirection {
static Mode current = Mode::Both;
Mode mode() { return current; }
}}
''' + code
        code = code.replace("    uint8_t state = STATE_RX, _active_profile = 0;",
            "    bool _direction_receive_disabled = false;\n"
            "    uint8_t state = STATE_RX, _active_profile = 0;")
        code += r'''
int main() {
  using namespace mesh::radioDirection;
  assert(legacy_main() == 0);
  current = Mode::Rx;
  {
    RadioLibWrapper w;
    assert(w.prepareBeaconTransmit(beacon(), 10) == Result::FAILED);
    assert(!w._beacon_owned && !w.applications && !w.power_sets && !w.board.before);
  }
  current = Mode::Tx;
  {
    RadioLibWrapper w;
    assert(w.prepareBeaconTransmit(beacon(), 10) == Result::APPLIED);
    assert(w._beacon_owned && w.state == STATE_IDLE && w.arms == 0);
    assert(!w.isBeaconChannelBusy() && w.arms == 0);
    assert(w.startSendRaw(nullptr, 0) && w.state == STATE_TX_WAIT);
    w.onSendFinished();
    assert(w.restoreBeaconReceive() == Result::APPLIED);
    assert(!w._beacon_owned && w.state == STATE_IDLE && w.arms == 0);
    assert(w.modem.freq == 906 && w.modem.power == 20 && w.isTxPowerApplied(20));
  }
  current = Mode::Both;
  {
    RadioLibWrapper w;
    assert(w.prepareBeaconTransmit(beacon(), 10) == Result::APPLIED);
    current = Mode::Off;
    assert(!w.startSendRaw(nullptr, 0));
    assert(w.restoreBeaconReceive() == Result::APPLIED);
    assert(!w._beacon_owned && w.state == STATE_IDLE && w.arms == 0);
  }
}
'''
        test_beacon_radio.BeaconRadioTest().run_harness(code)

    def test_carrier_and_morse_direction_gates_and_owned_cleanup(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        code = production_harness().replace("int main()", "int legacy_main()", 1)
        end = code.rfind("}")
        code = code[:end] + "return 0;\n" + code[end:]
        code = r'''
#define MESH_ENABLE_RADIO_DIRECTION 1
#define MESH_ENABLE_TX_BEACONS 0
#include <helpers/RadioDirectionPolicy.h>
namespace mesh { namespace radioDirection {
static Mode current = Mode::Both;
Mode mode() { return current; }
}}
''' + code
        code = code.replace("  uint8_t state = STATE_RX;", r'''
  uint8_t state = STATE_RX;
  bool _direction_receive_disabled = false, _rx_retry_pending = false;
  bool _nf_refresh_requested = false, _nf_window_armed = false;
  uint8_t _irq_probe_failures = 0;
  uint16_t _rx_retry_delay_ms = 0;
  void serviceDirectionPolicy();
''')
        code = code.replace("  void startRecv() override {", r'''
  void startRecv() override {
    if (!mesh::radioDirection::radioReceiveAllowed()) return;
''')
        code += method(source, "void RadioLibWrapper::serviceDirectionPolicy(")
        code += r'''
int main() {
  using namespace mesh::radioDirection;
  using Result = mesh::RadioParamApplyResult;
  assert(legacy_main() == 0);
  current = Mode::Rx;
  {
    RadioLibWrapper w;
    assert(w.setCarrierWave(0, 1000) == Result::FAILED);
    assert(w.startMorseTx(902.1, 12, "E") == mesh::MorseTxResult::Failed);
    assert(w.chip.starts == 0 && w.board.before == 0 && !w._cw_active);
  }
  current = Mode::Tx;
  {
    RadioLibWrapper w;
    assert(w.startMorseRx(902.1, 25, 1000) == mesh::MorseRxResult::Failed);
    assert(w.chip.raw_rx_starts == 0 && !w._cw_active);
    assert(w.setCarrierWave(0, 1000) == Result::APPLIED);
    assert(w.chip.carrier);
    const auto receivers = w.rx_starts;
    assert(w.setCarrierWave(0, 0) == Result::APPLIED);
    assert(!w.chip.carrier && !w._cw_active && w.state == STATE_IDLE);
    assert(w.rx_starts == receivers);
  }
  current = Mode::Both;
  {
    RadioLibWrapper w;
    assert(w.setCarrierWave(0, 1000) == Result::APPLIED);
    current = Mode::Rx;
    w.serviceDirectionPolicy();
    assert(!w.chip.carrier && !w._cw_active && w.state == STATE_RX);
    assert(!w.board.awake && w.board.after == 1);
  }
  current = Mode::Both;
  {
    RadioLibWrapper w;
    assert(w.startMorseTx(902.1, 12, "E") == mesh::MorseTxResult::Started);
    current = Mode::Rx;
    w.serviceDirectionPolicy();
    assert(!w.chip.carrier && !w._cw_active && !w._morse_owned);
    assert(w.state == STATE_RX && !w.board.awake);
  }
  current = Mode::Both;
  {
    RadioLibWrapper w;
    assert(w.startMorseRx(902.1, 25, 1000) == mesh::MorseRxResult::Started);
    current = Mode::Tx;
    const auto receivers = w.rx_starts;
    w.serviceDirectionPolicy();
    assert(!w._cw_active && !w._morse_rx_owned && w.state == STATE_IDLE);
    assert(w.rx_starts == receivers && !w.board.awake);
  }
  current = Mode::Both;
  {
    RadioLibWrapper w;
    assert(w.setCarrierWave(0, 1000) == Result::APPLIED);
    current = Mode::Off;
    w.serviceDirectionPolicy();
    assert(!w.chip.carrier && !w._cw_active && w.state == STATE_IDLE);
    assert(w._direction_receive_disabled);
  }
}
'''
        with tempfile.TemporaryDirectory(prefix="radio-direction-carrier-") as temporary:
            work = Path(temporary)
            cpp, executable = work / "test.cpp", work / "test"
            cpp.write_text(code)
            built = subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-O1",
                "-Wall", "-Wextra", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie", "-I", str(ROOT / "test/mocks"),
                "-I", str(ROOT / "src"), str(cpp), "-o", str(executable)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_direction_cleanup_cad_and_packet_last_gate(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        code = transition_harness(source).replace("int main()", "int legacy_main()", 1)
        end = code.rfind("}")
        code = code[:end] + "return 0;\n" + code[end:]
        code = r'''
#define MESH_ENABLE_RADIO_DIRECTION 1
#define MESH_ENABLE_CARRIER_TOOLS 0
#define MESH_ENABLE_MORSE_RX 0
#define MESH_ENABLE_TX_BEACONS 0
#define RADIOLIB_ERR_SPI_CMD_TIMEOUT -705
#include <helpers/RadioDirectionPolicy.h>
namespace mesh { namespace radioDirection {
static Mode current = Mode::Both;
Mode mode() { return current; }
}}
''' + code
        code = code.replace("  unsigned scans = 0;", r'''
  unsigned scans = 0, standbys = 0, clears = 0, transmissions = 0;
  int standby_error = 0, clear_error = 0, transmit_error = 0;
  int standby() { ++standbys; return standby_error; }
  int clearIrqFlags(uint32_t) { ++clears; return clear_error; }
  int startTransmit(uint8_t*, int) { ++transmissions; return transmit_error; }
''')
        code = code.replace("  unsigned n_cad_busy = 0, services = 0;", r'''
  unsigned n_cad_busy = 0, services = 0, before_tx = 0, after_tx = 0;
  void onBeforeTransmit() { ++before_tx; }
  void onAfterTransmit() { ++after_tx; }
''')
        code = code.replace("  uint8_t state = STATE_RX;", r'''
  uint8_t state = STATE_RX;
  bool _direction_receive_disabled = false;
  uint8_t _irq_probe_failures = 0;
  unsigned long _wd_observe_until = 0;
  void serviceDirectionPolicy();
  bool startSendRaw(const uint8_t*, int);
  bool getReceiveRetryDelay(uint32_t, uint32_t&) const;
  void idle() { _radio->standby(); state = STATE_IDLE; }
''')
        for signature in ("void RadioLibWrapper::serviceDirectionPolicy(",
                          "bool RadioLibWrapper::startSendRaw(",
                          "bool RadioLibWrapper::getReceiveRetryDelay("):
            code += "\n" + method(source, signature)
        code += r'''
int main() {
  using namespace mesh::radioDirection;
  assert(legacy_main() == 0); // Both preserves normal RX/CAD/calibration.
  const uint8_t message[] = {1, 2, 3};
  current = Mode::Rx;
  {
    RadioLibWrapper w;
    assert(!w.startSendRaw(message, sizeof(message)));
    assert(w.radio.transmissions == 0 && w.board.before_tx == 0);
    const auto old_arms = w.arms;
    w.startRecv();
    assert(w.arms == old_arms + 1 && w.state == STATE_RX);
  }
  current = Mode::Tx;
  {
    RadioLibWrapper w;
    w._rx_ps_enabled = w._rx_ps_armed = true;
    w._rx_retry_pending = w._rx_hold_continuous = true;
    w._nf_calib_active = w._nf_refresh_requested = w._nf_window_armed = true;
    w._wd_observe_until = 1000;
    w.events.record();
    w.loop();
    assert(w.radio.standbys == 1 && w.radio.clears == 1 && w.stops == 1);
    assert(w.state == STATE_IDLE && !w.hasPendingRadioInterrupt());
    assert(w._direction_receive_disabled && !w._rx_ps_armed && !w._rx_retry_pending);
    assert(!w._rx_hold_continuous && !w._nf_calib_active && !w._nf_refresh_requested);
    assert(!w._nf_window_armed && !w._wd_observe_until);
    for (unsigned i = 0; i < 1000; ++i) { ++now_ms; w.loop(); w.startRecv(); }
    assert(w.radio.standbys == 1 && w.radio.clears == 1 && w.arms == 0 && w.reads == 0);
    uint32_t delay = 123;
    assert(!w.getNoiseFloorWakeDelay(now_ms, delay));
    assert(!w.getReceiveRetryDelay(now_ms, delay));
    w.triggerNoiseFloorCalibrate(7);
    assert(!w._nf_refresh_requested && w._threshold == 7);
    // Free CAD must not require RX re-entry, even with no RSSI baseline.
    w._noise_floor_valid = false;
    assert(!w.isChannelActive());
    assert(w.radio.scans == 1 && w.arms == 0 && w.state == STATE_IDLE);
    assert(!w.isReceivingPassive(7));
    assert(w.radio.scans == 2 && w.reads == 0 && w.arms == 0);
    w.radio.scan_result = -9;
    assert(w.isChannelActive() && w.board.n_cad_busy == 1);
    w.radio.scan_result = RADIOLIB_CHANNEL_FREE;
    w.radio.clear_error = -8;
    assert(w.isChannelActive() && w.board.n_cad_busy == 2);
    w.radio.clear_error = 0;
    assert(w.startSendRaw(message, sizeof(message)));
    assert(w.state == STATE_TX_WAIT && w.radio.transmissions == 1);
    const auto standbys = w.radio.standbys;
    w.loop();
    assert(w.state == STATE_TX_WAIT && w.radio.standbys == standbys);
    current = Mode::Rx;
    w.serviceDirectionPolicy();
    assert(w.state == STATE_TX_WAIT && w.radio.standbys == standbys);
    assert(!w._direction_receive_disabled);
    w.state = STATE_IDLE; // Existing TxDone owner retires the on-air frame.
    w.startRecv();
    assert(w.state == STATE_RX && w.arms == 1);
  }
  current = Mode::Tx;
  {
    RadioLibWrapper w;
    w.state = STATE_IDLE; // Software idle may still conceal hardware RX.
    w.radio.standby_error = -7;
    w.serviceDirectionPolicy();
    assert(!w._direction_receive_disabled && w.radio.standbys == 1);
    w.radio.standby_error = 0;
    w.radio.clear_error = -8;
    w.serviceDirectionPolicy();
    assert(!w._direction_receive_disabled && w.radio.standbys == 2);
    w.radio.clear_error = 0;
    w.serviceDirectionPolicy();
    assert(w._direction_receive_disabled && w.radio.standbys == 3);
    current = Mode::Both;
    w.serviceDirectionPolicy();
    assert(!w._direction_receive_disabled);
    w.startRecv();
    assert(w.state == STATE_RX && w.arms == 1);
  }
  current = Mode::Off;
  {
    RadioLibWrapper w;
    w.loop(); w.startRecv();
    assert(w.state == STATE_IDLE && w.arms == 0 && w._direction_receive_disabled);
    assert(!w.startSendRaw(message, sizeof(message)) && w.radio.transmissions == 0);
  }
}
'''
        with tempfile.TemporaryDirectory(prefix="radio-direction-driver-") as temporary:
            work = Path(temporary)
            cpp, executable = work / "test.cpp", work / "test"
            cpp.write_text(code)
            built = subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-O1",
                "-Wall", "-Wextra", "-Werror", "-DUSE_SX1262=1", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie", "-I", str(ROOT / "src"),
                str(cpp), "-o", str(executable)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
