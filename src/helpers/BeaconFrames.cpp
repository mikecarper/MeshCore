#include "BeaconFrames.h"

#if MESH_ENABLE_TX_BEACONS

#include "UTF8Helpers.h"
#include <Utils.h>
#include <string.h>

namespace mesh { namespace beacon {

const uint8_t PublicKey[16] = {
  0x8b, 0x33, 0x87, 0xe9, 0xc5, 0xcd, 0xea, 0x6a,
  0xc9, 0xe5, 0xed, 0xba, 0xa1, 0x15, 0xcd, 0x72
};

bool validText(const char* text) {
  if (!text) return false;
  const size_t length = strnlen(text, MaxTextBytes + 1);
  if (!length || length > MaxTextBytes
      || validUtf8PrefixLength(text, length) != length) return false;
  for (size_t i = 0; i < length; ++i) {
    const uint8_t byte = static_cast<uint8_t>(text[i]);
    if (byte < 32 || byte == 127) return false;
  }
  return true;
}

size_t formatText(const char* node_name, const char* text, char* out, size_t capacity) {
  if (!node_name || !out || !validText(text)) return 0;
  const size_t name_length = strnlen(node_name, MaxNameBytes + 1);
  const size_t text_length = strlen(text);
  if (!name_length || name_length > MaxNameBytes
      || validUtf8PrefixLength(node_name, name_length) != name_length
      || capacity < name_length + 2 + text_length + 1) return 0;
  // The name and message use the normal MeshCore visible sender convention.
  // A colon in a configured name must not create a second sender delimiter.
  for (size_t i = 0; i < name_length; ++i) out[i] = node_name[i] == ':' ? ';' : node_name[i];
  out[name_length] = ':';
  out[name_length + 1] = ' ';
  memcpy(out + name_length + 2, text, text_length);
  const size_t length = name_length + 2 + text_length;
  out[length] = 0;
  return length;
}

size_t publicPayload(uint32_t timestamp, const char* node_name, const char* text,
                     uint8_t* out, size_t capacity) {
  if (!out || capacity < 6) return 0;
  char formatted[MaxFormattedBytes + 1];
  const size_t length = formatText(node_name, text, formatted, sizeof(formatted));
  if (!length || capacity < 5 + length) return 0;
  for (unsigned i = 0; i < 4; ++i) out[i] = static_cast<uint8_t>(timestamp >> (8 * i));
  out[4] = 0; // TXT_TYPE_PLAIN, no ACK/response requested.
  memcpy(out + 5, formatted, length);
  return 5 + length;
}

size_t meshtextFrame(mt::Preset preset, uint32_t from, uint32_t id, uint8_t hops,
                     const char* node_name, const char* text,
                     uint8_t out[mt::MaxFrameSize]) {
#if MESH_ENABLE_MESHTEXT_WIRE
  const char* channel = mt::presetName(preset);
  if (!channel || !out) return 0;
  char formatted[MaxFormattedBytes + 1];
  const size_t length = formatText(node_name, text, formatted, sizeof(formatted));
  return length ? mt::encodeText(from, id, hops, channel, mt::DefaultPsk,
      sizeof(mt::DefaultPsk), formatted, length, out) : 0;
#else
  (void)preset; (void)from; (void)id; (void)hops;
  (void)node_name; (void)text; (void)out;
  return 0;
#endif
}

uint32_t nodeId(const uint8_t public_key[32]) {
  if (!public_key) return 0;
  static const uint8_t domain[] = {'b', 'e', 'a', 'c', 'o', 'n'};
  uint8_t digest[4], chat_digest[4];
  Utils::sha256(digest, sizeof(digest), public_key, 32, domain, sizeof(domain));
  Utils::sha256(chat_digest, sizeof(chat_digest), public_key, 32);
  const auto number = [](const uint8_t bytes[4]) {
    return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8)
        | (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
  };
  uint32_t value = number(digest), chat = number(chat_digest);
  if (value < 4 || value == UINT32_MAX) value = 0x0badf00d;
  if (chat < 4 || chat == UINT32_MAX) chat = 0x0badf00d;
  // Domain separation avoids systematic reuse. Also cover a truncated-hash
  // collision with the default Companion identity without changing its ID.
  if (value == chat) value = value == UINT32_MAX - 1 ? 4 : value + 1;
  return value;
}

} } // namespace mesh::beacon

#endif // MESH_ENABLE_TX_BEACONS
