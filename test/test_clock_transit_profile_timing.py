#!/usr/bin/env python3
"""Execute both clock transit estimators with real packet and profile timing."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
ESTIMATORS = (
    ("examples/simple_repeater/MyMesh.cpp",
     "uint32_t MyMesh::estimateClockTransitMillis("),
    ("src/helpers/MeshClockSync.cpp",
     "uint32_t MeshClockSync::estimateTransitMillis("),
)

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <vector>
#include <Packet.h>
#include <RadioProfiles.h>

class RadioLibWrapper {
public:
  mesh::RadioProfiles _profiles;
  uint16_t profilePreamble(uint8_t profile) {
    return _profiles.preamble(profile, 32);
  }
  uint32_t getEstAirtimeFor(int) { assert(false); return 0; }
  uint32_t getProfileAirtime(uint8_t profile, int length, uint8_t cr = 0);
};
@AIRTIME@

struct Call { uint8_t profile; int length; uint8_t cr; };
struct Radio {
  RadioLibWrapper driver;
  uint8_t active = 0;
  unsigned legacy_calls = 0;
  std::vector<Call> calls;
  uint32_t getEstAirtimeFor(int length) {
    ++legacy_calls;
    return driver.getProfileAirtime(active, length);
  }
  uint32_t getProfileAirtime(uint8_t profile, int length, uint8_t cr = 0) {
    calls.push_back({profile, length, cr});
    return driver.getProfileAirtime(profile, length, cr);
  }
  void reset() { legacy_calls = 0; calls.clear(); }
};
namespace repeater {
constexpr uint32_t CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS = @REPEATER_WINDOW@;
class MyMesh {
public:
  Radio* _radio = nullptr;
  struct { float tx_delay_factor = 0; } _prefs;
  uint32_t estimateClockTransitMillis(const mesh::Packet*) const;
};
@REPEATER@
}
namespace mesh {
constexpr uint32_t CONSENSUS_WINDOW_SECONDS = @SHARED_WINDOW@;
class MeshClockSync {
public:
  Radio* _radio = nullptr;
  float factor = 0;
  float* _tx_delay_factor = &factor;
  uint32_t estimateTransitMillis(const Packet*) const;
};
@SHARED@
}

// Compute quarter-symbol airtime independently of the driver's floating-point
// symbol expression, using its actual configured preamble and legal tuples.
static uint32_t airtime(const RadioLibWrapper& radio, uint8_t profile,
                        unsigned length, uint8_t override_cr) {
  const auto& p = radio._profiles.params(profile);
  const unsigned cr = override_cr >= 5 && override_cr <= 8 ? override_cr : p.cr;
  const bool low_rate = (1U << p.sf) * 1000.0 / p.bw >= 16000;
  const int denominator = 4 * (p.sf - 2 * low_rate);
  const int numerator = 8 * int(length) - 4 * p.sf + (p.sf <= 6 ? 20 : 28) + 16;
  const unsigned coded = numerator > 0 ? (numerator + denominator - 1) / denominator : 0;
  const unsigned symbols = 8 + coded * cr;
  const unsigned quarters = 4 * (radio._profiles.preamble(profile, 32) + symbols)
      + (p.sf <= 6 ? 25 : 17);
  return uint32_t(ceil(double(quarters) * (1U << p.sf) / (4.0 * p.bw)));
}

static std::vector<Call> expectedCalls(const Radio& radio, const mesh::Packet& packet,
                                      unsigned factor_quarters, uint32_t& total) {
  std::vector<Call> calls;
  uint64_t sum = 0;
  for (unsigned hop = 0; hop <= packet.getPathHashCount(); ++hop) {
    // Serialization independently establishes each earlier frame's complete
    // wire length, including the optional four transport-code bytes.
    mesh::Packet prefix = packet;
    prefix.setPathHashCount(hop);
    uint8_t wire[MAX_TRANS_UNIT];
    const unsigned bytes = prefix.writeTo(wire);
    calls.push_back({packet.radio_profile, int(bytes), packet.tx_cr});
    const uint32_t air = airtime(radio.driver, packet.radio_profile, bytes, packet.tx_cr);
    assert(radio.driver._profiles.enabled());
    sum += air;
    if (hop) {
      // Midpoint is airtime * (factor_quarters / 4) * 2.5,
      // rounded to the nearest millisecond by the existing policy.
      sum += (uint64_t(air) * factor_quarters * 5 + 4) / 8;
      if (sum >= uint64_t(repeater::CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS) * 1000) {
        total = repeater::CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS * 1000;
        return calls;
      }
    }
  }
  total = uint32_t(sum);
  return calls;
}

static void checkResult(const Radio& radio, uint32_t observed, uint32_t expected,
                         const std::vector<Call>& calls) {
  if (observed != expected) fprintf(stderr,
      "clock transit mismatch: observed=%u expected=%u active-profile=%u\n",
      observed, expected, radio.active);
  assert(observed == expected && "clock transit must follow received packet profile");
  assert(radio.legacy_calls == 0 && "clock transit must not use the scanner tuple");
  assert(radio.calls.size() == calls.size() && "clock transit must preserve the cap boundary");
  for (unsigned i = 0; i != calls.size(); ++i) {
    assert(radio.calls[i].profile == calls[i].profile && "clock transit must follow received packet profile");
    assert(radio.calls[i].length == calls[i].length && "clock transit must use each complete wire length");
    assert(radio.calls[i].cr == calls[i].cr && "clock transit must preserve packet coding rate");
  }
}

int main() {
  static_assert(repeater::CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS == mesh::CONSENSUS_WINDOW_SECONDS,
                "clock roles must use the same existing cap");
  Radio radio;
  repeater::MyMesh repeater;
  mesh::MeshClockSync shared;
  repeater._radio = shared._radio = &radio;
  assert(repeater.estimateClockTransitMillis(nullptr) == 0);
  assert(shared.estimateTransitMillis(nullptr) == 0);
  mesh::Packet empty;
  empty.header = ROUTE_TYPE_FLOOD;
  empty.setPathHashSizeAndCount(1, 0);
  empty.payload_len = 0;
  repeater._radio = shared._radio = nullptr;
  assert(repeater.estimateClockTransitMillis(&empty) == 0);
  assert(shared.estimateTransitMillis(&empty) == 0);
  repeater._radio = shared._radio = &radio;

  unsigned cases = 0, capped = 0, overrides = 0;
  for (unsigned pair : {0, 1, 2, 3}) {
    auto& profiles = radio.driver._profiles;
    profiles.primary = {915, 125, 32, 11, 8};
    profiles.secondary.params = {916, 500, 512, 7, 5};
    profiles.secondary.mode = mesh::RadioProfileMode::RxTx;
    if (pair == 1) {
      const auto primary = profiles.primary;
      profiles.primary = profiles.secondary.params;
      profiles.secondary.params = primary;
    } else if (pair == 2) {
      profiles.primary = {915, 500, 32, 5, 5};
      profiles.secondary.params = {916, 250, 64, 6, 8};
    } else if (pair == 3) {
      profiles.primary = {915, 7.8f, 32, 12, 8};
    }
    assert(profiles.automaticPreambleFits());
    for (uint8_t profile : {1, 0}) for (uint8_t active : {0, 1})
    for (uint8_t route : {ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD})
    for (uint8_t hash_size : {1, 2, 3}) for (uint8_t hops : {0, 1, 7, 21})
    for (uint16_t payload : {0, 37, 100, 184}) for (uint8_t cr : {0, 5, 6, 8})
    for (unsigned factor_quarters : {0, 1, 4, 15}) {
      mesh::Packet packet;
      packet.header = route | (PAYLOAD_TYPE_ADVERT << PH_TYPE_SHIFT);
      packet.setPathHashSizeAndCount(hash_size, hops);
      packet.payload_len = payload;
      packet.radio_profile = profile;
      packet.tx_cr = cr;
      packet.transport_codes[0] = 0x1234;
      packet.transport_codes[1] = 0x5678;
      memset(packet.path, 0x21, sizeof(packet.path));
      memset(packet.payload, 0x43, sizeof(packet.payload));
      radio.active = active;
      repeater._prefs.tx_delay_factor = shared.factor = factor_quarters / 4.0f;
      uint32_t expected;
      const auto calls = expectedCalls(radio, packet, factor_quarters, expected);
      radio.reset();
      checkResult(radio, repeater.estimateClockTransitMillis(&packet), expected, calls);
      radio.reset();
      checkResult(radio, shared.estimateTransitMillis(&packet), expected, calls);
      capped += expected == repeater::CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS * 1000;
      overrides += cr && cr != profiles.params(profile).cr;
      ++cases;
    }
  }
  assert(capped > 0 && overrides > 0);
  printf("PASS: %u clock transit cases per estimator, %u capped, %u CR overrides\n",
         cases, capped, overrides);
}
'''


class ClockTransitProfileTimingTests(unittest.TestCase):
    def run_fixture(self, mutation=None):
        source = HARNESS
        production = [(ROOT / path).read_text() for path, _ in ESTIMATORS]
        for index, marker in enumerate(("@REPEATER@", "@SHARED@")):
            method = extract_braced(production[index], ESTIMATORS[index][1])
            if mutation is not None and mutation[0] == index:
                method = mutation[1](method)
            source = source.replace(marker, method)
        repeater_header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        repeater_window = re.search(
            r"^#define CLOCK_SYNC_CONSENSUS_WINDOW_SECONDS\s+(\d+UL)$",
            repeater_header, re.MULTILINE)
        shared_window = re.search(
            r"constexpr uint32_t CONSENSUS_WINDOW_SECONDS = (\d+UL);", production[1])
        self.assertIsNotNone(repeater_window)
        self.assertIsNotNone(shared_window)
        source = source.replace("@REPEATER_WINDOW@", repeater_window[1])
        source = source.replace("@SHARED_WINDOW@", shared_window[1])
        driver = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        source = source.replace("@AIRTIME@", extract_braced(
            driver, "uint32_t RadioLibWrapper::getProfileAirtime("))
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        cache = Path.home() / ".cache"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="clock-transit-profile-", dir=cache) as directory:
            work = Path(directory)
            cpp, binary = work / "timing.cpp", work / "timing"
            cpp.write_text(source, encoding="ascii")
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", *sanitizers,
                "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                str(cpp), str(ROOT / "src/Packet.cpp"), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)

    def test_actual_estimators_follow_packet_profile_wire_lengths_and_coding_rate(self):
        result = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: 24576 clock transit cases per estimator", result.stdout)

    def test_exact_legacy_estimator_mutation_is_detected_for_each_role(self):
        def legacy(method):
            changed, count = re.subn(
                r"_radio->getProfileAirtime\(\s*packet->radio_profile,\s*([^,]+),\s*packet->tx_cr\)",
                lambda match: "_radio->getEstAirtimeFor(" + match[1] + ")", method)
            self.assertEqual(count, 2, "production clock airtime boundaries changed")
            return changed
        for index, (path, _) in enumerate(ESTIMATORS):
            with self.subTest(role=path):
                result = self.run_fixture((index, legacy))
                self.assertNotEqual(result.returncode, 0, "legacy scanner timing escaped its oracle")
                self.assertIn("clock transit must follow received packet profile", result.stderr)


if __name__ == "__main__":
    unittest.main()
