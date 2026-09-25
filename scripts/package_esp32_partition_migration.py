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
    CODEC_FULL, FwIdent, build_container, build_endf, build_manifest, has_endf,
    hardware_id_for_env, pack_version, parse_container, parse_endf,
    parse_endf_ident,
    target_id_for_env, verify,
)
from check_esp32_app_size import app_partition_size  # noqa: E402
from firmware_memory_manifest import validate_package  # noqa: E402


BOARDS = {
    "heltec-v4": {
        "target": "heltec_v4_repeater",
        "expander_bridge": "heltec_v4_partition_expander",
        "wifi_bridge": "heltec_v4_partition_migrator",
        "lora_bridge": "heltec_v4_partition_migrator_lora_repeater",
        "flash_bytes": 16 * 1024 * 1024,
        "slot_bytes": 0x640000,
        "slot1_address": 0x650000,
    },
    "xiao-s3-wio": {
        "target": "Xiao_S3_WIO_repeater",
        "expander_bridge": "Xiao_S3_WIO_partition_expander",
        "wifi_bridge": "xiao_s3_partition_migrator",
        "lora_bridge": "xiao_s3_partition_migrator_lora_repeater",
        "flash_bytes": 8 * 1024 * 1024,
        "slot_bytes": 0x330000,
        "slot1_address": 0x340000,
    },
    "station-g2-repeater": {
        "target": "Station_G2_repeater",
        "wifi_bridge": "esp32_s3_16mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 16 * 1024 * 1024,
        "slot_bytes": 0x640000,
        "slot1_address": 0x650000,
    },
    "station-g2-room-server": {
        "target": "Station_G2_room_server",
        "wifi_bridge": "esp32_s3_16mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 16 * 1024 * 1024,
        "slot_bytes": 0x640000,
        "slot1_address": 0x650000,
    },
    "t3s3-sx1262-repeater": {
        "target": "LilyGo_T3S3_sx1262_repeater",
        "wifi_bridge": "esp32_s3_4mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 4 * 1024 * 1024,
        "slot_bytes": 0x1F0000,
        "slot1_address": 0x200000,
    },
    "t3s3-sx1262-room-server": {
        "target": "LilyGo_T3S3_sx1262_room_server",
        "wifi_bridge": "esp32_s3_4mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 4 * 1024 * 1024,
        "slot_bytes": 0x1F0000,
        "slot1_address": 0x200000,
    },
    "t3s3-sx1276-repeater": {
        "target": "LilyGo_T3S3_sx1276_repeater",
        "wifi_bridge": "esp32_s3_4mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 4 * 1024 * 1024,
        "slot_bytes": 0x1F0000,
        "slot1_address": 0x200000,
    },
    "t3s3-sx1276-room-server": {
        "target": "LilyGo_T3S3_sx1276_room_server",
        "wifi_bridge": "esp32_s3_4mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 4 * 1024 * 1024,
        "slot_bytes": 0x1F0000,
        "slot1_address": 0x200000,
    },
    "thinknode-m2-repeater": {
        "target": "ThinkNode_M2_Repeater",
        "wifi_bridge": "esp32_s3_4mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 4 * 1024 * 1024,
        "slot_bytes": 0x1F0000,
        "slot1_address": 0x200000,
    },
    "thinknode-m2-room-server": {
        "target": "ThinkNode_M2_room_server",
        "wifi_bridge": "esp32_s3_4mb_partition_migrator",
        "lora_bridge": None,
        "flash_bytes": 4 * 1024 * 1024,
        "slot_bytes": 0x1F0000,
        "slot1_address": 0x200000,
    },
}


def role_for_target(target: str) -> str:
    lower = target.lower()
    if "_room_server" in lower:
        return "room-server"
    if "_repeater" in lower:
        return "repeater"
    if "_sensor" in lower:
        return "sensor"
    raise ValueError(f"migration target has no infrastructure role: {target}")


def add_roles(family: str, flash_mib: int,
              targets: dict[str, str],
              bridge: str | None = None) -> None:
    """Register exact role identities; a family can have one or all roles."""
    if flash_mib not in (4, 8, 16):
        raise ValueError(f"unsupported migration flash size: {flash_mib} MiB")
    slot_bytes, slot1_address = {
        4: (0x1F0000, 0x200000),
        8: (0x330000, 0x340000),
        16: (0x640000, 0x650000),
    }[flash_mib]
    if bridge is None:
        bridge = f"esp32_s3_{flash_mib}mb_partition_migrator"
    for role, target in targets.items():
        if role not in ("repeater", "room-server", "sensor"):
            raise ValueError(f"unsupported migration role: {role}")
        key = f"{family}-{role}"
        if key in BOARDS:
            raise ValueError(f"duplicate migration board role: {key}")
        BOARDS[key] = {
            "target": target,
            "role": role,
            "wifi_bridge": bridge,
            "lora_bridge": None,
            "flash_bytes": flash_mib * 1024 * 1024,
            "slot_bytes": slot_bytes,
            "slot1_address": slot1_address,
        }


# The first two repeater keys above retain their published LoRa-capable bridge
# names. Other role packages use a chip-family bridge for both Wi-Fi and LoRa.
# These are the canonical ESP32 infrastructure identities on 4/8/16 MiB boards which can
# have a deployed Arduino default.csv 1.25 MiB table, including historically
# default.csv boards now using a larger layout. Other boards that were only
# ever built with unrelated layouts are not represented as 1.25 MiB migrants.
for family, flash_mib, roles in (
    ("heltec-v4", 16, {"room-server": "heltec_v4_room_server",
                       "sensor": "heltec_v4_sensor"}),
    ("heltec-v4-expansionkit", 16, {
        "repeater": "heltec_v4_expansionkit_repeater"}),
    ("xiao-s3-wio", 8, {"room-server": "Xiao_S3_WIO_room_server",
                          "sensor": "Xiao_S3_WIO_sensor"}),
    ("nibble-zero", 4, {"repeater": "nibble_zero_connect_repeater_",
                        "room-server": "nibble_zero_connect_room_server_"}),
    ("nibble-screen", 4, {"repeater": "nibble_screen_connect_repeater_",
                          "room-server": "nibble_screen_connect_room_server_"}),
    ("ebyte-eora-s3", 4, {"repeater": "Ebyte_EoRa-S3_Repeater",
                           "room-server": "Ebyte_EoRa-S3_room_server"}),
    ("thinknode-m5", 4, {"repeater": "ThinkNode_M5_Repeater",
                            "room-server": "ThinkNode_M5_room_server"}),
    ("heltec-wireless-tracker", 8, {
        "repeater": "Heltec_Wireless_Tracker_repeater",
        "room-server": "Heltec_Wireless_Tracker_room_server"}),
    ("heltec-tracker-v2", 8, {"repeater": "heltec_tracker_v2_repeater",
                               "room-server": "heltec_tracker_v2_room_server",
                               "sensor": "heltec_tracker_v2_sensor"}),
    ("heltec-v3", 8, {"repeater": "Heltec_v3_repeater",
                      "room-server": "Heltec_v3_room_server",
                      "sensor": "Heltec_v3_sensor"}),
    ("heltec-wsl3", 8, {"repeater": "Heltec_WSL3_repeater",
                        "room-server": "Heltec_WSL3_room_server",
                        "sensor": "Heltec_WSL3_sensor"}),
    ("heltec-wireless-paper", 8, {
        "repeater": "Heltec_Wireless_Paper_repeater",
        "room-server": "Heltec_Wireless_Paper_room_server"}),
    ("t-beam-s3-supreme-sx1262", 8, {
        "repeater": "T_Beam_S3_Supreme_SX1262_repeater",
        "room-server": "T_Beam_S3_Supreme_SX1262_room_server"}),
    ("thinknode-m7", 8, {"repeater": "ThinkNode_M7_repeater",
                            "room-server": "ThinkNode_M7_room_server"}),
    ("xiao-s3", 8, {"repeater": "Xiao_S3_repeater",
                       "room-server": "Xiao_S3_room_server",
                       "sensor": "Xiao_S3_sensor"}),
    ("mke-s3", 8, {"repeater": "MKE_s3_repeater",
                      "room-server": "MKE_s3_room_server",
                      "sensor": "MKE_s3_sensor"}),
    ("rak-3112", 8, {"repeater": "RAK_3112_repeater",
                        "room-server": "RAK_3112_room_server",
                        "sensor": "RAK_3112_sensor"}),
    ("heltec-e213", 16, {"repeater": "Heltec_E213_repeater",
                           "room-server": "Heltec_E213_room_server"}),
    ("heltec-e290", 16, {"repeater": "Heltec_E290_repeater",
                           "room-server": "Heltec_E290_room_server"}),
    ("heltec-rc32", 16, {"repeater": "heltec_rc32_repeater",
                           "room-server": "heltec_rc32_room_server",
                           "sensor": "heltec_rc32_sensor"}),
    ("heltec-rc32-no-display", 16, {
        "repeater": "heltec_rc32_without_display_repeater",
        "room-server": "heltec_rc32_without_display_room_server",
        "sensor": "heltec_rc32_without_display_sensor"}),
    ("heltec-t190", 16, {"repeater": "Heltec_T190_repeater_",
                           "room-server": "Heltec_T190_room_server_"}),
    ("heltec-v4-tft", 16, {"repeater": "heltec_v4_tft_repeater",
                             "room-server": "heltec_v4_tft_room_server",
                             "sensor": "heltec_v4_tft_sensor"}),
    ("heltec-v4-r8", 16, {"repeater": "heltec_v4_r8_repeater",
                            "room-server": "heltec_v4_r8_room_server",
                            "sensor": "heltec_v4_r8_sensor"}),
    ("heltec-v4-r8-tft", 16, {"repeater": "heltec_v4_r8_tft_repeater",
                                "room-server": "heltec_v4_r8_tft_room_server",
                                "sensor": "heltec_v4_r8_tft_sensor"}),
    ("lilygo-tbeam-1w", 16, {"repeater": "LilyGo_TBeam_1W_repeater",
                               "room-server": "LilyGo_TBeam_1W_room_server"}),
    ("lilygo-tdeck", 16, {"repeater": "LilyGo_TDeck_repeater"}),
    ("lilygo-teth-elite-sx1262", 16, {
        "repeater": "LilyGo_TETH_Elite_sx1262_repeater",
        "room-server": "LilyGo_TETH_Elite_sx1262_room_server"}),
    ("meshnology-w12", 16, {"repeater": "meshnology_w12_repeater",
                             "room-server": "meshnology_w12_room_server",
                             "sensor": "meshnology_w12_sensor"}),
    ("station-g3-esp32", 16, {"repeater": "Station_G3_ESP32_repeater",
                              "room-server": "Station_G3_ESP32_room_server"}),
    ("station-g3-esp32-logging", 16, {
        "repeater": "Station_G3_ESP32_logging_repeater"}),
    ("station-g2-logging", 16, {
        "repeater": "Station_G2_logging_repeater"}),
    ("thinknode-m9", 16, {"repeater": "ThinkNode_M9_repeater_",
                             "room-server": "ThinkNode_M9_room_server_"}),
):
    add_roles(family, flash_mib, roles)

add_roles("heltec-v2", 8, {
    "repeater": "Heltec_v2_repeater",
    "room-server": "Heltec_v2_room_server",
}, bridge="esp32_8mb_partition_migrator")
add_roles("heltec-ct62", 4, {
    "repeater": "Heltec_ct62_repeater",
    "sensor": "Heltec_ct62_sensor",
}, bridge="esp32_c3_4mb_partition_migrator")
add_roles("xiao-c3", 4, {
    "repeater": "Xiao_C3_repeater",
    "room-server": "Xiao_C3_room_server",
}, bridge="esp32_c3_4mb_partition_migrator")
add_roles("tenstar-c3-sx1262", 4, {
    "repeater": "Tenstar_C3_sx1262_repeater",
}, bridge="esp32_c3_4mb_partition_migrator")
add_roles("tenstar-c3-sx1268", 4, {
    "repeater": "Tenstar_C3_sx1268_repeater",
}, bridge="esp32_c3_4mb_partition_migrator")
add_roles("generic-e22-sx1262", 4, {
    "repeater": "Generic_E22_sx1262_repeater",
}, bridge="esp32_4mb_partition_migrator")
add_roles("generic-e22-sx1268", 4, {
    "repeater": "Generic_E22_sx1268_repeater",
}, bridge="esp32_4mb_partition_migrator")

for specification in BOARDS.values():
    specification.setdefault("role", role_for_target(specification["target"]))
    if specification["lora_bridge"] is None:
        specification["lora_bridge"] = specification["wifi_bridge"] + "_lora"

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


def mota_full(image: bytes, identity: FwIdent, block_size: int) -> bytes:
    # The wire format supports more than the 1024 leaves held by older
    # 4 KiB-scratch seeders. The package records the required scratch size so
    # an operator can select a capable seeder before installing the bridge.
    if (len(image) + block_size - 1) // block_size > 4096:
        raise ValueError("mOTA image exceeds the supported 4096-block seeder limit")
    manifest = build_manifest(
        target_id=identity.target_id,
        fw_version=identity.fw_version,
        image_size=len(image),
        payload=image,
        block_size=block_size,
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


def bridge_with_successor_endf(image: bytes, successor: FwIdent) -> bytes:
    """Bind a temporary role image to the old node's exact LoRa target.

    PlatformIO can already append EndF for the temporary role. Keeping that
    trailer would make the old repeater reject stage one on target mismatch.
    """
    body = parse_endf(image)[0] if has_endf(image) else image
    return body + build_endf(body, successor)


def check_esp32_stage(package: bytes, image: bytes, slot_bytes: int) -> None:
    # Mirror the ESP32 FULL staging geometry: the payload is written at the
    # start of the inactive app, with metadata in aligned sectors at its end.
    metadata_bytes = len(package) - len(image) - 5
    metadata_flush = (metadata_bytes + 5 + 4095) & ~4095
    if (len(package) > slot_bytes or metadata_flush > 65536
            or len(image) > slot_bytes - metadata_flush):
        raise ValueError("mOTA image cannot be staged in the inactive ESP32 slot")


def readme(board: str, spec: dict, version: str, source: str,
           has_full_mota: bool = True, full_mota_blocks: int = 0) -> str:
    expander_instructions = ""
    if spec.get("expander_bridge"):
        expander_instructions = f"""## Partition Expander route (automatic LoRa finish)

For this exact **{spec['target']}** hardware/role, `partition-expander.bin`
is an alternative to `wifi-bridge.bin` in step 1 above. It works from either
legacy OTA slot. It stages and verifies the private identity, saved node name,
radio profiles and ACL before changing the table. After **Expanded layout
ready**, it listens on the saved primary radio profile and automatically
fetches only this package's exact-target Full application. Serve
`full-application.mota` on that profile; do not upload it to the browser.
The bridge's status page reports the receiver profile and block progress.
Keep only the intended Full build for this target on the seeder during the
migration; the bridge pins the hardware/role target, not an individual MID.
If installed on an already-expanded board, it returns to a verified Full image
in the other slot. Without a recorded migration handoff or safe Full image, it
does not fetch or replace an image; the status page explains whether Wi-Fi
recovery is available. Firmware upload is disabled if identity verification
failed.

`partition-expander.mota` offers the same bridge as a first-stage LoRa update
only if the installed old firmware already supports compatible mOTA and its
target ID matches this repeater. A stock image without mOTA must use its
existing Wi-Fi updater for stage one. Keep a compatible seeder available before
installing the bridge; the bridge does not itself repeat normal traffic.
"""
    wifi_instructions = f"""## Wi-Fi route

1. On the old node, run `start ota ap` through its authenticated terminal.
   Join its `MeshCore-OTA` Wi-Fi and upload `wifi-bridge.bin` at the exact
   `Started:` URL returned by the command (the port can vary by old build).
2. Keep power stable. Join `MeshCore-Migrate` (password `meshcore-migrate`),
   open `http://192.168.4.1/`, and wait for **Expanded layout ready**.
3. At that page's `/update`, upload `full-application.bin`.
"""
    if spec["lora_bridge"]:
        slot_instructions = (
            """On this **4 MiB** layout, the bridge must be installed in old slot B
while a verified old LoRa receiver remains in old slot A. If the old node is
running B, first install/boot a working same-target old receiver in A, then
install the bridge in B. A bridge installed in A refuses migration. After the
table switch the old receiver in A may use a temporary identity; the Full
application restores the original private key from NVS on its first boot.
Do not abandon the update between those two boots.\n\n"""
            if spec["flash_bytes"] == 4 * 1024 * 1024 else
            "The bridge may be installed in either old OTA slot.\n\n"
        )
        handoff = (
            "the original key stays staged in NVS until the Full firmware "
            "restores it on first boot"
            if spec["flash_bytes"] == 4 * 1024 * 1024 else
            "the bridge restores the private key before handing off"
        )
        lora_instructions = f"""## LoRa route

The old node must already have working MeshCore LoRa mOTA, a valid EndF,
and the exact target ID in `manifest.json`. A stock image without that updater
cannot take this route. The bridge checks the old receiver before changing the
table and refuses if its image or target does not match.

If this bridge is accidentally installed on a node that already has the
expanded table, it verifies the other OTA slot's target, EndF image hash and
bootability, then returns to that firmware without changing the table. It
refuses a second migration bridge or an invalid other image; Wi-Fi recovery
remains available if no safe LoRa image exists and identity restoration has
completed. A failed identity restore disables firmware upload. Do not
deliberately install the bridge on an already-expanded node; use the Full image
directly.

{slot_instructions}The Full image has {full_mota_blocks} blocks and needs a seeder with at least
{full_mota_blocks * 4} bytes of proof scratch. Older 4 KiB-scratch seeders cannot
serve more than 1024 blocks. Verify that a capable seeder lists the Full image
before installing the bridge.

The resized SPIFFS may reset saved radio settings. Before stage one, know
the old receiver's compiled default radio profile and arrange a seeder on
that profile for stage two; a remote node that cannot hear the seeder cannot
finish its LoRa migration.

1. Make `lora-bridge.mota` available from a compatible mOTA seeder. On the old
   node use `ota ls`, `ota pull <id> flash`, then `ota install` after the
   complete download. A manual install handles an equal-version bridge.
2. Keep power stable. The bridge expands the table; {handoff}. It then reboots
   into the preserved old LoRa receiver in the opposite slot.
3. Serve `full-application.mota`; again use `ota ls`, `ota pull <id> flash`,
   then `ota install`. The old receiver now sees the expanded inactive slot.
   A full-image LoRa transfer can take considerable time.

`full-application.bin` and `full-application.mota` are the same exact-target
Full image. `target-partitions.bin` is included for verification only;
do not upload it to the browser. Neither bridge is a USB merged image.
"""
    else:
        full_mota_note = (
            "Do not upload `full-application.mota` until the expanded layout "
            "and new Full image are in place."
            if has_full_mota else
            "This ZIP does not include `full-application.mota`: the Full image "
            "exceeds the current LoRa mOTA 2 KiB x 1024-block serving limit. "
            "Use `full-application.bin` through Wi-Fi."
        )
        lora_instructions = f"""## LoRa limitation

This package has **no LoRa bridge**. The Wi-Fi updater in the installed old
firmware is required. On 4 MiB flash, the expanded app1 overlaps the old app1,
so the current LoRa receiver-copy method cannot survive a power loss safely.
Some older room-server images also have no LoRa mOTA receiver. Do not upload
the Full image through LoRa before the new layout is active. {full_mota_note}
`target-partitions.bin` is for verification only, not browser upload.
"""
    return f"""# {board} in-place partition migration

Source: `{source}`; firmware version: `{version}`. This package is only for the
physical board/role represented by `{spec['target']}` with
{spec['flash_bytes'] // 1048576} MiB flash. A legacy Wi-Fi-updatable observer or
bridge variant on the same hardware may migrate to this canonical Full target;
the Wi-Fi bridge stages its saved node/radio configuration and ACL when NVS has
enough room, and refuses migration otherwise. The older LoRa handoff bridge
preserves only the private identity. The LoRa route, where present,
requires the old image's exact target ID.
The current partition table must have stable NVS at 0x9000, OTA metadata at
0xE000, two OTA apps, and SPIFFS with `/identity/_main.id`.

The bridge checks the live layout and refuses unsupported boards. It stages
the 96-byte public/private identity in unchanged NVS, preserves a bootable old
receiver, and changes the partition table. On 8/16 MiB LoRa and Wi-Fi routes
the bridge restores and verifies the identity in expanded SPIFFS; on the
4 MiB LoRa route the new Full firmware does so at first boot. No full-chip
erase or USB connection is used.
The table sector and inactive app sectors are erased as part of migration.
SPIFFS may be reformatted. The Wi-Fi bridge restores verified radio 1 and
radio 2 settings, node name, ACL and login replay state, region keys, and
selected network/OTA settings. Temporary radio sessions and logs are not
preserved. The older LoRa handoff route may reset saved settings.
The stock bootloader has no atomic backup partition table: power loss during
the table-sector erase/write can still require cable recovery. Do not use this
on an inaccessible node without accepting that risk and testing a sacrificial
example of the same board first.

{wifi_instructions}
{expander_instructions}
{lora_instructions}
"""


def package_board(name: str, spec: dict, build_dir: Path, output_dir: Path,
                  version: str, source: str) -> Path:
    target = spec["target"]
    target_id = target_id_for_env(target)
    hw_id = hardware_id_for_env(target)
    images = sorted(build_dir.glob(f"{target}-full-*-{version}-{source}.bin"))
    if len(images) != 1:
        raise ValueError(f"{name}: expected one exact-target Full image, found {len(images)}")
    stem = images[0].stem
    full_path = build_dir / (stem + ".bin")
    capability_path = build_dir / (stem + ".capabilities.json")
    validate_package(build_dir / stem)
    capabilities = json.loads(capability_path.read_text())
    if (capabilities.get("target") != target
            or capabilities.get("build_profile") != "full"
            or capabilities.get("platform") != "ESP32_PLATFORM"
            or not capabilities.get("ota_update_verified")):
        raise ValueError(f"{name}: Full build has the wrong target or lacks OTA")

    # build.sh may clean .pio/build between board builds. Its merged image
    # retains the exact bootloader/partition table/app bytes that were audited.
    merged = (build_dir / (stem + "-merged.bin")).read_bytes()
    if len(merged) < 0x9000 or merged[0x10000:] != full_path.read_bytes():
        raise ValueError(f"{name}: merged image does not contain the release app")
    table = merged[0x8000:0x9000]
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
    bridge_ident = FwIdent(pack_version(version.split("-", 1)[0]), target_id, hw_id)
    if len(wifi_bridge) > LEGACY_SLOT_BYTES:
        raise ValueError(f"{name}: bridge does not fit the 1.25 MiB legacy slot")
    if full_ident.fw_version != bridge_ident.fw_version:
        raise ValueError(f"{name}: bridge and Full image versions differ")

    files = {
        "wifi-bridge.bin": wifi_bridge,
        "full-application.bin": full_image,
        "target-partitions.bin": table,
        "capabilities.json": capability_path.read_bytes(),
    }
    if spec.get("expander_bridge"):
        expander_body = (ROOT / ".pio" / "build" / spec["expander_bridge"] /
                         "firmware.bin").read_bytes()
        expander_image = bridge_with_successor_endf(expander_body, bridge_ident)
        if len(expander_image) > LEGACY_SLOT_BYTES:
            raise ValueError(f"{name}: Partition Expander does not fit the legacy slot")
        expander_mota = mota_full(expander_image, bridge_ident, 1024)
        check_esp32_stage(expander_mota, expander_image, LEGACY_SLOT_BYTES)
        files["partition-expander.bin"] = expander_body
        files["partition-expander.mota"] = expander_mota
    full_mota_blocks = (len(full_image) + 2047) // 2048
    if full_mota_blocks <= 4096:
        candidate_mota = mota_full(full_image, full_ident, 2048)
        try:
            check_esp32_stage(candidate_mota, full_image, slot_bytes)
        except ValueError:
            if spec["lora_bridge"]:
                raise
        else:
            files["full-application.mota"] = candidate_mota
    elif spec["lora_bridge"]:
        raise ValueError(f"{name}: LoRa Full image exceeds the mOTA block limit")
    if spec["lora_bridge"]:
        lora_bridge_body = (ROOT / ".pio" / "build" / spec["lora_bridge"] / "firmware.bin").read_bytes()
        lora_bridge = bridge_with_successor_endf(lora_bridge_body, bridge_ident)
        if len(lora_bridge) > LEGACY_SLOT_BYTES:
            raise ValueError(f"{name}: LoRa bridge does not fit the legacy slot")
        files["lora-bridge.mota"] = mota_full(lora_bridge, bridge_ident, 1024)
        check_esp32_stage(files["lora-bridge.mota"], lora_bridge, LEGACY_SLOT_BYTES)
    files["README.md"] = readme(name, spec, version, source,
                                 "full-application.mota" in files,
                                 full_mota_blocks).encode()
    manifest = {
        "board": name,
        "role": spec["role"],
        "target": target,
        "target_id": f"0x{target_id:08x}",
        "hardware_id": hw_id,
        "source_commit": source,
        "firmware_version": version,
        "flash_bytes": spec["flash_bytes"],
        "legacy_slot_bytes": LEGACY_SLOT_BYTES,
        "expanded_slot_bytes": slot_bytes,
        "atomic_power_loss_recovery": False,
        "lora_bridge_mode": ("slot-b-only-full-identity-recovery"
                             if spec["flash_bytes"] == 4 * 1024 * 1024
                             else "either-slot-preserve-receiver"),
        "partition_expander": spec.get("expander_bridge"),
        "full_mota_seeder_scratch_bytes": full_mota_blocks * 4,
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
    parser.add_argument("--board", action="append", choices=tuple(BOARDS),
                        help="board to package; repeat for multiple boards (default: all)")
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output directory must be empty")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected = dict.fromkeys(args.board or BOARDS)
    archives = [package_board(name, BOARDS[name], args.build_dir, args.output_dir,
                              args.version, args.source)
                for name in selected]
    for archive in archives:
        print(f"{archive}: {archive.stat().st_size} bytes")


if __name__ == "__main__":
    main()
