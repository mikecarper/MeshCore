import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('picker_controls', ROOT / 'scripts/generate_picker_controls.py')
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class PickerControlsTests(unittest.TestCase):
    def test_partition_migrations_follow_the_complete_packager_lookup(self):
        expected = {spec['target']: package
                    for package, spec in GENERATOR.BOARDS.items()}
        self.assertEqual(len(expected), len(GENERATOR.BOARDS))
        self.assertEqual(GENERATOR.partition_migrations(), expected)
        checked_in = json.loads((ROOT / 'docs/_data/firmware_controls.json').read_text())
        self.assertEqual(checked_in['partitionMigrations'], expected)
        self.assertEqual(expected['Station_G2_repeater'], 'station-g2-repeater')
        self.assertEqual(expected['Station_G2_logging_repeater'], 'station-g2-logging-repeater')
        self.assertEqual(expected['nibble_zero_connect_room_server_'], 'nibble-zero-room-server')
        self.assertEqual(expected['Waveshare_S3_LR1121_repeater_observer_mqtt'],
                         'waveshare-s3-lr1121-observer-repeater')
        self.assertEqual(expected['Waveshare_S3_LR1121_room_server_observer_mqtt'],
                         'waveshare-s3-lr1121-observer-room-server')
        # Only observer roles use the enlarged layout. Do not advertise an
        # observer migration package for ordinary fixed-layout firmware.
        for role in ('repeater', 'room_server', 'sensor',
                     'companion_radio_usb', 'companion_radio_ble'):
            self.assertNotIn('Waveshare_S3_LR1121_' + role, expected)

    def test_runtime_metadata_uses_verified_image_capabilities(self):
        source = 'a' * 40
        manifest = dict(source_commit=source,
                        capabilities=['companion.mota_sender', 'logging.usb.packets',
                                      'logging.usb.control', 'companion.dedicated_usb_logging'],
                        ota_update_methods=[], verification=[
                            dict(capability='companion.mota_sender', present=True, source='linked image'),
                            dict(capability='companion.dedicated_usb_logging', present=True, source='linked image'),
                            dict(capability='logging.usb.packets', present=True, source='packaged application'),
                            dict(capability='logging.usb.control', present=True, source='packaged application'),
                        ])
        self.assertEqual(GENERATOR.runtime_metadata(manifest, False), dict(
            otaRole='lora-source', loggingModes=['none', 'usb'],
            loggingControl='usb.logging', loggingSource=source,
            dedicatedUsbLogging=True))
        self.assertEqual(GENERATOR.runtime_metadata(manifest, True)['loggingModes'],
                         ['none', 'usb', 'wifi', 'both'])
        manifest['verification'][-1]['source'] = 'linked image'
        self.assertNotIn('loggingModes', GENERATOR.runtime_metadata(manifest, False))
        manifest['capabilities'].append('ota.update.lora')
        manifest['ota_update_methods'] = ['lora']
        self.assertEqual(GENERATOR.runtime_metadata(manifest, False)['otaRole'], 'lora-receiver')

    def test_sensor_profile_is_qualified_nrf52_receiver_metadata_not_a_filename(self):
        source = 'a' * 40
        manifest = dict(target='RAK_3401_repeater_lora_ota_no_external_sensors',
                        platform='NRF52_PLATFORM', verified=True, ota_update_verified=True,
                        capabilities=['ota.update.lora', 'sensor.profile.full'],
                        ota_update_methods=['lora'], verification=[
                            dict(capability='ota.update.lora', present=True, source='linked image')])
        result = GENERATOR.runtime_metadata(manifest, False, source)
        self.assertEqual(result['sensorProfile'], 'full')
        self.assertEqual(result['sensorProfileSource'], source)
        manifest['capabilities'][-1] = 'sensor.profile.reduced'
        self.assertEqual(GENERATOR.runtime_metadata(manifest, False, source)['sensorProfile'], 'reduced')
        for changes in (dict(platform='ESP32_PLATFORM'), dict(target='RAK_3401_companion_radio_full'),
                        dict(ota_update_verified=False), dict(verification=[]),
                        dict(verification=[dict(capability='ota.update.lora', present=True, source='filename')])):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'qualified nRF52'):
                GENERATOR.runtime_metadata({**manifest, **changes}, False, source)
        with self.assertRaisesRegex(ValueError, 'exact source commit'):
            GENERATOR.runtime_metadata(manifest, False, 'aaaaaaaa')
        manifest['capabilities'].append('sensor.profile.full')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            GENERATOR.runtime_metadata(manifest, False, source)

    def test_generates_independent_controls_for_same_identity_sensor_pair(self):
        source = 'a' * 40
        family = 'v1.17.1.7-dev-aaaaaaaa'
        logical = 'RAK_3401_repeater_lora_ota_no_external_sensors'
        manifests = []
        for policy in ('full', 'reduced'):
            manifests.append(dict(target=logical, artifact_target=logical + '-' + policy + '-ota',
                platformio_env='sample', platform='NRF52_PLATFORM', verified=True,
                ota_update_verified=True, capabilities=['sensor.profile.' + policy, 'ota.update.lora'],
                ota_update_methods=['lora'], verification=[dict(capability='ota.update.lora',
                    present=True, source='linked image')], files=[logical + '-' + policy + '-ota-' + family + ext
                    for ext in ('.zip', '.uf2')]))
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            for group in ('companion', 'lora-ota'):
                (stage / group).mkdir()
            (stage / 'release-plan.json').write_text(json.dumps(dict(source=source, groups=[
                dict(key='companion', tag=family), dict(key='lora-ota', tag='lora-ota-' + family)])))
            (stage / 'companion/TARGET-MANIFEST.json').write_text('[]')
            (stage / 'lora-ota/TARGET-MANIFEST.json').write_text(json.dumps(manifests))
            controls = GENERATOR.generate(stage, [('env:sample', [('build_flags', [])])])
        self.assertEqual(set(controls['profiles']), {logical + '-full', logical + '-reduced'})
        for policy in ('full', 'reduced'):
            profile = controls['profiles'][logical + '-' + policy]
            self.assertEqual(profile['sensorProfile'], policy)
            self.assertEqual(profile['sensorProfileSource'], source)
            self.assertEqual(profile['otaRole'], 'lora-receiver')
            self.assertEqual(profile['updateMethods'], ['lora'])

    def generate(self, reductions, *, source=None, flags=(), verified=True):
        initial = 'a' * 40
        manifest = dict(target='sample_companion_radio_full', platformio_env='sample',
                        platform='ESP32_PLATFORM', verified=verified, capabilities=['profile.full'],
                        reductions=reductions, ota_update_methods=['wifi'],
                        files=['sample_companion_radio_full-v1.17.1.6-dev-' + (source or initial)[:8] + '.bin'])
        if source is not None:
            manifest['source_commit'] = source
        with tempfile.TemporaryDirectory(prefix='meshcore-picker-controls-') as temporary:
            stage = Path(temporary)
            (stage / 'companion').mkdir()
            (stage / 'release-plan.json').write_text(json.dumps(dict(source=initial, groups=[
                dict(key='companion', tag='v1.17.1.6-dev-aaaaaaaa')
            ])))
            (stage / 'companion/TARGET-MANIFEST.json').write_text(json.dumps([manifest]))
            config = [('env:sample', [('build_flags', list(flags))])]
            result = GENERATOR.generate(stage, config)
        self.assertEqual(result['partitionMigrations'], GENERATOR.partition_migrations())
        return result['profiles']['sample_companion_radio_full']

    def test_compiled_capacity_overrides_unreduced_config_and_binds_repair_source(self):
        control = self.generate([
            'companion.capacity limited to 150 contacts for runtime RAM; 256 queued frames and all Full transports retained'
        ], source='b' * 40, flags=['-DMAX_CONTACTS=350'])
        self.assertIn('150 contacts and 256 queued messages', control['memoryNote'])
        self.assertNotIn('350 contacts', control['memoryNote'])
        self.assertEqual(control['memorySource'], 'b' * 40)

    def test_compact_contacts_channels_and_queue_limits(self):
        for channels in (8, 30):
            with self.subTest(channels=channels):
                control = self.generate([
                    f'companion.capacity limited to 100 contacts, {channels} channels, and 16 queued frames by measured internal DRAM'
                ])
                self.assertIn(f'100 contacts, {channels} channels and 16 queued messages', control['memoryNote'])

    def test_borrowed_queue_notes_retain_normal_and_active_capacities(self):
        control = self.generate(['nRF52 Full: 256 offline frames normally; 128 while mOTA borrows queue storage'])
        self.assertIn('256 offline messages normally; 128 while mOTA', control['memoryNote'])
        self.assertIn('restores all 256 slots', control['memoryNote'])
        self.assertEqual(control['memorySource'], 'a' * 40)

    def test_paper_includes_declared_channels_after_flag_overrides(self):
        control = self.generate([
            'Wireless Paper Full: 350 contacts; 256 offline frames normally, 128 while mOTA borrows queue storage'
        ], flags=['-DMAX_GROUP_CHANNELS=80', '-UMAX_GROUP_CHANNELS', '-D MAX_GROUP_CHANNELS=40'])
        self.assertIn('350 contacts and 40 channels', control['memoryNote'])
        self.assertIn('256 offline messages normally; 128 while mOTA', control['memoryNote'])

    def test_repeater_table_limits_are_kept_together(self):
        control = self.generate([
            'mesh.neighbors limited to 50 by measured RAM/flash capacity',
            'mesh.flood_rules limited to 16 by measured internal RAM; complete rule engine, color display, GPS, and OTA retained',
        ])
        self.assertIn('Neighbor table: 50 entries', control['memoryNote'])
        self.assertIn('Flood rules: 16 entries', control['memoryNote'])

    def test_other_reductions_do_not_invent_capacity_notes(self):
        control = self.generate(['web.webconfig omitted to preserve the legacy portable ESP32 app slot'])
        self.assertNotIn('memoryNote', control)
        self.assertNotIn('memorySource', control)

    def test_indicator_canvas_tradeoff_preserves_psram_capacities(self):
        control = self.generate([
            'display.canvas limited to 320x320 internal pixels, scaled to the 480x480 panel; 350 contacts, 512 queued frames and all Full transports retained',
        ])
        self.assertIn('320x320', control['memoryNote'])
        self.assertIn('480x480 panel', control['memoryNote'])
        self.assertIn('350 contacts, 512 queued messages', control['memoryNote'])
        self.assertIn('full-length message previews', control['memoryNote'])
        self.assertEqual(control['memorySource'], 'a' * 40)
        with self.assertRaisesRegex(ValueError, 'Unrecognized capacity reduction'):
            self.generate(['display.canvas changed without numeric limits'])

    def test_unknown_capacity_formats_are_not_silently_discarded(self):
        with self.assertRaisesRegex(ValueError, 'Unrecognized capacity reduction'):
            self.generate(['companion.capacity changed without numeric limits'])

    def test_unqualified_or_unbound_notes_are_rejected(self):
        reduction = ['nRF52 Full: 256 offline frames normally; 128 while mOTA borrows queue storage']
        with self.assertRaisesRegex(ValueError, 'Unqualified target'):
            self.generate(reduction, verified=False)
        with self.assertRaisesRegex(ValueError, 'exact source commit'):
            self.generate(reduction, source='abcdef12')


if __name__ == '__main__':
    unittest.main()
