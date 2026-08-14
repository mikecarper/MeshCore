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
import atexit
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar
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
MOTA_MAX_BLOCK_SIZE = 1024
TRANSMISSION_RETRY_LIMIT = 3
TRANSMISSION_RETRY_WINDOW_SECONDS = 90
TRANSMISSION_RETRY_DELAY_SECONDS = 2
TRANSMISSION_RETRY_MAX_DELAY_SECONDS = 8
TRANSMISSION_PROMPT_SECONDS = 10
ADAPTIVE_POLL_MAX_FACTOR = 3
TEMP_RADIO_SWITCH_DELAY_SECONDS = 3
TEMP_RADIO_RETURN_MINUTES = 1
TEMP_RADIO_RETURN_MARGIN_SECONDS = 15
INSTALL_TARGET_WINDOW_MINUTES = 3
DEFAULT_POST_INSTALL_READY_WAIT_SECONDS = 20
POST_INSTALL_READY_PROBE_INTERVAL_SECONDS = 10
COMPANION_TERMINAL_START = "+++MESHCORE-TERM-START"
COMPANION_TERMINAL_STOP = "+++MESHCORE-TERM-STOP"
# Firmware may hold the apply reboot for up to 15 seconds while its reply
# drains. Do not interpret "still ready" as a failed install before that cap.
INSTALL_RECONCILE_WAIT_SECONDS = 20

T = TypeVar("T")


class OtaError(RuntimeError):
    """Expected, actionable operator error."""


class TransmissionError(OtaError):
    """A command may not have reached its destination or returned a reply."""


class TransmissionStopped(OtaError):
    """The operator chose to stop an otherwise continuing retry loop."""


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
    current_version_source: str | None = None


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

    def matches(self, other: "RadioSettings") -> bool:
        return (
            abs(self.frequency - other.frequency) <= 0.001
            and abs(self.bandwidth - other.bandwidth) <= 0.001
            and self.spreading_factor == other.spreading_factor
            and self.coding_rate == other.coding_rate
            and self.repeat == other.repeat
        )


def prompt_after_transmission_failure(
    label: str,
    error: TransmissionError,
    timeout: int = TRANSMISSION_PROMPT_SECONDS,
) -> bool:
    """Return True to continue; timeout and blank input deliberately continue."""
    prompt = (
        f"\n[transmission] {label} is still failing after "
        f"{TRANSMISSION_RETRY_LIMIT} retries or "
        f"{TRANSMISSION_RETRY_WINDOW_SECONDS} seconds: {error}\n"
        f"Stop or continue? [s/C] (continuing in {timeout}s): "
    )
    print(prompt, end="", flush=True)

    if not sys.stdin.isatty():
        time.sleep(timeout)
        print("continue")
        return True

    answer = ""
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + timeout
        chars: list[str] = []
        while time.monotonic() < deadline:
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == "\x03":
                raise KeyboardInterrupt
            if char in ("\r", "\n"):
                print()
                answer = "".join(chars)
                break
            if char == "\b":
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            if char.isprintable():
                chars.append(char)
                print(char, end="", flush=True)
        else:
            print("continue")
    else:
        import select

        try:
            readable, _, _ = select.select([sys.stdin], [], [], timeout)
        except (OSError, ValueError):
            time.sleep(timeout)
            readable = []
        if readable:
            answer = sys.stdin.readline().strip()
        else:
            print("continue")

    return answer.strip().lower() not in ("s", "stop", "q", "quit", "n", "no")


def retry_transmission(action: Callable[[], T], label: str) -> T:
    """Retry a replay-safe transmission and let the operator stop prolonged loss."""
    cycle_started = time.monotonic()
    retries = 0
    while True:
        try:
            return action()
        except TransmissionError as exc:
            elapsed = time.monotonic() - cycle_started
            if (
                retries < TRANSMISSION_RETRY_LIMIT
                and elapsed < TRANSMISSION_RETRY_WINDOW_SECONDS
            ):
                retries += 1
                print(
                    f"[transmission] {label} failed; retry "
                    f"{retries}/{TRANSMISSION_RETRY_LIMIT}: {exc}"
                )
                time.sleep(transmission_retry_delay(retries))
                continue
            if not prompt_after_transmission_failure(label, exc):
                raise TransmissionStopped(
                    f"stopped after transmission failure during {label}"
                ) from exc
            cycle_started = time.monotonic()
            retries = 0


def transmission_retry_delay(retry_number: int) -> int:
    """Return a bounded exponential delay for consecutive RF failures."""
    exponent = max(0, retry_number - 1)
    return min(
        TRANSMISSION_RETRY_MAX_DELAY_SECONDS,
        TRANSMISSION_RETRY_DELAY_SECONDS * (2 ** exponent),
    )


def adaptive_poll_interval(
    current_seconds: float,
    baseline_seconds: float,
    query_seconds: float,
    reply_timeout: float,
) -> float:
    """Back status polling off after contention and recover after quick replies."""
    ceiling = max(baseline_seconds, baseline_seconds * ADAPTIVE_POLL_MAX_FACTOR)
    contention_threshold = max(5.0, reply_timeout * 0.5)
    if query_seconds >= contention_threshold:
        return min(ceiling, max(baseline_seconds, current_seconds * 1.5))
    return max(baseline_seconds, current_seconds * 0.75)


def adaptive_poll_ceiling(baseline_seconds: float) -> float:
    return max(baseline_seconds, baseline_seconds * ADAPTIVE_POLL_MAX_FACTOR)


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
    if block_size_log2 == 0 or block_size_log2 > 10:
        raise OtaError(
            f"invalid mOTA block size; firmware supports at most "
            f"{MOTA_MAX_BLOCK_SIZE} bytes"
        )
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


def read_bounded_file(path: Path, max_size: int, description: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise OtaError(f"cannot inspect {description} {path}: {exc}") from exc
    if size > max_size:
        raise OtaError(f"{description} is unexpectedly large: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OtaError(f"cannot read {description} {path}: {exc}") from exc
    # Check again after reading in case the file changed between stat and read.
    if len(raw) > max_size:
        raise OtaError(f"{description} is unexpectedly large: {path}")
    return raw


def read_firmware_file(path: Path) -> bytes:
    raw = read_bounded_file(path, MAX_FIRMWARE_IMAGE_SIZE, "firmware file")
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
        info = parse_mota(
            read_bounded_file(path, MAX_ARCHIVE_MEMBER_SIZE, "mOTA file"), path
        )
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
        selected_blob = read_bounded_file(
            source, MAX_ARCHIVE_MEMBER_SIZE, "mOTA file"
        )
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
        selected = parse_mota(
            read_bounded_file(output, MAX_ARCHIVE_MEMBER_SIZE, "mOTA file"), output
        )
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
        selected = parse_mota(
            read_bounded_file(output, MAX_ARCHIVE_MEMBER_SIZE, "mOTA file"), output
        )
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


def reply_matches_command(command_text: str, reply: str) -> bool:
    """Reject unrelated queued CLI messages before treating one as a reply."""
    command = command_text.strip().lower()
    text = reply.strip()
    lowered = text.lower()
    if not text:
        return False
    is_unknown = lowered.startswith(("unknown command", "command not found"))
    is_error = bool(re.match(r"^(?:err(?:or)?\b|\(err)", lowered))
    needs_temp = lowered.startswith("lora ota needs temp radio")
    if command == "ota status":
        return text.startswith("OTA |") or "not included" in lowered or is_unknown
    if command == "ota self":
        return (
            lowered.startswith("self ")
            or is_unknown
            or (is_error and ("endf" in lowered or "ota" in lowered))
        )
    if command == "ota stats":
        return text.startswith("OTA | fw ") or is_unknown or needs_temp
    if command == "ota ls":
        return (
            text.startswith("Updates ")
            or text.startswith("No updates ")
            or is_unknown
            or needs_temp
            or (is_error and ("update" in lowered or "page" in lowered))
        )
    if command.startswith("ota pull "):
        return (
            lowered.startswith(("ok pulling ", "usage: ota pull", "choose a destination"))
            or is_unknown
            or needs_temp
            or (
                is_error
                and any(
                    word in lowered
                    for word in ("update", "destination", "pull", "fetch", "busy", "folder")
                )
            )
        )
    if command == "ota cancel":
        return lowered.startswith("ok dropped ") or is_unknown or needs_temp
    if command == "ota install":
        return (
            lowered.startswith(("ok |", "err |"))
            or is_unknown
            or needs_temp
            or (
                is_error
                and any(
                    word in lowered
                    for word in ("update", "fetch", "apply", "archive", "signature")
                )
            )
        )
    if command.startswith("tempradio "):
        return (
            lowered.startswith("ok - temp params ")
            or is_unknown
            or (is_error and ("param" in lowered or "radio" in lowered))
        )
    if command == "ver":
        return extract_reply_version(text) is not None or is_unknown
    return True


def extract_reply_version(reply: str) -> int | None:
    match = re.search(r"\b[vV]?(\d+\.\d+\.\d+(?:\.\d+)?)\b", reply)
    return parse_version(match.group(1)) if match else None


class PersistentMeshcliSession:
    """Run meshcli scripts over one long-lived Companion connection."""

    def __init__(self, command: list[str]):
        self.command = command
        self.process: subprocess.Popen[bytes] | None = None
        self.output_queue: queue.Queue[bytes | None] = queue.Queue()
        self.pending = bytearray()
        self.reader_thread: threading.Thread | None = None
        self.announced = False

    @staticmethod
    def _read_output(
        stream,
        output_queue: queue.Queue[bytes | None],
    ) -> None:
        try:
            while True:
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    break
                output_queue.put(chunk)
        except OSError:
            pass
        finally:
            output_queue.put(None)

    def _start(self) -> None:
        self.close()
        self.output_queue = queue.Queue()
        self.pending = bytearray()
        try:
            self.process = subprocess.Popen(
                [*self.command, "-C", "-i"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except OSError as exc:
            raise OtaError(
                f"could not start persistent meshcli session: {exc}"
            ) from exc
        assert self.process.stdout is not None
        self.reader_thread = threading.Thread(
            target=self._read_output,
            args=(self.process.stdout, self.output_queue),
            name="meshcli-output",
            daemon=True,
        )
        self.reader_thread.start()

    @staticmethod
    def _line_pattern(marker: str) -> re.Pattern[bytes]:
        return re.compile(
            rb"(?:^|\r?\n)" + re.escape(marker.encode("ascii")) + rb"\r?\n"
        )

    def _read_frame(self, start: str, end: str, timeout: float) -> str:
        start_pattern = self._line_pattern(start)
        end_pattern = self._line_pattern(end)
        deadline = time.monotonic() + timeout
        while True:
            start_match = start_pattern.search(self.pending)
            if start_match is not None:
                end_match = end_pattern.search(self.pending, start_match.end())
                if end_match is not None:
                    frame = bytes(self.pending[start_match.end():end_match.start()])
                    del self.pending[:end_match.end()]
                    return frame.decode("utf-8", "replace")
            elif len(self.pending) > 256 * 1024:
                del self.pending[:-64 * 1024]

            if len(self.pending) > 8 * 1024 * 1024:
                raise OtaError("persistent meshcli output exceeded 8 MiB")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise OtaError("persistent meshcli command timed out")
            try:
                chunk = self.output_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self.process is not None and self.process.poll() is not None:
                    detail = bytes(self.pending[-4096:]).decode("utf-8", "replace")
                    self.close()
                    raise OtaError(
                        "persistent meshcli session exited"
                        + (f": {detail.strip()}" if detail.strip() else "")
                    )
                continue
            if chunk is None:
                detail = bytes(self.pending[-4096:]).decode("utf-8", "replace")
                self.close()
                raise OtaError(
                    "persistent meshcli session closed"
                    + (f": {detail.strip()}" if detail.strip() else "")
                )
            self.pending.extend(chunk)

    def run_script(
        self,
        script_path: Path,
        start_marker: str,
        end_marker: str,
        timeout: float,
    ) -> str:
        if self.process is None or self.process.poll() is not None:
            self._start()
        assert self.process is not None and self.process.stdin is not None
        command = shlex.join(["script", str(script_path)]) + "\n"
        try:
            self.process.stdin.write(command.encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise OtaError(f"persistent meshcli input failed: {exc}") from exc
        output = self._read_frame(start_marker, end_marker, timeout)
        if not self.announced:
            print("[controller] persistent meshcli connection established")
            self.announced = True
        return output

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write(b"quit\n")
                process.stdin.flush()
                process.wait(timeout=2)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
        if process.stdin is not None:
            process.stdin.close()


class Controller:
    def __init__(
        self,
        args: argparse.Namespace,
        password: str,
        *,
        persistent: bool = True,
    ):
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
        # Remote-admin authentication belongs to the radio node, not to each
        # host command. Reuse it across application reboots. A silent command
        # is packet loss or an airtime-starved reply, not evidence that the
        # authenticated session was lost.
        self._authenticated_targets: set[str] = set()
        self._meshcli_session = (
            PersistentMeshcliSession([
                self.meshcli, "-j", "-c", "off", *self.connection,
            ])
            if persistent else None
        )
        if self._meshcli_session is not None:
            atexit.register(self.close)

    def close(self) -> None:
        session = getattr(self, "_meshcli_session", None)
        if session is not None:
            session.close()

    def forget_remote_auth(self, target: str) -> None:
        authenticated = getattr(self, "_authenticated_targets", None)
        if authenticated is not None:
            authenticated.discard(target)

    def _remote_auth_is_cached(self, target: str) -> bool:
        return target in getattr(self, "_authenticated_targets", set())

    def _cache_remote_auth(self, target: str) -> None:
        if not hasattr(self, "_authenticated_targets"):
            self._authenticated_targets = set()
        self._authenticated_targets.add(target)

    def _execute(
        self, commands: list[str], label: str
    ) -> subprocess.CompletedProcess[str]:
        # Keep admin passwords out of the child process command line. meshcli's
        # script parser uses POSIX shlex on every platform, so shlex.join gives
        # us one safely quoted command line. The temporary file is removed even
        # when meshcli times out or fails.
        script_path: Path | None = None
        frame_start = f"MESHCORE_HOST_START_{secrets.token_hex(16)}"
        frame_end = f"MESHCORE_HOST_END_{secrets.token_hex(16)}"
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="meshcore-ota-",
                suffix=".meshcli", delete=False,
            ) as script:
                if self._meshcli_session is not None:
                    script.write(shlex.join(["echo", frame_start]))
                    script.write("\n")
                script.write(shlex.join(commands))
                script.write("\n")
                if self._meshcli_session is not None:
                    script.write(shlex.join(["echo", frame_end]))
                    script.write("\n")
                script_path = Path(script.name)
            if os.name != "nt":
                script_path.chmod(0o600)
            timeout = max(90, self.reply_timeout + 60)
            if self._meshcli_session is not None:
                stdout = self._meshcli_session.run_script(
                    script_path, frame_start, frame_end, timeout
                )
                result = subprocess.CompletedProcess(
                    [], 0, stdout=stdout, stderr=""
                )
            else:
                command = [
                    self.meshcli, "-j", "-c", "off", *self.connection,
                    "script", str(script_path),
                ]
                result = run_checked(command, label=label, timeout=timeout)
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)
        return result

    @staticmethod
    def _no_json_error(result: subprocess.CompletedProcess[str], label: str) -> OtaError:
        detail = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        return OtaError(f"{label} returned no JSON: {detail or 'no output'}")

    def _run(self, commands: list[str], label: str) -> list[dict]:
        result = self._execute(commands, label)
        objects = json_objects(result.stdout)
        if not objects:
            raise self._no_json_error(result, label)
        return objects

    def _run_marked(
        self, commands: list[str], label: str, marker: str
    ) -> tuple[list[dict], list[dict]]:
        result = self._execute(commands, label)
        before, found, after = result.stdout.partition(marker)
        if not found:
            detail = "\n".join(
                part.strip() for part in (result.stdout, result.stderr) if part.strip()
            )
            raise TransmissionError(
                f"meshcli did not reach the command marker ({detail or 'no output'})"
            )
        return json_objects(before + after), json_objects(after)

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

    def get_public_key(self) -> str:
        objects = self._run(["infos"], "read controller identity")
        for value in reversed(objects):
            public_key = value.get("public_key")
            if (
                isinstance(public_key, str)
                and re.fullmatch(r"[0-9A-Fa-f]{64}", public_key)
            ):
                return public_key.lower()
        raise OtaError("meshcli did not return the controller public key")

    def set_radio(self, settings: RadioSettings, label: str) -> None:
        command_error: OtaError | None = None
        try:
            objects = self._run(
                ["set", "radio", settings.meshcli_value()], label
            )
            for value in objects:
                if "error" in value or "error_code" in value:
                    command_error = OtaError(f"{label} failed: {value}")
                    break
        except OtaError as exc:
            command_error = exc

        try:
            actual = self.get_radio()
        except OtaError as verify_error:
            if command_error is not None:
                raise OtaError(
                    f"{command_error}; controller radio could not be verified: {verify_error}"
                ) from command_error
            raise
        if not settings.matches(actual):
            detail = f"requested {settings.meshcli_value()}, read back {actual.meshcli_value()}"
            if command_error is not None:
                detail = f"{command_error}; {detail}"
            raise OtaError(f"{label} failed: {detail}")
        print(f"[controller] verified radio {actual.meshcli_value()}")

    def _remote_command_once(
        self,
        target: str,
        command_text: str,
        password: str,
        reply_timeout: int | None = None,
    ) -> str:
        command_reply_timeout = (
            self.reply_timeout if reply_timeout is None else reply_timeout
        )
        if command_reply_timeout <= 0:
            raise OtaError("remote-command reply timeout must be positive")
        marker = f"MESHCORE_OTA_{secrets.token_hex(16)}"
        login_required = not self._remote_auth_is_cached(target)
        commands = ["contact_info", target]
        if login_required:
            commands.extend(["login", target, password])
        commands.extend([
            "sync_msgs",
            "echo", marker,
            "cmd", target, command_text,
            "trywait_msg", str(command_reply_timeout),
            "sync_msgs",
        ])
        try:
            objects, post_objects = self._run_marked(
                commands,
                f"remote command on {target}",
                marker,
            )
        except TransmissionError:
            raise
        except OtaError as exc:
            raise TransmissionError(f"meshcli could not contact {target}: {exc}") from exc

        if any(
            item.get("error") == "contact unknown"
            and item.get("name") == target
            for item in objects
        ):
            raise OtaError(f"controller has no contact named {target!r}")
        login_results = [item for item in objects if "login_success" in item]
        if login_required:
            if login_results and not login_results[-1].get("login_success"):
                raise OtaError(f"admin login failed for {target}")
            if not login_results:
                raise TransmissionError(f"no admin-login result from {target}")
            self._cache_remote_auth(target)
            print(f"[auth] remote admin session established for {target}")

        target_key = None
        for item in objects:
            if (
                item.get("adv_name") == target
                and isinstance(item.get("public_key"), str)
            ):
                target_key = item["public_key"].lower()
                break
        if target_key is None:
            raise TransmissionError(f"meshcli did not return contact identity for {target}")

        messages = [
            item for item in post_objects
            if item.get("txt_type") == 1
            and isinstance(item.get("text"), str)
            and isinstance(item.get("pubkey_prefix"), str)
            and target_key.startswith(item["pubkey_prefix"].lower())
            and reply_matches_command(command_text, item["text"])
        ]
        if not messages:
            raise TransmissionError(
                f"no matching CLI reply from {target} for {command_text!r}; "
                "check its path and reply timeout; the cached admin session "
                "was retained"
            )
        reply = messages[-1]["text"]
        print(f"[{target}] {reply}")
        return reply

    def remote_command(
        self,
        target: str,
        command_text: str,
        *,
        password: str | None = None,
        retry: bool = True,
        reply_timeout: int | None = None,
    ) -> str:
        login_password = self.password if password is None else password
        normalized = command_text.strip().lower()
        if retry and (
            normalized == "ota install" or normalized.startswith("ota pull ")
        ):
            raise OtaError(
                f"{command_text!r} requires state-aware retry handling"
            )
        action = lambda: self._remote_command_once(
            target, command_text, login_password, reply_timeout
        )
        if retry:
            return retry_transmission(
                action, f"{command_text!r} on {target}"
            )
        return action()


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
    current_version_source = None
    try:
        stats = controller.remote_command(args.target, "ota stats")
        version_match = re.search(r"\bfw (v\d+\.\d+\.\d+(?:\.\d+)?)\b", stats)
        if version_match:
            current_version = version_match.group(1)
            current_version_source = "ota stats"
    except TransmissionStopped:
        raise
    except OtaError as exc:
        print(f"[warn] could not query current OTA version: {exc}")
    if current_version is None:
        version_reply = controller.remote_command(args.target, "ver")
        version_value = extract_reply_version(version_reply)
        if version_value is None:
            raise OtaError(
                f"could not read destination firmware version from `ver`: {version_reply}"
            )
        current_version = format_version(version_value)
        current_version_source = "ver"
    return TargetInfo(
        args.target, target_id, base_hash, platform, nrf_sd, hw_id,
        bootloader_abi, bootloader_codecs, status, self_status, current_version,
        current_version_source,
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
    serial_port = args.source_cli_serial or args.source_serial
    tcp_console = args.source_cli_tcp
    if not serial_port and not tcp_console:
        if check:
            raise OtaError(
                "a source CLI connection is required to enable TempRadio (or use --source-already-temp)"
            )
        return ""

    def run_once() -> str:
        if tcp_console:
            host, port = split_host_port(tcp_console, 5002)
            try:
                with socket.create_connection((host, port), timeout=10) as connection:
                    connection.settimeout(10)
                    greeting = bytearray()
                    while b"\r\n> " not in greeting and len(greeting) < 4096:
                        chunk = connection.recv(512)
                        if not chunk:
                            break
                        greeting.extend(chunk)
                    connection.sendall(command_text.encode("utf-8") + b"\r\n")
                    response = bytearray()
                    while b"\r\n> " not in response and len(response) < 4096:
                        chunk = connection.recv(512)
                        if not chunk:
                            break
                        response.extend(chunk)
            except (OSError, UnicodeError) as exc:
                raise TransmissionError(f"source TCP console failed: {exc}") from exc
            text = response.decode("utf-8", "replace")
            match = re.search(r"(?:^|\r?\n)\s*->\s*(.*?)\r?\n>\s*$", text, re.DOTALL)
            if not match:
                raise TransmissionError(
                    f"source TCP console returned no command reply: {text.strip() or 'no output'}"
                )
            output = match.group(1).strip()
        else:
            wire_command = command_text
            if getattr(args, "source_companion_terminal", False):
                # meshcli raw mode keeps one serial open while writing this
                # compound command. The full Companion consumes the start/stop
                # tokens locally and runs the middle command in ASCII mode.
                wire_command = (
                    f"{COMPANION_TERMINAL_START}\r"
                    f"{command_text}\r"
                    f"{COMPANION_TERMINAL_STOP}"
                )
            command = [
                args.meshcli,
                "-r",
                "-c", "off",
                "-s", serial_port,
                "-b", str(args.source_baud),
                wire_command,
            ]
            try:
                result = run_checked(
                    command,
                    label=f"source command {command_text.split()[0]}",
                    timeout=30,
                )
            except OtaError as exc:
                raise TransmissionError(f"source CLI link failed: {exc}") from exc
            output = f"{result.stdout}\n{result.stderr}".strip()
        lowered = output.lower()
        if "error" in lowered or "unknown command" in lowered or "err " in lowered:
            raise OtaError(f"source rejected {command_text!r}: {output}")
        if (
            command_text.startswith("tempradio ")
            and "ok - temp params " not in lowered
        ):
            raise OtaError(
                f"source did not confirm {command_text!r}: {output or 'no output'}"
            )
        if output:
            print(f"[source] {output}")
        return output

    if check:
        return retry_transmission(run_once, f"{command_text!r} on OTA source")
    try:
        return run_once()
    except OtaError:
        return ""


def preflight_source_cli(args: argparse.Namespace) -> None:
    if not (args.source_serial or args.source_cli_serial or args.source_cli_tcp):
        return

    def valid_status(value: str) -> bool:
        full_seeder = "OTA seeder" in value and "install:disabled" in value
        return full_seeder or ("OTA |" in value and "target:" in value)

    serial_port = args.source_cli_serial or args.source_serial
    if serial_port:
        # Ordinary repeaters start in raw ASCII. A full Companion starts in
        # Binary mode, so retry an invalid raw probe through its terminal
        # control tokens and remember that transport for later TempRadio calls.
        args.source_companion_terminal = False
        output = source_cli_command(args, "ota status", check=False)
        if not valid_status(output):
            args.source_companion_terminal = True
            output = source_cli_command(args, "ota status")
    else:
        output = source_cli_command(args, "ota status")

    if not valid_status(output):
        raise OtaError(
            "OTA source did not return a valid `ota status`. Use an OTA-enabled "
            "repeater raw text CLI, nRF52 full Companion USB port, or "
            "companion_radio_full TCP console."
        )


def verify_shared_source_identity(
    controller: Controller,
    args: argparse.Namespace,
) -> None:
    """Prove that a TCP source is the controller's own Full Companion."""
    if not getattr(args, "source_shares_controller", False):
        return
    source_host, _source_port = split_host_port(args.source_tcp, 5001)
    cli_host, _cli_port = split_host_port(args.source_cli_tcp, 5002)
    if source_host.lower() != cli_host.lower():
        raise OtaError(
            "shared source data and CLI endpoints must use the same host"
        )
    api_host = f"[{source_host}]:5000" if ":" in source_host else f"{source_host}:5000"
    api_args = argparse.Namespace(
        meshcli=args.meshcli,
        controller_serial=None,
        controller_tcp=api_host,
        controller_ble=None,
        controller_baud=args.controller_baud,
        reply_timeout=args.reply_timeout,
    )
    controller_key = controller.get_public_key()
    if controller.connection == ["-t", source_host, "-p", "5000"]:
        source_key = controller_key
    else:
        source_api = Controller(api_args, "", persistent=False)
        source_key = source_api.get_public_key()
    if source_key != controller_key:
        raise OtaError(
            "--source-shares-controller identity mismatch: controller is "
            f"{controller_key}, source host is {source_key}"
        )
    print(f"[source] verified shared Full Companion {source_key}")


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
        self.ensure_running("during startup")
        print("[seeder] running")

    def _log_tail(self) -> str:
        if self.log_file is not None and not self.log_file.closed:
            self.log_file.flush()
        try:
            detail = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"could not read seeder log: {exc}"
        return detail[-4096:].strip() or "no seeder log output"

    def ensure_running(self, context: str = "") -> None:
        if self.process is None:
            raise OtaError("motatool seeder is not running")
        return_code = self.process.poll()
        if return_code is not None:
            suffix = f" {context}" if context else ""
            raise OtaError(
                f"motatool seeder exited with status {return_code}{suffix}:\n"
                f"{self._log_tail()}"
            )

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


def download_manifest_id(status: str) -> str | None:
    match = re.search(r"\bid=([0-9A-Fa-f]{8})\b", status)
    return match.group(1).upper() if match else None


def require_package_session(
    status: str,
    package: MotaInfo,
    *,
    ready: bool = False,
) -> None:
    lowered = status.lower()
    if "no download" in lowered:
        raise OtaError(f"destination lost its download session: {status}")
    active_id = download_manifest_id(status)
    if active_id is None:
        raise OtaError(f"destination download status has no manifest ID: {status}")
    if active_id != package.manifest_id:
        raise OtaError(
            f"destination switched to mOTA {active_id}; expected {package.manifest_id}"
        )
    if "download: failed" in lowered:
        raise OtaError(f"destination reports failed download: {status}")
    if ready and "ready to install" not in lowered:
        raise OtaError(f"destination is not ready to install {package.manifest_id}: {status}")


def ensure_seeder_running(
    seeder: SeederProcess | None,
    context: str,
) -> None:
    if seeder is not None:
        seeder.ensure_running(context)


def remote_command_with_seeder(
    controller: Controller,
    target: str,
    command: str,
    seeder: SeederProcess | None,
    context: str,
) -> str:
    if seeder is None:
        return controller.remote_command(target, command)

    def run_once() -> str:
        ensure_seeder_running(seeder, context)
        return controller.remote_command(target, command, retry=False)

    return retry_transmission(run_once, f"{command!r} on {target}")


def find_and_start_pull(
    controller: Controller,
    args: argparse.Namespace,
    package: MotaInfo,
    seeder: SeederProcess | None = None,
) -> None:
    status = remote_command_with_seeder(
        controller, args.target, "ota status", seeder, "before discovery"
    )
    active_id = download_manifest_id(status)
    if active_id:
        if active_id == package.manifest_id:
            if "download: failed" not in status.lower():
                print(f"[download] resuming existing session {active_id}")
                return
            print(f"[download] resetting failed session {active_id}")
            cancel_reply = controller.remote_command(args.target, "ota cancel")
            if not cancel_reply.startswith("OK"):
                raise OtaError(
                    f"could not reset failed destination download: {cancel_reply}"
                )
        elif active_id == getattr(args, "clear_completed_manifest", None):
            expected_hash = args.clear_completed_on_body_hash
            if "ready to install" not in status.lower():
                raise OtaError(
                    f"refusing to clear previous mOTA {active_id} because it is "
                    f"not complete: {status}"
                )
            identity = remote_command_with_seeder(
                controller, args.target, "ota self", seeder,
                "while proving a completed previous manifest",
            )
            hash_match = re.search(
                r"\bbase_hash=([0-9A-Fa-f]{16})\b", identity
            )
            running_hash = hash_match.group(1).upper() if hash_match else None
            if running_hash != expected_hash:
                raise OtaError(
                    f"refusing to clear previous mOTA {active_id}: running body "
                    f"hash is {running_hash or 'unknown'}, expected {expected_hash}"
                )
            try:
                cancel_reply = controller.remote_command(
                    args.target, "ota cancel", retry=False
                )
            except TransmissionError as cancel_error:
                resolved = remote_command_with_seeder(
                    controller, args.target, "ota status", seeder,
                    "while resolving a lost completed-manifest cancel reply",
                )
                if "no download" not in resolved.lower():
                    raise OtaError(
                        f"completed-manifest cancel outcome is ambiguous: {resolved}"
                    ) from cancel_error
            else:
                if not cancel_reply.startswith("OK"):
                    raise OtaError(
                        f"could not clear completed previous mOTA: {cancel_reply}"
                    )
            status = remote_command_with_seeder(
                controller, args.target, "ota status", seeder,
                "after clearing a completed previous manifest",
            )
            if "no download" not in status.lower():
                raise OtaError(
                    f"completed previous mOTA {active_id} remains: {status}"
                )
            print(
                f"[download] cleared completed previous session {active_id} "
                f"after proving running body {expected_hash}"
            )
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
    elif "download:" in status.lower():
        raise OtaError(f"destination download status has no manifest ID: {status}")

    deadline = time.monotonic() + args.discovery_timeout
    last_reply = ""
    while time.monotonic() < deadline:
        last_reply = remote_command_with_seeder(
            controller, args.target, "ota ls", seeder, "during discovery"
        )

        def attempt_pull() -> bool:
            try:
                pull_reply = controller.remote_command(
                    args.target,
                    f"ota pull {package.manifest_id} flash",
                    retry=False,
                )
            except TransmissionError as exc:
                resolved = remote_command_with_seeder(
                    controller, args.target, "ota status", seeder,
                    "while resolving a lost pull reply",
                )
                resolved_id = download_manifest_id(resolved)
                if resolved_id == package.manifest_id:
                    require_package_session(resolved, package)
                    print(
                        f"[download] pull reply was lost, but session "
                        f"{package.manifest_id} is active"
                    )
                    return True
                if resolved_id is not None:
                    raise OtaError(
                        f"destination has mOTA {resolved_id} after the pull attempt; "
                        f"expected {package.manifest_id}"
                    )
                if "download:" in resolved.lower():
                    raise OtaError(
                        f"destination download status has no manifest ID: {resolved}"
                    )
                raise exc

            if pull_reply.startswith("OK pulling"):
                reply_id = download_manifest_id(pull_reply.replace("mid=", "id="))
                if reply_id != package.manifest_id:
                    raise OtaError(
                        f"destination confirmed pull {reply_id or '?'}; "
                        f"expected {package.manifest_id}"
                    )
                return True
            if "no such update" in pull_reply.lower():
                return False
            raise OtaError(f"destination refused the pull: {pull_reply}")

        if retry_transmission(
            attempt_pull, f"start mOTA {package.manifest_id} on {args.target}"
        ):
            return
        time.sleep(args.discovery_interval)
    raise OtaError(
        f"destination never catalogued mOTA {package.manifest_id}; last `ota ls`: {last_reply}"
    )


def monitor_download(
    controller: Controller,
    args: argparse.Namespace,
    package: MotaInfo,
    seeder: SeederProcess | None = None,
) -> str:
    deadline = time.monotonic() + args.transfer_timeout_minutes * 60
    poll_seconds = float(args.poll_seconds)
    while time.monotonic() < deadline:
        query_started = time.monotonic()
        status = remote_command_with_seeder(
            controller, args.target, "ota status", seeder, "during transfer"
        )
        query_seconds = time.monotonic() - query_started
        require_package_session(status, package)
        lowered = status.lower()
        if "ready to install" in lowered:
            return status
        next_poll = adaptive_poll_interval(
            poll_seconds, float(args.poll_seconds), query_seconds,
            float(args.reply_timeout),
        )
        if round(next_poll) != round(poll_seconds):
            print(
                f"[download] adaptive status interval "
                f"{round(poll_seconds)}s -> {round(next_poll)}s "
                f"(query {query_seconds:.1f}s)"
            )
        poll_seconds = next_poll
        time.sleep(poll_seconds)
    raise OtaError(
        "transfer timeout; the partial download remains staged and can resume when the same mOTA is served again"
    )


def require_temp_radio_reply(node: str, reply: str) -> None:
    if not reply.lower().startswith("ok - temp params for "):
        raise OtaError(f"{node} did not accept TempRadio: {reply}")


def arm_target_temp_radio(
    controller: Controller,
    args: argparse.Namespace,
    command: str,
    temp_radio: RadioSettings,
    normal_radio: RadioSettings,
) -> None:
    """Resolve a lost TempRadio reply without blindly replaying the command."""
    retries = 0
    while True:
        try:
            reply = controller.remote_command(args.target, command, retry=False)
        except TransmissionError as command_error:
            print(
                "[destination] TempRadio reply was lost; probing the declared "
                "temporary channel"
            )
            controller.set_radio(temp_radio, "probe destination TempRadio state")
            found_on_temp = False
            try:
                identity = controller.remote_command(
                    args.target, "ota self", retry=False
                )
                if re.search(r"\bbase_hash=[0-9A-Fa-f]{16}\b", identity) is None:
                    raise OtaError(
                        "destination replied on TempRadio but did not return a valid "
                        "running EndF identity"
                    )
                found_on_temp = True
            except (OtaError, TransmissionError):
                pass
            finally:
                controller.set_radio(
                    normal_radio, "restore controller after TempRadio probe"
                )
            if found_on_temp:
                print(
                    "[destination] resolved lost TempRadio reply from the exact "
                    "target identity; continuing"
                )
                return

            # The 2-second scheduled handoff is long past by the time the
            # temporary-channel probe times out. An exact identity reply back
            # on the normal channel therefore proves that the target did not
            # remain on TempRadio, making a bounded replay safe.
            try:
                identity = controller.remote_command(
                    args.target, "ota self", retry=False
                )
                if re.search(r"\bbase_hash=[0-9A-Fa-f]{16}\b", identity) is None:
                    raise OtaError(
                        "destination replied on the normal channel but did not "
                        "return a valid running EndF identity"
                    )
            except (OtaError, TransmissionError) as normal_probe_error:
                raise OtaError(
                    "destination TempRadio outcome is ambiguous; the controller "
                    "was restored to its normal channel and the command was not "
                    "replayed"
                ) from normal_probe_error

            if retries >= TRANSMISSION_RETRY_LIMIT:
                raise OtaError(
                    "destination remained on the normal channel after "
                    f"{retries + 1} verified TempRadio delivery attempts"
                ) from command_error
            retries += 1
            delay = transmission_retry_delay(retries)
            print(
                "[destination] exact normal-channel identity proves TempRadio is "
                f"inactive; safe retry {retries}/{TRANSMISSION_RETRY_LIMIT} "
                f"in {format_decimal(delay)}s"
            )
            time.sleep(delay)
            continue
        require_temp_radio_reply(args.target, reply)
        return


def temp_radio_command_for_minutes(
    args: argparse.Namespace,
    minutes: int,
) -> str:
    freq, bandwidth, sf, cr, _configured_minutes = args.temp_values
    return (
        f"tempradio {format_decimal(freq)},{format_decimal(bandwidth)},"
        f"{sf},{cr},{minutes}"
    )


def arm_target_install_window(
    controller: Controller,
    args: argparse.Namespace,
) -> None:
    reply = controller.remote_command(
        args.target,
        temp_radio_command_for_minutes(args, INSTALL_TARGET_WINDOW_MINUTES),
    )
    require_temp_radio_reply(args.target, reply)


def confirm_ready_to_install(
    controller: Controller,
    args: argparse.Namespace,
    package: MotaInfo,
) -> str:
    status = controller.remote_command(args.target, "ota status")
    require_package_session(status, package, ready=True)
    return status


def require_system_watchdog_off(
    controller: Controller,
    args: argparse.Namespace,
) -> None:
    reply = controller.remote_command(args.target, "get system.watchdog")
    if re.fullmatch(r"\s*>\s*off\s*", reply, re.IGNORECASE) is None:
        raise OtaError(
            "destination system watchdog must report `> off` immediately "
            f"before installation; got: {reply}"
        )
    print(f"[install] {args.target} system watchdog is off")


def request_install(
    controller: Controller,
    args: argparse.Namespace,
    package: MotaInfo,
) -> bool:
    """Request install without ever blindly replaying an uncertain command."""
    cycle_started = time.monotonic()
    retries = 0
    confirm_ready_to_install(controller, args, package)
    while True:
        # If an install reply is lost and the target also stops replying, this
        # short window gets it back onto the normal channel for verification.
        arm_target_install_window(controller, args)
        if getattr(args, "require_system_watchdog_off", False):
            require_system_watchdog_off(controller, args)
        try:
            reply = controller.remote_command(args.target, "ota install", retry=False)
        except TransmissionError as exc:
            print(
                "[install] reply was lost; waiting before checking whether the "
                "destination is still staged"
            )
            time.sleep(INSTALL_RECONCILE_WAIT_SECONDS)
            try:
                status = controller.remote_command(
                    args.target, "ota status", retry=False
                )
            except TransmissionError:
                print(
                    "[install] destination stopped replying. The install command "
                    "will not be replayed; post-reboot identity will resolve it."
                )
                return False
            require_package_session(status, package, ready=True)

            elapsed = time.monotonic() - cycle_started
            if (
                retries < TRANSMISSION_RETRY_LIMIT
                and elapsed < TRANSMISSION_RETRY_WINDOW_SECONDS
            ):
                retries += 1
                print(
                    f"[install] destination is still staged; safe retry "
                    f"{retries}/{TRANSMISSION_RETRY_LIMIT}"
                )
                time.sleep(transmission_retry_delay(retries))
                continue
            if not prompt_after_transmission_failure(
                f"'ota install' on {args.target}", exc
            ):
                raise TransmissionStopped(
                    "stopped after an unacknowledged install request"
                ) from exc
            cycle_started = time.monotonic()
            retries = 0
            continue

        if not reply.startswith("OK |"):
            raise OtaError(f"destination refused installation: {reply}")
        return True


def shorten_relay_temp_windows(
    controller: Controller,
    args: argparse.Namespace,
) -> None:
    if not args.relay_values:
        return
    command = temp_radio_command_for_minutes(args, TEMP_RADIO_RETURN_MINUTES)
    print(
        f"[relays] scheduling return to the normal channel in "
        f"{TEMP_RADIO_RETURN_MINUTES} minute"
    )
    for relay_name, relay_password in args.relay_values:
        reply = controller.remote_command(
            relay_name, command, password=relay_password
        )
        require_temp_radio_reply(relay_name, reply)


def shorten_target_temp_window(
    controller: Controller,
    args: argparse.Namespace,
) -> None:
    command = temp_radio_command_for_minutes(args, TEMP_RADIO_RETURN_MINUTES)
    print(
        f"[destination] scheduling return to the normal channel in "
        f"{TEMP_RADIO_RETURN_MINUTES} minute"
    )
    reply = controller.remote_command(args.target, command)
    require_temp_radio_reply(args.target, reply)


def shorten_source_temp_window(
    args: argparse.Namespace,
    *,
    check: bool = True,
) -> bool:
    if args.source_already_temp:
        return True
    if getattr(args, "source_shares_controller", False):
        print("[source] ending shared Full Companion TempRadio before restore")
        output = source_cli_command(args, "normalradio", check=check)
        if not output and not check:
            print(
                "[warn] could not end the shared source TempRadio window; "
                "end it with `normalradio` before restoring the controller",
                file=sys.stderr,
            )
            return False
        time.sleep(TEMP_RADIO_SWITCH_DELAY_SECONDS)
        return True
    command = temp_radio_command_for_minutes(args, TEMP_RADIO_RETURN_MINUTES)
    print(
        f"[source] scheduling return to the normal channel in "
        f"{TEMP_RADIO_RETURN_MINUTES} minute"
    )
    output = source_cli_command(args, command, check=check)
    if not output and not check:
        print(
            "[warn] could not shorten the OTA source TempRadio window; "
            "it will return when its original bounded window ends",
            file=sys.stderr,
        )
        return False
    return True


def verify_installed(
    controller: Controller,
    args: argparse.Namespace,
    package: MotaInfo,
    expected_body_hash: bytes | None,
    previous_body_hash: bytes | None = None,
    identity_reply: str | None = None,
) -> None:
    if identity_reply is None:
        identity_reply = controller.remote_command(args.target, "ota self")
    hash_match = re.search(r"\bbase_hash=([0-9A-Fa-f]{16})\b", identity_reply)
    if not hash_match:
        raise OtaError("post-reboot `ota self` did not report a running body hash")
    installed_hash = bytes.fromhex(hash_match.group(1))
    if expected_body_hash is not None and installed_hash != expected_body_hash:
        raise OtaError(
            "destination rebooted, but its running firmware hash is not the expected new image"
        )
    if expected_body_hash is None:
        if previous_body_hash is None:
            raise OtaError(
                "the package does not expose its new body hash, so installation "
                "cannot be distinguished from the old firmware"
            )
        if installed_hash == previous_body_hash:
            raise OtaError(
                "destination still reports its pre-install firmware hash"
            )

    installed_version = None
    try:
        stats_reply = controller.remote_command(args.target, "ota stats")
        version_match = re.search(
            r"\bfw (v\d+\.\d+\.\d+(?:\.\d+)?)\b", stats_reply
        )
        if version_match:
            installed_version = parse_version(version_match.group(1))
    except OtaError as exc:
        print(f"[warn] post-reboot `ota stats` version query failed: {exc}")
    if installed_version is not None:
        if installed_version != package.fw_version:
            raise OtaError(
                f"destination `ota stats` reports "
                f"{format_version(installed_version)} after reboot; "
                f"expected {package.version}"
            )
    else:
        version_reply = controller.remote_command(args.target, "ver")
        runtime_version = extract_reply_version(version_reply)
        if runtime_version is None:
            raise OtaError(
                f"post-reboot `ver` returned no firmware version: {version_reply}"
            )
        if expected_body_hash is None and runtime_version != package.fw_version:
            raise OtaError(
                f"destination `ver` reports {format_version(runtime_version)} "
                f"after reboot; expected {package.version}"
            )
        if expected_body_hash is not None and runtime_version != package.fw_version:
            print(
                f"[verified] `ota stats` did not expose an EndF version; runtime "
                f"label {format_version(runtime_version)} differs from package "
                f"{package.version}, but the exact expected body hash matches"
            )
    print(
        f"[verified] {args.target}: {package.version} "
        f"body={installed_hash.hex().upper()}"
    )


def wait_for_post_install_identity(
    controller: Controller,
    args: argparse.Namespace,
    expected_body_hash: bytes | None,
    previous_body_hash: bytes | None,
    wait_seconds: int,
) -> str:
    """Probe the exact running identity instead of sleeping through reboot."""
    if wait_seconds <= 0:
        raise OtaError("post-install ready-probe window must be positive")
    if expected_body_hash is None and previous_body_hash is None:
        raise OtaError(
            "post-install readiness needs an expected or previous body hash"
        )

    started = time.monotonic()
    probe_at = min(POST_INSTALL_READY_PROBE_INTERVAL_SECONDS, wait_seconds)
    last_failure = "no identity reply"
    while True:
        delay = started + probe_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        print(
            f"[reboot] probing `ota self` at {probe_at}s "
            f"(window {wait_seconds}s)"
        )
        try:
            reply = controller.remote_command(
                args.target,
                "ota self",
                retry=False,
                reply_timeout=min(
                    POST_INSTALL_READY_PROBE_INTERVAL_SECONDS,
                    getattr(
                        controller,
                        "reply_timeout",
                        POST_INSTALL_READY_PROBE_INTERVAL_SECONDS,
                    ),
                ),
            )
        except TransmissionError as exc:
            last_failure = str(exc)
        else:
            match = re.search(r"\bbase_hash=([0-9A-Fa-f]{16})\b", reply)
            if match is None:
                raise OtaError(
                    "post-reboot `ota self` replied without a running body "
                    f"hash: {reply}"
                )
            running_hash = bytes.fromhex(match.group(1))
            if expected_body_hash is not None:
                if running_hash == expected_body_hash:
                    print(
                        f"[reboot] expected body "
                        f"{running_hash.hex().upper()} is ready"
                    )
                    return reply
                if (
                    previous_body_hash is not None
                    and running_hash == previous_body_hash
                ):
                    last_failure = (
                        "destination still reports its pre-install body "
                        f"{running_hash.hex().upper()}"
                    )
                else:
                    raise OtaError(
                        "post-reboot `ota self` reports an unexpected running "
                        f"body {running_hash.hex().upper()}"
                    )
            elif running_hash != previous_body_hash:
                print(
                    f"[reboot] new body {running_hash.hex().upper()} is ready"
                )
                return reply
            else:
                last_failure = (
                    "destination still reports its pre-install body "
                    f"{running_hash.hex().upper()}"
                )

        if probe_at >= wait_seconds:
            raise OtaError(
                f"destination did not report its installed identity during "
                f"the {wait_seconds}s ready-probe window; last result: "
                f"{last_failure}"
            )
        probe_at = min(
            probe_at + POST_INSTALL_READY_PROBE_INTERVAL_SECONDS,
            wait_seconds,
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
    source_cli = parser.add_mutually_exclusive_group()
    source_cli.add_argument(
        "--source-cli-serial", metavar="PORT",
        help="local text-CLI port for a TCP seeder source",
    )
    source_cli.add_argument(
        "--source-cli-tcp", metavar="HOST[:PORT]",
        help="companion_radio_full text console for a TCP seeder source (default port 5002)",
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
        "--temp-radio", default="909.950,250,5,5,120",
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
    parser.add_argument(
        "--reboot-wait",
        type=int,
        default=DEFAULT_POST_INSTALL_READY_WAIT_SECONDS,
        help=(
            "post-install identity-probe window in seconds; `ota self` is "
            "scheduled every 10 seconds"
        ),
    )
    parser.add_argument(
        "--source-already-temp", action="store_true",
        help="do not configure a TCP source; assert it is already on --temp-radio",
    )
    parser.add_argument(
        "--source-shares-controller",
        action="store_true",
        help=(
            "TCP source is the controller's own Full Companion; verify its "
            "port-5000 identity and let the controller radio switch move both"
        ),
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
    parser.add_argument(
        "--clear-completed-manifest",
        metavar="ID",
        help=(
            "clear this exact previous ready-to-install manifest only after "
            "--clear-completed-on-body-hash proves it is already running"
        ),
    )
    parser.add_argument(
        "--clear-completed-on-body-hash",
        metavar="HASH",
        help="16-hex running EndF body hash required by --clear-completed-manifest",
    )
    parser.add_argument(
        "--expected-installed-body-hash",
        metavar="HASH",
        help=(
            "require this exact 16-hex EndF body hash after installation; "
            "used when a delta does not expose its reconstructed target hash"
        ),
    )
    parser.add_argument("--yes", action="store_true", help="skip the destructive-action confirmation")
    parser.add_argument(
        "--require-system-watchdog-off",
        action="store_true",
        help=(
            "refuse each ota install attempt unless the destination immediately "
            "reports that its system watchdog is off"
        ),
    )
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
    clear_manifest = args.clear_completed_manifest
    clear_body_hash = args.clear_completed_on_body_hash
    if bool(clear_manifest) != bool(clear_body_hash):
        parser.error(
            "--clear-completed-manifest and --clear-completed-on-body-hash "
            "must be supplied together"
        )
    if clear_manifest:
        if not re.fullmatch(r"[0-9A-Fa-f]{8}", clear_manifest):
            parser.error("--clear-completed-manifest must be 8 hexadecimal characters")
        if not re.fullmatch(r"[0-9A-Fa-f]{16}", clear_body_hash):
            parser.error(
                "--clear-completed-on-body-hash must be 16 hexadecimal characters"
            )
        args.clear_completed_manifest = clear_manifest.upper()
        args.clear_completed_on_body_hash = clear_body_hash.upper()
        if args.prepare_only:
            parser.error("completed-manifest cleanup is only valid during a live run")
    expected_installed_hash = args.expected_installed_body_hash
    if expected_installed_hash:
        if not re.fullmatch(r"[0-9A-Fa-f]{16}", expected_installed_hash):
            parser.error(
                "--expected-installed-body-hash must be 16 hexadecimal characters"
            )
        args.expected_installed_body_hash = expected_installed_hash.upper()
        if args.prepare_only:
            parser.error(
                "--expected-installed-body-hash is only valid during a live install"
            )
        if args.no_install:
            parser.error(
                "--expected-installed-body-hash cannot be used with --no-install"
            )
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
        if args.source_serial and (args.source_cli_serial or args.source_cli_tcp):
            parser.error("--source-cli-serial/--source-cli-tcp are only used with --source-tcp")
        if args.source_tcp and not (
            args.source_cli_serial or args.source_cli_tcp or args.source_already_temp
        ):
            parser.error(
                "--source-tcp also needs --source-cli-serial, --source-cli-tcp, or --source-already-temp"
            )
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
    if not args.prepare_only:
        remote_setup_seconds = (1 + len(args.relay)) * args.reply_timeout
        source_setup_seconds = (
            0 if args.source_already_temp or args.source_shares_controller else 30
        )
        final_reply_count = 1 if args.no_install else 4 + len(args.relay)
        required_temp_seconds = (
            remote_setup_seconds
            + source_setup_seconds
            + TEMP_RADIO_SWITCH_DELAY_SECONDS
            + args.seeder_start_wait
            + args.discovery_timeout
            + args.transfer_timeout_minutes * 60
            + adaptive_poll_ceiling(args.poll_seconds)
            + args.reply_timeout * final_reply_count
        )
        temp_seconds = args.temp_values[4] * 60
        if required_temp_seconds >= temp_seconds:
            minimum_minutes = required_temp_seconds // 60 + 1
            parser.error(
                f"TempRadio must last at least {minimum_minutes} minutes for "
                "remote setup, seeder startup, discovery, transfer, and one "
                "final poll plus install checks"
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


def main(
    argv: list[str] | None = None,
    *,
    controller_override: Controller | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)
    work_dir: Path | None = None
    controller: Controller | None = controller_override
    original_radio: RadioSettings | None = None
    controller_changed = False
    seeder: SeederProcess | None = None
    seeder_attempted = False
    source_temp_owned = False
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
            if controller is None:
                controller = Controller(args, password)
            verify_shared_source_identity(controller, args)
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
        if args.expected_installed_body_hash:
            asserted_body_hash = bytes.fromhex(args.expected_installed_body_hash)
            if (
                expected_body_hash is not None
                and expected_body_hash != asserted_body_hash
            ):
                raise OtaError(
                    "--expected-installed-body-hash conflicts with the body hash "
                    "exposed by the selected package"
                )
            expected_body_hash = asserted_body_hash
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

        temp_radio = RadioSettings(
            freq, bandwidth, sf, cr, original_radio.repeat
        )

        # Move far nodes first while the controller is still on the normal
        # channel. Relays are supplied in farthest-to-nearest order. A target
        # can process TempRadio even when its reply is lost, so resolve that
        # ambiguity by probing its exact identity on the temporary channel
        # instead of replaying the command from the normal channel.
        arm_target_temp_radio(
            controller, args, temp_command, temp_radio, original_radio
        )
        for relay_name, relay_password in args.relay_values:
            temp_reply = controller.remote_command(
                relay_name, temp_command, password=relay_password
            )
            require_temp_radio_reply(relay_name, temp_reply)
        if not args.source_already_temp and not args.source_shares_controller:
            source_cli_command(args, temp_command)
            source_temp_owned = True

        controller_changed = True
        controller.set_radio(temp_radio, "switch controller to TempRadio")
        if args.source_shares_controller:
            # The Full Companion OTA egress gate needs its local TempRadio
            # state even though the Binary API has already moved the same
            # physical radio. Enter it after persisting the temporary tuple;
            # cleanup runs `normalradio` before the Binary restore.
            source_cli_command(args, temp_command)
            source_temp_owned = True
        time.sleep(TEMP_RADIO_SWITCH_DELAY_SECONDS)

        seeder = SeederProcess(args, package_path.parent, work_dir)
        seeder_attempted = True
        seeder.start()
        find_and_start_pull(controller, args, package, seeder)
        monitor_download(controller, args, package, seeder)

        if args.no_install:
            print(f"{args.target} is ready to install; leaving the verified update staged.")
            if args.leave_controller_radio:
                print(
                    "[cleanup] preserving TempRadio windows because "
                    "--leave-controller-radio was requested"
                )
            else:
                shorten_target_temp_window(controller, args)
                shorten_relay_temp_windows(controller, args)
                seeder.stop()
                seeder = None
                if source_temp_owned:
                    shorten_source_temp_window(args)
                    source_temp_owned = False
            return 0

        install_confirmed = request_install(controller, args, package)
        if install_confirmed:
            print(f"[install] {args.target} accepted the image and is rebooting")
        else:
            print(
                "[install] outcome is uncertain; post-reboot identity must "
                "prove whether the command arrived"
            )

        # Once the target has accepted (or may have accepted) installation,
        # the relays are no longer needed on TempRadio. Give each a short,
        # bounded window, then verify through the restored normal route.
        shorten_relay_temp_windows(controller, args)

        # Stop seeding before returning the controller to its ordinary channel.
        seeder.stop()
        seeder = None
        if source_temp_owned and not args.leave_controller_radio:
            shorten_source_temp_window(args)
            source_temp_owned = False
        controller.set_radio(original_radio, "restore controller radio for verification")
        controller_changed = False
        relay_wait = (
            TEMP_RADIO_RETURN_MINUTES * 60 + TEMP_RADIO_RETURN_MARGIN_SECONDS
            if args.relay_values else 0
        )
        ready_probe_window = max(args.reboot_wait, relay_wait)
        identity_reply = wait_for_post_install_identity(
            controller,
            args,
            expected_body_hash,
            target.base_hash,
            ready_probe_window,
        )
        try:
            verify_installed(
                controller,
                args,
                package,
                expected_body_hash,
                target.base_hash,
                identity_reply,
            )
        finally:
            if args.leave_controller_radio:
                controller.set_radio(
                    temp_radio, "return controller to TempRadio after verification"
                )
                controller_changed = True
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
            if source_temp_owned and not args.leave_controller_radio:
                shorten_source_temp_window(args, check=False)
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
