"""R2 sweep safety gates and honest RF-loss reporting, without hardware."""
import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import ethernet_radio2_sweep as sweep


class FakeNode:
    def __init__(self):
        self.identity = {"port": "fake-only"}
        self.commands = []
        self.guard = "> " + sweep.GUARD
        self.radio = "> 910.525,62.5,7,5"
        self.ethernet = "909.5,62.5,7,5,64"
        self.primary = 0
        self.bad_config = self.bad_set = False

    def command(self, text, timeout=10):
        self.commands.append(text)
        if text == "get lab.tx": return self.guard
        if text == "get radio": return self.radio
        if text == "get radio2.status": return f"> rxtx; RX=0,4 TX={self.primary},2 switches=20"
        if text == "get radio2.timing": return "> chirps=4.60,20.0; need=32,64; switch=2000us"
        if text == "get ethernet": return "> ethernet=on; active (RX time-shared); TCP 5003 listening"
        if text == "get ethernet.radio":
            return "> " + ("910.525,62.5,7,5,64" if self.bad_config else self.ethernet) + "; sync=0x16; saved"
        if text.startswith("set ethernet.radio "):
            self.ethernet = text.removeprefix("set ethernet.radio ")
        return "Error: test failed" if self.bad_set else "OK - Ethernet saved"


class FakeEndpoint:
    def __init__(self):
        self.decoder = SimpleNamespace(errors=0)
        self.overflow = 0
        self.error = None
        self.closed = False

    def close(self): self.closed = True


def fake_probe(*endpoints, **kwargs):
    return {"passed": True, "transfers": [
        {"received": True, "size": 244}, {"received": True, "size": 244}], "retries": 0}


class EthernetRadio2SweepTests(unittest.TestCase):
    def run_fake(self, a=None, b=None, cases=None, probe=fake_probe, factory=None):
        endpoints = []
        def make(endpoint):
            item = FakeEndpoint(); endpoints.append(item); return item
        report = sweep.run_sweep(a or FakeNode(), b or FakeNode(), "tcp://a:5003", "tcp://b:5003",
                                 cases or sweep.CANDIDATES[:2], probe_function=probe,
                                 endpoint_factory=factory or make)
        return report, endpoints

    def test_guard_is_exact_structured_unambiguous_and_not_an_echo(self):
        for text in ("> " + sweep.GUARD, "get lab.tx\r\n  -> > " + sweep.GUARD + "\r\n> "):
            sweep.require_guard(text)
        for text in ("get lab.tx " + sweep.GUARD, "> lock=909.500 MHz", "> Error: unknown command",
                     "> " + sweep.GUARD.replace("909.500", "910.525"),
                     "> " + sweep.GUARD + "\n> " + sweep.GUARD):
            with self.assertRaises(RuntimeError): sweep.require_guard(text)

    def test_tuple_parser_rejects_echoes_ambiguous_and_wrong_preamble(self):
        self.assertEqual(sweep.radio_tuple("get radio\n  -> > 910.525,62.500,7,5\n> ")[:4], sweep.PRIMARY)
        for text in ("set radio 910.525,62.5,7,5", "> unavailable",
                     "> 910.525,62.5,7,5\n> 910.525,62.5,7,5"):
            with self.assertRaises(RuntimeError): sweep.radio_tuple(text)

    def test_bad_initial_guard_or_primary_prevents_every_setting_and_socket(self):
        for field, value in (("guard", "> Error: unknown command"), ("radio", "> 909.5,500,5,5")):
            a, b = FakeNode(), FakeNode(); setattr(b, field, value)
            factory = Mock()
            report, _ = self.run_fake(a, b, factory=factory)
            self.assertTrue(report["aborted"])
            factory.assert_not_called()
            self.assertFalse(any(command.startswith("set ") for node in (a, b) for command in node.commands))

    def test_primary_nonzero_counter_refuses_configuration_and_tx(self):
        a, b = FakeNode(), FakeNode(); b.primary = 1
        report, endpoints = self.run_fake(a, b)
        self.assertTrue(report["aborted"])
        self.assertEqual(endpoints, [])
        self.assertFalse(any(command.startswith("set ") for node in (a, b) for command in node.commands))

    def test_primary_float_rounding_tolerates_only_50_hz_and_exact_phy(self):
        for value in ("> 910.5250244,62.5,7,5", "> 910.5249756,62.5,7,5"):
            a, b = FakeNode(), FakeNode(); b.radio = value
            report, _ = self.run_fake(a, b)
            self.assertTrue(report["passed"], report.get("error"))
        for value in ("> 910.526,62.5,7,5", "> 910.5250501,62.5,7,5",
                      "> 910.5250244,62.5001,7,5", "> 910.5250244,62.5,8,5",
                      "> 910.5250244,62.5,7,6"):
            a, b = FakeNode(), FakeNode(); b.radio = value
            factory = Mock()
            report, _ = self.run_fake(a, b, factory=factory)
            self.assertTrue(report["aborted"])
            factory.assert_not_called()
            self.assertFalse(any(command.startswith("set ") for node in (a, b) for command in node.commands))

    def test_only_predefined_9095_ethernet_settings_and_no_primary_writes(self):
        a, b = FakeNode(), FakeNode()
        report, endpoints = self.run_fake(a, b)
        self.assertTrue(report["passed"])
        self.assertTrue(all(endpoint.closed for endpoint in endpoints))
        for node in (a, b):
            writes = [command for command in node.commands if command.startswith("set ")]
            self.assertEqual(writes, ["set ethernet off", "set ethernet.radio 909.5,62.5,7,5,64", "set ethernet on",
                                      "set ethernet off", "set ethernet.radio 909.5,125,7,5,64", "set ethernet on"])
        for item in report["cases"]:
            self.assertEqual(item["primary_tx_before"], {"a": 0, "b": 0})
            self.assertEqual(item["primary_tx_after"], {"a": 0, "b": 0})
            self.assertIn("verified R2", item["probe"]["baseline"])

    def test_wrong_readback_or_failed_setting_aborts_before_sockets(self):
        for field in ("bad_config", "bad_set"):
            a, b = FakeNode(), FakeNode(); setattr(b, field, True)
            report, endpoints = self.run_fake(a, b)
            self.assertTrue(report["aborted"])
            self.assertEqual(endpoints, [])
            self.assertEqual(len(report["cases"]), 1)

    def test_rf_packet_loss_is_reported_and_remaining_cases_continue(self):
        def loss(*endpoints, **kwargs):
            report = fake_probe(); report["passed"] = False
            report["transfers"][0]["received"] = False
            return report
        report, endpoints = self.run_fake(probe=loss)
        self.assertFalse(report["aborted"])
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["cases"]), 2)
        self.assertTrue(all(item["measurement"]["loss_fraction"] == 0.5 for item in report["cases"]))
        self.assertTrue(all(endpoint.closed for endpoint in endpoints))

    def test_primary_counter_change_or_guard_change_stops_later_cases(self):
        for field, value in (("primary", 1), ("guard", "> Error: lock disappeared")):
            a, b = FakeNode(), FakeNode()
            def changed(*endpoints, **kwargs):
                setattr(b, field, value)
                return fake_probe()
            report, endpoints = self.run_fake(a, b, probe=changed)
            self.assertTrue(report["aborted"])
            self.assertEqual(len(report["cases"]), 1)
            self.assertTrue(all(endpoint.closed for endpoint in endpoints))

    def test_transport_error_aborts_without_retry_and_closes_both_sockets(self):
        probe = Mock(side_effect=ConnectionError("WiFi lost"))
        report, endpoints = self.run_fake(probe=probe)
        self.assertTrue(report["aborted"])
        self.assertEqual(probe.call_count, 1)
        self.assertTrue(all(endpoint.closed for endpoint in endpoints))

    def test_candidate_rate_ratios_and_real_payload_definition(self):
        self.assertEqual([case.describe()["nominal_ratio_to_usa"] for case in sweep.CANDIDATES[:4]], [1, 2, 4, 8])
        self.assertAlmostEqual(sweep.CANDIDATES[6].describe()["nominal_ratio_to_usa"], 22.857142857)
        self.assertEqual(sweep.CANDIDATES[7].describe()["nominal_kbps"], 62.5)
        self.assertEqual(sweep.CANDIDATES[7].preamble, 2048)
        metrics = sweep.measurement(fake_probe(), 2)
        self.assertEqual(metrics["ethernet_goodput_bps"], 1952)
        self.assertEqual(metrics["ethernet_payload_goodput_bps"], 1840)
        self.assertIn("not SSH", metrics["goodput_definition"])

    def test_plan_does_not_open_serial_or_tcp_or_create_reports(self):
        with patch.object(sweep, "SerialNode") as serial, patch.object(sweep, "KissEndpoint") as tcp, \
                patch.object(sweep.tempfile, "mkdtemp") as directory, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sweep.main(["--plan", "--cases", "2,3,4"]), 0)
        serial.assert_not_called(); tcp.assert_not_called(); directory.assert_not_called()

    def test_only_port5003_tcp_endpoints_without_credentials_are_valid(self):
        for value in ("tcp://radio", "tcp://192.168.1.84:5003"):
            self.assertTrue(sweep.endpoint_valid(value))
        for value in ("serial:///dev/ttyUSB0", "tcp://radio:5002", "tcp://user:password@radio:5003",
                      "tcp://radio:5003/path", "tcp://radio:5003?x=1"):
            self.assertFalse(sweep.endpoint_valid(value))


if __name__ == "__main__":
    unittest.main()
