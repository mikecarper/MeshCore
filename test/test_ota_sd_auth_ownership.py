#!/usr/bin/env python3
"""Execute the production SD auth helpers and constructor with RAM canaries."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/ota/OtaStoreSdNrf52.cpp"


class OtaSdAuthOwnershipTest(unittest.TestCase):
    def test_source_and_receiver_ownership_and_queue_lifetimes(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("fixed-address RAM canaries require Linux mmap")
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        source = SOURCE.read_text(encoding="utf-8")
        helpers = source.split("namespace {", 1)[1].split("} // namespace", 1)[0]
        constructor = extract_braced(source, "OtaStoreSdNrf52::OtaStoreSdNrf52()")
        cases = (
            ("source_unreserved", True, None, None, False),
            ("source_reserved", True, 0x20006008, 0x20006050, False),
            ("receiver_unreserved", False, None, None, False),
            ("receiver_wrong_start", False, 0x20006000, 0x20006050, False),
            ("receiver_wrong_end", False, 0x20006008, 0x20006058, False),
            ("receiver_reserved", False, 0x20006008, 0x20006050, True),
        )
        with tempfile.TemporaryDirectory(prefix="meshcore-sd-auth-owner-") as directory:
            work = Path(directory)
            generated = work / "production_auth.inc"
            generated.write_text(helpers + "\n" + constructor + "\n", encoding="ascii")
            for name, seeder, start, end, owns in cases:
                with self.subTest(case=name):
                    binary = work / name
                    flags = ["-DOTA_RAK15002_SD_STORE=1", "-DEXPECT_OWNS=" + str(int(owns))]
                    if seeder:
                        flags += ["-DOTA_SEEDER_ONLY=1"]
                    if start is not None:
                        flags += [f"-DTEST_AUTH_START={start}", f"-DTEST_AUTH_END={end}"]
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                        "-fno-pie", "-no-pie", "-g", *flags,
                        "-I", str(ROOT / "src"), "-I", str(work),
                        str(ROOT / "test/fixtures/ota_sd_auth_ownership.cpp"),
                        "-o", str(binary),
                    ], text=True, capture_output=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("SD auth ownership and 64 queue lifetimes passed", checked.stdout)


if __name__ == "__main__":
    unittest.main()
