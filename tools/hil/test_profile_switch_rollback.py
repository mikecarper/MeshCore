import unittest
from profile_switch_rollback import settling_budget, check_timing, CASES, PREAMBLE_US, DWELL_US


class RollbackTests(unittest.TestCase):
    def test_full_retune_repeat_requires_two_real_passes(self):
        case=dict(rollback=0,cache=False,repeat=True,retune_passes=2)
        status=dict(switch=dict(n=100),failures=0,rx_mode_errors=0,cache_errors=0,rx_errors=0,
                    rollback=0,rx_gain_reg=148,settle_us=0,frequency_repeat=True,
                    optimized_rx_resumes=200,rf_commands=400,modulation_commands=200,
                    warm_standby=True,batched_modulation=True,bulk_spi=True,retune_passes=2,
                    first_pass=dict(n=100),second_pass=dict(n=100),second_pass_blocked=0)
        check_timing(status,case,0)
        detour_case=dict(case,first_pass_detour=True)
        detour_status=dict(status,first_pass_detour=True,detour=dict(n=100,bad=0))
        check_timing(detour_status,detour_case,0)
        for detail in (dict(n=99,bad=0),dict(n=100,bad=1)):
            with self.assertRaises(RuntimeError):
                check_timing(dict(detour_status,detour=detail),detour_case,0)
        for key,value in (('rf_commands',200),('modulation_commands',100),
                          ('optimized_rx_resumes',100),('second_pass',dict(n=0)),
                          ('second_pass_blocked',1),('retune_passes',1)):
            with self.assertRaises(RuntimeError): check_timing(dict(status,**{key:value}),case,0)

    def test_maximum_delay_retains_measured_switch_and_reserve(self):
        for switch in (800, 1200, 8000):
            delay = settling_budget(switch)
            self.assertEqual(delay % 100, 0)
            self.assertLessEqual(4*(DWELL_US+switch+delay+500), PREAMBLE_US)
            self.assertGreater(4*(DWELL_US+switch+delay+600), PREAMBLE_US)
        with self.assertRaises(RuntimeError):
            settling_budget(24000)

    def test_observed_commands_must_match_each_rollback(self):
        for case in CASES:
            bits = case['rollback']
            fast = bits & 9 == 0
            mods = 3 if bits & 2 else 0 if fast and case['cache'] else 1
            status = dict(switch=dict(n=100), failures=0, rx_mode_errors=0, cache_errors=0,
                          rx_errors=0, rollback=bits, rx_gain_reg=148, settle_us=0,
                          frequency_repeat=case['repeat'], optimized_rx_resumes=int(fast)*100,
                          rf_commands=100*(2 if case['repeat'] else 1), modulation_commands=100*mods,
                          warm_standby=bits & 8 == 0, batched_modulation=bits & 2 == 0,
                          bulk_spi=bits & 4 == 0)
            status['tcxo_us']=case.get('tcxo_us',1600)
            check_timing(status, case, 0)
            with self.assertRaises(RuntimeError):
                check_timing(dict(status, modulation_commands=-1), case, 0)
            with self.assertRaises(RuntimeError):
                check_timing(dict(status, rf_commands=-1), case, 0)


if __name__ == '__main__':
    unittest.main()
