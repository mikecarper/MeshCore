#!/usr/bin/env python3
"""Bounded RegionMap name export through the real role-facing wrappers."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/region_name_export.cpp"


def implementation():
    source = (ROOT / "src/helpers/RegionMap.cpp").read_text(encoding="ascii")
    signatures = (
        "static const char* skip_hash(", "RegionMap::RegionMap(",
        "bool RegionMap::is_name_char(", "RegionEntry* RegionMap::putRegion(",
        "RegionEntry* RegionMap::findByName(", "RegionEntry* RegionMap::findById(",
        "bool RegionMap::wouldCreateCycle(", "int RegionMap::exportNamesTo(",
    )
    generated = "\n".join(body(source, signature) for signature in signatures)
    text_helpers = (ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(encoding="ascii")
    generated += "\n" + body(text_helpers, "void StrHelper::strncpy(")
    for role, directory in (("repeater", "simple_repeater"),
                            ("room", "simple_room_server")):
        role_source = (ROOT / "examples" / directory / "MyMesh.cpp").read_text(encoding="ascii")
        # Keep the original production wrapper body and signature unchanged.
        # A namespace gives each minimal role adapter its original MyMesh name.
        generated += "\nnamespace " + role + " {\n"
        generated += "class MyMesh {\npublic:\nRegionMap& region_map;\n"
        generated += "explicit MyMesh(RegionMap& map) : region_map(map) {}\n"
        generated += "void getLocalScopes(char* buf, size_t len);\n};\n"
        generated += body(role_source, "void MyMesh::getLocalScopes(") + "\n}\n"
    return generated


class RegionNameExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise AssertionError("a native C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="meshcore-region-name-export-")
        work = Path(cls.temporary.name)
        (work / "region_under_test.inc").write_text(implementation(), encoding="ascii")
        cls.executable = work / "test"
        command = [
            compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-sign-compare",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-pie", "-no-pie", "-I", str(work),
            "-I", str(ROOT / "test/fixtures/region_map_transactions/mocks"),
            "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
            str(FIXTURE), "-o", str(cls.executable),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            cls.temporary.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_case(self, case):
        environment = os.environ.copy()
        environment["ASAN_OPTIONS"] = "detect_leaks=1:abort_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        result = subprocess.run([str(self.executable), case], capture_output=True,
                                text=True, timeout=60, env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: " + case, result.stdout)

    def test_healthy_production_capacities(self):
        self.run_case("healthy")

    def test_repeater_one_byte_destination(self):
        self.run_case("repeater-one-byte")

    def test_direct_one_byte_destination(self):
        self.run_case("direct-one-byte")

    def test_room_one_byte_destination(self):
        self.run_case("room-one-byte")

    def test_null_and_nonpositive_capacities(self):
        self.run_case("invalid")

    def test_capacity_and_filter_matrix(self):
        self.run_case("matrix")

    def test_role_wrapper_capacity_matrix(self):
        self.run_case("wrappers")


if __name__ == "__main__":
    unittest.main()
