#!/usr/bin/env python3
"""Build plans and capacity checks for exact-target partition expanders."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

LEGACY_SLOT_BYTES = 0x140000
TARGET_MARKER = b"MC_EXPANDER_TARGET_V1" + b"\0" * 3
# These board bases retain the exact radio/pins of the listed role while
# avoiding unrelated UI or on-chip scan features in the temporary bridge.
SLIM_BASES = {
    "meshnology-w12-repeater": ("meshnology_w12_repeater", "meshnology_w12", ("-D MESH_ENABLE_LR2021_AUTO_XTRIM=1",)),
    "heltec-ct62-repeater": ("Heltec_ct62_repeater", "Heltec_ct62", ("-D LIGHTWEIGHT_WIFI_OTA=1",)),
}
REVIEWED_EXPANDERS = {
    "heltec_v4_partition_expander": "heltec_v4_repeater",
    "Xiao_S3_WIO_partition_expander": "Xiao_S3_WIO_repeater",
    "Waveshare_S3_LR1121_partition_expander_repeater_observer_mqtt": "Waveshare_S3_LR1121_repeater_observer_mqtt",
    "Waveshare_S3_LR1121_partition_expander_room_server_observer_mqtt": "Waveshare_S3_LR1121_room_server_observer_mqtt",
}


def target_id_for_env(target: str) -> int:
    return int.from_bytes(hashlib.sha256(target.encode()).digest()[:4], "little")


def expander_environment(board: str, target: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board) or not re.fullmatch(r"[A-Za-z0-9_-]+", target):
        raise ValueError("invalid migration board or target")
    identity = hashlib.sha256((board + "\0" + target).encode()).hexdigest()[:16]
    return "mc_expander_" + identity


def overlay_section(board: str, spec: dict) -> str:
    """Extend the exact production role so its radio, pins and board are retained.

    The expanded CSV permits a real linked image even if it is too large for
    the old slot. It is used only for sizing; never flash its partition table.
    The bridge reads the installed table at runtime and validates it itself.
    """
    target = spec["target"]
    env = expander_environment(board, target)
    flash = spec["flash_bytes"]
    # Prefer the already-reviewed slim bridge where one exists. Other exact
    # roles inherit their own board, radio configuration and libraries.
    slim = SLIM_BASES.get(board)
    if slim and slim[0] != target:
        raise ValueError(f"{board}: slim expander does not match target")
    reviewed = spec.get("expander_bridge")
    if reviewed and REVIEWED_EXPANDERS.get(reviewed) != target:
        raise ValueError(f"{board}: reviewed expander does not match target")
    source_env = (slim[1] if slim else "env:" + (reviewed or target))
    if not re.fullmatch(r"(?:env:)?[A-Za-z0-9_-]+", source_env):
        raise ValueError(f"{board}: invalid source environment")
    extra = "".join("\n  " + flag for flag in (slim[2] if slim else ()))
    if board == "heltec-ct62-repeater":
        # Match the existing qualified 4 MiB Full firmware's lossless crypto
        # and OTA-name implementations; the expander is excluded from the
        # general Full-role hook and must opt in explicitly.
        from esp32_full_size import compact_definitions
        extra += "".join(f"\n  -D {name}={value}"
                         for name, value in compact_definitions(flash).items())
    partitions = {
        4 * 1024 * 1024: "variants/dual_ota_full_4MB.csv",
        8 * 1024 * 1024: "default_8MB.csv",
        16 * 1024 * 1024: "default_16MB.csv",
    }.get(flash)
    if not partitions:
        raise ValueError(f"{board}: unsupported flash capacity")
    strict = "\n  -D MESHCORE_MIGRATION_STRICT_4MB=1" if flash == 4 * 1024 * 1024 else ""
    # Classic ESP32 allocates OtaContext on heap with a strict 16 KiB budget.
    # One in-flight block preserves that bound; S3/C3 can use the wider window.
    pipeline = 1 if spec.get("usb_chip") == 0 else 4
    return f"""
[env:{env}]
extends = {source_env}
board_build.partitions = {partitions}
build_src_filter = ${{{source_env}.build_src_filter}}
  -<../examples/simple_repeater>
  -<../examples/simple_room_server>
  -<../examples/simple_sensor>
  +<../examples/partition_expander/*.cpp>
build_flags = ${{{source_env}.build_flags}}{extra}
  -UARDUINO_USB_CDC_ON_BOOT
  -DARDUINO_USB_CDC_ON_BOOT=0
  -D MESH_MIN_RUNTIME_HEAP=100352
  -D MOTA_MIGRATION_TARGET_ID=0x{target_id_for_env(target):08x}
  -UOTA_FETCH_PIPELINE
  -D OTA_FETCH_PIPELINE={pipeline}{strict}
lib_deps = ${{{source_env}.lib_deps}}
  ${{esp32_ota.lib_deps}}
"""


def project_overlay(project_text: str, boards: list[str], catalog: dict) -> str:
    if not boards or len(boards) != len(set(boards)):
        raise ValueError("select distinct migration boards")
    sections = [overlay_section(board, catalog[board]) for board in boards]
    names = [expander_environment(board, catalog[board]["target"]) for board in boards]
    if len(names) != len(set(names)):
        raise ValueError("duplicate expander build environment")
    return project_text.rstrip() + "\n" + "\n".join(sections)


def verify_compiled_target(image: bytes, target: str) -> int:
    if not image or image[0] != 0xE9:
        raise ValueError("candidate is not an ESP32 application image")
    if image.count(TARGET_MARKER) != 1:
        raise ValueError("candidate must contain exactly one compiled expander target marker")
    offset = image.index(TARGET_MARKER) + len(TARGET_MARKER)
    if len(image) < offset + 4:
        raise ValueError("candidate expander target marker is truncated")
    target_id = struct.unpack_from("<I", image, offset)[0]
    if target_id != target_id_for_env(target):
        raise ValueError("candidate expander target does not match board role")
    return target_id


def verify_esp32_chip_flash(board: str, spec: dict, image: bytes) -> None:
    """Check an application's hardware header against the exact board plan."""
    if len(image) < 24 or image[0] != 0xE9:
        raise ValueError(f"{board}: invalid or truncated ESP32 image header")
    chip_id = struct.unpack_from("<H", image, 12)[0]
    flash_sizes = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 5: 32, 6: 64, 7: 128}
    flash_mib = flash_sizes.get(image[3] >> 4)
    if (chip_id != spec["usb_chip"] or flash_mib is None
            or flash_mib * 1024 * 1024 != spec["flash_bytes"]):
        raise ValueError(f"{board}: candidate chip or flash header does not match board")


def write_project_overlay(path: Path, project_ini: Path, boards: list[str], catalog: dict) -> None:
    path.write_text(project_overlay(project_ini.read_text(encoding="utf-8"), boards, catalog),
                    encoding="utf-8")


def evaluate_candidate(board: str, spec: dict, image: bytes, version: str) -> dict:
    """Prove the exact image identity and both legacy-slot staging limits.

    A missing or malformed image is an error. Only an actual image whose
    measured binary or OTA staging geometry exceeds the old slot is oversize.
    """
    from package_esp32_partition_migration import bridge_with_successor_endf, mota_full
    from motalib import FwIdent, hardware_id_for_env, pack_version

    target = spec["target"]
    target_id = verify_compiled_target(image, target)
    if "usb_chip" in spec and "flash_bytes" in spec:
        verify_esp32_chip_flash(board, spec, image)
    identity = FwIdent(pack_version(version.split("-", 1)[0]), target_id,
                       hardware_id_for_env(target))
    staged_image = bridge_with_successor_endf(image, identity)
    staging_bytes = None
    container_bytes = None
    # An image already larger than the legacy slot is conclusive evidence,
    # even if it also exceeds the mOTA container's independent block limit.
    if len(staged_image) <= 4096 * 1024:
        package = mota_full(staged_image, identity, 1024)
        container_bytes = len(package)
        metadata_bytes = len(package) - len(staged_image) - 5
        metadata_flush = (metadata_bytes + 5 + 4095) & ~4095
        if metadata_flush > 65536:
            raise ValueError(f"{board}: expander OTA metadata exceeds its independent limit")
        staging_bytes = len(staged_image) + metadata_flush
    elif len(staged_image) <= LEGACY_SLOT_BYTES:
        raise ValueError(f"{board}: candidate exceeds the mOTA block limit")

    fits = (len(image) <= LEGACY_SLOT_BYTES and staging_bytes is not None
            and staging_bytes <= LEGACY_SLOT_BYTES
            and container_bytes <= LEGACY_SLOT_BYTES)
    return {
        "format": "meshcore-expander-evaluation-v1",
        "board": board,
        "target": target,
        "target_id": f"0x{target_id:08x}",
        "environment": expander_environment(board, target),
        "firmware_version": version,
        "image_bytes": len(image),
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "legacy_slot_bytes": LEGACY_SLOT_BYTES,
        "mota_image_bytes": len(staged_image),
        "mota_staging_bytes": staging_bytes,
        "mota_container_bytes": container_bytes,
        "decision": "combined" if fits else "oversize",
    }


def build_expander_candidates(boards: list[str], version: str, jobs: int,
                              root: Path, utility_build_dir: Path,
                              environment: dict, output_dir: Path) -> Path:
    """Build serially, then publish validated candidate/evaluation pairs.

    A PIO failure is always fatal, including a compiler, linker, RAM, or
    expanded-slot capacity failure. Only a successfully built candidate can
    establish that the legacy slot is too small.
    """
    from package_esp32_partition_migration import BOARDS, bridge_with_successor_endf
    from motalib import FwIdent, hardware_id_for_env, pack_version

    root = Path(root).resolve()
    utility_build_dir = Path(utility_build_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if jobs < 1:
        raise ValueError("jobs must be positive")
    if not boards or any(board not in BOARDS for board in boards):
        raise ValueError("unknown or missing expander board")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mesh-expander-plan-", dir=output_dir) as temporary:
        temp = Path(temporary)
        # PIO can clean its whole build tree when --project-conf changes.
        # Isolate temporary candidates from the already-built fallback/USB
        # artifacts and remove compiler objects after evidence is published.
        candidate_build_dir = temp / "pio-build"
        if candidate_build_dir.resolve() == utility_build_dir:
            raise ValueError("expander and utility build directories overlap")
        candidate_build_dir.mkdir()
        project = temp / "platformio.ini"
        write_project_overlay(project, root / "platformio.ini", boards, BOARDS)
        built = []
        for board in boards:
            spec = BOARDS[board]
            target = spec["target"]
            pio_env = expander_environment(board, target)
            pio_env_dir = candidate_build_dir / pio_env
            image_path = pio_env_dir / "firmware.bin"
            if (pio_env_dir.is_symlink() or pio_env_dir.resolve().parent != candidate_build_dir
                    or image_path.is_symlink()):
                raise ValueError(f"{board}: unsafe expander build output path")
            # An interrupted prior build must not masquerade as this result.
            image_path.unlink(missing_ok=True)
            build_environment = dict(environment)
            build_environment["PLATFORMIO_BUILD_DIR"] = str(candidate_build_dir)
            command = ["pio", "run", "--project-conf", str(project),
                       "-e", pio_env, "-j", str(jobs)]
            print("Building exact-target partition expander:", board, flush=True)
            subprocess.run(command, cwd=root, env=build_environment, check=True)
            if (pio_env_dir.is_symlink() or pio_env_dir.resolve().parent != candidate_build_dir
                    or image_path.is_symlink() or not image_path.is_file()):
                raise ValueError(f"{board}: successful build did not produce firmware.bin")
            raw = image_path.read_bytes()
            verify_compiled_target(raw, target)
            identity = FwIdent(pack_version(version.split("-", 1)[0]),
                               target_id_for_env(target), hardware_id_for_env(target))
            # The temporary build environment must never leak into the EndF
            # identity; both browser and LoRa files target the original role.
            candidate = bridge_with_successor_endf(raw, identity)
            evaluation = evaluate_candidate(board, spec, candidate, version)
            candidate_path = temp / (board + ".bin")
            evaluation_path = temp / (board + ".json")
            candidate_path.write_bytes(candidate)
            evaluation_path.write_text(json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")
            built.extend((candidate_path, evaluation_path))
            # Keep the peak build-space demand to one candidate at a time.
            shutil.rmtree(pio_env_dir)

        for source in built:
            destination = output_dir / source.name
            if destination.exists():
                if destination.read_bytes() != source.read_bytes():
                    raise ValueError(f"existing expander evidence differs: {destination}")
            else:
                os.replace(source, destination)
    return output_dir
