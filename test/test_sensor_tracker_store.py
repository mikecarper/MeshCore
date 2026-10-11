#!/usr/bin/env python3
"""Run production tracker persistence against faults on all supported FS APIs."""

from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]

STAT_MOCK = r'''
#pragma once
struct stat {};
inline int stat(const char* absolute, struct stat*) {
  if (!metadata_filesystem || strncmp(absolute, "/spiffs", 7) != 0) { errno = EIO; return -1; }
  const char* path = absolute + 7;
  const std::string op = std::string("stat:") + path;
  const bool okay = metadata_filesystem->begin(op);
  const bool present = metadata_filesystem->files.count(path) || metadata_filesystem->directories.count(path);
  metadata_filesystem->end(op);
  if (!okay) { errno = EIO; return -1; }
  if (!present) { errno = ENOENT; return -1; }
  return 0;
}
'''

HARNESS = r'''
#include "tracker_storage_fs.h"
#include <helpers/SensorTrackerStore.h>
#include <helpers/TrackerProtocol.h>
using namespace mesh::tracker;
static unsigned scenarios = 0;
static const char* A = kTrackerBank0Path;
static const char* B = kTrackerBank1Path;
static const char* T = kTrackerTempPath;
static const std::vector<uint8_t> GOLDEN = {@GOLDEN@};
static StoreResult load(MemoryFS& fs, TrackerRecord& record) {
  metadata_filesystem = &fs; return loadTracker(&fs, record);
}
static StoreResult save(MemoryFS& fs, TrackerRecord& record) {
  metadata_filesystem = &fs; return saveTracker(&fs, record);
}
static TrackerRecord initial() {
  TrackerRecord record; record.enabled = true;
  for (size_t i = 0; i < sizeof(record.owner); ++i) record.owner[i] = static_cast<uint8_t>(i + 1);
  assert(noteFailure(record.route, 1000, record.route));
  record.path_len = 66;
  for (size_t i = 0; i < 4; ++i) record.path[i] = static_cast<uint8_t>(i + 10);
  record.last_lost = 2;
  record.last_request_tag = 1000;
  return record;
}
static void repair(std::vector<uint8_t>& image) {
  route_detail::put32(image.data() + 156, route_detail::crc(image.data(), 156));
}
static void unavailable(MemoryFS& fs) {
  TrackerRecord out = initial();
  assert(load(fs, out) == StoreResult::Unavailable);
  assert(!out.enabled && !out.generation && (out.route.flags & kClockFault));
  auto candidate = initial();
  assert(save(fs, candidate) == StoreResult::Unavailable);
  assert(!candidate.generation && !fs.write_opens); ++scenarios;
}
static void seed(MemoryFS& fs, unsigned count) {
  auto record = initial();
  for (unsigned i = 0; i < count; ++i) assert(save(fs, record) == StoreResult::Success);
  fs.reset();
}
int main() {
  { MemoryFS fs; TrackerRecord out = initial();
    assert(load(fs, out) == StoreResult::NotFound);
    assert(!out.enabled && !out.generation && out.path_len == kUnknownPath);
    assert(out.normal_interval == 1800 && out.lost_interval == 60 && !out.route.flags);
    fs.put(T, {1, 2, 3}); assert(load(fs, out) == StoreResult::NotFound);
    auto record = initial(); assert(save(fs, record) == StoreResult::Success);
    assert(record.generation == 1 && fs.get(A) == GOLDEN && !fs.files.count(T));
    assert(load(fs, out) == StoreResult::Success && out.enabled && out.generation == 1);
    assert(memcmp(out.owner, record.owner, 32) == 0 && out.path_len == 66);
    assert(memcmp(out.path, record.path, 64) == 0 && out.last_lost == 2);
    assert(out.route.outage_since == 1000 && out.last_request_tag == 1000);
    record.enabled = false; record.last_lost = 1; record.normal_interval = 86400; record.lost_interval = 60;
    assert(save(fs, record) == StoreResult::Success && record.generation == 2);
    assert(load(fs, out) == StoreResult::Success && !out.enabled && out.last_lost == 1);
    assert(out.normal_interval == 86400 && out.generation == 2);
    record.enabled = true; record.path_len = kUnknownPath; memset(record.path, 0xaa, sizeof(record.path));
    assert(save(fs, record) == StoreResult::Success && record.generation == 3);
    assert(load(fs, out) == StoreResult::Success && out.path_len == kUnknownPath);
    for (uint8_t byte : out.path) assert(byte == 0);
    ++scenarios; }
  { MemoryFS fs; seed(fs, 2); auto stale = initial();
    assert(save(fs, stale) == StoreResult::Conflict && !fs.write_opens);
    stale.generation = 1; assert(save(fs, stale) == StoreResult::Conflict && !fs.write_opens); ++scenarios; }
  { MemoryFS fs; auto record = initial();
    memset(record.owner, 0, sizeof(record.owner)); record.owner[31] = 1;
    record.path_len = 96; record.normal_interval = 60; record.lost_interval = 86400; record.last_lost = 0;
    for (size_t index = 0; index < sizeof(record.path); ++index) record.path[index] = static_cast<uint8_t>(255 - index);
    assert(save(fs, record) == StoreResult::Success);
    TrackerRecord reboot; assert(load(fs, reboot) == StoreResult::Success);
    assert(reboot.owner[31] == 1 && reboot.path_len == 96 && reboot.normal_interval == 60
           && reboot.lost_interval == 86400 && reboot.last_lost == 0);
    assert(memcmp(reboot.owner, record.owner, sizeof(record.owner)) == 0);
    assert(memcmp(reboot.path, record.path, sizeof(record.path)) == 0); ++scenarios; }
  { TrackerRecord out; assert(loadTracker(static_cast<MemoryFS*>(nullptr), out) == StoreResult::Unavailable);
    assert(!out.enabled && (out.route.flags & kClockFault));
    auto record = initial(); assert(saveTracker(static_cast<MemoryFS*>(nullptr), record) == StoreResult::Unavailable); ++scenarios; }
  for (unsigned bad = 0; bad < 12; ++bad) {
    MemoryFS fs; auto record = initial();
    if (bad == 0) memset(record.owner, 0, 32);
    if (bad == 1) record.normal_interval = 59;
    if (bad == 2) record.normal_interval = 86401;
    if (bad == 3) record.lost_interval = 59;
    if (bad == 4) record.lost_interval = 86401;
    if (bad == 5) record.last_lost = 3;
    if (bad == 6) record.path_len = 192;
    if (bad == 7) record.path_len = 127;
    if (bad == 8) record.route.flags = 0x80;
    if (bad == 9) record.route.outage_since = 0;
    if (bad == 10) record.last_request_tag = 1301;
    if (bad == 11) record.route = RouteState();
    assert(save(fs, record) == StoreResult::Invalid && fs.trace.empty()); ++scenarios;
  }
  // Accepted path encoding and boundary hash counts agree with Packet's wire
  // format; 4-byte hashes and count*width > 64 are unsupported.
  for (unsigned length = 0; length < 256; ++length) {
    auto record = initial(); record.path_len = static_cast<uint8_t>(length);
    const bool expected = length == 255 || ((length >> 6) != 3 && (length & 63) * ((length >> 6) + 1) <= 64);
    assert(isValidTrackerRecord(record) == expected); ++scenarios;
  }

  // Corrupt either committed bank: never fall back to an older state that could
  // permit a repeated flood, nor overwrite the unexpected image.
  for (const char* bank : {A, B}) {
    MemoryFS valid; seed(valid, 2); const auto original = valid.get(bank);
    for (size_t offset = 0; offset < original.size(); ++offset) {
      MemoryFS fs; seed(fs, 2); auto damaged = original; damaged[offset] ^= 1; fs.put(bank, damaged);
      unavailable(fs); assert(fs.get(bank) == damaged); ++scenarios;
    }
    for (const auto& image : {std::vector<uint8_t>(), std::vector<uint8_t>(159), std::vector<uint8_t>(161)}) {
      MemoryFS fs; seed(fs, 2); fs.put(bank, image); unavailable(fs);
    }
    for (const char* operation : {"open:r:", "size:", "read:"}) {
      MemoryFS fs; seed(fs, 2); fs.faults.insert(std::string(operation) + bank); unavailable(fs);
    }
    MemoryFS directory; seed(directory, 2); directory.directories.insert(bank); unavailable(directory);
  }
  for (unsigned mutation = 0; mutation < 12; ++mutation) {
    MemoryFS fs; auto image = GOLDEN;
    if (mutation == 0) image[3] = 2;
    if (mutation == 1) image[8] = 2;
    if (mutation == 2) image[9] = 3;
    if (mutation == 3) image[10] = 192;
    if (mutation == 4) image[11] = 1;
    if (mutation == 5) route_detail::put32(image.data() + 12, 59);
    if (mutation == 6) route_detail::put32(image.data() + 16, 86401);
    if (mutation == 7) std::fill(image.begin() + 20, image.begin() + 52, 0);
    if (mutation == 8) image[152] = 1;
    if (mutation == 9) image[88] = 1;
    if (mutation == 10) route_detail::put32(image.data() + 4, 0);
    if (mutation == 11) {
      image[56] = 0x80;
      route_detail::put32(image.data() + 80, route_detail::crc(image.data() + 52, 28));
    }
    repair(image); fs.put(A, image); unavailable(fs);
  }
  { MemoryFS fs; seed(fs, 2); auto image = fs.get(B);
    route_detail::put32(image.data() + 4, 4); repair(image); fs.put(B, image); unavailable(fs); }
  { MemoryFS fs; auto image = GOLDEN; route_detail::put32(image.data() + 4, UINT32_MAX); repair(image); fs.put(A, image);
    TrackerRecord record; assert(load(fs, record) == StoreResult::Success);
    fs.reset(); assert(save(fs, record) == StoreResult::Unavailable && !fs.write_opens); ++scenarios; }
  { MemoryFS fs; seed(fs, 2); fs.short_read = true; unavailable(fs); }
  // The durable nonce floor survives the same RTC second across reboot. An
  // authenticated older reply cannot match a subsequently allocated tag.
  { MemoryFS fs; auto record = initial(); assert(save(fs, record) == StoreResult::Success);
    TrackerRecord reboot; assert(load(fs, reboot) == StoreResult::Success);
    assert(reboot.last_request_tag == 1000 && reboot.route.latest_observed == 1000);
    reboot.last_request_tag = 1001;
    assert(save(fs, reboot) == StoreResult::Success);
    TrackerRecord second_boot; assert(load(fs, second_boot) == StoreResult::Success);
    assert(second_boot.last_request_tag == 1001 && second_boot.route.latest_observed == 1000);
    uint8_t response[ResponseLength], state; uint16_t awake;
    assert(makeStatusResponse(1000, Lost, 120, response, sizeof(response)) == sizeof(response));
    assert(!parseStatusResponse(response, sizeof(response), second_boot.last_request_tag, state, awake));
    assert(makeStatusResponse(1001, Safe, 0, response, sizeof(response)) == sizeof(response));
    assert(parseStatusResponse(response, sizeof(response), second_boot.last_request_tag, state, awake));
    assert(state == Safe && awake == 0); ++scenarios; }
  { MemoryFS fs; auto record = initial(); record.last_request_tag = 1300;
    assert(save(fs, record) == StoreResult::Success);
    TrackerRecord reboot; assert(load(fs, reboot) == StoreResult::Success && reboot.last_request_tag == 1300);
    fs.reset(); record.last_request_tag = 1301;
    assert(save(fs, record) == StoreResult::Invalid && fs.trace.empty());
    record.last_request_tag = 1000;
    assert(save(fs, record) == StoreResult::Invalid && !fs.write_opens);
    record.last_request_tag = 0;
    assert(save(fs, record) == StoreResult::Invalid && !fs.write_opens);
    assert(load(fs, reboot) == StoreResult::Success && reboot.last_request_tag == 1300);
    MemoryFS unallocated; auto fresh = initial(); fresh.last_request_tag = 0;
    assert(save(unallocated, fresh) == StoreResult::Success);
    assert(load(unallocated, reboot) == StoreResult::Success && !reboot.last_request_tag); ++scenarios; }
  { MemoryFS fs; auto record = initial(); record.route = RouteState();
    record.route.latest_observed = UINT32_MAX - 300; record.last_request_tag = UINT32_MAX;
    assert(save(fs, record) == StoreResult::Success);
    TrackerRecord reboot; assert(load(fs, reboot) == StoreResult::Success && reboot.last_request_tag == UINT32_MAX);
    assert(reboot.route.latest_observed == UINT32_MAX - 300); ++scenarios; }
#if defined(ESP32_PLATFORM) || defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  for (const char* bank : {A, B}) {
    for (bool present : {false, true}) {
      MemoryFS fs; if (present) seed(fs, 2); fs.faults.insert(std::string("stat:") + bank);
      unavailable(fs);
    }
  }
#endif

  // Every failure and power-cut boundary around a flood reservation. Exactly
  // old or fully committed new is selected after reboot, never partial state.
  for (unsigned prior : {0u, 1u, 2u, 3u}) {
    MemoryFS baseline; seed(baseline, prior);
    TrackerRecord old; assert(load(baseline, old) == (prior ? StoreResult::Success : StoreResult::NotFound));
    if (!prior) old = initial();
    TrackerRecord desired = old;
    assert(reserveFlood(desired.route, 22600, desired.route));
    baseline.reset(); assert(save(baseline, desired) == StoreResult::Success);
    const auto trace = baseline.trace; const size_t boundaries = baseline.boundary;
    std::set<std::string> operations;
    for (const auto& point : trace) if (point.compare(0, 7, "before:") == 0) operations.insert(point.substr(7));
    for (const auto& operation : operations) {
      // Boolean-only exists APIs cannot report metadata faults independently.
#if !defined(ESP32_PLATFORM) && !defined(NRF52_PLATFORM) && !defined(STM32_PLATFORM)
      if (operation.compare(0, 5, "stat:") == 0) continue;
#endif
      MemoryFS fs; seed(fs, prior); fs.faults.insert(operation); auto attempted = old;
      assert(reserveFlood(attempted.route, 22600, attempted.route));
      const StoreResult result = save(fs, attempted);
      const bool transmitted = result == StoreResult::Success;
      fs.reset(); TrackerRecord reboot;
      const StoreResult restored = load(fs, reboot);
      assert(restored == StoreResult::Success || (!prior && restored == StoreResult::NotFound));
      const bool committed = restored == StoreResult::Success && reboot.route.last_flood == 22600;
      assert(!transmitted || committed);
      if (committed) {
        RouteState candidate; assert(planRoute(reboot.route, 22601, true, candidate) == RouteDecision::Direct);
        assert(!reserveFlood(reboot.route, 22601, candidate));
        assert(reboot.generation == prior + 1);
      } else assert(reboot.generation == prior);
      ++scenarios;
    }
    for (size_t cut = 1; cut <= boundaries; ++cut) {
      MemoryFS fs; seed(fs, prior); fs.cut_at = cut; auto attempted = old;
      assert(reserveFlood(attempted.route, 22600, attempted.route));
      try { save(fs, attempted); } catch (const PowerCut&) {}
      fs.reset(); TrackerRecord reboot;
      const StoreResult restored = load(fs, reboot);
      assert(restored == StoreResult::Success || (!prior && restored == StoreResult::NotFound));
      assert(reboot.generation == prior || reboot.generation == prior + 1);
      if (reboot.generation == prior + 1) {
        assert(reboot.route.last_flood == 22600);
        RouteState candidate; assert(!reserveFlood(reboot.route, 22601, candidate));
      } else if (prior) assert(!reboot.route.last_flood);
      ++scenarios;
    }
    for (bool short_write : {false, true}) {
      MemoryFS fs; seed(fs, prior); fs.short_write = short_write;
      if (!short_write) fs.replacement_on_close = GOLDEN;
      auto attempted = old; assert(reserveFlood(attempted.route, 22600, attempted.route));
      assert(save(fs, attempted) == StoreResult::Unavailable);
      fs.reset(); TrackerRecord reboot;
      assert(load(fs, reboot) == (prior ? StoreResult::Success : StoreResult::NotFound));
      assert(reboot.generation == prior); ++scenarios;
    }
  }
  // Sticky cutoff and saved lost status/path survive a complete reload.
  { MemoryFS fs; auto record = initial();
    RouteState cutoff; assert(planRoute(record.route, 346600, false, cutoff) == RouteDecision::Locked);
    record.route = cutoff; assert(save(fs, record) == StoreResult::Success);
    TrackerRecord reboot; assert(load(fs, reboot) == StoreResult::Success && reboot.last_lost == 2);
    assert(reboot.path_len == 66 && reboot.route.flags & kFloodLocked);
    RouteState candidate; assert(noteRepeater(reboot.route, 400000, candidate));
    assert(planRoute(candidate, 500000, false, cutoff) == RouteDecision::Locked);
    assert(noteAuthenticatedReply(candidate, 500000, cutoff));
    assert(!cutoff.flags); ++scenarios; }
  printf("PASS: %u sensor tracker storage, reservation and power-cut scenarios\n", scenarios);
}
'''


def golden_image():
    route = b"TRP\x01\x01\0\0\0" + struct.pack("<5I", 1000, 0, 0, 0, 1000)
    route += struct.pack("<I", zlib.crc32(route))
    image = bytearray(156)
    image[:4] = b"STR\x01"
    struct.pack_into("<I", image, 4, 1)
    image[8:11] = bytes((1, 2, 66))
    struct.pack_into("<2I", image, 12, 1800, 60)
    image[20:52] = bytes(range(1, 33))
    image[52:84] = route
    image[84:88] = bytes(range(10, 14))
    struct.pack_into("<I", image, 148, 1000)
    return bytes(image) + struct.pack("<I", zlib.crc32(image))


class SensorTrackerStoreTest(unittest.TestCase):
    def test_real_store_all_backend_apis_and_faults(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++ compiler is required")
        source_text = HARNESS.replace("@GOLDEN@", ",".join(map(str, golden_image())))
        sanitizers = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []
        with tempfile.TemporaryDirectory(prefix="meshcore-sensor-tracker-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            source = work / "store.cpp"
            source.write_text(source_text)
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                            *sanitizers, *defines, "-I" + str(work),
                                            "-I" + str(ROOT / "test/fixtures"), "-I" + str(ROOT / "src"),
                                            str(source), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("sensor tracker storage, reservation and power-cut scenarios", checked.stdout)
                    print(platform or "generic", checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
