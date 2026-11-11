"""Exact-image stock bootstrap inventory and the shared C++ migration gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from nrf52_legacy_bootloaders import _RECORDS, legacy_bootloaders_for
from cpp_source import body


SOURCE = r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "helpers/ota/BootloaderMigrationPolicy.h"
using namespace mesh::ota;
using namespace mesh::ota::migration;
int main() {
  const uint8_t approved[][32] = {{1,2,3}, {4,5,6}};
  uint8_t digest[32]; std::memcpy(digest,approved[1],32);
  assert(legacy_digest_allowed(digest,approved,2));
  assert(!legacy_digest_allowed(digest,approved,1));
  assert(!legacy_digest_allowed(digest,approved,0));
  assert(!legacy_digest_allowed(digest,approved,33));
  assert(!legacy_digest_allowed(nullptr,approved,2));
  assert(!legacy_digest_allowed(digest,nullptr,2));
  for (unsigned i=0;i<32;++i) {
    digest[i]^=1; assert(!legacy_digest_allowed(digest,approved,2)); digest[i]^=1;
  }
  OtaBootloaderIdentity stock,modern;
  modern.present=modern.crc_ok=modern.continuity_present=true;
  modern.image_start=BOOT_BASE; modern.image_size=BOOT_SIZE;
  modern.board_id=0x239A0029; std::strcpy(modern.device_name,"4631_DFU");
  modern.boot_version=0x02040BFF; modern.softdevice_family=140;
  modern.softdevice_fwid=0xB6; modern.app_base=0x26000; modern.layout_abi=1;
  auto candidate=modern; candidate.boot_version=0x02040CFF;
  const char name[16]="4631_DFU";
  assert(installed_source_allowed(stock,InstalledVariant::Unknown,true,0x239A0029,name,0x26000,0xB6));
  assert(!installed_source_allowed(stock,InstalledVariant::Unknown,false,0x239A0029,name,0x26000,0xB6));
  assert(!installed_source_allowed(stock,InstalledVariant::Recovery,true,0x239A0029,name,0x26000,0xB6));
  assert(installed_can_migrate(stock,candidate,true,true,0x26000,0xB6));
  assert(!installed_can_migrate(stock,candidate,false,true,0x26000,0xB6));
  assert(!installed_can_migrate(stock,candidate,true,false,0x26000,0xB6));
  assert(!installed_can_migrate(stock,candidate,true,true,0x27000,0x123));
  assert(installed_source_allowed(modern,InstalledVariant::Normal,false,0x239A0029,name,0x26000,0xB6));
  assert(!installed_source_allowed(modern,InstalledVariant::Recovery,true,0x239A0029,name,0x26000,0xB6));
  assert(!installed_source_allowed(modern,InstalledVariant::Unknown,true,0x239A0029,name,0x26000,0xB6));
  for (unsigned fault=0;fault<6;++fault) {
    auto wrong=modern;
    if(fault==0)wrong.crc_ok=false;
    if(fault==1)wrong.board_id=0x28860044;
    if(fault==2)std::strcpy(wrong.device_name,"3401_DFU");
    if(fault==3)wrong.continuity_present=false;
    if(fault==4)wrong.app_base=0x27000;
    if(fault==5)wrong.softdevice_fwid=0x123;
    assert(!installed_source_allowed(wrong,InstalledVariant::Normal,true,0x239A0029,name,0x26000,0xB6));
  }
  // XIAO base and Sense use distinct identities; only an exact match or a
  // stock hash approved for that particular candidate may proceed.
  const char xiao_name[16]="XIAO_DFU";
  for (uint32_t id : {OTA_XIAO_BOARD_ID_BASE, OTA_XIAO_BOARD_ID_SENSE}) {
    auto xiao=modern; xiao.board_id=id;
    std::memset(xiao.device_name,0,sizeof(xiao.device_name));
    std::strcpy(xiao.device_name,xiao_name);
    xiao.app_base=0x27000; xiao.softdevice_fwid=0x123;
    auto xiao_candidate=xiao; xiao_candidate.boot_version=0x02040CFF;
    assert(installed_source_allowed(xiao,InstalledVariant::Normal,false,id,xiao_name,0x27000,0x123));
    assert(installed_source_allowed(stock,InstalledVariant::Unknown,true,id,xiao_name,0x27000,0x123));
    assert(!installed_source_allowed(stock,InstalledVariant::Unknown,false,id,xiao_name,0x27000,0x123));
    assert(installed_can_migrate(xiao,xiao_candidate,true,true,0x27000,0x123));
    assert(installed_can_migrate(stock,xiao_candidate,true,true,0x27000,0x123));
    assert(!installed_can_migrate(stock,xiao_candidate,true,false,0x27000,0x123));
    auto wrong=xiao; wrong.board_id=id==OTA_XIAO_BOARD_ID_BASE
        ? OTA_XIAO_BOARD_ID_SENSE : OTA_XIAO_BOARD_ID_BASE;
    assert(!installed_source_allowed(wrong,InstalledVariant::Normal,true,id,xiao_name,0x27000,0x123));
    assert(!installed_can_migrate(wrong,xiao_candidate,true,true,0x27000,0x123));
  }
  std::vector<uint8_t> image(BOOT_SIZE,0xFF);
  std::memcpy(image.data()+10,"UF2 Bootloader 0.4.3\r\n",22);
  assert(installed_variant(image.data(),image.size(),0)==InstalledVariant::Unknown);
  std::memcpy(image.data()+100,"R_0x02040CFF",12);
  assert(installed_variant(image.data(),image.size(),0)==InstalledVariant::Recovery);
}
'''

UTILS = '''#pragma once
#include <stdint.h>
#include <stddef.h>
namespace mesh { class Utils { public:
static void sha256(uint8_t*,size_t,const uint8_t*,int);
static void sha256(uint8_t*,size_t,const uint8_t*,int,const uint8_t*,int);
}; }
'''


class LegacyBootloaderTests(unittest.TestCase):
    def test_every_record_matches_independent_public_stock_reader_audit(self):
        artifacts = json.loads((ROOT / "test/fixtures/nrf52_bootloader_version/stock_images.json").read_text())["artifacts"]
        self.assertEqual(len(_RECORDS), 16)
        for record in _RECORDS:
            matches = [item for item in artifacts if item["url"] == record["source_url"]]
            self.assertEqual(len(matches), 1, record["name"])
            source = matches[0]
            self.assertEqual(record["version"], source["version"])
            self.assertEqual(record["image_sha256"], source["image_sha256"])
            self.assertEqual(record["artifact_sha256"], source["sha256"])
            self.assertEqual((source["start"], source["size"]), ("0xf4000", 40960))

    def test_factory_rak_images_work_for_exact_3401_and_4631_targets(self):
        for hardware, profile in (("RAK_3401", "wiscore_rak3401_auto"),
                                  ("RAK_4631", "wiscore_rak4631_auto")):
            records = legacy_bootloaders_for(profile, hardware, 0x26000, 0xB6)
            stock_records = [item for item in records if item in _RECORDS]
            self.assertEqual(len(stock_records), 5)
            stock = next(item for item in records if item["version"] == "0.4.3")
            self.assertEqual(stock["image_sha256"],
                             "6cbfa569a05cbd232dabe4a654abe9c04b7a03ab457615028d28f2d1329e58d2")
        neo = legacy_bootloaders_for("wiscore_rak4631_auto", "R1Neo", 0x26000, 0xB6)
        self.assertEqual(len(neo), 1)
        self.assertIn("muzi-works", neo[0]["source_url"])
        tag = legacy_bootloaders_for("wismesh_tag", "RAK_WisMesh_Tag", 0x26000, 0xB6)
        self.assertTrue(tag)
        self.assertTrue({item["image_sha256"] for item in tag}.isdisjoint(
            item["image_sha256"] for item in records))
        self.assertEqual(legacy_bootloaders_for("wiscore_rak4631_auto", "RAK_3401", 0x26000, 0xB6), [])
        self.assertEqual(legacy_bootloaders_for("wiscore_rak3401_auto", "RAK_4631", 0x26000, 0xB6), [])

    def test_shared_module_or_wrong_softdevice_cannot_inherit_a_stock_hash(self):
        for record in _RECORDS:
            profile, hardware = record["profiles"][0], record["hardware"][0]
            self.assertEqual(legacy_bootloaders_for(profile, hardware, record["app_base"] ^ 0x1000, record["softdevice_fwid"]), [])
            self.assertEqual(legacy_bootloaders_for(profile, hardware, record["app_base"], record["softdevice_fwid"] ^ 1), [])
            self.assertEqual(legacy_bootloaders_for(profile, "Unreviewed", record["app_base"], record["softdevice_fwid"]), [])
        stock_hashes = {record["image_sha256"] for record in _RECORDS}
        for profile, hardware, base, fwid in (
                ("heltec_t096", "Heltec_t096", 0x26000, 0xB6),
                ("xiao_nrf52840_ble", "ikoka_stick_nrf_33dbm", 0x27000, 0x123),
                ("wio_tracker_l1", "WioTrackerL1-1W", 0x27000, 0x123),
                ("sensecap_solar_p1", "SenseCap_Solar", 0x27000, 0x123)):
            result = legacy_bootloaders_for(profile, hardware, base, fwid)
            self.assertTrue(stock_hashes.isdisjoint(item["image_sha256"] for item in result))
        self.assertEqual(legacy_bootloaders_for("xiao_nrf52840_ble", "ikoka_stick_nrf_33dbm", 0x27000, 0x123), [])

    def test_xiao_factory_hashes_are_bound_to_the_matching_variant(self):
        base = legacy_bootloaders_for("xiao_nrf52840_ble", "Xiao_nrf52", 0x27000, 0x123)
        sense = legacy_bootloaders_for("xiao_nrf52840_ble_sense", "Xiao_nrf52_Sense", 0x27000, 0x123)
        self.assertIn("77941feb9ea9d99acaeb1f815a905e23f618381390d40f38374a894505e6798f",
                      {item["image_sha256"] for item in base})
        self.assertIn("894f2e6df7663fcc5c398e210db7f2e7f21d3da53036e0a11576909c849f78fb",
                      {item["image_sha256"] for item in sense})
        self.assertTrue(all("Plus" not in item["name"] for item in base + sense))
        self.assertTrue({item["image_sha256"] for item in base}.isdisjoint(
            item["image_sha256"] for item in sense))
        self.assertEqual(legacy_bootloaders_for("xiao_nrf52840_ble", "Xiao_nrf52_Sense", 0x27000, 0x123), [])
        self.assertEqual(legacy_bootloaders_for("xiao_nrf52840_ble_sense", "Xiao_nrf52", 0x27000, 0x123), [])

    def test_registry_results_are_not_mutable_global_authorization(self):
        result = legacy_bootloaders_for("wiscore_rak4631_auto", "R1Neo", 0x26000, 0xB6)
        result[0]["hardware"].append("OTHER")
        result[0]["image_sha256"] = "0" * 64
        again = legacy_bootloaders_for("wiscore_rak4631_auto", "R1Neo", 0x26000, 0xB6)
        self.assertNotIn("OTHER", again[0]["hardware"])
        self.assertNotEqual(again[0]["image_sha256"], "0" * 64)

    def test_manual_and_automatic_requests_share_the_source_gate_and_final_copy_rechecks(self):
        source = (ROOT / "src/helpers/ota/BootloaderMigration.cpp").read_text()
        self.assertIn("installed_allows_update()", body(source, "void bootloader_migration_tick("))
        request = body(source, "bool bootloader_migration_request(")
        self.assertLess(request.index("installed_allows_update()"), request.index("lock_automatic_for_firmware()"))
        install = body(source, "void install()")
        self.assertIn("installed_source_allowed(", install)
        self.assertIn("installed_can_migrate(", install)
        self.assertLess(install.index("installed_source_allowed("), install.index("SD_MBR_COMMAND_COPY_BL"))

    def test_real_cpp_policy_keeps_modern_identity_recovery_and_layout_guards(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="stock-bootloader-policy-") as directory:
            path = Path(directory)
            (path / "Utils.h").write_text(UTILS)
            (path / "test.cpp").write_text(SOURCE)
            binary = path / ("policy.exe" if os.name == "nt" else "policy")
            sanitize = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
            subprocess.run([compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *sanitize,
                            "-I", str(path), "-I", str(ROOT / "src"), "-I", str(ROOT / "src/helpers/ota"),
                            str(path / "test.cpp"), "-o", str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
