#!/usr/bin/env python3
"""Verify promised firmware capabilities and emit a machine-readable manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import struct
import zipfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--artifact-target")
    parser.add_argument("--platformio-env")
    parser.add_argument("--platform", required=True)
    parser.add_argument("--build-profile", required=True)
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--reduction", action="append", default=[])
    parser.add_argument("--require-ota", action="store_true")
    parser.add_argument("--firmware-bin", type=Path)
    parser.add_argument("--partitions", type=Path)
    parser.add_argument("--dfu-package", type=Path)
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="CAPABILITY=TEXT",
        help="Require TEXT to be present in the linked image.",
    )
    return parser.parse_args()


def stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def verify_ota_artifacts(args, methods):
    """Check the actual update medium; source/seeder support cannot qualify."""
    if not methods:
        return False, "no linked wireless self-update implementation"
    try:
        if args.platform == "ESP32_PLATFORM":
            if args.partitions is None or args.firmware_bin is None:
                return False, "ESP32 OTA requires firmware.bin and partitions.bin"
            table = args.partitions.read_bytes()
            slots = {}
            otadata = False
            for offset in range(0, len(table) - 31, 32):
                magic, kind, subtype, address, size = struct.unpack_from("<HBBII", table, offset)
                if magic != 0x50AA:
                    continue
                if kind == 0 and subtype in (0x10, 0x11):
                    slots[subtype] = (address, size)
                if kind == 1 and subtype == 0 and size >= 0x2000:
                    otadata = True
            if len(slots) != 2 or not otadata:
                return False, "ESP32 OTA requires ota_0, ota_1, and otadata"
            image_size = args.firmware_bin.stat().st_size
            ordered = sorted(slots.values())
            if ordered[0][0] + ordered[0][1] > ordered[1][0]:
                return False, "ESP32 OTA application slots overlap"
            if image_size <= 0 or any(image_size > size for _, size in ordered):
                return False, "ESP32 firmware does not fit both OTA application slots"
            return True, "firmware fits both OTA application slots; otadata present"
        if args.platform == "NRF52_PLATFORM" and "bluetooth" in methods:
            if args.dfu_package is None:
                return False, "Bluetooth DFU requires its application ZIP"
            with zipfile.ZipFile(args.dfu_package) as package:
                manifest = json.loads(package.read("manifest.json"))["manifest"]
                application = manifest["application"]
                if not package.read(application["bin_file"]) or not package.read(application["dat_file"]):
                    return False, "Bluetooth DFU application ZIP is empty"
                if package.testzip() is not None:
                    return False, "Bluetooth DFU application ZIP is corrupt"
            return True, "application DFU ZIP verified; matching BLE DFU bootloader required"
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        return False, str(exc)
    return False, "no qualified wireless update artifact for this platform"


def main() -> int:
    args = parse_args()
    try:
        image = args.image.read_bytes()
    except OSError as exc:
        print(f"capability check: cannot read {args.image}: {exc}", file=sys.stderr)
        return 2

    checks = []
    malformed = False
    for expectation in args.expect:
        if "=" not in expectation:
            print(
                f"capability check: malformed --expect {expectation!r}",
                file=sys.stderr,
            )
            malformed = True
            continue
        capability, needle = expectation.split("=", 1)
        present = bool(needle) and needle.encode("utf-8") in image
        checks.append(
            {
                "capability": capability,
                "evidence": needle,
                "present": present,
            }
        )

    capabilities = stable_unique(
        args.capability + [check["capability"] for check in checks]
    )
    missing = [check for check in checks if not check["present"]]
    ota_methods = stable_unique([
        check["capability"].removeprefix("ota.update.")
        for check in checks
        if check["present"] and check["capability"].startswith("ota.update.")
    ])
    ota_verified, ota_evidence = verify_ota_artifacts(args, ota_methods)
    if not ota_verified:
        capabilities = [value for value in capabilities if not value.startswith("ota.update.")]
    manifest = {
        "schema_version": 2,
        "target": args.target,
        "artifact_target": args.artifact_target or args.target,
        "platformio_env": args.platformio_env or args.target,
        "platform": args.platform,
        "build_profile": args.build_profile,
        "capabilities": capabilities,
        "reductions": stable_unique(args.reduction),
        "verification": checks,
        "ota_update_methods": ota_methods if ota_verified else [],
        "ota_update_verified": ota_verified,
        "ota_update_evidence": ota_evidence,
        "verified": not malformed and not missing and (not args.require_ota or ota_verified),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)

    if malformed:
        return 2
    if args.require_ota and not ota_verified:
        print(f"capability check failed: wireless self-update: {ota_evidence}", file=sys.stderr)
        return 1
    if missing:
        for check in missing:
            print(
                "capability check failed: "
                f"{check['capability']} promised but {check['evidence']!r} "
                f"is absent from {args.image}",
                file=sys.stderr,
            )
        return 1

    print(
        f"Verified {len(checks)} capability marker(s); manifest: {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
