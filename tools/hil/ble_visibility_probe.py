#!/usr/bin/env python3
"""Owned, bounded BlueZ discovery; print only one explicitly selected peer.

Does not pair/connect, power adapters, open USB, or stop shared discovery.
All nearby-device output stays in process memory and is discarded.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAC = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")


def device_cache_state(output: str, target: str, returncode: int) -> bool | None:
    """True=exact successful info, False=explicit unavailable, None=query error."""
    header = re.compile(r"^Device\s+" + re.escape(target) + r"(?:\s+\((?:public|random)\))?\s*$", re.I)
    unavailable = re.compile(r"^Device\s+" + re.escape(target) + r"\s+not available\s*$", re.I)
    lines = ANSI.sub("", output).splitlines()
    headers = sum(bool(header.fullmatch(line)) for line in lines)
    absent = sum(bool(unavailable.fullmatch(line)) for line in lines)
    if returncode == 0 and headers == 1 and not absent:
        return True
    if returncode in (0, 1) and absent == 1 and not headers:
        return False
    return None


def is_cached_device(output: str, target: str, returncode: int) -> bool:
    """Only a successful info header proves a cached record, never its error."""
    return device_cache_state(output, target, returncode) is True


def parse_discovery(output: str, target: str) -> dict:
    started = False
    events = 0
    new_events = 0
    rssi = []
    for line in ANSI.sub("", output).splitlines():
        if "Discovery started" in line:
            started = True
        if not started or not re.search(r"\bDevice " + re.escape(target) + r"(?:\s|$)", line, re.I):
            continue
        if "[NEW]" in line or "[CHG]" in line:
            events += 1
            new_events += int("[NEW]" in line)
            value = re.search(r"RSSI:\s*(?:0x[0-9a-f]+\s*\()?(-\d+)\)?", line, re.I)
            if value:
                rssi.append(int(value[1]))
    return {
        "discovery_started": started,
        "target_events": events,
        "new_target_events": new_events,
        "rssi_updates": len(rssi),
        "rssi_dbm": rssi,
        # Startup cached-device enumeration is ignored until StartDiscovery succeeds.
        "advertisement_evidence": bool(rssi or new_events),
    }


def with_cache_evidence(scan: dict, before: bool | None, after: bool | None) -> dict:
    """Do not invent event counts when BlueZ prints its start marker late.

    A verified absent -> present transition during successful discovery is
    separate evidence of a newly learned target. It does not prove the exact
    advertising time/RSSI, nor attribution if another discovery client exists.
    Unknown/failed info queries never constitute verified prior absence.
    """
    result = dict(scan)
    created = bool(scan["discovery_started"] and before is False and after is True)
    event_evidence = bool(scan["advertisement_evidence"])
    sources = []
    if scan["rssi_updates"]:
        sources.append("post_start_rssi_update")
    if scan["new_target_events"]:
        sources.append("post_start_new_target_event")
    if created:
        sources.append("absent_before_present_after_successful_discovery")
    result.update(
        cached_before=before is True,
        cached_after=after is True,
        cache_query_before_verified=before is not None,
        cache_query_after_verified=after is not None,
        post_start_event_evidence=event_evidence,
        new_target_record_during_scan=created,
        advertisement_evidence=event_evidence or created,
        evidence_sources=sources,
    )
    return result


def usb_identity(alias: str, expected_serial: str) -> dict:
    path = Path(alias)
    if path.parent != Path("/dev/serial/by-id") or expected_serial not in path.name:
        raise ValueError("Explicit matching USB by-id identity required")
    resolved = path.resolve(strict=True)
    if not re.fullmatch(r"ttyACM\d+", resolved.name):
        raise ValueError("Expected native USB ACM radio")
    device = Path("/sys/class/tty") / resolved.name / "device"
    for parent in (device.resolve(), *device.resolve().parents):
        if (parent / "serial").is_file():
            serial = (parent / "serial").read_text().strip()
            vid = (parent / "idVendor").read_text().strip().lower()
            pid = (parent / "idProduct").read_text().strip().lower()
            if (serial, vid, pid) != (expected_serial, "239a", "8029"):
                raise ValueError("USB identity mismatch")
            return {"serial": serial, "vid": vid, "pid": pid, "port": str(resolved)}
    raise ValueError("USB identity unavailable")


def bluetooth(*args: str, timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bluetoothctl", *args], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=timeout,
        env={**os.environ, "LC_ALL": "C"}, check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--usb-by-id", required=True)
    parser.add_argument("--usb-serial", required=True)
    parser.add_argument("--seconds", type=int, default=20, choices=range(1, 21))
    args = parser.parse_args()
    target, controller = args.target.upper(), args.controller.upper()
    result = {"target": target, "duration_seconds": args.seconds}
    try:
        if not MAC.fullmatch(target) or not MAC.fullmatch(controller):
            raise ValueError("Exact peer and controller MAC required")
        result["usb_before"] = usb_identity(args.usb_by_id, args.usb_serial)
        adapter = bluetooth("show", controller)
        if not re.search(r"Powered:\s*yes\b", adapter.stdout):
            raise ValueError("Expected controller is not already powered")
        # Verify this is the selected/default controller, rather than changing it.
        default = bluetooth("show")
        if not re.search(r"Controller\s+" + re.escape(controller) + r"\b", default.stdout, re.I):
            raise ValueError("Expected controller is not the default")
        before = bluetooth("info", target)
        before_state = device_cache_state(before.stdout, target, before.returncode)
        scan = bluetooth("--timeout", str(args.seconds), "scan", "on", timeout=args.seconds + 5)
        result["scan_exit_code"] = scan.returncode
        after = bluetooth("info", target)
        after_state = device_cache_state(after.stdout, target, after.returncode)
        result.update(with_cache_evidence(parse_discovery(scan.stdout, target), before_state, after_state))
        result["usb_after"] = usb_identity(args.usb_by_id, args.usb_serial)
        result["success"] = result["discovery_started"]
    except (ValueError, OSError, subprocess.SubprocessError):
        result["success"] = False
        result["error"] = "Identity-bound discovery could not complete; raw output suppressed"
    print(json.dumps(result, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
