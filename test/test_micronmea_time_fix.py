"""Exercise the reviewed complete GPS parser and its private build transform.

The compiler uses the real MicroNMEA fixture with Arduino/Stream adapters.
No PlatformIO command, dependency download, serial port or hardware is used.
"""
from pathlib import Path
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/micronmea_time_fix"
SPEC = importlib.util.spec_from_file_location("micronmea_time_fix", ROOT / "scripts/micronmea_time_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
SOURCE = (FIXTURES / "src/MicroNMEA.cpp").read_text(encoding="ascii")
HEADER = (FIXTURES / "src/MicroNMEA.h").read_text(encoding="ascii")


class Node:
    def __init__(self, path):
        self.path = Path(path)

    def srcnode(self):
        return self

    def get_abspath(self):
        return str(self.path)


class BuildEnv:
    def __init__(self, build):
        self.build = Path(build)
        self.registrations = []

    def subst(self, value):
        assert value == "$BUILD_DIR"
        return str(self.build)

    def File(self, path):
        return Path(path)

    def AddBuildMiddleware(self, callback, pattern):
        self.registrations.append((callback, pattern))


class TransformTests(unittest.TestCase):
    def test_original_fixtures_are_exact_ascii_reviewed_and_attributed(self):
        for path in FIXTURES.rglob("*"):
            if path.is_file():
                path.read_text(encoding="ascii")
        self.assertEqual(FIX.digest(SOURCE), FIX.SOURCE_SHA256)
        self.assertEqual(FIX.digest(HEADER), FIX.HEADER_SHA256)
        self.assertIn('#define MICRONMEA_VERSION "2.0.6"', HEADER)
        self.assertIn("Steve Marple", (FIXTURES / "README.txt").read_text())
        license_text = (FIXTURES / "LICENSE").read_text(encoding="ascii")
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 2.1, February 1999", license_text)

    def test_transform_is_hash_bound_and_idempotent_with_lf_or_crlf(self):
        patched = FIX.patched_source(SOURCE)
        self.assertEqual(FIX.digest(patched), FIX.PATCHED_SHA256)
        self.assertEqual(FIX.patched_source(SOURCE.replace("\n", "\r\n")), patched)
        self.assertEqual(FIX.patched_source(patched), patched)
        self.assertEqual(FIX.patched_source(patched.replace("\n", "\r\n")), patched)
        self.assertIn(FIX.PATCH_NOTICE, patched)
        self.assertEqual(patched.count(FIX.PATCHED_TIME), 1)
        self.assertEqual(patched.count(FIX.PATCHED_DATE), 1)

    def test_unknown_changed_and_half_patched_sources_fail_closed(self):
        for source in (SOURCE + "\n", SOURCE.replace("return r;", "return r + 1;", 1),
                       SOURCE.replace(FIX.ORIGINAL_TIME, FIX.PATCHED_TIME, 1),
                       SOURCE.replace(FIX.ORIGINAL_DATE, FIX.PATCHED_DATE, 1)):
            with self.subTest(source=FIX.digest(source)):
                with self.assertRaisesRegex(RuntimeError, "changed reviewed source"):
                    FIX.patched_source(source)

    def test_middleware_matches_only_the_expected_library_source(self):
        env = BuildEnv("unused")
        FIX.install(env)
        self.assertEqual(env.registrations, [(FIX.replace_micronmea_source, "*MicroNMEA.cpp")])
        with tempfile.TemporaryDirectory() as temporary:
            env = BuildEnv(Path(temporary) / "build")
            for relative in ("src/unrelated.cpp", "other/MicroNMEA.cpp"):
                node = Node(Path(temporary) / relative)
                self.assertIs(FIX.replace_micronmea_source(env, node), node)
            self.assertFalse(env.build.exists())

    def test_private_pair_leaves_downloaded_files_and_unchanged_outputs_untouched(self):
        for crlf in (False, True):
            with self.subTest(crlf=crlf), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "libdeps/MicroNMEA/src/MicroNMEA.cpp"
                source.parent.mkdir(parents=True)
                original = SOURCE.replace("\n", "\r\n") if crlf else SOURCE
                header = HEADER.replace("\n", "\r\n") if crlf else HEADER
                source.write_bytes(original.encode("ascii"))
                source.with_suffix(".h").write_bytes(header.encode("ascii"))
                before = source.read_bytes(), source.with_suffix(".h").read_bytes()
                env = BuildEnv(root / "build")
                destination = FIX.replace_micronmea_source(env, Node(source))
                self.assertEqual(destination, env.build / "patched-micronmea/MicroNMEA.cpp")
                self.assertEqual(destination.read_text(encoding="ascii"), FIX.patched_source(SOURCE))
                self.assertEqual(destination.with_suffix(".h").read_text(encoding="ascii"), HEADER)
                times = destination.stat().st_mtime_ns, destination.with_suffix(".h").stat().st_mtime_ns
                self.assertEqual(FIX.replace_micronmea_source(env, Node(source)), destination)
                self.assertEqual(times, (destination.stat().st_mtime_ns,
                                        destination.with_suffix(".h").stat().st_mtime_ns))
                self.assertEqual(before, (source.read_bytes(), source.with_suffix(".h").read_bytes()))

    def test_missing_or_changed_header_fails_before_creating_private_output(self):
        for header in (None, HEADER + "\n", "// unknown\n", "\u00b0"):
            with self.subTest(missing=header is None), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "libdeps/MicroNMEA/src/MicroNMEA.cpp"
                source.parent.mkdir(parents=True)
                source.write_text(SOURCE, encoding="ascii")
                if header is not None:
                    source.with_suffix(".h").write_text(header, encoding="utf-8")
                env = BuildEnv(root / "build")
                with self.assertRaisesRegex(RuntimeError, "reviewed header"):
                    FIX.replace_micronmea_source(env, Node(source))
                self.assertFalse(env.build.exists())


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")
        temporary = tempfile.TemporaryDirectory(prefix="micronmea-time-")
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        cls.binaries = {}
        sanitize = [] if not sys.platform.startswith("linux") else [
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        for kind in ("original", "patched"):
            source = root / kind / "src/MicroNMEA.cpp"
            source.parent.mkdir(parents=True)
            source.write_text(SOURCE, encoding="ascii")
            source.with_suffix(".h").write_text(HEADER, encoding="ascii")
            if kind == "patched":
                source = FIX.replace_micronmea_source(BuildEnv(root / "private"), Node(source))
            binary = root / (kind + ".exe")
            built = subprocess.run([
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", *sanitize, "-I" + str(source.parent),
                "-isystem", str(ROOT / "test/mocks"), str(FIXTURES / "runtime.cpp"),
                str(source), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries[kind] = binary
        # Force a wrong header ahead of the reviewed private pair. Only the
        # library implementation is compiled here; its quoted include must win.
        poison = root / "poison"
        poison.mkdir()
        (poison / "MicroNMEA.h").write_text('#error "unpaired header"\n', encoding="ascii")
        checked = subprocess.run([
            compiler, "-std=c++17", "-I" + str(poison), "-I" + str(ROOT / "test/mocks"),
            "-c", str(root / "private/patched-micronmea/MicroNMEA.cpp"),
            "-o", str(root / "private.o")], capture_output=True, text=True, timeout=30)
        if checked.returncode:
            raise AssertionError(checked.stdout + checked.stderr)

    def run_case(self, scenario, kind="patched", diagnostic=None):
        result = subprocess.run([str(self.binaries[kind]), scenario],
                                capture_output=True, text=True, timeout=10)
        if diagnostic:
            self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
            self.assertIn("SEMANTIC: " + diagnostic, result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("AddressSanitizer:", result.stderr)
        self.assertNotIn("runtime error:", result.stderr)

    def test_integer_and_variable_fraction_times_preserve_real_position_and_raw_sentences(self):
        self.run_case("formats")

    def test_malformed_clock_fields_clear_utc_but_preserve_valid_position_and_recover(self):
        self.run_case("invalid")

    def test_terminal_date_without_trailing_optional_fields_is_still_valid(self):
        self.run_case("terminal-date")

    def test_bad_checksum_unknown_sentence_crlf_and_send_callbacks_are_unchanged(self):
        self.run_case("callbacks")
        self.run_case("callbacks", "original")

    def test_small_buffer_and_end_of_buffer_short_date_stay_in_bounds(self):
        self.run_case("bounds")
        self.run_case("near-buffer-date")

    def test_original_parser_fails_semantic_compatibility_and_stale_utc_oracles(self):
        self.run_case("formats", "original", "RMC short time format lost validity")
        self.run_case("invalid", "original", "incomplete date retained old UTC date")


if __name__ == "__main__":
    unittest.main()
