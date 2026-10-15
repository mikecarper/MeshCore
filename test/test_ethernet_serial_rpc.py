"""Host-only SSH/serial framing and ownership tests; never opens hardware."""
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "hil"))
import ethernet_serial_rpc as rpc
from ethernet_ssh_serial import SshSerialNode, ssh_command
import ethernet_radio2_sweep as sweep


def ready(**changes):
    message = dict(type="ready", protocol=rpc.PROTOCOL, identity={"port": "/dev/ttyUSB0"}, hello="WELCOME")
    message.update(changes)
    return message


class Node:
    identity = {"port": "/dev/ttyUSB0"}
    hello = "WELCOME"

    def __init__(self, error=None):
        self.commands = []
        self.closed = False
        self.error = error

    def command(self, command, timeout):
        self.commands.append((command, timeout))
        if self.error:
            raise self.error
        return "> OK\n"

    def close(self):
        self.closed = True


class BlockingOutput:
    def __init__(self):
        self.event = threading.Event()

    def readline(self, limit):
        self.event.wait(5)
        return b""

    def close(self):
        self.event.set()


class Process:
    def __init__(self, output, stall_close=False):
        self.stdin = io.BytesIO()
        self.stdout = output if hasattr(output, "readline") else io.BytesIO(output)
        self.returncode = None
        self.kills = self.terminates = 0
        self.stall_close = stall_close

    def poll(self):
        return self.returncode

    def kill(self):
        self.kills += 1
        self.returncode = -9
        if isinstance(self.stdout, BlockingOutput):
            self.stdout.event.set()

    def terminate(self):
        self.terminates += 1
        self.returncode = -15

    def wait(self, timeout):
        if self.stall_close and self.returncode is None:
            raise subprocess.TimeoutExpired("owned ssh", timeout)
        self.returncode = self.returncode or 0
        return self.returncode


class SerialRpcTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.key = Path(self.folder.name) / "key"
        self.key.touch()

    def tearDown(self):
        self.folder.cleanup()

    def client(self, messages, **kwargs):
        process = Process(b"".join(rpc.encode_message(item) for item in messages))
        node = SshSerialNode("bench@example.test", str(self.key), "/tmp/helper.py", "/dev/ttyUSB0",
                             process_factory=lambda *args, **kw: process, **kwargs)
        return node, process

    def test_server_success_eof_closes_only_owned_serial(self):
        node, output = Node(), io.BytesIO()
        request = rpc.encode_message(dict(id=1, command="get lab.tx", timeout=12))
        self.assertEqual(rpc.serve(node, io.BytesIO(request), output), 0)
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(messages[0]["protocol"], rpc.PROTOCOL)
        self.assertEqual(messages[1], dict(id=1, completed=True, reply="> OK\n"))
        self.assertEqual(node.commands, [("get lab.tx", 12)])
        self.assertTrue(node.closed)

    def test_invalid_requests_never_reach_usb_and_close(self):
        invalid = [b"{}\n", b"[]\n", b"{}", b"x" * (rpc.MAX_LINE + 1),
                   rpc.encode_message(dict(id=1, command="get radio\nset radio bad", timeout=1)),
                   rpc.encode_message(dict(id=True, command="get radio", timeout=1)),
                   rpc.encode_message(dict(id=1, command="get radio", timeout=31)),
                   b'{"id":1,"command":"get radio","timeout":NaN}\n']
        for line in invalid:
            with self.subTest(line=line[:80]):
                node, output = Node(), io.BytesIO()
                self.assertEqual(rpc.serve(node, io.BytesIO(line), output), 1)
                self.assertFalse(node.commands)
                self.assertTrue(node.closed)
                self.assertFalse(json.loads(output.getvalue().splitlines()[-1])["completed"])

    def test_serial_timeout_not_reported_completed_or_followed_by_new_command(self):
        node, output = Node(TimeoutError("serial deadline")), io.BytesIO()
        request = rpc.encode_message(dict(id=1, command="get radio", timeout=.1))
        rpc.serve(node, io.BytesIO(request + request), output)
        self.assertEqual(len(node.commands), 1)
        self.assertFalse(json.loads(output.getvalue().splitlines()[-1])["completed"])
        self.assertTrue(node.closed)

    def test_shell_arguments_are_quoted_and_host_keys_stay_pinned(self):
        helper, port = "/tmp/has ' quotes/helper; nope.py", "/dev/serial/by-id/a; $(false)"
        argv = ssh_command("bench@example.test", str(self.key), helper, port)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(shlex.split(argv[-1]), ["python3", helper, "--port", port])
        for host in ("-oProxyCommand=bad", "host\nother", "user@host;touch x", "host other"):
            with self.assertRaises(ValueError):
                ssh_command(host, str(self.key), helper, port)
        with self.assertRaises(ValueError):
            ssh_command("host", str(self.key), "relative.py", port)

    def test_client_complete_response_logged_and_normal_close_is_eof(self):
        node, process = self.client([ready(), dict(id=1, completed=True, reply="> OK\n")])
        self.assertEqual(node.command("get radio", timeout=1), "> OK\n")
        request = json.loads(process.stdin.getvalue())
        self.assertEqual(request, dict(id=1, command="get radio", timeout=1))
        self.assertEqual(node.commands, [dict(command="get radio", reply="> OK\n")])
        node.close()
        node.close()
        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.kills, 0)

    def test_client_refuses_incomplete_mismatched_and_eof_replies(self):
        for messages in ([ready()], [ready(), dict(id=2, completed=True, reply="OK")],
                         [ready(), dict(id=1, completed=False, error="timeout")],
                         [ready(), dict(id=1, completed=1, reply="OK")]):
            with self.subTest(messages=messages):
                node, process = self.client(messages)
                with self.assertRaises(RuntimeError):
                    node.command("get radio", timeout=.01)
                self.assertFalse(node.commands)
                self.assertTrue(node._closed)
                self.assertEqual(process.kills, 1)

    def test_invalid_startup_identity_protocol_or_framing(self):
        for data in (rpc.encode_message(ready(protocol="other")), rpc.encode_message(ready(identity={})),
                     b"debug text\n", b"x" * (rpc.MAX_LINE + 1), b"{}"):
            process = Process(data)
            with self.subTest(data=data[:80]), self.assertRaises((ValueError, RuntimeError)):
                SshSerialNode("host", str(self.key), "/tmp/helper", "/dev/ttyUSB0",
                              process_factory=lambda *args, **kw: process)
            self.assertTrue(process.stdin.closed)

    def test_startup_deadline_kills_only_owned_child(self):
        process = Process(BlockingOutput())
        with self.assertRaises(TimeoutError):
            SshSerialNode("host", str(self.key), "/tmp/helper", "/dev/ttyUSB0",
                          process_factory=lambda *args, **kw: process, startup_timeout=.01)
        self.assertEqual(process.kills, 1)
        self.assertTrue(process.stdin.closed)

    def test_normal_close_has_bounded_terminate_fallback(self):
        node, process = self.client([ready()])
        process.stall_close = True
        node.close()
        self.assertEqual(process.terminates, 1)
        self.assertEqual(process.kills, 0)

    def test_client_rejects_bad_commands_before_writing(self):
        node, process = self.client([ready()])
        for command, timeout in (("get x\nset x", 1), ("x" * 513, 1), ("get x", float("inf")), ("get x", True)):
            with self.assertRaises(ValueError):
                node.command(command, timeout)
        self.assertEqual(process.stdin.getvalue(), b"")
        node.close()

    def test_fast_board_cases_are_exact_and_909_5_only(self):
        self.assertEqual([case.tuple_text for case in sweep.CANDIDATES[9:]], [
            "909.5,500,5,5,32", "909.5,500,5,5,64", "909.5,500,5,5,128", "909.5,500,5,5,256",
            "909.5,500,7,5,32", "909.5,500,7,5,64", "909.5,250,7,5,32", "909.5,500,8,5,32",
            "909.5,125,7,5,32", "909.5,250,8,5,32", "909.5,250,7,5,48",
            "909.5,500,5,5,512", "909.5,125,7,5,48"])


if __name__ == "__main__":
    unittest.main()
