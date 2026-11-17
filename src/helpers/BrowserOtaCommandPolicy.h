#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// The hosted documentation shares an origin with other GitHub Pages sites.
// An Origin check alone cannot authorize Companion's entire command surface.
// Apply this policy only to the native browser gateway, after the existing
// transport has reassembled one complete bounded Companion frame.
namespace mesh { namespace browser_ota {

inline bool decimal(const char* p, size_t len, bool dot = false) {
  if (!len) return false;
  bool digit = false, separator = false;
  for (size_t i = 0; i < len; ++i) {
    if (p[i] >= '0' && p[i] <= '9') { digit = true; continue; }
    if (dot && p[i] == '.' && digit && !separator && i + 1 < len) { separator = true; continue; }
    return false;
  }
  return digit;
}

inline bool hexadecimal(const char* p, size_t len) {
  if (!len) return false;
  for (size_t i = 0; i < len; ++i)
    if (!((p[i] >= '0' && p[i] <= '9') || (p[i] >= 'a' && p[i] <= 'f')
          || (p[i] >= 'A' && p[i] <= 'F'))) return false;
  return true;
}

inline bool radioTuple(const char* value, bool secondary) {
  // frequency,bw,sf,cr[,rxtx],minutes,preamble. Firmware performs the
  // numerical and regional checks; prohibit all other syntax here.
  for (unsigned field = 0; field < 6; ++field) {
    const char* end = strchr(value, ',');
    if ((field < 5 && !end) || (field == 5 && end)) return false;
    const size_t len = end ? size_t(end - value) : strlen(value);
    if (!decimal(value, len, field < 2)) return false;
    if (!end) return true;
    value = end + 1;
    if (secondary && field == 3) {
      if (strncmp(value, "rxtx,", 5)) return false;
      value += 5;
    }
  }
  return false;
}

inline bool readOnly(const char* text) {
  static const char* const allowed[] = {
    "get public.key", "ota status", "ota self", "ota ram", "ver", "board",
    "get bootloader.ver", "get usb.raw.max", "ota bootloader status", "ota config",
    "ota key list", "get tempradio", "get radio", "get radio.rxps", "ota installhash",
    "ota qspi", "ota stats", "ota ls", "get radio2", "get tempradio2", "get radio2.cross", "get radio2.only",
    "get radio2.status", "get radioat", "get tempradioat", "get radioat2",
    "get tempradioat2", "get repeat", "get radio.flood", "get radio2.flood",
    "get ota.direct", "get ota.direct.capabilities", "get ota.direct.radio", "ota folder"
  };
  for (auto item : allowed) if (!strcmp(text, item)) return true;
  if (!strncmp(text, "ota ls ", 7)) {
    const char* page = text + 7; const size_t n = strlen(page);
    if (!decimal(page, n) || n > 3 || *page == '0') return false;
    unsigned value = 0; for (size_t i = 0; i < n; ++i) value = value * 10 + page[i] - '0';
    return value <= 128;
  }
  return false;
}

inline bool command(const uint8_t* data, size_t len, bool remote) {
  if (!data || !len || len > 152) return false;
  char text[153];
  for (size_t i = 0; i < len; ++i) {
    if (data[i] < 0x20 || data[i] > 0x7e) return false;
    text[i] = char(data[i]);
  }
  text[len] = 0;
  if (readOnly(text)) return true;
  if (!strncmp(text, "set radio2.only on ", 19))
    return strlen(text + 19) == 16 && hexadecimal(text + 19, 16);
  if (!strncmp(text, "set radio2.only off ", 20))
    return strlen(text + 20) == 16 && hexadecimal(text + 20, 16);
  if (!remote && !strncmp(text, "get ota.direct.reply ", 21)) {
    const char* offset = text + 21; size_t n = strlen(offset);
    if (n > 3 || !decimal(offset, n)) return false;
    unsigned value = 0; for (size_t i = 0; i < n; ++i) value = value * 10 + offset[i] - '0';
    return value <= 160;
  }
  if (!remote && (!strcmp(text, "set ota.direct.radio radio") ||
                  !strcmp(text, "set ota.direct.radio radio2"))) return true;
  if (!strcmp(text, "ota config autofetch off") || !strcmp(text, "ota config autoinstall off") ||
      !strcmp(text, "ota config hops 0") || !strcmp(text, "set radio.rxps off") ||
      !strcmp(text, "set tempradio2 off")) return true;
  if (!strncmp(text, "tempradio ", 10)) return radioTuple(text + 10, false);
  if (!strncmp(text, "set tempradio2 ", 15)) return radioTuple(text + 15, true);
  if (remote && !strncmp(text, "tempradio.ifidle ", 17)) return radioTuple(text + 17, false);
  if (remote && (!strcmp(text, "ota install") || !strcmp(text, "ota cancel") ||
      !strcmp(text, "set repeat on") || !strcmp(text, "set repeat off") ||
      !strcmp(text, "set radio.flood on") || !strcmp(text, "set radio.flood off") ||
      !strcmp(text, "set radio2.flood on") || !strcmp(text, "set radio2.flood off"))) return true;
  if (remote && len == 23 && !memcmp(text, "ota pull ", 9) &&
      hexadecimal(text + 9, 8) && !memcmp(text + 17, " flash", 6)) return true;
  if (remote && len == 80 && !memcmp(text, "ota installhash ", 16) && hexadecimal(text + 16, 64)) return true;
  return false;
}

inline bool allowed(const uint8_t* frame, size_t len, bool guarded, const uint8_t* target) {
  if (!frame || !len) return false;
  switch (frame[0]) {
    case 1: return len >= 8 && len <= 32;  // APP_START
    case 4: return len == 1 || len == 5;   // contacts
    case 5: return len == 1;               // device clock
    case 22: return len == 2;              // device query
    case 42: return len == 34 && frame[1] == 0; // cached inbound advert path
    case 52: // guarded direct session modes
      if (len < 34 || frame[1] < 1 || frame[1] > 4) return false;
      if (frame[1] == 1) return len == 35 && frame[34] >= 2 && frame[34] <= 4;
      if (frame[1] == 3) return len == 34; // firmware checks owner/target before close
      if (!guarded || !target || memcmp(frame + 2, target, 32)) return false;
      if (frame[1] == 2) return len == 34;
      return len == 37 && frame[34] >= '!' && frame[34] <= '~' && frame[34] != '|'
          && frame[35] >= '!' && frame[35] <= '~' && frame[35] != '|' && frame[36] == '|';
    case 26: // authenticated login only for the active guarded peer
      if (!guarded || !target || len < 33 || len > 48 || memcmp(frame + 1, target, 32)) return false;
      for (size_t i = 33; i < len; ++i) if (frame[i] < 0x20 || frame[i] == 0x7f) return false;
      return true;
    case 2: // guarded remote OTA CLI with a two-character correlation tag
      return guarded && target && len > 16 && frame[1] == 1 && frame[2] == 0 &&
          !memcmp(frame + 7, target, 6) && frame[13] >= '!' && frame[13] <= '~' && frame[13] != '|' &&
          frame[14] >= '!' && frame[14] <= '~' && frame[14] != '|' && frame[15] == '|' &&
          command(frame + 16, len - 16, true);
    case 65: // only a direct zero-hop OTA packet, never arbitrary raw traffic
      return guarded && len >= 5 && len <= 188 && frame[1] == 1 && frame[2] == 0x32 &&
          frame[3] == 0 && frame[4] >= 1 && frame[4] <= 11;
    case 66: return command(frame + 1, len - 1, false);
    default: return false;
  }
}

} } // namespace mesh::browser_ota
