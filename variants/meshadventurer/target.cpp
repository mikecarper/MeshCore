#include <Arduino.h>
#include "target.h"

#include <helpers/sensors/MicroNMEALocationProvider.h>

MeshadventurerBoard board;

static SPIClass spi;
RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, spi);
WRAPPER_CLASS radio_driver(radio, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
MASensorManager sensors = MASensorManager(nmea);

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
#endif

bool radio_init() {
  fallback_clock.begin();
  rtc_clock.begin(Wire);

#if defined(P_LORA_SCLK)
  return radio.std_init(&spi);
#else
  return radio.std_init();
#endif
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}


void MASensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_location->getGPSPowerSaving()) return;
  _location->syncTime();
  _location->setNextGPSOn(0);
  _location->setNextSleep();
}

void MASensorManager::start_gps() {
  if (gps_active) return;
  MESH_DEBUG_PRINTLN("starting GPS");
  _location->begin();
  _location->reset();
  gps_active = true;
  armGpsPowerSavingCycle();
}

void MASensorManager::stop_gps() {
  if (!gps_active) return;
  MESH_DEBUG_PRINTLN("stopping GPS");
  gps_active = false;
  if (powersaving_enabled && _location->getGPSPowerSaving()) {
    _location->stopTimeSync();
    _location->setNextGPSOff(0);
    _location->setNextWake();
  }
  _location->stop();
}

bool MASensorManager::begin() {
  Serial1.setPins(PIN_GPS_RX, PIN_GPS_TX);
  Serial1.begin(9600);
  return true;
}

bool MASensorManager::querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) {
  queryGpsTelemetry(requester_permissions, telemetry);
  return true;
}

void MASensorManager::loop() {
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

  if (gps_active) _location->loop();
  if ((long)(now - next_gps_update) >= 0 && gps_active) {
    if(_location->isValid()) {
      node_lat = ((double)_location->getLatitude()) / 1000000.;
      node_lon = ((double)_location->getLongitude()) / 1000000.;
      node_altitude = ((double)_location->getAltitude()) / 1000.0;
      processGpsTelemetryFix(node_lat, node_lon, node_altitude, now);
      MESH_DEBUG_PRINTLN("lat %f lon %f", node_lat, node_lon);
    }
    next_gps_update = now + getGpsUpdateIntervalMillis();
  }
}

int MASensorManager::getNumSettings() const { return 1; }  // just one supported: "gps" (power switch)

const char* MASensorManager::getSettingName(int i) const {
  return i == 0 ? "gps" : NULL;
}
const char* MASensorManager::getSettingValue(int i) const {
  if(i == 0) {
    return isGpsTelemetryUserEnabled() ? "1" : "0";
  }
  return NULL;
}
bool MASensorManager::setSettingValue(const char* name, const char* value) {
  if(strcmp(name, "gps") == 0) {
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

void MASensorManager::setPowerSavingEnabled(bool enabled) {
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
