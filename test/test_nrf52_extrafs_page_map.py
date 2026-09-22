"""Host coverage for physical-page retirement in nRF52 internal ExtraFS."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InternalExtraFsPageMapTest(unittest.TestCase):
    def test_mapping_skips_whole_bad_pages_and_keeps_legacy_identity_layout(self):
        source = r"""
#include <helpers/nrf52/InternalExtraFsPageMap.h>
#include <stdint.h>

using mesh::storage::countInternalExtraFsBadPages;
using mesh::storage::internalExtraFsPhysicalPage;

int main() {
  for (uint32_t p = 0; p < 25; ++p) {
    if (internalExtraFsPhysicalPage(0, p) != p) return 1;
  }
  if (internalExtraFsPhysicalPage(0, 25) != UINT32_MAX) return 2;

  const uint32_t middle = 1UL << 12;
  if (countInternalExtraFsBadPages(middle) != 1) return 3;
  if (internalExtraFsPhysicalPage(middle, 11) != 11) return 4;
  if (internalExtraFsPhysicalPage(middle, 12) != 13) return 5;
  if (internalExtraFsPhysicalPage(middle, 23) != 24) return 6;
  if (internalExtraFsPhysicalPage(middle, 24) != UINT32_MAX) return 7;

  const uint32_t edges = (1UL << 0) | (1UL << 24);
  if (internalExtraFsPhysicalPage(edges, 0) != 1) return 8;
  if (internalExtraFsPhysicalPage(edges, 22) != 23) return 9;
  if (internalExtraFsPhysicalPage(edges, 23) != UINT32_MAX) return 10;

  const uint32_t four = (1UL << 0) | (1UL << 4)
      | (1UL << 17) | (1UL << 24);
  if (countInternalExtraFsBadPages(four) != 4) return 11;
  uint32_t previous = 0;
  for (uint32_t logical = 0; logical < 21; logical++) {
    const uint32_t physical = internalExtraFsPhysicalPage(four, logical);
    if (physical >= 25 || (four & (1UL << physical))) return 12;
    if (logical && physical <= previous) return 13;
    previous = physical;
  }
  if (internalExtraFsPhysicalPage(four, 21) != UINT32_MAX) return 14;
  if (internalExtraFsPhysicalPage(1UL << 25, 0) != UINT32_MAX) return 15;
  return 0;
}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "extrafs_page_map"
            compiled = subprocess.run(
                ["c++", "-std=c++11", f"-I{ROOT / 'src'}", "-x", "c++",
                 "-", "-o", str(executable)],
                input=source,
                text=True,
                capture_output=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(executable)], capture_output=True,
                                      text=True)
            self.assertEqual(executed.returncode, 0, executed.stderr)

    def test_destructive_scan_only_runs_during_early_boot(self):
        store = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        device = (ROOT / "examples/companion_radio/ResilientInternalExtraFS.cpp").read_text()
        self.assertIn("reinitializeInternalExtraFS(true)", store)
        self.assertIn("scan_physical_pages && !extra->scanAndRetireBadPages()", store)
        self.assertIn("extra->requestBootScan()", store)
        self.assertIn("setBootScanRequest(marker)", device)
        self.assertIn("BOOT_SCAN_ALL", device)
        self.assertIn("uint32_t bad = _bad_pages;", device)
        self.assertNotIn("_bad_pages | (_pending_pages & PAGE_MASK)", device)
        self.assertIn("return _boot_scan_forced ||", (
            ROOT / "examples/companion_radio/ResilientInternalExtraFS.h"
        ).read_text())
        self.assertIn("extra->acknowledgeRecoveredBootHint()", store)
        self.assertIn("extra->pageMapNeedsSave()", store)
        scan_command = mesh.split("void MyMesh::scanInternalExtraFS(Stream& output)", 1)[1]
        scan_command = scan_command.split("#if defined(MESHCORE_EXTRAFS_HIL)", 1)[0]
        self.assertIn("_store->requestInternalExtraFSBootScan()", scan_command)
        self.assertNotIn("scanAndRetireBadPages", scan_command)


if __name__ == "__main__":
    unittest.main()
