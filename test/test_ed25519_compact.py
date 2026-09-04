import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ED25519 = ROOT / "lib" / "ed25519"
HARNESS = ROOT / "test" / "fixtures" / "ed25519_compact" / "test_ed25519_compact.c"
SOURCES = [
    ED25519 / name
    for name in (
        "fe.c",
        "ge.c",
        "keypair.c",
        "sc.c",
        "sha512.c",
        "sign.c",
        "verify.c",
    )
]


class Ed25519CompactTest(unittest.TestCase):
    def test_stm32_builds_enable_compact_base_table(self):
        platformio = (ROOT / "platformio.ini").read_text()
        stm32_base = platformio.split("[stm32_base]", 1)[1].split("\n[", 1)[0]
        self.assertIn("-D ED25519_COMPACT_BASE=1", stm32_base)

    def test_standard_and_compact_paths_match_rfc8032(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for label, extra_flags in (
                ("standard", []),
                ("compact", ["-DED25519_COMPACT_BASE=1"]),
            ):
                executable = Path(temp_dir) / label
                subprocess.run(
                    [
                        "cc",
                        "-std=c99",
                        "-O2",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-DED25519_NO_SEED=1",
                        *extra_flags,
                        f"-I{ED25519}",
                        str(HARNESS),
                        *(str(source) for source in SOURCES),
                        "-o",
                        str(executable),
                    ],
                    check=True,
                    cwd=ROOT,
                )
                subprocess.run([str(executable)], check=True, cwd=ROOT)


if __name__ == "__main__":
    unittest.main()
