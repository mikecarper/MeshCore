#pragma once

#include <stdint.h>

// CommonRadioPrefs applies live settings through the target's global radio
// driver. Native serializer tests do not build a hardware variant, so provide
// the narrow interface that helper needs.
class NativeRadioDriverMock {
public:
  bool setRxBoostedGainMode(bool) { return true; }
  bool setTxPower(int8_t) { return true; }
};

inline NativeRadioDriverMock radio_driver;
