"""
PlatformIO post-build extra-script: append the MeshCore ``EndF`` trailer to the
firmware image so a running node can self-locate its size/identity (docs/ota_protocol.md Section 2).

Wire it (ONLY for OTA-enabled builds) from a variant/env, e.g.:

    extra_scripts =
      ${nrf52_base.extra_scripts}
      post:tools/mota/pio_endf.py

and define ``-D ENABLE_OTA=1``. With ENABLE_OTA unset this script is a no-op, so it is safe to
leave wired everywhere.

The byte logic is the same `motalib.ensure_endf` exercised by `endf.py` and the unit tests.

ESP32 / RP2040 emit ${PROGNAME}.bin (the raw app image) -> EndF appended to the .bin.
nRF52 emits ${PROGNAME}.hex (the app, for DFU/UF2) -> EndF appended into the .hex right after the
app's last byte (so the downstream .uf2 / DFU .zip carry it). Both feed the same on-device EndF scan.
"""

Import("env")  # noqa: F821  (injected by PlatformIO/SCons)

import os
import sys

sys.path.insert(0, os.path.join(env["PROJECT_DIR"], "tools", "mota"))  # noqa: F821
import motalib as ml


def _apply_forced_lora_ota_overlay() -> None:
    """Resolve a legacy trailing ``-UENABLE_OTA`` for release overlays.

    A few feature-rich nRF52 repeater environments are intentionally non-OTA
    and carry a final compiler undefine. PlatformIO can remove normal ``-D``
    entries through ``build_unflags``, but it leaves that ``-U`` after all
    newly supplied defines. The release builder opts into this correction only
    when it is deliberately producing the complete OTA variant.
    """
    if os.environ.get("MESHCORE_FORCE_LORA_OTA") != "1":
        return

    import re

    defines = []
    enable_seen = False
    for item in env.get("CPPDEFINES", []):  # noqa: F821
        name = item[0] if isinstance(item, (list, tuple)) else item
        if name == "DISABLE_LORA_OTA":
            continue
        if name == "ENABLE_OTA":
            if enable_seen:
                continue
            enable_seen = True
        defines.append(item)
    if not enable_seen:
        defines.append(("ENABLE_OTA", 1))
    env.Replace(CPPDEFINES=defines)  # noqa: F821

    cppdef_flags = str(env.get("_CPPDEFFLAGS", ""))  # noqa: F821
    cppdef_flags = re.sub(r"(?:^|\s)-U\s*ENABLE_OTA(?=\s|$)", " ", cppdef_flags)
    env.Replace(_CPPDEFFLAGS=" ".join(cppdef_flags.split()))  # noqa: F821
    print("LoRa OTA overlay: removed legacy -UENABLE_OTA and enabled the OTA command surface")


def _ota_enabled() -> bool:
    disabled = False
    enabled = False
    for d in env.get("CPPDEFINES", []):  # noqa: F821
        name = d[0] if isinstance(d, (list, tuple)) else d
        if name == "DISABLE_LORA_OTA":
            disabled = True
        if name == "ENABLE_OTA":
            enabled = True
    return enabled and not disabled


def _is_nrf52() -> bool:
    for d in env.get("CPPDEFINES", []):  # noqa: F821
        name = d[0] if isinstance(d, (list, tuple)) else d
        if name == "NRF52_PLATFORM":
            return True
    return False


def _cppdef(name):                                # value of a -D<name>=<value> build flag, or None
    for d in env.get("CPPDEFINES", []):           # noqa: F821
        if isinstance(d, (list, tuple)) and len(d) > 1 and d[0] == name:
            return str(d[1])
        if d == name:
            return ""
    return None


def _board_maximum_size(build_env):
    """Return PlatformIO's resolved application-size limit, including per-env overrides."""
    try:
        value = build_env.BoardConfig().get("upload.maximum_size")
        size = int(str(value), 0)
        return size if size > 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


def _source_filter_text():
    """Resolved source-filter text for detecting code that is actually part of this environment."""
    srcf = ""
    for getter in (lambda: env.GetProjectOption("build_src_filter", ""),  # noqa: F821
                   lambda: env.GetProjectOption("src_filter", ""),        # noqa: F821
                   lambda: env.get("SRC_FILTER", "")):                    # noqa: F821
        try:
            value = getter()
            srcf += " " + (" ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value))
        except Exception:
            pass
    return srcf


def _builds_companion_radio() -> bool:
    return "examples/companion_radio" in _source_filter_text().replace("\\", "/")


def _version_from_headers():
    """FIRMWARE_VERSION is a header ``#define`` in the example (upstream MeshCore convention), not a -D, so
    _cppdef() can't see it and the EndF version would otherwise default to 0. Read it from the source WITHOUT
    changing where MeshCore authors it: prefer the example this env actually builds (from build_src_filter),
    else fall back to the repo-wide value when it's unambiguous. Purely additive - no MeshCore file changes,
    and a -D override (checked first in _firmware_ident) still wins for release builds."""
    import re, glob
    proj = env["PROJECT_DIR"]                                 # noqa: F821
    pat = re.compile(r'#\s*define\s+FIRMWARE_VERSION\s+"([^"]+)"')

    def scan(paths):                                          # {version_string: first_path_seen}
        vals = {}
        for p in paths:
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        mm = pat.search(line)
                        if mm:
                            vals.setdefault(mm.group(1), p)
            except OSError:
                pass
        return vals

    # 1) restrict to the example(s) this env compiles (build_src_filter -> examples/<name>)
    srcf = _source_filter_text()
    ex_dirs = set(re.findall(r"examples[/\\]([A-Za-z0-9_]+)", srcf))
    hdrs = []
    for d in ex_dirs:
        hdrs += glob.glob(os.path.join(proj, "examples", d, "*.h"))
    vals = scan(hdrs)
    if len(vals) == 1:
        return next(iter(vals))
    # 2) fall back to the repo-wide value; use it only if all examples agree (they do today: v1.17.0)
    vals = scan(glob.glob(os.path.join(proj, "examples", "*", "*.h")))
    return next(iter(vals)) if len(vals) == 1 else ""


def _firmware_ident():
    """Self-describing identity to embed in EndF (docs/ota_protocol.md Section 2): target_id is computed from the
    PlatformIO env name (so it's correct even without build.sh's -D MOTA_TARGET_ID), hw_id from MOTA_HW_ID,
    fw_version from FIRMWARE_VERSION (a -D if set, else the example's header #define)."""
    import re
    target_define = (_cppdef("MOTA_TARGET_ID") or "").strip()
    try:
        target_id = int(target_define, 0) if target_define else ml.target_id_for_env(env["PIOENV"])  # noqa: F821
    except ValueError:
        target_id = ml.target_id_for_env(env["PIOENV"])       # noqa: F821
    hw_id = (_cppdef("MOTA_HW_ID") or "").replace("\\", "").strip().strip('"').strip("'")
    if not hw_id:
        hw_id = ml.hardware_id_for_env(env["PIOENV"])          # noqa: F821
    ver_s = (_cppdef("FIRMWARE_VERSION") or "").replace("\\", "").strip().strip('"').strip("'")
    if not ver_s:                                             # not a -D -> read the header MeshCore ships
        ver_s = _version_from_headers()
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?", ver_s)
    fw_version = ml.pack_version(
        f"{m.group(1)}.{m.group(2)}.{m.group(3) or 0}.{m.group(4) or 0}"
    ) if m else 0
    return ml.FwIdent(fw_version=fw_version, target_id=target_id, hw_id=hw_id)


def _append_endf(source, target, env):           # raw .bin path (ESP32 / RP2040)
    path = str(target[0])
    with open(path, "rb") as f:
        data = f.read()
    ident = _firmware_ident()
    out, h8 = ml.ensure_endf(data, ident)
    if len(out) != len(data):
        with open(path, "wb") as f:
            f.write(out)
        print(f"EndF: appended to {os.path.basename(path)} (body_len={len(data)} body_hash={h8.hex()} "
              f"target={ident.target_id:#010x} hw='{ident.hw_id}' fw={ident.fw_version:#010x})")
    else:
        print(f"EndF: already present in {os.path.basename(path)} (no change)")


def _append_endf_hex(source, target, env):        # Intel-HEX path (nRF52: app for DFU/UF2)
    from intelhex import IntelHex
    path = str(target[0])
    ih = IntelHex(path)
    segs = ih.segments()
    if not segs:
        print("EndF: empty .hex, skipping"); return
    app_start, app_end = segs[0]                  # first (lowest) segment = the application image
    raw_body = bytes(ih.tobinarray(start=app_start, size=app_end - app_start))
    if ml.has_endf(raw_body):
        print(f"EndF: already present in {os.path.basename(path)} (no change)"); return

    app_region_size = _board_maximum_size(env)
    if app_region_size is None:
        raise RuntimeError("nRF52 linked application region is unavailable")
    linked_app_end = app_start + app_region_size
    internal_extrafs = (_cppdef("EXTRAFS") is not None and _cppdef("QSPIFLASH") is None
                        and _builds_companion_radio())
    sd_backed = _cppdef("OTA_SD_STORE") is not None
    qspi_backed = _cppdef("OTA_QSPI_STORE") is not None
    qspi_bootloader_update = _cppdef("OTA_QSPI_BOOTLOADER_UPDATE") is not None
    internal_bootloader_update = _cppdef("OTA_INTERNAL_BOOTLOADER_UPDATE") is not None
    sd_bootloader_update = _cppdef("OTA_SD_BOOTLOADER_UPDATE") is not None
    if sd_backed and qspi_backed:
        raise RuntimeError("nRF52 build cannot enable both SD and QSPI OTA stores")
    if qspi_backed and _cppdef("QSPIFLASH") is not None:
        raise RuntimeError("raw QSPI OTA staging cannot share a chip with QSPIFLASH")
    if qspi_bootloader_update:
        xiao_module = _cppdef("XIAO_NRF52") is not None or _cppdef("IKOKA_NRF52") is not None
        if not qspi_backed or sd_backed or not xiao_module:
            raise RuntimeError("bootloader update requires a XIAO nRF52840 raw-QSPI OTA build")
        if linked_app_end != ml.NRF52_BOOT_SCRATCH_START:
            raise RuntimeError("bootloader-update build must link exactly below scratch at 0xE0000")
    if internal_bootloader_update:
        if sd_backed or qspi_backed or _cppdef("QSPIFLASH") is not None or internal_extrafs:
            raise RuntimeError("internal bootloader update cannot use SD/QSPI/ExtraFS")
        if linked_app_end != ml.NRF52_APP_END:
            raise RuntimeError("shared-slot bootloader update requires the normal 0xED000 linker ceiling")
    if sd_bootloader_update:
        if not sd_backed or qspi_backed or internal_extrafs or _cppdef("QSPIFLASH") is not None:
            raise RuntimeError("SD bootloader update requires the dedicated nRF52 SD OTA store")
        if linked_app_end != ml.NRF52_APP_END:
            raise RuntimeError("SD bootloader update requires the normal 0xED000 linker ceiling")
    stage_ceiling = (ml.NRF52_APP_END if (sd_backed or qspi_backed) else
                     ml.nrf52_stage_ceiling_for_layout(linked_app_end, internal_extrafs))
    layout_flags = ((ml.NRF52_LAYOUT_FLAG_SD if sd_backed else 0) |
                    (ml.NRF52_LAYOUT_FLAG_QSPI if qspi_backed else 0) |
                    (ml.NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS if internal_extrafs else 0) |
                    (ml.NRF52_LAYOUT_FLAG_BOOTLOADER_SCRATCH if qspi_bootloader_update else 0))
    layout = ml.Nrf52Layout(app_start, linked_app_end, stage_ceiling, layout_flags)
    body = ml.ensure_nrf52_layout(raw_body, layout)
    ident = _firmware_ident()
    out, h8 = ml.ensure_endf(body, ident)
    seeder_only = _cppdef("OTA_SEEDER_ONLY") is not None
    if seeder_only:
        # A source-only full Companion never stages or applies an update to
        # itself, so it does not need to fit in OTAFIX's in-place workspace.
        # It must still fit the board/env's resolved flash application region.
        image_limit = app_region_size
        limit_name = "application"
    else:
        # Dynamic in-place patches carry their own memory_size and are checked against the staged
        # container before approval. The firmware itself only needs to remain inside both its linked
        # application region and the selected filesystem-safe ceiling.
        image_limit = min(linked_app_end, stage_ceiling) - app_start
        limit_name = "nRF52 OTA application"
    if image_limit is not None and len(out) > image_limit:
        raise RuntimeError(f"nRF52 OTA image is {len(out)} bytes; {limit_name} limit is "
                           f"{image_limit} bytes")
    suffix = out[len(raw_body):]                   # layout record + EndF identity trailer
    for i, b in enumerate(suffix):
        ih[app_end + i] = b                        # write it right after the app's last byte
    ih.write_hex_file(path)
    endf_address = app_start + len(body)
    print(f"EndF: appended to {os.path.basename(path)} at 0x{endf_address:X} "
          f"(app=0x{app_start:X}.. body_len={len(body)} body_hash={h8.hex()} "
          f"stage=0x{stage_ceiling:X} "
          f"target={ident.target_id:#010x} hw='{ident.hw_id}' fw={ident.fw_version:#010x})")


_apply_forced_lora_ota_overlay()

if _ota_enabled():
    if _is_nrf52():
        env.AddPostAction("$BUILD_DIR/${PROGNAME}.hex", _append_endf_hex)  # noqa: F821
    else:
        env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", _append_endf)      # noqa: F821
else:
    print("EndF: ENABLE_OTA not defined; skipping trailer injection")
