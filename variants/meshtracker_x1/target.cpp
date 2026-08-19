#include <Arduino.h>
#include "target.h"
#include <helpers/sensors/MicroNMEALocationProvider.h>

MeshTrackerX1Board board;

RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, SPI);

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock rtc_clock;
MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
MeshTrackerX1SensorManager sensors = MeshTrackerX1SensorManager(nmea);

#ifdef DISPLAY_CLASS
  NullDisplayDriver display;
#endif

bool radio_init() {
  return radio.std_init(&SPI);
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}

void MeshTrackerX1SensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_nmea->getGPSPowerSaving()) return;
  _nmea->syncTime();
  _nmea->setNextGPSOn(0);
  _nmea->setNextSleep();
}

void MeshTrackerX1SensorManager::start_gps() {
  if (gps_active) return;
  gps_active = true;
  // this init sequence comes from seeed examples and deals with all gps pins
  pinMode(GPS_EN, OUTPUT);
  digitalWrite(GPS_EN, HIGH);
  delay(10);
  pinMode(GPS_VRTC_EN, OUTPUT);
  digitalWrite(GPS_VRTC_EN, HIGH);
  delay(10);

  pinMode(GPS_RESET, OUTPUT);
  digitalWrite(GPS_RESET, HIGH);
  delay(10);
  digitalWrite(GPS_RESET, LOW);

  pinMode(GPS_SLEEP_INT, OUTPUT);
  digitalWrite(GPS_SLEEP_INT, HIGH);
  pinMode(GPS_RTC_INT, OUTPUT);
  digitalWrite(GPS_RTC_INT, LOW);
  _nmea->begin();
  armGpsPowerSavingCycle();
}

void MeshTrackerX1SensorManager::sleep_gps() {
  if (!gps_active) return;
  gps_active = false;
  if (powersaving_enabled && _nmea->getGPSPowerSaving()) {
    _nmea->stopTimeSync();
    _nmea->setNextGPSOff(0);
    _nmea->setNextWake();
  }
  _nmea->stop();
  digitalWrite(GPS_VRTC_EN, HIGH);   // keep RTC alive for faster fix on wake
  digitalWrite(GPS_EN, LOW);
  digitalWrite(GPS_RESET, LOW);
  digitalWrite(GPS_SLEEP_INT, HIGH);
  digitalWrite(GPS_RTC_INT, LOW);
}

void MeshTrackerX1SensorManager::stop_gps() {
  gps_active = false;
  _nmea->stop();
  digitalWrite(GPS_VRTC_EN, LOW);
  digitalWrite(GPS_EN, LOW);
  digitalWrite(GPS_RESET, LOW);
  digitalWrite(GPS_SLEEP_INT, HIGH);
  digitalWrite(GPS_RTC_INT, LOW);
}

bool MeshTrackerX1SensorManager::begin() {
  // init GPS
  Serial1.begin(GPS_BAUD_RATE);
  pinMode(GPS_VRTC_EN, OUTPUT);
  pinMode(GPS_EN, OUTPUT);
  pinMode(GPS_RESET, OUTPUT);
  pinMode(GPS_SLEEP_INT, OUTPUT);
  pinMode(GPS_RTC_INT, OUTPUT);
  stop_gps();

  // init SPA06-003 barometer
  baro_ok = spa06.begin(SPA06_003_DEFAULT_ADDR, &Wire) || spa06.begin(0x76, &Wire);
  if (baro_ok) {
    spa06.setPressureOversampling(SPA06_003_OVERSAMPLE_8);
    spa06.setTemperatureOversampling(SPA06_003_OVERSAMPLE_8);
    // 1 Hz continuous keeps reads non-blocking at minimal power cost
    spa06.setPressureMeasureRate(SPA06_003_RATE_1);
    spa06.setTemperatureMeasureRate(SPA06_003_RATE_1);
    spa06.setMeasurementMode(SPA06_003_MEAS_CONTINUOUS_BOTH);
  }
  return true;
}

bool MeshTrackerX1SensorManager::querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) {
  queryGpsTelemetry(requester_permissions, telemetry);
  if (requester_permissions & TELEM_PERM_ENVIRONMENT && baro_ok) {
    telemetry.addTemperature(TELEM_CHANNEL_SELF, spa06.readTemperature());
    telemetry.addBarometricPressure(TELEM_CHANNEL_SELF, spa06.readPressure());
  }
  return true;
}

void MeshTrackerX1SensorManager::loop() {
  static unsigned long next_gps_update = 0;
  unsigned long now = millis();
  loopGpsTelemetry(now);

  if (powersaving_enabled && _nmea->getGPSPowerSaving()) {
    unsigned long next_off = _nmea->getNextGPSOff();
    unsigned long next_on = _nmea->getNextGPSOn();
    if (gps_active && !gpsTelemetryReceiverRequired(now)
        && ((next_off != 0 && (long)(now - next_off) >= 0)
            || !_nmea->waitingTimeSync())) {
      sleep_gps();
    } else if (!gps_active && ((next_on != 0 && (long)(now - next_on) >= 0)
                               || _nmea->waitingTimeSync())) {
      start_gps();
    }
  }

  if (gps_active) _nmea->loop();

  if ((long)(now - next_gps_update) >= 0) {
    if (gps_active && _nmea->isValid()) {
      node_lat = ((double)_nmea->getLatitude())/1000000.;
      node_lon = ((double)_nmea->getLongitude())/1000000.;
      node_altitude = ((double)_nmea->getAltitude()) / 1000.0;
      processGpsTelemetryFix(node_lat, node_lon, node_altitude, now);
    }
    next_gps_update = now + getGpsUpdateIntervalMillis();
  }
}

int MeshTrackerX1SensorManager::getNumSettings() const { return 1; }  // just one supported: "gps" (power switch)

const char* MeshTrackerX1SensorManager::getSettingName(int i) const {
  return i == 0 ? "gps" : NULL;
}
const char* MeshTrackerX1SensorManager::getSettingValue(int i) const {
  if (i == 0) {
    return isGpsTelemetryUserEnabled() ? "1" : "0";
  }
  return NULL;
}
bool MeshTrackerX1SensorManager::setSettingValue(const char* name, const char* value) {
  if (strcmp(name, "gps") == 0) {
    bool enabled = strcmp(value, "0") != 0;
    bool was_active = gps_active;
    _nmea->setGPSPowerSaving(enabled && powersaving_enabled);
    setGpsTelemetryUserEnabled(enabled);
    if (enabled && powersaving_enabled && was_active) {
      armGpsPowerSavingCycle();
    }
    return true;
  }
  return SensorManager::setSettingValue(name, value);
}

void MeshTrackerX1SensorManager::setPowerSavingEnabled(bool enabled) {
  if (powersaving_enabled == enabled) return;
  powersaving_enabled = enabled;

  bool gps_user_enabled = isGpsTelemetryUserEnabled();
  _nmea->setGPSPowerSaving(enabled && gps_user_enabled);
  if (!gps_user_enabled) return;

  if (enabled) {
    if (gps_active) armGpsPowerSavingCycle();
    else start_gps();
  } else if (!gps_active) {
    start_gps();
  }
}
