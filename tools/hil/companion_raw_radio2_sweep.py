#!/usr/bin/env python3
"""Identity-bound USB RAW_CUSTOM radio2 benchmark for two Full Companions.

TRANSMITS LoRa and changes radio2, tx.ssh and TX power, with explicit consent.
Requires both primary radios already rebooted onto USA 910.525/62.5/SF7/CR5
and an immutable compiled 909.5 MHz HIL TX lock. Never changes primary, WiFi,
channels, keys, bootloaders or firmware. Remove mt-/r2- channels beforehand.

The 176-byte Companion host response limits raw application payload to172B;
230-byte direct MTU requires the separate KISS modem path, not this API.
Sizes include the four-byte SSH routing tag. Measures exact opposite-radio
RAW reception and host stop/wait goodput, not an established SSH/TCP session.
No retries conceal RF losses. Run --plan without dependencies or hardware.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import time


GUARD = "HIL TX lock=909.500 MHz; off-frequency TX blocked; primary RX-only when off-frequency"
PREAMBLES = (128, 256, 384, 512)
SIZES = (64, 172)
PRIMARY = (910.525, 62.5, 7, 5)
RF = {"frequency_mhz": 909.5, "bandwidth_khz": 500, "sf": 5, "cr": 5, "tx_dbm": 2}
RAW_LIMIT = 172


class Failure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def parse_primary(reply):
    match = re.fullmatch(r"\s*(?:>\s*)?(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)\s*", reply)
    require(match is not None, "primary radio tuple is missing or ambiguous")
    result = (float(match[1]), float(match[2]), int(match[3]), int(match[4]))
    require(abs(result[0] - PRIMARY[0]) <= 0.00005 and result[1:] == PRIMARY[1:],
            "primary must already be USA 910.525,62.5,7,5; reboot after preconfiguration")
    return result


def parse_tx(reply):
    found = re.findall(r"\bTX=(\d+),(\d+)(?=\s|;|$)", reply)
    require(len(found) == 1, "per-profile TX counters are missing or ambiguous")
    return list(map(int, found[0]))


def prove_guard(reply):
    require(reply.strip().removeprefix("> ") == GUARD,
            "immutable compiled 909.5 MHz TX lock is not confirmed; refusing all setters/TX")


def make_payload(run_id, sequence, direction, size):
    require(len(run_id) == 8 and 0 <= sequence <= 0xffffffff and direction in (0, 1),
            "invalid raw probe identity")
    require(type(size) is int and 17 <= size <= RAW_LIMIT, "raw payload must fit17..172 bytes")
    header = b"SSH\x01" + run_id + struct.pack("<IB", sequence, direction)
    return header + (bytes(range(256)) * 2)[:size - len(header)]


def raw_payload(frame):
    return frame[4:] if len(frame) >= 8 and frame[0] == 0x84 else None


def safe_identity(path, expected_serial):
    """Enumerate metadata only; do not open a mismatched/stale tty."""
    from serial.tools import list_ports
    require(expected_serial and not any(c in expected_serial for c in "\r\n\0"),
            "an exact stable USB serial is required")
    resolved = os.path.normcase(os.path.realpath(path))
    matches = [port for port in list_ports.comports()
               if os.path.normcase(os.path.realpath(port.device)) == resolved]
    require(len(matches) == 1 and (matches[0].serial_number or "").upper() == expected_serial.upper(),
            "selected USB port does not match its expected physical serial")
    info = matches[0]
    return {"selected_path": path, "port": info.device, "serial": info.serial_number,
            "vid": info.vid, "pid": info.pid}


def safety(nodes):
    result = {}
    for name, node in nodes.items():
        guard, primary = node.cli("get lab.tx"), node.cli("get radio")
        prove_guard(guard)
        parse_primary(primary)
        status = node.cli("get radio2.status")
        counters = parse_tx(status)
        require(counters[0] == 0, "primary TX counter is nonzero; aborting before any further test TX")
        result[name] = {"guard": guard, "primary": primary, "status": status, "tx": counters}
    return result


def receive_exact(node, payload, timeout):
    from esp32_companion_serial_stress import FrameTimeout
    deadline = time.monotonic() + timeout
    unexpected_raw = ignored = 0
    while time.monotonic() < deadline:
        try:
            frame = node.reader.read_frame(node.port, min(0.1, max(0.001, deadline - time.monotonic())))
        except FrameTimeout:
            continue
        observed = raw_payload(frame)
        if observed == payload:
            return {"received": True, "completed_at": time.monotonic(),
                    "unexpected_raw": unexpected_raw, "ignored_frames": ignored}
        if observed is not None:
            unexpected_raw += 1
        else:
            ignored += 1
    return {"received": False, "unexpected_raw": unexpected_raw, "ignored_frames": ignored}


def ensure_low_tx(node, timeout=8):
    """Avoid needless radio handoffs; retry only the exact transient rejection."""
    def read_tx():
        reply = node.cli("get tx")
        match = re.fullmatch(r"\s*(?:>\s*)?([+-]?\d+)\s*", reply)
        require(match is not None, "TX power readback is missing or ambiguous")
        return int(match[1])

    initial = read_tx()
    if initial == RF["tx_dbm"]:
        return {"initial_dbm": initial, "verified_dbm": initial, "setter_attempts": 0}
    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        attempts += 1
        try:
            reply = node.cli("set tx 2")
        except Exception as error:
            if not re.fullmatch(r"Companion CLI set tx rejected: Error: radio busy or TX power rejected",
                                str(error)):
                raise
            busy = True
        else:
            busy = reply.strip() == "Error: radio busy or TX power rejected"
            require(busy or not re.match(r"(?:Error|ERR|Err)\b", reply), "TX power setter rejected")
        if not busy:
            require(read_tx() == RF["tx_dbm"], "TX power is not verified at2dBm; refusing transmission")
            return {"initial_dbm": initial, "verified_dbm": RF["tx_dbm"], "setter_attempts": attempts}
        remaining = deadline - time.monotonic()
        require(remaining > 0 and attempts < 33, "TX power stayed busy for the bounded8-second setup window")
        time.sleep(min(0.25, remaining))


def metrics(transfers, elapsed):
    received = [packet for packet in transfers if packet["received"]]
    total = sum(packet["raw_bytes"] for packet in received)
    app = sum(packet["raw_bytes"] - 4 for packet in received)
    packet_seconds = sum(packet["elapsed_seconds"] for packet in transfers)
    directions = {}
    for name in ("a_to_b", "b_to_a"):
        records = [packet for packet in transfers if packet["direction"] == name]
        ok = [packet for packet in records if packet["received"]]
        seconds = sum(packet["elapsed_seconds"] for packet in records)
        directions[name] = {"attempted": len(records), "received": len(ok),
            "lost": len(records) - len(ok),
            "raw_goodput_kbps": sum(packet["raw_bytes"] for packet in ok) * 8 / seconds / 1000 if seconds else 0}
    return {"attempted": len(transfers), "received": len(received),
            "lost": len(transfers) - len(received),
            "loss_fraction": 1 - len(received) / len(transfers) if transfers else None,
            "elapsed_seconds": elapsed, "raw_goodput_kbps": total * 8 / elapsed / 1000 if elapsed else 0,
            "application_goodput_kbps": app * 8 / elapsed / 1000 if elapsed else 0,
            "packet_window_seconds": packet_seconds,
            "packet_window_raw_goodput_kbps": total * 8 / packet_seconds / 1000 if packet_seconds else 0,
            "packet_window_application_goodput_kbps": app * 8 / packet_seconds / 1000 if packet_seconds else 0,
            "directions": directions,
            "definition": "wall-clock rates include command ACK, loss timeouts, pacing and report checkpoints; packet-window rates sum send-through-receipt/timeout windows excluding pacing/checkpoints; application excludes4-byte SSH routing tag; not SSH throughput"}


def run_matrix(nodes, preambles, sizes, *, count, timeout, interval, persist,
               receiver=receive_exact, run_id=None):
    run_id = secrets.token_bytes(8) if run_id is None else run_id
    report = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "result": "RUNNING", "rf": RF, "primary_rx_only": list(PRIMARY),
              "run_id": run_id.hex(), "retries": 0, "raw_payload_limit": RAW_LIMIT,
              "probe_parameters": {"count_per_direction": count, "timeout_seconds": timeout,
                                   "interval_seconds": interval},
              "mtu230_supported": False, "settings_restored": False, "cases": [],
              "note": "No primary/USA TX; no claim of actual USA receive qualification; no TCP/SSH connection tested"}
    sequence = 0
    try:
        report["initial_safety"] = safety(nodes)
        for node in nodes.values():
            for slot in range(node.max_channels):
                require(not node.channel_name(slot).lstrip("#").startswith(("mt-", "r2-")),
                        "remove conflicting mt-/r2- channels explicitly before the raw benchmark")
        for node in nodes.values():
            for command in ("normalradio", "set tempradio2 off"):
                node.cli(command)
        # Cancelling a scheduled/temporary profile also publishes settings.
        # Complete that bounded handoff before touching radio TX power.
        time.sleep(2.5)
        report["tx_power_setup"] = {name: ensure_low_tx(node) for name, node in nodes.items()}
        for node in nodes.values():
            for command in ("set radio2.cross off", "set tx.ssh radio2"):
                node.cli(command)
        persist(report)
        for preamble in preambles:
            for size in sizes:
                item = {"preamble": preamble, "raw_bytes": size, "application_bytes": size - 4,
                        "result": "RUNNING", "transfers": []}
                report["cases"].append(item)
                persist(report)
                item["safety_before"] = safety(nodes)
                for node in nodes.values():
                    node.cli(f"set radio2 909.5,500,5,5,rxtx,{preamble}")
                # Saved setting publication owns a bounded lease. Do not let
                # that control-plane wait pollute data-plane goodput.
                time.sleep(2.5)
                configured = {}
                for name, node in nodes.items():
                    radio2 = node.cli("get radio2")
                    expected = rf"(?:^|>\s*)909\.5(?:0*),500(?:\.0*)?,5,5,rxtx,{preamble}(?=\s|;|$)"
                    require(re.search(expected, radio2) is not None, "radio2 RF/preamble readback mismatch")
                    route = node.cli("get tx.ssh")
                    require(re.search(r"\btx\.ssh radio2; active=radio2(?:\s|$)", route) is not None,
                            "SSH-tagged raw radio2 policy is not saved and active")
                    configured[name] = {"radio2": radio2, "tx_ssh": route,
                        "timing": node.cli("get radio2.timing"), "status": node.cli("get radio2.status")}
                item["configured"] = configured
                item["safety_before_tx"] = safety(nodes)
                persist(report)
                started = time.monotonic()
                for repetition in range(count):
                    for direction, (source, destination) in enumerate((("a", "b"), ("b", "a"))):
                        sequence += 1
                        payload = make_payload(run_id, sequence, direction, size)
                        sent = time.monotonic()
                        # A valid direct path of zero hops. No advert/contact
                        # dependency, no flooding or packet-pool fragmentation.
                        nodes[source].request(b"\x19\x00" + payload, (0,))
                        observation = receiver(nodes[destination], payload, timeout)
                        elapsed = time.monotonic() - sent
                        completed = observation.pop("completed_at", None)
                        packet = {"direction": source + "_to_" + destination, "repetition": repetition,
                            "sequence": sequence, "raw_bytes": size, "command_completed": True,
                            "sha256": hashlib.sha256(payload).hexdigest(), **observation,
                            "elapsed_seconds": elapsed,
                            "latency_ms": (completed - sent) * 1000 if completed is not None else None}
                        item["transfers"].append(packet)
                        persist(report)
                        if interval:
                            time.sleep(interval)
                item["measurement"] = metrics(item["transfers"], time.monotonic() - started)
                item["safety_after"] = safety(nodes)
                item["result"] = "PASS" if all(packet["received"] for packet in item["transfers"]) else "LOSS"
                persist(report)
        # A loss is a measured result, not a setup failure. Rank useful delivered
        # bytes/time while preserving every individual loss and direction.
        ranked = sorted(report["cases"], key=lambda item: item["measurement"]["packet_window_application_goodput_kbps"], reverse=True)
        report["ranking_metric"] = "packet_window_application_goodput_kbps"
        report["ranked_measured_cases"] = [{"preamble": item["preamble"], "raw_bytes": item["raw_bytes"],
            "application_goodput_kbps": item["measurement"]["application_goodput_kbps"],
            "packet_window_application_goodput_kbps": item["measurement"]["packet_window_application_goodput_kbps"],
            "loss_fraction": item["measurement"]["loss_fraction"]} for item in ranked]
        report["result"] = "PASS" if all(item["result"] == "PASS" for item in report["cases"]) else "LOSS"
    except Exception as error:
        report["result"] = "FAIL"
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        if report["cases"] and report["cases"][-1]["result"] == "RUNNING":
            report["cases"][-1]["result"] = "FAIL"
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    persist(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("a-port", "a-serial", "b-port", "b-serial"):
        parser.add_argument("--" + option)
    parser.add_argument("--preambles", default=",".join(map(str, PREAMBLES)))
    parser.add_argument("--sizes", default="64,172", help="total RAW bytes including4-byte SSH tag; max172")
    parser.add_argument("--count", type=int, default=10, help="packets per direction/size/preamble")
    parser.add_argument("--timeout", type=float, default=1)
    parser.add_argument("--interval", type=float, default=0.01)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-radio-changes", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        preambles, sizes = ([int(value) for value in text.split(",")]
                            for text in (args.preambles, args.sizes))
        require(preambles and len(set(preambles)) == len(preambles) and set(preambles) <= set(PREAMBLES),
                "select unique preambles128,256,384,512")
        require(sizes and len(set(sizes)) == len(sizes) and all(17 <= size <= RAW_LIMIT for size in sizes),
                "select unique raw sizes17..172; 230-byte MTU requires KISS modem firmware")
        require(1 <= args.count <= 100 and 0 < args.timeout <= 5 and 0 <= args.interval <= 1
                and math.isfinite(args.timeout) and math.isfinite(args.interval), "invalid count/timing bounds")
        if args.plan:
            print(json.dumps({"rf": RF, "preambles": preambles, "raw_sizes": sizes,
                "count_per_direction": args.count, "primary_rx_only": list(PRIMARY), "required_guard": GUARD,
                "total_packets": 2 * len(preambles) * len(sizes) * args.count,
                "raw_payload_limit": RAW_LIMIT, "mtu230_supported": False}, indent=2))
            return 0
        require(args.allow_radio_changes, "--allow-radio-changes is required before opening ports")
        require(all((args.a_port, args.a_serial, args.b_port, args.b_serial, args.report)),
                "both ports, exact USB serials, and --report are required")
        require(os.path.realpath(args.a_port) != os.path.realpath(args.b_port)
                and args.a_serial.upper() != args.b_serial.upper(), "select two distinct physical radios")
        require(not args.report.exists() and args.report.parent.is_dir(), "select a fresh report in an existing directory")
    except (Failure, ValueError) as error:
        parser.error(str(error))
    nodes, usb = {}, {}
    with args.report.open("x", encoding="utf-8") as stream:
        try:
            os.chmod(args.report, 0o600)
        except OSError:
            pass

        def persist(report):
            report["usb"] = usb
            stream.seek(0)
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.truncate()
            stream.flush()

        try:
            from meshtext_delivery import dependencies, require_idle_port
            Companion, _ = dependencies()
            for name, port, serial in (("a", args.a_port, args.a_serial), ("b", args.b_port, args.b_serial)):
                usb[name] = safe_identity(port, serial)
                require_idle_port(port)
                nodes[name] = Companion(port)
                safe_identity(port, serial)  # Recheck enumeration after opening.
                usb[name]["companion"] = nodes[name].start()
            report = run_matrix(nodes, preambles, sizes, count=args.count, timeout=args.timeout,
                                interval=args.interval, persist=persist)
        except (Exception, KeyboardInterrupt) as error:
            report = {"result": "FAIL", "failure": {"type": type(error).__name__, "message": str(error)[:1000]}}
            persist(report)
        finally:
            for node in nodes.values():
                node.close()
    print(json.dumps({"result": report["result"], "report": str(args.report),
                      "failure": report.get("failure")}))
    return 0 if report["result"] in ("PASS", "LOSS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
