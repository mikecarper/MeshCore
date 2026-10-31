"""Hardware-free qualification of exact published-artifact migration repairs."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import repair_esp32_migration_release as repair
from motalib import FwIdent, build_endf, hardware_id_for_env, parse_container, pack_version, target_id_for_env, verify

VERSION = "v1.18.0.1-halo-keymind-cascade-dev"
SOURCE = "01234567"
BOARD = "heltec-v4"
TARGET = repair.BOARDS[BOARD]["target"]
IDENTITY = FwIdent(pack_version("v1.18.0.1"), target_id_for_env(TARGET), hardware_id_for_env(TARGET))


def image(fill, identity=IDENTITY):
    body = bytes([0xE9, 1, 2, 0x40]) + bytes([fill]) * 8192
    return body + build_endf(body, identity)


def checksummed_archive(files, manifest):
    files = dict(files)
    manifest = dict(manifest)
    manifest["files"] = {name: {"bytes": len(data), "sha256": repair.digest(data)} for name, data in files.items()}
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    files["SHA256SUMS.txt"] = "".join(f"{repair.digest(data)}  {name}\n" for name, data in sorted(files.items())).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return output.getvalue()


class RepairReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="meshcore-release-repair-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.apps = self.root / "published"
        self.apps.mkdir()
        self.old_image, self.new_image = image(65), image(66)
        self.table = bytes([0xAA, 0x50]) + bytes(4094)
        self.caps = {"target": TARGET, "artifact_target": TARGET, "build_profile": "full",
                     "platform": "ESP32_PLATFORM", "ota_update_verified": True, "verified": True}
        self.files = {"full-application.bin": self.old_image,
                      "full-application.mota": repair.mota_full(self.old_image, IDENTITY, 2048),
                      "target-partitions.bin": self.table, "capabilities.json": json.dumps(self.caps).encode(),
                      "usb-bridge.bin": b"unchanged reviewed USB bridge", "wifi-bridge.bin": b"unchanged Wi-Fi bridge",
                      "README.md": b"Original board-specific instructions"}
        self.manifest = {"board": BOARD, "target": TARGET, "role": "repeater",
                         "firmware_version": VERSION, "source_commit": SOURCE,
                         "target_id": f"0x{IDENTITY.target_id:08x}", "hardware_id": IDENTITY.hw_id,
                         "expanded_slot_bytes": 0x640000, "full_mota_seeder_scratch_bytes": 20}
        self.original = checksummed_archive(self.files, self.manifest)
        self.name = f"{TARGET}-full-usb-wifi-ota-{VERSION}-{SOURCE}"

    def publish(self, *, firmware=None, caps=None, table=None):
        binary = self.new_image if firmware is None else firmware
        capabilities = self.caps if caps is None else caps
        stem = self.apps / self.name
        prefix = bytearray(0x10000)
        prefix[0x8000:0x9000] = self.table if table is None else table
        contents = {self.name + ".bin": binary,
                    self.name + "-merged.bin": bytes(prefix) + binary,
                    self.name + ".capabilities.json": json.dumps(capabilities).encode()}
        for name, data in contents.items():
            (self.apps / name).write_bytes(data)
        report = {"schema_version": 1, "passed": True, "elf_sha256": "a" * 64,
                  "available_internal_bytes": 10000, "required_heap_bytes": 5000,
                  "largest_internal_region_bytes": 9000, "required_contiguous_bytes": 4000,
                  "files": {name: repair.digest(data) for name, data in contents.items()}}
        (self.apps / (self.name + ".memory.json")).write_text(json.dumps(report))
        return self.apps / (self.name + ".bin")

    def test_exact_published_image_and_mota_replace_rebuild_without_changing_bridges_or_instructions(self):
        published = self.publish()
        fixed, report = repair.repair_archive(self.original, self.apps, VERSION, SOURCE)
        manifest, files = repair.unpack_verified(fixed)
        self.assertEqual(report["state"], "updated")
        self.assertEqual(files["full-application.bin"], published.read_bytes())
        self.assertEqual(manifest["files"]["full-application.bin"]["sha256"], repair.digest(self.new_image))
        mota = parse_container(files["full-application.mota"])
        self.assertEqual(verify(mota), [])
        self.assertEqual(mota.payload, self.new_image)
        for name in ("usb-bridge.bin", "wifi-bridge.bin", "README.md", "target-partitions.bin"):
            self.assertEqual(files[name], self.files[name])
        self.assertEqual(manifest["full_mota_seeder_scratch_bytes"], ((len(self.new_image) + 2047) // 2048) * 4)
        self.assertEqual(repair.repair_archive(self.original, self.apps, VERSION, SOURCE)[0], fixed,
                         "Identical verified inputs produce deterministic ZIP bytes")

    def test_no_exact_target_never_substitutes_an_observer_or_retired_target(self):
        self.publish()
        for path in list(self.apps.iterdir()):
            path.rename(path.with_name(path.name.replace(TARGET, TARGET + "_observer")))
        fixed, report = repair.repair_archive(self.original, self.apps, VERSION, SOURCE)
        self.assertEqual(fixed, self.original)
        self.assertEqual(report["state"], "unmapped")

    def test_ambiguous_exact_full_artifacts_fail_before_selecting_one(self):
        self.publish()
        (self.apps / (self.name.replace("full-usb-wifi-ota", "full-other") + ".bin")).write_bytes(self.new_image)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            repair.repair_archive(self.original, self.apps, VERSION, SOURCE)

    def test_changed_published_package_wrong_identity_table_or_caps_fail_closed(self):
        cases = ("memory", "identity", "table", "caps")
        for failure in cases:
            with self.subTest(failure=failure):
                for path in self.apps.iterdir():
                    path.unlink()
                if failure == "identity":
                    self.publish(firmware=image(67, FwIdent(IDENTITY.fw_version, IDENTITY.target_id ^ 1, IDENTITY.hw_id)))
                elif failure == "table":
                    self.publish(table=bytes(4096))
                elif failure == "caps":
                    self.publish(caps={**self.caps, "target": TARGET + "_observer"})
                else:
                    published = self.publish()
                    published.write_bytes(b"changed after public qualification")
                with self.assertRaises(ValueError):
                    repair.repair_archive(self.original, self.apps, VERSION, SOURCE)

    def test_original_zip_inventory_and_checksums_must_verify_before_repair(self):
        self.publish()
        manifest, files = repair.unpack_verified(self.original)
        files["usb-bridge.bin"] = b"modified without updating the manifest"
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            repair.repair_archive(bad.getvalue(), self.apps, VERSION, SOURCE)

    def test_stage_keeps_original_inputs_and_binds_the_aggregate_inventory_to_repaired_zips(self):
        self.publish()
        originals = self.root / "originals"
        originals.mkdir()
        name = f"{BOARD}-{VERSION}-{SOURCE}-migration.zip"
        (originals / name).write_bytes(self.original)
        output = self.root / "stage"
        report = repair.stage(originals, self.apps, output, VERSION, SOURCE)
        self.assertEqual((report["updated"], report["unmapped"]), (1, 0))
        self.assertEqual((originals / name).read_bytes(), self.original)
        fixed = (output / "packages" / name).read_bytes()
        with zipfile.ZipFile(output / report["bundle"]) as archive:
            manifest = json.loads(archive.read("release-manifest.json"))
            self.assertEqual(archive.read(name), fixed)
            self.assertEqual(manifest["packages"][name], {"bytes": len(fixed), "sha256": repair.digest(fixed)})
        with self.assertRaisesRegex(ValueError, "empty"):
            repair.stage(originals, self.apps, output, VERSION, SOURCE)


if __name__ == "__main__":
    unittest.main()
