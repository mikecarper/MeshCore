"""Exercise explicit dog-tracker profile selection and durable storage."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_sensor_tracker_store import STAT_MOCK

ROOT = Path(__file__).resolve().parents[1]

SOURCE = r'''
#include "tracker_storage_fs.h"
#include <helpers/tracker/TrackerProfile.h>
using namespace mesh::tracker::profile;

static void cloneFS(MemoryFS& target, const MemoryFS& source) {
  for (const auto& entry : source.files) target.put(entry.first.c_str(), *entry.second);
}

int main() {
  MemoryFS fs; metadata_filesystem = &fs;
  Record record;
  assert(load(&fs, record) == StoreResult::NotFound);
  assert(record.mode == Mode::Backyard && record.generation == 0);
  char reply[100] = {};
  assert(parseCommand("get tracker.profile", record, reply, sizeof(reply)) == CommandResult::Query);
  assert(!strcmp(reply, "profile=backyard"));
  assert(parseCommand("set tracker.profile backyard", record, reply, sizeof(reply)) == CommandResult::Unchanged);
  assert(parseCommand("set tracker.profile hiking", record, reply, sizeof(reply)) == CommandResult::Changed);
  assert(record.mode == Mode::Hiking && !strcmp(reply, "OK - profile hiking"));
  assert(parseCommand("set tracker.profile running", record, reply, sizeof(reply)) == CommandResult::Error);
  assert(record.mode == Mode::Hiking);
  assert(parseCommand("set tracker.profile hiking extra", record, reply, sizeof(reply)) == CommandResult::Error);
  assert(parseCommand("set tracker.profile", record, reply, sizeof(reply)) == CommandResult::Error);
  assert(parseCommand("get tracker.profile extra", record, reply, sizeof(reply)) == CommandResult::Error);
  assert(parseCommand("set tracker.mode dog", record, reply, sizeof(reply)) == CommandResult::Unhandled);
  Mode toggled = record.mode;
  assert(toggle(toggled) && toggled == Mode::Backyard);
  assert(toggle(toggled) && toggled == Mode::Hiking);
  toggled = static_cast<Mode>(8);
  assert(!toggle(toggled) && static_cast<uint8_t>(toggled) == 8);

  assert(save(&fs, record) == StoreResult::Success && record.generation == 1);
  Record loaded;
  assert(load(&fs, loaded) == StoreResult::Success && loaded.mode == Mode::Hiking);
  Record stale = loaded;
  loaded.mode = Mode::Backyard;
  assert(save(&fs, loaded) == StoreResult::Success && loaded.generation == 2);
  assert(save(&fs, stale) == StoreResult::Conflict);
  assert(load(&fs, loaded) == StoreResult::Success && loaded.mode == Mode::Backyard);
  Record invalid = loaded; invalid.mode = static_cast<Mode>(2);
  assert(save(&fs, invalid) == StoreResult::Invalid);

  // Every interrupted publication retains the last complete selection or
  // the fully verified replacement; an uncommitted temp is never selected.
  MemoryFS baseline; cloneFS(baseline, fs); metadata_filesystem = &baseline;
  Record initial; assert(load(&baseline, initial) == StoreResult::Success);
  MemoryFS probe; cloneFS(probe, baseline); metadata_filesystem = &probe;
  Record next = initial; next.mode = Mode::Hiking;
  assert(save(&probe, next) == StoreResult::Success);
  const size_t boundaries = probe.boundary;
  for (size_t cut = 1; cut <= boundaries; ++cut) {
    MemoryFS trial; cloneFS(trial, baseline); metadata_filesystem = &trial;
    Record candidate = initial; candidate.mode = Mode::Hiking;
    trial.cut_at = cut;
    try { (void)save(&trial, candidate); } catch (const PowerCut&) {}
    trial.reset();
    Record recovered;
    assert(load(&trial, recovered) == StoreResult::Success);
    assert(recovered.mode == Mode::Backyard || recovered.mode == Mode::Hiking);
  }

  // A present corrupted record fails closed and reports unavailable. The
  // caller must not silently treat corruption as a valid mode selection.
  auto damaged = fs.get(store_detail::kBank0);
  damaged[8] ^= 3;
  fs.put(store_detail::kBank0, damaged); metadata_filesystem = &fs;
  loaded.mode = Mode::Hiking;
  assert(load(&fs, loaded) == StoreResult::Unavailable);
  assert(loaded.mode == Mode::Backyard && loaded.generation == 0);
}
'''


class TrackerProfileTest(unittest.TestCase):
    def test_selection_and_durability(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source = temp / "test.cpp"
            source.write_text(SOURCE, encoding="utf-8")
            (temp / "sys").mkdir()
            (temp / "sys/stat.h").write_text(STAT_MOCK, encoding="utf-8")
            for platform, flags in (("generic", []), ("nrf52", ["-DNRF52_PLATFORM=1"]),
                                    ("esp32", ["-DESP32_PLATFORM=1"])):
                with self.subTest(platform=platform):
                    binary = temp / ("test-" + platform)
                    subprocess.run([
                        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                        *flags, "-I", str(temp), "-I", str(ROOT / "test/fixtures"),
                        "-I", str(ROOT / "src"), str(source), "-o", str(binary),
                    ], check=True)
                    subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
