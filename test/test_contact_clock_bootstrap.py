#!/usr/bin/env python3
"""Run the actual contact-clock bootstrap against poisoned timestamps."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <vector>
struct Clock {
  uint32_t now;
  unsigned reads = 0, writes = 0;
  uint32_t getCurrentTime() { ++reads; return now; }
  void setCurrentTime(uint32_t value) { ++writes; now = value; }
};
struct BaseChatMesh {
  struct Contact { uint32_t lastmod; };
  std::vector<Contact> contacts;
  int num_contacts = 0;
  Clock clock;
  BaseChatMesh(uint32_t now, std::initializer_list<uint32_t> times) : clock{now} {
    for (auto time : times) contacts.push_back({time});
    num_contacts = contacts.size();
  }
  Clock* getRTCClock() { return &clock; }
  void bootstrapRTCfromContacts();
};
@METHODS@
static void check(uint32_t now, std::initializer_list<uint32_t> times,
                  uint32_t expected, unsigned writes) {
  BaseChatMesh mesh(now, times);
  mesh.bootstrapRTCfromContacts();
  assert(mesh.clock.now == expected && mesh.clock.writes == writes);
  assert(mesh.clock.reads == 1);
}
int main() {
  const uint32_t baseline = 1772323200U; // 1 March 2026: nRF52 fallback clock.
  const uint32_t plausible = baseline + 86400;
  const uint32_t corrupt = 3944678400U; // 1 January 2095.
  check(baseline, {}, baseline, 0);
  check(baseline, {0, UINT32_MAX, corrupt}, baseline, 0);
  check(baseline, {plausible, corrupt, UINT32_MAX}, plausible + 1, 1);
  check(baseline, {corrupt, UINT32_MAX, plausible}, plausible + 1, 1);
  check(plausible + 1, {baseline, plausible}, plausible + 1, 0);
  const uint32_t limit = contactClockBootstrapLimit(0);
  assert(limit == 1893456000U); // 1 January 2030, four years past the fixed build year.
  check(baseline, {limit, limit + 1, UINT32_MAX - 1}, baseline, 0);
  check(baseline, {limit - 1}, limit, 1);
  // An old build with an already-set RTC keeps accepting near-current contacts.
  const uint32_t old_installation = limit + 5 * 365U * 86400U;
  check(old_installation, {old_installation + 86400}, old_installation + 86401, 1);
  check(old_installation, {old_installation + 2 * 366U * 86400U}, old_installation, 0);
  assert(contactClockBootstrapLimit(UINT32_MAX - 10) == UINT32_MAX);
  check(UINT32_MAX - 10, {UINT32_MAX, 0, plausible}, UINT32_MAX - 10, 0);
  puts("contact clock corruption, boundary and long-lived firmware checks passed");
}
'''


class ContactClockBootstrapTests(unittest.TestCase):
    def test_production_bootstrap(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        source = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static uint32_t contactClockBootstrapLimit(",
            "void BaseChatMesh::bootstrapRTCfromContacts()"))
        with tempfile.TemporaryDirectory(prefix="contact-clock-bootstrap-") as directory:
            work = Path(directory)
            (work / "test.cpp").write_text(HARNESS.replace("@METHODS@", methods))
            binary = work / "bootstrap"
            cmd = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                   '-D__DATE__="Oct  9 2026"', "-Wno-builtin-macro-redefined",
                   str(work / "test.cpp"), "-o", str(binary)]
            if sys.platform.startswith("linux"):
                cmd[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-pie", "-no-pie"]
            built = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn("long-lived firmware checks passed", tested.stdout)


if __name__ == "__main__":
    unittest.main()
