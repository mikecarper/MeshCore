"""Binary/identity/ownership tests; no USB hardware, namespaces or listeners."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import windows_usb_stdio_bridge as usb


SERIAL = "AABBCCDDEEFF"
IDENTITY = dict(port="COM6", serial=SERIAL, vid=0x303a, pid=0x1001)


def device_info(serial=SERIAL, port="COM6", vid=0x303a, pid=0x1001):
    return SimpleNamespace(device=port, serial_number=serial, vid=vid, pid=pid)


class WindowsBridgeTests(unittest.TestCase):
    def test_exact_endpoint_and_required_settings_fail_before_open(self):
        self.assertEqual(usb.validate_windows_endpoint("winusb://com6").netloc, "com6")
        for value in ("winusb://COM0", "winusb://COM6/", "winusb://COM6:2", "winusb://user@COM6",
                      "winusb://COM6?baud=1", "winusb://COM6#x", "winusb:///COM6", "tcp://COM6"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                usb.validate_windows_endpoint(value)
        for serial, vid, pid, duration in ((None, 1, 1, 5), ("bad\n", 1, 1, 5), (SERIAL, None, 1, 5),
                (SERIAL, 1, 0, 5), (SERIAL, 1, 1, 0), (SERIAL, 1, 1, float("nan")), (SERIAL, 1, 1, 3601)):
            with self.assertRaises(ValueError):
                usb.validate_settings("COM6", serial, vid, pid, 115200, False, duration)

    def test_wrong_missing_ambiguous_usb_identity_never_opens(self):
        for inventory in ([], [device_info("OTHER")], [device_info(vid=1)], [device_info(pid=2)],
                          [device_info(port="COM7")], [device_info(), device_info()]):
            factory = mock.Mock()
            with self.assertRaises(ValueError):
                usb.open_selected_port("COM6", SERIAL, 0x303a, 0x1001, baud=115200, serial_dtr=False,
                                       inventory=lambda: inventory, serial_factory=factory)
            factory.assert_not_called()

    def test_safe_exclusive_open_checks_twice_and_selects_dtr_without_reset(self):
        for dtr in (False, True):
            device = mock.Mock()
            inventory = mock.Mock(return_value=[device_info()])
            with mock.patch.object(usb, "open_configured_session") as opened:
                result, identity = usb.open_selected_port("com6", SERIAL, 0x303a, 0x1001,
                    baud=115200, serial_dtr=dtr, inventory=inventory, serial_factory=lambda: device)
            self.assertIs(result, device)
            self.assertEqual(identity, dict(IDENTITY, baud=115200, serial_dtr=dtr))
            self.assertEqual(inventory.call_count, 2)
            opened.assert_called_once_with(device)
            self.assertEqual(device.port, "COM6")
            self.assertIs(device.exclusive, True)
            self.assertIs(device.dtr, dtr)
            self.assertIs(device.rts, False)
            self.assertEqual((device.timeout, device.write_timeout), (.05, .5))
            device.close.assert_not_called()

    def test_busy_or_swapped_identity_closes_only_owned_handle(self):
        for swap in (False, True):
            device = mock.Mock()
            inventory = mock.Mock(side_effect=[[device_info()], [device_info("OTHER")]])
            with mock.patch.object(usb, "open_configured_session", side_effect=None if swap else OSError("busy")):
                with self.assertRaises((ValueError, OSError)):
                    usb.open_selected_port("COM6", SERIAL, 0x303a, 0x1001, baud=115200, serial_dtr=True,
                        inventory=inventory, serial_factory=lambda: device)
            device.close.assert_called_once()

    def test_duplex_short_writes_preserve_every_binary_byte(self):
        payload = b"\0\xc0\xdb\xff\r\n" + bytes(range(256))
        written, output = bytearray(), bytearray()
        output_done = threading.Event()
        supplied = [False]
        returned = [False]
        device = mock.Mock()
        device.in_waiting = 0

        def host_read(fd, count):
            if not supplied[0]:
                supplied[0] = True
                return payload
            output_done.wait(1)
            return b""

        def serial_read(count):
            if not returned[0] and bytes(written) == payload:
                returned[0] = True
                return payload
            time.sleep(.001)
            return b""

        def serial_write(data):
            written.extend(data[:3])
            return min(3, len(data))

        def host_write(fd, data):
            output.extend(data[:2])
            if len(output) == len(payload):
                output_done.set()
            return min(2, len(data))

        device.write.side_effect, device.read.side_effect = serial_write, serial_read
        stats = usb.pump_stdio(device, 11, 12, duration=2, read=host_read, write=host_write)
        self.assertEqual(bytes(written), payload)
        self.assertEqual(bytes(output), payload)
        self.assertEqual(stats, dict(to_usb=len(payload), from_usb=len(payload), reason="stdin_eof"))
        device.close.assert_called_once()

    def test_eof_and_deadline_close_usb_even_if_stdout_worker_is_blocked(self):
        for eof in (False, True):
            blocked, release = threading.Event(), threading.Event()
            device = mock.Mock()
            device.in_waiting = 4
            device.read.return_value = b"\0\xc0\xdb\xff"

            def read(fd, count):
                blocked.wait(.2)
                if not eof:
                    release.wait(1)
                return b""

            def write(fd, data):
                blocked.set()
                release.wait(1)
                return len(data)

            started = time.monotonic()
            try:
                stats = usb.pump_stdio(device, 11, 12, duration=.05, read=read, write=write)
                self.assertLess(time.monotonic() - started, .6)
                device.close.assert_called_once()
                self.assertEqual(stats["reason"], "stdin_eof" if eof else "deadline")
            finally:
                release.set()

    def test_parent_uses_absolute_trusted_argv_no_shell_and_distinct_nonblocking_fds(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python.exe"
            executable.touch()
            child = mock.Mock()
            child.stdout.fileno.return_value, child.stdin.fileno.return_value = 11, 12
            identity = dict(IDENTITY, baud=115200, serial_dtr=True)
            with mock.patch.object(usb.sys, "platform", "linux"), \
                    mock.patch.object(usb.subprocess, "run", return_value=SimpleNamespace(stdout="C:\\git\\MeshCore\\tools\\hil\\windows_usb_stdio_bridge.py\n")), \
                    mock.patch.object(usb.subprocess, "Popen", return_value=child) as popen, \
                    mock.patch.object(usb, "_ready_record", return_value=dict(identity=identity)), \
                    mock.patch.object(usb.os, "set_blocking") as nonblocking:
                stream = usb.WindowsUsbStream("winusb://COM6", python=str(executable), expected_serial=SERIAL,
                    expected_vid=0x303a, expected_pid=0x1001, serial_dtr=True, duration=300)
                self.assertEqual((stream.read_fd, stream.write_fd), (11, 12))
                self.assertEqual(stream.identity, identity)
                argv = popen.call_args.args[0]
                self.assertEqual(argv[:3], [str(executable), "-u", "C:\\git\\MeshCore\\tools\\hil\\windows_usb_stdio_bridge.py"])
                self.assertIn(SERIAL, argv)
                self.assertIs(popen.call_args.kwargs["shell"], False)
                self.assertEqual(nonblocking.call_args_list, [mock.call(11, False), mock.call(12, False)])
                stream.close()
            child.stdin.close.assert_called_once()
            child.wait.assert_called_once_with(timeout=2)
            child.terminate.assert_not_called()

    def test_parent_rejects_changed_ready_identity_and_closes_owned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "python.exe"
            executable.touch()
            child = mock.Mock()
            with mock.patch.object(usb.sys, "platform", "linux"), \
                    mock.patch.object(usb.subprocess, "run", return_value=SimpleNamespace(stdout="C:\\trusted\\windows_usb_stdio_bridge.py")), \
                    mock.patch.object(usb.subprocess, "Popen", return_value=child), \
                    mock.patch.object(usb, "_ready_record", return_value=dict(identity=dict(IDENTITY, serial="OTHER"))):
                with self.assertRaisesRegex(ValueError, "identity"):
                    usb.WindowsUsbStream("winusb://COM6", python=str(executable), expected_serial=SERIAL,
                        expected_vid=0x303a, expected_pid=0x1001)
            child.stdin.close.assert_called_once()
            child.wait.assert_called_once_with(timeout=2)

    def test_parent_close_eof_then_bounded_owned_process_cleanup(self):
        stream = usb.WindowsUsbStream.__new__(usb.WindowsUsbStream)
        child = mock.Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired("owned", 2), subprocess.TimeoutExpired("owned", 1), 0]
        stream.process = child
        stream.close()
        stream.close()
        child.stdin.close.assert_called_once()
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_args_list, [mock.call(timeout=2), mock.call(timeout=1), mock.call(timeout=1)])

    def test_ready_status_bound_and_invalid_records_do_not_expose_child_stderr(self):
        for data in (b"secret payload, not JSON\n", b'{"state":"error","error":"private"}\n', b"x" * 4097):
            child = mock.Mock()
            child.stderr.fileno.return_value = 11
            with mock.patch.object(usb.select, "select", return_value=([11], [], [])), \
                    mock.patch.object(usb.os, "read", return_value=data):
                with self.assertRaises(RuntimeError) as failure:
                    usb._ready_record(child)
            self.assertNotIn("secret", str(failure.exception))
            self.assertNotIn("private", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
