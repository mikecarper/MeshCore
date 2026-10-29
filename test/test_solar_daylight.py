"""Compare the production integer daylight schedule to a floating NOAA oracle."""
from datetime import date, datetime, timedelta, timezone
import calendar
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DAY, NIGHT, UNKNOWN = 1, 2, 0
UINT32_MAX = (1 << 32) - 1


def reference_events(day, latitude, longitude):
    """NOAA general solar equations, evaluated at the local date's noon."""
    gamma = 2 * math.pi / (366 if calendar.isleap(day.year) else 365) * (day.timetuple().tm_yday - 1)
    equation = 229.18 * (0.000075 + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma) - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma))
    declination = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.001480 * math.sin(3 * gamma))
    latitude = math.radians(latitude)
    cosine = ((math.cos(math.radians(90.833)) - math.sin(latitude) * math.sin(declination))
              / (math.cos(latitude) * math.cos(declination)))
    if not -1 < cosine < 1:
        return None
    half_day = 4 * math.degrees(math.acos(cosine))
    midnight = datetime.combine(day, datetime.min.time(), timezone.utc).timestamp()
    noon = 720 - 4 * longitude - equation
    return midnight + (noon - half_day) * 60, midnight + (noon + half_day) * 60


class SolarDaylightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="mesh-solar-daylight-")
        root = Path(cls.work.name)
        source = root / "solar.cpp"
        source.write_text(r'''
#include <helpers/SolarDaylight.h>
#include <cstdio>
#include <type_traits>
static_assert(sizeof(mesh::solar::Window) == 8, "Bounded daylight cache");
static_assert(std::is_same<std::underlying_type<mesh::solar::State>::type, uint8_t>::value,
              "Compact daylight state");
int main() {
  unsigned utc; int latitude, longitude;
  while (std::scanf("%u %d %d", &utc, &latitude, &longitude) == 3) {
    const auto result = mesh::solar::evaluate(utc, latitude, longitude);
    std::printf("%u %u\n", unsigned(result.state), unsigned(result.nextChangeUtc));
  }
}
''', encoding="utf-8")
        cls.binary = root / ("solar.exe" if os.name == "nt" else "solar")
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("c++")
        if not compiler:
            raise RuntimeError("A C++ compiler is required for daylight tests")
        command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic",
                   "-I", str(ROOT / "src"), str(source), "-o", str(cls.binary)]
        if os.name != "nt":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def evaluate(self, queries):
        result = subprocess.run([str(self.binary)], input="".join(
            f"{utc} {latitude} {longitude}\n" for utc, latitude, longitude in queries),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
        self.assertEqual(len(output), len(queries))
        return output

    def test_independent_noaa_boundaries_and_next_transition(self):
        # NOAA: https://gml.noaa.gov/grad/solcalc/solareqns.PDF . The production
        # implementation uses integer Fourier coefficients/table interpolation;
        # this oracle independently uses full precision trigonometric functions.
        places = ((47.6, -122.3), (0, 0), (51.5, 0), (-33.9, 151.2),
                  (60.2, 24.9), (61.2, -149.9), (-34.9, -56.2),
                  (45, 179.9), (45, -179.9), (65.5, -150), (-65.5, 20))
        dates = [date(2026, month, 21) for month in range(1, 13)]
        dates += [date(2024, 2, 29), date(2024, 12, 31), date(2025, 1, 1),
                  date(2100, 2, 28), date(2100, 3, 1)]
        queries, expected = [], []
        for latitude, longitude in places:
            for day in dates:
                dawn, dusk = reference_events(day, latitude, longitude)
                next_dawn, _ = reference_events(day + timedelta(days=1), latitude, longitude)
                for utc, state, change in ((dawn - 600, NIGHT, dawn), (dawn + 600, DAY, dusk),
                                            (dusk - 600, DAY, dusk), (dusk + 600, NIGHT, next_dawn)):
                    queries.append((int(utc), round(latitude * 1e6), round(longitude * 1e6)))
                    expected.append((state, change, day, latitude, longitude))
        for query, output, target in zip(queries, self.evaluate(queries), expected):
            state, transition = output
            wanted_state, wanted_transition, *_ = target
            self.assertEqual(state, wanted_state, (query, output, target))
            self.assertLessEqual(abs(transition - wanted_transition), 600, (query, output, target))
            self.assertGreater(transition, query[0])

    def test_reported_normal_transition_changes_state_at_that_second(self):
        queries = []
        for day, latitude, longitude in ((date(2024, 2, 29), 47.6, -122.3),
                (date(2026, 12, 31), 45, 179.9), (date(2027, 1, 1), 45, -179.9),
                (date(2026, 6, 21), 61.2, -149.9)):
            dawn, _ = reference_events(day, latitude, longitude)
            queries.append((int(dawn - 600), round(latitude * 1e6), round(longitude * 1e6)))
        sunrises = self.evaluate(queries)
        edges = []
        for (_, latitude, longitude), (state, sunrise) in zip(queries, sunrises):
            self.assertEqual(state, NIGHT)
            edges.extend(((sunrise - 1, latitude, longitude), (sunrise, latitude, longitude)))
        sunrise_edges = self.evaluate(edges)
        self.assertEqual([state for state, _ in sunrise_edges], [NIGHT, DAY] * len(queries))
        sunset_queries = []
        for i, (_, latitude, longitude) in enumerate(queries):
            sunset = sunrise_edges[2 * i + 1][1]
            sunset_queries.extend(((sunset - 1, latitude, longitude), (sunset, latitude, longitude)))
        self.assertEqual([state for state, _ in self.evaluate(sunset_queries)], [DAY, NIGHT] * len(queries))

    def test_polar_states_recheck_and_southern_seasons(self):
        queries, wanted = [], []
        for month, north, south in ((6, DAY, NIGHT), (12, NIGHT, DAY)):
            utc = int(datetime(2026, month, 21, tzinfo=timezone.utc).timestamp())
            for longitude in (-180000000, 0, 180000000):
                queries.extend(((utc, 85000000, longitude), (utc, -85000000, longitude),
                                (utc, 90000000, longitude), (utc, -90000000, longitude)))
                wanted.extend((north, south, north, south))
        for query, (state, change), expected in zip(queries, self.evaluate(queries), wanted):
            self.assertEqual(state, expected)
            self.assertEqual(change, query[0] + 6 * 3600)

    def test_invalid_clock_coordinates_and_rtc_saturation(self):
        plausible = 1577836800
        invalid = ((0, 0, 0), (plausible - 1, 47000000, -122000000),
                   (plausible, -90000001, 0), (plausible, 90000001, 0),
                   (plausible, 0, -180000001), (plausible, 0, 180000001))
        self.assertEqual(self.evaluate(invalid), [(UNKNOWN, 0)] * len(invalid))
        valid = [(utc, latitude, longitude)
                 for utc in (plausible, UINT32_MAX - 1, UINT32_MAX)
                 for latitude in (-90000000, 0, 47600000, 90000000)
                 for longitude in (-180000000, 0, 180000000)]
        for query, (state, change) in zip(valid, self.evaluate(valid)):
            self.assertIn(state, (DAY, NIGHT))
            self.assertGreaterEqual(change, query[0])
            self.assertLessEqual(change, UINT32_MAX)


if __name__ == "__main__":
    unittest.main()
