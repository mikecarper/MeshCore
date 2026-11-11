"""Pure Companion and Open TAK media codecs for fanout hardware tests.

No function in this module opens hardware or files, starts a codec, or performs
network I/O. Callers supply already encoded media. Synthetic test bytes prove
transport integrity only; they do not prove that a clip or image is playable.

Media layout follows Open TAK's lib/vox/voice_wire.dart:
https://github.com/jade-on-mesh/meshcore-open-tak/blob/main/lib/vox/voice_wire.dart
Companion command/reply layouts follow examples/companion_radio/MyMesh.cpp.
"""

from __future__ import annotations

import hashlib
import re
import struct
from typing import TypedDict


MAX_CONTENT_PER_CHUNK = 156
MAX_PACKETS = 255
MAX_HOST_FRAME = 172  # Conservative common Companion frame budget.
MEDIA_DATA_TYPE = 0x5643
VOICE_FRAME_BYTES = {0: 4, 1: 6, 2: 6, 3: 7, 4: 7, 5: 8, 6: 8}


class ChannelFrame(TypedDict):
    data: bytes
    channel: int
    profileTag: int
    radio_profile: int | None
    snr_db: float
    data_type: int
    path_len: int


class GroupTextFrame(TypedDict):
    text: str
    channel: int
    profileTag: int
    radio_profile: int | None
    snr_db: float
    text_type: int
    path_len: int
    timestamp: int


def _uint(value, maximum, label):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("%s must be an unsigned integer <= %d" % (label, maximum))
    return value


def _bytes(value, label):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("%s must be bytes" % label)
    return bytes(value)


def _sid(value):
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9a-fA-F]{8}", value):
            raise ValueError("SID text must contain exactly eight hexadecimal digits")
        return int(value, 16)
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value)
        if len(value) != 4:
            raise ValueError("SID bytes must contain exactly four big-endian bytes")
        return int.from_bytes(value, "big")
    return _uint(value, 0xffffffff, "SID")


def _base36(value):
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, digit = divmod(value, 36)
        result = alphabet[digit] + result
    return result


def _announcement(kind, sid, mode, count, duration, aspect):
    last = duration if kind == "voice" else aspect
    prefix = "VE3" if kind == "voice" else "VI1"
    head = ":".join((prefix, _base36(sid), _base36(mode),
                     _base36(count), _base36(last)))
    return head if kind == "voice" else head + " IMAGE"


def encode_chunk(sid, index, data, *, salt=None):
    """Encode a standard 0x56 packet, or a 0x57 packet with trailing salt."""
    sid = _sid(sid)
    index = _uint(index, MAX_PACKETS - 1, "chunk index")
    data = _bytes(data, "chunk data")
    if not 1 <= len(data) <= MAX_CONTENT_PER_CHUNK:
        raise ValueError("Chunk data must contain 1..156 bytes")
    if salt is not None:
        salt = _uint(salt, 255, "salt")
    return bytes((0x56 if salt is None else 0x57,)) + struct.pack(">IB", sid, index) + \
        data + (b"" if salt is None else bytes((salt,)))


def parse_chunk(payload):
    """Parse bounded 0x56/0x57 media bytes; malformed input returns None."""
    try:
        payload = _bytes(payload, "chunk")
    except ValueError:
        return None
    if not payload or payload[0] not in (0x56, 0x57):
        return None
    salted = payload[0] == 0x57
    end = len(payload) - int(salted)
    if not 7 <= end <= 6 + MAX_CONTENT_PER_CHUNK or payload[5] >= MAX_PACKETS:
        return None
    sid = int.from_bytes(payload[1:5], "big")
    return {"sid": sid, "sid_hex": "%08x" % sid, "index": payload[5],
            "data": payload[6:end], "salt": payload[-1] if salted else None,
            "magic": payload[0]}


def media_bundle(kind, content, sid, *, mode=1, duration=1, aspect=100):
    """Make a transport manifest for externally supplied Codec2 or AV1 bytes.

    ``kind`` is voice or image. Voice wire modes 0..6 require complete Codec2
    frames. An image always advertises AV1 wire mode 40 (base36 "14"). Content
    is never transcoded or claimed to be playable by this function.
    """
    if kind not in ("voice", "image"):
        raise ValueError("Media kind must be voice or image")
    content, sid = _bytes(content, "content"), _sid(sid)
    duration = _uint(duration, 600, "duration")
    aspect = _uint(aspect, 600, "aspect")
    if not content:
        raise ValueError("Media content must not be empty")
    if kind == "voice":
        if type(mode) is not int or mode not in VOICE_FRAME_BYTES:
            raise ValueError("Voice requires Codec2 wire mode 0..6")
        frame_size = VOICE_FRAME_BYTES[mode]
        if len(content) % frame_size:
            raise ValueError("Voice content must contain complete Codec2 frames")
        chunk_size = MAX_CONTENT_PER_CHUNK // frame_size * frame_size
    else:
        mode, chunk_size = 40, MAX_CONTENT_PER_CHUNK
    count = (len(content) + chunk_size - 1) // chunk_size
    if not 1 <= count <= MAX_PACKETS:
        raise ValueError("Media content must fit in 1..255 chunks")
    chunks = [encode_chunk(sid, index, content[start:start + chunk_size])
              for index, start in enumerate(range(0, len(content), chunk_size))]
    return {"kind": kind, "sid": sid, "sid_hex": "%08x" % sid, "mode": mode,
            "duration": duration, "aspect": aspect,
            "envelope": _announcement(kind, sid, mode, count, duration, aspect),
            "chunks": chunks, "sha256": hashlib.sha256(content).hexdigest(),
            "content_bytes": len(content), "count": count}


def encode_fetch(sid, key6, missing=None):
    """Encode the standard fetch request. None requests the entire clip.

    Missing indices are encoded as a sorted set, matching Open TAK. A larger
    request is rejected instead of silently discarding indices; callers can
    split requests into batches of at most 140 indices.
    """
    sid, key6 = _sid(sid), _bytes(key6, "requester key6")
    if len(key6) != 6:
        raise ValueError("Requester key6 must contain exactly six bytes")
    if missing is None:
        indices, flags = [], 0
    else:
        try:
            indices = sorted({_uint(index, MAX_PACKETS - 1, "missing index")
                              for index in missing})
        except TypeError as error:
            raise ValueError("Missing indices must be an iterable of integers") from error
        if len(indices) > 140:
            raise ValueError("Split missing indices into batches of at most 140")
        flags = 1
    return b"\x72" + sid.to_bytes(4, "big") + bytes((flags,)) + key6 + \
        bytes((len(indices),)) + bytes(indices)


def parse_fetch(payload):
    """Parse exact basic/salt/path fetch layout; no unknown flags or suffixes.

    Repeated indices are legal wire salts and are returned as a sorted set.
    Path hashes retain their encoded width and bytes. Marked-packet bit 0x08
    is deliberately outside this raw-media test contract.
    """
    try:
        payload = _bytes(payload, "fetch")
    except ValueError:
        return None
    if not 13 <= len(payload) <= MAX_HOST_FRAME - 5 or payload[0] != 0x72:
        return None
    flags, count = payload[5], payload[12]
    end = 13 + count
    if flags & ~7 or end > len(payload) or (not flags & 1 and count):
        return None
    listed = payload[13:end]
    if any(index >= MAX_PACKETS for index in listed):
        return None
    path, path_len, path_width = None, None, None
    if flags & 4:
        if end >= len(payload):
            return None
        path_len = payload[end]
        path_width, hops = (path_len >> 6) + 1, path_len & 63
        path_bytes = hops * path_width
        if path_width > 3 or path_bytes > 64 or len(payload) != end + 1 + path_bytes:
            return None
        path = payload[end + 1:]
    elif len(payload) != end:
        return None
    sid = int.from_bytes(payload[1:5], "big")
    return {"sid": sid, "sid_hex": "%08x" % sid, "key6": payload[6:12],
            "flags": flags, "missing": sorted(set(listed)) if flags & 1 else None,
            "salt_ok": bool(flags & 2), "path": path,
            "path_len": path_len, "path_width": path_width}


def assemble(bundle, chunks):
    """Require one exact copy of every expected chunk, then verify SHA256.

    Salted retransmissions have the same content and are accepted individually.
    Duplicate indices, including identical copies, are errors: callers must
    explicitly account for retries before making a successful-transfer claim.
    """
    try:
        expected = list(bundle["chunks"])
        parsed = [parse_chunk(chunk) for chunk in expected]
        count = bundle["count"]
        sid = _sid(bundle["sid"])
        if type(count) is not int or not 1 <= count <= MAX_PACKETS or len(expected) != count:
            raise ValueError("Manifest has an invalid chunk count")
        for index, chunk in enumerate(parsed):
            if chunk is None or chunk["sid"] != sid or chunk["index"] != index or chunk["salt"] is not None:
                raise ValueError("Manifest chunks must be ordered standard packets for one SID")
        expected_content = b"".join(chunk["data"] for chunk in parsed)
        canonical = media_bundle(bundle["kind"], expected_content, sid, mode=bundle["mode"],
                                 duration=bundle["duration"], aspect=bundle["aspect"])
        for key in ("chunks", "envelope", "sha256", "content_bytes", "count"):
            if canonical[key] != bundle[key]:
                raise ValueError("Manifest %s does not match its content" % key)
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete media manifest") from error
    received = {}
    for payload in chunks:
        chunk = parse_chunk(payload)
        if chunk is None:
            raise ValueError("Malformed or truncated media chunk")
        index = chunk["index"]
        if chunk["sid"] != sid:
            raise ValueError("Media chunk has a different SID")
        if index >= count:
            raise ValueError("Media chunk index is outside the manifest")
        if index in received:
            raise ValueError("Duplicate media chunk index")
        if chunk["data"] != parsed[index]["data"]:
            raise ValueError("Media chunk differs from the expected content")
        received[index] = chunk["data"]
    if len(received) != count:
        raise ValueError("Media transfer is missing expected chunks")
    content = b"".join(received[index] for index in range(count))
    if len(content) != bundle["content_bytes"] or hashlib.sha256(content).hexdigest() != bundle["sha256"]:
        raise ValueError("Media transfer length or SHA256 differs from the manifest")
    return content


def channel_data_command(slot, payload, datatype=MEDIA_DATA_TYPE):
    """CMD_SEND_CHANNEL_DATA (62), flood route, without outer USB framing."""
    slot = _uint(slot, 255, "channel slot")
    datatype = _uint(datatype, 0xffff, "data type")
    payload = _bytes(payload, "channel data")
    # Reserve the nine-byte receive header on a conservative 172-byte frame.
    if not payload or len(payload) > MAX_HOST_FRAME - 9:
        raise ValueError("Channel payload must contain 1..163 bytes")
    return bytes((62, slot, 0xff)) + struct.pack("<H", datatype) + payload


def parse_channel_frame(frame) -> ChannelFrame | None:
    """Decode reply 27's nine-byte prefix; preserve an unknown profile tag."""
    try:
        frame = _bytes(frame, "channel frame")
    except ValueError:
        return None
    if not 9 <= len(frame) <= MAX_HOST_FRAME or frame[0] != 27 or len(frame) != 9 + frame[8]:
        return None
    return {"data": frame[9:], "channel": frame[4], "profileTag": frame[2],
            "radio_profile": {0xa0: 0, 0xa1: 1}.get(frame[2]),
            "snr_db": struct.unpack("b", frame[1:2])[0] / 4,
            "data_type": int.from_bytes(frame[6:8], "little"), "path_len": frame[5]}


def group_text_command(slot, text, timestamp):
    """CMD_SEND_CHANNEL_TXT_MSG (3), plain text, without outer USB framing."""
    slot, timestamp = _uint(slot, 255, "channel slot"), _uint(timestamp, 0xffffffff, "timestamp")
    if not isinstance(text, str) or "\0" in text:
        raise ValueError("Group text must be a string without NUL")
    encoded = text.encode("utf-8")
    if not encoded or len(encoded) > MAX_HOST_FRAME - 7:
        raise ValueError("Group text must contain 1..165 UTF-8 bytes")
    return bytes((3, 0, slot)) + struct.pack("<I", timestamp) + encoded


def parse_group_text_response(frame) -> GroupTextFrame | None:
    """Decode reply 17 (v3) only, including its four-byte timestamp."""
    try:
        frame = _bytes(frame, "group text frame")
    except ValueError:
        return None
    if not 11 <= len(frame) <= MAX_HOST_FRAME or frame[0] != 17 or frame[6] != 0:
        return None
    try:
        text = frame[11:].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\0" in text:
        return None
    return {"text": text, "channel": frame[4], "profileTag": frame[2],
            "radio_profile": {0xa0: 0, 0xa1: 1}.get(frame[2]),
            "snr_db": struct.unpack("b", frame[1:2])[0] / 4,
            "text_type": frame[6], "path_len": frame[5],
            "timestamp": int.from_bytes(frame[7:11], "little")}
