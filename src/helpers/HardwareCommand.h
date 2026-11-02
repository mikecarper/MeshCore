#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace hardwareCommand {

constexpr size_t MaxNameLength = 63;

struct Selector {
  const char* name = nullptr;
  const char* command = nullptr;
  size_t length = 0;
  uint32_t id = 0;
  char role = 0;
  bool compact = false;
  bool any = false;
};

enum class Parse { None, Valid, Invalid };

inline char canonical(char c) {
  if (c >= 'A' && c <= 'Z') return char(c + ('a' - 'A'));
  return c == ' ' ? '_' : c;
}

inline int hex(char c) {
  c = canonical(c);
  return c >= '0' && c <= '9' ? c - '0'
      : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

inline bool nameCharacter(char c) {
  c = canonical(c);
  return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
      || c == '_' || c == '-' || c == '.';
}

inline char roleCode(const char* role) {
  if (!role) return 0;
  return !strcmp(role, "repeater") ? 'r' : !strcmp(role, "room_server") ? 's'
      : !strcmp(role, "companion") ? 'c' : !strcmp(role, "sensor") ? 'n' : 0;
}

inline const char* hardware(const char* fallback) {
#ifdef MOTA_HW_ID
  (void)fallback;
  return MOTA_HW_ID;
#else
  return fallback;
#endif
}

// FNV-1a is a compact selector, not an authorization or image compatibility check.
inline uint32_t identifier(const char* name) {
  uint32_t hash = 2166136261UL;
  if (name) while (*name) hash = (hash ^ uint8_t(canonical(*name++))) * 16777619UL;
  return hash;
}

inline Parse parse(const char* text, Selector& out) {
  out = Selector{};
  if (!text || *text != '@') return Parse::None;
  const char* p = text + 1;
  out.name = p;
  while (*p && *p != '/' && *p != ' ' && *p != '\t') {
    if (size_t(p - out.name) >= MaxNameLength) return Parse::Invalid;
    ++p;
  }
  out.length = size_t(p - out.name);
  if (!out.length) return Parse::Invalid;
  out.any = out.length == 1 && *out.name == '*';
  out.compact = *out.name == '#';
  if (out.compact) {
    if (out.length != 9) return Parse::Invalid;
    for (size_t i = 1; i < out.length; ++i) {
      const int digit = hex(out.name[i]);
      if (digit < 0) return Parse::Invalid;
      out.id = (out.id << 4) | uint32_t(digit);
    }
  } else if (!out.any) {
    for (size_t i = 0; i < out.length; ++i)
      if (!nameCharacter(out.name[i])) return Parse::Invalid;
  }
  if (*p == '/') {
    out.role = canonical(*++p);
    if (out.role != 'r' && out.role != 's' && out.role != 'c' && out.role != 'n')
      return Parse::Invalid;
    ++p;
  }
  if (*p != ' ' && *p != '\t') return Parse::Invalid;
  do { ++p; } while (*p == ' ' || *p == '\t');
  if (!*p || *p == '@') return Parse::Invalid;
  out.command = p;
  return Parse::Valid;
}

inline bool matches(const Selector& selector, const char* name, const char* role) {
  if (selector.role && selector.role != roleCode(role)) return false;
  if (selector.any) return true;
  if (!name || !*name) return false;
  if (selector.compact) return selector.id == identifier(name);
  size_t i = 0;
  while (i < selector.length && name[i]
      && canonical(selector.name[i]) == canonical(name[i])) ++i;
  return i == selector.length && name[i] == 0;
}

inline bool word(const char* text, const char* expected) {
  while (*expected && canonical(*text) == *expected) { ++text; ++expected; }
  return !*expected && (!*text || *text == ' ' || *text == '\t');
}

// Return true when the guard consumed the request (skip/error); otherwise the
// caller dispatches the unchanged command or matching guard's inner command.
inline bool apply(const char*& command, const char* name, const char* role,
                  char* reply, size_t capacity) {
  Selector selector;
  const Parse parsed = parse(command, selector);
  if (parsed == Parse::None) return false;
  if (parsed == Parse::Invalid) {
    snprintf(reply, capacity, "Err - use @board[/r|s|c|n] command");
    return true;
  }
  if (!matches(selector, name, role)) {
    snprintf(reply, capacity, "OK - skipped (hardware/role)");
    return true;
  }
  // These commands have wire-text-specific recovery, secret-erasure or
  // interactive-input handling outside the ordinary CLI dispatcher.
  const char* inner = selector.command;
  const char* region_action = inner;
  if (word(inner, "region")) {
    region_action += 6;
    while (*region_action == ' ' || *region_action == '\t') ++region_action;
  }
  if (word(inner, "backup") || word(inner, "password") || word(inner, "replay")
      || (word(inner, "region") && word(region_action, "load"))) {
    snprintf(reply, capacity, "Err - run this command without a hardware guard");
    return true;
  }
  command = selector.command;
  return false;
}

inline void format(const char* name, const char* role, char* reply, size_t capacity) {
  char display[MaxNameLength + 1];
  size_t i = 0;
  if (name) while (name[i] && i < MaxNameLength) {
    display[i] = canonical(name[i]);
    ++i;
  }
  display[i] = 0;
  snprintf(reply, capacity, "> hw=%s role=%s id=%08lX", i ? display : "unknown",
           role ? role : "unknown", (unsigned long)identifier(name));
}

} // namespace hardwareCommand
} // namespace mesh
