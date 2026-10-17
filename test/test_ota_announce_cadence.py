#!/usr/bin/env python3
"""Execute the production OTA announcement branches with the real long timer."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <vector>
#include <helpers/ota/OtaTiming.h>
@DEFINES@
struct Clock {
  uint32_t now = 0;
  uint32_t getMillis() const { return now; }
};
namespace mesh { namespace ota {
struct Manager {
  Clock* clock = nullptr;
  uint32_t minutes = 1440;
  unsigned cleared = 0;
  std::vector<uint32_t> sent;
  uint32_t advert_mins() const { return minutes; }
  void announce() { sent.push_back(clock->now); }
  void clearPendingEgress() { ++cleared; }
};
struct OtaContext {
  Manager manager;
  bool self_serve_supported = true, serving = false;
  unsigned storage_preparations = 0;
  void prepareTowerStorage() { ++storage_preparations; }
};
static OtaContext context;
OtaContext& ota_ctx() { return context; }
bool ota_serve_self(OtaContext&, unsigned) { return true; }
}}
using namespace mesh;
struct Fixture {
  Clock clock;
  Clock* _ms = &clock;
  bool _ota_temp_was_active = false;
  uint32_t _next_ota_tick = 0;
  uint8_t _ota_announce_count = 0;
  ota::LongTimer _ota_announce_timer;
  float speed = 1;
  unsigned relay_resets = 0;
  Fixture(uint32_t minutes, float rate, uint32_t start) {
    clock.now = start; speed = rate;
    ota::context = {};
    ota::context.manager.clock = &clock;
    ota::context.manager.minutes = minutes;
  }
  void resetOtaRelayBackoff() { ++relay_resets; }
  void active(bool ota_active) {
@INACTIVE@
@ENTRY@
  }
  void step() {
    const float ota_speed = speed;
@ANNOUNCE@
  }
  // Repeated observations preserve the long timer's intended wrap-safe
  // elapsed-time contract, including 140-day intervals at minimum speed.
  void advance(uint64_t elapsed) {
    do {
      const uint32_t chunk = elapsed > 0x40000000ULL ? 0x40000000UL : uint32_t(elapsed);
      clock.now += chunk; elapsed -= chunk; step();
    } while (elapsed);
  }
};

static uint64_t scaled(uint32_t delay, float speed) {
  return uint64_t(ceil(double(delay) / speed));
}

static void burst(Fixture& f) {
  f.active(true);
  const auto first = f.clock.now;
  f.step();
  assert(ota::context.manager.sent.size() == 1);
  assert(ota::context.manager.sent[0] == first);
  assert(f._next_ota_tick == first);
  const uint64_t gap = scaled(OTA_ANNOUNCE_BURST_MS, f.speed);
  for (unsigned i = 1; i < OTA_ANNOUNCE_BURST; ++i) {
    f.advance(gap - 1);
    assert(ota::context.manager.sent.size() == i);
    f.advance(1);
    assert(ota::context.manager.sent.size() == i + 1);
    assert(ota::context.manager.sent[i] == uint32_t(first + i * gap));
  }
  assert(f._ota_announce_count == 4);
}

int main() {
  static_assert(OTA_ANNOUNCE_BURST == 4, "the existing four-announcement burst must remain intact");
  unsigned cases = 0;
  for (uint32_t minutes : {0U, 1U, 7U, 1440U, 10080U})
  for (float speed : {0.05f, 0.5f, 1.0f, 3.0f})
  for (uint32_t start : {0U, 0xffff0000U}) {
    Fixture f(minutes, speed, start);
    burst(f);
    const uint64_t delay = scaled(minutes ? minutes * 60000UL
        : OTA_ANNOUNCE_DISABLED_POLL_MS, speed);
    f.advance(delay - 1);
    assert(ota::context.manager.sent.size() == 4
        && "the first post-burst announcement must wait for the periodic cadence");
    f.advance(1);
    assert(ota::context.manager.sent.size() == (minutes ? 5U : 4U));
    assert(f._ota_announce_count == (minutes ? 5 : 4));
    f.advance(delay - 1);
    assert(ota::context.manager.sent.size() == (minutes ? 5U : 4U));
    f.advance(1);
    assert(ota::context.manager.sent.size() == (minutes ? 6U : 4U));
    ++cases;
  }
  {
    // A new TempRadio window restarts exactly the existing four-entry burst;
    // leaving it still clears retained egress instead of leaking old work.
    Fixture f(1440, 1, 0xffff8000U);
    burst(f);
    f.active(false);
    assert(!f._ota_temp_was_active && f.relay_resets == 1);
    assert(ota::context.manager.cleared == 1);
    f.clock.now += 12345;
    ota::context.manager.sent.clear();
    burst(f);
    f.advance(OTA_ANNOUNCE_BURST_MS);
    assert(ota::context.manager.sent.size() == 4);
  }
  {
    // advert=0 retains the slow configuration poll, not a fifth burst send.
    Fixture f(0, 1, 0);
    burst(f);
    ota::context.manager.minutes = 1;
    f.advance(OTA_ANNOUNCE_DISABLED_POLL_MS - 1);
    assert(ota::context.manager.sent.size() == 4);
    f.advance(1);
    assert(ota::context.manager.sent.size() == 5);
    f.advance(60000);
    assert(ota::context.manager.sent.size() == 6);
  }
  printf("PASS: %u OTA announcement cadence cases, re-entry and disabled polling\n", cases);
}
'''


class OtaAnnounceCadenceTest(unittest.TestCase):
    def run_fixture(self, seeder_only=False, legacy=False):
        production = (ROOT / "src/Mesh.cpp").read_text(encoding="ascii")
        defines = []
        for name in ("OTA_ANNOUNCE_BURST", "OTA_ANNOUNCE_BURST_MS",
                     "OTA_ANNOUNCE_DISABLED_POLL_MS"):
            match = re.search(r"^#define " + name + r"\s+(\d+(?:UL)?)\b", production, re.MULTILINE)
            self.assertIsNotNone(match)
            defines.append("#define " + name + " " + match[1])
        announcement = extract_braced(production, "if (_ota_announce_timer.ready(_ms->getMillis()))")
        if legacy:
            announcement, count = re.subn(
                r"uint32_t gap = _ota_announce_count < OTA_ANNOUNCE_BURST\s*\?",
                "uint32_t gap = in_burst ?", announcement)
            self.assertEqual(count, 1, "production cadence boundary changed")
        source = HARNESS.replace("@DEFINES@", "\n".join(defines))
        source = source.replace("@INACTIVE@", extract_braced(production, "if (!ota_active)"))
        source = source.replace("@ENTRY@", extract_braced(production, "if (!_ota_temp_was_active)"))
        source = source.replace("@ANNOUNCE@", announcement)
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="ota-announce-cadence-") as directory:
            work = Path(directory)
            cpp, binary = work / "cadence.cpp", work / "cadence"
            cpp.write_text(source, encoding="ascii")
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            role = ["-DOTA_SEEDER_ONLY=1"] if seeder_only else [
                "-DNRF52_PLATFORM=1", "-DOTA_TOWER_AUTO_STORE=1"]
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                *sanitizers, *role, "-I" + str(ROOT / "src"), str(cpp), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)

    def test_production_burst_periodic_disabled_speed_wrap_and_reentry(self):
        for seeder_only in (False, True):
            with self.subTest(seeder_only=seeder_only):
                result = self.run_fixture(seeder_only=seeder_only)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS: 40 OTA announcement cadence cases", result.stdout)

    def test_legacy_pre_send_count_regression_is_detected(self):
        result = self.run_fixture(legacy=True)
        self.assertNotEqual(result.returncode, 0, "legacy extra announcement escaped the oracle")
        self.assertIn("the first post-burst announcement must wait", result.stderr)


if __name__ == "__main__":
    unittest.main()
