#!/usr/bin/env python3
"""Run actual BLE TX methods across callback resets and connection changes."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
LEGACY_CLEAR = r'''
void SerialBLEInterface::clearBuffers() {
  send_queue_len = 0;
  _last_retry_attempt = 0;
  _tx_stall_watchdog.reset();
  _tx_disconnect_recovery.complete();
  recv_queue.reset([this]() { bleuart.flush(); });
}
'''
LEGACY_ENQUEUE = r'''
size_t SerialBLEInterface::writeFrame(const uint8_t src[], size_t len) {
  if (len > MAX_FRAME_SIZE) return 0;
  if (isConnected() && len > 0) {
    if (!mesh::enqueueCompanionFrame(send_queue, send_queue_len, FRAME_QUEUE_SIZE, src, len)) return 0;
    return len;
  }
  return 0;
}
'''
LEGACY_WRITE = r'''
size_t SerialBLEInterface::writeBleUartFrame(const Frame& frame) {
  BLEConnection* conn = Bluefruit.Connection(_conn_handle);
  if (!conn || !conn->connected() || !bleuart.notifyEnabled(_conn_handle)) return 0;
  const uint16_t mtu = conn->getMtu();
  if (mtu <= 3) return 0;
  return mesh::writeBleFrameInChunks(frame.buf, frame.len, mtu - 3,
      [this](const uint8_t* data, size_t len) { return bleuart.write(_conn_handle, data, len); });
}
'''


class Nrf52BleTxSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("BLE TX session regression requires a host C++17 compiler")
        source = (ROOT / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text()
        signatures = (
            "void SerialBLEInterface::clearBuffers()", "void SerialBLEInterface::resetTxState()",
            "bool SerialBLEInterface::servicePendingTxReset()", "void SerialBLEInterface::shiftSendQueueLeft()",
            "size_t SerialBLEInterface::writeBleUartFrame(", "bool SerialBLEInterface::hasRequiredReplyCapacity() const",
            "size_t SerialBLEInterface::writeFrame(", "size_t SerialBLEInterface::checkRecvFrame(",
            "void SerialBLEInterface::recoverStalledTx(", "void SerialBLEInterface::serviceTxRecovery(",
            "bool SerialBLEInterface::isConnected() const",
            "bool SerialBLEInterface::isWriteBusy() const", "bool SerialBLEInterface::hasPendingIO() const")
        methods = "\n".join(body(source, signature) for signature in signatures)
        variants = {"production": methods,
                    "legacy_enqueue": methods.replace(body(source, signatures[0]), LEGACY_CLEAR).replace(
                        body(source, "size_t SerialBLEInterface::writeFrame("), LEGACY_ENQUEUE),
                    "legacy_write": methods.replace(body(source, "size_t SerialBLEInterface::writeBleUartFrame("), LEGACY_WRITE)}
        fixture = (ROOT / "test/fixtures/nrf52_ble_tx_session.cpp").read_text(encoding="ascii")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-ble-tx-session-")
        cls.binaries = {}
        for name, methods in variants.items():
            directory = Path(cls.work.name)
            path, binary = directory / (name + ".cpp"), directory / name
            path.write_text(fixture.replace("@METHODS@", methods), encoding="ascii")
            result = subprocess.run([
                compiler, "-std=c++17", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I" + str(ROOT / "src"), str(path), "-o", str(binary)],
                capture_output=True, text=True, timeout=30)
            if result.returncode:
                cls.work.cleanup()
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_case(self, variant, scenario):
        return subprocess.run([str(self.binaries[variant]), scenario], capture_output=True,
                              text=True, timeout=10)

    def test_production_tx_state_is_main_owned_and_fragments_stay_in_session(self):
        result = self.run_case("production", "all")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_reset_and_chunk_paths_reproduce_the_failures(self):
        for variant, scenario, assertion in (
                ("legacy_enqueue", "enqueue", "writeFrame(interrupted"),
                ("legacy_write", "connection", "chunks.size() == 1")):
            with self.subTest(variant=variant):
                result = self.run_case(variant, scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(assertion, result.stderr)


if __name__ == "__main__":
    unittest.main()
