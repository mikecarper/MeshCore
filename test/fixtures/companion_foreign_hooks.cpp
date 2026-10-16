// Only application boundaries are stubbed. The Companion declarations,
// callbacks, inherited Mesh direct hooks and Dispatcher are production code.
@RADIO@
#include <helpers/ota/OtaManager.h>
#include <Arduino.h>
#include <cstdio>

struct ChannelDetails { struct { uint8_t secret[32] = {}; } channel; };
namespace mesh { namespace mt {
constexpr unsigned DeliverySlots = 2;
struct PendingFrame {
  uint32_t generation = 0, due = 0;
  bool heard = false, cancelled = false, relayed = false, nodeinfo = false, sent = false;
  int channel = 0;
  unsigned retries = 0;
  bool matchesRelayCopy(const uint8_t*, size_t) const { return false; }
  bool matchesEcho(const uint8_t*, size_t) const { return false; }
  void clear() { *this = {}; }
};
struct Channel { const char* name = "test"; uint8_t key[32] = {}; size_t key_length = 16; };
struct DecodedText { uint32_t from = 0, id = 0; uint8_t hops = 0; size_t text_len = 0; char text[32] = {}; };
inline bool decodeText(const uint8_t*, size_t, const char*, const uint8_t*, size_t,
                       uint32_t, DecodedText&) { return false; }
} }
static bool describe(const ChannelDetails&, mesh::mt::Channel&) { return false; }
static unsigned meshtextRetryLimit(const mesh::mt::PendingFrame&) { return 0; }
static uint32_t meshtextRetryDelay(const mesh::mt::PendingFrame&) { return 100; }
namespace mesh { namespace ota {
struct BoundaryManager {
  unsigned calls = 0;
  bool enabled = false;
  uint8_t hops = 255;
  OtaReplyRoute route;
  uint32_t directTransportEpoch() const { return 42; }
  void set_direct_transport_enabled(bool value) { enabled = value; }
  void note_rx_path_hops(uint8_t value) { hops = value; }
  bool on_message(const uint8_t*, uint16_t, OtaReplyRoute value) { ++calls; route = value; return true; }
  uint8_t fetchState() const { return 0; }
};
struct BoundaryContext {
  BoundaryManager manager;
  void track_session(uint8_t, unsigned long) {}
} context;
inline BoundaryContext* ota_context_if_active() { return &context; }
} }
namespace mesh {
class Mesh : public Dispatcher {
public:
  bool temp = false, primary = false;
  mutable unsigned channel_queries = 0;
  Mesh(Radio& r, MillisecondClock& c, PacketManager& m) : Dispatcher(r, c, m) {}
  bool isAnyTempRadioActive() const { return temp; }
  bool isTempRadioActive() const { return primary; }
  bool getChannel(int, ChannelDetails&) const { ++channel_queries; return false; }
  void syncOtaTiming(ota::BoundaryManager&) {}
  struct RTC { uint32_t getCurrentTime() const { return 1000; } } rtc;
  RTC* getRTCClock() { return &rtc; }
@DIRECT_DECLARATIONS@
};
@DIRECT_METHODS@
}
using BaseChatMesh = mesh::Mesh;
#define MAX_GROUP_CHANNELS 1
#define MAX_FRAME_SIZE 176
class MyMesh : public BaseChatMesh {
public:
  bool _radio_available = true;
  uint32_t _scheduled_reboot_at = 0;
  bool _mt_active = false, _ethernet_active = false;
  unsigned _mt_received = 0, _mt_sent = 0, _mt_failed = 0, _mt_heard = 0, _mt_retried = 0;
  unsigned _ethernet_received = 0, _ethernet_sent = 0, _ethernet_failed = 0;
  uint32_t _mt_last_tx_completed_at = 0, _mt_node = 1, _mt_nodeinfo_generation = 0;
  int _mt_tx_slot = -1;
  bool _mt_nodeinfo_pending[1] = {}, _mt_nodeinfo_announced[1] = {};
  mesh::mt::PendingFrame _mt_delivery[mesh::mt::DeliverySlots];
  struct Seen {
    bool seen(uint32_t, uint32_t) const { return false; }
    void remember(uint32_t, uint32_t) {}
  } _mt_seen;
  struct Kiss {
    unsigned calls = 0;
    bool queueReceive(const uint8_t* bytes, size_t length) { assert(bytes && length); ++calls; return true; }
  } _ethernet_kiss;
  bool meshtextDeliveryCurrent(const mesh::mt::PendingFrame&) { return false; }
  bool getChannel(int index, ChannelDetails& channel) { return BaseChatMesh::getChannel(index, channel); }
  template <typename... Args> bool queueChannelText(Args&&...) { return false; }
  void maybeRebroadcastMeshtext(const ChannelDetails&, const uint8_t*, size_t) {}
  MyMesh(TestRadio& r, Clock& c, StaticPoolPacketManager& p) : BaseChatMesh(r, c, p) {}
  unsigned mesh_rx = 0, raw_logs = 0;
  DispatcherAction onRecvPacket(Packet*) override { ++mesh_rx; return ACTION_RELEASE; }
  void logRxRaw(float, float, const uint8_t*, int) override { ++raw_logs; }
@COMPANION_DECLARATIONS@
#if MESH_ENABLE_ETHERNET_TUNNEL
  bool onEthernetFrame(const uint8_t*, size_t, uint8_t, uint32_t);
#endif
#if MESH_ENABLE_FOREIGN_RADIO
  using BaseChatMesh::queueForeignRadioFrame;
  using BaseChatMesh::discardQueuedForeignRadioFrame;
  bool foreignAllowed() const { return allowForeignRadioTransmit(); }
  bool foreignReceive(const uint8_t* bytes, size_t n, uint8_t profile, uint32_t generation) {
    // Explicit lookup also makes link failures identify the missing hook,
    // instead of only the vtable. Dispatcher RX/TX below still dispatch virtually.
    return MyMesh::onForeignRadioFrame(bytes, n, profile, generation, -4, -100);
  }
  void foreignComplete(bool okay) { MyMesh::onForeignRadioSendComplete(okay); }
#endif
#if MESH_ENABLE_OTA_KISS_DIRECT
  using BaseChatMesh::queueDirectOtaRadioFrame;
#endif
};
@CALLBACK_METHODS@

struct Fixture {
  Clock clock;
  TestRadio radio;
  StaticPoolPacketManager pool{8};
  MyMesh node{radio, clock, pool};
  Fixture() { node.begin(); }
  void tick(unsigned delta = 1) { clock.now += delta; node.loop(); }
};
static void transmit(Fixture& f) {
  f.tick(); assert(f.radio.sending); f.radio.complete = true; f.tick(10);
  assert(!f.node.hasOutbound());
}
int main() {
  using Protocol = mesh::RadioProfileProtocol;
  static_assert(bool(MESH_ENABLE_FOREIGN_RADIO) == bool(
      MESH_ENABLE_MESHTEXT_CHAT || MESH_ENABLE_ETHERNET_TUNNEL || MESH_ENABLE_OTA_KISS_DIRECT));
  const uint8_t bytes[] = {0x11, 0, 0x23};
  Fixture f;
#if MESH_ENABLE_FOREIGN_RADIO
  assert(!f.node.foreignAllowed() && "Companion must retain inactive-app admission rejection");
#if !MESH_ENABLE_MESHTEXT_CHAT && !MESH_ENABLE_ETHERNET_TUNNEL
  assert(!f.node.foreignReceive(bytes, sizeof(bytes), 1, f.radio.p.generation[1]));
  f.node.foreignComplete(true); f.node.foreignComplete(false);
  assert(!f.node._mt_sent && !f.node._mt_failed && !f.node._ethernet_sent && !f.node._ethernet_failed);
#endif
#endif
#if MESH_ENABLE_OTA_KISS_DIRECT
  // The actual Mesh inherited hooks and Dispatcher direct owner bypass
  // inactive foreign apps, without falling through to foreign callbacks.
  f.node.temp = true; f.radio.p.secondary_temporary = true;
  f.radio.p.secondary.params.protocol = Protocol::MeshCore;
  const auto gen = f.radio.p.generation[1];
  const uint8_t message[] = {mesh::ota::OTA_REQ, 0xc0, 0xdb};
  assert(f.node.queueDirectOtaRadioFrame(message, sizeof(message), 1, gen));
  transmit(f);
  assert(f.node.getNumSentDirect() == 1 && !f.node._mt_sent && !f.node._ethernet_sent);
  uint8_t raw[16]; const auto n = mesh::ota::ota_direct_wire_encode(raw, sizeof(raw), message, sizeof(message));
  f.radio.active = 1; f.radio.rx.assign(raw, raw + n); f.radio.incoming = true; f.tick();
  assert(mesh::ota::context.manager.calls == 1 && mesh::ota::context.manager.enabled);
  assert(mesh::ota::context.manager.hops == 0 && mesh::ota::context.manager.route.direct);
  assert(mesh::ota::context.manager.route.profile == 1 && mesh::ota::context.manager.route.generation == gen);
  assert(!f.node.mesh_rx && !f.node.raw_logs && !f.node._mt_received && !f.node._ethernet_received);
#endif
#if MESH_ENABLE_MESHTEXT_CHAT
  f.node._mt_active = true;
  f.radio.p.secondary.params.protocol = Protocol::Meshtext;
  assert(f.node.foreignAllowed());
  assert(!f.node.foreignReceive(bytes, sizeof(bytes), 1, f.radio.p.generation[1]));
  assert(f.node.channel_queries == 1); // Real Meshtext callback reached its channel boundary.
  assert(f.node.queueForeignRadioFrame(bytes, sizeof(bytes), 1, f.radio.p.generation[1], Protocol::Meshtext));
  transmit(f); assert(f.node._mt_sent == 1 && !f.node._ethernet_sent);
  assert(f.node.queueForeignRadioFrame(bytes, sizeof(bytes), 1, f.radio.p.generation[1], Protocol::Meshtext));
  assert(f.node.discardQueuedForeignRadioFrame(Protocol::Meshtext)); assert(f.node._mt_failed == 1);
#endif
#if MESH_ENABLE_ETHERNET_TUNNEL
  f.node._ethernet_active = true; f.node._mt_active = false;
  f.radio.p.secondary.params.protocol = Protocol::Ethernet;
  assert(f.node.foreignAllowed());
  assert(f.node.foreignReceive(bytes, sizeof(bytes), 1, f.radio.p.generation[1]));
  assert(f.node._ethernet_received == 1 && f.node._ethernet_kiss.calls == 1 && !f.node._mt_received);
  assert(!f.node.foreignReceive(bytes, sizeof(bytes), 1, f.radio.p.generation[1] + 1));
  const auto mt_sent = f.node._mt_sent;
  assert(f.node.queueForeignRadioFrame(bytes, sizeof(bytes), 1, f.radio.p.generation[1], Protocol::Ethernet));
  transmit(f); assert(f.node._ethernet_sent == 1 && f.node._mt_sent == mt_sent);
  assert(f.node.queueForeignRadioFrame(bytes, sizeof(bytes), 1, f.radio.p.generation[1], Protocol::Ethernet));
  assert(f.node.discardQueuedForeignRadioFrame(Protocol::Ethernet)); assert(f.node._ethernet_failed == 1);
#endif
#if MESH_ENABLE_FOREIGN_RADIO
  f.node._scheduled_reboot_at = 1; assert(!f.node.foreignAllowed());
  f.node._scheduled_reboot_at = 0; f.node._radio_available = false; assert(!f.node.foreignAllowed());
#endif
  std::puts("PASS: Companion foreign-hook feature matrix");
}
