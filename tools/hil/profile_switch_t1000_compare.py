#!/usr/bin/env python3
"""Compare four rapid RX hops per direction with a longer T1000-E switch run."""

import argparse
import json
import time

import serial


def read_reply(port, key, timeout, sequence=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline().decode("utf-8", errors="replace").strip()
        if not line.startswith("{"):
            continue
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        if key in reply and (sequence is None or reply[key] == sequence):
            return reply
    raise TimeoutError(f"No {key} reply from {port.port}")


def aggregate(result, prefix):
    sides = [result[f"{prefix}to_primary"], result[f"{prefix}to_secondary"]]
    count = sum(side["n"] for side in sides)
    return {
        "n": count,
        "mean_us": sum(side["n"] * side["mean_us"] for side in sides) / count,
        "max_us": max(side["max_us"] for side in sides),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--mode", choices=("mod", "freq", "both", "preamble"), default="both")
    args = parser.parse_args()
    if not 16 <= args.count <= 1000 or not 1 <= args.rounds <= 20:
        parser.error("count must be 16..1000 and rounds 1..20")

    port = serial.Serial()
    port.port = args.port
    port.baudrate = 115200
    port.timeout = 0.25
    port.write_timeout = 2
    port.rts = False
    port.dtr = True
    port.open()
    try:
        port.reset_input_buffer()
        port.write(b"info\n")
        info = read_reply(port, "bench", 5)
        if info.get("bench") != "t1000-lr1110-production-retune-v1" or not info.get("ready"):
            raise RuntimeError(f"Wrong or unready device: {info}")
        print(json.dumps(info), flush=True)
        last_sequence = info.get("last_sequence")
        if not isinstance(last_sequence, int) or not 0 <= last_sequence <= 0xFFFFFFFF:
            raise RuntimeError("Bench firmware must report its last_sequence; rebuild and flash it")
        if last_sequence + args.rounds > 0xFFFFFFFF:
            raise RuntimeError("Bench run sequence exhausted; reboot the bench firmware")
        trials = []
        for round_number in range(1, args.rounds + 1):
            sequence = last_sequence + round_number
            port.write(f"run {args.mode} {args.count} {sequence}\n".encode("ascii"))
            try:
                result = read_reply(port, "result", args.count * 0.05 + 20, sequence)
            except TimeoutError:
                port.write(f"result result {sequence}\n".encode("ascii"))
                result = read_reply(port, "result", 5, sequence)
            spot = aggregate(result, "spot_")
            long = aggregate(result, "")
            if (spot["n"] != 8 or long["n"] != args.count or not result["restored"]
                    or any(result[k] for k in ("failures", "busy_timeouts", "rx_errors", "cache_errors", "rssi_errors"))):
                raise RuntimeError(f"Trial {round_number} failed validation: {result}")
            trial = {
                "round": round_number,
                "mode": args.mode,
                "spot": spot,
                "spot_budget_us": result["spot_budget_us"],
                "long": long,
                "long_budget_us": result["long_budget_us"],
                "spot_budget_covers_long_max": result["spot_budget_us"] >= long["max_us"],
            }
            trials.append(trial)
            print(json.dumps(trial), flush=True)
        summary = {
            "trials": len(trials),
            "total_long_hops": sum(t["long"]["n"] for t in trials),
            "spot_mean_us": sum(t["spot"]["mean_us"] for t in trials) / len(trials),
            "spot_max_us": max(t["spot"]["max_us"] for t in trials),
            "long_mean_us": sum(t["long"]["mean_us"] for t in trials) / len(trials),
            "long_max_us": max(t["long"]["max_us"] for t in trials),
            "spot_budget_covered_every_trial": all(t["spot_budget_covers_long_max"] for t in trials),
        }
        print(json.dumps({"summary": summary}), flush=True)
    finally:
        port.close()


if __name__ == "__main__":
    main()
