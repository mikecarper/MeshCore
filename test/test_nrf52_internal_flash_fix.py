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
        source = "before\n" + module.OLD_WAIT + "    do_wait();\n  }\nafter\n"
        patched = module.patched_source(source)
        self.assertIn("volatile uint8_t sd_en", patched)
        self.assertIn('''__asm volatile ("" ::: "memory");''', patched)
        self.assertIn("if (sd_en)", patched)
        self.assertEqual(module.patched_source(patched), patched)

    def test_unrecognized_framework_fails_closed(self):
        with self.assertRaises(RuntimeError):
            module.patched_source("changed SDK layout")

    def test_all_nrf52_builds_load_fix(self):
        ini = (ROOT / "platformio.ini").read_text(encoding="utf-8")
        self.assertIn("pre:scripts/nrf52_internal_flash_fix.py", ini)
        self.assertIn("*InternalFileSytem*src*flash*flash_nrf5x.c", SCRIPT.read_text())

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
