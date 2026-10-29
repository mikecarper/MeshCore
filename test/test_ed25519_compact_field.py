"""Prove compact ref10 field operations against unrolled code and integers."""

import ctypes
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
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
            library.fe_mul121666.argtypes = [ctypes.POINTER(ctypes.c_int32)] * 2
            library.fe_mul121666.restype = None
            library.fe_frombytes.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_void_p]
            library.fe_frombytes.restype = None
            library.fe_tobytes.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]
            library.fe_tobytes.restype = None
            library.fe_isnonzero.argtypes = [ctypes.POINTER(ctypes.c_int32)]
            library.fe_isnonzero.restype = ctypes.c_int
            for name in ("fe_invert", "fe_pow22523"):
                getattr(library, name).argtypes = [ctypes.POINTER(ctypes.c_int32)] * 2
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

    def test_decode_matches_original_limbs_and_modular_integer_including_sign_bit(self):
        rng = random.Random(0xDEC0DE)
        values = [0, PRIME - 1, PRIME, PRIME + 1, 2**255 - 1]
        values += [value for bit in range(255)
                   for value in ((1 << bit) - 1, 1 << bit, (1 << bit) + 1)]
        values += [rng.getrandbits(255) for _ in range(10000)]
        for index, value in enumerate(values):
            for sign in (0, 1):
                encoded = (value | sign << 255).to_bytes(32, "little")
                outputs = []
                for library in self.libraries:
                    output = Field()
                    library.fe_frombytes(output, encoded)
                    outputs.append(list(output))
                self.assertEqual(outputs[0], outputs[1], (index, sign))
                self.assertEqual(integer(outputs[1]), value % PRIME, (index, sign))
                # The entire source is consumed before any destination write.
                overlap = ctypes.create_string_buffer(40)
                ctypes.memmove(overlap, encoded, 32)
                self.libraries[1].fe_frombytes(ctypes.cast(overlap, ctypes.POINTER(ctypes.c_int32)), overlap)
                self.assertEqual(list(Field.from_buffer(overlap)), outputs[0])

    def test_encode_and_nonzero_match_original_and_canonical_integer(self):
        values = vectors(0xEAC0DE, 10000)
        # Encoding and multiplication-by-121666 have a tighter ref10 bound.
        bounds = [110 * (1 << (26 - (i & 1))) // 100 for i in range(10)]
        values = [[min(bound, max(-bound, limb))
                   for limb, bound in zip(value, bounds)] for value in values]
        # Include nonzero representations of field zero and reduction edges.
        values += [[-19, 0, 0, 0, 0, 0, 0, 0, 0, 1 << 25],
                   [19, 0, 0, 0, 0, 0, 0, 0, 0, -(1 << 25)]]
        for index, value in enumerate(values):
            outputs = []
            for library in self.libraries:
                output = ctypes.create_string_buffer(32)
                library.fe_tobytes(output, Field(*value))
                outputs.append(output.raw)
                self.assertEqual(library.fe_isnonzero(Field(*value)), int(integer(value) != 0), index)
            self.assertEqual(outputs[0], outputs[1], index)
            self.assertEqual(int.from_bytes(outputs[1], "little"), integer(value), index)
            overlap = Field(*value)
            self.libraries[1].fe_tobytes(overlap, overlap)
            self.assertEqual(ctypes.string_at(overlap, 32), outputs[0])

    def test_multiply_constant_matches_original_integer_and_aliases(self):
        bounds = [110 * (1 << (26 - (i & 1))) // 100 for i in range(10)]
        for index, value in enumerate(vectors(0x121666, 10000)):
            value = [min(bound, max(-bound, limb)) for limb, bound in zip(value, bounds)]
            reference = Field()
            self.libraries[0].fe_mul121666(reference, Field(*value))
            for alias in (False, True):
                data = Field(*value)
                output = data if alias else Field()
                self.libraries[1].fe_mul121666(output, data)
                self.assertEqual(list(output), list(reference), (index, alias))
                self.assertEqual(integer(output), integer(value) * 121666 % PRIME, (index, alias))
                for limb, result in enumerate(output):
                    self.assertLessEqual(abs(result), 110 * (1 << (25 - (limb & 1))) // 100)

    def test_serialization_and_constant_multiply_under_sanitizers(self):
        values = vectors(0xC0DE, 128)
        bounds = [110 * (1 << (26 - (i & 1))) // 100 for i in range(10)]
        rows = []
        for index, value in enumerate(values):
            value = [min(bound, max(-bound, limb)) for limb, bound in zip(value, bounds)]
            encoded = ctypes.create_string_buffer(32)
            multiplied = Field()
            decoded = Field()
            self.libraries[0].fe_tobytes(encoded, Field(*value))
            # Alternate ignored high bits while retaining the encoded value.
            source = encoded.raw[:31] + bytes([encoded.raw[31] | ((index & 1) << 7)])
            self.libraries[0].fe_frombytes(decoded, source)
            self.libraries[0].fe_mul121666(multiplied, Field(*value))
            row = (value, list(multiplied), list(decoded), list(source), list(encoded.raw))
            rows.append("{" + ",".join("{" + ",".join(map(str, item)) + "}"
                                       for item in row) + "}")
        source = r'''
#include <assert.h>
#include <string.h>
#include "fe.h"
struct Vector { fe input, multiplied, decoded; unsigned char source[32], encoded[32]; };
static const struct Vector vectors[] = { @VECTORS@ };
int main(void) {
    for (unsigned int i = 0; i < sizeof(vectors) / sizeof(vectors[0]); ++i) {
        const struct Vector *v = &vectors[i];
        fe output;
        unsigned char bytes[32];
        fe_frombytes(output, v->source);
        assert(memcmp(output, v->decoded, sizeof(fe)) == 0);
        fe_tobytes(bytes, v->input);
        assert(memcmp(bytes, v->encoded, sizeof(bytes)) == 0);
        unsigned char nonzero = 0;
        for (unsigned int j = 0; j < sizeof(bytes); ++j) nonzero |= bytes[j];
        assert(fe_isnonzero(v->input) == (nonzero != 0));
        memcpy(output, v->input, sizeof(fe));
        fe_mul121666(output, output);
        assert(memcmp(output, v->multiplied, sizeof(fe)) == 0);
        union { fe limbs; unsigned char bytes[40]; } overlap;
        memcpy(overlap.bytes, v->source, 32);
        fe_frombytes(overlap.limbs, overlap.bytes);
        assert(memcmp(overlap.limbs, v->decoded, sizeof(fe)) == 0);
        memcpy(overlap.limbs, v->input, sizeof(fe));
        fe_tobytes(overlap.bytes, overlap.limbs);
        assert(memcmp(overlap.bytes, v->encoded, 32) == 0);
    }
}
'''.replace("@VECTORS@", ",\n".join(rows))
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "encoding.c"
            harness.write_text(source)
            executable = Path(directory) / "encoding"
            subprocess.run(["cc", "-std=c99", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-no-pie",
                            "-DED25519_COMPACT_FIELD=1", "-I", str(ED25519),
                            str(harness), str(ED25519 / "fe.c"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_shared_powers_match_original_limbs_integer_oracle_and_aliases(self):
        values = vectors(0x250255, 1500)
        bounds = [110 * (1 << (26 - (i & 1))) // 100 for i in range(10)]
        for index, value in enumerate(values):
            value = [min(bound, max(-bound, limb)) for limb, bound in zip(value, bounds)]
            for name, exponent in (("fe_invert", PRIME - 2), ("fe_pow22523", 2**252 - 3)):
                reference = Field()
                getattr(self.libraries[0], name)(reference, Field(*value))
                expected = pow(integer(value), exponent, PRIME)
                for alias in (False, True):
                    data = Field(*value)
                    output = data if alias else Field()
                    getattr(self.libraries[1], name)(output, data)
                    self.assertEqual(list(output), list(reference), (index, name, alias))
                    self.assertEqual(integer(output), expected, (index, name, alias))
                    self.check_bounds(output)

    def test_shared_powers_under_address_and_undefined_sanitizers(self):
        rows = []
        for value in vectors(0xFA250, 32)[::29]:
            bounds = [110 * (1 << (26 - (i & 1))) // 100 for i in range(10)]
            value = [min(bound, max(-bound, limb)) for limb, bound in zip(value, bounds)]
            outputs = []
            for name in ("fe_invert", "fe_pow22523"):
                output = Field()
                getattr(self.libraries[0], name)(output, Field(*value))
                outputs.append(list(output))
            rows.append("{" + ",".join("{" + ",".join(map(str, item)) + "}"
                                       for item in (value, *outputs)) + "}")
        source = r'''
#include <assert.h>
#include <string.h>
#include "fe.h"
struct Vector { fe input, inverse, power; };
static const struct Vector vectors[] = { @VECTORS@ };
int main(void) {
    for (unsigned int i = 0; i < sizeof(vectors) / sizeof(vectors[0]); ++i) {
        const struct Vector *v = &vectors[i];
        fe output;
        fe_invert(output, v->input);
        assert(memcmp(output, v->inverse, sizeof(fe)) == 0);
        memcpy(output, v->input, sizeof(fe));
        fe_invert(output, output);
        assert(memcmp(output, v->inverse, sizeof(fe)) == 0);
        fe_pow22523(output, v->input);
        assert(memcmp(output, v->power, sizeof(fe)) == 0);
        memcpy(output, v->input, sizeof(fe));
        fe_pow22523(output, output);
        assert(memcmp(output, v->power, sizeof(fe)) == 0);
    }
}
'''.replace("@VECTORS@", ",\n".join(rows))
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "powers.c"
            harness.write_text(source)
            executable = Path(directory) / "powers"
            subprocess.run(["cc", "-std=c99", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-no-pie",
                            "-DED25519_COMPACT_FIELD=1", "-I", str(ED25519),
                            str(harness), str(ED25519 / "fe.c"), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_arm_shared_powers_match_oracle_aliases_and_stay_within_stack_budget(self):
        compiler = shutil.which("arm-none-eabi-gcc")
        if not compiler:
            candidate = Path.home() / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-gcc"
            if candidate.exists():
                compiler = str(candidate)
        try:
            from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
            from unicorn import arm_const as arm
        except ImportError:
            self.skipTest("Unicorn is not installed in this host job")
        if not compiler:
            self.skipTest("ARM compiler is not installed in this host job")
        sys.path.insert(0, str(ROOT / "scripts"))
        from firmware_elf import FirmwareElf

        with tempfile.TemporaryDirectory() as directory:
            support = Path(directory) / "memory.c"
            support.write_text('''#include <stddef.h>
void *memset(void *dest, int value, size_t count) {
    volatile unsigned char *p = dest;
    for (size_t i = 0; i < count; ++i) p[i] = (unsigned char)value;
    return dest;
}
''')
            executable = Path(directory) / "powers.elf"
            subprocess.run([compiler, "-std=c99", "-mcpu=cortex-m4", "-mthumb", "-Oz",
                            "-fno-inline-small-functions", "-nostdlib", "-fno-builtin",
                            "-DED25519_COMPACT_FIELD=1", "-I", str(ED25519),
                            str(ED25519 / "fe.c"), str(support), "-lgcc",
                            "-Wl,-Ttext=0x08000000,--entry=fe_invert", "-o", str(executable)], check=True)
            elf = FirmwareElf(executable)
            machine = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
            machine.mem_map(0x08000000, 0x10000)
            machine.mem_map(0x20000000, 0x10000)
            for section in elf.sections:
                if section[1] != 8 and section[2] & 2 and section[5]:
                    machine.mem_write(section[3], elf.section_bytes(section))
            stack_top, stop = 0x2000F000, 0x0800F000
            machine.mem_write(stop, b"\x00\xbe")
            state = {"min_sp": stack_top}

            def instruction(current, _address, _size, _context):
                state["min_sp"] = min(state["min_sp"], current.reg_read(arm.UC_ARM_REG_SP))

            machine.hook_add(UC_HOOK_CODE, instruction)
            for value in ([0] * 10, [1] + [0] * 9, [127, -19] * 5):
                for name, exponent in (("fe_invert", PRIME - 2), ("fe_pow22523", 2**252 - 3)):
                    reference = Field()
                    getattr(self.libraries[0], name)(reference, Field(*value))
                    for alias in (False, True):
                        source, output = 0x20001000, 0x20001100
                        if alias:
                            output = source
                        machine.mem_write(source, struct.pack("<10i", *value))
                        machine.reg_write(arm.UC_ARM_REG_R0, output)
                        machine.reg_write(arm.UC_ARM_REG_R1, source)
                        machine.reg_write(arm.UC_ARM_REG_SP, stack_top)
                        machine.reg_write(arm.UC_ARM_REG_LR, stop | 1)
                        state["min_sp"] = stack_top
                        machine.emu_start(elf.address(name) | 1, stop, timeout=10000000, count=10000000)
                        self.assertEqual(machine.reg_read(arm.UC_ARM_REG_PC), stop)
                        self.assertEqual(machine.reg_read(arm.UC_ARM_REG_SP), stack_top)
                        actual = struct.unpack("<10i", machine.mem_read(output, 40))
                        self.assertEqual(list(actual), list(reference), (name, alias))
                        self.assertEqual(integer(actual), pow(integer(value), exponent, PRIME))
                        # Function frames only, excluding firmware callers and
                        # interrupts. The production 4 KiB stack gate remains.
                        self.assertLessEqual(stack_top - state["min_sp"], 512, (name, alias))

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

    def test_only_approved_size_constrained_stm32_profiles_opt_in(self):
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
            ("tiny_relay", "[env:Tiny_Relay_companion_radio_usb]"),
            ("wio-e5-dev", "[env:wio-e5_companion_radio_usb]"),
            ("wio-e5-mini", "[env:wio-e5-mini_companion_radio_usb]"),
            ("wio-e5-mini", "[env:wio-e5-mini_repeater]"),
        ])


if __name__ == "__main__":
    unittest.main()
