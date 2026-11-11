#include "RotarySettings.h"

#if MESH_CUSTOM_PERIPHERALS
#include <MeshCore.h>
#include <target.h>
#include <helpers/CheckedSettingsFile.h>
#include <helpers/FilePresence.h>
#include <helpers/RuntimePinClaims.h>
#include <helpers/ui/GpioRotaryInput.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#else
#include <helpers/ContactFileTransaction.h>
#endif
#include <stdio.h>
#include <string.h>

namespace mesh { namespace ui {
namespace {
constexpr char Path[] = "/rotary_v1";
constexpr char Backup[] = "/rotary_v1.bak";
constexpr size_t ImageSize = 16;
struct Config {
  bool enabled = false;
  int16_t clk = -1, dt = -1, button = -1;
  uint16_t steps = 4;
  bool reverse = false, pullup = true;
};
MainBoard* main_board = nullptr;
FILESYSTEM* settings_fs = nullptr;
bool ui_available = false, writable = false;
Config selected;
GpioRotaryInput input(-1, -1);
uint64_t active_claims = 0;

uint16_t checksum(const uint8_t* data, size_t size) {
  uint16_t crc = 0xffff;
  while (size--) {
    crc ^= uint16_t(*data++) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
  }
  return crc;
}

bool validShape(const Config& config) {
  if (config.steps < 1 || config.steps > 255 ||
      config.clk < -1 || config.clk > 63 || config.dt < -1 || config.dt > 63 ||
      config.button < -1 || config.button > 63) return false;
  if (config.clk < 0 || config.dt < 0)
    return !config.enabled && config.clk == -1 && config.dt == -1 && config.button == -1;
  return config.clk != config.dt && config.clk != config.button && config.dt != config.button;
}

void encode(const Config& config, uint8_t (&data)[ImageSize]) {
  memset(data, 0, sizeof(data));
  data[0] = 'R'; data[1] = 'Y'; data[2] = 1;
  data[3] = config.enabled;
  data[4] = config.clk < 0 ? 255 : config.clk;
  data[5] = config.dt < 0 ? 255 : config.dt;
  data[6] = config.button < 0 ? 255 : config.button;
  data[7] = config.steps;
  data[8] = config.reverse; data[9] = config.pullup;
  const uint16_t crc = checksum(data, 14);
  data[14] = crc; data[15] = crc >> 8;
}

bool readConfig(const char* path, Config& config) {
  uint8_t data[ImageSize], expected[ImageSize];
  if (!storage::readFixedSettingsFile(settings_fs, path, data, sizeof(data))) return false;
  Config decoded;
  decoded.enabled = data[3] != 0;
  decoded.clk = data[4] == 255 ? -1 : data[4];
  decoded.dt = data[5] == 255 ? -1 : data[5];
  decoded.button = data[6] == 255 ? -1 : data[6];
  decoded.steps = data[7]; decoded.reverse = data[8] != 0; decoded.pullup = data[9] != 0;
  encode(decoded, expected);
  if (!validShape(decoded) || memcmp(data, expected, sizeof(data)) != 0) return false;
  config = decoded;
  return true;
}

// Input-only signals use the same physical-pad registry as custom outputs and
// CLI GPIO. Alias pin numbers must never allocate one pad to two encoder wires.
bool validatePins(const Config& config, uint64_t& claims) {
  claims = 0;
  if (!main_board || !validShape(config)) return false;
  if (config.clk < 0) return !config.enabled;
  const int16_t wires[] = {config.clk, config.dt, config.button};
  for (unsigned i = 0; i < 3; ++i) {
    if (wires[i] < 0) continue;
    const uint8_t pin = uint8_t(wires[i]);
    const bool available = i == 2
        ? main_board->isPeripheralButtonAvailable(pin, config.pullup)
        : main_board->isPeripheralPinAvailable(pin, config.pullup);
    const uint16_t physical = main_board->peripheralPinIdentity(pin);
    const uint64_t mask = pins::pinMask(physical);
    if (!available || !mask || (claims & mask) ||
        pins::claimedByOther(physical, pins::Owner::Rotary)) return false;
    claims |= mask;
  }
  return true;
}

bool saveConfig(const Config& config) {
  if (!settings_fs || !writable) return false;
  uint8_t data[ImageSize], verified[ImageSize];
  encode(config, data);
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter file(settings_fs, Path);
#else
  ContactFileTransaction file(settings_fs, Path, filePresence<FILESYSTEM>);
#endif
  using Outcome = decltype(file.outcome());
  if (!file || file.write(data, sizeof(data)) != sizeof(data)) {
    if (file.outcome() == Outcome::Indeterminate) writable = false;
    return false;
  }
  file.commit();
  if (file.outcome() == Outcome::NotCommitted) return false;
  if (file.outcome() != Outcome::Committed ||
      !storage::readFixedSettingsFile(settings_fs, Path, verified, sizeof(verified)) ||
      memcmp(data, verified, sizeof(data)) != 0) {
    writable = false;
    return false;
  }
  return true;
}

bool apply(const Config& config, uint64_t claims) {
  input.end();
  bool ready = !config.enabled;
  if (config.enabled) {
    ready = input.configure(config.clk, config.dt, config.button, config.steps,
                            config.reverse, config.pullup) && input.begin();
  }
  pins::release(pins::Owner::Rotary, active_claims & ~claims);
  active_claims = claims;
  selected = config;
  return ready;
}

Config buildDefaults() {
  Config defaults;
#if defined(UI_ROTARY_INPUT_GPIO) && UI_ROTARY_INPUT_GPIO
#if !defined(PIN_ROTARY_CLK) || !defined(PIN_ROTARY_DT)
#error "UI_ROTARY_INPUT_GPIO requires PIN_ROTARY_CLK and PIN_ROTARY_DT"
#endif
  static_assert(PIN_ROTARY_CLK >= 0 && PIN_ROTARY_CLK <= 63 &&
                PIN_ROTARY_DT >= 0 && PIN_ROTARY_DT <= 63,
                "Rotary pins must be Arduino GPIO numbers 0..63");
  defaults.enabled = true;
  defaults.clk = PIN_ROTARY_CLK;
  defaults.dt = PIN_ROTARY_DT;
#ifdef PIN_ROTARY_BUTTON
  static_assert(PIN_ROTARY_BUTTON >= -1 && PIN_ROTARY_BUTTON <= 63,
                "Rotary switch must be -1 or Arduino GPIO 0..63");
  defaults.button = PIN_ROTARY_BUTTON;
#endif
#ifdef ROTARY_TRANSITIONS_PER_DETENT
  static_assert(ROTARY_TRANSITIONS_PER_DETENT >= 1 && ROTARY_TRANSITIONS_PER_DETENT <= 255,
                "ROTARY_TRANSITIONS_PER_DETENT must be 1..255");
  defaults.steps = ROTARY_TRANSITIONS_PER_DETENT;
#endif
#ifdef ROTARY_REVERSE
  defaults.reverse = ROTARY_REVERSE;
#endif
#ifdef ROTARY_PULLUP
  defaults.pullup = ROTARY_PULLUP;
#endif
#endif
  return defaults;
}

const char* skipSpaces(const char* text) {
  while (*text == ' ' || *text == '\t') ++text;
  return text;
}

bool parseNumber(const char*& text, int16_t& result, bool allow_absent = false) {
  text = skipSpaces(text);
  bool negative = *text == '-';
  if (negative) ++text;
  if (*text < '0' || *text > '9') return false;
  unsigned number = 0;
  do {
    number = number * 10 + unsigned(*text++ - '0');
    if (number > 255) return false;
  } while (*text >= '0' && *text <= '9');
  if (negative && (!allow_absent || number != 1)) return false;
  result = negative ? -1 : int16_t(number);
  text = skipSpaces(text);
  return true;
}

bool parsePins(const char* text, Config& config) {
  config.button = -1;
  if (!parseNumber(text, config.clk) || *text++ != ',' ||
      !parseNumber(text, config.dt)) return false;
  if (*text == ',') {
    ++text;
    if (!parseNumber(text, config.button, true)) return false;
  }
  return *text == 0 && validShape(config);
}

bool parseBoolean(const char* text, bool& value) {
  if (strcmp(text, "on") == 0) { value = true; return true; }
  if (strcmp(text, "off") == 0) { value = false; return true; }
  return false;
}

bool keyIs(const char* key, size_t size, const char* expected) {
  return strlen(expected) == size && memcmp(key, expected, size) == 0;
}

void availablePins(char* reply, size_t capacity) {
  // CLI replies are at most 160 bytes. Keep space for a visible truncation
  // marker so a full pin list never appears complete when it was cut short.
  if (capacity > 160) capacity = 160;
  const int prefix = snprintf(reply, capacity, "rotary.pins.available ");
  size_t used = prefix < 0 ? 0 : size_t(prefix);
  if (used >= capacity) return;
  bool any = false;
  for (uint8_t pin = 0; pin < 64; ++pin) {
    if (!main_board || !main_board->isPeripheralPinAvailable(pin, selected.pullup)) continue;
    const uint16_t physical = main_board->peripheralPinIdentity(pin);
    if (!pins::pinMask(physical) || pins::claimedByOther(physical, pins::Owner::Rotary)) continue;
    char token[5];
    const size_t count = size_t(snprintf(token, sizeof(token), "%s%u", any ? "," : "", unsigned(pin)));
    if (used + count + 4 >= capacity) {
      snprintf(reply + used, capacity - used, "...");
      return;
    }
    memcpy(reply + used, token, count + 1);
    used += count;
    any = true;
  }
  if (!any) snprintf(reply + used, capacity - used, "none");
}
} // namespace

bool beginRotarySettings(MainBoard& board, FILESYSTEM* fs, bool ui_supported) {
  input.end();
  pins::release(pins::Owner::Rotary, active_claims);
  active_claims = 0;
  selected = Config(); main_board = &board; settings_fs = fs;
  ui_available = ui_supported; writable = false;
  Config candidate;
  bool present = false, backup_present = false;
  if (fs) {
    if (!filePresence(fs, Path, present) || !filePresence(fs, Backup, backup_present)) return false;
    writable = true;
    if (present || backup_present) {
      if (!readConfig(Path, candidate)) {
        if (!readConfig(Backup, candidate)) return false;
        // Recovery must be confirmed before allowing later replacements.
        if ((present && !fs->remove(Path)) || !fs->rename(Backup, Path) ||
            !readConfig(Path, candidate)) {
          writable = false;
          return false;
        }
      }
    } else candidate = buildDefaults();
  } else candidate = buildDefaults();
  if (!validShape(candidate)) return false;
  selected = candidate;
  if (!candidate.enabled) return false;
  uint64_t claims = 0;
  if (!ui_available || !validatePins(candidate, claims) ||
      !pins::tryClaim(pins::Owner::Rotary, claims)) return false;
  return apply(candidate, claims);
}

RotaryInput* customRotaryInput() {
  return selected.enabled && input.isReady() ? &input : nullptr;
}

bool rotarySettingsEnabled() { return selected.enabled && input.isReady(); }

bool setRotaryUiSupported(bool supported) {
  ui_available = supported;
  if (!supported) {
    input.end();
    pins::release(pins::Owner::Rotary, active_claims);
    active_claims = 0;
    return false;
  }
  if (input.isReady()) return true;
  uint64_t claims = 0;
  if (!selected.enabled || !validatePins(selected, claims) ||
      !pins::tryClaim(pins::Owner::Rotary, claims)) return false;
  return apply(selected, claims);
}

bool rotaryOwnsButtonPin(uint8_t pin) {
  return rotarySettingsEnabled() && selected.button >= 0 && main_board &&
      main_board->peripheralPinIdentity(pin) ==
      main_board->peripheralPinIdentity(uint8_t(selected.button));
}

bool handleRotaryCommand(const char* command, char* reply, size_t capacity) {
  if (!command || !reply || !capacity) return false;
  const bool get = strncmp(command, "get ", 4) == 0;
  if (!get && strncmp(command, "set ", 4) != 0) return false;
  const char* key = command + 4;
  if (strncmp(key, "rotary", 6) != 0 ||
      (key[6] && key[6] != '.' && key[6] != ' ' && key[6] != '\t')) return false;
  const char* end = key;
  while (*end && *end != ' ' && *end != '\t') ++end;
  const size_t length = end - key;
  enum Field { All, Pins, Steps, Reverse, Pullup, Available, Unknown };
  const Field field = keyIs(key, length, "rotary") ? All
      : keyIs(key, length, "rotary.pins") ? Pins
      : keyIs(key, length, "rotary.steps") ? Steps
      : keyIs(key, length, "rotary.reverse") ? Reverse
      : keyIs(key, length, "rotary.pullup") ? Pullup
      : keyIs(key, length, "rotary.pins.available") ? Available : Unknown;
  const char* value = skipSpaces(end);
  if (field == Unknown) {
    snprintf(reply, capacity, "Error: unknown rotary setting");
    return true;
  }
  if (get) {
    if (*end) snprintf(reply, capacity, "Error: use get rotary[.pins|.steps|.reverse|.pullup]");
    else if (field == Available) availablePins(reply, capacity);
    else if (field == All) snprintf(reply, capacity,
        "rotary %s pins %d,%d,%d steps %u reverse %s pullup %s%s",
        selected.enabled ? "on" : "off", selected.clk, selected.dt, selected.button,
        unsigned(selected.steps), selected.reverse ? "on" : "off", selected.pullup ? "on" : "off",
        !selected.enabled || input.isReady() ? "" :
        !ui_available ? " inactive:no display" : " inactive:pin conflict");
    else if (field == Pins) snprintf(reply, capacity, "rotary.pins %d,%d,%d",
        selected.clk, selected.dt, selected.button);
    else if (field == Steps) snprintf(reply, capacity, "rotary.steps %u", unsigned(selected.steps));
    else snprintf(reply, capacity, "rotary.%s %s", field == Reverse ? "reverse" : "pullup",
        (field == Reverse ? selected.reverse : selected.pullup) ? "on" : "off");
    return true;
  }

  Config candidate = selected;
  bool parsed = false;
  if (field == Available) {
    snprintf(reply, capacity, "Error: rotary.pins.available is read-only");
    return true;
  }
  if (field == All) parsed = parseBoolean(value, candidate.enabled);
  else if (field == Pins) parsed = parsePins(value, candidate);
  else if (field == Reverse) parsed = parseBoolean(value, candidate.reverse);
  else if (field == Pullup) parsed = parseBoolean(value, candidate.pullup);
  else {
    int16_t steps = 0;
    const char* remaining = value;
    parsed = parseNumber(remaining, steps) && *remaining == 0 && steps >= 1;
    if (parsed) candidate.steps = steps;
  }
  uint64_t candidate_claims = 0;
  const char* error = nullptr;
  if (!parsed) error = "Error: invalid rotary value";
  else if (candidate.enabled && !ui_available) error = "Error: rotary UI unsupported";
  else if (!validShape(candidate)) error = "Error: invalid rotary value";
  else if ((candidate.enabled || field == Pins || field == Pullup) &&
           !validatePins(candidate, candidate_claims)) error = "Error: rotary pins reserved or unavailable";
  else {
    if (!candidate.enabled) candidate_claims = 0;
    const uint64_t added = candidate_claims & ~active_claims;
    if (!pins::tryClaim(pins::Owner::Rotary, candidate_claims)) error = "Error: rotary pins in use";
    else if (!saveConfig(candidate)) {
      pins::release(pins::Owner::Rotary, added);
      error = "Error: rotary save unconfirmed; reboot may differ";
    } else if (!apply(candidate, candidate_claims)) {
      error = "Error: rotary initialization failed";
    }
  }
  snprintf(reply, capacity, "%s", error ? error : "OK");
  return true;
}

} } // namespace mesh::ui
#endif
