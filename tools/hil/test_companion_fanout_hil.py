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
        self.events = []
        self.report = {"media": {kind: {"observations": {label: [] for label in labels}}}}

    def event(self, kind, **values):
        row = {"kind": kind, "monotonic": hil.time.monotonic(), **values}
        self.events.append(row)
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
        self.raw_delays, self.raw_mutations, self.pending_raw = {}, {}, []
        self.delays, self.mutations, self.pending = {}, {}, []
        self.lock = threading.Lock()

    def deliver(self, target, frame, kind):
        key = (target.label, kind)
        if key in self.mutations:
            frame = self.mutations[key](frame)
        delay = self.delays.get(key, 0)
        if delay:
            self.pending.append((hil.time.monotonic() + delay, target, frame))
        else:
            target.queue.append(frame)

    def deliver_raw(self, now):
        ready = [item for item in self.pending_raw if item[0] <= now]
        self.pending_raw = [item for item in self.pending_raw if item[0] > now]
        for due, target, frame in ready:
            self.evidence.event("rx", node=target.label, frame=frame.hex(), monotonic=due)

    def send(self, source, command):
        if command[0] == 3:
            assert command[1] == 0
            text = source.node_name.encode() + b": " + command[7:]
            frame = bytes((17, 0x10, 0, 0, command[2], 0xff, 0)) + command[3:7] + text
            for target in self.nodes:
                if target is not source and target.label not in self.drop_announcement:
                    self.deliver(target, frame, "announcement")
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
                kind = "chunk" if chunk else "fetch" if fetch else "manifest"
                self.deliver(target, frame, kind)
                if identity in self.duplicate_chunk:
                    self.deliver(target, frame, kind)
        elif command[0] == 25:
            assert command[1] == 0
            for target in self.nodes:
                if target is source:
                    continue
                tag = self.raw_tags.get(target.label, 0xa1)
                frame = bytes((0x84, 0xf4, 0x9c, tag)) + command[2:]
                kind = "fetch" if parse_fetch(command[2:]) else "chunk"
                key = (target.label, kind)
                if key in self.raw_mutations:
                    frame = self.raw_mutations[key](frame)
                delay = self.raw_delays.get(key, 0)
                if delay:
                    self.pending_raw.append((hil.time.monotonic() + delay, target, frame))
                else:
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
        with self.bus.lock:
            ready = [item for item in self.bus.pending if item[0] <= hil.time.monotonic()]
            self.bus.pending = [item for item in self.bus.pending if item[0] > hil.time.monotonic()]
            for _, target, frame in ready:
                target.queue.append(frame)
            frames, self.queue = self.queue, []
            return frames


class FakeClock:
    def __init__(self, advance=None):
        self.now = 100.0
        self.advance = advance

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        assert duration >= 0
        self.now += duration
        if self.advance:
            self.advance(self.now)


class EndpointTransactionTests(unittest.TestCase):
    def endpoint(self):
        node = object.__new__(hil.Endpoint)
        node.label, node.evidence = "left", MemoryEvidence()
        node.condition, node.transaction = threading.Condition(), threading.Lock()
        node.responses, node.failure = deque(), None

        class Port:
            def __init__(self):
                self.writes, self.rx, self.flushes = [], bytearray(), 0

            @property
            def in_waiting(self):
                return len(self.rx)

            def read(self, size):
                data = bytes(self.rx[:size]); del self.rx[:size]
                return data

            def write(self, frame):
                self.writes.append(bytes(frame))
                return len(frame)

            def flush(self):
                self.flushes += 1

        node.port = Port()
        return node

    def test_timeout_blocks_later_writes_before_and_after_delayed_cli_ack(self):
        node, clock = self.endpoint(), FakeClock()
        with patch.object(hil.time, "monotonic", clock.monotonic), \
                patch.object(node.condition, "wait", clock.sleep):
            with self.assertRaisesRegex(RuntimeError, "timed out; reconnect required"):
                node.request(b"\x42set radio.xtrim 8,8", (0x1d,), timeout=.2)
            # Failure alone must stop the next write even before the late ACK.
            with self.assertRaisesRegex(RuntimeError, "unsynchronized session"):
                node.request(b"\x42get agc", (0x1d,))
        self.assertEqual(len(node.port.writes), 1)
        self.assertEqual(node.port.flushes, 1)

        late_ack = b"\x1dOK - oscillator calibration applying in2s"
        push = b"\x88late RF observation"
        for frame in (push, late_ack):
            node.port.rx.extend(b">" + struct.pack("<H", len(frame)) + frame)
        # Run the production reader and frame parser on the delayed fake bytes.
        reader = hil.DeviceFrameReader(hil.TransportCounters())
        def read_frame(port, timeout):
            frame = reader.read_frame(port, timeout)
            if frame == late_ack:
                node.running = False
            return frame
        node.reader, node.running = SimpleNamespace(read_frame=read_frame), True
        node._read()
        with self.assertRaisesRegex(RuntimeError, "unsynchronized session"):
            node.request(b"\x42get agc", (0x1d,))
        self.assertEqual(len(node.port.writes), 1)
        self.assertEqual(list(node.responses), [late_ack])
        self.assertEqual([bytes.fromhex(row["frame"]) for row in node.evidence.frames],
                         [push, late_ack])

    def test_request_preserves_async_reader_failure_and_blocks_next_write(self):
        node, clock = self.endpoint(), FakeClock()
        def reader_failed(duration):
            node.failure = "OSError('USB disconnected')"
            clock.sleep(duration)
        with patch.object(hil.time, "monotonic", clock.monotonic), \
                patch.object(node.condition, "wait", reader_failed):
            with self.assertRaisesRegex(RuntimeError, "USB disconnected"):
                node.request(b"\x16\x0e", (13,))
        self.assertEqual(node.failure, "OSError('USB disconnected')")
        with self.assertRaisesRegex(RuntimeError, "unsynchronized session"):
            node.request(b"\x42get agc", (0x1d,))
        self.assertEqual(len(node.port.writes), 1)

    def test_successful_requests_remain_usable(self):
        node = self.endpoint()
        write = node.port.write
        replies = deque((b"\x0dinfo", b"\x05identity", b"\x1dversion"))
        def immediate_reply(frame):
            size = write(frame)
            node.responses.append(replies.popleft())
            return size
        node.port.write = immediate_reply
        for payload, expected in ((b"\x16\x0e", 13), (b"\x01app", 5), (b"\x42ver", 0x1d)):
            self.assertEqual(node.request(payload, (expected,))[0], expected)
        self.assertIsNone(node.failure)
        self.assertEqual(len(node.port.writes), 3)
        self.assertFalse(node.responses)


class BaselineNode:
    """Command boundary with the real W12 BUSY precondition, not serial I/O."""
    def __init__(self, board, statuses=("off 5-8 radio1; stopping", "off 5-8 radio1; off")):
        self.board, self.statuses = board, deque(statuses)
        self.commands, self.requests = [], []
        self.stopped, self.agc = False, "16"

    def cli(self, command):
        self.commands.append(command)
        if command == "board": return self.board
        if command == "get radio.multisf":
            status = self.statuses[0]
            if len(self.statuses) > 1: self.statuses.popleft()
            self.stopped = status == "off 5-8 radio1; off"
            return status
        if command == "set radio2 off" and self.board == "Meshnology W12" and not self.stopped:
            raise RuntimeError("BUSY: W12 multi-SF still owns radio")
        if command.startswith("set agc "): self.agc = command.rsplit(" ", 1)[1]
        if command == "get agc": return self.agc
        if command == "get tx": return "-9"
        if command == "get radio": return "906.525,62.5,7,5"
        return "OK"

    def cli_reply(self, command):
        self.commands.append(command)
        if command == "get radio.rxps": return "> off,level=1"
        if command == "get agc.old": return "> 0"
        raise AssertionError(command)

    def request(self, payload, expected):
        self.requests.append(bytes(payload))
        if expected == (0,): return b"\0"
        assert expected == (5,)
        frame = bytearray(58)
        frame[:2] = b"\5\1"
        frame[4:36] = bytes(range(32))
        struct.pack_into("<II", frame, 48, 906525, 62500)
        frame[56:58] = b"\7\5"
        return bytes(frame)


class CompanionFanoutRunnerTests(unittest.TestCase):
    run_id = bytes.fromhex("1020304050607080")

    def test_configure_waits_for_w12_selected_and_runtime_off_before_profiles(self):
        node, clock = BaselineNode("Meshnology W12"), FakeClock()
        with patch.object(hil.time, "sleep", clock.sleep), \
                patch.object(hil.time, "monotonic", clock.monotonic):
            result = hil.configure(node)
        self.assertEqual(node.commands[:4], ["board", "set radio.multisf off",
                                            "get radio.multisf", "get radio.multisf"])
        self.assertEqual(node.commands.count("set radio.multisf off"), 1)
        self.assertLess(node.commands.index("get radio.multisf"), node.commands.index("set radio2 off"))
        self.assertEqual(result["multisf"], "off 5-8 radio1; off")
        self.assertEqual(result["agc"], "16")
        self.assertEqual(len(node.requests), 2)
        self.assertGreater(clock.now, 100)

    def test_w12_stop_timeout_never_reaches_profile_changes_or_rf_requests(self):
        for status in ("off 5-8 radio1; stopping", "on 5-8 radio1; off",
                       "off 5-8 radio1; off unexpected", "off 5-8 radio1; safe mode"):
            with self.subTest(status=status):
                node, clock = BaselineNode("Meshnology W12", (status,)), FakeClock()
                with patch.object(hil.time, "sleep", clock.sleep), \
                        patch.object(hil.time, "monotonic", clock.monotonic), \
                        self.assertRaisesRegex(RuntimeError, "W12 multi-SF did not stop within 12s"):
                    hil.configure(node)
                self.assertEqual(clock.now, 112)
                self.assertEqual(node.commands.count("set radio.multisf off"), 1)
                self.assertFalse(any(command.startswith(("set radio2", "set radio "))
                                     for command in node.commands))
                self.assertEqual(node.requests, [])

    def test_non_w12_baseline_does_not_probe_or_mutate_multisf(self):
        for board in ("Heltec V4", "Seeed XIAO ESP32-S3", "Meshnology W12 extra"):
            with self.subTest(board=board):
                node = BaselineNode(board)
                result = hil.configure(node, source=True)
                self.assertEqual(node.commands[0], "board")
                self.assertFalse(any("multisf" in command for command in node.commands))
                self.assertNotIn("multisf", result)
                self.assertEqual(result["agc"], "0")
                self.assertEqual(node.requests[1], b"\x0c\xf7")

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
        self.clock = FakeClock()
        with patch.object(hil.time, "sleep", self.clock.sleep), \
                patch.object(hil.time, "monotonic", self.clock.monotonic), \
                patch.object(hil.time, "time", return_value=1700000000):
            return hil.send_media(source, receivers, bundle, 3, evidence)

    def raw(self, source, receivers, bundle, evidence):
        self.clock = FakeClock(source.bus.deliver_raw)
        with patch.object(hil.time, "sleep", self.clock.sleep), \
                patch.object(hil.time, "monotonic", self.clock.monotonic):
            return hil.raw_media_check(source, receivers, bundle, evidence)

    def test_late_manifest_at_4_1_seconds_is_gathered_without_rf_retry(self):
        bus, source, receivers, bundle, evidence = self.setup_bus()
        bus.delays[("right", "manifest")] = 4.1
        bus.delays[("left", "chunk")] = 4.1
        result = self.send(source, receivers, bundle, evidence)
        self.assertTrue(all(row["integrity_passed"] for row in result.values()))
        manifest = next(row for row in evidence.events if row["kind"] == "media_observation"
                        and row["node"] == "right" and row["classification"] == "manifest")
        window = next(row for row in evidence.events if row["kind"] == "media_gather"
                      and row["phase"] == "manifest-before-fetch")
        self.assertGreaterEqual(manifest["monotonic"] - window["started"], 4.1)
        self.assertLess(manifest["monotonic"], window["deadline"])
        self.assertEqual(len(source.requests), 2 + bundle["count"])
        self.assertTrue(all(len(node.requests) == 1 for node in receivers))

    def test_current_manifest_delayed_into_fetch_phase_is_retained(self):
        bus, source, receivers, bundle, evidence = self.setup_bus()
        bus.delays[("right", "manifest")] = hil.MEDIA_GATHER_SECONDS + 4.1
        result = self.send(source, receivers, bundle, evidence)
        self.assertTrue(result["right"]["integrity_passed"])
        late = [row for row in evidence.events if row["kind"] == "media_observation"
                and row["node"] == "right" and row["classification"] == "manifest"]
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["phase"], "fetch:left")
        self.assertEqual(len(source.requests), 2 + bundle["count"])

    def test_foreign_sid_manifest_hash_tag_and_duplicate_are_rejected_and_retained(self):
        mutations = {
            "sid": lambda frame: frame.replace(b'"sid":305419896', b'"sid":305419897'),
            "hash": lambda frame: frame.replace(bundle["sha256"].encode(), b"0" * 64),
            "tag": lambda frame: frame[:2] + b"\xa0" + frame[3:],
            "channel": lambda frame: frame[:4] + b"\4" + frame[5:],
        }
        for fault in (*mutations, "duplicate"):
            with self.subTest(fault=fault):
                bus, source, receivers, bundle, evidence = self.setup_bus()
                if fault == "duplicate":
                    def duplicate(frame):
                        receivers[1].queue.append(frame)
                        return frame
                    bus.mutations[("right", "manifest")] = duplicate
                else:
                    bus.mutations[("right", "manifest")] = mutations[fault]
                result = self.send(source, receivers, bundle, evidence)
                self.assertTrue(result["left"]["integrity_passed"])
                self.assertFalse(result["right"]["integrity_passed"])
                self.assertIn("error", result["right"])
                retained = [row for row in evidence.events if row["kind"] == "media_observation"
                            and row["node"] == "right" and (row["error"] or row["duplicate"])]
                self.assertTrue(retained)
                self.assertEqual(len(source.requests), 2 + bundle["count"])

    def test_missing_content_expires_at_fixed_deadline_without_transmit_retry(self):
        bus, source, receivers, bundle, evidence = self.setup_bus()
        bus.drop_chunk.add(("right", 1))
        result = self.send(source, receivers, bundle, evidence)
        self.assertFalse(result["right"]["integrity_passed"])
        windows = [row for row in evidence.events if row["kind"] == "media_gather"]
        self.assertEqual(len(windows), 2 + len(receivers) + bundle["count"])
        self.assertEqual(windows[-1]["phase"], "chunks-final")
        self.assertTrue(all(row["finished"] == row["deadline"]
                            and row["deadline"] - row["started"] == hil.MEDIA_GATHER_SECONDS
                            for row in windows))
        self.assertEqual(self.clock.now, 100 + len(windows) * hil.MEDIA_GATHER_SECONDS)
        self.assertEqual(len(source.requests), 2 + bundle["count"])
        self.assertTrue(all(len(node.requests) == 1 for node in receivers))

    def test_foreign_fetch_sid_requester_and_tag_cannot_satisfy_source_receipt(self):
        for fault in ("sid", "requester", "tag", "duplicate"):
            with self.subTest(fault=fault):
                bus, source, receivers, bundle, evidence = self.setup_bus()
                def mutate(frame):
                    if fault == "sid":
                        return frame[:10] + bytes((frame[10] ^ 1,)) + frame[11:]
                    if fault == "requester":
                        return frame[:15] + bytes((frame[15] ^ 1,)) + frame[16:]
                    if fault == "tag":
                        return frame[:2] + b"\xa0" + frame[3:]
                    source.queue.append(frame)
                    return frame
                bus.mutations[("source", "fetch")] = mutate
                result = self.send(source, receivers, bundle, evidence)
                self.assertTrue(all(not row["integrity_passed"] for row in result.values()))
                self.assertTrue(all(not row["fetch"]["received_exactly"] for row in result.values()))
                self.assertTrue(all(row["fetch"]["source_errors"] for row in result.values()))
                self.assertEqual(len(source.requests), 2 + bundle["count"])
                self.assertTrue(all(len(node.requests) == 1 for node in receivers))

    def test_announcement_reserved_tag_is_not_invented_profile_proof(self):
        bus, source, receivers, bundle, evidence = self.setup_bus()
        bus.mutations[("right", "announcement")] = lambda frame: frame[:2] + b"\xa1" + frame[3:]
        result = self.send(source, receivers, bundle, evidence)
        self.assertTrue(result["left"]["integrity_passed"])
        self.assertFalse(result["right"]["integrity_passed"])
        self.assertIn("announcement", result["right"]["error"])

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
        result = self.raw(source, receivers, bundle, evidence)
        self.assertEqual(source.requests[0][:3], b"\x19\0\x57")
        self.assertEqual(result["left"], {"raw_clip_received": 1, "raw_clip_profile1": True,
                                        "raw_fetch_received_by_source": 1, "raw_fetch_profile1": True})
        self.assertEqual(result["right"]["raw_clip_received"], 1)
        self.assertFalse(result["right"]["raw_clip_profile1"])
        self.assertTrue(result["right"]["raw_fetch_profile1"])

    def test_raw_api_gathers_4_1_second_pushes_without_resending(self):
        bus, source, receivers, bundle, evidence = self.setup_bus()
        bus.raw_delays.update({("left", "chunk"): 4.1, ("right", "chunk"): 4.1,
                               ("source", "fetch"): 4.1})
        result = self.raw(source, receivers, bundle, evidence)
        for node in receivers:
            self.assertEqual(result[node.label], {
                "raw_clip_received": 1, "raw_clip_profile1": True,
                "raw_fetch_received_by_source": 1, "raw_fetch_profile1": True})
            self.assertEqual(len(node.requests), 1)
        self.assertEqual(len(source.requests), 1)
        self.assertEqual(self.clock.now, 100 + (1 + len(receivers)) * hil.MEDIA_GATHER_SECONDS)
        delayed = [row for row in evidence.frames if row["node"] == "left"]
        self.assertEqual(delayed[0]["monotonic"], 104.1)

    def test_raw_api_wrong_requester_payload_cannot_count_as_a_fetch(self):
        bus, source, receivers, bundle, evidence = self.setup_bus()
        # RAW_DATA is4 header bytes; fetch key6 starts6 bytes into its payload.
        bus.raw_mutations[("source", "fetch")] = lambda frame: (
            frame[:10] + bytes((frame[10] ^ 0x80,)) + frame[11:])
        result = self.raw(source, receivers, bundle, evidence)
        self.assertTrue(all(row["raw_clip_received"] == 1 for row in result.values()))
        self.assertTrue(all(row["raw_fetch_received_by_source"] == 0
                            and not row["raw_fetch_profile1"] for row in result.values()))
        self.assertEqual(len(source.requests), 1)
        self.assertTrue(all(len(node.requests) == 1 for node in receivers))

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
