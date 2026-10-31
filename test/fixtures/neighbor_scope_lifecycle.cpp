#ifdef TEST_PSRAM
#define BOARD_HAS_PSRAM 1
#endif
#define MAX_NEIGHBOURS 254
#define PUB_KEY_SIZE 32
#define MAX_CLIENTS 32
#define PATH_HASH_SIZE 1
#define WITH_MQTT_NEIGHBORS 1
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define PERM_ACL_ROLE_MASK 3
#define PERM_ACL_GUEST 0
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>
#include <helpers/MQTTPayloadBuilder.h>
namespace mesh {
struct Identity {
  uint8_t pub_key[32];
@HASH_MATCH@
};
struct LocalIdentity : Identity {
  mutable unsigned derivations=0;
  // A deterministic crypto dependency records which immutable key the real
  // getPeerSharedSecret body selected; this is a routing test, not an ECDH test.
  void calcSharedSecret(uint8_t* out,const Identity& other) const {
    ++derivations;
    for(size_t i=0;i<32;++i) out[i]=uint8_t(other.pub_key[i]^0xA5);
  }
};
struct Packet {};
struct Utils {
  static void toHex(char* out, const uint8_t* key, size_t size) {
    for (size_t i=0;i<size;++i) snprintf(out+2*i,3,"%02x",key[i]);
  }
};
}
struct MQTTBridge {
@BRIDGE_CONSTANTS@
  std::string last_json;
  int publishes=0;
  void requestPublishNeighbors(const char* value,size_t size) {
    last_json.assign(value,size); ++publishes;
  }
};
struct MQTTMessageBuilder : MQTTPayloadBuilder {
  static void formatIsoTimestampForMqtt(uint32_t,long,void*,char* out,size_t size) {
    snprintf(out,size,"2026-10-10T01:23:45Z");
  }
};
struct MQTTConnectionPolicy { static const uint32_t kSyncedClockEpoch=1700000000; };
struct ObserverPrefs { bool mqtt_neighbors_enabled=true; uint32_t mqtt_neighbors_interval=60000; };
struct Cli { ObserverPrefs prefs; ObserverPrefs* getObserverPrefs() { return &prefs; } };
struct Clock { uint32_t getCurrentTime() { return 1800000000; } };
struct ClientInfo { mesh::Identity id; uint8_t permissions; uint8_t shared_secret[32]; };
struct ACL {
  std::vector<ClientInfo> clients;
  ClientInfo* getClient(const uint8_t* key,size_t size) {
    for(auto& client:clients) if(!memcmp(key,client.id.pub_key,size)) return &client;
    return nullptr;
  }
  int getNumClients() const { return int(clients.size()); }
  ClientInfo* getClientByIdx(int i) { return &clients.at(size_t(i)); }
};
struct GuestLogin { bool value=false; bool enabled() const { return value; } };
struct NeighbourInfo { mesh::Identity id; uint32_t heard_timestamp; int8_t snr; int16_t rssi;
  Radio2DiscoverTrailer radio2; };
static bool fail_scratch=false, fail_json=false;
static unsigned allocation=0;
static void* neighborsAlloc(size_t size) {
  ++allocation;
  if (!size || (allocation==1 && fail_scratch) || (allocation==2 && fail_json)) return nullptr;
  return malloc(size);
}
static void neighborsFree(void* ptr) { free(ptr); }
static uint32_t futureMillis(uint32_t value) { return value+10; }
static bool millisHasNowPassed(uint32_t) { return true; }
static const uint32_t NEIGHBOR_DISCOVER_QUEUE_TIMEOUT_MS=100;
@HELPERS@
struct MyMesh {
  enum { ND_UNSENT, ND_QUEUED, ND_PENDING, ND_RESPONDED, ND_TIMEOUT, ND_SEND_FAILED };
@LAYOUT@
@STATE@
};
struct LegacyMesh {
  enum { ND_UNSENT, ND_QUEUED, ND_PENDING, ND_RESPONDED, ND_TIMEOUT, ND_SEND_FAILED };
@LEGACY_LAYOUT@
@STATE@
};
@METHODS@
@LEGACY_METHODS@
template<class Mesh> std::string runPass(Mesh& m,int count,int result,size_t scope,bool admission=true) {
  m.admit=admission;
  m.requested=0;
  m.touched=0;
  m.mock_bridge.last_json.clear();
  memset(m.neighbours,0,sizeof(m.neighbours));
  for (int i=0;i<count;++i) {
    auto& row=m.neighbours[i];
    memset(row.id.pub_key,uint8_t(i),32);
    row.heard_timestamp=1799999700+uint32_t(i);
    row.snr=int8_t(i%32);
    row.rssi=int16_t(-80-i%15);
  }
  // The newest neighbour (published first) answered the radio2 flag: MediaMesh
  // 911.150 MHz / 250 kHz / SF7 / CR5, rxtx. The rest sent no trailer.
  if (count>0) {
    static const uint8_t trailer[]={1,0x05,0x2E,0xE7,0x0D,0x00,0x90,0xD0,0x03,0x00,7,5};
    m.neighbours[count-1].radio2=Radio2DiscoverTrailer::parse(trailer,sizeof(trailer));
  }
  memset(m.neighbor_discover_scopes,'z',sizeof(m.neighbor_discover_scopes));
  allocation=0;
  char reply[160]={};
  const int old_publishes=m.mock_bridge.publishes;
  assert(m.startNeighborDiscover(reply));
  if (count && admission) {
    assert(m.neighbor_discover_count==count);
    assert(m.neighbor_discover[0].id.pub_key[0]==uint8_t(count-1));
    assert(m.neighbor_discover[count-1].id.pub_key[0]==0);
    // Snapshot every entry, independent of the live table's later changes.
    memset(m.neighbours,0,sizeof(m.neighbours));
    for (size_t i=0;i<sizeof(m.neighbor_discover_scopes);++i)
      assert(reinterpret_cast<char*>(m.neighbor_discover_scopes)[i]==0);
  }
  int steps=0;
  while(m.neighbor_discover_active) {
    assert(++steps<1024);
    auto& entry=m.neighbor_discover_metadata[m.neighbor_discover_next];
    if(entry.status==Mesh::ND_QUEUED) {
      // The real TX hook starts the reply timeout, not queue admission.
      if(result==Mesh::ND_SEND_FAILED) m.markFailed();
      else m.markTransmitted();
      if(result==Mesh::ND_RESPONDED) {
        std::vector<uint8_t> response(8+scope,'x');
        memcpy(response.data(),&entry.tag,4);
        assert(m.handleNeighborDiscoverResponse(m.neighbor_discover_next,
                                               response.data(),response.size(),5.0f,-99));
      }
    }
    m.loopNeighborDiscover();
  }
  assert(m.neighbor_discover_count==0 && m.neighbor_discover_next==0);
  assert(m.neighbor_discover_publish_count==0 && m.neighbor_discover_queried_count==0);
  assert(!m.neighbor_discover_request && m.neighbor_discover_until==0);
  assert(m.next_neighbors_publish==60010);
  assert(m.mock_bridge.publishes==old_publishes+1);
  JsonDocument document;
  assert(!deserializeJson(document,m.mock_bridge.last_json));
  assert(document["total_neighbors"].template as<int>()==count);
  if(count>0) assert(document["neighbors"][0]["pubkey"].template as<const char*>()!=nullptr);
  // radio2 (MediaMesh): this node's own, and the one neighbour that reported one.
  assert(!strcmp(document["self"]["radio2"]["mode"].template as<const char*>(),"rxtx"));
  {
    int with_radio2=0;
    for (JsonObject nb : document["neighbors"].template as<JsonArray>()) {
      if (!nb["radio2"].template is<JsonObject>()) continue;
      ++with_radio2;
      assert(!strcmp(nb["radio2"]["mode"].template as<const char*>(),"rxtx"));
      assert(!strcmp(nb["radio2"]["radio"].template as<const char*>(),"911.150000,250.0,7,5"));
      assert(nb["pubkey"].template as<const char*>()[0]==
             "0123456789abcdef"[((count-1)>>4)&15]);
    }
    const bool newest_published=count>0 && document["neighbors"].template as<JsonArray>().size()>0;
    assert(with_radio2==(newest_published ? 1 : 0));
  }
  assert(document["neighbors"].template as<JsonArray>().size()<=size_t(count));
  if(count>MQTTBridge::NEIGHBORS_MAX_PUBLISH_ENTRIES)
    assert(document["truncated"].template as<bool>());
  return m.mock_bridge.last_json;
}

struct Snapshot {
  mesh::Identity id; uint32_t heard; int8_t snr; int16_t rssi;
};
// The old full-entry model snapshots every admitted row, then stable-sorts the
// complete rows. Compare the new indexed snapshot against an independent
// standard-library implementation rather than a second copy of its loop.
template<class Mesh> void checkOverlay(bool collision,bool guests) {
  Mesh m={};
  m.guest_login.value=guests;
  std::vector<Snapshot> previous;
  for(int i=0;i<254;++i) {
    auto& row=m.neighbours[i];
    for(size_t j=0;j<32;++j) row.id.pub_key[j]=uint8_t(i+j*13);
    row.id.pub_key[0]=collision ? 0x42 : uint8_t(i%16);
    row.heard_timestamp=1799999900+uint32_t((i*37)%11);
    row.snr=int8_t((i*7)%8);
    row.rssi=int16_t(-80-i%20);
    previous.push_back({row.id,row.heard_timestamp,row.snr,row.rssi});
  }
  // Add unheard rows too: only the positive-time source rows enter discovery.
  if(collision) {
    m.neighbours[42].heard_timestamp=0;
    previous.erase(previous.begin()+42);
  }
  std::stable_sort(previous.begin(),previous.end(),[](const Snapshot& lhs,const Snapshot& rhs) {
    if(lhs.heard!=rhs.heard) return lhs.heard>rhs.heard;
    if(lhs.snr!=rhs.snr) return lhs.snr>rhs.snr;
    return memcmp(lhs.id.pub_key,rhs.id.pub_key,32)<0;
  });
  for(size_t i : {size_t(0),size_t(17),size_t(73),size_t(200),size_t(252)}) {
    ClientInfo client={};client.id=previous[i].id;
    client.permissions=i%2 ? PERM_ACL_GUEST : 1;
    memset(client.shared_secret,uint8_t(0x30+i%16),32);
    m.acl.clients.push_back(client);
  }
  char reply[160]={};allocation=0;
  assert(m.startNeighborDiscover(reply));
  assert(m.neighbor_discover_count==previous.size());
#ifdef TEST_LOST_KEY_TAIL
  for(size_t i=Mesh::NEIGHBOR_SCOPE_RESULTS;i<previous.size();++i)
    memset(m.neighbor_discover[i].id.pub_key,0,32);
#endif
  for(size_t i=0;i<previous.size();++i) {
    assert(!memcmp(m.neighbor_discover[i].id.pub_key,previous[i].id.pub_key,32));
    if(i<Mesh::NEIGHBOR_SCOPE_RESULTS) {
      const auto& metadata=m.neighbor_discover_metadata[i];
      assert(metadata.heard_timestamp==previous[i].heard);
      assert(metadata.snr==previous[i].snr && metadata.rssi==previous[i].rssi);
    }
  }
  // Mutating the live cache after start must not alter even an unqueried key.
  memset(m.neighbours,0xE7,sizeof(m.neighbours));
  for(bool active : {true,false}) {
    m.neighbor_discover_active=active;
    for(unsigned hash=0;hash<256;++hash) {
      const uint8_t key_hash=uint8_t(hash);
      std::vector<int> old_matches;
      if(active) for(size_t i=0;i<previous.size() && old_matches.size()<MAX_CLIENTS;++i) {
        if(m.acl.getClient(previous[i].id.pub_key,32)) continue;
        if(previous[i].id.pub_key[0]==key_hash)
          old_matches.push_back(Mesh::NEIGHBOR_DISCOVER_PEER_BASE+int(i));
      }
      for(size_t i=0;i<m.acl.clients.size() && old_matches.size()<MAX_CLIENTS;++i) {
        const auto& client=m.acl.clients[i];
#ifdef TEST_REPEATER_ROLE
        if(!guests && (client.permissions&PERM_ACL_ROLE_MASK)==PERM_ACL_GUEST) continue;
#endif
        if(client.id.pub_key[0]==key_hash) old_matches.push_back(int(i));
      }
      const int found=m.searchPeersByHash(&key_hash);
      assert(size_t(found)==old_matches.size());
      for(int i=0;i<found;++i) {
        assert(m.matching_peer_indexes[i]==old_matches[size_t(i)]);
        uint8_t actual[32]={},expected[32]={};
        m.getPeerSharedSecret(actual,i);
        const int match=old_matches[size_t(i)];
        if(match>=Mesh::NEIGHBOR_DISCOVER_PEER_BASE) {
          const auto& id=previous[size_t(match-Mesh::NEIGHBOR_DISCOVER_PEER_BASE)].id;
          for(size_t b=0;b<32;++b) expected[b]=uint8_t(id.pub_key[b]^0xA5);
        } else memcpy(expected,m.acl.clients[size_t(match)].shared_secret,32);
        assert(!memcmp(actual,expected,32));
      }
    }
  }
  m.neighbor_discover_active=true;
  // Exercise every sorted identity directly, including keys beyond the
  // publishable/query prefix and keys hidden behind the collision-result cap.
  const unsigned before=m.self_id.derivations;
  for(size_t i=0;i<previous.size();++i) {
    m.matching_peer_indexes[0]=Mesh::NEIGHBOR_DISCOVER_PEER_BASE+int(i);
    uint8_t actual[32]={};m.getPeerSharedSecret(actual,0);
    for(size_t b=0;b<32;++b) assert(actual[b]==uint8_t(previous[i].id.pub_key[b]^0xA5));
  }
  assert(m.self_id.derivations-before==previous.size());
}
int main() {
  for(bool collision : {true,false}) for(bool guests : {true,false}) {
    checkOverlay<MyMesh>(collision,guests);
    checkOverlay<LegacyMesh>(collision,guests);
  }
  for(int count : {0,1,19,20,21,30,71,72,73,253,254})
    for(int result : {MyMesh::ND_RESPONDED,MyMesh::ND_TIMEOUT,MyMesh::ND_SEND_FAILED})
      for(size_t scope : {size_t(0),size_t(95),size_t(180)}) {
        MyMesh current={}; LegacyMesh baseline={};
        for(bool admitted : {true,false}) {
          auto actual=runPass(current,count,result,scope,admitted);
          auto previous=runPass(baseline,count,result,scope,admitted);
          assert(actual==previous && current.requested==baseline.requested);
          assert(current.touched==baseline.touched);
        }
      }
  // Failed publish allocation still resets the complete discovery lifetime;
  // the next pass starts with empty scopes and cannot retain stale replies.
  for(bool scratch : {true,false}) {
    MyMesh current={};
    current.neighbours[0].heard_timestamp=1799999999;
    fail_scratch=scratch; fail_json=!scratch; allocation=0;
    char reply[160]={}; assert(current.startNeighborDiscover(reply));
    current.markTransmitted();
    for (int i=0; current.neighbor_discover_active && i<4; ++i) current.loopNeighborDiscover();
    assert(!current.neighbor_discover_active && current.mock_bridge.publishes==0);
    assert(current.neighbor_discover_count==0 && !current.neighbor_discover_request);
    fail_scratch=fail_json=false;
    runPass(current,1,MyMesh::ND_TIMEOUT,0);
  }
  puts("real neighbor serialization and lifecycle checks passed");
}
