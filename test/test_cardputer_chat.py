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
                '-I', str(stubs), '-I', str(VARIANT),
                str(cpp), '-o', str(binary),
            ], capture_output=True, text=True, timeout=120, cwd=str(ROOT / 'test'))
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn(expected, ran.stdout)

    def test_keylock_filters(self):
        self.build_and_run('keylock', KEYLOCK_HARNESS, 'PASS keylock')


if __name__ == '__main__':
    unittest.main()
