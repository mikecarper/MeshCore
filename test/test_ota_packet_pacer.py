"""Compile the allocation-free OTA pacing policy against actual TX event traces."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include "src/helpers/ota/OtaPacketPacer.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>

using mesh::ota::OtaPacketPacer;
using Pacer = OtaPacketPacer;
using Key = Pacer::Key;

#define CHECK(x) do { if (!(x)) { \
  std::fprintf(stderr, "line %d: %s\n", __LINE__, #x); std::exit(1); \
} } while (0)

static Key key(uint16_t block, uint8_t profile = 0, uint32_t generation = 7,
               uint8_t format = Pacer::V2, uint32_t stream = 101) {
  Key result;
  result.profile = profile;
  result.generation = generation;
  result.block = block;
  result.format = format;
  result.stream = stream;
  result.mid[0] = 0x11; result.mid[1] = 0x22;
  result.mid[2] = 0x33; result.mid[3] = 0x44;
  return result;
}

static void data(Pacer& p, const Key& k, uint32_t now, uint16_t mask = 7) {
  for (uint16_t bit = 1; bit; bit = uint16_t(bit << 1))
    if (mask & bit) CHECK(p.queued(k, mask, bit));
  for (uint16_t bit = 1; bit; bit = uint16_t(bit << 1))
    if (mask & bit) CHECK(p.sent(k, bit, now++));
}

static void complete(Pacer& p, const Key& k, uint32_t now) {
  data(p, k, now);
  CHECK(p.proofSent(k.profile, k.generation, k.mid, k.block, now + 3));
}

static Pacer::Feedback request(Pacer& p, const Key& k, uint16_t want,
                               uint32_t now, uint32_t grace = 10) {
  return p.request(k.profile, k.generation, k.mid, k.block, k.format, want, now, grace);
}

static bool forward(Pacer& p, const Key& k, uint16_t next, uint32_t now) {
  return p.forwardRequest(k.profile, k.generation, k.mid, next, now, 0);
}

static void lower(Pacer& p) {
  for (uint16_t block = 0; block < 2; ++block) {
    const Key k = key(block);
    complete(p, k, 100 + block * 100);
    CHECK(request(p, k, 1, 150 + block * 100) == Pacer::Loss);
  }
  CHECK(p.pace(0, 7) == 0.25f);
  CHECK(!forward(p, key(0), 2, 300)); // dirty blocks cannot credit recovery
}

static void profiles() {
  Pacer p;
  const Key main = key(0), second = key(0, 1, 9);
  CHECK(!p.queued(main, 7, 1)); // queue admission never binds stale routes
  CHECK(p.pace(0, 7) == 1.0f);
  p.bind(0, 7); p.bind(1, 9);
  complete(p, main, 100);
  CHECK(request(p, main, 1, 200) == Pacer::Loss);
  CHECK(p.pace(0, 7) == 0.5f && p.pace(1, 9) == 1.0f);
  CHECK(p.pace(0) == 0.5f && p.pace(1) == 1.0f);
  complete(p, second, 200);
  CHECK(request(p, second, 2, 300) == Pacer::Loss);
  p.bind(0, 8);
  CHECK(p.pace(0, 8) == 1.0f && p.pace(1, 9) == 0.5f);
  CHECK(request(p, main, 1, 400) == Pacer::Ignored);
  CHECK(!p.sent(main, 1, 400) && !p.failed(main, 1));
  CHECK(!p.queued(main, 7, 1));
  const Key portable = key(0, Pacer::PortableProfile, 0);
  p.bind(Pacer::PortableProfile, 0);
  CHECK(request(p, portable, 1, 500) == Pacer::Ignored);
  complete(p, portable, 500);
  CHECK(request(p, portable, 1, 600) == Pacer::Loss);
  CHECK(p.pace(Pacer::PortableProfile, 0) == 0.5f);
  CHECK(p.pace(1, 9) == 0.5f && p.pace(0, 8) == 1.0f);
  p.bind(2, 0);
  CHECK(p.pace(2, 0) == 1.0f && !p.queued(key(0, 2, 0), 7, 1));
  p.reset();
  CHECK(p.pace() == 1.0f && p.pace(1, 9) == 1.0f);
  CHECK(!p.sent(portable, 1, 700));
}

static void pending() {
  Pacer p; p.bind(0, 7);
  const Key k = key(0);
  CHECK(p.queued(k, 7, 1));
  CHECK(request(p, k, 1, 90000) == Pacer::Suppressed); // CAD can hold indefinitely
  CHECK(!p.proofSent(0, 7, k.mid, 0, 90000));
  CHECK(p.sent(k, 1, 90001));
  CHECK(request(p, k, 1, 99999) == Pacer::Suppressed); // unsent rest of burst
  CHECK(p.queued(k, 7, 2) && p.queued(k, 7, 4));
  CHECK(p.sent(k, 2, 100000));
  CHECK(request(p, k, 1, 200000) == Pacer::Suppressed);
  CHECK(p.sent(k, 4, 200001));
  CHECK(request(p, k, 1, 200010) == Pacer::Suppressed);
  CHECK(request(p, k, 1, 200011) == Pacer::Loss); // grace begins at final actual TX
  CHECK(request(p, k, 1, 400000) == Pacer::Duplicate);
  CHECK(p.pace(0, 7) == 0.5f);
  CHECK(request(p, k, 7, 400000) == Pacer::Ignored);
  CHECK(request(p, k, 0, 400000) == Pacer::Ignored);
}

static void queued_duplicates() {
  Pacer p; p.bind(0, 7);
  const Key k = key(0);
  CHECK(p.queued(k, 7, 1) && p.queued(k, 7, 1));
  CHECK(p.queued(k, 7, 2) && p.queued(k, 7, 4));
  CHECK(p.sent(k, 1, 100) && p.sent(k, 2, 101) && p.sent(k, 4, 102));
  CHECK(request(p, k, 1, 500) == Pacer::Suppressed);
  CHECK(!p.proofSent(0, 7, k.mid, 0, 500));
  CHECK(p.sent(k, 1, 501));
  CHECK(!p.sent(k, 1, 999)); // callback without a queued token does not extend grace
  CHECK(request(p, k, 1, 510) == Pacer::Suppressed);
  CHECK(request(p, k, 1, 511) == Pacer::Loss);
  CHECK(!p.failed(k, 1));
  CHECK(!p.queued(k, 7, 0) && !p.queued(k, 7, 3) && !p.queued(k, 7, 8));
}

static void tx_failures() {
  Pacer p; p.bind(0, 7);
  const Key k = key(0);
  CHECK(p.queued(k, 7, 1));
  CHECK(p.failed(k, 1));
  CHECK(!p.failed(k, 1));
  CHECK(request(p, k, 1, 1000) == Pacer::Suppressed);
  complete(p, k, 2000); // a requeued block can complete, but is not a clean flight
  CHECK(request(p, k, 1, 3000) == Pacer::Suppressed);
  CHECK(!forward(p, k, 1, 3000));
  CHECK(p.pace(0, 7) == 1.0f);
  const Key proof_failure = key(1);
  data(p, proof_failure, 4000);
  CHECK(p.proofFailed(0, 7, proof_failure.mid, 1));
  CHECK(p.proofSent(0, 7, proof_failure.mid, 1, 4003));
  CHECK(!forward(p, proof_failure, 2, 4004));
  CHECK(!p.proofFailed(0, 7, proof_failure.mid, 99));
}

static void recovery() {
  Pacer p; p.bind(0, 7); lower(p);
  auto clean = [&](uint16_t block) {
    const Key k = key(block);
    complete(p, k, 1000 + block * 100);
    // A later full request may arrive immediately after PROOF, before sparse-loss grace.
    CHECK(forward(p, k, uint16_t(block + 1), 1004 + block * 100));
  };
  clean(2);
  CHECK(p.pace(0, 7) == 0.25f);
  CHECK(!forward(p, key(2), 3, 1205)); // duplicate full request cannot credit twice
  clean(3);
  CHECK(p.pace(0, 7) == 0.5f);
  complete(p, key(4), 1400);
  CHECK(request(p, key(4), 1, 1500) == Pacer::Loss);
  CHECK(p.pace(0, 7) == 0.25f); // failed probe rolls back to the last confirmed pace
  CHECK(request(p, key(4), 1, 1600) == Pacer::Duplicate);
  CHECK(!forward(p, key(4), 5, 1600));
  clean(5); clean(6);
  CHECK(p.pace(0, 7) == 0.5f);
  clean(7);
  CHECK(p.pace(0, 7) == 0.5f);
  clean(8);
  CHECK(p.pace(0, 7) == 0.75f);
  {
    Pacer failed_probe = p;
    complete(failed_probe, key(90), 5000);
    CHECK(request(failed_probe, key(90), 1, 5100) == Pacer::Loss);
    CHECK(failed_probe.pace(0, 7) == 0.5f);
  }
  clean(9);
  CHECK(p.pace(0, 7) == 0.75f);
  clean(10);
  CHECK(p.pace(0, 7) == 1.0f);
  {
    Pacer failed_probe = p;
    complete(failed_probe, key(91), 6000);
    CHECK(request(failed_probe, key(91), 1, 6100) == Pacer::Loss);
    CHECK(failed_probe.pace(0, 7) == 0.75f);
  }
  clean(11);
  CHECK(p.pace(0, 7) == 1.0f);
  complete(p, key(12), 7000);
  CHECK(request(p, key(12), 1, 7100) == Pacer::Loss);
  CHECK(p.pace(0, 7) == 0.5f); // confirmed 1x is no longer a probe of 0.75x
  complete(p, key(13), 7200);
  CHECK(request(p, key(13), 1, 7300) == Pacer::Loss);
  CHECK(p.pace(0, 7) == 0.25f);
}

static void forward_evidence() {
  Pacer p; p.bind(0, 7); lower(p);
  CHECK(!forward(p, key(0), 2, 400)); // initial and full duplicates provide no evidence
  const Key a = key(2), b = key(3);
  data(p, a, 500);
  CHECK(!forward(p, a, 3, 600)); // DATA alone is not a completed flight
  CHECK(p.proofSent(0, 7, a.mid, 2, 601));
  CHECK(p.queued(b, 7, 1));
  CHECK(!forward(p, a, 4, 602)); // incomplete burst cannot credit a multi-block window
  CHECK(p.sent(b, 1, 603));
  CHECK(p.queued(b, 7, 2) && p.queued(b, 7, 4));
  CHECK(p.sent(b, 2, 604) && p.sent(b, 4, 605));
  CHECK(p.proofSent(0, 7, b.mid, 3, 606));
  CHECK(forward(p, a, 4, 607)); // two blocks are one request flight, not two credits
  CHECK(p.pace(0, 7) == 0.25f);
  CHECK(!forward(p, a, 4, 608));
  complete(p, key(4), 700);
  CHECK(forward(p, key(4), 5, 704));
  CHECK(p.pace(0, 7) == 0.5f); // the prior multi-block window credited exactly once
}

static void representations() {
  Pacer p; p.bind(0, 7);
  const Key a = key(0), b = key(0, 0, 7, Pacer::V2, 202);
  complete(p, a, 100); complete(p, b, 200);
  CHECK(request(p, a, 1, 500) == Pacer::Suppressed); // REQ has no stream ID
  CHECK(p.pace(0, 7) == 1.0f);
  p.reset(); p.bind(0, 7);
  complete(p, a, 100);
  CHECK(request(p, a, 1, 200) == Pacer::Loss);
  const Key legacy = key(0, 0, 7, Pacer::Extended, 0);
  complete(p, legacy, 300);
  CHECK(request(p, legacy, 1, 500) == Pacer::Duplicate); // one loss per logical block
  CHECK(p.pace(0, 7) == 0.5f);
  CHECK(!forward(p, a, 1, 600));
  p.reset(); p.bind(0, 7);
  CHECK(p.queued(a, 7, 1)); CHECK(p.failed(a, 1)); // abandoned encoding
  complete(p, b, 200);
  CHECK(!forward(p, a, 1, 300)); // forward progress consumes dirty fallback, no credit
  complete(p, key(1), 400);
  CHECK(forward(p, key(1), 2, 404)); // abandoned encoding cannot block all later recovery
}

static void bounded_history() {
  Pacer p; p.bind(0, 7);
  for (uint16_t block = 0; block < Pacer::HistorySlots; ++block)
    CHECK(p.queued(key(block), 7, 1));
  CHECK(!p.queued(key(Pacer::HistorySlots), 7, 1)); // never evict a queued packet
  for (uint16_t block = 0; block < Pacer::HistorySlots; ++block) {
    CHECK(p.sent(key(block), 1, block));
    CHECK(request(p, key(block), 1, 500) == Pacer::Suppressed);
  }
  complete(p, key(10), 1000); // can evict an incomplete but no-longer-queued old entry
  CHECK(request(p, key(0), 1, 2000) == Pacer::Ignored);
  CHECK(request(p, key(10), 1, 2000) == Pacer::Loss);
  CHECK(p.pace(0, 7) == 0.5f);
}

static void clock_wrap() {
  Pacer p; p.bind(0, 7);
  const Key k = key(0);
  data(p, k, UINT32_MAX - 5); // final DATA at UINT32_MAX - 3
  CHECK(request(p, k, 1, 4) == Pacer::Suppressed); // elapsed 8ms, not a huge time gap
  CHECK(request(p, k, 1, 6) == Pacer::Loss); // elapsed 10ms across wrap
  CHECK(p.pace(0, 7) == 0.5f);
}

static void edge_bits_and_unaligned_ids() {
  Pacer p; p.bind(0, 7);
  const Key k = key(0);
  CHECK(p.queued(k, 0x8001, 1) && p.queued(k, 0x8001, 0x8000));
  CHECK(p.sent(k, 1, 100) && p.sent(k, 0x8000, 101));
  alignas(4) const uint8_t unaligned[] = {0, 0x11, 0x22, 0x33, 0x44};
  CHECK(p.request(0, 7, unaligned + 1, 0, k.format, 0x8000, 111, 10) == Pacer::Loss);
  CHECK(request(p, k, 1, 200) == Pacer::Duplicate);
  CHECK(p.pace(0, 7) == 0.5f);
}

int main(int argc, char** argv) {
  CHECK(argc == 2);
  struct Test { const char* name; void (*run)(); } tests[] = {
    {"profiles", profiles}, {"pending", pending},
    {"queued_duplicates", queued_duplicates}, {"tx_failures", tx_failures},
    {"recovery", recovery}, {"forward_evidence", forward_evidence},
    {"representations", representations}, {"bounded_history", bounded_history},
    {"clock_wrap", clock_wrap}, {"edge_bits_and_unaligned_ids", edge_bits_and_unaligned_ids},
  };
  for (const auto& test : tests) if (!std::strcmp(argv[1], test.name)) {
    test.run(); std::printf("%s: OK (workspace=%zu bytes)\n", test.name, sizeof(Pacer));
    return 0;
  }
  return 2;
}
'''


class TestOtaPacketPacer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("C++ compiler not available")
        cls.temp = tempfile.TemporaryDirectory(prefix="ota-packet-pacer-")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        source = directory / "pacer.cpp"
        source.write_text(HARNESS, encoding="utf-8")
        cls.binary = directory / ("pacer.exe" if os.name == "nt" else "pacer")
        command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic",
                   "-I", str(ROOT), str(source), "-o", str(cls.binary)]
        if os.name != "nt":
            command[1:1] = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        subprocess.run(command, check=True, capture_output=True, text=True)

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_profiles_generations_and_portable_fallback(self):
        self.run_case("profiles")

    def test_pending_and_incomplete_bursts_do_not_back_off(self):
        self.run_case("pending")

    def test_duplicate_queue_tokens_and_callbacks(self):
        self.run_case("queued_duplicates")

    def test_transmitter_failures_are_not_receiver_loss(self):
        self.run_case("tx_failures")

    def test_clean_probes_recover_and_failed_probe_rolls_back(self):
        self.run_case("recovery")

    def test_forward_progress_requires_actual_data_and_proof(self):
        self.run_case("forward_evidence")

    def test_representation_ambiguity_and_logical_block_deduplication(self):
        self.run_case("representations")

    def test_history_eviction_does_not_evict_pending_packets(self):
        self.run_case("bounded_history")

    def test_sparse_loss_grace_across_clock_wrap(self):
        self.run_case("clock_wrap")

    def test_high_fragment_bit_and_unaligned_manifest_id(self):
        self.run_case("edge_bits_and_unaligned_ids")


if __name__ == "__main__":
    unittest.main()
