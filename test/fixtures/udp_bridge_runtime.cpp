#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include <memory>
#include "helpers/bridges/BridgeBase.h"
#include "Arduino.h"
#include "lwip/sockets.h"
#include "helpers/bridges/UDPBridge.h"
#include <SHA256.h>
uint32_t test_now = 0;
uint32_t random_state = 0x1678abcd;
namespace mesh {
bool logging_enabled = false, debug_enabled = false;
DebugStream debug_stream;
}
namespace udp_test {
Socket sockets[64];
int next_socket = 0, opens = 0, receives = 0, sends = 0, receive_cap = 0;
uint32_t creating_ip = 0;
bool fail_send = false, fail_rcvbuf = false;
std::vector<Datagram> sent;
}
namespace udp = mesh::bridge::udp;
static bool hmac(void* context, const uint8_t* bytes, size_t size, uint8_t digest[32]) {
  const char* key = static_cast<const char*>(context);
  SHA256 sha; sha.resetHMAC(reinterpret_cast<const uint8_t*>(key), strlen(key));
  static const uint8_t domain[] = "MeshCore UDP bridge v1";
  sha.update(domain, sizeof(domain)); sha.update(bytes, size);
  sha.finalizeHMAC(reinterpret_cast<const uint8_t*>(key), strlen(key), digest, 32); return true;
}
static uint32_t address(uint8_t last) { uint8_t bytes[] = {10, 11, 12, last}; uint32_t value; memcpy(&value, bytes, 4); return value; }
static udp::Peer peer(uint8_t last) { udp::Peer value; value.ip[0] = 10; value.ip[1] = 11; value.ip[2] = 12; value.ip[3] = last; value.port = 4404; return value; }
struct Node {
  NodePrefs prefs; mesh::PacketManager manager; mesh::RTCClock rtc; udp::Prefs config; UDPBridge bridge;
  uint8_t id; int fd = -1;
  Node(uint8_t number, uint8_t other) : bridge(&prefs, &manager, &rtc, &config), id(number) {
    config.enabled = 1; config.peers[0] = peer(other);
  }
  void start() { udp_test::creating_ip = address(id); fd = udp_test::next_socket; bridge.serviceNetwork(true, address(id), 0x00ffffff); }
  std::string status() { char value[157]; bridge.formatStatus(value, sizeof(value)); return value; }
  std::string stats(const char* page) { char value[157]; bridge.formatStats(page, value, sizeof(value)); return value; }
};
static void run(Node& a, Node& b, unsigned count = 60) {
  for (unsigned i = 0; i < count; ++i) { test_now += 100; a.bridge.loop(); b.bridge.loop(); }
}
static udp_test::Datagram lastKind(udp::Kind kind, uint8_t sender) {
  for (auto it = udp_test::sent.rbegin(); it != udp_test::sent.rend(); ++it)
    if (it->source.sin_addr.s_addr == address(sender) && it->bytes[5] == uint8_t(kind)) return *it;
  assert(false); return {};
}
static void codec() {
  const char* key = "unit-test-key"; uint8_t payload[255]; memset(payload, 7, sizeof(payload));
  uint8_t bytes[udp::MAX_FRAME + 1];
  udp::Frame frame{udp::Kind::Mesh, 17, 29, 1, payload, sizeof(payload)};
  const size_t size = udp::encode(bytes, sizeof(bytes), frame, hmac, const_cast<char*>(key));
  assert(size == 299);
  // Independently calculated with Python hmac/hashlib, not this codec.
  static const uint8_t expected_tag[] = {0x98,0xee,0xa8,0xf4,0x80,0x8e,0x94,0x55,
      0x73,0x63,0xf1,0xf3,0xdb,0xbe,0xac,0x29};
  assert(!memcmp(bytes + size - udp::TAG_SIZE, expected_tag, sizeof(expected_tag)));
  udp::Frame decoded;
  assert(udp::decode(bytes, size, decoded, hmac, const_cast<char*>(key)) == udp::Decode::Ok);
  assert(decoded.length == 255 && decoded.sender == 17 && decoded.recipient == 29);
  for (size_t i = 0; i < size; ++i) {
    bytes[i] ^= 1;
    udp::Frame untouched; untouched.sequence = 789;
    assert(udp::decode(bytes, size, untouched, hmac, const_cast<char*>(key)) != udp::Decode::Ok);
    assert(untouched.sequence == 789); bytes[i] ^= 1;
  }
  for (size_t n = 0; n < size; ++n)
    assert(udp::decode(bytes, n, decoded, hmac, const_cast<char*>(key)) != udp::Decode::Ok);
  assert(udp::decode(bytes, size + 1, decoded, hmac, const_cast<char*>(key)) != udp::Decode::Ok);
  assert(udp::decode(bytes, size, decoded, hmac, const_cast<char*>("other-key")) == udp::Decode::Authentication);
  assert(!udp::encode(bytes, size - 1, frame, hmac, const_cast<char*>(key)));
  udp::Session session;
  assert(session.request(11, 77, 0)); assert(!session.prove(11, 78, 1)); assert(session.prove(11, 77, 2));
  assert(session.admit(11, 33, 33, 100)); assert(!session.admit(11, 33, 33, 100));
  assert(!session.admit(11, 33, 33, 99)); assert(!session.admit(11, 34, 33, 101));
  assert(session.request(22, 88, 3)); assert(!session.prove(22, 77, 4));
  assert(session.remote == 11 && session.received == 100);
  assert(session.prove(22, 88, 5)); assert(session.remote == 22 && !session.received);
  assert(session.request(11, 99, 6)); assert(!session.prove(11, 77, 7));
  assert(session.remote == 22); // Recorded retired sessions cannot flip it back.
  assert(session.prove(11, 99, 8)); assert(session.admit(11, 33, 33, 5));
  assert(session.request(11, 42, 9)); assert(session.prove(11, 42, 10)); assert(session.received == 5);
  assert(session.request(22, 123, 11)); assert(!session.prove(22, 123, 5011));
  uint32_t saturated = UINT32_MAX; udp::increment(saturated); assert(saturated == UINT32_MAX);
}
static void isolation() {
  Node a(1,2), b(2,1); a.start(); b.start();
  assert(udp_test::receive_cap == 768); assert(!a.bridge.sendTest(0, 1));
  run(a,b); assert(a.status().find("ready=1") != std::string::npos);
  assert(b.status().find("ready=1") != std::string::npos);
  const unsigned allocations = b.manager.allocations;
  assert(a.bridge.sendTest(0,0xa1b2c3d4)); run(a,b,5);
  assert(b.stats("test").find("rx=1") != std::string::npos);
  assert(b.stats("test").find("rxid=A1B2C3D4") != std::string::npos);
  assert(b.manager.allocations == allocations && b.manager.received.empty());
  const auto packet = lastKind(udp::Kind::Probe,1);
  const auto before = udp_test::sent.size(); udp_test::inject(b.fd,packet); b.bridge.loop();
  assert(udp_test::sent.size() == before); assert(b.stats("test").find("rx=1") != std::string::npos);
  assert(b.stats("drops").find("replay=1") != std::string::npos);
  auto forged = packet; forged.bytes.back() ^= 1; udp_test::inject(b.fd,forged); b.bridge.loop();
  assert(b.stats("drops").find("auth=1") != std::string::npos);
  forged = packet; forged.source.sin_port = htons(4405); udp_test::inject(b.fd,forged); b.bridge.loop();
  assert(b.stats("drops").find("foreign=1") != std::string::npos);
  assert(b.manager.allocations == allocations);
}
static void recovery() {
  Node a(1,2), b(2,1); a.start(); b.start(); run(a,b);
  assert(a.bridge.sendTest(0,1)); run(a,b,5); const auto old = lastKind(udp::Kind::Probe,1);
  const auto old_hello = lastKind(udp::Kind::Hello,1);
  const auto old_proof = lastKind(udp::Kind::Proof,1);
  a.bridge.end(); a.start(); run(a,b,120);
  assert(a.bridge.sendTest(0,2)); run(a,b,5);
  assert(b.stats("test").find("rx=2") != std::string::npos);
  udp_test::inject(b.fd,old); udp_test::inject(b.fd,old_hello); b.bridge.loop();
  udp_test::inject(b.fd,old_proof); b.bridge.loop();
  assert(b.stats("test").find("rx=2") != std::string::npos);
  assert(a.bridge.sendTest(0,3)); run(a,b,5);
  assert(b.stats("test").find("rx=3") != std::string::npos);
  // A fresh receiver boot rejects all recorded old-recipient frames.
  b.bridge.end(); b.start(); run(a,b,120);
  udp_test::inject(b.fd,old); b.bridge.loop();
  assert(b.stats("test").find("rx=3") != std::string::npos);
  assert(a.bridge.sendTest(0,4)); run(a,b,5);
  assert(b.stats("test").find("rx=4") != std::string::npos);
}
static void queues() {
  Node a(1,2), b(2,1); a.start(); b.start(); run(a,b);
  mesh::Packet packet; packet.payload = {1,0,3,4};
  a.bridge.sendPacket(&packet); a.bridge.sendPacket(&packet); run(a,b,5);
  assert(b.manager.received.size() == 1);
  assert(b.manager.received_sources.size() == 1);
  const auto first_source = b.manager.received_sources.front();
  assert(first_source.kind == mesh::bridge::IngressSource::Kind::Udp);
  assert(first_source.bytes[0] == 10 && first_source.bytes[1] == 11 &&
         first_source.bytes[2] == 12 && first_source.bytes[3] == 1);
  assert(first_source.bytes[4] == (4404 >> 8) && first_source.bytes[5] == (4404 & 255));
  assert(a.stats("tx").find("queued=1") != std::string::npos);
  // Same payload received through LoRa is deduplicated; received UDP packets
  // are marked locally and cannot re-export even for OTA.
  packet.udp_bridge_ingress = true; packet.payload[0] = PAYLOAD_TYPE_OTA << 2;
  a.bridge.sendPacket(&packet); run(a,b,2); assert(b.manager.received.size() == 1);
  packet.udp_bridge_ingress = false;
  b.manager.starved = true;
  a.bridge.sendPacket(&packet); run(a,b,3);
  const unsigned held = b.manager.allocations; assert(held > 1 && b.manager.received.size() == 1);
  const int reads = udp_test::receives; b.bridge.loop();
  assert(udp_test::receives == reads); // Held authenticated bytes own the RX slot.
  b.manager.starved = false; b.bridge.loop(); assert(b.manager.received.size() == 2);
  assert(b.manager.received_sources.size() == 2);
  assert(!memcmp(b.manager.received_sources.back().bytes, first_source.bytes, 6));
  // Failed UDP send is final, never automatically retried.
  packet.payload[3]++; a.bridge.sendPacket(&packet); udp_test::fail_send = true;
  a.bridge.loop(); const int sends = udp_test::sends; run(a,b,2);
  assert(udp_test::sends == sends); assert(a.stats("tx").find("err=1") != std::string::npos);
  // Unauthenticated noise, including oversized frames, never borrows the pool.
  auto noise = lastKind(udp::Kind::Mesh,1); noise.bytes.assign(600,0xaa);
  const unsigned allocations = b.manager.allocations; udp_test::inject(b.fd,noise); b.bridge.loop();
  assert(b.manager.allocations == allocations);
  // A valid maximum-size HMAC prefix followed by extra bytes must not become
  // a valid datagram when recvfrom truncates the oversized UDP payload.
  uint8_t payload[255]{}; payload[0] = 1;
  udp::Frame shape{udp::Kind::Mesh,1,2,1,payload,sizeof(payload)};
  uint8_t encoded[udp::MAX_FRAME];
  const auto size = udp::encode(encoded,sizeof(encoded),shape,hmac,a.prefs.bridge_secret);
  noise.bytes.assign(encoded,encoded+size); noise.bytes.push_back(0); noise.bytes.resize(600,0);
  udp_test::inject(b.fd,noise); b.bridge.loop(); assert(b.manager.allocations == allocations);
  packet.allowed = false; packet.payload[3]++; a.bridge.sendPacket(&packet);
  assert(a.stats("drops").find("txfilter=2") != std::string::npos);
}
static void fanout() {
  Node source(1,2); std::vector<std::unique_ptr<Node>> peers;
  for (uint8_t i = 0; i < udp::MAX_PEERS; ++i) {
    source.config.peers[i] = peer(i+2);
    peers.emplace_back(new Node(i+2,1)); peers.back()->start();
  }
  source.start();
  auto cycle = [&]() { test_now += 100; source.bridge.loop(); for (auto& peer : peers) peer->bridge.loop(); };
  for (unsigned i=0;i<100;++i) cycle();
  assert(source.status().find("ready=8") != std::string::npos);
  mesh::Packet packet; packet.payload = {1,0,12}; source.bridge.sendPacket(&packet);
  // New data cannot overwrite the bytes already owned by the fan-out slot.
  packet.payload[2]++; source.bridge.sendPacket(&packet);
  assert(source.stats("tx").find("busy=1") != std::string::npos);
  for (unsigned i=0;i<10;++i) cycle();
  for (auto& peer : peers) assert(peer->manager.received.size() == 1 && peer->manager.received[0][2] == 12);
  assert(source.stats("tx").find("sent=8") != std::string::npos);
  assert(source.bridge.sendTest(7,0x76543210));
  for (unsigned i=0;i<4;++i) cycle();
  for (unsigned i=0;i<8;++i) {
    assert(peers[i]->stats("test").find(i==7 ? "rx=1" : "rx=0") != std::string::npos);
    assert(peers[i]->manager.received.size() == 1);
  }
}
static void rotation() {
  Node a(1,2), b(2,1); a.start(); b.start(); run(a,b);
  assert(a.bridge.sendTest(0,1)); run(a,b,5);
  const auto recorded = lastKind(udp::Kind::Probe,1);
  assert(a.bridge.sendTest(0,2)); // Old-key queued probe must be canceled.
  strcpy(a.prefs.bridge_secret,"changed-key");
  udp_test::creating_ip=address(a.id); a.bridge.serviceNetwork(true,address(a.id),0x00ffffff); a.fd=udp_test::next_socket-1;
  run(a,b,60);
  assert(b.stats("test").find("rx=1") != std::string::npos);
  assert(a.status().find("ready=0") != std::string::npos);
  strcpy(b.prefs.bridge_secret,"changed-key");
  udp_test::creating_ip=address(b.id); b.bridge.serviceNetwork(true,address(b.id),0x00ffffff); b.fd=udp_test::next_socket-1; run(a,b,120);
  assert(a.status().find("ready=1") != std::string::npos);
  assert(a.bridge.sendTest(0,3)); run(a,b,5);
  assert(b.stats("test").find("rx=2") != std::string::npos);
  const auto allocations = b.manager.allocations;
  b.bridge.resetStats();
  udp_test::inject(b.fd,recorded); b.bridge.loop();
  assert(b.stats("test").find("rx=0") != std::string::npos && b.manager.allocations == allocations);
  assert(b.stats("drops").find("auth=1") != std::string::npos);
  assert(a.bridge.sendTest(0,4)); a.prefs.bridge_secret[0]=0;
  udp_test::creating_ip=address(a.id); a.bridge.serviceNetwork(true,address(a.id),0x00ffffff); a.fd=udp_test::next_socket-1;
  assert(!a.bridge.isRunning()); run(a,b,5);
  assert(b.stats("test").find("rx=0") != std::string::npos);
}
static void network() {
  Node a(1,2), b(2,1);
  a.bridge.begin(); a.bridge.serviceNetwork(false); assert(!a.bridge.isRunning() && !udp_test::opens);
  a.start(); b.start(); run(a,b);
  assert(a.bridge.sendTest(0,1)); a.bridge.serviceNetwork(false);
  assert(!a.bridge.isRunning()); a.bridge.loop(); assert(a.stats("test").find("sent=0") != std::string::npos);
  a.start(); run(a,b,120); assert(a.status().find("ready=1") != std::string::npos);
  a.bridge.end(); a.prefs.bridge_secret[0] = 0; const int opened = udp_test::opens;
  a.start(); assert(!a.bridge.isRunning() && udp_test::opens == opened);
  strcpy(a.prefs.bridge_secret,"unit-test-key");
  test_now += 5000; udp_test::fail_rcvbuf = true; a.start();
  assert(!a.bridge.isRunning()); udp_test::fail_rcvbuf = false;
  a.config.peers[0] = peer(255); test_now += 5000; a.start(); assert(!a.bridge.isRunning());
  a.config.peers[0] = peer(1); test_now += 5000; a.start(); assert(!a.bridge.isRunning());
  a.config.peers[0] = peer(2); test_now = 0xf0000000; a.bridge.serviceNetwork(false); a.start();
  assert(a.bridge.isRunning()); const auto sends = udp_test::sends; test_now += 1000; a.bridge.loop(); assert(udp_test::sends > sends);
}
static void bounds() {
  Node a(1,2), b(2,1); a.start(); b.start(); run(a,b);
  for (const char* page : {"", "rx", "tx", "drops", "test", "unknown"}) {
    for (size_t capacity = 1; capacity <= 157; ++capacity) {
      char bytes[160]; memset(bytes,0x55,sizeof(bytes));
      a.bridge.formatStats(page,bytes + 1,capacity);
      assert(bytes[0] == 0x55 && bytes[capacity+1] == 0x55 && bytes[capacity] == 0);
      a.bridge.formatStatus(bytes + 1,capacity);
      assert(bytes[0] == 0x55 && bytes[capacity+1] == 0x55 && bytes[capacity] == 0);
    }
  }
  mesh::logging_enabled = true; mesh::debug_enabled = false;
  test_now += 1000; a.bridge.loop(); assert(!mesh::debug_stream.writes);
  mesh::debug_enabled = true; a.bridge.loop(); assert(mesh::debug_stream.writes == 1);
  const auto bytes = std::string(mesh::debug_stream.bytes.begin(),mesh::debug_stream.bytes.end());
  assert(bytes.find("unit-test-key") == std::string::npos);
  const unsigned count = mesh::debug_stream.writes; a.bridge.loop(); assert(mesh::debug_stream.writes == count);
  a.bridge.resetStats(); assert(a.stats("test").find("rx=0") != std::string::npos);
}
int main(int argc, char** argv) {
  assert(argc == 2); const std::string name = argv[1];
  if (name == "codec") codec(); else if (name == "isolation") isolation();
  else if (name == "recovery") recovery(); else if (name == "queues") queues();
  else if (name == "rotation") rotation(); else if (name == "network") network(); else if (name == "fanout") fanout(); else if (name == "bounds") bounds(); else assert(false);
  printf("%s passed\n",argv[1]);
}
