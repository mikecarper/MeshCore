"""Independent HKDF and ChaCha20-Poly1305 check for the unwired DM prototype."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaDmAeadTest(unittest.TestCase):
    def test_real_library_against_independent_crypto(self):
        compiler = os.environ.get('CXX') or shutil.which('c++') or shutil.which('g++')
        candidates = [Path(os.environ.get('MESHCORE_CRYPTO_DIR', '/nonexistent'))]
        candidates += sorted((ROOT / '.pio/libdeps').glob('*/Crypto'))
        crypto = next((p for p in candidates if (p / 'ChaChaPoly.cpp').is_file() and (p / 'HKDF.cpp').is_file()), None)
        try:
            from Crypto.Cipher import ChaCha20_Poly1305
            from Crypto.Hash import SHA256
            from Crypto.Protocol.KDF import HKDF
        except ImportError:
            HKDF = None
        if not compiler or not crypto or HKDF is None:
            if os.environ.get('MESHCORE_REQUIRE_DM_AEAD') == '1':
                self.fail('C++ compiler, rweather Crypto 0.4.0 and Python pycryptodome are required')
            self.skipTest('C++ compiler, rweather Crypto or Python pycryptodome unavailable')
        with tempfile.TemporaryDirectory(prefix='ota-dm-aead-') as folder:
            binary = Path(folder) / ('test.exe' if os.name == 'nt' else 'test')
            sources = ['ChaChaPoly.cpp','ChaCha.cpp','Poly1305.cpp','AuthenticatedCipher.cpp',
                       'Cipher.cpp','HKDF.cpp','Crypto.cpp','SHA256.cpp','Hash.cpp']
            args = [compiler, '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-I'+str(ROOT/'src'), '-I'+str(crypto), '-I'+str(ROOT/'test/mocks'),
                    str(ROOT/'test/fixtures/ota_dm_aead.cpp'), *[str(crypto/name) for name in sources], '-o', str(binary)]
            result = subprocess.run(args, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines(); frame = bytes.fromhex(lines[0]); header = frame[:33]
        session = bytes([0x44])*16; sender = bytes([0x22])*32; receiver = bytes([0x33])*32
        key = HKDF(bytes([0x11])*32, 32, session, SHA256,
                   context=b'MeshCore OTA DM AEAD key v1\0'+sender+receiver)
        nonce = bytes(8)+(1).to_bytes(4,'little')
        aad = b'MeshCore OTA DM AEAD frame v1\0'+sender+receiver+header
        cipher=ChaCha20_Poly1305.new(key=key,nonce=nonce);cipher.update(aad)
        self.assertEqual(cipher.decrypt_and_verify(frame[33:-16], frame[-16:]), bytes(range(118)))
        self.assertEqual(lines[1], 'PASS experimental OTA DM AEAD')


if __name__ == '__main__': unittest.main()
