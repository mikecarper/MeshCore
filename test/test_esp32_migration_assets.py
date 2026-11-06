#!/usr/bin/env python3
"""Verify standalone migration assets are bound to the qualified Full image."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from esp32_migration_assets import standalone_assets  # noqa: E402
from esp32_migration_expanders import TARGET_MARKER, evaluate_candidate, target_id_for_env  # noqa: E402
from package_esp32_partition_migration import (  # noqa: E402
    FwIdent, LEGACY_SLOT_BYTES, bridge_with_successor_endf, build_endf, mota_full,
)


class StandaloneMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.board, self.target = 'board-repeater', 'board_repeater'
        self.identity = FwIdent(0x01120001, target_id_for_env(self.target), 'board')
        app = b'normal-published-full'
        self.full = self.root / 'exact-target-full-v1.bin'
        self.full.write_bytes(app + build_endf(app, self.identity))
        self.version, self.source = 'v1.18.0.1', '12345678'
        image = b'\xe9' + TARGET_MARKER + struct.pack('<I', self.identity.target_id) + b'expander'
        self.bridge = bridge_with_successor_endf(image, self.identity)
        self.files = {
            'full-application.bin': self.full.read_bytes(),
            'partition-expander.bin': self.bridge,
            'partition-expander.mota': mota_full(
                bridge_with_successor_endf(self.bridge, self.identity), self.identity, 1024),
        }
        self.manifest = {
            'board': self.board, 'target': self.target, 'role': 'repeater',
            'firmware_version': self.version, 'source_commit': self.source,
            'target_id': f'0x{self.identity.target_id:08x}', 'hardware_id': 'board',
            'legacy_slot_bytes': LEGACY_SLOT_BYTES, 'expanded_slot_bytes': 0x1f0000,
            'flash_bytes': 0x400000, 'partition_expander': 'board_partition_expander',
        }
        self.manifest['expander_evaluation'] = evaluate_candidate(
            self.board, {'target': self.target}, self.bridge, self.version)
        self.path = self.root / 'internal.zip'

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self):
        self.manifest['files'] = {name: {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                                  for name, value in self.files.items()}
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, value in self.files.items():
                archive.writestr(name, value)
            archive.writestr('manifest.json', json.dumps(self.manifest))

    def assets(self):
        self.write_archive()
        return standalone_assets(self.path, self.board, self.target,
                                 self.version, self.source, self.full)

    def test_combined_exact_target_has_raw_and_lora_transport_encodings(self):
        assets = self.assets()
        prefix = f'{self.board}-{self.version}-{self.source}'
        self.assertEqual(set(assets), {prefix + '-expander.bin', prefix + '-expander.mota',
                                       prefix + '-migration.json'})
        receipt = json.loads(assets[prefix + '-migration.json'])
        self.assertEqual(receipt['mode'], 'combined')
        self.assertEqual(receipt['full_application']['filename'], self.full.name)
        self.assertEqual(receipt['full_application']['sha256'],
                         hashlib.sha256(self.full.read_bytes()).hexdigest())
        self.assertNotIn('full-application.bin', assets)

    def test_missing_combined_never_silently_publishes_legacy_fallback(self):
        del self.files['partition-expander.bin'], self.files['partition-expander.mota']
        del self.manifest['expander_evaluation']
        self.assertEqual(self.assets(), {})

    def test_legacy_expander_without_evidence_is_not_promoted(self):
        del self.manifest['expander_evaluation']
        self.assertEqual(self.assets(), {})

    def test_mota_must_be_same_expander_and_target(self):
        other = b'other expander'
        self.files['partition-expander.mota'] = mota_full(
            bridge_with_successor_endf(other, self.identity), self.identity, 1024)
        with self.assertRaisesRegex(ValueError, 'payloads differ'):
            self.assets()

    def test_public_full_binding_must_match_exact_bytes(self):
        self.files['full-application.bin'] += b'not-the-published-image'
        with self.assertRaisesRegex(ValueError, 'Full differs'):
            self.assets()

    def test_incomplete_combined_pair_and_mismatched_target_are_errors(self):
        del self.files['partition-expander.mota']
        with self.assertRaisesRegex(ValueError, 'incomplete combined'):
            self.assets()
        self.files['partition-expander.mota'] = mota_full(
            bridge_with_successor_endf(self.bridge, self.identity), self.identity, 1024)
        self.manifest['target'] = 'different_repeater'
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            self.assets()

    def test_measured_combined_expander_evidence_is_rechecked(self):
        image = bridge_with_successor_endf(
            b'\xe9' + TARGET_MARKER + struct.pack('<I', self.identity.target_id) + b'firmware', self.identity)
        self.files['partition-expander.bin'] = image
        self.files['partition-expander.mota'] = mota_full(
            bridge_with_successor_endf(image, self.identity), self.identity, 1024)
        self.manifest['expander_evaluation'] = evaluate_candidate(
            self.board, {'target': self.target}, image, self.version)
        self.assertEqual(len(self.assets()), 3)
        self.manifest['expander_evaluation']['image_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'combined expander evidence'):
            self.assets()

    def test_oversize_fallback_requires_measured_exact_candidate(self):
        marker = b'\xe9' + TARGET_MARKER + struct.pack('<I', self.identity.target_id)
        candidate = marker + b'x' * (LEGACY_SLOT_BYTES + 1 - len(marker))
        del self.files['partition-expander.bin'], self.files['partition-expander.mota']
        self.files.update({
            'expander-candidate.bin': candidate,
            'wifi-bridge.bin': b'small legacy wifi bridge',
            'lora-bridge.mota': mota_full(bridge_with_successor_endf(b'legacy lora', self.identity),
                                          self.identity, 1024),
        })
        self.manifest['expander_evaluation'] = evaluate_candidate(
            self.board, {'target': self.target}, candidate, self.version)
        assets = self.assets()
        self.assertEqual(json.loads(next(value for name, value in assets.items()
                                         if name.endswith('-migration.json')))['mode'], 'separate')
        self.assertEqual(len(assets), 3)
        self.assertFalse(any('candidate' in name for name in assets))
        self.manifest['expander_evaluation']['image_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.assets()


if __name__ == '__main__':
    unittest.main()
