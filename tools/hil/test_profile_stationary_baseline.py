import unittest
from profile_stationary_baseline import check_status


class BaselineStatusTests(unittest.TestCase):
    def test_rejects_retune_hardware_and_count_mismatches(self):
        status = dict(stationary=True, active=True, rf_commands=0, rx_gain_reg=0x94,
                      rx_mode=1, device_errors=0, failures=0, received=100, missed=0,
                      rf_word=int(910250*(1 << 25)/32000))
        trials = [dict(valid=True)]*100
        check_status(status, trials, 910250)
        for key, value in dict(rf_commands=1, rx_gain_reg=0x96, rx_mode=0,
                               device_errors=1, failures=1, received=99, missed=1,
                               rf_word=0, active=False, stationary=False).items():
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                check_status(dict(status, **{key: value}), trials, 910250)


if __name__ == '__main__':
    unittest.main()
