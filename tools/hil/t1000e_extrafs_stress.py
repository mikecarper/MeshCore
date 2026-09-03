#!/usr/bin/env python3
"""Identity-gated T1000-E ExtraFS hardware stress harness.

The default ``inspect`` scenario is read-only.  The ``comprehensive`` scenario
fills the contact table, fills internal ExtraFS, checks channel and lazy
contact add/update/remove behavior at ENOSPC, reboots, and verifies persistence.
Optional page
payload/occupancy corruption, awake-USB unsafe-reset, and cleanup phases are
separate flags.  A
serial run cannot prove event-driven sleep while USB is attached; use the BLE
sleep HIL procedure for that distinct test.

This tool never transmits a LoRa message or advertisement.  It talks only to
the USB Companion interface and compile-time ``MESHCORE_EXTRAFS_HIL`` commands.

Example destructive invocation for the dedicated spare board::

    python tools/hil/t1000e_extrafs_stress.py \
        --port COM23 --scenario comprehensive \
        --allow-destructive --confirm-usb-serial 34A9141999729D5D

Read-only repeated enumeration of a stable contact table::

    python tools/hil/t1000e_extrafs_stress.py \
        --port COM23 --scenario inspect --contact-enumeration-passes 25

All progress is written to stderr.  A single JSON result is written to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence


EXPECTED_USB_SERIAL = "34A9141999729D5D"
KNOWN_PRE_ERASE_NODE_KEY_PREFIX = "f9ad7082"
EXPECTED_USB_VID = 0x239A
EXPECTED_USB_PID = 0x8029
EXPECTED_USB_INTERFACE = "MI_00"
EXPECTED_MODEL_TOKEN = "T1000-E"
EXPECTED_STORAGE_KIB = 100
EXPECTED_CONTACTS_PER_PAGE = 25
EXPECTED_FULL_PAGE_MASK = (1 << EXPECTED_CONTACTS_PER_PAGE) - 1
# The destructive filler deliberately closes and read-verifies every 1 KiB
# extent.  Near ENOSPC that can require substantially longer than the normal
# command timeout even though the device and filesystem remain healthy.
EXTRAFS_FILL_TIMEOUT_SECONDS = 45.0
CONTACT_PAGE_REPAIR_TIMEOUT_SECONDS = 10.0
ENOSPC_REBOOT_REFUSAL_TIMEOUT_SECONDS = 30.0

HIL_STATUS_RE = re.compile(
    r"HIL ExtraFS active=(?P<active>[01]) "
    r"used=(?P<used>\d+)KiB total=(?P<total>\d+)KiB "
    r"fill=(?P<fill>\d+)"
)
HIL_FILL_RE = re.compile(
    r"HIL ExtraFS fill requested=(?P<requested>\d+) "
    r"written=(?P<written>\d+) used=(?P<used>\d+)KiB "
    r"total=(?P<total>\d+)KiB"
)
HIL_PAGE_MASK_RE = re.compile(
    r"HIL contact page (?P<page>\d+) occupied=(?P<mask>[0-9A-Fa-f]{8})"
)
HIL_CONTACT_SLOT_RE = re.compile(r"HIL contact slot=(?P<slot>\d+)")
HIL_ADVERT_SEED_RE = re.compile(r"HIL advert seeded slot=(?P<slot>\d+)")


class HilFailure(RuntimeError):
    """Raised when a safety gate or hardware invariant fails."""


class IdentityGateFailure(HilFailure):
    """Raised when an opened device is not the explicitly selected spare."""


class RebootRefused(HilFailure):
    """Raised when firmware answers a requested reboot with an error."""

    def __init__(self, event: Any) -> None:
        self.event = event
        super().__init__(
            "reboot was refused by firmware: "
            f"{getattr(event, 'payload', None)!r}"
        )


@dataclass(frozen=True)
class PortIdentity:
    device: str
    serial_number: str
    vid: int
    pid: int
    description: str
    product: str
    interface: str
    location: str
    hwid: str


@dataclass(frozen=True)
class ExtraFsStatus:
    active: bool
    used_kib: int
    total_kib: int
    filler_bytes: int


@dataclass(frozen=True)
class ExtraFsFill:
    requested_bytes: int
    written_bytes: int
    used_kib: int
    total_kib: int


@dataclass(frozen=True)
class ContactPageMask:
    page: int
    occupied_mask: int


@dataclass(frozen=True)
class ReenumerationResult:
    identity: PortIdentity
    observed_absence: bool


@dataclass(frozen=True)
class RunConfig:
    port: str
    scenario: str
    allow_destructive: bool
    confirm_usb_serial: Optional[str]
    expected_node_key_prefix: Optional[str]
    contact_count: int
    fill_bytes: int
    channel_index: int
    settle_seconds: float
    reenum_timeout: float
    allow_existing_contacts: bool
    cleanup: bool
    corrupt_page: Optional[int]
    unsafe_reset: bool
    contact_enumeration_passes: Optional[int]
    expected_contact_keyset_sha256: Optional[str]
    verbose: bool
    # Keep opt-ins at the end with safe defaults so older positional callers
    # retain their original field mapping.
    corrupt_occupied_page: Optional[int] = None
    advert_remove_rollback: bool = False
    fail_read_page: Optional[int] = None
    fail_stat_page: Optional[int] = None


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def port_identity(info: Any) -> PortIdentity:
    """Copy the useful pyserial ListPortInfo fields into stable plain data."""

    return PortIdentity(
        device=_clean(getattr(info, "device", "")),
        serial_number=_upper(getattr(info, "serial_number", "")),
        vid=int(getattr(info, "vid", 0) or 0),
        pid=int(getattr(info, "pid", 0) or 0),
        description=_clean(getattr(info, "description", "")),
        product=_clean(getattr(info, "product", "")),
        interface=_clean(getattr(info, "interface", "")),
        location=_clean(getattr(info, "location", "")),
        hwid=_clean(getattr(info, "hwid", "")),
    )


def validate_runtime_identity(
    identity: PortIdentity,
    *,
    requested_port: Optional[str] = None,
    expected_location: Optional[str] = None,
) -> None:
    """Reject anything other than the locked T1000-E Companion interface."""

    if requested_port and _upper(identity.device) != _upper(requested_port):
        raise HilFailure(
            f"resolved port {identity.device!r} does not match requested "
            f"port {requested_port!r}"
        )
    if identity.serial_number != EXPECTED_USB_SERIAL:
        raise HilFailure(
            f"USB serial is {identity.serial_number!r}, expected the locked "
            f"spare {EXPECTED_USB_SERIAL}"
        )
    if (identity.vid, identity.pid) != (EXPECTED_USB_VID, EXPECTED_USB_PID):
        raise HilFailure(
            f"USB VID:PID is {identity.vid:04X}:{identity.pid:04X}, expected "
            f"{EXPECTED_USB_VID:04X}:{EXPECTED_USB_PID:04X}"
        )
    interface_text = f"{identity.interface} {identity.hwid}".upper()
    # pyserial's Windows backend parses MI_00 but exposes it only as the
    # ``:x.0`` suffix of ``location``; unlike Linux it does not populate the
    # ``interface`` property or retain MI_00 in ``hwid``.
    is_companion_interface = (
        EXPECTED_USB_INTERFACE in interface_text
        or identity.location.lower().endswith(":x.0")
    )
    if not is_companion_interface:
        raise HilFailure(
            f"{identity.device} is not the locked {EXPECTED_USB_INTERFACE} "
            "Companion interface"
        )
    model_text = (
        f"{identity.description} {identity.product} {identity.hwid}".upper()
    )
    if "T1000" not in model_text and "8029" not in model_text:
        raise HilFailure(
            f"{identity.device} does not identify as a T1000-E runtime port"
        )
    if expected_location and identity.location != expected_location:
        raise HilFailure(
            f"USB location changed from {expected_location!r} to "
            f"{identity.location!r}; refusing to follow a different device"
        )


def parse_extrafs_status(text: str) -> ExtraFsStatus:
    match = HIL_STATUS_RE.fullmatch(text.strip())
    if not match:
        raise HilFailure(f"unexpected ExtraFS status reply: {text!r}")
    values = {name: int(value) for name, value in match.groupdict().items()}
    if values["used"] > values["total"]:
        raise HilFailure("ExtraFS reports used space greater than total space")
    return ExtraFsStatus(
        active=bool(values["active"]),
        used_kib=values["used"],
        total_kib=values["total"],
        filler_bytes=values["fill"],
    )


def parse_extrafs_fill(text: str) -> ExtraFsFill:
    match = HIL_FILL_RE.fullmatch(text.strip())
    if not match:
        raise HilFailure(f"unexpected ExtraFS fill reply: {text!r}")
    values = {name: int(value) for name, value in match.groupdict().items()}
    if values["written"] > values["requested"]:
        raise HilFailure("ExtraFS filler wrote more bytes than requested")
    if values["used"] > values["total"]:
        raise HilFailure("ExtraFS reports used space greater than total space")
    return ExtraFsFill(
        requested_bytes=values["requested"],
        written_bytes=values["written"],
        used_kib=values["used"],
        total_kib=values["total"],
    )


def parse_contact_page_mask(text: str) -> ContactPageMask:
    match = HIL_PAGE_MASK_RE.fullmatch(text.strip())
    if not match:
        raise HilFailure(f"unexpected contact-page mask reply: {text!r}")
    page = int(match.group("page"), 10)
    if not 0 <= page <= 13:
        raise HilFailure(f"contact-page mask reported invalid page {page}")
    return ContactPageMask(page, int(match.group("mask"), 16))


def parse_contact_slot(text: str, *, operation: str = "contact slot") -> int:
    match = HIL_CONTACT_SLOT_RE.fullmatch(text.strip())
    if not match:
        raise HilFailure(f"unexpected {operation} reply: {text!r}")
    slot = int(match.group("slot"), 10)
    if not 0 <= slot < 350:
        raise HilFailure(f"{operation} reported invalid slot {slot}")
    return slot


def parse_advert_seed_slot(text: str) -> int:
    match = HIL_ADVERT_SEED_RE.fullmatch(text.strip())
    if not match:
        raise HilFailure(f"unexpected advert seed reply: {text!r}")
    slot = int(match.group("slot"), 10)
    if not 0 <= slot < 350:
        raise HilFailure(f"advert seed reported invalid slot {slot}")
    return slot


def make_contact(index: int) -> Dict[str, Any]:
    """Return a deterministic, non-radio synthetic Companion contact."""

    if index < 0:
        raise ValueError("contact index must not be negative")
    public_key = sha256(f"meshcore-t1000e-extrafs-hil:{index}".encode()).hexdigest()
    return {
        "public_key": public_key,
        "type": 1,
        "flags": 0,
        "out_path_hash_mode": -1,
        "out_path_len": -1,
        "out_path": "",
        "adv_name": f"HIL-{index:03d}",
        "last_advert": 1_700_000_000 + index,
        "adv_lat": 0.0,
        "adv_lon": 0.0,
    }


def encode_exact_contact_frame(contact: Dict[str, Any]) -> bytes:
    """Encode the 148-byte contact form, including its original lastmod."""

    try:
        public_key = bytes.fromhex(str(contact["public_key"]))
        contact_type = int(contact["type"])
        flags = int(contact["flags"])
        path_len = int(contact["out_path_len"])
        path_hash_mode = int(contact["out_path_hash_mode"])
        path = bytes.fromhex(str(contact.get("out_path", "")))
        name = str(contact["adv_name"]).encode("utf-8")
        last_advert = int(contact["last_advert"])
        latitude = round(float(contact["adv_lat"]) * 1_000_000)
        longitude = round(float(contact["adv_lon"]) * 1_000_000)
        lastmod = int(contact["lastmod"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HilFailure(f"cannot encode exact contact: {exc}") from exc

    if len(public_key) != 32:
        raise HilFailure("exact contact public key is not 32 bytes")
    if not 0 <= contact_type <= 0xFF or not 0 <= flags <= 0xFF:
        raise HilFailure("exact contact type or flags is outside one byte")
    if path_len == -1:
        encoded_path_len = 0xFF
        expected_path_bytes = 0
    elif 0 <= path_len <= 0x3F and 0 <= path_hash_mode <= 3:
        encoded_path_len = path_len | (path_hash_mode << 6)
        expected_path_bytes = path_len * (path_hash_mode + 1)
    else:
        raise HilFailure("exact contact path metadata is invalid")
    if len(path) != expected_path_bytes or len(path) > 64:
        raise HilFailure(
            f"exact contact path is {len(path)} bytes, expected "
            f"{expected_path_bytes}"
        )
    if len(name) > 31 or b"\0" in name:
        raise HilFailure("exact contact name is not a NUL-free 31-byte string")
    if not 0 <= last_advert <= 0xFFFFFFFF or not 0 <= lastmod <= 0xFFFFFFFF:
        raise HilFailure("exact contact timestamp is outside uint32")
    if (
        not -(1 << 31) <= latitude < (1 << 31)
        or not -(1 << 31) <= longitude < (1 << 31)
    ):
        raise HilFailure("exact contact coordinates are outside int32")

    frame = (
        b"\x09"
        + public_key
        + bytes((contact_type, flags, encoded_path_len))
        + path.ljust(64, b"\0")
        + name.ljust(32, b"\0")
        + last_advert.to_bytes(4, "little")
        + latitude.to_bytes(4, "little", signed=True)
        + longitude.to_bytes(4, "little", signed=True)
        + lastmod.to_bytes(4, "little")
    )
    if len(frame) != 148:
        raise HilFailure(f"exact contact frame is {len(frame)} bytes, expected 148")
    return frame


def require_destructive_consent(config: RunConfig) -> None:
    destructive = (
        config.scenario != "inspect"
        or config.cleanup
        or config.corrupt_page is not None
        or getattr(config, "corrupt_occupied_page", None) is not None
        or getattr(config, "advert_remove_rollback", False)
        or getattr(config, "fail_read_page", None) is not None
        or getattr(config, "fail_stat_page", None) is not None
        or config.unsafe_reset
    )
    if not destructive:
        return
    if not config.allow_destructive:
        raise HilFailure("destructive plan requires --allow-destructive")
    if _upper(config.confirm_usb_serial) != EXPECTED_USB_SERIAL:
        raise HilFailure(
            "destructive plan requires --confirm-usb-serial "
            f"{EXPECTED_USB_SERIAL}"
        )


def _list_serial_ports() -> List[Any]:
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise HilFailure(
            "pyserial is required; install it with 'python -m pip install "
            "pyserial'"
        ) from exc
    return list(list_ports.comports())


def resolve_initial_port(
    requested_port: str,
    port_source: Callable[[], Iterable[Any]] = _list_serial_ports,
) -> PortIdentity:
    matches = [
        port_identity(item)
        for item in port_source()
        if _upper(getattr(item, "device", "")) == _upper(requested_port)
    ]
    if len(matches) != 1:
        raise HilFailure(
            f"expected exactly one enumerated {requested_port}, found {len(matches)}"
        )
    validate_runtime_identity(matches[0], requested_port=requested_port)
    return matches[0]


def _matching_runtime_ports(
    expected_location: str,
    port_source: Callable[[], Iterable[Any]],
) -> List[PortIdentity]:
    matches: List[PortIdentity] = []
    for item in port_source():
        identity = port_identity(item)
        try:
            validate_runtime_identity(
                identity, expected_location=expected_location
            )
        except HilFailure:
            continue
        matches.append(identity)
    return matches


async def wait_for_reenumeration(
    previous: PortIdentity,
    timeout: float,
    port_source: Callable[[], Iterable[Any]] = _list_serial_ports,
    error_future: Optional[asyncio.Future[Any]] = None,
) -> ReenumerationResult:
    """Follow the identity through reset, including driver-retained devnodes."""

    started = time.monotonic()
    deadline = time.monotonic() + timeout
    saw_absence = False
    stable_device: Optional[str] = None
    stable_samples = 0
    while time.monotonic() < deadline:
        if error_future is not None and error_future.done():
            event = error_future.result()
            raise RebootRefused(event)
        matches = _matching_runtime_ports(previous.location, port_source)
        if not matches:
            saw_absence = True
            stable_device = None
            stable_samples = 0
        elif len(matches) > 1:
            raise HilFailure(
                "multiple matching T1000-E Companion ports appeared after reboot: "
                + ", ".join(match.device for match in matches)
            )
        else:
            current = matches[0]
            if _upper(current.device) == _upper(stable_device):
                stable_samples += 1
            else:
                stable_device = current.device
                stable_samples = 1
            # Windows normally removes the devnode.  Some CDC driver versions
            # retain it across a USB stack reset, so also permit a stably
            # identity-matched port after a bounded reset/recovery interval;
            # reboot proof is completed with the device uptime check below.
            recovery_elapsed = time.monotonic() - started
            if stable_samples >= 2 and (saw_absence or recovery_elapsed >= 1.0):
                return ReenumerationResult(current, saw_absence)
        await asyncio.sleep(0.20)
    raise HilFailure(
        f"locked T1000-E did not return stably within {timeout:.1f} seconds"
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value"):
        return _jsonable(value.value)
    return value


class StressRunner:
    def __init__(self, config: RunConfig, identity: PortIdentity) -> None:
        self.config = config
        self.identity = identity
        self.mc: Any = None
        self.steps: List[Dict[str, Any]] = []
        self.generated_keys: List[str] = []
        self.baseline_contact_count = 0
        self.baseline_contact_keys: set[str] = set()
        self.expected_contact_keys: set[str] = set()
        self.original_manual_add_contacts: Optional[bool] = None
        self.original_autoadd_config: Optional[int] = None
        self.original_channel_snapshot: Optional[Dict[str, Any]] = None
        self.contact_enumeration_transactions = 0
        self.contact_enumeration_short_attempts = 0
        self.contact_enumeration_retries = 0
        self.last_contact_advertised_count: Optional[int] = None

    def progress(self, message: str) -> None:
        if self.config.verbose:
            print(message, file=sys.stderr, flush=True)

    def record(self, name: str, **details: Any) -> None:
        self.steps.append({"step": name, **_jsonable(details)})
        self.progress(name)

    async def connect(self) -> None:
        try:
            from meshcore import MeshCore
        except ImportError as exc:
            raise HilFailure(
                "meshcore Python package is required; install meshcore-cli"
            ) from exc
        self.mc = await MeshCore.create_serial(
            self.identity.device,
            baudrate=115200,
            only_error=True,
            default_timeout=8.0,
            cx_dly=0.10,
        )
        if self.mc is None:
            raise HilFailure(f"APP_START failed on {self.identity.device}")

        self_event = await self.mc.commands.send_appstart()
        self._require_event(self_event, "SELF_INFO")
        node_key = _clean(self_event.payload.get("public_key", "")).lower()
        expected_node_key = _clean(
            self.config.expected_node_key_prefix
        ).lower()
        if expected_node_key and not node_key.startswith(expected_node_key):
            raise IdentityGateFailure(
                f"node public key starts {node_key[:8]!r}, expected requested "
                f"prefix {expected_node_key}"
            )

        device_event = await self.mc.commands.send_device_query()
        self._require_event(device_event, "DEVICE_INFO")
        model = _clean(device_event.payload.get("model", ""))
        if EXPECTED_MODEL_TOKEN.lower() not in model.lower():
            raise IdentityGateFailure(
                f"firmware model is {model!r}, expected {EXPECTED_MODEL_TOKEN}"
            )
        reported_capacity = int(device_event.payload.get("max_contacts", 0))
        if reported_capacity != self.config.contact_count:
            raise IdentityGateFailure(
                f"firmware reports {reported_capacity} contact slots, expected "
                f"{self.config.contact_count}"
            )
        reported_channels = int(device_event.payload.get("max_channels", 0))
        if not 0 <= self.config.channel_index < reported_channels:
            raise IdentityGateFailure(
                f"channel index {self.config.channel_index} is outside device "
                f"capacity {reported_channels}"
            )
        self.record(
            "connected",
            port=self.identity.device,
            usb_serial=self.identity.serial_number,
            node_key_prefix=node_key[:8],
            device=device_event.payload,
        )

    async def disconnect(self) -> None:
        if self.mc is None:
            return
        current, self.mc = self.mc, None
        try:
            await asyncio.wait_for(current.disconnect(), timeout=5.0)
        except Exception as exc:
            raise HilFailure(f"serial disconnect did not complete cleanly: {exc}") from exc

    async def connect_when_openable(self, timeout: float) -> None:
        """Retry the brief Windows devnode-present-but-busy reset interval.

        A T1000-E CDC devnode can satisfy enumeration checks before the serial
        driver will allow it to be opened.  Retry transport/handshake failures,
        but never retry an identity or capacity mismatch: that could silently
        move a destructive run onto the wrong radio.
        """

        deadline = time.monotonic() + timeout
        last_error: Optional[BaseException] = None
        while True:
            try:
                await self.connect()
                return
            except IdentityGateFailure:
                # connect() owns a live transport by the time application
                # identity is checked.  Drop it before failing closed so a
                # comprehensive run's cleanup can never act on this radio.
                try:
                    await self.disconnect()
                except HilFailure:
                    self.mc = None
                raise
            except (OSError, asyncio.TimeoutError, HilFailure) as exc:
                last_error = exc
                try:
                    await self.disconnect()
                except HilFailure:
                    # The reset may invalidate the handle while it is closing.
                    self.mc = None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.25, remaining))

        raise HilFailure(
            f"identity-matched {self.identity.device} did not become openable "
            f"within {timeout:.1f} seconds: {last_error}"
        ) from last_error

    @staticmethod
    def _require_event(event: Any, expected_name: str) -> Any:
        event_name = getattr(getattr(event, "type", None), "name", "")
        if event_name == "ERROR":
            raise HilFailure(
                f"expected {expected_name}, device returned error "
                f"{getattr(event, 'payload', None)!r}"
            )
        if event_name != expected_name:
            raise HilFailure(f"expected {expected_name}, received {event_name!r}")
        return event

    @staticmethod
    def _require_error_code(event: Any, code: int, operation: str) -> None:
        event_name = getattr(getattr(event, "type", None), "name", "")
        payload = getattr(event, "payload", {})
        if event_name != "ERROR" or payload.get("error_code") != code:
            raise HilFailure(
                f"{operation} returned {event_name} {payload!r}, expected "
                f"error code {code}"
            )

    async def cli(
        self, command: str, *, timeout: Optional[float] = None
    ) -> str:
        if timeout is None:
            event = await self.mc.commands.run_cli_command(command)
        else:
            # meshcore's run_cli_command() does not expose send()'s per-request
            # timeout.  Use the same public wire command directly so only the
            # intentionally slow filler gets a longer deadline; keeping the
            # ordinary 8-second timeout makes every other HIL failure prompt.
            try:
                from meshcore.events import EventType
                from meshcore.packets import CommandType
            except ImportError as exc:
                raise HilFailure("meshcore Python package is unavailable") from exc
            payload = bytes((CommandType.RUN_CLI_COMMAND.value,)) + command.encode(
                "utf-8"
            )
            event = await self.mc.commands.send(
                payload,
                [EventType.CLI_REPLY, EventType.ERROR],
                timeout=timeout,
            )
        self._require_event(event, "CLI_REPLY")
        return _clean(event.payload.get("text", ""))

    async def clear_filler(self) -> None:
        reply = await self.cli("hil extrafs clear")
        if reply != "HIL ExtraFS filler cleared":
            raise HilFailure(f"filler clear failed: {reply!r}")

    @staticmethod
    def contact_key_token(public_key: str) -> str:
        token = str(public_key)[:14].lower()
        try:
            decoded = bytes.fromhex(token)
        except ValueError as exc:
            raise HilFailure("contact key prefix is not hexadecimal") from exc
        if len(decoded) != 7:
            raise HilFailure("contact key prefix is not exactly seven bytes")
        return token

    async def contact_slot(self, public_key: str) -> int:
        token = self.contact_key_token(public_key)
        return parse_contact_slot(
            await self.cli(f"hil extrafs contact-slot {token}")
        )

    async def seed_cached_advert(self, public_key: str) -> int:
        token = self.contact_key_token(public_key)
        return parse_advert_seed_slot(
            await self.cli(f"hil extrafs seed-advert {token} CONFIRM")
        )

    async def clear_cached_advert(self, public_key: str) -> None:
        token = self.contact_key_token(public_key)
        reply = await self.cli(
            f"hil extrafs clear-advert {token} CONFIRM"
        )
        if reply != "HIL advert cleared":
            raise HilFailure(f"advert clear failed: {reply!r}")

    async def arm_contact_read_failure(self, page: int) -> None:
        reply = await self.cli(
            f"hil extrafs fail-read-page {page} CONFIRM"
        )
        expected = f"HIL contact page {page} read failure armed"
        if reply != expected:
            raise HilFailure(f"contact read-failure arm failed: {reply!r}")

    async def arm_contact_stat_failure(self, page: int) -> None:
        reply = await self.cli(
            f"hil extrafs fail-stat-page {page} CONFIRM"
        )
        expected = f"HIL contact page {page} stat failure armed"
        if reply != expected:
            raise HilFailure(f"contact stat-failure arm failed: {reply!r}")

    async def contact_page_mask(self, page: int) -> int:
        parsed = parse_contact_page_mask(
            await self.cli(f"hil extrafs page-mask {page}")
        )
        if parsed.page != page:
            raise HilFailure(
                f"contact-page mask reply named page {parsed.page}, expected {page}"
            )
        return parsed.occupied_mask

    async def wait_for_contact_page_mask(
        self, page: int, expected_mask: int
    ) -> int:
        """Poll the persisted header until the lazy repair is durable."""

        deadline = time.monotonic() + max(
            self.config.settle_seconds, CONTACT_PAGE_REPAIR_TIMEOUT_SECONDS
        )
        observed = await self.contact_page_mask(page)
        while observed != expected_mask and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            observed = await self.contact_page_mask(page)
        if observed != expected_mask:
            raise HilFailure(
                f"contact page {page} mask was not repaired: "
                f"{observed:08x}, expected {expected_mask:08x}"
            )
        return observed

    async def set_channel_exact(
        self, channel_index: int, channel_name: str, channel_secret: bytes
    ) -> Any:
        """Set a channel without the client's '#' secret auto-derivation."""

        try:
            from meshcore.events import EventType
        except ImportError as exc:
            raise HilFailure("meshcore Python package is unavailable") from exc
        name_bytes = channel_name.encode("utf-8")[:32].ljust(32, b"\0")
        if len(channel_secret) != 16:
            raise HilFailure("channel snapshot secret is not 16 bytes")
        payload = (
            b"\x20"
            + bytes((channel_index,))
            + name_bytes
            + bytes(channel_secret)
        )
        return await self.mc.commands.send(
            payload, [EventType.OK, EventType.ERROR]
        )

    async def set_contact_exact(self, contact: Dict[str, Any]) -> Any:
        """Add or update a contact while preserving its supplied lastmod."""

        try:
            from meshcore.events import EventType
        except ImportError as exc:
            raise HilFailure("meshcore Python package is unavailable") from exc
        return await self.mc.commands.send(
            encode_exact_contact_frame(contact),
            [EventType.OK, EventType.ERROR],
        )

    async def core_uptime(self) -> int:
        event = self._require_event(
            await self.mc.commands.get_stats_core(), "STATS_CORE"
        )
        return int(event.payload.get("uptime_secs", -1))

    async def status(self) -> ExtraFsStatus:
        status = parse_extrafs_status(await self.cli("hil extrafs status"))
        if not status.active:
            raise HilFailure("internal ExtraFS is not active")
        if status.total_kib != EXPECTED_STORAGE_KIB:
            raise HilFailure(
                f"ExtraFS capacity is {status.total_kib} KiB, expected "
                f"{EXPECTED_STORAGE_KIB} KiB"
            )
        battery = self._require_event(
            await self.mc.commands.get_bat(), "BATTERY"
        ).payload
        if (
            battery.get("used_kb") != status.used_kib
            or battery.get("total_kb") != status.total_kib
        ):
            raise HilFailure(
                "binary storage status disagrees with the HIL status reply"
            )
        self.record("storage_status", hil=asdict(status), binary=battery)
        return status

    async def _contact_snapshot_once(
        self,
    ) -> tuple[Dict[str, Dict[str, Any]], int]:
        """Read one unfiltered stream and preserve its advertised count."""

        reader = getattr(self.mc, "_reader", None)
        if reader is None or not hasattr(reader, "contact_nb"):
            raise HilFailure("client did not expose the CONTACTS_START count")

        # CONTACTS_START is not dispatched as an event by the current SDK. Clear
        # its private aggregation state so a lost START cannot reuse the count or
        # dictionary from the preceding pass and make a short stream look whole.
        reader.contact_nb = None
        reader.contacts = {}
        self.contact_enumeration_transactions += 1
        event = await self.mc.commands.get_contacts(lastmod=0, timeout=20)
        self._require_event(event, "CONTACTS")
        contacts = event.payload
        if not isinstance(contacts, dict):
            raise HilFailure("CONTACTS payload is not a dictionary")
        expected = getattr(reader, "contact_nb", None)
        if not isinstance(expected, int) or expected < 0:
            raise HilFailure("client did not expose a valid CONTACTS_START count")

        snapshot = dict(contacts)
        self.last_contact_advertised_count = expected
        if len(snapshot) != expected:
            self.contact_enumeration_short_attempts += 1
        return snapshot, expected

    async def contacts(self) -> Dict[str, Dict[str, Any]]:
        # Never use a short snapshot for destructive cleanup. The installed
        # client records CONTACTS_START's unfiltered count but does not validate
        # it before dispatching CONTACTS, so retry an incomplete transaction.
        failures: List[str] = []
        for attempt in range(1, 4):
            if attempt > 1:
                self.contact_enumeration_retries += 1
            snapshot, expected = await self._contact_snapshot_once()
            if len(snapshot) == expected:
                return snapshot

            failures.append(
                f"attempt {attempt}: expected {expected}, received {len(snapshot)}"
            )
            self.progress(f"incomplete contact list; retrying ({failures[-1]})")

        raise HilFailure("incomplete contact enumeration; " + "; ".join(failures))

    async def expect_contacts_file_io(self, context: str) -> None:
        """Require GET_CONTACTS to fail before emitting any partial stream."""

        reader = getattr(self.mc, "_reader", None)
        if reader is None or not hasattr(reader, "contact_nb"):
            raise HilFailure("client did not expose contact aggregation state")
        reader.contact_nb = None
        reader.contacts = {}
        try:
            from meshcore.events import EventType
        except ImportError as exc:
            raise HilFailure("meshcore Python package is unavailable") from exc
        # The SDK's aggregate get_contacts() installs its ERROR waiter after a
        # fire-and-forget send.  This immediate firmware error can beat that
        # waiter, so use send() directly to subscribe before transmitting.
        event = await self.mc.commands.send(
            b"\x04", [EventType.ERROR], timeout=20
        )
        self._require_error_code(event, 5, context)
        if reader.contact_nb is not None or reader.contacts:
            raise HilFailure(
                f"{context}: device emitted partial contact stream state "
                f"count={reader.contact_nb!r}, contacts={len(reader.contacts)}"
            )

    async def contact_by_key(self, public_key: str) -> Dict[str, Any]:
        event = self._require_event(
            await self.mc.commands.get_contact_by_key(
                bytes.fromhex(public_key)
            ),
            "NEXT_CONTACT",
        )
        contact = dict(event.payload)
        if contact.get("public_key") != public_key:
            raise HilFailure(
                "single-contact query returned a different public key"
            )
        return contact

    async def expect_contact_not_found(
        self, public_key: str, context: str
    ) -> None:
        event = await self.mc.commands.get_contact_by_key(
            bytes.fromhex(public_key)
        )
        self._require_error_code(event, 2, context)

    async def stress_contact_enumerations(
        self,
        expected_keys: Optional[set[str]],
        context: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Repeat complete list reads and surface even successfully retried loss."""

        requested = self.config.contact_enumeration_passes
        if requested is None or requested < 1:
            raise HilFailure("contact enumeration stress requires at least one pass")

        start_transactions = self.contact_enumeration_transactions
        start_short = self.contact_enumeration_short_attempts
        start_retries = self.contact_enumeration_retries
        reference_keys = set(expected_keys) if expected_keys is not None else None
        completed = 0
        final_snapshot: Dict[str, Dict[str, Any]] = {}
        advertised_counts: List[int] = []
        received_counts: List[int] = []

        def details(ok: bool, failure: Optional[str] = None) -> Dict[str, Any]:
            keys = reference_keys if reference_keys is not None else set()
            result: Dict[str, Any] = {
                "context": context,
                "ok": ok,
                "requested_passes": requested,
                "completed_passes": completed,
                "transport_transactions": (
                    self.contact_enumeration_transactions - start_transactions
                ),
                "short_attempts": (
                    self.contact_enumeration_short_attempts - start_short
                ),
                "retry_attempts": (
                    self.contact_enumeration_retries - start_retries
                ),
                "expected_count": len(keys),
                "advertised_counts": advertised_counts,
                "received_counts": received_counts,
                "keyset_sha256": self.keyset_digest(keys),
                "usb_serial": self.identity.serial_number,
            }
            if failure is not None:
                result["failure"] = failure
            return result

        try:
            for pass_number in range(1, requested + 1):
                final_snapshot = await self.contacts()
                actual_keys = set(final_snapshot)
                advertised_counts.append(
                    int(self.last_contact_advertised_count or 0)
                )
                received_counts.append(len(final_snapshot))
                if reference_keys is None:
                    reference_keys = actual_keys
                self.assert_exact_contacts(
                    final_snapshot,
                    reference_keys,
                    f"{context} pass {pass_number}/{requested}",
                )
                completed = pass_number
                self.progress(
                    f"contact enumeration pass {pass_number}/{requested}: "
                    f"{len(final_snapshot)} contacts"
                )

            expected_digest = self.config.expected_contact_keyset_sha256
            actual_digest = self.keyset_digest(reference_keys or set())
            if expected_digest is not None and actual_digest != expected_digest:
                raise HilFailure(
                    f"{context}: keyset digest is {actual_digest}, expected "
                    f"{expected_digest}"
                )

            short_attempts = self.contact_enumeration_short_attempts - start_short
            if short_attempts:
                retry_attempts = self.contact_enumeration_retries - start_retries
                raise HilFailure(
                    f"{context}: observed {short_attempts} incomplete contact "
                    f"stream(s) and {retry_attempts} retry attempt(s) across "
                    f"{requested} validated pass(es)"
                )
        except BaseException as exc:
            self.record("contact_enumeration_stress", **details(False, str(exc)))
            raise

        self.record("contact_enumeration_stress", **details(True))
        return final_snapshot

    async def channels(self) -> List[Dict[str, Any]]:
        device = self._require_event(
            await self.mc.commands.send_device_query(), "DEVICE_INFO"
        ).payload
        maximum = int(device.get("max_channels", 0))
        if maximum <= 0:
            raise HilFailure("device reported no channel capacity")
        result = []
        for index in range(maximum):
            payload = self._require_event(
                await self.mc.commands.get_channel(index), "CHANNEL_INFO"
            ).payload
            result.append(
                {
                    "channel_idx": payload["channel_idx"],
                    "channel_name": payload["channel_name"],
                    "channel_hash": payload["channel_hash"],
                }
            )
        return result

    async def set_manual_contact_mode(self, enabled: bool) -> None:
        event = await self.mc.commands.set_manual_add_contacts(enabled)
        self._require_event(event, "OK")
        verify = self._require_event(
            await self.mc.commands.send_appstart(), "SELF_INFO"
        ).payload
        if bool(verify.get("manual_add_contacts")) != enabled:
            raise HilFailure("manual/automatic contact mode did not change")
        self.record("manual_contact_mode", enabled=enabled)

    async def set_autoadd_config(self, value: int) -> None:
        self._require_event(
            await self.mc.commands.set_autoadd_config(value), "OK"
        )
        verify = self._require_event(
            await self.mc.commands.get_autoadd_config(), "AUTOADD_CONFIG"
        ).payload
        if int(verify.get("config", -1)) != value:
            raise HilFailure(
                f"automatic contact-type mask is {verify.get('config')}, "
                f"expected {value}"
            )
        self.record("autoadd_config", value=value)

    @staticmethod
    def keyset_digest(keys: Iterable[str]) -> str:
        canonical = "\n".join(sorted(keys)).encode("ascii")
        return sha256(canonical).hexdigest()

    @staticmethod
    def semantic_contact_digest(contacts: Dict[str, Dict[str, Any]]) -> str:
        fields = (
            "public_key",
            "type",
            "flags",
            "out_path_hash_mode",
            "out_path_len",
            "out_path",
            "adv_name",
            "last_advert",
            "adv_lat",
            "adv_lon",
            "lastmod",
        )
        normalized = [
            {field: contacts[key].get(field) for field in fields}
            for key in sorted(contacts)
        ]
        canonical = json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return sha256(canonical).hexdigest()

    @staticmethod
    def wire_contact_digest(contacts: Dict[str, Dict[str, Any]]) -> str:
        digest = sha256()
        for key in sorted(contacts):
            digest.update(encode_exact_contact_frame(contacts[key]))
        return digest.hexdigest()

    def exact_inventory_digests(
        self, contacts: Dict[str, Dict[str, Any]]
    ) -> Dict[str, str]:
        return {
            "keyset_sha256": self.keyset_digest(contacts),
            "semantic_sha256": self.semantic_contact_digest(contacts),
            "wire_sha256": self.wire_contact_digest(contacts),
        }

    def assert_exact_contact_inventory(
        self,
        actual: Dict[str, Dict[str, Any]],
        expected: Dict[str, Dict[str, Any]],
        context: str,
    ) -> Dict[str, str]:
        self.assert_exact_contacts(actual, set(expected), context)
        expected_digests = self.exact_inventory_digests(expected)
        actual_digests = self.exact_inventory_digests(actual)
        if actual_digests != expected_digests:
            raise HilFailure(
                f"{context}: exact inventory digest mismatch; "
                f"actual={actual_digests}, expected={expected_digests}"
            )
        return actual_digests

    async def capture_contact_slot_map(
        self,
        contacts: Dict[str, Dict[str, Any]],
        context: str,
    ) -> Dict[str, int]:
        """Read every live persistent slot through the identity-gated HIL API."""

        keys = list(contacts)
        tokens = [self.contact_key_token(key) for key in keys]
        if len(set(tokens)) != len(tokens):
            raise HilFailure(
                f"{context}: contact key prefixes are not unique enough for "
                "identity-safe slot queries"
            )

        slots: Dict[str, int] = {}
        owners: Dict[int, str] = {}
        for key in keys:
            slot = await self.contact_slot(key)
            if slot in owners:
                raise HilFailure(
                    f"{context}: contacts {owners[slot][:12]} and {key[:12]} "
                    f"both report persistent slot {slot}"
                )
            slots[key] = slot
            owners[slot] = key
        return slots

    async def assert_contact_slot_map(
        self,
        keys: Iterable[str],
        expected_slots: Dict[str, int],
        context: str,
    ) -> None:
        """Require each named live contact to retain its captured slot."""

        for key in keys:
            expected = expected_slots.get(key)
            if expected is None:
                raise HilFailure(
                    f"{context}: no captured slot for contact {key[:12]}"
                )
            actual = await self.contact_slot(key)
            if actual != expected:
                raise HilFailure(
                    f"{context}: contact {key[:12]} moved from slot "
                    f"{expected} to {actual}"
                )

    def assert_deterministic_contact_inventory(
        self, contacts: Dict[str, Dict[str, Any]], context: str
    ) -> set[str]:
        deterministic_keys = {
            make_contact(index)["public_key"]
            for index in range(self.config.contact_count)
        }
        self.assert_exact_contacts(contacts, deterministic_keys, context)
        if len({key[:14] for key in deterministic_keys}) != len(
            deterministic_keys
        ):
            raise HilFailure("deterministic HIL contact prefixes are not unique")
        for index in range(self.config.contact_count):
            expected = make_contact(index)
            key = expected["public_key"]
            actual = contacts[key]
            for field, expected_value in expected.items():
                if actual.get(field) != expected_value:
                    raise HilFailure(
                        f"{context}: {key[:12]} {field} is "
                        f"{actual.get(field)!r}, expected {expected_value!r}"
                    )
        return deterministic_keys

    def assert_exact_contacts(
        self,
        contacts: Dict[str, Dict[str, Any]],
        expected_keys: set[str],
        context: str,
    ) -> None:
        actual_keys = set(contacts)
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            unexpected = sorted(actual_keys - expected_keys)
            raise HilFailure(
                f"{context}: contact key set mismatch; "
                f"missing={[key[:12] for key in missing[:8]]}, "
                f"unexpected={[key[:12] for key in unexpected[:8]]}"
            )

    async def inspect(self) -> None:
        await self.status()
        if self.config.contact_enumeration_passes is not None:
            contacts = await self.stress_contact_enumerations(
                None, "read-only inventory"
            )
        else:
            contacts = await self.contacts()
        channels = await self.channels()
        self.record(
            "inventory",
            contact_count=len(contacts),
            channels=channels,
        )

    async def add_contacts_to_capacity(self) -> set[str]:
        existing = await self.contacts()
        synthetic = [make_contact(index) for index in range(self.config.contact_count)]
        synthetic_keys = {contact["public_key"] for contact in synthetic}
        resumed_keys = set(existing) & synthetic_keys
        self.baseline_contact_keys = set(existing) - synthetic_keys
        self.baseline_contact_count = len(self.baseline_contact_keys)
        if (
            getattr(self.config, "corrupt_occupied_page", None) is not None
            and self.baseline_contact_keys
        ):
            raise HilFailure(
                "occupied-mask recovery requires an existing inventory made "
                "only from this harness's deterministic contacts"
            )
        if existing and not self.config.allow_existing_contacts:
            raise HilFailure(
                f"expected an erased spare, but found {len(existing)} contact(s); "
                "use --allow-existing-contacts only after reviewing that inventory"
            )
        if len(existing) > self.config.contact_count:
            raise HilFailure(
                f"device already has {len(existing)} contacts, above requested "
                f"capacity target {self.config.contact_count}"
            )
        self.generated_keys = [
            contact["public_key"]
            for contact in synthetic
            if contact["public_key"] in resumed_keys
        ]
        to_add = self.config.contact_count - len(existing)
        added = 0
        for contact in synthetic:
            if added >= to_add:
                break
            if contact["public_key"] in existing:
                continue
            event = await self.mc.commands.add_contact(contact)
            self._require_event(event, "OK")
            self.generated_keys.append(contact["public_key"])
            added += 1
            if added % 25 == 0 or added == to_add:
                self.progress(f"contacts added: {added}/{to_add}")

        if not self.generated_keys:
            raise HilFailure(
                "comprehensive contact mutation requires at least one "
                "harness-owned deterministic contact"
            )

        await asyncio.sleep(self.config.settle_seconds)
        persisted = await self.contacts()
        self.expected_contact_keys = (
            self.baseline_contact_keys | set(self.generated_keys)
        )
        if len(self.expected_contact_keys) != self.config.contact_count:
            raise HilFailure("synthetic contact plan did not fill exactly 350 slots")
        self.assert_exact_contacts(
            persisted, self.expected_contact_keys, "after lazy persistence settle"
        )
        overflow = make_contact(self.config.contact_count + 1000)
        self._require_error_code(
            await self.mc.commands.add_contact(overflow),
            3,
            "contact beyond capacity",
        )
        self.record(
            "contact_capacity",
            baseline=self.baseline_contact_count,
            resumed=len(resumed_keys),
            added=added,
            generated_total=len(self.generated_keys),
            persisted=len(persisted),
            keyset_sha256=self.keyset_digest(persisted),
            overflow_error="ERR_CODE_TABLE_FULL",
        )
        return set(self.expected_contact_keys)

    async def fill_storage_to_enospc(self) -> ExtraFsFill:
        """Create a verified, non-empty filler that exhausts ExtraFS."""

        await self.clear_filler()
        fill = parse_extrafs_fill(
            await self.cli(
                f"hil extrafs fill {self.config.fill_bytes}",
                timeout=EXTRAFS_FILL_TIMEOUT_SECONDS,
            )
        )
        if fill.requested_bytes != self.config.fill_bytes:
            raise HilFailure("firmware echoed a different filler request size")
        if fill.written_bytes >= fill.requested_bytes:
            raise HilFailure(
                "filler did not encounter ENOSPC; increase --fill-bytes"
            )
        if fill.written_bytes <= 0:
            raise HilFailure("filler did not commit any storage pressure")
        if fill.total_kib != EXPECTED_STORAGE_KIB:
            raise HilFailure("ExtraFS capacity changed during fill")
        return fill

    async def fill_and_test_channel_atomicity(
        self,
    ) -> tuple[ExtraFsFill, Dict[str, Any]]:
        before_event = self._require_event(
            await self.mc.commands.get_channel(self.config.channel_index),
            "CHANNEL_INFO",
        )
        before = before_event.payload
        self.original_channel_snapshot = {
            "channel_name": before.get("channel_name", ""),
            "channel_secret": bytes(before.get("channel_secret", bytes(16))),
        }
        fill = await self.fill_storage_to_enospc()

        test_name = "HIL-FULL-ROLLBACK"
        test_secret = sha256(test_name.encode()).digest()[:16]
        set_event = await self.set_channel_exact(
            self.config.channel_index, test_name, test_secret
        )
        self._require_error_code(
            set_event, 5, "channel write while ExtraFS is full"
        )
        after = self._require_event(
            await self.mc.commands.get_channel(self.config.channel_index),
            "CHANNEL_INFO",
        ).payload
        if (
            after.get("channel_name") != before.get("channel_name")
            or after.get("channel_secret") != before.get("channel_secret")
        ):
            raise HilFailure("failed channel write changed the live channel table")
        self.record(
            "full_storage_atomicity",
            fill=asdict(fill),
            channel_index=self.config.channel_index,
            expected_error="ERR_CODE_FILE_IO_ERROR",
            rollback_verified=True,
        )
        return fill, before

    async def reboot_and_reconnect(self, *, unsafe: bool = False) -> None:
        old_identity = self.identity
        before_uptime = await self.core_uptime()
        if before_uptime < 6:
            await asyncio.sleep(6 - max(before_uptime, 0))
            before_uptime = await self.core_uptime()

        loop = asyncio.get_running_loop()
        reboot_error: asyncio.Future[Any] = loop.create_future()

        def capture_error(event: Any) -> None:
            if not reboot_error.done():
                reboot_error.set_result(event)

        try:
            from meshcore.events import EventType
        except ImportError as exc:
            raise HilFailure("meshcore Python package is unavailable") from exc
        subscription = self.mc.subscribe(EventType.ERROR, capture_error)
        if unsafe:
            # Fire-and-forget: firmware resets before it can emit CLI_REPLY.
            command = b"\x42hil extrafs unsafe-reset CONFIRM"
            await self.mc.commands.send(command)
        else:
            # Register the ERROR subscription before sending: a dirty-page
            # flush failure is the only response, while success resets without
            # an acknowledgement.
            await self.mc.commands.send(b"\x13reboot")
        try:
            transition = await wait_for_reenumeration(
                old_identity,
                self.config.reenum_timeout,
                error_future=reboot_error,
            )
        finally:
            self.mc.unsubscribe(subscription)
        try:
            await self.disconnect()
        except HilFailure:
            # The device may have removed the OS handle before our close.
            pass
        self.identity = transition.identity
        await self.connect_when_openable(
            min(10.0, self.config.reenum_timeout)
        )
        after_uptime = await self.core_uptime()
        if not transition.observed_absence and after_uptime >= before_uptime:
            raise HilFailure(
                f"reopened device uptime did not reset: {before_uptime} -> "
                f"{after_uptime} seconds"
            )
        self.record(
            "reboot_reenumeration",
            unsafe=unsafe,
            previous_port=old_identity.device,
            current_port=self.identity.device,
            location=self.identity.location,
            observed_usb_absence=transition.observed_absence,
            uptime_before=before_uptime,
            uptime_after=after_uptime,
        )

    async def graceful_reboot_or_enospc_refusal(
        self,
    ) -> tuple[str, int, int]:
        """Accept either a clean reboot or a verified FILE_IO refusal."""

        before_uptime = await self.core_uptime()
        if before_uptime < 6:
            await asyncio.sleep(6 - max(before_uptime, 0))
            before_uptime = await self.core_uptime()
        try:
            await self.reboot_and_reconnect()
        except RebootRefused as exc:
            self._require_error_code(
                exc.event,
                5,
                "graceful reboot after rolled-back advert-backed removal",
            )
            after_uptime = await self.core_uptime()
            if after_uptime < before_uptime:
                raise HilFailure(
                    "refused advert-rollback reboot reset uptime: "
                    f"{before_uptime} -> {after_uptime}"
                )
            self.record(
                "advert_rollback_reboot_refused",
                error="ERR_CODE_FILE_IO_ERROR",
                uptime_before=before_uptime,
                uptime_after=after_uptime,
            )
            return "refused", before_uptime, after_uptime

        # reboot_and_reconnect() already proves reset either by observing USB
        # absence or (for retained devnodes) by comparing uptime internally.
        # A slow Windows re-enumeration can legitimately make this later sample
        # exceed the low pre-reset floor, so do not impose a second comparison.
        after_uptime = await self.core_uptime()
        return "rebooted", before_uptime, after_uptime

    async def verify_contacts_after_reboot(
        self, expected_keys: set[str]
    ) -> None:
        if self.config.contact_enumeration_passes is not None:
            contacts = await self.stress_contact_enumerations(
                expected_keys, "after reboot"
            )
        else:
            contacts = await self.contacts()
            self.assert_exact_contacts(
                contacts, expected_keys, "after reboot"
            )
        self.record(
            "contact_reboot_persistence",
            expected=len(expected_keys),
            actual=len(contacts),
            keyset_sha256=self.keyset_digest(contacts),
        )

    async def verify_full_state_after_reboot(
        self,
        expected_fill: ExtraFsFill,
        expected_channel: Dict[str, Any],
    ) -> None:
        status = await self.status()
        if status.filler_bytes != expected_fill.written_bytes:
            raise HilFailure(
                f"filler size after reboot is {status.filler_bytes}, expected "
                f"{expected_fill.written_bytes}"
            )
        if status.used_kib != expected_fill.used_kib:
            raise HilFailure(
                f"used space changed across reboot: {expected_fill.used_kib} -> "
                f"{status.used_kib} KiB"
            )
        channel = self._require_event(
            await self.mc.commands.get_channel(self.config.channel_index),
            "CHANNEL_INFO",
        ).payload
        if (
            channel.get("channel_name") != expected_channel.get("channel_name")
            or channel.get("channel_secret")
            != expected_channel.get("channel_secret")
        ):
            raise HilFailure("failed full-filesystem channel write survived reboot")
        self.record(
            "full_storage_reboot_persistence",
            filler_bytes=status.filler_bytes,
            used_kib=status.used_kib,
            channel_rollback_verified=True,
        )

    def assert_contact_variant(
        self,
        contacts: Dict[str, Dict[str, Any]],
        expected_keys: set[str],
        target_key: str,
        expected_contact: Dict[str, Any],
        context: str,
        *,
        include_lastmod: bool = False,
    ) -> None:
        self.assert_exact_contacts(contacts, expected_keys, context)
        actual = contacts.get(target_key)
        if actual is None:
            raise HilFailure(f"{context}: target contact disappeared")
        fields = [
            "public_key",
            "type",
            "flags",
            "out_path_hash_mode",
            "out_path_len",
            "out_path",
            "adv_name",
            "last_advert",
            "adv_lat",
            "adv_lon",
        ]
        if include_lastmod:
            fields.append("lastmod")
        for field in fields:
            if actual.get(field) != expected_contact.get(field):
                raise HilFailure(
                    f"{context}: target {field} is {actual.get(field)!r}, "
                    f"expected {expected_contact.get(field)!r}"
                )

    @staticmethod
    def assert_exact_contact_record(
        actual: Dict[str, Any], expected: Dict[str, Any], context: str
    ) -> None:
        actual_wire = encode_exact_contact_frame(actual)
        expected_wire = encode_exact_contact_frame(expected)
        if actual_wire != expected_wire:
            raise HilFailure(f"{context}: exact contact record changed")

    async def expect_enospc_reboot_refusal(
        self, operation: str
    ) -> tuple[int, int]:
        """Prove a dirty contact page prevents a graceful ENOSPC reboot."""

        # Give the normal lazy writer enough time to encounter ENOSPC before
        # asking reboot to synchronously drain the same dirty page.
        await asyncio.sleep(self.config.settle_seconds)
        before_uptime = await self.core_uptime()
        if before_uptime < 6:
            await asyncio.sleep(6 - max(before_uptime, 0))
            before_uptime = await self.core_uptime()
        try:
            from meshcore.events import EventType
        except ImportError as exc:
            raise HilFailure("meshcore Python package is unavailable") from exc
        reboot_event = await self.mc.commands.send(
            b"\x13reboot",
            [EventType.ERROR],
            timeout=ENOSPC_REBOOT_REFUSAL_TIMEOUT_SECONDS,
        )
        self._require_error_code(reboot_event, 5, operation)
        after_uptime = await self.core_uptime()
        if after_uptime < before_uptime:
            raise HilFailure(
                f"reboot refusal reset uptime: {before_uptime} -> "
                f"{after_uptime}"
            )
        return before_uptime, after_uptime

    async def restore_exact_contact_inventory(
        self,
        expected_keys: set[str],
        original_contact: Dict[str, Any],
        *,
        replacement_key: Optional[str] = None,
        context: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Remove an owned substitute and durably restore one exact contact."""

        await self.clear_filler()
        original = dict(original_contact)
        original_key = str(original["public_key"])
        current = await self.contacts()
        if replacement_key is not None and replacement_key in current:
            self._require_event(
                await self.mc.commands.remove_contact(replacement_key), "OK"
            )
            current = dict(current)
            current.pop(replacement_key, None)

        self._require_event(await self.set_contact_exact(original), "OK")

        await asyncio.sleep(self.config.settle_seconds)
        await self.reboot_and_reconnect()
        restored = await self.contacts()
        self.assert_contact_variant(
            restored,
            expected_keys,
            original_key,
            original,
            context,
            include_lastmod=True,
        )
        expected_digest = self.keyset_digest(expected_keys)
        restored_digest = self.keyset_digest(restored)
        if restored_digest != expected_digest:
            raise HilFailure(
                f"{context}: restored keyset digest is {restored_digest}, "
                f"expected {expected_digest}"
            )
        return restored

    async def test_contact_update_recovery_when_full(
        self, expected_keys: set[str]
    ) -> None:
        """Prove a lazily ACKed contact cannot be lost to ENOSPC reboot."""

        before_contacts = await self.contacts()
        self.assert_exact_contacts(
            before_contacts, expected_keys, "before full-storage contact update"
        )
        preferred_key = make_contact(self.config.contact_count - 1)["public_key"]
        safe_targets = [
            key for key in self.generated_keys if key in before_contacts
        ]
        if not safe_targets:
            raise HilFailure(
                "full-storage contact test found no harness-owned target; "
                "refusing to mutate a pre-existing contact"
            )
        target_key = (
            preferred_key if preferred_key in safe_targets else safe_targets[-1]
        )
        original = dict(before_contacts[target_key])
        original.setdefault("public_key", target_key)
        changed = dict(original)
        changed["adv_name"] = (
            "HIL-ENOSPC-ALT"
            if original.get("adv_name") == "HIL-ENOSPC"
            else "HIL-ENOSPC"
        )
        changed["flags"] = int(original.get("flags", 0)) ^ 0x01

        async def restore_original() -> Dict[str, Dict[str, Any]]:
            # Clear the test filler even if an earlier assertion failed.  The
            # contact page cannot be repaired while the intentional ENOSPC
            # condition remains in place.
            await self.clear_filler()
            self._require_event(
                await self.mc.commands.update_contact(dict(original)), "OK"
            )
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            restored_snapshot = await self.contacts()
            self.assert_contact_variant(
                restored_snapshot,
                expected_keys,
                target_key,
                original,
                "after restoring the full-storage contact",
            )
            return restored_snapshot

        mutation_acknowledged = False
        original_restored = False
        try:
            self._require_event(
                await self.mc.commands.update_contact(changed), "OK"
            )
            mutation_acknowledged = True
            live_after_ack = await self.contacts()
            self.assert_contact_variant(
                live_after_ack,
                expected_keys,
                target_key,
                changed,
                "after full-storage contact ACK",
            )

            before_uptime, after_uptime = (
                await self.expect_enospc_reboot_refusal(
                    "graceful reboot with a dirty contact page at ENOSPC"
                )
            )
            live_after_refusal = await self.contacts()
            self.assert_contact_variant(
                live_after_refusal,
                expected_keys,
                target_key,
                changed,
                "after refused full-storage reboot",
            )

            await self.clear_filler()
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            persisted = await self.contacts()
            self.assert_contact_variant(
                persisted,
                expected_keys,
                target_key,
                changed,
                "after freeing space and rebooting",
            )

            restored = await restore_original()
            original_restored = True
        except Exception as test_exc:
            if mutation_acknowledged and not original_restored:
                try:
                    await restore_original()
                    original_restored = True
                    self.record(
                        "full_storage_contact_emergency_restore",
                        target_key_prefix=target_key[:12],
                        original_restored=True,
                    )
                except Exception as restore_exc:
                    raise HilFailure(
                        f"full-storage contact test failed ({test_exc}); "
                        f"emergency restore also failed ({restore_exc})"
                    ) from test_exc
            raise
        self.record(
            "full_storage_contact_recovery",
            target_key_prefix=target_key[:12],
            update_acknowledged=True,
            reboot_error="ERR_CODE_FILE_IO_ERROR",
            uptime_before_refusal=before_uptime,
            uptime_after_refusal=after_uptime,
            live_mutation_verified=True,
            persisted_after_filler_clear=True,
            original_restored=True,
            keyset_sha256=self.keyset_digest(restored),
        )

    async def test_contact_add_recovery_when_full(
        self, expected_keys: set[str]
    ) -> None:
        """Prove a lazily ACKed ADD is retained once ENOSPC is relieved."""

        before_contacts = await self.contacts()
        self.assert_exact_contacts(
            before_contacts, expected_keys, "before full-storage contact add"
        )
        safe_targets = [
            key for key in self.generated_keys if key in before_contacts
        ]
        preferred_key = make_contact(self.config.contact_count - 1)["public_key"]
        if not safe_targets:
            raise HilFailure(
                "full-storage contact add test found no harness-owned target; "
                "refusing to remove a pre-existing contact"
            )
        original_key = (
            preferred_key if preferred_key in safe_targets else safe_targets[-1]
        )
        original = dict(before_contacts[original_key])
        original.setdefault("public_key", original_key)
        replacement = make_contact(self.config.contact_count + 2001)
        replacement_key = replacement["public_key"]
        if replacement_key in expected_keys:
            raise HilFailure("full-storage ADD replacement already exists")

        without_original = set(expected_keys) - {original_key}
        substituted_keys = without_original | {replacement_key}
        mutation_attempted = False
        original_restored = False
        fill: Optional[ExtraFsFill] = None
        try:
            # Persist the empty slot before applying storage pressure.  This
            # makes the subsequent command an actual ADD, not an UPDATE.
            mutation_attempted = True
            self._require_event(
                await self.mc.commands.remove_contact(original_key), "OK"
            )
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            persisted_empty_slot = await self.contacts()
            self.assert_exact_contacts(
                persisted_empty_slot,
                without_original,
                "after persisting the full-storage ADD test's empty slot",
            )

            fill = await self.fill_storage_to_enospc()
            self._require_event(
                await self.mc.commands.add_contact(dict(replacement)), "OK"
            )
            live_after_ack = await self.contacts()
            self.assert_contact_variant(
                live_after_ack,
                substituted_keys,
                replacement_key,
                replacement,
                "after full-storage contact ADD ACK",
            )

            before_uptime, after_uptime = (
                await self.expect_enospc_reboot_refusal(
                    "graceful reboot with a dirty contact ADD at ENOSPC"
                )
            )
            live_after_refusal = await self.contacts()
            self.assert_contact_variant(
                live_after_refusal,
                substituted_keys,
                replacement_key,
                replacement,
                "after refused full-storage contact ADD reboot",
            )

            await self.clear_filler()
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            persisted_replacement = await self.contacts()
            self.assert_contact_variant(
                persisted_replacement,
                substituted_keys,
                replacement_key,
                replacement,
                "after freeing space for the contact ADD",
            )

            restored = await self.restore_exact_contact_inventory(
                expected_keys,
                original,
                replacement_key=replacement_key,
                context="after restoring the full-storage ADD inventory",
            )
            original_restored = True
        except BaseException as test_exc:
            if mutation_attempted and not original_restored:
                try:
                    restored = await self.restore_exact_contact_inventory(
                        expected_keys,
                        original,
                        replacement_key=replacement_key,
                        context=(
                            "after emergency restoration of the full-storage "
                            "ADD inventory"
                        ),
                    )
                    original_restored = True
                    self.record(
                        "full_storage_contact_add_emergency_restore",
                        original_key_prefix=original_key[:12],
                        replacement_key_prefix=replacement_key[:12],
                        original_restored=True,
                        keyset_sha256=self.keyset_digest(restored),
                    )
                except BaseException as restore_exc:
                    raise HilFailure(
                        f"full-storage contact ADD test failed ({test_exc}); "
                        f"emergency restore also failed ({restore_exc})"
                    ) from test_exc
            raise

        self.record(
            "full_storage_contact_add_recovery",
            original_key_prefix=original_key[:12],
            replacement_key_prefix=replacement_key[:12],
            fill=asdict(fill),
            add_acknowledged=True,
            reboot_error="ERR_CODE_FILE_IO_ERROR",
            uptime_before_refusal=before_uptime,
            uptime_after_refusal=after_uptime,
            live_mutation_verified=True,
            persisted_after_filler_clear=True,
            original_restored=True,
            keyset_sha256=self.keyset_digest(restored),
        )

    async def test_contact_remove_recovery_when_full(
        self, expected_keys: set[str]
    ) -> None:
        """Prove a lazily ACKed REMOVE is retained once ENOSPC is relieved."""

        before_contacts = await self.contacts()
        self.assert_exact_contacts(
            before_contacts, expected_keys, "before full-storage contact remove"
        )
        safe_targets = [
            key for key in self.generated_keys if key in before_contacts
        ]
        preferred_key = make_contact(self.config.contact_count - 2)["public_key"]
        if not safe_targets:
            raise HilFailure(
                "full-storage contact remove test found no harness-owned target; "
                "refusing to remove a pre-existing contact"
            )
        target_key = (
            preferred_key if preferred_key in safe_targets else safe_targets[0]
        )
        original = dict(before_contacts[target_key])
        original.setdefault("public_key", target_key)
        removed_keys = set(expected_keys) - {target_key}

        mutation_attempted = False
        original_restored = False
        try:
            # Prime a verified tombstone while space is available.  Removing
            # this synthetic contact at ENOSPC then takes the no-write cache
            # path instead of needing to create a new bucket tombstone.
            await self.clear_cached_advert(target_key)
            fill = await self.fill_storage_to_enospc()
            mutation_attempted = True
            self._require_event(
                await self.mc.commands.remove_contact(target_key), "OK"
            )
            live_after_ack = await self.contacts()
            self.assert_exact_contacts(
                live_after_ack,
                removed_keys,
                "after full-storage contact REMOVE ACK",
            )

            before_uptime, after_uptime = (
                await self.expect_enospc_reboot_refusal(
                    "graceful reboot with a dirty contact REMOVE at ENOSPC"
                )
            )
            live_after_refusal = await self.contacts()
            self.assert_exact_contacts(
                live_after_refusal,
                removed_keys,
                "after refused full-storage contact REMOVE reboot",
            )

            await self.clear_filler()
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            persisted_removal = await self.contacts()
            self.assert_exact_contacts(
                persisted_removal,
                removed_keys,
                "after freeing space for the contact REMOVE",
            )

            restored = await self.restore_exact_contact_inventory(
                expected_keys,
                original,
                context="after restoring the full-storage REMOVE inventory",
            )
            original_restored = True
        except BaseException as test_exc:
            if mutation_attempted and not original_restored:
                try:
                    restored = await self.restore_exact_contact_inventory(
                        expected_keys,
                        original,
                        context=(
                            "after emergency restoration of the full-storage "
                            "REMOVE inventory"
                        ),
                    )
                    original_restored = True
                    self.record(
                        "full_storage_contact_remove_emergency_restore",
                        target_key_prefix=target_key[:12],
                        original_restored=True,
                        keyset_sha256=self.keyset_digest(restored),
                    )
                except BaseException as restore_exc:
                    raise HilFailure(
                        f"full-storage contact REMOVE test failed ({test_exc}); "
                        f"emergency restore also failed ({restore_exc})"
                    ) from test_exc
            else:
                try:
                    await self.clear_filler()
                except BaseException as clear_exc:
                    raise HilFailure(
                        f"full-storage contact REMOVE test failed ({test_exc}); "
                        f"emergency filler clear also failed ({clear_exc})"
                    ) from test_exc
            raise

        self.record(
            "full_storage_contact_remove_recovery",
            target_key_prefix=target_key[:12],
            fill=asdict(fill),
            remove_acknowledged=True,
            reboot_error="ERR_CODE_FILE_IO_ERROR",
            uptime_before_refusal=before_uptime,
            uptime_after_refusal=after_uptime,
            live_mutation_verified=True,
            persisted_after_filler_clear=True,
            original_restored=True,
            keyset_sha256=self.keyset_digest(restored),
        )

    async def restore_advert_rollback_inventory(
        self,
        baseline: Dict[str, Dict[str, Any]],
        earlier_key: str,
        target_key: str,
        earlier_slot: int,
        target_slot: int,
        *,
        context: str,
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """Clear advert/filler artifacts and restore both original slots."""

        await self.clear_filler()
        current = await self.contacts()
        baseline_keys = set(baseline)
        unexpected = set(current) - baseline_keys
        missing = baseline_keys - set(current)
        if unexpected or not missing <= {earlier_key, target_key}:
            raise HilFailure(
                f"{context}: refusing ambiguous recovery; "
                f"missing={[key[:12] for key in sorted(missing)]}, "
                f"unexpected={[key[:12] for key in sorted(unexpected)]}"
            )

        # Restore in ascending slot order.  If an unexpected OK or lost reply
        # removed both contacts, this makes addContact() reclaim the same two
        # holes instead of swapping their persistent slots.
        target_was_present = target_key in current
        if target_was_present:
            await self.clear_cached_advert(target_key)
        self._require_event(
            await self.set_contact_exact(dict(baseline[earlier_key])), "OK"
        )
        self._require_event(
            await self.set_contact_exact(dict(baseline[target_key])), "OK"
        )
        if not target_was_present:
            await self.clear_cached_advert(target_key)

        await asyncio.sleep(self.config.settle_seconds)
        await self.reboot_and_reconnect()
        restored = await self.contacts()
        digests = self.assert_exact_contact_inventory(
            restored, baseline, context
        )
        restored_earlier_slot = await self.contact_slot(earlier_key)
        restored_target_slot = await self.contact_slot(target_key)
        if (
            restored_earlier_slot != earlier_slot
            or restored_target_slot != target_slot
        ):
            raise HilFailure(
                f"{context}: slots changed from {earlier_slot}/{target_slot} "
                f"to {restored_earlier_slot}/{restored_target_slot}"
            )
        return restored, digests

    async def test_advert_backed_remove_rollback(
        self, expected_keys: set[str]
    ) -> None:
        """Prove advert-cache ENOSPC removal rollback is slot-exact."""

        baseline = await self.contacts()
        deterministic_keys = self.assert_deterministic_contact_inventory(
            baseline,
            "before advert-backed contact removal rollback",
        )
        if expected_keys != deterministic_keys:
            raise HilFailure(
                "advert rollback requires the exact deterministic 350-key set"
            )
        first_key = make_contact(0)["public_key"]
        last_key = make_contact(self.config.contact_count - 1)["public_key"]
        first_slot = await self.contact_slot(first_key)
        last_slot = await self.contact_slot(last_key)
        if first_slot == last_slot:
            raise HilFailure("two deterministic contacts reported the same slot")
        if first_slot < last_slot:
            earlier_key, earlier_slot = first_key, first_slot
            target_key, target_slot = last_key, last_slot
        else:
            earlier_key, earlier_slot = last_key, last_slot
            target_key, target_slot = first_key, first_slot

        baseline_digests = self.assert_exact_contact_inventory(
            baseline, baseline, "capturing advert rollback baseline"
        )
        without_earlier = {
            key: dict(contact)
            for key, contact in baseline.items()
            if key != earlier_key
        }
        mutation_attempted = False
        original_restored = False
        try:
            mutation_attempted = True
            self._require_event(
                await self.mc.commands.remove_contact(earlier_key), "OK"
            )
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            persisted_hole = await self.contacts()
            self.assert_exact_contact_inventory(
                persisted_hole,
                without_earlier,
                "after persisting the earlier advert-rollback hole",
            )
            if await self.contact_slot(target_key) != target_slot:
                raise HilFailure("later target moved while persisting earlier hole")

            seeded_slot = await self.seed_cached_advert(target_key)
            if seeded_slot != target_slot:
                raise HilFailure(
                    f"advert seeded in slot {seeded_slot}, expected {target_slot}"
                )
            fill = await self.fill_storage_to_enospc()

            self._require_error_code(
                await self.mc.commands.remove_contact(target_key),
                5,
                "advert-backed contact removal at ENOSPC",
            )
            live_after_rollback = await self.contacts()
            self.assert_exact_contact_inventory(
                live_after_rollback,
                without_earlier,
                "after failed advert-backed contact removal",
            )
            if await self.contact_slot(target_key) != target_slot:
                raise HilFailure("failed removal changed the target slot")

            reboot_outcome, uptime_before, uptime_after = (
                await self.graceful_reboot_or_enospc_refusal()
            )
            after_reboot_attempt = await self.contacts()
            self.assert_exact_contact_inventory(
                after_reboot_attempt,
                without_earlier,
                "after advert-rollback reboot attempt",
            )
            if await self.contact_slot(target_key) != target_slot:
                raise HilFailure("reboot attempt changed the rolled-back target slot")

            restored, restored_digests = (
                await self.restore_advert_rollback_inventory(
                    baseline,
                    earlier_key,
                    target_key,
                    earlier_slot,
                    target_slot,
                    context="after restoring advert rollback baseline",
                )
            )
            original_restored = True
        except BaseException as test_exc:
            if mutation_attempted and not original_restored:
                try:
                    restored, restored_digests = (
                        await self.restore_advert_rollback_inventory(
                            baseline,
                            earlier_key,
                            target_key,
                            earlier_slot,
                            target_slot,
                            context=(
                                "after emergency advert rollback restoration"
                            ),
                        )
                    )
                    original_restored = True
                    self.record(
                        "advert_remove_rollback_emergency_restore",
                        earlier_key_prefix=earlier_key[:12],
                        target_key_prefix=target_key[:12],
                        original_restored=True,
                        **restored_digests,
                    )
                except BaseException as restore_exc:
                    raise HilFailure(
                        f"advert-backed removal rollback failed ({test_exc}); "
                        f"emergency restore also failed ({restore_exc})"
                    ) from test_exc
            raise

        if restored_digests != baseline_digests:
            raise HilFailure(
                "advert rollback restoration digest changed despite exact "
                f"inventory verification: {restored_digests} != "
                f"{baseline_digests}"
            )
        self.record(
            "advert_remove_rollback",
            earlier_key_prefix=earlier_key[:12],
            earlier_slot=earlier_slot,
            target_key_prefix=target_key[:12],
            target_slot=target_slot,
            seeded_advert_slot=seeded_slot,
            fill=asdict(fill),
            remove_error="ERR_CODE_FILE_IO_ERROR",
            live_target_and_slot_rolled_back=True,
            reboot_outcome=reboot_outcome,
            uptime_before_reboot_attempt=uptime_before,
            uptime_after_reboot_attempt=uptime_after,
            original_restored=True,
            **restored_digests,
        )

    async def select_read_failure_contacts(
        self, page: int
    ) -> tuple[str, str, str, Dict[str, int]]:
        """Choose one quarantined-page key and two readable-page keys."""

        likely = [
            page * EXPECTED_CONTACTS_PER_PAGE,
            page * EXPECTED_CONTACTS_PER_PAGE + 1,
            0,
            1,
            self.config.contact_count - 2,
            self.config.contact_count - 1,
        ]
        ordered_indexes = list(dict.fromkeys(
            likely + list(range(self.config.contact_count))
        ))
        failed_key: Optional[str] = None
        available: List[str] = []
        slots: Dict[str, int] = {}
        for index in ordered_indexes:
            key = make_contact(index)["public_key"]
            slot = await self.contact_slot(key)
            if slot // EXPECTED_CONTACTS_PER_PAGE == page:
                if failed_key is None:
                    failed_key = key
                    slots[key] = slot
            elif len(available) < 2:
                available.append(key)
                slots[key] = slot
            if failed_key is not None and len(available) == 2:
                break
        if failed_key is None or len(available) != 2:
            raise HilFailure(
                f"could not select read-failure contacts for page {page}"
            )
        return failed_key, available[0], available[1], slots

    async def recover_read_failure_baseline(
        self,
        baseline: Dict[str, Dict[str, Any]],
        expected_slots: Dict[str, int],
        *,
        context: str,
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """Consume any pending marker/incomplete boot and prove exact recovery."""

        await self.clear_filler()
        # Two reboots cover both ambiguous states after a lost host reply:
        # marker armed but not consumed, or marker consumed and load incomplete.
        await self.reboot_and_reconnect()
        await self.reboot_and_reconnect()
        recovered = await self.contacts()
        digests = self.assert_exact_contact_inventory(
            recovered, baseline, context
        )
        for key, expected_slot in expected_slots.items():
            actual_slot = await self.contact_slot(key)
            if actual_slot != expected_slot:
                raise HilFailure(
                    f"{context}: {key[:12]} moved from slot "
                    f"{expected_slot} to {actual_slot}"
                )
        status = await self.status()
        if status.filler_bytes != 0:
            raise HilFailure(
                f"{context}: filler remains at {status.filler_bytes} bytes"
            )
        return recovered, digests

    async def test_contact_page_read_failure(self, page: int) -> None:
        """Prove one unread page fails closed and recovers untouched."""

        await self.clear_filler()
        initial_status = await self.status()
        if initial_status.filler_bytes != 0:
            raise HilFailure("read-failure test requires a clear filler")
        baseline = await self.contacts()
        self.assert_deterministic_contact_inventory(
            baseline, "before contact-page read-failure injection"
        )
        baseline_digests = self.assert_exact_contact_inventory(
            baseline, baseline, "capturing contact read-failure baseline"
        )
        if await self.contact_page_mask(page) != EXPECTED_FULL_PAGE_MASK:
            raise HilFailure(
                f"contact read-failure page {page} is not fully populated"
            )
        failed_key, update_key, remove_key, expected_slots = (
            await self.select_read_failure_contacts(page)
        )

        marker_attempted = False
        recovered_exactly = False
        try:
            marker_attempted = True
            await self.arm_contact_read_failure(page)
            await self.reboot_and_reconnect()

            await self.expect_contacts_file_io(
                "GET_CONTACTS with one unread persisted page"
            )
            await self.expect_contact_not_found(
                failed_key, "contact from quarantined page"
            )
            update_original = dict(baseline[update_key])
            remove_original = dict(baseline[remove_key])
            self.assert_exact_contact_record(
                await self.contact_by_key(update_key),
                update_original,
                "available update target before blocked mutations",
            )
            self.assert_exact_contact_record(
                await self.contact_by_key(remove_key),
                remove_original,
                "available remove target before blocked mutations",
            )

            add_candidate = make_contact(self.config.contact_count + 3001)
            add_candidate["lastmod"] = max(
                int(contact["lastmod"]) for contact in baseline.values()
            ) + 1
            await self.expect_contact_not_found(
                add_candidate["public_key"],
                "new contact before blocked ADD",
            )
            self._require_error_code(
                await self.set_contact_exact(add_candidate),
                5,
                "contact ADD with an unread persisted page",
            )
            await self.expect_contact_not_found(
                add_candidate["public_key"],
                "new contact after blocked ADD",
            )

            update_candidate = dict(update_original)
            update_candidate["adv_name"] = "HIL-READFAIL-UPD"
            update_candidate["flags"] = int(update_candidate["flags"]) ^ 1
            self._require_error_code(
                await self.set_contact_exact(update_candidate),
                5,
                "contact UPDATE with an unread persisted page",
            )
            self.assert_exact_contact_record(
                await self.contact_by_key(update_key),
                update_original,
                "available update target after blocked UPDATE",
            )
            update_slot_after = await self.contact_slot(update_key)
            if update_slot_after != expected_slots[update_key]:
                raise HilFailure(
                    "blocked contact UPDATE moved the live target from slot "
                    f"{expected_slots[update_key]} to {update_slot_after}"
                )

            self._require_error_code(
                await self.mc.commands.remove_contact(remove_key),
                5,
                "contact REMOVE with an unread persisted page",
            )
            self.assert_exact_contact_record(
                await self.contact_by_key(remove_key),
                remove_original,
                "available remove target after blocked REMOVE",
            )
            remove_slot_after = await self.contact_slot(remove_key)
            if remove_slot_after != expected_slots[remove_key]:
                raise HilFailure(
                    "blocked contact REMOVE moved the live target from slot "
                    f"{expected_slots[remove_key]} to {remove_slot_after}"
                )
            self.assert_exact_contact_record(
                await self.contact_by_key(update_key),
                update_original,
                "available update target after all blocked mutations",
            )
            await self.expect_contacts_file_io(
                "GET_CONTACTS after blocked unread-page mutations"
            )
            incomplete_status = await self.status()
            if incomplete_status.filler_bytes != 0:
                raise HilFailure("read-failure boot unexpectedly created filler")

            # The marker was consumed during the incomplete boot, so one clean
            # reboot must recover the untouched source page.
            await self.reboot_and_reconnect()
            recovered = await self.contacts()
            recovered_digests = self.assert_exact_contact_inventory(
                recovered,
                baseline,
                "after contact-page read-failure recovery reboot",
            )
            for key, expected_slot in expected_slots.items():
                actual_slot = await self.contact_slot(key)
                if actual_slot != expected_slot:
                    raise HilFailure(
                        f"read-failure recovery moved {key[:12]} from slot "
                        f"{expected_slot} to {actual_slot}"
                    )
            final_status = await self.status()
            if final_status.filler_bytes != 0:
                raise HilFailure(
                    "contact read-failure recovery left filler behind"
                )
            if final_status.used_kib != initial_status.used_kib:
                raise HilFailure(
                    "contact read-failure recovery changed ExtraFS usage from "
                    f"{initial_status.used_kib} KiB to "
                    f"{final_status.used_kib} KiB"
                )
            recovered_exactly = True
        except BaseException as test_exc:
            if marker_attempted and not recovered_exactly:
                try:
                    recovered, recovered_digests = (
                        await self.recover_read_failure_baseline(
                            baseline,
                            expected_slots,
                            context=(
                                "after emergency contact read-failure recovery"
                            ),
                        )
                    )
                    recovered_exactly = True
                    self.record(
                        "contact_read_failure_emergency_recovery",
                        page=page,
                        recovered_exactly=True,
                        **recovered_digests,
                    )
                except BaseException as recover_exc:
                    raise HilFailure(
                        f"contact read-failure test failed ({test_exc}); "
                        f"emergency recovery also failed ({recover_exc})"
                    ) from test_exc
            raise

        if recovered_digests != baseline_digests:
            raise HilFailure(
                "contact read-failure recovery changed exact inventory digests"
            )
        self.record(
            "contact_read_failure_recovery",
            page=page,
            failed_key_prefix=failed_key[:12],
            update_key_prefix=update_key[:12],
            remove_key_prefix=remove_key[:12],
            expected_slots={key[:12]: slot for key, slot in expected_slots.items()},
            get_contacts_error="ERR_CODE_FILE_IO_ERROR",
            add_error="ERR_CODE_FILE_IO_ERROR",
            update_error="ERR_CODE_FILE_IO_ERROR",
            remove_error="ERR_CODE_FILE_IO_ERROR",
            partial_inventory_rejected=True,
            live_mutations_rolled_back=True,
            live_slots_rolled_back=True,
            recovered_exactly=True,
            filler_cleared=True,
            baseline_used_kib=initial_status.used_kib,
            final_used_kib=final_status.used_kib,
            **recovered_digests,
        )

    async def test_contact_page_stat_failure(self, page: int) -> None:
        """Prove a first-pass page stat error blocks the whole store safely."""

        await self.clear_filler()
        initial_status = await self.status()
        if initial_status.filler_bytes != 0:
            raise HilFailure("stat-failure test requires a clear filler")
        baseline = await self.contacts()
        self.assert_deterministic_contact_inventory(
            baseline, "before contact-page stat-failure injection"
        )
        baseline_digests = self.assert_exact_contact_inventory(
            baseline, baseline, "capturing contact stat-failure baseline"
        )
        if await self.contact_page_mask(page) != EXPECTED_FULL_PAGE_MASK:
            raise HilFailure(
                f"contact stat-failure page {page} is not fully populated"
            )
        failed_key, update_key, remove_key, expected_slots = (
            await self.select_read_failure_contacts(page)
        )

        marker_attempted = False
        recovered_exactly = False
        try:
            marker_attempted = True
            await self.arm_contact_stat_failure(page)
            await self.reboot_and_reconnect()

            await self.expect_contacts_file_io(
                "GET_CONTACTS after contact-page presence-stat failure"
            )
            add_candidate = make_contact(self.config.contact_count + 4001)
            add_candidate["lastmod"] = max(
                int(contact["lastmod"]) for contact in baseline.values()
            ) + 1
            self._require_error_code(
                await self.set_contact_exact(add_candidate),
                5,
                "contact ADD with incomplete source discovery",
            )

            update_candidate = dict(baseline[update_key])
            update_candidate["adv_name"] = "HIL-STATFAIL-UPD"
            update_candidate["flags"] = int(update_candidate["flags"]) ^ 1
            self._require_error_code(
                await self.set_contact_exact(update_candidate),
                5,
                "contact UPDATE with incomplete source discovery",
            )
            self._require_error_code(
                await self.mc.commands.remove_contact(remove_key),
                5,
                "contact REMOVE with incomplete source discovery",
            )
            await self.expect_contacts_file_io(
                "GET_CONTACTS after blocked stat-failure mutations"
            )
            incomplete_status = await self.status()
            if incomplete_status.filler_bytes != 0:
                raise HilFailure("stat-failure boot unexpectedly created filler")
            if incomplete_status.used_kib != initial_status.used_kib:
                raise HilFailure(
                    "contact stat-failure boot changed ExtraFS usage from "
                    f"{initial_status.used_kib} KiB to "
                    f"{incomplete_status.used_kib} KiB"
                )

            # The high-bit marker was consumed before source discovery, so the
            # untouched page and complete table must return after one reboot.
            await self.reboot_and_reconnect()
            recovered = await self.contacts()
            recovered_digests = self.assert_exact_contact_inventory(
                recovered,
                baseline,
                "after contact-page stat-failure recovery reboot",
            )
            for key, expected_slot in expected_slots.items():
                actual_slot = await self.contact_slot(key)
                if actual_slot != expected_slot:
                    raise HilFailure(
                        f"stat-failure recovery moved {key[:12]} from slot "
                        f"{expected_slot} to {actual_slot}"
                    )
            final_status = await self.status()
            if final_status.filler_bytes != 0:
                raise HilFailure(
                    "contact stat-failure recovery left filler behind"
                )
            if final_status.used_kib != initial_status.used_kib:
                raise HilFailure(
                    "contact stat-failure recovery changed ExtraFS usage from "
                    f"{initial_status.used_kib} KiB to "
                    f"{final_status.used_kib} KiB"
                )
            recovered_exactly = True
        except BaseException as test_exc:
            if marker_attempted and not recovered_exactly:
                try:
                    recovered, recovered_digests = (
                        await self.recover_read_failure_baseline(
                            baseline,
                            expected_slots,
                            context=(
                                "after emergency contact stat-failure recovery"
                            ),
                        )
                    )
                    recovered_exactly = True
                    self.record(
                        "contact_stat_failure_emergency_recovery",
                        page=page,
                        recovered_exactly=True,
                        **recovered_digests,
                    )
                except BaseException as recover_exc:
                    raise HilFailure(
                        f"contact stat-failure test failed ({test_exc}); "
                        f"emergency recovery also failed ({recover_exc})"
                    ) from test_exc
            raise

        if recovered_digests != baseline_digests:
            raise HilFailure(
                "contact stat-failure recovery changed exact inventory digests"
            )
        self.record(
            "contact_stat_failure_recovery",
            page=page,
            failed_key_prefix=failed_key[:12],
            update_key_prefix=update_key[:12],
            remove_key_prefix=remove_key[:12],
            expected_slots={key[:12]: slot for key, slot in expected_slots.items()},
            get_contacts_error="ERR_CODE_FILE_IO_ERROR",
            add_error="ERR_CODE_FILE_IO_ERROR",
            update_error="ERR_CODE_FILE_IO_ERROR",
            remove_error="ERR_CODE_FILE_IO_ERROR",
            whole_source_discovery_rejected=True,
            recovered_exactly=True,
            marker_storage_reclaimed=True,
            baseline_used_kib=initial_status.used_kib,
            final_used_kib=final_status.used_kib,
            **recovered_digests,
        )

    async def test_channel_persistence_when_free(self) -> None:
        await self.clear_filler()
        before = self._require_event(
            await self.mc.commands.get_channel(self.config.channel_index),
            "CHANNEL_INFO",
        ).payload
        if self.original_channel_snapshot is None:
            self.original_channel_snapshot = {
                "channel_name": before.get("channel_name", ""),
                "channel_secret": bytes(
                    before.get("channel_secret", bytes(16))
                ),
            }
        test_name = "HIL-FREE-COMMIT"
        test_secret = sha256(test_name.encode()).digest()[:16]
        self._require_event(
            await self.set_channel_exact(
                self.config.channel_index, test_name, test_secret
            ),
            "OK",
        )
        await self.reboot_and_reconnect()
        committed = self._require_event(
            await self.mc.commands.get_channel(self.config.channel_index),
            "CHANNEL_INFO",
        ).payload
        if (
            committed.get("channel_name") != test_name
            or committed.get("channel_secret") != test_secret
        ):
            raise HilFailure("channel update did not survive reboot")

        original_name = before.get("channel_name", "")
        original_secret = before.get("channel_secret", bytes(16))
        self._require_event(
            await self.set_channel_exact(
                self.config.channel_index, original_name, original_secret
            ),
            "OK",
        )
        await self.reboot_and_reconnect()
        restored = self._require_event(
            await self.mc.commands.get_channel(self.config.channel_index),
            "CHANNEL_INFO",
        ).payload
        if (
            restored.get("channel_name") != original_name
            or restored.get("channel_secret") != original_secret
        ):
            raise HilFailure("original channel did not survive restore reboot")
        self.record(
            "channel_commit_and_restore",
            channel_index=self.config.channel_index,
            persistence_verified=True,
        )

    async def restore_contact_inventory_exact(
        self,
        baseline: Dict[str, Dict[str, Any]],
        baseline_order: List[str],
        expected_slots: Dict[str, int],
        page: int,
        *,
        current: Optional[Dict[str, Dict[str, Any]]] = None,
        context: str,
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """Restore a baseline subset into its original lowest-free slots."""

        if (
            len(baseline_order) != len(baseline)
            or set(baseline_order) != set(baseline)
            or set(expected_slots) != set(baseline)
        ):
            raise HilFailure(f"{context}: incomplete baseline restoration metadata")
        if current is None:
            current = await self.contacts()
        else:
            current = {key: dict(value) for key, value in current.items()}

        unexpected = set(current) - set(baseline)
        if unexpected:
            raise HilFailure(
                f"{context}: cannot restore around unexpected contacts "
                f"{[key[:12] for key in sorted(unexpected)[:8]]}"
            )
        expected_current = {
            key: baseline[key]
            for key in current
        }
        self.assert_exact_contact_inventory(
            current, expected_current, f"{context}: surviving contacts"
        )
        expected_current_order = [
            key for key in baseline_order if key in current
        ]
        if list(current) != expected_current_order:
            raise HilFailure(
                f"{context}: surviving contacts are not in captured slot order"
            )
        await self.assert_contact_slot_map(
            current, expected_slots, f"{context}: surviving slots"
        )

        missing = sorted(
            set(baseline) - set(current), key=expected_slots.__getitem__
        )
        for key in missing:
            self._require_event(
                await self.set_contact_exact(dict(baseline[key])), "OK"
            )
            actual_slot = await self.contact_slot(key)
            expected_slot = expected_slots[key]
            if actual_slot != expected_slot:
                raise HilFailure(
                    f"{context}: restored contact {key[:12]} entered slot "
                    f"{actual_slot}, expected {expected_slot}"
                )

        await asyncio.sleep(self.config.settle_seconds)
        if await self.contact_page_mask(page) != EXPECTED_FULL_PAGE_MASK:
            raise HilFailure(
                f"{context}: restored page {page} is not fully occupied"
            )

        # The first reboot proves the rebuilt page was committed. Query every
        # slot afterward rather than inferring slot identity from key equality.
        await self.reboot_and_reconnect()
        first = await self.contacts()
        digests = self.assert_exact_contact_inventory(
            first, baseline, f"{context}: after restore reboot"
        )
        if list(first) != baseline_order:
            raise HilFailure(
                f"{context}: persistent contact stream order changed after restore"
            )
        await self.assert_contact_slot_map(
            first, expected_slots, f"{context}: restored slot map"
        )
        if await self.contact_page_mask(page) != EXPECTED_FULL_PAGE_MASK:
            raise HilFailure(
                f"{context}: full restored page {page} did not survive reboot"
            )

        # A second reboot distinguishes a durable rebuild from a RAM-only view
        # and rechecks the exact semantic/wire inventory and stream ordering.
        await self.reboot_and_reconnect()
        durable = await self.contacts()
        durable_digests = self.assert_exact_contact_inventory(
            durable, baseline, f"{context}: after durability reboot"
        )
        if list(durable) != baseline_order:
            raise HilFailure(
                f"{context}: persistent stream order changed on durability reboot"
            )
        if await self.contact_page_mask(page) != EXPECTED_FULL_PAGE_MASK:
            raise HilFailure(
                f"{context}: restored page {page} mask was not durable"
            )
        if durable_digests != digests:
            raise HilFailure(
                f"{context}: exact inventory digests changed across reboots"
            )
        return durable, durable_digests

    async def corrupt_contact_page(
        self, page: int, before_contacts: Dict[str, Dict[str, Any]]
    ) -> set[str]:
        """Prove CRC-loss isolation, then restore exact records and slots."""

        await self.clear_filler()
        initial_status = await self.status()
        if initial_status.filler_bytes != 0:
            raise HilFailure("contact-page corruption requires a clear filler")

        baseline = {
            key: dict(value) for key, value in before_contacts.items()
        }
        baseline_order = list(baseline)
        if len(baseline) != self.config.contact_count:
            raise HilFailure(
                "restorative page corruption requires a full 350-contact table"
            )
        baseline_digests = self.assert_exact_contact_inventory(
            baseline, baseline, "capturing contact-page corruption baseline"
        )
        expected_keyset = getattr(
            self.config, "expected_contact_keyset_sha256", None
        )
        if (
            expected_keyset is not None
            and baseline_digests["keyset_sha256"] != expected_keyset
        ):
            raise HilFailure(
                "contact-page corruption baseline keyset digest is "
                f"{baseline_digests['keyset_sha256']}, expected {expected_keyset}"
            )
        if await self.contact_page_mask(page) != EXPECTED_FULL_PAGE_MASK:
            raise HilFailure(
                f"contact-page corruption target page {page} is not full"
            )

        expected_slots = await self.capture_contact_slot_map(
            baseline, "before contact-page corruption"
        )
        all_slots = set(range(self.config.contact_count))
        if set(expected_slots.values()) != all_slots:
            raise HilFailure(
                "full contact inventory is not a bijection over slots 0..349"
            )
        slot_order = [
            key for key, _slot in sorted(
                expected_slots.items(), key=lambda item: item[1]
            )
        ]
        if baseline_order != slot_order:
            raise HilFailure(
                "pre-corruption contact stream is not in persistent slot order"
            )

        first = page * EXPECTED_CONTACTS_PER_PAGE
        page_items = sorted(
            (
                (slot, key)
                for key, slot in expected_slots.items()
                if slot // EXPECTED_CONTACTS_PER_PAGE == page
            )
        )
        expected_page_slots = list(
            range(first, first + EXPECTED_CONTACTS_PER_PAGE)
        )
        if [slot for slot, _key in page_items] != expected_page_slots:
            raise HilFailure(
                f"page {page} did not map to 25 contiguous populated slots"
            )
        lost_keys = {key for _slot, key in page_items}
        expected_after = {
            key: value for key, value in baseline.items() if key not in lost_keys
        }
        expected_after_order = [
            key for key in baseline_order if key not in lost_keys
        ]

        corruption_attempted = False
        restored_exactly = False
        try:
            # The write can succeed even if its reply is lost, so arm emergency
            # recovery before issuing the first destructive byte change.
            corruption_attempted = True
            reply = await self.cli(f"hil extrafs corrupt-page {page} CONFIRM")
            expected_reply = f"HIL contact page {page} CRC corrupted"
            if reply != expected_reply:
                raise HilFailure(f"contact-page corruption failed: {reply!r}")

            await self.reboot_and_reconnect()
            after_contacts = await self.contacts()
            self.assert_exact_contact_inventory(
                after_contacts,
                expected_after,
                f"after corrupting contact page {page}",
            )
            if list(after_contacts) != expected_after_order:
                raise HilFailure(
                    f"surviving stream order changed after corrupting page {page}"
                )
            for _slot, key in page_items:
                await self.expect_contact_not_found(
                    key, f"contact from CRC-invalid page {page}"
                )

            restored, restored_digests = (
                await self.restore_contact_inventory_exact(
                    baseline,
                    baseline_order,
                    expected_slots,
                    page,
                    current=after_contacts,
                    context="contact-page CRC restoration",
                )
            )
            restored_exactly = True
            final_status = await self.status()
            if final_status.filler_bytes != 0:
                raise HilFailure("contact-page restoration left filler behind")
            if final_status.used_kib != initial_status.used_kib:
                raise HilFailure(
                    "contact-page restoration changed ExtraFS usage from "
                    f"{initial_status.used_kib} KiB to "
                    f"{final_status.used_kib} KiB"
                )
        except BaseException as test_exc:
            if corruption_attempted and not restored_exactly:
                try:
                    await self.clear_filler()
                    # If the corrupt command's reply was lost, this reboot first
                    # materializes the CRC-invalid page as the expected holes.
                    # It is also safe after a partial restoration: reboot flushes
                    # acknowledged records before the resnapshot below.
                    await self.reboot_and_reconnect()
                    restored, restored_digests = (
                        await self.restore_contact_inventory_exact(
                            baseline,
                            baseline_order,
                            expected_slots,
                            page,
                            context="emergency contact-page CRC restoration",
                        )
                    )
                    recovery_status = await self.status()
                    if (
                        recovery_status.filler_bytes != 0
                        or recovery_status.used_kib != initial_status.used_kib
                    ):
                        raise HilFailure(
                            "emergency contact-page restoration changed "
                            "ExtraFS usage or left filler behind"
                        )
                    restored_exactly = True
                    self.record(
                        "contact_page_corruption_emergency_restore",
                        page=page,
                        recovered_exactly=True,
                        **restored_digests,
                    )
                except BaseException as restore_exc:
                    raise HilFailure(
                        f"contact-page corruption test failed ({test_exc}); "
                        f"emergency restoration also failed ({restore_exc})"
                    ) from test_exc
            raise

        if restored_digests != baseline_digests:
            raise HilFailure(
                "contact-page restoration changed exact inventory digests"
            )
        self.record(
            "contact_page_crc_loss_and_restore",
            page=page,
            before=len(baseline),
            isolated_after=len(expected_after),
            isolated_loss=EXPECTED_CONTACTS_PER_PAGE,
            lost_slots=expected_page_slots,
            lost_key_prefixes=[key[:12] for _slot, key in page_items],
            restored=len(restored),
            slots_restored=True,
            stream_order_restored=True,
            baseline_used_kib=initial_status.used_kib,
            final_used_kib=final_status.used_kib,
            **restored_digests,
        )
        return set(baseline)

    async def test_contact_occupied_mask_recovery(
        self, page: int, before_contacts: Dict[str, Dict[str, Any]]
    ) -> None:
        """Corrupt header-only occupancy bits and prove payload-derived repair.

        The populated-slot and invalid-high-bit cases must leave all 350
        contacts untouched.  The empty-slot case temporarily removes one
        deterministic contact from the selected page, flips that empty slot's
        header bit from zero to one, and restores the exact contact before this
        method returns.  Its emergency path makes the same restoration attempt
        after every failure following the acknowledged removal.
        """

        expected_keys = {
            make_contact(index)["public_key"]
            for index in range(self.config.contact_count)
        }
        self.assert_exact_contacts(
            before_contacts,
            expected_keys,
            "before occupied-mask corruption",
        )
        expected_digest = self.keyset_digest(expected_keys)
        initial_mask = await self.contact_page_mask(page)
        if initial_mask != EXPECTED_FULL_PAGE_MASK:
            raise HilFailure(
                f"contact page {page} starts with mask {initial_mask:08x}, "
                f"expected {EXPECTED_FULL_PAGE_MASK:08x}"
            )

        cases = (
            (0, EXPECTED_FULL_PAGE_MASK ^ 0x00000001, "valid occupied"),
            (31, EXPECTED_FULL_PAGE_MASK | 0x80000000, "invalid high"),
        )
        case_results: List[Dict[str, Any]] = []
        current_full_contacts = before_contacts
        for bit, corrupted_mask, label in cases:
            reply = await self.cli(
                f"hil extrafs corrupt-occupied {page} {bit} CONFIRM"
            )
            expected_reply = (
                f"HIL contact page {page} occupancy bit {bit} corrupted"
            )
            if reply != expected_reply:
                raise HilFailure(
                    f"contact-page occupancy corruption failed: {reply!r}"
                )
            observed_corrupt = await self.contact_page_mask(page)
            if observed_corrupt != corrupted_mask:
                raise HilFailure(
                    f"{label} bit corruption produced {observed_corrupt:08x}, "
                    f"expected {corrupted_mask:08x}"
                )

            await self.reboot_and_reconnect()
            await self.verify_contacts_after_reboot(expected_keys)
            repaired_mask = await self.wait_for_contact_page_mask(
                page, EXPECTED_FULL_PAGE_MASK
            )

            # A second reboot proves the repair itself reached ExtraFS, rather
            # than merely observing the loader's reconstructed RAM table.
            await self.reboot_and_reconnect()
            after_repair = await self.contacts()
            self.assert_exact_contacts(
                after_repair,
                expected_keys,
                f"after durable {label} mask repair",
            )
            actual_digest = self.keyset_digest(after_repair)
            if actual_digest != expected_digest:
                raise HilFailure(
                    f"{label} repair keyset digest is {actual_digest}, "
                    f"expected {expected_digest}"
                )
            durable_mask = await self.contact_page_mask(page)
            if durable_mask != EXPECTED_FULL_PAGE_MASK:
                raise HilFailure(
                    f"{label} repair did not survive reboot: {durable_mask:08x}"
                )
            case_results.append(
                {
                    "kind": label,
                    "bit": bit,
                    "corrupted_mask": f"{corrupted_mask:08x}",
                    "repaired_mask": f"{repaired_mask:08x}",
                    "durable_mask": f"{durable_mask:08x}",
                    "contact_count": len(after_repair),
                    "keyset_sha256": actual_digest,
                }
            )
            current_full_contacts = after_repair

        # Contact streams are emitted in persistent slot order after reboot.
        # Every selected page is full at this point, so the first streamed
        # contact for the page owns its valid bit zero.  Removing it creates an
        # all-zero, CRC-covered payload record whose header bit can safely be
        # changed from empty to occupied without inventing a real contact.
        page_first = page * EXPECTED_CONTACTS_PER_PAGE
        page_keys = list(current_full_contacts)[
            page_first:page_first + EXPECTED_CONTACTS_PER_PAGE
        ]
        if len(page_keys) != EXPECTED_CONTACTS_PER_PAGE:
            raise HilFailure(
                f"contact page {page} did not map to "
                f"{EXPECTED_CONTACTS_PER_PAGE} populated contacts"
            )
        empty_bit = 0
        target_key = page_keys[empty_bit]
        if target_key not in expected_keys:
            raise HilFailure(
                "empty occupancy test target is not a harness-owned "
                "deterministic contact"
            )
        original_contact = dict(current_full_contacts[target_key])
        original_contact.setdefault("public_key", target_key)
        expected_without_target = expected_keys - {target_key}
        expected_empty_mask = EXPECTED_FULL_PAGE_MASK & ~(1 << empty_bit)
        expected_empty_digest = self.keyset_digest(expected_without_target)
        removal_acknowledged = False
        original_restored = False

        async def restore_original_contact() -> Dict[str, Dict[str, Any]]:
            current = await self.contacts()
            if target_key in current:
                event = await self.mc.commands.update_contact(
                    dict(original_contact)
                )
            else:
                event = await self.mc.commands.add_contact(
                    dict(original_contact)
                )
            self._require_event(event, "OK")
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            restored = await self.contacts()
            self.assert_contact_variant(
                restored,
                expected_keys,
                target_key,
                original_contact,
                "after restoring the empty-mask contact",
            )
            restored_digest = self.keyset_digest(restored)
            if restored_digest != expected_digest:
                raise HilFailure(
                    f"restored contact keyset digest is {restored_digest}, "
                    f"expected {expected_digest}"
                )
            restored_mask = await self.contact_page_mask(page)
            if restored_mask != EXPECTED_FULL_PAGE_MASK:
                raise HilFailure(
                    f"restored contact page mask is {restored_mask:08x}, "
                    f"expected {EXPECTED_FULL_PAGE_MASK:08x}"
                )
            return restored

        try:
            self._require_event(
                await self.mc.commands.remove_contact(target_key), "OK"
            )
            removal_acknowledged = True
            await asyncio.sleep(self.config.settle_seconds)
            await self.reboot_and_reconnect()
            after_removal = await self.contacts()
            self.assert_exact_contacts(
                after_removal,
                expected_without_target,
                "after removing the empty-mask test contact",
            )
            removed_mask = await self.contact_page_mask(page)
            if removed_mask != expected_empty_mask:
                raise HilFailure(
                    f"contact removal produced page mask {removed_mask:08x}, "
                    f"expected {expected_empty_mask:08x}"
                )

            reply = await self.cli(
                f"hil extrafs corrupt-occupied {page} {empty_bit} CONFIRM"
            )
            expected_reply = (
                f"HIL contact page {page} occupancy bit {empty_bit} corrupted"
            )
            if reply != expected_reply:
                raise HilFailure(
                    f"empty contact-page occupancy corruption failed: {reply!r}"
                )
            observed_corrupt = await self.contact_page_mask(page)
            if observed_corrupt != EXPECTED_FULL_PAGE_MASK:
                raise HilFailure(
                    "empty-slot bit corruption produced "
                    f"{observed_corrupt:08x}, expected "
                    f"{EXPECTED_FULL_PAGE_MASK:08x}"
                )

            await self.reboot_and_reconnect()
            after_empty_repair = await self.contacts()
            self.assert_exact_contacts(
                after_empty_repair,
                expected_without_target,
                "after loading an empty record marked occupied",
            )
            repaired_mask = await self.wait_for_contact_page_mask(
                page, expected_empty_mask
            )

            # Reboot once more before restoring the contact so this test proves
            # that the corrected empty mask, not just the reconstructed RAM
            # table, was committed durably.
            await self.reboot_and_reconnect()
            durable_empty = await self.contacts()
            self.assert_exact_contacts(
                durable_empty,
                expected_without_target,
                "after durable empty-slot mask repair",
            )
            durable_mask = await self.contact_page_mask(page)
            if durable_mask != expected_empty_mask:
                raise HilFailure(
                    f"empty-slot repair did not survive reboot: "
                    f"{durable_mask:08x}"
                )
            durable_digest = self.keyset_digest(durable_empty)
            if durable_digest != expected_empty_digest:
                raise HilFailure(
                    f"empty-slot repair keyset digest is {durable_digest}, "
                    f"expected {expected_empty_digest}"
                )

            restored = await restore_original_contact()
            original_restored = True
            case_results.append(
                {
                    "kind": "valid empty",
                    "bit": empty_bit,
                    "removed_key_prefix": target_key[:12],
                    "before_corruption_mask": f"{expected_empty_mask:08x}",
                    "corrupted_mask": f"{EXPECTED_FULL_PAGE_MASK:08x}",
                    "repaired_mask": f"{repaired_mask:08x}",
                    "durable_mask": f"{durable_mask:08x}",
                    "contact_count": len(durable_empty),
                    "keyset_sha256": durable_digest,
                    "restored_contact_count": len(restored),
                    "restored_mask": f"{EXPECTED_FULL_PAGE_MASK:08x}",
                }
            )
        except BaseException as test_exc:
            if removal_acknowledged and not original_restored:
                try:
                    await restore_original_contact()
                    original_restored = True
                    self.record(
                        "contact_occupied_mask_emergency_restore",
                        page=page,
                        bit=empty_bit,
                        target_key_prefix=target_key[:12],
                        original_restored=True,
                    )
                except BaseException as restore_exc:
                    raise HilFailure(
                        f"empty occupancy test failed ({test_exc}); emergency "
                        f"contact restore also failed ({restore_exc})"
                    ) from test_exc
            raise

        self.record(
            "contact_occupied_mask_recovery",
            page=page,
            expected_mask=f"{EXPECTED_FULL_PAGE_MASK:08x}",
            contacts_preserved=len(expected_keys),
            keyset_sha256=expected_digest,
            original_contact_restored=original_restored,
            cases=case_results,
        )

    async def cleanup_generated_contacts(self) -> None:
        await self.clear_filler()
        present = await self.contacts()
        synthetic_keys = {
            make_contact(index)["public_key"]
            for index in range(self.config.contact_count)
        }
        expected_remaining = set(present) - synthetic_keys
        if self.baseline_contact_keys:
            expected_remaining = set(self.baseline_contact_keys)
        removed = 0
        for public_key in sorted(set(present) & synthetic_keys):
            if public_key not in present:
                continue
            self._require_event(
                await self.mc.commands.remove_contact(public_key), "OK"
            )
            removed += 1
            if removed % 25 == 0:
                self.progress(f"contacts removed: {removed}")
        await asyncio.sleep(self.config.settle_seconds)
        await self.reboot_and_reconnect()
        remaining_contacts = await self.contacts()
        self.assert_exact_contacts(
            remaining_contacts,
            expected_remaining,
            "after synthetic-contact cleanup",
        )
        self.record(
            "cleanup",
            removed=removed,
            remaining=len(remaining_contacts),
            keyset_sha256=self.keyset_digest(remaining_contacts),
            filler_cleared=True,
        )

    async def restore_test_channel(self) -> None:
        if self.original_channel_snapshot is None:
            return
        await self.clear_filler()
        expected = self.original_channel_snapshot
        current = self._require_event(
            await self.mc.commands.get_channel(self.config.channel_index),
            "CHANNEL_INFO",
        ).payload
        if (
            current.get("channel_name") != expected["channel_name"]
            or current.get("channel_secret") != expected["channel_secret"]
        ):
            self._require_event(
                await self.set_channel_exact(
                    self.config.channel_index,
                    expected["channel_name"],
                    expected["channel_secret"],
                ),
                "OK",
            )
        self.record(
            "test_channel_restore_staged",
            channel_index=self.config.channel_index,
        )

    async def comprehensive(self) -> None:
        # Recover cleanly from an earlier interrupted ENOSPC run before adding
        # contacts.  This is destructive and therefore occurs only after both
        # command-line consent gates have passed.
        await self.clear_filler()
        await self.status()
        self_info = self._require_event(
            await self.mc.commands.send_appstart(), "SELF_INFO"
        ).payload
        autoadd = self._require_event(
            await self.mc.commands.get_autoadd_config(), "AUTOADD_CONFIG"
        ).payload
        self.original_manual_add_contacts = bool(
            self_info.get("manual_add_contacts", False)
        )
        self.original_autoadd_config = int(autoadd.get("config", 0))
        try:
            if not self.original_manual_add_contacts:
                await self.set_manual_contact_mode(True)
            if self.original_autoadd_config != 0:
                await self.set_autoadd_config(0)
            expected_contacts = await self.add_contacts_to_capacity()

            # A RAM count is not persistence proof.  Reboot while there is
            # still free space before creating the ENOSPC condition.
            await self.reboot_and_reconnect()
            await self.verify_contacts_after_reboot(expected_contacts)

            if self.config.unsafe_reset:
                await self.reboot_and_reconnect(unsafe=True)
                await self.verify_contacts_after_reboot(expected_contacts)

            fill, original_channel = await self.fill_and_test_channel_atomicity()
            await self.reboot_and_reconnect()
            await self.verify_contacts_after_reboot(expected_contacts)
            await self.verify_full_state_after_reboot(fill, original_channel)

            await self.test_contact_update_recovery_when_full(expected_contacts)
            await self.test_contact_add_recovery_when_full(expected_contacts)
            await self.test_contact_remove_recovery_when_full(expected_contacts)
            if getattr(self.config, "advert_remove_rollback", False):
                await self.test_advert_backed_remove_rollback(expected_contacts)
            if getattr(self.config, "fail_read_page", None) is not None:
                await self.test_contact_page_read_failure(
                    self.config.fail_read_page
                )
            if getattr(self.config, "fail_stat_page", None) is not None:
                await self.test_contact_page_stat_failure(
                    self.config.fail_stat_page
                )

            await self.test_channel_persistence_when_free()
            if self.config.corrupt_occupied_page is not None:
                await self.test_contact_occupied_mask_recovery(
                    self.config.corrupt_occupied_page, await self.contacts()
                )
            if self.config.corrupt_page is not None:
                before_corruption = await self.contacts()
                expected_contacts = await self.corrupt_contact_page(
                    self.config.corrupt_page, before_corruption
                )
                self.expected_contact_keys = expected_contacts
            await self.status()
        finally:
            active_failure = sys.exc_info()[0] is not None
            restore_errors: List[str] = []

            if self.mc is not None and self.config.cleanup:
                try:
                    await self.restore_test_channel()
                except BaseException as restore_exc:
                    restore_errors.append(f"channel/filler: {restore_exc}")
                try:
                    await self.cleanup_generated_contacts()
                except BaseException as restore_exc:
                    restore_errors.append(f"contacts: {restore_exc}")

            if self.mc is not None and self.original_autoadd_config is not None:
                try:
                    current = self._require_event(
                        await self.mc.commands.get_autoadd_config(),
                        "AUTOADD_CONFIG",
                    ).payload
                    if int(current.get("config", -1)) != self.original_autoadd_config:
                        await self.set_autoadd_config(self.original_autoadd_config)
                except BaseException as restore_exc:
                    restore_errors.append(f"autoadd config: {restore_exc}")

            if self.mc is not None and self.original_manual_add_contacts is not None:
                try:
                    current = self._require_event(
                        await self.mc.commands.send_appstart(), "SELF_INFO"
                    ).payload
                    if bool(current.get("manual_add_contacts")) != (
                        self.original_manual_add_contacts
                    ):
                        await self.set_manual_contact_mode(
                            self.original_manual_add_contacts
                        )
                except BaseException as restore_exc:
                    restore_errors.append(f"manual contact mode: {restore_exc}")

            if restore_errors:
                self.record(
                    "contact_isolation_restore_failed",
                    errors=restore_errors,
                )
                if not active_failure:
                    raise HilFailure("; ".join(restore_errors))


async def run(config: RunConfig) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "schema_version": 1,
        "tool": "t1000e_extrafs_stress",
        "ok": False,
        "config": _jsonable(asdict(config)),
        "locked_identity": {
            "usb_serial": EXPECTED_USB_SERIAL,
            "expected_node_key_prefix": config.expected_node_key_prefix,
            "known_pre_erase_node_key_prefix": KNOWN_PRE_ERASE_NODE_KEY_PREFIX,
            "vid_pid": f"{EXPECTED_USB_VID:04X}:{EXPECTED_USB_PID:04X}",
            "interface": EXPECTED_USB_INTERFACE,
        },
        "steps": [],
        "failure": None,
    }
    runner: Optional[StressRunner] = None
    try:
        require_destructive_consent(config)
        identity = resolve_initial_port(config.port)
        runner = StressRunner(config, identity)
        await runner.connect()
        if config.scenario == "inspect":
            await runner.inspect()
        else:
            await runner.comprehensive()
        result["ok"] = True
    except BaseException as exc:
        result["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        if runner is not None:
            result["steps"] = runner.steps
            try:
                await runner.disconnect()
            except BaseException as exc:
                if result["failure"] is None:
                    result["failure"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                    result["ok"] = False
    return result


def _positive_int(value: str) -> int:
    parsed = int(value, 10)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_page(value: str) -> int:
    parsed = int(value, 10)
    if not 0 <= parsed <= 13:
        raise argparse.ArgumentTypeError("must be in 0..13")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="initial Companion port (for example COM23)")
    parser.add_argument(
        "--scenario",
        choices=("inspect", "comprehensive"),
        default="inspect",
        help="inspect is read-only and is the default",
    )
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument(
        "--confirm-usb-serial",
        help="must exactly name the locked spare for any destructive plan",
    )
    parser.add_argument(
        "--expected-node-key-prefix",
        help=(
            "optional data-identity check; omit after an intentional erase "
            "because erasing regenerates the node key"
        ),
    )
    parser.add_argument("--contact-count", type=_positive_int, default=350)
    parser.add_argument("--fill-bytes", type=_positive_int, default=131072)
    parser.add_argument("--channel-index", type=int, default=39)
    parser.add_argument("--settle-seconds", type=_positive_float, default=7.0)
    parser.add_argument("--reenum-timeout", type=_positive_float, default=35.0)
    parser.add_argument("--allow-existing-contacts", action="store_true")
    parser.add_argument(
        "--contact-enumeration-passes",
        type=_positive_int,
        metavar="N",
        help=(
            "run N validated full, unfiltered contact-list passes at each "
            "inventory/reboot checkpoint; retries are counted and make the "
            "stress result fail"
        ),
    )
    parser.add_argument(
        "--expected-contact-keyset-sha256",
        metavar="HEX64",
        help=(
            "optional expected digest of sorted contact public keys; useful "
            "for anchoring a read-only enumeration run"
        ),
    )
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument(
        "--corrupt-page",
        type=_nonnegative_page,
        metavar="0..13",
        help=(
            "corrupt one persisted contact page, verify isolated loss, then "
            "restore its exact records, slots, and stream order"
        ),
    )
    parser.add_argument(
        "--corrupt-occupied-page",
        type=_nonnegative_page,
        metavar="0..13",
        help=(
            "also flip populated, empty, and invalid occupied-mask bits, then "
            "verify payload-derived recovery, durable header repair, and exact "
            "contact restoration; any existing contacts must be the harness's "
            "deterministic set"
        ),
    )
    parser.add_argument(
        "--unsafe-reset",
        action="store_true",
        help=(
            "exercise the persistence-bypassing reset while USB is awake; "
            "this is not an event-driven sleep test"
        ),
    )
    parser.add_argument(
        "--advert-remove-rollback",
        action="store_true",
        help=(
            "seed a real cached advert and prove its ENOSPC removal rolls "
            "back the live contact and persistent slot exactly; requires the "
            "deterministic 350-contact inventory"
        ),
    )
    parser.add_argument(
        "--fail-read-page",
        type=_nonnegative_page,
        metavar="0..13",
        help=(
            "inject one non-destructive contact-page read failure on the next "
            "boot and prove fail-closed enumeration/mutations plus exact "
            "recovery on the following boot"
        ),
    )
    parser.add_argument(
        "--fail-stat-page",
        type=_nonnegative_page,
        metavar="0..13",
        help=(
            "inject one non-destructive contact-page presence-stat failure on "
            "the next boot and prove source discovery/mutations fail closed "
            "plus exact recovery on the following boot"
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run offline parser/safety tests; never enumerate or open hardware",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> RunConfig:
    # getattr keeps config construction compatible with callers that build a
    # Namespace rather than obtaining it from build_argument_parser().
    corrupt_occupied_page = getattr(args, "corrupt_occupied_page", None)
    advert_remove_rollback = bool(
        getattr(args, "advert_remove_rollback", False)
    )
    fail_read_page = getattr(args, "fail_read_page", None)
    fail_stat_page = getattr(args, "fail_stat_page", None)
    if not args.port:
        raise HilFailure("--port is required unless --self-test is used")
    if not 0 <= args.channel_index <= 39:
        raise HilFailure("--channel-index must be in 0..39")
    if args.contact_count != 350:
        raise HilFailure(
            "this T1000-E capacity harness requires --contact-count 350"
        )
    if args.expected_node_key_prefix:
        prefix = args.expected_node_key_prefix
        if len(prefix) < 8 or len(prefix) > 64 or len(prefix) % 2:
            raise HilFailure(
                "--expected-node-key-prefix must be 8..64 hexadecimal "
                "characters with even length"
            )
        try:
            bytes.fromhex(prefix)
        except ValueError as exc:
            raise HilFailure(
                "--expected-node-key-prefix must be hexadecimal"
            ) from exc
    if args.fill_bytes > 131072:
        raise HilFailure("--fill-bytes must not exceed the firmware HIL limit 131072")
    if fail_read_page is not None and not 0 <= fail_read_page <= 13:
        raise HilFailure("--fail-read-page must be in 0..13")
    if fail_stat_page is not None and not 0 <= fail_stat_page <= 13:
        raise HilFailure("--fail-stat-page must be in 0..13")
    if fail_read_page is not None and fail_stat_page is not None:
        raise HilFailure(
            "--fail-read-page and --fail-stat-page are mutually exclusive"
        )
    expected_keyset_digest = None
    if args.expected_contact_keyset_sha256 is not None:
        expected_keyset_digest = args.expected_contact_keyset_sha256.lower()
        if args.contact_enumeration_passes is None:
            raise HilFailure(
                "--expected-contact-keyset-sha256 requires "
                "--contact-enumeration-passes"
            )
        if len(expected_keyset_digest) != 64:
            raise HilFailure(
                "--expected-contact-keyset-sha256 must be 64 hexadecimal characters"
            )
        try:
            bytes.fromhex(expected_keyset_digest)
        except ValueError as exc:
            raise HilFailure(
                "--expected-contact-keyset-sha256 must be hexadecimal"
            ) from exc
    if (
        args.corrupt_page is not None
        or corrupt_occupied_page is not None
        or advert_remove_rollback
        or fail_read_page is not None
        or fail_stat_page is not None
        or args.unsafe_reset
    ) and (
        args.scenario != "comprehensive"
    ):
        raise HilFailure(
            "corruption, read-failure, advert rollback, and unsafe-reset "
            "options require --scenario comprehensive"
        )
    if args.cleanup and args.scenario != "comprehensive":
        raise HilFailure("--cleanup requires --scenario comprehensive")
    return RunConfig(
        port=args.port,
        scenario=args.scenario,
        allow_destructive=args.allow_destructive,
        confirm_usb_serial=args.confirm_usb_serial,
        expected_node_key_prefix=args.expected_node_key_prefix,
        contact_count=args.contact_count,
        fill_bytes=args.fill_bytes,
        channel_index=args.channel_index,
        settle_seconds=args.settle_seconds,
        reenum_timeout=args.reenum_timeout,
        allow_existing_contacts=args.allow_existing_contacts,
        cleanup=args.cleanup,
        corrupt_page=args.corrupt_page,
        corrupt_occupied_page=corrupt_occupied_page,
        unsafe_reset=args.unsafe_reset,
        contact_enumeration_passes=args.contact_enumeration_passes,
        expected_contact_keyset_sha256=expected_keyset_digest,
        verbose=args.verbose,
        advert_remove_rollback=advert_remove_rollback,
        fail_read_page=fail_read_page,
        fail_stat_page=fail_stat_page,
    )


def offline_self_test() -> Dict[str, Any]:
    """Exercise parsers and safety gates without importing serial libraries."""

    checks: List[str] = []
    status = parse_extrafs_status(
        "HIL ExtraFS active=1 used=73KiB total=100KiB fill=12345"
    )
    assert status == ExtraFsStatus(True, 73, 100, 12345)
    checks.append("status_parser")

    fill = parse_extrafs_fill(
        "HIL ExtraFS fill requested=131072 written=20480 "
        "used=100KiB total=100KiB"
    )
    assert fill == ExtraFsFill(131072, 20480, 100, 100)
    checks.append("fill_parser")

    page_mask = parse_contact_page_mask(
        "HIL contact page 13 occupied=81fF00a5"
    )
    assert page_mask == ContactPageMask(13, 0x81FF00A5)
    checks.append("contact_page_mask_parser")

    assert parse_contact_slot("HIL contact slot=349") == 349
    assert parse_advert_seed_slot("HIL advert seeded slot=12") == 12
    checks.append("advert_hil_reply_parsers")

    for malformed in (
        "HIL ExtraFS active=1 used=101KiB total=100KiB fill=0",
        "not a HIL response",
    ):
        try:
            parse_extrafs_status(malformed)
        except HilFailure:
            pass
        else:
            raise AssertionError(f"status parser accepted {malformed!r}")
    checks.append("malformed_status_rejected")

    first = make_contact(0)
    second = make_contact(1)
    assert len(first["public_key"]) == 64
    assert first["public_key"] != second["public_key"]
    checks.append("deterministic_contacts")

    first["lastmod"] = 1_800_000_000
    exact_frame = encode_exact_contact_frame(first)
    assert len(exact_frame) == 148
    assert int.from_bytes(exact_frame[-4:], "little") == first["lastmod"]
    checks.append("exact_contact_frame")

    safe = RunConfig(
        "COM23", "inspect", False, None, None, 350, 131072, 39, 7, 35,
        False, False, None, False, None, None, False,
    )
    require_destructive_consent(safe)
    denied = RunConfig(
        "COM23", "comprehensive", False, None, None, 350, 131072, 39, 7,
        35, False, False, None, False, None, None, False,
    )
    try:
        require_destructive_consent(denied)
    except HilFailure:
        pass
    else:
        raise AssertionError("destructive plan passed without consent")
    allowed = RunConfig(
        "COM23", "comprehensive", True, EXPECTED_USB_SERIAL, None, 350,
        131072, 39, 7, 35, False, False, None, False, None, None, False,
    )
    require_destructive_consent(allowed)
    checks.append("destructive_consent_gate")

    identity = PortIdentity(
        "COM23", EXPECTED_USB_SERIAL, EXPECTED_USB_VID, EXPECTED_USB_PID,
        "MeshCore T1000-E", "MeshCore T1000-E", "", "1-8:x.0",
        f"USB VID:PID={EXPECTED_USB_VID:04X}:{EXPECTED_USB_PID:04X}",
    )
    validate_runtime_identity(identity, requested_port="com23")
    wrong = PortIdentity(
        "COM23", "WRONG", EXPECTED_USB_VID, EXPECTED_USB_PID,
        "MeshCore T1000-E", "", "", "1-8", "USB MI_00",
    )
    try:
        validate_runtime_identity(wrong, requested_port="COM23")
    except HilFailure:
        pass
    else:
        raise AssertionError("wrong USB serial passed the identity gate")
    checks.append("usb_identity_gate")

    parser = build_argument_parser()
    parsed = parser.parse_args(["--port", "COM23"])
    assert (
        parsed.scenario == "inspect"
        and not parsed.allow_destructive
        and not parsed.advert_remove_rollback
        and parsed.fail_read_page is None
        and parsed.fail_stat_page is None
        and parsed.contact_enumeration_passes is None
    )
    checks.append("read_only_default")

    stress_args = parser.parse_args(
        [
            "--port",
            "COM23",
            "--contact-enumeration-passes",
            "5",
            "--expected-contact-keyset-sha256",
            "ab" * 32,
        ]
    )
    stress_config = config_from_args(stress_args)
    assert stress_config.contact_enumeration_passes == 5
    assert stress_config.expected_contact_keyset_sha256 == "ab" * 32
    require_destructive_consent(stress_config)
    checks.append("read_only_enumeration_config")

    read_failure_args = parser.parse_args(
        [
            "--port",
            "COM23",
            "--scenario",
            "comprehensive",
            "--fail-read-page",
            "13",
        ]
    )
    read_failure_config = config_from_args(read_failure_args)
    assert read_failure_config.fail_read_page == 13
    checks.append("read_failure_config")
    stat_failure_args = parser.parse_args(
        [
            "--port",
            "COM23",
            "--scenario",
            "comprehensive",
            "--fail-stat-page",
            "13",
        ]
    )
    stat_failure_config = config_from_args(stat_failure_args)
    assert stat_failure_config.fail_stat_page == 13
    checks.append("stat_failure_config")
    return {"ok": True, "checks": checks}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        result = offline_self_test()
        print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    try:
        config = config_from_args(args)
        result = asyncio.run(run(config))
    except BaseException as exc:
        result = {
            "schema_version": 1,
            "tool": "t1000e_extrafs_stress",
            "ok": False,
            "steps": [],
            "failure": {"type": type(exc).__name__, "message": str(exc)},
        }
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
