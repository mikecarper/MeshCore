#include "target.h"

#include <Arduino.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/sensors/MicroNMEALocationProvider.h>

NanoG2Ultra board;

RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, SPI);

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
NanoG2UltraSensorManager sensors = NanoG2UltraSensorManager(nmea);

#ifdef DISPLAY_CLASS
DISPLAY_CLASS display;
MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
#endif

bool radio_init() {
  rtc_clock.begin(Wire);
  return radio.std_init(&SPI);
}

void NanoG2UltraSensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_location->getGPSPowerSaving()) return;
  _location->syncTime();
  _location->setNextGPSOn(0);
  _location->setNextSleep();
}

void NanoG2UltraSensorManager::start_gps() {
  if (gps_active) return;
  MESH_DEBUG_PRINTLN("Starting GPS");
  digitalWrite(PIN_GPS_STANDBY, HIGH); // Wake GPS from standby
  _location->begin();
  gps_active = true;
  armGpsPowerSavingCycle();
}

void NanoG2UltraSensorManager::stop_gps() {
  if (!gps_active) return;
  MESH_DEBUG_PRINTLN("Stopping GPS");
  gps_active = false;
  if (powersaving_enabled && _location->getGPSPowerSaving()) {
    _location->stopTimeSync();
    _location->setNextGPSOff(0);
    _location->setNextWake();
  }
  _location->stop();
  digitalWrite(PIN_GPS_STANDBY, LOW); // sleep GPS
}

bool NanoG2UltraSensorManager::begin() {
  pinMode(PIN_GPS_STANDBY, OUTPUT);
  digitalWrite(PIN_GPS_STANDBY, LOW); // Known onboard GPS starts asleep
  Serial1.setPins(PIN_GPS_TX, PIN_GPS_RX);
  Serial1.begin(9600);
  gps_active = false;
  return true;
}

bool NanoG2UltraSensorManager::querySensors(uint8_t requester_permissions, CayenneLPP &telemetry) {
  queryGpsTelemetry(requester_permissions, telemetry);
  return true;
}

void NanoG2UltraSensorManager::loop() {
  static unsigned long next_gps_update = 0;
  unsigned long now = millis();
  loopGpsTelemetry(now);

  if (powersaving_enabled && _location->getGPSPowerSaving()) {
    unsigned long next_off = _location->getNextGPSOff();
    unsigned long next_on = _location->getNextGPSOn();
    if (gps_active && !gpsTelemetryReceiverRequired(now)
        && ((next_off != 0 && (long)(now - next_off) >= 0)
            || !_location->waitingTimeSync())) {
      stop_gps();
    } else if (!gps_active && ((next_on != 0 && (long)(now - next_on) >= 0)
                               || _location->waitingTimeSync())) {
      start_gps();
    }
  }

  if (!gps_active) {
    return; // GPS is not active, skip further processing
  }

  _location->loop();

  if ((long)(now - next_gps_update) >= 0) {
    if (_location->isValid()) {
      node_lat = ((double)_location->getLatitude()) / 1000000.;
      node_lon = ((double)_location->getLongitude()) / 1000000.;
      node_altitude = ((double)_location->getAltitude()) / 1000.0;
      processGpsTelemetryFix(node_lat, node_lon, node_altitude, now);
      MESH_DEBUG_PRINTLN("VALID location: lat %f lon %f", node_lat, node_lon);
    } else {
      MESH_DEBUG_PRINTLN("INVALID location, waiting for fix");
    }
    MESH_DEBUG_PRINTLN("GPS satellites: %d", _location->satellitesCount());
    next_gps_update = now + getGpsUpdateIntervalMillis();
  }
}

int NanoG2UltraSensorManager::getNumSettings() const {
  return 1;
} // just one supported: "gps" (power switch)

const char *NanoG2UltraSensorManager::getSettingName(int i) const {
  return i == 0 ? "gps" : NULL;
}

const char *NanoG2UltraSensorManager::getSettingValue(int i) const {
  if (i == 0) {
    return isGpsTelemetryUserEnabled() ? "1" : "0";
  }
  return NULL;
}

bool NanoG2UltraSensorManager::setSettingValue(const char *name, const char *value) {
  if (strcmp(name, "gps") == 0) {
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

void NanoG2UltraSensorManager::setPowerSavingEnabled(bool enabled) {
  if (powersaving_enabled == enabled) return;
  powersaving_enabled = enabled;

  bool gps_user_enabled = isGpsTelemetryUserEnabled();
  _location->setGPSPowerSaving(enabled && gps_user_enabled);
  if (!gps_user_enabled) return;

  if (enabled) {
    if (gps_active) armGpsPowerSavingCycle();
    else start_gps();
  } else if (!gps_active) {
    start_gps();
  }
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng); // create new random identity
}
