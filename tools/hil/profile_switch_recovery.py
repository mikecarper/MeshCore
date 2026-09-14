"""Verify HIL USB replay/correlation and fast-RX lifecycle invalidation.

Explicitly sends five 16-byte, -9 dBm probes per board (two USB, three TX
lifecycle). Reboots the HIL firmware after the direct-chip lifecycle checks.
"""
import argparse
import json
from pathlib import Path
import time

from profile_switch import exchange, read_packet_response
from profile_switch_packets import open_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose a fresh output file")
    result = {"schema": 8, "port": args.port, "complete": False, "reconnects": 0}
    port = None
    try:
        # Opening native USB must activate CDC, while opening the CH340 must
        # not hold BOOT asserted. No MCU reset is requested during these opens.
        for _ in range(20):
            port = open_port(args.port)
            port.close()
            port = None
            result["reconnects"] += 1
        port = open_port(args.port)
        measured = exchange(port, "run 1 7 16 1 768 8", "result", 8)
        replayed = read_packet_response(port, "result", measured["result"], 0.3)
        result["lost_timing_reply"] = replayed
        if not replayed.get("transport_recovered") or any(replayed[k] for k in
                ("failures", "busy_timeouts", "rx_mode_errors", "cache_errors")):
            raise RuntimeError("Cached timing result was not recovered")
        command = "tx 7 0 900001 16 32 -9 8 1"
        original = exchange(port, command, "sent", 6, 900001, cached=True)
        if original.get("rc") != 0:
            raise RuntimeError("USB probe TX failed")
        # Simulate a lost response by consuming it above, then deliberately
        # time out. Retrieval must return the cached result, without more TX.
        recovered = read_packet_response(port, "sent", 900001, 0.3)
        result["lost_reply"] = recovered
        if not recovered.get("transport_recovered") or recovered["rc"] != 0:
            raise RuntimeError("Cached TX result was not recovered")
        # Queue a stale acknowledgement, then issue a new, distinct sequence.
        port.write(b"result sent 900001\n")
        matched = exchange(port, "tx 7 0 900002 16 32 -9 8 1", "sent", 6, 900002, cached=True)
        result["late_reply"] = matched
        if matched.get("rc") != 0 or [r["sent"] for r in matched.get("stale_replies", [])] != [900001]:
            raise RuntimeError("Stale TX acknowledgement was not isolated")
        result["lifecycle"] = exchange(port, "lifecycle", "lifecycle", 30)
        checks = result["lifecycle"]["checks"]
        if len(checks) != 18 or any(c["rc"] or c["initial_fast"] != (c["operation"] != "sleep_rc")
                or c["fallback_fast"] != 0 or c["resumed_fast"] != (c["operation"] != "sleep_rc")
                or c["rx_mode"] != 1 or c["device_errors"] for c in checks):
            raise RuntimeError("Lifecycle invalidation/recovery failed")
        result["complete"] = True
    finally:
        if port:
            try:
                port.write(b"reboot\n")
            finally:
                port.close()
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    time.sleep(2)
    port = open_port(args.port)
    port.close()
    print(f"{args.port}: 20 reconnects, lost/stale reply recovery, 18 lifecycle checks passed")


if __name__ == "__main__":
    main()
