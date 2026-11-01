"""Offline soft-deadline DATA gating, including raw/DEFLATE and resume scope."""
import struct
import unittest

from ota_block_drain import BlockDrainError, BlockDrainSeeder
from test_lora_ota import firmware, mota_blob


def package():
    return mota_blob(firmware(b"A" * 2048 + b"B" * 2048 + b"C" * 2048, 0x01120001),
                     version=0x01120001, block_size_log2=11)


def raw(gate, block, offset=0, length=16):
    return gate.request(3, struct.pack("<BIH", 0,
        gate.info.payload_offset + block * gate.info.block_size + offset, length))


class BlockDrainTests(unittest.TestCase):
    def gate(self):
        now = [0]
        gate = BlockDrainSeeder(package(), clock=lambda: now[0])
        return gate, now

    def test_closed_baseline_blocks_data_but_not_manifest_or_proofs(self):
        gate, _ = self.gate()
        self.assertEqual(raw(gate, 0), (1, b""))
        self.assertEqual(gate.request(3, struct.pack("<BIH", 0, 0, 8))[0], 0)
        end = gate.info.payload_offset + gate.info.payload_size
        if end < len(gate.delegate.blob):
            self.assertEqual(gate.request(3, struct.pack("<BIH", 0, end, 1))[0], 0)
        with self.assertRaises(BlockDrainError): gate.arm(0, fresh_resume=False)
        self.assertEqual(gate.admitted, set())

    def test_deadline_itself_stops_new_indices_but_keeps_started_block_retries(self):
        gate, now = self.gate()
        gate.arm(0, fresh_resume=True, deadline=10)
        self.assertEqual(raw(gate, 0)[0], 0)
        now[0] = 10
        self.assertEqual(raw(gate, 1), (1, b""))
        self.assertEqual(gate.state, "draining")
        self.assertEqual(gate.required_blocks, 1)
        self.assertEqual(raw(gate, 0, 100, 50)[0], 0)
        self.assertFalse(gate.observe_verified(0))
        self.assertTrue(gate.observe_verified(1))
        gate.close()
        self.assertEqual(raw(gate, 0), (1, b""))

    def test_noncontiguous_two_block_flight_requires_both_verified(self):
        gate, _ = self.gate()
        gate.arm(2, fresh_resume=True)
        self.assertEqual(raw(gate, 3)[0], 0)
        self.assertEqual(raw(gate, 0)[0], 0)
        self.assertEqual(gate.freeze(), 4)
        self.assertFalse(gate.observe_verified(3))
        self.assertTrue(gate.observe_verified(4))
        self.assertEqual(gate.admitted, {0, 3})

    def test_deflate_size_probe_and_invalid_ranges_do_not_admit(self):
        gate, _ = self.gate()
        gate.arm(0, fresh_resume=True)
        for block in (0, 1, 2):
            self.assertEqual(gate.request(9, struct.pack("<BHHH", 0, block, 0, 0))[0], 0)
        self.assertEqual(gate.admitted, set())
        self.assertEqual(gate.request(9, struct.pack("<BHHH", 0, 0, 60000, 10)), (1, b""))
        self.assertEqual(gate.admitted, set())
        self.assertEqual(gate.request(9, struct.pack("<BHHH", 0, 1, 0, 4))[0], 0)
        gate.freeze()
        self.assertEqual(raw(gate, 0), (1, b""))
        self.assertEqual(gate.request(9, struct.pack("<BHHH", 0, 1, 4, 4))[0], 0)

    def test_cross_block_read_is_rejected_atomically(self):
        gate, _ = self.gate()
        gate.arm(0, fresh_resume=True)
        self.assertEqual(raw(gate, 0, 2040, 20), (1, b""))
        self.assertEqual(gate.admitted, set())

    def test_admission_is_journaled_before_data_is_returned(self):
        events = []
        gate = BlockDrainSeeder(package(), on_change=lambda state: events.append(state))
        gate.arm(0, fresh_resume=True)
        self.assertEqual(raw(gate, 2)[0], 0)
        self.assertEqual(events[-1]["admitted_blocks"], [2])
        gate.on_change = lambda state: (_ for _ in ()).throw(OSError("journal failed"))
        with self.assertRaisesRegex(OSError, "journal failed"): raw(gate, 0)
        self.assertEqual(gate.state, "failed")
        self.assertEqual(raw(gate, 0), (1, b""))

    def test_owned_restart_keeps_all_indices_and_existing_drain(self):
        gate, _ = self.gate()
        gate.arm(1, fresh_resume=True)
        raw(gate, 0)
        raw(gate, 2)
        gate.observe_verified(2)
        gate.freeze()
        restored, _ = self.gate()
        restored.restore_owned(gate.snapshot(), deadline=100)
        self.assertEqual(restored.state, "draining")
        self.assertEqual(raw(restored, 3), (1, b""))
        self.assertEqual(raw(restored, 0)[0], 0)
        self.assertTrue(restored.observe_verified(3))

    def test_unknown_progress_or_corrupt_journal_cannot_claim_a_drain(self):
        gate, _ = self.gate()
        gate.arm(1, fresh_resume=True)
        raw(gate, 0)
        gate.freeze()
        for count in (0, 3, None):
            with self.subTest(count=count), self.assertRaises(BlockDrainError): gate.observe_verified(count)
        for patch in ({"admitted_blocks": [0, 0]}, {"admitted_blocks": [999]},
                      {"baseline_blocks": -1}, {"last_verified_blocks": 4}):
            restored, _ = self.gate()
            snapshot = dict(gate.snapshot(), **patch)
            with self.assertRaises(BlockDrainError): restored.restore_owned(snapshot)


if __name__ == "__main__": unittest.main()
