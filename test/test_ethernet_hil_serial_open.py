"""Actual HIL constructors must not pulse CH340 reset on POSIX serial open."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


BASE = Path(__file__).resolve().parents[1] / "tools/hil"
sys.path.insert(0, str(BASE))
import ethernet_kiss_probe as probe
import ethernet_tap_relay as tap


class FakeSerial:
    def __init__(self, **kwargs):
        self.dsrdtr = False
        self._dtr = True
        self._rts = True
        self.driver_rts = True
        self.driver_dtr = True
        self.is_open = False
        self.resets = 0
        self.writes = []
        self.trace = []

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        self._dtr = value
        if self.is_open:
            self._apply_dtr(value)

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        self._rts = value
        if self.is_open:
            self.driver_rts = value
            self.trace.append(("rts", value))

    def _apply_dtr(self, value):
        if not value and self.driver_rts:
            self.resets += 1
        self.driver_dtr = value
        self.trace.append(("dtr", value))

    def open(self):
        self.is_open = True
        # POSIX pySerial's real open order: DTR before RTS unless deferred.
        if not self.dsrdtr:
            self._apply_dtr(self._dtr)
        self.rts = self._rts

    def close(self):
        self.is_open = False

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def fileno(self):
        return 77


def serial_modules(port):
    serial = ModuleType("serial")
    serial.Serial = Mock(return_value=port)
    tools = ModuleType("serial.tools")
    list_ports = ModuleType("serial.tools.list_ports")
    info = SimpleNamespace(device="/dev/ttyUSB-bench", serial_number="bench-only",
                           vid=0x1A86, pid=0x7523)
    list_ports.comports = Mock(return_value=[info])
    tools.list_ports = list_ports
    serial.tools = tools
    return {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": list_ports}


@unittest.skipUnless(os.name == "posix", "POSIX CH340 pin-order behavior")
class EthernetHilSerialTests(unittest.TestCase):
    def assert_safe(self, port):
        self.assertEqual(port.resets, 0)
        self.assertEqual(port.trace[0], ("rts", False))
        self.assertFalse(port.dsrdtr)
        self.assertFalse(port.driver_rts)
        self.assertFalse(port.driver_dtr)

    def test_reset_simulator_detects_original_plain_open_bug(self):
        port = FakeSerial()
        port.dtr = port.rts = False
        port.open()
        self.assertEqual(port.resets, 1)

    def test_raw_kiss_probe_uses_shared_safe_opener(self):
        port = FakeSerial()
        with patch.dict(sys.modules, serial_modules(port)), patch.object(probe.threading, "Thread"):
            endpoint = probe.KissEndpoint("serial:///dev/ttyUSB-bench")
            endpoint.close()
        self.assert_safe(port)

    def test_tap_serial_transport_uses_shared_safe_opener(self):
        port = FakeSerial()
        with patch.dict(sys.modules, serial_modules(port)):
            endpoint = tap.KissStream("serial:///dev/ttyUSB-bench", 115200)
            endpoint.close()
        self.assert_safe(port)

    def test_companion_cli_uses_shared_safe_opener(self):
        port = FakeSerial()
        spec = importlib.util.spec_from_file_location("ethernet_bench_serial_under_test", BASE / "ethernet_bench_serial.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, serial_modules(port)):
            spec.loader.exec_module(module)
            with patch.object(sys, "argv", ["ethernet_bench_serial.py", "--port", "/dev/ttyUSB-bench",
                                             "--command", "get ethernet"]), \
                    patch.object(module, "read_quiet", return_value="OK"), \
                    patch.object(module, "read_command_reply", return_value="get ethernet\r\n  -> on\r\n> "), \
                    contextlib.redirect_stdout(io.StringIO()):
                module.main()
        self.assert_safe(port)
        self.assertIn(b"get ethernet\n", port.writes)
        self.assertFalse(port.is_open)


if __name__ == "__main__":
    unittest.main()
