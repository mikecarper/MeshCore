"""Exercise reviewed real RTTTL dependencies with actual 32-bit clock widths.

The freestanding ELF32 binaries do not require 32-bit libc/libstdc++ packages.
Only the clock, pins, IRQ controller and PWM registers are mocked. The queue
test compiles the real buzzer, decoder, clip catalog and voice-player sources.
"""
from pathlib import Path
import importlib.util
import json
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures/rtttl_rollover"
SPEC = importlib.util.spec_from_file_location("rtttl_rollover_fix", ROOT / "scripts/rtttl_rollover_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
CXX = shutil.which("g++")
VERSIONS = ("1.3.0", "1.4.0")
PINS = {
    "1.3.0": (
        "b50c159c8806d177398972bf8f94ca762532a855b60cb1d3aa9c3ebf021423ca",
        "abea0a4870e58222fdefd1e88d291136518912e83880af30d1c85d39d7bd5cc1",
        "29204341d99b8adeef501c0d4cb47a4137534ebe6a30f2e6b3032d558aeef802",
    ),
    "1.4.0": (
        "d4fddfa663c8adeacea3b1daf56ec5fbe63ecaaf68e5477ac96dc4fb2e28d363",
        "5a33eec77e2ba53ed9b83a8242633b8ae11873d3b4f8cfd257abbb2eb61b6220",
        "e7710c4e3309b25517f5bace697c0c2574a5ce219f3cb156d352f739c178d75f",
    ),
}


def source_for(version):
    path = FIXTURES / version / "src/NonBlockingRtttl.cpp.json"
    return json.loads(path.read_text(encoding="ascii"))["source"]


def header_for(version):
    return (FIXTURES / version / "src/NonBlockingRtttl.h").read_text(encoding="ascii")


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

    def File(self, value):
        return Path(value)

    def AddBuildMiddleware(self, callback, pattern):
        self.registrations.append((callback, pattern))


class TransformTests(unittest.TestCase):
    def test_fixtures_are_ascii_licensed_and_exact_reviewed_bytes_after_json_decode(self):
        for path in FIXTURES.rglob("*"):
            if path.is_file():
                path.read_text(encoding="ascii")
        for version in VERSIONS:
            with self.subTest(version=version):
                source, header = source_for(version), header_for(version)
                expected_source, expected_header, _ = PINS[version]
                self.assertEqual(FIX.digest(source), expected_source)
                self.assertEqual(FIX.digest(header), expected_header)
                self.assertIn("#define NONBLOCKINGRTTTL_VERSION " + version, header)
                license_text = (FIXTURES / version / "LICENSE").read_text(encoding="ascii")
                self.assertIn("MIT License", license_text)
                self.assertIn("Copyright (c) 2018 Antoine Beauchamp", license_text)
                self.assertIn("Permission is hereby granted, free of charge", license_text)
                self.assertIn("THE SOFTWARE IS PROVIDED", license_text)
                self.assertIn("Antoine Beauchamp", (FIXTURES / version / "AUTHORS").read_text())

    def test_both_reviewed_versions_raw_crlf_idempotence_and_expected_patch_digest(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                original = source_for(version)
                patched = FIX.patched_source(original)
                self.assertEqual(FIX.digest(patched), PINS[version][2])
                patched.encode("ascii")
                self.assertEqual(FIX.patched_source(original.replace("\n", "\r\n")), patched)
                self.assertEqual(FIX.patched_source(patched), patched)
                self.assertEqual(FIX.patched_source(patched.replace("\n", "\r\n")), patched)
                self.assertEqual(patched.count(FIX.PATCHED_BEGIN), 1)
                self.assertEqual(patched.count(FIX.PATCHED_COMPARE), 1)

    def test_unknown_changed_or_half_patched_sources_fail_closed(self):
        for version in VERSIONS:
            original = source_for(version)
            for changed in (
                original + "\n", original.replace("bpm=63", "bpm=64", 1),
                original.replace(FIX.ORIGINAL_BEGIN, FIX.PATCHED_BEGIN, 1),
                original.replace(FIX.ORIGINAL_COMPARE, FIX.PATCHED_COMPARE, 1),
            ):
                with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "changed reviewed source"):
                    FIX.patched_source(changed)

    def test_middleware_registers_only_expected_source_filter(self):
        env = BuildEnv("unused")
        FIX.install(env)
        self.assertEqual(env.registrations, [(FIX.replace_rtttl_source, "*NonBlockingRtttl.cpp")])

    def test_unrelated_source_is_unchanged_and_not_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = BuildEnv(Path(temporary) / "build")
            for path in ("src/not_rtttl.cpp", "other/NonBlockingRtttl.cpp"):
                node = Node(Path(temporary) / path)
                self.assertIs(FIX.replace_rtttl_source(env, node), node)
            self.assertFalse(env.build.exists())

    def test_private_source_and_header_are_paired_idempotent_and_leave_shared_files_unchanged(self):
        for version in VERSIONS:
            for crlf in (False, True):
                with self.subTest(version=version, crlf=crlf), tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary)
                    source = directory / "libdeps/NonBlockingRTTTL/src/NonBlockingRtttl.cpp"
                    source.parent.mkdir(parents=True)
                    original, header = source_for(version), header_for(version)
                    if crlf:
                        original, header = original.replace("\n", "\r\n"), header.replace("\n", "\r\n")
                    source.write_bytes(original.encode("utf-8"))
                    source.with_suffix(".h").write_bytes(header.encode("ascii"))
                    before = (source.read_bytes(), source.with_suffix(".h").read_bytes())
                    env = BuildEnv(directory / "build")
                    destination = FIX.replace_rtttl_source(env, Node(source))
                    self.assertEqual(destination, env.build / "patched-rtttl/NonBlockingRtttl.cpp")
                    self.assertEqual(FIX.digest(destination.read_text(encoding="ascii")), PINS[version][2])
                    self.assertEqual(destination.with_suffix(".h").read_text(encoding="ascii"), header_for(version))
                    times = (destination.stat().st_mtime_ns, destination.with_suffix(".h").stat().st_mtime_ns)
                    self.assertEqual(FIX.replace_rtttl_source(env, Node(source)), destination)
                    self.assertEqual(times, (destination.stat().st_mtime_ns, destination.with_suffix(".h").stat().st_mtime_ns))
                    self.assertEqual(before, (source.read_bytes(), source.with_suffix(".h").read_bytes()))

    def test_missing_unknown_or_wrong_version_headers_fail_before_creating_private_output(self):
        for version in VERSIONS:
            other = "1.4.0" if version == "1.3.0" else "1.3.0"
            for header in (None, header_for(version) + "\n", header_for(other), "// unknown\n", "\u00b0"):
                with self.subTest(version=version, header=header is None), tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary)
                    source = directory / "libdeps/NonBlockingRTTTL/src/NonBlockingRtttl.cpp"
                    source.parent.mkdir(parents=True)
                    source.write_text(source_for(version), encoding="utf-8")
                    if header is not None:
                        source.with_suffix(".h").write_text(header, encoding="utf-8")
                    env = BuildEnv(directory / "build")
                    with self.assertRaisesRegex(RuntimeError, "reviewed header"):
                        FIX.replace_rtttl_source(env, Node(source))
                    self.assertFalse(env.build.exists())


@unittest.skipUnless(CXX and platform.system() == "Linux" and platform.machine() in ("x86_64", "i386", "i686"),
                     "Freestanding ELF32 tests require Linux x86 and g++")
class RuntimeTests(unittest.TestCase):
    def build_run(self, dependency, version, fixture):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "NonBlockingRtttl.cpp"
            source.write_text(dependency, encoding="utf-8")
            source.with_suffix(".h").write_text(header_for(version), encoding="ascii")
            binary = directory / "case"
            flags = ["-DRTTTL_NO_LOOPS=1"] if version == "1.3.0" else []
            subprocess.run([
                CXX, "-m32", "-std=c++17", "-Wall", "-Wextra", "-Werror", *flags,
                "-fno-exceptions", "-fno-rtti", "-fno-stack-protector", "-nostdlib", "-no-pie",
                "-I" + str(FIXTURES), "-I" + str(directory), "-I" + str(ROOT / "src"),
                str(FIXTURES / fixture), str(source), "-o", str(binary),
            ], check=True, capture_output=True, timeout=30)
            self.assertEqual(binary.read_bytes()[:5], b"\x7fELF\x01")
            return subprocess.run([str(binary)], capture_output=True, timeout=10).returncode

    def test_actual_unpatched_versions_reproduce_both_semantic_rollover_failures(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                self.assertEqual(self.build_run(source_for(version), version, "regression.cpp"), 30)
                self.assertEqual(self.build_run(source_for(version), version, "queue.cpp"), 33)

    def test_patched_versions_keep_tempo_empty_stop_replacement_and_real_voice_queue(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                patched = FIX.patched_source(source_for(version))
                self.assertEqual(self.build_run(patched, version, "regression.cpp"), 0)
                self.assertEqual(self.build_run(patched, version, "queue.cpp"), 0)

    def test_each_timing_fix_is_required_not_merely_accepted_by_source_checks(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                original = source_for(version)
                initial_only = original.replace(FIX.ORIGINAL_BEGIN, FIX.PATCHED_BEGIN, 1)
                signed_only = original.replace(FIX.ORIGINAL_COMPARE, FIX.PATCHED_COMPARE, 1)
                self.assertEqual(self.build_run(initial_only, version, "regression.cpp"), 30)
                self.assertEqual(self.build_run(signed_only, version, "regression.cpp"), 28)

    def test_private_cpp_quotes_its_paired_header_even_with_poison_include_path_first(self):
        for version in VERSIONS:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                original = directory / "libdeps/NonBlockingRTTTL/src/NonBlockingRtttl.cpp"
                original.parent.mkdir(parents=True)
                original.write_text(source_for(version), encoding="utf-8")
                original.with_suffix(".h").write_text(header_for(version), encoding="ascii")
                destination = FIX.replace_rtttl_source(BuildEnv(directory / "build"), Node(original))
                poison = directory / "poison"
                poison.mkdir()
                (poison / "NonBlockingRtttl.h").write_text('#error "wrong header selected"\n', encoding="ascii")
                subprocess.run([
                    CXX, "-m32", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fno-exceptions", "-fno-rtti", "-fno-stack-protector",
                    "-I" + str(poison), "-I" + str(FIXTURES),
                    "-c", str(destination), "-o", str(directory / "dependency.o"),
                ], check=True, capture_output=True, timeout=30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
