#!/usr/bin/env python3
"""Preserve OTA capacities while removing padding and oversized page bitmaps."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]


class OtaMemoryLayoutTest(unittest.TestCase):
    def test_real_headers_preserve_natural_alignment_and_descriptor_initialization(self):
        source = r'''
#include <helpers/ota/OtaManager.h>
#include <helpers/ota/OtaStoreQspiNrf52.h>
#include <assert.h>
#include <stddef.h>
using namespace mesh::ota;
static_assert(sizeof(OtaManager::ServeEntry) == (sizeof(void*) == 4 ? 60u : 64u),
              "source entries must retain every field without old padding");
static_assert(alignof(OtaManager::ServeEntry) >= alignof(MotaSource*), "unaligned source pointer");
static_assert(offsetof(OtaManager::ServeEntry, desc) % alignof(MotaDesc) == 0, "unaligned descriptor");
static_assert(offsetof(OtaManager::ServeEntry, target_id) % alignof(uint32_t) == 0, "unaligned word");
static_assert(sizeof(OtaStoreQspiNrf52) > 8192u, "both staging page buffers must remain");
int main() {
  OtaManager::ServeEntry row;
  assert(row.desc.target_id == 0 && row.desc.fw_version == 0 && row.desc.source_caps == 0);
  assert(row.desc.mid[0] == 0 && row.desc.mid[3] == 0 && row.desc.total_size == 0);
  row.mid[0] = 17; row.target_id = 123; row.fw_version = 456;
  row.have_count = 789; row.src = nullptr; row.src_idx = 255;
  row.codec_id = 2; row.flags = 3; row.is_self = true;
  row.desc.target_id = 987; row.desc.payload_size = 654;
  const OtaManager::ServeEntry copy = row;
  assert(copy.mid[0] == 17 && copy.target_id == 123 && copy.fw_version == 456);
  assert(copy.have_count == 789 && copy.src == nullptr && copy.src_idx == 255);
  assert(copy.codec_id == 2 && copy.flags == 3 && copy.is_self);
  assert(copy.desc.target_id == 987 && copy.desc.payload_size == 654);
}
'''
        with tempfile.TemporaryDirectory(prefix="ota-layout-") as directory:
            work = Path(directory)
            fixture = work / "layout.cpp"
            fixture.write_text(source, encoding="ascii")
            flags = ["-std=c++17", "-DNRF52_PLATFORM=1", "-DOTA_QSPI_STORE=1",
                     "-DOTA_QSPI_EXPECTED_SIZE=2097152UL", "-I", str(ROOT / "src")]
            compiler = shutil.which("c++") or shutil.which("g++")
            self.assertIsNotNone(compiler)
            binary = work / "layout"
            result = subprocess.run([compiler, *flags, "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                                     str(fixture), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            subprocess.run([str(binary)], check=True, timeout=10)
            arm = shutil.which("arm-none-eabi-g++")
            if arm:
                result = subprocess.run([arm, *flags, "-mcpu=cortex-m4", "-mthumb", "-c", str(fixture),
                                         "-o", str(work / "layout-arm.o")], capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_bitmap_geometry_and_boundary_accesses(self):
        header = (ROOT / "src/helpers/ota/OtaStoreQspiNrf52.h").read_text()
        source = (ROOT / "src/helpers/ota/OtaStoreQspiNrf52.cpp").read_text()
        constants = header.split("  static const uint32_t PAGE =", 1)[1]
        constants = "  static const uint32_t PAGE =" + constants.split(
            "  static const uint32_t INVALID_PAGE", 1)[0]
        member = next(line.strip() for line in header.splitlines() if "uint8_t _known_pages[" in line)
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "bool OtaStoreQspiNrf52::pageKnown(uint32_t page) const",
            "void OtaStoreQspiNrf52::setPageKnown(uint32_t page)"))
        fixture = """
#include <stdint.h>
#include <assert.h>
#include <string.h>
class Bitmap {
public:
CONSTANTS
  uint8_t before[8];
  MEMBER
  uint8_t after[8];
  bool pageKnown(uint32_t page) const;
  void setPageKnown(uint32_t page);
};
METHODS
int main() {
  Bitmap value;
  static_assert(sizeof(value._known_pages) == EXPECT_BITMAP_BYTES, "bitmap was not sized to accepted capacity");
  static_assert(sizeof(value._known_pages) > 0u, "zero-length bitmap");
  memset(value.before, 0xA7, sizeof(value.before));
  memset(value.after, 0xD3, sizeof(value.after));
  memset(value._known_pages, 0, sizeof(value._known_pages));
  for (uint32_t page = 0; page < Bitmap::MAX_PAGES; ++page) {
    assert(!value.pageKnown(page)); value.setPageKnown(page); assert(value.pageKnown(page));
    if (page + 1 < Bitmap::MAX_PAGES) assert(!value.pageKnown(page + 1));
  }
  uint8_t snapshot[sizeof(value._known_pages)];
  memcpy(snapshot, value._known_pages, sizeof(snapshot));
  const uint32_t invalid[] = {Bitmap::MAX_PAGES, Bitmap::MAX_PAGES + 1u, UINT32_MAX};
  for (uint32_t page : invalid) { assert(!value.pageKnown(page)); value.setPageKnown(page); }
  assert(memcmp(snapshot, value._known_pages, sizeof(snapshot)) == 0);
  for (uint8_t byte : value.before) assert(byte == 0xA7);
  for (uint8_t byte : value.after) assert(byte == 0xD3);
}
""".replace("CONSTANTS", constants).replace("MEMBER", member).replace(
            "METHODS", methods.replace("OtaStoreQspiNrf52::", "Bitmap::"))
        compiler = shutil.which("c++") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="ota-bitmap-") as directory:
            work = Path(directory)
            path = work / "bitmap.cpp"
            path.write_text(fixture, encoding="ascii")
            for mib in (None, 1, 2, 4, 8, 16):
                with self.subTest(expected_mib=mib):
                    binary = work / ("generic" if mib is None else str(mib))
                    flags = [] if mib is None else [f"-DOTA_QSPI_EXPECTED_SIZE={mib << 20}UL"]
                    expected = (16 if mib is None else mib) * 32
                    subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-fsanitize=address,undefined", "-fno-pie", "-no-pie", *flags,
                                    f"-DEXPECT_BITMAP_BYTES={expected}", str(path), "-o", str(binary)],
                                   check=True, capture_output=True, text=True, timeout=30)
                    subprocess.run([str(binary)], check=True, timeout=10)
            for invalid in (0, 1, 4096, 1048575, 3145728, 33554432, (1 << 32) + (2 << 20)):
                with self.subTest(invalid=invalid):
                    result = subprocess.run([compiler, "-std=c++17", "-c", str(path),
                                             f"-DOTA_QSPI_EXPECTED_SIZE={invalid}UL",
                                             "-DEXPECT_BITMAP_BYTES=1", "-o", str(work / "invalid.o")],
                                            capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("expected QSPI capacity must match", result.stderr)


if __name__ == "__main__":
    unittest.main()
