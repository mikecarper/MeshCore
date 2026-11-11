"""Preserve the complete profile suite while CI runs independent families.

These tests never source the actual build driver or invoke PlatformIO. A pinned
byte reconstruction proves that every pre-split assertion remains unchanged;
a fake build driver exercises the real runner's selection and failure paths.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "test/build_profile_sections.json"
RUNNER = ROOT / "test/test_build_profiles.sh"
BASELINE_SHA256 = "c681c918c75686fb66d2dac56f6ddfd2eb0a5755305ca33e475dfd9669bea6f0"
SUITES = ("esp32-release", "companion-transport", "nrf52-storage")


def manifest():
    return json.loads(MANIFEST.read_text())


def body(row):
    source = (ROOT / row["file"]).read_text()
    if row["kind"] == "raw":
        return source
    pattern = (r"^# build-profile-body: " + re.escape(row["id"]) + r"\n"
               r"([\s\S]*?)^# build-profile-end: " + re.escape(row["id"]) + r"\n")
    matches = re.findall(pattern, source, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError("Expected exactly one original section: " + row["id"])
    return matches[0]


def selected(suite):
    return [row for row in manifest()["sections"]
            if suite == "all" or row["suite"] in ("shared", suite)]


class BuildProfileSuites(unittest.TestCase):
    def test_every_original_byte_is_preserved_exactly_once_in_source_order(self):
        data = manifest()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["suites"], list(SUITES))
        self.assertEqual(data["original_sha256"], BASELINE_SHA256)
        rows = data["sections"]
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        cursor, parts = 1, []
        for row in rows:
            with self.subTest(section=row["id"]):
                self.assertEqual(row["start_line"], cursor)
                self.assertGreaterEqual(row["end_line"], cursor)
                chunk = body(row)
                self.assertEqual(len(chunk.splitlines()), row["end_line"] - cursor + 1)
                self.assertEqual(hashlib.sha256(chunk.encode()).hexdigest(), row["sha256"])
                self.assertIn(row["suite"], (*SUITES, "shared"))
                parts.append(chunk)
                cursor = row["end_line"] + 1
        self.assertEqual(cursor - 1, data["original_lines"])
        self.assertEqual(hashlib.sha256("".join(parts).encode()).hexdigest(), BASELINE_SHA256)

    def test_family_sources_only_define_the_registered_sections(self):
        data = manifest()
        for suite in SUITES:
            rows = [row for row in data["sections"] if row["suite"] == suite]
            self.assertTrue(rows)
            paths = {row["file"] for row in rows}
            self.assertEqual(len(paths), 1)
            source = (ROOT / paths.pop()).read_text()
            for row in rows:
                definition = ("build_profile_" + row["id"] + "() {\n"
                              "# build-profile-body: " + row["id"] + "\n" + body(row) +
                              "# build-profile-end: " + row["id"] + "\n}\n")
                self.assertEqual(source.count(definition), 1)
                source = source.replace(definition, "", 1)
            self.assertEqual(re.sub(r"(?m)^#.*$", "", source).strip(), "")

    def test_three_suites_partition_assertions_without_repeating_other_families(self):
        owned = [row["id"] for row in manifest()["sections"] if row["suite"] != "shared"]
        counts = Counter(row["id"] for suite in SUITES for row in selected(suite)
                         if row["suite"] != "shared")
        self.assertEqual(counts, Counter(owned))
        self.assertTrue(all(count == 1 for count in counts.values()))
        shared = [row for row in manifest()["sections"] if row["suite"] == "shared"]
        self.assertEqual([row["id"] for row in shared], ["bootstrap", "metadata", "finish"])
        self.assertEqual(body(shared[1]).count("init_project_context"), 1)
        self.assertNotRegex(body(shared[1]), r"\bfail\b")
        self.assertEqual(body(shared[2]), 'echo "test_build_profiles: OK"\n')

    def test_list_selection_is_read_only_and_matches_the_registered_order(self):
        for suite in ("all", *SUITES):
            with self.subTest(suite=suite):
                command = ["bash", str(RUNNER), "--list-sections"]
                if suite != "all":
                    command += ["--suite", suite]
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = "".join(row["suite"] + "\t" + row["id"] + "\n" for row in selected(suite))
                self.assertEqual(result.stdout, expected)

    def fake_project(self, directory):
        work = Path(directory)
        test = work / "test"
        test.mkdir()
        shutil.copyfile(RUNNER, test / RUNNER.name)
        for name in ("build_profile_common.sh", "build_profile_bootstrap.sh"):
            shutil.copyfile(ROOT / "test" / name, test / name)
        # The real runner sources build.sh only at global scope. This fixture
        # records its metadata call without any actual PlatformIO or network.
        (work / "build.sh").write_text('''printf 'bootstrap\\n' >> "$PROFILE_TEST_TRACE"
init_project_context() { printf 'metadata\\n' >> "$PROFILE_TEST_TRACE"; }
build_profile_finish() {
  printf 'finish\\n' >> "$PROFILE_TEST_TRACE"
  echo 'test_build_profiles: OK'
}
''')
        for suite in SUITES:
            rows = [row for row in manifest()["sections"] if row["suite"] == suite]
            definitions = []
            for row in rows:
                name = row["id"]
                definitions.append("build_profile_" + name + "() {\n"
                    "  printf '%s\\n' '" + name + "' >> \"$PROFILE_TEST_TRACE\"\n"
                    "  if [ \"${PROFILE_TEST_FAIL_SECTION:-}\" = '" + name + "' ]; then\n"
                    "    false\n"
                    "    printf 'UNREACHABLE\\n' >> \"$PROFILE_TEST_TRACE\"\n"
                    "  fi\n}\n")
            (work / rows[0]["file"]).write_text("\n".join(definitions))
        temporary = work / "tmp"
        temporary.mkdir()
        trace = work / "trace"
        env = {**os.environ, "PROFILE_TEST_TRACE": str(trace), "TMPDIR": str(temporary)}
        return work, trace, env

    def test_real_runner_defaults_to_all_and_each_family_runs_only_its_own_sections(self):
        for suite in ("all", *SUITES):
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as directory:
                work, trace, env = self.fake_project(directory)
                args = [] if suite == "all" else ["--suite", suite]
                result = subprocess.run(["bash", "test/test_build_profiles.sh", *args], cwd=work,
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(trace.read_text().splitlines(), [row["id"] for row in selected(suite)])
                self.assertTrue(result.stdout.endswith("test_build_profiles: OK\n"))
                self.assertEqual(list((work / "tmp").iterdir()), [])

    def test_selected_failures_stop_immediately_and_unselected_failures_are_not_run(self):
        for suite in SUITES:
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as directory:
                work, trace, env = self.fake_project(directory)
                rows = selected(suite)
                failure = next(row["id"] for row in rows if row["suite"] == suite)
                env["PROFILE_TEST_FAIL_SECTION"] = failure
                result = subprocess.run(["bash", "test/test_build_profiles.sh", "--suite", suite], cwd=work,
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                expected = [row["id"] for row in rows]
                self.assertEqual(trace.read_text().splitlines(), expected[:expected.index(failure) + 1])
                self.assertNotIn("test_build_profiles: OK", result.stdout)
                self.assertEqual(list((work / "tmp").iterdir()), [])
            with self.subTest(unselected_for=suite), tempfile.TemporaryDirectory() as directory:
                work, trace, env = self.fake_project(directory)
                env["PROFILE_TEST_FAIL_SECTION"] = next(row["id"] for row in manifest()["sections"]
                                                       if row["suite"] not in (suite, "shared"))
                result = subprocess.run(["bash", "test/test_build_profiles.sh", "--suite", suite], cwd=work,
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(env["PROFILE_TEST_FAIL_SECTION"], trace.read_text().splitlines())

    def test_invalid_selection_fails_before_loading_any_build_driver(self):
        for args in (["--suite"], ["--suite", "unknown"], ["--unexpected"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                work, trace, env = self.fake_project(directory)
                result = subprocess.run(["bash", "test/test_build_profiles.sh", *args], cwd=work,
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(trace.exists())


if __name__ == "__main__":
    unittest.main()
