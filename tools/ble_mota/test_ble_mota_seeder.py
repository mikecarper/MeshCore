#!/usr/bin/env python3

import hashlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import ble_mota_seeder as seeder


def request_frame(op: int, args: bytes) -> bytes:
    return b"MS" + bytes((op,)) + args + bytes((seeder.xor_bytes(args, op),))


def response_payload(response: bytes, op: int) -> tuple[int, bytes]:
    assert response[:2] == b"ms"
    assert response[2] == op
    assert response[-1] == seeder.xor_bytes(response[:-1])
    return response[3], response[4:-1]


class TransportDeflateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.mota"
        # The repeated 512-byte prefix is 1536 bytes behind its second copy. A 2 KiB
        # application window can encode that match; the legacy 1 KiB window cannot.
        noise = b"".join(
            hashlib.sha256(index.to_bytes(4, "little")).digest()
            for index in range(48)
        )
        self.raw = noise[:512] + noise[512:1536] + noise[:512]
        self.assertEqual(len(self.raw), seeder.MOTA_DEFLATE_BLOCK_MAX)
        payload_offset = 256
        self.path.write_bytes(bytes(payload_offset) + self.raw)
        descriptor = bytearray(seeder.MOTA_DESC_WIRE)
        struct.pack_into("<I", descriptor, 22, 1)  # block_count
        struct.pack_into("<I", descriptor, 26, payload_offset)
        struct.pack_into("<I", descriptor, 30, len(self.raw))
        descriptor[34] = 11
        mota = seeder.MotaFile(self.path, self.path.stat().st_size, bytes(descriptor))
        self.catalog = seeder.Catalog([mota], False)

    def tearDown(self):
        seeder.MotaFile.deflated_block.cache_clear()
        self.directory.cleanup()

    def exchange(self, block: int, offset: int, length: int) -> tuple[int, bytes]:
        args = struct.pack("<BHHH", 0, block, offset, length)
        response = self.catalog.handle_request(
            request_frame(seeder.OP_DEFLATE_BLOCK, args)
        )
        self.assertIsNotNone(response)
        return response_payload(response, seeder.OP_DEFLATE_BLOCK)

    def test_serves_2k_raw_deflate_with_long_distance_match(self):
        status, payload = self.exchange(0, 0, 0)
        self.assertEqual(status, seeder.STATUS_OK)
        total = struct.unpack("<H", payload)[0]
        self.assertLess(total, len(self.raw))

        encoded = bytearray()
        for offset in range(0, total, seeder.MOTA_DEFLATE_CHUNK_MAX):
            length = min(seeder.MOTA_DEFLATE_CHUNK_MAX, total - offset)
            status, payload = self.exchange(0, offset, length)
            self.assertEqual(status, seeder.STATUS_OK)
            self.assertEqual(struct.unpack_from("<H", payload)[0], total)
            encoded.extend(payload[2:])
        self.assertEqual((encoded[0] >> 1) & 0x03, 2)  # dynamic Huffman (BTYPE=2)
        self.assertEqual(zlib.decompress(bytes(encoded), wbits=-11), self.raw)

        compressor_1k = zlib.compressobj(
            level=9, method=zlib.DEFLATED, wbits=-10
        )
        encoded_1k = compressor_1k.compress(self.raw) + compressor_1k.flush()
        self.assertLess(len(encoded) + 400, len(encoded_1k))

    def test_rejects_bad_block_range_and_oversized_chunk(self):
        self.assertEqual(self.exchange(1, 0, 0)[0], seeder.STATUS_ERR)
        self.assertEqual(
            self.exchange(0, 0, seeder.MOTA_DEFLATE_CHUNK_MAX + 1)[0],
            seeder.STATUS_ERR,
        )


class SourceStatusTests(unittest.TestCase):
    def test_parses_current_status_with_packet_count(self):
        response = bytes.fromhex("0001030200020078563412")
        self.assertEqual(
            seeder.parse_source_status(response, seeder.MOTA_ACTION_START),
            (3, 2, 2, 0x12345678),
        )

    def test_accepts_legacy_status_without_packet_count(self):
        response = bytes.fromhex("00010302000200")
        self.assertEqual(
            seeder.parse_source_status(response, seeder.MOTA_ACTION_START),
            (3, 2, 2, None),
        )

    def test_rejects_wrong_action_and_malformed_lengths(self):
        current = bytes.fromhex("0001030200020078563412")
        with self.assertRaisesRegex(RuntimeError, "malformed source status"):
            seeder.parse_source_status(current, seeder.MOTA_ACTION_STOP)
        with self.assertRaisesRegex(RuntimeError, "malformed source status"):
            seeder.parse_source_status(current[:8], seeder.MOTA_ACTION_START)

    def test_reports_firmware_error(self):
        with self.assertRaisesRegex(RuntimeError, "error 4"):
            seeder.parse_source_status(
                bytes((seeder.RESP_ERR, 4)), seeder.MOTA_ACTION_START
            )


class SourceActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_and_parses_current_status(self):
        response = bytes.fromhex("0001030200020078563412")
        session = object.__new__(seeder.BleSession)

        async def companion_command(frame, expected_length, timeout):
            self.assertEqual(
                frame,
                bytes((seeder.CMD_BLE_MOTA_SOURCE, seeder.MOTA_ACTION_START)),
            )
            self.assertEqual(timeout, 30.0)
            self.assertIsNone(expected_length(bytes((seeder.RESP_OK,))))
            self.assertEqual(expected_length(response), 11)
            return response

        session.companion_command = companion_command
        self.assertEqual(
            await session.source_action(seeder.MOTA_ACTION_START),
            (3, 2, 2, 0x12345678),
        )

    async def test_accepts_legacy_seven_byte_status(self):
        response = bytes.fromhex("00000300000000")
        session = object.__new__(seeder.BleSession)

        async def companion_command(_frame, expected_length, timeout):
            self.assertEqual(timeout, 30.0)
            self.assertEqual(expected_length(response), 7)
            return response

        session.companion_command = companion_command
        self.assertEqual(
            await session.source_action(seeder.MOTA_ACTION_STATUS),
            (3, 0, 0, None),
        )


if __name__ == "__main__":
    unittest.main()
