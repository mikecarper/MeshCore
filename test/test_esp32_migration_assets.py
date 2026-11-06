#!/usr/bin/env python3
"""Verify standalone migration assets are bound to the qualified Full image."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from esp32_migration_assets import standalone_assets  # noqa: E402
import esp32_migration_assets as asset_module  # noqa: E402
from esp32_migration_expanders import TARGET_MARKER, evaluate_candidate, target_id_for_env  # noqa: E402
from motalib import CODEC_DETOOLS_SEQUENTIAL, build_container, build_manifest  # noqa: E402
from package_esp32_partition_migration import (  # noqa: E402
    FwIdent, LEGACY_SLOT_BYTES, bridge_with_successor_endf, build_endf, mota_full,
)
import build_esp32_partition_migration as recipe  # noqa: E402


class StandaloneMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.board, self.target = 'board-repeater', 'board_repeater'
        self.spec = {'target': self.target, 'usb_chip': 9,
                     'flash_bytes': 0x400000, 'slot_bytes': 0x1f0000}
        self.catalog = mock.patch.object(asset_module, 'BOARDS', {self.board: self.spec})
        self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.identity = FwIdent(0x01120001, target_id_for_env(self.target), 'board')
        app = b'normal-published-full'
        self.full = self.root / 'exact-target-full-v1.bin'
        self.full.write_bytes(app + build_endf(app, self.identity))
        self.version, self.source = 'v1.18.0.1', '12345678'
        image = self.candidate_header() + TARGET_MARKER + struct.pack('<I', self.identity.target_id) + b'expander'
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
            self.board, self.spec, self.bridge, self.version)
        self.path = self.root / 'internal.zip'

    def candidate_header(self):
        header = bytearray(24)
        header[:4] = b'\xe9\x01\x02\x20'
        struct.pack_into('<H', header, 12, self.spec['usb_chip'])
        return bytes(header)

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

    def test_asset_names_reject_traversal_and_invalid_source(self):
        for board, version, source in (("../escape", self.version, self.source),
                                       (self.board, "../../escape", self.source),
                                       (self.board, self.version, "../../etc")):
            with self.subTest(board=board, version=version, source=source):
                with self.assertRaisesRegex(ValueError, 'unsafe migration asset identity'):
                    standalone_assets(self.path, board, self.target, version, source, self.full)

    def test_catalog_target_and_chip_are_independently_checked(self):
        with self.assertRaisesRegex(ValueError, 'unknown or mismatched migration board target'):
            standalone_assets(self.path, self.board, 'different_repeater',
                              self.version, self.source, self.full)
        image = bytearray(self.candidate_header())
        struct.pack_into('<H', image, 12, 0)  # Classic ESP32 is not ESP32-S3.
        image.extend(TARGET_MARKER + struct.pack('<I', self.identity.target_id))
        image = bridge_with_successor_endf(bytes(image), self.identity)
        self.files['partition-expander.bin'] = image
        self.files['partition-expander.mota'] = mota_full(image, self.identity, 1024)
        # A faulty producer using a target-only spec could certify this image;
        # the publisher must recheck the real board's chip and flash capacity.
        self.manifest['expander_evaluation'] = evaluate_candidate(
            self.board, {'target': self.target}, image, self.version)
        with self.assertRaisesRegex(ValueError, 'chip or flash header'):
            self.assets()

    def test_mota_must_be_same_expander_and_target(self):
        other = b'other expander'
        self.files['partition-expander.mota'] = mota_full(
            bridge_with_successor_endf(other, self.identity), self.identity, 1024)
        with self.assertRaisesRegex(ValueError, 'payloads differ'):
            self.assets()

    def test_lora_must_be_full_not_a_verified_delta_or_non_full_codec(self):
        payload = bridge_with_successor_endf(self.bridge, self.identity)
        for is_full in (False, True):
            with self.subTest(is_full=is_full):
                manifest = build_manifest(
                    target_id=self.identity.target_id, fw_version=self.identity.fw_version,
                    image_size=len(payload), payload=payload, block_size=1024,
                    image_hash=hashlib.sha256(payload).digest(),
                    codec_id=CODEC_DETOOLS_SEQUENTIAL, is_full=is_full,
                    base_hash=None if is_full else b'baseline', hw_id=self.identity.hw_id)
                self.files['partition-expander.mota'] = build_container(manifest, payload)
                with self.assertRaisesRegex(ValueError, 'must be a full image'):
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
            self.candidate_header() + TARGET_MARKER + struct.pack('<I', self.identity.target_id)
            + b'firmware', self.identity)
        self.files['partition-expander.bin'] = image
        self.files['partition-expander.mota'] = mota_full(
            bridge_with_successor_endf(image, self.identity), self.identity, 1024)
        self.manifest['expander_evaluation'] = evaluate_candidate(
            self.board, self.spec, image, self.version)
        self.assertEqual(len(self.assets()), 3)
        self.manifest['expander_evaluation']['image_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'combined expander evidence'):
            self.assets()

    def test_oversize_fallback_requires_measured_exact_candidate(self):
        marker = self.candidate_header() + TARGET_MARKER + struct.pack('<I', self.identity.target_id)
        candidate = marker + b'x' * (LEGACY_SLOT_BYTES + 1 - len(marker))
        del self.files['partition-expander.bin'], self.files['partition-expander.mota']
        self.files.update({
            'expander-candidate.bin': candidate,
            'wifi-bridge.bin': b'small legacy wifi bridge',
            'lora-bridge.mota': mota_full(bridge_with_successor_endf(b'legacy lora', self.identity),
                                          self.identity, 1024),
        })
        self.manifest['expander_evaluation'] = evaluate_candidate(
            self.board, self.spec, candidate, self.version)
        assets = self.assets()
        self.assertEqual(json.loads(next(value for name, value in assets.items()
                                         if name.endswith('-migration.json')))['mode'], 'separate')
        self.assertEqual(len(assets), 3)
        self.assertFalse(any('candidate' in name for name in assets))
        self.manifest['expander_evaluation']['image_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.assets()


class AtomicMigrationPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage, self.output, self.builds = (self.root / name for name in ('stage', 'packages', 'builds'))
        self.stage.mkdir()
        self.builds.mkdir()
        self.boards = {'one': {'target': 'board_one'}, 'two': {'target': 'board_two'},
                       'unused': {'target': 'board_unused'}}
        self.version, self.source = 'v1.18.0.1', '12345678'
        for name in ('one', 'two'):
            (self.stage / f'{name}-{self.version}-{self.source}-migration.zip').write_bytes(name.encode())
            (self.builds / f'board_{name}-full-ota-{self.version}-{self.source}.bin').write_bytes(name.encode())
        self.patches = [mock.patch.object(recipe, 'BOARDS', self.boards),
                        mock.patch.object(recipe, 'verify_archive'),
                        mock.patch.object(recipe, 'standalone_assets', side_effect=lambda path, board, *args:
                                          {f'{board}-expander.bin': board.encode()})]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def publish(self):
        return recipe.publish_migration_outputs(
            self.stage, self.output, self.builds, self.root, ['one', 'two'],
            self.version, self.source, 'usa', 'cascade')

    def test_later_board_failure_leaves_no_partial_outputs(self):
        self.output.mkdir()
        existing = self.output / 'one-expander.bin'
        existing.write_bytes(b'one')
        (self.builds / f'board_two-full-ota-{self.version}-{self.source}.bin').unlink()
        with self.assertRaisesRegex(ValueError, 'one published Full'):
            self.publish()
        self.assertEqual(list(self.output.iterdir()), [existing])
        self.assertEqual(existing.read_bytes(), b'one')

    def test_publication_error_rolls_back_only_new_files(self):
        self.output.mkdir()
        existing = self.output / 'one-expander.bin'
        existing.write_bytes(b'one')
        real_link = os.link
        count = 0
        def fail_second(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('injected failure')
            return real_link(source, target)
        with mock.patch.object(recipe.os, 'link', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'injected failure'):
                self.publish()
        self.assertEqual(list(self.output.iterdir()), [existing])
        self.assertEqual(existing.read_bytes(), b'one')

    def test_bundle_failure_does_not_publish_other_board_outputs(self):
        self.boards.pop('unused')
        with mock.patch.object(recipe, 'bundle_release', side_effect=ValueError('invalid bundle')):
            with self.assertRaisesRegex(ValueError, 'invalid bundle'):
                self.publish()
        self.assertFalse(self.output.exists())

    def test_existing_later_conflict_is_detected_before_any_new_file(self):
        self.output.mkdir()
        conflict = self.output / 'two-expander.bin'
        conflict.write_bytes(b'other')
        with self.assertRaisesRegex(ValueError, 'existing migration output differs'):
            self.publish()
        self.assertEqual(list(self.output.iterdir()), [conflict])

    def test_existing_symlink_is_not_accepted_as_an_identical_asset(self):
        self.output.mkdir()
        outside = self.root / 'existing.bin'
        outside.write_bytes(b'one')
        link = self.output / 'one-expander.bin'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('symbolic-link creation is unavailable')
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.publish()
        self.assertEqual(outside.read_bytes(), b'one')
        self.assertEqual(list(self.output.iterdir()), [link])

    def test_complete_selection_publishes_each_image_and_archive(self):
        published = self.publish()
        self.assertEqual(len(published), 4)
        self.assertEqual((self.output / 'one-expander.bin').read_bytes(), b'one')
        self.assertEqual((self.output / 'two-expander.bin').read_bytes(), b'two')
        self.assertEqual(len(list(self.output.glob('*.zip'))), 2)


if __name__ == '__main__':
    unittest.main()
