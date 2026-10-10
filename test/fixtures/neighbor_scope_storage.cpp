#define BOARD_HAS_PSRAM 1
#define MAX_NEIGHBOURS 254
#define PUB_KEY_SIZE 32
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>
#include <helpers/MQTTPayloadBuilder.h>
namespace mesh {
struct Identity { uint8_t pub_key[32]; };
struct Utils {
  static void toHex(char* out, const uint8_t* key, size_t size) {
    for(size_t i=0;i<size;++i)snprintf(out+2*i,3,"%02x",key[i]);
  }
};
}
struct MQTTBridge {
@BRIDGE_CONSTANTS@
};
struct MQTTMessageBuilder : MQTTPayloadBuilder {
  static size_t measureNeighborsMessageEntry(const NeighborsMessageEntry& entry) {
    size_t measured=MQTTPayloadBuilder::measureNeighborsMessageEntry(entry);
#ifndef TEST_PUBLICATION_NEGATIVE_CONTROL
    // Production discovery calls this measurement before advancing its cursor.
    // Check the bound against its actual fields as well as formatter cases.
    assert(measured>=MQTTBridge::NEIGHBORS_MIN_DISCOVERY_ENTRY_JSON_BYTES);
#endif
    return measured;
  }
};
struct Clock { uint32_t getCurrentTime() { return UINT32_MAX; } };
struct MyMesh {
  enum { ND_UNSENT, ND_QUEUED, ND_PENDING, ND_RESPONDED, ND_TIMEOUT, ND_SEND_FAILED };
@LAYOUT@
  uint8_t neighbor_discover_next=0, neighbor_discover_count=254;
  uint8_t neighbor_discover_publish_count=0;
  size_t neighbor_discover_json_size=0;
  bool neighbor_discover_truncated=false, finished=false;
  Clock clock;
  Clock* getRTCClock() { return &clock; }
  void touchNeighbourHeard(const mesh::Identity&, uint32_t, float, int16_t) {}
  void finishNeighborDiscover() { finished=true; }
  bool completeNeighborDiscoverEntry();
  bool handleNeighborDiscoverResponse(int,const uint8_t*,size_t,float,int16_t);
};
struct LegacyMesh {
  enum { ND_UNSENT, ND_QUEUED, ND_PENDING, ND_RESPONDED, ND_TIMEOUT, ND_SEND_FAILED };
@LEGACY_LAYOUT@
  uint8_t neighbor_discover_next=0, neighbor_discover_count=254;
  uint8_t neighbor_discover_publish_count=0;
  size_t neighbor_discover_json_size=0;
  bool neighbor_discover_truncated=false, finished=false;
  Clock clock;
  Clock* getRTCClock() { return &clock; }
  void touchNeighbourHeard(const mesh::Identity&, uint32_t, float, int16_t) {}
  void finishNeighborDiscover() { finished=true; }
  bool completeNeighborDiscoverEntry();
  bool handleNeighborDiscoverResponse(int,const uint8_t*,size_t,float,int16_t);
};
@METHODS@
@LEGACY_METHODS@
template<class Mesh> std::string collect(Mesh& mesh,size_t scope_bytes,int state,int rssi,float snr) {
  const char* timestamp="2026-10-10T01:23:45Z";
  const std::string own_key(64,'a');
  mesh.neighbor_discover_json_size=MQTTPayloadBuilder::measureNeighborsMessageBase(
    "Seattle",own_key.c_str(),timestamp,"sea;pdx","sea",254);
  std::vector<std::string> keys; keys.reserve(254);
  std::vector<MQTTPayloadBuilder::NeighborsMessageEntry> entries;
  for(int i=0;i<254;++i) {
    auto& row=mesh.neighbor_discover[i];
    memset(row.id.pub_key,uint8_t(i),sizeof(row.id.pub_key));
    row.status=uint8_t(state); row.snr=int8_t(snr*4); row.rssi=int16_t(rssi);
    if(state==Mesh::ND_RESPONDED) {
      row.status=Mesh::ND_PENDING; row.tag=123;
      std::vector<uint8_t> response(8+scope_bytes,'x');
      memcpy(response.data(),&row.tag,4);
      assert(mesh.handleNeighborDiscoverResponse(i,response.data(),response.size(),snr,int16_t(rssi)));
    }
    if(!mesh.completeNeighborDiscoverEntry())break;
    char key[65]; mesh::Utils::toHex(key,row.id.pub_key,32); keys.emplace_back(key);
    entries.push_back({keys.back().c_str(),row.snr/4.0f,UINT32_MAX,
      mesh.neighbor_discover_scopes[i],state==Mesh::ND_RESPONDED ? "responded"
        : (state==Mesh::ND_SEND_FAILED ? "send_failed" : "timeout"),rssi,false});
  }
  assert(mesh.finished && mesh.neighbor_discover_truncated);
  assert(entries.size()==mesh.neighbor_discover_publish_count);
  JsonDocument document;
  std::vector<char> json(MQTTBridge::NEIGHBORS_JSON_BUFFER_SIZE);
  int length=MQTTPayloadBuilder::buildNeighborsMessage(document,"Seattle",own_key.c_str(),timestamp,
    "sea;pdx","sea",entries.data(),int(entries.size()),json.data(),json.size(),254,
    int(entries.size())+1,true);
  assert(length>0 && size_t(length)<json.size());
  assert(document["total_neighbors"].as<int>()==254 && document["truncated"].as<bool>());
  assert(document["neighbors"].as<JsonArray>().size()==entries.size());
  return std::string(json.data(),size_t(length));
}
int main() {
  MyMesh memory={}; LegacyMesh legacy_memory={};
  static_assert(sizeof(memory.neighbor_discover)/sizeof(memory.neighbor_discover[0])==254,
                "keep the complete neighbor snapshot capacity");
#ifndef TEST_PUBLICATION_NEGATIVE_CONTROL
  assert(sizeof(MyMesh::NeighborDiscoverEntry)==44);
  assert(sizeof(LegacyMesh::NeighborDiscoverEntry)==48);
  assert(sizeof(memory.neighbor_discover_scopes)==72*96);
  assert(sizeof(legacy_memory.neighbor_discover_scopes)-sizeof(memory.neighbor_discover_scopes)==17472);
  assert(sizeof(legacy_memory.neighbor_discover)-sizeof(memory.neighbor_discover)==1016);
#else
  (void)legacy_memory;
#endif
  const std::string key(64,'0');
  MQTTPayloadBuilder::NeighborsMessageEntry minimum={key.c_str(),0,0,"","",0,false};
  assert(MQTTPayloadBuilder::measureNeighborsMessageEntry(minimum)==128);
#ifndef TEST_PUBLICATION_NEGATIVE_CONTROL
  minimum.heard_secs_ago=UINT32_MAX; minimum.status="timeout";
  assert(MQTTPayloadBuilder::measureNeighborsMessageEntry(minimum)==144);
  assert(MQTTBridge::NEIGHBORS_MIN_DISCOVERY_ENTRY_JSON_BYTES<=144);
  for(int rssi : {0,-1,-100,INT16_MIN})
      for(float snr : {0.0f,-32.0f,12.75f})
          for(const char* status : {"responded","timeout","send_failed"}) {
            auto entry=minimum; entry.rssi=rssi;
            entry.snr=snr; entry.status=status;
            assert(MQTTPayloadBuilder::measureNeighborsMessageEntry(entry)>=MQTTBridge::NEIGHBORS_MIN_DISCOVERY_ENTRY_JSON_BYTES);
          }
#endif
  for(size_t scope : {size_t(0),size_t(1),size_t(31),size_t(95),size_t(200)})
    for(int state : {MyMesh::ND_RESPONDED,MyMesh::ND_TIMEOUT,MyMesh::ND_SEND_FAILED})
      for(int rssi : {0,-100})
        for(float snr : {0.0f,12.75f}) {
          MyMesh current={}; LegacyMesh baseline={};
          auto actual=collect(current,scope,state,rssi,snr);
          auto old=collect(baseline,scope,state,rssi,snr);
          assert(actual==old && current.neighbor_discover_publish_count==baseline.neighbor_discover_publish_count);
          assert(current.neighbor_discover_next<MyMesh::NEIGHBOR_SCOPE_RESULTS);
        }
  // Explicit bounds reject an unexpected late response and safely finish an
  // invalid cursor, even if future serializer changes violate the size proof.
  memory.neighbor_discover_next=MyMesh::NEIGHBOR_SCOPE_RESULTS;
  uint8_t response[8]={};
  assert(!memory.handleNeighborDiscoverResponse(int(MyMesh::NEIGHBOR_SCOPE_RESULTS),response,8,0,0));
  assert(!memory.completeNeighborDiscoverEntry() && memory.finished && memory.neighbor_discover_truncated);
  puts("real neighbor serialization and complete publication checks passed");
}
