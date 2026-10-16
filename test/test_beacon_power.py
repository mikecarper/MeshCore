"""Run production beacon validation with real read-only RadioLib checkers.

Only the driver hardware boundaries and Mesh queue are replaced. No retune,
power write, DAC write, or Indicator HAL service is allowed during validation.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
RADIO = ROOT / "src/helpers/radiolib"


class BeaconPowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise RuntimeError("beacon power regression requires a C++17 compiler")
        relative = "src/modules/SX126x/SX1262.cpp"
        required = (relative, "src/modules/SX127x/SX1278.cpp", "src/modules/SX127x/SX1272.cpp",
                    "src/modules/LR11x0/LR1110.cpp", "src/modules/LR11x0/LR1120.cpp",
                    "src/modules/LR2021/LR2021_config.cpp", "src/modules/SX126x/STM32WLx.cpp")
        override = os.environ.get("MESHCORE_RADIOLIB_DIR")
        candidates = [Path(override)] if override else sorted((ROOT / ".pio/libdeps").glob("*/RadioLib"))
        sdk = next((folder for folder in candidates if all((folder / name).is_file() for name in required)), None)
        if sdk is None:
            raise RuntimeError("an existing pinned RadioLib checkout is required (no download)")
        methods = []
        for filename, original, replacement, signatures in (
                (relative, "SX1262", "SX1262", ("int16_t SX1262::checkOutputPower(",)),
                ("src/modules/SX127x/SX1278.cpp", "SX1278", "SX1276",
                 ("int16_t SX1278::checkOutputPower(int8_t power, int8_t* clipped)",
                  "int16_t SX1278::checkOutputPower(int8_t power, int8_t* clipped, bool")),
                ("src/modules/SX127x/SX1272.cpp", "SX1272", "SX1272",
                 ("int16_t SX1272::checkOutputPower(int8_t power, int8_t* clipped)",
                  "int16_t SX1272::checkOutputPower(int8_t power, int8_t* clipped, bool")),
                ("src/modules/LR11x0/LR1110.cpp", "LR1110", "LR1110",
                 ("int16_t LR1110::checkOutputPower(int8_t power, int8_t* clipped)",
                  "int16_t LR1110::checkOutputPower(int8_t power, int8_t* clipped, bool")),
                ("src/modules/LR11x0/LR1120.cpp", "LR1120", "LR1121",
                 ("int16_t LR1120::checkOutputPower(int8_t power, int8_t* clipped)",
                  "int16_t LR1120::checkOutputPower(int8_t power, int8_t* clipped, bool")),
                ("src/modules/LR2021/LR2021_config.cpp", "LR2021", "LR2021",
                 ("int16_t LR2021::checkOutputPower(",)),
                ("src/modules/SX126x/STM32WLx.cpp", "STM32WLx", "STM32WLx",
                 ("int16_t STM32WLx::checkOutputPower(",))):
            text = (sdk / filename).read_text(encoding="utf-8")
            methods += [body(text, signature).replace(original + "::", replacement + "::")
                        for signature in signatures]
        header = (ROOT / "test/fixtures/beacons/power_radio.h").read_text(encoding="ascii")
        header = header.replace("@DRIVER_CHECKS@", "")
        header = header.replace("@RADIO_CAPABILITY@", body(
            (ROOT / "src/Dispatcher.h").read_text(encoding="ascii"), "virtual bool validateBeaconPower("))
        header = header.replace("@INDICATOR_WRAPPER@", body(
            (ROOT / "variants/sensecap_indicator-espnow/IndicatorSX1262Wrapper.h").read_text(encoding="ascii"),
            "class IndicatorSX1262Wrapper") + ";")
        dac = (RADIO / "DacPaSX1276Wrapper.h").read_text(encoding="ascii")
        header = header.replace("@DAC_LEVEL@", body(dac, "struct DacPaLevel") + ";")
        header = header.replace("@DAC_WRAPPER@", body(dac, "class DacPaSX1276Wrapper") + ";")
        mesh = (ROOT / "test/fixtures/beacons/mocks/Mesh.h").read_text(encoding="ascii")
        validator = body(mesh, "bool validateTextBeacon(")
        mesh = mesh.replace(validator, "bool validateTextBeacon(const BeaconSlot&) const;")
        mesh = mesh.replace("#pragma once", '#pragma once\n#include "PowerRadio.h"')
        mesh = mesh.replace("class Mesh {\n public:", "class Mesh {\n public:\n  ::RadioLibWrapper* _radio = nullptr;")
        cls.shared = (RADIO / "RadioLibWrappers.cpp").read_text(encoding="utf-8")
        cls.mesh_source = (ROOT / "src/MeshBeacon.cpp").read_text(encoding="ascii")
        production = "\n".join(methods) + "\n" + "\n".join(body(cls.shared, signature) for signature in (
            "bool RadioLibWrapper::validateBeaconPower(", "bool RadioLibWrapper::checkBeaconOutputPower(",
            "bool RadioLibWrapper::validateProfile("))
        production += "\nnamespace mesh {\n" + body(cls.mesh_source, "bool Mesh::validateTextBeacon(") + "\n}"
        production += "\nnamespace mesh { namespace beacon {\n" + body(
            (ROOT / "src/helpers/BeaconFrames.cpp").read_text(encoding="ascii"), "bool validText(") + "\n}}"
        cls.fixture = (ROOT / "test/fixtures/beacons/power_test.cpp").read_text(encoding="ascii")
        cls.production = production
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-beacon-power-")
        cls.addClassCleanup(cls.work.cleanup)
        cls.directory = Path(cls.work.name)
        (cls.directory / "PowerRadio.h").write_text(header, encoding="ascii")
        (cls.directory / "Mesh.h").write_text(mesh, encoding="ascii")

    def compile_run(self, name, flags, production=None, scenario=None, negative=False, manager_source=None):
        cpp, binary = self.directory / (name + ".cpp"), self.directory / name
        cpp.write_text(self.fixture.replace("@PRODUCTION_METHODS@", production or self.production), encoding="ascii")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                      if sys.platform.startswith("linux") else [])
        built = subprocess.run([
            self.compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-DMESH_ENABLE_TX_BEACONS=1", "-DMESH_ENABLE_MESHTEXT_CHAT=0",
            "-DMESH_ENABLE_MESHTEXT_WIRE=1", "-DLORA_TX_POWER=2", *flags, *sanitizers,
            "-I" + str(self.directory), "-I" + str(ROOT / "test/fixtures/beacons/mocks"),
            "-I" + str(ROOT / "test/fixtures/radio_profiles/mocks"), "-isystem", str(ROOT / "test/mocks"),
            "-I" + str(ROOT / "src"), "-I" + str(ROOT / "src/helpers"),
            str(manager_source or ROOT / "src/helpers/BeaconManager.cpp"),
            str(ROOT / "src/helpers/MeshtextChat.cpp"), str(cpp), "-o", str(binary)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        result = subprocess.run([str(binary), *([scenario] if scenario else [])],
                                capture_output=True, text=True, cwd=self.directory, timeout=15)
        if negative:
            self.assertNotEqual(result.returncode, 0, "old power validation unexpectedly passed")
            self.assertIn("Assertion", result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_chip_ranges_prospective_bands_board_caps_and_external_pa(self):
        for name, flags in (
                ("sx1262", ["-DRADIO_CLASS=CustomSX1262"]),
                ("sx1268", ["-DRADIO_CLASS=CustomSX1268"]),
                ("llcc68", ["-DRADIO_CLASS=CustomLLCC68"]),
                ("sx1276", ["-DRADIO_CLASS=CustomSX1276"]),
                ("sx1272", ["-DUSE_SX1272=1"]),
                ("lr1110", ["-DRADIO_CLASS=CustomLR1110"]),
                ("lr1121", ["-DRADIO_CLASS=CustomLR1121"]),
                ("lr1121_hp", ["-DRADIO_CLASS=CustomLR1121", "-DLR1121_FORCE_HP_PA=1"]),
                ("lr2021", ["-DRADIO_CLASS=CustomLR2021", "-DUSE_LR2021=1"]),
                ("stm32_dual", ["-DRADIO_CLASS=CustomSTM32WLx", "-DTEST_STM32=1"]),
                ("stm32_lp", ["-DRADIO_CLASS=CustomSTM32WLx", "-DTEST_STM32=1", "-DTEST_LP_ONLY=1"]),
                ("stm32_hp", ["-DRADIO_CLASS=CustomSTM32WLx", "-DTEST_STM32=1", "-DTEST_HP_ONLY=1"]),
                ("stm32_no_pa", ["-DRADIO_CLASS=CustomSTM32WLx", "-DTEST_STM32=1", "-DTEST_NO_PA=1"]),
                ("board_cap", ["-DRADIO_CLASS=CustomSX1262", "-DMIN_LORA_TX_POWER=0", "-DMAX_LORA_TX_POWER=13"]),
                ("dac_cap", ["-DRADIO_CLASS=CustomSX1276", "-DMIN_LORA_TX_POWER=17", "-DMAX_LORA_TX_POWER=30"])):
            with self.subTest(chip=name):
                self.compile_run(name, flags)

    def test_previous_mesh_validator_accepts_impossible_power_before_persistence(self):
        guard = "!_radio->validateBeaconPower(slot.radio.freq, slot.power)"
        self.assertEqual(self.production.count(guard), 1)
        old = self.production.replace(guard,
            "(slot.power < -9 || !isLoRaTxPowerValid(slot.power, slot.radio.freq))")
        self.compile_run("old_mesh", ["-DRADIO_CLASS=CustomSX1262"], old, "manager", negative=True)

    def test_legacy_power_image_remains_queryable_and_repairable(self):
        guard = "!_radio->validateBeaconPower(slot.radio.freq, slot.power)"
        self.assertEqual(self.production.count(guard), 1)
        legacy = self.production.replace(guard,
            "(slot.power < -9 || !isLoRaTxPowerValid(slot.power, slot.radio.freq))")
        flags = ["-DRADIO_CLASS=CustomSX1262"]
        self.compile_run("legacy_image", flags, legacy, "legacy_write")
        self.compile_run("upgrade", flags, scenario="upgrade")
        # Restoring the former disk-time capability check bricks every beacon
        # command on precisely the same actual legacy image.
        source = (ROOT / "src/helpers/BeaconManager.cpp").read_text(encoding="ascii")
        marker = "if (!decodeSlot(bytes, slot)"
        self.assertEqual(source.count(marker), 1)
        held = self.directory / "held_manager.cpp"
        held.write_text(source.replace(marker,
            marker + " || (slot.enabled && !mesh_->validateTextBeacon(slot))"), encoding="ascii")
        self.compile_run("held_upgrade", flags, scenario="upgrade", negative=True, manager_source=held)

    def test_current_band_driver_check_is_not_a_prospective_band_check(self):
        checker = body(self.shared, "bool RadioLibWrapper::checkBeaconOutputPower(")
        old = self.production.replace(checker, """bool RadioLibWrapper::checkBeaconOutputPower(
                float, int8_t power) const {
          return _radio->checkOutputPower(power, nullptr) == RADIOLIB_ERR_NONE;
        }""")
        self.compile_run("old_band", ["-DRADIO_CLASS=CustomLR1121"], old, negative=True)


if __name__ == "__main__":
    unittest.main()
