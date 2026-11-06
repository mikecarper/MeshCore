"""Create and revalidate phone-friendly standalone migration release assets.

Intermediate archives remain useful for existing host flashers. They are not
needed by a person installing a migration bridge from a phone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import zipfile

from package_esp32_partition_migration import (
    LEGACY_SLOT_BYTES, bridge_with_successor_endf, check_esp32_stage,
    pack_version, parse_container, parse_endf_ident, role_for_target, verify,
)
from motalib import CODEC_FULL, hw_id_bytes


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def standalone_assets(archive_path: Path, board: str, target: str,
                      version: str, source: str, full_image: Path | None) -> dict[str, bytes]:
    """Return exact standalone files; unqualified selections produce none.

    The caller must first run verify_archive. Recheck the relevant bytes here:
    a previously built ZIP's self-reported metadata alone is not sufficient to
    bind a bridge or its successor to the exact published Full application.
    """
    if (not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', board)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]*', version)
            or not re.fullmatch(r'[0-9a-f]{8}', source)):
        raise ValueError('unsafe migration asset identity')
    if full_image is None:
        return {}
    full_path = Path(full_image)
    if full_path.suffix != '.bin' or full_path.name.endswith('-merged.bin'):
        raise ValueError('migration successor must be a published app-only image')
    full = full_path.read_bytes()
    ident = parse_endf_ident(full)
    if ident is None:
        raise ValueError('migration successor has no firmware identity')
    if ident.fw_version != pack_version(version.split('-', 1)[0]):
        raise ValueError('migration successor version differs from release')
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip():
            raise ValueError('damaged migration archive')
        manifest = json.loads(archive.read('manifest.json'))
        if (manifest.get('board') != board or manifest.get('target') != target
                or manifest.get('role') != role_for_target(target)
                or manifest.get('firmware_version') != version
                or manifest.get('source_commit') != source
                or manifest.get('target_id') != f'0x{ident.target_id:08x}'
                or manifest.get('hardware_id') != ident.hw_id
                or manifest.get('legacy_slot_bytes') != LEGACY_SLOT_BYTES):
            raise ValueError('migration archive identity differs from published Full')
        recorded = manifest.get('files', {})
        def read_checked(name: str) -> bytes:
            data = archive.read(name)
            entry = recorded.get(name, {})
            if entry.get('bytes') != len(data) or entry.get('sha256') != _digest(data):
                raise ValueError(f'invalid migration archive file: {name}')
            return data
        if read_checked('full-application.bin') != full:
            raise ValueError('migration archive Full differs from published application')

        names = set(archive.namelist())
        evaluation = manifest.get('expander_evaluation')
        # Older archives were built without the compiled target marker and
        # independently measured legacy-slot proof. Keep them available to
        # existing host flashers, but do not promote them to standalone assets.
        if evaluation is None:
            return {}
        if not isinstance(evaluation, dict):
            raise ValueError('invalid expander evaluation')
        present = {'partition-expander.bin', 'partition-expander.mota'} & names
        if present and len(present) != 2:
            raise ValueError('incomplete combined expander transports')
        if evaluation is not None and evaluation.get('decision') == 'combined' and len(present) != 2:
            raise ValueError('qualified combined expander is missing')
        if {'partition-expander.bin', 'partition-expander.mota'} <= names:
            if evaluation.get('decision') != 'combined':
                raise ValueError('expander decision disagrees with combined images')
            if not manifest.get('partition_expander'):
                raise ValueError('expander has no verified board environment')
            wifi = read_checked('partition-expander.bin')
            lora = read_checked('partition-expander.mota')
            from esp32_migration_expanders import evaluate_candidate
            if evaluation != evaluate_candidate(board, {'target': target}, wifi, version):
                raise ValueError('invalid combined expander evidence')
            mode = 'combined'
            labels = ('expander.bin', 'expander.mota')
        elif evaluation is not None and evaluation.get('decision') == 'oversize':
            from esp32_migration_expanders import evaluate_candidate
            candidate = read_checked('expander-candidate.bin')
            if evaluation != evaluate_candidate(board, {'target': target}, candidate, version):
                raise ValueError('invalid oversized expander evidence')
            if evaluation.get('decision') != 'oversize':
                raise ValueError('expander fallback is not justified by size')
            wifi = read_checked('wifi-bridge.bin')
            lora = read_checked('lora-bridge.mota')
            mode = 'separate'
            labels = ('wifi-bridge.bin', 'lora-bridge.mota')
        else:
            raise ValueError('unrecognized or incomplete expander evaluation')

        if len(wifi) > LEGACY_SLOT_BYTES:
            raise ValueError('Wi-Fi migration image exceeds legacy slot')
        parsed = parse_container(lora)
        if verify(parsed):
            raise ValueError('invalid LoRa migration container')
        if not parsed.manifest.is_full or parsed.manifest.codec_id != CODEC_FULL:
            raise ValueError('LoRa migration must be a full image')
        if (parsed.manifest.target_id != ident.target_id
                or parsed.manifest.fw_version != ident.fw_version
                or parsed.manifest.hw_id != hw_id_bytes(ident.hw_id)):
            raise ValueError('LoRa migration target differs from published Full')
        payload_ident = parse_endf_ident(parsed.payload)
        if (payload_ident is None or payload_ident.target_id != ident.target_id
                or payload_ident.fw_version != ident.fw_version
                or payload_ident.hw_id != ident.hw_id):
            raise ValueError('LoRa migration payload identity differs from published Full')
        check_esp32_stage(lora, parsed.payload, LEGACY_SLOT_BYTES)
        if mode == 'combined' and parsed.payload != bridge_with_successor_endf(wifi, ident):
            raise ValueError('Wi-Fi and LoRa expander payloads differ')

    prefix = f'{board}-{version}-{source}'
    wifi_name, lora_name = (f'{prefix}-{label}' for label in labels)
    result = {wifi_name: wifi, lora_name: lora}
    data = {
        'format': 'meshcore-esp32-partition-migration-standalone-v1',
        'board': board, 'target': target, 'role': manifest['role'],
        'target_id': manifest['target_id'], 'hardware_id': ident.hw_id,
        'firmware_version': version, 'source_commit': source, 'mode': mode,
        'flash_bytes': manifest['flash_bytes'], 'legacy_slot_bytes': LEGACY_SLOT_BYTES,
        'expanded_slot_bytes': manifest['expanded_slot_bytes'],
        'lora_bridge_mode': ('self-hosted-either-slot-verified-configuration'
                             if mode == 'combined' else manifest.get('lora_bridge_mode')),
        'atomic_power_loss_recovery': False,
        'full_application': {'filename': full_path.name, 'bytes': len(full), 'sha256': _digest(full)},
        'files': {name: {'bytes': len(value), 'sha256': _digest(value)}
                  for name, value in result.items()},
    }
    result[f'{prefix}-migration.json'] = (json.dumps(data, indent=2) + '\n').encode()
    return result
