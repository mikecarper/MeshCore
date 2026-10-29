"""Host tests for Cardputer chat/preset UI helpers (KeyLock)."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / 'variants' / 'm5stack_cardputer_adv'

KEYLOCK_HARNESS = r'''
#include <companion_radio/ui-cardputer/KeyLock.h>
#include <cstdio>
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

static Keyboard::Event ev(Keyboard::Key k, bool fn) {
  Keyboard::Event e;
  e.key = k; e.changed = true; e.down = true; e.modifiers.fn = fn;
  return e;
}

int main() {
  KeyLock lock;
  // keylock_filters: unlocked passes everything
  CHECK(!lock.filter(ev(Keyboard::KEY_A, false)));
  CHECK(!lock.filter(ev(Keyboard::KEY_RETURN, false)));
  CHECK(!lock.filter(ev(Keyboard::KEY_Q, true)));
  CHECK(KeyLock::isToggle(ev(Keyboard::KEY_Q, true)));
  CHECK(!KeyLock::isToggle(ev(Keyboard::KEY_Q, false)));
  lock.toggle();
  CHECK(lock.locked);
  // locked: letters/Enter/other Fn combos dropped, plain Q dropped, Fn+Q passes
  CHECK(lock.filter(ev(Keyboard::KEY_A, false)));
  CHECK(lock.filter(ev(Keyboard::KEY_RETURN, false)));
  CHECK(lock.filter(ev(Keyboard::KEY_Q, false)));
  CHECK(lock.filter(ev(Keyboard::KEY_1, true)));
  CHECK(!lock.filter(ev(Keyboard::KEY_Q, true)));
  lock.toggle();
  CHECK(!lock.locked);
  CHECK(!lock.filter(ev(Keyboard::KEY_A, false)));
  if (failures) return 1;
  std::printf("PASS keylock\n");
  return 0;
}
'''

HISTORY_HARNESS = r'''
#include <HistoryLine.h>
#include <OwnMessageTracker.h>
#include <cstdio>
#include <cstring>
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

static void v2_roundtrip() {
  HistoryMessage m = {};
  m.out = false;
  std::snprintf(m.text, sizeof(m.text), "Bob: hello {there}");
  m.meta.has = true;
  m.meta.sent_ts = 1761700000u;
  m.meta.recv_ts = 4294967295u;
  m.meta.hops = 3;
  m.meta.snr_q4 = -37;
  m.meta.rssi = -118;
  m.meta.hash_size = 1;
  m.meta.path_len = 3;
  m.meta.path[0] = 0xC5; m.meta.path[1] = 0xD8; m.meta.path[2] = 0x14;
  char line[256];
  size_t n = encodeHistoryLine(m, line, sizeof(line));
  CHECK(n == std::strlen(line));
  CHECK(std::strncmp(line, "<< {m1;", 7) == 0);
  CHECK(std::strstr(line, "C5D814") != nullptr);
  HistoryMessage d;
  d.own_id = 77;
  CHECK(decodeHistoryLine(line, d));
  CHECK(!d.out);
  CHECK(std::strcmp(d.text, "Bob: hello {there}") == 0);
  CHECK(d.meta.has);
  CHECK(d.meta.sent_ts == 1761700000u);
  CHECK(d.meta.recv_ts == 4294967295u);
  CHECK(d.meta.hops == 3);
  CHECK(d.meta.snr_q4 == -37);
  CHECK(d.meta.rssi == -118);
  CHECK(d.meta.hash_size == 1);
  CHECK(d.meta.path_len == 3);
  CHECK(d.meta.path[0] == 0xC5 && d.meta.path[1] == 0xD8 && d.meta.path[2] == 0x14);
  CHECK(d.own_id == 0);

  // unknown hops and 2-byte hashes survive too
  m.meta.hops = 0xFF; m.meta.hash_size = 2; m.meta.path_len = 1;
  m.meta.path[0] = 0xAB; m.meta.path[1] = 0x01;
  encodeHistoryLine(m, line, sizeof(line));
  CHECK(decodeHistoryLine(line, d));
  CHECK(d.meta.hops == 0xFF && d.meta.hash_size == 2 && d.meta.path_len == 1);
  CHECK(d.meta.path[0] == 0xAB && d.meta.path[1] == 0x01);

  // own messages persist text only
  HistoryMessage own = {};
  own.out = true;
  own.meta.has = true;
  std::snprintf(own.text, sizeof(own.text), "line1\nline2");
  encodeHistoryLine(own, line, sizeof(line));
  CHECK(std::strcmp(line, ">> line1 line2") == 0);

  // a too-small buffer never overflows and stays terminated
  char small[8];
  encodeHistoryLine(m, small, sizeof(small));
  CHECK(std::strlen(small) < sizeof(small));
  std::printf("PASS v2_roundtrip\n");
}

static void legacy_lines_load() {
  HistoryMessage d;
  CHECK(decodeHistoryLine(">> hi", d));
  CHECK(d.out && std::strcmp(d.text, "hi") == 0 && !d.meta.has);
  CHECK(decodeHistoryLine("<< hi there", d));
  CHECK(!d.out && std::strcmp(d.text, "hi there") == 0 && !d.meta.has);
  CHECK(!decodeHistoryLine("<> hi", d));
  CHECK(!decodeHistoryLine("<<", d));
  CHECK(!decodeHistoryLine("garbage", d));
  std::printf("PASS legacy_lines_load\n");
}

static void brace_text_stays_text() {
  HistoryMessage d;
  CHECK(decodeHistoryLine("<< {not meta} hi", d));
  CHECK(std::strcmp(d.text, "{not meta} hi") == 0 && !d.meta.has);
  // partial / malformed v2 prefixes stay text
  CHECK(decodeHistoryLine("<< {m1;1;2;3} x", d));
  CHECK(std::strcmp(d.text, "{m1;1;2;3} x") == 0 && !d.meta.has);
  CHECK(decodeHistoryLine("<< {m1;1;2;3;4;5;1;ZZ} x", d));
  CHECK(!d.meta.has);
  CHECK(decodeHistoryLine("<< {m1;1;2;3;4;5;2;C5} x", d)); // odd hash bytes
  CHECK(!d.meta.has);
  CHECK(decodeHistoryLine("<< {m1;1;2;3;999;5;1;} x", d)); // snr out of range
  CHECK(!d.meta.has);
  CHECK(decodeHistoryLine("<< {m1;1;2;3;4;5;1;}x", d)); // no separator
  CHECK(!d.meta.has);
  CHECK(decodeHistoryLine("<< {m1;1;2;3;4;5;1;}", d)); // empty text is fine
  CHECK(d.meta.has && d.text[0] == 0);
  std::printf("PASS brace_text_stays_text\n");
}

static void tracker_counts_echoes() {
  OwnMessageTracker t;
  uint8_t h[MAX_HASH_SIZE] = {1, 2, 3, 4, 5, 6, 7, 8};
  uint8_t other[MAX_HASH_SIZE] = {9};
  uint32_t id = t.add(h, true, 1000);
  CHECK(id != 0);
  t.onEcho(h, 0xC5);
  t.onEcho(h, 0xD8);
  const OwnMessageInfo *i = t.find(id);
  CHECK(i != nullptr);
  CHECK(i->repeats == 2);
  CHECK(i->repeater_count == 2 && i->repeaters[0] == 0xC5 && i->repeaters[1] == 0xD8);
  t.onEcho(h, 0xC5); // duplicate repeater: counts the repeat, not a new ID
  CHECK(i->repeats == 3 && i->repeater_count == 2);
  t.onEcho(other, 0x11); // unrelated packet
  CHECK(i->repeats == 3);
  CHECK(i->state == OwnMessageInfo::Pending);

  uint32_t dm = t.add(other, false, 2000);
  t.onEcho(other, 0x22); // DMs are not counted as channel echoes
  CHECK(t.find(dm)->repeats == 0);
  t.onAck(dm, 2600);
  CHECK(t.find(dm)->state == OwnMessageInfo::Delivered && t.find(dm)->rtt_ms == 600);
  uint32_t dm2 = t.add(other, false, 3000);
  t.onFailed(dm2);
  CHECK(t.find(dm2)->state == OwnMessageInfo::Failed);
  CHECK(t.find(0) == nullptr);
  std::printf("PASS tracker_counts_echoes\n");
}

static void tracker_evicts_oldest() {
  OwnMessageTracker t;
  uint32_t ids[9];
  for (int k = 0; k < 9; k++) {
    uint8_t h[MAX_HASH_SIZE] = {(uint8_t)k};
    ids[k] = t.add(h, true, k);
  }
  CHECK(t.find(ids[0]) == nullptr);
  for (int k = 1; k < 9; k++) CHECK(t.find(ids[k]) != nullptr && t.find(ids[k])->own_id == ids[k]);
  std::printf("PASS tracker_evicts_oldest\n");
}

static void split_sender() {
  char name[8];
  CHECK(splitChannelSender("Bob: hi", name, sizeof(name)));
  CHECK(std::strcmp(name, "Bob") == 0);
  CHECK(!splitChannelSender("no colon", name, sizeof(name)));
  CHECK(splitChannelSender("Bartholomew: hi", name, sizeof(name)));
  CHECK(std::strcmp(name, "Barthol") == 0);
  std::printf("PASS split_sender\n");
}

int main() {
  v2_roundtrip();
  legacy_lines_load();
  brace_text_stays_text();
  tracker_counts_echoes();
  tracker_evicts_oldest();
  split_sender();
  return failures ? 1 : 0;
}
'''


class CardputerChatTest(unittest.TestCase):
    PLATFORM_MACROS = ('-DNAME_MAX=255',)

    def build_and_run(self, name, source, expected):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='cardputer-chat-') as directory:
            work = Path(directory)
            stubs = work / 'stubs'
            stubs.mkdir()
            (stubs / 'Adafruit_TCA8418.h').write_text(
                '#pragma once\nclass Adafruit_TCA8418 {};\n', encoding='ascii')
            cpp = work / (name + '.cpp')
            cpp.write_text(source, encoding='ascii')
            binary = work / (name + ('.exe' if os.name == 'nt' else ''))
            built = subprocess.run([
                compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                *sanitize, *self.PLATFORM_MACROS,
                '-I', str(stubs), '-I', str(VARIANT), '-isystem', str(ROOT / 'src'),
                str(cpp), '-o', str(binary),
            ], capture_output=True, text=True, timeout=120, cwd=str(ROOT / 'test'))
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn(expected, ran.stdout)

    def test_keylock_filters(self):
        self.build_and_run('keylock', KEYLOCK_HARNESS, 'PASS keylock')

    def _history(self, name):
        self.build_and_run('history', HISTORY_HARNESS, 'PASS ' + name)

    def test_v2_roundtrip(self):
        self._history('v2_roundtrip')

    def test_legacy_lines_load(self):
        self._history('legacy_lines_load')

    def test_brace_text_stays_text(self):
        self._history('brace_text_stays_text')

    def test_tracker_counts_echoes(self):
        self._history('tracker_counts_echoes')

    def test_tracker_evicts_oldest(self):
        self._history('tracker_evicts_oldest')

    def test_split_sender(self):
        self._history('split_sender')


if __name__ == '__main__':
    unittest.main()
