#pragma once
// HIL-only experiments. Never included by a production target. These rely on
// an explicitly identified RX-to-RX hop, not a general cached radio state.
#include <helpers/radiolib/CustomSX1262.h>

class ExperimentalSX1262 : public CustomSX1262 {
 public:
  enum : uint32_t {
    NoRxStandby = 1, NoIrqRewrite = 2, NoBufferRewrite = 4,
    NoPacketRewrite = 8, NoModemQuery = 16, DeferPreamble = 32,
    NoWakeNop = 64, ManualRxControl = 128,
  };
  uint32_t experiment = 0;
  bool productionPath = true;
  bool inHop = false, resumeRx = false, haveStandby = false;
  bool packetWritten = false, preamblePending = false, rxPrimed = false;
  using CustomSX1262::CustomSX1262;

  void beginHop(bool rx) {
    inHop = true; resumeRx = rx; haveStandby = packetWritten = preamblePending = false;
  }
  void endHop() { inHop = resumeRx = haveStandby = false; }

  int16_t standby() override {
    if (productionPath) return CustomSX1262::standby();
    const bool knownAwake = inHop && resumeRx && (experiment & NoWakeNop);
    const int16_t rc = SX1262::standby(standbyXOSC ? RADIOLIB_SX126X_STANDBY_XOSC
        : RADIOLIB_SX126X_STANDBY_RC, !knownAwake);
    if (inHop && rc == RADIOLIB_ERR_NONE) haveStandby = true;
    return rc;
  }

  int16_t setPreambleLength(size_t symbols) override {
    if (productionPath) return CustomSX1262::setPreambleLength(symbols);
    if (inHop && resumeRx && haveStandby && (experiment & DeferPreamble)) {
      preambleLengthLoRa = symbols;
      preamblePending = true;
      return RADIOLIB_ERR_NONE;
    }
    const int16_t rc = SX1262::setPreambleLength(symbols);
    if (inHop && rc == RADIOLIB_ERR_NONE) packetWritten = true;
    return rc;
  }

  int16_t startReceive() override {
    if (productionPath) return CustomSX1262::startReceive();
    if (!experiment || !inHop || !resumeRx || !haveStandby || !rxPrimed) {
      const int16_t rc = CustomSX1262::startReceive();
      rxPrimed = rc == RADIOLIB_ERR_NONE;
      return rc;
    }
    // Same sequence and error checks as pinned RadioLib's stageMode(RX),
    // selectively omitting only the particular operation under test.
    int16_t rc;
    if (!(experiment & NoRxStandby)) {
      rc = standby();
      RADIOLIB_ASSERT(rc);
    }
    if (!(experiment & NoIrqRewrite)) {
      rc = setDioIrqParams(getIrqMapped(RADIOLIB_IRQ_RX_DEFAULT_FLAGS
                               | (1UL << RADIOLIB_IRQ_PREAMBLE_DETECTED)),
                           getIrqMapped(RADIOLIB_IRQ_RX_DEFAULT_MASK));
      RADIOLIB_ASSERT(rc);
    }
    if (!(experiment & NoBufferRewrite)) {
      rc = setBufferBaseAddress();
      RADIOLIB_ASSERT(rc);
    }
    rc = clearIrqStatus();  // never omit clearing consumed/stale IRQ flags
    RADIOLIB_ASSERT(rc);
    if (!(experiment & NoModemQuery)
        && getPacketType() != RADIOLIB_SX126X_PACKET_TYPE_LORA) {
      return RADIOLIB_ERR_WRONG_MODEM;
    }
    if (!(experiment & NoPacketRewrite) || !packetWritten || preamblePending) {
      rc = setPacketParams(preambleLengthLoRa, crcTypeLoRa, implicitLen, headerType, invertIQEnabled);
      RADIOLIB_ASSERT(rc);
    }
    rxTimeout = RADIOLIB_SX126X_RX_TIMEOUT_INF;
    getMod()->setRfSwitchState(Module::MODE_RX);
    rc = setRx(rxTimeout);  // retains all RadioLib BUSY polling/error handling
    stagedMode = RADIOLIB_RADIO_MODE_NONE;
    if (rc != RADIOLIB_ERR_NONE) rxPrimed = false;
    return rc;
  }
};
