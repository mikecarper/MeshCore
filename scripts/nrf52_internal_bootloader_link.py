"""Enable shared-slot internal bootloader update support on qualified nRF52840 targets.

The bootloader package uses the ordinary bottom-aligned internal OTA store
below 0xED000. Qualified internal-only targets also use a dedicated linker that
reserves the top 64 KiB of SRAM for hybrid flash-prefix/RAM-suffix staging.
This pre-script binds both features to the exact release inventory and fails
closed if a target does not use the normal S140 v6/v7 1 MiB application layout.
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
    env.AppendUnique(CPPDEFINES=[
        "OTA_INTERNAL_BOOTLOADER_UPDATE",
        "OTA_HYBRID_RAM_STORE",
    ])
    board = env.BoardConfig()
    # Most variants inherit the framework's top-level ``build.ldscript``.
    # A few otherwise ordinary Adafruit nRF52840 boards (notably Keepteen LT1)
    # declare it under ``build.arduino.ldscript`` instead.  Resolve both forms
    # before making the safety decision; an unknown map still fails closed.
    current = str(
        board.get("build.ldscript", "")
        or board.get("build.arduino.ldscript", "")
    )
    linker_versions = {
        "nrf52840_s140_v6.ld": "v6",
        "nrf52840_s140_v7.ld": "v7",
    }
    # Compare the complete filename, not merely a suffix: a custom linker such
    # as ``board_nrf52840_s140_v6.ld`` must not be mistaken for the qualified
    # normal map and silently replaced with the mOTA layout.
    current_name = current.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = linker_versions.get(current_name)
    mcu = str(board.get("build.mcu", "")).lower()
    if mcu != "nrf52840" or suffix is None:
        raise RuntimeError(
            "shared-slot bootloader update requires an nRF52840 with the normal "
            f"S140 v6/v7 non-ExtraFS linker; found mcu={mcu or 'none'} "
            f"linker={current or 'none'}"
        )
    mota_linker = Path(env["PROJECT_DIR"]) / "boards" / (
        "nrf52840_s140_%s_mota64.ld" % suffix
    )
    if not mota_linker.is_file():
        raise RuntimeError("missing mOTA hybrid linker: %s" % mota_linker)

    # The framework builder normally resolves LDSCRIPT_PATH from BoardConfig.
    # Set both so this remains deterministic whether PlatformIO invokes this
    # pre-script before or after that framework step.
    board.update("build.ldscript", str(mota_linker))
    env.Replace(LDSCRIPT_PATH=str(mota_linker))
    print(
        "nRF52 shared-slot bootloader update: %s; fixed 64 KiB mOTA RAM arena; runtime EndF headroom required"
        % mota_linker.name
    )
