"""Verify build selection before PlatformIO clones project compilation flags."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class SelectionTests(unittest.TestCase):
    def test_exact_sdk_only_and_no_nimble_or_non_ble_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'scripts').mkdir()
            stock = root/'tools/sdk/esp32s3/lib/libbt.a'
            stock.parent.mkdir(parents=True)
            stock.write_bytes(b'known sdk')
            (root/'scripts/bluedroid_bond_manifest.json').write_text(json.dumps({
                'mcus': {'esp32s3': {'stock_sha256': hashlib.sha256(stock.read_bytes()).hexdigest()}}}))

            class Env(dict):
                def ParseFlags(self, value): return {'CPPDEFINES': self['defines']}
                def BoardConfig(self): return {'build.mcu': self.get('mcu', 'esp32s3')}
                def subst(self, value): return str(root)
                def PioPlatform(self): return self
                def get_package_dir(self, name): return str(root)
                def AppendUnique(self, **values): self.update(values)

            def select(defines, mcu='esp32s3'):
                env = Env(defines=defines, mcu=mcu)
                with patch.dict('os.environ', {'MESHCORE_BLUEDROID_BOND_PATCH': ''}):
                    runpy.run_path(str(ROOT/'scripts/bluedroid_bond_enable.py'),
                                   init_globals={'Import': lambda _: None, 'env': env})
                return env.get('BUILD_FLAGS', [])

            self.assertIn('-D MESH_BLUEDROID_BOND_PROTECTION=1', select([('BLE_PIN_CODE', 123456)]))
            self.assertEqual(select([]), [])
            self.assertEqual(select([('BLE_PIN_CODE', 123456), ('MESH_USE_NIMBLE_ARDUINO', 1)]), [])
            self.assertEqual(select([('BLE_PIN_CODE', 123456)], 'esp32'), [])
            stock.write_bytes(b'new incompatible sdk')
            self.assertEqual(select([('BLE_PIN_CODE', 123456)]), [])

if __name__ == '__main__':
    unittest.main()
