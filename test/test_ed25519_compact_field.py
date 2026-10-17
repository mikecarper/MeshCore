"""Prove compact ref10 products against unrolled code and integer arithmetic."""

import ctypes
from pathlib import Path
import random
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ED25519 = ROOT / "lib/ed25519"
SHIFTS = (0, 26, 51, 77, 102, 128, 153, 179, 204, 230)
BOUNDS = tuple(165 * (1 << (26 - (i & 1))) // 100 for i in range(10))
PRIME = 2**255 - 19
Field = ctypes.c_int32 * 10


def integer(limbs):
    return sum(value << shift for value, shift in zip(limbs, SHIFTS)) % PRIME


def vectors(seed, count):
    values = [[0] * 10, [1] + [0] * 9, list(BOUNDS), [-x for x in BOUNDS],
              [x if i & 1 else -x for i, x in enumerate(BOUNDS)]]
    for index, bound in enumerate(BOUNDS):
        for value in (-bound, -bound + 1, -1, 1, bound - 1, bound):
            item = [0] * 10
            item[index] = value
            values.append(item)
        for bit in range(26 - (index & 1)):
            for delta in (-1, 0, 1):
                item = [0] * 10
                item[index] = (1 << bit) + delta
                values.append(item)
    rng = random.Random(seed)
    values += [[rng.randint(-bound, bound) for bound in BOUNDS]
               for _ in range(count)]
    return values


class Ed25519CompactFieldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.libraries = []
        for compact in (0, 1):
            path = Path(cls.directory.name) / ("field-%d.so" % compact)
            subprocess.run(["cc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-shared", "-fPIC", "-DED25519_COMPACT_FIELD=%d" % compact,
                            str(ED25519 / "fe.c"), "-o", str(path)], check=True)
            library = ctypes.CDLL(str(path))
            for name, arguments in (("fe_mul", 3), ("fe_sq", 2), ("fe_sq2", 2)):
                function = getattr(library, name)
                function.argtypes = [ctypes.POINTER(ctypes.c_int32)] * arguments
                function.restype = None
            for name, arguments in (("fe_add", 3), ("fe_sub", 3),
                                    ("fe_copy", 2), ("fe_neg", 2)):
                getattr(library, name).argtypes = [ctypes.POINTER(ctypes.c_int32)] * arguments
                getattr(library, name).restype = None
            for name in ("fe_cmov", "fe_cswap"):
                getattr(library, name).argtypes = [ctypes.POINTER(ctypes.c_int32)] * 2 + [ctypes.c_uint]
                getattr(library, name).restype = None
            cls.libraries.append(library)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def check_bounds(self, result):
        for index, value in enumerate(result):
            self.assertLessEqual(abs(value), 101 * (1 << (25 - (index & 1))) // 100)

    def test_multiply_matches_original_integer_oracle_and_aliases(self):
        values = vectors(0xEDFE, 8000)
        rng = random.Random(0x26A25)
        for index, first in enumerate(values):
            second = values[rng.randrange(len(values))]
            expected = integer(first) * integer(second) % PRIME
            reference = Field()
            self.libraries[0].fe_mul(reference, Field(*first), Field(*second))
            for alias in range(3):
                a, b = Field(*first), Field(*second)
                output = a if alias == 0 else b if alias == 1 else Field()
                self.libraries[1].fe_mul(output, a, b)
                self.assertEqual(list(output), list(reference), (index, alias))
                self.assertEqual(integer(output), expected, (index, alias))
                self.check_bounds(output)

    def test_square_and_double_square_match_original_oracle_and_aliases(self):
        for index, first in enumerate(vectors(0x25519, 8000)):
            for name, scale in (("fe_sq", 1), ("fe_sq2", 2)):
                expected = integer(first)**2 * scale % PRIME
                reference = Field()
                getattr(self.libraries[0], name)(reference, Field(*first))
                for alias in (False, True):
                    data = Field(*first)
                    output = data if alias else Field()
                    getattr(self.libraries[1], name)(output, data)
                    self.assertEqual(list(output), list(reference), (index, name, alias))
                    self.assertEqual(integer(output), expected, (index, name, alias))
                    self.check_bounds(output)
            data = Field(*first)
            self.libraries[1].fe_mul(data, data, data)
            self.assertEqual(integer(data), integer(first)**2 % PRIME)

    def test_worst_case_products_and_carries_fit_signed_64_bits(self):
        # Absolute products bound every mixed-sign partial sum as well as the
        # final sum. Include the doubled-square operation and carry growth.
        limbs = [0] * 10
        for i in range(10):
            for j in range(10):
                weight = (1 if i + j < 10 else 19) * (1 + (i & j & 1))
                limbs[(i + j) % 10] += BOUNDS[i] * BOUNDS[j] * weight * 2
        self.assertLess(max(limbs), 2**62)
        for index in (0, 4, 1, 5, 2, 6, 3, 7, 4, 8, 9, 0):
            radix = 1 << (26 - (index & 1))
            self.assertLess(limbs[index] + radix // 2, 2**63)
            carry_bound = (limbs[index] + radix // 2) // radix + 1
            limbs[0 if index == 9 else index + 1] += carry_bound * (19 if index == 9 else 1)
            limbs[index] = radix // 2
            self.assertLess(max(limbs), 2**63)

    def test_limb_helpers_match_original_and_aliases(self):
        values = vectors(0xC0A5, 4000)
        for index, first in enumerate(values):
            second = values[-1 - index]
            for name in ("fe_add", "fe_sub", "fe_copy", "fe_neg"):
                binary = name in ("fe_add", "fe_sub")
                reference = Field()
                inputs = [Field(*first)] + ([Field(*second)] if binary else [])
                getattr(self.libraries[0], name)(reference, *inputs)
                for alias in range(3 if binary else 2):
                    inputs = [Field(*first)] + ([Field(*second)] if binary else [])
                    output = inputs[alias] if alias < len(inputs) else Field()
                    getattr(self.libraries[1], name)(output, *inputs)
                    self.assertEqual(list(output), list(reference), (index, name, alias))
            for name in ("fe_cmov", "fe_cswap"):
                for select in (0, 1):
                    expected_a, expected_b = Field(*first), Field(*second)
                    getattr(self.libraries[0], name)(expected_a, expected_b, select)
                    a, b = Field(*first), Field(*second)
                    getattr(self.libraries[1], name)(a, b, select)
                    self.assertEqual((list(a), list(b)), (list(expected_a), list(expected_b)))
                    a = Field(*first)
                    getattr(self.libraries[1], name)(a, a, select)
                    self.assertEqual(list(a), first)

    def test_compact_products_under_address_and_undefined_sanitizers(self):
        values = vectors(0xC0FE, 128)
        # Keep every edge/carry-boundary vector and a bounded random selection.
        rows = []
        for index, first in enumerate(values):
            second = values[-1 - index]
            outputs = []
            for name in ("fe_mul", "fe_sq", "fe_sq2"):
                output = Field()
                arguments = (output, Field(*first), Field(*second)) if name == "fe_mul" else (output, Field(*first))
                getattr(self.libraries[0], name)(*arguments)
                outputs.append(list(output))
            rows.append("{" + ",".join("{" + ",".join(map(str, row)) + "}"
                                       for row in (first, second, *outputs)) + "}")
        source = r'''
#include <assert.h>
#include <string.h>
#include "fe.h"
struct Vector { fe a, b, product, square, twice; };
static const struct Vector vectors[] = { @VECTORS@ };
int main(void) {
    unsigned int i, alias;
    for (i = 0; i < sizeof(vectors) / sizeof(vectors[0]); ++i) {
        for (alias = 0; alias < 3; ++alias) {
            fe a, b, separate;
            int32_t *output;
            memcpy(a, vectors[i].a, sizeof(a));
            memcpy(b, vectors[i].b, sizeof(b));
            output = alias == 0 ? a : alias == 1 ? b : separate;
            fe_mul(output, a, b);
            assert(memcmp(output, vectors[i].product, sizeof(fe)) == 0);
            memcpy(a, vectors[i].a, sizeof(a));
            output = alias == 0 ? a : separate;
            fe_sq(output, a);
            assert(memcmp(output, vectors[i].square, sizeof(fe)) == 0);
            memcpy(a, vectors[i].a, sizeof(a));
            fe_sq2(output, a);
            assert(memcmp(output, vectors[i].twice, sizeof(fe)) == 0);
            memcpy(a, vectors[i].a, sizeof(a));
            memcpy(b, vectors[i].b, sizeof(b));
            fe_add(separate, a, b);
            for (unsigned int limb = 0; limb < 10; ++limb)
                assert(separate[limb] == a[limb] + b[limb]);
            fe_sub(separate, a, b);
            for (unsigned int limb = 0; limb < 10; ++limb)
                assert(separate[limb] == a[limb] - b[limb]);
            fe_neg(separate, a);
            for (unsigned int limb = 0; limb < 10; ++limb)
                assert(separate[limb] == -a[limb]);
            fe_copy(separate, a);
            assert(memcmp(separate, a, sizeof(fe)) == 0);
            fe_cmov(a, b, 0);
            assert(memcmp(a, vectors[i].a, sizeof(fe)) == 0);
            fe_cmov(a, b, 1);
            assert(memcmp(a, b, sizeof(fe)) == 0);
            memcpy(a, vectors[i].a, sizeof(a));
            fe_cswap(a, b, 0);
            assert(memcmp(a, vectors[i].a, sizeof(fe)) == 0);
            fe_cswap(a, b, 1);
            assert(memcmp(a, vectors[i].b, sizeof(fe)) == 0);
            assert(memcmp(b, vectors[i].a, sizeof(fe)) == 0);
        }
    }
    return 0;
}
'''.replace("@VECTORS@", ",\n".join(rows))
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "fields.c"
            harness.write_text(source)
            executable = Path(directory) / "fields"
            subprocess.run(["cc", "-std=c99", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-no-pie",
                            "-DED25519_COMPACT_FIELD=1", "-I", str(ED25519),
                            str(harness), str(ED25519 / "fe.c"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_only_stm32_companion_recipes_opt_in(self):
        profiles = []
        for ini in (ROOT / "variants").glob("*/platformio.ini"):
            section = ""
            for line in ini.read_text().splitlines():
                if line.startswith("["):
                    section = line
                if "ED25519_COMPACT_FIELD" in line:
                    profiles.append((ini.parent.name, section))
        self.assertEqual(sorted(profiles), [
            ("rak3x72", "[env:RAK_3x72_companion_radio_usb]"),
            ("wio-e5-mini", "[env:wio-e5-mini_companion_radio_usb]"),
        ])


if __name__ == "__main__":
    unittest.main()
