#include <Arduino.h>
#include <target.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/IdentityGeneration.h>
#include <helpers/IdentityStore.h>
#include <helpers/UsbLogging.h>
#include "KissModem.h"

#if defined(ESP32_PLATFORM)
  #include <helpers/ESP32TrueRandom.h>
#endif

#if defined(NRF52_PLATFORM)
  #include <InternalFileSystem.h>
  #include <helpers/nrf52/InternalPrimaryFsBoot.h>
  #include <helpers/nrf52/RamFallbackFileSystem.h>
#elif defined(RP2040_PLATFORM)
  #include <LittleFS.h>
#elif defined(ESP32)
  #include <SPIFFS.h>
#else
  #include <InternalFileSystem.h>
#endif

#if defined(KISS_UART_RX) && defined(KISS_UART_TX)
  #include <HardwareSerial.h>
#endif

#define NOISE_FLOOR_CALIB_INTERVAL_MS 2000
#define AGC_RESET_INTERVAL_MS 30000

// Lab-only boot diagnostics must never contaminate a native-USB or dedicated
// UART KISS stream. Ordinary builds compile all stage calls away.
#if defined(ESP32) && defined(MESH_HIL_KISS_BOOT_TRACE) && MESH_HIL_KISS_BOOT_TRACE \
    && (!defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT) \
    && !(defined(KISS_UART_RX) && defined(KISS_UART_TX))
  #define KISS_UART_BOOT_TRACE 1
  #define KISS_BOOT_STAGE(stage) do { Serial.print("KISS_BOOT "); Serial.println(stage); } while (0)
#else
  #define KISS_UART_BOOT_TRACE 0
  #define KISS_BOOT_STAGE(stage) ((void)0)
#endif

#if MESH_ESP32_HWCDC_SESSION_GUARD && !(defined(KISS_UART_RX) && defined(KISS_UART_TX))
  #define KISS_HWCDC_TRANSPORT 1
#else
  #define KISS_HWCDC_TRANSPORT 0
#endif

StdRNG rng;
mesh::LocalIdentity identity;
KissModem* modem;
static uint32_t next_noise_floor_calib_ms = 0;
static uint32_t next_agc_reset_ms = 0;
#if KISS_HWCDC_TRANSPORT
static bool usb_host_reset_pending = false;
#endif

void halt() {
  while (1) ;
}

void loadOrCreateIdentity() {
#if defined(NRF52_PLATFORM)
  bool volatile_primary_fs = false;
  mesh::storage::RamFallbackFileSystem* ram_primary_fs = nullptr;
  FILESYSTEM* identity_fs = nullptr;
  const auto primary_fs_boot =
      mesh::storage::beginInternalPrimaryFilesystemSafely(InternalFS);
  if (mesh::storage::internalPrimaryFilesystemReady(primary_fs_boot)) {
    identity_fs = &InternalFS;
  } else {
    ram_primary_fs = mesh::storage::createRamFallbackFileSystem();
    if (ram_primary_fs == nullptr) {
      MESH_DEBUG_PRINTLN("InternalFS and RAM fallback initialization failed; rebooting");
      board.reboot();
      halt();
    }
    identity_fs = &ram_primary_fs->filesystem();
    volatile_primary_fs = true;
  }
  IdentityStore store(*identity_fs, "");
#elif defined(STM32_PLATFORM)
  InternalFS.begin();
  IdentityStore store(InternalFS, "");
#elif defined(ESP32)
  IdentityStore store(SPIFFS, "/identity");
#elif defined(RP2040_PLATFORM)
  LittleFS.begin();
  IdentityStore store(LittleFS, "/identity");
  store.begin();
#else
  #error "Filesystem not defined"
#endif

#if defined(NRF52_PLATFORM)
  IdentityLoadResult identity_load = volatile_primary_fs
      ? store.loadResult("_main", identity)
      : mesh::storage::loadIdentityWithPrimaryRecovery(
            InternalFS,
            [&store]() { return store.loadResult("_main", identity); });
  if (identity_load == IdentityLoadResult::Unreadable) {
    ram_primary_fs = mesh::storage::createRamFallbackFileSystem();
    if (ram_primary_fs != nullptr) {
      identity_fs = &ram_primary_fs->filesystem();
      store.useFileSystem(*identity_fs);
      volatile_primary_fs = true;
      identity_load = IdentityLoadResult::Missing;
    }
  }
#else
  KISS_BOOT_STAGE("identity.load.before");
  const IdentityLoadResult identity_load = store.loadResult("_main", identity);
  KISS_BOOT_STAGE("identity.load.after");
#endif
  const bool needs_identity = identity_load == IdentityLoadResult::Missing
      || (identity_load == IdentityLoadResult::Loaded
          && mesh::hasReservedIdentityPrefix(identity));
  bool identity_ready = identity_load != IdentityLoadResult::Unreadable;
  if (needs_identity) {
    KISS_BOOT_STAGE("identity.generate.before");
    identity_ready = mesh::generateUsableLocalIdentity(identity, radio_new_identity);
    KISS_BOOT_STAGE("identity.generate.after");
    KISS_BOOT_STAGE("identity.save.before");
    if (identity_ready) identity_ready = store.saveWithRetry("_main", identity);
    KISS_BOOT_STAGE("identity.save.after");
  }

#if defined(ESP32_PLATFORM)
  mesh::discardESP32TrueRandom();
#endif
  if (!identity_ready) {
    KISS_BOOT_STAGE("identity.failed");
    MESH_DEBUG_PRINTLN("Identity generation or persistence failed after retries; rebooting");
    board.reboot();
    halt(); // Never let setup continue if a platform's reboot returns.
  }
#if defined(NRF52_PLATFORM)
  if (volatile_primary_fs) {
    MESH_DEBUG_PRINTLN("Node: %s (volatile RAM storage)",
                       mesh::storage::BAD_FILESYSTEM_NODE_NAME);
  }
#endif
}

void onSetRadio(float freq, float bw, uint8_t sf, uint8_t cr) {
  radio_driver.setParams(freq, bw, sf, cr);
}

void onSetTxPower(uint8_t power) {
  radio_driver.setTxPower(power);
}

float onGetCurrentRssi() {
  return radio_driver.getCurrentRSSI();
}

void onGetStats(uint32_t* rx, uint32_t* tx, uint32_t* errors) {
  *rx = radio_driver.getPacketsRecv();
  *tx = radio_driver.getPacketsSent();
  *errors = radio_driver.getPacketsRecvErrors();
}

void setup() {
#if KISS_UART_BOOT_TRACE
  Serial.begin(115200);
  KISS_BOOT_STAGE("setup.enter");
#endif
#if KISS_HWCDC_TRANSPORT
  // KISS owns the entire serial byte stream, including gaps between chunks.
  mesh::setUsbLoggingEnabled(false);
  mesh::setUsbDebugEnabled(false);
#endif
  KISS_BOOT_STAGE("board.before");
  board.begin();
  KISS_BOOT_STAGE("board.after");

#if defined(ESP32)
  // Mount (and, on a virgin store, format) before radio interrupts start,
  // matching the Companion startup order. Never generate a replacement
  // identity when primary storage has failed to become available.
  KISS_BOOT_STAGE("filesystem.before");
  const bool filesystem_ready = SPIFFS.begin(true);
  KISS_BOOT_STAGE("filesystem.after");
  if (!filesystem_ready) {
    KISS_BOOT_STAGE("filesystem.failed");
    MESH_DEBUG_PRINTLN("SPIFFS initialization failed; rebooting");
    board.reboot();
    halt();
  }
#endif

  KISS_BOOT_STAGE("radio.before");
  int radioinit_attempts = 0;
  while (!radio_init()) {
    ++radioinit_attempts;
    MESH_DEBUG_PRINTLN("Radio init failed! (attempt %d)", radioinit_attempts);
    if (radioinit_attempts >= 3) {
      MESH_DEBUG_PRINTLN("Radio init failed 3x - rebooting");
      board.reboot();
    }
    delay(500);
  }
  KISS_BOOT_STAGE("radio.after");

  KISS_BOOT_STAGE("driver.before");
  radio_driver.begin();
  KISS_BOOT_STAGE("driver.after");

  KISS_BOOT_STAGE("rng.before");
  rng.begin(radio_driver.getRngSeed());
  KISS_BOOT_STAGE("rng.after");
  KISS_BOOT_STAGE("identity.before");
  loadOrCreateIdentity();
  KISS_BOOT_STAGE("identity.after");

  KISS_BOOT_STAGE("sensors.before");
  sensors.begin();
  KISS_BOOT_STAGE("sensors.after");

  KISS_BOOT_STAGE("serial.before");
#if defined(KISS_UART_RX) && defined(KISS_UART_TX)
#if defined(ESP32)
  Serial1.setPins(KISS_UART_RX, KISS_UART_TX);
  Serial1.begin(115200);
#elif defined(NRF52_PLATFORM)
  ((Uart *)&Serial1)->setPins(KISS_UART_RX, KISS_UART_TX);
  Serial1.begin(115200);
#elif defined(RP2040_PLATFORM)
  ((SerialUART *)&Serial1)->setRX(KISS_UART_RX);
  ((SerialUART *)&Serial1)->setTX(KISS_UART_TX);
  Serial1.begin(115200);
#elif defined(STM32_PLATFORM)
  ((HardwareSerial *)&Serial1)->setRx(KISS_UART_RX);
  ((HardwareSerial *)&Serial1)->setTx(KISS_UART_TX);
  Serial1.begin(115200);
#else
  #error "KISS UART not supported on this platform"
#endif
  modem = new KissModem(Serial1, identity, rng, radio_driver, board, sensors);
#else
#if KISS_HWCDC_TRANSPORT
  // Allocate the USB queues before begin() enables the live driver ISR.
  mesh::prepareUsbLoggingPort();
#endif
  Serial.begin(115200);
#if KISS_HWCDC_TRANSPORT
  mesh::beginUsbLoggingPort();
#endif
  uint32_t start = millis();
  while (!Serial && millis() - start < 3000) delay(10);
  delay(100);
#if KISS_HWCDC_TRANSPORT
  modem = new KissModem(mesh::usbCompanionPort(), identity, rng, radio_driver, board, sensors);
#else
  modem = new KissModem(Serial, identity, rng, radio_driver, board, sensors);
#endif
#endif

  KISS_BOOT_STAGE("serial.after");
  modem->setRadioCallback(onSetRadio);
  modem->setTxPowerCallback(onSetTxPower);
  modem->setGetCurrentRssiCallback(onGetCurrentRssi);
  modem->setGetStatsCallback(onGetStats);
  KISS_BOOT_STAGE("modem.before");
  modem->begin();
  KISS_BOOT_STAGE("modem.after");

  KISS_BOOT_STAGE("boot_complete.before");
  board.onBootComplete();
  KISS_BOOT_STAGE("setup.complete");
}

void loop() {
#if KISS_HWCDC_TRANSPORT
  mesh::serviceUsbLoggingPort();
  if (mesh::takeUsbTerminalSessionReset()) {
    modem->resetHostSession();
    usb_host_reset_pending = true;
  }
  if (usb_host_reset_pending && mesh::tryCompleteUsbTerminalSessionReset()) {
    usb_host_reset_pending = false;
  }
  // The guarded stream blocks host I/O during cleanup. Continue the modem
  // loop so an in-flight RF transmission can finish while USB is quarantined.
#endif
#if defined(NRF52_PLATFORM)
  board.feedWatchdog();
#endif
  modem->loop();
  // GPS acquisition, cache refresh, and delayed shutdown are driven by the
  // sensor manager loop even though KISS itself is normally host powered.
  sensors.loop();
#ifdef TBEAM_1W
  board.updateFanControl();
#endif

  if (!modem->isActuallyTransmitting() && !modem->isHostOutputBackedUp()) {
    if (!modem->isTxBusy()) {
      if ((uint32_t)(millis() - next_agc_reset_ms) >= AGC_RESET_INTERVAL_MS) {
        radio_driver.resetAGC();
        next_agc_reset_ms = millis();
      }
    }

    uint8_t rx_buf[256];
    int rx_len = radio_driver.recvRaw(rx_buf, sizeof(rx_buf));
    if (rx_len > 0) {
      // Snapshot before the receive-complete hook or fast profile scanner can
      // retune the one physical radio.  KISS port 0/1 reports this origin.
      const uint8_t profile = radio_driver.receiveProfile();
      int8_t snr = (int8_t)(radio_driver.getLastSNR() * 4);
      int8_t rssi = (int8_t)radio_driver.getLastRSSI();
      modem->onPacketReceived(snr, rssi, rx_buf, rx_len, profile);
      radio_driver.onReceiveProcessed();
    }
  }

  board.loop();

  if ((uint32_t)(millis() - next_noise_floor_calib_ms) >= NOISE_FLOOR_CALIB_INTERVAL_MS) {
    radio_driver.triggerNoiseFloorCalibrate(0);
    next_noise_floor_calib_ms = millis();
  }
  radio_driver.loop();
}
