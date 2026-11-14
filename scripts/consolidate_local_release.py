#!/usr/bin/env python3
"""Consolidate already-qualified preview builds into a preserved local release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from build_local_release import validate_embedded_bootloader_records
from nrf52_bootloader_exceptions import render_bootloader_exceptions
from nrf52_embedded_bootloader import PREVIEW_VERSION
from package_cascade_release import (category, collect_artifacts, collect_local_release,
                                     embedded_bootloader_variant, nrf52_sensor_profile,
                                     validate_supplemental_matches)


ROOT = Path(__file__).resolve().parents[1]
CONSOLIDATION_BLOCKER = ("The separate nRF52 preview is not integrated into this release matrix; "
                         "final release consolidation and current CI verification remain pending.")
REVIEW_BLOCKER = "The consolidated release and its tooling CI require final verification."


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def safe_paths(base: Path, additional: Path, output: Path) -> None:
    base, additional, output = (path.resolve() for path in (base, additional, output))
    if (base == additional or output.exists() or output == base or output == additional
            or base in output.parents or additional in output.parents
            or output in base.parents or output in additional.parents):
        raise ValueError('release inputs and new output must be separate')


def consolidate(base: Path, additional: Path, output: Path) -> None:
    safe_paths(base, additional, output)
    manifest = json.loads((base / 'manifest.json').read_text(encoding='ascii'))
    results = json.loads((additional / 'build-results.json').read_text(encoding='ascii'))
    source, version = manifest['source_commit'], manifest['firmware_version']
    extra = results['source_commit']
    if (not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}', source)
            or not isinstance(extra, str) or not re.fullmatch(r'[0-9a-f]{40}', extra)
            or source == extra or source[:8] == extra[:8]
            or results.get('bootloader_release') != PREVIEW_VERSION
            or results.get('hardware_tested') is not False
            or not isinstance(results.get('targets'), dict) or not results['targets']
            or any(not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_.+-]+', target)
                   for target in results['targets'])
            or any(not isinstance(item, dict) or type(item.get('exit_code')) is not int
                   or item['exit_code'] != 0 or not isinstance(item.get('seconds'), (int, float))
                   for item in results['targets'].values())):
        raise ValueError('supplemental build results are incomplete')
    if (git('status', '--porcelain') or git('merge-base', '--is-ancestor', source, extra)):
        raise ValueError('consolidation requires a clean checkout and descendant source')
    tooling = git('rev-parse', 'HEAD')
    blockers = manifest.get('publication_blockers', [])
    if not isinstance(blockers, list) or any(item != CONSOLIDATION_BLOCKER for item in blockers):
        raise ValueError('base release has an unrelated publication blocker')

    existing, _, _ = collect_local_release(base, version, source, historical_bootloader_inventory=True)
    preview = collect_artifacts(additional, f'{version}-{extra[:8]}')
    if (set(results['targets']) != {item['manifest']['target'] for item in preview}
            or any(embedded_bootloader_variant(item['manifest']) != PREVIEW_VERSION
                   or item['manifest']['embedded_bootloader'].get('hardware_tested') is not False
                   for item in preview)):
        raise ValueError('supplemental firmware differs from its completed build results')
    for item in preview:
        item['manifest']['source_commit'] = extra
    validate_supplemental_matches(existing + preview, source)
    ordinary = [item for item in existing if not embedded_bootloader_variant(item['manifest'])]
    embedded = [item for item in existing if embedded_bootloader_variant(item['manifest'])] + preview
    variants = validate_embedded_bootloader_records(ordinary, embedded)
    targets = sorted(item['manifest']['artifact_target'] for item in preview)
    receipt = {'source_commit': extra, 'directory': 'source-' + extra[:8],
               'kind': 'nrf52_bootloader_preview', 'bootloader_release': PREVIEW_VERSION,
               'hardware_tested': False, 'artifact_targets': targets}

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.consolidating-', dir=output.parent) as temp:
        stage = Path(temp) / 'release'
        shutil.copytree(base, stage)
        entries = manifest['firmware']
        for record in preview:
            data = record['manifest']
            folder = stage / 'firmware' / category(record) / receipt['directory']
            folder.mkdir(parents=True, exist_ok=True)
            paths = []
            for original in record['files']:
                destination = folder / original.name
                if destination.exists():
                    raise ValueError('duplicate supplemental artifact')
                shutil.copy2(original, destination)
                paths.append(str(destination.relative_to(stage)))
            entries.append({'target': data['target'], 'artifact_target': data['artifact_target'],
                            'platform': data['platform'], 'profile': data['build_profile'],
                            'sensor_profile': nrf52_sensor_profile(data),
                            'embedded_bootloader': data['embedded_bootloader'],
                            'ota_update_methods': data.get('ota_update_methods', []),
                            'source_commit': extra, 'files': paths})

        evidence = stage / 'build-evidence' / 'nrf52-preview-builds'
        evidence.mkdir(parents=True)
        target_evidence = {}
        for target, result in sorted(results['targets'].items()):
            log = additional / 'build-logs' / (target + '.log')
            if not log.is_file() or log.is_symlink():
                raise ValueError(f'{target}: missing build log')
            shutil.copy2(log, evidence / log.name)
            target_evidence[target] = {'exit_code': 0, 'seconds': result['seconds'],
                                       'log': str((evidence / log.name).relative_to(stage)),
                                       'log_sha256': sha256(log)}
        (evidence / 'results.json').write_text(json.dumps(
            {'source_commit': extra, 'bootloader_release': PREVIEW_VERSION,
             'hardware_tested': False, 'targets': target_evidence}, indent=2) + '\n', encoding='ascii')

        manifest['firmware_target_count'] = len(entries)
        manifest['consolidation_tooling_commit'] = tooling
        manifest['nrf52_bootloader_variants'] = variants
        manifest['supplemental_sources'] = [receipt]
        manifest.pop('supplemental_nrf52_preview', None)
        manifest['publication_blockers'] = [REVIEW_BLOCKER]
        manifest.pop('post_build_ci_run', None)
        manifest.pop('post_build_release_tooling_commit', None)
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='ascii')
        report = render_bootloader_exceptions(variants)
        (stage / 'NRF52-BOOTLOADER-EXCEPTIONS.md').write_text(report, encoding='ascii')
        readme = (stage / 'README.md').read_text(encoding='ascii')
        readme = readme.split('\nSupplemental nRF52 preview:', 1)[0]
        if blockers and not readme.startswith('DO NOT PUBLISH: final release consolidation and current CI verification remain pending.'):
            raise ValueError('base release README does not match its consolidation blocker')
        readme = readme.replace('DO NOT PUBLISH: final release consolidation and current CI verification remain pending.',
                                'DO NOT PUBLISH: the consolidated release and its tooling CI require final verification.', 1)
        readme = readme.replace('The XIAO S3 WIO partition-expander requires A/B and power-interruption\n'
                                'hardware testing before inclusion in a published release.',
                                'The XIAO S3 WIO migration passed A/B and post-rewrite power-interruption tests.\n'
                                'Interruption during the partition-table write was not tested and may require USB recovery.')
        readme += (f'\nThe {len(preview)} nRF52 bootloader preview variants are included in this matrix.\n'
                   f'Their source commit is `{extra}`; the bootloaders were not hardware tested.\n'
                   'Muzi stock bootloaders require a separate update. See the preview instructions:\n'
                   'https://github.com/mikecarper/MeshCore/blob/keymindCascade/docs/nrf52_bootloader_previews.md\n')
        (stage / 'README.md').write_text(readme, encoding='ascii')
        paths = sorted(path for path in stage.rglob('*') if path.is_file() and path.name != 'SHA256SUMS.txt')
        (stage / 'SHA256SUMS.txt').write_text(''.join(
            f'{sha256(path)}  {path.relative_to(stage)}\n' for path in paths), encoding='ascii')
        qualified, _, _ = collect_local_release(stage, version, source)
        if len(qualified) != len(existing) + len(preview):
            raise ValueError('consolidated release count differs from verified inputs')
        stage.rename(output)
    print(f'Consolidated {len(existing)} existing and {len(preview)} supplemental variants: {output}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-release', required=True, type=Path)
    parser.add_argument('--supplemental-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    consolidate(args.base_release, args.supplemental_dir, args.output)


if __name__ == '__main__':
    main()
