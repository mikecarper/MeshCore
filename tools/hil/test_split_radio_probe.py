import struct
import unittest

from split_radio_probe import (companion_send_command, counter_delta, inspect_push,
                               kiss_frame, make_probe, packet_stats, parse_probe,
                               profile_counters)


class SplitRadioProbeTests(unittest.TestCase):
    nonce = bytes.fromhex("1020304050607080")
    center = bytes(range(32))

    def test_original_and_center_path_remain_separate_from_duplicate_hash(self):
        for source in ("L", "R"):
            original = make_probe(self.nonce, source, 123)
            first = parse_probe(original, expected_run=self.nonce)
            self.assertTrue(first.original)
            self.assertFalse(first.forwarded_by(self.center))
            for width in (1, 2, 3):
                forwarded = original[:1] + bytes((((width-1)<<6)|1,)) + \
                    self.center[:width] + original[2:]
                final = parse_probe(forwarded, expected_run=self.nonce)
                self.assertTrue(final.forwarded_by(self.center))
                self.assertEqual(first.mesh_hash, final.mesh_hash)
                self.assertEqual(first.key, final.key)
                self.assertNotEqual(first.record()["wire_hex"], final.record()["wire_hex"])
            wrong = original[:1]+b"\x01\xff"+original[2:]
            self.assertFalse(parse_probe(wrong).forwarded_by(self.center))
        self.assertNotEqual(parse_probe(make_probe(self.nonce, "L", 123)).key,
                            parse_probe(make_probe(self.nonce, "R", 123)).key)

    def test_pushes_do_not_invent_missing_radio_profile_or_path(self):
        wire = make_probe(self.nonce, "L", 3)
        full = inspect_push(b"\x88\x18\x9c"+wire, expected_run=self.nonce,
                            configured_profile=1, frequency_mhz=906.15, observer="R2")
        self.assertEqual((full["snr_db"], full["rssi_dbm"]), (6.0, -100))
        self.assertIsNone(full["radio_profile"])
        self.assertEqual(full["configured_profile"], 1)
        self.assertEqual(full["path_hex"], "")
        for tag, profile in ((0xA0, 0), (0xA1, 1), (0xFF, None)):
            payload = inspect_push(bytes((0x84, 0, 0x9c, tag))+wire[2:])
            self.assertEqual(payload["radio_profile"], profile)
            self.assertIsNone(payload["wire_hex"])
            self.assertIsNone(payload["path_hex"])

    def test_controls_counters_wire_limits_and_rejections(self):
        wire = make_probe(self.nonce, "R", 7)
        self.assertEqual(companion_send_command(wire), b"\x41\0"+wire)
        direct = parse_probe(make_probe(self.nonce, "R", 7, direct=True))
        self.assertEqual(direct.route, 2)
        self.assertFalse(direct.original)
        transported = b"\x3c\x01\x02\x03\x04"+wire[1:]
        self.assertEqual(parse_probe(transported).key, parse_probe(wire).key)
        self.assertEqual(packet_stats(bytes((24, 2))+struct.pack("<7I", *range(7)))["recv_errors"], 6)
        self.assertEqual(profile_counters("> rx; RX=0,12 TX=1,0 switches=5"),
                         {"rx": [0, 12], "tx": [1, 0]})
        self.assertEqual(counter_delta({"tx": 0xffffffff}, {"tx": 0}), {"tx": 1})
        self.assertEqual(kiss_frame(0x10, b"\xc0\xdb"), b"\xc0\x10\xdb\xdc\xdb\xdd\xc0")
        for malformed in (b"", wire[:1], b"\xfd"+wire[1:], wire[:1]+b"\xc1"+wire[2:],
                          wire[:1]+b"\x7f"+wire[2:], wire[:-39]):
            self.assertIsNone(parse_probe(malformed))
        self.assertIsNone(parse_probe(wire, expected_run=bytes(8)))
        self.assertIsNone(parse_probe(make_probe(self.nonce, "L", 1, payload_size=184)+b"x"))
        with self.assertRaises(ValueError):
            profile_counters("RX=1,2 TX=1,2 RX=1,2 TX=1,2")
        with self.assertRaises(ValueError):
            packet_stats(b"\x18\2")
        with self.assertRaises(ValueError):
            make_probe(self.nonce, "X", 0)


if __name__ == "__main__":
    unittest.main()
