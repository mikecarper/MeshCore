#!/usr/bin/env python3
"""Identity-bound Windows USB <-> binary stdio, and a WSL pipe transport.

No commands, resets, listening sockets, drivers or USB assignments are changed.
Only the selected COM handle is opened. Child stdout is exclusively raw bytes;
stderr carries at most a ready record and a final, payload-free status record.
The Windows supervisor closes USB on stdin EOF or its own deadline, including
when its daemon stdout pump is blocked by a stopped Linux consumer.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from serial_session import open_configured_session

PROTOCOL = "meshcore-usb-stdio-1"
MAX_STATUS_BYTES = 4096


def validate_windows_endpoint(endpoint):
    parsed = urlparse(endpoint)
    if parsed.scheme != "winusb" or not re.fullmatch(r"COM[1-9][0-9]{0,3}", parsed.netloc, re.I) \
            or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("use an explicit winusb://COM6 endpoint without other URL fields")
    return parsed


def validate_settings(port, expected_serial, expected_vid, expected_pid, baud, serial_dtr, duration):
    if not re.fullmatch(r"COM[1-9][0-9]{0,3}", port, re.I):
        raise ValueError("invalid selected COM port")
    if not isinstance(expected_serial, str) or not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", expected_serial):
        raise ValueError("an exact nonempty USB serial is required")
    if any(type(value) is not int or not 1 <= value <= 0xffff for value in (expected_vid, expected_pid)):
        raise ValueError("exact USB VID and PID are required")
    if type(baud) is not int or not 1200 <= baud <= 2000000 or type(serial_dtr) is not bool:
        raise ValueError("invalid baud or explicit DTR selection")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError("a finite USB ownership deadline in (0,3600] is required")


def selected_identity(port, expected_serial, expected_vid, expected_pid, inventory):
    matches = [item for item in inventory() if item.device.upper() == port.upper()]
    if len(matches) != 1:
        raise ValueError("selected COM identity is missing or ambiguous")
    item = matches[0]
    identity = dict(port=port.upper(), serial=item.serial_number, vid=item.vid, pid=item.pid)
    if (identity["serial"], identity["vid"], identity["pid"]) != (expected_serial, expected_vid, expected_pid):
        raise ValueError("selected COM USB identity does not match")
    return identity


def open_selected_port(port, expected_serial, expected_vid, expected_pid, *, baud, serial_dtr,
                       inventory, serial_factory):
    """Verify twice; Windows CreateFile/pySerial opens the handle exclusively."""
    before = selected_identity(port, expected_serial, expected_vid, expected_pid, inventory)
    device = serial_factory()
    try:
        device.port, device.baudrate = port.upper(), baud
        device.timeout, device.write_timeout = .05, .5
        device.exclusive = True
        device.xonxoff = device.rtscts = device.dsrdtr = False
        device.dtr, device.rts = serial_dtr, False
        open_configured_session(device)
        after = selected_identity(port, expected_serial, expected_vid, expected_pid, inventory)
        if before != after:
            raise ValueError("selected USB identity changed during open")
        return device, dict(after, baud=baud, serial_dtr=serial_dtr)
    except BaseException:
        device.close()
        raise


def pump_stdio(device, input_fd, output_fd, *, duration, read=os.read, write=os.write,
               now=time.monotonic):
    """Bounded USB ownership; daemon workers cannot hold COM past supervision."""
    stopping = threading.Event()
    counters = {"to_usb": 0, "from_usb": 0}
    reason = ["deadline"]

    def failed(code):
        reason[0] = code
        stopping.set()

    def to_usb():
        try:
            while not stopping.is_set():
                data = read(input_fd, 4096)  # os.read returns short pipe data immediately.
                if not data:
                    failed("stdin_eof")
                    return
                offset = 0
                until = now() + 1
                while offset < len(data) and not stopping.is_set():
                    count = device.write(data[offset:])
                    if not isinstance(count, int) or not 0 <= count <= len(data) - offset:
                        raise IOError("invalid serial write length")
                    if count:
                        offset += count
                        counters["to_usb"] += count
                    elif now() >= until:
                        raise TimeoutError("serial write deadline")
                    else:
                        stopping.wait(.005)
        except Exception:
            failed("input_or_usb_write_error")

    def from_usb():
        try:
            while not stopping.is_set():
                # A short interactive SSH packet must not wait to fill 4096
                # bytes (or pay one serial timeout on every round trip).
                data = device.read(min(device.in_waiting or 1, 4096))
                offset = 0
                while offset < len(data) and not stopping.is_set():
                    count = write(output_fd, data[offset:])
                    if not isinstance(count, int) or not 0 < count <= len(data) - offset:
                        raise IOError("invalid stdout write length")
                    offset += count
                    counters["from_usb"] += count
        except Exception:
            failed("usb_read_or_output_error")

    workers = [threading.Thread(target=target, daemon=True) for target in (to_usb, from_usb)]
    start = now()
    try:
        for worker in workers:
            worker.start()
        while not stopping.wait(min(.05, max(0, duration - (now() - start)))):
            if now() - start >= duration:
                break
    finally:
        stopping.set()
        # The supervisor never joins a potentially blocked stdout writer first.
        for method in ("cancel_read", "cancel_write"):
            try:
                getattr(device, method)()
            except Exception:
                pass
        device.close()
        for worker in workers:
            worker.join(.1)
    return dict(counters, reason=reason[0])


def _ready_record(process, timeout=10):
    captured = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stderr.fileno()], [], [], min(.1, max(0, deadline - time.monotonic())))
        if not ready:
            if process.poll() is not None:
                break
            continue
        chunk = os.read(process.stderr.fileno(), 512)
        if not chunk:
            break
        captured.extend(chunk)
        if len(captured) > MAX_STATUS_BYTES:
            raise RuntimeError("Windows USB status exceeded bounded limit")
        if b"\n" in captured:
            try:
                value = json.loads(captured.split(b"\n", 1)[0])
            except (ValueError, UnicodeError) as error:
                raise RuntimeError("Invalid Windows USB status") from error
            if not isinstance(value, dict) or value.get("state") != "ready" or value.get("protocol") != PROTOCOL:
                raise RuntimeError("Windows USB bridge did not become ready")
            return value
    raise TimeoutError("Windows USB bridge ready deadline")


class WindowsUsbStream:
    """Linux select-compatible separate FDs backed by one owned Windows COM."""
    def __init__(self, endpoint, *, python, expected_serial, expected_vid, expected_pid,
                 baud=115200, serial_dtr=False, duration=300):
        self.process = None
        parsed = validate_windows_endpoint(endpoint)
        port = parsed.netloc.upper()
        validate_settings(port, expected_serial, expected_vid, expected_pid, baud, serial_dtr, duration)
        executable = Path(python or "")
        if not executable.is_absolute() or executable.name.lower() != "python.exe" or not executable.is_file():
            raise ValueError("use an existing absolute WSL path to the trusted Windows python.exe")
        if not sys.platform.startswith("linux"):
            raise ValueError("WindowsUsbStream is a WSL Linux transport")
        script = subprocess.run(["wslpath", "-w", str(Path(__file__).resolve())],
            check=True, capture_output=True, text=True, timeout=5).stdout.strip()
        if not re.fullmatch(r"[A-Za-z]:\\[^\r\n\0]+\.py", script):
            raise ValueError("trusted bridge must reside on an accessible Windows drive")
        command = [str(executable), "-u", script, "--port", port,
            "--expect-serial", expected_serial, "--expect-vid", str(expected_vid),
            "--expect-pid", str(expected_pid), "--baud", str(baud),
            "--serial-dtr", "on" if serial_dtr else "off", "--duration", str(duration)]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0, shell=False)
            record = _ready_record(self.process)
            expected = dict(port=port, serial=expected_serial, vid=expected_vid, pid=expected_pid,
                            baud=baud, serial_dtr=serial_dtr)
            if record.get("identity") != expected:
                raise ValueError("Windows bridge ready identity does not match")
            self.identity = expected
            self.read_fd, self.write_fd = self.process.stdout.fileno(), self.process.stdin.fileno()
            self.fd = self.read_fd  # Compatibility only; relays use separate read/write FDs.
            os.set_blocking(self.read_fd, False)
            os.set_blocking(self.write_fd, False)
        except BaseException:
            self.close()
            raise

    def read(self, count):
        data = os.read(self.read_fd, count)
        if not data:
            raise ConnectionError("Windows USB bridge disconnected")
        return data

    def write(self, data):
        return os.write(self.write_fd, data)

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        # EOF is an independent Windows-side stop signal, not a USB reset.
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        finally:
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()


def add_windows_arguments(parser):
    parser.add_argument("--windows-python", help="absolute WSL path to trusted Windows python.exe for winusb")
    parser.add_argument("--expect-vid", type=lambda value: int(value, 0))
    parser.add_argument("--expect-pid", type=lambda value: int(value, 0))


def validate_windows_options(parsed, *, python, expected_serial, expected_vid, expected_pid,
                             baud, serial_dtr, duration):
    if parsed.scheme == "winusb":
        validate_settings(parsed.netloc.upper(), expected_serial, expected_vid, expected_pid,
                          baud, serial_dtr, duration)
        if not python:
            raise ValueError("winusb requires --windows-python and exact --expect-serial/vid/pid")
    elif python is not None or expected_vid is not None or expected_pid is not None:
        raise ValueError("Windows USB options require winusb://COM6")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--expect-serial", required=True)
    parser.add_argument("--expect-vid", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--expect-pid", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--serial-dtr", choices=("off", "on"), default="off")
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args(argv)
    device = None

    def status(value):
        # Exactly two small records maximum. Never include payloads/exceptions.
        sys.stderr.write(json.dumps(dict(protocol=PROTOCOL, **value), sort_keys=True) + "\n")
        sys.stderr.flush()

    try:
        if os.name != "nt":
            raise ValueError("Windows child only")
        validate_settings(args.port, args.expect_serial, args.expect_vid, args.expect_pid,
                          args.baud, args.serial_dtr == "on", args.duration)
        import msvcrt
        import serial
        from serial.tools.list_ports import comports
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        device, identity = open_selected_port(args.port, args.expect_serial, args.expect_vid, args.expect_pid,
            baud=args.baud, serial_dtr=args.serial_dtr == "on", inventory=comports, serial_factory=serial.Serial)
        status(dict(state="ready", identity=identity))
        stats = pump_stdio(device, sys.stdin.fileno(), sys.stdout.fileno(), duration=args.duration)
        device = None  # pump owns close, even on EOF/deadline.
        status(dict(state="stopped", stats=stats))
        return 0
    except Exception:
        status(dict(state="error", error="USB bridge stopped; check exact identity and exclusive ownership"))
        return 1
    finally:
        if device:
            device.close()


if __name__ == "__main__":
    raise SystemExit(main())
