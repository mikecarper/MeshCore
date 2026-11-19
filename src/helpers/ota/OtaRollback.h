#pragma once

namespace mesh {
namespace ota {

#if defined(NRF52_PLATFORM) && defined(ENABLE_OTA) && !defined(OTA_SEEDER_ONLY) && \
    (defined(OTA_SD_STORE) || defined(OTA_QSPI_STORE))
bool ota_nrf52_trial_pending();
void ota_nrf52_confirm_trial();
#else
inline bool ota_nrf52_trial_pending() { return false; }
inline void ota_nrf52_confirm_trial() {}
#endif

} // namespace ota
} // namespace mesh
