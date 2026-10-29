#pragma once

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <helpers/TxtDataHelpers.h>

// Named radio presets: a small fixed table, a CRC-protected binary image for
// persistence, and text formatting for the "get preset" CLI replies.
// Slots are 1-based. Pure logic; no hardware access.
namespace mesh {

struct RadioPreset {
  char name[13];
  float freq;
  float bw;
  uint8_t sf;
  uint8_t cr;
  uint16_t preamble;  // 0 = default/auto
  bool used;
};

struct RadioPresetPending {
  bool active;
  uint8_t slot;
  RadioPreset previous;
  uint32_t remaining_secs;
};

class RadioPresetTable {
 public:
  static constexpr uint8_t SLOTS = 5, MAX_NAME_LEN = 12;
  static constexpr size_t ENCODED_MAX = 160;

  RadioPresetTable() { clearAll(); }

  static bool validName(const char* name) {
    if (!name) return false;
    size_t n = strlen(name);
    if (n == 0 || n > MAX_NAME_LEN) return false;
    for (size_t i = 0; i < n; i++) {
      unsigned char c = (unsigned char)name[i];
      if (c <= ' ' || c == ',' || c >= 0x7F) return false;
    }
    return true;
  }

  bool set(uint8_t slot, const char* name, float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble) {
    if (slot < 1 || slot > SLOTS || !validName(name)) return false;
    RadioPreset& p = slots_[slot - 1];
    memset(&p, 0, sizeof(p));
    memcpy(p.name, name, strlen(name));
    p.freq = freq;
    p.bw = bw;
    p.sf = sf;
    p.cr = cr;
    p.preamble = preamble;
    p.used = true;
    return true;
  }

  bool clear(uint8_t slot) {
    if (slot < 1 || slot > SLOTS) return false;
    memset(&slots_[slot - 1], 0, sizeof(RadioPreset));
    return true;
  }

  const RadioPreset* get(uint8_t slot) const {
    if (slot < 1 || slot > SLOTS || !slots_[slot - 1].used) return nullptr;
    return &slots_[slot - 1];
  }

  uint8_t match(float freq, float bw, uint8_t sf, uint8_t cr, uint16_t preamble) const {
    for (uint8_t i = 0; i < SLOTS; i++) {
      const RadioPreset& p = slots_[i];
      if (p.used && fabsf(p.freq - freq) < 0.0005f && fabsf(p.bw - bw) < 0.05f && p.sf == sf &&
          p.cr == cr && p.preamble == preamble) {
        return i + 1;
      }
    }
    return 0;
  }

  RadioPresetPending pending;  // persisted with the table

  // Layout: 'R','P', version, SLOTS records, pending slot (0 = none), pending
  // remaining secs, pending previous record, CRC32 of everything before it.
  size_t encode(uint8_t* out, size_t cap) const {
    if (!out || cap < IMAGE_SIZE) return 0;
    size_t o = 0;
    out[o++] = 'R';
    out[o++] = 'P';
    out[o++] = VERSION;
    for (uint8_t i = 0; i < SLOTS; i++) o += putRecord(out + o, slots_[i]);
    out[o++] = pending.active ? pending.slot : 0;
    put32(out + o, pending.active ? pending.remaining_secs : 0);
    o += 4;
    RadioPreset none;
    memset(&none, 0, sizeof(none));
    o += putRecord(out + o, pending.active ? pending.previous : none);
    put32(out + o, crc32(out, o));
    o += 4;
    return o;
  }

  // On any failure the table (including pending) is left cleared.
  bool decode(const uint8_t* in, size_t len) {
    clearAll();
    if (!in || len != IMAGE_SIZE || in[0] != 'R' || in[1] != 'P' || in[2] != VERSION) return false;
    if (get32(in + len - 4) != crc32(in, len - 4)) return false;
    size_t o = 3;
    for (uint8_t i = 0; i < SLOTS; i++) {
      getRecord(in + o, slots_[i]);
      o += RECORD_SIZE;
    }
    uint8_t pslot = in[o++];
    if (pslot > SLOTS) {
      clearAll();
      return false;
    }
    uint32_t remaining = get32(in + o);
    o += 4;
    if (pslot != 0) {
      pending.active = true;
      pending.slot = pslot;
      pending.remaining_secs = remaining;
      getRecord(in + o, pending.previous);
    }
    return true;
  }

 private:
  static constexpr uint8_t VERSION = 1;
  // Wire record: name (12, no terminator) + freq + bw + sf + cr + preamble.
  static constexpr size_t RECORD_SIZE = MAX_NAME_LEN + 4 + 4 + 1 + 1 + 2;
  static constexpr size_t IMAGE_SIZE = 3 + SLOTS * RECORD_SIZE + 1 + 4 + RECORD_SIZE + 4;
  static_assert(IMAGE_SIZE <= ENCODED_MAX, "ENCODED_MAX too small for table + pending");

  RadioPreset slots_[SLOTS];

  void clearAll() {
    memset(slots_, 0, sizeof(slots_));
    memset(&pending, 0, sizeof(pending));
  }

  static void put32(uint8_t* p, uint32_t v) {
    for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i));
  }
  static uint32_t get32(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
  }
  static void putFloat(uint8_t* p, float f) {
    uint32_t v;
    memcpy(&v, &f, 4);
    put32(p, v);
  }
  static float getFloat(const uint8_t* p) {
    uint32_t v = get32(p);
    float f;
    memcpy(&f, &v, 4);
    return f;
  }

  // An unused record is all zero; "used" is implied by a non-empty name.
  static size_t putRecord(uint8_t* p, const RadioPreset& r) {
    memset(p, 0, RECORD_SIZE);
    if (r.used) {
      memcpy(p, r.name, MAX_NAME_LEN);
      putFloat(p + 12, r.freq);
      putFloat(p + 16, r.bw);
      p[20] = r.sf;
      p[21] = r.cr;
      p[22] = (uint8_t)(r.preamble & 0xFF);
      p[23] = (uint8_t)(r.preamble >> 8);
    }
    return RECORD_SIZE;
  }
  static void getRecord(const uint8_t* p, RadioPreset& r) {
    memset(&r, 0, sizeof(r));
    if (p[0] == 0) return;
    memcpy(r.name, p, MAX_NAME_LEN);
    r.name[MAX_NAME_LEN] = 0;
    r.freq = getFloat(p + 12);
    r.bw = getFloat(p + 16);
    r.sf = p[20];
    r.cr = p[21];
    r.preamble = (uint16_t)(p[22] | (p[23] << 8));
    r.used = validName(r.name);
  }

  // Standard CRC-32 (IEEE, reflected), bitwise so it needs no table.
  static uint32_t crc32(const uint8_t* d, size_t n) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
      crc ^= d[i];
      for (int b = 0; b < 8; b++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
  }
};

// "<slot>[*]<name> <freq>,<bw>,<sf>,<cr>[,<preamble>]" or "<slot> -" when unused.
// '*' marks the active slot; freq/bw use the same formatting as "get radio".
inline size_t formatPreset(const RadioPresetTable& t, uint8_t slot, uint8_t active, char* out, size_t cap) {
  if (!out || cap == 0) return 0;
  const RadioPreset* p = t.get(slot);
  int w;
  if (!p) {
    w = snprintf(out, cap, "%u -", (unsigned)slot);
  } else {
    char freq[16], bw[16];  // ftoa/ftoa3 return shared static buffers; copy before the second call
    snprintf(freq, sizeof(freq), "%s", StrHelper::ftoa(p->freq));
    snprintf(bw, sizeof(bw), "%s", StrHelper::ftoa3(p->bw));
    w = snprintf(out, cap, "%u%c%s %s,%s,%u,%u", (unsigned)slot, slot == active ? '*' : ' ', p->name, freq, bw,
                 (unsigned)p->sf, (unsigned)p->cr);
    if (w > 0 && (size_t)w < cap && p->preamble) {
      int w2 = snprintf(out + w, cap - (size_t)w, ",%u", (unsigned)p->preamble);
      if (w2 > 0) w += w2;
    }
  }
  if (w < 0) {
    out[0] = 0;
    return 0;
  }
  return (size_t)w < cap ? (size_t)w : cap - 1;
}

// Full form is one preset per line. If it does not fit in `cap`, falls back to
// names only plus a hint: "1*Primary 2 Private 3 - 4 - 5 - (get preset N)".
inline size_t formatPresetList(const RadioPresetTable& t, uint8_t active, char* out, size_t cap) {
  if (!out || cap == 0) return 0;
  char line[64];
  size_t n = 0;
  bool fits = true;
  for (uint8_t s = 1; s <= RadioPresetTable::SLOTS; s++) {
    formatPreset(t, s, active, line, sizeof(line));
    size_t l = strlen(line);
    if (n + (s > 1 ? 1 : 0) + l >= cap) {
      fits = false;
      break;
    }
    if (s > 1) out[n++] = '\n';
    memcpy(out + n, line, l);
    n += l;
  }
  if (fits) {
    out[n] = 0;
    return n;
  }
  n = 0;
  out[0] = 0;
  for (uint8_t s = 1; s <= RadioPresetTable::SLOTS; s++) {
    const RadioPreset* p = t.get(s);
    if (p) {
      snprintf(line, sizeof(line), "%s%u%c%s", s > 1 ? " " : "", (unsigned)s, s == active ? '*' : ' ', p->name);
    } else {
      snprintf(line, sizeof(line), "%s%u -", s > 1 ? " " : "", (unsigned)s);
    }
    size_t l = strlen(line);
    if (n + l >= cap) break;
    memcpy(out + n, line, l + 1);
    n += l;
  }
  const char* hint = " (get preset N)";
  size_t hl = strlen(hint);
  if (n + hl < cap) {
    memcpy(out + n, hint, hl + 1);
    n += hl;
  }
  return n;
}

}  // namespace mesh
