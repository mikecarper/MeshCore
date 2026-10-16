"""Execute the bounded MT codec with real Crypto AES and an independent oracle.

No PlatformIO, hardware or downloaded sources are used. Set MESHCORE_CRYPTO_DIR
to an existing rweather/Crypto checkout, or use the cached PlatformIO dependency.
The Python cryptography package independently generates encrypted wire vectors.
"""

import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
FROM, PACKET_ID = 0x11223344, 0x89ABCDEF


def varint(value):
    encoded = bytearray()
    while value >= 128:
        encoded.append((value & 127) | 128)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def data_text(text):
    return b"\x08\x01\x12" + varint(len(text)) + text


def data_nodeinfo(long_name, short_name, sender, rebroadcast=False):
    # Independent handwritten schema oracle: User fields 1/2/3/7/9 only.
    user_id = ("!%08x" % sender).encode("ascii")
    user = b"\x0a" + varint(len(user_id)) + user_id
    if long_name:
        user += b"\x12" + varint(len(long_name)) + long_name
    user += b"\x1a" + varint(len(short_name)) + short_name
    user += b"\x38" + bytes([0 if rebroadcast else 1]) + b"\x48\x01"
    return b"\x08\x04\x12" + varint(len(user)) + user + b"\x48\x01"


def reference_frame(data, name="LongFast", key=DEFAULT_KEY, *, to=0xFFFFFFFF,
                    sender=FROM, packet_id=PACKET_ID, flags=0x63):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    channel_hash = 0
    for byte in name.encode() + key:
        channel_hash ^= byte
    header = struct.pack("<III4B", to, sender, packet_id, flags, channel_hash, 0, 0)
    encryptor = Cipher(algorithms.AES(key), modes.CTR(struct.pack("<QQ", packet_id, sender))).encryptor()
    return header + encryptor.update(data) + encryptor.finalize()


def array(value):
    return "{" + ",".join(map(str, value)) + "}"


def literal(value):
    return '"' + "".join("\\x%02x" % byte for byte in value) + '"'


HARNESS = r'''
#include <helpers/MeshtextChat.h>
#include <helpers/MeshtextDelivery.h>
#include <cassert>
#include <cstddef>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <initializer_list>
using namespace mesh::mt;
struct Golden {
  uint8_t key[32]; size_t key_len; const char* name; const char* text; size_t text_len;
  uint8_t frame[255]; size_t frame_len;
};
static const Golden golden[] = { @GOLDEN@ };
// Stock 2.7.26 Router::perhapsEncode marks Data.bitfield present even when
// config_ok_to_mqtt and want_response are both false: field 9 is 0x48,0x00.
static const Golden stock_zero_bitfield[] = { @STOCK_ZERO_BITFIELD@ };
struct NodeInfoGolden {
  uint8_t key[32]; size_t key_len; const char* name;
  const char* long_name; size_t long_name_len; const char* short_name; size_t short_name_len;
  uint32_t from, id; uint8_t hops; uint8_t frame[255]; size_t frame_len;
};
static const NodeInfoGolden node_info_golden[] = { @NODEINFO_GOLDEN@ };
static const NodeInfoGolden relay_node_info_golden[] = { @RELAY_NODEINFO_GOLDEN@ };
struct Malformed { const char* label; uint8_t frame[255]; size_t length; };
static const Malformed malformed[] = { @MALFORMED@ };
struct Modem {
  Region region; Preset preset; bool valid; float frequency, bandwidth; uint8_t sf, cr;
};
static const Modem modems[] = { @MODEMS@ };
static void presets() {
  Preset preset = Preset::LongFast;
  for (const auto* name : {"ShortTurbo", "ShortFast", "ShortSlow", "MediumTurbo", "MediumFast",
                          "MediumSlow", "LongTurbo", "LongFast", "LongMod", "LongSlow"}) {
    assert(parsePreset(name, preset)); assert(!strcmp(presetName(preset), name));
  }
  assert(parsePreset("LongModerate", preset) && preset == Preset::LongModerate);
  assert(parsePreset("longfast", preset) && preset == Preset::LongFast);
  assert(parsePreset("shortturbo", preset) && preset == Preset::ShortTurbo);
  assert(parsePreset("LoNgTuRbO", preset) && preset == Preset::LongTurbo);
  assert(parsePreset("LONGMODERATE", preset) && preset == Preset::LongModerate);
  for (const auto* invalid : {"", "Long-Fast", "LongUltra", "mt-LongFast", "LongFast "}) {
    preset = Preset::LongFast;
    assert(!parsePreset(invalid, preset) && preset == Preset::LongFast);
  }
  assert(!parsePreset(nullptr, preset));
  assert(presetName(static_cast<Preset>(255)) == nullptr);
  for (const auto& test : modems) {
    mesh::RadioProfileParams params;
    params.freq = 123; const auto original = params;
    assert(modemConfig(test.region, test.preset, params) == test.valid);
    if (!test.valid) { assert(params == original); continue; }
    assert(std::fabs(params.freq - test.frequency) < 0.0001f);
    assert(params.bw == test.bandwidth && params.sf == test.sf && params.cr == test.cr);
    assert(params.preamble == 16 && params.protocol == mesh::RadioProfileProtocol::Meshtext);
    assert(modemConfig(test.region, test.preset, params, 1));
    const float start = test.region == Region::US ? 902 : test.region == Region::EU868 ? 869.4f
                      : test.region == Region::EU433 ? 433 : 915;
    const uint32_t width = test.region == Region::US ? 26000 : test.region == Region::EU868 ? 250
                         : test.region == Region::EU433 ? 1000 : 13000;
    const uint32_t slots = width / static_cast<uint32_t>(test.bandwidth);
    assert(std::fabs(params.freq - (start + test.bandwidth / 2000)) < 0.0001f);
    assert(modemConfig(test.region, test.preset, params, slots));
    const auto last = params;
    assert(!modemConfig(test.region, test.preset, params, slots + 1) && params == last);
    assert(!modemConfig(test.region, test.preset, params, 0xffffffffU) && params == last);
  }
  mesh::RadioProfileParams params;
  assert(!modemConfig(Region::Unset, Preset::LongFast, params));
  assert(!modemConfig(static_cast<Region>(255), Preset::LongFast, params));
  assert(!modemConfig(Region::US, static_cast<Preset>(255), params));
  puts("PASS presets");
}
static void frames() {
  for (const auto& test : golden) {
    uint8_t frame[MaxFrameSize + 2]; memset(frame, 0xa5, sizeof(frame));
    const size_t length = encodeText(0x11223344, 0x89abcdef, 3, test.name, test.key, test.key_len,
                                     test.text, test.text_len, frame);
    assert(length == test.frame_len && !memcmp(frame, test.frame, length));
    assert(frame[MaxFrameSize] == 0xa5 && frame[MaxFrameSize + 1] == 0xa5);
    DecodedText decoded;
    assert(decodeText(test.frame, test.frame_len, test.name, test.key, test.key_len, 0x99887766, decoded));
    assert(decoded.from == 0x11223344 && decoded.id == 0x89abcdef);
    assert(decoded.hop_limit == 3 && decoded.hop_start == 3 && decoded.hops == 0);
    assert(decoded.text_len == test.text_len && !memcmp(decoded.text, test.text, test.text_len));
    assert(decoded.text[decoded.text_len] == 0);
    memcpy(frame, test.frame, test.frame_len); frame[12] = (7 << 5) | 2;
    assert(decodeText(frame, length, test.name, test.key, test.key_len, 0x99887766, decoded));
    assert(decoded.hops == 5 && decoded.hop_start == 7 && decoded.hop_limit == 2);
    frame[12] = 3; // older hop-start-less packets remain readable
    assert(decodeText(frame, length, test.name, test.key, test.key_len, 0x99887766, decoded));
    assert(decoded.hops == 0);
    assert(!decodeText(frame, length, test.name, test.key, test.key_len, 0x11223344, decoded));
    frame[13] ^= 1;
    assert(!decodeText(frame, length, test.name, test.key, test.key_len, 0x99887766, decoded));
  }
  assert(!memcmp(DefaultPsk, golden[0].key, 16));
  puts("PASS frames");
}
static void mqtt_defaults() {
  uint8_t keys[2][32] = {};
  memcpy(keys[0], DefaultPsk, 16);
  for (unsigned i = 0; i < 32; ++i) keys[1][i] = i;
  char text[MaxTextSize]; memset(text, 'x', sizeof(text));
  for (unsigned i = 0; i < 2; ++i) {
    const size_t key_len = i == 0 ? 16 : 32;
    uint8_t frame[MaxFrameSize + 1]; memset(frame, 0xa5, sizeof(frame));
    size_t count = encodeText(0x11223344, 0x89abcdef, 7, "LongFast", keys[i], key_len,
                              text, sizeof(text), frame);
    assert(count == HeaderSize + MaxDataSize && count == 251);
    assert(frame[count] == 0xa5 && frame[MaxFrameSize] == 0xa5);
    printf("TEXT%u ", unsigned(key_len * 8));
    for (size_t j = 0; j < count; ++j) printf("%02x", frame[j]);
    puts("");
    count = encodeNodeInfo(0x11223344, 0x89abcdef, 7, "LongFast", keys[i], key_len,
                           "MeshCore", 8, "CORE", 4, frame);
    assert(count != 0);
    printf("NODEINFO%u ", unsigned(key_len * 8));
    for (size_t j = 0; j < count; ++j) printf("%02x", frame[j]);
    puts("");
  }
  puts("PASS mqtt_defaults");
}
static void malformed_frames() {
  for (const auto& test : malformed) {
    DecodedText decoded;
    decoded.id = 123; strcpy(decoded.text, "unchanged"); decoded.text_len = 9;
    if (decodeText(test.frame, test.length, "LongFast", DefaultPsk, 16, 0x99887766, decoded)) {
      fprintf(stderr, "accepted malformed vector: %s\n", test.label); assert(false);
    }
    assert(decoded.id == 123 && decoded.text_len == 9 && !strcmp(decoded.text, "unchanged"));
  }
  puts("PASS malformed");
}
static void node_info_frames() {
  static_assert(MaxLongNameSize == 24, "stock long-name limit");
  static_assert(MaxShortNameSize == 4, "stock short-name max_size:5 includes NUL");
  for (const auto& test : node_info_golden) {
    uint8_t frame[MaxFrameSize + 2]; memset(frame, 0xa5, sizeof(frame));
    const size_t length = encodeNodeInfo(test.from, test.id, test.hops, test.name, test.key, test.key_len,
                                         test.long_name, test.long_name_len,
                                         test.short_name, test.short_name_len, frame);
    // Full independent encrypted-frame equality also proves exact standard User
    // fields, matching nonce/header, and absence of Data.want_response/keys/MAC.
    assert(length == test.frame_len && !memcmp(frame, test.frame, length));
    assert(frame[length] == 0xa5 && frame[MaxFrameSize] == 0xa5 && frame[MaxFrameSize + 1] == 0xa5);
    assert((frame[12] & 0x18) == 0); // Neither want_ack nor via_mqtt.
    assert((frame[12] & 7) == test.hops && (frame[12] >> 5) == test.hops);
    assert(frame[14] == 0 && frame[15] == 0);
    DecodedText decoded; decoded.id = 123; strcpy(decoded.text, "unchanged");
    assert(!decodeText(frame, length, test.name, test.key, test.key_len, 0x99887766, decoded));
    assert(decoded.id == 123 && !strcmp(decoded.text, "unchanged")); // NodeInfo RX still ignored.
  }
  puts("PASS nodeinfo");
}
static void node_info_bounds() {
  uint8_t frame[MaxFrameSize]; memset(frame, 0xa5, sizeof(frame));
  const auto invalid = [&](uint32_t from, uint32_t id, uint8_t hops, const char* name,
                           const uint8_t* key, size_t key_len, const char* long_name, size_t long_len,
                           const char* short_name, size_t short_len) {
    assert(!encodeNodeInfo(from, id, hops, name, key, key_len, long_name, long_len, short_name, short_len, frame));
    for (uint8_t byte : frame) assert(byte == 0xa5); // Rejection writes no partial packet.
  };
  char long_name[MaxLongNameSize + 1]; memset(long_name, 'L', sizeof(long_name));
  char short_name[MaxShortNameSize + 1]; memset(short_name, 'S', sizeof(short_name));
  for (uint32_t from : {0U, 1U, 2U, 3U, 0xffffffffU})
    invalid(from, 1, 7, "LongFast", DefaultPsk, 16, "long", 4, "shrt", 4);
  invalid(4, 0, 7, "LongFast", DefaultPsk, 16, "long", 4, "shrt", 4);
  invalid(4, 1, 8, "LongFast", DefaultPsk, 16, "long", 4, "shrt", 4);
  for (const auto* name : {static_cast<const char*>(nullptr), "", "123456789012"})
    invalid(4, 1, 7, name, DefaultPsk, 16, "long", 4, "shrt", 4);
  for (size_t key_len : {size_t(0), size_t(1), size_t(15), size_t(17), size_t(31), size_t(33)})
    invalid(4, 1, 7, "LongFast", DefaultPsk, key_len, "long", 4, "shrt", 4);
  invalid(4, 1, 7, "LongFast", nullptr, 16, "long", 4, "shrt", 4);
  invalid(4, 1, 7, "LongFast", DefaultPsk, 16, nullptr, 0, "shrt", 4);
  invalid(4, 1, 7, "LongFast", DefaultPsk, 16, "long", 4, nullptr, 4);
  invalid(4, 1, 7, "LongFast", DefaultPsk, 16, "long", 4, "", 0);
  for (size_t size : {MaxLongNameSize + 1, size_t(-1)})
    invalid(4, 1, 7, "LongFast", DefaultPsk, 16, long_name, size, "shrt", 4);
  for (size_t size : {MaxShortNameSize + 1, size_t(-1)})
    invalid(4, 1, 7, "LongFast", DefaultPsk, 16, "long", 4, short_name, size);
  const char* bad_names[] = {"\0", "\xc0\xaf", "\xe0\x80\x80", "\xed\xa0\x80",
                            "\xf0\x80\x80\x80", "\xf4\x90\x80\x80", "\x80", "\xc2", "\xe2\x82"};
  const size_t bad_lengths[] = {1, 2, 3, 3, 4, 4, 1, 1, 2};
  for (size_t i = 0; i < sizeof(bad_lengths) / sizeof(bad_lengths[0]); ++i) {
    invalid(4, 1, 7, "LongFast", DefaultPsk, 16, bad_names[i], bad_lengths[i], "shrt", 4);
    invalid(4, 1, 7, "LongFast", DefaultPsk, 16, "long", 4, bad_names[i], bad_lengths[i]);
  }
  const char embedded_nul[] = {'a', 0, 'b'};
  invalid(4, 1, 7, "LongFast", DefaultPsk, 16, embedded_nul, 3, "shrt", 4);
  invalid(4, 1, 7, "LongFast", DefaultPsk, 16, "long", 4, embedded_nul, 3);
  assert(!encodeNodeInfo(4, 1, 7, "LongFast", DefaultPsk, 16, "long", 4, "shrt", 4, nullptr));
  // Exact-length byte arrays need no trailing NUL; the codec never scans them.
  assert(encodeNodeInfo(4, 1, 7, "LongFast", DefaultPsk, 16,
                        long_name, MaxLongNameSize, short_name, MaxShortNameSize, frame));
  assert(encodeNodeInfo(4, 1, 0, "LongFast", DefaultPsk, 16, "", 0, "a", 1, frame));
  // A single non-ASCII scalar can occupy the complete four-byte stock field.
  assert(encodeNodeInfo(4, 1, 3, "LongFast", DefaultPsk, 16, "\xc3\xa9", 2, "\xf0\x9f\x9a\x80", 4, frame));
  puts("PASS nodeinfo_bounds");
}
static void node_info_roles() {
  for (size_t i = 0; i < sizeof(node_info_golden) / sizeof(node_info_golden[0]); ++i) {
    const auto& original = node_info_golden[i];
    const auto& expected = relay_node_info_golden[i];
    uint8_t passive[MaxFrameSize], explicit_false[MaxFrameSize], forwarding[MaxFrameSize];
    const size_t count = encodeNodeInfo(original.from, original.id, original.hops,
        original.name, original.key, original.key_len, original.long_name,
        original.long_name_len, original.short_name, original.short_name_len, passive);
    assert(count == original.frame_len && !memcmp(passive, original.frame, count));
    assert(encodeNodeInfo(original.from, original.id, original.hops, original.name,
        original.key, original.key_len, original.long_name, original.long_name_len,
        original.short_name, original.short_name_len, explicit_false, false) == count);
    assert(!memcmp(passive, explicit_false, count));
    assert(encodeNodeInfo(expected.from, expected.id, expected.hops, expected.name,
        expected.key, expected.key_len, expected.long_name, expected.long_name_len,
        expected.short_name, expected.short_name_len, forwarding, true) == count);
    assert(count == expected.frame_len && !memcmp(forwarding, expected.frame, count));
    assert(!memcmp(passive, forwarding, HeaderSize));
    size_t changes = 0;
    for (size_t offset = HeaderSize; offset < count; ++offset) {
      if (passive[offset] != forwarding[offset]) {
        ++changes;
        assert((passive[offset] ^ forwarding[offset]) == 1);
      }
    }
    assert(changes == 1); // Only User.role changes; CTR leaves all other bytes identical.
    DecodedText decoded;
    assert(!decodeText(forwarding, count, expected.name, expected.key,
                       expected.key_len, 0x99887766, decoded)); // Still not text/GPS/DM.
  }
  puts("PASS nodeinfo_roles");
}
static void relay_matching() {
  struct PreviousPendingFrame {
    uint8_t bytes[MaxFrameSize] = {}, fingerprint[8] = {};
    uint32_t generation = 0, due = 0;
    uint16_t length = 0;
    uint8_t channel = 0, retries = 0;
    bool nodeinfo = false, sent = false, heard = false, cancelled = false;
  };
  static_assert(sizeof(PendingFrame) == sizeof(PreviousPendingFrame), "relay flag must reuse padding");
  static_assert(offsetof(PendingFrame, generation) == offsetof(PreviousPendingFrame, generation),
                "relay flag must not move existing aligned state");
  static_assert(offsetof(PendingFrame, relayed) == offsetof(PendingFrame, fingerprint) + 8,
                "relay flag belongs immediately after fingerprint");
  const auto& golden_frame = golden[0];
  PendingFrame pending;
  pending.length = golden_frame.frame_len;
  memcpy(pending.bytes, golden_frame.frame, pending.length);
  pending.sent = true;
  uint8_t copy[MaxFrameSize]; memcpy(copy, pending.bytes, pending.length);
  assert(!pending.relayed && !pending.matchesRelayCopy(copy, pending.length));
  assert(!pending.matchesEcho(copy, pending.length)); // Original copy has not been relayed.
  copy[12] = (copy[12] & 0xf8) | 2;
  assert(pending.matchesEcho(copy, pending.length));
  pending.relayed = true;
  assert(!pending.matchesEcho(copy, pending.length)); // Relays are not originated delivery ACKs.
  pending.bytes[12] = (pending.bytes[12] & 0xf8) | 2; // Planned relay remaining hops.
  pending.sent = false; // Cancellation is allowed before the first relay TX.
  assert(pending.matchesRelayCopy(copy, pending.length));
  for (uint8_t remaining : {0, 1, 2}) {
    copy[12] = (pending.bytes[12] & 0xf8) | remaining;
    copy[14] = 0x12; copy[15] = 0x34; // Mutable routing bytes do not defeat cancellation.
    assert(pending.matchesRelayCopy(copy, pending.length));
  }
  for (uint8_t remaining : {3, 4, 5, 6, 7}) {
    copy[12] = (pending.bytes[12] & 0xf8) | remaining;
    assert(!pending.matchesRelayCopy(copy, pending.length));
  }
  for (size_t offset = 0; offset < pending.length; ++offset) {
    memcpy(copy, pending.bytes, pending.length);
    if (offset == 12 || offset == 14 || offset == 15) continue;
    copy[offset] ^= 1;
    assert(!pending.matchesRelayCopy(copy, pending.length));
  }
  for (uint8_t flag : {0x08, 0x10, 0x20, 0x40, 0x80}) {
    memcpy(copy, pending.bytes, pending.length); copy[12] ^= flag;
    assert(!pending.matchesRelayCopy(copy, pending.length));
  }
  memcpy(copy, pending.bytes, pending.length);
  assert(!pending.matchesRelayCopy(nullptr, pending.length));
  assert(!pending.matchesRelayCopy(copy, pending.length - 1));
  assert(!pending.matchesRelayCopy(copy, pending.length + 1));
  pending.relayed = false;
  assert(!pending.matchesRelayCopy(copy, pending.length));
  pending.relayed = true;
  pending.length = HeaderSize;
  assert(!pending.matchesRelayCopy(copy, pending.length));
  pending.length = MaxFrameSize + 1;
  assert(!pending.matchesRelayCopy(copy, pending.length));
  pending.clear();
  assert(!pending.matchesRelayCopy(copy, 0));
  puts("PASS relay_matching");
}
static void argument_bounds() {
  uint8_t frame[MaxFrameSize] = {};
  char text[MaxTextSize + 2]; memset(text, 'x', sizeof(text));
  assert(encodeText(1, 1, 7, "LongFast", DefaultPsk, 16, text, MaxTextSize, frame));
  for (size_t length : {size_t(0), MaxTextSize + 1, size_t(0xffffffffU)})
    assert(!encodeText(1, 1, 3, "LongFast", DefaultPsk, 16, text, length, frame));
  assert(!encodeText(0, 1, 3, "LongFast", DefaultPsk, 16, text, 1, frame));
  assert(!encodeText(0xffffffffU, 1, 3, "LongFast", DefaultPsk, 16, text, 1, frame));
  assert(!encodeText(1, 0, 3, "LongFast", DefaultPsk, 16, text, 1, frame));
  assert(!encodeText(1, 1, 8, "LongFast", DefaultPsk, 16, text, 1, frame));
  assert(encodeText(1, 1, 3, "12345678901", DefaultPsk, 16, text, 1, frame));
  for (const auto* name : {static_cast<const char*>(nullptr), "", "123456789012", "01234567890123456789012345678901xx"})
    assert(!encodeText(1, 1, 3, name, DefaultPsk, 16, text, 1, frame));
  for (size_t key_length : {size_t(0), size_t(1), size_t(15), size_t(17), size_t(31), size_t(33)})
    assert(!encodeText(1, 1, 3, "LongFast", DefaultPsk, key_length, text, 1, frame));
  assert(!encodeText(1, 1, 3, "LongFast", nullptr, 16, text, 1, frame));
  assert(!encodeText(1, 1, 3, "LongFast", DefaultPsk, 16, nullptr, 1, frame));
  assert(!encodeText(1, 1, 3, "LongFast", DefaultPsk, 16, text, 1, nullptr));
  const char* invalid_utf8[] = {"\0", "\xc0\xaf", "\xe0\x80\x80", "\xed\xa0\x80",
                              "\xf0\x80\x80\x80", "\xf4\x90\x80\x80", "\x80", "\xc2", "\xe2\x82"};
  const size_t lengths[] = {1, 2, 3, 3, 4, 4, 1, 1, 2};
  for (size_t i = 0; i < sizeof(lengths) / sizeof(lengths[0]); ++i)
    assert(!encodeText(1, 1, 3, "LongFast", DefaultPsk, 16, invalid_utf8[i], lengths[i], frame));
  const char nul_text[] = {'a', 0, 'b'};
  assert(!encodeText(1, 1, 3, "LongFast", DefaultPsk, 16, nul_text, 3, frame));
  DecodedText decoded;
  assert(!decodeText(nullptr, 50, "LongFast", DefaultPsk, 16, 0, decoded));
  assert(!decodeText(frame, 0, "LongFast", DefaultPsk, 16, 0, decoded));
  assert(!decodeText(frame, 256, "LongFast", DefaultPsk, 16, 0, decoded));
  assert(!decodeText(frame, 50, "LongFast", nullptr, 16, 0, decoded));
  assert(!decodeText(frame, 50, nullptr, DefaultPsk, 16, 0, decoded));
  puts("PASS bounds");
}
static void extensions() {
  const uint8_t frame[] = @EXTENSION_FRAME@;
  DecodedText decoded;
  assert(decodeText(frame, sizeof(frame), "LongFast", DefaultPsk, 16, 0x99887766, decoded));
  assert(decoded.text_len == 1 && !strcmp(decoded.text, "x"));
  // Header ACK/request flags do not make this passive decoder transmit.
  const uint8_t requests[] = @REQUEST_FRAME@;
  assert(decodeText(requests, sizeof(requests), "LongFast", DefaultPsk, 16, 0x99887766, decoded));
  puts("PASS extensions");
}
static void stock_zero_bitfield_frames() {
  for (const auto& test : stock_zero_bitfield) {
    DecodedText decoded;
    assert(decodeText(test.frame, test.frame_len, test.name, test.key, test.key_len,
                      0x99887766, decoded));
    assert(decoded.from == 0x11223344 && decoded.id == 0x89abcdef);
    assert(decoded.hop_limit == 7 && decoded.hop_start == 7 && decoded.hops == 0);
    assert((test.frame[12] & 0x18) == 0); // Stock SDK requested neither ACK nor MQTT.
    assert(decoded.text_len == test.text_len && !memcmp(decoded.text, test.text, test.text_len));
    assert(decoded.text[decoded.text_len] == 0);
  }
  puts("PASS stock_zero_bitfield");
}
static void dedup() {
  SeenPackets seen;
  assert(!seen.seen(1, 1));
  assert(seen.remember(1, 1)); assert(seen.seen(1, 1)); assert(!seen.remember(1, 1));
  assert(seen.remember(2, 1)); // same ID from another sender is distinct
  assert(!seen.remember(0, 1) && !seen.remember(1, 0) && !seen.remember(0xffffffffU, 1));
  seen.clear(); assert(!seen.seen(1, 1));
  for (uint32_t i = 1; i <= SeenPackets::Capacity; ++i) assert(seen.remember(1, i));
  assert(seen.seen(1, 1)); assert(seen.remember(1, 33)); assert(!seen.seen(1, 1));
  // Invalid data never poisons dedup; the subsequent valid frame is accepted.
  seen.clear(); DecodedText decoded;
  for (const auto& test : malformed)
    if (decodeText(test.frame, test.length, "LongFast", DefaultPsk, 16, 0x99887766, decoded))
      seen.remember(decoded.from, decoded.id);
  assert(!seen.seen(0x11223344, 0x89abcdef));
  assert(decodeText(golden[0].frame, golden[0].frame_len, "LongFast", DefaultPsk, 16, 0x99887766, decoded));
  assert(seen.remember(decoded.from, decoded.id));
  assert(!seen.remember(decoded.from, decoded.id));
  // Successful own TX uses the same pair cache to suppress over-air echoes.
  seen.clear(); assert(seen.remember(0x11223344, 0x89abcdef));
  assert(seen.seen(0x11223344, 0x89abcdef));
  puts("PASS dedup");
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!strcmp(argv[1], "presets")) presets();
  else if (!strcmp(argv[1], "frames")) frames();
  else if (!strcmp(argv[1], "mqtt_defaults")) mqtt_defaults();
  else if (!strcmp(argv[1], "malformed")) malformed_frames();
  else if (!strcmp(argv[1], "nodeinfo")) node_info_frames();
  else if (!strcmp(argv[1], "nodeinfo_bounds")) node_info_bounds();
  else if (!strcmp(argv[1], "nodeinfo_roles")) node_info_roles();
  else if (!strcmp(argv[1], "relay_matching")) relay_matching();
  else if (!strcmp(argv[1], "bounds")) argument_bounds();
  else if (!strcmp(argv[1], "extensions")) extensions();
  else if (!strcmp(argv[1], "stock_zero_bitfield")) stock_zero_bitfield_frames();
  else if (!strcmp(argv[1], "dedup")) dedup();
  else assert(false);
}
'''


def vectors():
    inputs = [(DEFAULT_KEY, "LongFast", b"hello"),
              (bytes(range(32)), "LongTurbo", "caf\u00e9 \U0001f6f0\ufe0f".encode()),
              (bytes(range(16)), "LongMod", bytes(range(33, 127))),
              (DEFAULT_KEY, "LongFast", b"a" * 127),
              (DEFAULT_KEY, "LongFast", b"b" * 128),
              (bytes(range(32)), "LongTurbo", b"c" * 228),
              (DEFAULT_KEY, "LongFast", "\u0080\u07ff\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff".encode())]
    golden = []
    for key, name, text in inputs:
        frame = reference_frame(data_text(text) + b"\x48\x01", name, key)
        golden.append("{" + array(key) + "," + str(len(key)) + ',"' + name + '",'
                      + literal(text) + "," + str(len(text)) + "," + array(frame) + "," + str(len(frame)) + "}")
    stock_zero_bitfield = []
    for key, name in [(bytes(range(16)), "ShortTurbo"), (bytes(range(32)), "LongFast")]:
        text = "stock field9 zero caf\u00e9".encode()
        # Independently encrypted exact stock Data shape, not our encodeText:
        # TEXT_MESSAGE_APP + UTF-8 payload + OPTIONAL uint32 bitfield=0.
        data = data_text(text) + b"\x48\x00"
        frame = reference_frame(data, name, key, flags=0xE7)
        stock_zero_bitfield.append("{" + array(key) + "," + str(len(key)) + ',"' + name + '",'
                                   + literal(text) + "," + str(len(text)) + ","
                                   + array(frame) + "," + str(len(frame)) + "}")
    node_inputs = [(DEFAULT_KEY, "LongFast", b"MeshCore", b"CORE", FROM, 7),
                   (bytes(range(32)), "LongTurbo", ("\u00e9" * 12).encode(), "\U0001f680".encode(), 0xABCDEF12, 0),
                   (bytes(range(16)), "LongMod", b"L" * 24, "\u00e9\u00e9".encode(), 4, 3),
                   (bytes(range(32)), "private", b"", b"a", 0xFFFFFFFE, 1)]
    node_inputs += [(DEFAULT_KEY, "LongFast", "\u6d4b\u8bd5".encode(), "\u6d4bA".encode(), FROM, hops)
                    for hops in (2, 4, 5, 6)]
    node_info = []
    relay_node_info = []
    for offset, (key, name, long_name, short_name, sender, hops) in enumerate(node_inputs):
        packet_id = PACKET_ID + offset
        frame = reference_frame(data_nodeinfo(long_name, short_name, sender), name, key,
                                sender=sender, packet_id=packet_id, flags=hops | (hops << 5))
        prefix = ("{" + array(key) + "," + str(len(key)) + ',"' + name + '",'
                  + literal(long_name) + "," + str(len(long_name)) + ","
                  + literal(short_name) + "," + str(len(short_name)) + ","
                  + str(sender) + "U," + str(packet_id) + "U," + str(hops) + ",")
        node_info.append(prefix + array(frame) + "," + str(len(frame)) + "}")
        relay = reference_frame(data_nodeinfo(long_name, short_name, sender, rebroadcast=True),
                                name, key, sender=sender, packet_id=packet_id, flags=hops | (hops << 5))
        relay_node_info.append(prefix + array(relay) + "," + str(len(relay)) + "}")
    bad_data = {
        "empty Data": b"", "missing port": b"\x12\x01x", "missing text": b"\x08\x01",
        "empty text": b"\x08\x01\x12\x00", "other app": b"\x08\x02\x12\x01x",
        "wrong port wire": b"\x0a\x01\x01\x12\x01x", "wrong payload wire": b"\x08\x01\x10\x01",
        "truncated length": b"\x08\x01\x12\x80", "oversized length": b"\x08\x01\x12\xff\x01x",
        "field zero": b"\x00" + data_text(b"x"), "unknown group wire": data_text(b"x") + b"\x53",
        "overflow varint": b"\xff" * 9 + b"\x02", "noncanonical tag": b"\x88\x00\x01\x12\x01x",
        "duplicate port": data_text(b"x") + b"\x08\x01", "duplicate text": data_text(b"x") + b"\x12\x01y",
        "UTF8 surrogate": data_text(b"\xed\xa0\x80"), "embedded NUL": data_text(b"x\x00y"),
        "proxied DM dest": data_text(b"x") + b"\x25" + struct.pack("<I", 0x99887766),
        "invalid boolean": data_text(b"x") + b"\x18\x02", "bad source wire": data_text(b"x") + b"\x28\x01",
        "truncated fixed32": data_text(b"x") + b"\x25\x00\x00",
        "truncated unknown fixed64": data_text(b"x") + b"\x51\x00",
        "long text": data_text(b"x" * 229),
    }
    rejected = [(label, reference_frame(data)) for label, data in bad_data.items()]
    rejected += [("DM", reference_frame(data_text(b"x"), to=0x99887766)),
                 ("zero sender", reference_frame(data_text(b"x"), sender=0)),
                 ("reserved sender", reference_frame(data_text(b"x"), sender=0xFFFFFFFF)),
                 ("zero packet ID", reference_frame(data_text(b"x"), packet_id=0)),
                 ("short header", reference_frame(data_text(b"x"))[:15]),
                 ("overlong frame", bytes(250))]
    malformed = ["{\"" + label + "\"," + array(frame) + "," + str(len(frame)) + "}" for label, frame in rejected]
    extension = reference_frame(data_text(b"x") + b"\x50\x81\x01\x59" + bytes(8)
                                + b"\x62\x03abc\x6d" + bytes(4))
    request = reference_frame(data_text(b"x") + b"\x18\x01", flags=0x6B)
    presets = [("ShortTurbo", 500000, 7, 5), ("ShortFast", 250000, 7, 5),
               ("ShortSlow", 250000, 8, 5), ("MediumTurbo", 500000, 9, 5),
               ("MediumFast", 250000, 9, 5), ("MediumSlow", 250000, 10, 5),
               ("LongTurbo", 500000, 11, 8), ("LongFast", 250000, 11, 5),
               ("LongModerate", 125000, 11, 8), ("LongSlow", 125000, 12, 8)]
    modems = []
    for region, start, end in [("US", 902000000, 928000000), ("EU868", 869400000, 869650000),
                               ("EU433", 433000000, 434000000), ("ANZ", 915000000, 928000000)]:
        for preset, bandwidth, sf, cr in presets:
            name = "LongMod" if preset == "LongModerate" else preset
            name_hash = 5381
            for byte in name.encode():
                name_hash = (name_hash * 33 + byte) & 0xFFFFFFFF
            slots = (end - start) // bandwidth
            frequency = (start + bandwidth // 2 + (name_hash % slots) * bandwidth) / 1e6 if slots else 0.0
            modems.append("{Region::" + region + ",Preset::" + preset + "," + str(bool(slots)).lower()
                          + "," + str(frequency) + "f," + str(bandwidth / 1000) + "f," + str(sf) + "," + str(cr) + "}")
    return (HARNESS.replace("@GOLDEN@", ",\n".join(golden)).replace("@MALFORMED@", ",\n".join(malformed))
            .replace("@STOCK_ZERO_BITFIELD@", ",\n".join(stock_zero_bitfield))
            .replace("@NODEINFO_GOLDEN@", ",\n".join(node_info))
            .replace("@RELAY_NODEINFO_GOLDEN@", ",\n".join(relay_node_info))
            .replace("@MODEMS@", ",\n".join(modems)).replace("@EXTENSION_FRAME@", array(extension))
            .replace("@REQUEST_FRAME@", array(request)))


class MeshtextChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Python cryptography is required for the independent AES oracle")
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++ compiler is required")
        required = ["AESCommon.cpp", "AES128.cpp", "AES256.cpp", "BlockCipher.cpp", "Crypto.cpp"]
        override = os.environ.get("MESHCORE_CRYPTO_DIR")
        candidates = [Path(override)] if override else sorted((ROOT / ".pio/libdeps").glob("*/Crypto"))
        crypto = next((path for path in candidates if all((path / item).is_file() for item in required)), None)
        if not crypto:
            raise unittest.SkipTest("an existing rweather/Crypto dependency is required (MESHCORE_CRYPTO_DIR)")
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-mt-codec-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        source = work / "chat.cpp"
        source.write_text(vectors(), encoding="utf-8")
        cls.binary = work / ("chat.exe" if sys.platform == "win32" else "chat")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        result = subprocess.run(
            [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
             "-DMESH_ENABLE_MESHTEXT_CHAT=1",
             *sanitizers, "-I" + str(ROOT / "src"), "-I" + str(crypto), str(source),
             str(ROOT / "src/helpers/MeshtextChat.cpp"), *[str(crypto / item) for item in required],
             "-o", str(cls.binary)], capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS " + name, result.stdout)

    def test_modem_presets_regions_and_strict_slots(self):
        self.run_case("presets")

    def test_actual_aes128_aes256_against_independent_golden_frames(self):
        self.run_case("frames")

    def test_outgoing_text_and_manual_nodeinfo_default_mqtt_consent_not_mqtt_origin(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        result = subprocess.run([str(self.binary), "mqtt_defaults"], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        encoded = dict(line.split(" ", 1) for line in result.stdout.splitlines()
                       if not line.startswith("PASS "))
        self.assertEqual(set(encoded), {"TEXT128", "TEXT256", "NODEINFO128", "NODEINFO256"})
        for label, wire_hex in encoded.items():
            with self.subTest(label=label):
                frame = bytes.fromhex(wire_hex)
                self.assertEqual(frame[12], 0xE7)  # No want_ack or via_mqtt.
                key = DEFAULT_KEY if label.endswith("128") else bytes(range(32))
                nonce = struct.pack("<Q", PACKET_ID) + struct.pack("<Q", FROM)
                decryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()
                data = decryptor.update(frame[16:]) + decryptor.finalize()
                # Independently parse standard Data fields; no production decoder.
                self.assertEqual(data[:3], b"\x08\x01\x12" if label.startswith("TEXT")
                                 else b"\x08\x04\x12")
                offset, payload_length, shift = 3, 0, 0
                while True:
                    byte = data[offset]
                    offset += 1
                    payload_length |= (byte & 127) << shift
                    if not byte & 128:
                        break
                    shift += 7
                payload = data[offset:offset + payload_length]
                self.assertEqual(data[offset + payload_length:], b"\x48\x01",
                                 "field9=1 sets only MQTT consent, not want_response")
                if label.startswith("TEXT"):
                    self.assertEqual(payload, b"x" * 228)
                    self.assertEqual(len(frame), 251)
                else:
                    self.assertEqual(payload, data_nodeinfo(b"MeshCore", b"CORE", FROM)[4:-2])

    def test_malformed_protobuf_utf8_and_broadcast_only(self):
        self.run_case("malformed")

    def test_explicit_standard_nodeinfo_aes128_aes256_all_hops_and_no_receive(self):
        self.run_case("nodeinfo")

    def test_nodeinfo_stock_name_utf8_and_argument_bounds(self):
        self.run_case("nodeinfo_bounds")

    def test_default_nodeinfo_unchanged_and_opt_in_changes_only_client_role(self):
        self.run_case("nodeinfo_roles")

    def test_bounded_relay_copy_match_and_origin_echo_are_disjoint_without_ram_growth(self):
        self.run_case("relay_matching")

    def test_argument_and_text_bounds(self):
        self.run_case("bounds")

    def test_safe_unknown_fields_and_no_request_response_action(self):
        self.run_case("extensions")

    def test_stock_present_zero_bitfield_broadcast_hops7_aes128_and_aes256(self):
        self.run_case("stock_zero_bitfield")

    def test_dedup_only_valid_packets_and_own_tx(self):
        self.run_case("dedup")


if __name__ == "__main__":
    unittest.main()
