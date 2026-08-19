#include <Arduino.h>
#include "target.h"
#include <helpers/ArduinoHelpers.h>
#include <helpers/sensors/MicroNMEALocationProvider.h>

ThinkNodeM1Board board;

RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, SPI);

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
ThinkNodeM1SensorManager sensors = ThinkNodeM1SensorManager(nmea);

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
#endif

bool radio_init() {
  rtc_clock.begin(Wire);
  return radio.std_init(&SPI);
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}

void ThinkNodeM1SensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_location->getGPSPowerSaving()) return;
  _location->syncTime();
  _location->setNextGPSOn(0);
  _location->setNextSleep();
}

void ThinkNodeM1SensorManager::start_gps() {
  if (gps_active) return;
  gps_active = true;
  _location->begin();
  armGpsPowerSavingCycle();
}

void ThinkNodeM1SensorManager::stop_gps() {
  if (!gps_active) return;
  gps_active = false;
  if (powersaving_enabled && _location->getGPSPowerSaving()) {
    _location->stopTimeSync();
    _location->setNextGPSOff(0);
    _location->setNextWake();
  }
  _location->stop();
}

bool ThinkNodeM1SensorManager::begin() {
  Serial1.begin(9600);

  // Initialize GPS switch pin
  pinMode(PIN_GPS_SWITCH, INPUT);
  last_gps_switch_state = digitalRead(PIN_GPS_SWITCH);

  // Initialize GPS power pin
  pinMode(GPS_EN, OUTPUT);

  // Check initial switch state to determine if GPS should be active
  if (last_gps_switch_state == HIGH) {  // Switch is HIGH when ON
    setSettingValue("gps", "1");
  }

  return true;
}

bool ThinkNodeM1SensorManager::querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) {
  queryGpsTelemetry(requester_permissions, telemetry);
  return true;
}

void ThinkNodeM1SensorManager::loop() {
  static unsigned long next_gps_update = 0;
  static unsigned long last_switch_check = 0;
  unsigned long now = millis();
  loopGpsTelemetry(now);

  // Check GPS switch state every second
  if (now - last_switch_check > 1000) {
    bool current_switch_state = digitalRead(PIN_GPS_SWITCH);
    
    // Detect switch state change
    if (current_switch_state != last_gps_switch_state) {
      last_gps_switch_state = current_switch_state;
      
      if (current_switch_state == HIGH) {  // Switch is ON
        MESH_DEBUG_PRINTLN("GPS switch ON");
        setSettingValue("gps", "1");
      } else {  // Switch is OFF
        MESH_DEBUG_PRINTLN("GPS switch OFF");
        setSettingValue("gps", "0");
      }
    }
    
    last_switch_check = now;
  }

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
    return;  // GPS is not active, skip further processing
  }

  _location->loop();

  if ((long)(now - next_gps_update) >= 0) {
    if (_location->isValid()) {
      node_lat = ((double)_location->getLatitude())/1000000.;
      node_lon = ((double)_location->getLongitude())/1000000.;
      node_altitude = ((double)_location->getAltitude()) / 1000.0;
      processGpsTelemetryFix(node_lat, node_lon, node_altitude, now);
      MESH_DEBUG_PRINTLN("lat %f lon %f", node_lat, node_lon);
    }
    next_gps_update = now + getGpsUpdateIntervalMillis();
  }
}

int ThinkNodeM1SensorManager::getNumSettings() const {
  return 1;  // always show GPS setting
}

const char* ThinkNodeM1SensorManager::getSettingName(int i) const {
  return (i == 0) ? "gps" : NULL;
}

const char* ThinkNodeM1SensorManager::getSettingValue(int i) const {
  if (i == 0) {
    return isGpsTelemetryUserEnabled() ? "1" : "0";
  }
  return NULL;
}

bool ThinkNodeM1SensorManager::setSettingValue(const char* name, const char* value) {
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

void ThinkNodeM1SensorManager::setPowerSavingEnabled(bool enabled) {
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
