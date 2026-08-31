#include "EnvironmentSensorManager.h"
#include "I2CAddressClaimPolicy.h"
#include "EnvironmentI2CConfig.h"
#include "NmeaSentenceProbe.h"

#include <Wire.h>

#if ENV_HAS_SECONDARY_I2C
#define TELEM_WIRE &Wire1  // Use Wire1 as the I2C bus for Environment Sensors
#else
#define TELEM_WIRE &Wire  // Use default I2C bus for Environment Sensors
#endif

#ifdef NRF52_PLATFORM
static bool isValidNrfI2cPinPair(int32_t sda, int32_t scl) {
  if (!mesh::isValidI2cPinPair(sda, scl, PINS_COUNT)) return false;
#ifdef NRF_P1
  static const uint32_t MAX_PHYSICAL_NRF_PIN = 48;
#else
  static const uint32_t MAX_PHYSICAL_NRF_PIN = 32;
#endif
  return digitalPinToPinName(static_cast<uint32_t>(sda))
             < MAX_PHYSICAL_NRF_PIN
      && digitalPinToPinName(static_cast<uint32_t>(scl))
             < MAX_PHYSICAL_NRF_PIN;
}
#endif

// The pinned Adafruit nRF52 Wire core has no transaction timeout. Reject a
// bus which is already held low, and make one bounded standard nine-clock
// recovery attempt, before entering this manager's blocking transaction loops.
// This cannot protect I2C users which ran earlier in boot or cure a peripheral
// which wedges mid-transaction, but it prevents discovery from entering a bus
// which is already visibly stuck and limits the number of exposed transactions.
static bool ensureI2cBusReleased(TwoWire* wire) {
#ifdef NRF52_PLATFORM
  // Board::begin() may remap the primary Wire instance away from the variant's
  // PIN_WIRE_* defaults (ProMicro is one example). Use the same board-level
  // pins first so recovery never probes or drives an unrelated GPIO.
  int32_t sda = -1;
  int32_t scl = -1;
#if ENV_HAS_SECONDARY_I2C
  if (wire == &Wire1) {
    sda = ENV_PIN_SDA;
    scl = ENV_PIN_SCL;
  } else
#endif
  {
#if defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)
    sda = PIN_BOARD_SDA;
    scl = PIN_BOARD_SCL;
#elif defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
    sda = PIN_WIRE_SDA;
    scl = PIN_WIRE_SCL;
#endif
  }

  if (!isValidNrfI2cPinPair(sda, scl)) return false;

  if (digitalRead(sda) == HIGH && digitalRead(scl) == HIGH) return true;

  wire->end();
  auto releaseLine = [](int32_t pin) {
    // OUTPUT_S0D1 is nRF open drain: drive zero, disconnect for one. The input
    // pull-up then observes the external bus without ever driving it high.
    digitalWrite(pin, HIGH);
    pinMode(pin, INPUT_PULLUP);
  };
  auto driveLineLow = [](int32_t pin) {
    // Prime the output latch before enabling an output, avoiding a brief
    // push-pull high pulse if the previous latch happened to contain one.
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT_S0D1);
  };
  auto waitLineHigh = [](int32_t pin) {
    for (uint8_t wait = 0; wait < 20; wait++) {
      if (digitalRead(pin) == HIGH) return true;
      delayMicroseconds(5);
    }
    return digitalRead(pin) == HIGH;
  };

  releaseLine(sda);
  releaseLine(scl);
  delayMicroseconds(10);

  // A secondary may have stopped halfway through a byte. Clock it to a byte
  // boundary while never driving either open-drain line high.
  if (digitalRead(scl) == HIGH) {
    for (uint8_t pulse = 0; pulse < 9 && digitalRead(sda) == LOW; pulse++) {
      driveLineLow(scl);
      delayMicroseconds(5);
      releaseLine(scl);
      if (!waitLineHigh(scl)) break;
    }

    if (waitLineHigh(scl)) {
      // Generate a STOP (SDA low-to-high while SCL is confirmed high).
      driveLineLow(sda);
      delayMicroseconds(5);
      releaseLine(scl);
      if (waitLineHigh(scl)) {
        releaseLine(sda);
        delayMicroseconds(10);
      }
    }
  }

#if ENV_HAS_SECONDARY_I2C
  if (wire == &Wire1) {
    Wire1.setPins(static_cast<uint8_t>(sda), static_cast<uint8_t>(scl));
    Wire1.begin();
    Wire1.setClock(100000);
  } else
#endif
  {
    Wire.setPins(static_cast<uint8_t>(sda), static_cast<uint8_t>(scl));
    Wire.begin();
  }
  delayMicroseconds(10);
  return digitalRead(sda) == HIGH && digitalRead(scl) == HIGH;
#else
  (void)wire;
  return true;
#endif
}

bool EnvironmentSensorManager::i2c_probe(TwoWire& wire, uint8_t addr) {
  if (!mesh::isValidI2cPeripheralAddress(addr)) return false;
  wire.beginTransmission(addr);
  uint8_t error = wire.endTransmission();
  return error == 0;
}

// ============================================================
// Sensor library includes and static driver instances
// ============================================================

#if ENV_INCLUDE_BME680_BSEC
#ifndef TELEM_BME680_ADDRESS
#define TELEM_BME680_ADDRESS 0x76
#endif
#define TELEM_BME680_SEALEVELPRESSURE_HPA (1013.25)
#include <bsec.h>
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>
static const uint8_t bsec_config_iaq[] = {
#include "config/generic_33v_3s_28d/bsec_iaq.txt" // 3.3v, LP, 28 day background calibration window
};
static Bsec     bsec_iaq;
static float    bsec_temperature     = 0;
static float    bsec_humidity        = 0;
static float    bsec_pressure_hpa    = 0;
static float    bsec_iaq_val         = 0;
static uint8_t  bsec_accuracy        = 0;
static bool     bsec_active          = false;
static bool     bsec_data_ready      = false;
static bool     bsec_first_save_done = false;
static uint32_t bsec_last_save_ms    = 0;
#define BSEC_STATE_FILE "/bsec_state.bin"
#define BSEC_SAVE_INTERVAL_MS (8UL * 60 * 60 * 1000) // 8 hour state-save interval
#endif

#ifdef ENV_INCLUDE_BME680
#ifndef TELEM_BME680_ADDRESS
#define TELEM_BME680_ADDRESS 0x76
#endif
#define TELEM_BME680_SEALEVELPRESSURE_HPA (1013.25)
#include <Adafruit_BME680.h>
static Adafruit_BME680 BME680(TELEM_WIRE);
#endif

#ifdef ENV_INCLUDE_BMP085
#define TELEM_BMP085_SEALEVELPRESSURE_HPA (1013.25)
#include <Adafruit_BMP085.h>
static Adafruit_BMP085 BMP085;
#endif

#if ENV_INCLUDE_AHTX0
#ifndef TELEM_AHTX_ADDRESS
#define TELEM_AHTX_ADDRESS      0x38      // AHT10, AHT20 temperature and humidity sensor I2C address
#endif
#include <Adafruit_AHTX0.h>
static Adafruit_AHTX0 AHTX0;
#endif

#if ENV_INCLUDE_BME280
#ifndef TELEM_BME280_ADDRESS
#define TELEM_BME280_ADDRESS    0x76      // BME280 environmental sensor I2C address
#endif
#define TELEM_BME280_SEALEVELPRESSURE_HPA (1013.25)    // Atmospheric pressure at sea level
#include <Adafruit_BME280.h>
static Adafruit_BME280 BME280;
#endif

#if ENV_INCLUDE_BMP280
#ifndef TELEM_BMP280_ADDRESS
#define TELEM_BMP280_ADDRESS    0x76      // BMP280 environmental sensor I2C address
#endif
#define TELEM_BMP280_SEALEVELPRESSURE_HPA (1013.25)    // Atmospheric pressure at sea level
#include <Adafruit_BMP280.h>
static Adafruit_BMP280 BMP280(TELEM_WIRE);
#endif

#if ENV_INCLUDE_SHTC3
#include <Adafruit_SHTC3.h>
static Adafruit_SHTC3 SHTC3;
#endif

#if ENV_INCLUDE_SHT4X
#ifndef TELEM_SHT4X_ADDRESS
#define TELEM_SHT4X_ADDRESS 0x44
#endif
#include <SensirionI2cSht4x.h>
static SensirionI2cSht4x SHT4X;
#endif

#if ENV_INCLUDE_LPS22HB
#include <Arduino_LPS22HB.h>
LPS22HBClass LPS22HB(*TELEM_WIRE);
#endif

#if ENV_INCLUDE_INA3221
#ifndef TELEM_INA3221_ADDRESS
#define TELEM_INA3221_ADDRESS     0x42    // INA3221 3 channel current sensor I2C address
#endif
#ifndef TELEM_INA3221_SHUNT_VALUE
#define TELEM_INA3221_SHUNT_VALUE 0.100 // most variants will have a 0.1 ohm shunts
#endif
#ifndef TELEM_INA3221_NUM_CHANNELS
#define TELEM_INA3221_NUM_CHANNELS 3
#endif
#include <Adafruit_INA3221.h>
static Adafruit_INA3221 INA3221;
#endif

#if ENV_INCLUDE_INA219
#ifndef TELEM_INA219_ADDRESS
#define TELEM_INA219_ADDRESS    0x40      // INA219 single channel current sensor I2C address
#endif
#include <Adafruit_INA219.h>
static Adafruit_INA219 INA219(TELEM_INA219_ADDRESS);
#endif

#if ENV_INCLUDE_INA260
#ifndef TELEM_INA260_ADDRESS
#define TELEM_INA260_ADDRESS    0x41      // INA260 single channel current sensor I2C address
#endif
#include <Adafruit_INA260.h>
static Adafruit_INA260 INA260;
#endif

#if ENV_INCLUDE_INA226
#ifndef TELEM_INA226_ADDRESS
#define TELEM_INA226_ADDRESS     0x44
#endif
#define TELEM_INA226_SHUNT_VALUE 0.100
#define TELEM_INA226_MAX_AMP     0.8
#include <INA226.h>
static INA226 INA226(TELEM_INA226_ADDRESS, TELEM_WIRE);
#endif

#if ENV_INCLUDE_MLX90614
#ifndef TELEM_MLX90614_ADDRESS
#define TELEM_MLX90614_ADDRESS 0x5A      // MLX90614 IR temperature sensor I2C address
#endif
#include <Adafruit_MLX90614.h>
static Adafruit_MLX90614 MLX90614;
#endif

#if ENV_INCLUDE_VL53L0X
#ifndef TELEM_VL53L0X_ADDRESS
#define TELEM_VL53L0X_ADDRESS 0x29      // VL53L0X time-of-flight distance sensor I2C address
#endif
#include <Adafruit_VL53L0X.h>
static Adafruit_VL53L0X VL53L0X;
#endif

#if ENV_INCLUDE_RAK12035
#ifndef TELEM_RAK12035_ADDRESS
#define TELEM_RAK12035_ADDRESS 0x20      // RAK12035 Soil Moisture sensor I2C address
#endif
#include "RAK12035_SoilMoisture.h"
static RAK12035_SoilMoisture RAK12035;
#endif

#if ENV_INCLUDE_GPS && defined(RAK_BOARD) && !defined(RAK_WISMESH_TAG)
#define RAK_WISBLOCK_GPS
#endif

#ifdef RAK_WISBLOCK_GPS
// Release capability manifests scan the linked firmware image rather than
// trusting build flags. Give the actual WisBlock provider a unique marker and
// keep an observable reference from begin() so LTO/section GC cannot discard
// it. The marker lives inside this provider guard, so generic GPS CLI text
// cannot produce a false positive.
extern "C" {
extern const char meshcore_capability_rak_wisblock_gps[];
const char meshcore_capability_rak_wisblock_gps[] __attribute__((used)) =
    "meshcore.capability.rak_wisblock_gps.v1";
}

// -1 = no enable pin; out-of-range values are no-ops in pinMode/digitalWrite,
// while 0 would be a real GPIO (P0.00 = LFXO crystal on nRF52)
static uint32_t gpsResetPin = -1;
static bool i2cGPSFlag = false;
static bool serialGPSFlag = false;
#ifndef TELEM_RAK12500_ADDRESS
#ifdef GPS_ADDRESS
#define TELEM_RAK12500_ADDRESS   GPS_ADDRESS
#else
#define TELEM_RAK12500_ADDRESS   0x42     //RAK12500 Ublox GPS via i2c
#endif
#endif
#include <SparkFun_u-blox_GNSS_Arduino_Library.h>
static SFE_UBLOX_GNSS ublox_GNSS;

#ifndef RAK_UART_GPS_PROBE_TIMEOUT_MS
#define RAK_UART_GPS_PROBE_TIMEOUT_MS 1200UL
#endif

static uint8_t rakGpsControlActiveLevel() {
#ifdef PIN_GPS_EN_ACTIVE
  return PIN_GPS_EN_ACTIVE;
#else
  return HIGH;
#endif
}

static void setRakGpsControl(uint8_t pin, bool shared_power_rail,
                             bool enabled) {
  pinMode(pin, OUTPUT);
  if (shared_power_rail) {
    // WB_IO2 controls every 3V3_S peripheral, not just the GPS. It must never
    // be dropped as a GPS power-saving operation.
    digitalWrite(pin, HIGH);
    return;
  }
  const uint8_t active = rakGpsControlActiveLevel();
  digitalWrite(pin, enabled ? active : static_cast<uint8_t>(!active));
}

static bool serialHasValidGpsSentence(Stream& serial, uint32_t timeout_ms) {
  mesh::NmeaSentenceProbe probe;
  const uint32_t started = millis();
  do {
    while (serial.available()) {
      if (probe.ingest(static_cast<uint8_t>(serial.read()))) return true;
    }
    delay(5);
  } while (static_cast<uint32_t>(millis() - started) < timeout_ms);
  return false;
}

#if ENV_INCLUDE_INA3221
static mesh::I2cRegisterProbeStatus readI2cRegister16(TwoWire* wire,
                                                       uint8_t address,
                                                       uint8_t reg,
                                                       uint16_t& value) {
  wire->beginTransmission(address);
  if (wire->write(reg) != 1) {
    return mesh::I2cRegisterProbeStatus::Inconclusive;
  }
  const uint8_t error = wire->endTransmission(false);
  if (error == 2) return mesh::I2cRegisterProbeStatus::NoResponse;
  if (error != 0) return mesh::I2cRegisterProbeStatus::Inconclusive;
  if (wire->requestFrom(address, static_cast<uint8_t>(2)) != 2) {
    return mesh::I2cRegisterProbeStatus::Inconclusive;
  }
  value = static_cast<uint16_t>(wire->read()) << 8;
  value |= static_cast<uint8_t>(wire->read());
  return mesh::I2cRegisterProbeStatus::Success;
}

static mesh::I2cIdentityProbeResult probeIna3221Identity(TwoWire* wire,
                                                         uint8_t address) {
  uint16_t manufacturer = 0;
  const mesh::I2cRegisterProbeStatus manufacturer_result = readI2cRegister16(
      wire, address, INA3221_REG_MANUFACTURER_ID, manufacturer);
  if (manufacturer_result != mesh::I2cRegisterProbeStatus::Success) {
    return mesh::classifyIna3221Identity(
        manufacturer_result, manufacturer,
        mesh::I2cRegisterProbeStatus::Inconclusive, 0,
        INA3221_MANUFACTURER_ID, INA3221_DIE_ID);
  }
  if (manufacturer != INA3221_MANUFACTURER_ID) {
    // Require two successful non-INA reads. The bytes exposed by u-blox at
    // 0xFE/0xFF are dynamic, so their values need not equal one another. This
    // still cannot make a physical address collision safe.
    uint16_t repeated_manufacturer = 0;
    const mesh::I2cRegisterProbeStatus repeated_result = readI2cRegister16(
        wire, address, INA3221_REG_MANUFACTURER_ID, repeated_manufacturer);
    return mesh::classifyIna3221Identity(
        manufacturer_result, manufacturer,
        repeated_result, repeated_manufacturer,
        INA3221_MANUFACTURER_ID, INA3221_DIE_ID);
  }

  uint16_t die = 0;
  const mesh::I2cRegisterProbeStatus die_result = readI2cRegister16(
      wire, address, INA3221_REG_DIE_ID, die);
  return mesh::classifyIna3221Identity(
      manufacturer_result, manufacturer, die_result, die,
      INA3221_MANUFACTURER_ID, INA3221_DIE_ID);
}
#endif

class RAK12500LocationProvider : public LocationProvider {
  long _lat = 0;
  long _lng = 0;
  long _alt = 0;
  int _sats = 0;
  long _epoch = 0;
  bool _fix = false;
  mesh::RTCClock* _clock = NULL;
  unsigned long _next_time_check = 0;
  unsigned long _last_time_sync = 0;
  uint8_t _valid_time_samples = 0;
  static const unsigned long TIME_SYNC_INTERVAL = 1800000;
  int _pin_en = -1;
public:
  void setRTCClock(mesh::RTCClock* clock) { _clock = clock; }
  mesh::RTCClock* getRTCClock() override { return _clock; }
  void syncTime() override {
    _valid_time_samples = 0;
    LocationProvider::syncTime();
  }
  long getLatitude() override { return _lat; }
  long getLongitude() override { return _lng; }
  long getAltitude() override { return _alt; }
  long satellitesCount() override { return _sats; }
  bool isValid() override { return _fix; }
  long getTimestamp() override { return _epoch; }
  void sendSentence(const char * sentence) override { }
  void reset() override {
    // Discovery can replace and later reselect this singleton provider. Never
    // expose the previous receiver's fix or time samples while the new device
    // is still acquiring.
    _lat = 0;
    _lng = 0;
    _alt = 0;
    _sats = 0;
    _epoch = 0;
    _fix = false;
    _next_time_check = 0;
    _last_time_sync = 0;
    _valid_time_samples = 0;
    _time_sync_needed = true;
    _time_sync_applied = false;
    _last_valid_time_sync = 0;
  }
  void begin() override { }
  void stop() override { }
  void loop() override {
    unsigned long now = millis();
    if ((int32_t)(now - _next_time_check) >= 0) {
      _next_time_check = now + 1000;

      if (ublox_GNSS.getGnssFixOk(8)) {
        _fix = true;
        _lat = ublox_GNSS.getLatitude(2) / 10;
        _lng = ublox_GNSS.getLongitude(2) / 10;
        _alt = ublox_GNSS.getAltitude(2);
        _sats = ublox_GNSS.getSIV(2);
      } else {
        _fix = false;
      }
      bool date_valid = ublox_GNSS.getDateValid(2);
      bool time_valid = ublox_GNSS.getTimeValid(2);
      _epoch = ublox_GNSS.getUnixEpoch(2);

      if (_fix && _sats >= 5 && date_valid && time_valid && _epoch > 0) {
        if (_valid_time_samples < 0xFF) _valid_time_samples++;
      } else {
        _valid_time_samples = 0;
      }

      if (!_time_sync_needed && _clock != NULL
          && (unsigned long)(now - _last_time_sync) > TIME_SYNC_INTERVAL) {
        _time_sync_needed = true;
      }
      if (_time_sync_needed && _clock != NULL && _valid_time_samples > 2) {
        _clock->setCurrentTime((uint32_t)_epoch);
        markTimeSyncApplied();
        _time_sync_needed = false;
        _last_time_sync = now;
        _last_valid_time_sync = _clock->getCurrentTime();
      }
    }
  }
  bool isEnabled() override {
    return _pin_en < 0 || digitalRead(_pin_en) == HIGH;
  }
  void setPinEn(int pin_en) override { _pin_en = pin_en; }
  int getPinEn() override { return _pin_en; }
};

static RAK12500LocationProvider RAK12500_provider;
#endif

// ============================================================
// Per-sensor init and query functions
//
// init(wire, address) - called only when the address was seen
//   on the bus. Returns 0 on failure, or the number of
//   telemetry channels the sensor will consume (1 for all
//   single-output sensors; INA3221 returns one per enabled
//   hardware channel; MLX90614 and RAK12035+calibration
//   return 2).
//
// query(channel, sub_channel, lpp) - called once per active
//   sensor entry during querySensors(). sub_channel is always
//   0 for single-output sensors.
// ============================================================

#if ENV_INCLUDE_AHTX0
static uint8_t init_ahtx0(TwoWire* wire, uint8_t addr) {
  return AHTX0.begin(wire, 0, addr) ? 1 : 0;
}
static void query_ahtx0(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  sensors_event_t humidity, temp;
  AHTX0.getEvent(&humidity, &temp);
  lpp.addTemperature(ch, temp.temperature);
  lpp.addRelativeHumidity(ch, humidity.relative_humidity);
}
#endif

#ifdef ENV_INCLUDE_BME680
static uint8_t init_bme680(TwoWire*, uint8_t addr) {
  // Wire was set in the static constructor; begin() takes address only.
  return BME680.begin(addr) ? 1 : 0;
}
static void query_bme680(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  if (BME680.performReading()) {
    lpp.addTemperature(ch, BME680.temperature);
    lpp.addRelativeHumidity(ch, BME680.humidity);
    const float pressure_hpa = BME680.pressure / 100.0f;
    lpp.addBarometricPressure(ch, pressure_hpa);
    lpp.addAltitude(ch, 44330.0f * (1.0f - powf(pressure_hpa / (float)TELEM_BME680_SEALEVELPRESSURE_HPA, 0.1903f)));
    lpp.addGenericSensor(ch, BME680.gas_resistance);
  }
}
#endif

#if ENV_INCLUDE_BME280
static uint8_t init_bme280(TwoWire* wire, uint8_t addr) {
  if (!BME280.begin(addr, wire)) return 0;
  BME280.setSampling(Adafruit_BME280::MODE_FORCED,
                     Adafruit_BME280::SAMPLING_X1,
                     Adafruit_BME280::SAMPLING_X1,
                     Adafruit_BME280::SAMPLING_X1,
                     Adafruit_BME280::FILTER_OFF,
                     Adafruit_BME280::STANDBY_MS_1000);
  return 1;
}
static void query_bme280(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  if (BME280.takeForcedMeasurement()) {
    lpp.addTemperature(ch, BME280.readTemperature());
    lpp.addRelativeHumidity(ch, BME280.readHumidity());
    lpp.addBarometricPressure(ch, BME280.readPressure() / 100);
    lpp.addAltitude(ch, BME280.readAltitude(TELEM_BME280_SEALEVELPRESSURE_HPA));
  }
}
#endif

#if ENV_INCLUDE_BMP280
static uint8_t init_bmp280(TwoWire*, uint8_t addr) {
  // BMP280 static instance was constructed with TELEM_WIRE; begin() uses it.
  return BMP280.begin(addr) ? 1 : 0;
}
static void query_bmp280(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  lpp.addTemperature(ch, BMP280.readTemperature());
  lpp.addBarometricPressure(ch, BMP280.readPressure() / 100);
  lpp.addAltitude(ch, BMP280.readAltitude(TELEM_BMP280_SEALEVELPRESSURE_HPA));
}
#endif

#if ENV_INCLUDE_SHTC3
static uint8_t init_shtc3(TwoWire* wire, uint8_t) {
  // Adafruit_SHTC3::begin() does not accept an address (fixed at 0x70).
  return SHTC3.begin(wire) ? 1 : 0;
}
static void query_shtc3(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  sensors_event_t humidity, temp;
  SHTC3.getEvent(&humidity, &temp);
  lpp.addTemperature(ch, temp.temperature);
  lpp.addRelativeHumidity(ch, humidity.relative_humidity);
}
#endif

#if ENV_INCLUDE_SHT4X
static uint8_t init_sht4x(TwoWire* wire, uint8_t addr) {
  // SensirionI2cSht4x::begin() does not probe the hardware; use serialNumber()
  // as the actual presence check since it performs a real I2C transaction.
  SHT4X.begin(*wire, addr);
  uint32_t serial = 0;
  return (SHT4X.serialNumber(serial) == 0) ? 1 : 0;
}
static void query_sht4x(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  float temperature, humidity;
  if (SHT4X.measureLowestPrecision(temperature, humidity) == 0) {
    lpp.addTemperature(ch, temperature);
    lpp.addRelativeHumidity(ch, humidity);
  }
}
#endif

#if ENV_INCLUDE_LPS22HB
static uint8_t init_lps22hb(TwoWire*, uint8_t) {
  // LPS22HBClass is constructed with the wire reference; begin() uses it.
  return LPS22HB.begin() ? 1 : 0;
}
static void query_lps22hb(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  lpp.addTemperature(ch, LPS22HB.readTemperature());
  lpp.addBarometricPressure(ch, LPS22HB.readPressure() * 10); // convert kPa to hPa
}
#endif

#if ENV_INCLUDE_INA3221
static uint8_t init_ina3221(TwoWire* wire, uint8_t addr) {
  if (!INA3221.begin(addr, wire)) return 0;
  for (int i = 0; i < TELEM_INA3221_NUM_CHANNELS; i++) {
    INA3221.setShuntResistance(i, TELEM_INA3221_SHUNT_VALUE);
  }
  // Each enabled hardware channel becomes its own telemetry channel.
  uint8_t enabled = 0;
  for (int i = 0; i < TELEM_INA3221_NUM_CHANNELS; i++) {
    if (INA3221.isChannelEnabled(i)) enabled++;
  }
  return enabled > 0 ? enabled : 1;
}
static void query_ina3221(uint8_t ch, uint8_t sub_ch, CayenneLPP& lpp) {
  // sub_ch is the index of the nth enabled hardware channel.
  uint8_t seen = 0;
  for (int i = 0; i < TELEM_INA3221_NUM_CHANNELS; i++) {
    if (INA3221.isChannelEnabled(i)) {
      if (seen == sub_ch) {
        float v = INA3221.getBusVoltage(i);
        float c = INA3221.getCurrentAmps(i);
        lpp.addVoltage(ch, v);
        lpp.addCurrent(ch, c);
        lpp.addPower(ch, v * c);
        return;
      }
      seen++;
    }
  }
}
static bool query_ina3221_voltage(uint8_t sub_ch, float& voltage) {
  uint8_t seen = 0;
  for (int i = 0; i < TELEM_INA3221_NUM_CHANNELS; i++) {
    if (!INA3221.isChannelEnabled(i)) continue;
    if (seen++ != sub_ch) continue;
    voltage = INA3221.getBusVoltage(i);
    return isfinite(voltage);
  }
  return false;
}
#endif

#if ENV_INCLUDE_INA219
static uint8_t init_ina219(TwoWire* wire, uint8_t) {
  // INA219 static instance was constructed with the address; begin() uses it.
  return INA219.begin(wire) ? 1 : 0;
}
static void query_ina219(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  lpp.addVoltage(ch, INA219.getBusVoltage_V());
  lpp.addCurrent(ch, INA219.getCurrent_mA() / 1000.0f);
  lpp.addPower(ch, INA219.getPower_mW() / 1000.0f);
}
static bool query_ina219_voltage(uint8_t, float& voltage) {
  voltage = INA219.getBusVoltage_V();
  return INA219.success() && isfinite(voltage);
}
#endif

#if ENV_INCLUDE_INA260
static uint8_t init_ina260(TwoWire* wire, uint8_t addr) {
  return INA260.begin(addr, wire) ? 1 : 0;
}
static void query_ina260(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  lpp.addVoltage(ch, INA260.readBusVoltage() / 1000.0f);
  lpp.addCurrent(ch, INA260.readCurrent() / 1000.0f);
  lpp.addPower(ch, INA260.readPower() / 1000.0f);
}
static bool query_ina260_voltage(uint8_t, float& voltage) {
  voltage = INA260.readBusVoltage() / 1000.0f;
  return isfinite(voltage);
}
#endif

#if ENV_INCLUDE_INA226
static uint8_t init_ina226(TwoWire*, uint8_t) {
  // INA226 static instance was constructed with address and wire.
  if (!INA226.begin()) return 0;
  INA226.setMaxCurrentShunt(TELEM_INA226_MAX_AMP, TELEM_INA226_SHUNT_VALUE);
  return 1;
}
static void query_ina226(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  lpp.addVoltage(ch, INA226.getBusVoltage());
  lpp.addCurrent(ch, INA226.getCurrent_mA() / 1000.0f);
  lpp.addPower(ch, INA226.getPower_mW() / 1000.0f);
}
static bool query_ina226_voltage(uint8_t, float& voltage) {
  voltage = INA226.getBusVoltage();
  return isfinite(voltage);
}
#endif

#if ENV_INCLUDE_MLX90614
static uint8_t init_mlx90614(TwoWire* wire, uint8_t addr) {
  return MLX90614.begin(addr, wire) ? 2 : 0;  // 2 channels: object temp, ambient temp
}
static void query_mlx90614(uint8_t ch, uint8_t sub_ch, CayenneLPP& lpp) {
  if (sub_ch == 0)
    lpp.addTemperature(ch, MLX90614.readObjectTempC());
  else
    lpp.addTemperature(ch, MLX90614.readAmbientTempC());
}
#endif

#if ENV_INCLUDE_VL53L0X
static uint8_t init_vl53l0x(TwoWire* wire, uint8_t addr) {
  return VL53L0X.begin(addr, false, wire) ? 1 : 0;
}
static void query_vl53l0x(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  VL53L0X_RangingMeasurementData_t measure;
  VL53L0X.rangingTest(&measure, false);
  lpp.addDistance(ch, measure.RangeStatus != 4 ? measure.RangeMilliMeter / 1000.0f : 0.0f);
}
#endif

#ifdef ENV_INCLUDE_BMP085
static uint8_t init_bmp085(TwoWire* wire, uint8_t) {
  return BMP085.begin(0, wire) ? 1 : 0;  // mode 0 = ULTRALOWPOWER
}
static void query_bmp085(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  lpp.addTemperature(ch, BMP085.readTemperature());
  lpp.addBarometricPressure(ch, BMP085.readPressure() / 100);
  lpp.addAltitude(ch, BMP085.readAltitude(TELEM_BMP085_SEALEVELPRESSURE_HPA * 100));
}
#endif

#if ENV_INCLUDE_RAK12035
static uint8_t init_rak12035(TwoWire* wire, uint8_t addr) {
  // RAK12035 requires setup() before begin().
  RAK12035.setup(*wire);
  if (!RAK12035.begin(addr)) return 0;
#ifdef ENABLE_RAK12035_CALIBRATION
  return 2;  // moisture channel + calibration channel
#else
  return 1;
#endif
}
static void query_rak12035(uint8_t ch, uint8_t sub_ch, CayenneLPP& lpp) {
  if (sub_ch == 0) {
    lpp.addTemperature(ch, RAK12035.get_sensor_temperature());
    lpp.addPercentage(ch, RAK12035.get_sensor_moisture());
  } else {
#ifdef ENABLE_RAK12035_CALIBRATION
    float cap = RAK12035.get_sensor_capacitance();
    float wet = RAK12035.get_humidity_full();
    float dry = RAK12035.get_humidity_zero();
    lpp.addFrequency(ch, cap);
    lpp.addTemperature(ch, wet);
    lpp.addPower(ch, dry);
    if (cap > dry) RAK12035.set_humidity_zero(cap);
    if (cap < wet) RAK12035.set_humidity_full(cap);
#endif
  }
}
#endif

#if ENV_INCLUDE_BME680_BSEC
static void bsec_load_state() {
  using namespace Adafruit_LittleFS_Namespace;
  File f = InternalFS.open(BSEC_STATE_FILE, FILE_O_READ);
  if (!f) return;
  uint8_t state[BSEC_MAX_STATE_BLOB_SIZE];
  f.read(state, BSEC_MAX_STATE_BLOB_SIZE);
  f.close();
  bsec_iaq.setState(state);
}

static void bsec_save_state() {
  using namespace Adafruit_LittleFS_Namespace;
  uint8_t state[BSEC_MAX_STATE_BLOB_SIZE];
  bsec_iaq.getState(state);
  InternalFS.remove(BSEC_STATE_FILE);
  File f = InternalFS.open(BSEC_STATE_FILE, FILE_O_WRITE);
  if (!f) return;
  f.write(state, BSEC_MAX_STATE_BLOB_SIZE);
  f.close();
}

static uint8_t init_bme680_bsec(TwoWire* wire, uint8_t addr) {
  bsec_iaq.begin(addr, *wire);
  if (bsec_iaq.bsecStatus != BSEC_OK) return 0;

  bsec_iaq.setConfig(bsec_config_iaq);
  if (bsec_iaq.bsecStatus != BSEC_OK) return 0;

  bsec_virtual_sensor_t outputs[] = {
    BSEC_OUTPUT_IAQ,
    BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_TEMPERATURE,
    BSEC_OUTPUT_SENSOR_HEAT_COMPENSATED_HUMIDITY,
    BSEC_OUTPUT_RAW_PRESSURE,
    BSEC_OUTPUT_STABILIZATION_STATUS,
    BSEC_OUTPUT_RUN_IN_STATUS,
  };
  bsec_iaq.updateSubscription(outputs, 6, BSEC_SAMPLE_RATE_LP);
  if (bsec_iaq.bsecStatus != BSEC_OK) return 0;

  bsec_load_state();
  bsec_active = true;
  return 1;
}

static void query_bme680_bsec(uint8_t ch, uint8_t, CayenneLPP& lpp) {
  if (!bsec_data_ready) return;
  lpp.addTemperature(ch, bsec_temperature);
  lpp.addRelativeHumidity(ch, bsec_humidity);
  lpp.addBarometricPressure(ch, bsec_pressure_hpa);
  lpp.addAltitude(ch, 44330.0f * (1.0f - powf(bsec_pressure_hpa / (float)TELEM_BME680_SEALEVELPRESSURE_HPA, 0.1903f)));
  lpp.addGenericSensor(ch, (uint16_t)bsec_iaq_val);
  lpp.addAnalogInput(ch, (float)bsec_accuracy);
}
#endif

// ============================================================
// Sensor descriptor table
//
// Each entry maps an I2C address to a sensor's init and query
// functions. Only entries whose ENV_INCLUDE_* guard is defined
// are compiled in. The sentinel at the end keeps the array
// non-empty regardless of which sensors are enabled.
//
// Bosch BMP/BME SDO selects 0x76 or 0x77; probe both.
//
// Ordering here determines channel assignment at runtime:
// the first detected+initialized sensor gets channel 2, the
// next gets channel 3, and so on.
// ============================================================

struct SensorDef {
  uint32_t    address;  // preserve invalid build overrides until validation
  const char* name;
  uint8_t   (*init)(TwoWire* wire, uint8_t address);
  void      (*query)(uint8_t channel, uint8_t sub_channel, CayenneLPP& telemetry);
  bool      (*query_voltage)(uint8_t sub_channel, float& voltage);
};

#define TELEM_BOSCH_ALT_ADDR(addr) \
  ((addr) == 0x76 ? 0x77U : ((addr) == 0x77 ? 0x76U : (addr)))

static const SensorDef SENSOR_TABLE[] = {
#if ENV_INCLUDE_AHTX0
  { TELEM_AHTX_ADDRESS,    "AHT10/AHT20", init_ahtx0,    query_ahtx0,    NULL },
#endif
#ifdef ENV_INCLUDE_BME680
  { TELEM_BME680_ADDRESS,                       "BME680",       init_bme680,      query_bme680,      NULL },
  { TELEM_BOSCH_ALT_ADDR(TELEM_BME680_ADDRESS), "BME680",       init_bme680,      query_bme680,      NULL },
#endif
#if ENV_INCLUDE_BME680_BSEC
  { TELEM_BME680_ADDRESS,                       "BME680+BSEC",  init_bme680_bsec, query_bme680_bsec, NULL },
  { TELEM_BOSCH_ALT_ADDR(TELEM_BME680_ADDRESS), "BME680+BSEC",  init_bme680_bsec, query_bme680_bsec, NULL },
#endif
#if ENV_INCLUDE_BME280
  { TELEM_BME280_ADDRESS,                       "BME280",       init_bme280,      query_bme280,      NULL },
  { TELEM_BOSCH_ALT_ADDR(TELEM_BME280_ADDRESS), "BME280",       init_bme280,      query_bme280,      NULL },
#endif
#if ENV_INCLUDE_BMP280
  { TELEM_BMP280_ADDRESS,                       "BMP280",       init_bmp280,      query_bmp280,      NULL },
  { TELEM_BOSCH_ALT_ADDR(TELEM_BMP280_ADDRESS), "BMP280",       init_bmp280,      query_bmp280,      NULL },
#endif
#if ENV_INCLUDE_SHTC3
  { 0x70,                  "SHTC3",        init_shtc3,    query_shtc3,    NULL },
#endif
#if ENV_INCLUDE_SHT4X
  { TELEM_SHT4X_ADDRESS,   "SHT4X",        init_sht4x,    query_sht4x,    NULL },
#endif
#if ENV_INCLUDE_LPS22HB
  { 0x5C,                  "LPS22HB",      init_lps22hb,  query_lps22hb,  NULL },
#endif
#if ENV_INCLUDE_INA3221
  { TELEM_INA3221_ADDRESS, "INA3221",      init_ina3221,  query_ina3221,
    query_ina3221_voltage },
#endif
#if ENV_INCLUDE_INA219
  { TELEM_INA219_ADDRESS,  "INA219",       init_ina219,   query_ina219,
    query_ina219_voltage },
#endif
#if ENV_INCLUDE_INA260
  { TELEM_INA260_ADDRESS,  "INA260",       init_ina260,   query_ina260,
    query_ina260_voltage },
#endif
#if ENV_INCLUDE_INA226
  { TELEM_INA226_ADDRESS,  "INA226",       init_ina226,   query_ina226,
    query_ina226_voltage },
#endif
#if ENV_INCLUDE_MLX90614
  { TELEM_MLX90614_ADDRESS,"MLX90614",     init_mlx90614, query_mlx90614, NULL },
#endif
#if ENV_INCLUDE_VL53L0X
  { TELEM_VL53L0X_ADDRESS, "VL53L0X",      init_vl53l0x,  query_vl53l0x,  NULL },
#endif
#ifdef ENV_INCLUDE_BMP085
  { 0x77,                  "BMP085",       init_bmp085,   query_bmp085,   NULL },
#endif
#if ENV_INCLUDE_RAK12035
  { TELEM_RAK12035_ADDRESS,"RAK12035",     init_rak12035, query_rak12035, NULL },
#endif
  { 0, nullptr, nullptr, nullptr, nullptr }  // sentinel keeps array non-empty
};

#undef TELEM_BOSCH_ALT_ADDR

static const size_t SENSOR_TABLE_SIZE = (sizeof(SENSOR_TABLE) / sizeof(SENSOR_TABLE[0])) - 1;

// ============================================================
// begin() - initialize an optional board GPS, then scan the I2C bus and invoke
// only table-driven sensor initializers whose address ACKed. GPS uses a
// device-specific probe outside SENSOR_TABLE; an address it positively claims
// is not handed to another driver.
// ============================================================

bool EnvironmentSensorManager::begin() {
  _active_sensor_count = 0;

  #if ENV_INCLUDE_GPS
  #ifdef RAK_WISBLOCK_GPS
  // A volatile read anchors the externally linked marker even under LTO.
  const volatile char* capability_marker =
      meshcore_capability_rak_wisblock_gps;
  (void)*capability_marker;
  rakGPSInit();
  #else
  initBasicGPS();
  #endif
  #endif

  #if ENV_HAS_SECONDARY_I2C
    #ifdef NRF52_PLATFORM
  if (!isValidNrfI2cPinPair(ENV_PIN_SDA, ENV_PIN_SCL)) {
    MESH_DEBUG_PRINTLN("Second I2C skipped: invalid SDA/SCL pin pair");
    return true;
  }
  Wire1.setPins(static_cast<uint8_t>(ENV_PIN_SDA),
                static_cast<uint8_t>(ENV_PIN_SCL));
  Wire1.begin();
  Wire1.setClock(100000);
    #else
  Wire1.begin(ENV_PIN_SDA, ENV_PIN_SCL, 100000);
    #endif
  MESH_DEBUG_PRINTLN("Second I2C initialized on pins SDA: %d SCL: %d", ENV_PIN_SDA, ENV_PIN_SCL);
  #endif

  // Avoid touching a shared I2C bus when no environmental drivers were built.
  if (SENSOR_TABLE_SIZE == 0) {
    return true;
  }

  if (!ensureI2cBusReleased(TELEM_WIRE)) {
    MESH_DEBUG_PRINTLN(
        "I2C discovery skipped: bus pins unavailable or SDA/SCL remained low");
    return true;
  }

  // Probe only unique addresses compiled into this image before invoking any
  // table-driven sensor library. The old all-address scan performed 112
  // blocking Wire transactions even when only a few drivers were present.
  bool detected[128] = {};
  bool probed[128] = {};
  for (size_t i = 0; i < SENSOR_TABLE_SIZE; i++) {
    const uint32_t configured_address = SENSOR_TABLE[i].address;
    if (!mesh::isValidI2cPeripheralAddress(configured_address)) {
      MESH_DEBUG_PRINTLN("Skipping invalid I2C sensor address %lX",
                         (unsigned long)configured_address);
      continue;
    }
    const uint8_t address = static_cast<uint8_t>(configured_address);
    if (!probed[address]) {
      probed[address] = true;
      detected[address] = i2c_probe(*TELEM_WIRE, address);
    }
  }

  // Walk the sensor table and initialize only detected devices.
  for (size_t i = 0; i < SENSOR_TABLE_SIZE && _active_sensor_count < MAX_ACTIVE_SENSORS; i++) {
    const SensorDef& def = SENSOR_TABLE[i];
    if (!mesh::isValidI2cPeripheralAddress(def.address)) continue;
    const uint8_t address = static_cast<uint8_t>(def.address);
#ifdef RAK_WISBLOCK_GPS
    if (mesh::shouldSkipSensorAtClaimedGpsAddress(
            i2cGPSFlag, TELEM_WIRE == &Wire,
            address, TELEM_RAK12500_ADDRESS)) {
      MESH_DEBUG_PRINTLN(
          "Skipping %s at I2C address %02X: address is claimed by RAK12500 GPS",
          def.name, address);
      continue;
    }
#endif
    // One static driver instance per type: an alternate address is a fallback, not a second device.
    bool already_active = false;
    for (int j = 0; j < _active_sensor_count; j++) {
      if (_active_sensors[j].query == def.query) { already_active = true; break; }
    }
    if (already_active) continue;
    if (!detected[address]) {
      MESH_DEBUG_PRINTLN("%s not detected at I2C address %02X", def.name, address);
      continue;
    }
    uint8_t n = def.init(TELEM_WIRE, address);
    if (n == 0) {
      MESH_DEBUG_PRINTLN("%s found at %02X but failed to initialize", def.name, address);
      continue;
    }
    MESH_DEBUG_PRINTLN("Found %s at address: %02X", def.name, address);
    detected[address] = false;  // consumed; later entries must not re-claim this device
    for (uint8_t sub = 0; sub < n && _active_sensor_count < MAX_ACTIVE_SENSORS; sub++) {
      _active_sensors[_active_sensor_count++] = {
        def.query, def.query_voltage, sub
      };
    }
  }

  return true;
}

// ============================================================
// querySensors() - GPS stays on channel 1; each active sensor
// gets the next available channel in the order it was
// initialized.
// ============================================================

bool EnvironmentSensorManager::querySensors(uint8_t requester_permissions, CayenneLPP& telemetry) {
  next_available_channel = TELEM_CHANNEL_SELF + 1;

  #if ENV_INCLUDE_GPS
  queryGpsTelemetry(requester_permissions, telemetry);
  #endif

  if (requester_permissions & TELEM_PERM_ENVIRONMENT) {
    for (int i = 0; i < _active_sensor_count; i++) {
      _active_sensors[i].query(next_available_channel, _active_sensors[i].sub_channel, telemetry);
      next_available_channel++;
    }
  }

  return true;
}

uint8_t EnvironmentSensorManager::getVoltageSensorChannels(
    uint8_t channels[], uint8_t capacity) const {
  uint8_t count = 0;
  for (int i = 0; i < _active_sensor_count; i++) {
    if (_active_sensors[i].query_voltage == NULL) continue;
    if (count < capacity && channels != NULL) {
      channels[count] = (uint8_t)(TELEM_CHANNEL_SELF + 1 + i);
    }
    count++;
  }
  return count < capacity ? count : capacity;
}

uint8_t EnvironmentSensorManager::queryVoltageSensors(
    VoltageSensorReading readings[], uint8_t capacity) {
  uint8_t count = 0;
  for (int i = 0; i < _active_sensor_count && count < capacity; i++) {
    const ActiveSensor& sensor = _active_sensors[i];
    if (sensor.query_voltage == NULL) continue;
    VoltageSensorReading& reading = readings[count++];
    reading.channel = (uint8_t)(TELEM_CHANNEL_SELF + 1 + i);
    reading.voltage = 0.0f;
    reading.valid = sensor.query_voltage(sensor.sub_channel,
                                         reading.voltage)
        && isfinite(reading.voltage);
  }
  return count;
}
int EnvironmentSensorManager::getNumSettings() const {
  int settings = 0;
  #if ENV_INCLUDE_GPS
    if (gps_detected) settings++;  // only show GPS setting if GPS is detected
  #endif
  return settings;
}

const char* EnvironmentSensorManager::getSettingName(int i) const {
  int settings = 0;
  #if ENV_INCLUDE_GPS
    if (gps_detected && i == settings++) {
      return "gps";
    }
  #endif
  return NULL;
}

const char* EnvironmentSensorManager::getSettingValue(int i) const {
  int settings = 0;
  #if ENV_INCLUDE_GPS
    if (gps_detected && i == settings++) {
      return isGpsTelemetryUserEnabled() ? "1" : "0";
    }
  #endif
  return NULL;
}

bool EnvironmentSensorManager::setSettingValue(const char* name, const char* value) {
  #if ENV_INCLUDE_GPS
  if (gps_detected && strcmp(name, "gps") == 0) {
    bool enabled = strcmp(value, "0") != 0;
#if defined(RAK_WISBLOCK_GPS) && defined(FORCE_GPS_ALIVE)
    // A UART L76K cannot remove power from the shared 3V3_S rail. "Off" stops
    // parsing and releases the MCU UART, but the still-powered receiver keeps
    // driving its TX pin. It therefore does not make that pin safe for an
    // external UART bridge.
    if (serialGPSFlag) {
      _location->setGPSPowerSaving(false);
      setGpsTelemetryUserEnabled(enabled);
      return true;
    }
#endif
    bool was_active = gps_active;
    _location->setGPSPowerSaving(enabled && powersaving_enabled);
    setGpsTelemetryUserEnabled(enabled);
    if (enabled && powersaving_enabled && was_active) {
      armGpsPowerSavingCycle();
    }
    return true;
  }
  #endif
  return SensorManager::setSettingValue(name, value);
}

void EnvironmentSensorManager::setPowerSavingEnabled(bool enabled) {
  if (powersaving_enabled == enabled) return;
  powersaving_enabled = enabled;

  #if ENV_INCLUDE_GPS
  if (!gps_detected) return;
  bool gps_user_enabled = isGpsTelemetryUserEnabled();
#if defined(RAK_WISBLOCK_GPS) && defined(FORCE_GPS_ALIVE)
  if (serialGPSFlag) {
    _location->setGPSPowerSaving(false);
    if (gps_user_enabled) {
      if (!gps_active) start_gps();
    } else if (gps_active) {
      stop_gps();
    }
    return;
  }
#endif
  _location->setGPSPowerSaving(enabled && gps_user_enabled);
  if (!gps_user_enabled) return;

  if (enabled) {
    if (gps_active) {
      armGpsPowerSavingCycle();
    } else {
      start_gps();
    }
  } else if (!gps_active) {
    // Manual GPS-on means continuous operation when node power saving is off.
    start_gps();
  }
  #endif
}

#if ENV_INCLUDE_GPS
void EnvironmentSensorManager::initBasicGPS() {

  // A repeated discovery pass must not steal a UART which an active bridge
  // already owns. The bridge release path explicitly restores availability.
  if (gps_serial_transport_blocked) {
    MESH_DEBUG_PRINTLN("GPS discovery skipped: serial transport is owned by bridge");
    return;
  }

  resetGpsTelemetryTransportState();
  gps_serial_transport = false;
  gps_serial_transport_blocked = false;

  Serial1.setPins(PIN_GPS_TX, PIN_GPS_RX);

  #ifdef GPS_BAUD_RATE
  Serial1.begin(GPS_BAUD_RATE);
  #else
  Serial1.begin(9600);
  #endif

  // Try to detect if GPS is physically connected to determine if we should expose the setting
  _location->begin();
  _location->reset();

  #ifndef PIN_GPS_EN
    MESH_DEBUG_PRINTLN("No GPS wake/reset pin found for this board. Continuing on...");
  #endif

  // Give GPS a moment to power up and send data
  delay(1000);

  // We'll consider GPS detected if we see any data on Serial1
#ifdef ENV_SKIP_GPS_DETECT
  gps_detected = true;
#else
  gps_detected = (Serial1.available() > 0);
#endif

  if (gps_detected) {
    gps_serial_transport = true;
    MESH_DEBUG_PRINTLN("GPS detected");
    #ifdef PERSISTANT_GPS
      gps_active = true;
      setGpsTelemetryUserEnabled(true);
      return;
    #endif
  } else {
    MESH_DEBUG_PRINTLN("No GPS detected");
  }
  _location->stop();
  gps_active = false; //Set GPS visibility off until setting is changed
}

// gps code for rak might be moved to MicroNMEALoactionProvider
// or make a new location provider ...
#ifdef RAK_WISBLOCK_GPS
void EnvironmentSensorManager::rakGPSInit() {
  // Preserve the established owner across repeated begin() calls. Clearing
  // this flag and starting Serial1 here would silently take UART1 back from an
  // active bridge.
  if (gps_serial_transport_blocked) {
    MESH_DEBUG_PRINTLN("RAK GPS discovery skipped: UART1 is owned by bridge");
    return;
  }

  // A repeated begin() must not retain ownership or a provider from an older
  // hardware probe.
  i2cGPSFlag = false;
  serialGPSFlag = false;
  resetGpsTelemetryTransportState();
  gpsResetPin = static_cast<uint32_t>(-1);
  gps_active = false;
  gps_detected = false;
  gps_serial_transport = false;
  gps_serial_transport_blocked = false;
  _location = _configured_location;

  Serial1.setPins(PIN_GPS_TX, PIN_GPS_RX);

#ifdef GPS_BAUD_RATE
  Serial1.begin(GPS_BAUD_RATE);
#else
  Serial1.begin(9600);
#endif

  // A RAK-derived board with a real GPS-enable pin must use it. Stock WisBlock
  // bases have no dedicated GPS switch, so their fallback is shared WB_IO2.
#if defined(PIN_GPS_EN) && PIN_GPS_EN >= 0
  const uint8_t gps_control_pin = PIN_GPS_EN;
  const bool shared_power_rail = false;
#else
  const uint8_t gps_control_pin = WB_IO2;
  const bool shared_power_rail = true;
#endif

  if (gpsIsAwake(gps_control_pin, shared_power_rail)) {
    _location->setPinEn(gps_control_pin);
  } else {
    MESH_DEBUG_PRINTLN("No GPS found");
    gps_active = false;
    gps_detected = false;
    Serial1.end();
    return;
  }

#ifdef FORCE_GPS_ALIVE
  if (i2cGPSFlag) {
    // The u-blox receiver can sleep while the shared rail remains available.
    stop_gps();
  } else if (serialGPSFlag) {
    // The UART L76K cannot be isolated from the RAK3401 radio power rail.
    setGpsTelemetryUserEnabled(true);
  }
#else
  // Now that GPS is found and set up, set it to sleep for the initial state.
  stop_gps();
#endif
}

bool EnvironmentSensorManager::gpsIsAwake(uint8_t ioPin,
                                          bool shared_power_rail) {
  if (!shared_power_rail) {
    setRakGpsControl(ioPin, false, false);
    delay(500);
  }
  setRakGpsControl(ioPin, shared_power_rail, true);
  // give the receiver time to become responsive
  delay(500);

  // Prove the UART device with a complete checksum-valid GPS sentence. This
  // must precede I2C probing so a UART GPS can coexist with INA3221 at 0x42.
  if (serialHasValidGpsSentence(Serial1, RAK_UART_GPS_PROBE_TIMEOUT_MS)) {
    MESH_DEBUG_PRINTLN("RAK12501 UART GPS identified with pin %i", ioPin);
    gpsResetPin = ioPin;
    serialGPSFlag = true;
    gps_serial_transport = true;
    gps_active = true;
    gps_detected = true;
    return true;
  }

  if (!ensureI2cBusReleased(&Wire)) {
    MESH_DEBUG_PRINTLN(
        "Skipping RAK12500 probe: I2C pins unavailable or bus remains low");
    setRakGpsControl(ioPin, shared_power_rail, false);
    return false;
  }

  const uint32_t configured_gps_address =
      static_cast<uint32_t>(TELEM_RAK12500_ADDRESS);
  if (!mesh::isValidI2cPeripheralAddress(configured_gps_address)) {
    MESH_DEBUG_PRINTLN("Skipping invalid RAK12500 I2C address");
    setRakGpsControl(ioPin, shared_power_rail, false);
    return false;
  }
  const uint8_t gps_i2c_address =
      static_cast<uint8_t>(configured_gps_address);
  bool probe_i2c_gps = true;
#if ENV_INCLUDE_INA3221
  // Inspect the actual device at the GPS address. This must not depend on the
  // configured INA address or telemetry bus: an unstrapped INA3221 can still
  // physically remain at 0x42 when firmware expects it at 0x43.
  const mesh::I2cIdentityProbeResult ina_identity =
      probeIna3221Identity(&Wire, gps_i2c_address);
  probe_i2c_gps = mesh::shouldProbeI2cGps(ina_identity);
  if (!probe_i2c_gps) {
    if (ina_identity == mesh::I2cIdentityProbeResult::Match) {
      MESH_DEBUG_PRINTLN(
          "Skipping RAK12500 probe at I2C address %02X: INA3221 identity confirmed",
          gps_i2c_address);
    } else {
      MESH_DEBUG_PRINTLN(
          "Skipping RAK12500 probe at I2C address %02X: INA identity read was inconclusive",
          gps_i2c_address);
    }
  }
#endif

  // After two successful non-INA manufacturer reads, use the u-blox-specific
  // probe. No software can make two physical devices at one address safe.
  if (probe_i2c_gps
      && ublox_GNSS.begin(Wire, gps_i2c_address) == true) {
    MESH_DEBUG_PRINTLN("RAK12500 GPS init correctly with pin %i", ioPin);
    ublox_GNSS.setI2COutput(COM_TYPE_UBX);
    ublox_GNSS.enableGNSS(true, SFE_UBLOX_GNSS_ID_GPS);
    ublox_GNSS.enableGNSS(true, SFE_UBLOX_GNSS_ID_GALILEO);
    ublox_GNSS.enableGNSS(true, SFE_UBLOX_GNSS_ID_GLONASS);
    ublox_GNSS.enableGNSS(true, SFE_UBLOX_GNSS_ID_SBAS);
    ublox_GNSS.enableGNSS(true, SFE_UBLOX_GNSS_ID_BEIDOU);
    ublox_GNSS.enableGNSS(true, SFE_UBLOX_GNSS_ID_IMES);
    ublox_GNSS.enableGNSS(true, SFE_UBLOX_GNSS_ID_QZSS);
    ublox_GNSS.setMeasurementRate(1000);
    ublox_GNSS.saveConfigSelective(VAL_CFG_SUBSEC_IOPORT);
    gpsResetPin = ioPin;
    i2cGPSFlag = true;
    gps_serial_transport = false;
    gps_active = true;
    gps_detected = true;

    RAK12500_provider.setRTCClock(_location->getRTCClock());
    RAK12500_provider.reset();
    _location = &RAK12500_provider;
    return true;
  }

  setRakGpsControl(ioPin, shared_power_rail, false);
  MESH_DEBUG_PRINTLN("GPS did not init with this IO pin... try the next");
  return false;
}
#endif

void EnvironmentSensorManager::armGpsPowerSavingCycle() {
  if (!powersaving_enabled || !_location->getGPSPowerSaving()) return;
  _location->syncTime();
  _location->setNextGPSOn(0);
  _location->setNextSleep();
}

void EnvironmentSensorManager::start_gps() {
  if (gps_active || gps_serial_transport_blocked) return;
  if (gps_serial_transport) {
    Serial1.setPins(PIN_GPS_TX, PIN_GPS_RX);
#ifdef GPS_BAUD_RATE
    Serial1.begin(GPS_BAUD_RATE);
#else
    Serial1.begin(9600);
#endif
  }
  gps_active = true;
#ifdef RAK_WISBLOCK_GPS
#ifdef FORCE_GPS_ALIVE
  // The UART L76K has no power-save command. Keep the shared rail enabled;
  // an I2C u-blox receiver can also be explicitly returned to full power.
  if (i2cGPSFlag) ublox_GNSS.powerSaveMode(false);
#else
  setRakGpsControl(gpsResetPin, false, true);
#endif
#else
  _location->begin();
  _location->reset();
#endif

#ifndef PIN_GPS_EN
  MESH_DEBUG_PRINTLN("Start GPS is N/A on this board. Actual GPS state unchanged");
#endif

  armGpsPowerSavingCycle();
}

void EnvironmentSensorManager::stop_gps() {
  if (!gps_active) return;
  gps_active = false;

  if (powersaving_enabled && _location->getGPSPowerSaving()) {
    _location->stopTimeSync();
    _location->setNextGPSOff(0);
    _location->setNextWake();
  }

#ifdef RAK_WISBLOCK_GPS
#ifdef FORCE_GPS_ALIVE
  // Keep the shared rail alive for the RAK3401 radio FEM. The I2C u-blox can
  // still enter its internal power-save mode. Ending Serial1 stops the MCU
  // parser, but a UART L76K remains powered and continues driving its TX pin.
  if (i2cGPSFlag) {
    ublox_GNSS.powerSaveMode(true);
  } else if (serialGPSFlag) {
    Serial1.end();
  }
#else
  setRakGpsControl(gpsResetPin, false, false);
#endif
#else
  _location->stop();
#endif

#ifndef PIN_GPS_EN
  MESH_DEBUG_PRINTLN("Stop GPS is N/A on this board. Actual GPS state unchanged");
#endif
}
#endif // ENV_INCLUDE_GPS

bool EnvironmentSensorManager::gpsUsesSerialUart(uint8_t uart) const {
#if ENV_INCLUDE_GPS
  return uart == 1 && gps_detected && gps_serial_transport;
#else
  (void)uart;
  return false;
#endif
}

bool EnvironmentSensorManager::gpsSerialTransportMayConflict(
    uint8_t uart) const {
#if ENV_INCLUDE_GPS
#if defined(RAK_WISBLOCK_GPS) \
    && defined(WITH_RS232_BRIDGE_GPS_CONFLICT_UART)
  // Silence is not proof of physical absence: a cold RAK12501/L76K can start
  // emitting NMEA after the bounded boot probe. Reserve the declared shared
  // connector in the merged image. Exact legacy Serial1 bridge targets omit
  // GPS support and this reservation; the merged image retains UART2.
  if (uart == WITH_RS232_BRIDGE_GPS_CONFLICT_UART) return true;
#endif
  return gpsUsesSerialUart(uart);
#else
  (void)uart;
  return false;
#endif
}

bool EnvironmentSensorManager::gpsSerialTransportCanYield(uint8_t uart) const {
#if ENV_INCLUDE_GPS
  if (!gpsUsesSerialUart(uart)) return false;
#if defined(RAK_WISBLOCK_GPS) && defined(FORCE_GPS_ALIVE)
  // RAK12501 exposes no independently controlled standby/power pin. WB_IO2 is
  // the shared 3V3_S rail and must remain high, so its L76K keeps transmitting
  // NMEA after Serial1.end(). Two TX drivers on UART1 would electrically
  // contend; require UART2 or physical removal/isolation of this GPS instead.
  return false;
#elif defined(RAK_WISBLOCK_GPS)
  // This profile is allowed to remove power with its validated GPS control.
  return true;
#endif
  // Generic NMEA receivers may share the same serial API but are safe to hand
  // off only when the provider has a real enable pin that stop() deasserts.
  return _location != NULL && _location->getPinEn() >= 0;
#else
  (void)uart;
  return false;
#endif
}

bool EnvironmentSensorManager::setGpsSerialTransportBlocked(uint8_t uart,
                                                             bool blocked) {
#if ENV_INCLUDE_GPS
  if (!gpsUsesSerialUart(uart)) return false;
  if (gps_serial_transport_blocked == blocked) return true;
  if (blocked && !gpsSerialTransportCanYield(uart)) return false;

  if (blocked) {
    // Cancel remote-query acquisition/hold state before releasing the UART.
    setGpsTelemetryTransportAvailable(false);
    gps_serial_transport_blocked = true;
    if (gps_active) stop_gps();
    Serial1.end();
  } else {
    gps_serial_transport_blocked = false;
    // Restoring availability restarts the receiver only when the user's GPS
    // preference is on. Otherwise a future authorized location query may
    // acquire it on demand.
    setGpsTelemetryTransportAvailable(true);
  }
  return true;
#else
  (void)uart;
  (void)blocked;
  return false;
#endif
}

#if ENV_INCLUDE_GPS || defined(ENV_INCLUDE_BME680_BSEC)
void EnvironmentSensorManager::loop() {

  #if ENV_INCLUDE_GPS
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
    if (gps_active) {
    #ifdef RAK_WISBLOCK_GPS
    if ((i2cGPSFlag || serialGPSFlag) && _location->isValid()) {
      float gps_lat = ((float)_location->getLatitude()) / 1000000.0f;
      float gps_lon = ((float)_location->getLongitude()) / 1000000.0f;
      float gps_altitude = ((float)_location->getAltitude()) / 1000.0f;
      node_lat = gps_lat;
      node_lon = gps_lon;
      MESH_DEBUG_PRINTLN("lat %f lon %f", node_lat, node_lon);
      node_altitude = gps_altitude;
      MESH_DEBUG_PRINTLN("lat %f lon %f alt %f", node_lat, node_lon, node_altitude);
      processGpsTelemetryFix(gps_lat, gps_lon, gps_altitude, now);
    }
    #else
    if (_location->isValid()) {
      float gps_lat = ((float)_location->getLatitude()) / 1000000.0f;
      float gps_lon = ((float)_location->getLongitude()) / 1000000.0f;
      float gps_altitude = ((float)_location->getAltitude()) / 1000.0f;
      node_lat = gps_lat;
      node_lon = gps_lon;
      MESH_DEBUG_PRINTLN("lat %f lon %f", node_lat, node_lon);
      node_altitude = gps_altitude;
      MESH_DEBUG_PRINTLN("lat %f lon %f alt %f", node_lat, node_lon, node_altitude);
      processGpsTelemetryFix(gps_lat, gps_lon, gps_altitude, now);
    }
    #endif

    }
    next_gps_update = now + getGpsUpdateIntervalMillis();
  }
  #endif
  #if ENV_INCLUDE_BME680_BSEC
  if (bsec_active && bsec_iaq.run()) {
    uint8_t prev_accuracy = bsec_accuracy;
    bsec_temperature  = bsec_iaq.temperature;
    bsec_humidity     = bsec_iaq.humidity;
    bsec_pressure_hpa = bsec_iaq.pressure / 100.0f;
    bsec_iaq_val      = bsec_iaq.iaq;
    bsec_accuracy     = bsec_iaq.iaqAccuracy;
    bsec_data_ready   = true;

    if (bsec_accuracy == 3) {
      if (!bsec_first_save_done) {
        bsec_save_state();
        bsec_last_save_ms = millis();
        bsec_first_save_done = true;
      } else if ((millis() - bsec_last_save_ms) >= BSEC_SAVE_INTERVAL_MS) {
        bsec_save_state();
        bsec_last_save_ms = millis();
      }
    }
  }
  #endif  // ENV_INCLUDE_BME680_BSEC
}
#endif // ENV_INCLUDE_GPS || ENV_INCLUDE_BME680_BSEC
