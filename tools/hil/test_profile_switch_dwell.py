import unittest
from profile_switch_dwell import DWELLS, dwell_levels


def capture(perfect=False):
    return dict(complete=True,
                stopped="channel_limit_without_miss" if perfect else "first_rf_miss",
                levels=[dict(channels=4, received=400 if perfect else 0,
                             attempted=400 if perfect else 1, status={},
                             per_channel=[dict(attempted=100, received=100)] * 4 if perfect else [])])


class DwellControllerTests(unittest.TestCase):
    def test_hard_limit_and_exact_half_symbol_grid(self):
        seen = []
        result = dwell_levels(lambda d: seen.append(d) or capture(), lambda _: None)
        self.assertEqual(seen, [4.1, 4.6, 5.1, 5.6, 6.1, 6.6, 7.1, 7.6, 8.1])
        self.assertEqual(result["stopped"], "8p1_limit_with_misses")
        self.assertIsNone(result["first_perfect_dwell"])

    def test_stops_at_first_complete_success(self):
        seen = []
        result = dwell_levels(lambda d: seen.append(d) or capture(d == 5.1), lambda _: None)
        self.assertEqual(seen, [4.1, 4.6, 5.1])
        self.assertEqual(result["first_perfect_dwell"], 5.1)

    def test_fixture_error_does_not_advance(self):
        seen = []
        with self.assertRaises(RuntimeError):
            dwell_levels(lambda d: seen.append(d) or dict(complete=False), lambda _: None)
        self.assertEqual(seen, [4.1])

    def test_claimed_success_requires_every_channel(self):
        result = capture(True)
        result["levels"][0]["per_channel"][0] = dict(attempted=100, received=99)
        with self.assertRaises(RuntimeError):
            dwell_levels(lambda _: result, lambda _: None)


if __name__ == "__main__":
    unittest.main()
