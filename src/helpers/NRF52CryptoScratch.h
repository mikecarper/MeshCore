#pragma once

#include "NRF52Crypto.h"

#if defined(USE_CC310_HW_CRYPTO)

#include "nrf_cc310/include/crys_ec_edw_api.h"
#include "nrf_cc310/include/crys_hash.h"
#include "nrf_cc310/include/ssi_aes.h"

namespace mesh {

// Select one word-aligned workspace while the session owns the CC310.
// Selecting another kind invalidates the previous one. Each selection
// initializes its complete SDK type; unavailable sessions return nullptr.
CRYS_ECEDW_TempBuff_t* cc310VerificationScratch(const CC310CryptoSession& session);
SaSiAesUserContext_t* cc310AesScratch(const CC310CryptoSession& session);
CRYS_HASH_Result_t* cc310HmacScratch(const CC310CryptoSession& session);

} // namespace mesh

#endif
