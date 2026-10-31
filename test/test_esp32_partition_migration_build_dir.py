"""Package real qualified fixtures from the selected PlatformIO utility tree."""

from contextlib import contextmanager
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_esp32_partition_migration as package  # noqa: E402
from build_esp32_partition_migration import verify_archive  # noqa: E402
from motalib import (  # noqa: E402
    FwIdent, ensure_endf, hardware_id_for_env, pack_version, parse_container,
    parse_endf, parse_endf_ident, target_id_for_env, verify,
)


VERSION = "v1.17.1.9-test"
SOURCE = "01234567"
UTILITY_KEYS = ("wifi_bridge", "usb_bridge", "expander_bridge", "lora_bridge")


@contextmanager
def platformio_build_dir(value):
    previous = os.environ.get("PLATFORMIO_BUILD_DIR")
    try:
        if value is None:
            os.environ.pop("PLATFORMIO_BUILD_DIR", None)
        else:
            os.environ["PLATFORMIO_BUILD_DIR"] = str(value)
        yield
    finally:
        if previous is None:
            os.environ.pop("PLATFORMIO_BUILD_DIR", None)
        else:
            os.environ["PLATFORMIO_BUILD_DIR"] = previous


class MigrationBuildDirTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="migration-build-tree-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.project = self.work / "project with spaces"
        self.project.mkdir()
        previous_root = package.ROOT
        self.addCleanup(setattr, package, "ROOT", previous_root)
        package.ROOT = self.project
        self.artifacts = self.work / "qualified artifacts"
        self.artifacts.mkdir()
        self.output = self.work / "packages"
        self.default = self.project / ".pio/build"
        self.custom = self.work / "current utility builds"
        # Exercise an actual metadata subprocess, never a PlatformIO process.
        # This boundary accepts only the exact read-only query used by the
        # resolver; firmware builds and all other PIO commands are forbidden.
        metadata_bin = self.work / "metadata-bin"; metadata_bin.mkdir()
        metadata_pio = metadata_bin / "pio"
        metadata_pio.write_text(r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
if len(sys.argv) != 6 or sys.argv[1:5] != ["project", "config", "--json-output", "--project-dir"]:
    raise SystemExit("forbidden PlatformIO command")
root = Path(sys.argv[5])
record = os.environ.get("MIGRATION_TEST_METADATA_LOG")
if record:
    with open(record, "a", encoding="ascii") as output:
        output.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(),
                                 "build_dir": os.environ.get("PLATFORMIO_BUILD_DIR"),
                                 "other": os.environ.get("MIGRATION_TEST_OTHER")}) + "\n")
if os.environ.get("MIGRATION_TEST_METADATA_EXIT"):
    print("private-metadata-credential", file=sys.stderr)
    raise SystemExit(int(os.environ["MIGRATION_TEST_METADATA_EXIT"]))
if "MIGRATION_TEST_METADATA_JSON" in os.environ:
    print(os.environ["MIGRATION_TEST_METADATA_JSON"])
    raise SystemExit(0)
value = os.environ.get("PLATFORMIO_BUILD_DIR") or str(root / ".pio/build")
value = value.replace("${PROJECT_DIR}", str(root))
directory = Path(value).expanduser()
if not directory.is_absolute():
    directory = root / directory
if os.environ.get("MIGRATION_TEST_CONFIGURED_DIR"):
    directory = Path(os.environ["MIGRATION_TEST_CONFIGURED_DIR"])
print(json.dumps([["platformio", [["build_dir", str(directory.resolve())]]]]))
''', encoding="ascii")
        metadata_pio.chmod(0o755)
        previous_path = os.environ["PATH"]
        self.addCleanup(os.environ.__setitem__, "PATH", previous_path)
        os.environ["PATH"] = str(metadata_bin) + os.pathsep + previous_path

    def qualified_full(self, board):
        spec = package.BOARDS[board]
        target = spec["target"]
        ident = FwIdent(pack_version(VERSION.split("-", 1)[0]),
                        target_id_for_env(target), hardware_id_for_env(target))
        image, _ = ensure_endf(b"\xe9qualified Full application\0OTA: status", ident)
        table = bytearray(b"\xff" * 4096)
        entries = (("nvs", 1, 2, 0x9000, 0x5000),
                   ("otadata", 1, 0, 0xE000, 0x2000),
                   ("app0", 0, 0x10, 0x10000, spec["slot_bytes"]),
                   ("app1", 0, 0x11, spec["slot1_address"], spec["slot_bytes"]))
        for index, (label, kind, subtype, address, size) in enumerate(entries):
            struct.pack_into("<HBBLL16sL", table, index * 32, 0x50AA, kind,
                             subtype, address, size, label.encode("ascii"), 0)
        merged = bytearray(b"\xff" * 0x10000)
        merged[0x8000:0x9000] = table
        merged.extend(image)
        stem = f"{target}-full-ota-{VERSION}-{SOURCE}"
        manifest = dict(
            schema_version=2, target=target, artifact_target=target,
            platformio_env=target, build_profile="full", platform="ESP32_PLATFORM",
            verified=True, capabilities=["ota.update.lora"], reductions=[],
            verification=[dict(capability="ota.update.lora", evidence="OTA: status",
                               present=True, source="linked image")],
            ota_update_methods=["lora"], ota_update_verified=True,
            ota_update_evidence="firmware fits both OTA application slots; otadata present")
        files = {stem + ".bin": image, stem + "-merged.bin": merged,
                 stem + ".capabilities.json": json.dumps(manifest).encode("ascii")}
        for name, data in files.items():
            (self.artifacts / name).write_bytes(data)
        proof = dict(schema_version=1, passed=True, available_internal_bytes=80000,
                     required_heap_bytes=50000, largest_internal_region_bytes=80000,
                     required_contiguous_bytes=5120, elf_sha256="a" * 64,
                     files={name: hashlib.sha256(data).hexdigest()
                            for name, data in files.items()})
        (self.artifacts / (stem + ".memory.json")).write_text(
            json.dumps(proof), encoding="ascii")
        return spec, ident

    def utility_tree(self, tree, spec, label):
        result = {}
        for key in UTILITY_KEYS:
            env = spec.get(key)
            if env:
                directory = tree / env
                directory.mkdir(parents=True)
                image = b"\xe9" + label.encode("ascii") + b" " + key.encode("ascii")
                if key == "usb_bridge":
                    header = bytearray(24)
                    header[0:3] = b"\xe9\x01\x02"
                    header[3] = {4: 2, 8: 3, 16: 4}[spec["flash_bytes"] // 1048576] << 4
                    struct.pack_into("<H", header, 12, spec["usb_chip"])
                    image = (bytes(header) + package.USB_BRIDGE_MARKER + b"\0"
                             + package.USB_NVS_GUARD_MARKER + b"\0" + image[1:])
                (directory / "firmware.bin").write_bytes(image)
                result[key] = image
        return result

    def check_archive(self, archive_path, board, expected, identity):
        verify_archive(archive_path, board, VERSION, SOURCE)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("manifest.json"))
            for name, metadata in manifest["files"].items():
                data = archive.read(name)
                self.assertEqual(len(data), metadata["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), metadata["sha256"])
            for row in archive.read("SHA256SUMS.txt").decode("ascii").splitlines():
                digest, name = row.split("  ", 1)
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
            self.assertEqual(archive.read("wifi-bridge.bin"), expected["wifi_bridge"])
            self.assertEqual(archive.read("usb-bridge.bin"), expected["usb_bridge"])
            usb = manifest["usb_migration"]
            spec = package.BOARDS[board]
            self.assertEqual(usb["format"], "meshcore-usb-partition-migration-v1")
            self.assertEqual(usb["environment"], spec["usb_bridge"])
            self.assertEqual(usb["chip"], spec["usb_chip"])
            self.assertEqual(usb["flash_bytes"], spec["flash_bytes"])
            self.assertEqual(usb["automatic_rom_return"], spec["usb_chip"] != 0)
            self.assertEqual(usb["uart_completion_status"], spec["usb_chip"] == 0)
            self.assertIs(usb["host_software_bootloader_entry"], True)
            self.assertIs(usb["application_usb_cdc"], False)
            if "expander_bridge" in expected:
                self.assertEqual(archive.read("partition-expander.bin"), expected["expander_bridge"])
            for key, name in (("expander_bridge", "partition-expander.mota"),
                              ("lora_bridge", "lora-bridge.mota")):
                if key not in expected:
                    continue
                container = parse_container(archive.read(name))
                self.assertEqual(verify(container), [])
                self.assertEqual(parse_endf(container.payload)[0], expected[key])
                self.assertEqual(parse_endf_ident(container.payload), identity)

    def test_default_tree_packages_all_utility_types(self):
        for board in ("heltec-v4", "xiao-s3-wio"):
            with self.subTest(board=board):
                spec, identity = self.qualified_full(board)
                expected = self.utility_tree(self.default, spec, "DEFAULT")
                with platformio_build_dir(None):
                    archive = package.package_board(board, spec, self.artifacts,
                                                    self.output, VERSION, SOURCE)
                self.check_archive(archive, board, expected, identity)

    def test_ambient_override_ignores_stale_default_for_all_utility_types(self):
        spec, identity = self.qualified_full("heltec-v4")
        self.utility_tree(self.default, spec, "STALE DEFAULT")
        expected = self.utility_tree(self.custom, spec, "CURRENT")
        with platformio_build_dir(self.custom):
            archive = package.package_board("heltec-v4", spec, self.artifacts,
                                            self.output, VERSION, SOURCE)
        self.check_archive(archive, "heltec-v4", expected, identity)

    def test_staged_publication_bytes_take_precedence_over_a_separate_rebuild(self):
        spec, identity = self.qualified_full("heltec-v4")
        expected = self.utility_tree(self.custom, spec, "PUBLICATION MATCH")
        publication = self.work / "staged firmware" / "full-profiles"
        shutil.copytree(self.artifacts, publication)
        published = next(path for path in publication.glob("*.bin")
                         if not path.name.endswith("-merged.bin"))
        original = published.read_bytes()
        # A separate rebuild is not authoritative merely because its filename
        # and version are the same. It must never replace the published bytes.
        (self.artifacts / published.name).write_bytes(b"different second build")
        archive = package.package_board("heltec-v4", spec, self.artifacts,
            self.output, VERSION, SOURCE, self.custom,
            release_build_dir=publication.parent)
        self.check_archive(archive, "heltec-v4", expected, identity)
        verify_archive(archive, "heltec-v4", VERSION, SOURCE, full_image=published)
        with zipfile.ZipFile(archive) as result:
            self.assertEqual(result.read("full-application.bin"), original)
            self.assertEqual(parse_container(result.read("full-application.mota")).payload,
                             original)

    def test_invalid_or_ambiguous_publication_never_falls_back_to_valid_rebuild(self):
        spec, _ = self.qualified_full("heltec-v4")
        self.utility_tree(self.custom, spec, "VALID UTILITIES")
        publication = self.work / "staged firmware"
        shutil.copytree(self.artifacts, publication)
        image = next(path for path in publication.glob("*.bin")
                     if not path.name.endswith("-merged.bin"))
        original = image.read_bytes()
        image.write_bytes(b"invalid published app")
        with self.assertRaises(ValueError):
            package.package_board("heltec-v4", spec, self.artifacts,
                self.output, VERSION, SOURCE, self.custom,
                release_build_dir=publication)
        self.assertFalse(self.output.exists())
        image.write_bytes(original)
        (publication / "duplicate").mkdir()
        shutil.copy2(image, publication / "duplicate" / image.name)
        with self.assertRaisesRegex(ValueError, "expected one exact-target Full image"):
            package.package_board("heltec-v4", spec, self.artifacts,
                self.output, VERSION, SOURCE, self.custom,
                release_build_dir=publication)
        self.assertFalse(self.output.exists())

    def test_absent_migration_only_target_can_use_its_separate_qualified_image(self):
        spec, identity = self.qualified_full("heltec-v4")
        expected = self.utility_tree(self.custom, spec, "MIGRATION ONLY")
        publication = self.work / "ordinary firmware"
        publication.mkdir()
        archive = package.package_board("heltec-v4", spec, self.artifacts,
            self.output, VERSION, SOURCE, self.custom,
            release_build_dir=publication)
        self.check_archive(archive, "heltec-v4", expected, identity)

    def test_published_image_selection_requires_one_exact_qualified_full_application(self):
        spec, _ = self.qualified_full("heltec-v4")
        target = spec["target"]
        image = next(path for path in self.artifacts.glob("*.bin")
                     if not path.name.endswith("-merged.bin"))
        record = {"manifest": {"target": target, "platform": "ESP32_PLATFORM",
                               "build_profile": "full"},
                  "files": [image, image.with_name(image.stem + "-merged.bin")]}
        self.assertEqual(package.published_full_image([record], target), image)
        self.assertIsNone(package.published_full_image([record], "another_repeater"))
        for field, value in (("platform", "NRF52_PLATFORM"), ("build_profile", "standard")):
            other = {"manifest": {**record["manifest"], field: value}, "files": record["files"]}
            self.assertIsNone(package.published_full_image([other], target))
        with self.assertRaisesRegex(ValueError, "multiple published Full"):
            package.published_full_image([record, record], target)
        for files in ([], [image, image]):
            with self.assertRaisesRegex(ValueError, "unique application"):
                package.published_full_image([{**record, "files": files}], target)

    def test_relative_override_is_project_relative_not_caller_relative(self):
        spec, identity = self.qualified_full("heltec-v4")
        relative = Path("custom utility builds")
        expected = self.utility_tree(self.project / relative, spec, "RELATIVE")
        self.utility_tree(self.default, spec, "STALE DEFAULT")
        with platformio_build_dir(relative):
            self.assertEqual(package.resolve_pio_build_dir(), (self.project / relative).resolve())
            archive = package.package_board("heltec-v4", spec, self.artifacts,
                                            self.output, VERSION, SOURCE)
        self.check_archive(archive, "heltec-v4", expected, identity)

    def test_explicit_relative_tree_overrides_ambient_tree(self):
        spec, identity = self.qualified_full("heltec-v4")
        relative = Path("explicit utilities")
        expected = self.utility_tree(self.project / relative, spec, "EXPLICIT")
        self.utility_tree(self.default, spec, "STALE DEFAULT")
        self.utility_tree(self.custom, spec, "AMBIENT")
        with platformio_build_dir(self.custom):
            archive = package.package_board("heltec-v4", spec, self.artifacts,
                                            self.output, VERSION, SOURCE, relative)
        self.check_archive(archive, "heltec-v4", expected, identity)

    def test_missing_selected_utilities_never_fall_back_or_publish(self):
        spec, _ = self.qualified_full("heltec-v4")
        self.utility_tree(self.default, spec, "STALE DEFAULT")
        for missing in UTILITY_KEYS:
            with self.subTest(missing=missing):
                selected = self.work / ("selected " + missing)
                self.utility_tree(selected, spec, "CURRENT")
                (selected / spec[missing] / "firmware.bin").unlink()
                with platformio_build_dir(selected):
                    with self.assertRaises(FileNotFoundError):
                        package.package_board("heltec-v4", spec, self.artifacts,
                                              self.output, VERSION, SOURCE)
                self.assertFalse(self.output.exists())

    def test_all_six_usb_profiles_have_exact_chip_capacity_and_safe_manifest(self):
        boards = ("thinknode-m2-repeater", "heltec-v3-repeater", "heltec-v4",
                  "generic-e22-sx1262-repeater", "heltec-v2-repeater", "heltec-ct62-sensor")
        self.assertEqual({package.BOARDS[b]["usb_bridge"] for b in boards},
                         set(package.USB_BRIDGE_PROFILES.values()))
        for board in boards:
            with self.subTest(board=board):
                spec, identity = self.qualified_full(board)
                expected = self.utility_tree(self.custom, spec, "ALL USB")
                archive = package.package_board(board, spec, self.artifacts,
                                                self.output, VERSION, SOURCE, self.custom)
                self.check_archive(archive, board, expected, identity)

    def test_usb_wrong_chip_capacity_flash_mode_marker_or_fit_refuses_publication(self):
        spec, _ = self.qualified_full("heltec-v4")
        expected = self.utility_tree(self.custom, spec, "USB VALID")
        path = self.custom / spec["usb_bridge"] / "firmware.bin"
        original = expected["usb_bridge"]
        wrong_chip = bytearray(original); struct.pack_into("<H", wrong_chip, 12, 5)
        wrong_flash = bytearray(original); wrong_flash[3] = 0x20
        wrong_mode = bytearray(original); wrong_mode[2] = 0
        cases = (bytes(wrong_chip), bytes(wrong_flash), bytes(wrong_mode),
                 original.replace(package.USB_BRIDGE_MARKER, b"old Wi-Fi-only bridge"),
                 original.replace(package.USB_NVS_GUARD_MARKER, b"unsafe NVS initialization"),
                 original + b"\0" * package.LEGACY_SLOT_BYTES, b"\xe9tiny")
        for image in cases:
            with self.subTest(size=len(image)):
                path.write_bytes(image)
                with self.assertRaises(ValueError):
                    package.package_board("heltec-v4", spec, self.artifacts,
                                          self.output, VERSION, SOURCE, self.custom)
                self.assertFalse(self.output.exists())

    def test_missing_explicit_tree_never_falls_back_to_valid_ambient_or_default(self):
        spec, _ = self.qualified_full("heltec-v4")
        self.utility_tree(self.default, spec, "STALE DEFAULT")
        self.utility_tree(self.custom, spec, "AMBIENT")
        selected = self.work / "missing explicit tree"
        with platformio_build_dir(self.custom):
            with self.assertRaises(FileNotFoundError):
                package.package_board("heltec-v4", spec, self.artifacts,
                                      self.output, VERSION, SOURCE, selected)
        self.assertFalse(self.output.exists())

    def test_cli_explicit_tree_has_priority_and_preserves_artifact_build_dir(self):
        spec, identity = self.qualified_full("heltec-v4")
        expected = self.utility_tree(self.custom, spec, "CLI EXPLICIT")
        environment = os.environ.copy()
        environment["PLATFORMIO_BUILD_DIR"] = str(self.work / "missing ambient tree")
        result = subprocess.run([
            sys.executable, "-B", str(ROOT / "scripts/package_esp32_partition_migration.py"),
            "--build-dir", str(self.artifacts), "--pio-build-dir", str(self.custom),
            "--output-dir", str(self.output), "--version", VERSION, "--source", SOURCE,
            "--board", "heltec-v4",
        ], cwd=self.work, env=environment, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archives = list(self.output.glob("*.zip"))
        self.assertEqual(len(archives), 1)
        self.check_archive(archives[0], "heltec-v4", expected, identity)

    def test_cli_missing_selected_utility_publishes_no_board_files(self):
        spec, _ = self.qualified_full("heltec-v4")
        self.utility_tree(self.custom, spec, "CLI CURRENT")
        (self.custom / spec["lora_bridge"] / "firmware.bin").unlink()
        environment = os.environ.copy()
        environment["PLATFORMIO_BUILD_DIR"] = str(self.custom)
        result = subprocess.run([
            sys.executable, "-B", str(ROOT / "scripts/package_esp32_partition_migration.py"),
            "--build-dir", str(self.artifacts), "--output-dir", str(self.output),
            "--version", VERSION, "--source", SOURCE, "--board", "heltec-v4",
        ], cwd=self.work, env=environment, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.custom / spec["lora_bridge"] / "firmware.bin"), result.stderr)
        self.assertEqual(list(self.output.iterdir()), [])


class MigrationMetadataTest(unittest.TestCase):
    def setUp(self):
        self.fixture = MigrationBuildDirTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work = self.fixture.work
        self.project = self.fixture.project
        self.record = self.work / "metadata-queries.jsonl"

    def environment(self, **values):
        environment = os.environ.copy()
        environment.pop("PLATFORMIO_BUILD_DIR", None)
        environment["MIGRATION_TEST_METADATA_LOG"] = str(self.record)
        environment["MIGRATION_TEST_OTHER"] = "preserved-option"
        environment.update(values)
        return environment

    def queries(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_default_query_exposes_empty_override_without_mutating_caller(self):
        environment = self.environment()
        previous = environment.copy()
        resolved = package.resolve_pio_build_dir(environment=environment)
        self.assertEqual(resolved, self.fixture.default.resolve())
        self.assertEqual(environment, previous)
        query, = self.queries()
        self.assertEqual(query["args"], ["project", "config", "--json-output",
                                         "--project-dir", str(self.project)])
        self.assertEqual(query["cwd"], str(self.project))
        self.assertEqual(query["build_dir"], "")
        self.assertEqual(query["other"], "preserved-option")

    def test_literal_tilde_and_project_dir_environment_paths_use_metadata(self):
        for raw, expected in (
            ("relative tree", self.project / "relative tree"),
            (str(self.fixture.custom), self.fixture.custom),
            ("~/migration-test-tree", Path("~/migration-test-tree").expanduser()),
            ("${PROJECT_DIR}/interpolated tree", self.project / "interpolated tree"),
        ):
            with self.subTest(raw=raw):
                environment = self.environment(PLATFORMIO_BUILD_DIR=raw)
                self.assertEqual(package.resolve_pio_build_dir(environment=environment),
                                 expected.resolve())
                self.assertEqual(environment["PLATFORMIO_BUILD_DIR"], raw)
                self.assertEqual(self.queries()[-1]["build_dir"], raw)

    def test_configured_build_or_workspace_directory_does_not_use_default_tree(self):
        for name in ("configured build tree", "custom workspace/build"):
            with self.subTest(directory=name):
                expected = self.project / name
                environment = self.environment(MIGRATION_TEST_CONFIGURED_DIR=str(expected))
                self.assertEqual(package.resolve_pio_build_dir(environment=environment),
                                 expected.resolve())
        self.assertEqual(len(self.queries()), 2)

    def test_explicit_relative_absolute_and_tilde_paths_do_not_query(self):
        for value, expected in (
            (Path("explicit tree"), self.project / "explicit tree"),
            (self.fixture.custom, self.fixture.custom),
            (Path("~/migration-explicit-tree"), Path("~/migration-explicit-tree").expanduser()),
        ):
            with self.subTest(value=value):
                self.assertEqual(package.resolve_pio_build_dir(
                    value, environment=self.environment(PLATFORMIO_BUILD_DIR="ignored")),
                    expected.resolve())
        self.assertFalse(self.record.exists())

    def test_malformed_missing_duplicate_and_unresolved_metadata_fail_closed(self):
        malformed = (
            None, {}, [], "private-metadata-credential", 12,
            [["platformio", []]],
            [["platformio", [["build_dir", ""]]]],
            [["platformio", [["build_dir", None]]]],
            [["platformio", [["build_dir", ["tree"]]]]],
            [["platformio", [["build_dir", "${UNRESOLVED}/tree"]]]],
            [["platformio", [["build_dir", "tree\0suffix"]]]],
            [["platformio", [["build_dir", "one"], ["build_dir", "two"]]]],
            [["platformio", [["build_dir", "one"]]], ["platformio", []]],
            [["platformio", [["build_dir", "one"], ["build_dir", "two", "bad"]]]],
            [["platformio", [["build_dir", "one"]]], ["bad section"]],
        )
        self.fixture.default.mkdir(parents=True)
        for value in malformed:
            with self.subTest(metadata=value):
                environment = self.environment(MIGRATION_TEST_METADATA_JSON=json.dumps(value))
                with self.assertRaisesRegex(ValueError, "^cannot resolve the PlatformIO build directory$"):
                    package.resolve_pio_build_dir(environment=environment)
        self.assertFalse(self.fixture.output.exists())

    def test_query_failure_missing_executable_and_timeout_hide_raw_diagnostics(self):
        for environment in (
            self.environment(MIGRATION_TEST_METADATA_EXIT="9"),
            self.environment(PATH=str(self.work / "missing-executables")),
            self.environment(MIGRATION_TEST_METADATA_JSON="private-metadata-credential"),
        ):
            with self.subTest(environment_keys=sorted(environment)):
                with self.assertRaisesRegex(ValueError, "^cannot resolve the PlatformIO build directory$"):
                    package.resolve_pio_build_dir(environment=environment)
        with mock.patch.object(package.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(
                                   "pio", 30, stderr="private-metadata-credential")):
            with self.assertRaisesRegex(ValueError, "^cannot resolve the PlatformIO build directory$"):
                package.resolve_pio_build_dir(environment=self.environment())

    def test_cli_metadata_failure_publishes_nothing_and_never_logs_private_output(self):
        self.fixture.qualified_full("heltec-v4")
        environment = self.environment(MIGRATION_TEST_METADATA_EXIT="9")
        result = subprocess.run([
            sys.executable, "-B", str(ROOT / "scripts/package_esp32_partition_migration.py"),
            "--build-dir", str(self.fixture.artifacts), "--output-dir", str(self.fixture.output),
            "--version", VERSION, "--source", SOURCE, "--board", "heltec-v4",
        ], cwd=self.work, env=environment, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot resolve the PlatformIO build directory", result.stderr)
        self.assertNotIn("private-metadata-credential", result.stdout + result.stderr)
        self.assertFalse(self.fixture.output.exists())

    def test_migration_recipe_pins_one_query_across_full_utilities_and_real_packaging(self):
        import build_esp32_partition_migration as recipe
        spec, identity = self.fixture.qualified_full("heltec-v4")
        expected = self.fixture.utility_tree(self.fixture.custom, spec, "PINNED RECIPE")
        environment = self.environment(
            PLATFORMIO_BUILD_DIR="${PROJECT_DIR}/original-option",
            MIGRATION_TEST_CONFIGURED_DIR=str(self.fixture.custom))
        calls = []
        original_run = subprocess.run

        def run(command, **options):
            if command[:3] == ["pio", "project", "config"]:
                return original_run(command, **options)
            calls.append((list(command), options.get("env", {}).copy()))
            if command[0] == "bash":
                output = Path(options["env"]["OUTPUT_DIR"])
                output.mkdir(parents=True, exist_ok=True)
                for source in self.fixture.artifacts.iterdir():
                    (output / source.name).write_bytes(source.read_bytes())
            elif command[:2] == ["pio", "run"]:
                pass  # Compiler boundary; qualified utility bytes already exist.
            elif len(command) > 2 and command[2] == "scripts/package_esp32_partition_migration.py":
                with mock.patch.object(sys, "argv", command[2:]):
                    package.main()  # Actual hash-valid packaging and archive verification.
            else:
                self.fail("unexpected recipe process: " + repr(command))
            return subprocess.CompletedProcess(command, 0)

        def git(*arguments):
            return "" if arguments[0] == "status" else SOURCE

        output_root = self.work / "recipe release"
        with mock.patch.object(recipe, "ROOT", self.project), \
                mock.patch.object(recipe, "git_output", side_effect=git), \
                mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(subprocess, "run", side_effect=run), \
                mock.patch.object(sys, "argv", [
                    "migration-recipe", "--board", "heltec-v4", "--version", VERSION,
                    "--radio-preset", "usa-cascadia", "--output-root", str(output_root)]), \
                redirect_stdout(io.StringIO()):
            recipe.main()
        self.assertEqual(len(self.queries()), 1)
        self.assertEqual(len(calls), 6)  # One Full, four utilities, one packager.
        for command, child_environment in calls:
            self.assertEqual(child_environment["PLATFORMIO_BUILD_DIR"], str(self.fixture.custom.resolve()))
            self.assertEqual(child_environment["MIGRATION_TEST_OTHER"], "preserved-option")
            if len(command) > 2 and command[2] == "scripts/package_esp32_partition_migration.py":
                self.assertEqual(command[command.index("--pio-build-dir") + 1],
                                 str(self.fixture.custom.resolve()))
        archive, = (output_root / "packages").glob("*.zip")
        self.fixture.check_archive(archive, "heltec-v4", expected, identity)

    def test_local_release_main_passes_one_canonical_directory_to_all_later_phases(self):
        import build_local_release as recipe
        environment = self.environment(MIGRATION_TEST_CONFIGURED_DIR=str(self.fixture.custom))
        captured = []
        source = SOURCE + "a" * 32
        with mock.patch.object(recipe, "ROOT", self.project), \
                mock.patch.object(recipe, "git", side_effect=lambda *args: "" if args[0] == "status" else source), \
                mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(recipe, "run_logged", side_effect=lambda command, log, env:
                                  captured.append(("matrix", env.copy()))), \
                mock.patch.object(recipe, "build_embedded_bootloader_artifacts", side_effect=lambda *args:
                                  captured.append(("embedded", args[4].copy()))), \
                mock.patch.object(recipe, "build_migration_artifacts", side_effect=lambda *args:
                                  captured.append(("migration", args[4].copy(), args[5]))), \
                mock.patch.object(recipe, "stage_release", side_effect=lambda *args, **kwargs:
                                  captured.append(("package", args[5]))), \
                mock.patch.object(sys, "argv", ["local-release"]), \
                redirect_stdout(io.StringIO()):
            recipe.main()
        self.assertEqual(len(self.queries()), 1)
        self.assertEqual([value[0] for value in captured], ["matrix", "embedded", "migration", "package"])
        for item in captured[:3]:
            self.assertEqual(item[1]["PLATFORMIO_BUILD_DIR"], str(self.fixture.custom.resolve()))
            self.assertEqual(item[1]["MIGRATION_TEST_OTHER"], "preserved-option")
        self.assertEqual(captured[2][2], self.fixture.custom.resolve())
        self.assertEqual(captured[3][1], self.fixture.custom.resolve())

    def test_local_utility_builder_preserves_environment_and_never_requeries_pinned_directory(self):
        import build_local_release as recipe
        spec = package.BOARDS["heltec-v4"]
        environment = self.environment(PLATFORMIO_BUILD_DIR="${PROJECT_DIR}/not-pinned")
        previous = environment.copy()
        work = self.work / "local migration builds"
        (work / "build-logs").mkdir(parents=True)
        captured = []

        def run(command, **options):
            self.assertEqual(command[:2], ["pio", "run"])
            captured.append(("utility", options["env"].copy()))
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(recipe, "ROOT", self.project), \
                mock.patch.object(recipe, "BOARDS", {"heltec-v4": spec}), \
                mock.patch.object(recipe, "run_logged", side_effect=lambda command, log, env:
                                  captured.append(("full", env.copy()))), \
                mock.patch.object(subprocess, "run", side_effect=run), \
                redirect_stdout(io.StringIO()):
            recipe.build_migration_artifacts(work, VERSION, SOURCE, 2, environment,
                                             self.fixture.custom)
        self.assertEqual(environment, previous)
        self.assertFalse(self.record.exists())
        self.assertEqual([item[0] for item in captured],
                         ["full", "utility", "utility", "utility", "utility"])
        for _, child_environment in captured:
            self.assertEqual(child_environment["PLATFORMIO_BUILD_DIR"], str(self.fixture.custom.resolve()))
            self.assertEqual(child_environment["MIGRATION_TEST_OTHER"], "preserved-option")

    def test_local_staging_passes_pinned_directory_and_environment_to_actual_packaging(self):
        import build_local_release as recipe
        spec, identity = self.fixture.qualified_full("heltec-v4")
        expected = self.fixture.utility_tree(self.fixture.custom, spec, "LOCAL PINNED")
        captured = []

        def run(command, **options):
            self.assertEqual(command[2], "scripts/package_esp32_partition_migration.py")
            captured.append((command, options["env"].copy()))
            with mock.patch.object(sys, "argv", command[2:]):
                package.main()
            return subprocess.CompletedProcess(command, 0)

        destination = self.work / "local staged release"
        environment = self.environment(PLATFORMIO_BUILD_DIR="wrong parent directory")
        with mock.patch.object(recipe, "ROOT", self.project), \
                mock.patch.object(recipe, "BOARDS", {"heltec-v4": spec}), \
                mock.patch.object(package, "BOARDS", {"heltec-v4": spec}), \
                mock.patch.object(recipe, "git", return_value=SOURCE + "a" * 32), \
                mock.patch.object(recipe, "digest", side_effect=lambda path:
                                  hashlib.sha256(path.read_bytes()).hexdigest()), \
                mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(subprocess, "run", side_effect=run), \
                redirect_stdout(io.StringIO()):
            recipe.stage_release(self.fixture.artifacts, self.fixture.artifacts, destination,
                                 VERSION, SOURCE + "a" * 32, self.fixture.custom)
        self.assertFalse(self.record.exists())  # Explicit directory bypasses metadata.
        command, child_environment = captured[0]
        self.assertEqual(child_environment["PLATFORMIO_BUILD_DIR"], str(self.fixture.custom.resolve()))
        self.assertEqual(child_environment["MIGRATION_TEST_OTHER"], "preserved-option")
        self.assertEqual(command[command.index("--pio-build-dir") + 1],
                         str(self.fixture.custom.resolve()))
        self.assertIn("--release-build-dir", command)
        self.assertEqual(Path(command[command.index("--release-build-dir") + 1]).name, "firmware")
        archive, = (destination / "esp32-partition-migration").glob("*.zip")
        self.fixture.check_archive(archive, "heltec-v4", expected, identity)

    def test_local_utility_builder_never_rebuilds_an_already_qualified_published_target(self):
        import build_local_release as recipe
        spec, _ = self.fixture.qualified_full("heltec-v4")
        work = self.work / "migration only builds"
        (work / "build-logs").mkdir(parents=True)
        with mock.patch.object(recipe, "ROOT", self.project), \
                mock.patch.object(recipe, "BOARDS", {"heltec-v4": spec}), \
                mock.patch.object(recipe, "run_logged") as build_full, \
                mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                redirect_stdout(io.StringIO()):
            recipe.build_migration_artifacts(work, VERSION, SOURCE, 2, {},
                self.fixture.custom, self.fixture.artifacts)
        build_full.assert_not_called()

    def test_local_release_rejects_full_without_verified_marker(self):
        import build_local_release as recipe
        self.fixture.qualified_full("heltec-v4")
        records = recipe.collect_artifacts(self.fixture.artifacts, VERSION + "-" + SOURCE)
        self.assertEqual(recipe.select_ordinary_full_records(records), records)
        records[0]["manifest"].pop("verified")
        with self.assertRaisesRegex(ValueError, "requires a verified Full image"):
            recipe.select_ordinary_full_records(records)

    def test_recipe_metadata_failure_stops_before_output_creation_without_private_diagnostic(self):
        import build_esp32_partition_migration as migration
        import build_local_release as local
        environment = self.environment(MIGRATION_TEST_METADATA_EXIT="9")
        for recipe, argv in (
            (migration, ["migration", "--board", "heltec-v4", "--version", VERSION,
                         "--radio-preset", "usa-cascadia",
                         "--output-root", str(self.work / "failed-output")]),
            (local, ["local-release"]),
        ):
            with self.subTest(recipe=recipe.__name__):
                git_name = "git_output" if recipe is migration else "git"
                errors = io.StringIO()
                with mock.patch.object(recipe, "ROOT", self.project), \
                        mock.patch.object(recipe, git_name,
                                          side_effect=lambda *args: "" if args[0] == "status" else SOURCE), \
                        mock.patch.dict(os.environ, environment, clear=True), \
                        mock.patch.object(sys, "argv", argv), \
                        redirect_stdout(io.StringIO()), redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as stopped:
                        recipe.main()
                    self.assertEqual(stopped.exception.code, 2)
                self.assertIn("cannot resolve the PlatformIO build directory", errors.getvalue())
                self.assertNotIn("private-metadata-credential", errors.getvalue())
        self.assertFalse((self.work / "failed-output").exists())
        self.assertFalse((self.project / ".releases").exists())

    def test_dry_runs_and_dirty_source_do_not_query_or_create_outputs(self):
        import build_esp32_partition_migration as migration
        import build_local_release as local
        for recipe, argv in (
            (migration, ["migration", "--board", "heltec-v4", "--version", VERSION,
                         "--radio-preset", "usa-cascadia"]),
            (local, ["local-release"]),
        ):
            with self.subTest(recipe=recipe.__name__):
                git_name = "git_output" if recipe is migration else "git"
                with mock.patch.object(recipe, "ROOT", self.project), \
                        mock.patch.object(recipe, git_name,
                                          side_effect=lambda *args: "dirty" if args[0] == "status" else SOURCE), \
                        mock.patch.object(sys, "argv", argv + ["--dry-run"]), \
                        redirect_stdout(io.StringIO()):
                    recipe.main()
                with mock.patch.object(recipe, "ROOT", self.project), \
                        mock.patch.object(recipe, git_name,
                                          side_effect=lambda *args: "dirty" if args[0] == "status" else SOURCE), \
                        mock.patch.object(sys, "argv", argv), \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        recipe.main()
                    self.assertEqual(stopped.exception.code, 2)
        self.assertFalse(self.record.exists())
        self.assertFalse((self.project / ".releases").exists())


@unittest.skipUnless(os.environ.get("MESHCORE_TEST_REAL_PIO_PATHS") == "1",
                     "real PlatformIO metadata checks require explicit opt-in")
class RealPlatformIOPathTest(unittest.TestCase):
    """Opt-in metadata integration only; never compile or create build output."""

    def test_real_platformio_resolves_nine_directory_selections_without_building(self):
        with tempfile.TemporaryDirectory(prefix="migration-real-pio-paths-") as temporary:
            work = Path(temporary)
            projects = {
                "default": "[platformio]\n\n[env:probe]\n",
                "config": ("[platformio]\n"
                           "build_dir = ${PROJECT_DIR}/configured build/with spaces\n"
                           "\n[env:probe]\n"),
                "workspace": ("[platformio]\n"
                              "workspace_dir = ${sysenv.MESHCORE_TEST_PATH_WORKSPACE}\n"
                              "\n[env:probe]\n"),
            }
            for name, content in projects.items():
                project = work / ("project " + name)
                project.mkdir()
                (project / "platformio.ini").write_text(content, encoding="ascii")
            project = work / "project default"
            # A unique home-relative name exercises expanduser without writing
            # into the home directory or colliding with another test's output.
            tilde = "~/" + work.name + "-home-build"
            base = os.environ.copy()
            base.pop("PLATFORMIO_BUILD_DIR", None)
            base.pop("PLATFORMIO_WORKSPACE_DIR", None)
            original_environment = os.environ.copy()
            cases = (
                ("default", project, {}, project / ".pio/build"),
                ("relative", project, {"PLATFORMIO_BUILD_DIR": "relative build"},
                 project / "relative build"),
                ("absolute", project, {"PLATFORMIO_BUILD_DIR": str(work / "absolute build")},
                 work / "absolute build"),
                ("tilde", project, {"PLATFORMIO_BUILD_DIR": tilde}, Path(tilde).expanduser()),
                ("project interpolation", project,
                 {"PLATFORMIO_BUILD_DIR": "${PROJECT_DIR}/project build"},
                 project / "project build"),
                ("environment interpolation", project,
                 {"PLATFORMIO_BUILD_DIR": "${sysenv.MESHCORE_TEST_PATH_BUILD_ROOT}/objects",
                  "MESHCORE_TEST_PATH_BUILD_ROOT": str(work / "env build")},
                 work / "env build/objects"),
                ("workspace environment", project,
                 {"PLATFORMIO_WORKSPACE_DIR": str(work / "workspace")},
                 work / "workspace/build"),
                ("configured build", work / "project config", {},
                 work / "project config/configured build/with spaces"),
                ("configured workspace", work / "project workspace",
                 {"MESHCORE_TEST_PATH_WORKSPACE": str(work / "configured workspace")},
                 work / "configured workspace/build"),
            )
            initial_files = {path.relative_to(work): path.read_bytes()
                             for path in work.rglob("*") if path.is_file()}
            initial_directories = {path.relative_to(work)
                                   for path in work.rglob("*") if path.is_dir()}
            for name, root, changes, expected in cases:
                with self.subTest(case=name):
                    environment = {**base, **changes}
                    before = environment.copy()
                    self.assertFalse(expected.exists())
                    selected = package.resolve_pio_build_dir(
                        environment=environment, project_dir=root)
                    self.assertEqual(selected, expected.resolve())
                    self.assertEqual(environment, before)
                    self.assertFalse(selected.exists(), "metadata query created build output")
                    self.assertFalse((root / ".pio").exists())
            self.assertEqual(os.environ, original_environment)
            self.assertEqual(initial_files, {path.relative_to(work): path.read_bytes()
                                            for path in work.rglob("*") if path.is_file()})
            self.assertEqual(initial_directories, {path.relative_to(work)
                                                  for path in work.rglob("*") if path.is_dir()})


if __name__ == "__main__":
    unittest.main()
