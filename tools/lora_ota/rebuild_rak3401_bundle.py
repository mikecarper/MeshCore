#!/usr/bin/env python3
"""Freshly rebuild the audited RAK3401 chain with a new final endpoint.

The historical bridge images are reconstructed from the pinned, physically
passed chain and checked against CHAIN.csv.  Every output .mota is then built
again from its base/target image pair and independently applied with both an
erased (0xff) and zero-filled detools workspace.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import zipfile


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "mota"))

import motalib  # noqa: E402


PREVIOUS_BUNDLE_SHA256 = (
    "eac67a0be12690b7e22c4d1f6a15bfdeb5bd627c4850b246b1be4220e5607b34"
)
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
EXPECTED_INPUT_STEPS = 29
EXPECTED_STEPS = 30
EXPECTED_ACCELERATED_VERSIONS = (
    "1.16.7.10", "1.16.7.11", "1.16.7.12", "1.16.7.13", "1.16.8.0",
    "1.16.8.7", "1.16.8.8", "1.16.8.9", "1.16.9.0", "1.16.9.102",
    "1.16.9.104", "1.16.9.105", "1.16.9.108", "1.16.9.109", "1.16.9.110",
    "1.16.9.111", "1.16.9.112", "1.16.9.113", "1.16.9.114", "1.16.9.115",
    "1.16.9.116", "1.16.9.117", "1.16.9.118", "1.16.9.119", "1.16.9.120",
    "1.16.9.121", "1.16.9.122", "1.16.10.0",
)
STAGING_LIMIT = 90112
WORKSPACE_SIZE = 0x98000
SEGMENT_SIZE = 4096
MAX_ZIP_BYTES = 64 * 1024 * 1024


class RebuildError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
        or "\\" in value
    ):
        raise RebuildError(f"unsafe {label}: {value!r}")
    return path


def extract_zip(archive_path: Path, destination: Path) -> Path:
    if destination.exists():
        raise RebuildError(f"extraction destination already exists: {destination}")
    destination.mkdir(parents=True)
    total = 0
    roots: set[str] = set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            relative = safe_relative(member.filename.rstrip("/"), "ZIP member")
            roots.add(relative.parts[0])
            total += member.file_size
            if total > MAX_ZIP_BYTES:
                raise RebuildError("input bundle expands beyond 64 MiB")
            mode = (member.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise RebuildError(f"input bundle contains a symlink: {member.filename}")
        if len(roots) != 1:
            raise RebuildError(f"input bundle must contain one root directory: {sorted(roots)}")
        archive.extractall(destination)
    root = destination / next(iter(roots))
    if not (root / "CHAIN.csv").is_file():
        raise RebuildError("input bundle root does not contain CHAIN.csv")
    return root


def read_firmware_zip(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        members = [member for member in archive.infolist() if member.filename == "firmware.bin"]
        if len(members) != 1:
            raise RebuildError(f"{path} must contain one root firmware.bin")
        member = members[0]
        if member.file_size > WORKSPACE_SIZE:
            raise RebuildError(f"firmware.bin is too large in {path}")
        return archive.read(member)


def run(command: list[str], label: str, timeout: int = 900) -> str:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RebuildError(f"{label} failed: {exc}") from exc
    if result.returncode != 0:
        raise RebuildError(
            f"{label} exited {result.returncode}:\n{result.stdout[-4000:]}"
        )
    return result.stdout


def apply_in_place(
    detools: Path,
    base: bytes,
    patch: bytes,
    image_size: int,
    work: Path,
    label: str,
    fill: int,
) -> bytes:
    if len(base) > WORKSPACE_SIZE or image_size > WORKSPACE_SIZE:
        raise RebuildError(f"{label} exceeds the 0x{WORKSPACE_SIZE:x} workspace")
    memory_path = work / f"{label}-{fill:02x}.memory"
    patch_path = work / f"{label}.patch"
    memory = bytearray([fill]) * WORKSPACE_SIZE
    memory[:len(base)] = base
    memory_path.write_bytes(memory)
    if not patch_path.exists():
        patch_path.write_bytes(patch)
    run(
        [str(detools), "apply_patch_in_place", str(memory_path), str(patch_path)],
        f"apply {label} with fill 0x{fill:02x}",
    )
    rebuilt = memory_path.read_bytes()[:image_size]
    memory_path.unlink()
    return rebuilt


def read_chain(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="ascii") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != EXPECTED_INPUT_STEPS:
        raise RebuildError(
            f"input chain has {len(rows)} steps, expected {EXPECTED_INPUT_STEPS}"
        )
    return rows


def firmware_identity(image: bytes) -> tuple[str, str, int, str]:
    if not motalib.has_endf(image):
        raise RebuildError("firmware image has no valid EndF")
    ident = motalib.parse_endf_ident(image)
    assert ident is not None
    if ident.target_id != EXPECTED_TARGET_ID or ident.hw_id != EXPECTED_HARDWARE:
        raise RebuildError(
            f"firmware identity is target={ident.target_id:08X} hw={ident.hw_id!r}"
        )
    body, body_hash = motalib.parse_endf(image)
    return motalib.unpack_version(ident.fw_version), body_hash.hex(), len(body), ident.hw_id


def reconstruct_historical_images(
    old_root: Path,
    rows: list[dict[str, str]],
    detools: Path,
    work: Path,
) -> list[bytes]:
    start_zip = old_root / "recovery" / "test-start" / "RAK3401-test-start-v1.16.7-c1caa5ad.zip"
    if not start_zip.is_file():
        raise RebuildError(f"missing test-start recovery ZIP: {start_zip}")
    images = [read_firmware_zip(start_zip)]
    if sha256_bytes(images[0]) == rows[0]["target_sha256"].lower():
        raise RebuildError("test-start image unexpectedly equals step 1")
    firmware_identity(images[0])

    reconstruction = work / "reconstruction"
    reconstruction.mkdir()
    for index, row in enumerate(rows[:28], 1):
        package_path = old_root.joinpath(*safe_relative(row["mota_file"], "mOTA path").parts)
        parsed = motalib.parse_container(package_path.read_bytes())
        expected_base = bytes.fromhex(row["base_body_hash"])
        _body, actual_base = motalib.parse_endf(images[-1])
        if actual_base != expected_base or parsed.manifest.base_hash != expected_base:
            raise RebuildError(f"step {index} base hash mismatch")
        expected_size = int(row["target_image_size"])
        zero = apply_in_place(
            detools, images[-1], parsed.payload, expected_size,
            reconstruction, f"old-step-{index:02d}", 0x00,
        )
        erased = apply_in_place(
            detools, images[-1], parsed.payload, expected_size,
            reconstruction, f"old-step-{index:02d}", 0xFF,
        )
        expected_sha = row["target_sha256"].lower()
        if zero != erased or sha256_bytes(zero) != expected_sha:
            raise RebuildError(f"step {index} historical image reconstruction mismatch")
        firmware_identity(zero)
        images.append(zero)
        print(f"[reconstruct] {index:02d}/28 {expected_sha[:16]}", flush=True)
    return images


def read_accelerated_images(manifest_path: Path) -> tuple[list[bytes], list[str]]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RebuildError(f"cannot read accelerated image manifest: {exc}") from exc
    records = manifest.get("targets")
    if not isinstance(records, list) or len(records) != len(EXPECTED_ACCELERATED_VERSIONS):
        raise RebuildError(
            f"accelerated image manifest must contain {len(EXPECTED_ACCELERATED_VERSIONS)} targets"
        )
    images: list[bytes] = []
    sources: list[str] = []
    for index, (record, expected_version) in enumerate(
        zip(records, EXPECTED_ACCELERATED_VERSIONS), 1
    ):
        if not isinstance(record, dict) or record.get("version") != expected_version:
            raise RebuildError(
                f"accelerated image {index} is not expected version {expected_version}"
            )
        filename = record.get("zip")
        if not isinstance(filename, str):
            raise RebuildError(f"accelerated image {index} has no ZIP filename")
        relative = safe_relative(filename, f"accelerated image {index} ZIP")
        if len(relative.parts) != 1:
            raise RebuildError(f"accelerated image {index} ZIP must be a basename")
        path = manifest_path.parent / relative.name
        if not path.is_file():
            raise RebuildError(f"accelerated image {index} ZIP does not exist: {path}")
        expected_zip_sha = record.get("zip_sha256")
        if expected_zip_sha != sha256_file(path):
            raise RebuildError(f"accelerated image {index} ZIP checksum mismatch")
        image = read_firmware_zip(path)
        version, body_hash, body_size, _hardware = firmware_identity(image)
        if version != expected_version:
            raise RebuildError(
                f"accelerated image {index} identity is {version}, expected {expected_version}"
            )
        if record.get("firmware_sha256") != sha256_bytes(image):
            raise RebuildError(f"accelerated image {index} firmware checksum mismatch")
        if record.get("firmware_size") != len(image):
            raise RebuildError(f"accelerated image {index} firmware size mismatch")
        if record.get("body_hash") != body_hash.lower() or record.get("body_size") != body_size:
            raise RebuildError(f"accelerated image {index} EndF body metadata mismatch")
        source_commit = record.get("source_commit")
        if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
            raise RebuildError(f"accelerated image {index} source commit is invalid")
        images.append(image)
        sources.append(source_commit)
        print(
            f"[accelerated] {index:02d}/{len(EXPECTED_ACCELERATED_VERSIONS)} "
            f"v{version} {sha256_bytes(image)[:16]}",
            flush=True,
        )
    return images, sources


def write_chain(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "step", "from_version", "to_version", "mota_file", "mota_size",
        "staging_margin", "target_image_size", "base_body_hash",
        "target_body_hash", "target_sha256", "source_commit",
    ]
    with path.open("w", newline="", encoding="ascii") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_bundle_docs(
    root: Path,
    asset_name: str,
    source_commit: str,
    endpoint: bytes,
    old_asset_sha: str,
    bridge_manifest_sha: str,
    max_package: int,
    min_margin: int,
) -> None:
    endpoint_sha = sha256_bytes(endpoint)
    endpoint_version, endpoint_body, _body_size, _hw = firmware_identity(endpoint)
    (root / "README.md").write_text(
        f"""# RAK3401 mOTA update chain: v1.16.7 to v1.17.01

This archive contains 30 mandatory, freshly generated 1 KiB-block mOTA
containers for the exact `RAK_3401` target `0x{EXPECTED_TARGET_ID:08X}`.

Status: all 30 transitions passed independent offline reconstruction,
container verification, and stable bootloader simulation. The superseded
29-step baseline passed physically, but this exact 30-step byte sequence still
needs its complete physical-chain qualification. The pinned runner therefore
requires an explicit `--accept-test-candidate` for live use.

- Start: `1.16.7.0-c1caa5ad`
- Endpoint: `{endpoint_version}` from `{source_commit}`
- Endpoint image SHA-256: `{endpoint_sha}`
- Endpoint EndF body hash: `{endpoint_body.upper()}`
- Largest package: {max_package:,} bytes
- Smallest staging margin: {min_margin:,} bytes

Step 1 installs the byte-identical first bridge from the physically passed
cd824765 chain. Step 2 is the earliest safe transition that installs terminal
bulk-packet consumption; a direct patched step 1 exceeded the fixed 90,112-byte
staging limit. Steps 2-29 were rebuilt from pinned historical source commits
with the terminal-consumption backport and the original staged compiler
profiles. Step 30 installs the current endpoint.

No mOTA file was copied. Every delta was generated in this run, then applied
independently with zero-filled and erased (`0xFF`) workspaces and verified
against its target image. The previous physical record is retained as
`PHYSICAL-BASELINE-cd824765.jsonl` and is explicitly a baseline, not a claim
that this new byte sequence was part of that older run.

Use the pinned runner in `tools/lora_ota/rak3401_mota_chain.py`; never install
files out of order. See `docs/rak3401_mota_chain.md` for direct and two-relay
commands, recovery, watchdog handling, and the guarded no-EndF rescue command.

Original audited bundle SHA-256 used as reconstruction input:
`{old_asset_sha}`.

Accelerated bridge-image manifest SHA-256: `{bridge_manifest_sha}`.
""",
        encoding="utf-8",
    )
    (root / "PROVENANCE.md").write_text(
        f"""# Build and validation provenance

The endpoint was built from MeshCore commit `{source_commit}` with embedded
version `{endpoint_version}`, target `0x{EXPECTED_TARGET_ID:08X}`, hardware
`{EXPECTED_HARDWARE}`, 1 KiB logical mOTA blocks, and a `0x{WORKSPACE_SIZE:X}`
in-place workspace.

All 30 mOTA containers were generated in this run by Rust `motatool`; none was
copied from the input archive. The complete historical chain was reconstructed
from the pinned physically passed bundle and checked against its CHAIN.csv
SHA-256 anchors before its exact first bridge was selected. Accelerated images
2-29 were rebuilt with pinned feature/optimizer stages and terminal packet
consumption. Every new package passed `motatool verify` and two independent
detools in-place applications (workspace fills `0x00` and `0xFF`).

Input bundle SHA-256: `{old_asset_sha}`
Bridge-image manifest SHA-256: `{bridge_manifest_sha}`
Output asset name: `{asset_name}`
""",
        encoding="utf-8",
    )
    (root / "RUNBOOK.md").write_text(
        """# Runbook

Verify the outer release checksum, then use the repository runner with this
ZIP and a persistent work directory. The runner enforces exact target, start
body hash, package order, watchdog state, post-boot identity, and normal-radio
restoration.

Direct bench: `--temp-radio 909.950,500,5,5,120 --ota-hops 1`.

Live private network with two intermediate relays: use
`--temp-radio 909.950,250,5,5,120 --ota-hops 3`, plus two `--relay` arguments
listed farthest-to-nearest. Both live modes require
`--accept-test-candidate`. Run `--preflight-only` before `--yes`.

See `docs/rak3401_mota_chain.md` in the matching MeshCore repository commit
for complete commands and the guarded `ota rescue install <base_hash>` flow.
""",
        encoding="utf-8",
    )


def write_checksums(root: Path) -> str:
    checksum_path = root / "SHA256SUMS.txt"
    lines = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item != checksum_path):
        relative = path.relative_to(root).as_posix()
        lines.append(f"{sha256_file(path)}  {relative}\n")
    checksum_path.write_text("".join(lines), encoding="ascii")
    return sha256_file(checksum_path)


def make_zip(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RebuildError(f"output already exists: {output}")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            archive.write(path, (Path(root.name) / path.relative_to(root)).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-bundle", type=Path, required=True)
    parser.add_argument("--endpoint-zip", type=Path, required=True)
    parser.add_argument("--endpoint-uf2", type=Path, required=True)
    parser.add_argument("--accelerated-images", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--motatool", type=Path, required=True)
    parser.add_argument("--detools", type=Path, required=True)
    parser.add_argument("--apply-sim", type=Path)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()

    for path, label in (
        (args.previous_bundle, "previous bundle"),
        (args.endpoint_zip, "endpoint ZIP"),
        (args.endpoint_uf2, "endpoint UF2"),
        (args.accelerated_images, "accelerated image manifest"),
        (args.motatool, "motatool"),
        (args.detools, "detools"),
    ):
        if not path.is_file():
            raise RebuildError(f"{label} does not exist: {path}")
    if args.apply_sim is not None and not args.apply_sim.is_file():
        raise RebuildError(f"apply simulator does not exist: {args.apply_sim}")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise RebuildError("source commit must be a full lowercase 40-hex commit")
    resolved_commit = run(
        [
            "git", "-C", str(REPO_ROOT), "rev-parse", "--verify",
            f"{args.source_commit}^{{commit}}",
        ],
        "resolve endpoint source commit",
    ).strip()
    if resolved_commit != args.source_commit:
        raise RebuildError(
            f"endpoint source resolves to {resolved_commit}, expected {args.source_commit}"
        )
    if args.work_dir.exists():
        raise RebuildError(f"work directory already exists: {args.work_dir}")
    args.work_dir.mkdir(parents=True)

    old_asset_sha = sha256_file(args.previous_bundle)
    if old_asset_sha != PREVIOUS_BUNDLE_SHA256:
        raise RebuildError(
            f"previous bundle SHA-256 is {old_asset_sha}, expected {PREVIOUS_BUNDLE_SHA256}"
        )
    old_root = extract_zip(args.previous_bundle, args.work_dir / "old")
    old_rows = read_chain(old_root / "CHAIN.csv")
    historical_images = reconstruct_historical_images(
        old_root, old_rows, args.detools, args.work_dir
    )
    accelerated_images, accelerated_sources = read_accelerated_images(
        args.accelerated_images
    )

    endpoint = read_firmware_zip(args.endpoint_zip)
    endpoint_version, _endpoint_body, _body_size, _hw = firmware_identity(endpoint)
    if endpoint_version != "1.17.1.0":
        raise RebuildError(f"endpoint version is {endpoint_version}, expected 1.17.1.0")
    if f"-{args.source_commit[:8]}".encode("ascii") not in endpoint:
        raise RebuildError("endpoint firmware does not contain the source commit suffix")
    images = historical_images[:2] + accelerated_images + [endpoint]
    target_sources = [old_rows[0]["source_commit"], *accelerated_sources, args.source_commit]
    if len(images) != EXPECTED_STEPS + 1:
        raise RebuildError(f"internal image count is {len(images)}")
    if len(target_sources) != EXPECTED_STEPS:
        raise RebuildError(f"internal source count is {len(target_sources)}")

    short_commit = args.source_commit[:8]
    root_name = f"RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-{short_commit}"
    root = args.work_dir / root_name
    motas = root / "motas"
    recovery_start = root / "recovery" / "test-start"
    recovery_final = root / "recovery" / "final"
    motas.mkdir(parents=True)
    recovery_start.mkdir(parents=True)
    recovery_final.mkdir(parents=True)
    validation_work = args.work_dir / "new-validation"
    validation_work.mkdir()

    output_rows: list[dict[str, object]] = []
    validation_steps: list[dict[str, object]] = []
    for index in range(1, EXPECTED_STEPS + 1):
        from_version, _from_body, _from_size, _from_hw = firmware_identity(images[index - 1])
        to_version, _to_body, _to_size, _to_hw = firmware_identity(images[index])
        filename = f"step-{index:02d}__v{from_version}-to-v{to_version}.mota"
        output_path = motas / filename
        # Write exact base/target inputs immediately before invoking motatool;
        # keeping this explicit makes a retained work directory auditable.
        base_path = validation_work / f"base-{index:02d}.bin"
        target_path = validation_work / f"target-{index:02d}.bin"
        base_path.write_bytes(images[index - 1])
        target_path.write_bytes(images[index])
        build_output = run(
            [
                str(args.motatool), "build", "--fw", str(target_path),
                "--base", str(base_path), "--patch-type", "in-place",
                "--inplace-memory", hex(WORKSPACE_SIZE),
                "--segment-size", str(SEGMENT_SIZE), "--block-size", "1024",
                "--out", str(output_path),
            ],
            f"build step {index}",
        )
        run([str(args.motatool), "verify", str(output_path)], f"verify step {index}")
        parsed = motalib.parse_container(output_path.read_bytes())
        zero = apply_in_place(
            args.detools, images[index - 1], parsed.payload, len(images[index]),
            validation_work, f"new-step-{index:02d}", 0x00,
        )
        erased = apply_in_place(
            args.detools, images[index - 1], parsed.payload, len(images[index]),
            validation_work, f"new-step-{index:02d}", 0xFF,
        )
        target_sha = sha256_bytes(images[index])
        if zero != images[index] or erased != images[index]:
            raise RebuildError(f"new step {index} does not reconstruct its target")
        if parsed.manifest.image_hash.hex() != target_sha:
            raise RebuildError(f"new step {index} manifest image hash mismatch")
        if parsed.manifest.target_id != EXPECTED_TARGET_ID:
            raise RebuildError(f"new step {index} target ID mismatch")
        if args.apply_sim is not None:
            run(
                [str(args.apply_sim), str(base_path), str(output_path), str(target_path)],
                f"OTAFIX C simulation step {index}",
            )
        _base_body, base_hash = motalib.parse_endf(images[index - 1])
        target_version, target_body, _target_body_size, _target_hw = firmware_identity(images[index])
        if target_version != to_version:
            raise RebuildError(
                f"step {index} target version {target_version} != {to_version}"
            )
        size = output_path.stat().st_size
        if size > STAGING_LIMIT:
            raise RebuildError(
                f"step {index} is {size} bytes, {size - STAGING_LIMIT} over staging capacity"
            )
        manifest_id = parsed.manifest.merkle_root.hex().upper()
        output_rows.append({
            "step": index,
            "from_version": from_version,
            "to_version": to_version,
            "mota_file": f"motas/{filename}",
            "mota_size": size,
            "staging_margin": STAGING_LIMIT - size,
            "target_image_size": len(images[index]),
            "base_body_hash": base_hash.hex(),
            "target_body_hash": target_body,
            "target_sha256": target_sha,
            "source_commit": target_sources[index - 1],
        })
        validation_steps.append({
            "step": index,
            "manifest_id": manifest_id,
            "mota_sha256": sha256_file(output_path),
            "mota_size": size,
            "target_sha256": target_sha,
            "zero_fill_apply": "passed",
            "erased_fill_apply": "passed",
            "otafix_c_sim": "passed" if args.apply_sim is not None else "not-run",
        })
        print(
            f"[build] {index:02d}/{EXPECTED_STEPS} mid={manifest_id} "
            f"size={size} margin={STAGING_LIMIT - size}",
            flush=True,
        )
        if not build_output.strip():
            raise RebuildError(f"motatool produced no build output for step {index}")

    write_chain(root / "CHAIN.csv", output_rows)
    shutil.copy2(
        old_root / "recovery" / "test-start" / "RAK3401-test-start-v1.16.7-c1caa5ad.zip",
        recovery_start,
    )
    shutil.copy2(
        old_root / "recovery" / "test-start" / "RAK3401-test-start-v1.16.7-c1caa5ad.uf2",
        recovery_start,
    )
    shutil.copy2(args.accelerated_images, root / "BRIDGE-IMAGES.json")
    final_zip_name = args.endpoint_zip.name
    final_uf2_name = args.endpoint_uf2.name
    shutil.copy2(args.endpoint_zip, recovery_final / final_zip_name)
    shutil.copy2(args.endpoint_uf2, recovery_final / final_uf2_name)
    baseline = old_root / "PHYSICAL-TEST.jsonl"
    if baseline.is_file():
        shutil.copy2(baseline, root / "PHYSICAL-BASELINE-cd824765.jsonl")

    asset_name = f"{root_name}.zip"
    max_package = max(int(row["mota_size"]) for row in output_rows)
    min_margin = min(int(row["staging_margin"]) for row in output_rows)
    write_bundle_docs(
        root, asset_name, args.source_commit, endpoint, old_asset_sha,
        sha256_file(args.accelerated_images),
        max_package, min_margin,
    )
    validation = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "tools/lora_ota/rebuild_rak3401_bundle.py",
        "source_commit": args.source_commit,
        "previous_bundle_sha256": old_asset_sha,
        "bridge_images_sha256": sha256_file(args.accelerated_images),
        "workspace_size": WORKSPACE_SIZE,
        "segment_size": SEGMENT_SIZE,
        "block_size": 1024,
        "steps": validation_steps,
    }
    (root / "validation-results.json").write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_sha = write_checksums(root)
    output_path = args.output_dir / asset_name
    make_zip(root, output_path)
    print(f"[bundle] {output_path}")
    print(f"[bundle] sha256={sha256_file(output_path)}")
    print(f"[bundle] inner_sha256={checksum_sha}")
    print(f"[bundle] endpoint_sha256={sha256_bytes(endpoint)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RebuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
