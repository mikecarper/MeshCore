"""Pure tests of the Meshtext RF runner, never its physical radio interfaces.

The simulated peer tests validate orchestration and truthful reporting only;
they do not replace real RF, production codec, or stock-firmware tests.
No PlatformIO, pyserial, stock SDK, or connected hardware is required.

Real hardware uses a stock SDK supporting SerialInterface(timeout=20), tested
with 2.7.11. An older 2.7.3 environment rejected that constructor argument.
"""

import base64
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "meshtext_delivery_hil_under_test", ROOT / "tools/hil/meshtext_delivery.py")
HIL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HIL)


class FakeSerial:
    def __init__(self, chunks=(), short_write=False):
        self.chunks = list(chunks)
        self.short_write = short_write
        self.events = []

    @property
    def rts(self):
        return False

    @rts.setter
    def rts(self, value):
        self.events.append(("rts", value))

    @property
    def dtr(self):
        return True

    @dtr.setter
    def dtr(self, value):
        self.events.append(("dtr", value))

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def write(self, data):
        self.events.append(("write", data))
        return len(data) - int(self.short_write)

    def flush(self):
        self.events.append(("flush",))

    def read(self, count):
        self.events.append(("read", count))
        return self.chunks.pop(0) if self.chunks else b""


class SimulatedRadios:
    """Small state model for the runner's policies, not a codec/RF oracle."""

    def __init__(self, *, missing_nodeinfo=False, duplicate_text=False,
                 heard_echo=True, primary_frequency=909.5,
                 stale_renamed_nodeinfo=False, drop_renamed_texts=0,
                 missing_renamed_nodeinfo=False, duplicate_renamed_nodeinfo=False):
        self.missing_nodeinfo = missing_nodeinfo
        self.duplicate_text = duplicate_text
        self.heard_echo = heard_echo
        self.stale_renamed_nodeinfo = stale_renamed_nodeinfo
        self.drop_renamed_texts = drop_renamed_texts
        self.missing_renamed_nodeinfo = missing_renamed_nodeinfo
        self.duplicate_renamed_nodeinfo = duplicate_renamed_nodeinfo
        self.primary_frequency = primary_frequency
        self.events = []
        self.closed = []
        self.commands = []
        self.role = "CLIENT_MUTE"
        self.name = "Original HIL"
        self.radio2_writes = 0
        self.busy_reads = 0
        self.sequence = 0
        self.clock = 100.0
        owner = self

        class Companion:
            def __init__(self, port):
                owner.companion = self
                self.max_channels = 3
                self.retry = self.heard = self.exhausted = self.tx = self.rx = 0
                self.count, self.hops = 2, 7
                self.announced = False

            def start(self):
                return {"firmware": "SIMULATED-NOT-RF", "primary": {"node_name": owner.name}}

            def close(self):
                owner.closed.append("companion")

            def channel_name(self, slot):
                return ""

            def mt_node(self):
                return 0x12345678

            def cli(self, command):
                owner.commands.append(command)
                if command == "get radio":
                    return f"> {owner.primary_frequency},500,5,5"
                if command == "get radio2":
                    return "> 909.5,500,5,5,rxtx,32"
                if command == "get mt.status":
                    status = "active"
                    if owner.busy_reads:
                        owner.busy_reads -= 1
                        status = "publication busy"
                    return (f"> Meshtext !12345678; {status}; manual radio2; "
                            f"RX={self.rx} TX={self.tx} fail=0")
                if command == "get mt.retry":
                    return (f"> retry={self.retry} heard={self.heard} exhausted={self.exhausted} "
                            f"pending=0 count={self.count} advert=off")
                if command.startswith("set flood.retry.count "):
                    self.count = int(command.split()[-1])
                if command.startswith("set mt.hops "):
                    self.hops = int(command.split()[-1])
                if command.startswith("set radio2 "):
                    owner.radio2_writes += 1
                    if owner.radio2_writes > 1:
                        owner.busy_reads = 2
                if command.startswith("set name "):
                    name = command[len("set name "):]
                    if name != owner.name:
                        owner.name = name
                        self.announced = False
                if command == "get name":
                    return "> " + owner.name
                return "OK"

            def send_text(self, slot, token):
                if not self.hops:
                    self.tx += 1
                elif owner.role == "CLIENT" and owner.heard_echo:
                    self.tx += 2
                    self.retry += 1
                    self.heard += 1
                else:
                    self.tx += 1 + self.count
                    self.retry += self.count
                    self.exhausted += 1
                renamed = owner.name != "Original HIL"
                drop_text = renamed and owner.drop_renamed_texts > 0
                if drop_text:
                    owner.drop_renamed_texts -= 1
                else:
                    owner.append_packet(self.mt_node(), token.encode(), "TEXT_MESSAGE_APP")
                if owner.duplicate_text:
                    owner.events.append(owner.events[-1])
                if not self.announced:
                    self.announced = True
                    self.tx += 1
                    if not owner.missing_nodeinfo and not (renamed and owner.missing_renamed_nodeinfo):
                        wire_name = ("Original HIL" if owner.stale_renamed_nodeinfo
                                     and owner.name != "Original HIL" else owner.name)
                        owner.append_packet(self.mt_node(), b"simulated-user", "NODEINFO_APP", wire_name)
                        if renamed and owner.duplicate_renamed_nodeinfo:
                            self.tx += 1
                            owner.append_packet(self.mt_node(), b"extra-user", "NODEINFO_APP", wire_name)

            def wait_text(self, slot, sender, token, timeout):
                self.rx += 1
                return {"received": True}

        class Stock:
            node = 0x87654321

            def __init__(self, port):
                owner.stock = self
                self.iface = SimpleNamespace(isConnected=True,
                                             stream=SimpleNamespace(is_open=True))

            def close(self):
                owner.closed.append("stock")

            def configure_lab(self, key, role):
                owner.role = role
                return {"role": role, "rf": dict(HIL.RF)}

            def packets_since(self, timestamp, sender):
                return [packet for when, packet in owner.events
                        if when >= timestamp and packet["from"] == sender]

            def wait_packet(self, sender, predicate, since, timeout):
                return next((packet for packet in self.packets_since(since, sender)
                             if predicate(packet)), None)

            def send_text(self, token, hops):
                return SimpleNamespace(id=123)

        self.classes = Companion, Stock

    def now(self):
        self.clock += 0.001
        return self.clock

    def append_packet(self, sender, payload, port, user_name=None):
        self.sequence += 1
        self.events.append((self.now(), {"id": self.sequence, "from": sender,
                            "simulated_long_name": user_name,
                            "decoded": {"payload": payload, "portnum": port}}))


class MeshtextDeliveryGuardrailTest(unittest.TestCase):
    def invoke_main(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["meshtext_delivery.py"] + arguments), \
                redirect_stdout(output), redirect_stderr(errors):
            try:
                code = HIL.main()
            except SystemExit as error:
                code = error.code
        return code, output.getvalue(), errors.getvalue()

    def test_plan_does_not_import_hardware_dependencies_or_inspect_ports(self):
        with mock.patch.object(HIL, "dependencies", side_effect=AssertionError("dependency import")), \
                mock.patch.object(HIL, "require_idle_port", side_effect=AssertionError("port probe")):
            code, output, _ = self.invoke_main(["--plan"])
        self.assertEqual(code, 0)
        plan = json.loads(output)
        self.assertEqual(plan["rf"]["frequency_mhz"], 909.5)
        self.assertEqual(plan["rf"]["bandwidth_khz"], 500)
        self.assertEqual((plan["rf"]["sf"], plan["rf"]["cr"]), (5, 5))
        self.assertEqual(plan["echo_retry_budget"], 15)

    def test_consent_is_required_before_run(self):
        with mock.patch.object(HIL, "run", side_effect=AssertionError("radio run")):
            code, _, error = self.invoke_main([])
        self.assertEqual(code, 2)
        self.assertIn("--allow-radio-changes is required", error)

    def test_invalid_timing_and_selection_never_run_hardware(self):
        for arguments in (["--timeout", "0"], ["--timeout", "30.01"],
                          ["--quiet-seconds", "0"], ["--quiet-seconds", "31"],
                          ["--echo-attempts", "0"], ["--echo-attempts", "6"],
                          ["--case", "not-a-case"], ["--channel-slot", "256"]):
            with self.subTest(arguments=arguments), \
                    mock.patch.object(HIL, "run", side_effect=AssertionError("radio run")):
                code, _, _ = self.invoke_main(["--allow-radio-changes"] + arguments)
                self.assertEqual(code, 2)

    def test_same_port_and_existing_report_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="meshtext-hil-guard-") as directory:
            report = Path(directory) / "report.json"
            with mock.patch.object(HIL, "run", side_effect=AssertionError("radio run")):
                common = ["--allow-radio-changes", "--companion-port", "SIMULATED-A",
                          "--stock-port", "SIMULATED-A", "--report", str(report)]
                code, _, error = self.invoke_main(common)
                self.assertEqual(code, 2)
                self.assertIn("ports must be distinct", error)
                report.write_text("preserve-me", encoding="utf-8")
                common[4] = "SIMULATED-B"
                code, _, error = self.invoke_main(common)
                self.assertEqual(code, 2)
                self.assertIn("fresh report", error)
                self.assertEqual(report.read_text(encoding="utf-8"), "preserve-me")

    def test_secret_values_never_enter_cli_labels_or_rejection_reasons(self):
        for size in (16, 32):
            key = bytes(range(size)).hex()
            command = "set mt.channel 1 mt-labtest " + key
            self.assertEqual(HIL.cli_command_label(command), "set mt.channel")
            reply = HIL.safe_cli_reply(command, "Error: echoed " + command)
            self.assertNotIn(key, reply)
            self.assertIn("<redacted>", reply)
        self.assertEqual(HIL.cli_command_label("normalradio"), "normalradio")
        self.assertEqual(HIL.cli_command_label("set tempradio2 off"), "set tempradio2")
        self.assertEqual(HIL.safe_cli_reply("normalradio", "Error: invalid preamble"),
                         "Error: invalid preamble")

    def test_counter_parsing_rejects_missing_fields(self):
        self.assertEqual(HIL.retry_stats("> retry=2 heard=1 exhausted=0 pending=0 count=15 advert=off")["heard"], 1)
        self.assertEqual(HIL.tx_stats("RX=3 TX=4 fail=0"), {"rx": 3, "tx": 4, "fail": 0})
        for parser, reply in ((HIL.retry_stats, "retry=2 pending=0"),
                              (HIL.tx_stats, "TX=4 fail=0")):
            with self.subTest(reply=reply), self.assertRaises(HIL.Failure):
                parser(reply)

    def test_idle_wait_has_a_real_deadline(self):
        observed = {"delivery": {"pending": 1}}
        with mock.patch.object(HIL, "snapshot", return_value=observed) as snapshot, \
                mock.patch.object(HIL.time, "monotonic", side_effect=(0, 0.1, 0.2, 0.3)), \
                mock.patch.object(HIL.time, "sleep"):
            with self.assertRaisesRegex(HIL.Failure, "did not become idle"):
                HIL.wait_idle(object(), 0.25)
        self.assertEqual(snapshot.call_count, 2)

    def test_publication_poll_is_read_only_and_time_bounded(self):
        companion = SimpleNamespace(cli=mock.Mock(return_value="> publication busy; RX=0 TX=0 fail=0"))
        with mock.patch.object(HIL.time, "monotonic", side_effect=(0, 0.1, 0.2, 0.3)), \
                mock.patch.object(HIL.time, "sleep"), \
                self.assertRaisesRegex(HIL.Failure, "did not become active"):
            HIL.wait_active(companion, 0.25)
        self.assertEqual(companion.cli.call_args_list, [mock.call("get mt.status")] * 2)


class MeshtextDeliveryUsbModeTest(unittest.TestCase):
    def test_explicit_terminal_stop_uses_safe_line_order_and_drains_plaintext(self):
        port = FakeSerial((b"> +++MESHCORE-TERM-STOP\r\n", b"Binary mode\r\n"))
        with mock.patch.object(HIL.time, "sleep") as sleep:
            result = HIL.enter_binary_mode(port)
        self.assertEqual(port.events[:4], [("rts", False), ("dtr", True),
                         ("write", b"+++MESHCORE-TERM-STOP\r"), ("flush",)])
        sleep.assert_called_once_with(0.3)
        self.assertEqual(result["discarded_terminal_bytes"], 38)
        self.assertNotIn("Binary mode", str(result))

    def test_already_binary_requires_no_ascii_acknowledgement(self):
        with mock.patch.object(HIL.time, "sleep"):
            self.assertEqual(HIL.enter_binary_mode(FakeSerial())["discarded_terminal_bytes"], 0)

    def test_short_write_and_logging_owner_fail_explicitly(self):
        for port, reason in ((FakeSerial(short_write=True), "incomplete"),
                (FakeSerial((b"ERROR: set usb.logging off before Binary mode\r\n> ",)), "USB logging owns")):
            with self.subTest(reason=reason), mock.patch.object(HIL.time, "sleep"), \
                    self.assertRaisesRegex(HIL.Failure, reason):
                HIL.enter_binary_mode(port)

    def test_terminal_drain_is_byte_bounded(self):
        port = FakeSerial([b"x" * 4096] * 17)
        with mock.patch.object(HIL.time, "sleep"), \
                mock.patch.object(HIL.time, "monotonic", return_value=0), \
                self.assertRaisesRegex(HIL.Failure, "bounded negotiation drain"):
            HIL.enter_binary_mode(port)

    def test_terminal_drain_is_time_bounded_even_with_continuous_output(self):
        port = FakeSerial([b"x"] * 20)
        with mock.patch.object(HIL.time, "sleep"), \
                mock.patch.object(HIL.time, "monotonic", side_effect=(0, 0.1, 0.5, 1.1)):
            result = HIL.enter_binary_mode(port)
        self.assertEqual(result["discarded_terminal_bytes"], 2)
        self.assertEqual(len(port.chunks), 18)


class MeshtextDeliveryStockSettlingTest(unittest.TestCase):
    class Peer:
        def __init__(self, outcomes):
            self.outcomes = iter(outcomes)
            self.iface = None
            self.attempts = 0
            self.closed = 0

        def close(self):
            self.closed += 1

        def connect(self):
            self.attempts += 1
            outcome = next(self.outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            self.iface = SimpleNamespace(localNode=outcome, close=self.close)

    def test_stale_readback_reconnects_then_accepts_committed_settings(self):
        peer = self.Peer(["stale", "settled"])
        with mock.patch.object(HIL.time, "sleep") as sleep:
            result = HIL.reconnect_stock_readback(
                peer, lambda node: ["channel.private_key_match"] if node == "stale" else [])
        self.assertEqual(result, {"attempts": 2, "retry_history": [
            {"attempt": 1, "mismatched_fields": ["channel.private_key_match"]}]})
        self.assertEqual(peer.closed, 1)
        self.assertIsNotNone(peer.iface)
        sleep.assert_called_once_with(2)

    def test_transient_connect_errors_are_retried_without_retaining_secret_text(self):
        key = bytes(range(16)).hex()
        peer = self.Peer([OSError("unrelated diagnostic " + key), "stale", "settled"])
        with mock.patch.object(HIL.time, "sleep") as sleep:
            result = HIL.reconnect_stock_readback(
                peer, lambda node: ["lora.override_frequency"] if node == "stale" else [])
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["retry_history"][0]["mismatched_fields"],
                         ["connect/readback error: OSError"])
        self.assertNotIn(key, json.dumps(result))
        self.assertEqual(sleep.call_args_list, [mock.call(2), mock.call(2)])

    def test_bad_settings_stop_after_three_attempts_with_only_field_names(self):
        peer = self.Peer(["wrong"] * 3)
        with mock.patch.object(HIL.time, "sleep") as sleep, \
                self.assertRaisesRegex(HIL.Failure, "three bounded reconnects.*lora.override_frequency"):
            HIL.reconnect_stock_readback(peer, lambda node: ["lora.override_frequency"])
        self.assertEqual(peer.attempts, 3)
        self.assertEqual(peer.closed, 3)
        self.assertIsNone(peer.iface)
        self.assertEqual(sleep.call_count, 2)

    def test_old_sdk_constructor_error_is_not_reported_as_rf_success(self):
        peer = self.Peer([TypeError("SerialInterface got unexpected keyword argument timeout")] * 3)
        with mock.patch.object(HIL.time, "sleep"), \
                self.assertRaisesRegex(HIL.Failure, "connect/readback error: TypeError"):
            HIL.reconnect_stock_readback(peer, lambda node: [])
        self.assertEqual(peer.attempts, 3)

    def test_quiet_requires_live_sdk_and_serial_indicators(self):
        event = mock.Mock()
        event.is_set.return_value = True
        peer = SimpleNamespace(iface=SimpleNamespace(isConnected=event,
                                stream=SimpleNamespace(is_open=True)))
        self.assertEqual(HIL.require_stock_connected(peer),
                         {"sdk_connected": True, "serial_open": True})
        event.is_set.return_value = False
        with self.assertRaisesRegex(HIL.Failure, "disconnected"):
            HIL.require_stock_connected(peer)
        event.is_set.return_value = True
        peer.iface.stream.is_open = False
        with self.assertRaisesRegex(HIL.Failure, "disconnected"):
            HIL.require_stock_connected(peer)

    def test_missing_health_indicator_cannot_pass_as_quiet(self):
        with self.assertRaisesRegex(HIL.Failure, "no supported connection-health"):
            HIL.require_stock_connected(SimpleNamespace(iface=object()))
        with self.assertRaisesRegex(HIL.Failure, "disconnected"):
            HIL.require_stock_connected(SimpleNamespace(iface=None))

    def test_real_configuration_method_waits_for_deferred_reboot_before_reconnect(self):
        # Execute the runner's real transaction/reconnect code with a tiny SDK
        # adapter. Any accidentally opened physical port raises immediately.
        events = []
        descriptor = SimpleNamespace(fields_by_name={})
        settings = SimpleNamespace(name="old", psk=b"old", uplink_enabled=True,
                                   downlink_enabled=True)
        node = SimpleNamespace(nodeNum=42,
            channels=[SimpleNamespace(role=1, settings=settings)],
            localConfig=SimpleNamespace(device=SimpleNamespace(DESCRIPTOR=descriptor),
                position=SimpleNamespace(DESCRIPTOR=descriptor), lora=SimpleNamespace()),
            moduleConfig=SimpleNamespace(telemetry=None),
            ensureSessionKey=mock.Mock(), beginSettingsTransaction=mock.Mock(),
            writeConfig=mock.Mock(), writeChannel=mock.Mock(),
            commitSettingsTransaction=lambda: events.append(("commit",)))

        def interface():
            return SimpleNamespace(localNode=node,
                _getOrCreateByNum=lambda number: {"adminSessionPassKey": b"session-ready"},
                close=lambda: events.append(("close",)))

        class StockAdapter:
            def __init__(self, port):
                self.iface = interface()

            def connect(self):
                events.append(("connect",))
                self.iface = interface()

            def identity(self):
                return {"node": "!0000002a"}

        def module(name, **attributes):
            result = ModuleType(name)
            result.__dict__.update(attributes)
            return result

        device_config = SimpleNamespace(Role=SimpleNamespace(Value=lambda name: {"CLIENT_MUTE": 1}[name]),
            RebroadcastMode=SimpleNamespace(Value=lambda name: {"ALL": 0}[name]))
        config = SimpleNamespace(Config=SimpleNamespace(DeviceConfig=device_config,
                                                       LoRaConfig=SimpleNamespace(US=1)))
        channel = SimpleNamespace(Channel=SimpleNamespace(PRIMARY=1, DISABLED=0))
        hardware_forbidden = mock.Mock(side_effect=AssertionError("physical serial access"))
        modules = {
            "serial": module("serial", Serial=hardware_forbidden),
            "meshtext_auto_radio2": module("meshtext_auto_radio2", Companion=object, StockPeer=StockAdapter),
            "esp32_companion_serial_stress": module("esp32_companion_serial_stress",
                DeviceFrameReader=mock.Mock(), TransportCounters=mock.Mock()),
            "serial_session": module("serial_session", open_configured_session=hardware_forbidden),
            "meshtastic": module("meshtastic"),
            "meshtastic.protobuf": module("meshtastic.protobuf", channel_pb2=channel, config_pb2=config),
            "meshtastic.util": module("meshtastic.util", to_node_num=lambda value: value),
        }
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(HIL.time, "sleep", side_effect=lambda seconds: events.append(("sleep", seconds))):
            _, Stock = HIL.dependencies()
            result = Stock("SIMULATED-ONLY").configure_lab(bytes(range(16)), "CLIENT_MUTE")
        hardware_forbidden.assert_not_called()
        committed = events.index(("commit",))
        self.assertEqual(events[committed + 1:committed + 4],
                         [("sleep", 20), ("close",), ("connect",)])
        self.assertTrue(all(event[1] in (0.75, 20) for event in events if event[0] == "sleep"))
        self.assertEqual(result["readback"]["attempts"], 1)


class MeshtextDeliveryMockFlowTest(unittest.TestCase):
    def run_simulation(self, radios, case="all", key_bits=128):
        key = bytes(range(key_bits // 8))
        with tempfile.TemporaryDirectory(prefix="meshtext-hil-simulated-") as directory, ExitStack() as stack:
            report = Path(directory) / "report.json"
            args = SimpleNamespace(case=case, key_bits=key_bits, report=report,
                companion_port="SIMULATED-DUT", stock_port="SIMULATED-STOCK", channel_slot=1,
                quiet_seconds=0.01, timeout=1, echo_attempts=3)
            stack.enter_context(mock.patch.object(HIL, "dependencies", return_value=radios.classes))
            stack.enter_context(mock.patch.object(HIL, "require_idle_port"))
            stack.enter_context(mock.patch.object(HIL.importlib.metadata, "version", return_value="SIMULATED"))
            stack.enter_context(mock.patch.object(HIL.secrets, "token_bytes", return_value=key))
            stack.enter_context(mock.patch.object(HIL.secrets, "token_hex", return_value="0123abcd"))
            stack.enter_context(mock.patch.object(HIL.time, "sleep"))
            stack.enter_context(mock.patch.object(HIL.time, "monotonic", side_effect=radios.now))
            stack.enter_context(mock.patch.object(HIL, "inspect_nodeinfo",
                side_effect=lambda packet, node: {"id": "!12345678",
                    "long_name": packet["simulated_long_name"],
                    "payload_type": "SIMULATED User; no protobuf/RF claim"}))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = HIL.run(args)
            saved = report.read_text(encoding="utf-8")
            self.assertEqual(result, json.loads(saved))
            self.assertNotIn(key.hex(), saved)
            self.assertNotIn(base64.b64encode(key).decode(), saved)
            return result

    def test_all_cases_success_and_safe_setup_commands(self):
        for bits in (128, 256):
            with self.subTest(key_bits=bits):
                radios = SimulatedRadios()
                result = self.run_simulation(radios, key_bits=bits)
                self.assertEqual(result["result"], "PASS", result.get("failure"))
                self.assertEqual(len(result["cases"]), 9)
                self.assertTrue(all(case["result"] == "PASS" for case in result["cases"]))
                self.assertIn("normalradio", radios.commands)
                self.assertNotIn("set tempradio off", radios.commands)
                self.assertIn("set flood.retry.count 15", radios.commands)
                self.assertEqual(sorted(radios.closed), ["companion", "stock"])
                self.assertFalse(result["settings_restored"])
                self.assertEqual(radios.name, "Original HIL")

    def test_quiet_only_never_sends_a_chat(self):
        radios = SimulatedRadios()
        result = self.run_simulation(radios, "quiet")
        self.assertEqual(result["result"], "PASS")
        self.assertEqual([case["name"] for case in result["cases"]], ["quiet"])
        self.assertEqual(radios.events, [])
        self.assertEqual(radios.companion.tx, 0)

    def test_new_cases_work_as_a_subset_with_read_only_reactivation_and_name_restore(self):
        radios = SimulatedRadios()
        result = self.run_simulation(radios, "same-tuple,rename")
        self.assertEqual(result["result"], "PASS", result.get("failure"))
        self.assertEqual([case["name"] for case in result["cases"]], ["first-use",
            "same-tuple-publication-recovery", "rename-rearms-first-chat-nodeinfo"])
        same_tuple, rename = result["cases"][-2:]
        self.assertGreater(same_tuple["publication"]["polls"], 1)
        self.assertEqual(same_tuple["traffic_delta"]["tx"], 0)
        self.assertEqual(rename["traffic_delta"]["tx"], 2)
        self.assertEqual(rename["nodeinfo"]["long_name"], rename["test_name"])
        self.assertTrue(rename["original_name_restored"])
        self.assertEqual(rename["restore_traffic_delta"]["tx"], 0)
        self.assertEqual(radios.name, "Original HIL")
        self.assertEqual(radios.companion.hops, 7)

    def test_rename_restores_original_name_even_when_wire_metadata_is_wrong(self):
        radios = SimulatedRadios(stale_renamed_nodeinfo=True)
        result = self.run_simulation(radios, "rename")
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("old long name", result["failure"]["message"])
        rename = result["cases"][-1]
        self.assertEqual(rename["result"], "FAIL")
        self.assertTrue(rename["original_name_restored"])
        self.assertEqual(radios.name, "Original HIL")
        self.assertEqual(rename["restore_traffic_delta"]["tx"], 0)

    def test_rename_records_first_chat_loss_and_accepts_its_one_nodeinfo(self):
        radios = SimulatedRadios(drop_renamed_texts=1)
        result = self.run_simulation(radios, "rename")
        self.assertEqual(result["result"], "PASS", result.get("failure"))
        rename = result["cases"][-1]
        self.assertEqual(rename["attempted_texts"], 2)
        self.assertEqual(rename["received_texts"], 1)
        self.assertEqual([probe["received"] for probe in rename["probes"]], [False, True])
        self.assertIsNone(rename["probes"][0]["packet_id"])
        self.assertIsNotNone(rename["probes"][1]["packet_id"])
        self.assertEqual(rename["decoded_nodeinfo_events"], 1)
        self.assertEqual(rename["packets"][0]["port"], "NODEINFO_APP")
        self.assertEqual(rename["traffic_delta"]["tx"], 3)
        self.assertTrue(rename["original_name_restored"])

    def test_rename_all_three_chats_lost_is_still_a_failed_rf_test(self):
        radios = SimulatedRadios(drop_renamed_texts=3)
        result = self.run_simulation(radios, "rename")
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("any of three", result["failure"]["message"])
        rename = result["cases"][-1]
        self.assertEqual(rename["attempted_texts"], 3)
        self.assertEqual(rename["received_texts"], 0)
        self.assertEqual(rename["decoded_nodeinfo_events"], 1)
        self.assertEqual(rename["traffic_delta"]["tx"], 4)
        self.assertTrue(rename["original_name_restored"])
        self.assertTrue(all(not probe["received"] for probe in rename["probes"]))

    def test_rename_missing_nodeinfo_is_not_masked_by_extra_successful_chats(self):
        radios = SimulatedRadios(missing_renamed_nodeinfo=True)
        result = self.run_simulation(radios, "rename")
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("exactly one observed", result["failure"]["message"])
        rename = result["cases"][-1]
        self.assertEqual(rename["attempted_texts"], 3)
        self.assertEqual(rename["received_texts"], 3)
        self.assertEqual(rename["decoded_nodeinfo_events"], 0)
        self.assertTrue(rename["original_name_restored"])

    def test_rename_extra_nodeinfo_is_never_accepted_as_one_time_announcement(self):
        radios = SimulatedRadios(duplicate_renamed_nodeinfo=True)
        result = self.run_simulation(radios, "rename")
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("exactly one observed", result["failure"]["message"])
        rename = result["cases"][-1]
        self.assertEqual(rename["decoded_nodeinfo_events"], 2)
        self.assertTrue(rename["original_name_restored"])

    def test_missing_metadata_fails_and_saves_partial_report(self):
        radios = SimulatedRadios(missing_nodeinfo=True)
        result = self.run_simulation(radios, "first-use")
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("automatic NodeInfo", result["failure"]["message"])
        self.assertEqual(result["cases"][-1]["result"], "FAIL")
        self.assertIn("finished_utc", result)
        self.assertEqual(sorted(radios.closed), ["companion", "stock"])

    def test_duplicate_user_delivery_fails_exhaustion_case(self):
        result = self.run_simulation(SimulatedRadios(duplicate_text=True), "exhaustion")
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("duplicate user text", result["failure"]["message"])
        self.assertEqual(result["cases"][-1]["decoded_text_events"], 2)

    def test_received_chat_without_heard_echo_never_passes_cancellation(self):
        result = self.run_simulation(SimulatedRadios(heard_echo=False), "echo")
        self.assertEqual(result["result"], "FAIL")
        echo = result["cases"][-1]
        self.assertEqual(echo["name"], "heard-repeat-cancellation")
        self.assertEqual(len(echo["attempts"]), 3)
        self.assertTrue(all(attempt["received_by_stock"] for attempt in echo["attempts"]))
        self.assertTrue(all(attempt["delivery_delta"]["heard"] == 0 for attempt in echo["attempts"]))

    def test_wrong_primary_is_rejected_before_any_setting_write(self):
        radios = SimulatedRadios(primary_frequency=910.525)
        result = self.run_simulation(radios)
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("preconfigure primary", result["failure"]["message"])
        self.assertFalse(any(command.startswith(("set ", "delete ")) for command in radios.commands))
        self.assertEqual(radios.closed, ["companion"])

    def test_failure_report_redacts_raw_key_bytes_hex_and_base64(self):
        key = bytes(range(16))
        message = f"bad setup {key.hex()} {key!r} {base64.b64encode(key).decode()}"
        with tempfile.TemporaryDirectory(prefix="meshtext-hil-secret-") as directory, \
                mock.patch.object(HIL.secrets, "token_bytes", return_value=key), \
                mock.patch.object(HIL.secrets, "token_hex", return_value="0123abcd"), \
                mock.patch.object(HIL, "dependencies", side_effect=RuntimeError(message)), \
                redirect_stdout(io.StringIO()):
            args = SimpleNamespace(case="all", key_bits=128, report=Path(directory) / "report.json")
            result = HIL.run(args)
            saved = args.report.read_text(encoding="utf-8")
        self.assertEqual(result["result"], "FAIL")
        self.assertNotIn(key.hex(), saved)
        self.assertNotIn(repr(key), result["failure"]["message"])
        self.assertNotIn(base64.b64encode(key).decode(), saved)
        self.assertIn("<redacted>", saved)


if __name__ == "__main__":
    unittest.main()
