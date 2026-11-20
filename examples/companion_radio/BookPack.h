#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace books {

constexpr unsigned kSlots = 32, kTitle = 48, kChapterTitle = 32;
constexpr unsigned kHeader = 64, kChapter = 36, kPassage = 10;
constexpr unsigned kPlain = 768, kPacked = 800;
constexpr uint32_t kMaxBytes = 8UL * 1024 * 1024;

// MRB1: little-endian header, chapter directory, passage directory, then
// raw DEFLATE blocks. Passages share a block and point to a NUL-terminated
// string inside it, so lookups never decompress the entire book.

inline uint16_t u16(const uint8_t* b) { return b[0] | uint16_t(b[1]) << 8; }
inline uint32_t u32(const uint8_t* b) { return u16(b) | uint32_t(u16(b + 2)) << 16; }

inline uint32_t crcStep(uint32_t crc, const uint8_t* data, size_t n) {
  while (n--) {
    crc ^= *data++;
    for (unsigned i = 0; i < 8; ++i) crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1)));
  }
  return crc;
}

struct Book { uint32_t bytes; uint16_t chapters, passages; char title[kTitle]; };
struct Chapter { uint16_t first, count; char title[kChapterTitle]; };
struct Passage { uint32_t offset; uint16_t packed, plain, start; };

inline bool label(const uint8_t* b, char* title, size_t n) {
  const auto* end = static_cast<const uint8_t*>(memchr(b, 0, n));
  if (!end || end == b) return false;
  for (const uint8_t* p = b; p != end; ++p) if (*p < 32 || *p == 127) return false;
  memcpy(title, b, end - b + 1);
  return true;
}

template<class Read>
bool header(Read read, uint32_t bytes, Book& book) {
  uint8_t b[kHeader];
  if (bytes < kHeader || bytes > kMaxBytes || !read(0, b, sizeof(b))
      || memcmp(b, "MRB1", 4) || u32(b + 4) != bytes || u32(b + 60)) return false;
  book.bytes = bytes;
  book.chapters = u16(b + 8);
  book.passages = u16(b + 10);
  return book.chapters && book.chapters <= book.passages
      && kHeader + kChapter * book.chapters + kPassage * book.passages < bytes
      && label(b + 12, book.title, sizeof(book.title));
}

template<class Read>
bool chapterAt(Read read, const Book& book, unsigned index, Chapter& chapter) {
  uint8_t b[kChapter];
  if (index >= book.chapters || !read(kHeader + kChapter * index, b, sizeof(b))) return false;
  chapter.first = u16(b);
  chapter.count = u16(b + 2);
  return chapter.count && uint32_t(chapter.first) + chapter.count <= book.passages
      && label(b + 4, chapter.title, sizeof(chapter.title));
}

template<class Read>
bool passageAt(Read read, const Book& book, unsigned index, Passage& passage) {
  uint8_t b[kPassage];
  const uint32_t table = kHeader + kChapter * book.chapters;
  if (index >= book.passages || !read(table + kPassage * index, b, sizeof(b))) return false;
  passage = {u32(b), u16(b + 4), u16(b + 6), u16(b + 8)};
  return passage.packed && passage.packed <= kPacked && passage.plain <= kPlain
      && passage.start < passage.plain && passage.offset >= table + kPassage * book.passages
      && passage.offset <= book.bytes && passage.packed <= book.bytes - passage.offset;
}

}  // namespace books
}  // namespace mesh
