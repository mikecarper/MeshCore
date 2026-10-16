#!/usr/bin/env python3
"""Exercise the production RX ring and callback with task preemption hooks."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


class Nrf52BleRxQueueTests(unittest.TestCase):
    def test_session_identity_is_observed_before_deferred_lifecycle_callbacks(self):
        source = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text()
        events = method(source, "void SerialBLEInterface::onBLEEvent(")
        self.assertIn("recv_queue.connect(evt->evt.gap_evt.conn_handle)", events)
        self.assertIn("recv_queue.disconnect(evt->evt.gap_evt.conn_handle)", events)
        for signature in ("void SerialBLEInterface::onConnect(",
                          "void SerialBLEInterface::onDisconnect("):
            lifecycle = method(source, signature)
            self.assertNotIn("recv_queue.connect(", lifecycle)
            self.assertNotIn("recv_queue.disconnect(", lifecycle)

    def test_production_queue_and_callback_interleavings_under_sanitizers(self):
        source = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text()
        signatures = ["void SerialBLEInterface::clearBuffers()",
                      "static bool isBondAuthenticationFailure(",
                      "void SerialBLEInterface::onBLEEvent(",
                      "void SerialBLEInterface::onConnect(",
                      "void SerialBLEInterface::onDisconnect(",
                      "void SerialBLEInterface::onBleUartRX(",
                      "bool SerialBLEInterface::isReadBusy() const",
                      "bool SerialBLEInterface::hasPendingIO() const"]
        methods = "\n".join(method(source, signature) for signature in signatures)
        harness = (ROOT / "test/fixtures/nrf52_ble_rx_queue.cpp").read_text()
        sanitizers = [] if os.name == "nt" else [
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="meshcore-nrf-ble-rx-") as temp:
            binary = Path(temp) / "rx_queue"
            built = subprocess.run([
                "c++", "-std=c++17", "-x", "c++", "-", "-I" + str(ROOT / "src"),
                *sanitizers, "-o", str(binary)],
                input=harness.replace("@METHODS@", methods),
                text=True, capture_output=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], text=True,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
