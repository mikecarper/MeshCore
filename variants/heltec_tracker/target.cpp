#include <Arduino.h>
#include "target.h"

#include <helpers/sensors/MicroNMEALocationProvider.h>

HeltecV3Board board;

#if defined(P_LORA_SCLK)
  static SPIClass spi;
  RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, spi);
#else
  RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
#endif

WRAPPER_CLASS radio_driver(radio, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
// GPS_EN (GPIO35) drives N-ch MOSFET -> P-ch high-side switch; GPS_RESET (GPIO36) active LOW
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock, GPS_RESET, GPS_EN, &board.periph_power);
HWTSensorManager sensors = HWTSensorManager(nmea);

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display(&board.periph_power);   // peripheral power pin is shared
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

void HWTSensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_location->getGPSPowerSaving()) return;
  _location->syncTime();
  _location->setNextGPSOn(0);
  _location->setNextSleep();
}

void HWTSensorManager::start_gps() {
  if (gps_active) return;
  _location->begin();  // Claims periph_power via RefCountedDigitalPin
  gps_active = true;
  armGpsPowerSavingCycle();
  Serial1.println("$CFGSYS,h35155*68");  // Configure GPS for all constellations
}

void HWTSensorManager::stop_gps() {
  if (!gps_active) return;
  gps_active = false;
  if (powersaving_enabled && _location->getGPSPowerSaving()) {
    _location->stopTimeSync();
    _location->setNextGPSOff(0);
    _location->setNextWake();
  }
  _location->stop();  // Releases periph_power via RefCountedDigitalPin
}

bool HWTSensorManager::begin() {
  // init GPS port
  Serial1.begin(115200, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  return true;
}

bool HWTSensorManager::querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) {
  queryGpsTelemetry(requester_permissions, telemetry);
  return true;
}

void HWTSensorManager::loop() {
  static unsigned long next_gps_update = 0;
  unsigned long now = millis();
  loopGpsTelemetry(now);

  if (powersaving_enabled && _location->getGPSPowerSaving()) {
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

int HWTSensorManager::getNumSettings() const { return 1; }  // just one supported: "gps" (power switch)

const char* HWTSensorManager::getSettingName(int i) const {
  return i == 0 ? "gps" : NULL;
}
const char* HWTSensorManager::getSettingValue(int i) const {
  if (i == 0) {
    return isGpsTelemetryUserEnabled() ? "1" : "0";
  }
  return NULL;
}
bool HWTSensorManager::setSettingValue(const char* name, const char* value) {
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

void HWTSensorManager::setPowerSavingEnabled(bool enabled) {
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
