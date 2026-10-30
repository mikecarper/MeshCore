"""One USB repeater link for direct admin commands and mOTA file serving.

Only the reader task consumes serial bytes. It answers device-initiated seeder
requests while command callers await their echoed, line-framed CLI replies.
Requires repeater firmware with ``ota.peer`` and shared-text seeder control.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import math
import re
import struct
import time
import zlib

import lora_ota as ota
from ota_wizard_usb import open_serial_session


class RepeaterError(ota.OtaError):
    pass


def _key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise RepeaterError("A complete destination public key is required")
    return value.lower()


def _one_line(value: str, limit: int) -> bytes:
    if not isinstance(value, str) or not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise RepeaterError("Commands must be one nonempty line without control characters")
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        raise RepeaterError("The command is too long for the USB repeater")
    return encoded


class SeederData:
    """The read-only subset of MotaSeederProto.h, with independent raw DEFLATE."""
    def __init__(self, blob: bytes):
        self.info = ota.parse_mota(bytes(blob))
        self.blob = self.info.blob
        self._compressed: dict[int, bytes | None] = {}

    def request(self, op: int, args: bytes) -> tuple[int, bytes]:
        info = self.info
        if op == 1 and not args:
            return 0, b"\x01"
        if op == 2 and args == b"\0":
            blocks = math.ceil(info.payload_size / info.block_size)
            return 0, struct.pack("<4sIIBBIIIIIBBH", info.merkle_root,
                info.target_id, info.fw_version, info.codec_id, info.flags,
                len(self.blob), 8 + ota.MOTA_FIXED_MANIFEST_SIZE, blocks,
                info.payload_offset, info.payload_size, info.block_size.bit_length() - 1,
                1, 0)
        if op == 3 and len(args) == 7:
            index, offset, length = struct.unpack("<BIH", args)
            if index == 0 and 0 < length <= 192 and offset + length <= len(self.blob):
                return 0, self.blob[offset:offset + length]
        if op == 9 and len(args) == 7:
            index, block, offset, length = struct.unpack("<BHHH", args)
            blocks = math.ceil(info.payload_size / info.block_size)
            if index != 0 or block >= blocks or length > 190 or (not length and offset):
                return 1, b""
            if block not in self._compressed:
                start = block * info.block_size
                raw = info.payload[start:start + info.block_size]
                compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
                encoded = compressor.compress(raw) + compressor.flush()
                self._compressed[block] = encoded if len(encoded) < len(raw) else None
            encoded = self._compressed[block]
            if encoded is not None and offset + length <= len(encoded):
                return 0, struct.pack("<H", len(encoded)) + encoded[offset:offset + length]
        # This updater serves one package; it never accepts device writes or
        # creates captured files from unsolicited storage operations.
        return 1, b""


class StreamDecoder:
    HEADER_LENGTHS = {1: 0, 2: 1, 3: 7, 4: 4, 5: 8, 6: 10, 7: 10, 8: 4, 9: 7}

    def __init__(self, *, frame_timeout: float = 2):
        self.buffer = bytearray()
        self.line = bytearray()
        self.pending_since = None
        self.frame_timeout = frame_timeout

    def feed(self, data: bytes):
        events = []
        now = time.monotonic()
        if self.pending_since is not None and now - self.pending_since > self.frame_timeout:
            self.buffer.clear()
            self.line.clear()
            self.pending_since = None
        self.buffer.extend(data)
        while self.buffer:
            if self.buffer[:1] == b"M" and len(self.buffer) < 3:
                self.pending_since = self.pending_since or now
                break
            if self.buffer[:2] == b"MS" and self.buffer[2] in self.HEADER_LENGTHS:
                op = self.buffer[2]
                header = self.HEADER_LENGTHS[op]
                needed = 3 + header + 1
                if len(self.buffer) < needed:
                    self.pending_since = self.pending_since or now
                    break
                if op == 6:
                    length = int.from_bytes(self.buffer[11:13], "little")
                    if length > 512:
                        del self.buffer[:3]
                        self.line.clear()
                        self.pending_since = None
                        continue
                    needed += length
                    if len(self.buffer) < needed:
                        self.pending_since = self.pending_since or now
                        break
                frame = bytes(self.buffer[:needed])
                del self.buffer[:needed]
                self.line.clear()
                self.pending_since = None
                checksum = 0
                for byte in frame[2:]:
                    checksum ^= byte
                if checksum == 0:
                    events.append(("request", op, frame[3:-1]))
                continue
            byte = self.buffer.pop(0)
            self.pending_since = None
            if byte == 10:
                line = self.line.decode("utf-8", "replace").strip("\r\n")
                self.line.clear()
                if line:
                    events.append(("line", line))
            else:
                self.line.append(byte)
                if len(self.line) > 512:
                    self.line.clear()
        return events


class RepeaterTransport:
    def __init__(self, port, baud=115200, *, command_timeout=12,
                 remote_timeout=40, poll_seconds=.25, serial_factory=None,
                 open_settle=.75, known_repeater: str | None = None, acl_only=False):
        self.port, self.baud = port, baud
        self.command_timeout, self.remote_timeout = command_timeout, remote_timeout
        self.poll_seconds, self.open_settle = poll_seconds, open_settle
        self._serial_factory = serial_factory
        self._serial = self._reader_task = None
        self._decoder = StreamDecoder()
        self._lines = asyncio.Queue(maxsize=64)
        self._write_lock, self._command_lock, self._admin_lock = (asyncio.Lock() for _ in range(3))
        self._reader_failed = asyncio.Event()
        self._failure = None
        self._source_key = None
        self._known_repeater = _key(known_repeater) if known_repeater is not None else None
        if acl_only and self._known_repeater is None:
            raise RepeaterError("ACL-only administration requires a complete known repeater key")
        self._acl_only = acl_only
        self._authenticated_key = None
        self._last_peer_use = 0
        self._clock_checked = False
        self._seeder = None
        self._serve_task = self._serve_stop = None

    @property
    def source_key(self):
        return self._source_key

    async def open(self):
        if self._serial is not None:
            return self
        factory = self._serial_factory
        if factory is None:
            try:
                import serial
            except ImportError as exc:
                raise RepeaterError("Install pyserial for a USB repeater connection") from exc
            factory = serial.Serial
        def create():
            connection = factory(port=None, baudrate=self.baud, timeout=.1,
                                 write_timeout=3, rtscts=False, dsrdtr=False)
            open_serial_session(connection, self.port)
            return connection
        try:
            self._serial = await asyncio.to_thread(create)
            self._reader_failed.clear()
            self._failure = None
            self._reader_task = asyncio.create_task(self._read_loop())
            await asyncio.sleep(self.open_settle)
            reply = await self.local_command("get public.key")
            match = re.search(r"\b[0-9a-fA-F]{64}\b", reply)
            if match is None:
                raise RepeaterError("The USB repeater did not report its complete public key")
            self._source_key = match[0].lower()
            status = await self.local_command("ota.peer status")
            if not re.fullmatch(r"OTApeer v=1 session=[01] busy=[01] peers=\d+", status):
                raise RepeaterError("This repeater needs firmware with direct USB OTA administration (ota.peer)")
            if self._known_repeater is not None:
                capabilities = await self.local_command("ota.peer capabilities")
                if capabilities != "OTApeer known_repeater=1":
                    raise RepeaterError("Update the USB source firmware to use a known repeater key "
                                        "without discovery (ota.peer login.repeater).")
            return self
        except BaseException:
            await self.close()
            raise

    async def _write(self, data: bytes):
        if self._serial is None or self._failure is not None:
            raise ConnectionError("The USB repeater is disconnected")
        async with self._write_lock:
            def send():
                remaining = memoryview(data)
                while remaining:
                    count = self._serial.write(remaining)
                    if not count:
                        raise OSError("USB write did not make progress")
                    remaining = remaining[count:]
            try:
                await asyncio.to_thread(send)
            except OSError as exc:
                raise ConnectionError("Could not write to the USB repeater") from exc

    async def _read_loop(self):
        try:
            while self._serial is not None:
                raw = await asyncio.to_thread(self._serial.read, 512)
                if not raw and not self._serial.is_open:
                    raise ConnectionError("USB repeater disconnected")
                for event in self._decoder.feed(raw):
                    if event[0] == "request":
                        _, op, args = event
                        status, payload = self._seeder.request(op, args) if self._seeder else (
                            (0, b"\0") if op == 1 else (1, b""))
                        frame = b"ms" + bytes((op, status)) + payload
                        checksum = 0
                        for byte in frame:
                            checksum ^= byte
                        await self._write(frame + bytes((checksum,)))
                    else:
                        if self._lines.full():
                            self._lines.get_nowait()
                        self._lines.put_nowait(event[1])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failure = exc
            self._reader_failed.set()
            if self._lines.full():
                self._lines.get_nowait()
            self._lines.put_nowait(None)

    @staticmethod
    def _echo_matches(line: str, command: str) -> bool:
        if line.strip() == command.strip():
            return True
        if command.startswith(("ota.peer login ", "ota.peer login.repeater ")):
            prefix = " ".join(command.split(" ", 3)[:3]) + " "
            return line.startswith(prefix) and bool(re.fullmatch(r"\*+", line[len(prefix):]))
        return False

    async def local_command(self, text):
        encoded = _one_line(text, 240)
        async with self._command_lock:
            while not self._lines.empty():
                self._lines.get_nowait()
            if self._failure is not None:
                raise ConnectionError("The USB repeater is disconnected") from self._failure
            await self._write(encoded + b"\r\n")
            deadline, echo_seen = time.monotonic() + self.command_timeout, False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("The USB repeater did not complete the command; its result is unknown")
                line = await asyncio.wait_for(self._lines.get(), remaining)
                if line is None:
                    raise ConnectionError("The USB repeater is disconnected") from self._failure
                if self._echo_matches(line, text):
                    echo_seen = True
                    continue
                if echo_seen:
                    match = re.match(r"\s*->\s?(.*)$", line)
                    if match:
                        return match[1].strip()

    async def _check_clock(self):
        if self._clock_checked:
            return
        now = int(time.time())
        start, end = ota.parse_source_clock_window(await self.local_command("clock"), "USB repeater")
        if start > now + 600:
            raise RepeaterError("The repeater clock is over ten minutes ahead; correct it before OTA login")
        if end < now:
            reply = await self.local_command(f"time {now + 2}")
            if not reply.startswith("OK"):
                raise RepeaterError("Could not advance the repeater clock for authenticated OTA")
        self._clock_checked = True

    async def _job(self, command: str) -> str:
        queued = await self.local_command(command)
        match = re.fullmatch(r"OTApeer job=([0-9a-fA-F]{8}) state=pending", queued)
        if match is None:
            raise RepeaterError(queued or "The repeater did not start the direct request")
        job, deadline = match[1].upper(), time.monotonic() + self.remote_timeout
        offset, total, result, result_state = 0, None, bytearray(), None
        while time.monotonic() < deadline:
            reply = await self.local_command("ota.peer result" + (f" {offset}" if offset else ""))
            page = re.fullmatch(r"OTApeer job=([0-9a-fA-F]{8}) state=(pending|ok|error) bytes=(\d+) off=(\d+) data=([0-9a-fA-F]+|-)", reply)
            if page is None or page[1].upper() != job:
                raise RepeaterError("The repeater's direct request changed; its result is unknown")
            state, size, page_offset = page[2], int(page[3]), int(page[4])
            if state == "pending":
                await asyncio.sleep(self.poll_seconds)
                continue
            if size > 160 or page_offset != offset or (total is not None and (total != size or result_state != state)):
                raise RepeaterError("The repeater returned inconsistent direct reply pages")
            try:
                chunk = b"" if page[5] == "-" else bytes.fromhex(page[5])
            except ValueError as exc:
                raise RepeaterError("The repeater returned an invalid direct reply") from exc
            if len(chunk) > 48 or offset + len(chunk) > size or (not chunk and offset < size):
                raise RepeaterError("The repeater returned an invalid direct reply length")
            total, result_state = size, state
            result.extend(chunk)
            offset += len(chunk)
            if offset == total:
                text = result.decode("utf-8", "replace")
                if state == "error":
                    if (text == "login return path transmission failed"
                            or "timed out" in text.lower() or "unknown" in text.lower()):
                        raise ota.TransmissionError(text)
                    raise RepeaterError(text)
                return text
        raise ota.TransmissionError("No completed direct reply; the command result is unknown")

    async def discover(self):
        async with self._admin_lock:
            return await self._discover_locked()

    async def _discover_locked(self):
        self._authenticated_key = None
        await self._job("ota.peer discover")
        status = await self.local_command("ota.peer status")
        count = re.search(r"\bpeers=(\d+)\b", status)
        if count is None or int(count[1]) > 8:
            raise RepeaterError("Invalid direct discovery result")
        result = []
        for index in range(int(count[1])):
            reply = await self.local_command(f"ota.peer peers {index}")
            peer = re.fullmatch(r"OTApeer peer=(\d+) key=([0-9a-fA-F]{64}) type=([23]) rssi=(-?\d+) snr=(-?\d+)", reply)
            if peer is None or int(peer[1]) != index:
                raise RepeaterError("The repeater returned an invalid direct peer")
            result.append({"public_key": peer[2].lower(), "type": int(peer[3]),
                           "rssi": int(peer[4]), "snr": int(peer[5])})
        return sorted(result, key=lambda item: item["rssi"], reverse=True)

    async def _login_locked(self, key, password):
        if self._known_repeater is not None:
            if key != self._known_repeater:
                raise RepeaterError("The destination differs from the explicit known repeater key")
            # ACL mode proves the existing identity without sending a password
            # login. The operator must already have granted this source access.
            if self._acl_only:
                await self._job(f"ota.peer acl {key}")
            else:
                await self._job(f"ota.peer login.repeater {key} {password}")
            self._authenticated_key = key
            self._last_peer_use = time.monotonic()
            return
        # Native login binds its packet layout to a freshly discovered board
        # role. A remembered/manual key must prove it is still directly heard.
        peers = await self._discover_locked()
        if key not in {item["public_key"] for item in peers}:
            raise RepeaterError("The selected destination did not answer fresh direct discovery")
        await self._job(f"ota.peer login {key} {password}")
        self._authenticated_key = key
        self._last_peer_use = time.monotonic()

    async def remote_command(self, public_key, password, text):
        key = _key(public_key)
        if self._known_repeater is not None and key != self._known_repeater:
            raise RepeaterError("The destination differs from the explicit known repeater key")
        _one_line(text, 154)
        if (not isinstance(password, str) or len(password.encode("utf-8")) > 63
                or any(ord(char) < 32 or ord(char) == 127 for char in password)):
            raise RepeaterError("Admin passwords must fit in 63 UTF-8 bytes without control characters")
        if self._acl_only and password:
            raise RepeaterError("ACL-only administration requires an empty password")
        async with self._admin_lock:
            await self._check_clock()
            if key != self._authenticated_key or time.monotonic() - self._last_peer_use > 240:
                await self._login_locked(key, password)
            command = f"ota.peer cmd {key} {text}"
            try:
                reply = await self._job(command)
            except RepeaterError as exc:
                if str(exc) != "ERR peer login required":
                    raise
                await self._login_locked(key, password)
                reply = await self._job(command)
            self._last_peer_use = time.monotonic()
            return reply

    async def get_radio(self):
        reply = (await self.local_command("get radio")).lstrip("> ")
        fields = reply.split(",")
        try:
            values = float(fields[0]), float(fields[1]), int(fields[2]), int(fields[3])
            ota.parse_temp_radio(",".join(map(str, values)) + ",240")
            return values
        except (ValueError, IndexError, ota.OtaError) as exc:
            raise RepeaterError("The repeater did not report valid radio parameters") from exc

    async def reset_remote_session(self, public_key):
        """Drop the peer timestamp floor after a target reboot, retaining USB serving."""
        _key(public_key)
        async with self._admin_lock:
            reply = await self.local_command("ota.peer close")
            if reply != "OTApeer closed":
                raise RepeaterError(reply or "Could not reset the direct admin session")
            self._authenticated_key = None
            self._last_peer_use = 0

    async def set_radio(self, radio):
        if len(radio) != 4:
            raise RepeaterError("Radio settings need frequency, bandwidth, SF and CR")
        text = ",".join(map(str, radio))
        ota.parse_temp_radio(text + ",240")
        reply = await self.local_command("set radio " + text)
        if not reply.startswith("OK"):
            raise RepeaterError(reply)
        actual = await self.get_radio()
        if any(abs(float(a) - float(b)) > .002 for a, b in zip(actual, radio)):
            raise RepeaterError("Saved repeater radio settings do not match the requested tuple")

    async def serve(self, blob, stop_event):
        if self._serve_task is not None:
            raise RepeaterError("The USB repeater is already serving an update")
        self._seeder = SeederData(blob)
        self._serve_task, self._serve_stop = asyncio.current_task(), stop_event
        stopped = failed = None
        try:
            reply = await self.local_command("ota folder on")
            if not reply.startswith("OK folder attached"):
                raise RepeaterError(reply or "Could not attach the OTA package")
            stopped = asyncio.create_task(stop_event.wait())
            failed = asyncio.create_task(self._reader_failed.wait())
            await asyncio.wait((stopped, failed), return_when=asyncio.FIRST_COMPLETED)
            if self._failure is not None:
                raise ConnectionError("The USB repeater disconnected while serving") from self._failure
        finally:
            for task in (stopped, failed):
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            if self._serial is not None and self._failure is None:
                with suppress(TimeoutError, ConnectionError, RepeaterError):
                    await self.local_command("ota folder off")
            self._seeder = self._serve_task = self._serve_stop = None

    async def close(self):
        if self._serve_stop is not None:
            self._serve_stop.set()
            if self._serve_task is not asyncio.current_task():
                with suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(self._serve_task), self.command_timeout + 1)
        if self._serial is not None and self._reader_task is not None and self._failure is None:
            with suppress(Exception):
                await self.local_command("ota.peer close")
        connection, self._serial = self._serial, None
        if connection is not None:
            with suppress(Exception):
                await asyncio.to_thread(connection.close)
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        self._reader_task = None
        self._source_key = self._authenticated_key = None
        self._clock_checked = False
