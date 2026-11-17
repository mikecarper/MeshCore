#include "PeripheralOutputs.h"

#if MESH_CUSTOM_PERIPHERALS
#include <Arduino.h>
#include <MeshCore.h>
#include "CheckedSettingsFile.h"
#include "LedPolicy.h"
#include "RuntimePinClaims.h"
#include <helpers/I2cExpander.h>
#include "ui/buzzer.h"
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#else
#include <helpers/ContactFileTransaction.h>
#endif
#include <stdio.h>
#include <string.h>

namespace mesh { namespace peripherals {
namespace {
enum Role : uint8_t { Buzzer, NotifyLed, GpsLed, BluetoothLed, RoleCount };
struct Settings {
  int8_t pin[RoleCount] = {-1, -1, -1, -1};
  bool active_low[RoleCount] = {};
  uint16_t expander_identity = 0;
};
const char* const names[RoleCount] = {
  "buzzer", "led.notify", "led.gps", "led.ble"
};
const char* const path = "/outputs_v1";
const char* const backup = "/outputs_v1.bak";
const size_t record_size = 12;
#if defined(ESP32_PLATFORM)
const size_t extended_record_size = 16;
#endif
MainBoard* main_board = nullptr;
FILESYSTEM* settings_fs = nullptr;
Settings settings;
genericBuzzer buzzer(-1);
bool notification_on = false, gps_on = false, bluetooth_on = false;
bool legacy_notification_on = false;
uint32_t legacy_notification_deadline = 0;
bool rejected_saved_pins = false;
bool writes_frozen = false;
uint16_t active_expander_claims = 0;
bool led_level_known[RoleCount] = {}, led_level_high[RoleCount] = {};

bool hasExpanderPins(const Settings& value) {
  for (unsigned i = 0; i < RoleCount; ++i)
    if (expander::isPin(value.pin[i])) return true;
  return false;
}

#if defined(ESP32_PLATFORM)
bool extended(const Settings& value) {
  for (unsigned i = 0; i < RoleCount; ++i)
    if (expander::isPin(value.pin[i]) || value.active_low[i]) return true;
  return false;
}
#endif

uint32_t checksum(const uint8_t* bytes, size_t size) {
  uint32_t crc = UINT32_C(0xffffffff);
  while (size--) {
    crc ^= *bytes++;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & (0U - (crc & 1U)));
  }
  return ~crc;
}

void encode(const Settings& value, uint8_t (&data)[record_size]) {
  data[0] = 'O'; data[1] = 'U'; data[2] = 'T'; data[3] = 1;
  for (unsigned i = 0; i < RoleCount; ++i) data[4 + i] = uint8_t(value.pin[i]);
  const uint32_t crc = checksum(data, 8);
  for (unsigned i = 0; i < 4; ++i) data[8 + i] = uint8_t(crc >> (i * 8));
}

#if defined(ESP32_PLATFORM)
void encodeExtended(const Settings& value, uint8_t (&data)[extended_record_size]) {
  memset(data, 0, sizeof(data));
  data[0] = 'O'; data[1] = 'U'; data[2] = 'T'; data[3] = 2;
  for (unsigned i = 0; i < RoleCount; ++i) {
    data[4 + i] = uint8_t(value.pin[i]);
    if (value.active_low[i]) data[8] |= uint8_t(1U << i);
  }
  data[9] = uint8_t(value.expander_identity);
  data[10] = uint8_t(value.expander_identity >> 8);
  const uint32_t crc = checksum(data, 12);
  for (unsigned i = 0; i < 4; ++i) data[12 + i] = uint8_t(crc >> (i * 8));
}
#endif

bool readSetting(const char* name, Settings& value) {
#if defined(ESP32_PLATFORM)
  uint8_t extended_data[extended_record_size];
  if (storage::readFixedSettingsFile(settings_fs, name, extended_data, sizeof(extended_data))) {
    if (extended_data[0] != 'O' || extended_data[1] != 'U' || extended_data[2] != 'T'
        || extended_data[3] != 2 || (extended_data[8] & ~0x0eU)
        || extended_data[11]) return false;
    const uint32_t crc = checksum(extended_data, 12);
    for (unsigned i = 0; i < 4; ++i)
      if (extended_data[12 + i] != uint8_t(crc >> (i * 8))) return false;
    Settings next;
    for (unsigned i = 0; i < RoleCount; ++i) {
      const uint8_t pin = extended_data[4 + i];
      if (pin != 0xff && pin > 63 && !expander::isPin(pin)) return false;
      if (i == Buzzer && expander::isPin(pin)) return false;
      next.pin[i] = pin == 0xff ? -1 : int8_t(pin);
      next.active_low[i] = (extended_data[8] & (1U << i)) != 0;
      if (next.pin[i] < 0 && next.active_low[i]) return false;
    }
    next.expander_identity = uint16_t(extended_data[9]) | uint16_t(extended_data[10]) << 8;
    if (hasExpanderPins(next) != (next.expander_identity != 0)) return false;
    value = next;
    return true;
  }
#endif
  uint8_t data[record_size];
  if (!storage::readFixedSettingsFile(settings_fs, name, data, sizeof(data))
      || data[0] != 'O' || data[1] != 'U' || data[2] != 'T' || data[3] != 1)
    return false;
  const uint32_t crc = checksum(data, 8);
  for (unsigned i = 0; i < 4; ++i)
    if (data[8 + i] != uint8_t(crc >> (i * 8))) return false;
  Settings next;
  for (unsigned i = 0; i < RoleCount; ++i) {
    if (data[4 + i] != 0xff && data[4 + i] > 63) return false;
    next.pin[i] = data[4 + i] == 0xff ? -1 : int8_t(data[4 + i]);
  }
  value = next;
  return true;
}

bool saveSetting(const Settings& value) {
  if (!settings_fs || writes_frozen) return false;
#if defined(ESP32_PLATFORM)
  uint8_t data[extended_record_size];
  const size_t size = extended(value) ? extended_record_size : record_size;
  if (size == extended_record_size) encodeExtended(value, data);
  else {
    uint8_t legacy[record_size]; encode(value, legacy); memcpy(data, legacy, sizeof(legacy));
  }
#else
  // Native-only targets retain the original active-high v1 record and code.
  uint8_t data[record_size];
  const size_t size = sizeof(data);
  encode(value, data);
#endif
#if defined(NRF52_PLATFORM)
  const auto outcome = storage::writeCheckedSettingsFile(settings_fs, path, data, size);
  if (outcome == AtomicFileWriter::Outcome::Indeterminate) writes_frozen = true;
  return outcome == AtomicFileWriter::Outcome::Committed;
#else
#if defined(STM32_PLATFORM)
  AtomicFileWriter file(settings_fs, path);
#else
  ContactFileTransaction file(settings_fs, path);
#endif
  if (!file || file.write(data, size) != size || !file.commit()) {
    if (file.outcome() == decltype(file)::Outcome::Indeterminate) writes_frozen = true;
    return false;
  }
  uint8_t verified[sizeof(data)];
  const bool confirmed = storage::readFixedSettingsFile(settings_fs, path, verified, size)
      && memcmp(data, verified, size) == 0;
  if (!confirmed) writes_frozen = true;
  return confirmed;
#endif
}

bool validate(const Settings& value, uint64_t& mask, uint16_t& expander_mask, char* reply, size_t capacity) {
  mask = 0;
  expander_mask = 0;
  if (hasExpanderPins(value) && (!value.expander_identity
      || value.expander_identity != expander::configIdentity())) {
    snprintf(reply, capacity, "Error: saved LED expander type/address changed; assign pins again"); return false;
  }
  for (unsigned i = 0; i < RoleCount; ++i) {
    const int8_t pin = value.pin[i];
    if (pin < 0) continue;
    if (expander::isPin(pin)) {
      if (i == Buzzer) {
        snprintf(reply, capacity, "Error: passive buzzer requires a native GPIO pin"); return false;
      }
      // An offline expander must still permit releasing its saved assignments.
      // Unchanged roles keep their ownership; no new offline pin is admitted.
      const bool retained = settings.pin[i] == pin
          && settings.active_low[i] == value.active_low[i]
          && (active_expander_claims & expander::pinMask(pin));
      if (!expander::available(pin) && !retained) {
        snprintf(reply, capacity, "Error: expander pin is unavailable; check get expander"); return false;
      }
      const uint16_t bit = expander::pinMask(pin);
      if (!bit || (expander_mask & bit)) {
        snprintf(reply, capacity, "Error: peripheral outputs must use distinct expander pins"); return false;
      }
      if (expander::claimedByOther(pin, pins::Owner::Outputs)) {
        snprintf(reply, capacity, "Error: expander pin is already in use"); return false;
      }
      expander_mask |= bit;
      continue;
    }
    if (!main_board || !main_board->isPeripheralOutputAvailable(uint8_t(pin))) {
      snprintf(reply, capacity, "Error: GPIO %u is reserved or cannot be used as an output", unsigned(pin));
      return false;
    }
    const uint16_t physical = main_board->peripheralPinIdentity(uint8_t(pin));
    const uint64_t bit = pins::pinMask(physical);
    if (!bit || (mask & bit)) {
      snprintf(reply, capacity, "Error: peripheral outputs must use distinct physical GPIO pins");
      return false;
    }
    if (pins::claimedByOther(physical, pins::Owner::Outputs)) {
      snprintf(reply, capacity, "Error: GPIO %u is already in use", unsigned(pin));
      return false;
    }
    mask |= bit;
  }
  return true;
}

void writeLed(Role role, bool on) {
  const int8_t pin = settings.pin[role];
  if (pin < 0) return;
  const bool high = (on && leds::enabled()) != settings.active_low[role];
  if (expander::isPin(pin)) {
    if (!expander::available(pin)) { led_level_known[role] = false; return; }
    if (led_level_known[role] && led_level_high[role] == high) return;
    if (expander::write(pin, high)) {
      led_level_known[role] = true; led_level_high[role] = high;
    } else led_level_known[role] = false;
  } else digitalWrite(uint8_t(pin), high ? HIGH : LOW);
}

void refreshLeds() {
  writeLed(NotifyLed, notification_on || legacy_notification_on);
  writeLed(GpsLed, gps_on);
  // A steady LED means the Bluetooth transport is enabled, including
  // advertising. It goes out when Bluetooth is disabled or inhibited.
  writeLed(BluetoothLed, bluetooth_on);
}

bool apply(const Settings& next, uint64_t next_mask, uint16_t next_expander_mask = 0) {
  const uint64_t previous_mask = pins::held(pins::Owner::Outputs);
  const uint16_t previous_expander_mask = active_expander_claims;
  const bool expander_was_online = expander::online();
  bool ready = true;
  if (settings.pin[Buzzer] != next.pin[Buzzer]) {
    buzzer.setPin(-1);
    genericBuzzer::setOverridePin(next.pin[Buzzer]);
  }
  for (unsigned i = NotifyLed; i < RoleCount; ++i) {
    if (settings.pin[i] >= 0 && (settings.pin[i] != next.pin[i]
        || settings.active_low[i] != next.active_low[i])) {
      if (expander::isPin(settings.pin[i])) {
        // Inactive first, then release. PCF devices are inactive when HIGH.
        expander::write(settings.pin[i], settings.active_low[i]);
        expander::configurePin(settings.pin[i], false, !expander::supportsPullup(false));
      } else {
        digitalWrite(uint8_t(settings.pin[i]), settings.active_low[i] ? HIGH : LOW);
        pinMode(uint8_t(settings.pin[i]), INPUT);
      }
    }
  }
  const Settings previous = settings;
  settings = next;
  for (unsigned i = NotifyLed; i < RoleCount; ++i) {
    led_level_known[i] = false;
    if (settings.pin[i] >= 0 && (settings.pin[i] != previous.pin[i]
        || settings.active_low[i] != previous.active_low[i])) {
      // Set the inactive latch before enabling the output.
      if (expander::isPin(settings.pin[i])) {
        if (!expander::write(settings.pin[i], settings.active_low[i])
            || !expander::configurePin(settings.pin[i], true, false)) ready = false;
      } else {
        digitalWrite(uint8_t(settings.pin[i]), settings.active_low[i] ? HIGH : LOW);
        pinMode(uint8_t(settings.pin[i]), OUTPUT);
      }
    }
  }
  if (previous.pin[Buzzer] != settings.pin[Buzzer]) buzzer.setPin(settings.pin[Buzzer]);
  refreshLeds();
  if (next_expander_mask && expander_was_online && !expander::online()) ready = false;
  pins::release(pins::Owner::Outputs, previous_mask & ~next_mask);
  expander::release(pins::Owner::Outputs, previous_expander_mask & ~next_expander_mask);
  active_expander_claims = next_expander_mask;
  return ready;
}

bool parsePin(const char* text, int8_t& pin, bool& active_low, bool buzzer_pin) {
  active_low = false;
  if (strcmp(text, "off") == 0) { pin = -1; return true; }
  const bool external = *text == 'x';
  if (external) ++text;
  if (*text < '0' || *text > '9') return false;
  unsigned value = 0;
  do {
    value = value * 10U + unsigned(*text++ - '0');
    if (value > (external ? 15U : 63U)) return false;
  } while (*text >= '0' && *text <= '9');
  const int parsed = int(value) + (external ? 64 : 0);
  if (external && !expander::isPin(parsed)) return false;
#if defined(ESP32_PLATFORM)
  if (external) active_low = expander::recommendedActiveLow();
  if (*text) {
    if (buzzer_pin) return false;
    if (strcmp(text, ",low") == 0) active_low = true;
    else if (strcmp(text, ",high") == 0) active_low = false;
    else return false;
  }
#else
  (void)buzzer_pin;
  if (*text) return false;
#endif
  pin = int8_t(parsed);
  return true;
}

void formatPin(int8_t pin, char* text, size_t capacity) {
  if (pin < 0) snprintf(text, capacity, "off");
  else if (expander::isPin(pin)) snprintf(text, capacity, "x%u", unsigned(pin - 64));
  else snprintf(text, capacity, "%u", unsigned(pin));
}

void formatRole(unsigned role, char* text, size_t capacity) {
#if defined(ESP32_PLATFORM)
  char pin[5]; formatPin(settings.pin[role], pin, sizeof(pin));
  snprintf(text, capacity, "%s%s", pin, settings.pin[role] >= 0 && role != Buzzer
      && (expander::isPin(settings.pin[role]) || settings.active_low[role])
      ? settings.active_low[role] ? ",low" : ",high" : "");
#else
  formatPin(settings.pin[role], text, capacity);
#endif
}
} // namespace

void begin(MainBoard& board, FILESYSTEM* fs) {
  // Explicit reloads release the previous configuration before consulting a
  // new board/filesystem. No invalid persisted pin is ever initialized.
  if (main_board) apply(Settings(), 0);
  main_board = &board;
  settings_fs = fs;
  settings = Settings();
  notification_on = gps_on = bluetooth_on = false;
  legacy_notification_on = false;
  rejected_saved_pins = false;
  writes_frozen = false;
  active_expander_claims = 0;
  Settings saved;
  bool loaded = readSetting(path, saved);
  if (!loaded && readSetting(backup, saved)) {
    loaded = true;
    if ((!fs->exists(path) || fs->remove(path)) && fs->rename(backup, path)) {
      Settings verified;
      if (!readSetting(path, verified)
          || memcmp(saved.pin, verified.pin, sizeof(saved.pin))
          || memcmp(saved.active_low, verified.active_low, sizeof(saved.active_low))
          || saved.expander_identity != verified.expander_identity) settings_fs = nullptr;
    } else settings_fs = nullptr;
  }
  if (!loaded) {
    rejected_saved_pins = fs && (fs->exists(path) || fs->exists(backup));
    return;
  }
  uint64_t mask;
  uint16_t expander_mask;
  char error[100];
  if (!validate(saved, mask, expander_mask, error, sizeof(error))
      || !pins::tryClaim(pins::Owner::Outputs, mask)) {
    rejected_saved_pins = true;
    return;
  }
  if (!expander::tryClaim(pins::Owner::Outputs, expander_mask)) {
    pins::release(pins::Owner::Outputs, mask); rejected_saved_pins = true; return;
  }
  if (!apply(saved, mask, expander_mask)) rejected_saved_pins = true;
}

void shutdown() {
  apply(Settings(), 0);
  notification_on = gps_on = bluetooth_on = legacy_notification_on = false;
  legacy_notification_deadline = 0;
  rejected_saved_pins = false;
  main_board = nullptr;
  settings_fs = nullptr;
}

bool handleCommand(const char* command, char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return false;
  if (strcmp(command, "get peripheral.pins.available") == 0) {
    size_t used = snprintf(reply, capacity, "> GPIOs:");
    for (unsigned pin = 0; main_board && pin < 64 && used < capacity; ++pin) {
      const uint16_t physical = main_board->peripheralPinIdentity(uint8_t(pin));
      if (!main_board->isPeripheralOutputAvailable(uint8_t(pin)) || !pins::pinMask(physical)
          || pins::claimedByOther(physical, pins::Owner::Outputs)) continue;
      char token[5];
      const size_t length = snprintf(token, sizeof(token), " %u", pin);
      if (used + length + 5 > capacity) {
        if (used + 5 <= capacity) memcpy(reply + used, " ...", 5);
        break;
      }
      memcpy(reply + used, token, length + 1);
      used += length;
    }
    for (unsigned channel = 0; main_board && channel < expander::pinCount() && used < capacity; ++channel) {
      const int pin = int(channel) + 64;
      if (!expander::available(pin) || expander::claimedByOther(pin, pins::Owner::Outputs)) continue;
      char token[6];
      const size_t length = snprintf(token, sizeof(token), " x%u", channel);
      if (used + length + 5 > capacity) {
        if (used + 5 <= capacity) memcpy(reply + used, " ...", 5);
        break;
      }
      memcpy(reply + used, token, length + 1); used += length;
    }
    return true;
  }
  if (strcmp(command, "get peripherals") == 0) {
    char values[RoleCount][12];
    for (unsigned i = 0; i < RoleCount; ++i) formatRole(i, values[i], sizeof(values[i]));
    snprintf(reply, capacity, "> buzzer=%s led.notify=%s led.gps=%s led.ble=%s%s",
        values[Buzzer], values[NotifyLed], values[GpsLed], values[BluetoothLed],
        rejected_saved_pins ? "; saved pins rejected" : "");
    return true;
  }
  const bool get = strncmp(command, "get peripheral.", 15) == 0;
  if (!get && strncmp(command, "set peripheral.", 15) != 0) return false;
  const char* name = command + 15;
  unsigned role;
  const char* value = nullptr;
  for (role = 0; role < RoleCount; ++role) {
    const size_t length = strlen(names[role]);
    if (strncmp(name, names[role], length) == 0
        && (!name[length] || name[length] == ' ' || name[length] == '\t')) {
      value = name + length;
      break;
    }
  }
  if (!value) {
    snprintf(reply, capacity, "Error: unknown peripheral role");
    return true;
  }
  if (get) {
    if (*value) snprintf(reply, capacity, "Error: usage get peripheral.%s", names[role]);
    else {
      char pin[12]; formatRole(role, pin, sizeof(pin));
      snprintf(reply, capacity, "> %s", pin);
    }
    return true;
  }
  while (*value == ' ' || *value == '\t') ++value;
  Settings next = settings;
  if (!parsePin(value, next.pin[role], next.active_low[role], role == Buzzer)) {
    snprintf(reply, capacity, "Error: usage set peripheral.%s %s", names[role],
#if defined(ESP32_PLATFORM)
        role == Buzzer ? "<0..63|off> (native GPIO)" : "<0..63|x0..x15|off>[,high|,low]");
#else
        "<0..63|off>");
#endif
    return true;
  }
  next.expander_identity = hasExpanderPins(next) ? expander::configIdentity() : 0;
  if (writes_frozen) {
    snprintf(reply, capacity, "Error: peripheral pin writes locked; reboot may differ");
    return true;
  }
  uint64_t next_mask;
  uint16_t next_expander_mask;
  if (!validate(next, next_mask, next_expander_mask, reply, capacity)) return true;
  const uint64_t previous_mask = pins::held(pins::Owner::Outputs);
  if (!pins::tryClaim(pins::Owner::Outputs, next_mask)) {
    snprintf(reply, capacity, "Error: peripheral GPIO is in use; retry");
    return true;
  }
  const uint16_t previous_expander_mask = active_expander_claims;
  // Existing assignments retain their claims even after the bus goes offline.
  // Claim only additions so roles can still be disabled one at a time offline.
  if (!expander::tryClaim(pins::Owner::Outputs, next_expander_mask & ~previous_expander_mask)) {
    pins::release(pins::Owner::Outputs, next_mask & ~previous_mask);
    snprintf(reply, capacity, "Error: expander pin is in use; retry"); return true;
  }
  if (!saveSetting(next)) {
    pins::release(pins::Owner::Outputs, next_mask & ~previous_mask);
    expander::release(pins::Owner::Outputs, next_expander_mask & ~previous_expander_mask);
    snprintf(reply, capacity, "Error: peripheral pin save unconfirmed; reboot may differ");
    return true;
  }
  const bool ready = apply(next, next_mask, next_expander_mask);
  rejected_saved_pins = false;
  snprintf(reply, capacity, "%s", ready ? "OK" : "Error: settings saved; expander I/O failed, check get expander");
  return true;
}

void loop(bool gps_active, bool bluetooth_enabled, bool bluetooth_connected) {
  (void)bluetooth_connected;
  gps_on = gps_active;
  bluetooth_on = bluetooth_enabled;
  if (legacy_notification_on && int32_t(millis() - legacy_notification_deadline) >= 0)
    legacy_notification_on = false;
  refreshLeds();
  if (configuredBuzzer()) buzzer.loop();
}
void notificationLed(bool on) {
  notification_on = on;
  writeLed(NotifyLed, on || legacy_notification_on);
}
void playNotification(const char* text) {
  if (!configuredBuzzer()) return;
  if (text) buzzer.playNotification(text); else buzzer.stopNotification();
}
void stopNotification() { if (configuredBuzzer()) buzzer.stopNotification(); }
void notifyMessage(bool channel, bool sound, bool led) {
  if (sound && configuredBuzzer()) {
    playNotification(channel ? "kerplop:d=16,o=6,b=120:32g#,32c#"
        : "MsgRcv3:d=4,o=6,b=200:32e,32g,32b,16c7");
  }
  if (led && configuredNotificationLed()) {
    legacy_notification_on = true;
    legacy_notification_deadline = millis() + 1500U;
    writeLed(NotifyLed, true);
  }
}
void setBuzzerQuiet(bool quiet) { if (buzzer.isQuiet() != quiet) buzzer.quiet(quiet); }
bool configuredBuzzer() { return settings.pin[Buzzer] >= 0; }
int8_t configuredBuzzerPin() { return settings.pin[Buzzer]; }
bool configuredNotificationLed() { return settings.pin[NotifyLed] >= 0; }
bool needsPolling() { return legacy_notification_on || (configuredBuzzer() && buzzer.isPlaying()); }
bool claims(uint8_t pin) {
  if (expander::isPin(pin)) return (active_expander_claims & expander::pinMask(pin)) != 0;
  return main_board && (pins::held(pins::Owner::Outputs)
      & pins::pinMask(main_board->peripheralPinIdentity(pin))) != 0;
}

}} // namespace mesh::peripherals
#endif // MESH_CUSTOM_PERIPHERALS
