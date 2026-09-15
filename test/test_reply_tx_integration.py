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
struct Packet { bool radio_reply=false; };
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
