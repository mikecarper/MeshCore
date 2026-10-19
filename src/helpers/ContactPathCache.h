#pragma once

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "PersistentStoreFormat.h"

namespace mesh {

// Paths are immutable values shared by ContactInfo copies. Editing a copy uses
// a new handle, so a pending app response or a rollback keeps its original path.
// Only the 16 most recently used path byte arrays are resident. Small handles
// retain the location of the other paths in the existing contact files.
class ContactPathBackend {
public:
  virtual bool readStoredPath(uint16_t source, uint8_t path[64]) = 0;
  virtual bool flushCachedPaths() = 0;
  // A lazy streaming transaction may be abandoned before the existing
  // synchronous eviction flush. Synchronous/paged commits keep their veto.
  virtual bool cancelCooperativeWrite() { return false; }
};

class ContactPathStorage {
public:
  static const uint16_t NONE = 0xffff;
  static const uint16_t PAGED = 0x8000;
  virtual void attach(ContactPathBackend* backend) = 0;
  virtual uint16_t bind(uint16_t source, const uint8_t path[64]) = 0;
  virtual void retain(uint16_t handle) = 0;
  virtual void release(uint16_t handle) = 0;
  virtual bool read(uint16_t handle, uint8_t path[64]) = 0;
  virtual const uint8_t* view(uint16_t handle) = 0;
  virtual bool replace(uint16_t& handle, const uint8_t path[64]) = 0;
  virtual uint16_t source(uint16_t handle) const = 0;
  virtual void beginCommit() = 0;
  virtual void mark(uint16_t handle) = 0;
  virtual bool preserveSnapshots(uint16_t first, uint16_t count) = 0;
  virtual void publish(uint16_t handle, uint16_t source) = 0;
  virtual void endCommit(bool success) = 0;
};

ContactPathStorage& contactPathStorage();

class ContactPathRef {
  uint16_t _handle = 0;
public:
  ContactPathRef() = default;
  ContactPathRef(const ContactPathRef& other) : _handle(other._handle) {
    if (_handle) contactPathStorage().retain(_handle);
  }
  ContactPathRef& operator=(const ContactPathRef& other) {
    if (this != &other) {
      if (other._handle) contactPathStorage().retain(other._handle);
      if (_handle) contactPathStorage().release(_handle);
      _handle = other._handle;
    }
    return *this;
  }
  ~ContactPathRef() { if (_handle) contactPathStorage().release(_handle); }
  bool bind(uint16_t source, const uint8_t path[64]) {
    uint16_t next = contactPathStorage().bind(source, path);
    if (next == ContactPathStorage::NONE) return false;
    if (_handle) contactPathStorage().release(_handle);
    _handle = next;
    return true;
  }
  bool set(const uint8_t path[64]) {
    return contactPathStorage().replace(_handle, path);
  }
  bool read(uint8_t path[64]) const {
    return contactPathStorage().read(_handle, path);
  }
  const uint8_t* view() const { return contactPathStorage().view(_handle); }
  uint16_t handle() const { return _handle; }
};

template<size_t Handles, size_t Resident = 16>
class ContactPathPool : public ContactPathStorage {
  static_assert(Handles > Resident && Handles < 0xffff, "Invalid path handle capacity");
  static_assert(Resident > 0 && Resident < 255, "Invalid path cache capacity");
  enum : uint8_t { DIRTY = 1, MARKED = 2, DETACH = 4 };
  struct Slot {
    uint32_t checksum = 0;
    uint16_t refs = 0;
    uint16_t location = NONE;
    uint8_t resident = 255;
    uint8_t flags = 0;
  } _slots[Handles];
  struct Entry {
    uint8_t path[64] = {};
    uint16_t handle = 0;
    uint8_t age = 0;
  } _entries[Resident];
  ContactPathBackend* _backend = nullptr;
  bool _committing = false;
  bool _flushing = false;

  static uint32_t checksum(const uint8_t path[64]) {
    return storage::updateCRC32(0xffffffffUL, path, 64);
  }

  Slot* slot(uint16_t h) {
    return h > 0 && h <= Handles && _slots[h - 1].refs ? &_slots[h - 1] : nullptr;
  }
  void touch(size_t index) {
    // Bounded ranks avoid millis/counter wrap affecting LRU ordering.
    for (size_t i = 0; i < Resident; ++i) {
      if (_entries[i].handle && _entries[i].age < Resident) ++_entries[i].age;
    }
    _entries[index].age = 0;
  }
  int availableEntry() {
    int oldest = -1;
    for (size_t i = 0; i < Resident; ++i) {
      Slot* s = slot(_entries[i].handle);
      if (!s) return static_cast<int>(i);
      if (s->location != NONE && !(s->flags & (DIRTY | DETACH)) &&
          (oldest < 0 || _entries[i].age > _entries[oldest].age)) oldest = i;
    }
    return oldest;
  }
  int makeRoom() {
    int index = availableEntry();
    if (index < 0 && _backend && _committing && !_flushing) {
      _backend->cancelCooperativeWrite();
    }
    if (index < 0 && _backend && !_committing && !_flushing) {
      _flushing = true;
      _backend->flushCachedPaths();
      _flushing = false;
      index = availableEntry();
    }
    if (index >= 0) {
      Slot* old = slot(_entries[index].handle);
      if (old) old->resident = 255;
      _entries[index].handle = 0;
    }
    return index;
  }
  uint16_t allocate() {
    for (size_t i = 0; i < Handles; ++i) {
      if (!_slots[i].refs) {
        _slots[i] = Slot();
        _slots[i].refs = 1;
        return static_cast<uint16_t>(i + 1);
      }
    }
    return NONE;
  }
  int load(uint16_t h) {
    Slot* s = slot(h);
    if (!s) return -1;
    if (s->resident != 255) { touch(s->resident); return s->resident; }
    // A flush in makeRoom() may move this source. Read its current location
    // afterward. read() itself never allocates or starts a persistence write.
    if (!_backend || s->location == NONE) return -1;
    int index = makeRoom();
    if (index < 0) return -1;
    if (!_backend->readStoredPath(s->location, _entries[index].path) ||
        checksum(_entries[index].path) != s->checksum) return -1;
    _entries[index].handle = h;
    s->resident = index;
    touch(index);
    return index;
  }

public:
  // Construct the empty cache on first use instead of storing a flash load
  // image of every slot's NONE/255 sentinels and the vtable pointer. Keep the
  // member defaults for both initial construction and Slot() resets.
  ContactPathPool() {}

  void attach(ContactPathBackend* backend) override { _backend = backend; }
  uint16_t bind(uint16_t location, const uint8_t path[64]) override {
    if (location == NONE) return NONE;
    const uint16_t h = allocate();
    if (h != NONE) {
      _slots[h - 1].location = location;
      _slots[h - 1].checksum = checksum(path);
    }
    return h;
  }
  void retain(uint16_t h) override { Slot* s = slot(h); if (s) ++s->refs; }
  void release(uint16_t h) override {
    Slot* s = slot(h);
    if (!s || --s->refs) return;
    if (s->resident != 255) _entries[s->resident].handle = 0;
    *s = Slot();
  }
  bool read(uint16_t h, uint8_t path[64]) override {
    if (!h) { memset(path, 0, 64); return true; }
    Slot* s = slot(h);
    if (!s) return false;
    if (s->resident != 255) {
      memcpy(path, _entries[s->resident].path, 64);
      touch(s->resident);
      return true;
    }
    // Serializing a whole contact table must not evict a dirty entry and
    // recursively rewrite the table it is currently reading.
    return _backend && s->location != NONE &&
        _backend->readStoredPath(s->location, path) && checksum(path) == s->checksum;
  }
  const uint8_t* view(uint16_t h) override {
    static const uint8_t empty[64] = {};
    if (!h) return empty;
    const int index = load(h);
    return index < 0 ? nullptr : _entries[index].path;
  }
  bool replace(uint16_t& h, const uint8_t path[64]) override {
    uint8_t previous[64];
    if (read(h, previous) && memcmp(previous, path, 64) == 0) return true;
    bool empty = true;
    for (size_t i = 0; i < 64; ++i) empty = empty && path[i] == 0;
    if (empty) { release(h); h = 0; return true; }
    Slot* old = slot(h);
    // In-place mutation is safe only when there is no ContactInfo snapshot.
    if (old && old->refs == 1 && old->resident != 255) {
      memcpy(_entries[old->resident].path, path, 64);
      old->flags |= DIRTY;
      old->checksum = checksum(path);
      touch(old->resident);
      return true;
    }
    const uint16_t next = allocate();
    if (next == NONE) return false;
    const int index = makeRoom();
    if (index < 0) { release(next); return false; }
    Slot& s = _slots[next - 1];
    s.location = old ? old->location : NONE;
    s.flags = DIRTY;
    s.checksum = checksum(path);
    s.resident = index;
    memcpy(_entries[index].path, path, 64);
    _entries[index].handle = next;
    touch(index);
    release(h);
    h = next;
    return true;
  }
  uint16_t source(uint16_t h) const override {
    return h && h <= Handles && _slots[h - 1].refs ? _slots[h - 1].location : NONE;
  }
  void beginCommit() override {
    _committing = true;
    for (auto& s : _slots) s.flags &= ~(MARKED | DETACH);
  }
  void mark(uint16_t h) override { Slot* s = slot(h); if (s) s->flags |= MARKED; }
  bool preserveSnapshots(uint16_t first, uint16_t count) override {
    for (size_t i = 0; i < Handles; ++i) {
      Slot& s = _slots[i];
      if (!s.refs || s.location == NONE || (s.flags & MARKED) ||
          s.location < first || static_cast<uint32_t>(s.location) >=
              static_cast<uint32_t>(first) + count) continue;
      if (load(i + 1) < 0) return false;
      s.flags |= DETACH;
    }
    return true;
  }
  void publish(uint16_t h, uint16_t location) override {
    Slot* s = slot(h);
    if (s) { s->location = location; s->flags &= ~DIRTY; }
  }
  void endCommit(bool success) override {
    for (auto& s : _slots) {
      if (success && (s.flags & DETACH)) s.location = NONE;
      s.flags &= ~(MARKED | DETACH);
    }
    _committing = false;
  }
};

} // namespace mesh
