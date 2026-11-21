"""Native accessory tracker frames use the Keymind Cascade RF wire format."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_client_acl_infrastructure import room_mail_crypto_arguments

ROOT = Path(__file__).resolve().parents[1]


class NativeWireTests(unittest.TestCase):
    def test_tracker_exchange_with_distinct_identities(self):
        fixture = ROOT / "test/fixtures/rak_findmy_mesh_wire.cpp"
        port = ROOT / "examples/rak_findmy"
        ed = ROOT / "lib/ed25519"
        crypto_args = room_mail_crypto_arguments("room")
        crypto = Path(next(a[2:] for a in crypto_args if a.startswith("-I")))
        with tempfile.TemporaryDirectory() as temp:
            objects = []
            for src in ("sha512", "keypair", "key_exchange", "sc", "fe", "ge"):
                obj = Path(temp) / (src + ".o")
                subprocess.run([os.environ.get("CC") or shutil.which("cc"), "-O1", "-I", str(ed),
                                "-c", str(ed / (src + ".c")), "-o", str(obj)], check=True)
                objects.append(str(obj))
            binary = Path(temp) / "wire"
            subprocess.run([os.environ.get("CXX") or shutil.which("c++"), "-std=c++17", "-O1",
                            "-I", str(ROOT / "src"), "-I", str(ed), "-I", str(port / "compat"),
                            "-I", str(port / "src"), str(fixture), str(ROOT / "src/Utils.cpp"),
                            str(ROOT / "src/Packet.cpp"), *crypto_args, *objects,
                            *(str(crypto / src) for src in ("AES128.cpp", "AESCommon.cpp", "BlockCipher.cpp")),
                            "-o", str(binary)], check=True)
            subprocess.run([binary], check=True)


if __name__ == "__main__":
    unittest.main()
