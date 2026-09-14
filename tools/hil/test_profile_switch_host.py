"""Regression tests for the serial HIL result reader."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from profile_switch import read_response, exchange_ready, configure_session, exchange


class Port:
    port = "test"

    def __init__(self, parts):
        self.parts = iter(parts)
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def readline(self):
        return next(self.parts)


class ResultReaderTests(unittest.TestCase):
    def test_timing_runs_get_correlated_cached_replies(self):
        port = Port([])
        with patch("profile_switch._run_sequences", iter([99])), patch(
                "profile_switch.read_response", side_effect=[TimeoutError("late"), {"result": 99}]):
            result = exchange(port, "run 1 7 16 1 768 8", "result", 5)
        self.assertEqual(port.writes, [b"run 1 7 16 1 768 8 99\n", b"result result 99\n"])
        self.assertTrue(result["transport_recovered"])

    def test_native_usb_and_bridge_have_distinct_control_lines(self):
        for vid, expected in ((0x303A, True), (0x1A86, False), (None, False)):
            port = Port([])
            with patch("profile_switch.list_ports.comports", return_value=[
                    SimpleNamespace(device="TEST", vid=vid),
                    SimpleNamespace(device="different", vid=0x303A)]):
                configure_session(port)
            self.assertEqual(port.dtr, expected)
            self.assertFalse(port.rts)

    def test_timeout_replays_cached_result_without_repeating_tx(self):
        port = Port([])
        with patch("profile_switch.read_response", side_effect=[
                TimeoutError("late"), {"sent": 12, "rc": 0}]) as reader:
            result = exchange(port, "tx 7 0 12 16 32 -9 8 1", "sent", 6, 12, cached=True)
        self.assertTrue(result["transport_recovered"])
        self.assertEqual(port.writes, [b"tx 7 0 12 16 32 -9 8 1\n", b"result sent 12\n"])
        self.assertEqual(reader.call_args_list[-1].args, (port, "sent", 3, 12))
        with patch("profile_switch.read_response", side_effect=[
                TimeoutError("late"), RuntimeError("result unavailable")]):
            with self.assertRaisesRegex(RuntimeError, "result unavailable"):
                exchange(port, "tx", "sent", 6, 13, cached=True)

    def test_partial_reads_and_idle_timeout_do_not_split_json(self):
        port = Port([b"startup\n", b'{"result":true,"directions":', b"", b"[]", b"}\n"])
        self.assertEqual(read_response(port, "result", 1), {"result": True, "directions": []})

    def test_ignores_other_complete_messages_but_never_hides_errors(self):
        port = Port([b'{"ready":true}\n', b'{"received":7}\n'])
        self.assertEqual(read_response(port, "received", 1), {"received": 7})
        port = Port([b'{"received":7}\n', b'{"received":8}\n'])
        self.assertEqual(read_response(port, "received", 1, expected=8),
                         {"received": 8, "stale_replies": [{"received": 7}]})
        with self.assertRaisesRegex(RuntimeError, "bad hop"):
            read_response(Port([b'{"error":"bad hop"}\n']), "result", 1)

    def test_only_initial_guard_rejections_are_retried_and_counted(self):
        with patch("profile_switch.exchange", side_effect=[RuntimeError("primary rejected"), {"result": True}]), patch("profile_switch.time.sleep") as sleep:
            self.assertEqual(exchange_ready(None, "run", "result", 1)["setup_deferrals"], 1)
            sleep.assert_called_once_with(0.1)
        with patch("profile_switch.exchange", side_effect=RuntimeError("receiver hop failed")), patch("profile_switch.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "receiver hop failed"):
                exchange_ready(None, "listen", "listening", 1)
            sleep.assert_not_called()
        with patch("profile_switch.exchange", side_effect=RuntimeError("primary rejected")) as command, patch("profile_switch.time.sleep"):
            with self.assertRaises(RuntimeError):
                exchange_ready(None, "run", "result", 1)
            self.assertEqual(command.call_count, 31)


if __name__ == "__main__":
    unittest.main()
