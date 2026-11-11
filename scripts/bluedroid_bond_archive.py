"""Fail-closed checks for matched Bluedroid replacement archives.

This module deliberately knows nothing about downloading or building an archive.
Only the checked-in manifest can authorize a particular stock/replacement pair.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile


class ArchiveError(ValueError):
    pass


def stage_archive(manifest_path, mcu, stock, package, replacement, ar, environ=None):
    """Rebuild a private copy of the SDK archive from authenticated objects."""
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    entry = manifest.get('mcus', {}).get(mcu)
    if not entry or _hash(stock) != entry.get('stock_sha256'):
        raise ArchiveError('unsupported stock Bluedroid archive')
    if _hash(package) != entry.get('package_sha256'):
        raise ArchiveError('Bluedroid replacement package hash mismatch')
    target = Path(replacement)
    if target.exists() and _hash(target) == entry.get('patched_sha256'):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    objects = entry.get('objects', {})
    if set(objects) != {'btc_dm.c.obj', 'bta_dm_act.c.obj', 'btm_sec.c.obj'}:
        raise ArchiveError('unexpected replacement object list')
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='stage-') as temp:
        root = Path(temp)
        with ZipFile(package) as z:
            if len(z.namelist()) != len(objects) or set(z.namelist()) != set(objects):
                raise ArchiveError('unexpected replacement package members')
            for name, expected in objects.items():
                data = z.read(name)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ArchiveError('replacement object hash mismatch: ' + name)
                (root/name).write_bytes(data)
        candidate = root/'libbt.a'
        shutil.copyfile(stock, candidate)
        subprocess.run([ar, 'rD', str(candidate), *[str(root/n) for n in objects]],
                       check=True, env=environ, capture_output=True)
        verify_archives(manifest_path, mcu, stock, candidate)
        os.replace(candidate, target)


def _hash(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as exc:
        raise ArchiveError(f"cannot read {path}: {exc}") from exc
    return h.hexdigest()


def verify_archives(manifest_path, mcu, stock, replacement):
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArchiveError(f"cannot read archive manifest: {exc}") from exc
    if manifest.get("version") != 1 or not isinstance(manifest.get("mcus"), dict):
        raise ArchiveError("unsupported archive manifest format")
    entry = manifest["mcus"].get(mcu)
    if not isinstance(entry, dict):
        raise ArchiveError(f"no qualified Bluedroid archive for MCU {mcu!r}")
    for name, path in (("stock_sha256", stock), ("patched_sha256", replacement)):
        expected = entry.get(name)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ArchiveError(f"missing or invalid {name} for MCU {mcu!r}")
        actual = _hash(path)
        if actual != expected:
            raise ArchiveError(f"{name} does not match the checked-in manifest: {path}")


_ARCHIVE_MEMBER = re.compile(r"(?<!\S)(\S*libbt\.a)\([^\s()]+\)")
_GUARD_SECTION = re.compile(
    r"(?m)^\s*\.text\.mesh_bl_narrow_sdk_guard\s+"
    r"0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+\s+"
    r"(\S*libbt\.a)\(btc_dm\.c\.obj\)"
)


def verify_link_map(map_path, project_dir, expected_archive):
    try:
        data = Path(map_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ArchiveError(f"cannot read linker map {map_path}: {exc}") from exc
    guard = _GUARD_SECTION.search(data)
    if not guard:
        raise ArchiveError("link map does not show the SDK guard in the Bluedroid object")
    found = _ARCHIVE_MEMBER.findall(data)
    if not found:
        raise ArchiveError("link map does not identify any libbt.a members")
    expected = os.path.normcase(os.path.realpath(expected_archive))
    for path in found:
        resolved = os.path.normcase(os.path.realpath(os.path.join(project_dir, path)))
        if resolved != expected:
            raise ArchiveError(f"link map references an unexpected Bluedroid archive: {path}")
