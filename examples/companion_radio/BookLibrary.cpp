#include "BookLibrary.h"
#if COMPANION_FEATURE_BOOKS
#include "DataStore.h"
#include <helpers/FileRead.h>
#include <helpers/ota/tinf/tinf.h>
#include <new>

namespace mesh {
namespace books {

Library library;
static constexpr const char* kStage = "/rdb-stage";

static void pathFor(unsigned slot, char (&path)[9]) {
  snprintf(path, sizeof(path), "/rdb-%02u", slot % kSlots);
}

static File openBook(DataStore& store, unsigned slot) {
  char path[9];
  pathFor(slot, path);
  FILESYSTEM* fs = store.readerFS();
  return slot < kSlots ? openFileRead(fs, path) : emptyFile(fs);
}

struct ReadFile {
  File& file;
  bool operator()(uint32_t offset, uint8_t* out, size_t n) const {
    return file.seek(offset) && file.read(out, n) == int(n);
  }
};

bool Library::book(DataStore& store, unsigned slot, Book& result) {
  File file = openBook(store, slot);
  if (!file) return false;
  const bool ok = header(ReadFile{file}, file.size(), result);
  file.close();
  return ok;
}

bool Library::chapter(DataStore& store, unsigned slot, unsigned index, Chapter& result) {
  File file = openBook(store, slot);
  Book book;
  if (!file) return false;
  const bool ok = header(ReadFile{file}, file.size(), book)
      && chapterAt(ReadFile{file}, book, index, result);
  file.close();
  return ok;
}

bool Library::passage(DataStore& store, unsigned slot, unsigned index, char (&out)[kPlain + 1]) {
  File file = openBook(store, slot);
  Book book;
  Passage passage;
  uint8_t compressed[kPacked];
  ReadFile read{file};
  if (!file) return false;
  const bool ok = header(read, file.size(), book) && passageAt(read, book, index, passage)
      && read(passage.offset, compressed, passage.packed);
  file.close();
  if (!ok) return false;
  unsigned plain = passage.plain;
  if (tinf_uncompress_exact(out, &plain, compressed, passage.packed) != TINF_OK
      || plain != passage.plain || out[plain - 1] != 0
      || (passage.start && out[passage.start - 1] != 0) || !out[passage.start]) return false;
  if (passage.start) memmove(out, out + passage.start, strlen(out + passage.start) + 1);
  return true;
}

void Library::closeUpload() {
  if (_upload) { _upload->close(); delete _upload; _upload = nullptr; }
}

void Library::abort() {
  closeUpload();
  if (_upload_fs) _upload_fs->remove(kStage);
  _upload_fs = nullptr;
}

void Library::poll() {
  if (_upload && uint32_t(millis() - _last) >= 20000) abort();
}

static bool number(const char*& input, uint32_t& value) {
  if (*input < '0' || *input > '9') return false;
  value = 0;
  do {
    const unsigned digit = *input++ - '0';
    if (value > (UINT32_MAX - digit) / 10) return false;
    value = value * 10 + digit;
  } while (*input >= '0' && *input <= '9');
  return true;
}

static int hex(char c) {
  return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

bool Library::command(DataStore& store, const char* input, char* reply, size_t cap) {
  if (strncmp(input, "rdb ", 4)) return false;
  input += 4;
  poll();
  auto status = [&](const char* text) { snprintf(reply, cap, "RDB %s", text); return true; };
  FILESYSTEM* fs = store.readerFS();
  if (_upload_fs && fs != _upload_fs) abort();
  if (!fs) return status("ERR No persistent book storage");
  if (!strcmp(input, "info")) {
    if (!_upload && fs->exists(kStage)) fs->remove(kStage);
    uint32_t slots = 0;
    char path[9];
    for (unsigned slot = 0; slot < kSlots; ++slot) {
      pathFor(slot, path);
      if (fs->exists(path)) slots |= uint32_t(1) << slot;
    }
    snprintf(reply, cap, "RDB INFO 1 %lu %lu", (unsigned long)store.readerFreeBytes(fs), (unsigned long)slots);
    return true;
  }
  if (!strcmp(input, "abort")) { abort(); return status("OK"); }
  if (!strncmp(input, "commit ", 7)) {
    const char* arg = input + 7;
    uint32_t expected;
    if (!_upload || _offset != _size) return status("ERR Incomplete upload");
    if (!number(arg, expected) || *arg || expected != ~_crc) {
      abort();
      return status("ERR Upload checksum mismatch");
    }
    closeUpload();
    File file = openFileRead(fs, kStage);
    const ReadFile read{file};
    Book book;
    Chapter chapter;
    Passage passage;
    const bool valid = file && header(read, file.size(), book) && book.bytes == _size
        && chapterAt(read, book, 0, chapter) && chapter.first == 0
        && chapterAt(read, book, book.chapters - 1, chapter)
        && uint32_t(chapter.first) + chapter.count == book.passages
        && passageAt(read, book, 0, passage) && passageAt(read, book, book.passages - 1, passage)
        && passage.offset + passage.packed == book.bytes;
    if (file) file.close();
    char path[9];
    pathFor(_slot, path);
    if (!valid || fs->exists(path) || !fs->rename(kStage, path)) {
      abort();
      return status("ERR Invalid pack or storage write failed");
    }
    _upload_fs = nullptr;
    snprintf(reply, cap, "RDB ADDED %u %lu", _slot, (unsigned long)~_crc);
    return true;
  }
  const char* arg = strchr(input, ' ');
  if (!arg) return status("ERR Unknown command");
  const size_t verb = arg - input;
  ++arg;
  auto is = [&](const char* name) { return strlen(name) == verb && !memcmp(input, name, verb); };
  uint32_t value;
  if (!number(arg, value)) return status("ERR Invalid argument");
  if (is("begin") && !*arg) {
    abort();
    if (fs->exists(kStage) && !fs->remove(kStage)) return status("ERR Staging unavailable");
    if (value <= kHeader || value > kMaxBytes || value > store.readerFreeBytes(fs))
      return status("ERR Not enough book storage");
    char path[9];
    for (_slot = 0; _slot < kSlots; ++_slot) { pathFor(_slot, path); if (!fs->exists(path)) break; }
    if (_slot == kSlots) return status("ERR Library is full");
#if defined(NRF52_PLATFORM)
    _upload = new (std::nothrow) File(fs->open(kStage, FILE_O_WRITE));
#else
    _upload = new (std::nothrow) File(fs->open(kStage, "w", true));
#endif
    _upload_fs = fs;
    if (!_upload || !*_upload) { abort(); return status("ERR Cannot open book storage"); }
    _size = value;
    _crc = UINT32_MAX;
    _offset = 0;
    _last = millis();
    return status("OK");
  }
  if (is("data") && *arg == ' ') {
    const char* bytes = arg + 1;
    const size_t digits = strlen(bytes), count = digits / 2;
    uint8_t data[76];
    if (!_upload || value != _offset || !count || digits % 2 || count > sizeof(data)
        || count > _size - _offset) return status("ERR Invalid upload offset or length");
    for (size_t i = 0; i < count; ++i) {
      const int hi = hex(bytes[i * 2]), lo = hex(bytes[i * 2 + 1]);
      if (hi < 0 || lo < 0) return status("ERR Invalid upload bytes");
      data[i] = (hi << 4) | lo;
    }
    if (_upload->write(data, count) != count) { abort(); return status("ERR Storage write failed"); }
    _crc = crcStep(_crc, data, count);
    _offset += count;
    _last = millis();
    snprintf(reply, cap, "RDB AT %lu", (unsigned long)_offset);
    return true;
  }
  if (is("book") && !*arg && value < kSlots) {
    Book book;
    if (!this->book(store, value, book)) {
      snprintf(reply, cap, "RDB BAD %lu", (unsigned long)value);
      return true;
    }
    snprintf(reply, cap, "RDB BOOK %lu %u %lu %s", (unsigned long)value,
             book.chapters, (unsigned long)book.bytes, book.title);
    return true;
  }
  if (is("remove") && *arg == ' ' && value < kSlots) {
    const unsigned slot = value;
    ++arg;
    Book book;
    if (!number(arg, value) || *arg++ != ' ' || !this->book(store, slot, book)
        || book.bytes != value || strcmp(book.title, arg)) return status("ERR Book changed; refresh library");
    char path[9];
    pathFor(slot, path);
    return fs->remove(path) ? status("OK") : status("ERR Remove failed");
  }
  if (is("remove-bad") && !*arg && value < kSlots) {
    Book book;
    char path[9];
    pathFor(value, path);
    if (this->book(store, value, book)) return status("ERR Book changed; refresh library");
    return fs->remove(path) ? status("OK") : status("ERR Remove failed");
  }
  return status("ERR Invalid book command");
}

}  // namespace books
}  // namespace mesh
#endif
