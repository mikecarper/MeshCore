"""Offline release identity gates; no serial port, SDK, browser or radio."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

HIL = Path(__file__).resolve().parents[1] / "tools/hil"
sys.path.insert(0, str(HIL))
# The acceptance code's serial entry points are never called by this fixture.
# Keep the offline contract runnable without installing a hardware dependency.
serial = ModuleType("serial")
serial.tools = ModuleType("serial.tools")
serial.tools.list_ports = SimpleNamespace(comports=lambda: [])
with mock.patch.dict(sys.modules, {"serial": serial, "serial.tools": serial.tools}):
    spec = importlib.util.spec_from_file_location("companion_acceptance", HIL / "companion_release_acceptance.py")
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)

VERSION = "v1.18.0.1-halo-keymind-cascade-dev-652289ee"
BUILD = "18-Oct-2026"
FULL_VERSION = VERSION + " (Build: " + BUILD + ")"


def fixed(value, size):
    return value.encode("ascii").ljust(size, b"\0")


def self_info():
    frame = bytearray(58)
    frame[:4] = bytes((5, 1, 20, 22))
    frame[4:36] = bytes(range(1, 33))
    struct.pack_into("<II", frame, 48, 910525, 62500)
    frame[56:58] = bytes((7, 5))
    return bytes(frame)


def device_info(board="Heltec V4"):
    return (bytes((13, 14, 50, 1)) + struct.pack("<I", 987654)
            + fixed(BUILD, 12) + fixed(board, 40) + fixed(VERSION[:19], 20)
            + bytes((0, 2)))


class WireSession(acceptance.Session):
    """Exercise production handshake/request validation with inert frames."""
    version_reply = b"\x1d7f|" + FULL_VERSION.encode("ascii")
    board_reply = device_info()
    sent = []

    def __enter__(self):
        self.handshake()
        return self

    def __exit__(self, *_):
        pass

    def send(self, payload):
        self.sent.append(payload)
        self.pending = payload

    def receive(self, timeout=None):
        if self.pending[:1] == b"\x01":
            return self_info()
        if self.pending[:1] == b"\x16":
            return self.board_reply
        if self.pending[:1] == b"\x42":
            return self.version_reply
        raise AssertionError("Unexpected request; no mutation is allowed by this wire fixture")

    def contacts(self):
        return {}

    def channels(self):
        return [{"index": 0, "name_hex": "70726976617465", "key_hex": "ab" * 32}]

    def stats(self):
        return {"uptime_seconds": 100, "error_flags": 0}


class ReleaseIdentityTests(unittest.TestCase):
    def setUp(self):
        WireSession.sent = []
        WireSession.version_reply = b"\x1d7f|" + FULL_VERSION.encode("ascii")
        WireSession.board_reply = device_info()

    def session(self):
        return WireSession(SimpleNamespace(expected_version=VERSION, expected_board="Heltec V4", serial="ExactUSB123"),
                           acceptance.TransportCounters())

    def test_exact_complete_candidate_and_device_build_are_verified(self):
        session = self.session()
        snapshot = session.snapshot()
        self.assertEqual(WireSession.sent[-1], b"\x427f|ver")
        self.assertEqual(session.full_cli_version, FULL_VERSION)
        self.assertEqual(snapshot["full_cli_version"], FULL_VERSION)
        self.assertEqual(snapshot["full_cli_version_frame_hex"], WireSession.version_reply.hex())
        self.assertEqual(acceptance.stable_state(snapshot)["full_cli_version"], FULL_VERSION)

    def test_same_19_byte_prefix_wrong_source_is_rejected_before_crud(self):
        other = VERSION.replace("652289ee", "d3e05abc")
        self.assertEqual(other[:19], VERSION[:19])
        WireSession.version_reply = b"\x1d7f|" + (other + " (Build: " + BUILD + ")").encode()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            args = SimpleNamespace(mode="owned-crud", expected_version=VERSION, expected_board="Heltec V4",
                                   manifest=None, serial="ExactUSB123", snapshot=directory / "private.json",
                                   report=directory / "report.json")
            output = io.StringIO()
            with mock.patch.object(acceptance, "parse_args", return_value=args), \
                    mock.patch.object(acceptance, "Session", WireSession), \
                    mock.patch.object(acceptance, "owned_plan") as plan, \
                    mock.patch.object(acceptance, "owned_crud") as crud, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(acceptance.main(), 1)
            plan.assert_not_called()
            crud.assert_not_called()
            self.assertFalse(args.snapshot.exists())
            report = json.loads(args.report.read_text())
            self.assertFalse(report["passed"])
            self.assertFalse(report["source_version_verified"])
            self.assertNotIn("full_runtime_version", report)
            self.assertNotIn(WireSession.board_reply.hex(), output.getvalue())

    def test_malformed_or_uncorrelated_cli_reply_is_rejected(self):
        for reply in (b"\x1d" + FULL_VERSION.encode(), b"\x1d7g|" + FULL_VERSION.encode(),
                      b"\x1d7f|" + FULL_VERSION.encode() + b"\0", b"\x1d7f|\xff"):
            with self.subTest(reply=reply[:4]):
                WireSession.version_reply = reply
                with self.assertRaises(acceptance.AcceptanceError):
                    self.session().handshake()

    def test_wrong_board_is_rejected_before_local_cli(self):
        WireSession.board_reply = device_info("Unrelated board")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "board"):
            self.session().handshake()
        self.assertFalse(any(frame[0] == 0x42 for frame in WireSession.sent))

    def test_cli_error_and_incomplete_build_formatter_are_rejected(self):
        for reply in (b"\x01\x03", b"\x1d7f|Error: unavailable",
                      b"\x1d7f|" + VERSION.encode(),
                      b"\x1d7f|" + (VERSION + " (Build: different)").encode()):
            with self.subTest(reply=reply[:4]):
                WireSession.version_reply = reply
                with self.assertRaises(acceptance.AcceptanceError):
                    self.session().handshake()

    def test_sanitized_pass_records_full_version_without_pin_or_channel_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            args = SimpleNamespace(mode="snapshot", expected_version=VERSION, expected_board="Heltec V4",
                                   manifest=None, serial="ExactUSB123", snapshot=directory / "private.json",
                                   report=directory / "report.json")
            output = io.StringIO()
            with mock.patch.object(acceptance, "parse_args", return_value=args), \
                    mock.patch.object(acceptance, "Session", WireSession), contextlib.redirect_stdout(output):
                self.assertEqual(acceptance.main(), 0)
            report = json.loads(args.report.read_text())
            self.assertTrue(report["passed"] and report["source_version_verified"])
            self.assertEqual(report["full_runtime_version"], FULL_VERSION)
            self.assertFalse(report["live_flash_hash_verified"])
            self.assertFalse(report["cold_boot_tested"])
            self.assertEqual(args.snapshot.stat().st_mode & 0o777, 0o600)
            for sanitized in (output.getvalue(), args.report.read_text()):
                self.assertNotIn("ab" * 32, sanitized)
                self.assertNotIn(WireSession.board_reply.hex(), sanitized)
                self.assertNotIn("987654", sanitized)


if __name__ == "__main__":
    unittest.main()
