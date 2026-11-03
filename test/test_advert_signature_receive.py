#!/usr/bin/env python3
"""Verify received signature bytes and rejection of malformed wire layouts."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/Mesh.cpp"


class AdvertSignatureReceiveTests(unittest.TestCase):
    def test_wire_gate_precedes_both_private_verifier_callers(self):
        source = SOURCE.read_text()
        receive = extract_braced(source, "DispatcherAction Mesh::onRecvPacket(")
        gate = extract_braced(receive, "if (is_advert)")
        after_gate = receive.index(gate) + len(gate)
        first_observer = receive.index("observeForwardedAdvertEcho(pkt)")
        calls = list(re.finditer(r"\bverifyAdvertSignature\s*\(", receive))
        self.assertEqual(len(calls), 2)
        self.assertLess(after_gate, first_observer)
        self.assertTrue(all(first_observer < call.start() for call in calls))
        # Every receive caller must remain below the wire gate, before routing.
        self.assertEqual(len(re.findall(r"\bverifyAdvertSignature\s*\(", source)), 3)
        direct_advert = extract_braced(receive[after_gate:], "if (is_advert)")
        local_advert = extract_braced(receive, "case PAYLOAD_TYPE_ADVERT:")
        for branch in (direct_advert, local_advert):
            self.assertIn("verifyAdvertSignature(pkt, id)", branch)

    def test_signed_bytes_match_public_builder_and_bad_layouts_stop_before_verifying(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        source = SOURCE.read_text()
        helper = extract_braced(source, "static bool verifyAdvertSignature(")
        receive = extract_braced(source, "DispatcherAction Mesh::onRecvPacket(")
        # Execute the entry code as written, before the first receive observer.
        entry = receive[receive.index("{") + 1:
                        receive.index("  observeForwardedAdvertEcho(pkt);")]
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <helpers/AdvertDataLayout.h>
#include <helpers/AdvertSignature.h>

constexpr unsigned PAYLOAD_TYPE_ADVERT = 4;
constexpr uint32_t ACTION_RELEASE = 0;
struct Packet {
  uint8_t* payload;
  uint16_t payload_len;
  unsigned getPayloadType() const { return PAYLOAD_TYPE_ADVERT; }
  bool violatesRoutePolicy() const { return false; }
};
struct Identity {
  const uint8_t* signature = nullptr;
  const uint8_t* message = nullptr;
  int length = 0;
  bool result = true;
  mutable unsigned calls = 0;
  bool verify(const uint8_t* sig, const uint8_t* text, int size) const {
    ++calls;
    assert(sig == signature && size == length);
    assert(!memcmp(text, message, size));
    return result;
  }
};
@HELPER@

static uint32_t receiveEntry(Packet* pkt, const Identity& identity) {
@ENTRY@
  return verifyAdvertSignature(pkt, identity) ? 1 : ACTION_RELEASE;
}

int main() {
  uint8_t reference[AdvertSignature::kMaxMessageLen];
  uint8_t golden[AdvertSignature::kMaxMessageLen];
  const uint8_t* signature = nullptr;
  // Every legal signed-message size, with every possible byte value and both
  // verification results, must match the original two-copy wire recipe.
  for (unsigned app_size = 0; app_size <= MAX_ADVERT_DATA_SIZE; ++app_size) {
    for (unsigned seed = 0; seed <= UINT8_MAX; ++seed) {
      std::vector<uint8_t> bytes(AdvertSignature::kHeaderLen + app_size);
      for (size_t i = 0; i < bytes.size(); ++i) bytes[i] = uint8_t(seed + i * 73);
      const auto before = bytes;
      Packet packet{bytes.data(), uint16_t(bytes.size())};
      memcpy(golden, bytes.data(), PUB_KEY_SIZE + 4);
      memcpy(golden + PUB_KEY_SIZE + 4,
             bytes.data() + PUB_KEY_SIZE + 4 + SIGNATURE_SIZE, app_size);
      const int golden_length = PUB_KEY_SIZE + 4 + int(app_size);
      const int length = AdvertSignature::buildMessage(
          bytes.data(), packet.payload_len, reference, &signature);
      assert(length == golden_length && !memcmp(reference, golden, length));
      assert(signature == bytes.data() + PUB_KEY_SIZE + 4);
      Identity identity{bytes.data() + PUB_KEY_SIZE + 4, golden, golden_length};
      for (bool result : {false, true}) {
        identity.result = result;
        assert(verifyAdvertSignature(&packet, identity) == result);
      }
      assert(identity.calls == 2 && bytes == before);
    }
  }

  // These are the protocol's required prefix sizes for the eight combinations
  // of location/feature flags. Name and future type bits add no fixed bytes.
  constexpr unsigned required[] = {1, 9, 3, 11, 3, 11, 5, 13};
  for (unsigned wire_size = 0; wire_size <= MAX_PACKET_PAYLOAD; ++wire_size) {
    for (unsigned flags = 0; flags <= UINT8_MAX; ++flags) {
      std::vector<uint8_t> bytes(wire_size, 0xa5);
      if (wire_size > unsigned(AdvertSignature::kHeaderLen)) {
        bytes[AdvertSignature::kHeaderLen] = uint8_t(flags);
      }
      Packet packet{bytes.data(), uint16_t(wire_size)};
      const int app_size = int(wire_size) - AdvertSignature::kHeaderLen;
      const bool valid = app_size >= 0 && app_size <= MAX_ADVERT_DATA_SIZE
          && (app_size == 0 || unsigned(app_size) >= required[(flags >> 4) & 7]);
      Identity identity;
      if (valid) {
        memcpy(golden, bytes.data(), PUB_KEY_SIZE + 4);
        memcpy(golden + PUB_KEY_SIZE + 4,
               bytes.data() + PUB_KEY_SIZE + 4 + SIGNATURE_SIZE, app_size);
        identity.length = PUB_KEY_SIZE + 4 + app_size;
        const int length = AdvertSignature::buildMessage(
            bytes.data(), wire_size, reference, &signature);
        assert(length == identity.length && !memcmp(reference, golden, length));
        assert(signature == bytes.data() + PUB_KEY_SIZE + 4);
        identity.signature = bytes.data() + PUB_KEY_SIZE + 4;
        identity.message = golden;
      }
      assert(receiveEntry(&packet, identity) == unsigned(valid));
      assert(identity.calls == unsigned(valid));
    }
  }
  // Check the entire remaining uint16_t length domain. No invalid length may
  // reach payload bytes, even when the claimed payload has no backing buffer.
  for (unsigned length = MAX_PACKET_PAYLOAD + 1; length <= UINT16_MAX; ++length) {
    Packet packet{nullptr, uint16_t(length)};
    Identity identity;
    assert(receiveEntry(&packet, identity) == ACTION_RELEASE);
    assert(identity.calls == 0);
  }
  puts("PASS: receive signature bytes and layout preconditions");
}
'''.replace("@HELPER@", helper).replace("@ENTRY@", entry)
        with tempfile.TemporaryDirectory(prefix="meshcore-advert-signature-") as directory:
            work = Path(directory)
            cpp, binary = work / "test.cpp", work / "test"
            cpp.write_text(harness)
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run([
                compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter",
                *sanitizers, "-I" + str(ROOT / "src"), str(cpp), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("PASS: receive signature bytes and layout preconditions", checked.stdout)


if __name__ == "__main__":
    unittest.main()
