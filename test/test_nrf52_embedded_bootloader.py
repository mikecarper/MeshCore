"""Offline tests of the embedded bootloader package and build hook."""
import binascii
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools/mota"))
import nrf52_embedded_bootloader as EMBED
from nrf52_legacy_bootloaders import legacy_bootloaders_for
import motalib


def boot_image(*, v7=False, name=None, board_id=None, storage=None,
               boot_version=EMBED.BOOT_VERSION, app_base=None):
    """Build the real on-flash envelope, not a mocked identity object."""
    name = name or ("XIAO_DFU" if v7 else "4631_DFU")
    board_id = board_id or (0x28860044 if v7 else 0x239A0029)
    storage = (0x0E if v7 else 0x0A) if storage is None else storage
    image = bytearray(b"\xff" * EMBED.BOOT_SIZE)
    struct.pack_into("<II", image, 0, 0x20030000, EMBED.BOOT_BASE + 0x101)
    image[0x100:0x110] = b"MOTABLDR" + struct.pack(
        "<HHBBBB", 3, motalib.BOOT_REQUIRED_APP_CODEC_MASK, storage, 0, 0, 0)
    if name == "4631_DFU":
        image[0x200:0x220] = (b"MOTARAMA" + struct.pack("<HHI", 1, 72, 65536)
                              + b"MOTASTOR" + struct.pack("<HHBBBB", 1, 16, 0x14, 0, 0, 0))
    elif name in ("X1INT_DFU", "SOLARINT_DFU"):
        image[0x200:0x210] = b"MOTARAMA" + struct.pack("<HHI", 1, 72, 65536)
    offset = EMBED.BOOT_SIZE - 76
    struct.pack_into("<8sHHIII16sI", image, offset, b"BLMFCRC1", 1, 44,
                     EMBED.BOOT_BASE, EMBED.BOOT_SIZE, board_id,
                     name.encode().ljust(16, b"\0"), 0)
    struct.pack_into("<8sHHIHHIHHI", image, offset + 44, b"BLM2SOFT", 2, 32,
                     boot_version, 140, 0x123 if v7 else 0xB6,
                     app_base or (0x27000 if v7 else 0x26000), 1, 0, 0)
    struct.pack_into("<I", image, offset + 40, zlib.crc32(image) & 0xFFFFFFFF)
    return bytes(image)


def candidate(image, *, v7=False, packet_bad=False, extras=()):
    firmware = b"\xa5" * 4096 + image
    crc = binascii.crc_hqx(firmware, 0xFFFF)
    init = {"device_type": 0x52, "device_revision": 52840,
            "application_version": 0, "softdevice_req": [0x123 if v7 else 0xB6],
            "firmware_crc16": crc}
    packet = struct.pack("<HHIHHH", 0x52, 52840, 0, 1, init["softdevice_req"][0], crc)
    if packet_bad:
        packet = packet[:-1] + bytes([packet[-1] ^ 1])
    document = {"manifest": {"dfu_version": 0.5, "softdevice_bootloader": {
        "bin_file": "firmware.bin", "dat_file": "firmware.dat", "sd_size": 4096,
        "bl_size": EMBED.BOOT_SIZE, "init_packet_data": init}}}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def member(name, data):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        member("manifest.json", json.dumps(document))
        member("firmware.bin", firmware)
        member("firmware.dat", packet)
        for name, data in extras:
            member(name, data)
    return output.getvalue()


def profile(data, *, v7=False, name=None, storage=None):
    slug = "xiao_nrf52840_ble" if v7 else "wiscore_rak4631_auto"
    filename = f"{slug}_bootloader-{EMBED.TAG}_s140_{'7.3.0' if v7 else '6.1.1'}.zip"
    return {"id": slug, "deviceName": name or ("XIAO_DFU" if v7 else "4631_DFU"),
            "storage": storage or ("qspi" if v7 else "adaptive"),
            "meshcoreHardware": ["Xiao_nrf52"] if v7 else ["RAK_4631", "R1Neo"],
            "files": {"zip": {"name": filename, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "url": f"https://github.com/{EMBED.REPOSITORY}/releases/download/{EMBED.TAG}/{filename}"}}}


class EmbeddedBootloaderTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="meshcore-embed-test-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.cache = self.project / ".pio/otafix-cache"
        self.cache.mkdir(parents=True)
        self.manifest = {"schemaVersion": 1, "repository": EMBED.REPOSITORY,
                         "tag": EMBED.TAG, "version": EMBED.VERSION,
                         "prerelease": False, "profiles": []}
        for v7 in (False, True):
            data = candidate(boot_image(v7=v7), v7=v7)
            item = profile(data, v7=v7)
            asset = item["files"]["zip"]
            (self.cache / (asset["sha256"] + "-" + asset["name"])).write_bytes(data)
            self.manifest["profiles"].append(item)
        self.pin_manifest()
        self.network = patch.object(EMBED, "urlopen", side_effect=AssertionError("network access in offline test"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def pin_manifest(self):
        path = self.project / "docs/_data/bootloader_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.manifest).encode()
        path.write_bytes(data)
        pin = patch.object(EMBED, "MANIFEST_SHA256", hashlib.sha256(data).hexdigest())
        pin.start()
        self.addCleanup(pin.stop)

    def emit(self, *, v7=False, **kwargs):
        target = "Xiao_nrf52_room_server" if v7 else "RAK_4631_repeater"
        options = dict(flags="-DNRF52_PLATFORM -DENABLE_OTA=1",
                       src_filter="+<../examples/simple_room_server>" if v7
                                  else "+<../examples/simple_repeater>",
                       linker="nrf52840_s140_v7.ld" if v7 else "nrf52840_s140_v6.ld")
        options.update(kwargs)
        return EMBED.emit_resource(self.project, target, self.project / "generated", **options)

    def test_preview_catalog_binds_each_board_identity_and_version(self):
        manifest = EMBED.load_manifest(ROOT, EMBED.PREVIEW_VERSION)
        self.assertTrue(manifest["prerelease"])
        self.assertEqual({p["id"] for p in manifest["profiles"]}, set(EMBED.PREVIEW_PROFILE_IDENTITIES))
        for item in manifest["profiles"]:
            with self.subTest(board=item["id"]):
                self.assertIs(item["hardwareTested"], False)
                board_id, name = EMBED.PREVIEW_PROFILE_IDENTITIES[item["id"]]
                v7 = item["id"] == "meshtracker_x1"
                storage = 0x0A if item["storage"] == "internal" else 0x0E
                image = boot_image(v7=v7, name=name, board_id=board_id, storage=storage,
                                   boot_version=EMBED.PREVIEW_BOOT_VERSION)
                archive = candidate(image, v7=v7)
                profile = copy.deepcopy(item)
                profile["files"]["zip"]["size"] = len(archive)
                profile["files"]["zip"]["sha256"] = hashlib.sha256(archive).hexdigest()
                self.assertEqual(EMBED.validate_candidate_zip(
                    archive, profile, version=EMBED.PREVIEW_VERSION)[0], image)
                wrong = boot_image(v7=v7, name=name, board_id=board_id, storage=storage,
                                   boot_version=EMBED.BOOT_VERSION)
                bad = candidate(wrong, v7=v7)
                profile["files"]["zip"]["size"] = len(bad)
                profile["files"]["zip"]["sha256"] = hashlib.sha256(bad).hexdigest()
                with self.assertRaisesRegex(ValueError, "identity/version"):
                    EMBED.validate_candidate_zip(bad, profile, version=EMBED.PREVIEW_VERSION)
                target = item["meshcoreHardware"][0] + "_repeater_lora_ota_no_external_sensors"
                self.assertEqual(EMBED.select_profile(manifest, target)[0]["id"], item["id"])

    def test_v6_v7_resources_link_and_contain_exact_candidate(self):
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "host g++ is required to verify the generated resource")
        for v7 in (False, True):
            with self.subTest(v7=v7):
                result = self.emit(v7=v7)
                output = self.project / "generated"
                image = boot_image(v7=v7)
                self.assertEqual((output / "BootloaderMigrationResource.bin").read_bytes(), image)
                self.assertEqual(result["candidate_sha256"], hashlib.sha256(image).hexdigest())
                self.assertEqual(result["version"], "2.4.12")
                self.assertEqual(result["softdevice_fwid"], 0x123 if v7 else 0xB6)
                self.assertEqual(result["app_base"], 0x27000 if v7 else 0x26000)
                expected_stock = legacy_bootloaders_for(result["profile"], result["hardware"],
                                                        result["app_base"], result["softdevice_fwid"])
                self.assertEqual(result["stock_legacy"], expected_stock)
                self.assertEqual(result, json.loads((output / "BootloaderMigrationResource.json").read_text()))
                harness = output / "main.cpp"
                harness.write_text('''#include "BootloaderMigrationResource.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif
int main() {
#ifdef _WIN32
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  const uint8_t expected_hash[] = {%s};
  if ((uintptr_t)migration_candidate %% 4 || migration_has_legacy ||
      migration_app_base != %d || migration_softdevice_fwid != %d ||
      migration_board_id != %d || migration_boot_version != %d ||
      strcmp(migration_device_name, "%s") ||
      memcmp(expected_hash, migration_candidate_sha256, 32)) return 1;
  for (uint8_t byte : migration_legacy_sha256) if (byte) return 2;
  const uint8_t stock_hashes[][32] = {%s};
  if (migration_stock_count != %d ||
      memcmp(stock_hashes, migration_stock_sha256, sizeof(stock_hashes))) return 4;
  return fwrite(migration_candidate, 1, 40960, stdout) == 40960 ? 0 : 3;
}
''' % (",".join(map(str, hashlib.sha256(image).digest())), result["app_base"],
       result["softdevice_fwid"], result["board_id"], EMBED.BOOT_VERSION, result["device_name"],
       ",".join("{" + ",".join(map(str, bytes.fromhex(item["image_sha256"]))) + "}"
                for item in expected_stock), len(expected_stock)))
                executable = output / ("resource.exe" if os.name == "nt" else "resource")
                subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                str(output / "BootloaderMigrationResource.cpp"), str(harness),
                                "-o", str(executable)], check=True, capture_output=True)
                self.assertEqual(subprocess.check_output([str(executable)]), image)

    def test_manifest_pin_and_release_identity_are_both_required(self):
        path = self.project / "docs/_data/bootloader_manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "pinned"):
            EMBED.load_manifest(self.project)
        self.manifest["version"] = "2.4.11"
        self.pin_manifest()
        with self.assertRaisesRegex(ValueError, "release identity"):
            EMBED.load_manifest(self.project)

    def test_no_reviewed_stock_image_emits_an_inert_zero_count_resource(self):
        with patch.object(EMBED, "legacy_bootloaders_for", return_value=[]):
            result = self.emit()
        self.assertEqual(result["stock_legacy"], [])
        source = (self.project / "generated/BootloaderMigrationResource.cpp").read_text()
        self.assertIn("const uint32_t migration_stock_count = 0;", source)
        self.assertIn("alignas(4) const uint8_t migration_stock_sha256[1][32]", source)
        row = source.split("migration_stock_sha256[1][32] = {\n", 1)[1].split("};", 1)[0]
        self.assertEqual(row.count("0x00"), 32)

    def test_whole_zip_pin_and_internal_crc_are_independent_checks(self):
        good = candidate(boot_image())
        with self.assertRaisesRegex(ValueError, "pinned release"):
            EMBED.validate_candidate_zip(good[:-1] + bytes([good[-1] ^ 1]), profile(good))
        bad_image = bytearray(boot_image())
        bad_image[0x300] ^= 1
        data = candidate(bytes(bad_image))  # Valid ZIP hash and Legacy CRC, invalid BLMF CRC.
        with self.assertRaisesRegex(ValueError, "identity/version/vector/CRC"):
            EMBED.validate_candidate_zip(data, profile(data))
        data = candidate(boot_image(), packet_bad=True)
        with self.assertRaisesRegex(ValueError, "init metadata/firmware CRC"):
            EMBED.validate_candidate_zip(data, profile(data))

    def test_candidate_version_device_layout_and_capability_must_match(self):
        cases = [
            (boot_image(boot_version=0x02040BFF), {}, "identity/version"),
            (boot_image(), {"name": "OTHER_DFU"}, "identity/version"),
            (boot_image(app_base=0x27000), {}, "layout differs"),
            (boot_image(), {"storage": "qspi"}, "capability marker"),
        ]
        for image, options, message in cases:
            with self.subTest(message=message, options=options):
                data = candidate(image)
                with self.assertRaisesRegex(ValueError, message):
                    EMBED.validate_candidate_zip(data, profile(data, **options))
        data = candidate(boot_image(v7=True))
        with self.assertRaisesRegex(ValueError, "reviewed profile"):
            EMBED.validate_candidate_zip(data, profile(data, name="XIAO_DFU", storage="qspi"))

    def test_unreviewed_profile_is_rejected_even_if_asset_is_pinned(self):
        image = boot_image(v7=True, name="FUTURE_DFU", board_id=0x12345678)
        data = candidate(image, v7=True)
        self.assertIsNone(motalib.bootloader_qualified_storage_profiles(0x12345678, "FUTURE_DFU"))
        item = profile(data, v7=True, name="FUTURE_DFU")
        item["id"] = "future_board"
        with self.assertRaisesRegex(ValueError, "reviewed profile"):
            EMBED.validate_candidate_zip(data, item)
        with patch.dict(EMBED.PROFILE_IDENTITIES, {"future_board": (0x12345678, "FUTURE_DFU")}):
            actual, identity = EMBED.validate_candidate_zip(data, item)
        self.assertEqual(actual, image)
        self.assertEqual(identity.board_id, 0x12345678)
        self.assertIsNone(motalib.bootloader_qualified_storage_profiles(0x12345678, "FUTURE_DFU"))

    def test_xiao_and_sense_candidates_cannot_be_swapped(self):
        for profile_id, correct, wrong in (
            ("xiao_nrf52840_ble", 0x28860044, 0x28860045),
            ("xiao_nrf52840_ble_sense", 0x28860045, 0x28860044),
        ):
            for board_id in (correct, wrong):
                with self.subTest(profile=profile_id, board_id=board_id):
                    image = boot_image(v7=True, board_id=board_id)
                    data = candidate(image, v7=True)
                    item = profile(data, v7=True)
                    item["id"] = profile_id
                    if board_id == correct:
                        self.assertEqual(EMBED.validate_candidate_zip(data, item)[0], image)
                    else:
                        with self.assertRaisesRegex(ValueError, "reviewed profile"):
                            EMBED.validate_candidate_zip(data, item)

    def test_reviewed_identities_cover_pinned_release_profiles(self):
        profiles = json.loads((ROOT / "docs/_data/bootloader_manifest.json").read_text())["profiles"]
        self.assertEqual(set(EMBED.PROFILE_IDENTITIES), {p["id"] for p in profiles})
        for item in profiles:
            self.assertEqual(EMBED.PROFILE_IDENTITIES[item["id"]][1], item["deviceName"])

    def test_zip_member_geometry_is_bounded(self):
        for extra in (("unexpected.txt", b"x"), ("../firmware.bin", b"x")):
            data = candidate(boot_image(), extras=[extra])
            with self.subTest(extra=extra[0]), self.assertRaisesRegex(ValueError, "exactly one"):
                EMBED.validate_candidate_zip(data, profile(data))

    def test_cached_asset_is_revalidated_and_bad_download_is_not_cached(self):
        data = candidate(boot_image())
        item = profile(data)
        asset = item["files"]["zip"]
        cached = self.cache / (asset["sha256"] + "-" + asset["name"])
        self.assertEqual(EMBED.download_zip(item, self.cache), data)
        cached.write_bytes(b"x" * len(data))
        with patch.object(EMBED, "urlopen", return_value=io.BytesIO(data)) as download:
            self.assertEqual(EMBED.download_zip(item, self.cache), data)
            download.assert_called_once_with(asset["url"], timeout=60)
        cached.unlink()
        with patch.object(EMBED, "urlopen", return_value=io.BytesIO(b"x" * len(data))):
            with self.assertRaisesRegex(ValueError, "size/SHA-256"):
                EMBED.download_zip(item, self.cache)
        self.assertFalse(cached.exists())

    def test_effective_ota_flags_and_exact_source_role_fail_closed(self):
        for flags in ("-DNRF52_PLATFORM", "-DENABLE_OTA=1", "-DNRF52_PLATFORM -DENABLE_OTA=0",
                      "-DNRF52_PLATFORM -DENABLE_OTA=1 -UENABLE_OTA",
                      "-DNRF52_PLATFORM -DENABLE_OTA=1 -DOTA_SEEDER_ONLY=0",
                      "-DNRF52_PLATFORM -DENABLE_OTA=1 -DDISABLE_LORA_OTA=0"):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, "OTA receiver"):
                self.emit(flags=flags)
        for source in ("", "+<*>", "+<../examples/simple_room_server>",
                       "+<../examples/simple_repeater> +<../examples/companion_radio>",
                       "+<../examples/simple_repeater> -<../examples/simple_repeater>"):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "exact repeater"):
                self.emit(src_filter=source)

    def test_profile_role_uniqueness_and_retired_hardware_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "limited to repeater"):
            EMBED.select_profile(self.manifest, "RAK_4631_companion_radio_full")
        ambiguous = copy.deepcopy(self.manifest)
        ambiguous["profiles"].append(copy.deepcopy(ambiguous["profiles"][0]))
        with self.assertRaisesRegex(ValueError, "one exact"):
            EMBED.select_profile(ambiguous, "RAK_4631_repeater")
        retired = copy.deepcopy(self.manifest)
        retired["unavailableProfiles"] = [{"meshcoreHardware": ["RAK_4631"]}]
        with self.assertRaisesRegex(ValueError, "no qualified"):
            EMBED.select_profile(retired, "RAK_4631_repeater")

    def test_linker_layout_and_full_legacy_hash_are_required(self):
        with self.assertRaisesRegex(ValueError, "application linker"):
            self.emit(linker="nrf52840_s140_v7.ld")
        linker = self.project / "board.ld"
        linker.write_text("MEMORY { FLASH (rx) : ORIGIN = 0x26000, LENGTH = 0x90000 }")
        self.assertEqual(EMBED.application_base(self.project, linker), 0x26000)
        with self.assertRaisesRegex(ValueError, "cannot verify"):
            EMBED.application_base(self.project, "unknown.ld")
        legacy = bytes(range(256)) * 160
        path = self.project / "legacy.bin"
        path.write_bytes(legacy)
        with self.assertRaisesRegex(ValueError, "qualified MeshEnvy"):
            EMBED.load_legacy(path)
        digest = hashlib.sha256(legacy).hexdigest()
        with patch.object(EMBED, "LEGACY_SHA256", digest):
            result = self.emit(legacy_path=path)
            self.assertEqual(result["legacy_sha256"], digest)
            self.assertEqual(EMBED.load_legacy(path), legacy)
            path.write_bytes(legacy[:-1] + bytes([legacy[-1] ^ 1]))
            with self.assertRaisesRegex(ValueError, "qualified MeshEnvy"):
                EMBED.load_legacy(path)
            path.write_bytes(legacy[:-1])
            with self.assertRaisesRegex(ValueError, "qualified MeshEnvy"):
                EMBED.load_legacy(path)
        with self.assertRaisesRegex(ValueError, "restricted to RAK4631"):
            self.emit(v7=True, legacy_path=path)

    def test_overlay_identity_is_bound_to_its_physical_environment(self):
        base = "-DNRF52_PLATFORM -DENABLE_OTA=1"
        logical = "RAK_4631_repeater_lora_ota_no_external_sensors"
        result = self.emit(flags=base + " -DOTA_VARIANT='\"" + logical + "\"'")
        self.assertEqual(result["target"], logical)
        self.assertEqual(result["hardware"], "RAK_4631")
        self.assertEqual(result["profile"], "wiscore_rak4631_auto")
        result = self.emit(flags=base, cppdefines=[("OTA_VARIANT", '"' + logical + '"')])
        self.assertEqual(result["target"], logical)
        # R1Neo shares the RAK profile, but it is not the selected board.
        # Likewise, a different role or a malformed value must never create a
        # resource for the physical environment.
        for alias in ("R1Neo_repeater", "RAK_4631_room_server",
                      "Xiao_nrf52_sense_repeater", "RAK_4631_repeater_bad",
                      "", "RAK_4631_repeater/other"):
            with self.subTest(alias=alias), self.assertRaisesRegex(ValueError, "target"):
                self.emit(flags=base, cppdefines=[("OTA_VARIANT", alias)])

    def test_overlay_suffix_does_not_authorize_another_hardware_or_role(self):
        base = "-DNRF52_PLATFORM -DENABLE_OTA=1"
        logical = "RAK_4631_repeater_lora_ota_no_external_sensors"
        physical = {"id": "same"}
        overlay = {"id": "same"}
        parsed = {"sourceHardware": "RAK_4631", "role": "repeater"}
        for changed in ({"sourceHardware": "R1Neo", "role": "repeater"},
                        {"sourceHardware": "RAK_4631", "role": "room"}):
            with self.subTest(changed=changed), patch.object(
                    EMBED, "select_profile", side_effect=[(physical, parsed), (overlay, changed)]):
                with self.assertRaisesRegex(ValueError, "hardware or role"):
                    self.emit(flags=base, cppdefines=[("OTA_VARIANT", logical)])

    def test_trailing_underscore_and_room_overlay_remain_supported(self):
        base = "-DNRF52_PLATFORM -DENABLE_OTA=1"
        for physical, role, logical in (
                ("RAK_4631_repeater_", "repeater",
                 "RAK_4631_repeater_lora_ota_no_external_sensors"),
                ("RAK_4631_room_server_", "room",
                 "RAK_4631_room_server_lora_ota_no_external_sensors")):
            folder = "simple_repeater" if role == "repeater" else "simple_room_server"
            with self.subTest(physical=physical):
                result = EMBED.emit_resource(
                    self.project, physical, self.project / "generated",
                    flags=base, cppdefines=[("OTA_VARIANT", logical)],
                    src_filter=f"+<../examples/{folder}>", linker="nrf52840_s140_v6.ld")
                self.assertEqual(result["target"], logical)
                self.assertEqual(result["role"], role)
                self.assertEqual(result["hardware"], "RAK_4631")

    def test_pio_hook_requires_explicit_version_and_correct_mcu(self):
        class Environment(dict):
            def __init__(inner):
                super().__init__(PROJECT_DIR=str(self.project), PIOENV="RAK_4631_repeater",
                                 BUILD_FLAGS="-DNRF52_PLATFORM -DENABLE_OTA=1")
                inner.actions = []
                inner.mcu = "nrf52840"
            def BoardConfig(inner):
                return {"build.mcu": inner.mcu, "build.ldscript": "nrf52840_s140_v6.ld"}
            def subst(inner, value):
                self.assertEqual(value, "$BUILD_DIR")
                return str(self.project / "hook-build")
            def GetProjectOption(inner, key, default):
                self.assertEqual(key, "build_src_filter")
                return "+<../examples/simple_repeater>"
            def AppendUnique(inner, **kwargs):
                inner.actions.append(("append", kwargs))
            def BuildSources(inner, *args, **kwargs):
                inner.actions.append(("sources", args, kwargs))

        def execute(environment, **options):
            controlled = {key: value for key, value in os.environ.items() if key not in (
                "MESHCORE_NRF52_EMBED_BOOTLOADER", "MESHCORE_NRF52_LEGACY_BOOTLOADER")}
            with patch.dict(os.environ, dict(controlled, **options), clear=True):
                runpy.run_path(str(ROOT / "scripts/nrf52_embedded_bootloader_pio.py"),
                               init_globals={"env": environment, "Import": lambda _: None})

        environment = Environment()
        execute(environment)
        self.assertEqual(environment.actions, [])
        for options in ({"MESHCORE_NRF52_EMBED_BOOTLOADER": "2.4.11"},
                        {"MESHCORE_NRF52_LEGACY_BOOTLOADER": "legacy.bin"}):
            with self.subTest(options=options), self.assertRaises(RuntimeError):
                execute(Environment(), **options)
        environment = Environment()
        environment.mcu = "nrf52832"
        with self.assertRaisesRegex(RuntimeError, "nRF52840"):
            execute(environment, MESHCORE_NRF52_EMBED_BOOTLOADER="2.4.12")
        environment = Environment()
        execute(environment, MESHCORE_NRF52_EMBED_BOOTLOADER="2.4.12")
        generated = self.project / "hook-build/bootloader-migration-resource"
        self.assertEqual((generated / "BootloaderMigrationResource.bin").read_bytes(), boot_image())
        self.assertEqual(environment.actions[0], ("append", {
            "CPPDEFINES": [("OTA_BOOTLOADER_MIGRATION", 1)], "CPPPATH": [str(generated)]}))
        self.assertEqual(environment.actions[1], ("sources", (str(generated / "objects"), str(generated)),
                                                 {"src_filter": ["+<*.cpp>"]}))
        environment = Environment()
        logical = "RAK_4631_repeater_lora_ota_no_external_sensors"
        environment["BUILD_FLAGS"] += " -DOTA_VARIANT='\"" + logical + "\"'"
        execute(environment, MESHCORE_NRF52_EMBED_BOOTLOADER="2.4.12")
        metadata = json.loads((generated / "BootloaderMigrationResource.json").read_text())
        self.assertEqual(metadata["target"], logical)


if __name__ == "__main__":
    unittest.main()
