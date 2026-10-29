#pragma once

// Pure (host-testable) chat history types and the per-chat SD line codec.
//
// Line formats (one message per line, no trailing newline in the codec):
//   legacy : "<< text" (received) / ">> text" (own)
//   v2     : "<< {m1;<sent>;<recv>;<hops>;<snr_q4>;<rssi>;<hs>;<pathhex>} text"
// Only received messages carry the v2 prefix; own messages persist text only
// (their delivery details are session-only, see OwnMessageTracker). A text that
// starts with '{' stays text unless the complete v2 prefix parses.

#include "globals.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MESSAGE_META_PATH_BYTES 16

// Zeroed (has == false) means "no details". Fields are ordered to keep the
// struct at 32 bytes; ChatPage keeps CHAT_HISTORY_RINGBUF_SIZE of these.
struct MessageMeta {
  uint32_t sent_ts = 0, recv_ts = 0;
  int16_t rssi = 0;
  bool has = false;
  uint8_t hops = 0xFF;   // 0xFF unknown, 0 direct
  int8_t snr_q4 = 0;     // SNR*4 as on the packet
  uint8_t path_len = 0;  // hashes stored in path[] (<= hops; capped to fit)
  uint8_t hash_size = 1; // bytes per repeater hash (1..4)
  uint8_t path[MESSAGE_META_PATH_BYTES]; // repeater hashes, path_len*hash_size bytes used
};
static_assert(sizeof(MessageMeta) == 32, "MessageMeta must stay 32 bytes (RAM budget)");

struct HistoryMessage {
  char text[MAX_MESSAGE_LENGTH];
  bool out;
  MessageMeta meta;
  uint32_t own_id; // OwnMessageTracker id for own messages this session, else 0
};
static_assert(sizeof(HistoryMessage) <= MAX_MESSAGE_LENGTH + 40, "HistoryMessage grew past the RAM budget");

// Fill `m` from a received packet's raw fields. packed_path_len uses the
// Packet::path_len packing: lower 6 bits = hash count, upper 2 = hash size - 1.
inline void fillMessageMeta(MessageMeta &m, uint32_t sent_ts, uint32_t recv_ts, bool is_flood,
                            uint16_t packed_path_len, const uint8_t *path, int8_t snr_q4, int16_t rssi) {
  m = MessageMeta();
  m.has = true;
  m.sent_ts = sent_ts;
  m.recv_ts = recv_ts;
  m.snr_q4 = snr_q4;
  m.rssi = rssi;
  uint8_t count = packed_path_len & 63;
  uint8_t hs = (uint8_t)(((packed_path_len >> 6) & 3) + 1);
  m.hash_size = hs;
  // A direct-routed packet's path is the remaining route, not the hops taken.
  m.hops = is_flood ? count : 0xFF;
  if (!is_flood || !path) {
    return;
  }
  uint8_t fit = MESSAGE_META_PATH_BYTES / hs;
  m.path_len = count < fit ? count : fit;
  memcpy(m.path, path, (size_t)m.path_len * hs);
}

namespace history_line_detail {

inline size_t append(char *out, size_t cap, size_t pos, const char *s) {
  while (*s && pos + 1 < cap) {
    out[pos++] = *s++;
  }
  if (cap) {
    out[pos < cap ? pos : cap - 1] = '\0';
  }
  return pos;
}

inline bool parseUInt(const char *&p, uint32_t max, uint32_t &v) {
  if (*p < '0' || *p > '9') {
    return false;
  }
  uint64_t acc = 0;
  int digits = 0;
  while (*p >= '0' && *p <= '9') {
    acc = acc * 10 + (uint64_t)(*p - '0');
    if (++digits > 10 || acc > max) {
      return false;
    }
    p++;
  }
  v = (uint32_t)acc;
  return true;
}

inline bool parseInt(const char *&p, int32_t min, int32_t max, int32_t &v) {
  bool neg = *p == '-';
  if (neg) {
    p++;
  }
  uint32_t mag;
  if (!parseUInt(p, neg ? (uint32_t)(-(int64_t)min) : (uint32_t)max, mag)) {
    return false;
  }
  v = neg ? (int32_t)(-(int64_t)mag) : (int32_t)mag;
  return true;
}

inline int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

// Parses "{m1;...}" at p; on success sets meta and returns the text start.
inline const char *parseV2(const char *p, MessageMeta &meta) {
  if (strncmp(p, "{m1;", 4) != 0) {
    return nullptr;
  }
  p += 4;
  MessageMeta m;
  uint32_t u;
  int32_t i;
  if (!parseUInt(p, 0xFFFFFFFFu, u) || *p++ != ';') return nullptr;
  m.sent_ts = u;
  if (!parseUInt(p, 0xFFFFFFFFu, u) || *p++ != ';') return nullptr;
  m.recv_ts = u;
  if (!parseUInt(p, 255, u) || *p++ != ';') return nullptr;
  m.hops = (uint8_t)u;
  if (!parseInt(p, -128, 127, i) || *p++ != ';') return nullptr;
  m.snr_q4 = (int8_t)i;
  if (!parseInt(p, -32768, 32767, i) || *p++ != ';') return nullptr;
  m.rssi = (int16_t)i;
  if (!parseUInt(p, 4, u) || u == 0 || *p++ != ';') return nullptr;
  m.hash_size = (uint8_t)u;
  size_t n = 0;
  while (*p != '}') {
    int hi = hexVal(p[0]);
    int lo = hi < 0 ? -1 : hexVal(p[1]);
    if (lo < 0 || n >= MESSAGE_META_PATH_BYTES) {
      return nullptr;
    }
    m.path[n++] = (uint8_t)((hi << 4) | lo);
    p += 2;
  }
  if (n % m.hash_size != 0) {
    return nullptr;
  }
  m.path_len = (uint8_t)(n / m.hash_size);
  p++; // '}'
  if (*p == ' ') {
    p++;
  } else if (*p != '\0') {
    return nullptr;
  }
  m.has = true;
  meta = m;
  return p;
}

} // namespace history_line_detail

// Writes the full line (no newline) into out; always NUL-terminates when cap > 0.
// meta is only written for received messages (out == false) with meta->has.
// Newlines in the text become spaces; at most text_max chars of text are read.
// Returns the length written. Uses no large stack buffers (SD write path).
inline size_t encodeHistoryLineParts(bool is_out, const MessageMeta *meta, const char *text, size_t text_max,
                                     char *out, size_t cap) {
  using namespace history_line_detail;
  if (!out || cap == 0) {
    return 0;
  }
  out[0] = '\0';
  size_t pos = append(out, cap, 0, is_out ? ">> " : "<< ");
  if (!is_out && meta && meta->has) {
    char hdr[48];
    snprintf(hdr, sizeof(hdr), "{m1;%lu;%lu;%u;%d;%d;%u;", (unsigned long)meta->sent_ts,
             (unsigned long)meta->recv_ts, (unsigned)meta->hops, (int)meta->snr_q4, (int)meta->rssi,
             (unsigned)meta->hash_size);
    pos = append(out, cap, pos, hdr);
    static const char digits[] = "0123456789ABCDEF";
    uint8_t hs = meta->hash_size ? meta->hash_size : 1;
    size_t n = (size_t)meta->path_len * hs;
    if (n > MESSAGE_META_PATH_BYTES) {
      n = MESSAGE_META_PATH_BYTES - MESSAGE_META_PATH_BYTES % hs;
    }
    for (size_t k = 0; k < n; k++) {
      char hx[3] = { digits[meta->path[k] >> 4], digits[meta->path[k] & 15], 0 };
      pos = append(out, cap, pos, hx);
    }
    pos = append(out, cap, pos, "} ");
  }
  for (size_t k = 0; text && k < text_max && text[k] && pos + 1 < cap; k++) {
    char c = text[k];
    out[pos++] = (c == '\n' || c == '\r') ? ' ' : c;
  }
  out[pos] = '\0';
  return pos;
}

inline size_t encodeHistoryLine(const HistoryMessage &m, char *out, size_t cap) {
  return encodeHistoryLineParts(m.out, &m.meta, m.text, sizeof(m.text), out, cap);
}

// Same acceptance rule the legacy loader used: "<<"/">>" prefix, >= 4 chars.
inline bool isHistoryLine(const char *line) {
  return line && strlen(line) >= 4 && (line[0] == '>' || line[0] == '<') && line[0] == line[1];
}

// Accepts legacy and v2 lines. Returns false for lines that are not history
// lines (out is then unspecified). own_id is always reset to 0.
inline bool decodeHistoryLine(const char *line, HistoryMessage &out) {
  if (!isHistoryLine(line)) {
    return false;
  }
  out.out = line[0] == '>';
  out.own_id = 0;
  out.meta = MessageMeta();
  const char *text = line + 3;
  if (!out.out && *text == '{') { // only received lines carry metadata
    const char *rest = history_line_detail::parseV2(text, out.meta);
    if (rest) {
      text = rest;
    }
  }
  strncpy(out.text, text, sizeof(out.text));
  out.text[sizeof(out.text) - 1] = '\0';
  return true;
}

// Channel sender: "Name: text" -> name (truncated to cap-1). False if no ": "
// or the name would be empty.
inline bool splitChannelSender(const char *text, char *name, size_t cap) {
  if (!text || !name || cap == 0) {
    return false;
  }
  const char *sep = strstr(text, ": ");
  if (!sep || sep == text) {
    return false;
  }
  size_t n = (size_t)(sep - text);
  if (n > cap - 1) {
    n = cap - 1;
  }
  memcpy(name, text, n);
  name[n] = '\0';
  return true;
}
