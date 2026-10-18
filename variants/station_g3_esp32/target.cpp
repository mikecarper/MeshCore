#include <Arduino.h>
#include "target.h"
#include "StationG3BootRecovery.h"
#include <helpers/UsbLogging.h>

// The normal Station G3 roles retain the established RadioLib Arduino HAL.
// The KISS modem can opt into buffered SPI for its on-board dual-profile RX
// scanner without changing the regular Station firmware.
#if defined(MC_SX1262_BUFFERED_RADIO_HAL) && MC_SX1262_BUFFERED_RADIO_HAL
  #include <helpers/radiolib/ESP32BufferedRadioHal.h>
#endif

StationG3Board board;

// Release qualification must prove the selector is in the linked application,
// not merely infer it from a board/environment name.
extern const char meshcore_capability_station_g3_auto_slots[];
const char meshcore_capability_station_g3_auto_slots[] __attribute__((used)) =
    "meshcore.capability.station_g3.auto_slots.v1";

#if defined(P_LORA_SCLK)
  static SPIClass spi;
  #if defined(MC_SX1262_BUFFERED_RADIO_HAL) && MC_SX1262_BUFFERED_RADIO_HAL
    static ESP32BufferedRadioHal radio_hal(spi);
    RADIO_CLASS radio = new Module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
  #else
    RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, spi);
  #endif
#else
  RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
#endif

WRAPPER_CLASS radio_driver(radio, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

#if ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
  MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
  EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);
#else
  EnvironmentSensorManager sensors;
#endif

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
#endif

namespace {
constexpr uint32_t kProbeTimeoutMs = 1000;

bool waitForProbeReady(const station_g3::RadioPins& pins, uint32_t started) {
  while (digitalRead(pins.busy)) {
    if (static_cast<uint32_t>(millis() - started) >= kProbeTimeoutMs) return false;
#if MESH_USB_CONSOLE_COOPERATIVE
    mesh::serviceUsbLoggingPort();
    mesh::serviceUsbTerminalPort();
#endif
    delay(1);
  }
  return static_cast<uint32_t>(millis() - started) < kProbeTimeoutMs;
}

// Identification only: no SetTx, CAD, packet send, or saved profile changes.
// Use a small bounded transaction rather than RadioLib's 10 reset attempts
// (each may wait a second) on every empty socket.
bool probeRadioSlot(uint8_t slot) {
  const uint32_t started = millis();
  const auto& pins = station_g3::pinsForSlot(slot);
  spi.end();
  station_g3::slotState().candidate = slot;
  digitalWrite(pins.nss, HIGH);
  pinMode(pins.nss, OUTPUT);
  digitalWrite(pins.lna, HIGH);
  pinMode(pins.lna, OUTPUT);
  pinMode(pins.irq, INPUT);
  pinMode(pins.busy, INPUT_PULLDOWN);
  digitalWrite(pins.reset, HIGH);
  pinMode(pins.reset, OUTPUT);
  digitalWrite(pins.reset, LOW);
  delay(1);
  digitalWrite(pins.reset, HIGH);
  delay(10);
  spi.begin(pins.sck, pins.miso, pins.mosi);
  bool found = false;
  // Match RadioLib's reset/RC-standby tolerance: some radios ignore their
  // first commands after reset. Share one budget across BUSY and all retries,
  // so an empty socket remains bounded without resetting a slow chip again.
  while (!found) {
    if (!waitForProbeReady(pins, started)) break;
    spi.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins.nss, LOW);
    spi.transfer(RADIOLIB_SX126X_CMD_SET_STANDBY);
    spi.transfer(RADIOLIB_SX126X_STANDBY_RC);
    digitalWrite(pins.nss, HIGH);
    spi.endTransaction();
    // SX126x BUSY can assert just after NSS rises (RadioLib uses the same guard).
    delayMicroseconds(1);
    if (!waitForProbeReady(pins, started)) break;
    char version[16] = {};
    spi.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(pins.nss, LOW);
    spi.transfer(RADIOLIB_SX126X_CMD_READ_REGISTER);
    spi.transfer(RADIOLIB_SX126X_REG_VERSION_STRING >> 8);
    spi.transfer(RADIOLIB_SX126X_REG_VERSION_STRING & 0xff);
    spi.transfer(0); // status/dummy byte before register data
    for (unsigned i = 0; i < sizeof(version); ++i) version[i] = spi.transfer(0);
    digitalWrite(pins.nss, HIGH);
    spi.endTransaction();
    // SX1262 intentionally uses RadioLib's SX1261 silicon version signature.
    found = memcmp(version, RADIOLIB_SX1262_CHIP_TYPE, 6) == 0;
    if (!found) {
#if MESH_USB_CONSOLE_COOPERATIVE
      mesh::serviceUsbLoggingPort();
      mesh::serviceUsbTerminalPort();
#endif
      const uint32_t elapsed = static_cast<uint32_t>(millis() - started);
      if (elapsed >= kProbeTimeoutMs) break;
      const uint32_t remaining = kProbeTimeoutMs - elapsed;
      delay(remaining < 10 ? remaining : 10);
    }
  }
  // Leave the unselected socket in reset, not receiving or driving an IRQ.
  // The chosen socket's std_init will perform its own verified reset.
  digitalWrite(pins.reset, LOW);
  spi.end();
  return found;
}

bool initializeSlot(uint8_t slot) {
  spi.end();
  station_g3::slotState().candidate = slot;
  const auto& pins = station_g3::activePins();
  Module* module = radio.getMod();
  // The pinned RadioLib build already opts into GODMODE. Keep one stable
  // Module/radio/wrapper address, and bind only before std_init/ISR startup.
  module->csPin = pins.nss;
  module->irqPin = pins.irq;
  module->rstPin = pins.reset;
  module->gpioPin = pins.busy;
  spi.begin(pins.sck, pins.miso, pins.mosi);
  if (!radio.std_init(&spi)) {
    digitalWrite(pins.reset, LOW);
    spi.end();
    return false;
  }
  station_g3::slotState().active = slot;
  station_g3::rememberActiveSlot(slot);
  board.loRaFEMControl.setRxModeEnable();
  return true;
}
} // namespace

bool radio_init() {
  volatile const char* capability = meshcore_capability_station_g3_auto_slots;
  (void)*capability;
  auto& state = station_g3::slotState();
  // Runtime recovery must never change physical sockets under live wrapper
  // state/interrupts. Only the first boot discovery is allowed to select one.
  if (state.active != 0) return radio.std_init(&spi);

  fallback_clock.begin();
  rtc_clock.begin(Wire);
  bool firstNotice = true;
  for (;;) {
    state.detected = 0;
    for (uint8_t slot = 1; slot <= 2; ++slot)
      if (probeRadioSlot(slot)) state.detected |= 1U << (slot - 1);
    const uint8_t preferred = state.choose(state.detected);
    if (preferred && initializeSlot(preferred)) return true;
    // Auto may recover using another detected, functional socket. Explicit
    // overrides never silently fall back to an unintended antenna/PA.
    if (state.bootMode == 0 && preferred && state.detected == 3 &&
        initializeSlot(preferred == 1 ? 2 : 1)) return true;

    // No radio is not permission to generate a new identity, transmit, or
    // reboot-loop before the operator can correct a saved hardware setting.
    const uint32_t started = millis();
    do {
#if MESH_USB_CONSOLE_COOPERATIVE
      mesh::serviceUsbLoggingPort();
      mesh::serviceUsbTerminalPort();
#endif
      station_g3::serviceBootRecovery(board, firstNotice);
      firstNotice = false;
      delay(2);
    } while (static_cast<uint32_t>(millis() - started) < 10000);
  }
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);
}
