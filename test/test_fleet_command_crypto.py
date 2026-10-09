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

inline unsigned fleet_sign_calls = 0;

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
  ++fleet_sign_calls;
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
  const std::string operation = argc > 1 ? argv[1] : "";
  if (argc == 7 && (operation == "encode" || operation == "encode-wide")) {
    FixedRng rng; mesh::LocalIdentity publisher(&rng);
    const auto key = unhex(argv[2]); assert(key.size() == Fleet::KeySize);
    std::array<uint8_t, Fleet::TargetSize> target;
    if (!Fleet::parseTarget(argv[3], target.data())) { std::cout << "reject\n"; return 0; }
    std::array<uint8_t, Fleet::MaxEnvelopeLength + 64> output;
    output.fill(0xA5);
    const unsigned signed_before = fleet_sign_calls;
    const size_t capacity = operation == "encode-wide" ? output.size() : Fleet::MaxEnvelopeLength;
    const size_t size = Fleet::encode(publisher, key.data(), uint32_t(std::stoul(argv[4])),
        uint32_t(std::stoul(argv[5])), target.data(), argv[6], output.data(), capacity);
    assert(size <= Fleet::MaxEnvelopeLength);
    if (!size) {
      assert(fleet_sign_calls == signed_before);
      for (uint8_t byte : output) assert(byte == 0xA5);
      std::cout << "reject\n";
    } else {
      assert(fleet_sign_calls == signed_before + 1);
      for (size_t i = size; i < output.size(); ++i) assert(output[i] == 0xA5);
      hex(output.data(), size);
    }
    return 0;
  }
  if (argc == 7 && (operation == "encode2" || operation == "encode2-wide")) {
    FixedRng rng; mesh::LocalIdentity publisher(&rng);
    const auto key = unhex(argv[2]); assert(key.size() == Fleet::KeySize);
    Fleet::Targets targets;
    if (!Fleet::parseTargets(argv[3], strlen(argv[3]), targets)) { std::cout << "reject\n"; return 0; }
    std::array<uint8_t, Fleet::MaxEnvelopeLength + 64> output;
    output.fill(0xA5);
    const unsigned signed_before = fleet_sign_calls;
    const size_t capacity = operation == "encode2-wide" ? output.size() : Fleet::MaxEnvelopeLength;
    const size_t size = Fleet::encode(publisher, key.data(), uint32_t(std::stoul(argv[4])),
        uint32_t(std::stoul(argv[5])), targets, argv[6], output.data(), capacity);
    assert(size <= Fleet::MaxEnvelopeLength);
    if (!size) {
      assert(fleet_sign_calls == signed_before);
      for (uint8_t byte : output) assert(byte == 0xA5);
      std::cout << "reject\n";
    } else {
      assert(fleet_sign_calls == signed_before + 1);
      for (size_t i = size; i < output.size(); ++i) assert(output[i] == 0xA5);
      hex(output.data(), size);
    }
    return 0;
  }
  if (argc == 5 && operation == "fragment") {
    const auto envelope = unhex(argv[2]);
    const unsigned index = unsigned(std::stoul(argv[3]));
    assert(index <= 255);
    const size_t capacity = std::stoul(argv[4]);
    std::array<uint8_t, Fleet::MaxPayloadLength + 64> output;
    assert(capacity <= output.size()); output.fill(0xA5);
    const unsigned signed_before = fleet_sign_calls;
    const size_t size = Fleet::fragment(envelope.data(), envelope.size(), uint8_t(index),
                                        output.data(), capacity);
    assert(fleet_sign_calls == signed_before && size <= Fleet::MaxPayloadLength);
    if (!size) {
      for (uint8_t byte : output) assert(byte == 0xA5);
      std::cout << "reject\n";
    } else {
      for (size_t i = size; i < output.size(); ++i) assert(output[i] == 0xA5);
      hex(output.data(), size);
    }
    return 0;
  }
  if (argc == 3 && operation == "parse-fragment") {
    const auto payload = unhex(argv[2]);
    Fleet::Fragment output{123, 300, 1, payload.data(), 100};
    if (!Fleet::parseFragment(payload.data(), payload.size(), output)) {
      assert(!output.sequence && !output.total_length && !output.index && !output.data && !output.length);
      std::cout << "reject\n";
    } else {
      std::cout << output.sequence << '\n' << output.total_length << '\n' << unsigned(output.index) << '\n';
      hex(output.data, output.length);
    }
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


def fragment_frame(payload, index):
    """Independent wire framing; the signature stays in the whole envelope."""
    sequence = struct.unpack("<I", payload[4:8])[0]
    return (b"FMP1" + struct.pack("<IHB", sequence, len(payload), index)
            + payload[index * 154:(index + 1) * 154])


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

    def test_scheduled_radio_commands_have_cross_language_real_signatures(self):
        for command in [
                "set radioat 910.5,500,5,5,+1,auto",
                "set radioat 910.5,500,5,5,1800000000,65528",
                "set tempradioat 910.5,500,5,5,+1,+2,32",
                "set tempradioat 910.5,500,5,5,+1,1800000600,auto",
                "set radioat2 910.5,500,5,5,rx,+1,0",
                "set tempradioat2 910.5,500,5,5,rxtx,1800000000,1800000600,auto"]:
            with self.subTest(command=command):
                expected = envelope(command)
                actual = self.run_tool("encode", CHANNEL.hex(), "all", NOW, NOW + 120, command)
                self.assertEqual(actual, expected.hex())
                PUBLISHER.public_key().verify(expected[-64:], DOMAIN + CHANNEL + expected[:-64])
                self.assertEqual(self.decode(expected), f"{NOW}\n{NOW + 120}\n{command}")

    def test_schedule_inspection_and_deletion_are_bounded_and_exact(self):
        for family in ["radioat", "tempradioat", "radioat2", "tempradioat2"]:
            for verb in ["get", "del"]:
                maximum = 4 if family.endswith("2") else 255
                for selector in ["", " all", " 1", f" {maximum}"]:
                    command = f"{verb} {family}{selector}"
                    with self.subTest(command=command):
                        self.assertEqual(self.decode(envelope(command)), f"{NOW}\n{NOW + 120}\n{command}")
                for selector in [" 0", " 01", " +1", " -1", " ALL", " 1 all", f" {maximum + 1}"]:
                    command = f"{verb} {family}{selector}"
                    with self.subTest(command=command):
                        self.assertEqual(self.decode(envelope(command)), "reject")

    def test_signed_malformed_schedule_and_alias_commands_are_rejected(self):
        for command in [
                "set radioat off", "set tempradioat off", "set radioat2 off", "set tempradioat2 off",
                "set radioat 910.5,500,5,5", "set tempradioat 910.5,500,5,5,+1",
                "set radioat2 910.5,500,5,5,+1", "set tempradioat2 910.5,500,5,5,rx,+1",
                "set radioat 910.5,500,5,5,rx,+1", "set radioat2 910.5,500,5,5,rx&tx,+1",
                "set radioat 910.5,500,5,5,+0", "set radioat 910.5,500,5,5,++1",
                "set radioat 910.5,500,5,5,+1m", "set radioat 910.5,500,5,5,+1.5",
                "set radioat 910.5,500,5,5,-1", "set radioat 910.5,500,5,5,1e9",
                "set radioat 910.5,500,5,5,4294967296", "set radioat 910.5,500,5,5,+71582789",
                "set radioat 910.5,500,256,5,+1", "set radioat 910.5,500,5,5,+1,7",
                "set radioat 910.5,500,5,5,+1,65529", "set radioat 910.5,500,5,5,+1,AUTO",
                "set radioat 910.5,500,5,5,+1,+8", "set radioat 910.5,500,5,5,+1,auto,8",
                "set radioat.extra 910.5,500,5,5,+1", "get radioat.all", "set radioat 910.5,500,5,5, +1",
                "set tempradioat2 910.5,500,5,5,rx,+1,+2;reboot",
                "set tempradioat2 910.5,500,5,5,rx,+1,+2\nreboot",
                "set radio 910.5,500,5,5", "set tempradio 910.5,500,5,5,10"]:
            with self.subTest(command=command):
                self.assertEqual(self.decode(envelope(command)), "reject")

    def test_multitarget_scheduled_request_is_signed_and_body_bounded(self):
        command = "set tempradioat2 910.5,500,5,5,rxtx,+1,+2,auto"
        entries = [(4, PUBLIC[:4]), (6, OTHER_PUBLIC[:6])]
        token = f"{PUBLIC[:4].hex()},{OTHER_PUBLIC[:6].hex()}"
        expected = envelope2(entries, command)
        actual = self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, command)
        self.assertEqual(actual, expected.hex())
        self.assertEqual(self.decode(expected, metadata=True), f"{NOW}\n{NOW + 120}\n{command}\n1")
        self.assertNotEqual(self.decode(expected, self_key=OTHER_PUBLIC), "reject")
        changed_command = command.replace("+2", "+3")
        self.assertEqual(self.decode(envelope2(entries, changed_command)[:-64] + expected[-64:]), "reject")
        prefix, suffix = "set tempradioat2 910.5,500,5,5,rxtx,+", "1,+2,auto"
        maximum = prefix + "0" * (72 - len(prefix) - len(suffix)) + suffix
        self.assertEqual(len(maximum), 72)
        self.assertNotEqual(self.decode(envelope(maximum)), "reject")
        self.assertNotEqual(self.decode(envelope2(entries, maximum)), "reject")
        larger = prefix + "0" + maximum[len(prefix):]
        self.assertNotEqual(self.decode(envelope(larger)), "reject")
        self.assertNotEqual(self.decode(envelope2(entries, larger)), "reject")
        target_maximum = prefix + "0" * (218 - len(prefix) - len(suffix)) + suffix
        self.assertNotEqual(self.decode(envelope2(entries, target_maximum)), "reject")
        too_large = prefix + "0" + target_maximum[len(prefix):]
        self.assertEqual(self.decode(envelope2(entries, too_large)), "reject")
        broadcast_maximum = prefix + "0" * (230 - len(prefix) - len(suffix)) + suffix
        expected = envelope2(command=broadcast_maximum)
        self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), "all", NOW, NOW + 120,
                                       broadcast_maximum), expected.hex())
        self.assertNotEqual(self.decode(expected), "reject")

    def test_clock_management_has_real_signatures_and_no_prefix_aliases(self):
        for command in ["clock", "clock sync", "time 1735689600", "time 1800000000", "time 4294967295"]:
            with self.subTest(command=command):
                expected = envelope(command)
                actual = self.run_tool("encode", CHANNEL.hex(), "all", NOW, NOW + 120, command)
                self.assertEqual(actual, expected.hex())
                self.assertEqual(self.decode(expected), f"{NOW}\n{NOW + 120}\n{command}")
                self.assertEqual(self.decode(expected, publisher=OTHER_PUBLIC), "reject")
                self.assertEqual(self.decode(expected, channel=bytes(range(17, 33))), "reject")
        for command in [
                "get clock", "set clock 1800000000", "clock now", "clock sync now", "clock.sync",
                "clkreboot", "clock sync;reboot", "clock sync\nreboot", "clock  sync", "time",
                "time  1800000000", "time +1800000000", "time -1800000000", "time 1735689599",
                "time 0", "time 4294967296", "time 9999999999999999999", "time 1800000000.0",
                "time 1e9", "time 1800000000 now", "time 1800000000;reboot", "TIME 1800000000"]:
            with self.subTest(command=command):
                self.assertEqual(self.decode(envelope(command)), "reject")

    def test_signed_clock_requests_cannot_bootstrap_or_bypass_replay_time_boundaries(self):
        for command in ["clock", "clock sync", "time 1800000000"]:
            with self.subTest(command=command):
                for sequence, expires, now in [
                        (NOW, NOW + 120, 0), (NOW, NOW + 120, 1735689599),
                        (NOW, NOW + 120, NOW - 61), (NOW, NOW + 120, NOW + 121),
                        (NOW, NOW + 601, NOW), (1735689599, 1735689600, 1735689600)]:
                    self.assertEqual(self.decode(envelope(command, sequence, expires), now=now), "reject")
                original = envelope(command)
                forged = envelope(command, sequence=NOW + 1)[:-64] + original[-64:]
                self.assertEqual(self.decode(forged), "reject")
                self.assertNotEqual(self.decode(original, now=NOW + 120), "reject")

    def test_fixed_target_exact_limit_cannot_be_raised_by_destination_capacity(self):
        maximum = "set flood.rule.1 " + "a" * (215 - len("set flood.rule.1 "))
        accepted = envelope(maximum)
        self.assertEqual(len(accepted), 308)
        self.assertNotEqual(self.decode(accepted), "reject")
        self.assertEqual(self.decode(envelope(maximum + "a")), "reject")
        for operation in ["encode", "encode-wide"]:
            self.assertEqual(self.run_tool(operation, CHANNEL.hex(), "all", NOW, NOW + 120, maximum),
                             accepted.hex())
            for length in [216, 230, 231]:
                command = "set flood.rule.1 " + "a" * (length - len("set flood.rule.1 "))
                self.assertEqual(self.run_tool(operation, CHANNEL.hex(), "all", NOW, NOW + 120, command), "reject")

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

    def test_compact_broadcast_is_signed_without_target_bytes_and_legacy_all_still_decodes(self):
        expected = envelope2()
        actual = self.run_tool("encode2", CHANNEL.hex(), "all", NOW, NOW + 120, "get radio2")
        self.assertEqual(actual, expected.hex())
        self.assertEqual(expected[:4], b"FMC2")
        self.assertEqual(expected[12], 0)
        self.assertEqual(len(expected), 14 + 10 + 64)
        self.assertEqual(len(envelope()) - len(expected), 15)
        PUBLISHER.public_key().verify(expected[-64:], DOMAIN + CHANNEL + expected[:-64])
        for node in [PUBLIC, OTHER_PUBLIC, hashlib.sha256(b"unrelated broadcast receiver").digest()]:
            with self.subTest(node=node.hex()):
                self.assertEqual(self.decode(expected, self_key=node, metadata=True),
                                 f"{NOW}\n{NOW + 120}\nget radio2\n1")
                self.assertEqual(self.decode(envelope(), self_key=node, metadata=True),
                                 f"{NOW}\n{NOW + 120}\nget radio2\n1")

    def test_new_sender_preserves_fmc1_for_single_complete_key(self):
        expected = envelope(target=hashlib.sha256(PUBLIC).digest()[:16])
        actual = self.run_tool("encode2", CHANNEL.hex(), PUBLIC.hex(), NOW, NOW + 120, "get radio2")
        self.assertEqual(actual, expected.hex())
        self.assertTrue(self.decode(bytes.fromhex(actual), metadata=True).endswith("\n0"))

    def test_compact_broadcast_signature_binds_no_target_count_command_and_times(self):
        original = envelope2()
        # All of these are valid, matching requests on their own. Attaching
        # the old zero-target signature must not authorize the changed data.
        for changed in [
                envelope2([(4, PUBLIC[:4])]),
                envelope2([(16, hashlib.sha256(PUBLIC).digest()[:16])]),
                envelope2(command="get tempradio2"),
                envelope2(sequence=NOW + 1), envelope2(expires=NOW + 121)]:
            with self.subTest(changed=changed.hex()):
                self.assertNotEqual(self.decode(changed), "reject")
                self.assertEqual(self.decode(changed[:-64] + original[-64:]), "reject")
        for offset in range(len(original) - 64, len(original)):
            with self.subTest(signature_byte=offset):
                changed = bytearray(original)
                changed[offset] ^= 1
                self.assertEqual(self.decode(changed), "reject")
        changed = bytearray(original)
        changed[12] = 1
        self.assertEqual(self.decode(changed), "reject")
        self.assertEqual(self.decode(original, publisher=OTHER_PUBLIC), "reject")
        self.assertEqual(self.decode(original, channel=bytes(range(17, 33))), "reject")

    def test_compact_broadcast_fits_230_byte_signed_command_and_framing_limits(self):
        prefix = "set flood.rule.1 "
        maximum = prefix + "a" * (230 - len(prefix))
        expected = envelope2(command=maximum)
        actual = self.run_tool("encode2", CHANNEL.hex(), "all", NOW, NOW + 120, maximum)
        self.assertEqual(actual, expected.hex())
        self.assertEqual(len(expected), 308)
        PUBLISHER.public_key().verify(expected[-64:], DOMAIN + CHANNEL + expected[:-64])
        for node in [PUBLIC, OTHER_PUBLIC]:
            self.assertEqual(self.decode(expected, self_key=node, metadata=True),
                             f"{NOW}\n{NOW + 120}\n{maximum}\n1")
        oversized = maximum + "a"
        self.assertEqual(self.decode(envelope2(command=oversized)), "reject")
        self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), "all", NOW, NOW + 120, oversized), "reject")
        self.assertEqual(self.run_tool("encode2-wide", CHANNEL.hex(), "all", NOW, NOW + 120, oversized), "reject")
        for invalid in [b"\n", b";", b"\x80", b"\0"]:
            malformed = maximum[:-1].encode("ascii") + invalid
            self.assertEqual(self.decode(envelope2(command=malformed)), "reject")
        for length in [0, 12, 13, 14, len(expected) - 64, len(expected) - 1]:
            with self.subTest(length=length):
                self.assertEqual(self.decode(expected[:length]), "reject")
        self.assertEqual(self.decode(expected + b"\0"), "reject")

    def test_each_target_form_has_exact_packet_budget_and_atomic_oversize_rejection(self):
        short, long = PUBLIC[:4].hex(), PUBLIC[:6].hex()
        digest = hashlib.sha256(PUBLIC).digest()[:16]
        for token, entries, maximum_length in [
                ("all", [], 230), (short, [(4, PUBLIC[:4])], 225),
                (long, [(6, PUBLIC[:6])], 223), (PUBLIC.hex(), None, 215),
                (f"{short},{short}", [(4, PUBLIC[:4])] * 2, 220),
                (f"{short},{long}", [(4, PUBLIC[:4]), (6, PUBLIC[:6])], 218),
                (f"{PUBLIC.hex()},{short}", [(16, digest), (4, PUBLIC[:4])], 208),
                (f"{PUBLIC.hex()},{PUBLIC.hex()}", [(16, digest)] * 2, 196)]:
            with self.subTest(token=token, maximum=maximum_length):
                prefix = "set flood.rule.1 "
                maximum = prefix + "a" * (maximum_length - len(prefix))
                expected = (envelope(maximum, target=digest) if entries is None
                            else envelope2(entries, maximum))
                self.assertEqual(len(expected), 308)
                self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120, maximum),
                                 expected.hex())
                self.assertNotEqual(self.decode(expected), "reject")
                # The fixture verifies no writes and no signature calls on
                # rejection, even when its output buffer exceeds packet size.
                oversized = maximum + "a"
                for operation in ["encode2", "encode2-wide"]:
                    self.assertEqual(self.run_tool(operation, CHANNEL.hex(), token, NOW, NOW + 120,
                                                   oversized), "reject")
                signed_oversized = (envelope(oversized, target=digest) if entries is None
                                    else envelope2(entries, oversized))
                self.assertEqual(self.decode(signed_oversized), "reject")
                altered = bytearray(expected)
                altered[-65] = ord("b")
                self.assertEqual(self.decode(altered), "reject")

    def test_maximum_broadcast_signature_covers_final_byte_and_230_byte_length(self):
        prefix = "set flood.rule.1 "
        maximum = prefix + "a" * (230 - len(prefix))
        original = envelope2(command=maximum)
        self.assertNotEqual(self.decode(original), "reject")
        for command in [maximum[:-1] + "b", maximum[:-1]]:
            changed = envelope2(command=command)
            self.assertNotEqual(self.decode(changed), "reject")
            self.assertEqual(self.decode(changed[:-64] + original[-64:]), "reject")
        for length_byte in [0, 87, 229, 231, 255]:
            changed = bytearray(original)
            changed[13] = length_byte
            self.assertEqual(self.decode(changed), "reject")
        for offset in range(len(original) - 64, len(original)):
            with self.subTest(signature_byte=offset):
                changed = bytearray(original)
                changed[offset] ^= 1
                self.assertEqual(self.decode(changed), "reject")

    def test_single_packet_boundaries_and_first_two_fragment_command(self):
        digest = hashlib.sha256(PUBLIC).digest()[:16]
        for token, entries, maximum in [
                ("all", [], 87), (PUBLIC[:4].hex(), [(4, PUBLIC[:4])], 82),
                (PUBLIC[:6].hex(), [(6, PUBLIC[:6])], 80),
                (PUBLIC.hex(), None, 72)]:
            with self.subTest(token=token):
                prefix = "set flood.rule.1 "
                command = prefix + "a" * (maximum - len(prefix))
                single = (envelope(command, target=digest) if entries is None
                          else envelope2(entries, command))
                self.assertEqual(len(single), 165)
                self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120,
                                               command), single.hex())
                self.assertNotEqual(self.decode(single), "reject")
                for index in [0, 1]:
                    self.assertEqual(self.run_tool("fragment", single.hex(), index, 165), "reject")
                longer = (envelope(command + "a", target=digest) if entries is None
                          else envelope2(entries, command + "a"))
                self.assertEqual(len(longer), 166)
                self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), token, NOW, NOW + 120,
                                               command + "a"), longer.hex())
                self.assertNotEqual(self.decode(longer), "reject")
                for index, size in [(0, 165), (1, 23)]:
                    actual = bytes.fromhex(self.run_tool("fragment", longer.hex(), index, 165))
                    self.assertEqual(actual, fragment_frame(longer, index))
                    self.assertEqual(len(actual), size)

    def test_two_fragments_reassemble_out_of_order_with_one_real_signature(self):
        for size in [166, 167, 307, 308]:
            with self.subTest(size=size):
                prefix = "set flood.rule.1 "
                command = prefix + "a" * (size - 78 - len(prefix))
                original = envelope2(command=command)
                self.assertEqual(len(original), size)
                # The fixture requires exactly one signature call for encode
                # and no signature calls while cutting either fragment.
                self.assertEqual(self.run_tool("encode2", CHANNEL.hex(), "all", NOW, NOW + 120,
                                               command), original.hex())
                parts = [bytes.fromhex(self.run_tool("fragment", original.hex(), index, 165))
                         for index in [0, 1]]
                for index, part in enumerate(parts):
                    self.assertEqual(part, fragment_frame(original, index))
                    self.assertLessEqual(len(part), 165)
                    self.assertEqual(self.run_tool("parse-fragment", part.hex()),
                                     f"{NOW}\n{size}\n{index}\n{part[11:].hex()}")
                    self.assertEqual(self.decode(part), "reject")
                for order in [(0, 1), (1, 0), (0, 0, 1), (1, 1, 0)]:
                    with self.subTest(order=order):
                        assembly = bytearray(size)
                        for index in order:
                            part = parts[index]
                            assembly[index * 154:index * 154 + len(part) - 11] = part[11:]
                        assembly = bytes(assembly)
                        self.assertEqual(assembly, original)
                        PUBLISHER.public_key().verify(assembly[-64:], DOMAIN + CHANNEL + assembly[:-64])
                        for node in [PUBLIC, OTHER_PUBLIC]:
                            self.assertEqual(self.decode(assembly, self_key=node, metadata=True),
                                             f"{NOW}\n{NOW + 120}\n{command}\n1")

    def test_fragment_codec_bounds_and_exact_shapes_reject_without_output_writes(self):
        prefix = "set flood.rule.1 "
        original = envelope2(command=prefix + "a" * (88 - len(prefix)))
        parts = [fragment_frame(original, index) for index in [0, 1]]
        for source in [b"", original[:165], original + b"a" * (309 - len(original)),
                       b"FMX1" + original[4:], b"FMP1" + original[4:]]:
            with self.subTest(source_length=len(source), magic=source[:4]):
                self.assertEqual(self.run_tool("fragment", source.hex(), 0, 229), "reject")
        for index in [2, 255]:
            self.assertEqual(self.run_tool("fragment", original.hex(), index, 229), "reject")
        for index, required in [(0, 165), (1, 23)]:
            for capacity in [0, 10, required - 1]:
                self.assertEqual(self.run_tool("fragment", original.hex(), index, capacity), "reject")
            self.assertEqual(self.run_tool("fragment", original.hex(), index, 229), parts[index].hex())
        malformed = [b"", b"FMP1", parts[0][:10], parts[0][:11],
                     parts[0][:-1], parts[0] + b"\0", parts[1][:-1], parts[1] + b"\0",
                     b"FMX1" + parts[0][4:]]
        for total in [0, 154, 165, 309, 65535]:
            malformed.append(parts[0][:8] + struct.pack("<H", total) + parts[0][10:])
        for index in [1, 2, 255]:
            malformed.append(parts[0][:10] + bytes([index]) + parts[0][11:])
        malformed.append(parts[1][:10] + b"\0" + parts[1][11:])
        for part in malformed:
            with self.subTest(part=part.hex()):
                self.assertEqual(self.run_tool("parse-fragment", part.hex()), "reject")

    def test_fragment_tampering_and_mixed_commands_cannot_forge_complete_signature(self):
        prefix = "set flood.rule.1 "
        command = prefix + "a" * (230 - len(prefix))
        original = envelope2(command=command)
        parts = [fragment_frame(original, index) for index in [0, 1]]
        self.assertNotEqual(self.decode(original), "reject")
        # Every chunk contains signed data. Matching frame metadata and an
        # allowed changed command cannot substitute for the full signature.
        for index, data_offset in [(0, 14 + len(prefix)), (1, 0), (1, 153)]:
            changed = bytearray(parts[index])
            changed[11 + data_offset] ^= 1
            self.assertNotEqual(self.run_tool("parse-fragment", changed.hex()), "reject")
            chunks = [parts[0][11:], parts[1][11:]]
            chunks[index] = changed[11:]
            self.assertEqual(self.decode(b"".join(chunks)), "reject")
        different = envelope2(command=prefix + "b" * (230 - len(prefix)))
        self.assertNotEqual(self.decode(different), "reject")
        different_parts = [fragment_frame(different, index) for index in [0, 1]]
        for combined in [parts[0][11:] + different_parts[1][11:],
                         different_parts[0][11:] + parts[1][11:]]:
            self.assertEqual(self.decode(combined), "reject")
        # Frame metadata is deliberately unauthenticated and only admits a
        # bounded assembly. Runtime must compare it to the signed sequence.
        changed_sequence = parts[0][:4] + struct.pack("<I", NOW + 1) + parts[0][8:]
        self.assertTrue(self.run_tool("parse-fragment", changed_sequence.hex()).startswith(f"{NOW + 1}\n"))
        self.assertEqual(self.decode(changed_sequence), "reject")

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
        maximum = "set flood.rule.1 " + "a" * (215 - len("set flood.rule.1 "))
        entries = [(4, PUBLIC[:4])] * 3
        expected = envelope2(entries, maximum)
        self.assertEqual(len(expected), 308)
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
            self.assertNotEqual(result, "reject")

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
