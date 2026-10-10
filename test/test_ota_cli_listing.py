"""Exercise the actual outlined OTA dispatch/listing with the real name decoder."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/ota/OtaCli.cpp"
FIXTURE = ROOT / "test/fixtures/ota_cli_listing/test.cpp"


def function(text, name):
    # Definitions have an opening brace, unlike the forward control declaration.
    match = re.search(r"^(?:static )?(?:__attribute__\(\(noinline\)\) )?[^;{}\n]+\b" + name + r"\([^;{}]*\)\s*\{", text, re.M)
    if not match:
        raise AssertionError(f"Missing production function: {name}")
    start = match.start()
    depth = 0
    # Strip strings/comments for brace counting; retain original source for compile.
    tokens = re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', text[match.end() - 1:], re.S)
    for token in tokens:
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return text[start:match.end() - 1 + token.end()]
    raise AssertionError(f"Unterminated production function: {name}")


class OtaCliListingTest(unittest.TestCase):
    def test_real_dispatch_listing_and_all_target_names(self):
        source = SOURCE.read_text()
        names = ("parse_page", "codec_kind", "state_word", "fetch_error_word", "ver_str", "is_cmd", "handle_status", "handle_neighbors", "handle_ota_command")
        bodies = "\n\n".join(function(source, name) for name in names)
        bodies = "static __attribute__((noinline)) bool handle_control_command(const char*, char*, mesh::MainBoard&, OtaContext&);\n" + bodies
        harness = FIXTURE.read_text().replace("// @PRODUCTION_FUNCTIONS@", bodies)
        rows = re.findall(r'\{ 0x([0-9a-fA-F]{8}), "([^"]+)" \},', (ROOT / "src/helpers/ota/OtaTargets.h").read_text())
        self.assertGreater(len(rows), 600)
        input_text = "\n".join(f"{target} {name}" for target, name in rows) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            unit = directory / "listing.cpp"
            unit.write_text(harness)
            flags = ["-g", "-O2", "-fno-omit-frame-pointer", "-fno-pie", "-fsanitize=address,undefined"]
            decoder = directory / "tinf.o"
            subprocess.run([os.environ.get("CC") or shutil.which("cc") or "gcc", *flags,
                            "-DMESHCORE_TINF_IMPLEMENTATION=1", "-c", str(ROOT / "src/helpers/ota/tinf/tinflate.c"), "-o", str(decoder)], check=True)
            for compact in (0, 1):
                for seeder in (False, True):
                    with self.subTest(compact=compact, seeder=seeder):
                        executable = directory / f"listing-{compact}-{seeder}"
                        defines = [f"-DOTA_TARGET_NAME_FRONT_CODED={compact}", "-DNRF52_PLATFORM=1"]
                        if seeder:
                            defines.append("-DOTA_SEEDER_ONLY=1")
                        subprocess.run([os.environ.get("CXX") or shutil.which("c++") or "g++", "-std=c++11", *flags, "-no-pie",
                                        "-Wall", "-Wextra", "-Wno-unused-function", *defines, f"-I{ROOT / 'src'}",
                                        str(unit), str(decoder), "-o", str(executable)], check=True)
                        subprocess.run([str(executable)], input=input_text, text=True, check=True,
                                       env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1"})

    def test_name_decoder_has_separate_stack_boundary(self):
        source = SOURCE.read_text()
        dispatcher = function(source, "handle_ota_command")
        control = function(source, "handle_control_command")
        self.assertNotIn("ota_target_env_name", dispatcher)
        self.assertNotIn("ota_target_env_name", control)
        for name in ("handle_status", "handle_neighbors", "handle_control_command"):
            self.assertIn("__attribute__((noinline))", function(source, name).split("{", 1)[0])
            self.assertIn(name, dispatcher)


if __name__ == "__main__":
    unittest.main()
