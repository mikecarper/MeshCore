#!/usr/bin/env python3
"""Offline tests for fem_noise_floor_test.py."""

from __future__ import annotations

import struct
import unittest

import esp32_companion_serial_stress as companion
import fem_noise_floor_test as hil


class FakeTransport:
    def __init__(self, original=True, off=None, on=None, fail_at=0):
        self.state = original
        self.original = original
        self.readings = {
            False: iter(off or (-112, -111, -113)),
            True: iter(on or (-99, -98, -100)),
        }
        self.fail_at = fail_at
        self.stats_calls = 0
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def identify(self):
        return {"manufacturer": "Heltec T096", "firmware": "test"}

    def get_enabled(self):
        return self.state

    def set_enabled(self, enabled):
        self.state = enabled

    def radio_stats(self):
        self.stats_calls += 1
        if self.stats_calls == self.fail_at:
            raise hil.FemTestError("injected stats failure")
        return {"noise_floor_dbm": next(self.readings[self.state])}

    def transport_summary(self):
        return {"fake": True}


def fast_config(**overrides):
    values = {
        "port": "FAKE",
        "cycles": 3,
        "settle_seconds": 0,
        "min_gain_db": 3,
        "expected_manufacturer": "T096",
    }
    values.update(overrides)
    return hil.FemTestConfig(**values)


class PayloadValidationTest(unittest.TestCase):
    def test_fem_get_and_set_payloads(self):
        self.assertTrue(hil.validate_fem_get_response(b"\x00\x01")["enabled"])
        self.assertFalse(hil.validate_fem_get_response(b"\x00\x00")["enabled"])
        self.assertTrue(hil.validate_fem_set_response(b"\x00")["accepted"])
        with self.assertRaises(companion.ProtocolError):
            hil.validate_fem_get_response(b"\x00\x02")
        with self.assertRaises(companion.ProtocolError):
            hil.validate_fem_set_response(b"\x00\x01")

    def test_radio_stats_payload(self):
        payload = struct.pack(
            "<BBhbbII", companion.RESP_CODE_STATS, 1,
            -108, -72, -21, 123, 456,
        )
        parsed = hil.validate_radio_stats_response(payload)
        self.assertEqual(parsed["noise_floor_dbm"], -108)
        self.assertEqual(parsed["last_snr_db"], -5.25)
        self.assertEqual(parsed["rx_air_seconds"], 456)
        with self.assertRaises(companion.ProtocolError):
            hil.validate_radio_stats_response(payload[:-1])


class FemExerciseTest(unittest.TestCase):
    def test_paired_gain_passes_and_restores_original_state(self):
        transport = FakeTransport(original=True)
        result = hil.run_fem_test(
            fast_config(), transport=transport, sleep=lambda _: None
        )
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["aggregate"]["median_on_minus_off_db"], 13)
        self.assertEqual(len(result["cycles"]), 3)
        self.assertEqual(result["restore"]["state"], "on")
        self.assertTrue(transport.state)
        self.assertTrue(transport.closed)

    def test_original_off_is_primed_and_restored_off(self):
        transport = FakeTransport(original=False)
        result = hil.run_fem_test(
            fast_config(cycles=1), transport=transport, sleep=lambda _: None
        )
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["original_fem_rx_gain"], "off")
        self.assertEqual(result["restore"]["state"], "off")
        self.assertFalse(transport.state)

    def test_small_noise_change_fails(self):
        transport = FakeTransport(
            off=(-110, -110, -110), on=(-109, -108, -109)
        )
        result = hil.run_fem_test(
            fast_config(), transport=transport, sleep=lambda _: None
        )
        self.assertFalse(result["ok"])
        self.assertIn("did not meet", result["failure"]["message"])
        self.assertTrue(result["restore"]["ok"])

    def test_mid_test_failure_still_restores_and_closes(self):
        transport = FakeTransport(original=True, fail_at=1)
        result = hil.run_fem_test(
            fast_config(), transport=transport, sleep=lambda _: None
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["type"], "FemTestError")
        self.assertEqual(result["failure"]["phase"], "cycle-1-off")
        self.assertTrue(transport.state)
        self.assertTrue(result["restore"]["ok"])
        self.assertTrue(transport.closed)

    def test_manufacturer_guard_fails_before_any_toggle(self):
        transport = FakeTransport(original=True)
        result = hil.run_fem_test(
            fast_config(expected_manufacturer="V4"),
            transport=transport,
            sleep=lambda _: None,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["phase"], "identify")
        self.assertEqual(transport.state, transport.original)
        self.assertTrue(transport.closed)


if __name__ == "__main__":
    unittest.main()
