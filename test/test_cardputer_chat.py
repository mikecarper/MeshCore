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

static void own_line_brace_prefix_stays_text() {
  HistoryMessage d;
  CHECK(decodeHistoryLine(">> {m1;1;2;3;4;5;1;C5} x", d));
  CHECK(d.out && std::strcmp(d.text, "{m1;1;2;3;4;5;1;C5} x") == 0 && !d.meta.has);
  CHECK(decodeHistoryLine(">> {m1;1;2;3;4;5;1;}", d));
  CHECK(d.out && std::strcmp(d.text, "{m1;1;2;3;4;5;1;}") == 0 && !d.meta.has);
  std::printf("PASS own_line_brace_prefix_stays_text\n");
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

static void tracker_stores_route() {
  OwnMessageTracker t;
  CHECK(sizeof(OwnMessageInfo) == 36);
  uint32_t direct = t.add(nullptr, false, 0, 0x00);  // direct, 0 hops
  uint32_t two = t.add(nullptr, false, 0, 0x02);     // 2 hops, 1-byte hashes
  uint32_t wide = t.add(nullptr, false, 0, 0x43);    // 3 hops, 2-byte hashes
  uint32_t flood = t.add(nullptr, false, 0, OWN_ROUTE_FLOOD);
  uint32_t dflt = t.add(nullptr, false, 0);
  CHECK(t.find(direct)->route == 0x00);
  CHECK(t.find(two)->route == 0x02);
  CHECK(t.find(wide)->route == 0x43);
  CHECK(t.find(flood)->route == OWN_ROUTE_FLOOD);
  CHECK(t.find(dflt)->route == OWN_ROUTE_FLOOD);
  t.setRoute(two, OWN_ROUTE_FLOOD); // retries fell back to flood
  CHECK(t.find(two)->route == OWN_ROUTE_FLOOD);
  t.setRoute(0, 0x01); // unknown id: no effect, no crash
  std::printf("PASS tracker_stores_route\n");
}

static void own_route_format() {
  char b[24];
  formatOwnRoute(OWN_ROUTE_FLOOD, b, sizeof(b));
  CHECK(std::strcmp(b, "Route Flood") == 0);
  formatOwnRoute(0x00, b, sizeof(b));
  CHECK(std::strcmp(b, "Route Direct") == 0);
  formatOwnRoute(0x01, b, sizeof(b));
  CHECK(std::strcmp(b, "Route 1 hop") == 0);
  formatOwnRoute(0x02, b, sizeof(b));
  CHECK(std::strcmp(b, "Route 2 hops") == 0);
  formatOwnRoute(0x43, b, sizeof(b)); // hash-size bits ignored
  CHECK(std::strcmp(b, "Route 3 hops") == 0);
  std::printf("PASS own_route_format\n");
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
  own_line_brace_prefix_stays_text();
  tracker_counts_echoes();
  tracker_evicts_oldest();
  tracker_stores_route();
  own_route_format();
  split_sender();
  return failures ? 1 : 0;
}
'''


FOCUS_HARNESS = r'''
#include <companion_radio/ui-cardputer/screen/main/ChatFocus.h>
#include <cstdio>
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)
using T = ChatFocus::Target;
using A = ChatFocus::Action;
using K = ChatFocus::Key;

static ChatFocus onMessage(int msg) {
  ChatFocus f;
  f.target = T::Message;
  f.msg = msg;
  return f;
}

static ChatFocus inMenu(bool own) {
  ChatFocus f = onMessage(2);
  CHECK(f.press(K::Enter, true, 5, own) == A::OpenMenu);
  return f;
}

static void up_from_empty_box_focuses_newest() {
  ChatFocus f;
  CHECK(f.target == T::TextBox && f.msg == -1);
  CHECK(f.press(K::Up, true, 5, false) == A::None);
  CHECK(f.target == T::Message && f.msg == 0);
  // no messages: nothing to focus, Up does nothing
  ChatFocus g;
  CHECK(g.press(K::Up, true, 0, false) == A::None);
  CHECK(g.target == T::TextBox);
  // Up on a message moves to older ones and clamps at the oldest
  CHECK(f.press(K::Up, true, 3, false) == A::None && f.msg == 1);
  CHECK(f.press(K::Up, true, 3, false) == A::None && f.msg == 2);
  CHECK(f.press(K::Up, true, 3, false) == A::None && f.msg == 2);
  std::printf("PASS up_from_empty_box_focuses_newest\n");
}

static void up_with_text_types_semicolon() {
  ChatFocus f;
  CHECK(f.press(K::Up, false, 5, false) == A::TypeChar);
  CHECK(f.target == T::TextBox && f.msg == -1);
  std::printf("PASS up_with_text_types_semicolon\n");
}

static void fn_up_leaves_box_with_text() {
  ChatFocus f;
  CHECK(f.press(K::FnUp, false, 5, false) == A::None);
  CHECK(f.target == T::Message && f.msg == 0);
  ChatFocus g;
  CHECK(g.press(K::FnUp, true, 5, false) == A::None);
  CHECK(g.target == T::Message && g.msg == 0);
  std::printf("PASS fn_up_leaves_box_with_text\n");
}

static void down_types_period() {
  ChatFocus f;
  CHECK(f.press(K::Down, true, 5, false) == A::TypeChar);
  CHECK(f.press(K::Down, false, 5, false) == A::TypeChar);
  CHECK(f.press(K::Char, true, 5, false) == A::TypeChar);
  CHECK(f.target == T::TextBox);
  std::printf("PASS down_types_period\n");
}

static void enter_in_box_sends() {
  ChatFocus f;
  CHECK(f.press(K::Enter, false, 5, false) == A::Send);
  CHECK(f.target == T::TextBox);
  std::printf("PASS enter_in_box_sends\n");
}

static void enter_on_message_opens_menu_never_sends() {
  for (int own = 0; own < 2; own++) {
    for (int empty = 0; empty < 2; empty++) {
      ChatFocus f = onMessage(1);
      A a = f.press(K::Enter, empty != 0, 5, own != 0);
      CHECK(a == A::OpenMenu);
      CHECK(a != A::Send);
      CHECK(f.target == T::Menu && f.msg == 1 && f.menu_item == 0);
    }
  }
  std::printf("PASS enter_on_message_opens_menu_never_sends\n");
}

static void char_on_message_ignored() {
  ChatFocus f = onMessage(1);
  CHECK(f.press(K::Char, false, 5, false) == A::None);
  CHECK(f.press(K::Char, true, 5, true) == A::None);
  CHECK(f.target == T::Message && f.msg == 1);
  std::printf("PASS char_on_message_ignored\n");
}

static void down_from_newest_returns_to_box() {
  ChatFocus f = onMessage(2);
  CHECK(f.press(K::Down, false, 5, false) == A::None && f.msg == 1 && f.target == T::Message);
  CHECK(f.press(K::Down, false, 5, false) == A::None && f.msg == 0 && f.target == T::Message);
  CHECK(f.press(K::Down, false, 5, false) == A::None);
  CHECK(f.target == T::TextBox && f.msg == -1);
  std::printf("PASS down_from_newest_returns_to_box\n");
}

static void esc_on_message_returns_to_box() {
  ChatFocus f = onMessage(3);
  CHECK(f.press(K::Esc, true, 5, false) == A::None);
  CHECK(f.target == T::TextBox && f.msg == -1);
  std::printf("PASS esc_on_message_returns_to_box\n");
}

static void esc_in_box_leaves_chat() {
  ChatFocus f;
  CHECK(f.press(K::Esc, true, 5, false) == A::LeaveChat);
  CHECK(f.press(K::Esc, false, 0, false) == A::LeaveChat);
  std::printf("PASS esc_in_box_leaves_chat\n");
}

static void menu_hides_resend_for_received() {
  // received: Reply(0) -> Info(1) -> Copy(3) -> Reply(0), never Resend(2)
  ChatFocus f = inMenu(false);
  CHECK(f.menu_item == 0);
  CHECK(f.press(K::Down, true, 5, false) == A::None && f.menu_item == 1);
  CHECK(f.press(K::Down, true, 5, false) == A::None && f.menu_item == 3);
  CHECK(f.press(K::Down, true, 5, false) == A::None && f.menu_item == 0);
  CHECK(f.press(K::Up, true, 5, false) == A::None && f.menu_item == 3);
  CHECK(f.press(K::Up, true, 5, false) == A::None && f.menu_item == 1);
  // own: all four, Resend reachable and acts, focus back on the message
  ChatFocus g = inMenu(true);
  CHECK(g.press(K::Down, true, 5, true) == A::None && g.menu_item == 1);
  CHECK(g.press(K::Down, true, 5, true) == A::None && g.menu_item == 2);
  CHECK(g.press(K::Enter, true, 5, true) == A::Resend);
  CHECK(g.target == T::Message && g.msg == 2);
  std::printf("PASS menu_hides_resend_for_received\n");
}

static void menu_reply_targets_box() {
  ChatFocus f = inMenu(false);
  CHECK(f.press(K::Enter, true, 5, false) == A::Reply);
  CHECK(f.target == T::TextBox && f.msg == -1);
  // Copy also moves to the text box
  ChatFocus g = inMenu(true);
  g.press(K::Up, true, 5, true); // wraps to Copy
  CHECK(g.menu_item == 3);
  CHECK(g.press(K::Enter, true, 5, true) == A::Copy);
  CHECK(g.target == T::TextBox && g.msg == -1);
  // Info keeps the menu owning the screen; Esc closes it back to the list
  ChatFocus h = inMenu(false);
  h.press(K::Down, true, 5, false);
  CHECK(h.press(K::Enter, true, 5, false) == A::Info);
  CHECK(h.target == T::Menu && h.info && h.msg == 2);
  CHECK(h.press(K::Down, true, 5, false) == A::None && h.info && h.menu_item == 1);
  CHECK(h.press(K::Esc, true, 5, false) == A::None);
  CHECK(h.target == T::Menu && !h.info && h.menu_item == 1);
  std::printf("PASS menu_reply_targets_box\n");
}

static void menu_esc_returns_to_message() {
  ChatFocus f = inMenu(true);
  CHECK(f.press(K::Char, true, 5, true) == A::None && f.target == T::Menu);
  CHECK(f.press(K::Esc, true, 5, true) == A::None);
  CHECK(f.target == T::Message && f.msg == 2);
  std::printf("PASS menu_esc_returns_to_message\n");
}

static void focus_follows_new_messages() {
  ChatFocus f = onMessage(1);
  f.onMessageAdded(5);
  CHECK(f.target == T::Message && f.msg == 2);
  f.msg = 3;
  f.onMessageAdded(5); // ring full, focused message not the oldest: follows it
  CHECK(f.target == T::Message && f.msg == 4);
  ChatFocus g;
  g.onMessageAdded(5); // text box focus is unaffected
  CHECK(g.target == T::TextBox && g.msg == -1);
  // focus on a message with an emptied history falls back to the box
  ChatFocus h = onMessage(0);
  CHECK(h.press(K::Up, true, 0, false) == A::None);
  CHECK(h.target == T::TextBox && h.msg == -1);
  std::printf("PASS focus_follows_new_messages\n");
}

static void evicted_focus_closes_menu_and_returns_to_box() {
  // ring of 5, full; the oldest message (index 4) is focused
  ChatFocus f = onMessage(4);
  f.onMessageAdded(5); // a new arrival evicts it
  CHECK(f.target == T::TextBox && f.msg == -1 && !f.info);
  // menu open on the oldest: closed, nothing can act on another message
  ChatFocus g = onMessage(4);
  CHECK(g.press(K::Enter, true, 5, true) == A::OpenMenu);
  g.onMessageAdded(5);
  CHECK(g.target == T::TextBox && g.msg == -1);
  CHECK(g.press(K::Enter, true, 5, true) == A::Send); // Enter is the box again
  // Info view open on the oldest: closed too
  ChatFocus h = onMessage(4);
  h.press(K::Enter, true, 5, false);
  h.press(K::Down, true, 5, false);
  CHECK(h.press(K::Enter, true, 5, false) == A::Info && h.info);
  h.onMessageAdded(5);
  CHECK(h.target == T::TextBox && !h.info && h.msg == -1);
  // ring not full (count grew): the menu stays on the same message
  ChatFocus k = onMessage(4);
  k.press(K::Enter, true, 5, true);
  k.onMessageAdded(6);
  CHECK(k.target == T::Menu && k.msg == 5);
  std::printf("PASS evicted_focus_closes_menu_and_returns_to_box\n");
}

int main() {
  up_from_empty_box_focuses_newest();
  up_with_text_types_semicolon();
  fn_up_leaves_box_with_text();
  down_types_period();
  enter_in_box_sends();
  enter_on_message_opens_menu_never_sends();
  char_on_message_ignored();
  down_from_newest_returns_to_box();
  esc_on_message_returns_to_box();
  esc_in_box_leaves_chat();
  menu_hides_resend_for_received();
  menu_reply_targets_box();
  menu_esc_returns_to_message();
  focus_follows_new_messages();
  evicted_focus_closes_menu_and_returns_to_box();
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


    def test_own_line_brace_prefix_stays_text(self):
        self._history('own_line_brace_prefix_stays_text')

    def test_tracker_stores_route(self):
        self._history('tracker_stores_route')

    def test_own_route_format(self):
        self._history('own_route_format')

    def _focus(self, name):
        self.build_and_run('focus', FOCUS_HARNESS, 'PASS ' + name)

    def test_up_from_empty_box_focuses_newest(self):
        self._focus('up_from_empty_box_focuses_newest')

    def test_up_with_text_types_semicolon(self):
        self._focus('up_with_text_types_semicolon')

    def test_fn_up_leaves_box_with_text(self):
        self._focus('fn_up_leaves_box_with_text')

    def test_down_types_period(self):
        self._focus('down_types_period')

    def test_enter_in_box_sends(self):
        self._focus('enter_in_box_sends')

    def test_enter_on_message_opens_menu_never_sends(self):
        self._focus('enter_on_message_opens_menu_never_sends')

    def test_char_on_message_ignored(self):
        self._focus('char_on_message_ignored')

    def test_down_from_newest_returns_to_box(self):
        self._focus('down_from_newest_returns_to_box')

    def test_esc_on_message_returns_to_box(self):
        self._focus('esc_on_message_returns_to_box')

    def test_esc_in_box_leaves_chat(self):
        self._focus('esc_in_box_leaves_chat')

    def test_menu_hides_resend_for_received(self):
        self._focus('menu_hides_resend_for_received')

    def test_menu_reply_targets_box(self):
        self._focus('menu_reply_targets_box')

    def test_menu_esc_returns_to_message(self):
        self._focus('menu_esc_returns_to_message')

    def test_focus_follows_new_messages(self):
        self._focus('focus_follows_new_messages')

    def test_evicted_focus_closes_menu_and_returns_to_box(self):
        self._focus('evicted_focus_closes_menu_and_returns_to_box')

if __name__ == '__main__':
    unittest.main()
