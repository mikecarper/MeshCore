#pragma once

#include <RadioProfiles.h>
#include <stddef.h>
#include <string.h>

namespace mesh { namespace companion {

enum class TrafficKind : uint8_t { Other, Media, Ssh };

// Channel application IDs, not new MeshCore packet types. SSH is an opt-in
// contract for tunnel clients, not a claim that opaque bytes can be inspected.
static constexpr uint16_t MEDIA_CHANNEL_DATA_TYPE = 0x5643; // Open TAK 'VC'
static constexpr uint16_t SSH_CHANNEL_DATA_TYPE = 0x5353;   // 'SS'

inline TrafficKind groupTrafficKind(uint16_t data_type) {
  return data_type == MEDIA_CHANNEL_DATA_TYPE ? TrafficKind::Media
      : data_type == SSH_CHANNEL_DATA_TYPE ? TrafficKind::Ssh : TrafficKind::Other;
}

inline TrafficKind rawTrafficKind(const uint8_t* data, size_t length) {
  if (!data || !length) return TrafficKind::Other;
  // Open TAK's VE3/VOX binary clip packets also carry photos. Do not capture
  // every RAW_CUSTOM application just because the media app uses that API.
  if ((data[0] == 0x56 && length >= 7) || (data[0] == 0x57 && length >= 8))
    return TrafficKind::Media;
  if (data[0] == 0x72 && length >= 13) {
    const uint8_t flags = data[5];
    const size_t count = data[12];
    if ((flags & ~7U) || (!(flags & 1) && count) || count > length - 13)
      return TrafficKind::Other;
    const size_t end = 13 + count;
    if (!(flags & 4)) return length == end ? TrafficKind::Media : TrafficKind::Other;
    if (length > end && data[end] <= 63 && length - end - 1 == data[end])
      return TrafficKind::Media;
    return TrafficKind::Other;
  }
  // Tunnel apps must opt in on EVERY packet, including their encrypted data,
  // ACKs and retries: ['S']['S']['H'][version=1][application bytes...]. The
  // envelope is passed through unchanged; it is not an SSH implementation.
  if (length >= 5 && !memcmp(data, "SSH\x01", 4)) return TrafficKind::Ssh;
  return TrafficKind::Other;
}

inline uint8_t trafficPolicy(TrafficKind kind, uint8_t media, uint8_t ssh) {
  return kind == TrafficKind::Media ? media
      : kind == TrafficKind::Ssh ? ssh : static_cast<uint8_t>(RADIO_TX_AUTO);
}

inline uint8_t trafficTransmitMask(uint8_t policy, const RadioProfiles* profiles) {
  uint8_t mask = policy == RADIO_TX_AUTO ? (profiles ? profiles->transmitMask(0) : 1)
      : explicitRadioTxMask(policy, profiles && profiles->canTransmit(1));
  // Match Dispatcher: MeshCore datagrams cannot be sent on a Meshtext profile,
  // even if that profile is configured rxtx.
  if (profiles) {
    for (uint8_t i = 0; i < 2; ++i) {
      if (profiles->params(i).protocol != RadioProfileProtocol::MeshCore)
        mask &= static_cast<uint8_t>(~(1U << i));
    }
  }
  return mask;
}

struct TrafficRouteCommand {
  bool matched = false;
  bool valid = false;
  bool set = false;
  TrafficKind kind = TrafficKind::Other;
  uint8_t policy = RADIO_TX_AUTO;
};

inline TrafficRouteCommand parseTrafficRouteCommand(const char* text) {
  TrafficRouteCommand result;
  if (!text) return result;
  if (!strncmp(text, "get ", 4)) result.set = false;
  else if (!strncmp(text, "set ", 4)) result.set = true;
  else return result;
  text += 4;
  if (!strncmp(text, "tx.media", 8)) result.kind = TrafficKind::Media;
  else if (!strncmp(text, "tx.ssh", 6)) result.kind = TrafficKind::Ssh;
  else return result;
  text += result.kind == TrafficKind::Media ? 8 : 6;
  if (*text && *text != ' ' && *text != '\t') return result;
  result.matched = true;
  while (*text == ' ' || *text == '\t') ++text;
  const char* end = text + strlen(text);
  while (end > text && (end[-1] == ' ' || end[-1] == '\t')) --end;
  if (!result.set) { result.valid = end == text; return result; }
  char value[8];
  const size_t length = end - text;
  if (!length || length >= sizeof(value)) return result;
  memcpy(value, text, length);
  value[length] = 0;
  result.valid = parseRadioTxPolicy(value, result.policy);
  return result;
}

} } // namespace mesh::companion
