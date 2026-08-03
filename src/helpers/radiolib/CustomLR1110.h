#pragma once

#include <RadioLib.h>
#include "MeshCore.h"
#include "LR1110RxRecovery.h"

class CustomLR1110 : public LR1110 {
  uint32_t _preambleMillis = 66;
  uint32_t _maxPayloadMillis = 3934;
  uint32_t _activityAt = 0;
  bool _headerSeen = false;
  bool _rx_boosted = false;

  public:
    CustomLR1110(Module *mod) : LR1110(mod) { }

    int16_t recoverReceivePath() {
      _activityAt = 0;
      _headerSeen = false;

      // Standby is the operation known to reset the LR1110's unreported
      // four-byte RX-buffer displacement. Clear stale bytes and IRQ state too,
      // then let RadioLibWrapper re-arm receive mode.
      int16_t state = standby();
      int16_t next = clearRxBuffer();
      if (state == RADIOLIB_ERR_NONE) state = next;
      next = clearIrqState(RADIOLIB_LR11X0_IRQ_ALL);
      if (state == RADIOLIB_ERR_NONE) state = next;
      return state;
    }

    size_t getPacketLength(bool update) override {
      // GetRxBufferStatus can report either zero or a stale/nonzero length for
      // a header-error event. Recover based on the IRQ itself, never its length.
      if (getIrqStatus() & RADIOLIB_LR11X0_IRQ_HEADER_ERR) {
        MESH_DEBUG_PRINTLN("LR1110: header error, resetting RX path");
        recoverReceivePath();
        return 0;
      }
      return LR1110::getPacketLength(update);
    }

    int16_t readData(uint8_t* data, size_t len) override {
      int16_t state = LR1110::readData(data, len);
      if (state != RADIOLIB_ERR_NONE) return state;

      // Defense in depth: if the trigger IRQ was missed, never pass the known
      // shifted/truncated representation up to Dispatcher where it can look
      // like a valid transport flood and be repeated by the mesh.
      if (mesh::hasLR1110RxBufferShiftSignature(data, len)) {
        MESH_DEBUG_PRINTLN("LR1110: four-byte RX shift detected, dropping packet");
        recoverReceivePath();
        return RADIOLIB_ERR_CRC_MISMATCH;
      }
      return RADIOLIB_ERR_NONE;
    }

    float getFreqMHz() const { return freqMHz; }

    int16_t startReceiveDutyCycle(uint32_t rxPeriod, uint32_t sleepPeriod,
                                  RadioLibIrqFlags_t irqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS,
                                  RadioLibIrqFlags_t irqMask = RADIOLIB_IRQ_RX_DEFAULT_MASK) {
      uint32_t symbolPeriod = (uint32_t)(((1000.0f * (float)(1UL << this->spreadingFactor)) /
                                          this->bandwidthKhz) + 0.999f);
      uint32_t transitionTime = this->tcxoDelay + 1000;
      if (sleepPeriod <= transitionTime) {
        return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      }
      uint32_t programmedSleepPeriod = sleepPeriod - transitionTime;

      // PreambleDetected restarts the timeout at 2*rx + sleep. LR1110 testing
      // established 78 ms RX / 26.851 ms sleep as the production minimum at
      // SF8, BW 62.5 kHz. Preamble + 11 symbols plus 1 ms preserves that margin.
      uint64_t requiredExtendedPeriod =
          ((uint64_t)this->preambleLengthLoRa + 11ULL) * symbolPeriod + 1000ULL;
      uint64_t extendedPeriod = 2ULL * rxPeriod + programmedSleepPeriod;
      if (extendedPeriod < requiredExtendedPeriod) {
        rxPeriod = (uint32_t)((requiredExtendedPeriod - programmedSleepPeriod + 1ULL) / 2ULL);
      }

      uint32_t rxPeriodRaw = (uint32_t)(((uint64_t)rxPeriod * 32768UL) / 1000000UL);
      uint32_t sleepPeriodRaw =
          (uint32_t)(((uint64_t)programmedSleepPeriod * 32768UL) / 1000000UL);

      if ((rxPeriodRaw & 0xFF000000) || (rxPeriodRaw == 0)) {
        return RADIOLIB_ERR_INVALID_RX_PERIOD;
      }

      if ((sleepPeriodRaw & 0xFF000000) || (sleepPeriodRaw == 0)) {
        return RADIOLIB_ERR_INVALID_SLEEP_PERIOD;
      }

      // Semtech requires Standby RC and an explicitly configured RTC source
      // before SetRxDutyCycle. RadioLib does neither in its LoRa RXPS path.
      int16_t state = standby(RADIOLIB_LR11X0_STANDBY_RC);
      RADIOLIB_ASSERT(state);
      state = configLfClock(
          RADIOLIB_LR11X0_LF_CLK_RC | RADIOLIB_LR11X0_LF_BUSY_RELEASE_ENABLED);
      RADIOLIB_ASSERT(state);

      RadioModeConfig_t cfg = {
        .receive = {
          .timeout = RADIOLIB_LR11X0_RX_TIMEOUT_INF,
          .irqFlags = irqFlags,
          .irqMask = irqMask,
          .len = 0,
        }
      };
      state = this->stageMode(RADIOLIB_RADIO_MODE_RX, &cfg);
      RADIOLIB_ASSERT(state);

      // Send the already converted values. RadioLib 7.7.1 converts them again
      // with 32-bit arithmetic, which overflows for periods above about 131 ms.
      return this->setRxDutyCycle(rxPeriodRaw, sleepPeriodRaw, RADIOLIB_LR11X0_RX_DUTY_CYCLE_MODE_RX);
    }

    int16_t setRxBoostedGainMode(bool en) {
      _rx_boosted = en;
      return LR1110::setRxBoostedGainMode(en);
    }

    bool getRxBoostedGainMode() const { return _rx_boosted; }

    int16_t startReceive() override {
      // Keep preamble detection visible to CAD and route header errors to DIO1
      // so the wrapper can reset the RX path before another packet is read.
      return LR1110::startReceive(
          RADIOLIB_LR11X0_RX_TIMEOUT_INF,
          RADIOLIB_IRQ_RX_DEFAULT_FLAGS | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED),
          RADIOLIB_IRQ_RX_DEFAULT_MASK | (1UL << RADIOLIB_IRQ_HEADER_ERR), 0);
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
        // Do not clear this here. The receive wrapper owns header-error
        // recovery and must see the IRQ in order to enter standby. Clearing it
        // from this polling path recreates the four-byte RX-buffer shift bug.
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
