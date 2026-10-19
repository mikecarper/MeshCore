#!/usr/bin/env python3
"""Sweep T-Deck radio SPI clocks with a fresh HIL reboot for each attempt.

WARNING: This collector explicitly REBOOTS the connected T-Deck before every
clock attempt. It does not erase or flash the device, save settings, or transmit
radio packets. Install the receive-only SPI-inspection HIL firmware first.

An 8 MHz baseline anchor is included even when --mhz omits it. Clock order
alternates ascending/descending between repeats. Each fresh boot runs 100 paired
modem/sync-word reads, then static-screen scheduling, then buffered redraws only
if the preceding checks passed. Requested and measured SPI clocks are retained;
ESP32 divider rounding can make different requests produce the same clock.
"""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time

from tdeck_display_switch import Bench
from tdeck_retune_experiments import command, validate


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--mhz", nargs="+", type=int, choices=range(8, 17),
                        default=list(range(8, 17)))
    parser.add_argument("--repeats", type=int, choices=range(1, 101), default=3)
    parser.add_argument("--seconds", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--fps", type=int, choices=range(1, 31), default=20)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def capture(attempt, bench):
    if bench:
        attempt["raw_lines"].extend(bench.raw_lines)
        bench.raw_lines.clear()


def checkpoint(path, report, attempt=None, bench=None):
    if attempt is not None:
        capture(attempt, bench)
    report["updated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["passed_attempts"] = sum(a.get("passed") is True for a in report["attempts"])
    report["failed_attempts"] = sum(a.get("complete") and not a.get("passed")
                                    for a in report["attempts"])
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def open_verified(port, attempt, phase):
    """Bounded reconnect; every failed open is closed before retrying."""
    errors = []
    for number in range(8):
        bench = None
        try:
            bench = Bench(port)
            bench.send("")
            bench.send("info")
            info = bench.receive(lambda value: isinstance(value.get("bench"), str)
                                 and value["bench"].startswith("tdeck-display-radio2-")
                                 and "ready" in value, 3)
            if not info.get("ready") or not info.get("command", "").startswith("trial "):
                raise RuntimeError("Ready T-Deck retune HIL firmware is required")
            if not isinstance(info.get("loop_priority"), int):
                raise RuntimeError("HIL did not report its owner priority")
            attempt.setdefault("connection_attempts", []).append(
                dict(phase=phase, attempt=number+1, passed=True))
            capture(attempt, bench)
            return bench, info
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            errors.append(error)
            attempt.setdefault("connection_attempts", []).append(
                dict(phase=phase, attempt=number+1, passed=False, error=error))
            if bench:
                capture(attempt, bench)
                bench.close()
            if number < 7:
                time.sleep(0.5)
    raise RuntimeError(f"Unable to connect during {phase} after 8 attempts: {errors[-1]}")


def inspection_errors(result, mhz):
    expected = dict(spi_inspection=True, spi_mhz=mhz, bulk=True,
                    standby_result=0, reference_result=0,
                    correct_modem_reads=100, bad_modem_reads=0,
                    bad_sync_reads=0, read_errors=0, recovery_modem=1,
                    chip_errors_at_8mhz=0, rx_resume_at_8mhz=0)
    errors = [f"{key}: expected {wanted!r}, got {result.get(key)!r}"
              for key, wanted in expected.items()
              if key not in result or result[key] != wanted]
    actual = result.get("actual_spi_hz")
    if not isinstance(actual, (int, float)) or isinstance(actual, bool) or actual <= 0:
        errors.append(f"actual_spi_hz: expected measured clock, got {actual!r}")
    if mhz == 8 and actual != 8000000:
        errors.append(f"8 MHz baseline measured an unexpected clock: {actual!r}")
    return errors


def run_trial(bench, attempt, firmware, mhz, mode, sequence, args):
    case = dict(name=f"{mhz}MHz SPI sweep", display_mode=mode, spi_mhz=mhz,
                modem_cache=True, iq_cache=False, retune_priority=0,
                synthetic_load=False, secondary_preamble=32)
    text = command(case, args.seconds, args.fps, sequence)
    step = dict(kind="static" if mode == 0 else "buffered", command=text,
                sequence=sequence, passed=False)
    attempt["steps"].append(step)
    try:
        bench.send(text)
        try:
            result = bench.receive(lambda value: value.get("result") == sequence,
                                   args.seconds + 6)
        except TimeoutError:
            step["retried_retained_result"] = True
            bench.send(f"result {sequence}")
            result = bench.receive(lambda value: value.get("result") == sequence, 4)
        step["result"] = result
        step["validation_errors"] = validate(
            result, case, firmware, sequence, args.seconds, args.fps)
        actual = result.get("actual_spi_hz")
        if actual != attempt["inspection"].get("actual_spi_hz"):
            step["validation_errors"].append(
                f"actual_spi_hz changed from inspection: {actual!r}")
        step["passed"] = not step["validation_errors"]
    except Exception as exc:
        step["collection_error"] = f"{type(exc).__name__}: {exc}"
    checkpoint(args.output, args.report, attempt, bench)
    return step["passed"]


def summarize(report):
    report["baseline_anchor_passed"] = any(
        a.get("passed") and a["spi_mhz"] == 8 for a in report["attempts"])
    report["clock_summary"] = []
    passing_hz = []
    for mhz in report["clock_plan_mhz"]:
        attempts = [a for a in report["attempts"] if a["spi_mhz"] == mhz]
        actual = sorted({a["inspection"]["actual_spi_hz"] for a in attempts
                         if isinstance(a.get("inspection", {}).get("actual_spi_hz"), (int, float))})
        passed = sum(a.get("passed") is True for a in attempts)
        report["clock_summary"].append(dict(spi_mhz=mhz, actual_spi_hz=actual,
                                             attempts=len(attempts), passed=passed))
        # Require every independent repetition, not one lucky successful boot.
        if len(attempts) == report["repeats"] and passed == len(attempts):
            passing_hz.extend(actual)
    report["maximum_passing_actual_spi_hz"] = (
        max(passing_hz) if passing_hz and report["baseline_anchor_passed"] else None)


def collect(args):
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing report: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clocks = sorted(set(args.mhz) | {8})
    report = dict(recorded_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                  port=args.port, requested_mhz=args.mhz, clock_plan_mhz=clocks,
                  repeats=args.repeats, seconds_per_trial=args.seconds,
                  requested_fps=args.fps, receive_only=True, reboots_device=True,
                  fresh_boot_per_clock_attempt=True, attempts=[])
    args.report = report
    checkpoint(args.output, report)
    sequence = 1
    try:
        for repeat in range(args.repeats):
            order = clocks if repeat % 2 == 0 else list(reversed(clocks))
            for mhz in order:
                attempt = dict(repeat=repeat+1, spi_mhz=mhz, baseline_anchor=mhz == 8,
                               steps=[], raw_lines=[], passed=False, complete=False)
                report["attempts"].append(attempt)
                bench = None
                checkpoint(args.output, report)
                print(f"[{repeat+1}/{args.repeats}] {mhz} MHz: rebooting HIL", flush=True)
                try:
                    bench, info = open_verified(args.port, attempt, "before reboot")
                    attempt["pre_reboot_firmware"] = info
                    bench.send("reboot")
                    attempt["reboot_sent"] = True
                    capture(attempt, bench)
                    bench.close()
                    bench = None
                    checkpoint(args.output, report)
                    time.sleep(1.5)
                    bench, firmware = open_verified(args.port, attempt, "after reboot")
                    attempt["firmware"] = firmware
                    checkpoint(args.output, report, attempt, bench)
                    text = f"inspect {mhz} 1"
                    bench.send(text)
                    inspection = bench.receive(lambda value: value.get("spi_inspection") is True
                                               and value.get("spi_mhz") == mhz, 6)
                    attempt["inspection"] = inspection
                    errors = inspection_errors(inspection, mhz)
                    attempt["steps"].append(dict(kind="inspection", command=text,
                                                  passed=not errors, validation_errors=errors))
                    checkpoint(args.output, report, attempt, bench)
                    if errors:
                        attempt["steps"].append(dict(kind="static", skipped=True,
                                                      reason="SPI inspection failed"))
                        attempt["steps"].append(dict(kind="buffered", skipped=True,
                                                      reason="SPI inspection failed"))
                    else:
                        static_ok = run_trial(bench, attempt, firmware, mhz, 0, sequence, args)
                        sequence += 1
                        if static_ok:
                            attempt["passed"] = run_trial(
                                bench, attempt, firmware, mhz, 2, sequence, args)
                            sequence += 1
                        else:
                            attempt["steps"].append(dict(kind="buffered", skipped=True,
                                                          reason="Static profile trial failed"))
                except Exception as exc:
                    attempt["collection_error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    capture(attempt, bench)
                    if bench:
                        bench.close()
                    attempt["complete"] = True
                    summarize(report)
                    checkpoint(args.output, report)
                    actual = attempt.get("inspection", {}).get("actual_spi_hz", "unavailable")
                    print(f"  {'PASS' if attempt['passed'] else 'FAIL'}: actual SPI {actual} Hz; "
                          f"checkpoint saved", flush=True)
    except BaseException as exc:
        report["collection_error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        summarize(report)
        checkpoint(args.output, report)
        print(f"Saved {len(report['attempts'])} clock attempts: "
              f"{report['passed_attempts']} passed, {report['failed_attempts']} failed; "
              f"maximum passing measured clock={report['maximum_passing_actual_spi_hz']} Hz; "
              f"{args.output}", flush=True)
    return 1 if report["failed_attempts"] or not report["baseline_anchor_passed"] else 0


if __name__ == "__main__":
    sys.exit(collect(arguments()))
