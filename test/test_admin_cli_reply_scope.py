#!/usr/bin/env python3
"""Execute role reply helpers to check full-admin CLI and GPIO policy scope."""

from pathlib import Path
import os
import re
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
#include <cstring>
#include <vector>
#include <Packet.h>
#include <RadioProfiles.h>
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/UserGpio.h>
#include <helpers/UserGpioReplyTracker.h>
#include <helpers/TempRadioReplyBarrier.h>
#include <helpers/RoomClientPathCommand.h>
#include <helpers/TxtDataHelpers.h>
#include <helpers/ClientACLResponse.h>
@CONSTANTS@
namespace mesh {
struct Identity { uint8_t pub_key[PUB_KEY_SIZE]{}; };
struct Utils {
  static void sha256(uint8_t* out, size_t size, const uint8_t*, size_t,
                     const uint8_t* = nullptr, size_t = 0) { memset(out, 0x11, size); }
};
@PACKET_METHODS@
}
using namespace mesh;
@CLIENT_INFO@;
@GPIO_STATE@
struct TransportKey {};
struct Clock {
  uint32_t getCurrentTimeUnique() { return 1000; }
  uint32_t getCurrentTime() { return 1000; }
};
struct RNG { void random(uint8_t* out, size_t size) { memset(out, 0x42, size); } };
struct Radio {
  mesh::RadioProfiles config;
  const mesh::RadioProfiles* profiles() const { return &config; }
};
struct ACL {
  ClientInfo clients[3]{};
  int count=2;
  int getNumClients() const { return count; }
  ClientInfo* getClientByIdx(int i) { assert(i>=0 && i<count); return &clients[i]; }
};
struct MyMesh {
  ACL acl;
  mesh::Identity self_id;
  Radio radio; Radio* _radio=&radio;
  Clock clock;
  RNG rng;
  UserGpioReplyTracker _gpio_reply_tracker;
  mesh::TempRadioReplyBarrier temp_radio_reply_barrier;
  struct Morse {
    uint32_t generation() const { return 0; }
    void beginCommand(const uint8_t*) {}
    void endCommand() {}
    bool pending() const { return false; }
    void prepareReply(const void*) {}
    void armReply(const void*, bool) {}
  } morse;
  struct CLI { Morse* morse; Morse& morseReply() { return *morse; } } _cli{&morse};
  uint8_t reply_data[MAX_PACKET_PAYLOAD]{};
  mesh::Packet packet;
  unsigned allocations=0;
  bool allocation_ok=true, queue_ok=true, receiving=true;
  uint8_t receive_profile=1;
  bool redundant=false;
  uint8_t default_scope=0;
  unsigned _num_post_pushes=0;
  struct Prefs { uint8_t path_hash_mode=0; } _prefs;
  struct Sent { bool reply; uint8_t mask; std::vector<uint8_t> data; };
  std::vector<Sent> sent;
  MyMesh() {
    radio.config.secondary.mode=mesh::RadioProfileMode::RxTx;
    radio.config.cross=mesh::RadioCrossMode::Off;
    radio.config.reply_tx=mesh::RADIO_TX_BOTH;
    for (int i=0;i<acl.count;++i) {
      memset(acl.clients[i].id.pub_key, i+1, PUB_KEY_SIZE);
      acl.clients[i].permissions=PERM_ACL_ADMIN;
      acl.clients[i].out_path_len=0;
    }
  }
  Clock* getRTCClock() { return &clock; }
  RNG* getRNG() { return &rng; }
  uint32_t futureMillis(uint32_t delay) { return 2000+delay; }
  mesh::Packet* createDatagram(uint8_t type, const mesh::Identity&,
      const uint8_t*, const uint8_t* data, size_t length) {
    ++allocations;
    if (!allocation_ok) return nullptr;
    packet=mesh::Packet();
    packet.header=type<<PH_TYPE_SHIFT;
    packet.radio_local=!receiving;
    packet.radio_profile=receiving ? receive_profile : 0;
    packet.payload_len=length;
    assert(length<=sizeof(packet.payload));
    memcpy(packet.payload,data,length);
    return &packet;
  }
  uint8_t getTransmitProfileMask(const mesh::Packet*) const;
  bool queue(mesh::Packet* p) {
    if (!p || !queue_ok) return false;
    sent.push_back({p->radio_reply,getTransmitProfileMask(p),
                   std::vector<uint8_t>(p->payload,p->payload+p->payload_len)});
    return true;
  }
  bool sendClientReplyWithFallbackScope(ClientInfo*, mesh::Packet* p,
      unsigned long, uint8_t, const TransportKey*, bool copies) {
    redundant=copies; return queue(p);
  }
  bool sendClientReply(ClientInfo*, mesh::Packet* p, unsigned long, uint8_t) { return queue(p); }
  bool sendFlood(mesh::Packet* p, unsigned long, uint8_t) { return queue(p); }
  bool sendFloodScoped(uint8_t,mesh::Packet* p,unsigned long,uint8_t) { return queue(p); }
  bool sendDirect(mesh::Packet* p,const uint8_t*,uint8_t,unsigned long) { return queue(p); }
  void replaceActiveMessageRetries(mesh::Packet*,const uint8_t*,uint32_t) {}
  bool sendRemoteCliReply(ClientInfo*,const uint8_t*,uint8_t,uint32_t,
      const char*,const TransportKey*,mesh::Packet** = nullptr);
  void onUserGpioTimerCompleted(uint8_t,uint8_t,uint32_t);
  bool pushRoomTextToClient(ClientInfo*,uint32_t,const mesh::Identity&,
                            const char*,uint32_t = 0);
#if SENSOR_ROLE
  int matching_peer_indexes[MAX_SEARCH_RESULTS]{};
  void onPeerDataRecv(mesh::Packet*,uint8_t,int,const uint8_t*,uint8_t*,size_t);
  uint8_t handleRequest(ClientInfo*,uint32_t,uint8_t,const uint8_t*,size_t,size_t) {
    reply_data[0]=0x5a; return 1;
  }
  bool handleIncomingMsg(ClientInfo&,uint32_t,uint8_t*,uint8_t,size_t) { return true; }
  void handleCommand(uint32_t,char*,char* reply,int,uint8_t) { strcpy(reply,"OK"); }
  bool sendFloodReply(mesh::Packet* p,unsigned long,uint8_t) { return queue(p); }
  void sendAckTo(ClientInfo& client,uint32_t,uint8_t) {
    const uint8_t data=0;
    queue(createDatagram(PAYLOAD_TYPE_ACK,client.id,client.shared_secret,&data,1));
  }
  mesh::Packet* createPathReturn(const mesh::Identity& id,const uint8_t* key,
      const uint8_t*,uint8_t,uint8_t type,const uint8_t* data,size_t length) {
    return createDatagram(type,id,key,data,length);
  }
#endif
};
@METHODS@
int main() {
#if SENSOR_ROLE
  for (uint8_t role=0;role<=PERM_ACL_ROLE_MASK;++role) {
    for (uint8_t flags : {TXT_TYPE_PLAIN,TXT_TYPE_CLI_DATA,TXT_TYPE_CLI_COMMAND}) {
      for (bool request : {false,true}) {
        MyMesh node;
        auto* client=&node.acl.clients[0]; client->permissions=role;
        uint8_t data[16]{};
        const uint32_t timestamp=900;
        memcpy(data,&timestamp,4); data[4]=flags<<2;
        strcpy(reinterpret_cast<char*>(data+5),"get tx");
        mesh::Packet incoming;
        incoming.header=ROUTE_TYPE_DIRECT;
        node.onPeerDataRecv(&incoming,request ? PAYLOAD_TYPE_REQ : PAYLOAD_TYPE_TXT_MSG,
            0,client->shared_secret,data,11);
        const bool admin=role==PERM_ACL_ADMIN;
        const bool admin_cli=admin && !request && flags!=TXT_TYPE_PLAIN;
        assert(node.sent.size()==(request || admin ? 1U : 0U));
        if (!node.sent.empty()) {
          assert(node.sent[0].reply==admin_cli);
          assert(node.sent[0].mask==(admin_cli ? 3 : 2));
        }
      }
    }
  }
#endif
#if REPEATER_ROLE
  for (unsigned permissions=0;permissions<256;++permissions) {
    MyMesh node;
    auto* client=&node.acl.clients[0]; client->permissions=permissions;
    assert(node.sendRemoteCliReply(client,client->shared_secret,1,1000,"OK",nullptr));
    assert(node.sent.size()==1);
    const bool admin=(permissions & PERM_ACL_ROLE_MASK)==PERM_ACL_ADMIN;
    assert(node.sent[0].reply==admin && node.sent[0].mask==(admin ? 3 : 2));
    // The same helper emits cached, deferred, and immediate CLI results.
    node.radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
    node.radio.config.reply_force=true;
    assert(node.sendRemoteCliReply(client,client->shared_secret,1,1000,"cached OK",nullptr));
    assert(node.sent.back().mask==(admin ? 3 : 0));
  }
  MyMesh failed;
  auto* client=&failed.acl.clients[0];
  mesh::Packet* queued=&failed.packet;
  failed.allocation_ok=false;
  assert(!failed.sendRemoteCliReply(client,client->shared_secret,1,0,"OK",nullptr,&queued));
  assert(queued==nullptr && failed.sent.empty());
#endif
  for (uint8_t role=0;role<=PERM_ACL_ROLE_MASK;++role) {
    for (bool move : {false,true}) {
      MyMesh node;
      const uint8_t original_key=node.acl.clients[0].id.pub_key[0];
      node._gpio_reply_tracker.beginCommand(0,2,node.acl.clients[0].id.pub_key);
      node._gpio_reply_tracker.timerScheduled(7,123);
      if (move) {
        node.acl.clients[1]=node.acl.clients[0];
        memset(node.acl.clients[0].id.pub_key,0xAA,PUB_KEY_SIZE);
      }
      node.acl.clients[move ? 1 : 0].permissions=role;
      node.onUserGpioTimerCompleted(7,UserGpio::STATE_OFF,123);
      const bool admin=role==PERM_ACL_ADMIN;
      assert(node.sent.size()==(admin ? 1U : 0U));
      if (admin) {
        assert(node.sent[0].reply && node.sent[0].mask==3);
        assert(node.acl.clients[move ? 1 : 0].id.pub_key[0]==original_key);
      }
      node.onUserGpioTimerCompleted(7,UserGpio::STATE_OFF,123);
      assert(node.sent.size()==(admin ? 1U : 0U)); // route is consumed once
    }
  }
  MyMesh removed;
  removed._gpio_reply_tracker.beginCommand(0,1,removed.acl.clients[0].id.pub_key);
  removed._gpio_reply_tracker.timerScheduled(3,456);
  memset(removed.acl.clients[0].id.pub_key,0x55,PUB_KEY_SIZE);
  removed.onUserGpioTimerCompleted(3,UserGpio::STATE_ON,456);
  assert(removed.sent.empty()); // a replacement ACL slot cannot inherit the reply
#if ROOM_ROLE
  for (bool flood : {false,true}) {
    for (uint8_t role : {PERM_ACL_READ_WRITE,PERM_ACL_ADMIN}) {
      MyMesh node; node.receiving=false;
      node.radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
      node.radio.config.reply_force=true;
      auto* client=&node.acl.clients[0]; client->permissions=role;
      if (flood) client->out_path_len=OUT_PATH_UNKNOWN;
      assert(node.pushRoomTextToClient(client,100,client->id,"room post"));
      assert(node.sent.size()==1 && !node.sent[0].reply && node.sent[0].mask==1);
    }
  }
#endif
  puts("full-admin CLI/GPIO reply scope and revoked identity checks passed");
}
'''


class AdminCliReplyScopeTests(unittest.TestCase):
    def test_role_helpers_gate_reply_override_and_reauthorize_gpio(self):
        acl = (ROOT / "src/helpers/ClientACL.h").read_text()
        packet = (ROOT / "src/Packet.cpp").read_text()
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        constants = "\n".join(line for line in acl.splitlines()
                              if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
        for role, path, class_name in (
                ("repeater", "examples/simple_repeater", "MyMesh"),
                ("room", "examples/simple_room_server", "MyMesh"),
                ("sensor", "examples/simple_sensor", "SensorMesh")):
            with self.subTest(role=role):
                source = (ROOT / path / (class_name + ".cpp")).read_text()
                header = (ROOT / path / (class_name + ".h")).read_text()
                protocol = (ROOT / "src/Mesh.h").read_text()
                labels = constants
                names = ["SERVER_RESPONSE_DELAY"]
                if role == "sensor":
                    names.append("TXT_ACK_DELAY")
                if role != "room":
                    names.append("CLI_REPLY_DELAY_MILLIS")
                else:
                    names += ["PUSH_ACK_TIMEOUT_FLOOD",
                              "PUSH_TIMEOUT_BASE", "PUSH_ACK_TIMEOUT_FACTOR"]
                for name in names:
                    match = re.search(r"^\s*#define\s+" + name + r"\s+.*$",
                                      source + "\n" + header + "\n" + protocol, re.MULTILINE)
                    self.assertIsNotNone(match, name)
                    labels += "\n" + match.group()
                methods = extract_braced(source, "void " + class_name + "::onUserGpioTimerCompleted(")
                methods = methods.replace(class_name + "::", "MyMesh::")
                if role == "repeater":
                    methods += "\n" + extract_braced(source, "bool MyMesh::sendRemoteCliReply(")
                elif role == "room":
                    methods += "\n" + extract_braced(source, "bool MyMesh::pushRoomTextToClient(")
                else:
                    labels += "\n#define MAX_SEARCH_RESULTS 24"
                    methods += "\n" + extract_braced(source, "void SensorMesh::onPeerDataRecv(").replace(
                        "SensorMesh::", "MyMesh::")
                mask = extract_braced(dispatcher, "uint8_t Dispatcher::getTransmitProfileMask(")
                methods += "\n" + mask.replace("Dispatcher::", "MyMesh::")
                generated = HARNESS.replace("@CONSTANTS@", labels)
                generated = generated.replace("@CLIENT_INFO@", extract_braced(acl, "struct ClientInfo {"))
                generated = generated.replace("@PACKET_METHODS@", "\n".join(
                    extract_braced(packet, signature) for signature in
                    ("Packet::Packet()", "bool Packet::isValidPathLen(")))
                gpio = (ROOT / "src/helpers/UserGpio.cpp").read_text()
                generated = generated.replace("@GPIO_STATE@", extract_braced(gpio, "const char* UserGpio::stateName("))
                generated = generated.replace("@METHODS@", methods)
                with tempfile.TemporaryDirectory(prefix="admin-cli-reply-scope-") as directory:
                    work = Path(directory)
                    cpp = work / "test.cpp"; cpp.write_text(generated, encoding="ascii")
                    binary = work / "reply-scope"
                    sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                   "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
                                  if sys.platform.startswith("linux") else [])
                    command = [os.environ.get("CXX", "g++"), "-std=c++17", "-O1",
                               "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", *sanitizers,
                               "-DREPEATER_ROLE=" + str(int(role == "repeater")),
                               "-DROOM_ROLE=" + str(int(role == "room")),
                               "-DSENSOR_ROLE=" + str(int(role == "sensor")),
                               "-I" + str(ROOT / "src"), "-I" + str(ROOT / "test/mocks"),
                               str(cpp), "-o", str(binary)]
                    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
