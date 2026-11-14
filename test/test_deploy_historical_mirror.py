#!/usr/bin/env python3
"""Exercise safe installation of the pinned public historical mirror."""

import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deploy_historical_mirror', ROOT / 'scripts/deploy_historical_mirror.py')
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)
digest = lambda data: hashlib.sha256(data).hexdigest()


def part(data):
    packed = gzip.compress(data, mtime=0)
    sha = digest(data)
    return {'sha256': sha, 'size': len(data), 'gzipSha256': digest(packed),
            'gzipSize': len(packed), 'path': sha + '.gz'}, packed


def fixture():
    one, one_bytes = part(b'firmware image bytes')
    left, left_bytes = part(b'first part ')
    right, right_bytes = part(b'second part')
    base = 'https://github.com/mikecarper/MeshCore/releases/download/' + mirror.FAMILY + '/'
    index = {'schemaVersion': 1, 'warnings': [], 'entries': [
        {'url': base + 'exact.uf2', **one},
        {'url': base + 'multipart.zip', 'sha256': digest(b'first part second part'),
         'size': len(b'first part second part'), 'parts': [left, right]},
    ]}
    files = {one['path']: one_bytes, left['path']: left_bytes, right['path']: right_bytes}
    return index, files


def make_archive(index, files, extra=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        all_files = {'index.json': json.dumps(index).encode(), **files}
        for name, data in all_files.items():
            info = tarfile.TarInfo(name); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if extra:
            info, data = extra
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


class HistoricalMirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='historical-mirror-test-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def install(self, data, sha=None, size=None):
        source = self.root / 'source.tar'; source.write_bytes(data)
        destination = self.root / 'site' / 'firmware-1.18.0.1' / 'firmware'
        result = mirror.install_archive(source, destination, sha or digest(data), size or len(data))
        return destination, result

    def test_verified_single_and_multipart_assets(self):
        index, files = fixture()
        target, result = self.install(make_archive(index, files))
        self.assertEqual(result, (2, 3))
        self.assertEqual(set(x.name for x in target.iterdir()), set(files) | {'index.json'})

    def test_wrong_archive_pin_leaves_no_mirror(self):
        index, files = fixture()
        with self.assertRaisesRegex(ValueError, 'pinned'):
            self.install(make_archive(index, files), sha='0' * 64)
        self.assertFalse((self.root / 'site').exists())

    def test_rejects_path_traversal_link_and_duplicate(self):
        index, files = fixture()
        for name, kind in [('../escape', tarfile.REGTYPE), ('a' * 64 + '.gz', tarfile.SYMTYPE),
                           ('index.json', tarfile.REGTYPE)]:
            with self.subTest(name=name, kind=kind):
                info = tarfile.TarInfo(name); info.type = kind
                data = None if kind == tarfile.SYMTYPE else b'x'
                if data: info.size = len(data)
                if kind == tarfile.SYMTYPE: info.linkname = '../escape'
                with self.assertRaises(ValueError):
                    self.install(make_archive(index, files, (info, data)))
                self.assertFalse((self.root / 'escape').exists())
                self.assertFalse((self.root / 'site/firmware-1.18.0.1/firmware').exists())

    def test_rejects_bad_source_part_and_whole_asset_hash(self):
        for fault in ('url', 'part', 'whole', 'extra'):
            with self.subTest(fault=fault):
                index, files = fixture()
                if fault == 'url': index['entries'][0]['url'] = 'https://example.com/untrusted.uf2'
                if fault == 'part':
                    path = index['entries'][0]['path']; files[path] = files[path][:-1] + bytes([files[path][-1] ^ 1])
                if fault == 'whole': index['entries'][1]['sha256'] = '0' * 64
                if fault == 'extra': files['a' * 64 + '.gz'] = gzip.compress(b'extra', mtime=0)
                with self.assertRaises((ValueError, OSError, EOFError)):
                    self.install(make_archive(index, files))
                self.assertFalse((self.root / 'site/firmware-1.18.0.1/firmware').exists())

    def test_existing_destination_is_never_overwritten(self):
        index, files = fixture()
        target = self.root / 'site/firmware-1.18.0.1/firmware'
        target.mkdir(parents=True); (target / 'preserve').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.install(make_archive(index, files))
        self.assertEqual((target / 'preserve').read_text(), 'keep')

    def test_symlink_ancestor_is_not_followed(self):
        index, files = fixture()
        (self.root / 'actual').mkdir()
        (self.root / 'site').symlink_to(self.root / 'actual', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink ancestor'):
            self.install(make_archive(index, files))
        self.assertEqual(list((self.root / 'actual').iterdir()), [])

    def test_verified_cache_hit_never_redownloads_and_corruption_fails_closed(self):
        index, files = fixture()
        data = make_archive(index, files)
        cache = self.root / 'cache'
        output = self.root / 'site/firmware-1.18.0.1/firmware'
        pin = digest(data)
        args = ['deploy_historical_mirror.py', '--url', 'https://unused.example/verified-by-stub',
                '--sha256', pin, '--size', str(len(data)), '--destination', str(output), '--cache-dir', str(cache)]
        def fake_download(_url, _size, _sha256, path):
            Path(path).write_bytes(data)
        with patch.object(mirror, 'download', side_effect=fake_download) as fetch, patch('sys.argv', args):
            mirror.main()
            self.assertEqual(fetch.call_count, 1)
        self.assertTrue((cache / (pin + '.tar')).is_file())
        import shutil
        shutil.rmtree(output)
        with patch.object(mirror, 'download', side_effect=AssertionError('cache should be used')), patch('sys.argv', args):
            mirror.main()
        shutil.rmtree(output)
        (cache / (pin + '.tar')).write_bytes(b'corrupt')
        with patch.object(mirror, 'download', side_effect=AssertionError('do not overwrite a bad cache')), patch('sys.argv', args):
            with self.assertRaisesRegex(ValueError, 'pinned'):
                mirror.main()
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
