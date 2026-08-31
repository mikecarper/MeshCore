#!/usr/bin/python3
"""Root clock-control service for the MeshCore host endpoint.

The service is activated by ``meshcore-clock-control.socket``.  It accepts one
bounded ASCII line per connection and authorizes the client using Linux
``SO_PEERCRED`` before parsing or executing the request.  Only ``sync`` and
``set <canonical_epoch>`` exist in the protocol.
"""

from __future__ import annotations

import argparse
import grp
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import struct
import subprocess
import sys
import time
from typing import Mapping


MIN_CLOCK_EPOCH = 1577836800  # 2020-01-01T00:00:00Z
MAX_CLOCK_EPOCH = 4102444799  # 2099-12-31T23:59:59Z
CLOCK_CONTROL_SOCKET = Path("/run/meshcore-clock-control.sock")
SOCKET_MODE = 0o660
SYSTEMD_LISTEN_FDS_START = 3
MAX_REQUEST_BYTES = 32
MAX_RESPONSE_BYTES = 192
CLIENT_IO_TIMEOUT_SECONDS = 1.0
CHILD_TIMEOUT_SECONDS = 1.5
MINIMAL_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
}
NTP_ENABLE_ERROR = "NTP enable request failed"
CHRONY_STEP_ERROR = "chrony step request failed"
TIMESYNCD_RESTART_ERROR = "systemd-timesyncd restart failed"
CHRONY_STEP_SERVICE = "meshcore-chrony-step.service"


def run_fixed(command: list[str]) -> bool:
    """Run one compile-time-selected argv with a short hard timeout."""
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CHILD_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            cwd="/",
            env=MINIMAL_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def request_ntp_sync() -> str | None:
    if not run_fixed(["/usr/bin/timedatectl", "set-ntp", "true"]):
        return NTP_ENABLE_ERROR
    chronyc = Path("/usr/bin/chronyc")
    if chronyc.is_file() and os.access(chronyc, os.X_OK):
        if not run_fixed(
            ["/usr/bin/systemctl", "start", CHRONY_STEP_SERVICE]
        ):
            return CHRONY_STEP_ERROR
        return None
    if not run_fixed(
        ["/usr/bin/systemctl", "restart", "systemd-timesyncd.service"]
    ):
        return TIMESYNCD_RESTART_ERROR
    return None


def set_clock(epoch: int) -> tuple[bool, str | None]:
    # CLOCK_REALTIME can be changed while NTP remains enabled. Never turn NTP
    # off: a crash or timeout must not strand the recovery host without NTP.
    try:
        time.clock_settime(time.CLOCK_REALTIME, float(epoch))
    except (OSError, OverflowError, ValueError):
        return False, None
    return True, request_ntp_sync()


def parse_request(request: bytes) -> tuple[str, int | None]:
    if not request or len(request) > MAX_REQUEST_BYTES:
        raise ValueError("invalid request length")
    if not request.endswith(b"\n") or request.count(b"\n") != 1:
        raise ValueError("invalid request framing")
    try:
        line = request[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("request is not ASCII") from exc
    if line == "sync":
        return "sync", None
    match = re.fullmatch(r"set (0|[1-9][0-9]*)", line)
    if match is None:
        raise ValueError("invalid request")
    epoch = int(match.group(1))
    if not MIN_CLOCK_EPOCH <= epoch <= MAX_CLOCK_EPOCH:
        raise ValueError("epoch outside accepted range")
    return "set", epoch


def process_request(request: bytes) -> str:
    action, epoch = parse_request(request)
    if action == "sync":
        error = request_ntp_sync()
        if error is None:
            return "OK NTP sync requested"
        if error == NTP_ENABLE_ERROR:
            return f"ERR {error}"
        return f"PARTIAL NTP enabled; {error}"

    assert epoch is not None
    changed, error = set_clock(epoch)
    if not changed:
        return "ERR clock set failed"
    if error is None:
        return f"OK clock set to {epoch}; NTP sync requested"
    if error == NTP_ENABLE_ERROR:
        return f"PARTIAL clock set to {epoch}; {error}"
    return f"PARTIAL clock set to {epoch}; NTP enabled; {error}"


def peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    encoded = connection.getsockopt(
        socket.SOL_SOCKET,
        socket.SO_PEERCRED,
        struct.calcsize("3i"),
    )
    return struct.unpack("3i", encoded)


def receive_request(connection: socket.socket) -> bytes:
    request = bytearray()
    deadline = time.monotonic() + CLIENT_IO_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("clock control request deadline expired")
        connection.settimeout(remaining)
        chunk = connection.recv(MAX_REQUEST_BYTES + 1 - len(request))
        if not chunk:
            break
        request.extend(chunk)
        if len(request) > MAX_REQUEST_BYTES:
            raise ValueError("request is too long")
    return bytes(request)


def send_response(connection: socket.socket, response: str) -> None:
    encoded = (response + "\n").encode("ascii", errors="strict")
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = b"ERR server error\n"
    connection.settimeout(CLIENT_IO_TIMEOUT_SECONDS)
    connection.sendall(encoded)


def handle_connection(
    connection: socket.socket,
    *,
    allowed_uid: int,
    allowed_gid: int,
) -> bool:
    try:
        _pid, peer_uid, peer_gid = peer_credentials(connection)
    except OSError:
        return False
    if peer_uid != allowed_uid or peer_gid != allowed_gid:
        try:
            send_response(connection, "ERR unauthorized peer")
        except (OSError, socket.timeout):
            pass
        return False
    try:
        request = receive_request(connection)
    except socket.timeout:
        response = "ERR request timed out"
    except (OSError, ValueError):
        response = "ERR invalid request"
    else:
        try:
            response = process_request(request)
        except ValueError:
            response = "ERR invalid request"
        except Exception:
            # Never disclose exception text across the privilege boundary.
            response = "ERR server error"
    try:
        send_response(connection, response)
    except (OSError, socket.timeout):
        return False
    return not response.startswith("ERR ")


def validate_socket_metadata(metadata: os.stat_result, expected_gid: int) -> None:
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError("clock control path is not a socket")
    if metadata.st_uid != 0:
        raise ValueError("clock control socket is not root-owned")
    if metadata.st_gid != expected_gid:
        raise ValueError("clock control socket has the wrong group")
    if stat.S_IMODE(metadata.st_mode) != SOCKET_MODE:
        raise ValueError("clock control socket must have mode 0660")


def validate_listening_socket(
    listener: socket.socket,
    *,
    path: Path,
    expected_gid: int,
) -> None:
    if listener.family != socket.AF_UNIX:
        raise ValueError("activation socket must use AF_UNIX")
    if listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
        raise ValueError("activation socket must use SOCK_STREAM")
    if listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1:
        raise ValueError("activation socket is not listening")
    socket_name = listener.getsockname()
    if not isinstance(socket_name, str) or Path(socket_name) != path:
        raise ValueError("activation socket has the wrong path")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError("clock control socket path is missing") from exc
    validate_socket_metadata(metadata, expected_gid)


def activation_socket(
    environment: Mapping[str, str] | None = None,
) -> socket.socket:
    variables = os.environ if environment is None else environment
    if variables.get("LISTEN_PID") != str(os.getpid()):
        raise ValueError("service must be started by systemd socket activation")
    if variables.get("LISTEN_FDS") != "1":
        raise ValueError("service requires exactly one activation socket")
    descriptor_names = variables.get("LISTEN_FDNAMES")
    if descriptor_names not in (None, "clock-control"):
        raise ValueError("activation socket has an unexpected descriptor name")
    try:
        return socket.socket(fileno=SYSTEMD_LISTEN_FDS_START)
    except OSError as exc:
        raise ValueError("activation socket descriptor is unavailable") from exc


def resolve_service_identity(user: str, group: str) -> tuple[int, int]:
    try:
        user_record = pwd.getpwnam(user)
        group_record = grp.getgrnam(group)
    except KeyError as exc:
        raise ValueError("configured service user or group does not exist") from exc
    if user_record.pw_gid != group_record.gr_gid:
        raise ValueError("configured group must be the service user's primary group")
    return user_record.pw_uid, group_record.gr_gid


def serve(
    listener: socket.socket,
    *,
    allowed_uid: int,
    allowed_gid: int,
) -> None:
    while True:
        connection, _address = listener.accept()
        with connection:
            handle_connection(
                connection,
                allowed_uid=allowed_uid,
                allowed_gid=allowed_gid,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Root-only MeshCore clock-control socket service"
    )
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--service-group", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ValueError("clock control service requires root")
        allowed_uid, allowed_gid = resolve_service_identity(
            arguments.service_user,
            arguments.service_group,
        )
        listener = activation_socket()
        validate_listening_socket(
            listener,
            path=CLOCK_CONTROL_SOCKET,
            expected_gid=allowed_gid,
        )
        serve(
            listener,
            allowed_uid=allowed_uid,
            allowed_gid=allowed_gid,
        )
    except (OSError, ValueError) as exc:
        print(f"meshcore-clock-control: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
