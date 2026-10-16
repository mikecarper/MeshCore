#!/usr/bin/env python3
"""Run actual role forwarding delays against packet-specific radio metadata."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
ROLES = (
    ("companion_radio", "MyMesh", None),
    ("simple_repeater", "MyMesh", "getSlowScopeRetransmitDelay"),
    ("simple_room_server", "MyMesh", "getSlowFloodRuleRetransmitDelay"),
    ("simple_sensor", "SensorMesh", None),
)
HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <Packet.h>
#include <helpers/FloodFilterPolicy.h>
struct Radio {
  uint8_t active = 0, observed_profile = 255, observed_cr = 255;
  int observed_length = -1;
  unsigned legacy_calls = 0, profile_calls = 0;
  static uint32_t airtime(uint8_t profile, int length, uint8_t cr) {
    return (17U + 113U * profile) * uint32_t(length) + 7U * cr + 1;
  }
  uint32_t getEstAirtimeFor(int length) {
    ++legacy_calls;
    return airtime(active, length, 5);
  }
  uint32_t getProfileAirtime(uint8_t profile, int length, uint8_t cr = 0) {
    ++profile_calls;
    observed_profile = profile; observed_length = length; observed_cr = cr;
    return airtime(profile, length, cr);
  }
};
struct Rng {
  unsigned choice = 0, calls = 0;
  uint32_t lower = 0, upper = 0;
  uint32_t nextInt(uint32_t minimum, uint32_t maximum) {
    assert(maximum > minimum);
    ++calls; lower = minimum; upper = maximum;
    return minimum + (choice == 0 ? 0 : choice == 1
        ? (maximum - minimum - 1) / 2 : maximum - minimum - 1);
  }
};
struct Prefs { float tx_delay_factor = 0, direct_tx_delay_factor = 0; };
template<class Node>
unsigned exercise(uint32_t (Node::*method)(const mesh::Packet*), bool sensor, bool slow) {
  unsigned cases = 0;
  for (uint8_t profile : {1, 0}) for (uint8_t active : {0, 1})
  for (uint8_t route : {ROUTE_TYPE_TRANSPORT_FLOOD, ROUTE_TYPE_FLOOD,
                       ROUTE_TYPE_DIRECT, ROUTE_TYPE_TRANSPORT_DIRECT})
  for (uint8_t hash_size : {3, 1, 2}) for (uint8_t hops : {7, 0})
  for (uint16_t payload : {77, 0, 184}) for (uint8_t cr : {8, 0, 4, 5})
  for (float factor : {1.5f, 0.25f, 0.0f}) for (unsigned choice : {2, 1, 0}) {
    Node node; Radio radio; radio.active = active; node._radio = &radio;
    node._prefs.tx_delay_factor = node._prefs.direct_tx_delay_factor = factor;
    node.rng.choice = choice;
    mesh::Packet packet;
    packet.header = uint8_t(route | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT));
    packet.setPathHashSizeAndCount(hash_size, hops);
    packet.payload_len = payload; packet.radio_profile = profile; packet.tx_cr = cr;
    packet.transport_codes[0] = 0x1234; packet.transport_codes[1] = 0x5678;
    memset(packet.path, 0x21, sizeof(packet.path));
    memset(packet.payload, 0x43, sizeof(packet.payload));
    // Serialization is an independent length boundary, including transport codes.
    uint8_t wire[MAX_TRANS_UNIT];
    const unsigned wire_length = packet.writeTo(wire);
    const uint32_t air = Radio::airtime(profile, wire_length, cr);
    const uint32_t scaled = uint32_t(air * factor);
    const uint32_t maximum = slow ? 10U * air : 5U * scaled;
    const uint32_t upper = sensor ? 6 : maximum + 1;
    const uint32_t picked = choice == 0 ? 0 : choice == 1 ? (upper - 1) / 2 : upper - 1;
    const uint32_t expected = sensor ? picked * scaled : picked;
    const uint32_t delay = (node.*method)(&packet);
    if (!cases && delay != expected) fprintf(stderr,
        "timing mismatch: observed=%u expected=%u profile=%u active=%u bytes=%u CR=%u\n",
        delay, expected, profile, active, wire_length, cr);
    assert(radio.profile_calls == 1 && radio.legacy_calls == 0
        && "forwarding must use packet-aware airtime");
    assert(radio.observed_profile == profile && "timing must follow packet profile");
    assert(radio.observed_length == int(wire_length) && "timing must include complete wire frame");
    assert(radio.observed_cr == cr && "timing must include packet coding rate");
    assert(node.rng.calls == 1 && node.rng.lower == 0 && node.rng.upper == upper);
    assert(delay == expected && "existing role delay distribution must be preserved");
    ++cases;
  }
  return cases;
}
'''


class ForwardingProfileTimingTests(unittest.TestCase):
    def run_fixture(self, mutation=None):
        source, calls = HARNESS, []
        for index, (role, owner, slow) in enumerate(ROLES):
            methods = ["getRetransmitDelay", "getDirectRetransmitDelay"] + ([slow] if slow else [])
            production = (ROOT / f"examples/{role}/{owner}.cpp").read_text()
            source += f"\nnamespace role{index} {{\nclass {owner} {{\npublic:\n"
            source += "Radio* _radio = nullptr; Prefs _prefs; Rng rng; Rng* getRNG(){return &rng;}\n"
            source += "\n".join(f"uint32_t {name}(const mesh::Packet*);" for name in methods)
            source += "\n};\n"
            for name in methods:
                body = extract_braced(production, f"uint32_t {owner}::{name}(")
                if mutation:
                    body = mutation(body)
                source += body + "\n"
                calls.append(f"cases += exercise(&role{index}::{owner}::{name}, "
                             f"{'true' if role == 'simple_sensor' else 'false'}, "
                             f"{'true' if name == slow else 'false'});")
            source += "}\n"
        source += "int main(){unsigned cases=0;\n" + "\n".join(calls)
        source += '\nprintf("PASS: %u actual-role profile timing cases\\n", cases);}\n'
        return self.compile_fixture(source)

    def compile_fixture(self, source):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="forwarding-profile-timing-") as directory:
            work = Path(directory)
            cpp, binary = work / "timing.cpp", work / "timing"
            cpp.write_text(source, encoding="ascii")
            sanitize = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                         "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter",
                *sanitize, "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                str(cpp), str(ROOT / "src/Packet.cpp"), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)

    def test_actual_role_delays_use_packet_profile_wire_length_and_coding_rate(self):
        result = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: 103680 actual-role profile timing cases", result.stdout)

    def test_suite_is_explicitly_wired_in_ci(self):
        workflow = (ROOT / ".github/workflows/run-unit-tests.yml").read_text()
        self.assertIn("python3 -B test/test_forwarding_profile_timing.py -v", workflow)

    def test_actual_driver_preserves_single_profile_default_cr_airtime(self):
        driver = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        method = extract_braced(driver, "uint32_t RadioLibWrapper::getProfileAirtime(")
        harness = r'''
#include <cassert>
#include <cstdio>
#include <RadioProfiles.h>
class RadioLibWrapper {
public:
  mesh::RadioProfiles _profiles;
  unsigned legacy_calls = 0;
  uint32_t getEstAirtimeFor(int length) { ++legacy_calls; return uint32_t(17 * length + 3); }
  uint16_t profilePreamble(uint8_t profile) { return _profiles.params(profile).preamble; }
  uint32_t getProfileAirtime(uint8_t profile, int length, uint8_t cr);
};
'''
        harness += method + r'''
int main() {
  RadioLibWrapper radio;
  assert(!radio._profiles.enabled());
  for (int length = 0; length <= 255; ++length) {
    radio.legacy_calls = 0;
    assert(radio.getProfileAirtime(0, length, 0) == uint32_t(17 * length + 3));
    assert(radio.legacy_calls == 1 && "single-profile default-CR must use existing driver estimate");
  }
  puts("PASS: 256 actual-driver legacy airtime cases");
}
'''
        result = self.compile_fixture(harness)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: 256 actual-driver legacy airtime cases", result.stdout)

    def test_metadata_regressions_are_detected(self):
        def replace(body, pattern, replacement):
            changed, count = re.subn(pattern, replacement, body)
            self.assertEqual(count, 1, "production timing boundary changed")
            return changed
        mutations = (
            (r"_radio->getProfileAirtime\(\s*packet->radio_profile,\s*packet->getRawLength\(\),\s*packet->tx_cr\)",
             "_radio->getEstAirtimeFor(packet->getRawLength())", "forwarding must use packet-aware airtime"),
            (r"packet->radio_profile,", "_radio->active,", "timing must follow packet profile"),
            (r"packet->getRawLength\(\)", "packet->getPathByteLen() + packet->payload_len + 2",
             "timing must include complete wire frame"),
            (r"packet->tx_cr", "0", "timing must include packet coding rate"),
        )
        for pattern, replacement, oracle in mutations:
            with self.subTest(oracle=oracle):
                result = self.run_fixture(lambda body: replace(body, pattern, replacement))
                self.assertNotEqual(result.returncode, 0, "timing regression escaped its oracle")
                self.assertIn(oracle, result.stderr)


if __name__ == "__main__":
    unittest.main()
