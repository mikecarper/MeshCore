#include <Arduino.h>
#include "target.h"
#include <helpers/ArduinoHelpers.h>
#include <helpers/sensors/MicroNMEALocationProvider.h>

MeshSolarBoard board;

RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, SPI);

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
SolarSensorManager sensors = SolarSensorManager(nmea);
SolarExternalWatchdog external_watchdog;

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
#endif

bool radio_init() {
  rtc_clock.begin(Wire);
  return radio.std_init(&SPI);
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}

void SolarSensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_location->getGPSPowerSaving()) return;
  _location->syncTime();
  _location->setNextGPSOn(0);
  _location->setNextSleep();
}

void SolarSensorManager::start_gps() {
  if (gps_active) return;
  gps_active = true;
  _location->begin();
  armGpsPowerSavingCycle();
}

void SolarSensorManager::stop_gps() {
  if (!gps_active) return;
  gps_active = false;
  if (powersaving_enabled && _location->getGPSPowerSaving()) {
    _location->stopTimeSync();
    _location->setNextGPSOff(0);
    _location->setNextWake();
  }
  _location->stop();
}

bool SolarSensorManager::begin() {
  Serial1.begin(9600);
  // GPS is onboard. An immediate UART availability check races its first NMEA
  // sentence and permanently hid the GPS setting on most cold boots.
  gps_detected = true;
  gps_active = false;
  _location->stop();
  MESH_DEBUG_PRINTLN("Onboard GPS available");
  return true;
}

bool SolarSensorManager::querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) {
  queryGpsTelemetry(requester_permissions, telemetry);
  return true;
}

void SolarSensorManager::loop() {
  static unsigned long next_gps_update = 0;
  unsigned long now = millis();
  loopGpsTelemetry(now);

  if (powersaving_enabled && gps_detected && _location->getGPSPowerSaving()) {
    unsigned long next_off = _location->getNextGPSOff();
    unsigned long next_on = _location->getNextGPSOn();
    if (gps_active && !gpsTelemetryReceiverRequired(now)
        && ((next_off != 0 && (long)(now - next_off) >= 0)
            || !_location->waitingTimeSync())) {
      POWERSAVING_DEBUG_PRINTLN("GPS entering sleep");
      stop_gps();
    } else if (!gps_active && ((next_on != 0 && (long)(now - next_on) >= 0)
                               || _location->waitingTimeSync())) {
      POWERSAVING_DEBUG_PRINTLN("GPS waking");
      start_gps();
    }
  }

  if (gps_active) _location->loop();

  if ((long)(now - next_gps_update) >= 0) {
    if (gps_active && _location->isValid()) {
      node_lat = ((double)_location->getLatitude())/1000000.;
      node_lon = ((double)_location->getLongitude())/1000000.;
      node_altitude = ((double)_location->getAltitude()) / 1000.0;
      processGpsTelemetryFix(node_lat, node_lon, node_altitude, now);
      MESH_DEBUG_PRINTLN("lat %f lon %f", node_lat, node_lon);
    }
    next_gps_update = now + getGpsUpdateIntervalMillis();
  }
}

int SolarSensorManager::getNumSettings() const {
  return gps_detected ? 1 : 0;  // only show GPS setting if GPS is detected
}

const char* SolarSensorManager::getSettingName(int i) const {
  return (gps_detected && i == 0) ? "gps" : NULL;
}

const char* SolarSensorManager::getSettingValue(int i) const {
  if (gps_detected && i == 0) {
    return isGpsTelemetryUserEnabled() ? "1" : "0";
  }
  return NULL;
}

bool SolarSensorManager::setSettingValue(const char* name, const char* value) {
  if (gps_detected && strcmp(name, "gps") == 0) {
    bool enabled = strcmp(value, "0") != 0;
    bool was_active = gps_active;
    _location->setGPSPowerSaving(enabled && powersaving_enabled);
    setGpsTelemetryUserEnabled(enabled);
    if (enabled && powersaving_enabled && was_active) {
      armGpsPowerSavingCycle();
    }
    return true;
  }
  return SensorManager::setSettingValue(name, value);
}

void SolarSensorManager::setPowerSavingEnabled(bool enabled) {
  if (powersaving_enabled == enabled) return;
  powersaving_enabled = enabled;

  bool gps_user_enabled = isGpsTelemetryUserEnabled();
  _location->setGPSPowerSaving(enabled && gps_user_enabled);
  if (!gps_user_enabled) return;

  if (enabled) {
    if (gps_active) {
      armGpsPowerSavingCycle();
    } else {
      start_gps();
    }
  } else if (!gps_active) {
    start_gps();
  }
}

bool SolarExternalWatchdog::begin() {
  last_feed_watchdog = 0;
  pinMode(EXTERNAL_WATCHDOG_WAKE_PIN, INPUT);
  pinMode(EXTERNAL_WATCHDOG_DONE_PIN, OUTPUT);
  delay(1);
  digitalWrite(EXTERNAL_WATCHDOG_DONE_PIN, LOW);
  delay(1);
  feed();
  return true;
}
void SolarExternalWatchdog::loop() {
  if (millis() - last_feed_watchdog >= EXTERNAL_WATCHDOG_FEED_INTERVAL_MS) {
    feed();
  }
}

unsigned long SolarExternalWatchdog::getIntervalMs() const {
    unsigned long elapsed_ms = millis() - last_feed_watchdog;
    if (elapsed_ms >= EXTERNAL_WATCHDOG_FEED_INTERVAL_MS) {
      return 0;
    }
    return EXTERNAL_WATCHDOG_FEED_INTERVAL_MS - elapsed_ms;
}

void SolarExternalWatchdog::feed() {
    digitalWrite(EXTERNAL_WATCHDOG_DONE_PIN, HIGH);
    delay(1);
    digitalWrite(EXTERNAL_WATCHDOG_DONE_PIN, LOW);
    last_feed_watchdog = millis();
}
