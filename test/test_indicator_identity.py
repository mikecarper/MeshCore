"""Keep Indicator's LoRa and ESP-NOW application reports distinguishable."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class IndicatorIdentityTests(unittest.TestCase):
    def test_actual_board_method_reports_its_compiled_transport(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("C++ compiler unavailable")
        header = (ROOT / "variants/sensecap_indicator-espnow/target.h").read_text()
        method = re.search(r"const char\* getManufacturerName\(\) const override\s*\{([^}]+)\}",
                           header).group(1)
        configuration = (ROOT / "variants/sensecap_indicator-espnow/platformio.ini").read_text()
        self.assertIn("-D SENSECAP_INDICATOR_LORA", configuration)
        for lora in (False, True):
            with self.subTest(lora=lora), tempfile.TemporaryDirectory(prefix="indicator-identity-") as folder:
                folder = Path(folder)
                cpp = folder / "identity.cpp"
                executable = folder / ("identity.exe" if os.name == "nt" else "identity")
                expected = "Seeed SenseCAP Indicator " + ("LoRa" if lora else "ESP-NOW")
                cpp.write_text('#include <cassert>\n#include <cstring>\n'
                               'const char* name() {' + method + '}\n'
                               f'int main() {{ assert(std::strcmp(name(), "{expected}") == 0); }}\n',
                               encoding="ascii")
                command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror"]
                if lora:
                    command.append("-DSENSECAP_INDICATOR_LORA")
                result = subprocess.run(command + [str(cpp), "-o", str(executable)],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
