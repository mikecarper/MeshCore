#include <cassert>
#include <cstring>
#include <ed_25519.h>
#include "TagPackets.h"

int main() {
  using namespace mesh::tracker;
  uint8_t aseed[32] = {11}, bseed[32] = {22};
  uint8_t collar[32], collar_key[64], owner[32], owner_key[64], ab[32], ba[32];
  ed25519_create_keypair(collar, collar_key, aseed);
  ed25519_create_keypair(owner, owner_key, bseed);
  ed25519_key_exchange(ab, owner, collar_key);
  ed25519_key_exchange(ba, collar, owner_key);
  assert(!memcmp(ab, ba, sizeof(ab)));
  uint32_t utc = 1700000000;
  StatusRequest check{}, received{};
  check.flags = FreshGps;
  check.latitude = 471234567; check.longitude = -1222345678; check.battery_mv = 3860;
  uint8_t body[TagLength + RequestLength];
  put32(body, utc);
  assert(makeStatusRequest(check, body + TagLength, RequestLength));
  mesh::Packet p;
  tag_packets::seal(p, PAYLOAD_TYPE_REQ, collar, owner, ab, body, sizeof(body));
  uint8_t raw[255];
  auto size = p.writeTo(raw);
  uint32_t matched = 0;
  assert(tag_packets::unpackCheck(raw, size, owner, collar, ba, utc - 1, utc, matched, received));
  assert(matched == utc && received.latitude == check.latitude && received.longitude == check.longitude);
  assert(received.battery_mv == 3860);
  assert(!tag_packets::unpackCheck(raw, size, owner, collar, ba, utc, utc, matched, received));
  assert(!tag_packets::unpackCheck(raw, size, owner, collar, ba, 0, utc - 301, matched, received));
  assert(!tag_packets::unpackCheck(raw, size, owner, collar, ba, 0, utc + 301, matched, received));
  raw[size-1] ^= 1;
  assert(!tag_packets::unpackCheck(raw, size, owner, collar, ba, 0, utc, matched, received));
  uint8_t reply[ResponseLength], state;
  assert(makeStatusResponse(utc, Lost, 0, reply, sizeof(reply)));
  tag_packets::seal(p, PAYLOAD_TYPE_RESPONSE, owner, collar, ba, reply, sizeof(reply));
  size = p.writeTo(raw);
  assert(tag_packets::unpackReply(raw, size, collar, owner, ab, utc, state) && state == Lost);
  assert(!tag_packets::unpackReply(raw, size, collar, owner, ab, utc+1, state));
  uint8_t wrong_secret[32]{};
  assert(!tag_packets::unpackReply(raw, size, collar, owner, wrong_secret, utc, state));
  raw[0] = ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_RESPONSE << PH_TYPE_SHIFT);
  assert(!tag_packets::unpackReply(raw, size, collar, owner, ab, utc, state));
}
