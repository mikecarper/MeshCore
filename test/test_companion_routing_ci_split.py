"""Verify Companion CI coverage and failure propagation with isolated fake Python.

The copied checkout contains no actual test scripts, and its PATH exposes only
our recorder and dirname. These checks cannot launch firmware tests or PIO.
"""
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tools/ci/run_companion_routing_tests.sh'
# The complete original command list from 48217a0dd, in source order. Keep this
# inventory independent of the helper so silently dropping a script fails CI.
BASELINE = """
test/test_companion_channel_alias.py
test/test_companion_tx_routing.py
test/test_companion_channel_publication.py
test/test_companion_tx_raw_alias.py
test/test_companion_traffic_routing.py
test/test_host_kiss_radio.py
test/test_host_kiss_infrastructure.py
test/test_host_kiss.py
test/test_host_kiss_usb.py
test/test_host_kiss_ingress.py
test/test_host_kiss_capabilities.py
test/test_host_kiss_ram.py
test/test_ethernet_kiss.py
test/test_ethernet_radio.py
test/test_companion_ethernet.py
test/test_companion_discovery.py
test/test_repeater_discovery.py
test/test_repeater_discovery_settings.py
test/test_companion_discovery_correlation.py
test/test_pico_ble_queues.py
test/test_companion_preferences_transaction.py
test/test_companion_prefs_values.py
test/test_companion_prefs_image.py
test/test_companion_timezone_prefs.py
test/test_companion_prefs_tail.py
test/test_companion_radio_settings_transaction.py
test/test_companion_prefs_transactions.py
test/test_companion_primary_radio_persistence.py
test/test_companion_response_bounds.py
test/test_companion_rx_via.py
test/test_companion_ack_delivery.py
test/test_companion_lost_reply.py
test/test_companion_delayed_reply_delivery.py
test/test_companion_scheduled_reboot.py
test/test_trace_profile_timing.py
test/test_companion_hardware_serial.py
test/test_companion_hardware_serial_gps.py
test/test_companion_contact_lifetime.py
test/test_contact_indices.py
test/test_small_table_sort.py
test/test_neighbor_resident_state.py
test/test_environment_sensor_references.py
test/test_companion_identity_cli.py
test/test_companion_packet_stream.py
test/test_companion_uncached_storage.py
test/test_companion_contact_partial_tail.py
test/test_companion_channel_storage.py
test/test_nrf52_extrafs_contract.py
test/test_xiao_qspi_recovery.py
test/test_nrf52_qspi_dma_fix.py
test/test_xiao_identity_recovery.py
test/test_companion_ota_config.py
test/test_ota_identity_policy.py
test/test_companion_radio_gain_restore.py
test/test_room_sensor_radio_restore.py
test/test_common_radio_persistence.py
test/test_infrastructure_radio_apply.py
test/test_common_cli_save_results.py
test/test_common_cli_tx_pending.py
test/test_radio_rx_settings_transaction.py
test/test_common_prefs_uncertain_reply.py
test/test_common_prefs_apply_reply.py
test/test_common_prefs_commit.py
test/test_common_cli_retry_tail.py
test/test_common_prefs_legacy_rxgain.py
test/test_flood_policy_capacity.py
test/test_wardriving_default_migration.py
test/test_flood_rule_layout.py
test/test_mqtt_prefs_commit.py
test/test_identity_and_settings_recovery.py
test/test_contact_cache.py
test/test_one_key_dm_shared_queue.py
test/test_one_key_dm_compact_held.py
test/test_companion_shared_ota_capacity.py
test/test_espnow_raw_initialization.py
""".split()
SHARDS = {
    'transport': BASELINE[:20] + BASELINE[28:37] + BASELINE[74:75],
    'preferences': BASELINE[20:28] + BASELINE[51:69],
    'storage': BASELINE[37:51] + BASELINE[69:74],
}


class CompanionRoutingCiSplitTest(unittest.TestCase):
    def run_shard(self, arguments, *, fail_on=0):
        bash, dirname = shutil.which('bash'), shutil.which('dirname')
        if not bash or not dirname:
            self.skipTest('bash and dirname required')
        with tempfile.TemporaryDirectory(prefix='companion-ci-shard-') as folder:
            root = Path(folder)
            helper = root / 'tools/ci/run_companion_routing_tests.sh'
            helper.parent.mkdir(parents=True)
            shutil.copyfile(HELPER, helper)
            trace = root / 'trace.jsonl'
            recorder = root / 'record.py'
            recorder.write_text(r'''import json, os, pathlib, sys
trace = pathlib.Path(os.environ['COMPANION_CI_TRACE'])
rows = trace.read_text().splitlines() if trace.exists() else []
with trace.open('a') as stream:
    stream.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}) + '\n')
raise SystemExit(43 if len(rows) + 1 == int(os.environ['COMPANION_CI_FAIL_ON']) else 0)
''')
            binary = root / 'bin'
            binary.mkdir()
            python = binary / 'python3'
            python.write_text('#!' + bash + '\nexec "$COMPANION_CI_PYTHON" "$COMPANION_CI_RECORDER" "$@"\n')
            python.chmod(0o755)
            # No other executable from the actual PATH is visible. An added
            # compile/build command must fail instead of touching a real SDK.
            (binary / 'dirname').symlink_to(dirname)
            elsewhere = root / 'elsewhere'
            elsewhere.mkdir()
            env = dict(os.environ, PATH=str(binary),
                       COMPANION_CI_TRACE=str(trace),
                       COMPANION_CI_FAIL_ON=str(fail_on),
                       COMPANION_CI_PYTHON=sys.executable,
                       COMPANION_CI_RECORDER=str(recorder))
            result = subprocess.run([bash, str(helper), *arguments], cwd=elsewhere,
                                    env=env, text=True, capture_output=True, timeout=20)
            rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
            for row in rows:
                self.assertEqual(row['cwd'], str(root), 'tests must run from their own checkout root')
            return result, [row['argv'] for row in rows]

    @staticmethod
    def expected_commands(shard):
        return [['-B', path, '-v'] for path in SHARDS[shard]]

    def test_every_original_command_runs_once_with_unchanged_flags(self):
        self.assertEqual(len(BASELINE), 75)
        self.assertEqual(len(set(BASELINE)), 75)
        self.assertEqual({name: len(paths) for name, paths in SHARDS.items()},
                         {'transport': 30, 'preferences': 26, 'storage': 19})
        actual = []
        for shard in SHARDS:
            with self.subTest(shard=shard):
                result, commands = self.run_shard([shard])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(commands, self.expected_commands(shard))
                actual.extend(command[1] for command in commands)
                for path in SHARDS[shard]:
                    self.assertIn('Companion routing [' + shard + ']: ' + path, result.stdout)
        self.assertEqual(Counter(actual), Counter(BASELINE))
        self.assertTrue(all(count == 1 for count in Counter(actual).values()))

    def test_each_shard_preserves_relative_source_order(self):
        for shard, paths in SHARDS.items():
            with self.subTest(shard=shard):
                positions = [BASELINE.index(path) for path in paths]
                self.assertEqual(positions, sorted(positions))

    def test_first_middle_and_last_failure_stop_the_shard_and_propagate_status(self):
        for shard in SHARDS:
            expected = self.expected_commands(shard)
            for position in sorted({1, (len(expected) + 1) // 2, len(expected)}):
                with self.subTest(shard=shard, failed_command=position):
                    result, commands = self.run_shard([shard], fail_on=position)
                    self.assertEqual(result.returncode, 43, result.stderr)
                    self.assertEqual(commands, expected[:position])
                    if position < len(expected):
                        self.assertNotIn(expected[position][1], result.stdout)

    def test_invalid_arguments_reject_before_tools_run(self):
        for arguments in [[], ['unknown'], ['transport', 'storage'], ['--help'], ['all']]:
            with self.subTest(arguments=arguments):
                result, commands = self.run_shard(arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('Usage:', result.stderr)
                self.assertEqual(commands, [])
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
