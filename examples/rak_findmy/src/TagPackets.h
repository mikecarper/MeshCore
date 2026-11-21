#pragma once

#include <Packet.h>
#include <Utils.h>
#include <helpers/TrackerProtocol.h>

namespace tag_packets {

// The first bytes route the packet. Integrity is checked against the shared
// secret for the full locally configured peer before interpreting its payload.
inline int open(const uint8_t* bytes, uint8_t size, uint8_t type,
                const uint8_t* local, const uint8_t* peer, const uint8_t* secret,
                uint8_t (&clear)[MAX_PACKET_PAYLOAD]) {
  mesh::Packet packet;
  if (!packet.readFrom(bytes, size) || packet.getRouteType() != ROUTE_TYPE_DIRECT
      || packet.path_len || packet.getPayloadVer() != PAYLOAD_VER_1
      || packet.getPayloadType() != type || packet.payload_len < 4
      || packet.payload[0] != local[0] || packet.payload[1] != peer[0]) return 0;
  return mesh::Utils::MACThenDecrypt(secret, clear, packet.payload + 2, packet.payload_len - 2);
}

inline void seal(mesh::Packet& packet, uint8_t type, const uint8_t* local,
                 const uint8_t* peer, const uint8_t* secret, const uint8_t* clear, size_t length) {
  packet.header = ROUTE_TYPE_DIRECT | (type << PH_TYPE_SHIFT);
  packet.path_len = 0;
  packet.payload[0] = peer[0];
  packet.payload[1] = local[0];
  packet.payload_len = 2 + mesh::Utils::encryptThenMAC(secret, packet.payload + 2, clear, length);
}

inline bool unpackReply(const uint8_t* bytes, uint8_t size, const uint8_t* local,
                        const uint8_t* peer, const uint8_t* secret, uint32_t pending, uint8_t& status) {
  uint8_t clear[MAX_PACKET_PAYLOAD];
  int len = open(bytes, size, PAYLOAD_TYPE_RESPONSE, local, peer, secret, clear);
  uint16_t lease;
  return pending && len > 0 && mesh::tracker::parseStatusResponse(clear, len, pending, status, lease);
}

inline bool unpackCheck(const uint8_t* bytes, uint8_t size, const uint8_t* local,
                        const uint8_t* peer, const uint8_t* secret, uint32_t latest, uint32_t utc,
                        uint32_t& tag, mesh::tracker::StatusRequest& fix) {
  using namespace mesh::tracker;
  uint8_t clear[MAX_PACKET_PAYLOAD];
  int len = open(bytes, size, PAYLOAD_TYPE_REQ, local, peer, secret, clear);
  if (len <= int(TagLength) || !parseStatusRequest(clear + TagLength, len - TagLength, fix)) return false;
  tag = get32(clear);
  return utc && tag > latest && uint64_t(tag) <= uint64_t(utc) + 300 && uint64_t(tag) + 300 >= utc;
}
} // namespace tag_packets
