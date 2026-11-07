#include "PeripheralOutputs.h"

#if MESH_CUSTOM_PERIPHERALS
#include <Arduino.h>
#include <MeshCore.h>
#include "CheckedSettingsFile.h"
#include "LedPolicy.h"
#include "RuntimePinClaims.h"
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
};
const char* const names[RoleCount] = {
  "buzzer", "led.notify", "led.gps", "led.ble"
};
const char* const path = "/outputs_v1";
const char* const backup = "/outputs_v1.bak";
const size_t record_size = 12;
MainBoard* main_board = nullptr;
FILESYSTEM* settings_fs = nullptr;
Settings settings;
genericBuzzer buzzer(-1);
bool notification_on = false, gps_on = false, bluetooth_on = false;
bool legacy_notification_on = false;
uint32_t legacy_notification_deadline = 0;
bool rejected_saved_pins = false;
bool writes_frozen = false;

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

bool readSetting(const char* name, Settings& value) {
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
  uint8_t data[record_size];
  encode(value, data);
#if defined(NRF52_PLATFORM)
  const auto outcome = storage::writeCheckedSettingsFile(settings_fs, path, data, sizeof(data));
  if (outcome == AtomicFileWriter::Outcome::Indeterminate) writes_frozen = true;
  return outcome == AtomicFileWriter::Outcome::Committed;
#else
#if defined(STM32_PLATFORM)
  AtomicFileWriter file(settings_fs, path);
#else
  ContactFileTransaction file(settings_fs, path);
#endif
  if (!file || file.write(data, sizeof(data)) != sizeof(data) || !file.commit()) {
    if (file.outcome() == decltype(file)::Outcome::Indeterminate) writes_frozen = true;
    return false;
  }
  uint8_t verified[record_size];
  const bool confirmed = storage::readFixedSettingsFile(settings_fs, path, verified, sizeof(verified))
      && memcmp(data, verified, sizeof(data)) == 0;
  if (!confirmed) writes_frozen = true;
  return confirmed;
#endif
}

bool validate(const Settings& value, uint64_t& mask, char* reply, size_t capacity) {
  mask = 0;
  for (unsigned i = 0; i < RoleCount; ++i) {
    const int8_t pin = value.pin[i];
    if (pin < 0) continue;
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
  if (pin >= 0) digitalWrite(uint8_t(pin), on && leds::enabled() ? HIGH : LOW);
}

void refreshLeds() {
  writeLed(NotifyLed, notification_on || legacy_notification_on);
  writeLed(GpsLed, gps_on);
  // A steady LED means the Bluetooth transport is enabled, including
  // advertising. It goes out when Bluetooth is disabled or inhibited.
  writeLed(BluetoothLed, bluetooth_on);
}

void apply(const Settings& next, uint64_t next_mask) {
  const uint64_t previous_mask = pins::held(pins::Owner::Outputs);
  if (settings.pin[Buzzer] != next.pin[Buzzer]) {
    buzzer.setPin(-1);
    genericBuzzer::setOverridePin(next.pin[Buzzer]);
  }
  for (unsigned i = NotifyLed; i < RoleCount; ++i) {
    if (settings.pin[i] >= 0 && settings.pin[i] != next.pin[i]) {
      digitalWrite(uint8_t(settings.pin[i]), LOW);
      pinMode(uint8_t(settings.pin[i]), INPUT);
    }
  }
  const Settings previous = settings;
  settings = next;
  for (unsigned i = NotifyLed; i < RoleCount; ++i) {
    if (settings.pin[i] >= 0 && settings.pin[i] != previous.pin[i]) {
      // Set the inactive latch before enabling the output.
      digitalWrite(uint8_t(settings.pin[i]), LOW);
      pinMode(uint8_t(settings.pin[i]), OUTPUT);
    }
  }
  if (previous.pin[Buzzer] != settings.pin[Buzzer]) buzzer.setPin(settings.pin[Buzzer]);
  refreshLeds();
  pins::release(pins::Owner::Outputs, previous_mask & ~next_mask);
}

bool parsePin(const char* text, int8_t& pin) {
  if (strcmp(text, "off") == 0) { pin = -1; return true; }
  if (*text < '0' || *text > '9') return false;
  unsigned value = 0;
  do {
    value = value * 10U + unsigned(*text++ - '0');
    if (value > 63) return false;
  } while (*text >= '0' && *text <= '9');
  if (*text) return false;
  pin = int8_t(value);
  return true;
}

void formatPin(int8_t pin, char* text, size_t capacity) {
  if (pin < 0) snprintf(text, capacity, "off");
  else snprintf(text, capacity, "%u", unsigned(pin));
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
  Settings saved;
  bool loaded = readSetting(path, saved);
  if (!loaded && readSetting(backup, saved)) {
    loaded = true;
    if ((!fs->exists(path) || fs->remove(path)) && fs->rename(backup, path)) {
      Settings verified;
      if (!readSetting(path, verified)
          || memcmp(saved.pin, verified.pin, sizeof(saved.pin))) settings_fs = nullptr;
    } else settings_fs = nullptr;
  }
  if (!loaded) {
    rejected_saved_pins = fs && (fs->exists(path) || fs->exists(backup));
    return;
  }
  uint64_t mask;
  char error[100];
  if (!validate(saved, mask, error, sizeof(error))
      || !pins::tryClaim(pins::Owner::Outputs, mask)) {
    rejected_saved_pins = true;
    return;
  }
  apply(saved, mask);
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
    return true;
  }
  if (strcmp(command, "get peripherals") == 0) {
    char values[RoleCount][5];
    for (unsigned i = 0; i < RoleCount; ++i) formatPin(settings.pin[i], values[i], sizeof(values[i]));
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
      char pin[5]; formatPin(settings.pin[role], pin, sizeof(pin));
      snprintf(reply, capacity, "> %s", pin);
    }
    return true;
  }
  while (*value == ' ' || *value == '\t') ++value;
  Settings next = settings;
  if (!parsePin(value, next.pin[role])) {
    snprintf(reply, capacity, "Error: usage set peripheral.%s <0..63|off>", names[role]);
    return true;
  }
  if (writes_frozen) {
    snprintf(reply, capacity, "Error: peripheral pin writes locked; reboot may differ");
    return true;
  }
  uint64_t next_mask;
  if (!validate(next, next_mask, reply, capacity)) return true;
  const uint64_t previous_mask = pins::held(pins::Owner::Outputs);
  if (!pins::tryClaim(pins::Owner::Outputs, next_mask)) {
    snprintf(reply, capacity, "Error: peripheral GPIO is in use; retry");
    return true;
  }
  if (!saveSetting(next)) {
    pins::release(pins::Owner::Outputs, next_mask & ~previous_mask);
    snprintf(reply, capacity, "Error: peripheral pin save unconfirmed; reboot may differ");
    return true;
  }
  apply(next, next_mask);
  rejected_saved_pins = false;
  snprintf(reply, capacity, "OK");
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
  return main_board && (pins::held(pins::Owner::Outputs)
      & pins::pinMask(main_board->peripheralPinIdentity(pin))) != 0;
}

}} // namespace mesh::peripherals
#endif // MESH_CUSTOM_PERIPHERALS
