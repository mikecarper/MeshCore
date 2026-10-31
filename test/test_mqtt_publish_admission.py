"""Run the production packet/raw/status admission paths with bounded heaps."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_mqtt_publish_ack import method

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = 'src/helpers/bridges/MQTTBridge.cpp'
HARNESS = r'''
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include "helpers/MQTTPublishAdmission.h"
#include "helpers/MQTTTransportHealth.h"
#define ESP_PLATFORM 1
#define BOARD_HAS_PSRAM 1
#define MQTT_DEBUG_PRINTLN(...) ((void)0)
static uint32_t clock_ms=0;
unsigned long millis(){return clock_ms;}
constexpr int MALLOC_CAP_DMA=1, RUNTIME_MQTT_SLOTS=1;
static size_t dma_free=15348,dma_largest=9716,heap_queries=0;
size_t heap_caps_get_free_size(int){++heap_queries;return dma_free;}
size_t heap_caps_get_largest_free_block(int){++heap_queries;return dma_largest;}
struct Esp { size_t size=8000000; size_t getPsramSize(){return size;}
  size_t getMaxAllocHeap(){++heap_queries;return dma_largest;} } ESP;
namespace mesh { struct Packet { uint8_t getPayloadType(){return 7;}
  uint8_t writeTo(uint8_t*){return 2;} }; }
static int builds=0;
namespace MQTTMessageBuilder {
constexpr size_t WIRE_SCRATCH_SIZE=255;
bool canSerializePacket(mesh::Packet*,size_t){return true;}
template<typename... Args> int buildPacketJSONFromRaw(Args...){++builds;return 1;}
template<typename... Args> int buildPacketJSON(Args...){++builds;return 1;}
template<typename... Args> int buildRawJSON(Args...){++builds;return 1;}
}
struct Radio {float packetScore(float,int){return 0;}};
struct Client {int calls=0,result=0;uint32_t ack=0;
  uint32_t getPublishAck(){return ack;}
  int publish(const char*,uint8_t,bool,const char*,int,bool){++calls;return result;}
};
struct MQTTBridge {
  struct MQTTSlot {Client* client=nullptr;bool enabled=true,connected=true;
    unsigned long last_log_time=0;MQTTTransportHealth::State transport_health;};
  MQTTSlot _slots[1];
  std::atomic<bool> _stop_requested{false};
  std::atomic<bool> _slot_health_reset_pending[1]{},_slot_transport_fault_pending[1]{};
  MQTTPublishAdmission::State _publish_memory;
  int _skipped_publishes=0;
  static constexpr unsigned long SLOT_LOG_INTERVAL=30000;
  static constexpr size_t PUBLISH_JSON_BUFFER_SIZE=2048;
  enum {MSG_PACKETS,MSG_RAW};
  char scratch[2048]{},*_json_scratch_buffer=scratch;
  int _json_scratch_doc=0;
  char _device_id[65]="ABC",_origin[32]="test";int _timezone=0;
  uint8_t* _last_raw_data=nullptr;int _last_raw_len=0;
  unsigned long _last_raw_timestamp=0;float _last_snr=0,_last_rssi=0;
  Radio* _radio=nullptr;uint8_t eligible=1;
  uint8_t eligiblePacketSlots(uint8_t,int){return eligible;}
  void refreshOriginFromPrefs(){}
  bool buildTopicForSlot(int,int,char*topic,size_t){strcpy(topic,"topic");return true;}
  void updateSlotTransportDiag(int,uint32_t){}
  bool hasPublishHeadroom(size_t=0,size_t=0,uint8_t=0);
  bool publishToSlot(int,const char*,const char*,size_t,bool=false,uint8_t=0);
  bool publishPacket(mesh::Packet*,bool,bool&,const uint8_t*=nullptr,int=0,float=0,float=0);
  bool publishRaw(mesh::Packet*,bool,bool&);
};
'''.replace('const uint8_t*=', 'const uint8_t* =')

MAIN = r'''
int main(){
  using namespace MQTTPublishAdmission;
  Heap heap;heap.psram=true;heap.dma_free=15348;heap.dma_largest=9716;
  assert(!canPublish(heap)); // Exact screenshot: preserve the DMA reserve.
  heap.dma_free+=5*897; // The five CPU-only reassembly buffers move to PSRAM.
  assert(canPublish(heap)); // No new 16K internal allocation is needed to write.
  heap.dma_free=16384;heap.dma_largest=4096;
  assert(canPublish(heap,2048,128,0));
  assert(!canPublish(heap,1024,128,1)); // Enqueue must also retain the reserve.
  --heap.dma_free;assert(!canPublish(heap));++heap.dma_free;
  --heap.dma_largest;assert(!canPublish(heap));++heap.dma_largest;
  heap.dma_free=16384+1024+128+128;
  assert(canPublish(heap,1024,128,1));
  --heap.dma_free;assert(!canPublish(heap,1024,128,1));
  assert(!canPublish(heap,10240,128,1)); // Large outbox records need more space.
  heap.dma_free=40000;heap.dma_largest=11000;
  assert(canPublish(heap,10240,128,1));
  assert(!canPublish(heap,SIZE_MAX,1,1)&&!canPublish(heap,1,SIZE_MAX,0));
  heap.psram=false;heap.internal_largest=7999;assert(!canPublish(heap));
  heap.internal_largest=8000;assert(canPublish(heap));
  State state;assert(state.needsSample(0));state.update(0,heap);
  assert(!state.needsSample(0)&&!state.needsSample(4999)&&state.needsSample(5000));
  state.update(UINT32_MAX-100,heap);
  assert(!state.needsSample(50)&&state.needsSample(5000));

  MQTTBridge bridge;Client client;bridge._slots[0].client=&client;
  mesh::Packet packet;bool eligible=false;
  assert(!bridge.publishPacket(&packet,false,eligible)&&eligible);
  assert(!bridge.publishRaw(&packet,false,eligible)&&eligible);
  assert(!bridge.publishToSlot(0,"status","{}",2,true,1));
  assert(builds==0&&client.calls==0&&bridge._skipped_publishes==3);
  assert(heap_queries==2); // Both denied paths cache the heap walk, even at tick0.
  assert(bridge._slots[0].transport_health.recovery_reason==
         MQTTTransportHealth::RecoveryReason::None);
  dma_free=19833;clock_ms=5000;
  assert(bridge.publishPacket(&packet,false,eligible));
  assert(bridge.publishRaw(&packet,false,eligible));
  assert(bridge.publishToSlot(0,"status","{}",2,true,1));
  assert(builds==2&&client.calls==3&&heap_queries==4);
  bridge.eligible=0;assert(!bridge.publishRaw(&packet,false,eligible)&&!eligible);
  assert(builds==2&&client.calls==3);
  client.result=-1;
  assert(!bridge.publishToSlot(0,"raw","{}",2,false,0));
  assert(!bridge._publish_memory.sampled); // Recheck after a real SDK failure.
}
'''


class MqttPublishAdmissionTests(unittest.TestCase):
    def test_actual_publish_paths_and_policy(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        source = HARNESS + '\n'.join(method(BRIDGE, signature) for signature in (
            'bool MQTTBridge::hasPublishHeadroom(', 'bool MQTTBridge::publishToSlot(',
            'bool MQTTBridge::publishPacket(', 'bool MQTTBridge::publishRaw(')) + MAIN
        with tempfile.TemporaryDirectory() as directory:
            path, binary = Path(directory)/'publish.cpp', Path(directory)/'publish'
            path.write_text(source)
            result = subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror',
                '-I'+str(ROOT/'src'),str(path),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
