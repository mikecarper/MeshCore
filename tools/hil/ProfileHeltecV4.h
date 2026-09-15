#pragma once
#include "LoRaFEMControl.h"

// HIL-only RF front-end adapter. The chip's requested TX power is not a
// calibrated antenna-port power, especially on a KCT8103L amplified path.
class ProfileHeltecV4 {
 public:
  LoRaFEMControl fem;
  void begin() { fem.init(); fem.setRxModeEnable(); }
  void beforeTransmit() {
    if (fem.getFEMType() == GC1109_PA) {
      // CSD=1, CPS=0; SX1262 DIO2 supplies CTX=1 during TX: PA bypass.
      // GC1109 datasheet Rev0.9.2, table 4. Keep the low-power experiment
      // from silently enabling the V4's external TX amplifier.
      fem.setRxModeEnable();
    } else {
      fem.setTxModeEnable();
    }
  }
  void afterTransmit() { fem.setRxModeEnable(); }
  const char* type() const { return fem.getFEMType() == GC1109_PA ? "GC1109" : "KCT8103L"; }
  const char* txMode() const { return fem.getFEMType() == GC1109_PA ? "bypass" : "amplified"; }
};
