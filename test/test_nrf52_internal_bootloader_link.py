#!/usr/bin/env python3
"""Build-wiring and memory-map contracts for the nRF52840 hybrid mOTA linkers."""

import ast
import contextlib
import io
import os
from pathlib import Path
import re
import runpy
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


def run_selector(*, pioenv, board_values, project=ROOT, requested=False):
    board = FakeBoardConfig(board_values)
    environment = FakeEnvironment(project, pioenv, board)
    process_environment = (
        {"MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE": "1"}
        if requested else {}
    )
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
                self.assertEqual(hybrid["RAM"][0], auth_start + auth_len)
                self.assertEqual(hybrid["RAM"][0] + hybrid["RAM"][1], arena_start)
                self.assertEqual(hybrid["MOTA_RAM"], (arena_start, arena_len))
                self.assertEqual(arena_start + arena_len, 0x20040000)
                self.assertEqual(
                    normal["RAM"][1] - hybrid["RAM"][1],
                    arena_len + auth_len,
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
            "ASSERT(ORIGIN(RAM) == __mota_hybrid_handoff_end__,",
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
