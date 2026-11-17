"""Qualify Mini process lifetime without changing ordinary C++ cleanup."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
FIXTURE = ROOT / "test/fixtures/stm32_program_lifetime/main.txt"
SOURCE = ROOT / "src/Stm32ProgramLifetime.cpp"


def load_policy(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = load_policy("stm32_mini_lifetime", "stm32_mini_program_lifetime.py")
ROLES = load_policy("stm32_mini_lifetime_roles", "nrf52_flash_trim.py")
REQUEST = "MESH_STM32_MINI_LIFETIME_POLICY"
FLAGS = ["-DSTM32_PLATFORM=1", "-D" + REQUEST + "=1"]


class Environment(dict):
    def BoardConfig(self):
        return {"build.mcu": self.get("test_mcu", "stm32wle5jc")}

    def PioPlatform(self):
        return self

    def get_package_dir(self, package):
        assert package == "framework-arduinoststm32"
        return self["framework"]

    def AppendUnique(self, **values):
        for key, items in values.items():
            for item in items:
                if item not in self.setdefault(key, []):
                    self[key].append(item)


def environment(framework, **changes):
    values = dict(framework=framework, PIOENV="wio-e5-mini_repeater",
                  SRC_FILTER=["+<../examples/simple_repeater>"],
                  BUILD_FLAGS=list(FLAGS), CPPDEFINES=[])
    values.update(changes)
    return Environment(values)


class Stm32MiniProgramLifetimeTest(unittest.TestCase):
    def make_framework(self, directory, source=None):
        main = Path(directory) / "cores/arduino/main.cpp"
        main.parent.mkdir(parents=True, exist_ok=True)
        main.write_bytes(FIXTURE.read_bytes() if source is None else source)
        return Path(directory)

    def test_only_exact_reviewed_nonreturning_main_qualifies(self):
        reviewed = FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(reviewed).hexdigest(), POLICY.MAIN_SHA256)
        self.assertIn(b"for (;;) {", reviewed)
        self.assertLess(reviewed.index(b"setup();"), reviewed.index(b"for (;;) {"))
        self.assertIn(b"loop();", reviewed)
        self.assertIn(b"serialEventRun();", reviewed)
        self.assertFalse(POLICY.supported_program_lifetime(None))
        self.assertFalse(POLICY.supported_program_lifetime(""))
        with tempfile.TemporaryDirectory(prefix="mini-lifetime-core-") as folder:
            self.assertFalse(POLICY.supported_program_lifetime(folder))
            for source, expected in ((reviewed, True),
                                     (reviewed.replace(b"\n", b"\r\n"), True),
                                     (reviewed + b"\n// changed SDK\n", False),
                                     (b"int main() { return 0; }\n", False)):
                with self.subTest(source=source[-32:]):
                    self.make_framework(folder, source)
                    self.assertEqual(POLICY.supported_program_lifetime(folder), expected)
                    env = environment(folder)
                    self.assertEqual(POLICY.install(env, ROLES), expected)
                    self.assertEqual((POLICY.FLAG, 1) in env["CPPDEFINES"], expected)

    def test_opt_in_is_limited_to_exact_mcu_platform_and_repeater_sources(self):
        with tempfile.TemporaryDirectory(prefix="mini-lifetime-role-") as folder:
            self.make_framework(folder)
            for name in ("wio-e5-mini_sensor", "wio-e5-mini_companion_radio_usb",
                         "wio-e5_repeater", "RAK_4631_repeater"):
                with self.subTest(environment=name):
                    env = environment(folder, PIOENV=name)
                    self.assertFalse(POLICY.install(env, ROLES))
                    self.assertEqual(env["CPPDEFINES"], [])
            env = environment(folder, BUILD_FLAGS=["-DSTM32_PLATFORM=1"])
            self.assertFalse(POLICY.install(env, ROLES))
            self.assertEqual(env["CPPDEFINES"], [])
            rejected = [dict(test_mcu="stm32wl55jc"),
                        dict(BUILD_FLAGS=["-D" + REQUEST + "=1"]),
                        dict(BUILD_FLAGS=["-DSTM32_PLATFORM=0", "-D" + REQUEST + "=1"]),
                        dict(SRC_FILTER=["+<../examples/simple_repeater>",
                                         "-<../examples/simple_repeater>"])]
            for other in ROLES.ROLE_SOURCES[1:] + ROLES.COMPANION_SOURCES:
                rejected.extend((dict(SRC_FILTER=["+<" + other + ">"]),
                                 dict(SRC_FILTER=["+<../examples/simple_repeater>",
                                                  "+<" + other + ">"])))
            for changes in rejected:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    POLICY.install(environment(folder, **changes), ROLES)

    def test_build_flags_and_cppdefines_overrides_cannot_bypass_unknown_core(self):
        disabled = [dict(CPPDEFINES=[(POLICY.FLAG, 0)]),
                    dict(CPPDEFINES=[(POLICY.FLAG, "(0)")]),
                    dict(BUILD_FLAGS=FLAGS + ["-D" + POLICY.FLAG + "=0"]),
                    dict(BUILD_FLAGS=FLAGS + ["-D", POLICY.FLAG + "='0'"]),
                    dict(BUILD_FLAGS=FLAGS + ["-U" + POLICY.FLAG],
                         CPPDEFINES=[(POLICY.FLAG, 1)]),
                    dict(BUILD_FLAGS=FLAGS + ["-D" + POLICY.FLAG + "=0"],
                         CPPDEFINES=[(POLICY.FLAG, 1)])]
        enabled = [dict(CPPDEFINES=[POLICY.FLAG]),
                   dict(CPPDEFINES=[(POLICY.FLAG, 1)]),
                   dict(BUILD_FLAGS=FLAGS + ["-D" + POLICY.FLAG + "=1"]),
                   dict(BUILD_FLAGS=" ".join(FLAGS) + " -D " + POLICY.FLAG + "=1"),
                   dict(BUILD_FLAGS=FLAGS + ["-D" + POLICY.FLAG + "=1"],
                        CPPDEFINES=[(POLICY.FLAG, 0)])]
        with tempfile.TemporaryDirectory(prefix="mini-lifetime-overrides-") as folder:
            self.make_framework(folder)
            for changes in disabled:
                with self.subTest(disabled=changes):
                    self.assertFalse(POLICY.install(environment(folder, **changes), ROLES))
            for changes in enabled:
                with self.subTest(enabled=changes):
                    self.assertTrue(POLICY.install(environment(folder, **changes), ROLES))
            self.make_framework(folder, b"int main() { return 0; }\n")
            self.assertFalse(POLICY.install(environment(folder), ROLES))
            for changes in disabled:
                with self.subTest(unknown_disabled=changes):
                    self.assertFalse(POLICY.install(environment(folder, **changes), ROLES))
            for changes in enabled:
                with self.subTest(unknown_enabled=changes), self.assertRaises(ValueError):
                    POLICY.install(environment(folder, **changes), ROLES)

    def test_automatic_delete_and_explicit_cleanup_keep_running(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("native G++ unavailable")
        program = r'''
#include <cassert>
extern "C" int __aeabi_atexit(void*, void (*)(void*), void*);
extern "C" int __cxa_atexit(void (*)(void*), void*, void*);
extern "C" void __cxa_finalize(void*);
unsigned closed = 0, destroyed = 0, callback = 0;
void cleanup(void* count) { ++*static_cast<unsigned*>(count); }
struct Resource {
  void close() { ++closed; }
  ~Resource() { close(); ++destroyed; }
};
int main() {
  { Resource automatic; }
  auto* allocated = new Resource;
  delete allocated;
  assert(closed == 2 && destroyed == 2);
  Resource explicit_close;
  explicit_close.close();
  assert(closed == 3 && destroyed == 2);
  unsigned dso = 0;
  assert(__aeabi_atexit(&callback, cleanup, &dso) == 0);
  __cxa_finalize(&dso);
  assert(callback == 0);
  // Positive control: ordinary finalization still invokes registered cleanup.
  assert(__cxa_atexit(cleanup, &callback, &dso) == 0);
  __cxa_finalize(&dso);
  assert(callback == 1);
}
'''
        with tempfile.TemporaryDirectory(prefix="mini-lifetime-cleanup-") as folder:
            main, binary = Path(folder) / "main.cpp", Path(folder) / "check"
            main.write_text(program, encoding="ascii")
            subprocess.run([compiler, "-std=c++11", "-Os", "-flto", "-DSTM32_PLATFORM=1",
                            "-DWIO_E5_MINI_COMPACT_UNSUPPORTED_AGC=1",
                            "-D" + POLICY.FLAG + "=1", str(main), str(SOURCE),
                            "-o", str(binary)], check=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_arm_cxa_registration_uses_the_qualified_abi_hook(self):
        compiler = shutil.which("arm-none-eabi-g++")
        if not compiler:
            candidates = sorted((CORE / "packages").glob(
                "toolchain-gccarmnoneeabi*/bin/arm-none-eabi-g++*"))
            compiler = next((str(path) for path in candidates if path.is_file()), None)
        if not compiler:
            if os.environ.get("MESHCORE_REQUIRE_ARM_COMPILER") == "1":
                self.fail("ARM compiler is required for the Mini ABI check")
            self.skipTest("ARM compiler unavailable")
        nm = str(Path(compiler).with_name("arm-none-eabi-nm" +
                 (".exe" if str(compiler).endswith(".exe") else "")))
        self.assertTrue(Path(nm).is_file(), "ARM nm is required with the compiler")
        program = """volatile unsigned destroyed;
struct Resource { ~Resource() { ++destroyed; } } global;
"""
        with tempfile.TemporaryDirectory(prefix="mini-lifetime-arm-") as folder:
            folder = Path(folder)
            main, application, hook, combined = (folder / name for name in
                ("main.cpp", "main.o", "hook.o", "combined.o"))
            main.write_text(program, encoding="ascii")
            flags = [compiler, "-std=c++11", "-Os", "-mcpu=cortex-m4", "-mthumb",
                     "-fuse-cxa-atexit", "-fno-exceptions", "-fno-rtti"]
            subprocess.run(flags + ["-c", str(main), "-o", str(application)], check=True, timeout=60)
            unresolved = subprocess.check_output([nm, "-u", str(application)], text=True)
            self.assertIn("__aeabi_atexit", unresolved)
            subprocess.run(flags + ["-DSTM32_PLATFORM=1", "-D" + POLICY.FLAG + "=1",
                "-DWIO_E5_MINI_COMPACT_UNSUPPORTED_AGC=1", "-c", str(SOURCE),
                "-o", str(hook)], check=True, timeout=60)
            subprocess.run([compiler, "-nostdlib", "-r", str(application), str(hook),
                            "-o", str(combined)], check=True, timeout=60)
            self.assertNotIn("__aeabi_atexit", subprocess.check_output(
                [nm, "-u", str(combined)], text=True))
            # Disabled and other-platform builds must not supply this ABI symbol.
            for definitions in ([], ["-DSTM32_PLATFORM=1", "-D" + POLICY.FLAG + "=0"],
                                ["-DNRF52_PLATFORM=1", "-D" + POLICY.FLAG + "=1"]):
                with self.subTest(definitions=definitions):
                    subprocess.run(flags + definitions + ["-c", str(SOURCE), "-o", str(hook)],
                                   check=True, timeout=60)
                    self.assertNotIn("__aeabi_atexit", subprocess.check_output(
                        [nm, str(hook)], text=True))


if __name__ == "__main__":
    unittest.main()
