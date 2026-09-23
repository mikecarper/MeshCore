import ctypes
import hashlib
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
    def test_compact_sha512_matches_hashlib_at_block_boundaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Path(temp_dir) / "sha512.so"
            subprocess.run(["cc", "-std=c99", "-O2", "-shared", "-fPIC",
                            "-DED25519_COMPACT_SHA512=1", str(ED25519 / "sha512.c"),
                            "-o", str(library)], check=True)
            sha = ctypes.CDLL(str(library)).sha512
            sha.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
            sha.restype = ctypes.c_int
            for length in (0, 1, 63, 64, 111, 112, 127, 128, 129, 255, 256, 1024, 4097):
                data = bytes((i * 73 + 19) % 256 for i in range(length))
                output = ctypes.create_string_buffer(64)
                with self.subTest(length=length):
                    self.assertEqual(sha(data, len(data), output), 0)
                    self.assertEqual(output.raw, hashlib.sha512(data).digest())

    def test_stm32_builds_enable_compact_base_table(self):
        platformio = (ROOT / "platformio.ini").read_text()
        stm32_base = platformio.split("[stm32_base]", 1)[1].split("\n[", 1)[0]
        self.assertIn("-D ED25519_COMPACT_BASE=1", stm32_base)

    def test_standard_and_compact_paths_match_rfc8032(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for label, extra_flags in (
                ("standard", []),
                ("compact", ["-DED25519_COMPACT_BASE=1"]),
                ("compact_sha512", ["-DED25519_COMPACT_BASE=1",
                                    "-DED25519_COMPACT_SHA512=1"]),
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
