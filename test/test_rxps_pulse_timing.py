#!/usr/bin/env python3
"""Compile the real generic helper and compare it with exact rational policy."""

from fractions import Fraction
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UINT32_MAX = (1 << 32) - 1
MAX_24_BIT = (1 << 24) - 1
DRIVER = r"""
#include <stdint.h>
#include <iostream>
#include "helpers/radiolib/RxpsPulseTiming.h"

int main() {
  uint32_t transition, hz, maximum;
  while (std::cin >> transition >> hz >> maximum) {
    std::cout << mesh::rxpspulse::bufferedSleepRequestUs(
        transition, hz, maximum) << '\n';
  }
  return std::cin.eof() ? 0 : 1;
}
"""


def ceil_fraction(value):
    return -(-value.numerator // value.denominator)


def oracle(transition, hz, maximum):
    """Exact fractions, without a fixed-width arithmetic implementation."""
    if not hz or not maximum:
        return 0
    ticks = 1 + ceil_fraction(Fraction(500 * hz, 1_000_000))
    dwell = ceil_fraction(Fraction(ticks * 1_000_000, hz))
    encoded = int(Fraction(dwell * hz, 1_000_000))
    request = transition + dwell
    if request > UINT32_MAX or encoded < ticks or encoded > maximum:
        return 0
    return request


class PulseTimingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            raise unittest.SkipTest("A C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="rxps-pulse-timing-")
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        source = folder / "timing.cpp"
        source.write_text(DRIVER, encoding="ascii")
        cls.binary = folder / "timing"
        result = subprocess.run([
            compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-fno-pie", "-no-pie", "-I", str(ROOT / "src"),
            str(source), "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def evaluate(self, cases):
        environment = os.environ.copy()
        environment["ASAN_OPTIONS"] = "detect_leaks=1:abort_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        result = subprocess.run(
            [str(self.binary)],
            input="".join("{} {} {}\n".format(*case) for case in cases),
            capture_output=True, text=True, env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = [int(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(values), len(cases))
        return values

    def compare_oracle(self, cases):
        actual = self.evaluate(cases)
        self.assertEqual(actual, [oracle(*case) for case in cases])
        return actual

    def test_sx126x_and_lr11xx_known_timer_geometries(self):
        cases = [(2600, 64000, MAX_24_BIT), (2600, 32768, MAX_24_BIT),
                 (0, 64000, MAX_24_BIT), (0, 32768, MAX_24_BIT)]
        self.assertEqual(self.evaluate(cases), [3116, 3150, 516, 550])
        for case, ticks in zip(cases, (33, 18, 33, 18)):
            value = oracle(*case)
            self.assertEqual((value - case[0]) * case[1] // 1_000_000, ticks)

    def test_xtal_and_tcxo_allowances_do_not_change_encoded_margin(self):
        cases = [(transition, hz, MAX_24_BIT)
                 for transition in (0, 1, 1000, 1600, 2600, 10000)
                 for hz in (32768, 64000)]
        for case, request in zip(cases, self.compare_oracle(cases)):
            self.assertEqual(request - case[0], 550 if case[1] == 32768 else 516)

    def test_exact_rational_oracle_across_timer_rates(self):
        rates = (1, 2, 10, 999, 1000, 1999, 2000, 2001, 32767, 32768,
                 32769, 64000, 999999, 1000000, 1000001, UINT32_MAX)
        self.compare_oracle([(2600, hz, MAX_24_BIT) for hz in rates])

    def test_minimum_policy_is_500_us_above_one_encoded_tick(self):
        cases = [(0, hz, UINT32_MAX) for hz in
                 (1, 3, 1999, 2000, 2001, 32768, 64000, 1000000, UINT32_MAX)]
        for case, request in zip(cases, self.compare_oracle(cases)):
            ticks = request * case[1] // 1_000_000
            self.assertGreaterEqual(Fraction((ticks - 1) * 1_000_000, case[1]), 500)
            target = 1 + ceil_fraction(Fraction(500 * case[1], 1_000_000))
            self.assertLess((request - 1) * case[1] // 1_000_000, target)

    def test_field_limit_checks_reencoded_microsecond_quantization(self):
        hz = UINT32_MAX
        target = 1 + ceil_fraction(Fraction(500 * hz, 1_000_000))
        encoded = 501 * hz // 1_000_000
        self.assertGreater(encoded, target)
        cases = [(2600, 64000, 32), (2600, 64000, 33),
                 (2600, 32768, 17), (2600, 32768, 18),
                 (0, hz, target), (0, hz, encoded - 1), (0, hz, encoded)]
        self.assertEqual(self.compare_oracle(cases), [0, 3116, 0, 3150, 0, 0, 501])

    def test_zero_rate_and_zero_or_insufficient_field_fail_closed(self):
        cases = [(0, 0, 0), (2600, 0, UINT32_MAX), (0, UINT32_MAX, 0),
                 (0, 1, 1), (2600, 64000, 1), (2600, 32768, 1)]
        self.assertEqual(self.evaluate(cases), [0] * len(cases))

    def test_full_uint32_transition_overflow_and_exact_last_value(self):
        cases = []
        for hz in (1, 32768, 64000, UINT32_MAX):
            dwell = oracle(0, hz, UINT32_MAX)
            cases.extend([(UINT32_MAX - dwell, hz, UINT32_MAX),
                          (UINT32_MAX - dwell + 1, hz, UINT32_MAX),
                          (UINT32_MAX, hz, UINT32_MAX)])
        self.assertEqual(self.compare_oracle(cases), [UINT32_MAX, 0, 0] * 4)

    def test_wide_intermediates_and_explicit_non_24_bit_fields(self):
        cases = [(0, UINT32_MAX, UINT32_MAX), (0, 1, 2),
                 (1000, 1000000, 501), (1000, 1000000, 500)]
        self.assertEqual(self.compare_oracle(cases), [501, 2_000_000, 1501, 0])

    def test_seeded_boundary_grid_against_independent_exact_arithmetic(self):
        randomizer = random.Random(0x52585053)
        cases = []
        for _ in range(500):
            hz = randomizer.randrange(1, UINT32_MAX + 1)
            dwell = oracle(0, hz, UINT32_MAX)
            encoded = dwell * hz // 1_000_000
            for maximum in (encoded - 1, encoded, UINT32_MAX):
                transition = randomizer.choice(
                    (0, 2600, UINT32_MAX - dwell, UINT32_MAX - dwell + 1,
                     randomizer.randrange(UINT32_MAX + 1)))
                cases.append((transition, hz, maximum))
        self.compare_oracle(cases)


if __name__ == "__main__":
    unittest.main()
