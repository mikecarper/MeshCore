#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "RoomBoardStore.h"

namespace mesh {

static constexpr uint8_t ROOM_BOARD_REQUEST_SUBTYPE = 0x0a;
static constexpr size_t ROOM_BOARD_MAX_COMMAND_LENGTH = 512;

namespace room_board_protocol_detail {
inline uint8_t status(RoomBoardResult result) {
  switch (result) {
    case RoomBoardResult::Success: return 0;
    case RoomBoardResult::Invalid: return 1;
    case RoomBoardResult::Unavailable: return 2;
    case RoomBoardResult::WriteFailure: return 3;
    case RoomBoardResult::NotFound: return 4;
    case RoomBoardResult::StaleVersion: return 5;
  }
  return 1;
}
inline size_t shortError(uint8_t op, RoomBoardResult result, uint8_t* reply, size_t capacity) {
  if (!reply || capacity < 3) return 0;
  reply[0] = ROOM_BOARD_REQUEST_SUBTYPE; reply[1] = op; reply[2] = status(result); return 3;
}
inline void textResult(char* reply, size_t capacity, RoomBoardResult result, uint32_t revision = 0) {
  if (!reply || capacity == 0) return;
  const char* message;
  switch (result) {
    case RoomBoardResult::Success: snprintf(reply, capacity, "OK rev=%lu", (unsigned long)revision); return;
    case RoomBoardResult::Unavailable: message = "Error unavailable"; break;
    case RoomBoardResult::WriteFailure: message = "Error write failure"; break;
    case RoomBoardResult::NotFound: message = "Error not found"; break;
    case RoomBoardResult::StaleVersion: message = "Error stale version"; break;
    default: message = "Error invalid"; break;
  }
  snprintf(reply, capacity, "%s", message);
}
inline bool prefix(const char* command, const char* expected) {
  const size_t length = strlen(expected);
  return strncmp(command, expected, length) == 0 && (command[length] == 0 || command[length] == ' ');
}
inline const char* spaces(const char* cursor) { while (*cursor == ' ') ++cursor; return cursor; }
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
inline size_t base64(const uint8_t* data, size_t length, char* output) {
  static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  size_t position = 0;
  for (size_t i = 0; i < length; i += 3) {
    const uint32_t bits = (uint32_t(data[i]) << 16)
        | (i + 1 < length ? uint32_t(data[i + 1]) << 8 : 0)
        | (i + 2 < length ? uint32_t(data[i + 2]) : 0);
    output[position++] = alphabet[(bits >> 18) & 63];
    output[position++] = alphabet[(bits >> 12) & 63];
    output[position++] = i + 1 < length ? alphabet[(bits >> 6) & 63] : '=';
    output[position++] = i + 2 < length ? alphabet[bits & 63] : '=';
  }
  output[position] = 0; return position;
}
} // namespace room_board_protocol_detail

// Read-only requests, callable for any authorized room reader, including
// guests. The caller reserves its transport tag outside this reply capacity.
// All integer fields are little-endian. No partial index entry is emitted.
template <typename Filesystem>
size_t handleRoomBoardRequest(Filesystem* fs, const uint8_t* payload, size_t length,
                              uint8_t* reply, size_t capacity) {
  using namespace room_board_protocol_detail;
  const uint8_t op = payload && length > 1 ? payload[1] : 0xff;
  if (!payload || length < 2 || payload[0] != ROOM_BOARD_REQUEST_SUBTYPE
      || !reply || capacity < 3 || op > 1) return shortError(op, RoomBoardResult::Invalid, reply, capacity);
  if (op == 0) {
    if (length < 7 || capacity < 10) return shortError(op, RoomBoardResult::Invalid, reply, capacity);
    const uint32_t expected = room_board_detail::get32(payload + 2);
    const uint8_t start = payload[6];
    RoomBoardIndex index;
    RoomBoardResult result = RoomBoardResult::Success;
    if (!loadRoomBoard(fs, index)) result = RoomBoardResult::Unavailable;
    else if ((!expected && start) || (expected && expected != index.revision)) result = RoomBoardResult::StaleVersion;
    else if (start > index.count) result = RoomBoardResult::Invalid;
    reply[0] = ROOM_BOARD_REQUEST_SUBTYPE; reply[1] = op;
    room_board_detail::put32(reply + 3, index.revision);
    reply[7] = index.count; reply[8] = start; reply[9] = start;
    size_t used = 10;
    if (result == RoomBoardResult::Success) {
      uint8_t position = start;
      while (position < index.count && position < size_t(start) + 2) {
        const RoomBoardArticle& entry = index.articles[position];
        const size_t title_length = strlen(entry.title);
        const size_t entry_length = 8 + title_length;
        if (entry_length > capacity - used) {
          if (position == start) result = RoomBoardResult::Invalid;
          break;
        }
        uint8_t* encoded = reply + used;
        encoded[0] = entry.id; room_board_detail::put32(encoded + 1, entry.version);
        room_board_detail::put16(encoded + 5, entry.body_length); encoded[7] = uint8_t(title_length);
        memcpy(encoded + 8, entry.title, title_length); used += entry_length; ++position;
      }
      reply[9] = position;
    }
    reply[2] = status(result); return used;
  }
  if (length < 9 || capacity < 13) return shortError(op, RoomBoardResult::Invalid, reply, capacity);
  const uint8_t id = payload[2];
  const uint32_t version = room_board_detail::get32(payload + 3);
  const uint16_t offset = room_board_detail::get16(payload + 7);
  reply[0] = ROOM_BOARD_REQUEST_SUBTYPE; reply[1] = op; reply[3] = id;
  room_board_detail::put32(reply + 4, version); room_board_detail::put16(reply + 8, offset);
  room_board_detail::put16(reply + 10, 0); reply[12] = 0;
  RoomBoardIndex index;
  RoomBoardResult result = RoomBoardResult::Success;
  const RoomBoardArticle* article = nullptr;
  if (id == 0 || id > ROOM_BOARD_MAX_ARTICLES || version == 0) result = RoomBoardResult::Invalid;
  else if (!loadRoomBoard(fs, index)) result = RoomBoardResult::Unavailable;
  else {
    for (uint8_t i = 0; i < index.count; ++i) if (index.articles[i].id == id) article = &index.articles[i];
    if (!article) result = RoomBoardResult::NotFound;
    else {
      room_board_detail::put32(reply + 4, article->version);
      room_board_detail::put16(reply + 10, article->body_length);
      if (article->version != version) result = RoomBoardResult::StaleVersion;
      else if (offset > article->body_length || (capacity == 13 && offset < article->body_length)) result = RoomBoardResult::Invalid;
    }
  }
  size_t copied = 0;
  if (result == RoomBoardResult::Success && offset < article->body_length) {
    const size_t available = capacity - 13;
    const size_t chunk = available < ROOM_BOARD_READ_MAX_CHUNK ? available : ROOM_BOARD_READ_MAX_CHUNK;
    result = readRoomBoardArticle(fs, id, version, offset, reply + 13, chunk, copied);
  }
  reply[2] = status(result); reply[12] = uint8_t(copied); return 13 + copied;
}

// Caller owns administrative authorization for put/del (and may grant get
// to ordinary room readers). The command body is borrowed, never buffered.
// `get room.board [cursor]` returns `next=` for the next whole-entry page.
template <typename Filesystem>
bool handleRoomBoardCommand(Filesystem* fs, const char* command, char* reply, size_t capacity) {
  using namespace room_board_protocol_detail;
  if (!command) return false;
  const bool put = prefix(command, "room.board.put");
  const bool del = prefix(command, "room.board.del");
  const bool read = prefix(command, "get room.board.read");
  const bool list = prefix(command, "get room.board");
  if (!put && !del && !read && !list) return false;
  if (!reply || capacity == 0) return true;
  reply[0] = 0;
  size_t length = 0;
  while (length <= ROOM_BOARD_MAX_COMMAND_LENGTH && command[length]) ++length;
  if (length > ROOM_BOARD_MAX_COMMAND_LENGTH) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
  if (put) {
    const char* cursor = command + strlen("room.board.put"); uint32_t id;
    if (!number(cursor, ROOM_BOARD_MAX_ARTICLES, id) || id == 0) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
    cursor = spaces(cursor); const char* separator = strchr(cursor, '|');
    size_t title_length = separator ? size_t(separator - cursor) : 0;
    while (title_length && cursor[title_length - 1] == ' ') --title_length;
    if (!separator || title_length == 0 || title_length >= ROOM_BOARD_TITLE_CAPACITY) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
    char title[ROOM_BOARD_TITLE_CAPACITY] = {}; memcpy(title, cursor, title_length);
    const char* body = separator + 1; const size_t body_length = strlen(body);
    const RoomBoardResult result = saveRoomBoardArticle(fs, uint8_t(id), title, body_length,
        [body](size_t offset, uint8_t* output, size_t count) -> size_t {
          memcpy(output, body + offset, count); return count;
        });
    RoomBoardIndex index;
    if (result == RoomBoardResult::Success) loadRoomBoard(fs, index);
    textResult(reply, capacity, result, index.revision); return true;
  }
  if (del) {
    const char* cursor = command + strlen("room.board.del"); uint32_t id, version;
    if (!number(cursor, ROOM_BOARD_MAX_ARTICLES, id) || id == 0 || !number(cursor, UINT32_MAX, version)
        || version == 0 || *spaces(cursor)) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
    const RoomBoardResult result = deleteRoomBoardArticle(fs, uint8_t(id), version);
    RoomBoardIndex index;
    if (result == RoomBoardResult::Success) loadRoomBoard(fs, index);
    textResult(reply, capacity, result, index.revision); return true;
  }
  RoomBoardIndex index;
  if (!loadRoomBoard(fs, index)) { textResult(reply, capacity, RoomBoardResult::Unavailable); return true; }
  if (read) {
    const char* cursor = command + strlen("get room.board.read"); uint32_t id, version, offset;
    if (!number(cursor, ROOM_BOARD_MAX_ARTICLES, id) || id == 0 || !number(cursor, UINT32_MAX, version)
        || version == 0 || !number(cursor, ROOM_BOARD_MAX_BODY_LENGTH, offset) || *spaces(cursor)) {
      textResult(reply, capacity, RoomBoardResult::Invalid); return true;
    }
    const RoomBoardArticle* article = nullptr;
    for (uint8_t i = 0; i < index.count; ++i) if (index.articles[i].id == id) article = &index.articles[i];
    if (!article) { textResult(reply, capacity, RoomBoardResult::NotFound); return true; }
    if (article->version != version) { textResult(reply, capacity, RoomBoardResult::StaleVersion); return true; }
    if (offset > article->body_length) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
    const size_t remaining = article->body_length - offset;
    size_t count = remaining < 80 ? remaining : 80;
    int prefix_length = -1;
    while (true) {
      prefix_length = snprintf(reply, capacity, "id=%lu v=%lu off=%lu total=%u n=%u data=",
          (unsigned long)id, (unsigned long)version, (unsigned long)offset,
          unsigned(article->body_length), unsigned(count));
      if (prefix_length >= 0 && size_t(prefix_length) + ((count + 2) / 3) * 4 < capacity) break;
      if (count == 0) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
      --count;
    }
    if (count == 0 && remaining) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
    uint8_t data[80]; size_t copied = 0;
    if (count) {
      const RoomBoardResult result = readRoomBoardArticle(fs, uint8_t(id), version, offset, data, count, copied);
      if (result != RoomBoardResult::Success) { textResult(reply, capacity, result); return true; }
    }
    base64(data, copied, reply + prefix_length); return true;
  }
  const char* cursor = spaces(command + strlen("get room.board")); uint32_t start = 0;
  if ((*cursor && !number(cursor, ROOM_BOARD_MAX_ARTICLES, start)) || *spaces(cursor) || start > index.count) {
    textResult(reply, capacity, RoomBoardResult::Invalid); return true;
  }
  // Format entries first, then the fixed prefix with the final next cursor.
  char entries[2 * (ROOM_BOARD_TITLE_CAPACITY + 35)] = {};
  size_t used = 0; uint32_t next = start;
  while (next < index.count && next < start + 2) {
    const RoomBoardArticle& article = index.articles[next];
    const int written = snprintf(entries + used, sizeof(entries) - used, " %u@%lu/%u:%s",
        unsigned(article.id), (unsigned long)article.version, unsigned(article.body_length), article.title);
    char header[64];
    const int header_length = snprintf(header, sizeof(header), "rev=%lu total=%u start=%lu next=%lu",
        (unsigned long)index.revision, unsigned(index.count), (unsigned long)start, (unsigned long)(next + 1));
    if (written < 0 || header_length < 0 || size_t(written) >= sizeof(entries) - used
        || size_t(header_length) + used + size_t(written) >= capacity) {
      entries[used] = 0;
      if (next == start) { textResult(reply, capacity, RoomBoardResult::Invalid); return true; }
      break;
    }
    used += size_t(written); ++next;
  }
  const int written = snprintf(reply, capacity, "rev=%lu total=%u start=%lu next=%lu%s",
      (unsigned long)index.revision, unsigned(index.count), (unsigned long)start, (unsigned long)next, entries);
  if (written < 0 || size_t(written) >= capacity) textResult(reply, capacity, RoomBoardResult::Invalid);
  return true;
}

} // namespace mesh
