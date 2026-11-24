"""Deterministic load receipts and causal guards around the production scanner."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("radio_profile_load", ROOT / "tools/sim/radio_profile_load.py")
load = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = load
SPEC.loader.exec_module(load)


class ProfileTrafficLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="radio2-load-test-")
        cls.addClassCleanup(cls.folder.cleanup)
        cls.native = load.NativeSimulation(cls.folder.name, os.environ.get("RADIO2_LOAD_SANITIZE") == "1")

    def control(self, name, profiles=load.PAIRS[0], duration=1_000_000, dual=True):
        return load.Scenario(name, 0, profiles, duration, dual,
                             services=list(range(250, duration + 1, 250)))

    def check_receipt(self, receipt):
        frames = receipt["frames"]
        self.assertEqual(receipt["offered"], len(frames))
        self.assertEqual(receipt["scanner_received"], sum(f["scanner_received"] for f in frames))
        self.assertEqual(receipt["scanner_lost"] + receipt["scanner_received"], len(frames))
        self.assertEqual(receipt["fixed_continuous_received"] + receipt["collision_frames"], len(frames))
        self.assertEqual(receipt["raw_modeled_locks"], sum(f["acquired_us"] is not None for f in frames))
        self.assertEqual(receipt["raw_modeled_locks"],
                         receipt["scanner_received"] + receipt["raw_modeled_crc_failures"])
        self.assertFalse(receipt["model"]["zero_loss_guarantee"])
        self.assertLessEqual(receipt["scanner_received"], receipt["fixed_same_tx_received"])
        self.assertLessEqual(receipt["fixed_same_tx_received"], receipt["fixed_continuous_received"])
        for frame in frames:
            if frame["scanner_received"]:
                self.assertIsNotNone(frame["acquired_us"])
                self.assertLessEqual(frame["arrival_us"], frame["acquired_us"])
                self.assertLessEqual(frame["acquired_us"], frame["preamble_end_us"])
                self.assertFalse(frame["collision"])
                self.assertTrue(frame["fixed_same_tx_received"])
                for start, end in receipt["blackouts"]:
                    self.assertFalse(start < frame["end_us"] and end > frame["acquired_us"])

    def test_seeded_mixed_load_replays_and_records_real_loss(self):
        masks = 0
        guards = {"packet": 0, "pending_irq": 0, "own_tx": 0}
        for pair in range(len(load.PAIRS)):
            for seed in (7, 20261124):
                with self.subTest(pair=pair, seed=seed):
                    scenario = load.randomized_scenario(seed, pair)
                    receipt = self.native.run(scenario)
                    self.check_receipt(receipt)
                    self.assertEqual(receipt, self.native.run(scenario))
                    self.assertGreater(receipt["scanner_received"], 0)
                    self.assertGreater(receipt["scanner_lost"], 0)
                    self.assertGreater(receipt["switches"], 0)
                    self.assertGreater(receipt["tx_admitted"], 0)
                    self.assertGreater(receipt["fixed_continuous_received"], receipt["scanner_received"])
                    self.assertTrue(any(f["payload_bytes"] == 255 for f in receipt["frames"]))
                    self.assertTrue(any(f["preamble_symbols"] == 8 for f in receipt["frames"]))
                    for frame in receipt["frames"]:
                        masks |= frame["exposure_mask"]
                    for kind in guards:
                        guards[kind] += receipt["guard_checks"][kind]
        self.assertEqual(masks & 255, 255)
        self.assertTrue(all(value > 0 for value in guards.values()))

    def test_different_seed_changes_the_workload(self):
        first = load.randomized_scenario(7)
        replay = load.randomized_scenario(7)
        second = load.randomized_scenario(8)
        self.assertEqual(first.native_input(), replay.native_input())
        self.assertNotEqual(first.frames, second.frames)
        self.assertNotEqual(first.delays, second.delays)

    def test_partial_preamble_windows_do_not_add_up_to_a_raw_lock(self):
        scenario = self.control("partial-preamble", profiles=((10, 125, 5), (7, 500, 5)))
        scenario.profile_preambles = (16, 0)
        scenario.frames = [(20_000, 0, 12, 8)]
        receipt = self.native.run(scenario)
        self.check_receipt(receipt)
        self.assertEqual(receipt["fixed_continuous_received"], 1)
        self.assertEqual(receipt["raw_modeled_locks"], 0)
        self.assertEqual(receipt["scanner_received"], 0)
        self.assertGreaterEqual(receipt["partial_preamble_windows"], 2)
        self.assertGreater(receipt["partial_preamble_listen_us"], 4 * 8192)
        self.assertLess(receipt["max_partial_preamble_listen_us"], 4 * 8192)

    def test_cpu_and_ui_gaps_causally_lose_short_preambles(self):
        for kind, bit in ((1, 32), (2, 64)):
            with self.subTest(kind=kind):
                scenario = self.control("service-gap", profiles=((10, 125, 5), (7, 500, 5)))
                scenario.frames = [(50_000, 1, 12, 8)]
                reference = self.native.run(scenario)
                self.assertEqual(reference["raw_modeled_locks"], 1)
                self.assertEqual(reference["scanner_received"], 1)
                scenario.services = [250, 500, 750, 1000, 1250, 1500, 1750, 2000, 100_000]
                scenario.delays = [(2001, 99_999, kind)]
                stalled = self.native.run(scenario)
                self.check_receipt(stalled)
                self.assertEqual(stalled["raw_modeled_locks"], 0)
                self.assertEqual(stalled["scanner_received"], 0)
                self.assertEqual(stalled["fixed_continuous_received"], 1)
                self.assertTrue(stalled["frames"][0]["exposure_mask"] & bit)
                scenario.frames = [(50_000, 1, 12, 512)]
                long_preamble = self.native.run(scenario)
                self.check_receipt(long_preamble)
                self.assertEqual(long_preamble["raw_modeled_locks"], 1)
                self.assertEqual(long_preamble["scanner_received"], 1)

    def test_empty_short_load_is_valid(self):
        scenario = self.control("empty", duration=100)
        receipt = self.native.run(scenario)
        self.check_receipt(receipt)
        self.assertEqual(receipt["offered"], 0)
        self.assertEqual(receipt["raw_modeled_locks"], 0)

    def test_isolated_fixed_primary_control(self):
        scenario = self.control("fixed-primary", dual=False)
        scenario.frames = [(1000, 0, 12, 8), (200_000, 0, 255, 32), (600_000, 0, 64, 16)]
        receipt = self.native.run(scenario)
        self.check_receipt(receipt)
        self.assertEqual(receipt["scanner_received"], 3)
        self.assertEqual(receipt["fixed_continuous_received"], 3)
        self.assertEqual(receipt["switches"], 0)

    def test_long_packet_holds_profile_and_loses_other_short_preamble(self):
        scenario = self.control("long-packet-hold")
        scenario.frames = [(0, 0, 255, 32), (20_000, 1, 12, 8)]
        receipt = self.native.run(scenario)
        self.check_receipt(receipt)
        self.assertTrue(receipt["frames"][0]["scanner_received"])
        self.assertFalse(receipt["frames"][1]["scanner_received"])
        self.assertEqual(receipt["fixed_continuous_received"], 2)
        self.assertTrue(receipt["frames"][1]["exposure_mask"] & 4)
        self.assertGreater(receipt["guard_checks"]["packet"], 0)
        self.assertGreater(receipt["max_packet_hold_us"], 90_000)

    def test_completion_fifo_survives_storage_delay(self):
        scenario = self.control("fifo-storage")
        scenario.frames = [(0, 0, 12, 32), (40_000, 1, 12, 8)]
        scenario.services = [250, 500, 750, 1000, 1250, 1500, 1750, 2000, 100_000, 101_000]
        scenario.delays = [(2001, 99_999, 3)]
        receipt = self.native.run(scenario)
        self.check_receipt(receipt)
        self.assertTrue(receipt["frames"][0]["scanner_received"])
        self.assertFalse(receipt["frames"][1]["scanner_received"])
        self.assertTrue(receipt["frames"][1]["exposure_mask"] & 8)
        self.assertTrue(receipt["frames"][1]["exposure_mask"] & 128)
        self.assertGreater(receipt["guard_checks"]["pending_irq"], 0)

    def test_own_tx_blacks_out_short_packet_but_can_acquire_long_tail(self):
        scenario = self.control("own-tx", dual=False)
        scenario.frames = [(20_000, 0, 12, 8), (150_000, 0, 12, 512)]
        scenario.transmissions = [(10_000, 80_000, 0), (140_000, 40_000, 0)]
        receipt = self.native.run(scenario)
        self.check_receipt(receipt)
        self.assertEqual(receipt["fixed_continuous_received"], 2)
        self.assertEqual(receipt["fixed_same_tx_received"], 1)
        self.assertEqual(receipt["scanner_received"], 1)
        self.assertTrue(receipt["frames"][0]["exposure_mask"] & 16)
        self.assertTrue(receipt["frames"][1]["scanner_received"])
        self.assertGreater(receipt["guard_checks"]["own_tx"], 0)

    def test_collision_is_recorded_and_does_not_stop_expectations(self):
        scenario = self.control("collision-continue", dual=False)
        scenario.frames = [(1000, 0, 255, 32), (20_000, 0, 12, 8), (500_000, 0, 12, 32)]
        receipt = self.native.run(scenario)
        self.check_receipt(receipt)
        self.assertEqual(receipt["collision_frames"], 2)
        self.assertEqual(receipt["scanner_lost"], 2)
        self.assertEqual(receipt["scanner_received"], 1)
        self.assertEqual(receipt["raw_modeled_locks"], 2)
        self.assertEqual(receipt["raw_modeled_crc_failures"], 1)
        self.assertTrue(receipt["frames"][2]["scanner_received"])
        self.assertEqual([frame["loss"] for frame in receipt["frames"]], ["collision", "collision", "none"])


if __name__ == "__main__":
    unittest.main()
