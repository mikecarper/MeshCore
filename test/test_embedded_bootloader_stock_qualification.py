"""Stock migration claims must survive packaged application revalidation."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools/mota"))
import nrf52_embedded_bootloader as embed
from nrf52_legacy_bootloaders import legacy_bootloaders_for
import package_cascade_release as release
import qualify_nrf52_embedded_bootloader as qualifier
from motalib import FwIdent, Nrf52Layout, ensure_endf, ensure_nrf52_layout, target_id_for_env


class StockQualificationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="embedded-stock-qualification-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.resource = self.directory / "bootloader-migration-resource"
        self.resource.mkdir()
        self.target = "RAK_3401_repeater"
        self.profile, self.parsed = embed.select_profile(embed.load_manifest(), self.target)
        self.candidate = b"\xa5" * embed.BOOT_SIZE
        self.stock = legacy_bootloaders_for(self.profile["id"], self.parsed["sourceHardware"],
                                           0x26000, 0xB6)
        self.assertTrue(any(item["version"] == "0.4.3" for item in self.stock))
        self.hash_table = b"".join(bytes.fromhex(item["image_sha256"]) for item in self.stock)
        self.metadata = dict(target=self.target, hardware=self.parsed["sourceHardware"],
            role=self.parsed["role"], profile=self.profile["id"], release=embed.VERSION,
            manifest_sha256=embed.MANIFEST_SHA256,
            asset_sha256=self.profile["files"]["zip"]["sha256"],
            candidate_sha256=hashlib.sha256(self.candidate).hexdigest(), legacy_sha256=None,
            app_base=0x26000, softdevice_fwid=0xB6, stock_legacy=deepcopy(self.stock))
        self.manifest = dict(target=self.target, platform="NRF52_PLATFORM", verified=True)
        self.manifest_path = self.directory / "firmware.capabilities.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        (self.resource / "BootloaderMigrationResource.bin").write_bytes(self.candidate)
        self.write_metadata()
        self.write_package()
        self.stack = patch.object(qualifier, "download_zip", return_value=b"pinned fixture ZIP")
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.validation = patch.object(qualifier, "validate_candidate_zip", return_value=(
            self.candidate, SimpleNamespace(app_base=0x26000, softdevice_fwid=0xB6)))
        self.validation.start()
        self.addCleanup(self.validation.stop)

    def write_metadata(self):
        (self.resource / "BootloaderMigrationResource.json").write_text(json.dumps(self.metadata))

    def write_package(self, *, table=None, suffix=b"", body_prefix=b"META"):
        table = self.hash_table if table is None else table
        body = body_prefix + self.candidate + table + suffix + b"BL embedded 2.4.12\0"
        self.application, _ = ensure_endf(ensure_nrf52_layout(body,
            Nrf52Layout(0x26000, 0xED000, 0xED000, 0x10)),
            FwIdent(0x01180002, target_id_for_env(self.target), "RAK3401"))
        with zipfile.ZipFile(self.directory / "firmware.zip", "w") as archive:
            archive.writestr("manifest.json", json.dumps({"manifest": {"application": {
                "bin_file": "application.bin", "dat_file": "application.dat"}}}))
            archive.writestr("application.bin", self.application)
            archive.writestr("application.dat", b"test init packet")

    def qualify(self):
        return qualifier.qualify(self.directory, self.manifest_path)

    def public_manifest(self, metadata):
        return {**self.manifest, "schema_version": 2,
            "artifact_target": self.target + "-with-bootloader-2.4.12-full-ota",
            "build_profile": "auto", "ota_update_verified": True,
            "embedded_bootloader": metadata, "capabilities": ["bootloader.embedded"],
            "verification": [dict(capability="bootloader.embedded", present=True,
                                  source="packaged application")]}

    def test_real_rak_stock_inventory_is_proven_in_the_published_body(self):
        metadata = self.qualify()
        self.assertEqual(metadata["stock_legacy"], self.stock)
        self.assertEqual(metadata["stock_legacy_offset"], 4 + embed.BOOT_SIZE)
        manifest = self.public_manifest(metadata)
        self.assertEqual(release.embedded_bootloader_variant(manifest), embed.VERSION)
        release.validate_embedded_bootloader_application(manifest, self.application)
        self.assertEqual(json.loads(self.manifest_path.read_text())["embedded_bootloader"], metadata)

    def test_sidecar_forgery_wrong_target_and_mutated_inventory_are_refused(self):
        original = deepcopy(self.metadata)
        changes = [dict(stock_legacy=[]), dict(target="RAK_4631_repeater"),
                   dict(hardware="RAK_4631"), dict(app_base=0x27000),
                   dict(softdevice_fwid=0x123), dict(role="companion")]
        for key in ("image_sha256", "artifact_sha256", "source_url", "version", "profiles", "hardware"):
            stock = deepcopy(self.stock)
            stock[0][key] = [] if isinstance(stock[0][key], list) else "forged"
            changes.append(dict(stock_legacy=stock))
        for change in changes:
            with self.subTest(change=change):
                self.metadata = {**original, **change}
                self.write_metadata()
                before = self.manifest_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "pinned release"):
                    self.qualify()
                self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_missing_reordered_mutated_duplicate_or_unaligned_hash_tables_are_refused(self):
        cases = [dict(table=b""), dict(table=self.hash_table[::-1]),
                 dict(table=bytes([self.hash_table[0] ^ 1]) + self.hash_table[1:]),
                 dict(suffix=self.hash_table), dict(body_prefix=b"META!")]
        for options in cases:
            with self.subTest(options=options):
                self.write_package(**options)
                before = self.manifest_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "aligned embedded|stock bootloader hash table"):
                    self.qualify()
                self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_public_claims_are_exact_and_malformed_offsets_never_authorize_support(self):
        metadata = self.qualify()
        original = self.public_manifest(metadata)
        changes = [dict(stock_legacy=[]), dict(stock_legacy=list(reversed(self.stock))),
                   dict(stock_legacy_offset=None), dict(stock_legacy_offset=True),
                   dict(stock_legacy_offset=-4), dict(stock_legacy_offset=1)]
        wrong = deepcopy(self.stock)
        wrong[0]["hardware"] = ["RAK_4631"]
        changes.append(dict(stock_legacy=wrong))
        for change in changes:
            with self.subTest(change=change):
                manifest = deepcopy(original)
                manifest["embedded_bootloader"].update(change)
                with self.assertRaisesRegex(ValueError, "pinned release"):
                    release.embedded_bootloader_variant(manifest)
        historical = deepcopy(original)
        del historical["embedded_bootloader"]["stock_legacy"]
        del historical["embedded_bootloader"]["stock_legacy_offset"]
        self.assertEqual(release.embedded_bootloader_variant(historical), embed.VERSION)

    def test_release_rechecks_the_actual_package_instead_of_trusting_qualification_json(self):
        metadata = self.qualify()
        manifest = self.public_manifest(metadata)
        self.write_package(table=b"\0" * len(self.hash_table))
        with self.assertRaisesRegex(ValueError, "stock bootloader hash table"):
            release.validate_embedded_bootloader_application(manifest, self.application)
        for offset in (metadata["stock_legacy_offset"] + 4, 4, len(self.application) + 4):
            changed = deepcopy(manifest)
            changed["embedded_bootloader"]["stock_legacy_offset"] = offset
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "stock bootloader hash table"):
                release.validate_embedded_bootloader_application(changed, self.application)

    def test_full_companion_and_sensor_still_cannot_claim_embedded_bootloader(self):
        metadata = self.qualify()
        for target in ("RAK_3401_companion_radio_full", "RAK_3401_sensor"):
            manifest = self.public_manifest(metadata)
            manifest["target"] = target
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "exact supported profile"):
                release.embedded_bootloader_variant(manifest)

    def test_sensor_pair_publication_reads_hashes_from_each_actual_dfu_zip(self):
        metadata = self.qualify()
        records = []
        for sensor in ("full", "reduced"):
            manifest = self.public_manifest(deepcopy(metadata))
            manifest["artifact_target"] = self.target + "-with-bootloader-2.4.12-" + sensor + "-ota"
            manifest["capabilities"] += ["sensor.profile." + sensor, "ota.update.lora"]
            manifest["ota_update_methods"] = ["lora"]
            manifest["verification"].append(dict(capability="ota.update.lora", present=True,
                                                 source="packaged application"))
            path = self.directory / (manifest["artifact_target"] + "-v1.18.0.2-test.zip")
            path.write_bytes((self.directory / "firmware.zip").read_bytes())
            records.append(dict(manifest=manifest, files=[path]))
        release.validate_nrf52_sensor_pairs(records)
        # Keep the valid qualification metadata but replace only the package's
        # table and recompute EndF. Metadata is not sufficient publication proof.
        self.write_package(table=b"\0" * len(self.hash_table))
        records[1]["files"][0].write_bytes((self.directory / "firmware.zip").read_bytes())
        with self.assertRaisesRegex(ValueError, "stock bootloader hash table"):
            release.validate_nrf52_sensor_pairs(records)

    def test_candidate_bytes_cannot_impersonate_the_separate_runtime_hash_table(self):
        metadata = self.qualify()
        manifest = self.public_manifest(deepcopy(metadata))
        candidate = self.hash_table + self.candidate[len(self.hash_table):]
        body = b"META" + candidate + b"BL embedded 2.4.12\0"
        application, _ = ensure_endf(ensure_nrf52_layout(body,
            Nrf52Layout(0x26000, 0xED000, 0xED000, 0x10)),
            FwIdent(0x01180002, target_id_for_env(self.target), "RAK3401"))
        manifest["embedded_bootloader"].update(
            candidate_sha256=hashlib.sha256(candidate).hexdigest(), stock_legacy_offset=4)
        with self.assertRaisesRegex(ValueError, "stock bootloader hash table"):
            release.validate_embedded_bootloader_application(manifest, application)


if __name__ == "__main__":
    unittest.main()
