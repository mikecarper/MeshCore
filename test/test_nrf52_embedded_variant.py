"""Exercise embedded-bootloader build identity and packaged-image gates without PIO."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools/mota"))
import motalib as ml
import qualify_nrf52_embedded_bootloader as qualifier

SPEC = importlib.util.spec_from_file_location(
    "embedded_variant_recipe", ROOT / "scripts/firmware_build_recipe.py")
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)

TARGET = "RAK_4631_repeater"
VERSION = "v1.18.0.1-halo-keymind-cascade-dev-12345678"


class BuildIdentityTests(unittest.TestCase):
    def bash(self, script, *arguments):
        environment = os.environ.copy()
        environment.pop("MESHCORE_NRF52_EMBED_BOOTLOADER", None)
        environment.pop("MESHCORE_NRF52_LEGACY_BOOTLOADER", None)
        return subprocess.run(
            ["bash", "-c", 'source "$1/build_legacy.sh"\n'
             'pio() { echo FORBIDDEN_PLATFORMIO >&2; return 99; }\n' + script,
             "embedded-variant", str(ROOT), *arguments],
            cwd=ROOT, env=environment, text=True, capture_output=True, timeout=10)

    def test_option_accepts_only_explicit_pinned_release_and_retains_target(self):
        result = self.bash('''
shift
parse_cli_options "$@" || exit $?
printf '%s\n' "$MESHCORE_NRF52_EMBED_BOOTLOADER" "${PARSED_COMMAND_ARGS[@]}"
''', "build-firmware", TARGET, "--with-bootloader", "2.4.12")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["2.4.12", "build-firmware", TARGET])
        for arguments in (("--with-bootloader",),
                          ("--with-bootloader", "2.4.11"),
                          ("--with-bootloader", "latest")):
            with self.subTest(arguments=arguments):
                result = self.bash('shift\nparse_cli_options "$@"\n', *arguments)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("pinned release 2.4.12", result.stdout + result.stderr)

    def filename(self, *, embedded=False, ota=True, infix="", full=False):
        return self.bash('''
PIO_ENV_PLATFORM_BY_NAME[RAK_4631_repeater]=NRF52_PLATFORM
PIO_ENV_OTA_BY_NAME[RAK_4631_repeater]=$3
BUILD_PROFILE_FOR_TARGET=auto
FIRMWARE_FILENAME_INFIX=$4
ESP32_FULL_BUILD=$5
if [ "$2" = yes ]; then export MESHCORE_NRF52_EMBED_BOOTLOADER=2.4.12; fi
embedded_bootloader_artifact_target RAK_4631_repeater || exit $?
get_firmware_filename RAK_4631_repeater "$6"
''', "yes" if embedded else "no", "1" if ota else "0", infix,
                         "1" if full else "0", VERSION)

    def test_ordinary_names_and_ota_classification_survive_publication_suffix(self):
        for embedded in (False, True):
            for ota in (False, True):
                with self.subTest(embedded=embedded, ota=ota):
                    result = self.filename(embedded=embedded, ota=ota)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    artifact = TARGET + ("-with-bootloader-2.4.12" if embedded else "")
                    filename = artifact + ("-ota" if ota else "") + "-" + VERSION
                    self.assertEqual(result.stdout.splitlines(), [artifact, filename])

    def test_full_ota_profile_keeps_both_publication_markers(self):
        for embedded in (False, True):
            for infix, full in (("full-ota", False), ("full", True),
                                ("full-logging", True), ("full-usb-wifi", True)):
                with self.subTest(embedded=embedded, infix=infix, full=full):
                    result = self.filename(embedded=embedded, infix=infix, full=full)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    artifact = TARGET + ("-with-bootloader-2.4.12" if embedded else "")
                    expected_infix = infix if infix.endswith("-ota") else infix + "-ota"
                    self.assertEqual(result.stdout.splitlines(),
                                     [artifact, artifact + "-" + expected_infix + "-" + VERSION])

    def test_invalid_inherited_release_cannot_create_publishable_name(self):
        result = self.bash('''
export MESHCORE_NRF52_EMBED_BOOTLOADER=2.4.11
embedded_bootloader_artifact_target RAK_4631_repeater
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class PackagedApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mesh-embedded-package-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.resource = self.directory / "bootloader-migration-resource"
        self.resource.mkdir()
        self.manifest_path = self.directory / "firmware.capabilities.json"
        self.manifest = {"platform": "NRF52_PLATFORM", "verified": True,
                         "target": TARGET,
                         "artifact_target": TARGET + "-with-bootloader-2.4.12",
                         "capabilities": ["ota.update.lora", "bootloader.embedded"],
                         "source_commit": "a" * 40}
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.candidate = (struct.pack("<II", 0x20030000, 0xF4101)
                          + bytes(range(256)) * (qualifier.BOOT_SIZE // 256))[0:qualifier.BOOT_SIZE]
        self.identity = SimpleNamespace(app_base=0x26000, softdevice_fwid=0xB6)
        self.parsed = {"sourceHardware": "R1Neo", "role": "repeater"}
        self.profile = {"id": "wiscore_rak4631_auto", "storage": "adaptive",
                        "files": {"zip": {"sha256": "b" * 64}}}
        self.stock = qualifier.legacy_bootloaders_for(
            self.profile["id"], self.parsed["sourceHardware"],
            self.identity.app_base, self.identity.softdevice_fwid)
        self.stock_hashes = b"".join(bytes.fromhex(item["image_sha256"]) for item in self.stock)
        self.metadata = {"target": TARGET, "hardware": self.parsed["sourceHardware"],
                         "role": self.parsed["role"], "app_base": self.identity.app_base,
                         "softdevice_fwid": self.identity.softdevice_fwid,
                         "stock_legacy": self.stock,
                         "profile": self.profile["id"], "release": "2.4.12",
                         "manifest_sha256": qualifier.MANIFEST_SHA256,
                         "asset_sha256": "b" * 64,
                         "candidate_sha256": hashlib.sha256(self.candidate).hexdigest(),
                         "legacy_sha256": None}
        self.write_resource()
        self.write_application(self.application())
        self.patches = {}
        for name, value in (("load_manifest", {"profiles": [self.profile]}),
                            ("select_profile", (self.profile, self.parsed)),
                            ("download_zip", b"verified published ZIP"),
                            ("validate_candidate_zip", (self.candidate, self.identity))):
            patcher = mock.patch.object(qualifier, name, return_value=value)
            self.patches[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def write_resource(self, candidate=None):
        (self.resource / "BootloaderMigrationResource.json").write_text(json.dumps(self.metadata))
        (self.resource / "BootloaderMigrationResource.bin").write_bytes(
            self.candidate if candidate is None else candidate)

    def application(self, *, prefix=b"\x02" * 32, candidate=None, duplicate=False,
                    marker=True, target=TARGET, layout=True, app_base=0x26000,
                    padding=0):
        candidate = self.candidate if candidate is None else candidate
        body = prefix + candidate + (candidate if duplicate else b"") + self.stock_hashes
        if marker:
            body += b"\0BL embedded 2.4.12\0"
        body += bytes(padding)
        if layout:
            body = ml.ensure_nrf52_layout(body, ml.Nrf52Layout(
                app_base, 0xED000, 0xED000, ml.NRF52_LAYOUT_FLAG_HYBRID_RAM))
        return ml.ensure_endf(body, ml.FwIdent(
            0x01120001, ml.target_id_for_env(target), "RAK_4631"))[0]

    def write_application(self, application):
        with zipfile.ZipFile(self.directory / "firmware.zip", "w") as package:
            package.writestr("manifest.json", json.dumps({"manifest": {"application": {
                "bin_file": "firmware.bin", "dat_file": "firmware.dat"}}}))
            package.writestr("firmware.bin", application)
            package.writestr("firmware.dat", b"qualified separately")

    def assert_refused_without_changing_manifest(self):
        original = self.manifest_path.read_bytes()
        with self.assertRaises(ValueError):
            qualifier.qualify(self.directory, self.manifest_path)
        self.assertEqual(original, self.manifest_path.read_bytes())

    def test_claim_is_bound_to_actual_application_and_preserves_existing_qualification(self):
        result = qualifier.qualify(self.directory, self.manifest_path)
        manifest = json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest.pop("embedded_bootloader"), result)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(result["app_offset"], 32)
        self.assertEqual(result["image_bytes"], 40960)
        self.assertEqual(result["release"], "2.4.12")
        self.assertEqual(result["profile"], self.profile["id"])
        self.assertEqual(result["candidate_sha256"], hashlib.sha256(self.candidate).hexdigest())
        self.assertTrue(self.stock)
        self.assertEqual(result["stock_legacy"], self.stock)
        self.assertEqual(result["stock_legacy_offset"], 32 + qualifier.BOOT_SIZE)
        self.assertEqual(result["activation"], "ota bootloader update")
        self.assertEqual(result["automatic_delay_seconds"], 70)
        self.patches["select_profile"].assert_called_once_with(
            {"profiles": [self.profile]}, TARGET)
        self.patches["validate_candidate_zip"].assert_called_once_with(
            b"verified published ZIP", self.profile)

    def test_resource_and_release_metadata_tampering_are_refused(self):
        for field, value in (("profile", "another_board"), ("release", "2.4.11"),
                             ("manifest_sha256", "0" * 64),
                             ("asset_sha256", "0" * 64),
                             ("candidate_sha256", "0" * 64),
                             ("legacy_sha256", "0" * 64),
                             ("target", "RAK_3401_repeater"),
                             ("hardware", "RAK_3401"), ("role", "room_server"),
                             ("app_base", 0x27000), ("softdevice_fwid", 0x123),
                             ("stock_legacy", [])):
            with self.subTest(field=field):
                original = self.metadata[field]
                self.metadata[field] = value
                self.write_resource()
                self.assert_refused_without_changing_manifest()
                self.metadata[field] = original
        self.write_resource(self.candidate[:-1] + bytes([self.candidate[-1] ^ 1]))
        self.assert_refused_without_changing_manifest()

    def test_invalid_pinned_candidate_never_attaches_claim(self):
        self.patches["validate_candidate_zip"].side_effect = ValueError("candidate identity invalid")
        self.assert_refused_without_changing_manifest()

    def test_corrupt_application_and_validly_restamped_wrong_images_are_refused(self):
        corrupted = bytearray(self.application())
        corrupted[40] ^= 1  # Leave EndF unchanged, as with corruption after packaging.
        variants = {"body corruption": bytes(corrupted),
                    "other target": self.application(target="RAK_4631_room_server"),
                    "unaligned candidate": self.application(prefix=b"\x02" * 31),
                    "duplicate candidate": self.application(duplicate=True),
                    "missing candidate": self.application(candidate=b""),
                    "activation absent": self.application(marker=False),
                    "layout absent": self.application(layout=False),
                    "other SoftDevice base": self.application(app_base=0x27000),
                    "exceeds application region": self.application(padding=0xC7000)}
        for name, application in variants.items():
            with self.subTest(variant=name):
                self.write_application(application)
                self.assert_refused_without_changing_manifest()

    def test_unqualified_or_other_platform_artifacts_cannot_gain_embedded_claim(self):
        for changes in ({"verified": False}, {"platform": "ESP32_PLATFORM"}):
            with self.subTest(changes=changes):
                self.manifest_path.write_text(json.dumps({**self.manifest, **changes}))
                self.assert_refused_without_changing_manifest()


class RecipeBindingTests(unittest.TestCase):
    def test_external_legacy_input_bytes_are_bound_even_when_path_does_not_change(self):
        with tempfile.TemporaryDirectory(prefix="mesh-embedded-recipe-") as temporary:
            directory = Path(temporary)
            legacy = directory / "legacy.hex"
            legacy.write_bytes(b"exact first legacy image")
            args = argparse.Namespace(
                root=directory, output=directory / "out", source_commit="a" * 40,
                target=TARGET, artifact_target=TARGET + "-with-bootloader-2.4.12",
                pio_env=TARGET, platform="NRF52_PLATFORM", embedded_version=VERSION,
                profile="auto", sensor_profile="", ota_policy="1")
            options = [["env:" + TARGET, [["board", "wiscore_rak4631"]]]]
            with mock.patch.object(recipe, "source_digest", return_value="c" * 64), \
                    mock.patch.dict(os.environ, {
                        "MESHCORE_NRF52_EMBED_BOOTLOADER": "2.4.12",
                        "MESHCORE_NRF52_LEGACY_BOOTLOADER": str(legacy)}, clear=True):
                first = recipe.recipe_digest(args, options)
                self.assertEqual(first, recipe.recipe_digest(args, options))
                legacy.write_bytes(b"same path, different legacy image")
                self.assertNotEqual(first, recipe.recipe_digest(args, options))
                legacy.unlink()
                with self.assertRaises(OSError):
                    recipe.recipe_digest(args, options)


if __name__ == "__main__":
    unittest.main()
