#!/usr/bin/env python3
"""Collect controlled T-Deck retune experiments using the receive-only v2 HIL.

The main suite separates 16 MHz SPI, confirmed-modem caching and scoped retune
priority. The load suite compares priority under a bounded same-core CPU load;
the iq suite compares the IQ workaround cache with a 40-symbol secondary
preamble. Every policy runs with a static screen and buffered redraws.

This tool does not build, flash, reset or transmit packets. Example:
  python tools/hil/tdeck_retune_experiments.py COM10 --suite main \
      --seconds 5 --repeats 3 --fps 20 --output tdeck-retune-main.json
"""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

from tdeck_display_switch import Bench


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--suite", choices=("main", "load", "iq"), default="main")
    parser.add_argument("--spi", nargs="+", type=int, choices=(8, 16), default=[8, 16],
                        help="SPI MHz policies to include (default: 8 16)")
    parser.add_argument("--seconds", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--fps", type=int, choices=range(1, 31), default=20)
    parser.add_argument("--repeats", type=int, choices=range(1, 101), default=3)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def cases(suite):
    # name, MHz, modem cache, IQ cache, scoped priority, synthetic load, preamble
    policies = {
        "main": [
            ("8MHz baseline", 8, 0, 0, 0, 0, 32),
            ("16MHz only", 16, 0, 0, 0, 0, 32),
            ("8MHz modem cache", 8, 1, 0, 0, 0, 32),
            ("8MHz priority 3", 8, 0, 0, 3, 0, 32),
            ("16MHz modem cache", 16, 1, 0, 0, 0, 32),
            ("16MHz modem cache priority 3", 16, 1, 0, 3, 0, 32),
        ],
        "load": [
            ("loaded modem cache priority baseline", 8, 1, 0, 0, 1, 32),
            ("loaded modem cache priority 3", 8, 1, 0, 3, 1, 32),
        ],
        "iq": [
            ("40-symbol IQ cache off", 8, 1, 0, 0, 0, 40),
            ("40-symbol IQ cache on", 8, 1, 1, 0, 0, 40),
        ],
    }[suite]
    return [dict(name=name, display_mode=mode, spi_mhz=mhz,
                 modem_cache=bool(modem), iq_cache=bool(iq),
                 retune_priority=priority, synthetic_load=bool(load),
                 secondary_preamble=preamble)
            for name, mhz, modem, iq, priority, load, preamble in policies
            for mode in (0, 2)]


def command(case, seconds, fps, sequence):
    return (f"trial {case['display_mode']} {case['spi_mhz']} "
            f"{int(case['modem_cache'])} {int(case['iq_cache'])} "
            f"{case['retune_priority']} {int(case['synthetic_load'])} "
            f"{case['secondary_preamble']} {seconds} {fps} {sequence}")


def validate(result, case, firmware, sequence, seconds, fps):
    failures = []
    expected = {key: value for key, value in case.items() if key != "name"}
    expected.update(result=sequence, bench=firmware["bench"], bulk=True,
                    seconds=seconds, requested_fps=fps, busy_timeout=False,
                    rx_mode=1, cache_correct=True, chip_errors_before=0,
                    chip_errors_after=0, switch_failures=0, failed_retunes=0,
                    actual_retune_priority=max(firmware["loop_priority"], case["retune_priority"]))
    if "core" in firmware:
        expected["core"] = firmware["core"]
    for key, wanted in expected.items():
        if (key not in result or result[key] != wanted
                or (isinstance(wanted, bool) and not isinstance(result[key], bool))):
            failures.append(f"{key}: expected {wanted!r}, got {result.get(key)!r}")
    for key in ("original_priority", "final_priority"):
        if result.get(key) != firmware.get("loop_priority"):
            failures.append(f"{key}: expected owner priority {firmware.get('loop_priority')!r}, "
                            f"got {result.get(key)!r}")
    if result.get("original_priority") != result.get("final_priority"):
        failures.append("owner priority was not restored")
    positive = ["switches", "hook_hops", "optimized_rx_resumes", "spi_transfers", "spi_bytes",
                "loop_stack_min_free_bytes"]
    if case["display_mode"]:
        positive.append("frames")
    if case["synthetic_load"]:
        positive.append("stress_rounds")
    if case["modem_cache"]:
        positive.append("modem_query_skips")
    if case["iq_cache"]:
        positive.append("iq_workaround_skips")
    for key in positive:
        value = result.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            failures.append(f"{key}: expected a positive measurement, got {value!r}")
    for key in ("service_gap", "service_duration"):
        timing = result.get(key)
        if not isinstance(timing, dict) or not timing.get("n", 0):
            failures.append(f"{key}: missing timing samples")
        elif not isinstance(timing.get("max_us"), (int, float)):
            failures.append(f"{key}: missing maximum duration")
    return failures


def persist(path, report, bench):
    report["updated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["raw_lines"] = list(bench.raw_lines) if bench else []
    report["passed"] = sum(trial.get("passed") is True for trial in report["trials"])
    report["failed"] = len(report["trials"]) - report["passed"]
    # A complete previous checkpoint survives interruption while writing.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def collect(args):
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing report: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plan = [case for case in cases(args.suite) if case["spi_mhz"] in args.spi]
    if not plan:
        raise SystemExit("No trial cases match the requested suite and SPI clocks")
    report = dict(recorded_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                  suite=args.suite, port=args.port, seconds_per_run=args.seconds,
                  requested_fps=args.fps, repeats=args.repeats, receive_only=True,
                  requested_spis=list(args.spi),
                  priority_scope="retune only", planned_trials=len(plan)*args.repeats,
                  cases=plan, trials=[], results=[])
    bench = None
    persist(args.output, report, bench)
    try:
        bench = Bench(args.port)
        bench.send("")
        bench.send("info")
        firmware = bench.receive(lambda value: isinstance(value.get("bench"), str)
                                 and value["bench"].startswith("tdeck-display-radio2-")
                                 and "ready" in value, 5)
        report["firmware"] = firmware
        if not firmware.get("ready") or not firmware.get("command", "").startswith("trial "):
            raise RuntimeError("A ready T-Deck retune-experiment firmware is required")
        if args.suite == "load" and not firmware.get("stress_task_ready"):
            raise RuntimeError("The same-core synthetic-load task is unavailable")
        if not isinstance(firmware.get("loop_priority"), int):
            raise RuntimeError("Firmware did not report its original owner priority")
        persist(args.output, report, bench)
        sequence = 1
        for repeat in range(args.repeats):
            # Rotate policy order, then reverse alternate rounds to reduce
            # systematic ordering effects without changing the controlled cases.
            offset = (repeat * 2) % len(plan)
            ordered = plan[offset:] + plan[:offset]
            if repeat % 2:
                ordered = list(reversed(ordered))
            for case in ordered:
                text = command(case, args.seconds, args.fps, sequence)
                trial = dict(sequence=sequence, repeat=repeat+1, case=case,
                             command=text, passed=False, validation_errors=[])
                print(f"[{repeat+1}/{args.repeats}] {case['name']} "
                      f"display={case['display_mode']}: {text}", flush=True)
                try:
                    bench.send(text)
                    try:
                        result = bench.receive(lambda value: value.get("result") == sequence,
                                               args.seconds + 6)
                    except TimeoutError:
                        trial["retried_retained_result"] = True
                        bench.send(f"result {sequence}")
                        result = bench.receive(lambda value: value.get("result") == sequence, 4)
                    trial["result"] = result
                    trial["validation_errors"] = validate(
                        result, case, firmware, sequence, args.seconds, args.fps)
                    trial["passed"] = not trial["validation_errors"]
                    report["results"].append(dict(result, repeat=repeat+1,
                                                   case_name=case["name"],
                                                   passed=trial["passed"]))
                except Exception as exc:
                    # A rejected or lost trial does not discard successful
                    # earlier measurements or prevent independent later cases.
                    trial["collection_error"] = f"{type(exc).__name__}: {exc}"
                report["trials"].append(trial)
                persist(args.output, report, bench)
                if trial["passed"]:
                    print("  PASS: switches={switches}, service gap max={service_gap[max_us]} us, "
                          "SPI transfers={spi_transfers}, modem skips={modem_query_skips}, "
                          "IQ skips={iq_workaround_skips}".format(**trial["result"]), flush=True)
                else:
                    print("  FAIL: " + (trial.get("collection_error")
                          or "; ".join(trial["validation_errors"])), flush=True)
                sequence += 1
    except BaseException as exc:
        report["collection_error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        persist(args.output, report, bench)
        if bench:
            bench.close()
        print(f"Saved {len(report['trials'])}/{report['planned_trials']} trials: "
              f"{report['passed']} passed, {report['failed']} failed; {args.output}", flush=True)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(collect(arguments()))
