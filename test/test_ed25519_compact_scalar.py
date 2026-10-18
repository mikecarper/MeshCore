"""Check compact ref10 scalars against independent integer arithmetic."""

import ctypes
import itertools
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ED25519 = ROOT / "lib/ed25519"
ORDER = 2**252 + 27742317777372353535851937790883648493
EDGES = (0, 1, ORDER - 1, ORDER, ORDER + 1, 2**256 - 1)


def octets(value, size):
    return value.to_bytes(size, "little")


class Ed25519CompactScalarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.libraries = []
        for compact in (0, 1):
            library_path = Path(cls.directory.name) / f"scalar-{compact}.so"
            subprocess.run(["cc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-shared", "-fPIC", f"-DED25519_COMPACT_SCALAR={compact}",
                            str(ED25519 / "sc.c"), "-o", str(library_path)], check=True)
            library = ctypes.CDLL(str(library_path))
            library.sc_reduce.argtypes = [ctypes.c_void_p]
            library.sc_reduce.restype = None
            library.sc_muladd.argtypes = [ctypes.c_void_p] * 4
            library.sc_muladd.restype = None
            cls.libraries.append(library)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_reduction_matches_integer_oracle_and_standard(self):
        rng = random.Random(0xED25519)
        values = list(EDGES) + [2**512 - 1]
        values += [ORDER * factor + delta for factor in (2, 7, 1000000)
                   for delta in (-1, 0, 1)]
        values += [1 << bit for bit in range(512)]
        values += [rng.getrandbits(512) for _ in range(4000)]
        for value in values:
            original = octets(value, 64)
            expected = octets(value % ORDER, 32)
            for compact, library in enumerate(self.libraries):
                with self.subTest(value=value, compact=compact):
                    data = ctypes.create_string_buffer(original, 64)
                    library.sc_reduce(data)
                    self.assertEqual(data.raw[:32], expected)
                    self.assertEqual(data.raw[32:], original[32:])

    def test_multiply_add_matches_integer_oracle_and_aliases(self):
        rng = random.Random(0x25519)
        triples = list(itertools.product(EDGES, repeat=3))
        triples += [tuple(rng.getrandbits(256) for _ in range(3)) for _ in range(4000)]
        for a, b, c in triples:
            expected = octets((a * b + c) % ORDER, 32)
            for compact, library in enumerate(self.libraries):
                for alias in range(4):
                    with self.subTest(a=a, b=b, c=c, compact=compact, alias=alias):
                        values = [ctypes.create_string_buffer(octets(value, 32), 32)
                                  for value in (a, b, c)]
                        output = ctypes.create_string_buffer(32) if alias == 3 else values[alias]
                        library.sc_muladd(output, *values)
                        self.assertEqual(output.raw, expected)

    def test_compact_scalars_under_address_and_undefined_sanitizers(self):
        rng = random.Random(0x8032)
        reductions = [0, ORDER - 1, ORDER, ORDER + 1, 2**512 - 1]
        reductions += [rng.getrandbits(512) for _ in range(64)]
        triples = list(itertools.product(EDGES, repeat=3))
        triples += [tuple(rng.getrandbits(256) for _ in range(3)) for _ in range(64)]

        def array(value, size):
            return "{" + ",".join(str(byte) for byte in octets(value, size)) + "}"

        reduce_vectors = ",\n".join(
            "{" + array(value, 64) + "," + array(value % ORDER, 32) + "}"
            for value in reductions)
        muladd_vectors = ",\n".join(
            "{" + ",".join(array(value, 32) for value in (a, b, c, (a * b + c) % ORDER)) + "}"
            for a, b, c in triples)
        source = r'''
#include <assert.h>
#include <string.h>
#include "sc.h"
struct ReduceVector { unsigned char input[64], expected[32]; };
struct MuladdVector { unsigned char a[32], b[32], c[32], expected[32]; };
static const struct ReduceVector reductions[] = { @REDUCTIONS@ };
static const struct MuladdVector triples[] = { @TRIPLES@ };
int main(void) {
    unsigned char data[64], a[32], b[32], c[32], separate[32];
    unsigned int i, alias;
    for (i = 0; i < sizeof(reductions) / sizeof(reductions[0]); ++i) {
        memcpy(data, reductions[i].input, sizeof(data));
        sc_reduce(data);
        assert(memcmp(data, reductions[i].expected, 32) == 0);
        assert(memcmp(data + 32, reductions[i].input + 32, 32) == 0);
    }
    for (i = 0; i < sizeof(triples) / sizeof(triples[0]); ++i) {
        for (alias = 0; alias < 4; ++alias) {
            unsigned char *out;
            memcpy(a, triples[i].a, 32);
            memcpy(b, triples[i].b, 32);
            memcpy(c, triples[i].c, 32);
            out = alias == 0 ? a : alias == 1 ? b : alias == 2 ? c : separate;
            sc_muladd(out, a, b, c);
            assert(memcmp(out, triples[i].expected, 32) == 0);
        }
    }
    return 0;
}
'''.replace("@REDUCTIONS@", reduce_vectors).replace("@TRIPLES@", muladd_vectors)
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "scalars.c"
            harness.write_text(source)
            executable = Path(directory) / "scalars"
            subprocess.run(["cc", "-std=c99", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-no-pie",
                            "-DED25519_COMPACT_SCALAR=1", "-I", str(ED25519),
                            str(harness), str(ED25519 / "sc.c"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_only_approved_size_constrained_stm32_profiles_enable_compact_scalars(self):
        profiles = []
        for ini in (ROOT / "variants").glob("*/platformio.ini"):
            section = ""
            for line in ini.read_text().splitlines():
                if line.startswith("["):
                    section = line
                if "ED25519_COMPACT_SCALAR" in line:
                    profiles.append((ini.parent.name, section))
        self.assertEqual(sorted(profiles), [
            ("rak3x72", "[env:RAK_3x72_companion_radio_usb]"),
            ("tiny_relay", "[env:Tiny_Relay_companion_radio_usb]"),
            ("wio-e5-dev", "[env:wio-e5_companion_radio_usb]"),
            ("wio-e5-mini", "[env:wio-e5-mini_companion_radio_usb]"),
            ("wio-e5-mini", "[env:wio-e5-mini_repeater]"),
        ])


if __name__ == "__main__":
    unittest.main()
