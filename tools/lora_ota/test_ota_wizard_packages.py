"""Offline package planning, builder fallback, and pinned bootloader bundles."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

import lora_ota as ota
import ota_wizard_packages as packages
import ota_wizard_session as session
from test_lora_ota import mota_blob
from test_lora_ota_bootloader import boot_blob
from test_ota_wizard_session import (application, boot_status, BOOT_NEW, BOOT_OLD,
                                     HW, KEY, OLD, NEW, TARGET, TEST_SIGNER)


def destination(**changes):
    current = application(OLD, b"old application" * 600)
    result = session.Destination(KEY, TARGET, HW, current.body_hash, OLD, "v1.17.1.9",
                                 "nrf52", True, 2048, 5,
                                 ota.parse_bootloader_status(boot_status()), BOOT_OLD)
    return replace(result, **changes)


def delta_blob(image, base, **changes):
    arguments = dict(full=False, version=image.fw_version, target=image.target_id,
                     hw=image.hw_id, base_hash=base.body_hash,
                     codec=ota.MOTA_CODEC_IN_PLACE, payload=b"example patch")
    arguments.update(changes)
    return mota_blob(image.image, **arguments)


class PackageTests(unittest.TestCase):
    def test_wizard_and_runner_use_the_same_motatool_revision(self):
        # Both entry points may repair/install motatool independently; a split
        # pin can make the wizard build a package that the runner cannot verify.
        self.assertEqual(packages.MOTATOOL_REV, ota.MOTATOOL_REPAIR_REVISION)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)
        self.new = application()
        self.old = application(OLD, b"old application" * 600)
        self.base = self.cache / "base.bin"
        self.base.write_bytes(self.old.image)

    def test_full_package_works_without_network_or_native_builder(self):
        with mock.patch.object(packages.subprocess, "run", side_effect=AssertionError("must not compile")):
            result = packages.prepare_application(self.new, destination(), self.cache, progress=lambda _: None)
        self.assertTrue(result.info.is_full)
        self.assertEqual((result.info.target_id, result.info.hw_id, result.body_hash),
                         (TARGET, HW, self.new.body_hash))
        self.assertEqual(result.info.image_hash, hashlib.sha256(self.new.image).digest())

    def test_exact_base_delta_uses_in_place_and_expected_new_image(self):
        commands = []
        def build(command, **_):
            commands.append(command)
            Path(command[command.index("--out") + 1]).write_bytes(delta_blob(self.new, self.old))
            return SimpleNamespace(returncode=0)
        with mock.patch.object(packages.subprocess, "run", side_effect=build):
            result = packages.prepare_application(self.new, destination(), self.cache,
                                                  base=self.base, builder="fake-builder", progress=lambda _: None)
        self.assertFalse(result.info.is_full)
        self.assertEqual(result.info.base_hash, self.old.body_hash)
        self.assertIn("in-place", commands[0])
        self.assertEqual(result.body_hash, self.new.body_hash)

    def test_failed_timed_out_and_malformed_delta_fall_back_to_full(self):
        for behavior in ("exit", "timeout", "malformed", "wrong-image", "missing"):
            with self.subTest(behavior=behavior):
                def build(command, **_):
                    output = Path(command[command.index("--out") + 1])
                    if behavior == "timeout":
                        raise subprocess.TimeoutExpired(command, 1)
                    if behavior == "malformed":
                        output.write_bytes(b"broken package")
                    if behavior == "wrong-image":
                        wrong = application(NEW, b"another new image" * 600)
                        output.write_bytes(delta_blob(wrong, self.old))
                    return SimpleNamespace(returncode=1 if behavior == "exit" else 0)
                with mock.patch.object(packages.subprocess, "run", side_effect=build):
                    result = packages.prepare_application(self.new, destination(), self.cache,
                                                          base=self.base, builder="fake-builder", progress=lambda _: None)
                self.assertTrue(result.info.is_full)

    def test_internal_nrf_rejects_full_fallback(self):
        with mock.patch.object(packages.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ota.OtaError, "Internal-flash"):
                packages.prepare_application(self.new, destination(external_storage=False), self.cache,
                                             base=self.base, builder="fake-builder", progress=lambda _: None)

    def test_explicit_wrong_base_is_rejected_before_builder(self):
        self.base.write_bytes(application(OLD, b"different old image" * 600).image)
        with mock.patch.object(packages.subprocess, "run") as build:
            with self.assertRaisesRegex(ota.OtaError, "exact running firmware"):
                packages.prepare_application(self.new, destination(), self.cache,
                                             base=self.base, builder="fake-builder", progress=lambda _: None)
        build.assert_not_called()

    def test_stale_delta_file_is_removed_before_build(self):
        directory = self.cache / "packages" / f"{TARGET:08X}"
        directory.mkdir(parents=True)
        output = directory / f"{self.old.body_hash.hex()}-{self.new.body_hash.hex()}-delta.mota"
        output.write_bytes(delta_blob(self.new, self.old))
        def build(*_, **__):
            self.assertFalse(output.exists())
            return SimpleNamespace(returncode=0)
        with mock.patch.object(packages.subprocess, "run", side_effect=build):
            result = packages.prepare_application(self.new, destination(), self.cache,
                                                  base=self.base, builder="fake-builder", progress=lambda _: None)
        self.assertTrue(result.info.is_full)

    def test_target_role_codec_blocksize_and_expected_body_are_required(self):
        package = session.full_package(self.new, 1024)
        for change, error in ((dict(target_id=TARGET ^ 1), "different board"),
                              (dict(hw_id="wrong-role"), "different board"),
                              (dict(max_block_size=512), "block size"),
                              (dict(codecs=4), "codec")):
            with self.subTest(change=change), self.assertRaisesRegex(ota.OtaError, error):
                session.validate_package(package, destination(**change))
        with self.assertRaisesRegex(ota.OtaError, "body hash"):
            session.validate_package(session.Package(package.blob), destination())

    def test_published_base_search_skips_bad_candidate_and_checks_hash(self):
        good = self.cache / "published.bin"
        good.write_bytes(self.old.image)
        source = mock.Mock()
        source.base_candidates.return_value = ["broken", "valid"]
        with mock.patch.object(packages, "download_asset", side_effect=[ota.OtaError("bad ZIP"), good]):
            found = packages.find_running_image(destination(), self.cache, source, progress=lambda _: None)
        self.assertEqual(ota.parse_endf(found.read_bytes()).body_hash, self.old.body_hash)
        source.base_candidates.assert_called_once_with(target_id=TARGET, fw_version=OLD, limit=4)

    def test_raspberry_pi_does_not_install_native_tool(self):
        with mock.patch.object(packages, "small_host", return_value=True), \
                mock.patch.object(packages.subprocess, "run") as run:
            self.assertIsNone(packages.install_builder(self.cache))
        run.assert_not_called()

    def test_apple_silicon_and_windows_arm_keep_workstation_features(self):
        for system in ("Darwin", "Windows"):
            with self.subTest(system=system), \
                    mock.patch.object(packages.platform, "system", return_value=system), \
                    mock.patch.object(packages.platform, "machine", return_value="arm64"), \
                    mock.patch.object(packages.Path, "read_text", side_effect=AssertionError("no Linux probing")):
                self.assertFalse(packages.small_host())

    def test_pi_board_model_remains_low_power_even_with_large_ram(self):
        with mock.patch.object(packages.platform, "system", return_value="Linux"), \
                mock.patch.object(packages.Path, "read_text", return_value="Raspberry Pi 5 Model B Rev 1.0\0"), \
                mock.patch.object(packages.os, "cpu_count", return_value=8):
            self.assertTrue(packages.small_host())

    def test_linux_arm_fallback_requires_both_low_ram_and_few_cores(self):
        for ram_kb, cores, expected in ((2 * 1024 * 1024, 4, True),
                                        (8 * 1024 * 1024, 4, False),
                                        (2 * 1024 * 1024, 8, False),
                                        (2 * 1024 * 1024, None, False)):
            with self.subTest(ram=ram_kb, cores=cores), \
                    mock.patch.object(packages.platform, "system", return_value="Linux"), \
                    mock.patch.object(packages.platform, "machine", return_value="aarch64"), \
                    mock.patch.object(packages.Path, "read_text", side_effect=[OSError("no model"), f"MemTotal: {ram_kb} kB\n"]), \
                    mock.patch.object(packages.os, "cpu_count", return_value=cores):
                self.assertEqual(packages.small_host(), expected)

    def test_native_setup_is_pinned_and_strips_password_environment(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout="rustc 1.86.0")
        with mock.patch.object(packages, "small_host", return_value=False), \
                mock.patch.object(packages.shutil, "which", return_value="tool"), \
                mock.patch.object(packages.subprocess, "run", side_effect=run), \
                mock.patch.dict(packages.os.environ, {"MESHCORE_ADMIN_PASSWORD": "private-password"}):
            packages.install_builder(self.cache, progress=lambda _: None)
        command, options = calls[-1]
        self.assertIn(packages.MOTATOOL_REV, command)
        self.assertIn("--locked", command)
        self.assertNotIn("MESHCORE_ADMIN_PASSWORD", options["env"])
        self.assertNotIn("private-password", str(calls))

    def boot_bundle(self, *, storage=14, key=TEST_SIGNER, checksum=True):
        blob = boot_blob(storage=storage, version=BOOT_NEW)
        info = ota.parse_mota(blob)
        path = self.cache / "bootloaders.zip"
        inventory = {"signing_public_key": key, "packages": [{
            "hardware_id": info.hw_id, "target_id": f"0x{info.target_id:08X}",
            "file": "boot.mota", "sha256": hashlib.sha256(blob).hexdigest() if checksum else "0" * 64}]}
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(inventory))
            archive.writestr("OTAFIX_MOTA_SIGNING_PUBLIC_KEY.txt", key + "\n")
            archive.writestr("mota/boot.mota", blob)
        release = {"tag_name": "v0.11.0-OTAFIX2.4.12", "assets": [{
            "name": "OTAFIX-2.4.12-bootloader-mota.zip", "size": path.stat().st_size,
            "browser_download_url": "https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/download/v0.11.0-OTAFIX2.4.12/OTAFIX-2.4.12-bootloader-mota.zip"}]}
        return path, json.dumps(release).encode()

    def prepare_boot(self, dest=None, **bundle_options):
        path, release = self.boot_bundle(**bundle_options)
        with mock.patch.object(packages, "OFFICIAL_BOOT_KEY", TEST_SIGNER), \
                mock.patch.object(packages, "_open", return_value=io.BytesIO(release)), \
                mock.patch.object(packages, "download_asset", return_value=path):
            return packages.prepare_bootloader(dest or destination(), self.cache)

    def test_bootloader_bundle_matches_pinned_key_and_exact_storage(self):
        result = self.prepare_boot()
        self.assertEqual((result.info.fw_version, result.info.bootloader_storage, result.signer),
                         (BOOT_NEW, 14, TEST_SIGNER))
        self.assertIsNone(self.prepare_boot(destination(boot_version=BOOT_NEW)))
        self.assertIsNone(self.prepare_boot(destination(boot_version=None)))

    def test_bootloader_wrong_key_checksum_and_storage_are_rejected(self):
        for options, error in ((dict(key="00" * 32), "pinned publisher"),
                               (dict(checksum=False), "checksum"),
                               (dict(storage=10), "exact installed identity")):
            with self.subTest(options=options), self.assertRaisesRegex(ota.OtaError, error):
                self.prepare_boot(**options)

    def test_bootloader_signature_cannot_be_approved_by_a_different_key(self):
        package = session.Package(boot_blob(storage=14, version=BOOT_NEW), signer="00" * 32)
        with self.assertRaises(ota.OtaError):
            session.validate_package(package, destination())

    def test_unsigned_sd_application_cannot_claim_a_trusted_signer(self):
        full = session.full_package(self.new, 1024)
        claimed = session.Package(full.blob, self.new.body_hash, TEST_SIGNER)
        with self.assertRaisesRegex(ota.OtaError, "signing key"):
            session.validate_package(claimed, destination(sd_storage=True))

    def test_bootloader_requires_supported_platform_codecs_and_block_size(self):
        package = session.Package(boot_blob(storage=14, version=BOOT_NEW), signer=TEST_SIGNER)
        for change in (dict(platform="esp32"), dict(codecs=4), dict(max_block_size=512)):
            with self.subTest(change=change), self.assertRaises(ota.OtaError):
                session.validate_package(package, destination(**change))

    def test_esp32_rejects_an_in_place_delta(self):
        package = session.Package(delta_blob(self.new, self.old), self.new.body_hash)
        with self.assertRaisesRegex(ota.OtaError, "sequential"):
            session.validate_package(package, destination(platform="esp32"))


if __name__ == "__main__":
    unittest.main()
