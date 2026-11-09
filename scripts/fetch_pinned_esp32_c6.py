#!/usr/bin/env python3
"""Recover two pinned ESP32-C6 build assets from their official release API.

Call only after the matching canonical release URL fails transiently. A local
archive must be verified before installation and again before binding the PIO
package record to that canonical URL. No package version or source is changed.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.request import Request, urlopen


ASSETS = {
    'platform': {
        'api': 'https://api.github.com/repos/pioarduino/platform-espressif32/releases/assets/259436108',
        'size': 1472729,
        'sha256': 'e42240daaba9d4dfb9cec7ec702b6c84a0605d34c7f03e1ad342bbee74fc1db8',
        'url': 'https://github.com/pioarduino/platform-espressif32/releases/download/53.03.13-1/platform-espressif32.zip',
        'type': 'platform', 'name': 'espressif32', 'version': '53.3.13',
        'manifest': 'platform.json', 'manifest_version': '53.03.13',
        'spec_name': 'platform-espressif32', 'owner': None, 'storage': 'platforms_dir',
    },
    'libs': {
        'api': 'https://api.github.com/repos/espressif/esp32-arduino-lib-builder/releases/assets/228479215',
        'size': 341414090,
        'sha256': '489012502218a7d30f6c312764bc8d10830a51e1db29558f15181c68373d0095',
        'url': 'https://github.com/espressif/esp32-arduino-lib-builder/releases/download/idf-release_v5.3/esp32-arduino-libs-idf-release_v5.3-489d7a2b-v1.zip',
        'type': 'tool', 'name': 'framework-arduinoespressif32-libs',
        'version': '5.3.0+sha.489d7a2b3a', 'manifest': 'package.json',
        'manifest_version': '5.3.0+sha.489d7a2b3a',
        'spec_name': 'framework-arduinoespressif32-libs', 'owner': 'espressif',
        'storage': 'packages_dir',
    },
}


def verify(archive, asset):
    archive = Path(archive)
    digest = hashlib.sha256()
    size = 0
    with archive.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            size += len(chunk)
            if size > asset['size']:
                raise ValueError('asset exceeds pinned size')
            digest.update(chunk)
    if size != asset['size'] or digest.hexdigest() != asset['sha256']:
        raise ValueError('asset does not match pinned size and SHA-256')


def download_verified(key, destination, *, opener=urlopen):
    asset = ASSETS[key]
    destination = Path(destination)
    request = Request(asset['api'], headers={'Accept': 'application/octet-stream',
                                            'X-GitHub-Api-Version': '2022-11-28',
                                            'User-Agent': 'MeshCore-build'})
    created = False
    try:
        digest = hashlib.sha256()
        size = 0
        with opener(request, timeout=60) as response, destination.open('xb') as output:
            created = True
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > asset['size']:
                    raise ValueError('asset exceeds pinned size')
                digest.update(chunk)
                output.write(chunk)
        if size != asset['size'] or digest.hexdigest() != asset['sha256']:
            raise ValueError('asset does not match pinned size and SHA-256')
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def bind_installed_package(key, archive, *, storage_dir=None):
    """Bind the unique verified file install to the identical official URL."""
    asset = ASSETS[key]
    archive = Path(archive)
    verify(archive, asset)
    if storage_dir is None:
        from platformio.project.config import ProjectConfig
        storage_dir = ProjectConfig.get_instance().get('platformio', asset['storage'])
    storage_dir = Path(storage_dir)
    # PIO records the exact CLI URI; it does not percent-encode spaces in a
    # file:// argument. The fallback passes an absolute path verbatim.
    local_uri = 'file://' + archive.resolve().as_posix()
    candidates = []
    for meta_path in storage_dir.glob('*/.piopm'):
        manifest_path = meta_path.parent / asset['manifest']
        if meta_path.is_symlink() or meta_path.parent.is_symlink() or manifest_path.is_symlink():
            continue
        try:
            metadata = json.loads(meta_path.read_text(encoding='utf-8'))
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict) or not isinstance(manifest, dict):
            continue
        spec = metadata.get('spec')
        if (metadata.get('type') == asset['type'] and metadata.get('name') == asset['name']
                and metadata.get('version') == asset['version'] and isinstance(spec, dict)
                and spec.get('uri') == local_uri and manifest.get('name') == asset['name']
                and manifest.get('version') == asset['manifest_version']):
            candidates.append((meta_path, metadata))
    if len(candidates) != 1:
        raise ValueError('verified package was not uniquely installed')
    meta_path, metadata = candidates[0]
    metadata['spec']['name'] = asset['spec_name']
    metadata['spec']['owner'] = asset['owner']
    metadata['spec']['uri'] = asset['url']
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
    if len(sys.argv) != 4 or sys.argv[1] not in ('fetch', 'bind') or sys.argv[2] not in ASSETS:
        print('usage: fetch_pinned_esp32_c6.py fetch|bind platform|libs <archive>', file=sys.stderr)
        return 2
    try:
        if sys.argv[1] == 'fetch':
            download_verified(sys.argv[2], sys.argv[3])
        else:
            bind_installed_package(sys.argv[2], sys.argv[3])
    except Exception as error:
        # GitHub redirect URLs may contain temporary signatures.
        print(f'Pinned ESP32-C6 asset verification failed ({type(error).__name__}).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
