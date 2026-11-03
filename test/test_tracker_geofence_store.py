"""Exercise geofence storage, interruptions and coordinate configuration."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_sensor_tracker_store import STAT_MOCK

ROOT = Path(__file__).resolve().parents[1]

SOURCE = r'''
#include "tracker_storage_fs.h"
#include <helpers/tracker/TrackerGeofenceStore.h>
#include <helpers/tracker/TrackerGeofenceCli.h>
using namespace mesh::tracker::geofence;

static void copy(MemoryFS& to, const MemoryFS& from) {
  for (const auto& entry : from.files) to.put(entry.first.c_str(), *entry.second);
}

int main() {
  Box box;
  assert(parseBox("47.1234567, -123.1,48,-122.1234567", box));
  assert(box.south == 471234567 && box.west == -1231000000
      && box.north == 480000000 && box.east == -1221234567);
  assert(parseBox("-10,170,10,-170", box) && box.west > box.east);
  assert(!parseBox("-91,0,1,1", box));
  assert(!parseBox("1,0,0,1", box));
  assert(!parseBox("0,-181,1,1", box));
  assert(!parseBox("0,0,1,1.00000001", box));
  assert(!parseBox("0,0,1,1.", box));
  assert(!parseBox("0,0,1,1x", box));
  assert(!parseBox("0,0,1", box));
  assert(!parseBox("nan,0,1,1", box));
  char text[20]; formatCoordinate(-1221234567, text, sizeof(text));
  assert(!strcmp(text, "-122.1234567"));
  formatCoordinate(0, text, sizeof(text)); assert(!strcmp(text, "0.0000000"));

  MemoryFS fs; metadata_filesystem = &fs;
  Record fresh; assert(load(&fs, fresh) == StoreResult::NotFound);
  assert(!fresh.box.configured && fresh.last_known == Result::Unknown);
  assert(parseBox("47,-123,48,-122", fresh.box));
  assert(save(&fs, fresh) == StoreResult::Success && fresh.generation == 1);
  Record loaded; assert(load(&fs, loaded) == StoreResult::Success);
  assert(loaded.box.configured && loaded.last_known == Result::Unknown);
  Record stale = loaded;
  loaded.last_known = Result::Outside;
  assert(save(&fs, loaded) == StoreResult::Success && loaded.generation == 2);
  assert(save(&fs, stale) == StoreResult::Conflict);
  assert(load(&fs, loaded) == StoreResult::Success && loaded.last_known == Result::Outside);
  loaded.previous_box = true;
  assert(save(&fs, loaded) == StoreResult::Success);
  assert(load(&fs, loaded) == StoreResult::Success && loaded.previous_box);
  loaded.last_known = Result::Inside; loaded.previous_box = false;
  assert(save(&fs, loaded) == StoreResult::Success);
  assert(load(&fs, loaded) == StoreResult::Success && loaded.last_known == Result::Inside);
  Record invalid = loaded; invalid.previous_box = true;
  assert(save(&fs, invalid) == StoreResult::Invalid);

  Record cleared; cleared.generation = loaded.generation;
  assert(save(&fs, cleared) == StoreResult::Success);
  assert(load(&fs, loaded) == StoreResult::Success && !loaded.box.configured
      && loaded.last_known == Result::Unknown && !loaded.previous_box);

  // Every interrupted publication retains either the prior complete record
  // or the fully verified replacement; a temp image is never authoritative.
  MemoryFS baseline; metadata_filesystem = &baseline;
  Record initial; assert(parseBox("47,-123,48,-122", initial.box));
  assert(save(&baseline, initial) == StoreResult::Success);
  MemoryFS probe; copy(probe, baseline); metadata_filesystem = &probe;
  Record next = initial; next.last_known = Result::Outside;
  assert(save(&probe, next) == StoreResult::Success);
  const size_t boundaries = probe.boundary;
  for (size_t cut = 1; cut <= boundaries; ++cut) {
    MemoryFS trial; copy(trial, baseline); metadata_filesystem = &trial;
    Record candidate = initial; candidate.last_known = Result::Outside;
    trial.cut_at = cut;
    try { (void)save(&trial, candidate); } catch (const PowerCut&) {}
    trial.reset();
    Record recovered;
    assert(load(&trial, recovered) == StoreResult::Success);
    assert(recovered.box.configured && (recovered.last_known == Result::Unknown
          || recovered.last_known == Result::Outside));
  }

  // A corrupted present bank is reported as unavailable, not as a safe fix.
  auto bytes = fs.get(store_detail::kBank0); bytes[12] ^= 0x10;
  fs.put(store_detail::kBank0, bytes); metadata_filesystem = &fs;
  loaded.last_known = Result::Inside;
  assert(load(&fs, loaded) == StoreResult::Unavailable);
  assert(!loaded.box.configured && loaded.last_known == Result::Unknown);
}
'''


class TrackerGeofenceStoreTest(unittest.TestCase):
    def test_coordinates_durability_and_faults(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            cpp = temp / "test.cpp"
            cpp.write_text(SOURCE, encoding="utf-8")
            (temp / "sys").mkdir()
            (temp / "sys/stat.h").write_text(STAT_MOCK, encoding="utf-8")
            for platform, flags in (("generic", []), ("nrf52", ["-DNRF52_PLATFORM=1"]),
                                    ("esp32", ["-DESP32_PLATFORM=1"])):
                with self.subTest(platform=platform):
                    binary = temp / ("test-" + platform)
                    subprocess.run([
                        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                        *flags, "-I", str(temp), "-I", str(ROOT / "test/fixtures"), "-I", str(ROOT / "src"),
                        str(cpp), "-o", str(binary),
                    ], check=True)
                    subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
