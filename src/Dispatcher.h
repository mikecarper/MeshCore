#pragma once

#include <MeshCore.h>
#include <Identity.h>
#include <Packet.h>
#include <Utils.h>
#ifndef RADIO_LIVENESS_SOFT_ONLY
  #include <helpers/RadioLivenessTracker.h>
#endif
#include <string.h>

namespace mesh {

enum class RadioParamApplyResult : uint8_t {
  APPLIED,
  BUSY,
  FAILED
};

/**
 * \brief  Abstraction of local/volatile clock with Millisecond granularity.
*/
class MillisecondClock {
public:
  virtual unsigned long getMillis() = 0;
};

/**
 * \brief  Abstraction of this device's packet radio.
*/
class Radio {
public:
  virtual void begin() { }

  /**
   * \brief  polls for incoming raw packet.
   * \param  bytes  destination to store incoming raw packet.
   * \param  sz   maximum packet size allowed.
   * \returns 0 if no incoming data, otherwise length of complete packet received.
  */
  virtual int recvRaw(uint8_t* bytes, int sz) = 0;

  /** Called after the received packet and its radio metadata have been processed. */
  virtual void onReceiveProcessed() { }

  /**
   * \returns  estimated transmit air-time needed for packet of 'len_bytes', in milliseconds.
  */
  virtual uint32_t getEstAirtimeFor(int len_bytes) = 0;

  virtual float packetScore(float snr, int packet_len) = 0;

  /**
   * \brief  starts the raw packet send. (no wait)
   * \param  bytes   the raw packet data
   * \param  len  the length in bytes
   * \returns true if successfully started
  */
  virtual bool startSendRaw(const uint8_t* bytes, int len) = 0;

  /**
   * \brief  Sets LoRa coding rate for subsequent transmits/receives.
   * \returns true if the radio accepted the coding rate.
  */
  virtual bool setCodingRate(uint8_t cr) { return false; }

  /**
   * \returns true if the previous 'startSendRaw()' completed successfully.
  */
  virtual bool isSendComplete() = 0;

  /**
   * \brief  a hook for doing any necessary clean up after transmit.
  */
  virtual void onSendFinished() = 0;

  /**
   * \brief  do any processing needed on each loop cycle
   */
  virtual void loop() { }

  virtual void idle() { }
  virtual void startRecv() { }

  // Recover a radio whose MCU driver is still running but whose RF state has
  // stopped making progress.  hard=false is a safe receive/AGC re-arm;
  // hard=true may reset only the radio peripheral.  The default remains useful
  // for simple radios that do not expose a dedicated reset.
  virtual bool recoverRadio(bool hard) {
    (void)hard;
    idle();
    startRecv();
    return true;
  }

  virtual int getNoiseFloor() const { return 0; }
  virtual float getNoiseFloorDbm() const {
    return static_cast<float>(getNoiseFloor());
  }

  virtual void triggerNoiseFloorCalibrate(int threshold) { }

  // Discard the current estimate and establish a new baseline. Use this after
  // changing receive-path gain, where the old floor is no longer a valid
  // filter reference for normal periodic calibration.
  virtual void recalibrateNoiseFloor() { }

  virtual void setCADEnabled(bool enable) { }

  // LoRa wrappers shadow these with their configurable CAD timeout. Other
  // transports (such as ESP-NOW) support only the automatic/default value.
  bool setCADScanTimeoutMillis(uint32_t timeout_ms) { return timeout_ms == 0; }
  uint32_t getCADScanTimeoutMillis() const { return 0; }

  virtual void resetAGC() { }

  virtual uint8_t getRadioState() const { return 0; }
  virtual unsigned long getLastRecvMillis() const { return 0; }
  virtual unsigned long getLastRadioInterruptMillis() const { return 0; }

  virtual bool isInRecvMode() const = 0;

  virtual bool supportsRxPowerSaving() const { return false; }
  virtual bool setRxPowerSaving(bool enabled, uint32_t rx_us, uint32_t sleep_us) { return !enabled; }
  virtual bool isRxPowerSavingContinuousFallback() const { return false; }
  virtual bool supportsRxPowerSavingRfRxDisable() const { return false; }
  virtual bool setRxPowerSavingRfRxDisabled(bool disabled) {
    (void)disabled;
    return false;
  }
  virtual bool isRxPowerSavingRfRxDisabled() const { return false; }

  /**
   * \brief Attempts to change the radio modulation parameters without waiting.
   *
   * BUSY means the request is valid but the radio is temporarily unable to
   * leave RX/TX. Callers may retry it from their main loop. FAILED means the
   * radio attempted or rejected the change and retrying the same tuple is not
   * expected to help.
   */
  virtual RadioParamApplyResult trySetParams(float freq, float bw, uint8_t sf, uint8_t cr,
                                             const uint32_t* rx_ps_timings = nullptr) {
    (void)freq;
    (void)bw;
    (void)sf;
    (void)cr;
    (void)rx_ps_timings;
    return RadioParamApplyResult::FAILED;
  }

  /**
   * \returns  true if the radio is currently mid-receive of a packet.
  */
  virtual bool isReceiving() { return false; }

  /**
   * \brief Non-invasive channel check for a queued retry.
   *
   * Implementations should avoid CAD or any operation that restarts RX, because
   * doing so can hide the downstream forwarding echo that would cancel the
   * retry. Radios without a passive implementation retain the legacy check.
   */
  virtual bool isReceivingPassive(int interference_margin_db) {
    (void)interference_margin_db;
    return isReceiving();
  }

  virtual float getLastRSSI() const { return 0; }
  virtual float getLastSNR() const { return 0; }

  /**
   * \returns  number of receive errors (e.g. CRC failures) since last reset; 0 if not tracked.
  */
  virtual uint32_t getPacketsRecvErrors() const { return 0; }
};

/**
 * \brief  An abstraction for managing instances of Packets (eg. in a static pool),
 *        and for managing the outbound packet queue.
*/
class PacketManager {
public:
  virtual Packet* allocNew() = 0;
  virtual void free(Packet* packet) = 0;

  virtual bool queueOutbound(Packet* packet, uint8_t priority, uint32_t scheduled_for) = 0;
  virtual Packet* getNextOutbound(uint32_t now) = 0;    // by priority
  virtual Packet* peekNextOutbound(uint32_t now) {
    (void)now;
    return NULL;
  }
  // Managers that shed queued outbound packets return them here instead of
  // freeing them silently. Dispatcher will run the normal send-failure
  // lifecycle hook before returning each packet to the pool.
  virtual Packet* getNextDroppedOutbound() { return NULL; }
  virtual int getOutboundCount(uint32_t now) const = 0;
  virtual int getOutboundTotal() const = 0;
  // Returns the earliest runnable time in the queue. A queue with any overdue
  // entry reports `now`, which keeps rollover-safe timer comparisons local to
  // the queue implementation.
  virtual bool getNextOutboundTime(uint32_t now, uint32_t& scheduled_for) const {
    (void)now;
    (void)scheduled_for;
    return false;
  }
  virtual int getFreeCount() const = 0;
  virtual Packet* getOutboundByIdx(int i) = 0;
  virtual Packet* removeOutboundByIdx(int i) = 0;
  virtual void queueInbound(Packet* packet, uint32_t scheduled_for) = 0;
  virtual Packet* getNextInbound(uint32_t now) = 0;
  virtual bool getNextInboundTime(uint32_t now, uint32_t& scheduled_for) const {
    (void)now;
    (void)scheduled_for;
    return false;
  }
};

typedef uint32_t  DispatcherAction;

#define ACTION_RELEASE           (0)
#define ACTION_MANUAL_HOLD       (1)
#define ACTION_RETRANSMIT(pri)   (((uint32_t)1 + (pri))<<24)
#define ACTION_RETRANSMIT_DELAYED(pri, _delay)  ((((uint32_t)1 + (pri))<<24) | (_delay))

#define ERR_EVENT_FULL              (1 << 0)
#define ERR_EVENT_CAD_TIMEOUT       (1 << 1)
#define ERR_EVENT_STARTRX_TIMEOUT   (1 << 2)
#define ERR_EVENT_RADIO_WATCHDOG    (1 << 3)

#ifndef RADIO_WATCHDOG_MS
  #define RADIO_WATCHDOG_MS  300000   // 5 minutes
#endif

/**
 * \brief  The low-level task that manages detecting incoming Packets, and the queueing
 *      and scheduling of outbound Packets.
*/
class Dispatcher {
  Packet* outbound;  // current outbound packet
  unsigned long outbound_expiry, outbound_start, total_air_time, rx_air_time;
  unsigned long outbound_radio_retry_at;
  unsigned long last_observed_radio_irq;
#ifdef RADIO_LIVENESS_SOFT_ONLY
  unsigned long last_radio_activity_ms;
#else
  RadioLivenessTracker radio_liveness;
#endif
  uint8_t outbound_restore_cr;
  unsigned long next_tx_time;
  unsigned long cad_busy_start;
  unsigned long radio_nonrx_start;
  unsigned long next_floor_calib_time, next_agc_reset_time;
  bool  prev_isrecv_mode;
  bool  radio_available;
  bool  dispatcher_started;
  bool  outbound_radio_retry_pending;
  bool  outbound_radio_retry_used;
#ifndef RADIO_LIVENESS_SOFT_ONLY
  bool  nonrx_soft_recovery_attempted;
#endif
  uint32_t n_sent_flood, n_sent_direct;
  uint32_t n_recv_flood, n_recv_direct;
  unsigned long tx_budget_ms;
  unsigned long last_budget_update;
  unsigned long duty_cycle_window_ms;

  void processRecvPacket(Packet* pkt);
  void releaseDroppedOutbound();
  bool startOutboundTransmit();
  bool scheduleOutboundRadioRetry();
  void failOutboundTransmit();
  void restoreOutboundTxOverrides();
  void updateTxBudget();

protected:
  PacketManager* _mgr;
  Radio* _radio;
  MillisecondClock* _ms;
  uint16_t _err_flags;

  Dispatcher(Radio& radio, MillisecondClock& ms, PacketManager& mgr)
    : _radio(&radio), _ms(&ms), _mgr(&mgr)
  {
    outbound = NULL;
    outbound_radio_retry_at = 0;
    outbound_radio_retry_pending = false;
    outbound_radio_retry_used = false;
    outbound_restore_cr = 0;
    total_air_time = rx_air_time = 0;
    next_tx_time = ms.getMillis();
    cad_busy_start = 0;
    next_floor_calib_time = next_agc_reset_time = 0;
    _err_flags = 0;
    radio_nonrx_start = 0;
    prev_isrecv_mode = true;
    radio_available = true;
    dispatcher_started = false;
    tx_budget_ms = 0;
    last_budget_update = 0;
    duty_cycle_window_ms = 3600000;
    last_observed_radio_irq = 0;
#ifdef RADIO_LIVENESS_SOFT_ONLY
    last_radio_activity_ms = 0;
#else
    nonrx_soft_recovery_attempted = false;
#endif
  }

  virtual DispatcherAction onRecvPacket(Packet* pkt) = 0;

  virtual void logRxRaw(float snr, float rssi, const uint8_t raw[], int len) { }   // custom hook

  virtual void logRx(Packet* packet, int len, float score) { }   // hooks for custom logging
  virtual void logTx(Packet* packet, int len) { }
  virtual void logTxFail(Packet* packet, int len) { }
  virtual void onTracePacketQueuedForSend(Packet* packet) { }
  virtual void onSendComplete(Packet* packet) { }
  virtual void onSendFail(Packet* packet) { }
  virtual const char* getLogDateTime() { return ""; }

  virtual float getAirtimeBudgetFactor() const;
  virtual int calcRxDelay(float score, uint32_t air_time) const;
  virtual bool shouldBypassRxDelay(const Packet* packet) {
    (void)packet;
    return false;
  }
  virtual int calcRxDelayForPacket(const Packet* packet, float score,
                                   uint32_t air_time) {
    return shouldBypassRxDelay(packet) ? 0 : calcRxDelay(score, air_time);
  }
  virtual uint32_t getCADFailRetryDelay() const;
  virtual uint32_t getCADFailMaxDuration() const;
  virtual uint8_t getDefaultTxCodingRate() const { return 0; }
  virtual bool allowPacketTransmit(const Packet* packet) const { return true; }
  virtual bool usePassiveChannelCheck(const Packet* packet) const {
    (void)packet;
    return false;
  }
  virtual int getRetryInterferenceMargin() const { return 12; }
  virtual int getInterferenceThreshold() const { return 0; }    // disabled by default
  virtual bool getCADEnabled() const { return false; }    // hardware CAD disabled by default
  virtual int getAGCResetInterval() const { return 0; }    // disabled by default
  virtual unsigned long getDutyCycleWindowMs() const { return 3600000; }
#ifdef WITH_MQTT_BRIDGE
  virtual uint32_t getRadioWatchdogMillis() const;  // observer-only radio recovery
#endif
  const Packet* getOutboundInFlight() const { return outbound; }
  // Milliseconds until Dispatcher can next make progress on a queued packet.
  // This includes queue schedules, delayed inbound processing, airtime-budget
  // waits, and short channel-busy deferrals.
  bool getNextQueueWakeDelay(uint32_t& delay_millis) const;
  bool hasQueuedWorkDue() const {
    uint32_t delay_millis;
    return getNextQueueWakeDelay(delay_millis) && delay_millis == 0;
  }
  bool queueOutboundPacket(Packet* packet, uint8_t priority, uint32_t delay_millis);
  bool tryParsePacket(Packet* pkt, const uint8_t* raw, int len);
  void setRadioAvailable(bool available);
  bool isRadioAvailable() const { return radio_available; }

public:
  void begin();
  void loop();

  Packet* obtainNewPacket();
  void releasePacket(Packet* packet);
  bool sendPacket(Packet* packet, uint8_t priority, uint32_t delay_millis=0);

  unsigned long getTotalAirTime() const { return total_air_time; }
  unsigned long getReceiveAirTime() const {return rx_air_time; }
  unsigned long getRemainingTxBudget() const { return tx_budget_ms; }
  uint32_t getNumSentFlood() const { return n_sent_flood; }
  uint32_t getNumSentDirect() const { return n_sent_direct; }
  uint32_t getNumRecvFlood() const { return n_recv_flood; }
  uint32_t getNumRecvDirect() const { return n_recv_direct; }
  uint16_t getErrFlags() const { return _err_flags; }  // Get error flags
  bool hasOutbound() const { return outbound != NULL; }
  bool isCurrentOutbound(const Packet* packet) const { return outbound == packet; }
  void resetStats() {
    n_sent_flood = n_sent_direct = n_recv_flood = n_recv_direct = 0;
    _err_flags = 0;
  }

  // helper methods
  bool millisHasNowPassed(unsigned long timestamp) const;
  unsigned long futureMillis(int millis_from_now) const;

private:
#if MESH_PACKET_LOGGING
  void logPacketLine(const char* direction, const Packet* packet, int len,
                     bool include_rx_metrics, float score,
                     uint32_t air_time) __attribute__((noinline));
#endif
  void checkRecv();
  void checkSend();
};

}
