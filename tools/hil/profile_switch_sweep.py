"""Screen individual RX-to-RX optimizations and combinations. HIL firmware V8."""
import argparse
import json
from pathlib import Path
import serial
from profile_switch import exchange, exchange_ready, configure_session

CASES = [
    ("production_batch", 0, 2), ("manual_full_control", 128, 2),
    ("skip_rx_standby", 1, 2), ("skip_irq_rewrite", 2, 2),
    ("skip_buffer_rewrite", 4, 2), ("skip_packet_rewrite", 8, 2),
    ("skip_modem_query", 16, 2), ("defer_preamble", 32, 2),
    ("skip_awake_nop", 64, 2), ("combined", 95, 2),
    ("combined_deferred", 119, 2), ("spi4_only", 0, 4),
    ("spi8_only", 0, 8), ("combined_spi4", 119, 4),
    ("combined_spi8", 119, 8),
]
BULK_CASES = [
    ("production_batch", 0, 2), ("spi4_only", 0, 4), ("spi8_only", 0, 8),
    ("bulk_spi2", 256, 2), ("bulk_spi4", 256, 4), ("bulk_spi8", 256, 8),
    ("combined_spi8", 119, 8),
    ("combined_bulk_spi2", 375, 2), ("combined_bulk_spi4", 375, 4),
    ("combined_bulk_spi8", 375, 8),
]
PRODUCTION_CASES = [
    ("production_batch", 0, 2), ("production_fast_rx", 512, 2),
    ("production_bulk_spi8", 256, 8), ("production_fast_all", 768, 8),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--count", type=int, default=256)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--group", choices=("rx", "bulk", "production"), default="production")
    args = parser.parse_args()
    if args.output.exists() or not 16 <= args.count <= 1000 or not 1 <= args.rounds <= 10:
        parser.error("use a fresh output, count 16..1000, rounds 1..10")
    cases = {"rx": CASES, "bulk": BULK_CASES, "production": PRODUCTION_CASES}[args.group]
    results = {"schema": 8, "experiment": "rx_setup_and_spi", "group": args.group, "port": args.port,
               "cases": cases, "trials": [], "complete": False,
               "scope": "RX-to-RX only; warm + batched; timings exclude application scheduling"}
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, 115200, 0.25
    port.write_timeout = 2
    configure_session(port)
    try:
        port.open()
        port.reset_input_buffer()
        info = exchange(port, "info", "bench", 5)
        if info.get("bench") != "production-profile-switch-v8" or not info.get("ready"):
            raise RuntimeError("Wrong/unready timing firmware")
        for repeat in range(args.rounds):
            for sf in (7, 8, 9):
                for name, mask, mhz in (cases if repeat % 2 == 0 else list(reversed(cases))):
                    result = exchange_ready(port, f"run 1 {sf} {args.count} 1 {mask} {mhz}", "result", 35)
                    result.update(case=name, round=repeat + 1)
                    results["trials"].append(result)
                    args.output.write_text(json.dumps(results, indent=2) + "\n")
                    samples = sum(d["total"]["n"] for d in result["directions"])
                    mean = sum(d["total"]["mean_us"] * d["total"]["n"]
                               for d in result["directions"]) / max(samples, 1)
                    maximum = max(d["total"]["max_us"] for d in result["directions"])
                    print(f"round={repeat+1} SF{sf}/500 {name}: {mean:.2f} us mean, {maximum} us max", flush=True)
                    if (samples != args.count or not result["warm"] or not result["batched"]
                            or result["mask"] != mask or result["spi_mhz"] != mhz
                            or result["sf500"] != sf or not result["off_restored_rc"]
                            or (mask & 512 and result["optimized_rx_resumes"] < args.count)
                            or (not mask & 512 and result["optimized_rx_resumes"] != 0)
                            or any(result[k] for k in ("failures", "busy_timeouts", "rx_mode_errors", "cache_errors"))):
                        raise RuntimeError(f"Failed trial: {name}; partial results saved")
        results["complete"] = True
    finally:
        port.close()
        if results["trials"]:
            args.output.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
