"""Bounded 0 dBm HIL packet checks after actual RX-to-RX profile switches."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
from pathlib import Path

import serial
from profile_switch import exchange, exchange_ready, read_packet_response, configure_session
from profile_switch_sweep import CASES, BULK_CASES, PRODUCTION_CASES


def open_port(name):
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = name, 115200, 0.25
    port.write_timeout = 2
    configure_session(port)
    port.open()
    port.reset_input_buffer()
    info = exchange(port, "info", "bench", 5)
    if info.get("bench") != "production-profile-switch-v8" or not info.get("ready"):
        port.close()
        raise RuntimeError(f"Wrong/unready HIL firmware on {name}")
    return port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--power", type=int, choices=range(-9, 1), default=0)
    parser.add_argument("--cases", nargs="+", choices=sorted({c[0] for c in CASES + BULK_CASES + PRODUCTION_CASES}))
    parser.add_argument("--sender-spi", type=int, choices=(2,4,8), default=8)
    parser.add_argument("--byte-sender", action="store_true")
    args = parser.parse_args()
    if args.receiver == args.sender or args.output.exists() or not 1 <= args.rounds <= 5:
        parser.error("use different ports, fresh output, rounds 1..5")
    results = {"schema": 8, "experiment": "rx_after_profile_switch_packets",
               "receiver": args.receiver, "sender": args.sender,
               "power_dbm": args.power, "sender_spi_mhz": args.sender_spi,
               "sender_bulk": not args.byte_sender, "complete": False, "trials": [],
               "scope": "Stationary short-range payload checks, not sensitivity/PER qualification"}
    rx = tx = None
    try:
        rx, tx = open_port(args.receiver), open_port(args.sender)
        sequence = secrets.randbelow(0x3fffffff) + 1
        cases = [PRODUCTION_CASES[0], PRODUCTION_CASES[-1]] if args.smoke else PRODUCTION_CASES
        if args.cases:
            available = {c[0]: c for c in CASES + BULK_CASES + PRODUCTION_CASES}
            cases = [available[name] for name in args.cases]
        if not cases:
            raise ValueError("No selected cases")
        for repeat in range(args.rounds):
            for name, mask, mhz in (cases if repeat % 2 == 0 else list(reversed(cases))):
                first = len(results["trials"])
                for sf in (7, 8, 9):
                    for target in (0, 1):
                        for length in ((16,) if args.smoke else (16, 64, 255)):
                            sequence += 1
                            listening = exchange_ready(rx, f"listen {sf} {target} {sequence} {mask} {mhz}", "listening", 6, sequence, cached=True)
                            if (listening["listening"] != sequence or listening["profile"] != target
                                    or listening["sf500"] != sf
                                    or (mask & 512 and listening["optimized_rx_resumes"] == 0)
                                    or (not mask & 512 and listening["optimized_rx_resumes"] != 0)):
                                raise RuntimeError("Mismatched receiver readiness")
                            # Drain the receiver while the other board transmits.
                            # Native USB CDC must not depend on the sender's
                            # blocking TX/serial round trip finishing first.
                            with ThreadPoolExecutor(max_workers=1) as pool:
                                incoming = pool.submit(read_packet_response, rx, "received", sequence, 7)
                                try:
                                    sent = exchange(tx, f"tx {sf} {target} {sequence} {length} {listening['preamble']} {args.power} {args.sender_spi} {int(not args.byte_sender)}", "sent", 6, sequence, cached=True)
                                except TimeoutError:
                                    # Do not retransmit an unacknowledged TX:
                                    # it may already have reached the receiver.
                                    sent = {"sent": sequence, "len": length, "rc": None, "host_timeout": True}
                                try:
                                    received = incoming.result()
                                except TimeoutError:
                                    received = {"received": sequence, "valid": False, "host_timeout": True}
                            payload_valid = (received.get("received") == sequence and received.get("valid")
                                             and received.get("len") == length and received.get("profile") == target)
                            valid = (sent.get("rc") == 0 and sent.get("sent") == sequence
                                     and sent.get("len") == length and payload_valid
                                     and sent.get("spi_mhz") == args.sender_spi
                                     and sent.get("bulk") == (not args.byte_sender))
                            trial = dict(case=name, mask=mask, spi_mhz=mhz, round=repeat + 1,
                                         sf500=sf, target=target, length=length, sequence=sequence,
                                         listening=listening, sent=sent, received=received,
                                         payload_valid=bool(payload_valid), valid=bool(valid))
                            results["trials"].append(trial)
                            args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
                            if not valid:
                                print(f"INCOMPLETE {name} SF{sf} target={target} length={length}: sent={sent}, received={received}", flush=True)
                trials = results["trials"][first:]
                print(f"round={repeat+1} {name}: {sum(t['payload_valid'] for t in trials)}/{len(trials)} payloads valid; "
                      f"{sum(t['valid'] for t in trials)}/{len(trials)} fully acknowledged", flush=True)
        results["complete"] = True
    finally:
        for port in (rx, tx):
            if port:
                port.close()
        if results["trials"]:
            args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
