#!/usr/bin/env python3
"""Point IPv4 TUN <-> existing MeshCore Companion RAW_CUSTOM API (cmd25/push84).

Uses one explicit TCP5000 or identity-bound USB Companion session, NOT KISS or Ethernet. The radio's
tx.ssh policy and MeshCore radio2 profiles must be configured by the operator;
this helper never edits radio settings, channels, keys or the host network.
Connects the socket before entering an existing isolated namespace, where its
own nonpersistent TUN receives only an explicit peer /32 route. SSH is encrypted
by the hosts, not by this public RAW_CUSTOM envelope. Command OK means accepted
by the Companion, never proof that RF delivered a packet.
"""
from __future__ import annotations

import argparse
from collections import deque
import ipaddress
import math
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time

from ethernet_tap_relay import emit, enter_network_namespace, validate_endpoint
from serial_session import assert_port_idle, open_configured_session
from windows_usb_stdio_bridge import WindowsUsbStream, add_windows_arguments, validate_windows_options

FRAME_SIZE = 172
RESPONSE_FRAME_SIZE = 176  # Actual firmware MAX_FRAME_SIZE; does not enlarge outgoing/IP budgets.
ENVELOPE_SIZE = 12
MAGIC = b"SSH\x01"
MINIMUM_MTU = 94


class MeshPath:
    def __init__(self, data=b"", width=1):
        self.data = bytes(data)
        self.width = width
        if type(width) is not int or not 1 <= width <= 3 or len(self.data) > 64 \
                or len(self.data) % width or len(self.data) // width > 63:
            raise ValueError("path requires width1..3, count0..63 and at most64 bytes")

    @classmethod
    def from_hex(cls, text, width=1):
        if not re.fullmatch(r"(?:[a-fA-F0-9]{2})*", text):
            raise ValueError("path must contain complete hexadecimal bytes without separators")
        return cls(bytes.fromhex(text), width)

    @property
    def encoded(self):
        return ((self.width - 1) << 6) | (len(self.data) // self.width)


def mtu_budget(forward_bytes, return_bytes, requested=None, frame_size=FRAME_SIZE, transport_bytes=0):
    if any(type(value) is not int or not 0 <= value <= 64 for value in (forward_bytes, return_bytes)) \
            or not FRAME_SIZE <= frame_size <= 176 or transport_bytes not in (0, 4) \
            or (requested is not None and (type(requested) is not int or requested < MINIMUM_MTU)):
        raise ValueError("invalid path/frame/MTU budget; minimum SSH MTU94")
    path_bytes = max(forward_bytes, return_bytes)
    maximum = min(184, frame_size - 2 - path_bytes, frame_size - 4,
                  255 - 2 - path_bytes - transport_bytes) - ENVELOPE_SIZE
    return maximum, min(requested if requested is not None else maximum, maximum)


def private_host(text):
    address = ipaddress.IPv4Address(text)
    if str(address) != text or not (address in ipaddress.IPv4Network("10.0.0.0/8")
            or address in ipaddress.IPv4Network("172.16.0.0/12")
            or address in ipaddress.IPv4Network("192.168.0.0/16")) or address.packed[-1] in (0, 255):
        raise ValueError("use explicit private IPv4 host addresses")
    return address.packed


def valid_ipv4(packet, source, destination, mtu):
    if not 20 <= len(packet) <= mtu or packet[0] >> 4 != 4:
        return False
    header = (packet[0] & 15) * 4
    length = int.from_bytes(packet[2:4], "big")
    if not 20 <= header <= 60 or length < header or length != len(packet) \
            or int.from_bytes(packet[6:8], "big") & 0xbfff \
            or packet[12:16] != source or packet[16:20] != destination:
        return False
    checksum = sum(struct.unpack("!" + "H" * (header // 2), packet[:header]))
    while checksum >> 16:
        checksum = (checksum & 0xffff) + (checksum >> 16)
    if checksum != 0xffff:
        return False
    if packet[9] == 1:
        return length >= header + 8
    return packet[9] == 6 and length >= header + 20 and 20 <= (packet[header + 12] >> 4) * 4 <= length - header


class IpCodec:
    def __init__(self, local, remote, path, return_bytes, *, mtu=None, session=None, sequence=0):
        self.local, self.remote = private_host(local), private_host(remote)
        if self.local == self.remote:
            raise ValueError("point IPv4 peers must differ")
        self.path = path
        self.maximum_mtu, self.mtu = mtu_budget(len(path.data), return_bytes, mtu)
        self.session = secrets.randbits(32) if session is None else session
        self.sequence = sequence
        if any(type(value) is not int or not 0 <= value <= 0xffffffff for value in (self.session, sequence)):
            raise ValueError("session/sequence must be unsigned32-bit integers")

    def command(self, packet):
        if self.sequence > 0xffffffff:
            raise OverflowError("sequence exhausted; start a new session")
        if not valid_ipv4(packet, self.local, self.remote, self.mtu):
            raise ValueError("invalid, fragmented or oversized point IPv4 packet")
        envelope = MAGIC + struct.pack("<II", self.session, self.sequence) + packet
        self.sequence += 1
        return bytes((25, self.path.encoded)) + self.path.data + envelope

    def raw_push(self, frame):
        if not 4 + ENVELOPE_SIZE + 20 <= len(frame) <= FRAME_SIZE \
                or frame[0] != 0x84 or frame[4:8] != MAGIC:
            return None
        packet = frame[4 + ENVELOPE_SIZE:]
        return packet if valid_ipv4(packet, self.remote, self.local, self.mtu) else None


def companion_frame(frame):
    if not 1 <= len(frame) <= FRAME_SIZE:
        raise ValueError("Companion command exceeds conservative host frame limit")
    return b"<" + struct.pack("<H", len(frame)) + frame


class CompanionDecoder:
    def __init__(self, *, ascii_preamble=False):
        self.staging = bytearray()
        self.ascii_preamble = ascii_preamble
        self.preamble_bytes = 0

    def feed(self, data):
        if len(data) > 4096 or len(self.staging) + len(data) > 4096 + RESPONSE_FRAME_SIZE + 3:
            raise BufferError("Companion input staging full")
        self.staging.extend(data)

    def pop(self):
        while self.ascii_preamble and self.staging:
            if self.staging[0] == ord(">"):
                if len(self.staging) < 3:
                    return None
                if 1 <= int.from_bytes(self.staging[1:3], "little") <= RESPONSE_FRAME_SIZE:
                    break
            byte = self.staging[0]
            if byte not in (9, 10, 13) and not 32 <= byte <= 126:
                raise ValueError("non-ASCII bytes before the first Companion frame")
            self.preamble_bytes += 1
            if self.preamble_bytes > 4096:
                raise BufferError("USB ASCII startup banner exceeded4096 bytes")
            del self.staging[0]
        if len(self.staging) < 3:
            return None
        length = int.from_bytes(self.staging[1:3], "little")
        if self.staging[0] != ord(">") or not 1 <= length <= RESPONSE_FRAME_SIZE:
            raise ValueError("invalid Companion response direction/length")
        if len(self.staging) < length + 3:
            return None
        frame = bytes(self.staging[3:length + 3])
        del self.staging[:length + 3]
        self.ascii_preamble = False  # Never resynchronize/discard data after binary ownership begins.
        return frame


class IpRelayBuffers:
    def __init__(self, codec, depth=8, command_timeout=12, now=time.monotonic, *, ascii_preamble=False):
        if not 1 <= depth <= 64 or not math.isfinite(command_timeout) or not 0 < command_timeout <= 30:
            raise ValueError("queue depth1..64 and command timeout(0,30] required")
        self.codec, self.depth, self.command_timeout, self.now = codec, depth, command_timeout, now
        self.decoder = CompanionDecoder(ascii_preamble=ascii_preamble)
        self.to_tun = deque()
        self.pending = 1
        self.pending_since = now()
        self.output = companion_frame(bytes((1,)) + bytes(7) + b"MeshCore IP")
        self.ready = False
        self.counters = dict(tun_read=0, tun_read_bytes=0, tun_written=0, tun_written_bytes=0,
                             accepted_commands=0, rejected_commands=0, invalid_ip=0,
                             ignored_frames=0, raw_pushes=0)

    def can_read_tun(self):
        return self.ready and self.pending is None and not self.output

    def can_read_companion(self):
        # A partial TCP header/body MUST allow another socket read. Complete
        # staged frames are drained by parse_pending before this is consulted.
        return len(self.to_tun) < self.depth

    def queue_tun(self, packet):
        if not self.can_read_tun():
            raise BufferError("one outstanding Companion command only")
        try:
            frame = self.codec.command(packet)
        except ValueError:
            self.counters["invalid_ip"] += 1
            return False
        self.output = companion_frame(frame)
        self.pending = 25
        self.pending_since = self.now()
        self.counters["tun_read"] += 1
        self.counters["tun_read_bytes"] += len(packet)
        return True

    def write_companion(self, write):
        if not self.output:
            return
        count = write(self.output)
        if not 0 <= count <= len(self.output):
            raise IOError("invalid Companion stream write count")
        self.output = self.output[count:]

    def feed_companion(self, data):
        if not self.can_read_companion():
            raise BufferError("Companion input must apply backpressure")
        self.decoder.feed(data)
        self.parse_pending()

    def parse_pending(self):
        while len(self.to_tun) < self.depth:
            frame = self.decoder.pop()
            if frame is None:
                return
            if frame[0] == 0x84:
                self.counters["raw_pushes"] += 1
                packet = self.codec.raw_push(frame)
                if packet is None:
                    self.counters["invalid_ip"] += 1
                else:
                    self.to_tun.append(packet)
            elif self.pending == 1 and frame[0] == 5 and not self.output:
                self.ready = True
                self.pending = None
            elif self.pending == 1 and frame[0] == 1:
                raise RuntimeError("Companion rejected APP_START")
            elif self.pending == 25 and ((frame[0] == 0 and len(frame) == 1)
                    or (frame[0] == 1 and len(frame) == 2)) and not self.output:
                self.counters["accepted_commands" if frame[0] == 0 else "rejected_commands"] += 1
                self.pending = None
            else:
                self.counters["ignored_frames"] += 1

    def write_tun(self, write):
        if not self.to_tun:
            return
        packet = self.to_tun[0]
        if write(packet) != len(packet):
            raise IOError("partial TUN datagram write")
        self.to_tun.popleft()
        self.counters["tun_written"] += 1
        self.counters["tun_written_bytes"] += len(packet)

    def check_deadline(self):
        if self.pending is not None and self.now() - self.pending_since >= self.command_timeout:
            raise TimeoutError("Companion command deadline; delivery was not confirmed")

    def stats(self):
        return dict(self.counters, ready=self.ready, pending_command=self.pending,
                    queued_to_tun=len(self.to_tun), staged_bytes=len(self.decoder.staging),
                    usb_ascii_preamble_bytes=self.decoder.preamble_bytes,
                    acceptance_note="Command OK means queued, not RF-delivered; TCP owns retries")


def validate_companion_endpoint(endpoint, *, expect_serial=None):
    parsed = validate_endpoint(endpoint)
    if parsed.scheme == "tcp":
        private_host(parsed.hostname or "")
        if parsed.port != 5000 or expect_serial is not None:
            raise ValueError("use explicit private IPv4 tcp://RADIO:5000 without USB identity options")
    elif parsed.scheme == "winusb":
        if expect_serial is None:
            raise ValueError("Windows USB requires an exact --expect-serial identity")
    elif Path(parsed.path).parent != Path("/dev/serial/by-id"):
        raise ValueError("RAW USB requires a stable explicit /dev/serial/by-id/ device")
    if expect_serial is not None and not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,128}", expect_serial):
        raise ValueError("invalid expected USB serial")
    return parsed


def serial_identity(path, expect_serial=None):
    """Read only sysfs/stat; never open or pulse the selected radio."""
    resolved = str(Path(path).resolve(strict=True))
    if not re.fullmatch(r"/dev/tty(?:ACM|USB)[A-Za-z0-9_-]+", resolved):
        raise ValueError("Selected USB alias must resolve to ttyACM/ttyUSB")
    info = os.stat(resolved)
    if not stat.S_ISCHR(info.st_mode):
        raise ValueError("Selected USB alias is not a character device")
    device = (Path("/sys/class/tty") / Path(resolved).name / "device").resolve(strict=True)
    for parent in (device, *device.parents):
        serial_path = parent / "serial"
        if serial_path.is_file():
            actual_serial = serial_path.read_text().strip()
            if not actual_serial or (expect_serial is not None and actual_serial != expect_serial):
                raise ValueError("Selected USB serial differs from expected identity")
            return resolved, actual_serial, info.st_dev, info.st_ino, info.st_rdev
    raise ValueError("Selected USB identity is unavailable")


class CompanionStream:
    def __init__(self, endpoint, *, baud=115200, serial_dtr=False, expect_serial=None,
                 windows_python=None, expect_vid=None, expect_pid=None, duration=300):
        self.socket = self.serial = self.windows = None
        parsed = validate_companion_endpoint(endpoint, expect_serial=expect_serial)
        if type(baud) is not int or not 1200 <= baud <= 2000000 or type(serial_dtr) is not bool:
            raise ValueError("use baud1200..2000000 and an explicit boolean DTR selection")
        validate_windows_options(parsed, python=windows_python, expected_serial=expect_serial,
            expected_vid=expect_vid, expected_pid=expect_pid, baud=baud,
            serial_dtr=serial_dtr, duration=duration)
        try:
            if parsed.scheme == "tcp":
                self.socket = socket.create_connection((parsed.hostname, 5000), timeout=5)
                self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.socket.setblocking(False)
                self.fd = self.socket.fileno()
            elif parsed.scheme == "winusb":
                self.windows = WindowsUsbStream(endpoint, python=windows_python,
                    expected_serial=expect_serial, expected_vid=expect_vid, expected_pid=expect_pid,
                    baud=baud, serial_dtr=serial_dtr, duration=duration)
                self.fd = self.windows.read_fd
            else:
                before = serial_identity(parsed.path, expect_serial)
                assert_port_idle(before[0])
                import serial
                self.serial = serial.Serial()
                # Open the verified resolved device, not a mutable symlink.
                self.serial.port = before[0]
                self.serial.baudrate = baud
                self.serial.timeout = self.serial.write_timeout = 0
                self.serial.exclusive = True
                self.serial.dtr = serial_dtr
                self.serial.rts = False
                open_configured_session(self.serial)
                self.fd = self.serial.fileno()
                after = serial_identity(parsed.path, expect_serial)
                opened = os.fstat(self.fd)
                if before != after or not stat.S_ISCHR(opened.st_mode) or \
                        (opened.st_dev, opened.st_ino, opened.st_rdev) != after[2:]:
                    raise ValueError("USB alias/device identity changed during the exclusive open")
            self.read_fd = self.windows.read_fd if self.windows else self.fd
            self.write_fd = self.windows.write_fd if self.windows else self.fd
        except BaseException:
            self.close()
            raise

    def read(self, count):
        if self.windows:
            return self.windows.read(count)
        data = self.socket.recv(count) if self.socket else os.read(self.fd, count)
        if not data:
            raise ConnectionError("Companion endpoint disconnected")
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


def create_tun(name):
    import fcntl
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}", name):
        raise ValueError("use a1..15-character TUN interface name")
    descriptor = os.open("/dev/net/tun", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        # IFF_TUN_EXCL refuses to attach an existing/persistent interface.
        request = struct.pack("16sH", name.encode("ascii"), 0x0001 | 0x1000 | 0x8000)
        result = fcntl.ioctl(descriptor, 0x400454CA, request)
        return descriptor, result[:16].split(b"\0", 1)[0].decode("ascii")
    except BaseException:
        os.close(descriptor)
        raise


def open_transport_then_tun(endpoint, netns, name, local, remote, mtu, *,
                            baud=115200, serial_dtr=False, expect_serial=None, **transport_options):
    stream = CompanionStream(endpoint, baud=baud, serial_dtr=serial_dtr, expect_serial=expect_serial,
                             **transport_options)
    # Open the USB/socket in the original namespace before creating our TUN.
    descriptor = None
    try:
        enter_network_namespace(netns)  # Rejects current host netns before any interface creation.
        descriptor, actual = create_tun(name)
        subprocess.run(["ip", "link", "set", "dev", actual, "mtu", str(mtu), "up"], check=True, timeout=5)
        subprocess.run(["ip", "address", "add", local + "/32", "peer", remote + "/32", "dev", actual], check=True, timeout=5)
        return stream, descriptor, actual
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        stream.close()
        raise


def relay(stream, tun_fd, buffers, *, duration, stop=None, on_ready=None):
    started = time.monotonic()
    ready_announced = False
    while not (stop and stop()) and time.monotonic() - started < duration:
        buffers.parse_pending()
        buffers.check_deadline()
        if buffers.ready and not ready_announced:
            ready_announced = True
            if on_ready:
                on_ready()
        reads = ([tun_fd] if buffers.can_read_tun() else [])
        if buffers.can_read_companion():
            reads.append(stream.read_fd)
        writes = ([tun_fd] if buffers.to_tun else []) + ([stream.write_fd] if buffers.output else [])
        ready_read, ready_write, _ = select.select(reads, writes, [], .1)
        for descriptor in ready_write:
            try:
                if descriptor == tun_fd:
                    buffers.write_tun(lambda packet: os.write(tun_fd, packet))
                else:
                    buffers.write_companion(stream.write)
            except BlockingIOError:
                pass
        for descriptor in ready_read:
            try:
                if descriptor == tun_fd:
                    buffers.queue_tun(os.read(tun_fd, 65536))  # Never truncate an oversized datagram.
                else:
                    buffers.feed_companion(stream.read(4096))
            except BlockingIOError:
                pass
    return dict(buffers.stats(), elapsed_seconds=round(time.monotonic() - started, 3))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--serial-dtr", choices=("off", "on"), default="off")
    parser.add_argument("--expect-serial", help="require this USB chip serial before and after opening")
    add_windows_arguments(parser)
    parser.add_argument("--netns", required=True)
    parser.add_argument("--interface", default="mcip0")
    parser.add_argument("--local-address", default="10.90.5.1")
    parser.add_argument("--remote-address", default="10.90.5.2")
    parser.add_argument("--path-hex", default="")
    parser.add_argument("--path-hash-width", type=int, default=1)
    parser.add_argument("--return-path-byte-count", type=int)
    parser.add_argument("--mtu", type=int)
    parser.add_argument("--session", type=lambda value: int(value, 0))
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--command-timeout", type=float, default=12)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--ready-after-handshake", action="store_true",
                        help="publish ready only after the Companion APP_START/SelfInfo handshake")
    args = parser.parse_args(argv)
    try:
        endpoint = validate_companion_endpoint(args.endpoint, expect_serial=args.expect_serial)
        validate_windows_options(endpoint, python=args.windows_python, expected_serial=args.expect_serial,
            expected_vid=args.expect_vid, expected_pid=args.expect_pid, baud=args.baud,
            serial_dtr=args.serial_dtr == "on", duration=args.duration)
        if not 1200 <= args.baud <= 2000000:
            raise ValueError("baud must be in1200..2000000")
        path = MeshPath.from_hex(args.path_hex, args.path_hash_width)
        if path.data and args.return_path_byte_count is None:
            raise ValueError("nonzero forward path also requires explicit --return-path-byte-count")
        if not math.isfinite(args.duration) or not 0 < args.duration <= 3600:
            raise ValueError("duration must be finite and in(0,3600]")
        if not Path(args.netns).is_absolute() or not Path(args.netns).is_file():
            raise ValueError("use an existing absolute isolated namespace handle")
        if args.status_file and (not args.status_file.is_absolute() or not args.status_file.parent.is_dir()):
            raise ValueError("status-file requires an absolute existing evidence directory")
        codec = IpCodec(args.local_address, args.remote_address, path, args.return_path_byte_count or 0,
                        mtu=args.mtu, session=args.session)
        buffers = IpRelayBuffers(codec, command_timeout=args.command_timeout,
                                 ascii_preamble=endpoint.scheme in ("serial", "winusb"))
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        parser.error("the isolated TUN helper requires Linux root")
    stopping = False

    def stopped(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stopped)
    signal.signal(signal.SIGTERM, stopped)
    stream, tun_fd, name = open_transport_then_tun(args.endpoint, args.netns, args.interface,
        args.local_address, args.remote_address, codec.mtu, baud=args.baud,
        serial_dtr=args.serial_dtr == "on", expect_serial=args.expect_serial,
        windows_python=args.windows_python, expect_vid=args.expect_vid, expect_pid=args.expect_pid,
        duration=args.duration)
    buffers.pending_since = time.monotonic()  # Exclude socket/TUN setup from APP_START deadline.
    try:
        ready_state = dict(state="ready", transport="meshcore-raw-ip", interface=name, mtu=codec.mtu,
                  maximum_mtu=codec.maximum_mtu, session=codec.session,
                  forward_path_bytes=len(path.data), return_path_bytes=args.return_path_byte_count or 0)
        emit(dict(ready_state, state="starting" if args.ready_after_handshake else "ready"), args.status_file)
        stats = relay(stream, tun_fd, buffers, duration=args.duration, stop=lambda: stopping,
            on_ready=(lambda: emit(ready_state, args.status_file)) if args.ready_after_handshake else None)
        emit(dict(state="stopped", interface=name, stats=stats), args.status_file)
        return 0
    except Exception as error:
        emit(dict(state="error", interface=name, error=str(error), stats=buffers.stats()), args.status_file)
        return 1
    finally:
        os.close(tun_fd)
        stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
