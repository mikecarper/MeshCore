#!/usr/bin/env python3
"""Resolved PlatformIO contracts for onboard-NOR Repeater and Room targets.

Set MESHCORE_TEST_PIO_CONFIG to a previously generated PlatformIO JSON file
when sharing a checkout with a build. Otherwise this suite resolves the project
once; it must follow the repository's single-PlatformIO-process rule.
"""

from dataclasses import dataclass
from functools import lru_cache
import glob
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
V6 = "boards/nrf52840_s140_v6.ld"
V7 = "boards/nrf52840_s140_v7.ld"
XIAO = "boards/nrf52840_s140_v7_xiao_bootloader_ota.ld"
XIAO_BOARD = "seeed-xiao-afruitnrf52-nrf52840"
PENDING = "OTA_QSPI_HARDWARE_QUALIFICATION_PENDING"
INTERNAL_ONLY = ("OTA_HYBRID_RAM_STORE", "OTA_INTERNAL_BOOTLOADER_UPDATE")
STORAGE_CHANGES = {
    "ENABLE_OTA", "DISABLE_LORA_OTA", "QSPIFLASH", "OTA_FLASH_STORE",
    "OTA_QSPI_STORE", "OTA_FOLDER_SERIAL", "OTA_QSPI_BOOTLOADER_UPDATE", PENDING,
}


@dataclass(frozen=True)
class Profile:
    parent: str
    board: str
    variant: str
    linker: str = V6
    maximum_size: int = 815104

    @property
    def target(self):
        return self.parent + "_lora_ota"


SUPPORTED = (
    Profile("Xiao_nrf52_room_server", XIAO_BOARD, "xiao_nrf52", XIAO, 757760),
    Profile("ikoka_handheld_nrf_e22_30dbm_room_server", XIAO_BOARD,
            "ikoka_handheld_nrf", XIAO, 757760),
    *(Profile(f"ikoka_nano_nrf_{power}dbm_room_server", XIAO_BOARD,
              "ikoka_nano_nrf", XIAO, 757760) for power in (22, 30, 33)),
    *(Profile(f"ikoka_stick_nrf_{power}dbm_room_server", XIAO_BOARD,
              "ikoka_stick_nrf", XIAO, 757760) for power in (22, 30, 33)),
    Profile("LilyGo_T-Echo_room_server", "t-echo", "lilygo_techo"),
    Profile("ThinkNode_M1_room_server", "thinknode_m1", "thinknode_m1"),
    Profile("ThinkNode_M6_room_server", "thinknode_m6", "thinknode_m6"),
    Profile("WioTrackerL1_room_server", "seeed-wio-tracker-l1",
            "wio-tracker-l1", V7, 811008),
    Profile("SenseCap_Solar_room_server", "seeed_sensecap_solar",
            "sensecap_solar", V7, 811008),
    Profile("LilyGo_T-Echo-Lite_repeater", "t-echo", "lilygo_techo_lite"),
    Profile("LilyGo_T-Echo-Lite_room_server", "t-echo", "lilygo_techo_lite"),
)
PREEXISTING_SOLAR_ROOMS = (
    Profile("solarxiao_30S_room_server", XIAO_BOARD, "xiao_nrf52", XIAO, 757760),
    Profile("solarxiao_33S_room_server", XIAO_BOARD, "xiao_nrf52", XIAO, 757760),
)
QUALIFICATION = (
    *(Profile(f"muzi_base_{radio}_{role}", "muzi_base", "muzi_base")
      for radio in ("uno", "duo") for role in ("repeater", "room_server")),
    *(Profile(f"LilyGo_T-Echo_Card_{role}", "t-echo", "lilygo_techo_card")
      for role in ("repeater", "room_server")),
    *(Profile(f"Nano_G2_Ultra_{role}", "nano-g2-ultra", "nano_g2_ultra")
      for role in ("repeater", "room_server")),
)


def text_option(value):
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def macro_operations(value):
    """Parse both '-D NAME=1' and '-UNAME', preserving command-line order."""
    tokens = iter(shlex.split(text_option(value)))
    operations = []
    for token in tokens:
        if token in ("-D", "-U"):
            kind, argument = token[1], next(tokens)
        elif token.startswith(("-D", "-U")):
            kind, argument = token[1], token[2:]
        else:
            continue
        name, separator, value = argument.partition("=")
        operations.append((kind, name, value if separator else None))
    return operations


def effective_defines(options):
    # ProcessFlags emits CPPDEFINES first and appends every -U afterwards in
    # _CPPDEFFLAGS, regardless of the order in build_flags. ProcessUnFlags
    # removes matching CPPDEFINES; a bare -D name also removes valued defines.
    # A defined zero still violates raw-QSPI/QSPIFLASH exclusivity.
    removed = set(macro_operations(options.get("build_unflags", "")))
    operations = macro_operations(options.get("build_flags", ""))
    defines = {}
    for operation in operations:
        kind, name, value = operation
        if kind == "D" and operation not in removed and (kind, name, None) not in removed:
            defines[name] = value
    for kind, name, _ in operations:
        if kind == "U":
            defines.pop(name, None)
    return defines


def role_defines(options):
    return {
        name: value for name, value in effective_defines(options).items()
        if name not in STORAGE_CHANGES
    }


def variant_includes(options):
    tokens = iter(shlex.split(text_option(options.get("build_flags", ""))))
    includes = set()
    for token in tokens:
        if token == "-I":
            path = next(tokens)
        elif token.startswith("-I"):
            path = token[2:]
        else:
            continue
        if path.startswith("variants/"):
            includes.add(path)
    return includes


@lru_cache(maxsize=None)
def source_matches(pattern, path):
    # MatchSourceFiles expands glob patterns under PROJECT_SRC_DIR, then
    # recursively includes matched directories. fnmatch('*.cpp') would also
    # match helpers/ota/foo.cpp or ../examples/foo.cpp, which PIO does not.
    source = (ROOT / "src" / path).resolve()
    for candidate in glob.iglob(str(ROOT / "src" / pattern), recursive=True):
        candidate = Path(candidate).resolve()
        if candidate == source or candidate.is_dir() and candidate in source.parents:
            return True
    return False


def source_selected(options, path):
    selected = False
    for action, pattern in re.findall(
        r"([+-])<([^>]+)>", text_option(options.get("build_src_filter", ""))
    ):
        if source_matches(pattern, path):
            selected = action == "+"
    return selected


class OnboardQspiProfilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cached = os.environ.get("MESHCORE_TEST_PIO_CONFIG")
        if cached:
            payload = Path(cached).read_text(encoding="utf-8")
        else:
            payload = subprocess.check_output(
                ["pio", "project", "config", "--json-output"], cwd=ROOT, text=True
            )
        cls.config = {section: dict(options) for section, options in json.loads(payload)}

    def options(self, target):
        self.assertIn("env:" + target, self.config)
        return self.config["env:" + target]

    def assert_external_contract(self, profile, pending=False):
        options = self.options(profile.target)
        parent = self.options(profile.parent)
        defines = effective_defines(options)
        parents = re.split(r"[,\s]+", text_option(options.get("extends", "")).strip())
        self.assertEqual(parents, ["env:" + profile.parent])
        self.assertEqual(options.get("board"), profile.board)
        self.assertEqual(options.get("board"), parent.get("board"))
        self.assertEqual(role_defines(options), role_defines(parent))
        self.assertEqual(variant_includes(options), {"variants/" + profile.variant})
        self.assertEqual(variant_includes(options), variant_includes(parent))
        self.assertEqual(options.get("board_build.ldscript"), profile.linker)
        self.assertEqual(int(options.get("board_upload.maximum_size", 0)), profile.maximum_size)
        for name in ("ENABLE_OTA", "OTA_FLASH_STORE", "OTA_QSPI_STORE"):
            self.assertEqual(defines.get(name), "1", name)
        for name in ("QSPIFLASH", "DISABLE_LORA_OTA", "OTA_SD_STORE", *INTERNAL_ONLY):
            self.assertNotIn(name, defines)
        if profile.linker == XIAO:
            self.assertEqual(defines.get("OTA_QSPI_BOOTLOADER_UPDATE"), "1")
        else:
            self.assertNotIn("OTA_QSPI_BOOTLOADER_UPDATE", defines)
        if pending:
            self.assertEqual(defines.get(PENDING), "1")
        else:
            self.assertNotIn(PENDING, defines)
        self.assertIn("post:tools/mota/pio_endf.py", text_option(options.get("extra_scripts", "")))
        for source in ("helpers/ota/OtaStoreQspiNrf52.cpp", "helpers/ota/OtaManager.cpp"):
            self.assertTrue(source_selected(options, source), source)
        role = "simple_room_server" if "_room_server" in profile.parent else "simple_repeater"
        self.assertTrue(source_selected(options, "../examples/" + role + "/main.cpp"))

    def test_supported_external_profiles_keep_exact_legacy_hardware(self):
        self.assertEqual(len(SUPPORTED), 15)
        for profile in (*SUPPORTED, *PREEXISTING_SOLAR_ROOMS):
            with self.subTest(target=profile.target):
                self.assert_external_contract(profile)

    def test_original_internal_recipes_remain_distinct(self):
        for profile in (*SUPPORTED, *PREEXISTING_SOLAR_ROOMS):
            with self.subTest(target=profile.parent):
                options = self.options(profile.parent)
                defines = effective_defines(options)
                self.assertEqual(options.get("board"), profile.board)
                self.assertEqual(options.get("board_build.ldscript"), V7 if profile.linker == XIAO else profile.linker)
                for name in ("OTA_QSPI_STORE", "OTA_QSPI_BOOTLOADER_UPDATE", PENDING, *INTERNAL_ONLY):
                    self.assertNotIn(name, defines)
                self.assertNotEqual(profile.target, profile.parent)

    def test_linker_files_keep_the_checked_application_geometry(self):
        for linker, start, end, maximum_size in (
            (V6, 0x26000, 0xED000, 815104),
            (V7, 0x27000, 0xED000, 811008),
            (XIAO, 0x27000, 0xE0000, 757760),
        ):
            with self.subTest(linker=linker):
                source = (ROOT / linker).read_text(encoding="ascii")
                flash = re.search(
                    r"FLASH\s*\(rx\)\s*:\s*ORIGIN\s*=\s*(0x[0-9A-Fa-f]+),"
                    r"\s*LENGTH\s*=\s*(0x[0-9A-Fa-f]+)\s*-\s*(0x[0-9A-Fa-f]+)",
                    source,
                )
                self.assertIsNotNone(flash)
                self.assertEqual(tuple(int(value, 16) for value in flash.groups()), (start, end, start))
                self.assertEqual(end - start, maximum_size)

    def test_unqualified_profiles_are_explicit_and_do_not_claim_xiao_layout(self):
        marked = {
            section[4:] for section, options in self.config.items()
            if section.startswith("env:") and PENDING in effective_defines(options)
        }
        self.assertEqual(len(QUALIFICATION), 8)
        self.assertEqual(marked, {profile.target for profile in QUALIFICATION})
        for profile in QUALIFICATION:
            with self.subTest(target=profile.target):
                self.assert_external_contract(profile, pending=True)
                parent = self.options(profile.parent)
                self.assertNotIn(PENDING, effective_defines(parent))
                self.assertNotIn("OTA_QSPI_STORE", effective_defines(parent))
                if profile.board == "muzi_base":
                    self.assertEqual(effective_defines(parent).get("QSPIFLASH"), "1")
                    self.assertIn(
                        ("D", "QSPIFLASH", "1"),
                        macro_operations(self.options(profile.target).get("build_unflags", "")),
                    )

    def test_pending_profiles_preserve_base_optimization_unflags(self):
        baseline = set(shlex.split(text_option(self.config["nrf52_base"]["build_unflags"])))
        self.assertIn("-Ofast", baseline)
        for profile in QUALIFICATION:
            with self.subTest(target=profile.target):
                options = self.options(profile.target)
                unflags = set(shlex.split(text_option(options.get("build_unflags", ""))))
                self.assertTrue(baseline <= unflags, "missing inherited build_unflags: " + repr(baseline - unflags))
                self.assertIn("-Oz", shlex.split(text_option(options.get("build_flags", ""))))

    def test_xiao_normal_repeaters_have_a_disabled_merged_uart_driver(self):
        for target in ("Xiao_nrf52_repeater", "solarxiao_30S_repeater", "solarxiao_33S_repeater"):
            with self.subTest(target=target):
                options = self.options(target)
                defines = effective_defines(options)
                expected = {
                    "WITH_RS232_BRIDGE": "Serial1",
                    "WITH_RS232_BRIDGE_UART": "1",
                    "WITH_RS232_BRIDGE_RX": "PIN_SERIAL1_RX",
                    "WITH_RS232_BRIDGE_TX": "PIN_SERIAL1_TX",
                    "BRIDGE_MAX_BAUD": "500000",
                    "RS232_BRIDGE_MERGED": "1",
                }
                for name, value in expected.items():
                    self.assertEqual(defines.get(name), value, name)
                self.assertNotIn("RS232_BRIDGE_DEFAULT_ON", defines)
                self.assertNotIn("WITH_MQTT_BRIDGE", defines)
                self.assertTrue(source_selected(options, "helpers/bridges/RS232Bridge.cpp"))
        legacy = self.options("Xiao_nrf52_repeater_bridge_rs232")
        defines = effective_defines(legacy)
        self.assertEqual(defines.get("WITH_RS232_BRIDGE"), "Serial1")
        self.assertNotIn("RS232_BRIDGE_MERGED", defines)
        self.assertNotIn("OTA_QSPI_STORE", defines)
        self.assertEqual(legacy.get("board_build.ldscript"), V7)
        self.assertTrue(source_selected(legacy, "helpers/bridges/RS232Bridge.cpp"))

    def test_effective_defines_honor_undefines_and_removed_definitions(self):
        options = {
            "build_flags": ["-D ENABLE_OTA=0 -UENABLE_OTA -DENABLE_OTA=1",
                            "-D QSPIFLASH=1 -D ZERO_IS_DEFINED=0 -UABSENT"],
            "build_unflags": "-D QSPIFLASH=1",
        }
        self.assertEqual(effective_defines(options), {"ZERO_IS_DEFINED": "0"})
        self.assertEqual(effective_defines({
            "build_flags": "-D QSPIFLASH=1 -D KEEP=2",
            "build_unflags": "-DQSPIFLASH",
        }), {"KEEP": "2"})
        self.assertEqual(effective_defines({
            "build_flags": "-D LEVEL=1 -D LEVEL=2",
            "build_unflags": "-D LEVEL=1",
        }), {"LEVEL": "2"})

    def test_source_filters_keep_globs_inside_the_selected_directory(self):
        options = {"build_src_filter": "+<*.cpp> +<helpers/*.cpp>"}
        self.assertFalse(source_selected(options, "../examples/simple_repeater/main.cpp"))
        self.assertFalse(source_selected(options, "helpers/ota/OtaStoreQspiNrf52.cpp"))
        options["build_src_filter"] = "+<*>"
        self.assertFalse(source_selected(options, "../examples/simple_repeater/main.cpp"))
        self.assertTrue(source_selected(options, "helpers/ota/OtaStoreQspiNrf52.cpp"))
        options["build_src_filter"] = "+<../examples/simple_repeater>"
        self.assertTrue(source_selected(options, "../examples/simple_repeater/main.cpp"))
        options["build_src_filter"] = "+<helpers/ota> -<helpers/ota/OtaStoreQspiNrf52.cpp>"
        self.assertFalse(source_selected(options, "helpers/ota/OtaStoreQspiNrf52.cpp"))
        options["build_src_filter"] += " +<helpers/ota/OtaStoreQspiNrf52.cpp>"
        self.assertTrue(source_selected(options, "helpers/ota/OtaStoreQspiNrf52.cpp"))


if __name__ == "__main__":
    unittest.main()
