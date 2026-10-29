"""Exercise the production bounded matcher, including hostile regex-like input."""
from pathlib import Path
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = r'''
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include "FloodTextPattern.h"
using namespace mesh::flood;
static bool forbidAllocation = false;
__attribute__((noinline)) void* operator new(size_t size) {
  assert(!forbidAllocation);
  void* result = malloc(size ? size : 1);
  if (!result) std::abort();
  return result;
}
__attribute__((noinline)) void* operator new[](size_t size) { return ::operator new(size); }
__attribute__((noinline)) void operator delete(void* pointer) noexcept { free(pointer); }
__attribute__((noinline)) void operator delete[](void* pointer) noexcept { free(pointer); }
static_assert(sizeof(pattern_detail::State) == 4, "small NFA instructions");
static_assert(sizeof(pattern_detail::State) * pattern_detail::MAX_STATES
    + 2 * sizeof(pattern_detail::StateList) <= 640, "bounded matching scratch");
static PatternMatchResult match(const char* pattern, const char* text, bool fold = false) {
  forbidAllocation = true;
  const auto result = matchTextPattern(pattern, strlen(pattern), reinterpret_cast<const uint8_t*>(text), strlen(text), fold);
  forbidAllocation = false;
  return result;
}
static void checks() {
  using R = PatternMatchResult;
  assert(match("", "") == R::Match);
  assert(match("Alice", "a message from Alice today") == R::Match);
  assert(match("^Alice$", "Alice") == R::Match);
  assert(match("^Alice$", "alice") == R::NoMatch);
  assert(match("^Alice$", "alice", true) == R::Match);
  assert(match("(?i)^Alice$", "aLiCe") == R::Match);
  assert(match("(?i)^Alice$", "aLiCe", true) == R::Match);
  assert(match("(?i)", "") == R::Match);
  assert(match("(?i)^(Jeffery Epstein|Ghislaine Maxwell)$", "jEfFeRy ePsTeIn") == R::Match);
  assert(match("(?i)^(Jeffery Epstein|Ghislaine Maxwell)$", "GHISLAINE MAXWELL") == R::Match);
  assert(match("(?i)^(Jeffery Epstein|Ghislaine Maxwell)$", "Jeffery Epstein Jr") == R::NoMatch);
  assert(match("(?i)^(Jeffery Epstein|Ghislaine Maxwell)$", "XGhislaine Maxwell") == R::NoMatch);
  assert(match("(?i)^(Jeffery Epstein|Ghislaine Maxwell)$", "someone else") == R::NoMatch);
  assert(match("^(Jeffery Epstein|Ghislaine Maxwell)$", "GHISLAINE MAXWELL") == R::NoMatch);
  assert(match("^Alice$", "Alice2") == R::NoMatch);
  assert(match("^Alice$", "2Alice") == R::NoMatch);
  assert(match("Alice$", "Alice\n") == R::NoMatch);
  assert(match("A.*e", "A\ne") == R::Match);
  assert(match("^a\\.b\\*\\[\\]\\(\\)\\|\\+\\?\\^\\$\\\\$", "a.b*[]()|+?^$\\") == R::Match);
  assert(match("^[A-F0-9]+$", "aBcF09", true) == R::Match);
  assert(match("(?i)^[A-F0-9]+$", "aBcF09") == R::Match);
  assert(match("(?i)^[A-F0-9]+$", "g") == R::NoMatch);
  assert(match("^[A-F0-9]+$", "aBcF09") == R::NoMatch);
  assert(match("^[^A-F]+$", "xyz", true) == R::Match);
  assert(match("^[^A-F]+$", "xyzA", true) == R::NoMatch);
  assert(match("(?i)^[^A-F]+$", "xyz") == R::Match);
  assert(match("(?i)^[^A-F]+$", "xyza") == R::NoMatch);
  assert(match("^[A-z]+$", "[_Zaz", true) == R::Match);
  assert(match("(?i)^[A-z]+$", "[_Zaz") == R::Match);
  assert(match("(?i)^\xC0$", "\xE0") == R::NoMatch); // ASCII folding only
  assert(match("(?i)^\xC0$", "\xC0") == R::Match);
  assert(match("^[\\]\\-\\[]+$", "]-[") == R::Match);
  assert(match("^[a-]+$", "a--") == R::Match);
  assert(match("^[-a]+$", "a--") == R::Match);
  assert(match("^(Alice|Bob)[0-9]?$", "Bob3") == R::Match);
  assert(match("^(Alice|Bob)[0-9]?$", "Bob33") == R::NoMatch);
  assert(match("^(ab)*c+$", "ababc") == R::Match);
  assert(match("^(ab)+c?$", "") == R::NoMatch);
  assert(match("^(ab)*c?$", "") == R::Match);
  assert(match("^(a|ab)*b$", "aabab") == R::Match);
  assert(match("^(()*)*$", "") == R::Match); // epsilon cycles terminate
  assert(match("^(a|)*$", "aaaa") == R::Match);
  for (const char* invalid : {"[", "[]", "[^]", "[z-a]", "[a-\\]]", "\\", "(", ")", "a)",
                             "a**", "a+?", "a??", "*a", "^*", "$+", "a]",
                             "a(?i)b", "^(?i)Alice$", "(?i)(?i)Alice", "(?I)Alice", "(?i:Alice)"}) {
    assert(!textPatternValid(invalid, strlen(invalid)));
    assert(match(invalid, "anything") == R::Invalid);
  }
  assert(!textPatternValid(nullptr, 0));
  assert(matchTextPattern("a", 1, nullptr, 1) == R::Invalid);
  assert(matchTextPattern("", 0, nullptr, 0) == R::Match);
  const char nulPattern[] = {'a', 0, 'b'};
  assert(!textPatternValid(nulPattern, sizeof(nulPattern)));
  const uint8_t nulText[] = {'a', 0, 'b'};
  assert(matchTextPattern("^a.b$", 5, nulText, 3) == R::Match);
  assert(matchTextPattern("^a$", 3, nulText, 3) == R::NoMatch);
  const std::string maximum(MAX_TEXT_PATTERN_LENGTH, 'a');
  assert(textPatternValid(maximum.data(), maximum.size()));
  assert(!textPatternValid(maximum.data(), maximum.size() + 1));
  const std::string foldedMaximum = "(?i)" + std::string(MAX_TEXT_PATTERN_LENGTH - 4, 'a');
  assert(textPatternValid(foldedMaximum.data(), foldedMaximum.size()));
  assert(match(foldedMaximum.c_str(), std::string(MAX_TEXT_PATTERN_LENGTH - 4, 'A').c_str()) == R::Match);
  const std::string foldedOversized = foldedMaximum + "a";
  assert(!textPatternValid(foldedOversized.data(), foldedOversized.size()));
  assert(!textPatternValid("(((((((((a)))))))))", 19));
  assert(textPatternValid("((((((((a))))))))", 17));
  const std::string tooManyStates(MAX_TEXT_PATTERN_LENGTH, '|');
  assert(!textPatternValid(tooManyStates.data(), tooManyStates.size()));
  const std::string longSender = std::string(200, 'x') + "Alice";
  assert(match("Alice$", longSender.c_str()) == R::Match);
  assert(match("^x+$", longSender.c_str()) == R::NoMatch);
  uint32_t budget = 0;
  assert(matchTextPattern("a", 1, reinterpret_cast<const uint8_t*>("a"), 1, false, &budget) == R::BudgetExceeded);
  assert(budget == 0);
  budget = 100;
  assert(matchTextPattern("a", 1, reinterpret_cast<const uint8_t*>("a"), 1, false, &budget) == R::Match);
  assert(budget < 100 && budget > 0);
  const uint32_t afterFirst = budget;
  assert(matchTextPattern("b", 1, reinterpret_cast<const uint8_t*>("b"), 1, false, &budget) == R::Match);
  assert(budget < afterFirst);
  const std::string hostile(100000, 'a');
  budget = 500;
  assert(matchTextPattern("(a|aa)*b", 8, reinterpret_cast<const uint8_t*>(hostile.data()), hostile.size(), false, &budget) == R::BudgetExceeded);
  assert(budget == 0);
  // This pattern is exponential with a backtracking engine. The NFA finishes
  // within the default fixed budget for the largest packet-sized payload.
  const std::string packet(255, 'a');
  assert(match("^(a|aa)*b$", packet.c_str()) == R::NoMatch);
}
static std::vector<uint8_t> unhex(const std::string& text) {
  std::vector<uint8_t> result;
  if (text == "-") return result;
  for (size_t index = 0; index < text.size(); index += 2) {
    result.push_back(uint8_t(std::stoul(text.substr(index, 2), nullptr, 16)));
  }
  return result;
}
int main() {
  checks();
  std::string patternHex, textHex;
  bool ignoreCase;
  while (std::cin >> patternHex >> textHex >> ignoreCase) {
    const auto pattern = unhex(patternHex);
    const auto text = unhex(textHex);
    // Exact-sized allocations let ASan detect an accidental C-string read.
    char* rawPattern = new char[pattern.size() ? pattern.size() : 1];
    uint8_t* rawText = new uint8_t[text.size() ? text.size() : 1];
    if (!pattern.empty()) memcpy(rawPattern, pattern.data(), pattern.size());
    if (!text.empty()) memcpy(rawText, text.data(), text.size());
    const auto result = matchTextPattern(rawPattern, pattern.size(), rawText, text.size(), ignoreCase);
    std::cout << int(result) << '\n';
    delete[] rawPattern;
    delete[] rawText;
  }
}
'''

ARM_PROGRAM = r'''
#include "FloodTextPattern.h"
extern "C" __attribute__((noinline)) mesh::flood::PatternMatchResult packet_match(
    const char* pattern, size_t patternLength, const uint8_t* text, size_t textLength, uint32_t* budget) {
  return mesh::flood::matchTextPattern(pattern, patternLength, text, textLength, false, budget);
}
'''


class FloodTextPatternTests(unittest.TestCase):
    def test_packet_patterns_bounds_and_reference_agreement(self):
        rng = random.Random(436)
        patterns = ["", "a", "^a$", "a|b", "^(ab|a)*b$", "a?b+c*", "[ab]+", "[^a]*b$", "[A-z]+",
                    "(?i)", "(?i)^a$", "(?i)^(ab|a)*b$", "(?i)^[A-C]+$", "(?i)^[^A-C]+$", "(?i)[A-z]+"]
        atoms = ["a", "b", "c", ".", "[ab]", "[^c]", "[a-c]", "[0-2]", r"\.", "(ab|c)", "(a|)"]
        for _ in range(300):
            pattern = "^" if rng.randrange(2) else ""
            for _ in range(rng.randrange(1, 5)):
                pattern += rng.choice(atoms) + rng.choice(["", "", "?", "*", "+"])
            if rng.randrange(2):
                pattern += "$"
            if rng.randrange(2):
                pattern = "(?i)" + pattern
            patterns.append(pattern)
        cases = []
        for pattern in patterns:
            for _ in range(15):
                text = "".join(rng.choice("abcABC012.") for _ in range(rng.randrange(21)))
                fold = bool(rng.randrange(2))
                expected = bool(re.search(pattern, text, re.DOTALL | re.ASCII | (re.IGNORECASE if fold else 0)))
                cases.append((pattern, text, fold, expected))
        encoded = "".join(f"{pattern.encode().hex() or '-'} {text.encode().hex() or '-'} {int(fold)}\n"
                          for pattern, text, fold, _ in cases)
        # Deterministic malformed-input fuzzing exercises validation and patch
        # list bounds without assuming Python supports exactly our byte syntax.
        fuzz = ["".join(rng.choice("abc^$.*+?()[\\]|-") for _ in range(rng.randrange(1, 64)))
                for _ in range(4000)]
        fuzz += ["(?i)" + pattern for pattern in fuzz[:250]]
        fuzz += ["(", "(?", "(?i", "(?i)"]
        encoded += "".join(f"{pattern.encode().hex()} 616263 0\n" for pattern in fuzz)
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "patterns.cpp"
            source.write_text(PROGRAM)
            binary = folder / "patterns"
            flags = []
            if os.name != "nt":
                flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            subprocess.run(["g++", "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror", *flags,
                            "-I" + str(ROOT / "src/helpers"), str(source), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], input=encoded, text=True, capture_output=True, check=True, timeout=30)
            results = result.stdout.splitlines()
            self.assertEqual(len(results), len(cases) + len(fuzz))
            for value, case in zip(results, cases):
                self.assertEqual(int(value), int(case[3]), case)
            self.assertTrue(all(value in ("0", "1", "2") for value in results[len(cases):]))

    def test_arm_stack_is_bounded(self):
        compiler = shutil.which("arm-none-eabi-g++")
        if not compiler:
            packages = Path.home() / ".platformio/packages"
            candidates = sorted(packages.glob("toolchain-gccarmnoneeabi*/bin/arm-none-eabi-g++*"))
            candidates = [path for path in candidates if path.suffix in ("", ".exe")]
            if candidates:
                compiler = str(candidates[0])
        if not compiler:
            self.skipTest("ARM compiler unavailable")
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "patterns.cpp"
            source.write_text(ARM_PROGRAM)
            subprocess.run([compiler, "-std=c++11", "-mcpu=cortex-m4", "-mthumb", "-Os", "-fstack-usage",
                            "-I" + str(ROOT / "src/helpers"), "-c", str(source), "-o", str(folder / "patterns.o")], check=True)
            frames = {}
            for line in (folder / "patterns.su").read_text().splitlines():
                name, size, kind = line.rsplit("\t", 2)
                self.assertEqual(kind, "static", line)
                frames[name] = int(size)
            self.assertTrue(frames)
            # Compiler recursion is limited to eight groups. Conservatively
            # count each parser frame at every level, plus runtime scratch.
            recursive = sum(size for name, size in frames.items()
                            if any(token in name for token in ("::atom(", "::sequence(", "::expression(")))
            other = max(size for name, size in frames.items()
                        if not any(token in name for token in ("::atom(", "::sequence(", "::expression(")))
            self.assertLessEqual(recursive * 9 + other + 64, 2048, frames)


if __name__ == "__main__":
    unittest.main()
