#!/usr/bin/env python3

import contextlib
import fnmatch
import io
import os
from pathlib import Path
import runpy
import shlex
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deduplicate_full_build_flags.py"
BUILD_SCRIPT = ROOT / "build.sh"


class FakeEnvironment:
    def __init__(self, flags):
        self.values = {"BUILD_FLAGS": flags}
        self.replacements = []
        self.middlewares = []

    def get(self, name, default=None):
        return self.values.get(name, default)

    def Replace(self, **values):
        self.values.update(values)
        self.replacements.append(values)

    def AddBuildMiddleware(self, callback, pattern=None):
        self.middlewares.append((callback, pattern))


def flatten(values):
    return [token for value in values for token in shlex.split(str(value))]


def apply_policy(flags, overlay, *, full=True, companion=True):
    environment = FakeEnvironment(flags)
    output = io.StringIO()
    variables = {
        "MESHCORE_ESP32_FULL_BUILD": "1" if full else "0",
        "MESHCORE_COMPANION_RADIO_FULL": "1" if companion else "0",
        "PLATFORMIO_BUILD_FLAGS": overlay,
    }
    with mock.patch.dict(os.environ, variables, clear=False):
        with contextlib.redirect_stdout(output):
            namespace = runpy.run_path(
                str(SCRIPT),
                init_globals={
                    "Import": lambda _name: None,
                    "env": environment,
                },
            )
    return environment, output.getvalue(), namespace


class FullBuildFlagDedupTest(unittest.TestCase):
    def test_repeated_extends_overlay_keeps_last_exact_occurrence(self):
        overlay = (
            "-DFEATURE=1 -UFEATURE -DFEATURE=1 "
            "-D LABEL='\"Full build\"' -fno-exceptions"
        )
        flags = [
            "-DBOARD=1 -Wextra " + overlay,
            "-DWITH_MQTT=1 -Wextra " + overlay,
            "-DCHILD_PROFILE=1 " + overlay,
        ]

        environment, output, namespace = apply_policy(flags, overlay)
        result = flatten(environment.values["BUILD_FLAGS"])
        logical = namespace["logical_flags"](
            environment.values["BUILD_FLAGS"]
        )

        self.assertEqual(result.count("-DFEATURE=1"), 2)
        self.assertEqual(result.count("-UFEATURE"), 1)
        macro_sequence = [
            token
            for token in result
            if token in {"-DFEATURE=1", "-UFEATURE"}
        ]
        self.assertEqual(
            macro_sequence,
            ["-DFEATURE=1", "-UFEATURE", "-DFEATURE=1"],
        )
        self.assertEqual(logical.count(("-D", 'LABEL="Full build"')), 1)
        self.assertEqual(result.count("-Wextra"), 2)
        self.assertIn("-DBOARD=1", result)
        self.assertIn("-DWITH_MQTT=1", result)
        self.assertIn("-DCHILD_PROFILE=1", result)
        # -fno-exceptions is not de-duplicated: unlike -D/-U, arbitrary
        # compiler/linker options can be order- or repetition-sensitive.
        self.assertEqual(result.count("-fno-exceptions"), 3)
        self.assertIn("removed 8 repeated external build flags", output)

    def test_two_token_options_remain_paired_and_quoted(self):
        overlay = "-D FEATURE=1 -I 'path with spaces'"
        flags = [overlay, "-DLOCAL=1", overlay]

        environment, _output, namespace = apply_policy(flags, overlay)
        logical = namespace["logical_flags"](
            environment.values["BUILD_FLAGS"]
        )

        self.assertEqual(logical.count(("-D", "FEATURE=1")), 1)
        self.assertEqual(logical.count(("-I", "path with spaces")), 2)
        self.assertIn(("-DLOCAL=1",), logical)

    def test_shell_tokens_round_trip_without_changing_quoting_semantics(self):
        raw = (
            "-D NAME='\"quoted value\"' -U OLD -I 'include path' "
            "-L lib -l mesh -F frameworks -arch xtensa "
            "-include 'config file.h' -imacros macros.h "
            "-isysroot sdk -isystem system -iquote quoted "
            "-idirafter after --param max-inline-insns-single=20 "
            "-Wl,--wrap=target $PROJECT_DIR/source.cpp"
        )
        _environment, _output, namespace = apply_policy(raw, "")
        logical = namespace["logical_flags"](raw)
        serialized = namespace["serialize_flags"](logical)

        self.assertEqual(flatten(serialized), shlex.split(raw))

    def test_only_column_zero_bang_is_preserved_as_a_command(self):
        command = "!python make_flags.py --profile full"
        quoted_literal = "'!not-a-command'"
        indented_literal = "  !also-not-a-command"
        flags = [command, quoted_literal, indented_literal, "-DOVERLAY=1"]

        environment, _output, _namespace = apply_policy(
            flags, "-DOVERLAY=1"
        )
        result = environment.values["BUILD_FLAGS"]

        self.assertEqual(result[0], command)
        self.assertFalse(result[1].startswith("!"))
        self.assertFalse(result[2].startswith("!"))
        self.assertEqual(shlex.split(result[1]), ["!not-a-command"])
        self.assertEqual(shlex.split(result[2]), ["!also-not-a-command"])

    def test_non_overlay_duplicates_and_non_full_build_are_untouched(self):
        flags = ["-Wextra -DOVERLAY=1", "-Wextra -DOVERLAY=1"]
        environment, _output, _namespace = apply_policy(
            flags, "-DOVERLAY=1"
        )
        result = flatten(environment.values["BUILD_FLAGS"])
        self.assertEqual(result.count("-Wextra"), 2)
        self.assertEqual(result.count("-DOVERLAY=1"), 1)

        environment, output, _namespace = apply_policy(
            flags, "-DOVERLAY=1", full=False
        )
        self.assertEqual(environment.values["BUILD_FLAGS"], flags)
        self.assertEqual(environment.replacements, [])
        self.assertEqual(output, "")

    def test_malformed_shell_quoting_fails_safe(self):
        flags = ["-DBOARD=1", "-DNAME='unterminated"]
        environment, output, _namespace = apply_policy(
            flags, "-DOVERLAY=1"
        )

        self.assertEqual(environment.values["BUILD_FLAGS"], flags)
        self.assertEqual(
            [item for item in environment.replacements if "BUILD_FLAGS" in item],
            [],
        )
        self.assertNotIn("removed", output)

    def test_interposed_project_macros_keep_final_overlay_state(self):
        overlay = "-DMODE=full -UOLD"
        flags = [
            overlay,
            "-DMODE=project -DOLD=1",
            overlay,
        ]

        environment, _output, _namespace = apply_policy(flags, overlay)
        result = flatten(environment.values["BUILD_FLAGS"])

        self.assertEqual(
            [token for token in result if token.startswith("-DMODE=")],
            ["-DMODE=project", "-DMODE=full"],
        )
        self.assertLess(result.index("-DOLD=1"), result.index("-UOLD"))

    def test_commands_and_response_files_keep_duplicates(self):
        command = "!python make_flags.py --profile full"
        flags = [command, "@common.rsp -DOVERLAY=1"] * 2

        environment, _output, _namespace = apply_policy(
            flags, "@common.rsp -DOVERLAY=1"
        )
        result = environment.values["BUILD_FLAGS"]

        self.assertEqual(result.count(command), 2)
        self.assertEqual(flatten(result).count("@common.rsp"), 2)
        self.assertEqual(flatten(result).count("-DOVERLAY=1"), 1)

    def test_include_order_and_repeated_inputs_are_not_deduplicated(self):
        overlay = "-I external -include forced.h -l mesh -DFEATURE=1"
        flags = [
            overlay,
            "-I project -include forced.h -l mesh",
            overlay,
        ]

        environment, _output, _namespace = apply_policy(flags, overlay)
        result = flatten(environment.values["BUILD_FLAGS"])

        self.assertEqual(
            [token for token in result if token in {"external", "project"}],
            ["external", "project", "external"],
        )
        self.assertEqual(result.count("forced.h"), 3)
        self.assertEqual(result.count("mesh"), 3)
        self.assertEqual(result.count("-DFEATURE=1"), 1)

    def test_cpppath_shortening_preserves_order_and_missing_entries(self):
        _environment, _output, namespace = apply_policy("", "")
        project = str(ROOT)
        framework = str(
            ROOT / "very-long-framework-directory" / "include"
        )
        missing = str(ROOT / "does-not-exist" / "include")
        aliases = {
            os.path.abspath(framework): "C:/PIO/FRAMEW~1/include",
            os.path.abspath(missing): None,
            os.path.abspath(ROOT / "include"): "C:/MC/include",
        }

        shortened, saved = namespace["shorten_cpppaths"](
            ["include", framework, missing],
            project,
            aliases.get,
        )

        # The relative project include is already shorter than its alias.
        self.assertEqual(shortened[0], "include")
        self.assertEqual(shortened[1], "C:/PIO/FRAMEW~1/include")
        self.assertEqual(shortened[2], missing)
        self.assertEqual(
            saved, len(framework) - len("C:/PIO/FRAMEW~1/include")
        )

    def test_full_build_driver_installs_the_pre_script(self):
        build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
        full_block_start = build_script.index(
            'if [ "$ESP32_FULL_BUILD" = "1" ] || '
            'is_esp32_companion_radio_full_target "$env_name"; then'
        )
        full_block_end = build_script.index("\n  else", full_block_start)
        full_block = build_script[full_block_start:full_block_end]
        self.assertIn(
            '"pre:scripts/deduplicate_full_build_flags.py"', full_block
        )

    def test_windows_shortener_waits_for_companion_project_sources(self):
        environment, _output, _namespace = apply_policy("", "")
        if os.name == "nt":
            self.assertEqual(len(environment.middlewares), 1)
            self.assertEqual(
                environment.middlewares[0][1],
                "*examples*companion_radio*.cpp",
            )

        environment, _output, _namespace = apply_policy(
            "", "", companion=False
        )
        self.assertEqual(environment.middlewares, [])

    def test_middleware_pattern_excludes_dependencies_with_full_env_name(self):
        pattern = "*examples*companion_radio*.cpp"
        self.assertTrue(
            fnmatch.fnmatch(
                ".pio/build/heltec_full/examples/companion_radio/MyMesh.cpp",
                pattern,
            )
        )
        self.assertFalse(
            fnmatch.fnmatch(
                ".pio/build/heltec_companion_radio_full/"
                "lib123/ArduinoJson/src/Json.cpp",
                pattern,
            )
        )


if __name__ == "__main__":
    unittest.main()
