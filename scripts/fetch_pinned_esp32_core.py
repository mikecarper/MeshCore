#!/usr/bin/env python3
"""Download and verify the pinned Arduino-ESP32 3.3.11 libraries asset."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.request import Request, urlopen


# The immutable GitHub release-asset ID and digest are specific to the
# 3.3.11 libraries archive, not the similarly named framework core archive.
ASSET_URL = 'https://api.github.com/repos/espressif/arduino-esp32/releases/assets/486039145'
ASSET_SIZE = 298739508
ASSET_SHA256 = '3cd58bcd67cb2105c058aef8c3fbdd233e2eae07bd41b5ee80c7652a5950e512'
CANONICAL_URL = ('https://github.com/espressif/arduino-esp32/releases/download/'
                 '3.3.11/esp32-core-3.3.11-libs.tar.xz')
PACKAGE_NAME = 'framework-arduinoespressif32-libs'
PACKAGE_VERSION = '5.5.5+sha.b774170ff46'


def download_verified(destination, *, opener=urlopen, expected_size=ASSET_SIZE,
                      expected_sha256=ASSET_SHA256):
    destination = Path(destination)
    request = Request(ASSET_URL, headers={'Accept': 'application/octet-stream',
                                          'X-GitHub-Api-Version': '2022-11-28',
                                          'User-Agent': 'MeshCore-build'})
    digest = hashlib.sha256()
    size = 0
    created = False
    try:
        with opener(request, timeout=60) as response, destination.open('xb') as output:
            created = True
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > expected_size:
                    raise ValueError('asset exceeds the pinned size')
                digest.update(chunk)
                output.write(chunk)
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise ValueError('asset does not match the pinned size and SHA-256')
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def bind_installed_package(archive, *, packages_dir=None, expected_size=ASSET_SIZE,
                           expected_sha256=ASSET_SHA256):
    """Make PlatformIO resolve the verified file install as its original URL.

    The caller must first verify and install the archive. PIO keys external
    packages by their exact URI; the GitHub API and canonical release URL
    deliver the same hash-pinned asset, but a file URI otherwise won't match.
    """
    archive = Path(archive)
    if archive.stat().st_size != expected_size:
        raise ValueError('asset does not match the pinned size')
    digest = hashlib.sha256()
    with archive.open('rb') as verified_file:
        for chunk in iter(lambda: verified_file.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError('asset does not match the pinned SHA-256')
    if packages_dir is None:
        from platformio.project.config import ProjectConfig
        packages_dir = ProjectConfig.get_instance().get('platformio', 'packages_dir')
    packages_dir = Path(packages_dir)
    # PlatformIO treats file:// as a raw filesystem path (without URL
    # percent-decoding), matching the URI passed by the shell installer.
    local_uri = 'file://' + str(archive.resolve())
    candidates = []
    for meta_path in packages_dir.glob('*/.piopm'):
        if meta_path.is_symlink() or meta_path.parent.is_symlink():
            continue
        try:
            metadata = json.loads(meta_path.read_text(encoding='utf-8'))
            manifest = json.loads((meta_path.parent / 'package.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict) or not isinstance(manifest, dict):
            continue
        spec = metadata.get('spec')
        if (metadata.get('type') == 'tool' and metadata.get('name') == PACKAGE_NAME
                and metadata.get('version') == PACKAGE_VERSION
                and isinstance(spec, dict) and spec.get('uri') == local_uri
                and manifest.get('name') == PACKAGE_NAME
                and manifest.get('version') == PACKAGE_VERSION):
            candidates.append((meta_path, metadata))
    if len(candidates) != 1:
        raise ValueError('verified framework package was not uniquely installed')
    meta_path, metadata = candidates[0]
    metadata['spec']['name'] = 'arduino-esp32'
    metadata['spec']['uri'] = CANONICAL_URL
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=meta_path.parent,
                                         prefix='.piopm-', delete=False) as output:
            temp_name = output.name
            json.dump(metadata, output)
        os.chmod(temp_name, meta_path.stat().st_mode & 0o777)
        os.replace(temp_name, meta_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('fetch', 'bind'):
        print('usage: fetch_pinned_esp32_core.py fetch|bind <archive-file>', file=sys.stderr)
        return 2
    try:
        if sys.argv[1] == 'fetch':
            download_verified(sys.argv[2])
        else:
            bind_installed_package(sys.argv[2])
    except Exception as error:
        # HTTP redirects can contain signed query strings. Never log URLs or
        # exception messages from the download stack.
        print(f'Pinned ESP32 library download/verification failed ({type(error).__name__}).',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
