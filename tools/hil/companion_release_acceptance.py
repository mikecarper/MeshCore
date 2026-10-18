#!/usr/bin/env python3
"""Identity-bound USB Companion release acceptance, without radio traffic.

snapshot saves private raw contact/channel frames and settings. smoke additionally
runs persistent queries and fresh USB sessions, checking monotonic uptime.
owned-crud changes only one randomly generated, confirmed-absent contact and
removes it before requiring the original contact/channel/settings baseline.

Neither APP_START nor reopening proves cold-boot durability. This tool never
reboots, exports a private key, sends a mesh message, or changes node settings.
The snapshot contains channel keys and the BLE PIN; keep it private.
The local binary CLI verifies the complete expected firmware version and its
Build suffix before any contact mutation; DEVICE_INFO alone truncates versions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import struct
import subprocess
import sys
import time

import serial
from serial.tools import list_ports

from esp32_companion_serial_stress import (
    DeviceFrameReader, TransportCounters, encode_host_frame,
    validate_app_start_response, validate_core_stats_response,
    validate_device_info_response,
)
from profile_switch import configure_session
from serial_session import open_configured_session

VERSION_REQUEST = b"\x427f|ver"
VERSION_REPLY_PREFIX = b"\x1d7f|"


class AcceptanceError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def validate_full_version_response(payload, expected_version, build_date):
    require(payload.startswith(VERSION_REPLY_PREFIX), "Local CLI version reply prefix mismatch")
    try:
        text = payload[len(VERSION_REPLY_PREFIX):].decode("ascii", "strict")
    except UnicodeError:
        raise AcceptanceError("Local CLI version reply is not ASCII") from None
    require(re.fullmatch(r"[ -~]{1,11}", build_date) is not None,
            "DEVICE_INFO build date is missing or invalid")
    require(text == f"{expected_version} (Build: {build_date})",
            "Full local CLI version or Build suffix does not match the expected candidate")
    return text


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def private_json(path, value):
    """Never replace a backup or follow a final-component symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def resolve_port(chip_serial):
    matches = [item for item in list_ports.comports()
               if (item.serial_number or "").upper() == chip_serial.upper()
               and "logging" not in (item.interface or "").lower()]
    require(len(matches) == 1, "USB serial must resolve to exactly one primary application interface")
    item = matches[0]
    require(re.fullmatch(r"/dev/tty(?:ACM|USB)\d+", os.path.realpath(item.device)) is not None,
            "Only a local Linux USB serial application endpoint is accepted")
    return item


def require_unowned(device):
    checked = subprocess.run(["sudo", "-n", "fuser", os.path.realpath(device)],
                             capture_output=True, text=True, timeout=10)
    require(checked.returncode == 1 and not checked.stdout.strip() and not checked.stderr.strip(),
            "Cannot prove the selected serial endpoint is unowned; no owner was stopped")


class Session:
    def __init__(self, args, counters):
        self.args, self.counters = args, counters
        self.port = None
        self.reader = DeviceFrameReader(counters)

    def __enter__(self):
        item = resolve_port(self.args.serial)
        require_unowned(item.device)
        port = serial.Serial(port=None, baudrate=115200, timeout=0.05,
                             write_timeout=5, exclusive=True)
        port.port = item.device
        configure_session(port)
        try:
            open_configured_session(port)
            self.port = port
            self.counters.ports_opened += 1
            require(os.path.realpath(resolve_port(self.args.serial).device) == os.path.realpath(item.device),
                    "USB serial changed endpoints while the session opened")
            # Inventory may have left the ASCII terminal enabled. STOP is safe
            # in an already-binary session; the following binary handshake is
            # the authority, not an optional ASCII acknowledgement.
            port.write(b"+++MESHCORE-TERM-STOP\r\n")
            port.flush()
            end, quiet = time.monotonic() + 2, time.monotonic() + 0.25
            text = bytearray()
            while time.monotonic() < min(end, quiet):
                part = port.read(min(port.in_waiting or 1, 4096))
                if part:
                    text.extend(part)
                    require(len(text) <= 65536, "Terminal exit exceeded its bounded reply capture")
                    quiet = time.monotonic() + 0.15
            require(b"ERROR:" not in text, "Terminal refused Binary mode; inspect USB logging policy")
            self.handshake()
            return self
        except BaseException:
            port.close()
            if self.port is not None:
                self.counters.ports_closed += 1
            self.port = None
            raise

    def __exit__(self, *_):
        if self.port is not None:
            self.port.close()
            self.counters.ports_closed += 1
            self.port = None

    def send(self, payload):
        frame = encode_host_frame(payload)
        require(self.port.write(frame) == len(frame), "Incomplete Companion request write")
        self.port.flush()
        self.counters.request_frames_sent += 1
        self.counters.request_bytes_sent += len(frame)

    def receive(self, timeout=None):
        end = time.monotonic() + (timeout or self.args.timeout)
        while time.monotonic() < end:
            value = self.reader.read_frame(self.port, max(0.01, end - time.monotonic()))
            if value[0] >= 0x80:
                self.counters.ignored_push_frames += 1
                continue
            return value
        raise AcceptanceError("Companion response deadline expired")

    def request(self, payload, response, *, allow_error=False):
        self.send(payload)
        value = self.receive()
        if value[0] == 1:
            require(len(value) == 2, "Malformed Companion error response")
            if allow_error:
                return value
            raise AcceptanceError(f"Companion command {payload[0]} failed with code {value[1]}")
        require(value[0] == response, f"Unexpected response opcode for command {payload[0]}")
        return value

    def handshake(self):
        self.self_frame = self.request(b"\x01" + bytes(7) + b"Release acceptance", 5)
        validate_app_start_response(self.self_frame)
        self.device_frame = self.request(b"\x16\x0e", 13)
        self.device = validate_device_info_response(self.device_frame)
        require(self.args.expected_board.casefold() in self.device["manufacturer"].casefold(),
                "Application board does not match --expected-board")
        expected = self.args.expected_version.encode("utf-8")[:19].decode("utf-8")
        require(self.device["firmware"] == expected,
                "Application binary version does not match --expected-version (19-byte field)")
        self.full_cli_version_frame = self.request(VERSION_REQUEST, 29)
        self.full_cli_version = validate_full_version_response(
            self.full_cli_version_frame, self.args.expected_version, self.device["build_date"])

    def stats(self):
        return validate_core_stats_response(self.request(b"\x38\x00", 24))

    def contacts(self):
        self.send(b"\x04")
        start = self.receive()
        require(len(start) == 5 and start[0] == 2, "Contact stream did not start with a complete header")
        count = struct.unpack_from("<I", start, 1)[0]
        require(count <= self.device["contact_capacity"], "Contact count exceeds device capacity")
        contacts = {}
        while True:
            frame = self.receive(timeout=max(10, self.args.timeout))
            if frame[0] == 4:
                require(len(frame) == 5 and len(contacts) == count,
                        "Contact stream ended with an incomplete or inconsistent count")
                return dict(sorted(contacts.items()))
            require(len(frame) == 148 and frame[0] == 3, "Contact frame must contain all 148 bytes")
            key = frame[1:33].hex()
            require(key not in contacts, "Contact stream contains a duplicate identity")
            require(len(contacts) < count, "Contact stream exceeds its advertised count")
            contacts[key] = frame.hex()

    def channels(self):
        result = []
        for index in range(self.device["channel_capacity"]):
            # Extended requests retain a 256-bit Meshtext key when supported;
            # ordinary channels return their stock 128-bit key.
            frame = self.request(bytes((31, index, 32)), 18)
            require(len(frame) in (50, 66) and frame[1] == index,
                    "Channel response length or index mismatch")
            name = frame[2:34].split(b"\0", 1)[0]
            result.append({"index": index, "name_hex": name.hex(),
                           "key_hex": frame[34:].hex(), "raw_frame_hex": frame.hex()})
        return result

    def snapshot(self):
        self.handshake()
        return {"schema": 1, "usb_serial": self.args.serial,
                "self_info_frame_hex": self.self_frame.hex(),
                "device_info_frame_hex": self.device_frame.hex(),
                "full_cli_version": self.full_cli_version,
                "full_cli_version_frame_hex": self.full_cli_version_frame.hex(),
                "contacts": self.contacts(), "channels": self.channels(),
                "core_stats": self.stats()}


def stable_state(snapshot):
    return {"self_info": snapshot["self_info_frame_hex"],
            "device_info": snapshot["device_info_frame_hex"],
            "full_cli_version": snapshot["full_cli_version"],
            "contacts": snapshot["contacts"],
            "channels": [{key: channel[key] for key in ("index", "name_hex", "key_hex")}
                         for channel in snapshot["channels"]]}


def check_uptime(previous, current):
    require(current["uptime_seconds"] >= previous["uptime_seconds"], "Device uptime decreased")
    require(current["error_flags"] & ~previous["error_flags"] == 0, "New core error flags appeared")


def smoke(args, counters, baseline, report):
    previous = baseline["core_stats"]
    with Session(args, counters) as session:
        for index in range(args.queries):
            if index % 2:
                session.handshake()
            current = session.stats()
            check_uptime(previous, current)
            previous = current
        report["persistent_query_cycles"] = args.queries
    for _ in range(args.reopens):
        with Session(args, counters) as session:
            current = session.stats()
            check_uptime(previous, current)
            previous = current
    report["reopen_cycles"] = args.reopens
    report["uptime_start"] = baseline["core_stats"]["uptime_seconds"]
    report["uptime_end"] = previous["uptime_seconds"]
    report["uptime_monotonic"] = True
    return previous


def owned_plan(baseline):
    require(len(baseline["contacts"]) < validate_device_info_response(
        bytes.fromhex(baseline["device_info_frame_hex"]))["contact_capacity"],
        "Owned CRUD needs a free contact slot; no existing contact will be removed")
    while True:
        key = secrets.token_bytes(32)
        if key.hex() not in baseline["contacts"] and key != bytes.fromhex(baseline["self_info_frame_hex"])[4:36]:
            break
    label = ("Release-HIL-" + secrets.token_hex(4)).encode("ascii")
    # Complete update form: opcode/key/type/flags/path/name/advert/GPS/lastmod.
    body = key + b"\x01\x00\xff" + bytes(64) + label.ljust(32, b"\0")
    body += struct.pack("<IiiI", 1700000000, 0, 0, 1700000000)
    return {"add_update_frame_hex": (b"\x09" + body).hex()}


def owned_crud(args, counters, baseline, report):
    body = bytes.fromhex(baseline["owned_crud_plan"]["add_update_frame_hex"])[1:]
    key = body[:32]
    label = body[99:131].split(b"\0", 1)[0]
    expected_frames = []
    cleanup_needed = False
    failure = None
    try:
        with Session(args, counters) as session:
            absent = session.request(b"\x1e" + key, 3, allow_error=True)
            require(absent == b"\x01\x02", "Synthetic contact is not confirmed absent")
            for update in range(2):
                changed = bytearray(body)
                if update:
                    changed[99:131] = (label + b"-updated").ljust(32, b"\0")
                expected = b"\x03" + bytes(changed)
                expected_frames.append(expected)
                cleanup_needed = True  # A lost acknowledgement may still mean acceptance.
                reply = session.request(b"\x09" + bytes(changed), 0)
                require(reply == b"\x00", "Contact mutation returned malformed success")
                readback = session.request(b"\x1e" + key, 3)
                require(readback == expected, "Owned contact readback differs from its complete update")
            time.sleep(args.settle_seconds)
        with Session(args, counters) as session:
            require(session.request(b"\x1e" + key, 3) == expected_frames[-1],
                    "Owned contact did not survive a USB reopen")
        report["owned_add_update_read_reopen"] = True
    except BaseException as error:
        failure = error
    finally:
        if cleanup_needed:
            # Fresh handshake after any timeout prevents a delayed mutation ACK
            # from being mistaken for this cleanup operation's acknowledgement.
            try:
                with Session(args, counters) as session:
                    present = session.request(b"\x1e" + key, 3, allow_error=True)
                    if present != b"\x01\x02":
                        require(present in expected_frames, "Owned contact changed; refusing to delete an unrecognized record")
                        require(session.request(b"\x0f" + key, 0) == b"\x00", "Owned contact deletion failed")
                    require(session.request(b"\x1e" + key, 3, allow_error=True) == b"\x01\x02",
                            "Owned contact remains after cleanup")
                    time.sleep(args.settle_seconds)
                report["owned_contact_removed"] = True
            except BaseException as cleanup_error:
                report["owned_cleanup_failed"] = True
                if failure is None:
                    failure = cleanup_error
    if failure is not None:
        raise failure


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="Exact application USB chip serial; no wildcard")
    parser.add_argument("--expected-board", required=True, help="Required DEVICE_INFO board substring")
    parser.add_argument("--expected-version", required=True,
                        help="Exact complete FIRMWARE_VERSION, including the candidate source suffix; exclude the Build suffix")
    parser.add_argument("--mode", choices=("snapshot", "smoke", "owned-crud"), default="snapshot")
    parser.add_argument("--snapshot", required=True, type=Path, help="New private 0600 baseline JSON path")
    parser.add_argument("--report", required=True, type=Path, help="New sanitized JSON report path")
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--reopens", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--settle-seconds", type=float, default=7)
    parser.add_argument("--manifest", type=Path, help="Optional externally reviewed artifact manifest")
    parser.add_argument("--manifest-sha256", help="Exact expected SHA-256 of --manifest")
    args = parser.parse_args()
    require(re.fullmatch(r"(?:[A-Za-z0-9]{6,64}|(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2})", args.serial) is not None,
            "USB serial must be exact alphanumeric text or a six-byte colon-separated MAC")
    require(args.expected_board.strip() and args.expected_version.isascii() and args.expected_version.strip(),
            "Expected board and ASCII version must be nonempty")
    require(1 <= args.queries <= 10000 and 1 <= args.reopens <= 1000
            and 1 <= args.timeout <= 120 and 5 <= args.settle_seconds <= 60,
            "Query, reopen, timeout or save-settle setting is outside its bounded range")
    require(args.snapshot.resolve() != args.report.resolve(), "Snapshot and report paths must differ")
    require(not args.snapshot.exists() and not args.snapshot.is_symlink()
            and not args.report.exists() and not args.report.is_symlink(), "Output paths must be new")
    require(bool(args.manifest) == bool(args.manifest_sha256), "Supply both manifest and its expected SHA-256")
    if args.manifest:
        require(re.fullmatch(r"[A-Fa-f0-9]{64}", args.manifest_sha256) is not None, "Manifest hash must be SHA-256")
        require(hashlib.sha256(args.manifest.read_bytes()).hexdigest() == args.manifest_sha256.lower(),
                "External artifact manifest hash mismatch")
    return args


def main():
    args = parse_args()
    counters = TransportCounters()
    report = {"schema": 1, "mode": args.mode, "passed": False,
              "cold_boot_tested": False, "private_key_exported": False,
              "source_version_verified": False,
              "live_flash_hash_verified": False}
    if args.manifest:
        report["external_manifest_sha256"] = args.manifest_sha256.lower()
    try:
        with Session(args, counters) as session:
            baseline = session.snapshot()
        report["full_runtime_version"] = baseline["full_cli_version"]
        report["source_version_verified"] = True
        if args.mode == "owned-crud":
            # Save the exact generated key/record privately before sending any
            # mutation, so interrupted cleanup remains manually recoverable.
            baseline["owned_crud_plan"] = owned_plan(baseline)
        private_json(args.snapshot, baseline)
        report["snapshot_sha256"] = hashlib.sha256(args.snapshot.read_bytes()).hexdigest()
        report["baseline_state_sha256"] = digest(stable_state(baseline))
        report["contacts"] = len(baseline["contacts"])
        report["channels"] = len(baseline["channels"])
        report["initial_core_stats"] = baseline["core_stats"]
        if args.mode == "smoke":
            smoke(args, counters, baseline, report)
        elif args.mode == "owned-crud":
            owned_crud(args, counters, baseline, report)
        with Session(args, counters) as session:
            final = session.snapshot()
        check_uptime(baseline["core_stats"], final["core_stats"])
        require(stable_state(final) == stable_state(baseline),
                "Final contacts, channels, identity/name or settings differ from the original baseline")
        report["final_state_sha256"] = digest(stable_state(final))
        report["final_core_stats"] = final["core_stats"]
        report["baseline_restored"] = True
        report["passed"] = True
    except BaseException as error:
        # Validators never include full wire frames or channel/private keys.
        report["failure"] = f"{type(error).__name__}: {error}"
    finally:
        report["transport"] = vars(counters)
        private_json(args.report, report)
        print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceError, OSError) as error:
        print(f"Acceptance preflight failed: {error}", file=sys.stderr)
        raise SystemExit(2)
