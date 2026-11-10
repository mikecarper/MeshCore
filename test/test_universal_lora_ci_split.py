"""Execute CI shards with fake tools; never start a firmware build."""
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
HELPER = ROOT / 'tools/ci/run_universal_lora_smoke.sh'
SHARDS = {
    'tbeam': (
        ['Tbeam_companion_radio_full', 'Tbeam_repeater', 'Tbeam_room_server',
         'Tbeam_repeater_observer_mqtt', 'Tbeam_room_server_observer_mqtt'],
        ['Tbeam_kiss_modem']),
    't3s3': (
        ['LilyGo_T3S3_companion_radio_full', 'LilyGo_T3S3_repeater',
         'LilyGo_T3S3_room_server'],
        ['LilyGo_T3S3_kiss_modem', 'LilyGo_T3S3_terminal_chat']),
    'meshadventurer': (
        ['Meshadventurer_repeater', 'Meshadventurer_companion_radio_full',
         'Meshadventurer_room_server'],
        ['Meshadventurer_kiss_modem', 'Meshadventurer_terminal_chat']),
    'e22-tenstar': (
        ['Generic_E22_repeater', 'Tenstar_C3_repeater'],
        ['Generic_E22_kiss_modem', 'Tenstar_C3_kiss_modem']),
    'muzi': (
        ['muzi_base_companion_radio_full'],
        ['muzi_base_repeater', 'muzi_base_room_server',
         'muzi_base_companion_radio_usb', 'muzi_base_companion_radio_ble']),
}
FULL_FLAGS = ['--firmware-version', 'v1.18.0.2-halo-keymind-cascade-dev',
              '--radio-preset', 'usa-cascade-fixed', '--profile', 'cascade']
CONTRACTS = [
    'test_universal_lora.py', 'test_universal_lora_builds.py',
    'test_universal_lora_gpio.py', 'test_portable_lora_probe.py',
    'test_universal_lr_policy.py', 'test_muzi_universal_builds.py',
    'test_muzi_universal_startup.py', 'test_muzi_qspi_capacity.py',
    'test_notification_rtc_storage.py', 'test_companion_channels_rtc.py',
    'test_universal_sx126x_builds.py', 'test_universal_sx126x_defaults.py',
    'test_universal_radio_startup.py', 'test_universal_radio_boot_policy.py',
    'test_universal_radio_lto.py', 'test_consolidated_profiles.py',
]


class UniversalLoRaCiSplitTest(unittest.TestCase):
    def run_shard(self, arguments, *, fail_on=0):
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('bash required')
        with tempfile.TemporaryDirectory(prefix='universal-ci-shard-') as folder:
            root = Path(folder)
            helper = root / 'tools/ci/run_universal_lora_smoke.sh'
            helper.parent.mkdir(parents=True)
            shutil.copyfile(HELPER, helper)
            trace = root / 'trace.jsonl'
            recorder = root / 'record'
            recorder.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
trace = pathlib.Path(os.environ['CI_SHARD_TRACE'])
rows = trace.read_text().splitlines() if trace.exists() else []
with trace.open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
raise SystemExit(43 if len(rows) + 1 == int(os.environ['CI_SHARD_FAIL_ON']) else 0)
''')
            recorder.chmod(0o755)
            # Only these harmless stubs exist in this copied project. Even a
            # wrapper regression cannot accidentally invoke real PlatformIO.
            for file, kind in [(root / 'build.sh', 'full'),
                               (root / 'tools/ci/pio_retry.sh', 'pio')]:
                file.write_text('exec "$CI_SHARD_RECORDER" ' + kind + ' "$@"\n')
            binary = root / 'bin'
            binary.mkdir()
            for kind in ['python3', 'node']:
                tool = binary / kind
                tool.write_text('#!' + bash + '\nexec "$CI_SHARD_RECORDER" ' + kind + ' "$@"\n')
                tool.chmod(0o755)
            env = dict(os.environ, CI_SHARD_TRACE=str(trace),
                       CI_SHARD_RECORDER=str(recorder), CI_SHARD_FAIL_ON=str(fail_on),
                       PATH=str(binary) + os.pathsep + os.environ.get('PATH', ''))
            result = subprocess.run([bash, str(helper), *arguments], cwd=root / 'bin',
                                    env=env, text=True, capture_output=True, timeout=15)
            rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
            return result, rows

    def expected_commands(self, shard):
        full, interfaces = SHARDS[shard]
        flags = FULL_FLAGS + ([] if shard == 'muzi' else ['--require-ota'])
        commands = [['full', 'build-firmware', target, *flags] for target in full]
        interface_command = ['pio', 'run']
        for target in interfaces:
            interface_command += ['-e', target]
        return ([interface_command, *commands] if shard == 'muzi'
                else [*commands, interface_command])

    def test_every_existing_target_is_built_once_with_unchanged_flags(self):
        actual_targets = []
        for shard in SHARDS:
            with self.subTest(shard=shard):
                result, commands = self.run_shard([shard])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(commands, self.expected_commands(shard))
                for command in commands:
                    if command[0] == 'full':
                        actual_targets.append(command[2])
                    else:
                        self.assertEqual(command[1], 'run')
                        self.assertTrue(all(value == '-e' for value in command[2::2]))
                        actual_targets += command[3::2]
        expected_targets = [target for groups in SHARDS.values()
                            for targets in groups for target in targets]
        self.assertEqual(len(actual_targets), 25)
        self.assertEqual(Counter(actual_targets), Counter(expected_targets))
        self.assertTrue(all(count == 1 for count in Counter(actual_targets).values()))

    def test_each_command_failure_stops_its_shard_and_propagates_status(self):
        # Exercise failures in both Full builds and the interface wrappers;
        # no later target or fallback command may run after a failure.
        for shard in SHARDS:
            expected = self.expected_commands(shard)
            for command_number in range(1, len(expected) + 1):
                with self.subTest(shard=shard, command=command_number):
                    result, commands = self.run_shard([shard], fail_on=command_number)
                    self.assertEqual(result.returncode, 43, result.stderr)
                    self.assertEqual(commands, expected[:command_number])

    def test_contract_checks_run_once_without_firmware_commands(self):
        result, commands = self.run_shard(['contracts'])
        expected = [['python3', '-B', 'test/' + file, '-v'] for file in CONTRACTS]
        expected.append(['node', 'test/test_universal_radio_picker.js'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, expected)
        self.assertEqual(len(commands), 17)

    def test_contract_failure_stops_before_later_checks(self):
        result, commands = self.run_shard(['contracts'], fail_on=2)
        self.assertEqual(result.returncode, 43, result.stderr)
        self.assertEqual(commands, [['python3', '-B', 'test/' + file, '-v']
                                    for file in CONTRACTS[:2]])

    def test_invalid_arguments_reject_before_invoking_any_tool(self):
        for arguments in [[], ['unknown'], ['tbeam', 'muzi'], ['--help']]:
            with self.subTest(arguments=arguments):
                result, commands = self.run_shard(arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Usage:', result.stderr)
                self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
