#!/usr/bin/env python3
"""Focused tests for the reproducible RAK3401 receive-inflate builder."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_rak3401_inflate_bridges as inflate


REPO_ROOT = Path(__file__).resolve().parents[2]


MOCK_MANAGER_HEADER = r'''#pragma once

#include <stdint.h>
#include <string.h>
#include <vector>

#define OTA_MAX_BLOCK 2048
#define OTA_FRAG_DATA 160
#define OTA_FRAG_DATA_V2 171
#define OTA_FETCH_PIPELINE 1

#include "OtaProtocol.h"

namespace mesh {
namespace ota {

typedef void (*OtaSend)(void*, const uint8_t*, uint16_t, bool);

class OtaManager {
public:
  void begin(uint32_t target_id, OtaSend send, void* send_ctx) {
    (void)target_id;
    _send = send;
    _send_ctx = send_ctx;
  }

  void configure(const uint8_t mid[4], uint16_t block, uint16_t block_len,
                 uint16_t nominal_size) {
    memcpy(_mid, mid, sizeof(_mid));
    _block = block;
    _block_len = block_len;
    _nominal_size = nominal_size;
    logical.assign(block_len, 0);
    offsets.clear();
    invalid = false;
  }

  bool request(uint16_t want_mask) {
    if (!_send) return false;
    ReqMsg request;
    memcpy(request.manifest_id, _mid, sizeof(request.manifest_id));
    request.block_idx = _block;
    request.want_mask = want_mask;
    uint8_t wire[9];
    const uint16_t wire_len = encode_req(wire, sizeof(wire), request);
    if (wire_len == 0) return false;
    _send(_send_ctx, wire, wire_len, false);
    return true;
  }

  uint16_t requestedBlockLength(const uint8_t* manifest_id, uint16_t block,
                                uint16_t* nominal_size = nullptr) const {
    if (nominal_size) *nominal_size = 0;
    if (!manifest_id || block != _block ||
        memcmp(manifest_id, _mid, sizeof(_mid)) != 0) return 0;
    if (nominal_size) *nominal_size = _nominal_size;
    return _block_len;
  }

  void on_message(const uint8_t* msg, uint16_t len) {
    DataMsg data;
    if (!decode_data(msg, len, data) ||
        memcmp(data.manifest_id, _mid, sizeof(_mid)) != 0 ||
        data.block_idx != _block || (data.frag_off & OTA_DATA_V2_MARK) != 0 ||
        data.frag_off >= _block_len) {
      invalid = true;
      return;
    }
    uint16_t expected = (uint16_t)(_block_len - data.frag_off);
    if (expected > OTA_FRAG_DATA) expected = OTA_FRAG_DATA;
    if (data.frag_off % OTA_FRAG_DATA != 0 || data.data_len != expected) {
      invalid = true;
      return;
    }
    memcpy(logical.data() + data.frag_off, data.data, data.data_len);
    offsets.push_back(data.frag_off);
  }

  std::vector<uint8_t> logical;
  std::vector<uint16_t> offsets;
  bool invalid = false;

private:
  OtaSend _send = nullptr;
  void* _send_ctx = nullptr;
  uint8_t _mid[4] = {0};
  uint16_t _block = 0;
  uint16_t _block_len = 0;
  uint16_t _nominal_size = 0;
};

} // namespace ota
} // namespace mesh
'''


MOCK_MULTIHASH_HEADER = r'''#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh {
namespace ota {

inline void mh4(uint8_t out[4], const uint8_t* data, size_t len) {
  uint32_t hash = 2166136261u;
  for (size_t i = 0; i < len; ++i) {
    hash ^= data[i];
    hash *= 16777619u;
  }
  out[0] = (uint8_t)hash;
  out[1] = (uint8_t)(hash >> 8);
  out[2] = (uint8_t)(hash >> 16);
  out[3] = (uint8_t)(hash >> 24);
}

} // namespace ota
} // namespace mesh
'''


BRIDGE_BEHAVIOR_HARNESS = r'''#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vector>

#include "Multihash.h"
#include "OtaDeflate.h"

using namespace mesh::ota;

namespace {

struct Capture {
  std::vector<std::vector<uint8_t> > packets;
};

void capture_send(void* context, const uint8_t* msg, uint16_t len, bool flood) {
  (void)flood;
  Capture* capture = static_cast<Capture*>(context);
  capture->packets.push_back(std::vector<uint8_t>(msg, msg + len));
}

std::vector<uint8_t> make_payload(uint16_t total, uint16_t random_prefix) {
  std::vector<uint8_t> payload(total, 'A');
  uint32_t state = 0xc001d00du ^ total;
  for (uint16_t i = 0; i < random_prefix; ++i) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    payload[i] = (uint8_t)state;
  }
  return payload;
}

std::vector<uint8_t> make_deflate(const std::vector<uint8_t>& payload,
                                  uint16_t random_prefix) {
  // Non-final stored block for the pseudo-random prefix, followed by a tiny
  // final fixed-Huffman block for the repeated 'A' suffix.  This deliberately
  // keeps the 2 KiB representation long enough to exercise fragment index 9.
  static const uint8_t tail_304[] = {0x73, 0x74, 0x1c, 0x05, 0xa4, 0x00, 0x00};
  static const uint8_t tail_512[] = {0x73, 0x74, 0x1c, 0x05, 0x23, 0x19, 0x00, 0x00};
  const uint8_t* tail = payload.size() == 1024 ? tail_304 : tail_512;
  const size_t tail_len = payload.size() == 1024 ? sizeof(tail_304) : sizeof(tail_512);

  std::vector<uint8_t> encoded;
  encoded.reserve((size_t)random_prefix + 5u + tail_len);
  encoded.push_back(0x00); // BFINAL=0, stored block, then align to the next byte.
  encoded.push_back((uint8_t)random_prefix);
  encoded.push_back((uint8_t)(random_prefix >> 8));
  const uint16_t complement = (uint16_t)~random_prefix;
  encoded.push_back((uint8_t)complement);
  encoded.push_back((uint8_t)(complement >> 8));
  encoded.insert(encoded.end(), payload.begin(), payload.begin() + random_prefix);
  encoded.insert(encoded.end(), tail, tail + tail_len);
  return encoded;
}

int run_case(uint16_t block_len, uint16_t random_prefix, bool extended) {
  const uint8_t mid[4] = {0x21, 0x43, 0x65, (uint8_t)(extended ? 0x87 : 0x76)};
  const uint16_t block = extended ? 9 : 4;
  const uint16_t canonical_fragments =
      (uint16_t)((block_len + OTA_FRAG_DATA - 1u) / OTA_FRAG_DATA);
  const uint16_t original_want =
      (uint16_t)((1u << canonical_fragments) - 1u);

  OtaManager manager;
  manager.configure(mid, block, block_len, block_len);
  Capture capture;
  OtaTransportInflateReceiver receiver;
  receiver.begin(manager, 0x2fa509c1u, capture_send, &capture);
  if (!manager.request(original_want) || capture.packets.size() != 1) return 10;

  ReqMsg request;
  if (!decode_req(capture.packets[0].data(),
                  (uint16_t)capture.packets[0].size(), request)) return 11;
  const uint16_t expected_want = ota_req_make_v2(original_want, true, extended);
  if (request.want_mask != expected_want || !ota_req_is_v2(request.want_mask) ||
      ota_req_v2_extended_length(request.want_mask) != extended) return 12;
  if ((!extended && request.want_mask != 0xc07fu) ||
      (extended && request.want_mask != 0xefffu)) return 13;

  const std::vector<uint8_t> payload = make_payload(block_len, random_prefix);
  const std::vector<uint8_t> encoded = make_deflate(payload, random_prefix);
  if (encoded.size() >= payload.size()) return 14;
  const uint16_t wire_fragments =
      (uint16_t)((encoded.size() + OTA_FRAG_DATA_V2 - 1u) / OTA_FRAG_DATA_V2);
  if ((!extended && wire_fragments != 5) || (extended && wire_fragments != 10)) return 15;

  uint8_t stream_id[4];
  mh4(stream_id, encoded.data(), encoded.size());
  // Reverse order proves that completion is driven by the descriptor bitmap,
  // rather than accidentally relying on sequential arrival.
  for (int fragment = (int)wire_fragments - 1; fragment >= 0; --fragment) {
    const uint16_t offset = (uint16_t)(fragment * OTA_FRAG_DATA_V2);
    uint16_t slice = (uint16_t)(encoded.size() - offset);
    if (slice > OTA_FRAG_DATA_V2) slice = OTA_FRAG_DATA_V2;

    uint16_t descriptor = 0;
    const bool packed = extended
        ? ota_data_v2_pack_extended((uint8_t)fragment,
                                    (uint16_t)encoded.size(), descriptor)
        : ota_data_v2_pack((uint8_t)fragment, (uint16_t)encoded.size(),
                           true, descriptor);
    if (!packed) return 16;

    std::vector<uint8_t> body(OTA_DATA_V2_STREAM_ID_BYTES + slice);
    memcpy(body.data(), stream_id, sizeof(stream_id));
    memcpy(body.data() + OTA_DATA_V2_STREAM_ID_BYTES,
           encoded.data() + offset, slice);
    DataMsg data;
    memcpy(data.manifest_id, mid, sizeof(data.manifest_id));
    data.block_idx = block;
    data.frag_off = descriptor;
    data.data = body.data();
    data.data_len = (uint16_t)body.size();
    std::vector<uint8_t> wire(9u + body.size());
    const uint16_t wire_len = encode_data(wire.data(), (uint16_t)wire.size(), data);
    if (wire_len != wire.size()) return 17;
    receiver.on_message(wire.data(), wire_len);
  }

  if (manager.invalid || manager.logical != payload ||
      manager.offsets.size() != canonical_fragments) return 18;
  for (uint16_t fragment = 0; fragment < canonical_fragments; ++fragment) {
    if (manager.offsets[fragment] != fragment * OTA_FRAG_DATA) return 19;
  }
  return 0;
}

} // namespace

int main() {
  int result = run_case(1024, 720, false);
  if (result != 0) {
    fprintf(stderr, "1 KiB bridge case failed: %d\n", result);
    return result;
  }
  result = run_case(2048, 1536, true);
  if (result != 0) {
    fprintf(stderr, "2 KiB bridge case failed: %d\n", result);
    return 100 + result;
  }
  return 0;
}
'''


class InflateAssetTests(unittest.TestCase):
    def test_vendored_snapshot_assets_match_their_pins(self) -> None:
        metadata = inflate.inflate_asset_metadata()

        self.assertEqual(set(metadata), set(inflate.INFLATE_ASSETS))
        for destination, (_asset, expected_hash) in inflate.INFLATE_ASSETS.items():
            self.assertEqual(metadata[destination]["sha256"], expected_hash)

    def test_install_and_remove_are_exact_inverses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            inflate.install_inflate_assets(source)
            for destination, (_asset, expected_hash) in inflate.INFLATE_ASSETS.items():
                self.assertEqual(
                    inflate.sha256_file(source / destination), expected_hash
                )

            inflate.remove_inflate_assets(source)
            self.assertFalse((source / "src/helpers/ota/tinf").exists())
            for destination in inflate.INFLATE_ASSETS:
                self.assertFalse((source / destination).exists())

    def test_receiver_negotiates_both_v2_descriptor_layouts(self) -> None:
        source = (inflate.INFLATE_ASSET_ROOT / "OtaDeflate.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("OTA_DATA_V2_LEGACY_MAX_ENCODED", source)
        self.assertIn("ota_data_v2_unpack_extended", source)
        self.assertIn("&nominal_block_size", source)
        self.assertIn(
            "nominal_block_size > OTA_DATA_V2_LEGACY_MAX_ENCODED", source
        )

    def test_installed_receiver_reassembles_deflated_1k_and_2k_blocks(self) -> None:
        cxx = shutil.which("c++") or shutil.which("g++")
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cxx or not cc:
            self.skipTest("a host C and C++ compiler are required")

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            inflate.install_inflate_assets(source)
            ota_dir = source / "src" / "helpers" / "ota"
            for filename in ("OtaFormat.h", "OtaProtocol.h", "OtaProtocol.cpp"):
                shutil.copy2(REPO_ROOT / "src" / "helpers" / "ota" / filename,
                             ota_dir / filename)
            (ota_dir / "OtaManager.h").write_text(
                MOCK_MANAGER_HEADER, encoding="ascii"
            )
            (ota_dir / "Multihash.h").write_text(
                MOCK_MULTIHASH_HEADER, encoding="ascii"
            )
            harness = source / "bridge_behavior.cpp"
            harness.write_text(BRIDGE_BEHAVIOR_HARNESS, encoding="ascii")

            tinf_object = source / "ota_tinf.o"
            compile_c = subprocess.run(
                [
                    cc,
                    "-std=c99",
                    "-DOTA_TRANSPORT_DEFLATE_TEST",
                    f"-I{ota_dir}",
                    "-c",
                    str(ota_dir / "OtaTinf.c"),
                    "-o",
                    str(tinf_object),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compile_c.returncode, 0, compile_c.stderr)

            executable = source / (
                "bridge_behavior.exe" if os.name == "nt" else "bridge_behavior"
            )
            compile_cpp = subprocess.run(
                [
                    cxx,
                    "-std=c++11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-DOTA_TRANSPORT_DEFLATE_TEST",
                    f"-I{ota_dir}",
                    str(ota_dir / "OtaDeflate.cpp"),
                    str(ota_dir / "OtaProtocol.cpp"),
                    str(harness),
                    str(tinf_object),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compile_cpp.returncode, 0, compile_cpp.stderr)

            run_result = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)


class ExactTransformTests(unittest.TestCase):
    def test_replace_exact_replaces_one_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.txt"
            path.write_bytes(b"before anchor after")
            inflate.replace_exact(path, b"anchor", b"replacement", "test")
            self.assertEqual(path.read_bytes(), b"before replacement after")

    def test_replace_exact_rejects_missing_or_ambiguous_anchor(self) -> None:
        for contents, count in ((b"none", 0), (b"anchor anchor", 2)):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.txt"
                path.write_bytes(contents)
                with self.assertRaisesRegex(
                    inflate.BuildError, f"found {count}"
                ):
                    inflate.replace_exact(path, b"anchor", b"replacement", "test")


class BuilderConfigurationTests(unittest.TestCase):
    def test_inflate_builder_has_distinct_reproducibility_identity(self) -> None:
        self.assertEqual(
            inflate.VERSION_SUFFIX, "halo-keymind-cascade-mota-inflate2k"
        )
        self.assertEqual(
            inflate.INFLATE_BASE_SNAPSHOT_COMMIT,
            "add51bf00c46c15ef54318ca766a6daf08a147ee",
        )
        self.assertNotIn("6f03eae8", {target.source_commit[:8] for target in inflate.TARGETS})


if __name__ == "__main__":
    unittest.main()
