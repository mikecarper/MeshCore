"""Bounded SF5/250 four-channel dwell sweep; leave HIL firmware installed.

Stop each dwell at its first RF miss, increase 0.5 symbols, and stop the entire
sweep at the first 400/400 result or after 8.1 symbols. Fixture failures abort.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import serial
from profile_switch import configure_session


DWELLS = tuple(value / 10 for value in range(41, 82, 5))


def dwell_levels(run, checkpoint):
    result = dict(complete=False, first_perfect_dwell=None, levels=[])
    for dwell in DWELLS:
        capture = run(dwell)
        if not capture.get("complete") or capture.get("stopped") not in (
                "first_rf_miss", "channel_limit_without_miss"):
            raise RuntimeError("Fixture/incomplete result; do not increase dwell")
        level = capture["levels"][0]
        if len(capture["levels"]) != 1 or level["channels"] != 4:
            raise RuntimeError("Unexpected channel count")
        perfect = capture["stopped"] == "channel_limit_without_miss"
        if perfect and (level["received"] != 400 or level["attempted"] != 400
                        or len(level["per_channel"]) != 4 or any(
                c != {"attempted": 100, "received": 100} for c in level["per_channel"])):
            raise RuntimeError("Incomplete per-channel perfect result")
        result["levels"].append(dict(dwell_symbols=dwell, attempted=level["attempted"],
                                     received=level["received"], perfect=perfect,
                                     per_channel=level["per_channel"], status=level["status"]))
        checkpoint(result)
        if perfect:
            result.update(complete=True, first_perfect_dwell=dwell, stopped="first_perfect_dwell")
            checkpoint(result)
            return result
    result.update(complete=True, stopped="8p1_limit_with_misses")
    checkpoint(result)
    return result


def reboot_boards(names):
    for name in names:
        port = serial.Serial()
        port.port, port.baudrate, port.timeout, port.write_timeout = name, 115200, .25, 2
        configure_session(port)
        try:
            port.open()
            port.write(b"reboot\n")
            port.flush()
        finally:
            port.close()
    time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.receiver == args.sender or args.output.exists():
        parser.error("different ports and fresh output required")
    paths = {d: args.output.with_name(args.output.stem + "_" + str(d).replace(".", "p") + ".json")
             for d in DWELLS}
    if any(p.exists() for p in paths.values()):
        parser.error("a per-dwell result already exists; choose a fresh output prefix")
    result = dict(experiment="sf5_250_dwell_sweep", receiver=args.receiver, sender=args.sender,
                  sf=5, bw_khz=250, channels=4, preamble_symbols=32, samples_per_channel=100,
                  power_dbm=-9, expected_rx_gain_reg=0x94, modulation_cache=True,
                  trace_enabled=False, dwells=DWELLS, complete=False, captures=[])

    def save(progress=None):
        if progress:
            result.update(progress)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    def run(dwell):
        reboot_boards((args.receiver, args.sender))
        command = [sys.executable, str(Path(__file__).with_name("profile_switch_channels.py")),
                   "--receiver", args.receiver, "--sender", args.sender, "--output", str(paths[dwell]),
                   "--samples", "100", "--preamble", "32", "--max-channels", "4",
                   "--sf", "5", "--bw-khz", "250", "--dwell-symbols", str(dwell),
                   "--modulation-cache", "on", "--expect-rx-gain", "normal"]
        completed = subprocess.run(command, check=False)
        result["captures"].append(dict(dwell_symbols=dwell, path=paths[dwell].name,
                                       exit_code=completed.returncode))
        save()
        if completed.returncode:
            raise RuntimeError("Collector failed; stopping sweep without another RF probe")
        return json.loads(paths[dwell].read_text(encoding="utf-8"))

    try:
        save()
        dwell_levels(run, save)
    except Exception as error:
        result.update(complete=False, stopped="fixture_error", error=str(error))
        raise
    finally:
        try:
            reboot_boards((args.receiver, args.sender))
        except Exception as error:
            result["cleanup_error"] = str(error)
        save()


if __name__ == "__main__":
    main()
