"""Board-scoped QSPI bootloader update settings and identity checks."""
import configparser
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/mota"))
import motalib as ml
PROFILES = (
    ("wio-tracker-l1", "OTA_QSPI_BOOT_WIO_L1", 0x28861667, "WTL1_DFU", 0x27000,
     ("WioTrackerL1_repeater", "WioTrackerL1_repeater_lora_ota_no_external_sensors",
      "WioTrackerL1_room_server_lora_ota")),
    ("lilygo_techo", "OTA_QSPI_BOOT_TECHO", 0x239A0029, "LGTE_DFU", 0x26000,
     ("LilyGo_T-Echo_repeater", "LilyGo_T-Echo_room_server_lora_ota")),
    ("lilygo_techo_lite", "OTA_QSPI_BOOT_TECHO_LITE", 0x239A00DA, "LTEL_DFU", 0x26000,
     ("LilyGo_T-Echo-Lite_repeater_lora_ota", "LilyGo_T-Echo-Lite_room_server_lora_ota")),
    ("thinknode_m1", "OTA_QSPI_BOOT_THINKNODE_M1", 0x239A00DA, "TNM1_DFU", 0x26000,
     ("ThinkNode_M1_repeater", "ThinkNode_M1_room_server_lora_ota")),
    ("thinknode_m6", "OTA_QSPI_BOOT_THINKNODE_M6", 0x239A00DA, "TNM6_DFU", 0x26000,
     ("ThinkNode_M6_repeater", "ThinkNode_M6_room_server_lora_ota")),
    ("wio-tracker-l1-1w", "OTA_QSPI_BOOT_WIO_L1", 0x28861667, "WTL1_DFU", 0x27000,
     ("WioTrackerL1-1W_repeater_lora_ota", "WioTrackerL1-1W_room_server_lora_ota")),
)

UTILS = '''#pragma once
#include <stdint.h>
#include <stddef.h>
namespace mesh { class Utils { public:
static void sha256(uint8_t*,size_t,const uint8_t*,int);
static void sha256(uint8_t*,size_t,const uint8_t*,int,const uint8_t*,int);
}; }
'''
SOURCE = r'''
#include <cassert>
#include <cstdlib>
#include <cstring>
#include "helpers/ota/OtaBootloaderUpdate.h"
using namespace mesh::ota;
int main(int argc, char **argv) {
  assert(argc == 3);
  OtaBootloaderIdentity id;
  id.present = true; id.crc_ok = true;
  id.board_id = static_cast<uint32_t>(strtoul(argv[1], nullptr, 16));
  strncpy(id.device_name, argv[2], OTA_BOOT_DEVICE_NAME_SIZE);
  assert(ota_bootloader_identity_allowed_for_build(id));
  id.crc_ok = false; assert(!ota_bootloader_identity_allowed_for_build(id));
  id.crc_ok = true; id.board_id ^= 1; assert(!ota_bootloader_identity_allowed_for_build(id));
  id.board_id ^= 1; id.device_name[0] ^= 1; assert(!ota_bootloader_identity_allowed_for_build(id));
  id.device_name[0] ^= 1; id.device_name[15] = 'x';
  assert(!ota_bootloader_identity_allowed_for_build(id));
  return 0;
}
'''


class QspiBootloaderProfiles(unittest.TestCase):
    def test_package_tool_uses_matching_storage_and_softdevice(self):
        for _variant, _marker, board, name, base, _envs in PROFILES:
            with self.subTest(name=name):
                self.assertEqual(ml.bootloader_qualified_storage_profiles(board, name),
                                 (ml.BOOT_STORAGE_QSPI_UPDATE,))
                family, fwid, app_base, abi = ml.bootloader_qualified_platform_profile(board, name)
                self.assertEqual((family, app_base, abi), (140, base, 1))
                self.assertEqual(fwid, 0x123 if base == 0x27000 else 0xB6)

    @unittest.skipUnless(os.environ.get("OTAFIX_MOTA_ZIP"), "published OTAFIX mOTA archive not supplied")
    def test_published_signed_packages_match_board_and_profile(self):
        archive = zipfile.ZipFile(os.environ["OTAFIX_MOTA_ZIP"])
        self.addCleanup(archive.close)
        published = ("wio_tracker_l1", "lilygo_techo", "lilygo_techo_lite", "thinknode_m1", "thinknode_m6",
                     "wio_tracker_l1")
        for (_variant, _marker, board, name, base, _envs), published_name in zip(PROFILES, published):
            member = f"mota/update-{published_name}_bootloader-v0.11.0-OTAFIX2.4.12.mota"
            with self.subTest(member=member):
                parsed = ml.parse_container(archive.read(member))
                official_key = bytes.fromhex(
                    "272564CC588D3D122285A15E6E2566D2ABE7177BB7EA1D1E41B23B29F0F85D2D")
                self.assertEqual(ml.verify(parsed, expect_pub=official_key), [])
                identity = ml.parse_bootloader_identity(parsed.payload)
                self.assertEqual((identity.board_id, identity.device_name, identity.app_base), (board, name, base))
                self.assertEqual(ml.bootloader_caps_storage(parsed.payload), ml.BOOT_STORAGE_QSPI_UPDATE)

    def test_enabled_roles_reserve_scratch_and_keep_raw_qspi(self):
        for variant, marker, _board, _name, base, envs in PROFILES:
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read(ROOT / "variants" / variant / "platformio.ini")
            for env in envs:
                with self.subTest(env=env):
                    section = config["env:" + env]
                    flags = section["build_flags"]
                    self.assertIn("${nrf52_qspi_ota.build_flags}", flags)
                    self.assertIn("OTA_QSPI_BOOTLOADER_UPDATE=1", flags)
                    self.assertIn(marker + "=1", flags)
                    self.assertNotIn("QSPIFLASH", flags)
                    self.assertEqual(int(section["board_upload.maximum_size"]), 0xE0000 - base)
                    linker = (ROOT / section["board_build.ldscript"]).read_text()
                    self.assertIn(f"ORIGIN = 0x{base:X}, LENGTH = 0xE0000 - 0x{base:X}", linker)

    def test_identity_gate_uses_exact_board_and_name(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / "Utils.h").write_text(UTILS)
            (tmp / "test.cpp").write_text(SOURCE)
            for _variant, marker, board, name, _base, _envs in PROFILES:
                with self.subTest(marker=marker):
                    binary = tmp / marker
                    subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                                    "-D" + marker, "-I", str(tmp), "-I", str(ROOT / "src"),
                                    str(tmp / "test.cpp"), "-o", str(binary)], check=True,
                                   text=True)
                    subprocess.run([str(binary), f"{board:X}", name], check=True)


if __name__ == "__main__":
    unittest.main()
