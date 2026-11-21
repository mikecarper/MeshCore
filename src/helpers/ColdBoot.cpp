#include "ColdBoot.h"

#ifdef ARDUINO
#if defined(ESP32)
#include <esp_system.h>
#elif defined(NRF52_PLATFORM)
#include "Nrf52RegulatorPlatform.h"
#elif defined(RP2040_PLATFORM)
#include <hardware/watchdog.h>
#if PICO_RP2350
#include <hardware/structs/powman.h>
#else
#include <hardware/structs/vreg_and_chip_reset.h>
#endif
#elif defined(STM32_PLATFORM)
#include <stm32_def.h>
#endif

namespace mesh {

bool wasColdBoot() {
#if defined(ESP32)
  const auto reason = esp_reset_reason();
  return reason == ESP_RST_POWERON || reason == ESP_RST_BROWNOUT;
#elif defined(NRF52_PLATFORM)
  return power::platformResetKind() == power::ResetKind::Cold;
#elif defined(RP2040_PLATFORM)
  // A watchdog reboot can leave the last chip-level power-on flag set.
  if (watchdog_caused_reboot()) return false;
#if PICO_RP2350
  return (powman_hw->chip_reset & (POWMAN_CHIP_RESET_HAD_POR_BITS | POWMAN_CHIP_RESET_HAD_BOR_BITS)) != 0;
#else
  return (vreg_and_chip_reset_hw->chip_reset & VREG_AND_CHIP_RESET_CHIP_RESET_HAD_POR_BITS) != 0;
#endif
#elif defined(STM32_PLATFORM)
  // STM32WL reset flags accumulate until acknowledged. Save the result once
  // so a software or watchdog reset cannot inherit the previous power-on.
  static const bool cold = [] {
    const uint32_t flags = RCC->CSR;
    __HAL_RCC_CLEAR_RESET_FLAGS();
    const uint32_t warm = RCC_CSR_SFTRSTF | RCC_CSR_IWDGRSTF | RCC_CSR_WWDGRSTF
        | RCC_CSR_LPWRRSTF | RCC_CSR_OBLRSTF;
    return (flags & RCC_CSR_BORRSTF) && !(flags & warm) && !__HAL_PWR_GET_FLAG(PWR_FLAG_SB);
  }();
  return cold;
#else
  return false;
#endif
}

} // namespace mesh
#endif
