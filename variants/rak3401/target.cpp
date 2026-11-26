#include <Arduino.h>
#include "target.h"
#include "OnboardRadioSleep.h"
#include "CadRxPowerSaving.h"
#include <helpers/ArduinoHelpers.h>
#include <helpers/radiolib/Nrf52BufferedRadioHal.h>
#include <helpers/radiolib/NrfRadioSpiStartupDiagHal.h>
#include <nrf_gpio.h>
#ifdef USE_CC310_HW_CRYPTO
  #include <helpers/NRF52Crypto.h>
#endif

#ifndef MC_RAK3401_SPI_HZ
#define MC_RAK3401_SPI_HZ 8000000
#endif
#if MC_RAK3401_SPI_HZ > 8000000
#if MC_RAK3401_SPI_HZ != 16000000 || !MESH_HIL_SPI_STARTUP_DIAG \
    || !defined(MESH_HIL_PROFILE_TIMING) || MESH_HIL_PROFILE_TIMING != 1 \
    || !defined(SPI_32MHZ_INTERFACE) || SPI_32MHZ_INTERFACE != 1 \
    || !defined(NRFX_SPIM3_NRF52840_ANOMALY_198_WORKAROUND_ENABLED) \
    || NRFX_SPIM3_NRF52840_ANOMALY_198_WORKAROUND_ENABLED != 1
#error "RAK3401 SPI above 8 MHz requires the explicit 16 MHz startup diagnostic and nrfx workaround"
#endif
#elif MC_RAK3401_SPI_HZ != 2000000 && MC_RAK3401_SPI_HZ != 4000000 \
    && MC_RAK3401_SPI_HZ != 8000000
#error "RAK3401 radio SPI clock must be 2, 4 or 8 MHz"
#endif

#include <helpers/radiolib/NrfRadioSpiReadProbe.h>

namespace {

// This is the optional private radio bus, NOT the external RAK13302's SPI1.
// It is only probed during board startup, before any other bus owner starts.
class OnboardRadioIO {
public:
  void begin() {
    digitalWrite(ONBOARD_LORA_NSS, HIGH);
    pinMode(ONBOARD_LORA_NSS, OUTPUT);
    digitalWrite(ONBOARD_LORA_RESET, HIGH);
    pinMode(ONBOARD_LORA_RESET, OUTPUT);
    digitalWrite(ONBOARD_LORA_ANT_SW, LOW);
    pinMode(ONBOARD_LORA_ANT_SW, OUTPUT);
    pinMode(ONBOARD_LORA_BUSY, INPUT_PULLUP);
    SPI.setPins(ONBOARD_LORA_MISO, ONBOARD_LORA_SCLK, ONBOARD_LORA_MOSI);
    SPI.begin();
    // Reject a floating MISO rather than mistaking it for a version string.
    pinMode(ONBOARD_LORA_MISO, INPUT_PULLDOWN);
  }

  void reset() {
    digitalWrite(ONBOARD_LORA_RESET, LOW);
    delay(1);
    digitalWrite(ONBOARD_LORA_RESET, HIGH);
    delay(5);
  }

  bool waitBusy(bool high, uint32_t timeout) {
    const uint32_t started = millis();
    while ((digitalRead(ONBOARD_LORA_BUSY) != LOW) != high) {
      if (millis() - started >= timeout) return false;
      delay(1);
    }
    return true;
  }

  void readVersion(char* version) {
    SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(ONBOARD_LORA_NSS, LOW);
    SPI.transfer(RADIOLIB_SX126X_CMD_READ_REGISTER);
    SPI.transfer(RADIOLIB_SX126X_REG_VERSION_STRING >> 8);
    SPI.transfer(RADIOLIB_SX126X_REG_VERSION_STRING & 0xff);
    SPI.transfer(0);
    for (unsigned i = 0; i < 6; ++i) version[i] = SPI.transfer(0);
    digitalWrite(ONBOARD_LORA_NSS, HIGH);
    SPI.endTransaction();
  }

  void releaseProbePulls() {
    pinMode(ONBOARD_LORA_BUSY, INPUT);
    pinMode(ONBOARD_LORA_MISO, INPUT);
  }

  void sleep() {
    SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(ONBOARD_LORA_NSS, LOW);
    SPI.transfer(RADIOLIB_SX126X_CMD_SET_SLEEP);
    SPI.transfer(RADIOLIB_SX126X_SLEEP_START_COLD | RADIOLIB_SX126X_SLEEP_RTC_OFF);
    digitalWrite(ONBOARD_LORA_NSS, HIGH);
    SPI.endTransaction();
    delay(1);
  }

  void end() {
    SPI.end();
    nrf_gpio_cfg_default(g_ADigitalPinMap[ONBOARD_LORA_SCLK]);
    nrf_gpio_cfg_default(g_ADigitalPinMap[ONBOARD_LORA_MOSI]);
    nrf_gpio_cfg_default(g_ADigitalPinMap[ONBOARD_LORA_MISO]);
    nrf_gpio_cfg_default(g_ADigitalPinMap[ONBOARD_LORA_BUSY]);
    // Retain NSS/reset HIGH and RF switch LOW. Releasing NSS can wake the
    // sleeping SX1262; only its clock/data/BUSY pads should be disconnected.
    digitalWrite(ONBOARD_LORA_NSS, HIGH);
  }
};

static_assert(ONBOARD_LORA_MISO == PIN_SPI_MISO &&
              ONBOARD_LORA_MOSI == PIN_SPI_MOSI &&
              ONBOARD_LORA_SCLK == PIN_SPI_SCK,
              "The optional radio probe must leave SPI on its default pins");

}  // namespace

rak3401::OnboardRadioState rak3401::sleepOnboardRadio() {
  OnboardRadioIO onboard;
#if MESH_DEBUG
  const uint32_t started = millis();
#endif
  // RadioLib identifies the SX1262 by the same six-byte SX1261 prefix.
  const auto state = rak3401::sleepOnboardRadio(onboard, RADIOLIB_SX1262_CHIP_TYPE);
#if MESH_DEBUG
  const char* outcome = state == OnboardRadioState::Absent ? "absent" :
                        state == OnboardRadioState::Asleep ? "asleep" :
                        state == OnboardRadioState::BusyTimeout ? "busy-timeout" : "sleep-failed";
  MESH_DEBUG_PRINTLN("onboard SX1262: %s elapsed=%lu ms", outcome,
                    static_cast<unsigned long>(millis() - started));
#endif
  return state;
}

RAK3401Board board;

#ifndef PIN_USER_BTN
  #define PIN_USER_BTN (-1)
#endif

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true, true);

  #if defined(PIN_USER_BTN_ANA)
  MomentaryButton analog_btn(PIN_USER_BTN_ANA, 1000, 20);
  #endif
#endif

// The RAK13302 is wired to the second WisBlock SPI controller. Keep the same
// controller instance for startup and watchdog recovery; resetting the radio
// must not replace or tear down the SPI object used by its Module.
// Batch each RadioLib command into one SPIM transfer rather than starting a
// transfer per byte. Keep 8 MHz by default on SPI1/SPIM2. The explicit HIL-only
// 16 MHz startup diagnostic refuses frames unless SoftDevice is disabled;
// this is not a production SPIM3/SoftDevice mitigation.
// Changing the speed never replaces this bus or its recovery/handoff path.
#if MESH_HIL_SPI_STARTUP_DIAG
static NrfRadioSpiStartupDiagHal radioHal(SPI1, MC_RAK3401_SPI_HZ);
#elif MESH_RAK3401_CAD_RXPS
class Rak3401RadioHal : public Nrf52BufferedRadioHal {
public:
  using Nrf52BufferedRadioHal::Nrf52BufferedRadioHal;
  void spiBeginTransaction() override {
    rak3401::cadRxps::stop();
    Nrf52BufferedRadioHal::spiBeginTransaction();
  }
};
static Rak3401RadioHal radioHal(SPI1, MC_RAK3401_SPI_HZ);
#else
static Nrf52BufferedRadioHal radioHal(SPI1, MC_RAK3401_SPI_HZ);
#endif
RADIO_CLASS radio = new Module(&radioHal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);

WRAPPER_CLASS radio_driver(radio, board);

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

#if ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
  MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
  EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);
#else
  EnvironmentSensorManager sensors;
#endif

static bool target_radio_available = false;

#if MESH_HIL_SPI_READ_PROBE
static bool finishSpiReadProbe() {
  if (mesh::spireadprobe::mesh_hil_spi_read_probe.runs) return true;
  mesh::spireadprobe::run(radioHal, *radio.getMod(), mesh::spistartup::mesh_hil_spi_startup);
  if (mesh::spireadprobe::trailingBaselineHealthy()) return true;
  // Preserve the probe evidence, but never hand normal firmware an unknown
  // radio state. The restored 8 MHz setting and existing reset/init path
  // rebuild it once. The caller re-enables the frontend after success.
  return board.recoverRadio() && radio.std_init(&SPI1);
}
#endif

bool radio_init() {
  rtc_clock.begin(Wire);
  // A successful physical reset proves that the external SX1262 can release
  // BUSY before RadioLib is allowed to issue SPI commands. This also prevents
  // a missing RAK13302 from turning an initialization retry into a watchdog
  // reset loop.
  if (!board.recoverRadio()) {
    target_radio_available = false;
    mesh::usbLoggingPort().println(
        "ERROR: RAK13302 BUSY stayed high after reset; check module seating and power");
    return false;
  }

  if (radio.std_init(&SPI1)
#if MESH_HIL_SPI_READ_PROBE
      && finishSpiReadProbe()
#endif
      ) {
    target_radio_available = true;
    board.enableRadioFrontend();
    return true;
  }

  // Retry once after another physical reset. Keep the shared GPS/sensor rail
  // and the SPI controller instance intact throughout recovery.
  if (!board.recoverRadio()) {
    target_radio_available = false;
    return false;
  }
  if (!radio.std_init(&SPI1)
#if MESH_HIL_SPI_READ_PROBE
      || !finishSpiReadProbe()
#endif
      ) {
    target_radio_available = false;
    return false;
  }
  target_radio_available = true;
  board.enableRadioFrontend();
  return true;
}

uint32_t radio_fallback_rng_seed() {
  uint32_t seed = micros() ^ NRF_FICR->DEVICEID[0] ^ NRF_FICR->DEVICEID[1];
#ifdef USE_CC310_HW_CRYPTO
  mesh::mixCC310Random(reinterpret_cast<uint8_t*>(&seed), sizeof(seed));
#endif
  return seed;
}

mesh::LocalIdentity radio_new_identity() {
  if (target_radio_available) {
    RadioNoiseListener rng(radio);
    return mesh::LocalIdentity(&rng);
  }

  class RAK3401IdentityRNG : public mesh::RNG {
  public:
    void random(uint8_t* dest, size_t sz) override {
      for (size_t i = 0; i < sz; ++i) {
        dest[i] = static_cast<uint8_t>(::random(0, 256));
      }
#ifdef USE_CC310_HW_CRYPTO
      mesh::mixCC310Random(dest, sz);
#endif
    }
  } rng;
  return mesh::LocalIdentity(&rng);
}
