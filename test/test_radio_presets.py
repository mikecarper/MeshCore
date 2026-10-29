"""Host tests for the radio preset table, binary codec and list formatting."""
from pathlib import Path
import os
import re
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

CLI_HARNESS = r'''
#include <helpers/RadioPresetCLI.h>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <vector>
using namespace mesh;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

struct Env {
  std::vector<uint8_t> store;
  bool haveStore = false;
  int saves = 0;
  bool failSave = false;
  bool failApply = false;
  int failApplyCount = 0;   // fail this many apply calls, then succeed
  bool curBad = false;
  bool tempRadio = false;
  RadioPreset cur;
  std::vector<RadioPreset> applied;
  Env() {
    std::memset(&cur, 0, sizeof(cur));
    setCur(910.525f, 62.5f, 7, 5, 0);
  }
  void setCur(float f, float bw, uint8_t sf, uint8_t cr, uint16_t pre) {
    std::strcpy(cur.name, "cur"); cur.freq = f; cur.bw = bw; cur.sf = sf; cur.cr = cr; cur.preamble = pre; cur.used = true;
  }
};
static Env* E(void* c) { return (Env*)c; }
static bool hLoad(void* c, uint8_t* buf, size_t cap, size_t& len) {
  Env* e = E(c);
  if (!e->haveStore || e->store.size() > cap) return false;
  std::memcpy(buf, e->store.data(), e->store.size());
  len = e->store.size();
  return true;
}
static bool hSave(void* c, const uint8_t* buf, size_t len) {
  Env* e = E(c);
  if (e->failSave) return false;
  e->store.assign(buf, buf + len); e->haveStore = true; e->saves++;
  return true;
}
static bool hCurrent(void* c, RadioPreset& out) { out = E(c)->cur; return true; }
static bool hAccepts(void*, float f, float bw, uint8_t sf, uint8_t cr, uint16_t) {
  return f > 400.0f && f < 1000.0f && bw > 0.0f && sf >= 5 && sf <= 12 && cr >= 5 && cr <= 8;
}
static bool hApply(void* c, const RadioPreset& p, char* reply, size_t cap) {
  Env* e = E(c);
  if (e->failApplyCount > 0) { e->failApplyCount--; std::snprintf(reply, cap, "Error: boom"); return false; }
  if (e->failApply) { std::snprintf(reply, cap, "Error: boom"); return false; }
  e->applied.push_back(p);
  e->cur = p;
  return true;
}
static bool hTemp(void* c) { return E(c)->tempRadio; }
static RadioPresetCLI::Hooks hooks(Env& e) {
  RadioPresetCLI::Hooks h; h.ctx = &e; h.load = hLoad; h.save = hSave; h.current = hCurrent;
  h.accepts = hAccepts; h.apply = hApply; h.tempRadioActive = hTemp;
  return h;
}
static bool near(float a, float b) { return std::fabs(a - b) < 0.001f; }
static bool has(const char* s, const char* sub) { return std::strstr(s, sub) != nullptr; }
static bool run(RadioPresetCLI& c, const char* cmd, char* reply, bool remote = true) {
  reply[0] = 0;
  return c.handle(cmd, reply, 160, remote);
}
static void setup(RadioPresetCLI& c, Env& e) {
  c.begin(hooks(e));
  char r[160];
  run(c, "add preset Private 2 915.0,62.5,7,5", r);
}

int main() {
  char r[160];
  { // seed_us_canada_default
    Env e; e.setCur(915.0f, 250.0f, 11, 5, 0);
    RadioPresetCLI c; c.begin(hooks(e));
    CHECK(run(c, "get preset", r));
    CHECK(has(r, "1 USA-Canada "));
    CHECK(run(c, "get preset 1", r));
    CHECK(has(r, "1 USA-Canada 910.5250244,62.5,7,5"));
    CHECK(!has(r, "*"));
    CHECK(c.activeSlot() == 0);
    CHECK(e.saves == 1 && e.haveStore);
    RadioPresetCLI d; d.begin(hooks(e));   // second boot loads, does not reseed
    CHECK(e.saves == 1 && d.table().get(1) != nullptr);
  }
  { // seed_marks_active_when_on_us
    Env e;
    RadioPresetCLI c; c.begin(hooks(e));
    CHECK(run(c, "get preset 1", r));
    CHECK(has(r, "1*USA-Canada 910.5250244,62.5,7,5"));
    CHECK(c.activeSlot() == 1);
    CHECK(e.saves == 1 && e.haveStore);
  }
  { // seed_skipped_when_rejected
    Env e; e.setCur(433.0f, 125.0f, 9, 5, 0);
    RadioPresetCLI::Hooks h = hooks(e);
    h.accepts = [](void*, float f, float, uint8_t, uint8_t, uint16_t) { return f < 500.0f; };
    RadioPresetCLI c; c.begin(h);
    CHECK(c.table().get(1) == nullptr);
    CHECK(e.saves == 0 && !e.haveStore);
  }
  { // seed_when_corrupt
    Env e; e.haveStore = true; e.store.assign(10, 0x55);
    RadioPresetCLI c; c.begin(hooks(e));
    CHECK(c.table().get(1) != nullptr && std::strcmp(c.table().get(1)->name, "USA-Canada") == 0);
    CHECK(e.saves == 1);
  }
  { // add_validates_like_set_radio
    Env e; RadioPresetCLI c; c.begin(hooks(e));
    CHECK(run(c, "add preset Private 2 915.0,62.5,7,5", r)); CHECK(has(r, "OK"));
    CHECK(c.table().get(2) && near(c.table().get(2)->freq, 915.0f));
    CHECK(run(c, "add preset Private 2 915.0,62.5,13,5", r)); CHECK(has(r, "Error"));
    CHECK(c.table().get(2) && c.table().get(2)->sf == 7);
    CHECK(run(c, "add preset bad name 2 915,62.5,7,5", r)); CHECK(has(r, "Error"));
    CHECK(run(c, "add preset Ok 9 915,62.5,7,5", r)); CHECK(has(r, "Error"));
    CHECK(run(c, "add preset Ok 3 915,62.5,7", r)); CHECK(has(r, "Error"));
    CHECK(c.table().get(3) == nullptr);
    CHECK(run(c, "add preset Pre 3 915,62.5,7,5,16", r)); CHECK(has(r, "OK"));
    CHECK(c.table().get(3)->preamble == 16);
    int s = e.saves; e.failSave = true;
    CHECK(run(c, "add preset Nope 4 915,62.5,7,5", r)); CHECK(has(r, "Error"));
    CHECK(c.table().get(4) == nullptr && e.saves == s);
  }
  { // local_load_applies_immediately
    Env e; RadioPresetCLI c; setup(c, e);
    CHECK(run(c, "load preset 2", r, false)); CHECK(has(r, "OK"));
    CHECK(e.applied.size() == 1 && near(e.applied[0].freq, 915.0f));
    CHECK(!c.pending());
    CHECK(c.loadSlot(1, r, sizeof(r))); CHECK(e.applied.size() == 2 && near(e.applied[1].freq, 910.525f));
  }
  { // remote_load_then_commit
    Env e; RadioPresetCLI c; setup(c, e); c.loop(1000);
    CHECK(run(c, "load preset 2", r));
    std::printf("DBG[%s]\n", r);
    CHECK(has(r, "OK - loading Private 915.0,62.5,7,5; log in again within 10 min to keep"));
    RadioPresetTable t; CHECK(t.decode(e.store.data(), e.store.size()));
    CHECK(t.pending.active && t.pending.slot == 2 && near(t.pending.previous.freq, 910.525f));
    CHECK(e.applied.size() == 1 && near(e.applied[0].freq, 915.0f) && c.pending());
    c.onAdminLogin();
    CHECK(!c.pending());
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size())); CHECK(!u.pending.active);
    c.loop(1000 + 700000);
    CHECK(e.applied.size() == 1);
    int s = e.saves; c.onAdminLogin(); CHECK(e.saves == s);  // no pending: no-op
  }
  { // get_preset_shows_pending
    Env e; RadioPresetCLI c; setup(c, e); c.loop(1000);
    run(c, "load preset 2", r);
    CHECK(run(c, "get preset", r));
    CHECK(std::strncmp(r, "pending, reverts in 10m; ", 25) == 0);
    CHECK(has(r, "2*Private"));
    c.loop(1000 + 300000);
    run(c, "get preset", r);
    CHECK(std::strncmp(r, "pending, reverts in 5m; ", 24) == 0);
    CHECK(std::strlen(r) < 160);
  }
  { // remote_load_reverts_on_timeout
    Env e; RadioPresetCLI c; setup(c, e); c.loop(1000);
    run(c, "load preset 2", r);
    c.loop(1000 + 599000);
    CHECK(e.applied.size() == 1 && c.pending());
    c.loop(1000 + 600001);
    CHECK(e.applied.size() == 2 && near(e.applied[1].freq, 910.525f) && !c.pending());
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size())); CHECK(!u.pending.active);
    c.loop(1000 + 900000); CHECK(e.applied.size() == 2);
  }
  { // countdown_wraps_and_limits_flash_writes
    Env e; RadioPresetCLI c; setup(c, e); c.loop(0xFFFFFF00u);
    run(c, "load preset 2", r);
    int s = e.saves;
    uint32_t t = 0xFFFFFF00u;
    for (int i = 0; i < 30; i++) { t += 1000; c.loop(t); }   // 30 s, crosses the wrap
    CHECK(e.saves == s && c.pending());
    t += 31000; c.loop(t);                                    // 61 s: minute 10 -> 9
    CHECK(e.saves == s + 1);
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size()));
    CHECK(u.pending.active && u.pending.remaining_secs == 540);
    t += 538000; c.loop(t); CHECK(c.pending());
    t += 2000; c.loop(t); CHECK(!c.pending() && e.applied.size() == 2);
  }
  { // pending_survives_reload
    Env e; RadioPresetCLI c; setup(c, e); c.loop(1000);
    run(c, "load preset 2", r);
    size_t n = e.applied.size();
    RadioPresetCLI d; d.begin(hooks(e));
    CHECK(e.applied.size() == n + 1 && near(e.applied.back().freq, 910.525f) && !d.pending());
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size())); CHECK(!u.pending.active);
  }
  { // refusals
    Env e; RadioPresetCLI c; setup(c, e);
    e.tempRadio = true;
    int s = e.saves;
    CHECK(run(c, "load preset 2", r)); CHECK(has(r, "Error: tempradio active"));
    e.tempRadio = false;
    CHECK(run(c, "load preset 4", r)); CHECK(has(r, "Error: preset 4 empty"));
    CHECK(run(c, "load preset 1", r)); CHECK(has(r, "OK - already on USA-Canada"));
    CHECK(e.applied.empty() && e.saves == s && !c.pending());
    CHECK(run(c, "load preset 2", r)); CHECK(has(r, "OK"));
    s = e.saves;
    CHECK(run(c, "load preset 1", r)); CHECK(has(r, "Error: preset load pending"));
    CHECK(e.applied.size() == 1 && e.saves == s);
  }
  { // failures_roll_back
    Env e; RadioPresetCLI c; setup(c, e);
    e.failSave = true;
    CHECK(run(c, "load preset 2", r)); CHECK(has(r, "Error"));
    CHECK(e.applied.empty() && !c.pending());
    e.failSave = false; e.failApply = true;
    CHECK(run(c, "load preset 2", r)); CHECK(has(r, "Error: boom"));
    CHECK(!c.pending());
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size())); CHECK(!u.pending.active);
  }
  { // revert_apply_failure_keeps_pending_and_retries
    Env e; RadioPresetCLI c; setup(c, e); c.loop(1000);
    run(c, "load preset 2", r);
    e.failApplyCount = 1;
    c.loop(1000 + 600001);                       // revert attempt fails
    CHECK(c.pending() && e.applied.size() == 1);
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size())); CHECK(u.pending.active);
    c.loop(1000 + 600001 + 30000);               // too early to retry
    CHECK(c.pending() && e.applied.size() == 1);
    c.loop(1000 + 600001 + 61000);               // retry succeeds
    CHECK(!c.pending() && e.applied.size() == 2 && near(e.applied[1].freq, 910.525f));
    RadioPresetTable v; CHECK(v.decode(e.store.data(), e.store.size())); CHECK(!v.pending.active);
  }
  { // reboot_revert_failure_keeps_pending
    Env e; RadioPresetCLI c; setup(c, e); c.loop(1000);
    run(c, "load preset 2", r);
    e.failApplyCount = 1;
    RadioPresetCLI d; d.begin(hooks(e));
    CHECK(d.pending());
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size())); CHECK(u.pending.active);
    RadioPresetCLI f; f.begin(hooks(e));         // next boot retries and succeeds
    CHECK(!f.pending() && e.applied.size() == 2 && near(e.applied[1].freq, 910.525f));
    d.loop(5000); d.loop(5000 + 61000);          // in-session retry also works
    CHECK(!d.pending());
  }
  { // remote_load_refused_when_current_invalid
    Env e; RadioPresetCLI c; setup(c, e);
    e.cur.sf = 13;                               // live tuple the role would not accept
    int s = e.saves;
    CHECK(run(c, "load preset 2", r)); CHECK(has(r, "Error"));
    CHECK(e.applied.empty() && e.saves == s && !c.pending());
  }
  { // del_preset
    Env e; RadioPresetCLI c; setup(c, e);
    CHECK(run(c, "del preset 2", r)); CHECK(has(r, "OK"));
    run(c, "get preset 2", r); CHECK(std::strcmp(r, "2 -") == 0);
    CHECK(run(c, "del preset 7", r)); CHECK(has(r, "Error"));
  }
  { // begin_drops_invalid_slots
    Env e;
    RadioPresetTable t;
    t.set(1, "Primary", 910.525f, 62.5f, 7, 5, 0);
    t.set(2, "BadSf", 915.0f, 62.5f, 13, 5, 0);
    t.set(3, "NanFreq", NAN, 62.5f, 7, 5, 0);
    t.set(4, "Good", 868.0f, 125.0f, 8, 5, 0);
    uint8_t buf[RadioPresetTable::ENCODED_MAX];
    size_t n = t.encode(buf, sizeof(buf));
    e.store.assign(buf, buf + n); e.haveStore = true;
    RadioPresetCLI c; c.begin(hooks(e));
    CHECK(c.table().get(1) && !c.table().get(2) && !c.table().get(3) && c.table().get(4));
    RadioPresetTable u; CHECK(u.decode(e.store.data(), e.store.size()));
    CHECK(!u.get(2) && !u.get(3));
  }
  { // unrelated_commands_pass_through
    Env e; RadioPresetCLI c; setup(c, e);
    CHECK(!run(c, "get radio", r));
    CHECK(!run(c, "set radio 1,2,3,4", r));
    CHECK(!run(c, "get retry.preset", r));
    CHECK(!run(c, "get presets", r));
    CHECK(!run(c, "ver", r));
  }
  if (failures) return 1;
  std::printf("PASS cli\n");
  return 0;
}
'''


class RadioPresetsTest(unittest.TestCase):
    # ESP-IDF/newlib <limits.h> defines NAME_MAX as a macro. Predefine it on host
    # so any identifier collision with a platform macro fails here too.
    PLATFORM_MACROS = ('-DNAME_MAX=255',)

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
                *sanitize, *self.PLATFORM_MACROS,
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

    def test_seed_override_defines(self):
        harness = CLI_HARNESS.replace('int main() {', 'int main_unused_() {', 1) + r'''
int main() {
  char r[160];
  Env e; e.setCur(915.0f, 250.0f, 11, 5, 0);
  RadioPresetCLI c; c.begin(hooks(e));
  CHECK(run(c, "get preset 1", r));
  CHECK(has(r, "1 EU-Narrow 869.6"));
  CHECK(c.table().get(1) && std::strcmp(c.table().get(1)->name, "EU-Narrow") == 0);
  CHECK(near(c.table().get(1)->freq, 869.618f) && c.table().get(1)->sf == 8 && c.table().get(1)->cr == 8);
  if (failures) return 1;
  std::printf("PASS override\n");
  return 0;
}
'''
        self.PLATFORM_MACROS = self.PLATFORM_MACROS + (
            '-DRADIO_PRESET_DEFAULT_NAME="EU-Narrow"',
            '-DRADIO_PRESET_DEFAULT_FREQ=869.618f', '-DRADIO_PRESET_DEFAULT_BW=62.5f',
            '-DRADIO_PRESET_DEFAULT_SF=8', '-DRADIO_PRESET_DEFAULT_CR=8')
        self.build_and_run('cli_override', harness, 'PASS override',
                           extra_sources=('src/helpers/TxtDataHelpers.cpp',
                                          'src/helpers/RadioPresetCLI.cpp'))

    def test_preset_cli(self):
        self.build_and_run('cli', CLI_HARNESS, 'PASS cli',
                           extra_sources=('src/helpers/TxtDataHelpers.cpp',
                                          'src/helpers/RadioPresetCLI.cpp'))


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8', errors='replace')


def _function_body(text, signature):
    """Return the brace-balanced body that follows the first match of signature."""
    start = text.find(signature)
    if start < 0:
        return None
    brace = text.find('{', start)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
    return None


class RadioPresetWiringContractTest(unittest.TestCase):
    """Source contracts for the repeater integration (no firmware build here)."""

    def assertSourceMatches(self, text, pattern, msg=None):  # keep failures short: no file dumps
        self.assertTrue(text is not None and re.search(pattern, text), msg or ('no match: ' + pattern))

    def test_dispatch_presets_before_profiles(self):
        cpp = _read('src/helpers/CommonCLI.cpp')
        body = _function_body(cpp, 'void CommonCLI::handleCommand(')
        self.assertIsNotNone(body)
        assert body is not None
        presets = body.find('_radio_presets.handle(command, reply, 160, sender_timestamp != 0)')
        profiles = body.find('_radio_profiles.handle(')
        self.assertGreaterEqual(presets, 0, 'preset dispatch missing')
        self.assertGreaterEqual(profiles, 0)
        self.assertLess(presets, profiles, 'presets must be dispatched before profiles')

    def test_begin_at_end_of_load_prefs_and_loop_serviced(self):
        cpp = _read('src/helpers/CommonCLI.cpp')
        load = _function_body(cpp, 'void CommonCLI::loadPrefs(FILESYSTEM* fs)')
        self.assertIsNotNone(load)
        assert load is not None
        begin = load.find('_radio_presets.begin(')
        self.assertGreaterEqual(begin, 0, 'begin() missing from loadPrefs')
        self.assertGreater(begin, load.find('_radio_profiles.stagePrimary('),
                           'begin() must run after prefs are loaded and staged')
        loop = _function_body(cpp, 'void CommonCLI::loop()')
        self.assertSourceMatches(loop, r'_radio_presets\.loop\(millis\(\)\)')

    def test_hooks_use_existing_radio_paths(self):
        cpp = _read('src/helpers/CommonCLI.cpp')
        expect = {
            'bool CommonCLI::presetAccepts(': r'acceptsPrimaryRadioParams\(freq, bw, sf, cr, preamble\)',
            'bool CommonCLI::presetApply(':
                r'applyAndSavePrimaryRadioParams\(\s*p\.freq, p\.bw, p\.sf, p\.cr, p\.preamble',
            'bool CommonCLI::presetTempRadioActive(': r'_callbacks->isTempRadioActive\(\)',
            'bool CommonCLI::presetLoad(': r'loadRadioPresetFile\(',
            'bool CommonCLI::presetSave(': r'saveRadioPresetFile\(',
            'bool CommonCLI::presetCurrent(': r'primary_radio_preamble',
        }
        for signature, pattern in expect.items():
            body = _function_body(cpp, signature)
            self.assertIsNotNone(body, signature + ' missing')
            self.assertTrue(re.search(pattern, body), signature + ' does not use ' + pattern)
        header = _read('src/helpers/CommonCLI.h')
        self.assertSourceMatches(header, r'mesh::RadioPresetCLI\s+_radio_presets\s*;')
        self.assertSourceMatches(header, r'mesh::RadioPresetCLI&\s*radioPresets\(\)')

    def test_preset_file_helpers(self):
        h = _read('src/helpers/RadioPresetFile.h')
        self.assertIn('"/presets"', h)
        self.assertSourceMatches(h, r'bool\s+loadRadioPresetFile\(FILESYSTEM\*\s*fs,\s*uint8_t\*\s*buf,'
                            r'\s*size_t\s+cap,\s*size_t&\s*len\)')
        self.assertSourceMatches(h, r'bool\s+saveRadioPresetFile\(FILESYSTEM\*\s*fs,\s*const uint8_t\*\s*buf,'
                            r'\s*size_t\s+len\)')
        self.assertIn('AtomicFileWriter', h)
        self.assertIn('ContactFileTransaction', h)

    def test_preset_file_include_is_not_platform_guarded(self):
        # Every platform needs the helpers: the include must not sit inside a
        # platform-specific #if/#elif/#else branch in CommonCLI.cpp.
        stack = []
        found = False
        for line in _read('src/helpers/CommonCLI.cpp').splitlines():
            t = line.strip()
            if t.startswith('#if'):
                stack.append(t)
            elif t.startswith('#elif') and stack:
                stack[-1] = stack[-1] + ' ' + t
            elif t.startswith('#else') and stack:
                stack[-1] = stack[-1] + ' #else'
            elif t.startswith('#endif') and stack:
                stack.pop()
            elif t == '#include "RadioPresetFile.h"':
                found = True
                guarded = [c for c in stack if '_PLATFORM' in c]
                self.assertEqual(guarded, [], 'RadioPresetFile.h include is platform-guarded')
        self.assertTrue(found, 'CommonCLI.cpp must include RadioPresetFile.h')

    def test_repeater_login_commits_only_for_full_admin(self):
        cpp = _read('examples/simple_repeater/MyMesh.cpp')
        login = _function_body(cpp, 'uint8_t MyMesh::handleLoginReq(')
        self.assertIsNotNone(login)
        self.assertSourceMatches(login, r'login_full_admin\s*=\s*false\s*;',
                         'flag must be cleared on every attempt')
        self.assertSourceMatches(login, r'login_full_admin\s*=\s*client->isAdmin\(\)\s*;\s*\n\s*return 13;')
        self.assertSourceMatches(
            cpp,
            r'reply_len\s*=\s*handleLoginReq\([^;]*;\s*\n(?:\s*//[^\n]*\n)*\s*if\s*\(reply_len\s*>\s*4\s*'
            r'&&\s*reply_data\[4\]\s*==\s*RESP_SERVER_LOGIN_OK\s*&&\s*login_full_admin\)\s*\n?\s*'
            r'_cli\.radioPresets\(\)\.onAdminLogin\(\);')
        self.assertEqual(cpp.count('onAdminLogin()'), 1)
        header = _read('examples/simple_repeater/MyMesh.h')
        self.assertSourceMatches(header, r'bool\s+login_full_admin\s*=\s*false\s*;')

    def test_room_server_and_sensor_login_commit_for_strict_admin(self):
        room = _read('examples/simple_room_server/MyMesh.cpp')
        anon = _function_body(room, 'void MyMesh::onAnonDataRecv(')
        self.assertIsNotNone(anon)
        assert anon is not None
        ok = anon.find('reply_data[4] = RESP_SERVER_LOGIN_OK;')
        commit = anon.find('if (client->isAdmin()) _cli.radioPresets().onAdminLogin();')
        self.assertGreaterEqual(ok, 0)
        self.assertGreater(commit, ok, 'room server must commit after a successful login')
        self.assertEqual(room.count('onAdminLogin()'), 1)
        sensor = _read('examples/simple_sensor/SensorMesh.cpp')
        login = _function_body(sensor, 'uint8_t SensorMesh::handleLoginReq(')
        self.assertIsNotNone(login)
        self.assertSourceMatches(login, r'RESP_SERVER_LOGIN_OK[\s\S]*if \(client->isAdmin\(\)\) '
                                r'_cli\.radioPresets\(\)\.onAdminLogin\(\);\s*\n\s*return 13;')
        self.assertEqual(sensor.count('onAdminLogin()'), 1)
        docs = _read('docs/cli_commands.md')
        self.assertNotIn('remote load always reverts', docs)

    def test_manager_roles_cannot_use_presets(self):
        cpp = _read('examples/simple_repeater/MyMesh.cpp')
        for name in ('static bool isRegionMgrAllowed(', 'static bool isFilterMgrAllowed('):
            body = _function_body(cpp, name)
            self.assertIsNotNone(body, name)
            self.assertNotIn('preset', body, name)

    def test_docs_list_preset_commands(self):
        docs = _read('docs/cli_commands.md')
        avail = _read('docs/cli_command_availability.md')
        for cmd in ('get preset', 'add preset', 'load preset', 'del preset'):
            self.assertTrue(cmd in docs, cmd + ' missing from cli_commands.md')
            self.assertTrue(cmd in avail, cmd + ' missing from cli_command_availability.md')


def _public_members(header, class_head):
    """Return the text of the public: sections of the class that starts at class_head."""
    start = header.find(class_head)
    if start < 0:
        return ''
    body = _function_body(header, class_head) or ''
    out, access = [], 'private'
    for line in body.splitlines():
        t = line.strip()
        if t in ('public:', 'protected:', 'private:'):
            access = t[:-1]
        elif access == 'public':
            out.append(line)
    return '\n'.join(out)


class CompanionPresetContractTest(unittest.TestCase):
    """Source contracts for the companion (T114, Cardputer) integration."""

    CPP = 'examples/companion_radio/MyMesh.cpp'
    H = 'examples/companion_radio/MyMesh.h'

    def assertSourceMatches(self, text, pattern, msg=None):
        self.assertTrue(text is not None and re.search(pattern, text), msg or ('no match: ' + pattern))

    def test_dispatch_is_local_only_and_before_profiles(self):
        body = _function_body(_read(self.CPP), 'bool MyMesh::handleCommand(')
        self.assertIsNotNone(body)
        assert body is not None
        call = '_radio_presets.handle(command, reply, reply_capacity, false)'
        presets = body.find(call)
        profiles = body.find('_radio_profiles.handle(')
        self.assertGreaterEqual(presets, 0, 'companion preset dispatch missing (literal false)')
        self.assertLess(presets, profiles, 'presets must be dispatched before profiles')
        self.assertEqual(body.count('_radio_presets.handle('), 1)
        # Authorized LoRa CLI reaches handleCommand with a nonzero timestamp;
        # companions have no revert path, so presets stay local-only.
        self.assertSourceMatches(body, r'sender_timestamp\s*==\s*0\s*&&\s*' + re.escape(call))

    def test_public_api(self):
        public = _public_members(_read(self.H), 'class MyMesh :')
        self.assertSourceMatches(public, r'bool\s+loadRadioPreset\(uint8_t\s+slot,\s*char\*\s*reply,'
                                 r'\s*size_t\s+cap\)')
        self.assertSourceMatches(public, r'uint8_t\s+activeRadioPreset\(\)\s*const')
        self.assertSourceMatches(public, r'const\s+mesh::RadioPresetTable&\s+radioPresetTable\(\)\s*const')
        self.assertSourceMatches(_read(self.H), r'mesh::RadioPresetCLI\s+_radio_presets\s*;')

    def test_begin_after_profiles_and_loop_every_pass(self):
        cpp = _read(self.CPP)
        begin = _function_body(cpp, 'void MyMesh::begin(bool has_display')
        self.assertIsNotNone(begin)
        assert begin is not None
        b = begin.find('_radio_presets.begin(')
        self.assertGreaterEqual(b, 0, 'begin() missing from MyMesh::begin')
        self.assertEqual(begin.count('_radio_presets.begin('), 1)
        # A pending record left by an infrastructure role reverts in begin()
        # and saves _prefs, so prefs must be loaded, HIL-adjusted and
        # sanitised first, and the radio not yet configured from them.
        anchors = ('_radio_profiles.begin(_store->getPrimaryFS()',
                   '_store->loadPrefs(_prefs,',
                   'applyHilStartupFrequency(_prefs.freq)',
                   '_prefs.cr = constrain(_prefs.cr, 5, 8);')
        for anchor in anchors:
            pos = begin.find(anchor)
            self.assertGreaterEqual(pos, 0, anchor + ' missing from MyMesh::begin')
            self.assertGreater(b, pos, 'presets begin() must come after ' + anchor)
        configure = begin.find('configureRadioFromPrefs();')
        self.assertGreater(configure, b, 'presets begin() must precede configureRadioFromPrefs()')
        loop = _function_body(cpp, 'void MyMesh::loop()')
        self.assertIsNotNone(loop)
        assert loop is not None
        self.assertSourceMatches(loop, r'\n\s*_radio_presets\.loop\(millis\(\)\);')

    def test_hooks_use_existing_radio_paths(self):
        cpp = _read(self.CPP)
        expect = {
            'bool MyMesh::presetAccepts(': r'_radio_profiles\.acceptsPrimary\(freq, bw, sf, cr, preamble\)',
            'bool MyMesh::presetApply(':
                r'applyAndSaveRadioParams\(\s*p\.freq, p\.bw, p\.sf, p\.cr,\s*'
                r'self->_prefs\.client_repeat, p\.preamble, false\)',
            'bool MyMesh::presetCurrent(': r'_radio_profiles\.primaryPreamble\(\)',
            'bool MyMesh::presetLoad(': r'loadRadioPresetFile\(\s*self->_store->getPrimaryFS\(\)',
            'bool MyMesh::presetSave(': r'saveRadioPresetFile\(\s*self->_store->getPrimaryFS\(\)',
            'bool MyMesh::presetTempRadioActive(': r'isTempRadioActive\(\)',
        }
        for signature, pattern in expect.items():
            body = _function_body(cpp, signature)
            self.assertIsNotNone(body, signature + ' missing')
            self.assertTrue(re.search(pattern, body), signature + ' does not use ' + pattern)
        apply = _function_body(cpp, 'bool MyMesh::presetApply(') or ''
        for err in ('"OK"', '"Error: radio not saved"', '"Error: radio save unconfirmed"',
                    '"Error: radio rejected"'):
            self.assertIn(err, apply)

    def test_preset_file_include_is_not_platform_guarded(self):
        depth_guards = []
        found = False
        for line in _read(self.CPP).splitlines():
            t = line.strip()
            if t.startswith('#if'):
                depth_guards.append(t)
            elif t.startswith('#endif') and depth_guards:
                depth_guards.pop()
            elif t == '#include <helpers/RadioPresetFile.h>':
                found = True
                self.assertEqual(depth_guards, [], 'RadioPresetFile.h include is guarded')
        self.assertTrue(found, 'companion MyMesh.cpp must include RadioPresetFile.h')

    def test_docs_list_companion_presets(self):
        avail = _read('docs/cli_command_availability.md')
        start = avail.find('## Companion framed CLI')
        self.assertGreaterEqual(start, 0, 'Companion framed CLI heading missing')
        section = avail[start:avail.find('\n## ', start + 1)]
        self.assertIn('load preset', section)


if __name__ == '__main__':
    unittest.main()
