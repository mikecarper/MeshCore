#!/usr/bin/env python3
"""Execute production browser mailbox ownership and retry handshakes under sanitizers."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <helpers/RoomWebMailbox.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <algorithm>
using Mailbox = mesh::RoomWebMailbox;
using Result = Mailbox::Result;
using State = Mailbox::State;
static unsigned cases = 0;
struct Client {
  uint8_t token[32] = {}, digest[32] = {};
  explicit Client(uint8_t value) { token[0] = value; digest[0] = uint8_t(value + 1); }
};
static Result submit(Mailbox& m, Client& c, uint32_t sequence, uint32_t now,
                     const char* body = "write-once") {
  return m.submit(c.token, sequence, c.digest, body, strlen(body), now);
}
static void execute(Mailbox& m, uint32_t now, const char* output = "success") {
  assert(m.begin()); assert(!m.begin()); strcpy(m.output(), output); m.finish(now);
  assert(m.state() == State::Done);
}
static const char* read(Mailbox& m, Client& c, uint32_t sequence) {
  bool pending = true; const char* result = m.read(c.token, sequence, pending);
  assert(!pending);
  if (result) m.delivered(c.token, sequence); // A successfully constructed HTTP response owns a copy.
  return result;
}
int main() {
  {
    const std::string lower = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    std::string upper = lower; std::transform(upper.begin(), upper.end(), upper.begin(),
        [](unsigned char c) { return c >= 'a' && c <= 'f' ? char(c - 'a' + 'A') : char(c); });
    uint8_t decoded[32], equivalent[32];
    assert(Mailbox::decodeToken(lower.c_str(), decoded));
    assert(Mailbox::decodeToken(upper.c_str(), equivalent)); assert(memcmp(decoded, equivalent, 32) == 0);
    for (unsigned position = 0; position < 64; ++position) {
      std::string invalid = lower; invalid[position] = position % 2 ? ':' : 'g';
      memset(decoded, 0xa5, sizeof(decoded)); assert(!Mailbox::decodeToken(invalid.c_str(), decoded));
      for (uint8_t byte : decoded) { assert(byte == 0xa5); } ++cases;
    }
    for (const char* invalid : {static_cast<const char*>(nullptr), "", "a", "0000000000000000000000000000000000000000000000000000000000000000"}) {
      memset(decoded, 0xa5, sizeof(decoded)); assert(!Mailbox::decodeToken(invalid, decoded));
      for (uint8_t byte : decoded) { assert(byte == 0xa5); } ++cases;
    }
    assert(!Mailbox::decodeToken((lower + "0").c_str(), decoded)); ++cases;
  }
  for (const uint32_t start : {0U, UINT32_MAX - 15U}) {
    Mailbox m; Client owner(1), stranger(2); bool pending = true;
    assert(m.state() == State::Idle && !m.begin()); m.finish(start);
    assert(m.read(owner.token, 1, pending) == nullptr && !pending);
    assert(submit(m, owner, 1, start) == Result::Accepted && m.state() == State::Pending);
    const std::string input(m.input()); strcpy(m.output(), "running-output");
    m.finish(start); assert(m.state() == State::Pending && std::string(m.input()) == input);
    assert(m.read(owner.token, 1, pending) == nullptr && pending);
    assert(m.read(stranger.token, 1, pending) == nullptr && !pending);
    assert(m.read(owner.token, 2, pending) == nullptr && !pending);
    for (const uint32_t elapsed : {0U, 1U, Mailbox::OWNER_TTL_MS, UINT32_C(0x80000000)}) {
      assert(submit(m, stranger, 1, start + elapsed) == Result::Busy);
      assert(submit(m, owner, 2, start + elapsed) == Result::Busy);
      assert(submit(m, owner, 1, start + elapsed) == Result::Accepted);
      Client changed = owner; changed.digest[31] ^= 1;
      assert(submit(m, changed, 1, start + elapsed, "different-body") == Result::Mismatch);
      assert(std::string(m.input()) == input && std::string(m.output()) == "running-output"); ++cases;
    }
    assert(m.begin()); char* original_input = m.input(); char* original_output = m.output();
    for (const uint32_t elapsed : {1U, Mailbox::OWNER_TTL_MS + 1U, UINT32_C(0x90000000)}) {
      assert(submit(m, stranger, 99, start + elapsed, "replace") == Result::Busy);
      assert(submit(m, owner, 2, start + elapsed, "replace") == Result::Busy);
      assert(submit(m, owner, 1, start + elapsed) == Result::Accepted && !m.begin());
      assert(m.input() == original_input && m.output() == original_output);
      assert(std::string(m.input()) == input && memcmp(m.token(), owner.token, 32) == 0); ++cases;
    }
    memset(m.output(), 'R', Mailbox::OUTPUT_CAPACITY); m.finish(start + 10);
    for (size_t i = 0; i < Mailbox::INPUT_CAPACITY; ++i) assert(m.input()[i] == 0);
    assert(m.output()[Mailbox::OUTPUT_CAPACITY - 1] == 0 && strlen(m.output()) == Mailbox::OUTPUT_CAPACITY - 1);
    assert(submit(m, owner, 1, start + 11) == Result::Accepted && !m.begin());
    Client changed = owner; changed.digest[0] ^= 1;
    assert(submit(m, changed, 1, start + 12, "new body") == Result::Mismatch);
    assert(submit(m, stranger, 1, start + 10 + Mailbox::RESULT_TTL_MS - 1) == Result::Busy);
    assert(m.read(stranger.token, 1, pending) == nullptr && !pending);
    assert(submit(m, stranger, 1, start + 10 + Mailbox::RESULT_TTL_MS - 1) == Result::Busy);
    const char* result = read(m, owner, 1); assert(result == m.output());
    assert(read(m, owner, 1) == result && !m.begin());
    assert(submit(m, stranger, 1, start + 13) == Result::Accepted);
    execute(m, start + 14); assert(std::string(read(m, stranger, 1)) == "success");
    assert(submit(m, owner, 1, start + 15) == Result::Completed);
    assert(submit(m, owner, 2, start + 16) == Result::Accepted);
    execute(m, start + 17, "second"); assert(std::string(read(m, owner, 2)) == "second");
    assert(submit(m, owner, 1, start + 18) == Result::Completed);
    assert(submit(m, stranger, 1, start + 18) == Result::Completed); ++cases;
  }
  {
    Mailbox m; Client c(1); std::string maximum(Mailbox::INPUT_CAPACITY - 1, 'P');
    const std::string too_large(Mailbox::INPUT_CAPACITY, 'X'); const char embedded[] = {'a', 0, 'b'};
    for (unsigned state = 0; state < 4; ++state) {
      const std::string prior(m.input()); const State prior_state = m.state();
      assert(m.submit(c.token, 1, c.digest, nullptr, 10, state) == Result::Invalid);
      assert(m.submit(c.token, 0, c.digest, "valid", 5, state) == Result::Invalid);
      assert(m.submit(c.token, 1, c.digest, "", 0, state) == Result::Invalid);
      assert(m.submit(c.token, 1, c.digest, too_large.data(), too_large.size(), state) == Result::Invalid);
      assert(m.submit(c.token, 1, c.digest, embedded, sizeof(embedded), state) == Result::Invalid);
      assert(m.state() == prior_state && std::string(m.input()) == prior);
      if (state == 0) {
        assert(m.submit(c.token, 1, c.digest, maximum.data(), maximum.size(), state) == Result::Accepted);
        assert(strlen(m.input()) == 4096 && m.input()[4096] == 0);
      } else if (state == 1) assert(m.begin());
      else if (state == 2) m.finish(state);
      ++cases;
    }
    for (size_t i = 0; i < Mailbox::INPUT_CAPACITY; ++i) assert(m.input()[i] == 0);
  }
  for (const uint32_t start : {100U, UINT32_MAX - 100U}) {
    Mailbox m; Client a(1), b(2);
    assert(submit(m, a, 10, start) == Result::Accepted); execute(m, start + 5);
    assert(submit(m, b, 1, start + 5 + Mailbox::RESULT_TTL_MS - 1) == Result::Busy);
    assert(submit(m, b, 1, start + 5 + Mailbox::RESULT_TTL_MS) == Result::Accepted);
    assert(read(m, a, 10) == nullptr); execute(m, start + 5 + Mailbox::RESULT_TTL_MS); assert(read(m, b, 1));
    assert(submit(m, a, 9, start + Mailbox::OWNER_TTL_MS - 1) == Result::Completed);
    assert(submit(m, a, 9, start + Mailbox::OWNER_TTL_MS) == Result::Accepted);
    execute(m, start + Mailbox::OWNER_TTL_MS); assert(read(m, a, 9)); ++cases;
  }
  {
    Mailbox m;
    for (uint8_t i = 1; i <= 8; ++i) {
      Client owner(i); assert(submit(m, owner, 7, i) == Result::Accepted);
      execute(m, i); assert(read(m, owner, 7)); ++cases;
    }
    Client ninth(9), first(1);
    assert(submit(m, ninth, 1, 10) == Result::Busy);
    assert(submit(m, first, 6, 10) == Result::Completed);
    assert(submit(m, first, 8, 10) == Result::Accepted); execute(m, 10); assert(read(m, first, 8));
    assert(submit(m, ninth, 1, 2 + Mailbox::OWNER_TTL_MS - 1) == Result::Busy);
    assert(submit(m, ninth, 1, 2 + Mailbox::OWNER_TTL_MS) == Result::Accepted); ++cases;
  }
  {
    Mailbox m; Client owner(1), other(2); bool pending;
    assert(submit(m, owner, 1, 100) == Result::Accepted); execute(m, 100);
    assert(std::string(m.read(owner.token, 1, pending)) == "success" && !pending);
    // Copy/allocation failure must leave the cached result protected for retry.
    assert(submit(m, other, 1, 101) == Result::Busy);
    m.delivered(other.token, 1); m.delivered(owner.token, 2);
    assert(submit(m, other, 1, 102) == Result::Busy);
    assert(std::string(m.read(owner.token, 1, pending)) == "success" && !pending);
    m.delivered(owner.token, 1);
    assert(submit(m, other, 1, 103) == Result::Accepted); ++cases;
  }
  {
    Mailbox m;
    for (uint8_t i = 1; i <= 100; ++i) {
      Client unauthenticated(i);
      assert(submit(m, unauthenticated, 1, i) == Result::Accepted);
      assert(m.begin()); strcpy(m.output(), "bad password"); m.finish(i, false);
      assert(std::string(read(m, unauthenticated, 1)) == "bad password");
      assert(!m.begin()); ++cases;
    }
    // Bad-password requests never exhaust the eight durable in-boot floors.
    for (uint8_t i = 101; i <= 108; ++i) {
      Client authenticated(i); assert(submit(m, authenticated, 7, i) == Result::Accepted);
      execute(m, i); assert(read(m, authenticated, 7)); ++cases;
    }
    Client ninth(109); assert(submit(m, ninth, 1, 109) == Result::Busy); ++cases;
  }
  {
    Mailbox m; Client owner(1), other(2);
    assert(submit(m, owner, 7, 100) == Result::Accepted); execute(m, 100); assert(read(m, owner, 7));
    assert(submit(m, owner, 8, 101) == Result::Accepted); assert(m.begin()); m.finish(101, false);
    assert(read(m, owner, 8));
    assert(submit(m, other, 1, 102) == Result::Accepted); execute(m, 102); assert(read(m, other, 1));
    assert(submit(m, owner, 7, 103) == Result::Completed);
    assert(submit(m, owner, 8, 103) == Result::Completed); ++cases;
  }
  {
    Mailbox m; Client c(1), other(2);
    assert(submit(m, c, UINT32_MAX, 10) == Result::Accepted); execute(m, 10); assert(read(m, c, UINT32_MAX));
    assert(submit(m, c, 0, 11) == Result::Invalid);
    assert(submit(m, c, 1, 11) == Result::Completed);
    assert(submit(m, other, 1, 11) == Result::Accepted); execute(m, 11); assert(read(m, other, 1));
    assert(submit(m, c, UINT32_MAX, 12) == Result::Completed); ++cases;
  }
  printf("%u production room web mailbox handshake scenarios passed\n", cases);
}
'''


class RoomWebMailboxTests(unittest.TestCase):
    def test_actual_bounded_mailbox_ownership_retry_and_expiry(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="room-web-mailbox-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(HARNESS)
            binary = work / "mailbox"
            built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *sanitizers, f"-I{ROOT / 'src'}", str(work / "test.cpp"), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn("production room web mailbox handshake scenarios passed", tested.stdout)
            print(tested.stdout.strip())


if __name__ == "__main__":
    unittest.main()
