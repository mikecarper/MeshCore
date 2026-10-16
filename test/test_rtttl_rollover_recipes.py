"""Resolve tracked RTTTL build hooks, including synthesized Full Companions."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from platformio.project.config import ProjectConfig

ROOT = Path(__file__).resolve().parents[1]
HOOK = "pre:scripts/rtttl_rollover_fix.py"


class RtttlRolloverRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ignore developer-local overlays when checking the tracked matrix.
        cls.config = ProjectConfig(str(ROOT / "platformio.ini"), parse_extra=False)
        for path in sorted((ROOT / "variants").glob("*/platformio.ini")):
            cls.config.read(str(path), parse_extra=False)

    def has_rtttl(self, section):
        return any("NonBlockingRTTTL" in dep
                   for dep in self.config.get(section, "lib_deps", []))

    def test_every_resolved_rtttl_recipe_loads_one_fix(self):
        tested = []
        for section in self.config.sections():
            if not section.startswith("env:") or not self.has_rtttl(section):
                continue
            with self.subTest(target=section):
                self.assertEqual(self.config.get(section, "extra_scripts", []).count(HOOK), 1)
                tested.append(section)
        self.assertGreater(len(tested), 70)
        self.assertIn("env:t1000e_companion_radio_usb", tested)
        self.assertIn("env:heltec_v4_r8_tft_companion_radio_ble", tested)

    def test_all_arduino_platform_bases_propagate_fix(self):
        for base in ("esp32_base", "nrf52_base", "rp2040_base", "stm32_base"):
            with self.subTest(base=base):
                self.assertEqual(self.config.get(base, "extra_scripts", []).count(HOOK), 1)

    def test_actual_builder_synthesized_full_targets_retain_their_base_hook(self):
        # Feed resolved tracked options into the real builder's target mapper.
        # No PlatformIO command, dependency download, compilation or flashing.
        options = ["build_flags", "lib_deps", "build_src_filter", "board", "extra_scripts"]
        records = [[section, [[key, self.config.get(section, key, [])] for key in options]]
                   for section in self.config.sections() if section.startswith("env:")]
        shell = r'''
source "$1/build_legacy.sh"
pio() { printf 'FORBIDDEN_PLATFORMIO\n' >&2; return 99; }
ALL_PIO_ENVS=(fixture)
PIO_CONFIG_JSON=$(<"$2")
init_project_context
for target in "${SUPPORTED_PIO_ENVS[@]}"; do
  if is_companion_radio_full_target "$target"; then
    printf '%s\t%s\n' "$target" "${PIO_ENV_BUILD_BASE_BY_NAME[$target]:-$target}"
  fi
done
'''
        with tempfile.TemporaryDirectory(prefix="rtttl-recipes-") as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(records, ensure_ascii=True), encoding="ascii")
            result = subprocess.run(["bash", "-c", shell, "rtttl-recipes", str(ROOT), str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("FORBIDDEN_PLATFORMIO", result.stdout + result.stderr)
        pairs = dict(line.split("\t") for line in result.stdout.splitlines())
        self.assertIn("t1000e_companion_radio_full", pairs)
        self.assertIn("heltec_v4_r8_tft_companion_radio_full", pairs)
        tested = []
        for target, base in pairs.items():
            section = "env:" + base
            if not self.has_rtttl(section):
                continue
            with self.subTest(target=target, base=base):
                self.assertEqual(self.config.get(section, "extra_scripts", []).count(HOOK), 1)
                tested.append(target)
        self.assertGreater(len(tested), 20)

    def test_regression_suites_are_required_in_ci(self):
        workflow = (ROOT / ".github/workflows/run-unit-tests.yml").read_text(encoding="ascii")
        self.assertIn("python3 -B test/test_rtttl_rollover.py -v", workflow)
        self.assertIn("python3 -B test/test_rtttl_rollover_recipes.py -v", workflow)


if __name__ == "__main__":
    unittest.main()
