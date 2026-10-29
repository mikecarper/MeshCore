"""Host tests for the radio preset table, binary codec and list formatting."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

TABLE_HARNESS = r'''
#include <helpers/RadioPresets.h>
#include <cstdio>
#include <cstring>
using namespace mesh;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

static void fillSample(RadioPresetTable& t) {
  t.set(1, "Primary", 910.525f, 62.5f, 7, 5, 0);
  t.set(2, "Private", 915.0f, 62.5f, 7, 5, 16);
  t.pending.active = true;
  t.pending.slot = 2;
  t.pending.remaining_secs = 600;
  std::strcpy(t.pending.previous.name, "Primary");
  t.pending.previous.freq = 910.525f; t.pending.previous.bw = 62.5f;
  t.pending.previous.sf = 7; t.pending.previous.cr = 5; t.pending.previous.preamble = 0;
  t.pending.previous.used = true;
}

static bool sameSlots(const RadioPresetTable& a, const RadioPresetTable& b) {
  for (uint8_t i = 1; i <= RadioPresetTable::SLOTS; i++) {
    const RadioPreset* x = a.get(i); const RadioPreset* y = b.get(i);
    if ((x == nullptr) != (y == nullptr)) return false;
    if (x && (std::strcmp(x->name, y->name) || x->freq != y->freq || x->bw != y->bw ||
              x->sf != y->sf || x->cr != y->cr || x->preamble != y->preamble)) return false;
  }
  return true;
}

static bool allUnused(const RadioPresetTable& t) {
  for (uint8_t i = 1; i <= RadioPresetTable::SLOTS; i++) if (t.get(i)) return false;
  return true;
}

int main() {
  { // set_and_get
    RadioPresetTable t;
    CHECK(t.set(2, "Private", 915.0f, 62.5f, 7, 5, 0));
    CHECK(t.get(2) != nullptr && t.get(2)->sf == 7);
    CHECK(t.get(3) == nullptr);
    CHECK(!t.set(6, "X", 915.0f, 62.5f, 7, 5, 0));
    CHECK(!t.set(0, "X", 915.0f, 62.5f, 7, 5, 0));
    CHECK(!t.set(1, "bad name", 915.0f, 62.5f, 7, 5, 0));
    CHECK(t.clear(2) && t.get(2) == nullptr);
    CHECK(!t.clear(0) && !t.clear(6));
  }
  { // name_rules
    CHECK(RadioPresetTable::validName("Private"));
    CHECK(!RadioPresetTable::validName(""));
    CHECK(!RadioPresetTable::validName("has space"));
    CHECK(!RadioPresetTable::validName("a,b"));
    CHECK(!RadioPresetTable::validName("ThirteenChars"));
    CHECK(RadioPresetTable::validName("TwelveChars1"));
  }
  { // match_active
    RadioPresetTable t;
    t.set(2, "Private", 915.0f, 62.5f, 7, 5, 0);
    CHECK(t.match(915.0f, 62.5f, 7, 5, 0) == 2);
    CHECK(t.match(915.0f, 62.5f, 7, 5, 16) == 0);
    CHECK(t.match(915.0004f, 62.52f, 7, 5, 0) == 2);
    CHECK(t.match(915.01f, 62.5f, 7, 5, 0) == 0);
  }
  { // roundtrip + size bound
    RadioPresetTable t; fillSample(t);
    for (uint8_t i = 3; i <= 5; i++) t.set(i, "TwelveChars1", 2400.0f, 812.5f, 12, 8, 65535);
    uint8_t buf[RadioPresetTable::ENCODED_MAX];
    size_t n = t.encode(buf, sizeof(buf));
    CHECK(n > 0 && n <= RadioPresetTable::ENCODED_MAX);
    CHECK(t.encode(buf, 10) == 0);
    RadioPresetTable u;
    CHECK(u.decode(buf, n));
    CHECK(sameSlots(t, u));
    CHECK(u.pending.active && u.pending.slot == 2 && u.pending.remaining_secs == 600);
    CHECK(u.pending.previous.freq == 910.525f && u.pending.previous.bw == 62.5f);
    CHECK(u.pending.previous.sf == 7 && u.pending.previous.cr == 5);
    CHECK(std::strcmp(u.pending.previous.name, "Primary") == 0);
  }
  { // decode_rejects_corrupt
    RadioPresetTable t; fillSample(t);
    uint8_t buf[RadioPresetTable::ENCODED_MAX];
    size_t n = t.encode(buf, sizeof(buf));
    RadioPresetTable u;
    uint8_t bad[RadioPresetTable::ENCODED_MAX];
    std::memcpy(bad, buf, n); bad[20] ^= 0x01;
    CHECK(!u.decode(bad, n)); CHECK(allUnused(u) && !u.pending.active);
    u = t;
    CHECK(!u.decode(buf, 10)); CHECK(allUnused(u) && !u.pending.active);
    u = t;
    std::memcpy(bad, buf, n); bad[2] = 2;
    CHECK(!u.decode(bad, n)); CHECK(allUnused(u));
    u = t;
    std::memcpy(bad, buf, n); bad[0] = 'X';
    CHECK(!u.decode(bad, n)); CHECK(allUnused(u));
    u = t;
    CHECK(!u.decode(buf, n - 1)); CHECK(allUnused(u));
  }
  { // list_fits_remote_capacity
    RadioPresetTable t;
    const char* names[5] = {"TwelveChars1","TwelveChars2","TwelveChars3","TwelveChars4","TwelveChars5"};
    for (uint8_t i = 1; i <= 5; i++) t.set(i, names[i-1], 2400.0f, 812.5f, 12, 8, 65535);
    char out[160];
    size_t n = formatPresetList(t, 0, out, sizeof(out));
    CHECK(n <= 159 && n == std::strlen(out));
    CHECK(out[0] == '1');
    CHECK(std::strstr(out, "(get preset N)") != nullptr);
    RadioPresetTable s;
    s.set(1, "Primary", 910.525f, 62.5f, 7, 5, 0);
    s.set(2, "Private", 915.0f, 62.5f, 7, 5, 16);
    n = formatPresetList(s, 1, out, sizeof(out));
    // 910.5250244 is the real StrHelper::ftoa output for float 910.525 (identical to "get radio").
    CHECK(std::strstr(out, "1*Primary 910.5250244,62.5,7,5\n") != nullptr);
    CHECK(std::strstr(out, "2 Private 915.0,62.5,7,5,16\n") != nullptr);
    CHECK(std::strstr(out, "3 -") != nullptr);
    char one[80];
    formatPreset(s, 2, 1, one, sizeof(one));
    CHECK(std::strcmp(one, "2 Private 915.0,62.5,7,5,16") == 0);
    formatPreset(s, 4, 1, one, sizeof(one));
    CHECK(std::strcmp(one, "4 -") == 0);
  }
  if (failures) return 1;
  std::printf("PASS table\n");
  return 0;
}
'''


class RadioPresetsTest(unittest.TestCase):
    def build_and_run(self, name, source, expected, extra_sources=()):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='radio-presets-') as directory:
            work = Path(directory)
            cpp = work / (name + '.cpp')
            cpp.write_text(source, encoding='ascii')
            binary = work / (name + ('.exe' if os.name == 'nt' else ''))
            built = subprocess.run([
                compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                *sanitize,
                '-I', str(ROOT / 'src'), '-I', str(ROOT / 'test'),
                '-isystem', str(ROOT / 'test' / 'mocks'),  # Arduino.h mock; not -Werror clean
                str(cpp), *[str(ROOT / s) for s in extra_sources], '-o', str(binary),
            ], capture_output=True, text=True, timeout=120, cwd=str(ROOT / 'test'))
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn(expected, ran.stdout)

    def test_preset_table(self):
        self.build_and_run('table', TABLE_HARNESS, 'PASS table',
                           extra_sources=('src/helpers/TxtDataHelpers.cpp',))


if __name__ == '__main__':
    unittest.main()
