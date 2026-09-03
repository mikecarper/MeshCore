#include "OtaDeflate.h"

#if defined(ENABLE_OTA) || defined(OTA_TRANSPORT_DEFLATE_TEST)

extern "C" {
#include "tinf/tinf.h"
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

} // namespace ota
} // namespace mesh

#endif // ENABLE_OTA || OTA_TRANSPORT_DEFLATE_TEST
