"""Check production beacon file helpers against the Adafruit constructor API."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/BeaconManager.cpp"

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
enum { FILE_O_READ = 0, FILE_O_WRITE = 1 };
struct Filesystem {
  bool present = false, metadata_ok = true, remove_ok = true, open_ok = true;
  unsigned removals = 0, opens = 0;
  uint8_t last_mode = 255;
  bool remove(const char* path) {
    assert(!std::strcmp(path, "/test")); ++removals;
    if (!remove_ok) return false;
    present = false; return true;
  }
};
#define FILESYSTEM Filesystem
struct File {
  Filesystem* fs;
  File() = delete; // Both nRF52 and STM32 Adafruit File require an FS.
  explicit File(Filesystem& owner) : fs(&owner) {}
  File(const File&) = delete;
  File(File&&) = default;
  File& operator=(const File&) = delete;
  explicit operator bool() const { return fs && fs->open_ok; }
  bool open(const char* path, uint8_t mode) {
    assert(!std::strcmp(path, "/test")); ++fs->opens; fs->last_mode = mode;
    return fs->open_ok;
  }
};
bool filePresence(Filesystem* fs, const char* path, bool& present) {
  assert(!std::strcmp(path, "/test"));
  present = fs->present; return fs->metadata_ok;
}
@METHODS@
int main() {
  Filesystem fs;
  auto file = makeFile(&fs);
  assert(file.fs == &fs);
  assert(openRead(&fs, "/test", file) && fs.opens == 1 && fs.last_mode == FILE_O_READ);
  assert(openWrite(&fs, "/test", file) && fs.opens == 2 && !fs.removals && fs.last_mode == FILE_O_WRITE);
  fs.present = true;
  assert(openWrite(&fs, "/test", file) && fs.opens == 3 && fs.removals == 1 && !fs.present);
  fs.metadata_ok = false;
  assert(!openWrite(&fs, "/test", file) && fs.opens == 3 && fs.removals == 1);
  fs.metadata_ok = true; fs.present = true; fs.remove_ok = false;
  assert(!openWrite(&fs, "/test", file) && fs.opens == 3 && fs.removals == 2);
  fs.present = false; fs.open_ok = false;
  assert(!openRead(&fs, "/test", file) && fs.opens == 4 && fs.last_mode == FILE_O_READ);
  assert(!openWrite(&fs, "/test", file) && fs.opens == 5 && fs.last_mode == FILE_O_WRITE);
}
'''


class BeaconFileApiTest(unittest.TestCase):
    def helper_source(self):
        source = SOURCE.read_text(encoding="ascii")
        return "\n".join(method(source, signature) for signature in
                         ("File makeFile(", "bool openRead(", "bool openWrite("))

    def compile_fixture(self, methods, platform, negative=False):
        with tempfile.TemporaryDirectory(prefix="beacon-file-api-") as folder:
            cpp, exe = Path(folder) / "test.cpp", Path(folder) / "test"
            cpp.write_text(HARNESS.replace("@METHODS@", methods), encoding="ascii")
            flags = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []
            result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                     "-D" + platform, *flags, str(cpp), "-o", str(exe)], capture_output=True, text=True)
            if negative:
                self.assertNotEqual(result.returncode, 0, "default-File regression unexpectedly compiled")
                self.assertIn("deleted", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                ran = subprocess.run([str(exe)], capture_output=True, text=True)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_stm32_and_nrf52_use_bound_file_objects_and_open_modes(self):
        source = self.helper_source()
        for platform in ("STM32_PLATFORM", "NRF52_PLATFORM"):
            with self.subTest(platform=platform):
                self.compile_fixture(source, platform)

    def test_negative_control_stm32_default_file_constructor_is_rejected(self):
        source = self.helper_source()
        condition = "#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)"
        self.assertEqual(source.count(condition), 3)
        # Reproduce only the old makeFile branch. Read/write remain correct,
        # proving this check specifically catches the missing constructor.
        self.compile_fixture(source.replace(condition, "#if defined(NRF52_PLATFORM)", 1), "STM32_PLATFORM", negative=True)


if __name__ == "__main__":
    unittest.main()
