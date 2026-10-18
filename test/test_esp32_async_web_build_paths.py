"""Verify the actual middleware's cloned-library project-header dependency."""
import fnmatch
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("web_build_fix", ROOT / "scripts/esp32_async_web_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)


class Node:
    def __init__(self, path):
        self.path = Path(path)

    def srcnode(self):
        return self

    def get_abspath(self):
        return str(self.path.resolve())


class Clone:
    def __init__(self, source_dir, build_dir):
        self.variables = {"$PROJECT_DIR": str(ROOT), "$PROJECT_SRC_DIR": str(source_dir),
                          "$BUILD_DIR": str(build_dir)}
        self.paths = []
        self.middleware = []

    def subst(self, value):
        return self.variables[value]

    def PrependUnique(self, **values):
        self.paths = [p for p in values["CPPPATH"] if p not in self.paths] + self.paths

    def AppendUnique(self, **values):
        self.paths += [p for p in values["CPPPATH"] if p not in self.paths]

    def File(self, value):
        return Node(value)

    def AddBuildMiddleware(self, callback, pattern):
        self.middleware.append((callback, pattern))


class AsyncWebBuildPathTest(unittest.TestCase):
    def test_cached_and_direct_library_clones_can_include_current_project_header(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="ota-build-path-") as temporary:
            work = Path(temporary)
            source_dir = work / "custom application src"
            project_header = source_dir / "helpers/esp32/ManagementPassword.h"
            project_header.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "src/helpers/esp32/ManagementPassword.h", project_header)
            stale = work / "libdeps/target/AsyncElegantOTA/src/AsyncElegantOTA.cpp"
            stale.parent.mkdir(parents=True)
            stale.write_text("// obsolete cached dependency\n")
            direct = ROOT / "arch/esp32/AsyncElegantOTA/src/AsyncElegantOTA.cpp"
            for index, source in enumerate((stale, direct)):
                with self.subTest(source=source.name, cached=index == 0):
                    clone = Clone(source_dir, work / ("build-" + str(index)))
                    FIX.install(clone)
                    matches = [(callback, pattern) for callback, pattern in clone.middleware
                               if fnmatch.fnmatch(str(source), pattern)]
                    self.assertEqual(len(matches), 1)
                    patched = matches[0][0](clone, Node(source)).path
                    self.assertEqual(patched.read_bytes(), direct.read_bytes())
                    for header in ("AsyncElegantOTA.h", "Hash.h", "elegantWebpage.h"):
                        self.assertEqual((patched.parent / header).read_bytes(), (direct.parent / header).read_bytes())
                    self.assertIn(str(source_dir), clone.paths)
                    probe = work / "probe.cpp"
                    probe.write_text('#define MESH_MANAGEMENT_PASSWORD_HOST_TEST 1\n'
                                     '#include <helpers/esp32/ManagementPassword.h>\n'
                                     'int main() { return mesh::esp32::managementPassword::validPassword("password") ? 0 : 1; }\n')
                    command = [compiler, "-std=c++11", *["-I" + path for path in clone.paths],
                               "-fsyntax-only", str(probe)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    # Recreate the failed production clone by omitting only
                    # the project src path. It must fail at the exact header.
                    result = subprocess.run([part for part in command if part != "-I" + str(source_dir)],
                                            capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("helpers/esp32/ManagementPassword.h", result.stderr)
                    self.assertEqual(stale.read_text(), "// obsolete cached dependency\n")


if __name__ == "__main__":
    unittest.main()
