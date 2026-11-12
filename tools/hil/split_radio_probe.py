"""Pure probe codec for split RX/bridge/TX radio tests; never opens hardware."""
from dataclasses import dataclass
import hashlib
import re
import struct

MAGIC = b"SPLIT1"


@dataclass(frozen=True)
class Probe:
    run: bytes
    source: str
    sequence: int
    payload: bytes
    wire: bytes | None = None
    route: int | None = None
    path_width: int | None = None
    path: bytes | None = None

    @property
    def key(self):
        return self.run.hex(), self.source, self.sequence

    @property
    def mesh_hash(self):
        # Packet::calculatePacketHash intentionally excludes the route/path.
        return hashlib.sha256(bytes((15,)) + self.payload).digest()[:8].hex()

    @property
    def original(self):
        return self.wire is not None and self.route in (0, 1) and self.path == b""

    def forwarded_by(self, public_key):
        if len(public_key) != 32:
            raise ValueError("Need the full independently verified center public key")
        return self.wire is not None and self.route in (0, 1) and \
            self.path_width in (1, 2, 3) and self.path == public_key[:self.path_width]

    def record(self):
        return {"run_id": self.run.hex(), "source": self.source, "sequence": self.sequence,
                "mesh_hash": self.mesh_hash, "payload_hex": self.payload.hex(),
                "wire_hex": None if self.wire is None else self.wire.hex(),
                "route": self.route, "path_width": self.path_width,
                "path_hex": None if self.path is None else self.path.hex()}


def make_probe(run, source, sequence, *, payload_size=40, direct=False):
    """Return a V1 RAW_CUSTOM packet, zero-hop flood by default."""
    if len(run) != 8 or source not in ("L", "R") or not 0 <= sequence <= 0xffffffff:
        raise ValueError("Need run[8], source L/R and uint32 sequence")
    fixed = MAGIC + run + source.encode("ascii") + struct.pack("<I", sequence)
    if not len(fixed) <= payload_size <= 184:
        raise ValueError("Probe does not fit the MeshCore application payload")
    payload = fixed + bytes(index & 255 for index in range(payload_size-len(fixed)))
    return bytes((0x3E if direct else 0x3D, 0)) + payload


def _payload_probe(payload, expected_run):
    if not 19 <= len(payload) <= 184 or not payload.startswith(MAGIC):
        return None
    run, source = payload[6:14], chr(payload[14])
    if source not in ("L", "R") or (expected_run is not None and run != expected_run):
        return None
    return Probe(run, source, struct.unpack_from("<I", payload, 15)[0], bytes(payload))


def parse_probe(wire, *, expected_run=None):
    """Parse complete wire bytes, preserving path evidence separately from hash."""
    wire = bytes(wire)
    if not 2 <= len(wire) <= 255 or wire[0] >> 6 or (wire[0] >> 2) & 15 != 15:
        return None
    route = wire[0] & 3
    path_offset = 5 if route in (0, 3) else 1
    if path_offset >= len(wire):
        return None
    encoded = wire[path_offset]
    width, count = (encoded >> 6)+1, encoded & 63
    if width > 3 or width * count > 64:
        return None
    end = path_offset + 1 + count * width
    if end > len(wire):
        return None
    payload = _payload_probe(wire[end:], expected_run)
    if payload is None:
        return None
    return Probe(payload.run, payload.source, payload.sequence, payload.payload,
                 wire, route, width, wire[path_offset+1:end])


def companion_send_command(wire, priority=0):
    """CMD_SEND_RAW_PACKET (65), excluding the outer USB framing."""
    if not 0 <= priority <= 255 or not 2 <= len(wire) <= 174:
        raise ValueError("Probe must fit the conservative Companion host frame")
    return bytes((65, priority)) + bytes(wire)


def inspect_push(frame, *, expected_run=None, observer=None,
                 configured_profile=None, frequency_mhz=None):
    """Decode push 88 or 84. Configuration labels are never treated as RF proof.

    Push 88 has full wire bytes but no receive-profile field. Push 84 reports
    direct RAW_CUSTOM payload and may carry A0/A1, but has no path/wire bytes.
    """
    if configured_profile not in (None, 0, 1):
        raise ValueError("Configured profile must be primary 0 or radio2 1")
    frame = bytes(frame)
    if len(frame) < 4 or frame[0] not in (0x88, 0x84):
        return None
    if frame[0] == 0x88:
        probe = parse_probe(frame[3:], expected_run=expected_run)
        profile = None
    else:
        probe = _payload_probe(frame[4:], expected_run)
        profile = {0xA0: 0, 0xA1: 1}.get(frame[3])
    if probe is None:
        return None
    snr, rssi = struct.unpack("bb", frame[1:3])
    return {**probe.record(), "push": frame[0], "snr_db": snr/4, "rssi_dbm": rssi,
            "radio_profile": profile, "observer": observer,
            "configured_profile": configured_profile, "frequency_mhz": frequency_mhz}


def packet_stats(frame):
    """Exact CMD_GET_STATS/2 reply: two-byte tag then seven LE uint32 counters."""
    if len(frame) != 30 or frame[:2] != bytes((24, 2)):
        raise ValueError("Expected exact Companion packet-stats response")
    return dict(zip(("recv", "sent", "flood_tx", "direct_tx", "flood_rx", "direct_rx", "recv_errors"),
                    struct.unpack_from("<7I", frame, 2)))


def profile_counters(reply):
    """Read the complete per-profile counter pair from get radio2.status."""
    matches = re.findall(r"\bRX=(\d+),(\d+)\s+TX=(\d+),(\d+)(?=\s|;|$)", reply)
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous per-profile RX/TX counters")
    values = tuple(map(int, matches[0]))
    return {"rx": list(values[:2]), "tx": list(values[2:])}


def counter_delta(before, after):
    if before.keys() != after.keys() or any(type(x) is not int or not 0 <= x <= 0xffffffff
                                         for record in (before, after) for x in record.values()):
        raise ValueError("Need matching unsigned 32-bit counter sets")
    return {name: (after[name]-value) & 0xffffffff for name, value in before.items()}


def kiss_frame(command, payload=b""):
    """Generic KISS encoder, including DATA port 0/1 for dedicated modem HIL."""
    if not 0 <= command <= 255 or len(payload) > 511:
        raise ValueError("Invalid KISS command/frame size")
    body = bytes((command,)) + bytes(payload)
    return b"\xc0" + body.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"
