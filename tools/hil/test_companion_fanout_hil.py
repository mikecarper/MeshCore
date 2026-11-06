"""Offline fanout runner checks using memory endpoints, never serial hardware."""

from collections import deque
import copy
import hashlib
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import companion_fanout_hil as hil
from companion_fanout_codec import media_bundle, parse_chunk, parse_fetch
from split_radio_probe import make_probe


class MemoryEvidence:
    def __init__(self, kind="voice", labels=("left", "right")):
        self.frames = []
        self.report = {"media": {kind: {"observations": {label: [] for label in labels}}}}

    def event(self, kind, **values):
        row = {"kind": kind, "monotonic": hil.time.monotonic(), **values}
        if kind == "rx":
            self.frames.append(row)
        return row


class MemoryBus:
    def __init__(self, evidence):
        self.evidence = evidence
        self.nodes = []
        self.drop_announcement = set()
        self.drop_fetch = set()
        self.drop_chunk = set()
        self.duplicate_chunk = set()
        self.corrupt_chunk = set()
        self.wrong_chunk_profile = set()
        self.raw_tags = {}

    def send(self, source, command):
        if command[0] == 3:
            assert command[1] == 0
            text = source.node_name.encode() + b": " + command[7:]
            frame = bytes((17, 0x10, 0, 0, command[2], 0xff, 0)) + command[3:7] + text
            for target in self.nodes:
                if target is not source and target.label not in self.drop_announcement:
                    target.queue.append(frame)
        elif command[0] == 62:
            assert command[2] == 0xff
            slot, datatype, data = command[1], command[3:5], command[5:]
            chunk, fetch = parse_chunk(data), parse_fetch(data)
            for target in self.nodes:
                if target is source:
                    continue
                if fetch and target.label == "source" and source.label in self.drop_fetch:
                    continue
                identity = (target.label, chunk["index"]) if chunk else None
                if identity in self.drop_chunk:
                    continue
                payload = data
                if identity in self.corrupt_chunk:
                    payload = data[:-1] + bytes((data[-1] ^ 1,))
                tag = 0xa0 if identity in self.wrong_chunk_profile else 0xa1
                frame = bytes((27, 0xf8, tag, 0, slot, 0xff)) + datatype + bytes((len(payload),)) + payload
                target.queue.append(frame)
                if identity in self.duplicate_chunk:
                    target.queue.append(frame)
        elif command[0] == 25:
            assert command[1] == 0
            for target in self.nodes:
                if target is source:
                    continue
                tag = self.raw_tags.get(target.label, 0xa1)
                frame = bytes((0x84, 0xf4, 0x9c, tag)) + command[2:]
                self.evidence.event("rx", node=target.label, frame=frame.hex())
        else:
            raise AssertionError("Unexpected fake request: %d" % command[0])


class MemoryNode:
    def __init__(self, label, bus, key_byte):
        self.label, self.bus = label, bus
        self.key = bytes((key_byte,)) * 32
        self.node_name = "Bench " + label
        self.queue, self.requests = [], []
        bus.nodes.append(self)

    def request(self, payload, expected, timeout=8):
        assert expected == (0,)
        self.requests.append(bytes(payload))
        self.bus.send(self, bytes(payload))
        return b"\0"

    def drain(self):
        frames, self.queue = self.queue, []
        return frames


class CompanionFanoutRunnerTests(unittest.TestCase):
    run_id = bytes.fromhex("1020304050607080")

    def setup_bus(self, kind="voice"):
        evidence = MemoryEvidence(kind)
        bus = MemoryBus(evidence)
        source = MemoryNode("source", bus, 1)
        receivers = [MemoryNode("left", bus, 2), MemoryNode("right", bus, 3)]
        # Opaque wire-test bytes; genuine encoded fixtures are supplied to the campaign.
        content = bytes(range(156)) + bytes(range(144))
        bundle = media_bundle(kind, content, 0x12345678, duration=2)
        return bus, source, receivers, bundle, evidence

    def send(self, source, receivers, bundle, evidence):
        with patch.object(hil.time, "sleep"), patch.object(hil.time, "time", return_value=1700000000):
            return hil.send_media(source, receivers, bundle, 3, evidence)

    def test_exact_announcement_manifest_fetch_chunks_and_integrity(self):
        for kind in ("voice", "image"):
            bus, source, receivers, bundle, evidence = self.setup_bus(kind)
            outcome = self.send(source, receivers, bundle, evidence)
            self.assertEqual(source.requests[0], bytes((3, 0, 3)) + struct.pack("<I", 1700000000)
                             + bundle["envelope"].encode())
            self.assertTrue(source.requests[1][5:].startswith(b"HIL1"))
            self.assertTrue(all(command[0] == 62 and command[1:5] == b"\3\xff\x43\x56"
                                for command in source.requests[1:]))
            for node in receivers:
                self.assertEqual(len(node.requests), 1)
                request = parse_fetch(node.requests[0][5:])
                self.assertEqual((request["sid"], request["key6"], request["missing"]),
                                 (bundle["sid"], node.key[:6], None))
                self.assertTrue(outcome[node.label]["integrity_passed"])
                self.assertEqual(outcome[node.label]["retrieved_sha256"], bundle["sha256"])
                self.assertEqual(outcome[node.label]["retrieved_bytes"], 300)
                self.assertEqual(outcome[node.label]["chunks_received"], 2)
                self.assertTrue(outcome[node.label]["announcement_and_manifest_exact"])
                self.assertTrue(outcome[node.label]["fetch"]["received_exactly"])
            self.assertTrue(all(not node.queue for node in bus.nodes))

    def test_one_receiver_loss_duplicate_corruption_or_wrong_profile_does_not_hide_other(self):
        for failure in ("drop_chunk", "duplicate_chunk", "corrupt_chunk", "wrong_chunk_profile"):
            bus, source, receivers, bundle, evidence = self.setup_bus()
            getattr(bus, failure).add(("right", 1))
            outcome = self.send(source, receivers, bundle, evidence)
            self.assertTrue(outcome["left"]["integrity_passed"])
            self.assertFalse(outcome["right"]["integrity_passed"])
            self.assertIn("error", outcome["right"])
            expected_count = 1 if failure == "drop_chunk" else 3 if failure == "duplicate_chunk" else 2
            self.assertEqual(outcome["right"]["chunks_received"], expected_count)
            self.assertEqual(len(evidence.report["media"]["voice"]["observations"]["right"]), expected_count)

    def test_missing_announcement_or_fetch_cannot_become_success_from_complete_chunks(self):
        for failure in ("drop_announcement", "drop_fetch"):
            bus, source, receivers, bundle, evidence = self.setup_bus()
            getattr(bus, failure).add("right")
            outcome = self.send(source, receivers, bundle, evidence)
            self.assertTrue(outcome["left"]["integrity_passed"])
            self.assertFalse(outcome["right"]["integrity_passed"])
            self.assertEqual(outcome["right"]["retrieved_sha256"], bundle["sha256"])
            self.assertEqual(outcome["right"]["retrieved_bytes"], 300)
            if failure == "drop_announcement":
                self.assertFalse(outcome["right"]["announcement_and_manifest_exact"])
            else:
                self.assertFalse(outcome["right"]["fetch"]["received_exactly"])

    def test_raw_api_keeps_profile_proof_separate_from_configuration(self):
        bus, source, receivers, bundle, evidence = self.setup_bus()
        bus.raw_tags["right"] = 0
        with patch.object(hil.time, "sleep"):
            result = hil.raw_media_check(source, receivers, bundle, evidence)
        self.assertEqual(source.requests[0][:3], b"\x19\0\x57")
        self.assertEqual(result["left"], {"raw_clip_received": 1, "raw_clip_profile1": True,
                                        "raw_fetch_received_by_source": 1, "raw_fetch_profile1": True})
        self.assertEqual(result["right"]["raw_clip_received"], 1)
        self.assertFalse(result["right"]["raw_clip_profile1"])
        self.assertTrue(result["right"]["raw_fetch_profile1"])

    def test_primary_counts_unique_sequences_and_retains_duplicates_invalid_bytes_and_misses(self):
        evidence = MemoryEvidence()
        receivers = [SimpleNamespace(label="left"), SimpleNamespace(label="right")]
        for node in receivers:
            for sequence in range(40):
                if node.label == "right" and sequence == 3:
                    continue
                wire = make_probe(self.run_id, "L", sequence, direct=True,
                                  payload_size=40 if sequence % 2 == 0 else 160)
                if node.label == "right" and sequence == 5:
                    wire = wire[:-1] + bytes((wire[-1] ^ 1,))
                evidence.event("rx", node=node.label, frame=(b"\x88\x18\x9c" + wire).hex())
        wire = make_probe(self.run_id, "L", 0, direct=True, payload_size=40)
        evidence.event("rx", node="left", frame=(b"\x88\x18\x9c" + wire).hex())
        # Other runs and RAW_DATA app frames cannot inflate wire-proof counts.
        other = make_probe(bytes(8), "L", 0, direct=True)
        evidence.event("rx", node="left", frame=(b"\x88\0\0" + other).hex())
        evidence.event("rx", node="right", frame=(b"\x84\0\0\xa1" + wire[2:]).hex())
        before = {node.label: {"recv": 0xffffffff} for node in receivers}
        after = {node.label: {"recv": 39} for node in receivers}
        result = hil.primary_results(evidence, self.run_id, receivers, before, after)
        self.assertEqual(result["left"]["received"], 40)
        self.assertEqual(result["left"]["duplicate_observations"], 1)
        self.assertEqual(result["left"]["missing_sequences"], [])
        self.assertEqual(result["left"]["snr_median_db"], 6)
        self.assertEqual(result["left"]["rssi_median_dbm"], -100)
        self.assertEqual(result["left"]["packet_counter_delta"], {"recv": 40})
        self.assertEqual(result["right"]["received"], 38)
        self.assertEqual(result["right"]["missing_sequences"], [3, 5])
        self.assertEqual(result["right"]["invalid_matching_run_observations"], 1)

    def test_application_queue_uses_cmd10_until_exact_end_marker(self):
        calls, replies = [], deque([b"\x1bexample", b"\x11example", b"\x0a"])
        def request(payload, expected):
            calls.append((payload, expected))
            return replies.popleft()
        fake = SimpleNamespace(request=request)
        self.assertEqual(hil.Endpoint.drain(fake), [b"\x1bexample", b"\x11example"])
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(payload == b"\x0a" for payload, _ in calls))
        self.assertTrue(all(expected == (10, 16, 17, 27) for _, expected in calls))
        bad = SimpleNamespace(request=lambda *args: b"\x0aextra")
        with self.assertRaises(RuntimeError):
            hil.Endpoint.drain(bad)
        never_ends = SimpleNamespace(request=lambda *args: b"\x1bexample")
        with self.assertRaises(RuntimeError):
            hil.Endpoint.drain(never_ends)

    def test_one_async_reader_retains_pushes_while_only_responses_enter_transaction_queue(self):
        evidence = MemoryEvidence()
        node = object.__new__(hil.Endpoint)
        node.label, node.evidence, node.port = "left", evidence, None
        node.condition, node.responses = threading.Condition(), deque()
        node.running, node.failure = True, None
        frames = deque([b"\x88wire", b"\x84media", b"\x83", b"\x1bqueued", b"\0"])
        def read_frame(port, timeout):
            frame = frames.popleft()
            if not frames:
                node.running = False
            return frame
        node.reader = SimpleNamespace(read_frame=read_frame)
        node._read()
        self.assertIsNone(node.failure)
        self.assertEqual([row["frame"] for row in evidence.frames],
                         [frame.hex() for frame in (b"\x88wire", b"\x84media", b"\x83", b"\x1bqueued", b"\0")])
        self.assertEqual(list(node.responses), [b"\x1bqueued", b"\0"])

    def test_config_requires_physical_binding_for_serialless_radios_and_fixed_source(self):
        config = {"schema": 1, "expected_version": "v1.18.0.2-candidate-exact",
            "source": {"label": "source", "port": "/dev/serial/by-id/source",
                       "usb_serial": "28:84:85:B4:09:80", "board_contains": "XIAO ESP32-S3"},
            "receivers": [{"label": "left", "port": "/dev/serial/by-path/usb-left",
                           "usb_location": "1-2:1.0", "board_contains": "Heltec V4"}],
            "media": {kind: {"path": "/external/" + kind, "sha256": "0" * 64}
                      for kind in ("voice", "image")}}
        self.assertEqual(len(hil.validate_config(config)), 2)
        for change in ("missing_location", "tty_order", "wrong_source", "duplicate_location"):
            broken = copy.deepcopy(config)
            if change == "missing_location":
                del broken["receivers"][0]["usb_location"]
            elif change == "tty_order":
                broken["receivers"][0]["port"] = "/dev/ttyACM3"
            elif change == "wrong_source":
                broken["source"]["usb_serial"] = "wrong"
            else:
                duplicate = copy.deepcopy(broken["receivers"][0])
                duplicate.update(label="right", port="/dev/serial/by-path/usb-right")
                broken["receivers"].append(duplicate)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                hil.validate_config(broken)


if __name__ == "__main__":
    unittest.main()
