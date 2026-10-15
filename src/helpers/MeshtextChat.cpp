#include "MeshtextChat.h"

#if MESH_ENABLE_MESHTEXT_WIRE

#include <AES.h>
#include <string.h>

// Adapted from esp-cpp/espp, Copyright (c) 2022 esp-cpp, MIT license.
// Pinned source and complete notice: licenses/ESPP-Meshtext-MIT.txt.
// No upstream GPL implementation or generated schema code is included.
namespace mesh {
namespace mt {

const uint8_t DefaultPsk[16] = {
  0xd4, 0xf1, 0xbb, 0x3a, 0x20, 0x29, 0x07, 0x59,
  0xf0, 0xbc, 0xff, 0xab, 0xcf, 0x4e, 0x69, 0x01
};

namespace {

static constexpr uint32_t Broadcast = 0xffffffffU;
struct PresetInfo {
  Preset preset;
  const char* name;
  uint32_t bandwidth;
  uint8_t sf, cr;
};
const PresetInfo Presets[] = {
  {Preset::ShortTurbo, "ShortTurbo", 500000, 7, 5},
  {Preset::ShortFast, "ShortFast", 250000, 7, 5},
  {Preset::ShortSlow, "ShortSlow", 250000, 8, 5},
  {Preset::MediumTurbo, "MediumTurbo", 500000, 9, 5},
  {Preset::MediumFast, "MediumFast", 250000, 9, 5},
  {Preset::MediumSlow, "MediumSlow", 250000, 10, 5},
  {Preset::LongTurbo, "LongTurbo", 500000, 11, 8},
  {Preset::LongFast, "LongFast", 250000, 11, 5},
  {Preset::LongModerate, "LongMod", 125000, 11, 8},
  {Preset::LongSlow, "LongSlow", 125000, 12, 8},
};

const PresetInfo* presetInfo(Preset preset) {
  for (const auto& entry : Presets) if (entry.preset == preset) return &entry;
  return nullptr;
}

uint32_t nameHash(const char* name) {
  uint32_t hash = 5381;
  while (*name) hash = hash * 33U + static_cast<uint8_t>(*name++);
  return hash;
}

bool validName(const char* name) {
  if (name == nullptr || name[0] == 0) return false;
  for (size_t i = 1; i < 12; ++i) if (name[i] == 0) return true;
  return false;
}

bool equalPresetName(const char* a, const char* b) {
  while (*a && *b) {
    uint8_t first = static_cast<uint8_t>(*a++), second = static_cast<uint8_t>(*b++);
    if (first >= 'A' && first <= 'Z') first += 'a' - 'A';
    if (second >= 'A' && second <= 'Z') second += 'a' - 'A';
    if (first != second) return false;
  }
  return *a == *b;
}

uint8_t channelHash(const char* name, const uint8_t* key, size_t key_len) {
  uint8_t hash = 0;
  while (*name) hash ^= static_cast<uint8_t>(*name++);
  for (size_t i = 0; i < key_len; ++i) hash ^= key[i];
  return hash;
}

void put32(uint8_t* out, uint32_t value) {
  for (unsigned i = 0; i < 4; ++i) out[i] = static_cast<uint8_t>(value >> (8 * i));
}

uint32_t get32(const uint8_t* in) {
  return uint32_t(in[0]) | uint32_t(in[1]) << 8 | uint32_t(in[2]) << 16 | uint32_t(in[3]) << 24;
}

void erase(uint8_t* bytes, size_t size) {
  volatile uint8_t* dest = bytes;
  while (size--) *dest++ = 0;
}

bool validUtf8(const uint8_t* text, size_t length, size_t maximum, size_t* scalar_count = nullptr) {
  if (text == nullptr || length > maximum) return false;
  size_t scalars = 0;
  // Full UTF-8 validation: no NUL, overlong encoding, surrogate or >U+10FFFF.
  for (size_t i = 0; i < length;) {
    const uint8_t first = text[i++];
    ++scalars;
    if (first == 0) return false;
    if (first < 0x80) continue;
    unsigned continuation;
    uint8_t second_min = 0x80, second_max = 0xbf;
    if (first >= 0xc2 && first <= 0xdf) continuation = 1;
    else if (first >= 0xe0 && first <= 0xef) {
      continuation = 2;
      if (first == 0xe0) second_min = 0xa0;
      if (first == 0xed) second_max = 0x9f;
    } else if (first >= 0xf0 && first <= 0xf4) {
      continuation = 3;
      if (first == 0xf0) second_min = 0x90;
      if (first == 0xf4) second_max = 0x8f;
    } else return false;
    if (continuation > length - i || text[i] < second_min || text[i] > second_max) return false;
    ++i;
    while (--continuation) {
      if (text[i] < 0x80 || text[i] > 0xbf) return false;
      ++i;
    }
  }
  if (scalar_count != nullptr) *scalar_count = scalars;
  return true;
}

bool validText(const uint8_t* text, size_t length) {
  return length != 0 && validUtf8(text, length, MaxTextSize);
}

template <class Cipher>
bool crypt(const uint8_t* key, size_t key_len, uint32_t id, uint32_t from,
           uint8_t* data, size_t length) {
  Cipher cipher;
  if (!cipher.setKey(key, key_len)) return false;
  // Initial counter is packet ID as LE64 followed by sender as LE64. The
  // standard CTR counter increment is big-endian across the entire block.
  uint8_t counter[16] = {}, stream[16];
  put32(counter, id);
  put32(counter + 8, from);
  for (size_t offset = 0; offset < length;) {
    cipher.encryptBlock(stream, counter);
    for (int i = 15; i >= 0; --i) if (++counter[i] != 0) break;
    size_t block = length - offset;
    if (block > sizeof(stream)) block = sizeof(stream);
    for (size_t i = 0; i < block; ++i) data[offset + i] ^= stream[i];
    offset += block;
  }
  cipher.clear();
  erase(counter, sizeof(counter));
  erase(stream, sizeof(stream));
  return true;
}

bool cryptPayload(const uint8_t* key, size_t key_len, uint32_t id, uint32_t from,
                  uint8_t* data, size_t length) {
  if (key_len == 16) return crypt<AES128>(key, key_len, id, from, data, length);
  if (key_len == 32) return crypt<AES256>(key, key_len, id, from, data, length);
  return false;
}

size_t finishFrame(uint32_t from, uint32_t id, uint8_t hop_limit, const char* channel_name,
                   const uint8_t* key, size_t key_len, uint8_t* out, size_t data_size) {
  if (!cryptPayload(key, key_len, id, from, out + HeaderSize, data_size)) return 0;
  put32(out, Broadcast); put32(out + 4, from); put32(out + 8, id);
  out[12] = hop_limit | (hop_limit << 5);
  out[13] = channelHash(channel_name, key, key_len);
  out[14] = out[15] = 0;
  return HeaderSize + data_size;
}

struct Reader {
  const uint8_t* data;
  size_t size, pos = 0;

  Reader(const uint8_t* input, size_t length) : data(input), size(length) {}
  bool varint(uint64_t& value) {
    value = 0;
    for (unsigned i = 0; i < 10 && pos < size; ++i) {
      const uint8_t byte = data[pos++];
      if (i == 9 && (byte & 0xfe) != 0) return false;
      value |= uint64_t(byte & 0x7f) << (7 * i);
      if ((byte & 0x80) == 0) return i == 0 || byte != 0;
    }
    return false;
  }
  bool bytes(const uint8_t*& out, size_t& length) {
    uint64_t count;
    if (!varint(count) || count > size - pos) return false;
    length = static_cast<size_t>(count); out = data + pos; pos += length;
    return true;
  }
  bool fixed32(uint32_t& value) {
    if (size - pos < 4) return false;
    value = get32(data + pos); pos += 4; return true;
  }
  bool skip(uint8_t wire) {
    uint64_t ignored;
    const uint8_t* bytes_;
    size_t count;
    if (wire == 0) return varint(ignored);
    if (wire == 2) return bytes(bytes_, count);
    count = wire == 1 ? 8 : wire == 5 ? 4 : 0;
    if (count == 0 || count > size - pos) return false;
    pos += count; return true;
  }
};

bool textData(const uint8_t* data, size_t size, const uint8_t*& text, size_t& text_len) {
  Reader reader(data, size);
  bool have_port = false, have_text = false;
  while (reader.pos < reader.size) {
    uint64_t tag, value;
    if (!reader.varint(tag) || tag < 8 || tag > 0xffffffffULL) return false;
    const uint32_t field = static_cast<uint32_t>(tag >> 3);
    const uint8_t wire = tag & 7;
    if (field == 1) {
      if (have_port || wire != 0 || !reader.varint(value) || value != 1) return false;
      have_port = true; // TEXT_MESSAGE_APP
    } else if (field == 2) {
      if (have_text || wire != 2 || !reader.bytes(text, text_len)) return false;
      have_text = true;
    } else if (field == 3) {
      // A received request does not cause any response in this text-only engine.
      if (wire != 0 || !reader.varint(value) || value > 1) return false;
    } else if (field >= 4 && field <= 8) {
      uint32_t fixed;
      if (wire != 5 || !reader.fixed32(fixed)) return false;
      if (field == 4 && fixed != 0 && fixed != Broadcast) return false;
    } else if (!reader.skip(wire)) return false;
  }
  return have_port && have_text && validText(text, text_len);
}

} // namespace

bool parsePreset(const char* name, Preset& preset) {
  if (name == nullptr) return false;
  for (const auto& entry : Presets) {
    if (equalPresetName(entry.name, name)) { preset = entry.preset; return true; }
  }
  if (equalPresetName(name, "LongModerate")) { preset = Preset::LongModerate; return true; }
  return false;
}

const char* presetName(Preset preset) {
  const auto info = presetInfo(preset);
  return info == nullptr ? nullptr : info->name;
}

bool modemConfig(Region region, Preset preset, RadioProfileParams& params, uint32_t slot) {
  const auto info = presetInfo(preset);
  if (info == nullptr) return false;
  uint32_t start, end;
  switch (region) {
    case Region::US: start = 902000000; end = 928000000; break;
    case Region::EU868: start = 869400000; end = 869650000; break;
    case Region::EU433: start = 433000000; end = 434000000; break;
    case Region::ANZ: start = 915000000; end = 928000000; break;
    default: return false;
  }
  if (info->bandwidth > end - start) return false;
  const uint32_t slots = (end - start) / info->bandwidth;
  if (slot > slots) return false;
  const uint32_t index = slot == 0 ? nameHash(info->name) % slots : slot - 1;
  RadioProfileParams result;
  result.freq = (start + info->bandwidth / 2 + index * info->bandwidth) / 1000000.0f;
  result.bw = info->bandwidth / 1000.0f;
  result.sf = info->sf; result.cr = info->cr; result.preamble = 16;
  result.protocol = RadioProfileProtocol::Meshtext;
  params = result;
  return true;
}

// Keep the nRF52 codec entry points visible under LTO so release qualification
// can verify executable send/receive code instead of assuming a feature flag.
#if defined(NRF52_PLATFORM) && defined(__GNUC__)
__attribute__((noinline))
#endif
size_t encodeText(uint32_t from, uint32_t id, uint8_t hop_limit, const char* channel_name,
                  const uint8_t* key, size_t key_len, const char* text, size_t text_len,
                  uint8_t out[MaxFrameSize]) {
  if (out == nullptr || key == nullptr || (key_len != 16 && key_len != 32)
      || !validName(channel_name) || from == 0 || from == Broadcast || id == 0 || hop_limit > 7
      || !validText(reinterpret_cast<const uint8_t*>(text), text_len)) return 0;
  uint8_t* data = out + HeaderSize;
  size_t count = 0;
  data[count++] = 0x08; data[count++] = 1; // Data.portnum = TEXT_MESSAGE_APP
  data[count++] = 0x12; // Data.payload
  if (text_len >= 128) data[count++] = static_cast<uint8_t>(text_len) | 0x80;
  data[count++] = static_cast<uint8_t>(text_len >> (text_len >= 128 ? 7 : 0));
  memcpy(data + count, text, text_len); count += text_len;
  // Data.bitfield field 9, bit 0: allow a receiving gateway to upload to MQTT.
  // This is consent, not the radio header's via_mqtt origin flag.
  data[count++] = 0x48; data[count++] = 1;
  return finishFrame(from, id, hop_limit, channel_name, key, key_len, out, count);
}

size_t encodeNodeInfo(uint32_t from, uint32_t id, uint8_t hop_limit, const char* channel_name,
                      const uint8_t* key, size_t key_len, const char* long_name, size_t long_name_len,
                      const char* short_name, size_t short_name_len, uint8_t out[MaxFrameSize]) {
  size_t short_characters = 0;
  if (out == nullptr || key == nullptr || (key_len != 16 && key_len != 32)
      || !validName(channel_name) || from <= 3 || from == Broadcast || id == 0 || hop_limit > 7
      || !validUtf8(reinterpret_cast<const uint8_t*>(long_name), long_name_len, MaxLongNameSize)
      || short_name_len == 0
      || !validUtf8(reinterpret_cast<const uint8_t*>(short_name), short_name_len,
                    MaxShortNameSize, &short_characters)
      || short_characters > MaxShortNameCharacters) return 0;

  // Handwritten standard protobuf wire fields, not generated schema code.
  // User: id=1, long_name=2, short_name=3, role=7, is_unmessagable=9.
  // All individual strings and the complete User payload fit one-byte lengths.
  static_assert(11 + 2 + MaxLongNameSize + 2 + MaxShortNameSize + 4 < 128,
                "NodeInfo requires bounded one-byte protobuf lengths");
  static const char Hex[] = "0123456789abcdef";
  uint8_t* data = out + HeaderSize;
  size_t count = 0;
  data[count++] = 0x08; data[count++] = 4; // Data.portnum = NODEINFO_APP.
  data[count++] = 0x12; data[count++] = 0; // Data.payload length set below.
  data[count++] = 0x0a; data[count++] = 9; data[count++] = '!';
  for (unsigned i = 0; i < 8; ++i) data[count++] = Hex[(from >> (28 - 4 * i)) & 15];
  if (long_name_len != 0) {
    data[count++] = 0x12; data[count++] = static_cast<uint8_t>(long_name_len);
    memcpy(data + count, long_name, long_name_len); count += long_name_len;
  }
  data[count++] = 0x1a; data[count++] = static_cast<uint8_t>(short_name_len);
  memcpy(data + count, short_name, short_name_len); count += short_name_len;
  data[count++] = 0x38; data[count++] = 1; // Config.DeviceConfig.Role.CLIENT_MUTE.
  data[count++] = 0x48; data[count++] = 1; // User.is_unmessagable explicitly true.
  data[3] = static_cast<uint8_t>(count - 4);
  data[count++] = 0x48; data[count++] = 1; // Data.bitfield: MQTT upload consent.
  return finishFrame(from, id, hop_limit, channel_name, key, key_len, out, count);
}

#if defined(NRF52_PLATFORM) && defined(__GNUC__)
__attribute__((noinline))
#endif
bool decodeText(const uint8_t* raw, size_t length, const char* channel_name,
                const uint8_t* key, size_t key_len, uint32_t local_node, DecodedText& decoded) {
  if (raw == nullptr || key == nullptr || (key_len != 16 && key_len != 32)
      || !validName(channel_name) || length <= HeaderSize || length > HeaderSize + MaxDataSize
      || get32(raw) != Broadcast || raw[13] != channelHash(channel_name, key, key_len)) return false;
  const uint32_t from = get32(raw + 4), id = get32(raw + 8);
  if (from == 0 || from == Broadcast || from == local_node || id == 0) return false;
  uint8_t data[MaxDataSize];
  const size_t data_size = length - HeaderSize;
  memcpy(data, raw + HeaderSize, data_size);
  if (!cryptPayload(key, key_len, id, from, data, data_size)) {
    erase(data, data_size); return false;
  }
  const uint8_t* text = nullptr;
  size_t text_len = 0;
  const bool valid = textData(data, data_size, text, text_len);
  if (valid) {
    decoded.from = from; decoded.id = id;
    decoded.hop_limit = raw[12] & 7; decoded.hop_start = raw[12] >> 5;
    decoded.hops = decoded.hop_start >= decoded.hop_limit ? decoded.hop_start - decoded.hop_limit : 0;
    decoded.text_len = static_cast<uint16_t>(text_len);
    memcpy(decoded.text, text, text_len); decoded.text[text_len] = 0;
  }
  erase(data, data_size);
  return valid;
}

bool SeenPackets::seen(uint32_t from, uint32_t id) const {
  if (from == 0 || from == Broadcast || id == 0) return false;
  for (const auto& entry : entries_) if (entry.from == from && entry.id == id) return true;
  return false;
}

bool SeenPackets::remember(uint32_t from, uint32_t id) {
  if (from == 0 || from == Broadcast || id == 0 || seen(from, id)) return false;
  entries_[next_].from = from; entries_[next_].id = id;
  next_ = (next_ + 1) % Capacity;
  return true;
}

void SeenPackets::clear() {
  for (auto& entry : entries_) { entry.from = 0; entry.id = 0; }
  next_ = 0;
}

} // namespace mt
} // namespace mesh

#endif // MESH_ENABLE_MESHTEXT_CHAT
