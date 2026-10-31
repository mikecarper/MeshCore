#!/usr/bin/env python3
"""Stage existing migration ZIPs against exact, already-published Full artifacts.

This tool has no network or publication capability. Inputs must first be checked
against the public release's uploaded SHA-256/size. Originals are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import zipfile

from build_esp32_partition_migration import bundle_release
from firmware_memory_manifest import validate_package
from package_esp32_partition_migration import (
    BOARDS, check_esp32_stage, mota_full, pack_version, parse_endf_ident,
)

MAX_ARCHIVE = 32 << 20
SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,255}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unpack_verified(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    if not 0 < len(raw) <= MAX_ARCHIVE:
        raise ValueError("migration archive exceeds its byte bound")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        inventory = archive.infolist()
        if not 4 <= len(inventory) <= 32 or sum(i.file_size for i in inventory) > MAX_ARCHIVE:
            raise ValueError("invalid migration ZIP inventory")
        files = {}
        for entry in inventory:
            name = entry.filename
            if (not SAFE_NAME.fullmatch(name) or name in {".", ".."}
                    or name in files or entry.flag_bits & 1
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.file_size <= 0 or entry.compress_size > MAX_ARCHIVE):
                raise ValueError("unsafe migration ZIP member")
            files[name] = archive.read(entry)
    manifest = json.loads(files["manifest.json"])
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ValueError("invalid migration manifest")
    if set(files) != set(manifest["files"]) | {"manifest.json", "SHA256SUMS.txt"}:
        raise ValueError("migration manifest inventory mismatch")
    for name, record in manifest["files"].items():
        if record != {"bytes": len(files[name]), "sha256": digest(files[name])}:
            raise ValueError("migration member size or SHA-256 mismatch")
    sums = {}
    for line in files["SHA256SUMS.txt"].decode("ascii").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]{1,255})", line)
        if not match or match[2] in sums:
            raise ValueError("invalid migration checksum inventory")
        sums[match[2]] = match[1]
    if sums != {name: digest(data) for name, data in files.items() if name != "SHA256SUMS.txt"}:
        raise ValueError("migration checksum inventory mismatch")
    if not {"full-application.bin", "target-partitions.bin", "capabilities.json", "README.md"} <= files.keys():
        raise ValueError("incomplete migration archive")
    return manifest, files


def application_for(target: str, directory: Path, version: str, source: str) -> Path | None:
    if not SAFE_NAME.fullmatch(target) or target in {".", ".."}:
        raise ValueError("invalid exact migration target")
    candidates = [path for path in directory.iterdir()
                  if path.is_file() and path.name.startswith(target + "-full-")
                  and path.name.endswith(f"-{version}-{source}.bin")]
    if len(candidates) > 1:
        raise ValueError(f"ambiguous published Full application for {target}")
    if candidates and candidates[0].is_symlink():
        raise ValueError("published application cannot be a symlink")
    return candidates[0] if candidates else None


def repair_archive(raw: bytes, application_dir: Path, version: str, source: str) -> tuple[bytes, dict]:
    manifest, files = unpack_verified(raw)
    board, target = manifest["board"], manifest["target"]
    if (board not in BOARDS or BOARDS[board]["target"] != target
            or BOARDS[board]["role"] != manifest.get("role")
            or manifest["firmware_version"] != version or manifest["source_commit"] != source):
        raise ValueError("migration archive identity does not match the requested release")
    published = application_for(target, application_dir, version, source)
    report = {"board": board, "target": target, "old_archive_sha256": digest(raw),
              "old_application_sha256": digest(files["full-application.bin"])}
    if published is None:
        return raw, {**report, "state": "unmapped", "archive_sha256": digest(raw),
                     "reason": "No exact published Full target in input inventory; original retained unchanged."}

    stem = published.with_suffix("")
    if any(path.is_symlink() for path in published.parent.iterdir() if path.name.startswith(stem.name + ".") or path.name == stem.name + "-merged.bin"):
        raise ValueError("published package files cannot be symlinks")
    validate_package(stem)
    image = published.read_bytes()
    capabilities = published.with_suffix(".capabilities.json").read_bytes()
    caps = json.loads(capabilities)
    if (caps.get("target") != target or caps.get("artifact_target", target) != target
            or caps.get("build_profile") != "full" or caps.get("platform") != "ESP32_PLATFORM"
            or caps.get("ota_update_verified") is not True):
        raise ValueError("published Full capabilities do not match the exact migration target")
    old_ident = parse_endf_ident(files["full-application.bin"])
    identity = parse_endf_ident(image)
    if (old_ident is None or identity is None or identity != old_ident
            or identity.target_id != int(manifest["target_id"], 16)
            or identity.hw_id != manifest["hardware_id"]
            or identity.fw_version != pack_version(version.split("-", 1)[0])):
        raise ValueError("published application EndF identity/version differs from the migration target")
    merged = (published.parent / (stem.name + "-merged.bin")).read_bytes()
    if (merged[0x10000:] != image
            or merged[0x8000:0x9000] != files["target-partitions.bin"]):
        raise ValueError("published merged application or partition table differs from the migration plan")
    slot = manifest["expanded_slot_bytes"]
    if type(slot) is not int or not 0 < len(image) < slot:
        raise ValueError("published application does not fit the expanded slot")

    original = dict(files)
    files["full-application.bin"] = image
    files["capabilities.json"] = capabilities
    if "full-application.mota" in files:
        files["full-application.mota"] = mota_full(image, identity, 2048)
        check_esp32_stage(files["full-application.mota"], image, slot)
        manifest["full_mota_seeder_scratch_bytes"] = ((len(image) + 2047) // 2048) * 4
    for notice in caps.get("third_party_notices", []):
        name = notice.get("file", "")
        if not SAFE_NAME.fullmatch(name) or name in {".", ".."}:
            raise ValueError("unsafe published license notice")
        notice_path = published.parent / name
        if notice_path.is_symlink():
            raise ValueError("published notice cannot be a symlink")
        data = notice_path.read_bytes()
        if len(data) > 1 << 20 or digest(data) != notice.get("sha256"):
            raise ValueError("published license notice differs from its capability manifest")
        files[name] = data
    # Do not regenerate bridges, tables, instructions or their identities.
    for name in original:
        if name.endswith("bridge.bin") or name.endswith("bridge.mota") or name in {"target-partitions.bin", "README.md", "partition-expander.bin", "partition-expander.mota"}:
            if files[name] != original[name]:
                raise ValueError("repair changed a migration bridge, table or instructions")
    files.pop("manifest.json")
    files.pop("SHA256SUMS.txt")
    manifest["files"] = {name: {"bytes": len(data), "sha256": digest(data)}
                         for name, data in files.items()}
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    files["SHA256SUMS.txt"] = "".join(f"{digest(data)}  {name}\n" for name, data in sorted(files.items())).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    repaired = output.getvalue()
    unpack_verified(repaired)
    return repaired, {**report, "state": "updated", "application": published.name,
                      "application_sha256": digest(image), "archive_sha256": digest(repaired)}


def stage(package_dir: Path, application_dir: Path, output_dir: Path, version: str, source: str) -> dict:
    if not re.fullmatch(r"v\d+(?:\.\d+){2,3}(?:-[A-Za-z0-9._-]+)?", version) or not re.fullmatch(r"[a-f0-9]{8}", source):
        raise ValueError("invalid release version or eight-character source")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be empty; original inputs are never overwritten")
    output_dir.mkdir(parents=True, exist_ok=True)
    packages = output_dir / "packages"
    packages.mkdir()
    reports, boards = [], []
    for path in sorted(package_dir.glob(f"*-{version}-{source}-migration.zip")):
        if path.is_symlink():
            raise ValueError("input migration archive cannot be a symlink")
        raw = path.read_bytes()
        manifest, _ = unpack_verified(raw)
        board = manifest["board"]
        if path.name != f"{board}-{version}-{source}-migration.zip" or board in boards:
            raise ValueError("input migration filename or board inventory is ambiguous")
        repaired, report = repair_archive(raw, application_dir, version, source)
        (packages / path.name).write_bytes(repaired)
        boards.append(board)
        reports.append(report)
    if not reports:
        raise ValueError("no exact release migration archives found")
    bundle = bundle_release(output_dir, packages, boards, version, source, "usa-cascade-fixed", "cascade")
    report = {"format": "meshcore-migration-release-repair-v1", "version": version,
              "source": source, "updated": sum(r["state"] == "updated" for r in reports),
              "unmapped": sum(r["state"] == "unmapped" for r in reports),
              "bundle": bundle.name, "bundle_sha256": digest(bundle.read_bytes()), "packages": reports}
    (output_dir / "repair-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--application-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    report = stage(args.package_dir, args.application_dir, args.output_dir, args.version, args.source)
    print(f"Staged {report['updated']} exact application repairs; {report['unmapped']} unmapped originals retained. No publication performed.")


if __name__ == "__main__":
    main()
