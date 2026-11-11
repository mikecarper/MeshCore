"""Exercise crossed startup replies through the actual bounded HIL request."""

from collections import deque
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import companion_serial as HIL


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class Port:
    def __init__(self):
        self.writes = []
        self.flushes = 0

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        self.flushes += 1


class Reader:
    def __init__(self, frames, clock):
        self.frames = deque(frames)
        self.clock = clock
        self.timeouts = []

    def read_frame(self, port, timeout):
        self.timeouts.append(timeout)
        self.clock.now += min(0.25, timeout)
        if len(self.timeouts) > 20:
            raise AssertionError("request did not retain a bounded deadline")
        if not self.frames:
            raise HIL.FrameTimeout()
        frame = self.frames.popleft()
        if isinstance(frame, Exception):
            raise frame
        return frame


class CompanionStartupReplyTests(unittest.TestCase):
    def request(self, payload, expected, frames, timeout=1):
        clock = Clock()
        node = HIL.Companion.__new__(HIL.Companion)
        node.port = Port()
        node.reader = Reader(frames, clock)
        self.node, self.clock = node, clock
        with mock.patch.object(HIL.time, "monotonic", clock.monotonic):
            return node.request(payload, expected, timeout=timeout)

    def assert_one_write(self, payload):
        self.assertEqual(self.node.port.writes, [HIL.encode_host_frame(payload)])
        self.assertEqual(self.node.port.flushes, 1)

    def test_app_start_ignores_duplicate_device_info_before_self_info(self):
        payload = b"\x01app"
        actual = b"\x05self info"
        result = self.request(payload, (5,), [b"\x80push", b"\x0dold info", b"\x0dduplicate", actual])
        self.assertEqual(result, actual)
        self.assert_one_write(payload)

    def test_device_query_ignores_stale_self_info_before_device_info(self):
        payload = bytes((22, 14))
        actual = b"\x0ddevice info"
        result = self.request(payload, (13,), [b"\x05old self info", actual])
        self.assertEqual(result, actual)
        self.assert_one_write(payload)

    def test_crossed_replies_do_not_extend_deadline_or_resend(self):
        for payload, expected, stale in ((b"\x01app", (5,), 13), (bytes((22, 14)), (13,), 5)):
            with self.subTest(command=payload[0]):
                target = bytes((expected[0],))
                with self.assertRaisesRegex(HIL.Failure, "response timeout for command " + str(payload[0])):
                    self.request(payload, expected, [bytes((stale,))] * 4 + [target])
                self.assertEqual(self.clock.now, 101.0)
                self.assertEqual(len(self.node.reader.timeouts), 4)
                self.assertEqual(list(self.node.reader.frames), [target])
                self.assert_one_write(payload)

    def test_timeouts_and_crossed_reply_keep_waiting_for_actual_reply(self):
        payload = b"\x01app"
        actual = b"\x05self info"
        self.assertEqual(self.request(payload, (5,), [HIL.FrameTimeout(), b"\x0dold info", actual]), actual)
        self.assert_one_write(payload)

    def test_other_commands_and_unexpected_response_types_still_fail(self):
        cases = ((b"\x42get tx", (29,), 13), (b"\x42get tx", (29,), 5),
                 (b"\x01app", (5,), 0), (b"\x01app", (5,), 12),
                 (bytes((22, 14)), (13,), 29),
                 (b"\x01app", (13,), 5), (bytes((22, 14)), (5,), 13))
        for payload, expected, received in cases:
            with self.subTest(command=payload[0], expected=expected, received=received):
                with self.assertRaisesRegex(HIL.Failure, "unexpected Companion response " + str(received)):
                    self.request(payload, expected, [bytes((received,)), bytes((expected[0],))])
                self.assertEqual(len(self.node.reader.frames), 1)
                self.assert_one_write(payload)

    def test_explicitly_expected_alternate_startup_type_is_returned(self):
        for payload, received in ((b"\x01app", 13), (bytes((22, 14)), 5)):
            with self.subTest(command=payload[0]):
                frame = bytes((received,))
                self.assertEqual(self.request(payload, (5, 13), [frame]), frame)
                self.assert_one_write(payload)

    def test_device_errors_remain_fatal_after_stale_startup_reply(self):
        for payload, expected, stale in ((b"\x01app", (5,), 13), (bytes((22, 14)), (13,), 5)):
            with self.subTest(command=payload[0]):
                with self.assertRaisesRegex(HIL.Failure, "Companion command rejected, error 0d"):
                    self.request(payload, expected, [bytes((stale,)), b"\x01\x0d", bytes((expected[0],))])
                self.assertEqual(len(self.node.reader.frames), 1)
                self.assert_one_write(payload)


if __name__ == "__main__":
    unittest.main()
