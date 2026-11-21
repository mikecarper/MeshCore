"""Exercise the RAK Companion collar using the existing tracker hardware fixture."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_client_acl_infrastructure import room_mail_crypto_arguments

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures"


class CompanionCollarTests(unittest.TestCase):
    def test_local_setup_delivery_replay_storage_and_gps(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            src = ROOT / "examples/companion_radio"
            shutil.copy(src / "DogTracker.cpp", work)
            header = (src / "MyMesh.h").read_text(encoding="ascii")
            start = header.index("  mesh::tracker::TrackerRecord dog;")
            fields = header[start:header.index("#endif", start)]
            fixture = (FIXTURES / "companion_dog_tracker.cpp").read_text(encoding="ascii")
            (work / "MyMesh.h").write_text(fixture.replace("@DOG_FIELDS@", fields), encoding="ascii")
            binary = work / "tracker"
            subprocess.run([compiler, "-std=c++17", "-DRAK_WISMESH_TAG", "-I", str(work),
                            "-I", str(FIXTURES), "-I", str(ROOT / "src"),
                            str(work / "DogTracker.cpp"), str(ROOT / "src/Packet.cpp"),
                            *room_mail_crypto_arguments("room"), "-o", str(binary)], check=True)
            subprocess.run([binary], check=True)


if __name__ == "__main__":
    unittest.main()
