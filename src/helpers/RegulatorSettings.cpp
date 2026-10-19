#include "RegulatorSettings.h"
#include "RegulatorPlatform.h"
#include "NRF52VoltageRules.h"
#include "NRF52VoltagePolicy.h"
#include "PersistentStoreFormat.h"
#if defined(NRF52_PLATFORM)
#include "AtomicFileWriter.h"
#endif
#include <Arduino.h>
#include <Dispatcher.h>
#include <stdio.h>
#include <string.h>

// Current STM32WL boards expose neither MCU nor radio regulator selection.
// Their radio and MCU share a supply, so adding that support requires a
// coordinated implementation rather than the independent controls used by
// nRF52 plus an external radio. Do not retain the unreachable persistence and
// recovery machinery on these flash-constrained targets. Match this exact
// driver: an STM32 target with a different radio keeps the ordinary path.
#if MESH_REGULATOR_FIXED_SUPPLY
namespace mesh { namespace power {
bool beginRegulatorSettings(MainBoard&, Radio&, FILESYSTEM*) { return true; }
void serviceRegulatorSettings() {}
const char* regulatorModeName(RegulatorMode mode) {
  return mode == RegulatorMode::Dcdc ? "dcdc" : mode == RegulatorMode::Ldo ? "ldo" : "auto";
}
uint8_t configuredRegulatorModes(bool) { return 0; }
RegulatorMode requestedRegulatorMode(bool) { return RegulatorMode::Default; }
RegulatorMode activeRegulatorMode(bool) { return RegulatorMode::Default; }
const char* activeRegulatorModeName(bool) { return "unknown"; }
bool regulatorFallbackActive(bool) { return false; }
bool regulatorRecoveryAvailable() { return false; }
// No regulator command exists on this driver. Let the ordinary CLI dispatch
// report its existing unknown-command/setting error, avoiding a second parser
// and error formatter solely for a capability that these targets lack.
bool handleRegulatorCommand(const char*, char*, size_t) { return false; }
void markRegulatorCleanReset() {}
void resumeRegulatorRecovery() {}
}} // namespace mesh::power
#else
namespace mesh { namespace power {
namespace {
MainBoard* main_board = nullptr;
Radio* main_radio = nullptr;
FILESYSTEM* settings_fs = nullptr;
RegulatorMode requested[2] = {RegulatorMode::Default, RegulatorMode::Default};
uint8_t saved_fallback = 0;
bool initialized = false;
bool fallback_saved = true;
uint32_t last_service = 0, last_battery = 0, last_save = 0;
RecoveryPolicy& recovery() {
  static RecoveryPolicy policy(platformRecoveryRecord());
  return policy;
}
const char* const path = "/regulator_v1";
const char* const temporary = "/regulator_v1.tmp";
const char* const backup = "/regulator_v1.bak";

void encode(RegulatorMode mcu, RegulatorMode radio, uint8_t fallback, uint8_t (&data)[12]) {
  memcpy(data, "REG1", 4);
  data[4] = uint8_t(mcu); data[5] = uint8_t(radio);
  data[6] = fallback; data[7] = 0;
  storage::writeLE32(data + 8, storage::updateCRC32(0xffffffffUL, data, 8));
}
bool readFile(const char* name, uint8_t (&data)[12]) {
  if (!settings_fs) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File file(*settings_fs);
  if (!file.open(name, FILE_O_READ)) return false;
#else
  File file = settings_fs->open(name, "r");
  if (!file) return false;
#endif
  const bool complete = file.size() == sizeof(data)
      && file.read(data, sizeof(data)) == sizeof(data);
  file.close();
  return complete && memcmp(data, "REG1", 4) == 0
      && data[4] <= 2 && data[5] <= 2 && data[6] <= RECOVERY_DOMAINS
      && data[7] == 0 && storage::readLE32(data + 8)
          == storage::updateCRC32(0xffffffffUL, data, 8);
}
bool save(RegulatorMode mcu, RegulatorMode radio, uint8_t fallback) {
  if (!settings_fs) return false;
#if defined(NRF52_PLATFORM)
  uint8_t data[12], checked[12];
  encode(mcu, radio, fallback, data);
  // The shared LittleFS writer already stages, flushes, checks size/CRC32C,
  // atomically publishes and verifies the public file. Reuse it rather than
  // linking a second replacement transaction into flash-constrained targets.
  mesh::AtomicFileWriter file(settings_fs, path);
  if (!file || file.write(data, sizeof(data)) != sizeof(data) || !file.commit()) return false;
  // Retain the format validation and exact-byte comparison as well as the
  // writer's independent transaction checksum. Legacy .bak files remain
  // readable at boot and are retired only after this verified publication.
  if (!readFile(path, checked) || memcmp(data, checked, sizeof(data)) != 0) return false;
  settings_fs->remove(backup);
  saved_fallback = fallback;
  return true;
#else
  settings_fs->remove(temporary);
  if (settings_fs->exists(temporary)) return false;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  File file(*settings_fs);
  if (!file.open(temporary, FILE_O_WRITE)) return false;
#elif defined(RP2040_PLATFORM)
  File file = settings_fs->open(temporary, "w");
  if (!file) return false;
#else
  File file = settings_fs->open(temporary, "w", true);
  if (!file) return false;
#endif
  uint8_t data[12], checked[12];
  encode(mcu, radio, fallback, data);
  const bool written = file.write(data, sizeof(data)) == sizeof(data);
  file.close();
  if (!written || !readFile(temporary, checked)
      || memcmp(data, checked, sizeof(data)) != 0) return false;
  if (settings_fs->exists(backup) && !settings_fs->remove(backup)) return false;
  if (settings_fs->exists(path) && !settings_fs->rename(path, backup)) return false;
  if (!settings_fs->rename(temporary, path)) {
    if (settings_fs->exists(backup)) settings_fs->rename(backup, path);
    return false;
  }
  settings_fs->remove(backup);
  saved_fallback = fallback;
  return true;
#endif
}

uint8_t readBatteryPercent(bool& valid) {
  uint16_t samples[3];
  for (unsigned i = 0; i < 3; ++i) {
    samples[i] = main_board->getBattMilliVolts();
    if (i != 2) delay(5);
  }
  if (samples[0] > samples[1]) { auto v = samples[0]; samples[0] = samples[1]; samples[1] = v; }
  if (samples[1] > samples[2]) { auto v = samples[1]; samples[1] = samples[2]; samples[2] = v; }
  if (samples[0] > samples[1]) samples[1] = samples[0];
  const uint16_t mv = samples[1];
  valid = mv >= 2000 && mv <= 5500;
  if (!valid) return 0;
#if defined(NRF52_PLATFORM) && defined(NRF52_POWER_MANAGEMENT)
  // Board ADC methods already return calibrated millivolts.
  return configuredBatteryPercent(mv);
#else
  return batteryPercent(mv, 3000, 4200);
#endif
}
RegulatorMode effective(bool mcu) {
  RegulatorMode mode = requested[mcu ? 0 : 1];
  if (mode == RegulatorMode::Default) mode = mcu
      ? main_board->defaultMcuRegulatorMode() : main_radio->defaultRegulatorMode();
  const uint8_t bit = mcu ? RECOVERY_MCU : RECOVERY_RADIO;
  if (mode == RegulatorMode::Dcdc && (recovery().fallbackMask() & bit)) mode = RegulatorMode::Ldo;
  return mode;
}
}

const char* regulatorModeName(RegulatorMode mode) {
  return mode == RegulatorMode::Dcdc ? "dcdc" : mode == RegulatorMode::Ldo ? "ldo" : "auto";
}
uint8_t configuredRegulatorModes(bool mcu) {
  if (!main_board || !main_radio) return 0;
  return mcu ? main_board->mcuRegulatorModes() : main_radio->regulatorModes();
}
RegulatorMode requestedRegulatorMode(bool mcu) { return requested[mcu ? 0 : 1]; }
RegulatorMode activeRegulatorMode(bool mcu) {
  if (!main_board || !main_radio) return RegulatorMode::Default;
  return mcu ? main_board->activeMcuRegulatorMode() : main_radio->activeRegulatorMode();
}
const char* activeRegulatorModeName(bool mcu) {
  const auto mode = activeRegulatorMode(mcu);
  return mode == RegulatorMode::Default ? "unknown" : regulatorModeName(mode);
}
bool regulatorFallbackActive(bool mcu) {
  return initialized && (recovery().fallbackMask() & (mcu ? RECOVERY_MCU : RECOVERY_RADIO));
}
bool regulatorRecoveryAvailable() { return initialized && platformRecoverySupported(); }

bool beginRegulatorSettings(MainBoard& board, Radio& radio, FILESYSTEM* fs) {
  if (initialized) return true;
  main_board = &board; main_radio = &radio; settings_fs = fs;
  if (!board.mcuRegulatorModes() && !radio.regulatorModes()) return true;
  uint8_t data[12];
  bool loaded = readFile(path, data);
  if (!loaded && readFile(backup, data)) {
    loaded = true;
    settings_fs->remove(path);
    settings_fs->rename(backup, path);
  }
  if (loaded) {
    requested[0] = RegulatorMode(data[4]); requested[1] = RegulatorMode(data[5]);
    saved_fallback = data[6];
  }
  recovery().begin(platformRecoverySupported() ? platformResetKind() : ResetKind::Unknown,
                   saved_fallback);
  initialized = true;
  uint8_t active_dcdc = 0;
  bool valid = false;
  const uint8_t percent = readBatteryPercent(valid);
  const uint32_t now = millis();
  const auto cpu_mode = effective(true), radio_mode = effective(false);
  if (configuredRegulatorModes(true) && cpu_mode == RegulatorMode::Dcdc) active_dcdc |= RECOVERY_MCU;
  if (configuredRegulatorModes(false) && radio_mode == RegulatorMode::Dcdc) active_dcdc |= RECOVERY_RADIO;
  recovery().arm(active_dcdc, now, percent, valid);
  fallback_saved = recovery().fallbackMask() == saved_fallback
      || save(requested[0], requested[1], recovery().fallbackMask());
  bool okay = true;
  if (configuredRegulatorModes(true)) {
    if (!board.applyMcuRegulator(cpu_mode)) {
      active_dcdc &= ~RECOVERY_MCU;
      recovery().arm(active_dcdc, now, percent, valid);
      okay = false;
    }
  }
  if (configuredRegulatorModes(false) && !radio.configureBootRegulator(radio_mode)) {
    active_dcdc &= ~RECOVERY_RADIO;
    recovery().updateActiveDomains(active_dcdc);
    okay = false;
  }
  last_service = last_battery = last_save = now;
  return okay;
}

void serviceRegulatorSettings() {
  if (!initialized) return;
  const uint32_t now = millis();
  if (uint32_t(now - last_service) < 1000) return;
  last_service = now;
  const uint8_t active_dcdc =
      (activeRegulatorMode(true) == RegulatorMode::Dcdc ? RECOVERY_MCU : 0)
      | (activeRegulatorMode(false) == RegulatorMode::Dcdc ? RECOVERY_RADIO : 0);
  recovery().updateActiveDomains(active_dcdc);
  if (uint32_t(now - last_battery) >= 30000) {
    bool valid = false;
    const uint8_t percent = readBatteryPercent(valid);
    last_battery = millis();
    recovery().updateBattery(last_battery, percent, valid);
  }
  recovery().heartbeat(millis());
  if (!fallback_saved && uint32_t(now - last_save) >= 30000) {
    last_save = now;
    fallback_saved = save(requested[0], requested[1], recovery().fallbackMask());
  }
}

bool handleRegulatorCommand(const char* command, char* reply, size_t capacity) {
  const bool get = strncmp(command, "get ", 4) == 0;
  const bool set = strncmp(command, "set ", 4) == 0;
  if (!get && !set) return false;
  const char* key = command + 4;
  bool mcu;
  size_t length;
  if (strncmp(key, "mcu.regulator", 13) == 0) { mcu = true; length = 13; }
  else if (strncmp(key, "radio.regulator", 15) == 0) { mcu = false; length = 15; }
  else return false;
  if (key[length] != 0 && key[length] != ' ') return false;
  if (!initialized || !configuredRegulatorModes(mcu)) {
    snprintf(reply, capacity, "Error: %s regulator selection unsupported", mcu ? "MCU" : "radio");
    return true;
  }
  const char* value = key + length;
  if (get) {
    if (*value) snprintf(reply, capacity, "Error: unexpected argument");
    else snprintf(reply, capacity, "> %s; active %s; fallback %s; crashes %u; recovery %s%s",
        regulatorModeName(requestedRegulatorMode(mcu)), activeRegulatorModeName(mcu),
        regulatorFallbackActive(mcu) ? "ldo" : "off",
        unsigned(recovery().crashCount(mcu ? RECOVERY_MCU : RECOVERY_RADIO)),
        regulatorRecoveryAvailable() ? "on" : "unsupported",
        fallback_saved ? "" : "; fallback not saved");
    return true;
  }
  while (*value == ' ') ++value;
  RegulatorMode mode;
  if (strcmp(value, "auto") == 0) mode = RegulatorMode::Default;
  else if (strcmp(value, "dcdc") == 0) mode = RegulatorMode::Dcdc;
  else if (strcmp(value, "ldo") == 0) mode = RegulatorMode::Ldo;
  else { snprintf(reply, capacity, "Error: use set %s.regulator auto|dcdc|ldo", mcu ? "mcu" : "radio"); return true; }
  if (!regulatorModeSupported(configuredRegulatorModes(mcu), mode)) {
    snprintf(reply, capacity, "Error: regulator mode unsupported by this hardware");
    return true;
  }
  const uint8_t domain = mcu ? RECOVERY_MCU : RECOVERY_RADIO;
  const uint8_t fallback = recovery().fallbackMask() & ~domain;
  const auto cpu = mcu ? mode : requested[0], rf = mcu ? requested[1] : mode;
  if (!save(cpu, rf, fallback)) {
    snprintf(reply, capacity, "Error: regulator setting save failed");
    return true;
  }
  requested[mcu ? 0 : 1] = mode;
  recovery().clearDomain(domain);
  fallback_saved = true;
  snprintf(reply, capacity, "OK - %s regulator %s saved; reboot required", mcu ? "MCU" : "radio", regulatorModeName(mode));
  return true;
}

void markRegulatorCleanReset() {
  if (initialized) recovery().markCleanReset();
  else {
    RecoveryPolicy policy(platformRecoveryRecord());
    policy.begin(ResetKind::Intentional, 0);
    policy.markCleanReset();
  }
}

void resumeRegulatorRecovery() {
  if (!initialized) return;
  bool valid = false;
  const uint8_t percent = readBatteryPercent(valid);
  const uint32_t now = millis();
  const uint8_t active_dcdc =
      (activeRegulatorMode(true) == RegulatorMode::Dcdc ? RECOVERY_MCU : 0)
      | (activeRegulatorMode(false) == RegulatorMode::Dcdc ? RECOVERY_RADIO : 0);
  recovery().arm(active_dcdc, now, percent, valid);
  last_service = last_battery = now;
}

}} // namespace mesh::power
#endif

extern "C" void meshRegulatorCleanReset() {
  // Intentional resets can happen before the settings/filesystem is ready.
  mesh::power::markRegulatorCleanReset();
}

extern "C" void meshRegulatorResumeRecovery() {
  mesh::power::resumeRegulatorRecovery();
}
