"""Execute production RXPS arming, fallback and status against faultable hardware."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <string>
#define MESH_ENABLE_RADIO_DIRECTION 1
#define MESH_ENABLE_TX_BEACONS 1
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/radiolib/NoiseFloorEstimator.h>
#include <helpers/radiolib/RXPowerSaving.h>
namespace mesh {
enum class RadioParamApplyResult : uint8_t { APPLIED, BUSY, FAILED };
namespace radioDirection {
Mode current = Mode::Both;
Mode mode() { return current; }
}
}
static uint32_t now_ms = 1000;
static uint32_t millis() { return now_ms; }
constexpr int RADIOLIB_ERR_NONE = 0;
constexpr uint8_t STATE_IDLE = 0, STATE_RX = 1, STATE_TX_WAIT = 3;
using RadioLibIrqFlags_t = uint32_t;
constexpr uint32_t RADIOLIB_IRQ_RX_DEFAULT_FLAGS = 0x100;
constexpr unsigned RADIOLIB_IRQ_PREAMBLE_DETECTED = 1, RADIOLIB_IRQ_RX_DONE = 2;
constexpr unsigned RADIOLIB_IRQ_TIMEOUT = 3, RADIOLIB_IRQ_CRC_ERR = 4;
constexpr unsigned RADIOLIB_IRQ_HEADER_ERR = 5;
#define NF_CONTINUOUS_TIMEOUT_MS NoiseFloorEstimator::WINDOW_TIMEOUT_MS

struct RadioLibWrapper {
  bool _rx_ps_enabled = true, _rx_ps_armed = false;
  bool _rx_ps_continuous_fallback = false, _nf_calib_active = false;
  bool _profile_rxps_suspended = false, _profile_refresh_required = false;
  bool _profile_repair_required = false;
  bool _profile_saved_rxps = false, _beacon_owned = false, _beacon_restoring = false;
  bool _cw_active = false, _rx_hold_continuous = false;
  bool _nf_refresh_requested = false, _nf_window_armed = false;
  bool _nf_lazy_refresh = false, _nf_refresh_attempted = false;
  bool _noise_floor_valid = false, _noise_floor_secondary_valid = false;
  uint8_t state = STATE_RX, _active_profile = 0;
  uint32_t _rx_ps_rx_us = 50000, _rx_ps_sleep_us = 50000;
  uint32_t _rx_ps_eff_rx_us = 0, _rx_ps_eff_sleep_us = 0;
  int16_t _rx_ps_last_error = RADIOLIB_ERR_NONE, _noise_floor = -100;
  int32_t _noise_floor_centi_dbm = -10000, _noise_floor_secondary_centi_dbm = -10000;
  uint32_t _rx_ps_arm_failures = 0, n_recv = 0, n_sent = 0, n_recv_errors = 0;
  uint32_t _nf_calib_deadline = 0, _nf_last_calib = 0, _nf_sample_from = 0;
  uint32_t _profile_scan_generation[2] = {0, 0};
  bool _rx_retry_pending = false;
  uint32_t _rx_retry_at = 0;
  RxPowerSavingArmRetryState _rx_ps_arm_retry;
  NoiseFloorEstimator _floor_estimator, _secondary_floor_estimator;
  struct Profiles {
    bool scanning = false;
    uint32_t generation[2] = {0, 0};
    bool enabled() const { return scanning; }
  } _profiles;

  bool packet_pending = false, irq_pending = false, busy = false, supported = true;
  bool inject_irq_at_packet_probe = false, inject_irq_at_standby = false;
  int16_t arm_error = 0, continuous_error = 0, stop_error = 0, standby_error = 0;
  uint32_t hardware_rx_us = 48000, hardware_sleep_us = 47000;
  unsigned arm_calls = 0, continuous_calls = 0, stop_calls = 0, standby_calls = 0;
  unsigned begin_calls = 0, end_calls = 0, start_calls = 0;
  mutable unsigned busy_probes = 0, packet_probes = 0, irq_probes = 0;
  std::string events;
  struct Physical {
    RadioLibWrapper* owner;
    int16_t standby() {
      ++owner->standby_calls; owner->events += 'B';
      if (owner->inject_irq_at_standby) owner->irq_pending = true;
      return owner->standby_error;
    }
  } physical{this};
  Physical* _radio = &physical;

  RadioLibWrapper() { now_ms = 1000; mesh::radioDirection::current = mesh::radioDirection::Mode::Both; }
  bool supportsRxPowerSaving() const { return supported; }
  bool isPacketPendingOrReceiving() const { return packet_pending || irq_pending; }
  bool isChipBusy() const { ++busy_probes; return busy; }
  bool isReceivingPacket() {
    ++packet_probes;
    if (inject_irq_at_packet_probe) irq_pending = true;
    return packet_pending;
  }
  bool hasPendingRadioInterrupt() const { ++irq_probes; return irq_pending; }
  void claimRadioInterrupt() { irq_pending = false; }
  uint32_t rxPowerSavingTransitionUs() const { return 2600; }
  int16_t startContinuousReceive() {
    ++continuous_calls; events += 'C'; return continuous_error;
  }
  int16_t stopDutyCycleHardware() {
    ++stop_calls; events += 'S'; return stop_error;
  }
  int16_t armDutyCycle(RadioLibIrqFlags_t flags, RadioLibIrqFlags_t mask,
                      uint32_t* rx_us, uint32_t* sleep_us) {
    ++arm_calls; events += 'A';
    assert(flags == (RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED)));
    assert(mask == ((1UL << RADIOLIB_IRQ_RX_DONE) | (1UL << RADIOLIB_IRQ_TIMEOUT)
        | (1UL << RADIOLIB_IRQ_CRC_ERR) | (1UL << RADIOLIB_IRQ_HEADER_ERR)));
    assert(*rx_us == _rx_ps_rx_us && *sleep_us == _rx_ps_sleep_us);
    if (!arm_error) { *rx_us = hardware_rx_us; *sleep_us = hardware_sleep_us; }
    return arm_error;
  }
  void startRecv() {
    ++start_calls;
    state = startReceivePowerSaving() == RADIOLIB_ERR_NONE ? STATE_RX : STATE_IDLE;
  }
  void serviceDirectionPolicy() {}
  void serviceProfileScan() {}
  void rxPsWatchdogCheck() {}
  void checkReceiveMode(uint32_t) {}
  void noiseFloorCalibCheck(unsigned long) {}
  void endNoiseFloorCalib(unsigned long) {}
  uint32_t profileTimestamp(bool) { return now_ms; }
  NoiseFloorEstimator& profileFloorEstimator(uint8_t profile) {
    return profile ? _secondary_floor_estimator : _floor_estimator;
  }
  int readReceiveMode() { return 1; }
  float getCurrentRSSI() { return -100; }

  int startReceivePowerSaving();
  void stopReceiveDutyCycle();
  uint8_t beginReconfigure();
  void endReconfigure(bool);
  void loop();
  bool rxPowerSavingRetryEligible() const;
  void serviceRxPowerSavingRetry(uint32_t);
  bool getRxPowerSavingRetryDelay(uint32_t, uint32_t&) const;
  bool getReceiveRetryDelay(uint32_t, uint32_t&) const;
  bool setRxPowerSaving(bool, uint32_t, uint32_t);
  mesh::RadioParamApplyResult trySetRxPowerSaving(bool, uint32_t, uint32_t);
  RxPowerSavingStatus getRxPowerSavingStatus() const;
  @RESET_STATS@
};

@METHODS@

static uint32_t retryDelay(const RadioLibWrapper& radio) {
  uint32_t wait = UINT32_MAX;
  assert(radio.getRxPowerSavingRetryDelay(now_ms, wait));
  return wait;
}

static void assertFailure(const RadioLibWrapper& radio, unsigned failures, int error = -708) {
  const auto status = radio.getRxPowerSavingStatus();
  assert(status.supported && !status.armed && status.last_error == error);
  assert(status.arm_failures == failures && status.consecutive_failures == failures);
  assert(!status.effective_rx_us && !status.effective_sleep_us);
  assert(radio._rx_ps_continuous_fallback && status.receiving && status.retry_pending && !status.suspended);
  assert(status.retry_delay_ms == retryDelay(radio));
}

static void failOnce(RadioLibWrapper& radio) {
  radio.arm_error = -708;
  assert(radio.startReceivePowerSaving() == RADIOLIB_ERR_NONE);
  assert(radio.arm_calls == 1 && radio.stop_calls == 1 && radio.continuous_calls == 1);
  assert(radio.events == "ASC" && retryDelay(radio) == 5000);
  assertFailure(radio, 1);
}

static void automaticRecoveryAndBoundedFailure() {
  for (uint32_t origin : {1000U, UINT32_MAX - 4999U}) {
    RadioLibWrapper radio; now_ms = origin;
    failOnce(radio);
    const uint32_t intervals[] = {5000, 15000, 60000, 300000, 300000};
    for (unsigned i = 0; i < 5; ++i) {
      assert(retryDelay(radio) == intervals[i]);
      const unsigned arms = radio.arm_calls, starts = radio.start_calls;
      const unsigned hardware = radio.standby_calls + radio.stop_calls + radio.continuous_calls;
      const unsigned probes = radio.busy_probes + radio.packet_probes + radio.irq_probes;
      // Wake/status inspection is pure, including while the wrapped deadline is zero.
      for (unsigned query = 0; query < 100; ++query) {
        assert(retryDelay(radio) == intervals[i]);
        assertFailure(radio, arms);
        uint32_t ordinary = 37;
        assert(!radio.getReceiveRetryDelay(now_ms, ordinary) && ordinary == 37);
      }
      assert(probes == radio.busy_probes + radio.packet_probes + radio.irq_probes);
      // Calling the normal loop or rearming after unrelated work cannot hot-retry.
      for (unsigned poll = 0; poll < 50; ++poll) radio.loop();
      assert(radio.arm_calls == arms && radio.start_calls == starts);
      assert(hardware == radio.standby_calls + radio.stop_calls + radio.continuous_calls);
      assert(!radio.startReceivePowerSaving() && radio.arm_calls == arms);
      assertFailure(radio, arms);
      now_ms += intervals[i] - 1;
      radio.loop();
      assert(radio.arm_calls == arms && retryDelay(radio) == 1);
      ++now_ms;
      assert(retryDelay(radio) == 0);
      radio.loop();
      assert(radio.arm_calls == arms + 1 && radio.state == STATE_RX);
      assert(radio.start_calls == starts + 1 && radio.standby_calls == i + 1);
      assertFailure(radio, arms + 1);
    }
    // A later hardware recovery rearms from the idle loop without config changes.
    const uint32_t requested_rx = radio._rx_ps_rx_us, requested_sleep = radio._rx_ps_sleep_us;
    radio.arm_error = 0;
    now_ms += retryDelay(radio);
    radio.loop();
    const auto status = radio.getRxPowerSavingStatus();
    assert(status.armed && !radio._rx_ps_continuous_fallback && status.last_error == 0);
    assert(status.arm_failures == 6 && status.consecutive_failures == 0);
    assert(!status.retry_pending && !status.retry_delay_ms && !status.suspended);
    assert(status.effective_rx_us == 48000 && status.effective_sleep_us == 47000);
    assert(radio._rx_ps_rx_us == requested_rx && radio._rx_ps_sleep_us == requested_sleep);
    uint32_t wait = 39;
    assert(!radio.getRxPowerSavingRetryDelay(now_ms, wait) && wait == 39);
    // Success resets the backoff while preserving the cumulative failure diagnostic.
    radio.arm_error = -707;
    assert(!radio.startReceivePowerSaving());
    assert(retryDelay(radio) == 5000);
    const auto later = radio.getRxPowerSavingStatus();
    assert(later.last_error == -707 && later.arm_failures == 7 && later.consecutive_failures == 1);
  }
  std::puts("PASS: loop-driven RXPS recovery, bounded retries and rollover");
}

static void ownershipAndAdmission() {
  for (unsigned cause = 0; cause < 14; ++cause) {
    RadioLibWrapper radio; failOnce(radio); now_ms += retryDelay(radio);
    if (cause == 0) radio.busy = true;
    if (cause == 1) radio.packet_pending = true;
    if (cause == 2) radio.irq_pending = true;
    if (cause == 3) radio.inject_irq_at_packet_probe = true;
    if (cause == 4) radio.inject_irq_at_standby = true;
    if (cause == 5) radio.state = STATE_TX_WAIT;
    if (cause == 6) radio._beacon_owned = true;
    if (cause == 7) radio._cw_active = true;
    if (cause == 8) radio._rx_hold_continuous = true;
    if (cause == 9) radio._nf_calib_active = true;
    if (cause == 10) radio._nf_refresh_requested = true;
    if (cause == 11) radio._profile_rxps_suspended = true;
    if (cause == 12) radio._profile_refresh_required = true;
    if (cause == 13) radio._profile_repair_required = true;
    radio.loop();
    assert(radio.arm_calls == 1 && radio.stop_calls == 1 && radio.continuous_calls == 1);
    const auto paused = radio.getRxPowerSavingStatus();
    assert(paused.last_error == -708 && paused.arm_failures == 1);
    assert(paused.retry_pending && paused.consecutive_failures == 1);
    assert(paused.suspended == (cause == 11) && paused.calibrating == (cause == 9));
    assert(paused.receiving == (cause != 5));
    if (cause < 5) {
      assert(retryDelay(radio) == 100);
      assert(radio.standby_calls == (cause == 4 ? 1U : 0U));
      const unsigned standbys = radio.standby_calls;
      for (unsigned poll = 0; poll < 50; ++poll) radio.loop();
      assert(radio.standby_calls == standbys && radio.arm_calls == 1);
      if (cause == 2 || cause == 3 || cause == 4) assert(radio.irq_pending);
    } else {
      uint32_t wait = 41;
      assert(!radio.getRxPowerSavingRetryDelay(now_ms, wait) && wait == 41);
    }
    // Once the owner releases the radio, its retained deadline recovers without a command.
    radio.busy = radio.packet_pending = radio.irq_pending = false;
    radio.inject_irq_at_packet_probe = radio.inject_irq_at_standby = false;
    radio._beacon_owned = radio._cw_active = radio._rx_hold_continuous = false;
    radio._nf_calib_active = radio._nf_refresh_requested = false;
    radio._profile_rxps_suspended = radio._profile_refresh_required = radio._profile_repair_required = false;
    radio.state = STATE_RX; radio.arm_error = 0;
    now_ms += retryDelay(radio);
    radio.loop();
    assert(radio.arm_calls == 2 && radio.getRxPowerSavingStatus().armed);
  }
  for (auto mode : {mesh::radioDirection::Mode::Tx, mesh::radioDirection::Mode::Off}) {
    RadioLibWrapper radio; failOnce(radio); now_ms += retryDelay(radio);
    mesh::radioDirection::current = mode;
    radio.loop();
    uint32_t wait = 43;
    assert(radio.arm_calls == 1 && !radio.getRxPowerSavingRetryDelay(now_ms, wait) && wait == 43);
  }
  RadioLibWrapper scan; failOnce(scan); now_ms += retryDelay(scan);
  scan._profiles.scanning = true;
  scan.loop();
  uint32_t wait = 47;
  assert(scan.arm_calls == 1 && !scan.getRxPowerSavingRetryDelay(now_ms, wait) && wait == 47);
  std::puts("PASS: RXPS recovery preserves radio ownership and pending packets");
}

static void longOwnerHoldsAndBoundedDefers() {
  for (uint32_t origin : {1000U, UINT32_MAX - 4999U}) {
    RadioLibWrapper radio; now_ms = origin; failOnce(radio);
    radio._profile_rxps_suspended = true;
    now_ms += UINT32_C(0x80000000) + 1000000U;
    radio.loop();
    assert(radio.arm_calls == 1);
    const auto held = radio.getRxPowerSavingStatus();
    assert(held.suspended && held.retry_pending && held.retry_delay_ms == 0);
    assert(held.last_error == -708 && held.arm_failures == 1);
    radio._profile_rxps_suspended = false;
    assert(retryDelay(radio) == 0);
    radio.arm_error = 0;
    radio.loop();
    assert(radio.arm_calls == 2 && radio.getRxPowerSavingStatus().armed);

    RadioLibWrapper deferred; now_ms = origin; failOnce(deferred);
    deferred._rx_ps_arm_retry.defer(now_ms, UINT32_MAX);
    assert(retryDelay(deferred) == 300000);
    deferred.arm_error = 0;
    now_ms += 299999;
    deferred.loop();
    assert(deferred.arm_calls == 1 && retryDelay(deferred) == 1);
    ++now_ms;
    deferred.loop();
    assert(deferred.arm_calls == 2 && deferred.getRxPowerSavingStatus().armed);
  }
  std::puts("PASS: RXPS retries stay bounded after long ownership holds");
}

static void manualRetryAndStats() {
  using Result = mesh::RadioParamApplyResult;
  RadioLibWrapper healthy;
  assert(!healthy.startReceivePowerSaving());
  const unsigned standbys = healthy.standby_calls, arms = healthy.arm_calls;
  assert(healthy.trySetRxPowerSaving(true, 50000, 50000) == Result::APPLIED);
  assert(healthy.standby_calls == standbys && healthy.arm_calls == arms);
  {
    // Validation and a rejected hardware transaction keep the accepted settings.
    RadioLibWrapper invalid;
    invalid.supported = false;
    assert(invalid.trySetRxPowerSaving(true, 70000, 90000) == Result::FAILED);
    invalid.supported = true;
    assert(invalid.trySetRxPowerSaving(true, 0, 90000) == Result::FAILED);
    assert(!invalid.begin_calls && !invalid.end_calls);
    invalid.standby_error = -705;
    assert(invalid.trySetRxPowerSaving(true, 70000, 90000) == Result::FAILED);
    assert(invalid.begin_calls == 1 && !invalid.end_calls);
    assert(invalid._rx_ps_rx_us == 50000 && invalid._rx_ps_sleep_us == 50000);
  }
  for (unsigned cause = 0; cause < 6; ++cause) {
    RadioLibWrapper radio; failOnce(radio);
    if (cause == 0) radio.busy = true;
    if (cause == 1) radio.packet_pending = true;
    if (cause == 2) radio.irq_pending = true;
    if (cause == 3) radio.state = STATE_TX_WAIT;
    if (cause == 4) radio._beacon_owned = true;
    if (cause == 5) radio._cw_active = true;
    const auto result = radio.trySetRxPowerSaving(true, 50000, 50000);
    assert(result == Result::BUSY && radio.arm_calls == 1);
    const auto status = radio.getRxPowerSavingStatus();
    assert(status.last_error == -708 && status.arm_failures == 1 && status.consecutive_failures == 1);
    assert(radio._rx_ps_arm_retry.delayMs(now_ms) == 5000);
  }
  RadioLibWrapper radio; failOnce(radio); radio.arm_error = 0;
  assert(radio.trySetRxPowerSaving(true, 50000, 50000) == Result::APPLIED);
  assert(radio.standby_calls == 1 && radio.arm_calls == 2);
  assert(radio.getRxPowerSavingStatus().armed && radio.getRxPowerSavingStatus().arm_failures == 1);
  // Explicit new settings and the existing clear-stats command also permit retry.
  radio.arm_error = -708;
  assert(!radio.startReceivePowerSaving());
  radio.arm_error = 0;
  assert(radio.setRxPowerSaving(true, 70000, 90000));
  assert(radio.getRxPowerSavingStatus().armed && radio._rx_ps_rx_us == 70000 && radio._rx_ps_sleep_us == 90000);
  radio.arm_error = -708;
  assert(!radio.startReceivePowerSaving());
  radio.n_recv = 2; radio.n_sent = 3; radio.n_recv_errors = 4;
  radio.resetStats();
  assert(!radio.n_recv && !radio.n_sent && !radio.n_recv_errors);
  assert(!radio.getRxPowerSavingStatus().arm_failures);
  assert(radio.getRxPowerSavingStatus().last_error == -708);
  assert(!radio._rx_ps_arm_retry.pending() && !radio._rx_ps_arm_retry.consecutiveFailures());
  radio.arm_error = 0;
  assert(!radio.startReceivePowerSaving() && radio.getRxPowerSavingStatus().armed);
  std::puts("PASS: same-setting manual retry, healthy no-op and stats reset");
}

static void diagnosticTruthAndTimingFallback() {
  {
    // Calibration's successful plain RX must retain the previous arm failure.
    RadioLibWrapper radio; failOnce(radio);
    radio._nf_calib_active = true;
    assert(!radio.startReceivePowerSaving());
    const auto status = radio.getRxPowerSavingStatus();
    assert(status.last_error == -708 && status.arm_failures == 1);
    assert(status.calibrating && status.retry_pending && status.retry_delay_ms == 5000);
    uint32_t wait = 59;
    assert(!radio.getRxPowerSavingRetryDelay(now_ms, wait) && wait == 59);
    radio._nf_calib_active = false;
    assert(!radio.startReceivePowerSaving() && radio.arm_calls == 1);
  }
  {
    RadioLibWrapper radio;
    assert(!radio.startReceivePowerSaving());
    radio._nf_calib_active = radio.packet_pending = true;
    assert(!radio.startReceivePowerSaving());
    assert(radio.arm_calls == 1 && radio.stop_calls == 0 && radio.continuous_calls == 0);
    assert(radio.getRxPowerSavingStatus().armed);
    radio.packet_pending = false;
    assert(!radio.startReceivePowerSaving());
    const auto status = radio.getRxPowerSavingStatus();
    assert(!status.armed && !status.effective_rx_us && !status.effective_sleep_us);
    assert(radio.events == "ASC");
  }
  {
    RadioLibWrapper radio;
    assert(!radio.startReceivePowerSaving());
    radio.stop_error = -705;
    radio.stopReceiveDutyCycle();
    const auto status = radio.getRxPowerSavingStatus();
    assert(!status.armed && status.last_error == -705);
    assert(!status.effective_rx_us && !status.effective_sleep_us);
  }
  {
    RadioLibWrapper radio;
    radio.arm_error = -708; radio.continuous_error = -705;
    assert(radio.startReceivePowerSaving() == -705);
    const auto status = radio.getRxPowerSavingStatus();
    assert(status.last_error == -705 && status.arm_failures == 1 && !status.armed);
    assert(!status.effective_rx_us && !status.effective_sleep_us);
  }
  for (bool planned : {false, true}) {
    RadioLibWrapper radio;
    if (planned) radio._rx_ps_rx_us = radio._rx_ps_sleep_us = RX_POWERSAVING_CONTINUOUS_FALLBACK_US;
    else radio._rx_ps_sleep_us = 2000; // Below the actual 2600 us transition.
    assert(!radio.startReceivePowerSaving());
    for (unsigned i = 0; i < 100; ++i) { now_ms += 300000; radio.loop(); }
    assert(!radio.arm_calls && radio.continuous_calls == 1);
    const auto status = radio.getRxPowerSavingStatus();
    assert(radio._rx_ps_continuous_fallback && status.timing_fallback && !status.armed && !status.arm_failures);
    assert(status.last_error == (planned ? 0 : RX_POWERSAVING_ERR_TIMING));
    assert(!status.retry_pending && !status.retry_delay_ms);
    uint32_t wait = 53;
    assert(!radio.getRxPowerSavingRetryDelay(now_ms, wait) && wait == 53);
  }
  std::puts("PASS: RXPS diagnostics and deliberate timing fallback");
}

int main() {
  automaticRecoveryAndBoundedFailure();
  ownershipAndAdmission();
  longOwnerHoldsAndBoundedDefers();
  manualRetryAndStats();
  diagnosticTruthAndTimingFallback();
}
'''


def production_source():
    wrapper = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
    header = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text()
    methods = "\n\n".join(body(wrapper, signature) for signature in (
        "int RadioLibWrapper::startReceivePowerSaving()",
        "void RadioLibWrapper::stopReceiveDutyCycle()",
        "uint8_t RadioLibWrapper::beginReconfigure()",
        "void RadioLibWrapper::endReconfigure(bool resume_rx)",
        "void RadioLibWrapper::loop()",
        "bool RadioLibWrapper::rxPowerSavingRetryEligible() const",
        "void RadioLibWrapper::serviceRxPowerSavingRetry(uint32_t now)",
        "bool RadioLibWrapper::getRxPowerSavingRetryDelay(",
        "bool RadioLibWrapper::getReceiveRetryDelay(",
        "bool RadioLibWrapper::setRxPowerSaving(",
        "mesh::RadioParamApplyResult RadioLibWrapper::trySetRxPowerSaving(",
        "RxPowerSavingStatus RadioLibWrapper::getRxPowerSavingStatus()"))
    methods = methods.replace("uint8_t RadioLibWrapper::beginReconfigure() {",
        "uint8_t RadioLibWrapper::beginReconfigure() { ++begin_calls;")
    methods = methods.replace("void RadioLibWrapper::endReconfigure(bool resume_rx) {",
        "void RadioLibWrapper::endReconfigure(bool resume_rx) { ++end_calls;")
    return HARNESS.replace("@METHODS@", methods).replace(
        "@RESET_STATS@", body(header, "void resetStats()"))


class RxPowerSavingRuntimeTests(unittest.TestCase):
    def run_harness(self, source, expect_pass=True):
        with tempfile.TemporaryDirectory(prefix="meshcore-rxps-runtime-") as directory:
            cpp, binary = Path(directory) / "runtime.cpp", Path(directory) / "runtime"
            cpp.write_text(source, encoding="ascii")
            build = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if expect_pass:
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("PASS:", run.stdout)
            else:
                self.assertNotEqual(run.returncode, 0, "negative control unexpectedly passed")
                self.assertIn("Assertion", run.stderr)

    def test_actual_wrapper_rxps_retry_status_and_recovery(self):
        self.run_harness(production_source())

    def test_pacing_service_admission_and_diagnostic_negative_controls(self):
        source = production_source()
        mutations = (
            ("!_rx_ps_arm_retry.canAttempt(static_cast<uint32_t>(millis()))", "false"),
            ("_rx_ps_arm_retry.recordSuccess();", "/* omit successful-arm reset */"),
            ("serviceRxPowerSavingRetry(static_cast<uint32_t>(millis()));", "/* omit idle recovery */"),
            ("|| isChipBusy() || isReceivingPacket()", "|| false"),
            ("if (hasPendingRadioInterrupt()) return 2;", "/* lose IRQ arriving during activity probe */"),
            ("!_profile_repair_required && !retry_requested &&", "!_profile_repair_required && (retry_requested || true) &&"),
            ("if (resume_rx != 1) {", "if (resume_rx == 255) {"),
            ("if (err != RADIOLIB_ERR_NONE || !_rx_ps_enabled) _rx_ps_last_error = err;",
             "_rx_ps_last_error = err;"),
        )
        for original, replacement in mutations:
            with self.subTest(mutation=original):
                self.assertEqual(source.count(original), 1)
                self.run_harness(source.replace(original, replacement, 1), expect_pass=False)


if __name__ == "__main__":
    unittest.main()
