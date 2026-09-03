#!/usr/bin/env python3
"""Destructive, identity-gated T1000-E USB contact-protocol stress test.

This focused HIL test assumes the spare T1000-E already contains the 350
deterministic contacts created by ``t1000e_extrafs_stress.py``.  It sends no
LoRa traffic.  Every operation is a local USB Companion command.

The test rejects the requested forbidden-length cases and representative
invalid-type/path encodings for ``CMD_ADD_UPDATE_CONTACT``, checking the
complete contact inventory after each rejection.  It then removes and re-adds
one deterministic contact while a paced ``GET_CONTACTS`` response is in
flight.  A raw response observer proves the firmware emits a new
``CONTACTS_START`` and that the final completed transaction and the SDK
aggregation are both internally consistent.

Example (the confirmations are intentionally verbose)::

    python tools/hil/t1000e_contact_protocol_stress.py \
      --port COM23 --allow-destructive \
      --confirm-usb-serial 34A9141999729D5D \
      --expected-node-key-prefix 01234567 \
      --expected-contact-keyset-sha256 \
      74f58d5b6b29bb7a95655a667768d803b7c12121eee5cab836ebd1da992b8b7c

Replace ``01234567`` with the current node public-key prefix printed by the
read-only inspection step.  It is deliberately supplied at run time because
a full erase regenerates the node identity.

Progress is written to stderr.  The single stdout value is a JSON result.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence


# The sibling harness owns the already-audited USB VID/PID/interface/location
# checks and the strict contact-list retry logic.  Loading it rather than
# duplicating those safety gates keeps both destructive tools fail-closed in
# exactly the same way.
try:
    import t1000e_extrafs_stress as BASE
except ModuleNotFoundError:  # Supports importlib-based unit-test loading.
    _HIL_DIR = str(Path(__file__).resolve().parent)
    if _HIL_DIR not in sys.path:
        sys.path.insert(0, _HIL_DIR)
    import t1000e_extrafs_stress as BASE


EXPECTED_USB_SERIAL = "34A9141999729D5D"
EXPECTED_CONTACT_COUNT = 350
MIN_RESTORE_FREE_KIB = 8
EXPECTED_KEYSET_SHA256 = (
    "74f58d5b6b29bb7a95655a667768d803b7c12121eee5cab836ebd1da992b8b7c"
)
DEFAULT_MUTATION_INDEX = EXPECTED_CONTACT_COUNT - 1
ERR_CODE_ILLEGAL_ARG = 6

CMD_GET_CONTACTS = 4
CMD_ADD_UPDATE_CONTACT = 9
CMD_REMOVE_CONTACT = 15
RESP_CODE_CONTACTS_START = 2
RESP_CODE_CONTACT = 3
RESP_CODE_END_OF_CONTACTS = 4

CONTACT_UPDATE_MIN_LEN = 136
CONTACT_UPDATE_GPS_LEN = 144
CONTACT_UPDATE_LASTMOD_LEN = 148
FORBIDDEN_CONTACT_UPDATE_LENGTHS = (
    135,
    *range(137, 144),
    *range(145, 148),
)
INVALID_CONTACT_TYPES = (0, 5, 255)
# 0x7f and 0xbf encode paths larger than the 64-byte path buffer.  0xc0
# selects the reserved four-byte hash size.
INVALID_PATH_LENGTHS = (0x7F, 0xBF, 0xC0)

SEMANTIC_CONTACT_FIELDS = (
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
)
WIRE_CONTACT_FIELDS = SEMANTIC_CONTACT_FIELDS + ("lastmod",)


class ProtocolHilFailure(RuntimeError):
    """Raised when an identity, protocol, or restoration invariant fails."""


@dataclass(frozen=True)
class Config:
    port: str
    allow_destructive: bool
    confirm_usb_serial: str
    expected_node_key_prefix: str
    expected_contact_keyset_sha256: str
    mutation_index: int = DEFAULT_MUTATION_INDEX
    stream_trigger_contacts: int = 12
    settle_seconds: float = 7.0
    verbose: bool = False


@dataclass(frozen=True)
class MalformedCase:
    name: str
    frame: bytes


@dataclass
class StreamSegment:
    advertised_count: int
    start_frame_index: int
    contact_keys: List[str] = field(default_factory=list)
    ended: bool = False
    end_frame_index: Optional[int] = None


class ContactStreamObserver:
    """Observe the raw response frames hidden by meshcore's SDK aggregator."""

    def __init__(self, trigger_contacts: int) -> None:
        self.trigger_contacts = trigger_contacts
        self.frame_count = 0
        self.segments: List[StreamSegment] = []
        self.ready = asyncio.Event()
        self.ended = asyncio.Event()
        self.background_failure: Optional[BaseException] = None

    def observe(self, raw: bytes) -> None:
        self.frame_count += 1
        if not raw:
            return
        packet_type = raw[0]
        if packet_type == RESP_CODE_CONTACTS_START:
            if len(raw) != 5:
                raise ProtocolHilFailure(
                    f"CONTACTS_START has {len(raw)} bytes, expected 5"
                )
            if self.segments and self.segments[-1].ended:
                raise ProtocolHilFailure(
                    "unexpected CONTACTS_START after a completed transaction"
                )
            self.segments.append(
                StreamSegment(
                    int.from_bytes(raw[1:5], "little"), self.frame_count
                )
            )
            return
        if packet_type == RESP_CODE_CONTACT:
            if len(raw) != 148:
                raise ProtocolHilFailure(
                    f"CONTACT has {len(raw)} bytes, expected 148"
                )
            if not self.segments or self.segments[-1].ended:
                raise ProtocolHilFailure(
                    "CONTACT arrived without a valid CONTACTS_START"
                )
            current = self.segments[-1]
            current.contact_keys.append(raw[1:33].hex())
            if (
                len(self.segments) == 1
                and len(current.contact_keys) >= self.trigger_contacts
            ):
                self.ready.set()
            return
        if packet_type == RESP_CODE_END_OF_CONTACTS:
            if len(raw) != 5:
                raise ProtocolHilFailure(
                    f"END_OF_CONTACTS has {len(raw)} bytes, expected 5"
                )
            if not self.segments or self.segments[-1].ended:
                raise ProtocolHilFailure(
                    "END_OF_CONTACTS arrived without CONTACTS_START"
                )
            current = self.segments[-1]
            current.ended = True
            current.end_frame_index = self.frame_count
            self.ended.set()

    def validate_restart(
        self, expected_keys: set[str], mutation_after_frame: int
    ) -> Dict[str, Any]:
        fresh = [
            segment
            for segment in self.segments
            if segment.start_frame_index > mutation_after_frame
        ]
        if not fresh:
            raise ProtocolHilFailure(
                "structural mutation did not produce a fresh CONTACTS_START"
            )
        final = self.segments[-1]
        if final not in fresh or not final.ended:
            raise ProtocolHilFailure(
                "the final restarted contact-list transaction did not reach "
                "END_OF_CONTACTS"
            )
        actual_keys = set(final.contact_keys)
        if final.advertised_count != len(expected_keys):
            raise ProtocolHilFailure(
                f"restarted stream advertised {final.advertised_count}, "
                f"expected {len(expected_keys)}"
            )
        if len(final.contact_keys) != final.advertised_count:
            raise ProtocolHilFailure(
                f"restarted stream emitted {len(final.contact_keys)} contacts, "
                f"advertised {final.advertised_count}"
            )
        if len(actual_keys) != len(final.contact_keys):
            raise ProtocolHilFailure("restarted stream contains duplicate keys")
        if actual_keys != expected_keys:
            raise ProtocolHilFailure("restarted stream has the wrong contact keyset")
        return {
            "starts": len(self.segments),
            "fresh_starts": len(fresh),
            "start_counts": [segment.advertised_count for segment in self.segments],
            "aborted_contact_counts": [
                len(segment.contact_keys)
                for segment in self.segments
                if not segment.ended
            ],
            "completed_count": len(final.contact_keys),
        }


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def require_consent(config: Config) -> None:
    if not config.allow_destructive:
        raise ProtocolHilFailure("test requires --allow-destructive")
    if config.confirm_usb_serial.upper() != EXPECTED_USB_SERIAL:
        raise ProtocolHilFailure(
            "test requires --confirm-usb-serial " + EXPECTED_USB_SERIAL
        )
    node_prefix = config.expected_node_key_prefix.strip().lower()
    if not 8 <= len(node_prefix) <= 64:
        raise ProtocolHilFailure(
            "test requires an 8..64 character --expected-node-key-prefix"
        )
    try:
        bytes.fromhex(node_prefix)
    except ValueError as exc:
        raise ProtocolHilFailure(
            "--expected-node-key-prefix must be hexadecimal"
        ) from exc
    if (
        config.expected_contact_keyset_sha256.lower()
        != EXPECTED_KEYSET_SHA256
    ):
        raise ProtocolHilFailure(
            "test requires --expected-contact-keyset-sha256 "
            + EXPECTED_KEYSET_SHA256
        )
    if not 0 <= config.mutation_index < EXPECTED_CONTACT_COUNT:
        raise ProtocolHilFailure("--mutation-index must be in 0..349")
    if not 1 <= config.stream_trigger_contacts <= 100:
        raise ProtocolHilFailure(
            "--stream-trigger-contacts must be in 1..100"
        )
    if config.settle_seconds <= 0:
        raise ProtocolHilFailure("--settle-seconds must be greater than zero")


def deterministic_keys() -> set[str]:
    return {
        BASE.make_contact(index)["public_key"]
        for index in range(EXPECTED_CONTACT_COUNT)
    }


def keyset_digest(keys: Iterable[str]) -> str:
    return sha256("\n".join(sorted(keys)).encode("ascii")).hexdigest()


def _contact_projection(
    contact: Dict[str, Any], fields: Sequence[str]
) -> Dict[str, Any]:
    return {field: contact.get(field) for field in fields}


def semantic_digest(contacts: Dict[str, Dict[str, Any]]) -> str:
    canonical = [
        _contact_projection(contacts[key], SEMANTIC_CONTACT_FIELDS)
        for key in sorted(contacts)
    ]
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def wire_snapshot_digest(contacts: Dict[str, Dict[str, Any]]) -> str:
    """Digest every contact field emitted on GET_CONTACTS, including lastmod."""

    canonical = [
        _contact_projection(contacts[key], WIRE_CONTACT_FIELDS)
        for key in sorted(contacts)
    ]
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def expected_semantic_digest() -> str:
    contacts = {
        contact["public_key"]: contact
        for contact in (
            BASE.make_contact(index) for index in range(EXPECTED_CONTACT_COUNT)
        )
    }
    return semantic_digest(contacts)


def validate_exact_snapshot(
    contacts: Dict[str, Dict[str, Any]], *, require_canonical_fields: bool
) -> Dict[str, str | int]:
    expected = deterministic_keys()
    actual = set(contacts)
    digest = keyset_digest(actual)
    if len(contacts) != EXPECTED_CONTACT_COUNT:
        raise ProtocolHilFailure(
            f"contact inventory has {len(contacts)}, expected 350"
        )
    if actual != expected or digest != EXPECTED_KEYSET_SHA256:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ProtocolHilFailure(
            "contact inventory is not the deterministic 350-key table; "
            f"digest={digest}, missing={[key[:12] for key in missing[:4]]}, "
            f"extra={[key[:12] for key in extra[:4]]}"
        )
    content = semantic_digest(contacts)
    if require_canonical_fields and content != expected_semantic_digest():
        raise ProtocolHilFailure(
            "deterministic keys have unexpected contact fields; "
            f"semantic digest={content}"
        )
    return {
        "count": len(contacts),
        "keyset_sha256": digest,
        "semantic_sha256": content,
        "wire_sha256": wire_snapshot_digest(contacts),
    }


def encode_contact_update(
    contact: Dict[str, Any], *, include_lastmod: bool = True
) -> bytes:
    """Encode the firmware's fixed-width local contact update structure."""

    public_key = bytes.fromhex(contact["public_key"])
    if len(public_key) != 32:
        raise ProtocolHilFailure("contact public key must be exactly 32 bytes")
    path_len = int(contact["out_path_len"])
    if path_len < 0:
        encoded_path_len = 0xFF
    else:
        mode = int(contact["out_path_hash_mode"])
        encoded_path_len = (path_len & 0x3F) | ((mode & 0x03) << 6)
    path = bytes.fromhex(_clean(contact.get("out_path")))
    if len(path) > 64:
        raise ProtocolHilFailure("contact path exceeds 64 bytes")
    name = _clean(contact.get("adv_name")).encode("utf-8")[:32]
    frame = bytearray((CMD_ADD_UPDATE_CONTACT,))
    frame.extend(public_key)
    frame.extend(
        (
            int(contact["type"]) & 0xFF,
            int(contact["flags"]) & 0xFF,
            encoded_path_len,
        )
    )
    frame.extend(path.ljust(64, b"\0"))
    frame.extend(name.ljust(32, b"\0"))
    frame.extend(struct.pack("<I", int(contact["last_advert"])))
    frame.extend(struct.pack("<i", int(float(contact["adv_lat"]) * 1_000_000)))
    frame.extend(struct.pack("<i", int(float(contact["adv_lon"]) * 1_000_000)))
    if include_lastmod:
        frame.extend(struct.pack("<I", int(contact.get("lastmod", 0))))
    expected_length = (
        CONTACT_UPDATE_LASTMOD_LEN if include_lastmod else CONTACT_UPDATE_GPS_LEN
    )
    if len(frame) != expected_length:
        raise AssertionError(
            f"contact encoder produced {len(frame)}, expected {expected_length}"
        )
    return bytes(frame)


def malformed_cases(contact: Dict[str, Any]) -> List[MalformedCase]:
    canonical = encode_contact_update(contact)
    cases = [
        MalformedCase(f"forbidden_length_{length}", canonical[:length])
        for length in FORBIDDEN_CONTACT_UPDATE_LENGTHS
    ]
    for invalid_type in INVALID_CONTACT_TYPES:
        frame = bytearray(canonical)
        frame[33] = invalid_type
        cases.append(MalformedCase(f"invalid_type_{invalid_type}", bytes(frame)))
    for invalid_path in INVALID_PATH_LENGTHS:
        frame = bytearray(canonical)
        frame[35] = invalid_path
        cases.append(MalformedCase(f"invalid_path_{invalid_path:02x}", bytes(frame)))
    return cases


def _event_name(event: Any) -> str:
    return _clean(getattr(getattr(event, "type", None), "name", ""))


def require_event(event: Any, expected: str, operation: str) -> Any:
    name = _event_name(event)
    if name != expected:
        raise ProtocolHilFailure(
            f"{operation} returned {name or '<none>'}: "
            f"{getattr(event, 'payload', None)!r}; expected {expected}"
        )
    return event


def require_illegal_arg(event: Any, operation: str) -> None:
    require_event(event, "ERROR", operation)
    payload = getattr(event, "payload", {})
    if payload.get("error_code") != ERR_CODE_ILLEGAL_ARG:
        raise ProtocolHilFailure(
            f"{operation} returned error {payload!r}; expected "
            "ERR_CODE_ILLEGAL_ARG (6)"
        )


class ProtocolRunner:
    def __init__(self, config: Config, identity: Any) -> None:
        self.config = config
        self.identity = identity
        # StressRunner only needs this subset for connect(), contacts(), and
        # manual-contact-mode verification.
        base_config = SimpleNamespace(
            expected_node_key_prefix=config.expected_node_key_prefix,
            contact_count=EXPECTED_CONTACT_COUNT,
            channel_index=39,
            contact_enumeration_passes=None,
            expected_contact_keyset_sha256=EXPECTED_KEYSET_SHA256,
            settle_seconds=config.settle_seconds,
            reenum_timeout=35.0,
            verbose=config.verbose,
        )
        self.base = BASE.StressRunner(base_config, identity)
        self.steps: List[Dict[str, Any]] = []
        self.original_manual_add_contacts: Optional[bool] = None
        self.original_autoadd_config: Optional[int] = None
        self.original_target_contact: Optional[Dict[str, Any]] = None
        self.baseline_wire_sha256: Optional[str] = None
        self.restoration_armed = False

    def progress(self, message: str) -> None:
        if self.config.verbose:
            print(message, file=sys.stderr, flush=True)

    def record(self, step: str, **details: Any) -> None:
        self.steps.append({"step": step, **details})
        self.progress(step)

    async def snapshot(self, *, canonical: bool = True) -> Dict[str, Dict[str, Any]]:
        contacts = await self.base.contacts()
        details = validate_exact_snapshot(
            contacts, require_canonical_fields=canonical
        )
        self.record("contact_snapshot", **details)
        return contacts

    async def freeze_contact_admission_for_test(self) -> None:
        event = self.base._require_event(
            await self.base.mc.commands.send_appstart(), "SELF_INFO"
        )
        autoadd = self.base._require_event(
            await self.base.mc.commands.get_autoadd_config(), "AUTOADD_CONFIG"
        )
        self.original_manual_add_contacts = bool(
            event.payload.get("manual_add_contacts")
        )
        self.original_autoadd_config = int(autoadd.payload.get("config", 0))
        if not self.original_manual_add_contacts:
            await self.base.set_manual_contact_mode(True)
        if self.original_autoadd_config != 0:
            await self.base.set_autoadd_config(0)
        self.record(
            "contact_admission_frozen",
            original_manual_add_contacts=self.original_manual_add_contacts,
            original_autoadd_config=self.original_autoadd_config,
        )

    async def test_malformed_updates(
        self, baseline: Dict[str, Dict[str, Any]]
    ) -> None:
        try:
            from meshcore.events import EventType
        except ImportError as exc:
            raise ProtocolHilFailure("meshcore Python package is unavailable") from exc
        baseline_semantic = semantic_digest(baseline)
        baseline_wire = wire_snapshot_digest(baseline)
        target = BASE.make_contact(self.config.mutation_index)
        completed: List[str] = []
        for case in malformed_cases(target):
            if not case.frame or case.frame[0] != CMD_ADD_UPDATE_CONTACT:
                raise AssertionError("malformed test escaped the local contact command")
            result = await self.base.mc.commands.send(
                case.frame, [EventType.ERROR], timeout=8.0
            )
            require_illegal_arg(result, case.name)
            after = await self.snapshot(canonical=True)
            if semantic_digest(after) != baseline_semantic:
                raise ProtocolHilFailure(
                    f"{case.name} changed contact content despite rejection"
                )
            if wire_snapshot_digest(after) != baseline_wire:
                raise ProtocolHilFailure(
                    f"{case.name} changed contact lastmod despite rejection"
                )
            completed.append(case.name)
        self.record(
            "malformed_contact_updates",
            cases=len(completed),
            names=completed,
            expected_error="ERR_CODE_ILLEGAL_ARG",
            unchanged_after_each=True,
        )

    async def test_structural_restart(self) -> None:
        expected_keys = deterministic_keys()
        if self.original_target_contact is None:
            raise ProtocolHilFailure("baseline target contact was not captured")
        target = dict(self.original_target_contact)
        target_key = target["public_key"]
        reader = getattr(self.base.mc, "_reader", None)
        if reader is None or not hasattr(reader, "handle_rx"):
            raise ProtocolHilFailure("meshcore reader cannot expose raw responses")

        observer = ContactStreamObserver(self.config.stream_trigger_contacts)
        original_handle_rx = reader.handle_rx

        async def observed_handle_rx(data: bytearray) -> None:
            try:
                observer.observe(bytes(data))
            except BaseException as exc:
                # SerialConnection dispatches reader calls as background tasks.
                # Retain observer failures for the foreground test while still
                # feeding the SDK parser so cleanup can drain the transaction.
                if observer.background_failure is None:
                    observer.background_failure = exc
                observer.ready.set()
                observer.ended.set()
            await original_handle_rx(data)

        reader.handle_rx = observed_handle_rx
        stream_task: Optional[asyncio.Task[Any]] = None
        mutation_after_frame = -1
        try:
            stream_task = asyncio.create_task(
                self.base.mc.commands.get_contacts(lastmod=0, timeout=20)
            )
            await asyncio.wait_for(observer.ready.wait(), timeout=5.0)
            if observer.background_failure is not None:
                raise ProtocolHilFailure(
                    f"raw contact observer failed: {observer.background_failure}"
                )
            if not observer.segments or observer.segments[0].ended:
                raise ProtocolHilFailure(
                    "initial contact stream ended before structural mutation"
                )
            if observer.segments[0].advertised_count != EXPECTED_CONTACT_COUNT:
                raise ProtocolHilFailure(
                    "initial contact stream advertised "
                    f"{observer.segments[0].advertised_count}, expected 350"
                )
            mutation_after_frame = observer.frame_count

            removed = await self.base.mc.commands.remove_contact(target_key)
            require_event(removed, "OK", "remove during contact stream")
            try:
                from meshcore.events import EventType
            except ImportError as exc:
                raise ProtocolHilFailure(
                    "meshcore Python package is unavailable"
                ) from exc
            # Use the supported 148-byte form so the original lastmod survives
            # the structural test and incremental sync clients see no phantom
            # update after the table has been restored.
            readded = await self.base.mc.commands.send(
                encode_contact_update(target),
                [EventType.OK, EventType.ERROR],
                timeout=8.0,
            )
            require_event(readded, "OK", "re-add during contact stream")

            stream_event = await asyncio.wait_for(stream_task, timeout=20.0)
            if observer.background_failure is not None:
                raise ProtocolHilFailure(
                    f"raw contact observer failed: {observer.background_failure}"
                )
            require_event(stream_event, "CONTACTS", "paced contact stream")
            sdk_contacts = getattr(stream_event, "payload", None)
            if not isinstance(sdk_contacts, dict):
                raise ProtocolHilFailure("SDK CONTACTS payload is not a dictionary")
            sdk_details = validate_exact_snapshot(
                dict(sdk_contacts), require_canonical_fields=True
            )
            reader_count = getattr(reader, "contact_nb", None)
            if reader_count != EXPECTED_CONTACT_COUNT:
                raise ProtocolHilFailure(
                    f"SDK retained CONTACTS_START count {reader_count!r}, expected 350"
                )
            raw_details = observer.validate_restart(
                expected_keys, mutation_after_frame
            )
            self.record(
                "structural_stream_restart",
                mutation_index=self.config.mutation_index,
                mutation_after_frame=mutation_after_frame,
                raw=raw_details,
                sdk=sdk_details,
                target_restored=True,
            )
        finally:
            # If a mutation assertion fails, let the in-device iterator finish
            # before the outer restoration sends another GET_CONTACTS command.
            if observer.segments and not observer.segments[-1].ended:
                try:
                    await asyncio.wait_for(observer.ended.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
            if stream_task is not None and not stream_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(stream_task), timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    stream_task.cancel()
                    try:
                        await stream_task
                    except asyncio.CancelledError:
                        pass
            elif stream_task is not None:
                # Retrieve a terminal exception when the foreground failed
                # before it awaited the stream task.
                try:
                    stream_task.result()
                except (asyncio.CancelledError, Exception):
                    pass
            reader.handle_rx = original_handle_rx

    async def restore(self) -> None:
        """Restore the one permitted target and the admission-mode setting."""

        errors: List[str] = []
        if self.restoration_armed and self.base.mc is None:
            errors.append("contact restoration failed: device is disconnected")
        elif self.restoration_armed and self.original_target_contact is None:
            errors.append("contact restoration failed: baseline target is missing")
        elif self.restoration_armed:
            target = dict(self.original_target_contact)
            try:
                current = await self.base.contacts()
                unexpected = set(current) - deterministic_keys()
                if unexpected:
                    raise ProtocolHilFailure(
                        "refusing automatic cleanup with unexpected keys: "
                        + ", ".join(key[:12] for key in sorted(unexpected)[:4])
                    )
                try:
                    from meshcore.events import EventType
                except ImportError as exc:
                    raise ProtocolHilFailure(
                        "meshcore Python package is unavailable"
                    ) from exc
                event = await self.base.mc.commands.send(
                    encode_contact_update(target),
                    [EventType.OK, EventType.ERROR],
                    timeout=8.0,
                )
                require_event(event, "OK", "restore deterministic contact")
                await asyncio.sleep(self.config.settle_seconds)
                # A live-RAM GET_CONTACTS is not persistence proof.  The HIL
                # preflight guarantees headroom and a graceful reboot forces
                # all lazy page writes to commit or explicitly refuses reset.
                await self.base.reboot_and_reconnect()
                restored = await self.base.contacts()
                details = validate_exact_snapshot(
                    restored, require_canonical_fields=True
                )
                if (
                    self.baseline_wire_sha256 is not None
                    and wire_snapshot_digest(restored)
                    != self.baseline_wire_sha256
                ):
                    raise ProtocolHilFailure(
                        "post-reboot contact table does not match the exact "
                        "baseline wire snapshot"
                    )
                self.record(
                    "contact_table_restored",
                    persistence="verified_after_graceful_reboot",
                    **details,
                )
            except BaseException as exc:
                errors.append(f"contact restoration failed: {exc}")

        if self.original_autoadd_config is not None and self.base.mc is None:
            errors.append(
                "autoadd config restoration failed: device is disconnected"
            )
        elif self.original_autoadd_config is not None:
            try:
                current = self.base._require_event(
                    await self.base.mc.commands.get_autoadd_config(),
                    "AUTOADD_CONFIG",
                )
                current_config = int(current.payload.get("config", -1))
                if current_config != self.original_autoadd_config:
                    await self.base.set_autoadd_config(
                        self.original_autoadd_config
                    )
                self.record(
                    "autoadd_config_restored",
                    autoadd_config=self.original_autoadd_config,
                )
            except BaseException as exc:
                errors.append(f"autoadd config restoration failed: {exc}")

        if self.original_manual_add_contacts is not None and self.base.mc is None:
            errors.append(
                "contact admission restoration failed: device is disconnected"
            )
        elif self.original_manual_add_contacts is not None:
            try:
                current = self.base._require_event(
                    await self.base.mc.commands.send_appstart(), "SELF_INFO"
                )
                current_mode = bool(current.payload.get("manual_add_contacts"))
                if current_mode != self.original_manual_add_contacts:
                    await self.base.set_manual_contact_mode(
                        self.original_manual_add_contacts
                    )
                self.record(
                    "contact_admission_restored",
                    manual_add_contacts=self.original_manual_add_contacts,
                )
            except BaseException as exc:
                errors.append(f"contact admission restoration failed: {exc}")
        if errors:
            raise ProtocolHilFailure("; ".join(errors))

    async def execute(self) -> None:
        await self.base.connect()
        connected = next(
            (
                step
                for step in reversed(self.base.steps)
                if step.get("step") == "connected"
            ),
            {},
        )
        self.record(
            "connected",
            port=connected.get("port", self.identity.device),
            usb_serial=connected.get("usb_serial", self.identity.serial_number),
            node_key_prefix=connected.get("node_key_prefix"),
            device=connected.get("device"),
        )
        await self.base.clear_filler()
        storage = await self.base.status()
        free_kib = storage.total_kib - storage.used_kib
        if free_kib < MIN_RESTORE_FREE_KIB:
            raise ProtocolHilFailure(
                f"only {free_kib} KiB is free after clearing the HIL filler; "
                f"at least {MIN_RESTORE_FREE_KIB} KiB is required for durable "
                "contact restoration"
            )
        self.record(
            "persistence_headroom",
            used_kib=storage.used_kib,
            total_kib=storage.total_kib,
            free_kib=free_kib,
            hil_filler_cleared=True,
        )
        baseline = await self.snapshot(canonical=True)
        target_key = BASE.make_contact(self.config.mutation_index)["public_key"]
        self.original_target_contact = dict(baseline[target_key])
        self.baseline_wire_sha256 = wire_snapshot_digest(baseline)
        self.restoration_armed = True
        await self.freeze_contact_admission_for_test()
        await self.test_malformed_updates(baseline)
        await self.test_structural_restart()


async def run(config: Config) -> Dict[str, Any]:
    require_consent(config)
    identity = BASE.resolve_initial_port(config.port)
    runner = ProtocolRunner(config, identity)
    result: Dict[str, Any] = {
        "schema_version": 1,
        "tool": "t1000e_contact_protocol_stress",
        "ok": False,
        "identity": asdict(identity),
        "steps": runner.steps,
        "failure": None,
    }
    primary_error: Optional[BaseException] = None
    try:
        await runner.execute()
    except BaseException as exc:
        primary_error = exc
    try:
        await runner.restore()
    except BaseException as exc:
        if primary_error is None:
            primary_error = exc
        else:
            primary_error = ProtocolHilFailure(
                f"test failed ({primary_error}); cleanup also failed ({exc})"
            )
    try:
        await runner.base.disconnect()
    except BaseException as exc:
        if primary_error is None:
            primary_error = exc
        else:
            primary_error = ProtocolHilFailure(
                f"test failed ({primary_error}); disconnect also failed ({exc})"
            )
    if primary_error is None:
        result["ok"] = True
    else:
        result["failure"] = {
            "type": type(primary_error).__name__,
            "message": str(primary_error),
        }
    return result


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="T1000-E USB Companion port")
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--confirm-usb-serial", default="")
    parser.add_argument("--expected-node-key-prefix", default="")
    parser.add_argument("--expected-contact-keyset-sha256", default="")
    parser.add_argument("--mutation-index", type=int, default=DEFAULT_MUTATION_INDEX)
    parser.add_argument("--stream-trigger-contacts", type=int, default=12)
    parser.add_argument("--settle-seconds", type=float, default=7.0)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run pure offline invariants without importing serial hardware",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    if not args.port:
        raise ProtocolHilFailure("--port is required unless --self-test is used")
    config = Config(
        port=args.port,
        allow_destructive=args.allow_destructive,
        confirm_usb_serial=args.confirm_usb_serial,
        expected_node_key_prefix=args.expected_node_key_prefix,
        expected_contact_keyset_sha256=args.expected_contact_keyset_sha256,
        mutation_index=args.mutation_index,
        stream_trigger_contacts=args.stream_trigger_contacts,
        settle_seconds=args.settle_seconds,
        verbose=args.verbose,
    )
    require_consent(config)
    return config


def offline_self_test() -> Dict[str, Any]:
    expected = {
        BASE.make_contact(index)["public_key"]: BASE.make_contact(index)
        for index in range(EXPECTED_CONTACT_COUNT)
    }
    snapshot = validate_exact_snapshot(
        expected, require_canonical_fields=True
    )
    cases = malformed_cases(BASE.make_contact(DEFAULT_MUTATION_INDEX))
    lengths = [
        len(case.frame)
        for case in cases
        if case.name.startswith("forbidden_length_")
    ]
    assert tuple(lengths) == FORBIDDEN_CONTACT_UPDATE_LENGTHS
    assert all(case.frame[0] == CMD_ADD_UPDATE_CONTACT for case in cases)

    observer = ContactStreamObserver(trigger_contacts=2)
    keys = sorted(deterministic_keys())
    observer.observe(bytes((RESP_CODE_CONTACTS_START,)) + struct.pack("<I", 350))
    observer.observe(
        bytes((RESP_CODE_CONTACT,)) + bytes.fromhex(keys[0]) + b"\0" * 115
    )
    observer.observe(
        bytes((RESP_CODE_CONTACT,)) + bytes.fromhex(keys[1]) + b"\0" * 115
    )
    marker = observer.frame_count
    observer.observe(bytes((RESP_CODE_CONTACTS_START,)) + struct.pack("<I", 350))
    for key in keys:
        observer.observe(
            bytes((RESP_CODE_CONTACT,)) + bytes.fromhex(key) + b"\0" * 115
        )
    observer.observe(bytes((RESP_CODE_END_OF_CONTACTS,)) + b"\0\0\0\0")
    stream = observer.validate_restart(set(keys), marker)
    return {
        "ok": True,
        "checks": [
            "deterministic_snapshot",
            "malformed_case_matrix",
            "raw_stream_restart",
        ],
        "snapshot": snapshot,
        "malformed_cases": len(cases),
        "stream": stream,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        result = offline_self_test()
    else:
        try:
            result = asyncio.run(run(config_from_args(args)))
        except BaseException as exc:
            result = {
                "schema_version": 1,
                "tool": "t1000e_contact_protocol_stress",
                "ok": False,
                "steps": [],
                "failure": {"type": type(exc).__name__, "message": str(exc)},
            }
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
