"""Safety and result checks for the RX-only modulation-cache A/B collector."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import profile_switch_same_modulation as collector


class SameModulationTests(unittest.TestCase):
    def run_collector(self, directory, gain=0x94, skipped=99, failures=0):
        output = Path(directory) / "result.json"
        port = MagicMock()
        commands = []
        enabled = False

        def exchange(_port, command, *_args, **_kwargs):
            nonlocal enabled
            commands.append(command)
            if command == "info":
                return dict(modulation_cache_ab=1, rx_gain_reg=gain)
            if command.startswith("scanmodcache "):
                enabled = command.endswith("1")
                return dict(modulation_cache=enabled)
            if command.startswith("scanstatus "):
                return dict(failures=failures, rx_mode_errors=0, cache_errors=0,
                            rx_gain_reg=gain, rf_commands=100,
                            modulation_commands=100-skipped if enabled else 100,
                            switch=dict(n=100, mean_us=400 if enabled else 500),
                            with_modulation=dict(n=100-skipped if enabled else 100),
                            without_modulation=dict(n=skipped if enabled else 0))
            return {}

        argv = ["collector", "--port", "COM31", "--output", str(output),
                "--expect-rx-gain", "normal"]
        with patch("sys.argv", argv), patch.object(collector, "open_port", return_value=port), \
                patch.object(collector, "exchange", side_effect=exchange), \
                patch.object(collector.time, "sleep"), patch("builtins.print"):
            error = None
            try:
                collector.main()
            except RuntimeError as caught:
                error = str(caught)
        return json.loads(output.read_text()), commands, port, error

    def test_balanced_order_without_any_transmission_and_reboots_between_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            result, commands, port, error = self.run_collector(directory)
        self.assertIsNone(error)
        self.assertTrue(result["complete"])
        self.assertEqual([t["enabled"] for t in result["trials"]], [False, True, True, False])
        self.assertEqual([c for c in commands if c.startswith("scanmodcache")],
                         ["scanmodcache 0", "scanmodcache 1", "scanmodcache 1", "scanmodcache 0"])
        self.assertTrue(all(c.split()[0] in
                            ("info", "scanmodcache", "scanstart", "scanstatus", "scanstop")
                            for c in commands))
        self.assertEqual(port.write.call_args_list, [unittest.mock.call(b"reboot\n")] * 4)
        self.assertEqual(port.close.call_count, 4)

    def test_wrong_gain_aborts_before_scan_and_closes_port(self):
        with tempfile.TemporaryDirectory() as directory:
            result, commands, port, error = self.run_collector(directory, gain=0x96)
        self.assertIn("gain", error)
        self.assertFalse(result["complete"])
        self.assertEqual(result["trials"], [])
        self.assertFalse(any(c.startswith("scanstart") for c in commands))
        port.close.assert_called_once()

    def test_missing_optimization_is_retained_as_a_failed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            result, commands, port, error = self.run_collector(directory, skipped=0)
        self.assertIn("No modulation writes were skipped", error)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["trials"]), 2)
        self.assertEqual(port.close.call_count, 2)

    def test_hardware_error_stops_after_first_window(self):
        with tempfile.TemporaryDirectory() as directory:
            result, commands, port, error = self.run_collector(directory, failures=1)
        self.assertIn("hardware/configuration", error)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["trials"]), 1)
        port.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
