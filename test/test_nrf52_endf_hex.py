#!/usr/bin/env python3
"""Exercise the nRF52 HEX post-build hook with the installed build dependencies."""

from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

from intelhex import IntelHex


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mota"))
import motalib as ml  # noqa: E402


class BuildEnvironment(dict):
    def __init__(self, app_base):
        super().__init__(
            PROJECT_DIR=str(ROOT),
            PIOENV="nrf52_sensor_hex_test",
            CPPDEFINES=[
                "ENABLE_OTA", "NRF52_PLATFORM",
                ("FIRMWARE_VERSION", "v1.17.1.9"),
                ("MOTA_TARGET_ID", "0x12345678"),
                ("MOTA_HW_ID", "NRF52_TEST"),
            ],
        )
        self.app_base = app_base
        self.maximum_size = ml.NRF52_APP_END - app_base
        self.source_filter = "+<examples/simple_sensor>"
        self.linker = ""
        self.post_actions = {}

    def AddPostAction(self, target, action):
        self.post_actions[target] = action

    def BoardConfig(self):
        return {"upload.maximum_size": self.maximum_size, "build.ldscript": self.linker}

    def GetProjectOption(self, name, default=""):
        if name in ("build_src_filter", "src_filter"):
            return self.source_filter
        return default

    def Replace(self, **values):
        self.update(values)


def load_post_script(env):
    spec = importlib.util.spec_from_file_location(
        "nrf52_endf_hex_test", ROOT / "tools" / "mota" / "pio_endf.py")
    module = importlib.util.module_from_spec(spec)
    module.Import = lambda _name: None
    module.env = env
    spec.loader.exec_module(module)
    return module


def read_application(path):
    image = IntelHex(str(path))
    start, end = image.segments()[0]
    return bytes(image.tobinarray(start=start, size=end - start))


def stamp(action, path, env):
    with redirect_stdout(io.StringIO()):
        action([], [path], env)


class Nrf52EndfHexTests(unittest.TestCase):
    def test_hex_hook_appends_valid_identity_and_preserves_other_segments(self):
        for app_base in (ml.NRF52_APP_BASE_S140_V6, ml.NRF52_APP_BASE_S140_V7):
            with self.subTest(app_base=hex(app_base)), tempfile.TemporaryDirectory() as tmp:
                env = BuildEnvironment(app_base)
                load_post_script(env)
                action = env.post_actions["$BUILD_DIR/${PROGNAME}.hex"]

                path = Path(tmp) / "firmware.hex"
                firmware = bytes(range(256))
                uicr_base = 0x10001080
                uicr = b"\x12\x34\x56\x78"
                original = IntelHex()
                original.frombytes(firmware, offset=app_base)
                original.frombytes(uicr, offset=uicr_base)
                original.write_hex_file(str(path))

                with redirect_stdout(io.StringIO()):
                    action([], [path], env)
                result = IntelHex(str(path))
                segments = result.segments()
                self.assertEqual(len(segments), 2)
                self.assertEqual(segments[0], (
                    app_base, app_base + len(firmware) + ml.NRF52_LAYOUT_LEN + ml.ENDF_LEN))
                image = bytes(result.tobinarray(start=app_base, end=segments[0][1] - 1))
                self.assertEqual(image[:len(firmware)], firmware)
                self.assertEqual(bytes(result.tobinarray(
                    start=uicr_base, size=len(uicr))), uicr)
                self.assertTrue(ml.has_endf(image))
                self.assertEqual(ml.parse_endf_ident(image), ml.FwIdent(
                    ml.pack_version("1.17.1.9"), 0x12345678, "NRF52_TEST"))
                self.assertEqual(ml.parse_nrf52_layout(image), ml.Nrf52Layout(
                    app_base, ml.NRF52_APP_END, ml.NRF52_APP_END))

                first_pass = path.read_bytes()
                with redirect_stdout(io.StringIO()):
                    action([], [path], env)
                self.assertEqual(path.read_bytes(), first_pass)

    def test_restamps_last_identity_overrides_without_growing_or_rewriting_unchanged_hex(self):
        for app_base in (ml.NRF52_APP_BASE_S140_V6, ml.NRF52_APP_BASE_S140_V7):
            with self.subTest(app_base=app_base), tempfile.TemporaryDirectory() as directory:
                env = BuildEnvironment(app_base)
                load_post_script(env)
                action = env.post_actions["$BUILD_DIR/${PROGNAME}.hex"]
                path = Path(directory) / "firmware.hex"
                body = bytes(range(256))
                original = IntelHex()
                original.frombytes(body, offset=app_base)
                original.write_hex_file(str(path))
                stamp(action, path, env)
                expected_length = len(body) + ml.NRF52_LAYOUT_LEN + ml.ENDF_LEN
                for index in range(3):
                    env["CPPDEFINES"].extend([
                        ("FIRMWARE_VERSION", f"v1.17.1.{10 + index}"),
                        ("MOTA_TARGET_ID", hex(0x87654321 + index)),
                        ("MOTA_HW_ID", f"NRF52_CHANGED_{index}"),
                    ])
                    stamp(action, path, env)
                    application = read_application(path)
                    self.assertEqual(len(application), expected_length)
                    self.assertEqual(application[:len(body)], body)
                    self.assertEqual(application.count(ml.ENDF_MAGIC), 1)
                    self.assertEqual(application.count(ml.NRF52_LAYOUT_MAGIC), 1)
                    self.assertEqual(ml.parse_endf_ident(application), ml.FwIdent(
                        ml.pack_version(f"1.17.1.{10 + index}"),
                        0x87654321 + index, f"NRF52_CHANGED_{index}"))
                    unchanged = path.read_bytes()
                    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
                    stamp(action, path, env)
                    self.assertEqual(path.read_bytes(), unchanged)
                    self.assertEqual(path.stat().st_mtime_ns, 1_000_000_000)

    def test_restamps_layout_flags_without_duplicate_record_and_keeps_other_segments(self):
        app_base = ml.NRF52_APP_BASE_S140_V7
        with tempfile.TemporaryDirectory() as directory:
            env = BuildEnvironment(app_base)
            load_post_script(env)
            action = env.post_actions["$BUILD_DIR/${PROGNAME}.hex"]
            path = Path(directory) / "firmware.hex"
            body = bytes(range(256))
            original = IntelHex()
            original.frombytes(body, offset=app_base)
            original.frombytes(b"separate-flash-data", offset=0x40000)
            original.frombytes(b"\x12\x34\x56\x78", offset=0x10001080)
            original.start_addr = {"EIP": app_base + 4}
            original.write_hex_file(str(path))
            base_defines = list(env["CPPDEFINES"])
            for extra, expected_flags in (([], 0), (["OTA_SD_STORE"], ml.NRF52_LAYOUT_FLAG_SD),
                                          (["OTA_QSPI_STORE"], ml.NRF52_LAYOUT_FLAG_QSPI), ([], 0)):
                env["CPPDEFINES"] = base_defines + extra
                stamp(action, path, env)
                application = read_application(path)
                self.assertEqual(ml.parse_nrf52_layout(application).flags, expected_flags)
                self.assertEqual(len(application), len(body) + ml.NRF52_LAYOUT_LEN + ml.ENDF_LEN)
                self.assertEqual(application.count(ml.NRF52_LAYOUT_MAGIC), 1)
                image = IntelHex(str(path))
                self.assertEqual(bytes(image.tobinarray(start=0x40000, size=19)), b"separate-flash-data")
                self.assertEqual(bytes(image.tobinarray(start=0x10001080, size=4)), b"\x12\x34\x56\x78")
                self.assertEqual(image.start_addr, original.start_addr)

    def test_existing_trailer_without_layout_is_upgraded_in_place(self):
        app_base = ml.NRF52_APP_BASE_S140_V7
        with tempfile.TemporaryDirectory() as directory:
            env = BuildEnvironment(app_base)
            load_post_script(env)
            path = Path(directory) / "old.hex"
            body = bytes(range(256))
            old, _ = ml.ensure_endf(body, ml.FwIdent(1, 2, "OLD"))
            original = IntelHex()
            original.frombytes(old, offset=app_base)
            original.write_hex_file(str(path))
            stamp(env.post_actions["$BUILD_DIR/${PROGNAME}.hex"], path, env)
            application = read_application(path)
            self.assertEqual(len(application), len(old) + ml.NRF52_LAYOUT_LEN)
            self.assertTrue(ml.has_endf(application))
            self.assertEqual(application.count(ml.ENDF_MAGIC), 1)
            self.assertEqual(application[:len(body)], body)

    def test_stamped_hex_still_rejects_changed_unsafe_profiles_without_writes(self):
        app_base = ml.NRF52_APP_BASE_S140_V7
        unsafe_profiles = (
            ["OTA_SD_STORE", "OTA_QSPI_STORE"],
            ["OTA_QSPI_STORE", "QSPIFLASH"],
            ["OTA_HYBRID_RAM_STORE"],
            ["OTA_INTERNAL_BOOTLOADER_UPDATE"],
            ["OTA_QSPI_BOOTLOADER_UPDATE"],
            ["OTA_SD_BOOTLOADER_UPDATE"],
        )
        with tempfile.TemporaryDirectory() as directory:
            env = BuildEnvironment(app_base)
            load_post_script(env)
            action = env.post_actions["$BUILD_DIR/${PROGNAME}.hex"]
            path = Path(directory) / "firmware.hex"
            original = IntelHex()
            original.frombytes(bytes(range(256)), offset=app_base)
            original.write_hex_file(str(path))
            stamp(action, path, env)
            before = path.read_bytes()
            base_defines = list(env["CPPDEFINES"])
            for flags in unsafe_profiles:
                with self.subTest(flags=flags):
                    env["CPPDEFINES"] = base_defines + flags
                    with self.assertRaises((RuntimeError, ValueError)):
                        stamp(action, path, env)
                    self.assertEqual(path.read_bytes(), before)
            env["CPPDEFINES"] = base_defines
            env.maximum_size = None
            with self.assertRaisesRegex(RuntimeError, "region is unavailable"):
                stamp(action, path, env)
            self.assertEqual(path.read_bytes(), before)

    def test_hex_exact_size_limit_and_smaller_profile_revalidated_after_stamp(self):
        app_base = ml.NRF52_APP_BASE_S140_V7
        with tempfile.TemporaryDirectory() as directory:
            env = BuildEnvironment(app_base)
            load_post_script(env)
            action = env.post_actions["$BUILD_DIR/${PROGNAME}.hex"]
            path = Path(directory) / "firmware.hex"
            body = b"\x55" * (env.maximum_size - ml.NRF52_LAYOUT_LEN - ml.ENDF_LEN)
            original = IntelHex()
            original.frombytes(body, offset=app_base)
            original.write_hex_file(str(path))
            stamp(action, path, env)
            self.assertEqual(len(read_application(path)), env.maximum_size)
            before = path.read_bytes()
            env["CPPDEFINES"].append(("FIRMWARE_VERSION", "v1.17.1.10"))
            stamp(action, path, env)
            self.assertEqual(len(read_application(path)), env.maximum_size)
            before = path.read_bytes()
            env.maximum_size = ml.NRF52_EXTRAFS_START - app_base
            with self.assertRaisesRegex(RuntimeError, "limit is"):
                stamp(action, path, env)
            self.assertEqual(path.read_bytes(), before)

    def test_source_only_and_filesystem_staging_limits_remain_distinct(self):
        app_base = ml.NRF52_APP_BASE_S140_V7
        with tempfile.TemporaryDirectory() as directory:
            env = BuildEnvironment(app_base)
            env.source_filter = "+<examples/companion_radio>"
            env["CPPDEFINES"].extend(["EXTRAFS", "OTA_SEEDER_ONLY"])
            load_post_script(env)
            action = env.post_actions["$BUILD_DIR/${PROGNAME}.hex"]
            path = Path(directory) / "firmware.hex"
            original = IntelHex()
            original.frombytes(b"\x55" * (ml.NRF52_EXTRAFS_START - app_base), offset=app_base)
            original.write_hex_file(str(path))
            stamp(action, path, env)
            layout = ml.parse_nrf52_layout(read_application(path))
            self.assertEqual(layout.stage_ceiling, ml.NRF52_EXTRAFS_START)
            self.assertEqual(layout.flags, ml.NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS)
            before = path.read_bytes()
            env["CPPDEFINES"].remove("OTA_SEEDER_ONLY")
            with self.assertRaisesRegex(RuntimeError, "nRF52 OTA application limit"):
                stamp(action, path, env)
            self.assertEqual(path.read_bytes(), before)

    def test_trailer_growth_cannot_overwrite_another_hex_segment(self):
        app_base = ml.NRF52_APP_BASE_S140_V7
        for following in (257, 256 + ml.NRF52_LAYOUT_LEN + ml.ENDF_LEN):
            with self.subTest(following=following), tempfile.TemporaryDirectory() as directory:
                env = BuildEnvironment(app_base)
                load_post_script(env)
                path = Path(directory) / "firmware.hex"
                original = IntelHex()
                original.frombytes(bytes(range(256)), offset=app_base)
                original.frombytes(b"untouched", offset=app_base + following)
                original.write_hex_file(str(path))
                before = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "separate HEX segment"):
                    stamp(env.post_actions["$BUILD_DIR/${PROGNAME}.hex"], path, env)
                self.assertEqual(path.read_bytes(), before)

    def test_uicr_added_after_first_stamp_survives_an_identity_restamp(self):
        app_base = ml.NRF52_APP_BASE_S140_V7
        with tempfile.TemporaryDirectory() as directory:
            env = BuildEnvironment(app_base)
            load_post_script(env)
            action = env.post_actions["$BUILD_DIR/${PROGNAME}.hex"]
            path = Path(directory) / "firmware.hex"
            original = IntelHex()
            original.frombytes(bytes(range(256)), offset=app_base)
            original.write_hex_file(str(path))
            stamp(action, path, env)
            previous = read_application(path)
            augmented = IntelHex(str(path))
            augmented.frombytes(b"new-uicr", offset=0x10001080)
            augmented.write_hex_file(str(path))
            env["CPPDEFINES"].append(("FIRMWARE_VERSION", "v1.17.1.10"))
            stamp(action, path, env)
            application = read_application(path)
            self.assertEqual(len(application), len(previous))
            self.assertEqual(application[:-ml.ENDF_LEN], previous[:-ml.ENDF_LEN])
            self.assertEqual(ml.parse_endf_ident(application).fw_version, ml.pack_version("1.17.1.10"))
            self.assertEqual(bytes(IntelHex(str(path)).tobinarray(start=0x10001080, size=8)), b"new-uicr")

    def test_new_hex_over_linked_application_bound_is_rejected_even_for_source_only(self):
        for source_only in (False, True):
            with self.subTest(source_only=source_only), tempfile.TemporaryDirectory() as directory:
                env = BuildEnvironment(ml.NRF52_APP_BASE_S140_V7)
                if source_only:
                    env["CPPDEFINES"].append("OTA_SEEDER_ONLY")
                load_post_script(env)
                path = Path(directory) / "firmware.hex"
                # Just one byte too large after adding layout and EndF.
                body = b"\x55" * (env.maximum_size - ml.NRF52_LAYOUT_LEN - ml.ENDF_LEN + 1)
                original = IntelHex()
                original.frombytes(body, offset=env.app_base)
                original.write_hex_file(str(path))
                before = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "limit is"):
                    stamp(env.post_actions["$BUILD_DIR/${PROGNAME}.hex"], path, env)
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
