"""Run real role deadlines while preserving timed sleep and interrupt idle."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <helpers/PowerManagementUtils.h>
#include <helpers/TempRadioLeaseDeadline.h>
#include <helpers/PowerSavingDiagnostics.h>
#define SENSOR_READ_INTERVAL_SECS 60
static uint32_t now_ms = 1000;
uint32_t millis() { return now_ms; }
struct Node {
  bool pending = false, queue = false, retry = false;
  uint32_t queue_ms = 0, retry_ms = 0;
  struct { bool battery_alert_enabled = false; } _prefs;
  struct { int getNumClients() const { return 0; } } acl;
  unsigned long next_flood_advert = 0, next_local_advert = 0;
  unsigned long dirty_contacts_expiry = 0, next_recent_repeater_sweep = 0;
  unsigned long next_battery_alert_check = 0, next_push = 0;
  unsigned long radio_apply_retry_at = 0, set_radio_at = 0, revert_radio_at = 0;
  uint32_t last_read_time = 100;
  struct Alert { unsigned long send_expiry = 0; } alert;
  Alert* alert_tasks[1] = {&alert};
  int num_alert_tasks = 0;
  struct Clock { uint32_t getCurrentTime() const { return 100; } } clock;
  const Clock* getRTCClock() const { return &clock; }
  bool hasPendingWork() const { return pending; }
  mesh::powerdiag::Mask pendingWorkReason() const {
    return pending ? mesh::powerdiag::mask(mesh::powerdiag::Reason::Mesh) : 0;
  }
  bool getNextQueueWakeDelay(uint32_t& out) const { out = queue_ms; return queue; }
  bool getNextRetryWakeDelay(uint32_t& out) const { out = retry_ms; return retry; }
  bool millisHasNowPassed(unsigned long at) const {
    return int32_t(now_ms - uint32_t(at)) > 0;
  }
  uint32_t limitSleepToScheduledRadioWork(uint32_t seconds) const { return seconds; }
  uint32_t limitSleepToMillisTimer(unsigned long, uint32_t) const;
  uint32_t getPowerSaveSleepMilliseconds(uint32_t) const;
  uint32_t getPowerSaveSleepSeconds(uint32_t) const;
};
@METHODS@
int main() {
  Node node;
  using Lease = mesh::TempRadioLeaseDeadline;
  assert(Lease::millisecondsUntil(10, 0) == UINT32_MAX);
  assert(Lease::millisecondsUntil(100, 100) == 0);
  assert(Lease::millisecondsUntil(101, 100) == 0);
  assert(Lease::millisecondsUntil(100, uint64_t(UINT32_MAX) + 101) == UINT32_MAX);
  for (uint64_t now : {1000ULL, 0x100000001ULL, UINT64_MAX - 100000ULL}) {
    for (uint32_t delay : {1U, 50U, 999U, 1001U, 30000U})
      assert(Lease::millisecondsUntil(now, now + delay) == delay);
  }
  assert(mesh::power::powerSaveSleepSeconds(0) == 0);
  assert(mesh::power::secondsToSleepMilliseconds(0) == 0);
  assert(mesh::power::secondsToSleepMilliseconds(30) == 30000);
  assert(mesh::power::secondsToSleepMilliseconds(4294967) == 4294967000U);
  assert(mesh::power::secondsToSleepMilliseconds(4294968) == UINT32_MAX);
  assert(mesh::power::secondsToSleepMilliseconds(UINT32_MAX) == UINT32_MAX);
  assert(mesh::power::limitSleepMilliseconds(0, 50) == 0);
  assert(mesh::power::limitSleepMilliseconds(30, 0) == 0);
  assert(mesh::power::limitSleepMilliseconds(30, 50) == 50);
  assert(mesh::power::limitSleepMilliseconds(1, 1001) == 1000);
  assert(mesh::power::limitSleepMilliseconds(UINT32_MAX, 50) == 50);
  assert(mesh::power::limitSleepMilliseconds(UINT32_MAX, UINT32_MAX) == UINT32_MAX);
#if EXPECT_INTERRUPT_IDLE
  assert(mesh::power::powerSaveSleepSeconds(UINT32_MAX) == 4294968U);
#else
  assert(mesh::power::powerSaveSleepSeconds(UINT32_MAX) == 4294967U);
#endif
  assert(node.getPowerSaveSleepSeconds(30) == 30);
  assert(node.getPowerSaveSleepSeconds(0) == 0);
  assert(node.getPowerSaveSleepMilliseconds(30000) == 30000);
  assert(node.getPowerSaveSleepMilliseconds(0) == 0);
  assert(node.limitSleepToMillisTimer(0, 30000) == 30000); // Disabled timer sentinel.
  assert(node.limitSleepToMillisTimer(1001, 0) == 0);
  node.pending = true;
  assert(node.getPowerSaveSleepSeconds(30) == 0);
  node.pending = false;
  for (uint32_t now : {1000U, UINT32_MAX - 15U}) {
    now_ms = now;
    // Timed sleep ends before the deadline. Event-driven boards instead permit
    // interrupt idle for every positive remainder, including across rollover.
    for (uint32_t ms : {0U, 1U, 999U, 1000U, 1001U, 1999U, 2000U,
                        2500U, 29999U, 30000U, 30001U, 60000U}) {
      const uint32_t whole_seconds = ms / 1000;
#if EXPECT_INTERRUPT_IDLE
      // Event-driven boards ignore seconds and wake at the next IRQ.
      const uint32_t idle_seconds = whole_seconds + (ms % 1000 != 0);
#else
      const uint32_t idle_seconds = whole_seconds;
#endif
      const uint32_t expected = idle_seconds < 30 ? idle_seconds : 30;
      const uint32_t expected_ms = ms < 30000 ? ms : 30000;
      node.queue = true; node.queue_ms = ms;
      assert(node.getPowerSaveSleepSeconds(30) == expected);
      assert(node.getPowerSaveSleepMilliseconds(30000) == expected_ms);
      node.queue = false; node.retry = true; node.retry_ms = ms;
      assert(node.getPowerSaveSleepSeconds(30) == expected);
      assert(node.getPowerSaveSleepMilliseconds(30000) == expected_ms);
      node.retry = false;
      const uint32_t deadline = now + ms;
      assert(deadline != 0); // Zero has a separate intentional sentinel meaning.
      assert(node.limitSleepToMillisTimer(deadline, 30000) == expected_ms);
      node.next_flood_advert = deadline;
      assert(node.getPowerSaveSleepSeconds(30) == expected);
      assert(node.getPowerSaveSleepMilliseconds(30000) == expected_ms);
      node.next_flood_advert = 0;
    }
    assert(node.limitSleepToMillisTimer(uint32_t(now - 1), 30) == 0);
    node.queue = true; node.queue_ms = 2500;
    node.retry = true; node.retry_ms = 1001;
    assert(node.getPowerSaveSleepMilliseconds(30000) == 1001);
#if EXPECT_INTERRUPT_IDLE
    assert(node.getPowerSaveSleepSeconds(30) == 2); // Interrupt idle is permitted.
#else
    assert(node.getPowerSaveSleepSeconds(30) == 1); // Earliest timed owner wins.
#endif
    node.dirty_contacts_expiry = uint32_t(now + 999);
    assert(node.getPowerSaveSleepMilliseconds(30000) == 999);
#if EXPECT_INTERRUPT_IDLE
    assert(node.getPowerSaveSleepSeconds(30) == 1);
#else
    assert(node.getPowerSaveSleepSeconds(30) == 0);
#endif
    node.dirty_contacts_expiry = 0;
    node.queue = node.retry = false;
  }
}
'''


class PowerSavingDeadlineTests(unittest.TestCase):
    def test_actual_role_loops_preserve_subsecond_sleep_budgets(self):
        from cpp_source import body
        harness = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/PowerManagementUtils.h>
#define POWERSAVING_FIRSTSLEEP_SECS 1
#define POWERSAVING_FIRST_SLEEP_SECS 1
static uint32_t millis() { return 100000; }
struct Mesh {
  uint32_t budget = 30000, noise_delay = 50;
  bool noise = false;
  uint32_t getPowerSaveSleepMilliseconds(uint32_t limit) const {
    assert(limit == 30000); return budget;
  }
  bool getNextNoiseFloorWakeDelay(uint32_t& out) const {
    out = noise_delay; return noise;
  }
  bool millisHasNowPassed(uint32_t) const { return true; }
} the_mesh;
struct Board {
  unsigned calls = 0;
  uint32_t last_ms = 0;
  void sleep(uint32_t seconds) { ++calls; last_ms = seconds * 1000; }
  void sleepMilliseconds(uint32_t ms) { ++calls; last_ms = ms; }
} board;
void idle(bool can_power_save) { @GATE@ }
int main() {
  for (uint32_t budget : {0U, 1U, 50U, 999U, 1001U, 29999U, 30000U}) {
    the_mesh.budget = budget;
    for (bool noise : {false, true}) {
      the_mesh.noise = noise;
      const auto before = board.calls;
      idle(true);
      assert(board.calls == before + (budget != 0));
      if (budget) assert(board.last_ms == (noise && budget > 50 ? 50 : budget));
      const auto after = board.calls;
      idle(false); assert(board.calls == after);
    }
  }
  the_mesh.noise_delay = 0;
  const auto before = board.calls;
  idle(true); assert(board.calls == before);
}
'''
        with tempfile.TemporaryDirectory(prefix='meshcore-role-ms-idle-') as directory:
            cpp, exe = Path(directory) / 'test.cpp', Path(directory) / 'test'
            for role in ('simple_repeater', 'simple_room_server', 'simple_sensor'):
                with self.subTest(role=role):
                    source = (ROOT / 'examples' / role / 'main.cpp').read_text()
                    gate = body(source, '  if (can_power_save) {')
                    cpp.write_text(harness.replace('@GATE@', gate), encoding='ascii')
                    built = subprocess.run([
                        os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT / 'src'), str(cpp), '-o', str(exe),
                    ], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(exe)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_real_board_millisecond_idle_preserves_port_wake_contract(self):
        source = (ROOT / 'src/MeshCore.h').read_text(encoding='ascii')
        implementation = method(source, 'virtual void sleepMilliseconds(uint32_t millisecs)')
        code = r'''
#include <cassert>
#include <cstdint>
struct Board {
  unsigned calls = 0;
  uint32_t last = 0;
  virtual void sleep(uint32_t secs) { ++calls; last = secs; }
@METHOD@
};
int main() {
  Board board;
  board.sleepMilliseconds(0);
  assert(board.calls == 0);
  for (uint32_t ms : {1U, 50U, 999U, 1000U, 1001U, UINT32_MAX}) {
    const auto before = board.calls;
    board.sleepMilliseconds(ms);
#if EXPECT_INTERRUPT_IDLE
    assert(board.calls == before + 1 && board.last == 0);
#else
    assert(board.calls == before + (ms >= 1000));
    if (ms >= 1000) assert(board.last == ms / 1000);
#endif
  }
}
'''.replace('@METHOD@', implementation).replace(
            '#include <cstdint>', '#include <cstdint>\n#include <initializer_list>')
        with tempfile.TemporaryDirectory(prefix='meshcore-board-ms-idle-') as directory:
            cpp, exe = Path(directory) / 'test.cpp', Path(directory) / 'test'
            cpp.write_text(code, encoding='ascii')
            for platform in ('', 'NRF52_PLATFORM', 'RP2040_PLATFORM', 'STM32_PLATFORM'):
                with self.subTest(platform=platform or 'unknown-timed'):
                    built = subprocess.run([
                        os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-DEXPECT_INTERRUPT_IDLE=' + ('1' if platform else '0'),
                        *(['-D' + platform + '=1'] if platform else []),
                        str(cpp), '-o', str(exe),
                    ], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(exe)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_real_role_deadlines_preserve_timed_and_interrupt_idle(self):
        with tempfile.TemporaryDirectory(prefix='meshcore-sleep-deadlines-') as directory:
            work = Path(directory)
            for role, filename, name in (
                    ('repeater', 'examples/simple_repeater/MyMesh.cpp', 'MyMesh'),
                    ('room', 'examples/simple_room_server/MyMesh.cpp', 'MyMesh'),
                    ('sensor', 'examples/simple_sensor/SensorMesh.cpp', 'SensorMesh')):
                for platform in ('', 'NRF52_PLATFORM', 'RP2040_PLATFORM', 'STM32_PLATFORM'):
                    with self.subTest(role=role, platform=platform or 'timed'):
                        source = (ROOT / filename).read_text()
                        methods = '\n'.join(method(source, signature) for signature in (
                            f'uint32_t {name}::limitSleepToMillisTimer(',
                            f'uint32_t {name}::getPowerSaveSleepMilliseconds(',
                            f'uint32_t {name}::getPowerSaveSleepSeconds('))
                        methods = methods.replace(name + '::', 'Node::')
                        cpp, exe = work / (role + '.cpp'), work / role
                        cpp.write_text(HARNESS.replace('@METHODS@', methods))
                        sanitizers = [] if os.name == 'nt' else [
                            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                            '-fno-pie', '-no-pie']
                        built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                            '-Wall', '-Wextra', '-Werror', *sanitizers,
                            '-DEXPECT_INTERRUPT_IDLE=' + ('1' if platform else '0'),
                            *(['-D' + platform + '=1'] if platform else []), '-I', str(ROOT / 'src'),
                            str(cpp), '-o', str(exe)], capture_output=True, text=True)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        ran = subprocess.run([str(exe)], capture_output=True, text=True)
                        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
