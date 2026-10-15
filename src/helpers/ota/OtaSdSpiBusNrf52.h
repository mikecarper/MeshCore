#pragma once

#include <SPI.h>

#if defined(OTA_RAK15002_SD_CACHE)
  #if !defined(NRF52_PLATFORM) || !defined(RAK_4631) || !defined(OTA_SD_STORE) || !defined(OTA_SEEDER_ONLY)
    #error "RAK15002 SD cache requires the exact nRF52 RAK4631 seeder-only profile"
  #endif
  #if defined(ETHERNET_ENABLED) || defined(OTA_QSPI_STORE) || defined(OTA_RAK_AUTO_STORE) || defined(OTA_TOWER_AUTO_STORE)
    #error "RAK15002 SD cache cannot share the WisBlock SPI/CS with Ethernet or OTA NOR"
  #endif
  #if SPI_INTERFACES_COUNT != 1 || (defined(SPI_32MHZ_INTERFACE) && SPI_32MHZ_INTERFACE != 0)
    #error "RAK15002 SD cache requires radio SPI on SPIM3 and reserves SPIM2 for the SD card"
  #endif
  #if defined(OTA_SD_BOOTLOADER_UPDATE) || defined(OTA_INTERNAL_BOOTLOADER_UPDATE) || defined(OTA_QSPI_BOOTLOADER_UPDATE)
    #error "RAK15002 cache profile has no qualified bootloader-update handoff"
  #endif
  #ifndef OTA_SD_CS_PIN
    #define OTA_SD_CS_PIN 26
  #endif
  static_assert(OTA_SD_CS_PIN == 26 && PIN_SPI_MISO == 29 && PIN_SPI_SCK == 3 && PIN_SPI_MOSI == 30,
                "RAK15002 exact WisBlock SPI pin map changed; requalify the profile");
#elif !defined(OTA_SD_CS_PIN)
  #define OTA_SD_CS_PIN PIN_SPI1_NSS
#endif

namespace mesh { namespace ota {

inline SPIClass& ota_sd_spi_bus() {
#if defined(OTA_RAK15002_SD_CACHE)
  // The RAK radio reassigns global SPI/SPIM3 to its private pins. Never use
  // that object for the external WisBlock bus, or reconfigure it on SD mount.
  static SPIClass sd_spi(NRF_SPIM2, 29, 3, 30);
  return sd_spi;
#else
  return SPI1;                         // unchanged MeshTower SD bus
#endif
}

inline void ota_sd_prepare_chip_select() {
  digitalWrite(OTA_SD_CS_PIN, HIGH);   // preload latch before enabling output
  pinMode(OTA_SD_CS_PIN, OUTPUT);
}

} } // namespace mesh::ota
