
#define RADIOLIB_STATIC_ONLY 1
#include "RadioLibWrappers.h"
#include "RadioInterruptEvents.h"
#include "RadioProfileChip.h"
#include "ProfileSwitchTiming.h"
#include "NrfRadioPriorityLease.h"
#include "NrfRadioUsbIrqGuard.h"
#if MESH_RADIO_SWITCH_TUNING && (defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM) || defined(RP2040_PLATFORM))
#include "TunableRadioHal.h"
#define MESH_TUNABLE_RADIO_HAL 1
#endif
#if MESH_HIL_PROFILE_TIMING
namespace mesh { namespace profiletiming {
extern "C" {
volatile Capture mesh_hil_profile_timing = {
    Magic, Version, sizeof(Capture), 0, ClockHz, StageCount,
    HistogramCount, HistogramWidthUs, 0, 0, {}};
Work mesh_hil_profile_timing_work = {};
}
} }
#endif
#if MESH_ENABLE_TX_BEACONS
#include "RadioPowerLimits.h"
#endif
#include <new>
#if defined(ESP32_PLATFORM)
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <driver/gpio.h>
#include "../esp32/PowerTestControls.h"
#elif defined(NRF52_PLATFORM)
#include <utility/debug.h>
#include "../NRF52Board.h"
#elif defined(STM32_PLATFORM)
#include <sys/types.h>
extern "C" caddr_t _sbrk(int increment);
#endif

#define STATE_IDLE       0
#define STATE_RX         1
#define STATE_TX_WAIT    3
#define STATE_TX_DONE    4

// Bounded noise-floor calibration in continuous RX, including RXPS refreshes.
// Requests are coalesced so retries cannot repeatedly force continuous RX.
#define NF_CALIB_INTERVAL_MS  (60UL * 1000UL)
#define NF_RXPS_REFRESH_INTERVAL_MS (60UL * 1000UL)
// A successful baseline need not hold the radio in continuous RX every minute.
// Rejected rise/failed attempts retain the shorter retry, not this lazy cadence.
#define NF_LAZY_REFRESH_INTERVAL_MS (5UL * 60UL * 1000UL)
#define NF_CALIB_TIMEOUT_MS   NoiseFloorEstimator::WINDOW_TIMEOUT_MS
#define NF_CONTINUOUS_TIMEOUT_MS NoiseFloorEstimator::WINDOW_TIMEOUT_MS
#define NF_CALIB_SETTLE_MS    RadioLibWrapper::NoiseFloorSettleMillis
// A profile normally visited for less than its RSSI settle time stays fast
// during ordinary scanning. Refresh its floor with one bounded slow-visit
// sample set every fifteen minutes instead.
#define NF_FAST_PROFILE_REFRESH_INTERVAL_MS (15UL * 60UL * 1000UL)

// RadioLib callbacks carry no context. The internal STM32WLx radio needs one
// physical slot; other classes retain two, including external STM32 radios.
// Dual-profile scanning uses one slot. ISR storage has static lifetime, so a
// callback already in flight cannot access a destroyed/rebound wrapper.
#define RADIO_INTERRUPT_INTERNAL_CustomSTM32WLx 1
#define RADIO_INTERRUPT_INTERNAL_(radio) RADIO_INTERRUPT_INTERNAL_##radio
#define RADIO_INTERRUPT_INTERNAL(radio) RADIO_INTERRUPT_INTERNAL_(radio)
#if RADIO_INTERRUPT_INTERNAL(RADIO_CLASS)
  #define RADIO_INTERRUPT_SLOT_COUNT 1
#else
  #define RADIO_INTERRUPT_SLOT_COUNT 2
#endif
#undef RADIO_INTERRUPT_INTERNAL
#undef RADIO_INTERRUPT_INTERNAL_
#undef RADIO_INTERRUPT_INTERNAL_CustomSTM32WLx
static RadioInterruptEvents radio_interrupt_events[RADIO_INTERRUPT_SLOT_COUNT];
static RadioLibWrapper* radio_interrupt_owners[RADIO_INTERRUPT_SLOT_COUNT] = {};


// Extend the 24-bit RTC counter on each loop poll. A valid explicit preamble
// can require a visit longer than its 512-second hardware wrap period.
#if defined(NRF52_PLATFORM)
static uint32_t profile_clock_raw = 0;
static uint32_t profile_clock_ticks = 0;
#endif

static void syncProfileClock(bool dual_profile) {
#if defined(NRF52_PLATFORM)
  static bool active = false;
  if (active == dual_profile) return;
  if (dual_profile) {
    NRF_RTC2->TASKS_STOP = 1;
    NRF_RTC2->PRESCALER = 0;
    // Keep the retained hardware count: an asynchronous clear could otherwise
    // look like a wrap before its first poll.
    profile_clock_raw = NRF_RTC2->COUNTER & 0x00ffffffUL;
    profile_clock_ticks = 0;
    NRF_RTC2->TASKS_START = 1;
  } else {
    NRF_RTC2->TASKS_STOP = 1;
  }
  active = dual_profile;
#else
  (void) dual_profile;
#endif
}

static uint32_t profileTimestamp(bool dual_profile) {
  syncProfileClock(dual_profile);
#if defined(NRF52_PLATFORM)
  if (dual_profile) {
    const uint32_t raw = NRF_RTC2->COUNTER & 0x00ffffffUL;
    profile_clock_ticks += (raw - profile_clock_raw) & 0x00ffffffUL;
    profile_clock_raw = raw;
    return profile_clock_ticks;
  }
#endif
#if defined(ESP32_PLATFORM)
  // Unlike a cycle counter, this domain includes elapsed light-sleep time.
  return static_cast<uint32_t>(esp_timer_get_time());
#else
  return micros();
#endif
}

static uint32_t profileElapsedUs(uint32_t since, uint32_t now, bool dual_profile) {
#if defined(NRF52_PLATFORM)
  if (!dual_profile) return now - since;
  const uint32_t ticks = now - since;
  const uint64_t elapsed = (uint64_t(ticks) * 15625 + 511) >> 9;
  return elapsed > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(elapsed);
#else
  (void) dual_profile;
  return now - since;
#endif
}

static
#if defined(ESP8266) || defined(ESP32)
  ICACHE_RAM_ATTR
#endif
void setFlag0() { radio_interrupt_events[0].record(); }
#if RADIO_INTERRUPT_SLOT_COUNT > 1
static
#if defined(ESP8266) || defined(ESP32)
  ICACHE_RAM_ATTR
#endif
void setFlag1() { radio_interrupt_events[1].record(); }
#endif

bool RadioLibWrapper::registerInterruptAction() {
  _loop_event_pending = false;
#if RADIO_INTERRUPT_SLOT_COUNT == 1
  // There is only one integrated radio. Ownership is still checked so a
  // second wrapper cannot steal its callback or consume its pending event.
  if (radio_interrupt_owners[0] != nullptr && radio_interrupt_owners[0] != this) {
    _interrupt_slot = 0xff;
    return false;
  }
  radio_interrupt_owners[0] = this;
  _interrupt_slot = 0;
  _radio->clearPacketReceivedAction();
  radio_interrupt_events[0].claim();
  _radio->setPacketReceivedAction(setFlag0);
#else
  if (_interrupt_slot >= RADIO_INTERRUPT_SLOT_COUNT || radio_interrupt_owners[_interrupt_slot] != this) {
    _interrupt_slot = 0xff;
    for (uint8_t i = 0; i < RADIO_INTERRUPT_SLOT_COUNT; ++i) {
      if (radio_interrupt_owners[i] == nullptr) {
        radio_interrupt_owners[i] = this;
        _interrupt_slot = i;
        break;
      }
    }
  }
  if (_interrupt_slot >= RADIO_INTERRUPT_SLOT_COUNT) return false;
  _radio->clearPacketReceivedAction();
  radio_interrupt_events[_interrupt_slot].claim();
  _radio->setPacketReceivedAction(_interrupt_slot == 0 ? setFlag0 : setFlag1);
#endif
  return true;
}

void RadioLibWrapper::unregisterInterruptAction() {
#if RADIO_INTERRUPT_SLOT_COUNT == 1
  if (radio_interrupt_owners[0] == this) {
    _radio->clearPacketReceivedAction();
    radio_interrupt_owners[0] = nullptr;
  }
#else
  if (_interrupt_slot < RADIO_INTERRUPT_SLOT_COUNT && radio_interrupt_owners[_interrupt_slot] == this) {
    _radio->clearPacketReceivedAction();
    radio_interrupt_owners[_interrupt_slot] = nullptr;
  }
#endif
  _interrupt_slot = 0xff;
}

bool RadioLibWrapper::hasAssertedRadioInterrupt() const {
#if defined(ESP32_PLATFORM)
  // GPIO wake does not guarantee that the normal edge ISR ran during light
  // sleep. Retain a completed packet even when DIO is already high after
  // restoring its edge mode. Read this PHY's pin, never a shared board pin.
  if (_interrupt_slot >= RADIO_INTERRUPT_SLOT_COUNT
      || radio_interrupt_owners[_interrupt_slot] != this || _radio == nullptr) return false;
  Module* module = _radio->getMod();
  if (module == nullptr || module->hal == nullptr) return false;
  const uint32_t irq = module->getIrq();
  // GPIO_IS_VALID_GPIO shifts by its argument on older IDF versions, so its
  // mask check must follow the range check even for an invalid/NC module pin.
  if (irq == RADIOLIB_NC || irq >= SOC_GPIO_PIN_COUNT || !GPIO_IS_VALID_GPIO(irq)) return false;
  #if MESH_RADIO_TRACE_ENABLED
  const bool tracing = mesh::radiotrace::trace().pending();
  const uint64_t before = tracing ? mesh::radiotrace::nowUs() : 0;
  #endif
  const bool asserted = module->hal->digitalRead(irq) == module->hal->GpioLevelHigh;
  #if MESH_RADIO_TRACE_ENABLED
  if (tracing) mesh::radiotrace::trace().dio(_radio, before, mesh::radiotrace::nowUs(), asserted);
  #endif
  return asserted;
#else
  return false;
#endif
}

bool RadioLibWrapper::hasPendingRadioInterrupt() const {
  return _loop_event_pending || (_interrupt_slot < RADIO_INTERRUPT_SLOT_COUNT && radio_interrupt_events[_interrupt_slot].pending())
      || hasAssertedRadioInterrupt();
}

bool RadioLibWrapper::claimRadioInterrupt() {
  const bool startup = _loop_event_pending;
  _loop_event_pending = false;
  const bool interrupt = _interrupt_slot < RADIO_INTERRUPT_SLOT_COUNT && radio_interrupt_events[_interrupt_slot].claim();
  // Always claim the software event too, even when the physical pin is high.
  // recvRaw still verifies RX_DONE before reading FIFO, including a late ISR.
  return startup || interrupt || hasAssertedRadioInterrupt();
}

void RadioLibWrapper::begin() {
#if MESH_ENABLE_RADIO_DIRECTION
  _direction_receive_disabled = false;
#endif
#if MESH_RADIO_TRACE_ENABLED
  mesh::radiotrace::trace().context(_radio, _active_profile, _profile_generation);
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return;
#endif
  _dbm_applied = false;
#if MESH_HIL_PROFILE_TIMING
  mesh::profiletiming::initializeClock();
#endif
  (void)profileTimestamp(_profiles.enabled());
#ifdef USE_CC310_HW_CRYPTO
  // Initialize CryptoCell once from normal task context. The session helper
  // still initializes lazily if an earlier crypto operation runs first.
  (void) mesh::initializeCC310Crypto();
#endif
  if (!registerInterruptAction()) {
    MESH_DEBUG_PRINTLN("RadioLibWrapper: no interrupt callback slot");
    return;
  }
  _radio->setPreambleLength(currentPreambleLength()); // longer preamble for lower SF improves reliability
#if defined(LORA_FREQ) && defined(LORA_BW) && defined(LORA_SF)
  if (!_params_valid) {
  #ifdef LORA_CR
    const uint8_t initial_cr = LORA_CR;
  #else
    const uint8_t initial_cr = 5;
  #endif
    cacheParams(LORA_FREQ, LORA_BW, LORA_SF, initial_cr);
    _profiles.primary.freq = LORA_FREQ;
    _profiles.primary.bw = LORA_BW;
    _profiles.primary.sf = LORA_SF;
    _profiles.primary.cr = initial_cr;
  }
#endif
#ifdef LORA_TX_POWER
  if (!_dbm_valid) {
    _cur_dbm = LORA_TX_POWER;
    _dbm_valid = true;
  }
#endif
  state = STATE_IDLE;
  _rx_retry_at = 0;
  _rx_retry_delay_ms = 0;
  _rx_retry_pending = false;

  if (_board->getStartupReason() == BD_STARTUP_RX_PACKET) {  // received a LoRa packet (while in deep sleep)
    _loop_event_pending = true; // Main-owned wake event; ISR remains sole publisher
  }

  _noise_floor = 0;
  _noise_floor_centi_dbm = 0;
  _noise_floor_secondary_centi_dbm = 0;
  _noise_floor_valid = false;
  _noise_floor_secondary_valid = false;
#if MESH_ENABLE_TELEMETRY_HISTORY
  _nf_published_valid = 0;
#endif
  _threshold = 0;
  _cad_enabled = false;
  _rx_mode_checked_at = millis();
  _rx_mode_failures = 0;
  _last_rssi = 0;
  _last_snr = 0;
  _rx_hold_continuous = false;

  // start average out some samples
  _floor_estimator.reset(true);
  _secondary_floor_estimator.reset(true);
  _nf_calib_active = false;
  _nf_window_armed = _nf_lazy_refresh = _nf_refresh_attempted = false;
  _nf_last_calib = 0;
  _nf_sample_from = 0;
  _nf_refresh_requested = true;  // establish one baseline after startup
  // Arm the bounded sampling window only after the radio actually reaches RX.
  // Companion initialization can take longer than the window itself.
  _nf_calib_deadline = 0;
}

uint32_t RadioLibWrapper::getRngSeed() {
  return _radio->random(0x7FFFFFFF);
}

bool RadioLibWrapper::setTxPower(int8_t dbm) {
  return trySetTxPower(dbm) == mesh::RadioParamApplyResult::APPLIED;
}

bool RadioLibWrapper::isTxPowerValid(int8_t dbm) const {
#ifdef MIN_LORA_TX_POWER
  if (dbm < MIN_LORA_TX_POWER) return false;
#endif
#ifdef MAX_LORA_TX_POWER
  if (dbm > MAX_LORA_TX_POWER) return false;
#endif
#if MESH_RADIO_PROFILE_CHIP == 1121 || MESH_RADIO_PROFILE_CHIP == 2021
  // These drivers intentionally clip desired power to the physically selected
  // band's PA limit. A temporary/radio2 band must not reject saved intent.
  return true;
#else
  return checkBeaconOutputPower(_cur_freq, dbm);
#endif
}

bool RadioLibWrapper::isTxPowerApplied(int8_t dbm) const {
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return false;
#endif
  return !_cw_active && _dbm_valid && _dbm_applied && _cur_dbm == dbm
      && !_profile_repair_required;
}

mesh::RadioParamApplyResult RadioLibWrapper::trySetTxPower(int8_t dbm) {
  using Result = mesh::RadioParamApplyResult;
  if (!isTxPowerValid(dbm)) return Result::FAILED;
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return Result::BUSY;
#endif
#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
  // A CW power-meter sweep may re-key the carrier. During Morse that would
  // turn an inter-symbol gap into RF and corrupt the text, so keep the whole
  // message exclusive (including cleanup retries).
  if ((_morse_owned
#if MESH_ENABLE_MORSE_RX
      || _morse_rx_owned
#endif
      ) && _cw_active) return Result::BUSY;
#if MESH_ENABLE_CARRIER_TOOLS
  if (_cw_active) {
    if (!mesh::radioDirection::radioTransmitAllowed()) {
      stopCarrierWave();
      return Result::FAILED;
    }
    if (_cw_stopping) return Result::BUSY;
#if defined(MESH_HIL_TX_FREQ_MHZ)
    if (!hilPacketTransmitAllowed() || _cw_profile != _active_profile
        || _cw_generation != _profile_generation) return Result::FAILED;
#endif
    // Permit meter sweeps through the board's normal power/PA calibration.
    // SX127x's power setter enters standby, so re-key without resetting the
    // modem or extending the original CW deadline.
    _dbm_applied = applyCachedTxPower(dbm) == RADIOLIB_ERR_NONE;
    if (!_dbm_applied) {
      stopCarrierWave();
      return Result::FAILED;
    }
    if (_radio->transmitDirect() != RADIOLIB_ERR_NONE) {
      stopCarrierWave();
      return Result::FAILED;
    }
    _cur_dbm = dbm;
    _dbm_valid = true;
    return Result::APPLIED;
  }
#else
  if (_cw_active) return Result::BUSY;
#endif
#endif
  // Settings screens also submit TX power when only another field changed.
  // The verified normal power needs no SPI, standby, or interruption of RX.
  if (_dbm_valid && _dbm_applied && _cur_dbm == dbm
      && !_profile_repair_required) return Result::APPLIED;
  const uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return resume_rx == 2 ? Result::BUSY : Result::FAILED;

  const int16_t status = applyCachedTxPower(dbm);
  _dbm_applied = status == RADIOLIB_ERR_NONE;
  if (status == RADIOLIB_ERR_NONE) {
    _cur_dbm = dbm;
    _dbm_valid = true;
  } else {
    MESH_DEBUG_PRINTLN("RadioLibWrapper: TX power %d rejected (%d)",
                       (int)dbm, (int)status);
  }
  endReconfigure(resume_rx);
  return status == RADIOLIB_ERR_NONE ? Result::APPLIED : Result::FAILED;
}

uint8_t RadioLibWrapper::beginReconfigure() {
#if MESH_HIL_PROFILE_TIMING
  mesh::profiletiming::ReconfigureScope profile_timing;
#endif
#if MESH_RADIO_TRACE_ENABLED
  mesh::radiotrace::trace().context(_radio, _active_profile, _profile_generation);
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned && !_beacon_restoring) {
#if defined(USE_LR2021)
    noteReconfigureAdmission("beacon-owned", 0);
#endif
    return 2;
  }
#endif
  if (_cw_active) {
#if defined(USE_LR2021)
    noteReconfigureAdmission("carrier-owned", 0);
#endif
    return 2;
  }
#if defined(USE_LR2021) && (MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX)
  if (_morse_owned
#if MESH_ENABLE_MORSE_RX
      || _morse_rx_owned
#endif
     ) {
    noteReconfigureAdmission("morse-owned", 0);
    return 2;
  }
#endif
  const uint8_t base_state = state;
  if (_profile_repair_required) {
    // The last transaction failed after changing the chip. Its old FIFO and
    // activity flags have no trustworthy modulation/profile ownership.
    if (base_state == STATE_TX_WAIT || isChipBusy()) return 2;
    claimRadioInterrupt();
    _rx_hold_continuous = false;
#if MESH_HIL_PROFILE_TIMING
    profile_timing.admissionComplete();
#endif
    const int16_t standby_status =
#if MESH_HIL_PROFILE_TIMING
        profile_timing.standbyResult(_radio->standby());
#else
        _radio->standby();
#endif
    if (standby_status != RADIOLIB_ERR_NONE) {
#if defined(USE_LR2021)
      noteReconfigureAdmission("standby", standby_status);
#endif
      state = STATE_IDLE;
      return 3;
    }
    _rx_ps_armed = false;
    claimRadioInterrupt();
    state = STATE_IDLE;
    // A receive-capable repair verifies re-entry; TX-only repairs stop after
    // restoring the modem, without opening an unsolicited receive window.
#if MESH_ENABLE_RADIO_DIRECTION
    return mesh::radioDirection::radioReceiveAllowed() ? 1 : 0;
#else
    return 1;
#endif
  }
  // On SX126x/LR11xx duty-cycle RX, BUSY may remain asserted during the sleep
  // side of the cycle. Do not issue an IRQ/preamble query over SPI then; due
  // scheduled work will retry as soon as the next safe listen window opens.
#if defined(USE_LR2021)
  if (hasPendingRadioInterrupt()) { noteReconfigureAdmission("pending-irq", 0); return 2; }
  if (base_state == STATE_TX_WAIT) { noteReconfigureAdmission("transmitting", 0); return 2; }
  if (isChipBusy()) { noteReconfigureAdmission("chip-busy", 0); return 2; }
  if (isReceivingPacket() && !_primary_receive_preempt) {
    noteReconfigureAdmission("receiving", 0);
    return 2;
  }
  // A completion can race the activity probe. Stop only unfinished RX; never
  // claim/clear an old-profile FIFO in order to apply a command faster.
  if (hasPendingRadioInterrupt()) { noteReconfigureAdmission("pending-irq", 0); return 2; }
#else
  if (hasPendingRadioInterrupt() || base_state == STATE_TX_WAIT
      || isChipBusy() || isReceivingPacket()) {
    return 2;
  }
#endif

#if MESH_HIL_PROFILE_TIMING
  profile_timing.admissionComplete();
#endif
  const bool was_rx = base_state == STATE_RX;
  const bool resume_rx = was_rx
#if MESH_ENABLE_RADIO_DIRECTION
      && mesh::radioDirection::radioReceiveAllowed()
#endif
      ;
  if (_rx_ps_armed || was_rx) {
    const int16_t standby_status =
#if MESH_HIL_PROFILE_TIMING
        profile_timing.standbyResult(_radio->standby());
#else
        _radio->standby();
#endif
    if (standby_status != RADIOLIB_ERR_NONE) {
#if defined(USE_LR2021)
      noteReconfigureAdmission("standby", standby_status);
#endif
      state = STATE_IDLE;
      _profile_refresh_required = _profile_repair_required = true;
      return 3;
    }
    _rx_ps_armed = false;
  }
  // An RX completion during standby still owns its FIFO and old profile.
  // Defer reconfiguration until recvRaw consumes it, rather than rearming and
  // clearing that completion as part of endReconfigure().
  if (hasPendingRadioInterrupt()) {
#if defined(USE_LR2021)
    noteReconfigureAdmission("pending-irq", 0);
#endif
    return 2;
  }
  state = STATE_IDLE;
#if defined(USE_LR2021)
  noteReconfigureAdmission("admitted", 0);
#endif
  return resume_rx;
}

void RadioLibWrapper::endReconfigure(bool resume_rx) {
  if (resume_rx) startRecv();
}

#if MESH_RADIO_SWITCH_TUNING
mesh::RadioSwitchCapabilities RadioLibWrapper::switchTuningCapabilities() const {
  mesh::RadioSwitchCapabilities caps;
#if MESH_TUNABLE_RADIO_HAL
  auto* hal = TunableRadioHal::find(_radio->getMod()->hal);
  if (hal) caps = hal->capabilities();
#endif
  caps.flags |= driverSwitchCapabilities();
  return caps;
}

mesh::RadioSwitchTuning RadioLibWrapper::switchTuning() const {
  mesh::RadioSwitchTuning value;
#if MESH_TUNABLE_RADIO_HAL
  auto* hal = TunableRadioHal::find(_radio->getMod()->hal);
  if (hal) value = hal->getTuning();
#endif
  value.flags |= getDriverSwitchFeatures();
  return value;
}

uint32_t RadioLibWrapper::switchSpiHz() const {
#if MESH_TUNABLE_RADIO_HAL
  auto* hal = TunableRadioHal::find(_radio->getMod()->hal);
  if (hal) return hal->effectiveHz();
#endif
  return 0;
}

mesh::RadioParamApplyResult RadioLibWrapper::trySetSwitchTuning(
    const mesh::RadioSwitchTuning& value,
    bool (*persist)(void*, const mesh::RadioSwitchTuning&), void* context) {
  using Result = mesh::RadioParamApplyResult;
  if (!mesh::validRadioSwitchTuning(value, switchTuningCapabilities())) return Result::FAILED;
  const auto previous = switchTuning();
  if (value == previous) return !persist || persist(context, value) ? Result::APPLIED : Result::FAILED;
  const uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return resume_rx == 2 ? Result::BUSY : Result::FAILED;

  // These setters change policy/SPISettings only, never an SPI peripheral or
  // pin mapping. Clearing owned caches forces the next retune to prove RX.
  configureBootSwitchTuning(value);
  _profiles.resetSwitchTest();
  endReconfigure(resume_rx);
  const bool resumed = !resume_rx || isInRecvMode();
  if (resumed && (!persist || persist(context, value))) return Result::APPLIED;

  // There is no SPI in these setters, including after an RX completion was
  // queued during a filesystem save. Never rearm or clear that unread FIFO.
  configureBootSwitchTuning(previous);
  if (!resumed) endReconfigure(resume_rx);
  return Result::FAILED;
}

bool RadioLibWrapper::configureBootSwitchTuning(const mesh::RadioSwitchTuning& value) {
  if (!mesh::validRadioSwitchTuning(value, switchTuningCapabilities())) return false;
#if MESH_TUNABLE_RADIO_HAL
  auto* hal = TunableRadioHal::find(_radio->getMod()->hal);
  if (hal && !hal->setTuning(value)) return false;
#endif
  setDriverSwitchFeatures(value.flags);
  return true;
}
#endif

bool RadioLibWrapper::restoreAfterDeepInit() {
#if MESH_ENABLE_RADIO_DIRECTION
  _direction_receive_disabled = false;
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) cacheBeaconRestoreParams();
#endif
  _dbm_applied = false;
  _rx_boosted_gain_applied = false;
  if (!radioDeepInit()) return false;

  _rx_ps_armed = false;
  state = STATE_IDLE;
  if (!registerInterruptAction()) return false;

  bool restored;
  if (_params_valid) {
    restored = applyParams(_cur_freq, _cur_bw, _cur_sf, _cur_cr);
  } else {
    restored = _radio->setPreambleLength(currentPreambleLength())
        == RADIOLIB_ERR_NONE;
  }
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  restored = restored && applyProtocol(_physical_protocol);
#endif
  if (_dbm_valid) {
    _dbm_applied = applyCachedTxPower(_cur_dbm) == RADIOLIB_ERR_NONE;
    restored = _dbm_applied && restored;
  }
  if (_rx_boosted_gain_valid) {
    _rx_boosted_gain_applied = applyRxBoostedGainMode(_cur_rx_boosted_gain);
    restored = _rx_boosted_gain_applied && restored;
  }
  recalibrateNoiseFloor();
  if (restored) _profile_repair_required = false;
  return restored;
}

mesh::RadioParamApplyResult RadioLibWrapper::trySetParams(float freq, float bw, uint8_t sf, uint8_t cr,
                                                          const uint32_t* rx_ps_timings) {
  if (rx_ps_timings != NULL && !supportsRxPowerSaving()) {
    return mesh::RadioParamApplyResult::FAILED;
  }

  uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return resume_rx == 2
      ? mesh::RadioParamApplyResult::BUSY : mesh::RadioParamApplyResult::FAILED;

  const bool had_previous_params = _params_valid;
  const float previous_freq = _cur_freq;
  const float previous_bw = _cur_bw;
  const uint8_t previous_sf = _cur_sf;
  const uint8_t previous_cr = _cur_cr;
  const uint16_t previous_preamble = _physical_preamble;
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  const auto previous_protocol = _physical_protocol;
#endif
  const uint8_t previous_profile = _active_profile;
  const uint32_t previous_generation = _profile_generation;
  const bool previous_refresh = _profile_refresh_required;
  const bool previous_rxps = _rx_ps_enabled;
  const bool previous_saved_rxps = _profile_saved_rxps;
  const bool previous_fallback = _rx_ps_continuous_fallback;
  const uint32_t previous_rx_us = _rx_ps_rx_us, previous_sleep_us = _rx_ps_sleep_us;
  mesh::RadioProfileParams requested = _profiles.primary;
  requested.freq = freq; requested.bw = bw; requested.sf = sf; requested.cr = cr;
  const auto previous_primary = _profiles.primary;
  _profiles.primary = requested;
  _physical_preamble = profilePreamble(0);

  bool success = applyParams(freq, bw, sf, cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  success = success && applyProtocol(requested.protocol);
  if (success) _physical_protocol = requested.protocol;
#endif
  if (success) {
    cacheParams(freq, bw, sf, cr);
    _active_profile = 0;
    if (rx_ps_timings != NULL) {
      if (_profile_rxps_suspended) _profile_saved_rxps = true;
      else _rx_ps_enabled = true;
      _rx_ps_rx_us = rx_ps_timings[0];
      _rx_ps_sleep_us = rx_ps_timings[1];
      _rx_ps_arm_retry.reset();
      _rx_ps_last_error = RADIOLIB_ERR_NONE;
      _rx_ps_continuous_fallback = rxPowerSavingUsesContinuousFallback(
          _rx_ps_rx_us, _rx_ps_sleep_us);
    }
    _profile_repair_required = false;
    endReconfigure(resume_rx);
    success = !resume_rx || isInRecvMode();
  }
  if (success) {
    _profiles.primary = previous_primary;
    _profiles.setPrimary(requested, _profiles.primary_temporary);
    _profile_generation = _profiles.generation[0];
    _profile_refresh_required = false;
  } else {
    // RX resume is part of a primary command just as it is for scan hops.
    // Restore the active physical profile and power policy before recovery;
    // neither a saved command nor a temporary lease may commit a deaf radio.
    _profiles.primary = previous_primary;
    cacheParams(previous_freq, previous_bw, previous_sf, previous_cr);
    _params_valid = had_previous_params;
    _active_profile = previous_profile;
    _profile_generation = previous_generation;
    _profile_refresh_required = previous_refresh;
    _physical_preamble = previous_preamble;
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    _physical_protocol = previous_protocol;
#endif
    _rx_ps_enabled = previous_rxps;
    _profile_saved_rxps = previous_saved_rxps;
    _rx_ps_continuous_fallback = previous_fallback;
    _rx_ps_rx_us = previous_rx_us;
    _rx_ps_sleep_us = previous_sleep_us;
    bool restored = had_previous_params
      && _radio->standby() == RADIOLIB_ERR_NONE
      && applyParams(previous_freq, previous_bw, previous_sf, previous_cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    restored = restored && applyProtocol(previous_protocol);
#endif

    if (!restored) restored = restoreAfterDeepInit();
    if (restored) {
      _profile_repair_required = false;
      endReconfigure(resume_rx);
    }
    if (!restored || (resume_rx && !isInRecvMode()))
      _profile_refresh_required = _profile_repair_required = true;

    if (!restored) {
      MESH_DEBUG_PRINTLN("RadioLibWrapper: failed to restore radio parameters after apply failure");
    }
  }

#if defined(ESP32_PLATFORM)
  _esp32_profile_visit_started_us = static_cast<uint64_t>(esp_timer_get_time());
  _profile_visit_stamp = static_cast<uint32_t>(_esp32_profile_visit_started_us);
#else
  _profile_visit_stamp = profileTimestamp(_profiles.enabled());
#endif
  return success ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::FAILED;
}

bool RadioLibWrapper::validateProfile(const mesh::RadioProfileParams& p) const {
#if MESH_RADIO_PROFILE_CHIP != 1110 && MESH_RADIO_PROFILE_CHIP != 1121 && MESH_RADIO_PROFILE_CHIP != 2021 && MESH_RADIO_PROFILE_CHIP != 68 && MESH_RADIO_PROFILE_CHIP != 1272
  if (!mesh::RadioProfiles::valid(p, false)) return false;
#else
  if (!mesh::RadioProfiles::valid(p)) return false;
#endif
  if (p.protocol == mesh::RadioProfileProtocol::Meshtext && !supportsMeshtext()) return false;
#if MESH_ENABLE_HOST_KISS
  if (p.protocol == mesh::RadioProfileProtocol::Kiss && !supportsHostKiss()) return false;
#else
  if (p.protocol == mesh::RadioProfileProtocol::Kiss) return false;
#endif
#if MESH_ENABLE_ETHERNET_WIRE
  if (p.protocol == mesh::RadioProfileProtocol::Ethernet && !supportsEthernetTunnel()) return false;
#else
  if (p.protocol == mesh::RadioProfileProtocol::Ethernet) return false;
#endif
#if MESH_RADIO_PROFILE_CHIP == 1276
  return p.freq >= 137 && p.freq <= 1020 && p.sf >= 7 && p.bw <= 500;
#elif MESH_RADIO_PROFILE_CHIP == 1272
  return p.freq >= 860 && p.freq <= 1020 && p.sf >= 7
      && (p.bw == 125 || p.bw == 250 || p.bw == 500);
#elif MESH_RADIO_PROFILE_CHIP == 68
  return p.freq >= 150 && p.freq <= 960
      && ((p.bw == 125 && p.sf <= 9) || (p.bw == 250 && p.sf <= 10)
          || (p.bw == 500 && p.sf <= 11));
#elif MESH_RADIO_PROFILE_CHIP == 2021
  static constexpr float bandwidths[] = {31.25f, 41.67f, 41.7f, 62.5f, 83.34f, 101.56f,
                                        125, 203.125f, 250, 406.25f, 500, 812.5f, 1000};
  bool supported = false;
  for (float bw : bandwidths) if (fabsf(p.bw - bw) < 0.01f) supported = true;
  return ((p.freq >= 150 && p.freq <= 1090) || (p.freq >= 1900 && p.freq <= 2200)
      || (p.freq >= 2400 && p.freq <= 2500)) && supported;
#elif MESH_RADIO_PROFILE_CHIP == 1110
  return p.freq >= 150 && p.freq <= 960
      && (p.bw == 62.5 || p.bw == 125 || p.bw == 250 || p.bw == 500);
#elif MESH_RADIO_PROFILE_CHIP == 1121
  return ((p.freq >= 150 && p.freq <= 960) || (p.freq >= 1900 && p.freq <= 2200)
      || (p.freq >= 2400 && p.freq <= 2500))
      && (p.bw == 62.5 || p.bw == 125 || p.bw == 250 || p.bw == 500
          || (p.freq >= 1900 && (p.bw == 203.125f || p.bw == 406.25f || p.bw == 812.5f)));
#elif MESH_RADIO_PROFILE_CHIP == 1268
  return p.freq >= 410 && p.freq <= 810 && p.bw <= 500;
#else
  return p.freq >= 150 && p.freq <= 960 && p.bw <= 500;
#endif
}

mesh::RadioParamApplyResult RadioLibWrapper::tuneProfile(uint8_t profile) {
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned && !_beacon_restoring) return mesh::RadioParamApplyResult::BUSY;
#endif
  if (_cw_active) return mesh::RadioParamApplyResult::BUSY;
  if (profile > 1 || (profile == 1 && !_profiles.enabled())) return mesh::RadioParamApplyResult::FAILED;
#if defined(ESP32_PLATFORM) && !defined(SENSECAP_INDICATOR_LORA)
  bool clock_changed = false;
  if (!_board->prepareRadioProfileCpuClock(_profiles.enabled(), clock_changed))
    return mesh::RadioParamApplyResult::BUSY;
  if (clock_changed) _profiles.resetSwitchTest();
#endif
  const auto& p = _profiles.params(profile);
  const uint16_t preamble = profilePreamble(profile);
  if (!_profile_refresh_required && !_profile_repair_required && _active_profile == profile && _profile_generation == _profiles.generation[profile]
      && _physical_preamble == preamble) return mesh::RadioParamApplyResult::APPLIED;
  if (!validateProfile(p)) return mesh::RadioParamApplyResult::FAILED;
  const bool dual_profile = _profiles.enabled();
  const uint32_t started = profileTimestamp(dual_profile);
#if MESH_HIL_PROFILE_TIMING
  mesh::profiletiming::begin(_active_profile, profile, mesh::profiletiming::readClock());
#endif
  beginProfileRetune(isInRecvMode() && !_rx_ps_armed && !_rx_ps_enabled);
#if MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD
  // The combined HIL experiment must admit the IRQ guard while the loop
  // still has its ordinary priority; its deadline remains independent.
  mesh::radiousb::Guard profile_usb_irq(dual_profile && _active_profile != profile
      && ownsWarmProfileRetune());
#endif
#if MC_NRF_RADIO_PROFILE_PRIORITY
  mesh::radiopriority::Lease profile_priority(dual_profile && _active_profile != profile
      && ownsWarmProfileRetune());
#endif
#if MC_NRF_RADIO_PROFILE_COMBINED
  // Explicit exits release in this order below. Preserve it for any future
  // early exit as well, before reverse member/local destruction can let a
  // queued USB worker run while its IRQ is still deferred.
  struct OrderedProfileRelease {
    mesh::radiousb::Guard& usb;
    mesh::radiopriority::Lease& priority;
    ~OrderedProfileRelease() { usb.release(); priority.release(); }
  } ordered_profile_release{profile_usb_irq, profile_priority};
#endif
  const uint8_t resume = beginReconfigure();
  if (resume > 1) {
    endProfileRetune(resume == 2);  // Only a BUSY guard deferred before changing hardware.
#if MESH_HIL_PROFILE_TIMING
    const uint32_t finished_ticks = mesh::profiletiming::readEndpointClock();
#if MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD
    profile_usb_irq.release();
#endif
#if MC_NRF_RADIO_PROFILE_PRIORITY
    profile_priority.release();
#endif
    mesh::profiletiming::finish(resume == 2 ? mesh::profiletiming::Busy
        : mesh::profiletiming::Failure, finished_ticks);
#endif
    return resume == 2 ? mesh::RadioParamApplyResult::BUSY : mesh::RadioParamApplyResult::FAILED;
  }
  const float old_freq = _cur_freq, old_bw = _cur_bw;
  const uint8_t old_sf = _cur_sf, old_cr = _cur_cr, old_profile = _active_profile;
  const uint32_t old_generation = _profile_generation;
  const bool old_refresh = _profile_refresh_required;
  const bool old_params_valid = _params_valid;
  const uint16_t old_preamble = _physical_preamble;
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  const auto old_protocol = _physical_protocol;
#endif
  _physical_preamble = preamble;
  bool applied = applyParams(p.freq, p.bw, p.sf, p.cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  // Changing only the protocol still changes the sync word. Conversely a
  // failed write must not publish the new generation or cached wire format.
  if (applied && (_physical_protocol != p.protocol || _profile_refresh_required)) {
    applied = applyProtocol(p.protocol);
  }
  if (applied) _physical_protocol = p.protocol;
#endif
  if (applied) {
    cacheParams(p.freq, p.bw, p.sf, p.cr);
    _active_profile = profile;
    _profile_generation = _profiles.generation[profile];
#if MESH_RADIO_TRACE_ENABLED
    mesh::radiotrace::trace().context(_radio, _active_profile, _profile_generation);
#endif
    _profile_refresh_required = false;
    _profile_repair_required = false;
#if MESH_HIL_PROFILE_TIMING
    mesh::profiletiming::mark(mesh::profiletiming::ProtocolAndCache, mesh::profiletiming::readClock());
#endif
    endReconfigure(resume);
#if MESH_HIL_PROFILE_TIMING
    mesh::profiletiming::mark(mesh::profiletiming::RxResume, mesh::profiletiming::readClock());
#endif
    applied = !resume || isInRecvMode();
  }
  endProfileRetune(applied);
  if (applied) {
    ++_profiles.switches;
  } else {
#if MESH_HIL_PROFILE_TIMING
    // Finish before rollback: restoration is a different operation and must
    // not overwrite the failed hop's partial stage evidence.
    const uint32_t finished_ticks = mesh::profiletiming::readEndpointClock();
#if MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD
    profile_usb_irq.release();
#endif
#if MC_NRF_RADIO_PROFILE_PRIORITY
    profile_priority.release();
#endif
    mesh::profiletiming::finish(mesh::profiletiming::Failure, finished_ticks);
#endif
    ++_profiles.switch_failures;
    // RX restart is part of the transaction, not merely a best-effort tail.
    // Revoke any fast-RX capability and restore both the physical tuple and
    // published profile on an apply or receive-resume failure.
    cacheParams(old_freq, old_bw, old_sf, old_cr);
    _params_valid = old_params_valid;
    _active_profile = old_profile;
    _profile_generation = old_generation;
    _profile_refresh_required = old_refresh;
    _physical_preamble = old_preamble;
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    _physical_protocol = old_protocol;
#endif
    bool restored = old_params_valid && _radio->standby() == RADIOLIB_ERR_NONE
        && applyParams(old_freq, old_bw, old_sf, old_cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
    restored = restored && applyProtocol(old_protocol);
#endif
    if (!restored) restored = restoreAfterDeepInit();
    if (restored) {
      _profile_repair_required = false;
      endReconfigure(resume);
    }
    if (!restored || (resume && !isInRecvMode()))
      _profile_refresh_required = _profile_repair_required = true;
  }
#if defined(ESP32_PLATFORM)
  _esp32_profile_visit_started_us = static_cast<uint64_t>(esp_timer_get_time());
  _profile_visit_stamp = static_cast<uint32_t>(_esp32_profile_visit_started_us);
#else
  _profile_visit_stamp = profileTimestamp(dual_profile);
#endif
  // A profile retune re-enters RX on a different tuple. Do not read its
  // instantaneous RSSI until the chip family's settle interval has elapsed.
  if (applied && _nf_refresh_requested) {
    _nf_sample_from = millis() + NF_CALIB_SETTLE_MS;
  }
  const uint32_t elapsed = profileElapsedUs(started, _profile_visit_stamp, dual_profile);
  if (elapsed > _profiles.longest_switch_us) _profiles.longest_switch_us = elapsed;
  // Require four good RX-to-RX hops in each direction before using the budget,
  // then continue raising the observed maximum as later good hops run.
  // Deferred and rolled-back operations are not samples.
  if (applied && resume && old_profile != profile) {
    _profiles.sampleSwitch(old_profile, profile, elapsed, millis());
  }
#if MESH_HIL_PROFILE_TIMING
  mesh::profiletiming::mark(mesh::profiletiming::Tail, mesh::profiletiming::readClock());
  const uint32_t finished_ticks = mesh::profiletiming::readEndpointClock();
#if MC_NRF_RADIO_PROFILE_USB_IRQ_GUARD
  profile_usb_irq.release();
#endif
#if MC_NRF_RADIO_PROFILE_PRIORITY
  profile_priority.release();
#endif
  mesh::profiletiming::finish(mesh::profiletiming::Success, finished_ticks);
#endif
  return applied ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::FAILED;
}

#if MESH_ENABLE_TX_BEACONS
bool RadioLibWrapper::validateBeaconPower(float frequency_mhz, int8_t power) const {
  return isfinite(frequency_mhz) && mesh::isLoRaTxPowerValid(power, frequency_mhz)
      && checkBeaconOutputPower(frequency_mhz, power);
}
#endif

bool RadioLibWrapper::checkBeaconOutputPower(float frequency_mhz, int8_t power) const {
  (void)frequency_mhz;
#if MESH_RADIO_PROFILE_CHIP == 1276
  // SX127x's setter automatically selects RFO below 2 dBm. Its two-argument
  // checker assumes PA_BOOST, so use the same selection as setOutputPower().
  return static_cast<SX1276*>(_radio)->checkOutputPower(power, nullptr, power < 2)
      == RADIOLIB_ERR_NONE;
#elif MESH_RADIO_PROFILE_CHIP == 1272
  return static_cast<SX1272*>(_radio)->checkOutputPower(power, nullptr, power < 2)
      == RADIOLIB_ERR_NONE;
#elif MESH_RADIO_PROFILE_CHIP == 1121
  // RadioLib's checker uses its current band. Validate the prospective band
  // without changing the cached frequency or triggering a hardware retune.
  if (frequency_mhz > 1000.0f) return power >= -18 && power <= 13;
#ifdef LR1121_FORCE_HP_PA
  return power >= -9 && power <= 22;
#else
  return power >= -17 && power <= 22;
#endif
#elif MESH_RADIO_PROFILE_CHIP == 2021
  return mesh::lr2021::isHighBand(frequency_mhz)
      ? power >= -19 && power <= 12 : power >= -9 && power <= 22;
#else
  // These RadioLib checkers inspect configuration only; no SPI is performed.
  return _radio->checkOutputPower(power, nullptr) == RADIOLIB_ERR_NONE;
#endif
}

#if MESH_ENABLE_TX_BEACONS
uint32_t RadioLibWrapper::getBeaconAirtime(const mesh::RadioProfileParams& p, int length) {
  if (length <= 0 || length > MAX_TRANS_UNIT || !validateProfile(p)) return 0;
  const uint16_t preamble = p.protocol == mesh::RadioProfileProtocol::Meshtext
      ? 16 : (p.preamble ? p.preamble : 32);
  if (!mesh::RadioProfiles::safePreamble(p, preamble)) return 0;
  const double symbol = mesh::RadioProfiles::symbolUs(p);
  const int de = symbol >= 16000
#if MESH_RADIO_PROFILE_CHIP == 1121
      || (p.freq > 1000 && p.sf > 10)
#endif
      ? 1 : 0;
  // Round the small integer bit/block ratio without floating-point division.
  const int denominator = p.sf - 2 * de;
  const int coded = (2 * length + (p.sf <= 6 ? 8 : 10) - 2 * de) / denominator;
  const double payload = 8 + coded * p.cr;
  return static_cast<uint32_t>(ceil((preamble + (p.sf <= 6 ? 6.25 : 4.25) + payload)
                                  * symbol / 1000.0));
}

void RadioLibWrapper::cacheBeaconRestoreParams() {
  const uint8_t profile = _beacon_restore_profile == 1 && _profiles.enabled() ? 1 : 0;
  const auto& p = _profiles.params(profile);
  // These remain NORMAL cached settings even while the physical modem is
  // briefly elsewhere. A hard recovery must never resurrect a beacon modem.
  cacheParams(p.freq, p.bw, p.sf, p.cr);
  _active_profile = profile;
  _profile_generation = _profiles.generation[profile];
  _physical_preamble = profilePreamble(profile);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  _physical_protocol = p.protocol;
#endif
  _profile_refresh_required = true;
}

mesh::RadioParamApplyResult RadioLibWrapper::prepareBeaconTransmit(
    const mesh::RadioProfileParams& p, int8_t power) {
  using Result = mesh::RadioParamApplyResult;
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioTransmitAllowed()) return Result::FAILED;
#endif
  if (_beacon_owned || _cw_active || _profile_repair_required) return Result::BUSY;
#if defined(MESH_HIL_TX_FREQ_MHZ)
  _hil_beacon_tx_frequency_mhz = 0;
  if (!mesh::hilTransmitFrequencyAllowed(p.freq)) return Result::FAILED;
#endif
  if (!_params_valid || !_dbm_valid || !getBeaconAirtime(p, MAX_TRANS_UNIT)
      || !validateBeaconPower(p.freq, power)) return Result::FAILED;
  if (isCalibratingNoiseFloor()) return Result::BUSY;

  beginProfileRetune(isInRecvMode() && !_rx_ps_armed && !_rx_ps_enabled);
  const uint8_t resume = beginReconfigure();
  if (resume > 1) {
    endProfileRetune(true);
    return Result::BUSY;
  }
  // Admission consumed no pending receive. From this point every error keeps
  // an exclusive lease until the ordinary receiver has been restored.
  _beacon_restore_profile = _active_profile;
  _beacon_owned = true;
  _dbm_applied = false;
  _beacon_restore_at = 0;
  _beacon_cad_timeout = cadScanTimeoutMillis(p.sf, p.bw);
  _physical_preamble = p.protocol == mesh::RadioProfileProtocol::Meshtext
      ? 16 : (p.preamble ? p.preamble : 32);
  bool applied = _radio->standby() == RADIOLIB_ERR_NONE
      && applyParams(p.freq, p.bw, p.sf, p.cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  applied = applied && applyProtocol(p.protocol);
  if (applied) _physical_protocol = p.protocol;
#endif
  applied = applied && applyCachedTxPower(power) == RADIOLIB_ERR_NONE;
  endProfileRetune(applied);
  _rx_hold_continuous = false;
  state = STATE_IDLE;
  // Do not startReceive: this is a TX-only excursion, not a third RX profile.
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (applied) _hil_beacon_tx_frequency_mhz = p.freq;
#endif
  return applied ? Result::APPLIED : Result::FAILED;
}

bool RadioLibWrapper::isBeaconChannelBusy() {
  if (!_beacon_owned || _beacon_restoring || state == STATE_TX_WAIT || isChipBusy()) return true;
  const int16_t result = performChannelScanWithTimeout(_beacon_cad_timeout);
  // CAD IRQs are not packets. Stop CAD even on failure before TX/restore, and
  // never compare this frequency's RSSI with either normal profile's floor.
  const bool stopped = _radio->standby() == RADIOLIB_ERR_NONE;
  const bool cleared = _radio->clearIrqFlags(UINT32_MAX) == RADIOLIB_ERR_NONE;
  claimRadioInterrupt();
  state = STATE_IDLE;
  return result != RADIOLIB_CHANNEL_FREE || !stopped || !cleared;
}

mesh::RadioParamApplyResult RadioLibWrapper::restoreBeaconReceive() {
  using Result = mesh::RadioParamApplyResult;
  if (!_beacon_owned) return Result::APPLIED;
  if (state == STATE_TX_WAIT) return Result::BUSY;
#if defined(MESH_HIL_TX_FREQ_MHZ)
  _hil_beacon_tx_frequency_mhz = 0;
#endif
  const uint32_t now = millis();
  if (_beacon_restore_at && static_cast<int32_t>(now - _beacon_restore_at) < 0) return Result::BUSY;
  _beacon_restoring = true;
  cacheBeaconRestoreParams();
  const auto& p = _profiles.params(_active_profile);
  bool restored = validateProfile(p) && !isChipBusy()
      && _radio->standby() == RADIOLIB_ERR_NONE;
  beginProfileRetune(false);
  restored = restored && applyParams(p.freq, p.bw, p.sf, p.cr);
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
  restored = restored && applyProtocol(p.protocol);
#endif
  restored = _dbm_applied = restored && applyCachedTxPower(_cur_dbm) == RADIOLIB_ERR_NONE;
  endProfileRetune(restored);
  if (restored) {
    // No RX ran on the beacon tuple, so any remaining event belongs to its
    // CAD/TX lifecycle. Retire it before the normal receiver is armed.
    restored = _radio->clearIrqFlags(UINT32_MAX) == RADIOLIB_ERR_NONE;
  }
  if (!restored) {
    restored = restoreAfterDeepInit()
        && _radio->clearIrqFlags(UINT32_MAX) == RADIOLIB_ERR_NONE;
  }
  claimRadioInterrupt();
  if (restored) {
    _profile_repair_required = false; // Full normal tuple/protocol/power/IRQ restore succeeded.
    state = STATE_IDLE;
    startRecv();
    restored = isInRecvMode()
#if MESH_ENABLE_RADIO_DIRECTION
        || !mesh::radioDirection::radioReceiveAllowed()
#endif
        ;
    if (!restored && restoreAfterDeepInit()) {
      state = STATE_IDLE;
      startRecv();
      restored = isInRecvMode()
#if MESH_ENABLE_RADIO_DIRECTION
          || !mesh::radioDirection::radioReceiveAllowed()
#endif
          ;
    }
  }
  _beacon_restoring = false;
  if (!restored) {
    _beacon_restore_at = now + 100;
    if (!_beacon_restore_at) _beacon_restore_at = 1;
    return Result::BUSY;
  }
  _profile_refresh_required = false;
  _beacon_restore_at = 0;
  _beacon_owned = false;
  return Result::APPLIED;
}
#endif

mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile) {
  return prepareTransmitProfile(profile, false);
}

mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool reply_rx_override) {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioTransmitAllowed()) return mesh::RadioParamApplyResult::FAILED;
#endif
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (profile > 1 || !mesh::hilTransmitFrequencyAllowed(_profiles.params(profile).freq))
    return mesh::RadioParamApplyResult::FAILED;
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return mesh::RadioParamApplyResult::BUSY;
#endif
  if (!_profiles.canTransmit(profile, reply_rx_override)) return mesh::RadioParamApplyResult::FAILED;
  if (_profiles.enabled() && (isChipBusy() || isPacketPendingOrReceiving())) {
    return mesh::RadioParamApplyResult::BUSY;
  }
  return tuneProfile(profile);
}

mesh::RadioParamApplyResult RadioLibWrapper::tryRestoreCodingRate(uint8_t cr) {
  const uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return mesh::RadioParamApplyResult::BUSY;
  bool applied = setCodingRate(cr);
  endReconfigure(resume_rx);
  applied = applied && (!resume_rx || isInRecvMode());
  if (!applied) _profile_refresh_required = true;
  return applied ? mesh::RadioParamApplyResult::APPLIED : mesh::RadioParamApplyResult::FAILED;
}

void RadioLibWrapper::serviceProfileScan() {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) return;
#endif
#if MESH_RADIO_TRACE_ENABLED
  if (mesh::radiotrace::trace().pending()) {
    mesh::radiotrace::trace().context(_radio, _active_profile, _profile_generation);
    mesh::radiotrace::trace().scan(_radio, mesh::radiotrace::nowUs(), 0, 0);
  }
#endif
#if defined(NRF52_PLATFORM)
  _profile_idle_visit_ticks = 0; // Every deferred/restarted scan fails closed.
#endif
#if defined(ESP32_PLATFORM)
  _esp32_profile_idle_visit_us = 0;
#endif
  (void)profileTimestamp(_profiles.enabled());
  if (_cw_active) return;
  if (!_params_valid || (_profile_retry_at && (int32_t)(millis() - _profile_retry_at) < 0)) return;
  _profile_retry_at = 0;
  if (_profiles.enabled() && !_profile_rxps_suspended) {
    const uint8_t resume = beginReconfigure();
    if (resume > 1) return;
    _profile_saved_rxps = _rx_ps_enabled;
    _rx_ps_enabled = false;
    _profile_rxps_suspended = true;
    _nf_calib_active = false;
    // Each profile owns a floor. Start fresh, independent baselines so RSSI
    // values from two frequencies are never blended into one estimate.
    _noise_floor_valid = false;
    _noise_floor_secondary_valid = false;
#if MESH_ENABLE_TELEMETRY_HISTORY
    _nf_published_valid = 0;
#endif
    _nf_refresh_requested = true;
    _nf_last_calib = 0;
    _nf_calib_deadline = 0;
    _nf_window_armed = _nf_lazy_refresh = _nf_refresh_attempted = false;
    _floor_estimator.reset(true);
    _secondary_floor_estimator.reset(true);
    _profile_refresh_required = true; // refresh side detectors even if the tuple is unchanged
    setProfileStandbyWarm(true);
    endReconfigure(resume);
  }
  if (_profiles.enabled() && !_profiles.switchTestReady()) {
    // A bounded startup self-test uses the real RX-to-RX retune path for the
    // active pair. Keep the maximum automatic provisional preamble until four good
    // samples in each direction are available. A packet, TX, or BUSY radio
    // defers the remaining hops without counting an invalid measurement.
    for (uint8_t hops = 0; hops < 2 * mesh::RadioProfiles::SwitchTestSamplesPerDirection;
         ++hops) {
      if (_profiles.switchTestReady()) break;
      const uint8_t from = _active_profile;
      const uint8_t samples = _profiles.switch_test_samples[from];
      const auto test_result = tuneProfile(from ^ 1);
      if (test_result == mesh::RadioParamApplyResult::FAILED) {
        _profile_retry_at = millis() + 1000;
        tuneProfile(0);
        return;
      }
      if (test_result != mesh::RadioParamApplyResult::APPLIED
          || (samples < mesh::RadioProfiles::SwitchTestSamplesPerDirection
              && _profiles.switch_test_samples[from] == samples)) return;
    }
    if (!_profiles.switchTestReady()) return;
  }
  uint8_t target = _active_profile;
#if defined(NRF52_PLATFORM) || defined(ESP32_PLATFORM)
  const uint8_t visited_profile = _active_profile;
#endif
  uint32_t visit_us = 0;
  const bool restart_scan = _profiles.enabled()
      && (_profile_scan_generation[0] != _profiles.generation[0]
          || _profile_scan_generation[1] != _profiles.generation[1]);
  if (restart_scan) {
    // A profile changed while scanning. Discard both partial baselines before
    // the first visit on the replacement tuples.
    _noise_floor_valid = false;
    _noise_floor_secondary_valid = false;
#if MESH_ENABLE_TELEMETRY_HISTORY
    _nf_published_valid = 0;
#endif
    _nf_refresh_requested = true;
    _nf_last_calib = 0;
    _nf_calib_deadline = 0;
    _nf_window_armed = _nf_lazy_refresh = _nf_refresh_attempted = false;
    _floor_estimator.reset(true);
    _secondary_floor_estimator.reset(true);
  }
  if (!_profiles.enabled()) target = 0;
  else if (restart_scan) target = _profiles.slowerProfile();
  else {
    visit_us = _profiles.listenUs(
        _active_profile, profilePreamble(_profiles.slowerProfile()));
    // A normal visit can be shorter than the RSSI settle interval on some
    // chips. Hold only a visit that is actually due to contribute its next
    // 50ms-spaced sample; very fast profiles otherwise retain their ordinary
    // scan cadence throughout calibration.
    const NoiseFloorEstimator& estimator = profileFloorEstimator(_active_profile);
    if (_nf_refresh_requested && estimator.ready(static_cast<uint32_t>(millis()))
        && visit_us < NF_CALIB_SETTLE_MS * 1000UL) {
      visit_us = NF_CALIB_SETTLE_MS * 1000UL;
    }
#if defined(ESP32_PLATFORM)
    // Keep long visits and light-sleep elapsed time in the same 64-bit domain.
    const uint64_t now_us = static_cast<uint64_t>(esp_timer_get_time());
#if MESH_RADIO_TRACE_ENABLED
    if (mesh::radiotrace::trace().pending())
      mesh::radiotrace::trace().scan(_radio, now_us, _esp32_profile_visit_started_us, visit_us, false);
#endif
    if (now_us < _esp32_profile_visit_started_us
        || now_us - _esp32_profile_visit_started_us >= visit_us) target ^= 1;
#else
    if (profileElapsedUs(_profile_visit_stamp, profileTimestamp(true), true) >= visit_us) target ^= 1;
#endif
  }
  const auto result = tuneProfile(target);
  if (restart_scan && result == mesh::RadioParamApplyResult::APPLIED) {
    _profile_scan_generation[0] = _profiles.generation[0];
    _profile_scan_generation[1] = _profiles.generation[1];
#if defined(ESP32_PLATFORM)
    _esp32_profile_visit_started_us = static_cast<uint64_t>(esp_timer_get_time());
    _profile_visit_stamp = static_cast<uint32_t>(_esp32_profile_visit_started_us);
#else
    _profile_visit_stamp = profileTimestamp(true);
#endif
  }
  if (result == mesh::RadioParamApplyResult::FAILED) {
    // Retry a rejected or failing profile at a bounded rate; retain primary RX.
    _profile_retry_at = millis() + 1000;
    tuneProfile(0);
  }
  if (!_profiles.enabled() && _profile_rxps_suspended && _active_profile == 0
      && result == mesh::RadioParamApplyResult::APPLIED) {
    const uint8_t resume = beginReconfigure();
    if (resume > 1) return;
    _rx_ps_enabled = _profile_saved_rxps;
    _profile_rxps_suspended = false;
    _profile_refresh_required = true;
    setProfileStandbyWarm(false);
    recalibrateNoiseFloor();
    endReconfigure(resume);
  }
#if defined(NRF52_PLATFORM)
  // A real hop gets a fresh budget on the next pass. Never use the old
  // profile's visit duration with the new profile's RX timestamp.
  if (_profiles.enabled() && !restart_scan && _active_profile == visited_profile
      && result == mesh::RadioParamApplyResult::APPLIED
      && !isChipBusy() && !isPacketPendingOrReceiving()) {
    _profile_idle_visit_ticks = uint64_t(visit_us) * 512 / 15625;
    _profile_idle_stamp = _profile_visit_stamp;
    _profile_idle_switch_us = _profiles.switchBudgetUs();
    _profile_idle_preamble = _physical_preamble;
  }
#endif
#if defined(ESP32_PLATFORM)
  if (_profiles.enabled() && !restart_scan && _active_profile == visited_profile
      && result == mesh::RadioParamApplyResult::APPLIED
      && !isChipBusy() && !isPacketPendingOrReceiving()) {
    _esp32_profile_idle_visit_us = visit_us;
    _esp32_profile_idle_stamp_us = _esp32_profile_visit_started_us;
    _esp32_profile_idle_switch_us = _profiles.switchBudgetUs();
    _esp32_profile_idle_preamble = _physical_preamble;
    _esp32_profile_idle_profile = _active_profile;
    _esp32_profile_idle_generation[0] = _profiles.generation[0];
    _esp32_profile_idle_generation[1] = _profiles.generation[1];
  }
#endif
}

bool RadioLibWrapper::getProfileIdleDelay(uint32_t& delay_ms) const {
  delay_ms = 0;
#if defined(NRF52_PLATFORM)
  if (!_profiles.enabled() || !NRF52Board::canIdleRadioProfile()
      || !_profile_idle_visit_ticks || !_params_valid || state != STATE_RX
      || !_profile_rxps_suspended || _rx_ps_enabled || _rx_ps_armed
      || _rx_hold_continuous || _rx_retry_pending || _profile_retry_at
      || _profile_refresh_required || _nf_refresh_requested || _nf_calib_active
      || !_noise_floor_valid || !_noise_floor_secondary_valid
      || _wd_observe_until || _cw_active || _morse_owned || hasPendingRadioInterrupt()
      || !_profiles.switchTestReady()
      || _profile_scan_generation[0] != _profiles.generation[0]
      || _profile_scan_generation[1] != _profiles.generation[1]
      || _active_profile > 1 || _profile_generation != _profiles.generation[_active_profile]
      || _profile_idle_stamp != _profile_visit_stamp
      || _profile_idle_preamble != _physical_preamble
      || _profile_idle_switch_us != _profiles.switchBudgetUs()) return false;
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return false;
#endif
#if MESH_ENABLE_MORSE_RX
  if (_morse_rx_owned) return false;
#endif
  // Use the extended domain without mutating the clock in this final sleep
  // check. The scanner already sampled it before publishing this idle proof.
  const uint32_t stamp = profile_clock_ticks
      + ((NRF_RTC2->COUNTER - profile_clock_raw) & 0x00ffffffUL);
  const uint32_t elapsed = stamp - _profile_idle_stamp;
  // RTC1 wakes the ready sketch task at 1024 Hz (32 RTC2 ticks). Retain
  // another 16 ticks (488 us) for return-to-loop work, validated on RAK3401.
  // Short visits poll; neither a new timer nor RF duty cycling is needed.
  constexpr uint32_t guard_ticks = 16;
  if (_profile_idle_visit_ticks >= 0x00800000UL || elapsed >= _profile_idle_visit_ticks
      || _profile_idle_visit_ticks - elapsed <= 32 + guard_ticks) return false;
  delay_ms = uint64_t(_profile_idle_visit_ticks - elapsed - guard_ticks) * 15625 / 512000;
  return delay_ms != 0;
#elif defined(ESP32_PLATFORM)
  if (!_profiles.enabled() || !_esp32_profile_idle_visit_us
      || !_params_valid || state != STATE_RX || !_profile_rxps_suspended
      || _rx_ps_enabled || _rx_ps_armed || _rx_hold_continuous
      || _rx_retry_pending || _profile_retry_at || _profile_refresh_required
      || _profile_repair_required || _nf_refresh_requested || _nf_calib_active
      || !_noise_floor_valid || !_noise_floor_secondary_valid
      || _wd_observe_until || _cw_active || _morse_owned || hasPendingRadioInterrupt()
      || !_profiles.switchTestReady() || _active_profile > 1
      || _esp32_profile_idle_profile != _active_profile
      || _profile_generation != _profiles.generation[_active_profile]
      || _profile_scan_generation[0] != _profiles.generation[0]
      || _profile_scan_generation[1] != _profiles.generation[1]
      || _esp32_profile_idle_generation[0] != _profiles.generation[0]
      || _esp32_profile_idle_generation[1] != _profiles.generation[1]
      || _esp32_profile_idle_stamp_us != _esp32_profile_visit_started_us
      || _esp32_profile_idle_preamble != _physical_preamble
      || _esp32_profile_idle_switch_us != _profiles.switchBudgetUs()) return false;
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return false;
#endif
#if MESH_ENABLE_MORSE_RX
  if (_morse_rx_owned) return false;
#endif
  const uint64_t now_us = static_cast<uint64_t>(esp_timer_get_time());
  if (now_us < _esp32_profile_idle_stamp_us) return false;
  const uint64_t elapsed_us = now_us - _esp32_profile_idle_stamp_us;
  // Keep frontend settling and short visits on their polling path. Only the
  // MCU idles; the RF receiver stays in continuous RX on the current profile.
  if (elapsed_us < mesh::esp32::profileSleepSettleMillis(NoiseFloorSettleMillis) * 1000UL
      || elapsed_us >= _esp32_profile_idle_visit_us) return false;
  const uint32_t remaining_us = _esp32_profile_idle_visit_us - static_cast<uint32_t>(elapsed_us);
  // Reserve return-to-loop/wake latency and require a useful timer interval.
  // These bounds are a conservative policy, not a measured latency guarantee.
  // The caller anchors this budget to an absolute ESP Timer deadline before
  // querying it and checks that deadline again immediately before sleeping.
  const uint32_t wake_guard_us = mesh::esp32::profileSleepGuardMicros();
  const uint32_t minimum_idle_us = mesh::esp32::profileSleepMinimumMicros();
  if (remaining_us < wake_guard_us + minimum_idle_us) return false;
  delay_ms = (remaining_us - wake_guard_us) / 1000;
  if (delay_ms > mesh::esp32::profileSleepSliceMillis())
    delay_ms = mesh::esp32::profileSleepSliceMillis();
  return true;
#else
  return false; // Other platforms retain polling until their wake path is validated.
#endif
}

uint32_t RadioLibWrapper::getProfileAirtime(uint8_t profile, int len, uint8_t cr) {
  if (profile > 1 || len < 0 || len > MAX_TRANS_UNIT) return 0;
  if (!_profiles.enabled() && profile == 0 && cr == 0) return getEstAirtimeFor(len);
  const auto& p = _profiles.params(profile);
  if (!validateProfile(p)) return 0;
  const double symbol = mesh::RadioProfiles::symbolUs(p);
  if (symbol <= 0 || len < 0) return 0;
  if (cr < 5 || cr > 8) cr = p.cr;
  const int de = symbol >= 16000
#if MESH_RADIO_PROFILE_CHIP == 1121
      // RadioLib 7.8.1 forces SX128x-compatible LDRO for high-band SF11/12.
      || (p.freq > 1000 && p.sf > 10)
#endif
      ? 1 : 0;
  // Round the small integer bit/block ratio without floating-point division.
  const int denominator = p.sf - 2 * de;
  const int coded = (2 * len + (p.sf <= 6 ? 8 : 10) - 2 * de) / denominator;
  const double payload_symbols = 8 + coded * cr;
  const double suffix = p.sf <= 6 ? 6.25 : 4.25;
  const double airtime = ceil((profilePreamble(profile) + suffix + payload_symbols) * symbol / 1000.0);
  const uint32_t rounded = airtime > UINT32_MAX ? UINT32_MAX : uint32_t(airtime);
#if MESH_RADIO_PROFILE_CHIP == 1110 || MESH_RADIO_PROFILE_CHIP == 1121
  // Preserve these wrappers' operational floor even for an inactive tuple.
  return rounded < 200 ? 200 : rounded;
#else
  return rounded;
#endif
}

bool RadioLibWrapper::setParams(float freq, float bw, uint8_t sf, uint8_t cr,
                                const uint32_t* rx_ps_timings) {
  return trySetParams(freq, bw, sf, cr, rx_ps_timings) == mesh::RadioParamApplyResult::APPLIED;
}

mesh::RadioParamApplyResult RadioLibWrapper::trySetPrimaryParams(const mesh::RadioProfileParams& p,
    bool temporary, const uint32_t* timings) {
  auto preview = _profiles;
  preview.setPrimary(p, temporary);
  if (!validateProfile(p) || !preview.automaticPreambleFits()) return mesh::RadioParamApplyResult::FAILED;
  const auto previous = _profiles.primary;
  const bool was_temp = _profiles.primary_temporary;
  const uint32_t generation = _profiles.generation[0];
  _profiles.primary.preamble = p.preamble;
  _profiles.primary.protocol = p.protocol;
  _profiles.primary_temporary = temporary;
#if defined(USE_LR2021)
  const bool previous_preempt = _primary_receive_preempt;
  if (supportsBoundedPrimaryReconfigure()) {
    const auto& current = _profiles.params(_active_profile);
    const uint16_t requested_preamble = preview.preamble(0,
        rxPowerSavingPreambleForParams(p.sf, p.bw));
    _primary_receive_preempt = _primary_apply_wait.mayAbandonReceive(
        millis(), p, temporary, current, currentPreambleLength(),
        requested_preamble, MAX_TRANS_UNIT);
  }
#endif
  const auto result = trySetParams(p.freq, p.bw, p.sf, p.cr, timings);
#if defined(USE_LR2021)
  _primary_receive_preempt = previous_preempt;
  if (result == mesh::RadioParamApplyResult::APPLIED) _primary_apply_wait.clear();
#endif
  if (result != mesh::RadioParamApplyResult::APPLIED) {
    _profiles.primary = previous;
    _profiles.primary_temporary = was_temp;
    _profiles.generation[0] = generation;
  } else {
    const bool changed = previous != p || was_temp != temporary;
    _profiles.generation[0] = generation + changed;
    // Preamble and lease flags are staged before trySetParams(), so its
    // setPrimary() cannot detect a change limited to those fields.
    if (changed) _profiles.resetSwitchTest();
    _profile_generation = _profiles.generation[0];
  }
  return result;
}

bool RadioLibWrapper::setRxBoostedGainMode(bool enabled) {
  return trySetRxBoostedGainMode(enabled) == mesh::RadioParamApplyResult::APPLIED;
}

mesh::RadioParamApplyResult RadioLibWrapper::trySetRxBoostedGainMode(bool enabled) {
  using Result = mesh::RadioParamApplyResult;
  if (!supportsRxBoostedGainMode()) return Result::FAILED;
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return Result::BUSY;
#endif
  if (_cw_active) return Result::BUSY;
  if (_rx_boosted_gain_valid && _rx_boosted_gain_applied
      && _cur_rx_boosted_gain == enabled && !_profile_repair_required) return Result::APPLIED;
  const uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return resume_rx == 2 ? Result::BUSY : Result::FAILED;

  const bool gain_changed = !_rx_boosted_gain_valid || !_rx_boosted_gain_applied
      || _cur_rx_boosted_gain != enabled;
  bool success = applyRxBoostedGainMode(enabled);
  _rx_boosted_gain_applied = success;
  if (success) {
    _cur_rx_boosted_gain = enabled;
    _rx_boosted_gain_valid = true;
    if (gain_changed) recalibrateNoiseFloor();
  }

  endReconfigure(resume_rx);
  return success ? Result::APPLIED : Result::FAILED;
}

void RadioLibWrapper::idle() {
  if (_cw_active) return;
  _radio->standby();
  _rx_hold_continuous = false;
  state = STATE_IDLE;   // need another startReceive()
}

bool RadioLibWrapper::setTrackerSleep(bool sleeping) {
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return false;
#endif
  if (_cw_active || (state & 0x0f) == STATE_TX_WAIT) return false;
  if (sleeping && isPacketPendingOrReceiving()) return false;
  if (_radio->standby() != RADIOLIB_ERR_NONE) return false;
  _rx_ps_armed = false;
  _rx_hold_continuous = false;
  state = STATE_IDLE;
  // PhysicalLayer's no-argument sleep retains configuration on SX126x and
  // LR11x0. The board's powerOff() intentionally uses cold sleep on some
  // radios and must not be used for a scheduled tracker wake.
  return !sleeping || _radio->sleep() == RADIOLIB_ERR_NONE;
}

void RadioLibWrapper::triggerNoiseFloorCalibrate(int threshold) {
  _threshold = threshold;
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) return;
#endif
  // The Dispatcher calls this every two seconds. Repeatedly recalibrating a
  // dual-profile scanner would repeatedly lengthen its visits. Once both
  // baselines are valid, retain them until a real RF-path change—except for a
  // fast profile: it receives one new bounded 7 ms-visit sample set every
  // fifteen minutes. Profiles already visiting for at least 7 ms need no
  // artificial slowdown after their normal baseline.
  if (_profiles.enabled() && _noise_floor_valid && _noise_floor_secondary_valid) {
    const uint16_t preamble = profilePreamble(_profiles.slowerProfile());
    const bool fast_profile = _profiles.listenUs(0, preamble) < NF_CALIB_SETTLE_MS * 1000UL
        || _profiles.listenUs(1, preamble) < NF_CALIB_SETTLE_MS * 1000UL;
    if (fast_profile && _nf_last_calib != 0
        && (uint32_t)(millis() - _nf_last_calib) >= NF_FAST_PROFILE_REFRESH_INTERVAL_MS) {
      requestNoiseFloorRefresh();
    }
    return;
  }
  requestPeriodicNoiseFloorRefresh();
}

void RadioLibWrapper::recalibrateNoiseFloor() {
  // A gain or tuning change starts a new baseline. Keep the published value
  // available while discarding the old samples and rise-hold history.
  _noise_floor_valid = false;
  _noise_floor_secondary_valid = false;
#if MESH_ENABLE_TELEMETRY_HISTORY
  _nf_published_valid = 0;
#endif
  _nf_refresh_requested = true;
  _nf_last_calib = 0;
  _nf_lazy_refresh = _nf_refresh_attempted = false;
  _floor_estimator.reset(true);
  _secondary_floor_estimator.reset(true);

  const unsigned long now = millis();
  _nf_sample_from = uint32_t(now) + (_rx_ps_armed ? 0 : NF_CALIB_SETTLE_MS);
  if (_nf_calib_active) {
    // Keep an already-open continuous-RX calibration window active, but restart
    // its sample and timeout bounds around the newly selected gain path.
    _nf_calib_deadline = now + NF_CALIB_TIMEOUT_MS;
    _nf_window_armed = true;
  } else {
    // Continuous RX starts its deadline in loop(); RX power saving opens a
    // bounded continuous-RX window in noiseFloorCalibCheck().
    _nf_calib_deadline = 0;
    _nf_window_armed = false;
  }
}

void RadioLibWrapper::requestPeriodicNoiseFloorRefresh() {
  const uint32_t interval = _nf_lazy_refresh ? NF_LAZY_REFRESH_INTERVAL_MS
      : (_rx_ps_enabled && !_rx_ps_continuous_fallback
          ? NF_RXPS_REFRESH_INTERVAL_MS : NF_CALIB_INTERVAL_MS);
  // Startup must not wait for a maintenance tick. Subsequent attempts, even
  // failed ones, need a real rest interval. Forced RF-path/AGC recalibration
  // bypasses this routine policy and retains its existing prompt behavior.
  if (_profiles.enabled()
      || (!_noise_floor_valid && _nf_last_calib == 0 && !_nf_refresh_attempted)
      || static_cast<uint32_t>(millis() - _nf_last_calib) >= interval) {
    requestNoiseFloorRefresh();
  }
}

void RadioLibWrapper::requestNoiseFloorRefresh() {
  if (_nf_refresh_requested) return;
  _nf_refresh_requested = true;
  _nf_lazy_refresh = false;
  _floor_estimator.reset();
  _secondary_floor_estimator.reset();
  _nf_calib_deadline = 0;  // starts when continuous RX is actually available
  _nf_window_armed = false;
  _nf_sample_from = uint32_t(millis()) + (_rx_ps_armed ? 0 : NF_CALIB_SETTLE_MS);
}

void RadioLibWrapper::doResetAGC() {
  _radio->sleep();  // warm sleep to reset analog frontend
}

void RadioLibWrapper::resetAGC() {
  resetAGCIfIdle();
}

bool RadioLibWrapper::tryResetAGC() {
  // BUSY is expected during RXPS sleep. Reuse the legacy explicit wake/reset
  // sequence there; polling only every 50 ms can miss every short RX window.
  // Outside known duty cycling, a busy SPI command still defers admission.
  return (_rx_ps_armed || !isChipBusy()) && resetAGCIfIdle(false);
}

bool RadioLibWrapper::resetAGCIfIdle(bool refresh_floor) {
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return false;
#endif
  if (_cw_active) return false;
  // make sure we're not mid-receiving and mid-sending of packet!
  if (isPacketPendingOrReceiving() || (state == STATE_TX_WAIT)) return false;

  doResetAGC();
  state = STATE_IDLE;   // trigger a startReceive()
  const bool gain_restored = !_rx_boosted_gain_valid
      || (_rx_boosted_gain_applied = applyRxBoostedGainMode(_cur_rx_boosted_gain));

  // Adaptive maintenance restores the same RF path. A valid, idle baseline
  // keeps its existing refresh deadline instead of spending another complete
  // continuous-RX window on every reset. Never cancel a requested collection,
  // postpone failed-sample retries, or change dual-profile scanning policy.
  if (!refresh_floor && gain_restored && _noise_floor_valid
      && !_profiles.enabled() && !_nf_refresh_requested && !_nf_calib_active) {
    return true;
  }

  // Recovery, legacy resets, changed/unknown paths and pending collections
  // retain prompt resampling. Do not mix samples across the analog reset.
  const bool previous_valid = _noise_floor_valid;
  const bool previous_secondary_valid = _noise_floor_secondary_valid;
  recalibrateNoiseFloor();
  _noise_floor_valid = previous_valid;
  _noise_floor_secondary_valid = previous_secondary_valid;
  return true;
}

bool RadioLibWrapper::recoverRadio(bool hard) {
#if MESH_ENABLE_TX_BEACONS
  // A failed startSendRaw() may request recovery. Defer that to the owner's
  // restore transaction so a reset never reapplies the beacon wire settings.
  if (_beacon_owned && !_beacon_restoring) return false;
#endif
  if (_cw_active) return false;
  const uint8_t base_state = state;
  if (hasPendingRadioInterrupt() || base_state == STATE_TX_WAIT) return false;

#ifdef RADIO_LIVENESS_SOFT_ONLY
  // STM32WL integrates the radio into the MCU and has no independent reset.
  // Keep its useful recovery path without linking unreachable deep-reset and
  // escalation machinery into flash-constrained builds.
  (void)hard;
  if (isReceivingPacket()) return false;
  n_wd_soft++;
  MESH_DEBUG_PRINTLN("RadioLibWrapper: liveness watchdog: soft AGC/RX re-arm");
  return resetAGCIfIdle();
#else
  const bool busy = isChipBusy();
  if (!busy && isReceivingPacket()) return false;

  if (hard) {
    n_wd_hard++;
    if (supportsRadioDeepInit()) {
      MESH_DEBUG_PRINTLN("RadioLibWrapper: liveness watchdog: hard radio reset");
      return restoreAfterDeepInit();
    }
    // LR11xx and integrated radios do not all expose a safe board-level reset
    // here. Fall back to their proven AGC/RX re-arm instead of reporting a
    // successful no-op. A stuck BUSY pin still reports failure and is retried.
    if (busy) return false;
    MESH_DEBUG_PRINTLN("RadioLibWrapper: hard reset unavailable; using RX re-arm");
    return resetAGCIfIdle();
  }

  // Never issue a warm-sleep/standby command while BUSY is stuck high.  The
  // hard stage can still escape that condition through the hardware reset pin.
  if (busy) return false;
  n_wd_soft++;
  MESH_DEBUG_PRINTLN("RadioLibWrapper: liveness watchdog: soft AGC/RX re-arm");
  return resetAGCIfIdle();
#endif
}

void RadioLibWrapper::rxPsWatchdogCheck() {
  // don't interfere mid-transmit or with a completed-but-unread packet
  // (a pending DIO1 event is itself proof the radio is alive; recvRaw() will
  // re-arm and re-base the watchdog)
  if (hasPendingRadioInterrupt() || state == STATE_TX_WAIT) {
    _wd_observe_until = 0;
    return;
  }

  unsigned long now = millis();
  bool tripped = false;

  if (_rx_ps_armed && state == STATE_RX && _wd_stuck_thresh > 0) {
    bool busy = isChipBusy();
    if (busy != _wd_last_busy) {
      // the sleep/listen wave is present -> radio healthy
      _wd_last_busy = busy;
      _wd_last_transition = now;
      _wd_stage = 0;
      _wd_strikes = 0;
      _wd_observe_until = 0;
    } else if (_wd_observe_until != 0) {
      // active observation window in progress (MCU kept awake via
      // isWatchdogObserving()); a healthy chip must toggle BUSY within it
      if ((long)(now - _wd_observe_until) >= 0) {
        _wd_observe_until = 0;
        if (!busy && isReceivingPacket()) {
          // BUSY held low by an ongoing reception (extended RX) - alive
          _wd_last_transition = now;
          _wd_strikes = 0;
        } else if (++_wd_strikes >= 2) {
          _wd_strikes = 0;
          tripped = true;
        } else {
          _wd_last_transition = now;   // full threshold before the next window
        }
      }
    } else if (now - _wd_last_transition > _wd_stuck_thresh) {
      // no proof of life for too long: actively watch one full cycle
      _wd_observe_until = now + _wd_observe_ms;
      if (_wd_observe_until == 0) _wd_observe_until = 1;  // 0 means "off"
    }
  } else {
    _wd_observe_until = 0;
  }
  if (_startrx_fails >= 3) tripped = true;  // can't even re-arm receive mode

  if (!tripped) return;

  _wd_last_transition = now;   // grace period before the next escalation
  _startrx_fails = 0;
  _wd_observe_until = 0;

  if (_wd_stage == 0) {
    _wd_stage = 1;
    n_wd_soft++;
    MESH_DEBUG_PRINTLN("RadioLibWrapper: watchdog: RX duty-cycle stuck, soft re-arm");
    state = STATE_IDLE;   // next recvRaw() re-arms receive mode
  } else {
    _wd_stage = 2;
    n_wd_hard++;
    MESH_DEBUG_PRINTLN("RadioLibWrapper: watchdog: still stuck, hard radio reset");
    restoreAfterDeepInit();
    state = STATE_IDLE;   // re-arm (rx powersaving settings are kept in members)
  }
}

// On-demand noise-floor calibration, active only with RX duty-cycle powersaving:
// a duty-cycled receiver can't be sampled reliably (the frontend is off in the
// sleep windows and settling right after each wake), so a requested refresh
// drops receive mode to plain continuous RX, collects a fresh sample batch,
// and re-arms the duty cycle. Requests are rate-limited and coalesced.
void RadioLibWrapper::requestRestartRecv() {
  // An RX interrupt can arrive between the caller's idle check and this state
  // transition. Preserve that flag so the completed packet is still consumed.
  if (state != STATE_TX_WAIT) state = STATE_IDLE;
}

bool RadioLibWrapper::isPacketPendingOrReceiving() {
  return hasPendingRadioInterrupt() || isReceivingPacket();
}

void RadioLibWrapper::noiseFloorCalibCheck(unsigned long now) {
  const uint32_t now_ms = static_cast<uint32_t>(now);
  if (_nf_calib_active) {
    if (!_rx_ps_enabled
        || (static_cast<int32_t>(now_ms - uint32_t(_nf_calib_deadline)) >= 0
            && !_floor_estimator.complete())) {
      // powersaving turned off mid-window, or the batch couldn't complete
      // (busy channel) - keep the previous floor
      endNoiseFloorCalib(now);
    } else if (_rx_ps_armed
        && static_cast<int32_t>(now_ms - uint32_t(_nf_sample_from)) >= 0) {
      // A packet can delay the transition from RXPS to continuous RX. Retry
      // at a bounded cadence without discarding a pending interrupt.
      _nf_sample_from = now_ms + NoiseFloorEstimator::SAMPLE_INTERVAL_MS;
      if (!isPacketPendingOrReceiving()) requestRestartRecv();
    }
  } else if (_nf_refresh_requested && _rx_ps_enabled && _rx_ps_armed && state == STATE_RX
             && static_cast<int32_t>(now_ms - uint32_t(_nf_sample_from)) >= 0) {
    // never interrupt an ongoing reception to calibrate (a TX in flight is
    // already excluded by state == STATE_RX). A requested forced baseline is
    // prompt; routine cooldown was enforced before creating the request.
    _nf_sample_from = now_ms + NoiseFloorEstimator::SAMPLE_INTERVAL_MS;
    if (isPacketPendingOrReceiving()) return;
    _nf_calib_active = true;
    _nf_calib_deadline = now_ms + NF_CALIB_TIMEOUT_MS;
    _nf_window_armed = true;
    _nf_sample_from = now_ms + NF_CALIB_SETTLE_MS;
    _floor_estimator.reset();
    if (!isPacketPendingOrReceiving()) {
      requestRestartRecv();   // startReceiveMode() selects continuous RX
    }
  }
}

void RadioLibWrapper::endNoiseFloorCalib(unsigned long now) {
  _nf_calib_active = false;
  _nf_refresh_requested = false;
  _nf_last_calib = now;
  _nf_calib_deadline = 0;
  _nf_window_armed = false;
  _nf_refresh_attempted = true;
  // force a receive re-arm back into duty-cycle mode, but don't clobber a
  // completed-but-unread packet or an in-flight TX (recvRaw()/onSendFinished()
  // will re-arm right after those anyway; same guard style as setRxPowerSaving)
  if (state != STATE_TX_WAIT
      && !isPacketPendingOrReceiving()) {
    requestRestartRecv();
  } else if (state == STATE_RX && !_rx_ps_armed
      && _rx_ps_enabled && !_rx_ps_continuous_fallback) {
    // A false preamble can release RX without producing another interrupt.
    // Keep the restoration pending: Dispatcher calls onReceiveProcessed()
    // even after an empty poll, so its normal packet-owner guard retries once
    // the reception or unread packet is gone. TX retirement rearms on its own.
    _rx_hold_continuous = true;
  }
}

void RadioLibWrapper::checkReceiveMode(uint32_t now) {
  // Duty-cycle standby/sleep is expected. Never wake it for a status probe,
  // touch an in-flight TX, or discard a completed/pending receive interrupt.
  if (state != STATE_RX || _rx_ps_armed || isChipBusy()
      || uint32_t(now - _rx_mode_checked_at) < 10000UL) return;
  _rx_mode_checked_at = now;
  if (isPacketPendingOrReceiving()) return;
  const int8_t mode = readReceiveMode();
  if (isPacketPendingOrReceiving()) return;
  if (mode < 0) return;  // an SPI error is not evidence of a particular mode
  if (mode == 1) {
    _rx_mode_failures = 0;
    return;
  }
  if (_rx_mode_failures < 3) ++_rx_mode_failures;
  if (_rx_mode_failures < 2) return;

  // Confirmed silent loss of RX (including a late RTC event after CAD).
  // Reuse the existing recovery path: it preserves tuning, TX power and gain.
  // A healthy status observation, rather than an accepted re-arm command,
  // clears the streak. A failed soft recovery can therefore reach hard reset.
  _floor_estimator.reset(true);
  MESH_DEBUG_PRINTLN("RadioLibWrapper: chip left RX, %s recovery",
                    _rx_mode_failures < 3 ? "soft" : "hard");
  recoverRadio(_rx_mode_failures >= 3);
}

#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
uint32_t RadioLibWrapper::carrierWaveRemainingMillis() const {
  if (!_cw_active || _cw_stopping) return 0;
  if (_morse_owned) return morseTxRemainingMillis();
#if MESH_ENABLE_MORSE_RX
  if (_morse_rx_owned) return morseRxRemainingMillis();
#endif
  const int32_t remaining = static_cast<int32_t>(_cw_until - millis());
  return remaining > 0 ? static_cast<uint32_t>(remaining) : 0;
}

mesh::RadioParamApplyResult RadioLibWrapper::setCarrierWave(uint8_t profile, uint32_t duration_ms) {
  using Result = mesh::RadioParamApplyResult;
#if MESH_ENABLE_RADIO_DIRECTION
  if (duration_ms && !mesh::radioDirection::radioTransmitAllowed()) return Result::FAILED;
#endif
#if MESH_ENABLE_CARRIER_TOOLS
  // The shared emergency stop also covers chips exposing Morse without the
  // older profile-CW CLI capability.
  if (!duration_ms && (_morse_owned
#if MESH_ENABLE_MORSE_RX
      || _morse_rx_owned
#endif
      )) return stopCarrierWave() ? Result::APPLIED : Result::FAILED;
  if (!supportsCarrierWave()) return Result::FAILED;
  if (!duration_ms) return !_cw_active || stopCarrierWave() ? Result::APPLIED : Result::FAILED;
  if (profile > 1 || duration_ms > CarrierWaveMaxMillis || !_params_valid
      || !_profiles.canTransmit(profile)) return Result::FAILED;
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (!mesh::hilTransmitFrequencyAllowed(_profiles.params(profile).freq)) return Result::FAILED;
#endif
  if (_cw_active) {
    if (_morse_owned
#if MESH_ENABLE_MORSE_RX
        || _morse_rx_owned
#endif
        || _cw_stopping || profile != _cw_profile
        || _cw_generation != _profiles.generation[profile]) return Result::BUSY;
    _cw_until = static_cast<uint32_t>(millis()) + duration_ms;
    return Result::APPLIED;
  }

  // Own an idle radio before changing profiles or keying the external FEM.
  // This also performs the SX1262 duty-cycle RTC cleanup used by packet TX.
  const uint8_t resume = beginReconfigure();
  if (resume > 1) return Result::BUSY;
  _cw_restore_profile = _active_profile;
  if (_radio->standby() != RADIOLIB_ERR_NONE
      || _radio->clearIrqFlags(UINT32_MAX) != RADIOLIB_ERR_NONE
      || tuneProfile(profile) != Result::APPLIED) {
    startRecv();
    return Result::FAILED;
  }
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (!hilPacketTransmitAllowed()) {
    startRecv();
    return Result::FAILED;
  }
#endif
  _radio->clearPacketReceivedAction();
  state = STATE_IDLE;
  _rx_ps_armed = false;
  _rx_hold_continuous = false;
  _nf_calib_active = false;
  _wd_observe_until = 0;
  _cw_profile = profile;
  _cw_generation = _profiles.generation[profile];
  _morse_owned = false;
  _cw_active = true;
  _cw_stopping = false;
  _board->setRadioTestActive(true);  // service the deadline even without USB
  _board->onBeforeTransmit();
  _cw_board_tx = true;
  if (enterCarrierWave() != RADIOLIB_ERR_NONE) {
    stopCarrierWave();  // a failed SPI result does not prove TX stayed off
    return Result::FAILED;
  }
  _cw_until = static_cast<uint32_t>(millis()) + duration_ms;
  return Result::APPLIED;
#else
  (void)profile;
  // Keep the shared emergency stop available to an independently enabled RX.
  return !duration_ms && _morse_rx_owned
      ? (stopCarrierWave() ? Result::APPLIED : Result::FAILED) : Result::FAILED;
#endif
}
#endif

#if MESH_ENABLE_CARRIER_TOOLS
bool RadioLibWrapper::supportsMorseTx() const {
  return supportsCarrierWave();
}

mesh::MorseTxResult RadioLibWrapper::startMorseTx(float frequency_mhz,
                                                uint8_t wpm, const char* text) {
  using Result = mesh::MorseTxResult;
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioTransmitAllowed()) return Result::Failed;
#endif
  if (!supportsMorseTx()) return Result::Unsupported;
  if (_cw_active) return Result::Busy;
  const Result valid_text = mesh::MorseTx::validate(text, wpm);
  if (valid_text != Result::Started) return valid_text;
  if (!_params_valid || !isfinite(frequency_mhz)) return Result::InvalidFrequency;
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (!mesh::hilTransmitFrequencyAllowed(frequency_mhz)) return Result::InvalidFrequency;
#endif
  // Only the carrier frequency matters here: existing LoRa SF/BW/protocol
  // constraints must not accidentally limit an unmodulated transmission.
  if (!validateMorseFrequency(frequency_mhz)) return Result::InvalidFrequency;
  for (uint8_t profile = 0; profile < 2; ++profile) {
    const auto& p = _profiles.params(profile);
    if (mesh::morseFrequencyOverlaps(frequency_mhz, p.freq, p.bw)) return Result::Overlap;
  }
  const uint8_t resume = beginReconfigure();
  if (resume > 1) return Result::Busy;
  _cw_restore_profile = _active_profile;
  if (_radio->standby() != RADIOLIB_ERR_NONE
      || _radio->clearIrqFlags(UINT32_MAX) != RADIOLIB_ERR_NONE) {
    endReconfigure(resume);
    return Result::Failed;
  }

  // Publish ownership before the first hardware mutation; even a failed SPI
  // command can have reached the chip, requiring a confirmed stop/restore.
  _radio->clearPacketReceivedAction();
  _cw_active = _morse_owned = true;
  _cw_stopping = false;
  _cw_profile = _active_profile;
  _morse_generation[0] = _profiles.generation[0];
  _morse_generation[1] = _profiles.generation[1];
  _morse_frequency = frequency_mhz;
  _morse_wpm = wpm;
  _rx_ps_armed = false;
  _rx_hold_continuous = false;
  _nf_calib_active = false;
  _wd_observe_until = 0;
  state = STATE_IDLE;
  _board->setRadioTestActive(true);
  if (prepareMorseCarrier(frequency_mhz) != RADIOLIB_ERR_NONE) {
    stopCarrierWave();
    return Result::Failed;
  }
  _board->onBeforeTransmit();
  _cw_board_tx = true;
  if (enterMorseCarrier() != RADIOLIB_ERR_NONE) {
    stopCarrierWave();
    return Result::Failed;
  }
  // Start timing when the hardware command has finished, not before the
  // one-time oscillator/modem setup. Validation above guarantees success.
  _morse.start(text, wpm, static_cast<uint32_t>(millis()));
  return Result::Started;
}

mesh::MorseTxResult RadioLibWrapper::stopMorseTx() {
  if (!supportsMorseTx()) return mesh::MorseTxResult::Unsupported;
  if (!_morse_owned) return mesh::MorseTxResult::Stopped;
  return stopCarrierWave() ? mesh::MorseTxResult::Stopped : mesh::MorseTxResult::Failed;
}

bool RadioLibWrapper::serviceMorseTx() {
  if (!_morse_owned || !_cw_active) return false;
  const uint32_t now = millis();
  if (_cw_stopping) {
    if (static_cast<int32_t>(now - _cw_retry_at) >= 0) stopCarrierWave();
    return true;
  }
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (!mesh::hilTransmitFrequencyAllowed(_morse_frequency)) {
    stopCarrierWave();
    return true;
  }
#endif
  if (_morse_generation[0] != _profiles.generation[0]
      || _morse_generation[1] != _profiles.generation[1]) {
    stopCarrierWave();
    return true;
  }
  // The public profile model is also used by board-specific integrations.
  // Fail closed if one changes a tuple directly without bumping generation.
  for (uint8_t profile = 0; profile < 2; ++profile) {
    const auto& p = _profiles.params(profile);
    if (mesh::morseFrequencyOverlaps(_morse_frequency, p.freq, p.bw)) {
      stopCarrierWave();
      return true;
    }
  }
  const auto edge = _morse.advance(now);
  if (edge == mesh::MorseTx::Edge::Complete || edge == mesh::MorseTx::Edge::Expired) {
    stopCarrierWave();
  } else if (edge == mesh::MorseTx::Edge::KeyUp) {
    const int16_t status = leaveMorseCarrier();
    if (_cw_board_tx) { _board->onAfterTransmit(); _cw_board_tx = false; }
    if (status != RADIOLIB_ERR_NONE) stopCarrierWave();
    else _morse.edgeApplied(static_cast<uint32_t>(millis()));
  } else if (edge == mesh::MorseTx::Edge::KeyDown) {
    _board->onBeforeTransmit();
    _cw_board_tx = true;
    if (enterMorseCarrier() != RADIOLIB_ERR_NONE) stopCarrierWave();
    else _morse.edgeApplied(static_cast<uint32_t>(millis()));
  }
  return true;
}
#endif

#if MESH_ENABLE_MORSE_RX
bool RadioLibWrapper::canAllocateMorseReceiver() const {
  // This is a real, opt-in runtime reservation, never hidden in the static
  // RAM baseline or silently borrowed from an independently running OTA.
  size_t allocation = _morse_rx ? 0U : sizeof(mesh::MorseRx);
#if defined(ESP32_PLATFORM)
  size_t largest_allocation = allocation;
#endif
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
  if (!_morse_trace) {
    allocation += sizeof(mesh::MorseRssiTrace);
#if defined(ESP32_PLATFORM)
    if (sizeof(mesh::MorseRssiTrace) > largest_allocation)
      largest_allocation = sizeof(mesh::MorseRssiTrace);
#endif
  }
#endif
  // A retained transcript/trace is reused in place after preparation succeeds.
  // Require only allocations still missing, while keeping the same reserve.
  const size_t required = allocation + 4096U;
#if defined(ESP32_PLATFORM)
  return ESP.getFreeHeap() >= required
      && (!largest_allocation
          || heap_caps_get_largest_free_block(MALLOC_CAP_8BIT) >= largest_allocation);
#elif defined(NRF52_PLATFORM)
  const int available = dbgHeapFree();
  return available > 0 && static_cast<size_t>(available) >= required;
#elif defined(RP2040_PLATFORM)
  const int available = rp2040.getFreeHeap();
  return available > 0 && static_cast<size_t>(available) >= required;
#elif defined(STM32_PLATFORM)
  uint8_t stack_marker;
  const caddr_t heap_end = _sbrk(0);
  if (heap_end == (caddr_t)-1) return false;
  const uintptr_t stack_address = reinterpret_cast<uintptr_t>(&stack_marker);
  const uintptr_t heap_address = reinterpret_cast<uintptr_t>(heap_end);
  return stack_address > heap_address && stack_address - heap_address >= required;
#else
  return false; // an unmeasured allocator must not consume the safety reserve
#endif
}

mesh::MorseRxResult RadioLibWrapper::startMorseRx(float frequency_mhz,
                                                uint8_t wpm, uint32_t duration_ms) {
  using Result = mesh::MorseRxResult;
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) return Result::Failed;
#endif
  if (!supportsMorseRx()) return Result::Unsupported;
  if (_cw_active) return Result::Busy;
  const Result valid = mesh::MorseRx::validate(wpm, duration_ms);
  if (valid != Result::Started) return valid;
  if (!_params_valid || !isfinite(frequency_mhz) || !validateMorseFrequency(frequency_mhz))
    return Result::InvalidFrequency;
  // Passive reception cannot interfere with either packet profile. Listening
  // on its centre is useful; only a transmitting Morse carrier needs spacing.
  if (!canAllocateMorseReceiver()) return Result::Failed;
  mesh::MorseRx* receiver = _morse_rx;
  const bool allocated = receiver == nullptr;
  if (allocated) receiver = new (std::nothrow) mesh::MorseRx;
  if (!receiver) return Result::Failed;
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
  mesh::MorseRssiTrace* trace = _morse_trace;
  const bool trace_allocated = trace == nullptr;
  if (trace_allocated) trace = new (std::nothrow) mesh::MorseRssiTrace;
  if (!trace) { if (allocated) delete receiver; return Result::Failed; }
#endif
  // Allocation/admission occurs before any hardware change. Keep a previous
  // transcript untouched until the replacement receiver is proven running.
  const uint8_t resume = beginReconfigure();
  if (resume > 1) {
    if (allocated) delete receiver;
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
    if (trace_allocated) delete trace;
#endif
    return Result::Busy;
  }
  _cw_restore_profile = _active_profile;
  if (_radio->standby() != RADIOLIB_ERR_NONE
      || _radio->clearIrqFlags(UINT32_MAX) != RADIOLIB_ERR_NONE) {
    if (allocated) delete receiver;
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
    if (trace_allocated) delete trace;
#endif
    endReconfigure(resume);
    return Result::Failed;
  }
  _radio->clearPacketReceivedAction();
  _cw_active = _morse_rx_owned = true;
#if MESH_ENABLE_CARRIER_TOOLS
  _morse_owned = false;
#endif
  _cw_stopping = false;
  _cw_profile = _active_profile;
  _morse_generation[0] = _profiles.generation[0];
  _morse_generation[1] = _profiles.generation[1];
  _rx_ps_armed = _rx_hold_continuous = _nf_calib_active = false;
  _wd_observe_until = 0;
  _morse_rx_read_errors = 0;
  state = STATE_IDLE;
  _board->setRadioTestActive(true);
  if (prepareMorseReceiver(frequency_mhz) != RADIOLIB_ERR_NONE
      || (_rx_boosted_gain_valid
          && !(_rx_boosted_gain_applied = applyRxBoostedGainMode(_cur_rx_boosted_gain)))
      || startMorseReceiver() != RADIOLIB_ERR_NONE) {
    if (allocated) delete receiver;
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
    if (trace_allocated) delete trace;
#endif
    stopCarrierWave();
    return Result::Failed;
  }
  const float initial_rssi = readMorseRssi();
  if (!isfinite(initial_rssi) || initial_rssi < -180.0f || initial_rssi > 20.0f) {
    if (allocated) delete receiver;
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
    if (trace_allocated) delete trace;
#endif
    stopCarrierWave();
    return Result::Failed;
  }
  const uint32_t now = millis();
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
  receiver->setTrace(trace);
  _morse_trace = trace;
#endif
  receiver->start(wpm, duration_ms, now);
  receiver->sample(initial_rssi, now);
  _morse_rx = receiver;
  _morse_rx_frequency = frequency_mhz;
  _morse_rx_next_sample = now + mesh::MorseRx::SampleIntervalMillis;
  ++_morse_capture_generation;
  return Result::Started;
}

mesh::MorseRxResult RadioLibWrapper::stopMorseRx() {
  if (!supportsMorseRx()) return mesh::MorseRxResult::Unsupported;
  if (!_morse_rx_owned) return mesh::MorseRxResult::Stopped;
  return stopCarrierWave() ? mesh::MorseRxResult::Stopped : mesh::MorseRxResult::Failed;
}

size_t RadioLibWrapper::readMorseCapture(size_t offset, char* out, size_t capacity) const {
  if (_morse_rx) return _morse_rx->read(offset, out, capacity);
  if (out && capacity) out[0] = 0;
  return 0;
}

void RadioLibWrapper::clearMorseCapture() {
  if (isMorseRxActive()) return;
  delete _morse_rx;
  _morse_rx = nullptr;
#if defined(MESH_MORSE_HIL) && MESH_MORSE_HIL
  delete _morse_trace;
  _morse_trace = nullptr;
#endif
  ++_morse_capture_generation;
}
#endif

#if MESH_ENABLE_MORSE_RX
bool RadioLibWrapper::serviceMorseRx() {
  if (!_morse_rx_owned || !_cw_active) return false;
  const uint32_t now = millis();
  if (_cw_stopping) {
    if (static_cast<int32_t>(now - _cw_retry_at) >= 0) stopCarrierWave();
    return true;
  }
  if (!_morse_rx || !_morse_rx->service(now)
      || _morse_generation[0] != _profiles.generation[0]
      || _morse_generation[1] != _profiles.generation[1]) {
    stopCarrierWave();
    return true;
  }
  if (static_cast<int32_t>(now - _morse_rx_next_sample) >= 0) {
    const float rssi = readMorseRssi();
    const uint32_t sampled_at = millis();
    _morse_rx_next_sample = sampled_at + mesh::MorseRx::SampleIntervalMillis;
    if (!isfinite(rssi) || rssi < -180.0f || rssi > 20.0f) {
      if (++_morse_rx_read_errors >= 8) stopCarrierWave();
    } else {
      _morse_rx_read_errors = 0;
      _morse_rx->sample(rssi, sampled_at);
      if (!_morse_rx->active()) stopCarrierWave();
    }
  }
  return true;
}
#endif

#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
bool RadioLibWrapper::stopCarrierWave() {
#if MESH_ENABLE_CARRIER_TOOLS
  _morse.stop();
#endif
#if MESH_ENABLE_MORSE_RX
  if (_morse_rx_owned && _morse_rx) _morse_rx->stop(static_cast<uint32_t>(millis()));
#endif
  _cw_stopping = true;
  _dbm_applied = false;
  // Deassert external TX even if SPI fails. SX127x must also rebuild its LoRa
  // modem after FSK direct mode before normal packet work can resume.
  const int16_t status = _radio->finishTransmit();
  // Some RadioLib finishTransmit() implementations return after a failed IRQ
  // clear, before their standby command. Always attempt the RF-off command.
  const int16_t standby_status = _radio->standby();
  if (_cw_board_tx) { _board->onAfterTransmit(); _cw_board_tx = false; }
  bool restored;
#if MESH_ENABLE_MORSE_RX
  if (_morse_rx_fsk_mode) {
    // applyParams only changes a LoRa tuple; it cannot rebuild a modem switched
    // to GFSK. This includes failed partial preparation and cleanup retries.
    restored = restoreAfterDeepInit();
    if (restored) _morse_rx_fsk_mode = false;
  } else {
#else
  {
#endif
    restored = status == RADIOLIB_ERR_NONE || standby_status == RADIOLIB_ERR_NONE
        ? restoreCarrierWaveModem() : restoreAfterDeepInit();
  }
  _dbm_applied = restored && _dbm_valid;
  if (!restored) {
    _cw_retry_at = static_cast<uint32_t>(millis()) + 100;
    return false;  // hold normal work until stopping the carrier is confirmed
  }
  if (!registerInterruptAction()) {
    _cw_retry_at = static_cast<uint32_t>(millis()) + 100;
    return false;
  }
  _cw_active = false;
  _cw_stopping = false;
  _rx_ps_armed = false;
  state = STATE_IDLE;
  _profile_refresh_required = true;
  const uint8_t restore = _profiles.enabled() ? _cw_restore_profile : 0;
  const bool tuned = tuneProfile(restore) == mesh::RadioParamApplyResult::APPLIED;
  recalibrateNoiseFloor();
  startRecv();
  bool receive_ready = isInRecvMode()
#if MESH_ENABLE_RADIO_DIRECTION
      || !mesh::radioDirection::radioReceiveAllowed()
#endif
      ;
  if (!receive_ready && restoreAfterDeepInit()) {
    startRecv();
    receive_ready = isInRecvMode();
  }
  if (!tuned || !receive_ready) {
    // A stopped carrier is not sufficient to hand ownership back if the
    // configured packet modem/profile could not be restored. Keep the guard
    // and watchdog wake veto until the bounded retry repairs reception.
    _cw_active = _cw_stopping = true;
    _cw_retry_at = static_cast<uint32_t>(millis()) + 100;
    return false;
  }
#if MESH_ENABLE_CARRIER_TOOLS
  _morse_owned = false;
#endif
#if MESH_ENABLE_MORSE_RX
  _morse_rx_owned = false;
#endif
  _board->setRadioTestActive(false);
  return true;
}

bool RadioLibWrapper::serviceCarrierWave() {
#if MESH_ENABLE_MORSE_RX
  if (serviceMorseRx()) return true;
#endif
#if MESH_ENABLE_CARRIER_TOOLS
  if (serviceMorseTx()) return true;
  if (_cw_active) {
#if defined(MESH_HIL_TX_FREQ_MHZ)
    if (!hilPacketTransmitAllowed()) {
      stopCarrierWave();
      return true;
    }
#endif
    const uint32_t now = millis();
    if ((_cw_stopping && static_cast<int32_t>(now - _cw_retry_at) >= 0)
        || (!_cw_stopping && (static_cast<int32_t>(now - _cw_until) >= 0
            || !_profiles.canTransmit(_cw_profile)
            || _cw_generation != _profiles.generation[_cw_profile]))) {
      if (!stopCarrierWave()) MESH_DEBUG_PRINTLN("CW: stop/RX recovery failed");
    }
    return true;
  }
#endif
  return false;
}
#endif

void RadioLibWrapper::serviceDirectionPolicy() {
#if MESH_ENABLE_RADIO_DIRECTION
  const bool receive = mesh::radioDirection::radioReceiveAllowed();
#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
  if (_cw_active) {
    bool receiver = false;
#if MESH_ENABLE_MORSE_RX
    receiver = _morse_rx_owned;
#endif
    if ((receiver && !receive)
        || (!receiver && !mesh::radioDirection::radioTransmitAllowed())) stopCarrierWave();
    if (_cw_active) return;
  }
#endif
  if (receive) {
    _direction_receive_disabled = false;
    return;
  }
  if (state == STATE_TX_WAIT) return;
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return;  // Its owner restores the saved tuple first.
#endif
  // A direction change deliberately retires any unread receive. No RX retry,
  // scanner or calibration may reopen it. Ordinary TX/CAD stays available.
  if (!_direction_receive_disabled || state != STATE_IDLE || _rx_ps_armed
      || hasPendingRadioInterrupt()) {
    if (_rx_ps_armed) stopReceiveDutyCycle();
    if (_radio->standby() != RADIOLIB_ERR_NONE
        || _radio->clearIrqFlags(UINT32_MAX) != RADIOLIB_ERR_NONE) return;
    claimRadioInterrupt();
    _irq_probe_failures = 0;
    state = STATE_IDLE;
    _direction_receive_disabled = true;
  }
  _rx_ps_armed = _rx_hold_continuous = false;
  _rx_retry_pending = false;
  _rx_retry_delay_ms = 0;
  _nf_calib_active = _nf_refresh_requested = _nf_window_armed = false;
  _wd_observe_until = 0;
#endif
}

void RadioLibWrapper::loop() {
#if MESH_ENABLE_RADIO_DIRECTION
  serviceDirectionPolicy();
  if (!mesh::radioDirection::radioReceiveAllowed()) {
#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
    serviceCarrierWave();
#endif
    return;
  }
#endif
#if MESH_RADIO_TRACE_ENABLED
  if (mesh::radiotrace::trace().pending()) {
    mesh::radiotrace::trace().context(_radio, _active_profile, _profile_generation);
    mesh::radiotrace::trace().tick(mesh::radiotrace::nowUs());
  }
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return;
#endif
  (void)profileTimestamp(_profiles.enabled());
#if MESH_ENABLE_CARRIER_TOOLS || MESH_ENABLE_MORSE_RX
  if (serviceCarrierWave()) return;
#endif
  if (_profiles.enabled()) {
    const unsigned long now = millis();
    checkReceiveMode(static_cast<uint32_t>(now));

    const bool restart_scan = _profile_scan_generation[0] != _profiles.generation[0]
        || _profile_scan_generation[1] != _profiles.generation[1];
    if (!_profile_rxps_suspended || restart_scan) {
      // Entering scan mode or changing a profile retunes before sampling. A
      // sample from the previous physical tuple would be misleading.
      serviceProfileScan();
      return;
    }

    if (_nf_refresh_requested && _nf_calib_deadline == 0 && state == STATE_RX) {
      _nf_calib_deadline = now + NF_CONTINUOUS_TIMEOUT_MS;
    }
    if (_nf_refresh_requested && _floor_estimator.complete()
        && _secondary_floor_estimator.complete()) {
      if (_floor_estimator.publish(_noise_floor_centi_dbm, _noise_floor_valid)) {
        _noise_floor_valid = true;
#if MESH_ENABLE_TELEMETRY_HISTORY
        _nf_published_at[0] = static_cast<uint32_t>(now);
        _nf_published_generation[0] = _profiles.generation[0];
        _nf_published_valid |= 1U;
#endif
        _noise_floor = (_noise_floor_centi_dbm - 50) / 100;
      }
      if (_secondary_floor_estimator.publish(_noise_floor_secondary_centi_dbm,
                                              _noise_floor_secondary_valid)) {
        _noise_floor_secondary_valid = true;
#if MESH_ENABLE_TELEMETRY_HISTORY
        _nf_published_at[1] = static_cast<uint32_t>(now);
        _nf_published_generation[1] = _profiles.generation[1];
        _nf_published_valid |= 2U;
#endif
      }
      _nf_refresh_requested = false;
      _nf_last_calib = now;
      _nf_calib_deadline = 0;
      MESH_DEBUG_PRINTLN("RadioLibWrapper: profile noise floors = %d, %d",
                         (int)_noise_floor,
                         (int)((_noise_floor_secondary_centi_dbm - 50) / 100));
    } else if (_nf_refresh_requested && _nf_calib_deadline != 0
        && (long)(now - _nf_calib_deadline) >= 0) {
      // Busy profile visits must not pin dual-profile scanning indefinitely.
      _nf_refresh_requested = false;
      _nf_last_calib = now;
      _nf_calib_deadline = 0;
      _floor_estimator.reset();
      _secondary_floor_estimator.reset();
    } else {
      NoiseFloorEstimator& estimator = profileFloorEstimator(_active_profile);
      if (_nf_refresh_requested && state == STATE_RX
        && !estimator.complete() && !_rx_ps_armed
        && estimator.ready(static_cast<uint32_t>(now))
        && !(_nf_sample_from != 0 && (long)(now - _nf_sample_from) < 0)
        && !isChipBusy() && !isPacketPendingOrReceiving()) {
        // Preserve samples across visits, but never let one profile's RSSI
        // contaminate the other profile's independent noise-floor estimate.
        _nf_sample_from = now + NoiseFloorEstimator::SAMPLE_INTERVAL_MS;
        if (readReceiveMode() == 0) {
          estimator.reset(true);
        } else if (!isPacketPendingOrReceiving()) {
          estimator.add(getCurrentRSSI(), static_cast<uint32_t>(now));
        }
      }
    }
    // Sample before deciding whether to hop. serviceProfileScan() extends a
    // short visit only when the active profile still needs a safe sample.
    serviceProfileScan();
    return;
  }
  serviceProfileScan();
  if (_rx_ps_enabled && !_rx_ps_continuous_fallback) {
    rxPsWatchdogCheck();
  }
  unsigned long now = millis();
  checkReceiveMode(static_cast<uint32_t>(now));
  if (_nf_calib_active || _nf_refresh_requested) {
    noiseFloorCalibCheck(now);
  }
  if (_nf_refresh_requested
      && (!_rx_ps_enabled || _rx_ps_continuous_fallback)
      && !_nf_window_armed
      && state == STATE_RX) {
    // Measure the awake-time bound from actual continuous RX, not from begin()
    // or a request made while the radio is idle, transmitting, or starting up.
    _nf_calib_deadline = uint32_t(now) + NF_CONTINUOUS_TIMEOUT_MS;
    _nf_window_armed = true;
  }
  if (_nf_refresh_requested && _floor_estimator.complete()) {
    _nf_lazy_refresh = _floor_estimator.publish(_noise_floor_centi_dbm, _noise_floor_valid);
    if (_nf_lazy_refresh) {
      _noise_floor_valid = true;
#if MESH_ENABLE_TELEMETRY_HISTORY
      _nf_published_at[0] = static_cast<uint32_t>(now);
      _nf_published_generation[0] = _profiles.generation[0];
      _nf_published_valid |= 1U;
#endif
      // Retain the whole-dB API/wire format and the fractional display value.
      _noise_floor = (_noise_floor_centi_dbm - 50) / 100;
    }
    _nf_refresh_requested = false;
    _nf_last_calib = now;
    _nf_calib_deadline = 0;
    _nf_window_armed = false;
    _nf_refresh_attempted = true;

    MESH_DEBUG_PRINTLN("RadioLibWrapper: noise_floor = %d", (int)_noise_floor);

    if (_nf_calib_active) {
      endNoiseFloorCalib(now);   // complete block, including a held rise - back to duty cycle
    }
    return;
  }

  if (_nf_refresh_requested
      && (!_rx_ps_enabled || _rx_ps_continuous_fallback)
      && _nf_window_armed
      && static_cast<int32_t>(uint32_t(now) - uint32_t(_nf_calib_deadline)) >= 0) {
    // A continuously busy channel can reject every candidate sample. Do not
    // keep the MCU awake indefinitely; retain the previous floor and let the
    // next scheduled calibration request retry an invalid startup baseline.
    _nf_refresh_requested = false;
    _nf_last_calib = now;
    _nf_calib_deadline = 0;
    _nf_window_armed = false;
    _nf_lazy_refresh = false;
    _nf_refresh_attempted = true;
    _floor_estimator.reset();
  }

  if (_nf_refresh_requested && state == STATE_RX
      && !_floor_estimator.complete()) {
    // Noise floor is only sampled outside RX duty-cycle mode: continuously in
    // plain RX (powersaving off), or inside an on-demand calibration window
    // (powersaving on). Skip the first moments after entering RX or changing
    // gain while the frontend/AGC settles (unsettled GetRssiInst reads
    // ~-127 dBm garbage).
    if (!_rx_ps_armed
        && _floor_estimator.ready(static_cast<uint32_t>(now))
        && static_cast<int32_t>(uint32_t(now) - uint32_t(_nf_sample_from)) >= 0) {
      // Silent RX loss can leave software in STATE_RX while standby RSSI is
      // meaningless. Reject that observation before it can lower the floor.
      // Space rejected attempts too; an empty block must not hammer SPI.
      _nf_sample_from = uint32_t(now) + NoiseFloorEstimator::SAMPLE_INTERVAL_MS;
      if (isChipBusy() || isPacketPendingOrReceiving()) return;
      if (readReceiveMode() == 0) {
        _floor_estimator.reset(true);
        return;
      }
      // Admission is independent of the old floor, allowing upward recovery.
      // A mode probe is unavailable on some radio families; keep their existing
      // RSSI path. Preserve a packet IRQ arriving during the status transaction.
      // The estimator enforces 50 ms spacing and rejects invalid radio readings.
      if (!isPacketPendingOrReceiving()) {
        _floor_estimator.add(getCurrentRSSI(), static_cast<uint32_t>(now));
      }
    }
  }
}

void RadioLibWrapper::startRecv() {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) return;
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned && !_beacon_restoring) return;
#endif
  if (_cw_active || _profile_repair_required) return;
  int err = startReceiveMode();
  if (err == RADIOLIB_ERR_NONE) {
    // A very short frame may complete while startReceiveMode() returns.
#if defined(ESP32_PLATFORM)
    _esp32_profile_visit_started_us = static_cast<uint64_t>(esp_timer_get_time());
    _profile_visit_stamp = static_cast<uint32_t>(_esp32_profile_visit_started_us);
#else
    _profile_visit_stamp = profileTimestamp(_profiles.enabled());
#endif
    // Retain that RX interrupt instead of overwriting it with software state.
    state = STATE_RX;
    // RSSI is unsettled after every RX entry, including CAD/TX completion.
    // Keep the partial block, but wait for the frontend before its next sample.
    _nf_sample_from = uint32_t(millis())
        + (_rx_ps_armed ? 0 : NF_CALIB_SETTLE_MS);
    _startrx_fails = 0;
    _rx_retry_pending = false;
    _rx_retry_delay_ms = 0;
    if (_rx_ps_armed) {
      // (re)base the duty-cycle watchdog on the freshly armed cycle
      _wd_last_busy = isChipBusy();
      _wd_last_transition = millis();
      // Longest legitimate silence on the BUSY pin: one full cycle, plus the
      // extended RX after a (possibly false) preamble detect (2*rx + sleep),
      // plus a worst-case packet airtime, plus margin for TCXO/transitions.
      // Floored at 60s so a light-sleeping MCU (ESP32 wakes every ~30s) opens
      // an observation window every couple of wakeups instead of on each one.
      uint32_t rx_ms = _rx_ps_eff_rx_us / 1000, sleep_ms = _rx_ps_eff_sleep_us / 1000;
      _wd_stuck_thresh = (rx_ms + sleep_ms) + 2 * (2 * rx_ms + sleep_ms)
                         + getEstAirtimeFor(MAX_TRANS_UNIT) + 1000;
      if (_wd_stuck_thresh < 60000) _wd_stuck_thresh = 60000;
      // active observation window must cover one full duty cycle
      _wd_observe_ms = rx_ms + sleep_ms + 50;
      if (_wd_observe_ms > 1500) _wd_observe_ms = 1500;
    }
  } else {
    state = STATE_IDLE;
    _rx_ps_armed = false;
    // Recover a transient failure promptly, then permit whole-second timed
    // sleep under persistent failure. Direct retune/CAD/packet rearms remain
    // immediate; only recvRaw's repeated idle fallback observes this deadline.
    _rx_retry_delay_ms = _rx_retry_delay_ms == 0 ? 20
        : (_rx_retry_delay_ms < 1000 ? _rx_retry_delay_ms * 2 : 2000);
    _rx_retry_at = millis() + _rx_retry_delay_ms;
    _rx_retry_pending = true;
    if (_startrx_fails < 255) _startrx_fails++;
    MESH_DEBUG_PRINTLN("RadioLibWrapper: error: startReceiveMode(%d)", err);
  }
}

int RadioLibWrapper::startReceiveMode() {
  return startContinuousReceive();
}

int RadioLibWrapper::startReceivePowerSaving() {
  if (_nf_calib_active && _rx_ps_armed && isPacketPendingOrReceiving()) {
    return RADIOLIB_ERR_NONE;
  }
  if (_rx_ps_armed) stopReceiveDutyCycle();
  if (!_rx_ps_enabled || _nf_calib_active) {
    if (!_rx_ps_enabled) _rx_ps_continuous_fallback = false;
    const int16_t err = startContinuousReceive();
    if (err != RADIOLIB_ERR_NONE || !_rx_ps_enabled) _rx_ps_last_error = err;
    return err;
  }
  const bool planned_continuous = rxPowerSavingUsesContinuousFallback(_rx_ps_rx_us, _rx_ps_sleep_us);
  const bool invalid_timing = !planned_continuous
      && !canStartRxPowerSavingDutyCycle(_rx_ps_rx_us, _rx_ps_sleep_us, rxPowerSavingTransitionUs());
  if (planned_continuous || invalid_timing || !_rx_ps_arm_retry.canAttempt()) {
    _rx_ps_continuous_fallback = true;
    if (invalid_timing) _rx_ps_last_error = RX_POWERSAVING_ERR_TIMING;
    const int16_t err = startContinuousReceive();
    if (err != RADIOLIB_ERR_NONE) _rx_ps_last_error = err;
    return err;
  }
  const RadioLibIrqFlags_t irq_flags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS
      | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED);
  const RadioLibIrqFlags_t irq_mask = (1UL << RADIOLIB_IRQ_RX_DONE)
      | (1UL << RADIOLIB_IRQ_TIMEOUT) | (1UL << RADIOLIB_IRQ_CRC_ERR)
      | (1UL << RADIOLIB_IRQ_HEADER_ERR);
  uint32_t effective_rx_us = _rx_ps_rx_us, effective_sleep_us = _rx_ps_sleep_us;
  const int16_t err = armDutyCycle(irq_flags, irq_mask, &effective_rx_us, &effective_sleep_us);
  _rx_ps_last_error = err;
  if (err == RADIOLIB_ERR_NONE) {
    _rx_ps_armed = true;
    _rx_ps_continuous_fallback = false;
    _rx_ps_eff_rx_us = effective_rx_us;
    _rx_ps_eff_sleep_us = effective_sleep_us;
    _rx_ps_arm_retry.recordSuccess();
    return err;
  }
  _rx_ps_armed = false;
  _rx_ps_continuous_fallback = true;
  _rx_ps_eff_rx_us = _rx_ps_eff_sleep_us = 0;
  ++_rx_ps_arm_failures;
  _rx_ps_arm_retry.recordFailure();
  MESH_DEBUG_PRINTLN("RadioLibWrapper: startReceiveDutyCycle(%d), continuous RX fallback", err);
  stopDutyCycleHardware();
  const int16_t fallback_error = startContinuousReceive();
  if (fallback_error != RADIOLIB_ERR_NONE) _rx_ps_last_error = fallback_error;
  return fallback_error;
}

void RadioLibWrapper::stopReceiveDutyCycle() {
  // The duty-cycle sequencer only stops on RxDone or an explicit standby;
  // issuing other mode commands while it runs leads to undefined behaviour.
  const int16_t err = stopDutyCycleHardware();
  _rx_ps_armed = false;
  _rx_ps_eff_rx_us = _rx_ps_eff_sleep_us = 0;
  if (err != RADIOLIB_ERR_NONE) _rx_ps_last_error = err;
}

bool RadioLibWrapper::isPacketReady() {
  // In duty-cycle RX the DIO1 interrupt also fires for RX timeout (false
  // preamble detect) and header errors. GetRxBufferStatus still reports the
  // *previous* packet's length then, so reading the buffer would re-deliver
  // stale bytes as a ghost packet. Only read when the radio reports RxDone.
  // An unsupported probe retains the legacy event-only behavior.
  const int16_t ready = _radio->checkIrq(RADIOLIB_IRQ_RX_DONE);
  if (ready <= 0 && ready != RADIOLIB_ERR_UNSUPPORTED) {
    // DIO can remain high after a transient SPI error, so no second edge will
    // wake us. Retry the probe without treating the error as a valid packet.
    _loop_event_pending = ++_irq_probe_failures < 3;
    if (!_loop_event_pending) _irq_probe_failures = 0;
    return false;
  }
  _irq_probe_failures = 0;
  return ready > 0 || ready == RADIOLIB_ERR_UNSUPPORTED;
}

bool RadioLibWrapper::isInRecvMode() const {
  return state == STATE_RX;
}

bool RadioLibWrapper::getReceiveRetryDelay(uint32_t now, uint32_t& delay_ms) const {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) return false;
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return false;
#endif
  if (_cw_active) return false;
  if (state == STATE_RX && _rx_hold_continuous && _rx_ps_enabled
      && !_rx_ps_continuous_fallback && !_rx_ps_armed && !_nf_calib_active) {
    // A false preamble can expire without any wake interrupt. Keep the normal
    // empty-poll completion path running until it can restore duty-cycle RX.
    delay_ms = 0;
    return true;
  }
  if (!_rx_retry_pending || state != STATE_IDLE) return false;
  delay_ms = static_cast<int32_t>(now - _rx_retry_at) >= 0 ? 0 : _rx_retry_at - now;
  return true;
}

bool RadioLibWrapper::getNoiseFloorWakeDelay(uint32_t now, uint32_t& delay_ms) const {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) return false;
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return false;
#endif
  // Dual-profile scanning and foreign owners retain their existing cadence.
  // Failed RX startup has its separate retry deadline, not an active batch.
  if (_cw_active || _profiles.enabled() || state != STATE_RX
      || (!_nf_refresh_requested && !_nf_calib_active)) return false;
  if (_floor_estimator.complete()
      || (!_nf_window_armed && !_rx_ps_armed)) {
    delay_ms = 0;  // Publish or establish the continuous-RX timeout first.
    return true;
  }

  const int32_t until_attempt = static_cast<int32_t>(uint32_t(_nf_sample_from) - now);
  delay_ms = until_attempt > 0 ? uint32_t(until_attempt) : 0;
  if (!_rx_ps_armed) {
    const uint32_t until_sample = _floor_estimator.sampleDelay(now);
    if (until_sample > delay_ms) delay_ms = until_sample;
  }
  if (_nf_window_armed) {
    const int32_t until_timeout = static_cast<int32_t>(uint32_t(_nf_calib_deadline) - now);
    const uint32_t timeout = until_timeout > 0 ? uint32_t(until_timeout) : 0;
    if (timeout < delay_ms) delay_ms = timeout;
  }
  return true;
}

// RX PowerSaving
bool RadioLibWrapper::setRxPowerSaving(bool enabled, uint32_t rx_us, uint32_t sleep_us) {
  return trySetRxPowerSaving(enabled, rx_us, sleep_us) == mesh::RadioParamApplyResult::APPLIED;
}

mesh::RadioParamApplyResult RadioLibWrapper::trySetRxPowerSaving(bool enabled, uint32_t rx_us, uint32_t sleep_us) {
  using Result = mesh::RadioParamApplyResult;
  if (enabled && (!isValidRxPowerSavingPeriod(rx_us) || !isValidRxPowerSavingPeriod(sleep_us)
      || !supportsRxPowerSaving())) return Result::FAILED;
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return Result::BUSY;
#endif
  if (_cw_active) return Result::BUSY;
  if (_profile_rxps_suspended) {
    _profile_saved_rxps = enabled;
    _rx_ps_rx_us = rx_us;
    _rx_ps_sleep_us = sleep_us;
    _rx_ps_arm_retry.reset();
    _rx_ps_last_error = RADIOLIB_ERR_NONE;
    return Result::APPLIED;
  }

  if (!_profile_repair_required && _rx_ps_enabled == enabled
      && _rx_ps_rx_us == rx_us && _rx_ps_sleep_us == sleep_us) return Result::APPLIED;

  uint8_t resume_rx = beginReconfigure();
  if (resume_rx > 1) return resume_rx == 2 ? Result::BUSY : Result::FAILED;

  _rx_ps_enabled = enabled;
  _rx_ps_rx_us = rx_us;
  _rx_ps_sleep_us = sleep_us;
  _rx_ps_arm_retry.reset();
  _rx_ps_last_error = RADIOLIB_ERR_NONE;
  _rx_ps_continuous_fallback = enabled
      && rxPowerSavingUsesContinuousFallback(rx_us, sleep_us);
  endReconfigure(resume_rx);
  return Result::APPLIED;
}

RxPowerSavingStatus RadioLibWrapper::getRxPowerSavingStatus() const {
  RxPowerSavingStatus status;
  status.supported = supportsRxPowerSaving();
  status.armed = _rx_ps_armed;
  status.last_error = _rx_ps_last_error;
  status.arm_failures = _rx_ps_arm_failures;
  status.effective_rx_us = _rx_ps_armed ? _rx_ps_eff_rx_us : 0;
  status.effective_sleep_us = _rx_ps_armed ? _rx_ps_eff_sleep_us : 0;
  return status;
}

int RadioLibWrapper::recvRaw(uint8_t* bytes, int sz) {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) return 0;
#endif
#if MESH_RADIO_TRACE_ENABLED
  auto& radio_trace = mesh::radiotrace::trace();
  uint16_t trace_rx = mesh::radiotrace::NoIndex;
  radio_trace.context(_radio, _active_profile, _profile_generation);
#endif
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) return 0;
#endif
  if (_cw_active || state == STATE_TX_WAIT || _profile_repair_required) return 0;
  int len = 0;
  #if defined(USE_LR2021)
  bool receiver_stopped = false;
  #endif
  if (claimRadioInterrupt()) {
    last_radio_interrupt_millis = millis();   // ISR fired -> radio hardware is alive
    const bool packet_ready = isPacketReady();
    if (packet_ready) {
#if MESH_RADIO_TRACE_ENABLED
      if (radio_trace.pending()) trace_rx = radio_trace.receiveRead(_radio, mesh::radiotrace::nowUs());
#endif
      #if defined(USE_LR2021)
      // The LR2021 base reader clears FIFO/IRQs while otherwise staying in
      // continuous RX. Freeze reception before taking length/status/FIFO
      // snapshots so a new packet cannot be cleared halfway through the read.
      if (_radio->standby() != RADIOLIB_ERR_NONE) {
        n_recv_errors++;
        state = STATE_IDLE;
        return 0;
      }
      receiver_stopped = true;
      #endif
      if (_rx_ps_armed) {
        // RxDone stops the active receive window, but the duty-cycle RTC can
        // still be running. Stop it before reading and re-arming another mode.
        stopReceiveDutyCycle();
      }
      len = _radio->getPacketLength();
      if (len > 0) {
        if (len > sz) { len = sz; }
        // Cache packet status before readData() and before any new receive is
        // started. Both operations may replace the radio's packet metadata.
        _last_snr = _radio->getSNR();
        _last_rssi = _radio->getRSSI();
        int err = _radio->readData(bytes, len);
        if (err != RADIOLIB_ERR_NONE) {
          MESH_DEBUG_PRINTLN("RadioLibWrapper: error: readData(%d)", err);
          len = 0;
          n_recv_errors++;
        } else {
        //  Serial.print("  readData() -> "); Serial.println(len);
          n_recv++;
          last_recv_millis = millis();
          _rx_retry_pending = false;
          _rx_retry_delay_ms = 0;
        }
      }
    }
    // Retry a transient IRQ-status probe before rearming/clearing hardware.
    if (_loop_event_pending) return 0;
    #if defined(ESP32_PLATFORM) && (MESH_RADIO_PROFILE_CHIP == 1262 \
        || MESH_RADIO_PROFILE_CHIP == 1268 || MESH_RADIO_PROFILE_CHIP == 68)
    // DIO may reveal RX_DONE before its edge ISR publishes. Servicing that
    // packet can leave a late software event for an already-cleared IRQ.
    // Its bounded empty probes must not restart RX over a newer packet.
    // Keep the normal activity timeouts and inactive/error rearm behavior.
    if (!packet_ready && state == STATE_RX && !_rx_ps_armed
        && !isChipBusy() && isReceivingPacket()) return 0;
    #endif
    #if defined(USE_LR2021)
    // An early FIFO/SPI failure must not leave RX_DONE asserted. Standby also
    // covers spurious events for which no packet was read. No new hardware RX
    // event can arrive during this acknowledgement; rearm happens afterwards.
    if ((!receiver_stopped && _radio->standby() != RADIOLIB_ERR_NONE)
        || static_cast<LR2021*>(_radio)->clearRxFifo() != RADIOLIB_ERR_NONE
        || _radio->clearIrqFlags(RADIOLIB_LR2021_IRQ_RX_DONE) != RADIOLIB_ERR_NONE) {
      n_recv_errors++;
      state = STATE_IDLE;
      return 0;
    }
    #endif
    // Serviced receive events retain the normal immediate rearm, even if an
    // earlier idle start failed. A pending IRQ probe above still owns its FIFO.
    _rx_retry_pending = false;
    state = STATE_IDLE;   // need another startReceive()
  }

  if (len > 0 && _rx_ps_enabled && !_rx_ps_continuous_fallback) {
    // Dispatcher still needs the just-cached RSSI/SNR. Keep the receiver in
    // ordinary continuous mode until it has parsed and scored this packet,
    // then onReceiveProcessed() restores the duty cycle.
    _rx_hold_continuous = true;
    int err = _radio->startReceive();
    if (err == RADIOLIB_ERR_NONE) {
      state = STATE_RX;
      _rx_retry_pending = false;
      _rx_retry_delay_ms = 0;
      if (_nf_calib_active) {
        _nf_sample_from = millis() + NF_CALIB_SETTLE_MS;
      }
    } else {
      _rx_retry_delay_ms = 20;  // the delivered packet just proved RX was working
      _rx_retry_at = millis() + _rx_retry_delay_ms;
      _rx_retry_pending = true;
      MESH_DEBUG_PRINTLN("RadioLibWrapper: error: startReceive after packet (%d)", err);
    }
#if MESH_RADIO_TRACE_ENABLED
    if (trace_rx != mesh::radiotrace::NoIndex)
      radio_trace.receiveFinish(_radio, trace_rx, mesh::radiotrace::nowUs(),
          mesh::radiotrace::fingerprint(bytes, len), static_cast<uint16_t>(len), true);
#endif
    return len;
  }

  if (state != STATE_RX) {
    if (_rx_retry_pending) {
      const uint32_t now = millis();
      if (static_cast<int32_t>(now - _rx_retry_at) < 0) return len;
      // A command error can leave hardware receiving despite the idle software
      // state. Preserve that owner and move the retry deadline forward instead
      // of keeping a permanently due wake or issuing SPI while BUSY is high.
      if (isChipBusy() || isPacketPendingOrReceiving() || hasPendingRadioInterrupt()) {
        _rx_retry_at = now + 2000;
        return len;
      }
    }
    startRecv();
  }
#if MESH_RADIO_TRACE_ENABLED
  if (trace_rx != mesh::radiotrace::NoIndex)
    radio_trace.receiveFinish(_radio, trace_rx, mesh::radiotrace::nowUs(),
        len > 0 ? mesh::radiotrace::fingerprint(bytes, len) : 0,
        static_cast<uint16_t>(len), len > 0);
#endif
  return len;
}

void RadioLibWrapper::finishReceiveProcessing() {
  if (!_rx_hold_continuous) return;

  if (state == STATE_TX_WAIT) {
    _rx_hold_continuous = false;
    return;
  }
  // A second packet may have completed while Dispatcher handled the first.
  // Leave continuous RX intact until recvRaw() consumes that pending packet.
  if (hasPendingRadioInterrupt() || isReceivingPacket()) {
    return;
  }

  _rx_hold_continuous = false;
  if (!_rx_ps_enabled || _rx_ps_continuous_fallback || _nf_calib_active
      || _rx_ps_armed) return; // an empty/error IRQ may already have rearmed RXPS

  state = STATE_IDLE;
  startRecv();
}

uint32_t RadioLibWrapper::getEstAirtimeFor(int len_bytes) {
  const uint32_t airtime_us =
      static_cast<uint32_t>(_radio->getTimeOnAir(len_bytes));
  if (mesh::isEncodedRadioLibAirtimeError(airtime_us)) {
    MESH_DEBUG_PRINTLN(
        "RadioLibWrapper: invalid time-on-air estimate (0x%08lX)",
        (unsigned long)airtime_us);
    return 0;
  }
  return airtime_us / 1000;
}

#if defined(MESH_HIL_TX_FREQ_MHZ)
bool RadioLibWrapper::hilPacketTransmitAllowed() const {
#if MESH_ENABLE_TX_BEACONS
  if (_beacon_owned) {
    return !_beacon_restoring
        && mesh::hilTransmitFrequencyAllowed(_hil_beacon_tx_frequency_mhz);
  }
#endif
  // Check the committed physical cache, not only a requested profile. Failed
  // retunes, stale generations, and an unresolved refresh never authorize RF.
  if (!_params_valid || _profile_refresh_required || _active_profile > 1
      || (_active_profile == 1 && !_profiles.enabled())
      || _profile_generation != _profiles.generation[_active_profile]) return false;
  const auto& p = _profiles.params(_active_profile);
  return mesh::hilTransmitFrequencyAllowed(_cur_freq)
      && _cur_freq == p.freq && _cur_bw == p.bw && _cur_sf == p.sf && _cur_cr == p.cr
      && _physical_preamble == profilePreamble(_active_profile)
#if MESH_ENABLE_MESHTEXT_WIRE || MESH_ENABLE_ETHERNET_WIRE || MESH_ENABLE_HOST_KISS
      && _physical_protocol == p.protocol
#endif
      ;
}
#endif

bool RadioLibWrapper::startSendRaw(const uint8_t* bytes, int len) {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioTransmitAllowed()) return false;
#endif
#if defined(MESH_HIL_TX_FREQ_MHZ)
  if (!hilPacketTransmitAllowed()) {
    MESH_DEBUG_PRINTLN("HIL: packet TX blocked outside the proven allowed frequency");
    return false;
  }
#endif
  if (_cw_active || hasPendingRadioInterrupt()) return false;
  _rx_hold_continuous = false;
  if (_rx_ps_armed) {
    // stop the duty-cycle sequencer before SetTx, otherwise its next RTC
    // event can fire mid-transmission and abort the TX
    stopReceiveDutyCycle();
  }
  _board->onBeforeTransmit();
  if (hasPendingRadioInterrupt()) {
    _board->onAfterTransmit();
    return false;
  }
  _irq_probe_failures = 0;
#if MESH_RADIO_TRACE_ENABLED
  if (mesh::radiotrace::trace().pending()) {
    mesh::radiotrace::trace().context(_radio, _active_profile, _profile_generation);
    mesh::radiotrace::trace().transmitPrepare(_radio, mesh::radiotrace::nowUs(),
        mesh::radiotrace::fingerprint(bytes, len), static_cast<uint16_t>(len), _radio->getTimeOnAir(len));
  }
#endif
  int err = _radio->startTransmit((uint8_t *) bytes, len);
  if (err == RADIOLIB_ERR_NONE) {
    state = STATE_TX_WAIT;
    return true;
  }
  MESH_DEBUG_PRINTLN("RadioLibWrapper: error: startTransmit(%d)", err);
#if MESH_RADIO_TRACE_ENABLED
  if (mesh::radiotrace::trace().pending())
    mesh::radiotrace::trace().transmitFinish(_radio, mesh::radiotrace::nowUs(), false);
#endif
  idle();   // trigger another startRecv()
  _board->onAfterTransmit();
  if (err == RADIOLIB_ERR_SPI_CMD_TIMEOUT) {
    // A stuck BUSY line cannot be repaired with another SPI command. Radios
    // with a reset pin recover immediately; others use their safest fallback.
    recoverRadio(true);
  }
  return false;
}

bool RadioLibWrapper::isSendComplete() {
  if (state == STATE_TX_WAIT && claimRadioInterrupt()) {
    const int16_t done = _radio->checkIrq(RADIOLIB_IRQ_TX_DONE);
    if (done <= 0 && done != RADIOLIB_ERR_UNSUPPORTED) {
      // getIrqFlags() may hide a failed SPI read as zero. Once DIO fired,
      // keep probing until TX_DONE is observed or Dispatcher expires this TX.
      _loop_event_pending = true;
      return false;
    }
    _irq_probe_failures = 0;
    state = STATE_IDLE;
    n_sent++;
#if MESH_RADIO_TRACE_ENABLED
    if (mesh::radiotrace::trace().pending())
      mesh::radiotrace::trace().transmitFinish(_radio, mesh::radiotrace::nowUs(), true);
#endif
    return true;
  }
  return false;
}

void RadioLibWrapper::onSendFinished() {
#if MESH_RADIO_TRACE_ENABLED
  if (mesh::radiotrace::trace().pending())
    mesh::radiotrace::trace().transmitFinish(_radio, mesh::radiotrace::nowUs(), false);
#endif
  _radio->finishTransmit();
  // Receive has not been rearmed yet: these are completion/probe events for
  // the retired TX. They must not block the Dispatcher's timeout recovery.
  claimRadioInterrupt();
  _irq_probe_failures = 0;
  _board->onAfterTransmit();
  _rx_retry_pending = false;  // TX retirement gets its usual immediate RX rearm
  state = STATE_IDLE;
}

int16_t RadioLibWrapper::performChannelScan() {
  return performChannelScanWithTimeout(cadScanTimeoutMillis());
}

int16_t RadioLibWrapper::performChannelScanWithTimeout(unsigned long timeout_ms) {
  // RadioLib's blocking scanChannel() waits forever if the radio never raises
  // its CAD-done IRQ. Keep CAD bounded so a missing IRQ cannot starve the main
  // loop until the MCU watchdog resets the node.
  int16_t result = _radio->startChannelScan();
  if (result != RADIOLIB_ERR_NONE) return result;

  const unsigned long started = millis();
  unsigned long last_watchdog_service = started;
  while (millis() - started < timeout_ms) {
    result = _radio->getChannelScanResult();
    if (result != RADIOLIB_ERR_UNKNOWN) return result;

    const unsigned long now = millis();
    if (now - last_watchdog_service >= 1000UL) {
      _board->serviceWatchdog();
      last_watchdog_service = now;
    }
    yield();
  }

  MESH_DEBUG_PRINTLN("RadioLibWrapper: CAD scan timed out after %lu ms", timeout_ms);
  return RADIOLIB_ERR_RX_TIMEOUT;
}

bool RadioLibWrapper::isChannelActive() {
#if MESH_ENABLE_RADIO_DIRECTION
  if (!mesh::radioDirection::radioReceiveAllowed()) {
    if (_cw_active || state == STATE_TX_WAIT || isChipBusy()) return true;
    if (!_cad_enabled) return false;
    const int16_t result = performChannelScan();
    const bool stopped = _radio->standby() == RADIOLIB_ERR_NONE;
    const bool cleared = _radio->clearIrqFlags(UINT32_MAX) == RADIOLIB_ERR_NONE;
    claimRadioInterrupt();
    state = STATE_IDLE;
    if (result != RADIOLIB_CHANNEL_FREE || !stopped || !cleared) {
      _board->n_cad_busy++;
      return true;
    }
    return false;
  }
#endif
  if (_cw_active) return true;
  if (isPacketPendingOrReceiving() || state == STATE_TX_WAIT) return true;
  // int.thresh: RSSI-based interference detection (relative to noise floor).
  // In RX duty-cycle mode only checked while the chip is in a listen window
  // (during the sleep window the frontend is off and the read would stall).
  if (_threshold != 0 && !_profiles.enabled() && !(_rx_ps_armed && isChipBusy())
      && getCurrentRSSI() > _noise_floor + _threshold) return true;

  // cad: hardware channel activity detection
  if (_cad_enabled) {
    if (_rx_ps_armed) {
      // CAD cannot safely run on top of an active duty-cycle sequencer: its
      // pending RTC event can force standby in the middle of the scan.
      stopReceiveDutyCycle();
    }
    int16_t result = performChannelScan();
    // CAD also publishes an IRQ event. Consume it before restarting RX so recvRaw() doesn't
    // try to read a non-existent packet and count a spurious recv error.
    claimRadioInterrupt();
    state = STATE_IDLE;
    startRecv();
    if (result != RADIOLIB_CHANNEL_FREE || state != STATE_RX
        || isPacketPendingOrReceiving()) {
      _board->n_cad_busy++;
      return true;
    }
  }

  return false;
}

bool RadioLibWrapper::isReceivingPassive(int interference_margin_db) {
#if MESH_ENABLE_RADIO_DIRECTION
  // A TX-only node has no continuous-RX noise baseline or forwarding echoes.
  // Use bounded CAD instead of waiting forever for either of those RX states.
  if (!mesh::radioDirection::radioReceiveAllowed()) return isChannelActive();
#endif
  if (_cw_active) return true;
  // RX duty-cycle radios hold BUSY high while asleep. Do not attempt an SPI
  // IRQ/RSSI read until the next listen window. Treat the unknown channel as
  // busy so the retry is deferred instead of transmitting blind; Dispatcher
  // retains its bounded busy timeout as a last-resort escape.
  if (isChipBusy()) return true;
  if (isPacketPendingOrReceiving()) return true;
  // Four-symbol visits are too short for the settled single-channel noise
  // calibration. Keep retries in RX and use the preamble/header IRQs here.
  if (_profiles.enabled()) return false;

  requestPeriodicNoiseFloorRefresh();
  // Until the startup baseline is ready, defer rather than compare against the
  // zero-initialized floor and transmit blind. Dispatcher retains its bounded
  // busy timeout if sampling cannot complete.
  if (!_noise_floor_valid) return true;

  // Use a fixed margin for retries even when the normal interference threshold
  // is disabled. This is passive (RSSI only): unlike CAD it does not restart RX
  // and cannot erase the forwarding echo that would cancel the retry.
  return interference_margin_db > 0
      && getCurrentRSSI() - _noise_floor >= interference_margin_db;
}

uint8_t RadioLibWrapper::getRadioState() const {
  return state;
}

float RadioLibWrapper::getLastRSSI() const {
  return _last_rssi;
}
float RadioLibWrapper::getLastSNR() const {
  return _last_snr;
}

float RadioLibWrapper::packetScoreInt(float snr, int sf, int packet_len) {
  if (sf < 7) return 0.0f;

  // Semtech's approximate threshold drops 2.5 dB for each step from SF7.
  const float snr_threshold = -7.5f - (sf - 7) * 2.5f;
  if (snr < snr_threshold) return 0.0f;

  const float success_rate = (snr - snr_threshold) * 0.1f;
  const float collision_penalty = 1.0f - packet_len / 256.0f;
  const float score = success_rate * collision_penalty;
  return score < 1.0f ? score : 1.0f;
}

PacketMillis RadioLibWrapper::calcMaxPacketMillis(uint8_t sf, float bw, uint8_t cr, uint16_t preambleSymbols) {
  // based on RadioLib's calculateTimeOnAir()
  uint32_t tsym_us = ((uint32_t)10000 << sf) / (bw * 10);
  uint32_t sfCoeff1_x4 = (sf == 5 || sf == 6) ? 25 : 17; // 6.25 : 4.25, semtech magic numbers to account for sync word + sfd

  // preamble + syncword + sfd + header
  uint64_t preamble_us = (((uint64_t)(preambleSymbols + 8) * 4 + sfCoeff1_x4) * tsym_us) / 4;
  // Allow four extra preamble symbols before giving up on header detection.
  // This is an RX guard only; keep it out of the payload airtime subtraction.
  const uint64_t preamble_guard_us = preamble_us + 4ULL * tsym_us;

  // airtime for max packet at current radio settings
  uint32_t total_us   = _radio->getTimeOnAir(MAX_TRANS_UNIT);
  // airtime for payload only (no preamble, header or SOF)
  const uint32_t fallback_total_us = 4000000UL;
  uint32_t payload_us = total_us > preamble_us
      ? total_us - preamble_us
      : (fallback_total_us > preamble_us ? fallback_total_us - preamble_us
                                        : fallback_total_us);
  // rescale payload_us for max possible CR
  if (cr >= 5 && cr < 8) { payload_us = (payload_us * 8) / cr; }

  return PacketMillis {static_cast<uint32_t>((preamble_guard_us + 999) / 1000),
                       (payload_us + 999) / 1000};
}
