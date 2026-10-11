#!/usr/bin/env python3
"""Execute Sensor subscription parsing and push admission with real LPP helpers."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'examples/simple_sensor/SensorMesh.cpp'
HELPER = ROOT / 'src/helpers/sensors/LPPDataHelpers.h'
FIXTURE = ROOT / 'test/fixtures/sensor_subscriptions/test_sensor_subscriptions.cpp'


def production_methods(source):
    methods = extract_braced(source, 'static float findTelemValue(') + '\n'
    methods += extract_braced(source, 'bool SensorMesh::telemHasChanged(') + '\n'
    if 'void SensorMesh::snapshotTelemetry(' in source:
        methods += extract_braced(source, 'void SensorMesh::snapshotTelemetry(') + '\n'
    handler = extract_braced(source, 'uint8_t SensorMesh::handleRequest(')
    prefix = handler[:handler.index('  if (req_type == REQ_TYPE_GET_TELEMETRY_DATA')]
    methods += prefix + extract_braced(handler, 'if (req_type == REQ_TYPE_SUBSCRIBE')
    methods += '\nreturn 0;\n}\n'
    loop = source[source.index('  uint32_t curr = getRTCClock()->getCurrentTime();', source.index('void SensorMesh::loop()')):]
    constructor = extract_braced(source, 'SensorMesh::SensorMesh(')
    capacity = re.search(r'telemetry\(([^)]+)\)', constructor).group(1)
    methods += 'SensorMesh::SensorMesh() : telemetry(' + capacity + ') {}\n'
    methods += 'void SensorMesh::pushSubscriptions() { uint32_t curr = 100;\n'
    methods += extract_braced(loop, 'for (int i = 0; i < acl.getNumClients(); i++)') + '\n}\n'
    packet = (ROOT / 'src/Packet.cpp').read_text()
    methods += 'namespace mesh {\n' + extract_braced(packet, 'Packet::Packet()') + '\n'
    methods += extract_braced(packet, 'bool Packet::isValidPathLen(') + '\n}\n'
    return methods


class SensorSubscriptionTest(unittest.TestCase):
    def execute(self, case, transform=None, helper_transform=None):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler, 'host C++17 compiler required')
        source = SOURCE.read_text()
        helper = HELPER.read_text()
        if transform:
            source = transform(source)
        if helper_transform:
            helper = helper_transform(helper)
        with tempfile.TemporaryDirectory(prefix='sensor-subscription-') as directory:
            work = Path(directory)
            (work / 'production.inc').write_text(production_methods(source), encoding='ascii')
            (work / 'LPPDataHelpers.h').write_text(helper, encoding='ascii')
            binary = work / 'test'
            args = [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-I' + str(work), '-I' + str(ROOT / 'src'), str(FIXTURE), '-o', str(binary)]
            if sys.platform.startswith('linux'):
                args[1:1] = ['-fsanitize=address,undefined,float-cast-overflow', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            built = subprocess.run(args, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            return subprocess.run([str(binary), case], capture_output=True, text=True, timeout=30)

    def passed(self, case):
        run = self.execute(case)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Sensor subscription checks passed', run.stdout)

    def test_supported_scalars_and_humidity_wire_encoding(self):
        self.passed('scalar')

    def test_unsupported_types_truncation_and_existing_subscription_preserved(self):
        self.passed('input')

    def test_allocation_and_queue_failures_keep_delta_available_for_retry(self):
        self.passed('admission')

    def test_subscription_collection_and_push_fit_host_packet_budget(self):
        self.passed('capacity')

    def test_tracker_mode_suppresses_active_and_pending_pushes_then_restores(self):
        self.passed('tracker')

    def test_missing_tracker_push_guard_is_rejected(self):
        def old(source):
            guard = '      if (isTrackerModeEnabled()) break; // Check-in replaces background pushes.'
            self.assertEqual(source.count(guard), 1)
            return source.replace(guard, '', 1)
        self.assertNotEqual(self.execute('tracker', old).returncode, 0)

    def test_scalar_writer_rejections_leave_buffer_and_length_untouched(self):
        self.passed('writer')

    def test_scalar_width_sign_nonfinite_and_uint32_round_trip(self):
        self.passed('conversion')

    def test_old_acceptance_of_vector_delta_records_is_rejected(self):
        def old(source):
            guard = 'LPPData::isScalarType(type)\n          && item_len <= min_deltas_len - i'
            self.assertIn(guard, source)
            return source.replace(guard, 'item_len <= min_deltas_len - i', 1)
        self.assertNotEqual(self.execute('input', old).returncode, 0)

    def test_old_invalid_request_overwrites_existing_push_tag(self):
        def old(source):
            marker = '    if (r && valid_deltas) {\n      memcpy(&from->extra.sensor.push_tag, &payload[0], 4);'
            self.assertIn(marker, source)
            source = source.replace(marker, '    if (r && valid_deltas) {', 1)
            marker = '    uint16_t timeout_secs;'
            return source.replace(marker, '    memcpy(&from->extra.sensor.push_tag, &payload[0], 4);\n' + marker, 1)
        self.assertNotEqual(self.execute('input', old).returncode, 0)

    def test_old_early_threshold_snapshot_is_rejected(self):
        def old(source):
            begin = source.index('bool SensorMesh::telemHasChanged(')
            end = source.index('void SensorMesh::snapshotTelemetry(', begin)
            before = source[begin:end]
            self.assertIn('  return changed;', before)
            altered = before.replace('  return changed;', '  if (changed) snapshotTelemetry(c);\n  return changed;', 1)
            return source[:begin] + altered + source[end:]
        self.assertNotEqual(self.execute('admission', old).returncode, 0)

    def test_old_humidity_width_and_multiplier_are_rejected(self):
        def width(helper):
            marker = '      case LPP_ALTITUDE:\n      case LPP_VOLTAGE:'
            self.assertIn(marker, helper)
            return helper.replace(marker, '      case LPP_RELATIVE_HUMIDITY:\n' + marker, 1)
        def multiplier(helper):
            marker = '      case LPP_RELATIVE_HUMIDITY:\n        return 2;'
            self.assertIn(marker, helper)
            return helper.replace(marker, '      case LPP_RELATIVE_HUMIDITY:\n        return 10;', 1)
        for old in (width, multiplier):
            with self.subTest(old=old.__name__):
                self.assertNotEqual(self.execute('scalar', helper_transform=old).returncode, 0)

    def test_old_unbounded_subscription_collection_is_rejected(self):
        def old(source):
            marker = '      telemetry(mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY - 8)'
            self.assertIn(marker, source)
            return source.replace(marker, '      telemetry(MAX_PACKET_PAYLOAD - 4)', 1)
        self.assertNotEqual(self.execute('capacity', old).returncode, 0)

    def test_old_push_allows_oversized_response_attempt(self):
        def old(source):
            marker = '      if (telemetry_len > mesh::CLIENT_ACL_DIRECT_REPLY_CAPACITY - 8) continue;'
            self.assertIn(marker, source)
            return source.replace(marker, '      if (telemetry_len > sizeof(reply_data) - 8) continue;', 1)
        self.assertNotEqual(self.execute('capacity', old).returncode, 0)

    def test_old_signed_four_byte_mask_is_rejected_by_sanitizer(self):
        def old(helper):
            marker = 'size == sizeof(uint32_t)\n        ? UINT32_MAX : (uint32_t(1) << (size * 8)) - 1'
            self.assertIn(marker, helper)
            return helper.replace(marker, '(1 << (size * 8)) - 1', 1)
        self.assertNotEqual(self.execute('conversion', helper_transform=old).returncode, 0)

    def test_old_writer_commits_incomplete_header_on_encoding_failure(self):
        def old(helper):
            begin = helper.index('    if (LPPData::isScalarType(type)', helper.index('bool writeData('))
            end = helper.index('    return false;', begin)
            original = '''    if (_len + 2 + sz <= _max_len) {
      _buf[_len++] = channel;
      _buf[_len++] = type;
      _len += LPPData::putFloat(&_buf[_len], v, sz, mul, s);
      return true;
    }
'''
            return helper[:begin] + original + helper[end:]
        self.assertNotEqual(self.execute('writer', helper_transform=old).returncode, 0)

    def test_old_unguarded_float_cast_is_rejected_by_sanitizer(self):
        def old(helper):
            begin = helper.index('    if (size == 0', helper.index('static uint8_t putFloat('))
            end = helper.index('    // add bytes (MSB first)', begin)
            original = '''    bool sign = value < 0;
    if (sign) value = -value;
    uint32_t v = value * multiplier;
    if (is_signed && sign) {
      const uint32_t mask = size == 4 ? UINT32_MAX : (uint32_t(1) << (size * 8)) - 1;
      v = (uint32_t(0) - v) & mask;
    }
'''
            return helper[:begin] + original + helper[end:]
        self.assertNotEqual(self.execute('conversion', helper_transform=old).returncode, 0)

    def test_header_and_ci_wiring(self):
        self.assertIn('  electroniccats/CayenneLPP @ 1.6.1', (ROOT / 'platformio.ini').read_text())
        self.assertIn('  void snapshotTelemetry(ClientInfo* c);',
                      (ROOT / 'examples/simple_sensor/SensorMesh.h').read_text())
        self.assertIn('          python3 -B test/test_sensor_subscriptions.py -v\n',
                      (ROOT / '.github/workflows/run-unit-tests.yml').read_text())


if __name__ == '__main__':
    unittest.main()
