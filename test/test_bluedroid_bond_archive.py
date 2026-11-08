import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bond_archive", ROOT / "scripts/bluedroid_bond_archive.py")
bond = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bond)


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stock = self.root / "stock/libbt.a"
        self.patched = self.root / "patched/libbt.a"
        for path, data in ((self.stock, b"stock"), (self.patched, b"patched")):
            path.parent.mkdir()
            path.write_bytes(data)
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"version": 1, "mcus": {"esp32s3": {
            "stock_sha256": hashlib.sha256(b"stock").hexdigest(),
            "patched_sha256": hashlib.sha256(b"patched").hexdigest(),
        }}}))
        self.map = self.root / "firmware.map"

    def test_rejects_wrong_mcu_and_modified_archives(self):
        bond.verify_archives(self.manifest, "esp32s3", self.stock, self.patched)
        with self.assertRaises(bond.ArchiveError):
            bond.verify_archives(self.manifest, "esp32", self.stock, self.patched)
        self.stock.write_bytes(b"different core")
        with self.assertRaises(bond.ArchiveError):
            bond.verify_archives(self.manifest, "esp32s3", self.stock, self.patched)
        self.stock.write_bytes(b"stock")
        self.patched.write_bytes(b"modified")
        with self.assertRaises(bond.ArchiveError):
            bond.verify_archives(self.manifest, "esp32s3", self.stock, self.patched)

    def test_rejects_missing_manifest_or_incomplete_hash(self):
        self.manifest.unlink()
        with self.assertRaises(bond.ArchiveError):
            bond.verify_archives(self.manifest, "esp32s3", self.stock, self.patched)
        self.manifest.write_text('{"version":1,"mcus":{"esp32s3":{}}}')
        with self.assertRaises(bond.ArchiveError):
            bond.verify_archives(self.manifest, "esp32s3", self.stock, self.patched)

    def test_rejects_wrong_or_missing_archive_and_guard_in_link_map(self):
        guard = " .text.mesh_bl_narrow_sdk_guard\n 0x1234 0x8 patched/libbt.a(btc_dm.c.obj)\n"
        self.map.write_text("patched/libbt.a(btc_dm.c.obj)\n" + guard)
        bond.verify_link_map(self.map, self.root, self.patched)
        self.map.write_text("patched/libbt.a(btc_dm.c.obj)\n")
        with self.assertRaises(bond.ArchiveError):
            bond.verify_link_map(self.map, self.root, self.patched)
        self.map.write_text("mesh_bl_narrow_sdk_guard\n")
        with self.assertRaises(bond.ArchiveError):
            bond.verify_link_map(self.map, self.root, self.patched)
        self.map.write_text("patched/libbt.a(btc_dm.c.obj)\nstock/libbt.a(other.obj)\n" + guard)
        with self.assertRaises(bond.ArchiveError):
            bond.verify_link_map(self.map, self.root, self.patched)


if __name__ == "__main__":
    unittest.main()
