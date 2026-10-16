"""Compile actual timing policy with an opt-in Meshtext RF dwell experiment.

No PlatformIO or hardware is used. Default builds retain the measured 4.6-symbol
policy; the HIL override changes only Meshtext, never MeshCore or Ethernet.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <RadioProfiles.h>
#include <cstdio>
#include <initializer_list>

int main() {
  mesh::RadioProfileParams p;
  p.freq = 909.5f; p.bw = 500; p.sf = 5; p.cr = 5;
  for (auto protocol : {mesh::RadioProfileProtocol::MeshCore,
                        mesh::RadioProfileProtocol::Meshtext,
                        mesh::RadioProfileProtocol::Ethernet}) {
    p.protocol = protocol;
    std::printf("%lu %lu\n",
        static_cast<unsigned long>(mesh::RadioProfiles::minimumListenUs(p, false)),
        static_cast<unsigned long>(mesh::RadioProfiles::minimumListenUs(p, true)));
  }
  // Existing invalid-modem rejection must remain intact with the override.
  p.protocol = mesh::RadioProfileProtocol::Meshtext; p.bw = 0;
  std::printf("%lu\n", static_cast<unsigned long>(mesh::RadioProfiles::minimumListenUs(p)));
}
'''


class MeshtextListenHilTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a native C++17 compiler is required")

    def compile_case(self, macro=None):
        temporary = tempfile.TemporaryDirectory(prefix="meshtext-listen-hil-")
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        source = folder / "listen.cpp"
        binary = folder / ("listen.exe" if os.name == "nt" else "listen")
        source.write_text(HARNESS, encoding="utf-8")
        command = [self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                   "-I", str(ROOT / "src"), str(source), "-o", str(binary)]
        if macro is not None:
            command.append("-DMESH_HIL_MESHTEXT_LISTEN_SYMBOLS=" + str(macro))
        result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
        return result, binary

    def run_case(self, macro=None):
        compiled, binary = self.compile_case(macro)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [tuple(map(int, line.split())) for line in result.stdout.splitlines()]

    def test_default_keeps_all_protocols_at_4_6_symbols(self):
        # SF5/BW500 = 64 us/symbol; ceil(4.6 * 64) = 295 us.
        self.assertEqual(self.run_case(), [(295, 295), (295, 295), (295, 295), (0,)])

    def test_twelve_symbol_hil_changes_only_meshtext_to_768_us(self):
        self.assertEqual(self.run_case(12), [(295, 295), (768, 768), (295, 295), (0,)])

    def test_smaller_override_cannot_shorten_the_existing_listen_window(self):
        self.assertEqual(self.run_case(1), [(295, 295), (295, 295), (295, 295), (0,)])

    def test_fractional_override_is_supported_and_does_not_change_other_protocols(self):
        self.assertEqual(self.run_case(8.5), [(295, 295), (544, 544), (295, 295), (0,)])

    def test_upper_bound_is_accepted_for_meshtext_only(self):
        self.assertEqual(self.run_case(64), [(295, 295), (4096, 4096), (295, 295), (0,)])

    def test_zero_negative_and_over_limit_macros_are_compile_time_errors(self):
        for macro in (0, -1, 65):
            with self.subTest(macro=macro):
                compiled, _ = self.compile_case(macro)
                self.assertNotEqual(compiled.returncode, 0)
                self.assertIn("MESH_HIL_MESHTEXT_LISTEN_SYMBOLS must be greater than 0 and at most 64",
                              compiled.stderr)


if __name__ == "__main__":
    unittest.main()
