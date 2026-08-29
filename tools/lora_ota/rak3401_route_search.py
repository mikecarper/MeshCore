#!/usr/bin/env python3
"""Exhaustively select a compact legacy-ceiling RAK3401 mOTA route.

Cache entries are keyed by source SHA, target SHA, and workspace size rather
than inventory indices.  A cache generated for an older inventory can therefore
be migrated safely when new candidate images are inserted.
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
FIELDS = [
    "source", "target", "source_sha256", "target_sha256", "memory",
    "payload", "container", "stage_start", "margin", "feasible", "error",
]


class RouteSearchError(RuntimeError):
    """An invalid input or internally inconsistent cache."""


def align_up(value: int, unit: int = PAGE) -> int:
    return (value + unit - 1) // unit * unit


def align_down(value: int, unit: int = PAGE) -> int:
    return value // unit * unit


def container_size(payload_size: int) -> int:
    return payload_size + 210 + 4 * ((payload_size + 1023) // 1024)


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

    result: list[dict[str, object]] = []
    seen: set[str] = set()
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


def migrate_csv(
    inventory_path: Path, csv_path: Path
) -> dict[tuple[str, str, int], dict[str, object]]:
    """Migrate numeric CSV indices through the inventory that created them."""
    images = load_inventory(inventory_path)
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
            source_node = _integer(row["source"], f"source at {csv_path}:{line}")
            target_node = _integer(row["target"], f"target at {csv_path}:{line}")
            if not (0 <= source_node < target_node < len(images)):
                raise RouteSearchError(f"bad nodes at {csv_path}:{line}")
            source_sha = str(images[source_node]["sha256"])
            target_sha = str(images[target_node]["sha256"])
            memory = _integer(row["memory"], f"memory at {csv_path}:{line}")
            for column, expected in (
                ("source_sha256", source_sha), ("target_sha256", target_sha)
            ):
                raw_recorded = row.get(column, "")
                if raw_recorded is None:
                    raw_recorded = ""
                if not isinstance(raw_recorded, str):
                    raise RouteSearchError(
                        f"{column} is invalid at {csv_path}:{line}"
                    )
                recorded = raw_recorded.lower()
                if recorded and recorded != expected:
                    raise RouteSearchError(
                        f"{column} disagrees with inventory at {csv_path}:{line}"
                    )
            try:
                payload = int(row.get("payload", ""))
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
            expected_total = container_size(payload)
            expected_stage = align_down(STAGE_CEILING - expected_total)
            expected_margin = expected_stage - (APP_BASE + memory)
            if (total, stage_start, margin) != (
                expected_total, expected_stage, expected_margin
            ):
                raise RouteSearchError(
                    f"inconsistent cached geometry at {csv_path}:{line}"
                )
            if truth(row.get("feasible", False)) != (payload >= 0 and margin >= 0):
                raise RouteSearchError(
                    f"cached feasibility is inconsistent at {csv_path}:{line}"
                )
            key = cache_key(source_sha, target_sha, memory)
            normalized = {field: row.get(field, "") for field in FIELDS}
            normalized.update(source=source_node, target=target_node,
                              source_sha256=source_sha, target_sha256=target_sha,
                              memory=memory)
            previous = migrated.get(key)
            comparable = ("payload", "container", "stage_start", "margin", "feasible", "error")
            if previous is not None and any(
                str(previous.get(item, "")) != str(normalized.get(item, ""))
                for item in comparable
            ):
                raise RouteSearchError(f"conflicting cached geometry for {key}")
            migrated[key] = normalized
    return migrated


Job = tuple[int, int, int, str, str, str, str]


def all_jobs(images: list[dict[str, object]]) -> list[Job]:
    """Enumerate every valid page-aligned workspace for every forward edge."""
    jobs: list[Job] = []
    endpoint = len(images) - 1
    # Node 0 may only use the byte-identical, physically qualified 0->1 package.
    # Node 1 still runs the fixed-workspace receiver, so measure each of its
    # possible forward transitions at exactly that legacy workspace.
    for target in range(2, endpoint + 1):
        jobs.append((1, target, FIXED_MEMORY, str(images[1]["path"]),
                     str(images[target]["path"]), str(images[1]["sha256"]),
                     str(images[target]["sha256"])))
    for source in range(2, endpoint):
        source_size = int(images[source]["size"])
        for target in range(source + 1, endpoint + 1):
            target_size = int(images[target]["size"])
            first_page = align_up(max(source_size + 2 * PAGE, target_size)) // PAGE
            for memory_page in range(first_page, AVAILABLE_PAGES):
                jobs.append((source, target, memory_page * PAGE,
                             str(images[source]["path"]), str(images[target]["path"]),
                             str(images[source]["sha256"]), str(images[target]["sha256"])))
    return jobs


def geometry_job(args: tuple[int, int, int, str, str, str, str, str]) -> dict[str, object]:
    try:
        import detools
    except ImportError as exc:
        raise RouteSearchError(
            "detools is required to generate geometry; run this tool in the "
            "detools pipx environment"
        ) from exc
    source, target, memory, from_raw, to_raw, source_sha, target_sha, patches_raw = args
    patch = Path(patches_raw) / f"{source_sha}-{target_sha}-{memory // PAGE:03d}.patch"
    try:
        detools.create_patch_filenames(
            from_raw, to_raw, str(patch), compression="crle", patch_type="in-place",
            algorithm="bsdiff", suffix_array_algorithm="divsufsort",
            memory_size=memory, segment_size=SEGMENT, use_mmap=True,
        )
        payload = patch.stat().st_size
        total = container_size(payload)
        stage_start = align_down(STAGE_CEILING - total)
        margin = stage_start - (APP_BASE + memory)
        error = ""
    except Exception as exc:  # detools reports several backend exception types
        detail = f"{type(exc).__name__}: {exc}"[-500:].replace("\n", " ")
        raise RouteSearchError(
            f"detools geometry failed for {source}->{target} at 0x{memory:X}: {detail}"
        ) from exc
    finally:
        patch.unlink(missing_ok=True)
    return {"source": source, "target": target, "source_sha256": source_sha,
            "target_sha256": target_sha, "memory": memory, "payload": payload,
            "container": total, "stage_start": stage_start, "margin": margin,
            "feasible": payload >= 0 and margin >= 0, "error": error}


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


def select_route(rows: list[dict[str, object]], images: list[dict[str, object]],
                 baseline_size: int, output: Path, complete: bool) -> dict[str, object]:
    endpoint = len(images) - 1
    baseline_stage = align_down(STAGE_CEILING - baseline_size)
    baseline_margin = baseline_stage - (APP_BASE + FIXED_MEMORY)
    if baseline_margin < 0:
        raise RouteSearchError("pinned first package does not fit")
    edges: dict[tuple[int, int], dict[str, object]] = {
        (0, 1): {"source": 0, "target": 1, "memory": FIXED_MEMORY,
                 "container": baseline_size, "stage_start": baseline_stage,
                 "margin": baseline_margin, "feasible": True}
    }
    for row in rows:
        if int(row["source"]) == 0:
            # Older evidence tables measured these irrelevant shortcuts. Keep
            # them countable for archive verification, but never admit them to
            # the route graph; node 0 is pinned unconditionally to node 1.
            continue
        if not truth(row.get("feasible", False)):
            continue
        key = int(row["source"]), int(row["target"])
        rank = (int(row["container"]), -int(row["margin"]), int(row["memory"]))
        old = edges.get(key)
        if old is None or rank < (int(old["container"]), -int(old["margin"]), int(old["memory"])):
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
            candidate = (best[source][0] + 1,
                         best[source][1] + int(row["container"]),
                         best[source][2] + [target])
            if target not in best or candidate < best[target]:
                best[target] = candidate
            distance = hop_distance[source] + 1
            if distance < hop_distance[target]:
                hop_distance[target], route_count[target] = distance, route_count[source]
            elif distance == hop_distance[target]:
                route_count[target] += route_count[source]

    common = {"schema": 2, "app_base": f"0x{APP_BASE:X}",
              "stage_ceiling": f"0x{STAGE_CEILING:X}", "node_count": len(images),
              "search_complete": complete, "candidate_geometries": len(rows),
              "feasible_edges": len(edges),
              "objective": "minimum packages, then minimum total container bytes"}
    if endpoint not in best:
        result = {**common, "status": "unreachable", "reachable_nodes": sorted(best),
                  "endpoint_node": endpoint,
                  "endpoint_incoming_feasible": sorted(
                      source for source, target in edges if target == endpoint
                  )}
    else:
        nodes = best[endpoint][2]
        steps = []
        for source, target in zip(nodes, nodes[1:]):
            row = edges[source, target]
            steps.append({"source_node": source, "target_node": target,
                          "inplace_memory": f"0x{int(row['memory']):X}",
                          "reuse_baseline_package": source == 0 and target == 1,
                          "expected_container_size": int(row["container"]),
                          "expected_staging_margin": int(row["margin"]),
                          "expected_target_sha256": images[target]["sha256"],
                          "expected_target_version": images[target].get("version", "unknown")})
        result = {**common, "status": "reachable", "nodes": nodes,
                  "shortest_package_count": hop_distance[endpoint],
                  "shortest_route_count": route_count[endpoint],
                  "selected_total_bytes": best[endpoint][1], "steps": steps}
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
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".frozen-inventory-", dir=args.work_dir
    ) as frozen_raw:
        frozen = Path(frozen_raw)
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
            "payload", "container", "stage_start", "margin", "feasible", "error"
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
                cache[key] = row

        jobs = all_jobs(images)
        required = {cache_key(job[5], job[6], job[2]) for job in jobs}
        remaining = [
            job for job in jobs
            if cache_key(job[5], job[6], job[2]) not in cache
        ]
        print(
            f"nodes={len(images)} geometries={len(jobs)} "
            f"reused={len(required & cache.keys())} remaining={len(remaining)}"
        )
        if remaining and not args.no_generate:
            # Patch payloads are disposable worker scratch. Keep them in a
            # uniquely owned directory so cleanup can never remove a caller's
            # pre-existing `work-dir/patches` tree.
            with tempfile.TemporaryDirectory(
                prefix=".route-patches-", dir=args.work_dir
            ) as patches_raw:
                patches = Path(patches_raw)
                with ProcessPoolExecutor(max_workers=args.workers) as pool:
                    futures = [
                        pool.submit(geometry_job, (*job, str(patches)))
                        for job in remaining
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
                            print(f"measured={done}/{len(remaining)}", flush=True)
        completed = required & cache.keys()
        rows = project_cache(cache, images, completed)
        write_csv(args.work_dir / "geometry.csv", rows)
        complete = completed == required
        result = select_route(
            rows, images, baseline_size, args.work_dir / "route.json",
            complete=complete,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
