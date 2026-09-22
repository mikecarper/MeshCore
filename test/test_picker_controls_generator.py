import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('picker_controls', ROOT / 'scripts/generate_picker_controls.py')
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class PickerControlsTests(unittest.TestCase):
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
