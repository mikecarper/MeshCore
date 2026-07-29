#pragma once

#include <RadioLib.h>
#include "MeshCore.h"

class CustomLR1110 : public LR1110 {
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_boosted = false;

  public:
    CustomLR1110(Module *mod) : LR1110(mod) { }

    size_t getPacketLength(bool update) override {
      size_t len = LR1110::getPacketLength(update);
      if (len == 0 && getIrqStatus() & RADIOLIB_LR11X0_IRQ_HEADER_ERR) {
        // we've just received a corrupted packet
        // this may have triggered a bug causing subsequent packets to be shifted
        // call standby() to return radio to known-good state
        // recvRaw will call startReceive() to restart rx
        MESH_DEBUG_PRINTLN("LR1110: got header err, calling standby()");
        standby();
      }
      return len;
    }

    float getFreqMHz() const { return freqMHz; }

    int16_t startReceiveDutyCycle(uint32_t rxPeriod, uint32_t sleepPeriod,
                                  RadioLibIrqFlags_t irqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS,
                                  RadioLibIrqFlags_t irqMask = RADIOLIB_IRQ_RX_DEFAULT_MASK) {
      // RadioLib's LR11x0 duty-cycle path stages RX but does not call
      // launchMode(), where the software RF switch is normally set to RX.
      // RadioLib 7.6 does not expose LR11x0::tcxoDelay. MeshCore initializes
      // these radios with RadioLib's 5000 us default TCXO delay.
      const uint32_t transitionTime = 5000 + 1000;
      if (sleepPeriod <= transitionTime) {
        return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      }
      sleepPeriod -= transitionTime;

      uint32_t rxPeriodRaw = (rxPeriod * 32768UL) / 1000000UL;
      uint32_t sleepPeriodRaw = (sleepPeriod * 32768UL) / 1000000UL;

      if ((rxPeriodRaw & 0xFF000000) || (rxPeriodRaw == 0)) {
        return RADIOLIB_ERR_INVALID_RX_PERIOD;
      }

      if ((sleepPeriodRaw & 0xFF000000) || (sleepPeriodRaw == 0)) {
        return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      }

      RadioModeConfig_t cfg = {
        .receive = {
          .timeout = RADIOLIB_LR11X0_RX_TIMEOUT_INF,
          .irqFlags = irqFlags,
          .irqMask = irqMask,
          .len = 0,
        }
      };
      int16_t state = this->stageMode(RADIOLIB_RADIO_MODE_RX, &cfg);
      RADIOLIB_ASSERT(state);

      this->mod->setRfSwitchState(Module::MODE_RX);
      return this->setRxDutyCycle(rxPeriodRaw, sleepPeriodRaw, RADIOLIB_LR11X0_RX_DUTY_CYCLE_MODE_RX);
    }

    int16_t setRxBoostedGainMode(bool en) {
      _rx_boosted = en;
      return LR1110::setRxBoostedGainMode(en);
    }

    bool getRxBoostedGainMode() const { return _rx_boosted; }

    int16_t startReceive() override {
      // Make preamble detection visible to CAD while retaining RadioLib's
      // normal RX-complete and error events.
      return LR1110::startReceive(
          RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED,
          RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED),
          RADIOLIB_IRQ_RX_DEFAULT_MASK, 0);
    }

    // BUSY high means the chip is asleep (RX duty-cycle sleep window) or mid
    // command; any SPI access would stall until the chip's next listen window.
    bool isChipBusy() {
      uint32_t busy = this->mod->getGpio();
      return busy != RADIOLIB_NC && this->mod->hal->digitalRead(busy);
    }

    bool isReceiving() {
      if (isChipBusy()) return false;   // asleep, cannot be mid-receive

      uint32_t irq = getIrqStatus();
      bool preamble = irq & RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED;      // bit 4
      bool header   = irq & RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID; // bit 5
      bool hdrErr   = irq & RADIOLIB_LR11X0_IRQ_HEADER_ERR;             // bit 6
      uint32_t now  = millis();
      if (hdrErr) {
        clearIrqState(RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED | RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID | RADIOLIB_LR11X0_IRQ_HEADER_ERR);
        _activityAt = 0;
        _headerSeen = false;
        return false;
      }
      if (!header && _headerSeen) {
        // Another path consumed the header IRQ; reset only our local timer.
        _activityAt = 0;
        _headerSeen = false;
        return false;
      }
      if (header) {
        if (!_headerSeen) { _headerSeen = true; _activityAt = now; };
        if (now - _activityAt > _maxPayloadMillis) {
          MESH_DEBUG_PRINTLN("Clearing header IRQ after %ums", _maxPayloadMillis);
          clearIrqState(RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED | RADIOLIB_LR11X0_IRQ_SYNC_WORD_HEADER_VALID | RADIOLIB_LR11X0_IRQ_HEADER_ERR);
          _activityAt = 0; _headerSeen = false;
          return false;
        }
        return true;
      }
      if (preamble) {
        if (_activityAt == 0) _activityAt = now;
        if (now - _activityAt > _preambleMillis) {
          clearIrqState(RADIOLIB_LR11X0_IRQ_PREAMBLE_DETECTED);
          _activityAt = 0;
          MESH_DEBUG_PRINTLN("Clearing preamble IRQ after %ums", _preambleMillis);

          return false;
        }
        return true;
      }
      _activityAt = 0; _headerSeen = false;
      return false;
    }

    void setPreambleMillis(uint32_t preambleMillis) {
      _preambleMillis = preambleMillis;
      MESH_DEBUG_PRINTLN("Set _preambleMillis=%u", _preambleMillis);
    }
    void setMaxPayloadMillis(uint32_t payloadMillis) {
      _maxPayloadMillis = payloadMillis;
      MESH_DEBUG_PRINTLN("Set _maxPayloadMillis=%u", _maxPayloadMillis);
    }

    uint8_t getSpreadingFactor() const { return spreadingFactor; }
};
