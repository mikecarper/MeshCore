#!/usr/bin/env python3
"""Exercise production scratch-copy branches with exact bounds and side effects."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/common_cli_argument_bounds/main.cpp"


class CommonCLIArgumentBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("CLI argument bounds require a C++17 sanitizer compiler")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        header = (ROOT / "src/helpers/CommonCLI.h").read_text()
        core = (ROOT / "src/MeshCore.h").read_text()
        utils = (ROOT / "src/Utils.cpp").read_text()
        constants = header[header.index("#ifndef FLOOD_RETRY_PREFIX_SLOTS"):
                           header.index("#ifndef FLOOD_CHANNEL_KEY_PREFIX_LEN")]
        for name in ("PRV_KEY_SIZE", "MAX_HASH_SIZE"):
            constants += re.search(r"^#define " + name + r"\s+.+$", core, re.MULTILINE).group() + "\n"
        constants += re.search(r"^#define RECENT_REPEATER_PREFIX_MAX_BYTES\s+.+$",
                               common, re.MULTILINE).group()
        parsers = "\n".join(body(common, signature) for signature in (
            "static bool copyCLIArgument(", "static bool looksNumeric(",
            "static bool looksUnsignedInteger(", "static const char* skipSpacesConst(",
            "static bool parseUint32Strict(", "static int16_t parseSnrDbX4(",
            "static void formatSnrDbX4(", "static bool parseHashPrefix(",
            "static void formatSnrDbX4Short("))
        utils_code = re.search(r"static const char hex_chars\[\][^;]+;", utils).group()
        utils_code += "\n" + "\n".join(body(utils, signature) for signature in (
            "static uint8_t hexVal(", "void Utils::toHex(", "bool Utils::isHexChar(",
            "bool Utils::fromHex(", "int Utils::parseTextParts("))
        source = FIXTURE.read_text(encoding="ascii")
        replacements = {
            "CONSTANTS": constants, "PARSERS": parsers, "UTILS": utils_code,
            "SENSOR_BRANCH": body(common, 'if (memcmp(command, "sensor set ", 11) == 0)'),
            "RETRY_BRANCH": body(common, 'if (memcmp(config, "direct.retry.cr ", 16) == 0)'),
            "RECENT_BRANCH": body(common, 'if (memcmp(config, "recent.repeater ", 16) == 0)'),
        }
        for key, value in replacements.items():
            source = source.replace("@" + key + "@", value)
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-cli-argument-bounds-")
        cls.binaries = {}
        for name, defines in (("default", []), ("larger", ["-DCOMMON_CLI_TMP_LEN=192"]),
                              ("compact", ["-DMESH_CLI_COMPACT_REPLIES=1"]),
                              ("legacy", [])):
            generated = source
            if name == "legacy":
                # Restore all three audited unchecked copies as a negative control.
                error = (r'(?:"Error, argument too long"|MESH_CLI_REPLY_TEXT\('
                         r'"Error, argument too long", "Error, arg too long"\))')
                pattern = (r'if \(!copyCLIArgument\(tmp, sizeof\(tmp\), (&(?:command|config)\[\d+\])\)\) '
                           r'\{\s*strcpy\(reply, ' + error + r'\);\s*return;\s*\}')
                generated, count = re.subn(pattern, r"strcpy(tmp, \1);", generated)
                assert count == 3, "review the negative controls after branch changes"
            directory = Path(cls.work.name)
            path, binary = directory / (name + ".cpp"), directory / name
            path.write_text(generated, encoding="ascii")
            result = subprocess.run([
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-omit-frame-pointer", "-fno-pie", "-no-pie", *defines,
                "-I" + str(ROOT / "src"), str(path), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if result.returncode:
                cls.work.cleanup()
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_case(self, variant, scenario):
        return subprocess.run([str(self.binaries[variant]), scenario], capture_output=True,
                              text=True, timeout=15,
                              env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0"))

    def test_production_boundaries_and_rejection_without_effects(self):
        for variant in ("default", "larger", "compact"):
            with self.subTest(variant=variant):
                result = self.run_case(variant, "production")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS: production CLI argument bounds", result.stdout)

    def test_all_three_previous_copies_fail_address_sanitizer(self):
        for command in ("sensor", "retry", "recent"):
            with self.subTest(command=command):
                result = self.run_case("legacy", command)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("AddressSanitizer", result.stderr)
                self.assertIn("buffer-overflow", result.stderr)


if __name__ == "__main__":
    unittest.main()
