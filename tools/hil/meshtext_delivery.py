#!/usr/bin/env python3
"""Exercise Meshtext first-chat metadata and heard-repeat delivery on two radios.

CHANGES BOTH RADIOS' SETTINGS AND TRANSMITS LoRa. Requires explicit consent.
The fixed private lab channel uses 909.5 MHz / 500 kHz / SF5 / CR5, TX 2 dBm.
Keys remain in memory and are never included in the report. This does not flash
firmware, back up settings, or restore them. Run --plan without dependencies or
ports. A stock peer's decoded events are not an independent raw RF capture.

Example:
  python meshtext_delivery.py --allow-radio-changes --companion-port /dev/ttyACM9 \
      --stock-port /dev/ttyUSB1 --report /tmp/meshtext-delivery-new.json
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time


RF = {"frequency_mhz": 909.5, "bandwidth_khz": 500, "sf": 5, "cr": 5,
      "tx_power_dbm": 2, "stock_preamble_symbols": 16}
CASES = ("quiet", "first-use", "exhaustion", "once", "echo", "inbound", "hops-zero",
         "same-tuple", "rename")


class Failure(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


def retry_stats(reply):
    values = dict(re.findall(r"\b(retry|heard|exhausted|pending|count)=(\d+)", reply))
    check(len(values) == 5, "get mt.retry did not provide all five counters")
    return {name: int(value) for name, value in values.items()}


def tx_stats(reply):
    match = re.search(r"\bRX=(\d+) TX=(\d+) fail=(\d+)", reply)
    check(match is not None, "get mt.status did not provide RF completion counters")
    return dict(zip(("rx", "tx", "fail"), map(int, match.groups())))


def delta(after, before):
    return {name: after[name] - before[name] for name in after}


def port_number(packet):
    value = packet.get("decoded", {}).get("portnum")
    # The Python SDK normally emits enum names, but accept numeric SDK output.
    return {1: "TEXT_MESSAGE_APP", 3: "POSITION_APP", 4: "NODEINFO_APP"}.get(value, value)


def packet_summary(packet):
    return {"id": packet.get("id"), "port": port_number(packet),
            "hop_limit": packet.get("hopLimit"), "hop_start": packet.get("hopStart")}


def cli_command_label(command):
    words = command.split()
    # Identify the setting, never its value (especially mt.channel keys).
    return " ".join(words[:2] if words and words[0] in ("get", "set", "send", "delete") else words[:1])


def safe_cli_reply(command, reply):
    # Ordinary firmware replies do not echo the key; still redact it if a
    # diagnostic implementation does so. Do not save the full command itself.
    words = command.split()
    if len(words) >= 5 and words[:2] == ["set", "mt.channel"]:
        reply = reply.replace(words[-1], "<redacted>")
    return re.sub(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,64}(?![0-9a-fA-F])", "<redacted>", reply)[:500]


def require_idle_port(path):
    # Do not reset/steal a serial session held by a flasher or client. BusyBox
    # fuser lacks '--', so the inspected absolute path is a separate argument.
    inspector = shutil.which("fuser") if sys.platform.startswith("linux") else None
    if inspector:
        result = subprocess.run([inspector, os.path.realpath(path)], capture_output=True, timeout=3)
        check(result.returncode == 1, "selected serial port is busy or ownership inspection failed")


def enter_binary_mode(port):
    """Explicitly release a Full Companion's default ASCII USB terminal.

    The startup probe is intentionally not assumed to be a permanent Binary
    mode. Assert host presence only after opening with RTS/DTR safely released,
    then drain the terminal's acknowledgement before creating framed requests.
    Never retain/log the discarded plaintext (it may include unrelated logs).
    """
    port.rts = False
    port.dtr = True
    token = b"+++MESHCORE-TERM-STOP\r"
    check(port.write(token) == len(token), "incomplete USB Binary-mode negotiation write")
    port.flush()
    time.sleep(0.3)
    deadline = time.monotonic() + 1
    discarded = 0
    tail = b""
    while time.monotonic() < deadline:
        chunk = port.read(min(port.in_waiting or 1, 4096))
        if not chunk:
            break
        discarded += len(chunk)
        check(discarded <= 65536, "USB terminal output exceeded bounded negotiation drain")
        tail = (tail + chunk)[-512:]
    check(b"set usb.logging off before Binary mode" not in tail,
          "USB logging owns this port; set usb.logging off in the terminal before this test")
    return {"explicit_stop_token": True, "discarded_terminal_bytes": discarded, "dtr": True}


def reconnect_stock_readback(peer, validate):
    """Allow a committed stock configuration to settle across its reboot.

    Each SDK connect has the existing helper's 20-second timeout. Only static
    field names/error types enter this history, never the observed secret key.
    """
    history = []
    for attempt in range(1, 4):
        try:
            peer.connect()
            mismatches = validate(peer.iface.localNode)
            if not mismatches:
                return {"attempts": attempt, "retry_history": history}
        except Exception as error:
            mismatches = ["connect/readback error: " + type(error).__name__]
        history.append({"attempt": attempt, "mismatched_fields": mismatches})
        if peer.iface is not None:
            try:
                peer.iface.close()
            finally:
                peer.iface = None
        if attempt < 3:
            time.sleep(2)
    raise Failure("stock configuration did not settle after three bounded reconnects; "
                  "last mismatched fields: " + ", ".join(history[-1]["mismatched_fields"]))


def require_stock_connected(peer):
    """Silence is evidence only while the stock receive path is connected."""
    iface = getattr(peer, "iface", None)
    check(iface is not None, "stock SDK disconnected; quiet/RX observations are invalid")
    state = {}
    connected = getattr(iface, "isConnected", None)
    if hasattr(connected, "is_set"):
        state["sdk_connected"] = bool(connected.is_set())
    elif isinstance(connected, bool):
        state["sdk_connected"] = connected
    stream = getattr(iface, "stream", None)
    if stream is not None and hasattr(stream, "is_open"):
        state["serial_open"] = bool(stream.is_open)
    check(state, "stock SDK exposes no supported connection-health indicator")
    check(all(state.values()), "stock SDK disconnected; quiet/RX observations are invalid")
    return state


def dependencies():
    """Delay hardware dependencies so --plan also works on a clean computer."""
    import serial
    from meshtext_auto_radio2 import Companion, StockPeer
    from esp32_companion_serial_stress import DeviceFrameReader, TransportCounters
    from serial_session import open_configured_session

    class SafeCompanion(Companion):
        def __init__(self, port):
            self.port = serial.Serial(port=None, baudrate=115200, timeout=0.05,
                                      write_timeout=2, exclusive=True)
            self.port.port, self.port.dtr, self.port.rts = port, False, False
            open_configured_session(self.port)
            self.reader = DeviceFrameReader(TransportCounters())
            self.max_channels = 0

        def start(self):
            negotiation = enter_binary_mode(self.port)
            # The reader has not received any framed traffic yet; the ASCII
            # acknowledgement must not become a stale framed response.
            self.reader = DeviceFrameReader(TransportCounters())
            return {**super().start(), "usb_binary_negotiation": negotiation}

        def cli(self, command):
            label = cli_command_label(command)
            try:
                frame = self.request(b"\x42" + command.encode("utf-8"), (0x1d,))
                reply = frame[1:].decode("utf-8", "strict").strip()
            except Exception as error:
                raise Failure("Companion CLI " + label + " failed: "
                              + safe_cli_reply(command, str(error))) from error
            check(not re.match(r"(?:Error|ERR|Err)\b", reply),
                  "Companion CLI " + label + " rejected: " + safe_cli_reply(command, reply))
            return reply

    class LabStock(StockPeer):
        def configure_lab(self, key, role_name, hops=7):
            from meshtastic.protobuf import channel_pb2, config_pb2
            from meshtastic.util import to_node_num

            node = self.iface.localNode
            node.ensureSessionKey()
            deadline = time.monotonic() + 10
            while not self.iface._getOrCreateByNum(to_node_num(node.nodeNum)).get("adminSessionPassKey"):
                check(time.monotonic() < deadline, "stock admin session setup timed out")
                time.sleep(0.05)
            node.beginSettingsTransaction()
            time.sleep(0.75)
            suppressed = []

            def optional(message, field, value):
                if field in message.DESCRIPTOR.fields_by_name:
                    setattr(message, field, value)
                    suppressed.append(field)

            device = node.localConfig.device
            device.role = config_pb2.Config.DeviceConfig.Role.Value(role_name)
            device.rebroadcast_mode = config_pb2.Config.DeviceConfig.RebroadcastMode.Value("ALL")
            optional(device, "node_info_broadcast_secs", 604800)
            node.writeConfig("device")
            time.sleep(0.75)
            position = node.localConfig.position
            optional(position, "position_broadcast_secs", 604800)
            optional(position, "position_broadcast_smart_enabled", False)
            optional(position, "fixed_position", False)
            gps_field = position.DESCRIPTOR.fields_by_name.get("gps_mode")
            if gps_field and "DISABLED" in gps_field.enum_type.values_by_name:
                optional(position, "gps_mode", gps_field.enum_type.values_by_name["DISABLED"].number)
            node.writeConfig("position")
            time.sleep(0.75)
            telemetry = getattr(node.moduleConfig, "telemetry", None)
            if telemetry is not None:
                for field in telemetry.DESCRIPTOR.fields:
                    if field.name.endswith("_enabled"):
                        optional(telemetry, field.name, False)
                    elif field.name.endswith("_interval"):
                        optional(telemetry, field.name, 604800)
                node.writeConfig("telemetry")
                time.sleep(0.75)
            primary = node.channels[0]
            primary.role = channel_pb2.Channel.PRIMARY
            primary.settings.name, primary.settings.psk = "labtest", key
            primary.settings.uplink_enabled = primary.settings.downlink_enabled = False
            lora = node.localConfig.lora
            lora.region = config_pb2.Config.LoRaConfig.US
            lora.use_preset = False
            lora.bandwidth, lora.spread_factor, lora.coding_rate = 500, 5, 5
            lora.channel_num, lora.override_frequency, lora.frequency_offset = 0, 909.5, 0
            lora.hop_limit, lora.tx_power, lora.tx_enabled = hops, 2, True
            lora.config_ok_to_mqtt, lora.ignore_mqtt = False, True
            node.writeConfig("lora")
            time.sleep(0.75)
            node.writeChannel(0)
            time.sleep(0.75)
            # Disable other channels rather than altering their secret keys.
            disabled = []
            for index, channel in enumerate(node.channels[1:], 1):
                if channel.role != channel_pb2.Channel.DISABLED:
                    channel.role = channel_pb2.Channel.DISABLED
                    node.writeChannel(index)
                    disabled.append(index)
                    time.sleep(0.75)
            node.commitSettingsTransaction()
            # A successful commit/readback can precede the actual stock reboot.
            # Reopening too early gives a convincing config followed by a dead
            # receive stream. Allow that deferred reboot to finish first.
            time.sleep(20)
            self.iface.close()
            self.iface = None

            def validate(observed_node):
                observed, channel = observed_node.localConfig.lora, observed_node.channels[0]
                checks = {
                    "channel.role": channel.role == channel_pb2.Channel.PRIMARY,
                    "channel.name": channel.settings.name == "labtest",
                    "channel.private_key_match": channel.settings.psk == key,
                    "channel.mqtt_off": not channel.settings.uplink_enabled and not channel.settings.downlink_enabled,
                    "lora.region": observed.region == config_pb2.Config.LoRaConfig.US,
                    "lora.custom_mode": not observed.use_preset,
                    "lora.bandwidth": observed.bandwidth == 500,
                    "lora.spread_factor": observed.spread_factor == 5,
                    "lora.coding_rate": observed.coding_rate == 5,
                    "lora.override_frequency": abs(observed.override_frequency - 909.5) < 0.001,
                    "lora.frequency_offset": observed.frequency_offset == 0,
                    "lora.channel_num": observed.channel_num == 0,
                    "lora.tx_power": observed.tx_power == 2,
                    "lora.tx_enabled": observed.tx_enabled,
                    "lora.hop_limit": observed.hop_limit == hops,
                    "lora.mqtt_off": not observed.config_ok_to_mqtt and observed.ignore_mqtt,
                    "device.role": observed_node.localConfig.device.role
                        == config_pb2.Config.DeviceConfig.Role.Value(role_name),
                    "device.rebroadcast_mode": observed_node.localConfig.device.rebroadcast_mode
                        == config_pb2.Config.DeviceConfig.RebroadcastMode.Value("ALL"),
                }
                return [field for field, matched in checks.items() if not matched]

            settled = reconnect_stock_readback(self, validate)
            return {**self.identity(), "role": role_name, "rebroadcast": "ALL", "rf": RF,
                    "periodic_suppression_requested": suppressed,
                    "secondary_channel_slots_disabled": disabled, "readback": settled}

    return SafeCompanion, LabStock


class Report:
    def __init__(self, path, data):
        # Reserve the destination before any radio setting is changed.
        self.stream = path.open("x", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self.data = data
        self.save()

    def save(self):
        self.stream.seek(0)
        json.dump(self.data, self.stream, indent=2)
        self.stream.write("\n")
        self.stream.truncate()
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def case(self, name):
        result = {"name": name, "result": "RUNNING"}
        self.data["cases"].append(result)
        self.save()
        print("Testing " + name, flush=True)
        return result

    def finish_case(self, case):
        case["result"] = "PASS"
        self.save()
        print("PASS " + case["name"], flush=True)


def snapshot(companion):
    status, retry = companion.cli("get mt.status"), companion.cli("get mt.retry")
    return {"status": status, "retry_status": retry,
            "traffic": tx_stats(status), "delivery": retry_stats(retry)}


def wait_idle(companion, timeout):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = snapshot(companion)
        if latest["delivery"]["pending"] == 0:
            return latest
        time.sleep(0.1)
    raise Failure("Meshtext delivery queue did not become idle; last=" + str(latest))


def wait_active(companion, timeout):
    deadline = time.monotonic() + timeout
    first = last = ""
    polls = 0
    while time.monotonic() < deadline:
        last = companion.cli("get mt.status")
        if not first:
            first = last
        polls += 1
        if "; active;" in last:
            return {"first_status": first, "active_status": last, "polls": polls}
        time.sleep(0.1)
    raise Failure("Meshtext did not become active after settings publication; last=" + last)


def inspect_nodeinfo(packet, node_id):
    from meshtastic.protobuf import mesh_pb2
    user = mesh_pb2.User()
    user.ParseFromString(packet.get("decoded", {}).get("payload", b""))
    check(user.id == f"!{node_id:08x}", "NodeInfo sender ID mismatch")
    check(user.role == 1 and user.is_unmessagable, "NodeInfo is not a receive-only hidden client")
    check(1 <= len(user.short_name.encode()) <= 4 and len(user.long_name.encode()) <= 24,
          "NodeInfo names exceed supported bounds")
    check(not user.public_key, "unexpected public encryption key in NodeInfo")
    raw = packet.get("raw")
    if raw is not None:
        check(not raw.want_ack and not raw.decoded.want_response, "NodeInfo requests a response")
    return {"id": user.id, "short_name": user.short_name, "long_name": user.long_name,
            "role": user.role, "is_unmessagable": user.is_unmessagable,
            "payload_type": "User (no position fields)", "packet": packet_summary(packet)}


def run(args):
    key = secrets.token_bytes(args.key_bits // 8)
    run_id = secrets.token_hex(4)
    selected = set(CASES if args.case == "all" else args.case.split(","))
    data = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
            "result": "RUNNING", "run_id": run_id, "rf": RF, "key_bits": args.key_bits,
            "cases_requested": sorted(selected), "settings_restored": False,
            "restore_owner": "operator", "cases": [],
            "limitations": ["Stock decoded delivery events are not an independent raw RF capture.",
                "Exact retry ciphertext identity is covered by host tests, not inspected by this runner.",
                "No POSITION_APP means none observed during bounded windows, not a proof of future behavior.",
                "The stock device may still send boot metadata despite long periodic intervals."]}
    report = Report(args.report, data)
    companion = stock = None
    try:
        Companion, Stock = dependencies()
        data["packages"] = {name: importlib.metadata.version(name)
                            for name in ("pyserial", "meshtastic", "pypubsub")}
        require_idle_port(args.companion_port)
        require_idle_port(args.stock_port)
        companion = Companion(args.companion_port)
        data["companion"] = companion.start()
        check(args.channel_slot < companion.max_channels, "Companion channel slot out of range")
        # The primary is intentionally not changed here: CLI 'set radio' is
        # reboot-only. Preconfigure it, and preferably use the lab TX guard too.
        data["primary"] = companion.cli("get radio")
        check(abs(float(data["primary"].lstrip("> ").split(",")[0]) - 909.5) < 0.001,
              "preconfigure primary to 909.5 MHz (or flash a lab profile) before this runner")
        for slot in range(companion.max_channels):
            if slot != args.channel_slot:
                name = companion.channel_name(slot).lstrip("#")
                check(not name.startswith(("mt-", "r2-")),
                      "another Meshtext/radio2 channel conflicts; remove it explicitly first")
        target_name = companion.channel_name(args.channel_slot).lstrip("#")
        if target_name.startswith("mt-"):
            companion.cli(f"delete mt.channel {args.channel_slot}")
        # Clear temporary leases before installing a manual lab-only profile.
        for command in ("normalradio", "set tempradio2 off", "set mt.region us",
                        "set mt.slot 0", "set mt.hops 7", "set tx 2", "set radio2.cross off",
                        "set flood.retry.count 2", "set flood.retry.advert off",
                        "set radio2 909.5,500,5,5,rxtx,32"):
            companion.cli(command)
        stock = Stock(args.stock_port)
        data["stock_silent"] = stock.configure_lab(key, "CLIENT_MUTE")
        stock_role = "CLIENT_MUTE"
        node_id = companion.mt_node()
        check(stock.node != node_id, "stock and Companion sender IDs collide")
        data["companion"]["mt_node"] = f"!{node_id:08x}"
        # Installing a new random key resets first-use identity announcement.
        companion.cli(f"set mt.channel {args.channel_slot} mt-labtest {key.hex()}")
        data["initial"] = snapshot(companion)
        data["radio2"] = companion.cli("get radio2")
        check("; active;" in data["initial"]["status"], "Meshtext profile is not active")
        report.save()

        if "quiet" in selected:
            case = report.case("quiet")
            case["stock_connected_before"] = require_stock_connected(stock)
            before, since = snapshot(companion), time.monotonic()
            time.sleep(args.quiet_seconds)
            case["stock_connected_after"] = require_stock_connected(stock)
            after = snapshot(companion)
            case.update(before=before, after=after, observed_seconds=args.quiet_seconds,
                        packets=[packet_summary(p) for p in stock.packets_since(since, node_id)])
            report.save()
            check(after["traffic"]["tx"] == before["traffic"]["tx"],
                  "Companion transmitted Meshtext before any user chat")
            check(not case["packets"], "stock observed unsolicited DUT traffic")
            report.finish_case(case)
            if selected == {"quiet"}:
                data["final"] = after
                data["result"] = "PASS"
                return data

        # The first send is also needed to prime the once/echo cases. Report it
        # even when only a dependent test was requested.
        case = report.case("first-use")
        token = f"MT-delivery {run_id} first"
        before, since = snapshot(companion), time.monotonic()
        companion.send_text(args.channel_slot, token)
        text = stock.wait_packet(node_id, lambda p: p.get("decoded", {}).get("payload")
                                 == token.encode(), since, args.timeout)
        check(text is not None, "stock did not receive first chat")
        info = stock.wait_packet(node_id, lambda p: port_number(p) == "NODEINFO_APP", since, args.timeout)
        check(info is not None, "stock did not receive automatic NodeInfo after first chat")
        case["nodeinfo"] = inspect_nodeinfo(info, node_id)
        after = wait_idle(companion, args.timeout)
        packets = stock.packets_since(since, node_id)
        texts = [p for p in packets if p.get("decoded", {}).get("payload") == token.encode()]
        infos = [p for p in packets if port_number(p) == "NODEINFO_APP"]
        case.update(before=before, after=after, traffic_delta=delta(after["traffic"], before["traffic"]),
                    delivery_delta=delta(after["delivery"], before["delivery"]),
                    packets=[packet_summary(p) for p in packets], decoded_text_events=len(texts),
                    decoded_nodeinfo_events=len(infos))
        report.save()
        check(not any(port_number(p) == "POSITION_APP" for p in packets),
              "DUT emitted a position packet after first chat")
        check(len(infos) == 1, "automatic NodeInfo was not observed exactly once")
        check(packets.index(text) < packets.index(info), "automatic NodeInfo arrived before user text")
        report.finish_case(case)

        if "exhaustion" in selected:
            case = report.case("silent-peer-exhaustion")
            case.update(evidence_from="first-use", **{name: report.data["cases"][-2][name]
                        for name in ("traffic_delta", "delivery_delta", "decoded_text_events")})
            report.save()
            check(case["delivery_delta"]["retry"] == 2
                  and case["delivery_delta"]["exhausted"] == 1
                  and case["delivery_delta"]["heard"] == 0,
                  "silent peer did not cause exactly two retries and one exhaustion")
            check(case["traffic_delta"]["tx"] == 4 and case["traffic_delta"]["fail"] == 0,
                  "expected completed RF TX: original chat + two retries + one NodeInfo")
            check(case["decoded_text_events"] == 1,
                  "stock delivered duplicate user text for the same retried packet ID")
            report.finish_case(case)

        if "once" in selected:
            case = report.case("second-chat-no-new-nodeinfo")
            token = f"MT-delivery {run_id} second"
            before, since = snapshot(companion), time.monotonic()
            companion.send_text(args.channel_slot, token)
            text = stock.wait_packet(node_id, lambda p: p.get("decoded", {}).get("payload")
                                     == token.encode(), since, args.timeout)
            after = wait_idle(companion, args.timeout)
            packets = stock.packets_since(since, node_id)
            case.update(received=text is not None, before=before, after=after,
                        packets=[packet_summary(p) for p in packets])
            report.save()
            check(text is not None, "stock did not receive second chat")
            check(not any(port_number(p) in ("NODEINFO_APP", "POSITION_APP") for p in packets),
                  "second chat emitted extra NodeInfo or position")
            check(after["traffic"]["tx"] - before["traffic"]["tx"] == 3,
                  "second silent-peer chat did not complete precisely three text TXs")
            report.finish_case(case)

        if "echo" in selected:
            case = report.case("heard-repeat-cancellation")
            case["stock"] = stock.configure_lab(key, "CLIENT")
            stock_role = "CLIENT"
            # At SF5 the DUT's first two attempts can finish before a stock
            # CLIENT's relay backoff (~2 s). Leave a genuine observation window.
            companion.cli("set flood.retry.count 15")
            case["retry_budget"] = 15
            case["attempts"] = []
            report.save()
            passed = False
            for attempt in range(args.echo_attempts):
                token = f"MT-delivery {run_id} echo {attempt}"
                before, since = snapshot(companion), time.monotonic()
                companion.send_text(args.channel_slot, token)
                text = stock.wait_packet(node_id, lambda p: p.get("decoded", {}).get("payload")
                                         == token.encode(), since, args.timeout)
                after = wait_idle(companion, args.timeout)
                delivery = delta(after["delivery"], before["delivery"])
                observation = {"received_by_stock": text is not None, "before": before, "after": after,
                               "delivery_delta": delivery,
                               "traffic_delta": delta(after["traffic"], before["traffic"]),
                               "packets": [packet_summary(p) for p in stock.packets_since(since, node_id)]}
                case["attempts"].append(observation)
                report.save()
                if text is not None and delivery["heard"] >= 1 and delivery["exhausted"] == 0 \
                        and delivery["retry"] < 15:
                    passed = True
                    break
            check(passed, "no progressed stock relay echo cancelled a retry before exhaustion; RF/scanning needs investigation")
            case["proof"] = "DUT heard counter advanced on a matching progressed frame; pending=0 before budget exhaustion"
            report.finish_case(case)

        if "inbound" in selected:
            case = report.case("stock-to-companion")
            token = f"MT-delivery {run_id} inbound"
            sent = stock.send_text(token, 7)
            received = companion.wait_text(args.channel_slot, stock.node, token, args.timeout)
            case.update(packet_id=sent.id, **received)
            report.save()
            check(received["received"], "Companion did not decode the stock private-channel chat")
            report.finish_case(case)

        if "hops-zero" in selected:
            case = report.case("zero-hops-no-useless-retries")
            companion.cli("set mt.hops 0")
            companion.cli("set flood.retry.count 2")
            before, since = snapshot(companion), time.monotonic()
            token = f"MT-delivery {run_id} zero"
            companion.send_text(args.channel_slot, token)
            text = stock.wait_packet(node_id, lambda p: p.get("decoded", {}).get("payload")
                                     == token.encode(), since, args.timeout)
            after = wait_idle(companion, args.timeout)
            time.sleep(1)
            final = snapshot(companion)
            case.update(received=text is not None, before=before, after=after, final=final,
                        delivery_delta=delta(final["delivery"], before["delivery"]),
                        traffic_delta=delta(final["traffic"], before["traffic"]))
            report.save()
            check(text is not None, "stock did not receive zero-hop text")
            check(case["traffic_delta"]["tx"] == 1 and case["delivery_delta"]["retry"] == 0,
                  "zero-hop text caused a useless retransmission or identity announcement")
            report.finish_case(case)

        if selected & {"same-tuple", "rename"}:
            # Previous all-case hops-zero tests intentionally leave hops=0.
            # Restore the baseline before observing either new configuration.
            wait_idle(companion, args.timeout)
            companion.cli("set mt.hops 7")
            companion.cli("set flood.retry.count 0")
            companion.cli("set flood.retry.advert off")

        if "same-tuple" in selected:
            case = report.case("same-tuple-publication-recovery")
            before, since = snapshot(companion), time.monotonic()
            companion.cli("set radio2 909.5,500,5,5,rxtx,32")
            # Do not repair/reapply any setting while the publication lease
            # expires: only read-only status polls may cause this test to pass.
            case["publication"] = wait_active(companion, args.timeout)
            after = snapshot(companion)
            case.update(before=before, after=after,
                        traffic_delta=delta(after["traffic"], before["traffic"]),
                        primary_after=companion.cli("get radio"),
                        packets=[packet_summary(p) for p in stock.packets_since(since, node_id)],
                        public_safety="primary unchanged; no Public transmission requested")
            report.save()
            check(case["traffic_delta"]["tx"] == 0, "same-tuple configuration caused unsolicited Meshtext TX")
            check(case["primary_after"] == data["primary"], "same-tuple radio2 update changed primary radio")
            check(not case["packets"], "stock observed unsolicited DUT traffic after same-tuple update")
            report.finish_case(case)

        if "rename" in selected:
            case = report.case("rename-rearms-first-chat-nodeinfo")
            original_name = data["companion"].get("primary", {}).get("node_name")
            check(isinstance(original_name, str) and original_name
                  and not any(character in original_name for character in "\r\n\0"),
                  "APP_START did not provide a safely restorable original node name")
            if stock_role != "CLIENT_MUTE":
                case["stock"] = stock.configure_lab(key, "CLIENT_MUTE")
                stock_role = "CLIENT_MUTE"
            require_stock_connected(stock)
            new_name = "MT-HIL-" + run_id
            check(len(new_name.encode()) < 24 and new_name != original_name, "invalid unique rename test name")
            case.update(original_name=original_name, test_name=new_name, original_name_restored=False)
            renamed = False
            try:
                before = snapshot(companion)
                companion.cli("set name " + new_name)
                renamed = True
                time.sleep(0.4)
                case["after_rename_quiet"] = snapshot(companion)
                check(case["after_rename_quiet"]["traffic"]["tx"] == before["traffic"]["tx"],
                      "renaming the node transmitted Meshtext before user chat")
                since = time.monotonic()
                tokens = []
                case["probes"] = []
                case["probe_timeout_seconds"] = min(5, args.timeout)
                # A zero-retry first chat can be lost over RF even though it
                # correctly triggered the one identity TX. Use distinct new
                # chats, retain all misses, and observe NodeInfo from before
                # the first send rather than demanding it after a received one.
                for probe in range(3):
                    require_stock_connected(stock)
                    token = f"MT-delivery {run_id} renamed {probe}"
                    tokens.append(token)
                    companion.send_text(args.channel_slot, token)
                    text = stock.wait_packet(node_id, lambda p: p.get("decoded", {}).get("payload")
                                             == token.encode(), since, min(5, args.timeout))
                    info = stock.wait_packet(node_id, lambda p: port_number(p) == "NODEINFO_APP",
                                             since, min(5, args.timeout))
                    case["probes"].append({"index": probe, "received": text is not None,
                        "received_in_probe_window": text is not None,
                        "packet_id": text.get("id") if text else None})
                    report.save()
                    if text is not None and info is not None:
                        break
                after = wait_idle(companion, args.timeout)
                require_stock_connected(stock)
                packets = stock.packets_since(since, node_id)
                for token, probe in zip(tokens, case["probes"]):
                    text = next((packet for packet in packets
                                 if packet.get("decoded", {}).get("payload") == token.encode()), None)
                    probe["received"] = text is not None
                    probe["packet_id"] = text.get("id") if text else None
                infos = [packet for packet in packets if port_number(packet) == "NODEINFO_APP"]
                case.update(before=before, after=after,
                            attempted_texts=len(tokens),
                            received_texts=sum(probe["received"] for probe in case["probes"]),
                            decoded_nodeinfo_events=len(infos),
                            traffic_delta=delta(after["traffic"], before["traffic"]),
                            packets=[packet_summary(p) for p in packets])
                report.save()
                check(case["received_texts"] >= 1, "stock did not receive any of three distinct chats after rename")
                check(len(infos) == 1, "rename did not produce exactly one observed automatic NodeInfo")
                case["nodeinfo"] = inspect_nodeinfo(infos[0], node_id)
                report.save()
                check(case["nodeinfo"]["long_name"] == new_name, "automatic NodeInfo retained the old long name")
                check(case["traffic_delta"]["tx"] == len(tokens) + 1 and case["traffic_delta"]["fail"] == 0,
                      "renamed chat probes did not complete precisely attempted texts + one NodeInfo TX")
                check(not any(port_number(p) == "POSITION_APP" for p in packets),
                      "renamed first chat emitted position data")
            finally:
                if renamed:
                    before_restore = snapshot(companion)
                    companion.cli("set name " + original_name)
                    time.sleep(0.4)
                    after_restore = snapshot(companion)
                    restored_name = companion.cli("get name")
                    check(restored_name in (original_name, "> " + original_name), "original node name was not restored")
                    case["original_name_restored"] = True
                    case["restore_traffic_delta"] = delta(after_restore["traffic"], before_restore["traffic"])
                    report.save()
                    check(case["restore_traffic_delta"]["tx"] == 0,
                          "restoring the original name caused unsolicited Meshtext TX")
            report.finish_case(case)
        data["final"] = snapshot(companion)
        data["result"] = "PASS"
    except (Exception, KeyboardInterrupt) as error:
        message = str(error)
        for secret in (key.hex(), str(key), repr(key), base64.b64encode(key).decode()):
            message = message.replace(secret, "<redacted>")
        data["result"] = "FAIL"
        data["failure"] = {"type": type(error).__name__, "message": message[:1500]}
        if data["cases"] and data["cases"][-1]["result"] == "RUNNING":
            data["cases"][-1]["result"] = "FAIL"
    finally:
        for name, device in (("stock", stock), ("companion", companion)):
            if device is not None:
                try:
                    device.close()
                except Exception as error:
                    data.setdefault("close_failures", []).append({"device": name, "type": type(error).__name__})
                    data["result"] = "FAIL"
        data["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report.save()
        report.stream.close()
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companion-port")
    parser.add_argument("--stock-port")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--channel-slot", type=int, default=1)
    parser.add_argument("--key-bits", type=int, choices=(128, 256), default=128)
    parser.add_argument("--case", default="all", help="all or comma-separated: " + ",".join(CASES))
    parser.add_argument("--timeout", type=float, default=25, help="each bounded observation, at most 30 s")
    parser.add_argument("--quiet-seconds", type=float, default=5)
    parser.add_argument("--echo-attempts", type=int, default=3)
    parser.add_argument("--allow-radio-changes", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    try:
        check(args.case == "all" or set(args.case.split(",")) <= set(CASES), "unknown case")
        check(0 <= args.channel_slot <= 255, "channel-slot must be 0..255")
        check(0 < args.timeout <= 30 and 0 < args.quiet_seconds <= 30,
              "each wait must be positive and at most 30 s")
        check(1 <= args.echo_attempts <= 5, "echo-attempts must be 1..5")
        if args.plan:
            print(json.dumps({"rf": RF, "cases": CASES, "key_bits": args.key_bits,
                              "consent_required": "--allow-radio-changes",
                              "primary_precondition": "DUT primary already 909.5 MHz; use lab TX-frequency guard",
                              "silent_role": "CLIENT_MUTE", "echo_role": "CLIENT",
                              "echo_retry_budget": 15, "settings_restored": False}, indent=2))
            return 0
        check(args.allow_radio_changes, "--allow-radio-changes is required before opening ports")
        check(args.companion_port and args.stock_port and args.report,
              "--companion-port, --stock-port and --report are required")
        check(os.path.realpath(args.companion_port) != os.path.realpath(args.stock_port), "ports must be distinct")
        check(not args.report.exists() and args.report.parent.is_dir(),
              "choose a fresh report in an existing directory")
        result = run(args)
        print(json.dumps({"result": result["result"], "report": str(args.report),
                          "failure": result.get("failure"), "settings_restored": False}, indent=2))
        return 0 if result["result"] == "PASS" else 1
    except Failure as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
