"""Preamble benchmark policy, accounting and subprocess protocol, no hardware."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import ethernet_preamble_bench as bench


class FakeNode:
    def __init__(self):
        self.identity = {"port": "fake-only"}
        self.commands = []
        self.guard = "> " + bench.GUARD
        self.radio = "> 910.5250244,62.5,7,5"
        self.tuple = "909.5,500,5,5,128"
        self.primary = self.secondary = self.rx = self.fail = 0
        self.enabled = True
        self.bad_set = self.bad_readback = False

    def command(self, text, timeout=10):
        self.commands.append(text)
        if text == "get lab.tx": return self.guard
        if text == "get radio": return self.radio
        if text == "get radio2.status": return f"> rxtx; RX=0,{self.rx} TX={self.primary},{self.secondary} switches=24"
        if text == "get radio2.timing": return "> need=32,128; switch=2000us"
        if text == "stats-radio-diag": return "> state=1(RX), recv=7, sent=9, errors=0"
        if text == "get ethernet":
            return f"> ethernet={'on; active (RX time-shared)' if self.enabled else 'off; off'}; TCP 5003 listening; RX={self.rx} TX={self.secondary} fail={self.fail}; radio2 only"
        if text == "get ethernet.radio": return "> " + ("909.5,500,6,5,128" if self.bad_readback else self.tuple) + "; sync=0x16; saved"
        if self.bad_set: return "Error: could not save"
        if text == "set ethernet off": self.enabled = False
        if text == "set ethernet on": self.enabled = True
        if text.startswith("set ethernet.radio "): self.tuple = text.removeprefix("set ethernet.radio ")
        return "OK - saved"


def result(seconds=2, **extra):
    return dict(verified=True, handshake_ms=7000, total_ms=11000,
        downlink=dict(bytes=4096, elapsed_ms=seconds * 1000, hash_verified=True),
        uplink=dict(bytes=4096, elapsed_seconds=seconds, hash_verified=True), **extra)


class EthernetPreambleBenchTests(unittest.TestCase):
    def run_fake(self, preambles=(128, 192), runner=None, nodes=None, **kwargs):
        nodes = nodes or dict(a=FakeNode(), b=FakeNode())
        return bench.run_cases(nodes, preambles, runner or (lambda request: result()), **kwargs), nodes

    def test_default_only_requested_preambles_and_explicit_controls(self):
        self.assertEqual(bench.DEFAULT_PREAMBLES, (128, 192, 256, 384, 512, 768))
        self.assertEqual(bench.validate_preambles([32, 64, 2048, 32]), [32, 64, 2048, 32])
        for values in ([], [31], [2049], [True], [128.0], [128] * 121):
            with self.assertRaises(ValueError): bench.validate_preambles(values)

    def test_sf5_default_unchanged_and_allowed_fixed_spreading_factors(self):
        default, _ = self.run_fake(preambles=(128,))
        self.assertEqual(default["sf"], 5)
        self.assertEqual(default["cases"][0]["preamble_ms"], 8.192)
        for sf in range(5, 10):
            calls = []
            report, nodes = self.run_fake(preambles=(64,), sf=sf,
                runner=lambda request: calls.append(request) or result())
            self.assertTrue(report["passed"], report.get("error"))
            self.assertEqual(calls[0]["sf"], sf)
            self.assertEqual(report["ranking"][0]["sf"], sf)
            self.assertEqual(report["cases"][0]["preamble_ms"], 64 * (2 ** sf) / 500)
            self.assertIn(f"SF{sf}", report["note"])
            for node in nodes.values():
                self.assertIn(f"set ethernet.radio 909.5,500,{sf},5,64", node.commands)

    def test_bad_sf_fails_before_commands_runner_or_snapshot(self):
        for sf in (4, 10, True, 7.0, "7", None):
            nodes = dict(a=FakeNode(), b=FakeNode()); run = Mock()
            with self.assertRaises(ValueError): bench.run_cases(nodes, [128], run, sf=sf)
            run.assert_not_called()
            self.assertTrue(all(not node.commands for node in nodes.values()))
            with self.assertRaises(ValueError): bench.snapshot(nodes, 12, sf=sf)
            self.assertTrue(all(not node.commands for node in nodes.values()))

    def test_bandwidth_defaults_to500_and125_propagates_to_fixed_tuple_and_timing(self):
        default, _ = self.run_fake(preambles=(128,))
        self.assertEqual(default["bandwidth_khz"], 500)
        for bandwidth in (125, 500):
            calls = []
            report, nodes = self.run_fake(preambles=(64,), bandwidth=bandwidth,
                runner=lambda request: calls.append(request) or result())
            self.assertTrue(report["passed"], report.get("error"))
            self.assertEqual(calls[0]["bandwidth_khz"], bandwidth)
            self.assertEqual(report["ranking"][0]["bandwidth_khz"], bandwidth)
            self.assertEqual(report["cases"][0]["preamble_ms"], 64 * 32 / bandwidth)
            self.assertEqual(report["cases"][0]["case_id"], "case-001-p64")
            for node in nodes.values():
                self.assertIn(f"set ethernet.radio 909.5,{bandwidth},5,5,64", node.commands)
            self.assertIn(f"> 909.5,{bandwidth},5,5,64", report["cases"][0]["before"]["a"]["ethernet_radio"])

    def test_invalid_bandwidth_fails_before_all_commands_and_runner(self):
        for bandwidth in (62.5, 250, 126, True, 125.0, "125", None):
            nodes = dict(a=FakeNode(), b=FakeNode()); run = Mock()
            with self.assertRaises(ValueError): bench.run_cases(nodes, [64], run, bandwidth=bandwidth)
            with self.assertRaises(ValueError): bench.snapshot(nodes, 12, bandwidth=bandwidth)
            run.assert_not_called(); self.assertTrue(all(not node.commands for node in nodes.values()))

    def test_cli_bandwidth125_plan_has_no_io_and_wrong_bandwidth_readback_aborts(self):
        output = io.StringIO()
        with patch.object(bench, "SerialNode") as serial, contextlib.redirect_stdout(output):
            self.assertEqual(bench.main(["--plan", "--bandwidth", "125", "--sf", "5"]), 0)
        serial.assert_not_called()
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["radio"], "909.5,125,5,5")
        self.assertEqual(plan["bandwidth_khz"], 125)
        for value in ("250", "125.0"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bench.main(["--plan", "--bandwidth", value])
        nodes = dict(a=FakeNode(), b=FakeNode())
        original = nodes["b"].command
        nodes["b"].command = lambda text, **kwargs: "> 909.5,500,5,5,64" if text == "get ethernet.radio" else original(text, **kwargs)
        run = Mock(return_value=result())
        report, _ = self.run_fake(nodes=nodes, bandwidth=125, preambles=(64,), runner=run)
        self.assertTrue(report["aborted"]); run.assert_not_called()

    def test_sf7_readback_requires_sf7_not_previous_sf5_or_other_sf(self):
        nodes = dict(a=FakeNode(), b=FakeNode())
        run = Mock(return_value=result())
        report, _ = self.run_fake(nodes=nodes, sf=7, preambles=(128,), runner=run)
        self.assertTrue(report["passed"])
        self.assertIn("> 909.5,500,7,5,128", report["cases"][0]["before"]["a"]["ethernet_radio"])
        nodes = dict(a=FakeNode(), b=FakeNode()); nodes["b"].bad_readback = True
        run = Mock(return_value=result())
        report, _ = self.run_fake(nodes=nodes, sf=7, preambles=(128,), runner=run)
        self.assertTrue(report["aborted"]); run.assert_not_called()

    def test_cli_plan_sf_choices_and_no_io(self):
        for sf in range(5, 10):
            output = io.StringIO()
            with patch.object(bench, "SerialNode") as serial, patch.object(bench, "python_runner") as runner, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(bench.main(["--plan", "--sf", str(sf), "--preambles", "32,64"]), 0)
            serial.assert_not_called(); runner.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["radio"], f"909.5,500,{sf},5")
        for sf in ("4", "10", "7.0"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bench.main(["--plan", "--sf", sf])

    def test_bad_initial_guard_radio_or_counter_prevents_all_writes_and_runner(self):
        for field, value in (("guard", "> Error: unavailable"), ("radio", "> 909.5,500,5,5"), ("primary", 1)):
            nodes = dict(a=FakeNode(), b=FakeNode()); setattr(nodes["b"], field, value)
            runner = Mock()
            report, _ = self.run_fake(nodes=nodes, runner=runner)
            self.assertTrue(report["aborted"])
            runner.assert_not_called()
            self.assertFalse(any(text.startswith("set ") for node in nodes.values() for text in node.commands))

    def test_exact_fixed_lab_tuple_only_and_complete_rf_counters(self):
        nodes = dict(a=FakeNode(), b=FakeNode())
        calls = []
        def run(request):
            calls.append(request)
            for node in nodes.values():
                node.secondary += 20; node.rx += 19; node.fail += 1
            return result(tcp_client=dict(OutSegs=80, RetransSegs=3))
        report, _ = self.run_fake(nodes=nodes, runner=run)
        self.assertTrue(report["passed"], report.get("error"))
        self.assertEqual([call["preamble"] for call in calls], [128, 192])
        for call in calls:
            self.assertEqual((call["frequency_mhz"], call["bandwidth_khz"], call["sf"], call["cr"]), (909.5, 500, 5, 5))
        for node in nodes.values():
            self.assertEqual([c for c in node.commands if c.startswith("set ")], [
                "set ethernet off", "set ethernet.radio 909.5,500,5,5,128", "set ethernet on",
                "set ethernet off", "set ethernet.radio 909.5,500,5,5,192", "set ethernet on"])
        for case in report["cases"]:
            self.assertEqual(case["counter_deltas"]["a"]["profile_tx"], [0, 20])
            self.assertEqual(case["counter_deltas"]["b"]["ethernet"], dict(rx=19, tx=20, fail=1))
            self.assertIn("sent=9", case["after"]["a"]["radio_diag"])
            self.assertEqual(case["metrics"]["tcp_client"]["RetransSegs"], 3)

    def test_failed_config_or_readback_aborts_before_runner(self):
        for field in ("bad_set", "bad_readback"):
            nodes = dict(a=FakeNode(), b=FakeNode()); setattr(nodes["b"], field, True)
            runner = Mock()
            report, _ = self.run_fake(nodes=nodes, runner=runner)
            self.assertTrue(report["aborted"])
            runner.assert_not_called()

    def test_runner_timeout_records_failure_disables_both_then_continues_no_retry(self):
        runner = Mock(side_effect=[subprocess.TimeoutExpired("redacted", 240), result()])
        report, nodes = self.run_fake(runner=runner)
        self.assertFalse(report["aborted"], report.get("error"))
        self.assertFalse(report["passed"])
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(report["cases"][0]["error"], "Runner timed out")
        self.assertEqual([case["verified"] for case in report["cases"]], [False, True])
        for node in nodes.values():
            self.assertEqual(node.commands.count("set ethernet off"), 3)

    def test_runner_false_hash_or_bad_protocol_does_not_rank_and_continues(self):
        for failed in (dict(verified=False, error="SSH deadline"), "not a result", dict(verified=1)):
            report, _ = self.run_fake(runner=Mock(side_effect=[failed, result()]))
            self.assertFalse(report["aborted"])
            self.assertEqual([item["index"] for item in report["ranking"]], [2])
        bad_hash = result(); bad_hash["uplink"]["hash_verified"] = False
        report, _ = self.run_fake(runner=lambda request: bad_hash)
        self.assertFalse(report["passed"])
        self.assertEqual(report["ranking"], [])

    def test_safety_change_after_runner_aborts_remaining_cases(self):
        for field, value in (("primary", 1), ("guard", "> unlocked"), ("radio", "> 909.5,500,5,5")):
            nodes = dict(a=FakeNode(), b=FakeNode())
            def changed(request):
                setattr(nodes["b"], field, value)
                return result()
            report, _ = self.run_fake(nodes=nodes, runner=changed)
            self.assertTrue(report["aborted"])
            self.assertEqual(len(report["cases"]), 1)
            self.assertEqual(report["ranking"], [])

    def test_actual_verified_goodput_ranks_even_with_retransmissions_and_excludes_handshake(self):
        first = result(1, tcp_client=dict(RetransSegs=6)); second = result(2, tcp_client=dict(RetransSegs=0))
        first["handshake_ms"] = 9000
        report, _ = self.run_fake(runner=Mock(side_effect=[first, second]))
        self.assertEqual([item["preamble"] for item in report["ranking"]], [128, 192])
        self.assertEqual(report["ranking"][0]["combined_payload_kbps"], 32.768)
        self.assertAlmostEqual(report["ranking"][0]["end_to_end_kbps"], 8192 * 8 / 11000)
        self.assertEqual(report["ranking"][0]["downlink_kbps"], 32.768)

    def test_unknown_tcp_metrics_and_missing_or_reset_counters_are_not_zero(self):
        metrics = bench.payload_metrics(result())
        self.assertIsNone(metrics["tcp_client"]); self.assertIsNone(metrics["tcp_server"])
        self.assertIsNone(bench.ethernet_counters("> unavailable"))
        self.assertIsNone(bench.counter_delta(10, 2))
        self.assertIsNone(bench.counter_delta(None, None))
        self.assertIsNone(bench.payload_metrics(dict(verified=True))["combined_payload_kbps"])
        self.assertFalse(bench.payload_metrics(dict(verified=True))["verified"])
        unknown_hash = result(); del unknown_hash["uplink"]["hash_verified"]
        self.assertFalse(bench.payload_metrics(unknown_hash)["verified"])

    def test_progress_persists_running_before_runner_and_complete_after_checks(self):
        events, saves = [], []
        report, _ = self.run_fake(preambles=(128,), progress=events.append,
            persist=lambda value: saves.append(json.loads(json.dumps(value))))
        self.assertEqual([event["phase"] for event in events], ["checking", "running", "complete"])
        running = next(save for save in saves if save["cases"] and save["cases"][0]["phase"] == "running")
        self.assertNotIn("runner", running["cases"][0])
        self.assertIn("commands", saves[-1])

    def test_plan_has_no_io_and_seeded_repeats_are_explicit(self):
        output = io.StringIO()
        with patch.object(bench, "SerialNode") as serial, patch.object(bench, "SshSerialNode") as ssh, \
                patch.object(bench, "python_runner") as runner, patch.object(bench.tempfile, "mkdtemp") as folder, \
                contextlib.redirect_stdout(output):
            self.assertEqual(bench.main(["--plan", "--preambles", "512,128,384,192,768,256", "--runs", "2", "--seed", "7"]), 0)
        for mock in (serial, ssh, runner, folder): mock.assert_not_called()
        plan = json.loads(output.getvalue())
        self.assertEqual(len(plan["preambles"]), 12)
        self.assertEqual(sorted(plan["preambles"][:6]), list(bench.DEFAULT_PREAMBLES))

    def test_runner_subprocess_exact_json_no_shell_and_no_stderr_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.py"; path.touch()
            completed = SimpleNamespace(stdout=json.dumps(result()), stderr="PRIVATE KEY", returncode=0)
            with patch.object(bench.subprocess, "run", return_value=completed) as process:
                value = bench.python_runner(path)(dict(preamble=128))
            args, kwargs = process.call_args
            self.assertEqual(args[0], [sys.executable, str(path)])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["timeout"], 240)
            self.assertEqual(json.loads(kwargs["input"]), dict(preamble=128))
            self.assertNotIn("PRIVATE KEY", json.dumps(value))
            completed.returncode = 2
            with patch.object(bench.subprocess, "run", return_value=completed):
                self.assertFalse(bench.python_runner(path)(dict(preamble=128))["verified"])


if __name__ == "__main__":
    unittest.main()
