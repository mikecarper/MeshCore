#!/usr/bin/env python3
"""Byte-exact Ethernet-over-KISS RF probe; no flashing or radio configuration.

TRANSMITS RF through the two explicitly selected KISS endpoints. Configure the
isolated lab radios first: 909.5 MHz / 500 kHz / SF5 / CR5, low TX power. This
script never changes those settings, opens Companion port 5000, or creates TAPs.

Examples (TCP port forwards may point at another computer or the Mercerwood Pi):
  python3 ethernet_kiss_probe.py --a tcp://192.168.4.1:5003 --b tcp://127.0.0.1:15003
  python3 ethernet_kiss_probe.py --a tcp://192.168.4.1:5003 --b serial:///dev/ttyUSB0
  python3 ethernet_kiss_probe.py --plan

Works with Companion KISS-over-TCP and a stock port-0 KISS raw-Ethernet peer.
Checksums in generated ARP/IPv4/UDP/TCP/ICMP frames are real. Passing synthetic
TCP frames is NOT a TCP connection or an SSH test; use Linux TAP/tncattach and a
real SSH session separately. --duplex intentionally overlaps half-duplex RF TX
and is a stress test, not the baseline. No automatic retries conceal RF loss.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import queue
import socket
import struct
import threading
import time
from urllib.parse import urlparse

from serial_session import assert_port_idle, open_configured_session


FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!" + "H" * (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def kiss_encode(frame: bytes, *, port: int = 0) -> bytes:
    if type(port) is not int or port not in (0, 1):
        raise ValueError("Select KISS data port 0 or 1")
    return bytes((FEND, port << 4)) + frame.replace(b"\xdb", b"\xdb\xdd").replace(
        b"\xc0", b"\xdb\xdc") + bytes((FEND,))


class KissDecoder:
    """Bounded streaming decoder: ignore control frames and reject bad escapes."""

    def __init__(self, port: int = 0, control=None):
        if type(port) is not int or port not in (0, 1):
            raise ValueError("Select KISS data port 0 or 1")
        self.port = port
        self.control = control
        self.buffer = bytearray()
        self.started = self.escaped = self.discard = False
        self.errors = 0

    def feed(self, data: bytes) -> list[bytes]:
        frames = []
        for byte in data:
            if byte == FEND:
                if self.started and self.buffer and not self.discard:
                    if self.escaped:
                        self.errors += 1
                    elif self.buffer[0] == self.port << 4:
                        frames.append(bytes(self.buffer[1:]))
                    elif self.buffer[0] & 15 and self.control is not None:
                        self.control(self.buffer[0], bytes(self.buffer[1:]))
                self.buffer.clear()
                self.started = True
                self.escaped = self.discard = False
            elif not self.started or self.discard:
                continue
            elif self.escaped:
                self.escaped = False
                if byte not in (TFEND, TFESC):
                    self.errors += 1
                    self.discard = True
                else:
                    self.buffer.append(FEND if byte == TFEND else FESC)
            elif byte == FESC:
                self.escaped = True
            else:
                self.buffer.append(byte)
            if len(self.buffer) > 256:
                self.errors += 1
                self.discard = True
        return frames


def make_frame(kind: str, size: int, sequence: int, reverse: bool = False) -> bytes:
    """Produce independently identifiable, valid network test frames up to 255B."""
    source = bytes.fromhex("0200c0db0001")
    destination = bytes.fromhex("0200c0db0002")
    src_ip = ipaddress.ip_address("192.0.2.1").packed
    dst_ip = ipaddress.ip_address("192.0.2.2").packed
    if reverse:
        source, destination = destination, source
        src_ip, dst_ip = dst_ip, src_ip
    if kind == "arp":
        # Vary sender protocol address to distinguish every repeated ARP probe.
        src_ip = bytes((192, 0, (sequence >> 8) & 255, sequence & 255))
        arp = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
        arp += source + src_ip + b"\0" * 6 + dst_ip
        return b"\xff" * 6 + source + b"\x08\x06" + arp + b"\0" * max(0, size - 42)
    minimum = {"raw": 14, "udp": 42, "icmp": 42, "tcp": 54}[kind]
    if not minimum <= size <= 255:
        raise ValueError(f"{kind} frame requires {minimum}..255 bytes")
    payload_size = size - minimum
    seed = b"ETHK" + struct.pack("!I?", sequence, reverse) + bytes((FEND, FESC))
    payload = (seed + bytes(range(256))) * ((payload_size // 267) + 1)
    payload = payload[:payload_size]
    if kind == "raw":
        # Even a minimum 14-byte frame must be unique across repetitions:
        # otherwise a delayed duplicate could impersonate a later probe.
        source = source[:2] + struct.pack("!I", sequence)
        return destination + source + b"\x88\xb5" + payload
    protocol = {"udp": 17, "tcp": 6, "icmp": 1}[kind]
    if kind == "udp":
        transport = struct.pack("!HHHH", 49152, 22, 8 + len(payload), 0) + payload
    elif kind == "tcp":
        transport = struct.pack("!HHIIHHHH", 49152, 22, sequence, 0, 0x5002, 4096, 0, 0) + payload
    else:
        transport = struct.pack("!BBHHH", 8, 0, 0, 0xC0DB, sequence & 0xFFFF) + payload
    pseudo = src_ip + dst_ip + struct.pack("!BBH", 0, protocol, len(transport))
    check = checksum(transport if kind == "icmp" else pseudo + transport)
    offset = {"udp": 6, "tcp": 16, "icmp": 2}[kind]
    # UDP checksum zero means disabled; wire representation of computed zero is FFFF.
    if kind == "udp" and not check:
        check = 0xFFFF
    transport = transport[:offset] + struct.pack("!H", check) + transport[offset + 2:]
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(transport), sequence & 0xFFFF,
                         0x4000, 64, protocol, 0, src_ip, dst_ip)
    header = header[:10] + struct.pack("!H", checksum(header)) + header[12:]
    return destination + source + b"\x08\x00" + header + transport


CASES = (("raw", 14), ("arp", 60), ("icmp", 60), ("udp", 64), ("tcp", 74),
         ("udp", 244), ("raw", 255), ("tcp", 255))


class KissEndpoint:
    def __init__(self, url: str, baud: int = 115200):
        self.url = url
        self.decoder = KissDecoder()
        self.received = queue.Queue(maxsize=256)
        self.overflow = 0
        self.error = None
        self.closed = threading.Event()
        self.serial = None
        self.socket = None
        parsed = urlparse(url)
        if parsed.scheme == "tcp":
            self.socket = socket.create_connection((parsed.hostname, parsed.port or 5003), timeout=5)
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.socket.settimeout(0.1)
        elif parsed.scheme == "serial":
            import serial  # Optional: pure TCP/--plan needs only Python stdlib.
            port = (parsed.netloc + parsed.path) if parsed.netloc else parsed.path
            if port.startswith("/COM"):
                port = port[1:]
            self.serial = serial.Serial()
            self.serial.baudrate = baud
            self.serial.timeout = self.serial.write_timeout = 0.1
            self.serial.dtr = self.serial.rts = False
            self.serial.port = port
            assert_port_idle(port)
            open_configured_session(self.serial)
        else:
            raise ValueError("endpoint must be tcp://host:port or serial:///device")
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                try:
                    data = self.socket.recv(1024) if self.socket else self.serial.read(1024)
                except socket.timeout:
                    continue
                if not data and self.socket:
                    raise ConnectionError("KISS TCP peer disconnected")
                for frame in self.decoder.feed(data):
                    try:
                        self.received.put_nowait((frame, time.monotonic()))
                    except queue.Full:
                        self.overflow += 1
        except Exception as error:
            if not self.closed.is_set():
                self.error = str(error)

    def send(self, frame: bytes, fragment: int = 0):
        encoded = kiss_encode(frame)
        step = fragment or len(encoded)
        for at in range(0, len(encoded), step):
            chunk = encoded[at:at + step]
            if self.socket:
                self.socket.sendall(chunk)
            elif self.serial.write(chunk) != len(chunk):
                raise IOError("short serial write")

    def receive(self, timeout: float):
        if self.error:
            raise ConnectionError(self.error)
        try:
            return self.received.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self.closed.set()
        if self.socket:
            self.socket.close()
        if self.serial:
            self.serial.close()
        self.thread.join(timeout=1)


def run_probe(a, b, *, count=3, timeout=3.0, interval=0.05, duplex=False, fragment=0):
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "baseline": "909.5 MHz / 500 kHz / SF5 / CR5 (operator configured; not verified)",
              "duplex_stress": duplex, "retries": 0, "transfers": [], "unexpected": []}
    endpoints = {"a": a, "b": b}
    sequence = 0
    for repetition in range(count):
        for kind, size in CASES:
            pending = []
            for source, destination in (("a", "b"), ("b", "a")):
                sequence += 1
                frame = make_frame(kind, size, sequence, source == "b")
                item = {"source": source, "destination": destination, "repetition": repetition,
                        "kind": kind, "size": len(frame), "sequence": sequence,
                        "sha256": hashlib.sha256(frame).hexdigest(), "received": False,
                        "sent_hex": frame.hex()}
                sent = time.monotonic()
                endpoints[source].send(frame, fragment)
                pending.append((item, frame, sent))
                if not duplex:
                    _collect(endpoints, pending, timeout, report)
                    pending = []
                    time.sleep(interval)
            if pending:
                _collect(endpoints, pending, timeout, report)
                time.sleep(interval)
    counts = Counter()
    for item in report["transfers"]:
        direction = item["source"] + "_to_" + item["destination"]
        counts[direction + "_attempted"] += 1
        counts[direction + "_received"] += bool(item["received"])
    report["counts"] = dict(counts)
    report["passed"] = all(item["received"] for item in report["transfers"])
    report["note"] = "Exact Ethernet bytes verified; this is not an established TCP/SSH session."
    return report


def _collect(endpoints, pending, timeout, report):
    deadline = max(item[2] for item in pending) + timeout
    while any(not item[0]["received"] for item in pending) and time.monotonic() < deadline:
        for name, endpoint in endpoints.items():
            received = endpoint.receive(min(0.01, max(0, deadline - time.monotonic())))
            if received is None:
                continue
            frame, timestamp = received
            match = next((item for item in pending
                          if item[0]["destination"] == name and item[1] == frame
                          and not item[0]["received"]), None)
            if match:
                match[0].update(received=True, received_hex=frame.hex(),
                                latency_ms=round((timestamp - match[2]) * 1000, 3))
            else:
                report["unexpected"].append({"at": name, "size": len(frame), "hex": frame.hex()})
    report["transfers"].extend(item[0] for item in pending)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", help="first KISS TCP or serial endpoint")
    parser.add_argument("--b", help="second KISS TCP or serial endpoint")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--count", type=int, default=3, help="repetitions per frame type and direction")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--fragment", type=int, default=0, help="split host KISS writes into N-byte chunks")
    parser.add_argument("--duplex", action="store_true", help="simultaneous TX collision stress test")
    parser.add_argument("--report", help="save complete byte-exact JSON evidence")
    parser.add_argument("--plan", action="store_true", help="show cases without opening any endpoint")
    args = parser.parse_args(argv)
    if args.count <= 0 or args.timeout <= 0 or args.interval < 0 or args.fragment < 0:
        parser.error("count/timeout must be positive; interval/fragment must be nonnegative")
    if args.plan:
        print(json.dumps({"cases": CASES, "frames_each_direction": args.count * len(CASES),
                          "radio_settings": "909.5 MHz / 500 kHz / SF5 / CR5; manually verified"}, indent=2))
        return 0
    if not args.a or not args.b:
        parser.error("both --a and --b are required, unless using --plan")
    if args.a == args.b:
        parser.error("--a and --b must be different endpoints")
    a = b = None
    try:
        a = KissEndpoint(args.a, args.baud)
        b = KissEndpoint(args.b, args.baud)
        result = run_probe(a, b, count=args.count, timeout=args.timeout, interval=args.interval,
                           duplex=args.duplex, fragment=args.fragment)
        result["endpoints"] = {"a": args.a, "b": args.b}
        result["host_diagnostics"] = {
            name: {"decode_errors": endpoint.decoder.errors, "queue_overflows": endpoint.overflow}
            for name, endpoint in (("a", a), ("b", b))}
        result["passed"] = result["passed"] and not any(
            value for diag in result["host_diagnostics"].values() for value in diag.values())
        output = json.dumps(result, indent=2)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as destination:
                destination.write(output + "\n")
        print(output)
        return 0 if result["passed"] else 1
    finally:
        if b:
            b.close()
        if a:
            a.close()


if __name__ == "__main__":
    raise SystemExit(main())
