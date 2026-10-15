"""V3 app-only role changes must select the same SDK and bootloader mode.

Image headers alone are not a sufficient witness: the ESP32 builder rewrites
QIO headers to DIO even when it selects the QIO SDK and bootloader ELF.
No PlatformIO process, build, flash, or hardware access is used here.
"""
import ast
import configparser
import json
import os
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "variants/heltec_v3/platformio.ini"


class Profiles:
    def __init__(self):
        self.config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        self.config.read([ROOT / "platformio.ini", PROFILE], encoding="utf-8")

    def raw(self, section, key):
        if key in self.config[section]:
            return self.config[section][key].strip()
        for parent in self.config[section].get("extends", "").replace(",", " ").split():
            try:
                return self.raw(parent, key)
            except KeyError:
                pass
        raise KeyError((section, key))

    def get(self, section, key):
        value = self.raw(section, key)
        return re.sub(r"\$\{([^}]+)\}", lambda match: self.get(
            *match[1].split(".", 1)), value)


def flatten(value, prefix=""):
    result = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(flatten(item, name))
        else:
            result[name] = item
    return result


class BuilderEnvironment:
    def __init__(self, values):
        self.values = values

    def BoardConfig(self):
        return self.values

    def get(self, key, default=None):
        return ["arduino"] if key == "PIOFRAMEWORK" else default

    def subst(self, value):
        for key, replacement in {
            "$PIOFRAMEWORK": "arduino",
            "$BOARD_FLASH_MODE": self.values["build.flash_mode"],
            "$BOARD_F_FLASH": self.values.get("build.f_flash", "80000000L"),
        }.items():
            value = value.replace(key, replacement)
        return value


class HeltecV3FlashModeTests(unittest.TestCase):
    def setUp(self):
        self.profiles = Profiles()

    def test_common_base_pins_sdk_flash_mode_not_only_full_header(self):
        self.assertEqual(self.profiles.config["Heltec_lora32_v3"]["board_build.flash_mode"], "dio")
        self.assertEqual(self.profiles.get("Heltec_lora32_v3", "board"), "esp32-s3-devkitc-1")

    def test_all_same_board_roles_inherit_dio_without_conflicting_memory_type(self):
        checked = []
        for section in self.profiles.config.sections():
            try:
                if self.profiles.get(section, "board") != "esp32-s3-devkitc-1":
                    continue
            except KeyError:
                continue
            checked.append(section)
            with self.subTest(section=section):
                self.assertEqual(self.profiles.get(section, "board_build.flash_mode"), "dio")
                for key in ("board_build.memory_type", "board_build.arduino.memory_type", "board_build.boot"):
                    with self.assertRaises(KeyError):
                        self.profiles.get(section, key)
        for role in ("kiss_modem", "repeater", "room_server", "sensor",
                     "companion_radio_usb", "companion_radio_ble", "companion_radio_full"):
            self.assertIn("env:Heltec_v3_" + role, checked)

    def test_removing_base_pin_reproduces_non_full_inheritance_gap(self):
        self.profiles.config.remove_option("Heltec_lora32_v3", "board_build.flash_mode")
        with self.assertRaises(KeyError):
            self.profiles.get("env:Heltec_v3_kiss_modem", "board_build.flash_mode")
        self.assertEqual(self.profiles.get("env:Heltec_v3_companion_radio_full", "board_build.flash_mode"), "dio")

    def test_actual_installed_builder_selects_dio_sdk_and_bootloader(self):
        core = Path(os.environ.get("PLATFORMIO_CORE_DIR", str(Path.home() / ".platformio")))
        builder = core / "platforms/espressif32/builder/main.py"
        sdk_builder = core / "packages/framework-arduinoespressif32/tools/platformio-build-esp32s3.py"
        board_file = core / "platforms/espressif32/boards/esp32-s3-devkitc-1.json"
        if not all(path.is_file() for path in (builder, sdk_builder, board_file)):
            self.skipTest("existing ESP32 SDK/board installation required; never installs or invokes PlatformIO")
        tree = ast.parse(builder.read_text(encoding="utf-8"))
        names = {"_get_board_memory_type", "_get_board_flash_mode", "_get_board_boot_mode"}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        namespace = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(builder), "exec"), namespace)
        sdk_tree = ast.parse(sdk_builder.read_text(encoding="utf-8"))
        library_paths = next(keyword.value for node in ast.walk(sdk_tree)
                             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                             and node.func.attr == "Append" for keyword in node.keywords
                             if keyword.arg == "LIBPATH")
        manifest = flatten(json.loads(board_file.read_text(encoding="utf-8")))
        self.assertEqual(manifest["build.flash_mode"], "qio", "regression witness changed; inspect the board manifest")
        for role in ("kiss_modem", "companion_radio_full", "repeater", "sensor", "room_server"):
            with self.subTest(role=role):
                values = dict(manifest)
                values["build.flash_mode"] = self.profiles.get("env:Heltec_v3_" + role, "board_build.flash_mode")
                env = BuilderEnvironment(values)
                self.assertEqual(namespace["_get_board_memory_type"](env), "dio_qspi")
                self.assertEqual(env.subst(namespace["_get_board_boot_mode"](env)), "dio")
                self.assertEqual(namespace["_get_board_flash_mode"](env), "dio")
                paths = eval(compile(ast.Expression(library_paths), str(sdk_builder), "eval"), {
                    "join": os.path.join, "FRAMEWORK_DIR": "framework", "env": env})
                self.assertTrue(any(Path(path).parts[-1] == "dio_qspi" for path in paths))
                self.assertFalse(any("qio_qspi" in Path(path).parts for path in paths))
        # Before the fix, a QIO app still had a DIO header: the selected SDK and
        # bootloader, rather than that header, expose the real incompatibility.
        old = BuilderEnvironment(manifest)
        self.assertEqual(namespace["_get_board_flash_mode"](old), "dio")
        self.assertEqual(namespace["_get_board_memory_type"](old), "qio_qspi")
        self.assertEqual(old.subst(namespace["_get_board_boot_mode"](old)), "qio")


if __name__ == "__main__":
    unittest.main()
