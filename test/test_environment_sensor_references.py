"""Keep compact sensor references equivalent to the immutable callback table."""
from pathlib import Path
import configparser
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_gps_upstream_adaptations import ARDUINO, CAYENNE, MESH
import test_environment_no_gps_link as no_gps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp"
HEADER = ROOT / "src/helpers/sensors/EnvironmentSensorManager.h"

FIXTURE = r'''
#include <helpers/sensors/EnvironmentSensorManager.h>
#include <helpers/sensors/I2CAddressClaimPolicy.h>
#include <cassert>
#include <limits>
#include <vector>
#define TELEM_WIRE (&Wire)
#define MESH_DEBUG_PRINTLN(...) ((void)0)
void LocationProvider::sendSentence(const char*) {}
static bool bus_ready=true;
static bool acknowledged[128]={};
static unsigned init_calls[128]={}, probe_calls[128]={};
static bool ensureI2cBusReleased(TwoWire*) { return bus_ready; }
struct Observation { unsigned driver,channel,sub; };
static std::vector<Observation> observations;
template<unsigned Driver> static void query(uint8_t channel,uint8_t sub,CayenneLPP&) {
  observations.push_back({Driver,channel,sub});
}
static uint8_t init(TwoWire*,uint8_t address) {
  ++init_calls[address];
  if(address==0x51) return 0;
  return address==0x40?3:1;
}
static bool voltage(uint8_t sub,float& value) {
  value=sub==2?std::numeric_limits<float>::quiet_NaN():1.25f+sub;
  return sub!=1;
}
struct SensorDef {
  uint32_t address;
  const char* name;
  uint8_t (*init)(TwoWire*,uint8_t);
  void (*query)(uint8_t,uint8_t,CayenneLPP&);
  bool (*query_voltage)(uint8_t,float&);
};
static const SensorDef SENSOR_TABLE[]={
  {0x100,"invalid",init,query<0>,nullptr},
  {0x76,"Bosch",init,query<1>,nullptr},
  {0x77,"Bosch alternate",init,query<1>,nullptr},
  {0x50,"absent",init,query<2>,nullptr},
  {0x51,"failed init",init,query<3>,nullptr},
  {0x40,"three voltages",init,query<4>,voltage},
  @EXTRA_ROWS@
  {0x30,"beyond active capacity",init,query<17>,nullptr},
  {0,nullptr,nullptr,nullptr,nullptr}
};
static const size_t SENSOR_TABLE_SIZE=sizeof(SENSOR_TABLE)/sizeof(SENSOR_TABLE[0])-1;
@TABLE_BOUND@
bool EnvironmentSensorManager::i2c_probe(TwoWire&,uint8_t address) {
  assert(address<128);++probe_calls[address];return acknowledged[address];
}
@METHODS@
struct TestSensors:EnvironmentSensorManager {
  unsigned activeCount()const{return _active_sensor_count;}
  static size_t activeBytes(){return sizeof(_active_sensors);}
};
int main() {
  for(auto& ack:acknowledged) ack=true;
  acknowledged[0x50]=false;
  TestSensors sensors;
  assert(sensors.begin() && sensors.activeCount()==16);
  assert(init_calls[0x76]==1 && init_calls[0x77]==0);
  assert(init_calls[0x50]==0 && init_calls[0x51]==1);
  assert(init_calls[0x40]==1 && init_calls[0x30]==0);
  for(unsigned address=0;address<8;++address) assert(probe_calls[address]==0);
  CayenneLPP telemetry(64);
  assert(sensors.querySensors(0,telemetry) && observations.empty());
  assert(sensors.querySensors(TELEM_PERM_ENVIRONMENT,telemetry));
  assert(observations.size()==16);
  for(unsigned i=0;i<16;++i) {
    const unsigned expected_driver=i==0?1:i<4?4:i+1;
    const unsigned expected_sub=i>=1&&i<=3?i-1:0;
    assert(observations[i].driver==expected_driver);
    assert(observations[i].channel==i+2 && observations[i].sub==expected_sub);
    printf("%u,%u,%u;",observations[i].driver,observations[i].channel,observations[i].sub);
  }
  uint8_t channels[4]={99,99,99,99};
  assert(sensors.getVoltageSensorChannels(channels,3)==3);
  assert(channels[0]==3 && channels[1]==4 && channels[2]==5 && channels[3]==99);
  assert(sensors.getVoltageSensorChannels(nullptr,3)==3);
  assert(sensors.getVoltageSensorChannels(channels,0)==0 && channels[3]==99);
  SensorManager::VoltageSensorReading readings[4]={};
  readings[3].channel=99;
  assert(sensors.queryVoltageSensors(readings,3)==3);
  assert(readings[0].channel==3 && readings[0].valid && readings[0].voltage==1.25f);
  assert(readings[1].channel==4 && !readings[1].valid);
  assert(readings[2].channel==5 && !readings[2].valid && std::isnan(readings[2].voltage));
  assert(readings[3].channel==99);
  assert(sensors.queryVoltageSensors(readings,1)==1 && readings[1].channel==4);
  assert(sensors.queryVoltageSensors(nullptr,0)==0);
  // Repeated discovery resets references and preserves fallback-address identity.
  acknowledged[0x76]=false;
  assert(sensors.begin() && sensors.activeCount()==16 && init_calls[0x77]==1);
  observations.clear();sensors.querySensors(TELEM_PERM_ENVIRONMENT,telemetry);
  assert(observations.size()==16 && observations[0].driver==1);
  bus_ready=false;
  assert(sensors.begin() && sensors.activeCount()==0);
  assert(sensors.getVoltageSensorChannels(channels,3)==0);
  printf("\n");
#if MESH_SENSOR_DESCRIPTOR_INDEX
  assert(TestSensors::activeBytes()==32);
#endif
}
'''


class EnvironmentSensorReferencesTest(unittest.TestCase):
    def fixture(self, mode, source=None):
        source = SOURCE.read_text() if source is None else source
        source = re.sub(r"^[ \t]*#[ \t]*include[^\n]*(?:\n|$)", "", source, flags=re.M)
        compiler = os.environ.get("CXX", "g++")
        preprocessed = subprocess.run(
            [compiler, "-E", "-P", "-x", "c++", "-", "-DENV_INCLUDE_GPS=0",
             "-DMESH_SENSOR_DESCRIPTOR_INDEX="+str(mode)], input=source,
            capture_output=True, text=True, check=True).stdout
        methods = "\n".join(body(preprocessed, signature) for signature in no_gps.SIGNATURES)
        bound = next((line for line in preprocessed.splitlines()
                      if "Sensor descriptor index must cover" in line), "")
        extra = "\n".join('{0x%02X,"extra",init,query<%d>,nullptr},' % (0x20+i, 5+i)
                          for i in range(12))
        return FIXTURE.replace("@METHODS@", methods).replace("@TABLE_BOUND@", bound).replace("@EXTRA_ROWS@", extra)

    def run_fixture(self, mode, source=None):
        with tempfile.TemporaryDirectory(prefix="sensor-descriptor-index-") as folder:
            work = Path(folder)
            for name, content in {"Arduino.h": ARDUINO, "Mesh.h": MESH, "CayenneLPP.h": CAYENNE,
                                  "Wire.h": "#pragma once\nclass TwoWire {};\ninline TwoWire Wire;\n"}.items():
                (work/name).write_text(content)
            cpp, executable = work/"test.cpp", work/"test"
            cpp.write_text(self.fixture(mode, source))
            result = subprocess.run(
                [os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-Wno-unused-parameter", "-Wno-unused-variable", "-fsanitize=address,undefined",
                 "-fno-sanitize-recover=all", "-fno-pie", "-no-pie", "-DENV_INCLUDE_GPS=0",
                 "-DMESH_SENSOR_DESCRIPTOR_INDEX="+str(mode), "-I"+str(work), "-I"+str(ROOT/"src"),
                 str(cpp), str(ROOT/"src/helpers/SensorManager.cpp"), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            return subprocess.run([str(executable)], capture_output=True, text=True)

    def test_real_discovery_queries_voltage_and_capacity_match_both_representations(self):
        outputs = []
        for mode in (0, 1):
            with self.subTest(mode=mode):
                result = self.run_fixture(mode)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_negative_control_wrong_descriptor_is_detected(self):
        source = SOURCE.read_text()
        original = "static_cast<uint8_t>(i), sub"
        self.assertEqual(source.count(original), 1)
        result = self.run_fixture(1, source.replace(original, "static_cast<uint8_t>(0), sub"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Assertion", result.stderr)

    def test_only_ram_tight_rak_repeater_recipes_opt_in(self):
        selected = []
        for path in [ROOT/"platformio.ini", *sorted((ROOT/"variants").rglob("*.ini"))]:
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read(path)
            for section in config.sections():
                if re.search(r"-D\s*MESH_SENSOR_DESCRIPTOR_INDEX=1\b", config[section].get("build_flags", "")):
                    selected.append(section)
        self.assertEqual(selected, ["env:RAK_4631_repeater", "env:RAK_4631_repeater_unified_lora_ota"])
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT/"variants/rak4631/platformio.ini")
        for section in selected:
            self.assertRegex(config[section]["build_flags"], r"-D\s*MESH_RADIO_PRESETS_RESIDENT=0\b")

    def test_real_arm_layout_reclaims_324_bytes_with_all_sensor_and_preset_slots(self):
        compiler = shutil.which("arm-none-eabi-g++")
        if not compiler:
            candidate = Path.home()/".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++"
            if candidate.exists():
                compiler = str(candidate)
        if not compiler:
            self.skipTest("ARM compiler is not installed")
        nm = str(Path(compiler).with_name("arm-none-eabi-nm"))
        active = body(HEADER.read_text(), "struct ActiveSensor")+";"
        sizes = []
        with tempfile.TemporaryDirectory(prefix="sensor-reference-arm-size-") as folder:
            work = Path(folder)
            source = work/"size.cpp"
            source.write_text('#include <stdint.h>\n#include <helpers/RadioPresetCLI.h>\nclass CayenneLPP;\n'+active+
                              '\nunsigned char active_slots[sizeof(ActiveSensor)*16];\n'
                              'unsigned char preset_cli_size[sizeof(mesh::RadioPresetCLI)];\n'
                              'static_assert(mesh::RadioPresetTable::SLOTS == 5, "All preset slots retained");\n'
                              'static_assert(MESH_ENABLE_RADIO_PRESETS == 1, "Preset commands retained");\n')
            for mode in (0, 1):
                obj = work/("size-%s.o" % mode)
                subprocess.run([compiler, "-std=c++17", "-mcpu=cortex-m4", "-mthumb",
                                "-I"+str(ROOT/"src"), "-DMESH_RADIO_PRESETS_RESIDENT="+str(1-mode),
                                "-DMESH_SENSOR_DESCRIPTOR_INDEX="+str(mode), "-c", str(source), "-o", str(obj)], check=True)
                symbols = subprocess.check_output([nm, "-S", str(obj)], text=True)
                size = lambda name:int(next(line for line in symbols.splitlines() if line.endswith(" "+name)).split()[1],16)
                sizes.append((size("active_slots"),size("preset_cli_size")))
        self.assertEqual(sizes, [(192,196), (32,32)])
        self.assertEqual(sum(sizes[0])-sum(sizes[1]),324)


if __name__ == "__main__":
    unittest.main()
