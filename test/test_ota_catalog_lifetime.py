#!/usr/bin/env python3
"""Exercise OTA catalog lifetime, rollover, and filtered-page recovery."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaCatalogLifetimeTest(unittest.TestCase):
    def test_source_and_catalog_ownership_survive_clock_rollover(self):
        with tempfile.TemporaryDirectory(prefix="ota-catalog-") as directory:
            path = Path(directory)
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
            tinf = path / "tinf.o"
            subprocess.run([shutil.which("cc") or "gcc", "-DENABLE_OTA=1", *flags,
                            "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"),
                            "-o", str(tinf)], check=True)
            sources = ["OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp",
                       "MerkleTree.cpp", "OtaDeflate.cpp"]
            binary = path / "catalog.exe"
            result = subprocess.run([
                "c++", "-std=c++17", *flags, "-DENABLE_OTA=1",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                str(ROOT / "test/fixtures/ota_catalog_lifetime/test.cpp"),
                *[str(ROOT / "src/helpers/ota" / name) for name in sources],
                str(ROOT / "src/Utils.cpp"), str(tinf), "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for rollover in (0, 1):
                for scenario in range(9):
                    with self.subTest(scenario=scenario, rollover=rollover):
                        subprocess.run([str(binary), str(scenario), str(rollover)],
                                       check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
