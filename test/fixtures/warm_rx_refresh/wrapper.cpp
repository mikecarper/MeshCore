#include <RadioProfiles.h>
#include <helpers/AGCResetScheduler.h>
#include <helpers/radiolib/AgessamanNoiseFloor.h>
#include <helpers/radiolib/WarmRxRefresh.h>

#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define MAX_TRANS_UNIT 255
#define NF_CALIB_SETTLE_MS 7UL

namespace mesh {
enum class AGCMaintenanceResult : uint8_t { Deferred, Failed, Applied };
enum class RadioParamApplyResult : uint8_t { APPLIED, BUSY, FAILED };
namespace radioDirection {
static bool allowed = true;
bool radioReceiveAllowed() { return allowed; }
}
}

struct RadioLibWrapper {
  uint8_t state = STATE_RX;
  RadioInterruptEvents events;
  CustomSX1262 chip;
  CustomSX1262* _radio = &chip;
  mesh::RadioProfiles _profiles;
  WarmRxRefresh _warm_refresh;
  @PERIODIC_OLD_FIELD@
  @LAB_TIMER_FIELDS@
  bool _cw_active = false, _beacon_owned = false, _beacon_restoring = false;
  bool _profile_repair_required = false, _profile_refresh_required = false;
  bool _params_valid = true, _rx_hold_continuous = false;
  bool _rx_ps_enabled = false, _rx_ps_armed = false, _rx_ps_continuous_fallback = false;
  bool _loop_event_pending = false;
  bool _nf_calib_active = false, _nf_refresh_requested = false, _nf_window_armed = false;
  bool _morse_owned = false, _morse_rx_owned = false;
  bool _noise_floor_valid = true, _noise_floor_secondary_valid = true;
  bool _rx_boosted_gain_valid = false, _rx_boosted_gain_applied = false, _cur_rx_boosted_gain = false;
  bool _profile_rxps_suspended = false, _rx_retry_pending = false;
  bool busy = false, packet = false, inject_probe_irq = false;
  uint8_t _active_profile = 0, _startrx_fails = 0;
  uint32_t _profile_generation = 1, _profile_visit_stamp = 0;
  uint32_t _profile_retry_at = 0, _nf_sample_from = 0, _rx_retry_at = 0;
  uint16_t _physical_preamble = 32, _rx_retry_delay_ms = 0;
  uint32_t _rx_ps_eff_rx_us = 50000, _rx_ps_eff_sleep_us = 50000;
  uint32_t _wd_last_transition = 0, _wd_stuck_thresh = 0, _wd_observe_ms = 0;
  bool _wd_last_busy = false;
  uint32_t n_wd_soft = 0, n_wd_hard = 0;
  uint32_t n_recv = 0, n_recv_errors = 0;
  uint32_t last_radio_interrupt_millis = 0, last_recv_millis = 0;
  float _last_snr = 0, _last_rssi = 0;
  int16_t _noise_floor = -105;
  AgessamanNoiseFloor _agessaman_floor;
  unsigned old_resets = 0, floor_resets = 0, reconfigure_ends = 0;
  RadioLibWrapper() {
    chip.events = &events;
    _profile_generation = _profiles.generation[0];
  }
  virtual ~RadioLibWrapper() = default;
  bool hasPendingRadioInterrupt() const { return events.pending(); }
  bool claimRadioInterrupt() { return events.claim(); }
  bool isChipBusy() { return busy; }
  bool isReceivingPacket() {
    if (inject_probe_irq) {
      events.record();
      chip.pending_fifo = true;
    }
    return packet;
  }
  bool isPacketPendingOrReceiving() { return hasPendingRadioInterrupt() || isReceivingPacket(); }
  bool isPacketReady() const { return _radio->pending_fifo; }
  void stopReceiveDutyCycle() { _rx_ps_armed = false; }
  bool isInRecvMode() const { return state == STATE_RX; }
  bool isCalibratingNoiseFloor() const { return _nf_calib_active; }
  uint32_t getRecoveryCount() const { return n_wd_soft + n_wd_hard; }
  bool getReceiveRetryDelay(uint32_t now, uint32_t& delay) const {
    if (!_rx_retry_pending) return false;
    const int32_t remaining = int32_t(_rx_retry_at - now);
    delay = remaining > 0 ? uint32_t(remaining) : 0;
    return true;
  }
  unsigned long getEstAirtimeFor(int) { return 10; }
  void recalibrateNoiseFloor() { ++floor_resets; }
  void resetAgessamanNoiseFloor() { ++floor_resets; _agessaman_floor.reset(); }
  void resetAGC() { ++old_resets; }
  void doResetAGC() { ++old_resets; }
  bool applyRxBoostedGainMode(bool) { return true; }
  bool tryResetAGC() {
    if (busy || isPacketPendingOrReceiving()) return false;
    resetAGC();
    return true;
  }
  bool resetAGCIfIdle(bool = true);
  bool usesAgessamanNoiseFloor() const;
  int startReceiveMode() { return _radio->startReceive(); }
  @BASE_SUPPORT@
  @LAB_TIMER_METHODS@
  virtual bool warmRxRefreshUsesFs() const { return false; }
  virtual void beginProfileRetune(bool) {}
  virtual void endProfileRetune(bool) {}
  virtual WarmRxRefresh::Outcome performWarmRxRefresh(WarmRxRefresh::Reason& why) {
    why = WarmRxRefresh::Reason::Policy;
    return WarmRxRefresh::Outcome::Deferred;
  }
  @WRAPPER_DECLARATIONS@
};

struct CustomSX1262Wrapper : RadioLibWrapper {
  bool _profile_standby_held = false, _saved_standby_xosc = false;
  @CUSTOM_WRAPPER_METHODS@
};

@WRAPPER_METHODS@
@DISPATCHER@
