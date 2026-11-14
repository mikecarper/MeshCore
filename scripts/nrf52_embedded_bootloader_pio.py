"""Explicit, build-local OTAFIX 2.4.12 bootloader resource hook."""
Import("env")

import os
from pathlib import Path
import sys

requested = os.environ.get("MESHCORE_NRF52_EMBED_BOOTLOADER")
legacy = os.environ.get("MESHCORE_NRF52_LEGACY_BOOTLOADER")
if legacy and not requested:
    raise RuntimeError("legacy bootloader pin requires MESHCORE_NRF52_EMBED_BOOTLOADER=2.4.12")
if requested:
    if requested not in ("2.4.12", "2.4.13-preview.1"):
        raise RuntimeError("only pinned OTAFIX embedded bootloader releases are supported")
    project = Path(env["PROJECT_DIR"])
    sys.path.insert(0, str(project / "scripts"))
    from nrf52_embedded_bootloader import emit_resource
    board = env.BoardConfig()
    if str(board.get("build.mcu", "")).lower() != "nrf52840":
        raise RuntimeError("embedded OTAFIX bootloader requires an nRF52840")
    generated = Path(env.subst("$BUILD_DIR")) / "bootloader-migration-resource"
    src_filter = env.get("SRC_FILTER")
    if src_filter is None:
        src_filter = env.GetProjectOption("build_src_filter", "")
    metadata = emit_resource(project, env["PIOENV"], generated,
        flags=env.get("BUILD_FLAGS", ()), cppdefines=env.get("CPPDEFINES", ()),
        src_filter=src_filter,
        linker=board.get("build.ldscript", "") or board.get("build.arduino.ldscript", ""),
        legacy_path=legacy, version=requested)
    env.AppendUnique(CPPDEFINES=[("OTA_BOOTLOADER_MIGRATION", 1)], CPPPATH=[str(generated)])
    env.BuildSources(str(generated / "objects"), str(generated), src_filter=["+<*.cpp>"])
    print(f"Embedded OTAFIX {metadata['release']}: {metadata['profile']}; 40960 bytes; "
          f"SHA256 {metadata['candidate_sha256']}")
