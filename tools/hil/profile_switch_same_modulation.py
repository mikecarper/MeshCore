"""RX-only same-image frequency-hop A/B. No packet is ever transmitted."""
import argparse
import json
from pathlib import Path
import secrets
import time

from profile_switch import exchange
from profile_switch_packets import open_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-rx-gain", choices=("normal", "boosted"), required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("use a fresh output file")
    expected_gain = 0x94 if args.expect_rx_gain == "normal" else 0x96
    result = dict(experiment="same_modulation_rx_only_ab", port=args.port, sf=6,
                  bw_khz=125, cr=5, channels=4, dwell_us=2612, preamble=32,
                  window_seconds=3, autonomous_tx=False, expected_rx_gain_reg=expected_gain,
                  order=[False, True, True, False], complete=False, trials=[])
    sequence = secrets.randbelow(0x3fffffff) + 1
    port = None
    try:
        for enabled in result["order"]:
            port = open_port(args.port)
            info = exchange(port, "info", "bench", 3)
            if info.get("modulation_cache_ab") != 1 or info.get("rx_gain_reg") != expected_gain:
                raise RuntimeError("Incorrect diagnostic capability or receiver gain")
            policy = exchange(port, f"scanmodcache {int(enabled)}", "modulation_cache", 2)
            if policy["modulation_cache"] != enabled:
                raise RuntimeError("Cache policy mismatch")
            sequence += 1
            armed = exchange(port, f"scanstart 4 32 {sequence} 2612 0 6", "listening", 3, sequence, cached=True)
            time.sleep(3)
            sequence += 1
            status = exchange(port, f"scanstatus {sequence}", "result", 3, sequence, cached=True)
            result["trials"].append(dict(enabled=enabled, config=armed, status=status))
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            if any(status[k] for k in ("failures", "rx_mode_errors", "cache_errors")):
                raise RuntimeError("Retune hardware/configuration check failed")
            if status["switch"]["n"] < 64 or status["rx_gain_reg"] != expected_gain:
                raise RuntimeError("Insufficient sample or receiver gain changed")
            if enabled and not status["without_modulation"]["n"]:
                raise RuntimeError("No modulation writes were skipped")
            if not enabled and status["with_modulation"]["n"] != status["switch"]["n"]:
                raise RuntimeError("Always-write control skipped a modulation command")
            print(f"{args.port} cache={enabled}: {status['switch']['n']} hops, "
                  f"mean={status['switch']['mean_us']:.3f} us, "
                  f"0x86={status['rf_commands']}, 0x8B={status['modulation_commands']}, "
                  f"gain=0x{status['rx_gain_reg']:02x}", flush=True)
            exchange(port, "scanstop", "stopped", 2)
            port.write(b"reboot\n")
            port.flush()
            port.close()
            port = None
            time.sleep(2)
        result["complete"] = True
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        if port:
            try:
                exchange(port, "scanstop", "stopped", 2)
                port.write(b"reboot\n")
                port.flush()
            finally:
                port.close()
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
