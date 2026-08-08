#!/usr/bin/env python3
"""End-to-end MeshCore LoRa OTA orchestration.

This program deliberately uses the public ``meshcli`` and ``motatool`` CLIs
instead of duplicating either protocol.  It prepares a safe package, moves a
remote destination (and optional relays) to TempRadio, attaches a host folder
to an OTA source, monitors the pull, installs it, and restores the controller's
saved radio settings.

Run through ``lora_ota.sh`` or ``lora_ota.ps1``; see
``docs/lora_ota_automation.md`` for the required two-radio topology.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
import zipfile


MOTA_MAGIC = b"mOTA"
MOTA_TRAILER = b"vk496"
MOTA_FORMAT_VERSION = 2
MOTA_FIXED_MANIFEST_SIZE = 197
MOTA_FLAG_FULL = 0x01
MOTA_CODEC_FULL = 0
MOTA_CODEC_SEQUENTIAL = 1
MOTA_CODEC_IN_PLACE = 2
ENDF_MAGIC = b"EndF"
ENDF_SIZE = 56
MAX_ARCHIVE_MEMBER_SIZE = 64 * 1024 * 1024
MAX_FIRMWARE_IMAGE_SIZE = 64 * 1024 * 1024


class OtaError(RuntimeError):
    """Expected, actionable operator error."""


@dataclass(frozen=True)
class EndFInfo:
    image: bytes
    body_hash: bytes
    fw_version: int
    target_id: int
    hw_id: str


@dataclass(frozen=True)
class MotaInfo:
    path: Path | None
    blob: bytes
    flags: int
    target_id: int
    fw_version: int
    image_size: int
    payload_size: int
    block_size: int
    merkle_root: bytes
    image_hash: bytes
    codec_id: int
    hw_id: str
    base_hash: bytes
    payload_offset: int

    @property
    def is_full(self) -> bool:
        return bool(self.flags & MOTA_FLAG_FULL)

    @property
    def kind(self) -> str:
        return "full" if self.is_full else "delta"

    @property
    def version(self) -> str:
        return format_version(self.fw_version)

    @property
    def manifest_id(self) -> str:
        return self.merkle_root.hex().upper()

    @property
    def payload(self) -> bytes:
        return self.blob[self.payload_offset:self.payload_offset + self.payload_size]


@dataclass(frozen=True)
class TargetInfo:
    name: str
    target_id: int
    base_hash: bytes
    platform: str
    nrf_sd: bool
    hw_id: str | None
    bootloader_abi: int | None
    bootloader_codecs: int | None
    status: str
    self_status: str
    current_version: str | None = None


@dataclass(frozen=True)
class RadioSettings:
    frequency: float
    bandwidth: float
    spreading_factor: int
    coding_rate: int
    repeat: bool

    def meshcli_value(self) -> str:
        repeat = "on" if self.repeat else "off"
        return (f"{format_decimal(self.frequency)},"
                f"{format_decimal(self.bandwidth)},"
                f"{self.spreading_factor},{self.coding_rate},{repeat}")


def format_decimal(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def format_version(value: int) -> str:
    parts = [(value >> shift) & 0xFF for shift in (24, 16, 8, 0)]
    text = f"v{parts[0]}.{parts[1]}.{parts[2]}"
    return f"{text}.{parts[3]}" if parts[3] else text


def parse_version(value: str) -> int | None:
    match = re.fullmatch(r"[vV]?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", value.strip())
    if not match:
        return None
    parts = [int(part or 0) for part in match.groups()]
    if any(part > 0xFF for part in parts):
        return None
    return sum(part << shift for part, shift in zip(parts, (24, 16, 8, 0)))


def parse_hex_exact(value: str, size: int, label: str) -> bytes:
    value = value.strip().removeprefix("0x").removeprefix("0X")
    if not re.fullmatch(rf"[0-9A-Fa-f]{{{size * 2}}}", value):
        raise OtaError(f"{label} must be exactly {size * 2} hexadecimal characters")
    return bytes.fromhex(value)


def merkle_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        raise OtaError("mOTA payload has no blocks")
    level = list(leaves)
    while len(level) > 1:
        next_level: list[bytes] = []
        for index in range(0, len(level), 2):
            if index + 1 == len(level):
                next_level.append(level[index])
            else:
                next_level.append(
                    hashlib.sha256(level[index] + level[index + 1]).digest()[:4]
                )
        level = next_level
    return level[0]


def parse_mota(blob: bytes, path: Path | None = None) -> MotaInfo:
    if len(blob) < 8 + MOTA_FIXED_MANIFEST_SIZE + len(MOTA_TRAILER):
        raise OtaError("mOTA is truncated")
    if blob[:4] != MOTA_MAGIC or blob[-5:] != MOTA_TRAILER:
        raise OtaError("not a MeshCore mOTA container")
    declared_size = struct.unpack_from("<I", blob, 4)[0]
    if declared_size != len(blob):
        raise OtaError(
            f"mOTA size field is {declared_size}, but the file is {len(blob)} bytes"
        )
    if blob[8] != MOTA_FORMAT_VERSION:
        raise OtaError(f"unsupported mOTA format version {blob[8]}")
    if blob[10] != 0x12:
        raise OtaError(f"unsupported mOTA hash algorithm 0x{blob[10]:02x}")

    flags = blob[9]
    target_id, fw_version, image_size, payload_size = struct.unpack_from("<IIII", blob, 11)
    block_size_log2 = blob[27]
    if block_size_log2 > 20:
        raise OtaError("invalid mOTA block size")
    block_size = 1 << block_size_log2
    root = blob[28:32]
    image_hash = blob[32:64]
    codec_id = blob[64]
    hw_id = blob[65:97].split(b"\0", 1)[0].decode("ascii", "replace")
    base_hash = blob[97:105]
    block_count = math.ceil(payload_size / block_size) if payload_size else 0
    leaves_offset = 8 + MOTA_FIXED_MANIFEST_SIZE
    payload_offset = leaves_offset + block_count * 4
    payload_end = payload_offset + payload_size
    if payload_end != len(blob) - len(MOTA_TRAILER):
        raise OtaError("mOTA manifest geometry does not match its file size")

    payload = blob[payload_offset:payload_end]
    calculated_leaves = [
        hashlib.sha256(payload[offset:offset + block_size]).digest()[:4]
        for offset in range(0, len(payload), block_size)
    ]
    stored_leaves = [
        blob[leaves_offset + index * 4:leaves_offset + (index + 1) * 4]
        for index in range(block_count)
    ]
    if calculated_leaves != stored_leaves:
        raise OtaError("mOTA block hashes do not match its payload")
    if merkle_root(calculated_leaves) != root:
        raise OtaError("mOTA Merkle root does not match its payload")
    if bool(flags & MOTA_FLAG_FULL):
        if codec_id != MOTA_CODEC_FULL:
            raise OtaError("full mOTA has a non-full codec")
        if image_size != payload_size:
            raise OtaError("full mOTA image and payload sizes differ")
        if hashlib.sha256(payload).digest() != image_hash:
            raise OtaError("full mOTA image hash does not match its payload")
        identity = parse_endf(payload)
        if identity.target_id and identity.target_id != target_id:
            raise OtaError("full mOTA manifest and firmware target IDs differ")
        if identity.fw_version and identity.fw_version != fw_version:
            raise OtaError("full mOTA manifest and firmware versions differ")
        if identity.hw_id and hw_id and identity.hw_id != hw_id:
            raise OtaError("full mOTA manifest and firmware hardware IDs differ")
    elif codec_id == MOTA_CODEC_FULL:
        raise OtaError("delta mOTA declares the full-image codec")

    return MotaInfo(
        path=path,
        blob=blob,
        flags=flags,
        target_id=target_id,
        fw_version=fw_version,
        image_size=image_size,
        payload_size=payload_size,
        block_size=block_size,
        merkle_root=root,
        image_hash=image_hash,
        codec_id=codec_id,
        hw_id=hw_id,
        base_hash=base_hash,
        payload_offset=payload_offset,
    )


def parse_endf(image: bytes) -> EndFInfo:
    if len(image) < ENDF_SIZE:
        raise OtaError("firmware image is too small to contain EndF")
    trailer = image[-ENDF_SIZE:]
    if trailer[:4] != ENDF_MAGIC:
        raise OtaError("firmware image has no EndF identity trailer")
    body = image[:-ENDF_SIZE]
    body_size = struct.unpack_from("<I", trailer, 4)[0]
    body_hash = trailer[8:16]
    if body_size != len(body):
        raise OtaError("firmware EndF body length is inconsistent")
    if hashlib.sha256(body).digest()[:8] != body_hash:
        raise OtaError("firmware EndF body hash is invalid")
    fw_version, target_id = struct.unpack_from("<II", trailer, 16)
    hw_id = trailer[24:56].split(b"\0", 1)[0].decode("ascii", "replace")
    return EndFInfo(image, body_hash, fw_version, target_id, hw_id)


def parse_intel_hex(raw: bytes) -> bytes:
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise OtaError("Intel HEX is not ASCII") from exc
    base = 0
    segments: list[tuple[int, bytes]] = []
    saw_eof = False
    for line_number, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise OtaError(f"bad Intel HEX record on line {line_number}")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise OtaError(f"bad Intel HEX digits on line {line_number}") from exc
        if len(record) < 5 or record[0] + 5 != len(record):
            raise OtaError(f"bad Intel HEX length on line {line_number}")
        if sum(record) & 0xFF:
            raise OtaError(f"bad Intel HEX checksum on line {line_number}")
        count = record[0]
        offset = (record[1] << 8) | record[2]
        record_type = record[3]
        data = record[4:4 + count]
        if record_type == 0:
            segments.append((base + offset, data))
        elif record_type == 1:
            saw_eof = True
            break
        elif record_type == 2 and len(data) == 2:
            base = int.from_bytes(data, "big") << 4
        elif record_type == 4 and len(data) == 2:
            base = int.from_bytes(data, "big") << 16
    if not saw_eof or not segments:
        raise OtaError("Intel HEX is missing data or its EOF record")
    lowest = min(address for address, _ in segments)
    highest = max(address + len(data) for address, data in segments)
    if highest - lowest > MAX_FIRMWARE_IMAGE_SIZE:
        raise OtaError("Intel HEX address span is unexpectedly large")
    image = bytearray(b"\xFF" * (highest - lowest))
    for address, data in segments:
        start = address - lowest
        image[start:start + len(data)] = data
    return bytes(image)


def read_firmware_file(path: Path) -> bytes:
    raw = path.read_bytes()
    return parse_intel_hex(raw) if path.suffix.lower() == ".hex" else raw


def read_zip_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
    if member.is_dir():
        raise OtaError(f"ZIP member is a directory: {member.filename}")
    if member.file_size > MAX_ARCHIVE_MEMBER_SIZE:
        raise OtaError(f"ZIP member is unexpectedly large: {member.filename}")
    try:
        return archive.read(member)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise OtaError(f"cannot read ZIP member {member.filename}: {exc}") from exc


def compatible_mota(info: MotaInfo, target: TargetInfo) -> tuple[bool, str]:
    if info.target_id != target.target_id:
        return False, f"target {info.target_id:08X}, need {target.target_id:08X}"
    if info.hw_id and target.hw_id and info.hw_id != target.hw_id:
        return False, f"hardware {info.hw_id!r}, destination is {target.hw_id!r}"
    if info.is_full:
        if target.platform == "nrf52" and not target.nrf_sd:
            return False, "internal-flash nRF52 accepts only an in-place delta"
    else:
        if info.base_hash != target.base_hash:
            return False, (
                f"base {info.base_hash.hex().upper()}, target runs "
                f"{target.base_hash.hex().upper()}"
            )
        expected_codec = (
            MOTA_CODEC_IN_PLACE if target.platform == "nrf52" else MOTA_CODEC_SEQUENTIAL
        )
        if info.codec_id != expected_codec:
            return False, f"codec {info.codec_id}, need {expected_codec} for {target.platform}"
    if target.platform == "nrf52":
        if (
            target.bootloader_abi is not None
            and target.bootloader_abi < MOTA_FORMAT_VERSION
        ):
            return False, (
                f"bootloader ABI {target.bootloader_abi} cannot apply mOTA format "
                f"{MOTA_FORMAT_VERSION}"
            )
        if (
            target.bootloader_codecs is not None
            and not target.bootloader_codecs & (1 << info.codec_id)
        ):
            return False, (
                f"bootloader codec mask 0x{target.bootloader_codecs:X} does not "
                f"support codec {info.codec_id}"
            )
    return True, ""


def select_mota_from_zip(
    archive: zipfile.ZipFile,
    target: TargetInfo,
    requested_member: str | None,
) -> tuple[MotaInfo, str] | None:
    candidates: list[tuple[MotaInfo, str]] = []
    rejected: list[str] = []
    for member in archive.infolist():
        if not member.filename.lower().endswith(".mota"):
            continue
        if requested_member and member.filename != requested_member:
            continue
        try:
            info = parse_mota(read_zip_member(archive, member))
            good, reason = compatible_mota(info, target)
            if good:
                candidates.append((info, member.filename))
            else:
                rejected.append(f"{member.filename}: {reason}")
        except (OtaError, zipfile.BadZipFile) as exc:
            rejected.append(f"{member.filename}: {exc}")
    if not candidates:
        if requested_member and requested_member.lower().endswith(".mota"):
            details = "; ".join(rejected) or "member not found"
            raise OtaError(f"requested ZIP mOTA is unusable: {details}")
        return None

    # Prefer the newest version. For an equal version, prefer a matching delta
    # because it transfers much faster; retain deterministic archive ordering.
    candidates.sort(
        key=lambda item: (item[0].fw_version, not item[0].is_full), reverse=True
    )
    best_version = candidates[0][0].fw_version
    best_is_full = candidates[0][0].is_full
    equally_ranked = [
        item for item in candidates
        if item[0].fw_version == best_version and item[0].is_full == best_is_full
    ]
    distinct = {item[0].manifest_id for item in equally_ranked}
    if len(distinct) > 1 and not requested_member:
        names = ", ".join(item[1] for item in equally_ranked)
        raise OtaError(
            "ZIP contains multiple equally suitable mOTAs; select one with "
            f"--zip-member: {names}"
        )
    return candidates[0]


def select_firmware_from_zip(
    archive: zipfile.ZipFile,
    target_id: int,
    requested_member: str | None,
) -> tuple[EndFInfo, str]:
    candidates: list[tuple[EndFInfo, str]] = []
    failures: list[str] = []
    for member in archive.infolist():
        suffix = Path(member.filename).suffix.lower()
        if suffix not in (".bin", ".hex"):
            continue
        if requested_member and member.filename != requested_member:
            continue
        try:
            raw = read_zip_member(archive, member)
            image = parse_intel_hex(raw) if suffix == ".hex" else raw
            identity = parse_endf(image)
            if identity.target_id == target_id:
                candidates.append((identity, member.filename))
            else:
                failures.append(
                    f"{member.filename}: target {identity.target_id:08X}"
                )
        except OtaError as exc:
            failures.append(f"{member.filename}: {exc}")
    if not candidates:
        detail = "; ".join(failures[:6]) or "no .bin/.hex with a valid EndF"
        raise OtaError(
            f"ZIP has no firmware image for target {target_id:08X} ({detail})"
        )
    by_hash: dict[bytes, tuple[EndFInfo, str]] = {
        hashlib.sha256(item[0].image).digest(): item for item in candidates
    }
    if len(by_hash) > 1 and not requested_member:
        names = ", ".join(item[1] for item in candidates)
        raise OtaError(
            "ZIP contains multiple different matching firmware images; select one "
            f"with --zip-member: {names}"
        )
    return next(iter(by_hash.values()))


def load_base_image(path: Path, target: TargetInfo) -> EndFInfo:
    suffix = path.suffix.lower()
    if suffix == ".mota":
        info = parse_mota(path.read_bytes(), path)
        if not info.is_full:
            raise OtaError("--base mOTA must be a full-image container")
        identity = parse_endf(info.payload)
    elif suffix == ".zip":
        identities: list[tuple[EndFInfo, str]] = []
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    suffix = Path(member.filename).suffix.lower()
                    try:
                        if suffix == ".mota":
                            candidate = parse_mota(read_zip_member(archive, member))
                            if not candidate.is_full:
                                continue
                            candidate_identity = parse_endf(candidate.payload)
                        elif suffix in (".bin", ".hex"):
                            raw = read_zip_member(archive, member)
                            image = parse_intel_hex(raw) if suffix == ".hex" else raw
                            candidate_identity = parse_endf(image)
                        else:
                            continue
                    except OtaError:
                        continue
                    if candidate_identity.target_id == target.target_id:
                        identities.append((candidate_identity, member.filename))
        except zipfile.BadZipFile as exc:
            raise OtaError(f"invalid base ZIP archive: {path}") from exc
        matching = [
            item for item in identities if item[0].body_hash == target.base_hash
        ]
        if not matching:
            found = ", ".join(
                f"{name}={identity.body_hash.hex().upper()}"
                for identity, name in identities[:6]
            ) or "no valid matching-target firmware"
            raise OtaError(
                "base ZIP does not contain the firmware currently running on the "
                f"destination ({found})"
            )
        distinct = {hashlib.sha256(item[0].image).digest() for item in matching}
        if len(distinct) > 1:
            names = ", ".join(item[1] for item in matching)
            raise OtaError(
                "base ZIP contains multiple different images with the running body "
                f"hash; pass an exact .bin/.hex/full.mota instead ({names})"
            )
        identity = matching[0][0]
    elif suffix in (".bin", ".hex"):
        identity = parse_endf(read_firmware_file(path))
    else:
        raise OtaError("--base must be a .bin, .hex, .zip, or full .mota")
    if identity.target_id != target.target_id:
        raise OtaError(
            f"base target is {identity.target_id:08X}, destination is "
            f"{target.target_id:08X}"
        )
    if identity.hw_id and target.hw_id and identity.hw_id != target.hw_id:
        raise OtaError(
            f"base hardware is {identity.hw_id!r}, destination is {target.hw_id!r}"
        )
    running_version = (
        parse_version(target.current_version) if target.current_version else None
    )
    if running_version is not None and identity.fw_version != running_version:
        raise OtaError(
            f"base firmware is {format_version(identity.fw_version)}, destination "
            f"reports {target.current_version}"
        )
    if identity.body_hash != target.base_hash:
        raise OtaError(
            "base image is not the firmware currently running on the destination: "
            f"file={identity.body_hash.hex().upper()} "
            f"node={target.base_hash.hex().upper()}"
        )
    return identity


def run_checked(
    command: list[str],
    *,
    label: str,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    print(f"[run] {label}")
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OtaError(f"required command was not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OtaError(f"timed out while running {label}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise OtaError(f"{label} failed: {detail or f'exit {result.returncode}'}")
    return result


def verify_with_motatool(
    motatool: str, package: Path, public_key: Path | None
) -> None:
    command = [motatool, "verify", str(package)]
    if public_key:
        command.extend(["--pub", str(public_key)])
    result = run_checked(command, label=f"verify {package.name}", timeout=120)
    print(result.stdout.strip())


def prepare_package(
    args: argparse.Namespace,
    target: TargetInfo,
    work_dir: Path,
) -> tuple[Path, MotaInfo, bytes | None]:
    source = args.package.resolve()
    if not source.is_file():
        raise OtaError(f"package does not exist: {source}")
    served_dir = work_dir / "served"
    served_dir.mkdir(parents=True, exist_ok=False)
    selected: MotaInfo | None = None
    selected_blob: bytes | None = None
    new_identity: EndFInfo | None = None

    if source.suffix.lower() == ".mota":
        selected_blob = source.read_bytes()
        selected = parse_mota(selected_blob, source)
    elif source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source) as archive:
                mota_member = select_mota_from_zip(
                    archive, target, args.zip_member
                )
                if mota_member is not None:
                    selected, member_name = mota_member
                    selected_blob = selected.blob
                    print(f"[package] selected {member_name} from {source.name}")
                else:
                    new_identity, member_name = select_firmware_from_zip(
                        archive, target.target_id, args.zip_member
                    )
                    print(f"[package] selected raw firmware {member_name}")
        except zipfile.BadZipFile as exc:
            raise OtaError(f"invalid ZIP archive: {source}") from exc
    else:
        raise OtaError("PACKAGE must be a .mota or .zip file")

    if selected is not None:
        good, reason = compatible_mota(selected, target)
        if not good:
            raise OtaError(f"package is not installable on {target.name}: {reason}")
        output = served_dir / f"{selected.manifest_id.lower()}.mota"
        output.write_bytes(selected_blob if selected_blob is not None else selected.blob)
        selected = parse_mota(output.read_bytes(), output)
    else:
        if new_identity is None:
            raise OtaError("could not obtain firmware from the input package")
        new_image = work_dir / "new-firmware.bin"
        new_image.write_bytes(new_identity.image)
        output = served_dir / "update.mota"
        command = [
            args.motatool,
            "build",
            "--fw",
            str(new_image),
            "--out",
            str(output),
        ]
        if target.platform == "nrf52" and (not target.nrf_sd or args.base is not None):
            if args.base is None:
                raise OtaError(
                    "this nRF52 ZIP contains raw firmware, not a ready delta mOTA; "
                    "provide the exact running image with --base"
                )
            base = load_base_image(args.base.resolve(), target)
            base_image = work_dir / "base-firmware.bin"
            base_image.write_bytes(base.image)
            command.extend([
                "--base",
                str(base_image),
                "--patch-type",
                "in-place",
            ])
            inplace_memory = args.inplace_memory or (
                "0xC7000" if target.nrf_sd else "0x98000"
            )
            command.extend(["--inplace-memory", inplace_memory])
        if args.sign_key:
            command.extend(["--sign", str(args.sign_key.resolve())])
        result = run_checked(command, label="build mOTA", timeout=600)
        print(result.stdout.strip())
        selected = parse_mota(output.read_bytes(), output)
        good, reason = compatible_mota(selected, target)
        if not good:
            raise OtaError(f"newly built package is not installable: {reason}")

    verify_with_motatool(args.motatool, output, args.public_key)
    expected_body_hash: bytes | None = None
    if selected.is_full:
        try:
            expected_body_hash = parse_endf(selected.payload).body_hash
        except OtaError:
            pass
    elif new_identity is not None:
        expected_body_hash = new_identity.body_hash
    return output, selected, expected_body_hash


def json_objects(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    offset = 0
    while offset < len(text):
        start = text.find("{", offset)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        offset = start + end
    return objects


class Controller:
    def __init__(self, args: argparse.Namespace, password: str):
        self.meshcli = args.meshcli
        self.password = password
        self.reply_timeout = args.reply_timeout
        self.connection: list[str]
        if args.controller_serial:
            self.connection = [
                "-s", args.controller_serial,
                "-b", str(args.controller_baud),
            ]
        elif args.controller_tcp:
            host, port = split_host_port(args.controller_tcp, 5000)
            self.connection = ["-t", host, "-p", str(port)]
        else:
            self.connection = ["-a", args.controller_ble]

    def _run(self, commands: list[str], label: str) -> list[dict]:
        # Keep admin passwords out of the child process command line. meshcli's
        # script parser uses POSIX shlex on every platform, so shlex.join gives
        # us one safely quoted command line. The temporary file is removed even
        # when meshcli times out or fails.
        script_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="meshcore-ota-",
                suffix=".meshcli", delete=False,
            ) as script:
                script.write(shlex.join(commands))
                script.write("\n")
                script_path = Path(script.name)
            if os.name != "nt":
                script_path.chmod(0o600)
            command = [
                self.meshcli, "-j", "-c", "off", *self.connection,
                "script", str(script_path),
            ]
            result = run_checked(
                command,
                label=label,
                timeout=max(90, self.reply_timeout + 60),
            )
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)
        objects = json_objects(result.stdout)
        if not objects and result.stderr.strip():
            raise OtaError(f"{label} returned no JSON: {result.stderr.strip()}")
        return objects

    def get_radio(self) -> RadioSettings:
        objects = self._run(["get", "radio"], "read controller radio")
        for value in reversed(objects):
            if all(key in value for key in (
                "radio_freq", "radio_bw", "radio_sf", "radio_cr"
            )):
                return RadioSettings(
                    float(value["radio_freq"]),
                    float(value["radio_bw"]),
                    int(value["radio_sf"]),
                    int(value["radio_cr"]),
                    bool(value.get("repeat", False)),
                )
        raise OtaError("meshcli did not return the controller radio settings")

    def set_radio(self, settings: RadioSettings, label: str) -> None:
        objects = self._run(
            ["set", "radio", settings.meshcli_value()], label
        )
        for value in objects:
            if "error" in value or "error_code" in value:
                raise OtaError(f"{label} failed: {value}")

    def remote_command(
        self,
        target: str,
        command_text: str,
        *,
        password: str | None = None,
    ) -> str:
        login_password = self.password if password is None else password
        objects = self._run(
            [
                "contact_info", target,
                "login", target, login_password,
                "cmd", target, command_text,
                "trywait_msg", str(self.reply_timeout),
                "sync_msgs",
            ],
            f"remote command on {target}",
        )
        login_results = [item for item in objects if "login_success" in item]
        if not login_results or not login_results[-1].get("login_success"):
            raise OtaError(f"admin login failed for {target}")
        target_key = None
        for item in objects:
            if (
                item.get("adv_name") == target
                and isinstance(item.get("public_key"), str)
            ):
                target_key = item["public_key"].lower()
                break
        messages = [
            item for item in objects
            if item.get("txt_type") == 1
            and isinstance(item.get("text"), str)
            and (
                target_key is None
                or not isinstance(item.get("pubkey_prefix"), str)
                or target_key.startswith(item["pubkey_prefix"].lower())
            )
        ]
        if not messages:
            raise OtaError(
                f"no CLI reply from {target} for {command_text!r}; check its path and timeout"
            )
        reply = messages[-1]["text"]
        print(f"[{target}] {reply}")
        return reply


def split_host_port(value: str, default_port: int) -> tuple[str, int]:
    try:
        if value.startswith("[") and "]" in value:
            end = value.index("]")
            host = value[1:end]
            remainder = value[end + 1:]
            port = int(remainder[1:]) if remainder.startswith(":") else default_port
        elif value.count(":") == 1:
            host, port_text = value.rsplit(":", 1)
            port = int(port_text)
        else:
            host, port = value, default_port
    except ValueError as exc:
        raise OtaError(f"invalid host/port: {value!r}") from exc
    if not host or not 1 <= port <= 65535:
        raise OtaError(f"invalid host/port: {value!r}")
    return host, port


def query_target(
    controller: Controller,
    args: argparse.Namespace,
) -> TargetInfo:
    status = controller.remote_command(args.target, "ota status")
    if "not included" in status.lower() or "no endf" in status.lower():
        raise OtaError(f"{args.target} is not running a LoRa-OTA-capable image")
    match = re.search(r"target:([0-9A-Fa-f]{8})", status)
    if not match:
        raise OtaError("could not read destination target ID from `ota status`")
    target_id = int(match.group(1), 16)
    self_status = controller.remote_command(args.target, "ota self")
    hash_match = re.search(r"base_hash=([0-9A-Fa-f]{16})", self_status)
    if not hash_match:
        raise OtaError("could not read destination base hash from `ota self`")
    base_hash = bytes.fromhex(hash_match.group(1))
    combined = f"{status} {self_status}"
    platform = "nrf52" if ("bootloader:" in combined or "| bl:" in combined) else "esp32"
    nrf_sd = "SD apply OK" in combined or bool(re.search(r"\bbl:SD\b", combined))
    if platform == "nrf52" and (
        "NO mota-apply" in combined
        or "NO SD mota-apply" in combined
        or bool(re.search(r"\bbl:NONE\b", combined))
    ):
        raise OtaError(
            "destination nRF52 bootloader cannot apply this mOTA; install the exact-board OTAFIX bootloader first"
        )
    hw_match = re.search(r"\bhw=([^ |]+)", status)
    hw_id = hw_match.group(1) if hw_match and hw_match.group(1) != "?" else None
    bootloader_abi = None
    bootloader_codecs = None
    caps_match = re.search(r"\babi=(\d+)\s+codecs=0x([0-9A-Fa-f]+)", combined)
    if caps_match:
        bootloader_abi = int(caps_match.group(1))
        bootloader_codecs = int(caps_match.group(2), 16)
    elif platform == "nrf52":
        raise OtaError(
            "could not read the nRF52 bootloader ABI and codec mask from `ota self`"
        )
    current_version = None
    try:
        stats = controller.remote_command(args.target, "ota stats")
        version_match = re.search(r"\bfw (v\d+\.\d+\.\d+(?:\.\d+)?)\b", stats)
        if version_match:
            current_version = version_match.group(1)
    except OtaError as exc:
        print(f"[warn] could not query current OTA version: {exc}")
    return TargetInfo(
        args.target, target_id, base_hash, platform, nrf_sd, hw_id,
        bootloader_abi, bootloader_codecs, status, self_status, current_version,
    )


def parse_temp_radio(value: str) -> tuple[float, float, int, int, int]:
    parts = value.split(",")
    if len(parts) != 5:
        raise argparse.ArgumentTypeError("expected freq,bw,sf,cr,minutes")
    try:
        freq, bandwidth = float(parts[0]), float(parts[1])
        sf, cr, minutes = (int(part) for part in parts[2:])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("TempRadio values must be numeric") from exc
    valid_bandwidths = (
        7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125.0, 250.0, 500.0,
    )
    bandwidth_valid = any(abs(bandwidth - allowed) <= 0.001 for allowed in valid_bandwidths)
    if not 150 <= freq <= 2500 or not 5 <= sf <= 12 or not 5 <= cr <= 8 or minutes <= 0:
        raise argparse.ArgumentTypeError("invalid TempRadio range")
    if not bandwidth_valid:
        raise argparse.ArgumentTypeError(
            "bandwidth must be one of 7.8,10.4,15.6,20.8,31.25,41.7,62.5,125,250,500"
        )
    return freq, bandwidth, sf, cr, minutes


def source_cli_command(args: argparse.Namespace, command_text: str, check: bool = True) -> str:
    port = args.source_cli_serial or args.source_serial
    if not port:
        if check:
            raise OtaError(
                "a source CLI serial port is required to enable TempRadio (or use --source-already-temp)"
            )
        return ""
    command = [
        args.meshcli,
        "-r",
        "-c", "off",
        "-s", port,
        "-b", str(args.source_baud),
        command_text,
    ]
    try:
        result = run_checked(
            command,
            label=f"source command {command_text.split()[0]}",
            timeout=30,
        )
    except OtaError:
        if check:
            raise
        return ""
    output = f"{result.stdout}\n{result.stderr}".strip()
    lowered = output.lower()
    if check and ("error" in lowered or "unknown command" in lowered or "err " in lowered):
        raise OtaError(f"source rejected {command_text!r}: {output}")
    if output:
        print(f"[source] {output}")
    return output


def preflight_source_cli(args: argparse.Namespace) -> None:
    if not (args.source_serial or args.source_cli_serial):
        return
    output = source_cli_command(args, "ota status")
    if "OTA |" not in output or "target:" not in output:
        raise OtaError(
            "OTA source did not return a valid `ota status`. Use an OTA-enabled "
            "repeater/FULL raw text CLI; Companion USB is a binary API port and "
            "cannot be the serial seeder."
        )


class SeederProcess:
    def __init__(self, args: argparse.Namespace, served_dir: Path, work_dir: Path):
        self.args = args
        self.log_path = work_dir / "motatool-serve.log"
        self.log_file = None
        self.process: subprocess.Popen[str] | None = None
        command = [args.motatool, "serve", "--dir", str(served_dir), "-v"]
        if args.source_serial:
            command.extend([
                "--serial", args.source_serial,
                "--baud", str(args.source_baud),
            ])
        else:
            command.extend(["--tcp", args.source_tcp])
        self.command = command

    def start(self) -> None:
        print(f"[seeder] log: {self.log_path}")
        self.log_file = self.log_path.open("w", encoding="utf-8")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self.process = subprocess.Popen(
                self.command,
                text=True,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            self.log_file.close()
            raise OtaError(f"required command was not found: {self.args.motatool}") from exc
        time.sleep(self.args.seeder_start_wait)
        if self.process.poll() is not None:
            self.log_file.close()
            detail = self.log_path.read_text(encoding="utf-8", errors="replace")
            raise OtaError(f"motatool seeder exited during startup:\n{detail}")
        print("[seeder] running")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                if os.name == "nt":
                    self.process.terminate()
                else:
                    self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=10)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log_file is not None and not self.log_file.closed:
            self.log_file.close()
        self.process = None
        print("[seeder] stopped")


def parse_relay(value: str, default_password: str) -> tuple[str, str]:
    if "=" in value:
        name, password = value.split("=", 1)
        if not name or not password:
            raise OtaError("--relay must be NAME or NAME=PASSWORD")
        return name, password
    return value, default_password


def confirm_update(
    args: argparse.Namespace,
    target: TargetInfo,
    package: MotaInfo,
) -> None:
    print("\nValidated update plan:")
    print(f"  destination : {target.name} ({target.target_id:08X}, {target.platform})")
    print(f"  running base: {target.base_hash.hex().upper()}")
    print(f"  update      : {package.version} {package.kind} hw={package.hw_id or '?'}")
    print(f"  mOTA id     : {package.manifest_id}")
    print(f"  TempRadio   : {args.temp_radio}")
    print(f"  action      : {'stage only' if args.no_install else 'install and reboot'}")
    current_version = (
        parse_version(target.current_version) if target.current_version else None
    )
    if current_version is not None and current_version >= package.fw_version:
        print(
            f"  warning     : destination reports {target.current_version}; "
            f"package is {package.version}"
        )
        if not args.allow_non_upgrade:
            raise OtaError(
                "package is not newer than the running firmware; use "
                "--allow-non-upgrade to reinstall or downgrade deliberately"
            )
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise OtaError("non-interactive execution requires --yes")
    answer = input(f"Continue with LoRa OTA to {target.name}? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise OtaError("cancelled by operator")


def find_and_start_pull(
    controller: Controller,
    args: argparse.Namespace,
    package: MotaInfo,
) -> None:
    status = controller.remote_command(args.target, "ota status")
    active_match = re.search(r"\bid=([0-9A-Fa-f]{8})\b", status)
    if active_match:
        active_id = active_match.group(1).upper()
        if active_id == package.manifest_id:
            if "download: failed" not in status.lower():
                print(f"[download] resuming existing session {active_id}")
                return
            print(f"[download] resetting failed session {active_id}")
            controller.remote_command(args.target, "ota cancel")
        elif not args.replace_active_download:
            raise OtaError(
                f"destination already has mOTA {active_id} staged or downloading; "
                "use --replace-active-download to discard it deliberately"
            )
        else:
            cancel_reply = controller.remote_command(args.target, "ota cancel")
            if not cancel_reply.startswith("OK"):
                raise OtaError(
                    f"could not discard active destination download: {cancel_reply}"
                )

    deadline = time.monotonic() + args.discovery_timeout
    last_reply = ""
    while time.monotonic() < deadline:
        last_reply = controller.remote_command(args.target, "ota ls")
        pull_reply = controller.remote_command(
            args.target, f"ota pull {package.manifest_id} flash"
        )
        if "OK pulling" in pull_reply:
            return
        time.sleep(args.discovery_interval)
    raise OtaError(
        f"destination never catalogued mOTA {package.manifest_id}; last `ota ls`: {last_reply}"
    )


def monitor_download(controller: Controller, args: argparse.Namespace) -> str:
    deadline = time.monotonic() + args.transfer_timeout_minutes * 60
    previous = ""
    while time.monotonic() < deadline:
        status = controller.remote_command(args.target, "ota status")
        if status != previous:
            previous = status
        lowered = status.lower()
        if "ready to install" in lowered:
            return status
        if "download: failed" in lowered:
            raise OtaError(f"destination reports failed download: {status}")
        if "no download" in lowered:
            raise OtaError(f"destination lost its download session: {status}")
        time.sleep(args.poll_seconds)
    raise OtaError(
        "transfer timeout; the partial download remains staged and can resume when the same mOTA is served again"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify, transfer, and install a MeshCore LoRa OTA package.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("package", type=Path, metavar="PACKAGE", help=".mota or .zip")
    parser.add_argument("target", metavar="TARGET_NODE", help="destination contact name")
    controller = parser.add_mutually_exclusive_group()
    controller.add_argument("--controller-serial", metavar="PORT")
    controller.add_argument("--controller-tcp", metavar="HOST[:PORT]")
    controller.add_argument("--controller-ble", metavar="ADDRESS_OR_NAME")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-serial", metavar="PORT")
    source.add_argument("--source-tcp", metavar="HOST[:PORT]")
    parser.add_argument(
        "--source-cli-serial", metavar="PORT",
        help="local text-CLI port for a TCP seeder source",
    )
    parser.add_argument("--controller-baud", type=int, default=115200)
    parser.add_argument("--source-baud", type=int, default=115200)
    parser.add_argument(
        "--password",
        help="destination admin password (prefer the MESHCORE_ADMIN_PASSWORD environment variable)",
    )
    parser.add_argument(
        "--relay", action="append", default=[], metavar="NAME[=PASSWORD]",
        help="optional relay, ordered farthest-to-nearest; repeat as needed",
    )
    parser.add_argument(
        "--temp-radio", default="909.950,250,7,5,120",
        help="frequency,bw,sf,cr,minutes",
    )
    parser.add_argument(
        "--base", type=Path,
        help=(
            "exact running .bin/.hex/.zip/full.mota (required to build an "
            "internal-flash nRF52 delta; optional for SD-backed nRF52)"
        ),
    )
    parser.add_argument("--zip-member", help="select one exact path inside PACKAGE ZIP")
    parser.add_argument("--sign-key", type=Path, help="Ed25519 private key for a newly built mOTA")
    parser.add_argument("--public-key", type=Path, help="require this signer when verifying")
    parser.add_argument(
        "--inplace-memory",
        help="nRF52 OTAFIX workspace (auto: 0x98000 internal, 0xC7000 SD)",
    )
    parser.add_argument(
        "--platform", choices=("esp32", "nrf52"),
        help="destination platform for --prepare-only (detected during a live run)",
    )
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--meshcli", default="meshcli")
    parser.add_argument("--motatool", default="motatool")
    parser.add_argument("--reply-timeout", type=int, default=20)
    parser.add_argument("--discovery-timeout", type=int, default=180)
    parser.add_argument("--discovery-interval", type=int, default=8)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--transfer-timeout-minutes", type=int, default=110)
    parser.add_argument("--seeder-start-wait", type=int, default=5)
    parser.add_argument("--reboot-wait", type=int, default=90)
    parser.add_argument(
        "--source-already-temp", action="store_true",
        help="do not configure a TCP source; assert it is already on --temp-radio",
    )
    parser.add_argument(
        "--leave-controller-radio", action="store_true",
        help="leave the controller on --temp-radio instead of restoring it",
    )
    parser.add_argument("--no-install", action="store_true", help="download and verify, but do not install")
    parser.add_argument(
        "--replace-active-download", action="store_true",
        help="discard a different update already staged on the destination",
    )
    parser.add_argument("--yes", action="store_true", help="skip the destructive-action confirmation")
    parser.add_argument(
        "--prepare-only", action="store_true",
        help="only select/build/verify the mOTA; requires offline target metadata",
    )
    parser.add_argument("--target-id", help="8-hex target ID for --prepare-only")
    parser.add_argument("--target-base-hash", help="16-hex EndF body hash for --prepare-only")
    parser.add_argument("--nrf-sd", action="store_true", help="offline target uses nRF52 SD staging")
    parser.add_argument("--target-hw", help="hardware identity for --prepare-only")
    parser.add_argument(
        "--allow-non-upgrade", action="store_true",
        help="permit reinstalling the same version or installing an older one",
    )
    return parser


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    try:
        args.temp_values = parse_temp_radio(args.temp_radio)
    except argparse.ArgumentTypeError as exc:
        parser.error(f"--temp-radio: {exc}")
    if args.prepare_only:
        if not args.platform or not args.target_id:
            parser.error("--prepare-only requires --platform and --target-id")
        if args.platform == "nrf52" and not args.nrf_sd and not args.target_base_hash:
            parser.error("offline internal-flash nRF52 preparation requires --target-base-hash")
        if args.nrf_sd and args.platform != "nrf52":
            parser.error("--nrf-sd requires --platform nrf52")
    else:
        if any((args.platform, args.target_id, args.target_base_hash, args.target_hw, args.nrf_sd)):
            parser.error(
                "--platform, --target-id, --target-base-hash, --target-hw, and "
                "--nrf-sd are only valid with --prepare-only"
            )
        if not any((args.controller_serial, args.controller_tcp, args.controller_ble)):
            parser.error("a controller connection is required")
        if not any((args.source_serial, args.source_tcp)):
            parser.error("a source seeder connection is required")
        if args.source_serial and args.source_cli_serial:
            parser.error("--source-cli-serial is only used with --source-tcp")
        if args.source_tcp and not (args.source_cli_serial or args.source_already_temp):
            parser.error("--source-tcp also needs --source-cli-serial or --source-already-temp")
        if args.controller_serial and args.source_serial:
            if os.path.abspath(args.controller_serial) == os.path.abspath(args.source_serial):
                parser.error("controller and source must be separate nodes/serial ports")
        if args.controller_serial and args.source_cli_serial:
            if os.path.abspath(args.controller_serial) == os.path.abspath(args.source_cli_serial):
                parser.error("controller and source CLI must use separate serial ports")
    unsafe_text = {
        "TARGET_NODE": args.target,
        "--password": args.password,
        "--target-hw": args.target_hw,
        **{f"--relay #{index}": value for index, value in enumerate(args.relay, 1)},
    }
    for label, value in unsafe_text.items():
        if value is not None and any(char in value for char in "\r\n\0"):
            parser.error(f"{label} contains an unsupported control character")
    for name in (
        "reply_timeout", "discovery_timeout", "discovery_interval", "poll_seconds",
        "transfer_timeout_minutes", "seeder_start_wait", "reboot_wait",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if not args.prepare_only and args.transfer_timeout_minutes >= args.temp_values[4]:
        parser.error(
            "--transfer-timeout-minutes must be shorter than the TempRadio window"
        )


def require_command(command: str, label: str) -> None:
    if shutil.which(command) is None:
        raise OtaError(
            f"{label} was not found: {command!r}. Install it or pass its path explicitly."
        )


def preflight_inputs(args: argparse.Namespace) -> None:
    if not args.package.is_file():
        raise OtaError(f"package does not exist: {args.package.resolve()}")
    if args.package.suffix.lower() not in (".zip", ".mota"):
        raise OtaError("PACKAGE must be a .zip or .mota file")
    for label, path in (
        ("--base", args.base),
        ("--sign-key", args.sign_key),
        ("--public-key", args.public_key),
    ):
        if path is not None and not path.is_file():
            raise OtaError(f"{label} file does not exist: {path.resolve()}")
    require_command(args.motatool, "motatool")
    if not args.prepare_only:
        require_command(args.meshcli, "meshcli")


def make_work_dir(requested: Path | None) -> Path:
    if requested is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        requested = Path.cwd() / f"meshcore-lora-ota-{stamp}-{os.getpid()}"
    path = requested.resolve()
    path.mkdir(parents=True, exist_ok=False)
    print(f"[work] {path}")
    return path


def offline_target(args: argparse.Namespace) -> TargetInfo:
    target_id_text = args.target_id.removeprefix("0x").removeprefix("0X")
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", target_id_text):
        raise OtaError("--target-id must be exactly 8 hexadecimal characters")
    base_hash = (
        parse_hex_exact(args.target_base_hash, 8, "--target-base-hash")
        if args.target_base_hash else b"\0" * 8
    )
    return TargetInfo(
        args.target, int(target_id_text, 16), base_hash,
        args.platform, args.nrf_sd, args.target_hw, None, None,
        "offline", "offline",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)
    work_dir: Path | None = None
    controller: Controller | None = None
    original_radio: RadioSettings | None = None
    controller_changed = False
    seeder: SeederProcess | None = None
    seeder_attempted = False
    password = args.password or os.environ.get("MESHCORE_ADMIN_PASSWORD", "")
    try:
        preflight_inputs(args)
        if not args.prepare_only and not password:
            if not sys.stdin.isatty():
                raise OtaError(
                    "set MESHCORE_ADMIN_PASSWORD or pass --password for non-interactive use"
                )
            password = getpass.getpass(f"Admin password for {args.target}: ")
        if any(char in password for char in "\r\n\0"):
            raise OtaError("admin password contains an unsupported control character")
        args.relay_values = [parse_relay(value, password) for value in args.relay]
        if not args.prepare_only:
            preflight_source_cli(args)
            controller = Controller(args, password)
            target = query_target(controller, args)
            original_radio = controller.get_radio()
            print(f"[controller] saved radio {original_radio.meshcli_value()}")
        else:
            target = offline_target(args)

        work_dir = make_work_dir(args.work_dir)
        if original_radio is not None:
            recovery_path = work_dir / "controller-radio.txt"
            recovery_path.write_text(original_radio.meshcli_value() + "\n", encoding="ascii")
            print(f"[controller] recovery settings: {recovery_path}")
        package_path, package, expected_body_hash = prepare_package(
            args, target, work_dir
        )
        print(
            f"[package] {package_path.name}: {package.version} {package.kind} "
            f"target={package.target_id:08X} mid={package.manifest_id}"
        )
        if args.prepare_only:
            print(f"Prepared and verified: {package_path}")
            return 0

        assert controller is not None and original_radio is not None
        confirm_update(args, target, package)
        freq, bandwidth, sf, cr, _minutes = args.temp_values
        temp_command = f"tempradio {args.temp_radio}"

        # Move far nodes first while the controller is still on the normal
        # channel. Relays are supplied in farthest-to-nearest order.
        controller.remote_command(args.target, temp_command)
        for relay_name, relay_password in args.relay_values:
            controller.remote_command(
                relay_name, temp_command, password=relay_password
            )
        if not args.source_already_temp:
            source_cli_command(args, temp_command)

        temp_radio = RadioSettings(
            freq, bandwidth, sf, cr, original_radio.repeat
        )
        controller.set_radio(temp_radio, "switch controller to TempRadio")
        controller_changed = True
        time.sleep(3)

        seeder = SeederProcess(args, package_path.parent, work_dir)
        seeder_attempted = True
        seeder.start()
        find_and_start_pull(controller, args, package)
        monitor_download(controller, args)

        if args.no_install:
            print(f"{args.target} is ready to install; leaving the verified update staged.")
            return 0

        install_reply = controller.remote_command(args.target, "ota install")
        if not install_reply.startswith("OK"):
            raise OtaError(f"destination refused installation: {install_reply}")
        print(f"[install] {args.target} accepted the image and is rebooting")

        # Stop seeding before returning the controller to its ordinary channel.
        seeder.stop()
        seeder = None
        if not args.leave_controller_radio:
            controller.set_radio(original_radio, "restore controller radio")
            controller_changed = False
        time.sleep(args.reboot_wait)
        try:
            post = controller.remote_command(args.target, "ota self")
            match = re.search(r"base_hash=([0-9A-Fa-f]{16})", post)
            if expected_body_hash is not None and match:
                installed_hash = bytes.fromhex(match.group(1))
                if installed_hash != expected_body_hash:
                    raise OtaError(
                        "destination rebooted, but its running firmware hash is not the expected new image"
                    )
            version_reply = controller.remote_command(args.target, "ver")
            print(f"[verified] {args.target}: {version_reply}")
        except OtaError as exc:
            print(f"[warn] install was accepted, but post-reboot confirmation failed: {exc}")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted; any partial target download remains resumable.", file=sys.stderr)
        return 130
    except (OtaError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if seeder is not None:
            seeder.stop()
        if not args.prepare_only:
            # On Windows terminate() cannot let motatool send `folder off`;
            # this is harmless on TCP and explicitly cleans the serial case.
            if seeder_attempted and args.source_serial:
                source_cli_command(args, "ota folder off", check=False)
            if (
                controller is not None
                and controller_changed
                and original_radio is not None
                and not args.leave_controller_radio
            ):
                try:
                    controller.set_radio(original_radio, "restore controller radio after failure")
                    print("[controller] original radio restored")
                except (OtaError, OSError) as exc:
                    print(
                        "CRITICAL: could not restore the controller radio. "
                        f"Restore it manually to {original_radio.meshcli_value()}: {exc}",
                        file=sys.stderr,
                    )
        if work_dir is not None:
            print(f"[work] retained at {work_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
