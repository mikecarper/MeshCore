#pragma once

#include <stdint.h>
#include <string.h>

namespace mesh {

// Local outbound choice. AUTO follows the radio2.cross policy; explicit masks
// choose profiles independently of cross. Mode permissions are enforced
// separately, including the infrastructure reply-only RX override.
enum RadioTxPolicy : uint8_t {
  RADIO_TX_AUTO = 0,
  RADIO_TX_PRIMARY = 1,
  RADIO_TX_SECONDARY = 2,
  RADIO_TX_BOTH = 3,
  RADIO_TX_OFF = 4,
};

inline const char* radioTxPolicyName(uint8_t policy) {
  switch (policy) {
    case RADIO_TX_PRIMARY: return "radio";
    case RADIO_TX_SECONDARY: return "radio2";
    case RADIO_TX_BOTH: return "both";
    case RADIO_TX_OFF: return "off";
    default: return "auto";
  }
}

inline bool parseRadioTxPolicy(const char* text, uint8_t& policy) {
  for (uint8_t value = RADIO_TX_AUTO; value <= RADIO_TX_OFF; ++value) {
    if (!strcmp(text, radioTxPolicyName(value))) { policy = value; return true; }
  }
  return false;
}

// Use a tagged value in previously reserved storage bytes. Zero and historical
// unrecognised bytes retain the old automatic behavior; record sizes stay fixed.
inline uint8_t encodeRadioTxPolicy(uint8_t policy) {
  return policy >= RADIO_TX_PRIMARY && policy <= RADIO_TX_OFF ? 0xa0 | policy : 0;
}
inline uint8_t decodeRadioTxPolicy(uint8_t stored) {
  return stored >= 0xa1 && stored <= 0xa4 ? stored & 7 : RADIO_TX_AUTO;
}

inline uint8_t explicitRadioTxMask(uint8_t policy, bool secondary_tx) {
  return policy >= RADIO_TX_PRIMARY && policy <= RADIO_TX_BOTH
      ? policy & (secondary_tx ? 3 : 1) : 0;
}

}  // namespace mesh
