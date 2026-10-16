"""Offline guarded four-node transfer controller tests; no serial/RF/PIO."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("chain_under_test", ROOT / "tools/hil/mota_espnow_chain.py")
CHAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHAIN)
MID = "ee375e77"


class Simulation:
    def __init__(self, *, off_leak=False, midpoint_leak=False, instantly_done=False, bad_guard=False):
        self.now, self.have, self.total = 100.0, 0, 9
        self.off_leak, self.midpoint_leak = off_leak, midpoint_leak
        self.instantly_done, self.bad_guard = instantly_done, bad_guard
        self.bridge_on, self.active, self.done = True, False, False
        self.commands, self.reports, self.resets = [], [], 0
        owner = self

        class Node:
            def __init__(self, name):
                self.name = name

            def command(self, text, timeout=15):
                owner.commands.append((self.name, text))
                frequency = 909.5 if self.name == "a" else 908.5
                if text == "get lab.tx":
                    value = ("bad" if owner.bad_guard else f"HIL TX lock={frequency:.3f} MHz; off-frequency TX blocked; primary RX-only when off-frequency")
                elif text == "get tempradio":
                    value = f"> {frequency:.3f},500.000,5,5,128"
                elif text in ("get radio2", "get tempradio2"):
                    value = "> off"
                elif text == "get repeat":
                    value = "> on"
                elif text in ("get espnow.enabled", "get espnow.running"):
                    value = "> " + ("on" if self.name == "a" or owner.bridge_on else "off")
                elif text.startswith("set espnow.enabled "):
                    owner.bridge_on = text.endswith(" on")
                    value = "OK"
                elif text == "ota self":
                    value = "self body=540000 image=540912 base_hash=AB0CA97D3E4D11FE abi=3 codecs=0x5"
                elif text.startswith("ota config "):
                    value = "OK saved"
                elif text == "ota config":
                    value = "ota config: speed=1.1x autofetch=off autoinstall=off hops=3"
                elif text == "ota ls":
                    value = "Catalogue:\n1) EE375E77 target=abcd codec2\n2) DEADBEEF ignored"
                elif text == "ota cancel":
                    owner.have, owner.active, owner.done = 0, False, False
                    owner.resets += 1
                    value = "OK dropped session"
                elif text == "ota pull " + MID + " flash":
                    owner.active = True
                    value = "OK pulling mid=EE375E77 -> flash (primary traffic)"
                elif text == "ota stats":
                    if not owner.active:
                        value = "OTA | fw v1 id=deadbeef | fetch idle | af=off hops=3"
                    else:
                        if owner.bridge_on:
                            owner.have = owner.total if owner.instantly_done else min(owner.total, owner.have + 1)
                        elif owner.off_leak or (owner.midpoint_leak and owner.have):
                            owner.have = min(owner.total, owner.have + 1)
                        state = "done" if owner.have == owner.total else "dl"
                        value = f"OTA | fw v1 id=deadbeef | fetch {state} {owner.have}/{owner.total} 0% id=EE375E77 3s | af=off hops=3"
                elif text == "ota dev verify":
                    value = "verify parsed=1 root=1 payload=1 img=1 signed=0 sig=0 trust=0 | ok=1 auto=0"
                else:
                    raise AssertionError("unexpected command " + text)
                # Deliberately truncated echo matches the observed3401 path.
                return "o\r\n  -> " + value.replace("\n", "\r\n") + "\r\n> "

        self.nodes = {name: Node(name) for name in ("a", "b", "target")}

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now

    def run(self):
        with mock.patch.object(CHAIN.time, "monotonic", self.monotonic), mock.patch.object(CHAIN.time, "sleep", self.sleep):
            return CHAIN.run_chain(self.nodes, MID, timeout=30, outage=8, poll=.25,
                                   persist=lambda report: self.reports.append(report["result"]))


class ChainTests(unittest.TestCase):
    def test_plan_and_consent_open_no_hardware(self):
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(CHAIN, "serial_node_factory", side_effect=AssertionError):
            self.assertEqual(CHAIN.main(["--plan"]), 0)
        plan = json.loads(output.getvalue())
        self.assertFalse(plan["source_port_opened"])
        self.assertFalse(plan["install_commands"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            CHAIN.main([])

    def test_multiline_reply_and_truncated_echo_accepted(self):
        value = CHAIN.reply("o\r\n  -> catalogue:\r\n1) EE375E77\r\n2) DEADBEEF\r\n> ")
        self.assertEqual(value, "catalogue:\n1) EE375E77\n2) DEADBEEF")
        with self.assertRaises(CHAIN.Failure):
            CHAIN.reply("ota stats\n> ")

    def test_ambiguous_arrows_fail_closed_with_safe_bounded_history(self):
        raw = "o\r\n  -> stale\r\n  -> actual\r\n"
        with self.assertRaisesRegex(CHAIN.Failure, "ambiguous"):
            CHAIN.reply(raw)
        node = SimpleNamespace(commands=[{"command": "ota stats", "reply": "old"}] * 3 +
                                        [{"command": "ota stats", "reply": raw}])
        history = CHAIN.failure_history({"target": node})["target"]
        self.assertEqual(len(history), 3)
        self.assertEqual(history[-1]["arrow_markers"], 2)
        self.assertEqual(history[-1]["reply"], raw)
        secret = "aa" * 32
        record = CHAIN.diagnostic_record("ota stats", " -> " + secret + "x" * 4000)
        self.assertLess(len(record["reply"]), 1600)
        self.assertNotIn(secret, record["reply"])
        self.assertNotIn("reply", CHAIN.diagnostic_record("get prv.key", secret))

    def test_fresh_marker_reader_does_not_require_echo(self):
        simulation = Simulation()

        class Port:
            fragments = [b"o\r\n  -> fetch", b" dl 1/9\r\n", b"> "]
            in_waiting = 1

            def read(self, count):
                simulation.now += .01
                return self.fragments.pop(0) if self.fragments else b""

        with mock.patch.object(CHAIN.time, "monotonic", simulation.monotonic):
            self.assertIn("fetch dl 1/9", CHAIN.read_fresh_reply(Port(), 1))

    def test_classic_no_prompt_truncated_echo_multiline_quiet(self):
        simulation = Simulation()

        class Port:
            fragments = [b"o\r\n  -> catalogue:\r\n", b"1) EE375E77\r\n", b"2) DEADBEEF\r\n"]
            in_waiting = 1

            def read(self, count):
                simulation.now += .04
                return self.fragments.pop(0) if self.fragments else b""

        with mock.patch.object(CHAIN.time, "monotonic", simulation.monotonic):
            text = CHAIN.read_fresh_reply(Port(), 2)
        self.assertEqual(CHAIN.reply(text), "catalogue:\n1) EE375E77\n2) DEADBEEF")

    def test_classic_open_no_term_tokens_lf_ignored_cr_executes(self):
        simulation = Simulation()

        class Port:
            def __init__(self, **kwargs):
                self.writes, self.line, self.incoming, self.executed = [], bytearray(), bytearray(), []
                self.closed, self.dtr, self.rts = False, False, False

            @property
            def in_waiting(self):
                return len(self.incoming)

            def write(self, value):
                self.writes.append(value)
                for byte in value:
                    if byte == 10:
                        continue
                    if byte == 13:
                        text = self.line.decode()
                        self.line.clear()
                        if text:
                            self.executed.append(text)
                            self.incoming.extend(b"o\r\n  -> answer\r\n")
                    else:
                        self.line.append(byte)
                return len(value)

            def read(self, count):
                simulation.now += .04
                value = bytes(self.incoming[:count])
                del self.incoming[:count]
                return value

            def flush(self):
                pass

            def close(self):
                self.closed = True

        class Base:
            def __init__(self, path):
                raise AssertionError("Companion constructor must never run")

            def read(self, timeout, quiet):
                simulation.now += timeout
                result = bytes(self.port.incoming)
                self.port.incoming.clear()
                return result.decode()

        modules = {name: ModuleType(name) for name in ("serial", "serial.tools", "serial.tools.list_ports",
                                                      "serial_session", "ethernet_primary_probe")}
        modules["serial"].Serial = Port
        modules["serial.tools"].list_ports = modules["serial.tools.list_ports"]
        modules["serial.tools.list_ports"].comports = lambda: [SimpleNamespace(device="/dev/test", serial_number="exact",
                                                                               vid=0x1a86, pid=0x7523, location="physical")]
        modules["serial_session"].assert_port_idle = lambda path: None
        modules["serial_session"].open_configured_session = lambda port: None
        modules["ethernet_primary_probe"].SerialNode = Base
        with mock.patch.dict(sys.modules, modules), mock.patch.object(CHAIN.time, "monotonic", simulation.monotonic):
            node = CHAIN.serial_node_factory()("/dev/test")
            self.assertEqual(node.port.writes, [b"\r"])
            self.assertFalse(node.port.dtr)
            self.assertFalse(node.port.rts)
            self.assertEqual(CHAIN.reply(node.command("get lab.tx")), "answer")
            self.assertEqual(node.port.executed, ["get lab.tx"])
            self.assertEqual(node.port.writes[-1], b"get lab.tx\r")
            node.close()
            self.assertFalse(any(b"MESHCORE-TERM" in value for value in node.port.writes))
            self.assertTrue(node.port.closed)

    def test_full_counterfactual_midpoint_resume_hash_and_integrity_proof(self):
        simulation = Simulation()
        result = simulation.run()
        self.assertEqual(result["result"], "PASS", result.get("failure"))
        self.assertTrue(result["initial_off_no_completed_blocks"])
        self.assertGreater(result["midpoint_off_stalled"]["retained_blocks"], 0)
        self.assertTrue(result["bridge_b_restored"])
        self.assertEqual(result["final_progress"]["have"], 9)
        self.assertEqual(result["running_before"], result["running_after"])
        self.assertFalse(result["rf_capture_independent"])
        self.assertFalse(any(command.startswith(("ota install", "ota rescue", "ota dev apply")) for _, command in simulation.commands))
        self.assertTrue(all(not command.startswith(("set radio", "tempradio ", "reboot")) for _, command in simulation.commands))

    def test_off_path_data_fails_and_restores_bridge(self):
        simulation = Simulation(off_leak=True)
        result = simulation.run()
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("while required bridge", result["failure"]["message"])
        self.assertTrue(result["bridge_b_restored"])

    def test_instant_complete_is_not_claimed_midpoint_pass_retries_once(self):
        simulation = Simulation(instantly_done=True)
        result = simulation.run()
        self.assertEqual(result["result"], "TRANSFER_VERIFIED_MIDPOINT_NOT_PROVEN", result.get("failure"))
        self.assertEqual(simulation.resets, 2)
        self.assertNotIn("midpoint_off_stalled", result)
        self.assertTrue(result["bridge_b_restored"])

    def test_failed_guard_no_setters_or_stage_clear(self):
        simulation = Simulation(bad_guard=True)
        result = simulation.run()
        self.assertEqual(result["result"], "FAIL")
        self.assertTrue(all(command.startswith("get ") for _, command in simulation.commands))
        self.assertEqual(simulation.resets, 0)

    def test_progress_rejects_other_mid_truncated_and_invalid_counts(self):
        class Node:
            value = ""

            def command(self, command, timeout):
                return "o\n  -> " + self.value + "\n> "

        node = Node()
        for value in ("fetch dl 1/9 0% id=DEADBEEF", "fetch dl 1/9 0% id=EE37", "fetch dl 10/9 0% id=EE375E77"):
            node.value = value
            with self.assertRaises(CHAIN.Failure):
                CHAIN.progress(node, MID)


if __name__ == "__main__":
    unittest.main()
