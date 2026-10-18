#!/usr/bin/env python3
"""Guard the constrained Mini Repeater recipe without bypassing size/storage gates."""

import configparser
import re
import runpy
import shlex
import subprocess
import textwrap
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TARGET = "wio-e5-mini_repeater"
SECTION = "env:" + TARGET
LINK_SIZE_FLAGS = (
    "-fno-caller-saves",
    "-fno-tree-dominator-opts",
    "-fno-reorder-blocks",
    "-fno-reorder-functions",
    "-fno-tree-switch-conversion",
)
OPTIONAL_CUTS = (
    "MESH_ENABLE_CARRIER_TOOLS",
    "MESH_ENABLE_MANAGEMENT_REPORTING",
    "MESH_ENABLE_PACKET_FILE_LOGGING",
)


def read_config(path):
    config = configparser.ConfigParser(
        interpolation=None, inline_comment_prefixes=(";",))
    config.read(path)
    return config


class FakeEnvironment(dict):
    def Append(self, **values):
        for name, items in values.items():
            self.setdefault(name, []).extend(items)

    def AppendUnique(self, **values):
        for name, items in values.items():
            current = self.setdefault(name, [])
            current.extend(item for item in items if item not in current)


class MiniRepeaterProfileTest(unittest.TestCase):
    def setUp(self):
        self.config = read_config(ROOT / "variants/wio-e5-mini/platformio.ini")
        self.repeater = self.config[SECTION]
        self.flags = self.repeater["build_flags"]

    def test_existing_flash_filesystem_and_neighbour_contracts_are_retained(self):
        self.assertEqual(self.repeater.getint("board_upload.maximum_size"), 245760)
        self.assertRegex(self.flags, r"-D\s+LFS_FLASH_TOTAL_SIZE=16384\b")
        self.assertRegex(self.flags, r"-D\s+MAX_NEIGHBOURS=50\b")
        self.assertEqual(245760 + 16384, 256 * 1024)
        self.assertEqual(self.repeater["platform_packages"].split(), [
            "platformio/toolchain-gccarmnoneeabi@^1.140201.0"])
        self.assertEqual(self.repeater["build_unflags"].split(), ["-Os"])
        self.assertIn("+<../examples/simple_repeater/*.cpp>",
                      self.repeater["build_src_filter"])
        self.assertNotIn("MAX_CLIENTS", self.flags)
        self.assertNotIn("--no-check", self.repeater.get("extra_scripts", ""))

    def test_three_opt_outs_apply_only_to_exact_mini_repeater(self):
        for macro in OPTIONAL_CUTS:
            self.assertRegex(self.flags, r"-D\s+" + macro + r"=0\b")
        for path in [ROOT / "platformio.ini", *sorted((ROOT / "variants").rglob("platformio.ini"))]:
            config = read_config(path)
            for name in config.sections():
                flags = config[name].get("build_flags", "")
                for macro in OPTIONAL_CUTS:
                    with self.subTest(path=path.relative_to(ROOT), section=name, macro=macro):
                        if path == ROOT / "variants/wio-e5-mini/platformio.ini" and name == SECTION:
                            continue
                        self.assertNotRegex(flags, r"-D\s*" + macro + r"=0\b")
        # Retain the profile's pre-existing trims, rather than adding unrelated cuts.
        disabled = set(re.findall(r"-D\s*(MESH_ENABLE_\w+)=0\b", self.flags))
        self.assertEqual(disabled, {*OPTIONAL_CUTS, "MESH_ENABLE_CLOCK_SYNC",
                                    "MESH_ENABLE_FLOOD_GROUP_MODERATION"})

    def test_host_cli_live_logging_and_existing_core_features_remain(self):
        self.assertNotIn("MESH_ENABLE_HOST_CLI", self.flags)
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        self.assertRegex(header, r"#ifndef MESH_ENABLE_HOST_CLI\s+"
                         r"#define MESH_ENABLE_HOST_CLI 1\b")
        self.assertNotRegex(self.flags, r"-D\s*(?:MESH_PACKET_LOGGING|MESH_USB_LOGGING_MERGED)=0\b")
        self.assertRegex(self.flags, r"-D\s+MESH_PACKET_LOGGING_COMPACT=1\b")
        self.assertRegex(self.flags, r"-D\s+MESH_ENABLE_RECENT_REPEATERS=1\b")
        # Numeric shortcut GET is deliberately not part of this size profile.
        self.assertNotIn("NUMERIC", self.flags)

    def test_compact_crypto_and_unused_timer_wrappers_are_explicit(self):
        for macro in ("ED25519_COMPACT_SHA512=1", "ED25519_COMPACT_SCALAR=1",
                      "ED25519_COMPACT_FIELD=1", "RADIOLIB_TONE_UNSUPPORTED",
                      "HAL_TIM_MODULE_ONLY"):
            self.assertRegex(self.flags, r"-D\s+" + macro + r"\b")
        common = read_config(ROOT / "platformio.ini")
        self.assertRegex(common["stm32_base"]["build_flags"],
                         r"-D\s+ED25519_COMPACT_BASE=1\b")
        self.assertIn("post:scripts/check_firmware_ram.py",
                      common["stm32_base"]["extra_scripts"])

    def test_measured_compiler_flags_reach_both_compile_and_lto_link(self):
        tokens = shlex.split(self.flags)
        for flag in ("-Oz", "-flto", *LINK_SIZE_FLAGS):
            self.assertIn(flag, tokens)
        scripts = self.repeater["extra_scripts"].split()
        self.assertIn("${stm32_base.extra_scripts}", scripts)
        self.assertIn("post:scripts/stm32_mini_size.py", scripts)
        helper = ROOT / "scripts/stm32_mini_size.py"
        for target in (TARGET, "wio-e5-mini_sensor", "wio-e5-mini_companion_radio_usb",
                       "RAK_3x72_companion_radio_usb", "wio-e5_repeater"):
            with self.subTest(target=target):
                environment = FakeEnvironment(PIOENV=target, LINKFLAGS=["keep-existing-flag"])
                runpy.run_path(str(helper), init_globals={
                    "env": environment,
                    "Import": lambda name: self.assertEqual(name, "env"),
                })
                expected = ["keep-existing-flag", *(LINK_SIZE_FLAGS if target == TARGET else ())]
                self.assertEqual(environment["LINKFLAGS"], expected)
                self.assertEqual(set(environment), {"PIOENV", "LINKFLAGS"})

    def test_capability_contract_declares_only_exact_mini_repeater_omissions(self):
        command = r'''
set -euo pipefail
source build.sh
pio() { echo 'FORBIDDEN PlatformIO call in host capability test' >&2; return 99; }
target="$1"
BUILD_PROFILE_FOR_TARGET="$2"
PIO_CONFIG_JSON='[]'
PIO_ENV_PLATFORM_BY_NAME["$target"]=STM32_PLATFORM
PIO_ENV_BOARD_BY_NAME["$target"]=lora_e5_mini
BUILD_CAPABILITIES=()
BUILD_EXPECTATIONS=()
BUILD_APPLICATION_EXPECTATIONS=()
BUILD_REDUCTIONS=(caller.record)
PLATFORMIO_BUILD_FLAGS=-DKEEP_CALLER_FLAGS=1
PLATFORMIO_BUILD_UNFLAGS=-DKEEP_CALLER_UNFLAGS=1
declare_build_capability_contract "$target" STM32_PLATFORM
[ "$PLATFORMIO_BUILD_FLAGS" = -DKEEP_CALLER_FLAGS=1 ]
[ "$PLATFORMIO_BUILD_UNFLAGS" = -DKEEP_CALLER_UNFLAGS=1 ]
printf '%s\n' "${BUILD_REDUCTIONS[@]}"
'''
        omissions = [
            "radio.carrier_tools omitted to preserve the Mini Repeater app boundary",
            "management.reporting and data.tx omitted to preserve the Mini Repeater app boundary",
            "logging.file omitted; live USB/MQTT packet logging retained",
        ]
        for target in (TARGET, "wio-e5-mini_companion_radio_usb", "wio-e5-mini_sensor",
                       "wio-e5_repeater", "RAK_3x72_repeater", TARGET + "_custom"):
            for profile in ("standard", "full"):
                with self.subTest(target=target, profile=profile):
                    result = subprocess.run(
                        ["bash", "-c", command, "mini-capability-contract", target, profile],
                        cwd=ROOT, capture_output=True, text=True, check=True, timeout=10)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(result.stdout.splitlines(),
                                     ["caller.record", *(omissions if target == TARGET else ())])

    def test_ci_links_exact_cable_only_profile_with_release_logging_and_gates(self):
        workflow = (ROOT / ".github/workflows/run-unit-tests.yml").read_text()
        job = re.search(r"^  stm32-mini-repeater-smoke:\n(.*?)(?=^  [\w-]+:|\Z)",
                        workflow, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(job)
        script = job[1].replace("\\\n", "")
        self.assertIn("set -euo pipefail", script)
        self.assertIn("bash build.sh build-firmware " + TARGET, script)
        self.assertIn("--firmware-version v1.18.0.1-halo-keymind-cascade-dev", script)
        self.assertIn("--radio-preset usa-cascade-fixed --profile cascade", script)
        self.assertIn("--allow-no-ota", script)
        self.assertIn("DISABLE_DEBUG: '0'", script)
        self.assertNotIn("--require-ota", script)
        self.assertNotIn("pio run", script)
        self.assertNotIn(" &", script)
        self.assertNotRegex(script, r"continue-on-error|maximum_size|LFS_FLASH_TOTAL_SIZE|MAX_NEIGHBOURS")
        for test in ("test_stm32_mini_repeater_profile.py", "test_mini_repeater_logging.py",
                     "test_management_feature_gate.py", "test_carrier_wave.py"):
            self.assertIn("python3 -B test/" + test + " -v", script)
        command = textwrap.dedent(job[1].rsplit("        run: |\n", 1)[1])
        subprocess.run(["bash", "-n"], input=command, text=True, check=True)


if __name__ == "__main__":
    unittest.main()
