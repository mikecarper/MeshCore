#!/usr/bin/env python3
"""Collect RX-only, RAM-only production-wrapper timing trials over USB."""
import argparse
import json
import itertools
import secrets
from pathlib import Path
import time

import serial
from serial.tools import list_ports

_run_sequences = itertools.count(secrets.randbelow(0x3fffffff) + 1)


def configure_session(port):
    # Native Espressif/Seeed/Adafruit USB and a CH340 bridge differ in DTR semantics.
    # DTR keeps the native session active; on a bridge it can drive BOOT/GPIO0.
    port.rts = False
    port.dtr = any(info.vid in (0x303A, 0x2886, 0x239A) and info.device.casefold() == str(port.port).casefold()
                   for info in list_ports.comports())


def read_response(port, key, timeout, expected=None):
    deadline = time.monotonic() + timeout
    pending = bytearray()
    stale = []
    while time.monotonic() < deadline:
        chunk = port.readline()
        pending.extend(chunk)
        if not pending.endswith(b"\n"):
            continue  # Serial timeout can split one logical JSON line.
        line = pending.decode("utf-8", errors="replace").strip()
        pending.clear()
        if not line.startswith("{"):
            continue
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        if key in reply:
            if expected is not None and reply[key] != expected:
                stale.append(reply)
                continue
            if stale:
                reply["stale_replies"] = stale
            return reply
    raise TimeoutError(f"No complete {key} response on {port.port}")


def read_packet_response(port, key, sequence, timeout):
    try:
        return read_response(port, key, timeout, sequence)
    except (TimeoutError, json.JSONDecodeError):
        # Replay an already recorded result, never the radio operation.
        port.write(f"result {key} {sequence}\n".encode("ascii"))
        result = read_response(port, key, 3, sequence)
        result["transport_recovered"] = True
        return result


def exchange(port, command, key, timeout, expected=None, cached=False):
    if key == "result" and expected is None and command.startswith("run "):
        expected = next(_run_sequences)
        command += f" {expected}"
        cached = True
    port.write((command + "\n").encode("ascii"))
    if cached:
        if expected is None:
            raise ValueError("Cached packet replies require a sequence")
        return read_packet_response(port, key, expected, timeout)
    return read_response(port, key, timeout, expected)


def exchange_ready(port, command, key, timeout, expected=None, cached=False):
    # These two replies occur before the measured run/listener is armed when
    # the normal packet guard refuses initial profile setup. Never retry RF
    # packet loss or a timed hop failure as though it had succeeded.
    for deferred in range(31):
        try:
            result = exchange(port, command, key, timeout, expected, cached)
            result["setup_deferrals"] = deferred
            return result
        except RuntimeError as error:
            if str(error) not in ("primary rejected", "receiver primary rejected") or deferred == 30:
                raise
            time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--count", type=int, default=512)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--experiment", choices=("batching", "warm"), default="batching")
    args = parser.parse_args()
    if not 16 <= args.count <= 1000 or not 1 <= args.rounds <= 10:
        parser.error("count must be 16..1000 and rounds 1..10")
    if args.output.exists():
        parser.error("output already exists; choose a fresh result path")
    results = {"schema": 2, "experiment": args.experiment,
               "board": "XIAO ESP32-S3 + WIO SX1262", "port": args.port,
               "primary": {"freq": 909.5, "bw": 62.5, "sf": 7, "cr": 5},
               "secondary": {"freq": 910.5, "bw": 500, "cr": 5},
               "timing": "production tuneProfile entry to return, then BUSY low; RX status verified afterward",
               "excluded": "first 8 successful hops per trial; no application/UI/network scheduling",
               "trials": [], "complete": False}
    port = serial.Serial()
    port.port = args.port
    port.baudrate = 115200
    port.timeout = 0.25
    port.write_timeout = 2
    configure_session(port)
    try:
        port.open()
        port.reset_input_buffer()
        info = exchange(port, "info", "bench", 5)
        if info.get("bench") != "production-profile-switch-v8" or not info.get("ready"):
            raise RuntimeError(f"Wrong or unready firmware: {info}")
        for repeat in range(args.rounds):
            for sf in (7, 8, 9):
                for choice in ((0, 1) if repeat % 2 == 0 else (1, 0)):
                    warm, batched = (1, choice) if args.experiment == "batching" else (choice, 0)
                    result = exchange(port, f"run {warm} {sf} {args.count} {batched} 0 2", "result", 35)
                    result["round"] = repeat + 1
                    results["trials"].append(result)
                    print(json.dumps(result), flush=True)
                    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
                    count = sum(d["total"]["n"] for d in result["directions"])
                    if (count != args.count or result["warm"] != bool(warm)
                            or result["batched"] != bool(batched) or result["sf500"] != sf
                            or any(result[k] for k in ("failures", "busy_timeouts", "rx_mode_errors", "cache_errors"))
                            or not result["off_restored_rc"]):
                        raise RuntimeError("Trial failed validation; partial results saved")
        results["complete"] = True
    finally:
        port.close()
        if results["trials"]:
            args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
