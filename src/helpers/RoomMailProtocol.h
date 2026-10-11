#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "RoomMailStore.h"

namespace mesh {

static constexpr uint8_t ROOM_MAIL_REQUEST_SUBTYPE = 0x0b;
static constexpr size_t ROOM_MAIL_PROTOCOL_READ_CHUNK = 128;
static constexpr size_t ROOM_MAIL_MAX_COMMAND_LENGTH = 600;

// Requests follow the existing encrypted room request tag. The trusted caller
// supplies the authenticated full sender key, send permission and durable nonce;
// requests never supply an inbox owner. All integers below are little-endian.
// Every response starts [0x0b, operation, status]. Status values follow
// RoomMailResult: 0 success, 1 invalid, 2 unavailable, 3 write failure,
// 4 not found, 5 stale revision, 6 forbidden, 7 full, 8 successful duplicate,
// 9 nonce/body mismatch. A short invalid response may contain only this header.
//
// op  request after [0x0b, op]                 response after common header
// 0   expected revision u32, allow cursor u8  revision u32, mode u8,
//                                            mailbox-only u8, allow-count u8,
//                                            start u8, next u8, queued u8,
//                                            zero or more full 32-byte keys
// 1   expected revision u32, cursor u8        revision u32, total u8, start u8,
//                                            next u8, copied u8, entries:
//                                            id u32, sender key[32], length u16
// 2   message id u32, offset u16              id u32, offset u16, total u16,
//                                            copied u8, sender key[32], bytes
// 3   recipient key[32], body length u16,     message id u32
//     complete UTF-8 body bytes
// 4   message id u32 (acknowledge receipt)    message id u32
// 5   message id u32 (delete)                 message id u32
// 6   expected revision u32, allowed key[32]  new revision u32
// 7   expected revision u32, denied key[32]   new revision u32
// 8   expected revision u32, mode u8          new revision u32
// 9   expected revision u32, mailbox-only u8  new revision u32
//
// Mode values: 0 closed, 1 public, 2 private (allow list). All queue entries are
// unacknowledged; ACK durably removes a message, preserving its retry receipt.
// Index responses contain at most two whole entries. next==255 means complete.
// Start an inbox index at revision=0,cursor=0, then retain its returned revision.
// Settings are private to this owner; their index uses the same revision rules.
// Policy changes must echo the current revision, including 0 for a new inbox.
// Message IDs identify immutable content; reads can resume by offset. Empty
// inboxes are closed by default and reads/checks never provision an inbox.
// Every mutating operation checks its complete response fits BEFORE writing.
// Bodies are not fragmented on upload: each send must fit its request route.
// Decrypted room packets include AES zero padding. Logical request lengths are
// determined by the operation (and send's declared body length); only zero bytes
// may follow the logical request. Padding never becomes part of a stored body.

namespace room_mail_protocol_detail {

inline uint16_t get16(const uint8_t* bytes) {
  return uint16_t(bytes[0]) | (uint16_t(bytes[1]) << 8);
}
inline uint32_t get32(const uint8_t* bytes) {
  return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8)
      | (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
}
inline void put16(uint8_t* bytes, uint16_t value) {
  bytes[0] = uint8_t(value); bytes[1] = uint8_t(value >> 8);
}
inline void put32(uint8_t* bytes, uint32_t value) {
  for (uint8_t i = 0; i < 4; ++i) bytes[i] = uint8_t(value >> (8 * i));
}
inline uint8_t status(RoomMailResult result) {
  switch (result) {
    case RoomMailResult::Success: return 0;
    case RoomMailResult::Invalid: return 1;
    case RoomMailResult::Unavailable: return 2;
    case RoomMailResult::WriteFailure: return 3;
    case RoomMailResult::NotFound: return 4;
    case RoomMailResult::StaleVersion: return 5;
    case RoomMailResult::Forbidden: return 6;
    case RoomMailResult::Full: return 7;
    case RoomMailResult::Duplicate: return 8;
    case RoomMailResult::Mismatch: return 9;
  }
  return 1;
}
inline size_t header(uint8_t operation, RoomMailResult result,
                     uint8_t* reply, size_t capacity) {
  if (!reply || capacity < 3) return 0;
  reply[0] = ROOM_MAIL_REQUEST_SUBTYPE; reply[1] = operation;
  reply[2] = status(result); return 3;
}
inline bool successful(RoomMailResult result) {
  return result == RoomMailResult::Success || result == RoomMailResult::Duplicate;
}
inline bool validKey(const uint8_t* key) {
  if (!key) return false;
  uint8_t nonzero = 0;
  for (size_t i = 0; i < 32; ++i) nonzero |= key[i];
  return nonzero != 0;
}
inline bool paddedLength(const uint8_t* payload, size_t length, size_t logical) {
  if (!payload || length < logical) return false;
  for (size_t i = logical; i < length; ++i) if (payload[i] != 0) return false;
  return true;
}
inline bool key(const char* text, uint8_t (&out)[32]) {
  if (!text || strlen(text) != 64) return false;
  uint8_t parsed[32];
  for (size_t i = 0; i < 32; ++i) {
    unsigned value = 0;
    for (size_t j = 0; j < 2; ++j) {
      const char c = text[i * 2 + j]; unsigned digit;
      if (c >= '0' && c <= '9') digit = unsigned(c - '0');
      else if (c >= 'a' && c <= 'f') digit = unsigned(c - 'a' + 10);
      else if (c >= 'A' && c <= 'F') digit = unsigned(c - 'A' + 10);
      else return false;
      value = (value << 4) | digit;
    }
    parsed[i] = uint8_t(value);
  }
  if (!validKey(parsed)) return false;
  memcpy(out, parsed, 32); return true;
}
inline void hex(const uint8_t* bytes, char (&out)[65]) {
  static const char alphabet[] = "0123456789abcdef";
  for (size_t i = 0; i < 32; ++i) {
    out[i * 2] = alphabet[bytes[i] >> 4]; out[i * 2 + 1] = alphabet[bytes[i] & 15];
  }
  out[64] = 0;
}
inline const char* modeName(RoomMailMode mode) {
  return mode == RoomMailMode::Public ? "public" : mode == RoomMailMode::Private ? "private" : "closed";
}
inline const char* spaces(const char* cursor) { while (*cursor == ' ') ++cursor; return cursor; }
inline bool prefix(const char* command, const char* expected) {
  const size_t length = strlen(expected);
  return strncmp(command, expected, length) == 0 && (command[length] == 0 || command[length] == ' ');
}
inline bool number(const char*& cursor, uint32_t maximum, uint32_t& value) {
  cursor = spaces(cursor); value = 0;
  if (*cursor < '0' || *cursor > '9') return false;
  do {
    const uint8_t digit = uint8_t(*cursor++ - '0');
    if (value > maximum / 10 || (value == maximum / 10 && digit > maximum % 10)) return false;
    value = value * 10 + digit;
  } while (*cursor >= '0' && *cursor <= '9');
  return *cursor == 0 || *cursor == ' ';
}
inline size_t utf8Prefix(const uint8_t* text, size_t length) {
  size_t cursor = 0;
  while (cursor < length) {
    const uint8_t first = text[cursor];
    const size_t width = first < 0x80 ? 1 : first < 0xe0 ? 2 : first < 0xf0 ? 3 : 4;
    if (width > length - cursor) break;
    cursor += width;
  }
  return cursor;
}
inline void textResult(char* reply, size_t capacity, RoomMailResult result,
                       uint32_t id = 0, bool message = false) {
  if (!reply || !capacity) return;
  if (successful(result)) {
    if (message) snprintf(reply, capacity, "OK id=%lu%s", (unsigned long)id,
        result == RoomMailResult::Duplicate ? " duplicate" : "");
    else snprintf(reply, capacity, "OK");
    return;
  }
  const char* error = "invalid";
  switch (result) {
    case RoomMailResult::Unavailable: error = "unavailable"; break;
    case RoomMailResult::WriteFailure: error = "write failure"; break;
    case RoomMailResult::NotFound: error = "not found"; break;
    case RoomMailResult::StaleVersion: error = "stale revision"; break;
    case RoomMailResult::Forbidden: error = "permission denied"; break;
    case RoomMailResult::Full: error = "mailbox full"; break;
    case RoomMailResult::Mismatch: error = "request changed"; break;
    default: break;
  }
  snprintf(reply, capacity, "Error %s", error);
}
template <typename Filesystem>
RoomMailResult ownStatus(Filesystem* fs, const uint8_t* owner, RoomMailStatus& value) {
  const RoomMailResult result = getRoomMailStatus(fs, owner, value);
  return result == RoomMailResult::NotFound ? RoomMailResult::Success : result;
}
inline RoomMailResult editSettings(RoomMailSettings& settings, uint8_t operation,
                                   const uint8_t* value) {
  if (operation == 8) {
    if (*value > 2) return RoomMailResult::Invalid;
    settings.mode = static_cast<RoomMailMode>(*value); return RoomMailResult::Success;
  }
  if (operation == 9) {
    if (*value > 1) return RoomMailResult::Invalid;
    settings.mailbox_only = *value != 0; return RoomMailResult::Success;
  }
  if (!validKey(value)) return RoomMailResult::Invalid;
  uint8_t position = 0;
  while (position < settings.allowed_count && memcmp(settings.allowed[position], value, 32) != 0) ++position;
  if (operation == 6) {
    if (position < settings.allowed_count) return RoomMailResult::Success;
    if (settings.allowed_count >= 8) return RoomMailResult::Full;
    memcpy(settings.allowed[settings.allowed_count++], value, 32);
  } else {
    if (position == settings.allowed_count) return RoomMailResult::Success;
    for (uint8_t i = position; i + 1 < settings.allowed_count; ++i)
      memcpy(settings.allowed[i], settings.allowed[i + 1], 32);
    memset(settings.allowed[--settings.allowed_count], 0, 32);
  }
  return RoomMailResult::Success;
}
} // namespace room_mail_protocol_detail

template <typename Filesystem>
size_t handleRoomMailRequest(Filesystem* fs, const uint8_t* owner, bool can_send,
                             uint64_t request_id, const uint8_t* payload, size_t length,
                             uint8_t* reply, size_t capacity, uint32_t created = 0) {
  using namespace room_mail_protocol_detail;
  const uint8_t operation = payload && length > 1 ? payload[1] : 255;
  if (!payload || length < 2 || payload[0] != ROOM_MAIL_REQUEST_SUBTYPE || operation > 9
      || !validKey(owner) || !reply || capacity < 3)
    return header(operation, RoomMailResult::Invalid, reply, capacity);
  RoomMailResult result = RoomMailResult::Invalid;
  if (operation == 0 || operation == 1) {
    if (!paddedLength(payload, length, 7) || capacity < (operation == 0 ? 13U : 11U))
      return header(operation, result, reply, capacity);
    RoomMailStatus state;
    result = ownStatus(fs, owner, state);
    if (result != RoomMailResult::Success) return header(operation, result, reply, capacity);
    const uint32_t expected = get32(payload + 2); const uint8_t start = payload[6];
    if ((expected && expected != state.revision) || (!expected && start))
      return header(operation, RoomMailResult::StaleVersion, reply, capacity);
    header(operation, result, reply, capacity); put32(reply + 3, state.revision);
    if (operation == 0) {
      if (start > state.settings.allowed_count) return header(operation, RoomMailResult::Invalid, reply, capacity);
      reply[7] = static_cast<uint8_t>(state.settings.mode); reply[8] = state.settings.mailbox_only ? 1 : 0;
      reply[9] = state.settings.allowed_count; reply[10] = start; reply[12] = state.count;
      size_t used = 13; uint8_t next = start;
      while (next < state.settings.allowed_count && capacity - used >= 32) {
        memcpy(reply + used, state.settings.allowed[next++], 32); used += 32;
      }
      if (next == start && next < state.settings.allowed_count)
        return header(operation, RoomMailResult::Invalid, reply, capacity);
      reply[11] = next < state.settings.allowed_count ? next : 255; return used;
    }
    if (start > state.count) return header(operation, RoomMailResult::Invalid, reply, capacity);
    const size_t room = (capacity - 11) / 38;
    if (!room && start < state.count) return header(operation, RoomMailResult::Invalid, reply, capacity);
    RoomMailMessage messages[2]; size_t copied = 0;
    if (state.count) result = listRoomMail(fs, owner, state.revision, start, messages, room < 2 ? room : 2, copied);
    if (result != RoomMailResult::Success) return header(operation, result, reply, capacity);
    reply[7] = state.count; reply[8] = start; reply[9] = start + copied < state.count ? uint8_t(start + copied) : 255;
    reply[10] = uint8_t(copied); size_t used = 11;
    for (size_t i = 0; i < copied; ++i) {
      put32(reply + used, messages[i].id); memcpy(reply + used + 4, messages[i].sender, 32);
      put16(reply + used + 36, messages[i].length); used += 38;
    }
    return used;
  }
  if (operation == 2) {
    if (!paddedLength(payload, length, 8) || capacity < 44) return header(operation, result, reply, capacity);
    const uint32_t id = get32(payload + 2); const uint16_t offset = get16(payload + 6);
    const size_t available = capacity - 44;
    uint8_t bytes[ROOM_MAIL_PROTOCOL_READ_CHUNK]; size_t copied = 0; RoomMailMessage message;
    // A zero-byte EOF still requires a nonzero read capacity in the store.
    result = readRoomMail(fs, owner, id, offset, bytes,
        available ? (available < sizeof(bytes) ? available : sizeof(bytes)) : 1, copied, message);
    if (result != RoomMailResult::Success) return header(operation, result, reply, capacity);
    if (copied > available) return header(operation, RoomMailResult::Invalid, reply, capacity);
    header(operation, result, reply, capacity); put32(reply + 3, id); put16(reply + 7, offset);
    put16(reply + 9, message.length); reply[11] = uint8_t(copied); memcpy(reply + 12, message.sender, 32);
    if (copied) memcpy(reply + 44, bytes, copied);
    return 44 + copied;
  }
  if (capacity < 7) return header(operation, result, reply, capacity);
  uint32_t id = 0;
  if (operation == 3) {
    if (length < 36 || !validKey(payload + 2))
      return header(operation, result, reply, capacity);
    const size_t body_length = get16(payload + 34);
    if (!paddedLength(payload, length, 36 + body_length)) return header(operation, result, reply, capacity);
    result = can_send ? sendRoomMail(fs, owner, payload + 2, request_id,
        reinterpret_cast<const char*>(payload + 36), body_length, id, created) : RoomMailResult::Forbidden;
  } else if (operation == 4 || operation == 5) {
    if (!paddedLength(payload, length, 6) || !(id = get32(payload + 2))) return header(operation, result, reply, capacity);
    result = operation == 4 ? acknowledgeRoomMail(fs, owner, id) : deleteRoomMail(fs, owner, id);
  } else {
    if (!paddedLength(payload, length, operation <= 7 ? 38U : 7U)) return header(operation, result, reply, capacity);
    RoomMailStatus state; result = ownStatus(fs, owner, state);
    if (result != RoomMailResult::Success) return header(operation, result, reply, capacity);
    if (get32(payload + 2) != state.revision) return header(operation, RoomMailResult::StaleVersion, reply, capacity);
    result = editSettings(state.settings, operation, payload + 6);
    if (result == RoomMailResult::Success) result = saveRoomMailSettings(fs, owner, state.settings, state.revision);
    if (result == RoomMailResult::Success) {
      result = ownStatus(fs, owner, state); id = state.revision;
    }
  }
  header(operation, result, reply, capacity); put32(reply + 3, id); return 7;
}

// The caller strips an optional ! prefix for the stock-app !mail fallback.
// Only the trusted owner is used for settings/list/read/ack/delete. Ordinary
// authenticated readers may manage their own inbox; sends additionally require
// can_send. CLI reads return complete UTF-8 characters with the next byte offset.
// An offset in the middle of a character is rejected. Mutating replies
// require capacity >=32 so their ID/confirmation cannot be silently truncated.
// CLI next==total (or allowed count) completes an index. A short route may show
// a sender key prefix followed by ".."; only complete keys authorize/send mail.
template <typename Filesystem>
bool handleRoomMailCommand(Filesystem* fs, const uint8_t* owner, bool can_send,
                           uint64_t request_id, const char* command,
                           char* reply, size_t capacity, uint32_t created = 0) {
  using namespace room_mail_protocol_detail;
  if (!command || !prefix(command, "mail")) return false;
  if (!reply || !capacity) return true;
  reply[0] = 0;
  size_t length = 0;
  while (length <= ROOM_MAIL_MAX_COMMAND_LENGTH && command[length]) ++length;
  if (length > ROOM_MAIL_MAX_COMMAND_LENGTH || !validKey(owner)) {
    textResult(reply, capacity, RoomMailResult::Invalid); return true;
  }
  const char* body = spaces(command + 4);
  const bool settings = prefix(body, "settings") || prefix(body, "check");
  const bool inbox = prefix(body, "inbox");
  const bool list = prefix(body, "list") || inbox, read = prefix(body, "read");
  const bool send = prefix(body, "send"), ack = prefix(body, "ack"), del = prefix(body, "delete");
  const bool mode = prefix(body, "mode"), delivery = prefix(body, "delivery");
  const bool allow = prefix(body, "allow"), deny = prefix(body, "deny");
  if (!(settings || list || read || send || ack || del || mode || delivery || allow || deny)) {
    textResult(reply, capacity, RoomMailResult::Invalid); return true;
  }
  if (!(settings || list || read) && capacity < 32) {
    textResult(reply, capacity, RoomMailResult::Invalid); return true;
  }
  RoomMailResult result = RoomMailResult::Invalid;
  if (send) {
    const char* cursor = spaces(body + 4); const char* end = strchr(cursor, ' ');
    uint8_t recipient[32]; char text_key[65];
    if (!end || end - cursor != 64) { textResult(reply, capacity, result); return true; }
    memcpy(text_key, cursor, 64); text_key[64] = 0;
    const char* text = spaces(end);
    uint32_t id = 0;
    if (key(text_key, recipient) && *text)
      result = can_send ? sendRoomMail(fs, owner, recipient, request_id, text, strlen(text), id, created) : RoomMailResult::Forbidden;
    textResult(reply, capacity, result, id, true); return true;
  }
  if (ack || del || read) {
    const char* cursor = body + (ack ? 3 : del ? 6 : 4); uint32_t id, offset = 0;
    if (!number(cursor, UINT32_MAX, id) || id == 0
        || (read && *spaces(cursor) && !number(cursor, 512, offset)) || *spaces(cursor)) {
      textResult(reply, capacity, result); return true;
    }
    if (!read) {
      result = ack ? acknowledgeRoomMail(fs, owner, id) : deleteRoomMail(fs, owner, id);
      textResult(reply, capacity, result, id, true); return true;
    }
    RoomMailMessage message; uint8_t bytes[ROOM_MAIL_PROTOCOL_READ_CHUNK]; size_t copied = 0;
    result = readRoomMail(fs, owner, id, offset, bytes, sizeof(bytes), copied, message);
    if (result != RoomMailResult::Success) { textResult(reply, capacity, result); return true; }
    if (copied && (bytes[0] & 0xc0) == 0x80) {
      snprintf(reply, capacity, "Error offset inside UTF-8 character"); return true;
    }
    size_t count = utf8Prefix(bytes, copied); int prefix_length;
    do {
      prefix_length = snprintf(reply, capacity, "id=%lu off=%lu next=%lu total=%u text=",
          (unsigned long)id, (unsigned long)offset, (unsigned long)(offset + count), unsigned(message.length));
      if (prefix_length >= 0 && size_t(prefix_length) + count < capacity) break;
      if (!count) { textResult(reply, capacity, RoomMailResult::Invalid); return true; }
      count = utf8Prefix(bytes, count - 1);
    } while (true);
    if (!count && copied) { textResult(reply, capacity, RoomMailResult::Invalid); return true; }
    memcpy(reply + prefix_length, bytes, count); reply[prefix_length + count] = 0; return true;
  }
  RoomMailStatus state; result = ownStatus(fs, owner, state);
  if (result != RoomMailResult::Success) { textResult(reply, capacity, result); return true; }
  if (settings || list) {
    const char* cursor = body + (settings ? (prefix(body, "check") ? 5 : 8) : inbox ? 5 : 4); uint32_t start = 0;
    const uint8_t total = settings ? state.settings.allowed_count : state.count;
    if ((*spaces(cursor) && !number(cursor, total, start)) || *spaces(cursor)) {
      textResult(reply, capacity, RoomMailResult::Invalid); return true;
    }
    char entries[160] = {}; size_t used = 0; uint32_t next = start; int prefix_length;
    RoomMailMessage messages[2]; size_t copied = 0;
    if (list && state.count) result = listRoomMail(fs, owner, state.revision, start, messages, 2, copied);
    if (result != RoomMailResult::Success) { textResult(reply, capacity, result); return true; }
    while (next < total && (settings || next - start < copied)) {
      char full_key[65]; char entry[90];
      if (settings) { hex(state.settings.allowed[next], full_key); snprintf(entry, sizeof(entry), " %s", full_key); }
      else {
        const auto& message = messages[next - start]; hex(message.sender, full_key);
        snprintf(entry, sizeof(entry), " %lu/%u:%s", (unsigned long)message.id, unsigned(message.length), full_key);
      }
      char header_text[128];
      const int n = settings ? snprintf(header_text, sizeof(header_text),
          "rev=%lu mode=%s delivery=%s queued=%u allowed=%u start=%lu next=%lu",
          (unsigned long)state.revision, modeName(state.settings.mode), state.settings.mailbox_only ? "mailbox" : "chat",
          unsigned(state.count), unsigned(total), (unsigned long)start, (unsigned long)(next + 1))
        : snprintf(header_text, sizeof(header_text), "rev=%lu total=%u start=%lu next=%lu",
          (unsigned long)state.revision, unsigned(total), (unsigned long)start, (unsigned long)(next + 1));
      if (!settings && n >= 0 && size_t(n) + used + strlen(entry) >= capacity) {
        const auto& message = messages[next - start];
        for (size_t digits = 16; digits >= 8; digits -= 8) {
          full_key[digits] = 0;
          snprintf(entry, sizeof(entry), " %lu/%u:%s..", (unsigned long)message.id, unsigned(message.length), full_key);
          if (size_t(n) + used + strlen(entry) < capacity) break;
        }
      }
      const size_t entry_length = strlen(entry);
      if (n < 0 || size_t(n) >= sizeof(header_text) || entry_length >= sizeof(entries) - used
          || size_t(n) + used + entry_length >= capacity) break;
      memcpy(entries + used, entry, entry_length + 1); used += entry_length; ++next;
    }
    if (next == start && next < total) { textResult(reply, capacity, RoomMailResult::Invalid); return true; }
    prefix_length = settings ? snprintf(reply, capacity,
        "rev=%lu mode=%s delivery=%s queued=%u allowed=%u start=%lu next=%lu%s",
        (unsigned long)state.revision, modeName(state.settings.mode), state.settings.mailbox_only ? "mailbox" : "chat",
        unsigned(state.count), unsigned(total), (unsigned long)start, (unsigned long)next, entries)
      : snprintf(reply, capacity, "rev=%lu total=%u start=%lu next=%lu%s", (unsigned long)state.revision,
        unsigned(total), (unsigned long)start, (unsigned long)next, entries);
    if (prefix_length < 0 || size_t(prefix_length) >= capacity) textResult(reply, capacity, RoomMailResult::Invalid);
    return true;
  }
  const char* value = spaces(body + (mode ? 4 : delivery ? 8 : allow ? 5 : 4));
  uint8_t bytes[32] = {}; uint8_t operation;
  if (mode) {
    operation = 8;
    if (strcmp(value, "closed") == 0) bytes[0] = 0;
    else if (strcmp(value, "public") == 0) bytes[0] = 1;
    else if (strcmp(value, "private") == 0) bytes[0] = 2;
    else { textResult(reply, capacity, RoomMailResult::Invalid); return true; }
  } else if (delivery) {
    operation = 9;
    if (strcmp(value, "mailbox") == 0) bytes[0] = 1;
    else if (strcmp(value, "chat") != 0) { textResult(reply, capacity, RoomMailResult::Invalid); return true; }
  } else {
    operation = allow ? 6 : 7;
    if (!key(value, bytes)) { textResult(reply, capacity, RoomMailResult::Invalid); return true; }
  }
  result = editSettings(state.settings, operation, bytes);
  if (result == RoomMailResult::Success) result = saveRoomMailSettings(fs, owner, state.settings, state.revision);
  textResult(reply, capacity, result); return true;
}

} // namespace mesh
