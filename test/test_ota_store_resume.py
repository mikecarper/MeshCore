#!/usr/bin/env python3
"""Run OTA staged-file validation and real ESP32 flash resume on the host."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/ota_store_resume"


class OtaStoreResumeTest(unittest.TestCase):
    def test_container_envelope_and_flash_resume(self):
        with tempfile.TemporaryDirectory(prefix="ota-resume-") as directory:
            path = Path(directory)
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
            tinf = path / "tinf.o"
            subprocess.run([shutil.which("cc") or "gcc", "-DENABLE_OTA=1", *flags,
                            "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"),
                            "-o", str(tinf)], check=True)
            sources = ["OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp",
                       "MerkleTree.cpp", "OtaDeflate.cpp", "OtaStoreFlashEsp32.cpp"]
            binary = path / "resume.exe"
            result = subprocess.run([
                "c++", "-std=c++17", *flags, "-DENABLE_OTA=1", "-DESP32_PLATFORM=1",
                "-DOTA_FLASH_STORE=1", "-DOTA_FETCH_PIPELINE=4", "-I", str(FIXTURE / "mocks"),
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                str(FIXTURE / "test.cpp"),
                *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                str(ROOT / "src/Utils.cpp"), str(tinf), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for scenario in range(50):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(binary), str(scenario)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
