"""Check effective feature guards without overriding Meshtext enablement.

Preprocess the production header in both Arduino include orders. These cases
catch a working host codec being advertised for a firmware build that omitted
it, and catch different class layouts between Arduino and shared mesh sources.
No PlatformIO or hardware is used.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MeshtextConfigTest(unittest.TestCase):
    def setUp(self):
        self.compiler = shutil.which("g++") or shutil.which("clang++")
        if self.compiler is None:
            self.skipTest("a C++ preprocessor is required")

    def effective(self, defines, *, arduino_first=False, sdk_target=None,
                  feature="MESH_ENABLE_MESHTEXT_CHAT"):
        with tempfile.TemporaryDirectory(prefix="meshcore-mt-config-") as folder:
            work = Path(folder)
            (work / "Arduino.h").write_text(
                "#pragma once\n#ifndef ARDUINO\n#define ARDUINO 10819\n#endif\n",
                encoding="ascii",
            )
            (work / "sdkconfig.h").write_text(
                "#pragma once\n" + ("#define " + sdk_target + " 1\n" if sdk_target else ""),
                encoding="ascii",
            )
            includes = ["helpers/MeshtextConfig.h", "Arduino.h"]
            if arduino_first:
                includes.reverse()
            source = "".join("#include <" + name + ">\n" for name in includes)
            source += feature + "\n"
            result = subprocess.run(
                [self.compiler, "-E", "-P", "-x", "c++", "-",
                 "-I", str(work), "-I", str(ROOT / "src"),
                 *("-D" + define for define in defines)],
                input=source, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

    def assert_orders(self, defines, expected, **kwargs):
        for arduino_first in (False, True):
            with self.subTest(defines=defines, arduino_first=arduino_first, **kwargs):
                self.assertEqual(self.effective(defines, arduino_first=arduino_first, **kwargs),
                                 str(expected))

    def test_rak4631_full_companion_is_enabled_in_every_translation_unit(self):
        self.assert_orders(["NRF52_PLATFORM", "RAK_4631", "COMPANION_RADIO_FULL", "USE_SX1262"], 1)

    def test_nrf52_default_stays_scoped_to_qualified_hardware_and_role(self):
        enabled = ["NRF52_PLATFORM", "RAK_4631", "COMPANION_RADIO_FULL", "USE_SX1262"]
        for missing in enabled:
            self.assert_orders([item for item in enabled if item != missing], 0)
        self.assert_orders(["NRF52_PLATFORM", "RAK_3401", "COMPANION_RADIO_FULL", "USE_SX1262"], 0)
        self.assert_orders(enabled + ["MESH_PRIMARY_ESPNOW=1"], 0)
        self.assert_orders(enabled + ["MESH_PRIMARY_ESPNOW=0"], 1)

    def test_esp32_s3_full_keeps_effective_sdk_target_enablement(self):
        enabled = ["ESP32_PLATFORM", "ARDUINO=10819", "COMPANION_RADIO_FULL", "USE_SX1262"]
        self.assert_orders(enabled, 1, sdk_target="CONFIG_IDF_TARGET_ESP32S3")
        self.assert_orders(enabled + ["MESH_PRIMARY_ESPNOW=0"], 1,
                           sdk_target="CONFIG_IDF_TARGET_ESP32S3")
        self.assert_orders(enabled + ["MESH_PRIMARY_ESPNOW=1"], 0,
                           sdk_target="CONFIG_IDF_TARGET_ESP32S3")
        for target in (None, "CONFIG_IDF_TARGET_ESP32", "CONFIG_IDF_TARGET_ESP32S2"):
            self.assert_orders(enabled, 0, sdk_target=target)
        for missing in ("COMPANION_RADIO_FULL", "USE_SX1262"):
            self.assert_orders([item for item in enabled if item != missing], 0,
                               sdk_target="CONFIG_IDF_TARGET_ESP32S3")

    def test_explicit_enable_and_disable_override_defaults(self):
        self.assert_orders(["MESH_ENABLE_MESHTEXT_CHAT=1"], 1)
        self.assert_orders(["NRF52_PLATFORM", "RAK_4631", "COMPANION_RADIO_FULL",
                            "USE_SX1262", "MESH_ENABLE_MESHTEXT_CHAT=0"], 0)
        self.assert_orders(["ESP32_PLATFORM", "ARDUINO=10819", "COMPANION_RADIO_FULL",
                            "USE_SX1262", "MESH_ENABLE_MESHTEXT_CHAT=0"], 0,
                           sdk_target="CONFIG_IDF_TARGET_ESP32S3")

    def test_tx_beacon_wire_support_does_not_enable_companion_chat(self):
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM", "RP2040_PLATFORM"):
            flags = [platform, "USE_SX1262"]
            self.assert_orders(flags, 0)
            self.assert_orders(flags, 1, feature="MESH_ENABLE_MESHTEXT_WIRE")
            self.assert_orders(flags + ["MESH_ENABLE_TX_BEACONS=0"], 0,
                               feature="MESH_ENABLE_MESHTEXT_WIRE")
            self.assert_orders(flags + ["MESH_PRIMARY_ESPNOW=1"], 1,
                               feature="MESH_ENABLE_MESHTEXT_WIRE")
            self.assert_orders(flags + ["SENSECAP_INDICATOR_LORA"], 1,
                               feature="MESH_ENABLE_MESHTEXT_WIRE")
            self.assert_orders([platform, "USE_LR1110"], 1,
                               feature="MESH_ENABLE_MESHTEXT_WIRE")
        for flags in (["STM32_PLATFORM", "USE_SX1262"], []):
            self.assert_orders(flags, 0, feature="MESH_ENABLE_MESHTEXT_WIRE")

    def test_late_variant_chip_discovery_does_not_change_wire_layout(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-mt-late-variant-") as folder:
            work = Path(folder)
            # Nano G2 Ultra declares USE_SX1262 in its Arduino variant header,
            # not in global flags. The codec translation unit omits Arduino.
            (work / "Arduino.h").write_text(
                "#pragma once\n#define USE_SX1262 1\n", encoding="ascii")
            for arduino_first in (False, True):
                source = "#include <Arduino.h>\n" if arduino_first else ""
                source += "#include <helpers/MeshtextConfig.h>\n"
                source += "static_assert(MESH_ENABLE_MESHTEXT_CHAT == 0, \"chat stays disabled\");\n"
                source += "static_assert(MESH_ENABLE_MESHTEXT_WIRE == 1, \"consistent wire layout\");\n"
                result = subprocess.run(
                    [self.compiler, "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                     "-DNRF52_PLATFORM", "-I", str(work), "-I", str(ROOT / "src")],
                    input=source, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_driver_capability_and_mc_only_no_spi(self):
        source = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
        support = source.index("  bool supportsMeshtext() const override")
        start = source.rfind("#if", 0, support)
        end = source.index("#endif", support) + len("#endif")
        capability = source[start:end]
        start = source.index("bool applyProtocol(")
        end = source.index("{", start) + 1
        depth = 1
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        protocol = source[start:end].replace(" override", "")
        code = r'''
#include <helpers/MeshtextConfig.h>
#include <cstdint>
#include <cassert>
#define RADIOLIB_ERR_NONE 0
namespace mesh { enum class RadioProfileProtocol { MeshCore, Meshtext }; }
struct Base { virtual bool supportsMeshtext() const { return false; } };
struct CustomSX1262 {
  unsigned writes = 0;
  int setProfileProtocol(uint8_t) { ++writes; return 0; }
};
struct Actual : Base {
  CustomSX1262 chip;
  CustomSX1262* _radio = &chip;
  @CAPABILITY@
#if MESH_ENABLE_MESHTEXT_WIRE
  @PROTOCOL@
#endif
};
int main() {
  Actual driver;
  assert(driver.supportsMeshtext() == EXPECTED);
#if MESH_ENABLE_MESHTEXT_WIRE
  assert(driver.applyProtocol(mesh::RadioProfileProtocol::MeshCore));
  assert(driver.applyProtocol(mesh::RadioProfileProtocol::Meshtext) == EXPECTED);
  assert(driver.chip.writes == (EXPECTED ? 2U : 0U));
#endif
}
'''.replace("@CAPABILITY@", capability).replace("@PROTOCOL@", protocol)
        cases = [
            (["NRF52_PLATFORM", "USE_SX1262"], True),
            (["NRF52_PLATFORM", "USE_SX1262", "SENSECAP_INDICATOR_LORA"], False),
            (["NRF52_PLATFORM", "USE_SX1262", "SENSECAP_INDICATOR_LORA",
              "MESH_ENABLE_MESHTEXT_CHAT=1"], True),
            (["NRF52_PLATFORM", "USE_SX1262", "MESH_ENABLE_MESHTEXT_WIRE=0"], False),
            (["NRF52_PLATFORM", "USE_SX1262", "MESH_ENABLE_MESHTEXT_CHAT=1",
              "MESH_ENABLE_MESHTEXT_WIRE=0"], False),
        ]
        with tempfile.TemporaryDirectory(prefix="meshcore-mt-driver-gate-") as folder:
            binary = Path(folder) / "test"
            for flags, expected in cases:
                with self.subTest(flags=flags):
                    command = [self.compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                               "-x", "c++", "-", "-I", str(ROOT / "src"), "-o", str(binary)]
                    command += ["-D" + flag for flag in flags] + ["-DEXPECTED=" + str(int(expected))]
                    result = subprocess.run(command, input=code, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_chat_keeps_wire_support_with_beacons_disabled(self):
        flags = ["NRF52_PLATFORM", "RAK_4631", "COMPANION_RADIO_FULL", "USE_SX1262",
                 "MESH_ENABLE_TX_BEACONS=0"]
        self.assert_orders(flags, 1)
        self.assert_orders(flags, 1, feature="MESH_ENABLE_MESHTEXT_WIRE")
        flags = ["ESP32_PLATFORM", "ARDUINO=10819", "COMPANION_RADIO_FULL", "USE_SX1262",
                 "MESH_ENABLE_TX_BEACONS=0"]
        self.assert_orders(flags, 1, sdk_target="CONFIG_IDF_TARGET_ESP32S3")
        self.assert_orders(flags, 1, feature="MESH_ENABLE_MESHTEXT_WIRE",
                           sdk_target="CONFIG_IDF_TARGET_ESP32S3")


if __name__ == "__main__":
    unittest.main()
