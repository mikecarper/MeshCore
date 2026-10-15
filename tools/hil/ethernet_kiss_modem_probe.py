#!/usr/bin/env python3
"""Configure an identity-bound full KISS modem for the Ethernet RF bench.

NO RF DATA is sent. A compiled-in 909.5 MHz TX lock must be proved over the
HIL-only read-only command before any setter. R1 listens on the USA profile;
R2 uses raw Ethernet at 909.5/SF5/CR5, selected BW125/500 and preamble32..1024
(defaults BW500/preamble512). Settings are session-only.
The USB port is closed before exit so a separate TAP/KISS relay may own it.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
import struct
import time

from serial_session import assert_port_idle, open_configured_session


FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
GUARD_HZ = 909_500_000
PRIMARY = (910_525_000, 62_500, 7, 5)
SECONDARY = (GUARD_HZ, 500_000, 5, 5, 2, 512, 2)
PREAMBLES = (32, 64, 96, 128, 192, 256, 384, 512, 768, 1024)
PRIMARY_STRUCT = struct.Struct("<IIBB")
SECONDARY_STRUCT = struct.Struct("<IIBBBHB")
MAX_FRAME = 514


class ProtocolError(RuntimeError):
    pass


def encode_frame(command: int, payload: bytes) -> bytes:
    if type(command) is not int or not 0 <= command <= 0x0F:
        raise ValueError("Only port-zero KISS controls are permitted")
    if command == 0:
        raise ValueError("This configuration tool never sends RF DATA")
    body = bytes((command,)) + payload
    if len(body) > MAX_FRAME:
        raise ValueError("KISS control exceeds the modem frame limit")
    return bytes((FEND,)) + body.replace(b"\xdb", b"\xdb\xdd").replace(
        b"\xc0", b"\xdb\xdc") + bytes((FEND,))


class Decoder:
    """Bounded full-frame decoder, including command byte and hardware replies."""

    def __init__(self):
        self.started = self.escaped = False
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        result = []
        for value in data:
            if value == FEND:
                if self.escaped:
                    raise ProtocolError("Truncated KISS escape")
                if self.started and self.buffer:
                    result.append(bytes(self.buffer))
                self.buffer.clear()
                self.started = True
            elif not self.started:
                continue  # Startup console text is not a KISS frame.
            elif self.escaped:
                self.escaped = False
                if value not in (TFEND, TFESC):
                    raise ProtocolError("Invalid KISS escape")
                self.buffer.append(FEND if value == TFEND else FESC)
            elif value == FESC:
                self.escaped = True
            else:
                self.buffer.append(value)
            if len(self.buffer) > MAX_FRAME:
                raise ProtocolError("Oversized KISS frame")
        return result


class ControlLink:
    def __init__(self, port, *, timeout=3.0, clock=time.monotonic):
        if not 0 < timeout <= 10:
            raise ValueError("Use a transaction timeout of at most ten seconds")
        self.port, self.timeout, self.clock = port, timeout, clock
        self.decoder = Decoder()
        self.pending = deque()
        self.ignored_data_frames = 0
        self.rx_metadata_frames = 0

    def control(self, command: int, payload: bytes):
        frame = encode_frame(command, payload)
        if self.port.write(frame) != len(frame):
            raise ProtocolError("Short serial write")
        self.port.flush()

    def request(self, command: int, payload=b"", *, size: int, setter=False) -> bytes:
        self.control(6, bytes((command,)) + payload)
        expected = 0xF0 if setter else command | 0x80
        deadline = self.clock() + self.timeout
        byte_count = frame_count = 0
        while self.clock() < deadline:
            if not self.pending:
                chunk = self.port.read(1024)
                byte_count += len(chunk)
                if byte_count > 65536:
                    raise ProtocolError("KISS transaction exceeded its byte limit")
                self.pending.extend(self.decoder.feed(chunk))
            while self.pending:
                frame = self.pending.popleft()
                frame_count += 1
                if frame_count > 256:
                    raise ProtocolError("KISS transaction exceeded its frame limit")
                if frame[0] in (0x00, 0x10):
                    self.ignored_data_frames += 1
                    continue
                if frame[0] != 6 or len(frame) < 2:
                    raise ProtocolError("Unexpected KISS control port or frame")
                response, body = frame[1], frame[2:]
                if response == 0xF1:
                    if len(body) != 1:
                        raise ProtocolError("Malformed hardware error")
                    raise ProtocolError(f"Hardware command 0x{command:02x} failed: 0x{body[0]:02x}")
                if response == 0xF8:
                    if len(body) != 1 or body[0] not in (0, 1):
                        raise ProtocolError("Malformed TX_DONE notification")
                    raise ProtocolError("Unexpected TX_DONE: setup never sends RF DATA")
                if response == 0xF9:
                    if len(body) != 2:
                        raise ProtocolError("Malformed RX metadata")
                    self.rx_metadata_frames += 1
                    continue
                if response != expected or len(body) != size:
                    raise ProtocolError(f"Unexpected response to hardware command 0x{command:02x}")
                return body
        raise ProtocolError(f"Timed out waiting for hardware command 0x{command:02x}")


def _stats(link: ControlLink) -> dict:
    rx, tx, errors = struct.unpack("<III", link.request(0x12, size=12))
    return {"rx": rx, "tx": tx, "errors": errors}


def _primary(link: ControlLink) -> dict:
    observed = PRIMARY_STRUCT.unpack(link.request(0x0B, size=PRIMARY_STRUCT.size))
    # The driver stores MHz as float32; 910.525 becomes 910525024 whole Hz.
    # No tolerance is allowed for bandwidth/SF/CR or the separate TX guard.
    if abs(observed[0] - PRIMARY[0]) > 50 or observed[1:] != PRIMARY[1:]:
        raise ProtocolError("Primary USA RX-profile readback does not match")
    return dict(zip(("frequency_hz", "bandwidth_hz", "sf", "cr"), observed))


def secondary_tuple(*, bandwidth=500, preamble=512):
    if type(bandwidth) is not int or bandwidth not in (125, 500):
        raise ValueError("Select bandwidth 125 or 500 kHz")
    if type(preamble) is not int or preamble not in PREAMBLES:
        raise ValueError("Select an explicit supported preamble from 32..1024 symbols")
    return (GUARD_HZ, bandwidth * 1000, 5, 5, 2, preamble, 2)


def configure_modem(link: ControlLink, *, bandwidth=500, preamble=512) -> dict:
    expected_secondary = secondary_tuple(bandwidth=bandwidth, preamble=preamble)
    guard = struct.unpack("<I", link.request(0x21, size=4))[0]
    if guard != GUARD_HZ:
        raise ProtocolError("Required compiled-in 909.5 MHz TX lock is absent")
    version = link.request(0x11, size=2)
    if version != b"\x02\x00":
        raise ProtocolError("Expected full MeshCore KISS protocol version 2.0")
    public_key = link.request(0x01, size=32)
    link.request(0x17, size=0)  # Standard OpenHop liveness handshake.
    before = _stats(link)
    link.request(0x0A, b"\0", size=0, setter=True)
    link.request(0x09, PRIMARY_STRUCT.pack(*PRIMARY), size=0, setter=True)
    link.request(0x1F, SECONDARY_STRUCT.pack(*expected_secondary), size=0, setter=True)
    secondary = SECONDARY_STRUCT.unpack(link.request(0x20, size=SECONDARY_STRUCT.size))
    if secondary != expected_secondary:
        raise ProtocolError("Secondary Ethernet-profile readback does not match")
    primary = _primary(link)
    power = struct.unpack("b", link.request(0x0C, size=1))[0]
    if power != 0:
        raise ProtocolError("TX power readback is not zero dBm")
    # Standard KISS controls do not return hardware acknowledgements.
    link.control(1, b"\0")
    link.control(2, b"\xff")
    if struct.unpack("<I", link.request(0x21, size=4))[0] != GUARD_HZ:
        raise ProtocolError("Compiled TX lock changed during configuration")
    after = _stats(link)
    if after["tx"] != before["tx"]:
        raise ProtocolError("TX counter changed during no-RF configuration")
    return {"success": True, "rf_data_sent": 0, "tx_guard_hz": guard,
            "version": {"major": version[0], "minor": version[1]},
            "public_identity_sha256": hashlib.sha256(public_key).hexdigest(),
            "primary_rx_only": primary,
            "radio2": dict(zip(("frequency_hz", "bandwidth_hz", "sf", "cr", "mode",
                                "preamble", "protocol"), secondary)),
            "tx_power_dbm": power, "stats_before": before, "stats_after": after,
            "ignored_rx_data_frames": link.ignored_data_frames,
            "rx_metadata_frames": link.rx_metadata_frames}


def open_verified_port(path: str, expected_serial: str):
    import serial
    from serial.tools import list_ports

    if not expected_serial or any(c in expected_serial for c in "\r\n\0"):
        raise ValueError("An exact stable USB serial is required")
    resolved = os.path.normcase(os.path.realpath(path))
    matches = [p for p in list_ports.comports()
               if os.path.normcase(os.path.realpath(p.device)) == resolved]
    if len(matches) != 1 or (matches[0].serial_number or "").upper() != expected_serial.upper():
        raise ProtocolError("Selected port does not match the expected USB serial")
    info = matches[0]
    assert_port_idle(info.device)
    port = serial.Serial(port=None, baudrate=115200, timeout=0.05,
                         write_timeout=0.5, exclusive=True)
    # Keep a caller's stable by-id path through the actual open, rather than
    # converting it to a transient ttyACM number that another board may reuse.
    port.port = path
    port.rts = False
    port.dtr = info.vid in (0x239A, 0x2886)
    try:
        open_configured_session(port)
        current = [p for p in list_ports.comports()
                   if os.path.normcase(os.path.realpath(p.device)) ==
                   os.path.normcase(os.path.realpath(path))]
        if len(current) != 1 or (current[0].serial_number or "").upper() != expected_serial.upper():
            raise ProtocolError("USB identity changed while opening the selected port")
    except BaseException:
        port.close()
        raise
    return port, info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--expect-serial", required=True)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--bandwidth", type=int, choices=(125, 500), default=500)
    parser.add_argument("--preamble", type=int, choices=PREAMBLES, default=512)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 10:
        parser.error("--timeout must be positive and at most ten seconds")
    port = None
    try:
        port, info = open_verified_port(args.port, args.expect_serial)
        report = configure_modem(ControlLink(port, timeout=args.timeout),
                                 bandwidth=args.bandwidth, preamble=args.preamble)
        report["usb"] = {"serial": info.serial_number, "port": info.device,
                         "vid": info.vid, "pid": info.pid}
    except (RuntimeError, OSError, ValueError) as error:
        report = {"success": False, "rf_data_sent": 0, "error": str(error)}
    finally:
        if port is not None:
            port.close()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
