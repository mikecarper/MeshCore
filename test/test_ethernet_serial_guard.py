"""Read-only serial ownership preflight and delayed USB reply regressions."""

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


BASE = Path(__file__).resolve().parents[1] / "tools/hil"
sys.path.insert(0, str(BASE))
import serial_session as session
import ethernet_primary_probe as primary


class PortOwnershipTests(unittest.TestCase):
    def run_inspector(self, result):
        return patch.object(session.subprocess, "run", return_value=result)

    def test_existing_owner_refuses_without_killing_anything(self):
        with patch.object(session.sys, "platform", "linux"), \
                patch.object(session.shutil, "which", return_value="/usr/bin/fuser"), \
                self.run_inspector(SimpleNamespace(returncode=0)) as run:
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                session.assert_port_idle("/dev/ttyUSB0")
        self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/fuser", "--"])
        self.assertNotIn("-k", run.call_args.args[0])

    def test_idle_port_is_allowed(self):
        with patch.object(session.sys, "platform", "linux"), \
                patch.object(session.shutil, "which", return_value="/usr/bin/fuser"), \
                self.run_inspector(SimpleNamespace(returncode=1)):
            session.assert_port_idle("/dev/ttyUSB0")

    def test_inspection_failure_refuses(self):
        with patch.object(session.sys, "platform", "linux"), \
                patch.object(session.shutil, "which", return_value="/usr/bin/fuser"), \
                self.run_inspector(SimpleNamespace(returncode=2)):
            with self.assertRaisesRegex(RuntimeError, "fuser error"):
                session.assert_port_idle("/dev/ttyUSB0")

    def test_timeout_refuses(self):
        with patch.object(session.sys, "platform", "linux"), \
                patch.object(session.shutil, "which", return_value="/usr/bin/fuser"), \
                patch.object(session.subprocess, "run", side_effect=subprocess.TimeoutExpired("fuser", 3)):
            with self.assertRaisesRegex(RuntimeError, "Cannot safely inspect"):
                session.assert_port_idle("/dev/ttyUSB0")

    def test_missing_optional_inspector_does_not_spawn_a_process(self):
        with patch.object(session.sys, "platform", "linux"), \
                patch.object(session.shutil, "which", return_value=None), \
                patch.object(session.subprocess, "run") as run:
            session.assert_port_idle("/dev/ttyUSB0")
        run.assert_not_called()

    def test_non_linux_does_not_try_fuser(self):
        with patch.object(session.sys, "platform", "win32"), \
                patch.object(session.subprocess, "run") as run:
            session.assert_port_idle("COM4")
        run.assert_not_called()


class DelayedPort:
    def __init__(self, clock, first_byte=1):
        self.clock = clock
        self.first_byte = first_byte
        self.sent = False
        self.in_waiting = 0

    def read(self, count):
        self.clock.now += 0.04
        if not self.sent and self.clock.now >= self.first_byte:
            self.sent = True
            return b"delayed reply\r\n"
        return b""


class DelayedReplyTests(unittest.TestCase):
    def bench_module(self):
        # Avoid importing optional pySerial just to exercise the pure read loop.
        from types import ModuleType
        serial = ModuleType("serial")
        tools = ModuleType("serial.tools")
        tools.list_ports = ModuleType("serial.tools.list_ports")
        spec = importlib.util.spec_from_file_location("ethernet_bench_delayed_test", BASE / "ethernet_bench_serial.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"serial": serial, "serial.tools": tools,
                                     "serial.tools.list_ports": tools.list_ports}):
            spec.loader.exec_module(module)
        return module

    def test_bench_waits_full_deadline_for_first_reply_byte(self):
        module = self.bench_module()
        clock = SimpleNamespace(now=0)
        port = DelayedPort(clock, first_byte=1.2)
        with patch.object(module.time, "monotonic", side_effect=lambda: clock.now):
            self.assertEqual(module.read_quiet(port, timeout=3), "delayed reply\r\n")
        self.assertGreaterEqual(clock.now, 1.2)
        self.assertLess(clock.now, 2)


class TimedChunks:
    def __init__(self, clock, events):
        self.clock = clock
        self.events = list(events)
        self.pending = bytearray()

    @property
    def in_waiting(self):
        return len(self.pending)

    def read(self, count):
        self.clock.now += 0.04
        while self.events and self.events[0][0] <= self.clock.now:
            self.pending.extend(self.events.pop(0)[1])
        out = bytes(self.pending[:count])
        del self.pending[:count]
        return out


class CommandTransactionTests(unittest.TestCase):
    def transact(self, events, command="set radio 909.5,500,5,5", timeout=3):
        clock = SimpleNamespace(now=0)
        port = TimedChunks(clock, events)
        with patch.object(session.time, "monotonic", side_effect=lambda: clock.now):
            reply = session.read_command_reply(port, command, timeout)
        return reply, clock.now

    def test_immediate_echo_then_long_nvs_pause_waits_for_actual_reply(self):
        reply, elapsed = self.transact([
            (0.04, b"> set radio 909.5,500,5,5\r\n"),
            (1.1, b"  -> OK - saved\r\n> ")])
        self.assertIn("OK - saved", reply)
        self.assertGreaterEqual(elapsed, 1.1)

    def test_stale_other_command_reply_and_prompt_cannot_complete_this_command(self):
        reply, elapsed = self.transact([
            (0.04, b"  -> OK - previous command\r\n> "),
            (0.7, b"set radio 909.5,500,5,5\r\n"),
            (1.4, b"  -> OK - current command\r\n> ")])
        self.assertIn("OK - current command", reply)
        self.assertGreaterEqual(elapsed, 1.4)

    def test_wrong_echo_does_not_count_even_with_prompt(self):
        clock = SimpleNamespace(now=0)
        port = TimedChunks(clock, [(0.04, b"> get radio\r\n  -> > 909.5,500,5,5\r\n> ")])
        with patch.object(session.time, "monotonic", side_effect=lambda: clock.now):
            with self.assertRaises(session.SerialCommandTimeout) as caught:
                session.read_command_reply(port, "get radio2", timeout=1)
        self.assertIn("get radio", caught.exception.partial_reply)
        self.assertGreaterEqual(clock.now, 1)

    def test_getter_value_prefix_is_not_a_completion_prompt(self):
        reply, elapsed = self.transact([
            (0.04, b"> get radio\r\n  -> > 909.5,500,5,5\r\n"),
            (0.9, b">"), (1.2, b" ")], command="get radio")
        self.assertTrue(reply.endswith("> "))
        self.assertGreaterEqual(elapsed, 1.2)

    def test_masked_password_echo_is_matched_exactly_without_requiring_plaintext(self):
        reply, _ = self.transact([(0.04, b"> set wifi.pwd ********\r\n"),
                                 (1.0, b"  -> OK\r\n> ")], command="set wifi.pwd secret42")
        self.assertNotIn("secret42", reply)
        self.assertIn("OK", reply)

    def test_timeout_stops_remaining_commands_and_still_attempts_stop(self):
        module = DelayedReplyTests().bench_module()
        port = Mock(is_open=True)
        info = SimpleNamespace(device="/dev/ttyUSB-test", serial_number="test", vid=0x1A86, pid=0x7523)
        output = io.StringIO()
        args = ["ethernet_bench_serial.py", "--port", "/dev/ttyUSB-test",
                "--command", "set radio 909.5,500,5,5", "--command", "get radio"]
        with patch.object(module.serial, "Serial", return_value=port, create=True), \
                patch.object(module.list_ports, "comports", return_value=[info], create=True), \
                patch.object(module, "assert_port_idle"), \
                patch.object(module, "open_configured_session"), \
                patch.object(module, "read_quiet", return_value="WELCOME"), \
                patch.object(module, "read_command_reply", side_effect=session.SerialCommandTimeout("echo only")), \
                patch.object(sys, "argv", args), contextlib.redirect_stdout(output):
            self.assertEqual(module.main(), 1)
        written = [call.args[0] for call in port.write.call_args_list]
        self.assertNotIn(b"get radio\n", written)
        self.assertEqual(written[-1], b"+++MESHCORE-TERM-STOP\n")
        self.assertFalse(json.loads(output.getvalue())["commands"][0]["completed"])
        port.close.assert_called_once()

    def test_bench_stops_after_post_reply_quiet_interval(self):
        module = DelayedReplyTests().bench_module()
        clock = SimpleNamespace(now=0)
        with patch.object(module.time, "monotonic", side_effect=lambda: clock.now):
            module.read_quiet(DelayedPort(clock, first_byte=0), timeout=3)
        self.assertLess(clock.now, 0.6)

    def test_bench_silence_waits_whole_deadline(self):
        module = DelayedReplyTests().bench_module()
        clock = SimpleNamespace(now=0)
        with patch.object(module.time, "monotonic", side_effect=lambda: clock.now):
            self.assertEqual(module.read_quiet(DelayedPort(clock, first_byte=99), timeout=3), "")
        self.assertGreaterEqual(clock.now, 3)

    def test_primary_helper_also_waits_for_delayed_first_byte(self):
        clock = SimpleNamespace(now=0)
        node = SimpleNamespace(port=DelayedPort(clock, first_byte=1.2))
        with patch.object(primary.time, "monotonic", side_effect=lambda: clock.now):
            self.assertEqual(primary.SerialNode.read(node, timeout=3), "delayed reply\r\n")
        self.assertGreaterEqual(clock.now, 1.2)


if __name__ == "__main__":
    unittest.main()
