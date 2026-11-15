#include <Mesh.h>

#if MESH_ENABLE_TX_BEACONS

#include <helpers/BeaconFrames.h>
#include <string.h>

namespace mesh {

bool Mesh::validateTextBeacon(const BeaconSlot& slot) const {
  int8_t power = slot.power;
  if (slot.use_default_power && !_radio->getBeaconDefaultPower(power)) return false;
  if (!beacon::validText(slot.text) || slot.hops > 7
      || !RadioProfiles::valid(slot.radio)
      || !RadioProfiles::safePreamble(slot.radio, slot.radio.preamble ? slot.radio.preamble : 32)
      || !_radio->validateBeaconPower(slot.radio.freq, power)
      || !_radio->validateProfile(slot.radio)) return false;
  if (slot.radio.protocol == RadioProfileProtocol::MeshCore) return true;
#if MESH_ENABLE_MESHTEXT_WIRE
  return slot.radio.protocol == RadioProfileProtocol::Meshtext
      && _radio->supportsMeshtext()
      && mt::presetName(static_cast<mt::Preset>(slot.preset)) != nullptr;
#else
  return false;
#endif
}

bool Mesh::queueTextBeacon(const BeaconSlot& slot, const char* node_name,
                           uint32_t mt_from, uint32_t mt_id) {
  // Resolve once for this scheduled attempt. The queue receives an ordinary
  // numeric power snapshot; a later node power change affects the next beacon.
  BeaconSlot effective = slot;
  if (effective.use_default_power) {
    if (!_radio->getBeaconDefaultPower(effective.power)) return false;
    effective.use_default_power = false;
  }
  if (!validateTextBeacon(effective)) return false;
  uint8_t raw[MAX_TRANS_UNIT];
  if (slot.radio.protocol == RadioProfileProtocol::Meshtext) {
    const size_t length = beacon::meshtextFrame(static_cast<mt::Preset>(slot.preset),
        mt_from, mt_id, slot.hops, node_name, slot.text, raw);
    return length && queueBeaconRadioFrame(raw, length, slot.radio, effective.power);
  }

  uint8_t plain[beacon::MaxPublicPayloadBytes];
  const size_t length = beacon::publicPayload(getRTCClock()->getCurrentTimeUnique(),
      node_name, slot.text, plain, sizeof(plain));
  if (!length) return false;
  GroupChannel channel{};
  memcpy(channel.secret, beacon::PublicKey, sizeof(beacon::PublicKey));
  Utils::sha256(channel.hash, sizeof(channel.hash), channel.secret, sizeof(beacon::PublicKey));
  Packet* packet = createGroupDatagram(PAYLOAD_TYPE_GRP_TXT, channel, plain, length);
  if (!packet) return false;
  // Serialize one unscoped Public flood. Do not enter sendFlood(), which can
  // schedule reliable retries or fan the packet out across receive profiles.
  packet->header = (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD;
  packet->setPathHashSizeAndCount(1, 0);
  const size_t wire_length = packet->writeTo(raw);
  const bool queued = queueBeaconRadioFrame(raw, wire_length, slot.radio, effective.power);
  if (queued) getTables()->markSent(packet);
  releasePacket(packet);
  return queued;
}

uint32_t Mesh::beaconNodeId() const {
  return beacon::nodeId(self_id.pub_key);
}

} // namespace mesh

#endif // MESH_ENABLE_TX_BEACONS
