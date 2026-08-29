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
import stat
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
MOTA_BOOT_FORMAT_VERSION = 3
MOTA_FIXED_MANIFEST_SIZE = 197
MOTA_FLAG_FULL = 0x01
MOTA_FLAG_SIGNED = 0x02
MOTA_FLAG_BOOTLOADER = 0x04
MOTA_KNOWN_FLAGS = MOTA_FLAG_FULL | MOTA_FLAG_SIGNED | MOTA_FLAG_BOOTLOADER
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
SOURCE_RXPS_BUSY_RETRY_LIMIT = 32
ADAPTIVE_POLL_MAX_FACTOR = 3
TEMP_RADIO_SWITCH_DELAY_SECONDS = 3
TEMP_RADIO_RETURN_MINUTES = 1
TEMP_RADIO_RETURN_MARGIN_SECONDS = 15
SHARED_SOURCE_NORMAL_TIMEOUT_SECONDS = 30
SHARED_SOURCE_NORMAL_POLL_SECONDS = 1
INSTALL_TARGET_WINDOW_MINUTES = 3
# Internal-flash nRF52 installs can spend minutes applying a large in-place
# patch before USB and LoRa are available again.  The RAK3401 hardware run
# measured a 106-KiB package taking 64 seconds from USB disconnect to
# re-enumeration, while later chain packages are substantially larger.  Keep
# the readiness probe bounded at five minutes; it still returns immediately
# when the exact installed identity replies.
DEFAULT_POST_INSTALL_READY_WAIT_SECONDS = 300
POST_INSTALL_READY_PROBE_INTERVAL_SECONDS = 10
COMPANION_TERMINAL_START = "+++MESHCORE-TERM-START"
COMPANION_TERMINAL_STOP = "+++MESHCORE-TERM-STOP"
# Firmware may hold the apply reboot for up to 15 seconds while its reply
# drains. Do not interpret "still ready" as a failed install before that cap.
INSTALL_RECONCILE_WAIT_SECONDS = 20
DEFAULT_RELAY_TX_DELAY = 0.3
RELAY_TIMING_COMMANDS_PER_RELAY = 12
RELAY_TIMING_RECOVERY_FILE = "relay-timing-settings.json"
TARGET_RXPS_RECOVERY_FILE = "target-rxps-settings.json"
SOURCE_RXPS_RECOVERY_FILE = "source-rxps-settings.json"
MIN_MESHCLI_VERSION = (1, 6, 0)
# v1.17.1.5 is the first release-version contract in which every packet,
# including retries, uses the same tuple-selected physical preamble: normally
# 32 symbols at SF5-SF8, then 64 or 128 only where each shorter choice cannot
# make RXPS viable. Radio changes also safely retune a saved level-based RXPS
# preference. Older or unparseable versions are deliberately treated as legacy.
RXPS_ADAPTIVE_PREAMBLE_MIN_VERSION = 0x01110105
RXPS_MIN_PERIOD_US = 32
RXPS_MAX_PERIOD_US = 30_000_000
RXPS_WIRE_TRANSITION_US = 6_000

# This module is also imported by the pinned RAK3401 chain runner and by the
# offline test suite. Keep diagnostics disabled until either CLI main enables
# them; an uninitialized module global would break those direct callers.
DEBUG = False

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
    bootloader_version: str | None
    bootloader_abi: int | None
    bootloader_codecs: int | None
    status: str
    self_status: str
    current_version: str | None = None
    current_version_source: str | None = None
    nrf_qspi: bool = False

    @property
    def nrf_external(self) -> bool:
        """Whether nRF52 OTA staging is outside internal application flash."""
        return self.nrf_sd or self.nrf_qspi


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


@dataclass(frozen=True)
class RelayTimingSettings:
    name: str
    password: str
    rxdelay: float
    txdelay: float


@dataclass(frozen=True)
class RxpsSettings:
    enabled: bool
    rx_us: int
    sleep_us: int
    level: int | None = None
    preamble: int | None = None


@dataclass(frozen=True)
class RxpsTempProfile:
    boundary_level: int
    boundary_preamble: int


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


def rxps_level_timings_us(
    level: int,
    bandwidth_khz: float,
    spreading_factor: int,
    preamble_symbols: int,
) -> tuple[int, int]:
    """Mirror MeshCore's level timing calculation for wire-preamble selection."""
    symbol_us = (1000.0 * (1 << spreading_factor)) / bandwidth_khz
    amount = (level - 1) / 9.0
    rx_start_symbols = 12.0 if preamble_symbols == 16 else 16.0
    sleep_start_symbols = 2.0 if preamble_symbols == 16 else 15.0
    rx_symbols = rx_start_symbols + amount * (8.0 - rx_start_symbols)
    sleep_edge_symbols = preamble_symbols + 4.25 - 8.0
    sleep_symbols = sleep_start_symbols + amount * (
        sleep_edge_symbols - sleep_start_symbols
    )
    rx_us = math.ceil(rx_symbols * symbol_us)
    sleep_us = int(sleep_symbols * symbol_us)
    if rx_us < RXPS_MIN_PERIOD_US or sleep_us < RXPS_MIN_PERIOD_US:
        return RXPS_MIN_PERIOD_US, RXPS_MIN_PERIOD_US
    return rx_us, sleep_us


def preamble_supports_rxps(
    bandwidth_khz: float,
    spreading_factor: int,
    preamble_symbols: int,
) -> bool:
    for level in range(1, 11):
        rx_us, sleep_us = rxps_level_timings_us(
            level, bandwidth_khz, spreading_factor, preamble_symbols
        )
        if (
            RXPS_MIN_PERIOD_US <= rx_us <= RXPS_MAX_PERIOD_US
            and RXPS_MIN_PERIOD_US <= sleep_us <= RXPS_MAX_PERIOD_US
            and sleep_us > RXPS_WIRE_TRANSITION_US
            and (rx_us * 8) // 125 != 0
            and ((sleep_us - RXPS_WIRE_TRANSITION_US) * 8) // 125 != 0
        ):
            return True
    return False


def wire_preamble_symbols(
    bandwidth_khz: float,
    spreading_factor: int,
) -> int:
    """Return the deterministic MeshCore wire preamble for an SF/BW tuple."""
    if spreading_factor > 8:
        return 16
    if spreading_factor < 5 or bandwidth_khz <= 0.0:
        return 32
    if preamble_supports_rxps(bandwidth_khz, spreading_factor, 32):
        return 32
    if preamble_supports_rxps(bandwidth_khz, spreading_factor, 64):
        return 64
    if preamble_supports_rxps(bandwidth_khz, spreading_factor, 128):
        return 128
    return 32


def lora_airtime_seconds(
    payload_bytes: int,
    bandwidth_khz: float,
    spreading_factor: int,
    coding_rate: int,
    *,
    preamble_symbols: int | None = None,
) -> float:
    """Estimate explicit-header LoRa airtime for one Mesh packet."""
    if preamble_symbols is None:
        preamble_symbols = wire_preamble_symbols(
            bandwidth_khz, spreading_factor
        )
    symbol_seconds = (2 ** spreading_factor) / (bandwidth_khz * 1000.0)
    low_data_rate = 1 if symbol_seconds >= 0.016 else 0
    numerator = (
        8 * payload_bytes - 4 * spreading_factor + 28 + 16
    )
    denominator = 4 * (spreading_factor - 2 * low_data_rate)
    payload_symbols = 8 + max(
        math.ceil(numerator / denominator) * coding_rate,
        0,
    )
    return (preamble_symbols + 4.25 + payload_symbols) * symbol_seconds


def ota_path_transmissions(args: argparse.Namespace) -> int:
    # The source always transmits once; every explicitly managed relay adds
    # one forwarding transmission to the response train.
    return 1 + len(getattr(args, "relay", []) or [])


def initial_status_wait_seconds(
    args: argparse.Namespace,
    package: MotaInfo,
) -> float:
    """Choose a quiet first-poll window from package and TempRadio airtime."""
    baseline = float(args.poll_seconds)
    temp_values = getattr(args, "temp_values", None)
    if not temp_values:
        return baseline
    _frequency, bandwidth, sf, cr, _minutes = temp_values
    blocks = math.ceil(package.payload_size / package.block_size)
    fragments = math.ceil(package.block_size / 160)
    # DATA dominates. Treat proofs and the roughly one request per three
    # adaptive blocks as maximum-size packets, then apply a measured 4x guard
    # for CAD, RX/TX turnarounds, flash commits, and scheduler latency.
    packets = blocks * (fragments + 1) + math.ceil(blocks / 3)
    estimated = (
        packets
        * lora_airtime_seconds(184, bandwidth, sf, cr)
        * ota_path_transmissions(args)
        * 4.0
    )
    return min(adaptive_poll_ceiling(baseline), max(baseline, estimated))


def transfer_tail_guard_seconds(args: argparse.Namespace) -> float:
    """Drain the last loaded block after the host-read progress signal."""
    temp_values = getattr(args, "temp_values", None)
    if not temp_values:
        return 3.0
    _frequency, bandwidth, sf, cr, _minutes = temp_values
    return max(
        2.0,
        10
        * lora_airtime_seconds(184, bandwidth, sf, cr)
        * ota_path_transmissions(args)
        * 4.0,
    )


def passive_progress_stall_seconds(
    args: argparse.Namespace,
    package: MotaInfo,
) -> float:
    """Return the no-progress interval that justifies an OTA status packet.

    A healthy transfer can legitimately spend a long time transmitting the
    fragments for one block before it asks the host for another. Scale that
    interval with the selected LoRa modulation and managed relay count so a
    slow/two-hop deployment is not mistaken for a stall. Fast TempRadio links
    retain a 20-second floor, which is long enough to cover scheduler and flash
    jitter without hiding a genuinely lost pull indefinitely.
    """
    baseline = float(args.poll_seconds)
    temp_values = getattr(args, "temp_values", None)
    if not temp_values:
        return max(20.0, baseline)
    _frequency, bandwidth, sf, cr, _minutes = temp_values
    fragments = math.ceil(package.block_size / 160)
    one_block_packets = fragments + 2  # request/proof plus DATA fragments
    estimated = (
        one_block_packets
        * lora_airtime_seconds(184, bandwidth, sf, cr)
        * ota_path_transmissions(args)
        * 6.0
    )
    return min(
        max(20.0, adaptive_poll_ceiling(baseline)),
        max(20.0, estimated),
    )


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


def select_rxps_temp_profile(
    current_version: str | None,
    temp_values: tuple[float, float, int, int, int],
    *,
    all_participants_support_adaptive_preamble: bool,
) -> RxpsTempProfile | None:
    """Return a qualified RXPS profile, or None when automation must use off.

    The version boundary is intentional. Several historical builds reused the
    same human version while carrying different RXPS implementations, so only
    the first forward release contract and later are allowed to keep RXPS on.
    """
    version = parse_version(current_version) if current_version else None
    if version is None or version < RXPS_ADAPTIVE_PREAMBLE_MIN_VERSION:
        return None

    _frequency, bandwidth, sf, _cr, _minutes = temp_values

    def is_bandwidth(expected: float) -> bool:
        return abs(bandwidth - expected) <= 0.001

    # A tuple-selected long preamble relies on every possible sender following
    # the same adaptive wire contract. A single legacy source, controller, or
    # relay makes it unsafe even when the destination itself is current.
    if (
        (sf == 5 and is_bandwidth(250.0))
        or (sf == 6 and is_bandwidth(500.0))
    ):
        if not all_participants_support_adaptive_preamble:
            return None
        return RxpsTempProfile(8, 64)

    if sf == 5 and is_bandwidth(500.0):
        if not all_participants_support_adaptive_preamble:
            return None
        return RxpsTempProfile(8, 128)

    # These three tuples all have a 256 us symbol and were qualified with the
    # same 32-symbol receive-window assumption. Since 32 is sufficient, current
    # firmware keeps the normal 32-symbol wire preamble.
    if (
        (sf == 7 and is_bandwidth(500.0))
        or (sf == 6 and is_bandwidth(250.0))
        or (sf == 5 and is_bandwidth(125.0))
    ):
        return RxpsTempProfile(7, 32)

    if sf == 5 and is_bandwidth(62.5):
        return RxpsTempProfile(10, 16)

    # Unknown tuples remain continuous-RX rather than extrapolating beyond
    # qualified timing data.
    return None


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
    flags = blob[9]
    if blob[8] == MOTA_BOOT_FORMAT_VERSION and flags & MOTA_FLAG_BOOTLOADER:
        raise OtaError(
            "bootloader mOTA packages require the device's explicit `ota bootloader install` "
            "workflow; this application-update runner deliberately refuses them"
        )
    if blob[8] != MOTA_FORMAT_VERSION:
        raise OtaError(f"unsupported mOTA format version {blob[8]}")
    if flags & MOTA_FLAG_BOOTLOADER or flags & ~MOTA_KNOWN_FLAGS:
        raise OtaError("invalid flags in v2 application mOTA")
    if blob[10] != 0x12:
        raise OtaError(f"unsupported mOTA hash algorithm 0x{blob[10]:02x}")

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
        if target.platform == "nrf52" and not target.nrf_external:
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
        version = target.bootloader_version or "unknown version"
        if (
            target.bootloader_abi is not None
            and target.bootloader_abi < MOTA_FORMAT_VERSION
        ):
            return False, (
                f"bootloader {version} ABI {target.bootloader_abi} cannot "
                f"apply mOTA format {MOTA_FORMAT_VERSION}; install an "
                "exact-board OTAFIX bootloader with current mOTA support"
            )
        if (
            target.bootloader_codecs is not None
            and not target.bootloader_codecs & (1 << info.codec_id)
        ):
            return False, (
                f"bootloader {version} codec mask "
                f"0x{target.bootloader_codecs:X} does not support codec "
                f"{info.codec_id}; install an exact-board OTAFIX bootloader "
                "with the required codec support"
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


def redact_text(value: str, sensitive_values: tuple[str, ...] = ()) -> str:
    for sensitive in sensitive_values:
        if sensitive:
            value = value.replace(sensitive, "<REDACTED>")
    return value


def _debug_text(
    value: str | bytes | bytearray | memoryview | None,
    sensitive_values: tuple[str, ...] = (),
) -> str:
    if value is None:
        return "<empty>"
    if not isinstance(value, str):
        value = bytes(value).decode("utf-8", "replace")
    return redact_text(value, sensitive_values).rstrip() or "<empty>"


def debug_stream(
    label: str,
    value: str | bytes | bytearray | memoryview | None,
    sensitive_values: tuple[str, ...] = (),
) -> None:
    if not DEBUG:
        return
    print(f"[debug] {label}:")
    print(_debug_text(value, sensitive_values))


def meshcli_sensitive_values(commands: list[str]) -> tuple[str, ...]:
    return tuple(
        commands[index + 2]
        for index, token in enumerate(commands)
        if token == "login" and index + 2 < len(commands)
    )


def redact_meshcli_commands(commands: list[str]) -> list[str]:
    """Return a printable copy with every remote-admin password removed."""
    redacted = list(commands)
    for index, token in enumerate(commands):
        # Controller command chains encode login as three consecutive tokens:
        # login, target, password. Redact from the original list so a target or
        # command literally named "login" cannot hide a later real password.
        if token == "login" and index + 2 < len(redacted):
            redacted[index + 2] = "<REDACTED>"
    return redacted


def run_checked(
    command: list[str],
    *,
    label: str,
    timeout: float | None = None,
    sensitive_values: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    print(f"[run] {label}")
    if DEBUG:
        printable_command = [
            redact_text(token, sensitive_values) for token in command
        ]
        print(f"[debug] command: {shlex.join(printable_command)}")
        print(f"[debug] timeout: {timeout if timeout is not None else 'none'}")
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
        if DEBUG:
            print("[debug] timed out")
            debug_stream("partial stdout", exc.stdout, sensitive_values)
            debug_stream("partial stderr", exc.stderr, sensitive_values)
        raise OtaError(f"timed out while running {label}") from exc
    if DEBUG:
        print(f"[debug] exit code: {result.returncode}")
        debug_stream("stdout", result.stdout, sensitive_values)
        debug_stream("stderr", result.stderr, sensitive_values)
    if result.returncode != 0:
        detail = redact_text(
            (result.stderr or result.stdout).strip(), sensitive_values
        )
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
        if target.platform == "nrf52" and (
            not target.nrf_external or args.base is not None
        ):
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
            # External staging leaves the application region available as the
            # detools workspace. 0xC6000 is safe for both S140 v7 (app starts
            # at 0x27000) and older v6 layouts (0x26000). Internal staging
            # retains its deliberately smaller legacy workspace.
            inplace_memory = args.inplace_memory or (
                "0xC6000" if target.nrf_external else "0x98000"
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
    if command == "get bootloader.ver":
        return (
            bool(re.fullmatch(r">\s*\S+", text))
            or is_unknown
            or (is_error and "unsupported" in lowered)
        )
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
            lowered.startswith((
                "ok pulling ", "ok resuming ", "usage: ota pull",
                "choose a destination",
            ))
            or is_unknown
            or needs_temp
            or (
                is_error
                and any(
                    word in lowered
                    for word in (
                        "update", "destination", "pull", "fetch", "busy",
                        "folder", "slot", "codec", "bootloader", "apply",
                        "rescue", "endf", "storage",
                    )
                )
            )
        )
    if command == "ota cancel":
        # Current persistent stores can report that the manager/RAM session
        # was dropped but durable media invalidation failed. Treat that as the
        # command's reply so callers surface the real rejection instead of
        # waiting through reply-timeout retries for an impossible OK.
        return (
            lowered.startswith("ok dropped ")
            or (is_error and "persistent ota slot" in lowered)
            or is_unknown
            or needs_temp
        )
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


def parse_bootloader_version_reply(
    reply: str,
) -> tuple[str | None, str | None]:
    """Return platform/version, or no platform for legacy firmware."""
    text = reply.strip()
    version_match = re.fullmatch(r">\s*(\S+)", text)
    if version_match:
        version = version_match.group(1)
        return "nrf52", None if version.lower() == "unknown" else version
    if re.fullmatch(r"err(?:or)?:\s*unsupported", text, re.IGNORECASE):
        return "esp32", None
    if text.lower().startswith(("unknown command", "command not found")):
        return None, None
    raise OtaError(
        "could not interpret destination `get bootloader.ver` reply: "
        f"{reply}"
    )


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
        launch_command = [*self.command, "-C", "-i"]
        if DEBUG:
            print(
                "[debug] persistent meshcli launch: "
                f"{shlex.join(launch_command)}"
            )
        try:
            self.process = subprocess.Popen(
                launch_command,
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

    def _read_frame(
        self,
        start: str,
        end: str,
        timeout: float,
        sensitive_values: tuple[str, ...] = (),
    ) -> str:
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
                debug_stream(
                    "persistent meshcli pending output",
                    self.pending[-4096:],
                    sensitive_values,
                )
                self.close()
                raise OtaError("persistent meshcli command timed out")
            try:
                chunk = self.output_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self.process is not None and self.process.poll() is not None:
                    detail = bytes(self.pending[-4096:]).decode("utf-8", "replace")
                    debug_stream(
                        "persistent meshcli final output", detail,
                        sensitive_values,
                    )
                    self.close()
                    raise OtaError(
                        "persistent meshcli session exited"
                        + (f": {detail.strip()}" if detail.strip() else "")
                    )
                continue
            if chunk is None:
                detail = bytes(self.pending[-4096:]).decode("utf-8", "replace")
                debug_stream(
                    "persistent meshcli final output", detail,
                    sensitive_values,
                )
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
        sensitive_values: tuple[str, ...] = (),
    ) -> str:
        if self.process is None or self.process.poll() is not None:
            self._start()
        assert self.process is not None and self.process.stdin is not None
        command = shlex.join(["script", str(script_path)]) + "\n"
        if DEBUG:
            print(f"[debug] persistent meshcli input: {command.rstrip()}")
            print(f"[debug] persistent meshcli timeout: {timeout}")
        try:
            self.process.stdin.write(command.encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise OtaError(f"persistent meshcli input failed: {exc}") from exc
        output = self._read_frame(
            start_marker, end_marker, timeout, sensitive_values
        )
        output = redact_text(output, sensitive_values)
        if DEBUG:
            print("[debug] persistent meshcli process: running")
            debug_stream(
                "persistent meshcli stdout", output, sensitive_values
            )
            debug_stream("persistent meshcli stderr", None)
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
        sensitive_values = meshcli_sensitive_values(commands)
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
            if DEBUG:
                printable = redact_meshcli_commands(commands)
                print(f"[debug] meshcli operation: {label}")
                print(f"[debug] meshcli commands: {shlex.join(printable)}")
                print(f"[debug] meshcli script: {script_path}")
            if os.name != "nt":
                script_path.chmod(0o600)
            timeout = max(90, self.reply_timeout + 60)
            if self._meshcli_session is not None:
                stdout = self._meshcli_session.run_script(
                    script_path, frame_start, frame_end, timeout,
                    sensitive_values,
                )
                result = subprocess.CompletedProcess(
                    [], 0, stdout=stdout, stderr=""
                )
            else:
                command = [
                    self.meshcli, "-j", "-c", "off", *self.connection,
                    "script", str(script_path),
                ]
                result = run_checked(
                    command,
                    label=label,
                    timeout=timeout,
                    sensitive_values=sensitive_values,
                )
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            stdout=redact_text(result.stdout, sensitive_values),
            stderr=redact_text(result.stderr, sensitive_values),
        )

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

    def get_firmware_version(self) -> tuple[str, int]:
        objects = self._run(["ver"], "read controller firmware version")
        for value in reversed(objects):
            version_text = value.get("ver")
            if not isinstance(version_text, str):
                continue
            version = extract_reply_version(version_text)
            if version is not None:
                return version_text, version
        raise OtaError("meshcli did not return the controller firmware version")

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
    bootloader_reply = controller.remote_command(
        args.target, "get bootloader.ver"
    )
    reported_platform, bootloader_version = parse_bootloader_version_reply(
        bootloader_reply
    )
    self_status = controller.remote_command(args.target, "ota self")
    hash_match = re.search(r"base_hash=([0-9A-Fa-f]{16})", self_status)
    if not hash_match:
        raise OtaError("could not read destination base hash from `ota self`")
    base_hash = bytes.fromhex(hash_match.group(1))
    combined = f"{status} {self_status}"
    if reported_platform is None:
        platform = (
            "nrf52"
            if "bootloader:" in combined or "| bl:" in combined
            else "esp32"
        )
        print(
            "[warn] destination firmware does not implement "
            "`get bootloader.ver`; using legacy `ota self` platform markers"
        )
    else:
        platform = reported_platform
    nrf_sd = "SD apply OK" in combined or bool(
        re.search(r"\bbl:SD\b", combined)
    )
    nrf_qspi = "QSPI apply OK" in combined or bool(
        re.search(r"\bbl:QSPI\b", combined)
    )
    qspi_store = re.search(r"\bQSPI store:(ERR\s+)?(\d+)K\b", combined)
    if nrf_qspi and qspi_store and (
        qspi_store.group(1) is not None or int(qspi_store.group(2)) == 0
    ):
        raise OtaError(
            "destination bootloader supports QSPI apply, but the application "
            "reports `QSPI store:ERR 0K`; check the exact-board firmware, "
            "flash wiring, and flash power before downloading"
        )
    if platform == "nrf52" and (
        "NO mota-apply" in combined
        or "NO SD mota-apply" in combined
        or "NO QSPI mota-apply" in combined
        or bool(re.search(r"\bbl:NONE\b", combined))
    ):
        version = bootloader_version or "unknown version"
        raise OtaError(
            f"destination nRF52 bootloader {version} cannot apply this mOTA; "
            "install the exact-board OTAFIX bootloader first"
        )
    hw_match = re.search(r"\bhw=([^ |]+)", status)
    hw_id = hw_match.group(1) if hw_match and hw_match.group(1) != "?" else None
    bootloader_abi = None
    bootloader_codecs = None
    caps_match = re.search(
        r"\babi=(\d+)\s+codecs=0x([0-9A-Fa-f]+)", combined
    )
    if caps_match:
        bootloader_abi = int(caps_match.group(1))
        bootloader_codecs = int(caps_match.group(2), 16)
    elif platform == "nrf52":
        version = bootloader_version or "unknown version"
        raise OtaError(
            f"destination nRF52 bootloader {version} does not report a "
            "compatible mOTA ABI and codec mask in `ota self`; install the "
            "exact-board OTAFIX bootloader first"
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
        name=args.target,
        target_id=target_id,
        base_hash=base_hash,
        platform=platform,
        nrf_sd=nrf_sd,
        hw_id=hw_id,
        bootloader_version=bootloader_version,
        bootloader_abi=bootloader_abi,
        bootloader_codecs=bootloader_codecs,
        status=status,
        self_status=self_status,
        current_version=current_version,
        current_version_source=current_version_source,
        nrf_qspi=nrf_qspi,
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
            # Legacy port 5002 prefixes its bounded OTA reply with `->`.
            # Full Companion now exposes the same terminal as USB and writes
            # the command reply directly. Accept both wire formats so current
            # automation can manage old and new source firmware.
            match = re.search(
                r"(?:^|\r?\n)[ \t]*(?:->[ \t]*)?(.*?)\r?\n>[ \t]*$",
                text,
                re.DOTALL,
            )
            if not match:
                raise TransmissionError(
                    f"source TCP console returned no command reply: {text.strip() or 'no output'}"
                )
            output = match.group(1).strip()
        else:
            wire_command = command_text
            if getattr(args, "source_companion_terminal", False):
                # meshcli raw mode keeps one serial open while writing this
                # compound command. STOP first makes this independent of the
                # port's current state: ASCII consumes it and returns to
                # Binary, while Binary ignores it as an ordinary bounded line.
                # START can then enter ASCII deterministically.
                wire_command = (
                    f"{COMPANION_TERMINAL_STOP}\r"
                    f"{COMPANION_TERMINAL_START}\r"
                    f"{command_text}\r"
                    f"{COMPANION_TERMINAL_STOP}\r"
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
        # Ordinary repeaters and current ASCII-first Full Companions answer the
        # raw probe directly. Older or already-binary Full Companions need a
        # terminal wrapper. Its STOP/START preamble is deliberately safe in
        # either mode, so this fallback does not assume that closing the first
        # raw probe caused an observable USB disconnect.
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
            "companion_radio_full TCP terminal."
        )


def has_managed_source_cli(args: argparse.Namespace) -> bool:
    """Return whether this run can safely inspect and restore its OTA source."""
    return bool(
        getattr(args, "source_serial", None)
        or getattr(args, "source_cli_serial", None)
        or getattr(args, "source_cli_tcp", None)
    )


def read_lora_ota_participant_versions(
    controller: Controller,
    args: argparse.Namespace,
    target: TargetInfo,
) -> dict[str, int | None]:
    """Read every version that can transmit during the maintenance window."""
    versions: dict[str, int | None] = {
        "destination": (
            parse_version(target.current_version)
            if target.current_version else None
        )
    }

    try:
        controller_text, controller_version = controller.get_firmware_version()
        versions["controller"] = controller_version
        print(f"[rxps] controller version {controller_text}")
    except TransmissionStopped:
        raise
    except OtaError as exc:
        versions["controller"] = None
        print(f"[rxps] controller version unavailable: {exc}")

    if getattr(args, "source_shares_controller", False):
        versions["source"] = versions["controller"]
        print("[rxps] source version is the shared controller version")
    elif getattr(args, "source_already_temp", False) and not (
        getattr(args, "source_cli_serial", None)
        or getattr(args, "source_cli_tcp", None)
    ):
        versions["source"] = None
        print("[rxps] source version unavailable (--source-already-temp)")
    else:
        try:
            source_reply = source_cli_command(args, "ver")
            source_version = extract_reply_version(source_reply)
            versions["source"] = source_version
            if source_version is None:
                print("[rxps] source returned no parseable version")
        except TransmissionStopped:
            raise
        except OtaError as exc:
            versions["source"] = None
            print(f"[rxps] source version unavailable: {exc}")

    for relay_name, relay_password in getattr(args, "relay_values", []):
        label = f"relay:{relay_name}"
        try:
            reply = controller.remote_command(
                relay_name, "ver", password=relay_password
            )
            versions[label] = extract_reply_version(reply)
            if versions[label] is None:
                print(f"[rxps] {relay_name} returned no parseable version")
        except TransmissionStopped:
            raise
        except OtaError as exc:
            versions[label] = None
            print(f"[rxps] {relay_name} version unavailable: {exc}")

    for label, version in versions.items():
        rendered = format_version(version) if version is not None else "unknown"
        print(f"[rxps] participant {label}={rendered}")
    return versions


def participants_support_adaptive_preamble(
    versions: dict[str, int | None],
) -> bool:
    return bool(versions) and all(
        version is not None
        and version >= RXPS_ADAPTIVE_PREAMBLE_MIN_VERSION
        for version in versions.values()
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
    READY_PATTERN = re.compile(r"(?mi)^\s*\[dev\]\s+COUNT\s*->\s*\d+\b")
    ATTACH_ERROR_PATTERN = re.compile(
        r"(?mi)^\s*\[dev\].*\b(?:ERR|ERROR)\b|"
        r"folder\s+(?:is\s+)?already\s+(?:owned|attached)|"
        r"could not (?:attach|enter).*folder"
    )

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
        self._wait_until_attached()
        print("[seeder] running (device COUNT confirmed)")

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

    def _wait_until_attached(self) -> None:
        deadline = time.monotonic() + self.args.seeder_start_wait
        while True:
            self.ensure_running("during startup")
            detail = self._log_tail()
            if self.ATTACH_ERROR_PATTERN.search(detail):
                raise OtaError(
                    "motatool seeder was rejected by the device while "
                    f"attaching:\n{detail}"
                )
            if self.READY_PATTERN.search(detail):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OtaError(
                    "motatool seeder did not receive the device COUNT "
                    f"acknowledgement within {self.args.seeder_start_wait:g}s:\n"
                    f"{detail}"
                )
            time.sleep(min(0.1, remaining))

    def payload_read_progress(self, package: MotaInfo) -> tuple[int, int, int]:
        """Return unique, total, and aggregate payload-block host reads.

        ``motatool serve -v`` logs each successful random-access read. A read
        at a payload block boundary means the source has reached that block's
        response job. This is a passive progress signal: unlike remote
        ``ota status``, reading it consumes no LoRa airtime and cannot collide
        with the transfer it is observing.
        """
        if self.log_file is not None and not self.log_file.closed:
            self.log_file.flush()
        try:
            detail = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0, math.ceil(package.payload_size / package.block_size)
        offsets = [
            int(match)
            for match in re.findall(r"\bREAD\s+\d+\s+@(\d+)\s+OK\b", detail)
        ]
        total = math.ceil(package.payload_size / package.block_size)
        boundaries = {
            package.payload_offset + index * package.block_size
            for index in range(total)
        }
        payload_reads = [offset for offset in offsets if offset in boundaries]
        return len(set(payload_reads)), total, len(payload_reads)

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


def parse_rxps_settings(reply: str, label: str) -> RxpsSettings:
    detailed = re.fullmatch(
        r"\s*>?\s*(?:radio\.rxps(?:\.config)?\s+)?(on|off),level=(\d+),"
        r"preamble=(\d+),rx=(\d+),sleep=(\d+)\s*",
        reply,
        re.IGNORECASE,
    )
    if detailed is not None:
        level = int(detailed.group(2))
        preamble = int(detailed.group(3))
        rx_us = int(detailed.group(4))
        sleep_us = int(detailed.group(5))
        if (
            level > 10
            or preamble not in (0, 16, 32)
            or not RXPS_MIN_PERIOD_US <= rx_us <= RXPS_MAX_PERIOD_US
            or not RXPS_MIN_PERIOD_US <= sleep_us <= RXPS_MAX_PERIOD_US
        ):
            raise OtaError(f"could not read {label} RXPS config: {reply}")
        return RxpsSettings(
            enabled=detailed.group(1).lower() == "on",
            rx_us=rx_us,
            sleep_us=sleep_us,
            level=level,
            preamble=preamble,
        )

    match = re.fullmatch(
        r"\s*>?\s*(?:radio\.rxps\s+)?(on|off),(\d+),(\d+)\s*",
        reply,
        re.IGNORECASE,
    )
    if match is None:
        raise OtaError(f"could not read {label} RXPS state: {reply}")
    rx_us = int(match.group(2))
    sleep_us = int(match.group(3))
    if (
        not RXPS_MIN_PERIOD_US <= rx_us <= RXPS_MAX_PERIOD_US
        or not RXPS_MIN_PERIOD_US <= sleep_us <= RXPS_MAX_PERIOD_US
    ):
        raise OtaError(f"could not read {label} RXPS state: {reply}")
    return RxpsSettings(
        enabled=match.group(1).lower() == "on",
        rx_us=rx_us,
        sleep_us=sleep_us,
    )


def read_remote_rxps(
    controller: Controller,
    target_name: str,
    *,
    password: str | None = None,
) -> RxpsSettings:
    try:
        reply = controller.remote_command(
            target_name, "get radio.rxps.config", password=password
        )
        return parse_rxps_settings(reply, target_name)
    except TransmissionStopped:
        raise
    except OtaError as detailed_error:
        # Old firmware does not expose the saved level metadata. Query the
        # legacy form explicitly so its fixed periods can still be preserved.
        try:
            reply = controller.remote_command(
                target_name, "get radio.rxps", password=password
            )
            return parse_rxps_settings(reply, target_name)
        except TransmissionStopped:
            raise
        except OtaError as legacy_error:
            raise OtaError(
                f"could not read {target_name} RXPS state using current or "
                f"legacy CLI: {legacy_error}"
            ) from detailed_error


def read_source_rxps(args: argparse.Namespace) -> RxpsSettings:
    """Read the managed source's persisted RXPS preference exactly."""
    try:
        reply = source_cli_command(args, "get radio.rxps.config")
        return parse_rxps_settings(reply, "OTA source")
    except TransmissionStopped:
        raise
    except OtaError as detailed_error:
        # Older full-parser sources expose only the fixed receive/sleep
        # periods. They are still sufficient for an exact legacy restore.
        try:
            reply = source_cli_command(args, "get radio.rxps")
            return parse_rxps_settings(reply, "OTA source")
        except TransmissionStopped:
            raise
        except OtaError as legacy_error:
            raise OtaError(
                "could not read OTA source RXPS state using current or "
                f"legacy CLI: {legacy_error}"
            ) from detailed_error


def source_rxps_busy_retry_delay(retry_number: int) -> float:
    """Return a unique short cadence for each retry in the bounded window."""
    # 73 and 173 are coprime, so none of the 32 delays repeat. Their sum is
    # 9.366 seconds, and the varying 210-378 ms spacing avoids phase-locking
    # retries to a periodic RX/TX scheduler slot.
    return (210 + (((retry_number - 1) * 73) % 173)) / 1000


def mutate_source_rxps(args: argparse.Namespace, command: str) -> str:
    """Run one idempotent source RXPS mutation across short busy phases."""
    busy_retries = 0
    while True:
        try:
            return source_cli_command(args, command)
        except TransmissionStopped:
            raise
        except OtaError as exc:
            # The Full Companion emits this exact response only when the
            # driver rejected the change before updating or saving the RXPS
            # preference. It is therefore safe to replay an idempotent RXPS
            # mutation, unlike an arbitrary rejected or reply-lost command.
            busy = re.search(
                r"\bradio busy;\s*retry\b", str(exc), re.IGNORECASE
            )
            if busy is None:
                raise
            if busy_retries >= SOURCE_RXPS_BUSY_RETRY_LIMIT:
                raise OtaError(
                    "OTA source remained radio busy after "
                    f"{SOURCE_RXPS_BUSY_RETRY_LIMIT} bounded RXPS retries "
                    "and about 9.4 seconds of retry waits (command round-trip "
                    "time is additional)"
                ) from exc
            busy_retries += 1
            delay = source_rxps_busy_retry_delay(busy_retries)
            print(
                "[rxps] OTA source radio busy; retrying RXPS mutation in "
                f"{delay:.2f}s ({busy_retries}/{SOURCE_RXPS_BUSY_RETRY_LIMIT})"
            )
            time.sleep(delay)


def disable_source_rxps(
    args: argparse.Namespace,
    saved: RxpsSettings,
) -> bool:
    """Disable RXPS for a managed source and prove continuous receive mode."""
    if not saved.enabled:
        print("[rxps] OTA source was already off; leaving it unchanged")
        return False

    reply = mutate_source_rxps(args, "set radio.rxps off")
    if re.search(r"\boff\b", reply, re.IGNORECASE) is None:
        raise OtaError(f"OTA source did not confirm RXPS off: {reply}")
    verified = read_source_rxps(args)
    if verified.enabled:
        raise OtaError("OTA source RXPS did not read back as off")
    print("[rxps] OTA source temporarily off for TempRadio transfer")
    return True


def rxps_restore_command(saved: RxpsSettings) -> str:
    if saved.level is not None and 1 <= saved.level <= 10:
        if saved.preamble in (16, 32):
            return (
                f"set radio.rxps level {saved.level} "
                f"preamble {saved.preamble}"
            )
        return f"set radio.rxps level {saved.level}"
    return f"set radio.rxps {saved.rx_us} {saved.sleep_us}"


def restore_source_rxps(
    args: argparse.Namespace,
    saved: RxpsSettings,
) -> None:
    """Restore and verify a managed source's exact saved RXPS preference."""
    current = read_source_rxps(args)
    if saved.enabled:
        if current != saved:
            reply = mutate_source_rxps(args, rxps_restore_command(saved))
            if re.search(r"\bon\b", reply, re.IGNORECASE) is None:
                raise OtaError(f"OTA source did not restore RXPS: {reply}")
    elif current.enabled:
        reply = mutate_source_rxps(args, "set radio.rxps off")
        if re.search(r"\boff\b", reply, re.IGNORECASE) is None:
            raise OtaError(f"OTA source did not restore RXPS-off: {reply}")

    verified = read_source_rxps(args)
    if verified != saved:
        raise OtaError("OTA source RXPS settings did not restore exactly")
    print("[rxps] OTA source original RXPS settings restored")


def source_rxps_connection(args: argparse.Namespace) -> dict[str, object]:
    """Describe the managed CLI without retaining an admin credential."""
    serial_port = getattr(args, "source_cli_serial", None) or getattr(
        args, "source_serial", None
    )
    tcp_console = getattr(args, "source_cli_tcp", None)
    if serial_port:
        return {
            "kind": "serial",
            "endpoint": str(serial_port),
            "baud": getattr(args, "source_baud", None),
        }
    if tcp_console:
        return {
            "kind": "tcp-console",
            "endpoint": str(tcp_console),
        }
    raise OtaError("cannot preserve source RXPS without a managed source CLI")


def source_rxps_recovery_payload(
    args: argparse.Namespace,
    saved: RxpsSettings,
) -> dict[str, object]:
    return {
        "connection": source_rxps_connection(args),
        "rxps_enabled": saved.enabled,
        "rxps_rx_us": saved.rx_us,
        "rxps_sleep_us": saved.sleep_us,
        "rxps_level": saved.level,
        "rxps_preamble": saved.preamble,
        "restore_command": (
            rxps_restore_command(saved)
            if saved.enabled else "set radio.rxps off"
        ),
    }


def fsync_parent_directory(path: Path) -> None:
    """Make a preceding replace/unlink durable on filesystems that support it."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(path.parent, flags)
    except OSError as exc:
        raise OtaError(
            f"cannot open recovery directory for a durable update: {path.parent}: {exc}"
        ) from exc
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        raise OtaError(
            f"cannot flush recovery directory update: {path.parent}: {exc}"
        ) from exc
    finally:
        os.close(directory_fd)


def write_private_recovery_file(path: Path, contents: str) -> Path:
    """Atomically install a private recovery file after flushing its contents."""
    if path.is_symlink():
        raise OtaError(f"recovery path is a symbolic link: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "w", encoding="ascii", newline="\n") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        fsync_parent_directory(path)
    except BaseException:
        try:
            os.close(temporary_fd)
        except OSError:
            pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return path


def retire_private_recovery_file(path: Path, label: str) -> None:
    """Atomically remove an active private record after exact restoration."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        raise OtaError(f"{label} path is a symbolic link: {path}")
    retired = path.with_name(
        f".{path.name}.restored-{os.getpid()}-{secrets.token_hex(4)}"
    )
    try:
        os.replace(path, retired)
        fsync_parent_directory(path)
        retired.unlink()
        fsync_parent_directory(path)
    except OSError as exc:
        raise OtaError(f"cannot retire {label} {path}: {exc}") from exc


def retire_source_rxps_recovery(path: Path) -> None:
    """Atomically remove an active source RXPS record after restoration."""
    retire_private_recovery_file(path, "source RXPS recovery record")


def write_source_rxps_recovery(
    work_dir: Path,
    args: argparse.Namespace,
    saved: RxpsSettings,
    *,
    recovery_path: Path | None = None,
) -> Path:
    """Persist a non-secret, exact source RXPS restore record atomically."""
    path = recovery_path or (work_dir / SOURCE_RXPS_RECOVERY_FILE)
    if path.is_symlink():
        raise OtaError(f"source RXPS recovery path is a symbolic link: {path}")
    path = path.resolve()
    payload = source_rxps_recovery_payload(args, saved)
    return write_private_recovery_file(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def read_source_rxps_recovery(
    path: Path,
    args: argparse.Namespace,
) -> RxpsSettings:
    """Load an exact saved preference only for the same managed source."""
    if path.is_symlink():
        raise OtaError(f"source RXPS recovery path is a symbolic link: {path}")
    path = path.resolve()
    if not path.is_file():
        raise OtaError(f"source RXPS recovery file is unavailable: {path}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise OtaError(f"source RXPS recovery file is not private (0600): {path}")
    try:
        payload = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OtaError(f"cannot read source RXPS recovery file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise OtaError(f"invalid source RXPS recovery record: {path}")
    if payload.get("connection") != source_rxps_connection(args):
        raise OtaError(
            "source RXPS recovery record belongs to a different CLI endpoint: "
            f"{path}"
        )
    enabled = payload.get("rxps_enabled")
    rx_us = payload.get("rxps_rx_us")
    sleep_us = payload.get("rxps_sleep_us")
    level = payload.get("rxps_level")
    preamble = payload.get("rxps_preamble")
    if (
        type(enabled) is not bool
        or type(rx_us) is not int
        or type(sleep_us) is not int
        or not RXPS_MIN_PERIOD_US <= rx_us <= RXPS_MAX_PERIOD_US
        or not RXPS_MIN_PERIOD_US <= sleep_us <= RXPS_MAX_PERIOD_US
        or (level is not None and (type(level) is not int or not 0 <= level <= 10))
        or (preamble is not None and preamble not in (0, 16, 32))
    ):
        raise OtaError(f"invalid source RXPS values in recovery record: {path}")
    saved = RxpsSettings(enabled, rx_us, sleep_us, level, preamble)
    expected_command = (
        rxps_restore_command(saved) if saved.enabled else "set radio.rxps off"
    )
    if payload.get("restore_command") != expected_command:
        raise OtaError(f"source RXPS recovery command is inconsistent: {path}")
    return saved


def write_target_rxps_recovery(
    work_dir: Path,
    target_name: str,
    saved: RxpsSettings,
    profile: RxpsTempProfile | None,
    participant_versions: dict[str, int | None],
) -> Path:
    path = work_dir / TARGET_RXPS_RECOVERY_FILE
    payload = {
        "target": target_name,
        "rxps_enabled": saved.enabled,
        "rxps_rx_us": saved.rx_us,
        "rxps_sleep_us": saved.sleep_us,
        "rxps_level": saved.level,
        "rxps_preamble": saved.preamble,
        "temporary_policy": (
            "preserve saved level-based preference"
            if profile else "set radio.rxps off"
        ),
        "participant_versions": {
            label: format_version(version) if version is not None else None
            for label, version in participant_versions.items()
        },
    }
    return write_private_recovery_file(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def apply_remote_rxps_policy(
    controller: Controller,
    target_name: str,
    saved: RxpsSettings,
    profile: RxpsTempProfile | None,
    *,
    password: str | None = None,
) -> bool:
    if not saved.enabled:
        print(f"[rxps] {target_name} was already off; leaving it off")
        return False

    if profile is not None and saved.level is not None and 1 <= saved.level <= 10:
        current = read_remote_rxps(
            controller, target_name, password=password
        )
        preference_matches = (
            current.enabled
            and current.level == saved.level
            and current.preamble == saved.preamble
        )
        if not preference_matches:
            if saved.preamble in (16, 32):
                command = (
                    f"set radio.rxps level {saved.level} "
                    f"preamble {saved.preamble}"
                )
            else:
                command = f"set radio.rxps level {saved.level}"
            reply = controller.remote_command(
                target_name, command, password=password
            )
            if re.search(r"\bon\b", reply, re.IGNORECASE) is None:
                raise OtaError(
                    f"{target_name} did not restore its saved RXPS "
                    f"preference: {reply}"
                )
            current = read_remote_rxps(
                controller, target_name, password=password
            )
            if (
                not current.enabled
                or current.level != saved.level
                or current.preamble != saved.preamble
            ):
                raise OtaError(
                    f"{target_name} saved RXPS preference did not read back"
                )
        print(
            f"[rxps] {target_name} saved preference preserved; qualified "
            f"boundary level {profile.boundary_level}, preamble "
            f"{profile.boundary_preamble}"
        )
        return False

    command = "set radio.rxps off"
    reply = controller.remote_command(
        target_name, command, password=password
    )
    expected_enabled = False
    expected_word = "on" if expected_enabled else "off"
    if re.search(rf"\b{expected_word}\b", reply, re.IGNORECASE) is None:
        raise OtaError(
            f"{target_name} did not apply temporary RXPS policy: {reply}"
        )
    verified = read_remote_rxps(
        controller, target_name, password=password
    )
    if verified.enabled != expected_enabled:
        raise OtaError(
            f"{target_name} RXPS policy did not read back as {expected_word}"
        )
    print(f"[rxps] {target_name} temporarily off for this version/tuple")
    return True


def restore_remote_rxps(
    controller: Controller,
    target_name: str,
    saved: RxpsSettings,
    *,
    password: str | None = None,
) -> None:
    current = read_remote_rxps(
        controller, target_name, password=password
    )
    if saved.enabled:
        if current != saved:
            command = rxps_restore_command(saved)
            reply = controller.remote_command(
                target_name,
                command,
                password=password,
            )
            if re.search(r"\bon\b", reply, re.IGNORECASE) is None:
                raise OtaError(f"{target_name} did not restore RXPS: {reply}")
    elif current.enabled:
        reply = controller.remote_command(
            target_name, "set radio.rxps off", password=password
        )
        if re.search(r"\boff\b", reply, re.IGNORECASE) is None:
            raise OtaError(f"{target_name} did not restore RXPS-off: {reply}")

    verified = read_remote_rxps(
        controller, target_name, password=password
    )
    metadata_matches = (
        saved.level is None
        or (
            verified.level == saved.level
            and verified.preamble == saved.preamble
        )
    )
    if (
        verified.enabled != saved.enabled
        or verified.rx_us != saved.rx_us
        or verified.sleep_us != saved.sleep_us
        or not metadata_matches
    ):
        raise OtaError(f"{target_name} RXPS settings did not restore exactly")
    print(f"[rxps] {target_name} original RXPS settings restored")


def parse_delay_reply(reply: str, label: str) -> float:
    match = re.fullmatch(r"\s*>\s*([0-9]+(?:\.[0-9]+)?)\s*", reply)
    if match is None:
        raise OtaError(f"could not read {label}: {reply}")
    return float(match.group(1))


def read_relay_timing(
    controller: Controller,
    relay_name: str,
    relay_password: str,
) -> RelayTimingSettings:
    rxdelay = parse_delay_reply(
        controller.remote_command(
            relay_name, "get rxdelay", password=relay_password
        ),
        f"{relay_name} rxdelay",
    )
    txdelay = parse_delay_reply(
        controller.remote_command(
            relay_name, "get txdelay", password=relay_password
        ),
        f"{relay_name} txdelay",
    )
    return RelayTimingSettings(
        name=relay_name,
        password=relay_password,
        rxdelay=rxdelay,
        txdelay=txdelay,
    )


def write_relay_timing_recovery(
    work_dir: Path,
    settings: list[RelayTimingSettings],
) -> Path:
    path = work_dir / RELAY_TIMING_RECOVERY_FILE
    payload = [
        {
            "name": item.name,
            "rxdelay": item.rxdelay,
            "txdelay": item.txdelay,
        }
        for item in settings
    ]
    return write_private_recovery_file(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def enforce_relay_timing(
    controller: Controller,
    saved: RelayTimingSettings,
    txdelay: float,
) -> None:
    if abs(saved.rxdelay) > 0.0001:
        reply = controller.remote_command(
            saved.name, "set rxdelay 0", password=saved.password
        )
        if not reply.upper().startswith("OK"):
            raise OtaError(f"{saved.name} did not disable rxdelay: {reply}")
    if abs(saved.txdelay - txdelay) > 0.0001:
        reply = controller.remote_command(
            saved.name,
            f"set txdelay {format_decimal(txdelay)}",
            password=saved.password,
        )
        if not reply.upper().startswith("OK"):
            raise OtaError(f"{saved.name} did not accept OTA txdelay: {reply}")

    verified = read_relay_timing(controller, saved.name, saved.password)
    if abs(verified.rxdelay) > 0.0001 or abs(verified.txdelay - txdelay) > 0.0001:
        raise OtaError(
            f"{saved.name} transfer timing did not read back as "
            f"rxdelay=0, txdelay={format_decimal(txdelay)}"
        )
    print(
        f"[relays] {saved.name} verified at rxdelay 0, "
        f"txdelay {format_decimal(txdelay)}"
    )


def restore_relay_timings(
    controller: Controller,
    settings: list[RelayTimingSettings],
) -> None:
    errors: list[str] = []
    for saved in reversed(settings):
        try:
            current = read_relay_timing(controller, saved.name, saved.password)
            if abs(current.rxdelay - saved.rxdelay) > 0.0001:
                reply = controller.remote_command(
                    saved.name,
                    f"set rxdelay {format_decimal(saved.rxdelay)}",
                    password=saved.password,
                )
                if not reply.upper().startswith("OK"):
                    raise OtaError(f"did not restore rxdelay: {reply}")
            if abs(current.txdelay - saved.txdelay) > 0.0001:
                reply = controller.remote_command(
                    saved.name,
                    f"set txdelay {format_decimal(saved.txdelay)}",
                    password=saved.password,
                )
                if not reply.upper().startswith("OK"):
                    raise OtaError(f"did not restore txdelay: {reply}")
            verified = read_relay_timing(controller, saved.name, saved.password)
            if (
                abs(verified.rxdelay - saved.rxdelay) > 0.0001
                or abs(verified.txdelay - saved.txdelay) > 0.0001
            ):
                raise OtaError("relay timing did not restore exactly")
            print(f"[relays] {saved.name} original rxdelay/txdelay restored")
        except (OtaError, OSError) as exc:
            errors.append(f"{saved.name}: {exc}")
    if errors:
        raise OtaError("could not restore relay timings: " + "; ".join(errors))


def confirm_update(
    args: argparse.Namespace,
    target: TargetInfo,
    package: MotaInfo,
) -> None:
    print("\nValidated update plan:")
    print(f"  destination : {target.name} ({target.target_id:08X}, {target.platform})")
    print(f"  running base: {target.base_hash.hex().upper()}")
    if target.platform == "nrf52":
        version = target.bootloader_version or "unknown"
        print(
            f"  bootloader  : {version} (ABI {target.bootloader_abi}, "
            f"codecs 0x{target.bootloader_codecs:X}; ready)"
        )
    else:
        print("  bootloader  : not required")
    print(f"  update      : {package.version} {package.kind} hw={package.hw_id or '?'}")
    print(f"  mOTA id     : {package.manifest_id}")
    print(f"  TempRadio   : {args.temp_radio}")
    saved_rxps = getattr(args, "target_rxps_saved", None)
    rxps_profile = getattr(args, "target_rxps_profile", None)
    if isinstance(saved_rxps, RxpsSettings) and saved_rxps.enabled:
        if isinstance(rxps_profile, RxpsTempProfile):
            print(
                "  RXPS        : preserve saved level-based preference; "
                f"qualified boundary level {rxps_profile.boundary_level}, "
                f"preamble {rxps_profile.boundary_preamble}"
            )
        else:
            print(
                "  RXPS        : temporarily off (version/preamble gate); "
                "restore original settings"
            )
    elif isinstance(saved_rxps, RxpsSettings):
        print("  RXPS        : already off; unchanged")
    if args.relay:
        print(
            f"  relay timing: rxdelay 0, "
            f"txdelay {format_decimal(args.relay_txdelay)} (saved/restored)"
        )
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


def wait_for_completed_manifest_verification(
    controller: Controller,
    args: argparse.Namespace,
    manifest_id: str,
    status: str,
    seeder: SeederProcess | None,
) -> str:
    """Bound a retained store's transient boot-time verification state."""
    if "verifying staged blocks" not in status.lower():
        return status

    deadline = time.monotonic() + args.discovery_timeout
    last_status = status
    while True:
        ensure_seeder_running(
            seeder, "while waiting for a completed previous manifest"
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OtaError(
                f"timed out waiting for previous mOTA {manifest_id} staged "
                f"block verification: {last_status}"
            )
        time.sleep(min(float(args.discovery_interval), remaining))
        ensure_seeder_running(
            seeder, "while waiting for a completed previous manifest"
        )
        try:
            last_status = controller.remote_command(
                args.target, "ota status", retry=False
            )
        except TransmissionError as exc:
            print(
                "[download] previous staged-block verification status reply "
                f"was lost; retrying within the discovery window: {exc}"
            )
            continue

        lowered = last_status.lower()
        active_id = download_manifest_id(last_status)
        if active_id is not None and active_id != manifest_id:
            raise OtaError(
                f"previous mOTA changed during staged-block verification: "
                f"expected {manifest_id}, got {active_id}; "
                f"status: {last_status}"
            )
        if "no download" in lowered:
            return last_status
        if active_id is None:
            raise OtaError(
                f"previous mOTA changed during staged-block verification: "
                f"expected {manifest_id}, got unknown; status: {last_status}"
            )
        if "download: failed" in lowered:
            raise OtaError(
                f"previous mOTA {manifest_id} failed staged-block "
                f"verification: {last_status}"
            )
        if "ready to install" in lowered:
            return last_status
        if "verifying staged blocks" not in lowered:
            raise OtaError(
                f"previous mOTA {manifest_id} became incomplete while "
                f"verifying staged blocks: {last_status}"
            )


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
            status = wait_for_completed_manifest_verification(
                controller, args, active_id, status, seeder
            )
            manager_is_idle = "no download" in status.lower()
            if not manager_is_idle and "ready to install" not in status.lower():
                raise OtaError(
                    f"refusing to clear previous mOTA {active_id} because "
                    f"it is not complete: {status}"
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
            if manager_is_idle:
                print(
                    f"[download] previous session {active_id} became idle after "
                    f"proving running body {expected_hash}; persistent erasure "
                    "is not inferred and no IDLE cancel was sent"
                )
            else:
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
                            "completed-manifest cancel outcome is ambiguous: "
                            f"{resolved}"
                        ) from cancel_error
                else:
                    if not cancel_reply.startswith("OK"):
                        raise OtaError(
                            "could not clear completed previous mOTA: "
                            f"{cancel_reply}"
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
                    f"[download] detached completed previous session {active_id} "
                    f"after proving running body {expected_hash}; persistent "
                    "erasure is not inferred"
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
        ensure_seeder_running(seeder, "during discovery")
        try:
            last_reply = controller.remote_command(
                args.target, "ota ls", retry=False
            )
        except TransmissionError as exc:
            # `ota ls` is a discovery broadcast followed by an ordinary admin
            # reply. On a busy half-duplex TempRadio channel that reply can be
            # lost even though discovery was sent. Do not rebroadcast it in the
            # generic retry loop; the exact pull below is safe, and its existing
            # status reconciliation proves whether a lost pull reply started the
            # requested manifest.
            last_reply = f"reply lost: {exc}"
            print(
                "[download] `ota ls` reply was lost; attempting the exact "
                f"manifest {package.manifest_id}"
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

            if pull_reply.startswith(("OK pulling", "OK resuming")):
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
    # A status command is ordinary half-duplex LoRa traffic. Asking immediately
    # after `ota pull` can occupy the link for a full reply timeout and was
    # measured adding tens of seconds to an otherwise short transfer. First
    # watch the local seeder log, which is airtime-free, and query only after
    # every payload block has reached the source (plus a bounded final-packet
    # drain). If verbose progress is unavailable, fall back to an airtime-based
    # quiet window before the first query.
    first_wait = initial_status_wait_seconds(args, package)
    ensure_seeder_running(seeder, "before first transfer status check")
    if seeder is not None and hasattr(seeder, "payload_read_progress"):
        stall_wait = passive_progress_stall_seconds(args, package)
        tail_wait = transfer_tail_guard_seconds(args)
        last_seen = -1
        last_activity = -1
        last_progress = time.monotonic()
        last_status_activity = -1
        last_reported_quarter = -1
        while time.monotonic() < deadline:
            ensure_seeder_running(seeder, "before first transfer status check")
            seen, total, activity = seeder.payload_read_progress(package)
            now = time.monotonic()
            if activity > last_activity:
                last_activity = activity
                last_progress = now
            if seen > last_seen:
                last_seen = seen
                quarter = (seen * 4 // total) if total else 0
                if seen and quarter > last_reported_quarter:
                    print(f"[download] passive source progress {seen}/{total}")
                    last_reported_quarter = quarter
            quiet_for = now - last_progress
            query_reason: str | None = None
            if (
                total > 0
                and seen >= total
                and activity > last_status_activity
                and quiet_for >= tail_wait
            ):
                query_reason = (
                    f"source read all {total} payload blocks and has been quiet "
                    f"for {quiet_for:.1f}s"
                )
            elif quiet_for >= stall_wait:
                query_reason = f"no new source block read for {quiet_for:.0f}s"

            if query_reason is not None:
                print(
                    f"[download] {query_reason}; checking destination status"
                )
                status = remote_command_with_seeder(
                    controller, args.target, "ota status", seeder,
                    "during transfer",
                )
                require_package_session(status, package)
                if "ready to install" in status.lower():
                    return status
                # A status exchange can overlap a useful retry read. Snapshot
                # it on the next loop and remain passive until that activity
                # drains. If nothing else moves, use the radio-scaled stall
                # interval before the next diagnostic query.
                last_status_activity = activity
                last_progress = time.monotonic()
                continue
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        raise OtaError(
            "transfer timeout; the partial download remains staged and can "
            "resume when the same mOTA is served again"
        )

    time.sleep(min(first_wait, max(0.0, deadline - time.monotonic())))
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
            shared_controller = bool(
                getattr(args, "source_shares_controller", False)
            )
            found_on_temp = False
            try:
                switch_controller_to_temp_radio(
                    controller, args, command, temp_radio
                )
                if shared_controller:
                    time.sleep(TEMP_RADIO_SWITCH_DELAY_SECONDS)
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
                # Always reassert the saved Binary tuple, even if ending the
                # shared source's local live override fails. The caller owns a
                # conservative source/target cleanup flag before entering this
                # helper, so either failure is retried by outer cleanup.
                try:
                    if shared_controller:
                        # The shared Full Companion entered TempRadio through
                        # its bounded local command. Leave it through that same
                        # path without ever persisting the temporary tuple.
                        shorten_source_temp_window(args)
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

            # The 1.5-second scheduled handoff is long past by the time the
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
    relay_values: list[tuple[str, str]] | None = None,
) -> None:
    values = args.relay_values if relay_values is None else relay_values
    if not values:
        return
    command = temp_radio_command_for_minutes(args, TEMP_RADIO_RETURN_MINUTES)
    print(
        f"[relays] scheduling return to the normal channel in "
        f"{TEMP_RADIO_RETURN_MINUTES} minute"
    )
    for relay_name, relay_password in values:
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
    shares_controller = getattr(args, "source_shares_controller", False)
    label = "shared Full Companion" if shares_controller else "managed OTA source"
    print(f"[source] ending {label} TempRadio before restore")

    # Current full-parser sources can cancel the lease immediately. For a
    # separate legacy source, a rejected `normalradio` falls back to its
    # bounded one-minute lease below. A shared source cannot use that fallback:
    # its normal tuple must be proven before the Binary controller is restored.
    output = source_cli_command(
        args,
        "normalradio",
        check=check if shares_controller else False,
    )
    if output:
        deadline = time.monotonic() + SHARED_SOURCE_NORMAL_TIMEOUT_SECONDS
        while True:
            status = source_cli_command(
                args,
                "tempradio",
                check=check if shares_controller else False,
            )
            lowered = status.strip().lower()
            if lowered.startswith("tempradio inactive"):
                print(f"[source] {label} TempRadio is inactive")
                return True
            if not lowered.startswith(("tempradio active:", "tempradio pending:")):
                if not shares_controller:
                    break
                error = OtaError(
                    "shared Full Companion returned an unexpected TempRadio "
                    f"status after normalradio: {status or 'no output'}"
                )
                if check:
                    raise error
                print(f"[warn] {error}", file=sys.stderr)
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = OtaError(
                    f"{label} did not leave TempRadio within "
                    f"{SHARED_SOURCE_NORMAL_TIMEOUT_SECONDS} seconds"
                )
                if check:
                    raise error
                print(f"[warn] {error}", file=sys.stderr)
                return False
            time.sleep(min(SHARED_SOURCE_NORMAL_POLL_SECONDS, remaining))
    elif shares_controller:
        print(
            "[warn] could not end the shared source TempRadio window; "
            "end it with `normalradio` before restoring the controller",
            file=sys.stderr,
        )
        return False

    # Compatibility for older separate source CLIs without `normalradio` or a
    # TempRadio status query. Re-arm the same tuple for one minute and wait out
    # the complete bounded lease before reporting that RXPS may be restored.
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
    time.sleep(
        TEMP_RADIO_RETURN_MINUTES * 60 + TEMP_RADIO_RETURN_MARGIN_SECONDS
    )
    print("[source] managed OTA source bounded TempRadio lease expired")
    return True


def switch_controller_to_temp_radio(
    controller: Controller,
    args: argparse.Namespace,
    temp_command: str,
    temp_radio: RadioSettings,
) -> None:
    """Enter TempRadio without overwriting a shared Companion's saved tuple."""
    if getattr(args, "source_shares_controller", False):
        # This local command changes only the bounded live radio tuple. A Binary
        # `set radio` would persist the temporary tuple, leaving `normalradio`
        # with no saved normal tuple to restore.
        source_cli_command(args, temp_command)
        return
    controller.set_radio(temp_radio, "switch controller to TempRadio")


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
        help="companion_radio_full TCP terminal for a seeder source (default port 5002)",
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
        "--relay-txdelay",
        type=float,
        default=DEFAULT_RELAY_TX_DELAY,
        help=(
            "temporary flood txdelay for managed relays; rxdelay is also "
            "temporarily set to 0 and both original values are restored"
        ),
    )
    parser.add_argument(
        "--temp-radio", default="909.950,250,5,5,120",
        help="frequency,bw,sf,cr,minutes",
    )
    parser.add_argument(
        "--base", type=Path,
        help=(
            "exact running .bin/.hex/.zip/full.mota (required to build an "
            "internal-flash nRF52 delta; optional for external SD/QSPI nRF52)"
        ),
    )
    parser.add_argument("--zip-member", help="select one exact path inside PACKAGE ZIP")
    parser.add_argument("--sign-key", type=Path, help="Ed25519 private key for a newly built mOTA")
    parser.add_argument("--public-key", type=Path, help="require this signer when verifying")
    parser.add_argument(
        "--inplace-memory",
        help="nRF52 OTAFIX workspace (auto: 0x98000 internal, 0xC6000 external SD/QSPI)",
    )
    parser.add_argument(
        "--platform", choices=("esp32", "nrf52"),
        help="destination platform for --prepare-only (detected during a live run)",
    )
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--source-rxps-recovery-file",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--meshcli", default="meshcli")
    parser.add_argument("--motatool", default="motatool")
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "show redacted motatool/meshcli commands, subprocess status, "
            "stdout, stderr, and timeouts"
        ),
    )
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
            "port-5000 identity and use its local CLI for the bounded live "
            "TempRadio override without persisting that tuple through Binary"
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
    parser.add_argument(
        "--nrf-qspi", action="store_true",
        help="offline target uses nRF52 external QSPI staging",
    )
    parser.add_argument("--target-hw", help="hardware identity for --prepare-only")
    parser.add_argument(
        "--allow-non-upgrade", action="store_true",
        help="permit reinstalling the same version or installing an older one",
    )
    return parser


def serial_paths_match(first: str, second: str) -> bool:
    """Recognize two names for one serial endpoint, including /dev symlinks."""
    try:
        if os.path.samefile(first, second):
            return True
    except OSError:
        # One or both paths may not exist yet (common in dry-run tests and after
        # a USB reset), but resolving existing symlink components still catches
        # stable by-id aliases of the same eventual device.
        pass
    return Path(first).resolve(strict=False) == Path(second).resolve(strict=False)


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    try:
        args.temp_values = parse_temp_radio(args.temp_radio)
    except argparse.ArgumentTypeError as exc:
        parser.error(f"--temp-radio: {exc}")
    if not math.isfinite(args.relay_txdelay) or not 0.0 <= args.relay_txdelay <= 2.0:
        parser.error("--relay-txdelay must be between 0 and 2")
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
        if (
            args.platform == "nrf52"
            and not (args.nrf_sd or args.nrf_qspi)
            and not args.target_base_hash
        ):
            parser.error("offline internal-flash nRF52 preparation requires --target-base-hash")
        if args.nrf_sd and args.nrf_qspi:
            parser.error("--nrf-sd and --nrf-qspi are mutually exclusive")
        if (args.nrf_sd or args.nrf_qspi) and args.platform != "nrf52":
            parser.error("--nrf-sd/--nrf-qspi require --platform nrf52")
    else:
        if any((args.platform, args.target_id, args.target_base_hash,
                args.target_hw, args.nrf_sd, args.nrf_qspi)):
            parser.error(
                "--platform, --target-id, --target-base-hash, --target-hw, and "
                "--nrf-sd/--nrf-qspi are only valid with --prepare-only"
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
        if args.source_already_temp and not args.source_tcp:
            parser.error("--source-already-temp requires --source-tcp")
        if args.source_already_temp and (
            args.source_cli_serial or args.source_cli_tcp
        ):
            parser.error(
                "--source-already-temp cannot be combined with a managed source CLI"
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
        if args.source_shares_controller and args.leave_controller_radio:
            parser.error(
                "--leave-controller-radio cannot be combined with a shared "
                "managed source because source RXPS must be restored only "
                "after its normal radio is verified"
            )
        if args.controller_serial and args.source_serial:
            if serial_paths_match(args.controller_serial, args.source_serial):
                parser.error("controller and source must be separate nodes/serial ports")
        if args.controller_serial and args.source_cli_serial:
            if serial_paths_match(args.controller_serial, args.source_cli_serial):
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
        relay_timing_seconds = (
            len(args.relay)
            * RELAY_TIMING_COMMANDS_PER_RELAY
            * args.reply_timeout
        )
        required_temp_seconds = (
            remote_setup_seconds
            + source_setup_seconds
            + TEMP_RADIO_SWITCH_DELAY_SECONDS
            + args.seeder_start_wait
            + args.discovery_timeout
            + args.transfer_timeout_minutes * 60
            + adaptive_poll_ceiling(args.poll_seconds)
            + args.reply_timeout * final_reply_count
            + relay_timing_seconds
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


def require_meshcli_version(command: str) -> tuple[int, int, int]:
    require_command(command, "meshcli")
    result = run_checked(
        [command, "-v"], label="read meshcli version", timeout=30
    )
    match = re.search(
        r"\bv(\d+)\.(\d+)\.(\d+)\b", result.stdout + result.stderr
    )
    if match is None:
        raise OtaError("could not determine meshcli version")
    version = tuple(int(part) for part in match.groups())
    if version < MIN_MESHCLI_VERSION:
        need = ".".join(str(part) for part in MIN_MESHCLI_VERSION)
        got = ".".join(str(part) for part in version)
        raise OtaError(f"meshcli {got} is too old; install {need} or newer")
    print(f"[host] meshcli {'.'.join(str(part) for part in version)}")
    return version


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
        require_meshcli_version(args.meshcli)


def make_work_dir(requested: Path | None) -> Path:
    if requested is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        requested = Path.cwd() / f"meshcore-lora-ota-{stamp}-{os.getpid()}"
    path = requested.resolve()
    path.mkdir(parents=True, exist_ok=False)
    print(f"[work] {path}")
    return path


def validate_source_recovery_location(
    recovery_path: Path | None, work_dir: Path
) -> Path | None:
    """Keep caller-owned chain recovery state outside per-attempt artifacts."""
    if recovery_path is None:
        return None
    resolved_recovery = recovery_path.resolve(strict=False)
    resolved_work = work_dir.resolve(strict=True)
    if resolved_recovery == resolved_work or resolved_recovery.is_relative_to(
        resolved_work
    ):
        raise OtaError(
            "--source-rxps-recovery-file must be outside the run work directory"
        )
    return resolved_recovery


def offline_target(args: argparse.Namespace) -> TargetInfo:
    target_id_text = args.target_id.removeprefix("0x").removeprefix("0X")
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", target_id_text):
        raise OtaError("--target-id must be exactly 8 hexadecimal characters")
    base_hash = (
        parse_hex_exact(args.target_base_hash, 8, "--target-base-hash")
        if args.target_base_hash else b"\0" * 8
    )
    return TargetInfo(
        name=args.target,
        target_id=int(target_id_text, 16),
        base_hash=base_hash,
        platform=args.platform,
        nrf_sd=args.nrf_sd,
        hw_id=args.target_hw,
        bootloader_version=None,
        bootloader_abi=None,
        bootloader_codecs=None,
        status="offline",
        self_status="offline",
        nrf_qspi=args.nrf_qspi,
    )


def main(
    argv: list[str] | None = None,
    *,
    controller_override: Controller | None = None,
) -> int:
    global DEBUG

    parser = build_parser()
    args = parser.parse_args(argv)
    DEBUG = bool(args.debug)
    validate_args(args, parser)
    work_dir: Path | None = None
    controller: Controller | None = controller_override
    original_radio: RadioSettings | None = None
    temp_radio: RadioSettings | None = None
    controller_changed = False
    seeder: SeederProcess | None = None
    seeder_attempted = False
    source_temp_owned = False
    source_rxps_saved: RxpsSettings | None = None
    source_rxps_changed = False
    source_rxps_recovery_path: Path | None = None
    target_temp_owned = False
    target_rxps_saved: RxpsSettings | None = None
    target_rxps_changed = False
    armed_relay_values: list[tuple[str, str]] = []
    relay_timing_settings: list[RelayTimingSettings] = []
    password = args.password or os.environ.get("MESHCORE_ADMIN_PASSWORD", "")
    temp_command = f"tempradio {args.temp_radio}"

    def restore_source_rxps_once(context: str) -> None:
        """Restore the source only after its ordinary radio is proven active."""
        nonlocal source_rxps_changed
        if not source_rxps_changed or source_rxps_saved is None:
            return
        if source_temp_owned:
            raise OtaError(
                f"cannot restore OTA source RXPS during {context}: its "
                "TempRadio state is still active or uncertain"
            )
        if args.source_shares_controller and controller_changed:
            raise OtaError(
                f"cannot restore OTA source RXPS during {context}: the "
                "shared controller has not returned to its normal radio"
            )
        # Keep this flag armed until exact readback succeeds. A transient
        # restore failure remains retryable from the outer finally block; the
        # restore mutation itself is deliberately idempotent.
        restore_source_rxps(args, source_rxps_saved)
        source_rxps_changed = False

    def retire_local_source_rxps_recovery() -> None:
        """A caller-supplied chain record remains owned by that caller."""
        nonlocal source_rxps_recovery_path
        if (
            source_rxps_recovery_path is None
            or args.source_rxps_recovery_file is not None
        ):
            return
        retire_source_rxps_recovery(source_rxps_recovery_path)
        source_rxps_recovery_path = None

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
        # Retain recovery state before any source mutation. A hard kill after
        # RXPS is disabled must not erase the only copy of its saved setting.
        work_dir = make_work_dir(args.work_dir)
        args.source_rxps_recovery_file = validate_source_recovery_location(
            args.source_rxps_recovery_file, work_dir
        )
        if not args.prepare_only:
            preflight_source_cli(args)
            if has_managed_source_cli(args):
                try:
                    current_source_rxps = read_source_rxps(args)
                except TransmissionStopped:
                    raise
                except OtaError as exc:
                    raise OtaError(
                        "cannot safely preserve or disable OTA source RXPS "
                        f"because its current state is unavailable: {exc}"
                    ) from exc
                source_rxps_recovery_path = (
                    args.source_rxps_recovery_file
                    if args.source_rxps_recovery_file is not None
                    else work_dir / SOURCE_RXPS_RECOVERY_FILE
                )
                if source_rxps_recovery_path.exists():
                    source_rxps_saved = read_source_rxps_recovery(
                        source_rxps_recovery_path, args
                    )
                    print(
                        "[rxps] loaded original source recovery settings: "
                        f"{source_rxps_recovery_path}"
                    )
                else:
                    source_rxps_saved = current_source_rxps
                    source_rxps_recovery_path = write_source_rxps_recovery(
                        work_dir,
                        args,
                        source_rxps_saved,
                        recovery_path=source_rxps_recovery_path,
                    )
                    print(
                        "[rxps] source recovery settings: "
                        f"{source_rxps_recovery_path}"
                    )
                # Arm cleanup before the mutating command. The command may
                # reach the source even if its acknowledgement is lost. A
                # retained record can also differ because a killed prior run
                # left the source off; restore that original at cleanup.
                source_rxps_changed = (
                    current_source_rxps != source_rxps_saved
                    or source_rxps_saved.enabled
                )
                disabled_now = disable_source_rxps(
                    args, current_source_rxps
                )
                source_rxps_changed = source_rxps_changed or disabled_now
            if controller is None:
                controller = Controller(args, password)
            verify_shared_source_identity(controller, args)
            target = query_target(controller, args)
            original_radio = controller.get_radio()
            print(f"[controller] saved radio {original_radio.meshcli_value()}")
        else:
            target = offline_target(args)

        if original_radio is not None:
            recovery_path = work_dir / "controller-radio.txt"
            write_private_recovery_file(
                recovery_path, original_radio.meshcli_value() + "\n"
            )
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
        participant_versions = read_lora_ota_participant_versions(
            controller, args, target
        )
        all_participants_support_adaptive_preamble = (
            participants_support_adaptive_preamble(
                participant_versions
            )
        )
        try:
            target_rxps_saved = read_remote_rxps(controller, args.target)
        except TransmissionStopped:
            raise
        except OtaError as exc:
            raise OtaError(
                "cannot safely preserve or disable destination RXPS because "
                f"its current state is unavailable: {exc}"
            ) from exc
        target_rxps_profile = select_rxps_temp_profile(
            target.current_version,
            args.temp_values,
            all_participants_support_adaptive_preamble=(
                all_participants_support_adaptive_preamble
            ),
        )
        if (
            target_rxps_saved is None
            or target_rxps_saved.level is None
            or not 1 <= target_rxps_saved.level <= 10
        ):
            target_rxps_profile = None
        args.target_rxps_saved = target_rxps_saved
        args.target_rxps_profile = target_rxps_profile
        confirm_update(args, target, package)
        freq, bandwidth, sf, cr, _minutes = args.temp_values
        temp_radio = RadioSettings(
            freq, bandwidth, sf, cr, original_radio.repeat
        )

        if target_rxps_saved is not None and target_rxps_saved.enabled:
            recovery_path = write_target_rxps_recovery(
                work_dir,
                args.target,
                target_rxps_saved,
                target_rxps_profile,
                participant_versions,
            )
            print(f"[rxps] recovery settings: {recovery_path}")
            # Arm cleanup before the mutating command. If the command reaches
            # the node but its confirmation is lost, restoration must still
            # run from the saved recovery record.
            target_rxps_changed = target_rxps_profile is None
            target_rxps_changed = apply_remote_rxps_policy(
                controller,
                args.target,
                target_rxps_saved,
                target_rxps_profile,
            )

        # Move far nodes first while the controller is still on the normal
        # channel. Relays are supplied in farthest-to-nearest order. A target
        # can process TempRadio even when its reply is lost, so resolve that
        # ambiguity by probing its exact identity on the temporary channel
        # instead of replaying the command from the normal channel.
        target_temp_owned = True
        if args.source_shares_controller:
            # A lost-reply probe can schedule the shared local source onto the
            # temporary tuple. Own that possible override before entering the
            # helper so outer cleanup retries any failed local restore.
            source_temp_owned = True
        arm_target_temp_radio(
            controller, args, temp_command, temp_radio, original_radio
        )
        for relay_name, relay_password in args.relay_values:
            temp_reply = controller.remote_command(
                relay_name, temp_command, password=relay_password
            )
            require_temp_radio_reply(relay_name, temp_reply)
            armed_relay_values.append((relay_name, relay_password))
        if not args.source_already_temp and not args.source_shares_controller:
            # Arm cleanup before the mutating command because the source can
            # enter TempRadio even when its acknowledgement is lost.
            source_temp_owned = True
            source_cli_command(args, temp_command)

        if args.source_shares_controller:
            # Arm cleanup before the local command. Its reply can be lost after
            # the shared physical radio has already scheduled the handoff.
            source_temp_owned = True
        switch_controller_to_temp_radio(
            controller, args, temp_command, temp_radio
        )
        # Arm Binary restoration only after the handoff succeeds. For a
        # shared Full Companion the helper deliberately uses its bounded
        # local TempRadio command so the temporary tuple is never persisted as
        # the normal radio configuration.
        controller_changed = True
        time.sleep(TEMP_RADIO_SWITCH_DELAY_SECONDS)

        for relay_name, relay_password in args.relay_values:
            saved_timing = read_relay_timing(
                controller, relay_name, relay_password
            )
            relay_timing_settings.append(saved_timing)
            recovery_path = write_relay_timing_recovery(
                work_dir, relay_timing_settings
            )
            print(f"[relays] timing recovery settings: {recovery_path}")
            enforce_relay_timing(
                controller, saved_timing, args.relay_txdelay
            )

        seeder = SeederProcess(args, package_path.parent, work_dir)
        seeder_attempted = True
        seeder.start()
        find_and_start_pull(controller, args, package, seeder)
        monitor_download(controller, args, package, seeder)

        if args.no_install:
            print(f"{args.target} is ready to install; leaving the verified update staged.")
            restore_relay_timings(controller, relay_timing_settings)
            relay_timing_settings.clear()
            if args.leave_controller_radio:
                print(
                    "[cleanup] preserving TempRadio windows because "
                    "--leave-controller-radio was requested"
                )
            else:
                shorten_target_temp_window(controller, args)
                target_temp_owned = False
                shorten_relay_temp_windows(controller, args)
                armed_relay_values.clear()
            if target_rxps_changed and target_rxps_saved is not None:
                if args.leave_controller_radio:
                    print(
                        "[rxps] destination remains off while TempRadio is "
                        "preserved; restore it from "
                        f"{TARGET_RXPS_RECOVERY_FILE} after normalradio"
                    )
                else:
                    # Restore while the controller and destination still share
                    # the temporary tuple. The destination's one-minute return
                    # window remains bounded after this exact readback.
                    restore_remote_rxps(
                        controller, args.target, target_rxps_saved
                    )
                target_rxps_changed = False
            seeder.stop()
            seeder = None
            if source_temp_owned and (
                not args.leave_controller_radio
                or not args.source_shares_controller
            ):
                if shorten_source_temp_window(args):
                    source_temp_owned = False
            if controller_changed and not args.leave_controller_radio:
                controller.set_radio(
                    original_radio, "restore controller radio after staging"
                )
                controller_changed = False
            restore_source_rxps_once("stage-only cleanup")
            retire_local_source_rxps_recovery()
            return 0

        install_confirmed = request_install(controller, args, package)
        # The install path replaces the long download lease with a bounded
        # three-minute window, then either reboots or is safely self-restoring.
        target_temp_owned = False
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
        restore_relay_timings(controller, relay_timing_settings)
        relay_timing_settings.clear()
        shorten_relay_temp_windows(controller, args)
        armed_relay_values.clear()

        # Stop seeding before returning the controller to its ordinary channel.
        seeder.stop()
        seeder = None
        if source_temp_owned and (
            not args.leave_controller_radio
            or not args.source_shares_controller
        ):
            if shorten_source_temp_window(args):
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
            if target_rxps_changed and target_rxps_saved is not None:
                restore_remote_rxps(
                    controller, args.target, target_rxps_saved
                )
                target_rxps_changed = False
        finally:
            if args.leave_controller_radio:
                controller.set_radio(
                    temp_radio, "return controller to TempRadio after verification"
                )
                controller_changed = True
        restore_source_rxps_once("successful update cleanup")
        retire_local_source_rxps_recovery()
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
            needs_remote_cleanup = (
                target_temp_owned
                or bool(armed_relay_values)
                or bool(relay_timing_settings)
            )
            if (
                needs_remote_cleanup
                and controller is not None
                and original_radio is not None
                and temp_radio is not None
            ):
                if not controller_changed:
                    try:
                        switch_controller_to_temp_radio(
                            controller, args, temp_command, temp_radio
                        )
                        controller_changed = True
                        time.sleep(TEMP_RADIO_SWITCH_DELAY_SECONDS)
                    except (OtaError, OSError) as exc:
                        print(
                            "WARNING: could not reach TempRadio to restore relay "
                            "timing or shorten remote leases; TempRadio windows "
                            f"remain bounded: {exc}",
                            file=sys.stderr,
                        )
                if controller_changed:
                    if relay_timing_settings:
                        try:
                            restore_relay_timings(controller, relay_timing_settings)
                            relay_timing_settings.clear()
                        except (OtaError, OSError) as exc:
                            print(
                                "CRITICAL: could not restore every relay's "
                                f"rxdelay/txdelay; use {RELAY_TIMING_RECOVERY_FILE}: {exc}",
                                file=sys.stderr,
                            )
                    if target_temp_owned and not args.leave_controller_radio:
                        try:
                            shorten_target_temp_window(controller, args)
                            target_temp_owned = False
                        except (OtaError, OSError) as exc:
                            print(
                                "WARNING: could not shorten the destination "
                                f"TempRadio lease: {exc}",
                                file=sys.stderr,
                            )
                    if armed_relay_values and not args.leave_controller_radio:
                        try:
                            shorten_relay_temp_windows(
                                controller, args, armed_relay_values
                            )
                            armed_relay_values.clear()
                        except (OtaError, OSError) as exc:
                            print(
                                "WARNING: could not shorten every relay TempRadio "
                                f"lease: {exc}",
                                file=sys.stderr,
                            )
                    if target_rxps_changed and target_rxps_saved is not None:
                        if args.leave_controller_radio:
                            print(
                                "WARNING: destination RXPS remains off while "
                                "TempRadio is preserved; restore it from "
                                f"{TARGET_RXPS_RECOVERY_FILE} after normalradio",
                                file=sys.stderr,
                            )
                            target_rxps_changed = False
                        else:
                            try:
                                restore_remote_rxps(
                                    controller, args.target, target_rxps_saved
                                )
                                target_rxps_changed = False
                            except (OtaError, OSError) as exc:
                                print(
                                    "WARNING: could not restore destination RXPS "
                                    "on TempRadio; retrying after radio restore: "
                                    f"{exc}",
                                    file=sys.stderr,
                                )
            if source_temp_owned and (
                not args.leave_controller_radio
                or not args.source_shares_controller
            ):
                if shorten_source_temp_window(args, check=False):
                    source_temp_owned = False
            if (
                controller is not None
                and controller_changed
                and original_radio is not None
                and not args.leave_controller_radio
                and not (
                    args.source_shares_controller and source_temp_owned
                )
            ):
                try:
                    controller.set_radio(original_radio, "restore controller radio after failure")
                    controller_changed = False
                    print("[controller] original radio restored")
                except (OtaError, OSError) as exc:
                    print(
                        "CRITICAL: could not restore the controller radio. "
                        f"Restore it manually to {original_radio.meshcli_value()}: {exc}",
                        file=sys.stderr,
                    )
            if (
                target_rxps_changed
                and target_rxps_saved is not None
                and controller is not None
                and not controller_changed
            ):
                try:
                    restore_remote_rxps(
                        controller, args.target, target_rxps_saved
                    )
                    target_rxps_changed = False
                except (OtaError, OSError) as exc:
                    print(
                        "CRITICAL: could not restore destination RXPS; use "
                        f"{TARGET_RXPS_RECOVERY_FILE}: {exc}",
                        file=sys.stderr,
                    )
            if source_rxps_changed:
                try:
                    restore_source_rxps_once("failure cleanup")
                except (OtaError, OSError) as exc:
                    recovery_hint = (
                        str(source_rxps_recovery_path)
                        if source_rxps_recovery_path is not None
                        else SOURCE_RXPS_RECOVERY_FILE
                    )
                    print(
                        "CRITICAL: could not safely restore OTA source RXPS; "
                        "leave RXPS off and return the source to its normal "
                        "radio before restoring it manually with "
                        f"{recovery_hint}: {exc}",
                        file=sys.stderr,
                    )
            if not source_rxps_changed:
                try:
                    retire_local_source_rxps_recovery()
                except (OtaError, OSError) as exc:
                    print(
                        "WARNING: source RXPS was restored, but its completed "
                        f"recovery record could not be retired: {exc}",
                        file=sys.stderr,
                    )
        if work_dir is not None:
            print(f"[work] retained at {work_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
