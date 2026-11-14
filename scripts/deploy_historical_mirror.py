#!/usr/bin/env python3
"""Install one pinned, verified public 1.18.0.1 mirror into a Pages build."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


FAMILY = 'v1.18.0.1-halo-keymind-cascade-dev-03214a16'
TAGS = {FAMILY, *(prefix + FAMILY for prefix in
                 ('lora-ota-', 'lora-ota-2-', 'lora-ota-3-', 'full-profiles-', 'utility-'))}
SHARED = ('https://github.com/mikecarper/MeshCore/releases/download/'
          'utility-usb-partition-v1-3d3d5827/meshcore-usb-partition-bridges.zip')
MAX_ARCHIVE = 850 * 1024 * 1024
MAX_PIECE = 64 * 1024 * 1024
MAX_INDEX = 4 * 1024 * 1024
MAX_ENTRIES = 10000
HASH = re.compile(r'^[0-9a-f]{64}$')
PIECE = re.compile(r'^[0-9a-f]{64}\.gz$')
FILENAME = re.compile(r'^[A-Za-z0-9_.-]{1,256}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_url(value):
    if value == SHARED:
        return True
    if not isinstance(value, str) or len(value) > 2048:
        return False
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return False
    if parsed.netloc == 'api.github.com':
        return parsed.path == '/repos/mikecarper/MeshCore/releases/tags/utility-' + FAMILY
    if parsed.netloc != 'github.com':
        return False
    prefix = '/mikecarper/MeshCore/releases/download/'
    if not parsed.path.startswith(prefix):
        return False
    parts = parsed.path[len(prefix):].split('/')
    return len(parts) == 2 and parts[0] in TAGS and bool(FILENAME.fullmatch(parts[1]))


def valid_digest(value):
    return isinstance(value, str) and bool(HASH.fullmatch(value))


def valid_size(value, maximum=MAX_PIECE):
    return type(value) is int and 0 < value <= maximum


def check_index(index):
    require(isinstance(index, dict) and index.get('schemaVersion') == 1, 'Invalid mirror index version')
    entries, warnings = index.get('entries'), index.get('warnings')
    require(isinstance(entries, list) and 0 < len(entries) <= MAX_ENTRIES, 'Invalid mirror entry count')
    require(isinstance(warnings, list) and len(warnings) <= MAX_ENTRIES and
            all(isinstance(value, str) and len(value) <= 4096 for value in warnings), 'Invalid mirror warnings')
    paths, urls = {}, set()
    for entry in entries:
        require(isinstance(entry, dict) and valid_url(entry.get('url')), 'Invalid historical source URL')
        require(entry['url'] not in urls, 'Duplicate historical source URL')
        urls.add(entry['url'])
        require(valid_digest(entry.get('sha256')) and valid_size(entry.get('size')), 'Invalid asset hash or size')
        parts = entry.get('parts')
        if parts is None:
            parts = [entry]
        else:
            require(isinstance(parts, list) and 2 <= len(parts) <= 128 and
                    not any(key in entry for key in ('path', 'gzipSha256', 'gzipSize')),
                    'Invalid multipart entry')
            require(sum(part.get('size', 0) for part in parts if isinstance(part, dict)) == entry['size'],
                    'Multipart asset size mismatch')
        for part in parts:
            require(isinstance(part, dict) and valid_digest(part.get('sha256')) and
                    valid_digest(part.get('gzipSha256')) and valid_size(part.get('size')) and
                    valid_size(part.get('gzipSize')) and part['gzipSize'] >= 20 and
                    part.get('path') == part['sha256'] + '.gz', 'Invalid content-addressed part')
            previous = paths.setdefault(part['path'], (part['sha256'], part['size'],
                                                       part['gzipSha256'], part['gzipSize']))
            require(previous == (part['sha256'], part['size'], part['gzipSha256'], part['gzipSize']),
                    'Conflicting content-addressed part')
    return paths


def read_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def uncompress_into(path, part, whole=None):
    require(path.stat().st_size == part['gzipSize'] and read_hash(path) == part['gzipSha256'],
            'Compressed part differs from index')
    total, digest = 0, hashlib.sha256()
    with gzip.open(path, 'rb') as stream:
        while True:
            data = stream.read(min(65536, part['size'] + 1 - total))
            if not data:
                break
            total += len(data)
            require(total <= part['size'], 'Compressed part exceeds its declared size')
            digest.update(data)
            if whole is not None:
                whole.update(data)
    require(total == part['size'] and digest.hexdigest() == part['sha256'],
            'Uncompressed part differs from index')


def install_archive(archive, destination, expected_sha256, expected_size):
    require(valid_digest(expected_sha256) and valid_size(expected_size, MAX_ARCHIVE), 'Invalid archive pin')
    archive, destination = Path(archive), Path(destination)
    require(not archive.is_symlink() and archive.is_file() and archive.stat().st_size == expected_size and
            read_hash(archive) == expected_sha256, 'Archive does not match its pinned size and SHA-256')
    parent = destination.parent
    require(not any(path.is_symlink() for path in (parent, *parent.parents)),
            'Mirror destination has a symlink ancestor')
    parent.mkdir(parents=True, exist_ok=True)
    require(not parent.is_symlink() and not destination.exists() and not destination.is_symlink(),
            'Mirror destination already exists or is a symlink')
    with tempfile.TemporaryDirectory(prefix='.historical-mirror-', dir=parent) as temporary:
        temporary = Path(temporary)
        with tarfile.open(archive, 'r:') as tar:
            members = tar.getmembers()
            require(1 < len(members) <= MAX_ENTRIES + 1, 'Invalid archive member count')
            require(sum(member.size for member in members) <= MAX_ARCHIVE, 'Archive contents exceed bound')
            names = set()
            for member in members:
                require(member.isfile() and (member.name == 'index.json' or PIECE.fullmatch(member.name)),
                        'Archive contains an unsafe name or non-file entry')
                require(member.name not in names, 'Archive contains a duplicate member')
                require(valid_size(member.size, MAX_INDEX if member.name == 'index.json' else MAX_PIECE),
                        'Archive member exceeds its size bound')
                names.add(member.name)
            require('index.json' in names, 'Archive is missing its index')
            for member in members:
                stream = tar.extractfile(member)
                require(stream is not None, 'Archive member is unreadable')
                with stream, open(temporary / member.name, 'xb') as out:
                    shutil.copyfileobj(stream, out, length=1024 * 1024)
        with open(temporary / 'index.json', encoding='utf-8') as stream:
            index = json.load(stream)
        paths = check_index(index)
        require(names == set(paths) | {'index.json'}, 'Archive members do not match its index')
        for entry in index['entries']:
            parts = entry.get('parts', [entry])
            whole = hashlib.sha256()
            for part in parts:
                uncompress_into(temporary / part['path'], part, whole)
            require(whole.hexdigest() == entry['sha256'], 'Reconstructed asset differs from index')
        os.rename(temporary, destination)
    return len(index['entries']), len(paths)


def download(url, expected_size, expected_sha256, destination):
    require(isinstance(url, str) and url.startswith('https://github.com/mikecarper/MeshCore/releases/download/utility-' + FAMILY + '/'),
            'Archive URL must point to the exact public 1.18.0.1 utility release')
    require(valid_digest(expected_sha256) and valid_size(expected_size, MAX_ARCHIVE), 'Invalid archive pin')
    digest, total = hashlib.sha256(), 0
    with urlopen(Request(url, headers={'User-Agent': 'MeshCore-historical-mirror'}), timeout=180) as response, open(destination, 'xb') as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            require(total <= expected_size, 'Downloaded archive exceeds its pinned size')
            digest.update(chunk)
            out.write(chunk)
    require(total == expected_size and digest.hexdigest() == expected_sha256,
            'Downloaded archive does not match its pinned size and SHA-256')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--destination', default='site/firmware-1.18.0.1/firmware')
    parser.add_argument('--cache-dir', help='Optional trusted-runner cache; archive pin is always checked')
    args = parser.parse_args()
    if args.cache_dir:
        require(valid_digest(args.sha256) and valid_size(args.size, MAX_ARCHIVE), 'Invalid archive pin')
        cache = Path(args.cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        require(not cache.is_symlink(), 'Archive cache directory cannot be a symlink')
        archive = cache / (args.sha256 + '.tar')
        if not archive.exists() and not archive.is_symlink():
            with tempfile.TemporaryDirectory(prefix='.download-', dir=cache) as folder:
                temporary = Path(folder) / 'mirror.tar'
                download(args.url, args.size, args.sha256, temporary)
                try:
                    os.link(temporary, archive)
                except FileExistsError:
                    pass  # Another writer completed; installation verifies its bytes.
        entries, parts = install_archive(archive, args.destination, args.sha256, args.size)
        print(f'Installed verified historical mirror: {entries} assets, {parts} content-addressed parts.')
        return
    with tempfile.TemporaryDirectory(prefix='meshcore-historical-archive-') as folder:
        archive = Path(folder) / 'mirror.tar'
        download(args.url, args.size, args.sha256, archive)
        entries, parts = install_archive(archive, args.destination, args.sha256, args.size)
    print(f'Installed verified historical mirror: {entries} assets, {parts} content-addressed parts.')


if __name__ == '__main__':
    main()
