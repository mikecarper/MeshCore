"""Controller invariants for stop-on-first-miss channel-capacity experiments."""
import unittest
from unittest.mock import patch
from profile_switch_channels import capacity_levels, collect_trace, channel_dwell_us, reception_validity, packet_error_summary


class CapacityControllerTests(unittest.TestCase):
    def test_fixed_sample_finishes_all_400_without_restart_or_retries(self):
        starts=[];calls=[];finishes=[]
        def probe(ch,pause):
            calls.append(ch)
            received=dict(valid=False,timeout=True)
            if ch==0: received=dict(valid=True,len=16,channel=ch)
            if ch==1: received=dict(valid=False,payload_valid=True,len=16,channel=0,payload_channel=ch,expected_channel=ch)
            return dict(sequence=len(calls),valid=ch==0,received=received)
        result=capacity_levels(lambda n:starts.append(n),probe,lambda:finishes.append(1),lambda _:None,
                               samples=100,maximum=4,continue_on_miss=True)
        self.assertEqual(starts,[4]);self.assertEqual(finishes,[1]);self.assertEqual(len(calls),400)
        self.assertEqual([calls.count(ch) for ch in range(4)],[100]*4)
        self.assertEqual(result['stopped'],'fixed_sample_complete')
        self.assertIsNone(result['last_perfect_channels'])
        level=result['levels'][0]
        self.assertTrue(level['complete'])
        summary=level['error_rates']['overall']
        self.assertEqual((summary['strict_received'],summary['offchannel_received'],summary['timeouts']),(100,100,200))
        self.assertEqual((summary['strict_error_percent'],summary['delivery_error_percent']),(75,50))
        with self.assertRaises(ValueError):
            capacity_levels(None,None,None,None,maximum=5,continue_on_miss=True)

    def test_fixed_sample_does_not_treat_fixture_failure_as_rf_loss(self):
        calls=[]
        def probe(*_):
            calls.append(1);raise RuntimeError('hardware failure')
        with self.assertRaises(RuntimeError):
            capacity_levels(lambda _:None,probe,lambda:None,lambda _:None,maximum=4,continue_on_miss=True)
        self.assertEqual(len(calls),1)

    def test_delivery_diagnostic_does_not_relabel_strict_validity(self):
        wrong=dict(valid=False,payload_valid=True,len=16,channel=2,payload_channel=3,expected_channel=3)
        self.assertEqual(reception_validity(wrong,3),(False,False))
        self.assertEqual(reception_validity(wrong,3,True),(False,True))
        for key,value in dict(payload_valid=False,len=15,payload_channel=2,expected_channel=2).items():
            self.assertEqual(reception_validity(dict(wrong,**{key:value}),3,True),(False,False))
        self.assertEqual(reception_validity(dict(valid=False,timeout=True),3,True),(False,False))

    def test_sf8_dwell_is_four_times_sf6_before_rounding(self):
        self.assertEqual(channel_dwell_us(6, 4.8), 2458)
        self.assertEqual(channel_dwell_us(6, 5.1), 2612)
        self.assertEqual(channel_dwell_us(8, 5.1), 10445)
        self.assertEqual(channel_dwell_us(8, 32), 65536)
        for sf, dwell in ((11, 5.1), (8, float("nan")), (8, float("inf")), (6, 3.9)):
            with self.assertRaises(ValueError):
                channel_dwell_us(sf, dwell)

    def test_sf10_to_sf5_dwell_grid(self):
        self.assertEqual([channel_dwell_us(sf, 5.1) for sf in range(10,4,-1)],
                         [41780,20890,10445,5223,2612,1306])

    def test_arrival_phase_range_includes_added_settling(self):
        pauses=[]
        capacity_levels(lambda _: {}, lambda ch,pause: pauses.append(pause) or dict(valid=True),
                        lambda: {}, lambda _: None, samples=100, maximum=4,
                        dwell_us=41780, settle_us=23300)
        self.assertGreater(max(pauses), 2*4*(41.780+.7))
        self.assertLessEqual(max(pauses), 2*4*(41.780+23.300+.7))

    def test_sf5_250_dwell_grid_and_bandwidth_validation(self):
        self.assertEqual([channel_dwell_us(5, t / 10, 250) for t in range(41, 82, 5)],
                         [525, 589, 653, 717, 781, 845, 909, 973, 1037])
        with self.assertRaises(ValueError):
            channel_dwell_us(5, 4.1, 500)

    def test_starts_at_four_balances_samples_then_stops_without_another_tx(self):
        starts, probes = [], []
        def start(n):
            starts.append(n)
            return {"channels": n}
        def probe(channel, pause):
            probes.append((starts[-1], channel, pause))
            return {"sequence": len(probes), "valid": len(probes) != 10}
        result = capacity_levels(start, probe, lambda: {}, lambda _: None, samples=2, maximum=8)
        self.assertEqual(starts, [4, 5])
        self.assertEqual(len(probes), 10)
        self.assertEqual(result["last_perfect_channels"], 4)
        self.assertEqual(result["first_failed_channels"], 5)
        self.assertTrue(result["complete"])
        self.assertEqual(result["levels"][0]["per_channel"], [{"attempted": 2, "received": 2}] * 4)
        self.assertEqual(result["levels"][1]["attempted"], 2)
        self.assertTrue(all(pause >= 0 for _, _, pause in probes))

    def test_first_packet_miss_at_four_does_not_try_fewer_or_more_channels(self):
        starts = []
        result = capacity_levels(lambda n: starts.append(n),
                                 lambda *_: {"sequence": 1, "valid": False},
                                 lambda: {}, lambda _: None)
        self.assertEqual(starts, [4])
        self.assertIsNone(result["last_perfect_channels"])
        self.assertEqual(result["first_failed_channels"], 4)

    def test_fixture_failure_does_not_expand_or_become_an_rf_miss(self):
        starts = []
        def probe(*_):
            raise TimeoutError("USB result unavailable")
        with self.assertRaises(TimeoutError):
            capacity_levels(lambda n: starts.append(n), probe, lambda: {}, lambda _: None)
        self.assertEqual(starts, [4])

    def test_fixed_four_channel_diagnostic_uses_requested_dwell(self):
        pauses = []
        result = capacity_levels(lambda n: n,
                                 lambda ch, pause: pauses.append(pause) or {"sequence": 1, "valid": True},
                                 lambda: {}, lambda _: None, samples=1, maximum=4, dwell_us=2612)
        self.assertEqual(result["last_perfect_channels"], 4)
        self.assertEqual(len(pauses), 4)
        self.assertTrue(all(0 <= p <= 2 * 4 * (2.612 + 0.7) for p in pauses))

    def test_collect_trace_checks_page_identity(self):
        common = {"result": 42, "trace": 42, "total": 2, "origin_us": 1000}
        with patch("profile_switch_channels.exchange", side_effect=[
            dict(common, offset=0, next=1, events=[[0, 1, 0, 0, 3, 0]]),
            dict(common, offset=1, next=2, events=[[100, 2, 0, 4, 0, 0]])]):
            self.assertEqual(len(collect_trace(None, 42)["events"]), 2)
        with patch("profile_switch_channels.exchange", return_value=dict(common, offset=1, next=1, events=[])):
            with self.assertRaises(RuntimeError):
                collect_trace(None, 42)


if __name__ == "__main__":
    unittest.main()
