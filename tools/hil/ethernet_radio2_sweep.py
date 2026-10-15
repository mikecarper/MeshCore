#!/usr/bin/env python3
"""Guarded Ethernet-only R2 sweep; R1 stays USA receive-only.

Requires two explicitly selected USB CLI ports and KISS TCP endpoints. Refuses
old firmware without the immutable 909.500 MHz HIL TX lock. Never writes R1,
WiFi, TX power, channels, or credentials; never flashes or reboots. Raw frame
loss/goodput is measured, not an SSH connection or USA receive qualification.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlparse

from ethernet_primary_probe import SerialNode
from ethernet_ssh_serial import SshSerialNode, ssh_command
from ethernet_kiss_probe import KissEndpoint, run_probe


GUARD = "HIL TX lock=909.500 MHz; off-frequency TX blocked; primary RX-only when off-frequency"
PRIMARY = (Decimal("910.525"), Decimal("62.5"), 7, 5)
PRIMARY_KBPS = 2.734375


@dataclass(frozen=True)
class Candidate:
    index: int
    bandwidth: float
    sf: int
    preamble: int

    @property
    def tuple_text(self):
        return f"909.5,{self.bandwidth:g},{self.sf},5,{self.preamble}"

    def describe(self):
        kbps = self.sf * 4 * self.bandwidth / (5 * (2 ** self.sf))
        return dict(asdict(self), frequency_mhz=909.5, cr=5,
                    nominal_kbps=kbps, nominal_ratio_to_usa=kbps / PRIMARY_KBPS,
                    preamble_ms=self.preamble * (2 ** self.sf) / self.bandwidth)


# The 1x control gets extra margin for the Indicator's slow I2C hop. Other
# candidates hold approximately 65.5 ms of preamble, not a fixed symbol count.
CANDIDATES = tuple(Candidate(*item) for item in (
    (1, 62.5, 7, 64), (2, 125, 7, 64), (3, 250, 7, 128),
    (4, 500, 7, 256), (5, 500, 8, 128), (6, 500, 6, 512), (7, 500, 5, 1024),
    # Longer-preamble controls distinguish acquisition losses from PHY limits.
    (8, 500, 5, 2048), (9, 500, 7, 512),
    # Fast-switch-board controls, including ~16 ms chirp acquisition windows.
    (10, 500, 5, 32), (11, 500, 5, 64), (12, 500, 5, 128), (13, 500, 5, 256),
    (14, 500, 7, 32), (15, 500, 7, 64), (16, 250, 7, 32), (17, 500, 8, 32),
    (18, 125, 7, 32), (19, 250, 8, 32), (20, 250, 7, 48),
    (21, 500, 5, 512), (22, 125, 7, 48)))


def data_lines(reply):
    result = []
    for line in reply.splitlines():
        match = re.match(r"^\s*(?:->\s*)?>\s*(.*?)\s*$", line)
        if match and match[1]:
            result.append(match[1])
    return result


def require_guard(reply):
    matches = [line for line in data_lines(reply) if line.startswith("HIL TX")]
    if matches != [GUARD]:
        raise RuntimeError("Refusing TX/configuration: exact immutable 909.500 MHz HIL lock is not confirmed")


def radio_tuple(reply):
    matches = []
    for line in data_lines(reply):
        match = re.match(r"^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)"
                         r"(?:,(\d+))?(?=[;\s]|$)", line)
        if match:
            matches.append((Decimal(match[1]), Decimal(match[2]), int(match[3]), int(match[4]),
                            int(match[5]) if match[5] is not None else None))
    if len(matches) != 1:
        raise RuntimeError("Missing or ambiguous structured radio tuple")
    return matches[0]


def primary_tx(reply):
    matches = []
    for line in data_lines(reply):
        matches.extend(re.findall(r"(?:^|\s)TX=(\d+),(\d+)(?=\s|;|$)", line))
    if len(matches) != 1:
        raise RuntimeError("Missing or ambiguous per-profile TX counters")
    return int(matches[0][0])


def safety_snapshot(nodes, timeout):
    states = {}
    # Complete all four assertions before permitting ANY R2 setting or KISS TX.
    for name, node in nodes.items():
        guard = node.command("get lab.tx", timeout=timeout)
        require_guard(guard)
        radio = node.command("get radio", timeout=timeout)
        actual = radio_tuple(radio)
        # ESP32's float getter can print 910.5250244 for requested 910.525.
        # Accept only this sub-50 Hz representation error, never a nearby RF
        # channel or any bandwidth/SF/coding-rate change.
        if abs(actual[0] - PRIMARY[0]) > Decimal("0.00005") or actual[1:4] != PRIMARY[1:4]:
            raise RuntimeError("Refusing TX/configuration: R1 must remain exactly 910.525,62.5,7,5")
        states[name] = {"lab_tx": guard, "primary_radio": radio}
    return states


def require_ok(reply):
    if re.search(r"\b(?:error|err)\b", reply, re.IGNORECASE) or not re.search(r"\bOK\b", reply):
        raise RuntimeError("Radio2 configuration did not complete successfully: " + reply)


def measurement(probe, elapsed):
    transfers = probe.get("transfers", [])
    if not transfers:
        raise RuntimeError("Probe returned no frame evidence")
    received = [item for item in transfers if item["received"]]
    received_bytes = sum(item["size"] for item in received)
    payload_bytes = sum(max(0, item["size"] - 14) for item in received)
    return dict(elapsed_seconds=round(elapsed, 6), frames_attempted=len(transfers),
                frames_received=len(received), frames_lost=len(transfers) - len(received),
                loss_fraction=1 - len(received) / len(transfers),
                ethernet_bytes_received=received_bytes,
                ethernet_goodput_bps=received_bytes * 8 / elapsed if elapsed else 0,
                ethernet_payload_goodput_bps=payload_bytes * 8 / elapsed if elapsed else 0,
                goodput_definition="received Ethernet bytes (payload excludes 14-byte MAC header), both directions; not SSH application throughput")


def run_sweep(a, b, endpoint_a, endpoint_b, candidates, *, count=1, timeout=3,
              interval=0.1, command_timeout=12, endpoint_factory=KissEndpoint,
              probe_function=run_probe, persist=lambda report: None):
    if not candidates or any(case not in CANDIDATES for case in candidates):
        raise ValueError("Select only predefined 909.5 MHz cases")
    nodes = {"a": a, "b": b}
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(), cases=[], aborted=False,
                  primary_usa=list(map(str, PRIMARY)), primary_nominal_kbps=PRIMARY_KBPS,
                  probe_parameters=dict(count=count, timeout=timeout, interval=interval,
                                        command_timeout=command_timeout, duplex=False, retries=0),
                  usb={name: node.identity for name, node in nodes.items()},
                  note="No USA TX. Primary configuration/counters only; no claim of actual USA receive reliability. Raw Ethernet probe, not SSH.")
    try:
        report["initial_safety"] = safety_snapshot(nodes, command_timeout)
        for case in candidates:
            item = dict(candidate=case.describe(), passed=False)
            report["cases"].append(item)
            endpoints = []
            try:
                item["safety_before_configuration"] = safety_snapshot(nodes, command_timeout)
                before = {}
                for name, node in nodes.items():
                    status = node.command("get radio2.status", timeout=command_timeout)
                    before[name] = primary_tx(status)
                    if before[name] != 0:
                        raise RuntimeError("Primary TX counter is nonzero; refusing any further test TX")
                item["primary_tx_before"] = before
                # Previous case's endpoints were closed in finally before any
                # new tuple. Explicit off releases only the Ethernet R2 lease.
                for node in nodes.values():
                    for command in ("set ethernet off", "set ethernet.radio " + case.tuple_text,
                                    "set ethernet on"):
                        require_ok(node.command(command, timeout=command_timeout))
                states = {}
                for name, node in nodes.items():
                    tuple_reply = node.command("get ethernet.radio", timeout=command_timeout)
                    expected = (Decimal("909.5"), Decimal(str(case.bandwidth)), case.sf, 5, case.preamble)
                    if radio_tuple(tuple_reply) != expected:
                        raise RuntimeError("Ethernet radio2 does not match the selected tuple/preamble")
                    states[name] = {"ethernet_radio": tuple_reply,
                        "timing": node.command("get radio2.timing", timeout=command_timeout),
                        "status": node.command("get radio2.status", timeout=command_timeout),
                        "ethernet": node.command("get ethernet", timeout=command_timeout)}
                    if "ethernet=on; active (RX time-shared)" not in states[name]["ethernet"]:
                        raise RuntimeError("Ethernet radio2 is not active on station WiFi")
                    if primary_tx(states[name]["status"]) != before[name]:
                        raise RuntimeError("Primary TX counter changed during radio2 setup")
                item["configured"] = states
                item["safety_before_tx"] = safety_snapshot(nodes, command_timeout)
                endpoints.append(endpoint_factory(endpoint_a))
                endpoints.append(endpoint_factory(endpoint_b))
                started = time.monotonic()
                probe = probe_function(*endpoints, count=count, timeout=timeout, interval=interval, duplex=False)
                elapsed = time.monotonic() - started
                # Replace the generic helper's old fixed SF5/BW500 label with
                # the exact PHY verified above; preserve all original frames.
                probe["baseline"] = "verified R2 " + case.tuple_text + "; R1 USA RX-only"
                item["probe"] = probe
                item["measurement"] = measurement(probe, elapsed)
                item["host_diagnostics"] = {name: dict(decode_errors=endpoint.decoder.errors,
                    queue_overflows=endpoint.overflow, error=endpoint.error)
                    for name, endpoint in zip(("a", "b"), endpoints)}
            finally:
                for endpoint in endpoints:
                    endpoint.close()
            item["safety_after_tx"] = safety_snapshot(nodes, command_timeout)
            item["primary_tx_after"] = {}
            for name, node in nodes.items():
                status = node.command("get radio2.status", timeout=command_timeout)
                item["primary_tx_after"][name] = primary_tx(status)
                if item["primary_tx_after"][name] != before[name]:
                    raise RuntimeError("Primary TX counter changed; aborting all remaining cases")
            item["passed"] = bool(item["probe"].get("passed")) and not any(
                value for state in item["host_diagnostics"].values() for value in state.values())
            persist(report)
    except Exception as error:
        report["aborted"] = True
        report["error"] = str(error)
    report["completed"] = not report["aborted"]
    report["passed"] = report["completed"] and all(item["passed"] for item in report["cases"])
    report["commands"] = {name: node.commands for name, node in nodes.items()}
    persist(report)
    return report


def endpoint_valid(endpoint):
    parsed = urlparse(endpoint)
    return (parsed.scheme == "tcp" and bool(parsed.hostname) and parsed.port in (None, 5003)
            and not parsed.username and not parsed.password and parsed.path in ("", "/")
            and not parsed.query and not parsed.fragment)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("a-port", "b-port", "a-endpoint", "b-endpoint"):
        parser.add_argument("--" + name)
    parser.add_argument("--a-ssh", help="optional user@host owning A's USB port")
    parser.add_argument("--a-key", help="absolute local private-key path for --a-ssh")
    parser.add_argument("--a-helper", help="absolute remote ethernet_serial_rpc.py path")
    parser.add_argument("--cases", default="1,2,3,4,5,6,7", help="comma-separated predefined case indexes")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=3)
    parser.add_argument("--command-timeout", type=float, default=12)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--plan", action="store_true", help="print candidates without opening any port/socket")
    args = parser.parse_args(argv)
    try:
        indexes = [int(value) for value in args.cases.split(",")]
        if len(set(indexes)) != len(indexes):
            raise ValueError("duplicate cases")
        cases = [next(case for case in CANDIDATES if case.index == index) for index in indexes]
    except (ValueError, StopIteration):
        parser.error(f"--cases must select unique indexes from 1..{len(CANDIDATES)}")
    if not 1 <= args.count <= 4 or not 0 < args.timeout <= 15 or not 0 < args.command_timeout <= 30 \
            or not 0 <= args.interval <= 5 or not all(map(math.isfinite, (args.timeout, args.command_timeout, args.interval))):
        parser.error("count 1..4, timeout (0,15], command-timeout (0,30], interval [0,5] required")
    if args.plan:
        print(json.dumps({"candidates": [case.describe() for case in cases], "required_guard": GUARD,
                          "primary_rx_only": "910.525,62.5,7,5"}, indent=2))
        return 0
    if not all((args.a_port, args.b_port, args.a_endpoint, args.b_endpoint, args.report_dir)):
        parser.error("both USB ports, both TCP endpoints and an absolute --report-dir are required")
    if any((args.a_ssh, args.a_key, args.a_helper)):
        if not all((args.a_ssh, args.a_key, args.a_helper)):
            parser.error("--a-ssh, --a-key and --a-helper must be supplied together")
        try:
            ssh_command(args.a_ssh, args.a_key, args.a_helper, args.a_port)
        except (ValueError, TypeError) as error:
            parser.error(str(error))
    try:
        valid_endpoints = endpoint_valid(args.a_endpoint) and endpoint_valid(args.b_endpoint)
    except ValueError:
        valid_endpoints = False
    if not args.report_dir.is_absolute() or not valid_endpoints or args.a_endpoint == args.b_endpoint \
            or (not args.a_ssh and os.path.realpath(args.a_port) == os.path.realpath(args.b_port)):
        parser.error("use an absolute report directory and distinct USB ports/TCP port-5003 endpoints")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="ethernet-r2-sweep-", dir=args.report_dir))

    def persist(report):
        output = json.dumps(report, indent=2) + "\n"
        temporary = folder / "summary.json.tmp"
        temporary.write_text(output, encoding="utf-8")
        temporary.replace(folder / "summary.json")
        for item in report["cases"]:
            (folder / f"case-{item['candidate']['index']}.json").write_text(json.dumps(item, indent=2) + "\n", encoding="utf-8")

    a = b = None
    try:
        a = (SshSerialNode(args.a_ssh, args.a_key, args.a_helper, args.a_port)
             if args.a_ssh else SerialNode(args.a_port))
        b = SerialNode(args.b_port)
        report = run_sweep(a, b, args.a_endpoint, args.b_endpoint, cases, count=args.count,
                           timeout=args.timeout, interval=args.interval, command_timeout=args.command_timeout,
                           persist=persist)
    except Exception as error:
        report = dict(passed=False, aborted=True, cases=[], error=str(error))
        persist(report)
    finally:
        for node in (b, a):
            if node:
                node.close()
    print(json.dumps({"report": str(folder / "summary.json"), "passed": report["passed"],
                      "aborted": report["aborted"], "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
