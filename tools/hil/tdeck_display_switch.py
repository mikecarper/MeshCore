#!/usr/bin/env python3
"""Collect receive-only T-Deck display/radio scheduler A/B measurements.

Flash env:tdeck_display_switch first. This tool does not build, erase, flash,
reset, or transmit radio packets. Example:
  python tools/hil/tdeck_display_switch.py COM10 --seconds 5 --repeats 3 \
      --output tdeck-display-results.json

Modes: 0 static screen; 1 original direct redraw; 2 buffered strips with radio
service callbacks. SPI policies: original bytewise 2 MHz and buffered 8 MHz.
--full-spi-matrix adds the two intermediate policies to separate those effects.
Exact min/mean/max and coarse p95/p99 bucket upper bounds come from firmware.
"""

import argparse
import datetime as dt
import json
from pathlib import Path
import time

import serial
from profile_switch import open_session


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--seconds", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--fps", type=int, choices=range(1, 31), default=20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--full-spi-matrix", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


class Bench:
    def __init__(self, port):
        self.device = serial.Serial()
        self.device.port = port
        self.device.baudrate = 115200
        self.device.timeout = 0.2
        self.device.write_timeout = 2
        # Reuse the native-USB DTR policy and no-reset bridge opener used by
        # the other production-wrapper HIL tools.
        open_session(self.device)
        self.raw_lines = []

    def close(self):
        self.device.close()

    def send(self, command):
        self.device.write((command + "\n").encode("ascii"))
        self.device.flush()

    def receive(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        pending = bytearray()
        while time.monotonic() < deadline:
            pending.extend(self.device.readline())
            if len(pending) > 16384:
                raise RuntimeError("Firmware response exceeded bounded line capture")
            if not pending.endswith(b"\n"):
                continue  # A serial read timeout can split one logical line.
            raw = pending.decode("utf-8", errors="replace").strip()
            pending.clear()
            if not raw:
                continue
            self.raw_lines.append(raw)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "error" in value:
                raise RuntimeError(f"Firmware rejected command: {value['error']}")
            if predicate(value):
                return value
        raise TimeoutError("Timed out waiting for firmware JSON")


def main():
    args = arguments()
    if not 1 <= args.repeats <= 100:
        raise SystemExit("--repeats must be between 1 and 100")
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing report: {args.output}")
    report = {
        "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "port": args.port,
        "seconds_per_run": args.seconds,
        "requested_fps": args.fps,
        "repeats": args.repeats,
        "receive_only": True,
        "results": [],
    }
    bench = Bench(args.port)
    try:
        # Flush any partial input command, then request a fresh self-description.
        bench.send("")
        bench.send("info")
        info = bench.receive(lambda value: value.get("bench") in (
                                 "tdeck-display-radio2-v1", "tdeck-display-radio2-v2")
                             and "ready" in value, 5)
        report["firmware"] = info
        if not info["ready"]:
            raise RuntimeError("Firmware is present but hardware initialization failed")
        policies = [(2, 0), (8, 1)]
        if args.full_spi_matrix:
            policies = [(2, 0), (8, 0), (2, 1), (8, 1)]
        sequence = 1
        for repeat in range(args.repeats):
            # Rotate display order between repeats to reduce run-order bias.
            modes = [0, 1, 2]
            modes = modes[repeat % 3:] + modes[:repeat % 3]
            for mhz, bulk in policies:
                for mode in modes:
                    command = (f"run {mode} {mhz} {bulk} {args.seconds} "
                               f"{args.fps} {sequence}")
                    print(f"{command} (repeat {repeat + 1})", flush=True)
                    bench.send(command)
                    try:
                        result = bench.receive(lambda value: value.get("result") == sequence,
                                               args.seconds + 6)
                    except TimeoutError:
                        # Native USB may lose an output line while the stored
                        # firmware result remains available without rerunning.
                        bench.send(f"result {sequence}")
                        result = bench.receive(lambda value: value.get("result") == sequence, 4)
                    result["repeat"] = repeat + 1
                    report["results"].append(result)
                    print("  switches={switches}, frame max={frame[max_us]} us, "
                          "service gap max={service_gap[max_us]} us, chip errors="
                          "{chip_errors_after}".format(**result), flush=True)
                    sequence += 1
    except BaseException as exc:
        report["collection_error"] = str(exc)
        raise
    finally:
        report["raw_lines"] = bench.raw_lines
        bench.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Saved {len(report['results'])} runs to {args.output}", flush=True)


if __name__ == "__main__":
    main()
