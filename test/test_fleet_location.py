#!/usr/bin/env python3
"""Exercise fleet location selection and the real read-only GPS cache accessor."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_gps_upstream_adaptations import ARDUINO, CAYENNE, MESH


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>
#include <helpers/SensorManager.h>
#include <helpers/FleetLocation.h>
void LocationProvider::sendSentence(const char*) {}

struct Prefs { double node_lat = 0, node_lon = 0; unsigned advert_loc_policy = 0; };
struct Cached {
  bool valid = true;
  double latitude = 47.6062, longitude = -122.3321;
  mutable unsigned reads = 0;
  bool getCachedGpsPosition(double& lat, double& lon) const {
    ++reads; lat = latitude; lon = longitude; return valid;
  }
};
#if ENV_INCLUDE_GPS
struct Sensors : SensorManager {
  bool active = true;
  unsigned starts = 0, stops = 0, providers = 0, queries = 0;
  bool telemetryGpsDetected() const override { return true; }
  bool telemetryGpsActive() const override { return active; }
  void telemetryGpsStart() override { ++starts; active = true; }
  void telemetryGpsStop() override { ++stops; active = false; }
  LocationProvider* getLocationProvider() override { ++providers; return nullptr; }
  bool querySensors(uint8_t, CayenneLPP&) override { ++queries; return false; }
  void seed(float lat, float lon) {
    setGpsTelemetryUserEnabled(true);
    processGpsTelemetryFix(lat, lon, 5, millis());
    setGpsTelemetryUserEnabled(false);
  }
  void block(bool blocked) { setGpsTelemetryTransportAvailable(!blocked); }
  bool receiving() const { return gpsTelemetryReceiverRequired(millis()); }
  void service() { loopGpsTelemetry(millis()); }
};
#endif

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  Prefs prefs; Cached cache; int32_t lat = 1, lon = 1;
  if (scenario == "configured") {
    prefs.node_lat = 47.6062; prefs.node_lon = -122.3321;
    cache.valid = false;
    for (unsigned policy : {0, 1, 2}) {
      prefs.advert_loc_policy = policy;
      assert(mesh::readFleetLocation(prefs, cache, lat, lon));
      assert(lat == 47606200 && lon == -122332100 && cache.reads == 0);
    }
    prefs.node_lat = 0; prefs.node_lon = 180;
    assert(mesh::readFleetLocation(prefs, cache, lat, lon) && lat == 0 && lon == 180000000);
    prefs.node_lat = -90; prefs.node_lon = 0;
    assert(mesh::readFleetLocation(prefs, cache, lat, lon) && lat == -90000000 && lon == 0);
    prefs.node_lat = -0.0000015; prefs.node_lon = 0.0000015;
    assert(mesh::readFleetLocation(prefs, cache, lat, lon) && lat == -2 && lon == 2);
    assert(cache.reads == 0);
  } else if (scenario == "invalid_configured") {
    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double inf = std::numeric_limits<double>::infinity();
    const double pairs[][2] = {{nan,0}, {0,nan}, {inf,1}, {1,-inf}, {90.1,0},
      {-90.1,0}, {0,180.1}, {0,-180.1}, {0.0000001,-0.0000001}};
    for (const auto& pair : pairs) {
      prefs.node_lat = pair[0]; prefs.node_lon = pair[1]; lat = lon = 123;
      assert(!mesh::readFleetLocation(prefs, cache, lat, lon));
      assert(lat == 0 && lon == 0 && cache.reads == 0);
    }
  } else if (scenario == "fallback") {
    for (unsigned policy : {0, 1, 2}) {
      prefs.advert_loc_policy = policy;
      assert(mesh::readFleetLocation(prefs, cache, lat, lon));
      assert(lat == 47606200 && lon == -122332100);
    }
    assert(cache.reads == 3);
    cache.valid = false; lat = lon = 123;
    assert(!mesh::readFleetLocation(prefs, cache, lat, lon) && lat == 0 && lon == 0);
    cache.valid = true;
    for (const auto& pair : {std::pair<double,double>{0,0}, {0.0000001,-0.0000001},
        {std::numeric_limits<double>::quiet_NaN(),0}, {0,std::numeric_limits<double>::infinity()},
        {91,0}, {0,-181}}) {
      cache.latitude = pair.first; cache.longitude = pair.second; lat = lon = 123;
      assert(!mesh::readFleetLocation(prefs, cache, lat, lon) && lat == 0 && lon == 0);
    }
#if ENV_INCLUDE_GPS
  } else if (scenario == "cache_readonly") {
    now_ms = 1000; Sensors sensors; sensors.powersaving_enabled = true;
    double latitude = 1, longitude = 1;
    assert(!sensors.getCachedGpsPosition(latitude, longitude));
    assert(latitude == 0 && longitude == 0 && sensors.starts == 0 && sensors.stops == 0);
    sensors.seed(47.6062f, -122.3321f);
    sensors.block(true); // Cached reading must not reclaim a bridge-owned UART.
    const unsigned starts = sensors.starts, stops = sensors.stops;
    const bool active = sensors.active, receiving = sensors.receiving();
    for (unsigned i = 0; i < 100; ++i) {
      assert(sensors.getCachedGpsPosition(latitude, longitude));
      assert(std::abs(latitude - 47.6062) < 0.00001);
      assert(std::abs(longitude + 122.3321) < 0.00001);
      assert(mesh::readFleetLocation(prefs, sensors, lat, lon));
    }
    assert(sensors.starts == starts && sensors.stops == stops && sensors.active == active);
    assert(sensors.receiving() == receiving && sensors.providers == 0 && sensors.queries == 0);
    assert(sensors.powersaving_enabled && !sensors.active && !sensors.receiving());
  } else if (scenario == "cache_freshness" || scenario == "cache_wrap") {
    const uint32_t start = scenario == "cache_wrap" ? UINT32_MAX - 1000 : 0;
    now_ms = start; Sensors sensors; sensors.seed(47.6f, -122.3f);
    double latitude = 1, longitude = 1;
    now_ms = start + 12U * 3600000U;
    assert(sensors.getCachedGpsPosition(latitude, longitude));
    ++now_ms; latitude = longitude = 123;
    assert(!sensors.getCachedGpsPosition(latitude, longitude));
    assert(latitude == 0 && longitude == 0);
    assert(!mesh::readFleetLocation(prefs, sensors, lat, lon) && lat == 0 && lon == 0);
    // Configured fixed locations remain usable while GPS cache is stale.
    prefs.node_lat = 47.6; prefs.node_lon = -122.3;
    assert(mesh::readFleetLocation(prefs, sensors, lat, lon));
  } else if (scenario == "cache_full_cycle") {
    const uint32_t original = 1000;
    now_ms = original; Sensors sensors; sensors.seed(47.6f, -122.3f);
    sensors.block(true);
    double latitude = 1, longitude = 1;
    assert(sensors.getCachedGpsPosition(latitude, longitude));
    now_ms = original + 12U * 3600000U + 1;
    sensors.service(); // Expiry must persist even while another owner has the UART.
    assert(!sensors.getCachedGpsPosition(latitude, longitude));
    // These low32 bits recur after one complete millis cycle. A cache that
    // was observed expired must never become fresh again without a new fix.
    now_ms = original;
    assert(!sensors.getCachedGpsPosition(latitude, longitude));
    assert(!mesh::readFleetLocation(prefs, sensors, lat, lon));
    assert(latitude == 0 && longitude == 0 && lat == 0 && lon == 0);
    sensors.block(false);
    ++now_ms; sensors.seed(47.7f, -122.4f);
    assert(sensors.getCachedGpsPosition(latitude, longitude));
    assert(std::abs(latitude - 47.7) < 0.00001 && std::abs(longitude + 122.4) < 0.00001);
  } else if (scenario == "invalid_cache") {
    for (const auto& pair : {std::pair<float,float>{0,0}, {91,0}, {-91,0}, {0,181}, {0,-181},
        {std::numeric_limits<float>::quiet_NaN(),0}, {0,std::numeric_limits<float>::infinity()}}) {
      now_ms = 100; Sensors sensors; sensors.seed(pair.first, pair.second);
      double latitude = 1, longitude = 1;
      assert(!sensors.getCachedGpsPosition(latitude, longitude));
      assert(latitude == 0 && longitude == 0);
    }
#else
  } else if (scenario == "no_gps") {
    SensorManager sensors; double latitude = 1, longitude = 1;
    assert(!sensors.getCachedGpsPosition(latitude, longitude));
    assert(latitude == 0 && longitude == 0);
    assert(!mesh::readFleetLocation(prefs, sensors, lat, lon) && lat == 0 && lon == 0);
    prefs.node_lat = 47.6062; prefs.node_lon = -122.3321;
    assert(mesh::readFleetLocation(prefs, sensors, lat, lon));
    assert(lat == 47606200 && lon == -122332100);
#endif
  } else assert(false && "unknown scenario");
}
'''


class FleetLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("host C++17 compiler required")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-fleet-location-")
        path = Path(cls.work.name)
        for name, content in {"Arduino.h": ARDUINO, "Mesh.h": MESH,
                              "CayenneLPP.h": CAYENNE,
                              "Wire.h": "#pragma once\nclass TwoWire {};\n"}.items():
            (path / name).write_text(content)
        cpp = path / "fixture.cpp"
        cpp.write_text(HARNESS)
        cls.binaries = {}
        sanitizers = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
                      if os.environ.get("MESHCORE_FLEET_SANITIZERS") == "1" else [])
        for gps in (0, 1):
            binary = path / f"gps-{gps}"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O2",
                "-Wno-unused-parameter", *sanitizers, f"-DENV_INCLUDE_GPS={gps}",
                "-I", str(path), "-I", str(ROOT / "src"), str(cpp),
                str(ROOT / "src/helpers/SensorManager.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if result.returncode:
                cls.work.cleanup()
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[gps] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def scenario(self, name, gps=1):
        result = subprocess.run([str(self.binaries[gps]), name], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_configured_position_preferred_and_independent_of_public_advertisement_policy(self):
        self.scenario("configured")

    def test_invalid_configured_position_fails_closed_without_gps_fallback(self):
        self.scenario("invalid_configured")

    def test_only_unset_configured_pair_falls_back_to_valid_cached_position(self):
        self.scenario("fallback")

    def test_real_cached_accessor_never_acquires_or_changes_sleep_or_uart_ownership(self):
        self.scenario("cache_readonly")

    def test_real_cache_twelve_hour_boundary_and_configured_position_after_expiry(self):
        self.scenario("cache_freshness")

    def test_real_cache_freshness_survives_millis_wrap(self):
        self.scenario("cache_wrap")

    def test_expired_cache_never_becomes_fresh_after_full_millis_cycle_while_uart_blocked(self):
        self.scenario("cache_full_cycle")

    def test_real_cached_accessor_rejects_nonfinite_out_of_range_and_unset_positions(self):
        self.scenario("invalid_cache")

    def test_gps_disabled_profiles_can_use_configured_position_without_receiver(self):
        self.scenario("no_gps", gps=0)


if __name__ == "__main__":
    unittest.main()
