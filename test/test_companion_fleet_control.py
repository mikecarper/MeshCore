#!/usr/bin/env python3
"""Execute the real Companion fleet-send CLI, preserving its client boundary.

Only channel storage and packet admission are mocked here. Protocol encoding,
target hashing and permission checks come from FleetCommand.cpp; OpenSSL
supplies real Ed25519 signing and verification through the Identity interface.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/companion_radio/MyMesh.cpp"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <helpers/FleetCommand.h>
#include <openssl/evp.h>

namespace mesh {
Identity::Identity() { memset(pub_key, 0, sizeof(pub_key)); }
LocalIdentity::LocalIdentity() : Identity() {
  memset(prv_key, 0xAA, 32);
  EVP_PKEY* key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, prv_key, 32);
  assert(key);
  size_t length = sizeof(pub_key);
  assert(EVP_PKEY_get_raw_public_key(key, pub_key, &length) == 1);
  assert(length == sizeof(pub_key));
  memcpy(prv_key + 32, pub_key, sizeof(pub_key));
  EVP_PKEY_free(key);
}
void LocalIdentity::sign(uint8_t* signature, const uint8_t* data, int length) const {
  EVP_PKEY* key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, nullptr, prv_key, 32);
  EVP_MD_CTX* context = EVP_MD_CTX_new();
  assert(key && context);
  assert(EVP_DigestSignInit(context, nullptr, nullptr, nullptr, key) == 1);
  size_t size = SIGNATURE_SIZE;
  assert(EVP_DigestSign(context, signature, &size, data, length) == 1);
  assert(size == SIGNATURE_SIZE);
  EVP_MD_CTX_free(context); EVP_PKEY_free(key);
}
bool Identity::verify(const uint8_t* signature, const uint8_t* data, int length) const {
  EVP_PKEY* key = EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519, nullptr, pub_key, sizeof(pub_key));
  EVP_MD_CTX* context = EVP_MD_CTX_new();
  assert(key && context);
  assert(EVP_DigestVerifyInit(context, nullptr, nullptr, nullptr, key) == 1);
  const bool valid = EVP_DigestVerify(context, signature, SIGNATURE_SIZE, data, length) == 1;
  EVP_MD_CTX_free(context); EVP_PKEY_free(key);
  return valid;
}
}

#define MAX_GROUP_CHANNELS 4
#define OUT_PATH_UNKNOWN 255
struct GroupChannel {
  uint8_t secret[32] = {};
  uint8_t tx_radio = 0;
};
struct ChannelDetails { GroupChannel channel; char name[32] = {}; };
struct Clock { uint32_t now = mesh::FleetCommand::MinEpoch + 1000;
  uint32_t getCurrentTime() const { return now; } };
class MyMesh {
 public:
  mesh::LocalIdentity self_id;
  Clock clock;
  ChannelDetails channels[MAX_GROUP_CHANNELS];
  bool queue_ok = true;
  unsigned queued = 0;
  uint8_t sent_policy = 0;
  uint16_t sent_type = 0;
  uint8_t sent[mesh::FleetCommand::MaxPayloadLength] = {};
  size_t sent_length = 0;
  MyMesh() {
    strcpy(channels[1].name, "private fleet");
    memset(channels[1].channel.secret, 0x21, mesh::FleetCommand::KeySize);
    channels[1].channel.tx_radio = 2;
  }
  bool getChannel(int index, ChannelDetails& out) {
    if (index < 0 || index >= MAX_GROUP_CHANNELS) return false;
    out = channels[index]; return true;
  }
  Clock* getRTCClock() { return &clock; }
  bool sendGroupData(GroupChannel& channel, uint8_t* path, uint8_t path_length,
                     uint16_t type, const uint8_t* data, int length) {
    assert(path == nullptr && path_length == OUT_PATH_UNKNOWN);
    assert(length > 0 && size_t(length) <= sizeof(sent));
    if (!queue_ok) return false;
    sent_policy = channel.tx_radio; sent_type = type;
    memcpy(sent, data, length); sent_length = length; ++queued;
    return true;
  }
  bool handleCommand(const char* command, uint32_t sender_timestamp, char* reply);
};
@METHOD@

static void rejected(MyMesh& node, const char* text, uint32_t remote = 0) {
  char reply[160] = {};
  const unsigned before = node.queued;
  assert(node.handleCommand(text, remote, reply));
  assert(!strncmp(reply, "Error:", 6));
  assert(before == node.queued);
}

static std::string fullKey(uint8_t value) {
  char hex[65];
  for (unsigned i = 0; i < 32; ++i) sprintf(hex + i * 2, "%02x", value);
  return hex;
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  MyMesh node; char reply[160] = {};
  if (scenario == "local_boundary") {
    assert(!node.handleCommand(nullptr, 0, reply));
    assert(!node.handleCommand("fleet send 1 all set radio2 off", 0, nullptr));
    assert(!node.handleCommand("fleetish", 0, reply));
    rejected(node, "fleet send 1 all set radio2 off", 1);
    rejected(node, "fleet send 1 00112233,445566778899 set radio2 off", 1);
    rejected(node, "fleet", 1);
    rejected(node, "fleet send 1 all reboot");
    rejected(node, "fleet send 1 all get prv.key");
    rejected(node, "fleet send 1 all fleet send 1 all set radio2 off");
    assert(node.handleCommand(" \tAB| fleet send 1 all set radio2 off", 0, reply));
    assert(!strncmp(reply, "AB|OK - fleet command queued; seq=", 32));
    assert(node.queued == 1 && node.sent_type == mesh::FleetCommand::DataType);
    assert(node.sent_policy == 2); // The configured radio2 channel route survives.
    assert(!memcmp(node.sent, "FMC1", 4)); // Broadcasts remain compatible with earlier receivers.
  } else if (scenario == "syntax") {
    for (const char* command : {"fleet", "fleet send", "fleet sender 1 all set radio2 off",
        "fleet send -1 all set radio2 off", "fleet send 4 all set radio2 off",
        "fleet send 4294967296 all set radio2 off", "fleet send 1x all set radio2 off",
        "fleet send 1 all", "fleet send 1 a set radio2 off",
        "fleet send 1 11111111111111 set radio2 off", "fleet send 1 all get password",
        "fleet send 1 all set radio2 off\nreboot"}) rejected(node, command);
    rejected(node, ("fleet send 1 " + std::string(65, '1') + " set radio2 off").c_str());
    rejected(node, ("fleet send 1 all set flood.filter " + std::string(150, '1')).c_str());
    assert(node.handleCommand("fleet\tsend\t1\tall\tset radio2 off", 0, reply));
    assert(!strncmp(reply, "OK - fleet command queued", 25));
  } else if (scenario == "private_channel") {
    rejected(node, "fleet send 0 all set radio2 off");
    memset(node.channels[1].channel.secret, 0, 32);
    rejected(node, "fleet send 1 all set radio2 off");
    const uint8_t public_key[16] = {0x8b,0x33,0x87,0xe9,0xc5,0xcd,0xea,0x6a,
                                   0xc9,0xe5,0xed,0xba,0xa1,0x15,0xcd,0x72};
    memcpy(node.channels[1].channel.secret, public_key, sizeof(public_key));
    strcpy(node.channels[1].name, "renamed public");
    rejected(node, "fleet send 1 all set radio2 off");
    memset(node.channels[1].channel.secret, 0x21, 32);
    rejected(node, "fleet send 1 all set radio2 off");
    memset(node.channels[1].channel.secret + 16, 0, 16);
    strcpy(node.channels[1].name, "Public"); // Names are not key authorization.
    assert(node.handleCommand("fleet send 1 all set radio2 off", 0, reply));
    assert(node.queued == 1);
  } else if (scenario == "queue_sequence") {
    node.queue_ok = false;
    rejected(node, "fleet send 1 all set radio2 off");
    node.queue_ok = true;
    assert(node.handleCommand("fleet send 1 all set radio2 off", 0, reply));
    assert(node.queued == 1);
    rejected(node, "fleet send 1 all set radio2 off");
    ++node.clock.now;
    assert(node.handleCommand("fleet send 1 all set radio2 off", 0, reply));
    assert(node.queued == 2);
    --node.clock.now;
    rejected(node, "fleet send 1 all set radio2 off");
  } else if (scenario == "clock") {
    node.clock.now = 0;
    rejected(node, "fleet send 1 all set radio2 off");
    node.clock.now = mesh::FleetCommand::MinEpoch - 1;
    rejected(node, "fleet send 1 all set radio2 off");
    node.clock.now = UINT32_MAX;
    rejected(node, "fleet send 1 all set radio2 off");
    node.clock.now = mesh::FleetCommand::MinEpoch;
    assert(node.handleCommand("fleet send 1 all set radio2 off", 0, reply));
    assert(node.queued == 1);
  } else if (scenario == "signed_target") {
    const std::string command = "fleet send 1 " + fullKey(0x42)
        + " set tempradio2 910.5,500,5,5,rxtx,1";
    assert(node.handleCommand(command.c_str(), 0, reply));
    assert(node.queued == 1);
    assert(!memcmp(node.sent, "FMC1", 4)); // One full key keeps the original envelope.
    uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
    mesh::FleetCommand::Decoded decoded;
    assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
        node.sent, node.sent_length, node.clock.now, recipient, decoded));
    assert(decoded.sequence == node.clock.now);
    assert(decoded.expires == node.clock.now + mesh::FleetCommand::MaxLifetime);
    assert(!strcmp(decoded.command, "set tempradio2 910.5,500,5,5,rxtx,1"));
    recipient[31] ^= 1;
    assert(!mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
        node.sent, node.sent_length, node.clock.now, recipient, decoded));
  } else if (scenario == "mixed_targets") {
    const std::string command = "fleet send 1 42424242,515151515151," + fullKey(0x63)
        + " set radio2 off";
    assert(node.handleCommand(command.c_str(), 0, reply));
    assert(node.queued == 1 && node.sent_policy == 2);
    assert(!memcmp(node.sent, "FMC2", 4));
    mesh::FleetCommand::Decoded decoded;
    uint8_t recipient[32];
    auto matched = [&]() {
      return mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded);
    };
    memset(recipient, 0x42, sizeof(recipient));
    assert(matched());
    assert(!strcmp(decoded.command, "set radio2 off"));
    recipient[31] ^= 1;
    assert(matched()); // An eight-hex target selects the four-byte prefix.
    recipient[3] ^= 1;
    assert(!matched());
    memset(recipient, 0x51, sizeof(recipient));
    assert(matched());
    recipient[31] ^= 1;
    assert(matched()); // Twelve hex selects all six specified prefix bytes.
    recipient[5] ^= 1;
    assert(!matched());
    memset(recipient, 0x63, sizeof(recipient));
    assert(matched());
    recipient[31] ^= 1;
    assert(!matched()); // Full keys retain the complete-key fingerprint.
    memset(recipient, 0x77, sizeof(recipient));
    assert(!matched());
  } else if (scenario == "multiple_full_targets") {
    const std::string command = "fleet send 1 " + fullKey(0x42) + "," + fullKey(0x63)
        + " set radio2 off";
    assert(node.handleCommand(command.c_str(), 0, reply));
    assert(node.queued == 1);
    assert(!memcmp(node.sent, "FMC2", 4));
    mesh::FleetCommand::Decoded decoded;
    uint8_t recipient[32];
    for (uint8_t value : {0x42, 0x63}) {
      memset(recipient, value, sizeof(recipient));
      assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      recipient[31] ^= 1;
      assert(!mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
    }
  } else if (scenario == "invalid_target_lists") {
    for (const char* targets : {"all,00112233", "00112233,all", "00112233,,44556677",
        ",00112233", "00112233,", "0011223", "001122334", "0011223344556",
        "GG112233", "00112233,44556677889Z"}) {
      rejected(node, (std::string("fleet send 1 ") + targets + " set radio2 off").c_str());
    }
    assert(node.handleCommand("fleet send 1 00112233 set radio2 off", 0, reply));
    assert(node.queued == 1); // Invalid lists consumed no sequence or partial send.
  } else if (scenario == "target_capacity") {
    std::string targets;
    for (unsigned i = 1; i <= 17; ++i) {
      char prefix[9]; snprintf(prefix, sizeof(prefix), "%08x", i);
      if (!targets.empty()) targets += ',';
      targets += prefix;
    }
    mesh::FleetCommand::Targets parsed;
    assert(mesh::FleetCommand::parseTargets(targets.c_str(), targets.size(), parsed));
    assert(node.handleCommand(("fleet send 1 " + targets + " set radio2 off").c_str(), 0, reply));
    assert(strstr(reply, "exceed one packet") && node.queued == 0);
    assert(node.handleCommand("fleet send 1 00112233 set radio2 off", 0, reply));
    assert(node.queued == 1);
    ++node.clock.now;
    targets += ",44556677"; // More than the entire target-record capacity.
    rejected(node, ("fleet send 1 " + targets + " set radio2 off").c_str());
    assert(node.queued == 1);
  } else if (scenario == "scheduled_sets") {
    const char* relative[] = {
      "set radioat 910.5,500,5,5,+1,auto",
      "set tempradioat 910.5,500,5,5,+1,+2,32",
      "set radioat2 910.5,500,5,5,rxtx,+1,auto",
      "set tempradioat2 910.5,500,5,5,rx,+1,+2,32",
    };
    uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
    auto sendAndDecode = [&](const std::string& scheduled) {
      const std::string command = "fleet send 1 all " + scheduled;
      rejected(node, command.c_str(), 1);
      const unsigned before = node.queued;
      assert(node.handleCommand(command.c_str(), 0, reply));
      assert(!strncmp(reply, "OK - fleet command queued", 25));
      assert(node.queued == before + 1 && node.sent_policy == 2);
      mesh::FleetCommand::Decoded decoded;
      assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      assert(!strcmp(decoded.command, scheduled.c_str()));
      assert(decoded.sequence == node.clock.now);
      ++node.clock.now;
    };
    for (const char* scheduled : relative) sendAndDecode(scheduled);
    const std::string start = std::to_string(node.clock.now + 60);
    const std::string end = std::to_string(node.clock.now + 120);
    sendAndDecode("set radioat 910.5,500,5,5," + start);
    sendAndDecode("set tempradioat 910.5,500,5,5," + start + "," + end);
    sendAndDecode("set radioat2 910.5,500,5,5,rx," + start);
    sendAndDecode("set tempradioat2 910.5,500,5,5,rxtx," + start + "," + end);
    assert(node.queued == 8);
  } else if (scenario == "schedule_get_delete") {
    uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
    for (const char* scheduled : {
        "get radioat", "get radioat 1", "get radioat 255", "get radioat all",
        "get tempradioat", "get tempradioat 2", "get tempradioat all",
        "get radioat2", "get radioat2 4", "get radioat2 all",
        "get tempradioat2", "get tempradioat2 1", "get tempradioat2 all",
        "del radioat", "del radioat 1", "del radioat all",
        "del tempradioat", "del tempradioat 2", "del tempradioat all",
        "del radioat2", "del radioat2 4", "del radioat2 all",
        "del tempradioat2", "del tempradioat2 1", "del tempradioat2 all"}) {
      const std::string command = "fleet send 1 42424242 " + std::string(scheduled);
      rejected(node, command.c_str(), 100);
      const unsigned before = node.queued;
      assert(node.handleCommand(command.c_str(), 0, reply));
      assert(node.queued == before + 1 && node.sent_policy == 2);
      mesh::FleetCommand::Decoded decoded;
      assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      assert(!strcmp(decoded.command, scheduled));
      ++node.clock.now;
    }
  } else if (scenario == "malformed_schedules") {
    for (const char* scheduled : {
        "set radioat 910.5,500,5,5", "set tempradioat 910.5,500,5,5,+1",
        "set radioat2 910.5,500,5,5,rx", "set tempradioat2 910.5,500,5,5,rxtx,+1",
        "set radioat 910.5,500,5,5,+0", "set radioat 910.5,500,5,5,-1",
        "set radioat 910.5,500,5,5,+1.5", "set radioat 910.5,500,5,5,4294967296",
        "set radioat2 910.5,500,5,5,bogus,+1",
        "set tempradioat2 910.5,500,5,5,rx,+1,+2,auto,extra",
        "set tempradioat 910.5,500,5,5,+1,+2,7",
        "set radioat 910.5,500,5,5,+1,65529",
        "get radioat 256", "get radioat2 5", "get radioat 01", "get radioat 1x",
        "del tempradioat2 0", "del radioat2 all extra", "radioat 910.5,500,5,5,+1"}) {
      rejected(node, ("fleet send 1 all " + std::string(scheduled)).c_str());
    }
    assert(node.handleCommand("fleet send 1 all set radioat 910.5,500,5,5,+1", 0, reply));
    assert(node.queued == 1);
  } else if (scenario == "schedule_packet_budget") {
    const std::string scheduled = "set tempradioat2 910.5,500,5,5,rxtx,+1,+2,auto";
    std::string targets = fullKey(0x42) + "," + fullKey(0x51) + ","
        + fullKey(0x63) + "," + fullKey(0x74);
    mesh::FleetCommand::Targets parsed;
    assert(mesh::FleetCommand::parseTargets(targets.c_str(), targets.size(), parsed));
    assert(mesh::FleetCommand::commandAllowed(scheduled.c_str()));
    assert(node.handleCommand(("fleet send 1 " + targets + " " + scheduled).c_str(), 0, reply));
    assert(strstr(reply, "exceed one packet") && node.queued == 0);
    assert(node.handleCommand(("fleet send 1 42424242 " + scheduled).c_str(), 0, reply));
    assert(node.queued == 1 && node.sent_policy == 2);
  } else if (scenario == "clock_controls") {
    uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
    for (const char* clock_command : {"clock", "clock sync", "time 1800000000"}) {
      const std::string command = "fleet send 1 all " + std::string(clock_command);
      rejected(node, command.c_str(), 1);
      const unsigned before = node.queued;
      assert(node.handleCommand(command.c_str(), 0, reply));
      assert(node.queued == before + 1 && node.sent_policy == 2);
      mesh::FleetCommand::Decoded decoded;
      assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      assert(!strcmp(decoded.command, clock_command));
      ++node.clock.now;
    }
    for (const char* clock_command : {"get clock", "set clock", "clock synctime",
        "clock sync extra", "time", "time -1", "time +1800000000", "time NaN",
        "time 4294967296", "time 1735689599", "time 1800000000 extra"})
      rejected(node, ("fleet send 1 all " + std::string(clock_command)).c_str());
  } else assert(false);
  printf("Companion fleet scenario %s passed\n", argv[1]);
}
'''


class CompanionFleetControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("host C++ compiler required")
        cls.compiler = compiler
        source = SOURCE.read_text()
        function = body(source, "bool MyMesh::handleCommand(const char* command,")
        # Retain the real NULL, whitespace and request-prefix handling too.
        prefix = function[:function.index("#if COMPANION_FEATURE_READER")]
        fleet_start = function.rindex("#if MESH_ENABLE_FLEET_CONTROL", 0,
                                      function.index('if (!strncmp(command, "fleet", 5)'))
        fleet_end = function.index("  if (_radio_profiles.handle", fleet_start)
        # Keep the actual compile guard as well as its complete branch so
        # compact profiles prove that none of the fleet dependencies survive.
        cls.production = prefix + function[fleet_start:fleet_end] + "\n  return false;\n}\n"
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-fleet-companion-")
        work = Path(cls.work.name)
        cls.binary = work / "companion-fleet"
        fixture = work / "fixture.cpp"
        fixture.write_text(HARNESS.replace("@METHOD@", cls.production))
        result = subprocess.run([
            compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-O2",
            "-fstack-usage", "-isystem", str(ROOT / "test/mocks"),
            "-I", str(ROOT / "src"), str(fixture),
            str(ROOT / "src/helpers/FleetCommand.cpp"), "-lcrypto", "-o", str(cls.binary),
        ], capture_output=True, text=True, timeout=60)
        if result.returncode:
            cls.work.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_local_client_boundary_and_channel_tx_route(self):
        self.scenario("local_boundary")

    def test_invalid_syntax_targets_and_forbidden_commands(self):
        self.scenario("syntax")

    def test_private_128_bit_key_required_even_if_channel_is_renamed(self):
        self.scenario("private_channel")

    def test_queue_failure_does_not_consume_sequence_and_success_does(self):
        self.scenario("queue_sequence")

    def test_invalid_clock_and_timestamp_overflow_never_queue(self):
        self.scenario("clock")

    def test_production_encode_targets_full_key_and_sets_bounded_expiry(self):
        self.scenario("signed_target")

    def test_mixed_prefix_and_full_key_list_matches_only_selected_nodes(self):
        self.scenario("mixed_targets")

    def test_multiple_full_keys_share_one_signed_packet(self):
        self.scenario("multiple_full_targets")

    def test_invalid_lists_reject_atomically_without_consuming_sequence(self):
        self.scenario("invalid_target_lists")

    def test_valid_list_exceeding_packet_capacity_never_partially_sends(self):
        self.scenario("target_capacity")

    def test_four_schedule_set_forms_are_signed_without_rewriting(self):
        self.scenario("scheduled_sets")

    def test_schedule_get_and_delete_forms_preserve_access_and_tx_route(self):
        self.scenario("schedule_get_delete")

    def test_malformed_and_truncated_schedules_never_queue_or_consume_sequence(self):
        self.scenario("malformed_schedules")

    def test_schedule_and_target_list_share_one_packet_budget(self):
        self.scenario("schedule_packet_budget")

    def test_exact_clock_controls_are_signed_and_remote_origin_is_denied(self):
        self.scenario("clock_controls")

    def test_disabled_profiles_require_no_sender_peripherals_or_crypto_link(self):
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <helpers/FleetCommand.h>
static_assert(MESH_ENABLE_FLEET_CONTROL == 0, "compact profile must disable fleet");
// No channels, clock, signing identity, codec definitions or sendGroupData.
class MyMesh {
 public:
  bool handleCommand(const char*, uint32_t, char*);
};
@METHOD@
int main() {
  MyMesh node;
  char reply[160] = {};
  assert(!node.handleCommand("fleet send 1 all set radio2 off", 0, reply));
  assert(!node.handleCommand("fleet send 1 all set radio2 off", 1, reply));
  assert(!node.handleCommand("AB|fleet send 1 all set radio2 off", 0, reply));
}
'''
        work = Path(self.work.name)
        source = work / "disabled.cpp"
        source.write_text(harness.replace("@METHOD@", self.production))
        for profile, flags in (("stm32-default", ["-DSTM32_PLATFORM=1"]),
                               ("explicit-disable", ["-DMESH_ENABLE_FLEET_CONTROL=0"])):
            with self.subTest(profile=profile):
                executable = work / profile
                built = subprocess.run([
                    self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-isystem", str(ROOT / "test/mocks"),
                    "-I", str(ROOT / "src"), *flags, str(source), "-o", str(executable),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                result = subprocess.run([str(executable)], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
