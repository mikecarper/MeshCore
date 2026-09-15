"""Bounded same-image rollback isolation with measured per-mode settling budgets."""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time

from profile_switch import exchange
from profile_switch_packets import open_port
from profile_switch_dwell import reboot_boards

CASES = [
    dict(name="fast_double", rollback=0, cache=True, repeat=True),
    dict(name="always_modulation", rollback=0, cache=False, repeat=True),
    dict(name="individual_modulation", rollback=2, cache=False, repeat=True),
    dict(name="full_rx", rollback=1, cache=False, repeat=True),
    dict(name="full_rx_individual", rollback=3, cache=False, repeat=True),
    dict(name="legacy_warm", rollback=7, cache=False, repeat=True),
    dict(name="legacy_cold", rollback=15, cache=False, repeat=True),
    dict(name="fast_single", rollback=0, cache=True, repeat=False),
    dict(name="legacy_cold_single", rollback=15, cache=False, repeat=False),
    dict(name="legacy_cold_6ms", rollback=15, cache=False, repeat=True, tcxo_us=6000),
    dict(name="legacy_cold_single_6ms", rollback=15, cache=False, repeat=False, tcxo_us=6000),
]
PREAMBLE_US = 262144
DWELL_US = 41780


def settling_budget(maximum_switch_us):
    # Largest 100-us step allowing the observed max retune + 0.5 ms per-hop
    # scheduling reserve. Then verify an actual full-cycle window before TX.
    delay = (PREAMBLE_US//4 - DWELL_US - maximum_switch_us - 500)//100*100
    if not 0 <= delay <= 24000:
        raise RuntimeError("Rollback cannot fit the full-sweep preamble budget")
    return int(delay)


def check_timing(status, case, delay):
    n = status["switch"]["n"]
    passes=case.get('retune_passes',1)
    detour=case.get('first_pass_detour',False)
    fast = case["rollback"] & 9 == 0
    expected_mod = 3 if case["rollback"] & 2 else 0 if fast and case["cache"] else 1
    if (n < 64 or any(status[k] for k in ("failures", "rx_mode_errors", "cache_errors", "rx_errors"))
            or status.get("rollback") != case["rollback"] or status.get("rx_gain_reg") != 0x94
            or status.get("settle_us") != delay or status.get("frequency_repeat") != case["repeat"]
            or bool(status["optimized_rx_resumes"]) != fast
            or status.get('retune_passes',1)!=passes or status.get('second_pass_blocked',0)
            or status.get('first_pass_detour',False)!=detour
            or status["rf_commands"] != n*passes*(2 if case["repeat"] else 1)
            or status["modulation_commands"] != n*passes*expected_mod
            or status.get("warm_standby") != (case["rollback"] & 8 == 0)
            or status.get("batched_modulation") != (case["rollback"] & 2 == 0)
            or status.get("bulk_spi") != (case["rollback"] & 4 == 0)):
        raise RuntimeError("Rollback timing/actual command policy mismatch")
    if passes==2 and (status['optimized_rx_resumes']!=2*n
            or status['first_pass']['n']!=n or status['second_pass']['n']!=n):
        raise RuntimeError('Both full retune passes were not measured')
    if detour and (status['detour']['n']!=n or status['detour']['bad']):
        raise RuntimeError('Actual first-pass detour/final correction mismatch')
    if delay and (status["settling"]["n"] != n or status["settling"]["min_us"] < delay):
        raise RuntimeError("Settling not applied")
    if case.get("tcxo_us") is not None and status.get("tcxo_us") != case["tcxo_us"]:
        raise RuntimeError("Actual TCXO delay differs from requested rollback")


def timing_window(port_name, case, delay):
    reboot_boards((port_name,))
    port = open_port(port_name)
    try:
        info = exchange(port, "info", "bench", 3)
        if info.get("rollback_ab") != 1 or info.get("rx_gain_reg") != 0x94:
            raise RuntimeError("Wrong rollback firmware/gain")
        if case.get('retune_passes',1)==2:
            if exchange(port,'retuneinfo','full_retune_repeat',2).get('guarded') is not True:
                raise RuntimeError('Missing guarded full retune repeat')
            if exchange(port,'scanretunepasses 2','retune_passes',2)['retune_passes']!=2:
                raise RuntimeError('Retune repetition acknowledgement mismatch')
        if case.get('first_pass_detour',False):
            capability=exchange(port,'detourinfo','first_pass_detour',2)
            if any(capability.get(k)!=v for k,v in dict(first_pass_detour=1,requested_offset_khz=0.01,rf_offset_steps=10,offset_hz=9.5367431640625,first_cr=6,final_cr=5).items()):
                raise RuntimeError('Unexpected first-pass detour configuration')
            if exchange(port,'scanfirstdetour 1','first_pass_detour',2)['first_pass_detour'] is not True:
                raise RuntimeError('First-pass detour acknowledgement mismatch')
        if case.get("tcxo_us") is not None:
            value=case["tcxo_us"]
            if exchange(port,f"scantcxo {value}","tcxo_us",2)["tcxo_us"]!=value:
                raise RuntimeError("TCXO rollback acknowledgement mismatch")
        step=case.get("channel_step_khz",250)
        if step!=250:
            if info.get("channel_step_select")!=1 or exchange(port,f"scanstep {step}","channel_step_khz",2)["channel_step_khz"]!=step:
                raise RuntimeError("Receiver spacing acknowledgement mismatch")
        for command, key, value in (("scanrollback", "rollback", case["rollback"]),
                                    ("scanmodcache", "modulation_cache", case["cache"]),
                                    ("scanfreqrepeat", "frequency_repeat", case["repeat"]),
                                    ("scansettle", "settle_us", delay)):
            if exchange(port, f"{command} {int(value)}", key, 2)[key] != value:
                raise RuntimeError("Rollback configuration acknowledgement mismatch")
        seq = secrets.randbelow(0x3fffffff)+1
        config = exchange(port, f"scanstart 4 32 {seq} {DWELL_US} 0 10 125", "listening", 3, seq, cached=True)
        if config.get("step_mhz")!=step/1000:
            raise RuntimeError("Timing control spacing mismatch")
        time.sleep(6)
        status = exchange(port, f"scanstatus {seq+1}", "result", 3, seq+1, cached=True)
        check_timing(status, case, delay)
        return dict(info=info, config=config, status=status, seconds=6)
    finally:
        try:
            exchange(port, "scanstop", "stopped", 2)
        finally:
            port.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=[c["name"] for c in CASES])
    parser.add_argument("--channel-step-khz", type=int, choices=(250,1000), default=250)
    args = parser.parse_args()
    cases = [dict(c,channel_step_khz=args.channel_step_khz) for c in CASES if not args.cases or c["name"] in args.cases]
    paths = {c["name"]: args.output.with_name(args.output.stem+"_"+c["name"]+".json") for c in cases}
    if args.receiver == args.sender or args.output.exists() or any(p.exists() for p in paths.values()):
        parser.error("Different ports and fresh output prefix required")
    result = dict(experiment="sf10_125_rollback_isolation", receiver=args.receiver, sender=args.sender,
                  complete=False, sf=10, bw_khz=125, preamble_symbols=32, dwell_symbols=5.1,
                  samples_per_channel=100, channels=4, reserve_us_per_hop=500, budget_step_us=100,
                  nominal_preamble_us=PREAMBLE_US, cases=[])
    result['channel_step_khz']=args.channel_step_khz

    def save():
        args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")

    try:
        save()
        for case in cases:
            entry = dict(case, complete=False)
            result["cases"].append(entry)
            entry["untimed_delay_control"] = timing_window(args.receiver, case, 0)
            delay = settling_budget(entry["untimed_delay_control"]["status"]["switch"]["max_us"])
            entry["settle_us"] = delay
            save()
            entry["settled_control"] = timing_window(args.receiver, case, delay)
            cycle = entry["settled_control"]["status"]["idle_cycle"]
            if cycle["n"] < 8 or cycle["max_us"] >= PREAMBLE_US:
                raise RuntimeError("Measured full sweep exceeds preamble or insufficient complete cycles; no TX")
            save()
            print(f"{case['name']}: settle {delay} us; measured max idle cycle {cycle['max_us']} / {PREAMBLE_US} us", flush=True)
            reboot_boards((args.receiver, args.sender))
            command = [sys.executable, str(Path(__file__).with_name("profile_switch_channels.py")),
                       "--receiver", args.receiver, "--sender", args.sender, "--output", str(paths[case["name"]]),
                       "--sf", "10", "--bw-khz", "125", "--samples", "100", "--max-channels", "4",
                       "--dwell-symbols", "5.1", "--preamble", "32", "--settle-us", str(delay),
                       "--modulation-cache", "on" if case["cache"] else "off",
                       "--frequency-repeat", "on" if case["repeat"] else "off", "--rollback", str(case["rollback"])]
            if case.get("tcxo_us") is not None:
                command.extend(("--tcxo-us",str(case["tcxo_us"])))
            command.extend(("--channel-step-khz",str(args.channel_step_khz)))
            completed = subprocess.run(command, check=False)
            entry["capture"] = str(paths[case["name"]])
            entry["exit_code"] = completed.returncode
            save()
            if completed.returncode:
                raise RuntimeError("Packet collector failed; not an RF result")
            capture = json.loads(paths[case["name"]].read_text())
            if not capture["complete"]:
                raise RuntimeError("Incomplete packet capture")
            level = capture["levels"][0]
            entry.update(complete=True, received=level["received"], attempted=level["attempted"],
                         status=level["status"], stopped=capture["stopped"])
            save()
            if capture["stopped"] == "channel_limit_without_miss":
                if level["received"] != 400 or any(p != dict(attempted=100, received=100) for p in level["per_channel"]):
                    raise RuntimeError("Invalid perfect result")
                result.update(complete=True, stopped="first_400_of_400_pass", passed_case=case["name"])
                return
        result.update(complete=True, stopped="all_rollback_cases_failed")
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
