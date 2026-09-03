#!/usr/bin/env python3
"""Exhaustively select a compact legacy-ceiling RAK3401 mOTA route.

Cache entries are keyed by source SHA, target SHA, and workspace size rather
than inventory indices.  A cache generated for an older inventory can therefore
be migrated safely when new candidate images are inserted.

The default secondary objective remains container size. ``--objective transport``
instead measures the exact live motatool encoder and minimizes clean serialized
MeshCore bytes. Inventory fields ``ota_transport_deflate``,
``ota_fetch_pipeline``, and ``transport_max_block_bytes`` belong to the
currently running/source image; the pinned first bootstrap is always legacy
raw. The explicit maximum controls signed-container block geometry independently
of whether DEFLATE is allowed; either DEFLATE or 2 KiB geometry negotiates the
171-byte v2 wire profile.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "mota"))

import motalib  # noqa: E402

APP_BASE = 0x26000
STAGE_CEILING = 0xD4000
PAGE = 4096
SEGMENT = PAGE
FIXED_MEMORY = 0x98000
AVAILABLE_PAGES = (STAGE_CEILING - APP_BASE) // PAGE
EXPECTED_TARGET_ID = 0x2FA509C1
EXPECTED_HARDWARE = "RAK_3401"
TRANSPORT_CAPABILITY = "ota_transport_deflate"
TRANSPORT_PIPELINE_FIELD = "ota_fetch_pipeline"
TRANSPORT_MAX_BLOCK_FIELD = "transport_max_block_bytes"
TRANSPORT_CAPABILITY_ALIAS = "transport_inflate"
TRANSPORT_PIPELINE_ALIAS = "fetch_pipeline"
LEGACY_BLOCK_SIZE = 1024
DEFLATE_BLOCK_SIZE = 2048
TRANSPORT_PROFILE_MATRIX = {
    "1024,deflate=false": "legacy-160-raw",
    "1024,deflate=true": "v2-171-deflate",
    "2048,deflate=false": "v2-171-raw",
    "2048,deflate=true": "v2-171-deflate",
}
# Retain the historical name for callers that are specifically dealing with
# the immutable first package. New route geometry must choose a block size from
# the currently running/source image via ``source_block_size``.
BLOCK_SIZE = LEGACY_BLOCK_SIZE
LEGACY_DATA_BYTES = 160
V2_DATA_BYTES = 171
MANIFEST_BYTES = 197
MANIFEST_FRAGMENT_BYTES = 176
RAK3401_PIPELINE_MAX = 4
OTA_MAX_HOPS = 8
TRANSPORT_FIELDS = [
    "payload_sha256", "transport_encoder_sha256", "v2_wire_bytes",
    "v2_deflate_bytes", "v2_deflate_blocks", "v2_data_packets",
]
FIELDS = [
    "source", "target", "source_sha256", "target_sha256", "memory",
    "payload", "block_size", "container", "stage_start", "margin", "feasible", "error",
] + TRANSPORT_FIELDS


class RouteSearchError(RuntimeError):
    """An invalid input or internally inconsistent cache."""


def align_up(value: int, unit: int = PAGE) -> int:
    return (value + unit - 1) // unit * unit


def align_down(value: int, unit: int = PAGE) -> int:
    return value // unit * unit


def _validate_block_size(block_size: int) -> int:
    if block_size not in (LEGACY_BLOCK_SIZE, DEFLATE_BLOCK_SIZE):
        raise RouteSearchError(
            f"logical block size must be {LEGACY_BLOCK_SIZE} or {DEFLATE_BLOCK_SIZE}"
        )
    return block_size


def container_size(
    payload_size: int, block_size: int = LEGACY_BLOCK_SIZE
) -> int:
    block_size = _validate_block_size(block_size)
    if payload_size <= 0:
        raise RouteSearchError("container payload must be positive")
    return payload_size + 210 + 4 * (
        (payload_size + block_size - 1) // block_size
    )


def payload_size_from_container(
    total: int, block_size: int = LEGACY_BLOCK_SIZE
) -> int:
    """Invert the fixed mOTA geometry used by the pinned bootstrap package."""
    block_size = _validate_block_size(block_size)
    if total <= 210:
        raise RouteSearchError("container is too small to contain a payload")
    max_blocks = (total - 210 + block_size - 1) // block_size
    for blocks in range(1, max_blocks + 1):
        payload = total - 210 - 4 * blocks
        if payload > 0 and (payload + block_size - 1) // block_size == blocks:
            return payload
    raise RouteSearchError(f"container size {total} has no valid payload geometry")


def _block_lengths(payload_size: int, block_size: int) -> list[int]:
    if payload_size <= 0:
        raise RouteSearchError("transport payload must be positive")
    block_size = _validate_block_size(block_size)
    full, tail = divmod(payload_size, block_size)
    result = [block_size] * full
    if tail:
        result.append(tail)
    return result


def proof_sibling_count(block_count: int, block_index: int) -> int:
    """Match the promote-odd Merkle proof emitted by MeshCore."""
    if block_count <= 0 or not 0 <= block_index < block_count:
        raise RouteSearchError("invalid block for Merkle proof sizing")
    siblings = 0
    count = block_count
    index = block_index
    while count > 1:
        if not (count % 2 == 1 and index == count - 1):
            siblings += 1
        index //= 2
        count = (count + 1) // 2
    return siblings


def _common_transport_cost(block_count: int) -> tuple[int, int, int]:
    """Return origin-serialized Mesh bytes, packets, and proof-response bytes."""
    manifest_fragments = (
        MANIFEST_BYTES + MANIFEST_FRAGMENT_BYTES - 1
    ) // MANIFEST_FRAGMENT_BYTES
    # GET_MANIFEST is 7 OTA bytes; each MANIFEST has a 7-byte OTA header.
    # Every source frame also has MeshCore's header + path-length bytes.
    manifest_source_bytes = 7 + 2 + MANIFEST_BYTES + 9 * manifest_fragments
    proof_source_bytes = sum(
        10 + 4 * proof_sibling_count(block_count, block)
        for block in range(block_count)
    )
    return (
        manifest_source_bytes + proof_source_bytes,
        1 + manifest_fragments + block_count,
        proof_source_bytes,
    )


def legacy_transport_cost(payload_size: int) -> dict[str, int | str]:
    """Ideal clean legacy transfer, including requests, DATA, and proofs."""
    blocks = _block_lengths(payload_size, LEGACY_BLOCK_SIZE)
    block_count = len(blocks)
    data_packets = sum(
        (length + LEGACY_DATA_BYTES - 1) // LEGACY_DATA_BYTES
        for length in blocks
    )
    common_bytes, common_packets, proof_bytes = _common_transport_cost(block_count)
    # DATA is 9 OTA header + 2 Mesh bytes. Each legacy block also needs one
    # 9-byte OTA_REQ frame and one 7-byte OTA_REQ_PROOF frame.
    data_bytes = payload_size + 11 * data_packets
    request_bytes = 11 * block_count
    proof_request_bytes = 9 * block_count
    manifest_bytes = common_bytes - proof_bytes
    return {
        "profile": "legacy-160-raw",
        "block_size": LEGACY_BLOCK_SIZE,
        "payload_bytes": payload_size,
        "wire_bytes": payload_size,
        "deflate_bytes": 0,
        "deflate_blocks": 0,
        "data_packets": data_packets,
        "manifest_packets": common_packets - block_count,
        "request_pipeline": 1,
        "request_packets": block_count,
        "proof_request_packets": block_count,
        "proof_packets": block_count,
        "origin_mesh_bytes": (
            common_bytes + data_bytes + request_bytes + proof_request_bytes
        ),
        "packets": common_packets + data_packets + 2 * block_count,
        "manifest_bytes": manifest_bytes,
        "block_request_bytes": request_bytes,
        "proof_request_bytes": proof_request_bytes,
        "proof_response_bytes": proof_bytes,
        "data_bytes": data_bytes,
        "request_bytes": request_bytes + proof_request_bytes,
    }


def _v2_request_flights(
    block_count: int, pipeline_capacity: int = RAK3401_PIPELINE_MAX
) -> list[int]:
    if block_count <= 0 or not 1 <= pipeline_capacity <= RAK3401_PIPELINE_MAX:
        raise RouteSearchError("invalid v2 request-flight geometry")
    flights: list[int] = []
    remaining = block_count
    width = 1
    while remaining:
        active = min(width, remaining)
        flights.append(active)
        remaining -= active
        width = min(width + 1, pipeline_capacity)
    return flights


def v2_transport_cost(
    payload_size: int,
    wire_bytes: int,
    deflate_bytes: int,
    deflate_blocks: int,
    data_packets: int,
    pipeline_capacity: int = RAK3401_PIPELINE_MAX,
    block_size: int = DEFLATE_BLOCK_SIZE,
) -> dict[str, int | str]:
    """Ideal clean negotiated v2 transfer using measured seeder output."""
    blocks = _block_lengths(payload_size, block_size)
    block_count = len(blocks)
    raw_data_packets = sum(
        (length + V2_DATA_BYTES - 1) // V2_DATA_BYTES for length in blocks
    )
    if not (0 < wire_bytes <= payload_size):
        raise RouteSearchError("v2 wire-byte count is inconsistent")
    if not (0 <= deflate_bytes <= wire_bytes):
        raise RouteSearchError("v2 DEFLATE-byte count is inconsistent")
    if not (0 <= deflate_blocks <= block_count):
        raise RouteSearchError("v2 DEFLATE-block count is inconsistent")
    if not (block_count <= data_packets <= raw_data_packets):
        raise RouteSearchError("v2 DATA-packet count is inconsistent")
    if (deflate_blocks == 0) != (deflate_bytes == 0):
        raise RouteSearchError("v2 DEFLATE totals disagree")
    if not 1 <= pipeline_capacity <= RAK3401_PIPELINE_MAX:
        raise RouteSearchError("v2 request pipeline is out of range")

    common_bytes, common_packets, proof_bytes = _common_transport_cost(block_count)
    flights = _v2_request_flights(block_count, pipeline_capacity)
    # DATA: 9-byte existing header + repeated 4-byte stream id + 2 Mesh bytes.
    data_bytes = wire_bytes + 15 * data_packets
    # One request frame per adaptive flight: 5-byte prefix + 4 bytes/block + 2 Mesh bytes.
    request_bytes = sum(7 + 4 * width for width in flights)
    manifest_bytes = common_bytes - proof_bytes
    return {
        "profile": "v2-171-deflate",
        "block_size": block_size,
        "payload_bytes": payload_size,
        "wire_bytes": wire_bytes,
        "deflate_bytes": deflate_bytes,
        "deflate_blocks": deflate_blocks,
        "data_packets": data_packets,
        "manifest_packets": common_packets - block_count,
        "request_pipeline": pipeline_capacity,
        "request_packets": len(flights),
        "proof_request_packets": 0,
        "proof_packets": block_count,
        "origin_mesh_bytes": common_bytes + data_bytes + request_bytes,
        "packets": common_packets + data_packets + len(flights),
        "manifest_bytes": manifest_bytes,
        "block_request_bytes": request_bytes,
        "proof_request_bytes": 0,
        "proof_response_bytes": proof_bytes,
        "data_bytes": data_bytes,
        "request_bytes": request_bytes,
    }


def v2_raw_transport_cost(
    payload_size: int,
    pipeline_capacity: int = RAK3401_PIPELINE_MAX,
    block_size: int = LEGACY_BLOCK_SIZE,
) -> dict[str, int | str]:
    """Ideal negotiated v2 transfer without compression for a comparison base."""
    blocks = _block_lengths(payload_size, block_size)
    data_packets = sum(
        (length + V2_DATA_BYTES - 1) // V2_DATA_BYTES for length in blocks
    )
    result = v2_transport_cost(
        payload_size,
        payload_size,
        0,
        0,
        data_packets,
        pipeline_capacity,
        block_size,
    )
    result["profile"] = "v2-171-raw"
    return result


def linear_path_bytes(origin_mesh_bytes: int, packets: int, relay_hops: int) -> int:
    """Serialized bytes for an ideal single path; real flood fan-out is external."""
    if origin_mesh_bytes < 0 or packets < 0:
        raise RouteSearchError("transport byte inputs cannot be negative")
    if not 0 <= relay_hops <= OTA_MAX_HOPS:
        raise RouteSearchError(f"relay hops must be between 0 and {OTA_MAX_HOPS}")
    transmissions = relay_hops + 1
    # Each relay retransmits the frame after appending one one-byte path hash.
    return (
        transmissions * origin_mesh_bytes
        + packets * relay_hops * transmissions // 2
    )


def truth(value: object) -> bool:
    return value is True or str(value).lower() == "true"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _integer(value: object, label: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        raise RouteSearchError(f"{label} must be an integer")
    if result < 0:
        raise RouteSearchError(f"{label} cannot be negative")
    return result


def load_inventory(path: Path) -> list[dict[str, object]]:
    try:
        document = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RouteSearchError(f"cannot read image inventory {path}: {exc}") from exc
    images = document.get("images") if isinstance(document, dict) else None
    if not isinstance(images, list) or len(images) < 2:
        raise RouteSearchError(f"invalid image inventory: {path}")

    capability_contract = document.get("capability_contract", {})
    strict_transport_contract = (
        isinstance(capability_contract, dict)
        and TRANSPORT_CAPABILITY in capability_contract
    ) or any(
        isinstance(record, dict) and TRANSPORT_CAPABILITY in record
        for record in images
    )
    strict_block_contract = strict_transport_contract or (
        isinstance(capability_contract, dict)
        and TRANSPORT_MAX_BLOCK_FIELD in capability_contract
    ) or any(
        isinstance(record, dict) and TRANSPORT_MAX_BLOCK_FIELD in record
        for record in images
    )
    edge_policy = document.get("edge_policy", {})
    strict_version_ranks = (
        isinstance(edge_policy, dict)
        and edge_policy.get("require_target_version_rank_gt_source_version_rank")
        is True
    ) or any(
        isinstance(record, dict) and "version_rank" in record
        for record in images
    )

    result: list[dict[str, object]] = []
    seen: set[str] = set()
    previous_version_rank = -1
    rank_by_version: dict[str, int] = {}
    version_by_rank: dict[int, str] = {}
    for index, raw in enumerate(images):
        if not isinstance(raw, dict):
            raise RouteSearchError(f"inventory image {index} is not an object")
        record = dict(raw)
        if _integer(record.get("node", index), f"inventory node {index}") != index:
            raise RouteSearchError(
                "inventory nodes must be ordered, contiguous, and zero-based"
            )
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise RouteSearchError(f"inventory image {index} has no path")
        image_path = Path(raw_path)
        if not image_path.is_absolute():
            image_path = path.parent / image_path
        image_path = image_path.resolve()
        if not image_path.is_file():
            raise RouteSearchError(f"missing inventory image: {image_path}")
        try:
            image = image_path.read_bytes()
        except OSError as exc:
            raise RouteSearchError(f"cannot read inventory image: {image_path}") from exc
        actual_sha = hashlib.sha256(image).hexdigest()
        expected_sha = record.get("sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in expected_sha
        ):
            raise RouteSearchError(f"image {index} SHA-256 is invalid")
        expected_sha = expected_sha.lower()
        if actual_sha != expected_sha:
            raise RouteSearchError(f"image {index} SHA mismatch")
        if expected_sha in seen:
            raise RouteSearchError(f"duplicate image SHA at node {index}")
        seen.add(expected_sha)
        actual_size = len(image)
        if _integer(record.get("size"), f"image {index} size") != actual_size:
            raise RouteSearchError(f"image {index} size mismatch")
        if not motalib.has_endf(image):
            raise RouteSearchError(f"image {index} has no valid EndF")
        ident = motalib.parse_endf_ident(image)
        if (
            ident is None
            or ident.target_id != EXPECTED_TARGET_ID
            or ident.hw_id != EXPECTED_HARDWARE
        ):
            raise RouteSearchError(f"image {index} has the wrong RAK3401 identity")
        _body, body_hash = motalib.parse_endf(image)
        declared_body_hash = record.get("body_hash")
        if (
            not isinstance(declared_body_hash, str)
            or not re.fullmatch(r"[0-9a-f]{16}", declared_body_hash)
            or declared_body_hash != body_hash.hex()
        ):
            raise RouteSearchError(f"image {index} body hash mismatch")
        version = motalib.unpack_version(ident.fw_version)
        if record.get("version") != version:
            raise RouteSearchError(f"image {index} version mismatch")
        source_commit = record.get("source_commit")
        if source_commit is not None and (
            not isinstance(source_commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", source_commit)
        ):
            raise RouteSearchError(f"image {index} source commit is invalid")
        if TRANSPORT_CAPABILITY in record:
            capability = record[TRANSPORT_CAPABILITY]
        elif strict_transport_contract:
            raise RouteSearchError(
                f"image {index} {TRANSPORT_CAPABILITY} is required by the "
                "inventory capability contract"
            )
        else:
            # Compatibility for pre-contract inventories. New inventories must
            # carry the canonical field explicitly so transport is fail-closed.
            capability = record.get(TRANSPORT_CAPABILITY_ALIAS, False)
        if not isinstance(capability, bool):
            raise RouteSearchError(
                f"image {index} {TRANSPORT_CAPABILITY} must be boolean"
            )
        alias_capability = record.get(TRANSPORT_CAPABILITY_ALIAS, capability)
        if not isinstance(alias_capability, bool) or alias_capability != capability:
            raise RouteSearchError(
                f"image {index} {TRANSPORT_CAPABILITY_ALIAS} disagrees with "
                f"{TRANSPORT_CAPABILITY}"
            )
        record[TRANSPORT_CAPABILITY] = capability
        raw_max_block = record.get(TRANSPORT_MAX_BLOCK_FIELD)
        if raw_max_block is None:
            if strict_block_contract:
                raise RouteSearchError(
                    f"image {index} {TRANSPORT_MAX_BLOCK_FIELD} is required by "
                    "the inventory capability contract"
                )
            max_block = LEGACY_BLOCK_SIZE
        else:
            max_block = _integer(
                raw_max_block, f"image {index} {TRANSPORT_MAX_BLOCK_FIELD}"
            )
            if max_block not in (LEGACY_BLOCK_SIZE, DEFLATE_BLOCK_SIZE):
                raise RouteSearchError(
                    f"image {index} {TRANSPORT_MAX_BLOCK_FIELD} must be "
                    f"{LEGACY_BLOCK_SIZE} or {DEFLATE_BLOCK_SIZE}"
                )
        record[TRANSPORT_MAX_BLOCK_FIELD] = max_block
        uses_v2 = capability or max_block > LEGACY_BLOCK_SIZE
        raw_pipeline = record.get(TRANSPORT_PIPELINE_FIELD)
        if (
            raw_pipeline is None
            and not strict_transport_contract
            and TRANSPORT_PIPELINE_ALIAS in record
        ):
            raw_pipeline = record[TRANSPORT_PIPELINE_ALIAS]
        if uses_v2 and raw_pipeline is None:
            raise RouteSearchError(
                f"image {index} {TRANSPORT_PIPELINE_FIELD} is required for "
                "v2 transport"
            )
        if uses_v2:
            pipeline = _integer(
                raw_pipeline, f"image {index} {TRANSPORT_PIPELINE_FIELD}"
            )
            if not 1 <= pipeline <= RAK3401_PIPELINE_MAX:
                raise RouteSearchError(
                    f"image {index} {TRANSPORT_PIPELINE_FIELD} must be between 1 "
                    f"and {RAK3401_PIPELINE_MAX}"
                )
            record[TRANSPORT_PIPELINE_FIELD] = pipeline
        elif raw_pipeline is not None and strict_transport_contract:
            raise RouteSearchError(
                f"image {index} {TRANSPORT_PIPELINE_FIELD} must be null when "
                "neither DEFLATE nor 2 KiB blocks are enabled"
            )
        else:
            record[TRANSPORT_PIPELINE_FIELD] = None

        if "version_rank" not in record and strict_version_ranks:
            raise RouteSearchError(
                f"image {index} version_rank is required by the inventory edge policy"
            )
        version_rank = _integer(
            record.get("version_rank", index), f"image {index} version_rank"
        )
        if version_rank < previous_version_rank:
            raise RouteSearchError("inventory version_rank values must be nondecreasing")
        if strict_version_ranks:
            known_rank = rank_by_version.setdefault(version, version_rank)
            known_version = version_by_rank.setdefault(version_rank, version)
            if known_rank != version_rank or known_version != version:
                raise RouteSearchError(
                    "inventory version_rank must map one-to-one with firmware version"
                )
        previous_version_rank = version_rank
        record["version_rank"] = version_rank
        if index == 1 and _integer(
            record.get("baseline_container_size"),
            "node 1 baseline_container_size",
        ) <= 0:
            raise RouteSearchError("node 1 baseline_container_size must be positive")
        record.update(node=index, path=str(image_path), sha256=expected_sha, size=actual_size)
        result.append(record)
    return result


def snapshot_inventory(source: Path, destination: Path) -> Path:
    """Copy an inventory and its images before starting parallel patch work."""
    try:
        document = json.loads(source.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RouteSearchError(f"cannot read image inventory {source}: {exc}") from exc
    images = document.get("images") if isinstance(document, dict) else None
    if not isinstance(images, list) or len(images) < 2:
        raise RouteSearchError(f"invalid image inventory: {source}")
    image_directory = destination.parent / "images"
    image_directory.mkdir(parents=True, exist_ok=False)
    for index, raw in enumerate(images):
        if not isinstance(raw, dict):
            raise RouteSearchError(f"inventory image {index} is not an object")
        raw_path = raw.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise RouteSearchError(f"inventory image {index} has no path")
        image_source = Path(raw_path)
        if not image_source.is_absolute():
            image_source = source.parent / image_source
        image_destination = image_directory / f"image-{index:03d}.bin"
        try:
            shutil.copy2(image_source, image_destination, follow_symlinks=True)
        except OSError as exc:
            raise RouteSearchError(
                f"cannot snapshot inventory image {index}: {image_source}: {exc}"
            ) from exc
        raw["path"] = str(Path("images") / image_destination.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    # Validate the copied bytes against every declared content and EndF pin.
    load_inventory(destination)
    return destination


def insert_bridges_before_endpoint(
    document: dict[str, object], bridges: list[dict[str, object]]
) -> dict[str, object]:
    """Insert ordered bridge records immediately before the existing endpoint."""
    existing = document.get("images")
    if (
        not isinstance(existing, list)
        or len(existing) < 2
        or not all(isinstance(item, dict) for item in existing)
    ):
        raise RouteSearchError("inventory document has no valid start and endpoint")
    if not all(isinstance(item, dict) for item in bridges):
        raise RouteSearchError("inventory bridges must be objects")
    # Copy every record, preserving the last existing record as the endpoint.
    ordered = [dict(item) for item in existing[:-1]]
    ordered.extend(dict(item) for item in bridges)
    ordered.append(dict(existing[-1]))
    images = []
    for node, record in enumerate(ordered):
        record["node"] = node
        images.append(record)
    result = dict(document)
    result["images"] = images
    return result


def cache_key(source_sha: str, target_sha: str, memory: int) -> tuple[str, str, int]:
    return source_sha.lower(), target_sha.lower(), memory


def transport_stats_complete(row: dict[str, object]) -> bool:
    return all(row.get(field) not in (None, "") for field in TRANSPORT_FIELDS)


def normalize_transport_stats(
    row: dict[str, object], payload_size: int, label: str,
    block_size: int = DEFLATE_BLOCK_SIZE,
) -> dict[str, object]:
    present = [row.get(field) not in (None, "") for field in TRANSPORT_FIELDS]
    if any(present) and not all(present):
        raise RouteSearchError(f"partial transport statistics at {label}")
    if not any(present):
        return {field: "" for field in TRANSPORT_FIELDS}

    normalized: dict[str, object] = {}
    for field in TRANSPORT_FIELDS[:2]:
        digest = row.get(field)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RouteSearchError(f"invalid {field} at {label}")
        normalized[field] = digest
    for field in TRANSPORT_FIELDS[2:]:
        normalized[field] = _integer(row.get(field), f"{field} at {label}")
    v2_transport_cost(
        payload_size,
        int(normalized["v2_wire_bytes"]),
        int(normalized["v2_deflate_bytes"]),
        int(normalized["v2_deflate_blocks"]),
        int(normalized["v2_data_packets"]),
        block_size=block_size,
    )
    return normalized


def measure_transport_size(
    tool: Path,
    payload: Path,
    block_size: int = DEFLATE_BLOCK_SIZE,
) -> dict[str, object]:
    """Ask motatool's live-seeder encoder for exact per-block byte counts."""
    block_size = _validate_block_size(block_size)
    try:
        completed = subprocess.run(
            [str(tool), "transport-size", "--payload", str(payload),
             "--block-size", str(block_size)],
            check=False, capture_output=True, text=True, encoding="utf-8",
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise RouteSearchError(f"transport-size tool timed out for {payload}") from exc
    except OSError as exc:
        raise RouteSearchError(f"cannot run transport-size tool {tool}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise RouteSearchError(
            f"transport-size tool failed for {payload}: {detail or completed.returncode}"
        )
    try:
        measured = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RouteSearchError("transport-size tool returned invalid JSON") from exc
    if not isinstance(measured, dict) or measured.get("schema") != 1:
        raise RouteSearchError("transport-size tool returned an unsupported result")
    expected_payload = payload.stat().st_size
    if measured.get("payload_bytes") != expected_payload:
        raise RouteSearchError("transport-size tool measured the wrong payload")
    if measured.get("block_size") != block_size:
        raise RouteSearchError("transport-size tool used the wrong block size")
    expected_blocks = (expected_payload + block_size - 1) // block_size
    if measured.get("block_count") != expected_blocks:
        raise RouteSearchError("transport-size tool returned the wrong block count")
    row = {
        "payload_sha256": sha256_file(payload),
        "transport_encoder_sha256": sha256_file(tool),
        "v2_wire_bytes": measured.get("wire_bytes"),
        "v2_deflate_bytes": measured.get("deflate_bytes"),
        "v2_deflate_blocks": measured.get("deflate_blocks"),
        "v2_data_packets": measured.get("data_packets"),
    }
    return normalize_transport_stats(
        row, expected_payload, str(payload), block_size
    )


def migrate_csv(
    inventory_path: Path, csv_path: Path
) -> dict[tuple[str, str, int], dict[str, object]]:
    """Migrate cached edges by hash, with numeric-index fallback for old CSVs."""
    images = load_inventory(inventory_path)
    nodes_by_sha = {
        str(image["sha256"]): int(image["node"]) for image in images
    }
    migrated: dict[tuple[str, str, int], dict[str, object]] = {}
    try:
        source = csv_path.open(newline="", encoding="ascii")
    except OSError as exc:
        raise RouteSearchError(f"cannot read geometry cache {csv_path}: {exc}") from exc
    with source:
        reader = csv.DictReader(source)
        required_fields = {"source", "target", "memory"}
        if reader.fieldnames is None or not required_fields.issubset(reader.fieldnames):
            raise RouteSearchError(f"geometry cache has missing columns: {csv_path}")
        for line, row in enumerate(reader, 2):
            recorded_source = _integer(
                row["source"], f"source at {csv_path}:{line}"
            )
            recorded_target = _integer(
                row["target"], f"target at {csv_path}:{line}"
            )
            if recorded_source >= recorded_target:
                raise RouteSearchError(f"bad nodes at {csv_path}:{line}")
            resolved: list[tuple[int, str]] = []
            for column, recorded_node in (
                ("source_sha256", recorded_source),
                ("target_sha256", recorded_target),
            ):
                raw_recorded = row.get(column, "")
                if raw_recorded is None:
                    raw_recorded = ""
                if not isinstance(raw_recorded, str):
                    raise RouteSearchError(
                        f"{column} is invalid at {csv_path}:{line}"
                    )
                recorded_sha = raw_recorded.lower()
                if recorded_sha:
                    if not re.fullmatch(r"[0-9a-f]{64}", recorded_sha):
                        raise RouteSearchError(
                            f"{column} is invalid at {csv_path}:{line}"
                        )
                    resolved_node = nodes_by_sha.get(recorded_sha)
                    if resolved_node is None:
                        raise RouteSearchError(
                            f"{column} is absent from inventory at {csv_path}:{line}"
                        )
                    resolved.append((resolved_node, recorded_sha))
                else:
                    if recorded_node >= len(images):
                        raise RouteSearchError(f"bad nodes at {csv_path}:{line}")
                    resolved.append(
                        (recorded_node, str(images[recorded_node]["sha256"]))
                    )
            (source_node, source_sha), (target_node, target_sha) = resolved
            if source_node >= target_node:
                raise RouteSearchError(f"bad nodes at {csv_path}:{line}")
            memory = _integer(row["memory"], f"memory at {csv_path}:{line}")
            try:
                payload = int(row.get("payload", ""))
                recorded_block_size = row.get("block_size", "")
                total = int(row.get("container", ""))
                stage_start = int(row.get("stage_start", ""))
                margin = int(row.get("margin", ""))
            except (TypeError, ValueError) as exc:
                raise RouteSearchError(
                    f"invalid numeric geometry at {csv_path}:{line}"
                ) from exc
            if payload < 0:
                raise RouteSearchError(
                    f"failed cached geometry cannot prove an exhaustive search at "
                    f"{csv_path}:{line}; regenerate it"
                )
            if row.get("error", ""):
                raise RouteSearchError(
                    f"successful cached geometry carries an error at "
                    f"{csv_path}:{line}; regenerate it"
                )
            expected_block_size = source_block_size(images, source_node)
            # Geometry caches predating per-source block sizing have no column
            # and therefore contain 1 KiB container arithmetic. The detools
            # payload itself does not depend on the Merkle block size, so first
            # authenticate that historical arithmetic and then migrate only
            # the derived container/staging fields. Transport counters do
            # depend on block boundaries and are deliberately invalidated.
            if recorded_block_size in (None, ""):
                recorded_block_size_int = LEGACY_BLOCK_SIZE
            else:
                recorded_block_size_int = _integer(
                    recorded_block_size, f"block_size at {csv_path}:{line}"
                )
            recorded_total = container_size(payload, recorded_block_size_int)
            recorded_stage = align_down(STAGE_CEILING - recorded_total)
            recorded_margin = recorded_stage - (APP_BASE + memory)
            if (total, stage_start, margin) != (
                recorded_total, recorded_stage, recorded_margin
            ):
                raise RouteSearchError(
                    f"inconsistent cached geometry at {csv_path}:{line}"
                )
            if truth(row.get("feasible", False)) != (payload >= 0 and margin >= 0):
                raise RouteSearchError(
                    f"cached feasibility is inconsistent at {csv_path}:{line}"
                )
            block_size = expected_block_size
            total = container_size(payload, block_size)
            stage_start = align_down(STAGE_CEILING - total)
            margin = stage_start - (APP_BASE + memory)
            feasible = payload >= 0 and margin >= 0
            key = cache_key(source_sha, target_sha, memory)
            normalized = {field: row.get(field, "") for field in FIELDS}
            normalized.update(source=source_node, target=target_node,
                              source_sha256=source_sha, target_sha256=target_sha,
                              memory=memory, payload=payload,
                              block_size=block_size, container=total,
                              stage_start=stage_start, margin=margin,
                              feasible=feasible, error="")
            if recorded_block_size_int == block_size:
                normalized.update(normalize_transport_stats(
                    row, payload, f"{csv_path}:{line}", block_size
                ))
            else:
                normalized.update({field: "" for field in TRANSPORT_FIELDS})
            previous = migrated.get(key)
            comparable = (
                "payload", "block_size", "container", "stage_start", "margin",
                "feasible", "error",
            )
            if previous is not None and any(
                str(previous.get(item, "")) != str(normalized.get(item, ""))
                for item in comparable
            ):
                raise RouteSearchError(f"conflicting cached geometry for {key}")
            if previous is not None and transport_stats_complete(previous):
                if transport_stats_complete(normalized) and any(
                    str(previous.get(item, "")) != str(normalized.get(item, ""))
                    for item in TRANSPORT_FIELDS
                ):
                    raise RouteSearchError(f"conflicting transport statistics for {key}")
                if not transport_stats_complete(normalized):
                    normalized.update(
                        {field: previous[field] for field in TRANSPORT_FIELDS}
                    )
            migrated[key] = normalized
    return migrated


Job = tuple[int, int, int, str, str, str, str, int]


def is_forward_progress(
    images: list[dict[str, object]], source: int, target: int
) -> bool:
    """Only a strictly newer firmware version may be a route edge."""
    return int(images[target]["version_rank"]) > int(images[source]["version_rank"])


def all_jobs(images: list[dict[str, object]]) -> list[Job]:
    """Enumerate every valid page-aligned workspace for every forward edge."""
    jobs: list[Job] = []
    endpoint = len(images) - 1
    # Node 0 may only use the byte-identical, physically qualified 0->1 package.
    # Node 1 still runs the fixed-workspace receiver, so measure each of its
    # possible forward transitions at exactly that legacy workspace.
    for target in range(2, endpoint + 1):
        if not is_forward_progress(images, 1, target):
            continue
        jobs.append((1, target, FIXED_MEMORY, str(images[1]["path"]),
                     str(images[target]["path"]), str(images[1]["sha256"]),
                     str(images[target]["sha256"]),
                     source_block_size(images, 1)))
    for source in range(2, endpoint):
        source_size = int(images[source]["size"])
        for target in range(source + 1, endpoint + 1):
            if not is_forward_progress(images, source, target):
                continue
            target_size = int(images[target]["size"])
            first_page = align_up(max(source_size + 2 * PAGE, target_size)) // PAGE
            for memory_page in range(first_page, AVAILABLE_PAGES):
                jobs.append((source, target, memory_page * PAGE,
                             str(images[source]["path"]), str(images[target]["path"]),
                             str(images[source]["sha256"]), str(images[target]["sha256"]),
                             source_block_size(images, source)))
    return jobs


def feasible_forward_pairs(
    rows: list[dict[str, object]], images: list[dict[str, object]]
) -> set[tuple[int, int]]:
    """Collapse feasible workspace rows into the forward route graph."""
    pairs = {(0, 1)}
    for row in rows:
        source = int(row["source"])
        target = int(row["target"])
        if not (0 <= source < len(images) and 0 <= target < len(images)):
            raise RouteSearchError(f"geometry edge {source}->{target} is out of range")
        if (
            source != 0
            and truth(row.get("feasible", False))
            and is_forward_progress(images, source, target)
        ):
            pairs.add((source, target))
    return pairs


def minimum_hop_pairs(
    rows: list[dict[str, object]], images: list[dict[str, object]]
) -> set[tuple[int, int]]:
    """Return exactly the edges lying on at least one shortest endpoint route."""
    pairs = feasible_forward_pairs(rows, images)
    endpoint = len(images) - 1
    outgoing: dict[int, list[int]] = {}
    for source, target in pairs:
        outgoing.setdefault(source, []).append(target)

    infinity = sys.maxsize
    from_start = [infinity] * len(images)
    from_start[0] = 0
    for source in range(endpoint):
        if from_start[source] == infinity:
            continue
        for target in outgoing.get(source, []):
            from_start[target] = min(from_start[target], from_start[source] + 1)
    if from_start[endpoint] == infinity:
        return set()

    to_endpoint = [infinity] * len(images)
    to_endpoint[endpoint] = 0
    for source in range(endpoint - 1, -1, -1):
        for target in outgoing.get(source, []):
            if to_endpoint[target] != infinity:
                to_endpoint[source] = min(
                    to_endpoint[source], to_endpoint[target] + 1
                )
    shortest = from_start[endpoint]
    return {
        (source, target)
        for source, target in pairs
        if from_start[source] != infinity
        and to_endpoint[target] != infinity
        and from_start[source] + 1 + to_endpoint[target] == shortest
    }


def job_needs_transport_stats(
    job: Job,
    images: list[dict[str, object]],
    objective: str,
    relevant_pairs: set[tuple[int, int]] | None = None,
) -> bool:
    return (
        objective == "transport"
        and source_uses_deflate(images, job[0])
        and (relevant_pairs is None or (job[0], job[1]) in relevant_pairs)
    )


def cache_satisfies_job(
    row: dict[str, object] | None,
    needs_transport: bool,
    expected_encoder_sha256: str | None = None,
    expected_block_size: int | None = None,
) -> bool:
    return row is not None and (
        expected_block_size is None
        or int(row.get("block_size") or LEGACY_BLOCK_SIZE) == expected_block_size
    ) and (
        not needs_transport
        or not truth(row.get("feasible", False))
        or (
            transport_stats_complete(row)
            and (
                expected_encoder_sha256 is None
                or row.get("transport_encoder_sha256") == expected_encoder_sha256
            )
        )
    )


def geometry_job(args: tuple[object, ...]) -> dict[str, object]:
    try:
        import detools
    except ImportError as exc:
        raise RouteSearchError(
            "detools is required to generate geometry; run this tool in the "
            "detools pipx environment"
        ) from exc
    if len(args) not in (9, 10):
        raise RouteSearchError("invalid geometry job")
    (
        source, target, memory, from_raw, to_raw, source_sha, target_sha,
        block_size, patches_raw,
    ) = args[:9]
    transport_tool = str(args[9]) if len(args) == 10 and args[9] else ""
    source = int(source)
    target = int(target)
    memory = int(memory)
    block_size = _validate_block_size(int(block_size))
    from_raw = str(from_raw)
    to_raw = str(to_raw)
    source_sha = str(source_sha)
    target_sha = str(target_sha)
    patches_raw = str(patches_raw)
    patch = Path(patches_raw) / f"{source_sha}-{target_sha}-{memory // PAGE:03d}.patch"
    try:
        detools.create_patch_filenames(
            from_raw, to_raw, str(patch), compression="crle", patch_type="in-place",
            algorithm="bsdiff", suffix_array_algorithm="divsufsort",
            memory_size=memory, segment_size=SEGMENT, use_mmap=True,
        )
        payload = patch.stat().st_size
        total = container_size(payload, block_size)
        stage_start = align_down(STAGE_CEILING - total)
        margin = stage_start - (APP_BASE + memory)
        error = ""
        transport = (
            measure_transport_size(Path(transport_tool), patch, block_size)
            if transport_tool and margin >= 0
            else {field: "" for field in TRANSPORT_FIELDS}
        )
    except Exception as exc:  # detools reports several backend exception types
        detail = f"{type(exc).__name__}: {exc}"[-500:].replace("\n", " ")
        raise RouteSearchError(
            f"detools geometry failed for {source}->{target} at 0x{memory:X}: {detail}"
        ) from exc
    finally:
        patch.unlink(missing_ok=True)
    return {"source": source, "target": target, "source_sha256": source_sha,
            "target_sha256": target_sha, "memory": memory, "payload": payload,
            "block_size": block_size, "container": total,
            "stage_start": stage_start, "margin": margin,
            "feasible": payload >= 0 and margin >= 0, "error": error,
            **transport}


def project_cache(cache: dict[tuple[str, str, int], dict[str, object]],
                  images: list[dict[str, object]],
                  required: set[tuple[str, str, int]]) -> list[dict[str, object]]:
    index = {str(record["sha256"]): int(record["node"]) for record in images}
    rows: list[dict[str, object]] = []
    for key in sorted(required, key=lambda item: (index[item[0]], index[item[1]], item[2])):
        if key not in cache:
            raise RouteSearchError(f"required cache geometry is missing: {key}")
        row = dict(cache[key])
        if int(row.get("payload", -1)) < 0 or str(row.get("error", "")):
            raise RouteSearchError(
                f"required cache geometry is not a successful measurement: {key}"
            )
        row.update(source=index[key[0]], target=index[key[1]],
                   source_sha256=key[0], target_sha256=key[1], memory=key[2])
        expected_block_size = source_block_size(images, int(row["source"]))
        if int(row.get("block_size") or LEGACY_BLOCK_SIZE) != expected_block_size:
            raise RouteSearchError(
                f"required cache geometry has the wrong block size: {key}"
            )
        row["block_size"] = expected_block_size
        rows.append(row)
    return rows


def write_atomic_text(path: Path, contents: str) -> None:
    """Replace one regular output without ever following its pathname symlink."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RouteSearchError(f"output path is not a regular file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(
            descriptor, "w", encoding="ascii", newline="\n"
        ) as output:
            descriptor_open = False
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except BaseException:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        temporary.unlink(missing_ok=True)
        raise


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RouteSearchError(f"output path is not a regular file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="ascii") as output:
            descriptor_open = False
            writer = csv.DictWriter(
                output, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except BaseException:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        temporary.unlink(missing_ok=True)
        raise


def source_block_size(images: list[dict[str, object]], source: int) -> int:
    """Logical package/Merkle block size accepted by the running receiver."""
    if source == 0:
        return LEGACY_BLOCK_SIZE
    block_size = int(images[source].get(TRANSPORT_MAX_BLOCK_FIELD, 0))
    if block_size not in (LEGACY_BLOCK_SIZE, DEFLATE_BLOCK_SIZE):
        raise RouteSearchError(
            f"source image {source} has no explicit supported application "
            "block size"
        )
    return block_size


def source_uses_deflate(images: list[dict[str, object]], source: int) -> bool:
    """Whether the running receiver permits compressed v2 DATA blocks."""
    return source != 0 and bool(images[source][TRANSPORT_CAPABILITY])


def source_uses_v2(images: list[dict[str, object]], source: int) -> bool:
    """Whether either independent capability requires the negotiated v2 wire."""
    return source != 0 and (
        source_uses_deflate(images, source)
        or source_block_size(images, source) > LEGACY_BLOCK_SIZE
    )


def source_transport_profile(
    images: list[dict[str, object]], source: int
) -> str:
    """Select the profile from the independent compression/block-size axes."""
    if source == 0:
        return "legacy-160-raw"
    key = (
        f"{source_block_size(images, source)},deflate="
        f"{str(source_uses_deflate(images, source)).lower()}"
    )
    try:
        return TRANSPORT_PROFILE_MATRIX[key]
    except KeyError as exc:
        raise RouteSearchError(
            f"source image {source} has an unsupported transport capability matrix"
        ) from exc


def source_transport_pipeline(images: list[dict[str, object]], source: int) -> int:
    """Return the request-flight capacity compiled into the source image."""
    pipeline = images[source][TRANSPORT_PIPELINE_FIELD]
    if pipeline is None:
        raise RouteSearchError(f"source image {source} has no transport pipeline")
    return int(pipeline)


def transport_encoder_for_rows(
    rows: list[dict[str, object]], images: list[dict[str, object]],
    relevant_pairs: set[tuple[int, int]] | None = None,
) -> str | None:
    """Require one exact live-seeder encoder across comparable measurements."""
    encoders = {
        str(row["transport_encoder_sha256"])
        for row in rows
        if truth(row.get("feasible", False))
        and source_uses_deflate(images, int(row["source"]))
        and (
            relevant_pairs is None
            or (int(row["source"]), int(row["target"])) in relevant_pairs
        )
        and transport_stats_complete(row)
    }
    if len(encoders) > 1:
        raise RouteSearchError(
            "transport statistics mix different motatool encoder binaries"
        )
    return next(iter(encoders)) if encoders else None


def edge_transport_cost(
    row: dict[str, object], images: list[dict[str, object]], relay_hops: int
) -> dict[str, int | str]:
    source = int(row["source"])
    block_size = source_block_size(images, source)
    recorded_block_size = int(row.get("block_size") or block_size)
    if recorded_block_size != block_size:
        raise RouteSearchError(
            f"geometry edge {source}->{int(row['target'])} uses "
            f"{recorded_block_size}-byte blocks, expected {block_size}"
        )
    payload = int(row.get("payload", 0))
    if payload <= 0:
        payload = payload_size_from_container(int(row["container"]), block_size)
    elif int(row["container"]) != container_size(payload, block_size):
        raise RouteSearchError(
            f"geometry edge {source}->{int(row['target'])} container does not "
            "match its logical block size"
        )
    profile = source_transport_profile(images, source)
    if profile == "v2-171-deflate":
        if not transport_stats_complete(row):
            raise RouteSearchError(
                f"missing transport statistics for compressed source edge "
                f"{source}->{int(row['target'])} memory=0x{int(row['memory']):X}"
            )
        cost = v2_transport_cost(
            payload,
            int(row["v2_wire_bytes"]),
            int(row["v2_deflate_bytes"]),
            int(row["v2_deflate_blocks"]),
            int(row["v2_data_packets"]),
            source_transport_pipeline(images, source),
            block_size,
        )
    elif profile == "v2-171-raw":
        cost = v2_raw_transport_cost(
            payload,
            source_transport_pipeline(images, source),
            block_size,
        )
    else:
        cost = legacy_transport_cost(payload)
    result = dict(cost)
    result["linear_path_bytes"] = linear_path_bytes(
        int(cost["origin_mesh_bytes"]), int(cost["packets"]), relay_hops
    )
    return result


def select_route(rows: list[dict[str, object]], images: list[dict[str, object]],
                 baseline_size: int, output: Path, complete: bool,
                 objective: str = "container", relay_hops: int = 0) -> dict[str, object]:
    if objective not in ("container", "transport"):
        raise RouteSearchError(f"unknown route objective: {objective}")
    if objective == "transport" and not complete:
        raise RouteSearchError(
            "transport objective requires complete geometry before shortest-DAG sizing"
        )
    if not 0 <= relay_hops <= OTA_MAX_HOPS:
        raise RouteSearchError(f"relay hops must be between 0 and {OTA_MAX_HOPS}")
    endpoint = len(images) - 1
    baseline_stage = align_down(STAGE_CEILING - baseline_size)
    baseline_margin = baseline_stage - (APP_BASE + FIXED_MEMORY)
    if baseline_margin < 0:
        raise RouteSearchError("pinned first package does not fit")
    baseline_row: dict[str, object] = {
        "source": 0, "target": 1, "memory": FIXED_MEMORY,
        "block_size": LEGACY_BLOCK_SIZE,
        "container": baseline_size, "stage_start": baseline_stage,
        "margin": baseline_margin, "feasible": True,
    }
    if objective == "transport":
        baseline_row["payload"] = payload_size_from_container(baseline_size)
    all_feasible_pairs = feasible_forward_pairs(rows, images)
    relevant_pairs = (
        minimum_hop_pairs(rows, images)
        if objective == "transport" else all_feasible_pairs
    )
    edges: dict[tuple[int, int], dict[str, object]] = {(0, 1): baseline_row}
    for row in rows:
        source = int(row["source"])
        target = int(row["target"])
        if not (0 <= source < len(images) and 0 <= target < len(images)):
            raise RouteSearchError(f"geometry edge {source}->{target} is out of range")
        if source == 0:
            # Older evidence tables measured these irrelevant shortcuts. Keep
            # them countable for archive verification, but never admit them to
            # the route graph; node 0 is pinned unconditionally to node 1.
            continue
        if not is_forward_progress(images, source, target):
            continue
        if not truth(row.get("feasible", False)):
            continue
        key = source, target
        if key not in relevant_pairs:
            continue
        if objective == "transport":
            transport_bytes = int(
                edge_transport_cost(row, images, relay_hops)["linear_path_bytes"]
            )
            rank = (
                transport_bytes, int(row["container"]),
                -int(row["margin"]), int(row["memory"]),
            )
        else:
            rank = (int(row["container"]), -int(row["margin"]), int(row["memory"]))
        old = edges.get(key)
        if old is None:
            edges[key] = row
        else:
            if objective == "transport":
                old_rank = (
                    int(edge_transport_cost(old, images, relay_hops)["linear_path_bytes"]),
                    int(old["container"]), -int(old["margin"]), int(old["memory"]),
                )
            else:
                old_rank = (
                    int(old["container"]), -int(old["margin"]), int(old["memory"]),
                )
            if rank < old_rank:
                edges[key] = row

    best: dict[int, tuple[int, int, list[int]]] = {0: (0, 0, [0])}
    hop_distance = [sys.maxsize] * len(images)
    route_count = [0] * len(images)
    hop_distance[0], route_count[0] = 0, 1
    outgoing: dict[int, list[tuple[int, dict[str, object]]]] = {}
    for (source, target), row in edges.items():
        outgoing.setdefault(source, []).append((target, row))
    for source in range(endpoint):
        if source not in best:
            continue
        for target, row in outgoing.get(source, []):
            edge_bytes = (
                int(edge_transport_cost(row, images, relay_hops)["linear_path_bytes"])
                if objective == "transport" else int(row["container"])
            )
            candidate = (best[source][0] + 1,
                         best[source][1] + edge_bytes,
                         best[source][2] + [target])
            if target not in best or candidate < best[target]:
                best[target] = candidate
            distance = hop_distance[source] + 1
            if distance < hop_distance[target]:
                hop_distance[target], route_count[target] = distance, route_count[source]
            elif distance == hop_distance[target]:
                route_count[target] += route_count[source]

    objective_text = (
        "minimum packages, then minimum ideal linear-path serialized MeshCore bytes"
        if objective == "transport"
        else "minimum packages, then minimum total container bytes"
    )
    common = {"schema": 2, "app_base": f"0x{APP_BASE:X}",
              "stage_ceiling": f"0x{STAGE_CEILING:X}", "node_count": len(images),
              "search_complete": complete, "candidate_geometries": len(rows),
              "feasible_edges": len(all_feasible_pairs),
              "objective": objective_text}
    if objective == "transport":
        transport_encoder = transport_encoder_for_rows(
            rows, images, relevant_pairs
        )
        common["transport_accounting"] = {
            "relay_hops": relay_hops,
            "legacy_block_size": LEGACY_BLOCK_SIZE,
            "supported_block_sizes": [LEGACY_BLOCK_SIZE, DEFLATE_BLOCK_SIZE],
            "comparison_baseline_block_size": LEGACY_BLOCK_SIZE,
            "source_capability_field": TRANSPORT_CAPABILITY,
            "source_pipeline_field": TRANSPORT_PIPELINE_FIELD,
            "source_max_block_field": TRANSPORT_MAX_BLOCK_FIELD,
            "source_profile_matrix": dict(TRANSPORT_PROFILE_MATRIX),
            "first_bootstrap_profile": "legacy-160-raw",
            "included": (
                "OTA messages, repeated 4-byte v2 stream IDs, 171-byte DATA slicing, "
                "adaptive requests, manifest, exact per-block proofs, and MeshCore framing"
            ),
            "excluded": (
                "discovery, retries, flood fan-out, and radio-dependent LoRa PHY coding/preamble"
            ),
        }
        if transport_encoder is not None:
            common["transport_accounting"]["encoder_sha256"] = transport_encoder
    if endpoint not in best:
        result = {**common, "status": "unreachable", "reachable_nodes": sorted(best),
                  "endpoint_node": endpoint,
                  "endpoint_incoming_feasible": sorted(
                      source for source, target in all_feasible_pairs
                      if target == endpoint
                  )}
    else:
        nodes = best[endpoint][2]
        steps = []
        for source, target in zip(nodes, nodes[1:]):
            row = edges[source, target]
            step = {"source_node": source, "target_node": target,
                    "block_size": source_block_size(images, source),
                    "inplace_memory": f"0x{int(row['memory']):X}",
                    "reuse_baseline_package": source == 0 and target == 1,
                    "expected_container_size": int(row["container"]),
                    "expected_staging_margin": int(row["margin"]),
                    "expected_target_sha256": images[target]["sha256"],
                    "expected_target_version": images[target].get("version", "unknown")}
            if objective == "transport":
                cost = edge_transport_cost(row, images, relay_hops)
                step["transport"] = {
                    key: cost[key] for key in (
                        "profile", "payload_bytes", "wire_bytes", "deflate_bytes",
                        "deflate_blocks", "block_size", "data_packets", "request_packets",
                        "manifest_packets", "request_pipeline", "proof_request_packets",
                        "proof_packets", "packets", "manifest_bytes", "data_bytes",
                        "block_request_bytes", "proof_request_bytes",
                        "proof_response_bytes", "origin_mesh_bytes", "linear_path_bytes",
                    )
                }
                if source_uses_deflate(images, source):
                    step["transport"]["payload_sha256"] = row["payload_sha256"]
                    step["transport"]["encoder_sha256"] = row[
                        "transport_encoder_sha256"
                    ]
            steps.append(step)
        selected_container_bytes = sum(
            int(edges[source, target]["container"])
            for source, target in zip(nodes, nodes[1:])
        )
        result = {**common, "status": "reachable", "nodes": nodes,
                  "shortest_package_count": hop_distance[endpoint],
                  "shortest_route_count": route_count[endpoint],
                  "selected_total_bytes": selected_container_bytes, "steps": steps}
        if objective == "transport":
            result["selected_total_transport_bytes"] = best[endpoint][1]
    write_atomic_text(
        output, json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--reuse", action="append", default=[], metavar="IMAGES_JSON=GEOMETRY_CSV")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--no-generate", action="store_true")
    parser.add_argument(
        "--objective", choices=("container", "transport"), default="container",
        help="secondary objective after minimum package count",
    )
    parser.add_argument(
        "--motatool", metavar="PATH",
        help="motatool with `transport-size`; required to measure capable-source edges",
    )
    parser.add_argument(
        "--relay-hops", type=int, default=0,
        help="ideal linear-path relays for transport accounting (default: direct)",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    if not 0 <= args.relay_hops <= OTA_MAX_HOPS:
        parser.error(f"--relay-hops must be between 0 and {OTA_MAX_HOPS}")
    transport_tool: Path | None = None
    if args.motatool:
        discovered = shutil.which(args.motatool)
        transport_tool = Path(discovered or args.motatool).resolve()
        if not transport_tool.is_file():
            parser.error(f"--motatool is not a regular file: {transport_tool}")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".frozen-inventory-", dir=args.work_dir
    ) as frozen_raw:
        frozen = Path(frozen_raw)
        frozen_transport_tool: Path | None = None
        frozen_transport_tool_sha256: str | None = None
        if transport_tool is not None:
            frozen_transport_tool = frozen / (
                "motatool.exe" if transport_tool.suffix.lower() == ".exe" else "motatool"
            )
            try:
                shutil.copy2(transport_tool, frozen_transport_tool, follow_symlinks=True)
            except OSError as exc:
                raise RouteSearchError(
                    f"cannot snapshot transport-size tool {transport_tool}: {exc}"
                ) from exc
            frozen_transport_tool_sha256 = sha256_file(frozen_transport_tool)
        inventory_path = snapshot_inventory(
            args.inventory, frozen / "current" / "images.json"
        )
        images = load_inventory(inventory_path)
        baseline_size = _integer(
            images[1].get("baseline_container_size"),
            "node 1 baseline_container_size",
        )
        cache: dict[tuple[str, str, int], dict[str, object]] = {}
        comparable = (
            "payload", "block_size", "container", "stage_start", "margin",
            "feasible", "error",
        )
        for reuse_number, value in enumerate(args.reuse, 1):
            left, separator, right = value.partition("=")
            if not separator:
                parser.error("--reuse requires IMAGES_JSON=GEOMETRY_CSV")
            reuse_root = frozen / f"reuse-{reuse_number:03d}"
            reuse_inventory = snapshot_inventory(
                Path(left), reuse_root / "images.json"
            )
            reuse_csv = reuse_root / "geometry.csv"
            try:
                shutil.copy2(Path(right), reuse_csv, follow_symlinks=True)
            except OSError as exc:
                raise RouteSearchError(
                    f"cannot snapshot geometry cache {right}: {exc}"
                ) from exc
            for key, row in migrate_csv(reuse_inventory, reuse_csv).items():
                old = cache.get(key)
                if old is not None and any(
                    str(old.get(field, "")) != str(row.get(field, ""))
                    for field in comparable
                ):
                    raise RouteSearchError(f"conflicting reuse inputs for {key}")
                if old is not None and transport_stats_complete(old):
                    if transport_stats_complete(row) and any(
                        str(old.get(field, "")) != str(row.get(field, ""))
                        for field in TRANSPORT_FIELDS
                    ):
                        raise RouteSearchError(
                            f"conflicting reuse transport statistics for {key}"
                        )
                    if not transport_stats_complete(row):
                        row.update({field: old[field] for field in TRANSPORT_FIELDS})
                cache[key] = row

        jobs = all_jobs(images)
        required = {cache_key(job[5], job[6], job[2]) for job in jobs}
        geometry_remaining = [
            job for job in jobs
            if not cache_satisfies_job(
                cache.get(cache_key(job[5], job[6], job[2])),
                False,
                expected_block_size=job[7],
            )
        ]
        print(
            f"nodes={len(images)} geometries={len(jobs)} "
            f"reused={len(required & cache.keys())} "
            f"geometry_remaining={len(geometry_remaining)}"
        )
        if geometry_remaining and not args.no_generate:
            # Patch payloads are disposable worker scratch. Keep them in a
            # uniquely owned directory so cleanup can never remove a caller's
            # pre-existing `work-dir/patches` tree.
            with tempfile.TemporaryDirectory(
                prefix=".route-patches-", dir=args.work_dir
            ) as patches_raw:
                patches = Path(patches_raw)
                with ProcessPoolExecutor(max_workers=args.workers) as pool:
                    futures = [
                        pool.submit(
                            geometry_job, (*job, str(patches), "")
                        )
                        for job in geometry_remaining
                    ]
                    for done, future in enumerate(as_completed(futures), 1):
                        row = future.result()
                        key = cache_key(
                            str(row["source_sha256"]),
                            str(row["target_sha256"]),
                            int(row["memory"]),
                        )
                        cache[key] = row
                        if done % 100 == 0 or done == len(futures):
                            print(
                                f"geometry_measured={done}/{len(geometry_remaining)}",
                                flush=True,
                            )
        geometry_completed = {
            cache_key(job[5], job[6], job[2])
            for job in jobs
            if cache_satisfies_job(
                cache.get(cache_key(job[5], job[6], job[2])),
                False,
                expected_block_size=job[7],
            )
        }
        rows = project_cache(cache, images, geometry_completed)
        write_csv(args.work_dir / "geometry.csv", rows)
        geometry_complete = geometry_completed == required

        complete = geometry_complete
        if args.objective == "transport":
            if not geometry_complete:
                raise RouteSearchError(
                    "transport objective requires complete geometry before "
                    "shortest-DAG sizing"
                )
            relevant_pairs = minimum_hop_pairs(rows, images)
            transport_remaining = [
                job for job in jobs
                if not cache_satisfies_job(
                    cache.get(cache_key(job[5], job[6], job[2])),
                    job_needs_transport_stats(
                        job, images, args.objective, relevant_pairs
                    ),
                    frozen_transport_tool_sha256,
                    job[7],
                )
            ]
            print(
                f"shortest_dag_pairs={len(relevant_pairs)} "
                f"transport_remaining={len(transport_remaining)}"
            )
            if transport_remaining and args.no_generate:
                raise RouteSearchError(
                    "shortest-DAG transport statistics are incomplete"
                )
            if transport_remaining and frozen_transport_tool is None:
                parser.error(
                    "--motatool is required to measure shortest-DAG transport costs"
                )
            if transport_remaining:
                with tempfile.TemporaryDirectory(
                    prefix=".transport-patches-", dir=args.work_dir
                ) as patches_raw:
                    with ProcessPoolExecutor(max_workers=args.workers) as pool:
                        futures = [
                            pool.submit(
                                geometry_job,
                                (*job, patches_raw, str(frozen_transport_tool)),
                            )
                            for job in transport_remaining
                        ]
                        for done, future in enumerate(as_completed(futures), 1):
                            row = future.result()
                            key = cache_key(
                                str(row["source_sha256"]),
                                str(row["target_sha256"]),
                                int(row["memory"]),
                            )
                            old = cache.get(key)
                            if old is None or any(
                                str(old.get(field, "")) != str(row.get(field, ""))
                                for field in comparable
                            ):
                                raise RouteSearchError(
                                    f"transport regeneration changed geometry for {key}"
                                )
                            cache[key] = row
                            if done % 100 == 0 or done == len(futures):
                                print(
                                    f"transport_measured={done}/"
                                    f"{len(transport_remaining)}",
                                    flush=True,
                                )
                rows = project_cache(cache, images, geometry_completed)
                write_csv(args.work_dir / "geometry.csv", rows)
            complete = all(
                cache_satisfies_job(
                    cache.get(cache_key(job[5], job[6], job[2])),
                    job_needs_transport_stats(
                        job, images, args.objective, relevant_pairs
                    ),
                    frozen_transport_tool_sha256,
                    job[7],
                )
                for job in jobs
            )
        result = select_route(
            rows, images, baseline_size, args.work_dir / "route.json",
            complete=complete,
            objective=args.objective, relay_hops=args.relay_hops,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
