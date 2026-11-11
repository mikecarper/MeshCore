"""Audit the RAK release inventory; optional source ZIP validation is offline."""
import binascii
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/mota'))
import motalib

DATA = json.loads((ROOT / 'docs/_data/otafix_rak_inventory.json').read_text(encoding='utf-8'))
EXPECTED = {
    ('2.4.3', 'wiscore_rak3401'): 'B993684E',
    ('2.4.3', 'wiscore_rak4631_board'): '8CD63C00',
    ('2.4.3', 'wiscore_rak4631_board_rak15001_slot_c'): '0189C671',
    ('2.4.6', 'wiscore_rak3401'): 'A9C9F190',
    ('2.4.6', 'wiscore_rak3401_rak13302_w25q16'): '7279BBB3',
    ('2.4.6', 'wiscore_rak4631_board'): 'F0045404',
    ('2.4.6', 'wiscore_rak4631_board_rak15001_slot_c'): '4BF8A1AF',
    ('2.4.6', 'wiscore_rak4631_w25q16'): '6D49014F',
    ('2.4.9', 'wiscore_rak3401'): 'FEEB920F',
    ('2.4.9', 'wiscore_rak3401_auto_NO_LORA_BOOTLOADER_UPDATE'): 'F441BE89',
    ('2.4.9', 'wiscore_rak3401_rak13302_w25q16'): 'AE90F75F',
    ('2.4.9', 'wiscore_rak4631_board'): 'A957950E',
    ('2.4.9', 'wiscore_rak4631_auto_NO_LORA_BOOTLOADER_UPDATE'): '7994CDFC',
    ('2.4.9', 'wiscore_rak4631_board_rak15001_slot_c'): '95BB6F09',
    ('2.4.9', 'wiscore_rak4631_w25q16'): 'EA34C51A',
    ('2.4.11', 'wiscore_rak3401_auto'): '3023B6AD',
    ('2.4.11', 'wiscore_rak4631_auto'): 'CD0863E0',
    ('2.4.12', 'wiscore_rak3401_auto'): '11750A13',
    ('2.4.12', 'wiscore_rak4631_auto'): 'FF374B1B',
}

class RakInventoryTest(unittest.TestCase):
    def test_expected_identities_and_versions(self):
        self.assertEqual(DATA['schema_version'], 1)
        actual = {(p['tag'].split('OTAFIX')[-1], p['profile']): p['manifest_crc32'] for p in DATA['profiles']}
        self.assertEqual(actual, EXPECTED)
        self.assertEqual(len(DATA['profiles']), len(EXPECTED))
        for p in DATA['profiles']:
            self.assertEqual(p['board_id'], '239A0029')
            self.assertEqual(p['app_base'], '00026000')
            self.assertEqual((p['layout_abi'], p['compat_flags']), (1, 0))
            self.assertEqual(int(p['target_id'],16), motalib.bootloader_target_id(int(p['board_id'],16),p['device_name']))
            self.assertEqual(p['asset_url'], f"https://github.com/{DATA['repository']}/releases/download/{p['tag']}/{p['asset']}")
            for key in ('asset_sha256','bootloader_region_sha256'):
                self.assertRegex(p[key], r'^[0-9a-f]{64}$')

    def test_successful_exact_tag_run_inventory(self):
        runs = {
            '0.11.0-OTAFIX2.4.3': (33023653687, 3),
            '0.11.0-OTAFIX2.4.6': (33992368624, 5),
            'v0.11.0-OTAFIX2.4.9': (36355397235, 7),
            'v0.11.0-OTAFIX2.4.11': (37066565552, 2),
            'v0.11.0-OTAFIX2.4.12': (39904580496, 2),
        }
        self.assertEqual(set(runs), {r['tag'] for r in DATA['releases']})
        for release in DATA['releases']:
            run, count = runs[release['tag']]
            self.assertEqual(release['actions_run_url'],
                f"https://github.com/{DATA['repository']}/actions/runs/{run}")
            self.assertEqual(release['run_artifact_zip_matches'], count)
            self.assertEqual(sum(p['tag'] == release['tag'] for p in DATA['profiles']), count)

    def test_independently_published_11_and_12_manifests(self):
        files = {'v0.11.0-OTAFIX2.4.11': ROOT/'test/fixtures/otafix_2_4_11_manifest.json',
                 'v0.11.0-OTAFIX2.4.12': ROOT/'docs/_data/bootloader_manifest.json'}
        release = {r['tag']: r for r in DATA['releases']}
        published = {
            'v0.11.0-OTAFIX2.4.11': 'd3e31993cf4aab2b479d3c3ba38d8b3cad524393f68ac2b999da3123e0726378',
            'v0.11.0-OTAFIX2.4.12': 'd2f62a4ec9b7c5643d8ed787d9e1710a2fabac34607a0bbb6ea5883a458e6df3',
        }
        for tag,path in files.items():
            self.assertEqual(release[tag]['manifest_sha256'], published[tag])
            raw=path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), release[tag]['manifest_sha256'])
            m=json.loads(raw)
            self.assertEqual(m['tag'],tag)
            profiles={p['id']:p for p in m['profiles']}
            for p in (x for x in DATA['profiles'] if x['tag']==tag):
                ref=profiles[p['profile']]
                self.assertEqual(ref['deviceName'],p['device_name'])
                self.assertEqual(ref['files']['zip']['name'],p['asset'])
                self.assertEqual(ref['files']['zip']['sha256'],p['asset_sha256'])
                self.assertEqual(ref['files']['zip']['url'],p['asset_url'])

    def test_optional_downloaded_release_bytes(self):
        directory=os.environ.get('MESHCORE_OTAFIX_ARCHIVE_DIR')
        if not directory: self.skipTest('Set MESHCORE_OTAFIX_ARCHIVE_DIR to the release ZIP directory')
        for p in DATA['profiles']:
            with self.subTest(profile=p['profile'],tag=p['tag']):
                raw=(Path(directory)/p['tag']/p['asset']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),p['asset_sha256'])
                import io
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    entry=json.loads(z.read('manifest.json'))['manifest']['softdevice_bootloader']
                    firmware=z.read(entry['bin_file'])
                    self.assertEqual(binascii.crc_hqx(firmware,0xffff),entry['init_packet_data']['firmware_crc16'])
                    bl=firmware[entry['sd_size']:]
                self.assertEqual(hashlib.sha256(bl).hexdigest(),p['bootloader_region_sha256'])
                ident=motalib.parse_bootloader_identity(bl)
                self.assertIsNotNone(ident)
                self.assertEqual((f'{ident.board_id:08X}',ident.device_name,f'{ident.crc32:08X}',f'{ident.app_base:08X}'),
                                 (p['board_id'],p['device_name'],p['manifest_crc32'],p['app_base']))
                self.assertEqual((ident.layout_abi,ident.compat_flags,f'{ident.boot_version:08X}'),
                                 (p['layout_abi'],p['compat_flags'],p['boot_version']))
                matches=[]
                for off in range(0,len(bl)-15,4):
                    if bl[off:off+8] == b'MOTABLDR':
                        abi,codec,caps=struct.unpack_from('<HHB',bl,off+8)
                        if abi in (2,3) and codec==5 and caps<=31 and bl[off+13:off+16]==b'\0\0\0':
                            matches.append((abi,codec,caps))
                self.assertEqual(matches,[(p['capability_abi'],p['codec_mask'],p['storage_caps'])])
                self.assertEqual(motalib.bootloader_optional_app_storage(bl),p['optional_app_storage'])

if __name__ == '__main__': unittest.main()
