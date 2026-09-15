"""Bounded stationary RX controls for adjacent-channel preamble detection.

Unlike a capacity sweep, off-frequency non-reception is expected. Never retry
an RF probe. Stop if an on-channel positive control or a fixture check fails.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import secrets
import time

from profile_switch import exchange, read_packet_response
from profile_switch_packets import open_port
from profile_switch_channels import collect_trace


OFFSETS = (None, 0, -250, 250, -500, 500, -1000, 1000)


def diagnostic_plan(rounds, seed):
    rng = random.Random(seed)
    for repeat in range(rounds):
        cases = list(OFFSETS)
        rng.shuffle(cases)
        for offset in cases:
            yield repeat + 1, offset


def check_word(reply, frequency):
    expected = int(frequency * (1 << 25) / 32000)
    if abs(reply.get("rf_word", 0) - expected) > 4:
        raise RuntimeError("Observed SetRfFrequency bytes do not match requested frequency")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sf", type=int, choices=(6, 8), default=8)
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--seed", type=int, default=814910)
    parser.add_argument("--expect-rx-gain", choices=("normal", "boosted"), default="normal")
    args = parser.parse_args()
    if args.receiver == args.sender or args.output.exists() or not 1 <= args.rounds <= 16:
        parser.error("different ports, fresh output, 1..16 rounds required")
    result = dict(schema=1, experiment="stationary_preamble_diagnostic", sf=args.sf,
                  bw_khz=125, receiver=args.receiver, sender=args.sender, rx_khz=910000,
                  window_ms=450, power_dbm=-9, preamble=32, rounds=args.rounds,
                  seed=args.seed, complete=False, trials=[])
    result["expected_rx_gain_reg"] = 0x94 if args.expect_rx_gain == "normal" else 0x96
    rx = tx = None
    sequence = secrets.randbelow(0x3fffffff) + 1

    def next_seq():
        nonlocal sequence
        sequence += 1
        return sequence

    def save():
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    try:
        rx = open_port(args.receiver)
        tx = open_port(args.sender)
        for port in (rx, tx):
            if exchange(port, "info", "bench", 3).get("preamble_diagnostic") != 1:
                raise RuntimeError("Wrong diagnostic firmware")
        for repeat, offset in diagnostic_plan(args.rounds, args.seed):
            tx_khz = 910000 + (offset or 0)
            setup_seq = next_seq()
            setup = exchange(tx, f"diagtxsetup {args.sf} {tx_khz} {setup_seq}", "listening", 3, setup_seq, cached=True)
            if setup.get("rc") or not setup.get("tx_ready") or setup.get("sf") != args.sf or setup.get("freq_khz") != tx_khz:
                raise RuntimeError("Reference TX setup failed")
            check_word(setup, tx_khz)
            seq = next_seq()
            arm_start = time.monotonic_ns()
            armed = exchange(rx, f"diaglisten {args.sf} 910000 {seq} 450", "listening", 3, seq, cached=True)
            arm_end = time.monotonic_ns()
            if not armed.get("stationary") or not armed.get("full_init") or armed.get("sf") != args.sf or armed.get("window_ms") != 450:
                raise RuntimeError("Receiver configuration mismatch")
            check_word(armed, 910000)
            if armed.get("rx_gain_reg") != result["expected_rx_gain_reg"]:
                raise RuntimeError("Receiver gain does not match requested control")
            sent = None
            tx_start = tx_end = None
            with ThreadPoolExecutor(max_workers=1) as pool:
                incoming = pool.submit(read_packet_response, rx, "received", seq, 2)
                if offset is not None:
                    tx_start = time.monotonic_ns()
                    sent = exchange(tx, f"diagtx {seq}", "sent", 2, seq, cached=True)
                    tx_end = time.monotonic_ns()
                    if sent.get("rc") or not sent.get("set_tx_seen") or sent.get("sf") != args.sf or sent.get("freq_khz") != tx_khz:
                        raise RuntimeError("Reference TX failed")
                    check_word(sent, tx_khz)
                received = incoming.result()
            if not received.get("window_complete") or received.get("device_errors") or received.get("rx_mode") != 1:
                raise RuntimeError("Receiver hardware/fixture error")
            check_word(received, 910000)
            trace = collect_trace(rx, seq)
            if trace.get("overflow"):
                raise RuntimeError("Diagnostic trace overflow")
            result["trials"].append(dict(sequence=seq, repeat=repeat, offset_khz=offset,
                                        setup=setup, armed=armed, sent=sent, received=received, trace=trace,
                                        host_times_ns=dict(arm_start=arm_start, arm_end=arm_end,
                                                           tx_start=tx_start, tx_end=tx_end)))
            save()
            label = "silent" if offset is None else f"{offset:+d} kHz"
            print(f"Round {repeat}, {label}: preambles={received['preambles']} headers={received['headers']} valid={received['valid']} IRQ=0x{received['seen_irq']:04x}", flush=True)
            if offset == 0 and received.get("valid") != 1:
                result["stopped"] = "positive_control_failed"
                return
        result.update(complete=True, stopped="bounded_controls_complete")
    except Exception as error:
        result.update(stopped="fixture_error", error=str(error))
        raise
    finally:
        for port in (rx, tx):
            if port:
                try:
                    exchange(port, "diagstop", "stopped", 2)
                except Exception as error:
                    result.setdefault("cleanup_errors", []).append(str(error))
                finally:
                    port.close()
        save()


if __name__ == "__main__":
    main()
