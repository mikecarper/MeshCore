#!/usr/bin/env python3
"""Verify an external LoRa FEM RX-gain path with paired noise floors.

The test uses the framed Companion USB protocol. Each OFF and ON reading is
taken after a real state transition so firmware starts a fresh 64-sample noise
floor calibration. The original FEM state is restored even when the test
fails. It is intended for T096 and V4/V4.3 KCT8103L RX paths. Mesh Tower V2
uses its KCT8103L as a transmit PA only and therefore is not an RX-toggle
target.

Examples::

    python tools/hil/fem_noise_floor_test.py \
        --port /dev/serial/by-id/usb-Heltec_HT-n5262G_...-if00 \
        --expect-manufacturer "Heltec T096" --dtr on

    python tools/hil/fem_noise_floor_test.py \
        --port /dev/serial/by-id/usb-Espressif_USB_JTAG_...-if00 \
        --expect-manufacturer "Heltec V4" --dtr off

Exactly one JSON result is written to stdout. A successful test requires the
reported ON noise floor to rise by at least ``--min-gain-db`` relative to OFF
in a majority of paired cycles, as expected when the external LNA is active.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import re
import statistics
import struct
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import esp32_companion_serial_stress as companion


CMD_GET_FEM_RX_GAIN = 0x42
CMD_SET_FEM_RX_GAIN = 0x43
STATS_TYPE_RADIO = 1
RESP_CODE_OK = 0


class FemTestError(RuntimeError):
    """An actionable FEM hardware-test failure."""


@dataclass(frozen=True)
class FemTestConfig:
    port: str
    cycles: int = 3
    settle_seconds: float = 2.5
    min_gain_db: float = 3.0
    expected_manufacturer: str = ""
    dtr: str = "auto"
    baudrate: int = 115200
    open_delay: float = 2.5
    response_timeout: float = 6.0
    read_poll_timeout: float = 0.05
    write_timeout: float = 2.0


def validate_fem_get_response(payload: bytes) -> Dict[str, Any]:
    if len(payload) != 2 or payload[0] != RESP_CODE_OK:
        raise companion.ProtocolError(
            f"FEM get response must be OK plus one byte, got {payload.hex()}"
        )
    if payload[1] not in (0, 1):
        raise companion.ProtocolError(
            f"FEM get returned invalid state {payload[1]}"
        )
    return {"enabled": bool(payload[1])}


def validate_fem_set_response(payload: bytes) -> Dict[str, Any]:
    if payload != bytes((RESP_CODE_OK,)):
        raise companion.ProtocolError(
            f"FEM set response must be OK, got {payload.hex()}"
        )
    return {"accepted": True}


def validate_radio_stats_response(payload: bytes) -> Dict[str, Any]:
    if len(payload) != 14:
        raise companion.ProtocolError(
            f"radio STATS length is {len(payload)}, expected 14"
        )
    if payload[0] != companion.RESP_CODE_STATS or payload[1] != STATS_TYPE_RADIO:
        raise companion.ProtocolError("response is not radio STATS")
    noise_floor, last_rssi, last_snr_quarters, tx_air, rx_air = (
        struct.unpack_from("<hbbII", payload, 2)
    )
    if not -160 <= noise_floor <= -1:
        raise companion.ProtocolError(
            f"noise floor {noise_floor} dBm is not a calibrated reading"
        )
    return {
        "noise_floor_dbm": noise_floor,
        "last_rssi_dbm": last_rssi,
        "last_snr_db": last_snr_quarters / 4.0,
        "tx_air_seconds": tx_air,
        "rx_air_seconds": rx_air,
    }


def _fem_get_spec() -> companion.RequestSpec:
    return companion.RequestSpec(
        "get_fem_rx_gain",
        bytes((CMD_GET_FEM_RX_GAIN,)),
        RESP_CODE_OK,
        validate_fem_get_response,
    )


def _fem_set_spec(enabled: bool) -> companion.RequestSpec:
    return companion.RequestSpec(
        "set_fem_rx_gain",
        bytes((CMD_SET_FEM_RX_GAIN, int(enabled))),
        RESP_CODE_OK,
        validate_fem_set_response,
    )


def _radio_stats_spec() -> companion.RequestSpec:
    return companion.RequestSpec(
        "radio_stats",
        bytes((companion.CMD_GET_STATS, STATS_TYPE_RADIO)),
        companion.RESP_CODE_STATS,
        validate_radio_stats_response,
    )


def _resolve_dtr(config: FemTestConfig) -> bool:
    if config.dtr == "on":
        return True
    if config.dtr == "off":
        return False
    # Adafruit nRF52 USB CDC requires DTR before it will return data. ESP32-S3
    # USB-Serial-JTAG has no DTR concept and should not receive reset-line
    # signaling from the host.
    return "HT-n5262" in config.port


class CompanionFemTransport:
    def __init__(self, config: FemTestConfig) -> None:
        self.config = config
        self.counters = companion.TransportCounters()
        self.reader = companion.DeviceFrameReader(self.counters)
        self.port: Any = None

    def open(self) -> None:
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise companion.DependencyError(
                "pyserial is required; install it with "
                "'python -m pip install pyserial'"
            ) from exc

        port = serial.Serial()
        port.port = self.config.port
        port.baudrate = self.config.baudrate
        port.timeout = min(
            self.config.read_poll_timeout, self.config.response_timeout
        )
        port.write_timeout = self.config.write_timeout
        port.dtr = _resolve_dtr(self.config)
        port.rts = False
        port.open()
        self.counters.ports_opened += 1
        self.port = port
        try:
            if self.config.open_delay:
                time.sleep(self.config.open_delay)
            port.reset_input_buffer()
            port.reset_output_buffer()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.port is None:
            return
        port, self.port = self.port, None
        companion._close_port_synchronously(port, self.counters)

    def _transact(self, spec: companion.RequestSpec) -> Dict[str, Any]:
        if self.port is None:
            raise FemTestError("serial transport is not open")
        return companion.transact(
            self.port,
            self.reader,
            spec,
            self.config.response_timeout,
            self.counters,
        )

    def identify(self) -> Dict[str, Any]:
        self._transact(companion.make_app_start_spec("MeshCore-FEM-HIL"))
        return self._transact(companion.SAFE_QUERY_SPECS["device_info"])

    def get_enabled(self) -> bool:
        return bool(self._transact(_fem_get_spec())["enabled"])

    def set_enabled(self, enabled: bool) -> None:
        self._transact(_fem_set_spec(enabled))

    def radio_stats(self) -> Dict[str, Any]:
        return self._transact(_radio_stats_spec())

    def transport_summary(self) -> Dict[str, Any]:
        return asdict(self.counters)


def _validate_config(config: FemTestConfig) -> None:
    if not config.port:
        raise ValueError("port must not be empty")
    if config.cycles < 1:
        raise ValueError("cycles must be at least 1")
    if config.settle_seconds < 0:
        raise ValueError("settle_seconds must not be negative")
    if config.min_gain_db < 0:
        raise ValueError("min_gain_db must not be negative")
    if config.dtr not in ("auto", "on", "off"):
        raise ValueError("dtr must be auto, on, or off")
    for name, value in (
        ("baudrate", config.baudrate),
        ("response_timeout", config.response_timeout),
        ("read_poll_timeout", config.read_poll_timeout),
        ("write_timeout", config.write_timeout),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")
    if config.open_delay < 0:
        raise ValueError("open_delay must not be negative")
    if config.expected_manufacturer:
        re.compile(config.expected_manufacturer)


def run_fem_test(
    config: FemTestConfig,
    transport: Optional[Any] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Run paired OFF/ON measurements and return a JSON-safe summary."""

    summary: Dict[str, Any] = {
        "schema_version": 1,
        "tool": "fem_noise_floor_test",
        "ok": False,
        "parameters": asdict(config),
        "device": None,
        "original_fem_rx_gain": None,
        "cycles": [],
        "aggregate": None,
        "restore": {"attempted": False, "ok": False, "state": None},
        "transport": {},
        "failure": None,
        "elapsed_seconds": 0.0,
    }
    started = time.monotonic()
    active_phase = "setup"
    test_transport = transport or CompanionFemTransport(config)
    opened = False
    original: Optional[bool] = None
    pending_error: Optional[BaseException] = None
    failure_phase = active_phase

    try:
        _validate_config(config)
        test_transport.open()
        opened = True
        active_phase = "identify"
        device = test_transport.identify()
        summary["device"] = device
        manufacturer = str(device.get("manufacturer", ""))
        if config.expected_manufacturer and not re.search(
            config.expected_manufacturer, manufacturer, re.IGNORECASE
        ):
            raise FemTestError(
                f"manufacturer {manufacturer!r} does not match "
                f"{config.expected_manufacturer!r}"
            )

        active_phase = "read-original-state"
        original = test_transport.get_enabled()
        summary["original_fem_rx_gain"] = "on" if original else "off"

        # The first recorded OFF sample must also follow an actual transition.
        if not original:
            active_phase = "prime-on-transition"
            test_transport.set_enabled(True)
            if not test_transport.get_enabled():
                raise FemTestError("FEM failed to verify ON while priming")

        for cycle_index in range(config.cycles):
            active_phase = f"cycle-{cycle_index + 1}-off"
            test_transport.set_enabled(False)
            if test_transport.get_enabled():
                raise FemTestError("FEM readback remained ON after OFF request")
            sleep(config.settle_seconds)
            off_stats = test_transport.radio_stats()

            active_phase = f"cycle-{cycle_index + 1}-on"
            test_transport.set_enabled(True)
            if not test_transport.get_enabled():
                raise FemTestError("FEM readback remained OFF after ON request")
            sleep(config.settle_seconds)
            on_stats = test_transport.radio_stats()

            delta = (
                float(on_stats["noise_floor_dbm"])
                - float(off_stats["noise_floor_dbm"])
            )
            summary["cycles"].append(
                {
                    "cycle": cycle_index + 1,
                    "off": off_stats,
                    "on": on_stats,
                    "on_minus_off_db": delta,
                }
            )

        deltas = [row["on_minus_off_db"] for row in summary["cycles"]]
        median_delta = float(statistics.median(deltas))
        positive_pairs = sum(delta >= config.min_gain_db for delta in deltas)
        required_pairs = math.floor(config.cycles / 2) + 1
        summary["aggregate"] = {
            "median_on_minus_off_db": median_delta,
            "min_on_minus_off_db": min(deltas),
            "max_on_minus_off_db": max(deltas),
            "pairs_at_or_above_minimum": positive_pairs,
            "pairs_required": required_pairs,
        }
        if median_delta < config.min_gain_db or positive_pairs < required_pairs:
            raise FemTestError(
                f"FEM gain delta did not meet {config.min_gain_db:g} dB: "
                f"median={median_delta:g} dB, "
                f"passing pairs={positive_pairs}/{config.cycles}"
            )
    except KeyboardInterrupt:
        pending_error = KeyboardInterrupt("interrupted by user")
        failure_phase = active_phase
    except BaseException as exc:
        pending_error = exc
        failure_phase = active_phase
    finally:
        if opened and original is not None:
            active_phase = "restore-original-state"
            summary["restore"]["attempted"] = True
            try:
                current = test_transport.get_enabled()
                if current != original:
                    test_transport.set_enabled(original)
                restored = test_transport.get_enabled()
                summary["restore"].update(
                    {"ok": restored == original,
                     "state": "on" if restored else "off"}
                )
                if restored != original:
                    raise FemTestError("original FEM state did not restore")
            except BaseException as exc:
                if pending_error is None:
                    pending_error = exc
                    failure_phase = active_phase
                else:
                    pending_error = FemTestError(
                        f"{pending_error}; restore also failed: {exc}"
                    )
        active_phase = "close"
        try:
            if opened:
                test_transport.close()
        except BaseException as exc:
            if pending_error is None:
                pending_error = exc
                failure_phase = active_phase
            else:
                pending_error = FemTestError(
                    f"{pending_error}; close also failed: {exc}"
                )
        try:
            summary["transport"] = test_transport.transport_summary()
        except (AttributeError, TypeError):
            summary["transport"] = {}
        summary["elapsed_seconds"] = round(
            max(0.0, time.monotonic() - started), 6
        )

    if pending_error is None:
        summary["ok"] = True
    else:
        summary["failure"] = {
            "phase": failure_phase,
            "type": type(pending_error).__name__,
            "message": str(pending_error),
        }
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


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Toggle a Companion board's LoRa FEM and compare noise floors."
    )
    parser.add_argument("--port", required=True)
    parser.add_argument("--cycles", type=_positive_int, default=3)
    parser.add_argument(
        "--settle-seconds", type=_nonnegative_float, default=2.5,
        help="wait after each transition for the 64-sample calibration",
    )
    parser.add_argument("--min-gain-db", type=_nonnegative_float, default=3.0)
    parser.add_argument(
        "--expect-manufacturer", default="", metavar="REGEX",
        help="case-insensitive guard against selecting the wrong serial port",
    )
    parser.add_argument("--dtr", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--baudrate", type=_positive_int, default=115200)
    parser.add_argument("--open-delay", type=_nonnegative_float, default=2.5)
    parser.add_argument("--response-timeout", type=_positive_float, default=6.0)
    parser.add_argument("--read-poll-timeout", type=_positive_float, default=0.05)
    parser.add_argument("--write-timeout", type=_positive_float, default=2.0)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    config = FemTestConfig(
        port=args.port,
        cycles=args.cycles,
        settle_seconds=args.settle_seconds,
        min_gain_db=args.min_gain_db,
        expected_manufacturer=args.expect_manufacturer,
        dtr=args.dtr,
        baudrate=args.baudrate,
        open_delay=args.open_delay,
        response_timeout=args.response_timeout,
        read_poll_timeout=args.read_poll_timeout,
        write_timeout=args.write_timeout,
    )
    summary = run_fem_test(config)
    print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
