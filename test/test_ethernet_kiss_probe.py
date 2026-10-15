"""Host-only regression tests; no radio, network connection or TAP is opened."""

import contextlib
import importlib.util
import io
from pathlib import Path
import queue
import struct
import sys
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/hil"))
SPEC = importlib.util.spec_from_file_location("ethernet_kiss_probe", ROOT / "tools/hil/ethernet_kiss_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class FakeEndpoint:
    def __init__(self):
        self.incoming = queue.Queue()
        self.peer = None
        self.drop = False
        self.frames = []

    def send(self, frame, fragment=0):
        self.frames.append(frame)
        if not self.drop:
            self.peer.incoming.put((frame, time.monotonic()))

    def receive(self, timeout):
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None


class EthernetKissProbeTests(unittest.TestCase):
    def test_tcp_endpoint_defaults_to_dedicated_kiss_port(self):
        with patch.object(probe.socket, "create_connection") as connect, \
                patch.object(probe.threading, "Thread"):
            endpoint = probe.KissEndpoint("tcp://bench-radio")
            connect.assert_called_once_with(("bench-radio", 5003), timeout=5)
            endpoint.close()

    def test_streaming_kiss_preserves_all_bytes(self):
        frame = bytes(range(256))[:255]
        encoded = probe.kiss_encode(frame)
        decoder = probe.KissDecoder()
        frames = []
        for byte in encoded:
            frames.extend(decoder.feed(bytes((byte,))))
        self.assertEqual(frames, [frame])
        self.assertEqual(decoder.errors, 0)

    def test_decoder_ignores_controls_and_rejects_invalid_escapes(self):
        decoder = probe.KissDecoder()
        encoded = b"noise\xc0\x01control\xc0\x00\xdbbad\xc0"
        self.assertEqual(decoder.feed(encoded), [])
        self.assertEqual(decoder.errors, 1)
        self.assertEqual(decoder.feed(probe.kiss_encode(b"valid")), [b"valid"])
        self.assertEqual(decoder.feed(b"\xc0\x00" + b"X" * 300 + b"\xc0"), [])
        self.assertEqual(decoder.errors, 2)

    def test_ipv4_and_transport_checksums_are_valid_both_directions(self):
        for kind in ("udp", "tcp", "icmp"):
            for reverse in (False, True):
                frame = probe.make_frame(kind, 255, 0x12345678, reverse)
                self.assertEqual(len(frame), 255)
                self.assertEqual(probe.checksum(frame[14:34]), 0)
                self.assertEqual(struct.unpack("!H", frame[16:18])[0], 241)
                transport = frame[34:]
                protocol = frame[23]
                pseudo = frame[26:34] + struct.pack("!BBH", 0, protocol, len(transport))
                self.assertEqual(probe.checksum(transport if kind == "icmp" else pseudo + transport), 0)
                self.assertIn(b"\xc0\xdb", transport)

    def test_arp_and_boundaries_are_distinct(self):
        arp = probe.make_frame("arp", 60, 1)
        self.assertEqual(len(arp), 60)
        self.assertEqual(arp[:6], b"\xff" * 6)
        self.assertEqual(arp[12:14], b"\x08\x06")
        self.assertNotEqual(arp, probe.make_frame("arp", 60, 2))
        self.assertNotEqual(probe.make_frame("raw", 14, 1), probe.make_frame("raw", 14, 2))
        for kind, size in probe.CASES:
            self.assertEqual(len(probe.make_frame(kind, size, 1)), size)
        for kind, size in (("raw", 13), ("udp", 41), ("tcp", 53), ("icmp", 256)):
            with self.assertRaises(ValueError):
                probe.make_frame(kind, size, 1)

    def test_probe_exact_integrity_and_both_directions(self):
        for duplex in (False, True):
            a, b = FakeEndpoint(), FakeEndpoint()
            a.peer, b.peer = b, a
            result = probe.run_probe(a, b, count=2, timeout=0.1, interval=0, duplex=duplex)
            self.assertTrue(result["passed"])
            self.assertEqual(result["counts"], {"a_to_b_attempted": 16, "a_to_b_received": 16,
                                                "b_to_a_attempted": 16, "b_to_a_received": 16})
            for transfer in result["transfers"]:
                self.assertEqual(transfer["sent_hex"], transfer["received_hex"])
                self.assertGreaterEqual(transfer["latency_ms"], 0)

    def test_lost_frames_are_not_retried_or_hidden(self):
        a, b = FakeEndpoint(), FakeEndpoint()
        a.peer, b.peer = b, a
        a.drop = True
        b.incoming.put((b"foreign traffic", time.monotonic()))
        result = probe.run_probe(a, b, count=1, timeout=0.001, interval=0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["counts"]["a_to_b_received"], 0)
        self.assertEqual(result["counts"]["b_to_a_received"], 8)
        self.assertEqual(len(a.frames), 8)
        self.assertEqual(len(result["unexpected"]), 1)
        self.assertEqual(result["retries"], 0)

    def test_plan_does_not_open_endpoints(self):
        with patch.object(probe, "KissEndpoint") as endpoint, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main(["--plan"]), 0)
        endpoint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
