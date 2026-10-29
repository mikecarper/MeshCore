"""User-facing choices, recovery identity, and compiler-free offline preparation."""
import asyncio
from dataclasses import replace
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import lora_ota as ota
import ota_wizard as wizard
import ota_wizard_bundle as bundles
from test_ota_wizard_bundle import KEY, destination, image, signed_package

SOURCE = '22' * 32


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='meshcore-wizard-ui-')
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)

    def test_explicit_port_does_not_probe_or_prompt(self):
        with mock.patch.object(wizard, 'select', side_effect=AssertionError('unexpected prompt')):
            self.assertEqual(wizard.choose_port('/dev/ttyUSB0'), '/dev/ttyUSB0')

    def test_one_usb_device_is_selected_without_prompting(self):
        ports = SimpleNamespace(comports=lambda: [SimpleNamespace(
            device='/dev/ttyUSB0', description='Station G2', vid=0x10C4)])
        serial = ModuleType('serial')
        tools = ModuleType('serial.tools')
        tools.list_ports = ports
        serial.tools = tools
        with mock.patch.dict(sys.modules, {'serial': serial, 'serial.tools': tools}), \
                mock.patch.object(wizard, 'select', side_effect=AssertionError('unexpected prompt')):
            self.assertEqual(wizard.choose_port(None), '/dev/ttyUSB0')

    def test_menu_retries_invalid_selection_and_preserves_default(self):
        with mock.patch.object(wizard, 'ask', side_effect=['bad', '3', '2']), redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.select('Firmware', ['Published', 'Local']), 1)

    def test_windows_custom_build_maps_paths_into_wsl_and_excludes_credentials(self):
        source = self.cache/'source checkout'
        source.mkdir()
        (source/'build.sh').write_text('# fixture only\n')
        args = wizard.arguments(['--build', '--source-tree', str(source), '--cache', str(self.cache)])
        builds = []
        def process(command, **kwargs):
            if command[:4] == ['wsl.exe', '-e', 'wslpath', '-a']:
                mapped = '/mnt/source checkout' if Path(command[4]) == source else '/mnt/output cache'
                return SimpleNamespace(returncode=0, stdout=mapped+'\n')
            builds.append((command, kwargs))
            output = Path(kwargs['env']['OUTPUT_DIR'])
            (output/'firmware.bin').write_bytes(image().image)
            return SimpleNamespace(returncode=0)
        fake_os = SimpleNamespace(name='nt', environ={
            'MESHCORE_ADMIN_PASSWORD': 'never inherit', 'MESHCORE_OTA_PASSWORD': 'nor this',
            'PATH': 'tool path'})
        with mock.patch.object(wizard, 'os', fake_os), \
                mock.patch.object(wizard.shutil, 'which', return_value='wsl.exe'), \
                mock.patch.object(wizard.subprocess, 'run', side_effect=process), \
                redirect_stdout(io.StringIO()):
            built = wizard.custom_build(args, destination(), 'ikoka_stick_nrf_33dbm_repeater')
            wizard.custom_build(args, replace(destination(), platform='esp32'), 'heltec_v4_repeater')
        self.assertEqual(built.read_bytes(), image().image)
        self.assertEqual(len(builds), 2)
        command, kwargs = builds[0]
        self.assertEqual(command[:5], ['wsl.exe', '--cd', '/mnt/source checkout', '--exec', 'env'])
        self.assertIn('OUTPUT_DIR=/mnt/output cache', command)
        self.assertEqual(command[command.index('--build-profile')+1], 'auto')
        self.assertNotIn('--full-exact', command)
        esp_command = builds[1][0]
        self.assertEqual(esp_command[esp_command.index('--build-profile')+1], 'full')
        self.assertIn('--full-exact', esp_command)
        self.assertNotIn('MESHCORE_ADMIN_PASSWORD', kwargs['env'])
        self.assertNotIn('MESHCORE_OTA_PASSWORD', kwargs['env'])
        self.assertNotIn('never inherit', repr(command))

    def test_prepare_only_uses_local_files_without_usb_network_or_compiler(self):
        request, firmware, output = (self.cache/name for name in
                                     ('device.json', 'new.bin', 'update.ota.zip'))
        bundles.save_request(request, destination())
        firmware.write_bytes(image().image)
        argv = ['--prepare-only', '--full', '--request', str(request),
                '--firmware', str(firmware), '--output', str(output), '--cache', str(self.cache)]
        with mock.patch.object(wizard, 'ensure_dependencies', side_effect=AssertionError('USB bootstrap')), \
                mock.patch.object(wizard, 'choose_port', side_effect=AssertionError('USB probe')), \
                mock.patch('ota_wizard_catalog._open', side_effect=AssertionError('network')), \
                mock.patch.object(wizard.subprocess, 'run', side_effect=AssertionError('compiler')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.main(argv), 0)
        prepared_image, package = bundles.load_bundle(output, destination())
        self.assertEqual(prepared_image.image, image().image)
        self.assertTrue(package.info.is_full)

    def test_prepare_only_rejects_wrong_board_before_writing_bundle(self):
        request, firmware, output = (self.cache/name for name in
                                     ('device.json', 'new.bin', 'update.ota.zip'))
        bundles.save_request(request, destination())
        firmware.write_bytes(image(hw='wrong_board').image)
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            result = wizard.main(['--prepare-only', '--full', '--request', str(request),
                                  '--firmware', str(firmware), '--output', str(output),
                                  '--cache', str(self.cache)])
        self.assertEqual(result, 1)
        self.assertFalse(output.exists())

    def test_internal_flash_destination_does_not_silently_fall_back_to_full(self):
        from dataclasses import replace
        request, firmware, output = (self.cache/name for name in
                                     ('device.json', 'new.bin', 'update.ota.zip'))
        bundles.save_request(request, replace(destination(), external_storage=False))
        firmware.write_bytes(image().image)
        with mock.patch.object(wizard, 'find_builder', return_value=None), \
                mock.patch.object(wizard, 'install_builder', return_value=None), \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            result = wizard.main(['--prepare-only', '--request', str(request),
                                  '--firmware', str(firmware), '--output', str(output),
                                  '--cache', str(self.cache)])
        self.assertEqual(result, 1)
        self.assertFalse(output.exists())

    def test_prepare_only_preserves_signed_full_input_even_with_full_option(self):
        from dataclasses import replace
        live = replace(destination(), sd_storage=True)
        original = signed_package()
        request, firmware, output = (self.cache/name for name in
                                     ('device.json', 'signed-full.mota', 'update.ota.zip'))
        bundles.save_request(request, live)
        firmware.write_bytes(original.blob)
        for full in ([], ['--full']):
            argv = ['--prepare-only', '--request', str(request), '--firmware', str(firmware),
                    '--output', str(output), '--cache', str(self.cache), *full]
            with self.subTest(full=bool(full)), \
                    mock.patch.object(wizard, 'GitHubCatalog', side_effect=AssertionError('network')), \
                    mock.patch.object(wizard, 'find_builder', side_effect=AssertionError('compiler')), \
                    mock.patch.object(wizard, 'prepare_application', side_effect=AssertionError('repackage')), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(wizard.main(argv), 0)
            _, prepared = bundles.load_bundle(output, live)
            self.assertEqual(prepared, original)

    def link(self):
        return SimpleNamespace(source_key=SOURCE, open=mock.AsyncMock(), close=mock.AsyncMock(),
                               discover=mock.AsyncMock(return_value=[{'public_key': KEY, 'type': 2}]))

    def run_options(self, *more):
        return wizard.arguments(['--port', '/dev/ttyUSB0', '--mode', 'repeater', '--target', KEY,
                                 '--cache', str(self.cache), '--yes', *more])

    def fake_transport(self, link):
        module = ModuleType('ota_wizard_repeater')
        module.RepeaterTransport = mock.Mock(return_value=link)
        return mock.patch.dict(sys.modules, {'ota_wizard_repeater': module})

    def test_check_does_not_prepare_download_or_install_and_saves_no_password(self):
        link = self.link()
        session = SimpleNamespace(inspect=mock.AsyncMock(return_value=destination()),
                                  snapshot=mock.AsyncMock(), cleanup=mock.AsyncMock(return_value=[]),
                                  journal=self.cache/'session.json')
        with self.fake_transport(link), \
                mock.patch.object(wizard, 'DirectSession', return_value=session), \
                mock.patch.object(wizard, 'GitHubCatalog', side_effect=AssertionError('network')), \
                mock.patch.dict(os.environ, {'MESHCORE_ADMIN_PASSWORD': 'DoNotPersistThis'}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(asyncio.run(wizard.run(self.run_options('--check'))), 0)
        session.snapshot.assert_not_awaited()
        link.close.assert_awaited_once()
        for file in self.cache.rglob('*.json'):
            self.assertNotIn('DoNotPersistThis', file.read_text())

    def test_explicit_known_repeater_skips_discovery_but_still_inspects_authenticated_identity(self):
        link = self.link()
        link.discover.side_effect = AssertionError('unexpected discovery')
        session = SimpleNamespace(inspect=mock.AsyncMock(return_value=destination()),
                                  snapshot=mock.AsyncMock(), cleanup=mock.AsyncMock(return_value=[]),
                                  journal=self.cache/'session.json')
        with self.fake_transport(link), \
                mock.patch.object(wizard, 'DirectSession', return_value=session), \
                mock.patch.object(wizard, 'GitHubCatalog', side_effect=AssertionError('network')), \
                mock.patch.dict(os.environ, {'MESHCORE_ADMIN_PASSWORD': ''}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(asyncio.run(wizard.run(self.run_options(
                '--check', '--target-type', 'repeater'))), 0)
            sys.modules['ota_wizard_repeater'].RepeaterTransport.assert_called_once_with(
                '/dev/ttyUSB0', known_repeater=KEY)
        link.discover.assert_not_awaited()
        session.inspect.assert_awaited_once()
        session.snapshot.assert_not_awaited()
        link.close.assert_awaited_once()

    def test_known_repeater_requires_full_key_and_repeater_source_before_opening_usb(self):
        for extra, error in (([], 'requires --target'),
                             (['--target', 'abcd'], 'complete destination public key'),
                             (['--target', KEY, '--mode', 'companion'], 'USB repeater source')):
            options = wizard.arguments(['--port', '/dev/ttyUSB0', '--mode', 'repeater',
                                        '--target-type', 'repeater', '--cache', str(self.cache), *extra])
            with self.subTest(extra=extra), self.fake_transport(self.link()), \
                    mock.patch.object(wizard, 'DirectSession') as session, \
                    self.assertRaisesRegex(ota.OtaError, error):
                asyncio.run(wizard.run(options))
            session.assert_not_called()

    def test_live_full_option_retains_supplied_signed_mota_for_sd_destination(self):
        from dataclasses import replace
        live = replace(destination(), sd_storage=True)
        original = signed_package()
        firmware = self.cache/'signed-full.mota'
        firmware.write_bytes(original.blob)
        link = self.link()
        session = SimpleNamespace(inspect=mock.AsyncMock(return_value=live),
            snapshot=mock.AsyncMock(), install=mock.AsyncMock(return_value=live),
            cleanup=mock.AsyncMock(return_value=[]), journal=self.cache/'session.json')
        catalog = SimpleNamespace(choose_asset=mock.Mock(side_effect=ota.OtaError('offline')))
        with self.fake_transport(link), \
                mock.patch.object(wizard, 'DirectSession', return_value=session), \
                mock.patch.object(wizard, 'GitHubCatalog', return_value=catalog), \
                mock.patch.object(wizard, 'prepare_application', side_effect=AssertionError('repackage')), \
                mock.patch.object(wizard, 'find_builder', side_effect=AssertionError('compiler')), \
                mock.patch.dict(os.environ, {'MESHCORE_ADMIN_PASSWORD': 'not saved'}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(asyncio.run(wizard.run(self.run_options(
                '--firmware', str(firmware), '--full', '--no-bootloader'))), 0)
        session.install.assert_awaited_once_with(original, live)
        link.close.assert_awaited_once()

    def test_unfinished_recovery_cannot_change_a_different_source(self):
        link = self.link()
        journal = self.cache/'sessions'/(KEY+'.json')
        journal.parent.mkdir()
        journal.write_text(json.dumps({'format': 1, 'phase': 'armed', 'target_key': KEY,
                                       'source_key': '33'*32, 'settings': {}}))
        with self.fake_transport(link), \
                mock.patch.object(wizard, 'DirectSession') as session, \
                mock.patch.dict(os.environ, {'MESHCORE_ADMIN_PASSWORD': 'not saved'}), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(ota.OtaError, 'different source'):
            asyncio.run(wizard.run(self.run_options('--recover')))
        session.assert_not_called()
        link.close.assert_awaited_once()

    def test_invalid_recovery_identity_is_rejected_as_a_user_error(self):
        link = self.link()
        journal = self.cache/'sessions'/(KEY+'.json')
        journal.parent.mkdir()
        journal.write_text(json.dumps({'format': 1, 'phase': 'armed', 'target_key': KEY,
                                       'source_key': None, 'settings': {}}))
        with self.fake_transport(link), \
                mock.patch.object(wizard, 'DirectSession') as session, \
                mock.patch.dict(os.environ, {'MESHCORE_ADMIN_PASSWORD': 'not saved'}), \
                redirect_stdout(io.StringIO()), self.assertRaises(ota.OtaError):
            asyncio.run(wizard.run(self.run_options('--recover')))
        session.assert_not_called()
        link.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
