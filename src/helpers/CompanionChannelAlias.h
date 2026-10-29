#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <helpers/RadioProfilePresets.h>

// Local labels, not part of a hashtag PSK. Keep the original app label/key.
enum class CompanionChannelKind : uint8_t { MeshCore, Radio2, Meshtext, Invalid };
struct CompanionChannelAlias {
  CompanionChannelKind kind;
  bool hashtag;
  const char* tail;
};

inline CompanionChannelAlias parseCompanionChannelAlias(const char* name) {
  if (name == nullptr) return {CompanionChannelKind::Invalid, false, ""};
  const bool hashtag = name[0] == '#';
  const char* tail = name + (hashtag ? 1 : 0);
  CompanionChannelKind kind = CompanionChannelKind::MeshCore;
  if (strncmp(tail, "r2-", 3) == 0) {
    kind = CompanionChannelKind::Radio2;
    tail += 3;
  } else if (strncmp(tail, "mt-", 3) == 0) {
    kind = CompanionChannelKind::Meshtext;
    tail += 3;
  }
  if (kind != CompanionChannelKind::MeshCore
      && (tail[0] == 0 || tail[0] == '#' || strncmp(tail, "r2-", 3) == 0
          || strncmp(tail, "mt-", 3) == 0)) kind = CompanionChannelKind::Invalid;
  return {kind, hashtag, tail};
}

// Only exact named radio2 aliases request RF setup. Ordinary channels and
// arbitrary r2 labels keep the operator's existing profile.
inline const char* companionChannelRadio2Preset(const char* name) {
  const auto alias = parseCompanionChannelAlias(name);
  return alias.kind == CompanionChannelKind::Radio2
      && mesh::cli::radio2PresetTuple(alias.tail) ? alias.tail : nullptr;
}

inline bool companionChannelCanonicalName(const char* name, char* out, size_t size) {
  if (out == nullptr) return false;
  const auto alias = parseCompanionChannelAlias(name);
  if (alias.kind == CompanionChannelKind::Invalid) return false;
  const size_t offset = alias.hashtag ? 1 : 0;
  const size_t length = strlen(alias.tail);
  if (size <= offset + length) return false;
  if (offset) out[0] = '#';
  memcpy(out + offset, alias.tail, length + 1);
  return true;
}

template <typename Hash>
bool resolveCompanionChannelKey(const char* name, uint8_t secret[32], Hash hash) {
  const auto alias = parseCompanionChannelAlias(name);
  if (alias.kind == CompanionChannelKind::Invalid) return false;
  if (alias.kind == CompanionChannelKind::MeshCore || !alias.hashtag) return true;
  for (size_t i = 16; i < 32; ++i) {
    if (secret[i] != 0) return true; // Never rewrite an explicit 256-bit PSK.
  }
  uint8_t app_key[16];
  hash(app_key, sizeof(app_key), reinterpret_cast<const uint8_t*>(name), strlen(name));
  if (memcmp(secret, app_key, sizeof(app_key)) != 0) return true; // Custom PSK.
  char canonical[32];
  if (!companionChannelCanonicalName(name, canonical, sizeof(canonical))) return false;
  hash(secret, 16, reinterpret_cast<const uint8_t*>(canonical), strlen(canonical));
  return true;
}

inline int companionChannelReceivePriority(const char* name, uint8_t origin,
                                          bool radio2_defined = true) {
  const auto alias = parseCompanionChannelAlias(name);
  if (alias.kind == CompanionChannelKind::Invalid) return -1;
  if (radio2_defined) {
    if (alias.kind == CompanionChannelKind::Meshtext) return -1;
    if (alias.kind == CompanionChannelKind::Radio2) return origin == 1 ? 0 : -1;
  }
  return origin == 1 ? 1 : 0;
}
