#!/usr/bin/env python3
"""Execute real floor deadlines/transitions with counted host I/O adapters.

These tests prove scheduling and IRQ-ownership contracts, not actual current
consumption, RTOS sleep residency, or RF sample quality on untested hardware.
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from test_radio_receive_contract import ROOT, method, transition_harness


class NoiseFloorSleepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='meshcore-floor-sleep-')
        cls.addClassCleanup(cls.directory.cleanup)
        work = Path(cls.directory.name)
        source = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        header = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.h').read_text()
        getter = method(source, 'bool RadioLibWrapper::getNoiseFloorWakeDelay(')
        harness = transition_harness(source).split('struct LegacyRadio {', 1)[0]
        harness = harness.replace(getter, '', 1)
        harness = harness.replace('#include <cassert>', '#include <cassert>\n#include <cstring>')
        harness = harness.replace('  bool tryResetAGC();',
            '  bool isInRecvMode() const { return state == STATE_RX; }\n'
            + method(header, 'bool isCalibratingNoiseFloor() const').replace(' override', '')
            + '\n  bool tryResetAGC();')
        harness = harness.replace('bool isChipBusy() { return busy; }',
            'unsigned busy_probes = 0, packet_probes = 0;\n'
            '  bool isChipBusy() { ++busy_probes; return busy; }')
        harness = harness.replace('bool isReceivingPacket() { return packet; }',
            'bool isReceivingPacket() { ++packet_probes; return packet; }')
        fixture = (ROOT / 'test/fixtures/noise_floor_sleep.cpp').read_text()
        variants = {
            'production': getter,
            'old_batch_veto': getter[:getter.index('{') + 1] + '''
  (void)now;
  if (!isCalibratingNoiseFloor()) return false;
  delay_ms = 0;
  return true;
}
''',
        }
        cls.binaries = {}
        sanitize = [] if not sys.platform.startswith('linux') else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-omit-frame-pointer', '-fno-pie', '-no-pie']
        for family in ('sx1262', 'other'):
            for name, implementation in variants.items():
                filename = name + '_' + family
                cpp, binary = work / (filename + '.cpp'), work / filename
                cpp.write_text((harness + implementation + fixture).replace('\u2014', '-'),
                               encoding='ascii')
                built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                    '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    *(['-DUSE_SX1262=1'] if family == 'sx1262' else []),
                    *sanitize, '-I', str(ROOT / 'src'), str(cpp), '-o', str(binary)],
                    capture_output=True, text=True, timeout=60)
                if built.returncode:
                    raise AssertionError(built.stdout + built.stderr)
                cls.binaries[name, family] = binary

    def run_case(self, scenario, variant='production', failure=None):
        for family in ('sx1262', 'other'):
            with self.subTest(family=family):
                result = subprocess.run([str(self.binaries[variant, family]), scenario],
                    capture_output=True, text=True, timeout=20)
                if failure:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(failure, result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('PASS: ' + scenario, result.stdout)

    def test_quiet_batches_allow_idle_between_all_original_samples(self):
        self.run_case('quiet')

    def test_deadlines_and_settling_survive_rollover_including_zero(self):
        self.run_case('rollover')

    def test_deadline_queries_have_no_io_or_irq_consumption(self):
        self.run_case('queries')

    def test_rejected_samples_can_idle_and_keep_timeout_bound(self):
        self.run_case('rejected')

    def test_success_becomes_lazy_but_held_rises_and_timeouts_retry_sooner(self):
        self.run_case('lazy')

    def test_timeout_timestamp_zero_is_not_an_unarmed_or_startup_sentinel(self):
        self.run_case('timeout-zero')

    def test_old_whole_batch_sleep_veto_negative_control(self):
        self.run_case('quiet', 'old_batch_veto', 'pending batch must permit idle')


if __name__ == '__main__':
    unittest.main()
