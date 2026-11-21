#include <stdbool.h>
#include <stdint.h>

#ifdef RAK_FINDMY_HAS_PLAN
extern const uint8_t fmna_pp_product_data[8];
extern const uint8_t fmna_pp_server_encryption_key[65];
extern const uint8_t fmna_pp_server_sig_verification_key[65];
#endif

bool tag_has_product_plan(void)
{
#ifdef RAK_FINDMY_HAS_PLAN
    uint8_t data = 0;
    for (unsigned i = 0; i < sizeof(fmna_pp_product_data); ++i)
        data |= fmna_pp_product_data[i];
    return data != 0 && fmna_pp_server_encryption_key[0] == 4
        && fmna_pp_server_sig_verification_key[0] == 4;
#else
    return false;
#endif
}
