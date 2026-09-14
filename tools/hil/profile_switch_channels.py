"""SF6 or SF8 / 125 kHz scan test: start at four, stop at the first RF miss.

XIAO scanner / Indicator reference recommended. Fixed 32-symbol preamble,
4.8-symbol nominal visits, 16-byte synthetic packets at -9 dBm. This measures
100% of a finite sample, not a proof of a zero packet-error rate.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import random
import secrets
import time

from profile_switch import exchange, exchange_ready, read_packet_response
from profile_switch_packets import open_port


def channel_dwell_us(sf, symbols):
    if sf not in (6, 8) or not math.isfinite(symbols) or not 4 <= symbols <= 32:
        raise ValueError("SF must be 6 or 8; dwell must be finite, 4..32 symbols")
    return math.ceil(symbols * (1 << sf) * 8)  # 125 kHz: 8 us per chip


def capacity_levels(start, probe, finish, checkpoint, samples=100, maximum=64, seed=606125, dwell_us=2458):
    """Pure controller: never expand or transmit again after the first miss."""
    rng = random.Random(seed)
    result = {"levels": [], "complete": False, "last_perfect_channels": None}
    for channels in range(4, maximum + 1):
        config = start(channels)
        level = {"channels": channels, "config": config, "attempted": 0, "received": 0,
                 "per_channel": [{"attempted": 0, "received": 0} for _ in range(channels)],
                 "trials": [], "complete": False}
        result["levels"].append(level)
        checkpoint(result)
        for sample_round in range(samples):
            order = list(range(channels))
            rng.shuffle(order)
            for channel in order:
                # Vary arrivals across about two nominal round trips; do not
                # select a known receive phase or tune the listener to TX.
                pause_ms = rng.uniform(0, 2 * channels * (dwell_us / 1000 + 0.7))
                trial = probe(channel, pause_ms)
                trial.update(channel=channel, sample_round=sample_round + 1, pause_ms=pause_ms)
                level["trials"].append(trial)
                level["attempted"] += 1
                level["per_channel"][channel]["attempted"] += 1
                if trial["valid"]:
                    level["received"] += 1
                    level["per_channel"][channel]["received"] += 1
                else:
                    level["status"] = finish()
                    result.update(complete=True, stopped="first_rf_miss", first_failed_channels=channels,
                                  failed_sequence=trial["sequence"])
                    checkpoint(result)
                    return result
                checkpoint(result)
        level["status"] = finish()
        level["complete"] = True
        result["last_perfect_channels"] = channels
        checkpoint(result)
    result.update(complete=True, stopped="channel_limit_without_miss")
    checkpoint(result)
    return result


def collect_trace(port, sequence):
    offset, events, metadata = 0, [], None
    while True:
        page = exchange(port, f"scantrace {sequence} {offset}", "result", 3, sequence, cached=True)
        if (page.get("trace") != sequence or page.get("offset") != offset
                or page.get("next") != offset + len(page.get("events", []))
                or not offset <= page.get("next", -1) <= page.get("total", -1)):
            raise RuntimeError("Mismatched diagnostic trace page")
        if metadata is None:
            metadata = {k: v for k, v in page.items() if k not in ("events", "offset", "next")}
        elif page["total"] != metadata["total"] or page["origin_us"] != metadata["origin_us"]:
            raise RuntimeError("Diagnostic trace changed during retrieval")
        events.extend(page["events"])
        offset = page["next"]
        if offset == page["total"]:
            return dict(metadata, events=events)
        if not page["events"]:
            raise RuntimeError("Diagnostic trace made no progress")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--max-channels", type=int, default=64)
    parser.add_argument("--preamble", type=int, default=32)
    parser.add_argument("--seed", type=int, default=606125)
    parser.add_argument("--dwell-symbols", type=float, default=4.8)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--sf", type=int, choices=(6, 8), default=6)
    args = parser.parse_args()
    if (args.receiver == args.sender or args.output.exists() or not 1 <= args.samples <= 1000
            or not 4 <= args.max_channels <= 64 or not 12 <= args.preamble <= 256
            or not math.isfinite(args.dwell_symbols) or not 4 <= args.dwell_symbols <= 32):
        parser.error("different ports, fresh output, samples 1..1000, channels 4..64, preamble 12..256 required")
    dwell_us = channel_dwell_us(args.sf, args.dwell_symbols)
    result = {"schema": 3, "experiment": f"sf{args.sf}_125_channel_capacity", "complete": False,
              "receiver": args.receiver, "sender": args.sender, "sf": args.sf, "bw_khz": 125,
              "cr": 5, "preamble_symbols": args.preamble, "dwell_symbols": args.dwell_symbols, "dwell_us": dwell_us,
              "trace_enabled": args.trace, "irq_poll_us": 64 if args.trace else 0,
              "samples_per_channel": args.samples, "packet_bytes": 16, "power_dbm": -9,
              "base_mhz": 909.5, "channel_step_mhz": 0.25, "seed": args.seed,
              "scope": "Lab N-channel scheduler over production retunes; finite short-range sample, not guaranteed PER"}
    rx = tx = None
    sequence = secrets.randbelow(0x3fffffff) + 1

    def next_sequence():
        nonlocal sequence
        sequence += 1
        return sequence

    def save(progress=None):
        if progress:
            result.update(progress)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    def start(channels):
        seq = next_sequence()
        reply = exchange_ready(rx, f"scanstart {channels} {args.preamble} {seq} {dwell_us} {int(args.trace)} {args.sf}", "listening", 1, seq, cached=True)
        if (reply.get("channels") != channels or reply.get("sf") != args.sf or reply.get("bw_khz") != 125
                or reply.get("preamble") != args.preamble or reply.get("dwell_us") != dwell_us
                or reply.get("trace") != args.trace):
            raise RuntimeError("Scanner configuration mismatch")
        print(f"Testing SF{args.sf}/125, {channels} channels; {args.samples} packets/channel, {args.preamble}-symbol preamble, {args.dwell_symbols:g}-symbol visits", flush=True)
        return reply

    def probe(channel, pause_ms):
        seq = next_sequence()
        armed_start = time.monotonic_ns()
        armed = exchange(rx, f"scanexpect {channel} {seq}", "listening", 1, seq, cached=True)
        armed_end = time.monotonic_ns()
        if armed.get("channel") != channel or not armed.get("scanning"):
            raise RuntimeError("Expectation mismatch")
        # USB acknowledgement retrieval may take time, but expectation arming
        # itself does not change the scanner's frequency or visit clock.
        time.sleep(pause_ms / 1000)
        with ThreadPoolExecutor(max_workers=1) as pool:
            incoming = pool.submit(read_packet_response, rx, "received", seq, 12)
            sent_start = time.monotonic_ns()
            sent = exchange(tx, f"scantx {channel} {seq} 16 {args.preamble} {args.sf}", "sent", 3, seq, cached=True)
            sent_end = time.monotonic_ns()
            received = incoming.result()
        if (sent.get("rc") != 0 or sent.get("channel") != channel or sent.get("len") != 16
                or sent.get("preamble") != args.preamble or sent.get("sf") != args.sf or sent.get("bw_khz") != 125):
            raise RuntimeError(f"Reference TX failed/mismatched; not a capacity result: {sent}")
        if received.get("device_errors") or (received.get("timeout") and received.get("rx_mode") != 1):
            raise RuntimeError(f"Receiver hardware failure; not a capacity result: {received}")
        valid = (received.get("valid") is True and received.get("len") == 16
                 and received.get("channel") == channel)
        trial = dict(sequence=seq, valid=valid, armed=armed, sent=sent, received=received,
                     host_times_ns=dict(arm_start=armed_start, arm_end=armed_end,
                                        tx_start=sent_start, tx_end=sent_end))
        if args.trace:
            trial["trace"] = collect_trace(rx, seq)
        print(f"Probe {seq}, channel {channel}: {'received' if valid else 'MISSED'}", flush=True)
        return trial

    def finish():
        seq = next_sequence()
        status = exchange(rx, f"scanstatus {seq}", "result", 1, seq, cached=True)
        if any(status[k] for k in ("failures", "rx_mode_errors", "cache_errors")):
            raise RuntimeError(f"Scanner failed; not a capacity result: {status}")
        if not status["switch"]["n"] or not status["optimized_rx_resumes"]:
            raise RuntimeError("Scanner did not exercise actual fast retunes")
        print(f"{status['channels']} channels: {status['received']} received, {status['missed']} missed; "
              f"switch mean {status['switch']['mean_us']:.1f} us; idle dwell mean {status['idle_dwell']['mean_us']:.1f} us", flush=True)
        return status

    try:
        rx, tx = open_port(args.receiver), open_port(args.sender)
        for port in (rx, tx):
            info = exchange(port, "info", "bench", 3)
            if (info.get("channel_sweep") != 1 or info.get("channel_sf_select") != 1
                    or (args.trace and info.get("channel_trace", 0) < 1)):
                raise RuntimeError("Firmware does not support the channel-capacity test")
        capacity_levels(start, probe, finish, save, args.samples, args.max_channels, args.seed, dwell_us)
        print(f"Stopped: {result['stopped']}; last perfect channel count: {result['last_perfect_channels']}", flush=True)
    except Exception as error:
        result.update(complete=False, stopped="fixture_error", error=str(error))
        raise
    finally:
        if rx:
            try:
                rx.write(b"scanstop\n")
                rx.flush()
            except Exception:
                pass
        for port in (rx, tx):
            if port:
                port.close()
        save()


if __name__ == "__main__":
    main()
