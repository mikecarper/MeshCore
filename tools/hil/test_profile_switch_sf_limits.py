import unittest
from profile_switch_sf_limits import sf_limits, SF_ORDER
from profile_switch_settling import settling_plan


def capture(sf, stopped="first_failure_above_pass"):
    return dict(complete=True, stopped=stopped, sf=sf, bw_khz=125, find_upper_limit=True,
                plan=settling_plan(sf=sf), first_perfect_settle_us=0,
                last_perfect_settle_us=100, first_failed_above_pass_us=200, levels=[])


class SfLimitsTests(unittest.TestCase):
    def test_first_pass_stops_entire_sweep_without_lower_sf(self):
        seen=[]
        def run(sf):
            seen.append(sf)
            reply=capture(sf, "first_400_of_400_pass" if sf==9 else "nominal_timing_limit_with_misses")
            reply["find_upper_limit"]=False
            if sf==10:
                reply["first_perfect_settle_us"]=reply["last_perfect_settle_us"]=None
            return reply
        result=sf_limits(run,lambda _:None,stop_on_first_pass=True)
        self.assertEqual(seen,[10,9])
        self.assertEqual(result["stopped"],"first_400_of_400_pass")
        self.assertEqual(result["first_perfect_sf"],9)
        self.assertTrue(result["complete"])

    def test_first_pass_mode_rejects_upper_limit_child(self):
        with self.assertRaises(RuntimeError):
            sf_limits(lambda sf:capture(sf),lambda _:None,stop_on_first_pass=True)

    def test_descends_through_every_sf_even_when_one_has_no_pass(self):
        seen=[]
        result=sf_limits(lambda sf: seen.append(sf) or capture(sf,"nominal_timing_limit_with_misses"
                                                             if sf==8 else "first_failure_above_pass"),
                         lambda _:None)
        self.assertEqual(seen,[10,9,8,7,6,5])
        self.assertTrue(result["complete"])

    def test_fixture_error_never_advances_to_next_sf(self):
        seen=[]
        with self.assertRaises(RuntimeError):
            sf_limits(lambda sf: seen.append(sf) or dict(complete=False),lambda _:None)
        self.assertEqual(seen,[10])

    def test_wrong_sf_and_first_pass_only_results_rejected(self):
        for reply in (capture(9),capture(10,"first_400_of_400_pass")):
            with self.assertRaises(RuntimeError):
                sf_limits(lambda _:reply,lambda _:None)


if __name__ == "__main__":
    unittest.main()
