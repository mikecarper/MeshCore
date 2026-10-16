"""Preserve real preprocessor guards before linking no-GPS sensor hooks.

The former method-extraction fixtures could find definitions hidden inside a
disabled outer ENV_INCLUDE_GPS guard. Preprocess the complete production unit
first so an absent vtable definition cannot be manufactured by the fixture.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_gps_upstream_adaptations import ARDUINO, CAYENNE, MESH
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"

SIGNATURES = (
    "bool EnvironmentSensorManager::begin()",
    "bool EnvironmentSensorManager::querySensors(",
    "uint8_t EnvironmentSensorManager::getVoltageSensorChannels(",
    "uint8_t EnvironmentSensorManager::queryVoltageSensors(",
    "int EnvironmentSensorManager::getNumSettings()",
    "const char* EnvironmentSensorManager::getSettingName(",
    "const char* EnvironmentSensorManager::getSettingValue(",
    "bool EnvironmentSensorManager::setSettingValue(",
    "void EnvironmentSensorManager::setPowerSavingEnabled(",
    "bool EnvironmentSensorManager::supportsSharedPeripheralMode()",
    "void EnvironmentSensorManager::configureSharedPeripheralMode(",
    "bool EnvironmentSensorManager::canRemoveSharedStoragePowerForDeepSleep() const",
    "bool EnvironmentSensorManager::isGpsPowered()",
    "bool EnvironmentSensorManager::gpsUsesSerialUart(",
    "bool EnvironmentSensorManager::gpsSerialTransportMayConflict(",
    "bool EnvironmentSensorManager::gpsSerialTransportCanYield(",
    "bool EnvironmentSensorManager::setGpsSerialTransportBlocked(",
    "void EnvironmentSensorManager::shutdownGpsTransportForDeepSleep()",
)

GENERIC = r'''
#include <helpers/sensors/EnvironmentSensorManager.h>
#include <helpers/sensors/I2CAddressClaimPolicy.h>
#include <cassert>
#include <cstring>
#define TELEM_WIRE (&Wire)
#define MESH_DEBUG_PRINTLN(...) ((void)0)
void LocationProvider::sendSentence(const char*) {}
static bool ensureI2cBusReleased(TwoWire*) { return true; }
struct SensorDef {
  uint32_t address;
  const char* name;
  uint8_t (*init)(TwoWire*, uint8_t);
  void (*query)(uint8_t, uint8_t, CayenneLPP&);
  bool (*query_voltage)(uint8_t, float&);
};
static const SensorDef SENSOR_TABLE[1] = {};
static const size_t SENSOR_TABLE_SIZE = 0;
bool EnvironmentSensorManager::i2c_probe(TwoWire&, uint8_t) { return false; }
@METHODS@
int main() {
  EnvironmentSensorManager sensors;
  assert(sensors.begin());
  SensorManager& hooks = sensors;
  assert(!hooks.supportsSharedPeripheralMode());
  hooks.configureSharedPeripheralMode(true);
  hooks.configureSharedPeripheralMode(false);
  assert(!hooks.canRemoveSharedStoragePowerForDeepSleep());
  assert(!hooks.isGpsPowered() && !hooks.isGPSDetected());
  assert(!hooks.gpsUsesSerialUart(1) && !hooks.setGpsSerialTransportBlocked(1, true));
}
'''


class EnvironmentNoGpsLinkTest(unittest.TestCase):
    def preprocess(self, source, defines):
        # Includes are hardware boundaries, not conditional-compilation logic.
        # Preserve every #if/#else/#endif and local #define in the real unit.
        source = re.sub(r"^[ \t]*#[ \t]*include[^\n]*(?:\n|$)", "", source, flags=re.MULTILINE)
        result = subprocess.run([os.environ.get("CXX", "g++"), "-E", "-P", "-x", "c++", "-",
                                 "-DENV_INCLUDE_GPS=0", *defines], input=source,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_no_gps_generic_and_wisblock_vtables_link_with_real_guards(self):
        variants = (
            ("-DESP32_PLATFORM",),
            ("-DNRF52_PLATFORM", "-DRAK_BOARD", "-DRAK_4631", "-DOTA_RAK_AUTO_STORE"),
            ("-DNRF52_PLATFORM", "-DRAK_BOARD", "-DRAK_3401", "-DOTA_RAK_AUTO_STORE"),
            ("-DNRF52_PLATFORM", "-DRAK_BOARD", "-DRAK_4631", "-DOTA_RAK_AUTO_STORE", "-DETHERNET_ENABLED=0"),
        )
        original = SOURCE.read_text(encoding="ascii")
        for defines in variants:
            with self.subTest(defines=defines), tempfile.TemporaryDirectory(prefix="environment-no-gps-") as folder:
                processed = self.preprocess(original, defines)
                signatures = SIGNATURES
                rak = "-DRAK_BOARD" in defines
                if rak:
                    signatures += ("void EnvironmentSensorManager::applyWisBlockRailPower()",
                                   "uint8_t EnvironmentSensorManager::detectWisBlockStorageHolds()")
                for signature in signatures:
                    self.assertIn(signature, processed)
                methods = "\n".join(extract_braced(processed, signature) for signature in signatures)
                fixture = (ROOT / "test/fixtures/rak_peripheral_power_no_gps.cpp").read_text(encoding="ascii") if rak else GENERIC
                fixture = fixture.replace("@METHODS@", methods)
                work = Path(folder)
                arduino = ARDUINO.replace("#include <deque>", "#include <deque>\n#include <vector>\n#include <string>\n#include <cassert>\n#define INPUT 0")
                arduino = arduino.replace(
                    "inline void pinMode(int,int){}\ninline void digitalWrite(int,int){}\ninline int digitalRead(int){return HIGH;}",
                    "struct HardwareEvent { std::string name; int a; int b; uint32_t time; };\n"
                    "inline std::vector<HardwareEvent> hardware_events;\ninline int pin_values[64]={};\n"
                    "inline void pinMode(int pin,int mode){hardware_events.push_back({\"pinMode\",pin,mode,now_ms});}\n"
                    "inline void digitalWrite(int pin,int value){assert(pin>=0&&pin<64);pin_values[pin]=value;"
                    "hardware_events.push_back({\"write\",pin,value,now_ms});}\n"
                    "inline int digitalRead(int pin){assert(pin>=0&&pin<64);return pin_values[pin];}")
                for name, content in {"Arduino.h": arduino, "Mesh.h": MESH, "CayenneLPP.h": CAYENNE,
                                      "Wire.h": "#pragma once\nclass TwoWire {};\ninline TwoWire Wire;\n"}.items():
                    (work / name).write_text(content, encoding="ascii")
                cpp, exe = work / "test.cpp", work / "test"
                cpp.write_text(fixture, encoding="ascii")
                compiled = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                           "-Wno-unused-parameter", "-Wno-unused-variable", "-fsanitize=address,undefined",
                                           "-fno-sanitize-recover=all", "-fno-pie", "-no-pie", "-DENV_INCLUDE_GPS=0", *defines,
                                           "-I" + str(work), "-I" + str(ROOT / "src"), str(cpp),
                                           str(ROOT / "src/helpers/SensorManager.cpp"), "-o", str(exe)],
                                          capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                ran = subprocess.run([str(exe), *(["no_gps"] if rak else [])], capture_output=True, text=True)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_negative_control_original_outer_gps_guard_hides_required_hooks(self):
        source = SOURCE.read_text(encoding="ascii")
        close = "#endif // ENV_INCLUDE_GPS\n\n// These hooks also exist in no-GPS images."
        reopen = "#if ENV_INCLUDE_GPS\n// gps code for rak might be moved to MicroNMEALoactionProvider"
        self.assertEqual(source.count(close), 1)
        self.assertEqual(source.count(reopen), 1)
        original = source.replace(close, "// These hooks also exist in no-GPS images.")
        original = original.replace(reopen, "// gps code for rak might be moved to MicroNMEALoactionProvider")
        processed = self.preprocess(original, ("-DESP32_PLATFORM",))
        for signature in SIGNATURES[9:12]:
            self.assertNotIn(signature, processed)


if __name__ == "__main__":
    unittest.main()
