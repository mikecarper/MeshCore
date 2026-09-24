#!/usr/bin/env python3
"""Package the two-stage Wi-Fi and LoRa ESP32 partition migrations."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mota"))
from motalib import (  # noqa: E402
    CODEC_FULL, FwIdent, build_container, build_manifest, ensure_endf,
    hardware_id_for_env, pack_version, parse_container, parse_endf_ident,
    target_id_for_env, verify,
)
from check_esp32_app_size import app_partition_size  # noqa: E402
from firmware_memory_manifest import validate_package  # noqa: E402


BOARDS = {
    "heltec-v4": {
        "target": "heltec_v4_repeater",
        "full_env": "heltec_v4_repeater_observer_mqtt",
        "wifi_bridge": "heltec_v4_partition_migrator",
        "lora_bridge": "heltec_v4_partition_migrator_lora_repeater",
        "flash_bytes": 16 * 1024 * 1024,
        "slot_bytes": 0x640000,
        "slot1_address": 0x650000,
    },
    "xiao-s3-wio": {
        "target": "Xiao_S3_WIO_repeater",
        "full_env": "Xiao_S3_WIO_repeater_observer_mqtt",
        "wifi_bridge": "xiao_s3_partition_migrator",
        "lora_bridge": "xiao_s3_partition_migrator_lora_repeater",
        "flash_bytes": 8 * 1024 * 1024,
        "slot_bytes": 0x330000,
        "slot1_address": 0x340000,
    },
}
LEGACY_SLOT_BYTES = 0x140000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def partition_entries(table: bytes) -> dict[str, tuple[int, int]]:
    entries = {}
    for offset in range(0, len(table) - 31, 32):
        entry = table[offset:offset + 32]
        if entry[:2] != b"\xaa\x50":
            continue
        address, size = struct.unpack_from("<II", entry, 4)
        label = entry[12:28].split(b"\0", 1)[0].decode("ascii")
        entries[label] = (address, size)
    return entries


def mota_full(image: bytes, identity: FwIdent) -> bytes:
    manifest = build_manifest(
        target_id=identity.target_id,
        fw_version=identity.fw_version,
        image_size=len(image),
        payload=image,
        block_size=1024,
        image_hash=hashlib.sha256(image).digest(),
        codec_id=CODEC_FULL,
        is_full=True,
        hw_id=identity.hw_id,
    )
    package = build_container(manifest, image)
    problems = verify(parse_container(package))
    if problems:
        raise ValueError("mOTA verification failed: " + "; ".join(problems))
    return package


def readme(board: str, spec: dict, version: str, source: str) -> str:
    return f"""# {board} in-place partition migration

Source: `{source}`; firmware version: `{version}`. This package is only for the
exact `{spec['target']}` board/role with {spec['flash_bytes'] // 1048576} MiB flash.
The current partition table must have stable NVS at 0x9000, OTA metadata at
0xE000, two OTA apps, and SPIFFS with `/identity/_main.id`.

The bridge checks the live layout and refuses unsupported boards. It stages
the 96-byte public/private identity in unchanged NVS, copies and verifies the
required application, changes the partition table, then restores and verifies
the identity in expanded SPIFFS. No full-chip erase or USB connection is used.
The table sector and inactive app sectors are erased as part of migration.
SPIFFS may be reformatted; other SPIFFS settings can be recreated.

## Wi-Fi route

1. Upload `wifi-bridge.bin` through the existing repeater's Wi-Fi OTA page.
2. Keep power stable. Join `MeshCore-Migrate` (password `meshcore-migrate`),
   open `http://192.168.4.1/`, and wait for **Expanded layout ready**.
3. At that page's `/update`, upload `full-application.bin`.

## LoRa route

The old repeater must already have working MeshCore LoRa mOTA, a valid EndF,
and the exact target ID in `manifest.json`. A stock image without that updater
cannot take this route. The bridge checks the old receiver before changing the
table and refuses if its image or target does not match.

1. Make `lora-bridge.mota` available from a compatible mOTA seeder. On the old
   repeater use `ota ls`, `ota pull <id> flash`, then `ota install` after the
   complete download. A manual install handles an equal-version bridge.
2. Keep power stable. The bridge expands the table, restores the private key,
   and reboots into the preserved old LoRa repeater in the opposite slot.
3. Serve `full-application.mota`; again use `ota ls`, `ota pull <id> flash`,
   then `ota install`. The old repeater now sees the expanded inactive slot.
   A full-image LoRa transfer can take considerable time.

`full-application.bin` and `full-application.mota` are the same exact-target
Full repeater image. `target-partitions.bin` is included for verification only;
do not upload it to the browser. Neither bridge is a USB merged image.
"""


def package_board(name: str, spec: dict, build_dir: Path, output_dir: Path,
                  version: str, source: str) -> Path:
    target = spec["target"]
    target_id = target_id_for_env(target)
    hw_id = hardware_id_for_env(target)
    stem = f"{target}-full-usb-wifi-ota-{version}-{source}"
    full_path = build_dir / (stem + ".bin")
    capability_path = build_dir / (stem + ".capabilities.json")
    validate_package(build_dir / stem)
    capabilities = json.loads(capability_path.read_text())
    if (capabilities.get("target") != target
            or capabilities.get("build_profile") != "full"
            or capabilities.get("platform") != "ESP32_PLATFORM"
            or not capabilities.get("ota_update_verified")):
        raise ValueError(f"{name}: Full build has the wrong target or lacks OTA")

    table_path = ROOT / ".pio" / "build" / spec["full_env"] / "partitions.bin"
    table = table_path.read_bytes()
    entries = partition_entries(table)
    if (entries.get("nvs") != (0x9000, 0x5000)
            or entries.get("otadata") != (0xE000, 0x2000)
            or entries.get("app0") != (0x10000, spec["slot_bytes"])
            or entries.get("app1") != (spec["slot1_address"], spec["slot_bytes"])):
        raise ValueError(f"{name}: built partition table differs from the migration plan")
    _label, slot_bytes = app_partition_size(table)
    full_image = full_path.read_bytes()
    full_ident = parse_endf_ident(full_image)
    if (full_ident is None or full_ident.target_id != target_id
            or full_ident.hw_id != hw_id or len(full_image) >= slot_bytes):
        raise ValueError(f"{name}: Full image identity or partition fit failed")

    wifi_bridge = (ROOT / ".pio" / "build" / spec["wifi_bridge"] / "firmware.bin").read_bytes()
    lora_bridge_body = (ROOT / ".pio" / "build" / spec["lora_bridge"] / "firmware.bin").read_bytes()
    bridge_ident = FwIdent(pack_version(version.split("-", 1)[0]), target_id, hw_id)
    lora_bridge, _ = ensure_endf(lora_bridge_body, bridge_ident)
    if max(len(wifi_bridge), len(lora_bridge)) > LEGACY_SLOT_BYTES:
        raise ValueError(f"{name}: bridge does not fit the 1.25 MiB legacy slot")
    if full_ident.fw_version != bridge_ident.fw_version:
        raise ValueError(f"{name}: bridge and Full image versions differ")

    files = {
        "wifi-bridge.bin": wifi_bridge,
        "lora-bridge.mota": mota_full(lora_bridge, bridge_ident),
        "full-application.bin": full_image,
        "full-application.mota": mota_full(full_image, full_ident),
        "target-partitions.bin": table,
        "capabilities.json": capability_path.read_bytes(),
    }
    files["README.md"] = readme(name, spec, version, source).encode()
    manifest = {
        "board": name,
        "target": target,
        "target_id": f"0x{target_id:08x}",
        "hardware_id": hw_id,
        "source_commit": source,
        "firmware_version": version,
        "flash_bytes": spec["flash_bytes"],
        "legacy_slot_bytes": LEGACY_SLOT_BYTES,
        "expanded_slot_bytes": slot_bytes,
        "files": {filename: {"bytes": len(data), "sha256": sha256(data)}
                  for filename, data in files.items()},
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    files["SHA256SUMS.txt"] = "".join(
        f"{sha256(data)}  {filename}\n" for filename, data in sorted(files.items())
    ).encode()
    directory = output_dir / name
    directory.mkdir(parents=True)
    for filename, data in files.items():
        (directory / filename).write_bytes(data)
    archive = output_dir / f"{name}-{version}-{source}-migration.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zip_file:
        for filename in sorted(files):
            zip_file.write(directory / filename, arcname=filename)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source", required=True, help="eight-character source commit")
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output directory must be empty")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archives = [package_board(name, spec, args.build_dir, args.output_dir,
                              args.version, args.source)
                for name, spec in BOARDS.items()]
    for archive in archives:
        print(f"{archive}: {archive.stat().st_size} bytes")


if __name__ == "__main__":
    main()
