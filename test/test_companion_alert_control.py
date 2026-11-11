"""Run the Companion alert CLI against storage and live-output fixtures."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def alert_handler():
    source = (ROOT / "examples/companion_radio/Notifications.cpp").read_text()
    match = re.search(r"\bbool\s+MyMesh::handleAlertCommand\([^)]*\)\s*\{", source)
    if not match:
        raise AssertionError("production Companion alert handler is missing")
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


STUBS = r"""
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <initializer_list>
struct Prefs {
  uint8_t buzzer_quiet = 1, vibe_quiet = 0;
  uint32_t unrelated = 0x12345678;
};
struct UI {
  Prefs* prefs;
  Prefs* persisted;
  unsigned syncs = 0, melodies = 0;
  bool sound_quiet = true, vibration_quiet = false;
  UI(Prefs* p, Prefs* saved) : prefs(p), persisted(saved) {}
  void syncAlertPreferences() {
    assert(prefs->buzzer_quiet == persisted->buzzer_quiet);
    assert(prefs->vibe_quiet == persisted->vibe_quiet);
    ++syncs;
    sound_quiet = prefs->buzzer_quiet;
    vibration_quiet = prefs->vibe_quiet;
  }
  void notificationMelody(const char* text) {
    assert(!sound_quiet && !prefs->buzzer_quiet && !persisted->buzzer_quiet);
    assert(text && !strncmp(text, "SoundOn:", 8));
    ++melodies;
  }
};
class MyMesh {
public:
  Prefs _prefs, saved;
  UI ui{&_prefs, &saved};
  UI* _ui = &ui;
  unsigned saves = 0;
  bool save_ok = true;
  bool savePrefs() {
    ++saves;
    if (!save_ok) return false;
    saved = _prefs;
    return true;
  }
  bool handleAlertCommand(const char*, char*, size_t);
};
"""


SCENARIOS = r"""
std::string run(MyMesh& mesh, const char* command) {
  char reply[160] = {};
  assert(mesh.handleAlertCommand(command, reply, sizeof(reply)));
  return reply;
}
void error(MyMesh& mesh, const char* command) {
  const auto before = mesh._prefs;
  const unsigned saves = mesh.saves, syncs = mesh.ui.syncs, melodies = mesh.ui.melodies;
  assert(run(mesh, command).find("Error") == 0);
  assert(mesh._prefs.buzzer_quiet == before.buzzer_quiet);
  assert(mesh._prefs.vibe_quiet == before.vibe_quiet);
  assert(mesh.saves == saves && mesh.ui.syncs == syncs);
  assert(mesh.ui.melodies == melodies);
}
void supported(MyMesh& mesh, const char* name, bool initial_on) {
  const std::string get = std::string("get ") + name;
  const std::string set = std::string("set ") + name;
  const bool sound = std::string(name) == "buzzer";
  const uint8_t other = sound ? mesh._prefs.vibe_quiet : mesh._prefs.buzzer_quiet;
  assert(run(mesh, get.c_str()) == (initial_on ? "> on" : "> off"));
  assert(mesh.saves == 0 && mesh.ui.syncs == 0);
  assert(mesh.ui.melodies == 0);
  for (const char* value : {"", " ", "on extra", "off extra", "1", "0", "ON", "OFF",
                            "true", "false", "onward", "officer"}) {
    error(mesh, (set + " " + value).c_str());
  }
  error(mesh, set.c_str());
  error(mesh, (get + " on").c_str());

  // An unchanged value acknowledges the command without rewriting flash.
  mesh.ui.sound_quiet = false;
  mesh.ui.vibration_quiet = true;
  assert(run(mesh, (set + (initial_on ? " on" : " off")).c_str()) == "OK");
  assert(mesh.saves == 0 && mesh.ui.syncs == 1);
  assert(mesh.ui.sound_quiet == bool(mesh._prefs.buzzer_quiet));
  assert(mesh.ui.vibration_quiet == bool(mesh._prefs.vibe_quiet));
  assert(mesh.ui.melodies == 0);

  // Persist first, then apply to the running UI. Changing one output must not
  // overwrite the independent preference or unrelated node settings.
  assert(run(mesh, (set + (initial_on ? " off" : " on")).c_str()) == "OK");
  assert(mesh.saves == 1 && mesh.ui.syncs == 2);
  assert(run(mesh, get.c_str()) == (initial_on ? "> off" : "> on"));
  assert((sound ? mesh._prefs.buzzer_quiet : mesh._prefs.vibe_quiet) == initial_on);
  assert((sound ? mesh._prefs.vibe_quiet : mesh._prefs.buzzer_quiet) == other);
  assert(mesh.saved.buzzer_quiet == mesh._prefs.buzzer_quiet);
  assert(mesh.saved.vibe_quiet == mesh._prefs.vibe_quiet);
  assert(mesh.saved.unrelated == 0x12345678);
  assert(mesh.ui.sound_quiet == bool(mesh._prefs.buzzer_quiet));
  assert(mesh.ui.vibration_quiet == bool(mesh._prefs.vibe_quiet));
  assert(mesh.ui.melodies == (sound ? 1u : 0u));

  const Prefs previous = mesh._prefs, persisted = mesh.saved;
  mesh.save_ok = false;
  assert(run(mesh, (set + (initial_on ? " on" : " off")).c_str()).find("Error") == 0);
  assert(mesh.saves == 2 && mesh.ui.syncs == 2);
  assert(mesh._prefs.buzzer_quiet == previous.buzzer_quiet);
  assert(mesh._prefs.vibe_quiet == previous.vibe_quiet);
  assert(mesh.saved.buzzer_quiet == persisted.buzzer_quiet);
  assert(mesh.saved.vibe_quiet == persisted.vibe_quiet);
  assert(run(mesh, get.c_str()) == (initial_on ? "> off" : "> on"));
  assert(mesh.ui.melodies == (sound ? 1u : 0u));

  // Explicitly enabling an already enabled buzzer is an audible test without
  // another storage write, even when storage would reject a changed setting.
  if (sound) {
    assert(run(mesh, "set buzzer on") == "OK");
    assert(mesh.saves == 2 && mesh.ui.syncs == 3 && mesh.ui.melodies == 2);
    assert(run(mesh, "get buzzer") == "> on" && mesh.ui.melodies == 2);
  }

  // A build without an attached UI can still persist the board preference.
  mesh._ui = nullptr;
  mesh.save_ok = true;
  assert(run(mesh, (set + (initial_on ? " on" : " off")).c_str()) == "OK");
  assert(mesh.saves == 3 && mesh.ui.syncs == (sound ? 3u : 2u));
  assert(run(mesh, get.c_str()) == (initial_on ? "> on" : "> off"));
  assert(mesh.ui.melodies == (sound ? 2u : 0u));

  // A failed enable leaves the muted device silent.
  if (sound) {
    mesh._ui = &mesh.ui;
    mesh.ui.syncAlertPreferences();
    mesh.save_ok = false;
    const unsigned syncs = mesh.ui.syncs;
    assert(run(mesh, "set buzzer on").find("Error") == 0);
    assert(mesh.saves == 4 && mesh.ui.syncs == syncs);
    assert(mesh._prefs.buzzer_quiet && mesh.saved.buzzer_quiet);
    assert(mesh.ui.sound_quiet && mesh.ui.melodies == 2);
  }
}
int main() {
  MyMesh buzzer, vibration;
#if EXPECT_BUZZER
  supported(buzzer, "buzzer", false);
#else
  error(buzzer, "get buzzer");
  error(buzzer, "set buzzer on");
  error(buzzer, "set buzzer off");
#endif
#if EXPECT_VIBRATION
  supported(vibration, "vibration", true);
#else
  error(vibration, "get vibration");
  error(vibration, "set vibration on");
  error(vibration, "set vibration off");
#endif
  MyMesh mesh;
  for (const char* command : {"get buzzerx", "get buzzer.extra", "get vibrationx",
       "set buzzerx on", "set vibrationx off", "get leds", "get radio", "get notify",
       "get sound", "set vibe on", "buzzer", "vibration", "get", "", "set"}) {
    char reply[16] = "unchanged";
    assert(!mesh.handleAlertCommand(command, reply, sizeof(reply)));
    assert(strcmp(reply, "unchanged") == 0);
    assert(mesh.saves == 0 && mesh.ui.syncs == 0);
  }
  for (const char* command : {"get buzzer", "get vibration", "set buzzer bad",
                             "set vibration bad", "set buzzer on", "set vibration off"}) {
    for (size_t capacity = 1; capacity <= 160; ++capacity) {
      char bounded[164]; memset(bounded, '#', sizeof(bounded));
      memcpy(bounded, "ab|", 3);
      assert(mesh.handleAlertCommand(command, bounded + 3, capacity));
      assert(memcmp(bounded, "ab|", 3) == 0);
      assert(bounded[3 + capacity] == '#');
      assert(memchr(bounded + 3, 0, capacity));
    }
  }
  char reply[16];
  assert(!mesh.handleAlertCommand(nullptr, reply, sizeof(reply)));
  assert(!mesh.handleAlertCommand("get buzzer", nullptr, sizeof(reply)));
  assert(!mesh.handleAlertCommand("get buzzer", reply, 0));
}
"""


class CompanionAlertControlTests(unittest.TestCase):
    def test_cli_storage_and_hardware_matrix(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        cases = (
            ("none", (), False, False),
            ("buzzer", ("PIN_BUZZER=1",), True, False),
            ("gpio_vibration", ("PIN_VIBRATION=2",), False, True),
            ("drv2605", ("HAS_DRV2605=1",), False, True),
            ("buzzer_gpio", ("PIN_BUZZER=1", "PIN_VIBRATION=2"), True, True),
            ("buzzer_drv2605", ("PIN_BUZZER=1", "HAS_DRV2605=1"), True, True),
        )
        source = STUBS + alert_handler() + SCENARIOS
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cpp = directory / "alert_cli.cpp"
            cpp.write_text(source, encoding="ascii")
            for name, macros, buzzer, vibration in cases:
                with self.subTest(hardware=name):
                    exe = directory / (name + (".exe" if os.name == "nt" else ""))
                    flags = [f"-D{macro}" for macro in macros]
                    if os.name != "nt":
                        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                  "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
                    result = subprocess.run(
                        [compiler, "-std=c++11", "-Wall", "-Wextra", *flags,
                         f"-DEXPECT_BUZZER={int(buzzer)}",
                         f"-DEXPECT_VIBRATION={int(vibration)}",
                         str(cpp), "-o", str(exe)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(exe)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
