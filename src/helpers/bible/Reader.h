#pragma once
#include "ReaderLookup.h"

namespace mesh { namespace bible {

// Offset is into the display text, not a screen number, so rotation/font
// changes can resume at the page containing the same words.
struct Position {
  uint16_t verse = 0;
  uint16_t offset = 0;
  bool atStart() const { return verse == 0 && offset == 0; }
  bool valid() const { return verse < kVerseCount && offset < kBlockSize; }
  bool operator==(const Position& other) const {
    return verse == other.verse && offset == other.offset;
  }
  bool operator!=(const Position& other) const { return !(*this == other); }
};

inline Reference referenceAt(uint16_t index) {
  Reference ref = {1, 1};
  if (index >= kVerseCount) return ref;
  while (index >= kChapterVerses[ref.chapter - 1]) {
    index -= kChapterVerses[ref.chapter++ - 1];
  }
  ref.verse = static_cast<uint8_t>(index + 1);
  return ref;
}

constexpr size_t kReaderLineBytes = 128;

// Return the next unread byte, preferring word boundaries. Never discard a
// word or cut a UTF-8 codepoint, including overlong words on narrow displays.
template <typename Measure>
uint16_t readerLine(const char* text, uint16_t start, int width,
                    Measure measure, char (&line)[kReaderLineBytes]) {
  size_t count = 0, last_space = 0;
  while (text[start + count]) {
    size_t bytes = 1;
    while (bytes < 4 && text[start + count + bytes]
           && (static_cast<uint8_t>(text[start + count + bytes]) & 0xc0) == 0x80) ++bytes;
    if (count + bytes >= sizeof(line)) break;
    memcpy(line + count, text + start + count, bytes);
    line[count + bytes] = 0;
    const int pixels = static_cast<int>(measure(line));
    if (pixels > width && count != 0) break;
    if (text[start + count] == ' ') last_space = count;
    count += bytes;
    if (pixels > width) break; // one too-wide glyph still makes progress
  }
  if (text[start + count] && last_space > 0) count = last_space;
  memcpy(line, text + start, count);
  line[count] = 0;
  uint16_t next = static_cast<uint16_t>(start + count);
  while (text[next] == ' ') ++next;
  return next;
}

struct ReaderPage {
  uint16_t start = 0, next = 0, previous = 0, part = 1, parts = 1;
};

template <typename Measure>
ReaderPage readerPage(const char* text, uint16_t offset, int width,
                      int rows, Measure measure) {
  ReaderPage result;
  if (rows < 1) rows = 1;
  uint16_t start = 0, previous = 0, part = 0;
  char line[kReaderLineBytes];
  do {
    uint16_t next = start;
    for (int row = 0; row < rows && text[next]; ++row)
      next = readerLine(text, next, width, measure, line);
    ++part;
    if (start <= offset) {
      result.start = start;
      result.next = next;
      result.previous = previous;
      result.part = part;
    }
    previous = start;
    start = next;
  } while (text[start]);
  result.parts = part;
  return result;
}

// Fixed, versioned record independent of compiler padding and radio prefs.
constexpr size_t kBookmarkBytes = 12;
inline uint32_t bookmarkChecksum(const uint8_t* data) {
  uint32_t hash = 2166136261UL;
  for (unsigned i = 0; i < 8; ++i) hash = (hash ^ data[i]) * 16777619UL;
  return hash;
}
inline void encodeBookmark(Position pos, uint8_t (&out)[kBookmarkBytes]) {
  out[0] = 'R'; out[1] = 'D'; out[2] = 1; out[3] = 0;
  out[4] = pos.verse; out[5] = pos.verse >> 8;
  out[6] = pos.offset; out[7] = pos.offset >> 8;
  const uint32_t hash = bookmarkChecksum(out);
  for (unsigned i = 0; i < 4; ++i) out[8 + i] = hash >> (8 * i);
}
inline bool decodeBookmark(const uint8_t (&in)[kBookmarkBytes], Position& pos) {
  pos = Position{};
  if (in[0] != 'R' || in[1] != 'D' || in[2] != 1 || in[3] != 0) return false;
  const uint32_t hash = bookmarkChecksum(in);
  for (unsigned i = 0; i < 4; ++i)
    if (in[8 + i] != static_cast<uint8_t>(hash >> (8 * i))) return false;
  Position candidate;
  candidate.verse = in[4] | (static_cast<uint16_t>(in[5]) << 8);
  candidate.offset = in[6] | (static_cast<uint16_t>(in[7]) << 8);
  if (!candidate.valid() || candidate.atStart()) return false;
  pos = candidate;
  return true;
}

// Debounced checkpointing: no writes just for opening reader 1:1, no rewrite
// of unchanged positions, and a failed save remains pending for retry.
class ReaderBookmark {
  Position _position, _saved;
  uint32_t _changed_at = 0;
public:
  Position position() const { return _position; }
  void restore(Position pos) { _position = _saved = pos.valid() ? pos : Position{}; }
  void move(Position pos, uint32_t now) {
    if (pos.valid() && pos != _position) { _position = pos; _changed_at = now; }
  }
  bool dirty() const { return _position != _saved; }
  bool due(uint32_t now) const { return dirty() && uint32_t(now - _changed_at) >= 2000; }
  void saved() { _saved = _position; }
};

}} // namespace mesh::bible
