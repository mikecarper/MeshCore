#!/usr/bin/env python3
"""Run the actual complete OTA core suite without invoking PlatformIO."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaDirectManagerTests(unittest.TestCase):
    def test_actual_ota_core_with_direct_interop_and_sanitizers(self):
        googletest = ROOT / ".pio/libdeps/native/googletest/googletest"
        self.assertTrue(googletest.is_dir())
        with tempfile.TemporaryDirectory(prefix="ota-direct-core-") as directory:
            directory = Path(directory)
            objects = []
            flags = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                     "-fno-pie", "-no-pie", "-g", "-O1"]
            for source in ("OtaTinf.c", "detools/detools.c"):
                obj = directory / (Path(source).name + ".o")
                built = subprocess.run([
                    "gcc", "-DOTA_TRANSPORT_DEFLATE_TEST=1", *flags,
                    "-c", str(ROOT / "src/helpers/ota" / source), "-o", str(obj)],
                    capture_output=True, text=True)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                objects.append(str(obj))
            sources = ("OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp",
                       "MerkleTree.cpp", "FirmwareInfo.cpp", "OtaDeflate.cpp",
                       "OtaApply.cpp", "MotaSourceSerial.cpp", "FolderMotaStore.cpp")
            binary = directory / "ota-core"
            built = subprocess.run([
                "g++", "-std=c++17", "-pthread", *flags,
                "-DOTA_TRANSPORT_DEFLATE_TEST=1", "-DOTA_FETCH_PIPELINE=4",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                "-I", str(googletest / "include"), "-I", str(googletest),
                str(ROOT / "test/test_ota/test_ota_core.cpp"),
                *[str(ROOT / "src/helpers/ota" / source) for source in sources],
                str(ROOT / "src/Utils.cpp"), str(googletest / "src/gtest-all.cc"),
                *objects, "-o", str(binary)], capture_output=True, text=True, timeout=180)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary), "--gtest_brief=1"],
                                     capture_output=True, text=True, timeout=90)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
