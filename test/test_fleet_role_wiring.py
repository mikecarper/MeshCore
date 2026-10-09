#!/usr/bin/env python3
"""Execute production role hooks around the separately tested fleet receiver."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <algorithm>
#include <SHA256.h>
#define MESH_ENABLE_CLOCK_SYNC 0
#define MAX_TKS_ENTRIES 16
static std::vector<int> events;
namespace mesh {
struct Packet {
  bool scoped = false;
  uint16_t transport_codes[2] = {};
  uint8_t payload[32] = {}, payload_len = 32, type = 5;
  bool hasTransportCodes() const { return scoped; }
  uint8_t getPayloadType() const { return type; }
};
struct Mesh {
  virtual ~Mesh() = default;
  virtual bool supportsFleetControl() const { return false; }
  virtual void onSendComplete(Packet*) { events.push_back(1); }
  virtual void onSendFail(Packet*) { events.push_back(3); }
  virtual void onRadioProfileCopyQueued(Packet*, const Packet*, uint8_t) { events.push_back(5); }
};
}
struct TransportKey {
  uint8_t key[16] = {};
  uint16_t calcTransportCode(const mesh::Packet*) const;
};
struct Region { bool wildcard = false; bool isWildcard() const { return wildcard; } };
struct Regions {
  std::vector<TransportKey> keys;
  unsigned calls = 0;
  int getTransportKeysFor(const Region&, TransportKey* out, int capacity) {
    ++calls;
    const int count = std::min<int>(keys.size(), capacity);
    for (int i = 0; i < count; ++i) out[i] = keys[i];
    return count;
  }
};
@SCOPE_HELPERS@
struct Profiles {};
struct Fleet {
  unsigned received = 0, serviced = 0;
  bool scoped = false;
  TransportKey reply_scope;
  uint32_t sequence = 1735689610;
  char command[80] = "set radio2 off", reply[160] = {};
  void receive(mesh::Packet*, mesh::Mesh&, const TransportKey* scope) {
    ++received; scoped = scope != nullptr;
    if (scope) reply_scope = *scope;
  }
  void complete(mesh::Packet*, Profiles&) { events.push_back(2); }
  void fail(mesh::Packet*, Profiles&) { events.push_back(4); }
  void copy(mesh::Packet*, const mesh::Packet*) { events.push_back(6); }
  template<typename Handler>
  void service(mesh::Mesh&, Profiles&, const char* name, Handler handler) {
    assert(!strcmp(name, "fleet node")); ++serviced;
    handler(sequence, command, reply);
  }
};
struct CLI {
  Fleet fleet;
  Profiles profiles;
  bool enrolled = true;
  Fleet* fleetChannel() { return enrolled ? &fleet : nullptr; }
  Profiles& radioProfiles() { return profiles; }
};
struct ClockSync {
  unsigned observed = 0;
  void observeGroupPacket(mesh::Packet*) { ++observed; }
};
class Repeater : public mesh::Mesh {
 public:
  CLI _cli;
  bool region_load_active = false;
  Regions region_map;
  Region region;
  Region* recv_pkt_region = &region;
  struct { const char* node_name = "fleet node"; } _prefs;
  unsigned commands = 0;
  uint32_t timestamp = 0;
  @REPEATER_CAPABILITY@
  void onGroupPacketRecv(mesh::Packet*);
  void serviceFleet();
  void handleCommand(uint32_t value, void* sender, char* command, char* reply) {
    assert(sender == nullptr && !strcmp(command, "set radio2 off"));
    timestamp = value; ++commands; strcpy(reply, "OK");
  }
};
class Room : public mesh::Mesh {
 public:
  CLI _cli;
  ClockSync _clock_sync;
  bool region_load_active = false;
  Regions region_map;
  Region region;
  Region* recv_pkt_region = &region;
  struct { const char* node_name = "fleet node"; } _prefs;
  unsigned commands = 0;
  uint32_t timestamp = 0;
  @ROOM_CAPABILITY@
  void onGroupPacketRecv(mesh::Packet*);
  void serviceFleet();
#if MESH_ENABLE_FLEET_CONTROL
  void onSendComplete(mesh::Packet*) override;
  void onSendFail(mesh::Packet*) override;
  void onRadioProfileCopyQueued(mesh::Packet*, const mesh::Packet*, uint8_t) override;
#endif
  void handleCommand(uint32_t value, char* command, char* reply) {
    assert(!strcmp(command, "set radio2 off"));
    timestamp = value; ++commands; strcpy(reply, "OK");
  }
};
@METHODS@

template<typename Role>
void checkRole() {
  Role role;
  mesh::Packet packet;
  TransportKey original, rotated;
  memset(original.key, 0x11, sizeof(original.key));
  memset(rotated.key, 0x22, sizeof(rotated.key));
  role.region_map.keys = {original, rotated};
  packet.scoped = true;
  packet.transport_codes[0] = rotated.calcTransportCode(&packet);
  assert(role.supportsFleetControl());
  role.onGroupPacketRecv(&packet);
  role.serviceFleet();
#if MESH_ENABLE_FLEET_CONTROL
  assert(role._cli.fleet.received == 1 && role._cli.fleet.serviced == 1);
  assert(role._cli.fleet.scoped);
  assert(!memcmp(role._cli.fleet.reply_scope.key, rotated.key, sizeof(rotated.key)));
  assert(role.commands == 1 && role.timestamp == role._cli.fleet.sequence);
  role.region_load_active = true;
  role.serviceFleet();
  assert(role.commands == 1);
  assert(strstr(role._cli.fleet.reply, "region load active"));
#else
  assert(role._cli.fleet.received == 0 && role._cli.fleet.serviced == 0);
  assert(role.commands == 0);
#endif
  role._cli.enrolled = false;
  const unsigned before = role._cli.fleet.serviced;
  role.onGroupPacketRecv(&packet);
  role.serviceFleet();
  assert(role._cli.fleet.serviced == before);
}

static void checkScopeSelection() {
  Regions regions;
  Region region;
  mesh::Packet packet;
  TransportKey first, rotated, selected;
  memset(first.key, 0x31, sizeof(first.key));
  memset(rotated.key, 0xA4, sizeof(rotated.key));
  regions.keys = {first, rotated};
  assert(!mesh::captureFleetReplyScope(regions, &region, &packet, selected));
  assert(regions.calls == 0);
  packet.scoped = true;
  packet.payload[0] = 0x6D;
  packet.transport_codes[1] = rotated.calcTransportCode(&packet);
  assert(mesh::captureFleetReplyScope(regions, &region, &packet, selected));
  assert(!memcmp(selected.key, rotated.key, sizeof(rotated.key)));
  // Codes belong to the received payload. Changing it invalidates the match.
  packet.payload[0] ^= 1;
  assert(!mesh::captureFleetReplyScope(regions, &region, &packet, selected));
  packet.payload[0] ^= 1;
  packet.transport_codes[0] = first.calcTransportCode(&packet);
  packet.transport_codes[1] = 0;
  assert(mesh::captureFleetReplyScope(regions, &region, &packet, selected));
  assert(!memcmp(selected.key, first.key, sizeof(first.key)));
  region.wildcard = true;
  const unsigned calls = regions.calls;
  assert(!mesh::captureFleetReplyScope(regions, &region, &packet, selected));
  assert(!mesh::captureFleetReplyScope(regions, (const Region*)nullptr, &packet, selected));
  assert(!mesh::captureFleetReplyScope(regions, &region, nullptr, selected));
  assert(regions.calls == calls);
}

int main() {
  checkScopeSelection();
  checkRole<Repeater>(); checkRole<Room>();
  Room room; mesh::Packet first, copy;
  room.onGroupPacketRecv(&first);
  assert(room._clock_sync.observed == 1); // Existing clock delivery survives.
  room.onSendComplete(&first);
  room.onSendFail(&first);
  room.onRadioProfileCopyQueued(&copy, &first, 7);
#if MESH_ENABLE_FLEET_CONTROL
  assert((events == std::vector<int>{1,2,3,4,5,6}));
#else
  assert((events == std::vector<int>{1,3,5}));
#endif
  puts("Fleet role receive, deferred dispatch, busy guard and callbacks passed");
}
'''

AUTHORIZATION = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <new>
#include <initializer_list>
static unsigned configurations = 0, allocations = 0;
namespace mesh {
struct FleetChannel {
  explicit FleetChannel(void*, void* = nullptr) { ++allocations; }
  bool handleConfig(const char*, char* reply, size_t capacity) {
    assert(capacity == 160); ++configurations; strcpy(reply, "OK"); return true;
  }
};
}
struct Callbacks {
  bool supported = true;
  bool supportsFleetControl() const { return supported; }
};
class CommonCLI {
 public:
  Callbacks callbacks;
  Callbacks* _callbacks = &callbacks;
  void* _management_fs = nullptr;
  mesh::FleetChannel* _fleet_channel = nullptr;
  ~CommonCLI() { delete _fleet_channel; }
  bool handleFleetCommand(const char*, char*);
};
struct ClientInfo {
  enum Role { Guest, Admin, RegionManager, FilterManager } role;
  bool isAdmin() const { return role == Admin; }
  bool isRegionMgr() const { return role == RegionManager; }
  bool isFilterMgr() const { return role == FilterManager; }
};
@MANAGER_HELPERS@
@COMMON_FLEET@
static void dispatch(CommonCLI& cli, ClientInfo* sender, const char* command, char* reply) {
  @MANAGER_GUARD@
  if (!cli.handleFleetCommand(command, reply)) strcpy(reply, "unhandled");
}
int main() {
  CommonCLI cli;
  char reply[160] = {};
  ClientInfo admin{ClientInfo::Admin};
  for (const char* command : {"get fleet.channel", "get fleet.controller", "get fleet.stats",
                              "set fleet.channel off", "set fleet.controller 010203"}) {
    for (auto role : {ClientInfo::Guest, ClientInfo::RegionManager, ClientInfo::FilterManager}) {
      ClientInfo sender{role};
      const unsigned before = configurations;
      dispatch(cli, &sender, command, reply);
      assert(!strcmp(reply, "Err - not permitted"));
      assert(configurations == before);
      assert(!isRegionMgrAllowed(command) && !isFilterMgrAllowed(command));
    }
    dispatch(cli, &admin, command, reply);
    assert(!strcmp(reply, "OK"));
    dispatch(cli, nullptr, command, reply);
    assert(!strcmp(reply, "OK"));
  }
  assert(allocations == 1 && configurations == 10);
  assert(isFilterMgrAllowed("set flood.filter.1 group-text 4"));
  assert(isRegionMgrAllowed("set flood.channel.scope public #local"));
  assert(!isFilterMgrAllowed("set radio2 off"));
  assert(!isRegionMgrAllowed("get password"));
  CommonCLI unsupported;
  unsupported.callbacks.supported = false;
  assert(unsupported.handleFleetCommand("set fleet.channel off", reply));
  assert(strstr(reply, "infrastructure roles"));
  assert(allocations == 1 && configurations == 10);
  assert(!cli.handleFleetCommand("set fleeter.channel off", reply));
  puts("Fleet enrollment remains restricted to local/admin infrastructure dispatch");
}
'''


class FleetRoleWiringTests(unittest.TestCase):
    def test_enrollment_common_cli_stays_behind_live_manager_permissions(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        helpers = "\n".join(body(repeater, signature) for signature in (
            "static bool commandFamilyMatches(", "static bool isCommonManagerReadOnlyAllowed(",
            "static bool isRegionMgrAllowed(", "static bool isFilterMgrAllowed(",
        ))
        handler = body(repeater, "void MyMesh::handleCommand(uint32_t sender_timestamp,")
        guard_start = handler.rindex("if (sender && !sender->isAdmin())", 0,
                                    handler.index("mesh::cli::handleACLGet("))
        guard = body(handler[guard_start:], "if (sender && !sender->isAdmin())")
        common = body((ROOT / "src/helpers/CommonCLI_Management.cpp").read_text(),
                      "bool CommonCLI::handleFleetCommand(")
        harness = AUTHORIZATION.replace("@MANAGER_HELPERS@", helpers)
        harness = harness.replace("@COMMON_FLEET@", common).replace("@MANAGER_GUARD@", guard)
        with tempfile.TemporaryDirectory(prefix="meshcore-fleet-authorization-") as directory:
            work = Path(directory)
            source = work / "fixture.cpp"
            source.write_text(harness)
            for extended_filters in (0, 1):
                with self.subTest(extended_filters=extended_filters):
                    executable = work / str(extended_filters)
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-DMESH_ENABLE_FLOOD_RULE_ENGINE=" + str(extended_filters),
                        str(source), "-o", str(executable),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(executable)], capture_output=True,
                                             text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_roles_with_feature_enabled_and_disabled(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        methods = []
        capabilities = {}
        for role, folder in (("Repeater", "simple_repeater"), ("Room", "simple_room_server")):
            source = (ROOT / "examples" / folder / "MyMesh.cpp").read_text()
            header = (ROOT / "examples" / folder / "MyMesh.h").read_text()
            capabilities[role] = body(header, "bool supportsFleetControl() const override")
            methods.append(body(source, "void MyMesh::onGroupPacketRecv(").replace("MyMesh::", role + "::", 1))
            loop = body(source, "void MyMesh::loop()")
            fleet = body(loop, "if (_cli.fleetChannel())")
            methods.append("void " + role + "::serviceFleet() {\n#if MESH_ENABLE_FLEET_CONTROL\n"
                           + fleet + "\n#endif\n}\n")
            if role == "Room":
                for signature in ("void MyMesh::onSendComplete(", "void MyMesh::onSendFail(",
                                  "void MyMesh::onRadioProfileCopyQueued("):
                    methods.append("#if MESH_ENABLE_FLEET_CONTROL\n"
                                   + body(source, signature).replace("MyMesh::", role + "::", 1)
                                   + "\n#endif\n")
        harness = HARNESS.replace("@METHODS@", "\n".join(methods))
        scope = body((ROOT / "src/helpers/FleetChannel.h").read_text(),
                     "template <typename Regions, typename Region>")
        transport_code = body((ROOT / "src/helpers/TransportKeyStore.cpp").read_text(),
                              "uint16_t TransportKey::calcTransportCode(")
        # The host SHA-256 shim has a byte-pointer output API; on-device Crypto
        # accepts void*. Preserve the production algorithm with that cast only.
        transport_code = transport_code.replace("&code, 2", "(uint8_t*)&code, 2")
        harness = harness.replace("@SCOPE_HELPERS@", transport_code + "\nnamespace mesh {\n" + scope + "\n}\n")
        harness = harness.replace("@REPEATER_CAPABILITY@", capabilities["Repeater"])
        harness = harness.replace("@ROOM_CAPABILITY@", capabilities["Room"])
        with tempfile.TemporaryDirectory(prefix="meshcore-fleet-role-") as directory:
            work = Path(directory)
            fixture = work / "fixture.cpp"
            fixture.write_text(harness)
            for enabled in (0, 1):
                with self.subTest(fleet_control=enabled):
                    executable = work / str(enabled)
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-isystem", str(ROOT / "test/mocks"),
                        "-DMESH_ENABLE_FLEET_CONTROL=" + str(enabled),
                        str(fixture), "-o", str(executable),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(executable)], capture_output=True,
                                             text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
