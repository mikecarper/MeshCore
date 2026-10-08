#include "OtaDeflate.h"
#if defined(RAK4631_COMBINED_ETHERNET)
  #include "../nrf52/Rak4631SharedSpi.h"
#endif

#if defined(ENABLE_OTA) || defined(OTA_TRANSPORT_DEFLATE_TEST)

extern "C" {
#include "tinf/tinf.h"
#if MESHCORE_OTA_DEVICE_DEFLATE
#include "tinydeflate/deflate.h"
#endif
}

namespace mesh {
namespace ota {

bool ota_transport_inflate(void* context, const uint8_t* src, uint16_t src_len,
                           uint8_t* dst, uint16_t dst_cap, uint16_t* dst_len) {
  (void)context;
  if (dst_len) *dst_len = 0;
  if (!src || src_len == 0 || !dst || dst_cap == 0 || !dst_len) return false;

  unsigned int produced = dst_cap;
  const int result = tinf_uncompress_exact(dst, &produced, src, src_len);
  if (result != TINF_OK || produced != dst_cap) return false;
  *dst_len = (uint16_t)produced;
  return true;
}

#if MESHCORE_OTA_DEVICE_DEFLATE
bool ota_transport_deflate(void* context, const uint8_t* src, uint16_t src_len,
                           uint8_t* dst, uint16_t dst_cap, uint16_t* dst_len) {
  (void)context;
  if (dst_len) *dst_len = 0;
#if defined(RAK4631_COMBINED_ETHERNET)
  // The Ethernet worker and encoder workspace share the combined image's
  // runtime RAM allowance. Refuse before tinydeflate can allocate its table.
  if (rak4631_ethernet_owns_spi()) return false;
#endif
  if (!src || src_len < 3 || src_len > MESHCORE_OTA_DEFLATE_BLOCK_MAX ||
      !dst || !dst_cap || !dst_len) return false;
  // Proof/output scratch is disjoint from the manager's raw input. Reject
  // accidental aliases without overflowing a pointer-range endpoint.
  const uintptr_t source = reinterpret_cast<uintptr_t>(src);
  const uintptr_t output = reinterpret_cast<uintptr_t>(dst);
  if ((output >= source && output - source < src_len) ||
      (source > output && source - output < dst_cap)) return false;
  // Abort as soon as the stream cannot save a byte. Never need a worst-case
  // expanded-output buffer, and leave the original raw input intact on failure.
  const uint16_t limit = dst_cap < src_len ? dst_cap : (uint16_t)(src_len - 1);
  const uint16_t produced = meshcore_deflate_raw(src, src_len, dst, limit);
  if (!produced || produced >= src_len) return false;
  *dst_len = produced;
  return true;
}
#endif

} // namespace ota
} // namespace mesh

#endif // ENABLE_OTA || OTA_TRANSPORT_DEFLATE_TEST
