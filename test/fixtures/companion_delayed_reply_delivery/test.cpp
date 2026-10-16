#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <limits>
#include <string>
#include <type_traits>
#include <vector>
#include <helpers/ArduinoSerialInterface.h>
#include <helpers/MultiSerialInterface.h>
#include <helpers/wifi/SerialWifiInterface.h>
#include <helpers/CompanionDelayedReplies.h>
#include <helpers/CompanionStatusResponse.h>
#include <helpers/TracePathHelpers.h>
#include <helpers/TxtDataHelpers.h>

#define MESH_DEBUG_PRINTLN(...) ((void)0)
#include "production_constants.inc"
#include "production_stream.inc"
std::deque<WiFiClient> WiFiServer::incoming;

using Tracker = mesh::CompanionDelayedReplies;
static_assert(std::is_nothrow_default_constructible<Tracker>::value,
              "The fixed reply tracker constructor must not add exception cleanup");
using Kind = Tracker::Kind;
using Bytes = std::vector<uint8_t>;
struct Identity { uint8_t pub_key[32] = {1, 2, 3, 4, 5, 6}; };
struct ContactInfo {
  Identity id;
  uint8_t type = ADV_TYPE_ROOM, out_path_len = 1;
  uint32_t sync_since = 0, lastmod = 0;
  char name[32] = "synthetic peer";
  uint8_t path[64] = {0x12};
  const uint8_t* getPath() const { return path; }
  bool copyPathTo(uint8_t* target) const { memcpy(target,path,sizeof(path)); return true; }
  const uint8_t* getSharedSecret(const Identity&) const {
    static const uint8_t synthetic[32] = {};
    return synthetic;
  }
  bool setPath(const uint8_t*, uint8_t) { return true; }
};
struct RtcBoundary {
  uint32_t wall = 1000, last_unique = 0;
  uint32_t getCurrentTime() const { return wall; }
#include "production_clock.inc"
};
static uint32_t clock_step = 0;
struct Clock { uint32_t getMillis() const { uint32_t now=millis();g_mock_millis+=clock_step;return now; } };
struct RngBoundary {
  unsigned calls = 0;
  void random(uint8_t* bytes, size_t len) {
    const uint32_t value = (calls++ & 1) ? 23 : 17;
    assert(len <= sizeof(value));
    memcpy(bytes, &value, len);
  }
};
struct RadioBoundary { uint32_t getEstAirtimeFor(size_t) const { return 100; } };
namespace mesh {
struct Packet {
  uint8_t payload[256] = {}, payload_len = 0, path_len = 0;
  float getSNR() const { return 0; }
  static bool isValidPathLen(uint8_t);
  static size_t writePath(uint8_t*, const uint8_t*, uint8_t);
};
struct Utils {
  static void printHex(Stream& stream, const uint8_t*, size_t) { stream.print("00"); }
};
}

struct BaseChatMesh {
  Clock clock;
  Clock* _ms = &clock;
  RtcBoundary rtc;
  RngBoundary rng;
  RadioBoundary radio;
  RadioBoundary* _radio = &radio;
  Identity self_id;
  mesh::Packet packet;
  Bytes transmitted_payload;
  unsigned allocations = 0, transmissions = 0, connection_starts = 0;
  uint32_t txt_send_timeout = 0;
  uint32_t direct_timeout = 5000;
  bool radio_accepts = true;
  RtcBoundary* getRTCClock() { return &rtc; }
  RngBoundary* getRNG() { return &rng; }
  virtual bool allowRequestTag(uint32_t) { return true; }
  virtual bool allocateRequestTag(uint32_t&);
  virtual void onContactResponse(const ContactInfo&, const uint8_t*, uint8_t) = 0;
  bool canMutateContacts() const { return false; }
  void onContactPathUpdated(ContactInfo&) {}
  bool processAck(const uint8_t*, ContactInfo*&) { return false; }
  bool onContactPathRecv(ContactInfo&, uint8_t*, uint8_t, uint8_t*, uint8_t,
                         uint8_t, uint8_t*, uint8_t);
  mesh::Packet* allocate() { ++allocations; return &packet; }
  mesh::Packet* createDatagram(uint8_t, const Identity&, const uint8_t*, const uint8_t* data, size_t len) {
    transmitted_payload.assign(data, data + len);
    return allocate();
  }
  mesh::Packet* createAnonDatagram(uint8_t, const Identity&, const Identity&,
                                  const uint8_t*, const uint8_t*, size_t) { return allocate(); }
  mesh::Packet* createTrace(uint32_t, uint32_t, uint8_t) { return allocate(); }
  bool sendDirect(mesh::Packet*, const uint8_t*, uint8_t) { ++transmissions; return radio_accepts; }
  bool sendFloodScoped(const ContactInfo&, mesh::Packet*) { ++transmissions; return radio_accepts; }
  uint32_t getTransmitAirtime(mesh::Packet*) const { return 100; }
  uint32_t calcFloodTimeoutMillisFor(uint32_t) const { return 5000; }
  uint32_t calcDirectTimeoutMillisFor(uint32_t, uint8_t) const { return direct_timeout; }
  void startConnection(const ContactInfo&, uint16_t) { ++connection_starts; }
  int sendContactPacket(const ContactInfo&, mesh::Packet*, uint32_t&);
  int sendLogin(const ContactInfo&, const char*, uint32_t&);
  int sendAnonReq(const ContactInfo&, const uint8_t*, uint8_t, uint32_t&, uint32_t&);
  int sendRequest(const ContactInfo&, const uint8_t*, uint8_t, uint32_t&, uint32_t&);
  int sendRequest(const ContactInfo&, uint8_t, uint32_t&, uint32_t&);
};

struct MyMesh : BaseChatMesh {
  BaseSerialInterface* _serial = nullptr;
  Tracker _delayed_replies;
  bool _request_tag_rejected = false;
  uint8_t before[16] = {}, out_frame[MAX_FRAME_SIZE + 1] = {}, after[16] = {};
  uint8_t cmd_frame[MAX_FRAME_SIZE + 1] = {};
  ContactInfo recipient;
  BaseSerialInterface *private_key_backup_route = nullptr, *command_radio_reply_route = nullptr;
  BaseSerialInterface* sign_data_reply_route = nullptr;
  uint32_t private_key_backup_deadline = 0;
  char private_key_backup_nonce[17] = {};
  uint8_t private_key_backup_sender[6] = {};
  bool command_radio_apply_pending = false;
  struct AckBoundary { uint32_t ack = 0; BaseSerialInterface* reply_route = nullptr; };
  AckBoundary expected_ack_table[EXPECTED_ACK_TABLE_SIZE];
  BufferStream terminal_stream;
  bool _terminal_login_pending = false, _terminal_trace_pending = false;
  uint8_t _terminal_login_key[32] = {}, _terminal_trace_hash_size = 0;
  uint8_t _terminal_trace_history = Tracker::NO_HISTORY;
  uint32_t _terminal_login_expires_at = 0, _terminal_trace_tag = 0, _terminal_trace_auth = 0;
  uint32_t _terminal_trace_sent_at = 0, _terminal_trace_expires_at = 0;
  char _terminal_login_target[32] = "synthetic", _terminal_trace_target[32] = "synthetic";
  MyMesh() {
    memset(before, 0xa5, sizeof(before)); memset(after, 0xa5, sizeof(after));
    memset(out_frame, 0x5a, sizeof(out_frame)); terminal_stream.write_capacity = 4096;
  }
  void checkGuards() const {
    for (auto byte : before) assert(byte == 0xa5);
    for (auto byte : after) assert(byte == 0xa5);
    assert(out_frame[MAX_FRAME_SIZE] == 0x5a);
  }
  ContactInfo* lookupContactByPubKey(const uint8_t* key, size_t len) {
    return len == 32 && memcmp(key, recipient.id.pub_key, 32) == 0 ? &recipient : nullptr;
  }
  bool addContact(const ContactInfo&) { return false; }
  bool hasTerminalOutput() const { return true; }
  Stream& terminalOutput() { return terminal_stream; }
  void cancelPendingRadioParamApply() { command_radio_reply_route = nullptr; }
  void cancelSigningSession() { sign_data_reply_route = nullptr; }
  void expireExpectedAcks() {}
  bool millisHasNowPassed(unsigned long) const;
  unsigned long futureMillis(int) const;
  void writeErrFrame(uint8_t, BaseSerialInterface* = nullptr);
  size_t writePendingSerialFrame(const uint8_t*, size_t, uint32_t);
  bool beginPendingRequest(Kind, const ContactInfo&, bool = false);
  bool allowRequestTag(uint32_t) override;
  bool allocateRequestTag(uint32_t&) override;
  void armPendingRequest(uint32_t, uint32_t, bool);
  void finishPendingRequest(int, uint32_t, uint32_t);
  void abandonPendingRequest();
  void clearPendingReqs();
  bool hasPendingReqs() const;
  void servicePendingSerialReply();
  void servicePendingSerialReply(uint32_t);
  void clearBinaryTraceReply();
  void serviceBinaryTraceReply();
  void serviceBinaryTraceReply(uint32_t);
  void cancelSerialOperationsForRoute(BaseSerialInterface*);
  bool hasFiniteDelayedReplyForRoute(BaseSerialInterface*) const;
  void onContactResponse(const ContactInfo&, const uint8_t*, uint8_t) override;
  bool onContactPathRecv(ContactInfo&, uint8_t*, uint8_t, uint8_t*, uint8_t,
                         uint8_t, uint8_t*, uint8_t);
  void onTraceRecv(mesh::Packet*, uint32_t, uint32_t, uint8_t,
                   const uint8_t*, const uint8_t*, uint8_t);
  void handleRequestFrame(size_t);
  void clearTerminalLogin();
  void clearTerminalLogin(uint32_t);
  void serviceTerminalLogin();
  void serviceTerminalLogin(uint32_t);
  void sendTerminalLogin(ContactInfo&,const char*);
  void printTerminalSendStatus(const char*,const ContactInfo&,int,uint32_t);
  void clearTerminalTrace();
  void clearTerminalTrace(uint32_t);
  void serviceTerminalTrace();
  void serviceTerminalTrace(uint32_t);
  void sendTerminalTraceRoute(const uint8_t*, uint8_t, uint8_t, const char*);
};
#include "production_replies.inc"

static bool usb_connected = true, other_connected = true;
static bool usbConnected() { return usb_connected; }
static bool otherConnected() { return other_connected; }
struct Fixture {
  BufferStream usb_stream, other_stream;
  ArduinoSerialInterface usb, other;
  SerialWifiInterface tcp;
  std::shared_ptr<MockSocket> socket;
  MultiSerialInterface manager;
  BaseSerialInterface* owner = nullptr;
  MyMesh mesh;
  explicit Fixture(bool tcp_owner = false) {
    g_mock_millis = 100; clock_step=0; usb_connected = other_connected = true;
    WiFiServer::incoming.clear();
    usb_stream.write_capacity = other_stream.write_capacity = 0;
    usb.begin(usb_stream); usb.enableFlowControl(true); usb.setConnectedCheck(usbConnected);
    other.begin(other_stream); other.enableFlowControl(true); other.setConnectedCheck(otherConnected);
    tcp.begin(5000);
    assert(manager.addInterface(InterfaceType::USB, &usb));
    assert(manager.addInterface(InterfaceType::Bluetooth, &other));
    assert(manager.addInterface(InterfaceType::WiFi, &tcp));
    manager.enable(); mesh._serial = &manager;
    if (tcp_owner) {
      socket = std::make_shared<MockSocket>(IPAddress(192, 168, 1, 20));
      socket->received = {'<', 1, 0, 1}; WiFiServer::incoming.push_back(WiFiClient(socket)); owner = &tcp;
      uint8_t command[MAX_FRAME_SIZE]; assert(manager.checkRecvFrame(command) == 1);
    } else { select(usb_stream, &usb); owner = &usb; }
    assert(manager.captureReplyRoute() == owner);
  }
  ~Fixture() { tcp.end(); }
  void select(BufferStream& stream, BaseSerialInterface* expected) {
    const uint8_t request[] = {'<', 1, 0, 1}; stream.push(request, sizeof(request));
    uint8_t command[MAX_FRAME_SIZE]; assert(manager.checkRecvFrame(command) == 1);
    assert(manager.captureReplyRoute() == expected);
  }
  void otherRequest() { select(other_stream, &other); }
  unsigned fill(BaseSerialInterface* target = nullptr) {
    if (!target) target = owner;
    const uint8_t required[] = {RESP_CODE_OK, 0x55}; unsigned count = 0;
    while (target->writeFrame(required, sizeof(required)) == sizeof(required)) { assert(++count <= 4); }
    return count;
  }
  void service() { mesh.servicePendingSerialReply(); mesh.serviceBinaryTraceReply(); }
  void drain() {
    usb_stream.write_capacity = other_stream.write_capacity = 4096;
    if (socket) socket->write_limit = 4096;
    uint8_t command[MAX_FRAME_SIZE];
    for (unsigned i = 0; i != 20; ++i) { manager.loop(); manager.checkRecvFrame(command); service(); }
    mesh.checkGuards();
  }
  void disconnect() { if (socket) socket->connected = false; else usb_connected = false; manager.loop(); }
  const Bytes& wire() const { return socket ? socket->sent : usb_stream.output; }
  void arm(Kind kind, uint32_t tag = 17, uint32_t timeout = 5000) {
    if (kind == Tracker::Trace) {
      assert(mesh._delayed_replies.reserveBinaryTrace(tag, 23, owner, millis()));
      mesh._delayed_replies.armBinaryTrace(timeout, millis()); mesh.serviceBinaryTraceReply();
    } else {
      assert(mesh.beginPendingRequest(kind, mesh.recipient));
      assert(kind == Tracker::Login || mesh.allowRequestTag(tag));
      mesh.armPendingRequest(tag, timeout, false);
    }
  }
  void command(Kind kind, bool anonymous = false, uint32_t trace_tag = 17) {
    memset(mesh.cmd_frame, 0, sizeof(mesh.cmd_frame));
    unsigned offset = 1, len = 33;
    switch (kind) {
      case Tracker::Login: mesh.cmd_frame[0] = CMD_SEND_LOGIN; len = 34; mesh.cmd_frame[33] = 'x'; break;
      case Tracker::Status: mesh.cmd_frame[0] = CMD_SEND_STATUS_REQ; break;
      case Tracker::Telemetry: mesh.cmd_frame[0] = CMD_SEND_TELEMETRY_REQ; offset = 4; len = 36; break;
      case Tracker::Discovery: mesh.cmd_frame[0] = CMD_SEND_PATH_DISCOVERY_REQ; offset = 2; len = 34; break;
      case Tracker::Binary: mesh.cmd_frame[0] = anonymous ? CMD_SEND_ANON_REQ : CMD_SEND_BINARY_REQ; len = 34; break;
      case Tracker::Trace: {
        mesh.cmd_frame[0] = CMD_SEND_TRACE_PATH; const uint32_t tag = trace_tag, auth = 23;
        memcpy(mesh.cmd_frame + 1, &tag, 4); memcpy(mesh.cmd_frame + 5, &auth, 4);
        mesh.cmd_frame[10] = 0x12; mesh.handleRequestFrame(11); return;
      }
      default: assert(false);
    }
    memcpy(mesh.cmd_frame + offset, mesh.recipient.id.pub_key, 32);
    mesh.handleRequestFrame(len);
  }
  void aclCommand() {
    memset(mesh.cmd_frame, 0, sizeof(mesh.cmd_frame));
    mesh.cmd_frame[0] = CMD_SEND_BINARY_REQ;
    memcpy(mesh.cmd_frame + 1, mesh.recipient.id.pub_key, 32);
    const uint8_t request[] = {5, 0, 0, 0x12, 0x34, 0x56, 0x78};
    memcpy(mesh.cmd_frame + 33, request, sizeof(request));
    mesh.handleRequestFrame(33 + sizeof(request));
  }
};

static const Kind kinds[] = {Tracker::Login, Tracker::Status, Tracker::Telemetry,
                             Tracker::Binary, Tracker::Discovery, Tracker::Trace};
static uint8_t code(Kind kind) {
  switch (kind) {
    case Tracker::Login: return PUSH_CODE_LOGIN_SUCCESS;
    case Tracker::Status: return PUSH_CODE_STATUS_RESPONSE;
    case Tracker::Telemetry: return PUSH_CODE_TELEMETRY_RESPONSE;
    case Tracker::Binary: return PUSH_CODE_BINARY_RESPONSE;
    case Tracker::Discovery: return PUSH_CODE_PATH_DISCOVERY_RESPONSE;
    case Tracker::Trace: return PUSH_CODE_TRACE_DATA;
    default: assert(false); return 0;
  }
}
static std::vector<Bytes> frames(const Bytes& wire) {
  std::vector<Bytes> result; size_t at = 0;
  while (at < wire.size()) {
    assert(at + 3 <= wire.size() && wire[at] == '>');
    size_t len = wire[at + 1] + (size_t(wire[at + 2]) << 8); at += 3;
    assert(len && at + len <= wire.size()); result.emplace_back(wire.begin() + at, wire.begin() + at + len); at += len;
  }
  return result;
}
static unsigned count(const Bytes& wire, uint8_t expected) {
  unsigned n = 0; for (const auto& frame : frames(wire)) n += frame[0] == expected; return n;
}
static Bytes frameWith(const Bytes& wire, uint8_t expected) {
  for (const auto& frame : frames(wire)) if (frame[0] == expected) return frame;
  return {};
}
static void reply(Fixture& fixture, Kind kind, const ContactInfo* from = nullptr,
                   uint32_t tag = 17, uint8_t marker = 0x65, unsigned length = 64) {
  const ContactInfo& contact = from ? *from : fixture.mesh.recipient;
  uint8_t data[256] = {}; memcpy(data, &tag, 4); data[4] = marker;
  if (kind == Tracker::Trace) {
    uint8_t path[] = {marker}; mesh::Packet packet;
    fixture.mesh.onTraceRecv(&packet, tag, 23, 0, path, path, 1);
  } else if (kind == Tracker::Discovery) {
    uint8_t path[] = {marker}; ContactInfo mutable_contact = contact;
    fixture.mesh.onContactPathRecv(mutable_contact, path, 1, path, 1,
                                  PAYLOAD_TYPE_RESPONSE, data, 5);
  } else {
    if (kind == Tracker::Login) { data[4] = RESP_SERVER_LOGIN_OK; data[5] = 1; data[6] = 1;
      data[7] = marker; data[12] = 13; length = 13; }
    fixture.mesh.onContactResponse(contact, data, uint8_t(length));
  }
  fixture.mesh.checkGuards();
}
static const Tracker::Reply& slot(const Fixture& f, Kind kind) {
  return kind == Tracker::Trace ? f.mesh._delayed_replies.trace : f.mesh._delayed_replies.request;
}
static void assertSentBeforeFinal(const Bytes& wire, Kind kind, uint32_t tag = 17) {
  bool sent = false; unsigned results = 0;
  for (const auto& frame : frames(wire)) {
    if (frame[0] == RESP_CODE_SENT) {
      assert(!sent && frame.size() == 10); uint32_t actual; memcpy(&actual, frame.data() + 2, 4);
      assert(actual == tag); sent = true;
    }
    if (frame[0] == code(kind)) { assert(sent); ++results; }
  }
  assert(sent && results == 1);
}

static void serviceIdleHistory(Tracker& tracker, bool request_service, uint32_t now) {
  if (request_service) tracker.serviceRequest(nullptr, now);
  else tracker.serviceBinaryTrace(nullptr, now);
  assert(tracker.request.phase == Tracker::Empty && tracker.trace.phase == Tracker::Empty);
}

static void testRetiredHistoryPruning(bool request_service, uint32_t start,
                                     unsigned days, bool daily_service) {
  constexpr uint32_t day_ms = 24U * 60U * 60U * 1000U;
  const uint8_t login_peer[32] = {1}, status_peer[32] = {2};
  Tracker tracker;
  assert(tracker.reserveRequest(Tracker::Login, login_peer, nullptr, true, start));
  tracker.rememberLoginTag(101);
  tracker.armRequest(101, 5000, false, start);
  tracker.retireRequest(start);
  assert(tracker.reserveRequest(Tracker::Status, status_peer, nullptr, true, start));
  assert(tracker.allowRequestTag(17));
  tracker.armRequest(17, 5000, false, start);
  tracker.retireRequest(start);
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i) {
    const uint8_t history = tracker.reserveTrace(i + 1, 23, start);
    assert(history != Tracker::NO_HISTORY);
    tracker.retireTrace(history, start, start + 6000U);
  }

  // Both Reply slots are empty. Servicing only one slot must still prune both
  // histories at the first exact expiry, rather than waiting for a new send.
  serviceIdleHistory(tracker, request_service, start + 15999U);
  assert(tracker.blocksResponse(login_peer, 999, start + 15999U));
  assert(tracker.blocksResponse(status_peer, 17, start + 15999U));
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i)
    assert(tracker.blocksTrace(i + 1, 23, start + 15999U));
  serviceIdleHistory(tracker, request_service, start + 16000U);

  // Checking a future time is non-mutating. It distinguishes physical pruning
  // from merely ignoring an expired guard until the signed range flips again.
  const uint32_t far_after_trace_expiry = start + 16000U + 0x80000000U;
  assert(!tracker.blocksResponse(status_peer, 17, far_after_trace_expiry));
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i)
    assert(!tracker.blocksTrace(i + 1, 23, far_after_trace_expiry));
  serviceIdleHistory(tracker, request_service, start + 29999U);
  assert(tracker.blocksResponse(login_peer, 999, start + 29999U));
  serviceIdleHistory(tracker, request_service, start + Tracker::LOGIN_QUARANTINE_MS);

  if (daily_service) {
    for (unsigned day = 1; day <= days; ++day)
      serviceIdleHistory(tracker, request_service, start + day * day_ms);
  }
  const uint32_t later = start + days * day_ms;
  assert(days * day_ms > 0x80000000U);
  assert(!tracker.blocksResponse(login_peer, 999, later));
  assert(!tracker.blocksResponse(status_peer, 17, later));
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i)
    assert(!tracker.blocksTrace(i + 1, 23, later));

  // No reservation occurs before the checks above: reserve's own prune cannot
  // conceal a missing idle-service prune. All old identities/tuples are reusable.
  assert(tracker.reserveRequest(Tracker::Login, login_peer, nullptr, true, later));
  tracker.abandonRequest();
  assert(tracker.reserveRequest(Tracker::Status, status_peer, nullptr, true, later));
  assert(tracker.allowRequestTag(17));
  tracker.abandonRequest();
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i)
    assert(tracker.reserveTrace(i + 1, 23, later) != Tracker::NO_HISTORY);
  assert(tracker.reserveTrace(99, 23, later) == Tracker::NO_HISTORY);
}

static void testReservedHistorySurvivesIdle(bool request_service, uint32_t start,
                                          Kind kind) {
  constexpr uint32_t day_ms = 24U * 60U * 60U * 1000U;
  const uint8_t peer[32] = {3};
  Tracker tracker;
  assert(tracker.reserveRequest(kind, peer, nullptr, true, start));
  if (kind == Tracker::Login) tracker.rememberLoginTag(17);
  else assert(tracker.allowRequestTag(17));
  const uint8_t request_history = tracker.request.history;
  uint8_t trace_history[Tracker::HISTORY_SIZE];
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i) {
    trace_history[i] = tracker.reserveTrace(i + 1, 23, start);
    assert(trace_history[i] != Tracker::NO_HISTORY);
  }
  const auto service = [&](uint32_t now) {
    if (request_service) tracker.serviceRequest(nullptr, now);
    else tracker.serviceBinaryTrace(nullptr, now);
    assert(tracker.request.phase == Tracker::Reserved);
    assert(tracker.request.history == request_history && tracker.request.kind == kind);
    assert(tracker.requestPeer() != nullptr && memcmp(tracker.requestPeer(), peer, 32) == 0);
    assert(tracker.blocksResponse(peer, 17, now));
    assert(tracker.trace.phase == Tracker::Empty);
  };
  service(start + 16000U);
  service(start + Tracker::LOGIN_QUARANTINE_MS);
  for (unsigned day = 1; day <= 40; ++day) service(start + day * day_ms);
  const uint32_t later = start + 40U * day_ms;
  // Reserved entries have no expiry authority, even if stale fields look due.
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i)
    assert(tracker.reserveTrace(i + 1, 23, later) == Tracker::NO_HISTORY);
  assert(tracker.reserveTrace(99, 23, later) == Tracker::NO_HISTORY);
  assert(!tracker.reserveRequest(kind, peer, nullptr, true, later));
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i) tracker.abandonTrace(trace_history[i]);
  tracker.abandonRequest();
  assert(tracker.reserveRequest(kind, peer, nullptr, true, later));
  tracker.abandonRequest();
  for (unsigned i = 0; i < Tracker::HISTORY_SIZE; ++i)
    assert(tracker.reserveTrace(i + 1, 23, later) != Tracker::NO_HISTORY);
}

int main() {
  unsigned checks = 0;
  // Exercise the real CMD50 producer and callback with a legacy one-admin ACL
  // reply after the old estimate + 20% cutoff but inside the app's +5s window.
  for (bool tcp : {false, true}) for (uint32_t start : {100U, 0xfffffff0U}) {
    Fixture f(tcp); g_mock_millis = start; f.mesh.direct_timeout = 2000;
    f.aclCommand(); const uint32_t tag = slot(f, Tracker::Binary).tag;
    assert(!slot(f, Tracker::Binary).sent_pending);
    assert(slot(f, Tracker::Binary).radio_deadline == start + 7000U);
    f.otherRequest(); g_mock_millis = start + 3000U;
    uint8_t acl[11]; memcpy(acl, &tag, 4);
    memcpy(acl + 4, f.mesh.recipient.id.pub_key, 6); acl[10] = 3;
    f.mesh.onContactResponse(f.mesh.recipient, acl, sizeof(acl)); f.drain();
    assertSentBeforeFinal(f.wire(), Tracker::Binary, tag);
    Bytes expected = {PUSH_CODE_BINARY_RESPONSE, 0};
    expected.insert(expected.end(), acl, acl + sizeof(acl));
    assert(frameWith(f.wire(), PUSH_CODE_BINARY_RESPONSE) == expected);
    assert(f.other_stream.output.empty()); ++checks;
  }
  // Every nonterminal kind shares the host budget. A pending SENT is bounded
  // for 10s, then successful admission starts the full host window exactly once.
  for (bool tcp : {false, true}) for (Kind kind : kinds)
      for (uint32_t start : {100U, 0xfffffff0U}) {
    Fixture f(tcp); g_mock_millis = start; assert(f.fill() == 4);
    f.arm(kind, 17, 2000);
    assert(slot(f, kind).sent_pending);
    assert(slot(f, kind).radio_deadline == start + Tracker::DELIVERY_GRACE_MS);
    g_mock_millis = start + 9000U; f.service();
    assert(slot(f, kind).phase == Tracker::AwaitRadio && slot(f, kind).sent_pending);
    assert(f.mesh.hasFiniteDelayedReplyForRoute(f.owner));
    f.drain(); assert(!slot(f, kind).sent_pending);
    const uint32_t admitted_deadline = start + 9000U + 7000U;
    assert(slot(f, kind).radio_deadline == admitted_deadline);
    g_mock_millis = start + 12000U; f.service();
    assert(slot(f, kind).radio_deadline == admitted_deadline);
    reply(f, kind); f.drain(); assertSentBeforeFinal(f.wire(), kind); ++checks;
  }
  // Radio callbacks may arrive before backpressured SENT admission. Retain the
  // response without extending the existing pre-SENT/delivery grace.
  for (bool tcp : {false, true}) for (Kind kind : kinds) {
    Fixture f(tcp); assert(f.fill() == 4); f.arm(kind, 17, 2000);
    const uint32_t pre_sent_deadline = slot(f, kind).sent_deadline;
    g_mock_millis += 9000U; reply(f, kind);
    assert(slot(f, kind).phase == Tracker::AwaitAdmission);
    assert(slot(f, kind).delivery_deadline == pre_sent_deadline);
    f.drain(); assertSentBeforeFinal(f.wire(), kind); ++checks;
  }
  // Independent wide arithmetic checks saturation and both exact boundaries.
  const uint32_t ceiling = 0x7fffffffU - Tracker::DELIVERY_GRACE_MS;
  for (uint32_t start : {100U, 0xfffffff0U})
      for (uint32_t timeout : {0U, 1U, 2000U, 25000U, 30000U, ceiling, 0xffffffffU}) {
    const uint64_t extra = uint64_t(timeout) + timeout / 5U;
    const uint64_t host = uint64_t(timeout) + 5000U;
    const uint32_t budget = uint32_t(std::min<uint64_t>(ceiling, std::max(extra, host)));
    Fixture f; g_mock_millis = start; f.arm(Tracker::Binary, 17, timeout);
    assert(slot(f, Tracker::Binary).radio_deadline == start + budget);
    g_mock_millis = start + budget - 1U; f.service();
    assert(slot(f, Tracker::Binary).phase == Tracker::AwaitRadio);
    g_mock_millis += 1; reply(f, Tracker::Binary); f.drain();
    assert(slot(f, Tracker::Binary).phase == Tracker::Empty);
    assert(count(f.wire(), PUSH_CODE_BINARY_RESPONSE) == 0); ++checks;
    Fixture blocked; g_mock_millis = start; assert(blocked.fill() == 4);
    blocked.arm(Tracker::Binary, 17, timeout);
    g_mock_millis = start + Tracker::DELIVERY_GRACE_MS - 1U; blocked.service();
    assert(slot(blocked, Tracker::Binary).phase == Tracker::AwaitRadio);
    g_mock_millis += 1; reply(blocked, Tracker::Binary); blocked.drain();
    assert(count(blocked.wire(), RESP_CODE_SENT) == 0);
    assert(count(blocked.wire(), PUSH_CODE_BINARY_RESPONSE) == 0); ++checks;
  }
  for (bool tcp : {false, true}) for (Kind kind : kinds) {
    Fixture f(tcp); f.arm(kind); assert(f.fill() == 3); f.otherRequest();
    reply(f, kind); const auto& held = slot(f, kind);
    assert(held.phase == Tracker::AwaitAdmission && held.route == f.owner && held.frame_len > 0);
    const Bytes original(held.frame, held.frame + held.frame_len); const uint32_t deadline = held.delivery_deadline;
    assert(f.mesh.hasFiniteDelayedReplyForRoute(f.owner));
    g_mock_millis += 10; reply(f, kind, nullptr, 17, 0x77);
    assert(slot(f, kind).delivery_deadline == deadline);
    assert(Bytes(slot(f, kind).frame, slot(f, kind).frame + slot(f, kind).frame_len) == original);
    assert(f.mesh.connection_starts == (kind == Tracker::Login ? 1U : 0U));
    f.drain(); assertSentBeforeFinal(f.wire(), kind); assert(frameWith(f.wire(), code(kind)) == original);
    assert(f.other_stream.output.empty() && slot(f, kind).phase == Tracker::Empty); ++checks;
  }
  for (bool tcp : {false, true}) for (Kind kind : kinds) {
    Fixture f(tcp); assert(f.fill() == 4); f.arm(kind); assert(slot(f, kind).sent_pending);
    const uint32_t first_deadline = slot(f, kind).sent_deadline;
    g_mock_millis += 1000; f.otherRequest(); reply(f, kind);
    assert(slot(f, kind).delivery_deadline == first_deadline);
    f.drain(); assertSentBeforeFinal(f.wire(), kind); assert(f.other_stream.output.empty()); ++checks;
  }
  for (bool tcp : {false, true}) for (Kind kind : kinds) for (unsigned reason = 0; reason < 4; ++reason) {
    Fixture f(tcp); f.arm(kind);
    if (reason == 0) f.mesh.cancelSerialOperationsForRoute(f.owner);
    else if (reason == 1) { f.disconnect(); f.service(); }
    else { g_mock_millis = slot(f, kind).radio_deadline + (reason == 3); }
    f.manager.forgetReplyRouteForDisconnected(f.owner); f.otherRequest(); reply(f, kind); f.drain();
    assert(slot(f, kind).phase == Tracker::Empty);
    assert(count(f.wire(), code(kind)) == 0 && f.other_stream.output.empty()); ++checks;
  }
  for (bool tcp : {false, true}) for (Kind kind : kinds) {
    Fixture f(tcp); f.arm(kind); reply(f, kind); f.otherRequest(); reply(f, kind); f.drain();
    assertSentBeforeFinal(f.wire(), kind); assert(f.other_stream.output.empty()); ++checks;
  }
  for (bool tcp : {false, true}) for (Kind kind : {Tracker::Status, Tracker::Telemetry, Tracker::Binary, Tracker::Discovery}) {
    Fixture f(tcp); f.arm(kind); ContactInfo wrong = f.mesh.recipient; wrong.id.pub_key[0] ^= 0x40;
    f.otherRequest(); reply(f, kind, &wrong); assert(slot(f, kind).phase == Tracker::AwaitRadio);
    assert(f.other_stream.output.empty()); reply(f, kind); f.drain(); assertSentBeforeFinal(f.wire(), kind); ++checks;
  }
  for (bool tcp : {false, true}) {
    Fixture f(tcp); f.arm(Tracker::Login); ContactInfo collision = f.mesh.recipient; collision.id.pub_key[12] ^= 0x80;
    reply(f, Tracker::Login, &collision); assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio);
    assert(f.mesh.connection_starts == 0); reply(f, Tracker::Login); f.drain();
    assert(count(f.wire(), PUSH_CODE_LOGIN_SUCCESS) == 1 && f.mesh.connection_starts == 1); ++checks;
  }
  for (Kind kind : {Tracker::Status, Tracker::Telemetry, Tracker::Binary}) {
    Fixture f; f.arm(kind); reply(f, kind, nullptr, 17, 0x65, 4);
    assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio);
    reply(f, kind, nullptr, 17, 0x65, 255); assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio);
    reply(f, kind); f.drain(); assertSentBeforeFinal(f.wire(), kind); ++checks;
  }
  {
    Fixture f; f.arm(Tracker::Discovery); uint8_t path[64] = {}, extra[5] = {}; uint32_t tag = 17;
    memcpy(extra, &tag, 4); f.mesh.onContactPathRecv(f.mesh.recipient, path, 0xc0, path, 1,
                                                 PAYLOAD_TYPE_RESPONSE, extra, sizeof(extra));
    assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio);
    reply(f, Tracker::Discovery); f.drain(); assertSentBeforeFinal(f.wire(), Tracker::Discovery); ++checks;
  }
  {
    Fixture f; f.arm(Tracker::Trace); mesh::Packet packet; uint8_t data[256] = {};
    f.mesh.onTraceRecv(&packet, 17, 23, 0, data, data, 255);
    assert(f.mesh._delayed_replies.trace.phase == Tracker::AwaitRadio);
    reply(f, Tracker::Trace); f.drain(); assertSentBeforeFinal(f.wire(), Tracker::Trace); ++checks;
  }
  for (uint32_t start : {100U, 0xfffffff0U}) for (Kind kind : kinds) {
    Fixture f; g_mock_millis = start; f.arm(kind); assert(f.fill() == 3); reply(f, kind);
    const uint32_t expiry = slot(f, kind).delivery_deadline; g_mock_millis = expiry - 1;
    f.service(); assert(slot(f, kind).phase == Tracker::AwaitAdmission);
    g_mock_millis = expiry; f.service(); assert(slot(f, kind).phase == Tracker::Empty);
    f.otherRequest(); reply(f, kind); f.drain(); assert(count(f.wire(), code(kind)) == 0);
    assert(f.other_stream.output.empty()); ++checks;
  }
  {
    Fixture f; f.arm(Tracker::Binary); assert(f.fill() == 3); reply(f, Tracker::Binary);
    f.otherRequest(); assert(f.mesh._delayed_replies.reserveBinaryTrace(18, 23, &f.other, millis()));
    f.mesh._delayed_replies.armBinaryTrace(5000, millis()); f.mesh.serviceBinaryTraceReply();
    assert(f.fill(&f.other) == 3); reply(f, Tracker::Trace, nullptr, 18);
    assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitAdmission);
    assert(f.mesh._delayed_replies.trace.phase == Tracker::AwaitAdmission);
    f.mesh.cancelSerialOperationsForRoute(f.owner);
    assert(f.mesh._delayed_replies.request.phase == Tracker::Empty);
    assert(f.mesh._delayed_replies.trace.phase == Tracker::AwaitAdmission);
    f.drain(); assert(count(f.wire(), PUSH_CODE_BINARY_RESPONSE) == 0);
    assertSentBeforeFinal(f.other_stream.output, Tracker::Trace, 18); ++checks;
  }
  {
    Fixture f; f.arm(Tracker::Status); uint8_t extra[5] = {}, path[] = {1};
    f.mesh.onContactPathRecv(f.mesh.recipient, path, 1, path, 1, PAYLOAD_TYPE_RESPONSE, extra, 5);
    assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio && f.mesh._delayed_replies.request.tag == 17);
    reply(f, Tracker::Status); f.drain(); assertSentBeforeFinal(f.wire(), Tracker::Status); ++checks;
  }
  // A retired login peer is intentionally quarantined because its wire reply
  // has no reflected nonce. Other peers' legitimate unsolicited data survives.
  {
    Fixture f; f.arm(Tracker::Login); f.mesh.cancelSerialOperationsForRoute(f.owner); f.otherRequest();
    assert(!f.mesh.beginPendingRequest(Tracker::Login, f.mesh.recipient));
    reply(f, Tracker::Login, nullptr, 0x11223344); ContactInfo unrelated = f.mesh.recipient;
    unrelated.id.pub_key[0] ^= 0x40; reply(f, Tracker::Telemetry, &unrelated, 99); f.drain();
    assert(count(f.other_stream.output, PUSH_CODE_LOGIN_SUCCESS) == 0);
    assert(count(f.other_stream.output, PUSH_CODE_BINARY_RESPONSE) == 1); ++checks;
  }
  // Known reflected old replies must not become a newer login result; and a
  // positively matched newer status must remain valid after login quarantine.
  {
    Fixture f; f.arm(Tracker::Status); reply(f, Tracker::Status); f.drain();
    assert(f.mesh.beginPendingRequest(Tracker::Login, f.mesh.recipient)); f.mesh.armPendingRequest(23, 5000, false);
    reply(f, Tracker::Status); assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio);
    reply(f, Tracker::Login, nullptr, 0x11223344); f.drain(); assert(count(f.wire(), PUSH_CODE_LOGIN_SUCCESS) == 1); ++checks;
  }
  {
    Fixture f; f.arm(Tracker::Login); reply(f, Tracker::Login, nullptr, 0x11223344); f.drain();
    f.arm(Tracker::Status, 18); reply(f, Tracker::Status, nullptr, 18); f.drain();
    assert(count(f.wire(), PUSH_CODE_STATUS_RESPONSE) == 1); ++checks;
  }
  // A real login response carries the server's clock, not the client's
  // request timestamp. It can equal the client's next timestamp. The app's
  // immediate status/ACL request must select another tag without accepting
  // the old login response or changing the wall clock.
  for (bool tcp : {false, true}) for (Kind kind : {
      Tracker::Status, Tracker::Binary, Tracker::Telemetry, Tracker::Discovery}) {
    Fixture f(tcp); f.command(Tracker::Login);
    const uint32_t server_tag = f.mesh.rtc.wall + 1;
    reply(f, Tracker::Login, nullptr, server_tag); f.drain();
    const size_t request_start = f.wire().size();
    if (kind == Tracker::Binary) f.aclCommand(); else f.command(kind);
    assert(f.mesh.transmissions == 2 && f.mesh.allocations == 2);
    const uint32_t tag = f.mesh._delayed_replies.request.tag;
    assert(tag > server_tag && f.mesh.rtc.wall == 1000);
    reply(f, Tracker::Login, nullptr, server_tag);
    assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio);
    reply(f, kind, nullptr, tag); f.drain();
    assert(count(f.wire(), RESP_CODE_ERR) == 0);
    assert(count(f.wire(), PUSH_CODE_LOGIN_SUCCESS) == 1);
    assertSentBeforeFinal(Bytes(f.wire().begin() + request_start, f.wire().end()),
                          kind, tag); ++checks;
  }
  // An active request still rejects overlap before packet allocation/TX.
  for (Kind kind : {Tracker::Login, Tracker::Status, Tracker::Telemetry, Tracker::Binary, Tracker::Discovery, Tracker::Trace}) {
    Fixture f; f.command(kind); assert(f.mesh.transmissions == 1 && f.mesh.allocations == 1);
    f.command(kind); assert(f.mesh.transmissions == 1 && f.mesh.allocations == 1); ++checks;
  }
  for (bool tcp : {false,true}) for (Kind kind : kinds) for (bool flood : {false,true}) {
    Fixture f(tcp); if(flood && kind!=Tracker::Trace) f.mesh.recipient.out_path_len=OUT_PATH_UNKNOWN;
    f.command(kind); const auto& active=slot(f,kind);const uint32_t tag=active.tag;
    if(kind==Tracker::Login) { uint32_t expected;memcpy(&expected,f.mesh.recipient.id.pub_key,4);assert(tag==expected); }
    reply(f,kind,nullptr,kind==Tracker::Login ? 0x11223344U : tag);f.drain();
    assertSentBeforeFinal(f.wire(),kind,tag);const Bytes sent=frameWith(f.wire(),RESP_CODE_SENT);
    uint32_t timeout;memcpy(&timeout,sent.data()+6,4);assert(timeout==5000);
    assert(sent[1]==uint8_t(kind!=Tracker::Trace && (flood || kind==Tracker::Discovery)));++checks;
  }
  for(unsigned format=0;format<3;++format) {
    Fixture f;f.arm(Tracker::Login);assert(f.fill()==3);uint8_t data[13]={};uint32_t server_time=0x11223344;
    memcpy(data,&server_time,4);data[4]=RESP_SERVER_LOGIN_OK;data[5]=1;data[6]=1;data[7]=4;data[12]=13;
    f.mesh.onContactResponse(f.mesh.recipient,data,5);assert(slot(f,Tracker::Login).phase==Tracker::AwaitRadio);
    f.mesh.onContactResponse(f.mesh.recipient,data,12);assert(slot(f,Tracker::Login).phase==Tracker::AwaitRadio);
    data[4]='O';data[5]='K';f.mesh.onContactResponse(f.mesh.recipient,data,5);
    assert(slot(f,Tracker::Login).phase==Tracker::AwaitRadio);
    unsigned length=13;uint8_t expected=PUSH_CODE_LOGIN_SUCCESS;
    if(format==0) length=6;
    if(format==1) { data[4]=RESP_SERVER_LOGIN_OK;data[5]=1; }
    if(format==2) { data[4]='N';data[5]='O';length=6;expected=PUSH_CODE_LOGIN_FAIL; }
    f.mesh.onContactResponse(f.mesh.recipient,data,uint8_t(length));
    assert(slot(f,Tracker::Login).phase==Tracker::AwaitAdmission);
    const auto& held=slot(f,Tracker::Login);const Bytes original(held.frame,held.frame+held.frame_len);
    data[4]=RESP_SERVER_LOGIN_OK;data[5]=1;
    f.mesh.onContactResponse(f.mesh.recipient,data,13);
    assert(Bytes(slot(f,Tracker::Login).frame,slot(f,Tracker::Login).frame+slot(f,Tracker::Login).frame_len)==original);
    assert(f.mesh.connection_starts==(format==1 ? 1U : 0U));f.drain();
    assert(count(f.wire(),expected)==1 && frameWith(f.wire(),expected)==original);++checks;
  }
  // The callback captures one time. Crossing a deadline during later reads
  // must not invalidate a result which arrived before that deadline.
  for(Kind kind : kinds) {
    Fixture f;f.arm(kind);assert(f.fill()==3);const uint32_t deadline=slot(f,kind).radio_deadline;
    g_mock_millis=deadline-1;clock_step=1;reply(f,kind);clock_step=0;
    assert(g_mock_millis==deadline);
    assert(slot(f,kind).phase==Tracker::AwaitAdmission);f.drain();assertSentBeforeFinal(f.wire(),kind);++checks;
  }
  {
    Fixture f;f.arm(Tracker::Binary);assert(f.fill()==3);
    g_mock_millis=slot(f,Tracker::Binary).radio_deadline-1;reply(f,Tracker::Binary);
    g_mock_millis+=1000;f.drain();assertSentBeforeFinal(f.wire(),Tracker::Binary);++checks;
  }
  {
    Fixture f;assert(f.fill()==4);f.arm(Tracker::Binary,17,20000);
    const uint32_t host_deadline=slot(f,Tracker::Binary).sent_deadline;
    g_mock_millis=host_deadline-1;reply(f,Tracker::Binary);
    assert(slot(f,Tracker::Binary).delivery_deadline==host_deadline);
    g_mock_millis=host_deadline;f.service();assert(slot(f,Tracker::Binary).phase==Tracker::Empty);
    f.drain();assert(count(f.wire(),RESP_CODE_SENT)==0 && count(f.wire(),PUSH_CODE_BINARY_RESPONSE)==0);++checks;
  }
  for(Kind kind : {Tracker::Status,Tracker::Binary,Tracker::Telemetry,Tracker::Discovery,Tracker::Login,Tracker::Trace}) {
    Fixture f;f.mesh.radio_accepts=false;
    for(unsigned attempt=0;attempt<Tracker::HISTORY_SIZE+2;++attempt) {
      f.command(kind);assert(slot(f,kind).phase==Tracker::Empty);f.drain();
    }
    const unsigned sent=f.mesh.transmissions;f.mesh.radio_accepts=true;f.command(kind);
    assert(f.mesh.transmissions==sent+1 && slot(f,kind).phase==Tracker::AwaitRadio);++checks;
  }
  for(uint32_t now : {100U,0xfffffff0U}) {
    Tracker tracker;
    for(unsigned i=0;i<Tracker::HISTORY_SIZE;++i) {
      uint8_t history=tracker.reserveTrace(i+1,23,now);assert(history!=Tracker::NO_HISTORY);
      tracker.retireTrace(history,now,now+6000);
    }
    assert(tracker.reserveTrace(99,23,now)==Tracker::NO_HISTORY);
    assert(tracker.reserveTrace(99,23,now+15999)==Tracker::NO_HISTORY);
    assert(tracker.reserveTrace(99,23,now+16000)!=Tracker::NO_HISTORY);++checks;
  }
  for (bool request_service : {false, true}) for (uint32_t start : {100U, 0xfffffff0U}) {
    for (unsigned days : {31U, 40U}) for (bool daily_service : {false, true}) {
      testRetiredHistoryPruning(request_service, start, days, daily_service); ++checks;
    }
    for (Kind kind : {Tracker::Login, Tracker::Status}) {
      testReservedHistorySurvivesIdle(request_service, start, kind); ++checks;
    }
  }
  // RTC correction also skips recent tags. Old replies remain quarantined;
  // the newly transmitted tag is the one reported to the host and matched.
  for (bool anonymous : {false, true}) for (Kind kind : {
      Tracker::Status, Tracker::Binary, Tracker::Telemetry, Tracker::Discovery}) {
    Fixture f; f.command(kind, anonymous);
    const uint32_t old_tag = f.mesh._delayed_replies.request.tag;
    reply(f, kind, nullptr, old_tag); f.drain();
    const size_t request_start = f.wire().size();
    f.mesh.rtc.resetUniqueTime(old_tag); f.command(kind, anonymous);
    assert(f.mesh.transmissions == 2 && f.mesh.allocations == 2);
    const uint32_t tag = f.mesh._delayed_replies.request.tag;
    assert(tag == old_tag + 1);
    reply(f, kind, nullptr, old_tag);
    assert(f.mesh._delayed_replies.request.phase == Tracker::AwaitRadio);
    reply(f, kind, nullptr, tag); f.drain();
    assertSentBeforeFinal(Bytes(f.wire().begin() + request_start, f.wire().end()),
                          kind, tag); ++checks;
  }
  {
    Fixture f;
    for (unsigned i = 0; i < Tracker::HISTORY_SIZE - 1; ++i) {
      f.command(Tracker::Binary);
      reply(f, Tracker::Binary, nullptr, f.mesh._delayed_replies.request.tag);
      f.drain();
    }
    f.mesh.rtc.resetUniqueTime(1000); f.command(Tracker::Binary);
    assert(f.mesh.transmissions == Tracker::HISTORY_SIZE);
    assert(f.mesh._delayed_replies.request.tag == 1000 + Tracker::HISTORY_SIZE - 1);
    assert(f.mesh.rtc.wall == 1000); ++checks;
  }
  {
    struct RejectAllTags : MyMesh {
      unsigned attempts = 0;
      bool allowRequestTag(uint32_t) override { ++attempts; return false; }
    } mesh;
    uint32_t tag = 0;
    assert(!mesh.allocateRequestTag(tag));
    assert(mesh.attempts == Tracker::HISTORY_SIZE);
    assert(mesh.allocations == 0 && mesh.transmissions == 0); ++checks;
  }
  {
    Fixture f;
    for (unsigned i = 0; i != Tracker::HISTORY_SIZE; ++i) {
      f.command(Tracker::Binary); assert(f.mesh.transmissions == i + 1);
      const uint32_t tag = f.mesh._delayed_replies.request.tag;
      reply(f, Tracker::Binary, nullptr, tag); f.drain();
    }
    f.command(Tracker::Binary); assert(f.mesh.transmissions == Tracker::HISTORY_SIZE);
    assert(f.mesh.allocations == Tracker::HISTORY_SIZE); ++checks;
  }
  {
    Fixture f;
    for(unsigned i=0;i<Tracker::HISTORY_SIZE;++i) {
      f.command(Tracker::Trace,false,i+1);assert(f.mesh.transmissions==i+1);
      reply(f,Tracker::Trace,nullptr,i+1);f.drain();
    }
    f.command(Tracker::Trace,false,99);
    assert(f.mesh.transmissions==Tracker::HISTORY_SIZE && f.mesh.allocations==Tracker::HISTORY_SIZE);
    assert(f.mesh._delayed_replies.trace.phase==Tracker::Empty);++checks;
  }
  {
    Fixture f; f.command(Tracker::Trace); f.mesh.cancelSerialOperationsForRoute(f.owner);
    f.otherRequest(); f.command(Tracker::Trace); assert(f.mesh.transmissions == 1 && f.mesh.allocations == 1);
    reply(f, Tracker::Trace); f.drain();
    assert(count(f.other_stream.output, PUSH_CODE_TRACE_DATA) == 0);
    assert(frameWith(f.other_stream.output, RESP_CODE_ERR) == Bytes({RESP_CODE_ERR, ERR_CODE_BAD_STATE})); ++checks;
  }
  // Tombstones must retain more than the most recent retired reflected tag.
  {
    Fixture f; f.arm(Tracker::Binary, 17); reply(f, Tracker::Binary); f.drain();
    f.arm(Tracker::Binary, 18); reply(f, Tracker::Binary, nullptr, 18); f.drain();
    f.otherRequest(); reply(f, Tracker::Binary); reply(f, Tracker::Binary, nullptr, 18); f.drain();
    assert(f.other_stream.output.empty()); ++checks;
  }
#if COMPANION_FEATURE_TEXT_TERMINAL
  // Terminal login keeps the arm-time +20% deadline printed to the operator.
  for (uint32_t start : {100U, 0xfffffff0U}) {
    Fixture f; g_mock_millis = start; f.mesh.direct_timeout = 2000;
    f.mesh.sendTerminalLogin(f.mesh.recipient, "x");
    assert(f.mesh._terminal_login_expires_at == start + 2400U);
    assert(slot(f, Tracker::Login).radio_deadline == start + 2400U);
    assert(!slot(f, Tracker::Login).sent_pending);
    g_mock_millis = start + 2400U; reply(f, Tracker::Login);
    assert(!f.mesh._terminal_login_pending && !f.mesh.hasPendingReqs());
    f.drain(); assert(f.wire().empty()); ++checks;
  }
  // The timeout plus retirement grace must stay in the signed half-range.
  // Test the last reachable tripled base estimate and the next estimate,
  // including a millis wrap while a maximum-duration trace is pending.
  for(uint32_t start : {100U,0xfffffff0U}) {
    Fixture f;g_mock_millis=start;uint8_t route[]={0x12};
    const uint32_t ceiling=0x7fffffffU-Tracker::DELIVERY_GRACE_MS;
    const uint32_t last_base=ceiling/3U, last_timeout=last_base*3U;
    f.mesh.direct_timeout=last_base;
    f.mesh.sendTerminalTraceRoute(route,1,1,"synthetic");
    assert(f.mesh._terminal_trace_pending && f.mesh.transmissions==1 && f.mesh.allocations==1);
    assert(f.mesh._terminal_trace_expires_at==start+last_timeout);
    g_mock_millis=f.mesh._terminal_trace_expires_at-1;f.mesh.serviceTerminalTrace();
    assert(f.mesh._terminal_trace_pending);
    g_mock_millis+=1;f.mesh.serviceTerminalTrace();assert(!f.mesh._terminal_trace_pending);
    assert(f.mesh._delayed_replies.reserveTrace(17,23,millis())==Tracker::NO_HISTORY);
    g_mock_millis+=Tracker::DELIVERY_GRACE_MS;
    assert(f.mesh._delayed_replies.reserveTrace(17,23,millis())!=Tracker::NO_HISTORY);++checks;
  }
  {
    Fixture f;uint8_t route[]={0x12};
    f.mesh.direct_timeout=(0x7fffffffU-Tracker::DELIVERY_GRACE_MS)/3U+1U;
    f.mesh.sendTerminalTraceRoute(route,1,1,"synthetic");
    assert(!f.mesh._terminal_trace_pending && f.mesh.transmissions==0 && f.mesh.allocations==0);
    assert(f.mesh.rng.calls==0 && f.mesh._terminal_trace_history==Tracker::NO_HISTORY);
    const std::string text(f.mesh.terminal_stream.output.begin(),f.mesh.terminal_stream.output.end());
    assert(text.find("trace timeout is out of range")!=std::string::npos);
    for(unsigned i=0;i<Tracker::HISTORY_SIZE;++i)
      assert(f.mesh._delayed_replies.reserveTrace(i+1,23,millis())!=Tracker::NO_HISTORY);
    assert(f.mesh._delayed_replies.reserveTrace(99,23,millis())==Tracker::NO_HISTORY);++checks;
  }
  {
    Fixture f;f.mesh.sendTerminalLogin(f.mesh.recipient,"x");
    assert(f.mesh._terminal_login_pending && f.mesh.transmissions==1);
    assert(f.mesh._delayed_replies.request.terminal && f.mesh._delayed_replies.request.route==nullptr);
    reply(f,Tracker::Login,nullptr,0x11223344);reply(f,Tracker::Login,nullptr,0x11223344);
    assert(!f.mesh._terminal_login_pending && !f.mesh.hasPendingReqs() && f.mesh.connection_starts==1);
    f.drain();assert(f.wire().empty() && f.other_stream.output.empty());
    const std::string text(f.mesh.terminal_stream.output.begin(),f.mesh.terminal_stream.output.end());
    assert(text.find("LOGIN -> synthetic peer accepted")!=std::string::npos);++checks;
  }
  for(bool same_peer : {false,true}) {
    Fixture f;f.arm(Tracker::Login);f.mesh._terminal_login_pending=true;
    memcpy(f.mesh._terminal_login_key,f.mesh.recipient.id.pub_key,32);
    if(!same_peer) f.mesh._terminal_login_key[12]^=0x80;
    f.mesh.clearTerminalLogin();assert(f.mesh.hasPendingReqs());
    assert(f.mesh._delayed_replies.request.route==f.owner && !f.mesh._delayed_replies.request.terminal);
    reply(f,Tracker::Login);f.drain();assertSentBeforeFinal(f.wire(),Tracker::Login);++checks;
  }
  {
    Fixture f;f.mesh.radio_accepts=false;
    for(unsigned attempt=0;attempt<Tracker::HISTORY_SIZE+2;++attempt) {
      f.mesh.sendTerminalLogin(f.mesh.recipient,"x");assert(!f.mesh.hasPendingReqs());
    }
    f.mesh.radio_accepts=true;f.mesh.sendTerminalLogin(f.mesh.recipient,"x");
    assert(f.mesh._terminal_login_pending && f.mesh.hasPendingReqs());++checks;
  }
  {
    Fixture f; uint8_t route[] = {0x12}; f.mesh.sendTerminalTraceRoute(route, 1, 1, "synthetic");
    assert(f.mesh._terminal_trace_pending && f.mesh.transmissions == 1);
    f.command(Tracker::Trace); assert(f.mesh.transmissions == 1);
    mesh::Packet packet; uint8_t path[] = {0x12};
    f.mesh.onTraceRecv(&packet, 17, 23, 0, path, path, 1);
    assert(!f.mesh._terminal_trace_pending); f.command(Tracker::Trace); assert(f.mesh.transmissions == 1); ++checks;
  }
  {
    Fixture f; f.command(Tracker::Trace); uint8_t route[] = {0x12};
    f.mesh.sendTerminalTraceRoute(route, 1, 1, "synthetic"); assert(f.mesh.transmissions == 1);
    f.mesh.cancelSerialOperationsForRoute(f.owner); f.mesh.rng.calls = 0;
    f.mesh.sendTerminalTraceRoute(route, 1, 1, "synthetic"); assert(f.mesh.transmissions == 1); ++checks;
  }
#endif
  std::printf("PASS: delayed production reply delivery (%u runtime checks)\n", checks);
}
