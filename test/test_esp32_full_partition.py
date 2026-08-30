#!/usr/bin/env python3

import contextlib
import io
import os
from pathlib import Path
import runpy
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "esp32_full_partition.py"
INDICATOR_PROFILE = (
    ROOT / "variants" / "sensecap_indicator-espnow" / "platformio.ini"
)
INDICATOR_PARTITIONS = (
    "variants/sensecap_indicator-espnow/"
    "dual_ota_2560k_preserve_spiffs.csv"
)


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
    def __init__(self, board):
        self.board = board

    def BoardConfig(self):
        return self.board


def apply_policy(
    values, *, full=True, companion=True, required_partitions=""
):
    board = FakeBoardConfig(values)
    environment = {
        "MESHCORE_ESP32_FULL_BUILD": "1" if full else "0",
        "MESHCORE_COMPANION_RADIO_FULL": "1" if companion else "0",
        "MESHCORE_ESP32_FULL_PARTITION_TABLE": required_partitions,
    }
    output = io.StringIO()
    with mock.patch.dict(os.environ, environment, clear=False):
        with contextlib.redirect_stdout(output):
            runpy.run_path(
                str(SCRIPT),
                init_globals={
                    "Import": lambda _name: None,
                    "env": FakeEnvironment(board),
                },
            )
    return board, output.getvalue()


class Esp32FullPartitionTest(unittest.TestCase):
    def test_indicator_full_retains_preserve_spiffs_layout(self):
        profile = INDICATOR_PROFILE.read_text()
        self.assertIn(
            f"board_build.partitions = {INDICATOR_PARTITIONS}", profile
        )

        table = (ROOT / INDICATOR_PARTITIONS).read_text()
        self.assertIn("app0,     app,  ota_0,   0x10000,  0x280000", table)
        self.assertIn("spiffs,   data, spiffs,  0x290000, 0x160000", table)
        self.assertIn("app1,     app,  ota_1,   0x400000, 0x280000", table)

        board, output = apply_policy(
            {
                "upload.flash_size": "8MB",
                "build.mcu": "esp32s3",
                "build.variant": "esp32s3",
                "build.flash_mode": "qio",
                "build.partitions": INDICATOR_PARTITIONS,
            }
        )

        self.assertEqual(board.values["build.partitions"], INDICATOR_PARTITIONS)
        self.assertIn(("build.partitions", INDICATOR_PARTITIONS), board.updates)
        self.assertEqual(board.values["build.flash_mode"], "dio")
        self.assertIn(INDICATOR_PARTITIONS, output)

    def test_absolute_indicator_partition_path_is_also_preserved(self):
        absolute = str(ROOT / INDICATOR_PARTITIONS)
        board, _output = apply_policy(
            {
                "upload.flash_size": "8MB",
                "build.mcu": "esp32s3",
                "build.partitions": absolute,
            }
        )
        self.assertEqual(board.values["build.partitions"], INDICATOR_PARTITIONS)

    def test_indicator_target_override_preserves_espnow_base_data_layout(self):
        board, _output = apply_policy(
            {
                "upload.flash_size": "8MB",
                "build.mcu": "esp32s3",
                "build.partitions": "default.csv",
            },
            required_partitions=INDICATOR_PARTITIONS,
        )
        self.assertEqual(board.values["build.partitions"], INDICATOR_PARTITIONS)

    def test_unknown_required_partition_table_fails_closed(self):
        with self.assertRaisesRegex(
            ValueError, "unsupported required Full partition table"
        ):
            apply_policy(
                {
                    "upload.flash_size": "8MB",
                    "build.mcu": "esp32s3",
                    "build.partitions": "default.csv",
                },
                required_partitions="variants/not-a-real-layout.csv",
            )

    def test_generic_eight_megabyte_full_still_expands(self):
        board, _output = apply_policy(
            {
                "upload.flash_size": "8MB",
                "build.mcu": "esp32s3",
                "build.partitions": "default.csv",
            }
        )
        self.assertEqual(board.values["build.partitions"], "default_8MB.csv")

    def test_capacity_only_custom_table_does_not_bypass_full_policy(self):
        board, _output = apply_policy(
            {
                "upload.flash_size": "4MB",
                "build.mcu": "esp32",
                "build.partitions": "variants/dual_ota_1536k.csv",
            }
        )
        self.assertEqual(board.values["build.partitions"], "huge_app.csv")

    def test_tbeam_1w_factory_layout_keeps_precedence(self):
        board, _output = apply_policy(
            {
                "upload.flash_size": "8MB",
                "build.mcu": "esp32s3",
                "build.variant": "lilygo_tbeam_1w",
                "build.partitions": INDICATOR_PARTITIONS,
            }
        )
        self.assertEqual(board.values["build.partitions"], "huge_app.csv")

    def test_non_full_build_is_untouched(self):
        board, output = apply_policy(
            {
                "upload.flash_size": "8MB",
                "build.mcu": "esp32s3",
                "build.flash_mode": "qio",
                "build.partitions": INDICATOR_PARTITIONS,
            },
            full=False,
        )
        self.assertEqual(board.updates, [])
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
