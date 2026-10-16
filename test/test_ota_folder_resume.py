#!/usr/bin/env python3
"""Exercise actual folder store, manager, and CLI pull branch across host failures."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_ota_cli_listing import function

ROOT = Path(__file__).resolve().parents[1]


class OtaFolderResumeTest(unittest.TestCase):
    def compile_run(self, manager_source=None, expect_rejection=False):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        start = cli.index('  } else if (is_cmd(a, "pull|get|download", &rest)) {')
        end = cli.index('  // ---- discard the current session', start)
        harness = (ROOT / "test/fixtures/ota_folder_resume.cpp").read_text()
        helpers = "\n\n".join(function(cli, name) for name in ("is_cmd", "state_word"))
        harness = harness.replace("// @CLI_HELPERS@", helpers).replace("// @CLI_PULL@", cli[start:end])
        with tempfile.TemporaryDirectory(prefix="meshcore-ota-folder-resume-") as temporary:
            work = Path(temporary)
            source = work / "resume.cpp"
            source.write_text(harness)
            binary = work / "resume"
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if os.name != "nt" else [])
            manager = ROOT / "src/helpers/ota/OtaManager.cpp"
            if manager_source is not None:
                manager = work / "OtaManager.cpp"
                manager.write_text(manager_source)
            sources = ("FolderMotaStore.cpp", "OtaProtocol.cpp",
                       "MotaContainer.cpp", "MerkleTree.cpp")
            built = subprocess.run([
                compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-sign-compare", "-Wno-unused-variable",
                "-DOTA_SEEDER_ONLY=1", *sanitizers,
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                "-I", str(ROOT / "src/helpers/ota"),
                "-I", str(ROOT / "test"), str(source),
                str(manager),
                *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                str(ROOT / "src/Utils.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            if expect_rejection:
                self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("resume I/O fallback issued BEGIN and erased staging", checked.stderr)
                self.assertIn("Assertion", checked.stderr)
            else:
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("PASS: folder resume I/O failures preserve staging", checked.stdout)

    def test_failed_probe_preserves_staging_and_allows_later_resume(self):
        self.compile_run()

    def test_reintroduced_fresh_fetch_after_io_failure_is_rejected(self):
        source = (ROOT / "src/helpers/ota/OtaManager.cpp").read_text()
        unsafe = source.replace("    if (_fetch_error == FETCH_ERROR_STORAGE) {",
                                "    if (false) {", 1)
        self.assertNotEqual(source, unsafe)
        self.compile_run(unsafe, expect_rejection=True)


if __name__ == "__main__":
    unittest.main()
