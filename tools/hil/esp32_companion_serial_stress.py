#!/usr/bin/env python3
"""Stress an ESP32 MeshCore Companion serial session without radio traffic.

This utility talks directly to the Companion framing protocol with pyserial. It
has two complementary modes:

* ``persistent`` opens one handle, performs APP_START, then cycles through
  read-only local queries.
* ``reopen`` creates and synchronously closes a fresh handle for every
  APP_START transaction.

The selected requests only inspect local device state. They do not send mesh
packets, advertisements, messages, or other LoRa traffic.

Example (run only when the intended board and COM port have been confirmed)::

    python tools/hil/esp32_companion_serial_stress.py \
        --port COM7 --mode both --count 100

Exactly one JSON summary is written to stdout. Diagnostics requested with
``--verbose`` go to stderr, and any failed transaction produces a nonzero exit.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import struct
import sys
import time
from typing import Any, Callable, Dict, Optional, Sequence, Tuple


HOST_FRAME_MARKER = ord("<")
DEVICE_FRAME_MARKER = ord(">")
MAX_FRAME_SIZE = 176

CMD_APP_START = 1
CMD_GET_DEVICE_TIME = 5
CMD_GET_BATT_AND_STORAGE = 20
CMD_DEVICE_QUERY = 22
CMD_GET_STATS = 56

STATS_TYPE_CORE = 0

RESP_CODE_ERR = 1
RESP_CODE_SELF_INFO = 5
RESP_CODE_CURR_TIME = 9
RESP_CODE_BATT_AND_STORAGE = 12
RESP_CODE_DEVICE_INFO = 13
RESP_CODE_STATS = 24

ADV_TYPE_CHAT = 1
COMPANION_PROTOCOL_VERSION = 14


class HilError(RuntimeError):
    """Base class for an actionable HIL failure."""


class FrameTimeout(HilError):
    """Raised when no complete valid frame arrives before the deadline."""


class ProtocolError(HilError):
    """Raised when the peer returns a malformed or unexpected response."""


class DependencyError(HilError):
    """Raised when the runtime serial dependency is unavailable."""


@dataclass(frozen=True)
class StressConfig:
    port: str
    mode: str = "both"
    count: int = 100
    baudrate: int = 115200
    app_name: str = "MeshCore-HIL-Stress"
    queries: Tuple[str, ...] = (
        "device_info",
        "device_time",
        "battery_storage",
        "core_stats",
    )
    response_timeout: float = 2.0
    read_poll_timeout: float = 0.05
    write_timeout: float = 2.0
    open_delay: float = 0.25
    request_delay: float = 0.02
    cycle_delay: float = 0.05
    close_delay: float = 0.10
    verbose: bool = False


@dataclass
class TransportCounters:
    ports_opened: int = 0
    ports_closed: int = 0
    request_frames_sent: int = 0
    response_frames_received: int = 0
    request_bytes_sent: int = 0
    response_payload_bytes: int = 0
    discarded_prefix_bytes: int = 0
    invalid_length_candidates: int = 0
    ignored_push_frames: int = 0


@dataclass(frozen=True)
class RequestSpec:
    name: str
    payload: bytes
    response_type: int
    validator: Callable[[bytes], Dict[str, Any]]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def _decode_fixed_text(field: bytes, field_name: str) -> str:
    value, separator, padding = field.partition(b"\0")
    if separator:
        _require(
            not any(padding),
            f"{field_name} has nonzero bytes after its terminator",
        )
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"{field_name} is not valid UTF-8") from exc


def validate_app_start_response(payload: bytes) -> Dict[str, Any]:
    _require(payload and payload[0] == RESP_CODE_SELF_INFO,
             "APP_START response type is not SELF_INFO")
    _require(58 <= len(payload) <= 89,
             f"SELF_INFO length {len(payload)} is outside 58..89")
    _require(payload[1] == ADV_TYPE_CHAT,
             f"SELF_INFO advert type is {payload[1]}, expected chat")

    public_key = payload[4:36]
    _require(len(public_key) == 32, "SELF_INFO public key is truncated")
    _require(any(public_key) and any(value != 0xFF for value in public_key),
             "SELF_INFO public key is an erased value")

    latitude, longitude = struct.unpack_from("<ii", payload, 36)
    _require(-90_000_000 <= latitude <= 90_000_000,
             f"SELF_INFO latitude {latitude} is invalid")
    _require(-180_000_000 <= longitude <= 180_000_000,
             f"SELF_INFO longitude {longitude} is invalid")

    frequency_khz, bandwidth_hz = struct.unpack_from("<II", payload, 48)
    spreading_factor = payload[56]
    coding_rate = payload[57]
    _require(frequency_khz > 0, "SELF_INFO frequency is zero")
    _require(bandwidth_hz > 0, "SELF_INFO bandwidth is zero")
    _require(5 <= spreading_factor <= 12,
             f"SELF_INFO spreading factor {spreading_factor} is invalid")
    _require(5 <= coding_rate <= 8,
             f"SELF_INFO coding rate {coding_rate} is invalid")

    try:
        node_name = payload[58:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("SELF_INFO node name is not valid UTF-8") from exc

    return {
        "node_name": node_name,
        "public_key_prefix": public_key[:6].hex(),
        "latitude_e6": latitude,
        "longitude_e6": longitude,
        "frequency_khz": frequency_khz,
        "bandwidth_hz": bandwidth_hz,
        "spreading_factor": spreading_factor,
        "coding_rate": coding_rate,
    }


def validate_device_time_response(payload: bytes) -> Dict[str, Any]:
    _require(len(payload) == 5,
             f"CURR_TIME length is {len(payload)}, expected 5")
    _require(payload[0] == RESP_CODE_CURR_TIME,
             "device-time response type is not CURR_TIME")
    return {"unix_time": struct.unpack_from("<I", payload, 1)[0]}


def validate_battery_storage_response(payload: bytes) -> Dict[str, Any]:
    _require(len(payload) == 11,
             f"BATT_AND_STORAGE length is {len(payload)}, expected 11")
    _require(payload[0] == RESP_CODE_BATT_AND_STORAGE,
             "battery/storage response type is not BATT_AND_STORAGE")
    battery_mv, used_kib, total_kib = struct.unpack_from("<HII", payload, 1)
    _require(total_kib == 0 or used_kib <= total_kib,
             f"storage use {used_kib} KiB exceeds total {total_kib} KiB")
    return {
        "battery_mv": battery_mv,
        "storage_used_kib": used_kib,
        "storage_total_kib": total_kib,
    }


def validate_device_info_response(payload: bytes) -> Dict[str, Any]:
    _require(len(payload) == 82,
             f"DEVICE_INFO length is {len(payload)}, expected 82")
    _require(payload[0] == RESP_CODE_DEVICE_INFO,
             "device-query response type is not DEVICE_INFO")
    _require(payload[1] >= 1, "DEVICE_INFO protocol version is zero")
    _require(payload[2] > 0, "DEVICE_INFO contact capacity is zero")
    _require(payload[3] > 0, "DEVICE_INFO channel capacity is zero")
    _require(payload[80] in (0, 1),
             f"DEVICE_INFO repeat flag {payload[80]} is invalid")
    _require(payload[81] <= 2,
             f"DEVICE_INFO path-hash mode {payload[81]} is invalid")

    build_date = _decode_fixed_text(payload[8:20], "build date")
    manufacturer = _decode_fixed_text(payload[20:60], "manufacturer")
    firmware = _decode_fixed_text(payload[60:80], "firmware version")
    _require(bool(manufacturer), "DEVICE_INFO manufacturer is empty")
    _require(bool(firmware), "DEVICE_INFO firmware version is empty")

    return {
        "protocol_version": payload[1],
        "contact_capacity": payload[2] * 2,
        "channel_capacity": payload[3],
        "ble_pin": struct.unpack_from("<I", payload, 4)[0],
        "build_date": build_date,
        "manufacturer": manufacturer,
        "firmware": firmware,
        "repeat_enabled": bool(payload[80]),
        "path_hash_mode": payload[81],
    }


def validate_core_stats_response(payload: bytes) -> Dict[str, Any]:
    _require(len(payload) == 11,
             f"core STATS length is {len(payload)}, expected 11")
    _require(payload[0] == RESP_CODE_STATS,
             "core-stats response type is not STATS")
    _require(payload[1] == STATS_TYPE_CORE,
             f"STATS subtype is {payload[1]}, expected core")
    battery_mv, uptime_seconds, error_flags, queue_length = struct.unpack_from(
        "<HIHB", payload, 2
    )
    return {
        "battery_mv": battery_mv,
        "uptime_seconds": uptime_seconds,
        "error_flags": error_flags,
        "outbound_queue_length": queue_length,
    }


SAFE_QUERY_SPECS: Dict[str, RequestSpec] = {
    "device_info": RequestSpec(
        "device_info",
        bytes((CMD_DEVICE_QUERY, COMPANION_PROTOCOL_VERSION)),
        RESP_CODE_DEVICE_INFO,
        validate_device_info_response,
    ),
    "device_time": RequestSpec(
        "device_time",
        bytes((CMD_GET_DEVICE_TIME,)),
        RESP_CODE_CURR_TIME,
        validate_device_time_response,
    ),
    "battery_storage": RequestSpec(
        "battery_storage",
        bytes((CMD_GET_BATT_AND_STORAGE,)),
        RESP_CODE_BATT_AND_STORAGE,
        validate_battery_storage_response,
    ),
    "core_stats": RequestSpec(
        "core_stats",
        bytes((CMD_GET_STATS, STATS_TYPE_CORE)),
        RESP_CODE_STATS,
        validate_core_stats_response,
    ),
}


def make_app_start_spec(app_name: str) -> RequestSpec:
    try:
        encoded_name = app_name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("app name cannot be encoded as UTF-8") from exc
    payload = bytes((CMD_APP_START,)) + (b"\0" * 7) + encoded_name
    if len(payload) > MAX_FRAME_SIZE:
        raise ValueError(
            f"APP_START payload is {len(payload)} bytes; maximum is "
            f"{MAX_FRAME_SIZE}"
        )
    return RequestSpec(
        "app_start",
        payload,
        RESP_CODE_SELF_INFO,
        validate_app_start_response,
    )


def encode_host_frame(payload: bytes) -> bytes:
    if not 1 <= len(payload) <= MAX_FRAME_SIZE:
        raise ValueError(
            f"Companion payload length must be 1..{MAX_FRAME_SIZE}, got "
            f"{len(payload)}"
        )
    return bytes((HOST_FRAME_MARKER,)) + struct.pack("<H", len(payload)) + payload


class DeviceFrameReader:
    """Incrementally find validated device frames among optional ASCII noise."""

    def __init__(self, counters: TransportCounters) -> None:
        self._buffer = bytearray()
        self._counters = counters

    def _extract_frame(self) -> Optional[bytes]:
        while True:
            marker_index = self._buffer.find(bytes((DEVICE_FRAME_MARKER,)))
            if marker_index < 0:
                self._counters.discarded_prefix_bytes += len(self._buffer)
                self._buffer.clear()
                return None
            if marker_index:
                self._counters.discarded_prefix_bytes += marker_index
                del self._buffer[:marker_index]
            if len(self._buffer) < 3:
                return None

            payload_length = self._buffer[1] | (self._buffer[2] << 8)
            if not 1 <= payload_length <= MAX_FRAME_SIZE:
                # Drop only this marker. A later byte consumed as part of the
                # invalid candidate may itself be the start of a valid frame.
                self._counters.invalid_length_candidates += 1
                del self._buffer[0]
                continue

            frame_length = 3 + payload_length
            if len(self._buffer) < frame_length:
                return None
            payload = bytes(self._buffer[3:frame_length])
            del self._buffer[:frame_length]
            self._counters.response_frames_received += 1
            self._counters.response_payload_bytes += len(payload)
            return payload

    def read_frame(
        self,
        port: Any,
        timeout: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> bytes:
        deadline = clock() + timeout
        while True:
            payload = self._extract_frame()
            if payload is not None:
                return payload

            remaining = deadline - clock()
            if remaining <= 0:
                raise FrameTimeout(
                    "timed out waiting for a valid '>' Companion frame "
                    f"({len(self._buffer)} buffered byte(s))"
                )

            waiting = max(0, int(getattr(port, "in_waiting", 0)))
            data = port.read(min(max(waiting, 1), 512))
            if data:
                self._buffer.extend(data)
            else:
                sleep(min(0.001, remaining))


def _send_request(port: Any, spec: RequestSpec,
                  counters: TransportCounters) -> None:
    frame = encode_host_frame(spec.payload)
    written = port.write(frame)
    if written != len(frame):
        raise HilError(
            f"{spec.name}: serial write accepted {written!r} of "
            f"{len(frame)} bytes"
        )
    port.flush()
    counters.request_frames_sent += 1
    counters.request_bytes_sent += len(frame)


def transact(
    port: Any,
    reader: DeviceFrameReader,
    spec: RequestSpec,
    timeout: float,
    counters: TransportCounters,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    _send_request(port, spec, counters)
    deadline = clock() + timeout
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise FrameTimeout(f"{spec.name}: expected response did not arrive")
        payload = reader.read_frame(port, remaining, clock=clock, sleep=sleep)
        response_type = payload[0]
        if response_type >= 0x80:
            # Push notifications are valid unsolicited frames. They are not a
            # response to this transaction and may be skipped safely.
            counters.ignored_push_frames += 1
            continue
        if response_type == RESP_CODE_ERR:
            error_code = payload[1] if len(payload) >= 2 else None
            raise ProtocolError(
                f"{spec.name}: device returned ERR code {error_code!r}"
            )
        if response_type != spec.response_type:
            raise ProtocolError(
                f"{spec.name}: response type 0x{response_type:02X}, "
                f"expected 0x{spec.response_type:02X}"
            )
        return spec.validator(payload)


def _validate_config(config: StressConfig) -> None:
    if not config.port:
        raise ValueError("port must not be empty")
    if config.mode not in ("persistent", "reopen", "both"):
        raise ValueError(f"unsupported mode {config.mode!r}")
    if config.count < 1:
        raise ValueError("count must be at least 1")
    if config.baudrate < 1:
        raise ValueError("baudrate must be positive")
    for name in config.queries:
        if name not in SAFE_QUERY_SPECS:
            raise ValueError(f"unknown or unsafe query {name!r}")
    if not config.queries:
        raise ValueError("at least one safe query must be selected")
    for name, value in (
        ("response_timeout", config.response_timeout),
        ("read_poll_timeout", config.read_poll_timeout),
        ("write_timeout", config.write_timeout),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")
    for name, value in (
        ("open_delay", config.open_delay),
        ("request_delay", config.request_delay),
        ("cycle_delay", config.cycle_delay),
        ("close_delay", config.close_delay),
    ):
        if value < 0:
            raise ValueError(f"{name} must not be negative")


def _make_pyserial_factory() -> Callable[[StressConfig], Any]:
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise DependencyError(
            "pyserial is required; install it with 'python -m pip install "
            "pyserial'"
        ) from exc

    def factory(config: StressConfig) -> Any:
        # Configure DTR/RTS while closed so opening a port never deliberately
        # asserts a reset/boot strap control line.
        port = serial.Serial()
        port.port = config.port
        port.baudrate = config.baudrate
        port.timeout = min(config.read_poll_timeout, config.response_timeout)
        port.write_timeout = config.write_timeout
        port.dtr = False
        port.rts = False
        port.open()
        return port

    return factory


def _open_prepared_port(
    config: StressConfig,
    serial_factory: Callable[[StressConfig], Any],
    counters: TransportCounters,
    sleep: Callable[[float], None],
) -> Any:
    port = serial_factory(config)
    if not bool(getattr(port, "is_open", True)):
        raise HilError(f"serial factory returned a closed port for {config.port}")
    counters.ports_opened += 1
    try:
        if config.open_delay:
            sleep(config.open_delay)
        port.reset_input_buffer()
        port.reset_output_buffer()
    except BaseException as prepare_exc:
        try:
            _close_port_synchronously(port, counters)
        except BaseException as close_exc:
            raise HilError(
                f"serial preparation failed ({prepare_exc}); synchronous "
                f"close also failed ({close_exc})"
            ) from close_exc
        raise
    return port


def _close_port_synchronously(port: Any, counters: TransportCounters) -> None:
    flush_error: Optional[BaseException] = None
    try:
        port.flush()
    except BaseException as exc:  # close the OS handle even after flush failure
        flush_error = exc
    try:
        port.close()
    except BaseException as exc:
        if flush_error is not None:
            raise HilError(
                f"serial flush failed ({flush_error}); close also failed ({exc})"
            ) from exc
        raise
    if bool(getattr(port, "is_open", False)):
        raise HilError("serial close returned while the port remained open")
    counters.ports_closed += 1
    if flush_error is not None:
        raise HilError(f"serial flush before close failed: {flush_error}")


def _new_phase(cycles: int) -> Dict[str, Any]:
    return {
        "cycles_requested": cycles,
        "cycles_started": 0,
        "cycles_completed": 0,
        "requests_completed": 0,
        "responses_by_request": {},
        "last_validated_payload": {},
    }


def _record_response(phase: Dict[str, Any], name: str,
                     parsed: Dict[str, Any]) -> None:
    phase["requests_completed"] += 1
    by_request = phase["responses_by_request"]
    by_request[name] = by_request.get(name, 0) + 1
    phase["last_validated_payload"][name] = parsed


def _verbose(config: StressConfig, message: str) -> None:
    if config.verbose:
        print(message, file=sys.stderr, flush=True)


def _run_persistent(
    config: StressConfig,
    serial_factory: Callable[[StressConfig], Any],
    counters: TransportCounters,
    phase: Dict[str, Any],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    app_start = make_app_start_spec(config.app_name)
    query_specs = [SAFE_QUERY_SPECS[name] for name in config.queries]
    port = None
    pending_error: Optional[BaseException] = None
    reader = DeviceFrameReader(counters)
    try:
        port = _open_prepared_port(config, serial_factory, counters, sleep)
        parsed = transact(
            port, reader, app_start, config.response_timeout, counters,
            clock=clock, sleep=sleep,
        )
        _record_response(phase, app_start.name, parsed)
        _verbose(config, "persistent APP_START validated")

        for cycle in range(config.count):
            phase["cycles_started"] += 1
            # Rotate the same read-only set so adjacent command transitions
            # differ from one cycle to the next without randomizing a failure.
            offset = cycle % len(query_specs)
            ordered = query_specs[offset:] + query_specs[:offset]
            for request_index, spec in enumerate(ordered):
                parsed = transact(
                    port, reader, spec, config.response_timeout, counters,
                    clock=clock, sleep=sleep,
                )
                _record_response(phase, spec.name, parsed)
                if config.request_delay and request_index + 1 < len(ordered):
                    sleep(config.request_delay)
            phase["cycles_completed"] += 1
            _verbose(
                config,
                f"persistent cycle {cycle + 1}/{config.count} validated",
            )
            if config.cycle_delay and cycle + 1 < config.count:
                sleep(config.cycle_delay)
    except BaseException as exc:
        pending_error = exc
    finally:
        if port is not None:
            try:
                _close_port_synchronously(port, counters)
            except BaseException as close_exc:
                if pending_error is None:
                    pending_error = close_exc
                else:
                    pending_error = HilError(
                        f"{pending_error}; synchronous close also failed: "
                        f"{close_exc}"
                    )
    if pending_error is not None:
        raise pending_error


def _run_reopen(
    config: StressConfig,
    serial_factory: Callable[[StressConfig], Any],
    counters: TransportCounters,
    phase: Dict[str, Any],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    app_start = make_app_start_spec(config.app_name)
    for cycle in range(config.count):
        phase["cycles_started"] += 1
        port = None
        pending_error: Optional[BaseException] = None
        try:
            port = _open_prepared_port(config, serial_factory, counters, sleep)
            reader = DeviceFrameReader(counters)
            parsed = transact(
                port, reader, app_start, config.response_timeout, counters,
                clock=clock, sleep=sleep,
            )
            _record_response(phase, app_start.name, parsed)
        except BaseException as exc:
            pending_error = exc
        finally:
            if port is not None:
                try:
                    _close_port_synchronously(port, counters)
                except BaseException as close_exc:
                    if pending_error is None:
                        pending_error = close_exc
                    else:
                        pending_error = HilError(
                            f"{pending_error}; synchronous close also failed: "
                            f"{close_exc}"
                        )
        if pending_error is not None:
            raise pending_error
        phase["cycles_completed"] += 1
        _verbose(config, f"reopen cycle {cycle + 1}/{config.count} validated")
        if config.close_delay and cycle + 1 < config.count:
            sleep(config.close_delay)


def run_stress(
    config: StressConfig,
    serial_factory: Optional[Callable[[StressConfig], Any]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Run selected phases and always return a JSON-serializable summary."""

    counters = TransportCounters()
    phases: Dict[str, Dict[str, Any]] = {}
    if config.mode in ("persistent", "both"):
        phases["persistent"] = _new_phase(config.count)
    if config.mode in ("reopen", "both"):
        phases["reopen"] = _new_phase(config.count)

    summary: Dict[str, Any] = {
        "schema_version": 1,
        "tool": "esp32_companion_serial_stress",
        "ok": False,
        "parameters": {
            **asdict(config),
            "queries": list(config.queries),
        },
        "phases": phases,
        "transport": {},
        "failure": None,
        "elapsed_seconds": 0.0,
    }
    started = clock()
    active_phase = "setup"
    try:
        _validate_config(config)
        if serial_factory is None:
            serial_factory = _make_pyserial_factory()
        if "persistent" in phases:
            active_phase = "persistent"
            _run_persistent(
                config, serial_factory, counters, phases[active_phase],
                clock, sleep,
            )
        if "reopen" in phases:
            active_phase = "reopen"
            _run_reopen(
                config, serial_factory, counters, phases[active_phase],
                clock, sleep,
            )
        summary["ok"] = True
    except KeyboardInterrupt:
        summary["failure"] = {
            "phase": active_phase,
            "type": "KeyboardInterrupt",
            "message": "interrupted by user",
        }
    except BaseException as exc:
        summary["failure"] = {
            "phase": active_phase,
            "type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        summary["transport"] = asdict(counters)
        summary["elapsed_seconds"] = round(max(0.0, clock() - started), 6)
    return summary


def _positive_int(value: str) -> int:
    parsed = int(value, 10)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _safe_query_list(value: str) -> Tuple[str, ...]:
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    if not names:
        raise argparse.ArgumentTypeError("select at least one query")
    unknown = [name for name in names if name not in SAFE_QUERY_SPECS]
    if unknown:
        raise argparse.ArgumentTypeError(
            "unknown query(s): " + ", ".join(unknown)
        )
    if len(set(names)) != len(names):
        raise argparse.ArgumentTypeError("query names must not be repeated")
    return names


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stress an ESP32 MeshCore Companion COM port using APP_START and "
            "read-only, non-radio framed queries."
        )
    )
    parser.add_argument("--port", required=True, help="Windows COM port, e.g. COM7")
    parser.add_argument(
        "--mode", choices=("persistent", "reopen", "both"), default="both",
        help="session pattern to exercise (default: both)",
    )
    parser.add_argument(
        "--count", type=_positive_int, default=100,
        help="cycles per selected mode (default: 100)",
    )
    parser.add_argument("--baudrate", type=_positive_int, default=115200)
    parser.add_argument("--app-name", default="MeshCore-HIL-Stress")
    parser.add_argument(
        "--queries", type=_safe_query_list,
        default=tuple(SAFE_QUERY_SPECS),
        metavar="LIST",
        help=(
            "comma-separated safe persistent queries: "
            + ",".join(SAFE_QUERY_SPECS)
        ),
    )
    parser.add_argument(
        "--response-timeout", type=_positive_float, default=2.0,
        help="whole-response deadline in seconds",
    )
    parser.add_argument(
        "--read-poll-timeout", type=_positive_float, default=0.05,
        help="pyserial read poll timeout in seconds",
    )
    parser.add_argument(
        "--write-timeout", type=_positive_float, default=2.0,
        help="pyserial write timeout in seconds",
    )
    parser.add_argument(
        "--open-delay", type=_nonnegative_float, default=0.25,
        help="settle delay after each open",
    )
    parser.add_argument(
        "--request-delay", type=_nonnegative_float, default=0.02,
        help="delay between persistent requests",
    )
    parser.add_argument(
        "--cycle-delay", type=_nonnegative_float, default=0.05,
        help="delay between persistent cycles",
    )
    parser.add_argument(
        "--close-delay", type=_nonnegative_float, default=0.10,
        help="delay after each completed close/reopen cycle",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="write per-cycle progress to stderr",
    )
    parser.add_argument(
        "--pretty", action="store_true",
        help="indent the final JSON summary",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    config = StressConfig(
        port=args.port,
        mode=args.mode,
        count=args.count,
        baudrate=args.baudrate,
        app_name=args.app_name,
        queries=args.queries,
        response_timeout=args.response_timeout,
        read_poll_timeout=args.read_poll_timeout,
        write_timeout=args.write_timeout,
        open_delay=args.open_delay,
        request_delay=args.request_delay,
        cycle_delay=args.cycle_delay,
        close_delay=args.close_delay,
        verbose=args.verbose,
    )
    summary = run_stress(config)
    print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
