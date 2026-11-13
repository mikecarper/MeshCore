#!/usr/bin/env python3
"""Exercise real package qualification and safe recipe-bound resume, without PIO."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("firmware_build_recipe", ROOT / "scripts/firmware_build_recipe.py")
RECIPE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECIPE)
SECRET = "private-credential-not-for-manifests"
TARGET = "test_companion_radio_usb"
OPTIONS = [["env:" + TARGET, [["build_flags", ["-DESP32_PLATFORM"]],
                              ["board", "test-board"], ["extra_scripts", []]]]]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class RecipeTests(unittest.TestCase):
    def source_fixture(self, root, *, untracked=(), stage=b"0", tracked="source.cpp"):
        def git(_root, *arguments):
            if arguments == ("rev-parse", "HEAD"):
                return b"a" * 40 + b"\n"
            if arguments == ("ls-files", "--stage", "-z"):
                return b"100644 " + b"b" * 40 + b" " + stage + b"\t" + os.fsencode(tracked) + b"\0"
            if arguments == ("ls-files", "--others", "--exclude-standard", "-z"):
                return b"\0".join(os.fsencode(name) for name in untracked)
            raise AssertionError(arguments)
        return mock.patch.object(RECIPE, "git", side_effect=git)

    def test_actual_tracked_bytes_deletions_and_untracked_source_change_digest(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-source-") as temporary:
            root = Path(temporary)
            path = root / "source.cpp"; path.write_bytes(b"original tracked source")
            with self.source_fixture(root):
                original = RECIPE.source_digest(root, root / "output")
                path.write_bytes(b"changed without changing HEAD or index")
                self.assertNotEqual(original, RECIPE.source_digest(root, root / "output"))
                path.unlink()
                self.assertNotEqual(original, RECIPE.source_digest(root, root / "output"))
                path.write_bytes(b"original tracked source")
            extra = root / "added.cpp"; extra.write_bytes(b"uncommitted source")
            with self.source_fixture(root, untracked=(extra.name,)):
                self.assertNotEqual(original, RECIPE.source_digest(root, root / "output"))
            with self.source_fixture(root, stage=b"2"):
                with self.assertRaises(ValueError):
                    RECIPE.source_digest(root, root / "output")

    def test_source_commit_must_match_filename_and_remain_stable_during_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-source-") as temporary:
            root = Path(temporary)
            (root / "source.cpp").write_bytes(b"source")
            with self.source_fixture(root):
                with self.assertRaises(ValueError):
                    RECIPE.source_digest(root, root / "output", expected_commit="d" * 40)
            commits = iter((b"a" * 40, b"b" * 40))
            def git(_root, *arguments):
                return next(commits) if arguments == ("rev-parse", "HEAD") else b""
            with mock.patch.object(RECIPE, "git", side_effect=git):
                with self.assertRaises(ValueError):
                    RECIPE.source_digest(root, root / "output", expected_commit="a" * 40)

    def test_output_root_aliases_and_ancestors_are_rejected_instead_of_hiding_source(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-source-") as temporary:
            root = Path(temporary)
            (root / "source.cpp").write_bytes(b"source")
            link = root / "root-link"; link.symlink_to(root, target_is_directory=True)
            with self.source_fixture(root):
                for output in (root, root / "child/..", root.parent, Path("/"), link):
                    with self.subTest(output=output), self.assertRaises(ValueError):
                        RECIPE.source_digest(root, output)

    def test_tracked_sources_inside_output_are_hashed_but_untracked_artifacts_are_not(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-source-") as temporary:
            root = Path(temporary)
            output = root / "src"; output.mkdir()
            source = output / "source.cpp"; source.write_bytes(b"tracked source")
            artifact = output / "generated.bin"; artifact.write_bytes(b"old artifact")
            with self.source_fixture(root, tracked="src/source.cpp", untracked=("src/generated.bin",)):
                first = RECIPE.source_digest(root, output)
                artifact.write_bytes(b"new artifact")
                self.assertEqual(first, RECIPE.source_digest(root, output))
                source.write_bytes(b"changed tracked source")
                self.assertNotEqual(first, RECIPE.source_digest(root, output))

    def test_ignored_local_config_absence_presence_and_bytes_are_source_inputs(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-source-") as temporary:
            root = Path(temporary)
            (root / "source.cpp").write_bytes(b"source")
            local = root / "platformio.local.ini"
            with self.source_fixture(root):
                absent = RECIPE.source_digest(root, root / "output")
                local.write_text("[env:test]\nbuild_flags=-DONE=1\n")
                present = RECIPE.source_digest(root, root / "output")
                self.assertNotEqual(absent, present)
                local.write_text("[env:test]\nbuild_flags=-DONE=2\n")
                self.assertNotEqual(present, RECIPE.source_digest(root, root / "output"))

    def test_external_config_imports_globs_nested_files_and_missing_matches_are_bound(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-config-") as temporary:
            directory = Path(temporary)
            root = directory / "project"; root.mkdir()
            external = directory / "external.ini"
            nested = directory / "nested.ini"
            overrides = directory / "overrides"; overrides.mkdir()
            external.write_text("[platformio]\nextra_configs = ../nested.ini\n")
            nested.write_text("[env:test]\nbuild_flags=-DPRIVATE=" + SECRET + "\n")
            # An intermediate external file is not necessarily listed in the
            # final resolved extra_configs value, but remains an input.
            (root / "platformio.ini").write_text("[platformio]\nextra_configs=../external.ini\n")
            options = {"extra_configs": [str(directory / "overrides/*.ini"), "../nested.ini"]}
            before = RECIPE.canonical_digest(RECIPE.configuration_inputs(root, options))
            external.write_text("[platformio]\nextra_configs=../nested.ini\n[env:test]\nboard=changed\n")
            after = RECIPE.canonical_digest(RECIPE.configuration_inputs(root, options))
            self.assertNotEqual(before, after)
            (overrides / "added.ini").write_text("[env:test]\nboard=added\n")
            self.assertNotEqual(after, RECIPE.canonical_digest(RECIPE.configuration_inputs(root, options)))
            for pattern in ("${sysenv.UNRESOLVED_RECIPE_CONFIG}", "%(unresolved_config)s"):
                with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                    RECIPE.configuration_inputs(root, {"extra_configs": [pattern]})

    def test_custom_output_and_output_link_do_not_hash_generated_files(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-source-") as temporary:
            root = Path(temporary)
            (root / "source.cpp").write_bytes(b"source")
            output = root / "unignored-artifacts"; output.mkdir()
            generated = output / "firmware.bin"; generated.write_bytes(b"old artifact")
            with self.source_fixture(root, untracked=("unignored-artifacts/firmware.bin",)):
                initial = RECIPE.source_digest(root, output)
                generated.write_bytes(b"new artifact")
                self.assertEqual(initial, RECIPE.source_digest(root, output))
            link = root / "output-link"; link.symlink_to(output, target_is_directory=True)
            with self.source_fixture(root, untracked=("output-link", "unignored-artifacts/firmware.bin")):
                self.assertEqual(initial, RECIPE.source_digest(root, link))

    def test_gitlinks_and_source_symlink_targets_do_not_hide_local_changes(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-source-") as temporary:
            root = Path(temporary)
            nested = root / "nested"; nested.mkdir()
            gitlink = b"160000 " + b"b" * 40 + b" 0\tnested\0"
            def git(where, *arguments):
                if arguments == ("rev-parse", "HEAD"):
                    return b"a" * 40 + b"\n"
                if arguments == ("ls-files", "--stage", "-z"):
                    return gitlink if where == root else b"100644 " + b"c" * 40 + b" 0\tfile.cpp\0"
                return b""
            with mock.patch.object(RECIPE, "git", side_effect=git):
                RECIPE.source_digest(root, root / "output")
                source = nested / "file.cpp"; source.write_bytes(b"local source")
                with self.assertRaises(ValueError):
                    RECIPE.source_digest(root, root / "output")
                (nested / ".git").write_text("fixture metadata marker")
                first = RECIPE.source_digest(root, root / "output")
                source.write_bytes(b"changed local source")
                self.assertNotEqual(first, RECIPE.source_digest(root, root / "output"))
            external = root / "external.txt"; external.write_bytes(b"initial target")
            (root / "source.cpp").symlink_to(external)
            with self.source_fixture(root):
                first = RECIPE.source_digest(root, root / "output")
                external.write_bytes(b"changed target")
                self.assertNotEqual(first, RECIPE.source_digest(root, root / "output"))

    def test_every_package_suffix_and_broken_symlink_counts_as_occupied(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-occupied-") as temporary:
            stem = Path(temporary) / "firmware"
            self.assertFalse(RECIPE.package_occupied(stem))
            for suffix in RECIPE.PACKAGE_SUFFIXES:
                path = Path(str(stem) + suffix)
                path.write_bytes(b"")
                self.assertTrue(RECIPE.package_occupied(stem))
                path.unlink()
            path.symlink_to(stem.parent / "missing")
            self.assertTrue(RECIPE.package_occupied(stem))

    def test_selected_options_are_canonical_but_ordered_flags_and_source_are_bound(self):
        args = argparse.Namespace(root=ROOT, output=ROOT / "out", target=TARGET,
                                  artifact_target=TARGET, pio_env=TARGET, platform="ESP32_PLATFORM",
                                  source_commit="a" * 40,
                                  embedded_version="vtest.12-abcdef00", profile="auto",
                                  sensor_profile="", ota_policy="0")
        environment = {"PLATFORMIO_BUILD_FLAGS": "-DONE=1 -UTWO -DTWO=2",
                       "MESHCORE_RECIPE_ORIGINAL_FLAGS": "-DWIFI_PWD=" + SECRET}
        with mock.patch.object(RECIPE, "source_digest", return_value="c" * 64), \
                mock.patch.dict(os.environ, environment, clear=True):
            first = RECIPE.recipe_digest(args, OPTIONS)
            reordered = [[OPTIONS[0][0], list(reversed(OPTIONS[0][1]))],
                         ["env:unrelated", [["build_flags", ["not compiled"]]]]]
            self.assertEqual(first, RECIPE.recipe_digest(args, reordered))
            os.environ["PLATFORMIO_BUILD_FLAGS"] = "-DONE=1 -DTWO=2 -UTWO"
            self.assertNotEqual(first, RECIPE.recipe_digest(args, OPTIONS))
            os.environ.update(environment)
            args.embedded_version = "vtest.13-abcdef00"
            self.assertNotEqual(first, RECIPE.recipe_digest(args, OPTIONS))
            args.embedded_version = "vtest.12-abcdef00"
            os.environ["MESHCORE_NEW_RECIPE_OPTION"] = "1"
            self.assertNotEqual(first, RECIPE.recipe_digest(args, OPTIONS))

    def test_stored_recipe_is_digest_only_and_has_strict_schema(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-manifest-") as temporary:
            manifest = Path(temporary) / "capabilities.json"
            manifest.write_text(json.dumps({"verified": True}))
            digest = RECIPE.canonical_digest({"credentials": SECRET})
            RECIPE.attach_recipe(manifest, digest)
            text = manifest.read_text()
            self.assertNotIn(SECRET, text)
            self.assertTrue(RECIPE.matches_recipe(manifest, digest))
            self.assertEqual(json.loads(text)["build_recipe"],
                             {"schema_version": 1, "sha256": digest})
            for record in (None, {}, {"schema_version": True, "sha256": digest},
                           {"schema_version": 1, "sha256": "broken"},
                           {"schema_version": 1, "sha256": digest, "flags": SECRET}):
                manifest.write_text(json.dumps({"build_recipe": record}))
                self.assertFalse(RECIPE.matches_recipe(manifest, digest))

    def test_malformed_inputs_cannot_echo_credentials_in_diagnostics(self):
        command = ["python3", "-B", str(ROOT / "scripts/firmware_build_recipe.py"), "digest",
                   "--root", str(ROOT), "--output", str(ROOT / "out"), "--target", TARGET,
                   "--artifact-target", TARGET, "--pio-env", TARGET, "--platform", "ESP32_PLATFORM",
                   "--source-commit", "a" * 40,
                   "--embedded-version", "vtest", "--profile", "auto", "--sensor-profile", "",
                   "--ota-policy", "0"]
        result = subprocess.run(command, input=SECRET, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mesh-recipe-resume-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        (self.work / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
        self.output = self.work / "artifacts"; self.output.mkdir()
        self.build_dir = self.work / "current-builds"
        build = self.build_dir / TARGET; build.mkdir(parents=True)
        image = (b"qualified fixture\0ERR usage: tempradio\0OTA: status\0"
                 b"image_hash MISMATCH after decode\0")
        (build / "firmware.elf").write_bytes(image)
        (build / "firmware.bin").write_bytes(image)
        (build / "firmware-merged.bin").write_bytes(b"merged fixture\0" + image)
        table = struct.pack("<HBBII16sI", 0x50AA, 0, 0x10, 0x10000, 0x140000, b"ota_0", 0)
        (build / "partitions.bin").write_bytes(table)
        report = dict(schema_version=1, passed=True, target=TARGET,
                      available_internal_bytes=80000, required_heap_bytes=50000,
                      largest_internal_region_bytes=80000, required_contiguous_bytes=5120,
                      elf_sha256=sha256(image))
        (build / "firmware.memory.json").write_text(json.dumps(report))

    def build(self, *, version="v1.17.1.9", settings="", succeed=False, ambient="exported",
              clock="", after_build="", logged=False, source_root=None):
        script = r'''
source "$1/build_legacy.sh"
recipe_root=$1
git() { command git -C "$recipe_root" "$@"; }
pio() { printf 'FORBIDDEN_PLATFORMIO\n' >&2; return 99; }
prepare_esp32_arduino3_framework() { :; }
print_build_flags() { :; }
PIO_ENV_PLATFORM_BY_NAME[test_companion_radio_usb]=ESP32_PLATFORM
PIO_CONFIG_JSON=$4
OUTPUT_DIR=$2
PIO_BUILD_DIR_OVERRIDE=$3
FIRMWARE_VERSION=$5
RESUME_BUILD_OUTPUT=1
RADIO_FREQ_OVERRIDE=868.1
RADIO_BW_OVERRIDE=125
RADIO_SF_OVERRIDE=8
RADIO_CR_OVERRIDE=6
FIRMWARE_PROFILE_OVERRIDE=default
REQUIRE_OTA_UPDATES=0
recipe_source=$6
if [ -n "$recipe_source" ]; then
  fixture_git_dir=$(command git -C "$recipe_root" rev-parse --absolute-git-dir)
  compute_build_recipe_digest() {
    GIT_DIR="$fixture_git_dir" GIT_WORK_TREE="$recipe_source" \
      MESHCORE_RECIPE_ORIGINAL_FLAGS="$5" \
      command python3 "$recipe_root/scripts/firmware_build_recipe.py" digest \
        --root "$recipe_source" --output "$OUTPUT_DIR" --source-commit "$6" \
        --target "$1" --artifact-target "$1" --pio-env "$3" --platform "$2" \
        --embedded-version "$4" --profile "$BUILD_PROFILE_FOR_TARGET" \
        --sensor-profile "${NRF52_OTA_SENSOR_PROFILE:-}" --ota-policy "$REQUIRE_OTA_UPDATES" \
        <<<"$PIO_CONFIG_JSON"
  }
fi
'''
        values = {"PLATFORMIO_BUILD_FLAGS": "-DWIFI_PWD='\\\"" + SECRET + "\\\"'",
                  "PLATFORMIO_BUILD_UNFLAGS": "-DOLD_FLAG",
                  "PLATFORMIO_BUILD_SRC_FILTER": "+<original/*.cpp>",
                  "PLATFORMIO_EXTRA_SCRIPTS": "pre:scripts/original.py"}
        if ambient == "unset":
            script += "unset " + " ".join(values) + "\n"
        else:
            prefix = "export " if ambient == "exported" else ""
            for name, value in values.items():
                script += prefix + name + "=" + self.shell_quote(value) + "\n"
        script += settings + "\n" + clock + "\n"
        script += r'''
ambient_before=$(for name in PLATFORMIO_BUILD_FLAGS PLATFORMIO_BUILD_UNFLAGS PLATFORMIO_BUILD_SRC_FILTER PLATFORMIO_EXTRA_SCRIPTS BUILD_RECIPE_SHA256 recipe_build_flags completed_recipe; do declare -p "$name" 2>/dev/null || :; done)
run_pio_with_size_detection() {
  printf 'STUB_BUILD_REQUIRED\n'
'''
        script += after_build + "\nreturn " + ("0" if succeed else "89") + "\n}\n"
        script += r'''
build_firmware_one_profile test_companion_radio_usb
build_result=$?
ambient_after=$(for name in PLATFORMIO_BUILD_FLAGS PLATFORMIO_BUILD_UNFLAGS PLATFORMIO_BUILD_SRC_FILTER PLATFORMIO_EXTRA_SCRIPTS BUILD_RECIPE_SHA256 recipe_build_flags completed_recipe; do declare -p "$name" 2>/dev/null || :; done)
if [ "$ambient_before" != "$ambient_after" ]; then printf 'AMBIENT_CHANGED\n' >&2; exit 77; fi
exit "$build_result"
'''
        if logged:
            script = script.replace("build_firmware_one_profile test_companion_radio_usb\n",
                                    "run_logged_build_targets test_companion_radio_usb\n")
            script = script.replace('exit "$build_result"\n',
                                    'for failure in "${LOGGING_MATRIX_FAILURES[@]}"; do '
                                    'printf "RECORDED_FAILURE:%s\\n" "$failure"; done\n'
                                    'exit "$build_result"\n')
        result = subprocess.run(["bash", "-c", script, "recipe-test", str(ROOT), str(self.output),
                                 str(self.build_dir), json.dumps(OPTIONS), version,
                                 str(source_root) if source_root is not None else ""],
                                cwd=self.work, text=True, capture_output=True, timeout=45)
        self.assertNotIn("FORBIDDEN_PLATFORMIO", result.stdout + result.stderr)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn("AMBIENT_CHANGED", result.stdout + result.stderr)
        return result

    @staticmethod
    def shell_quote(value):
        return "'" + value.replace("'", "'\"'\"'") + "'"

    def qualified_fixture(self, **kwargs):
        result = self.build(succeed=True, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("STUB_BUILD_REQUIRED", result.stdout)
        capabilities = next(self.output.glob("*.capabilities.json"))
        self.stem = Path(str(capabilities).removesuffix(".capabilities.json"))
        self.capabilities = capabilities
        self.memory = Path(str(self.stem) + ".memory.json")
        self.assertNotIn(SECRET, capabilities.read_text() + self.memory.read_text())
        self.validate_fixture()

    def validate_fixture(self):
        result = subprocess.run(["python3", "-B", str(ROOT / "scripts/firmware_memory_manifest.py"),
                                 "validate-package", str(self.stem)], cwd=ROOT,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def snapshot(self):
        return {str(path.relative_to(self.output)):
                (os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None,
                 path.lstat().st_ino, path.lstat().st_mtime_ns)
                for path in self.output.rglob("*")}

    def reseal_capabilities(self):
        report = json.loads(self.memory.read_text())
        report["files"][self.capabilities.name] = sha256(self.capabilities.read_bytes())
        self.memory.write_text(json.dumps(report))
        self.validate_fixture()

    def assert_conflict(self, **kwargs):
        before = self.snapshot()
        result = self.build(**kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("STUB_BUILD_REQUIRED", result.stdout)
        self.assertNotIn("Skipping", result.stdout)
        self.assertIn("fresh OUTPUT_DIR", result.stderr)
        self.assertEqual(before, self.snapshot())
        return result

    def test_resume_recipe_rejection_does_not_execute_artifact_gate(self):
        self.qualified_fixture()
        result = self.assert_conflict(settings=r'''RADIO_FREQ_OVERRIDE=910.525
build_artifacts_exist() { printf 'FORBIDDEN_ARTIFACT_GATE\n' >&2; return 73; }
''')
        self.assertIn("Resume qualification gates: recipe_exit=1; artifact_capability_ram_exit=not-run.",
                      result.stderr)
        self.assertNotIn("FORBIDDEN_ARTIFACT_GATE", result.stdout + result.stderr)
        self.assertIn("existing package has a missing/different recipe or failed qualification.",
                      result.stderr)

    def test_resume_recipe_error_status_is_private_and_still_fails_closed(self):
        self.qualified_fixture()
        result = self.assert_conflict(settings=r'''
python3() {
  if [ "$1" = scripts/firmware_build_recipe.py ] && [ "$2" = matches ]; then
    printf '%s\n' PRIVATE_FIXTURE_SECRET
    printf '%s\n' PRIVATE_FIXTURE_SECRET >&2
    return 73
  fi
  command python3 "$@"
}
build_artifacts_exist() { printf 'FORBIDDEN_ARTIFACT_GATE\n' >&2; return 74; }
'''.replace("PRIVATE_FIXTURE_SECRET", SECRET))
        self.assertEqual(result.returncode, 1)
        self.assertIn("recipe_exit=73; artifact_capability_ram_exit=not-run.", result.stderr)
        self.assertNotIn("FORBIDDEN_ARTIFACT_GATE", result.stdout + result.stderr)

    def test_resume_corrupt_artifact_and_failed_ram_report_identify_qualification_gate(self):
        self.qualified_fixture()
        application = Path(str(self.stem) + ".bin")
        original_application = application.read_bytes()
        original_memory = self.memory.read_bytes()
        for failure in ("application", "ram"):
            with self.subTest(failure=failure):
                application.write_bytes(original_application)
                self.memory.write_bytes(original_memory)
                if failure == "application":
                    application.write_bytes(b"tampered packaged application")
                else:
                    manifest = json.loads(original_memory)
                    manifest["passed"] = False
                    self.memory.write_text(json.dumps(manifest))
                result = self.assert_conflict()
                self.assertIn("recipe_exit=0; artifact_capability_ram_exit=1.", result.stderr)

    def test_resume_missing_packaged_marker_identifies_artifact_gate_without_exposing_marker(self):
        self.qualified_fixture()
        # Add a current requirement after the real contract is declared. The
        # cached package remains sealed and its recipe still matches, but its
        # actual packaged-application evidence cannot satisfy this requirement.
        result = self.assert_conflict(settings=r'''
contract=$(declare -f declare_build_capability_contract)
eval "${contract/declare_build_capability_contract /fixture_original_contract }"
declare_build_capability_contract() {
  fixture_original_contract "$@"
  BUILD_APPLICATION_EXPECTATIONS+=("fixture.private=''' + SECRET + r'''")
}
''')
        self.assertIn("recipe_exit=0; artifact_capability_ram_exit=1.", result.stderr)
        self.validate_fixture()

    def test_resume_artifact_error_status_suppresses_private_gate_output(self):
        self.qualified_fixture()
        result = self.assert_conflict(settings=r'''
build_artifacts_exist() {
  printf '%s\n' PRIVATE_FIXTURE_SECRET
  printf '%s\n' PRIVATE_FIXTURE_SECRET >&2
  return 74
}
'''.replace("PRIVATE_FIXTURE_SECRET", SECRET))
        self.assertEqual(result.returncode, 1)
        self.assertIn("recipe_exit=0; artifact_capability_ram_exit=74.", result.stderr)

    def test_fresh_build_attaches_recipe_and_exact_qualified_match_skips(self):
        self.qualified_fixture()
        before = self.snapshot()
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("matching recipe and qualified artifacts", result.stdout)
        self.assertNotIn("Resume qualification gates:", result.stdout + result.stderr)
        self.assertNotIn("STUB_BUILD_REQUIRED", result.stdout)
        self.assertEqual(before, self.snapshot())

    def test_generated_clock_changes_do_not_invalidate_recipe(self):
        self.qualified_fixture()
        result = self.build(clock=r'''date() { case "$*" in *+%s*) printf '9999999999\n';; *) printf '01-Jan-2099\n';; esac; }''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Skipping", result.stdout)

    def test_real_logged_recipe_resume_preserves_original_compile_log(self):
        self.qualified_fixture()
        logs = self.output / "build-logs"; logs.mkdir()
        log = logs / (TARGET + "-standard.log")
        log.write_bytes(b"original compiler output and memory qualification\n")
        before = (log.read_bytes(), log.stat().st_ino, log.stat().st_mtime_ns)
        artifacts = {name: value for name, value in self.snapshot().items() if not name.startswith("build-logs")}
        result = self.build(logged=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("STUB_BUILD_REQUIRED", result.stdout + log.read_text())
        self.assertEqual(before, (log.read_bytes(), log.stat().st_ino, log.stat().st_mtime_ns))
        self.assertEqual(artifacts, {name: value for name, value in self.snapshot().items()
                                    if not name.startswith("build-logs")})
        self.assertFalse(Path(str(log) + ".tmp").exists())

    def test_real_logged_resume_conflict_preserves_prior_log_and_points_to_new_attempt(self):
        self.qualified_fixture()
        logs = self.output / "build-logs"; logs.mkdir()
        log = logs / (TARGET + "-standard.log")
        log.write_bytes(b"original compiler output and memory qualification\n")
        before = (log.read_bytes(), log.stat().st_ino, log.stat().st_mtime_ns)
        artifacts = {name: value for name, value in self.snapshot().items() if not name.startswith("build-logs")}
        result = self.build(logged=True, settings="RADIO_FREQ_OVERRIDE=910.525")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(before, (log.read_bytes(), log.stat().st_ino, log.stat().st_mtime_ns))
        self.assertEqual(artifacts, {name: value for name, value in self.snapshot().items()
                                    if not name.startswith("build-logs")})
        attempts = list(logs.glob(TARGET + "-standard-resume-attempt.*.log"))
        self.assertEqual(len(attempts), 1)
        attempt = attempts[0]
        self.assertIn("Cannot resume", attempt.read_text())
        self.assertIn("recipe_exit=1; artifact_capability_ram_exit=not-run.", attempt.read_text())
        self.assertNotIn("STUB_BUILD_REQUIRED", result.stdout + attempt.read_text())
        self.assertNotIn(SECRET, attempt.read_text())
        self.assertIn("status 1; log: " + str(attempt), result.stdout)
        self.assertIn("RECORDED_FAILURE:" + TARGET + " (standard) -> " + str(attempt), result.stdout)

    def test_changed_radio_profile_build_number_and_ambient_options_stop_without_overwrite(self):
        self.qualified_fixture()
        for settings in ("RADIO_FREQ_OVERRIDE=910.525", "RADIO_BW_OVERRIDE=62.5",
                         "RADIO_SF_OVERRIDE=7", "RADIO_CR_OVERRIDE=5",
                         "FIRMWARE_PROFILE_OVERRIDE=cascade", "FIRMWARE_BUILD_NUMBER=2",
                         "export PLATFORMIO_BUILD_FLAGS+=' -DCHANGED_OPTION=1'",
                         "export PLATFORMIO_BUILD_UNFLAGS+=' -DANOTHER_OPTION'",
                         "export PLATFORMIO_BUILD_SRC_FILTER+=' +<changed/*.cpp>'",
                         "export PLATFORMIO_EXTRA_SCRIPTS+=' post:scripts/changed.py'",
                         "export MESHCORE_REDUCED_TLS=1",
                         "PIO_CONFIG_JSON=" + self.shell_quote(json.dumps(
                             [[OPTIONS[0][0], OPTIONS[0][1] + [["board_build.partitions", "other.csv"]]]]))):
            with self.subTest(settings=settings):
                self.assert_conflict(settings=settings)

    def test_missing_and_malformed_recipes_stop_even_when_package_hashes_are_valid(self):
        self.qualified_fixture()
        original = json.loads(self.capabilities.read_text())
        for recipe in (None, {}, {"schema_version": True, "sha256": "a" * 64},
                       {"schema_version": 1, "sha256": "broken"},
                       {"schema_version": 1, "sha256": "a" * 64}):
            with self.subTest(recipe=recipe):
                manifest = dict(original)
                if recipe is None:
                    manifest.pop("build_recipe")
                else:
                    manifest["build_recipe"] = recipe
                self.capabilities.write_text(json.dumps(manifest))
                self.reseal_capabilities()
                self.assert_conflict()

    def test_corrupt_and_partial_occupied_packages_stop_without_pio_or_writes(self):
        self.qualified_fixture()
        application = Path(str(self.stem) + ".bin")
        application.write_bytes(b"tampered application")
        self.assert_conflict()
        for path in list(self.output.iterdir()):
            if path != application:
                path.unlink()
        self.assert_conflict()
        application.write_bytes(b"")
        self.assert_conflict()
        application.unlink()
        application.symlink_to(self.work / "missing-image")
        self.assert_conflict()

    def test_memory_seal_prevents_recipe_marker_tampering(self):
        self.qualified_fixture()
        old_output = self.output
        self.output = self.work / "other-artifacts"; self.output.mkdir()
        result = self.build(succeed=True, settings="RADIO_FREQ_OVERRIDE=910.525")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        new_manifest = json.loads(next(self.output.glob("*.capabilities.json")).read_text())
        self.output = old_output
        manifest = json.loads(self.capabilities.read_text())
        # Marker now matches the changed settings, but cannot be substituted
        # into the old image's sealed capability manifest to bypass the gate.
        manifest["build_recipe"] = new_manifest["build_recipe"]
        self.capabilities.write_text(json.dumps(manifest))
        self.assert_conflict(settings="RADIO_FREQ_OVERRIDE=910.525")

    def test_ambient_unset_unexported_and_exported_state_is_restored_on_skip_and_conflict(self):
        for ambient in ("unset", "unexported", "exported"):
            with self.subTest(ambient=ambient):
                self.qualified_fixture(ambient=ambient, version="vtest-" + ambient)
                result = self.build(ambient=ambient, version="vtest-" + ambient)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Skipping", result.stdout)
                self.assert_conflict(ambient=ambient, version="vtest-" + ambient,
                                     settings="RADIO_FREQ_OVERRIDE=910.525")

    def test_empty_different_stem_may_build_without_overwriting_previous_version(self):
        self.qualified_fixture()
        before = self.snapshot()
        result = self.build(version="v1.17.1.10")
        self.assertEqual(result.returncode, 89, result.stdout + result.stderr)
        self.assertIn("STUB_BUILD_REQUIRED", result.stdout)
        self.assertEqual(before, self.snapshot())

    def test_input_change_during_stub_compile_prevents_artifact_publication(self):
        result = self.build(succeed=True, after_build="export PLATFORMIO_BUILD_UNFLAGS+=' -DCHANGED_DURING_BUILD'")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed during compilation", result.stderr)
        self.assertEqual(self.snapshot(), {})

    def test_ignored_local_config_change_during_stub_compile_prevents_publication(self):
        source = self.work / "source-fixture"; source.mkdir()
        # Read-only Git metadata from the real checkout plus a scratch working
        # tree avoids initializing/changing Git or touching the real private
        # local config. The helper reads only Git inventory/HEAD commands.
        (source / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
        result = self.build(succeed=True, source_root=source, after_build=r'''
python3 - "$recipe_source/platformio.local.ini" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text("[env:test]\nbuild_flags=-DLOCAL_RECIPE_REPRO=1\n")
PY
''')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed during compilation", result.stderr)
        self.assertEqual(self.snapshot(), {})

    def test_external_config_change_during_stub_compile_prevents_publication(self):
        source = self.work / "source-fixture"; source.mkdir()
        (source / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
        (source / "platformio.ini").write_text("[platformio]\nextra_configs=../external.ini\n")
        external = self.work / "external.ini"
        external.write_text("[env:test]\nbuild_flags=-DEXTERNAL_RECIPE_REPRO=1\n")
        result = self.build(succeed=True, source_root=source, after_build=r'''
python3 - "$recipe_source/../external.ini" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text("[env:test]\nbuild_flags=-DEXTERNAL_RECIPE_REPRO=2\n")
PY
''')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed during compilation", result.stderr)
        self.assertEqual(self.snapshot(), {})

    def test_occupied_inspection_error_fails_closed_before_stub_compile(self):
        result = self.build(settings=r'''
python3() {
  if [ "$1" = scripts/firmware_build_recipe.py ] && [ "$2" = occupied ]; then return 2; fi
  command python3 "$@"
}
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("STUB_BUILD_REQUIRED", result.stdout)
        self.assertIn("Cannot inspect", result.stderr)
        self.assertEqual(self.snapshot(), {})


class LogPreservationTests(unittest.TestCase):
    def test_serial_and_dormant_worker_consumers_preserve_new_and_legacy_skip_logs(self):
        for parallel in (False, True):
            for message in ("existing artifacts found", "matching recipe and qualified artifacts found"):
                with self.subTest(parallel=parallel, message=message), \
                        tempfile.TemporaryDirectory(prefix="mesh-recipe-logs-") as temporary:
                    work = Path(temporary)
                    output = work / "artifacts"
                    logs = output / "build-logs"; logs.mkdir(parents=True)
                    paths = [logs / (name + "-standard.log") for name in ("first", "second")]
                    for path in paths:
                        path.write_bytes(b"original compile log\n")
                    before = [(path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                              for path in paths]
                    script = r'''
source "$1/build_legacy.sh"
OUTPUT_DIR=$2
skip_message=$3
pio() { printf 'FORBIDDEN_PLATFORMIO\n' >&2; return 99; }
build_firmware() { printf 'Skipping %s; %s for qualified-fixture.\n' "$1" "$skip_message"; }
if [ "$4" = parallel ]; then
  # Exercise the retained consumer code using harmless shell-only workers.
  # The production policy stays at one worker; no PIO process is launched.
  consumer=$(declare -f run_logged_build_targets)
  consumer=${consumer/local worker_limit=1/local worker_limit=2}
  eval "$consumer"
fi
run_logged_build_targets first second
'''
                    result = subprocess.run(["bash", "-c", script, "log-test", str(ROOT), str(output),
                                             message, "parallel" if parallel else "serial"],
                                            cwd=work, text=True, capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("FORBIDDEN_PLATFORMIO", result.stdout + result.stderr)
                    self.assertEqual(before, [(path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                                              for path in paths])
                    self.assertEqual(list(logs.glob("*.tmp")), [])

    def test_failed_resume_attempts_get_unique_logs_but_other_build_results_keep_old_policy(self):
        for parallel in (False, True):
            for resume, status in ((True, 1), (False, 1), (True, 0), (False, 0)):
                with self.subTest(parallel=parallel, resume=resume, status=status), \
                        tempfile.TemporaryDirectory(prefix="mesh-recipe-log-failures-") as temporary:
                    work = Path(temporary)
                    output = work / "artifacts"
                    logs = output / "build-logs"; logs.mkdir(parents=True)
                    paths = [logs / (name + "-standard.log") for name in ("first", "second")]
                    for path in paths:
                        path.write_bytes(b"original compile log\n")
                    before = [(path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                              for path in paths]
                    script = r'''
source "$1/build_legacy.sh"
OUTPUT_DIR=$2
RESUME_BUILD_OUTPUT=$3
stub_status=$4
pio() { printf 'FORBIDDEN_PLATFORMIO\n' >&2; return 99; }
build_firmware() { printf 'current attempt diagnostics\n'; return "$stub_status"; }
if [ "$5" = parallel ]; then
  consumer=$(declare -f run_logged_build_targets)
  consumer=${consumer/local worker_limit=1/local worker_limit=2}
  eval "$consumer"
fi
run_logged_build_targets first second
first_result=$?
run_logged_build_targets first second
second_result=$?
for failure in "${LOGGING_MATRIX_FAILURES[@]}"; do printf 'RECORDED_FAILURE:%s\n' "$failure"; done
printf 'RESULTS:%s/%s\n' "$first_result" "$second_result"
'''
                    result = subprocess.run(["bash", "-c", script, "failed-log-test", str(ROOT), str(output),
                                             "1" if resume else "0", str(status),
                                             "parallel" if parallel else "serial"],
                                            cwd=work, text=True, capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("FORBIDDEN_PLATFORMIO", result.stdout + result.stderr)
                    self.assertIn(f"RESULTS:{status}/{status}", result.stdout)
                    attempts = list(logs.glob("*-resume-attempt.*.log"))
                    failed_paths = {line.split(" -> ", 1)[1] for line in result.stdout.splitlines()
                                    if line.startswith("RECORDED_FAILURE:")}
                    if resume and status:
                        self.assertEqual(before, [(path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                                                  for path in paths])
                        self.assertEqual(len(attempts), 4)
                        self.assertEqual(failed_paths, set(map(str, attempts)))
                        for attempt in attempts:
                            self.assertIn("current attempt diagnostics", attempt.read_text())
                            self.assertIn("FAILED:", attempt.read_text())
                            self.assertIn("status 1; log: " + str(attempt), result.stdout)
                    else:
                        self.assertEqual(attempts, [])
                        for path in paths:
                            self.assertIn("current attempt diagnostics", path.read_text())
                            self.assertNotIn("original compile log", path.read_text())
                        self.assertEqual(failed_paths, set(map(str, paths)) if status else set())
                    self.assertEqual(list(logs.glob("*.tmp")), [])

    def test_failed_attempt_log_allocation_preserves_prior_log_and_never_starts_build(self):
        with tempfile.TemporaryDirectory(prefix="mesh-recipe-log-allocation-") as temporary:
            work = Path(temporary)
            output = work / "artifacts"
            logs = output / "build-logs"; logs.mkdir(parents=True)
            original = logs / "first-standard.log"; original.write_bytes(b"original compile log\n")
            before = (original.read_bytes(), original.stat().st_ino, original.stat().st_mtime_ns)
            script = r'''
source "$1/build_legacy.sh"
OUTPUT_DIR=$2
RESUME_BUILD_OUTPUT=1
mktemp() { return 70; }
build_firmware() { printf 'SHOULD_NOT_BUILD\n'; }
run_logged_build_targets first second
status=$?
printf 'RESULT:%s BATCH:%s\n' "$status" "$BATCH_BUILD_MODE"
'''
            result = subprocess.run(["bash", "-c", script, "allocation-log-test", str(ROOT), str(output)],
                                    cwd=work, text=True, capture_output=True, timeout=10)
            self.assertNotIn("SHOULD_NOT_BUILD", result.stdout)
            self.assertIn("RESULT:1 BATCH:0", result.stdout)
            self.assertEqual(before, (original.read_bytes(), original.stat().st_ino, original.stat().st_mtime_ns))


class CompilerJobLimitTests(unittest.TestCase):
    def test_serial_resume_does_not_leak_indicator_partition_to_next_target(self):
        with tempfile.TemporaryDirectory(prefix="mesh-resume-scope-") as temporary:
            script = r'''
source "$1/build_legacy.sh"
OUTPUT_DIR=$2
RESUME_BUILD_OUTPUT=1
unset MESHCORE_ESP32_FULL_PARTITION_TABLE
indicator_profile() {
  unset MESHCORE_ESP32_FULL_PARTITION_TABLE
  export MESHCORE_ESP32_FULL_PARTITION_TABLE=indicator-partitions.csv
}
build_firmware() {
  if [ "$1" = indicator ]; then
    local MESHCORE_ESP32_FULL_PARTITION_TABLE="${MESHCORE_ESP32_FULL_PARTITION_TABLE-}"
    # Match the Indicator profile's nested unset/export followed by a skip.
    indicator_profile
    printf 'Skipping indicator; matching recipe and qualified artifacts found for indicator.\n'
    return 0
  fi
  if [ "${MESHCORE_ESP32_FULL_PARTITION_TABLE+x}" ]; then
    printf 'PARTITION_OVERRIDE_LEAKED\n'
    return 1
  fi
  printf 'Skipping g2; matching recipe and qualified artifacts found for g2.\n'
}
run_logged_build_targets indicator g2
status=$?
if [ "${MESHCORE_ESP32_FULL_PARTITION_TABLE+x}" ]; then exit 76; fi
printf 'RESULT:%s\n' "$status"
exit "$status"
'''
            result = subprocess.run(["bash", "-c", script, "resume-scope-test", str(ROOT), temporary],
                                    cwd=temporary, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("RESULT:0", result.stdout)
            self.assertNotIn("PARTITION_OVERRIDE_LEAKED", result.stdout + result.stderr)

    def test_serial_matrix_passes_validated_job_limit_and_restores_caller_state(self):
        for requested, expected in (("4", "4"), ("2", "2"), ("0", "8"), ("invalid", "8")):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory(prefix="mesh-compiler-jobs-") as temporary:
                script = r'''
source "$1/build_legacy.sh"
OUTPUT_DIR=$2
OPTION3_PIO_JOBS=$3
PIO_BUILD_JOBS_OVERRIDE=3
pio() { printf 'FORBIDDEN_PLATFORMIO\n' >&2; return 99; }
build_firmware() {
  printf '%s:%s:%s\n' "$1" "$PIO_BUILD_JOBS_OVERRIDE" "$BATCH_BUILD_MODE" >> "$OUTPUT_DIR/jobs-observed.txt"
}
run_logged_build_targets first second
status=$?
printf 'RESULT:%s RESTORED_JOBS:%s RESTORED_BATCH:%s\n' "$status" "$PIO_BUILD_JOBS_OVERRIDE" "$BATCH_BUILD_MODE"
'''
                result = subprocess.run(["bash", "-c", script, "jobs-test", str(ROOT), temporary, requested],
                                        cwd=temporary, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("FORBIDDEN_PLATFORMIO", result.stdout + result.stderr)
                self.assertIn("RESULT:0 RESTORED_JOBS:3 RESTORED_BATCH:0", result.stdout)
                self.assertEqual((Path(temporary) / "jobs-observed.txt").read_text().splitlines(),
                                 ["first:" + expected + ":1", "second:" + expected + ":1"])


if __name__ == "__main__":
    unittest.main()
