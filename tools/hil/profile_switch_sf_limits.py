"""Descending SF10..SF5 at 125 kHz; find sampled settling pass/fail brackets."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from profile_switch_settling import settling_plan


SF_ORDER = tuple(range(10, 4, -1))


def sf_limits(run, checkpoint, stop_on_first_pass=False):
    result = dict(complete=False, spreading_factors=[])
    for sf in SF_ORDER:
        capture = run(sf)
        allowed = (("first_400_of_400_pass", "nominal_timing_limit_with_misses") if stop_on_first_pass else
                   ("first_failure_above_pass", "nominal_timing_limit_after_pass", "nominal_timing_limit_with_misses"))
        if not capture.get("complete") or capture.get("stopped") not in allowed:
            raise RuntimeError("Incomplete/fixture result; do not advance to lower SF")
        if (capture.get("sf") != sf or capture.get("find_upper_limit") != (not stop_on_first_pass)
                or capture.get("bw_khz") != 125):
            raise RuntimeError("Unexpected SF sweep configuration")
        result["spreading_factors"].append(dict(sf=sf, plan=capture["plan"],
                                              first_perfect_settle_us=capture["first_perfect_settle_us"],
                                              last_perfect_settle_us=capture["last_perfect_settle_us"],
                                              first_failed_above_pass_us=capture.get("first_failed_above_pass_us"),
                                              stopped=capture["stopped"], levels=capture["levels"]))
        checkpoint(result)
        if stop_on_first_pass and capture["stopped"] == "first_400_of_400_pass":
            result.update(complete=True, stopped="first_400_of_400_pass", first_perfect_sf=sf,
                          first_perfect_settle_us=capture["first_perfect_settle_us"])
            checkpoint(result)
            return result
    result.update(complete=True, stopped="sf10_through_sf5_complete")
    checkpoint(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-on-first-pass", action="store_true",
                        help="Stop the entire descending-SF sweep at the first 400/400 setting")
    parser.add_argument("--reuse-prefix", type=Path,
                        help="Reuse completed raw settings from a stopped sweep; preserve original files")
    args = parser.parse_args()
    paths = {sf: args.output.with_name(args.output.stem + f"_sf{sf}.json") for sf in SF_ORDER}
    if args.receiver == args.sender or args.output.exists() or any(p.exists() for p in paths.values()):
        parser.error("different ports and fresh output prefix required")
    result = dict(experiment="descending_sf_125_settling_limits", receiver=args.receiver, sender=args.sender,
                  sf_order=SF_ORDER, bw_khz=125, channels=4, dwell_symbols=5.1, preamble_symbols=32,
                  samples_per_channel=100, power_dbm=-9, expected_rx_gain_reg=0x94,
                  modulation_cache=True, trace_enabled=False, step_us=100,
                  stop_on_first_pass=args.stop_on_first_pass,
                  scope="Sampled pass/fail brackets, not proof of monotonic behavior or zero PER",
                  complete=False, captures=[])

    def save(progress=None):
        if progress:
            result.update(progress)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    def run(sf):
        plan = settling_plan(sf=sf)
        print(f"SF{sf}/125: search 0..{plan['delays_us'][-1]} us added delay; "
              + ("stop entire sweep on first 400/400" if args.stop_on_first_pass else "find upper limit"), flush=True)
        command = [sys.executable, str(Path(__file__).with_name("profile_switch_settling.py")),
                   "--receiver", args.receiver, "--sender", args.sender, "--output", str(paths[sf]),
                   "--sf", str(sf)]
        if not args.stop_on_first_pass:
            command.append("--find-upper-limit")
        if args.reuse_prefix:
            source = args.reuse_prefix.with_name(args.reuse_prefix.stem + f"_sf{sf}.json")
            command.extend(("--reuse-prefix", str(source)))
        completed = subprocess.run(command, check=False)
        result["captures"].append(dict(sf=sf, path=paths[sf].name, exit_code=completed.returncode))
        save()
        if completed.returncode:
            raise RuntimeError("SF collector failed; stopping without another RF probe")
        return json.loads(paths[sf].read_text(encoding="utf-8"))

    try:
        save()
        sf_limits(run, save, args.stop_on_first_pass)
    except Exception as error:
        result.update(complete=False, stopped="fixture_error", error=str(error))
        raise
    finally:
        # Each child ends by rebooting both boards into idle HIL.
        save()


if __name__ == "__main__":
    main()
