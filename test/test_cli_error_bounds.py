#!/usr/bin/env python3
"""Run both production unknown-config fallbacks with real sanitizer bounds.

The final CommonCLI setter branch and nested observer MQTT-slot branch are
extracted verbatim. Hardware-dependent recognized setters are excluded. Negative
controls restore the audited legacy sprintf statement and must cause a genuine
AddressSanitizer overflow for the same maximum-size inputs.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/cli_error_bounds/main.cpp"


def fallback(method):
    marker = method.index('"unknown config"')
    assert method.count('"unknown config"') == 1
    start = method.rfind("} else {", 0, marker)
    assert start >= 0, "unknown-setting branch moved; review the fixture"
    branch = body(method[start + 2:], "else")
    assert '"unknown config"' in branch
    return branch


class CliErrorBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("CLI reply bounds require a C++17 sanitizer compiler")
        common = body((ROOT / "src/helpers/CommonCLI.cpp").read_text(),
                      "void CommonCLI::handleSetCmd(")
        observer = body((ROOT / "src/helpers/CommonCLI_Observer.cpp").read_text(),
                        "bool CommonCLI::handleObserverSetCmd(")
        config = re.search(r"const char\* config = &command\[4\];", common)
        assert config, "production setter command prefix moved"
        common_branch, observer_branch = fallback(common), fallback(observer)
        template = FIXTURE.read_text(encoding="ascii")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-cli-error-bounds-")
        cls.binaries = {}
        for legacy in (False, True):
            source = template.replace("@COMMON_CONFIG@", config.group(0))
            source = source.replace("@COMMON_FALLBACK@", common_branch)
            source = source.replace("@OBSERVER_FALLBACK@", observer_branch)
            if legacy:
                previous = 'strcpy(reply, "unknown config");'
                assert source.count(previous) == 2, "update the old formatter negative control"
                source = source.replace(previous, 'sprintf(reply, "unknown config: %s", config);')
            directory = Path(cls.work.name)
            path = directory / ("legacy.cpp" if legacy else "production.cpp")
            binary = directory / ("legacy" if legacy else "production")
            path.write_text(source, encoding="ascii")
            result = subprocess.run([
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                str(path), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            if result.returncode:
                cls.work.cleanup()
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[legacy] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_case(self, scenario, legacy=False):
        for parser in ("common", "observer"):
            with self.subTest(parser=parser, legacy=legacy):
                environment = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0")
                result = subprocess.run([str(self.binaries[legacy]), parser, scenario],
                                        capture_output=True, text=True, timeout=10, env=environment)
                if legacy:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("AddressSanitizer", result.stderr)
                    self.assertIn("buffer-overflow", result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_maximum_local_command_fits_exact_160_byte_reply_and_preserves_canaries(self):
        self.run_case("local")

    def test_maximum_radio_command_preserves_reply_path_after_exact_179_byte_window(self):
        self.run_case("radio")

    def test_error_fits_the_exact_literal_size_even_with_maximum_command(self):
        self.run_case("minimum")

    def test_input_lengths_through_four_kilobytes_never_change_reply_or_touch_path(self):
        self.run_case("lengths")

    def test_previous_formatter_fails_sanitizer_for_maximum_local_command(self):
        self.run_case("local", legacy=True)

    def test_previous_formatter_fails_sanitizer_for_maximum_radio_command(self):
        self.run_case("radio", legacy=True)


if __name__ == "__main__":
    unittest.main()
