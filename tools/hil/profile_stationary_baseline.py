"""100 fixed-channel probes before scanning. Full RX initialization once; no RF retries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import secrets
import time

from profile_switch import exchange, read_packet_response
from profile_switch_packets import open_port
from profile_switch_dwell import reboot_boards
from profile_preamble_diagnostic import check_word


def check_status(status, trials, frequency_khz):
    check_word(status, frequency_khz)
    if (not status.get("stationary") or not status.get("active") or status.get("rf_commands") != 0
            or status.get("rx_gain_reg") != 0x94 or status.get("rx_mode") != 1
            or status.get("device_errors") or status.get("failures")):
        raise RuntimeError("Stationary receiver changed frequency/gain or failed")
    successes = sum(t["valid"] for t in trials)
    if status.get("received") != successes or status.get("missed") != len(trials)-successes:
        raise RuntimeError("Host/device baseline counts disagree")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", type=int, choices=range(4), default=3)
    parser.add_argument("--sf", type=int, choices=range(5, 11), default=10)
    args = parser.parse_args()
    if args.receiver == args.sender or args.output.exists():
        parser.error("Different ports and fresh output required")
    result = dict(schema=1, experiment="stationary_100_packet_baseline", complete=False,
                  receiver=args.receiver, sender=args.sender, sf=args.sf, bw_khz=125, cr=5,
                  channel=args.channel, frequency_khz=909500+250*args.channel,
                  samples=100, preamble_symbols=32, power_dbm=-9, packet_bytes=16,
                  expected_rx_gain_reg=0x94, full_init_once=True, retunes=False,
                  seed=606125, trials=[])
    rx = tx = None
    seq = secrets.randbelow(0x3fffffff)+1

    def next_seq():
        nonlocal seq
        seq += 1
        return seq

    def save():
        args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")

    try:
        save()
        reboot_boards((args.receiver, args.sender))
        rx, tx = open_port(args.receiver), open_port(args.sender)
        result["receiver_info"] = exchange(rx, "info", "bench", 3)
        result["sender_info"] = exchange(tx, "info", "bench", 3)
        if result["receiver_info"].get("stationary_baseline") != 1:
            raise RuntimeError("Receiver lacks stationary baseline")
        sender = result["sender_info"]
        if (not (sender.get("channel_tx") == 1 or sender.get("channel_sweep") == 1)
                or sender.get("channel_sf_select") != 1 or sender.get("channel_bw_select") != 1):
            raise RuntimeError("Sender lacks reference TX")
        setup_seq = next_seq()
        setup = exchange(rx, f"basestart {args.channel} {args.sf} {setup_seq}", "listening", 3,
                         setup_seq, cached=True)
        result["setup"] = setup
        check_word(setup, result["frequency_khz"])
        expected = dict(stationary=True, full_init=True, channel=args.channel, sf=args.sf,
                        bw_khz=125, freq_khz=result["frequency_khz"], preamble=32, rx_gain_reg=0x94)
        if any(setup.get(k) != v for k, v in expected.items()):
            raise RuntimeError("Stationary setup mismatch")
        rng = random.Random(result["seed"])
        for sample in range(1, 101):
            probe_seq = next_seq()
            armed = exchange(rx, f"baseexpect {probe_seq}", "listening", 2, probe_seq, cached=True)
            if not armed.get("stationary") or armed.get("channel") != args.channel:
                raise RuntimeError("Stationary expectation mismatch")
            pause = rng.uniform(0, .2)
            time.sleep(pause)
            with ThreadPoolExecutor(max_workers=1) as pool:
                incoming = pool.submit(read_packet_response, rx, "received", probe_seq, 5)
                sent = exchange(tx, f"scantx {args.channel} {probe_seq} 16 32 {args.sf} 125",
                                "sent", 3, probe_seq, cached=True)
                received = incoming.result()
            if any(sent.get(k) != v for k, v in dict(rc=0, channel=args.channel, len=16,
                    preamble=32, sf=args.sf, bw_khz=125, power_dbm=-9).items()):
                raise RuntimeError(f"Reference TX failed/mismatched: {sent}")
            if (not received.get("stationary") or received.get("channel") != args.channel
                    or received.get("device_errors")
                    or (received.get("timeout") and received.get("rx_mode") != 1)):
                raise RuntimeError(f"Stationary RX hardware/mode failure: {received}")
            valid = received.get("valid") is True and received.get("len") == 16
            result["trials"].append(dict(sample=sample, sequence=probe_seq, armed=armed,
                                         sent=sent, received=received, valid=valid, pause_ms=pause*1000))
            # Finish all 100 requested probes, including actual RF misses, without retries.
            save()
            if sample % 10 == 0 or not valid:
                print(f"Baseline {sample}/100: {sum(t['valid'] for t in result['trials'])} received", flush=True)
        status_seq = next_seq()
        result["status"] = exchange(rx, f"basestatus {status_seq}", "result", 3, status_seq, cached=True)
        check_status(result["status"], result["trials"], result["frequency_khz"])
        result["received"] = sum(t["valid"] for t in result["trials"])
        result.update(complete=True, passed=result["received"] == 100, stopped="100_probes_complete")
    except Exception as error:
        result.update(complete=False, stopped="fixture_error", error=str(error))
        raise
    finally:
        if rx:
            try:
                exchange(rx, "basestop", "stopped", 2)
            except Exception as error:
                result.setdefault("cleanup_errors", []).append(str(error))
        for port in (rx, tx):
            if port:
                port.close()
        try:
            reboot_boards((args.receiver, args.sender))
        except Exception as error:
            result.setdefault("cleanup_errors", []).append(str(error))
        save()


if __name__ == "__main__":
    main()
