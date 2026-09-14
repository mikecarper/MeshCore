import unittest
from profile_preamble_diagnostic import diagnostic_plan, check_word, OFFSETS


class PreambleDiagnosticTests(unittest.TestCase):
    def test_balanced_bounded_controls(self):
        plan = list(diagnostic_plan(8, 814910))
        self.assertEqual(len(plan), 64)
        for case in OFFSETS:
            self.assertEqual(sum(offset == case for _, offset in plan), 8)
        self.assertEqual(sum(offset is not None for _, offset in plan), 56)

    def test_physical_frequency_word_check(self):
        check_word({"rf_word": 954204160}, 910000)
        check_word({"rf_word": 954466304}, 910250)
        with self.assertRaises(RuntimeError):
            check_word({"rf_word": 954204160}, 910250)


if __name__ == "__main__":
    unittest.main()
