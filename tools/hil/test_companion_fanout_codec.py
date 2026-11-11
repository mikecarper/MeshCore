"""Offline wire/manifest tests. Fixture bytes are not playable media claims."""

import copy
import hashlib
import struct
import unittest

from companion_fanout_codec import (
    assemble, channel_data_command, encode_chunk, encode_fetch, group_text_command,
    media_bundle, parse_channel_frame, parse_chunk, parse_fetch,
    parse_group_text_response,
)


class CompanionFanoutCodecTests(unittest.TestCase):
    sid = 0x12345678
    key6 = bytes.fromhex("102030405060")
    # Opaque transport vectors, deliberately not advertised as Codec2 or AV1 fixtures.
    opaque_voice = bytes(range(156)) * 2 + bytes(range(18))
    opaque_image = bytes(range(256)) + bytes(range(101))

    def test_announcements_match_independent_base36_vectors(self):
        voice = media_bundle("voice", self.opaque_voice, self.sid, duration=37)
        self.assertEqual(voice["envelope"], "VE3:51u7i0:1:3:11")
        self.assertEqual(voice["count"], 3)
        self.assertEqual(voice["content_bytes"], 330)
        self.assertEqual(voice["sha256"], hashlib.sha256(self.opaque_voice).hexdigest())
        self.assertEqual(voice["chunks"][0], b"\x56\x12\x34\x56\x78\0" + bytes(range(156)))
        image = media_bundle("image", self.opaque_image, "12345678", aspect=178)
        self.assertEqual(image["envelope"], "VI1:51u7i0:14:3:4y IMAGE")
        self.assertEqual(image["mode"], 40)
        self.assertEqual(assemble(image, reversed(image["chunks"])), self.opaque_image)
        self.assertEqual(media_bundle("voice", b"\0" * 6, bytes.fromhex("ffffffff"),
                                      duration=600)["envelope"], "VE3:1z141z3:1:1:go")

    def test_voice_modes_never_split_complete_frames(self):
        for mode, size in {0: 4, 1: 6, 2: 6, 3: 7, 4: 7, 5: 8, 6: 8}.items():
            bundle = media_bundle("voice", b"\xa5" * (size * 100), 0, mode=mode)
            for chunk in bundle["chunks"]:
                self.assertEqual(len(parse_chunk(chunk)["data"]) % size, 0)
                self.assertLessEqual(len(parse_chunk(chunk)["data"]), 156)
            self.assertEqual(assemble(bundle, bundle["chunks"]), b"\xa5" * (size * 100))
            with self.assertRaises(ValueError):
                media_bundle("voice", b"\0" * (size + 1), 0, mode=mode)

    def test_maximum_counts_and_invalid_manifest_inputs(self):
        self.assertEqual(media_bundle("image", bytes(156 * 255), 0)["count"], 255)
        invalid = [
            (("voice", b"", 0), {}), (("image", bytes(156 * 255 + 1), 0), {}),
            (("video", b"x", 0), {}), (("voice", bytes(6), True), {}),
            (("voice", bytes(6), -1), {}), (("voice", bytes(6), 1 << 32), {}),
            (("voice", bytes(6), "1"), {}), (("voice", bytes(6), b"123"), {}),
            (("voice", bytes(6), 0), {"mode": 32}),
            (("voice", bytes(6), 0), {"duration": 601}),
            (("image", b"x", 0), {"aspect": 601}),
        ]
        for args, kwargs in invalid:
            with self.subTest(args=args[:1], kwargs=kwargs), self.assertRaises(ValueError):
                media_bundle(*args, **kwargs)

    def test_standard_and_salted_chunk_vectors_and_bounds(self):
        packet = encode_chunk(self.sid, 254, b"\x01\x02", salt=0xc3)
        self.assertEqual(packet, bytes.fromhex("5712345678fe0102c3"))
        parsed = parse_chunk(packet)
        self.assertEqual((parsed["sid"], parsed["index"], parsed["data"], parsed["salt"]),
                         (self.sid, 254, b"\x01\x02", 0xc3))
        for packet in (b"", b"\x56", bytes.fromhex("561234567800"),
                       bytes.fromhex("571234567800a5"), bytes.fromhex("5612345678ff01"),
                       b"\x56" + bytes(5) + bytes(157), b"\x58" + bytes(7), "not bytes"):
            self.assertIsNone(parse_chunk(packet))
        for data, index, salt in ((b"", 0, None), (bytes(157), 0, None),
                                  (b"x", 255, None), (b"x", 0, 256)):
            with self.assertRaises(ValueError):
                encode_chunk(self.sid, index, data, salt=salt)

    def test_fetch_exact_vectors_and_missing_set_semantics(self):
        all_packets = encode_fetch(self.sid, self.key6)
        self.assertEqual(all_packets, bytes.fromhex("72123456780010203040506000"))
        self.assertIsNone(parse_fetch(all_packets)["missing"])
        missing = encode_fetch("12345678", self.key6, [254, 2, 0, 2])
        self.assertEqual(missing, bytes.fromhex("721234567801102030405060030002fe"))
        parsed = parse_fetch(missing)
        self.assertEqual(parsed["missing"], [0, 2, 254])
        self.assertEqual((parsed["sid"], parsed["key6"]), (self.sid, self.key6))
        self.assertEqual(parse_fetch(encode_fetch(self.sid, self.key6, []))["missing"], [])
        # Repeating an index salts a fetch without changing its requested set.
        salted = missing[:5] + b"\3" + missing[6:12] + b"\4" + missing[13:] + b"\2"
        self.assertEqual(parse_fetch(salted)["missing"], [0, 2, 254])
        self.assertTrue(parse_fetch(salted)["salt_ok"])

    def test_fetch_paths_and_strict_malformed_rejection(self):
        base = encode_fetch(self.sid, self.key6)
        routed = base[:5] + b"\4" + base[6:] + b"\x42\xaa\xbb\xcc\xdd"
        parsed = parse_fetch(routed)
        self.assertEqual((parsed["path_width"], parsed["path_len"], parsed["path"]),
                         (2, 0x42, bytes.fromhex("aabbccdd")))
        malformed = [base[:-1], base + b"x", b"x" + base[1:],
                     base[:5] + b"\x08" + base[6:],
                     base[:-1] + b"\1\0",  # Count is nonzero without missing-only.
                     base[:5] + b"\1" + base[6:12] + b"\1",  # Truncated list.
                     base[:5] + b"\1" + base[6:12] + b"\1\xff",  # Index 255.
                     routed[:-1], routed + b"x",
                     base[:5] + b"\4" + base[6:],
                     base[:5] + b"\4" + base[6:] + b"\xc0"]
        for packet in malformed:
            self.assertIsNone(parse_fetch(packet), packet.hex())
        for key, missing in ((b"short", None), (self.key6, [255]),
                             (self.key6, [-1]), (self.key6, range(141)), (self.key6, 7)):
            with self.assertRaises(ValueError):
                encode_fetch(self.sid, key, missing)

    def test_reassembly_accepts_reordering_salt_and_requires_every_exact_byte(self):
        bundle = media_bundle("voice", self.opaque_voice, self.sid)
        packets = list(reversed(bundle["chunks"]))
        packets[0] = b"\x57" + packets[0][1:] + b"\x88"
        self.assertEqual(assemble(bundle, packets), self.opaque_voice)
        first = bundle["chunks"][0]
        malformed_sets = [bundle["chunks"][:-1], bundle["chunks"] + [first],
                          [first, b"\x57" + first[1:] + b"\0"] + bundle["chunks"][1:],
                          [b"\x56\x87\x65\x43\x21" + first[5:]] + bundle["chunks"][1:],
                          [first[:5] + b"\3" + first[6:]] + bundle["chunks"][1:],
                          [first[:-1]] + bundle["chunks"][1:],
                          [first[:-1] + bytes((first[-1] ^ 1,))] + bundle["chunks"][1:]]
        for chunks in malformed_sets:
            with self.assertRaises(ValueError):
                assemble(bundle, chunks)
        for field, replacement in (("count", 2), ("sha256", "0" * 64),
                                   ("content_bytes", 1), ("envelope", "VE3:0:1:3:1")):
            broken = copy.deepcopy(bundle)
            broken[field] = replacement
            with self.assertRaises(ValueError):
                assemble(broken, bundle["chunks"])
        broken = copy.deepcopy(bundle)
        broken["chunks"] = list(reversed(broken["chunks"]))
        with self.assertRaises(ValueError):
            assemble(broken, bundle["chunks"])

    def test_channel_command_and_reply_exact_offsets_profile_and_limits(self):
        payload = encode_chunk(self.sid, 0, bytes(range(156)))
        command = channel_data_command(7, payload)
        self.assertEqual(command[:5], b"\x3e\7\xff\x43\x56")
        self.assertEqual(len(command), 167)
        self.assertEqual(command[5:], payload)
        for tag, expected_profile in ((0xa0, 0), (0xa1, 1), (0, None)):
            frame = bytes((27, 0xf2, tag, 0, 7, 0xff, 0x43, 0x56, len(payload))) + payload
            parsed = parse_channel_frame(frame)
            self.assertEqual(parsed["data"], payload)
            self.assertEqual((parsed["channel"], parsed["data_type"], parsed["profileTag"],
                              parsed["radio_profile"], parsed["snr_db"], parsed["path_len"]),
                             (7, 0x5643, tag, expected_profile, -3.5, 255))
            self.assertIsNone(parse_channel_frame(frame[:-1]))
            self.assertIsNone(parse_channel_frame(frame + b"x"))
        for slot, data, datatype in ((256, b"x", 1), (0, bytes(164), 1),
                                      (0, b"", 1), (0, b"x", 0x10000)):
            with self.assertRaises(ValueError):
                channel_data_command(slot, data, datatype)
        self.assertIsNone(parse_channel_frame(b"\x1b"))

    def test_group_text_command_and_v3_reply_round_trip(self):
        announcement = media_bundle("image", self.opaque_image, self.sid)["envelope"]
        command = group_text_command(3, announcement, 0x12345678)
        self.assertEqual(command, b"\3\0\3\x78\x56\x34\x12" + announcement.encode())
        received_text = "Sender: " + announcement
        frame = b"\x11\x14\xa1\0\3\xff\0" + struct.pack("<I", 0x12345678) + received_text.encode()
        parsed = parse_group_text_response(frame)
        self.assertEqual((parsed["text"], parsed["channel"], parsed["timestamp"],
                          parsed["radio_profile"], parsed["snr_db"]),
                         (received_text, 3, 0x12345678, 1, 5.0))
        self.assertEqual(group_text_command(0, "\u00e9", 0)[7:], b"\xc3\xa9")
        for frame in (b"\x11", frame[:10], frame[:6] + b"\1" + frame[7:],
                       frame[:11] + b"\xff", frame + b"\0"):
            self.assertIsNone(parse_group_text_response(frame))
        for text in ("", "has\0nul", "a" * 166, "\u00e9" * 83):
            with self.assertRaises(ValueError):
                group_text_command(0, text, 0)


if __name__ == "__main__":
    unittest.main()
