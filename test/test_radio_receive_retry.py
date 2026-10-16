#!/usr/bin/env python3
"""Run failed RX starts through actual Dispatcher and radio lifecycle methods."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import HARNESS, ROOT, method


class RadioReceiveRetryTests(unittest.TestCase):
    def build(self, folder, mutation=None):
        source = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        header = (ROOT / 'src/helpers/radiolib/RadioLibWrappers.h').read_text()
        dispatcher = (ROOT / 'src/Dispatcher.h').read_text()
        rxps = (ROOT / 'src/helpers/radiolib/RXPowerSaving.h').read_text()
        if mutation == 'unpaced':
            source = source.replace('    if (_rx_retry_pending) {', '    if (false) {')
        getter = method(header, 'bool isCalibratingNoiseFloor() const')
        if mutation == 'pending_veto':
            getter = getter.replace('isInRecvMode() && !_rx_ps_armed && ', '')
        names = [('int16_t', 'performChannelScanWithTimeout'),
                 ('bool', 'isPacketPendingOrReceiving'), ('bool', 'isChannelActive'),
                 ('bool', 'isReceivingPassive'), ('bool', 'setRxPowerSaving'),
                 ('bool', 'setRxBoostedGainMode'), ('void', 'startRecv'),
                 ('void', 'checkReceiveMode'), ('void', 'loop'),
                 ('void', 'triggerNoiseFloorCalibrate'), ('void', 'requestRestartRecv'),
                 ('void', 'noiseFloorCalibCheck'), ('void', 'endNoiseFloorCalib'),
                 ('void', 'requestNoiseFloorRefresh'), ('void', 'requestPeriodicNoiseFloorRefresh'),
                 ('void', 'recalibrateNoiseFloor'), ('bool', 'resetAGCIfIdle'),
                 ('bool', 'tryResetAGC'), ('void', 'resetAGC'), ('int', 'recvRaw'),
                 ('void', 'onSendFinished'), ('void', 'begin'),
                 ('bool', 'getReceiveRetryDelay'), ('void', 'rxPsWatchdogCheck')]
        methods = method(rxps, 'inline bool rxPowerSavingUsesContinuousFallback(') + '\n'
        methods += '\n'.join(method(source, f'{kind} RadioLibWrapper::{name}(')
                             for kind, name in names)
        constants = '\n'.join(re.findall(r'^#define NF_\w+\s+[^\n]+', source, re.MULTILINE))
        constants += '\n' + re.search(
            r'^#define RX_POWERSAVING_CONTINUOUS_FALLBACK_US\s+[^\n]+', rxps, re.MULTILINE).group(0)
        start = header.index('#if defined(USE_SX1262) || defined(USE_SX1268) || defined(USE_LLCC68)')
        settle = header[start:header.index('#endif', start) + len('#endif')]
        harness = HARNESS[:HARNESS.index('int main() {')]
        harness = harness.replace('#include <RadioProfiles.h>',
            '#include <Dispatcher.h>\n#include <helpers/StaticPoolPacketManager.h>\n'
            '#include <helpers/PowerManagementUtils.h>\n#define BD_STARTUP_RX_PACKET 1')
        harness = harness.replace('struct RadioLibWrapper {', 'struct RadioLibWrapper : mesh::Radio {')
        harness = harness.replace('Radio radio{&events}; Radio* _radio',
                                  '::Radio radio{&events}; ::Radio* _radio')
        harness = harness.replace('unsigned long getEstAirtimeFor(int)', 'uint32_t getEstAirtimeFor(int)')
        harness = harness.replace('void rxPsWatchdogCheck() {}', 'void rxPsWatchdogCheck();')
        harness = harness.replace('  void serviceWatchdog()',
            '  unsigned after = 0;\n  int getStartupReason() { return 0; }\n'
            '  void onAfterTransmit() { ++after; }\n  void serviceWatchdog()')
        harness = harness.replace('  int startChannelScan()', '''
  unsigned packet_length = 0, direct_arms = 0;
  int startReceive() { ++direct_arms; return start_result; }
  int setPreambleLength(uint16_t) { return 0; }
  int finishTransmit() { return 0; }
  size_t getPacketLength() { return packet_length; }
  float getSNR() { return 0; }
  float getRSSI() { return 0; }
  int readData(uint8_t* out, int len) { for (int i=0;i<len;++i) out[i]=0x42; return 0; }
  int startChannelScan()''')
        harness = harness.replace('return events.pending();', 'return _loop_event_pending || events.pending();')
        harness = harness.replace('bool claimRadioInterrupt() { return events.claim(); }', '''
  bool claimRadioInterrupt() {
    const bool pending = _loop_event_pending; _loop_event_pending = false;
    return events.claim() || pending;
  }''')
        harness = harness.replace('  @RECOVERY_GETTER@', '''
  int recvRaw(uint8_t*, int) override;
  void begin() override;
  void onSendFinished() override;
  bool getReceiveRetryDelay(uint32_t, uint32_t&) const override;
  bool registerInterruptAction() { _loop_event_pending=false; events.claim(); return true; }
  uint16_t currentPreambleLength() const { return 32; }
  bool isPacketReady() { return true; }
  bool isInRecvMode() const override { return state == STATE_RX; }
  bool startSendRaw(const uint8_t*, int) override { ++sends; return false; }
  bool isSendComplete() override { return false; }
  float packetScore(float, int) override { return 0; }
  bool _rx_hold_continuous = false, _loop_event_pending = false;
  uint32_t last_radio_interrupt_millis = 0, last_recv_millis = 0;
  float _last_snr = 0, _last_rssi = 0;
  unsigned n_recv = 0, n_recv_errors = 0, sends = 0;
  uint8_t _wd_stage = 0, _wd_strikes = 0, _irq_probe_failures = 0;
  unsigned long _wd_observe_until = 0;
  ''' + getter.replace(' override', '') + '\n  @RECOVERY_GETTER@')
        harness = harness.replace(
            'bool recoverRadio(bool use_hard) { use_hard ? ++hard : ++soft; return true; }',
            method(source, 'bool RadioLibWrapper::recoverRadio(').replace('RadioLibWrapper::', '')
            + '\n bool supportsRadioDeepInit() const { return false; }\n'
            + ' bool restoreAfterDeepInit() { recalibrateNoiseFloor(); return false; }')
        harness = (harness.replace('@METHODS@', methods).replace('@NF_CONSTANTS@', constants)
            .replace('@SETTLE_CONSTANT@', settle)
            .replace('@RECOVERY_GETTER@', method(header, 'uint32_t getRecoveryCount() const'))
            .replace('@LEGACY_TRY_RESET@', method(dispatcher, 'virtual bool tryResetAGC('))
            .replace('@LEGACY_RECOVERY_GETTER@', method(dispatcher, 'virtual uint32_t getRecoveryCount(')))
        harness += (ROOT / 'test/fixtures/radio_receive_retry.cpp').read_text()
        folder = Path(folder)
        cpp, binary = folder / 'retry.cpp', folder / 'retry'
        cpp.write_text(harness.replace('\u2014', '-'), encoding='ascii')
        dispatcher_source = ROOT / 'src/Dispatcher.cpp'
        if mutation == 'agc_fast':
            original = dispatcher_source.read_text()
            alignment = ('      if (adaptive && _radio->getReceiveRetryDelay(deferred_at, receive_delay)\n'
                         '          && receive_delay > retry_delay) retry_delay = receive_delay;\n')
            self.assertEqual(original.count(alignment), 1)
            dispatcher_source = folder / 'Dispatcher.cpp'
            dispatcher_source.write_text(original.replace(alignment, ''), encoding='ascii')
        built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-O1',
            '-DUSE_SX1262=1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie', '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
            str(cpp), str(dispatcher_source), str(ROOT / 'src/Packet.cpp'),
            str(ROOT / 'src/helpers/StaticPoolPacketManager.cpp'), '-o', str(binary)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stderr)
        return binary

    def run_probe(self, binary):
        env = os.environ.copy()
        env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS', '') + ':detect_leaks=0'
        return subprocess.run([str(binary)], capture_output=True, text=True, env=env, timeout=20)

    def test_actual_dispatcher_and_receive_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix='meshcore-rx-retry-') as folder:
            result = self.run_probe(self.build(folder))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: receive retry lifecycle', result.stdout)
            print(result.stdout, end='')

    def test_old_hot_poll_and_pending_calibration_veto_negative_controls(self):
        for mutation in ('unpaced', 'pending_veto', 'agc_fast'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix='meshcore-rx-retry-negative-') as folder:
                result = self.run_probe(self.build(folder, mutation))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('busyAfterFailure' if mutation == 'agc_fast'
                              else 'persistentFailure', result.stderr)


if __name__ == '__main__':
    unittest.main()
