#!/usr/bin/env python3
"""Explicit, witnessed real-RF packet delivery during T-Deck redraw.

--plan needs neither serial dependencies nor hardware. Live runs require three
explicit ports/USB serials and --allow-transmit. Production Full Companions must
already be configured as stationary primary-only sender and dual-profile RX-only
witness matching the T-Deck. No setters, flashing, rebooting or RF retries.
See tdeck_radio2_delivery_README.md for fixture and evidence requirements.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import time

from split_radio_probe import make_probe, parse_probe, companion_send_command, profile_counters

PROFILES = (
    dict(freq_mhz=909.5, bw_khz=62.5, sf=7, cr=5, preamble=32),
    dict(freq_mhz=910.5, bw_khz=500, sf=8, cr=5, preamble=32),
)


class EvidenceError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def uint(value, maximum=0xffffffff):
    return type(value) is int and 0 <= value <= maximum


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "Duplicate JSON evidence key: " + key)
        value[key] = item
    return value


class JsonLines:
    """Keep fragments across timeouts; fail closed on malformed JSON evidence."""
    def __init__(self):
        self.pending = bytearray()
        self.values = deque()
        self.raw = []

    def feed(self, chunk):
        self.pending.extend(chunk)
        require(len(self.pending) <= 16384, "T-Deck line capture overflow")
        while b"\n" in self.pending:
            line, _, tail = self.pending.partition(b"\n")
            self.pending = bytearray(tail)
            text = line.decode("utf-8", "strict").strip()
            self.raw.append(text)
            if not text.startswith(("{", "[")):
                continue
            try:
                value = json.loads(text, object_pairs_hook=unique_object)
            except ValueError as error:
                raise EvidenceError("Malformed T-Deck JSON") from error
            require(type(value) is dict, "T-Deck JSON must be an object")
            require("error" not in value, "T-Deck rejected command: " + str(value))
            require(len(self.values) < 64, "T-Deck response queue overflow")
            self.values.append(value)

    def take(self, key, expected=None):
        while self.values:
            value = self.values.popleft()
            if key not in value:
                continue
            if expected is not None:
                require(type(value[key]) is type(expected) and value[key] == expected,
                        f"Stale/mismatched {key} response")
            return value
        return None


class TDeck:
    def __init__(self, name):
        import serial
        from profile_switch import open_session
        self.device = serial.Serial(port=None, baudrate=115200, timeout=0.05,
                                    write_timeout=2, exclusive=True)
        self.device.port = name
        self.lines = JsonLines()
        self.commands = []
        try:
            open_session(self.device)
        except BaseException:
            self.device.close()
            raise

    def close(self):
        self.device.close()

    def send(self, command):
        self.commands.append(command)
        payload = (command + "\n").encode("ascii")
        require(self.device.write(payload) == len(payload), "Short T-Deck command write")
        self.device.flush()

    def receive(self, key, expected=None, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.lines.take(key, expected)
            if value is not None:
                return value
            self.lines.feed(self.device.readline())
        raise TimeoutError("No complete T-Deck " + key)

    def retained(self, command, key, expected, timeout=5):
        self.send(command)
        try:
            return self.receive(key, expected, timeout)
        except TimeoutError:
            # Only a read-only retained result may be replayed, never delivery/TX.
            self.send(command)
            return self.receive(key, expected, timeout)


def unprompt(reply):
    return reply.strip().removeprefix("> ")


def check_fixture(node, profile, *, witness=False):
    commands = ("get radio", "get radio.preamble", "get radio.mode", "get radio2", "get tempradio",
                "get tempradio2", "get radio2.status", "get repeat", "get radio2.cross",
                "get radio.rxps.config", "get radioat", "get tempradioat",
                "get radioat2", "get tempradioat2")
    state = {command: node.cli(command) for command in commands}
    rf = PROFILES[0 if witness else profile]
    fields = unprompt(state["get radio"]).split(",")
    require(len(fields) == 4, "Missing exact primary radio tuple")
    try:
        actual = tuple(float(value) for value in fields)
    except ValueError as error:
        raise EvidenceError("Malformed primary radio tuple") from error
    expected = (rf["freq_mhz"], rf["bw_khz"], rf["sf"], rf["cr"])
    require(actual == expected, "Companion primary tuple differs from selected target")
    require(unprompt(state["get radio.preamble"]) == "32", "Explicit primary preamble32 required")
    require(unprompt(state["get radio.mode"]) in (("rx",) if witness else ("both", "tx")),
            "Witness must be RX-only; sender must permit primary TX")
    require(all(unprompt(state[key]) == "off" for key in ("get tempradio", "get tempradio2")),
            "Temporary profiles must already be off")
    require(all(unprompt(state[key]) == "off" for key in ("get repeat", "get radio2.cross")),
            "Repeating and cross-profile bridging must already be off")
    require(unprompt(state["get radio.rxps.config"]).startswith("off,"), "Disable RX power saving first")
    require(all(unprompt(state[key]).lower() in ("off", "none", "-none-", "slots:")
                for key in ("get radioat", "get tempradioat", "get radioat2", "get tempradioat2")),
            "Remove pre-existing radio schedules first")
    status = unprompt(state["get radio2.status"])
    if witness:
        secondary = unprompt(state["get radio2"]).split(";", 1)[0].split(",")
        require(len(secondary) == 6 and secondary[3:] == ["5", "rx", "32"],
                "Witness secondary must be receive-only with explicit preamble32")
        require(tuple(map(float, secondary[:3])) == (910.5, 500, 8), "Witness secondary tuple mismatch")
        require(status.startswith("rx; ") and re.search(r"\bpreamble=32,32(?:;|$)", status),
                "Witness must have both actual RX profiles active (required for A0/A1 tags)")
    else:
        require(status.startswith("off; ") and re.search(r"\bpreamble=32,\d+(?:;|$)", status),
                "Sender active radio2 must be off and active primary preamble32")
    state["counters"] = profile_counters(status)
    if not witness:
        state["get tx"] = node.cli("get tx")
        state["get tx.status"] = node.cli("get tx.status")
        power = unprompt(state["get tx"])
        require(re.fullmatch(r"-?\d+", power) is not None and -9 <= int(power) <= 0,
                "Preconfigure sender power in -9..0dBm")
        require(unprompt(state["get tx.status"]) == f"desired={int(power)},apply=applied",
                "Sender TX power has not been applied")
    return state


def check_ready(ready, sequence, mode, seconds, fps, spi_mhz, bulk):
    expected = dict(delivery_ready=sequence, display_mode=mode, spi_mhz=spi_mhz,
                    bulk=bulk, seconds=seconds, fps=fps, capture_slots=32,
                    profiles=list(PROFILES))
    for key, value in expected.items():
        require(type(ready.get(key)) is type(value) and ready[key] == value,
                "T-Deck readiness mismatch: " + key)


def capture_witness(node, seconds):
    from esp32_companion_serial_stress import FrameTimeout
    frames = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            frame = node.reader.read_frame(node.port, min(0.1, deadline - time.monotonic()))
        except FrameTimeout:
            continue
        require(len(frames) < 256, "Witness frame capture overflow")
        frames.append(dict(frame_hex=frame.hex(), host_monotonic=time.monotonic()))
    return frames


def evaluate(trial):
    """Pure, strict evidence checks. ACKs/counters alone never credit delivery."""
    failures = []

    def check(condition, reason):
        if not condition:
            failures.append(reason)

    sequence, profile, mode = (trial[key] for key in ("sequence", "profile", "mode"))
    wire = bytes.fromhex(trial["wire_hex"])
    probe = parse_probe(wire)
    check(probe is not None and probe.path == b"" and probe.route == 2
          and probe.sequence == sequence and probe.source == ("R" if profile else "L")
          and wire == make_probe(probe.run, probe.source, sequence,
                                 payload_size=len(wire) - 2, direct=True), "expected_probe_identity_invalid")
    result = trial.get("result", {})
    delivery = result.get("delivery", {})
    records = trial.get("records", [])
    check(trial.get("sender_ack_hex") == "00", "sender_ack_missing_or_not_exact_OK")
    tx_confirmed = {}
    for label, expected_delta in (("sender", [1, 0]), ("witness", [0, 0])):
        before = trial.get(label + "_before", {}).get("counters", {}).get("tx", [])
        after = trial.get(label + "_after", {}).get("counters", {}).get("tx", [])
        valid = len(before) == len(after) == 2 and all(uint(x) for x in before + after)
        # Reset/wrap is not assumed to be a single acknowledged transmission.
        tx_confirmed[label] = valid and [b + d for b, d in zip(before, expected_delta)] == after
        check(tx_confirmed[label], label + "_physical_tx_counter_mismatch")
    check(type(result.get("result")) is int and result["result"] == sequence, "result_sequence_mismatch")
    check(delivery.get("enabled") is True and type(delivery.get("sequence")) is int
          and delivery["sequence"] == sequence, "delivery_not_armed_for_sequence")
    check(uint(delivery.get("records"), 32) and delivery["records"] == len(records), "record_count_mismatch")
    check(type(delivery.get("dropped")) is int and delivery["dropped"] == 0, "capture_overflow_or_missing")
    check(type(delivery.get("rx_errors")) is int and delivery["rx_errors"] == 0, "rx_errors_or_missing")
    for key, expected in (("display_mode", mode), ("spi_mhz", trial["spi_mhz"]),
                          ("seconds", trial["seconds"]), ("requested_fps", trial["fps"]),
                          ("switch_failures", 0), ("failed_retunes", 0),
                          ("chip_errors_before", 0), ("chip_errors_after", 0), ("rx_mode", 1)):
        check(type(result.get(key)) is int and result[key] == expected, "result_" + key)
    check(result.get("bulk") is trial["bulk"], "result_bulk")
    check(result.get("busy_timeout") is False and result.get("cache_correct") is True,
          "radio_health_missing_or_failed")
    elapsed, frames = result.get("elapsed_us"), result.get("frames")
    check(uint(elapsed) and trial["seconds"] * 1000000 <= elapsed <= (trial["seconds"] + 1) * 1000000,
          "measurement_window_invalid")
    check(uint(frames) and (frames == 0 if mode == 0 else frames >= 2), "redraw_workload_missing")
    check(uint(result.get("switches")) and result["switches"] > 0, "profile_switching_missing")
    check(uint(result.get("received_packets")) and result["received_packets"] == len(records),
          "received_packet_count_mismatch")
    exact, indices = [], []
    for record in records:
        check(type(record.get("delivery_record")) is int and record["delivery_record"] == sequence,
              "record_sequence_mismatch")
        indices.append(record.get("index"))
        raw = record.get("wire_hex", "")
        valid_hex = type(raw) is str and re.fullmatch(r"(?:[0-9a-f]{2}){1,255}", raw) is not None
        check(valid_hex and uint(record.get("len"), 255) and len(raw) == record["len"] * 2,
              "record_length_or_bytes_invalid")
        check(uint(record.get("at_us")) and uint(elapsed) and record["at_us"] <= elapsed,
              "record_outside_measurement")
        check(uint(record.get("frame")) and uint(frames) and record["frame"] <= frames
              and (record["frame"] >= 1 if mode else record["frame"] == 0), "record_redraw_context_invalid")
        check(type(record.get("during_redraw")) is bool
              and (mode == 2 or record["during_redraw"] is False), "record_redraw_flag_invalid")
        if valid_hex and raw == wire.hex():
            exact.append(record)
    check(all(uint(index, 31) for index in indices) and indices == list(range(len(records))),
          "duplicate_or_missing_record_indices")
    check(len(exact) == 1, "missing_or_duplicate_exact_TDeck_packet")
    check(len(exact) == 1 and type(exact[0].get("profile")) is int
          and exact[0]["profile"] == profile, "wrong_or_missing_TDeck_receive_profile")
    witness_exact = []
    for observation in trial.get("witness_frames", []):
        try:
            frame = bytes.fromhex(observation["frame_hex"])
        except (ValueError, KeyError, TypeError):
            check(False, "malformed_witness_frame")
            continue
        # Production PUSH_RAW_DATA includes SNR, RSSI and the actual A0/A1
        # receive marker. A single-profile witness would emit FF, which is
        # deliberately insufficient: it also permits unlabelled bridge ingress.
        if len(frame) >= 4 and frame[0] == 0x84 and frame[4:] == wire[2:]:
            witness_exact.append(frame)
    check(len(witness_exact) == 1, "missing_or_duplicate_exact_RF_witness")
    witnessed = len(witness_exact) == 1 and witness_exact[0][3] == 0xA0 + profile
    check(witnessed, "witness_profile_unproven")
    return dict(passed=not failures, failures=failures, exact_tdeck_packets=len(exact),
                exact_witness_packets=len(witness_exact),
                sent_confirmed=trial.get("sender_ack_hex") == "00" and tx_confirmed["sender"] and witnessed
                    and "expected_probe_identity_invalid" not in failures,
                received_confirmed=len(exact) == 1 and type(exact[0].get("profile")) is int
                    and exact[0]["profile"] == profile and all(
                        reason.startswith(("sender_", "witness_"))
                        or reason in ("missing_or_duplicate_exact_RF_witness", "malformed_witness_frame")
                        for reason in failures),
                received_during_redraw_callback=any(record.get("during_redraw") is True for record in exact))


def run_trial(bench, sender, witness, trial, *, capture=capture_witness, checkpoint=lambda: None):
    """One emission at most. Partial evidence stays in trial on every failure."""
    sequence = trial["sequence"]
    trial["sender_before"] = check_fixture(sender, trial["profile"])
    trial["witness_before"] = check_fixture(witness, trial["profile"], witness=True)
    command = (f"delivery {trial['mode']} {trial['spi_mhz']} {int(trial['bulk'])} "
               f"{trial['seconds']} {trial['fps']} {sequence}")
    bench.send(command)
    trial["ready"] = bench.receive("delivery_ready", sequence, 5)
    check_ready(trial["ready"], sequence, trial["mode"], trial["seconds"],
                trial["fps"], trial["spi_mhz"], trial["bulk"])
    trial["send_attempted"] = True
    trial["send_requested_host_monotonic"] = time.monotonic()
    checkpoint()
    try:
        ack = sender.request(companion_send_command(bytes.fromhex(trial["wire_hex"])), (0,), timeout=2)
        trial["sender_ack_hex"] = ack.hex()
    except Exception as error:
        # A timeout may already have emitted RF. Preserve receiver evidence and
        # fail the trial; never resend the command or infer an acknowledgement.
        trial["sender_error"] = str(error)
    checkpoint()
    trial["witness_frames"] = capture(witness, trial["seconds"] + 0.25)
    checkpoint()
    try:
        trial["result"] = bench.receive("result", sequence, trial["seconds"] + 3)
    except TimeoutError:
        trial["result"] = bench.retained(f"result {sequence}", "result", sequence)
    count = trial["result"].get("delivery", {}).get("records")
    require(uint(count, 32), "Invalid capture record count")
    trial["records"] = []
    for index in range(count):
        record = bench.retained(f"delivery-record {sequence} {index}", "delivery_record", sequence)
        require(type(record.get("index")) is int and record["index"] == index, "Record index mismatch")
        trial["records"].append(record)
        checkpoint()
    trial["sender_after"] = check_fixture(sender, trial["profile"])
    trial["witness_after"] = check_fixture(witness, trial["profile"], witness=True)
    trial["assessment"] = evaluate(trial)
    return trial["assessment"]["passed"]


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tdeck", "sender", "witness"):
        parser.add_argument("--" + name + "-port")
        parser.add_argument("--" + name + "-serial")
    parser.add_argument("--profile", type=int, choices=(0, 1), default=1)
    parser.add_argument("--modes", type=int, nargs="+", choices=(0, 1, 2), default=[0, 1, 2])
    parser.add_argument("--sizes", type=int, nargs="+", choices=(32, 64, 172), default=[64, 172],
                        help="exact RF wire bytes, including two-byte MeshCore header")
    parser.add_argument("--count", type=int, choices=range(1, 101), default=3)
    parser.add_argument("--seconds", type=int, choices=range(3, 11), default=5)
    parser.add_argument("--fps", type=int, choices=range(1, 31), default=20)
    parser.add_argument("--spi-mhz", type=int, choices=(2, 8), default=8)
    parser.add_argument("--bytewise", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-transmit", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    if len(set(args.modes)) != len(args.modes) or len(set(args.sizes)) != len(args.sizes):
        parser.error("select unique modes and sizes")
    if not args.plan:
        if not args.allow_transmit:
            parser.error("--allow-transmit is required before opening any port")
        ports = [getattr(args, name + "_port") for name in ("tdeck", "sender", "witness")]
        serials = [getattr(args, name + "_serial") for name in ("tdeck", "sender", "witness")]
        if not all(ports + serials) or not args.output:
            parser.error("three explicit ports, USB serials and a fresh --output are required")
        if len({os.path.realpath(port).casefold() for port in ports}) != 3 or len({s.casefold() for s in serials}) != 3:
            parser.error("select three distinct physical USB devices")
        if args.output.exists() or not args.output.parent.is_dir():
            parser.error("select a fresh report in an existing directory")
    return args


def main(argv=None):
    args = arguments(argv)
    plan = dict(profile=args.profile, rf=PROFILES[args.profile], modes=args.modes,
                sizes=args.sizes, packets=args.count * len(args.modes) * len(args.sizes),
                seconds_per_packet=args.seconds, fps=args.fps, spi_mhz=args.spi_mhz,
                bulk=not args.bytewise, retries=0, settings_changed=False,
                witness_required=True, scope="PHY/raw delivery during redraw; not Full Companion UI/app delivery")
    if args.plan:
        print(json.dumps(plan, indent=2))
        return 0
    # All hardware/pyserial imports and opens remain behind explicit invocation.
    from companion_raw_radio2_sweep import safe_identity
    from companion_serial import Companion, require_idle_port
    nodes, bench = {}, None
    report = dict(schema=1, campaign="tdeck-radio2-delivery-v1", plan=plan,
                  started_utc=datetime.now(timezone.utc).isoformat(), complete=False,
                  passed=False, trials=[], run_id=secrets.token_bytes(8).hex())
    with args.output.open("x", encoding="utf-8") as stream:
        def persist():
            if bench is not None:
                report["tdeck_raw_lines"] = bench.lines.raw
                report["tdeck_commands"] = bench.commands
            stream.seek(0)
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.truncate()
            stream.flush()

        persist()
        try:
            report["usb"] = {}
            for name in ("tdeck", "sender", "witness"):
                port, serial = getattr(args, name + "_port"), getattr(args, name + "_serial")
                report["usb"][name] = safe_identity(port, serial)
                require_idle_port(port)
                if name != "tdeck":
                    usb = report["usb"][name]
                    # Companion.start asserts DTR: restrict to native CDC that
                    # does not wire DTR/RTS to boot/reset. T-Deck uses its own
                    # existing board-aware no-reset opener.
                    require(usb["vid"] in (0x2886, 0x239A, 0x303A)
                            and (usb["vid"], usb["pid"]) != (0x303A, 0x1001),
                            "Production sender/witness require native CDC, not UART/JTAG reset bridges")
            bench = TDeck(args.tdeck_port)
            bench.send("")
            bench.send("info")
            info = bench.receive("ready")
            report["firmware"] = info
            require(info.get("ready") is True and info.get("autonomous_tx") is False
                    and info.get("bench") == "tdeck-display-radio2-v2"
                    and info.get("delivery_version") == 1 and info.get("delivery_slots") == 32,
                    "Wrong/unready T-Deck delivery firmware")
            for name in ("sender", "witness"):
                nodes[name] = Companion(getattr(args, name + "_port"))
                safe_identity(getattr(args, name + "_port"), getattr(args, name + "_serial"))
                report[name + "_firmware"] = nodes[name].start()
            safe_identity(args.tdeck_port, args.tdeck_serial)
            sequence = secrets.randbelow(0x3fffffff) + 1
            run_id = bytes.fromhex(report["run_id"])
            for repeat in range(args.count):
                modes = args.modes[repeat % len(args.modes):] + args.modes[:repeat % len(args.modes)]
                for mode in modes:
                    for size in args.sizes:
                        sequence += 1
                        wire = make_probe(run_id, "R" if args.profile else "L", sequence,
                                          payload_size=size - 2, direct=True)
                        trial = dict(sequence=sequence, profile=args.profile, mode=mode,
                                     repeat=repeat + 1, wire_hex=wire.hex(), spi_mhz=args.spi_mhz,
                                     bulk=not args.bytewise, seconds=args.seconds, fps=args.fps)
                        report["trials"].append(trial)
                        persist()
                        passed = run_trial(bench, nodes["sender"], nodes["witness"], trial, checkpoint=persist)
                        persist()
                        require(passed, "Delivery evidence failed; stopping without retries")
            report["complete"] = report["passed"] = True
        except (Exception, KeyboardInterrupt) as error:
            report["error"] = dict(type=type(error).__name__, message=str(error))
        finally:
            for node in nodes.values():
                try:
                    node.close()
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append(str(error))
            if bench is not None:
                try:
                    bench.close()
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append(str(error))
            if report.get("cleanup_errors"):
                report["passed"] = False
            trials = report["trials"]
            report["summary"] = dict(planned=plan["packets"],
                attempted=sum(trial.get("send_attempted") is True for trial in trials),
                sent_confirmed=sum(trial.get("assessment", {}).get("sent_confirmed") is True for trial in trials),
                received_confirmed=sum(trial.get("assessment", {}).get("received_confirmed") is True for trial in trials),
                passed_trials=sum(trial.get("assessment", {}).get("passed") is True for trial in trials))
            report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            persist()
    print(json.dumps(dict(passed=report["passed"], complete=report["complete"], output=str(args.output))))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
