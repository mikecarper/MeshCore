"""SerialNode-compatible persistent SSH transport; no RF or settings policy."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import queue
import re
import shlex
import subprocess
import threading

from ethernet_serial_rpc import (MAX_LINE, PROTOCOL, decode_message,
                                 encode_message, validate_command)


def ssh_command(host, key, helper, port):
    if not isinstance(host, str) or not re.fullmatch(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*", host):
        raise ValueError("SSH host must be a hostname/IP, optionally user@host")
    for name, value in (("helper", helper), ("port", port)):
        if not isinstance(value, str) or not PurePosixPath(value).is_absolute() \
                or any(ord(character) < 32 for character in value):
            raise ValueError("SSH " + name + " must be an absolute remote path")
    key_path = Path(key)
    if not key_path.is_absolute() or not key_path.is_file():
        raise ValueError("SSH key must be an existing absolute local file")
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes", "-i", str(key_path), "--", host,
            shlex.join(["python3", helper, "--port", port])]


class SshSerialNode:
    def __init__(self, host, key, helper, path, *, process_factory=subprocess.Popen,
                 startup_timeout=15):
        self.commands = []
        self._closed = False
        self._sequence = 0
        self._messages = queue.Queue(maxsize=4)
        self._reader_error = None
        self._lock = threading.Lock()
        self.process = process_factory(ssh_command(host, key, helper, path), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self._reader = threading.Thread(target=self._read_lines, daemon=True)
        self._reader.start()
        try:
            ready = self._receive(startup_timeout)
            if ready.get("type") != "ready" or ready.get("protocol") != PROTOCOL \
                    or not isinstance(ready.get("identity"), dict) or not ready["identity"] \
                    or not isinstance(ready.get("hello"), str):
                raise RuntimeError("Invalid serial RPC startup identity")
            self.identity = dict(ready["identity"], ssh_host=host)
            self.hello = ready["hello"]
        except Exception:
            self._stop(force=True)
            raise

    def _read_lines(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_LINE + 1)
                if line and (len(line) > MAX_LINE or not line.endswith(b"\n")):
                    raise RuntimeError("Oversized or incomplete SSH RPC line")
                self._messages.put_nowait(line)
                if not line:
                    return
        except Exception as error:
            self._reader_error = str(error) or "SSH RPC receive queue overflow"
            if self.process.poll() is None:
                self.process.kill()  # Only this Popen child, never a process group.

    def _receive(self, timeout):
        if self._reader_error:
            raise RuntimeError(self._reader_error)
        try:
            line = self._messages.get(timeout=timeout)
        except queue.Empty:
            self._stop(force=True)
            raise TimeoutError("SSH serial RPC deadline exceeded") from None
        if self._reader_error:
            raise RuntimeError(self._reader_error)
        if not line:
            raise RuntimeError("SSH serial RPC closed before a complete response")
        return decode_message(line)

    def command(self, text, timeout=10):
        validate_command(text, timeout)
        with self._lock:
            if self._closed:
                raise RuntimeError("SSH serial RPC is closed")
            self._sequence += 1
            try:
                self.process.stdin.write(encode_message(dict(id=self._sequence, command=text, timeout=timeout)))
                self.process.stdin.flush()
                response = self._receive(timeout + 2)
                if type(response.get("id")) is not int or response["id"] != self._sequence \
                        or response.get("completed") is not True or not isinstance(response.get("reply"), str):
                    raise RuntimeError("SSH serial command was not confirmed complete: " + str(response.get("error", "invalid response")))
                reply = response["reply"]
                self.commands.append(dict(command=text, reply=reply))
                return reply
            except Exception:
                self._stop(force=True)
                raise

    def _stop(self, force=False):
        if self._closed:
            return
        self._closed = True
        try:
            self.process.stdin.close()  # EOF lets the remote server close its USB.
        except (OSError, ValueError):
            pass
        if force and self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        self._reader.join(timeout=1)
        self.process.stdout.close()

    def close(self):
        self._stop()
