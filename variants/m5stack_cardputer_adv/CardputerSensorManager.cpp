#include "CardputerSensorManager.h"

namespace {
const uint32_t STANDBY_RENEW_MS = 60UL * 60UL * 1000UL;
bool due(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}
}

#if ENV_INCLUDE_GPS
void CardputerSensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_location->getGPSPowerSaving()) return;
  _location->syncTimeForPowerSavingCycle();
  _location->setNextGPSOn(0);
  _location->setNextSleep();
  // The provider uses zero as "no deadline", including at millis rollover.
  if (static_cast<uint32_t>(_location->getNextGPSOff()) == 0) _location->setNextGPSOff(1);
}

void CardputerSensorManager::start_gps() {
  if (!gps_serial_open || gps_active) return;
  gps_standby(false);
  gps_active = true;
  // Do not turn a retained pre-sleep fix into a new telemetry observation.
  // Receiver startup is asynchronous; the normal bounded parser polls it.
  _location->clearPositionFix();
  _location->begin();
  next_gps_update = millis();
  if (!_location->getGPSPowerSaving()) _location->syncTimeForPowerSavingCycle();
  armGpsPowerSavingCycle();
}

void CardputerSensorManager::stop_gps() {
  if (!gps_active) return;
  gps_standby(true);
  gps_active = false;
  _location->stopTimeSync();
  _location->setNextGPSOff(0);
  if (powersaving_enabled && _location->getGPSPowerSaving()
      && isGpsTelemetryUserEnabled()) _location->setNextWake();
  else _location->setNextGPSOn(0);
  _location->stop();
}
#endif

void CardputerSensorManager::gps_standby(bool active) {
  // CASIC protocol sections CAS10/CAS12: hot start and standby for at most
  // 65535 seconds. The provider's MicroNMEA sender generates the checksum.
#if ENV_INCLUDE_GPS
  _location->sendSentence(active ? "$PCAS12,65535" : "$PCAS10,0");
#else
  // GPS-free roles still have a powered physical receiver. Keep it in standby
  // without linking a parser/provider or advertising a GPS firmware feature.
  Serial1.print(active ? "$PCAS12,65535*1E\r\n" : "$PCAS10,0*1C\r\n");
#endif
  standby_renew_at = static_cast<uint32_t>(millis()) + STANDBY_RENEW_MS;
}

bool CardputerSensorManager::begin() {
  if (gps_serial_open) return true;
  // Existing board macros name the cap's RX/TX nets; setPins takes MCU RX/TX.
  Serial1.setPins(PIN_GPS_TX, PIN_GPS_RX);
  Serial1.begin(GPS_BAUD_RATE);
  gps_serial_open = true;
  gps_active = false;
#if ENV_INCLUDE_GPS
  _location->setGPSPowerSaving(false);
  _location->stopTimeSync();
  _location->stop();
#endif
  // A fixed cap powers GNSS at boot even when the saved preference is off.
  // Silence it explicitly; UART traffic during startup is not a user choice.
  gps_standby(true);
#if !CARDPUTER_ADV_UI
#if ENV_INCLUDE_GPS
  // The fixed cap uses command-based standby. Keep the generic manager's GPS
  // discovery/transport permanently blocked: it must not reset or reopen this
  // UART independently. Its environmental discovery and telemetry stay intact.
  CardputerSensorBase::gps_serial_transport_blocked = true;
#endif
  return CardputerSensorBase::begin();
#else
  return true;
#endif
}

bool CardputerSensorManager::querySensors(uint8_t requester_permissions, CayenneLPP &telemetry) {
  // Shared cache, permissions, bounded acquisition and manual-off cancellation.
#if !CARDPUTER_ADV_UI
  return CardputerSensorBase::querySensors(requester_permissions, telemetry);
#elif ENV_INCLUDE_GPS
  queryGpsTelemetry(requester_permissions, telemetry);
#endif
  return true;
}

void CardputerSensorManager::loop() {
  if (!gps_serial_open) return;
#if !CARDPUTER_ADV_UI && (ENV_INCLUDE_GPS || defined(ENV_INCLUDE_BME680_BSEC))
  // Generic GPS discovery is blocked and its gps_detected/active remain false.
  // This services external BSEC sensors and the one shared GPS telemetry cache.
  CardputerSensorBase::loop();
#endif
  const uint32_t now = millis();
#if ENV_INCLUDE_GPS
#if CARDPUTER_ADV_UI
  loopGpsTelemetry(now);
#endif
  if (powersaving_enabled && isGpsTelemetryUserEnabled()
      && !isTrackerGpsModeEnabled() && _location->getGPSPowerSaving()) {
    const uint32_t next_off = _location->getNextGPSOff();
    const uint32_t next_on = _location->getNextGPSOn();
    if (gps_active && !gpsTelemetryReceiverRequired(now)
        && ((next_off != 0 && due(now, next_off)) || !_location->waitingTimeSync())) {
      stop_gps();
    } else if (!gps_active && ((next_on != 0 && due(now, next_on))
                               || _location->waitingTimeSync())) {
      start_gps();
    }
  }
#endif
  if (!gps_active) {
    // CAS12 eventually wakes on its own. Renew off/standby without changing
    // saved intent or creating a GPS telemetry/clock acquisition.
    if (due(now, standby_renew_at)) gps_standby(true);
    return;
  }
#if ENV_INCLUDE_GPS
  _location->loop();
  if (due(now, next_gps_update)) {
    if (_location->isValid()) {
      node_lat = static_cast<double>(_location->getLatitude()) / 1000000.0;
      node_lon = static_cast<double>(_location->getLongitude()) / 1000000.0;
      node_altitude = static_cast<double>(_location->getAltitude()) / 1000.0;
      processGpsTelemetryFix(node_lat, node_lon, node_altitude, now);
    }
    next_gps_update = now + getGpsUpdateIntervalMillis();
  }
#endif
}

int CardputerSensorManager::getNumSettings() const {
  return CardputerSensorBase::getNumSettings()
#if ENV_INCLUDE_GPS
      + 1
#endif
      ;
}

const char *CardputerSensorManager::getSettingName(int i) const {
#if ENV_INCLUDE_GPS
  if (i == 0) return "gps";
  --i;
#endif
  return CardputerSensorBase::getSettingName(i);
}

const char *CardputerSensorManager::getSettingValue(int i) const {
#if ENV_INCLUDE_GPS
  if (i == 0) return isGpsTelemetryUserEnabled() ? "1" : "0";
  --i;
#endif
  return CardputerSensorBase::getSettingValue(i);
}

bool CardputerSensorManager::setSettingValue(const char *name, const char *value) {
  if (!name || !value) return false;
#if ENV_INCLUDE_GPS
  if (strcmp(name, "gps") == 0) {
    const bool enabled = strcmp(value, "0") != 0;
    const bool was_active = gps_active;
    _location->setGPSPowerSaving(enabled && powersaving_enabled);
    setGpsTelemetryUserEnabled(enabled);
    if (enabled && powersaving_enabled && was_active) armGpsPowerSavingCycle();
    if (!enabled) {
      _location->stopTimeSync();
      _location->setNextGPSOn(0);
      _location->setNextGPSOff(0);
    }
    return true;
  }
#endif
  const bool accepted = CardputerSensorBase::setSettingValue(name, value);
#if ENV_INCLUDE_GPS
  if (accepted && strcmp(name, "gps_interval") == 0) next_gps_update = millis();
#endif
  return accepted;
}

void CardputerSensorManager::setPowerSavingEnabled(bool enabled) {
#if ENV_INCLUDE_GPS
  if (powersaving_enabled == enabled) return;
  powersaving_enabled = enabled;
  if (isTrackerGpsModeEnabled()) {
    _location->setGPSPowerSaving(false);
    return;
  }
  const bool user_enabled = isGpsTelemetryUserEnabled();
  _location->setGPSPowerSaving(enabled && user_enabled);
  if (!user_enabled) return;
  if (!gps_active) start_gps();
  else if (enabled) armGpsPowerSavingCycle();
#else
  CardputerSensorBase::setPowerSavingEnabled(enabled);
#endif
}

void CardputerSensorManager::shutdownGpsTransportForDeepSleep() {
  if (!gps_serial_open) return;
#if ENV_INCLUDE_GPS
  stop_gps();
#else
  gps_standby(true);
#endif
  Serial1.end();
  gps_serial_open = false;
}
