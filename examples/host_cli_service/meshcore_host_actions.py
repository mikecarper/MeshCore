#!/usr/bin/python3
"""Root broker for exact MeshCore host recovery actions.

This service is activated by ``meshcore-host-actions.socket``.  The unprivileged
MQTT endpoint may reserve an operation, publish its LoRa reply, and only then
commit the operation.  Linux ``SO_PEERCRED`` and a root-owned systemd policy
gate the two fixed actions.  No request text becomes an executable, unit, or
argument.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import grp
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import time
from typing import Callable, Mapping


HOST_ACTIONS_SOCKET = Path("/run/meshcore-host-actions.sock")
STATE_DIRECTORY = Path("/run/meshcore-host-actions")
STATE_PATH = STATE_DIRECTORY / "state.json"
SOCKET_MODE = 0o660
STATE_DIRECTORY_MODE = 0o700
STATE_FILE_MODE = 0o600
SYSTEMD_LISTEN_FDS_START = 3
MAX_REQUEST_BYTES = 96
MAX_RESPONSE_BYTES = 128
MAX_STATE_BYTES = 32768
MAX_OPERATIONS = 64
MAX_SEQUENCE = (1 << 63) - 1
CLIENT_IO_TIMEOUT_SECONDS = 1.0
SCHEDULE_TIMEOUT_SECONDS = 1.5
VALID_ACTIONS = frozenset(("network-restart", "reboot"))
VALID_STATES = frozenset(("prepared", "in-progress", "scheduled", "ambiguous"))
ACTION_UNITS = {
    "network-restart": "meshcore-networkmanager-restart.service",
    "reboot": "meshcore-host-reboot.timer",
}
MINIMAL_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
}
OPERATION_ID_RE = re.compile(r"[0-9A-F]{32}")


class StateError(RuntimeError):
    """Persistent state is unavailable or invalid."""


class StateFullError(StateError):
    """No operation can be safely forgotten during this boot."""


class RebootPendingError(RuntimeError):
    """A distinct reboot is already committed or has an unknown outcome."""


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise StateError("host action state contains a duplicate key")
        document[key] = value
    return document


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    action: str
    state: str
    sequence: int


def parse_allowed_actions(value: str) -> frozenset[str]:
    if value == "":
        return frozenset()
    values = value.split(",")
    if (
        any(not item or item not in VALID_ACTIONS for item in values)
        or len(values) != len(set(values))
    ):
        raise ValueError(
            "allowed actions must be a comma-separated subset of "
            "network-restart,reboot"
        )
    return frozenset(values)


def validate_operation_id(operation_id: str) -> None:
    if OPERATION_ID_RE.fullmatch(operation_id) is None:
        raise ValueError("operation ID must be 32 uppercase hexadecimal digits")


def _document_from_records(
    records: dict[str, OperationRecord],
    next_sequence: int,
) -> dict[str, object]:
    return {
        "version": 1,
        "next_sequence": next_sequence,
        "operations": [
            {
                "id": record.operation_id,
                "action": record.action,
                "state": record.state,
                "sequence": record.sequence,
            }
            for record in sorted(records.values(), key=lambda item: item.sequence)
        ],
    }


def parse_state_document(
    document: object,
) -> tuple[dict[str, OperationRecord], int]:
    if not isinstance(document, dict) or set(document) != {
        "version", "next_sequence", "operations"
    }:
        raise StateError("host action state has an invalid top-level schema")
    if (
        not isinstance(document["version"], int)
        or isinstance(document["version"], bool)
        or document["version"] != 1
    ):
        raise StateError("host action state has an unsupported version")
    next_sequence = document["next_sequence"]
    operations = document["operations"]
    if (
        not isinstance(next_sequence, int)
        or isinstance(next_sequence, bool)
        or next_sequence < 1
        or next_sequence > MAX_SEQUENCE
        or not isinstance(operations, list)
        or len(operations) > MAX_OPERATIONS
    ):
        raise StateError("host action state bounds are invalid")
    records: dict[str, OperationRecord] = {}
    sequences: set[int] = set()
    for raw_record in operations:
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "id", "action", "state", "sequence"
        }:
            raise StateError("host action record has an invalid schema")
        operation_id = raw_record["id"]
        action = raw_record["action"]
        state_name = raw_record["state"]
        sequence = raw_record["sequence"]
        if (
            not isinstance(operation_id, str)
            or OPERATION_ID_RE.fullmatch(operation_id) is None
            or not isinstance(action, str)
            or action not in VALID_ACTIONS
            or not isinstance(state_name, str)
            or state_name not in VALID_STATES
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
            or sequence > MAX_SEQUENCE
            or operation_id in records
            or sequence in sequences
        ):
            raise StateError("host action record is invalid")
        records[operation_id] = OperationRecord(
            operation_id, action, state_name, sequence
        )
        sequences.add(sequence)
    if sequences and next_sequence <= max(sequences):
        raise StateError("host action next sequence is invalid")
    return records, next_sequence


def validate_state_directory(path: Path = STATE_DIRECTORY) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StateError(f"host action state directory is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0:
        raise StateError("host action state directory must be root-owned directory")
    if stat.S_IMODE(metadata.st_mode) != STATE_DIRECTORY_MODE:
        raise StateError("host action state directory must have mode 0700")


def _validate_state_file_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
        raise StateError("host action state must be a root-owned regular file")
    if stat.S_IMODE(metadata.st_mode) != STATE_FILE_MODE:
        raise StateError("host action state must have mode 0600")
    if metadata.st_nlink != 1 or metadata.st_size > MAX_STATE_BYTES:
        raise StateError("host action state metadata is invalid")


class StateFile:
    def __init__(
        self,
        path: Path = STATE_PATH,
        directory: Path = STATE_DIRECTORY,
    ) -> None:
        self.path = path
        self.directory = directory

    def load(self) -> tuple[dict[str, OperationRecord], int]:
        validate_state_directory(self.directory)
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        except FileNotFoundError:
            return {}, 1
        except OSError as exc:
            raise StateError("host action state could not be opened") from exc
        try:
            metadata = os.fstat(descriptor)
            _validate_state_file_metadata(metadata)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                encoded = stream.read(MAX_STATE_BYTES + 1)
            if len(encoded) > MAX_STATE_BYTES:
                raise StateError("host action state is too large")
        finally:
            os.close(descriptor)
        try:
            document = json.loads(
                encoded.decode("ascii", errors="strict"),
                object_pairs_hook=_strict_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateError("host action state is not valid ASCII JSON") from exc
        return parse_state_document(document)

    def save(
        self,
        records: dict[str, OperationRecord],
        next_sequence: int,
    ) -> None:
        validate_state_directory(self.directory)
        document = _document_from_records(records, next_sequence)
        encoded = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        if len(encoded) > MAX_STATE_BYTES:
            raise StateError("host action state exceeds its size limit")
        temporary = self.directory / (
            ".state." + str(os.getpid()) + "." + secrets.token_hex(8)
        )
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | os.O_CLOEXEC,
                STATE_FILE_MODE,
            )
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.path)
            directory_descriptor = os.open(
                self.directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise StateError("host action state could not be saved") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except OSError:
                pass


Persist = Callable[[dict[str, OperationRecord], int], None]
Scheduler = Callable[[str], bool]


class OperationState:
    def __init__(
        self,
        records: dict[str, OperationRecord] | None = None,
        next_sequence: int = 1,
        persist: Persist | None = None,
    ) -> None:
        self.records = dict(records or {})
        self.next_sequence = next_sequence
        self.persist = persist or (lambda _records, _next: None)
        recovered = {
            operation_id: (
                replace(record, state="ambiguous")
                if record.state == "in-progress" else record
            )
            for operation_id, record in self.records.items()
        }
        if recovered != self.records:
            self.persist(recovered, self.next_sequence)
            self.records = recovered

    def _persist_candidate(
        self,
        records: dict[str, OperationRecord],
        next_sequence: int | None = None,
    ) -> None:
        candidate_next = self.next_sequence if next_sequence is None else next_sequence
        self.persist(records, candidate_next)
        self.records = records
        self.next_sequence = candidate_next

    def _existing(self, operation_id: str, action: str) -> OperationRecord | None:
        record = self.records.get(operation_id)
        if record is not None and record.action != action:
            raise ValueError("operation conflict")
        return record

    def prepare(
        self,
        action: str,
        operation_id: str,
        allowed_actions: frozenset[str],
    ) -> str:
        record = self._existing(operation_id, action)
        if action not in allowed_actions:
            raise PermissionError("action disabled")
        if record is not None:
            return record.state
        records = dict(self.records)
        if action == "reboot":
            other_reboots = [
                item
                for item in records.values()
                if item.action == "reboot"
            ]
            if any(item.state != "prepared" for item in other_reboots):
                raise RebootPendingError("another reboot is already pending")
            # An abandoned PREPARE has no side effect and is safe to supersede.
            # Removing all older reboot reservations atomically ensures only one
            # distinct operation can remain commit-capable at a time.
            for item in other_reboots:
                del records[item.operation_id]
        if len(records) >= MAX_OPERATIONS:
            prepared = [
                item for item in records.values() if item.state == "prepared"
            ]
            if not prepared:
                raise StateFullError("host action state is full")
            oldest = min(prepared, key=lambda item: item.sequence)
            del records[oldest.operation_id]
        if self.next_sequence >= MAX_SEQUENCE:
            raise StateFullError("host action sequence is exhausted")
        record = OperationRecord(
            operation_id, action, "prepared", self.next_sequence
        )
        records[operation_id] = record
        self._persist_candidate(records, self.next_sequence + 1)
        return record.state

    def commit(
        self,
        action: str,
        operation_id: str,
        allowed_actions: frozenset[str],
        scheduler: Scheduler,
    ) -> str:
        record = self._existing(operation_id, action)
        if record is None:
            raise LookupError("operation not prepared")
        if action not in allowed_actions:
            raise PermissionError("action disabled")
        if record.state != "prepared":
            return record.state

        in_progress = replace(record, state="in-progress")
        records = dict(self.records)
        records[operation_id] = in_progress
        self._persist_candidate(records)

        scheduled = False
        try:
            scheduled = scheduler(action)
        except Exception:
            scheduled = False
        final_state = "scheduled" if scheduled else "ambiguous"
        final = replace(in_progress, state=final_state)
        records = dict(self.records)
        records[operation_id] = final
        try:
            self._persist_candidate(records)
        except StateError:
            # The durable record remains in-progress. Keep the live state
            # ambiguous and never invoke the scheduler again.
            self.records[operation_id] = replace(in_progress, state="ambiguous")
            return "ambiguous"
        return final_state

    def status(self, operation_id: str) -> OperationRecord | None:
        return self.records.get(operation_id)


def schedule_action(action: str) -> bool:
    unit = ACTION_UNITS.get(action)
    if unit is None:
        return False
    try:
        completed = subprocess.run(
            ["/usr/bin/systemctl", "--no-block", "start", unit],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SCHEDULE_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            cwd="/",
            env=MINIMAL_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def parse_request(request: bytes) -> tuple[str, str | None, str]:
    if not request or len(request) > MAX_REQUEST_BYTES:
        raise ValueError("invalid request length")
    if not request.endswith(b"\n") or request.count(b"\n") != 1:
        raise ValueError("invalid request framing")
    try:
        line = request[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("request is not ASCII") from exc
    status_match = re.fullmatch(r"status ([0-9A-F]{32})", line)
    if status_match is not None:
        return "status", None, status_match.group(1)
    action_match = re.fullmatch(
        r"(prepare|commit) (network-restart|reboot) ([0-9A-F]{32})",
        line,
    )
    if action_match is None:
        raise ValueError("invalid request")
    return action_match.group(1), action_match.group(2), action_match.group(3)


def _state_response(record: OperationRecord) -> str:
    label = record.state.upper()
    return f"{label} {record.action} {record.operation_id}"


def process_request(
    request: bytes,
    state: OperationState,
    allowed_actions: frozenset[str],
    scheduler: Scheduler = schedule_action,
) -> str:
    verb, action, operation_id = parse_request(request)
    if verb == "status":
        record = state.status(operation_id)
        return (
            f"UNKNOWN {operation_id}"
            if record is None else _state_response(record)
        )
    assert action is not None
    try:
        if verb == "prepare":
            state_name = state.prepare(action, operation_id, allowed_actions)
        else:
            state_name = state.commit(
                action, operation_id, allowed_actions, scheduler
            )
    except PermissionError:
        return "ERR action disabled"
    except LookupError:
        return "ERR operation not prepared"
    except RebootPendingError:
        return "ERR reboot pending"
    except StateFullError:
        return "ERR state full"
    except StateError:
        return "ERR state unavailable"
    except ValueError:
        return "ERR operation conflict"
    return f"{state_name.upper()} {action} {operation_id}"


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
            raise socket.timeout("host action request deadline expired")
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
    allowed_actions: frozenset[str],
    state: OperationState,
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
            response = process_request(request, state, allowed_actions)
        except ValueError:
            response = "ERR invalid request"
        except Exception:
            response = "ERR server error"
    try:
        send_response(connection, response)
    except (OSError, socket.timeout):
        return False
    return not response.startswith("ERR ")


def validate_socket_metadata(metadata: os.stat_result, expected_gid: int) -> None:
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError("host actions path is not a socket")
    if metadata.st_uid != 0:
        raise ValueError("host actions socket is not root-owned")
    if metadata.st_gid != expected_gid:
        raise ValueError("host actions socket has the wrong group")
    if stat.S_IMODE(metadata.st_mode) != SOCKET_MODE:
        raise ValueError("host actions socket must have mode 0660")


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
        raise ValueError("host actions socket path is missing") from exc
    validate_socket_metadata(metadata, expected_gid)


def activation_socket(
    environment: Mapping[str, str] | None = None,
) -> socket.socket:
    variables = os.environ if environment is None else environment
    if variables.get("LISTEN_PID") != str(os.getpid()):
        raise ValueError("service must be started by systemd socket activation")
    if variables.get("LISTEN_FDS") != "1":
        raise ValueError("service requires exactly one activation socket")
    if variables.get("LISTEN_FDNAMES") not in (None, "host-actions"):
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
    allowed_actions: frozenset[str],
    state: OperationState,
) -> None:
    while True:
        connection, _address = listener.accept()
        with connection:
            handle_connection(
                connection,
                allowed_uid=allowed_uid,
                allowed_gid=allowed_gid,
                allowed_actions=allowed_actions,
                state=state,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Root-only MeshCore host recovery action broker"
    )
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--service-group", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ValueError("host action broker requires root")
        allowed_uid, allowed_gid = resolve_service_identity(
            arguments.service_user,
            arguments.service_group,
        )
        # This environment value comes only from the root-owned systemd unit
        # (or a root-started diagnostic process), never from the socket peer.
        allowed_actions = parse_allowed_actions(
            os.environ.get("MESHCORE_HOST_ACTIONS", "")
        )
        listener = activation_socket()
        validate_listening_socket(
            listener,
            path=HOST_ACTIONS_SOCKET,
            expected_gid=allowed_gid,
        )
        state_file = StateFile()
        records, next_sequence = state_file.load()
        state = OperationState(records, next_sequence, state_file.save)
        serve(
            listener,
            allowed_uid=allowed_uid,
            allowed_gid=allowed_gid,
            allowed_actions=allowed_actions,
            state=state,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"meshcore-host-actions: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
