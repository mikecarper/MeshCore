#!/usr/bin/env python3
"""Focused tests for variable-node compact RAK3401 bundle inputs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_rak3401_compact_bundle as compact


def firmware(body: bytes, version: str) -> bytes:
    hardware = compact.EXPECTED_HARDWARE.encode("ascii").ljust(32, b"\0")
    return (
        body
        + b"EndF"
        + struct.pack("<I", len(body))
        + hashlib.sha256(body).digest()[:8]
        + struct.pack(
            "<II", compact.motalib.pack_version(version), compact.EXPECTED_TARGET_ID
        )
        + hardware
    )


def uf2(image: bytes, payload_size: int = 256) -> bytes:
    padded_size = compact.align_up(len(image), payload_size)
    encoded = image + b"\xFF" * (padded_size - len(image))
    block_count = len(encoded) // payload_size
    blocks = []
    for number in range(block_count):
        block = bytearray(compact.UF2_BLOCK_SIZE)
        struct.pack_into(
            "<IIIIIIII", block, 0,
            compact.UF2_MAGIC_START0,
            compact.UF2_MAGIC_START1,
            compact.UF2_FLAG_FAMILY_ID,
            compact.APP_BASE + number * payload_size,
            payload_size,
            number,
            block_count,
            compact.UF2_NRF52840_FAMILY_ID,
        )
        block[compact.UF2_DATA_OFFSET:compact.UF2_DATA_OFFSET + payload_size] = (
            encoded[number * payload_size:(number + 1) * payload_size]
        )
        struct.pack_into(
            "<I", block, compact.UF2_BLOCK_SIZE - 4, compact.UF2_MAGIC_END
        )
        blocks.append(bytes(block))
    return b"".join(blocks)


class CompactRouteInputTests(unittest.TestCase):
    def schema2_route(self) -> dict[str, object]:
        return {
            "schema": 2,
            "status": "reachable",
            "search_complete": True,
            "app_base": "0x26000",
            "stage_ceiling": "0xD4000",
            "node_count": 4,
            "objective": "minimum packages, then minimum total container bytes",
            "candidate_geometries": 100,
            "feasible_edges": 4,
            "shortest_package_count": 2,
            "shortest_route_count": 1,
            "selected_total_bytes": 300,
            "steps": [
                {
                    "source_node": 0,
                    "target_node": 1,
                    "inplace_memory": "0x98000",
                    "reuse_baseline_package": True,
                    "expected_container_size": 100,
                    "expected_staging_margin": 1,
                    "expected_target_sha256": "1" * 64,
                    "expected_target_version": "1.16.7.9",
                },
                {
                    "source_node": 1,
                    "target_node": 3,
                    "inplace_memory": "0x98000",
                    "reuse_baseline_package": False,
                    "expected_container_size": 200,
                    "expected_staging_margin": 2,
                    "expected_target_sha256": "2" * 64,
                    "expected_target_version": "1.17.1.5",
                },
            ],
        }

    def test_existing_schema1_route_remains_accepted(self) -> None:
        route = Path(__file__).with_name("rak3401_compact_route.json")
        steps, document = compact.read_route(route)
        self.assertEqual(document["schema"], 1)
        self.assertEqual(document["endpoint_node"], 30)
        self.assertEqual(len(steps), 9)

    def test_schema2_route_is_normalized_for_bundle_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "route.json"
            path.write_text(json.dumps(self.schema2_route()), encoding="ascii")
            steps, document = compact.read_route(path)
        self.assertEqual([step["target_node"] for step in steps], [1, 3])
        self.assertEqual(document["endpoint_node"], 3)
        self.assertTrue(document["search"]["search_complete"])
        self.assertEqual(document["search"]["selected_total_bytes"], 300)

    def test_public_route_search_drops_schema1_private_fields(self) -> None:
        route = Path(__file__).with_name("rak3401_compact_route.json")
        _steps, document = compact.read_route(route)
        document["search"].update({
            "password": "must-not-be-archived",
            "local_path": "/tmp/private/route.json",
            "nested": {"api_token": "must-not-be-archived"},
        })

        public = compact.public_route_search(1, document)

        self.assertNotIn("password", public)
        self.assertNotIn("local_path", public)
        self.assertNotIn("nested", public)
        self.assertEqual(
            set(public),
            {
                "objective", "shortest_package_count", "selected_total_bytes",
                "page_size", "candidate_pairs", "candidate_geometries",
                "feasible_edges", "shortest_route_count",
            },
        )

    def test_schema2_route_must_be_a_complete_reachable_search(self) -> None:
        for field, value, message in (
            ("status", "unreachable", "does not reach"),
            ("search_complete", False, "incomplete"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                document = self.schema2_route()
                document[field] = value
                path = Path(directory) / "route.json"
                path.write_text(json.dumps(document), encoding="ascii")
                with self.assertRaisesRegex(compact.CompactBuildError, message):
                    compact.read_route(path)


class CompactImageInventoryTests(unittest.TestCase):
    def test_schema2_snapshot_freezes_manifest_and_referenced_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            image = firmware(b"frozen body", "1.17.1.5")
            image_path = source / "image.bin"
            image_path.write_bytes(image)
            record = {
                "node": 0,
                "path": image_path.name,
                "size": len(image),
                "sha256": hashlib.sha256(image).hexdigest(),
                "body_hash": hashlib.sha256(image[:-56]).digest()[:8].hex(),
                "version": "1.17.1.5",
            }
            manifest = source / "images.json"
            manifest.write_text(json.dumps({"images": [record]}), encoding="ascii")
            frozen = compact.snapshot_schema2_inventory(
                manifest, root / "frozen" / "images.json"
            )

            image_path.write_bytes(b"changed")
            manifest.write_text("{}", encoding="ascii")
            images, _sources = compact.read_image_inventory(frozen, 1)

        self.assertEqual(images[0], image)

    def test_schema1_snapshot_freezes_manifest_sibling_zips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            archive = source / "bridge.zip"
            archive.write_bytes(b"frozen zip")
            manifest = source / "bridges.json"
            manifest.write_text(
                json.dumps({"targets": [{"zip": archive.name}]}),
                encoding="ascii",
            )
            frozen = compact.snapshot_schema1_manifest(
                manifest, root / "frozen" / manifest.name
            )

            archive.write_bytes(b"changed")
            manifest.write_text("{}", encoding="ascii")
            self.assertEqual(
                json.loads(frozen.read_text(encoding="ascii")),
                {"targets": [{"zip": "bridge.zip"}]},
            )
            self.assertEqual(
                (frozen.parent / "bridge.zip").read_bytes(), b"frozen zip"
            )

    def test_inventory_validates_order_hash_size_identity_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            expected = {}
            for node, version in enumerate(("1.16.7.0", "1.16.7.9", "1.17.1.5")):
                image = firmware(bytes([node + 1]) * (128 + node), version)
                image_path = root / f"image-{node:02d}.bin"
                image_path.write_bytes(image)
                expected[node] = image
                records.append({
                    "node": node,
                    "path": image_path.name,
                    "size": len(image),
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "body_hash": hashlib.sha256(image[:-56]).digest()[:8].hex(),
                    "version": version,
                })
            manifest = root / "images.json"
            manifest.write_text(json.dumps({"images": records}), encoding="ascii")
            images, sources = compact.read_image_inventory(manifest, 3)
        self.assertEqual(images, expected)
        self.assertTrue(sources[1].startswith("image-sha256:"))

    def test_inventory_rejects_a_stale_image_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = firmware(b"body", "1.17.1.5")
            (root / "image-00.bin").write_bytes(image)
            record = {
                "node": 0,
                "path": "image-00.bin",
                "size": len(image),
                "sha256": "0" * 64,
                "body_hash": hashlib.sha256(b"body").digest()[:8].hex(),
                "version": "1.17.1.5",
            }
            manifest = root / "images.json"
            manifest.write_text(
                json.dumps({"images": [record, record, record]}), encoding="ascii"
            )
            with self.assertRaisesRegex(compact.CompactBuildError, "SHA-256 mismatch"):
                compact.read_image_inventory(manifest, 3)

    def test_bundled_inventory_drops_machine_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            destination = root / "bundled.json"
            source.write_text(
                json.dumps({
                    "baseline_mota": "/tmp/private/step.mota",
                    "admin_password": "must-not-be-archived",
                    "api_token": "must-not-be-archived",
                    "notes": "must-not-be-archived",
                    "images": [{
                        "node": 0,
                        "path": "/tmp/private/image.bin",
                        "size": 123,
                        "sha256": "1" * 64,
                        "body_hash": "2" * 16,
                        "version": "1.16.7.0",
                        "latitude": 47.0,
                        "nested": {
                            "source_path": "/tmp/private/source.bin",
                            "private_key": "must-not-be-archived",
                            "keep": 1,
                        },
                    }],
                }),
                encoding="ascii",
            )
            compact.write_inventory_provenance(source, destination)
            bundled = json.loads(destination.read_text(encoding="ascii"))
        self.assertNotIn("path", bundled["images"][0])
        self.assertNotIn("baseline_mota", bundled)
        self.assertNotIn("admin_password", bundled)
        self.assertNotIn("api_token", bundled)
        self.assertNotIn("notes", bundled)
        self.assertEqual(
            bundled,
            {
                "schema": 2,
                "images": [{
                    "node": 0,
                    "size": 123,
                    "sha256": "1" * 64,
                    "body_hash": "2" * 16,
                    "version": "1.16.7.0",
                }],
            },
        )

    def test_inventory_rejects_wrong_declared_body_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = []
            for node in range(3):
                image = firmware(bytes([node + 1]) * 32, f"1.16.7.{node}")
                image_path = root / f"image-{node}.bin"
                image_path.write_bytes(image)
                records.append({
                    "node": node, "path": image_path.name, "size": len(image),
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "body_hash": "0" * 16, "version": f"1.16.7.{node}",
                })
            manifest = root / "images.json"
            manifest.write_text(json.dumps({"images": records}), encoding="ascii")
            with self.assertRaisesRegex(compact.CompactBuildError, "body hash mismatch"):
                compact.read_image_inventory(manifest, 3)


class CompactSimulatorTests(unittest.TestCase):
    def test_exact_required_simulators_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preview5 = Path(directory) / "preview5"
            current = Path(directory) / "current"
            preview5.write_bytes(b"p5")
            current.write_bytes(b"current")
            hashes = {
                preview5: compact.REQUIRED_SIMULATORS["preview5"]["sha256"],
                current: compact.REQUIRED_SIMULATORS["current"]["sha256"],
            }
            with mock.patch.object(compact.common, "sha256_file", side_effect=hashes.get):
                result = compact.parse_simulators([
                    f"preview5={preview5}", f"current={current}"
                ])
        self.assertEqual([label for label, _path in result], ["preview5", "current"])

    def test_missing_or_wrong_simulator_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            simulator = Path(directory) / "sim"
            simulator.write_bytes(b"wrong")
            for values, digest in (([], None), ([f"preview5={simulator}"], "0" * 64)):
                with self.subTest(values=values), mock.patch.object(
                    compact.common, "sha256_file", return_value=digest
                ):
                    with self.assertRaisesRegex(compact.CompactBuildError, "require exactly"):
                        compact.parse_simulators(values)


class CompactUf2Tests(unittest.TestCase):
    def test_exact_app_only_uf2_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = firmware(b"endpoint" * 41, "1.17.1.5")
            path = Path(directory) / "endpoint.uf2"
            path.write_bytes(uf2(image))
            compact.validate_uf2_firmware(path, image, "test UF2")

    def test_wrong_family_and_non_app_address_are_rejected(self) -> None:
        image = firmware(b"endpoint", "1.17.1.5")
        for label, offset, value, message in (
            ("family", 28, 0xDEADBEEF, "nRF52840"),
            ("address", 12, compact.APP_BASE - 256, "app-only"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                raw = bytearray(uf2(image))
                struct.pack_into("<I", raw, offset, value)
                path = Path(directory) / "endpoint.uf2"
                path.write_bytes(raw)
                with self.assertRaisesRegex(compact.CompactBuildError, message):
                    compact.validate_uf2_firmware(path, image, "test UF2")

    def test_duplicate_block_and_firmware_mismatch_are_rejected(self) -> None:
        image = firmware(b"x" * 600, "1.17.1.5")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = bytearray(uf2(image))
            struct.pack_into("<I", duplicate, compact.UF2_BLOCK_SIZE + 20, 0)
            duplicate_path = root / "duplicate.uf2"
            duplicate_path.write_bytes(duplicate)
            with self.assertRaisesRegex(compact.CompactBuildError, "repeats"):
                compact.validate_uf2_firmware(duplicate_path, image, "test UF2")

            mismatch = bytearray(uf2(image))
            mismatch[compact.UF2_DATA_OFFSET] ^= 1
            mismatch_path = root / "mismatch.uf2"
            mismatch_path.write_bytes(mismatch)
            with self.assertRaisesRegex(compact.CompactBuildError, "does not match"):
                compact.validate_uf2_firmware(mismatch_path, image, "test UF2")

    def test_non_erased_final_padding_is_rejected(self) -> None:
        image = firmware(b"odd", "1.17.1.5")
        with tempfile.TemporaryDirectory() as directory:
            raw = bytearray(uf2(image))
            raw[compact.UF2_DATA_OFFSET + len(image)] = 0
            path = Path(directory) / "padding.uf2"
            path.write_bytes(raw)
            with self.assertRaisesRegex(compact.CompactBuildError, "non-erased"):
                compact.validate_uf2_firmware(path, image, "test UF2")


class CompactToolProvenanceTests(unittest.TestCase):
    def test_required_versions_and_actual_launcher_hashes_are_recorded(self) -> None:
        motatool = Path("/tools/motatool")
        detools = Path("/tools/detools")
        hashes = {motatool: "1" * 64, detools: "2" * 64}
        with mock.patch.object(
            compact, "run", side_effect=["motatool 0.1.0\n", "0.53.0\n"]
        ), mock.patch.object(compact.common, "sha256_file", side_effect=hashes.get):
            result = compact.tool_provenance(motatool, detools)
        self.assertEqual(result["motatool"]["version"], "0.1.0")
        self.assertEqual(
            result["motatool"]["asserted_source_commit"], compact.MOTATOOL_COMMIT
        )
        self.assertEqual(result["motatool"]["executable_sha256"], "1" * 64)
        self.assertEqual(result["detools"]["launcher_sha256"], "2" * 64)

    def test_wrong_tool_versions_fail_closed(self) -> None:
        for outputs, message in (
            (["motatool 0.2.0\n"], "motatool version"),
            (["motatool 0.1.0\n", "0.54.0\n"], "detools version"),
        ):
            with self.subTest(outputs=outputs), mock.patch.object(
                compact, "run", side_effect=outputs
            ):
                with self.assertRaisesRegex(compact.CompactBuildError, message):
                    compact.tool_provenance(Path("motatool"), Path("detools"))


class CompactGeometryEvidenceTests(unittest.TestCase):
    def make_evidence(self, root: Path) -> tuple[Path, Path, list[dict[str, object]]]:
        records = []
        for node, version in enumerate(("1.16.7.0", "1.16.7.9", "1.17.1.3", "1.17.1.5")):
            image = firmware(bytes([node + 1]) * (64 + node), version)
            image_path = root / f"image-{node:02d}.bin"
            image_path.write_bytes(image)
            records.append({
                "node": node, "path": image_path.name, "size": len(image),
                "sha256": hashlib.sha256(image).hexdigest(),
                "body_hash": hashlib.sha256(image[:-56]).digest()[:8].hex(),
                "version": version,
                **({"baseline_container_size": 100} if node == 1 else {}),
            })
        inventory_path = root / "inventory.json"
        inventory_path.write_text(json.dumps({"images": records}), encoding="ascii")
        inventory = compact.route_search.load_inventory(inventory_path)
        rows = []
        for job in compact.route_search.all_jobs(inventory):
            source, target, memory, _from, _to, source_sha, target_sha = job
            if source == 1 and target == 3:
                payload = 10
            else:
                # A successfully generated patch may be too large for staging;
                # that is valid infeasible evidence. A tool exception is not.
                payload = 700_000
            container = compact.route_search.container_size(payload)
            stage_start = compact.route_search.align_down(
                compact.STAGE_CEILING - container
            )
            margin = stage_start - (compact.APP_BASE + memory)
            feasible, error = margin >= 0, ""
            rows.append({
                "source": source, "target": target, "source_sha256": source_sha,
                "target_sha256": target_sha, "memory": memory, "payload": payload,
                "container": container, "stage_start": stage_start, "margin": margin,
                "feasible": feasible, "error": error,
            })
        geometry_path = root / "geometry.csv"
        compact.route_search.write_csv(geometry_path, rows)
        route_path = root / "route.json"
        compact.route_search.select_route(rows, inventory, 100, route_path, complete=True)
        return inventory_path, route_path, rows

    def test_complete_geometry_reproduces_declared_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, route, _rows = self.make_evidence(root)
            result = compact.validate_geometry_results(root / "geometry.csv", inventory, route)
        self.assertEqual(result["nodes"], [0, 1, 3])

    def test_legacy_source_zero_measurement_is_accepted_but_not_routed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path, route_path, rows = self.make_evidence(root)
            inventory = compact.route_search.load_inventory(inventory_path)
            payload = 10
            container = compact.route_search.container_size(payload)
            stage_start = compact.route_search.align_down(
                compact.STAGE_CEILING - container
            )
            margin = stage_start - (compact.APP_BASE + compact.FIXED_WORKSPACE)
            rows.append({
                "source": 0,
                "target": 2,
                "source_sha256": inventory[0]["sha256"],
                "target_sha256": inventory[2]["sha256"],
                "memory": compact.FIXED_WORKSPACE,
                "payload": payload,
                "container": container,
                "stage_start": stage_start,
                "margin": margin,
                "feasible": margin >= 0,
                "error": "",
            })
            compact.route_search.write_csv(root / "geometry.csv", rows)
            compact.route_search.select_route(
                rows, inventory, 100, route_path, complete=True
            )

            result = compact.validate_geometry_results(
                root / "geometry.csv", inventory_path, route_path
            )

        self.assertEqual(result["nodes"], [0, 1, 3])
        self.assertEqual(result["candidate_geometries"], len(rows))

    def test_geometry_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, route, rows = self.make_evidence(root)
            rows[0]["container"] = 123
            compact.route_search.write_csv(root / "geometry.csv", rows)
            with self.assertRaisesRegex(compact.CompactBuildError, "inconsistent"):
                compact.validate_geometry_results(root / "geometry.csv", inventory, route)

    def test_missing_and_extra_geometry_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, route, rows = self.make_evidence(root)
            for label, altered, message in (
                ("missing", rows[:-1], "missing"),
                ("extra", rows + [{**rows[0], "memory": int(rows[0]["memory"]) + 1}], "extra"),
            ):
                with self.subTest(label=label):
                    compact.route_search.write_csv(root / "geometry.csv", altered)
                    with self.assertRaisesRegex(compact.CompactBuildError, message):
                        compact.validate_geometry_results(root / "geometry.csv", inventory, route)

    def test_route_metric_or_step_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, route, _rows = self.make_evidence(root)
            document = json.loads(route.read_text(encoding="ascii"))
            document["selected_total_bytes"] += 1
            route.write_text(json.dumps(document), encoding="ascii")
            with self.assertRaisesRegex(compact.CompactBuildError, "selected_total_bytes"):
                compact.validate_geometry_results(root / "geometry.csv", inventory, route)

    def test_failed_patch_job_cannot_be_exhaustive_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, route, rows = self.make_evidence(root)
            rows[0].update(
                payload=-1, container=-1, stage_start=-1, margin=-1,
                feasible=False, error="MemoryError: test",
            )
            compact.route_search.write_csv(root / "geometry.csv", rows)
            with self.assertRaisesRegex(
                compact.CompactBuildError, "cannot prove an exhaustive route search"
            ):
                compact.validate_geometry_results(root / "geometry.csv", inventory, route)

    def test_short_geometry_row_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory, route, _rows = self.make_evidence(root)
            (root / "geometry.csv").write_text(
                ",".join(compact.route_search.FIELDS) + "\n1,2\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                compact.CompactBuildError, "integer|malformed|invalid"
            ):
                compact.validate_geometry_results(root / "geometry.csv", inventory, route)


class CompactPhysicalValidationTests(unittest.TestCase):
    def evidence(self) -> tuple[dict[str, object], dict[str, object]]:
        expected = {
            "chain_sha256": "1" * 64,
            "start_sha256": "2" * 64,
            "endpoint_sha256": "3" * 64,
            "endpoint_body_hash": "a" * 16,
            "endpoint_version": "1.17.1.5",
            "output_rows": [{"target_body_hash": "5" * 16}],
            "validation_steps": [{
                "mota_sha256": "6" * 64,
                "target_sha256": "7" * 64,
            }],
        }
        document: dict[str, object] = {
            "schema": 1,
            "kind": compact.PHYSICAL_VALIDATION_KIND,
            "status": "passed",
            "chain_sha256": expected["chain_sha256"],
            "start_sha256": expected["start_sha256"],
            "endpoint_sha256": expected["endpoint_sha256"],
            "endpoint_body_hash": expected["endpoint_body_hash"],
            "endpoint_version": expected["endpoint_version"],
            "step_count": 1,
            "steps": [{
                "step": 1,
                "status": "passed",
                "mota_sha256": "6" * 64,
                "target_sha256": "7" * 64,
                "target_body_hash": "5" * 16,
            }],
            "final_swd": {"status": "passed", "app_sha256": "3" * 64},
        }
        return document, expected

    def test_exact_record_is_accepted_and_canonicalized(self) -> None:
        document, expected = self.evidence()
        document["endpoint_body_hash"] = str(document["endpoint_body_hash"]).upper()
        result = compact.validate_physical_validation_record(document, **expected)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["endpoint_body_hash"], "a" * 16)

    def test_stale_package_or_incomplete_step_list_is_rejected(self) -> None:
        for label, mutate, message in (
            (
                "stale",
                lambda document: document["steps"][0].__setitem__(
                    "mota_sha256", "8" * 64
                ),
                "does not match",
            ),
            (
                "incomplete",
                lambda document: document.__setitem__("steps", []),
                "every chain step",
            ),
        ):
            with self.subTest(label=label):
                document, expected = self.evidence()
                mutate(document)
                with self.assertRaisesRegex(compact.CompactBuildError, message):
                    compact.validate_physical_validation_record(document, **expected)

    def test_extra_transcript_or_secret_fields_are_rejected(self) -> None:
        document, expected = self.evidence()
        document["admin_password"] = "must-not-be-archived"
        with self.assertRaisesRegex(compact.CompactBuildError, "unexpected"):
            compact.validate_physical_validation_record(document, **expected)


class CompactReproducibilityTests(unittest.TestCase):
    def test_zip_metadata_does_not_depend_on_source_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archives = []
            for number in (1, 2):
                tree = root / f"tree-{number}" / "same-root"
                tree.mkdir(parents=True)
                item = tree / "payload.bin"
                item.write_bytes(b"identical")
                os.utime(item, (946684800 + number, 946684800 + number))
                output = root / f"archive-{number}.zip"
                compact.make_reproducible_zip(tree, output)
                archives.append(output.read_bytes())
            self.assertEqual(archives[0], archives[1])

    def test_zip_output_inside_archived_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory) / "tree"
            tree.mkdir()
            (tree / "payload.bin").write_bytes(b"payload")
            with self.assertRaisesRegex(
                compact.CompactBuildError, "outside the archived tree"
            ):
                compact.make_reproducible_zip(tree, tree / "bundle.zip")

    def test_zip_output_symlinked_inside_archived_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            tree = parent / "tree"
            tree.mkdir()
            (tree / "payload.bin").write_bytes(b"payload")
            alias = parent / "output-alias"
            alias.symlink_to(tree, target_is_directory=True)
            with self.assertRaisesRegex(
                compact.CompactBuildError, "outside the archived tree"
            ):
                compact.make_reproducible_zip(tree, alias / "bundle.zip")


if __name__ == "__main__":
    unittest.main()
