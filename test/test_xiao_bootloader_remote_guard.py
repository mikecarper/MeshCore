"""Exercise the real bootloader confirmation gate without PlatformIO."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class XiaoBootloaderRemoteGuardTest(unittest.TestCase):
    def test_confirmation_gate_and_both_apply_paths(self):
        source = r'''
#include <cassert>
#include <cstring>
#include "helpers/ota/OtaBootloaderUpdate.h"
using namespace mesh::ota;
int main() {
  uint8_t zero[32] = {}, mid[4] = {}, hw[32] = {};
  MotaManifest m;
  m.format_ver = MOTA_BOOT_FORMAT_VER;
  m.flags = MFLAG_FULL | MFLAG_SIGNED | MFLAG_BOOTLOADER;
  m.codec_id = CODEC_FULL;
  m.image_size = m.payload_size = OTA_BOOT_IMAGE_SIZE;
  m.block_size_log2 = 10;
  m.block_count = OTA_BOOT_IMAGE_SIZE / 1024;
  m.base_hash = m.image_hash = m.merkle_root = zero;
  m.hw_id = hw;
  OtaBootloaderIdentity id;
  id.present = id.crc_ok = true;
  memcpy(id.device_name, OTA_XIAO_BOOT_DEVICE_NAME, 16);
  for (uint32_t board : {OTA_XIAO_BOARD_ID_BASE, OTA_XIAO_BOARD_ID_SENSE}) {
    id.board_id = board;
    m.target_id = board;
    assert(ota_xiao_bootloader_hw_id(board, hw));
    assert(ota_bootloader_confirmation_gate(m, id, mid, mid, zero)
           == OTA_BOOT_CONFIRM_OK);
    OtaBootloaderIdentity candidate = id;
    candidate.image_start = id.image_start = OTA_BOOT_IMAGE_START;
    candidate.image_size = id.image_size = OTA_BOOT_IMAGE_SIZE;
    assert(ota_bootloader_identity_matches(id, candidate));
    candidate.board_id = board == OTA_XIAO_BOARD_ID_BASE
        ? OTA_XIAO_BOARD_ID_SENSE : OTA_XIAO_BOARD_ID_BASE;
    assert(!ota_bootloader_identity_matches(id, candidate));
    m.target_id = candidate.board_id;
    assert(ota_bootloader_confirmation_gate(m, id, mid, mid, zero)
           == OTA_BOOT_CONFIRM_TARGET);
    m.target_id = board;
    assert(ota_xiao_bootloader_hw_id(candidate.board_id, hw));
    assert(ota_bootloader_confirmation_gate(m, id, mid, mid, zero)
           == OTA_BOOT_CONFIRM_HW_ID);
  }
  // The Solar P1 shares the base ID but has a distinct device name.
  id.board_id = OTA_XIAO_BOARD_ID_BASE;
  memcpy(id.device_name, OTA_SENSECAP_BOOT_DEVICE_NAME, 16);
  assert(ota_bootloader_hw_id(id, hw));
  m.target_id = ota_bootloader_target_id(id);
  assert(ota_bootloader_confirmation_gate(m, id, mid, mid, zero)
         == OTA_BOOT_CONFIRM_OK);
  // A generic board using a shared ID must remain eligible too.
  id.board_id = 0x239A0029;
  memset(id.device_name, 0, sizeof(id.device_name));
  memcpy(id.device_name, "4631_DFU", 8);
  assert(ota_bootloader_hw_id(id, hw));
  m.target_id = ota_bootloader_target_id(id);
  assert(ota_bootloader_confirmation_gate(m, id, mid, mid, zero)
         == OTA_BOOT_CONFIRM_OK);
}
'''
        mock = r'''
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace mesh { struct Utils {
  static void sha256(uint8_t* p, size_t n, const uint8_t*, int) { memset(p, 0, n); }
  static void sha256(uint8_t* p, size_t n, const uint8_t*, int,
                     const uint8_t*, int) { memset(p, 0, n); }
}; }
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "Utils.h").write_text(mock)
            (path / "test.cpp").write_text("#include <initializer_list>\n" + source)
            binary = path / "test.exe"
            result = subprocess.run(["g++", "-std=c++17", "-I", str(path),
                                     "-I", str(ROOT / "src"), str(path / "test.cpp"),
                                     "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
