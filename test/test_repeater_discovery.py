#!/usr/bin/env python3
"""Execute repeater discovery policy and wire replies from the production source.

The real handler, hidden-node predicate and rate limiter run unchanged. Only
the radio, packet allocation, identity and clock boundaries are host doubles.
"""
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
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include "examples/simple_repeater/RateLimiter.h"
#include "src/RadioProfiles.h"
#include "src/helpers/RadioDirectionPolicy.h"
#include "src/helpers/DiscoveryResponsePrefs.h"
@CONSTANTS@
#define MESH_DEBUG_PRINTLN(...) ((void)0)
struct CountedLimiter : RateLimiter {
  using RateLimiter::RateLimiter;
  unsigned calls = 0;
  bool allow(uint32_t now) { ++calls; return RateLimiter::allow(now); }
};
static uint32_t now_ms = 100;
bool millisHasNowPassed(uint32_t deadline) {
  return static_cast<int32_t>(now_ms - deadline) > 0;
}
static void require(bool condition, const char* message) {
  if (!condition) {
    std::fprintf(stderr, "discovery contract: %s\n", message);
    std::exit(1);
  }
}
namespace mesh {
struct Identity {
  uint8_t pub_key[PUB_KEY_SIZE] = {};
  Identity() = default;
  explicit Identity(const uint8_t* key) { std::memcpy(pub_key, key, PUB_KEY_SIZE); }
  bool matches(const Identity& other) const {
    return std::memcmp(pub_key, other.pub_key, PUB_KEY_SIZE) == 0;
  }
};
struct Packet {
  uint8_t payload[64] = {};
  uint8_t payload_len = 0;
  uint8_t tx_radio = RADIO_TX_AUTO;
  uint8_t radio_profile = 1;
  bool radio_reply = true, radio_bound = false, radio_reply_force = false, radio_local = true;
  int8_t _snr = -20;
  uint8_t getPayloadType() const { return PAYLOAD_TYPE_CONTROL; }
  float getSNR() const { return _snr / 4.0f; }
  int getRSSI() const { return -90; }
};
struct Radio {
  mesh::RadioProfiles state;
  bool available = true;
  const mesh::RadioProfiles* profiles() const { return available ? &state : nullptr; }
};
struct Dispatcher {
  const Radio* _radio;
  uint8_t getTransmitProfileMask(const Packet* packet) const;
};
@DISPATCHER@
}
struct MyMesh {
  struct {
    uint16_t advert_interval = 30, flood_advert_interval = 0;
    bool disable_fwd = false;
    uint8_t discovery_responses = 3;
    uint32_t discovery_mod_timestamp = 500;
  } _prefs;
  struct Clock {
    uint32_t now = 1000;
    uint32_t getCurrentTime() const { return now; }
  } rtc_clock;
  CountedLimiter @LIMITER@;
  mesh::Radio radio;
  mesh::Radio* _radio = &radio;
  mesh::Identity self_id, neighbour;
  mesh::Packet response;
  uint32_t pending_discover_tag = 0, pending_discover_until = 1000;
  unsigned create_attempts = 0, sent_packets = 0, neighbours = 0;
  uint32_t sent_delay = 0, neighbour_timestamp = 0;
  float neighbour_snr = 0;
  int neighbour_rssi = 0;
  bool fail_allocation = false;
  MyMesh() {
    for (size_t i = 0; i < PUB_KEY_SIZE; ++i) self_id.pub_key[i] = 0x60 + i;
  }
  @HIDDEN@
  void onControlDataRecv(mesh::Packet* packet);
  mesh::Packet* createControlData(const uint8_t* bytes, size_t length) {
    ++create_attempts;
    if (fail_allocation) return nullptr;
    require(length <= sizeof(response.payload), "reply exceeded packet capacity");
    std::memcpy(response.payload, bytes, length);
    response.payload_len = length;
    return &response;
  }
  uint32_t getRetransmitDelay(mesh::Packet* packet) {
    require(packet == &response, "delay used the wrong reply packet");
    return 7;
  }
  void sendZeroHop(mesh::Packet* packet, uint32_t delay) {
    require(packet == &response, "radio sent the wrong reply packet");
    require(packet->tx_radio == mesh::RADIO_TX_PRIMARY,
            "discovery response was not restricted to the main radio");
    require(mesh::Dispatcher{_radio}.getTransmitProfileMask(packet) == 1,
            "dispatcher would send discovery response on radio2 or both profiles");
    ++sent_packets;
    sent_delay = delay;
  }
  void putNeighbour(const mesh::Identity& id, uint32_t timestamp, float snr, int rssi) {
    ++neighbours;
    neighbour = id;
    neighbour_timestamp = timestamp;
    neighbour_snr = snr;
    neighbour_rssi = rssi;
  }
};
@HANDLER@
static mesh::Packet request(bool prefix = false, uint32_t since = 0,
                            bool include_since = false, bool include_radio2 = false) {
  mesh::Packet packet;
  packet.payload[0] = CTL_TYPE_NODE_DISCOVER_REQ | (prefix ? 1 : 0)
      | (include_radio2 ? 2 : 0);
  packet.payload[1] = 1 << ADV_TYPE_REPEATER;
  const uint32_t tag = 0x78563412;
  std::memcpy(packet.payload + 2, &tag, sizeof(tag));
  if (include_since) std::memcpy(packet.payload + 6, &since, sizeof(since));
  packet.payload_len = include_since ? 10 : 6;
  return packet;
}
static void policy() {
  for (uint16_t local : {uint16_t(0), uint16_t(30)}) {
    for (uint16_t flood : {uint16_t(0), uint16_t(30)}) {
      for (bool repeat_off : {false, true}) {
        for (bool prefix : {false, true}) {
         for (bool include_radio2 : {false, true}) {
          MyMesh receiver;
          receiver._prefs.advert_interval = local;
          receiver._prefs.flood_advert_interval = flood;
          receiver._prefs.disable_fwd = repeat_off;
          const bool hidden = local == 0 && flood == 0;
          require(receiver.isHiddenNode() == hidden, "hidden predicate misread advert intervals");
          auto packet = request(prefix, 0, false, include_radio2);
          receiver.onControlDataRecv(&packet);
          if (hidden) {
            require(receiver.create_attempts == 0 && receiver.sent_packets == 0,
                    "hidden node emitted a discovery response");
          } else {
            require(receiver.create_attempts == 1 && receiver.sent_packets == 1,
                    "visible node or repeat-off observer lost discovery");
          }
         }
        }
      }
    }
  }
}
static void gates() {
 for (unsigned flags = 0; flags < 4; ++flags) {
  for (size_t length = 0; length < 6; ++length) {
    MyMesh receiver;
    auto packet = request(flags & 1, 0, false, flags & 2);
    packet.payload_len = length;
    receiver.onControlDataRecv(&packet);
    require(receiver.create_attempts == 0, "truncated request produced a reply");
  }
  for (uint8_t filter : {uint8_t(0), uint8_t(1 << 3)}) {
    MyMesh receiver;
    auto packet = request(flags & 1, 0, false, flags & 2);
    packet.payload[1] = filter;
    receiver.onControlDataRecv(&packet);
    require(receiver.create_attempts == 0, "unrelated node filter produced a reply");
  }
  for (uint32_t since : {499U, 500U, 501U}) {
    MyMesh receiver;
    auto packet = request(flags & 1, since, true, flags & 2);
    receiver.onControlDataRecv(&packet);
    require(receiver.sent_packets == (since <= 500 ? 1U : 0U),
            "discovery timestamp boundary changed");
  }
 }
  MyMesh limited;
  auto packet = request();
  for (unsigned count = 1; count <= 5; ++count) {
    // The optional trailer and key-prefix flags share one limiter.
    packet.payload[0] = CTL_TYPE_NODE_DISCOVER_REQ | ((count - 1) & 3);
    limited.onControlDataRecv(&packet);
    require(limited.sent_packets == (count <= 4 ? count : 4),
            "discovery rate limit changed");
  }
  limited.rtc_clock.now = 1119;
  limited.onControlDataRecv(&packet);
  require(limited.sent_packets == 4, "rate limit expired before two minutes");
  limited.rtc_clock.now = 1120;
  limited.onControlDataRecv(&packet);
  require(limited.sent_packets == 5, "rate limit did not reopen at two minutes");
  MyMesh unrelated;
  packet.payload[0] = 0x70;
  unrelated.onControlDataRecv(&packet);
  require(unrelated.create_attempts == 0, "unrelated control type produced a reply");
}
static void response_switches() {
  for (uint8_t node : {0, 1}) {
    for (uint8_t region : {0, 1}) {
      for (unsigned flags = 0; flags < 4; ++flags) {
        MyMesh receiver;
        receiver._prefs.discovery_responses = node | (region << 1);
        receiver.response.payload_len = 17;
        receiver.response.tx_radio = mesh::RADIO_TX_BOTH;
        std::memset(receiver.response.payload, 0xa5, sizeof(receiver.response.payload));
        auto packet = request(flags & 1, 0, false, flags & 2);
        for (unsigned count = 0; count < 8; ++count) {
          receiver.onControlDataRecv(&packet);
        }
        if (node) {
          require(receiver.sent_packets == 4 && receiver.create_attempts == 4,
                  "region response choice affected normal node discovery");
        } else {
          require(receiver.discover_limiter.calls == 0,
                  "disabled node discovery consumed rate-limit quota");
          require(receiver.sent_packets == 0 && receiver.create_attempts == 0,
                  "disabled node discovery allocated or transmitted a reply");
          require(receiver.response.payload_len == 17
                      && receiver.response.tx_radio == mesh::RADIO_TX_BOTH,
                  "disabled node discovery changed pending reply metadata");
          for (uint8_t byte : receiver.response.payload) {
            require(byte == 0xa5, "disabled node discovery changed reply bytes");
          }
          // Turning the live switch back on must admit the complete original
          // quota: rejected requests did not secretly exhaust the limiter.
          receiver._prefs.discovery_responses |= 1;
          for (unsigned count = 1; count <= 5; ++count) {
            receiver.onControlDataRecv(&packet);
            require(receiver.sent_packets == (count < 5 ? count : 4),
                    "disabled requests exhausted the later enabled quota");
          }
        }
        // Enabling responses must not override the legacy hidden-node rule.
        MyMesh hidden;
        hidden._prefs.discovery_responses = node | (region << 1);
        hidden._prefs.advert_interval = hidden._prefs.flood_advert_interval = 0;
        hidden.onControlDataRecv(&packet);
        require(hidden.create_attempts == 0 && hidden.discover_limiter.calls == 0,
                "response setting bypassed hidden-node protection");
      }
    }
  }
}
static void wire() {
  for (bool prefix : {false, true}) {
    MyMesh receiver;
    auto packet = request(prefix);
    receiver.onControlDataRecv(&packet);
    require(receiver.sent_packets == 1, "visible request lost its reply");
    require(receiver.response.payload_len == 6 + (prefix ? 8 : PUB_KEY_SIZE),
            "prefix/full reply length changed");
    require(receiver.response.payload[0] == (CTL_TYPE_NODE_DISCOVER_RESP | ADV_TYPE_REPEATER),
            "reply control type or node type changed");
    require(receiver.response.payload[1] == static_cast<uint8_t>(packet._snr),
            "reply did not preserve signed inbound SNR bytes");
    require(std::memcmp(receiver.response.payload + 2, packet.payload + 2, 4) == 0,
            "reply did not echo the request tag");
    require(std::memcmp(receiver.response.payload + 6, receiver.self_id.pub_key,
                        prefix ? 8 : PUB_KEY_SIZE) == 0,
            "reply did not preserve the public key or prefix");
    require(receiver.sent_delay == 28, "discovery reply lost its widened random delay");
  }
  MyMesh exhausted;
  exhausted.fail_allocation = true;
  auto packet = request();
  exhausted.onControlDataRecv(&packet);
  require(exhausted.create_attempts == 1 && exhausted.sent_packets == 0,
          "allocation failure attempted to transmit a reply");
}
static uint32_t little_endian(const uint8_t* bytes) {
  return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8)
      | (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
}
static void radio2_wire() {
  for (bool prefix : {false, true}) {
    for (bool temporary : {false, true}) {
      for (auto mode : {mesh::RadioProfileMode::Rx, mesh::RadioProfileMode::RxTx}) {
        for (float bandwidth : {62.5f, 125.0f, 250.0f}) {
          MyMesh receiver;
          auto& profiles = receiver.radio.state;
          profiles.secondary.mode = mode;
          // Strict decimal CLI parsing may land one float ULP below the
          // literal. Both values must round back to the requested 911150 kHz.
          profiles.secondary.params.freq = temporary ? nextafterf(911.150f, 0.0f) : 911.150f;
          profiles.secondary.params.bw = bandwidth;
          profiles.secondary.params.sf = 7;
          profiles.secondary.params.cr = 5;
          profiles.secondary_temporary = temporary;
          // Ordinary discovery must remain byte-for-byte compatible, even
          // while a temporary radio2 profile and cross-radio replies are active.
          profiles.cross = mesh::RadioCrossMode::On;
          profiles.reply_tx = mesh::RADIO_TX_BOTH;
          profiles.reply_force = true;
          auto original = request(prefix);
          receiver.onControlDataRecv(&original);
          const size_t offset = 6 + (prefix ? 8 : PUB_KEY_SIZE);
          require(receiver.response.payload_len == offset,
                  "unsolicited radio2 trailer changed ordinary discovery");
          uint8_t baseline[6 + PUB_KEY_SIZE];
          std::memcpy(baseline, receiver.response.payload, offset);
          auto opted_in = request(prefix, 0, false, true);
          receiver.onControlDataRecv(&opted_in);
          require(receiver.response.payload_len == offset + 12,
                  "active MeshCore radio2 reply has the wrong trailer length");
          require(std::memcmp(baseline, receiver.response.payload, offset) == 0,
                  "radio2 trailer changed the existing discovery fields");
          const uint8_t* trailer = receiver.response.payload + offset;
          const uint8_t flags = mode == mesh::RadioProfileMode::Rx ? 3 : 5;
          require(trailer[0] == 1 && trailer[1] == (flags | (temporary ? 8 : 0)),
                  "radio2 version, configured/mode or temporary flags changed");
          require(little_endian(trailer + 2) == 911150,
                  "radio2 frequency was truncated or encoded in the wrong units/order");
          require(little_endian(trailer + 6) == uint32_t(bandwidth * 1000),
                  "radio2 bandwidth lost fractional kHz or used the wrong units/order");
          require(trailer[10] == 7 && trailer[11] == 5,
                  "radio2 spreading factor or coding rate changed");
          require(receiver.sent_packets == 2 && receiver.sent_delay == 28,
                  "extended discovery changed zero-hop reply count or random delay");
          // The unchanged full-key parser accepts optional trailing bytes.
          MyMesh peer;
          peer.pending_discover_tag = 0x78563412;
          receiver.response.payload[6] ^= 1; // A different repeater identity.
          peer.onControlDataRecv(&receiver.response);
          require(peer.neighbours == (prefix ? 0U : 1U),
                  "optional trailer broke full-key discovery parsing");
        }
      }
    }
  }
}
static void radio2_disabled() {
  for (bool prefix : {false, true}) {
    for (int inactive_reason : {0, 1, 2, 3, 4}) {
      MyMesh receiver;
      auto& profiles = receiver.radio.state;
      profiles.secondary.params.freq = 911.150f;
      profiles.secondary.params.bw = 250;
      profiles.secondary.params.sf = 7;
      profiles.secondary.params.cr = 5;
      profiles.secondary_temporary = true;
      if (inactive_reason == 0) receiver.radio.available = false;
      if (inactive_reason >= 2) {
        profiles.secondary.mode = mesh::RadioProfileMode::RxTx;
        profiles.secondary.params.protocol = inactive_reason == 2
            ? mesh::RadioProfileProtocol::Meshtext : mesh::RadioProfileProtocol::Ethernet;
      }
      if (inactive_reason == 4) {
        profiles.secondary.mode = static_cast<mesh::RadioProfileMode>(3);
        profiles.secondary.params.protocol = mesh::RadioProfileProtocol::MeshCore;
      }
      auto packet = request(prefix, 0, false, true);
      receiver.onControlDataRecv(&packet);
      const size_t offset = 6 + (prefix ? 8 : PUB_KEY_SIZE);
      require(receiver.response.payload_len == offset + 2,
              "unavailable/off/foreign radio2 advertised usable MeshCore settings");
      require(receiver.response.payload[offset] == 1 && receiver.response.payload[offset + 1] == 0,
              "disabled radio2 did not use the two-byte version/off trailer");
      require(std::memcmp(receiver.response.payload + 6, receiver.self_id.pub_key,
                          prefix ? 8 : PUB_KEY_SIZE) == 0,
              "disabled radio2 trailer overwrote the public key or prefix");
    }
    MyMesh exhausted;
    exhausted.fail_allocation = true;
    auto packet = request(prefix, 0, false, true);
    exhausted.onControlDataRecv(&packet);
    require(exhausted.create_attempts == 1 && exhausted.sent_packets == 0,
            "extended discovery allocation failure attempted transmission");
  }
}
static void received() {
 for (uint8_t node : {0, 1}) {
  for (uint8_t region : {0, 1}) {
  MyMesh receiver;
  receiver._prefs.discovery_responses = node | (region << 1);
  receiver._prefs.advert_interval = 0;
  receiver._prefs.flood_advert_interval = 0;
  receiver.pending_discover_tag = 0x78563412;
  auto packet = request();
  packet.payload[0] = CTL_TYPE_NODE_DISCOVER_RESP | ADV_TYPE_REPEATER;
  packet.payload_len = 6 + PUB_KEY_SIZE;
  std::memset(packet.payload + 6, 0xAB, PUB_KEY_SIZE);
  receiver.onControlDataRecv(&packet);
  require(receiver.neighbours == 1 && receiver.neighbour_timestamp == 1000
          && receiver.neighbour_snr == -5.0f && receiver.neighbour_rssi == -90,
          "hidden node lost matching discovery response bookkeeping");
  require(std::memcmp(receiver.neighbour.pub_key, packet.payload + 6, PUB_KEY_SIZE) == 0,
          "neighbour response public key changed");
  packet.payload[2] ^= 1;
  receiver.onControlDataRecv(&packet);
  packet.payload[2] ^= 1;
  packet.payload_len -= 1;
  receiver.onControlDataRecv(&packet);
  packet.payload_len += 1;
  packet.payload[0] = CTL_TYPE_NODE_DISCOVER_RESP | 3;
  receiver.onControlDataRecv(&packet);
  packet.payload[0] = CTL_TYPE_NODE_DISCOVER_RESP | ADV_TYPE_REPEATER;
  std::memcpy(packet.payload + 6, receiver.self_id.pub_key, PUB_KEY_SIZE);
  receiver.onControlDataRecv(&packet);
  require(receiver.neighbours == 1, "unrelated/truncated/self response added a neighbour");
  now_ms = receiver.pending_discover_until + 1;
  receiver.onControlDataRecv(&packet);
  require(receiver.neighbours == 1 && receiver.pending_discover_tag == 0,
          "expired discovery response retained its pending request");
  now_ms = 100;
  }
 }
}
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  if (std::strcmp(argv[1], "policy") == 0) policy();
  else if (std::strcmp(argv[1], "gates") == 0) gates();
  else if (std::strcmp(argv[1], "response_switches") == 0) response_switches();
  else if (std::strcmp(argv[1], "wire") == 0) wire();
  else if (std::strcmp(argv[1], "radio2_wire") == 0) radio2_wire();
  else if (std::strcmp(argv[1], "radio2_disabled") == 0) radio2_disabled();
  else if (std::strcmp(argv[1], "received") == 0) received();
  else return 2;
}
'''


class RepeaterDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        handler = extract_braced(source, "void MyMesh::onControlDataRecv(")
        dispatcher = extract_braced((ROOT / "src/Dispatcher.cpp").read_text(),
                                    "uint8_t Dispatcher::getTransmitProfileMask(")
        hidden = extract_braced(header, "bool isHiddenNode() const")
        constants = []
        for text, name in (
            (source, "CTL_TYPE_NODE_DISCOVER_REQ"),
            (source, "CTL_TYPE_NODE_DISCOVER_RESP"),
            ((ROOT / "src/helpers/AdvertDataHelpers.h").read_text(), "ADV_TYPE_REPEATER"),
            ((ROOT / "src/MeshCore.h").read_text(), "PUB_KEY_SIZE"),
            ((ROOT / "src/Packet.h").read_text(), "PAYLOAD_TYPE_CONTROL"),
            ((ROOT / "src/Packet.h").read_text(), "PAYLOAD_TYPE_OTA"),
        ):
            constants.append(re.search(rf"^#define {name}\s+[^\n]+", text, re.M).group())
        limiter = re.search(r"discover_limiter\(\d+,\s*\d+\)", source).group()
        limiter = limiter.replace("(", "{", 1).replace(")", "}", 1)
        mutant, replacements = re.subn(r"&&\s*!isHiddenNode\(\)", "", handler, count=1)
        if replacements != 1:
            raise AssertionError("production discovery handler must contain the hidden-node gate")
        cls.temporary = tempfile.TemporaryDirectory(prefix="repeater-discovery-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binaries = {}
        for name, body in (("production", handler), ("without_hidden_guard", mutant)):
            code = (HARNESS.replace("@CONSTANTS@", "\n".join(constants))
                    .replace("@LIMITER@", limiter)
                    .replace("@HIDDEN@", hidden)
                    .replace("@DISPATCHER@", dispatcher)
                    .replace("@HANDLER@", body))
            work = Path(cls.temporary.name)
            cpp, binary = work / f"{name}.cpp", work / name
            cpp.write_text(code)
            compiled = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Wno-reorder",
                *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
                "-I", str(ROOT), str(cpp), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if compiled.returncode != 0:
                raise AssertionError(compiled.stderr)
            cls.binaries[name] = binary

    def run_case(self, case, binary="production"):
        return subprocess.run([str(self.binaries[binary]), case], capture_output=True,
                              text=True, timeout=10)

    def test_advert_visibility_and_repeat_off_observer_policy(self):
        checked = self.run_case("policy")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_request_filter_timestamp_and_real_rate_limiter(self):
        checked = self.run_case("gates")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_prefix_full_reply_bytes_delay_and_allocation_failure(self):
        checked = self.run_case("wire")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_saved_response_switches_are_independent_and_reject_before_quota(self):
        checked = self.run_case("response_switches")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_hidden_nodes_still_process_matching_discovery_responses(self):
        checked = self.run_case("received")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_requested_active_radio2_trailer_bytes_and_legacy_parser(self):
        checked = self.run_case("radio2_wire")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_unavailable_disabled_and_foreign_profiles_have_only_off_marker(self):
        checked = self.run_case("radio2_disabled")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_removed_hidden_protection_fails_the_same_policy_contract(self):
        checked = self.run_case("policy", "without_hidden_guard")
        self.assertEqual(checked.returncode, 1, checked.stderr)
        self.assertIn("hidden node emitted a discovery response", checked.stderr)


if __name__ == "__main__":
    unittest.main()
