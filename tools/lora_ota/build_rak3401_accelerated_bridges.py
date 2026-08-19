#!/usr/bin/env python3
"""Build terminal-suppressed RAK3401 images for the historical mOTA chain.

The input commits are pinned because each image is a deliberate binary bridge.
The script uses one temporary Git worktree, applies the reviewed backport
patches without committing them, and emits a machine-readable image manifest.
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
VERSION_SUFFIX = "halo-keymind-cascade-mota-fast"
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
COMMON_PATCH = SCRIPT_DIR / "rak3401_terminal_ota_backport.patch"
LEGACY_MESH_PATCH = SCRIPT_DIR / "rak3401_terminal_ota_mesh_legacy.patch"
GUARDED_MESH_PATCH = SCRIPT_DIR / "rak3401_terminal_ota_mesh_guarded.patch"
VERSION_PATCH = SCRIPT_DIR / "rak3401_four_component_endf_backport.patch"
WORKSPACE_PATCH = SCRIPT_DIR / "rak3401_dynamic_workspace_backport.patch"
SELECTIVE_OS_HOOK = SCRIPT_DIR / "rak3401_selective_os.py"


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
    run(["git", "diff", "--check"], "backport whitespace check", source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
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
    if args.work_dir.exists():
        raise BuildError(f"work directory already exists: {args.work_dir}")
    if args.output_dir.exists():
        raise BuildError(f"output directory already exists: {args.output_dir}")
    args.work_dir.mkdir(parents=True)
    args.output_dir.mkdir(parents=True)
    log_dir = args.work_dir / "logs"
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
        for index, target in enumerate(TARGETS, 1):
            if target.source_commit != current_commit:
                if current_commit:
                    run(
                        [
                            "git", "restore", "--source=HEAD", "--staged", "--worktree", "--",
                            "src/Mesh.cpp", "src/helpers/ota/OtaManager.cpp",
                            "src/helpers/ota/OtaManager.h",
                            "src/helpers/ota/OtaFlashLayout_nrf52.h",
                            "tools/mota/pio_endf.py",
                        ],
                        "restore prior backport",
                        source,
                    )
                    run(
                        ["git", "checkout", "--detach", target.source_commit],
                        f"checkout {target.source_commit}",
                        source,
                    )
                apply_backport(source, target.source_commit)
                current_commit = target.source_commit

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
                "-DLORA_CR=5", "-DOTA_FETCH_PIPELINE=4", "-DOTA_TX_PRIORITY=0",
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

        manifest = {
            "environment": ENV_NAME,
            "target_id": f"{EXPECTED_TARGET_ID:08X}",
            "hardware": EXPECTED_HARDWARE,
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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
