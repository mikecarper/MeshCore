#!/usr/bin/env python3
"""Focused tests for the reproducible RAK3401 receive-inflate builder."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_rak3401_inflate_bridges as inflate


class InflateAssetTests(unittest.TestCase):
    def test_vendored_snapshot_assets_match_their_pins(self) -> None:
        metadata = inflate.inflate_asset_metadata()

        self.assertEqual(set(metadata), set(inflate.INFLATE_ASSETS))
        for destination, (_asset, expected_hash) in inflate.INFLATE_ASSETS.items():
            self.assertEqual(metadata[destination]["sha256"], expected_hash)

    def test_install_and_remove_are_exact_inverses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            inflate.install_inflate_assets(source)
            for destination, (_asset, expected_hash) in inflate.INFLATE_ASSETS.items():
                self.assertEqual(
                    inflate.sha256_file(source / destination), expected_hash
                )

            inflate.remove_inflate_assets(source)
            self.assertFalse((source / "src/helpers/ota/tinf").exists())
            for destination in inflate.INFLATE_ASSETS:
                self.assertFalse((source / destination).exists())


class ExactTransformTests(unittest.TestCase):
    def test_replace_exact_replaces_one_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.txt"
            path.write_bytes(b"before anchor after")
            inflate.replace_exact(path, b"anchor", b"replacement", "test")
            self.assertEqual(path.read_bytes(), b"before replacement after")

    def test_replace_exact_rejects_missing_or_ambiguous_anchor(self) -> None:
        for contents, count in ((b"none", 0), (b"anchor anchor", 2)):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.txt"
                path.write_bytes(contents)
                with self.assertRaisesRegex(
                    inflate.BuildError, f"found {count}"
                ):
                    inflate.replace_exact(path, b"anchor", b"replacement", "test")


class BuilderConfigurationTests(unittest.TestCase):
    def test_inflate_builder_has_distinct_reproducibility_identity(self) -> None:
        self.assertEqual(
            inflate.VERSION_SUFFIX, "halo-keymind-cascade-mota-inflate"
        )
        self.assertEqual(
            inflate.INFLATE_SNAPSHOT_COMMIT,
            "add51bf00c46c15ef54318ca766a6daf08a147ee",
        )
        self.assertNotIn("6f03eae8", {target.source_commit[:8] for target in inflate.TARGETS})


if __name__ == "__main__":
    unittest.main()
