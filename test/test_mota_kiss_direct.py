"""Host-only direct KISS OTA source tests; no serial device is opened."""
from collections import deque
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/lora_ota"))
import mota_kiss_direct as ota


def container(payload, block_size=2048):
    ml = ota.motalib
    leaves = ml.leaf_hashes(payload, block_size)
    manifest = ml.Manifest(flags=ml.FLAG_FULL, target_id=123, fw_version=0x01170000,
        image_size=len(payload), payload_size=len(payload), block_size_log2=block_size.bit_length() - 1,
        merkle_root=ml.merkle_root(leaves), image_hash=ml.mh32(payload), leaves=leaves)
    return ml.build_container(manifest, payload)


def request(source, *items, wide=True):
    message = bytes((ota.REQ,)) + source.mid + b"".join(struct.pack("<HH", *item) for item in items)
    return ota.wrap_radio(message, wide=wide)


def hw(code, body=b""):
    return ota.encode_frame(6, bytes((code,)) + body)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += .001
        return self.now


class FakePort:
    def __init__(self):
        self.decoder, self.pending, self.writes = ota.Decoder(), deque(), []
        self.public = bytes(range(32))
        self.radio = (ota.GUARD_HZ, 500000, 5, 5, 2, 512, 0)
        self.overrides = {}
        self.tx_reply = hw(0xF8, b"\1")
        self.before_tx_done = b""

    def write(self, data):
        for frame in self.decoder.feed(data):
            self.writes.append(frame)
            if frame[0] in (0, 0x10):
                result = self.before_tx_done + self.tx_reply
            elif frame[0] == 6:
                command = frame[1]
                bodies = {0x21: struct.pack("<I", ota.GUARD_HZ), 0x11: b"\2\0",
                    0x01: self.public, 0x17: b"", 0x20: ota.SECONDARY_STRUCT.pack(*self.radio),
                    0x1E: bytes(15),
                    0x0B: ota.PRIMARY_STRUCT.pack(*self.radio[:4])}
                result = self.overrides.get(command, hw(command | 0x80, bodies.get(command, b"")))
            else:
                result = b""
            self.pending.extend(result[i:i + 3] for i in range(0, len(result), 3))
        return len(data)

    def read(self, _):
        return self.pending.popleft() if self.pending else b""

    def flush(self):
        pass


class DirectWireTests(unittest.TestCase):
    def source(self, payload=None, block_size=2048, compressor=lambda data: data):
        return ota.MotaSource(container(payload or bytes(range(256)) * 8, block_size), b"seed", compressor=compressor)

    def test_canonical_wire_maximum_and_kiss_binary_escaping(self):
        for port in (0, 1):
            radio = ota.wrap_radio(bytes((ota.DATA,)) + bytes((0xC0, 0xDB)) * 126, wide=True)
            self.assertEqual(len(radio), 255)
            frame = ota.kiss_data(port, radio)
            decoder = ota.Decoder()
            decoded = []
            for byte in frame:
                decoded.extend(decoder.feed(bytes((byte,))))
            self.assertEqual(decoded, [bytes((port << 4,)) + radio])
        for raw in (b"\xf2\1\6", b"\xf2\0\1", b"\xf2\0\2", b"\xf2\0\6" + b"x" * 253,
                    b"\x30\0\6", b"\x33\0\6", b"\x32\xc0\6"):
            self.assertIsNone(ota.decode_radio(raw))
        with self.assertRaises(ValueError):
            ota.wrap_radio(b"\7" * 254, wide=True)

    def test_zero_hop_2k_raw_is_nine_wide_vs_twelve_normal_frames(self):
        source = self.source()
        for wide, size, frames in ((True, 240, 9), (False, 171, 12)):
            replies = source.replies(request(source, (0, 0xAFFF), wide=wide))
            messages = [ota.decode_radio(r).message for r in replies]
            data = [m for m in messages if m[0] == ota.DATA]
            self.assertEqual(len(data), frames)
            self.assertEqual(messages[-1][0], ota.PROOF)
            reconstructed = bytearray()
            for number, message in enumerate(data):
                packed = struct.unpack_from("<H", message, 7)[0]
                self.assertEqual((packed & 0x7800) >> 11, number)
                self.assertEqual((packed & 2047) + 1, 2048)
                self.assertEqual(message[9:13], ota.motalib.mh4(source.block(0)))
                self.assertLessEqual(len(message[13:]), size)
                reconstructed.extend(message[13:])
            self.assertEqual(reconstructed, source.block(0))
            self.assertEqual(max(map(len, replies)), 255 if wide else 186)

    def test_1k_tail_descriptor_and_legacy160_fallback(self):
        source = self.source(bytes(range(256)) * 5, block_size=1024)
        self.assertEqual(len(source.replies(request(source, (0, 0x9FFF)))), 6)  # 5DATA+PROOF
        legacy = source.replies(request(source, (0, 0xFFFF), wide=False))
        data = [ota.decode_radio(r).message for r in legacy if ota.decode_radio(r).message[0] == ota.DATA]
        self.assertEqual(len(data), 7)
        self.assertEqual([struct.unpack_from("<H", m, 7)[0] for m in data], [i * 160 for i in range(7)])
        tail = source.replies(request(source, (1, 0x9FFF)))
        self.assertEqual(len(tail), 3)  # 256B tail /240 +proof
        self.assertEqual(struct.unpack_from("<H", ota.decode_radio(tail[0]).message, 7)[0] & 1023, 255)

    def test_compressed_stream_identity_cached_and_resumable_masks(self):
        calls = []
        def compress(data):
            calls.append(data)
            obj = zlib.compressobj(9, wbits=-15)
            return obj.compress(data) + obj.flush()
        source = self.source(b"abc" * 650, block_size=2048, compressor=compress)
        source.prepare()
        source.prepare()  # deterministic preparation may be called independently
        replies = source.replies(request(source, (0, 0xEFFF)))
        message = ota.decode_radio(replies[0]).message
        encoded = message[13:]
        self.assertEqual(zlib.decompress(encoded, -15), source.block(0))
        self.assertEqual(message[9:13], ota.motalib.mh4(encoded))
        before = len(calls)
        self.assertEqual(source.replies(request(source, (0, 0xE001))), replies)
        self.assertEqual(len(calls), before)
        source.note_sent(replies[0]); source.note_sent(replies[0])
        self.assertEqual(source.stats["repeated_data_frames"], 1)

    def test_compression_non_winner_stays_raw_and_zopfli_uses_1000(self):
        source = self.source(compressor=lambda data: data + b"bigger")
        source.prepare()
        self.assertEqual(source.compressed[0], source.block(0))
        fake = SimpleNamespace(MAXIMUM_ITERATIONS=1000, raw_deflate_compress=Mock(return_value=b"compressed"))
        with patch.dict(sys.modules, {"zopfli_compress": fake}):
            self.assertEqual(ota.zopfli1000(b"payload"), b"compressed")
        fake.raw_deflate_compress.assert_called_once_with(b"payload", numiterations=1000)

    def test_discovery_manifest_and_merkle_proof_use_real_container(self):
        source = self.source(bytes(range(256)) * 21)
        query = b"\2seed" + source.digest + struct.pack("<II", 123, 0)
        have = ota.decode_radio(source.replies(ota.wrap_radio(query, flood=True))[0]).message
        self.assertEqual(have[:12], b"\3seed" + source.digest + b"\0\1\1")
        self.assertEqual(have[12:16], source.mid)
        metadata = ota.wrap_radio(b"\4" + source.mid + b"\xff\xff")
        messages = [ota.decode_radio(r).message for r in source.replies(metadata)]
        self.assertEqual(b"".join(m[7:] for m in messages), source.manifest_bytes)
        self.assertEqual(len(source.manifest_bytes), 197)
        proof = source.proof(2)
        self.assertEqual(proof[8:], ota.motalib.proof_siblings(source.manifest.leaves, 2))
        self.assertTrue(ota.motalib.verify_proof(source.manifest.leaves[2], 2,
            ota.motalib.merkle_proof(source.manifest.leaves, 2), source.mid, source.manifest.block_count))

    def test_malformed_batch_wrong_geometry_and_routed_wide_are_rejected(self):
        source = self.source()
        for raw in (request(source, (0, 0x9FFF)), request(source, (0, 0xFFFF)),
                    request(source, (0, 0xAFFF), (99, 0xAFFF)),
                    request(source, *((0, 0xAFFF),) * 5),
                    ota.wrap_radio(b"\6" + source.mid + b"\0\0\xff" , wide=True),
                    b"\x32\1A" + b"\6" + source.mid + struct.pack("<HH", 0, 0xAFFF)):
            self.assertEqual(source.replies(raw), [])
        self.assertGreater(source.stats["invalid_requests"], 0)
        damaged = bytearray(container(b"valid" * 400)); damaged[-6] ^= 1
        with self.assertRaises(ValueError):
            ota.MotaSource(bytes(damaged), b"seed")

    def test_legacy_flood_path_uses_ordinary_flood_reply_not_wide(self):
        source = self.source()
        message = b"\6" + source.mid + struct.pack("<HH", 0, 0xAFFF)
        replies = source.replies(b"\x31\2AB" + message)
        self.assertEqual(len(replies), 13)
        self.assertTrue(all(reply[:2] == b"\x31\0" for reply in replies))
        self.assertTrue(all(not ota.decode_radio(reply).wide for reply in replies))


class KissSafetyTests(unittest.TestCase):
    def link(self, port=None):
        return ota.KissLink(port or FakePort(), 1, timeout=.5, clock=Clock())

    def test_reads_wait_for_one_byte_or_drain_only_available_bounded_bytes(self):
        class SizeAwarePort:
            def __init__(self, data=b""):
                self.buffer = data
                self.read_sizes = []

            @property
            def in_waiting(self):
                return len(self.buffer)

            def read(self, size):
                self.read_sizes.append(size)
                data, self.buffer = self.buffer[:size], self.buffer[size:]
                return data

        empty = SizeAwarePort(); link = self.link(empty)
        self.assertIsNone(link._next(.005))
        self.assertTrue(empty.read_sizes)
        self.assertTrue(all(size == 1 for size in empty.read_sizes))

        reply = hw(0xF8, b"\1")
        short = SizeAwarePort(reply); link = self.link(short)
        self.assertEqual(link._next(.1), b"\6\xf8\1")
        self.assertEqual(short.read_sizes, [len(reply)])

        backlog = SizeAwarePort(bytes(2048) + reply); link = self.link(backlog)
        self.assertEqual(link._next(.1), b"\6\xf8\1")
        self.assertEqual(backlog.read_sizes, [1024, 1024, len(reply)])

    def test_probe_defaults_read_only_and_bench_guard_precedes_setters(self):
        port = FakePort(); link = self.link(port)
        report, identity = link.preflight(expected_frequency=ota.GUARD_HZ, require_lab_lock=True)
        self.assertEqual(identity, port.public)
        self.assertFalse(report["serve"])
        self.assertEqual(port.writes[0], b"\6\x21")
        self.assertTrue(all(frame[0] == 6 for frame in port.writes))
        with self.assertRaises(ota.ProtocolError):
            link.send_radio(b"\xf2\0\6")
        with self.assertRaises(ValueError):
            self.link().preflight(zero_pacing=True)

    def test_production_no_hil_command_and_explicit_pacing_only_after_proof(self):
        port = FakePort(); link = self.link(port)
        link.preflight(expected_frequency=ota.GUARD_HZ, expected_public_key=port.public,
                       serve=True, zero_pacing=True)
        self.assertNotIn(b"\6\x21", port.writes)
        self.assertEqual(port.writes[-3:], [b"\1\0", b"\2\xff", b"\3\1"])
        self.assertTrue(all(frame[0] == 6 for frame in port.writes[:-3]))

    def test_wrong_identity_frequency_protocol_and_missing_guard_never_set_or_send(self):
        for kind in ("identity", "frequency", "protocol", "guard", "version"):
            port = FakePort(); link = self.link(port)
            if kind == "protocol": port.radio = (*port.radio[:6], 2)
            if kind == "guard": port.overrides[0x21] = hw(0xF1, b"\5")
            if kind == "version": port.overrides[0x11] = hw(0x91, b"\1\0")
            with self.subTest(kind=kind), self.assertRaises((ota.ProtocolError, ValueError)):
                link.preflight(expected_frequency=910525000 if kind == "frequency" else ota.GUARD_HZ,
                    expected_public_key=b"x" * 32 if kind == "identity" else None,
                    require_lab_lock=True, serve=True, zero_pacing=True)
            self.assertTrue(all(frame[0] == 6 for frame in port.writes))

    def test_real_txdone_required_and_rx_requests_survive_while_waiting(self):
        port = FakePort(); link = self.link(port)
        link.preflight(expected_frequency=ota.GUARD_HZ, serve=True)
        request = b"\xf2\0\6seed\0\0\xff\xaf"
        port.before_tx_done = ota.kiss_data(1, request)
        link.send_radio(b"\xf2\0\7payload")
        self.assertEqual(link.tx_frames, 1)
        self.assertEqual(link.rf_bytes, 10)
        self.assertEqual(link.poll(), request)
        self.assertGreater(link.tx_done_wait_seconds, 0)
        self.assertGreater(link.estimated_airtime_seconds, 0)
        for bad in (hw(0xF0), hw(0xF8, b"\0"), b""):
            port = FakePort(); link = self.link(port)
            link.preflight(expected_frequency=ota.GUARD_HZ, serve=True)
            port.tx_reply = bad
            with self.assertRaises(ota.ProtocolError):
                link.send_radio(b"\xf2\0\7payload")
            self.assertEqual(sum(frame[0] == 0x10 for frame in port.writes), 1)

    def test_frequency_generation_change_before_rf_fails_closed(self):
        port = FakePort(); link = self.link(port)
        link.preflight(expected_frequency=ota.GUARD_HZ, serve=True)
        port.radio = (910525000, *port.radio[1:])
        with self.assertRaises(ota.ProtocolError):
            link.send_radio(b"\xf2\0\7payload")
        self.assertFalse(any(frame[0] in (0, 0x10) for frame in port.writes))

    def test_saved_radio2_cannot_hide_active_temporary_lease_before_setters(self):
        # Even an expiring lease or the expected frequency is not a stable
        # profile: require the entire temporary tuple to be zero.
        for temporary in (struct.pack("<IIBBBHH", 910525000, 62500, 7, 5, 2, 32, 1),
                          struct.pack("<IIBBBHH", ota.GUARD_HZ, 500000, 5, 5, 2, 512, 0),
                          bytes(13) + b"\1\0"):
            with self.subTest(temporary=temporary.hex()):
                port = FakePort(); link = self.link(port)
                port.overrides[0x1E] = hw(0x9E, temporary)
                with self.assertRaisesRegex(ota.ProtocolError, "temporary radio2"):
                    link.preflight(expected_frequency=ota.GUARD_HZ, serve=True, zero_pacing=True)
                self.assertTrue(all(frame[0] == 6 for frame in port.writes))
                self.assertFalse(link.authorized)
        port = FakePort(); link = self.link(port)
        port.overrides[0x1E] = hw(0xF1, b"\5")
        with self.assertRaises(ota.ProtocolError):
            link.preflight(expected_frequency=ota.GUARD_HZ, serve=True, zero_pacing=True)
        self.assertTrue(all(frame[0] == 6 for frame in port.writes))

    def test_temporary_lease_started_after_preflight_refuses_next_data(self):
        port = FakePort(); link = self.link(port)
        link.preflight(expected_frequency=ota.GUARD_HZ, serve=True)
        port.overrides[0x1E] = hw(0x9E,
            struct.pack("<IIBBBHH", 910525000, 62500, 7, 5, 2, 32, 1))
        with self.assertRaisesRegex(ota.ProtocolError, "temporary radio2"):
            link.send_radio(b"\xf2\0\7payload")
        self.assertFalse(any(frame[0] in (0, 0x10) for frame in port.writes))

    def test_primary_getter_effective_override_change_refuses_data(self):
        port = FakePort(); link = ota.KissLink(port, 0, timeout=.5, clock=Clock())
        link.preflight(expected_frequency=ota.GUARD_HZ, serve=True)
        self.assertNotIn(b"\6\x1e", port.writes)  # no primary-lease KISS API
        port.radio = (910525000, *port.radio[1:])
        with self.assertRaisesRegex(ota.ProtocolError, "Radio changed"):
            link.send_radio(b"\xf2\0\7payload")
        self.assertFalse(any(frame[0] in (0, 0x10) for frame in port.writes))


class PacingTests(unittest.TestCase):
    def test_sparse_distinct_losses_downshift_duplicates_do_not_compound(self):
        pace = ota.AdaptivePace()
        self.assertEqual(pace.quiet(.1), 0)
        pace.observe(0, 1, 0x1FF)
        self.assertEqual(pace.effective, .5)
        self.assertAlmostEqual(pace.quiet(.1), .1)
        for _ in range(20):
            pace.observe(0, 1, 0x1FF)
        self.assertEqual(pace.effective, .5)
        pace.observe(1, 2, 0x1FF)
        self.assertEqual(pace.effective, .25)
        self.assertAlmostEqual(pace.quiet(.1), .3)
        self.assertEqual(pace.report()["duplicate_sparse_requests"], 20)
        floor = ota.AdaptivePace(.5)
        for block in range(5):
            floor.observe(block, 1, 0x1FF)
        self.assertEqual(floor.effective, .5)

    def test_sixteen_forward_clean_blocks_recover_point_one_only(self):
        pace = ota.AdaptivePace()
        pace.observe(0, 1, 0x1FF)
        for _ in range(20):
            pace.observe(0, 0x1FF, 0x1FF)
        self.assertEqual(pace.effective, .5)
        for block in range(1, 16):
            pace.observe(block, 0x1FF, 0x1FF)
        self.assertEqual(pace.effective, .5)
        pace.observe(16, 0x1FF, 0x1FF)
        self.assertEqual(pace.effective, .6)
        for block in range(17, 33):
            pace.observe(block, 0x1FF, 0x1FF)
        self.assertEqual(pace.effective, .7)
        pace.observe(100, 0, 0x1FF)
        self.assertEqual(pace.effective, .7)

    def test_source_validated_retry_masks_drive_geometry_specific_pace(self):
        source = ota.MotaSource(container(bytes(range(256)) * 24), b"seed", compressor=lambda data: data)
        source.replies(request(source, (0, 0xA1FF)))  # complete2KiBwide mask
        self.assertEqual(source.pace.effective, 1)
        source.replies(request(source, (0, 0xA001)))
        self.assertEqual(source.pace.effective, .5)
        source.replies(request(source, (0, 0xA001)))
        self.assertEqual(source.pace.effective, .5)
        source.replies(request(source, (1, 0xA001)))
        self.assertEqual(source.pace.effective, .25)
        before = source.pace.report()
        source.replies(request(source, (0, 0xA001), (500, 0xA001)))
        self.assertEqual(source.pace.report(), before)

    def test_wide_proof_immediate_legacy_proof_gap_and_subone_quiet_only(self):
        source = ota.MotaSource(container(bytes(range(256)) * 8), b"seed", compressor=lambda data: data)
        class Link:
            radio = (ota.GUARD_HZ, 500000, 5, 5, 2, 512, 0)
            last_airtime_seconds = .05
            def __init__(self): self.sent = []
            def send_radio(self, frame): self.sent.append(frame)
        for wide in (True, False):
            link, sleeps = Link(), []
            batch = source.replies(request(source, (0, 0xAFFF), wide=wide))
            ota.send_batch(link, source, batch, sleep=sleeps.append, clock=lambda: 0)
            self.assertEqual(link.sent, batch)
            self.assertEqual(sleeps, [] if wide else [.1])
        source.pace.observe(0, 1, 0x1FF)
        link, sleeps = Link(), []
        batch = source.replies(request(source, (0, 0xA001)))
        ota.send_batch(link, source, batch, sleep=sleeps.append, clock=lambda: 0)
        self.assertEqual(sleeps, [.05, .05])  # oneDATA and onePROOF; no wide gap
        stopped = Link()
        ota.send_batch(stopped, source, batch, sleep=sleeps.append, clock=lambda: 10, deadline=10)
        self.assertEqual(stopped.sent, [])


if __name__ == "__main__":
    unittest.main()
