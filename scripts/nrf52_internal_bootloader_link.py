"""Enable shared-slot internal bootloader update support on qualified nRF52840 targets.

The bootloader package uses the ordinary bottom-aligned internal OTA store
below 0xED000; no second linker reservation exists. This pre-script only binds
the feature macro to the exact release inventory and fails closed if a target
does not use the normal S140 v6/v7 1 MiB application layout.
"""

Import("env")

import os
from pathlib import Path


inventory_path = Path(env["PROJECT_DIR"]) / "tools/mota/nrf52_internal_bootloader_targets.txt"
try:
    direct_targets = {
        line.strip()
        for line in inventory_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
except OSError as exc:
    raise RuntimeError(f"cannot read nRF52 internal-bootloader inventory: {exc}")

requested = (
    os.environ.get("MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE") == "1"
    or env["PIOENV"] in direct_targets
)

if requested:
    # The macro is injected here so direct PIO and build.sh synthetic aliases
    # cannot silently differ. Package admission is runtime-capacity based.
    env.AppendUnique(CPPDEFINES=["OTA_INTERNAL_BOOTLOADER_UPDATE"])
    board = env.BoardConfig()
    # Most variants inherit the framework's top-level ``build.ldscript``.
    # A few otherwise ordinary Adafruit nRF52840 boards (notably Keepteen LT1)
    # declare it under ``build.arduino.ldscript`` instead.  Resolve both forms
    # before making the safety decision; an unknown map still fails closed.
    current = str(
        board.get("build.ldscript", "")
        or board.get("build.arduino.ldscript", "")
    )
    mcu = str(board.get("build.mcu", "")).lower()
    if mcu != "nrf52840" or not (
        current.endswith("nrf52840_s140_v6.ld")
        or current.endswith("nrf52840_s140_v7.ld")
    ):
        raise RuntimeError(
            "shared-slot bootloader update requires an nRF52840 with the normal "
            f"S140 v6/v7 non-ExtraFS linker; found mcu={mcu or 'none'} "
            f"linker={current or 'none'}"
        )
    print(
        "nRF52 shared-slot bootloader update: normal linker %s; runtime EndF headroom required"
        % current
    )
