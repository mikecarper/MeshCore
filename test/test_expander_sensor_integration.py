"""Exercise real sensor discovery against a configured expander address."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body
import test_environment_sensor_references as refs

ROOT = Path(__file__).resolve().parents[1]


class ExpanderSensorIntegration(unittest.TestCase):
    def test_reserved_expander_is_not_probed_or_initialized_as_a_sensor(self):
        source = refs.SOURCE.read_text()
        expanded = refs.EnvironmentSensorReferencesTest().fixture(
            0, '#define ESP32_PLATFORM 1\n#define MESH_CUSTOM_PERIPHERALS 1\n' + source)
        expanded = expanded.replace(
            'static bool bus_ready=true;',
            'static bool bus_ready=true;\n'
            'namespace mesh { namespace expander {\n'
            'bool ownsAddress(uint8_t address) { return address == 0x76; }\n'
            '} }')
        reserved = body(source, 'bool EnvironmentSensorManager::isPrimaryI2cAddressReserved(')
        expanded = expanded.replace('struct TestSensors:', reserved + '\nstruct TestSensors:')
        original = body(expanded, 'int main()')
        expanded = expanded.replace(original, r'''int main() {
          for(auto& ack:acknowledged) ack=true;
          TestSensors sensors;
          SensorManager no_environment;
          assert(no_environment.isPrimaryI2cAddressReserved(0x3c));
          assert(no_environment.isPrimaryI2cAddressReserved(0x3d));
          assert(!no_environment.isPrimaryI2cAddressReserved(0x27));
          const SensorManager& generic = sensors;
          assert(generic.isPrimaryI2cAddressReserved(0x76));
          assert(sensors.isPrimaryI2cAddressReserved(0x76));
          assert(sensors.isPrimaryI2cAddressReserved(0x3c));
          assert(sensors.isPrimaryI2cAddressReserved(0x3d));
          assert(!sensors.isPrimaryI2cAddressReserved(0x7e));
          assert(sensors.begin());
          assert(probe_calls[0x76] == 0 && init_calls[0x76] == 0);
          assert(probe_calls[0x77] == 1 && init_calls[0x77] == 1);
          assert(sensors.activeCount() > 0);
        }''')
        with tempfile.TemporaryDirectory(prefix='expander-sensor-') as directory:
            work = Path(directory)
            for name, content in {
                'Arduino.h': refs.ARDUINO, 'Mesh.h': refs.MESH,
                'CayenneLPP.h': refs.CAYENNE,
                'Wire.h': '#pragma once\nclass TwoWire {};\ninline TwoWire Wire;\n',
            }.items():
                (work / name).write_text(content)
            cpp, binary = work / 'probe.cpp', work / 'probe.exe'
            cpp.write_text(expanded)
            result = subprocess.run([
                os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra',
                '-Werror', '-Wno-unused-parameter', '-Wno-unused-variable',
                '-DESP32_PLATFORM=1',
                '-DENV_INCLUDE_GPS=0', '-DMESH_SENSOR_DESCRIPTOR_INDEX=0',
                '-I' + str(work), '-I' + str(ROOT / 'src'), str(cpp),
                str(ROOT / 'src/helpers/SensorManager.cpp'), '-o', str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)

    def test_address_reservation_loads_before_discovery(self):
        source = (ROOT / 'examples/companion_radio/main.cpp').read_text()
        self.assertLess(source.index('mesh::expander::begin(board,'), source.index('sensors.begin();'))


if __name__ == '__main__':
    unittest.main()
