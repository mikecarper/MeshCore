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
import struct
import subprocess
import sys
import tempfile
import zipfile


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "mota"))

import motalib  # noqa: E402
import rebuild_rak3401_bundle as common  # noqa: E402
import rak3401_route_search as route_search  # noqa: E402


PREVIOUS_BUNDLE_SHA256 = (
    "b2781e02460b200a7c37bfae352bad81618716e550d1d042dca8aa29bfc73c29"
)
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
ENDPOINT_NODE = 30
APP_BASE = 0x26000
STAGE_CEILING = 0xD4000
FIXED_WORKSPACE = 0x98000
FLASH_PAGE = 4096
SEGMENT_SIZE = 4096
LEGACY_BLOCK_SIZE = 1024
DEFLATE_BLOCK_SIZE = 2048
# Schema-1 and the pinned bootstrap always use the historical geometry.
BLOCK_SIZE = LEGACY_BLOCK_SIZE
UF2_BLOCK_SIZE = 512
UF2_DATA_OFFSET = 32
UF2_DATA_CAPACITY = 476
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_NOT_MAIN_FLASH = 0x00000001
UF2_FLAG_FAMILY_ID = 0x00002000
UF2_NRF52840_FAMILY_ID = 0xADA52840
REQUIRED_SIMULATORS = {
    "otafix2.4": {
        "sha256": "9dacf24b1023fe2f4c620419417649ccc9538dd4a611b47bc19b7fedb97ceba6",
        "tag": "0.9.2-OTAFIX2.4",
        "commit": "d73de8372e89b8ef352747c8bc7a1aaeab80fbfe",
    },
}
MOTATOOL_VERSION = "0.1.0"
MOTATOOL_COMMIT = "abdabec012cb53883a01da53b3e7b604ee0fd070"
TRANSPORT_ROUTE_ENCODER_SHA256 = (
    "53e3ca7e82f2f95b62c65d0858a83f5c54bd238a955b3a01fbe41c08bb4962e8"
)
DETOOLS_VERSION = "0.53.0"
CONTAINER_ROUTE_OBJECTIVE = "minimum packages, then minimum total container bytes"
TRANSPORT_ROUTE_OBJECTIVE = (
    "minimum packages, then minimum ideal linear-path serialized MeshCore bytes"
)
TRANSPORT_STEP_FIELDS = (
    "profile", "block_size", "payload_bytes", "wire_bytes", "deflate_bytes",
    "deflate_blocks", "data_packets", "request_packets",
    "manifest_packets", "request_pipeline", "proof_request_packets",
    "proof_packets", "packets", "manifest_bytes", "data_bytes",
    "block_request_bytes", "proof_request_bytes", "proof_response_bytes",
    "origin_mesh_bytes", "linear_path_bytes",
)
LEGACY_RELEASE_TRANSPORT_BYTES = 1_563_957
LEGACY_RELEASE_TRANSPORT_PACKETS = 13_490
PHYSICAL_VALIDATION_KIND = "rak3401-mota-exact-chain"
MAX_PHYSICAL_VALIDATION_BYTES = 256 * 1024
PHYSICAL_VALIDATION_FIELDS = {
    "schema", "kind", "status", "chain_sha256", "start_sha256",
    "endpoint_sha256", "endpoint_body_hash", "endpoint_version",
    "step_count", "steps", "final_swd",
}

# Capture the source text at module load.  The bundle must archive the code
# that this process loaded, even if another process edits the shared checkout
# while the relatively long patch/simulator pass is running.
EXECUTING_BUILDER_SOURCE = Path(__file__).resolve().read_bytes()
EXECUTING_ROUTE_SEARCH_SOURCE = Path(route_search.__file__).resolve().read_bytes()


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
    if isinstance(value, int) and not isinstance(value, bool):
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
    expected = {label: details["sha256"] for label, details in REQUIRED_SIMULATORS.items()}
    actual = {label: common.sha256_file(path) for label, path in simulators}
    if len(actual) != len(simulators):
        raise CompactBuildError("apply simulator labels must be unique")
    if actual != expected:
        required = " and ".join(
            f"{label}={digest}" for label, digest in expected.items()
        )
        raise CompactBuildError(
            f"legacy RAK3401 builds require exactly {required} apply simulators"
        )
    return simulators


def snapshot_file(source: Path, destination: Path) -> Path:
    """Freeze a regular build input before any package generation starts."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, destination, follow_symlinks=True)
    except OSError as exc:
        raise CompactBuildError(f"cannot snapshot build input {source}: {exc}") from exc
    if not destination.is_file():
        raise CompactBuildError(f"snapshotted build input is not a file: {destination}")
    return destination


def make_reproducible_zip(root: Path, output: Path) -> None:
    """Archive content with stable metadata so identical inputs hash identically."""
    resolved_root = root.resolve(strict=True)
    resolved_output = output.resolve(strict=False)
    if resolved_output.is_relative_to(resolved_root):
        raise CompactBuildError(
            f"archive output must be outside the archived tree: {output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise CompactBuildError(f"output already exists: {output}")
    archived_files = sorted(item for item in root.rglob("*") if item.is_file())
    with zipfile.ZipFile(
        output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in archived_files:
            archive_name = (Path(root.name) / path.relative_to(root)).as_posix()
            info = zipfile.ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def snapshot_schema2_inventory(source: Path, destination: Path) -> Path:
    """Freeze a schema-2 manifest and every firmware image it references."""
    try:
        document = json.loads(source.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompactBuildError(f"cannot read image inventory {source}: {exc}") from exc
    records = document.get("images") if isinstance(document, dict) else None
    if not isinstance(records, list):
        raise CompactBuildError("image inventory must contain an images list")
    image_directory = destination.parent / "inventory-images"
    for node, record in enumerate(records):
        if not isinstance(record, dict):
            raise CompactBuildError(f"image inventory node {node} is invalid")
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise CompactBuildError(f"image inventory node {node} has no path")
        image_source = Path(raw_path)
        if not image_source.is_absolute():
            image_source = source.parent / image_source
        image_name = f"image-{node:02d}.bin"
        snapshot_file(image_source, image_directory / image_name)
        record["path"] = str(Path("inventory-images") / image_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    return destination


def snapshot_schema1_manifest(source: Path, destination: Path) -> Path:
    """Freeze a schema-1 manifest together with its sibling firmware ZIPs."""
    snapshot_file(source, destination)
    try:
        document = json.loads(destination.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompactBuildError(f"cannot read accelerated image manifest: {exc}") from exc
    records = document.get("targets") if isinstance(document, dict) else None
    if not isinstance(records, list):
        raise CompactBuildError("accelerated image manifest must contain a targets list")
    for index, record in enumerate(records, 1):
        filename = record.get("zip") if isinstance(record, dict) else None
        if not isinstance(filename, str):
            raise CompactBuildError(f"accelerated image {index} has no ZIP filename")
        try:
            relative = common.safe_relative(filename, f"accelerated image {index} ZIP")
        except common.RebuildError as exc:
            raise CompactBuildError(str(exc)) from exc
        if len(relative.parts) != 1:
            raise CompactBuildError(
                f"accelerated image {index} ZIP must be a basename"
            )
        snapshot_file(source.parent / relative.name, destination.parent / relative.name)
    return destination


def tool_provenance(motatool: Path, detools: Path) -> dict[str, object]:
    """Validate portable tool versions and record the exact executing launchers."""
    motatool_output = run([str(motatool), "-V"], "read motatool version").strip()
    if motatool_output != f"motatool {MOTATOOL_VERSION}":
        raise CompactBuildError(
            f"motatool version is {motatool_output!r}, expected {MOTATOOL_VERSION}"
        )
    detools_output = run([str(detools), "--version"], "read detools version").strip()
    if detools_output != DETOOLS_VERSION:
        raise CompactBuildError(
            f"detools version is {detools_output!r}, expected {DETOOLS_VERSION}"
        )
    return {
        "motatool": {
            "version": MOTATOOL_VERSION,
            "asserted_source_commit": MOTATOOL_COMMIT,
            "executable_sha256": common.sha256_file(motatool),
        },
        "detools": {
            "version": DETOOLS_VERSION,
            "launcher_sha256": common.sha256_file(detools),
        },
    }


def read_route(path: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    try:
        document = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompactBuildError(f"cannot read route plan {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") not in (1, 2):
        raise CompactBuildError("route plan must use schema 1 or 2")
    schema = int(document["schema"])
    if schema == 2:
        if document.get("status") != "reachable":
            raise CompactBuildError("schema-2 route plan does not reach the endpoint")
        if document.get("search_complete") is not True:
            raise CompactBuildError("schema-2 route plan search is incomplete")
        node_count = parse_int(document.get("node_count"), "route node_count")
        if node_count < 3:
            raise CompactBuildError("route node_count is invalid")
        endpoint_node = node_count - 1
    else:
        node_count = ENDPOINT_NODE + 1
        endpoint_node = ENDPOINT_NODE
    search = document.get("search") if schema == 1 else document
    if not isinstance(search, dict):
        raise CompactBuildError("route plan is missing its exhaustive-search summary")
    objective = search.get("objective")
    allowed_objectives = (
        (CONTAINER_ROUTE_OBJECTIVE,)
        if schema == 1
        else (CONTAINER_ROUTE_OBJECTIVE, TRANSPORT_ROUTE_OBJECTIVE)
    )
    if objective not in allowed_objectives:
        raise CompactBuildError("route plan has the wrong search objective")
    transport_accounting: dict[str, object] | None = None
    transport_encoder_sha: str | None = None
    relay_hops = 0
    if objective == TRANSPORT_ROUTE_OBJECTIVE:
        raw_accounting = search.get("transport_accounting")
        if not isinstance(raw_accounting, dict):
            raise CompactBuildError("route plan transport accounting is missing")
        transport_accounting = dict(raw_accounting)
        relay_hops = parse_int(
            raw_accounting.get("relay_hops"), "route transport relay_hops"
        )
        if relay_hops > route_search.OTA_MAX_HOPS:
            raise CompactBuildError("route transport relay_hops is out of range")
        if (
            raw_accounting.get("source_capability_field")
            != route_search.TRANSPORT_CAPABILITY
            or raw_accounting.get("source_pipeline_field")
            != route_search.TRANSPORT_PIPELINE_FIELD
            or raw_accounting.get("source_max_block_field")
            != route_search.TRANSPORT_MAX_BLOCK_FIELD
            or raw_accounting.get("source_profile_matrix")
            != route_search.TRANSPORT_PROFILE_MATRIX
            or raw_accounting.get("first_bootstrap_profile") != "legacy-160-raw"
            or raw_accounting.get("legacy_block_size") != LEGACY_BLOCK_SIZE
            or raw_accounting.get("supported_block_sizes")
            != [LEGACY_BLOCK_SIZE, DEFLATE_BLOCK_SIZE]
            or raw_accounting.get("comparison_baseline_block_size")
            != LEGACY_BLOCK_SIZE
            or raw_accounting.get("included")
            != (
                "OTA messages, repeated 4-byte v2 stream IDs, 171-byte DATA "
                "slicing, adaptive requests, manifest, exact per-block proofs, "
                "and MeshCore framing"
            )
            or raw_accounting.get("excluded")
            != (
                "discovery, retries, flood fan-out, and radio-dependent LoRa "
                "PHY coding/preamble"
            )
        ):
            raise CompactBuildError("route transport capability contract is invalid")
        encoder_sha = raw_accounting.get("encoder_sha256")
        if encoder_sha is not None and encoder_sha != TRANSPORT_ROUTE_ENCODER_SHA256:
            raise CompactBuildError(
                "route transport encoder does not match the audited release binary"
            )
        transport_encoder_sha = encoder_sha
        transport_accounting = {
            key: raw_accounting[key]
            for key in (
                "relay_hops", "source_capability_field", "source_pipeline_field",
                "source_max_block_field", "source_profile_matrix",
                "first_bootstrap_profile", "legacy_block_size",
                "supported_block_sizes", "comparison_baseline_block_size",
                "included", "excluded",
            )
        }
        if transport_encoder_sha is not None:
            transport_accounting["encoder_sha256"] = transport_encoder_sha
    if parse_int(document.get("app_base"), "route app_base") != APP_BASE:
        raise CompactBuildError("route plan has the wrong application base")
    if parse_int(document.get("stage_ceiling"), "route stage_ceiling") != STAGE_CEILING:
        raise CompactBuildError("route plan does not target the deployed 0xD4000 ceiling")
    expected_steps = document.get("shortest_package_count")
    if (
        not isinstance(expected_steps, int)
        or isinstance(expected_steps, bool)
        or expected_steps < 2
        or expected_steps > endpoint_node
    ):
        raise CompactBuildError("route plan shortest_package_count is invalid")
    raw_steps = document.get("steps")
    if not isinstance(raw_steps, list) or len(raw_steps) != expected_steps:
        raise CompactBuildError(f"route plan must contain {expected_steps} steps")
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
        # Older all-legacy schema-2 plans predate the explicit field. Treat an
        # omission as their historical 1 KiB geometry; new route output always
        # writes the field and transport routes also pin it in accounting.
        raw_block_size = raw.get("block_size", LEGACY_BLOCK_SIZE)
        block_size = parse_int(raw_block_size, f"route step {number} block_size")
        if block_size not in (LEGACY_BLOCK_SIZE, DEFLATE_BLOCK_SIZE):
            raise CompactBuildError(f"route step {number} block_size is unsupported")
        raw_transport = raw.get("transport")
        transport = None
        if objective == TRANSPORT_ROUTE_OBJECTIVE:
            if not isinstance(raw_transport, dict):
                raise CompactBuildError(
                    f"route step {number} is missing transport accounting"
                )
            if raw_transport.get("profile") not in (
                "legacy-160-raw", "v2-171-raw", "v2-171-deflate"
            ):
                raise CompactBuildError(
                    f"route step {number} has an invalid transport profile"
                )
            transport = dict(raw_transport)
            for field in TRANSPORT_STEP_FIELDS[1:]:
                transport[field] = parse_int(
                    raw_transport.get(field),
                    f"route step {number} transport {field}",
                )
            payload_sha = raw_transport.get("payload_sha256")
            encoder_sha = raw_transport.get("encoder_sha256")
            profile = str(transport["profile"])
            compressed_profile = profile == "v2-171-deflate"
            if (
                profile == "legacy-160-raw"
                and block_size != LEGACY_BLOCK_SIZE
            ) or (
                profile == "v2-171-raw"
                and block_size != DEFLATE_BLOCK_SIZE
            ):
                raise CompactBuildError(
                    f"route step {number} block_size disagrees with its "
                    "transport profile"
                )
            if (
                compressed_profile
                and (payload_sha is None or encoder_sha is None)
            ) or (
                not compressed_profile
                and (payload_sha is not None or encoder_sha is not None)
            ):
                raise CompactBuildError(
                    f"route step {number} transport hashes do not match its profile"
                )
            for field, value in (
                ("payload_sha256", payload_sha),
                ("encoder_sha256", encoder_sha),
            ):
                if value is not None:
                    if not isinstance(value, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", value
                    ):
                        raise CompactBuildError(
                            f"route step {number} transport {field} is invalid"
                        )
                    transport[field] = value
            if encoder_sha is not None and encoder_sha != TRANSPORT_ROUTE_ENCODER_SHA256:
                raise CompactBuildError(
                    f"route step {number} transport encoder is not the audited binary"
                )
            try:
                if compressed_profile:
                    calculated = route_search.v2_transport_cost(
                        int(transport["payload_bytes"]),
                        int(transport["wire_bytes"]),
                        int(transport["deflate_bytes"]),
                        int(transport["deflate_blocks"]),
                        int(transport["data_packets"]),
                        int(transport["request_pipeline"]),
                        block_size,
                    )
                elif profile == "v2-171-raw":
                    calculated = route_search.v2_raw_transport_cost(
                        int(transport["payload_bytes"]),
                        int(transport["request_pipeline"]),
                        block_size,
                    )
                else:
                    calculated = route_search.legacy_transport_cost(
                        int(transport["payload_bytes"])
                    )
                calculated["linear_path_bytes"] = route_search.linear_path_bytes(
                    int(calculated["origin_mesh_bytes"]),
                    int(calculated["packets"]),
                    relay_hops,
                )
            except route_search.RouteSearchError as exc:
                raise CompactBuildError(
                    f"route step {number} transport accounting is invalid: {exc}"
                ) from exc
            for field in TRANSPORT_STEP_FIELDS:
                if transport[field] != calculated[field]:
                    raise CompactBuildError(
                        f"route step {number} transport {field} is inconsistent"
                    )
            if route_search.container_size(
                int(transport["payload_bytes"]), block_size
            ) != expected_size:
                raise CompactBuildError(
                    f"route step {number} transport payload does not match its container"
                )
        elif raw_transport is not None:
            raise CompactBuildError(
                f"route step {number} unexpectedly contains transport accounting"
            )
        if source >= target or target >= node_count or memory % FLASH_PAGE:
            raise CompactBuildError(f"route step {number} has invalid nodes or workspace alignment")
        if memory < FLASH_PAGE or APP_BASE + memory >= STAGE_CEILING:
            raise CompactBuildError(f"route step {number} workspace is outside legacy flash")
        if number == 1:
            nodes.append(source)
        elif source != steps[-1]["target_node"]:
            raise CompactBuildError(f"route step {number} is discontinuous")
        nodes.append(target)
        step = {
            "source_node": source,
            "target_node": target,
            "inplace_memory": memory,
            "block_size": block_size,
            "reuse_baseline_package": reuse,
            "expected_container_size": expected_size,
            "expected_staging_margin": expected_margin,
            "expected_target_sha256": expected_sha,
            "expected_target_version": expected_version,
        }
        if transport is not None:
            step["transport"] = transport
        steps.append(step)
    if not nodes or nodes[0] != 0 or nodes[-1] != endpoint_node:
        raise CompactBuildError(
            f"route nodes must run from 0 through {endpoint_node}: {tuple(nodes)}"
        )
    if len(nodes) < 2 or nodes[1] != 1:
        raise CompactBuildError("route must retain the pinned 0->1 physical bridge")
    if not steps[0]["reuse_baseline_package"] or any(
        step["reuse_baseline_package"] for step in steps[1:]
    ):
        raise CompactBuildError("only the first route step may reuse the baseline package")
    if steps[0]["inplace_memory"] != FIXED_WORKSPACE:
        raise CompactBuildError("the pinned first bridge requires a 0x98000 workspace")
    if steps[1]["inplace_memory"] != FIXED_WORKSPACE:
        raise CompactBuildError("the fixed-receiver second step requires a 0x98000 workspace")
    if search.get("shortest_package_count") != expected_steps:
        raise CompactBuildError("route plan search package count is inconsistent")
    if schema == 1:
        if parse_int(search.get("page_size"), "route search page_size") != FLASH_PAGE:
            raise CompactBuildError("route plan search used the wrong page size")
    selected_bytes = sum(int(step["expected_container_size"]) for step in steps)
    if (
        parse_int(
            search.get("selected_total_bytes"),
            "route search selected_total_bytes",
        )
        != selected_bytes
    ):
        raise CompactBuildError("route plan search byte total is inconsistent")
    if objective == TRANSPORT_ROUTE_OBJECTIVE:
        has_compressed_step = any(
            step["transport"]["profile"] == "v2-171-deflate"
            for step in steps
        )
        if has_compressed_step != (transport_encoder_sha is not None):
            raise CompactBuildError(
                "route transport encoder presence does not match its profiles"
            )
        selected_transport_bytes = sum(
            int(step["transport"]["linear_path_bytes"]) for step in steps
        )
        if (
            parse_int(
                search.get("selected_total_transport_bytes"),
                "route search selected_total_transport_bytes",
            )
            != selected_transport_bytes
        ):
            raise CompactBuildError(
                "route plan search transport-byte total is inconsistent"
            )
        assert transport_accounting is not None
    search_aliases = {
        "candidate_geometries": (
            "candidate_geometries", "shorter_path_workspace_candidates"
        ),
        "feasible_edges": ("feasible_edges", "shortest_dag_edges"),
    }
    if schema == 1:
        search_aliases["candidate_pairs"] = ("candidate_pairs", "shorter_path_pairs")
    for field, aliases in search_aliases.items():
        value = next((search.get(alias) for alias in aliases if alias in search), None)
        if parse_int(value, f"route search {field}") < 1:
            raise CompactBuildError(f"route plan search {field} must be positive")
    if parse_int(search.get("shortest_route_count"), "route search shortest_route_count") < 1:
        raise CompactBuildError("route plan search shortest_route_count must be positive")
    document["endpoint_node"] = endpoint_node
    if schema == 2:
        document["search"] = {
            key: search[key]
            for key in (
                "candidate_geometries", "feasible_edges", "objective",
                "selected_total_bytes", "shortest_package_count",
                "shortest_route_count", "search_complete", "node_count",
                "selected_total_transport_bytes", "transport_accounting",
            )
            if key in search
        }
        if transport_accounting is not None:
            document["transport_accounting"] = transport_accounting
            document["search"]["transport_accounting"] = transport_accounting
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


def read_image_inventory(
    path: Path,
    expected_node_count: int,
) -> tuple[dict[int, bytes], dict[int, str]]:
    """Read a schema-2 optimizer inventory without trusting its paths or pins."""
    try:
        document = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompactBuildError(f"cannot read image inventory {path}: {exc}") from exc
    raw_images = document.get("images") if isinstance(document, dict) else None
    if not isinstance(raw_images, list) or len(raw_images) != expected_node_count:
        raise CompactBuildError(
            f"image inventory must contain {expected_node_count} ordered nodes"
        )

    images: dict[int, bytes] = {}
    sources: dict[int, str] = {}
    seen_hashes: set[str] = set()
    for node, raw in enumerate(raw_images):
        if not isinstance(raw, dict) or parse_int(raw.get("node", node), f"inventory node {node}") != node:
            raise CompactBuildError("image inventory nodes must be contiguous and zero-based")
        raw_path = raw.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raw_path = f"image-{node:02d}.bin"
        image_path = Path(raw_path)
        if not image_path.is_absolute():
            image_path = path.parent / image_path
        try:
            image = image_path.read_bytes()
        except OSError as exc:
            raise CompactBuildError(
                f"cannot read inventory node {node} image {image_path}: {exc}"
            ) from exc
        actual_sha = common.sha256_bytes(image)
        expected_sha = raw.get("sha256")
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise CompactBuildError(f"inventory node {node} SHA-256 is invalid")
        if actual_sha != expected_sha:
            raise CompactBuildError(f"inventory node {node} SHA-256 mismatch")
        if actual_sha in seen_hashes:
            raise CompactBuildError(f"inventory node {node} duplicates an earlier image")
        seen_hashes.add(actual_sha)
        if parse_int(raw.get("size"), f"inventory node {node} size") != len(image):
            raise CompactBuildError(f"inventory node {node} size mismatch")
        _packed, actual_body_hash, version, _body_size = firmware_identity(image)
        declared_body_hash = raw.get("body_hash")
        if (
            not isinstance(declared_body_hash, str)
            or not re.fullmatch(r"[0-9a-f]{16}", declared_body_hash)
            or declared_body_hash != actual_body_hash
        ):
            raise CompactBuildError(f"inventory node {node} body hash mismatch")
        expected_version = raw.get("version")
        if not isinstance(expected_version, str) or expected_version != version:
            raise CompactBuildError(f"inventory node {node} version mismatch")
        source_commit = raw.get("source_commit")
        if source_commit is not None and (
            not isinstance(source_commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_commit)
        ):
            raise CompactBuildError(f"inventory node {node} source commit is invalid")
        images[node] = image
        # A complete firmware hash is still stable provenance for an optimizer-created
        # bridge whose source commit was not recorded by the temporary inventory tool.
        sources[node] = source_commit or f"image-sha256:{actual_sha}"
    return images, sources


def write_inventory_provenance(source: Path, destination: Path) -> None:
    """Emit only the schema-2 content pins needed for public provenance."""
    document = json.loads(source.read_text(encoding="ascii"))
    raw_images = document.get("images") if isinstance(document, dict) else None
    if not isinstance(raw_images, list) or not all(
        isinstance(record, dict) for record in raw_images
    ):
        raise CompactBuildError("cannot normalize invalid image inventory")
    allowed = (
        "node", "size", "sha256", "body_hash", "version",
        "source_commit", "baseline_container_size", "version_rank",
        "source_kind", "capability_class", "ota_send_abi",
        "ota_transport_deflate", "ota_fetch_pipeline",
        "transport_max_block_bytes",
        "transport_impl", "transport_fragment_bytes",
        "accepted_transport_codecs", "prohibit_same_version_hop",
        "variant_id", "variant_rank", "variant_count",
    )
    normalized_top = {
        key: document[key]
        for key in (
            "kind", "source_inventory_sha256", "image_count", "start_node",
            "bootstrap_node", "endpoint_node", "app_base", "stage_ceiling",
        )
        if key in document
    }
    capability_contract = document.get("capability_contract")
    if isinstance(capability_contract, dict):
        normalized_top["capability_contract"] = {
            key: capability_contract[key]
            for key in (
                "compatibility_alias", "cost_capability_owner",
                "ota_fetch_pipeline", "ota_transport_deflate",
                "transport_max_block_bytes",
                "request_cost_rule",
            )
            if key in capability_contract
        }
    edge_policy = document.get("edge_policy")
    if isinstance(edge_policy, dict):
        normalized_top["edge_policy"] = {
            key: edge_policy[key]
            for key in (
                "bootstrap_outbound_workspace", "bootstrap_outbound_workspace_hex",
                "capability_owner", "eligibility_expression", "endpoint_must_be_last",
                "pinned_first_edge", "prohibit_same_version_hops", "reason",
                "require_target_version_rank_gt_source_version_rank",
            )
            if key in edge_policy
        }
    document = {
        "schema": 2,
        **normalized_top,
        "images": [
            {key: record[key] for key in allowed if key in record}
            for record in raw_images
        ],
    }
    destination.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def write_accelerated_provenance(source: Path, destination: Path) -> None:
    """Emit only validated schema-1 bridge-image content pins."""
    document = json.loads(source.read_text(encoding="ascii"))
    raw_targets = document.get("targets") if isinstance(document, dict) else None
    if not isinstance(raw_targets, list) or not all(
        isinstance(record, dict) for record in raw_targets
    ):
        raise CompactBuildError("cannot normalize invalid accelerated image manifest")
    allowed = (
        "version", "zip", "zip_sha256", "firmware_sha256", "firmware_size",
        "body_hash", "body_size", "source_commit",
    )
    normalized = {
        "schema": 1,
        "targets": [
            {key: record[key] for key in allowed if key in record}
            for record in raw_targets
        ],
    }
    destination.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def public_route_search(
    schema: int, evidence: dict[str, object]
) -> dict[str, object]:
    """Return only validated route-search metrics safe for public artifacts."""
    raw_search = evidence.get("search")
    search = raw_search if isinstance(raw_search, dict) else evidence
    if schema == 2:
        allowed = (
            "candidate_geometries", "feasible_edges", "objective",
            "selected_total_bytes", "shortest_package_count",
            "shortest_route_count", "search_complete", "node_count",
            "selected_total_transport_bytes", "transport_accounting",
        )
        return {key: search[key] for key in allowed if key in search}
    if schema != 1 or not isinstance(raw_search, dict):
        raise CompactBuildError("schema-1 route has no validated search summary")
    normalized = {
        key: search[key]
        for key in (
            "objective", "shortest_package_count", "selected_total_bytes",
            "page_size", "candidate_pairs", "candidate_geometries",
            "feasible_edges", "shortest_route_count",
        )
        if key in search
    }
    # Older schema-1 plans used aliases. Publish canonical names only.
    aliases = {
        "candidate_pairs": "shorter_path_pairs",
        "candidate_geometries": "shorter_path_workspace_candidates",
        "feasible_edges": "shortest_dag_edges",
    }
    for canonical, legacy in aliases.items():
        if canonical not in normalized:
            normalized[canonical] = search[legacy]
    return normalized


def write_route_provenance(
    destination: Path,
    schema: int,
    steps: list[dict[str, object]],
    evidence: dict[str, object],
    source_commit: str,
    endpoint_version: str,
) -> None:
    """Emit a reusable route plan containing only validated public fields."""
    normalized_steps = []
    for step in steps:
        normalized_step = {
            "source_node": int(step["source_node"]),
            "target_node": int(step["target_node"]),
            "block_size": int(step["block_size"]),
            "inplace_memory": f"0x{int(step['inplace_memory']):X}",
            "reuse_baseline_package": bool(step["reuse_baseline_package"]),
            "expected_container_size": int(step["expected_container_size"]),
            "expected_staging_margin": int(step["expected_staging_margin"]),
            "expected_target_sha256": str(step["expected_target_sha256"]),
            "expected_target_version": str(step["expected_target_version"]),
        }
        raw_transport = step.get("transport")
        if isinstance(raw_transport, dict):
            normalized_step["transport"] = {
                key: raw_transport[key]
                for key in (*TRANSPORT_STEP_FIELDS, "payload_sha256", "encoder_sha256")
                if key in raw_transport
            }
        normalized_steps.append(normalized_step)
    if schema == 2:
        allowed = (
            "schema", "status", "search_complete", "app_base",
            "stage_ceiling", "node_count", "candidate_geometries",
            "feasible_edges", "objective", "nodes",
            "shortest_package_count", "shortest_route_count",
            "selected_total_bytes", "selected_total_transport_bytes",
            "transport_accounting",
        )
        normalized = {key: evidence[key] for key in allowed if key in evidence}
    else:
        normalized = {
            "schema": 1,
            "app_base": f"0x{APP_BASE:X}",
            "stage_ceiling": f"0x{STAGE_CEILING:X}",
            "shortest_package_count": len(normalized_steps),
            "search": public_route_search(schema, evidence),
        }
    normalized.update({
        "endpoint_node": int(steps[-1]["target_node"]),
        "endpoint_version": endpoint_version,
        "endpoint_source_commit": source_commit,
        "endpoint_name": "halo-keymind-cascade-dev",
        "steps": normalized_steps,
    })
    destination.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def validate_geometry_results(
    geometry_path: Path,
    inventory_path: Path,
    route_path: Path,
) -> dict[str, object]:
    """Prove a schema-2 route from a complete, internally consistent geometry set."""
    try:
        inventory = route_search.load_inventory(inventory_path)
    except route_search.RouteSearchError as exc:
        raise CompactBuildError(f"invalid route-search inventory: {exc}") from exc
    jobs = route_search.all_jobs(inventory)
    required = {
        route_search.cache_key(job[5], job[6], job[2]): (job[0], job[1], job[7])
        for job in jobs
    }
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    try:
        source = geometry_path.open(newline="", encoding="ascii")
    except OSError as exc:
        raise CompactBuildError(f"cannot read geometry results {geometry_path}: {exc}") from exc
    with source:
        reader = csv.DictReader(source)
        if reader.fieldnames != route_search.FIELDS:
            raise CompactBuildError("geometry results have unexpected columns")
        for line, raw in enumerate(reader, 2):
            try:
                source_node = parse_int(raw["source"], f"geometry line {line} source")
                target_node = parse_int(raw["target"], f"geometry line {line} target")
                memory = parse_int(raw["memory"], f"geometry line {line} memory")
            except (KeyError, TypeError) as exc:
                raise CompactBuildError(f"geometry line {line} is malformed") from exc
            source_sha_raw = raw.get("source_sha256")
            target_sha_raw = raw.get("target_sha256")
            if (
                not isinstance(source_sha_raw, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha_raw)
                or not isinstance(target_sha_raw, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", target_sha_raw)
            ):
                raise CompactBuildError(f"geometry line {line} has invalid image hashes")
            source_sha = source_sha_raw.lower()
            target_sha = target_sha_raw.lower()
            key = route_search.cache_key(source_sha, target_sha, memory)
            if key in seen:
                raise CompactBuildError(f"duplicate geometry result at line {line}")
            seen.add(key)
            legacy_pinned_source_measurement = (
                source_node == 0
                and 2 <= target_node < len(inventory)
                and memory == FIXED_WORKSPACE
                and source_sha == str(inventory[0]["sha256"])
                and target_sha == str(inventory[target_node]["sha256"])
            )
            if (
                (
                    key not in required
                    or required[key][:2] != (source_node, target_node)
                )
                and not legacy_pinned_source_measurement
            ):
                raise CompactBuildError(f"extra or misindexed geometry result at line {line}")
            try:
                payload = int(raw["payload"])
                block_size = int(raw["block_size"])
                container = int(raw["container"])
                stage_start = int(raw["stage_start"])
                margin = int(raw["margin"])
            except (TypeError, ValueError) as exc:
                raise CompactBuildError(f"geometry line {line} has invalid numeric results") from exc
            feasible_raw = raw.get("feasible")
            if not isinstance(feasible_raw, str):
                raise CompactBuildError(
                    f"geometry line {line} has invalid feasible value"
                )
            feasible_text = feasible_raw.lower()
            if feasible_text not in ("true", "false"):
                raise CompactBuildError(f"geometry line {line} has invalid feasible value")
            feasible = feasible_text == "true"
            error_raw = raw.get("error")
            if not isinstance(error_raw, str):
                raise CompactBuildError(f"geometry line {line} has invalid error value")
            error = error_raw
            if payload < 0:
                raise CompactBuildError(
                    f"geometry line {line} is a failed patch job and cannot prove "
                    "an exhaustive route search"
                )
            else:
                expected_block_size = (
                    LEGACY_BLOCK_SIZE
                    if legacy_pinned_source_measurement
                    else required[key][2]
                )
                if block_size != expected_block_size:
                    raise CompactBuildError(
                        f"geometry line {line} has the wrong block size"
                    )
                expected_container = route_search.container_size(
                    payload, block_size
                )
                expected_stage = route_search.align_down(STAGE_CEILING - expected_container)
                expected_margin = expected_stage - (APP_BASE + memory)
                if (
                    container != expected_container
                    or stage_start != expected_stage
                    or margin != expected_margin
                    or feasible != (expected_margin >= 0)
                    or error
                ):
                    raise CompactBuildError(f"geometry line {line} has inconsistent geometry")
            row = dict(raw)
            row.update(source=source_node, target=target_node, memory=memory,
                       payload=payload, block_size=block_size,
                       container=container, stage_start=stage_start,
                       margin=margin, feasible=feasible, error=error,
                       source_sha256=source_sha, target_sha256=target_sha)
            try:
                row.update(
                    route_search.normalize_transport_stats(
                        row, payload, f"geometry line {line}", block_size
                    )
                )
            except route_search.RouteSearchError as exc:
                raise CompactBuildError(
                    f"geometry line {line} has invalid transport statistics: {exc}"
                ) from exc
            rows.append(row)
    missing = set(required) - seen
    if missing:
        raise CompactBuildError(f"geometry results are missing {len(missing)} required rows")
    baseline_size = parse_int(
        inventory[1].get("baseline_container_size"),
        "inventory node 1 baseline_container_size",
    )
    try:
        declared = json.loads(route_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompactBuildError(f"cannot reread route plan: {exc}") from exc
    declared_objective = declared.get("objective") if isinstance(declared, dict) else None
    if declared_objective == TRANSPORT_ROUTE_OBJECTIVE:
        objective = "transport"
        raw_accounting = declared.get("transport_accounting")
        if not isinstance(raw_accounting, dict):
            raise CompactBuildError("route plan transport accounting is missing")
        relay_hops = parse_int(
            raw_accounting.get("relay_hops"), "route transport relay_hops"
        )
    elif declared_objective == CONTAINER_ROUTE_OBJECTIVE:
        objective = "container"
        relay_hops = 0
    else:
        raise CompactBuildError("route plan has the wrong search objective")
    try:
        with tempfile.TemporaryDirectory() as directory:
            recomputed = route_search.select_route(
                rows, inventory, baseline_size, Path(directory) / "route.json",
                complete=True, objective=objective, relay_hops=relay_hops,
            )
    except route_search.RouteSearchError as exc:
        raise CompactBuildError(f"route recomputation failed: {exc}") from exc
    compared = [
        "schema", "status", "search_complete", "app_base", "stage_ceiling",
        "node_count", "candidate_geometries", "feasible_edges", "objective",
        "nodes", "shortest_package_count", "shortest_route_count",
        "selected_total_bytes", "steps",
    ]
    if objective == "transport":
        compared.extend(("selected_total_transport_bytes", "transport_accounting"))
    for field in compared:
        if declared.get(field) != recomputed.get(field):
            raise CompactBuildError(f"route plan does not match recomputed {field}")
    return recomputed


def validate_uf2_firmware(path: Path, image: bytes, label: str) -> None:
    """Require an app-only nRF52840 UF2 carrying this exact firmware image."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompactBuildError(f"cannot read {label}: {exc}") from exc
    if not raw or len(raw) % UF2_BLOCK_SIZE:
        raise CompactBuildError(f"{label} is not a complete UF2 block stream")

    blocks = len(raw) // UF2_BLOCK_SIZE
    block_numbers: set[int] = set()
    flash: dict[int, int] = {}
    payload_size: int | None = None
    for index in range(blocks):
        block = raw[index * UF2_BLOCK_SIZE:(index + 1) * UF2_BLOCK_SIZE]
        (
            magic0, magic1, flags, address, size, block_number,
            declared_blocks, family_id,
        ) = struct.unpack_from("<IIIIIIII", block)
        magic_end = struct.unpack_from("<I", block, UF2_BLOCK_SIZE - 4)[0]
        if (
            magic0 != UF2_MAGIC_START0
            or magic1 != UF2_MAGIC_START1
            or magic_end != UF2_MAGIC_END
        ):
            raise CompactBuildError(f"{label} block {index} has invalid UF2 magic")
        if flags & UF2_FLAG_NOT_MAIN_FLASH:
            raise CompactBuildError(f"{label} contains a non-flash UF2 block")
        if not flags & UF2_FLAG_FAMILY_ID or family_id != UF2_NRF52840_FAMILY_ID:
            raise CompactBuildError(f"{label} does not identify an nRF52840 image")
        if size < 1 or size > UF2_DATA_CAPACITY:
            raise CompactBuildError(f"{label} block {index} has invalid payload size")
        if payload_size is None:
            payload_size = size
        elif size != payload_size:
            raise CompactBuildError(f"{label} uses inconsistent UF2 payload sizes")
        if declared_blocks != blocks or block_number >= blocks:
            raise CompactBuildError(f"{label} block {index} has inconsistent numbering")
        if block_number in block_numbers:
            raise CompactBuildError(f"{label} repeats UF2 block {block_number}")
        block_numbers.add(block_number)
        for offset, value in enumerate(block[UF2_DATA_OFFSET:UF2_DATA_OFFSET + size]):
            byte_address = address + offset
            if byte_address in flash:
                raise CompactBuildError(f"{label} contains overlapping flash data")
            flash[byte_address] = value

    if block_numbers != set(range(blocks)):
        raise CompactBuildError(f"{label} is missing a numbered UF2 block")
    assert payload_size is not None
    expected_end = align_up(APP_BASE + len(image), payload_size)
    if set(flash) != set(range(APP_BASE, expected_end)):
        raise CompactBuildError(f"{label} is not a contiguous app-only UF2 image")
    encoded = bytes(flash[address] for address in range(APP_BASE, expected_end))
    if encoded[:len(image)] != image:
        raise CompactBuildError(f"{label} firmware does not match the endpoint ZIP")
    if any(value != 0xFF for value in encoded[len(image):]):
        raise CompactBuildError(f"{label} has non-erased data after the endpoint image")


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


def validate_selected_transport(
    motatool: Path,
    payload_path: Path,
    plan_step: dict[str, object],
    inventory: list[dict[str, object]],
    relay_hops: int,
    number: int,
) -> dict[str, object]:
    """Re-measure a selected payload and match every declared radio-cost field."""
    declared = plan_step.get("transport")
    if not isinstance(declared, dict):
        raise CompactBuildError(f"step {number} has no transport accounting")
    source_node = int(plan_step["source_node"])
    uses_v2 = route_search.source_uses_v2(inventory, source_node)
    uses_deflate = route_search.source_uses_deflate(inventory, source_node)
    expected_block_size = route_search.source_block_size(inventory, source_node)
    if int(plan_step.get("block_size", 0)) != expected_block_size:
        raise CompactBuildError(
            f"step {number} logical block size disagrees with source capability"
        )
    expected_profile = route_search.source_transport_profile(
        inventory, source_node
    )
    if declared.get("profile") != expected_profile:
        raise CompactBuildError(
            f"step {number} transport profile disagrees with source capability"
        )
    payload_size = payload_path.stat().st_size
    payload_sha = common.sha256_file(payload_path)
    verification_encoder_sha: str | None = None
    try:
        if uses_deflate:
            measured = route_search.measure_transport_size(
                motatool, payload_path, expected_block_size
            )
            if measured["payload_sha256"] != payload_sha:
                raise CompactBuildError(
                    f"step {number} transport tool measured a different payload"
                )
            verification_encoder_sha = str(measured["transport_encoder_sha256"])
            if verification_encoder_sha != declared.get("encoder_sha256"):
                raise CompactBuildError(
                    f"step {number} transport measurement used a different "
                    "encoder binary"
                )
            calculated = route_search.v2_transport_cost(
                payload_size,
                int(measured["v2_wire_bytes"]),
                int(measured["v2_deflate_bytes"]),
                int(measured["v2_deflate_blocks"]),
                int(measured["v2_data_packets"]),
                route_search.source_transport_pipeline(inventory, source_node),
                expected_block_size,
            )
            if declared.get("payload_sha256") != payload_sha:
                raise CompactBuildError(
                    f"step {number} payload does not match transport route pin"
                )
            if declared.get("encoder_sha256") != TRANSPORT_ROUTE_ENCODER_SHA256:
                raise CompactBuildError(
                    f"step {number} route encoder is not the audited release binary"
                )
        elif uses_v2:
            calculated = route_search.v2_raw_transport_cost(
                payload_size,
                route_search.source_transport_pipeline(inventory, source_node),
                expected_block_size,
            )
            if "payload_sha256" in declared or "encoder_sha256" in declared:
                raise CompactBuildError(
                    f"step {number} raw transport unexpectedly has encoder pins"
                )
        else:
            calculated = route_search.legacy_transport_cost(payload_size)
            if "payload_sha256" in declared or "encoder_sha256" in declared:
                raise CompactBuildError(
                    f"step {number} legacy transport unexpectedly has encoder pins"
                )
        calculated["linear_path_bytes"] = route_search.linear_path_bytes(
            int(calculated["origin_mesh_bytes"]),
            int(calculated["packets"]),
            relay_hops,
        )
        if uses_v2:
            one_kib_raw = route_search.v2_raw_transport_cost(
                payload_size,
                route_search.source_transport_pipeline(inventory, source_node),
                LEGACY_BLOCK_SIZE,
            )
        else:
            one_kib_raw = route_search.legacy_transport_cost(payload_size)
        one_kib_raw["linear_path_bytes"] = route_search.linear_path_bytes(
            int(one_kib_raw["origin_mesh_bytes"]),
            int(one_kib_raw["packets"]),
            relay_hops,
        )
    except route_search.RouteSearchError as exc:
        raise CompactBuildError(
            f"step {number} transport verification failed: {exc}"
        ) from exc
    for field in TRANSPORT_STEP_FIELDS:
        if declared.get(field) != calculated[field]:
            raise CompactBuildError(
                f"step {number} regenerated transport {field} does not match route"
            )
    return {
        "status": "passed",
        **{field: calculated[field] for field in TRANSPORT_STEP_FIELDS},
        "payload_sha256": payload_sha,
        "route_encoder_sha256": (
            TRANSPORT_ROUTE_ENCODER_SHA256 if uses_deflate else None
        ),
        "verification_encoder_sha256": verification_encoder_sha,
        "one_kib_no_compression": {
            field: one_kib_raw[field]
            for field in TRANSPORT_STEP_FIELDS
        },
    }


def write_chain(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "step", "from_version", "to_version", "mota_file", "mota_size",
        "mota_block_size",
        "inplace_memory", "stage_start", "workspace_end", "staging_margin",
        "target_image_size", "base_body_hash", "target_body_hash",
        "target_sha256", "source_commit", "transport_profile",
        "transport_relay_hops", "transport_request_pipeline",
        "transport_payload_sha256", "transport_encoder_sha256",
        "transport_payload_bytes", "transport_wire_bytes",
        "transport_deflate_bytes", "transport_deflate_blocks",
        "transport_data_packets", "transport_request_packets",
        "transport_manifest_packets", "transport_proof_request_packets",
        "transport_proof_packets", "transport_packets",
        "transport_manifest_bytes", "transport_data_bytes",
        "transport_block_request_bytes", "transport_proof_request_bytes",
        "transport_proof_response_bytes",
        "transport_origin_mesh_bytes", "transport_linear_path_bytes",
        "baseline_1k_raw_packets", "baseline_1k_raw_origin_mesh_bytes",
        "baseline_1k_raw_linear_path_bytes",
    ]
    with path.open("w", newline="", encoding="ascii") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_physical_validation_record(path: Path) -> dict[str, object]:
    """Read a structured publisher assertion; arbitrary transcripts are unsafe."""
    try:
        document = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompactBuildError(
            f"cannot read physical validation record {path}: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise CompactBuildError("physical validation record must be a JSON object")
    if set(document) != PHYSICAL_VALIDATION_FIELDS:
        raise CompactBuildError(
            "physical validation record has unexpected or missing fields; "
            f"expected {sorted(PHYSICAL_VALIDATION_FIELDS)}"
        )
    return document


def validate_physical_validation_record(
    document: dict[str, object],
    *,
    chain_sha256: str,
    start_sha256: str,
    endpoint_sha256: str,
    endpoint_body_hash: str,
    endpoint_version: str,
    output_rows: list[dict[str, object]],
    validation_steps: list[dict[str, object]],
) -> dict[str, object]:
    """Bind a publisher's hardware/SWD assertion to every exact package."""
    if set(document) != PHYSICAL_VALIDATION_FIELDS:
        raise CompactBuildError(
            "physical validation record has unexpected or missing fields; "
            f"expected {sorted(PHYSICAL_VALIDATION_FIELDS)}"
        )
    if (
        not isinstance(document.get("schema"), int)
        or isinstance(document.get("schema"), bool)
        or document["schema"] != 1
    ):
        raise CompactBuildError("physical validation record must use schema 1")
    if document.get("kind") != PHYSICAL_VALIDATION_KIND:
        raise CompactBuildError("physical validation record has the wrong kind")
    if document.get("status") != "passed":
        raise CompactBuildError("physical validation record is not passed")

    def require_hash(value: object, expected: str, label: str, length: int = 64) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            rf"[0-9a-fA-F]{{{length}}}", value
        ):
            raise CompactBuildError(f"physical validation {label} is invalid")
        normalized = value.lower()
        if normalized != expected.lower():
            raise CompactBuildError(f"physical validation {label} does not match this chain")
        return normalized

    normalized_chain = require_hash(
        document.get("chain_sha256"), chain_sha256, "CHAIN.csv SHA-256"
    )
    normalized_start = require_hash(
        document.get("start_sha256"), start_sha256, "start image SHA-256"
    )
    normalized_endpoint = require_hash(
        document.get("endpoint_sha256"), endpoint_sha256,
        "endpoint image SHA-256",
    )
    normalized_body = require_hash(
        document.get("endpoint_body_hash"), endpoint_body_hash,
        "endpoint body hash", 16,
    )
    if document.get("endpoint_version") != endpoint_version:
        raise CompactBuildError("physical validation endpoint version does not match")
    if (
        not isinstance(document.get("step_count"), int)
        or isinstance(document.get("step_count"), bool)
        or document["step_count"] != len(validation_steps)
    ):
        raise CompactBuildError("physical validation step count does not match")
    raw_steps = document.get("steps")
    if not isinstance(raw_steps, list) or len(raw_steps) != len(validation_steps):
        raise CompactBuildError("physical validation must contain every chain step")

    normalized_steps = []
    step_fields = {
        "step", "status", "mota_sha256", "target_sha256", "target_body_hash"
    }
    for number, (raw, expected, output_row) in enumerate(
        zip(raw_steps, validation_steps, output_rows), 1
    ):
        if not isinstance(raw, dict) or set(raw) != step_fields:
            raise CompactBuildError(
                f"physical validation step {number} has unexpected or missing fields"
            )
        if (
            not isinstance(raw.get("step"), int)
            or isinstance(raw.get("step"), bool)
            or raw["step"] != number
        ):
            raise CompactBuildError(f"physical validation step {number} is misnumbered")
        if raw.get("status") != "passed":
            raise CompactBuildError(f"physical validation step {number} is not passed")
        normalized_steps.append({
            "step": number,
            "status": "passed",
            "mota_sha256": require_hash(
                raw.get("mota_sha256"), str(expected["mota_sha256"]),
                f"step {number} mOTA SHA-256",
            ),
            "target_sha256": require_hash(
                raw.get("target_sha256"), str(expected["target_sha256"]),
                f"step {number} target SHA-256",
            ),
            "target_body_hash": require_hash(
                raw.get("target_body_hash"), str(output_row["target_body_hash"]),
                f"step {number} target body hash", 16,
            ),
        })

    final_swd = document.get("final_swd")
    if not isinstance(final_swd, dict) or set(final_swd) != {"status", "app_sha256"}:
        raise CompactBuildError("physical validation final_swd record is invalid")
    if final_swd.get("status") != "passed":
        raise CompactBuildError("physical validation final SWD verification is not passed")
    normalized_swd = require_hash(
        final_swd.get("app_sha256"), endpoint_sha256,
        "final SWD application SHA-256",
    )
    return {
        "schema": 1,
        "kind": PHYSICAL_VALIDATION_KIND,
        "status": "passed",
        "chain_sha256": normalized_chain,
        "start_sha256": normalized_start,
        "endpoint_sha256": normalized_endpoint,
        "endpoint_body_hash": normalized_body,
        "endpoint_version": endpoint_version,
        "step_count": len(normalized_steps),
        "steps": normalized_steps,
        "final_swd": {"status": "passed", "app_sha256": normalized_swd},
    }


def write_docs(
    root: Path,
    source_commit: str,
    endpoint_version: str,
    endpoint: bytes,
    previous_sha: str,
    bridge_sha: str,
    route_sha: str,
    geometry_sha: str | None,
    route_search_sha: str | None,
    builder_source_sha: str,
    tools: dict[str, object],
    rows: list[dict[str, object]],
    physically_qualified: bool,
) -> None:
    endpoint_sha = common.sha256_bytes(endpoint)
    _packed, endpoint_body, _version, _body_size = firmware_identity(endpoint)
    total_bytes = sum(int(row["mota_size"]) for row in rows)
    largest = max(int(row["mota_size"]) for row in rows)
    minimum_margin = min(int(row["staging_margin"]) for row in rows)
    expected_steps = len(rows)
    transport_rows = [row for row in rows if row.get("transport_profile")]
    if transport_rows and len(transport_rows) != expected_steps:
        raise CompactBuildError("generated chain has partial transport accounting")
    if transport_rows:
        relay_hop_values = {
            int(row["transport_relay_hops"]) for row in transport_rows
        }
        if len(relay_hop_values) != 1:
            raise CompactBuildError("generated chain mixes transport relay-hop models")
        relay_hops = relay_hop_values.pop()
        model_label = (
            "ideal direct-source"
            if relay_hops == 0
            else f"ideal linear path with {relay_hops} relay hops"
        )
        transport_bytes = sum(
            int(row["transport_linear_path_bytes"]) for row in transport_rows
        )
        transport_packets = sum(
            int(row["transport_packets"]) for row in transport_rows
        )
        payload_bytes = sum(
            int(row["transport_payload_bytes"]) for row in transport_rows
        )
        wire_bytes = sum(
            int(row["transport_wire_bytes"]) for row in transport_rows
        )
        deflate_bytes = sum(
            int(row["transport_deflate_bytes"]) for row in transport_rows
        )
        deflate_blocks = sum(
            int(row["transport_deflate_blocks"]) for row in transport_rows
        )
        data_packets = sum(
            int(row["transport_data_packets"]) for row in transport_rows
        )
        baseline_bytes = sum(
            int(row["baseline_1k_raw_linear_path_bytes"])
            for row in transport_rows
        )
        baseline_packets = sum(
            int(row["baseline_1k_raw_packets"])
            for row in transport_rows
        )
        baseline_byte_savings = baseline_bytes - transport_bytes
        baseline_packet_savings = baseline_packets - transport_packets
        baseline_byte_percent = 100.0 * baseline_byte_savings / baseline_bytes
        baseline_packet_percent = 100.0 * baseline_packet_savings / baseline_packets
        legacy_release_transport_bytes = route_search.linear_path_bytes(
            LEGACY_RELEASE_TRANSPORT_BYTES,
            LEGACY_RELEASE_TRANSPORT_PACKETS,
            relay_hops,
        )
        transport_savings = legacy_release_transport_bytes - transport_bytes
        packet_savings = LEGACY_RELEASE_TRANSPORT_PACKETS - transport_packets
        transport_percent = (
            100.0 * transport_savings / legacy_release_transport_bytes
        )
        packet_percent = 100.0 * packet_savings / LEGACY_RELEASE_TRANSPORT_PACKETS
        deflate_steps = [
            str(row["step"])
            for row in transport_rows
            if row["transport_profile"] == "v2-171-deflate"
        ]
        raw_v2_steps = [
            str(row["step"])
            for row in transport_rows
            if row["transport_profile"] == "v2-171-raw"
        ]
        legacy_steps = [
            str(row["step"])
            for row in transport_rows
            if row["transport_profile"] == "legacy-160-raw"
        ]
        transport_summary = f"""
- Staged container bytes: {total_bytes:,}

| Same-route radio model | Logical block geometry | Origin packets | {model_label.capitalize()} serialized MeshCore bytes |
|---|---:|---:|---:|
| No DEFLATE baseline; packet profiles preserved | 1 KiB all steps | {baseline_packets:,} | {baseline_bytes:,} |
| Selected capability-aware route | Per-source 1/2 KiB signed geometry | {transport_packets:,} | {transport_bytes:,} |

- Selected block streams: {payload_bytes:,} raw payload bytes become
  {wire_bytes:,} radio bytes, including {deflate_bytes:,} DEFLATE bytes across
  {deflate_blocks:,} compressed blocks and {data_packets:,} DATA packets
- Same-route savings: {baseline_packet_savings:,} packets
  ({baseline_packet_percent:.2f}%) and {baseline_byte_savings:,} bytes
  ({baseline_byte_percent:.2f}%)
- The baseline preserves each step's packet profile (171-byte negotiated v2
  or 160-byte legacy); it changes only logical blocks to 1 KiB and disables
  DEFLATE.
- Serialized-byte reduction versus the published b40d2e6c chain:
  {transport_savings:,} ({transport_percent:.2f}%)
- Origin packet reduction versus the published b40d2e6c chain:
  {packet_savings:,} ({packet_percent:.2f}%)
"""
        mode_descriptions = []
        if deflate_steps:
            mode_descriptions.append(
                f"Steps {', '.join(deflate_steps)} use negotiated 171-byte DATA "
                "and independent per-block DEFLATE at each source's declared "
                "signed-container block size."
            )
        if raw_v2_steps:
            mode_descriptions.append(
                f"Steps {', '.join(raw_v2_steps)} use negotiated 171-byte raw "
                "DATA with 2 KiB signed-container blocks; they require no "
                "compression measurement or encoder hash."
            )
        if legacy_steps:
            mode_descriptions.append(
                f"Steps {', '.join(legacy_steps)} use 1 KiB logical blocks and "
                "the legacy 160-byte raw transport."
            )
        transport_description = " ".join(mode_descriptions) + (
            " The protocol remains multi-hop; this report uses the "
            f"{model_label} serialization model, excluding retries and flood "
            "fan-out."
        )
        package_description = (
            f"{expected_steps}; exhaustive route search proved that {expected_steps} "
            "is the minimum for the declared firmware inventory"
        )
    else:
        transport_summary = f"\n- Staged container bytes: {total_bytes:,}\n"
        transport_description = "This legacy route has no audited transport-byte accounting."
        package_description = str(expected_steps)
    physical_text = (
        "The publisher supplied an unsigned operator assertion that the exact "
        "package sequence passed physical/SWD validation; every hash in that "
        "assertion is bound to this archive. It is provenance, not a "
        "cryptographic attestation, so consumers must trust the publisher."
        if physically_qualified else
        "A publisher physical/SWD validation assertion has not yet been attached to this archive."
    )
    live_text = (
        "Live use requires a runner revision that pins this exact archive SHA, checksum list, and step anchors."
        if physically_qualified else
        "Do not use this candidate live until its exact package sequence is physically qualified."
    )
    (root / "README.md").write_text(
        f"""# RAK3401 compact mOTA update chain

This archive contains {expected_steps} mandatory mOTA packages from the deployed
`1.16.7.0-c1caa5ad` image to
`v{endpoint_version}-halo-keymind-cascade-dev-{source_commit[:8]}`.

The deployed bootloader is unchanged. Every flash package is bottom-aligned
below its original `0x{STAGE_CEILING:X}` scan ceiling, and every encoded detools
workspace ends at or below that package. No byte from `0x{STAGE_CEILING:X}`
through `0xED000` is used by this chain.

- Packages: {package_description}
{transport_summary.rstrip()}
- Largest package: {largest:,} bytes
- Smallest workspace-to-stage margin: {minimum_margin:,} bytes
- Endpoint image SHA-256: `{endpoint_sha}`
- Endpoint EndF body hash: `{endpoint_body.upper()}`

Step 1 is byte-for-byte identical to the physically passed baseline package.
The next package leaves the fixed-workspace receiver while still obeying its
old `0x{FIXED_WORKSPACE:X}` limit. Every later package uses the page-aligned
workspace selected in `ROUTE.json`.

{transport_description} When DEFLATE is selected, it exists only on the radio
transfer. The staged `.mota` container and bootloader input remain unchanged
and uncompressed, so the deployed bootloader does not need an inflater.

All {expected_steps} transitions passed container verification, zero-filled and
erased-workspace reconstruction, and validation with the simulator built from
the exact deployed OTAFIX 2.4 source (tag `0.9.2-OTAFIX2.4`, commit
`d73de8372e89b8ef352747c8bc7a1aaeab80fbfe`).
{physical_text} Use the pinned chain runner and do not install packages out of
order.
""",
        encoding="ascii",
    )
    (root / "PROVENANCE.md").write_text(
        f"""# Build and validation provenance

Endpoint source commit: `{source_commit}`
Endpoint requested version: `{endpoint_version}`
Endpoint packed version: `0x{motalib.pack_version(endpoint_version):08X}`
Endpoint profile name: `halo-keymind-cascade-dev`
Legacy stage ceiling: `0x{STAGE_CEILING:X}`
Application base: `0x{APP_BASE:X}`
Segment size: `{SEGMENT_SIZE}`
Logical mOTA block size: independently selected per running source from its
explicit `{route_search.TRANSPORT_MAX_BLOCK_FIELD}` capability (`{LEGACY_BLOCK_SIZE}`
or `{DEFLATE_BLOCK_SIZE}` bytes); DEFLATE permission is a separate capability

Pinned 30-step reconstruction input SHA-256: `{previous_sha}`
Dynamic bridge-image manifest SHA-256: `{bridge_sha}`
Compact route plan SHA-256: `{route_sha}`
Geometry-results SHA-256: `{geometry_sha or "not applicable (schema 1)"}`
Bundled route-selection/verifier source SHA-256:
`{route_search_sha or "not applicable (schema 1)"}`
Executing bundle-builder source SHA-256: `{builder_source_sha}`
Audited route transport encoder SHA-256: `{TRANSPORT_ROUTE_ENCODER_SHA256}`

Tool provenance:

- motatool `{tools['motatool']['version']}`, asserted source commit
  `{tools['motatool']['asserted_source_commit']}`, executable SHA-256
  `{tools['motatool']['executable_sha256']}`
- detools `{tools['detools']['version']}`, launcher SHA-256
  `{tools['detools']['launcher_sha256']}`

Required bootloader simulators:

- Exact deployed OTAFIX 2.4 (`0.9.2-OTAFIX2.4`, commit
  `d73de8372e89b8ef352747c8bc7a1aaeab80fbfe`):
  `9dacf24b1023fe2f4c620419417649ccc9538dd4a611b47bc19b7fedb97ceba6`

For schema 2, the route plan is independently selected again from every row in
the complete, content-pinned `GEOMETRY.csv` table. Transport measurements are
required only for feasible capable-source edges on the shortest-route DAG.
Failed tool jobs and missing relevant transport rows are not admissible
evidence. The secondary objective is exact ideal linear-path serialized
MeshCore bytes, after minimizing package count. It applies only to the declared
firmware inventory and geometry table; it does not claim that no other
conceivable bridge firmware could improve the route.

The bundle generator rebuilds every selected package, matches every v2 payload
SHA-256 and transport counter against `ROUTE.json`, checks the old bootloader
ceiling, reconstructs under both erased and zero-filled workspace assumptions,
then invokes the exact deployed bootloader simulator.
""",
        encoding="ascii",
    )
    (root / "RUNBOOK.md").write_text(
        f"""# Runbook

Use `tools/lora_ota/rak3401_mota_chain.py` from the matching MeshCore commit.
Run `--verify-only`, then `--preflight-only`, before a live attempt. {live_text}
Keep the persistent work directory so an interrupted transfer resumes from the
target's exact EndF body hash.

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
    image_input = parser.add_mutually_exclusive_group(required=True)
    image_input.add_argument(
        "--accelerated-images",
        type=Path,
        help="legacy 28-image bridge manifest used by schema-1 routes",
    )
    image_input.add_argument(
        "--image-inventory",
        type=Path,
        help="ordered variable-node image inventory used by schema-2 routes",
    )
    parser.add_argument("--route-plan", type=Path, required=True)
    parser.add_argument(
        "--geometry-results", type=Path,
        help="complete geometry CSV required by schema-2 routes",
    )
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
    parser.add_argument(
        "--physical-validation-record", "--physical-validation-log",
        dest="physical_validation_record",
        type=Path,
        help=(
            "unsigned schema-1 publisher assertion binding every passed "
            "package and the final SWD application hash to this exact chain"
        ),
    )
    args = parser.parse_args()

    for path, label in (
        (args.previous_bundle, "previous bundle"),
        (args.endpoint_zip, "endpoint ZIP"),
        (args.endpoint_uf2, "endpoint UF2"),
        (args.route_plan, "route plan"),
        (args.motatool, "motatool"),
        (args.detools, "detools"),
    ):
        if not path.is_file():
            raise CompactBuildError(f"{label} does not exist: {path}")
    original_image_manifest = args.image_inventory or args.accelerated_images
    assert original_image_manifest is not None
    if not original_image_manifest.is_file():
        raise CompactBuildError(
            f"image manifest does not exist: {original_image_manifest}"
        )
    if args.geometry_results is not None and not args.geometry_results.is_file():
        raise CompactBuildError(
            f"geometry results do not exist: {args.geometry_results}"
        )
    if args.physical_validation_record is not None and \
            not args.physical_validation_record.is_file():
        raise CompactBuildError(
            "physical validation record does not exist: "
            f"{args.physical_validation_record}"
        )
    if (
        args.physical_validation_record is not None
        and args.physical_validation_record.stat().st_size
        > MAX_PHYSICAL_VALIDATION_BYTES
    ):
        raise CompactBuildError("physical validation record exceeds 256 KiB")
    original_simulators = parse_simulators(args.apply_sim)
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
    source_epoch_text = run(
        [
            "git", "-C", str(REPO_ROOT), "show", "-s", "--format=%ct",
            args.source_commit,
        ],
        "read endpoint source timestamp",
    ).strip()
    try:
        source_epoch = int(source_epoch_text)
        generated_at = datetime.fromtimestamp(source_epoch, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise CompactBuildError(
            f"endpoint source timestamp is invalid: {source_epoch_text!r}"
        ) from exc
    if args.work_dir.exists():
        raise CompactBuildError(f"work directory already exists: {args.work_dir}")
    args.work_dir.mkdir(parents=True)

    # All subsequent file reads and launcher invocations use stable snapshots.
    # A Python launcher can still reference its pinned pipx environment, whose
    # version and launcher hash are recorded below rather than called hermetic.
    # This checkout is shared by multiple agents, and a compact-chain build is
    # long enough that copying provenance only at the end would be racy.
    frozen = args.work_dir / "frozen-inputs"
    args.previous_bundle = snapshot_file(
        args.previous_bundle, frozen / "previous-bundle.zip"
    )
    args.endpoint_zip = snapshot_file(args.endpoint_zip, frozen / "endpoint.zip")
    args.endpoint_uf2 = snapshot_file(args.endpoint_uf2, frozen / "endpoint.uf2")
    args.route_plan = snapshot_file(args.route_plan, frozen / "route.json")
    args.motatool = snapshot_file(args.motatool, frozen / "tools" / "motatool")
    args.detools = snapshot_file(args.detools, frozen / "tools" / "detools")
    if args.geometry_results is not None:
        args.geometry_results = snapshot_file(
            args.geometry_results, frozen / "geometry.csv"
        )
    physical_validation_document = None
    if args.physical_validation_record is not None:
        args.physical_validation_record = snapshot_file(
            args.physical_validation_record, frozen / "physical-validation.json"
        )
        physical_validation_document = read_physical_validation_record(
            args.physical_validation_record
        )
    frozen_simulators = [
        (
            label,
            snapshot_file(path, frozen / "simulators" / label),
        )
        for label, path in original_simulators
    ]
    simulators = parse_simulators(
        [f"{label}={path}" for label, path in frozen_simulators]
    )
    tools = tool_provenance(args.motatool, args.detools)

    route, route_document = read_route(args.route_plan)
    route_evidence = route_document
    route_schema = int(route_document["schema"])
    route_search_summary = route_document.get("search")
    transport_route = (
        route_schema == 2
        and isinstance(route_search_summary, dict)
        and route_search_summary.get("objective") == TRANSPORT_ROUTE_OBJECTIVE
    )
    transport_relay_hops = 0
    if transport_route:
        transport_accounting = route_search_summary.get("transport_accounting")
        assert isinstance(transport_accounting, dict)
        transport_relay_hops = int(transport_accounting["relay_hops"])
        if (
            tools["motatool"]["executable_sha256"]
            != TRANSPORT_ROUTE_ENCODER_SHA256
        ):
            raise CompactBuildError(
                "motatool executable does not match the audited route encoder"
            )
    if route_schema == 1 and args.accelerated_images is None:
        raise CompactBuildError("schema-1 routes require --accelerated-images")
    if route_schema == 2 and args.image_inventory is None:
        raise CompactBuildError("schema-2 routes require --image-inventory")
    if route_schema == 2 and args.geometry_results is None:
        raise CompactBuildError("schema-2 routes require --geometry-results")
    if route_schema == 1 and args.geometry_results is not None:
        raise CompactBuildError("--geometry-results is only valid for schema-2 routes")
    route_inventory: list[dict[str, object]] | None = None
    if route_schema == 1:
        assert args.accelerated_images is not None
        args.accelerated_images = snapshot_schema1_manifest(
            original_image_manifest,
            frozen / "accelerated" / original_image_manifest.name,
        )
        image_manifest = args.accelerated_images
    else:
        assert args.image_inventory is not None
        args.image_inventory = snapshot_schema2_inventory(
            original_image_manifest,
            frozen / "inventory" / "images.json",
        )
        image_manifest = args.image_inventory
    endpoint_node = int(route_document["endpoint_node"])
    endpoint_version = route_document.get(
        "endpoint_version", route[-1]["expected_target_version"]
    )
    if not isinstance(endpoint_version, str):
        raise CompactBuildError("route plan endpoint version is invalid")
    try:
        endpoint_version_packed = motalib.pack_version(endpoint_version)
    except (TypeError, ValueError):
        raise CompactBuildError("route plan endpoint version is invalid") from None
    route_source_commit = route_document.get("endpoint_source_commit", args.source_commit)
    route_endpoint_name = route_document.get("endpoint_name", "halo-keymind-cascade-dev")
    if route_source_commit != args.source_commit or \
            route_endpoint_name != "halo-keymind-cascade-dev":
        raise CompactBuildError("route plan endpoint identity does not match this build")
    expected_steps = len(route)
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

    endpoint = common.read_firmware_zip(args.endpoint_zip)
    endpoint_packed, _body_hash, _version_text, _body_size = firmware_identity(endpoint)
    validate_uf2_firmware(args.endpoint_uf2, endpoint, "endpoint UF2")
    if endpoint_packed != endpoint_version_packed:
        raise CompactBuildError(
            f"endpoint packed version is 0x{endpoint_packed:08X}, "
            f"expected 0x{endpoint_version_packed:08X}"
        )
    expected_label = f"v{endpoint_version}-halo-keymind-cascade-dev-{args.source_commit[:8]}"
    if expected_label.encode("ascii") not in endpoint:
        raise CompactBuildError(f"endpoint firmware does not contain {expected_label}")

    if route_schema == 1:
        assert args.accelerated_images is not None
        accelerated, accelerated_sources = common.read_accelerated_images(
            args.accelerated_images
        )
        images = {0: image0, 1: image1}
        sources = {1: old_rows[0]["source_commit"]}
        for node, (image, source_commit) in enumerate(
            zip(accelerated, accelerated_sources), 2
        ):
            images[node] = image
            sources[node] = source_commit
        images[endpoint_node] = endpoint
        sources[endpoint_node] = args.source_commit
    else:
        assert args.image_inventory is not None
        assert args.geometry_results is not None
        try:
            route_inventory = route_search.load_inventory(args.image_inventory)
        except route_search.RouteSearchError as exc:
            raise CompactBuildError(
                f"invalid route-search inventory: {exc}"
            ) from exc
        images, sources = read_image_inventory(
            args.image_inventory, endpoint_node + 1
        )
        route_evidence = validate_geometry_results(
            args.geometry_results, args.image_inventory, args.route_plan
        )
        if images[0] != image0:
            raise CompactBuildError("inventory node 0 does not match the pinned start image")
        if images[1] != image1:
            raise CompactBuildError("inventory node 1 does not match the pinned first bridge")
        if images[endpoint_node] != endpoint:
            raise CompactBuildError("inventory endpoint does not match the endpoint ZIP")
        sources[1] = old_rows[0]["source_commit"]
        sources[endpoint_node] = args.source_commit
    expected_nodes = set(range(endpoint_node + 1))
    if set(images) != expected_nodes or not (expected_nodes - {0}).issubset(sources):
        raise CompactBuildError(f"assembled image nodes are {sorted(images)}")

    short_commit = args.source_commit[:8]
    root_name = (
        "RAK3401-update-chain-v1.16.7-c1caa5ad-to-"
        f"v{endpoint_version}-{short_commit}"
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
        block_size = int(plan_step["block_size"])
        base_image = images[source_node]
        target_image = images[target_node]
        base_packed, base_body, base_version, _base_body_size = firmware_identity(base_image)
        target_packed, target_body, target_version, _target_body_size = firmware_identity(target_image)
        from_version = base_version
        to_version = endpoint_version if target_node == endpoint_node else target_version
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
                    "--block-size", str(block_size), "--out", str(output_path),
                ],
                f"build compact step {number}",
            )
            if not build_output.strip():
                raise CompactBuildError(f"motatool produced no output for step {number}")
            build_kind = "freshly generated"
        run([str(args.motatool), "verify", str(output_path)], f"verify compact step {number}")
        parsed = motalib.parse_container(output_path.read_bytes())
        if parsed.manifest.block_size != block_size:
            raise CompactBuildError(
                f"step {number} manifest block size is "
                f"{parsed.manifest.block_size}, expected {block_size}"
            )
        payload_path.write_bytes(parsed.payload)
        transport_validation = None
        if transport_route:
            assert route_inventory is not None
            transport_validation = validate_selected_transport(
                args.motatool, payload_path, plan_step, route_inventory,
                transport_relay_hops, number,
            )
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

        output_row = {
            "step": number,
            "from_version": from_version,
            "to_version": to_version,
            "mota_file": f"motas/{filename}",
            "mota_size": package_size,
            "mota_block_size": block_size,
            "inplace_memory": f"0x{memory_size:X}",
            "stage_start": f"0x{stage_start:X}",
            "workspace_end": f"0x{workspace_end:X}",
            "staging_margin": staging_margin,
            "target_image_size": len(target_image),
            "base_body_hash": base_body,
            "target_body_hash": target_body,
            "target_sha256": target_sha,
            "source_commit": sources[target_node],
            "transport_profile": "",
            "transport_relay_hops": "",
            "transport_request_pipeline": "",
            "transport_payload_sha256": "",
            "transport_encoder_sha256": "",
            "transport_payload_bytes": "",
            "transport_wire_bytes": "",
            "transport_deflate_bytes": "",
            "transport_deflate_blocks": "",
            "transport_data_packets": "",
            "transport_request_packets": "",
            "transport_manifest_packets": "",
            "transport_proof_request_packets": "",
            "transport_proof_packets": "",
            "transport_packets": "",
            "transport_manifest_bytes": "",
            "transport_data_bytes": "",
            "transport_block_request_bytes": "",
            "transport_proof_request_bytes": "",
            "transport_proof_response_bytes": "",
            "transport_origin_mesh_bytes": "",
            "transport_linear_path_bytes": "",
            "baseline_1k_raw_packets": "",
            "baseline_1k_raw_origin_mesh_bytes": "",
            "baseline_1k_raw_linear_path_bytes": "",
        }
        if transport_validation is not None:
            output_row.update({
                "transport_profile": transport_validation["profile"],
                "transport_relay_hops": transport_relay_hops,
                "transport_request_pipeline": transport_validation[
                    "request_pipeline"
                ],
                "transport_payload_sha256": transport_validation["payload_sha256"],
                "transport_encoder_sha256": (
                    transport_validation["route_encoder_sha256"] or ""
                ),
                "transport_payload_bytes": transport_validation["payload_bytes"],
                "transport_wire_bytes": transport_validation["wire_bytes"],
                "transport_deflate_bytes": transport_validation["deflate_bytes"],
                "transport_deflate_blocks": transport_validation["deflate_blocks"],
                "transport_data_packets": transport_validation["data_packets"],
                "transport_request_packets": transport_validation["request_packets"],
                "transport_manifest_packets": transport_validation["manifest_packets"],
                "transport_proof_request_packets": transport_validation[
                    "proof_request_packets"
                ],
                "transport_proof_packets": transport_validation["proof_packets"],
                "transport_packets": transport_validation["packets"],
                "transport_manifest_bytes": transport_validation["manifest_bytes"],
                "transport_data_bytes": transport_validation["data_bytes"],
                "transport_block_request_bytes": transport_validation[
                    "block_request_bytes"
                ],
                "transport_proof_request_bytes": transport_validation[
                    "proof_request_bytes"
                ],
                "transport_proof_response_bytes": transport_validation[
                    "proof_response_bytes"
                ],
                "transport_origin_mesh_bytes": transport_validation["origin_mesh_bytes"],
                "transport_linear_path_bytes": transport_validation["linear_path_bytes"],
                "baseline_1k_raw_packets": transport_validation[
                    "one_kib_no_compression"
                ]["packets"],
                "baseline_1k_raw_origin_mesh_bytes": transport_validation[
                    "one_kib_no_compression"
                ]["origin_mesh_bytes"],
                "baseline_1k_raw_linear_path_bytes": transport_validation[
                    "one_kib_no_compression"
                ]["linear_path_bytes"],
            })
        output_rows.append(output_row)
        validation_step = {
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
            "block_size": block_size,
            "target_sha256": target_sha,
            "zero_fill_apply": "passed",
            "erased_fill_apply": "passed",
            "apply_simulators": simulator_results,
        }
        if transport_validation is not None:
            validation_step["transport"] = transport_validation
        validation_steps.append(validation_step)
        transport_text = (
            f" radio={transport_validation['profile']} "
            f"bytes={transport_validation['linear_path_bytes']}"
            if transport_validation is not None else ""
        )
        print(
            f"[compact] {number:02d}/{expected_steps} nodes={source_node}->{target_node} "
            f"memory=0x{memory_size:X} size={package_size} margin={staging_margin}"
            f"{transport_text}",
            flush=True,
        )

    chain_path = root / "CHAIN.csv"
    write_chain(chain_path, output_rows)
    chain_sha = common.sha256_file(chain_path)
    physical_validation = None
    physical_validation_path = root / "PHYSICAL-VALIDATION.json"
    if physical_validation_document is not None:
        _endpoint_packed, endpoint_body_hash, _endpoint_text, _endpoint_body_size = (
            firmware_identity(endpoint)
        )
        physical_validation = validate_physical_validation_record(
            physical_validation_document,
            chain_sha256=chain_sha,
            start_sha256=common.sha256_bytes(image0),
            endpoint_sha256=common.sha256_bytes(endpoint),
            endpoint_body_hash=endpoint_body_hash,
            endpoint_version=endpoint_version,
            output_rows=output_rows,
            validation_steps=validation_steps,
        )
        physical_validation_path.write_text(
            json.dumps(physical_validation, indent=2, sort_keys=True) + "\n",
            encoding="ascii",
        )
    shutil.copy2(start_zip, recovery_start)
    start_uf2 = old_root / "recovery/test-start/RAK3401-test-start-v1.16.7-c1caa5ad.uf2"
    shutil.copy2(start_uf2, recovery_start)
    shutil.copy2(args.endpoint_zip, recovery_final)
    shutil.copy2(args.endpoint_uf2, recovery_final)
    manifest_name = (
        "BRIDGE-IMAGES.json" if route_schema == 1 else "IMAGE-INVENTORY.json"
    )
    bundled_manifest = root / manifest_name
    if route_schema == 1:
        write_accelerated_provenance(image_manifest, bundled_manifest)
    else:
        write_inventory_provenance(image_manifest, bundled_manifest)
    bundled_route = root / "ROUTE.json"
    write_route_provenance(
        bundled_route, route_schema, route, route_evidence,
        args.source_commit, endpoint_version,
    )
    builder_source_copy = root / "build_rak3401_compact_bundle.py"
    builder_source_copy.write_bytes(EXECUTING_BUILDER_SOURCE)
    builder_source_sha = common.sha256_file(builder_source_copy)
    geometry_sha = None
    route_search_sha = None
    if route_schema == 2:
        assert args.geometry_results is not None
        geometry_copy = root / "GEOMETRY.csv"
        route_search_copy = root / "rak3401_route_search.py"
        shutil.copy2(args.geometry_results, geometry_copy)
        route_search_copy.write_bytes(EXECUTING_ROUTE_SEARCH_SOURCE)
        geometry_sha = common.sha256_file(geometry_copy)
        route_search_sha = common.sha256_file(route_search_copy)
    baseline = old_root / "PHYSICAL-BASELINE-cd824765.jsonl"
    if baseline.is_file():
        shutil.copy2(baseline, root)
    route_sha = common.sha256_file(bundled_route)
    bridge_sha = common.sha256_file(bundled_manifest)
    write_docs(
        root, args.source_commit, endpoint_version, endpoint, previous_sha,
        bridge_sha, route_sha, geometry_sha, route_search_sha,
        builder_source_sha, tools, output_rows,
        physical_validation is not None,
    )
    validation = {
        "generated_at": generated_at,
        "generator": "tools/lora_ota/build_rak3401_compact_bundle.py",
        "source_commit": args.source_commit,
        "endpoint_requested_version": endpoint_version,
        "endpoint_packed_version": endpoint_version_packed,
        "endpoint_name": "halo-keymind-cascade-dev",
        "previous_bundle_sha256": previous_sha,
        "bridge_images_sha256": bridge_sha,
        "route_plan_sha256": route_sha,
        "route_search": public_route_search(route_schema, route_evidence),
        "required_simulator_provenance": REQUIRED_SIMULATORS,
        "bundle_builder_source_sha256": builder_source_sha,
        "tool_provenance": tools,
        "app_base": APP_BASE,
        "stage_ceiling": STAGE_CEILING,
        "segment_size": SEGMENT_SIZE,
        "block_size_policy": {
            "source_max_block_field": "transport_max_block_bytes",
            "supported_block_sizes": [LEGACY_BLOCK_SIZE, DEFLATE_BLOCK_SIZE],
            "source_deflate_field": "ota_transport_deflate",
            "source_profile_matrix": dict(route_search.TRANSPORT_PROFILE_MATRIX),
        },
        "steps": validation_steps,
    }
    if route_schema == 2:
        validation["geometry_results_sha256"] = geometry_sha
        validation["route_search_source_sha256"] = route_search_sha
        validation["image_inventory_sha256"] = bridge_sha
    if transport_route:
        transport_validations = [
            step["transport"] for step in validation_steps
            if isinstance(step.get("transport"), dict)
        ]
        if len(transport_validations) != expected_steps:
            raise CompactBuildError("not every selected step has transport validation")
        selected_container_bytes = sum(
            int(row["mota_size"]) for row in output_rows
        )
        selected_transport_bytes = sum(
            int(item["linear_path_bytes"]) for item in transport_validations
        )
        baseline_transport_bytes = sum(
            int(item["one_kib_no_compression"]["linear_path_bytes"])
            for item in transport_validations
        )
        route_encoder_hashes = {
            str(item["route_encoder_sha256"])
            for item in transport_validations
            if item.get("route_encoder_sha256") is not None
        }
        if len(route_encoder_hashes) > 1:
            raise CompactBuildError("selected steps mix route transport encoders")
        selected_route_encoder = next(iter(route_encoder_hashes), None)
        selected_packets = sum(
            int(item["packets"]) for item in transport_validations
        )
        baseline_packets = sum(
            int(item["one_kib_no_compression"]["packets"])
            for item in transport_validations
        )
        component_totals = {
            field: sum(int(item[field]) for item in transport_validations)
            for field in (
                "manifest_packets", "data_packets", "request_packets",
                "proof_request_packets", "proof_packets", "manifest_bytes",
                "data_bytes", "block_request_bytes", "proof_request_bytes",
                "proof_response_bytes",
            )
        }
        if selected_packets != sum(
            component_totals[field]
            for field in (
                "manifest_packets", "data_packets", "request_packets",
                "proof_request_packets", "proof_packets",
            )
        ) or sum(
            component_totals[field]
            for field in (
                "manifest_bytes", "data_bytes", "block_request_bytes",
                "proof_request_bytes", "proof_response_bytes",
            )
        ) != sum(
            int(item["origin_mesh_bytes"]) for item in transport_validations
        ):
            raise CompactBuildError("transport component totals are inconsistent")
        if (
            selected_container_bytes != route_evidence.get("selected_total_bytes")
            or selected_transport_bytes
            != route_evidence.get("selected_total_transport_bytes")
        ):
            raise CompactBuildError("selected transport totals do not match route evidence")
        validation["transport_validation"] = {
            "status": "passed",
            "relay_hops": transport_relay_hops,
            "route_encoder_sha256": selected_route_encoder,
            "selected_total_container_bytes": selected_container_bytes,
            "selected_total_transport_bytes": selected_transport_bytes,
            "selected_total_packets": selected_packets,
            "selected_total_payload_bytes": sum(
                int(item["payload_bytes"]) for item in transport_validations
            ),
            "selected_total_wire_bytes": sum(
                int(item["wire_bytes"]) for item in transport_validations
            ),
            "selected_total_deflate_bytes": sum(
                int(item["deflate_bytes"]) for item in transport_validations
            ),
            "selected_total_deflate_blocks": sum(
                int(item["deflate_blocks"]) for item in transport_validations
            ),
            "component_totals": component_totals,
            "one_kib_no_compression_baseline": {
                "description": (
                    "same selected route with 1 KiB logical blocks and no "
                    "DEFLATE, preserving each step's negotiated 171-byte or "
                    "legacy 160-byte packet profile"
                ),
                "block_size": LEGACY_BLOCK_SIZE,
                "total_payload_bytes": sum(
                    int(item["payload_bytes"]) for item in transport_validations
                ),
                "total_wire_bytes": sum(
                    int(item["one_kib_no_compression"]["wire_bytes"])
                    for item in transport_validations
                ),
                "total_transport_bytes": baseline_transport_bytes,
                "total_packets": baseline_packets,
                "saved_transport_bytes": (
                    baseline_transport_bytes - selected_transport_bytes
                ),
                "saved_packets": baseline_packets - selected_packets,
                "saved_transport_percent": round(
                    100.0
                    * (baseline_transport_bytes - selected_transport_bytes)
                    / baseline_transport_bytes,
                    6,
                ),
                "saved_packet_percent": round(
                    100.0 * (baseline_packets - selected_packets)
                    / baseline_packets,
                    6,
                ),
            },
        }
    if physical_validation is not None:
        validation["physical_validation_record_sha256"] = common.sha256_file(
            physical_validation_path
        )
    (root / "validation-results.json").write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    checksum_sha = common.write_checksums(root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"{root_name}.zip"
    make_reproducible_zip(root, output_path)
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
