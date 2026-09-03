from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rak3401_route_search as search


def firmware(body: bytes, version: str) -> bytes:
    hardware = search.EXPECTED_HARDWARE.encode("ascii").ljust(32, b"\0")
    return (
        body
        + b"EndF"
        + struct.pack("<I", len(body))
        + hashlib.sha256(body).digest()[:8]
        + struct.pack(
            "<II", search.motalib.pack_version(version), search.EXPECTED_TARGET_ID
        )
        + hardware
    )


class RouteSearchTests(unittest.TestCase):
    def write_inventory(
        self, root: Path, payloads: list[bytes], *, baseline_size: int = 100,
        versions: list[str] | None = None,
        transport_capabilities: list[bool] | None = None,
        transport_block_sizes: list[int] | None = None,
        transport_pipelines: list[int | None] | None = None,
        version_ranks: list[int] | None = None,
    ) -> tuple[Path, list[dict[str, object]]]:
        records = []
        for node, payload in enumerate(payloads):
            version = versions[node] if versions is not None else f"1.0.0.{node}"
            encoded = firmware(payload, version)
            image = root / f"image-{node:02d}.bin"
            image.write_bytes(encoded)
            record: dict[str, object] = {
                "node": node,
                "path": image.name,
                "size": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "body_hash": hashlib.sha256(payload).digest()[:8].hex(),
                "version": version,
                "version_rank": (
                    version_ranks[node] if version_ranks is not None else node
                ),
            }
            if node == 1:
                record["baseline_container_size"] = baseline_size
            capability = (
                transport_capabilities[node]
                if transport_capabilities is not None else False
            )
            record[search.TRANSPORT_CAPABILITY] = capability
            record[search.TRANSPORT_MAX_BLOCK_FIELD] = (
                transport_block_sizes[node]
                if transport_block_sizes is not None
                else (
                    search.DEFLATE_BLOCK_SIZE
                    if capability else search.LEGACY_BLOCK_SIZE
                )
            )
            record[search.TRANSPORT_PIPELINE_FIELD] = (
                transport_pipelines[node]
                if transport_pipelines is not None else None
            )
            records.append(record)
        manifest = root / "images.json"
        manifest.write_text(json.dumps({
            "capability_contract": {
                search.TRANSPORT_CAPABILITY: "required boolean on every record",
                search.TRANSPORT_MAX_BLOCK_FIELD: (
                    "required signed-container block size on every record; "
                    "independent of ota_transport_deflate"
                ),
            },
            "edge_policy": {
                "require_target_version_rank_gt_source_version_rank": True,
            },
            "images": records,
        }), encoding="ascii")
        return manifest, records

    def test_cache_migration_survives_inserted_node_and_remaps_indices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_dir, new_dir = root / "old", root / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            old_manifest, old = self.write_inventory(
                old_dir, [b"A", b"B", b"C"],
                versions=["1.0.0.0", "1.0.0.1", "1.0.0.2"],
            )
            cache = old_dir / "geometry.csv"
            with cache.open("w", newline="", encoding="ascii") as output:
                writer = csv.DictWriter(output, fieldnames=search.FIELDS)
                writer.writeheader()
                writer.writerow({
                    "source": 1, "target": 2, "memory": search.FIXED_MEMORY,
                    "source_sha256": old[1]["sha256"],
                    "target_sha256": old[2]["sha256"],
                    "payload": 7,
                    "container": search.container_size(7),
                    "stage_start": search.align_down(
                        search.STAGE_CEILING - search.container_size(7)
                    ),
                    "margin": search.align_down(
                        search.STAGE_CEILING - search.container_size(7)
                    ) - (search.APP_BASE + search.FIXED_MEMORY),
                    "feasible": True, "error": "",
                })
            new_manifest, _ = self.write_inventory(
                new_dir, [b"A", b"X", b"B", b"C"],
                versions=["1.0.0.0", "1.0.0.99", "1.0.0.1", "1.0.0.2"],
            )
            # The recorded numeric nodes no longer identify these images in
            # the expanded inventory; authenticated hashes still do.
            migrated = search.migrate_csv(new_manifest, cache)
            key = search.cache_key(str(old[1]["sha256"]), str(old[2]["sha256"]),
                                   search.FIXED_MEMORY)
            self.assertIn(key, migrated)
            projected = search.project_cache(
                migrated, search.load_inventory(new_manifest), {key}
            )
            self.assertEqual((projected[0]["source"], projected[0]["target"]), (2, 3))

    def test_cache_migration_reuses_payload_but_recomputes_2k_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, records = self.write_inventory(
                root,
                [b"start", b"capable", b"endpoint"],
                transport_capabilities=[False, True, False],
                transport_pipelines=[None, 1, None],
            )
            payload = 3000
            old_total = search.container_size(payload, search.LEGACY_BLOCK_SIZE)
            old_stage = search.align_down(search.STAGE_CEILING - old_total)
            cache = root / "old-1k.csv"
            with cache.open("w", newline="", encoding="ascii") as output:
                writer = csv.DictWriter(output, fieldnames=search.FIELDS)
                writer.writeheader()
                writer.writerow({
                    "source": 1,
                    "target": 2,
                    "source_sha256": records[1]["sha256"],
                    "target_sha256": records[2]["sha256"],
                    "memory": search.FIXED_MEMORY,
                    "payload": payload,
                    # Blank means a pre-per-step cache with 1 KiB geometry.
                    "block_size": "",
                    "container": old_total,
                    "stage_start": old_stage,
                    "margin": old_stage - (search.APP_BASE + search.FIXED_MEMORY),
                    "feasible": True,
                    "error": "",
                })

            migrated = search.migrate_csv(manifest, cache)
            row = next(iter(migrated.values()))

        self.assertEqual(row["payload"], payload)
        self.assertEqual(row["block_size"], search.DEFLATE_BLOCK_SIZE)
        self.assertEqual(
            row["container"],
            search.container_size(payload, search.DEFLATE_BLOCK_SIZE),
        )
        self.assertFalse(search.transport_stats_complete(row))

    def test_all_jobs_covers_every_valid_page_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, _ = self.write_inventory(
                Path(directory), [b"a", b"b", b"c" * 10000, b"d" * 12000]
            )
            jobs = search.all_jobs(search.load_inventory(manifest))
            fixed = [job for job in jobs if job[0] < 2]
            dynamic = [job for job in jobs if job[0] >= 2]
            self.assertEqual(len(fixed), 2)
            self.assertTrue(all(job[2] == search.FIXED_MEMORY for job in fixed))
            self.assertTrue(all(job[0] == 1 for job in fixed))
            inventory = search.load_inventory(manifest)
            first_page = search.align_up(
                max(
                    int(inventory[2]["size"]) + 2 * search.PAGE,
                    int(inventory[3]["size"]),
                )
            ) // search.PAGE
            self.assertEqual(
                [job[2] for job in dynamic],
                [page * search.PAGE for page in range(first_page, search.AVAILABLE_PAGES)],
            )
            self.assertEqual(len(jobs), 2 + search.AVAILABLE_PAGES - first_page)

    def test_route_ignores_geometry_that_bypasses_pinned_first_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.write_inventory(root, [b"0", b"1", b"2"])
            images = search.load_inventory(manifest)
            result = search.select_route(
                [
                    {
                        "source": 0, "target": 2, "memory": search.FIXED_MEMORY,
                        "container": 1, "stage_start": 0xC0000,
                        "margin": 10, "feasible": True,
                    },
                    {
                        "source": 1, "target": 2, "memory": search.FIXED_MEMORY,
                        "container": 10, "stage_start": 0xC0000,
                        "margin": 10, "feasible": True,
                    },
                ],
                images, 100, root / "route.json", True,
            )
            self.assertEqual(result["nodes"], [0, 1, 2])

    def test_cache_migration_rejects_inconsistent_numeric_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.write_inventory(root, [b"A", b"B", b"C"])
            cache = root / "geometry.csv"
            with cache.open("w", newline="", encoding="ascii") as output:
                writer = csv.DictWriter(output, fieldnames=search.FIELDS)
                writer.writeheader()
                writer.writerow({
                    "source": 1, "target": 2, "memory": search.FIXED_MEMORY,
                    "payload": 7, "container": 999, "stage_start": 800000,
                    "margin": 1, "feasible": True, "error": "",
                })
            with self.assertRaisesRegex(
                search.RouteSearchError, "inconsistent cached geometry"
            ):
                search.migrate_csv(manifest, cache)

    def test_cache_migration_rejects_failed_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.write_inventory(root, [b"A", b"B", b"C"])
            cache = root / "geometry.csv"
            with cache.open("w", newline="", encoding="ascii") as output:
                writer = csv.DictWriter(output, fieldnames=search.FIELDS)
                writer.writeheader()
                writer.writerow({
                    "source": 1, "target": 2, "memory": search.FIXED_MEMORY,
                    "payload": -1, "container": -1, "stage_start": -1,
                    "margin": -1, "feasible": False, "error": "out of memory",
                })
            with self.assertRaisesRegex(
                search.RouteSearchError, "cannot prove an exhaustive search"
            ):
                search.migrate_csv(manifest, cache)

    def test_geometry_tool_error_aborts_instead_of_becoming_infeasible(self) -> None:
        class BrokenDetools:
            @staticmethod
            def create_patch_filenames(*_args: object, **_kwargs: object) -> None:
                raise OSError("disk full")

        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            sys.modules, {"detools": BrokenDetools}
        ):
            with self.assertRaisesRegex(search.RouteSearchError, "disk full"):
                search.geometry_job((
                    1, 2, search.FIXED_MEMORY,
                    str(Path(directory) / "from.bin"),
                    str(Path(directory) / "to.bin"),
                    "1" * 64, "2" * 64, search.LEGACY_BLOCK_SIZE, directory,
                ))

    def test_snapshot_inventory_is_immune_to_later_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            manifest, _ = self.write_inventory(source, [b"A", b"B", b"C"])
            frozen = search.snapshot_inventory(
                manifest, root / "frozen" / "images.json"
            )
            source_image = source / "image-02.bin"
            source_image.write_bytes(b"changed")
            images = search.load_inventory(frozen)
            self.assertNotEqual(Path(images[2]["path"]).read_bytes(), b"changed")

    def test_generation_never_removes_a_preexisting_patches_directory(self) -> None:
        class ImmediateFuture:
            def __init__(self, value: dict[str, object]):
                self._value = value

            def result(self) -> dict[str, object]:
                return self._value

        class ImmediatePool:
            def __init__(self, **_kwargs: object):
                pass

            def __enter__(self) -> "ImmediatePool":
                return self

            def __exit__(self, *_args: object) -> None:
                pass

            def submit(self, function: object, *args: object) -> ImmediateFuture:
                return ImmediateFuture(function(*args))  # type: ignore[operator]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_root = root / "inventory"
            inventory_root.mkdir()
            manifest, records = self.write_inventory(
                inventory_root, [b"start", b"bridge", b"endpoint"]
            )
            work = root / "work"
            existing = work / "patches"
            existing.mkdir(parents=True)
            sentinel = existing / "keep.txt"
            sentinel.write_text("caller-owned", encoding="ascii")
            source_sha = str(records[1]["sha256"])
            target_sha = str(records[2]["sha256"])
            job = (
                1, 2, search.FIXED_MEMORY,
                str(inventory_root / str(records[1]["path"])),
                str(inventory_root / str(records[2]["path"])),
                source_sha, target_sha, search.LEGACY_BLOCK_SIZE,
            )

            def measured(args: tuple[object, ...]) -> dict[str, object]:
                source, target, memory, *_rest = args
                payload = 7
                total = search.container_size(payload)
                stage = search.align_down(search.STAGE_CEILING - total)
                return {
                    "source": source, "target": target,
                    "source_sha256": source_sha,
                    "target_sha256": target_sha,
                    "memory": memory, "payload": payload,
                    "block_size": search.LEGACY_BLOCK_SIZE,
                    "container": total, "stage_start": stage,
                    "margin": stage - (search.APP_BASE + int(memory)),
                    "feasible": True, "error": "",
                }

            with (
                mock.patch.object(search, "all_jobs", return_value=[job]),
                mock.patch.object(search, "geometry_job", side_effect=measured),
                mock.patch.object(search, "ProcessPoolExecutor", ImmediatePool),
                mock.patch.object(search, "as_completed", side_effect=lambda values: values),
            ):
                self.assertEqual(
                    search.main([
                        "--inventory", str(manifest),
                        "--work-dir", str(work),
                        "--workers", "1",
                    ]),
                    0,
                )
            self.assertEqual(sentinel.read_text(encoding="ascii"), "caller-owned")

    def test_route_minimizes_hops_then_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.write_inventory(root, [b"0", b"1", b"2", b"3", b"4"])
            images = search.load_inventory(manifest)

            def edge(source: int, target: int, size: int) -> dict[str, object]:
                return {
                    "source": source, "target": target, "memory": 0x1000,
                    "container": size, "stage_start": 0xC0000,
                    "margin": 100, "feasible": True,
                }

            # Both endpoint routes have three hops including the pinned 0->1.
            # The path through node 3 is smaller and must win even though it is
            # encountered after the path through node 2.
            rows = [edge(1, 2, 100), edge(2, 4, 100),
                    edge(1, 3, 40), edge(3, 4, 50)]
            result = search.select_route(rows, images, 100, root / "route.json", True)
            self.assertEqual(result["nodes"], [0, 1, 3, 4])
            self.assertEqual(result["shortest_package_count"], 3)
            self.assertEqual(result["shortest_route_count"], 2)
            self.assertEqual(result["selected_total_bytes"], 190)

            # A direct 1->4 edge wins on hop count even when it costs more.
            rows.append(edge(1, 4, 1000))
            result = search.select_route(rows, images, 100, root / "route2.json", True)
            self.assertEqual(result["nodes"], [0, 1, 4])
            self.assertEqual(result["selected_total_bytes"], 1100)

    def test_same_version_variants_are_never_progress_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.write_inventory(
                root, [b"0", b"1", b"2-raw", b"2-inflate", b"3"],
                versions=[
                    "1.0.0.0", "1.0.0.1", "1.0.0.2", "1.0.0.2", "1.0.0.3",
                ],
                version_ranks=[0, 1, 2, 2, 3],
            )
            images = search.load_inventory(manifest)
            jobs = search.all_jobs(images)
            self.assertFalse(any(job[0] == 2 and job[1] == 3 for job in jobs))

            def edge(source: int, target: int) -> dict[str, object]:
                return {
                    "source": source, "target": target, "memory": 0x1000,
                    "container": 100, "stage_start": 0xC0000,
                    "margin": 100, "feasible": True,
                }

            # The only apparent path depends on changing variants without
            # advancing the running version. The route must remain unreachable.
            result = search.select_route(
                [edge(1, 2), edge(2, 3), edge(3, 4)], images, 100,
                root / "route.json", True,
            )
            self.assertEqual(result["status"], "unreachable")
            self.assertNotIn(3, result["reachable_nodes"])

    def test_transport_cost_accounts_for_every_clean_wire_message(self) -> None:
        legacy = search.legacy_transport_cost(1024)
        self.assertEqual(legacy["data_packets"], 7)
        self.assertEqual(legacy["packets"], 13)
        self.assertEqual(legacy["origin_mesh_bytes"], 1355)
        self.assertEqual(legacy["manifest_bytes"], 224)
        self.assertEqual(
            legacy["origin_mesh_bytes"],
            sum(
                int(legacy[field])
                for field in (
                    "manifest_bytes", "data_bytes", "block_request_bytes",
                    "proof_request_bytes", "proof_response_bytes",
                )
            ),
        )

        raw_v2 = search.v2_transport_cost(1024, 1024, 0, 0, 6)
        self.assertEqual(raw_v2["data_packets"], 6)
        self.assertEqual(raw_v2["packets"], 11)
        self.assertEqual(raw_v2["origin_mesh_bytes"], 1359)
        self.assertEqual(raw_v2["manifest_bytes"], 224)

        compressed = search.v2_transport_cost(1024, 800, 800, 1, 5)
        self.assertEqual(compressed["data_bytes"], 875)
        self.assertEqual(compressed["origin_mesh_bytes"], 1120)
        self.assertEqual(
            search.linear_path_bytes(1120, int(compressed["packets"]), 2),
            3 * 1120 + 3 * int(compressed["packets"]),
        )

        # The receive-inflate compatibility bridges are compiled with a single
        # block in flight. Account for their five individual request frames,
        # rather than the full RAK3401 receiver's clean [1, 2, 2] flights.
        full = search.v2_transport_cost(
            5 * 1024, 5 * 1024, 0, 0, 30, 4,
            search.LEGACY_BLOCK_SIZE,
        )
        bridge = search.v2_transport_cost(
            5 * 1024, 5 * 1024, 0, 0, 30, 1,
            search.LEGACY_BLOCK_SIZE,
        )
        self.assertEqual(full["request_packets"], 3)
        self.assertEqual(full["request_bytes"], 41)
        self.assertEqual(bridge["request_packets"], 5)
        self.assertEqual(bridge["request_bytes"], 55)
        self.assertEqual(
            int(bridge["origin_mesh_bytes"]) - int(full["origin_mesh_bytes"]), 14
        )

    def test_deflate_geometry_uses_2k_blocks_and_raw_baseline_uses_1k(self) -> None:
        payload = 4096
        self.assertEqual(
            search.container_size(payload, search.LEGACY_BLOCK_SIZE),
            payload + 210 + 4 * 4,
        )
        self.assertEqual(
            search.container_size(payload, search.DEFLATE_BLOCK_SIZE),
            payload + 210 + 4 * 2,
        )
        selected = search.v2_transport_cost(
            payload, 2000, 2000, 2, 12, 1
        )
        baseline = search.v2_raw_transport_cost(payload, 1)
        self.assertEqual(selected["block_size"], search.DEFLATE_BLOCK_SIZE)
        self.assertEqual(selected["proof_packets"], 2)
        self.assertEqual(baseline["profile"], "v2-171-raw")
        self.assertEqual(baseline["block_size"], search.LEGACY_BLOCK_SIZE)
        self.assertEqual(baseline["proof_packets"], 4)
        self.assertGreater(baseline["packets"], selected["packets"])

    def test_published_legacy_chain_and_new_bootstrap_cost_regressions(self) -> None:
        old_payloads = [
            89282, 45974, 105407, 149133, 110666,
            119942, 138883, 173312, 270303, 173854,
        ]
        old = [search.legacy_transport_cost(size) for size in old_payloads]
        self.assertEqual(sum(old_payloads), 1_376_756)
        self.assertEqual(sum(int(cost["packets"]) for cost in old), 13_490)
        self.assertEqual(
            sum(int(cost["origin_mesh_bytes"]) for cost in old), 1_563_957
        )

        new_bootstrap = search.legacy_transport_cost(64_088)
        self.assertEqual(new_bootstrap["data_packets"], 438)
        self.assertEqual(new_bootstrap["packets"], 630)
        self.assertEqual(new_bootstrap["origin_mesh_bytes"], 72_528)
        with self.assertRaisesRegex(search.RouteSearchError, "between 0 and 8"):
            search.linear_path_bytes(100, 1, 9)

    def test_current_route_2k_and_1k_raw_projection_regressions(self) -> None:
        # Exact current-route detools payloads and pinned motatool 2 KiB
        # measurements. Steps 1, 2, and 10 remain legacy; steps 3-9 negotiate
        # v2 and use a one-block request pipeline.
        legacy_payloads = [89_282, 64_756, 196_843]
        v2_measurements = [
            (104_543, 60_379, 60_284, 51, 381),
            (148_997, 92_549, 92_549, 73, 577),
            (112_123, 59_704, 59_704, 55, 375),
            (121_934, 63_977, 63_977, 60, 405),
            (135_068, 87_425, 87_425, 66, 547),
            (176_140, 130_264, 130_252, 86, 807),
            (271_013, 210_192, 210_192, 133, 1297),
        ]
        selected = [
            search.legacy_transport_cost(payload) for payload in legacy_payloads
        ] + [
            search.v2_transport_cost(*measurement, 1, search.DEFLATE_BLOCK_SIZE)
            for measurement in v2_measurements
        ]
        baseline = [
            search.legacy_transport_cost(payload) for payload in legacy_payloads
        ] + [
            search.v2_raw_transport_cost(
                measurement[0], 1, search.LEGACY_BLOCK_SIZE
            )
            for measurement in v2_measurements
        ]

        self.assertEqual(
            (
                sum(int(cost["origin_mesh_bytes"]) for cost in selected),
                sum(int(cost["packets"]) for cost in selected),
            ),
            (1_195_442, 8_906),
        )
        self.assertEqual(
            (
                sum(int(cost["origin_mesh_bytes"]) for cost in baseline),
                sum(int(cost["packets"]) for cost in baseline),
            ),
            (1_618_318, 11_835),
        )
        self.assertEqual(
            (
                1_618_318 - 1_195_442,
                11_835 - 8_906,
            ),
            (422_876, 2_929),
        )
        self.assertEqual(round(100 * 422_876 / 1_618_318, 2), 26.13)
        self.assertEqual(round(100 * 2_929 / 11_835, 2), 24.75)

    def test_proof_sizing_matches_promote_odd_merkle_implementation(self) -> None:
        for count in range(1, 33):
            leaves = [bytes([index]) * 4 for index in range(count)]
            for index in range(count):
                self.assertEqual(
                    search.proof_sibling_count(count, index),
                    len(search.motalib.merkle_proof(leaves, index)),
                )

    def test_transport_objective_uses_running_source_capability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = search.container_size(64)
            manifest, _ = self.write_inventory(
                root, [b"0", b"1", b"2", b"3", b"4"],
                baseline_size=baseline,
                transport_capabilities=[True, False, True, False, True],
                transport_pipelines=[4, None, 1, None, 4],
            )
            images = search.load_inventory(manifest)

            def edge(source: int, target: int, payload: int,
                     *, compressed: int | None = None) -> dict[str, object]:
                row: dict[str, object] = {
                    "source": source, "target": target, "memory": 0x1000,
                    "payload": payload, "container": search.container_size(payload),
                    "stage_start": 0xC0000, "margin": 100, "feasible": True,
                }
                if compressed is not None:
                    row.update({
                        "payload_sha256": "a" * 64,
                        "transport_encoder_sha256": "b" * 64,
                        "v2_wire_bytes": compressed,
                        "v2_deflate_bytes": compressed,
                        "v2_deflate_blocks": 1,
                        "v2_data_packets": (compressed + 170) // 171,
                    })
                return row

            rows = [
                edge(1, 2, 100), edge(1, 3, 100),
                edge(2, 4, 1000, compressed=50), edge(3, 4, 100),
            ]
            transport = search.select_route(
                rows, images, baseline, root / "transport.json", True,
                objective="transport",
            )
            self.assertEqual(transport["nodes"], [0, 1, 2, 4])
            # Node 0 is marked capable on purpose: bootstrap is nevertheless raw.
            self.assertEqual(
                transport["steps"][0]["transport"]["profile"], "legacy-160-raw"
            )
            # Target node 2's capability does not apply until node 2 is running.
            self.assertEqual(
                transport["steps"][1]["transport"]["profile"], "legacy-160-raw"
            )
            self.assertEqual(
                transport["steps"][2]["transport"]["profile"], "v2-171-deflate"
            )
            self.assertEqual(
                transport["steps"][2]["transport"]["request_pipeline"], 1
            )
            self.assertEqual(
                [step["block_size"] for step in transport["steps"]],
                [
                    search.LEGACY_BLOCK_SIZE,
                    search.LEGACY_BLOCK_SIZE,
                    search.DEFLATE_BLOCK_SIZE,
                ],
            )
            self.assertEqual(
                transport["transport_accounting"]["source_max_block_field"],
                search.TRANSPORT_MAX_BLOCK_FIELD,
            )
            self.assertEqual(
                transport["transport_accounting"]["source_profile_matrix"],
                search.TRANSPORT_PROFILE_MATRIX,
            )
            self.assertEqual(
                transport["selected_total_bytes"],
                sum(step["expected_container_size"] for step in transport["steps"]),
            )
            self.assertEqual(
                transport["selected_total_transport_bytes"],
                sum(
                    step["transport"]["linear_path_bytes"]
                    for step in transport["steps"]
                ),
            )

            container = search.select_route(
                rows, images, baseline, root / "container.json", True,
                objective="container",
            )
            self.assertEqual(container["nodes"], [0, 1, 3, 4])

    def test_block_size_and_deflate_capabilities_form_independent_matrix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.write_inventory(
                root, [b"bootstrap", b"legacy", b"deflate-1k", b"raw-2k", b"deflate-2k"],
                transport_capabilities=[False, False, True, False, True],
                transport_block_sizes=[
                    search.LEGACY_BLOCK_SIZE,
                    search.LEGACY_BLOCK_SIZE,
                    search.LEGACY_BLOCK_SIZE,
                    search.DEFLATE_BLOCK_SIZE,
                    search.DEFLATE_BLOCK_SIZE,
                ],
                transport_pipelines=[None, None, 1, 2, 4],
            )
            images = search.load_inventory(manifest)

            self.assertEqual(
                [search.source_block_size(images, node) for node in range(1, 5)],
                [1024, 1024, 2048, 2048],
            )
            self.assertEqual(
                [
                    search.source_transport_profile(images, node)
                    for node in range(1, 5)
                ],
                [
                    "legacy-160-raw",
                    "v2-171-deflate",
                    "v2-171-raw",
                    "v2-171-deflate",
                ],
            )
            jobs = [
                (
                    node, 9, search.FIXED_MEMORY, "from", "to",
                    "1" * 64, "2" * 64,
                    search.source_block_size(images, node),
                )
                for node in range(1, 5)
            ]
            self.assertEqual(
                [
                    search.job_needs_transport_stats(job, images, "transport")
                    for job in jobs
                ],
                [False, True, False, True],
            )

            def edge(source: int, include_measurement: bool) -> dict[str, object]:
                block_size = search.source_block_size(images, source)
                payload = 3000
                row: dict[str, object] = {
                    "source": source,
                    "target": 4,
                    "memory": 0x1000,
                    "payload": payload,
                    "block_size": block_size,
                    "container": search.container_size(payload, block_size),
                    "stage_start": 0xC0000,
                    "margin": 100,
                    "feasible": True,
                }
                if include_measurement:
                    row.update({
                        "payload_sha256": "a" * 64,
                        "transport_encoder_sha256": "b" * 64,
                        "v2_wire_bytes": 1500,
                        "v2_deflate_bytes": 1500,
                        "v2_deflate_blocks": (
                            payload + block_size - 1
                        ) // block_size,
                        "v2_data_packets": 9,
                    })
                return row

            self.assertEqual(
                search.edge_transport_cost(edge(1, False), images, 0)["profile"],
                "legacy-160-raw",
            )
            with self.assertRaisesRegex(search.RouteSearchError, "missing transport"):
                search.edge_transport_cost(edge(2, False), images, 0)
            self.assertEqual(
                search.edge_transport_cost(edge(2, True), images, 0)["profile"],
                "v2-171-deflate",
            )
            # Raw 2 KiB v2 accounting is derived and needs no encoder evidence.
            raw_2k = search.edge_transport_cost(edge(3, False), images, 0)
            self.assertEqual(raw_2k["profile"], "v2-171-raw")
            self.assertEqual(raw_2k["block_size"], search.DEFLATE_BLOCK_SIZE)
            self.assertEqual(
                search.edge_transport_cost(edge(4, True), images, 0)["profile"],
                "v2-171-deflate",
            )

    def test_raw_2k_route_requires_no_transport_encoder_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = search.container_size(64)
            manifest, _ = self.write_inventory(
                root, [b"bootstrap", b"raw-2k", b"endpoint"],
                baseline_size=baseline,
                transport_capabilities=[False, False, False],
                transport_block_sizes=[1024, 2048, 1024],
                transport_pipelines=[None, 2, None],
            )
            images = search.load_inventory(manifest)
            payload = 3000
            row = {
                "source": 1,
                "target": 2,
                "memory": search.FIXED_MEMORY,
                "payload": payload,
                "block_size": 2048,
                "container": search.container_size(payload, 2048),
                "stage_start": 0xC0000,
                "margin": 100,
                "feasible": True,
            }

            result = search.select_route(
                [row], images, baseline, root / "route.json", True,
                objective="transport",
            )

            self.assertEqual(
                result["steps"][1]["transport"]["profile"], "v2-171-raw"
            )
            self.assertNotIn("payload_sha256", result["steps"][1]["transport"])
            self.assertNotIn("encoder_sha256", result["steps"][1]["transport"])
            self.assertNotIn("encoder_sha256", result["transport_accounting"])

    def test_transport_image_without_explicit_block_capability_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self.write_inventory(
                root, [b"start", b"capable", b"endpoint"],
                transport_capabilities=[False, True, False],
                transport_pipelines=[None, 1, None],
            )
            document = json.loads(manifest.read_text(encoding="ascii"))
            del document["capability_contract"][search.TRANSPORT_MAX_BLOCK_FIELD]
            for image in document["images"]:
                del image[search.TRANSPORT_MAX_BLOCK_FIELD]
            manifest.write_text(json.dumps(document), encoding="ascii")

            with self.assertRaisesRegex(
                search.RouteSearchError,
                "transport_max_block_bytes is required by the inventory",
            ):
                search.load_inventory(manifest)

    def test_transport_stats_are_required_only_on_the_shortest_hop_dag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = search.container_size(64)
            manifest, _ = self.write_inventory(
                root, [b"0", b"1", b"2", b"3", b"4", b"5"],
                baseline_size=baseline,
                transport_capabilities=[False, False, True, False, False, False],
                transport_pipelines=[None, None, 1, None, None, None],
            )
            images = search.load_inventory(manifest)

            def edge(
                source: int, target: int, memory: int = 0x1000,
                *, transport: bool = False,
            ) -> dict[str, object]:
                row: dict[str, object] = {
                    "source": source, "target": target, "memory": memory,
                    "payload": 100, "container": search.container_size(100),
                    "stage_start": 0xC0000, "margin": 100, "feasible": True,
                }
                if transport:
                    row.update({
                        "payload_sha256": f"{memory:064x}",
                        "transport_encoder_sha256": "d" * 64,
                        "v2_wire_bytes": 50, "v2_deflate_bytes": 50,
                        "v2_deflate_blocks": 1, "v2_data_packets": 1,
                    })
                return row

            rows = [
                edge(1, 2), edge(2, 5, transport=True),
                edge(1, 3), edge(3, 5),
                # Feasible, but adding 2->3 makes a four-hop route. Its capable
                # source therefore does not need a transport measurement.
                edge(2, 3),
            ]
            expected_pairs = {(0, 1), (1, 2), (2, 5), (1, 3), (3, 5)}
            self.assertEqual(search.minimum_hop_pairs(rows, images), expected_pairs)
            result = search.select_route(
                rows, images, baseline, root / "route.json", True,
                objective="transport",
            )
            self.assertEqual(result["shortest_package_count"], 3)

            # Every feasible workspace for a relevant capable-source pair is
            # comparable. A blank second 2->5 workspace must fail closed.
            rows.append(edge(2, 5, memory=0x2000))
            with self.assertRaisesRegex(search.RouteSearchError, "missing transport"):
                search.select_route(
                    rows, images, baseline, root / "missing.json", True,
                    objective="transport",
                )

            with self.assertRaisesRegex(search.RouteSearchError, "complete geometry"):
                search.select_route(
                    rows[:-1], images, baseline, root / "partial.json", False,
                    objective="transport",
                )

    def test_transport_geometry_rank_includes_per_packet_stream_id_and_framing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = search.container_size(64)
            manifest, _ = self.write_inventory(
                root, [b"0", b"1", b"2"], baseline_size=baseline,
                transport_capabilities=[False, True, False],
                transport_pipelines=[None, 1, None],
            )
            images = search.load_inventory(manifest)

            def edge(memory: int, payload: int, wire: int, packets: int) -> dict[str, object]:
                return {
                    "source": 1, "target": 2, "memory": memory,
                    "payload": payload, "container": search.container_size(payload),
                    "stage_start": 0xC0000, "margin": 100, "feasible": True,
                    "payload_sha256": f"{memory:064x}",
                    "transport_encoder_sha256": "c" * 64,
                    "v2_wire_bytes": wire, "v2_deflate_bytes": wire,
                    "v2_deflate_blocks": 1, "v2_data_packets": packets,
                }

            # The 342-byte representation wins despite its larger patch because
            # crossing 342 -> 343 adds a third repeated stream ID and frame.
            rows = [edge(0x2000, 400, 343, 3), edge(0x3000, 500, 342, 2)]
            result = search.select_route(
                rows, images, baseline, root / "route.json", True,
                objective="transport",
            )
            self.assertEqual(result["steps"][1]["inplace_memory"], "0x3000")
            self.assertEqual(result["steps"][1]["transport"]["data_packets"], 2)

    def test_transport_measurement_is_bound_to_payload_and_encoder_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload.patch"
            payload.write_bytes(b"x" * 300)
            tool = root / "motatool"
            tool.write_bytes(b"exact encoder binary")
            measured = {
                "schema": 1, "payload_bytes": 300, "block_size": 2048,
                "block_count": 1, "wire_bytes": 200, "deflate_bytes": 200,
                "deflate_blocks": 1, "data_packets": 2,
            }
            completed = mock.Mock(returncode=0, stdout=json.dumps(measured), stderr="")
            with mock.patch.object(search.subprocess, "run", return_value=completed) as run:
                result = search.measure_transport_size(tool, payload)
            self.assertEqual(result["payload_sha256"], search.sha256_file(payload))
            self.assertEqual(result["transport_encoder_sha256"], search.sha256_file(tool))
            self.assertEqual(result["v2_data_packets"], 2)
            self.assertIn("transport-size", run.call_args.args[0])

    def test_partial_or_missing_capable_transport_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(search.RouteSearchError, "partial transport"):
            search.normalize_transport_stats(
                {"payload_sha256": "a" * 64}, 100, "test row"
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = search.container_size(64)
            manifest, _ = self.write_inventory(
                root, [b"0", b"1", b"2"], baseline_size=baseline,
                transport_capabilities=[False, True, False],
                transport_pipelines=[None, 1, None],
            )
            images = search.load_inventory(manifest)
            row = {
                "source": 1, "target": 2, "memory": 0x1000,
                "payload": 100, "container": search.container_size(100),
                "stage_start": 0xC0000, "margin": 100, "feasible": True,
            }
            with self.assertRaisesRegex(search.RouteSearchError, "missing transport"):
                search.select_route(
                    [row], images, baseline, root / "route.json", True,
                    objective="transport",
                )

    def test_transport_cache_cannot_mix_or_reuse_a_different_encoder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = search.container_size(64)
            manifest, _ = self.write_inventory(
                root, [b"0", b"1", b"2"], baseline_size=baseline,
                transport_capabilities=[False, True, False],
                transport_pipelines=[None, 1, None],
            )
            images = search.load_inventory(manifest)

            def row(memory: int, encoder: str) -> dict[str, object]:
                return {
                    "source": 1, "target": 2, "memory": memory,
                    "payload": 100, "container": search.container_size(100),
                    "stage_start": 0xC0000, "margin": 100, "feasible": True,
                    "payload_sha256": "a" * 64,
                    "transport_encoder_sha256": encoder,
                    "v2_wire_bytes": 50, "v2_deflate_bytes": 50,
                    "v2_deflate_blocks": 1, "v2_data_packets": 1,
                }

            first = row(0x1000, "b" * 64)
            self.assertTrue(search.cache_satisfies_job(first, True, "b" * 64))
            self.assertFalse(search.cache_satisfies_job(first, True, "c" * 64))
            with self.assertRaisesRegex(search.RouteSearchError, "mix different"):
                search.select_route(
                    [first, row(0x2000, "c" * 64)], images, baseline,
                    root / "route.json", True, objective="transport",
                )

    def test_fixed_outputs_never_follow_preexisting_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim.txt"
            victim.write_text("caller-owned", encoding="ascii")
            geometry = root / "geometry.csv"
            geometry.symlink_to(victim)
            with self.assertRaisesRegex(search.RouteSearchError, "regular file"):
                search.write_csv(geometry, [])
            self.assertEqual(victim.read_text(encoding="ascii"), "caller-owned")

            manifest, _ = self.write_inventory(
                root, [b"start", b"bridge", b"endpoint"]
            )
            route = root / "route.json"
            route.symlink_to(victim)
            with self.assertRaisesRegex(search.RouteSearchError, "regular file"):
                search.select_route(
                    [], search.load_inventory(manifest), 100, route, True
                )
            self.assertEqual(victim.read_text(encoding="ascii"), "caller-owned")

    def test_invalid_inventory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, records = self.write_inventory(root, [b"one", b"two"])
            cases = []
            bad = json.loads(manifest.read_text())
            bad["images"][1]["node"] = 3
            cases.append((bad, "contiguous"))
            bad = json.loads(manifest.read_text())
            bad["images"][1]["sha256"] = bad["images"][0]["sha256"]
            cases.append((bad, "SHA mismatch"))
            bad = json.loads(manifest.read_text())
            bad["images"][1]["path"] = "missing.bin"
            cases.append((bad, "missing inventory image"))
            bad = json.loads(manifest.read_text())
            bad["images"][1][search.TRANSPORT_CAPABILITY] = "yes"
            cases.append((bad, "must be boolean"))
            bad = json.loads(manifest.read_text())
            del bad["images"][1][search.TRANSPORT_CAPABILITY]
            cases.append((bad, "required by the inventory capability contract"))
            bad = json.loads(manifest.read_text())
            del bad["images"][1][search.TRANSPORT_MAX_BLOCK_FIELD]
            cases.append((bad, "required by the inventory capability contract"))
            bad = json.loads(manifest.read_text())
            bad["images"][1][search.TRANSPORT_MAX_BLOCK_FIELD] = 4096
            cases.append((bad, "must be 1024 or 2048"))
            bad = json.loads(manifest.read_text())
            bad["images"][1][search.TRANSPORT_CAPABILITY_ALIAS] = True
            cases.append((bad, "disagrees"))
            bad = json.loads(manifest.read_text())
            bad["images"][1][search.TRANSPORT_CAPABILITY] = True
            bad["images"][1][search.TRANSPORT_MAX_BLOCK_FIELD] = (
                search.DEFLATE_BLOCK_SIZE
            )
            cases.append((bad, "is required"))
            bad = json.loads(manifest.read_text())
            bad["images"][1][search.TRANSPORT_CAPABILITY] = True
            bad["images"][1][search.TRANSPORT_MAX_BLOCK_FIELD] = (
                search.DEFLATE_BLOCK_SIZE
            )
            bad["images"][1][search.TRANSPORT_PIPELINE_FIELD] = 0
            cases.append((bad, "must be between"))
            bad = json.loads(manifest.read_text())
            bad["images"][1][search.TRANSPORT_MAX_BLOCK_FIELD] = (
                search.DEFLATE_BLOCK_SIZE
            )
            cases.append((bad, "required for v2 transport"))
            bad = json.loads(manifest.read_text())
            del bad["images"][1]["version_rank"]
            cases.append((bad, "required by the inventory edge policy"))
            bad = json.loads(manifest.read_text())
            bad["images"][1]["version_rank"] = 0
            cases.append((bad, "one-to-one"))
            for number, (document, message) in enumerate(cases):
                candidate = root / f"bad-{number}.json"
                candidate.write_text(json.dumps(document), encoding="ascii")
                with self.subTest(message=message), self.assertRaisesRegex(
                    search.RouteSearchError, message
                ):
                    search.load_inventory(candidate)

    def test_bridges_are_inserted_before_preserved_endpoint(self) -> None:
        original = {
            "schema": 1,
            "images": [
                {"node": 0, "kind": "start"},
                {"node": 1, "kind": "old-bridge"},
                {"node": 2, "kind": "endpoint", "path": "endpoint.bin"},
            ],
        }
        result = search.insert_bridges_before_endpoint(
            original,
            [{"node": 99, "kind": "new-bridge-1"}, {"kind": "new-bridge-2"}],
        )
        self.assertEqual(
            [record["node"] for record in result["images"]], [0, 1, 2, 3, 4]
        )
        self.assertEqual(
            [record["kind"] for record in result["images"]],
            ["start", "old-bridge", "new-bridge-1", "new-bridge-2", "endpoint"],
        )
        self.assertEqual(result["images"][-1]["path"], "endpoint.bin")
        self.assertEqual(len(original["images"]), 3)


if __name__ == "__main__":
    unittest.main()
