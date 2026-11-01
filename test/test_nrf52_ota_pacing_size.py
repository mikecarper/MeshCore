"""Keep the RAK Full size workaround scoped and preserve the reviewed SDK."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nrf52_ota_pacing_size", ROOT / "scripts/nrf52_ota_pacing_size.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
FILTER = "+<*.cpp> +<../examples/companion_radio/*.cpp>"
PORT_FIXTURE = b"/* reviewed test SDK */\nvoid xPortPendSVHandler(void) __attribute__((naked));\n"


class Node:
    def __init__(self, path):
        self.path = Path(path)

    def srcnode(self):
        return self

    def get_abspath(self):
        return str(self.path)


class Environment(dict):
    def __init__(self, framework, **options):
        super().__init__(PIOENV=POLICY.PROFILE, SRC_FILTER=FILTER,
                         BUILD_FLAGS=["-Oz", "-flto", "-flto-partition=one"],
                         CCFLAGS=["-Wall", "-include", "SoftDeviceSvcCompat.h", "-flto"],
                         CFLAGS=["-std=gnu11", "-flto=auto"], CXXFLAGS=["-std=gnu++11"],
                         LINKFLAGS=["-flto", "-Wl,--sort-section=alignment"], CPPDEFINES=[])
        self.update(options)
        self.framework = framework
        self.middleware = []
        self.objects = []
        self.platform_name = "nordicnrf52"
        self.mcu = "nrf52840"

    def PioPlatform(self):
        owner = self

        class Platform:
            name = owner.platform_name

            def get_package_dir(self, package):
                assert package == "framework-arduinoadafruitnrf52"
                return owner.framework

        return Platform()

    def BoardConfig(self):
        return {"build.mcu": self.mcu}

    def AppendUnique(self, **options):
        for name, values in options.items():
            original = self.setdefault(name, [])
            for value in values:
                if value not in original:
                    original.append(value)

    def AddBuildMiddleware(self, function, pattern):
        self.middleware.append((function, pattern))

    def Object(self, node, **options):
        result = (node, options)
        self.objects.append(result)
        return result


class Nrf52OtaPacingSizeTest(unittest.TestCase):
    def policy(self, **changes):
        arguments = dict(platform="nordicnrf52", mcu="nrf52840", env_name=POLICY.PROFILE,
                         src_filter=FILTER)
        arguments.update(changes)
        return POLICY.size_policy(**arguments)

    def test_exact_board_role_and_effective_source_filter(self):
        self.assertTrue(self.policy())
        for changes in (
            dict(platform="espressif32"), dict(mcu="nrf52832"), dict(env_name="custom"),
            dict(env_name="RAK_4631_companion_radio_usb"),
            dict(env_name="RAK_3401_companion_radio_full"),
            dict(src_filter="+<*>"),
            dict(src_filter=FILTER + " -<../examples/companion_radio>"),
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.policy(**changes))
        for role in ("simple_repeater", "simple_room_server", "simple_sensor", "kiss_modem", "simple_secure_chat"):
            self.assertFalse(self.policy(src_filter=FILTER + " +<../examples/" + role + ">"))
        self.assertTrue(self.policy(src_filter=FILTER + " +<../examples/simple_repeater> -<../examples/simple_repeater>"))

    def test_explicit_optouts_conflicts_and_hil_preserve_existing_policy(self):
        for flag in (
            "-DMESH_NRF52_OTA_PACING_SIZE=0", "-UMESH_NRF52_OTA_PACING_SIZE",
            "-DMESH_NRF52_FLASH_TRIM=0", "-DMESH_NRF52_COMPANION_TRIM=0",
            "-DCOMPANION_RADIO_FULL=0", "-DMESH_HIL_IRQ_TIMING=1",
            "-DMC_NRF52_HIL_SPIM1_RADIO=1", "-DCOMPANION_PAIRING_UI_HIL=0",
            "-DOTA_VARIANT=custom", "-fcaller-saves", "-ftree-dominator-opts",
            "-freorder-blocks", "-ftree-loop-im", "-fno-lto",
            "-flto-partition=balanced",
        ):
            with self.subTest(flag=flag):
                self.assertFalse(self.policy(flags=flag))
        self.assertFalse(self.policy(cppdefines=[("MESH_HIL_IRQ_TIMING", 1)]))
        self.assertFalse(self.policy(ccflags=["-fcaller-saves"]))
        self.assertFalse(self.policy(linkflags=["-fcaller-saves"]))
        self.assertFalse(self.policy(ccflags=["-DMESH_HIL_IRQ_TIMING=1"]))
        self.assertFalse(self.policy(ccflags=["-D", "MESH_NRF52_OTA_PACING_SIZE=0"]))
        self.assertTrue(self.policy(flags="-Oz -flto -flto-partition=one -fno-inline-small-functions"))
        self.assertTrue(self.policy(flags="-freorder-functions"))
        self.assertTrue(self.policy(flags=['-DOTA_VARIANT="' + POLICY.PROFILE + '"']))

    def test_only_positive_lto_controls_are_removed(self):
        original = ["-Oz", "-flto", "-flto=auto", "-flto-partition=one", "-fno-lto",
                    "-include", "C:/SDK helpers/SoftDeviceSvcCompat.h", "-DVALUE=1", "-fno-inline-small-functions"]
        expected = [value for value in original if not value.startswith("-flto")]
        self.assertEqual(POLICY.without_lto(original), expected)
        self.assertEqual(len(original), 9)
        self.assertEqual(POLICY.without_lto('-Oz -flto=auto -include "SVC compat.h"'),
                         ["-Oz", "-include", '"SVC compat.h"'])

    def make_framework(self, directory):
        source = Path(directory) / POLICY.PORT_RELATIVE
        source.parent.mkdir(parents=True)
        source.write_bytes(PORT_FIXTURE)
        return source

    def test_install_is_idempotent_and_all_compiler_stages_match(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_framework(directory)
            original = source.read_bytes()
            env = Environment(directory)
            with patch.object(POLICY, "PORT_SHA256", hashlib.sha256(PORT_FIXTURE).hexdigest()):
                self.assertTrue(POLICY.install(env))
                self.assertTrue(POLICY.install(env))
            self.assertEqual(len(env.middleware), 1)
            for stage in ("CCFLAGS", "BUILD_FLAGS", "LINKFLAGS"):
                for flag in POLICY.SIZE_FLAGS:
                    self.assertEqual(env[stage].count(flag), 1)
            self.assertEqual(env["CCFLAGS"][:3], ["-Wall", "-include", "SoftDeviceSvcCompat.h"])
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(Path(directory).rglob("*port.c")), [source])

    def test_port_object_is_exact_sdk_only_and_keeps_svc_and_other_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_framework(directory)
            original = source.read_bytes()
            env = Environment(directory)
            before = {key: list(env[key]) for key in ("CCFLAGS", "CFLAGS", "CXXFLAGS", "BUILD_FLAGS")}
            for other in (Path(directory) / "other/port.c", Path(directory) / "patched-nrf52-hil-irqs/port.c"):
                node = Node(other)
                self.assertIs(POLICY.compile_port_without_lto(env, node), node)
            self.assertFalse(env.objects)
            with patch.object(POLICY, "PORT_SHA256", hashlib.sha256(PORT_FIXTURE).hexdigest()):
                node = Node(source)
                returned, options = POLICY.compile_port_without_lto(env, node)
            self.assertIs(returned, node)
            for stage, values in options.items():
                self.assertFalse(any(value.startswith("-flto") for value in values))
                expected = [value for value in before[stage] if not value.startswith("-flto")]
                if stage == "CCFLAGS":
                    expected.append("-fno-lto")
                self.assertEqual(values, expected)
                self.assertEqual(env[stage], before[stage])
            self.assertEqual(options["CCFLAGS"][:3], ["-Wall", "-include", "SoftDeviceSvcCompat.h"])
            self.assertEqual(source.read_bytes(), original)

    def test_unknown_sdk_fails_before_flags_change_and_crlf_is_equivalent(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_framework(directory)
            env = Environment(directory)
            before = dict(env)
            with self.assertRaisesRegex(RuntimeError, "changed pinned FreeRTOS"):
                POLICY.install(env)
            self.assertEqual(dict(env), before)
            self.assertFalse(env.middleware)
            expected = hashlib.sha256(PORT_FIXTURE).hexdigest()
            with patch.object(POLICY, "PORT_SHA256", expected):
                source.write_bytes(PORT_FIXTURE.replace(b"\n", b"\r\n"))
                self.assertEqual(POLICY.reviewed_port(directory), source.resolve())
                source.write_bytes(PORT_FIXTURE + b"/* changed */\n")
                with self.assertRaisesRegex(RuntimeError, "changed pinned FreeRTOS"):
                    POLICY.compile_port_without_lto(env, Node(source))
            self.assertFalse(env.objects)

    def test_excluded_install_never_reads_or_changes_sdk(self):
        for options in (dict(PIOENV="custom"), dict(BUILD_FLAGS=["-DMESH_HIL_IRQ_TIMING=1"]),
                        dict(BUILD_FLAGS=["-DMESH_NRF52_OTA_PACING_SIZE=0"]), dict(SRC_FILTER="+<*>")):
            env = Environment("/missing/framework", **options)
            before = dict(env)
            self.assertFalse(POLICY.install(env))
            self.assertEqual(dict(env), before)
            self.assertFalse(env.middleware)


if __name__ == "__main__":
    unittest.main()
