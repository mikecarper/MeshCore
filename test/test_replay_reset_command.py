#!/usr/bin/env python3
"""Native, hardware-free tests of replay-reset parsing and one-use challenges."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReplayResetCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a host C++ compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="mesh-replay-reset-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.executable = Path(cls.directory.name) / (
            "replay-reset.exe" if os.name == "nt" else "replay-reset"
        )
        subprocess.run(
            [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-I", str(ROOT / "src"),
             str(ROOT / "test/fixtures/replay_reset_command/main.cpp"),
             "-o", str(cls.executable)],
            check=True,
        )

    def run_case(self, name):
        subprocess.run([str(self.executable), name], check=True)

    def test_strict_parser_and_family_classification(self):
        self.run_case("parser")

    def test_one_use_identity_bound_lifecycle(self):
        self.run_case("lifecycle")

    def test_expiration_rollover_and_clock_changes(self):
        self.run_case("time")

    def test_invalid_arguments_and_complete_binding(self):
        self.run_case("invalid")


if __name__ == "__main__":
    unittest.main()
