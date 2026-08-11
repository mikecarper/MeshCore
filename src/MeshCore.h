#pragma once

#include <stdint.h>
#include <stddef.h>
#include <math.h>

#define MAX_HASH_SIZE        8
#define PUB_KEY_SIZE        32
#define PRV_KEY_SIZE        64
#define SEED_SIZE           32
#define SIGNATURE_SIZE      64
#define MAX_ADVERT_DATA_SIZE  32
#define CIPHER_KEY_SIZE     16
#define CIPHER_BLOCK_SIZE   16

// V1
#define CIPHER_MAC_SIZE      2
#define PATH_HASH_SIZE       1

#define MAX_PACKET_PAYLOAD  184
#define MAX_GROUP_DATA_LENGTH  (MAX_PACKET_PAYLOAD - CIPHER_BLOCK_SIZE - 3)
#define MAX_PATH_SIZE        64
#define MAX_TRANS_UNIT      255

#if MESH_DEBUG && ARDUINO
  #include <Arduino.h>
  #define MESH_DEBUG_PRINT(F, ...) do { if (Serial.availableForWrite() > 0) { Serial.printf("DEBUG: " F, ##__VA_ARGS__); } } while(0)
  #define MESH_DEBUG_PRINTLN(F, ...) do { if (Serial.availableForWrite() > 0) { Serial.printf("DEBUG: " F "\n", ##__VA_ARGS__); } } while(0)
#else
  #define MESH_DEBUG_PRINT(...) {}
  #define MESH_DEBUG_PRINTLN(...) {}
#endif

#if BRIDGE_DEBUG && ARDUINO
#define BRIDGE_DEBUG_PRINTLN(F, ...) do { if (Serial.availableForWrite() > 0) { Serial.printf("%s BRIDGE: " F, getLogDateTime(), ##__VA_ARGS__); } } while(0)
#else
#define BRIDGE_DEBUG_PRINTLN(...) {}
#endif

#if POWERSAVING_DEBUG && ARDUINO
  #include <Arduino.h>
  #define POWERSAVING_DEBUG_PRINT(F, ...) Serial.printf("POWERSAVING: " F, ##__VA_ARGS__)
  #define POWERSAVING_DEBUG_PRINTLN(F, ...) Serial.printf("POWERSAVING: " F "\n", ##__VA_ARGS__)
#else
  #define POWERSAVING_DEBUG_PRINT(...) {}
  #define POWERSAVING_DEBUG_PRINTLN(...) {}
#endif

namespace mesh {

#define  BD_STARTUP_NORMAL     0  // getStartupReason() codes
#define  BD_STARTUP_RX_PACKET  1

class MainBoard {
public:
  virtual uint16_t getBattMilliVolts() = 0;
  virtual float getMCUTemperature() { return NAN; }
  virtual bool setAdcMultiplier(float multiplier) { return false; };
  virtual float getAdcMultiplier() const { return 0.0f; }
  virtual const char* getManufacturerName() const = 0;
  virtual void onBeforeTransmit() { }
  virtual void onAfterTransmit() { }
  virtual void reboot() = 0;
  virtual void powerOff() { /* no op */ }
  // Reload an already-running system watchdog without enabling one. Long,
  // internally bounded operations can use this while retaining their own
  // timeout. Boards without an explicit watchdog need no implementation.
  virtual void serviceWatchdog() { /* no op */ }
  // Called by example setup() functions to signal that boot is complete.
  // Boards may override to stop a boot-indicator LED sequence or similar.
  // Default no-op: boards that don't care need not implement anything.
  virtual void onBootComplete() { /* no op */ }
  virtual uint32_t getIRQGpio() { return -1; } // not supported. Returns DIO1 (SX1262) and DIO0 (SX127x)
  virtual void sleep(uint32_t secs)  { /* no op */ }
  virtual uint32_t getGpio() { return 0; }
  virtual void setGpio(uint32_t values) {}
  // Returns true only for physical MCU GPIOs that are safe for the user to
  // control in this build. Board implementations must reject pins already
  // claimed by firmware or internal hardware.
  virtual bool isUserGpioAvailable(uint8_t pin) const { return false; }
  virtual uint8_t getStartupReason() const = 0;
  virtual bool getBootloaderVersion(char* version, size_t max_len) { return false; }
  virtual bool startOTAUpdate(const char* id, char reply[], bool force_ap = false) { return false; }   // not supported
  virtual bool stopOTAUpdate(char reply[]) { return false; }   // not supported
  virtual bool isOTAUpdateRunning() const { return false; }
  // Pull-based OTA: fetch the firmware build for this variant from a baked-in manifest and flash it.
  // current_ver is the running firmware version string (used to skip if already up to date); when
  // dry_run is true the build is only reported, not flashed. Observer (ESP32+WiFi) builds only.
  virtual bool otaFromManifest(const char* current_ver, bool dry_run, char reply[]) { return false; }

  // LoRa front-end-module LNA (RX gain) control. Only FEM-equipped boards override
  // these; others report they can't control it. Driven by NodePrefs.radio_fem_rxgain.
  virtual bool setLoRaFemLnaEnabled(bool enable) { return false; }
  virtual bool canControlLoRaFemLna() const { return false; }
  virtual bool isLoRaFemLnaEnabled() const { return false; }
  // Software-selectable external FEM transmit gain. This is not a PA power switch.
  virtual bool setLoRaFemPaGainEnabled(bool enable) { return false; }
  virtual bool canControlLoRaFemPaGain() const { return false; }
  virtual bool isLoRaFemPaGainEnabled() const { return false; }
#if defined(ENABLE_OTA)
  // 4-byte build-target discriminator for OTA-over-LoRa (docs/ota_protocol.md Section 9). Default is the
  // MOTA_TARGET_ID build flag injected by build.sh; 0 when unset (e.g. a bare IDE build).
  virtual uint32_t getOtaTargetId() const {
  #ifdef MOTA_TARGET_ID
    return (uint32_t)(MOTA_TARGET_ID);
  #else
    return 0;
  #endif
  }
  // Human-readable hardware tag (<=32 ASCII chars, e.g. "RAK4631") naming the hardware this firmware can
  // boot on. Same tag == bootable-compatible; the OTA applier refuses a `.mota` whose hw_id differs (brick-
  // safety). Defined per-variant via the MOTA_HW_ID build flag; "" when unset (then the check is skipped).
  virtual const char* getOtaHwId() const {
  #ifdef MOTA_HW_ID
    return MOTA_HW_ID;
  #else
    return "";
  #endif
  }
#endif

  // Power management interface (boards with power management override these)
  virtual bool isExternalPowered() { return false; }
  virtual bool isUsbDataConnected() { return false; }
  // True when the device is enumerated by a USB host, even if its serial port
  // is not open. Defaults to the stricter data-connection signal on boards
  // that cannot distinguish a computer from USB power.
  virtual bool isUsbHostConnected() { return isUsbDataConnected(); }
  virtual uint16_t getBootVoltage() { return 0; }
  virtual uint32_t getResetReason() const { return 0; }
  virtual const char* getResetReasonString(uint32_t reason) { return "Not available"; }
  virtual uint8_t getShutdownReason() const { return 0; }
  virtual const char* getShutdownReasonString(uint8_t reason) { return "Not available"; }
  virtual bool isPowerManagementInitialized() const { return false; }
  virtual bool supportsVoltageWake() const { return false; }

  inline static uint32_t n_cad_busy = 0;
};

/**
 * An abstraction of the device's Realtime Clock.
*/
class RTCClock {
  uint32_t last_unique;
protected:
  RTCClock() { last_unique = 0; }

public:
  /**
   * \returns  the current time. in UNIX epoch seconds.
  */
  virtual uint32_t getCurrentTime() = 0;

  /**
   * \param time  current time in UNIX epoch seconds.
  */
  virtual void setCurrentTime(uint32_t time) = 0;

  /**
   * override in classes that need to periodically update internal state
   */
  virtual void tick() { /* no op */}

  uint32_t getCurrentTimeUnique() {
    uint32_t t = getCurrentTime();
    if (t <= last_unique) {
      return ++last_unique;
    }
    return last_unique = t;
  }

  /** Reset the monotonic timestamp helper after an intentional RTC correction.
   *  This is needed when a caller explicitly permits moving the wall clock
   *  backward and wants subsequent generated timestamps to use the new clock. */
  void resetUniqueTime(uint32_t time) {
    last_unique = time > 0 ? time - 1 : 0;
  }
};

}
