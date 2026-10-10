#!/usr/bin/env python3
"""Exercise the real on-device transport codec without PlatformIO or hardware."""

from pathlib import Path
import os
import random
import shutil
import subprocess
import tempfile
import unittest
import zlib

from test_t096_full_memory import method


ROOT = Path(__file__).resolve().parents[1]

CODEC_RUNNER = r'''
#include <helpers/ota/OtaDeflate.h>
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
using namespace mesh::ota;
static bool fail_allocation = false;
static void* active_allocation = nullptr;
static unsigned allocation_calls = 0;
extern "C" void* __real_calloc(size_t, size_t);
extern "C" void __real_free(void*);
extern "C" void* __wrap_calloc(size_t n, size_t size) {
  ++allocation_calls;
  if (fail_allocation) return nullptr;
  assert(!active_allocation);
  active_allocation = __real_calloc(n, size);
  return active_allocation;
}
extern "C" void __wrap_free(void* pointer) {
  if (pointer && pointer == active_allocation) active_allocation = nullptr;
  __real_free(pointer);
}
static std::vector<uint8_t> unhex(const std::string& hex) {
  std::vector<uint8_t> bytes;
  if (hex == "-") return bytes;
  assert(hex.size() % 2 == 0);
  for (size_t i = 0; i < hex.size(); i += 2)
    bytes.push_back(static_cast<uint8_t>(std::stoul(hex.substr(i, 2), nullptr, 16)));
  return bytes;
}
static std::string hex(const uint8_t* bytes, unsigned len) {
  if (!len) return "-";
  std::ostringstream out;
  for (unsigned i = 0; i < len; ++i)
    out << std::hex << std::setw(2) << std::setfill('0') << unsigned(bytes[i]);
  return out.str();
}
static void invalid_arguments() {
  uint8_t source[32] = {}, destination[32] = {};
  uint16_t length = 0xBEEF;
  assert(!ota_transport_deflate(nullptr, nullptr, 16, destination, 32, &length));
  assert(length == 0);
  length = 0xBEEF;
  assert(!ota_transport_deflate(nullptr, source, 0, destination, 32, &length));
  assert(length == 0);
  length = 0xBEEF;
  assert(!ota_transport_deflate(nullptr, source, 16, nullptr, 32, &length));
  assert(length == 0);
  length = 0xBEEF;
  assert(!ota_transport_deflate(nullptr, source, 16, destination, 0, &length));
  assert(length == 0);
  assert(!ota_transport_deflate(nullptr, source, 16, destination, 32, nullptr));
  length = 0xBEEF;
  assert(!ota_transport_deflate(nullptr, source, 2049, destination, 32, &length));
  assert(length == 0);
  for (int displacement : {-8, 0, 1, 8}) {
    uint8_t shared[128];
    memset(shared, 0x71, sizeof(shared));
    uint8_t expected[128]; memcpy(expected, shared, sizeof(shared));
    length = 0xBEEF;
    assert(!ota_transport_deflate(nullptr, shared + 32, 32,
                                 shared + 32 + displacement, 32, &length));
    assert(length == 0 && memcmp(expected, shared, sizeof(shared)) == 0);
  }
  assert(!active_allocation);
}
int main() {
  std::string operation, input;
  unsigned capacity = 0;
  while (std::cin >> operation) {
    if (operation == "invalid") {
      invalid_arguments(); std::cout << "OK\n"; continue;
    }
    assert(std::cin >> input >> capacity);
    assert(capacity <= 65535);
    auto bytes = unhex(input);
    assert(bytes.size() <= 65535);
    std::vector<uint8_t> source(bytes.size() + 32, 0xC3);
    if (!bytes.empty()) memcpy(source.data() + 16, bytes.data(), bytes.size());
    const auto original = source;
    std::vector<uint8_t> destination(capacity + 32, 0xA5);
    uint16_t produced = 0xBEEF;
    fail_allocation = operation == "oom";
    allocation_calls = 0;
    bool ok;
    if (operation == "decode")
      ok = ota_transport_inflate(nullptr, source.data() + 16, bytes.size(),
                                 destination.data() + 16, capacity, &produced);
    else
      ok = ota_transport_deflate(nullptr, source.data() + 16, bytes.size(),
                                 destination.data() + 16, capacity, &produced);
    fail_allocation = false;
    assert(!active_allocation);
    assert(source == original);
    assert(produced <= capacity);
    if (!ok) assert(produced == 0);
    if (operation == "oom") assert(!ok && allocation_calls > 0);
    for (unsigned i = 0; i < 16; ++i) {
      assert(destination[i] == 0xA5);
      assert(destination[16 + capacity + i] == 0xA5);
    }
    std::cout << ok << ' ' << produced << ' '
              << hex(destination.data() + 16, produced) << '\n';
  }
}
'''


MANAGER_RUNNER = r'''
#include <helpers/ota/OtaContext.h>
#include <helpers/ota/OtaByteIO.h>
#include <SHA256.h>
#include <cassert>
#include <deque>
#include <iostream>
#include <map>
#include <string>
#include <vector>
using namespace mesh::ota;
#define OTA_SELF_LEAVES_MAX 65536u
static std::vector<uint8_t> firmware;
static std::vector<size_t> allocations;
static void* self_malloc(size_t bytes) {
  allocations.push_back(bytes);
  return malloc(bytes);
}
namespace mesh { namespace ota {
bool ota_self_firmware(SelfFwInfo& info) {
  info = SelfFwInfo(); info.valid = true; info.image_len = firmware.size();
  info.target_id = 123; info.fw_version = 2;
  strcpy(info.hw_id, "test");
  return true;
}
bool ota_self_read(uint32_t off, uint8_t* buf, uint32_t len) {
  if (uint64_t(off) + len > firmware.size()) return false;
  memcpy(buf, firmware.data() + off, len);
  return true;
}
static bool self_read_cb(void*, uint32_t off, uint8_t* buf, uint32_t len) {
  return ota_self_read(off, buf, len);
}
#define malloc self_malloc
#include "self_serve.h"
#undef malloc
} }
struct Message { OtaManager* destination; std::vector<uint8_t> data; };
struct Route { OtaManager* destination; bool source; };
static std::deque<Message> messages;
static unsigned data_packets = 0, deflated_packets = 0, raw_v2_packets = 0, legacy_packets = 0;
static bool drop_one = false, dropped = false;
static std::map<unsigned, std::vector<uint8_t>> representations;
static bool send(void* context, const uint8_t* bytes, uint16_t len, bool) {
  const Route& route = *static_cast<Route*>(context);
  if (route.source && len && bytes[0] == OTA_DATA) {
    ++data_packets;
    DataMsg data;
    assert(decode_data(bytes, len, data));
    if (data.frag_off & OTA_DATA_V2_MARK) {
      uint8_t fragment; uint16_t length; bool deflated;
      const unsigned raw_length = std::min<size_t>(2048, firmware.size() - data.block_idx*2048);
      assert(ota_data_v2_unpack_extended(data.frag_off, raw_length, fragment, length, deflated));
      assert(data.data_len > 4 && data.data_len <= 175);
      const std::vector<uint8_t> identity(data.data, data.data + 4);
      if (representations.count(data.block_idx)) assert(representations[data.block_idx] == identity);
      else representations[data.block_idx] = identity;
      if (deflated) ++deflated_packets; else ++raw_v2_packets;
      if (drop_one && !dropped && data.block_idx == 0 && fragment == 1) {
        dropped = true; return true;
      }
    } else {
      ++legacy_packets;
      assert(data.data_len > 0 && data.data_len <= 160);
    }
  }
  messages.push_back({route.destination, {bytes, bytes + len}});
  return true;
}
static bool reject_encoder(void*, const uint8_t*, uint16_t, uint8_t* out,
                           uint16_t cap, uint16_t* len) {
  if (cap) memset(out, 0x42, cap);
  *len = 0; return false;
}
int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  const unsigned tail = scenario == "tail1" ? 1 : scenario == "tail2" ? 2 : 360;
  firmware.resize(2048 + tail);
  uint32_t rng = 0xDEAD1234;
  for (unsigned i = 0; i < firmware.size(); ++i) {
    rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
    firmware[i] = scenario == "incompressible" ? uint8_t(rng) :
                  scenario == "retry" ? uint8_t(rng & 15) : uint8_t(i % 32);
  }
  OtaContext source;
  OtaManager receiver;
  OtaStoreRam<8192> store;
  Route to_receiver{&receiver, true}, to_source{&source.manager, false};
  source.begin(123, send, &to_receiver, "test");
#if defined(OTA_SEEDER_ONLY) || defined(NRF52_PLATFORM)
  // These fixture variants are raw diagnostic exports, not automatically
  // offered full own-images: Companion source-only or internal-only nRF52.
  assert(!source.self_serve_supported);
#else
  assert(source.self_serve_supported);
#endif
  allocations.clear();
  assert(ota_serve_self(source, 2));
#if MESHCORE_OTA_DEVICE_DEFLATE
  assert(allocations.size() == 2 && allocations[0] == 8 && allocations[1] == 2048);
#else
  // No encoder means no extra output allocation. The original proof working
  // area remains exactly block_count*4 even for a tiny two-block own-image.
  assert(allocations.size() == 2 && allocations[0] == 8 && allocations[1] == 8);
#endif
  if (scenario == "rejected") source.manager.set_transport_deflate_encoder(reject_encoder);
  receiver.begin(123, send, &to_source);
  receiver.set_accept_full(true);
  receiver.set_fetch_store(&store);
  if (scenario != "rawv2" && scenario != "legacy")
    receiver.set_transport_deflate_decoder(ota_transport_inflate);
  if (scenario == "legacy") receiver.set_wire_v2_enabled(false);
  drop_one = scenario == "retry";
  receiver.pull(source.serve_self_manifest + 20, 123);
  for (unsigned step = 1; step <= 10000 && receiver.fetchState() != OtaManager::COMPLETE; ++step) {
    source.manager.set_clock(step*20); receiver.set_clock(step*20);
    source.manager.loop(); receiver.loop();
    source.manager.serviceEgress(); receiver.serviceEgress();
    unsigned delivered = 0;
    while (!messages.empty()) {
      assert(++delivered < 100);
      Message message = std::move(messages.front()); messages.pop_front();
      message.destination->on_message(message.data.data(), message.data.size());
    }
    assert(receiver.fetchState() != OtaManager::FAILED);
  }
  assert(receiver.fetchState() == OtaManager::COMPLETE);
  MotaManifest staged;
  assert(mota_parse(store.data(), store.staged_size(), staged));
  assert(staged.payload_size == firmware.size());
  assert(memcmp(staged.payload, firmware.data(), firmware.size()) == 0);
  if (scenario == "legacy") {
    assert(legacy_packets > 0 && !raw_v2_packets && !deflated_packets);
    assert(data_packets == 13 + (tail + 159)/160);
  } else if (scenario == "rawv2" || scenario == "incompressible" || scenario == "rejected" ||
             scenario == "disabled") {
    assert(raw_v2_packets > 0 && !legacy_packets && !deflated_packets);
    assert(data_packets == 12 + (tail + 170)/171);
  } else {
    assert(deflated_packets > 0 && !legacy_packets);
    // Deliberately lossy runs include retransmissions; compare clean geometry
    // only for the loss-free cases, not actual TX counts with a dropped packet.
    if (scenario != "retry") assert(data_packets < 12 + (tail + 170)/171);
    if (tail < 3) assert(raw_v2_packets == 1);
    if (scenario == "retry") assert(dropped);
  }
  std::cout << scenario << ": " << data_packets << " DATA, " << deflated_packets
            << " compressed; strict decoder + Merkle + original bytes OK\n";
}
'''


class DeviceDeflateEncoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ota-device-deflate-")
        cls.path = Path(cls.directory.name)
        source = cls.path / "codec.cpp"
        source.write_text(CODEC_RUNNER)
        flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        tinf = cls.path / "tinf.o"
        compiled = subprocess.run([
            shutil.which("cc") or "gcc", "-std=c99", "-Os", *flags,
            "-DOTA_TRANSPORT_DEFLATE_TEST=1", "-c",
            str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(tinf),
        ], capture_output=True, text=True)
        if compiled.returncode:
            cls.directory.cleanup()
            raise AssertionError(compiled.stderr)
        cls.binary = cls.path / "codec.exe"
        compiled = subprocess.run([
            "c++", "-std=c++17", "-Os", *flags, "-DOTA_TRANSPORT_DEFLATE_TEST=1",
            "-I", str(ROOT / "src"), str(source),
            str(ROOT / "src/helpers/ota/OtaDeflate.cpp"), str(tinf),
            "-Wl,--wrap=calloc", "-Wl,--wrap=free", "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if compiled.returncode:
            cls.directory.cleanup()
            raise AssertionError(compiled.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def exchange(self, requests):
        lines = [f"{operation} {data.hex() or '-'} {capacity}"
                 for operation, data, capacity in requests]
        result = subprocess.run([str(self.binary)], input="\n".join(lines) + "\n",
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        replies = result.stdout.splitlines()
        self.assertEqual(len(replies), len(requests))
        decoded = []
        for reply in replies:
            success, length, encoded = reply.split()
            data = b"" if encoded == "-" else bytes.fromhex(encoded)
            self.assertEqual(len(data), int(length))
            decoded.append((success == "1", data))
        return decoded

    def roundtrips(self, blocks, require_savings=False):
        encoded = self.exchange([("encode", block, len(block)) for block in blocks])
        decode_requests, expected = [], []
        for block, (success, stream) in zip(blocks, encoded):
            if require_savings:
                self.assertTrue(success, f"No saving for {len(block)} byte patterned block")
            if not success:
                self.assertEqual(stream, b"")
                continue
            self.assertLess(len(stream), len(block))
            inflater = zlib.decompressobj(wbits=-15)
            self.assertEqual(inflater.decompress(stream) + inflater.flush(), block)
            self.assertTrue(inflater.eof)
            self.assertEqual(inflater.unused_data, b"")
            self.assertEqual(inflater.unconsumed_tail, b"")
            decode_requests.append(("decode", stream, len(block)))
            expected.append(block)
        if decode_requests:
            for block, (success, decoded) in zip(expected, self.exchange(decode_requests)):
                self.assertTrue(success)
                self.assertEqual(decoded, block)
        return encoded

    def test_firmware_like_patterns_roundtrip_both_existing_decoders(self):
        patterns = [b"\x00" * 2048, b"\xff" * 2048,
                    (b"\x36\x41\x00\x22\xa1\xff\x0c\x3d" * 256),
                    (b"MeshCore repeater ota self target radio2 TempRadio\x00" * 48)[:2048],
                    bytes(range(256)) * 8,
                    (b"\x00\xff\x10\x00\xff\x20" * 342)[:2048]]
        self.roundtrips(patterns, require_savings=True)

    def test_all_small_and_fragment_boundary_tails_are_bounded(self):
        sizes = list(range(0, 33)) + [170, 171, 172, 341, 342, 343, 359, 360, 361,
                                     1023, 1024, 1025, 1880, 1881, 1882, 2047, 2048]
        blocks = [(b"abcd" * 512)[:size] for size in sizes]
        result = self.roundtrips(blocks)
        for size, (success, encoded) in zip(sizes, result):
            if size < 3:
                self.assertFalse(success)
                self.assertEqual(encoded, b"")

    def test_random_and_low_entropy_blocks_roundtrip_or_fall_back(self):
        rng = random.Random(0x90955055)
        blocks = []
        for length in [1, 2, 3, 17, 171, 360, 1024, 2048]:
            for _ in range(8):
                blocks.append(rng.randbytes(length))
                blocks.append(bytes(rng.randrange(8) for _ in range(length)))
        encoded = self.roundtrips(blocks)
        self.assertGreater(sum(success for success, _ in encoded), 0)
        self.assertGreater(sum(not success for success, _ in encoded), 0)

    def test_output_capacity_exact_boundary_and_canaries(self):
        rng = random.Random(4711)
        block = bytes(rng.randrange(16) for _ in range(2048))
        success, stream = self.roundtrips([block], require_savings=True)[0]
        self.assertTrue(success)
        caps = sorted({0, 1, 2, 3, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128,
                       255, 256, len(stream)-1, len(stream), len(stream)+1, 2048, 4096})
        for cap, (success, bounded) in zip(caps, self.exchange([
                ("encode", block, cap) for cap in caps])):
            if cap < len(stream):
                self.assertFalse(success)
                self.assertEqual(bounded, b"")
            else:
                self.assertTrue(success)
                self.assertEqual(bounded, stream)

    def test_incompressible_full_block_returns_raw_fallback_without_heap_leak(self):
        block = random.Random(12).randbytes(2048)
        for success, stream in self.exchange([("encode", block, 2048)] * 32):
            self.assertFalse(success)
            self.assertEqual(stream, b"")

    def test_deterministic_independent_blocks_and_allocation_failure_recovery(self):
        first = (b"MeshCore\x00OTA\x00" * 256)[:2048]
        second = (bytes(range(64)) * 32)
        requests = [("encode", first, 2048), ("encode", second, 2048),
                    ("oom", first, 2048), ("encode", first, 2048),
                    ("encode", second, 2048), ("encode", first, 2048)]
        result = self.exchange(requests)
        self.assertEqual(result[0], result[3])
        self.assertEqual(result[0], result[5])
        self.assertEqual(result[1], result[4])
        self.assertEqual(result[2], (False, b""))
        for request, (success, stream) in zip(requests, result):
            if success:
                self.assertEqual(zlib.decompress(stream, wbits=-15), request[1])

    def test_invalid_and_overlapping_arguments_fail_before_writing(self):
        result = subprocess.run([str(self.binary)], input="invalid\n", capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "OK\n")

    def test_existing_decoder_stays_strict_for_encoded_streams(self):
        block = b"\x00" * 2048
        _, stream = self.roundtrips([block], require_savings=True)[0]
        requests = [("decode", stream[:-1], 2048), ("decode", stream + b"\x00", 2048),
                    ("decode", stream, 2047), ("decode", stream, 2049)]
        for success, output in self.exchange(requests):
            self.assertFalse(success)
            self.assertEqual(output, b"")

    def test_packet_savings_count_headers_and_final_tail(self):
        def packets(length):
            return (length + 170) // 171
        self.assertEqual(packets(2048), 12)
        self.assertEqual(packets(1882), 12)
        self.assertEqual(packets(1881), 11)
        self.assertEqual(packets(360), 3)
        self.assertEqual(packets(342), 2)
        self.assertEqual(packets(171), 1)
        self.assertEqual(811 * packets(2048) + packets(360), 9735)
        blocks = [b"\x00" * 2048, b"\xff" * 360]
        for block, (success, stream) in zip(blocks, self.roundtrips(blocks, True)):
            self.assertTrue(success)
            self.assertGreater(packets(len(block)) - packets(len(stream)), 0)
            self.assertGreater(len(block) + 15*packets(len(block)) -
                               len(stream) - 15*packets(len(stream)), 0)


class DeviceDeflateManagerTest(unittest.TestCase):
    def compile_and_run(self, defines, scenarios):
        with tempfile.TemporaryDirectory(prefix="ota-device-wire-") as directory:
            path = Path(directory)
            (path / "self_serve.h").write_text(method(
                (ROOT / "src/helpers/ota/OtaSelf.cpp").read_text(),
                "bool ota_serve_self(OtaContext& c, uint32_t fw_version)"))
            source = path / "manager.cpp"
            source.write_text(MANAGER_RUNNER)
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
            tinf = path / "tinf.o"
            result = subprocess.run([
                shutil.which("cc") or "gcc", "-std=c99", "-Os", *flags, *defines,
                "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(tinf),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            binary = path / "manager.exe"
            sources = ["OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp",
                       "MerkleTree.cpp", "OtaDeflate.cpp"]
            result = subprocess.run([
                "c++", "-std=c++17", "-Os", *flags, *defines,
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                str(source), *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                str(ROOT / "src/Utils.cpp"), str(tinf),
                            str(ROOT / "test/fixtures/ota_security/identity_verify.cpp"),
                            "-lcrypto", "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for scenario in scenarios:
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), scenario], capture_output=True,
                                            text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_self_source_negotiation_fallback_small_scratch_and_retry(self):
        # Match the positively qualified production recipe: the common
        # pre-script enables this flag uniformly for all translation units.
        self.compile_and_run(["-DENABLE_OTA=1", "-DESP32_PLATFORM=1",
                              "-DMESHCORE_OTA_DEVICE_DEFLATE=1"],
                             ("small", "tail1", "tail2", "rawv2", "legacy",
                              "incompressible", "rejected", "retry"))

    def test_disabled_internal_nrf52_and_companion_keep_original_proof_allocation(self):
        for defines in (
                ["-DENABLE_OTA=1", "-DESP32_PLATFORM=1", "-DMESHCORE_OTA_DEVICE_DEFLATE=0"],
                ["-DENABLE_OTA=1", "-DNRF52_PLATFORM=1"],
                ["-DENABLE_OTA=1", "-DESP32_PLATFORM=1", "-DOTA_SEEDER_ONLY=1"],
                ["-DENABLE_OTA=1", "-DNRF52_PLATFORM=1", "-DOTA_SEEDER_ONLY=1"],
        ):
            with self.subTest(defines=defines):
                self.compile_and_run(defines, ("disabled",))


if __name__ == "__main__":
    unittest.main()
