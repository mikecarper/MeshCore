#!/usr/bin/env python3
"""Offline tests for esp32_companion_serial_stress.py."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import struct
import unittest
from unittest import mock

import esp32_companion_serial_stress as hil


def device_frame(payload: bytes) -> bytes:
    return b">" + struct.pack("<H", len(payload)) + payload


def fixed_text(value: str, size: int) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) >= size:
        raise ValueError("fixture string is too long")
    return encoded + bytes(size - len(encoded))


def self_info_payload() -> bytes:
    payload = bytearray(58)
    payload[0:4] = bytes((hil.RESP_CODE_SELF_INFO, hil.ADV_TYPE_CHAT, 20, 22))
    payload[4:36] = bytes(range(1, 33))
    struct.pack_into("<ii", payload, 36, 47_606_200, -122_332_100)
    payload[44:48] = bytes((1, 0, 0, 1))
    struct.pack_into("<II", payload, 48, 910_525, 62_500)
    payload[56:58] = bytes((7, 5))
    return bytes(payload) + b"HIL Node"


def device_info_payload() -> bytes:
    return b"".join(
        (
            bytes((hil.RESP_CODE_DEVICE_INFO, 14, 50, 40)),
            struct.pack("<I", 123456),
            fixed_text("01-Sep-2026", 12),
            fixed_text("Heltec", 40),
            fixed_text("v1.17.1-test", 20),
            bytes((0, 2)),
        )
    )


def response_for(request: bytes) -> bytes:
    command = request[0]
    if command == hil.CMD_APP_START:
        return self_info_payload()
    if command == hil.CMD_DEVICE_QUERY:
        return device_info_payload()
    if command == hil.CMD_GET_DEVICE_TIME:
        return struct.pack("<BI", hil.RESP_CODE_CURR_TIME, 1_788_200_000)
    if command == hil.CMD_GET_BATT_AND_STORAGE:
        return struct.pack(
            "<BHII", hil.RESP_CODE_BATT_AND_STORAGE, 4012, 64, 1024
        )
    if command == hil.CMD_GET_STATS:
        return struct.pack(
            "<BBHIHB", hil.RESP_CODE_STATS, hil.STATS_TYPE_CORE,
            4000, 1234, 0, 2,
        )
    raise AssertionError(f"unsafe or unexpected command {command}")


class FakeSerial:
    def __init__(self, responder=response_for, prefix: bytes = b"") -> None:
        self.responder = responder
        self.prefix = prefix
        self.rx = bytearray()
        self.writes = []
        self.is_open = True
        self.flush_count = 0
        self.input_resets = 0
        self.output_resets = 0
        self.close_count = 0

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def write(self, frame: bytes) -> int:
        self.writes.append(bytes(frame))
        if len(frame) < 4 or frame[0] != hil.HOST_FRAME_MARKER:
            raise AssertionError("invalid host frame")
        length = frame[1] | (frame[2] << 8)
        if length != len(frame) - 3:
            raise AssertionError("invalid host length")
        self.rx.extend(self.prefix)
        self.rx.extend(device_frame(self.responder(frame[3:])))
        return len(frame)

    def read(self, size: int) -> bytes:
        size = min(size, len(self.rx))
        data = bytes(self.rx[:size])
        del self.rx[:size]
        return data

    def flush(self) -> None:
        self.flush_count += 1

    def reset_input_buffer(self) -> None:
        self.input_resets += 1
        self.rx.clear()

    def reset_output_buffer(self) -> None:
        self.output_resets += 1

    def close(self) -> None:
        self.close_count += 1
        self.is_open = False


class FakeFactory:
    def __init__(self, responder=response_for, prefix: bytes = b"") -> None:
        self.responder = responder
        self.prefix = prefix
        self.instances = []

    def __call__(self, _config: hil.StressConfig) -> FakeSerial:
        port = FakeSerial(self.responder, self.prefix)
        self.instances.append(port)
        return port


def fast_config(**overrides) -> hil.StressConfig:
    values = {
        "port": "FAKE",
        "count": 2,
        "open_delay": 0,
        "request_delay": 0,
        "cycle_delay": 0,
        "close_delay": 0,
        "response_timeout": 0.05,
        "read_poll_timeout": 0.001,
        "write_timeout": 0.05,
    }
    values.update(overrides)
    return hil.StressConfig(**values)


class FrameProtocolTest(unittest.TestCase):
    def test_host_frame_uses_marker_and_little_endian_length(self) -> None:
        frame = hil.encode_host_frame(b"\x01\xAA\xBB")
        self.assertEqual(frame, b"<\x03\x00\x01\xAA\xBB")

    def test_reader_resynchronizes_after_ascii_prompt_candidate(self) -> None:
        counters = hil.TransportCounters()
        reader = hil.DeviceFrameReader(counters)
        port = FakeSerial()
        port.rx.extend(b"terminal banner\r\n> " + device_frame(
            struct.pack("<BI", hil.RESP_CODE_CURR_TIME, 123)
        ))

        payload = reader.read_frame(port, 0.05)

        self.assertEqual(payload[0], hil.RESP_CODE_CURR_TIME)
        self.assertGreater(counters.discarded_prefix_bytes, 0)
        self.assertEqual(counters.invalid_length_candidates, 1)

    def test_reader_rejects_stream_without_device_marker(self) -> None:
        counters = hil.TransportCounters()
        reader = hil.DeviceFrameReader(counters)
        port = FakeSerial()
        port.rx.extend(b"plain text only")
        with self.assertRaises(hil.FrameTimeout):
            reader.read_frame(port, 0.005)


class PayloadValidationTest(unittest.TestCase):
    def test_all_safe_response_payloads_validate(self) -> None:
        hil.validate_app_start_response(self_info_payload())
        hil.validate_device_info_response(device_info_payload())
        hil.validate_device_time_response(
            struct.pack("<BI", hil.RESP_CODE_CURR_TIME, 123)
        )
        hil.validate_battery_storage_response(
            struct.pack("<BHII", hil.RESP_CODE_BATT_AND_STORAGE, 4000, 1, 2)
        )
        hil.validate_core_stats_response(
            struct.pack("<BBHIHB", hil.RESP_CODE_STATS, 0, 4000, 9, 0, 0)
        )

    def test_wrong_length_and_payload_are_rejected(self) -> None:
        with self.assertRaises(hil.ProtocolError):
            hil.validate_device_time_response(b"\x09\0\0\0")
        invalid = bytearray(self_info_payload())
        invalid[56] = 99
        with self.assertRaises(hil.ProtocolError):
            hil.validate_app_start_response(bytes(invalid))

    def test_wrong_response_type_fails_transaction(self) -> None:
        def wrong_response(_request: bytes) -> bytes:
            return struct.pack("<BI", hil.RESP_CODE_CURR_TIME, 123)

        port = FakeSerial(wrong_response)
        counters = hil.TransportCounters()
        reader = hil.DeviceFrameReader(counters)
        with self.assertRaises(hil.ProtocolError):
            hil.transact(
                port,
                reader,
                hil.SAFE_QUERY_SPECS["battery_storage"],
                0.05,
                counters,
            )


class StressModesTest(unittest.TestCase):
    def test_persistent_mode_uses_one_synchronously_closed_port(self) -> None:
        factory = FakeFactory(prefix=b"startup> ")
        config = fast_config(mode="persistent", count=3)

        summary = hil.run_stress(config, serial_factory=factory, sleep=lambda _: None)

        self.assertTrue(summary["ok"], summary["failure"])
        self.assertEqual(len(factory.instances), 1)
        port = factory.instances[0]
        self.assertFalse(port.is_open)
        self.assertEqual(port.close_count, 1)
        self.assertGreaterEqual(port.flush_count, 2)
        phase = summary["phases"]["persistent"]
        self.assertEqual(phase["cycles_completed"], 3)
        self.assertEqual(phase["requests_completed"], 1 + 3 * 4)
        self.assertEqual(port.writes[0][3], hil.CMD_APP_START)
        commands = {frame[3] for frame in port.writes[1:]}
        self.assertEqual(
            commands,
            {
                hil.CMD_DEVICE_QUERY,
                hil.CMD_GET_DEVICE_TIME,
                hil.CMD_GET_BATT_AND_STORAGE,
                hil.CMD_GET_STATS,
            },
        )

    def test_reopen_mode_uses_fresh_handle_for_every_app_start(self) -> None:
        factory = FakeFactory()
        config = fast_config(mode="reopen", count=4)

        summary = hil.run_stress(config, serial_factory=factory, sleep=lambda _: None)

        self.assertTrue(summary["ok"], summary["failure"])
        self.assertEqual(len(factory.instances), 4)
        self.assertTrue(all(not port.is_open for port in factory.instances))
        self.assertTrue(all(port.close_count == 1 for port in factory.instances))
        self.assertTrue(all(len(port.writes) == 1 for port in factory.instances))
        self.assertTrue(
            all(port.writes[0][3] == hil.CMD_APP_START
                for port in factory.instances)
        )
        self.assertEqual(summary["transport"]["ports_opened"], 4)
        self.assertEqual(summary["transport"]["ports_closed"], 4)

    def test_failure_summary_is_machine_readable_and_closes_port(self) -> None:
        def wrong_response(request: bytes) -> bytes:
            if request[0] == hil.CMD_APP_START:
                return self_info_payload()
            return bytes((hil.RESP_CODE_ERR, 6))

        factory = FakeFactory(wrong_response)
        config = fast_config(
            mode="persistent", count=1, queries=("device_time",)
        )

        summary = hil.run_stress(config, serial_factory=factory, sleep=lambda _: None)

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["failure"]["phase"], "persistent")
        self.assertEqual(summary["failure"]["type"], "ProtocolError")
        self.assertIn("ERR code 6", summary["failure"]["message"])
        self.assertFalse(factory.instances[0].is_open)
        self.assertEqual(summary["transport"]["ports_closed"], 1)

    def test_preparation_failure_still_closes_open_handle(self) -> None:
        class ResetFailureSerial(FakeSerial):
            def reset_input_buffer(self) -> None:
                raise OSError("reset failed")

        class ResetFailureFactory:
            def __init__(self) -> None:
                self.instance = ResetFailureSerial()

            def __call__(self, _config: hil.StressConfig) -> FakeSerial:
                return self.instance

        factory = ResetFailureFactory()
        summary = hil.run_stress(
            fast_config(mode="persistent", count=1),
            serial_factory=factory,
            sleep=lambda _: None,
        )

        self.assertFalse(summary["ok"])
        self.assertFalse(factory.instance.is_open)
        self.assertEqual(summary["transport"]["ports_opened"], 1)
        self.assertEqual(summary["transport"]["ports_closed"], 1)

    def test_main_prints_json_and_returns_failure_exit_code(self) -> None:
        failure = {
            "schema_version": 1,
            "tool": "esp32_companion_serial_stress",
            "ok": False,
            "failure": {"type": "ProtocolError", "message": "fixture"},
        }
        output = io.StringIO()
        with mock.patch.object(hil, "run_stress", return_value=failure):
            with redirect_stdout(output):
                exit_code = hil.main(["--port", "FAKE"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue()), failure)


if __name__ == "__main__":
    unittest.main()
