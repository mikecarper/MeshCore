#!/usr/bin/env python3
"""Build the compact, release-bound firmware-picker catalog for GitHub Pages.

The public GitHub releases API includes tens of megabytes of unused metadata.
Fetching every page here keeps the browser's catalog complete without making
each visitor download all historical releases and unrelated preview assets.

Maintainers: after a new version or intentional asset removal, first update
the pinned controls and picker release selection. Run with
``--propose-inventory /tmp/firmware-inventory.json`` against the published
release. Independently compare every proposed release tag and asset count to
the complete GitHub release pages; review source identity and any decreases.
Only after review, replace docs/_data/firmware_release_inventory.json and
commit it with the controls. Normal builds cannot lower the inventory and fail
before publication when the API is unavailable or the release is incomplete.
"""
import argparse
from datetime import datetime, timezone
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API = 'https://api.github.com/repos/mikecarper/MeshCore/releases'
REPO = 'https://github.com/mikecarper/MeshCore'
# Asset-heavy releases make 100-item pages tens of megabytes. Smaller pages
# reduce truncated responses while retaining the same 10,000-release bound.
PAGE_SIZE = 10
MAX_PAGES = 1000
PREFIXES = ('', 'repeater-room-', 'utility-', 'logging-', 'lora-ota-', 'full-profiles-')
CHUNK_GROUPS = ('companion', 'repeater-room', 'utility', 'logging', 'lora-ota', 'full-profiles')


def fetch_github_releases(endpoint=API, token=None, opener=urlopen, sleep=time.sleep):
    """Read all pages; a failed or malformed page is never a partial success."""
    if endpoint != API:
        raise ValueError('Unexpected release API endpoint')
    result = []
    for page in range(1, MAX_PAGES + 1):
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'MeshCore-firmware-picker'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(f'{endpoint}?per_page={PAGE_SIZE}&page={page}', headers=headers)
        for attempt in range(3):
            try:
                with opener(request, timeout=60) as response:
                    batch = json.load(response)
                break
            except HTTPError as error:
                code = error.code
                error.close()
                if code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f'GitHub releases page {page} returned HTTP {code}') from None
                reason = f'HTTP {code}'
            except (URLError, TimeoutError, socket.timeout, ConnectionError,
                    HTTPException, json.JSONDecodeError, UnicodeDecodeError):
                if attempt == 2:
                    raise RuntimeError(f'GitHub releases page {page} could not be fetched completely after 3 attempts') from None
                reason = 'incomplete or unreadable response'
            print(f'GitHub releases page {page}: retry {attempt + 2}/3 after {reason}', file=sys.stderr)
            sleep(2 ** attempt)
        if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
            raise ValueError('Invalid GitHub releases page')
        if not all(isinstance(release, dict) for release in batch):
            raise ValueError('Invalid GitHub release entry')
        result.extend(batch)
        if len(batch) < PAGE_SIZE:
            return result
    raise ValueError('GitHub release catalog exceeds pagination limit')


def compact_release(release):
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', tag):
        raise ValueError('Invalid release tag')
    if release.get('draft') is not False:
        raise ValueError(f'Release {tag} is not published')
    if release.get('html_url') != f'{REPO}/releases/tag/{tag}':
        raise ValueError(f'Unexpected release URL for {tag}')
    assets = release.get('assets')
    if not isinstance(assets, list) or not assets:
        raise ValueError(f'Release {tag} has no assets')
    compact = []
    names = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError(f'Invalid asset in {tag}')
        name, url, size = asset.get('name'), asset.get('browser_download_url'), asset.get('size')
        if not isinstance(name, str) or not name or name in names or '/' in name or '\\' in name:
            raise ValueError(f'Invalid or duplicate asset name in {tag}')
        parsed = urlsplit(url if isinstance(url, str) else '')
        if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
                or unquote(parsed.path) != f'/mikecarper/MeshCore/releases/download/{tag}/{name}'):
            raise ValueError(f'Invalid asset URL in {tag}')
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError(f'Invalid asset size in {tag}')
        names.add(name)
        compact.append({'name': name, 'browser_download_url': url, 'size': size})
    row = {key: release.get(key) for key in ('name', 'tag_name', 'html_url', 'published_at', 'created_at', 'draft', 'prerelease')}
    row['assets'] = compact
    return row


def _controls(controls):
    family, source = controls.get('familyTag'), controls.get('source')
    if not isinstance(family, str) or not re.fullmatch(r'v[0-9][A-Za-z0-9._-]*', family):
        raise ValueError('Invalid controls family tag')
    if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}', source):
        raise ValueError('Invalid controls source')
    tail = family.rsplit('-', 1)[-1]
    if not re.fullmatch(r'[0-9a-f]{8,40}', tail) or not source.startswith(tail):
        raise ValueError('Controls source does not match family tag')
    supplemental = set()
    for entry in controls.get('supplementalReleases', []):
        tag, tag_family, tag_source = entry.get('releaseTag'), entry.get('familyTag'), entry.get('source')
        if not isinstance(tag, str) or not re.fullmatch(r'heltec-t114-v\d+(?:\.\d+){2,3}\.preview[1-9]\d*', tag):
            raise ValueError('Invalid supplemental release')
        if not isinstance(tag_source, str) or not re.fullmatch(r'[0-9a-f]{40}', tag_source):
            raise ValueError('Invalid supplemental source')
        suffix = str(tag_family or '').removeprefix(tag[len('heltec-t114-'):] + '-')
        if (tag_family != tag[len('heltec-t114-'):] + '-' + suffix
                or not re.fullmatch(r'[0-9a-f]{8,40}', suffix) or not tag_source.startswith(suffix)
                or not isinstance(entry.get('profiles'), dict)):
            raise ValueError('Unqualified supplemental release')
        supplemental.add(tag)
    return family, source, supplemental


def snapshot_for_releases(releases, controls_list, now=None):
    if not isinstance(releases, list) or not isinstance(controls_list, (list, tuple)) or not controls_list:
        raise ValueError('Releases and controls are required')
    by_tag = {}
    for release in releases:
        if not isinstance(release, dict) or not isinstance(release.get('tag_name'), str):
            raise ValueError('Invalid release list')
        tag = release['tag_name']
        if tag in by_tag:
            raise ValueError('Duplicate release tag: ' + tag)
        by_tag[tag] = release
    families, selected = {}, {}
    for controls in controls_list:
        family, source, supplements = _controls(controls)
        if family in families:
            raise ValueError('Duplicate controls family: ' + family)
        groups = '|'.join(re.escape(group) for group in CHUNK_GROUPS)
        chunk = re.compile(rf'^({groups})-([2-9]|[1-9][0-9]+)-{re.escape(family)}$')
        exact = {prefix + family for prefix in PREFIXES}
        tags = {tag for tag in by_tag if tag in exact or chunk.fullmatch(tag)} | supplements
        if family not in tags or family not in by_tag:
            raise ValueError('Missing primary release: ' + family)
        for tag in tags:
            if tag not in by_tag:
                raise ValueError('Missing supplemental release: ' + tag)
            selected[tag] = compact_release(by_tag[tag])
        for group in CHUNK_GROUPS:
            nums = sorted(int(match.group(2)) for tag in tags
                          if (match := chunk.fullmatch(tag)) and match.group(1) == group)
            if nums and (nums != list(range(2, nums[-1] + 1))
                         or (family if group == 'companion' else group + '-' + family) not in tags):
                raise ValueError('Missing release chunk in ' + group + ': ' + family)
        ordered = sorted(tags)
        families[family] = {'source': source, 'releaseTags': ordered,
                            'assetCounts': {tag: len(selected[tag]['assets']) for tag in ordered}}
    timestamp = now if now is not None else datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    return {'schemaVersion': 1, 'generatedAt': timestamp, 'families': families,
            'releases': [selected[tag] for tag in sorted(selected)]}


def validate_snapshot_baseline(snapshot, baseline):
    """Fail deployment if a previously verified release page or assets vanish.

    GitHub release pages can be temporarily incomplete while uploads happen.
    The small checked-in inventory tracks the last reviewed minimum counts;
    additional pages and assets are accepted. Deliberate removals need an
    explicit inventory update in the same review as the controls change.
    """
    expected = baseline.get('families') if isinstance(baseline, dict) else None
    if (not isinstance(baseline, dict) or baseline.get('schemaVersion') != 1 or not isinstance(expected, dict)
            or not expected or set(expected) != set(snapshot.get('families', {}))):
        raise ValueError('Release inventory families do not match controls')
    for family, record in expected.items():
        actual = snapshot['families'][family]
        tags = record.get('releaseTags') if isinstance(record, dict) else None
        counts = record.get('assetCounts') if isinstance(record, dict) else None
        if (not isinstance(record, dict) or record.get('source') != actual['source'] or not isinstance(tags, list)
                or not all(isinstance(tag, str) for tag in tags) or len(tags) != len(set(tags)) or not isinstance(counts, dict)
                or set(tags) != set(counts) or family not in tags):
            raise ValueError('Invalid release inventory for ' + family)
        for tag in tags:
            count = counts[tag]
            if (not isinstance(count, int) or isinstance(count, bool) or count <= 0
                    or tag not in actual['assetCounts'] or actual['assetCounts'][tag] < count):
                raise ValueError('Incomplete release assets for ' + tag)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/_data/firmware_releases.json')
    parser.add_argument('--propose-inventory', type=Path, help='Write a separate inventory proposal for manual review; do not publish a snapshot')
    args = parser.parse_args()
    controls = [json.loads((ROOT / 'docs/_data' / name).read_text(encoding='utf-8'))
                for name in ('firmware_controls.json', 'firmware_controls_1.18.0.1.json')]
    releases = fetch_github_releases(token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'))
    snapshot = snapshot_for_releases(releases, controls)
    inventory_path = ROOT / 'docs/_data/firmware_release_inventory.json'
    if args.propose_inventory:
        if args.propose_inventory.resolve() in (inventory_path.resolve(), args.output.resolve()):
            raise ValueError('Inventory proposals must be written to a separate review file')
        args.propose_inventory.parent.mkdir(parents=True, exist_ok=True)
        args.propose_inventory.write_text(json.dumps({'schemaVersion': 1, 'families': snapshot['families']}, indent=2) + '\n', encoding='utf-8')
        print(f'Inventory proposal written for review: {args.propose_inventory}')
        return
    baseline = json.loads(inventory_path.read_text(encoding='utf-8'))
    validate_snapshot_baseline(snapshot, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + '.tmp')
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    tmp.replace(args.output)
    print(f'Firmware snapshot: {len(snapshot["releases"])} release pages, '
          f'{sum(len(release["assets"]) for release in snapshot["releases"])} assets; {args.output.stat().st_size} bytes')


if __name__ == '__main__':
    main()
