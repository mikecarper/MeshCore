"""Host harness guards; these do not claim hardware or battery qualification."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


PATH = Path(__file__).resolve().parents[1] / "tools/hil/emergency_power_bench.py"
SPEC = importlib.util.spec_from_file_location("emergency_power_bench", PATH)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def reply(command, value):
    return command + "\n  -> > " + value + "\n"


class EmergencyBench(unittest.TestCase):
    def test_status_requires_every_field_and_seconds(self):
        text = "mode=manual active=on battery=5% clock=rtc window=sleep radio=sleep wake=2699s hold=0s"
        state = bench.status(text)
        self.assertEqual(state["wake"], 2699)
        self.assertEqual(state["hold"], 0)
        self.assertEqual(state["mode"], "manual")
        for bad in (text.replace("2699s", "2699m"), text.replace(" radio=sleep", ""),
                    text.replace("mode=manual", "mode=unknown")):
            with self.assertRaises(RuntimeError):
                bench.status(bad)

    def test_raw_advert_uses_actual_route_path_identity_and_timestamp(self):
        identity = bytes(range(32))
        epoch = 1793000000
        payload = identity + epoch.to_bytes(4, "little") + bytes(64) + b"node"
        for route in range(4):
            for width in (1, 2, 3):
                raw = bytes((16 | route,)) + (bytes(4) if route in (0, 3) else b"")
                raw += bytes((((width - 1) << 6) | 2,)) + b"x" * (width * 2) + payload
                self.assertEqual(bench.advert_timestamp("2026 RAW: " + raw.hex(), identity), epoch)
                self.assertEqual(bench.advert_timestamp("R" + raw.hex(), identity), epoch)
                self.assertIsNone(bench.advert_timestamp("TX RAW? " + raw.hex(), identity))
                self.assertIsNone(bench.advert_timestamp("R" + raw.hex(), bytes(32)))
                self.assertIsNone(bench.advert_timestamp("R" + raw[:10].hex(), identity))
        for header, path in ((16, 192), (16, 127), (20, 0), (80, 0)):
            self.assertIsNone(bench.advert_timestamp("R" + (bytes((header, path)) + payload).hex(), identity))

    def test_reply_and_identity_require_complete_exact_key(self):
        self.assertEqual(bench.public_key(reply("get public.key", "12" * 32)), b"\x12" * 32)
        for value in ("12" * 31, "12" * 33, "x" * 64, "pending"):
            with self.assertRaises(RuntimeError):
                bench.public_key(reply("get public.key", value))
        with self.assertRaises(RuntimeError):
            bench.reply_value("get radio\n> partial")

    def test_rxps_restore_preserves_level_or_manual_timings(self):
        self.assertEqual(bench.rxps_restore("off,level=8,preamble=16,rx=39657,sleep=15975"), "set radio.rxps off")
        self.assertEqual(bench.rxps_restore("on,level=8,preamble=16,rx=39657,sleep=15975"), "set radio.rxps level 8 preamble 16")
        self.assertEqual(bench.rxps_restore("on,level=0,preamble=0,rx=65625,sleep=60000"), "set radio.rxps 65625 60000")
        for bad in ("unknown setting", "on", "on,level=8,preamble=16,rx=1"):
            with self.assertRaises(RuntimeError):
                bench.rxps_restore(bad)

    def test_clock_uses_real_minute_precision_cli(self):
        epoch = bench.clock_epoch(reply("clock", "12:34 - 28/10/2026 UTC"))
        self.assertEqual(epoch % 3600, 34 * 60)
        with self.assertRaises(RuntimeError):
            bench.clock_epoch(reply("clock", "OK"))

    def test_passive_capture_matches_key_and_minimum_epoch_without_command(self):
        identity = bytes(range(32))
        witness = types.SimpleNamespace(condition=threading.Condition(), failed=False)
        raw = bytes((17, 0)) + identity + (200).to_bytes(4, "little") + bytes(64)
        witness.lines = [(8, "RAW: " + raw.hex()), (12, "RAW: " + raw.hex())]
        observed = bench.await_advert(witness, identity, after=10, minimum_epoch=200, timeout=.1)
        self.assertEqual(observed, {"received_monotonic": 12, "advert_epoch": 200})
        with self.assertRaises(RuntimeError):
            bench.await_advert(witness, identity, after=10, minimum_epoch=201, timeout=.01)
        with self.assertRaises(RuntimeError):
            bench.await_advert(witness, identity, after=10, minimum_epoch=100,
                               maximum_epoch=150, timeout=.01)

    def test_preexisting_manual_mode_is_not_changed_during_failed_preflight(self):
        calls = []
        collector_events = []

        class FakeConsole:
            def __init__(self, serial_number, path):
                self.serial_number = serial_number

            def command(self, command, **kwargs):
                calls.append(command)
                values = {"get public.key": "12" * 32, "board": "SolarXiao 33S" if self.serial_number.startswith("9E") else "RAK 3401",
                          "ver": "verified build", "get power.emergency": "mode=manual active=on battery=unknown clock=rtc window=sleep radio=sleep wake=200s hold=0s"}
                return reply(command, values[command])

            def close(self):
                pass

        @contextmanager
        def collector():
            collector_events.append("pause")
            try:
                yield
            finally:
                collector_events.append("restore")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "baseline.json"
            backup.write_text(json.dumps({"usb_serial": bench.TARGETS["solarxiao33s"][0],
                                          "queries": {"get public.key": reply("get public.key", "12" * 32)}}))
            args = types.SimpleNamespace(run_dir=root / "run", identity_backup=backup,
                                         target="solarxiao33s", witness=None, expected_version="verified build")
            with mock.patch.object(bench, "Console", FakeConsole), mock.patch.object(bench, "paused_collector", collector), \
                    mock.patch.object(bench, "snapshot", return_value={}), mock.patch("builtins.print"):
                with self.assertRaisesRegex(RuntimeError, "automatic mode"):
                    bench.run_trial(args)
            self.assertFalse(any(command.startswith(("set ", "time ", "clkreboot")) for command in calls))
            self.assertEqual(collector_events, ["pause", "restore"])
            report = json.loads((args.run_dir / "report.json").read_text())
            self.assertFalse(report["passed"])
            self.assertEqual(report["cleanup"], "complete")

    def test_default_plan_opens_no_port_or_service(self):
        arguments = ["bench", "--target", "rak3401", "--expected-version", "verified", "--identity-backup", "unused.json", "--run-dir", "unused"]
        with mock.patch("sys.argv", arguments), mock.patch.object(bench, "run_trial") as run, mock.patch("builtins.print"):
            bench.main()
        run.assert_not_called()

    def test_console_initialization_delimits_stale_input_without_reset_pulse(self):
        writes = []

        class Port:
            def __init__(self, **kwargs):
                self.dtr = self.rts = None

            def open(self):
                self_values = (self.dtr, self.rts, self.port)
                if self_values != (True, False, "/dev/ttyACM10"):
                    raise AssertionError(self_values)

            def write(self, value):
                writes.append(value)

            def flush(self):
                pass

            def close(self):
                pass

        serial_module = types.SimpleNamespace(Serial=Port)
        ports = types.SimpleNamespace(comports=lambda: [types.SimpleNamespace(
            serial_number="9EBCE8853076EE86", interface="TinyUSB Serial", device="/dev/ttyACM10")])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict("sys.modules", {"serial": serial_module, "serial.tools": types.SimpleNamespace(list_ports=ports)}), \
                mock.patch.object(bench.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"")), \
                mock.patch.object(bench.threading, "Thread"), mock.patch.object(bench.time, "sleep"):
            console = bench.Console("9EBCE8853076EE86", Path(directory) / "private.log")
            console.close()
        self.assertEqual(writes, [b"\r"])

    def test_snapshot_preserves_disabled_radio2_ownership_annotation(self):
        values = {"radio": "910.5250244,62.5,7,5", "tx": "-6", "repeat": "on", "tempradio": "off",
                  "radio.rxps.config": "off,level=0,preamble=0,rx=65625,sleep=60000",
                  "usb.logging": "on", "usb.logging.always": "off", "radioat": "-none-",
                  "tempradioat": "-none-", "radioat2": "slots:", "tempradioat2": "slots:"}
        console = types.SimpleNamespace(command=lambda command, **kwargs: reply(command, values[command[4:]]))
        for disabled in ("off", "off (manual)", "off (automatic allowed)"):
            values["radio2"] = disabled
            self.assertEqual(bench.snapshot(console)["radio2"], disabled)
        values["radio2"] = "911.150,250,7,5,rxtx,32"
        with self.assertRaisesRegex(RuntimeError, "inactive"):
            bench.snapshot(console)
        values["radio2"] = "off"
        for key in ("radioat", "tempradioat", "radioat2", "tempradioat2"):
            previous = values[key]
            values[key] = "1:911.5,125,7,5@1900000000"
            with self.assertRaisesRegex(RuntimeError, "empty future"):
                bench.snapshot(console)
            values[key] = previous

    def test_radio_admission_never_confuses_reboot_required_with_live_retune(self):
        calls = []
        def command(text):
            calls.append(text)
            return reply(text, "OK - reboot to apply")
        console = types.SimpleNamespace(command=command)
        with mock.patch.object(bench.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "saved getter"):
                bench.apply_radio(console, bench.BENCH_RADIO)
        self.assertEqual(calls, ["set radio " + bench.BENCH_RADIO])
        sleep.assert_not_called()

    def test_rf_witness_cannot_share_an_original_main_frequency(self):
        target = {"radio": "910.5250244,62.5,7,5"}
        bench.require_distinct_original_frequencies(target, {"radio": "917,62.5,7,5"})
        with self.assertRaisesRegex(RuntimeError, "different original"):
            bench.require_distinct_original_frequencies(target, {"radio": "910.525,125,8,5"})

    def test_wireless_snapshot_distinguishes_pending_and_failure(self):
        console = types.SimpleNamespace(command=lambda command: reply(command,
            "wifi=on, bluetooth=unavailable, espnow=off (pending)"))
        self.assertEqual(bench.wireless_snapshot(console),
                         {"wifi": "on", "bluetooth": "unavailable", "espnow": "off", "pending": True})
        console.command = lambda command: reply(command,
            "wifi=off, bluetooth=unavailable, espnow=off (failed: retry command)")
        with self.assertRaisesRegex(RuntimeError, "failed transition"):
            bench.wireless_snapshot(console)

    def test_network_clock_hold_restores_only_original_runtime_subset(self):
        calls = []
        live = {"wifi": "on", "bluetooth": "unavailable", "espnow": "off"}
        refused = [False]
        def command(text, **kwargs):
            calls.append(text)
            if text == "get 2.4ghz":
                return reply(text, ", ".join(key + "=" + value for key, value in live.items()))
            if text == "get power.emergency":
                return reply(text, "mode=manual active=on battery=100% clock=rtc window=awake radio=awake wake=0s hold=60s")
            if text == "set wifi off force":
                if not refused[0]:
                    refused[0] = True
                    return reply(text, "Error: emergency sleep; use set power.awake <minutes> first")
                live["wifi"] = "off"
            elif text == "set wifi on":
                live["wifi"] = "on"
            elif text not in ("set power.awake 0", "set power.awake 1"):
                raise AssertionError(text)
            return reply(text, "OK")
        console = types.SimpleNamespace(command=command)
        original = bench.wireless_snapshot(console)
        with mock.patch.object(bench.time, "sleep"):
            bench.hold_network_clock(console)
            self.assertEqual(live["wifi"], "off")
            bench.restore_wireless(console, original)
        self.assertEqual(bench.wireless_snapshot(console), original)
        self.assertEqual(calls.count("set wifi off force"), 2)
        self.assertEqual(calls.count("set wifi on"), 1)
        self.assertFalse(any("ssid" in value or "pwd" in value or "password" in value for value in calls))


if __name__ == "__main__":
    unittest.main()
