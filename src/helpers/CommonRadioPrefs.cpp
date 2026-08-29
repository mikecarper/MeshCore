#include "CommonRadioPrefs.h"
#include "TxtDataHelpers.h"
#include "target.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

namespace {

constexpr size_t RADIO_ARGS_CAPACITY = 132;

size_t boundedLength(const char* text, size_t capacity) {
  if (text == nullptr) return capacity;
  size_t length = 0;
  while (length < capacity && text[length] != 0) length++;
  return length;
}

bool startsWith(const char* text, const char* prefix) {
  if (text == nullptr || prefix == nullptr) return false;
  const size_t prefix_len = strlen(prefix);
  return strncmp(text, prefix, prefix_len) == 0;
}

bool parseIntStrict(const char* text, int32_t min_value, int32_t max_value,
                    int32_t& result) {
  if (text == nullptr || *text == 0 || min_value > max_value) return false;

  bool negative = false;
  if (*text == '-') {
    negative = true;
    text++;
  }
  if (*text == 0) return false;

  int64_t magnitude = 0;
  while (*text != 0) {
    if (*text < '0' || *text > '9') return false;
    magnitude = magnitude * 10 + (*text++ - '0');
    if (magnitude > 2147483648LL) return false;
  }

  const int64_t parsed = negative ? -magnitude : magnitude;
  if (parsed < min_value || parsed > max_value) return false;
  result = static_cast<int32_t>(parsed);
  return true;
}

// Accept only a plain fixed-point decimal. Exponents, NaN/Inf, whitespace,
// and trailing characters are rejected so malformed CLI values never become
// zero through atoi()/atof() fallback behavior.
bool parseDecimalStrict(const char* text, float& result) {
  if (text == nullptr || *text == 0) return false;

  const char* cursor = text;
  bool negative = false;
  if (*cursor == '-') {
    negative = true;
    cursor++;
  }

  bool saw_digit = false;
  double value = 0.0;
  while (*cursor >= '0' && *cursor <= '9') {
    value = value * 10.0 + (*cursor++ - '0');
    if (!isfinite(value)) return false;
    saw_digit = true;
  }

  if (*cursor == '.') {
    cursor++;
    double place = 0.1;
    while (*cursor >= '0' && *cursor <= '9') {
      value += (*cursor++ - '0') * place;
      place *= 0.1;
      saw_digit = true;
    }
  }

  if (!saw_digit || *cursor != 0) return false;
  value = negative ? -value : value;
  if (!isfinite(value) || value > 3.402823466e+38
      || value < -3.402823466e+38) {
    return false;
  }

  result = static_cast<float>(value);
  return isfinite(result);
}

bool splitRadioArgs(char* text, const char* parts[4]) {
  if (text == nullptr || *text == 0) return false;

  size_t count = 0;
  char* field = text;
  while (true) {
    if (*field == 0 || count >= 4) return false;
    parts[count++] = field;

    char* comma = strchr(field, ',');
    if (comma == nullptr) break;
    *comma = 0;
    field = comma + 1;
  }
  return count == 4;
}

bool bwMatches(float bw, float allowed) {
  return fabsf(bw - allowed) <= 0.001f;
}

bool isValidLoRaBandwidth(float bw) {
#if defined(USE_LR1110)
  return bwMatches(bw, 62.5f)
      || bwMatches(bw, 125.0f)
      || bwMatches(bw, 250.0f)
      || bwMatches(bw, 500.0f);
#elif defined(USE_LLCC68) || defined(USE_SX1272)
  return bwMatches(bw, 125.0f)
      || bwMatches(bw, 250.0f)
      || bwMatches(bw, 500.0f);
#else
  return bwMatches(bw, 7.8f)
      || bwMatches(bw, 10.4f)
      || bwMatches(bw, 15.6f)
      || bwMatches(bw, 20.8f)
      || bwMatches(bw, 31.25f)
      || bwMatches(bw, 41.7f)
      || bwMatches(bw, 62.5f)
      || bwMatches(bw, 125.0f)
      || bwMatches(bw, 250.0f)
      || bwMatches(bw, 500.0f);
#endif
}

bool copyValue(char* destination, size_t capacity, const char* value) {
  if (destination == nullptr || capacity == 0 || value == nullptr) return false;
  const size_t value_len = strlen(value);
  if (value_len >= capacity) {
    destination[0] = 0;
    return false;
  }
  memcpy(destination, value, value_len + 1);
  return true;
}

}  // namespace

bool CommonRadioPrefs::getByKey(const char* key, char* value, size_t max_len) {
  if (key == nullptr || value == nullptr || max_len == 0) return false;

  if (strcmp(key, "fem_rxgain") == 0) {
    return copyValue(value, max_len, getFEMRxGain() == 0 ? "0" : "1");
  }
  if (strcmp(key, "fem_txgain") == 0) {
    return copyValue(value, max_len, getFEMTxGain() == 0 ? "0" : "1");
  }
  return false;
}

bool CommonRadioPrefs::setByKey(const char* key, const char* value) {
  if (key == nullptr || value == nullptr
      || (strcmp(value, "0") != 0 && strcmp(value, "1") != 0)) {
    return false;
  }

  const uint8_t enabled = strcmp(value, "1") == 0 ? 1 : 0;
  if (strcmp(key, "fem_rxgain") == 0) {
    setFEMRxGain(enabled);
    markDirty();
    return true;
  }
  if (strcmp(key, "fem_txgain") == 0) {
    setFEMTxGain(enabled);
    markDirty();
    return true;
  }
  return false;
}

bool CommonRadioPrefs::handleCommand(const char* command, uint32_t sender_timestamp, char* reply) {
  (void)sender_timestamp;
  if (command == nullptr || reply == nullptr) return false;

  if (strcmp(command, "get radio") == 0) {
    char freq[16], bw[16];
    snprintf(freq, sizeof(freq), "%s", StrHelper::ftoa(getFreq()));
    snprintf(bw, sizeof(bw), "%s", StrHelper::ftoa3(getBandwidth()));
    sprintf(reply, "> %s,%s,%d,%d", freq, bw, (uint32_t)getSpreadFactor(), (uint32_t)getCodingRate());
    return true;
  }
  if (startsWith(command, "set radio ")) {
    const char* args = command + strlen("set radio ");
    const size_t args_len = boundedLength(args, RADIO_ARGS_CAPACITY);
    if (args_len >= RADIO_ARGS_CAPACITY) {
      strcpy(reply, "Error, invalid radio params");
      return true;
    }

    char tmp[RADIO_ARGS_CAPACITY];
    memcpy(tmp, args, args_len + 1);
    const char *parts[4];
    float freq = 0.0f;
    float bw = 0.0f;
    int32_t sf = 0;
    int32_t cr = 0;
    if (splitRadioArgs(tmp, parts)
        && parseDecimalStrict(parts[0], freq)
        && parseDecimalStrict(parts[1], bw)
        && parseIntStrict(parts[2], 5, 12, sf)
        && parseIntStrict(parts[3], 5, 8, cr)
        && freq >= 150.0f && freq <= 2500.0f
        && isValidLoRaBandwidth(bw)) {
      setSpreadFactor(static_cast<uint8_t>(sf));
      setCodingRate(static_cast<uint8_t>(cr));
      setFreq(freq);
      setBandwidth(bw);
      strcpy(reply, "OK - reboot to apply");
    } else {
      strcpy(reply, "Error, invalid radio params");
    }
    return true;
  }

  if (strcmp(command, "get freq") == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(getFreq()));
    return true;
  }

  if (strcmp(command, "get af") == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(getAirtimeFactor()));
    return true;
  }
  if (startsWith(command, "set af ")) {
    float factor = 0.0f;
    if (parseDecimalStrict(command + strlen("set af "), factor)
        && factor >= 0.0f && factor <= 9.0f) {
      setAirtimeFactor(factor);
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, invalid airtime factor");
    }
    return true;
  }

  if (strcmp(command, "get dutycycle") == 0) {
    const float factor = getAirtimeFactor();
    if (!isfinite(factor) || factor < 0.0f) {
      strcpy(reply, "Error: invalid airtime factor");
    } else {
      const int tenths = static_cast<int>(1000.0f / (factor + 1.0f) + 0.5f);
      sprintf(reply, "> %d.%d%%", tenths / 10, tenths % 10);
    }
    return true;
  }
  if (startsWith(command, "set dutycycle ")) {
    float dc = 0.0f;
    if (!parseDecimalStrict(command + strlen("set dutycycle "), dc)
        || dc < 1.0f || dc > 100.0f) {
      strcpy(reply, "ERROR: dutycycle must be 1-100");
    } else {
      setAirtimeFactor((100.0f / dc) - 1.0f);
      const float actual = 100.0f / (getAirtimeFactor() + 1.0f);
      const int tenths = static_cast<int>(actual * 10.0f + 0.5f);
      sprintf(reply, "OK - %d.%d%%", tenths / 10, tenths % 10);
    }
    return true;
  }

  if (strcmp(command, "get int.thresh") == 0) {
    sprintf(reply, "> %d", (uint32_t) getIntThresh());
    return true;
  }
  if (startsWith(command, "set int.thresh ")) {
    int32_t threshold = 0;
    if (parseIntStrict(command + strlen("set int.thresh "), 0, 255, threshold)) {
      setIntThresh(static_cast<uint8_t>(threshold));
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-255");
    }
    return true;
  }

  if (strcmp(command, "get cad") == 0) {
    sprintf(reply, "> %s", isCadEnabled() ? "on" : "off");
    return true;
  }
  if (startsWith(command, "set cad ")) {
    const char* value = command + strlen("set cad ");
    if (strcmp(value, "on") == 0 || strcmp(value, "off") == 0) {
      setCadEnabled(strcmp(value, "on") == 0);
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error: use set cad on|off");
    }
    return true;
  }

  if (strcmp(command, "get radio.rxgain") == 0) {
    sprintf(reply, "> %s", getRxGain() != 0 ? "on" : "off");
    return true;
  }
  if (startsWith(command, "set radio.rxgain ")) {
    const char* value = command + strlen("set radio.rxgain ");
    if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
      strcpy(reply, "Error: use set radio.rxgain on|off");
      return true;
    }

    const bool enabled = strcmp(value, "on") == 0;
    if (radio_driver.setRxBoostedGainMode(enabled)) {
      setRxGain(enabled ? 1 : 0);
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error: unsupported");
    }
    return true;
  }

  if (strcmp(command, "get tx") == 0) {
    sprintf(reply, "> %d", static_cast<int>(getTxPower()));
    return true;
  }
  if (startsWith(command, "set tx ")) {
    int32_t dbm = 0;
    if (!parseIntStrict(command + strlen("set tx "), -128, 127, dbm)) {
      strcpy(reply, "Error: invalid TX power");
    } else if (!radio_driver.setTxPower(static_cast<int8_t>(dbm))) {
      strcpy(reply, "Error: TX power rejected");
    } else {
      setTxPower(static_cast<int8_t>(dbm));
      strcpy(reply, "OK");
    }
    return true;
  }

  if (strcmp(command, "get rxdelay") == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(getRxDelay()));
    return true;
  }
  if (startsWith(command, "set rxdelay ")) {
    float db = 0.0f;
    if (parseDecimalStrict(command + strlen("set rxdelay "), db)
        && db >= 0.0f && db <= 20.0f) {
      setRxDelay(db);
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-20");
    }
    return true;
  }

  if (strcmp(command, "get agc.reset.interval") == 0) {
    sprintf(reply, "> %d", (uint32_t) getAgcResetInt());
    return true;
  }
  if (startsWith(command, "set agc.reset.interval ")) {
    int32_t seconds = 0;
    if (parseIntStrict(command + strlen("set agc.reset.interval "), 0, 255, seconds)) {
      setAgcResetInt(static_cast<uint8_t>(seconds));
      sprintf(reply, "OK - interval rounded to %d", (uint32_t) getAgcResetInt());
    } else {
      strcpy(reply, "Error, must be 0-255");
    }
    return true;
  }

  if (strcmp(command, "get path.hash.mode") == 0) {
    sprintf(reply, "> %d", (uint32_t)getHashMode());
    return true;
  }
  if (startsWith(command, "set path.hash.mode ")) {
    int32_t mode = 0;
    if (parseIntStrict(command + strlen("set path.hash.mode "), 0, 2, mode)) {
      setHashMode(static_cast<uint8_t>(mode));
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0,1, or 2");
    }
    return true;
  }

  if (strcmp(command, "get multi.acks") == 0) {
    sprintf(reply, "> %d", (uint32_t) getMultiAcks());
    return true;
  }
  if (startsWith(command, "set multi.acks ")) {
    int32_t enabled = 0;
    if (parseIntStrict(command + strlen("set multi.acks "), 0, 1, enabled)) {
      setMultiAcks(static_cast<uint8_t>(enabled));
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0 or 1");
    }
    return true;
  }

  if (strcmp(command, "get txdelay") == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(getFloodTxDelay()));
    return true;
  }
  if (startsWith(command, "set txdelay ")) {
    float f = 0.0f;
    if (parseDecimalStrict(command + strlen("set txdelay "), f)
        && f >= 0.0f && f <= 2.0f) {
      setFloodTxDelay(f);
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-2");
    }
    return true;
  }

  if (strcmp(command, "get direct.txdelay") == 0) {
    sprintf(reply, "> %s", StrHelper::ftoa(getDirectTxDelay()));
    return true;
  }
  if (startsWith(command, "set direct.txdelay ")) {
    float f = 0.0f;
    if (parseDecimalStrict(command + strlen("set direct.txdelay "), f)
        && f >= 0.0f && f <= 2.0f) {
      setDirectTxDelay(f);
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error, must be 0-2");
    }
    return true;
  }

  return false; // not handled
}
