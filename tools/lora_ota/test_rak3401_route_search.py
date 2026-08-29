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
            }
            if node == 1:
                record["baseline_container_size"] = baseline_size
            records.append(record)
        manifest = root / "images.json"
        manifest.write_text(json.dumps({"images": records}), encoding="ascii")
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
            migrated = search.migrate_csv(old_manifest, cache)
            key = search.cache_key(str(old[1]["sha256"]), str(old[2]["sha256"]),
                                   search.FIXED_MEMORY)
            self.assertIn(key, migrated)
            projected = search.project_cache(
                migrated, search.load_inventory(new_manifest), {key}
            )
            self.assertEqual((projected[0]["source"], projected[0]["target"]), (2, 3))

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
                    "1" * 64, "2" * 64, directory,
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
                source_sha, target_sha,
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
