#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace mesh {
namespace flood {

// Byte patterns: substring search by default, ^/$ anchors, ., escaped literals,
// classes/ranges/[^...], groups, alternation, and *, +, ? repetition. Matching is
// case-sensitive unless a leading (?i) or the caller requests ASCII folding.
// No heap or backtracking; one budget can cover every rule for a packet.
static constexpr size_t MAX_TEXT_PATTERN_LENGTH = 63;
static constexpr uint32_t TEXT_PATTERN_WORK_BUDGET = 32768;

enum class PatternMatchResult : uint8_t {
  NoMatch,
  Match,
  Invalid,
  BudgetExceeded,
};

namespace pattern_detail {

static constexpr uint8_t NO_EDGE = 255;
static constexpr uint8_t MAX_STATES = 96;
static constexpr uint8_t MAX_GROUP_DEPTH = 8;
enum Op : uint8_t { Literal, Any, Class, Begin, End, Split, Jump, Accept };

struct State {
  uint8_t op, out, other, value;
};

struct Fragment {
  uint8_t start, head, tail;
};

// Thompson fragments use unfinished edges as a linked patch list while
// compiling. After compilation all those edges contain ordinary state indices.
class Compiler {
  const char* pattern;
  size_t length, cursor;
  State* states;
  uint8_t used, depth;
  bool good;

  uint8_t emit(Op op, uint8_t value = 0) {
    if (used == MAX_STATES) { good = false; return 0; }
    const uint8_t index = used++;
    states[index] = {op, NO_EDGE, NO_EDGE, value};
    return index;
  }

  uint8_t& edge(uint8_t index) {
    return index & 1 ? states[index / 2].other : states[index / 2].out;
  }

  Fragment single(Op op, uint8_t value = 0) {
    const uint8_t index = emit(op, value);
    return {index, uint8_t(index * 2), uint8_t(index * 2)};
  }

  void patch(Fragment fragment, uint8_t target) {
    uint8_t link = fragment.head;
    while (link != NO_EDGE) {
      const uint8_t next = edge(link);
      edge(link) = target;
      link = next;
    }
  }

  Fragment append(Fragment left, Fragment right) {
    edge(left.tail) = right.head;
    return {left.start, left.head, right.tail};
  }

  bool classChar(uint8_t& value) {
    if (cursor == length || pattern[cursor] == ']') return false;
    value = uint8_t(pattern[cursor++]);
    if (value == '\\') {
      if (cursor == length) return false;
      value = uint8_t(pattern[cursor++]);
    }
    return true;
  }

  bool characterClass() {
    if (cursor < length && pattern[cursor] == '^') ++cursor;
    bool any = false;
    while (cursor < length && pattern[cursor] != ']') {
      uint8_t first, last;
      if (!classChar(first)) return false;
      any = true;
      if (cursor + 1 < length && pattern[cursor] == '-' && pattern[cursor + 1] != ']') {
        ++cursor;
        if (!classChar(last) || first > last) return false;
      }
    }
    if (!any || cursor == length) return false;
    ++cursor;
    return true;
  }

  Fragment atom() {
    const uint8_t value = uint8_t(pattern[cursor++]);
    switch (value) {
      case '(' : {
        if (++depth > MAX_GROUP_DEPTH) { good = false; return single(Jump); }
        Fragment result = expression();
        --depth;
        if (cursor == length || pattern[cursor++] != ')') good = false;
        return result;
      }
      case '[' : {
        const uint8_t offset = uint8_t(cursor);
        if (!characterClass()) good = false;
        return single(Class, offset);
      }
      case '\\' :
        if (cursor == length) { good = false; return single(Jump); }
        return single(Literal, uint8_t(pattern[cursor++]));
      case '.' : return single(Any);
      case '^' : return single(Begin);
      case '$' : return single(End);
      case '*' : case '+' : case '?' : case ']' :
        good = false;
        return single(Jump);
      default : return single(Literal, value);
    }
  }

  Fragment sequence() {
    Fragment result = {NO_EDGE, NO_EDGE, NO_EDGE};
    while (good && cursor < length && pattern[cursor] != ')' && pattern[cursor] != '|') {
      const uint8_t first = uint8_t(pattern[cursor]);
      Fragment next = atom();
      if (!good) return next;
      if (good && cursor < length && (pattern[cursor] == '*' || pattern[cursor] == '+' || pattern[cursor] == '?')) {
        const char repeat = pattern[cursor++];
        if (first == '^' || first == '$') { good = false; break; }
        const uint8_t split = emit(Split);
        if (!good) return next;
        states[split].out = next.start;
        const uint8_t tail = uint8_t(split * 2 + 1);
        if (repeat == '?') {
          const uint8_t start = split;
          next = append(next, {start, tail, tail});
          next.start = start;
        } else {
          const uint8_t start = repeat == '*' ? split : next.start;
          patch(next, split);
          next = {start, tail, tail};
        }
        // Lazy/stacked quantifiers are deliberately unsupported.
        if (cursor < length && (pattern[cursor] == '*' || pattern[cursor] == '+' || pattern[cursor] == '?')) good = false;
      }
      if (result.start == NO_EDGE) result = next;
      else { patch(result, next.start); result.head = next.head; result.tail = next.tail; }
    }
    return result.start == NO_EDGE ? single(Jump) : result;
  }

  Fragment expression() {
    Fragment result = sequence();
    while (good && cursor < length && pattern[cursor] == '|') {
      ++cursor;
      Fragment right = sequence();
      if (!good) return right;
      const uint8_t split = emit(Split);
      if (!good) return result;
      states[split].out = result.start;
      states[split].other = right.start;
      result = append(result, right);
      result.start = split;
    }
    return result;
  }

 public:
  Compiler(const char* text, size_t size, State* output)
      : pattern(text), length(size), cursor(0), states(output), used(0), depth(0), good(true) {}

  bool compile(uint8_t& start) {
    if (!pattern || length > MAX_TEXT_PATTERN_LENGTH) return false;
    // NUL is data in the input text, but cannot appear inside a CLI pattern.
    if (memchr(pattern, 0, length)) return false;
    Fragment fragment = expression();
    if (!good || cursor != length) return false;
    const uint8_t finish = emit(Accept);
    if (!good) return false;
    patch(fragment, finish);
    start = fragment.start;
    return true;
  }
};

inline bool spend(uint32_t& budget) {
  if (!budget) return false;
  --budget;
  return true;
}

struct StateList {
  uint8_t indices[MAX_STATES];
  uint8_t seen[(MAX_STATES + 7) / 8];
  uint8_t count;

  void clear() { memset(seen, 0, sizeof(seen)); count = 0; }
  void push(uint8_t index) {
    const uint8_t bit = uint8_t(1U << (index & 7));
    if (seen[index / 8] & bit) return;
    seen[index / 8] |= bit;
    indices[count++] = index;
  }

  bool add(const State* states, uint8_t start, size_t position, size_t length, uint32_t& budget) {
    const uint8_t first = count;
    push(start);
    for (uint8_t cursor = first; cursor < count; ++cursor) {
      if (!spend(budget)) return false;
      const State& state = states[indices[cursor]];
      switch (state.op) {
        case Split: push(state.other); push(state.out); break;
        case Jump: push(state.out); break;
        case Begin: if (position == 0) push(state.out); break;
        case End: if (position == length) push(state.out); break;
        default: break;
      }
    }
    return true;
  }
};

inline uint8_t fold(uint8_t byte) {
  return byte >= 'A' && byte <= 'Z' ? uint8_t(byte + ('a' - 'A')) : byte;
}

// The modifier stays in the saved pattern, so no rule-layout change is needed.
// Count it toward the existing limit before removing it for the compiler.
inline bool preparePattern(const char*& pattern, size_t& length, bool& ignoreCase) {
  if (!pattern || length > MAX_TEXT_PATTERN_LENGTH) return false;
  if (length >= 4 && memcmp(pattern, "(?i)", 4) == 0) {
    pattern += 4;
    length -= 4;
    ignoreCase = true;
  }
  return true;
}

inline uint8_t classChar(const char* pattern, uint8_t& cursor) {
  uint8_t byte = uint8_t(pattern[cursor++]);
  if (byte == '\\') byte = uint8_t(pattern[cursor++]);
  return byte;
}

inline int classMatches(const char* pattern, uint8_t cursor, uint8_t byte, bool ignoreCase, uint32_t& budget) {
  const bool negate = pattern[cursor] == '^';
  if (negate) ++cursor;
  bool matched = false;
  while (pattern[cursor] != ']') {
    if (!spend(budget)) return -1;
    const uint8_t first = classChar(pattern, cursor);
    uint8_t last = first;
    if (pattern[cursor] == '-' && pattern[cursor + 1] != ']') {
      ++cursor;
      last = classChar(pattern, cursor);
    }
    // Test the original interval and the opposite ASCII case. This keeps
    // mixed ranges such as [A-z] faithful when case folding is enabled.
    matched |= byte >= first && byte <= last;
    if (ignoreCase) {
      const uint8_t folded = fold(byte);
      matched |= folded >= first && folded <= last;
      if (folded >= 'a' && folded <= 'z') {
        const uint8_t upper = uint8_t(folded - ('a' - 'A'));
        matched |= upper >= first && upper <= last;
      }
    }
  }
  return matched != negate;
}

} // namespace pattern_detail

inline bool textPatternValid(const char* pattern, size_t patternLength) {
  bool ignoreCase = false;
  if (!pattern_detail::preparePattern(pattern, patternLength, ignoreCase)) return false;
  pattern_detail::State states[pattern_detail::MAX_STATES];
  uint8_t start;
  return pattern_detail::Compiler(pattern, patternLength, states).compile(start);
}

inline PatternMatchResult matchTextPattern(const char* pattern, size_t patternLength,
                                          const uint8_t* text, size_t textLength,
                                          bool ignoreCase = false, uint32_t* remainingWork = nullptr) {
  using namespace pattern_detail;
  if (!text && textLength) return PatternMatchResult::Invalid;
  if (!preparePattern(pattern, patternLength, ignoreCase)) return PatternMatchResult::Invalid;
  State states[MAX_STATES];
  uint8_t start;
  if (!Compiler(pattern, patternLength, states).compile(start)) return PatternMatchResult::Invalid;
  uint32_t localBudget = TEXT_PATTERN_WORK_BUDGET;
  uint32_t& budget = remainingWork ? *remainingWork : localBudget;
  StateList lists[2];
  StateList* current = &lists[0];
  StateList* next = &lists[1];
  current->clear();
  for (size_t position = 0;; ++position) {
    // Injecting the start state at each byte provides substring search without
    // restarting the automaton or rescanning any input prefix.
    if (!current->add(states, start, position, textLength, budget)) return PatternMatchResult::BudgetExceeded;
    next->clear();
    for (uint8_t cursor = 0; cursor < current->count; ++cursor) {
      if (!spend(budget)) return PatternMatchResult::BudgetExceeded;
      const State& state = states[current->indices[cursor]];
      if (state.op == Accept) return PatternMatchResult::Match;
      if (position == textLength) continue;
      bool matches = state.op == Any;
      if (state.op == Literal) matches = ignoreCase ? fold(text[position]) == fold(state.value) : text[position] == state.value;
      else if (state.op == Class) {
        const int result = classMatches(pattern, state.value, text[position], ignoreCase, budget);
        if (result < 0) return PatternMatchResult::BudgetExceeded;
        matches = result != 0;
      }
      if (matches && !next->add(states, state.out, position + 1, textLength, budget)) return PatternMatchResult::BudgetExceeded;
    }
    if (position == textLength) return PatternMatchResult::NoMatch;
    StateList* old = current; current = next; next = old;
  }
}

} // namespace flood
} // namespace mesh
