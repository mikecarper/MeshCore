#pragma once

#include <Mesh.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

// RAM-only receive admission for a USB client's selected OTA admin peer.
// Matching happens after ECDH/MAC verification, before Binary login events or
// queued CLI messages lose their original RF route metadata.
class CompanionDirectAdminGuard {
  Identity peer_;
  uint8_t secret_[PUB_KEY_SIZE] = {};
  uint32_t until_ = 0;
  bool enabled_ = false, login_ = false;
  uint32_t last_peer_timestamp_ = 0;
  uint8_t reply_[160] = {};
  uint16_t reply_length_ = 0;
  char correlation_[3] = {};
  bool waiting_ = false, ready_ = false;

public:
  static constexpr uint32_t LEASE_MS = 300000;
  ~CompanionDirectAdminGuard() { clear(); }
  void clear() {
    volatile uint8_t* bytes = secret_;
    for (size_t i = 0; i < sizeof(secret_); ++i) bytes[i] = 0;
    bytes = reply_;
    for (size_t i = 0; i < sizeof(reply_); ++i) bytes[i] = 0;
    enabled_ = login_ = false;
    waiting_ = ready_ = false;
    reply_length_ = 0;
    last_peer_timestamp_ = 0;
    memset(correlation_, 0, sizeof(correlation_));
  }
  bool active(uint32_t now) {
    if (enabled_ && static_cast<int32_t>(now - until_) >= 0) clear();
    return enabled_;
  }
  bool matches(const Identity& peer, uint32_t now) {
    return active(now) && peer_.matches(peer);
  }
  bool selected(const Identity& peer, uint32_t now) const {
    return enabled_ && static_cast<int32_t>(now - until_) < 0 && peer_.matches(peer);
  }
  void arm(const Identity& peer, const uint8_t* secret, uint32_t now) {
    if (!matches(peer, now)) clear();
    peer_ = peer;
    memcpy(secret_, secret, sizeof(secret_));
    enabled_ = true;
    until_ = now + LEASE_MS;
  }
  void beginLogin(const Identity& peer, uint32_t now) {
    if (matches(peer, now)) login_ = true;
  }
  bool matchesPrefix(const uint8_t* prefix, size_t len, uint32_t now) {
    return prefix && len <= PUB_KEY_SIZE && active(now) && !memcmp(peer_.pub_key, prefix, len);
  }
  void noteLogin(const uint8_t* data, size_t len) {
    if (login_ && data && len >= 13 && data[4] == 0 && (data[7] & 7) == 3)
      memcpy(&last_peer_timestamp_, data, 4);
  }
  bool expect(const uint8_t* correlation, uint32_t now) {
    if (!active(now) || !correlation || correlation[2] != '|') return false;
    for (size_t i = 0; i < 2; ++i) {
      const uint8_t c = correlation[i];
      if (c < '!' || c > '~' || c == '|') return false;
    }
    memcpy(correlation_, correlation, sizeof(correlation_));
    reply_length_ = 0;
    waiting_ = true;
    ready_ = false;
    until_ = now + LEASE_MS;
    return true;
  }
  // Called only after the selected peer's ECDH/MAC and DIRECT0 admission.
  // Other messages continue through the ordinary inbox unchanged.
  bool captureReply(const uint8_t* data, size_t len, uint32_t now) {
    if (!active(now) || (!waiting_ && !ready_) || !data || len < 8
        || (data[4] >> 2) != 1 || memcmp(data + 5, correlation_, 3)) return false;
    if (ready_) return true;  // duplicate maintenance replies do not enter the inbox
    uint32_t timestamp;
    memcpy(&timestamp, data, 4);
    if (timestamp <= last_peer_timestamp_) return true;  // consume stale copy only
    const uint8_t* text = data + 8;
    size_t length = len - 8;
    const void* zero = memchr(text, 0, length);
    if (zero) length = static_cast<const uint8_t*>(zero) - text;
    if (length > sizeof(reply_)) return true;
    memcpy(reply_, text, length);
    reply_length_ = length;
    last_peer_timestamp_ = timestamp;
    ready_ = true;
    waiting_ = false;
    until_ = now + LEASE_MS;
    return true;
  }
  void readReply(size_t offset, char* output, size_t capacity, uint32_t now) {
    active(now);
    if (offset > reply_length_) {
      snprintf(output, capacity, "ERR invalid reply offset");
      return;
    }
    const int used = snprintf(output, capacity,
        "> ota.reply req=%c%c state=%s bytes=%u off=%u data=",
        correlation_[0] ? correlation_[0] : '-', correlation_[1] ? correlation_[1] : '-',
        ready_ ? "ready" : waiting_ ? "pending" : "idle",
        static_cast<unsigned>(reply_length_), static_cast<unsigned>(offset));
    if (used < 0 || static_cast<size_t>(used) >= capacity) return;
    size_t at = used;
    const size_t end = offset + 40 < reply_length_ ? offset + 40 : reply_length_;
    static const char hex[] = "0123456789ABCDEF";
    for (; offset < end && at + 2 < capacity; ++offset) {
      output[at++] = hex[reply_[offset] >> 4];
      output[at++] = hex[reply_[offset] & 15];
    }
    if (at == static_cast<size_t>(used) && at + 1 < capacity) output[at++] = '-';
    output[at] = 0;
  }
  __attribute__((noinline)) bool matchesSecret(const uint8_t* secret, uint32_t now) {
    return active(now) && secret && !memcmp(secret_, secret, sizeof(secret_));
  }
  bool accepts(const Packet* packet, uint8_t type, const uint8_t* secret, uint32_t now) {
    if (!matchesSecret(secret, now)) return true;
    if (!packet || packet->getPathHashCount() != 0) return false;
    if (packet->isRouteDirect()) return true;
    // Unknown ACL return paths cause old servers to flood the first login
    // response. No relayed copy, other response type, or CLI uses this exception.
    return login_ && type == PAYLOAD_TYPE_RESPONSE && packet->isRouteFlood();
  }
  bool acceptsPath(const Packet* packet, const uint8_t* secret, uint8_t path_len, uint32_t now) {
    if (!matchesSecret(secret, now)) return true;
    return !(path_len & 63) && packet && packet->getPathHashCount() == 0
        && packet->isRouteDirect();
  }
  bool sendReturnPath(Mesh& mesh, const Identity& peer, uint32_t now,
                      uint8_t tx_radio = RADIO_TX_PRIMARY) {
    if (!matches(peer, now) || (tx_radio != RADIO_TX_PRIMARY && tx_radio != RADIO_TX_SECONDARY)) return false;
    const uint8_t empty[1] = {};
    Packet* packet = mesh.createPathReturn(peer_, secret_, empty, 0, 0, nullptr, 0);
    if (packet) {
      packet->tx_radio = tx_radio;
      packet->radio_profile = packet->radio_origin = tx_radio == RADIO_TX_SECONDARY ? 1 : 0;
    }
    if (!packet || !mesh.sendDirect(packet, nullptr, 0)) return false;
    login_ = false;
    until_ = now + LEASE_MS;
    return true;
  }
};

}  // namespace mesh
