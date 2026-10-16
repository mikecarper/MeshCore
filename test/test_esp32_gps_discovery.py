#!/usr/bin/env python3
"""Execute cooperative ESP32/nRF52 GPS discovery with preference/UART policy.

Only the UART, clock and GPS hardware are peripheral doubles. Discovery,
completion, loop servicing, settings, power transitions and UART handoff come
from EnvironmentSensorManager.cpp; SensorManager.cpp and LocationProvider's
power/timing policy compile unchanged. This does not qualify physical GPS/USB.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_gps_upstream_adaptations import ARDUINO, CAYENNE, MESH
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
HEADER = ROOT / "src/helpers/sensors/EnvironmentSensorManager.h"

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <helpers/SensorManager.h>
#define PIN_GPS_TX 39
#define PIN_GPS_RX 38
#define PIN_GPS_EN 34
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define POWERSAVING_DEBUG_PRINTLN(...) ((void)0)
void LocationProvider::sendSentence(const char*) {}

struct SerialMock {
  unsigned begins=0, ends=0, polls=0, baud=0;
  int first_pin=-1, second_pin=-1, bytes=0;
  bool installed=false;
  explicit operator bool() const { return installed; }
  void setPins(int first, int second) { first_pin=first; second_pin=second; }
  void begin(unsigned speed) { assert(!installed); installed=true; baud=speed; ++begins; gpio_modes[first_pin]=INPUT; gpio_modes[second_pin]=OUTPUT; }
  void end() { assert(installed); installed=false; ++ends; }
  int available() { ++polls; return bytes; }
} Serial1;

struct TestLocation : LocationProvider {
  unsigned begins=0, resets=0, stops=0, loops=0;
  int enable_pin=PIN_GPS_EN;
  bool powered=false;
  void begin() override { powered=true; ++begins; }
  void reset() override { ++resets; }
  void stop() override {
    assert(!Serial1.installed);
    assert(gpio_modes[PIN_GPS_TX]==INPUT && gpio_modes[PIN_GPS_RX]==INPUT);
    powered=false; ++stops;
  }
  void loop() override { ++loops; }
  int getPinEn() override { return enable_pin; }
  bool isEnabled() override { return powered; }
  bool isValid() override { return false; }
  long getLatitude() override { return 0; }
  long getLongitude() override { return 0; }
  long getAltitude() override { return 0; }
  long satellitesCount() override { return 0; }
  long getTimestamp() override { return 0; }
};

struct EnvironmentSensorManager : SensorManager {
  @FIELDS@
  TestLocation* _location;
  explicit EnvironmentSensorManager(TestLocation& location) : _location(&location) {}
  bool telemetryGpsDetected() const override { return gps_detected; }
  bool telemetryGpsActive() const override { return gps_active; }
  void telemetryGpsStart() override { start_gps(); }
  void telemetryGpsStop() override { stop_gps(); }
  LocationProvider* getLocationProvider() override { return _location; }
  bool userEnabled() const { return isGpsTelemetryUserEnabled(); }
  bool transportAvailable() const { return isGpsTelemetryTransportAvailable(); }
  void initBasicGPS();
  void startGpsSerialTransport();
  void stopGpsSerialTransport();
  void armGpsPowerSavingCycle();
  void start_gps();
  void stop_gps();
  void loop() override;
  bool setSettingValue(const char* name, const char* value) override;
  void setPowerSavingEnabled(bool enabled) override;
  bool gpsUsesSerialUart(uint8_t uart) const override;
  bool gpsSerialTransportMayConflict(uint8_t uart) const override;
  bool gpsSerialTransportCanYield(uint8_t uart) const override;
  bool setGpsSerialTransportBlocked(uint8_t uart, bool blocked) override;
  void shutdownGpsTransportForDeepSleep() override;
};
@METHODS@

static void fresh(uint32_t now=100) {
  now_ms=now; delay_count=0; Serial1=SerialMock{};
}
static void requireImmediateInit(EnvironmentSensorManager& sensors) {
  const uint32_t before=millis();
  sensors.initBasicGPS();
  assert(millis()==before && delay_count==0);
  assert(Serial1.installed==(sensors.gps_discovery_pending || sensors.gps_active));
  assert(Serial1.first_pin==PIN_GPS_TX && Serial1.second_pin==PIN_GPS_RX);
#ifdef GPS_BAUD_RATE
  assert(Serial1.baud==GPS_BAUD_RATE);
#else
  assert(Serial1.baud==9600);
#endif
}

static void absent() {
  fresh(); TestLocation location; EnvironmentSensorManager sensors(location);
  requireImmediateInit(sensors);
  assert(sensors.gps_discovery_pending && !sensors.gps_detected && !sensors.gps_active);
  assert(location.powered && location.begins==1 && location.resets==1 && location.stops==0);
  assert(sensors.gpsUsesSerialUart(1) && sensors.gpsSerialTransportMayConflict(1));
  assert(!sensors.gpsUsesSerialUart(0) && !sensors.gpsUsesSerialUart(2));
  assert(!sensors.setGpsSerialTransportBlocked(2, true));
  now_ms=5099; sensors.loop();
  assert(sensors.gps_discovery_pending && location.powered && delay_count==0);
  now_ms=5100; sensors.loop();
  assert(!sensors.gps_discovery_pending && !sensors.gps_detected && !sensors.gps_active);
  assert(!location.powered && location.stops==1 && !sensors.gpsUsesSerialUart(1));
  assert(!Serial1.installed && Serial1.ends==1);
  assert(gpio_modes[PIN_GPS_TX]==INPUT && gpio_modes[PIN_GPS_RX]==INPUT);
  const unsigned polls=Serial1.polls;
  Serial1.bytes=8; now_ms=6100; sensors.loop();
  assert(Serial1.polls==polls && !sensors.gps_detected && location.stops==1);
  assert(delay_count==0);
}

static void latePreferences() {
  fresh(); TestLocation location; EnvironmentSensorManager sensors(location);
  requireImmediateInit(sensors);
  // Common preferences load after begin() while the optional receiver is cold.
  assert(sensors.setSettingValue("gps", "1"));
  sensors.setPowerSavingEnabled(true);
  assert(sensors.gps_discovery_preference_known && sensors.userEnabled());
  assert(!sensors.gps_active && location.begins==1);
  now_ms=1100; Serial1.bytes=8; sensors.loop();
  assert(!sensors.gps_discovery_pending && sensors.gps_detected && sensors.gps_active);
  assert(sensors.userEnabled() && location.powered && location.getGPSPowerSaving());
  assert(location.waitingTimeSync() && location.getNextGPSOff()>millis());
  assert(location.loops==1 && delay_count==0);
#ifdef PERSISTANT_GPS
  assert(location.stops==0 && location.begins==1 && location.resets==1);
#else
  assert(location.stops==1 && location.begins==2 && location.resets==2);
#endif
  sensors.setPowerSavingEnabled(false);
  assert(sensors.gps_active && !location.getGPSPowerSaving());
  assert(location.getNextGPSOff()==0 && location.getNextGPSOn()==0);
  assert(sensors.setSettingValue("gps", "0"));
  assert(!sensors.userEnabled() && !sensors.gps_active && !location.powered);
  assert(sensors.setSettingValue("gps", "1"));
  assert(sensors.userEnabled() && sensors.gps_active && location.powered);
}

static void latestPreferenceWins() {
  fresh(); TestLocation location; EnvironmentSensorManager sensors(location);
  requireImmediateInit(sensors);
  assert(sensors.setSettingValue("gps", "1"));
  sensors.setPowerSavingEnabled(true);
  assert(sensors.setSettingValue("gps", "0"));
  now_ms=900; Serial1.bytes=8; sensors.loop();
  assert(sensors.gps_detected && !sensors.gps_discovery_pending);
  assert(sensors.gps_discovery_preference_known && !sensors.userEnabled());
  assert(!sensors.gps_active && !location.powered && !location.getGPSPowerSaving());
  // Reprobe preserves an explicit user choice; the current node power policy
  // also wins over the policy in force when discovery originally started.
  Serial1.bytes=0; sensors.initBasicGPS();
  assert(sensors.gps_discovery_pending);
  assert(sensors.setSettingValue("gps", "1"));
  sensors.setPowerSavingEnabled(false);
  now_ms=1200; Serial1.bytes=8; sensors.loop();
  assert(sensors.gps_active && sensors.userEnabled() && location.powered);
  assert(!location.getGPSPowerSaving() && location.getNextGPSOff()==0);
}

static void awakeOrBypass() {
  fresh(); TestLocation location; EnvironmentSensorManager sensors(location);
#ifndef ENV_SKIP_GPS_DETECT
  Serial1.bytes=8;
#endif
  requireImmediateInit(sensors);
  assert(sensors.gps_detected && !sensors.gps_discovery_pending && sensors.gpsUsesSerialUart(1));
  assert(location.begins==1 && location.resets==1);
#ifdef ENV_SKIP_GPS_DETECT
  assert(Serial1.polls==0);
#else
  assert(Serial1.polls==1);
#endif
#ifdef PERSISTANT_GPS
  assert(sensors.gps_active && sensors.userEnabled() && location.powered && location.stops==0);
#else
  assert(!sensors.gps_active && !sensors.userEnabled() && !location.powered && location.stops==1);
#endif
  assert(sensors.setSettingValue("gps", "0"));
  assert(!sensors.gps_active && !sensors.userEnabled() && !location.powered);
}

static void wrap() {
  fresh(UINT32_MAX-1000U); TestLocation location; EnvironmentSensorManager sensors(location);
  const uint32_t start=millis(); requireImmediateInit(sensors);
  now_ms=start+4999U; sensors.loop();
  assert(sensors.gps_discovery_pending && location.powered);
  now_ms=start+5000U; sensors.loop();
  assert(!sensors.gps_discovery_pending && !sensors.gps_detected && !location.powered);
  assert(delay_count==0);
}

static void blockedRelease() {
  fresh(); TestLocation location; EnvironmentSensorManager sensors(location);
  requireImmediateInit(sensors);
  assert(sensors.setSettingValue("gps", "1"));
  sensors.setPowerSavingEnabled(true);
  assert(sensors.gpsSerialTransportCanYield(1));
  assert(sensors.setGpsSerialTransportBlocked(1, true));
  assert(sensors.gps_discovery_pending && sensors.gps_serial_transport_blocked);
  assert(sensors.gpsUsesSerialUart(1) && !sensors.transportAvailable());
  assert(!location.powered && location.stops==1 && Serial1.ends==1 && !Serial1.installed);
  const unsigned polls=Serial1.polls, begins=Serial1.begins, starts=location.begins;
  // These are bytes belonging to the bridge, not evidence of a late GPS.
  now_ms=20100; Serial1.bytes=8; sensors.loop();
  assert(Serial1.polls==polls && sensors.gps_discovery_pending && !sensors.gps_detected);
  sensors.initBasicGPS(); sensors.start_gps();
  assert(Serial1.begins==begins && location.begins==starts && !location.powered);
  assert(sensors.setSettingValue("gps", "0"));
  assert(sensors.setSettingValue("gps", "1"));
  assert(sensors.userEnabled() && !sensors.gps_active && !location.powered);
  Serial1.bytes=0;
  assert(sensors.setGpsSerialTransportBlocked(1, false));
  assert(sensors.gps_discovery_pending && !sensors.gps_serial_transport_blocked);
  assert(sensors.gps_discovery_started_at==20100 && sensors.transportAvailable());
  assert(Serial1.begins==begins+1 && location.begins==starts+1 && location.powered);
  now_ms=25099; sensors.loop();
  assert(sensors.gps_discovery_pending); // Bridge time cannot consume this window.
  now_ms=25100; Serial1.bytes=8; sensors.loop();
  assert(sensors.gps_detected && !sensors.gps_discovery_pending && sensors.gps_active);
  assert(sensors.userEnabled() && location.powered && location.getGPSPowerSaving());
  assert(delay_count==0);
}

static void noEnablePin() {
  fresh(); TestLocation location; location.enable_pin=-1;
  EnvironmentSensorManager sensors(location); requireImmediateInit(sensors);
  assert(sensors.gpsUsesSerialUart(1) && !sensors.gpsSerialTransportCanYield(1));
  assert(!sensors.setGpsSerialTransportBlocked(1, true));
  assert(sensors.gps_discovery_pending && !sensors.gps_serial_transport_blocked);
  assert(location.powered && Serial1.installed && location.stops==0 && Serial1.ends==0);
  now_ms=5100; sensors.loop();
  assert(!sensors.gps_discovery_pending && !sensors.gps_detected && !location.powered);
}

static void offDutyAndShutdown() {
  fresh(); TestLocation location; EnvironmentSensorManager sensors(location);
  // Shutdown before the first GPS begin must never call the nRF UARTE end.
  sensors.shutdownGpsTransportForDeepSleep();
  assert(Serial1.ends==0);
  Serial1.bytes=8; sensors.initBasicGPS();
  assert(sensors.gps_detected && sensors.gpsUsesSerialUart(1));
  sensors.setPowerSavingEnabled(true);
  assert(sensors.setSettingValue("gps", "1"));
  assert(sensors.gps_active && sensors.gps_serial_open && Serial1.installed);
  const unsigned ends=Serial1.ends, begins=Serial1.begins;
  // Repeated discovery must reuse the driver that GPS already opened.
  sensors.startGpsSerialTransport();
  assert(Serial1.begins==begins);
  location.stopTimeSync(); location.setNextGPSOff(millis()+1); now_ms+=2;
  sensors.loop();
  assert(!sensors.gps_active && !sensors.gps_serial_open && !Serial1.installed);
  assert(Serial1.ends==ends+1 && sensors.gpsUsesSerialUart(1));
  const uint32_t wake=location.getNextGPSOn(); assert(wake!=0);
  sensors.stop_gps(); sensors.shutdownGpsTransportForDeepSleep();
  sensors.shutdownGpsTransportForDeepSleep();
  assert(Serial1.ends==ends+1);
  now_ms=wake; sensors.loop();
  assert(sensors.gps_active && Serial1.installed && Serial1.begins==begins+1);
  assert(sensors.setSettingValue("gps", "0"));
  assert(!location.powered && !Serial1.installed && Serial1.ends==ends+2);
  // A bridge can acquire an already-off GPS. Its driver and pads then belong
  // to that bridge even though GPS keeps its logical UART reservation.
  assert(sensors.setGpsSerialTransportBlocked(1, true));
  Serial1.begin(115200);
  const unsigned bridge_ends=Serial1.ends;
  sensors.shutdownGpsTransportForDeepSleep(); sensors.stop_gps();
  sensors.loop(); sensors.initBasicGPS();
  assert(Serial1.installed && Serial1.baud==115200 && Serial1.ends==bridge_ends);
  assert(gpio_modes[PIN_GPS_RX]==OUTPUT && !location.powered);
}

int main(int argc, char** argv) {
  assert(argc==2);
  if (!strcmp(argv[1], "absent")) absent();
  else if (!strcmp(argv[1], "late")) latePreferences();
  else if (!strcmp(argv[1], "latest")) latestPreferenceWins();
  else if (!strcmp(argv[1], "awake")) awakeOrBypass();
  else if (!strcmp(argv[1], "wrap")) wrap();
  else if (!strcmp(argv[1], "blocked")) blockedRelease();
  else if (!strcmp(argv[1], "no_enable")) noEnablePin();
  else if (!strcmp(argv[1], "off_duty_shutdown")) offDutyAndShutdown();
  else assert(false && "unknown scenario");
  std::printf("PASS %s\n", argv[1]);
}
'''


def harness(*, negative=None):
    source = SOURCE.read_text(encoding="utf-8")
    header = HEADER.read_text(encoding="utf-8")
    # Preserve the actual header's platform/provider guards and declarations.
    # Extracting each field without its guard could invent nRF52 state that
    # the production header never made available to that platform.
    fields = header[header.index("  bool     gps_detected"):
                    header.index("  LocationProvider* _location")]
    fields += "\n#endif // ENV_INCLUDE_GPS\n"
    signatures = (
        "void EnvironmentSensorManager::initBasicGPS()",
        "void EnvironmentSensorManager::armGpsPowerSavingCycle()",
        "void EnvironmentSensorManager::start_gps()",
        "void EnvironmentSensorManager::stop_gps()",
        "bool EnvironmentSensorManager::setSettingValue(",
        "void EnvironmentSensorManager::setPowerSavingEnabled(",
        "bool EnvironmentSensorManager::gpsUsesSerialUart(",
        "bool EnvironmentSensorManager::gpsSerialTransportMayConflict(",
        "bool EnvironmentSensorManager::gpsSerialTransportCanYield(",
        "bool EnvironmentSensorManager::setGpsSerialTransportBlocked(",
        "void EnvironmentSensorManager::shutdownGpsTransportForDeepSleep()",
        "void EnvironmentSensorManager::loop()",
    )
    # These two methods live under the production platform/provider guard.
    # Compile that guard unchanged as well, instead of calling an extracted
    # unconditional definition that would mask a missing nRF52 implementation.
    begin = source.rindex("#if ENV_INCLUDE_GPS\n", 0, source.index(
        "void EnvironmentSensorManager::finishBasicGpsDiscovery(bool found)"))
    end = source.index("void EnvironmentSensorManager::initBasicGPS()", begin)
    guarded_discovery = source[begin:end] + "\n#endif // ENV_INCLUDE_GPS\n"
    methods = guarded_discovery + "\n" + "\n".join(
        extract_braced(source, signature) for signature in signatures)
    mutations = {
        "blocking": ("  gps_discovery_started_at = millis();",
                     "  gps_discovery_started_at = millis();\n  delay(5000);"),
        "blocked_poll": ("if (!gps_discovery_pending || gps_serial_transport_blocked) return;",
                         "if (!gps_discovery_pending) return;"),
        "cancellation": ("      _location->stop();",
                         "      // Negative control: leave the pending receiver powered."),
        "preference_loss": ("    gps_discovery_preference_known = true;",
                            "    // Negative control: forget that an explicit preference was loaded."),
        "failed_uart": ("  stopGpsSerialTransport();\n  _location->stop();\n  gps_active = false;\n  if (found",
                        "  _location->stop();\n  gps_active = false;\n  if (found"),
        "unopened_end": ("  if (!gps_serial_open) return;",
                         "  // Negative control: end an unopened UART."),
    }
    if negative:
        before, after = mutations[negative]
        assert methods.count(before) == 1, "negative-control anchor changed"
        methods = methods.replace(before, after, 1)
    return HARNESS.replace("@FIELDS@", fields).replace("@METHODS@", methods)


class CooperativeGpsDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if cls.compiler is None:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, cases, *, defines=(), negative=None,
                    platform="ESP32_PLATFORM"):
        with tempfile.TemporaryDirectory(prefix="meshcore-esp32-gps-") as directory:
            work = Path(directory)
            for name, text in {
                "Arduino.h": ARDUINO.replace("inline void pinMode(int,int){}",
                    "#define INPUT 0\ninline int gpio_modes[64]={};\ninline void pinMode(int pin,int mode){gpio_modes[pin]=mode;}"),
                "Mesh.h": MESH, "CayenneLPP.h": CAYENNE,
                "Wire.h": "#pragma once\nclass TwoWire {};\n",
            }.items():
                (work / name).write_text(text, encoding="utf-8")
            cpp, binary = work / "discovery.cpp", work / "discovery"
            cpp.write_text(harness(negative=negative), encoding="utf-8")
            command = [self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-parameter", "-DENV_INCLUDE_GPS=1", "-D" + platform,
                       *["-D" + define for define in defines],
                       *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else []),
                       "-I" + str(work), "-I" + str(ROOT / "src"), str(cpp),
                       str(ROOT / "src/helpers/SensorManager.cpp"), "-o", str(binary)]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            for case in cases:
                with self.subTest(case=case, defines=defines, negative=negative,
                                  platform=platform):
                    result = subprocess.run([str(binary), case], capture_output=True,
                                            text=True, timeout=10)
                    if negative:
                        self.assertNotEqual(result.returncode, 0, "negative control escaped assertions")
                        self.assertIn("Assertion", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn("PASS " + case, result.stdout)

    def test_boot_is_cooperative_and_loop_finishes_cold_gps(self):
        self.compile_run(("absent", "late", "latest", "awake", "wrap", "blocked", "no_enable", "off_duty_shutdown"),
                         defines=("GPS_BAUD_RATE=38400",))

    def test_persistent_default_cannot_replace_loaded_user_preference(self):
        self.compile_run(("absent", "late", "latest", "awake", "wrap", "blocked", "no_enable"),
                         defines=("PERSISTANT_GPS=1",))

    def test_explicit_detection_bypass_is_immediate(self):
        for defines in (("ENV_SKIP_GPS_DETECT=1",),
                        ("ENV_SKIP_GPS_DETECT=1", "PERSISTANT_GPS=1")):
            self.compile_run(("awake",), defines=defines)

    def test_boot_blocking_negative_control(self):
        self.compile_run(("absent",), negative="blocking")

    def test_bridge_bytes_cannot_finish_pending_discovery_negative_control(self):
        self.compile_run(("blocked",), negative="blocked_poll")

    def test_uart_handoff_must_stop_pending_receiver_negative_control(self):
        self.compile_run(("blocked",), negative="cancellation")

    def test_loaded_preference_must_be_retained_negative_control(self):
        self.compile_run(("latest",), defines=("PERSISTANT_GPS=1",), negative="preference_loss")

    def test_nrf52_basic_gps_uses_production_guarded_state_and_methods(self):
        for defines in (("GPS_BAUD_RATE=38400",), ("PERSISTANT_GPS=1",),
                        ("RAK_BOARD=1", "RAK_WISMESH_TAG=1")):
            self.compile_run(("absent", "late", "latest", "awake", "wrap", "blocked", "no_enable", "off_duty_shutdown"),
                             defines=defines, platform="NRF52_PLATFORM")
        for defines in (("ENV_SKIP_GPS_DETECT=1",),
                        ("ENV_SKIP_GPS_DETECT=1", "PERSISTANT_GPS=1")):
            self.compile_run(("awake",), defines=defines, platform="NRF52_PLATFORM")

    def test_nrf52_guards_preserve_distinct_wisblock_provider(self):
        # Unlike WisMesh Tag, ordinary WisBlock must not acquire basic GPS
        # discovery members. Preprocess the real header; the complete RAK
        # provider has its own behavior and tests.
        header = HEADER.read_text(encoding="ascii")
        with tempfile.TemporaryDirectory(prefix="meshcore-nrf52-gps-header-") as directory:
            work = Path(directory)
            for relative in ("Mesh.h", "helpers/SensorManager.h",
                             "helpers/sensors/LocationProvider.h"):
                path = work / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n", encoding="ascii")
            cpp = work / "header.cpp"
            cpp.write_text(header, encoding="ascii")
            for defines, expected in ((("NRF52_PLATFORM",), True),
                                      (("NRF52_PLATFORM", "RAK_BOARD"), False),
                                      (("NRF52_PLATFORM", "RAK_BOARD", "RAK_WISMESH_TAG"), True),
                                      (("RP2040_PLATFORM",), False)):
                with self.subTest(defines=defines):
                    result = subprocess.run([self.compiler, "-E", "-P", "-x", "c++",
                        "-DENV_INCLUDE_GPS=1", *["-D" + value for value in defines],
                        "-I" + str(work), "-I" + str(ROOT / "src/helpers/sensors"),
                        str(cpp)], text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual("gps_discovery_pending" in result.stdout, expected)

    def test_nrf52_runtime_negative_controls(self):
        for cases, negative, defines in ((("absent",), "blocking", ()),
                                        (("blocked",), "blocked_poll", ()),
                                        (("blocked",), "cancellation", ()),
                                        (("latest",), "preference_loss", ("PERSISTANT_GPS=1",))):
            self.compile_run(cases, negative=negative, defines=defines,
                             platform="NRF52_PLATFORM")

    def test_failed_esp32_probe_releases_its_uart_before_ownership_is_dropped(self):
        self.compile_run(("absent",))
        self.compile_run(("absent",), negative="failed_uart")

    def test_nrf52_uart_stop_requires_an_open_driver(self):
        self.compile_run(("off_duty_shutdown",), platform="NRF52_PLATFORM",
                         negative="unopened_end")


if __name__ == "__main__":
    unittest.main(verbosity=2)
