#pragma once

#include "BridgeIngressProfile.h"
#include <stddef.h>
#include <stdio.h>
#include <string.h>

namespace mesh { namespace bridge {

constexpr size_t MaxIngressRoutes = 8;
constexpr size_t RoutesImageSize = 4 + MaxIngressRoutes * 8 + 4;

struct IngressRule {
  IngressSource source;
  IngressProfile profile = IngressProfile::Radio;
};

inline bool sameSource(const IngressSource& a, const IngressSource& b) {
  return a.kind == b.kind && memcmp(a.bytes, b.bytes, sizeof(a.bytes)) == 0;
}

inline bool validSource(const IngressSource& source) {
  const auto* b = source.bytes;
  if (source.kind == IngressSource::Kind::Mac) {
    uint8_t any = 0;
    for (uint8_t value : source.bytes) any |= value;
    return any && !(b[0] & 1);  // a sender must have an individual MAC address
  }
  return source.kind == IngressSource::Kind::Udp && b[0] && b[0] != 127
      && b[0] < 224 && (b[4] || b[5]);
}

inline bool parseIngressProfile(const char* text, IngressProfile& profile) {
  if (!text) return false;
  if (!strcmp(text, "radio")) profile = IngressProfile::Radio;
  else if (!strcmp(text, "radio2")) profile = IngressProfile::Radio2;
  else if (!strcmp(text, "off")) profile = IngressProfile::Off;
  else return false;
  return true;
}

inline const char* ingressProfileName(IngressProfile profile) {
  return profile == IngressProfile::Radio ? "radio"
      : profile == IngressProfile::Radio2 ? "radio2"
      : profile == IngressProfile::Off ? "off" : "blocked";
}

inline int ingressHex(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

inline bool parseIngressSource(const char* text, IngressSource& source) {
  if (!text) return false;
  IngressSource candidate;
  if (!strncmp(text, "mac:", 4)) {
    if (strlen(text + 4) != 17) return false;
    candidate.kind = IngressSource::Kind::Mac;
    for (unsigned i = 0; i < 6; ++i) {
      const int hi = ingressHex(text[4 + i * 3]), lo = ingressHex(text[5 + i * 3]);
      if (hi < 0 || lo < 0 || (i < 5 && text[6 + i * 3] != ':')) return false;
      candidate.bytes[i] = (hi << 4) | lo;
    }
  } else if (!strncmp(text, "udp:", 4)) {
    candidate.kind = IngressSource::Kind::Udp;
    const char* cursor = text + 4;
    for (unsigned i = 0; i < 4; ++i) {
      unsigned value = 0, digits = 0;
      while (*cursor >= '0' && *cursor <= '9') {
        value = value * 10 + static_cast<uint8_t>(*cursor++ - '0');
        if (++digits > 3 || value > 255) return false;
      }
      if (!digits || *cursor++ != (i == 3 ? ':' : '.')) return false;
      candidate.bytes[i] = value;
    }
    unsigned port = 0, digits = 0;
    while (*cursor >= '0' && *cursor <= '9') {
      port = port * 10 + static_cast<uint8_t>(*cursor++ - '0');
      if (++digits > 5 || port > 65535) return false;
    }
    if (!digits || *cursor || !port) return false;
    candidate.bytes[4] = port >> 8;
    candidate.bytes[5] = port;
  } else return false;
  if (!validSource(candidate)) return false;
  source = candidate;
  return true;
}

inline bool validIngressRules(const IngressRule* rules) {
  for (unsigned i = 0; i < MaxIngressRoutes; ++i) {
    if (rules[i].source.kind == IngressSource::Kind::None) {
      if (rules[i].profile != IngressProfile::Radio) return false;
      for (uint8_t value : rules[i].source.bytes) if (value) return false;
      continue;
    }
    if (!validSource(rules[i].source) ||
        (rules[i].profile != IngressProfile::Radio && rules[i].profile != IngressProfile::Radio2
         && rules[i].profile != IngressProfile::Off)) return false;
    for (unsigned j = 0; j < i; ++j)
      if (sameSource(rules[i].source, rules[j].source)) return false;
  }
  return true;
}

inline uint32_t ingressRoutesChecksum(const uint8_t* data, size_t size) {
  uint32_t crc = 0xffffffffU;
  while (size--) {
    crc ^= *data++;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
  }
  return ~crc;
}

inline void encodeIngressRules(uint8_t image[RoutesImageSize], const IngressRule* rules) {
  memcpy(image, "BRT\1", 4);
  for (unsigned i = 0; i < MaxIngressRoutes; ++i) {
    image[4 + i * 8] = static_cast<uint8_t>(rules[i].source.kind);
    memcpy(image + 5 + i * 8, rules[i].source.bytes, 6);
    image[11 + i * 8] = static_cast<uint8_t>(rules[i].profile);
  }
  const uint32_t crc = ingressRoutesChecksum(image, RoutesImageSize - 4);
  for (unsigned i = 0; i < 4; ++i) image[RoutesImageSize - 4 + i] = crc >> (i * 8);
}

inline bool decodeIngressRules(const uint8_t image[RoutesImageSize], IngressRule* rules) {
  if (memcmp(image, "BRT\1", 4)) return false;
  uint32_t crc = 0;
  for (unsigned i = 0; i < 4; ++i) crc |= uint32_t(image[RoutesImageSize - 4 + i]) << (i * 8);
  if (crc != ingressRoutesChecksum(image, RoutesImageSize - 4)) return false;
  IngressRule candidate[MaxIngressRoutes];
  for (unsigned i = 0; i < MaxIngressRoutes; ++i) {
    candidate[i].source.kind = static_cast<IngressSource::Kind>(image[4 + i * 8]);
    memcpy(candidate[i].source.bytes, image + 5 + i * 8, 6);
    candidate[i].profile = static_cast<IngressProfile>(image[11 + i * 8]);
  }
  if (!validIngressRules(candidate)) return false;
  memcpy(rules, candidate, sizeof(candidate));
  return true;
}

inline void formatIngressRule(unsigned number, const IngressRule& rule, char* reply, size_t capacity) {
  const auto* b = rule.source.bytes;
  if (rule.source.kind == IngressSource::Kind::None) snprintf(reply, capacity, "> %u off", number);
  else if (rule.source.kind == IngressSource::Kind::Mac)
    snprintf(reply, capacity, "> %u mac:%02X:%02X:%02X:%02X:%02X:%02X %s", number,
             b[0], b[1], b[2], b[3], b[4], b[5], ingressProfileName(rule.profile));
  else snprintf(reply, capacity, "> %u udp:%u.%u.%u.%u:%u %s", number,
             b[0], b[1], b[2], b[3], unsigned((b[4] << 8) | b[5]), ingressProfileName(rule.profile));
}

}} // namespace mesh::bridge
