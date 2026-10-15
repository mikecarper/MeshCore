#!/usr/bin/env python3
"""Measure real SSH payload speed versus R2 preamble, never nominal bitrate.

R2 is fixed to 909.5/CR5, with explicit BW125 or500 (default500) and SF5..9
(default SF5); R1 must already be USA receive-only under
the immutable HIL TX lock. This tool never changes R1, WiFi, keys or TX power.
The runner owns its KISS/TAP/SSH resources and must close them before returning;
it receives one JSON request on stdin and returns one JSON object on stdout.
LORA_SSH_BENCH_CONFIG is inherited as a local configuration-file path. No
automatic retries: explicitly repeat a preamble or use --runs for replication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time

from ethernet_primary_probe import SerialNode
from ethernet_ssh_serial import SshSerialNode, ssh_command
from ethernet_radio2_sweep import (GUARD, PRIMARY, data_lines, primary_tx,
                                   radio_tuple, require_ok, safety_snapshot)

DEFAULT_PREAMBLES = (128, 192, 256, 384, 512, 768)


def validate_preambles(values):
    values = list(values)
    if not values or len(values) > 120 or any(type(n) is not int or not 32 <= n <= 2048 for n in values):
        raise ValueError("Select 1..120 explicit preambles, each 32..2048 symbols")
    return values


def validate_sf(sf):
    if type(sf) is not int or sf not in range(5, 10):
        raise ValueError("Select a fixed spreading factor from 5..9")
    return sf


def validate_bandwidth(bandwidth):
    if type(bandwidth) is not int or bandwidth not in (125, 500):
        raise ValueError("Select a fixed bandwidth of 125 or 500 kHz")
    return bandwidth


def paired_counters(reply, field):
    matches = re.findall(r"(?:^|\s)" + field + r"=(\d+),(\d+)(?=\s|;|$)",
                         "\n".join(data_lines(reply)))
    return list(map(int, matches[0])) if len(matches) == 1 else None


def ethernet_counters(reply):
    matches = re.findall(r"\bRX=(\d+) TX=(\d+) fail=(\d+)(?=\s|;|$)",
                         "\n".join(data_lines(reply)))
    return dict(zip(("rx", "tx", "fail"), map(int, matches[0]))) if len(matches) == 1 else None


def snapshot(nodes, timeout, preamble=None, *, sf=5, bandwidth=500):
    validate_sf(sf)
    validate_bandwidth(bandwidth)
    states = safety_snapshot(nodes, timeout)
    for name, node in nodes.items():
        state = states[name]
        for key, command in (("status", "get radio2.status"), ("ethernet", "get ethernet"),
                             ("ethernet_radio", "get ethernet.radio"),
                             ("timing", "get radio2.timing"), ("radio_diag", "stats-radio-diag")):
            state[key] = node.command(command, timeout=timeout)
        if primary_tx(state["status"]) != 0:
            raise RuntimeError("Primary TX counter is nonzero; aborting all remaining cases")
        if preamble is not None:
            if radio_tuple(state["ethernet_radio"]) != (Decimal("909.5"), Decimal(bandwidth), sf, 5, preamble):
                raise RuntimeError(f"Ethernet readback does not match 909.5,{bandwidth},{sf},5 and selected preamble")
            if "ethernet=on; active (RX time-shared)" not in state["ethernet"]:
                raise RuntimeError("Ethernet R2 is not active on station WiFi")
        state["counters"] = dict(primary_tx=0, profile_tx=paired_counters(state["status"], "TX"),
                                  profile_rx=paired_counters(state["status"], "RX"),
                                  ethernet=ethernet_counters(state["ethernet"]))
    return states


def counter_delta(before, after):
    """Unknown/reset counters stay unknown, never fabricated as zero loss."""
    if isinstance(before, dict) and isinstance(after, dict):
        return {key: counter_delta(value, after.get(key)) for key, value in before.items()}
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [counter_delta(a, b) for a, b in zip(before, after)]
    if type(before) is int and type(after) is int and after >= before:
        return after - before
    return None


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def payload_metrics(result):
    verified = result.get("verified") is True
    total_bytes = total_seconds = 0
    directions = {}
    for name in ("downlink", "uplink"):
        value = result.get(name)
        metrics = None
        if isinstance(value, dict):
            size = value.get("bytes")
            seconds = value.get("elapsed_seconds")
            if seconds is None and number(value.get("elapsed_ms")):
                seconds = value["elapsed_ms"] / 1000
            if type(size) is int and size > 0 and number(seconds) and seconds > 0:
                metrics = dict(bytes=size, elapsed_seconds=seconds, goodput_kbps=size * 8 / seconds / 1000)
                total_bytes += size
                total_seconds += seconds
            if value.get("hash_verified") is False:
                verified = False
        directions[name] = metrics
    # A boolean alone is not byte/time evidence; require both verified hashes
    # and actual payload durations before claiming a successful benchmark.
    verified = verified and all(directions.values()) and all(
        isinstance(result.get(name), dict) and result[name].get("hash_verified") is True
        for name in ("downlink", "uplink"))
    combined = total_bytes * 8 / total_seconds / 1000 if all(directions.values()) else None
    total_ms = result.get("total_ms")
    end_to_end = total_bytes * 8 / total_ms if combined is not None and number(total_ms) and total_ms > 0 else None
    return dict(verified=bool(verified), directions=directions, combined_payload_kbps=combined,
                end_to_end_kbps=end_to_end, handshake_ms=result.get("handshake_ms"),
                tcp_client=result.get("tcp_client"), tcp_server=result.get("tcp_server"),
                definition="Combined verified SSH payload bytes / sum of downlink and uplink durations; excludes handshake. End-to-end uses runner total_ms.")


def python_runner(path, timeout=240):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.suffix.lower() != ".py":
        raise ValueError("--runner must be an existing absolute local Python script")
    if not number(timeout) or not 1 <= timeout <= 900:
        raise ValueError("runner timeout must be finite, 1..900 seconds")

    def run(request):
        completed = subprocess.run([sys.executable, str(path)], input=json.dumps(request),
            text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, shell=False, check=False)
        # Stdout is a machine-readable, single JSON object. Do not copy stderr
        # into public reports: a misconfigured external SSH tool may log secrets.
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or type(result.get("verified")) is not bool:
            raise ValueError("Runner must return one JSON object with boolean verified")
        if completed.returncode:
            result["verified"] = False
            result["exit_code"] = completed.returncode
            result.setdefault("error", "Runner exited unsuccessfully")
        return result
    return run


def run_cases(nodes, preambles, runner, *, sf=5, bandwidth=500, command_timeout=12,
              persist=lambda report: None, progress=lambda event: None):
    preambles = validate_preambles(preambles)
    sf = validate_sf(sf)
    bandwidth = validate_bandwidth(bandwidth)
    if len(nodes) != 2 or len({id(node) for node in nodes.values()}) != 2:
        raise ValueError("Exactly two distinct CLI nodes are required")
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(), cases=[], aborted=False,
        primary_rx_only=list(map(str, PRIMARY)), required_guard=GUARD,
        usb={name: node.identity for name, node in nodes.items()}, retries=0,
        sf=sf, bandwidth_khz=bandwidth,
        note=f"Only R2 transmits, at 909.5/{bandwidth}/SF{sf}/CR5. No claim of actual USA receive reliability.")

    def checkpoint(item, phase):
        item["phase"] = phase
        persist(report)
        progress(dict(index=item["index"], preamble=item["preamble"], sf=sf, bandwidth_khz=bandwidth, phase=phase,
                      verified=item.get("verified", False), error=item.get("error")))

    try:
        report["initial_safety"] = snapshot(nodes, command_timeout, sf=sf, bandwidth=bandwidth)
        for index, preamble in enumerate(preambles, 1):
            item = dict(index=index, case_id=f"case-{index:03d}-p{preamble}", preamble=preamble, sf=sf,
                        bandwidth_khz=bandwidth, verified=False, preamble_ms=preamble * (2 ** sf) / bandwidth)
            report["cases"].append(item)
            checkpoint(item, "checking")
            item["before_configuration"] = snapshot(nodes, command_timeout, sf=sf, bandwidth=bandwidth)
            for node in nodes.values():
                for command in ("set ethernet off", f"set ethernet.radio 909.5,{bandwidth},{sf},5,{preamble}",
                                "set ethernet on"):
                    require_ok(node.command(command, timeout=command_timeout))
            item["before"] = snapshot(nodes, command_timeout, preamble, sf=sf, bandwidth=bandwidth)
            request = dict(index=index, case_id=item["case_id"], preamble=preamble,
                           frequency_mhz=909.5, bandwidth_khz=bandwidth, sf=sf, cr=5)
            item["request"] = request
            checkpoint(item, "running")
            started = time.monotonic()
            runner_failed = False
            try:
                result = runner(request)
                if not isinstance(result, dict) or type(result.get("verified")) is not bool:
                    raise ValueError("Runner did not return boolean verified")
                item["runner"] = result
                item["metrics"] = payload_metrics(result)
                item["verified"] = item["metrics"]["verified"]
                if not item["verified"]:
                    item["error"] = str(result.get("error", "Payload integrity/SSH run was not verified"))
                    runner_failed = True
            except Exception as error:
                runner_failed = True
                item["error"] = ("Runner timed out" if isinstance(error, subprocess.TimeoutExpired)
                                 else f"Runner failed: {type(error).__name__}")
            item["runner_wall_seconds"] = time.monotonic() - started
            # Timeout kills the owned runner. Explicit off also invalidates its
            # KISS clients before another tuple is configured; no reconnects.
            if runner_failed:
                for node in nodes.values():
                    require_ok(node.command("set ethernet off", timeout=command_timeout))
            item["after"] = snapshot(nodes, command_timeout, None if runner_failed else preamble, sf=sf, bandwidth=bandwidth)
            item["counter_deltas"] = {name: counter_delta(item["before"][name]["counters"],
                item["after"][name]["counters"]) for name in nodes}
            checkpoint(item, "complete")
    except Exception as error:
        report["aborted"] = True
        report["error"] = str(error)
        if report["cases"]:
            checkpoint(report["cases"][-1], "aborted")
    report["completed"] = not report["aborted"]
    eligible = [item for item in report["cases"] if item.get("phase") == "complete"
                and item["verified"] and item.get("metrics", {}).get("combined_payload_kbps") is not None]
    report["ranking"] = [dict(index=item["index"], preamble=item["preamble"], sf=item["sf"],
        bandwidth_khz=item["bandwidth_khz"],
        combined_payload_kbps=item["metrics"]["combined_payload_kbps"],
        downlink_kbps=item["metrics"]["directions"]["downlink"]["goodput_kbps"],
        end_to_end_kbps=item["metrics"]["end_to_end_kbps"])
        for item in sorted(eligible, key=lambda value: value["metrics"]["combined_payload_kbps"], reverse=True)]
    report["passed"] = report["completed"] and all(item["verified"] for item in report["cases"])
    report["commands"] = {name: node.commands for name, node in nodes.items()}
    persist(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("a-port", "b-port", "a-ssh", "a-key", "a-helper"):
        parser.add_argument("--" + name)
    parser.add_argument("--preambles", default=",".join(map(str, DEFAULT_PREAMBLES)))
    parser.add_argument("--sf", type=int, choices=range(5, 10), default=5,
                        help="fixed spreading factor (5..9), never changes R1")
    parser.add_argument("--bandwidth", type=int, choices=(125, 500), default=500,
                        help="fixed R2 bandwidth in kHz (125 or 500), never changes R1")
    parser.add_argument("--runs", type=int, default=1, help="explicit repetitions, no hidden retry")
    parser.add_argument("--seed", type=int, help="optional reproducible shuffle of each run")
    parser.add_argument("--runner", type=Path, help="absolute local Python runner; JSON stdin/stdout")
    parser.add_argument("--runner-timeout", type=float, default=240)
    parser.add_argument("--command-timeout", type=float, default=12)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--plan", action="store_true", help="no port/socket/process/report I/O")
    args = parser.parse_args(argv)
    try:
        selected = validate_preambles([int(value) for value in args.preambles.split(",")])
        if not 1 <= args.runs <= 10 or len(selected) * args.runs > 120:
            raise ValueError("runs 1..10 and at most 120 total cases required")
        if not number(args.command_timeout) or not 0 < args.command_timeout <= 30:
            raise ValueError("command timeout must be finite, (0,30] seconds")
        if not number(args.runner_timeout) or not 1 <= args.runner_timeout <= 900:
            raise ValueError("runner timeout must be finite, 1..900 seconds")
    except ValueError as error:
        parser.error(str(error))
    rng = random.Random(args.seed)
    preambles = []
    for _ in range(args.runs):
        one_run = selected.copy()
        if args.seed is not None:
            rng.shuffle(one_run)
        preambles.extend(one_run)
    if args.plan:
        print(json.dumps(dict(preambles=preambles, sf=args.sf, bandwidth_khz=args.bandwidth,
                              radio=f"909.5,{args.bandwidth},{args.sf},5", primary_rx_only="910.525,62.5,7,5",
                              required_guard=GUARD, runner_timeout=args.runner_timeout), indent=2))
        return 0
    if not args.a_port or not args.b_port or not args.report_dir or not args.report_dir.is_absolute():
        parser.error("both CLI ports and absolute --report-dir required")
    try:
        if any((args.a_ssh, args.a_key, args.a_helper)):
            if not all((args.a_ssh, args.a_key, args.a_helper)):
                raise ValueError("--a-ssh, --a-key, --a-helper must be supplied together")
            ssh_command(args.a_ssh, args.a_key, args.a_helper, args.a_port)
        elif os.path.realpath(args.a_port) == os.path.realpath(args.b_port):
            raise ValueError("CLI ports must be distinct")
        runner = python_runner(args.runner, args.runner_timeout)
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    args.report_dir.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="ethernet-preamble-", dir=args.report_dir))

    def persist(report):
        for name, value in [("summary", report)] + [(item["case_id"], item) for item in report["cases"]]:
            temporary = folder / (name + ".json.tmp")
            temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            temporary.replace(folder / (name + ".json"))

    a = b = None
    try:
        a = (SshSerialNode(args.a_ssh, args.a_key, args.a_helper, args.a_port)
             if args.a_ssh else SerialNode(args.a_port))
        b = SerialNode(args.b_port)
        report = run_cases(dict(a=a, b=b), preambles, runner, sf=args.sf, bandwidth=args.bandwidth,
            command_timeout=args.command_timeout,
            persist=persist, progress=lambda event: print(json.dumps(event), flush=True))
    except Exception as error:
        report = dict(passed=False, aborted=True, cases=[], error=str(error))
        persist(report)
    finally:
        for node in (b, a):
            if node:
                node.close()
    print(json.dumps(dict(report=str(folder / "summary.json"), passed=report["passed"],
                         aborted=report["aborted"], ranking=report.get("ranking", []))))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
