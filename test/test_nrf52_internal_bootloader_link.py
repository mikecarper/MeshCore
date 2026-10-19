#!/usr/bin/env python3
"""Build-wiring and memory-map contracts for the nRF52840 hybrid mOTA linkers."""

import ast
import contextlib
import io
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nrf52_internal_bootloader_link.py"
V6_TARGET = "RAK_3401_repeater_lora_ota_no_external_sensors"
V7_TARGET = "KeepteenLT1_repeater_lora_ota_no_external_sensors"


class FakeBoardConfig:
    def __init__(self, values):
        self.values = dict(values)
        self.updates = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def update(self, key, value):
        self.values[key] = value
        self.updates.append((key, value))


class FakeEnvironment:
    def __init__(self, project, pioenv, board):
        self.values = {
            "PROJECT_DIR": str(project),
            "PIOENV": pioenv,
        }
        self.board = board
        self.cppdefines = []
        self.replacements = {}

    def __getitem__(self, key):
        return self.values[key]

    def get(self, key, default=None):
        return self.values.get(key, default)

    def BoardConfig(self):
        return self.board

    def AppendUnique(self, **values):
        self.assert_only_key(values, "CPPDEFINES")
        for value in values["CPPDEFINES"]:
            if value not in self.cppdefines:
                self.cppdefines.append(value)

    def Replace(self, **values):
        self.replacements.update(values)

    @staticmethod
    def assert_only_key(values, expected):
        if set(values) != {expected}:
            raise AssertionError(f"unexpected fake-SCons call: {values!r}")


def run_selector(*, pioenv, board_values, project=ROOT, requested=False, legacy_flash=None,
                 flags=(), cppdefines=()):
    board = FakeBoardConfig(board_values)
    environment = FakeEnvironment(project, pioenv, board)
    environment.values.update(BUILD_FLAGS=flags, CPPDEFINES=cppdefines)
    process_environment = (
        {"MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE": "1"}
        if requested else {}
    )
    if legacy_flash is not None:
        process_environment["MESHCORE_NRF52_LEGACY_FLASH_OTA"] = legacy_flash
    output = io.StringIO()
    with mock.patch.dict(os.environ, process_environment, clear=True):
        with contextlib.redirect_stdout(output):
            runpy.run_path(
                str(SCRIPT),
                init_globals={
                    "Import": lambda name: None,
                    "env": environment,
                },
            )
    return environment, output.getvalue()


def integer_expression(expression):
    """Evaluate the integer +/- expressions used by these MEMORY declarations."""
    tree = ast.parse(expression.strip(), mode="eval")

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            left = evaluate(node.left)
            right = evaluate(node.right)
            return left + right if isinstance(node.op, ast.Add) else left - right
        raise ValueError(f"unsupported linker expression: {expression!r}")

    return evaluate(tree)


def memory_regions(path):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\bMEMORY\s*\{(?P<body>.*?)\}\s*SECTIONS\b", text, re.S)
    if not match:
        raise AssertionError(f"no MEMORY block in {path}")
    regions = {}
    pattern = re.compile(
        r"^\s*(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)\s*:\s*"
        r"ORIGIN\s*=\s*(?P<origin>.*?),\s*LENGTH\s*=\s*(?P<length>.*?)\s*$",
        re.M,
    )
    for region in pattern.finditer(match.group("body")):
        regions[region.group("name")] = (
            integer_expression(region.group("origin")),
            integer_expression(region.group("length")),
        )
    return regions


def c_integer(path, name):
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*(0x[0-9A-Fa-f]+|[0-9]+)u?\s*;",
        text,
    )
    if not match:
        raise AssertionError(f"cannot find {name} in {path}")
    return int(match.group(1), 0)


class Nrf52InternalBootloaderSelectorTest(unittest.TestCase):
    def test_legacy_sd_receiver_reserves_auth_without_hybrid_features(self):
        for flags, cppdefines in (("-D OTA_SD_STORE=1", ()),
                                  ((), [("OTA_SD_STORE", 1)]),
                                  ("-DOTA_SD_STORE -DOTA_SEEDER_ONLY -UOTA_SEEDER_ONLY", ())):
            with self.subTest(flags=flags, cppdefines=cppdefines):
                environment, output = run_selector(
                    pioenv="RAK_4631_repeater_lora_ota_no_external_sensors",
                    legacy_flash="1", flags=flags, cppdefines=cppdefines,
                    board_values={"build.mcu": "nrf52840",
                                  "build.ldscript": "boards/nrf52840_s140_v6.ld"},
                )
                expected = str(ROOT / "boards/nrf52840_s140_v6_sd_ota.ld")
                self.assertEqual(environment.cppdefines, [])
                self.assertEqual(environment.board.updates, [("build.ldscript", expected)])
                self.assertEqual(environment.replacements, {"LDSCRIPT_PATH": expected})
                self.assertIn("reserved SD authorization", output)

    def test_legacy_source_only_or_undefined_sd_keeps_ordinary_linker(self):
        for flags in ("-DOTA_SD_STORE -DOTA_SEEDER_ONLY=1",
                      "-DOTA_SD_STORE -UOTA_SD_STORE",
                      "-DOTA_SD_STORE -DOTA_SEEDER_ONLY=0"):
            with self.subTest(flags=flags):
                environment, _ = run_selector(
                    pioenv=V6_TARGET, legacy_flash="1", flags=flags,
                    board_values={"build.mcu": "nrf52840",
                                  "build.ldscript": "nrf52840_s140_v6.ld"},
                )
                self.assertEqual(environment.board.updates, [])
                self.assertEqual(environment.replacements, {})

    def test_legacy_sd_unknown_reservation_map_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "missing legacy SD authorization linker"):
            run_selector(pioenv=V7_TARGET, legacy_flash="1", flags="-DOTA_SD_STORE=1",
                         board_values={"build.mcu": "nrf52840",
                                       "build.ldscript": "nrf52840_s140_v7.ld"})

    def test_legacy_flash_preserves_linker_for_direct_and_release_builds(self):
        for requested in (False, True):
            with self.subTest(requested=requested):
                environment, output = run_selector(
                    pioenv=V6_TARGET, requested=requested, legacy_flash="1",
                    board_values={"build.mcu": "nrf52840",
                                  "build.ldscript": "boards/nrf52840_s140_v6.ld"},
                )
                self.assertEqual(environment.cppdefines, [])
                self.assertEqual(environment.board.updates, [])
                self.assertEqual(environment.replacements, {})
                self.assertIn("no hybrid RAM or bootloader self-update", output)

    def test_legacy_flash_rejects_unqualified_target_and_typo(self):
        for target, value, error in (
            ("RAK_3401_repeater", "1", "qualified internal"),
            (V6_TARGET, "yes", "must be 0 or 1"),
        ):
            with self.subTest(target=target, value=value):
                with self.assertRaisesRegex(RuntimeError, error):
                    run_selector(pioenv=target, legacy_flash=value,
                                 board_values={"build.mcu": "nrf52840"})

    def test_legacy_flash_still_validates_mcu_and_memory_map(self):
        for mcu, linker in (("nrf52832", "nrf52840_s140_v6.ld"),
                            ("nrf52840", "nrf52840_s140_v6_mota64.ld"),
                            ("nrf52840", "custom_nrf52840_s140_v6.ld")):
            with self.subTest(mcu=mcu, linker=linker):
                with self.assertRaisesRegex(RuntimeError, "normal S140 v6/v7"):
                    run_selector(pioenv=V6_TARGET, legacy_flash="1",
                                 board_values={"build.mcu": mcu, "build.ldscript": linker})

    def test_direct_v6_target_selects_hybrid_linker_and_both_features(self):
        environment, output = run_selector(
            pioenv=V6_TARGET,
            board_values={
                "build.mcu": "nrf52840",
                "build.ldscript": "boards/nrf52840_s140_v6.ld",
            },
        )
        expected = str(ROOT / "boards" / "nrf52840_s140_v6_mota64.ld")
        self.assertEqual(
            environment.cppdefines,
            ["OTA_INTERNAL_BOOTLOADER_UPDATE", "OTA_HYBRID_RAM_STORE"],
        )
        self.assertEqual(environment.board.values["build.ldscript"], expected)
        self.assertEqual(environment.replacements, {"LDSCRIPT_PATH": expected})
        self.assertIn("nrf52840_s140_v6_mota64.ld", output)

    def test_arduino_v7_fallback_selects_v7_hybrid_linker(self):
        environment, output = run_selector(
            pioenv=V7_TARGET,
            board_values={
                "build.mcu": "NRF52840",
                "build.arduino.ldscript": "nrf52840_s140_v7.ld",
            },
        )
        expected = str(ROOT / "boards" / "nrf52840_s140_v7_mota64.ld")
        self.assertEqual(environment.board.updates, [("build.ldscript", expected)])
        self.assertEqual(environment.replacements["LDSCRIPT_PATH"], expected)
        self.assertIn("nrf52840_s140_v7_mota64.ld", output)

    def test_build_sh_environment_flag_activates_a_synthetic_alias(self):
        environment, _ = run_selector(
            pioenv="Heltec_tower_v2_repeater",
            requested=True,
            board_values={
                "build.mcu": "nrf52840",
                "build.ldscript": "boards/nrf52840_s140_v6.ld",
            },
        )
        self.assertIn("OTA_HYBRID_RAM_STORE", environment.cppdefines)
        self.assertTrue(environment.replacements["LDSCRIPT_PATH"].endswith("_v6_mota64.ld"))

    def test_unselected_nrf52_environment_is_untouched(self):
        environment, output = run_selector(
            pioenv="RAK_3401_repeater",
            board_values={
                "build.mcu": "nrf52840",
                "build.ldscript": "boards/nrf52840_s140_v6.ld",
            },
        )
        self.assertEqual(environment.cppdefines, [])
        self.assertEqual(environment.board.updates, [])
        self.assertEqual(environment.replacements, {})
        self.assertEqual(output, "")

    def test_wrong_mcu_and_nonstandard_linkers_fail_closed(self):
        cases = (
            ("nrf52832", "boards/nrf52840_s140_v6.ld"),
            ("nrf52840", "boards/nrf52840_s140_v6_extrafs.ld"),
            ("nrf52840", "boards/custom_nrf52840_s140_v6.ld"),
            ("nrf52840", ""),
        )
        for mcu, linker in cases:
            with self.subTest(mcu=mcu, linker=linker):
                with self.assertRaisesRegex(RuntimeError, "normal S140 v6/v7"):
                    run_selector(
                        pioenv=V6_TARGET,
                        board_values={
                            "build.mcu": mcu,
                            "build.ldscript": linker,
                        },
                    )

    def test_missing_selected_linker_fails_before_mutating_board(self):
        board = FakeBoardConfig({
            "build.mcu": "nrf52840",
            "build.ldscript": "nrf52840_s140_v6.ld",
        })
        environment = FakeEnvironment(ROOT, V6_TARGET, board)
        with mock.patch.object(Path, "is_file", return_value=False):
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "missing mOTA hybrid linker"):
                    runpy.run_path(
                        str(SCRIPT),
                        init_globals={
                            "Import": lambda name: None,
                            "env": environment,
                        },
                    )
        self.assertEqual(board.updates, [])
        self.assertEqual(environment.replacements, {})


class Nrf52HybridLinkerContractTest(unittest.TestCase):
    def test_real_linker_markers_do_not_confuse_late_data_with_reservation(self):
        compiler, linker, nm, readelf = (
            shutil.which(name) for name in ("cc", "ld", "nm", "readelf"))
        if not all((compiler, linker, nm, readelf)):
            self.skipTest("native cc, ld, nm and readelf are required for linker fixtures")
        # A large framework registration prefix moves .data beyond the auth
        # ABI even in an unreserved map. Only the explicit region markers can
        # distinguish it from a genuinely reserved receiver layout.
        assembly = """
.section .persistent_magic,"aw"
.long 0
.section .persistent_data,"aw"
.long 0
.section .regulator_recovery,"aw"
.space 64
.section .svc_data,"aw"
.space 128
.section .fs_data,"aw"
.space 32
.section .data,"aw"
.long 0x3f800000
"""
        common = """
SECTIONS {
  .data : { __data_start__ = .; *(.data) } > RAM
}
__HeapBase = __data_start__ + SIZEOF(.data);
__HeapLimit = ORIGIN(RAM) + LENGTH(RAM);
"""
        with tempfile.TemporaryDirectory(prefix="meshcore-sd-auth-linker-") as directory:
            work = Path(directory)
            (work / "fixture.S").write_text(assembly, encoding="ascii")
            (work / "nrf52_common.ld").write_text(common, encoding="ascii")
            (work / "boards").mkdir()
            shutil.copyfile(ROOT / "boards/nrf52_heap_reserve.ld",
                            work / "boards/nrf52_heap_reserve.ld")
            subprocess.run([compiler, "-c", str(work / "fixture.S"), "-o", str(work / "fixture.o")],
                           check=True, capture_output=True, text=True, timeout=30)
            (work / "overflow.S").write_text(
                assembly.replace('.section .regulator_recovery,"aw"\n.space 64',
                                 '.section .regulator_recovery,"aw"\n.space 65'),
                encoding="ascii")
            subprocess.run([compiler, "-c", str(work / "overflow.S"),
                            "-o", str(work / "overflow.o")],
                           check=True, capture_output=True, text=True, timeout=30)
            for suffix in ("v6", "v7", "v6_extrafs", "v7_extrafs",
                           "v7_xiao_bootloader_ota", "v6_sd_ota", "v6_mota64", "v7_mota64"):
                with self.subTest(suffix=suffix):
                    source = (ROOT / "boards" / f"nrf52840_s140_{suffix}.ld").read_text()
                    # Native tools suffice: omit only the ARM C library group;
                    # every production MEMORY/SECTIONS/ASSERT is kept verbatim.
                    source = re.sub(r"^GROUP\([^\n]*\)\s*$", "", source, flags=re.M)
                    (work / "fixture.ld").write_text(source, encoding="ascii")
                    linked = subprocess.run([linker, "-T", "fixture.ld", "fixture.o", "-o", "fixture.elf"],
                                            cwd=work, text=True, capture_output=True, timeout=30)
                    self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
                    output = subprocess.check_output([nm, str(work / "fixture.elf")], text=True)
                    symbols = {name: int(value, 16) for value, _kind, name in
                               (line.split() for line in output.splitlines())}
                    self.assertGreaterEqual(symbols["__data_start__"], 0x20006090)
                    self.assertEqual(symbols["__regulator_recovery_start__"], 0x20006050)
                    self.assertEqual(symbols["__regulator_recovery_end__"], 0x20006090)
                    section_info = subprocess.check_output(
                        [readelf, "-SW", str(work / "fixture.elf")],
                        text=True)
                    self.assertRegex(section_info,
                                     r"\.regulator_recovery\s+NOBITS\s+0*20006050\s+\S+\s+0*40\s")
                    if suffix in ("v6", "v7", "v6_extrafs", "v7_extrafs", "v7_xiao_bootloader_ota"):
                        self.assertNotIn("__mota_sd_auth_start__", symbols)
                        self.assertNotIn("__mota_sd_auth_end__", symbols)
                    else:
                        self.assertEqual(symbols["__mota_sd_auth_start__"], 0x20006008)
                        self.assertEqual(symbols["__mota_sd_auth_end__"], 0x20006050)
                    overflow = subprocess.run(
                        [linker, "-T", "fixture.ld", "overflow.o", "-o", "overflow.elf"],
                        cwd=work, text=True, capture_output=True, timeout=30)
                    self.assertNotEqual(overflow.returncode, 0,
                                        "65-byte recovery record must fail the production link")

    def test_sd_authorization_has_explicit_reserved_boundaries(self):
        for suffix in ("v6_sd_ota", "v6_mota64", "v7_mota64"):
            with self.subTest(suffix=suffix):
                path = ROOT / "boards" / f"nrf52840_s140_{suffix}.ld"
                regions = memory_regions(path)
                source = path.read_text(encoding="utf-8")
                self.assertEqual(regions["RAM"][0], 0x20006090)
                self.assertEqual(regions["REGULATOR_RECOVERY_RAM"], (0x20006050, 64))
                self.assertEqual(regions["PERSISTENT_RAM"], (0x20006000, 80))
                self.assertIn("__mota_sd_auth_start__ = ", source)
                self.assertIn("__mota_sd_auth_end__ = ", source)
                self.assertIn("ASSERT(ORIGIN(RAM) >= __mota_sd_auth_end__,", source)
                if suffix == "v6_sd_ota":
                    self.assertEqual(regions["RAM"][1], 0x20040000 - 0x20006090)
                    self.assertIn("ASSERT(__mota_sd_auth_start__ == 0x20006008,", source)
                    self.assertIn("ASSERT(__mota_sd_auth_end__ == 0x20006050,", source)
                else:
                    self.assertIn("__mota_sd_auth_start__ = __mota_hybrid_handoff_start__;", source)
                    self.assertIn("__mota_sd_auth_end__ = __mota_hybrid_handoff_end__;", source)
        for version in ("v6", "v7"):
            source = (ROOT / "boards" / f"nrf52840_s140_{version}.ld").read_text()
            self.assertNotIn("__mota_sd_auth_", source)

    def test_v6_and_v7_maps_match_runtime_handoff_abi(self):
        handoff = ROOT / "src" / "helpers" / "ota" / "OtaHybridHandoff.h"
        layout = ROOT / "src" / "helpers" / "ota" / "OtaFlashLayout_nrf52.h"
        auth_start = c_integer(handoff, "MOTA_HYBRID_AUTH_ADDR")
        auth_len = c_integer(handoff, "MOTA_HYBRID_AUTH_LEN")
        arena_start = c_integer(layout, "MOTA_NRF52_HYBRID_RAM_START")
        arena_len = c_integer(layout, "MOTA_NRF52_HYBRID_RAM_SIZE")

        for version, flash_start in (("v6", 0x26000), ("v7", 0x27000)):
            with self.subTest(version=version):
                hybrid_path = ROOT / "boards" / f"nrf52840_s140_{version}_mota64.ld"
                normal_path = ROOT / "boards" / f"nrf52840_s140_{version}.ld"
                hybrid = memory_regions(hybrid_path)
                normal = memory_regions(normal_path)

                self.assertEqual(hybrid["FLASH"], normal["FLASH"])
                self.assertEqual(hybrid["FLASH"], (flash_start, 0xED000 - flash_start))
                self.assertEqual(hybrid["PERSISTENT_RAM"], (0x20006000, 8 + auth_len))
                self.assertEqual(auth_start, 0x20006000 + 8)
                self.assertEqual(hybrid["REGULATOR_RECOVERY_RAM"], (auth_start + auth_len, 64))
                self.assertEqual(normal["REGULATOR_RECOVERY_RAM"], hybrid["REGULATOR_RECOVERY_RAM"])
                self.assertEqual(hybrid["RAM"][0], auth_start + auth_len + 64)
                self.assertEqual(hybrid["RAM"][0] + hybrid["RAM"][1], arena_start)
                self.assertEqual(hybrid["MOTA_RAM"], (arena_start, arena_len))
                self.assertEqual(arena_start + arena_len, 0x20040000)
                self.assertEqual(
                    normal["RAM"][1] - hybrid["RAM"][1],
                    arena_len,
                )

    def test_linkers_export_and_assert_every_fixed_handoff_boundary(self):
        required = (
            "__mota_hybrid_handoff_start__ = ADDR(.persistent) + SIZEOF(.persistent);",
            "__mota_hybrid_handoff_end__ = ORIGIN(PERSISTENT_RAM) + LENGTH(PERSISTENT_RAM);",
            "ASSERT(ADDR(.persistent) == 0x20006000,",
            "ASSERT(SIZEOF(.persistent) == 8,",
            "ASSERT(__mota_hybrid_handoff_start__ == 0x20006008,",
            "ASSERT(__mota_hybrid_handoff_end__ == 0x20006050,",
            "ASSERT(__mota_hybrid_handoff_end__ - __mota_hybrid_handoff_start__ == 72,",
            "ASSERT(ORIGIN(REGULATOR_RECOVERY_RAM) == __mota_hybrid_handoff_end__,",
            "ASSERT(ORIGIN(RAM) == __regulator_recovery_end__,",
            "ASSERT(ORIGIN(RAM) + LENGTH(RAM) == ORIGIN(MOTA_RAM),",
            "ASSERT(ORIGIN(MOTA_RAM) + LENGTH(MOTA_RAM) == 0x20040000,",
            ".mota_ram (NOLOAD)",
            'INCLUDE "nrf52_common.ld"',
        )
        for version in ("v6", "v7"):
            with self.subTest(version=version):
                path = ROOT / "boards" / f"nrf52840_s140_{version}_mota64.ld"
                text = path.read_text(encoding="utf-8")
                for contract in required:
                    self.assertIn(contract, text)


if __name__ == "__main__":
    unittest.main()
