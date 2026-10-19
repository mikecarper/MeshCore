#include "RegulatorPlatform.h"

#if defined(ESP32)
#include <esp_system.h>
#include <esp_attr.h>
#include <soc/soc_caps.h>
#if SOC_RTC_SLOW_MEM_SUPPORTED || SOC_RTC_FAST_MEM_SUPPORTED
#define MESH_REGULATOR_RTC 1
static RTC_NOINIT_ATTR volatile mesh::power::RecoveryRecord regulator_record;
#else
static volatile mesh::power::RecoveryRecord regulator_record;
#endif
namespace mesh { namespace power {
volatile RecoveryRecord& platformRecoveryRecord() { return regulator_record; }
bool platformRecoverySupported() {
#ifdef MESH_REGULATOR_RTC
  return true;
#else
  return false;
#endif
}
ResetKind platformResetKind() {
  switch (esp_reset_reason()) {
    case ESP_RST_PANIC:
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT: return ResetKind::Crash;
    case ESP_RST_DEEPSLEEP: return ResetKind::Wake;
    case ESP_RST_SW: return ResetKind::Intentional;
    case ESP_RST_POWERON:
    case ESP_RST_EXT: return ResetKind::Cold;
    // A supply failure is not evidence that the DC/DC regulator is faulty.
    default: return ResetKind::Unknown;
  }
}
}}
#elif defined(RP2040_PLATFORM)
#include <hardware/watchdog.h>
// Arduino-Pico's linker explicitly keeps this outside BSS and above the
// boot-ROM's scratch area. No watchdog scratch registers are commandeered.
static volatile mesh::power::RecoveryRecord regulator_record
    __attribute__((section(".uninitialized_data.regulator_recovery"), used));
namespace mesh { namespace power {
volatile RecoveryRecord& platformRecoveryRecord() { return regulator_record; }
bool platformRecoverySupported() { return true; }
ResetKind platformResetKind() {
  if (watchdog_enable_caused_reboot()) return ResetKind::Crash;
  return watchdog_caused_reboot() ? ResetKind::Intentional : ResetKind::Cold;
}
}}
#elif !defined(NRF52_PLATFORM)
namespace mesh { namespace power {
static volatile RecoveryRecord regulator_record;
volatile RecoveryRecord& platformRecoveryRecord() { return regulator_record; }
bool platformRecoverySupported() { return false; }
ResetKind platformResetKind() { return ResetKind::Unknown; }
}}
#endif
