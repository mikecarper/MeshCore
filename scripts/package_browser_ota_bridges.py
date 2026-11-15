#!/usr/bin/env python3
"""Publish exact, compiled nRF52 application candidates for browser path search.

This packages existing images. It never edits code, metadata, or version bytes.
The browser verifies each image and prepares every fitting patch at runtime.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/mota'))
import motalib


def image(path):
    raw = Path(path).read_bytes()
    if not 80 <= len(raw) <= 1024 * 1024 or not motalib.has_endf(raw):
        raise ValueError('Candidate lacks a valid EndF: ' + str(path))
    ident = motalib.parse_endf_ident(raw)
    layout = motalib.parse_nrf52_layout(raw)
    if ident is None or layout is None:
        raise ValueError('Candidate lacks a verified nRF52 identity and layout')
    if b'OTA: status' not in raw or b'get <id> flash | install' not in raw:
        raise ValueError('Candidate lacks LoRa OTA receiver support')
    return raw, ident, layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--candidate', action='append', required=True,
                        help='SOURCE_COMMIT=BIN_PATH, in forward build order; up to six')
    parser.add_argument('--destination', type=Path,
                        default=ROOT / 'docs/_data/lora_ota_bridges')
    args = parser.parse_args()
    if not 1 <= len(args.candidate) <= 6:
        parser.error('Choose one to six compiled candidates')
    base, start, layout = image(args.base)
    target, goal, final_layout = image(args.target)
    if (start.target_id, start.hw_id) != (goal.target_id, goal.hw_id) or layout != final_layout or goal.fw_version <= start.fw_version:
        raise ValueError('The starting and final images have incompatible identities, layouts, or versions')
    entries, payloads, version = [], {}, start.fw_version
    for value in args.candidate:
        commit, path = value.split('=', 1)
        commit = subprocess.check_output(['git', 'rev-parse', '--verify', commit + '^{commit}'],
                                         cwd=ROOT, text=True).strip()
        raw, ident, current_layout = image(Path(path))
        if ((ident.target_id, ident.hw_id) != (start.target_id, start.hw_id) or
                current_layout != layout or not version <= ident.fw_version <= goal.fw_version):
            raise ValueError('Intermediate identity, layout, or forward version order differs')
        version = ident.fw_version
        digest = hashlib.sha256(raw).hexdigest()
        entries.append({'sha256': digest, 'size': len(raw), 'name': Path(path).name,
                        'sourceCommit': commit})
        payloads[digest] = json.dumps({'encoding': 'gzip-base64', 'data': base64.b64encode(
            gzip.compress(raw, compresslevel=9, mtime=0)).decode('ascii')},
            separators=(',', ':'), ensure_ascii=True) + '\n'
    route = {'from': hashlib.sha256(base).hexdigest(), 'to': hashlib.sha256(target).hexdigest(),
             'targetId': start.target_id, 'hwId': start.hw_id, 'images': entries}
    if len({route['from'], route['to'], *payloads}) != len(entries) + 2:
        raise ValueError('Duplicate images in bridge candidates')
    destination = args.destination
    index_file = destination / 'index.json'
    index = json.loads(index_file.read_text('ascii')) if index_file.exists() else {'schemaVersion': 1, 'routes': []}
    if index.get('schemaVersion') != 1 or not isinstance(index.get('routes'), list):
        raise ValueError('Invalid existing bridge index')
    index['routes'] = [r for r in index['routes'] if (r['from'], r['to']) != (route['from'], route['to'])] + [route]
    destination.mkdir(parents=True, exist_ok=True)
    for digest, text in payloads.items():
        (destination / (digest + '.json')).write_text(text, encoding='ascii')
    index_file.write_text(json.dumps(index, indent=2, ensure_ascii=True) + '\n', encoding='ascii')
    print('Packaged', len(entries), 'unchanged compiled application candidates for', start.hw_id)


if __name__ == '__main__':
    main()
