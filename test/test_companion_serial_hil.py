"""Exercise the shared local USB transport without opening a real device."""

from collections import deque
from contextlib import ExitStack
from pathlib import Path
import os
import struct
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/hil"
sys.path.insert(0, str(TOOLS))
import companion_serial as HIL


def device_frame(payload):
    return b">" + struct.pack("<H", len(payload)) + payload


def self_info():
    result = bytearray(58)
    result[:2] = b"\x05\x01"
    result[4:36] = bytes(range(1, 33))
    struct.pack_into("<II", result, 48, 910525, 62500)
    result[56:58] = bytes((7, 5))
    return bytes(result) + b"USB fixture"


def device_info():
    result = bytearray(82)
    result[:4] = bytes((13, 14, 0, 3))
    result[20:27] = b"Fixture"
    result[60:67] = b"1.2.3.4"
    return bytes(result)


class FakeSerial:
    def __init__(self, **options):
        self.options = options
        self.events = []
        self._dtr, self._rts, self._dsrdtr, self._rtscts = True, True, False, False
        self.driver_dtr, self.driver_rts = True, True
        self.port = None
        self.is_open = False
        self.chunks = deque()
        self.responses = deque()
        self.negotiation = b"> Binary mode\r\n"
        self.fail_open = False
        self.short_write = False

    def _line(name):
        def get(self):
            return getattr(self, "_" + name)

        def set(self, value):
            setattr(self, "_" + name, value)
            self.events.append((name, value))
            if self.is_open and name in ("dtr", "rts"):
                getattr(self, "_update_" + name + "_state")()
        return property(get, set)

    dtr = _line("dtr")
    rts = _line("rts")
    dsrdtr = _line("dsrdtr")
    rtscts = _line("rtscts")

    def _update_dtr_state(self):
        self.driver_dtr = self.dtr
        self.events.append(("driver_dtr", self.driver_dtr))

    def _update_rts_state(self):
        self.driver_rts = self.rts
        self.events.append(("driver_rts", self.driver_rts))

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def open(self):
        self.events.append(("open", self.dtr, self.rts, self.dsrdtr))
        if self.fail_open:
            raise OSError("fixture open failure")
        self.is_open = True
        # Match pySerial's independent open-time modem hooks. The Linux
        # atomic opener must suppress both until the joint ioctl below.
        if not self.dsrdtr:
            self._update_dtr_state()
        if not self.rtscts:
            self._update_rts_state()
        self.events.append(("opened_levels", self.driver_dtr, self.driver_rts))

    def fileno(self):
        return 77

    def close(self):
        self.events.append(("close",))
        self.is_open = False

    def write(self, data):
        self.events.append(("write", data))
        if data == b"+++MESHCORE-TERM-STOP\r":
            if self.negotiation:
                self.chunks.append(self.negotiation)
        elif data.startswith(b"<") and self.responses:
            # Fragmentation and a push frame exercise the actual shared reader.
            reply = device_frame(b"\x80notice") + device_frame(self.responses.popleft())
            self.chunks.extend((reply[:2], reply[2:7], reply[7:]))
        return len(data) - int(self.short_write)

    def flush(self):
        self.events.append(("flush",))

    def read(self, count):
        if not self.chunks:
            return b""
        chunk = self.chunks.popleft()
        if len(chunk) > count:
            self.chunks.appendleft(chunk[count:])
        return chunk[:count]


class CompanionSerialTests(unittest.TestCase):
    def connect(self, port=None):
        port = port or FakeSerial()
        module = ModuleType("serial")
        module.Serial = mock.Mock(return_value=port)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(sys.modules, {"serial": module}))
            if sys.platform.startswith("linux") and os.name == "posix":
                import fcntl
                import termios

                def attributes(fd):
                    self.assertEqual(fd, 77)
                    return [0, 0, termios.HUPCL | 0x4000, 0, 0, 0, []]

                def clear_hupcl(fd, when, values):
                    self.assertEqual((fd, when), (77, termios.TCSANOW))
                    self.assertEqual(values, [0, 0, 0x4000, 0, 0, 0, []])
                    port.events.append(("clear_hupcl",))

                def clear_lines(fd, request, packed):
                    self.assertEqual((fd, request), (77, termios.TIOCMBIC))
                    self.assertEqual(struct.unpack("I", packed)[0], termios.TIOCM_DTR | termios.TIOCM_RTS)
                    port.driver_dtr = port.driver_rts = False
                    port.events.append(("joint_clear",))

                stack.enter_context(mock.patch.object(termios, "tcgetattr", side_effect=attributes))
                stack.enter_context(mock.patch.object(termios, "tcsetattr", side_effect=clear_hupcl))
                stack.enter_context(mock.patch.object(fcntl, "ioctl", side_effect=clear_lines))
            node = HIL.Companion("/dev/fake-local")
        self.assertEqual(module.Serial.call_args.kwargs,
                         {"port": None, "baudrate": 115200, "timeout": 0.05,
                          "write_timeout": 2, "exclusive": True})
        return node, port

    def test_clean_import_has_no_serial_dependency_or_port_access(self):
        script = (
            "import sys; sys.path.insert(0, " + repr(str(TOOLS)) + "); "
            "import companion_serial, companion_raw_radio2_sweep, mota_usb_seeder; "
            "assert 'serial' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-S", "-B", "-c", script],
                                cwd="/tmp", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_safe_configured_open_and_close_do_not_pulse_reset_lines(self):
        node, port = self.connect()
        self.assertEqual(port.events[:2], [("dtr", False), ("rts", False)])
        opened = next(event for event in port.events if event[0] == "open")
        self.assertEqual(opened[1:3], (False, False))
        if sys.platform.startswith("linux") and os.name == "posix":
            self.assertFalse(opened[3])
            self.assertIn(("opened_levels", True, True), port.events)
            self.assertEqual(port.events[-2:], [("clear_hupcl",), ("joint_clear",)])
            self.assertFalse(any(event[0] in ("driver_dtr", "driver_rts") for event in port.events))
            self.assertNotIn("_update_dtr_state", port.__dict__)
            self.assertNotIn("_update_rts_state", port.__dict__)
        elif os.name == "posix":
            self.assertTrue(opened[3])
        self.assertFalse(port.driver_dtr or port.driver_rts)
        self.assertFalse(port.dsrdtr)
        self.assertFalse(any(event in (("dtr", True), ("rts", True)) for event in port.events))
        node.close()
        self.assertFalse(port.is_open)

    def test_failed_open_closes_the_handle_and_restores_handshake(self):
        port = FakeSerial()
        port.fail_open = True
        with self.assertRaisesRegex(OSError, "fixture open failure"):
            self.connect(port)
        self.assertIn(("close",), port.events)
        self.assertFalse(port.dsrdtr)
        self.assertFalse(port.is_open)

    def test_start_negotiates_then_validates_actual_fragmented_frames(self):
        node, port = self.connect()
        stale_reader = node.reader
        stale_reader._buffer.extend(device_frame(b"\x01\x05"))
        port.responses.extend((device_info(), self_info()))
        with mock.patch.object(HIL.time, "sleep"):
            result = node.start()
        self.assertIsNot(node.reader, stale_reader)
        self.assertEqual(result["protocol"], 14)
        self.assertEqual(result["max_channels"], 3)
        self.assertEqual(node.max_channels, 3)
        self.assertEqual(result["primary"]["node_name"], "USB fixture")
        self.assertEqual(result["model"], "Fixture")
        self.assertEqual(result["firmware"], "1.2.3.4")
        self.assertEqual(result["usb_binary_negotiation"],
                         {"explicit_stop_token": True,
                          "discarded_terminal_bytes": len(port.negotiation), "dtr": True})
        writes = [event[1] for event in port.events if event[0] == "write"]
        self.assertEqual(writes, [b"+++MESHCORE-TERM-STOP\r",
                                 HIL.encode_host_frame(bytes((22, 14))),
                                 HIL.encode_host_frame(HIL.make_app_start_spec("Meshtext-Auto-HIL").payload)])
        stop = port.events.index(("write", writes[0]))
        lines_before_stop = [event for event in port.events[:stop] if event[0] in ("dtr", "rts")]
        self.assertEqual(lines_before_stop[-2:], [("rts", False), ("dtr", True)])

    def test_start_rejects_logging_owner_before_any_framed_request(self):
        node, port = self.connect()
        port.negotiation = b"Error: set usb.logging off before Binary mode\r\n"
        with mock.patch.object(HIL.time, "sleep"), self.assertRaisesRegex(HIL.Failure, "USB logging owns"):
            node.start()
        self.assertEqual([event[1] for event in port.events if event[0] == "write"],
                         [b"+++MESHCORE-TERM-STOP\r"])

    def test_start_rejects_truncated_device_and_invalid_primary_information(self):
        for replies, reason in (((b"\x0d",), "truncated Companion device info"),
                                ((device_info(), b"\x05"), "SELF_INFO length")):
            with self.subTest(reason=reason):
                node, port = self.connect()
                port.responses.extend(replies)
                with mock.patch.object(HIL.time, "sleep"), self.assertRaisesRegex(RuntimeError, reason):
                    node.start()

    def test_cli_retains_exact_retry_error_and_redacts_keys(self):
        node, port = self.connect()
        port.responses.append(b"\x1dError: radio busy or TX power rejected")
        with self.assertRaisesRegex(HIL.Failure,
                "^Companion CLI set tx rejected: Error: radio busy or TX power rejected$"):
            node.cli("set tx 2")
        key = "private-channel-key"
        port.responses.append(("\x1dError: echoed " + key).encode())
        with self.assertRaises(HIL.Failure) as rejected:
            node.cli("set mt.channel 1 mt-lab " + key)
        self.assertNotIn(key, str(rejected.exception))
        self.assertIn("set mt.channel rejected: Error: echoed <redacted>", str(rejected.exception))
        secret = "a1" * 16
        with mock.patch.object(node, "request", side_effect=RuntimeError("diagnostic " + secret)):
            with self.assertRaises(HIL.Failure) as failed:
                node.cli("set mt.channel 1 mt-lab " + secret)
        self.assertNotIn(secret, str(failed.exception))
        self.assertIn("<redacted>", str(failed.exception))

    def test_request_rejects_partial_write_device_error_and_wrong_response(self):
        node, port = self.connect()
        port.short_write = True
        with self.assertRaisesRegex(HIL.Failure, "incomplete Companion write"):
            node.request(b"\x16\x0e", (13,))
        port.short_write = False
        for reply, reason in ((b"\x01\x09", "command rejected, error 09"),
                              (b"\x0a", "unexpected Companion response 10")):
            with self.subTest(reason=reason):
                port.responses.append(reply)
                with self.assertRaisesRegex(HIL.Failure, reason):
                    node.request(b"\x16\x0e", (13,))

    def test_request_retries_frame_timeout_with_a_bounded_deadline(self):
        node, _ = self.connect()
        with mock.patch.object(node.reader, "read_frame", side_effect=HIL.FrameTimeout("fixture")) as read, \
                mock.patch.object(HIL.time, "monotonic", side_effect=(0, 0.1, 0.2, 5.1)):
            with self.assertRaisesRegex(HIL.Failure, "response timeout for command 22"):
                node.request(b"\x16\x0e", (13,))
        read.assert_called_once()

    def test_channel_name_uses_existing_request_and_minimum_size(self):
        node, port = self.connect()
        reply = bytearray(50)
        reply[0] = 18
        reply[2:7] = b"Local"
        port.responses.append(bytes(reply))
        self.assertEqual(node.channel_name(2), "Local")
        self.assertIn(("write", HIL.encode_host_frame(bytes((31, 2)))), port.events)
        port.responses.append(b"\x12")
        with self.assertRaisesRegex(HIL.Failure, "truncated channel info"):
            node.channel_name(2)

    def test_negotiation_drain_is_bounded_by_bytes_and_time(self):
        port = FakeSerial()
        port.negotiation = b"x" * 65537
        with mock.patch.object(HIL.time, "sleep"), \
                mock.patch.object(HIL.time, "monotonic", return_value=0), \
                self.assertRaisesRegex(HIL.Failure, "bounded negotiation drain"):
            HIL.enter_binary_mode(port)
        port = FakeSerial()
        port.negotiation = b"x" * 20000
        with mock.patch.object(HIL.time, "sleep"), \
                mock.patch.object(HIL.time, "monotonic", side_effect=(0, 0.1, 0.5, 1.1)):
            result = HIL.enter_binary_mode(port)
        self.assertEqual(result["discarded_terminal_bytes"], 8192)
        self.assertTrue(port.chunks)

    def test_port_ownership_guard_preserves_fail_closed_statuses(self):
        with mock.patch.object(HIL.sys, "platform", "linux"), \
                mock.patch.object(HIL.shutil, "which", return_value="/bin/fuser"), \
                mock.patch.object(HIL.subprocess, "run") as inspect:
            inspect.return_value = SimpleNamespace(returncode=1)
            HIL.require_idle_port("/dev/fake-local")
            inspect.assert_called_with(["/bin/fuser", os.path.realpath("/dev/fake-local")],
                                       capture_output=True, timeout=3)
            for status in (0, 2):
                inspect.return_value = SimpleNamespace(returncode=status)
                with self.assertRaisesRegex(HIL.Failure, "busy or ownership inspection failed"):
                    HIL.require_idle_port("/dev/fake-local")


if __name__ == "__main__":
    unittest.main()
