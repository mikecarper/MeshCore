#!/usr/bin/env python3
"""Exercise the actual patched framework handler and isolated build integration."""

import configparser
import importlib.util
from pathlib import Path
import tempfile
import unittest

from nrf5x_power_harness import exercise


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("usb_fix", ROOT / "scripts/nrf52_usb_power_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
ORIGINAL = (ROOT / "test/fixtures/nrf52_usb_power_original.c").read_text()


class UsbPowerTests(unittest.TestCase):
    def test_actual_handler_and_inherited_hang(self):
        exercise(FIX.patched_source(ORIGINAL))

    def test_crlf_and_idempotence(self):
        fixed = FIX.patched_source(ORIGINAL)
        self.assertEqual(fixed, FIX.patched_source(ORIGINAL.replace("\n", "\r\n")))
        self.assertEqual(fixed, FIX.patched_source(fixed))

    def test_unrecognized_or_partly_fixed_driver_fails_closed(self):
        for source in ("", ORIGINAL.replace("hfclk_running()", "clock_running()"),
                       ORIGINAL + ORIGINAL, ORIGINAL.replace(FIX.OLD_READY, FIX.READY)):
            with self.subTest(source=source[:40]), self.assertRaises(RuntimeError):
                FIX.patched_source(source)

    def test_build_local_copy_does_not_modify_shared_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "shared-sdk/dcd_nrf5x.c"
            original.parent.mkdir()
            original.write_text(ORIGINAL)

            class Node:
                def get_abspath(self):
                    return str(original)

            class VariantNode:
                def srcnode(self):
                    return Node()

                def get_abspath(self):
                    raise AssertionError("VariantDir file does not exist yet; use srcnode()")

            class Env:
                def subst(self, value):
                    assert value == "$BUILD_DIR"
                    return str(root / "build")

                def File(self, value):
                    return Path(value)

            output = FIX.replace_driver(Env(), VariantNode())
            self.assertEqual(original.read_text(), ORIGINAL)
            self.assertEqual(output, root / "build/patched-nrf52-usb/dcd_nrf5x.c")
            self.assertEqual(output.read_text(), FIX.patched_source(ORIGINAL))
            modified = output.stat().st_mtime_ns
            FIX.replace_driver(Env(), VariantNode())
            self.assertEqual(output.stat().st_mtime_ns, modified)

    def test_all_nrf52_environments_inherit_pre_hook(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read([str(ROOT / "platformio.ini"), *map(str, (ROOT / "variants").glob("*/platformio.ini"))])

        def option(section, key, seen=()):
            if section in seen:
                raise AssertionError("configuration inheritance cycle")
            if config.has_option(section, key):
                value = config.get(section, key)
                import re
                return re.sub(r"\$\{([^}.]+)\.([^}]+)\}",
                              lambda m: option(m[1], m[2], seen + (section,)), value)
            for parent in config.get(section, "extends", fallback="").split(","):
                parent = parent.strip()
                if parent and config.has_section(parent):
                    value = option(parent, key, seen + (section,))
                    if value:
                        return value
            return ""

        checked = 0
        for section in config.sections():
            if section.startswith("env:") and option(section, "platform") == "nordicnrf52":
                with self.subTest(environment=section):
                    self.assertIn("pre:scripts/nrf52_usb_power_fix.py", option(section, "extra_scripts"))
                checked += 1
        self.assertGreater(checked, 30)
        print("nRF52 USB hook inherited by %d environments" % checked)


if __name__ == "__main__":
    unittest.main()
