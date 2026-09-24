#!/usr/bin/env python3
"""Execute production CAD, receive re-arm, calibration and mode-watchdog methods."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

def method(text, signature):
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <RadioProfiles.h>
#include <helpers/radiolib/NoiseFloorEstimator.h>
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define STATE_INT_READY 16
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_CHANNEL_FREE -15
#define RADIOLIB_ERR_UNKNOWN -16
#define RADIOLIB_ERR_RX_TIMEOUT -6
#define MAX_TRANS_UNIT 255
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define NF_CALIB_INTERVAL_MS 2000UL
#define NF_CALIB_TIMEOUT_MS NoiseFloorEstimator::WINDOW_TIMEOUT_MS
#define NF_CONTINUOUS_TIMEOUT_MS NoiseFloorEstimator::WINDOW_TIMEOUT_MS
#define NF_CALIB_SETTLE_MS 7UL
#define NF_FAST_PROFILE_REFRESH_INTERVAL_MS (15UL * 60UL * 1000UL)
static volatile uint8_t state = STATE_RX;
static uint32_t now_ms = 0;
uint32_t millis() { return now_ms; }
uint32_t micros() { return now_ms * 1000UL; }
// The dedicated profile-clock contract exercises the production clock helpers.
// This receive-state harness only needs their portable single-process behavior.
void syncProfileClock(bool) {}
uint32_t profileTimestamp(bool) { return micros(); }
void noInterrupts() {}
void interrupts() {}
void yield() { ++now_ms; }
struct Board {
  unsigned n_cad_busy = 0, services = 0;
  void serviceWatchdog() { ++services; }
};
struct Radio {
  int scan_result = RADIOLIB_CHANNEL_FREE, start_result = 0;
  unsigned scans = 0;
  int startChannelScan() { ++scans; return start_result; }
  int getChannelScanResult() {
    if (scan_result != RADIOLIB_ERR_UNKNOWN) state |= STATE_INT_READY;
    return scan_result;
  }
};
struct RadioLibWrapper {
  bool _cw_active = false;
  bool serviceCarrierWave() { return false; } // dedicated CW harness owns this path
  mesh::RadioProfiles _profiles;
  uint32_t _profile_visit_stamp = 0;
  uint32_t _profile_scan_generation[2] = {};
  uint8_t _active_profile = 0;
  bool _profile_rxps_suspended = false;
  void serviceProfileScan() {} // separate profile-scan harness exercises tuning
  Board board; Board* _board = &board;
  Radio radio; Radio* _radio = &radio;
  bool _rx_ps_enabled = false, _rx_ps_armed = false, _rx_ps_continuous_fallback = false;
  bool _nf_calib_active = false, _nf_refresh_requested = true, _noise_floor_valid = true,
      _noise_floor_secondary_valid = true;
  bool _cad_enabled = true, packet = false, busy = false, inject_arm_irq = false;
  bool _rx_boosted_gain_valid = false, _cur_rx_boosted_gain = false;
  bool _wd_last_busy = false;
  uint8_t _startrx_fails = 0, _rx_mode_failures = 0;
  uint32_t _rx_mode_checked_at = 0, _rx_ps_rx_us = 50000, _rx_ps_sleep_us = 50000;
  unsigned long _nf_last_calib = 0, _nf_calib_deadline = 0, _nf_sample_from = 0;
  unsigned long _wd_last_transition = 0, _wd_stuck_thresh = 0, _wd_observe_ms = 0;
  int16_t _threshold = 0, _noise_floor = -105;
  int32_t _noise_floor_centi_dbm = -10500, _noise_floor_secondary_centi_dbm = -10600;
  int arm_result = 0, chip_mode = 1;
  float rssi = -100;
  unsigned arms = 0, stops = 0, soft = 0, hard = 0, reads = 0, mode_reads = 0;
  bool inject_mode_irq = false;
  NoiseFloorEstimator _floor_estimator;
  NoiseFloorEstimator _secondary_floor_estimator;
  NoiseFloorEstimator& profileFloorEstimator(uint8_t profile) {
    return profile == 1 ? _secondary_floor_estimator : _floor_estimator;
  }
  uint16_t profilePreamble(uint8_t profile) const { return _profiles.preamble(profile, 32); }
  RadioLibWrapper() { state = STATE_RX; now_ms = 100; }
  bool isChipBusy() { return busy; }
  bool isReceivingPacket() { return packet; }
  float getCurrentRSSI() { ++reads; return rssi; }
  int8_t readReceiveMode() {
    ++mode_reads;
    if (inject_mode_irq) state |= STATE_INT_READY;
    return chip_mode;
  }
  bool recoverRadio(bool use_hard) { use_hard ? ++hard : ++soft; return true; }
  void rxPsWatchdogCheck() {}
  unsigned long getEstAirtimeFor(int) { return 10; }
  void stopReceiveDutyCycle() { ++stops; _rx_ps_armed = false; }
  void doResetAGC() {}
  bool applyRxBoostedGainMode(bool) { return true; }
  int startReceiveMode() {
    ++arms;
    if (!arm_result) _rx_ps_armed = _rx_ps_enabled && !_nf_calib_active;
    if (inject_arm_irq) state |= STATE_INT_READY;
    return arm_result;
  }
  int16_t performChannelScan() { return performChannelScanWithTimeout(2500); }
  int16_t performChannelScanWithTimeout(unsigned long);
  bool isPacketPendingOrReceiving();
  bool isChannelActive();
  void startRecv();
  void checkReceiveMode(uint32_t);
  void loop();
  void triggerNoiseFloorCalibrate(int);
  void requestRestartRecv();
  void noiseFloorCalibCheck(unsigned long);
  void endNoiseFloorCalib(unsigned long);
  void requestNoiseFloorRefresh();
  void recalibrateNoiseFloor();
  void resetAGC();
};
@METHODS@

int main() {
  // A completed packet, current frame, or TX owns the radio; CAD cannot erase it.
  for (int owned : {1, 2, 3}) {
    RadioLibWrapper w;
    if (owned == 1) state |= STATE_INT_READY;
    if (owned == 2) w.packet = true;
    if (owned == 3) state = STATE_TX_WAIT;
    assert(w.isChannelActive());
    assert(w.radio.scans == 0 && w.arms == 0);
  }
  for (int result : {RADIOLIB_CHANNEL_FREE, 1, -5, RADIOLIB_ERR_UNKNOWN}) {
    for (bool ps : {false, true}) {
      RadioLibWrapper w;
      w._rx_ps_enabled = w._rx_ps_armed = ps;
      w.radio.scan_result = result;
      const bool active = w.isChannelActive();
      assert(active == (result != RADIOLIB_CHANNEL_FREE));
      assert(w.stops == unsigned(ps));
      assert(w.arms == 1 && state == STATE_RX);
      assert(w._rx_ps_armed == ps);
      if (result == RADIOLIB_ERR_UNKNOWN) {
        assert(now_ms == 2600 && w.board.services == 2);
      }
    }
  }
  {
    RadioLibWrapper w;
    w.radio.start_result = -5;
    assert(w.isChannelActive());
    assert(w.arms == 1 && state == STATE_RX);
  }
  {
    RadioLibWrapper w;
    w.arm_result = -5;
    assert(w.isChannelActive()); // a free CAD with failed RX restoration is not success
    assert(state == STATE_IDLE);
  }
  {
    RadioLibWrapper w;
    w.inject_arm_irq = true;
    assert(w.isChannelActive());
    assert(state == (STATE_RX | STATE_INT_READY)); // preserves the new packet
  }
  // A quiet bounded window completes in ~3.2 s and powersaving resumes.
  for (bool ps : {false, true}) {
    RadioLibWrapper w;
    w._rx_ps_enabled = w._rx_ps_armed = ps;
    w._noise_floor_valid = false;
    w._nf_last_calib = 0;
    for (; now_ms < 4000; ++now_ms) {
      w.loop();
      if (state == STATE_IDLE) w.startRecv();
    }
    assert(!w._nf_refresh_requested && !w._nf_calib_active);
    assert(w._noise_floor_valid && w._noise_floor_centi_dbm == -10000);
    assert(w._rx_ps_armed == ps);
    assert(w.reads == 64); // no unnecessary SPI RSSI reads between accepted samples
  }
  // Each receive re-arm needs fresh frontend settling before sampling RSSI.
  {
    RadioLibWrapper w;
    w.startRecv();
    for (; now_ms < 107; ++now_ms) w.loop();
    assert(w.reads == 0);
    w.loop();
    assert(w.reads == 1);
    now_ms = 200;
    w.startRecv(); // CAD/TX re-arm must settle again, retaining earlier samples
    for (; now_ms < 207; ++now_ms) w.loop();
    assert(w.reads == 1 && w._floor_estimator.count() == 1);
    w.loop();
    assert(w.reads == 2 && w._floor_estimator.count() == 2);
  }
  // Repeated scheduled requests must not reset a partial block.
  {
    RadioLibWrapper w;
    for (; now_ms < 4100; ++now_ms) {
      if (now_ms % 2000 == 0) w.requestNoiseFloorRefresh();
      w.loop();
    }
    assert(w._noise_floor_centi_dbm == -10125);
  }
  // After dual-profile initialization, the Dispatcher's two-second
  // maintenance tick updates the threshold but must not restart calibration.
  // A profile whose normal visit is under 7 ms gets one new bounded 7 ms
  // sample set after fifteen minutes; a naturally long profile does not.
  {
    RadioLibWrapper w;
    mesh::RadioProfileConfig second;
    second.params.freq = 910.5; second.params.bw = 62.5; second.params.sf = 7; second.params.cr = 5;
    second.mode = mesh::RadioProfileMode::RxTx;
    w._profiles.primary = second.params;
    w._profiles.setSecondary(second, false);
    w._nf_refresh_requested = false;
    w._noise_floor_valid = w._noise_floor_secondary_valid = true;
    w.triggerNoiseFloorCalibrate(7);
    assert(!w._nf_refresh_requested && w._threshold == 7);
    w._noise_floor_secondary_valid = false;
    w.triggerNoiseFloorCalibrate(8);
    assert(w._nf_refresh_requested && w._threshold == 8);
  }
  {
    RadioLibWrapper w;
    mesh::RadioProfileConfig second;
    second.params.freq = 910.5; second.params.bw = 500; second.params.sf = 5; second.params.cr = 5;
    second.mode = mesh::RadioProfileMode::Rx;
    w._profiles.primary = second.params;
    w._profiles.setSecondary(second, false);
    w._nf_refresh_requested = false;
    w._noise_floor_valid = w._noise_floor_secondary_valid = true;
    w._nf_last_calib = now_ms;
    now_ms += NF_FAST_PROFILE_REFRESH_INTERVAL_MS - 1;
    w.triggerNoiseFloorCalibrate(7);
    assert(!w._nf_refresh_requested);
    ++now_ms;
    w.triggerNoiseFloorCalibrate(7);
    assert(w._nf_refresh_requested);
  }
  {
    RadioLibWrapper w;
    mesh::RadioProfileConfig second;
    second.params.freq = 910.5; second.params.bw = 62.5; second.params.sf = 7; second.params.cr = 5;
    second.mode = mesh::RadioProfileMode::Rx;
    w._profiles.primary = second.params;
    w._profiles.setSecondary(second, false);
    w._nf_refresh_requested = false;
    w._noise_floor_valid = w._noise_floor_secondary_valid = true;
    w._nf_last_calib = now_ms;
    now_ms += NF_FAST_PROFILE_REFRESH_INTERVAL_MS;
    w.triggerNoiseFloorCalibrate(7);
    assert(!w._nf_refresh_requested);
  }
  now_ms = 100; // subsequent timing tests use their original short timeline
  // R1 and R2 collect distinct RSSI populations across alternating visits;
  // publishing one must never blend the other channel into its floor.
  {
    RadioLibWrapper w;
    mesh::RadioProfileConfig second;
    second.params.freq = 910.5; second.params.bw = 62.5; second.params.sf = 7; second.params.cr = 5;
    second.mode = mesh::RadioProfileMode::RxTx;
    w._profiles.primary = second.params;
    w._profiles.setSecondary(second, false);
    w._profile_scan_generation[0] = w._profiles.generation[0];
    w._profile_scan_generation[1] = w._profiles.generation[1];
    w._profile_rxps_suspended = true;
    w._noise_floor_valid = w._noise_floor_secondary_valid = false;
    w._nf_refresh_requested = true;
    w._nf_sample_from = 0;
    for (unsigned i = 0; i < NoiseFloorEstimator::SAMPLE_COUNT; ++i) {
      w._active_profile = 0; w.rssi = -100; w.loop(); now_ms += 50;
      w._active_profile = 1; w.rssi = -90; w.loop(); now_ms += 50;
    }
    w.loop(); // publish after both profile estimators complete
    assert(!w._nf_refresh_requested && w._noise_floor_centi_dbm == -10000);
    assert(w._noise_floor_secondary_centi_dbm == -9000);
  }
  // Hardware standby RSSI is not background noise, even if software says RX.
  {
    RadioLibWrapper w;
    w._floor_estimator.add(-100, 0);
    w.chip_mode = 0;
    w.rssi = -127;
    for (; now_ms < 4100; ++now_ms) w.loop();
    assert(w._noise_floor_centi_dbm == -10500);
    assert(w._floor_estimator.count() == 0 && w.reads == 0);
    assert(w.mode_reads == 80); // rejected samples are also limited to 20 Hz
    w.chip_mode = 1;
    w.rssi = -100;
    for (; now_ms < 8100; ++now_ms) w.loop();
    assert(w.reads == 64 && !w._nf_refresh_requested);
    assert(w._noise_floor_centi_dbm == -10125);
  }
  // A BUSY chip or a packet arriving during the mode probe must block RSSI I/O.
  for (bool busy : {false, true}) {
    RadioLibWrapper w;
    w.busy = busy;
    w.inject_mode_irq = !busy;
    w.loop();
    assert(w.reads == 0);
    assert(w.mode_reads == (busy ? 0U : 1U));
  }
  // Families without a mode probe retain the existing RSSI sampling path.
  {
    RadioLibWrapper w;
    w.chip_mode = -1;
    w.loop();
    assert(w.reads == 1);
  }
  // Busy continuous RX times out without changing the published floor.
  {
    RadioLibWrapper w;
    w.packet = true;
    for (; now_ms <= NoiseFloorEstimator::WINDOW_TIMEOUT_MS + 101; ++now_ms) w.loop();
    assert(!w._nf_refresh_requested);
    assert(w._noise_floor_centi_dbm == -10500);
    assert(w.reads == 0);
  }
  {
    RadioLibWrapper w;
    w._floor_estimator.add(-30, 100);
    w.resetAGC();
    assert(w._noise_floor_valid && w._noise_floor_centi_dbm == -10500);
    assert(w._floor_estimator.count() == 0);
  }
  // Unknown/SPI-busy/sleep and packet ownership cannot trigger false recovery.
  for (int skip : {0,1,2,3,4}) {
    RadioLibWrapper w;
    w.chip_mode = 0;
    if (skip == 0) w.chip_mode = -1;
    if (skip == 1) w.busy = true;
    if (skip == 2) w._rx_ps_armed = true;
    if (skip == 3) w.packet = true;
    if (skip == 4) state |= STATE_INT_READY;
    for (uint32_t t : {10000U,20000U,30000U}) w.checkReceiveMode(t);
    assert(w.soft == 0 && w.hard == 0);
  }
  {
    RadioLibWrapper w;
    w.chip_mode = 0;
    w.checkReceiveMode(10000); assert(w.soft == 0);
    w.checkReceiveMode(20000); assert(w.soft == 1);
    w.checkReceiveMode(30000); assert(w.hard == 1);
    w.chip_mode = 1; w.checkReceiveMode(40000);
    w.chip_mode = 0; w.checkReceiveMode(50000);
    assert(w.soft == 1 && w.hard == 1);
  }
  {
    RadioLibWrapper w;
    w._rx_mode_checked_at = 0xFFFFF000U;
    w.checkReceiveMode(0x10); assert(w._rx_mode_checked_at == 0xFFFFF000U);
    w.checkReceiveMode(0x2000); assert(w._rx_mode_checked_at == 0x2000);
  }
}
'''

class RadioReceiveContractTest(unittest.TestCase):
    def test_preamble_margin_and_receive_deadlines(self):
        radio_source = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        timing = method(radio_source, 'PacketMillis RadioLibWrapper::calcMaxPacketMillis(')
        sx1262 = method((ROOT / 'src/helpers/radiolib/CustomSX1262.h').read_text(),
                        'bool isReceiving()')
        lr1110 = method((ROOT / 'src/helpers/radiolib/CustomLR1110.h').read_text(),
                        'bool isReceiving()')
        source = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#define MAX_TRANS_UNIT 255
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED 4U
#define RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID 8U
#define RADIOLIB_SX126X_IRQ_HEADER_VALID 16U
#define RADIOLIB_SX126X_IRQ_HEADER_ERR 32U
#define RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED 16U
#define RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID 32U
#define RADIOLIB_LR11X0_IRQ_HEADER_ERR 64U
static uint32_t now_ms;
uint32_t millis() { return now_ms; }
struct PacketMillis { uint32_t preambleMillis, payloadMillis; };
struct Chip { uint32_t getTimeOnAir(int) { return 1000000; } };
struct RadioLibWrapper {
  Chip chip;
  Chip* _radio = &chip;
  PacketMillis calcMaxPacketMillis(uint8_t, float, uint8_t, uint16_t);
};
@TIMING@
struct ReceiverState {
  uint32_t _preambleMillis, _maxPayloadMillis;
  uint32_t _activityAt = 0, irq = 0;
  bool _headerSeen = false;
  explicit ReceiverState(PacketMillis p)
      : _preambleMillis(p.preambleMillis), _maxPayloadMillis(p.payloadMillis) {}
  bool isChipBusy() { return false; }
  uint32_t getIrqFlags() { return irq; }
  uint32_t getIrqStatus() { return irq; }
  void clearIrqFlags(uint32_t flags) { irq &= ~flags; }
  void clearIrqState(uint32_t flags) { irq &= ~flags; }
};
struct SX1262Receiver : ReceiverState {
  using ReceiverState::ReceiverState;
  @SX1262@
};
struct LR1110Receiver : ReceiverState {
  using ReceiverState::ReceiverState;
  @LR1110@
};

template <typename Receiver>
void checkReceive(PacketMillis limits, uint32_t preamble, uint32_t header,
                  uint32_t error, bool preserve_header_error) {
  // A latched preamble with no header expires despite repeated polling.
  // Check both normal uptime and a millis() rollover during the hold.
  for (uint32_t start : {100U, 0xFFFFFFF0U}) {
    Receiver rx(limits);
    now_ms = start;
    assert(!rx.isReceiving());
    rx.irq = preamble;
    assert(rx.isReceiving());
    for (uint32_t elapsed : {1U, limits.preambleMillis - 1, limits.preambleMillis}) {
      now_ms = start + elapsed;
      assert(rx.isReceiving());
    }
    now_ms = start + limits.preambleMillis + 1;
    assert(!rx.isReceiving() && !(rx.irq & preamble));

    // A subsequent packet gets a fresh hold, not the abandoned packet's timer.
    ++now_ms;
    rx.irq = preamble;
    assert(rx.isReceiving());
    now_ms += limits.preambleMillis;
    rx.irq |= header;
    assert(rx.isReceiving());
    now_ms += limits.payloadMillis;
    assert(rx.isReceiving());
    ++now_ms;
    assert(!rx.isReceiving() && !(rx.irq & (preamble | header)));
  }
  {
    // A completed packet releases the scan immediately when its IRQs are consumed.
    Receiver rx(limits);
    now_ms = 100;
    rx.irq = preamble;
    assert(rx.isReceiving());
    ++now_ms;
    rx.irq |= header;
    assert(rx.isReceiving());
    ++now_ms;
    rx.irq = 0;
    assert(!rx.isReceiving());
    rx.irq = preamble;
    assert(rx.isReceiving());
  }
  {
    // Header failure releases the hold; LR1110's recovery path owns its error IRQ.
    Receiver rx(limits);
    now_ms = 100;
    rx.irq = preamble;
    assert(rx.isReceiving());
    ++now_ms;
    rx.irq |= error;
    assert(!rx.isReceiving());
    assert(bool(rx.irq & error) == preserve_header_error);
  }
}
int main() {
  RadioLibWrapper radio;
  struct Case {
    uint8_t sf;
    float bw;
    uint8_t cr;
    uint16_t preamble;
    uint32_t hold_ms, payload_ms;
  };
  // Independent timing examples: configured preamble + four symbols + the
  // existing sync/header allowance. Payload limits retain their old values.
  const Case cases[] = {
    {7, 62.5f, 5, 32, 99, 1456},
    {7, 125,   5, 32, 50, 1528},
    {7, 500,   5, 64, 21, 1569},
    {8, 500,   5, 88, 54, 1518},
    {9, 500,   5, 32, 50, 1528},
    {5, 500,   5, 128, 10, 1586},
    {6, 125,   8, 16, 18, 985},
    {7, 62.5f, 8, 120, 280, 730},
    {7, 125,   8, 65535, 67125, 4000},
  };
  for (const Case& c : cases) {
    const auto limits = radio.calcMaxPacketMillis(c.sf, c.bw, c.cr, c.preamble);
    assert(limits.preambleMillis == c.hold_ms);
    assert(limits.payloadMillis == c.payload_ms);
    checkReceive<SX1262Receiver>(limits, RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED,
        RADIOLIB_SX126X_IRQ_HEADER_VALID, RADIOLIB_SX126X_IRQ_HEADER_ERR, false);
    checkReceive<LR1110Receiver>(limits, RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED,
        RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID, RADIOLIB_LR11X0_IRQ_HEADER_ERR, true);
  }
}
'''
        with tempfile.TemporaryDirectory(prefix='meshcore-preamble-hold-') as tmp:
            cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            cpp.write_text(source.replace('@TIMING@', timing)
                           .replace('@SX1262@', sx1262).replace('@LR1110@', lr1110))
            result = subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-O1',
                '-Wall', '-Wextra', str(cpp), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_rxps_setter_preserves_intent_and_rejects_bad_values(self):
        common = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        setter = method(common, 'if (memcmp(config, "radio.rxps ", 11) == 0)')
        calculator = method(common, 'bool CommonCLI::calculateRxPowerSavingLevel(')
        source = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
#include <helpers/radiolib/RXPowerSaving.h>
struct NodePrefs {
  uint8_t rx_powersaving_enabled = 1, rx_ps_level = 6, rx_ps_preamble = 32, sf = 8;
  uint32_t rx_ps_rx_us = 40000, rx_ps_sleep_us = 32000;
  float bw = 62.5;
};
struct Callbacks {
  bool accept = true;
  unsigned calls = 0;
  bool setRxPowerSaving(bool, uint32_t, uint32_t) { ++calls; return accept; }
};
struct CommonCLI {
  NodePrefs* _prefs;
  Callbacks* _callbacks;
  unsigned saves = 0;
  void savePrefs() { ++saves; }
  bool calculateRxPowerSavingLevel(uint32_t, uint8_t, float, uint32_t, uint32_t*, uint32_t*);
  void set(const char* config, char* reply);
};
void appendRxPowerSavingAdjustmentNote(char*, const NodePrefs*, uint8_t, float) {}
@CALCULATOR@
void CommonCLI::set(const char* config, char* reply) { @SETTER@ }
int main() {
  NodePrefs p;
  Callbacks callbacks;
  CommonCLI cli{&p, &callbacks};
  char reply[160] = {};
  cli.set("radio.rxps level 8 preamble 32", reply);
  assert(p.rx_powersaving_enabled && p.rx_ps_level == 8 && p.rx_ps_preamble == 32);
  const uint32_t rx = p.rx_ps_rx_us, sleep = p.rx_ps_sleep_us;
  cli.set("radio.rxps off \t", reply);
  assert(!p.rx_powersaving_enabled && p.rx_ps_level == 8 && p.rx_ps_preamble == 32);
  assert(p.rx_ps_rx_us == rx && p.rx_ps_sleep_us == sleep && cli.saves == 2);
  for (const char* command : {"radio.rxps level 264 preamble 32",
      "radio.rxps level 8 preamble 288", "radio.rxps level 4294967296",
      "radio.rxps 32 1000", "radio.rxps max extra"}) {
    cli.set(command, reply);
    assert(cli.saves == 2 && callbacks.calls == 2);
    assert(!p.rx_powersaving_enabled && p.rx_ps_level == 8 && p.rx_ps_preamble == 32);
  }
  cli.set("radio.rxps 12345 67890", reply);
  assert(p.rx_powersaving_enabled && p.rx_ps_level == 0 && p.rx_ps_preamble == 0);
  assert(p.rx_ps_rx_us == 12345 && p.rx_ps_sleep_us == 67890 && cli.saves == 3);
  callbacks.accept = false;
  cli.set("radio.rxps max", reply);
  assert(p.rx_ps_level == 0 && p.rx_ps_rx_us == 12345 && cli.saves == 3);
  callbacks.accept = true;
  cli.set("radio.rxps max", reply);
  assert(p.rx_ps_level == 8 && p.rx_ps_preamble == 16 && cli.saves == 4);
}
'''
        with tempfile.TemporaryDirectory(prefix='meshcore-rxps-cli-') as tmp:
            cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            cpp.write_text(source.replace('@CALCULATOR@', calculator).replace('@SETTER@', setter))
            result = subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-O1',
                '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
                str(cpp), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_transitions(self):
        source = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        names = [('int16_t','performChannelScanWithTimeout'), ('bool','isPacketPendingOrReceiving'),
                 ('bool','isChannelActive'), ('void','startRecv'), ('void','checkReceiveMode'),
                 ('void','loop'), ('void','triggerNoiseFloorCalibrate'), ('void','requestRestartRecv'), ('void','noiseFloorCalibCheck'),
                 ('void','endNoiseFloorCalib'), ('void','requestNoiseFloorRefresh'),
                 ('void','recalibrateNoiseFloor'), ('void','resetAGC')]
        methods = '\n'.join(method(source, f'{kind} RadioLibWrapper::{name}(') for kind,name in names)
        with tempfile.TemporaryDirectory(prefix='meshcore-radio-transitions-') as tmp:
            cpp = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / 'test'
            cpp.write_text(HARNESS.replace('@METHODS@', methods))
            result = subprocess.run([os.environ.get('CXX','c++'), '-std=c++17', '-O1',
                '-Wall', '-Wextra', '-I', str(ROOT / 'src'), str(cpp), '-o', str(binary)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == '__main__':
    unittest.main()
