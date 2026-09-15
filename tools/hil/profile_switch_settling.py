"""SF5..10/125 four-channel post-BUSY settling sweep with explicit stop policy."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from profile_switch_dwell import reboot_boards
from profile_switch_channels import channel_dwell_us


def reusable_capture(path, receiver, sender, sf, delay, frequency_repeat=False):
    """Reuse only complete matching measurements; never alter source evidence."""
    raw = path.read_bytes()
    capture = json.loads(raw)
    expected = dict(receiver=receiver, sender=sender, sf=sf, settle_us=delay, bw_khz=125,
                    cr=5, dwell_symbols=5.1, preamble_symbols=32, samples_per_channel=100,
                    expected_rx_gain_reg=0x94, modulation_cache=True, trace_enabled=False,
                    irq_poll_us=0, packet_bytes=16, power_dbm=-9, base_mhz=909.5,
                    channel_step_mhz=0.25, seed=606125)
    if (not capture.get("complete") or capture.get("accept_offchannel",False) or capture.get("mixed_profiles",False)
            or capture.get("frequency_repeat", False) != frequency_repeat
            or any(capture.get(k) != v for k, v in expected.items())):
        raise RuntimeError(f"Incomplete or mismatched reusable capture: {path}")
    return capture, hashlib.sha256(raw).hexdigest()


def settling_plan(base_switch_us=453, sf=6, step_us=100, frequency_repeat=False):
    # 32 programmed symbols; do not spend sync/header time as scan budget.
    dwell_us = channel_dwell_us(sf, 5.1, 125)
    symbol_us = (1 << sf) * 8
    preamble_us, channels = 32*symbol_us, 4
    maximum = preamble_us // channels - dwell_us - base_switch_us
    if base_switch_us < 0 or not 0 <= maximum <= 24000 or step_us not in (100, 1000):
        raise ValueError("Unsupported settling budget")
    return dict(sf=sf, symbol_us=symbol_us, preamble_us=preamble_us, dwell_us=dwell_us, channels=channels,
                nominal_switch_us=base_switch_us, maximum_added_us=maximum,
                step_us=step_us, frequency_repeat=frequency_repeat,
                delays_us=list(range(0, maximum+1, step_us)))


def settling_levels(run, checkpoint, plan, find_upper_limit=False):
    result = dict(complete=False, first_perfect_settle_us=None, last_perfect_settle_us=None, levels=[])
    for delay in plan["delays_us"]:
        capture = run(delay)
        if (not capture.get("complete") or capture.get("accept_offchannel",False)
                or capture.get("mixed_profiles",False) or capture.get("stopped") not in (
                "first_rf_miss", "channel_limit_without_miss")):
            raise RuntimeError("Fixture/incomplete result; do not increase delay")
        if (capture.get("settle_us") != delay or capture.get("sf") != plan["sf"]
                or capture.get("frequency_repeat", False) != plan.get("frequency_repeat", False)
                or capture.get("bw_khz") != 125 or capture.get("dwell_us") != plan["dwell_us"]
                or capture.get("preamble_symbols") != 32 or len(capture.get("levels", [])) != 1):
            raise RuntimeError("Unexpected scan configuration")
        level = capture["levels"][0]
        if level["channels"] != 4:
            raise RuntimeError("Unexpected channel count")
        perfect = capture["stopped"] == "channel_limit_without_miss"
        if perfect and (level["received"] != 400 or level["attempted"] != 400
                        or len(level["per_channel"]) != 4 or any(
                            c != dict(attempted=100, received=100) for c in level["per_channel"])):
            raise RuntimeError("Incomplete per-channel perfect result")
        result["levels"].append(dict(settle_us=delay, attempted=level["attempted"],
                                     received=level["received"], perfect=perfect,
                                     per_channel=level["per_channel"], status=level["status"]))
        checkpoint(result)
        if perfect:
            if result["first_perfect_settle_us"] is None:
                result["first_perfect_settle_us"] = delay
            result["last_perfect_settle_us"] = delay
            if not find_upper_limit:
                result.update(complete=True, stopped="first_400_of_400_pass")
                checkpoint(result)
                return result
        elif find_upper_limit and result["last_perfect_settle_us"] is not None:
            result.update(complete=True, stopped="first_failure_above_pass", first_failed_above_pass_us=delay)
            checkpoint(result)
            return result
        checkpoint(result)
    result.update(complete=True, stopped="nominal_timing_limit_with_misses"
                  if result["last_perfect_settle_us"] is None else "nominal_timing_limit_after_pass")
    checkpoint(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sf", type=int, choices=range(5, 11), default=6)
    parser.add_argument("--find-upper-limit", action="store_true")
    parser.add_argument("--step-us", type=int, choices=(100, 1000), default=100)
    parser.add_argument("--frequency-repeat", choices=("off", "on"), default="off")
    parser.add_argument("--reuse-prefix", type=Path,
                        help="Read completed raw settings under this old prefix without modifying them")
    args = parser.parse_args()
    frequency_repeat = args.frequency_repeat == "on"
    # Same-image RX-only means were 451.948 / 538.192 us; round upward for
    # each nominal budget. Actual cycle timing remains the measured evidence.
    plan = settling_plan(base_switch_us=539 if frequency_repeat else 453, sf=args.sf,
                         step_us=args.step_us, frequency_repeat=frequency_repeat)
    paths = {d: args.output.with_name(args.output.stem + f"_{d}us.json") for d in plan["delays_us"]}
    if args.receiver == args.sender or args.output.exists() or any(p.exists() for p in paths.values()):
        parser.error("different ports and fresh output prefix required")
    result = dict(experiment=f"sf{args.sf}_125_post_busy_settling", receiver=args.receiver, sender=args.sender,
                  plan=plan, sf=args.sf, bw_khz=125, dwell_symbols=5.1, preamble_symbols=32,
                  find_upper_limit=args.find_upper_limit,
                  frequency_repeat=frequency_repeat, step_us=args.step_us,
                  samples_per_channel=100, expected_rx_gain_reg=0x94, modulation_cache=True,
                  power_dbm=-9, trace_enabled=False, rx_active_during_settling=True,
                  complete=False, captures=[])

    def save(progress=None):
        if progress:
            result.update(progress)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    def run(delay):
        if args.reuse_prefix:
            source = args.reuse_prefix.with_name(args.reuse_prefix.stem + f"_{delay}us.json")
            if source.exists():
                capture, digest = reusable_capture(source, args.receiver, args.sender, args.sf, delay, frequency_repeat)
                result["captures"].append(dict(settle_us=delay, path=source.resolve().as_posix(),
                                               reused=True, sha256=digest))
                save()
                print(f"Reused completed SF{args.sf}/125 setting: {delay} us", flush=True)
                return capture
        reboot_boards((args.receiver, args.sender))
        print(f"Added post-BUSY settling: {delay} us", flush=True)
        command = [sys.executable, str(Path(__file__).with_name("profile_switch_channels.py")),
                   "--receiver", args.receiver, "--sender", args.sender, "--output", str(paths[delay]),
                   "--samples", "100", "--preamble", "32", "--max-channels", "4",
                   "--sf", str(args.sf), "--bw-khz", "125", "--dwell-symbols", "5.1",
                   "--modulation-cache", "on", "--expect-rx-gain", "normal", "--settle-us", str(delay),
                   "--frequency-repeat", args.frequency_repeat]
        completed = subprocess.run(command, check=False)
        result["captures"].append(dict(settle_us=delay, path=paths[delay].name, exit_code=completed.returncode))
        save()
        if completed.returncode:
            raise RuntimeError("Collector failed; stopping without another RF probe")
        return json.loads(paths[delay].read_text(encoding="utf-8"))

    try:
        save()
        settling_levels(run, save, plan, args.find_upper_limit)
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
