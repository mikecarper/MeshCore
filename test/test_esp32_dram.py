#!/usr/bin/env python3
"""Boot/static DRAM limits, independent of the larger running heap budget."""

import contextlib
import configparser
import io
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_esp32_dram.py"
CHECKER = runpy.run_path(str(SCRIPT))


def linker_map(used, *, origin=0x3FFBDB5C, length=0x1E6A4):
    # Include the real ESP32 Arduino 2.x region and an alignment/.noinit gap.
    # The other half of internal DRAM and PSRAM must not inflate this budget.
    return f"""Discarded input sections
 .bss.discarded 0x00000000 0x80000 unused.o

Memory Configuration
Name             Origin             Length             Attributes
dram0_0_seg      {origin:#018x} {length:#018x} rw
extern_ram_seg  0x000000003f800000 0x0000000000400000 xrw

Linker script and memory map
.dram0.data {origin + 4:#x} 0x6000
                {origin + 4:#x} _data_start = ABSOLUTE (.)
                {origin + 0x6004:#x} _data_end = ABSOLUTE (.)
.noinit {origin + 0x6004:#x} 0x1fc
.dram0.bss {origin + 0x6200:#x} {used - 0x6200:#x}
                {origin + 0x6200:#x} _bss_start = ABSOLUTE (.)
                {origin + used:#x} _bss_end = ABSOLUTE (.)
.dram0.heap_start
                {origin + used:#x} 0x0
                {origin + used:#x} _heap_start = ABSOLUTE (.)
.ext_ram.bss 0x3f800000 0x100000
Cross Reference Table
_heap_start libheap.a
"""


class FakeEnvironment:
    def __init__(self, directory, mcu="esp32", reserve="8192"):
        self.directory = str(directory)
        self.mcu = mcu
        self.reserve = reserve
        self.actions = {}

    def BoardConfig(self):
        return {"build.mcu": self.mcu, "upload.maximum_ram_size": 327680}

    def GetProjectOption(self, name, default):
        return self.reserve

    def subst(self, value):
        return value.replace("$BUILD_DIR", self.directory).replace(
            "${PROGNAME}", "firmware"
        )

    def AddPreAction(self, target, action):
        self.actions[target] = action

    def Alias(self, name):
        return name


class Esp32DramTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.map = self.directory / "firmware.map"
        (self.directory / "firmware.elf").write_bytes(b"fixture ELF")

    def check(self, text, reserve=8192):
        self.map.write_text(text)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = CHECKER["check_map"](self.map, reserve)
        return result, output.getvalue()

    def test_reported_38_percent_image_is_rejected(self):
        result, output = self.check(linker_map(124020))
        self.assertEqual(result, 1)
        self.assertIn("560 bytes free", output)
        self.assertIn("reserve short by 7,632 bytes", output)

    def test_tuned_repeater_passes_with_actual_region_headroom(self):
        result, output = self.check(linker_map(108308))
        self.assertEqual(result, 0)
        self.assertIn("108,308/124,580", output)
        self.assertIn("16,272 bytes free", output)

    def test_reserve_boundary_includes_noinit_and_alignment(self):
        self.assertEqual(self.check(linker_map(124580 - 8192))[0], 0)
        self.assertEqual(self.check(linker_map(124580 - 8191))[0], 1)

    def test_documented_ceiling_wins_over_a_larger_linker_region(self):
        result, output = self.check(linker_map(
            160 * 1024 - 8191, origin=0x3FFB0000, length=0x2C200
        ))
        self.assertEqual(result, 1)
        self.assertIn("/163,840 bytes", output)

    def test_sdk_bluetooth_trace_reservations_reduce_the_limit(self):
        # A later start/smaller length already incorporates SDK reservations;
        # do not subtract another blanket 64 KiB from the linked region.
        result, output = self.check(linker_map(
            65536 - 8191, origin=0x3FFC0000, length=65536
        ))
        self.assertEqual(result, 1)
        self.assertIn("/65,536 bytes", output)

    def test_region_overflow_fails_even_below_160_kib(self):
        result, output = self.check(linker_map(124584))
        self.assertEqual(result, 1)
        self.assertIn("-4 bytes free", output)

    def test_larger_reserve_is_supported_but_cannot_be_disabled(self):
        self.assertEqual(self.check(linker_map(108308), "0x4000")[0], 1)
        for reserve in (0, -1, 8191, "invalid"):
            with self.subTest(reserve=reserve):
                self.assertEqual(self.check(linker_map(108308), reserve)[0], 2)

    def test_missing_or_inconsistent_map_never_passes(self):
        valid = linker_map(108308)
        for text in (
            "", valid.replace("dram0_0_seg", "other_region"),
            valid.replace("_heap_start =", "missing ="),
            valid + "\n 0x3ffd8270 _heap_start = ABSOLUTE (.)\n",
            valid.replace("0x3ffd8270 _heap_start", "0x3ffb0000 _heap_start"),
            linker_map(108308, origin=0x3F800000),
        ):
            with self.subTest(text=text[:40]):
                self.assertEqual(self.check(text)[0], 2)

    def test_missing_map_fails(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(CHECKER["check_map"](self.map), 2)

    def test_other_esp32_chips_do_not_inherit_classic_limits(self):
        for mcu in ("esp32s2", "esp32s3", "esp32c3", "esp32c6"):
            with self.subTest(mcu=mcu):
                env = FakeEnvironment(self.directory, mcu)
                CHECKER["register_platformio"](env)
                self.assertFalse(env.actions)

    def test_guard_loads_after_the_custom_merge_target_is_defined(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "platformio.ini")
        scripts = config["esp32_base"]["extra_scripts"].split()
        self.assertGreater(
            scripts.index("post:scripts/check_esp32_dram.py"),
            scripts.index("merge-bin.py"),
        )

    def test_build_image_merge_and_cached_upload_are_all_gated(self):
        self.map.write_text(linker_map(124020))
        env = FakeEnvironment(self.directory)
        CHECKER["register_platformio"](env)
        for target in ("checkprogsize", "$BUILD_DIR/${PROGNAME}.bin", "mergebin", "upload"):
            with self.subTest(target=target):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(env.actions[target]([], [], env), 1)

    def test_cached_success_does_not_hide_a_changed_map(self):
        self.map.write_text(linker_map(108308))
        env = FakeEnvironment(self.directory)
        CHECKER["register_platformio"](env)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(env.actions["checkprogsize"]([], [], env), 0)
            self.map.write_text(linker_map(124020) + "\n")
            self.assertEqual(env.actions["upload"]([], [], env), 1)

    def test_cached_upload_without_map_or_elf_fails(self):
        env = FakeEnvironment(self.directory)
        CHECKER["register_platformio"](env)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(env.actions["upload"]([], [], env), 2)
            self.map.write_text(linker_map(108308))
            (self.directory / "firmware.elf").unlink()
            self.assertEqual(env.actions["upload"]([], [], env), 2)

    def test_command_line_returns_failure_for_an_unsafe_image(self):
        self.map.write_text(linker_map(124020))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.map)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ESP32 static DRAM check failed", result.stderr)


class Esp32MergeTest(unittest.TestCase):
    def load_merge(self, extra_images, exit_code=0):
        commands = []
        env = SimpleNamespace(
            BoardConfig=lambda: {"build.mcu": "esp32"},
            get=lambda name, default: extra_images,
            Flatten=lambda value: value,
            AddCustomTarget=lambda **kwargs: None,
            Execute=lambda command: commands.append(command) or exit_code,
        )

        def import_env(*names):
            # nobuild exports env without creating projenv.
            self.assertEqual(names, ("env",))

        namespace = runpy.run_path(
            str(ROOT / "merge-bin.py"),
            init_globals={"Import": import_env, "env": env},
        )
        return namespace["merge_bin_action"], env, commands

    def test_missing_boot_layout_cannot_produce_app_only_merged_image(self):
        merge, env, commands = self.load_merge([])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(merge([], [], env), 1)
        self.assertFalse(commands)

    def test_merge_tool_failure_is_propagated(self):
        merge, env, commands = self.load_merge(
            ["0x1000", "bootloader.bin", "0x8000", "partitions.bin"], 7
        )
        source = SimpleNamespace(get_abspath=lambda: "/fixture/firmware.bin")
        self.assertEqual(merge([source], [], env), 7)
        self.assertEqual(len(commands), 1)


if __name__ == "__main__":
    unittest.main()
