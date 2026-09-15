import unittest
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from profile_switch_settling import settling_plan, settling_levels, reusable_capture


def capture(delay, perfect=False):
    return dict(complete=True, settle_us=delay, sf=6, bw_khz=125, dwell_us=2612, preamble_symbols=32,
                stopped="channel_limit_without_miss" if perfect else "first_rf_miss",
                levels=[dict(channels=4, received=400 if perfect else 0,
                             attempted=400 if perfect else 1, status={},
                             per_channel=[dict(attempted=100, received=100)]*4 if perfect else [])])


class SettlingTests(unittest.TestCase):
    def test_sf10_double_write_one_ms_grid_and_first_pass_stop(self):
        plan=settling_plan(base_switch_us=539,sf=10,step_us=1000,frequency_repeat=True)
        self.assertEqual(plan['maximum_added_us'],23217)
        self.assertEqual(plan['delays_us'],list(range(0,23001,1000)))
        seen=[]
        def run(delay):
            seen.append(delay)
            reply=capture(delay,delay==2000)
            reply.update(sf=10,dwell_us=41780,frequency_repeat=True)
            return reply
        result=settling_levels(run,lambda _:None,plan)
        self.assertEqual(seen,[0,1000,2000])
        self.assertEqual(result['stopped'],'first_400_of_400_pass')
        self.assertEqual(result['first_perfect_settle_us'],2000)

    def test_double_write_plan_rejects_single_write_evidence(self):
        plan=settling_plan(sf=6,frequency_repeat=True)
        with self.assertRaises(RuntimeError):
            settling_levels(lambda d:capture(d),lambda _:None,plan)

    def test_payload_delivery_diagnostic_cannot_be_a_strict_pass(self):
        reply = capture(0, perfect=True)
        reply["accept_offchannel"] = True
        with self.assertRaises(RuntimeError):
            settling_levels(lambda _: reply, lambda _: None, settling_plan())
        reply["accept_offchannel"] = False
        reply["mixed_profiles"] = True
        with self.assertRaises(RuntimeError):
            settling_levels(lambda _: reply, lambda _: None, settling_plan())

    def test_reuse_requires_complete_matching_configuration_and_hashes_evidence(self):
        reply=dict(complete=True, receiver="RX", sender="TX", sf=10, settle_us=100,
                   bw_khz=125, cr=5, dwell_symbols=5.1, preamble_symbols=32,
                   samples_per_channel=100, expected_rx_gain_reg=148, modulation_cache=True,
                   trace_enabled=False, irq_poll_us=0, packet_bytes=16, power_dbm=-9,
                   base_mhz=909.5, channel_step_mhz=0.25, seed=606125)
        raw=json.dumps(reply).encode()
        with patch.object(Path,"read_bytes",return_value=raw):
            got,digest=reusable_capture(Path("source.json"),"RX","TX",10,100)
            self.assertEqual(got,reply)
            self.assertEqual(digest,hashlib.sha256(raw).hexdigest())
            with self.assertRaises(RuntimeError):
                reusable_capture(Path("source.json"),"RX","TX",9,100)
        reply["accept_offchannel"] = True
        with patch.object(Path,"read_bytes",return_value=json.dumps(reply).encode()):
            with self.assertRaises(RuntimeError):
                reusable_capture(Path("source.json"),"RX","TX",10,100)
        reply["accept_offchannel"] = False
        reply["complete"]=False
        with patch.object(Path,"read_bytes",return_value=json.dumps(reply).encode()):
            with self.assertRaises(RuntimeError):
                reusable_capture(Path("source.json"),"RX","TX",10,100)

    def test_budget_scales_with_sf_but_switch_cost_does_not(self):
        plans=[settling_plan(sf=sf) for sf in range(10,4,-1)]
        self.assertEqual([p['maximum_added_us'] for p in plans],[23303,11425,5486,2516,1031,289])
        self.assertEqual([p['delays_us'][-1] for p in plans],[23300,11400,5400,2500,1000,200])
        self.assertEqual([p['nominal_switch_us'] for p in plans],[453]*6)

    def test_upper_limit_continues_after_pass_and_stops_at_next_failure(self):
        seen=[]
        result=settling_levels(lambda d: seen.append(d) or capture(d,d in (100,200)),
                               lambda _:None,settling_plan(),True)
        self.assertEqual(seen,[0,100,200,300])
        self.assertEqual(result['first_perfect_settle_us'],100)
        self.assertEqual(result['last_perfect_settle_us'],200)
        self.assertEqual(result['first_failed_above_pass_us'],300)
        self.assertEqual(result['stopped'],'first_failure_above_pass')

    def test_upper_limit_is_censored_when_every_step_passes(self):
        result=settling_levels(lambda d:capture(d,True),lambda _:None,settling_plan(),True)
        self.assertEqual(len(result['levels']),11)
        self.assertEqual(result['last_perfect_settle_us'],1000)
        self.assertEqual(result['stopped'],'nominal_timing_limit_after_pass')
    def test_full_cycle_budget_and_grid(self):
        plan = settling_plan()
        self.assertEqual(plan["maximum_added_us"], 1031)
        self.assertEqual(plan["delays_us"], list(range(0, 1001, 100)))
        self.assertLessEqual(4*(2612+453+1000), plan["preamble_us"])
        self.assertGreater(4*(2612+453+1100), plan["preamble_us"])

    def test_stop_at_first_400_pass(self):
        seen=[]
        result=settling_levels(lambda d: seen.append(d) or capture(d,d==200),lambda _:None,settling_plan())
        self.assertEqual(seen,[0,100,200])
        self.assertEqual(result["first_perfect_settle_us"],200)

    def test_all_failures_stop_at_limit(self):
        seen=[]
        result=settling_levels(lambda d: seen.append(d) or capture(d),lambda _:None,settling_plan())
        self.assertEqual(seen,list(range(0,1001,100)))
        self.assertEqual(result["stopped"],"nominal_timing_limit_with_misses")

    def test_fixture_failure_or_wrong_policy_never_advances(self):
        repeated=capture(0)
        repeated["frequency_repeat"]=True
        for reply in (dict(complete=False),capture(100),repeated):
            seen=[]
            with self.assertRaises(RuntimeError):
                settling_levels(lambda d: seen.append(d) or reply,lambda _:None,settling_plan())
            self.assertEqual(seen,[0])

    def test_incomplete_success_rejected(self):
        reply=capture(0,True)
        reply["levels"][0]["per_channel"][0]=dict(attempted=100,received=99)
        with self.assertRaises(RuntimeError):
            settling_levels(lambda _:reply,lambda _:None,settling_plan())


if __name__ == "__main__":
    unittest.main()
