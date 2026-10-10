#!/usr/bin/env python3
"""Exercise actual folder transactions against malicious/slow host streams."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MotaFolderDeadlineTest(unittest.TestCase):
    def compile_run(self, production, expect_rejection=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="meshcore-mota-folder-deadline-") as temporary:
            work = Path(temporary)
            source = work / "FolderMotaStore.cpp"
            source.write_text(production)
            binary = work / "deadline"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    *sanitizers, "-I" + str(ROOT / "src"),
                                    "-I" + str(ROOT / "src/helpers/ota"),
                                    "-isystem", str(ROOT / "test/mocks"),
                                    str(source), str(ROOT / "test/fixtures/mota_folder_deadline.cpp"),
                                    "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if expect_rejection:
                self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("Assertion", checked.stderr)
            else:
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_single_response_deadline_and_bounded_pre_request_drain(self):
        self.compile_run((ROOT / "src/helpers/ota/FolderMotaStore.cpp").read_text())

    def test_reintroduced_per_byte_timeout_is_rejected_at_runtime(self):
        source = (ROOT / "src/helpers/ota/FolderMotaStore.cpp").read_text()
        old = source.replace("bool FolderMotaStore::readByteT(uint8_t& b, uint32_t started) const {",
                             "bool FolderMotaStore::readByteT(uint8_t& b, uint32_t started) const {\n  started = millis();", 1)
        self.assertNotEqual(old, source)
        self.compile_run(old, expect_rejection=True)

    def test_reintroduced_unbounded_drain_is_rejected_at_runtime(self):
        source = (ROOT / "src/helpers/ota/FolderMotaStore.cpp").read_text()
        old = source.replace("  int pending = _io.available();\n  while (pending-- > 0) if (_io.read() < 0) break;",
                             "  while (_io.read() >= 0) {}", 1)
        self.assertNotEqual(old, source)
        self.compile_run(old, expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
