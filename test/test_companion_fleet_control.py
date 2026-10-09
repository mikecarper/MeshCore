#!/usr/bin/env python3
"""Execute the real Companion fleet-send CLI, preserving its client boundary.

Only channel storage and packet admission are mocked here. Protocol encoding,
target hashing and permission checks come from FleetCommand.cpp; OpenSSL
supplies real Ed25519 signing and verification through the Identity interface.
"""

from pathlib import Path
import os
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
#include <vector>
#include <helpers/FleetCommand.h>
#include <helpers/CompanionFrameLimits.h>
#include <openssl/evp.h>

struct RescueStream : Stream {
  std::string input, output;
  size_t position = 0, reads = 0;
  int available() override { return position < input.size(); }
  int read() override { ++reads; return input[position++]; }
  size_t write(uint8_t value) override { output += char(value); return 1; }
};
static RescueStream rescue_input, rescue_output;

namespace mesh {
Stream& usbTerminalPort() { return rescue_output; }
Stream& usbCompanionPort() { return rescue_input; }
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
namespace mesh {
struct GroupChannel {
  uint8_t secret[32] = {};
  uint8_t tx_radio = 0;
};
struct Packet {
  uint8_t payload[168] = {};
  size_t payload_len = 0;
  uint8_t tx_radio = 0;
  uint16_t scope = 0;
  bool isRouteDirect() const { return false; }
  uint8_t getPayloadType() const { return 6; }
};
struct Mesh {
  unsigned base_copy_hooks = 0;
  void onRadioProfileCopyQueued(Packet*, const Packet*, uint8_t) { ++base_copy_hooks; }
};
}
using GroupChannel = mesh::GroupChannel;
#define PAYLOAD_TYPE_GRP_DATA 6
#define PAYLOAD_TYPE_TRACE 9

struct PacketManager {
  mesh::Packet pool[64];
  bool used[64] = {};
  std::vector<mesh::Packet*> outbound;
  unsigned total = 0, allocations = 0, released = 0;
  unsigned fail_allocation = 0;
  mesh::Packet* allocNew() {
    if (++allocations == fail_allocation) return nullptr;
    for (unsigned i = 0; i < 64; ++i) if (!used[i]) {
      used[i] = true; pool[i] = {}; return &pool[i];
    }
    return nullptr;
  }
  void free(mesh::Packet* packet) {
    const ptrdiff_t index = packet - pool;
    assert(index >= 0 && index < 64 && used[index]);
    used[index] = false; ++released;
  }
  unsigned freeCount() const { unsigned count = 0; for (bool value : used) count += !value; return count; }
  int getOutboundTotal() const { return int(total); }
  mesh::Packet* getOutboundByIdx(int index) { return outbound.at(index); }
  mesh::Packet* removeOutboundByIdx(int index) {
    auto* packet = outbound.at(index); outbound.erase(outbound.begin() + index); --total;
    return packet;
  }
  void queue(mesh::Packet* packet) { outbound.push_back(packet); ++total; }
};
struct ChannelDetails { GroupChannel channel; char name[32] = {}; };
struct Clock { uint32_t now = mesh::FleetCommand::MinEpoch + 1000;
  uint32_t getCurrentTime() const { return now; } };
class MyMesh : public mesh::Mesh {
 public:
  mesh::LocalIdentity self_id;
  Clock clock;
  ChannelDetails channels[MAX_GROUP_CHANNELS];
  bool queue_ok = true;
  PacketManager manager;
  PacketManager* _mgr = &manager;
  unsigned& queued = manager.total;
  unsigned queue_calls = 0, fail_queue = 0, failed_hooks = 0, radio_cancellations = 0;
  bool radio_copy = false;
  mesh::Packet* _fleet_admission_original = nullptr;
  mesh::Packet* _fleet_admission_copy = nullptr;
  char cli_command[80] = {};
  bool _cli_line_overflow = false;
  bool _cli_rescue = false;
  std::vector<std::string> rescue_commands;
  uint8_t sent_policy = 0;
  uint16_t sent_type = 0;
  uint8_t sent[mesh::FleetCommand::MaxEnvelopeLength] = {};
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
  mesh::Packet* createGroupDatagram(uint8_t type, const GroupChannel& channel,
                                  const uint8_t* data, size_t length) {
    assert(type == PAYLOAD_TYPE_GRP_DATA && length <= sizeof(mesh::Packet::payload));
    auto* packet = manager.allocNew();
    if (!packet) return nullptr;
    packet->tx_radio = channel.tx_radio; packet->payload_len = length;
    memcpy(packet->payload, data, length); return packet;
  }
  void releasePacket(mesh::Packet* packet) { manager.free(packet); }
  void onSendFail(mesh::Packet*) { ++failed_hooks; }
  void cancelOutboundRadioRetry(const mesh::Packet*) { ++radio_cancellations; }
  void onTracePacketQueuedForSend(mesh::Packet*) { assert(false); }
  bool queueOutboundPacket(mesh::Packet* packet, uint8_t, uint32_t) {
    if (++queue_calls == fail_queue || !queue_ok) return false;
    manager.queue(packet);
    sent_policy = packet->tx_radio;
    sent_type = uint16_t(packet->payload[0]) | uint16_t(packet->payload[1]) << 8;
    const uint8_t* blob = packet->payload + 3;
    const size_t length = packet->payload[2];
    assert(length + 3 == packet->payload_len && length <= mesh::FleetCommand::MaxPayloadLength);
    mesh::FleetCommand::Fragment part;
    if (mesh::FleetCommand::parseFragment(blob, length, part)) {
      memcpy(sent + part.index * mesh::FleetCommand::FragmentDataLength, part.data, part.length);
      sent_length = part.total_length;
    } else {
      memcpy(sent, blob, length); sent_length = length;
    }
    if (radio_copy) {
      auto* copy = manager.allocNew(); assert(copy); *copy = *packet;
      manager.queue(copy); onRadioProfileCopyQueued(copy, packet, 1);
    }
    return true;
  }
  bool sendPacket(mesh::Packet*, uint8_t, uint32_t = 0);
  bool sendFloodScoped(const GroupChannel& channel, mesh::Packet* packet, uint32_t = 0) {
    assert(packet->tx_radio == channel.tx_radio);
    packet->scope = 0x4321; // Scope adapter; production helper preserves this channel route.
    return sendPacket(packet, 1);
  }
  bool sendGroupData(GroupChannel& channel, uint8_t* path, uint8_t path_length,
                     uint16_t type, const uint8_t* data, int length) {
    assert(path == nullptr && path_length == OUT_PATH_UNKNOWN);
    assert(length > 0 && size_t(length) <= mesh::FleetCommand::MaxPayloadLength);
    uint8_t group[168] = {uint8_t(type), uint8_t(type >> 8), uint8_t(length)};
    memcpy(group + 3, data, length);
    auto* packet = createGroupDatagram(PAYLOAD_TYPE_GRP_DATA, channel, group, 3 + length);
    return packet && sendFloodScoped(channel, packet);
  }
  bool sendFleetCommandData(GroupChannel&, const uint8_t*, size_t);
  void onRadioProfileCopyQueued(mesh::Packet*, const mesh::Packet*, uint8_t);
  bool handleCommand(const char* command, uint32_t sender_timestamp, char* reply);
  void checkCLIRescueCmd();
  void enterCLIRescue();
  void resetUsbHostSessionInput();
};
@METHOD@
@SEND_HELPERS@
@RESCUE_READER@

static void rejected(MyMesh& node, const char* text, uint32_t remote = 0) {
  char reply[160] = {};
  const unsigned before = node.queued;
  assert(node.handleCommand(text, remote, reply));
  const char* result = strlen(reply) > 3 && reply[2] == '|' ? reply + 3 : reply;
  assert(!strncmp(result, "Error:", 6));
  assert(before == node.queued);
}

static std::string fullKey(uint8_t value) {
  char hex[65];
  for (unsigned i = 0; i < 32; ++i) sprintf(hex + i * 2, "%02x", value);
  return hex;
}

static void checkedFragments(MyMesh& node, size_t first_index) {
  assert(node.sent_length > mesh::FleetCommand::MaxPayloadLength);
  uint8_t assembled[mesh::FleetCommand::MaxEnvelopeLength] = {};
  // Read in reverse order; the immutable part index determines placement.
  for (uint8_t index : {1, 0}) {
    auto* packet = node.manager.outbound.at(first_index + index);
    assert(packet->scope == 0x4321 && packet->tx_radio == 2);
    assert(packet->payload[0] == 1 && packet->payload[1] == 0xff);
    const size_t bytes = packet->payload[2];
    mesh::FleetCommand::Fragment part;
    assert(mesh::FleetCommand::parseFragment(packet->payload + 3, bytes, part));
    assert(part.index == index && part.total_length == node.sent_length);
    assert(part.sequence == node.clock.now && bytes <= mesh::FleetCommand::MaxPayloadLength);
    assert(part.length == (index == 0 ? 154 : node.sent_length - 154));
    assert(bytes == mesh::FleetCommand::FragmentHeaderSize + part.length);
    memcpy(assembled + index * 154, part.data, part.length);
    uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
    mesh::FleetCommand::Decoded decoded;
    assert(!mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
        packet->payload + 3, bytes, node.clock.now, recipient, decoded));
  }
  assert(!memcmp(assembled, node.sent, node.sent_length));
  uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
  mesh::FleetCommand::Decoded decoded;
  assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
      assembled, node.sent_length, node.clock.now, recipient, decoded));
  assembled[node.sent_length - 1] ^= 1;
  assert(!mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
      assembled, node.sent_length, node.clock.now, recipient, decoded));
}

static void compactBroadcast(MyMesh& node, const char* command) {
  assert(!memcmp(node.sent, "FMC2", 4));
  assert(node.sent[12] == 0); // No target records means every authorized receiver.
  assert(node.sent[13] == strlen(command));
  assert(mesh::FleetCommand::MinHeaderSize == 14);
  assert(node.sent_length == 14 + strlen(command) + mesh::FleetCommand::SignatureSize);
  assert(!memcmp(node.sent + 14, command, strlen(command)));
  if (node.sent_length > mesh::FleetCommand::MaxPayloadLength)
    checkedFragments(node, node.manager.outbound.size() - 2);
  for (uint8_t value : {0x42, 0x77}) {
    uint8_t recipient[32]; memset(recipient, value, sizeof(recipient));
    mesh::FleetCommand::Decoded decoded;
    assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
        node.sent, node.sent_length, node.clock.now, recipient, decoded));
    assert(decoded.broadcast && !strcmp(decoded.command, command));
    assert(decoded.sequence == node.clock.now);
    assert(decoded.expires == node.clock.now + mesh::FleetCommand::MaxLifetime);
  }
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
    compactBroadcast(node, "set radio2 off");
  } else if (scenario == "implicit_broadcast") {
    for (const char* command : {"set radio2 off", "get radio2.status", "del flood.filter all",
        "clock", "clock sync", "time 1800000000",
        "set tempradioat2 910.5,500,5,5,rxtx,+1,+2,auto"}) {
      const std::string implicit = std::string("fleet send 1 ") + command;
      assert(node.handleCommand(implicit.c_str(), 0, reply));
      assert(!strncmp(reply, "OK - fleet command queued", 25));
      assert(node.sent_policy == 2 && node.sent_type == mesh::FleetCommand::DataType);
      compactBroadcast(node, command);
      ++node.clock.now;
      const std::string explicit_all = std::string("fleet send 1 all ") + command;
      assert(node.handleCommand(explicit_all.c_str(), 0, reply));
      compactBroadcast(node, command);
      ++node.clock.now;
    }
    assert(node.queued == 14);
  } else if (scenario == "implicit_local_prefix_tabs") {
    rejected(node, "fleet send 1 set radio2 off", 1);
    rejected(node, "AB|fleet send 1 set radio2 off", 100);
    rejected(node, "AB|fleet\tsend\t1\tset radio2 off", 1);
    assert(node.handleCommand(" \tAB| fleet\tsend\t1\tset radio2 off", 0, reply));
    assert(!strncmp(reply, "AB|OK - fleet command queued; seq=", 32));
    compactBroadcast(node, "set radio2 off");
    assert(node.queued == 1 && node.sent_policy == 2);
    ++node.clock.now;
    assert(node.handleCommand("CD|fleet\tsend\t1\tall\tclock sync", 0, reply));
    assert(!strncmp(reply, "CD|OK - fleet command queued; seq=", 32));
    compactBroadcast(node, "clock sync");
    assert(node.queued == 2);
  } else if (scenario == "implicit_retry_sequence") {
    node.queue_ok = false;
    rejected(node, "fleet send 1 set radio2 off");
    node.queue_ok = true;
    assert(node.handleCommand("fleet send 1 all set radio2 off", 0, reply));
    compactBroadcast(node, "set radio2 off");
    rejected(node, "fleet send 1 set radio2 off");
    ++node.clock.now;
    assert(node.handleCommand("EF|fleet send 1 set radio2 off", 0, reply));
    assert(!strncmp(reply, "EF|OK - fleet command queued; seq=", 32));
    compactBroadcast(node, "set radio2 off");
    rejected(node, "fleet send 1 all set radio2 off");
    --node.clock.now;
    rejected(node, "fleet send 1 clock sync");
    node.clock.now += 2;
    rejected(node, "fleet send 1 nonsense set radio2 off");
    assert(node.handleCommand("fleet send 1 clock sync", 0, reply));
    compactBroadcast(node, "clock sync");
    assert(node.queued == 3);
  } else if (scenario == "implicit_target_ambiguity") {
    for (const char* targets : {"nonsense", "All", "ALL", "all,42424242", "42424242,all",
        "allall", "4242424", "424242424", "4242424242424", "GG424242",
        "42424242,,51515151", "42424242,", ",42424242", "set", "get", "clock"}) {
      rejected(node, (std::string("fleet send 1 ") + targets + " set radio2 off").c_str());
    }
    rejected(node, ("fleet send 1 " + fullKey(0) + " set radio2 off").c_str());
    rejected(node, ("fleet send 1 " + std::string(65, '4') + " set radio2 off").c_str());
    assert(node.handleCommand("fleet send 1 get radio2", 0, reply));
    compactBroadcast(node, "get radio2");
    assert(node.queued == 1); // No typo became a broadcast or consumed a sequence.
  } else if (scenario == "implicit_command_controls") {
    for (const char* command : {"reboot", "get prv.key", "get password", "get clock",
        "set radio2 of", "clock sync extra", "set radio2 off\nreboot", "clock\r",
        "set\tradio2 off", "set radio2  off", "clock sync ", "clock\x7f",
        "set radio2 off;reboot", "set radio2 off|reboot", "set radio2 off&reboot",
        "set flood.filter `reboot`", "set flood.filter \\reboot"}) {
      rejected(node, (std::string("fleet send 1 ") + command).c_str());
      rejected(node, (std::string("fleet send 1 all ") + command).c_str());
    }
    const std::string prefix = "set flood.filter ";
    const std::string maximum = prefix + std::string(mesh::FleetCommand::MaxCommandLength - prefix.size(), '1');
    assert(maximum.size() == 230 && mesh::FleetCommand::commandAllowed(maximum.c_str()));
    rejected(node, ("fleet send 1 " + maximum + "1").c_str());
    rejected(node, ("fleet send 1 all " + maximum + "1").c_str());
    assert(node.handleCommand(("fleet send 1 " + maximum).c_str(), 0, reply));
    compactBroadcast(node, maximum.c_str());
    assert(node.sent_length == 308 && node.queued == 2);
    ++node.clock.now;
    assert(node.handleCommand(("fleet send 1 all " + maximum).c_str(), 0, reply));
    compactBroadcast(node, maximum.c_str());
    assert(node.sent_length == 308 && node.queued == 4);
  } else if (scenario == "target_command_capacity") {
    struct Boundary { std::string targets; size_t command_length; };
    const Boundary boundaries[] = {
      {"42424242", 225}, {"424242424242", 223}, {fullKey(0x42), 215},
      {"42424242,51515151", 220}, {"42424242,515151515151", 218},
      {fullKey(0x42) + "," + fullKey(0x51), 196},
    };
    const std::string prefix = "set flood.filter ";
    for (const Boundary& boundary : boundaries) {
      const std::string fitting = prefix + std::string(boundary.command_length - prefix.size(), '1');
      const std::string oversize = fitting + '1';
      assert(mesh::FleetCommand::commandAllowed(oversize.c_str()));
      const std::string cli_prefix = "fleet send 1 " + boundary.targets + " ";
      const unsigned before = node.queued;
      rejected(node, (cli_prefix + oversize).c_str());
      // A valid target plus a long command must not become a broadcast, a
      // partial send, or a consumed sequence. A queue refusal is retryable too.
      assert(node.handleCommand((cli_prefix + oversize).c_str(), 0, reply));
      assert(strstr(reply, "exceed two packets") && node.queued == before);
      node.queue_ok = false;
      rejected(node, (cli_prefix + fitting).c_str());
      node.queue_ok = true;
      assert(node.handleCommand((cli_prefix + fitting).c_str(), 0, reply));
      assert(node.queued == before + 2 && node.sent_length == 308);
      assert(node.sent_policy == 2);
      uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
      mesh::FleetCommand::Decoded decoded;
      assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      assert(decoded.sequence == node.clock.now && !strcmp(decoded.command, fitting.c_str()));
      assert(!memcmp(node.sent, boundary.targets.size() == 64 ? "FMC1" : "FMC2", 4));
      memset(recipient, 0x77, sizeof(recipient));
      assert(!mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      rejected(node, (cli_prefix + fitting).c_str()); // Successful send consumes its second.
      ++node.clock.now;
    }
    assert(node.queued == 12);
  } else if (scenario == "maximum_correlated_app_command") {
    const std::string prefix = "set flood.filter ";
    const std::string maximum = prefix + std::string(158 - prefix.size(), '1');
    const std::string local_cli = "AB|fleet send 1 " + maximum;
    // The app's local CLI frame adds opcode 66 and a trailing NUL. Its request
    // correlation prefix is already inside local_cli; all are ASCII bytes.
    assert(local_cli.size() + 2 == MAX_FRAME_SIZE);
    assert((local_cli + '1').size() + 2 > MAX_FRAME_SIZE);
    rejected(node, local_cli.c_str(), 1);
    node.queue_ok = false;
    rejected(node, local_cli.c_str());
    node.queue_ok = true;
    assert(node.handleCommand(local_cli.c_str(), 0, reply));
    assert(!strncmp(reply, "AB|OK - fleet command queued; seq=", 32));
    compactBroadcast(node, maximum.c_str());
    assert(node.sent_length == 236 && node.queued == 2);
    assert(node.sent_policy == 2);
    rejected(node, local_cli.c_str());
    ++node.clock.now;
    const std::string unprefixed = prefix + std::string(161 - prefix.size(), '1');
    const std::string implicit = "fleet send 1 " + unprefixed;
    assert(implicit.size() + 2 == MAX_FRAME_SIZE);
    assert((implicit + '1').size() + 2 > MAX_FRAME_SIZE);
    assert(node.handleCommand(implicit.c_str(), 0, reply));
    compactBroadcast(node, unprefixed.c_str());
    assert(node.sent_length == 239 && node.queued == 4);
  } else if (scenario == "fragment_boundaries") {
    struct Boundary { std::string targets; size_t single_length; };
    const Boundary boundaries[] = {
      {"", 87}, {"42424242 ", 82}, {"424242424242 ", 80}, {fullKey(0x42) + " ", 72},
    };
    const std::string prefix = "set flood.filter ";
    for (const Boundary& boundary : boundaries) {
      std::string command = prefix + std::string(boundary.single_length - prefix.size(), '1');
      const std::string cli_prefix = "fleet send 1 " + boundary.targets;
      const size_t before = node.manager.outbound.size();
      assert(node.handleCommand((cli_prefix + command).c_str(), 0, reply));
      assert(node.queued == before + 1 && node.sent_length == 165);
      assert(!memcmp(node.manager.outbound.back()->payload + 3,
          boundary.targets.size() == 65 ? "FMC1" : "FMC2", 4));
      ++node.clock.now; command += '1';
      assert(node.handleCommand((cli_prefix + command).c_str(), 0, reply));
      assert(node.queued == before + 3 && node.sent_length == 166);
      checkedFragments(node, before + 1);
      ++node.clock.now;
    }
    assert(node.queued == 12);
  } else if (scenario == "fragment_atomic_admission") {
    unsigned scenario_index = 0;
    for (const char* failure : {"pool_first", "pool_second", "queue_first", "queue_second", "queue_second_copy"}) {
      MyMesh trial; trial.clock.now += ++scenario_index * 10;
      const std::string prefix = "set flood.filter ";
      const std::string command = prefix + std::string(161 - prefix.size(), '1');
      const std::string cli = "fleet send 1 " + command;
      auto* unrelated = trial.manager.allocNew(); assert(unrelated);
      unrelated->payload[0] = 99; trial.manager.queue(unrelated);
      const unsigned free_before = trial.manager.freeCount();
      if (!strcmp(failure, "pool_first")) trial.manager.fail_allocation = trial.manager.allocations + 1;
      if (!strcmp(failure, "pool_second")) trial.manager.fail_allocation = trial.manager.allocations + 2;
      if (!strcmp(failure, "queue_first")) trial.fail_queue = trial.queue_calls + 1;
      if (!strcmp(failure, "queue_second")) trial.fail_queue = trial.queue_calls + 2;
      if (!strcmp(failure, "queue_second_copy")) {
        trial.fail_queue = trial.queue_calls + 2; trial.radio_copy = true;
      }
      rejected(trial, cli.c_str());
      assert(trial.queued == 1 && trial.manager.outbound[0] == unrelated);
      assert(trial.manager.freeCount() == free_before);
      assert(!trial._fleet_admission_original && !trial._fleet_admission_copy);
      if (!strcmp(failure, "queue_first")) assert(trial.failed_hooks == 1);
      if (!strcmp(failure, "queue_second"))
        assert(trial.failed_hooks == 2 && trial.radio_cancellations == 1);
      if (!strcmp(failure, "queue_second_copy")) {
        assert(trial.failed_hooks == 3 && trial.radio_cancellations == 2);
        assert(trial.base_copy_hooks == 1); // The production override chains Mesh.
      }
      trial.manager.fail_allocation = 0; trial.fail_queue = 0; trial.radio_copy = false;
      assert(trial.handleCommand(cli.c_str(), 0, reply));
      assert(trial.queued == 3 && trial.manager.outbound[0] == unrelated);
      compactBroadcast(trial, command.c_str());
      assert(trial.manager.freeCount() == free_before - 2);
    }
  } else if (scenario == "rescue_overflow") {
    const std::string unsafe = "fleet send 1 set flood.rule.1 type=grp_txt drop hops=all in=any priority=00001 suspend=tempradio";
    assert(unsafe.size() > sizeof(node.cli_command));
    // The old forced-completion behavior would execute this valid prefix,
    // silently omitting the requested suspend=tempradio condition.
    const std::string truncated = unsafe.substr(0, 78);
    assert(mesh::FleetCommand::commandAllowed(truncated.c_str() + 13));
    rescue_input.input = unsafe + "\r\nfleet send 1 clock\r\n";
    while (rescue_input.available()) {
      const size_t before = rescue_input.reads;
      node.checkCLIRescueCmd();
      assert(rescue_input.reads - before <= sizeof(node.cli_command));
    }
    assert(node.rescue_commands.size() == 1 && node.rescue_commands[0] == "fleet send 1 clock");
    assert(node.queued == 1 && !node._cli_line_overflow && node.cli_command[0] == 0);
    assert(rescue_output.output.find("ERR: too long") != std::string::npos);
    compactBroadcast(node, "clock");
  } else if (scenario == "rescue_split_overflow") {
    rescue_input.input = std::string(400, 'x');
    node.checkCLIRescueCmd();
    assert(rescue_input.reads == sizeof(node.cli_command) && node._cli_line_overflow);
    assert(node.rescue_commands.empty() && node.queued == 0);
    while (rescue_input.available()) node.checkCLIRescueCmd();
    assert(node._cli_line_overflow && node.rescue_commands.empty());
    // Even a valid fleet suffix is discarded until the actual line ending.
    rescue_input.input += "fleet send 1 set radio2 off\nfleet send 1 clock\n";
    while (rescue_input.available()) node.checkCLIRescueCmd();
    assert(node.rescue_commands.size() == 1 && node.rescue_commands[0] == "fleet send 1 clock");
    assert(node.queued == 1 && !node._cli_line_overflow);
  } else if (scenario == "rescue_boundaries") {
    const std::string exact(79, 'x');
    rescue_input.input = exact;
    node.checkCLIRescueCmd();
    assert(node.rescue_commands.empty() && strlen(node.cli_command) == 79);
    rescue_input.input += "\r\nfirst\r\nsecond\nthird\r";
    while (rescue_input.available()) node.checkCLIRescueCmd();
    const std::vector<std::string> expected = {exact, "first", "second", "third"};
    assert(node.rescue_commands == expected && !node._cli_line_overflow);
    assert(node.cli_command[0] == 0);
  } else if (scenario == "rescue_unterminated") {
    memset(node.cli_command, 'x', sizeof(node.cli_command));
    rescue_input.input = "fleet send 1 set radio2 off\r\nfleet send 1 clock\r";
    while (rescue_input.available()) node.checkCLIRescueCmd();
    assert(node.rescue_commands.size() == 1 && node.rescue_commands[0] == "fleet send 1 clock");
    assert(node.queued == 1 && !node._cli_line_overflow);
  } else if (scenario == "rescue_session_reset") {
    node._cli_line_overflow = true; strcpy(node.cli_command, "old host prefix");
    node.enterCLIRescue();
    assert(node._cli_rescue && !node._cli_line_overflow && node.cli_command[0] == 0);
    rescue_input.input = "fleet send 1 clock\r";
    while (rescue_input.available()) node.checkCLIRescueCmd();
    assert(node.queued == 1);
    ++node.clock.now;
    node._cli_line_overflow = true; strcpy(node.cli_command, "old host prefix");
    node.resetUsbHostSessionInput();
    assert(!node._cli_line_overflow);
    for (char value : node.cli_command) assert(value == 0);
    rescue_input.input += "fleet send 1 clock\n";
    while (rescue_input.available()) node.checkCLIRescueCmd();
    assert(node.queued == 2 && node.rescue_commands.size() == 2);
  } else if (scenario == "stm32_rescue") {
    rescue_input.input = std::string(400, 'x') + "fleet send 1 clock\r\nnext\r\n";
    while (rescue_input.available()) {
      const size_t before = rescue_input.reads;
      node.checkCLIRescueCmd();
      assert(rescue_input.reads - before <= sizeof(node.cli_command));
    }
    assert(node.rescue_commands == std::vector<std::string>{"next"});
    assert(rescue_output.output.find("  Error: unknown command") != std::string::npos);
    assert(rescue_output.output.find("ERR: too long") == std::string::npos);
    assert(node.queued == 0 && !node._cli_line_overflow);
    memset(node.cli_command, 'x', sizeof(node.cli_command));
    rescue_input.input += "suffix\nlast\n";
    while (rescue_input.available()) node.checkCLIRescueCmd();
    assert(node.rescue_commands == (std::vector<std::string>{"next", "last"}));
  } else if (scenario == "explicit_target_forms") {
    for (const std::string& targets : {std::string("42424242"), std::string("424242424242"),
        fullKey(0x42), std::string("42424242,515151515151,") + fullKey(0x63)}) {
      assert(node.handleCommand(("fleet send 1 " + targets + " set radio2 off").c_str(), 0, reply));
      uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
      mesh::FleetCommand::Decoded decoded;
      assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      assert(!strcmp(decoded.command, "set radio2 off"));
      // Prefixes and lists spread replies like broadcasts because they may
      // select several receivers, but a nonmatching node still cannot act.
      assert(decoded.broadcast == (targets.size() != 64));
      memset(recipient, 0x77, sizeof(recipient));
      assert(!mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
          node.sent, node.sent_length, node.clock.now, recipient, decoded));
      assert(node.sent_policy == 2);
      ++node.clock.now;
    }
    assert(node.queued == 4);
  } else if (scenario == "syntax") {
    for (const char* command : {"fleet", "fleet send", "fleet sender 1 all set radio2 off",
        "fleet send -1 all set radio2 off", "fleet send 4 all set radio2 off",
        "fleet send 4294967296 all set radio2 off", "fleet send 1x all set radio2 off",
        "fleet send 1 all", "fleet send 1 a set radio2 off",
        "fleet send 1 11111111111111 set radio2 off", "fleet send 1 all get password",
        "fleet send 1 all set radio2 off\nreboot"}) rejected(node, command);
    rejected(node, ("fleet send 1 " + std::string(65, '1') + " set radio2 off").c_str());
    rejected(node, ("fleet send 1 all set flood.filter " + std::string(mesh::FleetCommand::MaxCommandLength, '1')).c_str());
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
    const std::string prefix = "set flood.filter ";
    const std::string too_long = prefix + std::string(mesh::FleetCommand::MaxCommandLength - prefix.size(), '1');
    assert(node.handleCommand(("fleet send 1 " + targets + " " + too_long).c_str(), 0, reply));
    assert(strstr(reply, "exceed two packets") && node.queued == 0);
    assert(node.handleCommand(("fleet send 1 " + targets + " set radio2 off").c_str(), 0, reply));
    assert(node.queued == 2); // The valid list now fits one atomic two-packet envelope.
    ++node.clock.now;
    targets += ",44556677"; // More than the entire target-record capacity.
    rejected(node, ("fleet send 1 " + targets + " set radio2 off").c_str());
    assert(node.queued == 2);
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
    assert(!strncmp(reply, "OK - fleet command queued", 25) && node.queued == 2);
    uint8_t recipient[32]; memset(recipient, 0x42, sizeof(recipient));
    mesh::FleetCommand::Decoded decoded;
    assert(mesh::FleetCommand::decode(node.self_id, node.channels[1].channel.secret,
        node.sent, node.sent_length, node.clock.now, recipient, decoded));
    assert(!strcmp(decoded.command, scheduled.c_str()));
    ++node.clock.now;
    assert(node.handleCommand(("fleet send 1 42424242 " + scheduled).c_str(), 0, reply));
    assert(node.queued == 3 && node.sent_policy == 2);
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
        helpers = "\n".join(body(source, signature) for signature in (
            "void MyMesh::onRadioProfileCopyQueued(", "bool MyMesh::sendFleetCommandData(",
        ))
        send_packet = body((ROOT / "src/Dispatcher.cpp").read_text(), "bool Dispatcher::sendPacket(")
        send_packet = send_packet.replace("bool Dispatcher::sendPacket(Packet*", "bool MyMesh::sendPacket(mesh::Packet*", 1)
        rescue = body(source, "void MyMesh::checkCLIRescueCmd()")
        # Keep the entire production reader/line-admission logic. Replace only
        # downstream rescue commands (filesystem/UI peripherals) with a record
        # and the real Companion fleet dispatcher used throughout this suite.
        rescue = rescue[:rescue.index("    reply_buf[0] = 0;")]
        # Print::println(text) is absent from the shared host Stream adapter.
        # Preserve STM32's actual shared error text and adapt only printing.
        rescue = rescue.replace('output.println("  Error: unknown command");',
                                'output.print("  Error: unknown command"); output.println();')
        rescue += '''
    rescue_commands.emplace_back(cli_command);
    char reply[160] = {};
    handleCommand(cli_command, 0, reply);
    cli_command[0] = 0;
  }
}
'''
        reset = body(source, "void MyMesh::resetUsbHostSessionInput()")
        enter = body(source, "void MyMesh::enterCLIRescue()")
        # The shared Stream mock lacks Print::println(text); preserve the
        # actual entry-state reset and adapt only this banner's formatting.
        banner = 'mesh::usbTerminalPort().println("========= CLI Rescue =========");'
        assert enter.count(banner) == 1
        enter = enter.replace(banner, 'mesh::usbTerminalPort().print("========= CLI Rescue =========\\n");')
        cls.fixture = fixture
        fixture.write_text(HARNESS.replace("@METHOD@", cls.production)
                           .replace("@SEND_HELPERS@", send_packet + "\n" + helpers)
                           .replace("@RESCUE_READER@", rescue + "\n" + reset + "\n" + enter))
        sanitizer_flags = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
                           if os.environ.get("MESHCORE_FLEET_SANITIZERS") == "1" else [])
        cls.sanitizer_flags = sanitizer_flags
        result = subprocess.run([
            compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-O2", *sanitizer_flags,
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

    def test_omitted_target_and_explicit_all_emit_compact_signed_broadcasts(self):
        self.scenario("implicit_broadcast")

    def test_implicit_broadcast_requires_local_client_and_preserves_prefix_and_tabs(self):
        self.scenario("implicit_local_prefix_tabs")

    def test_implicit_and_explicit_all_share_queue_retry_and_same_second_replay_guard(self):
        self.scenario("implicit_retry_sequence")

    def test_unknown_or_malformed_explicit_targets_never_fall_back_to_broadcast(self):
        self.scenario("implicit_target_ambiguity")

    def test_omitted_target_requires_canonical_allowlisted_command_and_230_byte_cap(self):
        self.scenario("implicit_command_controls")

    def test_target_specific_exact_packet_limits_reject_atomically_and_allow_retry(self):
        self.scenario("target_command_capacity")

    def test_maximum_correlated_implicit_command_fits_app_frame_and_remains_local_only(self):
        self.scenario("maximum_correlated_app_command")

    def test_one_packet_boundary_automatically_becomes_two_signed_fragments(self):
        self.scenario("fragment_boundaries")

    def test_two_part_admission_reserves_pool_and_rolls_back_exact_packets_and_radio_copy(self):
        self.scenario("fragment_atomic_admission")

    def test_rescue_overflow_discards_full_fleet_rule_and_preserves_next_crlf_command(self):
        self.scenario("rescue_overflow")

    def test_rescue_overflow_drains_bounded_chunks_and_never_executes_valid_suffix(self):
        self.scenario("rescue_split_overflow")

    def test_rescue_exact_capacity_waits_for_real_terminator_and_keeps_lines_separate(self):
        self.scenario("rescue_boundaries")

    def test_rescue_unterminated_buffer_is_bounded_and_discards_remainder(self):
        self.scenario("rescue_unterminated")

    def test_rescue_entry_and_usb_host_recovery_reset_discard_state(self):
        self.scenario("rescue_session_reset")

    def test_stm32_rescue_shared_error_still_drains_and_rejects_entire_overflow(self):
        binary = Path(self.work.name) / "companion-stm32-rescue"
        built = subprocess.run([
            self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-DSTM32_PLATFORM=1", "-O2", *self.sanitizer_flags,
            "-isystem", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
            str(self.fixture), str(ROOT / "src/helpers/FleetCommand.cpp"),
            "-lcrypto", "-o", str(binary),
        ], capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        result = subprocess.run([str(binary), "stm32_rescue"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_explicit_8_12_64_hex_and_list_targets_remain_restricted(self):
        self.scenario("explicit_target_forms")

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

    def test_valid_list_exceeding_two_packet_capacity_never_partially_sends(self):
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
