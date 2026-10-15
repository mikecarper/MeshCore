#!/usr/bin/env python3
"""Opt-in single-file mOTA source over full KISS, not Companion CMD65.

Probe-only by default. --serve requires an explicit USB identity, logical radio
port and expected frequency. --require-lab-lock additionally proves the compiled
909.5 MHz TX lock and the 909.5/500/SF5/CR5 bench tuple. No radio/frequency setters
exist here. --zero-pacing explicitly selects TXDELAY=0/PERSIST=255/SLOTTIME=1.
KISS escaping is cable-only; radio bytes are F2 00 + OTA for negotiated direct
transfers, or ordinary 31/32 path-zero framing for discovery/legacy fallback.
Every emitted radio frame waits for F8 TX_DONE; no host ACK means RF delivery.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / "tools/hil", ROOT / "tools/mota", ROOT / "scripts"):
    sys.path.insert(0, str(folder))
from ethernet_kiss_modem_probe import (Decoder, ProtocolError, PRIMARY_STRUCT,
    SECONDARY_STRUCT, GUARD_HZ, encode_frame, open_verified_port)
import motalib

ADV, QUERY, HAVE, GET_MANIFEST, MANIFEST, REQ, DATA, REQ_PROOF, PROOF = range(1, 10)
GET_LEAVES, LEAVES = 10, 11
TRANSFER = frozenset(range(GET_MANIFEST, LEAVES + 1))
WIDE_PREFIX = b"\xf2\0"
WIDE_MESSAGE_MAX, WIDE_DATA, V2_DATA, LEGACY_DATA = 253, 240, 171, 160
MAX_BATCH, MAX_PENDING_RX = 64, 64


class AdaptivePace:
    """Loss-aware packet spacing; duplicate requests never compound slowdown."""
    def __init__(self, minimum=.25):
        if minimum not in (.25, .5, 1):
            raise ValueError("Minimum pace must be0.25,0.5 or1.0")
        self.minimum, self.effective = minimum, 1.0
        self.loss_blocks = set()
        self.last_forward = -1
        self.clean = self.sparse_requests = self.duplicate_sparse = self.recovery_steps = 0

    def observe(self, block, wanted, valid):
        wanted &= valid
        if not wanted:
            return
        if wanted != valid:
            self.sparse_requests += 1
            if block in self.loss_blocks:
                self.duplicate_sparse += 1
                return
            self.loss_blocks.add(block)
            self.effective = max(self.minimum, .5 if self.effective > .5 else .25)
            self.last_forward = max(self.last_forward, block)
            self.clean = 0
        elif block > self.last_forward:
            self.last_forward = block
            if self.effective < 1:
                self.clean += 1
                if self.clean >= 16:
                    self.effective = min(1.0, round(self.effective + .1, 2))
                    self.recovery_steps += 1
                    self.clean = 0

    def quiet(self, airtime):
        return max(0.0, airtime) * (1 / self.effective - 1)

    def report(self):
        return {"effective": self.effective, "minimum": self.minimum,
                "unique_loss_blocks": len(self.loss_blocks), "sparse_requests": self.sparse_requests,
                "duplicate_sparse_requests": self.duplicate_sparse, "recovery_steps": self.recovery_steps}


def kiss_data(port: int, radio: bytes) -> bytes:
    if port not in (0, 1) or not 1 <= len(radio) <= 255:
        raise ValueError("KISS DATA requires explicit port 0/1 and 1..255 RF bytes")
    body = bytes((port << 4,)) + radio
    return b"\xc0" + body.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"


@dataclass(frozen=True)
class RadioMessage:
    message: bytes
    wide: bool
    observed_path_bytes: int = 0


def decode_radio(raw: bytes) -> RadioMessage | None:
    if not 3 <= len(raw) <= 255:
        return None
    if raw[0] == 0xF2:
        return RadioMessage(raw[2:], True) if raw[1] == 0 and raw[2] in TRANSFER else None
    if raw[0] not in (0x31, 0x32):
        return None  # no transport codes, other payload types or future versions
    mode, count = raw[1] >> 6, raw[1] & 63
    if mode == 3:
        return None
    path_bytes = count * (mode + 1)
    at = 2 + path_bytes
    if path_bytes > 64 or at >= len(raw) or len(raw) - at > 184:
        return None
    if not ADV <= raw[at] <= LEAVES:
        return None
    # This tool is a direct source, never a repeater or reverse-route builder.
    # Background QUERY may be overheard through relays; DATA is zero-hop only.
    return RadioMessage(raw[at:], False, path_bytes)


def wrap_radio(message: bytes, *, wide=False, flood=False) -> bytes:
    if not message or not ADV <= message[0] <= LEAVES:
        raise ValueError("Unknown OTA message")
    if wide:
        if message[0] not in TRANSFER or flood or len(message) > WIDE_MESSAGE_MAX:
            raise ValueError("Wide OTA is transfer-only, path-zero and at most253 bytes")
        return WIDE_PREFIX + message
    if len(message) > 184:
        raise ValueError("Legacy OTA message exceeds Packet payload")
    return bytes((0x31 if flood else 0x32, 0)) + message


def zopfli1000(data: bytes) -> bytes:
    from zopfli_compress import MAXIMUM_ITERATIONS, raw_deflate_compress
    return raw_deflate_compress(data, numiterations=MAXIMUM_ITERATIONS)


class MotaSource:
    def __init__(self, blob: bytes, seeder_id: bytes, *, compressor=zopfli1000, minimum_pace=.25):
        if len(blob) > 32 * 1024 * 1024 or len(seeder_id) != 4:
            raise ValueError("Invalid source size or public seeder id")
        self.parsed = motalib.parse_container(blob)
        problems = motalib.verify(self.parsed)
        if problems:
            raise ValueError("Invalid mOTA: " + "; ".join(problems))
        self.manifest = self.parsed.manifest
        if (self.manifest.hash_algo != motalib.HASH_ALGO_SHA256 or
            self.manifest.codec_id not in (motalib.CODEC_FULL, motalib.CODEC_DETOOLS_SEQUENTIAL,
                                           motalib.CODEC_DETOOLS_INPLACE) or
            self.manifest.is_full != (self.manifest.codec_id == motalib.CODEC_FULL)):
            raise ValueError("Unsupported manifest hash/codec contract")
        if self.manifest.block_size not in (1024, 2048) or not 1 <= self.manifest.block_count <= 65535:
            raise ValueError("Only1/2KiB OTA blocks and unsigned16-bit indexes are supported")
        self.mid = self.manifest.merkle_root
        self.seeder_id, self.digest = seeder_id, motalib.mh4(self.mid)
        self.manifest_bytes = self.manifest.serialize()[:197]
        self.compressor = compressor
        self.pace = AdaptivePace(minimum_pace)
        self.compressed: dict[int, bytes] = {}
        self.seen: set[tuple] = set()
        self.stats = {"requests": 0, "invalid_requests": 0, "data_frames": 0,
                      "repeated_data_frames": 0, "ota_payload_bytes": 0}

    def block(self, index):
        size = self.manifest.block_size
        return self.parsed.payload[index * size:(index + 1) * size]

    def prepare(self):
        """Do expensive host compression before opening the live radio session."""
        for index in range(self.manifest.block_count):
            if index in self.compressed:
                continue
            raw = self.block(index)
            encoded = self.compressor(raw)
            self.compressed[index] = encoded if 0 < len(encoded) < len(raw) else raw

    def advert(self):
        return wrap_radio(bytes((ADV,)) + self.seeder_id + b"\1" + self.digest, flood=True)

    def proof(self, index):
        siblings = motalib.proof_siblings(self.manifest.leaves, index)
        return bytes((PROOF,)) + self.mid + struct.pack("<HB", index, len(siblings) // 4) + siblings

    def _fragments(self, opcode, body, mask):
        chunks = [body[at:at + 176] for at in range(0, len(body), 176)]
        if not 1 <= len(chunks) <= 16:
            return []
        return [bytes((opcode,)) + self.mid + bytes((i, len(chunks))) + chunk
                for i, chunk in enumerate(chunks) if mask & (1 << i)]

    def _block_replies(self, index, mask, wide):
        if index >= self.manifest.block_count:
            raise ValueError("Out-of-range block")
        v2 = bool(mask & 0x8000) and (not mask & 0x2000 or not mask & 0x1000)
        extended = v2 and bool(mask & 0x2000)
        if wide and not v2:
            raise ValueError("Wide request must explicitly negotiate v2")
        if v2 and extended != (self.manifest.block_size > 1024):
            raise ValueError("Wrong v2 block descriptor geometry")
        raw = self.block(index)
        encoded = self.compressed.get(index, raw) if v2 and mask & 0x4000 else raw
        fragment_size = WIDE_DATA if wide else V2_DATA if v2 else LEGACY_DATA
        wanted = mask & (0x0FFF if extended else 0x1FFF) if v2 else mask
        raw_mask = (1 << math.ceil(len(raw) / fragment_size)) - 1
        wanted &= raw_mask
        stream_id = motalib.mh4(encoded)
        result = []
        for fragment, at in enumerate(range(0, len(encoded), fragment_size)):
            if not wanted & (1 << fragment):
                continue
            offset = (0x8000 | (fragment << (11 if extended else 10)) | (len(encoded) - 1)
                      | (0x4000 if not extended and encoded != raw else 0)) if v2 else at
            data = (stream_id if v2 else b"") + encoded[at:at + fragment_size]
            result.append(bytes((DATA,)) + self.mid + struct.pack("<HH", index, offset) + data)
        if wanted:
            result.append(self.proof(index))
        return result

    def replies(self, radio: bytes) -> list[bytes]:
        incoming = decode_radio(radio)
        if incoming is None:
            return []
        message, wide = incoming.message, incoming.wide
        opcode = message[0]
        result = []
        try:
            if opcode == QUERY:
                if len(message) not in (13, 17):
                    raise ValueError("Truncated QUERY")
                if message[1:5] != self.seeder_id or message[5:9] != self.digest:
                    return []
                target = struct.unpack_from("<I", message, 9)[0]
                wanted = struct.unpack_from("<I", message, 13)[0] if len(message) == 17 else 0
                if wanted and not wanted & 1:
                    return []
                m = self.manifest
                rows = (self.mid + struct.pack("<IIBBH", m.target_id, m.fw_version,
                        m.codec_id, m.flags, m.block_count)) if target in (0, m.target_id) else b""
                have = bytes((HAVE,)) + self.seeder_id + self.digest + bytes((0, 1, bool(rows))) + rows
                self.stats["requests"] += 1
                return [wrap_radio(have, flood=True)]
            if opcode not in (GET_MANIFEST, GET_LEAVES, REQ, REQ_PROOF):
                return []
            if incoming.observed_path_bytes and radio[0] != 0x31:
                return []  # a not-yet-consumed direct path belongs to its relay
            if len(message) < 5 or message[1:5] != self.mid:
                return []
            if opcode in (GET_MANIFEST, GET_LEAVES):
                if len(message) != 7:
                    raise ValueError("Bad metadata request")
                mask = struct.unpack_from("<H", message, 5)[0]
                body = self.manifest_bytes if opcode == GET_MANIFEST else b"".join(self.manifest.leaves)
                result = self._fragments(MANIFEST if opcode == GET_MANIFEST else LEAVES, body, mask)
            elif opcode == REQ_PROOF:
                if len(message) != 7:
                    raise ValueError("Bad proof request")
                index = struct.unpack_from("<H", message, 5)[0]
                if index >= self.manifest.block_count:
                    raise ValueError("Out-of-range proof")
                result = [self.proof(index)]
            else:
                if len(message) < 9 or (len(message) - 5) % 4 or (len(message) - 5) // 4 > 4:
                    raise ValueError("Bad request window")
                observations = []
                for at in range(5, len(message), 4):
                    index, mask = struct.unpack_from("<HH", message, at)
                    result.extend(self._block_replies(index, mask, wide))
                    v2 = bool(mask & 0x8000) and (not mask & 0x2000 or not mask & 0x1000)
                    fragment_size = WIDE_DATA if wide else V2_DATA if v2 else LEGACY_DATA
                    valid = (1 << math.ceil(len(self.block(index)) / fragment_size)) - 1
                    wanted = mask & (0x0FFF if mask & 0x2000 else 0x1FFF) if v2 else mask
                    observations.append((index, wanted, valid))
                # Only an entirely valid snapshot may change the sender pace.
                for observation in observations:
                    self.pace.observe(*observation)
            if len(result) > MAX_BATCH:
                raise ValueError("Response batch exceeds bounded window")
        except (ValueError, struct.error):
            self.stats["invalid_requests"] += 1
            return []  # reject whole malformed batch, never a partial surprise
        self.stats["requests"] += 1
        # Existing OTA adapters flood every normal transfer response from a new
        # path-zero origin. Preserve that legacy routed fallback exactly; only
        # explicit F2/00 requests establish the non-forwardable wide transport.
        return [wrap_radio(message, wide=wide, flood=not wide) for message in result]

    def note_sent(self, radio):
        incoming = decode_radio(radio)
        if incoming is None:
            raise ValueError("Invalid emitted OTA frame")
        message = incoming.message
        self.stats["ota_payload_bytes"] += len(message)
        if message[0] == DATA:
            self.stats["data_frames"] += 1
            identity = (incoming.wide, message[1:9], hashlib.sha256(message[9:]).digest())
            if identity in self.seen:
                self.stats["repeated_data_frames"] += 1
            self.seen.add(identity)


class KissLink:
    """One exclusive serial owner, one TX in flight, bounded received requests."""
    def __init__(self, port, logical_port, *, timeout=10, clock=time.monotonic):
        if logical_port not in (0, 1) or not 0 < timeout <= 60:
            raise ValueError("Invalid logical port or bounded deadline")
        self.port, self.logical_port, self.timeout, self.clock = port, logical_port, timeout, clock
        self.decoder, self.frames, self.received = Decoder(), deque(), deque()
        self.authorized = False
        self.radio = None
        self.rf_bytes = self.kiss_bytes = self.tx_frames = self.tx_failed = 0
        self.tx_started_frames = self.tx_started_bytes = 0
        self.last_airtime_seconds = 0.0
        self.tx_done_wait_seconds = self.estimated_airtime_seconds = 0.0

    def _write(self, frame):
        if self.port.write(frame) != len(frame):
            raise ProtocolError("Short KISS write")
        self.kiss_bytes += len(frame)
        self.port.flush()

    def _next(self, deadline):
        byte_count = 0
        while self.clock() < deadline:
            if self.frames:
                return self.frames.popleft()
            # A fixed read(1024) waits out the serial timeout for every tiny
            # hardware/TX_DONE reply. Block for one byte only when empty, then
            # drain the bytes already available without waiting to fill a chunk.
            data = self.port.read(min(getattr(self.port, "in_waiting", 0) or 1, 1024))
            byte_count += len(data)
            if byte_count > 65536:
                raise ProtocolError("KISS receive byte budget exceeded")
            self.frames.extend(self.decoder.feed(data))
            if len(self.frames) > 256:
                raise ProtocolError("KISS receive frame budget exceeded")
        return None

    def _data_or_meta(self, frame):
        if frame[0] in (0, 0x10):
            if frame[0] >> 4 == self.logical_port:
                if len(self.received) >= MAX_PENDING_RX:
                    raise ProtocolError("Received request backlog exceeded bound")
                self.received.append(frame[1:])
            return True
        if frame[:2] == b"\6\xf9" and len(frame) == 4:
            return True
        return False

    def request(self, command, payload=b"", *, size):
        self._write(encode_frame(6, bytes((command,)) + payload))
        deadline = self.clock() + self.timeout
        for _ in range(256):
            frame = self._next(deadline)
            if frame is None:
                break
            if self._data_or_meta(frame):
                continue
            if frame[:2] == bytes((6, command | 0x80)) and len(frame) == size + 2:
                return frame[2:]
            raise ProtocolError("Unexpected KISS hardware response")
        raise ProtocolError("KISS hardware response deadline exceeded")

    def selected_radio(self):
        if self.logical_port == 0:
            # KISS has no primary temporary-lease setter/saved shadow: this
            # getter synchronizes from the effective RadioProfiles.primary.
            return PRIMARY_STRUCT.unpack(self.request(0x0B, size=10))
        values = SECONDARY_STRUCT.unpack(self.request(0x20, size=14))
        # GET_RADIO2_EXT is the SAVED profile, not an active tempradio2 lease.
        # Reject every nonzero temporary tuple (even expiry at zero minutes)
        # rather than authorizing RF from a misleading saved-frequency echo.
        if any(self.request(0x1E, size=15)):
            raise ProtocolError("Active temporary radio2 lease; refusing RF/pacing changes")
        if values[4] != 2 or values[6] != 0:
            raise ProtocolError("Radio2 must already be RX/TX MeshCore, not Ethernet/Meshtext")
        return values

    def preflight(self, *, expected_frequency=None, expected_public_key=None,
                  require_lab_lock=False, serve=False, zero_pacing=False):
        if zero_pacing and not serve:
            raise ValueError("Pacing setters require explicit --serve")
        guard = None
        if require_lab_lock:
            guard = struct.unpack("<I", self.request(0x21, size=4))[0]
            if guard != GUARD_HZ:
                raise ProtocolError("Required compiled909.5 MHz TX lock is absent")
        version = self.request(0x11, size=2)
        if version[0] < 2:
            raise ProtocolError("Full KISS protocol2+ with F8 TX_DONE is required")
        public_key = self.request(0x01, size=32)
        if expected_public_key is not None and public_key != expected_public_key:
            raise ProtocolError("Public identity mismatch")
        self.request(0x17, size=0)
        radio = self.selected_radio()
        if expected_frequency is not None and abs(radio[0] - expected_frequency) > 50:
            raise ProtocolError("Selected frequency does not match explicit expectation")
        if serve and expected_frequency is None:
            raise ValueError("Serving requires an explicit expected frequency")
        if require_lab_lock and (abs(radio[0] - GUARD_HZ) > 50 or radio[1:4] != (500000, 5, 5)):
            raise ProtocolError("Lab requires909.5MHz/BW500/SF5/CR5")
        self.radio = radio
        self.authorized = serve
        if zero_pacing:
            for command, value in ((1, 0), (2, 255), (3, 1)):
                self._write(encode_frame(command, bytes((value,))))
        return {"protocol": list(version), "identity_sha256": hashlib.sha256(public_key).hexdigest(),
                "logical_port": self.logical_port, "radio": list(radio), "lab_lock_hz": guard,
                "serve": serve, "zero_pacing": zero_pacing}, public_key

    def poll(self, timeout=.1):
        if self.received:
            return self.received.popleft()
        deadline = self.clock() + timeout
        for _ in range(256):
            frame = self._next(deadline)
            if frame is None:
                return None
            if not self._data_or_meta(frame):
                raise ProtocolError("Unsolicited KISS control/TX_DONE without pending TX")
            if self.received:
                return self.received.popleft()
        raise ProtocolError("KISS poll frame budget exceeded")

    def send_radio(self, radio):
        if not self.authorized or self.radio is None:
            raise ProtocolError("Probe-only session cannot transmit")
        if self.selected_radio() != self.radio:
            raise ProtocolError("Radio changed after preflight; refusing RF")
        started = self.clock()
        self._write(kiss_data(self.logical_port, radio))
        self.tx_started_frames += 1
        self.tx_started_bytes += len(radio)
        deadline = self.clock() + self.timeout
        for _ in range(256):
            frame = self._next(deadline)
            if frame is None:
                break
            if self._data_or_meta(frame):
                continue
            if frame[:2] == b"\6\xf8" and len(frame) == 3 and frame[2] in (0, 1):
                self.tx_done_wait_seconds += self.clock() - started
                if not frame[2]:
                    self.tx_failed += 1
                    raise ProtocolError("Modem reported failed RF TX")
                self.tx_frames += 1
                self.rf_bytes += len(radio)
                if len(self.radio) == 7:
                    self.last_airtime_seconds = lora_airtime(len(radio), *self.radio[1:4], self.radio[5])
                    self.estimated_airtime_seconds += self.last_airtime_seconds
                else:
                    self.last_airtime_seconds = self.clock() - started
                return
            raise ProtocolError("Expected actual F8 TX_DONE, not queue acceptance")
        raise ProtocolError("RF TX_DONE deadline exceeded; no automatic retransmission")


def lora_airtime(length, bandwidth_hz, sf, cr, preamble):
    symbol = (1 << sf) / bandwidth_hz
    low_rate = int(symbol >= .016)
    payload_symbols = 8 + max(0, math.ceil((8 * length - 4 * sf + 28 + 16) /
                                           (4 * (sf - 2 * low_rate))) * cr)
    return (preamble + 4.25 + payload_symbols) * symbol


def send_batch(link, source, batch, *, sleep=time.sleep, clock=time.monotonic, deadline=float("inf")):
    """Actual TX completion gates every next packet; no hidden RF retry."""
    def pause(seconds):
        remaining = min(seconds, max(0, deadline - clock()))
        while remaining > 0 and clock() < deadline:
            interval = min(remaining, .5)
            sleep(interval)
            remaining -= interval

    previous_data = False
    for reply in batch:
        if clock() >= deadline:
            return
        packet = decode_radio(reply)
        if packet is None:
            raise ValueError("Invalid queued OTA response")
        # Deployed receivers ask REQ_PROOF immediately after their final DATA.
        # Leave room for that half-duplex request. Wide negotiation explicitly
        # establishes a quiet DATA/PROOF train and needs no extra proof delay.
        if packet.message[0] == PROOF and previous_data and not packet.wide:
            request_airtime = (lora_airtime(9, *link.radio[1:4], link.radio[5])
                               if len(link.radio) == 7 else link.last_airtime_seconds)
            delay = max(.1, request_airtime * 1.5)
            pause(delay)
        if clock() >= deadline:
            return
        link.send_radio(reply)
        source.note_sent(reply)
        previous_data = packet.message[0] == DATA
        quiet = source.pace.quiet(link.last_airtime_seconds)
        if quiet:
            pause(quiet)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--expect-serial", required=True)
    parser.add_argument("--logical-port", required=True, type=int, choices=(0, 1))
    parser.add_argument("--expect-public-key")
    parser.add_argument("--expected-frequency", type=float, help="MHz, checked before every RF frame")
    parser.add_argument("--require-lab-lock", action="store_true")
    parser.add_argument("--serve", action="store_true", help="explicitly permit requested OTA RF responses")
    parser.add_argument("--zero-pacing", action="store_true")
    parser.add_argument("--advertise", action="store_true", help="one initial discovery advert; requires --serve")
    parser.add_argument("--mota", type=Path)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--tx-timeout", type=float, default=10)
    parser.add_argument("--minimum-pace", type=float, choices=(.25, .5, 1), default=.25,
                        help="adaptive floor; default0.25, starts at1.0")
    args = parser.parse_args(argv)
    if not 0 < args.duration <= 86400 or not 0 < args.tx_timeout <= 60:
        parser.error("Use positive bounded duration<=86400s and TX timeout<=60s")
    if args.serve and (not args.mota or args.expected_frequency is None):
        parser.error("--serve requires --mota and --expected-frequency")
    if (args.zero_pacing or args.advertise) and not args.serve:
        parser.error("--zero-pacing/--advertise require --serve")
    if args.expected_frequency is not None and (not math.isfinite(args.expected_frequency) or
                                               not 100 <= args.expected_frequency <= 2500):
        parser.error("Invalid explicit expected frequency")
    public = None
    if args.expect_public_key:
        try:
            public = bytes.fromhex(args.expect_public_key)
        except ValueError:
            parser.error("Expected public key must be64 hex characters")
        if len(public) != 32:
            parser.error("Expected public key must be64 hex characters")
    port = None
    source = link = None
    report = {"success": False, "rf_frames": 0}
    started = time.monotonic()
    try:
        # Validate/compress before owning USB or accepting live receiver requests.
        source = MotaSource(args.mota.read_bytes(), b"\0" * 4,
                            minimum_pace=args.minimum_pace) if args.serve else None
        if source:
            print("Preparing cached raw DEFLATE with Google Zopfli1000 before opening KISS.", file=sys.stderr)
            source.prepare()
        port, info = open_verified_port(args.port, args.expect_serial)
        link = KissLink(port, args.logical_port, timeout=args.tx_timeout)
        safety, identity = link.preflight(expected_frequency=(round(args.expected_frequency * 1000000)
            if args.expected_frequency is not None else None), expected_public_key=public,
            require_lab_lock=args.require_lab_lock, serve=args.serve, zero_pacing=args.zero_pacing)
        report.update(safety)
        if source:
            source.seeder_id = identity[:4]
            if args.advertise:
                link.send_radio(source.advert())
            until = time.monotonic() + args.duration
            while time.monotonic() < until:
                incoming = link.poll()
                if incoming is None:
                    continue
                batch = source.replies(incoming)
                send_batch(link, source, batch, deadline=until)
            report["serving_elapsed_seconds"] = args.duration - max(0, until - time.monotonic())
        report.update({"success": True, "usb_serial": info.serial_number})
    except (RuntimeError, OSError, ValueError) as error:
        report["error"] = str(error)
    finally:
        if port is not None:
            port.close()
        if source is not None:
            report["source"] = {"manifest_id": source.mid.hex(), "block_size": source.manifest.block_size,
                "blocks": source.manifest.block_count, "compression": "Google Zopfli1000 raw DEFLATE; smaller only",
                "pace": source.pace.report(), **source.stats}
        if link is not None:
            report.update({"rf_frames": link.tx_frames, "rf_bytes": link.rf_bytes,
                "rf_frames_started": link.tx_started_frames, "rf_bytes_started": link.tx_started_bytes,
                "kiss_bytes_written": link.kiss_bytes, "tx_failed": link.tx_failed,
                "tx_done_wait_seconds": link.tx_done_wait_seconds,
                "estimated_rf_airtime_seconds": link.estimated_airtime_seconds if args.logical_port == 1 else None,
                "airtime_is_estimate": True, "local_tx_done_is_delivery_ack": False})
        report["elapsed_seconds"] = time.monotonic() - started
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
