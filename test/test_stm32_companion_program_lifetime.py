"""Qualify process lifetime for four exact STM32 USB Companion recipes."""
import configparser
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

from test_stm32_mini_program_lifetime import CORE, FIXTURE, POLICY, ROLES, SOURCE

ROOT = Path(__file__).resolve().parents[1]
REQUEST = "MESH_STM32_COMPANION_LIFETIME_POLICY"
MARKER = "STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC"
TARGETS = {
    "RAK_3x72_companion_radio_usb": "stm32wle5ccu",
    "wio-e5-mini_companion_radio_usb": "stm32wle5jc",
    "wio-e5_companion_radio_usb": "stm32wle5jc",
    "Tiny_Relay_companion_radio_usb": "stm32wle5ccu",
}
FLAGS = ["-DSTM32_PLATFORM=1", "-D" + MARKER + "=1", "-D" + REQUEST + "=1"]


class Environment(dict):
    @property
    def name(self):
        return self.get("test_platform", "ststm32")

    def BoardConfig(self):
        return {"build.mcu": self["test_mcu"]}

    def PioPlatform(self):
        return self

    def get_package_dir(self, package):
        assert package == "framework-arduinoststm32"
        return self["framework"]

    def GetProjectOption(self, name, default):
        assert name == "build_src_filter"
        return self.get("project_sources", default)

    def AppendUnique(self, **values):
        for key, items in values.items():
            for item in items:
                if item not in self.setdefault(key, []):
                    self[key].append(item)


def environment(framework, target, **changes):
    result = Environment(framework=framework, PIOENV=target,
                         test_mcu=TARGETS.get(target, "stm32wle5jc"),
                         SRC_FILTER=["+<../examples/companion_radio/*.cpp>"],
                         BUILD_FLAGS=list(FLAGS), CPPDEFINES=[])
    result.update(changes)
    return result


def framework(directory, content=None):
    main = Path(directory) / "cores/arduino/main.cpp"
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_bytes(FIXTURE.read_bytes() if content is None else content)
    return Path(directory)


class Stm32CompanionProgramLifetimeTest(unittest.TestCase):
    def test_only_the_four_exact_variant_sections_request_the_policy(self):
        variants = (('rak3x72', 'RAK_3x72_companion_radio_usb'),
                    ('wio-e5-mini', 'wio-e5-mini_companion_radio_usb'),
                    ('wio-e5-dev', 'wio-e5_companion_radio_usb'),
                    ('tiny_relay', 'Tiny_Relay_companion_radio_usb'))
        hook = 'pre:scripts/stm32_companion_packed_cli.py'
        for variant, target in variants:
            config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(';',))
            config.read(ROOT / 'variants' / variant / 'platformio.ini')
            for section in config.sections():
                flags = shlex.split(config[section].get('build_flags', ''))
                scripts = config[section].get('extra_scripts', '').split()
                if section == 'env:' + target:
                    self.assertEqual(flags.count(REQUEST + '=1'), 1)
                    self.assertEqual(flags.count('-fuse-cxa-atexit'), 1)
                    self.assertEqual(flags.count(MARKER + '=1'), 1)
                    self.assertNotIn(POLICY.FLAG + '=1', flags,
                                     'Only the qualified core policy may generate the ABI opt-in')
                    self.assertEqual(scripts.count(hook), 1)
                    self.assertEqual(config[section]['build_unflags'].split(),
                                     ['-Os', '-fno-use-cxa-atexit'])
                else:
                    self.assertNotIn(REQUEST + '=1', flags, section)
                    self.assertNotIn(hook, scripts, section)

    def test_exact_four_recipes_and_core_share_the_reviewed_qualification(self):
        self.assertEqual(POLICY.COMPANION_TARGETS, TARGETS)
        self.assertEqual(POLICY.COMPANION_REQUEST, REQUEST)
        self.assertEqual(POLICY.COMPANION_SOURCES, (
            "../examples/companion_radio/main.cpp", "../examples/companion_radio/MyMesh.cpp"))
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-core-") as folder:
            for content, expected in (
                    (FIXTURE.read_bytes(), True),
                    (FIXTURE.read_bytes().replace(b"\n", b"\r\n"), True),
                    (FIXTURE.read_bytes() + b"\n// changed SDK\n", False),
                    (b"int main() { return 0; }\n", False),
                    (b"\xff\x00 unreviewed core\n", False)):
                framework(folder, content)
                for target in TARGETS:
                    with self.subTest(target=target, source=content[-24:]):
                        env = environment(folder, target)
                        self.assertEqual(POLICY.install(env, ROLES), expected)
                        self.assertEqual(env["CPPDEFINES"], [(POLICY.FLAG, 1)] if expected else [])
            for missing in (None, "", Path(folder) / "missing"):
                self.assertFalse(POLICY.supported_program_lifetime(missing))
            framework(folder)
            with mock.patch.object(Path, "read_text", side_effect=PermissionError("unreadable SDK")):
                self.assertFalse(POLICY.supported_program_lifetime(folder))

    def test_opt_in_and_overrides_do_not_cross_role_or_target_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-scope-") as folder:
            framework(folder)
            for target in TARGETS:
                for flags in (["-DSTM32_PLATFORM=1", "-D" + MARKER + "=1"],
                              ["-DSTM32_PLATFORM=1", "-D" + MARKER + "=1",
                               "-DMESH_STM32_MINI_LIFETIME_POLICY=1"],
                              FLAGS + ["-D" + REQUEST + "=0"],
                              FLAGS + ["-U" + REQUEST]):
                    with self.subTest(target=target, flags=flags):
                        env = environment(folder, target, BUILD_FLAGS=flags)
                        self.assertFalse(POLICY.install(env, ROLES))
                        self.assertEqual(env["CPPDEFINES"], [])
                for other in (target + "_custom", target.replace("_usb", "_ble"),
                              "RAK_4631_companion_radio_full", "wio-e5-mini_sensor",
                              "wio-e5_repeater", "Tiny_Relay_kiss_modem"):
                    with self.subTest(target=other):
                        env = environment(folder, other)
                        self.assertFalse(POLICY.install(env, ROLES))
                        self.assertEqual(env["CPPDEFINES"], [])
            env = environment(folder, "wio-e5-mini_repeater",
                              SRC_FILTER=["+<../examples/simple_repeater>"])
            self.assertFalse(POLICY.install(env, ROLES), "Companion request must not opt in Mini")

    def test_mcu_platform_and_compact_marker_are_exactly_qualified(self):
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-recipe-") as folder:
            framework(folder)
            for target, mcu in TARGETS.items():
                self.assertTrue(POLICY.install(environment(folder, target, test_mcu=mcu.upper()), ROLES))
                rejected = [dict(test_mcu=value) for value in (
                    "", "stm32wl55jc", "nrf52840", "esp32s3", mcu + "_custom",
                    "stm32wle5jc" if mcu == "stm32wle5ccu" else "stm32wle5ccu")]
                rejected += [dict(test_platform="espressif32"), dict(test_platform="nordicnrf52"),
                             dict(BUILD_FLAGS=["-D" + MARKER + "=1", "-D" + REQUEST + "=1"]),
                             dict(BUILD_FLAGS=FLAGS + ["-DSTM32_PLATFORM=0"]),
                             dict(BUILD_FLAGS=FLAGS + ["-U" + MARKER]),
                             dict(BUILD_FLAGS=FLAGS + ["-D" + MARKER + "=0"])]
                for changes in rejected:
                    with self.subTest(target=target, changes=changes), self.assertRaises(ValueError):
                        POLICY.install(environment(folder, target, **changes), ROLES)

    def test_both_companion_translation_units_and_no_other_role_are_required(self):
        own = "../examples/companion_radio/"
        forbidden = ROLES.ROLE_SOURCES + ROLES.COMPANION_SOURCES[1:]
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-sources-") as folder:
            framework(folder)
            for target in TARGETS:
                rejected = [[], ["+<*.cpp>"], ["+<" + own + "MyMesh.cpp>"],
                            ["+<" + own + "main.cpp>"],
                            ["+<" + own + ">", "-<" + own + "main.cpp>"],
                            ["+<" + own + ">", "-<" + own + "MyMesh.cpp>"]]
                rejected += [["+<" + own + ">", "+<" + other + ">"] for other in forbidden]
                for sources in rejected:
                    with self.subTest(target=target, sources=sources), self.assertRaises(ValueError):
                        POLICY.install(environment(folder, target, SRC_FILTER=sources), ROLES)
                for sources in (["+<" + own + ">"],
                                ["+<" + own + "main.cpp>", "+<" + own + "MyMesh.cpp>"],
                                ["+<" + own + ">", "-<" + own + "main.cpp>",
                                 "+<" + own + "main.cpp>"],
                                ["+<" + own + ">", "+<" + forbidden[0] + ">",
                                 "-<" + forbidden[0] + ">"]):
                    with self.subTest(target=target, sources=sources):
                        self.assertTrue(POLICY.install(environment(folder, target, SRC_FILTER=sources), ROLES))
                env = environment(folder, target, project_sources=["+<" + own + "*.cpp>"])
                del env["SRC_FILTER"]
                self.assertTrue(POLICY.install(env, ROLES), "Project-option filter must qualify identically")

    def test_explicit_enable_still_requires_the_pinned_core_and_disable_wins(self):
        disabled = [dict(CPPDEFINES=[(POLICY.FLAG, 0)]),
                    dict(CPPDEFINES=[(POLICY.FLAG, "(0)")]),
                    dict(BUILD_FLAGS=FLAGS + ["-D" + POLICY.FLAG + "=0"]),
                    dict(BUILD_FLAGS=FLAGS + ["-U" + POLICY.FLAG], CPPDEFINES=[(POLICY.FLAG, 1)]),
                    dict(BUILD_FLAGS=FLAGS + ["-D", POLICY.FLAG + "='0'"], CPPDEFINES=[POLICY.FLAG])]
        enabled = [dict(CPPDEFINES=[POLICY.FLAG]),
                   dict(CPPDEFINES=[(POLICY.FLAG, 1)]),
                   dict(BUILD_FLAGS=FLAGS + ["-D" + POLICY.FLAG + "=1"], CPPDEFINES=[(POLICY.FLAG, 0)]),
                   dict(BUILD_FLAGS=" ".join(FLAGS) + " -D " + POLICY.FLAG + "=1"),
                   dict(BUILD_FLAGS=["-DSTM32_PLATFORM=1", "-D" + MARKER + "=1", "-D" + POLICY.FLAG + "=1"])]
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-overrides-") as folder:
            for supported in (True, False):
                framework(folder, FIXTURE.read_bytes() if supported else b"int main() { return 0; }\n")
                for target in TARGETS:
                    for changes in disabled:
                        with self.subTest(target=target, supported=supported, disabled=changes):
                            self.assertFalse(POLICY.install(environment(folder, target, **changes), ROLES))
                    for changes in enabled:
                        with self.subTest(target=target, supported=supported, enabled=changes):
                            env = environment(folder, target, **changes)
                            if supported:
                                self.assertTrue(POLICY.install(env, ROLES))
                            else:
                                with self.assertRaises(ValueError):
                                    POLICY.install(env, ROLES)

    def test_qualification_only_adds_the_existing_lifetime_definition(self):
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-limits-") as folder:
            framework(folder)
            for target in TARGETS:
                env = environment(folder, target, CPPDEFINES=[("KEEP", 7)],
                                  LINKFLAGS=["-Wl,--gc-sections"],
                                  upload_maximum_size=229376, minimum_stack=1024)
                before = {key: value.copy() if isinstance(value, list) else value
                          for key, value in env.items()}
                self.assertTrue(POLICY.install(env, ROLES))
                before["CPPDEFINES"].append((POLICY.FLAG, 1))
                self.assertEqual(dict(env), before)
                self.assertTrue(POLICY.install(env, ROLES))
                self.assertEqual(dict(env), before, "Repeated installation must be idempotent")

    def test_companion_hook_keeps_automatic_delete_and_explicit_cleanup(self):
        compiler = shutil.which("g++")
        if not compiler or os.name == "nt":
            self.skipTest("native ELF C++ runtime required for cleanup positive control")
        program = r'''
#include <cassert>
extern "C" int __aeabi_atexit(void*, void (*)(void*), void*);
extern "C" int __cxa_atexit(void (*)(void*), void*, void*);
extern "C" void __cxa_finalize(void*);
unsigned closed=0, destroyed=0, callback=0;
void cleanup(void* count) { ++*static_cast<unsigned*>(count); }
struct Resource {
  void close() { ++closed; }
  ~Resource() { close(); ++destroyed; }
};
int main() {
  { Resource automatic; }
  auto* allocated=new Resource; delete allocated;
  assert(closed==2 && destroyed==2);
  Resource explicit_close; explicit_close.close();
  assert(closed==3 && destroyed==2);
  unsigned dso=0;
  assert(__aeabi_atexit(&callback,cleanup,&dso)==0);
  __cxa_finalize(&dso); assert(callback==0);
  assert(__cxa_atexit(cleanup,&callback,&dso)==0);
  __cxa_finalize(&dso); assert(callback==1);
}
'''
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-cleanup-") as folder:
            cpp, binary = Path(folder) / "main.cpp", Path(folder) / "check"
            cpp.write_text(program)
            subprocess.run([compiler, "-std=c++11", "-Os", "-flto", "-DSTM32_PLATFORM=1",
                            "-D" + MARKER + "=1", "-D" + POLICY.FLAG + "=1",
                            str(cpp), str(SOURCE), "-o", str(binary)], check=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_companion_arm_registration_and_disabled_platform_guards(self):
        compiler = shutil.which("arm-none-eabi-g++")
        if not compiler:
            compiler = next((str(path) for path in sorted((CORE / "packages").glob(
                "toolchain-gccarmnoneeabi*/bin/arm-none-eabi-g++*")) if path.is_file()), None)
        if not compiler:
            if os.environ.get("MESHCORE_REQUIRE_ARM_COMPILER") == "1":
                self.fail("ARM compiler required for Companion ABI qualification")
            self.skipTest("ARM compiler unavailable")
        nm = str(Path(compiler).with_name("arm-none-eabi-nm" +
                 (".exe" if str(compiler).endswith(".exe") else "")))
        self.assertTrue(Path(nm).is_file())
        program = "volatile unsigned destroyed;\nstruct Resource { ~Resource() { ++destroyed; } } global;\n"
        with tempfile.TemporaryDirectory(prefix="companion-lifetime-arm-") as folder:
            folder = Path(folder)
            cpp, application, hook, combined = (folder / name for name in
                ("main.cpp", "main.o", "hook.o", "combined.o"))
            cpp.write_text(program)
            flags = [compiler, "-std=c++11", "-Os", "-mcpu=cortex-m4", "-mthumb",
                     "-fuse-cxa-atexit", "-fno-exceptions", "-fno-rtti"]
            subprocess.run(flags + ["-c", str(cpp), "-o", str(application)], check=True, timeout=60)
            self.assertIn("__aeabi_atexit", subprocess.check_output([nm, "-u", str(application)], text=True))
            enabled = ["-DSTM32_PLATFORM=1", "-D" + MARKER + "=1", "-D" + POLICY.FLAG + "=1"]
            subprocess.run(flags + enabled + ["-c", str(SOURCE), "-o", str(hook)], check=True, timeout=60)
            subprocess.run([compiler, "-nostdlib", "-r", str(application), str(hook), "-o", str(combined)],
                           check=True, timeout=60)
            self.assertNotIn("__aeabi_atexit", subprocess.check_output([nm, "-u", str(combined)], text=True))
            for definitions in ([], ["-DSTM32_PLATFORM=1", "-D" + POLICY.FLAG + "=0"],
                                ["-DSTM32_PLATFORM=0", "-D" + MARKER + "=1", "-D" + POLICY.FLAG + "=1"],
                                ["-DNRF52_PLATFORM=1", "-D" + MARKER + "=1", "-D" + POLICY.FLAG + "=1"]):
                with self.subTest(definitions=definitions):
                    subprocess.run(flags + definitions + ["-c", str(SOURCE), "-o", str(hook)], check=True, timeout=60)
                    self.assertNotIn("__aeabi_atexit", subprocess.check_output([nm, str(hook)], text=True))
            for definitions in (["-DSTM32_PLATFORM=1", "-D" + POLICY.FLAG + "=1"],
                                ["-DSTM32_PLATFORM=1", "-D" + POLICY.FLAG + "=1", "-D" + MARKER + "=0"]):
                with self.subTest(unqualified=definitions):
                    result = subprocess.run(flags + definitions + ["-c", str(SOURCE), "-o", str(hook)],
                                            capture_output=True, text=True, timeout=60)
                    self.assertNotEqual(result.returncode, 0, "Manual flags must not bypass the ABI recipe guard")


if __name__ == "__main__":
    unittest.main()
