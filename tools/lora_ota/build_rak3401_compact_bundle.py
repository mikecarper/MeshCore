#!/usr/bin/env python3
"""Build the compact legacy-bootloader RAK3401 mOTA chain.

The first package is retained byte-for-byte from the pinned physical baseline.
Later packages use page-aligned detools workspaces selected by the audited
route plan. Every package must fit below the deployed bootloader's original
0xD4000 scan ceiling; no expanded-ceiling bootloader feature is assumed.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "mota"))

import motalib  # noqa: E402
import rebuild_rak3401_bundle as common  # noqa: E402


PREVIOUS_BUNDLE_SHA256 = (
    "b2781e02460b200a7c37bfae352bad81618716e550d1d042dca8aa29bfc73c29"
)
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
EXPECTED_ENDPOINT_VERSION = "1.17.1.02"
EXPECTED_ENDPOINT_VERSION_PACKED = motalib.pack_version(EXPECTED_ENDPOINT_VERSION)
EXPECTED_ROUTE_NODES = (0, 1, 2, 7, 13, 16, 17, 22, 24, 30)
EXPECTED_STEPS = len(EXPECTED_ROUTE_NODES) - 1
APP_BASE = 0x26000
STAGE_CEILING = 0xD4000
FIXED_WORKSPACE = 0x98000
FLASH_PAGE = 4096
SEGMENT_SIZE = 4096
BLOCK_SIZE = 1024


class CompactBuildError(RuntimeError):
    pass


def run(command: list[str], label: str, cwd: Path | None = None, timeout: int = 900) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CompactBuildError(f"{label} failed: {exc}") from exc
    if result.returncode != 0:
        raise CompactBuildError(
            f"{label} exited {result.returncode}:\n{result.stdout[-6000:]}"
        )
    return result.stdout


def align_up(value: int, unit: int) -> int:
    return (value + unit - 1) // unit * unit


def align_down(value: int, unit: int) -> int:
    return value // unit * unit


def parse_int(value: object, label: str) -> int:
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", value):
        result = int(value, 0)
    else:
        raise CompactBuildError(f"{label} must be an integer or 0x-prefixed integer")
    if result < 0:
        raise CompactBuildError(f"{label} cannot be negative")
    return result


def parse_simulators(values: list[str]) -> list[tuple[str, Path]]:
    simulators: list[tuple[str, Path]] = []
    for value in values:
        label, separator, raw_path = value.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
            raise CompactBuildError("--apply-sim must use LABEL=/path/to/apply_sim")
        path = Path(raw_path)
        if not path.is_file():
            raise CompactBuildError(f"apply simulator does not exist: {path}")
        simulators.append((label, path))
    return simulators


def read_route(path: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    try:
        document = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompactBuildError(f"cannot read route plan {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise CompactBuildError("route plan must use schema 1")
    if parse_int(document.get("app_base"), "route app_base") != APP_BASE:
        raise CompactBuildError("route plan has the wrong application base")
    if parse_int(document.get("stage_ceiling"), "route stage_ceiling") != STAGE_CEILING:
        raise CompactBuildError("route plan does not target the deployed 0xD4000 ceiling")
    if document.get("shortest_package_count") != EXPECTED_STEPS:
        raise CompactBuildError(
            f"route plan shortest_package_count must be {EXPECTED_STEPS}"
        )
    raw_steps = document.get("steps")
    if not isinstance(raw_steps, list) or len(raw_steps) != EXPECTED_STEPS:
        raise CompactBuildError(f"route plan must contain {EXPECTED_STEPS} steps")
    steps: list[dict[str, object]] = []
    nodes = []
    for number, raw in enumerate(raw_steps, 1):
        if not isinstance(raw, dict):
            raise CompactBuildError(f"route step {number} must be an object")
        source = parse_int(raw.get("source_node"), f"route step {number} source_node")
        target = parse_int(raw.get("target_node"), f"route step {number} target_node")
        memory = parse_int(raw.get("inplace_memory"), f"route step {number} inplace_memory")
        expected_size = parse_int(
            raw.get("expected_container_size"),
            f"route step {number} expected_container_size",
        )
        expected_margin = parse_int(
            raw.get("expected_staging_margin"),
            f"route step {number} expected_staging_margin",
        )
        expected_sha = raw.get("expected_target_sha256")
        expected_version = raw.get("expected_target_version")
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise CompactBuildError(f"route step {number} target SHA-256 is invalid")
        if not isinstance(expected_version, str):
            raise CompactBuildError(f"route step {number} target version is invalid")
        try:
            motalib.pack_version(expected_version)
        except (TypeError, ValueError):
            raise CompactBuildError(f"route step {number} target version is invalid") from None
        reuse = raw.get("reuse_baseline_package", False)
        if not isinstance(reuse, bool):
            raise CompactBuildError(f"route step {number} reuse flag must be boolean")
        if source >= target or memory % FLASH_PAGE:
            raise CompactBuildError(f"route step {number} has invalid nodes or workspace alignment")
        if memory < FLASH_PAGE or APP_BASE + memory >= STAGE_CEILING:
            raise CompactBuildError(f"route step {number} workspace is outside legacy flash")
        if number == 1:
            nodes.append(source)
        elif source != steps[-1]["target_node"]:
            raise CompactBuildError(f"route step {number} is discontinuous")
        nodes.append(target)
        steps.append({
            "source_node": source,
            "target_node": target,
            "inplace_memory": memory,
            "reuse_baseline_package": reuse,
            "expected_container_size": expected_size,
            "expected_staging_margin": expected_margin,
            "expected_target_sha256": expected_sha,
            "expected_target_version": expected_version,
        })
    if tuple(nodes) != EXPECTED_ROUTE_NODES:
        raise CompactBuildError(
            f"route nodes are {tuple(nodes)}, expected {EXPECTED_ROUTE_NODES}"
        )
    if not steps[0]["reuse_baseline_package"] or any(
        step["reuse_baseline_package"] for step in steps[1:]
    ):
        raise CompactBuildError("only the first route step may reuse the baseline package")
    if steps[0]["inplace_memory"] != FIXED_WORKSPACE or steps[1]["inplace_memory"] != FIXED_WORKSPACE:
        raise CompactBuildError("the two fixed-receiver steps require a 0x98000 workspace")
    return steps, document


def read_input_rows(root: Path) -> list[dict[str, str]]:
    try:
        with (root / "CHAIN.csv").open(newline="", encoding="ascii") as source:
            rows = list(csv.DictReader(source))
    except OSError as exc:
        raise CompactBuildError(f"cannot read baseline CHAIN.csv: {exc}") from exc
    if len(rows) != 30:
        raise CompactBuildError(f"baseline chain has {len(rows)} steps, expected 30")
    return rows


def firmware_identity(image: bytes) -> tuple[int, str, str, int]:
    if not motalib.has_endf(image):
        raise CompactBuildError("firmware image has no valid EndF")
    ident = motalib.parse_endf_ident(image)
    assert ident is not None
    if ident.target_id != EXPECTED_TARGET_ID or ident.hw_id != EXPECTED_HARDWARE:
        raise CompactBuildError(
            f"firmware identity is target={ident.target_id:08X} hw={ident.hw_id!r}"
        )
    body, body_hash = motalib.parse_endf(image)
    return ident.fw_version, body_hash.hex(), motalib.unpack_version(ident.fw_version), len(body)


def apply_in_place(
    detools: Path,
    base: bytes,
    payload: bytes,
    target_size: int,
    memory_size: int,
    work: Path,
    label: str,
    fill: int,
) -> bytes:
    if len(base) > memory_size or target_size > memory_size:
        raise CompactBuildError(f"{label} image exceeds the selected workspace")
    memory_path = work / f"{label}-{fill:02x}.memory"
    payload_path = work / f"{label}.patch"
    memory = bytearray([fill]) * memory_size
    memory[:len(base)] = base
    memory_path.write_bytes(memory)
    if not payload_path.exists():
        payload_path.write_bytes(payload)
    run(
        [str(detools), "apply_patch_in_place", str(memory_path), str(payload_path)],
        f"apply {label} with fill 0x{fill:02x}",
    )
    result = memory_path.read_bytes()[:target_size]
    memory_path.unlink()
    return result


def patch_geometry(detools: Path, payload_path: Path, label: str) -> tuple[int, int, int, int]:
    output = run(
        [str(detools), "patch_info", "--no-human", str(payload_path)],
        f"inspect {label}",
    )
    values: dict[str, int] = {}
    for key, output_key in (
        ("Memory size", "memory"),
        ("Segment size", "segment"),
        ("From size", "from_size"),
        ("To size", "to_size"),
    ):
        match = re.search(rf"^{re.escape(key)}:\s+([0-9]+) bytes$", output, re.MULTILINE)
        if match is None:
            raise CompactBuildError(f"{label} patch_info is missing {key}")
        values[output_key] = int(match.group(1))
    return values["memory"], values["segment"], values["from_size"], values["to_size"]


def write_chain(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "step", "from_version", "to_version", "mota_file", "mota_size",
        "inplace_memory", "stage_start", "workspace_end", "staging_margin",
        "target_image_size", "base_body_hash", "target_body_hash",
        "target_sha256", "source_commit",
    ]
    with path.open("w", newline="", encoding="ascii") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_docs(
    root: Path,
    source_commit: str,
    endpoint: bytes,
    previous_sha: str,
    bridge_sha: str,
    route_sha: str,
    rows: list[dict[str, object]],
) -> None:
    endpoint_sha = common.sha256_bytes(endpoint)
    _packed, endpoint_body, _version, _body_size = firmware_identity(endpoint)
    total_bytes = sum(int(row["mota_size"]) for row in rows)
    largest = max(int(row["mota_size"]) for row in rows)
    minimum_margin = min(int(row["staging_margin"]) for row in rows)
    (root / "README.md").write_text(
        f"""# RAK3401 compact mOTA update chain

This archive contains {EXPECTED_STEPS} mandatory mOTA packages from the deployed
`1.16.7.0-c1caa5ad` image to
`v{EXPECTED_ENDPOINT_VERSION}-halo-keymind-cascade-dev-{source_commit[:8]}`.

The deployed bootloader is unchanged. Every flash package is bottom-aligned
below its original `0x{STAGE_CEILING:X}` scan ceiling, and every encoded detools
workspace ends at or below that package. No byte from `0x{STAGE_CEILING:X}`
through `0xED000` is used by this chain.

- Packages: {EXPECTED_STEPS} (the replaced chain used 30)
- Total transferred bytes: {total_bytes:,}
- Largest package: {largest:,} bytes
- Smallest workspace-to-stage margin: {minimum_margin:,} bytes
- Endpoint image SHA-256: `{endpoint_sha}`
- Endpoint EndF body hash: `{endpoint_body.upper()}`

Step 1 is byte-for-byte identical to the physically passed baseline package.
Step 2 installs the dynamic-workspace receiver while still obeying the old
fixed `0x{FIXED_WORKSPACE:X}` receiver limit. Steps 3-{EXPECTED_STEPS} use the
page-aligned workspace selected in `ROUTE.json`.

All {EXPECTED_STEPS} transitions passed container verification, zero-filled and
erased-workspace reconstruction, the supplied legacy bootloader simulator, and
the exact physical chain on a deployed RAK3401 1W on 19-Aug-2026. Use the pinned
chain runner and do not install packages out of order.
""",
        encoding="ascii",
    )
    (root / "PROVENANCE.md").write_text(
        f"""# Build and validation provenance

Endpoint source commit: `{source_commit}`
Endpoint requested version: `{EXPECTED_ENDPOINT_VERSION}`
Endpoint packed version: `0x{EXPECTED_ENDPOINT_VERSION_PACKED:08X}`
Endpoint profile name: `halo-keymind-cascade-dev`
Legacy stage ceiling: `0x{STAGE_CEILING:X}`
Application base: `0x{APP_BASE:X}`
Segment size: `{SEGMENT_SIZE}`
Logical mOTA block size: `{BLOCK_SIZE}`

Pinned 30-step reconstruction input SHA-256: `{previous_sha}`
Dynamic bridge-image manifest SHA-256: `{bridge_sha}`
Compact route plan SHA-256: `{route_sha}`

The route plan records the shortest path found by sweeping every page-aligned
workspace relevant to a path shorter than {EXPECTED_STEPS} packages. The
bundle generator independently checks the selected geometry against the old
bootloader ceiling before invoking either simulator.
""",
        encoding="ascii",
    )
    (root / "RUNBOOK.md").write_text(
        """# Runbook

Use `tools/lora_ota/rak3401_mota_chain.py` from the matching MeshCore commit.
Run `--verify-only`, then `--preflight-only`, before a live attempt. Live use
is enabled for this physically qualified exact chain. Keep the persistent work
directory so an interrupted transfer resumes from the target's exact EndF body
hash.

Do not use `--inplace-memory 0x98000` as a manual override for these files.
Each package already carries its audited workspace in the detools payload.
""",
        encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-bundle", type=Path, required=True)
    parser.add_argument("--endpoint-zip", type=Path, required=True)
    parser.add_argument("--endpoint-uf2", type=Path, required=True)
    parser.add_argument("--accelerated-images", type=Path, required=True)
    parser.add_argument("--route-plan", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--motatool", type=Path, required=True)
    parser.add_argument("--detools", type=Path, required=True)
    parser.add_argument(
        "--apply-sim",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="apply simulator and stable provenance label; repeat to validate multiple bootloaders",
    )
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()

    for path, label in (
        (args.previous_bundle, "previous bundle"),
        (args.endpoint_zip, "endpoint ZIP"),
        (args.endpoint_uf2, "endpoint UF2"),
        (args.accelerated_images, "accelerated image manifest"),
        (args.route_plan, "route plan"),
        (args.motatool, "motatool"),
        (args.detools, "detools"),
    ):
        if not path.is_file():
            raise CompactBuildError(f"{label} does not exist: {path}")
    simulators = parse_simulators(args.apply_sim)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise CompactBuildError("source commit must be a full lowercase 40-hex commit")
    resolved = run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", f"{args.source_commit}^{{commit}}"],
        "resolve endpoint source commit",
    ).strip()
    if resolved != args.source_commit:
        raise CompactBuildError(
            f"endpoint source resolves to {resolved}, expected {args.source_commit}"
        )
    if args.work_dir.exists():
        raise CompactBuildError(f"work directory already exists: {args.work_dir}")
    args.work_dir.mkdir(parents=True)

    route, route_document = read_route(args.route_plan)
    if (
        route_document.get("endpoint_source_commit") != args.source_commit
        or route_document.get("endpoint_version") != EXPECTED_ENDPOINT_VERSION
        or route_document.get("endpoint_name") != "halo-keymind-cascade-dev"
    ):
        raise CompactBuildError("route plan endpoint identity does not match this build")
    previous_sha = common.sha256_file(args.previous_bundle)
    if previous_sha != PREVIOUS_BUNDLE_SHA256:
        raise CompactBuildError(
            f"previous bundle SHA-256 is {previous_sha}, expected {PREVIOUS_BUNDLE_SHA256}"
        )
    old_root = common.extract_zip(args.previous_bundle, args.work_dir / "old")
    old_rows = read_input_rows(old_root)
    start_zip = old_root / "recovery/test-start/RAK3401-test-start-v1.16.7-c1caa5ad.zip"
    image0 = common.read_firmware_zip(start_zip)
    firmware_identity(image0)
    first_relative = common.safe_relative(old_rows[0]["mota_file"], "baseline step-1 mOTA")
    first_package_path = old_root.joinpath(*first_relative.parts)
    first_package = motalib.parse_container(first_package_path.read_bytes())
    reconstruction = args.work_dir / "reconstruction"
    reconstruction.mkdir()
    image1 = apply_in_place(
        args.detools, image0, first_package.payload,
        int(old_rows[0]["target_image_size"]), FIXED_WORKSPACE,
        reconstruction, "baseline-step-01", 0xFF,
    )
    image1_zero = apply_in_place(
        args.detools, image0, first_package.payload,
        int(old_rows[0]["target_image_size"]), FIXED_WORKSPACE,
        reconstruction, "baseline-step-01", 0x00,
    )
    if image1 != image1_zero or common.sha256_bytes(image1) != old_rows[0]["target_sha256"]:
        raise CompactBuildError("baseline step 1 does not reconstruct its pinned target")

    accelerated, accelerated_sources = common.read_accelerated_images(args.accelerated_images)
    endpoint = common.read_firmware_zip(args.endpoint_zip)
    endpoint_packed, _body_hash, _version_text, _body_size = firmware_identity(endpoint)
    if endpoint_packed != EXPECTED_ENDPOINT_VERSION_PACKED:
        raise CompactBuildError(
            f"endpoint packed version is 0x{endpoint_packed:08X}, "
            f"expected 0x{EXPECTED_ENDPOINT_VERSION_PACKED:08X}"
        )
    expected_label = f"v{EXPECTED_ENDPOINT_VERSION}-halo-keymind-cascade-dev-{args.source_commit[:8]}"
    if expected_label.encode("ascii") not in endpoint:
        raise CompactBuildError(f"endpoint firmware does not contain {expected_label}")

    images = {0: image0, 1: image1}
    sources = {1: old_rows[0]["source_commit"]}
    for node, (image, source_commit) in enumerate(zip(accelerated, accelerated_sources), 2):
        images[node] = image
        sources[node] = source_commit
    images[30] = endpoint
    sources[30] = args.source_commit
    expected_nodes = set(range(31))
    if set(images) != expected_nodes:
        # Do not silently accept a shortened or reordered bridge manifest.
        raise CompactBuildError(f"assembled image nodes are {sorted(images)}")

    short_commit = args.source_commit[:8]
    root_name = (
        "RAK3401-update-chain-v1.16.7-c1caa5ad-to-"
        f"v{EXPECTED_ENDPOINT_VERSION}-{short_commit}"
    )
    root = args.work_dir / root_name
    motas = root / "motas"
    recovery_start = root / "recovery/test-start"
    recovery_final = root / "recovery/final"
    motas.mkdir(parents=True)
    recovery_start.mkdir(parents=True)
    recovery_final.mkdir(parents=True)
    validation_work = args.work_dir / "validation"
    validation_work.mkdir()

    output_rows: list[dict[str, object]] = []
    validation_steps: list[dict[str, object]] = []
    for number, plan_step in enumerate(route, 1):
        source_node = int(plan_step["source_node"])
        target_node = int(plan_step["target_node"])
        memory_size = int(plan_step["inplace_memory"])
        base_image = images[source_node]
        target_image = images[target_node]
        base_packed, base_body, base_version, _base_body_size = firmware_identity(base_image)
        target_packed, target_body, target_version, _target_body_size = firmware_identity(target_image)
        from_version = base_version
        to_version = EXPECTED_ENDPOINT_VERSION if target_node == 30 else target_version
        filename = f"step-{number:02d}__v{from_version}-to-v{to_version}.mota"
        output_path = motas / filename
        base_path = validation_work / f"base-{number:02d}.bin"
        target_path = validation_work / f"target-{number:02d}.bin"
        payload_path = validation_work / f"payload-{number:02d}.patch"
        base_path.write_bytes(base_image)
        target_path.write_bytes(target_image)
        if bool(plan_step["reuse_baseline_package"]):
            shutil.copy2(first_package_path, output_path)
            build_kind = "byte-identical physical baseline"
        else:
            build_output = run(
                [
                    str(args.motatool), "build", "--fw", str(target_path),
                    "--base", str(base_path), "--patch-type", "in-place",
                    "--inplace-memory", hex(memory_size),
                    "--segment-size", str(SEGMENT_SIZE),
                    "--block-size", str(BLOCK_SIZE), "--out", str(output_path),
                ],
                f"build compact step {number}",
            )
            if not build_output.strip():
                raise CompactBuildError(f"motatool produced no output for step {number}")
            build_kind = "freshly generated"
        run([str(args.motatool), "verify", str(output_path)], f"verify compact step {number}")
        parsed = motalib.parse_container(output_path.read_bytes())
        payload_path.write_bytes(parsed.payload)
        patch_memory, patch_segment, patch_from, patch_to = patch_geometry(
            args.detools, payload_path, f"compact step {number}"
        )
        if (
            patch_memory != memory_size
            or patch_segment != SEGMENT_SIZE
            or patch_from != len(base_image)
            or patch_to != len(target_image)
        ):
            raise CompactBuildError(
                f"step {number} detools geometry does not match its route plan/images"
            )
        if parsed.manifest.fw_version != target_packed:
            raise CompactBuildError(f"step {number} manifest version does not match target EndF")
        if parsed.manifest.base_hash.hex() != base_body:
            raise CompactBuildError(f"step {number} manifest base hash mismatch")
        target_sha = common.sha256_bytes(target_image)
        if parsed.manifest.image_hash.hex() != target_sha:
            raise CompactBuildError(f"step {number} manifest target hash mismatch")
        if target_sha != plan_step["expected_target_sha256"]:
            raise CompactBuildError(f"step {number} target does not match its route pin")
        if target_packed != motalib.pack_version(str(plan_step["expected_target_version"])):
            raise CompactBuildError(f"step {number} target version does not match its route pin")
        if parsed.manifest.target_id != EXPECTED_TARGET_ID:
            raise CompactBuildError(f"step {number} manifest target ID mismatch")
        if parsed.manifest.image_size != len(target_image):
            raise CompactBuildError(f"step {number} manifest target size mismatch")

        package_size = output_path.stat().st_size
        if package_size != plan_step["expected_container_size"]:
            raise CompactBuildError(
                f"step {number} is {package_size} bytes, expected "
                f"{plan_step['expected_container_size']}"
            )
        stage_start = align_down(STAGE_CEILING - package_size, FLASH_PAGE)
        workspace_end = APP_BASE + memory_size
        staging_margin = stage_start - workspace_end
        if stage_start < APP_BASE + len(base_image):
            raise CompactBuildError(f"step {number} staged package overlaps its running image")
        if staging_margin < 0:
            raise CompactBuildError(
                f"step {number} needs {-staging_margin} bytes beyond the legacy ceiling"
            )
        if staging_margin != plan_step["expected_staging_margin"]:
            raise CompactBuildError(
                f"step {number} staging margin is {staging_margin}, expected "
                f"{plan_step['expected_staging_margin']}"
            )
        if memory_size < align_up(max(len(base_image) + 2 * SEGMENT_SIZE, len(target_image)), SEGMENT_SIZE):
            raise CompactBuildError(f"step {number} workspace is below detools' safe minimum")

        zero = apply_in_place(
            args.detools, base_image, parsed.payload, len(target_image), memory_size,
            validation_work, f"compact-step-{number:02d}", 0x00,
        )
        erased = apply_in_place(
            args.detools, base_image, parsed.payload, len(target_image), memory_size,
            validation_work, f"compact-step-{number:02d}", 0xFF,
        )
        if zero != target_image or erased != target_image:
            raise CompactBuildError(f"step {number} reconstruction does not match target")
        simulator_results = []
        for simulator_label, simulator in simulators:
            run(
                [str(simulator), str(base_path), str(output_path), str(target_path)],
                f"{simulator_label} step {number}",
            )
            simulator_results.append({
                "label": simulator_label,
                "sha256": common.sha256_file(simulator),
            })

        output_rows.append({
            "step": number,
            "from_version": from_version,
            "to_version": to_version,
            "mota_file": f"motas/{filename}",
            "mota_size": package_size,
            "inplace_memory": f"0x{memory_size:X}",
            "stage_start": f"0x{stage_start:X}",
            "workspace_end": f"0x{workspace_end:X}",
            "staging_margin": staging_margin,
            "target_image_size": len(target_image),
            "base_body_hash": base_body,
            "target_body_hash": target_body,
            "target_sha256": target_sha,
            "source_commit": sources[target_node],
        })
        validation_steps.append({
            "step": number,
            "source_node": source_node,
            "target_node": target_node,
            "build_kind": build_kind,
            "inplace_memory": memory_size,
            "stage_start": stage_start,
            "workspace_end": workspace_end,
            "staging_margin": staging_margin,
            "mota_sha256": common.sha256_file(output_path),
            "mota_size": package_size,
            "target_sha256": target_sha,
            "zero_fill_apply": "passed",
            "erased_fill_apply": "passed",
            "apply_simulators": simulator_results,
        })
        print(
            f"[compact] {number:02d}/{EXPECTED_STEPS} nodes={source_node}->{target_node} "
            f"memory=0x{memory_size:X} size={package_size} margin={staging_margin}",
            flush=True,
        )

    write_chain(root / "CHAIN.csv", output_rows)
    shutil.copy2(start_zip, recovery_start)
    start_uf2 = old_root / "recovery/test-start/RAK3401-test-start-v1.16.7-c1caa5ad.uf2"
    shutil.copy2(start_uf2, recovery_start)
    shutil.copy2(args.endpoint_zip, recovery_final)
    shutil.copy2(args.endpoint_uf2, recovery_final)
    shutil.copy2(args.accelerated_images, root / "BRIDGE-IMAGES.json")
    shutil.copy2(args.route_plan, root / "ROUTE.json")
    baseline = old_root / "PHYSICAL-BASELINE-cd824765.jsonl"
    if baseline.is_file():
        shutil.copy2(baseline, root)

    route_sha = common.sha256_file(args.route_plan)
    bridge_sha = common.sha256_file(args.accelerated_images)
    write_docs(
        root, args.source_commit, endpoint, previous_sha, bridge_sha,
        route_sha, output_rows,
    )
    validation = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "tools/lora_ota/build_rak3401_compact_bundle.py",
        "source_commit": args.source_commit,
        "endpoint_requested_version": EXPECTED_ENDPOINT_VERSION,
        "endpoint_packed_version": EXPECTED_ENDPOINT_VERSION_PACKED,
        "endpoint_name": "halo-keymind-cascade-dev",
        "previous_bundle_sha256": previous_sha,
        "bridge_images_sha256": bridge_sha,
        "route_plan_sha256": route_sha,
        "route_search": route_document.get("search"),
        "app_base": APP_BASE,
        "stage_ceiling": STAGE_CEILING,
        "segment_size": SEGMENT_SIZE,
        "block_size": BLOCK_SIZE,
        "steps": validation_steps,
    }
    (root / "validation-results.json").write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    checksum_sha = common.write_checksums(root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"{root_name}.zip"
    common.make_zip(root, output_path)
    print(f"[bundle] {output_path}")
    print(f"[bundle] sha256={common.sha256_file(output_path)}")
    print(f"[bundle] inner_sha256={checksum_sha}")
    print(f"[bundle] endpoint_sha256={common.sha256_bytes(endpoint)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CompactBuildError, common.RebuildError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
