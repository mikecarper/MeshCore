#pragma once

#include <stddef.h>
#include <stdint.h>
#include <RadioProfiles.h>
#include "MeshtextConfig.h"

// Bounded adaptation of MIT-licensed esp-cpp/espp wire helpers.
// See licenses/ESPP-Meshtext-MIT.txt for the pinned source and license.
// Meshtext is a bounded text interoperability component, not a peer firmware fork.
// NodeInfo is a separate GPS-free User packet; received NodeInfo stays ignored.
namespace mesh {
namespace mt {

enum class Region : uint8_t { Unset, US, EU868, EU433, ANZ };
enum class Preset : uint8_t {
  ShortTurbo, ShortFast, ShortSlow, MediumTurbo, MediumFast, MediumSlow,
  LongTurbo, LongFast, LongModerate, LongSlow
};

// ASCII case-insensitive alias parsing; wire/default names retain stock case.
bool parsePreset(const char* name, Preset& preset);
// The stock default channel name; LongModerate uses "LongMod".
const char* presetName(Preset preset);
// freq is MHz, bw is kHz; slot is 1-based, or 0 for the preset-name hash.
// Invalid regions/presets/slots are rejected, leaving params unchanged.
bool modemConfig(Region region, Preset preset, RadioProfileParams& params, uint32_t slot = 0);

static constexpr size_t HeaderSize = 16;
static constexpr size_t MaxFrameSize = 255;
static constexpr size_t MaxTextSize = 228;
// Keep the existing text limit with port/payload (5B) and MQTT consent (2B).
// The peer's 233B limit applies to Data.payload, not the complete protobuf.
static constexpr size_t MaxDataSize = MaxTextSize + 7;
static_assert(HeaderSize + MaxDataSize <= MaxFrameSize, "Meshtext frame exceeds radio limit");
static constexpr size_t MaxLongNameSize = 24; // Current stock User.long_name bound.
static constexpr size_t MaxShortNameSize = 4; // Stock max_size:5 includes NUL.
static constexpr size_t MaxShortNameCharacters = 4; // Unicode scalar values.
extern const uint8_t DefaultPsk[16]; // Expanded AQ== public key; never a hashtag hash.

struct DecodedText {
  uint32_t from = 0, id = 0;
  uint8_t hops = 0, hop_limit = 0, hop_start = 0;
  uint16_t text_len = 0;
  char text[MaxTextSize + 1] = {};
};

// Expanded AES-128/256 PSK only. No unencrypted-key fallback. Names are exact,
// canonical peer channel names (<12 bytes), without local display markers.
// Outgoing text and NodeInfo permit peer MQTT upload (Data.bitfield
// bit 0); this neither enables a local MQTT client nor marks traffic via_mqtt.
// Returns 0 for invalid arguments, malformed UTF-8, embedded NUL or overflow.
size_t encodeText(uint32_t from, uint32_t id, uint8_t hop_limit, const char* channel_name,
                  const uint8_t* key, size_t key_len, const char* text, size_t text_len,
                  uint8_t out[MaxFrameSize]);
// Explicit broadcast NODEINFO_APP containing only User id, long/short names,
// CLIENT_MUTE by default (no forwarding), and is_unmessagable=true (no DMs).
// Explicitly enabled rebroadcast advertises CLIENT instead, without adding
// any fields or changing encryption, names, hop policy or MQTT consent.
// Names use explicit UTF-8 byte lengths, without their terminating NUL. Long
// names may be empty; short names require 1..4 UTF-8 bytes (stock wire bound).
// This can hold four ASCII characters but fewer multi-byte characters. Longer
// names are rejected, not byte-truncated. No MAC, PSK, public key or MC identity
// is copied. No GPS/position or ACK/response is requested. The caller controls
// explicit and first-successful-chat announcements and bounded retries.
size_t encodeNodeInfo(uint32_t from, uint32_t id, uint8_t hop_limit, const char* channel_name,
                      const uint8_t* key, size_t key_len, const char* long_name, size_t long_name_len,
                      const char* short_name, size_t short_name_len, uint8_t out[MaxFrameSize],
                      bool rebroadcast = false);
// Only broadcast TEXT_MESSAGE_APP is delivered. No DMs, ACKs, administrative
// handling or automatic transmissions occur in this decoder; a caller can
// separately opt into bounded relay handling. AES-CTR is not authenticated;
// a successful parse is not a proof of sender identity or PSK authenticity.
// On failure, decoded is left unchanged. Caller records dedup only on success.
bool decodeText(const uint8_t* raw, size_t length, const char* channel_name,
                const uint8_t* key, size_t key_len, uint32_t local_node, DecodedText& decoded);

// Shared across channel slots. Packet IDs must never be reused with one sender
// and PSK (CTR nonce reuse). Call remember only after valid RX or successful TX.
class SeenPackets {
 public:
  static constexpr size_t Capacity = 32;
  bool seen(uint32_t from, uint32_t id) const;
  bool remember(uint32_t from, uint32_t id); // false for duplicate/invalid pair
  void clear();
 private:
  struct Entry { uint32_t from = 0, id = 0; } entries_[Capacity];
  uint8_t next_ = 0;
};

} // namespace mt
} // namespace mesh
