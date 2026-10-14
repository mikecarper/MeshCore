#!/usr/bin/env python3
"""Exercise bounded MQTT liveness clocks without an SDK or PlatformIO build."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MqttTransportHealthTest(unittest.TestCase):
    def compile_run(self, main):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        source = ('#include <cassert>\n#include <cstring>\n#include <initializer_list>\n'
                  '#include "helpers/MQTTTransportHealth.h"\n'
                  'using namespace MQTTTransportHealth;\n') + main
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-health-") as directory:
            work = Path(directory)
            path, binary = work / "health.cpp", work / "health"
            path.write_text(source, encoding="ascii")
            result = subprocess.run(
                [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src"), str(path), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repeated_acceptance_cannot_postpone_oldest_missing_ack(self):
        self.compile_run(r'''
int main() {
  State state;
  state.acceptedQos1(1000, 7);
  for (uint32_t now = 11000; now < 121000; now += 10000) {
    state.acceptedQos1(now, 7);
    assert(state.ack_started_ms == 1000 && state.ack_snapshot == 7);
    assert(!state.ackExpired(now));
  }
  assert(state.ackExpired(121000));
}
''')

    def test_ack_advance_clears_watch_and_new_enqueue_starts_a_new_deadline(self):
        self.compile_run(r'''
int main() {
  State state;
  state.acceptedQos1(1000, 7);
  state.observeAck(7);
  assert(state.ack_pending);
  state.observeAck(8);
  assert(!state.ack_pending && !state.ackExpired(121000));
  state.acceptedQos1(300000, 8);
  assert(state.ack_started_ms == 300000 && state.ack_snapshot == 8);
  assert(!state.ackExpired(419999) && state.ackExpired(420000));
  // An ACK arriving between maintenance passes is recognized on enqueue.
  state.acceptedQos1(450000, 9);
  assert(state.ack_started_ms == 450000 && state.ack_snapshot == 9);
}
''')

    def test_immediate_ack_race_uses_snapshot_from_before_enqueue(self):
        self.compile_run(r'''
int main() {
  State state;
  const uint32_t before_enqueue = 11;
  const uint32_t after_enqueue = 12; // broker event arrived inside SDK enqueue
  state.acceptedQos1(1000, before_enqueue);
  state.observeAck(after_enqueue);
  assert(!state.ack_pending && !state.ackExpired(121000));
}
''')

    def test_zero_start_ticks_and_millis_wrap_preserve_bounded_deadlines(self):
        self.compile_run(r'''
int main() {
  for (uint32_t start : {uint32_t(0), UINT32_MAX - 60000}) {
    State state;
    state.beginAttempt(start);
    state.acceptedQos1(start, UINT32_MAX);
    assert(!state.attemptExpired(start + kAttemptTimeoutMs - 1));
    assert(state.attemptExpired(start + kAttemptTimeoutMs));
    assert(!state.ackExpired(start + kAckTimeoutMs - 1));
    assert(state.ackExpired(start + kAckTimeoutMs));
    // The acknowledgment counter may wrap too; inequality still proves progress.
    state.observeAck(0);
    assert(!state.ack_pending);
    state.completeAttempt();
    assert(!state.attemptExpired(start + kAttemptTimeoutMs));
  }
}
''')

    def test_recovery_keeps_first_reason_and_saturates_lifetime_counter(self):
        self.compile_run(r'''
int main() {
  State state;
  state.beginAttempt(1000);
  state.acceptedQos1(1000, 3);
  state.requestRecovery(RecoveryReason::TransportError);
  state.requestRecovery(RecoveryReason::AckTimeout);
  assert(state.recovery_reason == RecoveryReason::TransportError);
  state.recovered();
  assert(state.recoveries == 1 && !state.attempt_active && !state.ack_pending);
  assert(state.recovery_reason == RecoveryReason::None);
  assert(state.last_recovery_reason == RecoveryReason::TransportError);
  state.recoveries = UINT32_MAX;
  state.requestRecovery(RecoveryReason::PublishFailure);
  state.recovered();
  assert(state.recoveries == UINT32_MAX);
  assert(state.last_recovery_reason == RecoveryReason::PublishFailure);
  assert(strcmp(reasonLabel(RecoveryReason::HandshakeTimeout), "handshake") == 0);
  assert(strcmp(reasonLabel(RecoveryReason::AckTimeout), "puback") == 0);
}
''')

    def test_quiet_session_without_accepted_qos1_never_times_out(self):
        self.compile_run(r'''
int main() {
  State state;
  state.beginAttempt(1000);
  state.completeAttempt();
  for (uint32_t now = 0; now <= 7 * 24 * 60 * 60 * 1000; now += 60000) {
    state.observeAck(0);
    assert(!state.attemptExpired(now) && !state.ackExpired(now));
    assert(state.recovery_reason == RecoveryReason::None);
  }
}
''')


if __name__ == "__main__":
    unittest.main()
