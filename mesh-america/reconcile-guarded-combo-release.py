#!/usr/bin/env python3
"""Reconcile the rebuilt 1.18.0.1 embedded-bootloader assets with GitHub.

The applications were rebuilt after publication; the release family tag did
not change. This deliberately recognizes only that reviewed replacement.
"""
import argparse
import copy
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

FAMILY = 'v1.18.0.1-halo-keymind-cascade-dev-03214a16'
OLD_SOURCE = '03214a16df2bd09d3272dfb001d2d322c6740f60'
NEW_SOURCE = '40dd42de972762d38083d1faf68f1ba3bc104d61'
REPOSITORY = 'https://github.com/mikecarper/MeshCore'
TAGS = {'lora-ota-' + FAMILY, 'lora-ota-2-' + FAMILY}
OLD_SUFFIX = '-' + FAMILY
NEW_SUFFIX = OLD_SUFFIX[:-8] + NEW_SOURCE[:8]


def entries(provider):
    for device in provider['device']:
        for firmware in device.get('firmware', []):
            for version, record in firmware.get('version', {}).items():
                if version == FAMILY and any('-with-bootloader-2.4.12-' in item.get('name', '')
                                             for item in record.get('files', [])):
                    yield device, record


def reconcile(provider, releases):
    updated = copy.deepcopy(provider)
    count = 0
    for device, record in entries(updated):
        if device.get('type') != 'nrf52':
            raise ValueError('embedded bootloader target is not nRF52')
        files = record.get('files', [])
        names = set()
        for item in files:
            old_name = item.get('name', '')
            match = re.fullmatch(r'(.+?-with-bootloader-2\.4\.12-.+)' + re.escape(OLD_SUFFIX) + r'(\.(?:uf2|zip))', old_name)
            if not match:
                raise ValueError('unexpected asset name: ' + old_name)
            old_url = urlsplit(item.get('url', ''))
            parts = old_url.path.split('/')
            if (old_url.scheme != 'https' or old_url.netloc != 'github.com' or old_url.query or old_url.fragment
                    or len(parts) != 7 or parts[1:5] != ['mikecarper', 'MeshCore', 'releases', 'download']
                    or unquote(parts[5]) not in TAGS or unquote(parts[6]) != old_name):
                raise ValueError('unexpected release asset URL: ' + item.get('url', ''))
            tag = unquote(parts[5]); release = releases.get(tag)
            if not isinstance(release, dict) or release.get('tag_name') != tag or release.get('draft'):
                raise ValueError('missing exact public release: ' + tag)
            new_name = match.group(1) + NEW_SUFFIX + match.group(2)
            assets = [asset for asset in release.get('assets', []) if asset.get('name') == new_name]
            if len(assets) != 1:
                raise ValueError('missing or ambiguous replacement: ' + new_name)
            asset = assets[0]
            expected_url = f'{REPOSITORY}/releases/download/{tag}/{new_name}'
            digest = asset.get('digest', '')
            if (asset.get('browser_download_url') != expected_url or asset.get('state') != 'uploaded'
                    or not isinstance(asset.get('size'), int) or asset['size'] <= 0
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest)):
                raise ValueError('unverified replacement metadata: ' + new_name)
            stem = match.group(1)
            for ext in ('.capabilities.json', '.memory.json'):
                meta = stem + NEW_SUFFIX + ext
                matches = [a for a in release['assets'] if a.get('name') == meta]
                if len(matches) != 1 or matches[0].get('browser_download_url') != f'{REPOSITORY}/releases/download/{tag}/{meta}':
                    raise ValueError('missing exact replacement report: ' + meta)
            item.update(name=new_name, url=expected_url, sha256=digest[7:])
            names.add(stem); count += 1
        if len(names) != 1:
            raise ValueError('record combines different firmware profiles')
        old_stem = next(iter(names)) + OLD_SUFFIX
        new_stem = next(iter(names)) + NEW_SUFFIX
        notes = record.get('notes', '')
        if OLD_SOURCE not in notes or old_stem + '.capabilities.json' not in notes or old_stem + '.memory.json' not in notes:
            raise ValueError('missing expected source or report references for ' + old_stem)
        record['notes'] = notes.replace(old_stem + '.capabilities.json', new_stem + '.capabilities.json').replace(
            old_stem + '.memory.json', new_stem + '.memory.json').replace(OLD_SOURCE, NEW_SOURCE)
    return updated, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path(__file__).with_name('keymind-cascade-v1.16.0-provider.json'))
    args = parser.parse_args()
    provider = json.loads(args.catalog.read_text(encoding='utf-8'))
    releases = {tag: json.loads(subprocess.run(['gh', 'api', 'repos/mikecarper/MeshCore/releases/tags/' + tag],
                                              check=True, capture_output=True, text=True).stdout) for tag in TAGS}
    updated, count = reconcile(provider, releases)
    if not count:
        raise ValueError('no old embedded-bootloader assets found; no changes written')
    args.catalog.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    print(f'Updated {count} verified embedded-bootloader file entries.')


if __name__ == '__main__':
    main()
