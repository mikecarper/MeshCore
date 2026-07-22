#include "RegionMap.h"
#include <helpers/RegionNameUtils.h>
#include <helpers/TxtDataHelpers.h>
#if defined(NRF52_PLATFORM)
#include <helpers/AtomicFileWriter.h>
#endif
#include <SHA256.h>

// helper class for region map exporter, we emulate Stream with a safe buffer writer.

class BufStream : public Stream {
public:
  BufStream(char *buf, size_t max_len)
    : _buf(buf), _max_len(max_len), _pos(0) {
    if (_max_len > 0) _buf[0] = 0;
  }

  size_t write(uint8_t c) override {
    if (_pos + 1 >= _max_len) return 0;
    _buf[_pos++] = c;
    _buf[_pos] = 0;
    return 1;
  }

  size_t write(const uint8_t *buffer, size_t size) override {
    size_t written = 0;
    while (written < size) {
      if (!write(buffer[written])) break;
      written++;
    }
    return written;
  }

  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}

  size_t length() const { return _pos; }

private:
  char *_buf;
  size_t _max_len;
  size_t _pos;
};


RegionMap::RegionMap(TransportKeyStore& store) : _store(&store) {
  next_id = 1; num_regions = 0;
  default_id = home_id = 0;
  wildcard.id = wildcard.parent = 0;
  wildcard.flags = 0;  // default behaviour, allow flood and direct
  strcpy(wildcard.name, "*");
}

bool RegionMap::is_name_char(uint8_t c) {
  // accept all alpha-num or accented characters, but exclude most punctuation chars
  return c == '-' || c == '$' || c == '#' || (c >= '0' && c <= '9') || c >= 'A';
}

static const char* skip_hash(const char* name) {
  return RegionNameUtils::canonical(name);
}

#if !defined(NRF52_PLATFORM)
static File openWrite(FILESYSTEM* _fs, const char* filename) {
  #if defined(STM32_PLATFORM)
    _fs->remove(filename);
    return _fs->open(filename, FILE_O_WRITE);
  #elif defined(RP2040_PLATFORM)
    return _fs->open(filename, "w");
  #else
    return _fs->open(filename, "w", true);
  #endif
}
#endif

bool RegionMap::load(FILESYSTEM* _fs, const char* path) {
  if (_fs->exists(path ? path : "/regions2")) {
  #if defined(RP2040_PLATFORM)
    File file = _fs->open(path ? path : "/regions2", "r");
  #else
    File file = _fs->open(path ? path : "/regions2");
  #endif

    if (file) {
      uint8_t pad[128];
      RegionEntry loaded[MAX_REGION_ENTRIES];
      uint16_t loaded_count = 0;
      uint16_t loaded_default = 0;
      uint16_t loaded_home = 0;
      uint16_t loaded_next = 1;
      uint8_t loaded_wildcard_flags = 0;

      bool success = file.read(pad, 3) == 3;  // reserved header
      success = success && file.read((uint8_t *) &loaded_default, sizeof(loaded_default)) == sizeof(loaded_default);
      success = success && file.read((uint8_t *) &loaded_home, sizeof(loaded_home)) == sizeof(loaded_home);
      success = success && file.read((uint8_t *) &loaded_wildcard_flags, sizeof(loaded_wildcard_flags)) == sizeof(loaded_wildcard_flags);
      success = success && file.read((uint8_t *) &loaded_next, sizeof(loaded_next)) == sizeof(loaded_next);

      while (success && loaded_count < MAX_REGION_ENTRIES) {
        RegionEntry& r = loaded[loaded_count];
        memset(&r, 0, sizeof(r));
        int n = file.read((uint8_t *) &r.id, sizeof(r.id));
        if (n == 0) break;  // clean EOF
        success = n == sizeof(r.id);
        success = success && file.read((uint8_t *) &r.parent, sizeof(r.parent)) == sizeof(r.parent);
        success = success && file.read((uint8_t *) r.name, sizeof(r.name)) == sizeof(r.name);
        success = success && file.read((uint8_t *) &r.flags, sizeof(r.flags)) == sizeof(r.flags);
        success = success && file.read(pad, sizeof(pad)) == sizeof(pad);
        if (!success) break;

        const char* terminator = (const char*)memchr(r.name, 0, sizeof(r.name));
        if (r.id == 0 || r.id == 0xFFFF || terminator == NULL || r.name[0] == 0
            || RegionNameUtils::canonical(r.name)[0] == 0) {
          success = false;
          break;
        }
        for (const char* p = r.name; *p; p++) {
          if (!is_name_char((uint8_t)*p)) {
            success = false;
            break;
          }
        }
        if (!success) break;
        loaded_count++;
      }
      if (success && loaded_count == MAX_REGION_ENTRIES && file.read() >= 0) {
        success = false;  // more data than the table can hold
      }
      file.close();

      // Validate IDs and parent links only after every record is present so forward
      // parent references are allowed. A bounded walk also rejects cycles.
      for (uint16_t i = 0; success && i < loaded_count; i++) {
        for (uint16_t j = i + 1; j < loaded_count; j++) {
          if (loaded[i].id == loaded[j].id
              || RegionNameUtils::equivalent(loaded[i].name, loaded[j].name)) {
            success = false;
          }
        }
        uint16_t parent = loaded[i].parent;
        for (uint16_t depth = 0; success && parent != 0; depth++) {
          if (depth >= loaded_count || parent == loaded[i].id) {
            success = false;
            break;
          }
          const RegionEntry* found = NULL;
          for (uint16_t j = 0; j < loaded_count; j++) {
            if (loaded[j].id == parent) {
              found = &loaded[j];
              break;
            }
          }
          if (found == NULL) {
            success = false;
            break;
          }
          parent = found->parent;
        }
      }

      bool default_found = loaded_default == 0;
      bool home_found = loaded_home == 0;
      uint16_t minimum_next = 1;
      for (uint16_t i = 0; success && i < loaded_count; i++) {
        if (loaded[i].id == loaded_default) default_found = true;
        if (loaded[i].id == loaded_home) home_found = true;
        if (loaded[i].id >= minimum_next) minimum_next = loaded[i].id + 1;
      }
      if (!default_found || !home_found) success = false;
      if (loaded_next == 0 || loaded_next == 0xFFFF || loaded_next < minimum_next) loaded_next = minimum_next;
      if (loaded_next == 0xFFFF && loaded_count < MAX_REGION_ENTRIES) success = false;

      if (success) {
        memcpy(regions, loaded, loaded_count * sizeof(RegionEntry));
        if (loaded_count < MAX_REGION_ENTRIES) {
          memset(&regions[loaded_count], 0, (MAX_REGION_ENTRIES - loaded_count) * sizeof(RegionEntry));
        }
        num_regions = loaded_count;
        default_id = loaded_default;
        home_id = loaded_home;
        next_id = loaded_next;
        wildcard.flags = loaded_wildcard_flags;
      }
      return success;
    }
  }
  return false;  // failed
}

bool RegionMap::save(FILESYSTEM* _fs, const char* path) {
  const char* target_path = path ? path : "/regions2";
#if defined(NRF52_PLATFORM)
  mesh::AtomicFileWriter file(_fs, target_path);
#else
  File file = openWrite(_fs, target_path);
#endif
  if (file) {
    uint8_t pad[128];
    memset(pad, 0, sizeof(pad));

    bool success = file.write(pad, 3) == 3;  // reserved header
    success = success && file.write((uint8_t *) &default_id, sizeof(default_id)) == sizeof(default_id);
    success = success && file.write((uint8_t *) &home_id, sizeof(home_id)) == sizeof(home_id);
    success = success && file.write((uint8_t *) &wildcard.flags, sizeof(wildcard.flags)) == sizeof(wildcard.flags);
    success = success && file.write((uint8_t *) &next_id, sizeof(next_id)) == sizeof(next_id);

    if (success) {
      for (int i = 0; i < num_regions; i++) {
        auto r = &regions[i];

        success = file.write((uint8_t *) &r->id, sizeof(r->id)) == sizeof(r->id);
        success = success && file.write((uint8_t *) &r->parent, sizeof(r->parent)) == sizeof(r->parent);
        success = success && file.write((uint8_t *) r->name, sizeof(r->name)) == sizeof(r->name);
        success = success && file.write((uint8_t *) &r->flags, sizeof(r->flags)) == sizeof(r->flags);
        success = success && file.write(pad, sizeof(pad)) == sizeof(pad);
        if (!success) break; // write failed
      }
    }
#if defined(NRF52_PLATFORM)
    success = file.commit(success);
#else
    file.close();
#endif
    return success;
  }
  return false;  // failed
}

RegionEntry* RegionMap::putRegion(const char* name, uint16_t parent_id, uint16_t id) {
  if (name == NULL || RegionNameUtils::canonical(name)[0] == 0) return NULL;

  size_t name_len = 0;
  const char* sp = name;  // check length and illegal name chars in one pass
  while (*sp) {
    if (!is_name_char(*sp) || ++name_len >= sizeof(regions[0].name)) return NULL;
    sp++;
  }

  auto region = findByName(name);
  if (region) {
    if ((id != 0 && id != region->id) || wouldCreateCycle(region->id, parent_id)) {
      return NULL;
    }

    region->parent = parent_id;   // re-parent / move this region in the hierarchy
  } else {
    if (num_regions >= MAX_REGION_ENTRIES) return NULL;  // full!
    if (findById(parent_id) == NULL) return NULL;
    if (id == 0xFFFF || (id == 0 && (next_id == 0 || next_id == 0xFFFF))) return NULL;
    if (id != 0 && findById(id) != NULL) return NULL;
    // 0xFFFF is the reserved/exhausted sentinel. Only consume 0xFFFE when this
    // entry fills the table; otherwise saving would produce a map that load()
    // correctly rejects as unable to allocate its remaining slots.
    if ((id == 0 ? next_id : id) == 0xFFFE
        && num_regions + 1 < MAX_REGION_ENTRIES) return NULL;

    region = &regions[num_regions++];   // alloc new RegionEntry
    region->flags = REGION_DENY_FLOOD;     // DENY by default
    if (id == 0) {
      region->id = next_id++;
    } else {
      region->id = id;
      if (id >= next_id) next_id = id + 1;
    }
    StrHelper::strncpy(region->name, name, sizeof(region->name));
    region->parent = parent_id;
  }
  return region;
}

int RegionMap::getTransportKeysFor(const RegionEntry& src, TransportKey dest[], int max_num) {
  int num;
  if (src.name[0] == '$') {   // private region
    num = _store->loadKeysFor(src.id, dest, max_num);
  } else if (src.name[0] == '#') {   // auto hashtag region
    _store->getAutoKeyFor(src.id, src.name, dest[0]);
    num = 1;
  } else {   // new: implicit auto hashtag region
    char tmp[sizeof(src.name)+1];
    tmp[0] = '#';
    strcpy(&tmp[1], src.name);
    _store->getAutoKeyFor(src.id, tmp, dest[0]);
    num = 1;
  }
  return num;
}

RegionEntry* RegionMap::findMatch(const mesh::Packet* packet, uint8_t mask) {
  for (int i = 0; i < num_regions; i++) {
    auto region = &regions[i];
    if ((region->flags & mask) == 0) {   // does region allow this? (per 'mask' param)
      TransportKey keys[4];
      int num = getTransportKeysFor(*region, keys, 4);
      for (int j = 0; j < num; j++) {
        uint16_t code = keys[j].calcTransportCode(packet);
        if (packet->transport_codes[0] == code) {   // a match!!
          return region;
        }
      }
    }
  }
  return NULL;  // no matches
}

RegionEntry* RegionMap::findByName(const char* name) {
  if (name == NULL) return NULL;
  if (strcmp(name, "*") == 0) return &wildcard;

  if (*name == '#') { name++; }  // ignore the '#' when matching by name
  for (int i = 0; i < num_regions; i++) {
    auto region = &regions[i];
    if (strcmp(name, skip_hash(region->name)) == 0) return region;
  }
  return NULL;  // not found
}

RegionEntry* RegionMap::findByNamePrefix(const char* prefix) {
  if (prefix == NULL) return NULL;
  if (strcmp(prefix, "*") == 0) return &wildcard;

  if (*prefix == '#') { prefix++; }  // ignore the '#' when matching by name
  if (*prefix == 0) return NULL;
  size_t prefix_len = strlen(prefix);
  RegionEntry* partial = NULL;
  bool ambiguous = false;
  for (int i = 0; i < num_regions; i++) {
    auto region = &regions[i];
    const char* candidate = skip_hash(region->name);
    if (strcmp(prefix, candidate) == 0) return region;  // exact matches always win
    if (strlen(candidate) >= prefix_len && memcmp(prefix, candidate, prefix_len) == 0) {
      if (partial != NULL) {
        ambiguous = true;
      } else {
        partial = region;
      }
    }
  }
  return ambiguous ? NULL : partial;
}

RegionEntry* RegionMap::findById(uint16_t id) {
  if (id == 0) return &wildcard;   // special root Region

  for (int i = 0; i < num_regions; i++) {
    auto region = &regions[i];
    if (region->id == id) return region;
  }
  return NULL;  // not found
}

RegionEntry* RegionMap::getHomeRegion() {
  return findById(home_id);
}

void RegionMap::setHomeRegion(const RegionEntry* home) {
  home_id = home ? home->id : 0;
}

RegionEntry* RegionMap::getDefaultRegion() {
  return default_id == 0 ? NULL : findById(default_id);
}

void RegionMap::setDefaultRegion(const RegionEntry* def) {
  default_id = def ? def->id : 0;
}

bool RegionMap::removeRegion(const RegionEntry& region) {
  if (region.id == 0) return false;  // failed (cannot remove the wildcard Region)

  int i;     // first check region has no child regions
  for (i = 0; i < num_regions; i++) {
    if (regions[i].parent == region.id) return false;   // failed (must remove child Regions first)
  }

  i = 0;
  while (i < num_regions) {
    if (region.id == regions[i].id) break;
    i++;
  }
  if (i >= num_regions) return false;  // failed (not found)

  num_regions--;    // remove from regions array
  while (i < num_regions) {
    regions[i] = regions[i + 1];
    i++;
  }
  memset(&regions[num_regions], 0, sizeof(regions[num_regions]));
  if (default_id == region.id) default_id = 0;
  if (home_id == region.id) home_id = 0;
  return true;  // success
}

bool RegionMap::clear() {
  num_regions = 0;
  next_id = 1;
  default_id = home_id = 0;
  memset(regions, 0, sizeof(regions));
  wildcard.id = wildcard.parent = 0;
  wildcard.flags = 0;
  strcpy(wildcard.name, "*");
  return true;  // success
}

bool RegionMap::wouldCreateCycle(uint16_t region_id, uint16_t parent_id) const {
  uint16_t current = parent_id;
  for (uint16_t depth = 0; current != 0; depth++) {
    if (current == region_id || depth >= num_regions) return true;

    const RegionEntry* parent = NULL;
    for (uint16_t i = 0; i < num_regions; i++) {
      if (regions[i].id == current) {
        parent = &regions[i];
        break;
      }
    }
    if (parent == NULL) return true;
    current = parent->parent;
  }
  return false;
}

void RegionMap::resetFrom(const RegionMap& src) {
  next_id = src.next_id;
  home_id = src.home_id;
  default_id = src.default_id;
  num_regions = 0;
  memset(regions, 0, sizeof(regions));
  wildcard = src.wildcard;
}

void RegionMap::printChildRegions(int indent, const RegionEntry* parent, Stream& out) const {
  for (int i = 0; i < indent; i++) {
    out.print(' ');
  }

  if (parent->flags & REGION_DENY_FLOOD) {
    out.printf("%s%s\n", skip_hash(parent->name), parent->id == home_id ? "^" : "");
  } else {
    out.printf("%s%s F\n", skip_hash(parent->name), parent->id == home_id ? "^" : "");
  }

  for (int i = 0; i < num_regions; i++) {
    auto r = &regions[i];
    if (r->parent == parent->id) {
      printChildRegions(indent + 1, r, out);
    }
  }
}

void RegionMap::exportTo(Stream& out) const {
  printChildRegions(0, &wildcard, out);   // recursive
}

size_t RegionMap::exportTo(char *dest, size_t max_len) const {
  if (!dest || max_len == 0) return 0;

  BufStream bs(dest, max_len);
  exportTo(bs);              // <- reuse existing logic
  return bs.length();
}

int RegionMap::exportNamesTo(char *dest, int max_len, uint8_t mask, bool invert) {
  char *dp = dest;
  
  // Check wildcard region
  bool wildcard_matches = invert ? (wildcard.flags & mask) : !(wildcard.flags & mask);
  if (wildcard_matches) {
    *dp++ = '*';
    *dp++ = ',';
  }

    for (int i = 0; i < num_regions; i++) {
    auto region = &regions[i];
    
    // Check if region matches the filter criteria
    bool region_matches = invert ? (region->flags & mask) : !(region->flags & mask);
    
    if (region_matches) {
      int len = strlen(skip_hash(region->name));
      if ((dp - dest) + len + 2 < max_len) {   // only append if name will fit
        memcpy(dp, skip_hash(region->name), len);
        dp += len;
        *dp++ = ',';
      }
    }
  }

  if (dp > dest) { dp--; }   // don't include trailing comma

  *dp = 0;  // set null terminator
  return dp - dest;   // return length
}
