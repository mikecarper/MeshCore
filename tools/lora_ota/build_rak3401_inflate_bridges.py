#!/usr/bin/env python3
"""Build receive-DEFLATE RAK3401 bridge images for the historical mOTA chain.

The input commits are pinned because each image is a deliberate binary bridge.
The script uses one temporary Git worktree, applies the reviewed backport
patches plus the pinned receive-only transport shim without committing them,
and emits a machine-readable image manifest.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "mota"))

import motalib  # noqa: E402


ENV_NAME = "RAK_3401_repeater_lora_ota_no_external_sensors"
VERSION_SUFFIX = "halo-keymind-cascade-mota-inflate"
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
COMMON_PATCH = SCRIPT_DIR / "rak3401_terminal_ota_backport.patch"
LEGACY_MESH_PATCH = SCRIPT_DIR / "rak3401_terminal_ota_mesh_legacy.patch"
GUARDED_MESH_PATCH = SCRIPT_DIR / "rak3401_terminal_ota_mesh_guarded.patch"
VERSION_PATCH = SCRIPT_DIR / "rak3401_four_component_endf_backport.patch"
WORKSPACE_PATCH = SCRIPT_DIR / "rak3401_dynamic_workspace_backport.patch"
SELECTIVE_OS_HOOK = SCRIPT_DIR / "rak3401_selective_os.py"
INFLATE_ASSET_ROOT = SCRIPT_DIR / "rak3401_inflate_assets"
INFLATE_SNAPSHOT_COMMIT = "add51bf00c46c15ef54318ca766a6daf08a147ee"

INFLATE_ASSETS = {
    "src/helpers/ota/OtaDeflate.cpp": (
        "OtaDeflate.cpp",
        "7cc464fc3c304cc65f52973593068c8d85a783853da121476c4bc2e196798e8e",
    ),
    "src/helpers/ota/OtaDeflate.h": (
        "OtaDeflate.h",
        "f6cd5b0fe8b2164178881d93664df37722d6434f8ef61c37d08255bce12f842c",
    ),
    "src/helpers/ota/OtaTinf.c": (
        "OtaTinf.c",
        "faf6e28f6f7b719926b27968e7223930f44ab45ed146c79b7ee19bcaa26a15f7",
    ),
    "src/helpers/ota/tinf/LICENSE": (
        "tinf/LICENSE",
        "f39b507b81b9ba1edce7fe742bbd68fda4b852063ce7299274ae97991d48814b",
    ),
    "src/helpers/ota/tinf/README.meshcore.txt": (
        "tinf/README.meshcore.txt",
        "1973f6495907341e3fc1319324f7fcb04275bb40efe37496b31d811270de1052",
    ),
    "src/helpers/ota/tinf/tinf.h": (
        "tinf/tinf.h",
        "f51fbba69e6efd3495fac28559df1e875e742951d4c1df1db8086785a6da4761",
    ),
    "src/helpers/ota/tinf/tinflate.c": (
        "tinf/tinflate.c",
        "a01033388bb784b859d36a1f04c16a2eb087539249144dd2a1f50278f07db610",
    ),
}

TRACKED_BACKPORT_FILES = (
    "platformio.ini",
    "src/Mesh.cpp",
    "src/helpers/ota/OtaContext.h",
    "src/helpers/ota/OtaFlashLayout_nrf52.h",
    "src/helpers/ota/OtaManager.cpp",
    "src/helpers/ota/OtaManager.h",
    "src/helpers/ota/OtaProtocol.h",
    "tools/mota/pio_endf.py",
)


@dataclass(frozen=True)
class Target:
    version: str
    source_commit: str
    bridge_stage: int = 0
    os_stage: int = 0
    os_stage_part: int = 0
    os_stage_parts: int = 0
    os_stage_subpart: int = 0
    os_stage_subparts: int = 0
    split_stage5: str = ""
    mymesh_opt: str = ""
    flood_rule_engine: bool = True


TARGETS = (
    Target("1.16.7.10", "90abbd110ef4fa7c96fca30205bbc566bf5c966c", bridge_stage=6),
    Target("1.16.7.11", "90abbd110ef4fa7c96fca30205bbc566bf5c966c", bridge_stage=7),
    Target("1.16.7.12", "90abbd110ef4fa7c96fca30205bbc566bf5c966c", bridge_stage=8),
    Target("1.16.7.13", "804f45303ac615362ab56f44da0789301bb3de11"),
    Target("1.16.8.0", "cd89b5400cbc16b31a87079a793bc88600369c80"),
    Target("1.16.8.7", "1fa940aa3a5d98d41a7320a25d60798edeae4921"),
    Target("1.16.8.8", "bbc2c905a1befb83365cccec583c527a24ebb104"),
    Target("1.16.8.9", "bec98977389bae5116d675107fa6c11eeab1a3f7"),
    Target("1.16.9.0", "1f7f2a8034f0fc74b378dfb44126ef869c2a9344"),
    Target("1.16.9.102", "63ab53e4445d726945ce32d530252e34ecf6d1b1"),
    Target("1.16.9.104", "262542cf3411bfd093978c502ebb94ba3ba3cc0c", flood_rule_engine=False),
    Target("1.16.9.105", "352d70465276d66cda5cbda955aa3dfffee39bbb", flood_rule_engine=False),
    Target("1.16.9.108", "352d70465276d66cda5cbda955aa3dfffee39bbb"),
    Target("1.16.9.109", "03edc027b19086918f567561cab673fe3724b9b8"),
    Target("1.16.9.110", "07a624af00cea922ae495612b728e4abf51f9f87"),
    Target("1.16.9.111", "4efd561dcdbafd345641f2a07689d4657b103eed", os_stage=1),
    Target("1.16.9.112", "217b78aac01f538cac49afe79886371a8b000528", os_stage=1),
    Target("1.16.9.113", "217b78aac01f538cac49afe79886371a8b000528", os_stage=2),
    Target("1.16.9.114", "217b78aac01f538cac49afe79886371a8b000528", os_stage=3),
    Target("1.16.9.115", "217b78aac01f538cac49afe79886371a8b000528", os_stage=4),
    Target(
        "1.16.9.116", "217b78aac01f538cac49afe79886371a8b000528",
        os_stage=5, split_stage5="first",
    ),
    Target("1.16.9.117", "217b78aac01f538cac49afe79886371a8b000528", os_stage=5),
    Target("1.16.9.118", "217b78aac01f538cac49afe79886371a8b000528", os_stage=7),
    Target(
        "1.16.9.119", "217b78aac01f538cac49afe79886371a8b000528",
        os_stage=8, mymesh_opt="O2_SIZE_1_INLINE_HOIST",
    ),
    Target(
        "1.16.9.120", "217b78aac01f538cac49afe79886371a8b000528",
        os_stage=8, mymesh_opt="O2_SIZE_1_HOIST",
    ),
    Target(
        "1.16.9.121", "217b78aac01f538cac49afe79886371a8b000528",
        os_stage=8, mymesh_opt="-Os",
    ),
    Target(
        "1.16.9.122", "217b78aac01f538cac49afe79886371a8b000528",
        os_stage=12, mymesh_opt="-Os",
    ),
    Target(
        "1.16.10.0", "217b78aac01f538cac49afe79886371a8b000528",
        os_stage=13, mymesh_opt="-Os",
    ),
)

LEGACY_MESH_COMMITS = {
    "90abbd110ef4fa7c96fca30205bbc566bf5c966c",
    "804f45303ac615362ab56f44da0789301bb3de11",
}


class BuildError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_exact(path: Path, old: bytes, new: bytes, label: str) -> None:
    data = path.read_bytes()
    count = data.count(old)
    if count != 1:
        raise BuildError(
            f"{label}: expected one exact anchor in {path}, found {count}"
        )
    path.write_bytes(data.replace(old, new, 1))


def inflate_asset_metadata() -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    for destination, (relative_source, expected_sha256) in INFLATE_ASSETS.items():
        source = INFLATE_ASSET_ROOT / relative_source
        if not source.is_file():
            raise BuildError(f"missing receive-inflate asset: {source}")
        actual_sha256 = sha256_file(source)
        if actual_sha256 != expected_sha256:
            raise BuildError(
                f"receive-inflate asset hash mismatch for {relative_source}: "
                f"{actual_sha256}, expected {expected_sha256}"
            )
        metadata[destination] = {
            "asset": relative_source,
            "sha256": actual_sha256,
        }
    return metadata


def install_inflate_assets(source: Path) -> None:
    for destination, (relative_source, _) in INFLATE_ASSETS.items():
        output = source / destination
        if output.exists():
            raise BuildError(
                f"historical source unexpectedly already contains {destination}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INFLATE_ASSET_ROOT / relative_source, output)


def remove_inflate_assets(source: Path) -> None:
    for destination in reversed(tuple(INFLATE_ASSETS)):
        output = source / destination
        if output.is_file() or output.is_symlink():
            output.unlink()
        elif output.exists():
            raise BuildError(f"receive-inflate asset is not a file: {output}")
    tinf_dir = source / "src/helpers/ota/tinf"
    if tinf_dir.exists():
        try:
            tinf_dir.rmdir()
        except OSError as exc:
            raise BuildError(f"receive-inflate asset directory is not empty: {tinf_dir}") from exc


def restore_backport(source: Path) -> None:
    run(
        [
            "git", "restore", "--source=HEAD", "--staged", "--worktree", "--",
            *TRACKED_BACKPORT_FILES,
        ],
        "restore prior backport",
        source,
    )
    remove_inflate_assets(source)


def validate_inflate_source(source: Path) -> None:
    manager_h = (source / "src/helpers/ota/OtaManager.h").read_text(encoding="utf-8")
    manager_cpp = (source / "src/helpers/ota/OtaManager.cpp").read_text(encoding="utf-8")
    context_h = (source / "src/helpers/ota/OtaContext.h").read_text(encoding="utf-8")
    protocol_h = (source / "src/helpers/ota/OtaProtocol.h").read_text(encoding="utf-8")
    platformio = (source / "platformio.ini").read_text(encoding="utf-8")
    required = {
        "void OTA send ABI": "typedef void (*OtaSend)",
        "void manager receive ABI": "void on_message(const uint8_t* msg, uint16_t len)",
        "requested block hook declaration": "uint16_t requestedBlockLength(",
        "requested block hook implementation": "uint16_t OtaManager::requestedBlockLength(",
        "inflater context member": "OtaTransportInflateReceiver transport_inflate;",
        "inflater context begin": "transport_inflate.begin(manager, target_id, send, ctx);",
        "v2 request marker": "OTA_REQ_V2_MARK             = 0x8000u",
        "v2 fragment size": "#define OTA_FRAG_DATA_V2 171",
        "tinf build filter": "+<helpers/ota/OtaTinf.c>",
    }
    haystacks = {
        "void OTA send ABI": manager_h,
        "void manager receive ABI": manager_h,
        "requested block hook declaration": manager_h,
        "requested block hook implementation": manager_cpp,
        "inflater context member": context_h,
        "inflater context begin": context_h,
        "v2 request marker": protocol_h,
        "v2 fragment size": manager_h,
        "tinf build filter": platformio,
    }
    for label, needle in required.items():
        if needle not in haystacks[label]:
            raise BuildError(f"receive-inflate validation lacks {label}: {needle}")
    for destination, (_, expected_sha256) in INFLATE_ASSETS.items():
        actual_sha256 = sha256_file(source / destination)
        if actual_sha256 != expected_sha256:
            raise BuildError(
                f"installed receive-inflate asset mismatch for {destination}: {actual_sha256}"
            )
    run(["git", "diff", "--check"], "backport whitespace check", source)


def apply_inflate_transforms(source: Path) -> None:
    """Apply the add51bf0 receive shim through exact, fail-closed anchors."""

    manager_h = source / "src/helpers/ota/OtaManager.h"
    replace_exact(
        manager_h,
        b"#ifndef OTA_FRAG_DATA\n"
        b"#define OTA_FRAG_DATA 160           // data bytes per DATA fragment (<= MAX_PACKET_PAYLOAD - 9-byte header)\n"
        b"#endif\n",
        b"#ifndef OTA_FRAG_DATA\n"
        b"#define OTA_FRAG_DATA 160           // deployed legacy DATA geometry; never change in place\n"
        b"#endif\n"
        b"#ifndef OTA_FRAG_DATA_V2\n"
        b"#define OTA_FRAG_DATA_V2 171        // negotiated DATA geometry (13-byte overhead => 184-byte packet payload)\n"
        b"#endif\n",
        "OTA fragment constants",
    )
    replace_exact(
        manager_h,
        b"  bool terminallyConsumes(const uint8_t* msg, uint16_t len);\n"
        b"  void on_message(const uint8_t* msg, uint16_t len);   // feed one received OTA message\n",
        b"  bool terminallyConsumes(const uint8_t* msg, uint16_t len);\n"
        b"  // Exact logical length of a block this receiver currently requested, or zero for unsolicited DATA.\n"
        b"  // Used by the historical transport shim without exposing or changing the manager's reassembly buffers.\n"
        b"  uint16_t requestedBlockLength(const uint8_t* manifest_id, uint16_t block) const;\n"
        b"  void on_message(const uint8_t* msg, uint16_t len);   // feed one received OTA message\n",
        "OtaManager receive shim declaration",
    )

    manager_cpp = source / "src/helpers/ota/OtaManager.cpp"
    replace_exact(
        manager_cpp,
        b"bool OtaManager::terminallyConsumes(const uint8_t* msg, uint16_t len) {\n",
        b"uint16_t OtaManager::requestedBlockLength(const uint8_t* manifest_id, uint16_t block) const {\n"
        b"  if (!manifest_id || !_fetch || _fstate != FETCHING || block >= _fbc ||\n"
        b"      memcmp(manifest_id, _fid, sizeof(_fid)) != 0 || findReassemblySlot(block) < 0) return 0;\n"
        b"  const uint32_t len = blockLen(block);\n"
        b"  return len <= OTA_MAX_BLOCK ? (uint16_t)len : 0;\n"
        b"}\n\n"
        b"bool OtaManager::terminallyConsumes(const uint8_t* msg, uint16_t len) {\n",
        "OtaManager receive shim implementation",
    )

    protocol_h = source / "src/helpers/ota/OtaProtocol.h"
    data_message = (
        b"struct DataMsg {\n"
        b"  uint8_t  manifest_id[4];\n"
        b"  uint16_t block_idx;\n"
        b"  uint16_t frag_off;\n"
        b"  const uint8_t* data; uint16_t data_len;\n"
        b"};\n"
    )
    v2_protocol = data_message + (
        b"\n// OTA_REQ want_mask extension. These bits sit outside every valid fragment bit for <=1 KiB blocks. A first\n"
        b"// v2 request deliberately includes all seven legacy fragment bits so an old source can answer it completely.\n"
        b"static const uint16_t OTA_REQ_V2_MARK             = 0x8000u;\n"
        b"static const uint16_t OTA_REQ_V2_ALLOW_DEFLATE    = 0x4000u;\n"
        b"static const uint16_t OTA_REQ_V2_RESERVED         = 0x2000u;\n"
        b"static const uint16_t OTA_REQ_V2_FRAGMENT_MASK    = 0x1FFFu;\n"
        b"\n// OTA_DATA v2 frag_off packing:\n"
        b"//   bit 15      v2 marker\n"
        b"//   bit 14      data[] is one raw-RFC1951-DEFLATE stream fragment (clear = raw block bytes)\n"
        b"//   bits 13..10 fragment index (0..15; byte offset = index * OTA_FRAG_DATA_V2)\n"
        b"//   bits  9..0  total encoded block length minus one (1..1024 bytes)\n"
        b"static const uint16_t OTA_DATA_V2_MARK             = 0x8000u;\n"
        b"static const uint16_t OTA_DATA_V2_DEFLATED         = 0x4000u;\n"
        b"static const uint16_t OTA_DATA_V2_FRAGMENT_BITS    = 0x3C00u;\n"
        b"static const uint16_t OTA_DATA_V2_LENGTH_BITS      = 0x03FFu;\n"
        b"static const uint8_t  OTA_DATA_V2_FRAGMENT_SHIFT   = 10;\n"
        b"static const uint16_t OTA_DATA_V2_MAX_ENCODED      = 1024;\n"
        b"static const uint8_t  OTA_DATA_V2_STREAM_ID_BYTES  = 4;\n"
        b"\ninline bool ota_req_is_v2(uint16_t want_mask) {\n"
        b"  return (want_mask & OTA_REQ_V2_MARK) != 0 && (want_mask & OTA_REQ_V2_RESERVED) == 0;\n"
        b"}\n"
        b"\ninline uint16_t ota_req_v2_fragments(uint16_t want_mask) {\n"
        b"  return (uint16_t)(want_mask & OTA_REQ_V2_FRAGMENT_MASK);\n"
        b"}\n"
        b"\ninline uint16_t ota_req_make_v2(uint16_t fragments, bool allow_deflate) {\n"
        b"  return (uint16_t)((fragments & OTA_REQ_V2_FRAGMENT_MASK) | OTA_REQ_V2_MARK |\n"
        b"                    (allow_deflate ? OTA_REQ_V2_ALLOW_DEFLATE : 0));\n"
        b"}\n"
        b"\ninline bool ota_data_v2_pack(uint8_t fragment, uint16_t encoded_len, bool deflated,\n"
        b"                             uint16_t& packed) {\n"
        b"  if (fragment >= 16 || encoded_len == 0 || encoded_len > OTA_DATA_V2_MAX_ENCODED) return false;\n"
        b"  packed = (uint16_t)(OTA_DATA_V2_MARK |\n"
        b"      (deflated ? OTA_DATA_V2_DEFLATED : 0) |\n"
        b"      ((uint16_t)fragment << OTA_DATA_V2_FRAGMENT_SHIFT) |\n"
        b"      (encoded_len - 1u));\n"
        b"  return true;\n"
        b"}\n"
        b"\ninline bool ota_data_v2_unpack(uint16_t packed, uint8_t& fragment, uint16_t& encoded_len,\n"
        b"                               bool& deflated) {\n"
        b"  if ((packed & OTA_DATA_V2_MARK) == 0) return false;\n"
        b"  fragment = (uint8_t)((packed & OTA_DATA_V2_FRAGMENT_BITS) >> OTA_DATA_V2_FRAGMENT_SHIFT);\n"
        b"  encoded_len = (uint16_t)((packed & OTA_DATA_V2_LENGTH_BITS) + 1u);\n"
        b"  deflated = (packed & OTA_DATA_V2_DEFLATED) != 0;\n"
        b"  return true;\n"
        b"}\n"
    )
    replace_exact(protocol_h, data_message, v2_protocol, "OTA v2 protocol helpers")

    context_h = source / "src/helpers/ota/OtaContext.h"
    replace_exact(
        context_h,
        b'#include "OtaManager.h"\n#include "OtaStore.h"\n',
        b'#include "OtaManager.h"\n#include "OtaDeflate.h"\n#include "OtaStore.h"\n',
        "OtaContext inflater include",
    )
    replace_exact(
        context_h,
        b"struct OtaContext {\n  OtaManager manager;\n",
        b"struct OtaContext {\n  OtaManager manager;\n  OtaTransportInflateReceiver transport_inflate;\n",
        "OtaContext inflater member",
    )
    replace_exact(
        context_h,
        b"  void begin(uint32_t target_id, OtaSend send, void* ctx, const char* hw = nullptr) {\n",
        b"  void on_message(const uint8_t* msg, uint16_t len) {\n"
        b"    transport_inflate.on_message(msg, len);\n"
        b"  }\n\n"
        b"  void begin(uint32_t target_id, OtaSend send, void* ctx, const char* hw = nullptr) {\n",
        "OtaContext inflater receive wrapper",
    )
    replace_exact(
        context_h,
        b"    manager.begin(target_id, send, ctx);\n",
        b"    transport_inflate.begin(manager, target_id, send, ctx);\n",
        "OtaContext inflater begin wrapper",
    )

    mesh_cpp = source / "src/Mesh.cpp"
    replace_exact(
        mesh_cpp,
        b"ota::ota_ctx().manager.on_message(pkt->payload, pkt->payload_len);",
        b"ota::ota_ctx().on_message(pkt->payload, pkt->payload_len);        ",
        "Mesh inflater receive wrapper",
    )

    platformio = source / "platformio.ini"
    replace_exact(
        platformio,
        b"  +<helpers/*.cpp>\n  ; MQTT-only sources",
        b"  +<helpers/*.cpp>\n  +<helpers/ota/OtaTinf.c>\n  ; MQTT-only sources",
        "PlatformIO tinf C source",
    )

    install_inflate_assets(source)


def run(
    command: list[str],
    label: str,
    cwd: Path = REPO_ROOT,
    timeout: int = 900,
    env: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"{label} failed: {exc}") from exc
    if result.returncode != 0:
        raise BuildError(
            f"{label} exited {result.returncode}:\n{result.stdout[-6000:]}"
        )
    return result.stdout


def read_image(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if item.filename == "firmware.bin"]
        if len(members) != 1:
            raise BuildError(f"{path} does not contain one root firmware.bin")
        return archive.read(members[0])


def verify_image(path: Path, target: Target) -> dict[str, object]:
    image = read_image(path)
    if not motalib.has_endf(image):
        raise BuildError(f"{path} has no valid EndF")
    ident = motalib.parse_endf_ident(image)
    assert ident is not None
    version = motalib.unpack_version(ident.fw_version)
    if version != target.version:
        raise BuildError(f"{path} version is {version}, expected {target.version}")
    if ident.target_id != EXPECTED_TARGET_ID or ident.hw_id != EXPECTED_HARDWARE:
        raise BuildError(
            f"{path} identity is target={ident.target_id:08X} hw={ident.hw_id!r}"
        )
    body, body_hash = motalib.parse_endf(image)
    return {
        "version": target.version,
        "source_commit": target.source_commit,
        "zip": path.name,
        "firmware_size": len(image),
        "firmware_sha256": hashlib.sha256(image).hexdigest(),
        "body_size": len(body),
        "body_hash": body_hash.hex(),
    }


def apply_backport(source: Path, commit: str) -> None:
    required = (
        COMMON_PATCH,
        LEGACY_MESH_PATCH if commit in LEGACY_MESH_COMMITS else GUARDED_MESH_PATCH,
        WORKSPACE_PATCH,
    )
    for patch in required:
        run(["git", "apply", "--check", str(patch)], f"check {patch.name}", source)
        run(["git", "apply", str(patch)], f"apply {patch.name}", source)
    version_check = subprocess.run(
        ["git", "apply", "--check", str(VERSION_PATCH)],
        cwd=source,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if version_check.returncode == 0:
        run(["git", "apply", str(VERSION_PATCH)], f"apply {VERSION_PATCH.name}", source)
    else:
        run(
            ["git", "apply", "--reverse", "--check", str(VERSION_PATCH)],
            f"confirm existing {VERSION_PATCH.name}",
            source,
        )
    apply_inflate_transforms(source)
    validate_inflate_source(source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--validate-backports-only",
        action="store_true",
        help="apply and verify every unique pinned source without running PlatformIO",
    )
    parser.add_argument("--keep-worktree", action="store_true")
    args = parser.parse_args()

    for patch in (
        COMMON_PATCH, LEGACY_MESH_PATCH, GUARDED_MESH_PATCH, VERSION_PATCH,
        WORKSPACE_PATCH,
    ):
        if not patch.is_file():
            raise BuildError(f"missing backport patch: {patch}")
    if not SELECTIVE_OS_HOOK.is_file():
        raise BuildError(f"missing selective optimizer hook: {SELECTIVE_OS_HOOK}")
    asset_metadata = inflate_asset_metadata()
    if not args.validate_backports_only and args.output_dir is None:
        raise BuildError("--output-dir is required unless --validate-backports-only is used")
    if args.work_dir.exists():
        raise BuildError(f"work directory already exists: {args.work_dir}")
    if args.output_dir is not None and args.output_dir.exists():
        raise BuildError(f"output directory already exists: {args.output_dir}")
    args.work_dir.mkdir(parents=True)
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True)
    log_dir = args.work_dir / "logs"
    if not args.validate_backports_only:
        log_dir.mkdir()
    source = args.work_dir / "source"

    run(
        ["git", "worktree", "add", "--detach", str(source), TARGETS[0].source_commit],
        "create build worktree",
    )
    current_commit = ""
    records: list[dict[str, object]] = []
    succeeded = False
    try:
        targets = TARGETS
        if args.validate_backports_only:
            unique: dict[str, Target] = {}
            for target in TARGETS:
                unique.setdefault(target.source_commit, target)
            targets = tuple(unique.values())
        for index, target in enumerate(targets, 1):
            if target.source_commit != current_commit:
                if current_commit:
                    restore_backport(source)
                    run(
                        ["git", "checkout", "--detach", target.source_commit],
                        f"checkout {target.source_commit}",
                        source,
                    )
                apply_backport(source, target.source_commit)
                current_commit = target.source_commit

            if args.validate_backports_only:
                print(
                    f"[validate] {index:02d}/{len(targets)} "
                    f"{target.source_commit} receive-inflate backport applies",
                    flush=True,
                )
                continue

            firmware_version = f"v{target.version}-{VERSION_SUFFIX}"
            build_env = dict(os.environ)
            for name in (
                "MOTA_BRIDGE_OS_STAGE", "MOTA_BRIDGE_OS_STAGE_PART",
                "MOTA_BRIDGE_OS_STAGE_PARTS", "MOTA_BRIDGE_OS_STAGE_SUBPART",
                "MOTA_BRIDGE_OS_STAGE_SUBPARTS", "MOTA_BRIDGE_SPLIT_STAGE5",
                "MOTA_BRIDGE_MY_MESH_OPT", "MOTA_BRIDGE_OS_VERBOSE",
                "PLATFORMIO_BUILD_FLAGS", "PLATFORMIO_EXTRA_SCRIPTS",
            ):
                build_env.pop(name, None)
            build_env["DISABLE_DEBUG"] = "1"
            flags = [
                "-DLORA_FREQ=910.525", "-DLORA_BW=62.5", "-DLORA_SF=7",
                "-DLORA_CR=5", "-DOTA_FETCH_PIPELINE=1", "-DOTA_TX_PRIORITY=0",
            ]
            if not target.flood_rule_engine:
                flags.append("-DMESH_ENABLE_FLOOD_RULE_ENGINE=0")
            if target.bridge_stage:
                flags.append(f"-DMOTA_BRIDGE_586_STAGE={target.bridge_stage}")
            if target.os_stage:
                build_env["MOTA_BRIDGE_OS_STAGE"] = str(target.os_stage)
                build_env["PLATFORMIO_EXTRA_SCRIPTS"] = f"pre:{SELECTIVE_OS_HOOK}"
            if target.os_stage_parts:
                build_env["MOTA_BRIDGE_OS_STAGE_PART"] = str(target.os_stage_part)
                build_env["MOTA_BRIDGE_OS_STAGE_PARTS"] = str(target.os_stage_parts)
            if target.os_stage_subparts:
                build_env["MOTA_BRIDGE_OS_STAGE_SUBPART"] = str(target.os_stage_subpart)
                build_env["MOTA_BRIDGE_OS_STAGE_SUBPARTS"] = str(target.os_stage_subparts)
            if target.split_stage5:
                build_env["MOTA_BRIDGE_SPLIT_STAGE5"] = target.split_stage5
            if target.mymesh_opt:
                build_env["MOTA_BRIDGE_MY_MESH_OPT"] = target.mymesh_opt
            build_env["PLATFORMIO_BUILD_FLAGS"] = " ".join(flags)
            run(
                ["pio", "run", "-e", ENV_NAME, "-t", "clean"],
                f"clean {target.version}",
                source,
            )
            output = run(
                [
                    "bash", "build.sh", "build-firmware", ENV_NAME,
                    "--profile", "cascade", "--firmware-version", firmware_version,
                ],
                f"build {target.version}",
                source,
                env=build_env,
            )
            (log_dir / f"build-{index:02d}-v{target.version}.log").write_text(
                output, encoding="utf-8"
            )
            for required in (*flags, "-DMOTA_TARGET_ID=0x2fa509c1", "SUCCESS"):
                if required not in output:
                    raise BuildError(f"build {target.version} log lacks {required}")
            if target.os_stage and f"selective optimizer stage {target.os_stage}/13" not in output:
                raise BuildError(f"build {target.version} did not run the selective optimizer")
            stem = f"{ENV_NAME}-ota-{firmware_version}-{target.source_commit[:8]}"
            built_zip = source / "out" / f"{stem}.zip"
            built_uf2 = source / "out" / f"{stem}.uf2"
            if not built_zip.is_file() or not built_uf2.is_file():
                raise BuildError(f"build {target.version} did not emit the expected ZIP and UF2")
            assert args.output_dir is not None
            output_zip = args.output_dir / built_zip.name
            output_uf2 = args.output_dir / built_uf2.name
            shutil.copy2(built_zip, output_zip)
            shutil.copy2(built_uf2, output_uf2)
            record = verify_image(output_zip, target)
            record["uf2"] = output_uf2.name
            record["zip_sha256"] = sha256_file(output_zip)
            record["uf2_sha256"] = sha256_file(output_uf2)
            record["build_flags"] = flags
            record["bridge_stage"] = target.bridge_stage
            record["os_stage"] = target.os_stage
            record["os_stage_part"] = target.os_stage_part
            record["os_stage_parts"] = target.os_stage_parts
            record["os_stage_subpart"] = target.os_stage_subpart
            record["os_stage_subparts"] = target.os_stage_subparts
            record["split_stage5"] = target.split_stage5
            record["mymesh_opt"] = target.mymesh_opt
            record["flood_rule_engine"] = target.flood_rule_engine
            records.append(record)
            print(
                f"[build] {index:02d}/{len(TARGETS)} v{target.version} "
                f"image={record['firmware_size']} sha={str(record['firmware_sha256'])[:16]}",
                flush=True,
            )

        if args.validate_backports_only:
            succeeded = True
            print(
                f"[validate] all {len(targets)} unique pinned source commits passed",
                flush=True,
            )
            return 0

        assert args.output_dir is not None
        manifest = {
            "environment": ENV_NAME,
            "target_id": f"{EXPECTED_TARGET_ID:08X}",
            "hardware": EXPECTED_HARDWARE,
            "version_suffix": VERSION_SUFFIX,
            "inflate_snapshot_commit": INFLATE_SNAPSHOT_COMMIT,
            "inflate_assets": asset_metadata,
            "builder_script": Path(__file__).name,
            "builder_script_sha256": sha256_file(Path(__file__)),
            "common_patch": COMMON_PATCH.name,
            "common_patch_sha256": sha256_file(COMMON_PATCH),
            "legacy_mesh_patch": LEGACY_MESH_PATCH.name,
            "legacy_mesh_patch_sha256": sha256_file(LEGACY_MESH_PATCH),
            "guarded_mesh_patch": GUARDED_MESH_PATCH.name,
            "guarded_mesh_patch_sha256": sha256_file(GUARDED_MESH_PATCH),
            "version_patch": VERSION_PATCH.name,
            "version_patch_sha256": sha256_file(VERSION_PATCH),
            "workspace_patch": WORKSPACE_PATCH.name,
            "workspace_patch_sha256": sha256_file(WORKSPACE_PATCH),
            "selective_os_hook": SELECTIVE_OS_HOOK.name,
            "selective_os_hook_sha256": sha256_file(SELECTIVE_OS_HOOK),
            "transport_fragment_bytes": 171,
            "transport_codec": "raw-deflate-receive-only",
            "fetch_pipeline": 1,
            "targets": records,
        }
        (args.output_dir / "images.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="ascii"
        )
        succeeded = True
        print(f"[manifest] {args.output_dir / 'images.json'}", flush=True)
    finally:
        if succeeded and not args.keep_worktree:
            run(
                ["git", "worktree", "remove", "--force", str(source)],
                "remove build worktree",
            )
            if args.validate_backports_only:
                args.work_dir.rmdir()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
