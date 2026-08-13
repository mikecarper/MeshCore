#!/usr/bin/env python3
"""Verify and run a pinned RAK3401 chain while rejecting unsafe bridges."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request
import zipfile

try:
    from . import lora_ota as ota
except ImportError:
    import lora_ota as ota


RELEASE_TAG = "rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.01-c96bdd6e"
RELEASE_URL = f"https://github.com/mikecarper/MeshCore/releases/tag/{RELEASE_TAG}"
ASSET_NAME = "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-c96bdd6e.zip"
ASSET_URL = (
    f"https://github.com/mikecarper/MeshCore/releases/download/{RELEASE_TAG}/"
    f"{ASSET_NAME}"
)
ASSET_SHA256 = "46c7480ed6bdc2aa01fb23a0f70e34c4012ffdd42b616d07bde66cf66d594630"
CHECKSUM_LIST_SHA256 = "8c74bbf9ae244f8c32041c68791b2780650c8a9321c23d32206614009739be5f"
BUNDLE_ROOT_NAME = "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.01-c96bdd6e"

# The first v1.17.01 candidate reused an unsafe source-transition bridge at
# step 15. Keep its hashes recognizable for offline diagnosis, but never allow
# it to reach a live device.
KNOWN_FAILED_V11701_ASSET_SHA256 = (
    "693f08187e42cce72124f01328983965726bfbbb3fef80de503f06c4cbe9256a"
)
KNOWN_FAILED_V11701_CHECKSUM_LIST_SHA256 = (
    "a3bd757db2138fc11be766976295051c017ceb02e0c3a22fe1c4c73e93f30f0a"
)

# Keep the withdrawn bundle recognizable for offline diagnosis. Live use is
# still refused before meshcli, password handling, or any device mutation.
KNOWN_UNSAFE_RELEASE_TAG = (
    "rak3401-mota-v1.16.07-c1caa5ad-to-v1.17.02-c96bdd6e"
)
KNOWN_UNSAFE_ASSET_SHA256 = (
    "a7d20449f87436dbf0b2d273e2798ebcb1e3152ccb2528cff71040ecf105a1df"
)
KNOWN_UNSAFE_CHECKSUM_LIST_SHA256 = (
    "751eb571eab4445c9862fc7f2534ad19d74530ae6105cfed1ca16a91d60a54c1"
)
KNOWN_UNSAFE_ROOT_NAME = (
    "RAK3401-update-chain-v1.16.7-c1caa5ad-to-v1.17.2-c96bdd6e"
)

DEFAULT_TARGET_KEY = (
    "63d8df6387eaffd2e25db7d2a8ad967a"
    "65202182a48d681d7e7a9260f917280d"
)
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
EXPECTED_STEP_COUNT = 26
EXPECTED_START_VERSION = "1.16.7.0"
EXPECTED_FINAL_VERSION = "1.17.1.0"
MIN_MESHCLI_VERSION = (1, 6, 0)
WATCHDOG_RESET_WAIT_SECONDS = 90
WATCHDOG_STABILITY_WAIT_SECONDS = 90

# Physical RAK3401 testing proved two retired packages unsafe. The original
# v1.17.02 chain failed at step 6. Its software-SHA replacement then passed
# steps 1 through 14, but the first v1.17.01 candidate failed at step 15 because
# that retained 386ae4a5 bridge still used unchecked CC310 SHA. Keep both
# bundles useful for offline diagnosis, but fail closed before a live
# connection. The second corrected replacement remains behind an explicit
# lab-only gate until its end-to-end physical-board test is complete.
KNOWN_UNSAFE_STEP = 6
KNOWN_UNSAFE_VERSION = "1.16.8.7"
KNOWN_UNSAFE_IMAGE_SHA256 = (
    "61ced8b63953c614748c2fa1b04c2c01e8eb6626a604f6ef95fd2594d6d8ce71"
)
SAFE_CANDIDATE_STEP6_IMAGE_SHA256 = (
    "4bab3d2d6f6d3a033713d2db87565cb5f7fabe29b2b902b911724dd602fb7df8"
)
KNOWN_FAILED_V11701_STEP = 15
KNOWN_FAILED_V11701_VERSION = "1.16.9.111"
KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256 = (
    "e8d9d1bd06217c7fd8d7fd333c6fcfde79000838550aa149b10be12fdc64fccb"
)
SAFE_CANDIDATE_STEP15_IMAGE_SHA256 = (
    "1124247f65772f11f9527408e51971eb9633ed656206276fa95019275bb8fdd2"
)
KNOWN_UNSAFE_RELEASE_MESSAGE = (
    f"live installation of {KNOWN_UNSAFE_RELEASE_TAG} is disabled: a physical RAK3401 "
    f"test reached step {KNOWN_UNSAFE_STEP} (v{KNOWN_UNSAFE_VERSION}) but "
    "that bridge cannot validate its own EndF and cannot install step 7. "
    "Use --verify-only for artifact inspection. Do not deploy this release; "
    "publish and physically test a corrected chain first."
)
KNOWN_FAILED_V11701_MESSAGE = (
    "live installation of the first v1.17.01 candidate is disabled: a physical "
    f"RAK3401 test passed steps 1-14, but step {KNOWN_FAILED_V11701_STEP} "
    f"(v{KNOWN_FAILED_V11701_VERSION}) booted without a usable EndF because its "
    "unchecked CC310 SHA path failed. Use --verify-only for artifact inspection. "
    "Do not deploy this replaced candidate."
)


class KnownUnsafeReleaseError(ota.OtaError):
    """The pinned artifacts are intact but their live transition is unsafe."""


def require_live_release_safe(
    args: argparse.Namespace,
    steps: list[ChainStep],
) -> None:
    step6_sha256 = steps[KNOWN_UNSAFE_STEP - 1].target_sha256
    if step6_sha256 == KNOWN_UNSAFE_IMAGE_SHA256:
        raise KnownUnsafeReleaseError(KNOWN_UNSAFE_RELEASE_MESSAGE)
    if step6_sha256 != SAFE_CANDIDATE_STEP6_IMAGE_SHA256:
        raise KnownUnsafeReleaseError(
            "live installation is disabled: step 6 is not a recognized, "
            "audited RAK3401 bridge image"
        )
    step15_sha256 = steps[KNOWN_FAILED_V11701_STEP - 1].target_sha256
    if step15_sha256 == KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256:
        raise KnownUnsafeReleaseError(KNOWN_FAILED_V11701_MESSAGE)
    if step15_sha256 != SAFE_CANDIDATE_STEP15_IMAGE_SHA256:
        raise KnownUnsafeReleaseError(
            "live installation is disabled: step 15 is not a recognized, "
            "audited RAK3401 SHA-safe bridge image"
        )
    if not args.accept_test_candidate:
        raise KnownUnsafeReleaseError(
            "this corrected bundle has passed offline reconstruction and bootloader "
            "simulation but still needs its first end-to-end board test; rerun with "
            "--accept-test-candidate only on a recoverable lab RAK3401"
        )


@dataclass(frozen=True)
class ChainStep:
    number: int
    from_version: str
    to_version: str
    path: Path
    size: int
    base_hash: bytes
    target_sha256: str
    package: ota.MotaInfo


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: str, label: str) -> PurePosixPath:
    if "\\" in value:
        raise ota.OtaError(f"{label} contains a backslash: {value!r}")
    path = PurePosixPath(value.removeprefix("./"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ota.OtaError(f"{label} is not a safe relative path: {value!r}")
    return path


def download_release_asset(destination: Path) -> None:
    if destination.exists():
        actual = sha256_file(destination)
        if actual != ASSET_SHA256:
            raise ota.OtaError(
                f"cached release asset has SHA-256 {actual}, expected {ASSET_SHA256}: "
                f"{destination}"
            )
        print(f"[bundle] using verified cached asset {destination}")
        return

    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()
    print(f"[bundle] downloading {ASSET_URL}")
    request = urllib.request.Request(
        ASSET_URL,
        headers={"User-Agent": "MeshCore-RAK3401-chain-runner/1"},
    )
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > 64 * 1024 * 1024:
                    raise ota.OtaError("release asset exceeds the 64 MiB safety limit")
                digest.update(chunk)
                output.write(chunk)
        actual = digest.hexdigest()
        if actual != ASSET_SHA256:
            raise ota.OtaError(
                f"downloaded release asset has SHA-256 {actual}, expected {ASSET_SHA256}"
            )
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    print(f"[bundle] downloaded and verified {received} bytes")


def extract_bundle(archive_path: Path, destination: Path) -> Path:
    root_names = (BUNDLE_ROOT_NAME, KNOWN_UNSAFE_ROOT_NAME)
    existing_roots = [destination / name for name in root_names if (destination / name).is_dir()]
    if len(existing_roots) == 1:
        return existing_roots[0]
    if len(existing_roots) > 1:
        raise ota.OtaError(f"bundle extraction contains multiple recognized roots: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise ota.OtaError(
            f"bundle extraction directory is incomplete or unexpected: {destination}"
        )

    staging = destination.with_name(destination.name + f".part-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                safe_relative_path(member.filename.rstrip("/"), "ZIP member")
                mode = (member.external_attr >> 16) & 0o170000
                if stat.S_ISLNK(mode):
                    raise ota.OtaError(f"release ZIP contains a symbolic link: {member.filename}")
            archive.extractall(staging)
        staged_roots = [staging / name for name in root_names if (staging / name).is_dir()]
        if len(staged_roots) != 1:
            raise ota.OtaError(
                f"release ZIP must contain exactly one recognized bundle root: {root_names}"
            )
        staged_root = staged_roots[0]
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination / staged_root.name


def locate_bundle(args: argparse.Namespace, work_dir: Path) -> Path:
    if args.bundle is None:
        archive = work_dir / ASSET_NAME
        download_release_asset(archive)
        return extract_bundle(archive, work_dir / "bundle")

    supplied = args.bundle.resolve()
    if supplied.is_dir():
        direct = supplied / "CHAIN.csv"
        nested_roots = [
            supplied / name for name in (BUNDLE_ROOT_NAME, KNOWN_UNSAFE_ROOT_NAME)
            if (supplied / name / "CHAIN.csv").is_file()
        ]
        if direct.is_file():
            return supplied
        if len(nested_roots) == 1:
            return nested_roots[0]
        raise ota.OtaError(f"bundle directory does not contain CHAIN.csv: {supplied}")
    if not supplied.is_file() or supplied.suffix.lower() != ".zip":
        raise ota.OtaError("--bundle must be the pinned release ZIP or its extracted root")
    actual = sha256_file(supplied)
    if actual not in {
        ASSET_SHA256,
        KNOWN_FAILED_V11701_ASSET_SHA256,
        KNOWN_UNSAFE_ASSET_SHA256,
    }:
        raise ota.OtaError(
            f"release ZIP has SHA-256 {actual}, expected a pinned audited asset: {supplied}"
        )
    return extract_bundle(supplied, work_dir / "bundle")


def verify_checksum_list(bundle_root: Path) -> None:
    checksum_path = bundle_root / "SHA256SUMS.txt"
    if not checksum_path.is_file():
        raise ota.OtaError("bundle is missing SHA256SUMS.txt")
    checksum_digest = sha256_file(checksum_path)
    expected_lists = {
        CHECKSUM_LIST_SHA256,
        KNOWN_FAILED_V11701_CHECKSUM_LIST_SHA256,
        KNOWN_UNSAFE_CHECKSUM_LIST_SHA256,
    }
    if checksum_digest not in expected_lists:
        raise ota.OtaError(
            "bundle checksum list is not the one pinned by this chain runner: "
            f"got {checksum_digest}, expected one of {sorted(expected_lists)}"
        )
    checked = 0
    listed: set[PurePosixPath] = set()
    for line_number, raw_line in enumerate(checksum_path.read_text(encoding="ascii").splitlines(), 1):
        if not raw_line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", raw_line)
        if match is None:
            raise ota.OtaError(f"invalid SHA256SUMS.txt line {line_number}")
        expected, name = match.groups()
        relative = safe_relative_path(name, "checksum entry")
        if relative in listed:
            raise ota.OtaError(f"duplicate checksum entry: {relative}")
        listed.add(relative)
        path = bundle_root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise ota.OtaError(f"checksum entry is missing: {relative}")
        actual = sha256_file(path)
        if actual.lower() != expected.lower():
            raise ota.OtaError(
                f"checksum mismatch for {relative}: got {actual}, expected {expected}"
            )
        checked += 1

    actual_files: set[PurePosixPath] = set()
    for path in bundle_root.rglob("*"):
        if path.is_symlink():
            raise ota.OtaError(f"bundle contains a symbolic link: {path}")
        if path.is_file() and path != checksum_path:
            actual_files.add(PurePosixPath(path.relative_to(bundle_root).as_posix()))
    if listed != actual_files:
        missing = sorted(str(path) for path in actual_files - listed)
        extra = sorted(str(path) for path in listed - actual_files)
        raise ota.OtaError(
            "bundle checksum coverage mismatch; "
            f"unlisted={missing or 'none'}, nonexistent={extra or 'none'}"
        )
    print(f"[bundle] verified all {checked} SHA-256 entries")


def parse_chain(bundle_root: Path) -> tuple[list[ChainStep], bytes]:
    chain_path = bundle_root / "CHAIN.csv"
    try:
        with chain_path.open(newline="", encoding="ascii") as source:
            rows = list(csv.DictReader(source))
    except OSError as exc:
        raise ota.OtaError(f"cannot read {chain_path}: {exc}") from exc
    if len(rows) != EXPECTED_STEP_COUNT:
        raise ota.OtaError(
            f"chain contains {len(rows)} steps, expected {EXPECTED_STEP_COUNT}"
        )

    steps: list[ChainStep] = []
    previous_to: str | None = None
    for expected_number, row in enumerate(rows, 1):
        try:
            number = int(row["step"])
            from_version = row["from_version"]
            to_version = row["to_version"]
            relative = safe_relative_path(row["mota_file"], "mOTA path")
            path = bundle_root.joinpath(*relative.parts)
            size = int(row["mota_size"])
            target_image_size = int(row["target_image_size"])
            base_hash = bytes.fromhex(row["base_body_hash"])
            target_sha256 = row["target_sha256"].lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise ota.OtaError(f"invalid CHAIN.csv row {expected_number}: {exc}") from exc
        if number != expected_number:
            raise ota.OtaError(f"chain step {expected_number} is numbered {number}")
        if previous_to is not None and from_version != previous_to:
            raise ota.OtaError(
                f"chain discontinuity at step {number}: {from_version} follows {previous_to}"
            )
        if not path.is_file() or path.stat().st_size != size:
            raise ota.OtaError(f"chain package size/path mismatch at step {number}: {path}")
        package = ota.parse_mota(path.read_bytes(), path)
        expected_version = ota.parse_version(to_version)
        if expected_version is None or package.fw_version != expected_version:
            raise ota.OtaError(f"step {number} version does not match its mOTA manifest")
        if package.target_id != EXPECTED_TARGET_ID or package.hw_id != EXPECTED_HARDWARE:
            raise ota.OtaError(f"step {number} targets the wrong hardware")
        if package.is_full or package.codec_id != ota.MOTA_CODEC_IN_PLACE:
            raise ota.OtaError(f"step {number} is not an nRF52 in-place delta")
        if package.base_hash != base_hash:
            raise ota.OtaError(f"step {number} base hash does not match CHAIN.csv")
        if package.image_size != target_image_size:
            raise ota.OtaError(f"step {number} target image size does not match CHAIN.csv")
        if package.image_hash.hex() != target_sha256:
            raise ota.OtaError(f"step {number} target SHA-256 does not match CHAIN.csv")
        steps.append(
            ChainStep(
                number, from_version, to_version, path, size, base_hash,
                target_sha256, package,
            )
        )
        previous_to = to_version

    if steps[0].from_version != EXPECTED_START_VERSION:
        raise ota.OtaError("chain has an unexpected starting version")
    step6 = steps[KNOWN_UNSAFE_STEP - 1]
    recognized_step6_hashes = {
        KNOWN_UNSAFE_IMAGE_SHA256,
        SAFE_CANDIDATE_STEP6_IMAGE_SHA256,
    }
    if (
        step6.to_version != KNOWN_UNSAFE_VERSION
        or step6.target_sha256 not in recognized_step6_hashes
    ):
        raise ota.OtaError(
            "bundle does not contain a recognized audited step-6 image; "
            "review and repin the runner before using it"
        )
    if step6.target_sha256 == SAFE_CANDIDATE_STEP6_IMAGE_SHA256:
        step15 = steps[KNOWN_FAILED_V11701_STEP - 1]
        recognized_step15_hashes = {
            KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256,
            SAFE_CANDIDATE_STEP15_IMAGE_SHA256,
        }
        if (
            step15.to_version != KNOWN_FAILED_V11701_VERSION
            or step15.target_sha256 not in recognized_step15_hashes
        ):
            raise ota.OtaError(
                "bundle does not contain a recognized audited step-15 image; "
                "review and repin the runner before using it"
            )
    expected_final_version = (
        "1.17.2.0"
        if step6.target_sha256 == KNOWN_UNSAFE_IMAGE_SHA256
        else EXPECTED_FINAL_VERSION
    )
    if steps[-1].to_version != expected_final_version:
        raise ota.OtaError(
            f"chain ends at {steps[-1].to_version}, expected {expected_final_version}"
        )

    final_recovery_dir = bundle_root / "recovery/final"
    recovery_zips = sorted(
        (final_recovery_dir if final_recovery_dir.is_dir() else bundle_root / "recovery")
        .glob("*.zip")
    )
    if len(recovery_zips) != 1:
        raise ota.OtaError("bundle must contain exactly one recovery ZIP")
    with zipfile.ZipFile(recovery_zips[0]) as recovery:
        try:
            final_image = recovery.read("firmware.bin")
        except KeyError as exc:
            raise ota.OtaError("recovery ZIP is missing firmware.bin") from exc
    final_identity = ota.parse_endf(final_image)
    final_version = ota.parse_version(expected_final_version)
    if (
        final_identity.target_id != EXPECTED_TARGET_ID
        or final_identity.hw_id != EXPECTED_HARDWARE
        or final_identity.fw_version != final_version
        or hashlib.sha256(final_image).hexdigest() != steps[-1].target_sha256
    ):
        raise ota.OtaError("final recovery image does not match the chain endpoint")
    return steps, final_identity.body_hash


def verify_motatool(args: argparse.Namespace, steps: list[ChainStep]) -> None:
    ota.require_command(args.motatool, "motatool")
    for step in steps:
        ota.run_checked(
            [args.motatool, "verify", str(step.path)],
            label=f"verify chain step {step.number:02d}",
            timeout=60,
        )
    print(f"[bundle] motatool verified all {len(steps)} containers")


def require_meshcli_version(command: str) -> None:
    ota.require_command(command, "meshcli")
    result = ota.run_checked([command, "-v"], label="read meshcli version", timeout=30)
    match = re.search(r"\bv(\d+)\.(\d+)\.(\d+)\b", result.stdout + result.stderr)
    if match is None:
        raise ota.OtaError("could not determine meshcli version")
    version = tuple(int(part) for part in match.groups())
    if version < MIN_MESHCLI_VERSION:
        need = ".".join(str(part) for part in MIN_MESHCLI_VERSION)
        got = ".".join(str(part) for part in version)
        raise ota.OtaError(f"meshcli {got} is too old; install {need} or newer")
    print(f"[host] meshcli {'.'.join(str(part) for part in version)}")


def controller_namespace(args: argparse.Namespace, target: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(
        meshcli=args.meshcli,
        controller_serial=args.controller_serial,
        controller_tcp=args.controller_tcp,
        controller_ble=args.controller_ble,
        controller_baud=args.controller_baud,
        reply_timeout=args.reply_timeout,
        target=target,
    )


def resolve_target_by_key(
    controller: ota.Controller,
    key_value: str,
) -> tuple[str, str]:
    key_prefix = key_value.lower().removeprefix("0x")
    if len(key_prefix) < 8 or re.fullmatch(r"[0-9a-f]{8,64}", key_prefix) is None:
        raise ota.OtaError("--target-key must contain 8 to 64 hexadecimal characters")
    matches: list[tuple[str, dict]] = []
    for value in controller._run(["contacts"], "list controller contacts"):
        for public_key, contact in value.items():
            if (
                isinstance(public_key, str)
                and re.fullmatch(r"[0-9a-fA-F]{64}", public_key)
                and public_key.lower().startswith(key_prefix)
                and isinstance(contact, dict)
            ):
                matches.append((public_key.lower(), contact))
    if len(matches) != 1:
        raise ota.OtaError(
            f"target key prefix {key_prefix} matched {len(matches)} controller contacts; need exactly one"
        )
    full_key, contact = matches[0]
    name = contact.get("adv_name")
    if contact.get("type") != 2 or not isinstance(name, str) or not name:
        raise ota.OtaError("target key does not identify a named repeater contact")
    return name, full_key


def source_namespace(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        source_serial=args.source_serial,
        source_tcp=args.source_tcp,
        source_cli_serial=args.source_cli_serial,
        source_cli_tcp=args.source_cli_tcp,
        source_already_temp=args.source_already_temp,
        source_shares_controller=args.source_shares_controller,
        source_companion_terminal=False,
        source_baud=args.source_baud,
        controller_baud=args.controller_baud,
        reply_timeout=args.reply_timeout,
        meshcli=args.meshcli,
    )


def query_live_target(
    controller: ota.Controller,
    args: argparse.Namespace,
    target_name: str,
) -> ota.TargetInfo:
    query_args = controller_namespace(args, target_name)
    target = ota.query_target(controller, query_args)
    if target.target_id != EXPECTED_TARGET_ID:
        raise ota.OtaError(
            f"live target ID is {target.target_id:08X}, expected {EXPECTED_TARGET_ID:08X}"
        )
    if target.hw_id != EXPECTED_HARDWARE:
        raise ota.OtaError(
            f"live hardware is {target.hw_id!r}, expected {EXPECTED_HARDWARE!r}"
        )
    if target.platform != "nrf52" or target.nrf_sd:
        raise ota.OtaError("live target is not the expected internal-flash nRF52 node")
    if target.bootloader_abi is None or target.bootloader_abi < 2:
        raise ota.OtaError("live target does not report OTAFIX mOTA ABI 2")
    if target.bootloader_codecs is None or not target.bootloader_codecs & (1 << ota.MOTA_CODEC_IN_PLACE):
        raise ota.OtaError("live target bootloader does not support in-place codec 2")
    return target


def version_number(value: str) -> int:
    parsed = ota.parse_version(value)
    if parsed is None:
        raise ota.OtaError(f"could not parse live firmware version {value!r}")
    return parsed


def expected_hash_after(
    steps: list[ChainStep],
    final_body_hash: bytes,
    index: int,
) -> bytes:
    return steps[index + 1].base_hash if index + 1 < len(steps) else final_body_hash


def find_resume_index(
    target: ota.TargetInfo,
    steps: list[ChainStep],
    final_body_hash: bytes,
) -> int:
    if target.base_hash == final_body_hash:
        return len(steps)
    matching_indexes = [
        index for index, step in enumerate(steps)
        if target.base_hash == step.base_hash
    ]
    if len(matching_indexes) == 1:
        return matching_indexes[0]
    if len(matching_indexes) > 1:
        raise ota.OtaError(
            "live body hash occurs at multiple points in the pinned chain"
        )
    raise ota.OtaError(
        f"live body hash {target.base_hash.hex().upper()} is not a recognized "
        "point in this exact chain"
    )


def require_watchdog_state(
    controller: ota.Controller,
    target_name: str,
    expected: str,
) -> str:
    reply = controller.remote_command(target_name, "get system.watchdog")
    if re.fullmatch(rf"\s*>\s*{expected}\s*", reply, re.IGNORECASE) is None:
        raise ota.OtaError(
            f"system watchdog must report `> {expected}`; got: {reply}"
        )
    return reply


def read_ota_hops(controller: ota.Controller, target_name: str) -> int:
    reply = controller.remote_command(target_name, "ota config")
    match = re.search(r"\bhops=(\d+)\b", reply)
    if match is None:
        raise ota.OtaError(f"could not read OTA hop policy: {reply}")
    return int(match.group(1))


def enforce_ota_hops(
    controller: ota.Controller,
    target_name: str,
    expected: int,
) -> None:
    current = read_ota_hops(controller, target_name)
    if current != expected:
        reply = controller.remote_command(
            target_name, f"ota config hops {expected}"
        )
        if not reply.startswith(f"OK OTA reach = {expected} hop"):
            raise ota.OtaError(f"target did not accept OTA hop policy: {reply}")
        current = read_ota_hops(controller, target_name)
    if current != expected:
        raise ota.OtaError(
            f"target OTA reach is {current} hops, expected {expected}"
        )
    print(f"[radio] destination OTA reach verified at {current} hops")


def wait_with_label(seconds: int, label: str) -> None:
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(f"[wait] {label}: {int(remaining + 0.999)} seconds remaining", flush=True)
        time.sleep(min(30, remaining))


def prepare_watchdog(
    controller: ota.Controller,
    target_name: str,
) -> None:
    reply = controller.remote_command(target_name, "get system.watchdog")
    if re.fullmatch(r"\s*>\s*on\s*", reply, re.IGNORECASE):
        disable_reply = controller.remote_command(
            target_name, "set system.watchdog off"
        )
        if not disable_reply.lower().startswith("ok - disabled"):
            raise ota.OtaError(f"target did not accept watchdog disable: {disable_reply}")
        print("[watchdog] disabled; do not issue a normal reboot")
        wait_with_label(
            WATCHDOG_RESET_WAIT_SECONDS,
            "waiting for the inherited watchdog reset and reconnection",
        )
        require_watchdog_state(controller, target_name, "off")
    elif re.fullmatch(r"\s*>\s*off\s*", reply, re.IGNORECASE) is None:
        raise ota.OtaError(f"could not determine watchdog state: {reply}")

    wait_with_label(
        WATCHDOG_STABILITY_WAIT_SECONDS,
        "proving stable uptime with the watchdog off",
    )
    require_watchdog_state(controller, target_name, "off")
    controller.remote_command(target_name, "ota self")
    print("[watchdog] off and target remained responsive through the stability window")


def next_attempt_dir(work_dir: Path, step_number: int) -> Path:
    parent = work_dir / "steps"
    parent.mkdir(exist_ok=True)
    for attempt in range(1, 1000):
        candidate = parent / f"step-{step_number:02d}-attempt-{attempt:03d}"
        if not candidate.exists():
            return candidate
    raise ota.OtaError(f"too many saved attempts for step {step_number}")


def append_progress(
    work_dir: Path,
    step: ChainStep,
    body_hash: bytes,
) -> None:
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "step": step.number,
        "from_version": step.from_version,
        "to_version": step.to_version,
        "body_hash": body_hash.hex().upper(),
    }
    with (work_dir / "progress.jsonl").open("a", encoding="ascii") as output:
        output.write(json.dumps(record, sort_keys=True) + "\n")


def clear_completed_download(
    controller: ota.Controller,
    target_name: str,
    step: ChainStep,
) -> None:
    """Clear only a retained record for the exact, proven installed step."""
    status = controller.remote_command(target_name, "ota status")
    active_id = ota.download_manifest_id(status)
    if active_id is None:
        if "no download" not in status.lower():
            raise ota.OtaError(
                f"post-install download state is ambiguous after step {step.number}: "
                f"{status}"
            )
        return
    if active_id != step.package.manifest_id:
        raise ota.OtaError(
            f"post-install target retained mOTA {active_id} after step "
            f"{step.number}; expected only {step.package.manifest_id}"
        )
    if "ready to install" not in status.lower():
        raise ota.OtaError(
            f"post-install target still has an active step-{step.number} session: "
            f"{status}"
        )
    reply = controller.remote_command(target_name, "ota cancel")
    if not reply.startswith("OK"):
        raise ota.OtaError(
            f"could not clear completed step-{step.number} staging record: {reply}"
        )
    status = controller.remote_command(target_name, "ota status")
    if "no download" not in status.lower():
        raise ota.OtaError(
            f"completed step-{step.number} staging record remains: {status}"
        )
    print(f"[chain] cleared retained staging record for step {step.number:02d}")


def connection_arguments(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.controller_serial:
        values.extend(["--controller-serial", args.controller_serial])
    elif args.controller_tcp:
        values.extend(["--controller-tcp", args.controller_tcp])
    else:
        values.extend(["--controller-ble", args.controller_ble])

    if args.source_serial:
        values.extend(["--source-serial", args.source_serial])
    else:
        values.extend(["--source-tcp", args.source_tcp])
        if args.source_cli_serial:
            values.extend(["--source-cli-serial", args.source_cli_serial])
        elif args.source_cli_tcp:
            values.extend(["--source-cli-tcp", args.source_cli_tcp])
        elif args.source_already_temp:
            values.append("--source-already-temp")
        if args.source_shares_controller:
            values.append("--source-shares-controller")
    return values


def run_step(
    args: argparse.Namespace,
    target_name: str,
    step: ChainStep,
    previous_step: ChainStep | None,
    work_dir: Path,
) -> None:
    command = [
        str(step.path),
        target_name,
        *connection_arguments(args),
        "--controller-baud", str(args.controller_baud),
        "--source-baud", str(args.source_baud),
        "--temp-radio", args.temp_radio,
        "--meshcli", args.meshcli,
        "--motatool", args.motatool,
        "--reply-timeout", str(args.reply_timeout),
        "--discovery-timeout", str(args.discovery_timeout),
        "--discovery-interval", str(args.discovery_interval),
        "--poll-seconds", str(args.poll_seconds),
        "--transfer-timeout-minutes", str(args.transfer_timeout_minutes),
        "--seeder-start-wait", str(args.seeder_start_wait),
        "--reboot-wait", str(args.reboot_wait),
        "--work-dir", str(next_attempt_dir(work_dir, step.number)),
        "--require-system-watchdog-off",
        # Some audited bridge binaries retain a later historical runtime
        # version string than their pinned EndF chain version. The exact base
        # hash, target hash, package ID, and ordered step still gate the move.
        "--allow-non-upgrade",
        "--yes",
    ]
    if previous_step is not None:
        command.extend([
            "--clear-completed-manifest", previous_step.package.manifest_id,
            "--clear-completed-on-body-hash", step.base_hash.hex().upper(),
        ])
    for relay in args.relay:
        command.extend(["--relay", relay])
    result = ota.main(command)
    if result != 0:
        raise ota.OtaError(f"chain step {step.number} exited with status {result}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download, verify, resume, and install the exact 26-step RAK3401 "
            "mOTA chain from c1caa5ad to c96bdd6e."
        )
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        help="pinned release ZIP or extracted bundle root (downloads it when omitted)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path.cwd() / "rak3401-mota-chain-work",
        help="persistent cache, logs, and resume records",
    )
    parser.add_argument("--target-key", default=DEFAULT_TARGET_KEY)
    parser.add_argument(
        "--accept-test-candidate",
        action="store_true",
        help=(
            "permit the corrected, offline-validated chain on a recoverable lab "
            "RAK3401 before its first complete physical test"
        ),
    )

    controller = parser.add_mutually_exclusive_group()
    controller.add_argument("--controller-serial")
    controller.add_argument("--controller-tcp")
    controller.add_argument("--controller-ble")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-serial")
    source.add_argument("--source-tcp")
    parser.add_argument("--source-cli-serial")
    parser.add_argument("--source-cli-tcp")
    parser.add_argument("--source-already-temp", action="store_true")
    parser.add_argument("--source-shares-controller", action="store_true")

    parser.add_argument(
        "--relay",
        action="append",
        default=[],
        metavar="NAME[=PASSWORD]",
        help="intermediate relay, farthest-to-nearest; repeat for each relay",
    )
    parser.add_argument("--temp-radio", default="909.950,250,7,5,120")
    parser.add_argument(
        "--ota-hops",
        type=int,
        default=3,
        help="destination OTA receive/relay reach to enforce before every step",
    )
    parser.add_argument("--controller-baud", type=int, default=115200)
    parser.add_argument("--source-baud", type=int, default=115200)
    parser.add_argument("--meshcli", default="meshcli")
    parser.add_argument("--motatool", default="motatool")
    parser.add_argument("--reply-timeout", type=int, default=45)
    parser.add_argument("--discovery-timeout", type=int, default=180)
    parser.add_argument("--discovery-interval", type=int, default=8)
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="seconds between transfer status checks; sparse checks leave airtime for OTA",
    )
    parser.add_argument("--transfer-timeout-minutes", type=int, default=90)
    parser.add_argument("--seeder-start-wait", type=int, default=5)
    parser.add_argument("--reboot-wait", type=int, default=90)
    parser.add_argument("--keep-watchdog-off", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="verify the pinned bundle and all mOTA containers without connecting",
    )
    mode.add_argument(
        "--preflight-only",
        action="store_true",
        help="also validate the live source and destination, but change no radio settings",
    )
    parser.add_argument("--yes", action="store_true")
    return parser


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.controller_serial is None and args.controller_tcp is None and args.controller_ble is None:
        args.controller_serial = "/dev/ttyACM0"
    if not args.verify_only and args.source_serial is None and args.source_tcp is None:
        parser.error("live operation requires --source-serial or --source-tcp")
    if args.source_serial and (args.source_cli_serial or args.source_cli_tcp):
        parser.error("--source-cli-serial/--source-cli-tcp are only used with --source-tcp")
    if args.source_tcp and not (
        args.source_cli_serial or args.source_cli_tcp or args.source_already_temp
    ):
        parser.error(
            "--source-tcp also needs --source-cli-serial, --source-cli-tcp, or --source-already-temp"
        )
    if args.source_already_temp and not args.source_tcp:
        parser.error("--source-already-temp requires --source-tcp")
    if args.source_shares_controller and not (
        args.source_tcp and args.source_cli_tcp
    ):
        parser.error(
            "--source-shares-controller requires --source-tcp and --source-cli-tcp"
        )
    if args.source_shares_controller and args.source_already_temp:
        parser.error(
            "--source-shares-controller and --source-already-temp are mutually exclusive"
        )
    for name in (
        "controller_baud", "source_baud", "reply_timeout", "discovery_timeout",
        "discovery_interval", "poll_seconds", "transfer_timeout_minutes",
        "seeder_start_wait", "reboot_wait",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if not 0 <= args.ota_hops <= 8:
        parser.error("--ota-hops must be from 0 through 8")
    try:
        temp_values = ota.parse_temp_radio(args.temp_radio)
    except argparse.ArgumentTypeError as exc:
        parser.error(f"--temp-radio: {exc}")
    remote_setup_seconds = (1 + len(args.relay)) * args.reply_timeout
    required_seconds = (
        remote_setup_seconds
        + (0 if args.source_already_temp or args.source_shares_controller else 30)
        + ota.TEMP_RADIO_SWITCH_DELAY_SECONDS
        + args.seeder_start_wait
        + args.discovery_timeout
        + args.transfer_timeout_minutes * 60
        + args.poll_seconds
        + args.reply_timeout * (4 + len(args.relay))
    )
    if not args.verify_only and temp_values[4] * 60 <= required_seconds:
        parser.error(
            "TempRadio window is too short for the selected relay count and timeouts"
        )


def confirm_chain(
    args: argparse.Namespace,
    target_name: str,
    full_key: str,
    target: ota.TargetInfo,
    first_index: int,
    steps: list[ChainStep],
) -> None:
    print("\nValidated RAK3401 chain plan:")
    print(f"  bundle      : {args.bundle or RELEASE_URL}")
    print(f"  destination : {target_name}")
    print(f"  public key  : {full_key}")
    print(f"  target      : {target.target_id:08X} hw={target.hw_id}")
    print(f"  running     : {target.current_version} {target.base_hash.hex().upper()}")
    if first_index == len(steps):
        print("  action      : endpoint already installed")
    else:
        print(
            f"  action      : steps {steps[first_index].number}-{steps[-1].number} "
            f"through {steps[-1].to_version}"
        )
    print(f"  TempRadio   : {args.temp_radio}")
    print(f"  relays      : {len(args.relay)}")
    print(f"  OTA reach   : enforce {args.ota_hops} hops before every step")
    print("  watchdog    : disable, prove stable, gate every install, then re-enable")
    if args.preflight_only:
        print("Live preflight passed; no radio or watchdog settings were changed.")
        return
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise ota.OtaError("non-interactive execution requires --yes")
    answer = input("Continue with the complete RAK3401 chain? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise ota.OtaError("cancelled by operator")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        bundle_root = locate_bundle(args, work_dir)
        verify_checksum_list(bundle_root)
        steps, final_body_hash = parse_chain(bundle_root)
        verify_motatool(args, steps)
        if args.verify_only:
            print(f"Verified release bundle: {bundle_root}")
            if steps[KNOWN_UNSAFE_STEP - 1].target_sha256 == KNOWN_UNSAFE_IMAGE_SHA256:
                print(f"WARNING: {KNOWN_UNSAFE_RELEASE_MESSAGE}", file=sys.stderr)
            elif (
                steps[KNOWN_FAILED_V11701_STEP - 1].target_sha256
                == KNOWN_FAILED_V11701_STEP15_IMAGE_SHA256
            ):
                print(f"WARNING: {KNOWN_FAILED_V11701_MESSAGE}", file=sys.stderr)
            else:
                print(
                    "Corrected test candidate verified offline; a complete physical-board "
                    "run is still required before production use."
                )
            return 0

        # This must precede meshcli, password handling, source preflight, and
        # every radio/watchdog mutation. Structural checks passing does not
        # make either physically failed chain safe to deploy.
        require_live_release_safe(args, steps)

        require_meshcli_version(args.meshcli)
        password = os.environ.get("MESHCORE_ADMIN_PASSWORD", "")
        if not password:
            if not sys.stdin.isatty():
                raise ota.OtaError("set MESHCORE_ADMIN_PASSWORD for non-interactive use")
            password = getpass.getpass("RAK3401 admin password: ")
        if any(character in password for character in "\r\n\0"):
            raise ota.OtaError("admin password contains an unsupported control character")

        source_args = source_namespace(args)
        ota.preflight_source_cli(source_args)
        controller = ota.Controller(controller_namespace(args), password)
        ota.verify_shared_source_identity(controller, source_args)
        target_name, full_key = resolve_target_by_key(controller, args.target_key)
        target = query_live_target(controller, args, target_name)
        first_index = find_resume_index(target, steps, final_body_hash)
        confirm_chain(args, target_name, full_key, target, first_index, steps)
        if args.preflight_only:
            return 0

        if first_index == len(steps):
            enforce_ota_hops(controller, target_name, args.ota_hops)
            if not args.keep_watchdog_off:
                enabled = controller.remote_command(target_name, "set system.watchdog on")
                if not enabled.lower().startswith("ok - system watchdog enabled"):
                    raise ota.OtaError(f"could not enable system watchdog: {enabled}")
                require_watchdog_state(controller, target_name, "on")
            print("RAK3401 already matches the verified final endpoint.")
            return 0

        prepare_watchdog(controller, target_name)
        enforce_ota_hops(controller, target_name, args.ota_hops)
        for index in range(first_index, len(steps)):
            step = steps[index]
            resolved_name, current_key = resolve_target_by_key(controller, args.target_key)
            if current_key != full_key or resolved_name != target_name:
                raise ota.OtaError("target contact identity changed during the chain")
            target = query_live_target(controller, args, target_name)
            current_index = find_resume_index(target, steps, final_body_hash)
            if current_index > index:
                print(f"[chain] step {step.number:02d} is already installed; continuing")
                continue
            if current_index != index:
                raise ota.OtaError(
                    f"live target is at chain index {current_index}, expected {index}"
                )
            require_watchdog_state(controller, target_name, "off")
            enforce_ota_hops(controller, target_name, args.ota_hops)
            print(
                f"\n[chain] step {step.number:02d}/{len(steps)}: "
                f"{step.from_version} -> {step.to_version}"
            )
            previous_step = steps[index - 1] if index > 0 else None
            run_step(args, target_name, step, previous_step, work_dir)

            target = query_live_target(controller, args, target_name)
            expected_hash = expected_hash_after(steps, final_body_hash, index)
            if target.base_hash != expected_hash:
                raise ota.OtaError(
                    f"step {step.number} returned body hash {target.base_hash.hex().upper()}, "
                    f"expected {expected_hash.hex().upper()}"
                )
            if (
                target.current_version_source == "ota stats"
                and version_number(target.current_version or "")
                != version_number(step.to_version)
            ):
                raise ota.OtaError(
                    f"step {step.number} EndF metadata reports version "
                    f"{target.current_version}, expected {step.to_version}"
                )
            if (
                target.current_version_source == "ver"
                and version_number(target.current_version or "")
                != version_number(step.to_version)
            ):
                print(
                    f"[chain] step {step.number:02d} exact body hash is verified; "
                    f"runtime label {target.current_version} is historical and is "
                    f"not the EndF chain version {step.to_version}"
                )
            clear_completed_download(controller, target_name, step)
            require_watchdog_state(controller, target_name, "off")
            append_progress(work_dir, step, target.base_hash)
            print(f"[chain] step {step.number:02d} verified")

        final_target = query_live_target(controller, args, target_name)
        if find_resume_index(final_target, steps, final_body_hash) != len(steps):
            raise ota.OtaError("final target identity did not match the release endpoint")
        if not args.keep_watchdog_off:
            enabled = controller.remote_command(target_name, "set system.watchdog on")
            if not enabled.lower().startswith("ok - system watchdog enabled"):
                raise ota.OtaError(f"could not enable system watchdog: {enabled}")
            require_watchdog_state(controller, target_name, "on")
            print("[watchdog] re-enabled after verified step 26 boot")
        print(
            f"RAK3401 update complete: {final_target.current_version} "
            f"{final_target.base_hash.hex().upper()}"
        )
        return 0
    except KnownUnsafeReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("No device connection or setting change was attempted.", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print(
            "\nInterrupted. Any partial mOTA remains resumable. The target watchdog "
            "may intentionally remain off until the chain completes.",
            file=sys.stderr,
        )
        return 130
    except (ota.OtaError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "The target watchdog may intentionally remain off. Rerun the same command "
            "to resume from the live version; do not skip a chain step.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
