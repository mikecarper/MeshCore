"""Verify geofence coordinates, freshness and antimeridian behavior."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TrackerGeofenceTest(unittest.TestCase):
    def test_coordinate_box_and_unknown(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler not installed")
        source = r'''
#include <cassert>
#include <cstdint>
#include <limits>
#include "helpers/tracker/TrackerGeofence.h"

using namespace mesh::tracker::geofence;

static Box box(int32_t south, int32_t north, int32_t west, int32_t east) {
  Box b; b.configured = true; b.south = south; b.north = north;
  b.west = west; b.east = east; return b;
}

int main() {
  Box b = box(470000000, 480000000, -1230000000, -1220000000);
  assert(isValid(b));
  assert(classify(b, true, 475000000, -1225000000) == Result::Inside);
  assert(classify(b, true, 470000000, -1230000000) == Result::Inside);
  assert(classify(b, true, 480000000, -1220000000) == Result::Inside);
  assert(classify(b, true, 469999999, -1225000000) == Result::Outside);
  assert(classify(b, true, 480000001, -1225000000) == Result::Outside);
  assert(classify(b, true, 475000000, -1230000001) == Result::Outside);
  assert(classify(b, true, 475000000, -1219999999) == Result::Outside);
  assert(classify(b, false, 475000000, -1225000000) == Result::Unknown);
  assert(classify(b, true, 900000001, 0) == Result::Unknown);
  assert(classify(b, true, -900000001, 0) == Result::Unknown);
  assert(classify(b, true, 0, 1800000001) == Result::Unknown);
  assert(classify(b, true, 0, -1800000001) == Result::Unknown);
  assert(classify(b, true, std::numeric_limits<int32_t>::min(), 0) == Result::Unknown);

  b.configured = false;
  assert(!isValid(b));
  assert(classify(b, true, 475000000, -1225000000) == Result::Unknown);
  b = box(480000000, 470000000, 0, 1);
  assert(!isValid(b));
  assert(classify(b, true, 475000000, 0) == Result::Unknown);
  assert(!isValid(box(-900000001, 0, 0, 1)));
  assert(!isValid(box(0, 900000001, 0, 1)));
  assert(!isValid(box(0, 1, -1800000001, 1)));
  assert(!isValid(box(0, 1, 0, 1800000001)));

  // A wrapped longitude interval covers both sides of 180 degrees.
  b = box(-100000000, 100000000, 1700000000, -1700000000);
  assert(isValid(b));
  assert(classify(b, true, 0, 1700000000) == Result::Inside);
  assert(classify(b, true, 0, -1700000000) == Result::Inside);
  assert(classify(b, true, 0, 1800000000) == Result::Inside);
  assert(classify(b, true, 0, -1800000000) == Result::Inside);
  assert(classify(b, true, 0, 1699999999) == Result::Outside);
  assert(classify(b, true, 0, -1699999999) == Result::Outside);
  assert(classify(b, true, 100000001, 1800000000) == Result::Outside);
  b = box(-1, 1, 1700000000, 1800000000);
  assert(classify(b, true, 0, -1800000000) == Result::Inside);
  b = box(-1, 1, -1800000000, -1700000000);
  assert(classify(b, true, 0, 1800000000) == Result::Inside);
  b = box(-1, 1, 1800000000, 1800000000);
  assert(classify(b, true, 0, -1800000000) == Result::Inside);

  // Full range, a single point, and a real (0,0) fix are distinct from no fix.
  b = box(-900000000, 900000000, -1800000000, 1800000000);
  assert(isValid(b));
  assert(classify(b, true, 0, 0) == Result::Inside);
  assert(classify(b, true, -900000000, -1800000000) == Result::Inside);
  assert(classify(b, true, 900000000, 1800000000) == Result::Inside);
  b = box(0, 0, 0, 0);
  assert(isValid(b));
  assert(classify(b, true, 0, 0) == Result::Inside);
  assert(classify(b, true, 0, 1) == Result::Outside);
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            source_path = temp / "test.cpp"
            executable = temp / "test"
            source_path.write_text(source, encoding="utf-8")
            subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-pedantic", "-I", str(ROOT / "src"), str(source_path),
                 "-o", str(executable)], check=True,
            )
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
