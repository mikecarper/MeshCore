"""Resolve shared Muzi recipes and compile their real board pin definitions."""
import configparser
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("repeater", "room_server", "companion_radio_usb", "companion_radio_ble")


class MuziUniversalBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(interpolation=None, strict=False,
                                              inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini", ROOT / "variants/muzi_base/platformio.ini"])

    def option(self, section, key):
        values = self.config[section]
        if key not in values:
            for parent in values.get("extends", "").split(","):
                if parent.strip():
                    value = self.option(parent.strip(), key)
                    if value is not None:
                        return value
            return None
        return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                      lambda m: self.option(m[1], m[2]) or "", values[key])

    def flags(self, target):
        return dict((m[1], m[2] or "1") for m in re.finditer(
            r"-D\s*([A-Za-z_][A-Za-z0-9_]*)(?:=([^\s]+))?",
            self.option("env:" + target, "build_flags")))

    def test_shared_roles_select_both_drivers_and_checked_flash(self):
        for role in ROLES:
            with self.subTest(role=role):
                flags = self.flags("muzi_base_" + role)
                for key in ("MESH_RADIO_AUTO_FAMILY", "MESH_RADIO_AUTO_LR1121",
                            "MESH_MUZI_QSPI_CAPACITY_GUARD", "USE_SX1262", "USE_LR1121"):
                    self.assertEqual(flags[key], "1")
                for key in ("MESH_RADIO_AUTO_SX127X", "MESH_RADIO_AUTO_SX1268"):
                    self.assertEqual(flags[key], "0")
                self.assertEqual(flags["RADIO_CLASS"], "UniversalLoRaRadio")
                self.assertEqual(flags["WRAPPER_CLASS"], "UniversalLoRaRadio")
                self.assertEqual(flags["SX126X_DIO3_TCXO_VOLTAGE"], "3.3")
                self.assertEqual(flags["LR11X0_DIO3_TCXO_VOLTAGE"], "3.0")
                self.assertIn("+<../variants/muzi_base>",
                              self.option("env:muzi_base_" + role, "build_src_filter"))
                self.assertNotIn("env:muzi_base_" + role + "_lora_ota", self.config)

    def test_capacities_peripherals_and_legacy_builds_are_retained(self):
        replaced = {"RADIO_CLASS", "WRAPPER_CLASS"}
        for role in ROLES:
            target = "muzi_base_" + role
            shared = self.flags(target)
            for chip, driver in (("uno", "CustomSX1262"), ("duo", "CustomLR1121")):
                legacy = "muzi_base_" + chip + "_" + role
                original = self.flags(legacy)
                with self.subTest(target=legacy):
                    self.assertEqual(original["RADIO_CLASS"], driver)
                    self.assertNotIn("MESH_RADIO_AUTO_FAMILY", original)
                    self.assertNotIn("MESH_MUZI_QSPI_CAPACITY_GUARD", original)
                    for key, value in original.items():
                        if key not in replaced:
                            self.assertEqual(shared[key], value, key)
                    for key in ("board", "board_build.ldscript", "board_upload.maximum_size"):
                        self.assertEqual(self.option("env:" + target, key),
                                         self.option("env:" + legacy, key), key)
            if role.startswith("companion"):
                for key, value in {"MAX_CONTACTS": "350", "MAX_GROUP_CHANNELS": "40",
                                   "OFFLINE_QUEUE_SIZE": "256",
                                   "MUZI_BASE_ACCESSORY_AUTODETECT": "1"}.items():
                    self.assertEqual(shared[key], value)
        for chip in ("uno", "duo"):
            for role in ("repeater", "room_server"):
                flags = self.flags("muzi_base_" + chip + "_" + role + "_lora_ota")
                self.assertEqual(flags["OTA_QSPI_HARDWARE_QUALIFICATION_PENDING"], "1")
                self.assertNotIn("MESH_RADIO_AUTO_FAMILY", flags)

    def test_real_variant_compiles_both_irq_pins_without_forcing_flash_geometry(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="muzi-pin-map-") as folder:
            work = Path(folder)
            (work / "WVariant.h").write_text("#include <cstdint>\n")
            program = '''#include "variant.h"
static_assert(LORA_NSS == 44 && LORA_RESET == 42 && LORA_BUSY == 43, "control");
static_assert(LORA_MISO == 47 && LORA_MOSI == 46 && LORA_SCLK == 45, "SPI");
static_assert(PIN_QSPI_SCK == 3 && PIN_QSPI_CS == 26, "flash");
static_assert(PIN_QSPI_IO0 == 30 && PIN_QSPI_IO1 == 29 && PIN_QSPI_IO2 == 28
              && PIN_QSPI_IO3 == 2, "flash IO");
#if MESH_RADIO_AUTO_FAMILY
static_assert(LORA_SX1262_DIO_1 == 38 && LORA_LR1121_DIO_1 == 40, "runtime IRQ");
#ifdef EXTERNAL_FLASH_DEVICES
#error "shared image must detect real flash rather than force 16 MB"
#endif
#elif defined(USE_LR1121)
static_assert(LORA_DIO_1 == 40, "legacy Duo");
#else
static_assert(LORA_DIO_1 == 38, "legacy Uno");
#endif
int main(){}
'''
            source = work / "check.cpp"
            source.write_text(program)
            for flags in (("USE_SX1262",), ("USE_LR1121",),
                          ("USE_SX1262", "USE_LR1121", "MESH_RADIO_AUTO_FAMILY=1",
                           "MESH_RADIO_AUTO_LR1121=1", "MESH_MUZI_QSPI_CAPACITY_GUARD=1")):
                result = subprocess.run([compiler, "-std=c++11", "-Werror",
                    "-I" + str(work), "-I" + str(ROOT / "variants/muzi_base"),
                    *("-D" + flag for flag in flags), "-fsyntax-only", str(source)],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
