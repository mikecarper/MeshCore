"""Guard the build-local nRF52 SoftDevice flash-completion repair."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/nrf52_internal_flash_fix.py"
spec = spec_from_file_location("nrf52_internal_flash_fix", SCRIPT)
module = module_from_spec(spec)
spec.loader.exec_module(module)


class Nrf52InternalFlashFixTest(unittest.TestCase):
    def test_wait_cannot_be_optimized_into_immediate_success(self):
        source = ("before\n" + module.OLD_WAIT + "    do_wait();\n  }\n"
                  + module.OLD_FLUSH + module.OLD_VERIFY + "after\n")
        patched = module.patched_source(source)
        self.assertIn("volatile uint8_t sd_en", patched)
        self.assertIn('''__asm volatile ("" ::: "memory");''', patched)
        self.assertIn("if (sd_en)", patched)
        self.assertIn("mesh_flash_nrf5x_flush_checked", patched)
        self.assertIn("volatile uint8_t const * flash", patched)
        self.assertEqual(module.patched_source(patched), patched)

    def test_cache_failures_are_sticky_until_checked_sync(self):
        patched = module.patched_cache_source(module.OLD_CACHE_FLUSH)
        self.assertIn("mesh_flash_cache_take_flush_result", patched)
        self.assertIn("mesh_flash_cache_flush_ok = false", patched)
        self.assertIn("fc->program", patched)
        self.assertIn("fc->verify", patched)
        self.assertEqual(module.patched_cache_source(patched), patched)

    def test_internalfs_propagates_checked_flush_failure(self):
        source = (module.OLD_INTERNAL_READ + module.OLD_INTERNAL_PROG
                  + module.OLD_INTERNAL_ERASE_WRITE + module.OLD_INTERNAL_SYNC)
        patched = module.patched_internal_fs_source(source)
        self.assertIn("== (int) size", patched)
        self.assertIn("mesh_flash_nrf5x_flush_checked() ? 0 : LFS_ERR_IO", patched)
        self.assertEqual(module.patched_internal_fs_source(patched), patched)

    def test_unrecognized_framework_fails_closed(self):
        with self.assertRaises(RuntimeError):
            module.patched_source("changed SDK layout")

    def test_all_nrf52_builds_load_fix(self):
        ini = (ROOT / "platformio.ini").read_text(encoding="utf-8")
        self.assertIn("pre:scripts/nrf52_internal_flash_fix.py", ini)
        self.assertIn('"*flash_nrf5x.c"', SCRIPT.read_text())
        self.assertIn('"*flash_cache.c"', SCRIPT.read_text())
        self.assertIn('"*InternalFileSystem.cpp"', SCRIPT.read_text())
        # The hardware-in-loop images intentionally bypass nrf52_base, but
        # still use the same framework and must not regress to its unsafe SVC
        # flash-completion wait.
        for path in (ROOT / "tools/hil/profile_fixed_tx.ini",
                     ROOT / "tools/hil/profile_switch.ini"):
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(contents.count("pre:scripts/nrf52_usb_power_fix.py"),
                             contents.count("pre:scripts/nrf52_internal_flash_fix.py"),
                             str(path))
            for section in contents.split("[env:"):
                if "pre:scripts/nrf52_usb_power_fix.py" in section:
                    self.assertIn("pre:scripts/nrf52_internal_flash_fix.py", section)

    def test_application_softdevice_checks_use_compiler_safe_wrapper(self):
        helper = ROOT / "src/helpers/nrf52/SoftDeviceState.h"
        source = helper.read_text(encoding="utf-8")
        self.assertIn("volatile uint8_t state", source)
        self.assertIn('''__asm volatile ("" ::: "memory");''', source)
        for path in (ROOT / "src", ROOT / "variants", ROOT / "examples/companion_radio"):
            for source_file in path.rglob("*.cpp"):
                self.assertNotIn("sd_softdevice_is_enabled(",
                                 source_file.read_text(encoding="utf-8"),
                                 str(source_file))


if __name__ == "__main__":
    unittest.main()
