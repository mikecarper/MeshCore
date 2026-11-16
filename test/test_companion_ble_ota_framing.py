import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BleOtaFramingTest(unittest.TestCase):
    def test_bounded_reassembly_and_generation_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = pathlib.Path(temp) / 'ble_ota_framing'
            subprocess.run(['g++', '-std=c++11', '-I', str(ROOT / 'src'),
                            str(ROOT / 'test/fixtures/browser_ota_socket/ble_ota_framing.cpp'),
                            '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
