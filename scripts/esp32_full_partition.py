Import("env")

import os


def parse_flash_size(value):
    text = str(value).strip().upper()
    multipliers = {"KB": 1024, "MB": 1024 * 1024}
    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            return int(text[: -len(suffix)]) * multiplier
    return int(text, 0)


if os.environ.get("MESHCORE_ESP32_FULL_BUILD") == "1":
    board = env.BoardConfig()
    flash_size = parse_flash_size(board.get("upload.flash_size", "4MB"))
    if str(board.get("build.mcu", "")).lower() == "esp32s3":
        # Every S3 Full image must remain ROM-bootable across the supported
        # flash parts. Some board manifests default to QIO even though their
        # ROM/bootloader chain only starts a merged image reliably in DIO.
        # PSRAM mode is an independent setting and is intentionally untouched.
        board.update("build.flash_mode", "dio")
        print("ESP32-S3 FULL build: forcing ROM-compatible DIO flash mode")
    companion_radio_full = (
        os.environ.get("MESHCORE_COMPANION_RADIO_FULL") == "1"
    )
    tbeam_1w = board.get("build.variant", "") == "lilygo_tbeam_1w"

    if companion_radio_full and tbeam_1w:
        # The Full Companion is an mOTA source only and never installs into a
        # second local app slot. Match LilyGo's factory T-Beam 1W boot layout so
        # a merged recovery image changes only the application, not the proven
        # boot/partition chain. The 3 MiB app slot has ample room for this role.
        partitions = "huge_app.csv"
    elif flash_size >= 16 * 1024 * 1024:
        partitions = "default_16MB.csv"
    elif flash_size >= 8 * 1024 * 1024:
        partitions = "default_8MB.csv"
    elif flash_size >= 4 * 1024 * 1024 and companion_radio_full:
        # A classic ESP32 Companion with USB, BLE, WiFi, WebConfig, and the
        # mOTA seeder can exceed the largest possible pair of 4 MiB OTA
        # slots. This role cannot install mOTA on itself, so give it one 3 MiB
        # application slot and retain SPIFFS/coredump.
        partitions = "huge_app.csv"
    elif flash_size >= 4 * 1024 * 1024:
        partitions = "variants/dual_ota_full_4MB.csv"
    else:
        partitions = None

    if partitions:
        board.update("build.partitions", partitions)
        print(
            "ESP32 FULL build%s: using %s for %s flash"
            % (
                " (Companion source-only)" if companion_radio_full else "",
                partitions,
                board.get("upload.flash_size", "4MB"),
            )
        )
    else:
        print(
            "ESP32 FULL build: retaining target partition table for %s flash"
            % board.get("upload.flash_size", "unknown")
        )
