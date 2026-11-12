#!/usr/bin/env python3
"""Exercise Full infrastructure selection and final compiler flag precedence."""
import copy
import configparser
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("esp32_full_size", ROOT / "scripts/esp32_full_size.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class Board(dict):
    pass


class FakeEnvironment(dict):
    def __init__(self, *, flash="4MB", mcu="esp32s3", role="simple_repeater"):
        super().__init__(SRC_FILTER="+<../examples/%s>" % role,
                         BUILD_FLAGS=[], CPPDEFINES=[], CCFLAGS=["-Os"],
                         CXXFLAGS=["-std=c++11", "-fexceptions"], CFLAGS=[], LINKFLAGS=["-Wl,--gc-sections"])
        self.board = Board({"build.mcu": mcu, "upload.flash_size": flash})
        self.middleware = []

    def BoardConfig(self):
        return self.board

    def PioPlatform(self):
        return type("Platform", (), {"name": "espressif32"})()

    def Replace(self, **values):
        self.update(values)

    def AppendUnique(self, **values):
        for key, items in values.items():
            self.setdefault(key, []).extend(item for item in items if item not in self.get(key, []))

    def AddBuildMiddleware(self, callback):
        self.middleware.append(callback)


class FullEsp32SizeTests(unittest.TestCase):
    def test_constrained_boards_preserve_cli_text_with_shared_copies(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read([ROOT / 'variants/ebyte_eora_s3/platformio.ini', ROOT / 'variants/xiao_c6/platformio.ini', ROOT / 'variants/lilygo_tlora_c6/platformio.ini'])
        sections = {'env:Ebyte_EoRa-S3_repeater_observer_mqtt', 'env:Xiao_C6_repeater_', 'env:LilyGo_Tlora_C6_repeater_'}
        for name in config.sections():
            flags = config[name].get('build_flags', '')
            if name in sections:
                self.assertIn('-D MESH_CLI_SHARE_REPLY_COPIES=1', flags)
                self.assertNotIn('MESH_CLI_COMPACT_REPLIES', flags)
            else:
                self.assertNotIn('MESH_CLI_SHARE_REPLY_COPIES', flags)

    def test_only_one_full_esp32_infrastructure_role_is_selected(self):
        for role in POLICY.ROLES:
            self.assertTrue(POLICY.infrastructure_policy("espressif32", "esp32s3", "+<%s>" % role, full=True))
            self.assertFalse(POLICY.infrastructure_policy("espressif32", "esp32s3", "+<%s>" % role))
            self.assertFalse(POLICY.infrastructure_policy("nordicnrf52", "nrf52840", "+<%s>" % role, full=True))
            self.assertFalse(POLICY.infrastructure_policy("espressif32", "esp32", "+<%s>" % role, full=True, companion=True))
        self.assertFalse(POLICY.infrastructure_policy("espressif32", "esp32", "+<../examples/simple_repeater> +<../examples/simple_room_server>", full=True))
        for role in POLICY.EXCLUDED_ROLES:
            self.assertFalse(POLICY.infrastructure_policy("espressif32", "esp32", "+<../examples/simple_repeater> +<%s>" % role, full=True))
        self.assertFalse(POLICY.infrastructure_policy("espressif32", "esp32", "+<../examples/simple_repeater> -<../examples/simple_repeater/main.cpp>", full=True))

    def test_compact_crypto_scopes_small_flash_and_preserves_explicit_options(self):
        self.assertEqual(POLICY.compact_definitions(8 * 1024 * 1024), {})
        values = POLICY.compact_definitions(4 * 1024 * 1024)
        self.assertEqual(values, POLICY.COMPACT_DEFINES)
        self.assertNotIn("ED25519_COMPACT_BASE", values)
        self.assertNotIn("MAX_NEIGHBOURS", values)
        explicit = POLICY.compact_definitions(4 * 1024 * 1024,
                                             "-DED25519_SMALL_BASE=0 -UED25519_COMPACT_FIELD -DED25519_USE_CRYPTO_SHA512=0")
        for name in ("ED25519_SMALL_BASE", "ED25519_COMPACT_FIELD", "ED25519_USE_CRYPTO_SHA512"):
            self.assertNotIn(name, explicit)
        self.assertNotIn("ED25519_SMALL_BASE", POLICY.compact_definitions(4 * 1024 * 1024, "-DED25519_COMPACT_BASE=1"))

    def test_framework_and_library_flags_are_corrected_after_defaults(self):
        env = FakeEnvironment()
        self.assertTrue(POLICY.install(env, {"MESHCORE_ESP32_FULL_BUILD": "1"}))
        self.assertEqual(dict(env["CPPDEFINES"]), POLICY.COMPACT_DEFINES)
        self.assertEqual(len(env.middleware), 1)
        # Emulate Arduino appending its flags after pre-scripts and then
        # PlatformIO cloning environments for framework and dependency code.
        for label in ("framework", "dependency", "application"):
            clone = copy.deepcopy(env)
            clone["CCFLAGS"] += ["-funwind-tables", "-fasynchronous-unwind-tables", "-fexceptions"]
            clone["CXXFLAGS"] += ["-fexceptions", "-funwind-tables"]
            clone["LINKFLAGS"] += ["-fexceptions", "-fasynchronous-unwind-tables"]
            node = object()
            self.assertIs(env.middleware[0](clone, node), node, label)
            for key in ("CCFLAGS", "CFLAGS", "CXXFLAGS", "LINKFLAGS"):
                self.assertFalse(POLICY.POSITIVE_FLAGS.intersection(clone[key]))
                self.assertTrue(set(POLICY.UNWIND_FLAGS).issubset(clone[key]))
            self.assertIn("-fno-exceptions", clone["CXXFLAGS"])
            self.assertIn("-Wl,--gc-sections", clone["LINKFLAGS"])
            self.assertIn("-Os", clone["CCFLAGS"])
            first = copy.deepcopy(dict(clone))
            POLICY.apply_final_flags(clone)
            self.assertEqual(dict(clone), first)

    def test_companion_standard_and_nrf52_environment_remain_unchanged(self):
        for args, process in (({}, {}), ({}, {"MESHCORE_ESP32_FULL_BUILD": "1", "MESHCORE_COMPANION_RADIO_FULL": "1"}),
                              ({"mcu": "nrf52840"}, {"MESHCORE_ESP32_FULL_BUILD": "1"}),
                              ({"role": "companion_radio"}, {"MESHCORE_ESP32_FULL_BUILD": "1"})):
            env = FakeEnvironment(**args)
            original = copy.deepcopy(dict(env))
            self.assertFalse(POLICY.install(env, process))
            self.assertEqual(dict(env), original)
            self.assertEqual(env.middleware, [])
        large = FakeEnvironment(flash="16MB")
        self.assertTrue(POLICY.install(large, {"MESHCORE_ESP32_FULL_BUILD": "1"}))
        self.assertEqual(large["CPPDEFINES"], [])

    def test_final_flags_remove_unwinding_without_changing_object_functions(self):
        compiler = shutil.which("g++")
        readelf = shutil.which("readelf")
        self.assertTrue(compiler and readelf)
        env = FakeEnvironment()
        env["CCFLAGS"] = ["-Os", "-funwind-tables", "-fasynchronous-unwind-tables"]
        POLICY.apply_final_flags(env)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "policy.cpp"
            source.write_text('struct Radio { virtual int receive() = 0; };\nint receive_packet(Radio* r) { return r->receive(); }\n')
            obj = source.with_suffix(".o")
            subprocess.run([compiler, *env["CCFLAGS"], *env["CXXFLAGS"], "-c", str(source), "-o", str(obj)], check=True)
            sections = subprocess.check_output([readelf, "-SW", str(obj)], text=True)
            symbols = subprocess.check_output(["nm", "-C", str(obj)], text=True)
            self.assertNotIn(".eh_frame", sections)
            self.assertIn("receive_packet(Radio*)", symbols)


if __name__ == "__main__":
    unittest.main()
