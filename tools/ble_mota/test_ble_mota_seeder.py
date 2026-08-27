#!/usr/bin/env python3

import unittest

import ble_mota_seeder as seeder


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
