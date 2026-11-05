#pragma once

#include <stdint.h>

// ESP-IDF 4.x expresses the station's maximum transmit power in 0.25 dBm
// steps, valid range [8, 84] (2 dBm to 20 dBm). A build profile may cap that
// power without changing the SDK default for every board: a board that does not
// define WIFI_TX_POWER_DBM keeps the limit from its PHY init data.
//
// Some ESP32-S3 boards reach a practical WiFi ceiling well below the SDK
// maximum, and pushing the PA harder lengthens association and produces 802.11
// missing-ack and timeout drops. The cap is therefore a per-profile setting,
// never a global default.
//
// Pure lookup so a host test can hold the profile flag, the driver call and the
// reported value to one table. The wire value is always the SDK's 0.25 dBm
// unit, never a dBm number.
#if defined(WIFI_TX_POWER_DBM)
static_assert(WIFI_TX_POWER_DBM >= 2 && WIFI_TX_POWER_DBM <= 20,
              "WIFI_TX_POWER_DBM must be 2..20 dBm");
#endif

namespace mesh {
namespace wifi {

// One SDK step is 0.25 dBm.
static constexpr int8_t kTxPowerStepUnits = 4;
static constexpr int8_t kTxPowerMinUnits = 8;    // 2 dBm
static constexpr int8_t kTxPowerMaxUnits = 84;   // 20 dBm

inline bool hasConfiguredTxPowerCap() {
#if defined(WIFI_TX_POWER_DBM)
  return true;
#else
  return false;
#endif
}

// The SDK 0.25 dBm value to write, or 0 when the profile sets no cap.
inline int8_t configuredTxPowerUnits() {
#if defined(WIFI_TX_POWER_DBM)
  const int units = WIFI_TX_POWER_DBM * kTxPowerStepUnits;
  return static_cast<int8_t>(units < kTxPowerMinUnits ? kTxPowerMinUnits
                      : (units > kTxPowerMaxUnits ? kTxPowerMaxUnits : units));
#else
  return 0;
#endif
}

// The driver rounds a requested step down to the nearest table entry, so a
// reported value can sit below the requested one. Compare in whole dBm.
inline int txPowerDbmFromUnits(int8_t units) {
  return units / kTxPowerStepUnits;
}

}  // namespace wifi
}  // namespace mesh
