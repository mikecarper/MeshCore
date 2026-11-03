"""Exercise the nRF52 Find My and BLE DFU advertising ownership rules."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Nrf52FindMyTest(unittest.TestCase):
    def test_shared_advertising_handoff(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "nrf_findmy_test"
            subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "src"),
                str(ROOT / "test/fixtures/findmy_nrf52/test.cpp"), "-o", str(executable),
            ], check=True)
            subprocess.run([str(executable)], check=True)

    def test_real_board_adapter_and_dfu_handoff(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ is unavailable")
        source = (ROOT / "src/helpers/NRF52Board.cpp").read_text(encoding="utf-8")

        def function(signature):
            start = source.index(signature)
            brace = source.index("{", start)
            depth = 0
            for offset in range(brace, len(source)):
                if source[offset] == "{":
                    depth += 1
                elif source[offset] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[start:offset + 1]
            raise AssertionError(f"unclosed function: {signature}")

        start = source.index("namespace {\n// One S140 advertising set")
        end = source.index("#endif\n\n// Intentional reboot", start)
        excerpt = source[start:end] + "\n" + "\n".join(
            function(signature) for signature in (
                "static void connect_callback(",
                "static void disconnect_callback(",
                "bool NRF52Board::startOTAUpdate(",
                "bool NRF52Board::stopOTAUpdate(",
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            (temp / "production_board_excerpt.h").write_text(excerpt, encoding="utf-8")
            executable = temp / "nrf_findmy_board_test"
            subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "src"), "-I", str(temp),
                str(ROOT / "test/fixtures/findmy_nrf52/board_test.cpp"), "-o", str(executable),
            ], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
