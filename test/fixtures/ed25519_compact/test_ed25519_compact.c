#include <stdio.h>
#include <string.h>

#include "ed_25519.h"

typedef struct {
    const char *seed;
    const char *public_key;
    const char *message;
    const char *signature;
} rfc8032_vector;

static int hex_nibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int decode_hex(unsigned char *out, size_t out_len, const char *hex) {
    size_t i;

    if (strlen(hex) != out_len * 2) return 0;
    for (i = 0; i < out_len; ++i) {
        const int high = hex_nibble(hex[i * 2]);
        const int low = hex_nibble(hex[i * 2 + 1]);
        if (high < 0 || low < 0) return 0;
        out[i] = (unsigned char)((high << 4) | low);
    }
    return 1;
}

static int run_vector(const rfc8032_vector *vector, int number) {
    unsigned char seed[32];
    unsigned char expected_public_key[32];
    unsigned char expected_signature[64];
    unsigned char public_key[32];
    unsigned char private_key[64];
    unsigned char signature[64];
    unsigned char message[256];
    const size_t message_len = strlen(vector->message) / 2;

    if (message_len > sizeof(message) ||
        !decode_hex(seed, sizeof(seed), vector->seed) ||
        !decode_hex(expected_public_key, sizeof(expected_public_key), vector->public_key) ||
        !decode_hex(message, message_len, vector->message) ||
        !decode_hex(expected_signature, sizeof(expected_signature), vector->signature)) {
        fprintf(stderr, "RFC 8032 vector %d is malformed\n", number);
        return 0;
    }

    ed25519_create_keypair(public_key, private_key, seed);
    if (memcmp(public_key, expected_public_key, sizeof(public_key)) != 0) {
        fprintf(stderr, "RFC 8032 vector %d public key mismatch\n", number);
        return 0;
    }

    ed25519_sign(signature, message, message_len, public_key, private_key);
    if (memcmp(signature, expected_signature, sizeof(signature)) != 0) {
        fprintf(stderr, "RFC 8032 vector %d signature mismatch\n", number);
        return 0;
    }
    if (!ed25519_verify(signature, message, message_len, public_key)) {
        fprintf(stderr, "RFC 8032 vector %d signature did not verify\n", number);
        return 0;
    }

    signature[17] ^= 0x01;
    if (ed25519_verify(signature, message, message_len, public_key)) {
        fprintf(stderr, "RFC 8032 vector %d accepted a damaged signature\n", number);
        return 0;
    }
    return 1;
}

int main(void) {
    static const rfc8032_vector vectors[] = {
        {
            "9d61b19deffd5a60ba844af492ec2cc4"
            "4449c5697b326919703bac031cae7f60",
            "d75a980182b10ab7d54bfed3c964073a"
            "0ee172f3daa62325af021a68f707511a",
            "",
            "e5564300c360ac729086e2cc806e828a"
            "84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46b"
            "d25bf5f0595bbe24655141438e7a100b"
        },
        {
            "4ccd089b28ff96da9db6c346ec114e0f"
            "5b8a319f35aba624da8cf6ed4fb8a6fb",
            "3d4017c3e843895a92b70aa74d1b7ebc"
            "9c982ccf2ec4968cc0cd55f12af4660c",
            "72",
            "92a009a9f0d4cab8720e820b5f642540"
            "a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c"
            "387b2eaeb4302aeeb00d291612bb0c00"
        }
    };
    size_t i;

    for (i = 0; i < sizeof(vectors) / sizeof(vectors[0]); ++i) {
        if (!run_vector(&vectors[i], (int)i + 1)) return 1;
    }
    return 0;
}
