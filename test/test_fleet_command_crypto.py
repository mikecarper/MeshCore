#!/usr/bin/env python3
"""Exercise the production fleet envelope with real Ed25519, never native mocks."""

import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
NOW = 1791500000
CHANNEL = bytes(range(1, 17))
DOMAIN = b"MeshCoreFleet1"
PUBLISHER = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
PUBLIC = PUBLISHER.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)
OTHER = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
OTHER_PUBLIC = OTHER.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)


FIXTURE = r'''
#include <helpers/FleetCommand.h>
#include <openssl/evp.h>
#include <array>
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

// The production Identity interface is retained. OpenSSL implements its
// signatures here so these tests cannot inherit native's always-true mock.
namespace mesh {
Identity::Identity() { memset(pub_key, 0, sizeof(pub_key)); }
LocalIdentity::LocalIdentity(RNG* rng) {
  memset(prv_key, 0, sizeof(prv_key)); rng->random(prv_key, 32);
  EVP_PKEY* key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, prv_key, 32);
  assert(key); size_t length = sizeof(pub_key);
  assert(EVP_PKEY_get_raw_public_key(key, pub_key, &length) == 1 && length == sizeof(pub_key));
  EVP_PKEY_free(key);
}
void LocalIdentity::sign(uint8_t* signature, const uint8_t* message, int length) const {
  EVP_PKEY* key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, prv_key, 32);
  EVP_MD_CTX* context = EVP_MD_CTX_new(); assert(key && context);
  assert(EVP_DigestSignInit(context, nullptr, nullptr, nullptr, key) == 1);
  size_t size = SIGNATURE_SIZE;
  assert(EVP_DigestSign(context, signature, &size, message, size_t(length)) == 1);
  assert(size == SIGNATURE_SIZE); EVP_MD_CTX_free(context); EVP_PKEY_free(key);
}
bool Identity::verify(const uint8_t* signature, const uint8_t* message, int length) const {
  EVP_PKEY* key = EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519, nullptr, pub_key, sizeof(pub_key));
  EVP_MD_CTX* context = EVP_MD_CTX_new(); assert(key && context);
  assert(EVP_DigestVerifyInit(context, nullptr, nullptr, nullptr, key) == 1);
  const bool valid = EVP_DigestVerify(context, signature, SIGNATURE_SIZE, message, size_t(length)) == 1;
  EVP_MD_CTX_free(context); EVP_PKEY_free(key); return valid;
}
} // namespace mesh

struct FixedRng : mesh::RNG {
  void random(uint8_t* output, size_t size) override {
    for (size_t i = 0; i < size; ++i) output[i] = uint8_t(i + 1);
  }
};

std::vector<uint8_t> unhex(const char* input) {
  const std::string text(input); assert(text.size() % 2 == 0);
  std::vector<uint8_t> bytes(text.size() / 2);
  for (size_t i = 0; i < bytes.size(); ++i)
    bytes[i] = uint8_t(std::stoul(text.substr(i * 2, 2), nullptr, 16));
  return bytes;
}

void hex(const uint8_t* bytes, size_t size) {
  constexpr char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < size; ++i) std::cout << digits[bytes[i] >> 4] << digits[bytes[i] & 15];
  std::cout << '\n';
}

int main(int argc, char** argv) {
  using Fleet = mesh::FleetCommand;
  if (argc == 7 && std::string(argv[1]) == "encode") {
    FixedRng rng; mesh::LocalIdentity publisher(&rng);
    const auto key = unhex(argv[2]); assert(key.size() == Fleet::KeySize);
    std::array<uint8_t, Fleet::TargetSize> target;
    if (!Fleet::parseTarget(argv[3], target.data())) { std::cout << "reject\n"; return 0; }
    std::array<uint8_t, Fleet::MaxPayloadLength> output;
    const size_t size = Fleet::encode(publisher, key.data(), uint32_t(std::stoul(argv[4])),
        uint32_t(std::stoul(argv[5])), target.data(), argv[6], output.data(), output.size());
    if (!size) std::cout << "reject\n"; else hex(output.data(), size);
    return 0;
  }
  if (argc == 7 && std::string(argv[1]) == "encode2") {
    FixedRng rng; mesh::LocalIdentity publisher(&rng);
    const auto key = unhex(argv[2]); assert(key.size() == Fleet::KeySize);
    Fleet::Targets targets;
    if (!Fleet::parseTargets(argv[3], strlen(argv[3]), targets)) { std::cout << "reject\n"; return 0; }
    std::array<uint8_t, Fleet::MaxPayloadLength> output;
    const size_t size = Fleet::encode(publisher, key.data(), uint32_t(std::stoul(argv[4])),
        uint32_t(std::stoul(argv[5])), targets, argv[6], output.data(), output.size());
    if (!size) std::cout << "reject\n"; else hex(output.data(), size);
    return 0;
  }
  if (argc == 7 && (std::string(argv[1]) == "decode" || std::string(argv[1]) == "decode2")) {
    const auto public_key = unhex(argv[2]), key = unhex(argv[3]), self = unhex(argv[4]), bytes = unhex(argv[6]);
    assert(public_key.size() == PUB_KEY_SIZE && self.size() == PUB_KEY_SIZE && key.size() == Fleet::KeySize);
    mesh::Identity publisher(public_key.data()); Fleet::Decoded output;
    if (!Fleet::decode(publisher, key.data(), bytes.data(), bytes.size(),
                       uint32_t(std::stoul(argv[5])), self.data(), output)) {
      assert(output.sequence == 0 && output.expires == 0 && !output.broadcast && output.command[0] == 0);
      std::cout << "reject\n";
    } else {
      std::cout << output.sequence << '\n' << output.expires << '\n' << output.command << '\n';
      if (std::string(argv[1]) == "decode2") std::cout << int(output.broadcast) << '\n';
    }
    return 0;
  }
  return 2;
}
'''


def envelope(command="get radio2", sequence=NOW, expires=NOW + 120,
             target=b"\0" * 16, channel=CHANNEL, publisher=PUBLISHER):
    command = command.encode("ascii") if isinstance(command, str) else command
    unsigned = b"FMC1" + struct.pack("<II", sequence, expires) + target + bytes([len(command)]) + command
    return unsigned + publisher.sign(DOMAIN + channel + unsigned)


def envelope2(entries=(), command="get radio2", sequence=NOW, expires=NOW + 120,
              channel=CHANNEL, publisher=PUBLISHER, count=None):
    command = command.encode("ascii") if isinstance(command, str) else command
    records = b"".join(bytes([kind]) + value for kind, value in entries)
    count = len(entries) if count is None else count
    unsigned = (b"FMC2" + struct.pack("<II", sequence, expires) + bytes([count])
                + records + bytes([len(command)]) + command)
    return unsigned + publisher.sign(DOMAIN + channel + unsigned)


class FleetCommandCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="meshcore-fleet-crypto-")
        cls.addClassCleanup(cls.directory.cleanup)
        path = Path(cls.directory.name)
        source = path / "fleet-crypto.cpp"
        source.write_text(FIXTURE)
        cls.binary = path / "fleet-crypto"
        build = subprocess.run([
            "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-sign-compare",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
            "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
            str(source), str(ROOT / "src/helpers/FleetCommand.cpp"),
            "-lcrypto", "-o", str(cls.binary)], capture_output=True, text=True)
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)

    def run_tool(self, *arguments):
        result = subprocess.run([str(self.binary), *map(str, arguments)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("runtime error:", result.stderr)
        self.assertNotIn("AddressSanitizer", result.stderr)
        return result.stdout.strip()

    def decode(self, payload, channel=CHANNEL, publisher=PUBLIC, self_key=PUBLIC, now=NOW, metadata=False):
        return self.run_tool("decode2" if metadata else "decode",
                             publisher.hex(), channel.hex(), self_key.hex(), now, payload.hex())

    def test_cpp_and_python_signatures_match_exactly(self):
        expected = envelope()
        actual = self.run_tool("encode", CHANNEL.hex(), "all", NOW, NOW + 120, "get radio2")
        self.assertEqual(actual, expected.hex())
        PUBLISHER.public_key().verify(expected[-64:], DOMAIN + CHANNEL + expected[:-64])
        self.assertEqual(self.decode(expected), f"{NOW}\n{NOW + 120}\nget radio2")

    def test_python_signature_accepted_for_complete_hashed_target(self):
        target = hashlib.sha256(PUBLIC).digest()[:16]
        expected = envelope(target=target)
        actual = self.run_tool("encode", CHANNEL.hex(), PUBLIC.hex(), NOW, NOW + 120, "get radio2")
        self.assertEqual(actual, expected.hex())
        self.assertNotEqual(self.decode(expected), "reject")
        self.assertEqual(self.decode(expected, self_key=OTHER_PUBLIC), "reject")

    def test_all_targets_reaches_another_receiver_without_other_private_key(self):
        self.assertNotEqual(self.decode(envelope(), self_key=OTHER_PUBLIC), "reject")

    def test_wrong_publisher_and_forged_publisher_signature_rejected(self):
        self.assertEqual(self.decode(envelope(), publisher=OTHER_PUBLIC), "reject")
        self.assertEqual(self.decode(envelope(publisher=OTHER)), "reject")
        self.assertNotEqual(self.decode(envelope(publisher=OTHER), publisher=OTHER_PUBLIC), "reject")

    def test_signature_is_bound_to_the_encryption_channel(self):
        different = bytes(range(17, 33))
        original = envelope()
        self.assertEqual(self.decode(original, channel=different), "reject")
        self.assertEqual(self.decode(envelope(channel=different)), "reject")
        self.assertNotEqual(self.decode(envelope(channel=different), channel=different), "reject")

    def test_every_signature_byte_is_verified(self):
        original = envelope()
        for offset in range(len(original) - 64, len(original)):
            with self.subTest(signature_byte=offset):
                altered = bytearray(original)
                altered[offset] ^= 1
                self.assertEqual(self.decode(altered), "reject")

    def test_tampered_command_that_remains_allowed_is_rejected(self):
        original = envelope(command="set flood.max 4")
        # Equal-length valid commands exercise cryptographic rejection, rather
        # than relying on the allowlist rejecting the changed text.
        other = envelope(command="set flood.max 5")
        forged = other[:-64] + original[-64:]
        self.assertEqual(self.decode(forged), "reject")

    def test_every_header_field_and_target_is_signed(self):
        for changed in [
                envelope(sequence=NOW + 1)[:-64],
                envelope(expires=NOW + 121)[:-64],
                envelope(target=hashlib.sha256(PUBLIC).digest()[:16])[:-64],
                envelope(command="get tempradio2")[:-64]]:
            with self.subTest(header=changed.hex()):
                self.assertEqual(self.decode(changed + envelope()[-64:]), "reject")

    def test_signed_forbidden_commands_and_multistatement_text_rejected(self):
        for command in ["get prv.key", "setperm 0102 3", "start ota", "reboot",
                        "get radio2\nreboot", "xy|set radio2 off", "get radio2;reboot",
                        "get radio2\0", "get flood.filter.secret", "get radio2.scan"]:
            with self.subTest(command=command):
                self.assertEqual(self.decode(envelope(command)), "reject")

    def test_exact_and_oversized_command_boundaries(self):
        maximum = "set flood.rule.1 " + "a" * (72 - len("set flood.rule.1 "))
        accepted = envelope(maximum)
        self.assertEqual(len(accepted), 165)
        self.assertNotEqual(self.decode(accepted), "reject")
        self.assertEqual(self.decode(envelope(maximum + "a")), "reject")

    def test_valid_signature_does_not_override_clock_policy(self):
        for sequence, expires, now in [
                (NOW, NOW - 1, NOW), (NOW, NOW + 601, NOW),
                (NOW + 61, NOW + 120, NOW), (NOW, NOW + 120, NOW + 121),
                (1735689599, 1735689600, 1735689600), (NOW, NOW + 120, 0)]:
            with self.subTest(sequence=sequence, expires=expires, now=now):
                self.assertEqual(self.decode(envelope(sequence=sequence, expires=expires), now=now), "reject")
        self.assertNotEqual(self.decode(envelope(sequence=NOW + 60, expires=NOW + 120)), "reject")
        self.assertNotEqual(self.decode(envelope(), now=NOW + 120), "reject")

    def test_truncation_extra_padding_and_changed_length_rejected(self):
        original = envelope()
        for length in [0, 1, 28, 29, 92, len(original) - 1]:
            with self.subTest(length=length):
                self.assertEqual(self.decode(original[:length]), "reject")
        self.assertEqual(self.decode(original + b"\0"), "reject")
        altered = bytearray(original)
        altered[28] = 0
        self.assertEqual(self.decode(altered), "reject")

    def test_default_public_key_cannot_form_fleet_privacy_channel(self):
        public_channel = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")
        for channel in [b"\0" * 16, public_channel]:
            with self.subTest(channel=channel.hex()):
                self.assertEqual(self.decode(envelope(channel=channel), channel=channel), "reject")
                self.assertEqual(self.run_tool("encode", channel.hex(), "all", NOW, NOW + 120, "get radio2"), "reject")

    def test_new_sender_preserves_fmc1_for_all_and_single_complete_key(self):
        for token, target in [("all", b"\0" * 16), (PUBLIC.hex(), hashlib.sha256(PUBLIC).digest()[:16])]:
            with self.subTest(token=token):
                actual = self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, "get radio2")
                self.assertEqual(actual, envelope(target=target).hex())
                expected_broadcast = "1" if token == "all" else "0"
                self.assertTrue(self.decode(bytes.fromhex(actual), metadata=True).endswith("\n" + expected_broadcast))

    def test_real_signature_round_trip_for_mixed_prefixes_and_full_keys(self):
        entries = [(4, PUBLIC[:4]), (6, OTHER_PUBLIC[:6]), (16, hashlib.sha256(PUBLIC).digest()[:16])]
        token = f"{PUBLIC[:4].hex()},{OTHER_PUBLIC[:6].hex()},{PUBLIC.hex()}"
        expected = envelope2(entries)
        actual = self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, "get radio2")
        self.assertEqual(actual, expected.hex())
        self.assertTrue(self.decode(expected, metadata=True).endswith("\n1"))
        self.assertTrue(self.decode(expected, self_key=OTHER_PUBLIC, metadata=True).endswith("\n1"))
        unrelated = hashlib.sha256(b"unrelated fleet node").digest()
        self.assertEqual(self.decode(expected, self_key=unrelated), "reject")

    def test_single_raw_prefix_is_a_broadcast_capability_for_matching_collisions(self):
        first = PUBLIC
        second = PUBLIC[:4] + bytes([PUBLIC[4] ^ 1]) + PUBLIC[5:]
        broad = envelope2([(4, PUBLIC[:4])])
        self.assertTrue(self.decode(broad, self_key=first, metadata=True).endswith("\n1"))
        self.assertTrue(self.decode(broad, self_key=second, metadata=True).endswith("\n1"))
        narrow = envelope2([(6, PUBLIC[:6])])
        self.assertNotEqual(self.decode(narrow, self_key=first), "reject")
        self.assertEqual(self.decode(narrow, self_key=second), "reject")
        full = envelope2([(16, hashlib.sha256(first).digest()[:16])])
        self.assertTrue(self.decode(full, self_key=first, metadata=True).endswith("\n0"))
        self.assertEqual(self.decode(full, self_key=second), "reject")

    def test_fmc2_all_is_accepted_and_duplicate_matches_decode_once(self):
        self.assertTrue(self.decode(envelope2(), metadata=True).endswith("\n1"))
        duplicates = envelope2([(16, hashlib.sha256(PUBLIC).digest()[:16])] * 2)
        result = self.decode(duplicates, metadata=True)
        self.assertEqual(result, f"{NOW}\n{NOW + 120}\nget radio2\n1")

    def test_legitimate_zero_prefixes_are_accepted_but_zero_complete_identity_is_not(self):
        node = b"\0" * 6 + b"\1" * 26
        for width in [4, 6]:
            with self.subTest(width=width):
                self.assertNotEqual(self.decode(envelope2([(width, b"\0" * width)]), self_key=node), "reject")
        self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), "0" * 64, NOW, NOW + 120, "get radio2"), "reject")

    def test_multitarget_signature_binds_every_target_entry_and_count(self):
        original = envelope2([(4, PUBLIC[:4]), (6, OTHER_PUBLIC[:6]),
                              (16, hashlib.sha256(PUBLIC).digest()[:16])])
        # The first prefix still matches. Every later record byte nevertheless
        # remains signed, so modifying a nonmatching or duplicate target fails.
        for offset in range(18, 13 + 5 + 7 + 17):
            with self.subTest(target_byte=offset):
                changed = bytearray(original)
                changed[offset] ^= 1
                self.assertEqual(self.decode(changed), "reject")
        changed = bytearray(original)
        changed[12] = 2
        self.assertEqual(self.decode(changed), "reject")

    def test_malformed_target_after_matching_record_is_rejected_even_when_signed(self):
        matching = (4, PUBLIC[:4])
        malformed = [
            [(3, b"abc")], [(0, b"")], [(5, b"abcde")], [(32, PUBLIC)],
            [(16, b"x" * 15)], [(6, b"x" * 5)], [(255, b"")]]
        for suffix in malformed:
            with self.subTest(suffix=suffix):
                self.assertEqual(self.decode(envelope2([matching] + suffix)), "reject")
        self.assertEqual(self.decode(envelope2([matching], count=18)), "reject")
        self.assertEqual(self.decode(envelope2([matching], count=0)), "reject")
        self.assertEqual(self.decode(envelope2([matching], count=2)), "reject")

    def test_new_target_parser_refuses_partial_or_mixed_all_lists(self):
        for token in ["", "all," + PUBLIC[:4].hex(), PUBLIC[:4].hex() + ",all",
                      PUBLIC[:4].hex() + ",", "," + PUBLIC[:4].hex(),
                      PUBLIC[:4].hex() + ",," + OTHER_PUBLIC[:4].hex(),
                      PUBLIC[:4].hex() + ",zzzzzzzz", "1" * 10, "1" * 16,
                      ",".join([PUBLIC.hex()] * 6), ",".join([PUBLIC[:4].hex()] * 18)]:
            with self.subTest(token=token):
                self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, "get radio2"), "reject")

    def test_fmc2_packet_capacity_is_checked_before_signing_or_sending(self):
        maximum = "set flood.rule.1 " + "a" * (72 - len("set flood.rule.1 "))
        entries = [(4, PUBLIC[:4])] * 3
        expected = envelope2(entries, maximum)
        self.assertEqual(len(expected), 165)
        token = ",".join([PUBLIC[:4].hex()] * 3)
        actual = self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, maximum)
        self.assertEqual(actual, expected.hex())
        self.assertNotEqual(self.decode(expected), "reject")
        token += "," + PUBLIC[:4].hex()
        self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, maximum), "reject")
        self.assertEqual(self.decode(envelope2(entries * 2, maximum)), "reject")
        for count in [15, 16, 17]:
            token = ",".join([PUBLIC[:4].hex()] * count)
            result = self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, "get radio2")
            self.assertEqual(result == "reject", count > 15)

    def test_fmc2_rejects_short_trailing_and_signed_forbidden_commands(self):
        original = envelope2([(4, PUBLIC[:4]), (6, OTHER_PUBLIC[:6])])
        for length in [0, 12, 13, 14, 18, 19, len(original) - 1]:
            self.assertEqual(self.decode(original[:length]), "reject")
        self.assertEqual(self.decode(original + b"\0"), "reject")
        for command in ["get prv.key", "set fleet.controller off", "get radio2\nreboot", b"get radio2\0"]:
            self.assertEqual(self.decode(envelope2([(4, PUBLIC[:4])], command)), "reject")

    def test_fmc2_uses_same_signed_sequence_and_clock_rules(self):
        entries = [(4, PUBLIC[:4])]
        original = envelope2(entries)
        self.assertEqual(struct.unpack("<I", original[4:8])[0], NOW)
        forged = envelope2(entries, sequence=NOW + 1)[:-64] + original[-64:]
        self.assertEqual(self.decode(forged), "reject")
        for sequence, expires, now in [(NOW + 61, NOW + 120, NOW),
                                       (NOW, NOW + 601, NOW), (NOW, NOW + 120, NOW + 121)]:
            self.assertEqual(self.decode(envelope2(entries, sequence=sequence, expires=expires), now=now), "reject")

    def test_fmc2_signature_cannot_move_between_channels_or_publishers(self):
        original = envelope2([(4, PUBLIC[:4]), (6, OTHER_PUBLIC[:6])])
        self.assertEqual(self.decode(original, publisher=OTHER_PUBLIC), "reject")
        self.assertEqual(self.decode(original, channel=bytes(range(17, 33))), "reject")
        self.assertEqual(self.decode(envelope2([(4, PUBLIC[:4])], publisher=OTHER)), "reject")


if __name__ == "__main__":
    unittest.main()
