#include <Identity.h>
#include <openssl/evp.h>

// Real Ed25519 behind the firmware Identity API for host OTA-policy fixtures.
namespace mesh {
unsigned signature_checks = 0;
bool Identity::verify(const uint8_t* signature, const uint8_t* message, int length) const {
  ++signature_checks;
  if (!signature || !message || length < 0) return false;
  EVP_PKEY* public_key = EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519, nullptr,
                                                  pub_key, sizeof(pub_key));
  EVP_MD_CTX* context = EVP_MD_CTX_new();
  const bool valid = public_key && context &&
      EVP_DigestVerifyInit(context, nullptr, nullptr, nullptr, public_key) == 1 &&
      EVP_DigestVerify(context, signature, SIGNATURE_SIZE, message, length) == 1;
  EVP_MD_CTX_free(context); EVP_PKEY_free(public_key);
  return valid;
}
}
