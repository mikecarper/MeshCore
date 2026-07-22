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

    if flash_size >= 16 * 1024 * 1024:
        partitions = "default_16MB.csv"
    elif flash_size >= 8 * 1024 * 1024:
        partitions = "default_8MB.csv"
    elif flash_size >= 4 * 1024 * 1024:
        partitions = "variants/dual_ota_full_4MB.csv"
    else:
        partitions = None

    if partitions:
        board.update("build.partitions", partitions)
        print(
            "ESP32 FULL build: using %s for %s flash"
            % (partitions, board.get("upload.flash_size", "4MB"))
        )
    else:
        print(
            "ESP32 FULL build: retaining target partition table for %s flash"
            % board.get("upload.flash_size", "unknown")
        )
