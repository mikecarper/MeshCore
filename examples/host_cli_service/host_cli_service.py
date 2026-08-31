#!/usr/bin/env python3
"""MQTT endpoint for the repeater LoRa-to-host CLI service.

Built-in requests use a small fixed allowlist; optional local programs use a
separate alias and argument allowlist. Requests are accepted only after the
repeater's Ed25519 signature, identity, framing, and size limits have all been
verified and the repeater completes a live one-time challenge. Replies use
meshcoretomqtt's signed remote-serial channel.
"""

from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Callable


LOGGER = logging.getLogger("meshcore-host-cli")
REQUEST_PROTOCOL_PREFIX = "DEBUG HOSTCLI/1 REQUEST "
CLAIM_PROTOCOL_PREFIX = "DEBUG HOSTCLI/1 CLAIMED "
MAX_REQUEST_BYTES = 155
MAX_REQUEST_ENCODED_BYTES = (MAX_REQUEST_BYTES * 4 + 2) // 3
MAX_REPLY_BYTES = 162
CHALLENGE_TIMEOUT_SECONDS = 4.0
MIN_CLOCK_EPOCH = 1577836800  # 2020-01-01T00:00:00Z
MAX_CLOCK_EPOCH = 4102444799  # 2099-12-31T23:59:59Z
CLOCK_CONTROL_SOCKET = Path("/run/meshcore-clock-control.sock")
CLOCK_CONTROL_TIMEOUT_SECONDS = 5.0
CLOCK_CONTROL_MAX_RESPONSE_BYTES = 192
CLOCK_STATUS_CHILD_TIMEOUT_SECONDS = 1.5
HOST_ACTIONS_SOCKET = Path("/run/meshcore-host-actions.sock")
HOST_ACTION_TOTAL_TIMEOUT_SECONDS = 5.0
HOST_ACTION_PUBLISH_TIMEOUT_SECONDS = 2.0
HOST_ACTION_MAX_RESPONSE_BYTES = 128
HOST_ACTION_QUEUE_DEPTH = 1
HOST_ACTION_REBOOT_DELAY_SECONDS = 10
HOST_ACTION_OPERATION_RE = re.compile(r"[0-9A-F]{32}")
HOST_ACTION_NAMES = frozenset(("network-restart", "reboot"))
HOST_ACTION_ERROR_RESPONSES = frozenset(
    (
        "ERR action disabled",
        "ERR operation not prepared",
        "ERR reboot pending",
        "ERR state full",
        "ERR state unavailable",
        "ERR operation conflict",
        "ERR invalid request",
        "ERR request timed out",
        "ERR unauthorized peer",
        "ERR server error",
    )
)
REQUEST_RE = re.compile(
    r"^DEBUG (HOSTCLI/1 REQUEST "
    r"([0-9A-Fa-f]{8}) "
    r"([0-9A-Fa-f]{16}) "
    r"([A-Za-z0-9_-]{2," + str(MAX_REQUEST_ENCODED_BYTES) + r"})) "
    r"([0-9A-Fa-f]{128})$"
)
CLAIM_RE = re.compile(
    r"^DEBUG (HOSTCLI/1 CLAIMED "
    r"([0-9A-Fa-f]{8}) "
    r"([0-9A-Fa-f]{16}) "
    r"([0-9A-Fa-f]{16})) "
    r"([0-9A-Fa-f]{128})$"
)
KEY_CHECK_MESSAGE = b"MeshCore HOSTCLI/1 service key check"

Signer = Callable[[bytes, bytes, bytes], bytes]
Verifier = Callable[[bytes, bytes, bytes], bool]


@dataclass(frozen=True)
class HostRequest:
    request_id: str
    request_nonce: str
    text: str


@dataclass(frozen=True)
class HostClaim:
    request_id: str
    request_nonce: str
    challenge: str


@dataclass(frozen=True)
class PendingHostAction:
    request: HostRequest
    challenge: str
    expires_at: float


@dataclass(frozen=True)
class ServiceKey:
    public_key: str
    private_key: str


@dataclass(frozen=True)
class HostResult:
    text: str
    host_action: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True)
class VerifiedHostAction:
    request: HostRequest
    action: str
    operation_id: str


@dataclass(frozen=True)
class ProgramArgumentRule:
    name: str
    kind: str
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    max_bytes: int = 32


@dataclass(frozen=True)
class ProgramDefinition:
    alias: str
    argv: tuple[str, ...]
    arguments: tuple[ProgramArgumentRule, ...]
    timeout_seconds: int = 3


def _orlp_signer() -> Signer:
    try:
        from ed25519_orlp import ed25519_sign
    except ImportError as exc:
        raise RuntimeError(
            "ed25519-orlp is required; use meshcoretomqtt's virtualenv"
        ) from exc
    return ed25519_sign


def _orlp_verifier() -> Verifier:
    try:
        from ed25519_orlp import ed25519_verify
    except ImportError as exc:
        raise RuntimeError(
            "ed25519-orlp is required; use meshcoretomqtt's virtualenv"
        ) from exc
    return ed25519_verify


def normalize_key(value: str, expected_bytes: int, label: str) -> str:
    normalized = "".join(value.split()).upper()
    if len(normalized) != expected_bytes * 2:
        raise ValueError(
            f"{label} must be {expected_bytes} bytes "
            f"({expected_bytes * 2} hex characters)"
        )
    try:
        bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} is not valid hexadecimal") from exc
    return normalized


def verify_service_key(
    key: ServiceKey,
    signer: Signer | None = None,
    verifier: Verifier | None = None,
) -> None:
    signer = signer or _orlp_signer()
    verifier = verifier or _orlp_verifier()
    public_bytes = bytes.fromhex(key.public_key)
    private_bytes = bytes.fromhex(key.private_key)
    signature = signer(KEY_CHECK_MESSAGE, public_bytes, private_bytes)
    if len(signature) != 64 or not verifier(
        signature, KEY_CHECK_MESSAGE, public_bytes
    ):
        raise ValueError("service public and private keys do not form a keypair")


def load_service_key(path: Path) -> ServiceKey:
    file_mode = stat.S_IMODE(path.stat().st_mode)
    if file_mode & 0o077:
        raise ValueError(
            f"service key {path} must not be accessible by group or others; "
            "use chmod 600"
        )
    data = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(data, dict):
        raise ValueError("service key file must contain a JSON object")
    key = ServiceKey(
        public_key=normalize_key(str(data.get("public_key", "")), 32,
                                 "public key"),
        private_key=normalize_key(str(data.get("private_key", "")), 64,
                                  "private key"),
    )
    verify_service_key(key)
    return key


def generate_service_key(path: Path) -> str:
    try:
        from ed25519_orlp import ed25519_create_keypair
    except ImportError as exc:
        raise RuntimeError(
            "ed25519-orlp is required; use meshcoretomqtt's virtualenv"
        ) from exc

    public_key, private_key, _seed = ed25519_create_keypair()
    document = json.dumps(
        {
            "public_key": public_key.hex().upper(),
            "private_key": private_key.hex().upper(),
        },
        indent=2,
    ) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(document)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return public_key.hex().upper()


def _decode_request_text(encoded: str) -> str:
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        raw = base64.b64decode(
            (encoded + padding).encode("ascii"), altchars=b"-_", validate=True
        )
    except (ValueError, UnicodeError) as exc:
        raise ValueError("host request has invalid Base64URL text") from exc
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("host request text length is invalid")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("host request text is not valid UTF-8") from exc


def _load_mqtt_debug_document(
    mqtt_payload: bytes | str,
) -> tuple[dict[str, Any], str | None]:
    if isinstance(mqtt_payload, bytes):
        try:
            mqtt_payload = mqtt_payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("MQTT debug payload is not valid UTF-8") from exc
    try:
        document = json.loads(mqtt_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("MQTT debug payload is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("MQTT debug payload must be a JSON object")
    message = document.get("message")
    return document, message if isinstance(message, str) else None


def _verify_signed_record(
    document: dict[str, Any],
    message: str,
    pattern: re.Pattern[str],
    repeater_public_key: str,
    label: str,
    verifier: Verifier | None,
) -> re.Match[str]:
    if document.get("type") != "DEBUG":
        raise ValueError(f"{label} has the wrong MQTT message type")

    expected_public_key = normalize_key(
        repeater_public_key, 32, "repeater public key"
    )
    origin_id = normalize_key(
        str(document.get("origin_id", "")), 32, "MQTT origin_id"
    )
    if origin_id != expected_public_key:
        raise ValueError(f"{label} MQTT origin does not match the repeater")

    match = pattern.fullmatch(message)
    if match is None:
        raise ValueError(f"{label} framing or length is invalid")
    signed_content = match.group(1)
    signature = bytes.fromhex(match.group(match.lastindex or 0))
    verifier = verifier or _orlp_verifier()
    if not verifier(
        signature,
        signed_content.encode("ascii"),
        bytes.fromhex(expected_public_key),
    ):
        raise ValueError(f"{label} signature is invalid")
    return match


def parse_and_verify_request(
    mqtt_payload: bytes | str,
    repeater_public_key: str,
    verifier: Verifier | None = None,
) -> HostRequest | None:
    document, message = _load_mqtt_debug_document(mqtt_payload)

    if message is None or not message.startswith(REQUEST_PROTOCOL_PREFIX):
        return None
    match = _verify_signed_record(
        document, message, REQUEST_RE, repeater_public_key, "host request",
        verifier,
    )
    (
        _signed_content,
        request_id,
        request_nonce,
        encoded,
        _signature_hex,
    ) = match.groups()

    return HostRequest(
        request_id=request_id.upper(),
        request_nonce=request_nonce.upper(),
        text=_decode_request_text(encoded),
    )


def parse_and_verify_claim(
    mqtt_payload: bytes | str,
    repeater_public_key: str,
    verifier: Verifier | None = None,
) -> HostClaim | None:
    document, message = _load_mqtt_debug_document(mqtt_payload)
    if message is None or not message.startswith(CLAIM_PROTOCOL_PREFIX):
        return None
    match = _verify_signed_record(
        document, message, CLAIM_RE, repeater_public_key, "host claim",
        verifier,
    )
    (
        _signed_content,
        request_id,
        request_nonce,
        challenge,
        _signature_hex,
    ) = match.groups()
    if int(challenge, 16) == 0:
        raise ValueError("host claim challenge must not be zero")
    return HostClaim(
        request_id=request_id.upper(),
        request_nonce=request_nonce.upper(),
        challenge=challenge.upper(),
    )


def read_cpu_temperature(path: Path) -> str:
    try:
        milli_celsius = int(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return "Err - CPU temperature unavailable"
    if milli_celsius < -40000 or milli_celsius > 200000:
        return "Err - CPU temperature unavailable"
    return f"CPU {milli_celsius / 1000.0:.1f} C"


def read_uptime(path: Path) -> str:
    try:
        seconds = int(float(path.read_text(encoding="ascii").split()[0]))
    except (IndexError, OSError, UnicodeError, ValueError):
        return "Err - host uptime unavailable"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    return f"Uptime {days}d {hours}h {minutes}m"


def read_load_average(path: Path) -> str:
    try:
        values = path.read_text(encoding="ascii").split()[:3]
        if len(values) != 3:
            raise ValueError("missing load averages")
        parsed = [float(value) for value in values]
    except (OSError, UnicodeError, ValueError):
        return "Err - host load unavailable"
    return "Load " + " ".join(f"{value:.2f}" for value in parsed)


def read_memory(path: Path) -> str:
    try:
        fields: dict[str, int] = {}
        for line in path.read_text(encoding="ascii").splitlines():
            name, separator, value = line.partition(":")
            if separator and name in ("MemTotal", "MemAvailable"):
                fields[name] = int(value.split()[0])
        total_kib = fields["MemTotal"]
        available_kib = fields["MemAvailable"]
        if total_kib <= 0 or available_kib < 0 or available_kib > total_kib:
            raise ValueError("invalid memory totals")
    except (IndexError, KeyError, OSError, UnicodeError, ValueError):
        return "Err - host memory unavailable"
    return (
        f"Memory {available_kib // 1024}/{total_kib // 1024} MiB available"
    )


def read_disk_free(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return "Err - host disk unavailable"
    gib = 1024.0 * 1024.0 * 1024.0
    return f"Disk {usage.free / gib:.1f}/{usage.total / gib:.1f} GiB free"


def read_clock_status() -> str:
    epoch = int(time.time())
    synchronized = "unknown"
    try:
        completed = subprocess.run(
            [
                "/usr/bin/timedatectl", "show",
                "--property=NTPSynchronized", "--value",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLOCK_STATUS_CHILD_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            cwd="/",
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
        value = completed.stdout.strip().lower()
        if completed.returncode == 0 and value in ("yes", "no"):
            synchronized = value
    except (OSError, subprocess.SubprocessError):
        pass
    # systemd's NTPSynchronized marker can remain "no" when chrony owns the
    # clock. Prefer chrony's own bounded tracking state in that case instead of
    # falsely reporting an unsynchronized recovery host.
    if synchronized != "yes":
        chronyc = Path("/usr/bin/chronyc")
        if chronyc.is_file() and os.access(chronyc, os.X_OK):
            try:
                tracking = subprocess.run(
                    [str(chronyc), "-n", "tracking"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=CLOCK_STATUS_CHILD_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                    cwd="/",
                    env={
                        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                        "LANG": "C.UTF-8",
                    },
                )
                leap = re.search(
                    r"^\s*Leap status\s*:\s*(.*?)\s*$",
                    tracking.stdout,
                    flags=re.IGNORECASE | re.MULTILINE,
                )
                if tracking.returncode == 0 and leap is not None:
                    state = leap.group(1).casefold()
                    if state == "normal":
                        synchronized = "yes (chrony)"
                    elif state in ("not synchronised", "not synchronized"):
                        synchronized = "no (chrony)"
            except (OSError, subprocess.SubprocessError):
                pass
    return f"Clock epoch {epoch}; NTP synchronized {synchronized}"


def validate_clock_control_socket(
    path: Path = CLOCK_CONTROL_SOCKET,
    *,
    expected_gid: int | None = None,
) -> None:
    """Require the systemd socket's documented privilege boundary."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"host clock control socket is missing: {path}") from exc
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError(f"host clock control path must be a socket: {path}")
    if metadata.st_uid != 0:
        raise ValueError(f"host clock control socket must be root-owned: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o660:
        raise ValueError(f"host clock control socket must have mode 0660: {path}")
    if expected_gid is not None and metadata.st_gid != expected_gid:
        raise ValueError(
            "host clock control socket group must match the endpoint's "
            f"primary group: {path}"
        )


def _remaining_clock_control_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("clock control deadline expired")
    return remaining


def _read_clock_control_response(
    connection: socket.socket,
    deadline: float,
) -> str:
    response = bytearray()
    while True:
        connection.settimeout(_remaining_clock_control_time(deadline))
        chunk = connection.recv(CLOCK_CONTROL_MAX_RESPONSE_BYTES + 1 - len(response))
        if not chunk:
            break
        response.extend(chunk)
        if len(response) > CLOCK_CONTROL_MAX_RESPONSE_BYTES:
            raise ValueError("clock control response is too long")
    if not response or not response.endswith(b"\n") or response.count(b"\n") != 1:
        raise ValueError("clock control response framing is invalid")
    try:
        line = response[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("clock control response is not ASCII") from exc
    if not line or any(ord(character) < 0x20 for character in line):
        raise ValueError("clock control response contains control characters")
    return line


def _exchange_clock_control(request: str) -> str:
    """Send one exact request to the fixed, root-authenticated local socket."""
    request_bytes = (request + "\n").encode("ascii")
    deadline = time.monotonic() + CLOCK_CONTROL_TIMEOUT_SECONDS
    validate_clock_control_socket(
        CLOCK_CONTROL_SOCKET,
        expected_gid=os.getegid(),
    )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(_remaining_clock_control_time(deadline))
        connection.connect(str(CLOCK_CONTROL_SOCKET))
        credentials = connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _server_pid, server_uid, _server_gid = struct.unpack("3i", credentials)
        if server_uid != 0:
            raise PermissionError("clock control peer is not root")
        connection.settimeout(_remaining_clock_control_time(deadline))
        connection.sendall(request_bytes)
        connection.shutdown(socket.SHUT_WR)
        return _read_clock_control_response(connection, deadline)


def _clock_control_result(
    wire_response: str,
    action: str,
    epoch: int | None,
) -> HostResult:
    common_failures = {
        "invalid request",
        "request timed out",
        "unauthorized peer",
        "server error",
    }
    if wire_response.startswith("ERR "):
        detail = wire_response[4:]
        action_failures = (
            {"NTP enable request failed"}
            if action == "sync"
            else {"clock set failed"}
        )
        if detail not in common_failures | action_failures:
            return HostResult("Err - invalid clock control response")
        return HostResult(f"Err - {detail}")
    if action == "sync":
        if wire_response == "OK NTP sync requested":
            return HostResult("OK - NTP sync requested")
        partial = re.fullmatch(
            r"PARTIAL NTP enabled; (chrony step request failed|"
            r"systemd-timesyncd restart failed)",
            wire_response,
        )
        if partial is not None:
            return HostResult(f"Warning - NTP enabled; {partial.group(1)}")
        return HostResult("Err - invalid clock control response")

    assert epoch is not None
    if wire_response == f"OK clock set to {epoch}; NTP sync requested":
        return HostResult(f"OK - clock set to {epoch}; NTP sync requested")
    ntp_enable_failure = (
        f"PARTIAL clock set to {epoch}; NTP enable request failed"
    )
    if wire_response == ntp_enable_failure:
        return HostResult(
            f"Warning - clock set to {epoch}; NTP enable request failed"
        )
    for failure in (
        "chrony step request failed",
        "systemd-timesyncd restart failed",
    ):
        if wire_response == (
            f"PARTIAL clock set to {epoch}; NTP enabled; {failure}"
        ):
            return HostResult(
                f"Warning - clock set to {epoch}; NTP enabled; {failure}"
            )
    return HostResult("Err - invalid clock control response")


def run_clock_control(action: str, epoch: int | None = None) -> HostResult:
    if action not in ("set", "sync"):
        return HostResult("Err - unsupported clock action")
    if action == "set":
        if epoch is None or not MIN_CLOCK_EPOCH <= epoch <= MAX_CLOCK_EPOCH:
            return HostResult("Err - clock epoch must be from 2020 through 2099")
        request = f"set {epoch}"
    else:
        if epoch is not None:
            return HostResult("Err - invalid clock sync request")
        request = "sync"
    try:
        response = _exchange_clock_control(request)
    except (TimeoutError, socket.timeout):
        return HostResult("Err - clock control timed out")
    except PermissionError:
        return HostResult("Err - clock control peer authentication failed")
    except (OSError, ValueError):
        return HostResult("Err - clock control unavailable")
    return _clock_control_result(response, action, epoch)


def validate_host_actions_socket(
    path: Path = HOST_ACTIONS_SOCKET,
    *,
    expected_gid: int,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"host actions socket is missing: {path}") from exc
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError(f"host actions path must be a socket: {path}")
    if metadata.st_uid != 0:
        raise ValueError(f"host actions socket must be root-owned: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o660:
        raise ValueError(f"host actions socket must have mode 0660: {path}")
    if metadata.st_gid != expected_gid:
        raise ValueError(
            "host actions socket group must match the endpoint's primary group"
        )


def _remaining_host_action_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("host action deadline expired")
    return remaining


def _read_host_action_response(
    connection: socket.socket,
    deadline: float,
) -> str:
    response = bytearray()
    while True:
        connection.settimeout(_remaining_host_action_time(deadline))
        chunk = connection.recv(HOST_ACTION_MAX_RESPONSE_BYTES + 1 - len(response))
        if not chunk:
            break
        response.extend(chunk)
        if len(response) > HOST_ACTION_MAX_RESPONSE_BYTES:
            raise ValueError("host action response is too long")
    if not response.endswith(b"\n") or response.count(b"\n") != 1:
        raise ValueError("host action response framing is invalid")
    try:
        line = response[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("host action response is not ASCII") from exc
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in line):
        raise ValueError("host action response contains control characters")
    return line


def _validate_host_action_response(
    response: str,
    verb: str,
    action: str | None,
    operation_id: str,
) -> str:
    if response in HOST_ACTION_ERROR_RESPONSES:
        return response
    if verb == "status" and response == f"UNKNOWN {operation_id}":
        return response
    states = (
        ("PREPARED", "IN-PROGRESS", "SCHEDULED", "AMBIGUOUS")
        if verb in ("prepare", "status")
        else ("IN-PROGRESS", "SCHEDULED", "AMBIGUOUS")
    )
    actions = HOST_ACTION_NAMES if verb == "status" else (action,)
    if any(
        response == f"{state_name} {candidate_action} {operation_id}"
        for state_name in states
        for candidate_action in actions
    ):
        return response
    raise ValueError("invalid host action response")


def exchange_host_action(
    verb: str,
    operation_id: str,
    action: str | None = None,
    *,
    deadline: float | None = None,
) -> str:
    if HOST_ACTION_OPERATION_RE.fullmatch(operation_id) is None:
        raise ValueError("invalid host action operation ID")
    if verb == "status":
        if action is not None:
            raise ValueError("status must not include an action")
        request = f"status {operation_id}\n"
    elif verb in ("prepare", "commit") and action in HOST_ACTION_NAMES:
        request = f"{verb} {action} {operation_id}\n"
    else:
        raise ValueError("invalid host action request")
    deadline = (
        time.monotonic() + HOST_ACTION_TOTAL_TIMEOUT_SECONDS
        if deadline is None else deadline
    )
    validate_host_actions_socket(
        HOST_ACTIONS_SOCKET,
        expected_gid=os.getegid(),
    )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(_remaining_host_action_time(deadline))
        connection.connect(str(HOST_ACTIONS_SOCKET))
        encoded_credentials = connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _pid, peer_uid, _peer_gid = struct.unpack("3i", encoded_credentials)
        # With socket activation this authenticates the root-created listening
        # socket (normally PID 1), while the root-owned path protects connect().
        if peer_uid != 0:
            raise PermissionError("host action peer is not root")
        connection.settimeout(_remaining_host_action_time(deadline))
        connection.sendall(request.encode("ascii"))
        connection.shutdown(socket.SHUT_WR)
        response = _read_host_action_response(connection, deadline)
    return _validate_host_action_response(
        response, verb, action, operation_id
    )


def host_action_operation_id(
    repeater_public_key: str,
    request: HostRequest,
) -> str:
    normalized_key = normalize_key(
        repeater_public_key, 32, "repeater public key"
    )
    if re.fullmatch(r"[0-9A-Fa-f]{8}", request.request_id) is None:
        raise ValueError("invalid host request ID")
    if re.fullmatch(r"[0-9A-Fa-f]{16}", request.request_nonce) is None:
        raise ValueError("invalid host request nonce")
    digest = hashlib.sha256()
    digest.update(b"MeshCore HOSTCLI/1 host action operation\x00")
    digest.update(bytes.fromhex(normalized_key))
    digest.update(bytes.fromhex(request.request_id))
    digest.update(bytes.fromhex(request.request_nonce))
    return digest.digest()[:16].hex().upper()


def _safe_config_text(value: Any, label: str, max_bytes: int = 128) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    if len(value.encode("utf-8")) > max_bytes or any(
        not character.isprintable() for character in value
    ):
        raise ValueError(f"{label} contains invalid characters or is too long")
    return value


def load_programs_file(path: Path) -> dict[str, ProgramDefinition]:
    file_mode = stat.S_IMODE(path.stat().st_mode)
    if file_mode & 0o022:
        raise ValueError(
            f"program allowlist {path} must not be group/world writable"
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != {"programs"}:
        raise ValueError("program allowlist must contain only a programs object")
    raw_programs = document["programs"]
    if not isinstance(raw_programs, dict) or len(raw_programs) > 16:
        raise ValueError("programs must be an object with at most 16 entries")

    programs: dict[str, ProgramDefinition] = {}
    for raw_alias, raw_definition in raw_programs.items():
        alias = _safe_config_text(raw_alias, "program alias", 32)
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", alias):
            raise ValueError(f"invalid program alias: {alias}")
        if not isinstance(raw_definition, dict):
            raise ValueError(f"program {alias} must be an object")
        allowed_fields = {"argv", "arguments", "timeout_seconds"}
        unknown_fields = set(raw_definition) - allowed_fields
        if unknown_fields:
            raise ValueError(
                f"program {alias} has unknown fields: "
                + ", ".join(sorted(unknown_fields))
            )

        raw_argv = raw_definition.get("argv")
        if not isinstance(raw_argv, list) or not 1 <= len(raw_argv) <= 16:
            raise ValueError(f"program {alias} argv must contain 1-16 strings")
        argv = tuple(
            _safe_config_text(value, f"program {alias} argv", 128)
            for value in raw_argv
        )
        executable = Path(argv[0])
        if not executable.is_absolute():
            raise ValueError(f"program {alias} executable must be absolute")
        resolved_executable = executable.resolve(strict=True)
        executable_mode = stat.S_IMODE(resolved_executable.stat().st_mode)
        if (
            not resolved_executable.is_file()
            or not os.access(resolved_executable, os.X_OK)
            or executable_mode & 0o022
        ):
            raise ValueError(
                f"program {alias} executable must be executable and not "
                "group/world writable"
            )
        argv = (str(resolved_executable), *argv[1:])

        raw_arguments = raw_definition.get("arguments", [])
        if not isinstance(raw_arguments, list) or len(raw_arguments) > 8:
            raise ValueError(f"program {alias} arguments must have at most 8 entries")
        arguments: list[ProgramArgumentRule] = []
        for index, raw_rule in enumerate(raw_arguments):
            if not isinstance(raw_rule, dict):
                raise ValueError(f"program {alias} argument {index} must be an object")
            name = _safe_config_text(
                raw_rule.get("name"), f"program {alias} argument name", 24
            )
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", name):
                raise ValueError(f"program {alias} has invalid argument name {name}")
            kind = raw_rule.get("type")
            common_fields = {"name", "type"}
            if kind == "choice":
                if set(raw_rule) - (common_fields | {"choices"}):
                    raise ValueError(
                        f"program {alias} choice {name} has unknown fields"
                    )
                raw_choices = raw_rule.get("choices")
                if not isinstance(raw_choices, list) or not 1 <= len(raw_choices) <= 32:
                    raise ValueError(f"program {alias} choice {name} needs 1-32 values")
                choices = tuple(
                    _safe_config_text(
                        choice, f"program {alias} choice {name}", 64
                    )
                    for choice in raw_choices
                )
                if any(choice.startswith("-") for choice in choices):
                    raise ValueError(
                        f"program {alias} choice {name} cannot start with '-'"
                    )
                arguments.append(ProgramArgumentRule(name, kind, choices=choices))
            elif kind == "integer":
                if set(raw_rule) - (common_fields | {"min", "max"}):
                    raise ValueError(
                        f"program {alias} integer {name} has unknown fields"
                    )
                minimum = raw_rule.get("min")
                maximum = raw_rule.get("max")
                if (
                    isinstance(minimum, bool)
                    or isinstance(maximum, bool)
                    or not isinstance(minimum, int)
                    or not isinstance(maximum, int)
                    or minimum < 0
                    or maximum > 1000000000
                    or minimum > maximum
                ):
                    raise ValueError(
                        f"program {alias} integer {name} has invalid bounds"
                    )
                arguments.append(
                    ProgramArgumentRule(
                        name, kind, minimum=minimum, maximum=maximum
                    )
                )
            elif kind == "token":
                if set(raw_rule) - (common_fields | {"max_bytes"}):
                    raise ValueError(f"program {alias} token {name} has unknown fields")
                max_bytes = raw_rule.get("max_bytes", 32)
                if (
                    isinstance(max_bytes, bool)
                    or not isinstance(max_bytes, int)
                    or not 1 <= max_bytes <= 64
                ):
                    raise ValueError(
                        f"program {alias} token {name} has invalid max_bytes"
                    )
                arguments.append(
                    ProgramArgumentRule(name, kind, max_bytes=max_bytes)
                )
            else:
                raise ValueError(
                    f"program {alias} argument {name} has unsupported type"
                )

        timeout_seconds = raw_definition.get("timeout_seconds", 3)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 5
        ):
            raise ValueError(f"program {alias} timeout_seconds must be 1-5")
        programs[alias] = ProgramDefinition(
            alias=alias,
            argv=argv,
            arguments=tuple(arguments),
            timeout_seconds=timeout_seconds,
        )
    return programs


def _validate_program_argument(
    value: str, rule: ProgramArgumentRule
) -> str | None:
    if rule.kind == "choice":
        return value if value in rule.choices else None
    if rule.kind == "integer":
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
            return None
        parsed = int(value)
        if rule.minimum is None or rule.maximum is None:
            return None
        if parsed < rule.minimum or parsed > rule.maximum:
            return None
        return str(parsed)
    if rule.kind == "token":
        if len(value.encode("utf-8")) > rule.max_bytes:
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@+,-]*", value):
            return None
        return value
    return None


def run_configured_program(
    request_text: str,
    programs: dict[str, ProgramDefinition],
) -> HostResult:
    if any(not character.isprintable() for character in request_text):
        return HostResult("Err - invalid program request")
    try:
        tokens = shlex.split(request_text, posix=True)
    except ValueError:
        return HostResult("Err - invalid program request")
    if len(tokens) < 2 or tokens[0] != "run":
        return HostResult("Err - use: run <alias> [arguments]")
    definition = programs.get(tokens[1])
    if definition is None:
        return HostResult("Err - program alias is not allowed")
    supplied = tokens[2:]
    if len(supplied) != len(definition.arguments):
        names = " ".join(f"<{rule.name}>" for rule in definition.arguments)
        usage = f"Err - use: run {definition.alias}"
        if names:
            usage += " " + names
        return HostResult(usage)

    validated: list[str] = []
    for value, rule in zip(supplied, definition.arguments):
        safe_value = _validate_program_argument(value, rule)
        if safe_value is None:
            return HostResult(f"Err - invalid {rule.name}")
        validated.append(safe_value)

    argv = [*definition.argv, *validated]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=definition.timeout_seconds,
            check=False,
            shell=False,
            cwd="/",
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired:
        return HostResult(f"Err - {definition.alias} timed out")
    except OSError:
        return HostResult(f"Err - {definition.alias} could not start")
    if completed.returncode != 0:
        return HostResult(
            f"Err - {definition.alias} failed ({completed.returncode})"
        )
    output = bounded_line_text(completed.stdout or "")
    if output == "Err - empty host reply":
        output = f"OK - {definition.alias} completed"
    return HostResult(output)


def handle_request(
    request_text: str,
    temperature_path: Path,
    *,
    allow_reboot: bool = False,
    allow_network_restart: bool = False,
    allow_clock_control: bool = False,
    uptime_path: Path = Path("/proc/uptime"),
    load_path: Path = Path("/proc/loadavg"),
    memory_path: Path = Path("/proc/meminfo"),
    disk_path: Path = Path("/"),
    programs: dict[str, ProgramDefinition] | None = None,
) -> HostResult:
    # Built-ins are exact strings. Program requests reach argv only after an
    # alias lookup, exact arity check, and per-argument allowlist validation;
    # request text is never used as a shell command, executable, or file path.
    if request_text == "help":
        program_aliases = ",".join(sorted(programs or {})) or "off"
        return HostResult(
            "Commands: cpu-temp,hostname,uptime,load,memory,disk-free,clock; "
            f"clock-control={'on' if allow_clock_control else 'off'}; "
            f"network={'on' if allow_network_restart else 'off'}; "
            f"reboot={'on' if allow_reboot else 'off'}; run={program_aliases}"
        )
    if request_text == "cpu-temp":
        return HostResult(read_cpu_temperature(temperature_path))
    if request_text == "hostname":
        return HostResult("Hostname " + socket.gethostname())
    if request_text == "uptime":
        return HostResult(read_uptime(uptime_path))
    if request_text == "load":
        return HostResult(read_load_average(load_path))
    if request_text == "memory":
        return HostResult(read_memory(memory_path))
    if request_text == "disk-free":
        return HostResult(read_disk_free(disk_path))
    if request_text == "clock status":
        return HostResult(read_clock_status())
    if request_text == "clock sync":
        if not allow_clock_control:
            return HostResult("Err - host clock control is disabled")
        return run_clock_control("sync")
    if request_text.startswith("clock set"):
        match = re.fullmatch(r"clock set (0|[1-9][0-9]*)", request_text)
        if match is None:
            return HostResult("Err - use: clock set <unix_epoch>")
        epoch = int(match.group(1))
        if not MIN_CLOCK_EPOCH <= epoch <= MAX_CLOCK_EPOCH:
            return HostResult("Err - clock epoch must be from 2020 through 2099")
        if not allow_clock_control:
            return HostResult("Err - host clock control is disabled")
        return run_clock_control("set", epoch)
    if request_text == "reboot":
        if not allow_reboot:
            return HostResult("Err - host reboot is disabled")
        return HostResult("", host_action="reboot")
    if request_text == "network restart":
        if not allow_network_restart:
            return HostResult("Err - host network restart is disabled")
        return HostResult("", host_action="network-restart")
    if request_text.startswith("action status"):
        match = re.fullmatch(r"action status ([0-9A-F]{32})", request_text)
        if match is None:
            return HostResult("Err - use: action status <operation_id>")
        if not (allow_reboot or allow_network_restart):
            return HostResult("Err - host recovery actions are disabled")
        return HostResult(
            "", host_action="status", operation_id=match.group(1)
        )
    if request_text == "run" or request_text.startswith("run "):
        return run_configured_program(request_text, programs or {})
    return HostResult("Err - unsupported host request")


def bounded_line_text(text: str, max_bytes: int = MAX_REPLY_BYTES) -> str:
    line_safe = "".join(
        character if character.isprintable() else " "
        for character in text
    ).strip()
    if not line_safe:
        line_safe = "Err - empty host reply"
    encoded = line_safe.encode("utf-8")
    if len(encoded) <= max_bytes:
        return line_safe
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def make_serial_reply(request: HostRequest, response: str) -> str:
    return (
        f"host.reply {request.request_id} {request.request_nonce} "
        f"{bounded_line_text(response)}"
    )


def generate_service_challenge() -> str:
    while True:
        challenge = secrets.token_hex(8).upper()
        if int(challenge, 16) != 0:
            return challenge


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_auth_token(
    key: ServiceKey,
    claims: dict[str, Any],
    expiry_seconds: int = 30,
    now: int | None = None,
    signer: Signer | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    header = {"alg": "Ed25519", "typ": "JWT"}
    payload: dict[str, Any] = {
        "publicKey": key.public_key,
        "iat": issued_at,
        "exp": issued_at + expiry_seconds,
    }
    payload.update(claims)
    header_part = _base64url(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    )
    payload_part = _base64url(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_part}.{payload_part}"
    signer = signer or _orlp_signer()
    signature = signer(
        signing_input.encode("ascii"),
        bytes.fromhex(key.public_key),
        bytes.fromhex(key.private_key),
    )
    if len(signature) != 64:
        raise ValueError("Ed25519 signer returned the wrong signature length")
    return f"{signing_input}.{signature.hex().upper()}"


class HostCliEndpoint:
    def __init__(
        self,
        client: Any,
        repeater_public_key: str,
        service_key: ServiceKey,
        command_topic: str,
        temperature_path: Path,
        allow_reboot: bool = False,
        allow_network_restart: bool = False,
        allow_clock_control: bool = False,
        host_action_exchange: Callable[..., str] | None = None,
        programs: dict[str, ProgramDefinition] | None = None,
        dedupe_seconds: float = 60.0,
        challenge_timeout_seconds: float = CHALLENGE_TIMEOUT_SECONDS,
        challenge_generator: Callable[[], str] = generate_service_challenge,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.repeater_public_key = normalize_key(
            repeater_public_key, 32, "repeater public key"
        )
        self.service_key = service_key
        self.command_topic = command_topic
        self.temperature_path = temperature_path
        self.allow_reboot = allow_reboot
        self.allow_network_restart = allow_network_restart
        self.allow_clock_control = allow_clock_control
        self.host_action_exchange = (
            exchange_host_action
            if host_action_exchange is None else host_action_exchange
        )
        self.programs = programs or {}
        self.dedupe_seconds = dedupe_seconds
        self.challenge_timeout_seconds = challenge_timeout_seconds
        self.challenge_generator = challenge_generator
        self.monotonic = monotonic
        self.seen: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.pending: PendingHostAction | None = None
        self._action_queue: queue.Queue[VerifiedHostAction] = queue.Queue(
            maxsize=HOST_ACTION_QUEUE_DEPTH
        )
        self._action_worker_lock = threading.Lock()
        self._action_worker_started = False
        self._action_slot = threading.BoundedSemaphore(value=1)

    def _start_action_worker(self) -> None:
        with self._action_worker_lock:
            if self._action_worker_started:
                return
            worker = threading.Thread(
                target=self._action_worker_loop,
                name="meshcore-host-actions",
                daemon=True,
            )
            worker.start()
            self._action_worker_started = True

    def _enqueue_host_action(self, work: VerifiedHostAction) -> bool:
        if not self._action_slot.acquire(blocking=False):
            self._publish_serial_reply(
                work.request,
                "Err - another host action is active; action was not reserved",
            )
            return False
        try:
            self._action_queue.put_nowait(work)
        except queue.Full:
            self._action_slot.release()
            self._publish_serial_reply(
                work.request,
                "Err - host action queue is full; action was not reserved",
            )
            return False
        self._start_action_worker()
        return True

    def _action_worker_loop(self) -> None:
        while True:
            work = self._action_queue.get()
            try:
                self._process_host_action(work)
            except Exception:
                # The worker must remain available for later status checks. A
                # failed or ambiguous operation is deliberately never retried.
                LOGGER.exception(
                    "Host action worker failed for operation %s; not retrying",
                    work.operation_id,
                )
            finally:
                self._action_queue.task_done()
                self._action_slot.release()

    def _broker_request(
        self,
        verb: str,
        operation_id: str,
        action: str | None,
        deadline: float,
    ) -> str:
        response = self.host_action_exchange(
            verb,
            operation_id,
            action,
            deadline=deadline,
        )
        return _validate_host_action_response(
            response, verb, action, operation_id
        )

    def _publish_action_status(
        self,
        work: VerifiedHostAction,
        deadline: float,
    ) -> None:
        try:
            response = self._broker_request(
                "status", work.operation_id, None, deadline
            )
        except (OSError, PermissionError, TimeoutError, ValueError, socket.timeout):
            reply = (
                "Err - host action status unavailable for "
                + work.operation_id
            )
        else:
            if response == f"UNKNOWN {work.operation_id}":
                reply = f"Host action {work.operation_id} is unknown"
            elif response in HOST_ACTION_ERROR_RESPONSES:
                reply = f"Err - host action status failed for {work.operation_id}"
            else:
                state_name, action, _operation_id = response.split(" ")
                reply = (
                    f"Host action {work.operation_id}: {action} "
                    f"{state_name.lower()}"
                )
        self._publish_serial_reply(work.request, reply)

    @staticmethod
    def _prepare_failure_reply(
        action: str,
        operation_id: str,
        response: str,
    ) -> str:
        if response == "ERR action disabled":
            return f"Err - {action} is disabled by root policy; {operation_id}"
        if response == "ERR operation conflict":
            return f"Err - operation {operation_id} conflicts; not retried"
        if response == "ERR reboot pending":
            return f"Err - another reboot is pending; {operation_id} not reserved"
        if response == "ERR state full":
            return f"Err - host action state is full; {operation_id} not reserved"
        return f"Err - host action unavailable; {operation_id} not retried"

    def _process_host_action(self, work: VerifiedHostAction) -> None:
        deadline = time.monotonic() + HOST_ACTION_TOTAL_TIMEOUT_SECONDS
        if work.action == "status":
            self._publish_action_status(work, deadline)
            return

        try:
            prepared = self._broker_request(
                "prepare", work.operation_id, work.action, deadline
            )
        except (TimeoutError, socket.timeout):
            self._publish_serial_reply(
                work.request,
                f"Warning - {work.action} state unknown; "
                f"{work.operation_id} not retried",
            )
            return
        except (OSError, PermissionError, ValueError):
            self._publish_serial_reply(
                work.request,
                f"Err - {work.action} broker unavailable; "
                f"{work.operation_id} not retried",
            )
            return

        if prepared in HOST_ACTION_ERROR_RESPONSES:
            self._publish_serial_reply(
                work.request,
                self._prepare_failure_reply(
                    work.action, work.operation_id, prepared
                ),
            )
            return
        state_name = prepared.split(" ", 1)[0]
        if state_name == "SCHEDULED":
            self._publish_serial_reply(
                work.request,
                f"OK - {work.action} {work.operation_id} already scheduled",
            )
            return
        if state_name in ("IN-PROGRESS", "AMBIGUOUS"):
            self._publish_serial_reply(
                work.request,
                f"Warning - {work.action} {work.operation_id} outcome unknown; "
                "not retried",
            )
            return
        if state_name != "PREPARED":
            raise ValueError("unexpected host action prepare result")

        reply = (
            f"OK - reboot {work.operation_id} accepted; fixed "
            f"{HOST_ACTION_REBOOT_DELAY_SECONDS}s timer follows reply"
            if work.action == "reboot"
            else f"OK - network restart {work.operation_id} accepted after reply"
        )
        try:
            publish_result = self._publish_serial_reply(work.request, reply)
            wait_for_publish = getattr(publish_result, "wait_for_publish", None)
            is_published = getattr(publish_result, "is_published", None)
            if not callable(wait_for_publish) or not callable(is_published):
                LOGGER.error(
                    "MQTT client lacks publish confirmation for %s; not committing",
                    work.operation_id,
                )
                return
            wait_timeout = min(
                HOST_ACTION_PUBLISH_TIMEOUT_SECONDS,
                _remaining_host_action_time(deadline),
            )
            wait_for_publish(timeout=wait_timeout)
            if not is_published():
                LOGGER.error(
                    "Reply was not MQTT-confirmed for %s; not committing",
                    work.operation_id,
                )
                return
        except Exception:
            LOGGER.exception(
                "Reply publish failed for %s; not committing",
                work.operation_id,
            )
            return

        try:
            _remaining_host_action_time(deadline)
            committed = self._broker_request(
                "commit", work.operation_id, work.action, deadline
            )
        except (OSError, PermissionError, TimeoutError, ValueError, socket.timeout):
            LOGGER.exception(
                "Commit outcome is unknown for %s; not retrying",
                work.operation_id,
            )
            return
        if committed != f"SCHEDULED {work.action} {work.operation_id}":
            LOGGER.error(
                "Host action %s commit returned %r; not retrying",
                work.operation_id,
                committed,
            )
            return
        LOGGER.info(
            "Scheduled verified host action %s (%s)",
            work.operation_id,
            work.action,
        )

    def _already_seen(self, request: HostRequest) -> bool:
        now = self.monotonic()
        cutoff = now - self.dedupe_seconds
        while self.seen and next(iter(self.seen.values())) < cutoff:
            self.seen.popitem(last=False)
        key = (request.request_id, request.request_nonce)
        if key in self.seen:
            return True
        self.seen[key] = now
        while len(self.seen) > 64:
            self.seen.popitem(last=False)
        return False

    def _publish_serial_reply(
        self, request: HostRequest, response: str
    ) -> Any:
        serial_command = make_serial_reply(request, response)
        token = create_auth_token(
            self.service_key,
            {
                "command": serial_command,
                "target": self.repeater_public_key,
                "nonce": secrets.token_hex(16),
            },
        )
        result = self.client.publish(self.command_topic, token, qos=1)
        return_code = getattr(result, "rc", 0)
        if return_code != 0:
            raise RuntimeError(f"MQTT publish failed with code {return_code}")
        return result

    def _handle_request(self, request: HostRequest) -> bool:
        if self._already_seen(request):
            LOGGER.info("Ignoring duplicate host request %s", request.request_id)
            return True

        now = self.monotonic()
        if self.pending is not None and self.pending.expires_at > now:
            LOGGER.warning(
                "Ignoring host request %s while another claim is pending",
                request.request_id,
            )
            return True
        self.pending = None

        challenge = self.challenge_generator().upper()
        if (
            re.fullmatch(r"[0-9A-F]{16}", challenge) is None
            or int(challenge, 16) == 0
        ):
            self.seen.pop((request.request_id, request.request_nonce), None)
            raise RuntimeError("challenge generator returned an invalid value")
        self.pending = PendingHostAction(
            request=request,
            challenge=challenge,
            expires_at=now + self.challenge_timeout_seconds,
        )
        try:
            self._publish_serial_reply(request, "@claim=" + challenge)
        except Exception:
            self.pending = None
            self.seen.pop((request.request_id, request.request_nonce), None)
            raise
        LOGGER.info("Requested live proof for host request %s", request.request_id)
        return True

    def _handle_claim(self, claim: HostClaim) -> bool:
        pending = self.pending
        if pending is None:
            LOGGER.info("Ignoring host claim with no pending request")
            return True
        if pending.expires_at <= self.monotonic():
            self.pending = None
            LOGGER.info("Ignoring expired host claim %s", claim.request_id)
            return True
        request = pending.request
        if not (
            hmac.compare_digest(claim.request_id, request.request_id)
            and hmac.compare_digest(claim.request_nonce, request.request_nonce)
            and hmac.compare_digest(claim.challenge, pending.challenge)
        ):
            LOGGER.warning("Ignoring mismatched host claim %s", claim.request_id)
            return True

        # Remove the one-time proof before doing anything with side effects.
        # MQTT redelivery or a replay can therefore never execute the action twice.
        self.pending = None
        response = handle_request(
            request.text,
            self.temperature_path,
            allow_reboot=self.allow_reboot,
            allow_network_restart=self.allow_network_restart,
            allow_clock_control=self.allow_clock_control,
            programs=self.programs,
        )
        if response.host_action is not None:
            operation_id = (
                response.operation_id
                if response.host_action == "status"
                else host_action_operation_id(self.repeater_public_key, request)
            )
            if operation_id is None:
                raise ValueError("host action is missing its operation ID")
            self._enqueue_host_action(
                VerifiedHostAction(
                    request=request,
                    action=response.host_action,
                    operation_id=operation_id,
                )
            )
        else:
            self._publish_serial_reply(request, response.text)
        LOGGER.info(
            "Replied to verified host request %s (%r)",
            request.request_id,
            request.text,
        )
        return True

    def handle_mqtt_message(self, mqtt_payload: bytes | str) -> bool:
        request = parse_and_verify_request(
            mqtt_payload, self.repeater_public_key
        )
        if request is not None:
            return self._handle_request(request)
        claim = parse_and_verify_claim(mqtt_payload, self.repeater_public_key)
        if claim is not None:
            return self._handle_claim(claim)
        return False


def _read_password(path: Path | None) -> str | None:
    if path is None:
        return None
    password = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise ValueError("MQTT password file is empty")
    return password


def run_endpoint(args: argparse.Namespace) -> None:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError(
            "paho-mqtt is required; use meshcoretomqtt's virtualenv"
        ) from exc

    service_key = load_service_key(args.service_key)
    repeater_public_key = normalize_key(
        args.repeater_key, 32, "repeater public key"
    )
    iata = args.iata.upper()
    if not re.fullmatch(r"[A-Z0-9]{3}", iata):
        raise ValueError("IATA must be exactly three letters or digits")

    request_topic = args.request_topic or (
        f"meshcore/{iata}/{repeater_public_key}/debug"
    )
    command_topic = args.command_topic or (
        f"meshcore/{iata}/{repeater_public_key}/serial/commands"
    )
    client_id = args.client_id or (
        "meshcore-host-" + repeater_public_key[:8].lower()
    )

    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id
        )
    except AttributeError:
        client = mqtt.Client(client_id=client_id)

    if args.username is not None:
        client.username_pw_set(args.username, _read_password(args.password_file))
    elif args.password_file is not None:
        raise ValueError("--password-file requires --username")
    if args.tls:
        client.tls_set(ca_certs=str(args.ca_cert) if args.ca_cert else None)

    if args.allow_clock_control:
        validate_clock_control_socket(
            CLOCK_CONTROL_SOCKET,
            expected_gid=os.getegid(),
        )
    if args.allow_reboot or args.allow_network_restart:
        validate_host_actions_socket(
            HOST_ACTIONS_SOCKET,
            expected_gid=os.getegid(),
        )

    programs = (
        load_programs_file(args.programs_file)
        if args.programs_file is not None else {}
    )

    endpoint = HostCliEndpoint(
        client=client,
        repeater_public_key=repeater_public_key,
        service_key=service_key,
        command_topic=command_topic,
        temperature_path=args.temperature_path,
        allow_reboot=args.allow_reboot,
        allow_network_restart=args.allow_network_restart,
        allow_clock_control=args.allow_clock_control,
        programs=programs,
    )

    def on_connect(
        connected_client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any = None,
    ) -> None:
        if reason_code != 0:
            LOGGER.error("MQTT connection failed: %s", reason_code)
            return
        connected_client.subscribe(request_topic, qos=1)
        LOGGER.info("Listening for signed host requests on %s", request_topic)

    def on_message(
        _client: Any, _userdata: Any, message: Any
    ) -> None:
        try:
            endpoint.handle_mqtt_message(message.payload)
        except (RuntimeError, ValueError) as exc:
            LOGGER.warning("Rejected host request: %s", exc)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port, keepalive=60)
    LOGGER.info("Connecting to MQTT broker %s:%d", args.broker, args.port)
    client.loop_forever(retry_first_connection=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verified MeshCore host CLI Raspberry Pi MQTT endpoint"
    )
    parser.add_argument(
        "--generate-key", type=Path,
        help="create a mode-0600 service key file and exit",
    )
    parser.add_argument("--broker")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--iata")
    parser.add_argument("--repeater-key")
    parser.add_argument("--service-key", type=Path)
    parser.add_argument("--username")
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--ca-cert", type=Path)
    parser.add_argument("--request-topic")
    parser.add_argument("--command-topic")
    parser.add_argument("--client-id")
    parser.add_argument(
        "--temperature-path", type=Path,
        default=Path("/sys/class/thermal/thermal_zone0/temp"),
    )
    parser.add_argument(
        "--allow-reboot", action="store_true",
        help="allow the exact 'reboot' request for the attached host",
    )
    parser.add_argument(
        "--allow-network-restart", action="store_true",
        help="allow the exact 'network restart' request for the attached host",
    )
    parser.add_argument(
        "--allow-clock-control", action="store_true",
        help="allow clock set/sync through the root-authenticated Unix socket",
    )
    parser.add_argument(
        "--programs-file", type=Path,
        help="JSON allowlist for fixed programs and validated arguments",
    )
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        if args.generate_key is not None:
            public_key = generate_service_key(args.generate_key)
            print(f"Created {args.generate_key} (mode 600)")
            print(f"Add this public key to allowed_companions: {public_key}")
            return 0
        required = {
            "--broker": args.broker,
            "--iata": args.iata,
            "--repeater-key": args.repeater_key,
            "--service-key": args.service_key,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error("required for service mode: " + ", ".join(missing))
        run_endpoint(args)
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
