#!/usr/bin/env python3
"""Isolated Linux TAP <-> selected port-0/1 KISS relay for real Ethernet/TCP/SSH RF tests.

REQUIRES ROOT and an already-created, explicitly named network namespace. Opens
the KISS socket in the host namespace FIRST, then enters --netns and creates a
nonpersistent TAP there. Never changes host LAN, addresses, routes, firewall or
sshd. A TAP vanishes when this process exits. Configure its MTU to 230 and its
address separately with `ip -n NAME ...`; do not connect the namespace by veth.
The KISS socket retains its original network namespace despite setns().

Example (operator must first create eth-a namespace):
  sudo python3 ethernet_tap_relay.py --endpoint tcp://RADIO:5003 \
      --netns /var/run/netns/eth-a --interface lora0

The relay starts no network services. A passing actual SSH session between two
otherwise-isolated namespaces then exercises ARP, TCP handshake, retransmission,
SSH negotiation and payload over the radios, not a synthetic packet imitation.
Use only operator-configured 909.5 MHz / BW500 / SF5 / CR5 low-power lab radios.
"""

from __future__ import annotations

import argparse
from collections import deque
import ctypes
import ipaddress
import json
import math
import os
import re
from pathlib import Path
import select
import signal
import socket
import struct
import sys
import time
from urllib.parse import urlparse

from ethernet_kiss_probe import KissDecoder, kiss_encode
from serial_session import assert_port_idle, open_configured_session
from windows_usb_stdio_bridge import (WindowsUsbStream, add_windows_arguments,
    validate_windows_endpoint, validate_windows_options)


class ModemFlowError(RuntimeError):
    def __init__(self, message, stats):
        super().__init__(message)
        self.stats = stats


class RelayBuffers:
    """Bounded queues; retain partial stream writes and unparsed input bytes."""

    def __init__(self, depth=8, kiss_port=0, *, kiss_modem=False, tx_done_timeout=10,
                 now=time.monotonic):
        if depth <= 0:
            raise ValueError("queue depth must be positive")
        self.depth = depth
        self.to_tap = deque()
        self.to_kiss = deque()
        self.staging = bytearray()
        if not math.isfinite(tx_done_timeout) or not .5 <= tx_done_timeout <= 120:
            raise ValueError("TX_DONE timeout must be .5..120 seconds")
        self.kiss_modem = kiss_modem
        self.tx_done_timeout = tx_done_timeout
        self.now = now
        self.tx_deadline = None
        # Standard port-0 KISS pacing controls only, never RF tuples or keys.
        self.controls = deque((b"\xc0\x01\x00\xc0", b"\xc0\x02\xff\xc0", b"\xc0\x03\x01\xc0") if kiss_modem else ())
        self.decoder = KissDecoder(port=kiss_port, control=self.modem_control if kiss_modem else None)
        self.kiss_port = kiss_port
        self.counters = {"tap_read": 0, "kiss_written": 0, "kiss_read": 0,
                         "tap_written": 0, "bad_frame_length": 0,
                         "tap_read_bytes": 0, "tap_written_bytes": 0,
                         "modem_controls_written": 0, "modem_tx_done": 0,
                         "modem_tx_error": 0, "modem_tx_timeout": 0,
                         "modem_unexpected_tx_done": 0, "modem_rx_meta": 0}

    def can_write_kiss(self):
        return bool(self.controls or self.to_kiss and self.tx_deadline is None)

    def check_tx_done(self):
        if self.tx_deadline is not None and self.now() >= self.tx_deadline:
            self.counters["modem_tx_timeout"] += 1
            raise ModemFlowError("Full KISS modem TX_DONE deadline exceeded; no retry attempted", self.stats())

    def modem_control(self, command, payload):
        if command != 0x06 or not payload:
            return
        if payload[0] == 0xF9:
            self.counters["modem_rx_meta"] += 1
        elif payload[0] == 0xF8:
            if self.tx_deadline is None:
                self.counters["modem_unexpected_tx_done"] += 1
            elif len(payload) != 2 or payload[1] not in (0, 1):
                self.counters["modem_tx_error"] += 1
                raise ModemFlowError("Full KISS modem returned malformed TX_DONE", self.stats())
            elif payload[1] == 0:
                # A completed local submission can lose its RF opportunity
                # (e.g. RX wins the final IRQ race). Drop this one datagram;
                # normal TCP, not a hidden host/firmware ARQ, owns retrying it.
                self.counters["modem_tx_error"] += 1
                self.tx_deadline = None
            else:
                self.counters["modem_tx_done"] += 1
                self.tx_deadline = None
        elif payload[0] == 0xF1 and self.tx_deadline is not None:
            self.counters["modem_tx_error"] += 1
            raise ModemFlowError("Full KISS modem returned a hardware error during DATA", self.stats())

    def can_read_tap(self):
        return len(self.to_kiss) < self.depth

    def can_read_kiss(self):
        return not self.staging and len(self.to_tap) < self.depth

    def queue_tap_frame(self, frame):
        if not self.can_read_tap():
            raise BufferError("TAP queue full; caller must stop reading")
        if not 14 <= len(frame) <= 255:
            self.counters["bad_frame_length"] += 1
            return False
        self.to_kiss.append(kiss_encode(frame, port=self.kiss_port))
        self.counters["tap_read"] += 1
        self.counters["tap_read_bytes"] += len(frame)
        return True

    def feed_kiss(self, data):
        if not self.can_read_kiss() or len(data) > 4096:
            raise BufferError("KISS staging full; caller must stop reading")
        self.staging.extend(data)
        self.parse_pending()

    def parse_pending(self):
        consumed = 0
        for byte in self.staging:
            if len(self.to_tap) >= self.depth:
                break
            consumed += 1
            for frame in self.decoder.feed(bytes((byte,))):
                if not 14 <= len(frame) <= 255:
                    self.counters["bad_frame_length"] += 1
                    continue
                self.to_tap.append(frame)
                self.counters["kiss_read"] += 1
        del self.staging[:consumed]

    def write_kiss(self, write):
        self.check_tx_done()
        if not self.can_write_kiss():
            return
        control = bool(self.controls)
        queue = self.controls if control else self.to_kiss
        pending = queue[0]
        count = write(pending)
        if not 0 <= count <= len(pending):
            raise IOError("invalid KISS write length")
        if count == len(pending):
            queue.popleft()
            if control:
                self.counters["modem_controls_written"] += 1
            else:
                self.counters["kiss_written"] += 1
                if self.kiss_modem:
                    self.tx_deadline = self.now() + self.tx_done_timeout
        elif count:
            queue[0] = pending[count:]

    def write_tap(self, write):
        if not self.to_tap:
            return
        pending = self.to_tap[0]
        if write(pending) != len(pending):
            raise IOError("partial TAP datagram write; refusing to split Ethernet frame")
        self.to_tap.popleft()
        self.counters["tap_written"] += 1
        self.counters["tap_written_bytes"] += len(pending)

    def stats(self):
        return dict(self.counters, decode_errors=self.decoder.errors,
                    pending_to_tap=len(self.to_tap), pending_to_kiss=len(self.to_kiss),
                    staged_kiss_bytes=len(self.staging), awaiting_tx_done=self.tx_deadline is not None)


def validate_endpoint(endpoint, *, strict_tcp=False):
    """Lexical allowlist only: never opens devices, namespaces or sockets."""
    parsed = urlparse(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("KISS endpoint must not contain credentials, query or fragment")
    if parsed.scheme == "tcp":
        if not parsed.hostname or parsed.path not in ("", "/") or parsed.port not in range(1, 65536) and parsed.port is not None:
            raise ValueError("Use an explicit KISS TCP host and port")
        if strict_tcp:
            host = ipaddress.IPv4Address(parsed.hostname)
            if parsed.port != 5003 or host.is_multicast or host.is_unspecified:
                raise ValueError("Use numeric IPv4 tcp://RADIO:5003")
    elif parsed.scheme == "winusb":
        return validate_windows_endpoint(endpoint)
    elif parsed.scheme == "serial":
        path = parsed.path
        if parsed.netloc or not re.fullmatch(r"/dev/(?:tty(?:ACM|USB)[A-Za-z0-9_-]+|serial/(?:by-id|by-path)/[A-Za-z0-9_.:+-]+)", path) \
                or path.rsplit("/", 1)[-1] in (".", ".."):
            raise ValueError("Use an explicit /dev/ttyACM*, ttyUSB*, or serial/by-id|by-path device")
    else:
        raise ValueError("Use tcp://RADIO:5003, serial:///dev/serial/by-id/DEVICE or winusb://COM6")
    return parsed


class KissStream:
    def __init__(self, endpoint, baud, *, serial_dtr=False, windows_python=None,
                 expect_serial=None, expect_vid=None, expect_pid=None, duration=300):
        self.socket = self.serial = self.windows = None
        parsed = validate_endpoint(endpoint)
        validate_windows_options(parsed, python=windows_python, expected_serial=expect_serial,
            expected_vid=expect_vid, expected_pid=expect_pid, baud=baud,
            serial_dtr=serial_dtr, duration=duration)
        if parsed.scheme == "tcp":
            self.socket = socket.create_connection((parsed.hostname, parsed.port or 5003), timeout=5)
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.socket.setblocking(False)
            self.fd = self.socket.fileno()
        elif parsed.scheme == "serial":
            if not re.fullmatch(r"/dev/tty(?:ACM|USB)[A-Za-z0-9_-]+", os.path.realpath(parsed.path)):
                raise ValueError("Selected serial alias must resolve to a ttyACM/ttyUSB device")
            import serial
            self.serial = serial.Serial()
            self.serial.port = parsed.netloc + parsed.path
            self.serial.baudrate = baud
            self.serial.timeout = self.serial.write_timeout = 0
            self.serial.exclusive = True
            self.serial.dtr = serial_dtr
            self.serial.rts = False
            assert_port_idle(self.serial.port)
            open_configured_session(self.serial)
            self.fd = self.serial.fileno()
        elif parsed.scheme == "winusb":
            self.windows = WindowsUsbStream(endpoint, python=windows_python, expected_serial=expect_serial,
                expected_vid=expect_vid, expected_pid=expect_pid, baud=baud, serial_dtr=serial_dtr,
                duration=duration)
            self.fd = self.windows.read_fd
        self.read_fd = self.windows.read_fd if self.windows else self.fd
        self.write_fd = self.windows.write_fd if self.windows else self.fd

    def read(self, count):
        if self.windows:
            return self.windows.read(count)
        data = self.socket.recv(count) if self.socket else os.read(self.fd, count)
        if not data:
            raise ConnectionError("KISS endpoint disconnected")
        return data

    def write(self, data):
        if self.windows:
            return self.windows.write(data)
        return self.socket.send(data) if self.socket else os.write(self.fd, data)

    def close(self):
        if self.socket:
            self.socket.close()
        if self.serial:
            self.serial.close()
        if self.windows:
            self.windows.close()


def enter_network_namespace(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        target = os.fstat(descriptor)
        current = os.stat("/proc/self/ns/net")
        if (target.st_dev, target.st_ino) == (current.st_dev, current.st_ino):
            raise ValueError("Refusing to create a test TAP in the current host network namespace")
        if hasattr(os, "setns"):
            os.setns(descriptor, 0x40000000)  # CLONE_NEWNET
        else:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.setns.argtypes = (ctypes.c_int, ctypes.c_int)
            libc.setns.restype = ctypes.c_int
            if libc.setns(descriptor, 0x40000000) != 0:
                failure = ctypes.get_errno()
                raise OSError(failure, os.strerror(failure), path)
    finally:
        os.close(descriptor)


def create_tap(name):
    import fcntl
    encoded = name.encode("ascii")
    if not encoded or len(encoded) > 15 or b"\0" in encoded:
        raise ValueError("TAP interface name must contain 1..15 ASCII characters")
    descriptor = os.open("/dev/net/tun", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        request = struct.pack("16sH", encoded, 0x0002 | 0x1000)  # IFF_TAP | IFF_NO_PI
        result = fcntl.ioctl(descriptor, 0x400454CA, request)  # TUNSETIFF
        return descriptor, result[:16].split(b"\0", 1)[0].decode("ascii")
    except BaseException:
        os.close(descriptor)
        raise


def open_transport_then_tap(endpoint, baud, netns, name, *, serial_dtr=False, **transport_options):
    """Ordering is intentional: socket routes must remain in host namespace."""
    stream = KissStream(endpoint, baud, serial_dtr=serial_dtr, **transport_options)
    try:
        enter_network_namespace(netns)
        tap_fd, actual_name = create_tap(name)
        return stream, tap_fd, actual_name
    except BaseException:
        stream.close()
        raise


def emit(value, status_file=None):
    text = json.dumps(value, sort_keys=True)
    print(text, flush=True)
    if status_file:
        # Evidence only; no system configuration is written by this helper.
        target = Path(status_file)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text + "\n", encoding="utf-8")
        os.replace(temporary, target)


def relay(stream, tap_fd, *, duration=0, queue_depth=8, kiss_port=0, kiss_modem=False,
          tx_done_timeout=10, stop=None):
    buffers = RelayBuffers(queue_depth, kiss_port=kiss_port, kiss_modem=kiss_modem,
                           tx_done_timeout=tx_done_timeout)
    start = time.monotonic()
    while not (stop and stop()) and (not duration or time.monotonic() - start < duration):
        buffers.check_tx_done()
        buffers.parse_pending()
        reads = ([tap_fd] if buffers.can_read_tap() else [])
        if buffers.can_read_kiss():
            reads.append(stream.read_fd)
        writes = ([tap_fd] if buffers.to_tap else [])
        if buffers.can_write_kiss():
            writes.append(stream.write_fd)
        ready_read, ready_write, _ = select.select(reads, writes, [], 0.2)
        for descriptor in ready_write:
            try:
                if descriptor == tap_fd:
                    buffers.write_tap(lambda data: os.write(tap_fd, data))
                else:
                    buffers.write_kiss(stream.write)
            except BlockingIOError:
                pass
        for descriptor in ready_read:
            try:
                if descriptor == tap_fd:
                    # A too-big datagram is discarded, never sent as a truncated frame.
                    frame = os.read(tap_fd, 65536)
                    buffers.queue_tap_frame(frame)
                else:
                    buffers.feed_kiss(stream.read(4096))
            except BlockingIOError:
                pass
    return dict(buffers.stats(), elapsed_seconds=round(time.monotonic() - start, 3))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--netns", required=True, help="existing namespace handle, e.g. /var/run/netns/eth-a")
    parser.add_argument("--interface", default="lora0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--kiss-port", type=int, choices=(0, 1), default=0)
    parser.add_argument("--serial-dtr", choices=("off", "on"), default="off",
                        help="explicitly assert native USB CDC DTR; never sends a command")
    parser.add_argument("--expect-serial", help="exact USB identity required for winusb")
    add_windows_arguments(parser)
    parser.add_argument("--kiss-modem", action="store_true",
                        help="explicit full serial modem: set standard pacing, await each TX_DONE")
    parser.add_argument("--tx-done-timeout", type=float, default=10)
    parser.add_argument("--queue-depth", type=int, default=8)
    parser.add_argument("--duration", type=float, default=0, help="seconds; zero means until terminated")
    parser.add_argument("--status-file", help="atomically updated ready/final JSON evidence")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        parser.error("the TAP relay requires Linux")
    if args.queue_depth <= 0 or args.queue_depth > 256 or args.duration < 0:
        parser.error("queue-depth must be 1..256 and duration must be nonnegative")
    if not 1200 <= args.baud <= 2000000:
        parser.error("baud must be 1200..2000000")
    try:
        parsed = validate_endpoint(args.endpoint)
        if args.kiss_modem and parsed.scheme not in ("serial", "winusb"):
            raise ValueError("--kiss-modem is explicit full serial-modem mode only")
        validate_windows_options(parsed, python=args.windows_python, expected_serial=args.expect_serial,
            expected_vid=args.expect_vid, expected_pid=args.expect_pid, baud=args.baud,
            serial_dtr=args.serial_dtr == "on", duration=args.duration)
        if not math.isfinite(args.tx_done_timeout) or not .5 <= args.tx_done_timeout <= 120:
            raise ValueError("TX_DONE timeout must be .5..120 seconds")
    except ValueError as error:
        parser.error(str(error))
    stopping = False

    def stop_signal(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)
    stream, tap_fd, name = open_transport_then_tap(args.endpoint, args.baud, args.netns, args.interface,
        serial_dtr=args.serial_dtr == "on", windows_python=args.windows_python,
        expect_serial=args.expect_serial, expect_vid=args.expect_vid, expect_pid=args.expect_pid,
        duration=args.duration)
    try:
        emit({"state": "ready", "endpoint": args.endpoint, "netns": args.netns,
              "interface": name, "configure_mtu": 230, "queue_depth": args.queue_depth,
              "kiss_port": args.kiss_port, "serial_dtr": args.serial_dtr,
              "kiss_modem": args.kiss_modem}, args.status_file)
        stats = relay(stream, tap_fd, duration=args.duration, queue_depth=args.queue_depth, kiss_port=args.kiss_port,
                      kiss_modem=args.kiss_modem, tx_done_timeout=args.tx_done_timeout,
                      stop=lambda: stopping)
        emit({"state": "stopped", "interface": name, "stats": stats}, args.status_file)
        return 0
    except Exception as error:
        emit({"state": "error", "interface": name, "error": str(error),
              "stats": getattr(error, "stats", None)}, args.status_file)
        return 1
    finally:
        os.close(tap_fd)
        stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
