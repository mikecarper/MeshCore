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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "mota"))
from motalib import parse_nrf52_layout


def nrf52_lora_details(application):
    """Inspect application bytes, not just an ELF symbol or an OTA CLI stub."""
    if b"invalid in-place patch geometry" not in application or b"OTA: status" not in application:
        raise ValueError("nRF52 LoRa OTA receiver/apply implementation is missing")
    layout = parse_nrf52_layout(application)
    if layout is None:
        raise ValueError("nRF52 LoRa OTA requires a valid EndF and storage-layout record")
    if len(application) > layout.linked_app_end - layout.app_base:
        raise ValueError("nRF52 LoRa OTA application exceeds its linked flash region")
    storage = ("adaptive_internal_or_external_qspi" if layout.auto_store
               else "external_qspi" if layout.qspi_backed else "external_sd" if layout.sd_backed
               else "internal_flash_and_retained_ram" if layout.hybrid_ram else "internal_flash")
    notes = ["Use a destination-specific .mota package matching the board, target, and storage layout.",
             "An in-place delta requires the exact firmware currently running as its base.",
             "The package must fit the receiver's staging and apply workspace.",
             "Artifact inspection verifies compiled support; it does not verify the bootloader installed on a physical device."]
    if layout.auto_store:
        notes.append("One application detects RAK external NOR and chooses QSPI only with the exact matching OTAFIX bootloader; otherwise it uses internal flash where safe.")
        notes.append("External QSPI accepts full images and in-place deltas; internal flash accepts in-place deltas only and needs the retained-RAM OTAFIX profile.")
    elif layout.hybrid_ram:
        notes.append("Requires OTAFIX 2.4.6 retained-RAM handoff support; a transfer cannot resume after the receiver restarts.")
    elif layout.external_backed:
        notes.append("Requires the matching external-storage hardware, wiring, and storage-aware OTAFIX bootloader.")
    else:
        notes.append("Internal storage accepts in-place application deltas, not full application packages.")
    return {
        "storage": storage,
        "package_types": ["full", "in_place_delta"] if layout.auto_store or layout.external_backed else ["in_place_delta"],
        "bootloader": "Matching board/storage OTAFIX bootloader",
        "bootloader_release": "https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.11.0-OTAFIX2.4.6",
        "notes": notes,
    }


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
    parser.add_argument(
        "--expect-application", action="append", default=[],
        metavar="CAPABILITY=TEXT",
        help="Require TEXT in the packaged firmware.bin or DFU application, not ELF metadata.",
    )
    return parser.parse_args()


def stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def read_application(args):
    if args.firmware_bin is not None:
        return args.firmware_bin.read_bytes()
    if args.dfu_package is not None:
        with zipfile.ZipFile(args.dfu_package) as package:
            app = json.loads(package.read("manifest.json"))["manifest"]["application"]
            return package.read(app["bin_file"])
    raise ValueError("application checks require --firmware-bin or --dfu-package")


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
        if args.platform == "NRF52_PLATFORM" and set(methods) <= {"bluetooth", "lora"}:
            if args.dfu_package is None:
                return False, "nRF52 OTA verification requires its application DFU ZIP"
            with zipfile.ZipFile(args.dfu_package) as package:
                manifest = json.loads(package.read("manifest.json"))["manifest"]
                application = manifest["application"]
                image = package.read(application["bin_file"])
                if not image or not package.read(application["dat_file"]):
                    return False, "Bluetooth DFU application ZIP is empty"
                if package.testzip() is not None:
                    return False, "Bluetooth DFU application ZIP is corrupt"
            evidence = []
            if "bluetooth" in methods:
                evidence.append("application DFU ZIP verified; matching BLE DFU bootloader required")
            if "lora" in methods:
                if "companion" in args.target.lower() or "comp_radio" in args.target.lower():
                    return False, "Companion MOTA sending does not qualify as LoRa self-update"
                details = nrf52_lora_details(image)
                evidence.append("LoRa mOTA receiver/apply code and EndF storage layout verified: " + details["storage"] +
                                "; matching board/storage OTAFIX bootloader and compatible destination package required")
            return True, "; ".join(evidence)
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
    application = b""
    if args.expect_application:
        try:
            application = read_application(args)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            print(f"capability check: cannot read packaged application: {exc}", file=sys.stderr)
            malformed = True
    expectations = [(value, image, "linked image") for value in args.expect]
    expectations += [(value, application, "packaged application") for value in args.expect_application]
    for expectation, content, source in expectations:
        if "=" not in expectation:
            print(
                f"capability check: malformed expectation {expectation!r}",
                file=sys.stderr,
            )
            malformed = True
            continue
        capability, needle = expectation.split("=", 1)
        present = bool(needle) and needle.encode("utf-8") in content
        checks.append(
            {
                "capability": capability,
                "evidence": needle,
                "present": present,
                "source": source,
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
    if ota_verified and args.platform == "NRF52_PLATFORM" and "lora" in ota_methods:
        with zipfile.ZipFile(args.dfu_package) as package:
            app = json.loads(package.read("manifest.json"))["manifest"]["application"]
            manifest["ota_update_requirements"] = {"lora": nrf52_lora_details(package.read(app["bin_file"]))}

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
                f"is absent from the {check['source']}",
                file=sys.stderr,
            )
        return 1

    print(
        f"Verified {len(checks)} capability marker(s); manifest: {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
