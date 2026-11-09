"""Verify the C6 fallback's pinned bytes and exact PlatformIO identity."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('c6_fallback', ROOT / 'scripts/fetch_pinned_esp32_c6.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DATA = b'reviewed archive fixture'


class PinnedC6Tests(unittest.TestCase):
    def asset(self, key):
        return {**MODULE.ASSETS[key], 'size': len(DATA), 'sha256': hashlib.sha256(DATA).hexdigest()}

    def make_package(self, root, archive, asset, suffix=''):
        package = root / ('installed' + suffix)
        package.mkdir()
        spec = {'owner': None, 'id': None, 'name': 'download', 'requirements': None,
                'uri': 'file://' + archive.resolve().as_posix()}
        meta = {'type': asset['type'], 'name': asset['name'], 'version': asset['version'], 'spec': spec}
        (package / '.piopm').write_text(json.dumps(meta))
        (package / asset['manifest']).write_text(json.dumps({'name': asset['name'],
                                                             'version': asset['manifest_version']}))
        return package

    def test_both_assets_download_only_exact_pinned_bytes(self):
        for key in MODULE.ASSETS:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'asset.zip'
                seen = []
                def opener(request, timeout):
                    seen.append((request.full_url, request.get_header('Accept'), timeout))
                    return io.BytesIO(DATA)
                with patch.dict(MODULE.ASSETS, {key: self.asset(key)}):
                    MODULE.download_verified(key, path, opener=opener)
                    self.assertEqual(path.read_bytes(), DATA)
                    self.assertEqual(seen, [(MODULE.ASSETS[key]['api'], 'application/octet-stream', 60)])
                    with self.assertRaises(ValueError):
                        MODULE.download_verified(key, Path(tmp) / 'bad.zip',
                                                 opener=lambda *_args, **_kwargs: io.BytesIO(DATA + b'x'))
                    self.assertFalse((Path(tmp) / 'bad.zip').exists())

    def test_binding_is_unique_atomic_and_targets_exact_official_uri(self):
        for key in MODULE.ASSETS:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / 'asset with spaces.zip'
                archive.write_bytes(DATA)
                asset = self.asset(key)
                pkg = self.make_package(root, archive, asset)
                with patch.dict(MODULE.ASSETS, {key: asset}):
                    MODULE.bind_installed_package(key, archive, storage_dir=root)
                    meta = json.loads((pkg / '.piopm').read_text())
                    self.assertEqual(meta['spec']['uri'], asset['url'])
                    self.assertEqual(meta['spec']['name'], asset['spec_name'])
                    self.assertEqual(meta['spec']['owner'], asset['owner'])
                    self.assertEqual(list(pkg.glob('.piopm-*')), [])
                    # Binding a second time or binding unrelated package data fails closed.
                    with self.assertRaises(ValueError):
                        MODULE.bind_installed_package(key, archive, storage_dir=root)

    def test_tampered_duplicate_and_wrong_version_refuse_without_modification(self):
        for key in MODULE.ASSETS:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / 'asset.zip'
                archive.write_bytes(DATA)
                asset = self.asset(key)
                pkg = self.make_package(root, archive, asset)
                with patch.dict(MODULE.ASSETS, {key: asset}):
                    before = (pkg / '.piopm').read_bytes()
                    archive.write_bytes(DATA + b'x')
                    with self.assertRaises(ValueError):
                        MODULE.bind_installed_package(key, archive, storage_dir=root)
                    self.assertEqual((pkg / '.piopm').read_bytes(), before)
                    archive.write_bytes(DATA)
                    duplicate = self.make_package(root, archive, asset, '-duplicate')
                    with self.assertRaises(ValueError):
                        MODULE.bind_installed_package(key, archive, storage_dir=root)
                    self.assertEqual((pkg / '.piopm').read_bytes(), before)
                    (duplicate / asset['manifest']).write_text(json.dumps({'name': asset['name'], 'version': 'wrong'}))
                    (pkg / asset['manifest']).write_text(json.dumps({'name': asset['name'], 'version': 'wrong'}))
                    with self.assertRaises(ValueError):
                        MODULE.bind_installed_package(key, archive, storage_dir=root)
                    self.assertEqual((pkg / '.piopm').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
