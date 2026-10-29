#include "UDPBridge.h"
#if defined(ESP32) && defined(WITH_UDP_BRIDGE)
#include <Arduino.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <lwip/sockets.h>
#include <SHA256.h>
#include <esp_system.h>

namespace udp = mesh::bridge::udp;
static_assert(sizeof(UDPBridge) <= 3072, "UDP bridge object exceeds its Full memory reserve");

UDPBridge::UDPBridge(NodePrefs* prefs, mesh::PacketManager* mgr, mesh::RTCClock* rtc,
                     const udp::Prefs* settings) : BridgeBase(prefs, mgr, rtc), _settings(settings) {}
UDPBridge::~UDPBridge() { end(); }
void UDPBridge::note(uint32_t& value) { udp::increment(value); _debug_dirty = true; }
void UDPBridge::error(int code) { _last_error = code; _debug_dirty = true; }
uint64_t UDPBridge::randomNonce() {
  uint64_t value;
  do { value = (uint64_t(esp_random()) << 32) | esp_random(); } while (!value);
  return value;
}
bool UDPBridge::authenticate(void* context, const uint8_t* data, size_t size, uint8_t digest[32]) {
  const auto* self = static_cast<const UDPBridge*>(context);
  if (!self->_secret_valid) return false;
  SHA256 sha;
  static const uint8_t domain[] = "MeshCore UDP bridge v1";
  sha.resetHMAC(self->_secret, self->_secret_length);
  sha.update(domain, sizeof(domain));
  sha.update(data, size);
  sha.finalizeHMAC(self->_secret, self->_secret_length, digest, 32);
  return true;
}
void UDPBridge::begin() {
  if (_initialized || !_network_ready || !_settings || !_settings->enabled) return;
  _active = *_settings;
  _secret_length = 0;
  while (_secret_length < sizeof(_prefs->bridge_secret) && _prefs->bridge_secret[_secret_length]) ++_secret_length;
  _secret_valid = _secret_length > 0 && _secret_length < sizeof(_prefs->bridge_secret);
  if (!_secret_valid || !udp::validPrefs(_active)) { error(EINVAL); return; }
  for (const auto& peer : _active.peers) {
    if (!peer.port) continue;
    uint32_t address; memcpy(&address, peer.ip, sizeof(address));
    if (_local_address && (address == _local_address || (_netmask
        && (address == (_local_address | ~_netmask)
            || address == (_local_address & _netmask))))) {
      error(EADDRNOTAVAIL); return;
    }
  }
  memcpy(_secret, _prefs->bridge_secret, _secret_length);
  _socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (_socket < 0) { error(errno); return; }
  const int flags = fcntl(_socket, F_GETFL, 0);
  if (flags < 0 || fcntl(_socket, F_SETFL, flags | O_NONBLOCK) < 0) {
    const int code = errno; end(); error(code); return;
  }
  // Bound lwIP's receive-byte accounting where supported. Application ingress
  // is additionally limited to two datagrams per loop and one owned RX slot.
#if defined(SO_RCVBUF)
  const int receive_bytes = 768;
  if (setsockopt(_socket, SOL_SOCKET, SO_RCVBUF, &receive_bytes, sizeof(receive_bytes)) != 0) {
    const int code = errno; end(); error(code); return;
  }
#else
  end(); error(ENOTSUP); return;
#endif
  sockaddr_in local{};
  local.sin_family = AF_INET; local.sin_port = htons(_active.port); local.sin_addr.s_addr = htonl(INADDR_ANY);
  if (bind(_socket, reinterpret_cast<sockaddr*>(&local), sizeof(local)) != 0) {
    const int code = errno; end(); error(code); return;
  }
  _boot = randomNonce(); _sequence = 0;
  for (auto& session : _sessions) { session = udp::Session{}; session.next_hello = millis() + esp_random() % 1000; }
  _initialized = true; _last_error = 0; _debug_dirty = true;
}
void UDPBridge::end() {
  if (_socket >= 0) { close(_socket); _socket = -1; }
  _initialized = false; _tx_mask = 0; _tx_length = _rx_length = 0;
  _boot = 0; _sequence = 0;
  for (auto& session : _sessions) session = udp::Session{};
  memset(_secret, 0, sizeof(_secret)); _secret_length = 0; _secret_valid = false;
  _debug_dirty = true;
}
void UDPBridge::serviceNetwork(bool ready, uint32_t local_address, uint32_t netmask) {
  // The shared bridge secret can change without a transport-specific restart
  // callback. End the old authenticated session before any more socket work.
  if (_initialized) {
    const size_t length = strnlen(_prefs->bridge_secret, sizeof(_prefs->bridge_secret));
    if (!length || length >= sizeof(_prefs->bridge_secret) || length != _secret_length
        || memcmp(_secret, _prefs->bridge_secret, length)) {
      end(); _retry_at = millis();
    }
  }
  if (_local_address != local_address || _netmask != netmask) {
    _local_address = local_address; _netmask = netmask;
    if (_initialized) end();
    _retry_at = millis();
  }
  if (_network_ready != ready) { _network_ready = ready; _debug_dirty = true; _retry_at = millis(); }
  if (!ready || !_settings || !_settings->enabled) {
    if (_socket >= 0 || _initialized) end();
    return;
  }
  const uint32_t now = millis();
  if (!_initialized && udp::due(now, _retry_at)) { _retry_at = now + 5000; begin(); }
}
uint8_t UDPBridge::peerMask(bool established_only) const {
  uint8_t mask = 0;
  for (uint8_t i = 0; i < udp::MAX_PEERS; ++i)
    if (_active.peers[i].port && (!established_only || _sessions[i].remote)) mask |= uint8_t(1U << i);
  return mask;
}
uint32_t UDPBridge::nextSequence() {
  // Never reuse a sequence within the same boot/session. Reopen on the next
  // service iteration; queued data is explicitly best-effort and discarded.
  if (_sequence == UINT32_MAX) { end(); error(EOVERFLOW); return 0; }
  return ++_sequence;
}
bool UDPBridge::sendFrame(uint8_t peer, udp::Kind kind, uint64_t recipient,
                          const uint8_t* payload, uint16_t length, uint32_t sequence) {
  if (!_initialized || peer >= udp::MAX_PEERS || !_active.peers[peer].port) return false;
  if (!sequence) sequence = nextSequence();
  if (!_initialized || !sequence) return false;
  udp::Frame frame;
  frame.kind = kind;
  frame.sender = _boot;
  frame.recipient = recipient;
  frame.sequence = sequence;
  frame.payload = payload;
  frame.length = length;
  uint8_t bytes[udp::MAX_FRAME];
  const size_t size = udp::encode(bytes, sizeof(bytes), frame, authenticate, this);
  if (!size) { error(EINVAL); return false; }
  const auto& endpoint = _active.peers[peer];
  sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(endpoint.port);
  memcpy(&address.sin_addr.s_addr, endpoint.ip, sizeof(endpoint.ip));
  const int sent = sendto(_socket, bytes, size, MSG_DONTWAIT,
      reinterpret_cast<const sockaddr*>(&address), sizeof(address));
  if (sent != static_cast<int>(size)) { error(sent < 0 ? errno : EIO); return false; }
  return true;
}
void UDPBridge::pumpHello() {
  const uint32_t now = millis();
  for (uint8_t attempts = 0; attempts < udp::MAX_PEERS; ++attempts) {
    const uint8_t i = _hello_cursor++ % udp::MAX_PEERS;
    if (!_active.peers[i].port) continue;
    auto& session = _sessions[i];
    if (session.candidate && !udp::due(now, session.challenge_deadline)
        && uint32_t(now - session.challenge_sent_at) >= 1000) {
      uint8_t nonce[8]; udp::put64(nonce, session.challenge);
      session.challenge_sent_at = now;
      if (sendFrame(i, udp::Kind::Challenge, session.candidate, nonce, sizeof(nonce))) note(_stats.challenges);
      return; // Retransmit handshake control only, never data or test probes.
    }
    if (!udp::due(now, session.next_hello)) continue;
    _sessions[i].next_hello = now + (_sessions[i].remote ? 10000 : 1000) + esp_random() % 500;
    if (sendFrame(i, udp::Kind::Hello, 0, nullptr, 0)) note(_stats.hellos);
    return; // At most one periodic control datagram per main loop.
  }
}
void UDPBridge::receiveFrame(uint8_t peer, const uint8_t* data, size_t length) {
  udp::Frame frame;
  const auto decoded = udp::decode(data, length, frame, authenticate, this);
  if (decoded != udp::Decode::Ok) {
    note(decoded == udp::Decode::Authentication ? _stats.authentication : _stats.malformed); return;
  }
  note(_stats.authenticated);
  if (frame.sender == _boot) { note(_stats.foreign); return; }
  auto& session = _sessions[peer];
  const uint32_t now = millis();
  if (frame.kind == udp::Kind::Hello) {
    // Refreshing the same proven boot does not reset its replay high-water.
    if (session.request(frame.sender, randomNonce(), now)) {
      uint8_t nonce[8]; udp::put64(nonce, session.challenge);
      if (sendFrame(peer, udp::Kind::Challenge, frame.sender, nonce, sizeof(nonce))) note(_stats.challenges);
    }
    return;
  }
  if (frame.recipient != _boot) { note(_stats.replay); return; }
  if (frame.kind == udp::Kind::Challenge) {
    // Proof is the only automatic control echo. Explicit probes never echo.
    (void)sendFrame(peer, udp::Kind::Proof, frame.sender, frame.payload, frame.length);
    return;
  }
  if (frame.kind == udp::Kind::Proof) {
    const bool changed = session.remote != frame.sender;
    if (session.prove(frame.sender, udp::get64(frame.payload), now)) {
      note(_stats.established);
      // Expedite the reciprocal challenge after first learning a boot. This
      // also recovers a dropped startup Hello without a long ready interval.
      if (changed) session.next_hello = now;
    } else note(_stats.replay);
    return;
  }
  if (!session.admit(frame.sender, frame.recipient, _boot, frame.sequence)) { note(_stats.replay); return; }
  if (frame.kind == udp::Kind::Probe) {
    _stats.last_rx_nonce = udp::get32(frame.payload); note(_stats.probe_received); return;
  }
  // Copy verified bytes into one bounded holding slot before allocating from
  // the shared mesh pool. A full slot stops socket reads, preserving its owner.
  memcpy(_rx_payload, frame.payload, frame.length); _rx_length = frame.length;
  _rx_peer = peer;
}
void UDPBridge::pumpReceive() {
  for (uint8_t count = 0; count < 2 && !_rx_length && _initialized; ++count) {
    uint8_t bytes[udp::MAX_FRAME + 1]; sockaddr_in source{}; socklen_t source_size = sizeof(source);
    const int length = recvfrom(_socket, bytes, sizeof(bytes), MSG_DONTWAIT,
        reinterpret_cast<sockaddr*>(&source), &source_size);
    if (length < 0) {
      if (errno != EAGAIN && errno != EWOULDBLOCK) error(errno);
      return;
    }
    note(_stats.frames);
    int peer = -1;
    for (uint8_t i = 0; i < udp::MAX_PEERS; ++i)
      if (_active.peers[i].port && source.sin_port == htons(_active.peers[i].port)
          && !memcmp(&source.sin_addr.s_addr, _active.peers[i].ip, 4)) { peer = i; break; }
    if (source_size != sizeof(source) || source.sin_family != AF_INET || peer < 0) { note(_stats.foreign); continue; }
    receiveFrame(static_cast<uint8_t>(peer), bytes, static_cast<size_t>(length));
    drainReceive();
  }
}
void UDPBridge::drainReceive() {
  if (!_rx_length) return;
  mesh::Packet* packet = _mgr->allocNew();
  if (!packet) { note(_stats.pool); return; }
  const bool parsed = packet->readFrom(_rx_payload, static_cast<uint8_t>(_rx_length));
  _rx_length = 0;
  if (!parsed) { _mgr->free(packet); note(_stats.parse); return; }
  const auto ingress_source = mesh::bridge::IngressSource::udp(
      _active.peers[_rx_peer].ip, _active.peers[_rx_peer].port);
  IngressSourceScope ingress_scope(*this, ingress_source);
  packet->udp_bridge_ingress = true;
  onPacketReceived(packet);
}
void UDPBridge::onPacketReceived(mesh::Packet* packet) {
  if (packet) { packet->udp_bridge_ingress = true; handleReceivedPacket(packet); }
}
void UDPBridge::onIngressResult(IngressResult result) {
  switch (result) {
    case IngressResult::Queued: note(_stats.delivered); break;
    case IngressResult::Filtered: note(_stats.filtered); break;
    case IngressResult::Duplicate: note(_stats.duplicate); break;
    case IngressResult::Stopped: break;
  }
}
void UDPBridge::sendPacket(mesh::Packet* packet) {
  note(_stats.requested);
  if (!_initialized) { note(_stats.stopped); return; }
  if (!packet || packet->udp_bridge_ingress || !allowsPacket(packet)) { note(_stats.tx_filtered); return; }
  const bool ota = packet->getPayloadType() == PAYLOAD_TYPE_OTA;
  if (!ota && _seen_packets.wasSeen(packet)) { note(_stats.tx_duplicate); return; }
  const uint8_t destinations = peerMask(true);
  if (_tx_mask || !destinations) { note(_stats.busy); return; }
  const int length = packet->getRawLength();
  if (length < 2 || length > static_cast<int>(sizeof(_tx_payload))) { note(_stats.oversize); return; }
  const uint32_t sequence = nextSequence();
  if (!sequence) { note(_stats.stopped); return; }
  if (packet->writeTo(_tx_payload) != length) { note(_stats.failed); return; }
  _tx_length = length; _tx_mask = destinations; _tx_kind = udp::Kind::Mesh; _tx_sequence = sequence;
  if (!ota) _seen_packets.markSeen(packet);
  note(_stats.queued);
}
void UDPBridge::pumpTransmit() {
  if (!_tx_mask) return;
  uint8_t peer = 0;
  while (!(_tx_mask & (1U << peer))) ++peer;
  _tx_mask &= static_cast<uint8_t>(~(1U << peer));
  const bool sent = sendFrame(peer, _tx_kind, _sessions[peer].remote, _tx_payload, _tx_length, _tx_sequence);
  if (_tx_kind == udp::Kind::Probe) note(sent ? _stats.probe_sent : _stats.probe_failed);
  else note(sent ? _stats.sent : _stats.failed);
  if (!_tx_mask) _tx_length = 0;
}
bool UDPBridge::sendTest(uint8_t peer, uint32_t nonce) {
  if (!_initialized || peer >= udp::MAX_PEERS || !(peerMask(true) & (1U << peer)) || _tx_mask) {
    note(_stats.probe_failed); return false;
  }
  const uint32_t sequence = nextSequence();
  if (!sequence) { note(_stats.probe_failed); return false; }
  udp::put32(_tx_payload, nonce); _tx_length = 4;
  _tx_mask = static_cast<uint8_t>(1U << peer); _tx_kind = udp::Kind::Probe; _tx_sequence = sequence;
  _stats.last_tx_nonce = nonce; note(_stats.probe_queued); return true;
}
void UDPBridge::loop() {
  if (_initialized) { drainReceive(); pumpReceive(); pumpTransmit(); pumpHello(); }
  emitDebug();
}
void UDPBridge::resetStats() { _stats = Stats{}; _debug_dirty = true; }
void UDPBridge::formatStatus(char* out, size_t capacity) const {
  if (!out || !capacity) return;
  unsigned peers = 0, ready = 0;
  for (uint8_t i = 0; i < udp::MAX_PEERS; ++i) {
    const auto* prefs = _initialized ? &_active : _settings;
    if (prefs && prefs->peers[i].port) ++peers;
    if (_sessions[i].remote) ++ready;
  }
  const char* state = !_settings || !_settings->enabled ? "off" : !_network_ready ? "netwait"
      : !_initialized ? "retrying" : "ready";
  const uint8_t* ip = reinterpret_cast<const uint8_t*>(&_local_address);
  snprintf(out, capacity, "> %s ip=%u.%u.%u.%u port=%u peers=%u ready=%u src=%s q=%u/%u last=%d", state,
      unsigned(ip[0]), unsigned(ip[1]), unsigned(ip[2]), unsigned(ip[3]),
      unsigned(_initialized ? _active.port : _settings ? _settings->port : 0), peers, ready,
      _prefs->bridge_pkt_src ? "rx" : "tx", unsigned(_rx_length != 0), unsigned(_tx_mask != 0), _last_error);
  out[capacity - 1] = 0;
}
void UDPBridge::formatStats(const char* page, char* out, size_t capacity) const {
  if (!out || !capacity) return;
  const auto& s = _stats;
  if (!page || !page[0]) snprintf(out, capacity, "> rx=%lu mesh=%lu tx=%lu sent=%lu; pages: rx tx drops test",
      (unsigned long)s.frames, (unsigned long)s.delivered, (unsigned long)s.queued, (unsigned long)s.sent);
  else if (!strcmp(page, "rx")) snprintf(out, capacity, "> frames=%lu auth=%lu mesh=%lu parse=%lu pool=%lu hello=%lu proof=%lu",
      (unsigned long)s.frames, (unsigned long)s.authenticated, (unsigned long)s.delivered,
      (unsigned long)s.parse, (unsigned long)s.pool, (unsigned long)s.hellos, (unsigned long)s.established);
  else if (!strcmp(page, "tx")) snprintf(out, capacity, "> req=%lu queued=%lu sent=%lu err=%lu busy=%lu stopped=%lu",
      (unsigned long)s.requested, (unsigned long)s.queued, (unsigned long)s.sent,
      (unsigned long)s.failed, (unsigned long)s.busy, (unsigned long)s.stopped);
  else if (!strcmp(page, "drops")) snprintf(out, capacity, "> bad=%lu auth=%lu foreign=%lu replay=%lu filter=%lu dupe=%lu txfilter=%lu txdupe=%lu big=%lu",
      (unsigned long)s.malformed, (unsigned long)s.authentication, (unsigned long)s.foreign, (unsigned long)s.replay,
      (unsigned long)s.filtered, (unsigned long)s.duplicate, (unsigned long)s.tx_filtered,
      (unsigned long)s.tx_duplicate, (unsigned long)s.oversize);
  else if (!strcmp(page, "test")) snprintf(out, capacity, "> queued=%lu sent=%lu fail=%lu rx=%lu txid=%08lX rxid=%08lX",
      (unsigned long)s.probe_queued, (unsigned long)s.probe_sent, (unsigned long)s.probe_failed,
      (unsigned long)s.probe_received, (unsigned long)s.last_tx_nonce, (unsigned long)s.last_rx_nonce);
  else snprintf(out, capacity, "Error: use get udp.stats [rx|tx|drops|test]");
  out[capacity - 1] = 0;
}
void UDPBridge::emitDebug() {
  if (!mesh::isUsbDebugLoggingEnabled()) { _debug_was_enabled = false; return; }
  if (!_debug_was_enabled) _debug_dirty = true;
  _debug_was_enabled = true;
  const uint32_t now = millis();
  if (!_debug_dirty || uint32_t(now - _debug_last_at) < 1000) return;
  _debug_last_at = now; _debug_dirty = false;
  char status[157]; formatStatus(status, sizeof(status));
#if MESH_ESP32_USB_CONSOLE_COOPERATIVE
  mesh::nrf52DebugPrintf("UDP: %s rx=%lu mesh=%lu authbad=%lu replay=%lu tx=%lu sent=%lu\n", status + 2,
      (unsigned long)_stats.frames, (unsigned long)_stats.delivered, (unsigned long)_stats.authentication,
      (unsigned long)_stats.replay, (unsigned long)_stats.queued, (unsigned long)_stats.sent);
#else
  char line[256];
  const int length = snprintf(line, sizeof(line), "UDP: %s rx=%lu mesh=%lu authbad=%lu replay=%lu tx=%lu sent=%lu\n", status + 2,
      (unsigned long)_stats.frames, (unsigned long)_stats.delivered, (unsigned long)_stats.authentication,
      (unsigned long)_stats.replay, (unsigned long)_stats.queued, (unsigned long)_stats.sent);
  if (length > 0 && size_t(length) < sizeof(line) && mesh::usbLoggingPort().availableForWrite() >= length)
    mesh::usbLoggingPort().write(reinterpret_cast<const uint8_t*>(line), length);
#endif
}
#endif
