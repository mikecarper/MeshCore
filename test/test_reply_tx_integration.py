"""Exercise delayed OTA response classification and infrastructure reply wiring."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class ReplyTxIntegrationTest(unittest.TestCase):
    def test_deferred_responses_keep_the_request_profile(self):
        mesh_source = (ROOT / 'src/Mesh.cpp').read_text()
        dispatcher = (ROOT / 'src/Dispatcher.cpp').read_text()
        harness = r'''
#include <cassert>
#include <vector>
#include <RadioProfiles.h>
#include <Packet.h>
#include <helpers/ota/OtaManager.h>
#include <helpers/ota/OtaProtocol.h>
#include "mota_vectors.h"
namespace mesh {
namespace ota {
struct OtaContext { OtaManager& manager; };
static OtaContext* active;
OtaContext* ota_context_if_active() { return active; }
OtaContext& ota_ctx() { return *active; }
}
struct Manager {
  int free=40;
  int getFreeCount() { return free; }
};
static int queuedPacedOtaResponses(Manager*) { return 0; }
static constexpr int OTA_EGRESS_QUEUE_CREDIT=4, OTA_EGRESS_MIN_FREE=4;
@PACED@
struct Radio {
  RadioProfiles config;
  const RadioProfiles* profiles() const { return &config; }
};
struct Mesh {
  Manager manager; Manager* _mgr=&manager;
  Radio radio; Radio* _radio=&radio;
  ota::OtaManager ota_manager;
  ota::OtaContext context{ota_manager};
  Packet packet;
  std::vector<uint8_t> masks;
  uint8_t receive_profile=0;
  bool receiving=false;
  Mesh() { ota::active=&context; }
  bool isAnyTempRadioActive() { return true; }
  Packet* createOtaPacket(const uint8_t*,uint16_t) {
    packet=Packet(); packet.header=PAYLOAD_TYPE_OTA << PH_TYPE_SHIFT;
    packet.radio_profile=receive_profile; packet.radio_local=!receiving;
    packet.radio_generation=receiving ? radio.config.generation[receive_profile] : 0;
    return &packet;
  }
  void releasePacket(Packet*) {}
  bool sendOtaFlood(Packet* p) { masks.push_back(getTransmitProfileMask(p)); return true; }
  uint8_t getTransmitProfileMask(const Packet*) const;
  static bool otaSendAdapter(void*,const uint8_t*,uint16_t,bool);
  void service() { @SERVICE@ }
};
@MASK@
@ADAPTER@
}
int main() {
  mesh::Mesh node;
  node.radio.config.primary_temporary=true;
  node.radio.config.secondary_temporary=true;
  node.radio.config.secondary.mode=mesh::RadioProfileMode::RxTx;
  node.radio.config.cross=mesh::RadioCrossMode::Off;
  node.ota_manager.begin(0,node.otaSendAdapter,&node);
  assert(node.ota_manager.serve(MOTA_VEC,sizeof(MOTA_VEC)));
  mesh::ota::ReqMsg request{};
  memcpy(request.manifest_id,EXP_MERKLE_ROOT,4);
  request.block_idx=0; request.want_mask=1;
  uint8_t wire[MAX_PACKET_PAYLOAD];
  const auto length=mesh::ota::encode_req(wire,sizeof(wire),request);
  node.receiving=true; node.receive_profile=1;
  mesh::ota::OtaReplyRoute route;
  route.profile=1; route.generation=node.radio.config.generation[1];
  assert(node.ota_manager.on_message(wire,length,route));
  assert(node.masks.empty()); // DATA is deliberately deferred until later
  node.receiving=false; node.receive_profile=0;
  node.manager.free=4; node.service(); // backpressure must retain origin
  assert(node.ota_manager.pendingServeJobs()==1 && node.masks.empty());
  node.manager.free=40;
  node.service();
  assert(node.masks.size()==1 && node.masks[0]==2);
  assert(node.packet.radio_profile==1 && !node.packet.radio_local);
  assert(node.packet.radio_generation==route.generation);
  assert(node.ota_manager.replyRoute().profile==0xFF);

  // Infrastructure reply-both still overrides AUTO origin selection, including
  // a receive-only secondary when the configured reply policy forces it.
  node.ota_manager.clearPendingEgress(); node.masks.clear();
  node.radio.config.reply_tx=mesh::RADIO_TX_BOTH;
  node.radio.config.reply_force=true;
  node.radio.config.secondary.mode=mesh::RadioProfileMode::Rx;
  assert(node.ota_manager.on_message(wire,length,route));
  node.service();
  assert(node.masks.size()==1 && node.masks[0]==3);
  node.radio.config.reply_tx=mesh::RADIO_TX_AUTO;
  node.radio.config.reply_force=false;
  node.radio.config.secondary.mode=mesh::RadioProfileMode::RxTx;

  // Identical block requests heard on separate radio domains must not merge.
  node.ota_manager.clearPendingEgress(); node.masks.clear();
  assert(node.ota_manager.on_message(wire,length,route));
  auto primary=route; primary.profile=0;
  assert(node.ota_manager.on_message(wire,length,primary));
  assert(node.ota_manager.pendingServeJobs()==2);
  node.service();
  mesh::ota::ReqProofMsg proof{};
  memcpy(proof.manifest_id,EXP_MERKLE_ROOT,4); proof.block_idx=0;
  uint8_t proof_wire[MAX_PACKET_PAYLOAD];
  const auto proof_len=mesh::ota::encode_req_proof(proof_wire,sizeof(proof_wire),proof);
  assert(node.ota_manager.on_message(proof_wire,proof_len,primary));
  assert(node.ota_manager.pendingServeJobs()==2); // primary proof merges only with primary DATA
  assert(node.ota_manager.on_message(proof_wire,proof_len,route));
  node.service(); // secondary proof
  node.service(); // primary DATA
  node.service(); // primary proof
  assert(node.ota_manager.pendingServeJobs()==0);
  assert((node.masks==std::vector<uint8_t>{2,2,1,1}));

  // Reconfiguration retires the stale job even when it was waiting on proof.
  node.masks.clear();
  assert(node.ota_manager.on_message(wire,length,route));
  node.service();
  const auto sent=node.ota_manager.packetsSent();
  ++node.radio.config.generation[1];
  auto replacement=route; ++replacement.generation;
  assert(node.ota_manager.on_message(wire,length,replacement));
  assert(node.ota_manager.pendingServeJobs()==2);
  node.service();
  assert(node.ota_manager.pendingServeJobs()==1);
  assert(node.ota_manager.packetsSent()==sent); // discarded is not reported as sent
  node.service();
  assert(node.masks.size()==2 && node.packet.radio_generation==replacement.generation);
  node.radio.config.secondary.mode=mesh::RadioProfileMode::Off;
  node.service();
  assert(node.ota_manager.pendingServeJobs()==0);
  node.radio.config.secondary.mode=mesh::RadioProfileMode::RxTx;

  // The paced manifest queue also separates origins and expires stale sessions.
  node.masks.clear();
  mesh::ota::GetManifestMsg manifest{};
  memcpy(manifest.manifest_id,EXP_MERKLE_ROOT,4); manifest.want_mask=1;
  uint8_t manifest_wire[MAX_PACKET_PAYLOAD];
  const auto manifest_len=mesh::ota::encode_get_manifest(manifest_wire,sizeof(manifest_wire),manifest);
  assert(node.ota_manager.on_message(manifest_wire,manifest_len,route));
  assert(node.ota_manager.on_message(manifest_wire,manifest_len,replacement));
  assert(node.ota_manager.on_message(manifest_wire,manifest_len,primary));
  assert(node.ota_manager.pendingManifestJobs()==3);
  node.service(); // stale route is discarded even before its pacing deadline
  assert(node.ota_manager.pendingManifestJobs()==2 && node.masks.empty());
  node.ota_manager.set_clock(1000000);
  node.service(); node.service();
  assert((node.masks==std::vector<uint8_t>{2,1}));
  assert(node.ota_manager.pendingManifestJobs()==0);

  // Changing primary also retires its old request before any fragment is sent.
  node.masks.clear();
  assert(node.ota_manager.on_message(wire,length,primary));
  ++node.radio.config.generation[0];
  ++primary.generation;
  assert(node.ota_manager.on_message(wire,length,primary));
  node.service();
  assert(node.masks.empty() && node.ota_manager.pendingServeJobs()==1);
  node.service();
  assert(node.masks.size()==1 && node.masks[0]==1);
  assert(node.packet.radio_generation==primary.generation);
  node.ota_manager.clearPendingEgress();

  // LEAVES remains an immediate response and inherits the live receive scope.
  // Deferred affinity cannot bleed into that callback or a later local send.
  node.masks.clear();
  mesh::ota::GetLeavesMsg leaves{};
  memcpy(leaves.manifest_id,EXP_MERKLE_ROOT,4); leaves.want_mask=1;
  uint8_t leaves_wire[MAX_PACKET_PAYLOAD];
  const auto leaves_len=mesh::ota::encode_get_leaves(leaves_wire,sizeof(leaves_wire),leaves);
  node.receiving=true; node.receive_profile=1;
  node.ota_manager.on_message(leaves_wire,leaves_len,replacement);
  assert(node.masks.size()==1 && node.masks[0]==2);
  assert(node.packet.radio_reply && !node.packet.radio_local);
  node.receiving=false; node.receive_profile=0;
  const uint8_t data=mesh::ota::OTA_DATA;
  assert(node.otaSendAdapter(&node,&data,1,false));
  assert(node.masks.size()==2 && node.masks[1]==1 && node.packet.radio_local);

  // A regular primary and temporary secondary stay isolated unless cross is on.
  node.radio.config.primary_temporary=false;
  node.masks.clear();
  assert(node.ota_manager.on_message(wire,length,replacement));
  node.service();
  assert(node.masks.size()==1 && node.masks[0]==2);
  node.ota_manager.clearPendingEgress();
  node.radio.config.cross=mesh::RadioCrossMode::On;
  assert(node.ota_manager.on_message(wire,length,replacement));
  node.service();
  assert(node.masks.size()==2 && node.masks[1]==3);
}
'''
        harness = harness.replace('@PACED@', method(mesh_source, 'static bool isPacedOtaResponse('))
        harness = harness.replace('@ADAPTER@', method(mesh_source, 'bool Mesh::otaSendAdapter('))
        harness = harness.replace('@MASK@', method(dispatcher, 'uint8_t Dispatcher::getTransmitProfileMask(')
                                  .replace('Dispatcher::', 'Mesh::'))
        start = mesh_source.index('ota::ota_ctx().manager.serviceEgress(')
        service = mesh_source[start:mesh_source.index('}, this);', start) + len('}, this);')]
        harness = harness.replace('@SERVICE@', service)
        self.assertIn('context->manager.on_message(pkt->payload, pkt->payload_len, route)', mesh_source)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / 'routing.cpp'; source.write_text(harness)
            binary = path / 'routing'
            sources = ['OtaManager.cpp', 'OtaProtocol.cpp', 'MotaContainer.cpp', 'MerkleTree.cpp']
            result = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                '-I', str(ROOT / 'src'), '-I', str(ROOT / 'test/mocks'),
                '-I', str(ROOT / 'test/test_ota'), str(source),
                *[str(ROOT / 'src/helpers/ota' / name) for name in sources],
                str(ROOT / 'src/Utils.cpp'), str(ROOT / 'src/Packet.cpp'), '-o', str(binary)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_async_ota_adapter_marks_only_response_types_and_retains_backpressure(self):
        source = (ROOT / 'src/Mesh.cpp').read_text()
        adapter = method(source, 'bool Mesh::otaSendAdapter(')
        paced = method(source, 'static bool isPacedOtaResponse(')
        harness = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <helpers/ota/OtaFormat.h>
namespace mesh {
struct Packet {
  bool radio_reply=false, radio_local=true;
  uint8_t radio_profile=0, radio_origin=0;
  uint32_t radio_generation=0, radio_origin_generation=0;
};
namespace ota {
struct Route { uint32_t generation=0; uint8_t profile=0xFF; };
struct Context { struct Manager { Route replyRoute() const { return {}; } } manager; };
Context* ota_context_if_active() { return nullptr; }
}
struct Manager { int free=10; int getFreeCount() { return free; } };
static int queued;
static int queuedPacedOtaResponses(Manager*) { return queued; }
static constexpr int OTA_EGRESS_QUEUE_CREDIT=@CREDIT@, OTA_EGRESS_MIN_FREE=@RESERVE@;
@PACED@
struct Mesh {
  Manager manager; Manager* _mgr=&manager;
  Packet packet;
  bool temporary=true, allocation=true, admit=true, was_reply=false;
  uint8_t mask=1;
  int allocations=0, sends=0;
  bool isAnyTempRadioActive() { return temporary; }
  Packet* createOtaPacket(const uint8_t*,uint16_t) {
    ++allocations;
    // Deliberately opposite to expected for half the types: classification
    // must not accidentally depend on the receive call stack or a prior send.
    packet.radio_reply=!packet.radio_reply;
    if (!allocation || !manager.free) return nullptr;
    --manager.free;
    return &packet;
  }
  uint8_t getTransmitProfileMask(const Packet*) { return mask; }
  void releasePacket(Packet*) { ++manager.free; }
  bool sendOtaFlood(Packet* p) { ++sends; was_reply=p->radio_reply; releasePacket(p); return admit; }
  static bool otaSendAdapter(void*,const uint8_t*,uint16_t,bool);
};
@ADAPTER@
}
int main() {
  mesh::Mesh node;
  for (unsigned type=0;type<256;++type) {
    const uint8_t msg=type;
    bool reply=type==0x03 || type==0x05 || type==0x07 || type==0x09 || type==0x0b;
    assert(node.otaSendAdapter(&node,&msg,1,true));
    assert(node.was_reply==reply);
  }
  const uint8_t data=mesh::ota::OTA_DATA, proof=mesh::ota::OTA_PROOF;
  for (const uint8_t type : {data,proof}) {
    for (uint8_t mask : {1,2,3}) {
      for (int queued=0;queued<=mesh::OTA_EGRESS_QUEUE_CREDIT+1;++queued) {
        for (int free=0;free<=mesh::OTA_EGRESS_MIN_FREE+3;++free) {
          node.mask=mask; mesh::queued=queued; node.manager.free=free;
          const int copies=mask==3 ? 2 : 1;
          const bool allowed=queued+copies<=mesh::OTA_EGRESS_QUEUE_CREDIT
              && free>=mesh::OTA_EGRESS_MIN_FREE+copies;
          const int allocations=node.allocations;
          assert(node.otaSendAdapter(&node,&type,1,true)==allowed);
          assert(node.manager.free==free); // failure releases, simulated successful TX drains
          if (queued>=mesh::OTA_EGRESS_QUEUE_CREDIT || free<=mesh::OTA_EGRESS_MIN_FREE)
            assert(node.allocations==allocations); // normal backpressure is not pool exhaustion
        }
      }
    }
  }
  node.mask=0; node.manager.free=10; mesh::queued=0;
  assert(!node.otaSendAdapter(&node,&data,1,true));
  assert(node.manager.free==10);
  node.mask=3;
  int allocated=node.allocations;
  node.manager.free=10; node.temporary=false;
  assert(!node.otaSendAdapter(&node,&data,1,true));
  assert(node.allocations==allocated);
  node.temporary=true; node.allocation=false;
  int sends=node.sends;
  assert(!node.otaSendAdapter(&node,&data,1,true)); assert(node.sends==sends);
  node.allocation=true; node.admit=false;
  assert(!node.otaSendAdapter(&node,&data,1,true));
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp = Path(folder) / 'reply.cpp'
            exe = Path(folder) / 'reply'
            header = (ROOT / 'src/Mesh.h').read_text()
            credit = re.search(r'#define OTA_EGRESS_QUEUE_CREDIT (\d+)', header).group(1)
            reserve = re.search(r'#define OTA_EGRESS_MIN_FREE (\d+)', header).group(1)
            cpp.write_text(harness.replace('@ADAPTER@', adapter).replace('@PACED@', paced)
                           .replace('@CREDIT@', credit).replace('@RESERVE@', reserve))
            result = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                '-Wall', '-Wextra', '-I', str(ROOT / 'src'), str(cpp), '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(exe)], check=True)

    def test_all_infrastructure_roles_enable_replies_and_mark_delayed_completions(self):
        common = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        self.assertIn('_radio_profiles.begin(fs, _callbacks->getProfileRadio(), _rtc, true);', common)
        for path in ['examples/simple_repeater/MyMesh.cpp',
                     'examples/simple_room_server/MyMesh.cpp',
                     'examples/simple_sensor/SensorMesh.cpp']:
            with self.subTest(path=path):
                text = (ROOT / path).read_text()
                self.assertIn('_cli.loadPrefs(', text)
                self.assertIn('packet->radio_reply = true;', text)
        room = (ROOT / 'examples/simple_room_server/MyMesh.cpp').read_text()
        self.assertIn('reply->radio_reply = true;  // asynchronous response to the room subscription', room)


if __name__ == '__main__':
    unittest.main()
