"""Test TX-only beacon frames with real AES/SHA and independent wire oracles.

Only the radio queue, clock and packet-pool boundary are substituted. The
production group-datagram builder, packet serializer and Mesh adapter run as
written; no PlatformIO, hardware or downloaded dependency is needed.
"""

import hashlib
import hmac
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body
from test_meshtext_chat import DEFAULT_KEY, data_text, reference_frame


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")


ADAPTER = r'''
#pragma once
#include <Packet.h>
#include <Utils.h>
#include <helpers/BeaconConfig.h>
#include <cassert>
#include <vector>
namespace mesh {
struct GroupChannel {
  uint8_t hash[PATH_HASH_SIZE] = {};
  uint8_t secret[PUB_KEY_SIZE] = {};
  uint8_t tx_radio = RADIO_TX_AUTO;
};
class TestRadio {
public:
  bool valid = true, meshtext = true;
  RadioProfiles profiles;
  bool validateProfile(const RadioProfileParams& p) const { return valid && RadioProfiles::valid(p); }
  bool supportsMeshtext() const { return meshtext; }
};
class TestClock {
public:
  uint32_t now = 0x12345678;
  uint32_t getCurrentTimeUnique() { return now++; }
};
class TestTables {
public:
  unsigned sent = 0;
  std::vector<uint8_t> marked;
  void markSent(const Packet* packet) {
    ++sent;
    uint8_t raw[MAX_TRANS_UNIT];
    const size_t length = packet->writeTo(raw);
    marked.assign(raw, raw + length);
  }
};
class Mesh {
public:
  TestRadio radio;
  TestRadio* _radio = &radio;
  TestClock clock;
  TestTables tables;
  struct { uint8_t pub_key[32] = {}; } self_id;
  bool temporary = false, accept = true, pool_available = true;
  unsigned obtained = 0, released = 0, queues = 0;
  std::vector<uint8_t> queued;
  RadioProfileParams queued_params;
  int8_t queued_power = 0;
  Packet pool_packet;
  bool validateTextBeacon(const BeaconSlot&) const;
  bool queueTextBeacon(const BeaconSlot&, const char*, uint32_t, uint32_t);
  uint32_t beaconNodeId() const;
  bool isAnyTempRadioActive() const { return temporary; }
  TestClock* getRTCClock() { return &clock; }
  TestTables* getTables() { return &tables; }
  Packet* obtainNewPacket() {
    if (!pool_available) return nullptr;
    ++obtained;
    pool_packet = Packet();
    return &pool_packet;
  }
  void releasePacket(Packet* p) { assert(p == &pool_packet); ++released; }
  Packet* createGroupDatagram(uint8_t, const GroupChannel&, const uint8_t*, size_t);
  bool queueBeaconRadioFrame(const uint8_t* bytes, size_t size,
                             const RadioProfileParams& params, int8_t power) {
    ++queues;
    if (!accept) return false;
    queued.assign(bytes, bytes + size);
    queued_params = params;
    queued_power = power;
    return true;
  }
};
}
'''


HARNESS = r'''
#include <Mesh.h>
#include <helpers/BeaconFrames.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
using namespace mesh;
static std::vector<uint8_t> unhex(const char* input) {
  const std::string text(input);
  assert(text.size() % 2 == 0);
  std::vector<uint8_t> bytes;
  for (size_t i = 0; i < text.size(); i += 2)
    bytes.push_back(uint8_t(std::stoul(text.substr(i, 2), nullptr, 16)));
  return bytes;
}
static BeaconSlot slot(bool mt = false) {
  BeaconSlot result;
  result.radio.freq = 906.875f;
  result.radio.bw = 250;
  result.radio.sf = 11;
  result.radio.cr = 5;
  result.radio.preamble = mt ? 16 : 32;
  result.radio.protocol = mt ? RadioProfileProtocol::Meshtext : RadioProfileProtocol::MeshCore;
  result.power = 17;
  result.preset = static_cast<uint8_t>(mt::Preset::LongFast);
  result.hops = 3;
  result.enabled = true;
  strcpy(result.text, "hello");
  return result;
}
static void public_frame(const char* expected) {
  Mesh mesh;
  const auto before = mesh.radio.profiles;
  auto config = slot();
  assert(mesh.validateTextBeacon(config));
  assert(mesh.queueTextBeacon(config, "Node:name", 0, 0));
  assert(mesh.queued == unhex(expected));
  assert(mesh.tables.sent == 1 && mesh.tables.marked == mesh.queued);
  assert(mesh.obtained == 1 && mesh.released == 1 && mesh.queues == 1);
  assert(mesh.queued_params == config.radio && mesh.queued_power == 17);
  assert(mesh.radio.profiles.primary == before.primary);
  assert(mesh.radio.profiles.secondary.params == before.secondary.params);
  assert(mesh.radio.profiles.secondary.mode == before.secondary.mode);
  assert(mesh.radio.profiles.generation[0] == before.generation[0]);
  assert(mesh.radio.profiles.generation[1] == before.generation[1]);
}
static void meshtext_frame(const char* expected, bool medium) {
  Mesh mesh;
  auto config = slot(true);
  if (medium) {
    config.preset = static_cast<uint8_t>(mt::Preset::MediumFast);
    config.radio.sf = 9;
    config.hops = 7;
  }
  strcpy(config.text, "caf\xc3\xa9");
  mesh.radio.profiles.secondary.params.freq = 915;
  mesh.radio.profiles.secondary.mode = RadioProfileMode::RxTx;
  const auto before = mesh.radio.profiles;
  assert(mesh.queueTextBeacon(config, "Node", 0x11223344, 0x89abcdef));
  assert(mesh.queued == unhex(expected));
  assert(mesh.obtained == 0 && mesh.released == 0 && mesh.tables.sent == 0);
  assert(mesh.queues == 1 && mesh.queued_params == config.radio);
  assert(mesh.radio.profiles.secondary.params == before.secondary.params);
  assert(mesh.radio.profiles.secondary.mode == before.secondary.mode);
  assert(mesh.radio.profiles.generation[0] == before.generation[0]);
  assert(mesh.radio.profiles.generation[1] == before.generation[1]);
  assert((mesh.queued[12] & 0x18) == 0); // No want_ack/via_mqtt bits.
  mt::DecodedText decoded;
  assert(mt::decodeText(mesh.queued.data(), mesh.queued.size(), medium ? "MediumFast" : "LongFast",
      mt::DefaultPsk, sizeof(mt::DefaultPsk), 0x99887766, decoded));
  assert(!strcmp(decoded.text, "Node: caf\xc3\xa9"));
}
static void bounds() {
  char output[beacon::MaxFormattedBytes + 2];
  uint8_t raw[mt::MaxFrameSize + 1];
  char name[33], text[82];
  memset(name, 'n', sizeof(name)); name[31] = 0;
  memset(text, 't', sizeof(text)); text[80] = 0;
  memset(output, 0xa5, sizeof(output));
  assert(beacon::formatText(name, text, output, sizeof(output)) == beacon::MaxFormattedBytes);
  assert(output[beacon::MaxFormattedBytes] == 0);
  assert(uint8_t(output[beacon::MaxFormattedBytes + 1]) == 0xa5);
  assert(!beacon::formatText(name, text, output, beacon::MaxFormattedBytes));
  assert(!beacon::formatText(nullptr, text, output, sizeof(output)));
  assert(!beacon::formatText("", text, output, sizeof(output)));
  assert(!beacon::formatText(name, text, nullptr, sizeof(output)));
  name[31] = 'n'; name[32] = 0;
  assert(!beacon::formatText(name, text, output, sizeof(output)));
  name[31] = 0;
  text[80] = 't'; text[81] = 0;
  assert(!beacon::validText(text));
  assert(!beacon::validText(nullptr) && !beacon::validText(""));
  for (unsigned control = 1; control <= 127; ++control) {
    if (control >= 32 && control != 127) continue;
    const char controlled[] = {'t', static_cast<char>(control), 'x', 0};
    assert(!beacon::validText(controlled));
    assert(!beacon::formatText("node", controlled, output, sizeof(output)));
  }
  for (const char* invalid : {"\x80", "\xc0\xaf", "\xe0\x80\x80", "\xed\xa0\x80",
                              "\xf0\x80\x80\x80", "\xf4\x90\x80\x80", "\xc2", "\xe2\x82"}) {
    assert(!beacon::validText(invalid));
    assert(!beacon::formatText(invalid, "text", output, sizeof(output)));
  }
  // Exact maximum byte lengths, including multibyte scalars, stay intact.
  memset(text, 't', 77); memcpy(text + 77, "\xe2\x82\xac", 4);
  assert(beacon::validText(text));
  memset(name, 'n', 27); memcpy(name + 27, "\xf0\x9f\x9a\x80", 5);
  assert(beacon::formatText(name, text, output, sizeof(output)) == beacon::MaxFormattedBytes);
  memset(raw, 0xa5, sizeof(raw));
  assert(!beacon::meshtextFrame(static_cast<mt::Preset>(255), 4, 1, 0, "n", "text", raw));
  for (uint32_t from : {0U, UINT32_MAX})
    assert(!beacon::meshtextFrame(mt::Preset::LongFast, from, 1, 0, "n", "text", raw));
  assert(!beacon::meshtextFrame(mt::Preset::LongFast, 4, 0, 0, "n", "text", raw));
  assert(!beacon::meshtextFrame(mt::Preset::LongFast, 4, 1, 8, "n", "text", raw));
  for (uint8_t byte : raw) assert(byte == 0xa5); // Rejection produces no partial frame.
  assert(beacon::meshtextFrame(mt::Preset::LongFast, 4, 1, 0, name, text, raw));
  assert(raw[mt::MaxFrameSize] == 0xa5);
  uint8_t payload[beacon::MaxPublicPayloadBytes + 1];
  memset(payload, 0xa5, sizeof(payload));
  assert(!beacon::publicPayload(1, name, text, payload, beacon::MaxPublicPayloadBytes - 1));
  for (uint8_t byte : payload) assert(byte == 0xa5);
  assert(beacon::publicPayload(1, name, text, payload, sizeof(payload)) == beacon::MaxPublicPayloadBytes);
  assert(payload[beacon::MaxPublicPayloadBytes] == 0xa5);
}
static void admission() {
  auto config = slot();
  { Mesh mesh; mesh.accept = false;
    assert(!mesh.queueTextBeacon(config, "Node", 0, 0));
    assert(mesh.obtained == 1 && mesh.released == 1 && mesh.tables.sent == 0 && mesh.queues == 1); }
  { Mesh mesh; mesh.pool_available = false;
    assert(!mesh.queueTextBeacon(config, "Node", 0, 0));
    assert(mesh.obtained == 0 && mesh.released == 0 && mesh.queues == 0); }
  { Mesh mesh; mesh.temporary = true;
    assert(mesh.queueTextBeacon(config, "Node", 0, 0));
    assert(mesh.queues == 1 && mesh.obtained == 1 && mesh.released == 1); }
  { Mesh mesh; mesh.radio.valid = false;
    assert(!mesh.validateTextBeacon(config));
    assert(!mesh.queueTextBeacon(config, "Node", 0, 0) && !mesh.queues); }
  { Mesh mesh; config.radio.sf = 0; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(); config.radio.preamble = 7; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(); config.radio.preamble = 65528; config.radio.sf = 12;
    config.radio.bw = 7.8f; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(); config.power = -10; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(); config.power = 23; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(); config.hops = 8; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(); config.radio.protocol = static_cast<RadioProfileProtocol>(2);
    assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(true); mesh.radio.meshtext = false; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(true); config.preset = 255; assert(!mesh.validateTextBeacon(config)); }
  { Mesh mesh; config = slot(true); assert(!mesh.queueTextBeacon(config, "Node", 4, 0));
    assert(!mesh.queues && !mesh.obtained); }
  { Mesh mesh; config = slot(); assert(!mesh.queueTextBeacon(config, "", 0, 0));
    assert(!mesh.queues && !mesh.obtained); }
}
static void identity(const char* public_hex, const char* expected) {
  const auto public_key = unhex(public_hex);
  assert(public_key.size() == 32);
  const uint32_t wanted = static_cast<uint32_t>(strtoul(expected, nullptr, 10));
  assert(beacon::nodeId(public_key.data()) == wanted);
  assert(beacon::nodeId(nullptr) == 0);
  Mesh mesh;
  memcpy(mesh.self_id.pub_key, public_key.data(), 32);
  assert(mesh.beaconNodeId() == wanted && wanted >= 4 && wanted != UINT32_MAX);
}
int main(int argc, char** argv) {
  assert(argc >= 2);
  if (!strcmp(argv[1], "public")) { assert(argc == 3); public_frame(argv[2]); }
  else if (!strcmp(argv[1], "longfast")) { assert(argc == 3); meshtext_frame(argv[2], false); }
  else if (!strcmp(argv[1], "mediumfast")) { assert(argc == 3); meshtext_frame(argv[2], true); }
  else if (!strcmp(argv[1], "bounds")) bounds();
  else if (!strcmp(argv[1], "admission")) admission();
  else if (!strcmp(argv[1], "identity")) { assert(argc == 4); identity(argv[2], argv[3]); }
  else assert(false);
  puts("PASS");
}
'''


class BeaconFramesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Python cryptography is required for independent frame vectors")
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++ compiler is required")
        required = ["AESCommon.cpp", "AES128.cpp", "AES256.cpp", "BlockCipher.cpp",
                    "Crypto.cpp", "SHA256.cpp", "Hash.cpp"]
        override = os.environ.get("MESHCORE_CRYPTO_DIR")
        candidates = [Path(override)] if override else sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        crypto = next((path for path in candidates if all((path / name).is_file() for name in required)), None)
        if not crypto:
            raise unittest.SkipTest("an existing rweather/Crypto checkout is required (MESHCORE_CRYPTO_DIR)")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-beacon-frames-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        (work / "Stream.h").write_text(
            "#pragma once\nclass Stream { public: void print(char) {} };\n", encoding="ascii")
        (work / "Mesh.h").write_text(ADAPTER, encoding="ascii")
        group_builder = body((ROOT / "src/Mesh.cpp").read_text(encoding="utf-8"),
                             "Packet* Mesh::createGroupDatagram(")
        cpp = work / "beacon.cpp"
        cpp.write_text(HARNESS + "\nnamespace mesh {\n" + group_builder + "\n}\n", encoding="ascii")
        cls.binary = work / ("beacon.exe" if sys.platform == "win32" else "beacon")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        result = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
             "-DMESH_ENABLE_TX_BEACONS=1",
             "-DMESH_ENABLE_MESHTEXT_WIRE=1", "-DMESH_ENABLE_MESHTEXT_CHAT=0",
             "-DMIN_LORA_TX_POWER=-9", "-DMAX_LORA_TX_POWER=22", *sanitizers,
             "-I" + str(work), "-I" + str(crypto), "-I" + str(ROOT / "src"), str(cpp),
             str(ROOT / "src/helpers/BeaconFrames.cpp"), str(ROOT / "src/MeshBeacon.cpp"),
             str(ROOT / "src/helpers/MeshtextChat.cpp"), str(ROOT / "src/Utils.cpp"),
             str(ROOT / "src/Packet.cpp"), *[str(crypto / name) for name in required],
             "-o", str(cls.binary)], capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, *arguments):
        result = subprocess.run([str(self.binary), *arguments], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_public_frame_matches_independent_aes_ecb_hmac_oracle_and_dedup(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        plain = struct.pack("<I", 0x12345678) + b"\0Node;name: hello"
        plain += bytes((-len(plain)) % 16)
        encryptor = Cipher(algorithms.AES(PUBLIC_KEY), modes.ECB()).encryptor()
        encrypted = encryptor.update(plain) + encryptor.finalize()
        mac = hmac.digest(PUBLIC_KEY + bytes(16), encrypted, "sha256")[:2]
        expected = b"\x15\0" + hashlib.sha256(PUBLIC_KEY).digest()[:1] + mac + encrypted
        self.run_case("public", expected.hex())

    def test_longfast_and_mediumfast_match_independent_aes_ctr_wire_oracle(self):
        for preset, hops in (("LongFast", 3), ("MediumFast", 7)):
            with self.subTest(preset=preset):
                frame = reference_frame(data_text("Node: caf\u00e9".encode()), preset, DEFAULT_KEY,
                                        flags=hops | (hops << 5))
                self.run_case(preset.lower(), frame.hex())

    def test_utf8_size_null_sender_preset_and_no_partial_frame_bounds(self):
        self.run_case("bounds")

    def test_queue_failure_pool_cleanup_temporary_radio_and_parameter_validation(self):
        self.run_case("admission")

    def test_beacon_identity_is_domain_separated_from_normal_chat(self):
        for first in range(16):
            public = bytes([first]) + bytes(range(1, 32))
            raw = int.from_bytes(hashlib.sha256(public + b"beacon").digest()[:4], "little")
            expected = raw if 4 <= raw < 0xFFFFFFFF else 0x0BADF00D
            chat_raw = int.from_bytes(hashlib.sha256(public).digest()[:4], "little")
            chat = chat_raw if 4 <= chat_raw < 0xFFFFFFFF else 0x0BADF00D
            if expected == chat:
                expected = 4 if expected == 0xFFFFFFFE else expected + 1
            self.assertNotEqual(expected, chat)
            self.run_case("identity", public.hex(), str(expected))


if __name__ == "__main__":
    unittest.main()
