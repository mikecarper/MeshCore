"""Offline benchmark policy/orchestration tests; never open a radio or SDK."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/hil"
with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]):
    SPEC = importlib.util.spec_from_file_location("meshtext_speed_under_test", TOOLS / "meshtext_speed.py")
    SPEED = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(SPEED)


class Simulation:
    def __init__(self, *, guard=SPEED.GUARD, primary=910.525, primary_tx=0,
                 stock_frequency=909.5, queue_stuck=False, drop_outbound=False, drop_inbound=False):
        self.now = 100.0
        self.guard, self.primary, self.primary_tx = guard, primary, primary_tx
        self.queue_stuck, self.drop_outbound, self.drop_inbound = queue_stuck, drop_outbound, drop_inbound
        self.commands, self.stock_writes, self.sent, self.closed = [], [], [], []
        self.frames, self.events, self.sf, self.name, self.announced = [], [], 5, "", False
        self.tx, self.rx, self.stock_sequence = 0, 0, 0
        owner = self

        class Companion:
            max_channels = 2

            def __init__(self, port):
                owner.companion = self

            def start(self):
                return {"model": "SIMULATED", "primary": {"node_name": "Test"}}

            def close(self):
                owner.closed.append("companion")

            def channel_name(self, index):
                return owner.name if index == 1 else ""

            def mt_node(self):
                return 0x12345678

            def cli(self, command):
                owner.commands.append(command)
                if command == "get lab.tx":
                    return "> " + owner.guard
                if command == "get radio":
                    return f"> {owner.primary},62.5,7,5"
                if command == "get radio2.status":
                    return f"> rxtx; RX=0,{owner.rx} TX={owner.primary_tx},{owner.tx} preamble=32,16"
                if command == "get radio2":
                    return f"> 909.5,500,{owner.sf},5,rxtx,32"
                if command == "get mt.hops":
                    return "> 0"
                if command == "get mt.status":
                    return f"> Meshtext !12345678; active; RX={owner.rx} TX={owner.tx} fail=0"
                if command == "get mt.retry":
                    return "> retry=0 heard=0 exhausted=0 pending=0 count=0 advert=off"
                if command.startswith("set radio2 "):
                    owner.sf = int(command.split()[2].split(",")[2])
                if command.startswith("set mt.channel "):
                    owner.name, owner.announced = "mt-labtest", False
                if command.startswith("delete mt.channel "):
                    owner.name = ""
                return "OK"

            def send_text(self, slot, token):
                assert 7 + len(token.encode()) <= 176, "Companion USB payload envelope overflow"
                owner.sent.append(("companion", token))
                owner.tx += 1
                if not owner.announced:
                    owner.announced = True
                    owner.tx += 1
                owner.events.append((owner.now, {"from": 0x12345678, "id": len(owner.sent),
                    "decoded": {"payload": token.encode(), "portnum": "TEXT_MESSAGE_APP"}}))

            def request(self, payload, expected, timeout):
                assert payload == b"\x0a"
                return owner.frames.pop(0) if owner.frames else b"\x0a"

        class Node:
            def __init__(self):
                self.localConfig = SimpleNamespace(
                    lora=SimpleNamespace(use_preset=False, override_frequency=stock_frequency,
                        bandwidth=500, coding_rate=5, spread_factor=5, frequency_offset=0,
                        hop_limit=0, tx_power=2, tx_enabled=True, channel_num=0),
                    device=SimpleNamespace(role=1))
                self.channels = [SimpleNamespace(role=1, settings=SimpleNamespace(name="labtest", psk=b"a"*16))]

            def beginSettingsTransaction(self):
                owner.stock_writes.append("begin")

            def writeConfig(self, section):
                owner.stock_writes.append(section)

            def commitSettingsTransaction(self):
                owner.stock_writes.append("commit")

        class Interface:
            def __init__(self, node):
                self.localNode = node
                self.isConnected = True
                self.stream = SimpleNamespace(is_open=True)
                self.queue = {}
                self.queueStatus = SimpleNamespace(free=4, maxlen=4, res=0, mesh_packet_id=0)

            def close(self):
                self.isConnected = False
                self.stream.is_open = False

        class Stock:
            node = 0x11223344

            def __init__(self, port):
                self.saved_node = Node()
                self.connect()
                owner.stock = self

            def connect(self):
                self.iface = Interface(self.saved_node)

            def close(self):
                owner.closed.append("stock")

            def configure_lab(self, key, role):
                assert role == "CLIENT_MUTE"
                self.saved_node.channels[0].settings.psk = key
                owner.stock_writes.append("suppression")
                return {"role": role, "simulated": True}

            def send_text(self, token, hops):
                assert hops == 0
                owner.sent.append(("stock", token))
                owner.stock_sequence += 1
                identity = owner.stock_sequence
                if owner.queue_stuck:
                    self.iface.queue[identity] = object()
                self.iface.queueStatus.mesh_packet_id = identity
                if not owner.drop_inbound:
                    prefix = b"!11223344: "
                    text = (prefix + token.encode())[:165]
                    owner.frames.append(bytes((17, 0, 0, 0, 1, 0, 0)) + struct.pack("<I", 0) + text)
                    owner.rx += 1
                return SimpleNamespace(id=identity)

            def wait_packet(self, sender, predicate, since, timeout):
                if not owner.drop_outbound:
                    return next((packet for when, packet in owner.events
                                 if when >= since and packet["from"] == sender and predicate(packet)), None)
                owner.sleep(timeout)
                return None

        self.classes = Companion, Stock

    def sleep(self, duration):
        self.now += duration


class MeshtextSpeedTest(unittest.TestCase):
    def run_simulated(self, simulation, extra=()):
        directory = tempfile.TemporaryDirectory(prefix="meshtext-speed-offline-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "report.json"
        with mock.patch.object(SPEED.delivery, "dependencies", return_value=simulation.classes), \
             mock.patch.object(SPEED.delivery, "require_idle_port"), \
             mock.patch.object(sys, "path", [str(TOOLS), *sys.path]), \
             mock.patch.object(SPEED.time, "monotonic", side_effect=lambda: simulation.now), \
             mock.patch.object(SPEED.time, "sleep", side_effect=simulation.sleep), \
             redirect_stdout(io.StringIO()):
            status = SPEED.main(["--allow-radio-changes", "--companion-port", "/dev/FAKE-A",
                "--stock-port", "/dev/FAKE-B", "--report", str(path), "--sf", "7", "--count", "2", *extra])
        return status, json.loads(path.read_text())

    def test_plan_has_no_dependencies_or_ports_and_never_uses_preset_frequencies(self):
        with mock.patch.object(SPEED.delivery, "dependencies") as deps, \
             mock.patch.object(SPEED.delivery, "require_idle_port") as ports, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(SPEED.main(["--plan"]), 0)
        deps.assert_not_called()
        ports.assert_not_called()
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["frequency_mhz"], 909.5)
        self.assertEqual(plan["spreading_factors"], [7, 9, 10])
        self.assertEqual(plan["preamble_symbols"], 16)

    def test_consent_invalid_parameters_same_ports_and_existing_report_refuse_before_open(self):
        for arguments in ([], ["--plan", "--count", "0"], ["--plan", "--sf", "7", "7"],
                          ["--plan", "--gap", "0"], ["--plan", "--timeout", "31"],
                          ["--allow-radio-changes", "--companion-port", "same", "--stock-port", "same", "--report", "new.json"]):
            with self.subTest(arguments=arguments), mock.patch.object(SPEED.delivery, "dependencies") as deps, \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                SPEED.main(arguments)
            deps.assert_not_called()

    def test_complete_run_measures_both_directions_and_truthful_truncation(self):
        simulation = Simulation()
        status, data = self.run_simulated(simulation, ["--sf", "7", "9", "10"])
        self.assertEqual(status, 0)
        self.assertEqual(data["result"], "MEASURED")
        self.assertEqual(len(data["cases"]), 12)
        self.assertEqual(set(data["stock_profiles"]), {"7", "9", "10"})
        self.assertEqual(simulation.stock_writes.count("suppression"), 1)
        for case in data["cases"]:
            self.assertEqual(case["measurement"]["received"], 2)
            self.assertEqual(case["measurement"]["loss_fraction"], 0)
            self.assertEqual(case["nominal_lora_kbps"], SPEED.nominal_kbps(case["sf"]))
            expected = 154 if case["direction"] == "stock-to-companion" and case["wire_bytes"] == 160 else case["wire_bytes"]
            self.assertEqual(case["measurement"]["application_bytes_delivered"], 2 * expected)
            self.assertEqual(case["safety_after"]["primary_tx"], 0)
        self.assertFalse(any(command.startswith(("set radio ", "advert", "reboot", "set freq ")) for command in simulation.commands))
        self.assertEqual(sorted(simulation.closed), ["companion", "stock"])

    def test_guard_primary_and_counter_fail_before_any_configuration(self):
        for simulation in (Simulation(guard="HIL TX lock=909.500 MHz"), Simulation(primary=909.5), Simulation(primary_tx=1)):
            with self.subTest(simulation=simulation):
                status, data = self.run_simulated(simulation)
                self.assertEqual(status, 1)
                self.assertEqual(data["result"], "FAIL")
                self.assertFalse(simulation.stock_writes or simulation.sent)
                self.assertFalse(any(command.startswith("set ") for command in simulation.commands))

    def test_wrong_initial_stock_frequency_is_not_reconfigured_or_transmitted(self):
        simulation = Simulation(stock_frequency=910.525)
        status, data = self.run_simulated(simulation)
        self.assertEqual(status, 1)
        self.assertFalse(simulation.stock_writes or simulation.sent)
        self.assertIn("stock must use custom 909.5", data["failure"]["message"])

    def test_packet_id_queue_depletion_prevents_a_second_sdk_send(self):
        simulation = Simulation(queue_stuck=True)
        status, data = self.run_simulated(simulation)
        self.assertEqual(status, 1)
        self.assertEqual(sum(who == "stock" for who, _ in simulation.sent), 1)
        self.assertIn("TX completion uncertain", data["failure"]["message"])
        self.assertEqual(data["cases"][-1]["packets"][-1]["tx_completion"], "uncertain")

    def test_loss_is_reported_not_promoted_to_successful_phy_transmission(self):
        status, data = self.run_simulated(Simulation(drop_outbound=True, drop_inbound=True))
        self.assertEqual(status, 0)
        for case in data["cases"]:
            self.assertEqual(case["measurement"]["received"], 0)
            self.assertEqual(case["measurement"]["measured_application_goodput_bps"], 0)
            if case["direction"] == "stock-to-companion":
                self.assertTrue(all("uncertain" in packet["tx_completion"] for packet in case["packets"]))

    def test_sdk_ack_and_empty_queue_are_not_claimed_as_rf_completion(self):
        simulation = Simulation()
        stock = simulation.classes[1]("not-a-port")
        stock.iface.queueStatus.mesh_packet_id = 99
        with mock.patch.object(SPEED.time, "monotonic", side_effect=lambda: simulation.now), \
             mock.patch.object(SPEED.time, "sleep", side_effect=simulation.sleep):
            result = SPEED.wait_stock_queue(stock, 1, 99)
            self.assertTrue(result["packet_id_depleted"])
            self.assertIn("RF completion unconfirmed", result["meaning"])
            stock.iface.queue = None
            with self.assertRaisesRegex(SPEED.delivery.Failure, "no observable packet queue"):
                SPEED.wait_stock_queue(stock, 1, 99)

    def test_stale_empty_sdk_queue_cannot_prove_an_unseen_packet_id(self):
        simulation = Simulation()
        stock = simulation.classes[1]("not-a-port")
        with mock.patch.object(SPEED.time, "monotonic", side_effect=lambda: simulation.now), \
             mock.patch.object(SPEED.time, "sleep", side_effect=simulation.sleep):
            with self.assertRaisesRegex(SPEED.delivery.Failure, "TX completion uncertain"):
                SPEED.wait_stock_queue(stock, 1, 99)

    def test_unique_tokens_differ_between_sizes_directions_and_packets(self):
        tokens = {SPEED.token_for("12345678", sf, direction, index, size)
                  for sf in SPEED.SFS for size in SPEED.SIZES for direction in ("companion", "stock") for index in range(5)}
        self.assertEqual(len(tokens), 60)
        self.assertTrue(all(len(token) in SPEED.SIZES for token in tokens))

    def test_nominal_bitrate_is_not_the_measured_goodput(self):
        self.assertEqual([SPEED.nominal_kbps(sf) for sf in SPEED.SFS], [21.875, 7.03125, 3.90625])
        measured = SPEED.summary([{"received": True, "wire_bytes": 180, "application_bytes": 154, "truncated": True}], 2)
        self.assertEqual(measured["measured_application_goodput_bps"], 616)
        self.assertEqual(measured["application_bytes_delivered"], 154)


if __name__ == "__main__":
    unittest.main()
