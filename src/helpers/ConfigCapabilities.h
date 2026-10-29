#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace configcap {

// config.cap v1 bit assignments are a wire contract. Do not reorder or reuse
// bits; unknown/reserved bits stay zero. This is a bounded subset, not a claim
// that every stored preference can be exported or restored.
enum Key : uint8_t {
  SystemWatchdog = 0,       // system.watchdog
  RadioCad = 1,             // radio.cad (scan/retry/max timing preferences)
  RadioFemRxGain = 2,       // radio.fem.rxgain
  RadioFemTxGain = 3,       // radio.fem.txgain
  RadioRxpsRfRxDisabled = 4,  // radio.rxps.rfrx_disabled
  Bluetooth = 5,            // bluetooth
  BluetoothName = 6,        // bluetooth.name
  DisplayRotation = 7,      // display.rotation
  UsbLoggingAlways = 8,     // usb.logging.always
  UsbLogging = 9,           // usb.logging
  UsbDebug = 10,            // usb.debug
  RadioPreamble = 11,       // radio.preamble (saved primary-radio preference)
  KeyCount = 12
};

struct Manifest {
  uint32_t get = 0;
  uint32_t set = 0;
  uint32_t fixed = 0;
  uint32_t na = 0;

  void readWrite(Key key) {
    get |= uint32_t(1) << key;
    set |= uint32_t(1) << key;
  }
  void notApplicable(Key key) { na |= uint32_t(1) << key; }
  void fixedValue(Key key, bool readable = false) {
    fixed |= uint32_t(1) << key;
    if (readable) get |= uint32_t(1) << key;
  }
  void configurable(Key key, bool supported) {
    if (supported) readWrite(key);
    else notApplicable(key);
  }
};

// Inputs describe actual parser/board support, not whether a setting happens
// to be enabled, a management client is connected, or a runtime radio is busy.
struct Features {
  bool watchdog_read = false;
  bool watchdog_write = false;
  bool cad_timings = false;
  bool fem_rxgain = false;
  bool fem_txgain = false;
  bool fem_txgain_fixed = false;
  bool rxps_rfrx_disabled = false;
  bool bluetooth = false;
  bool bluetooth_name = false;
  bool display_rotation = false;
  bool usb_logging = false;
  bool usb_logging_always = false;
};

inline Manifest describe(const Features& features) {
  Manifest result;
  if (features.watchdog_read) {
    if (features.watchdog_write) result.readWrite(SystemWatchdog);
    else result.fixedValue(SystemWatchdog, true);
  } else {
    result.notApplicable(SystemWatchdog);
  }
  // Infrastructure CAD has an enable gate, but no saved scan/retry/max
  // timing preferences. Do not fabricate restoreable timing values for it.
  if (features.cad_timings) result.readWrite(RadioCad);
  else result.fixedValue(RadioCad);
  result.configurable(RadioFemRxGain, features.fem_rxgain);
  if (features.fem_txgain) result.readWrite(RadioFemTxGain);
  else if (features.fem_txgain_fixed) result.fixedValue(RadioFemTxGain, true);
  else result.notApplicable(RadioFemTxGain);
  result.configurable(RadioRxpsRfRxDisabled, features.rxps_rfrx_disabled);
  result.configurable(Bluetooth, features.bluetooth);
  result.configurable(BluetoothName, features.bluetooth_name);
  result.configurable(DisplayRotation, features.display_rotation);
  result.configurable(UsbLoggingAlways,
      features.usb_logging && features.usb_logging_always);
  result.configurable(UsbLogging, features.usb_logging);
  result.configurable(UsbDebug, features.usb_logging);
  // Both CLI roles restore this using the existing optional fifth field of
  // set radio <frequency>,<bandwidth>,<sf>,<cr>,<preamble|auto>.
  result.readWrite(RadioPreamble);
  return result;
}

inline bool isCommand(const char* command) {
  return command != nullptr && strcmp(command, "get config.cap") == 0;
}

inline bool handleCommand(const char* command, char* reply, size_t capacity,
                          const Features& features) {
  if (!isCommand(command) || reply == nullptr || capacity == 0) return false;
  const Manifest result = describe(features);
  snprintf(reply, capacity,
      "config.cap v1 get=%08lX set=%08lX fixed=%08lX na=%08lX",
      (unsigned long)result.get, (unsigned long)result.set,
      (unsigned long)result.fixed, (unsigned long)result.na);
  return true;
}

// Read saved intent, not a temporary profile or a computed dwell recommendation.
inline void formatPrimaryPreamble(uint16_t preamble, char* reply,
                                  size_t capacity) {
  if (reply == nullptr || capacity == 0) return;
  if (preamble == 0) snprintf(reply, capacity, "> auto");
  else snprintf(reply, capacity, "> %u", (unsigned)preamble);
}

} }  // namespace mesh::configcap
