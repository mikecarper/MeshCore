#!/usr/bin/env python3
"""Execute bounded source proofs and real manager ownership under sanitizers."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaSourceProofTests(unittest.TestCase):
    def test_actual_policy_preserves_custom_source_capacities(self):
        source = """
#include <helpers/ota/OtaManager.h>
static_assert(OTA_BOUNDED_SOURCE_PROOFS == EXPECT_BOUNDED, "wrong source policy");
#if OTA_BOUNDED_SOURCE_PROOFS
static_assert(sizeof(mesh::ota::SourceMerkleProof<OTA_PROOFGEN_SCRATCH / 4, OTA_LEAVES_FRAG>) <= 1536,
              "bounded source storage exceeds the actual allocation budget");
#endif
#if OTA_PROOFGEN_SCRATCH == 16383
static_assert(OTA_PROOFGEN_SCRATCH / 4 == 4095, "odd byte limit lost usable leaf capacity");
#endif
"""
        base = [os.environ.get("CXX", "g++"), "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                "-I", str(ROOT / "src"), "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1"]
        for scratch in (None, 0, 1, 3, 4, 5, 16383, 16384, 16385, 32768):
            bounded = scratch is None or 4 <= scratch <= 16384
            flags = [] if scratch is None else [f"-DOTA_PROOFGEN_SCRATCH={scratch}"]
            with self.subTest(scratch=scratch):
                result = subprocess.run([*base, *flags, f"-DEXPECT_BOUNDED={int(bounded)}"],
                                        input=source, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
        for scratch in (0, 3, 16385):
            with self.subTest(forced_invalid=scratch):
                result = subprocess.run([*base, f"-DOTA_PROOFGEN_SCRATCH={scratch}",
                                         "-DOTA_BOUNDED_SOURCE_PROOFS=1", "-DEXPECT_BOUNDED=1"],
                                        input=source, text=True, capture_output=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Bounded source proofs require 4..16384 leaf bytes", result.stderr)

    def test_serial_metadata_stays_one_transaction_across_capacity_changes(self):
        native = (ROOT / "test/test_ota/test_ota_core.cpp").read_text()
        fake = native[native.index("class FakeMotaSeederStream"):
                      native.index("class FakeDeflateMotaSeederStream")]
        code = r'''
#include <cassert>
#include <vector>
#include <helpers/ota/MotaSourceSerial.h>
#include <helpers/ota/MotaSeederProto.h>
#include <helpers/ota/OtaByteIO.h>
using namespace mesh::ota;
@FAKE@
class MtuSource : public SerialMotaSource {
public:
  using SerialMotaSource::SerialMotaSource;
  uint16_t frame = 197;
protected:
  uint16_t responseFrameCapacity() const override { return frame; }
};
int main() {
  FakeMotaSeederStream stream;
  MtuSource source(stream, MotaStreamWritePolicy::NoFlush);
  uint8_t bytes[192];
  for (uint16_t frame : {197,182,176,20}) {
    source.frame = frame;
    const auto before = stream.lengths.size();
    const auto quantum = source.read_quantum();
    assert(quantum == frame - 5);
    assert(source.read_metadata(0, 99, bytes, quantum));
    assert(stream.lengths.size() == before + 1 && stream.lengths.back() == quantum);
    for (unsigned i = 0; i < quantum; ++i) assert(bytes[i] == uint8_t(99 + i));
  }
  source.frame = 197;
  const auto old_quantum = source.read_quantum();
  source.frame = 20;
  const auto before = stream.lengths.size();
  assert(!source.read_metadata(0, 0, bytes, old_quantum));
  source.frame = 0;
  assert(!source.read_metadata(0, 0, bytes, 1));
  assert(stream.lengths.size() == before);
}
'''.replace("@FAKE@", fake)
        with tempfile.TemporaryDirectory(prefix="ota-source-quantum-") as folder:
            source, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            source.write_text(code)
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"), str(source),
                str(ROOT / "src/helpers/ota/MotaSourceSerial.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_grouped_equivalence_faults_and_manager_lifetimes(self):
        # Reuse the existing source/wire/allocator harness, retaining its legacy
        # tests unchanged in their own suite. Only its helpers precede this point.
        prefix = (ROOT / "test/fixtures/ota_cold_buffers/test.cpp").read_text().split(
            "static void host_allocation_and_fallbacks()", 1)[0]
        prefix = prefix.replace("(data[0] == OTA_MANIFEST && !sent.accept_manifest)",
                                "((data[0] == OTA_MANIFEST || data[0] == OTA_LEAVES) && !sent.accept_manifest)")
        code = prefix + (ROOT / "test/fixtures/ota_source_proofs/test.cpp").read_text()
        with tempfile.TemporaryDirectory(prefix="ota-source-proofs-") as folder:
            source, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            source.write_text(code)
            files = ("OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp", "MerkleTree.cpp")
            result = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-sign-compare",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"), str(source),
                *[str(ROOT / "src/helpers/ota" / name) for name in files], str(ROOT / "src/Utils.cpp"),
                "-Wl,--wrap=malloc", "-Wl,--wrap=free", "-o", str(binary),
            ], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            for marker in ("byte-identical grouped proofs", "partial reads and source changes",
                           "bounded manager ownership and backpressure", "simultaneous capture and serving"):
                self.assertIn("PASS: " + marker, ran.stdout)
            print(ran.stdout, flush=True)


if __name__ == "__main__":
    unittest.main()
