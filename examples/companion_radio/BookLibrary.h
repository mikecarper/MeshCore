#pragma once

#include <helpers/CompanionReaderConfig.h>
#if COMPANION_FEATURE_BOOKS
#include <helpers/IdentityStore.h>
#include "BookPack.h"

class DataStore;

namespace mesh {
namespace books {

class Library {
  File* _upload = nullptr;
  FILESYSTEM* _upload_fs = nullptr;
  uint32_t _size = 0, _offset = 0, _crc = 0, _last = 0;
  unsigned _slot = kSlots;
  void closeUpload();

public:
  void abort();
  bool book(DataStore& store, unsigned slot, Book& result);
  bool chapter(DataStore& store, unsigned slot, unsigned index, Chapter& result);
  bool passage(DataStore& store, unsigned slot, unsigned index, char (&out)[kPlain + 1]);
  bool command(DataStore& store, const char* input, char* reply, size_t cap);
  void poll();
};

extern Library library;

}  // namespace books
}  // namespace mesh
#endif
