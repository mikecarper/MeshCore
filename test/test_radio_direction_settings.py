"""Exercise the saved LoRa direction gate and real publication failures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "RadioDirectionSettings.h"
#include "settings_under_test.cpp"

int main() {
  using namespace mesh::radioDirection;
  // Uninitialized users such as Companion retain their existing direction.
  assert(mode() == Mode::Both && radioReceiveAllowed() && radioTransmitAllowed());
  MemoryFS fs;
  const char* path = "/radio_mode";
  const std::vector<uint8_t> legacy = {9, 7, 5, 3};
  fs.files["/com_prefs"] = legacy;
  loadDirectionSettings(&fs);
  assert(mode() == Mode::Both && fs.files.size() == 1);
  char reply[160];
  auto run = [&](const char* command) {
    strcpy(reply, "untouched");
    return handleDirectionCommand(command, reply, sizeof(reply));
  };
  assert(run("get radio.mode") && !strcmp(reply, "> both"));
  assert(!run("get radio.model") && !strcmp(reply, "untouched"));

  struct Choice { const char* text; Mode value; bool rx, tx; };
  for (const Choice& choice : {Choice{"rx", Mode::Rx, true, false},
       Choice{"tx", Mode::Tx, false, true}, Choice{"off", Mode::Off, false, false},
       Choice{"both", Mode::Both, true, true}}) {
    char command[32];
    snprintf(command, sizeof(command), "set radio.mode %s", choice.text);
    assert(run(command) && !strncmp(reply, "OK", 2));
    assert(mode() == choice.value);
    assert(radioReceiveAllowed() == choice.rx && radioTransmitAllowed() == choice.tx);
    loadDirectionSettings(&fs);
    assert(mode() == choice.value);
    assert(radioReceiveAllowed() == choice.rx && radioTransmitAllowed() == choice.tx);
    char expected[16]; snprintf(expected, sizeof(expected), "> %s", choice.text);
    assert(run("get radio.mode") && !strcmp(reply, expected));
  }
  assert(run("set radio.mode\trx") && !strncmp(reply, "OK", 2));
  const auto good = fs.files.at(path);
  assert(good.size() == 10 && !memcmp(good.data(), "RDM\1", 4));
  assert(good[4] == 1 && good[5] == 0xfe);
  for (const char* invalid : {"set radio.mode", "set radio.mode receive",
       "set radio.mode Rx", "set radio.mode rxtx", "set radio.mode txjunk",
       "set radio.mode rx trailing", "get radio.mode extra"}) {
    assert(run(invalid) && !strncmp(reply, "Error:", 6));
    assert(mode() == Mode::Rx && fs.files.at(path) == good);
  }
  assert(!run("set radio.mode2 off"));
  assert(!handleDirectionCommand(nullptr, reply, sizeof(reply)));
  assert(!handleDirectionCommand("set radio.mode off", nullptr, sizeof(reply)));
  assert(!handleDirectionCommand("set radio.mode off", reply, 0));
  char bounded[] = {'x', 'x', 'x', 'z'};
  assert(handleDirectionCommand("get radio.mode", bounded, 3));
  assert(bounded[2] == 0 && bounded[3] == 'z');

  // A known failed save must keep both persisted and effective directions.
  fs.fail_write = true;
  assert(run("set radio.mode tx") && !strncmp(reply, "Error:", 6));
  assert(mode() == Mode::Rx && fs.files.at(path) == good);
  fs.fail_write = false;
  fs.fail_write_after = 4;
  assert(run("set radio.mode off") && !strncmp(reply, "Error:", 6));
  assert(mode() == Mode::Rx && fs.files.at(path) == good);
  fs.fail_write_after = -1;
  assert(run("set radio.mode rx") && !strncmp(reply, "OK", 2));

  // Publication uncertainty cannot enable LoRa TX or silently restore both.
  fs.fail_after_publication = true;
  assert(run("set radio.mode tx") && !strncmp(reply, "Error:", 6));
  assert(mode() == Mode::Off && !radioReceiveAllowed() && !radioTransmitAllowed());
  const auto published = fs.files.at(path);
  assert(published != good);
  fs.fail_after_publication = false; fs.fail_read_open = false;
  assert(run("set radio.mode both") && !strncmp(reply, "Error:", 6));
  assert(mode() == Mode::Off && fs.files.at(path) == published);
  loadDirectionSettings(&fs);
  assert(mode() == Mode::Tx && !radioReceiveAllowed() && radioTransmitAllowed());
  assert(run("set radio.mode rx") && !strncmp(reply, "OK", 2));

  // Corruption is distinguishable from deliberately saved off, and explicit
  // repair is allowed only when the record's existence/read was determinate.
  for (size_t bit = 0; bit < good.size() * 8; ++bit) {
    fs.files[path] = good;
    fs.files[path][bit / 8] ^= uint8_t(1U << (bit % 8));
    loadDirectionSettings(&fs);
    assert(mode() == Mode::Off && !radioReceiveAllowed() && !radioTransmitAllowed());
    assert(run("get radio.mode") && strstr(reply, "invalid or unreadable"));
  }
  assert(run("set radio.mode rx") && !strncmp(reply, "OK", 2));
  for (size_t size = 0; size < good.size(); ++size) {
    fs.files[path] = std::vector<uint8_t>(good.begin(), good.begin() + size);
    loadDirectionSettings(&fs);
    assert(mode() == Mode::Off);
  }
  assert(run("set radio.mode rx") && !strncmp(reply, "OK", 2));
  fs.files[path].push_back(0);
  loadDirectionSettings(&fs); assert(mode() == Mode::Off);
  assert(run("set radio.mode off") && !strncmp(reply, "OK", 2));
  assert(run("get radio.mode") && !strcmp(reply, "> off"));
  assert(run("set radio.mode rx") && !strncmp(reply, "OK", 2));

  fs.fail_read_open = true;
  loadDirectionSettings(&fs); assert(mode() == Mode::Off);
  fs.fail_read_open = false;
  assert(run("set radio.mode both") && !strncmp(reply, "Error:", 6));
  assert(fs.files.at(path) == good);
  loadDirectionSettings(&fs); assert(mode() == Mode::Rx);
  fs.fail_read_after = 4;
  loadDirectionSettings(&fs); assert(mode() == Mode::Off);
  fs.fail_read_after = -1;
  assert(run("set radio.mode both") && !strncmp(reply, "Error:", 6));
  loadDirectionSettings(&fs); assert(mode() == Mode::Rx);
  fs.fail_metadata = true;
  loadDirectionSettings(&fs); assert(mode() == Mode::Off);
  fs.fail_metadata = false;
  assert(run("set radio.mode both") && !strncmp(reply, "Error:", 6));
  loadDirectionSettings(&fs); assert(mode() == Mode::Rx);

  fs.files["/radio_mode.bak"] = good; fs.files.erase(path);
  loadDirectionSettings(&fs);
  assert(mode() == Mode::Rx && fs.files.at(path) == good);
  fs.files["/radio_mode.bak"] = good; fs.files.erase(path);
  fs.fail_rename = 1;
  loadDirectionSettings(&fs); assert(mode() == Mode::Off);
  assert(fs.files.at("/radio_mode.bak") == good);
  assert(run("set radio.mode tx") && !strncmp(reply, "Error:", 6));
  loadDirectionSettings(&fs); assert(mode() == Mode::Rx);
  loadDirectionSettings(nullptr);
  assert(mode() == Mode::Off && !radioReceiveAllowed() && !radioTransmitAllowed());
  assert(run("set radio.mode both") && !strncmp(reply, "Error:", 6));
  assert(fs.files.at("/com_prefs") == legacy);
}
'''


class RadioDirectionSettingsTests(unittest.TestCase):
    def test_saved_modes_and_real_storage_faults(self):
        with tempfile.TemporaryDirectory(prefix="radio-direction-settings-") as temporary:
            work = Path(temporary)
            (work / "helpers").mkdir()
            mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
            mock = mock.replace("  bool fail_read_open = false;",
                "  bool fail_read_open = false;\n"
                "  bool fail_after_publication = false;\n  bool fail_metadata = false;")
            mock = mock.replace("    files[to] = files[from]; files.erase(from); return true;",
                "    files[to] = files[from]; files.erase(from);\n"
                "    if (fail_after_publication && !strcmp(to, \"/radio_mode\")) fail_read_open = true;\n"
                "    return true;")
            (work / "IdentityStore.h").write_text(mock)
            (work / "helpers/IdentityStore.h").write_text('#include "../IdentityStore.h"\n')
            (work / "FilePresence.h").write_text(r'''
#pragma once
#include "IdentityStore.h"
namespace mesh {
template <typename FS> bool filePresence(FS* fs, const char* path, bool& present) {
  if (!fs || !path || fs->fail_metadata) return false;
  present = fs->exists(path); return true;
}
}
''')
            for name in ("RadioDirectionSettings.h", "RadioDirectionPolicy.h",
                         "CheckedSettingsFile.h", "ContactFileTransaction.h"):
                (work / name).write_bytes((ROOT / "src/helpers" / name).read_bytes())
            (work / "settings_under_test.cpp").write_bytes(
                (ROOT / "src/helpers/RadioDirectionSettings.cpp").read_bytes())
            source, executable = work / "test.cpp", work / "test"
            source.write_text(HARNESS)
            built = subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-O1",
                "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DESP32_PLATFORM=1", "-I" + str(work), "-I" + str(ROOT / "src"),
                "-I" + str(ROOT / "src/helpers"), str(source), "-o", str(executable)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_other_platforms_have_both_directions_without_storage(self):
        with tempfile.TemporaryDirectory(prefix="radio-direction-default-") as temporary:
            work = Path(temporary)
            source, executable, obj = work / "test.cpp", work / "test", work / "settings.o"
            source.write_text('''
#include <cassert>
#include "RadioDirectionPolicy.h"
int main() {
  using namespace mesh::radioDirection;
  assert(mode() == Mode::Both && radioReceiveAllowed() && radioTransmitAllowed());
}
''')
            for platform in ("NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM"):
                flags = ["-std=c++17", "-D" + platform, "-I" + str(ROOT / "src/helpers")]
                subprocess.run([os.environ.get("CXX", "c++"), *flags,
                    str(source), "-o", str(executable)], check=True, capture_output=True)
                subprocess.run([str(executable)], check=True, capture_output=True)
                subprocess.run([os.environ.get("CXX", "c++"), *flags, "-c",
                    str(ROOT / "src/helpers/RadioDirectionSettings.cpp"), "-o", str(obj)],
                    check=True, capture_output=True)
                symbols = subprocess.check_output(["nm", "-C", str(obj)], text=True)
                for name in ("settings_fs", "selected", "writable", "valid", "loadDirectionSettings", "handleDirectionCommand"):
                    self.assertNotIn(name, symbols)


if __name__ == "__main__":
    unittest.main()
