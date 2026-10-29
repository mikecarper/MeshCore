#include "CommonCLI.h"

#if MESH_ENABLE_EMERGENCY_POWER
#include "CheckedSettingsFile.h"
#include "FleetLocation.h"
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif

namespace {
const char* const EMERGENCY_POWER_PATH = "/power_emergency";
const char* const POWER_SCHEDULE_PATH = "/power_schedule";

bool writePowerSettings(FILESYSTEM* fs, const char* path, const uint8_t* bytes, size_t size) {
  if (!fs) return false;
#if defined(NRF52_PLATFORM)
  return mesh::storage::writeCheckedSettingsFile(fs, path, bytes, size)
      == mesh::AtomicFileWriter::Outcome::Committed;
#else
#if defined(STM32_PLATFORM)
  mesh::AtomicFileWriter writer(fs, path);
#else
  mesh::ContactFileTransaction writer(fs, path);
#endif
  if (!writer || writer.write(bytes, size) != size || !writer.commit()) return false;
  uint8_t verified[mesh::powerschedule::RecordSize];
  return size <= sizeof(verified)
      && mesh::storage::readFixedSettingsFile(fs, path, verified, size)
      && memcmp(bytes, verified, size) == 0;
#endif
}

bool readEmergencyPower(FILESYSTEM* fs, const char* path,
                        mesh::emergencypower::Settings& settings) {
  uint8_t bytes[mesh::emergencypower::RecordSize];
  return mesh::storage::readFixedSettingsFile(fs, path, bytes, sizeof(bytes))
      && mesh::emergencypower::decode(bytes, sizeof(bytes), settings);
}

bool emergencyCommand(const char* command, const char* prefix, const char*& value) {
  const size_t length = strlen(prefix);
  if (strncmp(command, prefix, length) != 0
      || (command[length] && command[length] != ' ' && command[length] != '\t')) return false;
  value = command + length;
  while (*value == ' ' || *value == '\t') ++value;
  return true;
}

bool emergencyValue(const char* value, const char* expected) {
  const size_t length = strlen(expected);
  if (strncmp(value, expected, length) != 0) return false;
  value += length;
  while (*value == ' ' || *value == '\t') ++value;
  return !*value;
}

bool operatingHours(const char* value, mesh::powerschedule::Settings& settings) {
  size_t length = strlen(value);
  while (length && (value[length - 1] == ' ' || value[length - 1] == '\t')) --length;
  if (length != 11 || value[2] != ':' || value[5] != '-' || value[8] != ':') return false;
  uint16_t minute[2];
  for (unsigned i = 0; i < 2; ++i) {
    const char* p = value + i * 6;
    if (p[0] < '0' || p[0] > '9' || p[1] < '0' || p[1] > '9'
        || p[3] < '0' || p[3] > '9' || p[4] < '0' || p[4] > '9') return false;
    const unsigned hour = unsigned(p[0] - '0') * 10 + unsigned(p[1] - '0');
    const unsigned min = unsigned(p[3] - '0') * 10 + unsigned(p[4] - '0');
    if (min >= 60 || hour > (i ? 24U : 23U) || (hour == 24 && min)) return false;
    minute[i] = uint16_t(hour * 60 + min);
  }
  if (minute[0] == minute[1]) return false;
  settings.startMinute = minute[0]; settings.endMinute = minute[1]; settings.hours = true;
  return true;
}

bool operatingOffset(const char* value, int16_t& offset) {
  const bool negative = *value == '-';
  if (negative || *value == '+') ++value;
  const char* first = value;
  unsigned number = 0;
  while (*value >= '0' && *value <= '9') {
    number = number * 10 + unsigned(*value++ - '0');
    if (number > (negative ? 720U : 840U)) return false;
  }
  if (value == first) return false;
  while (*value == ' ' || *value == '\t') ++value;
  if (*value) return false;
  offset = negative ? -int16_t(number) : int16_t(number);
  return true;
}

const char* daylightLabel(mesh::solar::State state) {
  return state == mesh::solar::State::Day ? "day"
      : state == mesh::solar::State::Night ? "night" : "unknown";
}

void operatingHoursText(const mesh::powerschedule::Settings& settings, char* text, size_t size) {
  if (!settings.hours) snprintf(text, size, "off");
  else snprintf(text, size, "%02u:%02u-%02u:%02u", unsigned(settings.startMinute / 60),
      unsigned(settings.startMinute % 60), unsigned(settings.endMinute / 60),
      unsigned(settings.endMinute % 60));
}
}

void CommonCLI::loadEmergencyPower(FILESYSTEM* fs) {
  _emergency_power_fs = fs;
  _power_settings_blocked = 0;
  mesh::emergencypower::Settings loaded;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!mesh::ContactFileTransaction::recover(fs, EMERGENCY_POWER_PATH))
    _power_settings_blocked |= 1;
  else
#endif
  if (!readEmergencyPower(fs, EMERGENCY_POWER_PATH, loaded))
    readEmergencyPower(fs, "/power_emergency.bak", loaded);
  _emergency_power.begin(loaded, millis());
  _emergency_radio_sleeping = false;
  loadPowerSchedule(fs);
}

bool CommonCLI::saveEmergencyPower(const mesh::emergencypower::Settings& settings) {
  if (_power_settings_blocked & 1) return false;
  uint8_t bytes[mesh::emergencypower::RecordSize];
  mesh::emergencypower::encode(bytes, settings);
  return writePowerSettings(_emergency_power_fs, EMERGENCY_POWER_PATH, bytes, sizeof(bytes));
}

void CommonCLI::loadPowerSchedule(FILESYSTEM* fs) {
  _power_settings_blocked &= uint8_t(~2U);
  mesh::powerschedule::Settings loaded;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!mesh::ContactFileTransaction::recover(fs, POWER_SCHEDULE_PATH))
    _power_settings_blocked |= 2;
#endif
  if (fs && !(_power_settings_blocked & 2)) {
    uint8_t bytes[mesh::powerschedule::RecordSize];
    if (!(mesh::storage::readFixedSettingsFile(fs, POWER_SCHEDULE_PATH, bytes, sizeof(bytes))
          && mesh::powerschedule::decode(bytes, sizeof(bytes), loaded))) {
      if (mesh::storage::readFixedSettingsFile(fs, "/power_schedule.bak", bytes, sizeof(bytes)))
        mesh::powerschedule::decode(bytes, sizeof(bytes), loaded);
    }
  }
  _power_schedule.begin(loaded);
  if (loaded.daylight || loaded.hours) _emergency_power.holdReplies(millis());
}

bool CommonCLI::savePowerSchedule(const mesh::powerschedule::Settings& settings) {
  if (_power_settings_blocked & 2) return false;
  uint8_t bytes[mesh::powerschedule::RecordSize];
  mesh::powerschedule::encode(bytes, settings);
  return writePowerSettings(_emergency_power_fs, POWER_SCHEDULE_PATH, bytes, sizeof(bytes));
}

uint32_t CommonCLI::powerScheduleSleepMilliseconds(uint32_t epoch) {
  int32_t latitude = 0, longitude = 0;
  const bool located = _power_schedule.settings.daylight
      && mesh::readFleetLocation(*_prefs, *_sensors, latitude, longitude);
  return _power_schedule.sleepMilliseconds(epoch, located, latitude, longitude);
}

bool CommonCLI::handlePowerScheduleCommand(const char* command, char* reply) {
  const char* value;
  if (emergencyCommand(command, "get power.daylight", value)) {
    if (*value) snprintf(reply, 157, "Error: use get power.daylight");
    else {
      powerScheduleSleepMilliseconds(_rtc->getCurrentTime());
      if (!_power_schedule.settings.daylight) snprintf(reply, 157, "> off");
      else snprintf(reply, 157, "> on state=%s", daylightLabel(_power_schedule.daylightState));
    }
    return true;
  }
  if (emergencyCommand(command, "get power.hours", value)) {
    if (*value) snprintf(reply, 157, "Error: use get power.hours");
    else {
      char hours[12]; operatingHoursText(_power_schedule.settings, hours, sizeof(hours));
      snprintf(reply, 157, "> %s", hours);
    }
    return true;
  }
  if (emergencyCommand(command, "get power.hours.offset", value)) {
    if (*value) snprintf(reply, 157, "Error: use get power.hours.offset");
    else snprintf(reply, 157, "> %d", int(_power_schedule.settings.offsetMinutes));
    return true;
  }
  if (emergencyCommand(command, "get power.schedule", value)) {
    if (*value) snprintf(reply, 157, "Error: use get power.schedule");
    else {
      const uint32_t budget = emergencySleepMilliseconds();
      char hours[12]; operatingHoursText(_power_schedule.settings, hours, sizeof(hours));
      snprintf(reply, 157, "> daylight=%s state=%s hours=%s offset=%d clock=%s emergency=%s window=%s radio=%s check=%lus hold=%lus",
          _power_schedule.settings.daylight ? "on" : "off", daylightLabel(_power_schedule.daylightState),
          hours, int(_power_schedule.settings.offsetMinutes),
          mesh::emergencypower::State::usesRtc(_rtc->getCurrentTime()) ? "rtc" : "unknown",
          emergencyActive() ? "on" : "off", budget ? "sleep" : "awake",
          _emergency_radio_sleeping ? "sleep" : "awake",
          static_cast<unsigned long>((budget + 999UL) / 1000UL),
          static_cast<unsigned long>((_emergency_power.holdRemaining(millis()) + 999UL) / 1000UL));
    }
    return true;
  }
  mesh::powerschedule::Settings candidate = _power_schedule.settings;
  if (emergencyCommand(command, "set power.daylight", value)) {
    if (!emergencyValue(value, "on") && !emergencyValue(value, "off")) {
      snprintf(reply, 157, "Error: use set power.daylight on|off"); return true;
    }
    candidate.daylight = emergencyValue(value, "on");
  } else if (emergencyCommand(command, "set power.hours", value)) {
    if (emergencyValue(value, "off")) candidate.hours = false;
    else if (!operatingHours(value, candidate)) {
      snprintf(reply, 157, "Error: use set power.hours HH:MM-HH:MM|off"); return true;
    }
  } else if (emergencyCommand(command, "set power.hours.offset", value)) {
    int16_t offset;
    if (!operatingOffset(value, offset)) {
      snprintf(reply, 157, "Error: offset must be -720..840 minutes"); return true;
    }
    candidate.offsetMinutes = offset;
  } else if (emergencyCommand(command, "set power.schedule", value)) {
    if (!emergencyValue(value, "off")) {
      snprintf(reply, 157, "Error: use set power.schedule off"); return true;
    }
    candidate.daylight = candidate.hours = false;
  } else return false;
  if ((candidate.daylight || candidate.hours) && !_board->supportsEmergencySleep()) {
    snprintf(reply, 157, "Error: scheduled sleep unsupported on this board"); return true;
  }
  if (!mesh::powerschedule::equal(candidate, _power_schedule.settings)) {
    if (!savePowerSchedule(candidate)) {
      snprintf(reply, 157, "Error: schedule save unconfirmed; active policy unchanged"); return true;
    }
    _power_schedule.begin(candidate);
    _emergency_power.holdReplies(millis());
  }
  snprintf(reply, 157, "OK");
  return true;
}

void CommonCLI::loopEmergencyPower() {
  const uint32_t now = millis();
  if (!_emergency_power.due(now)) return;
  const uint16_t mv = _board->getBattMilliVolts();
  _emergency_power.sample(now, mv, batteryPercent(mv),
      mesh::batteryforward::validEndpoints(batteryEmptyMv(), batteryFullMv()));
}

uint32_t CommonCLI::emergencySleepMilliseconds() {
  loopEmergencyPower();
  const uint32_t now = millis(), epoch = _rtc->getCurrentTime();
  const uint32_t emergency = _emergency_power.sleepMilliseconds(now, epoch);
  const uint32_t schedule = powerScheduleSleepMilliseconds(epoch);
  if (_emergency_power.holdRemaining(now)) return 0;
  return emergency > schedule ? emergency : schedule;
}

bool CommonCLI::handleEmergencyPowerCommand(const char* command, char* reply) {
  if (!command || !reply) return false;
  if (handlePowerScheduleCommand(command, reply)) return true;
  const char* value;
  if (emergencyCommand(command, "get power.emergency", value)) {
    if (*value) snprintf(reply, 157, "Error: use get power.emergency");
    else {
      const uint32_t sleep_ms = emergencySleepMilliseconds();
      char battery[12] = "unknown";
      if (_emergency_power.valid)
        snprintf(battery, sizeof(battery), "%u%%", unsigned(_emergency_power.percentage));
      snprintf(reply, 157, "> mode=%s active=%s battery=%s clock=%s window=%s radio=%s wake=%lus hold=%lus",
          _emergency_power.settings.manual ? "manual" : "auto", emergencyActive() ? "on" : "off",
          battery, mesh::emergencypower::State::usesRtc(_rtc->getCurrentTime()) ? "rtc" : "uptime",
          sleep_ms ? "sleep" : "awake", _emergency_radio_sleeping ? "sleep" : "awake",
          static_cast<unsigned long>((sleep_ms + 999UL) / 1000UL),
          static_cast<unsigned long>((_emergency_power.holdRemaining(millis()) + 999UL) / 1000UL));
    }
    return true;
  }
  if (emergencyCommand(command, "set power.emergency", value)) {
    const bool manual = emergencyValue(value, "on");
    if (!manual && !emergencyValue(value, "auto")) {
      snprintf(reply, 157, "Error: use set power.emergency on|auto");
      return true;
    }
    if (manual && !_board->supportsEmergencySleep()) {
      snprintf(reply, 157, "Error: emergency sleep unsupported on this board");
      return true;
    }
    mesh::emergencypower::Settings candidate;
    candidate.manual = manual;
    if (candidate.manual != _emergency_power.settings.manual && !saveEmergencyPower(candidate))
      snprintf(reply, 157, "Error: emergency settings save unconfirmed; active policy unchanged");
    else {
      _emergency_power.setManual(manual, millis());
      snprintf(reply, 157, "OK - emergency %s", manual ? "on" : "auto");
    }
    return true;
  }
  if (!emergencyCommand(command, "set power.awake", value)) return false;
  uint16_t minutes = 0;
  const char* p = value;
  bool digits = false;
  while (*p >= '0' && *p <= '9') {
    digits = true;
    const unsigned number = unsigned(minutes) * 10U + unsigned(*p++ - '0');
    if (number > mesh::emergencypower::MaximumAwakeMinutes) {
      snprintf(reply, 157, "Error: use set power.awake 0-10080 (minutes)");
      return true;
    }
    minutes = uint16_t(number);
  }
  while (*p == ' ' || *p == '\t') ++p;
  if (!digits || *p) snprintf(reply, 157, "Error: use set power.awake 0-10080 (minutes)");
  else {
    _emergency_power.stayAwake(minutes, millis());
    snprintf(reply, 157, "OK - awake for %u minutes", unsigned(minutes));
  }
  return true;
}
#endif // MESH_ENABLE_EMERGENCY_POWER
