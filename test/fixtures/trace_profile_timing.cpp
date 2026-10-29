#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <Packet.h>
#include <RadioProfiles.h>
#include <helpers/RadioDirectionPolicy.h>
#include <helpers/CompanionDelayedReplies.h>
#include <helpers/TracePathHelpers.h>

#define ERR_EVENT_FULL 1
#define MESH_ENABLE_FOREIGN_RADIO 0
#define ACTION_RELEASE 0
#define ACTION_RETRANSMIT_DELAYED(pri, delay) ((((uint32_t)1 + (pri))<<24) | (delay))
#include "constants.inc"

class RadioLibWrapper {
public:
  mesh::RadioProfiles _profiles;
  bool validateProfile(const mesh::RadioProfileParams& p) const { return mesh::RadioProfiles::valid(p); }
  uint16_t profilePreamble(uint8_t profile) { return _profiles.preamble(profile, 32); }
  uint32_t getEstAirtimeFor(int) { return 77; }
  uint32_t getProfileAirtime(uint8_t, int, uint8_t = 0);
};
struct Call { uint8_t profile; int bytes; uint8_t cr; };
struct Radio {
  RadioLibWrapper driver;
  bool expose_profiles = true;
  uint8_t active = 0;
  unsigned legacy_calls = 0;
  std::vector<Call> calls;
  mesh::RadioProfiles* profiles() { return expose_profiles ? &driver._profiles : nullptr; }
  uint32_t getEstAirtimeFor(int length) {
    ++legacy_calls;
    return driver.getProfileAirtime(active, length);
  }
  uint32_t getProfileAirtime(uint8_t profile, int length, uint8_t cr = 0) {
    calls.push_back({profile, length, cr});
    return driver.getProfileAirtime(profile, length, cr);
  }
};
struct Clock { uint32_t now = 100; uint32_t getMillis() const { return now; } };
struct Manager {
  std::array<mesh::Packet, 4> packets;
  std::array<bool, 4> used{};
  std::vector<mesh::Packet*> queued;
  unsigned allocations = 0, releases = 0;
  mesh::Packet* allocNew() {
    ++allocations;
    for (unsigned i = 0; i != used.size(); ++i) if (!used[i]) {
      used[i] = true; return &packets[i];
    }
    return nullptr;
  }
  bool queueOutbound(mesh::Packet* packet, uint8_t, uint32_t) {
    queued.push_back(packet); return true;
  }
  int getOutboundTotal() const { return int(queued.size()); }
  mesh::Packet* getOutboundByIdx(int index) const { return queued[index]; }
  void free(mesh::Packet* packet) {
    for (unsigned i = 0; i != used.size(); ++i) if (&packets[i] == packet) {
      assert(used[i]); used[i] = false; ++releases; return;
    }
    assert(false);
  }
};
namespace mesh {
class Dispatcher {
public:
  Radio* _radio = nullptr;
  Manager manager;
  Manager* _mgr = &manager;
  Clock clock;
  Clock* _ms = &clock;
  bool radio_available = true, receive_context_active = false;
  uint8_t receive_context_profile = 0;
  uint32_t receive_context_generation = 0;
  uint32_t profile_cad_busy[2] = {};
  Packet* outbound = nullptr;
  uint16_t _err_flags = 0;
  Packet* obtainNewPacket();
  void releasePacket(Packet* packet) { _mgr->free(packet); }
  uint32_t futureMillis(uint32_t delta) const { return _ms->getMillis() + delta; }
  bool isPacketRadioCurrent(const Packet*) const { return true; }
  bool allowRadioProfileCross(const Packet*) { return true; }
  void onSendFail(Packet*) {}
  void onRadioProfileCopyQueued(Packet*, const Packet*, uint8_t) {}
  void onTracePacketQueuedForSend(Packet*) {}
  uint8_t getTransmitProfileMask(const Packet*) const;
  uint32_t getTransmitAirtime(const Packet*) const;
  uint32_t getTransmitAirtime(const Packet*, int) const;
  void retireUnusedCADWait(uint8_t);
  bool queueTransmitCandidate(Packet*, uint8_t, uint32_t);
  bool queueOutboundPacket(Packet*, uint8_t, uint32_t);
  bool sendPacket(Packet*, uint8_t, uint32_t);
};
struct Tables {
  void markSent(const Packet*) {}
  bool wasSeen(const Packet*) { return false; }
  void markSeen(const Packet*) {}
};
#include "trace_priority.inc"
class Mesh : public Dispatcher {
public:
  Tables tables;
  Tables* _tables = &tables;
  void maybeScheduleDirectRetry(Packet*, uint8_t) {}
  struct Identity {
    uint8_t key[8] = {};
    bool isHashMatch(const uint8_t* bytes, size_t length) { return memcmp(bytes, key, length) == 0; }
  } self_id;
  unsigned completed = 0;
  bool allowPacketForward(const Packet*) { return true; }
  uint32_t getDirectRetransmitDelay(const Packet*) { return 0; }
  void onTraceRecv(Packet*, uint32_t, uint32_t, uint8_t, const uint8_t*, const uint8_t*, uint8_t) { ++completed; }
  uint32_t receiveTrace(Packet*);
  Packet* createTrace(uint32_t, uint32_t, uint8_t);
  bool sendDirect(Packet*, const uint8_t*, uint8_t, uint32_t = 0);
};
}
struct Output {
  void print(const char*) {}
  template<class... Args> void printf(const char*, Args...) {}
};
struct Rng {
  unsigned calls = 0;
  void random(uint8_t* out, size_t length) {
    const uint32_t value = calls++ % 2 ? 23 : 17;
    assert(length == sizeof(value)); memcpy(out, &value, length);
  }
};
struct Serial : BaseSerialInterface {
  void enable() override {}
  void disable() override {}
  bool isEnabled() const override { return true; }
  bool isConnected() const override { return true; }
  bool isReadBusy() const override { return false; }
  bool isWriteBusy() const override { return false; }
  size_t writeFrame(const uint8_t*, size_t size) override { return size; }
  size_t checkRecvFrame(uint8_t*) override { return 0; }
};
namespace StrHelper {
static void strzcpy(char* out, const char* text, size_t capacity) { snprintf(out, capacity, "%s", text); }
}
class MyMesh : public mesh::Mesh {
public:
  mesh::CompanionDelayedReplies _delayed_replies;
  bool _terminal_trace_pending = false;
  uint8_t _terminal_trace_history = 255, _terminal_trace_hash_size = 0;
  uint32_t _terminal_trace_tag = 0, _terminal_trace_auth = 0;
  uint32_t _terminal_trace_sent_at = 0, _terminal_trace_expires_at = 0;
  char _terminal_trace_target[32] = {};
  Output output;
  Rng rng;
  Serial serial;
  Serial* _serial = &serial;
  uint8_t cmd_frame[MAX_FRAME_SIZE + 1] = {};
  unsigned error = 0;
  void serviceTerminalTrace() {}
  void serviceBinaryTraceReply() {}
  Output& terminalOutput() { return output; }
  Rng* getRNG() { return &rng; }
  void writeErrFrame(uint8_t code) { error = code; }
  uint32_t calcDirectTimeoutMillisFor(uint32_t, uint8_t) const;
  void sendTerminalTraceRoute(const uint8_t*, uint8_t, uint8_t, const char*);
  void handleTraceFrame(size_t);
};
#include "production.inc"

static void configure(Radio& radio, unsigned mode, bool swapped) {
  auto& p = radio.driver._profiles;
  p.primary = {915, 125, 32, 11, 8};
  p.secondary.params = {916, 500, 512, 7, 5};
  if (swapped) {
    const auto primary = p.primary;
    p.primary = p.secondary.params;
    p.secondary.params = primary;
  }
  p.secondary.mode = mesh::RadioProfileMode::RxTx;
  p.cross = mesh::RadioCrossMode::On;
  switch (mode) {
    case 0: p.secondary.mode = mesh::RadioProfileMode::Off; break;
    case 1: p.secondary.mode = mesh::RadioProfileMode::Rx; break;
    case 2: p.cross = mesh::RadioCrossMode::Off; break;
    case 4: p.cross = mesh::RadioCrossMode::Auto; break;
    case 5: p.cross = mesh::RadioCrossMode::Auto; p.primary_temporary = true; break;
    case 6: p.cross = mesh::RadioCrossMode::Auto; p.primary_temporary = p.secondary_temporary = true; break;
    case 7: p.secondary.params.protocol = mesh::RadioProfileProtocol::Meshtext; break;
    case 8: p.secondary.params.protocol = mesh::RadioProfileProtocol::Ethernet; break;
    case 9: radio.expose_profiles = false; p.secondary.mode = mesh::RadioProfileMode::Off; break;
    default: break;
  }
  assert(p.automaticPreambleFits());
}

static unsigned expectedMask(unsigned mode) { return mode == 3 || mode == 4 || mode == 6 ? 3 : 1; }
static unsigned verify(MyMesh& node, Radio& radio, unsigned mode,
                       const uint8_t* route, uint8_t bytes, uint8_t flags,
                       uint8_t hops, bool terminal) {
  if (node.error) fprintf(stderr, "trace admission error=%u mode=%u bytes=%u flags=%u hops=%u terminal=%u\n",
      node.error, mode, bytes, flags, hops, terminal);
  assert(node.error == 0);
  const unsigned mask = expectedMask(mode);
  unsigned queued_mask = 0;
  for (const auto* packet : node.manager.queued) {
    uint8_t wire[MAX_TRANS_UNIT];
    const unsigned length = packet->writeTo(wire);
    assert(length == unsigned(11 + bytes) && packet->getRawLength() == int(length));
    assert(packet->isRouteDirect() && packet->path_len == 0 && packet->payload_len == 9 + bytes);
    assert(packet->payload[8] == flags && memcmp(packet->payload + 9, route, bytes) == 0);
    uint32_t tag, auth; memcpy(&tag, packet->payload, 4); memcpy(&auth, packet->payload + 4, 4);
    assert(tag == 17 && auth == 23 && "trace payload must retain final tag/auth");
    queued_mask |= 1U << packet->radio_profile;
  }
  assert(queued_mask == mask && "estimated mask must match actual trace queue admission");
  uint32_t airtime = 0;
  for (unsigned profile = 0; profile != 2; ++profile) if (mask & (1U << profile))
    airtime += radio.driver.getProfileAirtime(profile, 11 + bytes);
  const uint32_t base = 500U + (6U * airtime + 250U) * ((hops & 63U) + 1U);
  const uint32_t observed = terminal ? node._terminal_trace_expires_at - node.clock.now
                                    : node._delayed_replies.trace.timeout;
  // Diagnose metadata at the wire boundary before checking the resulting timer.
  for (const auto& call : radio.calls)
    assert(call.bytes == 11 + bytes && "trace timing must use final serialized wire length");
  assert(observed == base * (terminal ? 3U : 1U)
      && "trace timeout must follow selected profiles");
  assert(radio.legacy_calls == 0 && "trace timeout must follow selected profiles");
  assert(radio.calls.size() == node.manager.queued.size());
  for (const auto& call : radio.calls) assert((mask & (1U << call.profile)) && call.cr == 0);
  return observed;
}

static unsigned producerChecks() {
  unsigned cases = 0;
  for (unsigned mode = 0; mode != 10; ++mode) for (bool swapped : {false, true})
  for (bool terminal : {false, true}) for (uint8_t hash_size : {1, 2, 4, 8})
  for (uint8_t hops : {1, 7, 20, 21, 31, 41, 43, 63}) {
    const unsigned bytes = unsigned(hash_size) * hops;
    if (bytes > MAX_PACKET_PAYLOAD - 9 || (!terminal && bytes > MAX_FRAME_SIZE - 10)
        || (terminal && hash_size == 8)) continue;
    uint8_t route[MAX_PACKET_PAYLOAD] = {};
    for (unsigned i = 0; i < bytes; ++i) route[i] = uint8_t(i + 1);
    const uint8_t flags = hash_size == 8 ? 3 : mesh::traceFlagsForHashSize(hash_size);
    uint32_t previous_timeout = 0;
    for (uint8_t active : {0, 1}) {
      if ((mode == 0 || mode == 9) && active == 1) continue;
      Radio radio; configure(radio, mode, swapped); radio.active = active;
      MyMesh node; node._radio = &radio;
      if (terminal) {
        node.sendTerminalTraceRoute(route, hash_size, hops, "synthetic");
        assert(node._terminal_trace_pending && node.rng.calls == 2);
      } else {
        node.cmd_frame[0] = CMD_SEND_TRACE_PATH;
        const uint32_t tag = 17, auth = 23;
        memcpy(node.cmd_frame + 1, &tag, 4); memcpy(node.cmd_frame + 5, &auth, 4);
        node.cmd_frame[9] = flags;
        memcpy(node.cmd_frame + 10, route, bytes);
        node.handleTraceFrame(10 + bytes);
        assert(node._delayed_replies.hasBinaryTrace() && node.rng.calls == 0);
      }
      const auto timeout = verify(node, radio, mode, route, bytes, flags, hops, terminal);
      if (active) assert(previous_timeout == timeout && "scanner visits must not change trace timeout");
      previous_timeout = timeout;
      ++cases;
    }
  }
  return cases;
}

static unsigned helperChecks() {
  unsigned cases = 0;
  for (uint8_t origin : {0, 1}) for (uint8_t policy : {mesh::RADIO_TX_AUTO, mesh::RADIO_TX_PRIMARY,
      mesh::RADIO_TX_SECONDARY, mesh::RADIO_TX_BOTH, mesh::RADIO_TX_OFF})
  for (bool reply : {false, true}) for (bool force : {false, true})
  for (uint8_t reply_policy : {mesh::RADIO_TX_BOTH, mesh::RADIO_TX_SECONDARY})
  for (uint8_t route : {ROUTE_TYPE_FLOOD, ROUTE_TYPE_DIRECT, ROUTE_TYPE_TRANSPORT_DIRECT}) {
    Radio radio; configure(radio, 1, false);
    auto& profiles = radio.driver._profiles;
    profiles.reply_tx = reply_policy; profiles.reply_force = force;
    MyMesh node; node._radio = &radio;
    auto* packet = node.obtainNewPacket(); assert(packet);
    packet->header = route | (PAYLOAD_TYPE_TXT_MSG << PH_TYPE_SHIFT);
    packet->payload_len = 57; packet->setPathHashSizeAndCount(3, 7);
    packet->transport_codes[0] = 0x1234; packet->transport_codes[1] = 0x5678;
    memset(packet->path, 0x21, sizeof(packet->path));
    memset(packet->payload, 0x43, sizeof(packet->payload));
    packet->radio_profile = origin; packet->tx_radio = policy;
    packet->radio_reply = reply; packet->radio_reply_force = force;
    packet->tx_cr = 8;
    uint8_t wire[MAX_TRANS_UNIT]; const unsigned length = packet->writeTo(wire);
    const uint8_t effective_policy = reply && policy == mesh::RADIO_TX_AUTO ? reply_policy : policy;
    unsigned mask = effective_policy == mesh::RADIO_TX_AUTO ? 1
        : effective_policy == mesh::RADIO_TX_OFF ? 0
        : effective_policy & (reply && force ? 3 : 1);
    uint32_t expected = 0, supplied = 0;
    for (unsigned profile = 0; profile != 2; ++profile) if (mask & (1U << profile)) {
      expected += radio.driver.getProfileAirtime(profile, length, 8);
      supplied += radio.driver.getProfileAirtime(profile, 123, 8);
    }
    assert(node.getTransmitProfileMask(packet) == mask);
    assert(node.getTransmitAirtime(packet) == expected && "existing one-argument airtime must remain unchanged");
    assert(node.getTransmitAirtime(packet, 123) == supplied);
    node.releasePacket(packet);
    ++cases;
  }
  return cases;
}

struct TraceWalk {
  unsigned forwarded, completed, path_length, snr_bytes, wire_length;
};

static TraceWalk forwardTrace(MyMesh& origin, uint8_t hash_size, uint8_t hops) {
  uint8_t wire[MAX_TRANS_UNIT];
  unsigned length = origin.manager.queued.front()->writeTo(wire);
  for (unsigned hop = 0; hop != hops; ++hop) {
    MyMesh receiver; receiver._radio = origin._radio;
    auto* packet = receiver.obtainNewPacket(); assert(packet);
    assert(packet->readFrom(wire, length));
    memcpy(receiver.self_id.key, packet->payload + 9 + hop * hash_size, hash_size);
    packet->_snr = 16;
    const uint32_t action = receiver.receiveTrace(packet);
    assert(action != ACTION_RELEASE && receiver.completed == 0);
    assert(packet->path_len == hop + 1);
    assert(receiver.queueOutboundPacket(packet, (action >> 24) - 1, action & 0x00ffffff));
    length = packet->writeTo(wire);
    assert(length <= MAX_TRANS_UNIT);
    receiver.releasePacket(packet);
  }
  MyMesh endpoint; endpoint._radio = origin._radio;
  auto* packet = endpoint.obtainNewPacket(); assert(packet);
  assert(packet->readFrom(wire, length));
  assert(endpoint.receiveTrace(packet) == ACTION_RELEASE);
  TraceWalk result{hops, endpoint.completed, packet->path_len,
                   packet->getPathByteLen(), length};
  endpoint.releasePacket(packet);
  return result;
}

static void hopBoundaryChecks(unsigned selected_hash = 0) {
  for (uint8_t hash_size : {1, 2}) {
    if (selected_hash && selected_hash != hash_size) continue;
    for (uint8_t hops : {63, 64}) {
      Radio radio; configure(radio, 2, false);
      MyMesh node; node._radio = &radio;
      node.cmd_frame[0] = CMD_SEND_TRACE_PATH;
      const uint32_t tag = 17, auth = 23;
      memcpy(node.cmd_frame + 1, &tag, 4); memcpy(node.cmd_frame + 5, &auth, 4);
      node.cmd_frame[9] = hash_size - 1;
      for (unsigned hop = 0; hop != hops; ++hop) {
        node.cmd_frame[10 + hop * hash_size] = hop + 1;
        if (hash_size == 2) node.cmd_frame[11 + hop * hash_size] = 0x99;
      }
      const size_t host_length = 10 + hash_size * hops;
      assert(host_length <= MAX_FRAME_SIZE);
      node.handleTraceFrame(host_length);
      if (hops == 63) {
        assert(node.error == 0 && node._delayed_replies.hasBinaryTrace());
        assert(node.manager.allocations == 1 && node.manager.queued.size() == 1);
        const auto walked = forwardTrace(node, hash_size, hops);
        assert(walked.forwarded == 63 && walked.completed == 1);
        assert(walked.path_length == 63 && walked.snr_bytes == 63);
      } else if (node.error != ERR_CODE_ILLEGAL_ARG) {
        // Exact old admission exposes two independent protocol limits: a raw
        // path_len of 64 serializes as packed hash-size 2/count 0, and TRACE's
        // completion callback is guarded by path_len < MAX_PATH_SIZE.
        assert(node.error == 0 && node._delayed_replies.hasBinaryTrace());
        assert(node.manager.allocations == 1 && node.manager.queued.size() == 1);
        const auto walked = forwardTrace(node, hash_size, hops);
        assert(walked.forwarded == 64 && walked.completed == 0);
        assert(walked.path_length == 64 && walked.snr_bytes == 0);
        const uint32_t airtime = radio.driver.getProfileAirtime(0, 11 + hash_size * hops);
        const uint32_t wrapped = 500U + (6U * airtime + 250U);
        const uint32_t unwrapped = 500U + (6U * airtime + 250U) * 65U;
        assert(node._delayed_replies.trace.timeout == wrapped && wrapped < unwrapped);
        fprintf(stderr, "old 64-hop admission hash=%u host=%zu forwarded=%u completed=%u snr_bytes=%u timeout=%u unwrapped=%u\n",
            hash_size, host_length, walked.forwarded, walked.completed, walked.snr_bytes, wrapped, unwrapped);
        assert(false && "64-hop trace must be rejected: admitted route cannot complete and timeout count wraps");
      } else {
        assert(node.manager.allocations == 0 && node.manager.releases == 0);
        assert(node.manager.queued.empty() && node.rng.calls == 0);
        assert(!node._delayed_replies.hasBinaryTrace() && !node._terminal_trace_pending);
        assert(node._delayed_replies.trace.phase == mesh::CompanionDelayedReplies::Empty);
        for (unsigned i = 0; i != mesh::CompanionDelayedReplies::HISTORY_SIZE; ++i)
          assert(node._delayed_replies.reserveTrace(tag + i, auth, node.clock.now)
              != mesh::CompanionDelayedReplies::NO_HISTORY);
      }
    }
  }
  puts("PASS: 63-hop traces complete and 64-hop commands have no side effects");
}

int main(int argc, char** argv) {
  if (argc == 2 && strncmp(argv[1], "hop-boundary", 12) == 0) {
    hopBoundaryChecks(strlen(argv[1]) == 14 ? argv[1][13] - '0' : 0);
    return 0;
  }
  const unsigned producers = producerChecks(), helpers = helperChecks();
  printf("PASS: trace timing %u producer cases, %u existing/helper policy cases\n", producers, helpers);
}
