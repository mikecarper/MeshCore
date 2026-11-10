#!/usr/bin/env python3
"""Compile the reviewed sensor decoder with SNMP's formerly colliding type."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sensirion_type_fix", ROOT / "scripts/sensirion_integer_type_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
FIXTURE = ROOT / "test/fixtures/sensirion_core_073"


class FakeNode:
    def __init__(self, path): self.path = str(path)
    def srcnode(self): return self
    def get_abspath(self): return self.path


class FakeEnvironment:
    def __init__(self, root, build, env="test"):
        self.values = {"$PROJECT_LIBDEPS_DIR": str(root), "$BUILD_DIR": str(build), "$PIOENV": env}
        self.includes = []
    def subst(self, value): return self.values[value]
    def PrependUnique(self, **values):
        self.includes = values["CPPPATH"] + [v for v in self.includes if v not in values["CPPPATH"]]
    def File(self, value): return FakeNode(value)


class SensirionTypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sensirion-type-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "deps/test/Sensirion Core"
        shutil.copytree(FIXTURE, self.library)
        FIX._OVERLAYS.clear()

    def test_only_reviewed_type_tokens_change_and_dependencies_remain_unchanged(self):
        before = {p.relative_to(self.library): p.read_bytes() for p in self.library.rglob("*") if p.is_file()}
        output = FIX.private_overlay(self.library, self.root / "build")
        for name in FIX.SOURCE_HASHES:
            original = (self.library / "src" / name).read_text()
            self.assertEqual((output / name).read_text(), original.replace("IntegerType", "SensirionIntegerType"))
        for relative, content in before.items():
            self.assertEqual((self.library / relative).read_bytes(), content)
        self.assertEqual((output / "SensirionErrors.h").read_bytes(), (self.library / "src/SensirionErrors.h").read_bytes())
        self.assertEqual(FIX.private_overlay(self.library, self.root / "build"), output)

    def test_changed_library_version_or_source_is_rejected(self):
        metadata = self.library / "library.properties"
        original = metadata.read_text()
        metadata.write_text(original.replace("version=0.7.3", "version=0.7.4"))
        with self.assertRaisesRegex(RuntimeError, "unreviewed library"):
            FIX.private_overlay(self.library, self.root / "build")
        metadata.write_text(original)
        source = self.library / "src/SensirionRxFrame.h"
        source.write_text(source.read_text() + "\n// upstream edit\n")
        with self.assertRaisesRegex(RuntimeError, "changed reviewed source"):
            FIX.private_overlay(self.library, self.root / "build")

    def test_core_sources_and_consumer_clones_resolve_same_private_header_tree(self):
        # A second Core source with a quoted sibling include demonstrates why
        # every Core cpp must move, not just the file containing IntegerType.
        sibling = self.library / "src/SecondCore.cpp"
        sibling.write_text('#include "SensirionRxFrame.h"\n')
        for path in (self.library / "src/SensirionRxFrame.cpp", sibling, self.root / "consumer.cpp"):
            env = FakeEnvironment(self.root / "deps", self.root / "build")
            node = FakeNode(path)
            result = FIX.replace_sensirion_source(env, node)
            overlay = self.root / "build/patched-sensirion-core"
            self.assertEqual(env.includes[0], str(overlay))
            if path.parent == self.library / "src":
                self.assertEqual(result.path, str(overlay / path.name))
                self.assertTrue(Path(result.path).is_file())
            else:
                self.assertIs(result, node)

    def test_environment_without_sensor_library_is_unchanged(self):
        env = FakeEnvironment(self.root / "missing", self.root / "build")
        node = FakeNode(self.root / "ordinary.cpp")
        self.assertIs(FIX.replace_sensirion_source(env, node), node)
        self.assertEqual(env.includes, [])

    def test_lto_coexistence_and_integer_decode_without_warning_suppression(self):
        overlay = FIX.private_overlay(self.library, self.root / "build")
        program = self.root / "main.cpp"
        program.write_text(r'''
#include "SensirionRxFrame.h"
#include <cassert>
#include <type_traits>
// SNMP_Agent's BER header owns this distinct global class name.
class IntegerType { public: virtual ~IntegerType() = default; int value = 42; };
class SensirionI2CCommunication {
public: static void received(SensirionRxFrame& frame, unsigned count) { frame._numBytes = count; }
};
int main() {
  static_assert(sizeof(SensirionIntegerType) == sizeof(uint16_t), "enum ABI width");
  static_assert(Byte == 1 && Short == 2 && Integer == 4 && LongInteger == 8, "wire widths");
  uint8_t raw[] = {0x12, 0x34};
  SensirionRxFrame frame(raw, sizeof raw);
  SensirionI2CCommunication::received(frame, sizeof raw);
  uint8_t decoded[] = {0, 0};
  assert(frame.getInteger(decoded, Short, 2) == 0);
  assert(decoded[0] == 0x34 && decoded[1] == 0x12);
  IntegerType snmp;
  assert(snmp.value == 42);
}
''')
        executable = self.root / "check"
        result = subprocess.run(["c++", "-std=c++17", "-O2", "-flto", "-Wall", "-Wextra",
                                 "-Werror=odr", "-Werror=lto-type-mismatch", "-I", str(overlay),
                                 str(program), str(overlay / "SensirionRxFrame.cpp"), "-o", str(executable)],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("one definition rule", result.stderr.lower())
        subprocess.run([str(executable)], check=True, timeout=10)
        # The unchanged header cannot coexist with that class in one TU;
        # this verifies the test actually exercises the conflicting name.
        failed = subprocess.run(["c++", "-std=c++17", "-fsyntax-only", "-I", str(self.library / "src"),
                                 str(program)], text=True, capture_output=True, timeout=30)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("IntegerType", failed.stderr)


if __name__ == "__main__":
    unittest.main()
