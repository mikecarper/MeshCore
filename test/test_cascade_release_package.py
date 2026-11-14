#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import struct
import sys
import hashlib
from copy import deepcopy
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_cascade_release", ROOT / "scripts/package_cascade_release.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
from motalib import ENDF_LEN, FwIdent, Nrf52Layout, ensure_endf, ensure_nrf52_layout, target_id_for_env


class ReleaseQualificationTest(unittest.TestCase):
    def test_historical_stock_receipt_allows_only_newly_reviewed_aliases(self):
        from nrf52_embedded_bootloader import load_manifest, select_profile
        target = 'Heltec_t114_repeater_lora_ota_no_external_sensors'
        profile, parsed = select_profile(load_manifest(), target)
        current = package.embedded_stock_legacy_records(profile, parsed)
        old = deepcopy(current)
        item = next(row for row in old if 'heltec_mesh_solar' in row['profiles'])
        item['profiles'].remove('heltec_mesh_solar')
        item['hardware'].remove('Heltec_mesh_solar')
        metadata = {'stock_legacy': old, 'stock_legacy_offset': 4}
        self.assertTrue(package.validate_embedded_stock_metadata(metadata, profile, parsed))
        for fault in ('hash', 'board', 'invented_alias', 'missing_record'):
            bad = deepcopy(metadata)
            if fault == 'hash':
                bad['stock_legacy'][0]['image_sha256'] = 'a' * 64
            elif fault == 'board':
                bad['stock_legacy'][0]['hardware'].remove('Heltec_t114')
            elif fault == 'invented_alias':
                bad['stock_legacy'][0]['profiles'].append('invented')
            else:
                bad['stock_legacy'].pop()
            with self.subTest(fault=fault):
                self.assertFalse(package.validate_embedded_stock_metadata(bad, profile, parsed))

    def test_supplemental_source_receipt_is_exact_and_marks_preview(self):
        source, extra = 'a' * 40, 'b' * 40
        receipt = dict(source_commit=extra, directory='source-bbbbbbbb',
                       kind='nrf52_bootloader_preview', bootloader_release='2.4.13-preview.1',
                       hardware_tested=False, artifact_targets=['example-repeater-full-ota'])
        self.assertEqual(package.supplemental_sources({'supplemental_sources': [receipt]}, source),
                         {'source-bbbbbbbb': receipt})
        for field, value in (('source_commit', source), ('directory', '../bad'),
                             ('hardware_tested', True), ('kind', 'ordinary'),
                             ('artifact_targets', []), ('bootloader_release', '2.4.12')):
            bad = {**receipt, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'supplemental'):
                package.supplemental_sources({'supplemental_sources': [bad]}, source)
        with self.assertRaisesRegex(ValueError, 'supplemental'):
            package.supplemental_sources({'supplemental_sources': [receipt, receipt]}, source)
        candidate = {'source_commit': extra, 'artifact_target': receipt['artifact_targets'][0],
                     'platform': 'NRF52_PLATFORM', 'embedded_bootloader': {'hardware_tested': False}}
        receipts = {'source-bbbbbbbb': receipt}
        with patch.object(package, 'embedded_bootloader_variant', return_value='2.4.13-preview.1'):
            self.assertTrue(package.source_allowed(candidate, receipts, source))
            self.assertFalse(package.source_allowed({**candidate, 'platform': 'ESP32_PLATFORM'}, receipts, source))
            self.assertFalse(package.source_allowed({**candidate, 'artifact_target': 'other'}, receipts, source))
            self.assertFalse(package.source_allowed({**candidate, 'source_commit': 'c' * 40}, receipts, source))

    def test_supplemental_must_match_ordinary_packaged_identity_and_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / 'old'
            new = root / 'new'
            old.mkdir()
            new.mkdir()
            base = 'a' * 40
            ordinary = self.sensor_record(old, 'full', target='test_repeater')
            preview = self.sensor_record(new, 'full', target='test_repeater')
            preview['manifest']['source_commit'] = 'b' * 40
            package.validate_supplemental_matches([ordinary, preview], base)
            for changes in (dict(target_id=123), dict(app_base=0x27000),
                            dict(storage='external_qspi')):
                replacement = self.sensor_record(new, 'full', target='test_repeater', **changes)
                replacement['manifest']['source_commit'] = 'b' * 40
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'identity or flash layout'):
                    package.validate_supplemental_matches([ordinary, replacement], base)
            with self.assertRaisesRegex(ValueError, 'lacks its exact ordinary'):
                package.validate_supplemental_matches([preview], base)

    def test_portable_exclusions_require_a_complete_summary(self):
        self.assertEqual(package.portable_profile_exclusions({}), [])
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "matrix.log"
            status = {"log": str(log)}
            header = "2 standard ESP32 target(s) exceeded the portable OTA slot and were deferred to the expanded FULL pass:\n"
            for body in ("DEFERRED: one (standard)\n", header + "  one\n", header + "  one\n  one\n"):
                log.write_text(body)
                with self.assertRaisesRegex(ValueError, "exclusion summary"):
                    package.portable_profile_exclusions(status)
            log.write_text(header + "  one\n  two\nLogging matrix completed successfully.\n")
            self.assertEqual([r["target"] for r in package.portable_profile_exclusions(status)], ["one", "two"])

    def test_partial_matrix_requires_completion_and_an_explicit_opt_in(self):
        self.assertEqual(package.completed_matrix_failures({"state": "completed", "exit_code": "0"}), [])
        for state, code in (("running", "0"), ("starting", "0"), ("failed", "143")):
            with self.subTest(state=state, code=code), self.assertRaisesRegex(ValueError, "finished matrix"):
                package.completed_matrix_failures({"state": state, "exit_code": code}, True)
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "matrix.log"
            status = {"state": "failed", "exit_code": "1", "log": str(log)}
            with self.assertRaisesRegex(ValueError, "not completed successfully"):
                package.completed_matrix_failures(status)
            for body in ("", "Building a target\n", "Logging matrix completed with 2 failed build(s):\n  one (standard) -> /tmp/one.log\n"):
                log.write_text(body)
                with self.assertRaisesRegex(ValueError, "summary"):
                    package.completed_matrix_failures(status, True)
            log.write_text("Logging matrix completed with 2 failed build(s):\n"
                           "  one (standard) -> /tmp/one.log\n"
                           "  two (full-usb-wifi-ota) -> /tmp/two.log\n")
            failures = package.completed_matrix_failures(status, True)
            self.assertEqual([f["target"] for f in failures], ["one", "two"])
            self.assertEqual(failures[1]["profile"], "full-usb-wifi-ota")
            self.assertEqual(failures[1]["log_file"], "two.log")

    def manifest(self, target="test_repeater", **changes):
        return {"target": target, "platform": "ESP32_PLATFORM",
                "schema_version": 2, "verified": True,
                "ota_update_verified": True, "verification": [], **changes}

    def test_source_capability_cannot_qualify_infrastructure(self):
        with self.assertRaisesRegex(ValueError, "wireless updater"):
            package.validate_manifest(self.manifest(ota_update_verified=False))

    def test_pending_hardware_qualification_cannot_be_published(self):
        for platform in ('NRF52_PLATFORM', 'ESP32_PLATFORM'):
            manifest = self.manifest(platform=platform, capabilities=['hardware.qualification.pending'])
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, 'hardware qualification pending'):
                package.validate_manifest(manifest)

    def test_usb_companion_is_accepted(self):
        package.validate_manifest(self.manifest("test_companion_radio_usb", ota_update_verified=False))

    def test_nrf52_sensor_profiles_require_actual_qualified_lora_receiver(self):
        for role in ('repeater', 'room_server', 'sensor'):
            for sensor in ('full', 'reduced'):
                manifest = self.manifest('test_' + role, platform='NRF52_PLATFORM', build_profile='auto',
                    capabilities=['sensor.profile.' + sensor, 'ota.update.lora'], ota_update_methods=['lora'],
                    verification=[dict(capability='ota.update.lora', present=True, source='linked image')])
                package.validate_manifest(manifest)
                self.assertEqual(package.nrf52_sensor_profile(manifest), sensor)
                self.assertIn('sensors + LoRa OTA', package.release_profile_label(manifest))
                self.assertEqual(package.category({'manifest': manifest}), 'lora-ota')
                for change in (dict(platform='ESP32_PLATFORM'), dict(target='test_companion_radio_full'),
                               dict(ota_update_methods=['bluetooth']), dict(verification=[]),
                               dict(verification=[dict(capability='ota.update.lora', present=True, source='filename')])):
                    with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'qualified nRF52'):
                        package.validate_manifest({**manifest, **change})
                with self.assertRaisesRegex(ValueError, 'ambiguous'):
                    package.validate_manifest({**manifest, 'capabilities': manifest['capabilities'] +
                        ['sensor.profile.' + ('reduced' if sensor == 'full' else 'full')]})

    def test_full_companion_requires_linked_source_implementation(self):
        manifest = self.manifest("test_companion_radio_full", ota_update_verified=False)
        with self.assertRaisesRegex(ValueError, "MOTA sending"):
            package.validate_manifest(manifest)
        manifest["verification"] = [{"capability": name, "present": True} for name in (
            "companion.usb_mota_source", "companion.mota_sender", "companion.temp_radio",
            "companion.ota_cli", "companion.wifi_ota_seeder")]
        package.validate_manifest(manifest)
        manifest["verification"][1]["present"] = False
        with self.assertRaisesRegex(ValueError, "MOTA sending"):
            package.validate_manifest(manifest)

    def sensor_record(self, directory, sensor, *, target='test_repeater', target_id=None,
                      hw_id='TEST', app_base=0x26000, flags=0x10, fw_version=0x01110107,
                      version='v1.17.1.7-dev-deadbeef', storage='internal_flash_and_retained_ram',
                      extra_application=b'', artifact_variant=''):
        artifact_target = target + artifact_variant + '-' + sensor + '-ota'
        stem = artifact_target + '-' + version
        application, _ = ensure_endf(ensure_nrf52_layout(b'OTA: status\0invalid in-place patch geometry\0'
                                                       + extra_application,
            Nrf52Layout(app_base, 0xED000, 0xED000, flags)),
            FwIdent(fw_version, target_id_for_env(target) if target_id is None else target_id, hw_id))
        path = directory / (stem + '.zip')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('application.bin', application)
            archive.writestr('manifest.json', json.dumps({'manifest': {
                'application': {'bin_file': 'application.bin', 'dat_file': 'application.dat'}}}))
            archive.writestr('application.dat', b'fixture init')
        manifest = self.manifest(target, artifact_target=artifact_target,
            platform='NRF52_PLATFORM', build_profile='auto', capabilities=['sensor.profile.' + sensor,
                'ota.update.lora'], ota_update_methods=['lora'],
            verification=[dict(capability='ota.update.lora', present=True, source='linked image')],
            ota_update_requirements={'lora': {'storage': storage}})
        return {'manifest': manifest, 'files': [path]}

    def sensor_package_fixture(self, directory, target, sensor, *, external=False,
                               merged_bridge=False, extra_capabilities=()):
        """Write fictional, structurally valid packages for the real CLI path."""
        record = self.sensor_record(directory, sensor, target=target,
            version='v1.17.1.5-halo-keymind-cascade-dev-deadbeef',
            flags=0x04 if external else 0x10,
            storage='external_qspi' if external else 'internal_flash_and_retained_ram',
            extra_application=b'meshcore.capability.rs232.merged.v1\0' if merged_bridge else b'')
        manifest = record['manifest']
        manifest['capabilities'].extend(extra_capabilities)
        if merged_bridge:
            for capability, source, evidence in (
                    ('bridge.rs232', 'linked image', '_ZN11RS232Bridge5beginEv'),
                    ('bridge.rs232.merged', 'packaged application', 'meshcore.capability.rs232.merged.v1')):
                manifest['capabilities'].append(capability)
                manifest['verification'].append(dict(capability=capability, source=source,
                                                     present=True, evidence=evidence))
        path = record['files'][0]
        with zipfile.ZipFile(path) as archive:
            application = archive.read('application.bin')
        self.assertLessEqual(len(application), 256)
        uf2 = (struct.pack('<8I', 0x0A324655, 0x9E5D5157, 0x2000, 0x26000,
                           256, 0, 1, 0xADA52840)
               + application.ljust(256, b'\xff') + b'\0' * 220
               + struct.pack('<I', 0x0AB16F30))
        path.with_suffix('.uf2').write_bytes(uf2)
        path.with_suffix('.capabilities.json').write_text(json.dumps(record['manifest']))
        files = [path, path.with_suffix('.uf2'), path.with_suffix('.capabilities.json')]
        proof = dict(schema_version=1, passed=True, available_internal_bytes=100000,
                     required_heap_bytes=10000, largest_internal_region_bytes=100000,
                     required_contiguous_bytes=5120, elf_sha256='a' * 64,
                     files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
        path.with_suffix('.memory.json').write_text(json.dumps(proof))
        return record['manifest']

    def embedded_sensor_record(self, directory, sensor, *, target='RAK_4631_repeater',
                               candidate=None, **options):
        import nrf52_embedded_bootloader as bootloader
        profile, _ = bootloader.select_profile(bootloader.load_manifest(), target)
        candidate = b'\xa5' * bootloader.BOOT_SIZE if candidate is None else candidate
        prefix = b'OTA: status\0invalid in-place patch geometry\0'
        padding = b'\0' * (-len(prefix) % 4)
        item = self.sensor_record(directory, sensor, target=target,
            extra_application=padding + candidate + b'BL embedded 2.4.12\0',
            artifact_variant='-with-bootloader-2.4.12', **options)
        manifest = item['manifest']
        manifest['capabilities'].append('bootloader.embedded')
        manifest['verification'].append(dict(capability='bootloader.embedded', present=True,
            source='packaged application', evidence='exact pinned candidate in packaged application'))
        manifest['embedded_bootloader'] = dict(release=bootloader.VERSION, profile=profile['id'],
            manifest_sha256=bootloader.MANIFEST_SHA256, asset_sha256=profile['files']['zip']['sha256'],
            candidate_sha256=hashlib.sha256(candidate).hexdigest(), legacy_sha256=None,
            image_bytes=bootloader.BOOT_SIZE, app_offset=len(prefix) + len(padding),
            activation='ota bootloader update', automatic_delay_seconds=70)
        return item

    def test_ordinary_and_embedded_pairs_coexist_without_satisfying_each_other(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            ordinary = [self.sensor_record(directory, sensor, target='RAK_4631_repeater')
                        for sensor in ('full', 'reduced')]
            embedded = [self.embedded_sensor_record(directory, sensor) for sensor in ('full', 'reduced')]
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            for inputs in (ordinary + embedded, list(reversed(ordinary + embedded))):
                package.validate_nrf52_sensor_pairs(inputs)
            self.assertEqual(package.embedded_bootloader_variant(ordinary[0]['manifest']), '')
            self.assertEqual(package.embedded_bootloader_variant(embedded[0]['manifest']), '2.4.12')
            self.assertIn('OTAFIX 2.4.12 bootloader', package.release_profile_label(embedded[0]['manifest']))
            for wrong in (ordinary + embedded[:1], ordinary[:1] + embedded,
                          [ordinary[0], embedded[1]], ordinary + embedded + embedded[:1]):
                with self.subTest(count=len(wrong)), self.assertRaisesRegex(ValueError, 'exactly one full'):
                    package.validate_nrf52_sensor_pairs(wrong)
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_preview_metadata_requires_an_explicit_untested_label(self):
        import nrf52_embedded_bootloader as bootloader
        with tempfile.TemporaryDirectory() as temp:
            item = self.embedded_sensor_record(Path(temp), 'full')
            manifest = item['manifest']
            target = 'MeshTracker_X1_repeater_lora_ota_no_external_sensors'
            profile, _ = bootloader.select_profile(
                bootloader.load_manifest(version=bootloader.PREVIEW_VERSION), target)
            manifest['target'] = target
            manifest['artifact_target'] = target + '-with-bootloader-2.4.13-preview.1-full-ota'
            manifest['embedded_bootloader'].update(
                release=bootloader.PREVIEW_VERSION, profile=profile['id'],
                manifest_sha256=bootloader.PREVIEW_MANIFEST_SHA256,
                asset_sha256=profile['files']['zip']['sha256'], hardware_tested=False)
            self.assertEqual(package.embedded_bootloader_variant(manifest), bootloader.PREVIEW_VERSION)
            self.assertIn('not hardware tested', package.release_profile_label(manifest))
            manifest['embedded_bootloader']['hardware_tested'] = True
            with self.assertRaisesRegex(ValueError, 'pinned release'):
                package.embedded_bootloader_variant(manifest)

    def test_embedded_metadata_requires_exact_pinned_profile_and_packaged_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            item = self.embedded_sensor_record(Path(temp), 'full')
            package.validate_manifest(item['manifest'])
            for key, value in (('release', '2.4.11'), ('profile', 'another_board'),
                    ('manifest_sha256', 'b' * 64), ('asset_sha256', 'c' * 64),
                    ('candidate_sha256', 'A' * 64), ('image_bytes', 40959),
                    ('app_offset', -4), ('app_offset', 1), ('app_offset', True),
                    ('activation', 'automatic'), ('automatic_delay_seconds', 69),
                    ('legacy_sha256', 'd' * 64)):
                manifest = deepcopy(item['manifest'])
                manifest['embedded_bootloader'][key] = value
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'pinned release'):
                    package.validate_manifest(manifest)
            for fault in ('metadata', 'capability', 'proof', 'false_proof', 'wrong_proof', 'platform'):
                manifest = deepcopy(item['manifest'])
                if fault == 'metadata':
                    manifest.pop('embedded_bootloader')
                elif fault == 'capability':
                    manifest['capabilities'].remove('bootloader.embedded')
                elif fault == 'proof':
                    manifest['verification'].pop()
                elif fault == 'false_proof':
                    manifest['verification'][-1]['present'] = False
                elif fault == 'wrong_proof':
                    manifest['verification'][-1]['source'] = 'filename'
                else:
                    manifest['platform'] = 'ESP32_PLATFORM'
                with self.subTest(fault=fault), self.assertRaises(ValueError):
                    package.validate_manifest(manifest)

    def test_embedded_pair_checks_actual_candidate_marker_and_same_candidate_in_both_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            full = self.embedded_sensor_record(directory, 'full')
            reduced = self.embedded_sensor_record(directory, 'reduced', candidate=b'\x5a' * 40960)
            with self.assertRaisesRegex(ValueError, 'storage layout'):
                package.validate_nrf52_sensor_pairs([full, reduced])
            reduced = self.embedded_sensor_record(directory, 'reduced')
            for fault in ('hash', 'offset', 'marker'):
                damaged = deepcopy(full)
                if fault == 'hash':
                    damaged['manifest']['embedded_bootloader']['candidate_sha256'] = '0' * 64
                elif fault == 'offset':
                    damaged['manifest']['embedded_bootloader']['app_offset'] += 4
                else:
                    path = directory / 'damaged' / damaged['files'][0].name
                    path.parent.mkdir(exist_ok=True)
                    with zipfile.ZipFile(full['files'][0]) as source, zipfile.ZipFile(path, 'w') as out:
                        for name in source.namelist():
                            data = source.read(name)
                            if name == 'application.bin':
                                identity = package.parse_endf_ident(data)
                                data, _ = ensure_endf(data[:-ENDF_LEN].replace(
                                    b'BL embedded 2.4.12', b'NO embedded 2.4.12'), identity)
                            out.writestr(name, data)
                    damaged['files'] = [path]
                with self.subTest(fault=fault), self.assertRaisesRegex(ValueError, 'differs from qualification'):
                    package.validate_nrf52_sensor_pairs([damaged, reduced])

    def test_qspi_retirement_requires_matching_bootloader_variant(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            legacy_target = 'Xiao_nrf52_room_server'
            primary_target = legacy_target + '_lora_ota'
            legacy = [self.sensor_record(directory, sensor, target=legacy_target) for sensor in ('full', 'reduced')]
            primary = [self.sensor_record(directory, sensor, target=primary_target, storage='external_qspi')
                       for sensor in ('full', 'reduced')]
            embedded_legacy = [self.embedded_sensor_record(directory, sensor, target=legacy_target)
                               for sensor in ('full', 'reduced')]
            embedded_primary = [self.embedded_sensor_record(directory, sensor, target=primary_target,
                storage='external_qspi') for sensor in ('full', 'reduced')]
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            for mixed in (legacy + embedded_primary, embedded_legacy + primary):
                with self.assertRaisesRegex(ValueError, 'qualified external-QSPI Full/Reduced pair'):
                    package.omit_retired_onboard_qspi_records(mixed)
            inputs = legacy + embedded_primary + embedded_legacy + primary
            self.assertCountEqual(package.omit_retired_onboard_qspi_records(inputs), primary + embedded_primary)
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_local_release_cannot_drop_or_forge_its_bootloader_receipts(self):
        from build_local_release import validate_embedded_bootloader_records
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            folder = directory / 'firmware/lora-ota'
            folder.mkdir(parents=True)
            ordinary = [self.sensor_record(folder, sensor, target='RAK_4631_repeater')
                        for sensor in ('full', 'reduced')]
            embedded = [self.embedded_sensor_record(folder, sensor) for sensor in ('full', 'reduced')]
            source, version = 'deadbeef' + 'a' * 32, 'v1.17.1.7-dev'
            records = ordinary + embedded
            standalone = directory / 'nrf52-bootloaders'
            standalone.mkdir()
            (standalone / 'verified-inventory.json').write_text('{}')
            receipt = validate_embedded_bootloader_records(ordinary, embedded)
            catalog_receipt = {'kind': 'nrf52-standalone-bootloader-catalog', 'version': '2.4.12'}
            manifest = dict(format='meshcore-local-release-v1', source_commit=source,
                firmware_version=version, profile='cascade', publication='local-only',
                radio=dict(frequency_mhz=910.525, bandwidth_khz=62.5, spreading_factor=7, coding_rate=5),
                firmware_target_count=4, migration_board_role_count=0,
                nrf52_bootloader_variants=receipt, nrf52_bootloaders=catalog_receipt,
                firmware=[dict(target=item['manifest']['target'], artifact_target=item['manifest']['artifact_target'],
                    platform='NRF52_PLATFORM', profile='auto',
                    embedded_bootloader=item['manifest'].get('embedded_bootloader'),
                    files=[str(p.relative_to(directory)) for p in item['files']]) for item in records])
            bundle = directory / ('esp32-partition-migration-' + version + '-deadbeef-release.zip')
            with zipfile.ZipFile(bundle, 'w') as archive:
                archive.writestr('release-manifest.json', '{}')
            def write_inventory():
                (directory / 'manifest.json').write_text(json.dumps(manifest))
                (directory / 'SHA256SUMS.txt').write_text(''.join(
                    hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(directory)) + '\n'
                    for path in sorted(directory.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS.txt'))
            with patch.object(package, 'collect_artifacts', return_value=records), \
                    patch('package_esp32_partition_migration.BOARDS', {}), \
                    patch('package_nrf52_bootloaders.validate_bootloader_catalog', return_value=catalog_receipt):
                write_inventory()
                qualified, extras, _ = package.collect_local_release(directory, version, source)
                self.assertEqual(qualified, records)
                self.assertIn(standalone / 'verified-inventory.json', extras)
                for fault in ('drop_variants', 'drop_catalog', 'drop_both', 'wrong_variants', 'wrong_catalog', 'entry'):
                    saved = deepcopy(manifest)
                    if fault in ('drop_variants', 'drop_both'):
                        manifest.pop('nrf52_bootloader_variants')
                    if fault in ('drop_catalog', 'drop_both'):
                        manifest.pop('nrf52_bootloaders')
                    if fault == 'wrong_variants':
                        manifest['nrf52_bootloader_variants']['firmware_count'] += 1
                    elif fault == 'wrong_catalog':
                        manifest['nrf52_bootloaders'] = {'version': '2.4.11'}
                    elif fault == 'entry':
                        manifest['firmware'][2]['embedded_bootloader'] = None
                    write_inventory()
                    with self.subTest(fault=fault), self.assertRaisesRegex(ValueError,
                            'inventor|manifest disagrees'):
                        package.collect_local_release(directory, version, source)
                    manifest = saved

    def matrix_package_command(self, directory):
        commit = 'deadbeef' + '0' * 32
        label = 'v1.17.1.5-halo-keymind-cascade-dev'
        status = directory / 'status'
        status.write_text(f'state=completed\nexit_code=0\nworking_directory={directory}\n'
                          f'output_directory=input\nsource_commit={commit}\n'
                          f'firmware_version={label}\nfirmware_profile=cascade\n'
                          'radio_frequency=910.525\nradio_bandwidth=62.5\nradio_sf=7\nradio_cr=5\n')
        return [sys.executable, str(ROOT / 'scripts/package_cascade_release.py'),
                '--input', str(directory / 'input'), '--output', str(directory / 'stage'),
                '--build-status', str(status), '--commit', commit]

    def test_direct_matrix_packaging_omits_resumed_onboard_qspi_alternatives(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            inputs = directory / 'input'
            inputs.mkdir()
            retired = []
            expected = []
            cases = [(target, target + '_lora_ota') for target in (
                'solarxiao_30S_room_server', 'solarxiao_33S_room_server', 'Xiao_nrf52_room_server',
                'ikoka_nano_nrf_33dbm_room_server', 'ikoka_stick_nrf_22dbm_room_server',
                'ikoka_handheld_nrf_e22_30dbm_room_server', 'LilyGo_T-Echo_room_server',
                'SenseCap_Solar_room_server', 'ThinkNode_M1_room_server', 'ThinkNode_M6_room_server',
                'WioTrackerL1_room_server', 'LilyGo_T-Echo-Lite_room_server', 'LilyGo_T-Echo-Lite_repeater')]
            cases += [(target + '_lora_ota_no_external_sensors', target) for target in (
                'LilyGo_T-Echo_repeater', 'SenseCap_Solar_repeater', 'ThinkNode_M1_repeater',
                'ThinkNode_M6_repeater', 'WioTrackerL1_repeater')]
            cases.append(('Xiao_nrf52_repeater_bridge_rs232', 'Xiao_nrf52_repeater'))
            for target, primary in cases:
                for sensor in ('full', 'reduced'):
                    retired.append(self.sensor_package_fixture(inputs, target, sensor))
                    expected.append(self.sensor_package_fixture(inputs, primary, sensor, external=True,
                                                               merged_bridge=primary == 'Xiao_nrf52_repeater'))
            for target in ('muzi_base_uno_room_server', 'RAK_3401_room_server',
                           'Nano_G2_Ultra_room_server', 'LilyGo_T-Echo_Card_room_server'):
                for sensor in ('full', 'reduced'):
                    expected.append(self.sensor_package_fixture(inputs, target, sensor))
            before = {p.name: p.read_bytes() for p in inputs.iterdir()}
            result = subprocess.run(self.matrix_package_command(directory), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({p.name: p.read_bytes() for p in inputs.iterdir()}, before)
            stage = directory / 'stage'
            manifests = [manifest for path in stage.glob('*/TARGET-MANIFEST.json')
                         for manifest in json.loads(path.read_text())]
            self.assertCountEqual([m['artifact_target'] for m in manifests],
                                  [m['artifact_target'] for m in expected])
            self.assertTrue(all(m['target'] not in {old['target'] for old in retired} for m in manifests))
            by_artifact = {m['artifact_target']: m for m in manifests}
            for manifest in expected:
                staged = by_artifact[manifest['artifact_target']]
                self.assertEqual(staged['target'], manifest['target'])
                self.assertEqual(staged['ota_update_requirements'], manifest['ota_update_requirements'])
                self.assertEqual(staged['capabilities'], manifest['capabilities'])
                for name in staged['files']:
                    matches = list(stage.glob('*/' + name))
                    self.assertEqual(len(matches), 1)
                    self.assertEqual(matches[0].read_bytes(), before[name])
            for manifest in retired:
                prefix = manifest['artifact_target'] + '-v'
                self.assertFalse(any(path.name.startswith(prefix) for path in stage.rglob('*')))

    def test_direct_matrix_packaging_rejects_legacy_without_matching_qspi_room_pair(self):
        for board, other in (('30S', '33S'), ('33S', '30S')):
            with self.subTest(board=board), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                inputs = directory / 'input'
                inputs.mkdir()
                for sensor in ('full', 'reduced'):
                    self.sensor_package_fixture(inputs, 'solarxiao_' + board + '_room_server', sensor)
                    self.sensor_package_fixture(inputs, 'solarxiao_' + other + '_room_server_lora_ota',
                                                sensor, external=True)
                before = {p.name: p.read_bytes() for p in inputs.iterdir()}
                result = subprocess.run(self.matrix_package_command(directory), capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('qualified external-QSPI Full/Reduced pair', result.stderr)
                self.assertFalse((directory / 'stage').exists())
                self.assertEqual({p.name: p.read_bytes() for p in inputs.iterdir()}, before)

    def test_direct_matrix_packaging_requires_xiao_runtime_bridge_in_each_profile(self):
        for missing_sensor in ('full', 'reduced'):
            with self.subTest(profile=missing_sensor), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                inputs = directory / 'input'
                inputs.mkdir()
                for sensor in ('full', 'reduced'):
                    self.sensor_package_fixture(inputs, 'Xiao_nrf52_repeater_bridge_rs232', sensor)
                    self.sensor_package_fixture(inputs, 'Xiao_nrf52_repeater', sensor, external=True,
                                                merged_bridge=sensor != missing_sensor)
                before = {p.name: p.read_bytes() for p in inputs.iterdir()}
                result = subprocess.run(self.matrix_package_command(directory), capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('verified runtime RS232 bridge', result.stderr)
                self.assertFalse((directory / 'stage').exists())
                self.assertEqual({p.name: p.read_bytes() for p in inputs.iterdir()}, before)

    def test_direct_matrix_packaging_rejects_experimental_qspi_even_with_complete_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            inputs = directory / 'input'
            inputs.mkdir()
            for sensor in ('full', 'reduced'):
                self.sensor_package_fixture(inputs, 'experimental_repeater_lora_ota', sensor, external=True,
                                            extra_capabilities=('hardware.qualification.pending',))
            before = {p.name: p.read_bytes() for p in inputs.iterdir()}
            result = subprocess.run(self.matrix_package_command(directory), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('hardware qualification pending', result.stderr)
            self.assertFalse((directory / 'stage').exists())
            self.assertEqual({p.name: p.read_bytes() for p in inputs.iterdir()}, before)

    def test_sensor_pair_requires_one_of_each_with_matching_packaged_identity_and_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            full = self.sensor_record(directory, 'full')
            reduced = self.sensor_record(directory, 'reduced')
            package.validate_nrf52_sensor_pairs([full, reduced])
            for records in ([full], [reduced], [full, reduced, reduced]):
                with self.subTest(count=len(records)), self.assertRaisesRegex(ValueError, 'exactly one full'):
                    package.validate_nrf52_sensor_pairs(records)
            for change in (dict(target='another_repeater'), dict(target_id=456), dict(hw_id='OTHER'),
                           dict(app_base=0x27000), dict(flags=0x20), dict(fw_version=0x01110108)):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'sensor pair'):
                    package.validate_nrf52_sensor_pairs([full, self.sensor_record(directory, 'reduced', **change)])
            reduced = self.sensor_record(directory, 'reduced')
            reduced['manifest']['ota_update_requirements']['lora']['storage'] = 'external_qspi'
            with self.assertRaisesRegex(ValueError, 'storage layout'):
                package.validate_nrf52_sensor_pairs([full, reduced])
            with self.assertRaisesRegex(ValueError, 'target ID disagrees with logical target'):
                package.validate_nrf52_sensor_pairs([self.sensor_record(directory, 'full', target_id=456),
                                                    self.sensor_record(directory, 'reduced', target_id=456)])

    def test_sensor_pair_filename_does_not_replace_metadata_or_packaged_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            full = self.sensor_record(directory, 'full')
            missing = {**full, 'manifest': {**full['manifest'], 'capabilities': ['ota.update.lora']}}
            with self.assertRaisesRegex(ValueError, 'lacks qualified metadata'):
                package.validate_nrf52_sensor_pairs([missing])
            missing['manifest']['artifact_target'] = 'test_repeater'
            with self.assertRaisesRegex(ValueError, 'lacks qualified metadata'):
                package.validate_nrf52_sensor_pairs([missing])
            full['manifest']['artifact_target'] = 'test_repeater-reduced-ota'
            with self.assertRaisesRegex(ValueError, 'disagrees with metadata'):
                package.validate_nrf52_sensor_pairs([full])
            full['manifest']['artifact_target'] = 'different_repeater-full-ota'
            with self.assertRaisesRegex(ValueError, 'filename disagrees'):
                package.validate_nrf52_sensor_pairs([full])
            full = self.sensor_record(directory, 'full')
            with zipfile.ZipFile(full['files'][0], 'w') as archive:
                archive.writestr('application.bin', b'no EndF identity or layout')
                archive.writestr('manifest.json', json.dumps({'manifest': {
                    'application': {'bin_file': 'application.bin'}}}))
            with self.assertRaisesRegex(ValueError, 'valid packaged EndF'):
                package.validate_nrf52_sensor_pairs([full])
            # The new policy is not retroactively applied to historical files.
            package.validate_nrf52_sensor_pairs([{'manifest': self.manifest('legacy_repeater',
                platform='NRF52_PLATFORM'), 'files': [directory / 'legacy_repeater-ota-v1.0.zip']}])

    def test_collect_artifacts_always_enforces_sensor_pair_even_for_partial_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            record = self.sensor_record(directory, 'full')
            stem = record['files'][0].stem
            (directory / (stem + '.uf2')).write_bytes(b'validated fixture UF2')
            (directory / (stem + '.capabilities.json')).write_text(json.dumps(record['manifest']))
            memory = dict(passed=True, available_internal_bytes=100000,
                          required_heap_bytes=10000, elf_sha256='a' * 64)
            with patch.object(package, 'check_uf2'), patch.object(package, 'validate_package', return_value=memory), \
                 patch.object(package, 'validate_artifact_notice', return_value=None):
                with self.assertRaisesRegex(ValueError, 'exactly one full'):
                    package.collect_artifacts(directory, 'v1.17.1.7-dev-deadbeef')
                reduced = self.sensor_record(directory, 'reduced')
                other = reduced['files'][0].stem
                (directory / (other + '.uf2')).write_bytes(b'validated fixture UF2')
                (directory / (other + '.capabilities.json')).write_text(json.dumps(reduced['manifest']))
                self.assertEqual(len(package.collect_artifacts(directory, 'v1.17.1.7-dev-deadbeef')), 2)
    def test_missing_qualification_and_mixed_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            stem = "test_companion_radio_usb-v1.0-deadbeef"
            (directory / (stem + ".bin")).write_bytes(b"firmware")
            with self.assertRaisesRegex(ValueError, "without qualification"):
                package.collect_artifacts(directory, "v1.0-deadbeef")
            (directory / (stem + ".capabilities.json")).write_text(json.dumps(self.manifest("test_companion_radio_usb")))
            with self.assertRaisesRegex(ValueError, "mixed-version"):
                package.collect_artifacts(directory, "v2.0-deadbeef")
            with self.assertRaisesRegex(ValueError, "pair incomplete"):
                package.collect_artifacts(directory, "v1.0-deadbeef")

    def test_stages_named_prerelease_and_checksums_only_after_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            inputs = directory / "input"
            inputs.mkdir()
            commit = "deadbeef" + "0" * 32
            label = "v1.17.1.5-halo-keymind-cascade-dev"
            stem = "test_companion_radio_usb-" + label + "-deadbeef"
            manifest = self.manifest("test_companion_radio_usb", ota_update_verified=False,
                                     artifact_target="test_companion_radio_usb", build_profile="standard")
            (inputs / (stem + ".capabilities.json")).write_text(json.dumps(manifest))
            (inputs / (stem + ".bin")).write_bytes(b"test application")
            (inputs / (stem + "-merged.bin")).write_bytes(b"test merged image")
            proof = dict(schema_version=1, passed=True, available_internal_bytes=80000,
                         required_heap_bytes=50000, largest_internal_region_bytes=80000,
                         required_contiguous_bytes=5120, elf_sha256="a" * 64,
                         files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.iterdir()})
            (inputs / (stem + ".memory.json")).write_text(json.dumps(proof))
            status = directory / "status"
            settings = (f"exit_code=0\nworking_directory={directory}\noutput_directory=input\n"
                        f"source_commit={commit}\nfirmware_version={label}\nfirmware_profile=cascade\n"
                        "radio_frequency=910.525\nradio_bandwidth=62.5\nradio_sf=7\nradio_cr=5\n")
            status.write_text("state=running\n" + settings)
            command = [sys.executable, str(ROOT / "scripts/package_cascade_release.py"),
                       "--input", str(inputs), "--output", str(directory / "stage"),
                       "--build-status", str(status), "--commit", commit]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "stage").exists())
            status.write_text("state=completed\n" + settings)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((directory / "stage/release-plan.json").read_text())
            self.assertEqual(plan["groups"][0]["tag"], label + "-deadbeef")
            self.assertTrue(plan["groups"][0]["prerelease"])
            assets = directory / "stage/companion"
            picker = (directory / "stage/FIRMWARE-PICKER-1.17.1.5.html").read_text()
            self.assertIn(f'companion/{stem}.bin', picker)
            self.assertNotIn("/releases/download/", picker)
            self.assertIn("companion/FULL-COMPANION-FEATURES.md", picker)
            self.assertIn("/releases/download/", (assets / "FIRMWARE-PICKER-1.17.1.5.html").read_text())
            self.assertIn(stem + ".bin", (assets / "TARGET-MANIFEST.tsv").read_text())
            self.assertIn("910.525", (assets / "BUILD-NOTES.txt").read_text())
            for line in (assets / "SHA256SUMS.txt").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((assets / name).read_bytes()).hexdigest(), digest)

            # A finished matrix can publish good files while explicitly
            # retaining the failed attempts and the real nonzero exit code.
            log = directory / "matrix.log"
            log.write_text("1 standard ESP32 target(s) exceeded the portable OTA slot and were deferred to the expanded FULL pass:\n"
                           "  large_repeater\n"
                           "Logging matrix completed with 1 failed build(s):\n"
                           "  missing_repeater (standard) -> /tmp/missing.log\n")
            status.write_text("state=failed\n" + settings.replace("exit_code=0", "exit_code=1")
                              + f"log={log}\n")
            partial_command = command.copy()
            partial_command[partial_command.index("--output") + 1] = str(directory / "partial")
            result = subprocess.run(partial_command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "partial").exists())
            result = subprocess.run(partial_command + ["--allow-partial"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((directory / "partial/release-plan.json").read_text())
            self.assertEqual(plan["matrix"]["exit_code"], 1)
            self.assertEqual(plan["matrix"]["failures"][0]["target"], "missing_repeater")
            assets = directory / "partial/companion"
            self.assertIn("Partial matrix", (assets / "BUILD-NOTES.txt").read_text())
            self.assertIn("missing_repeater", (assets / "BUILD-FAILURES.md").read_text())
            self.assertEqual(plan["matrix"]["portable_profile_exclusions"][0]["target"], "large_repeater")
            self.assertIn("large_repeater", (assets / "PORTABLE-PROFILE-EXCLUSIONS.md").read_text())
            self.assertIn("Portable image limits", (assets / "BUILD-NOTES.txt").read_text())
            self.assertEqual(len(list(assets.iterdir())), plan["groups"][0]["asset_count"])
            checksums = (assets / "SHA256SUMS.txt").read_text()
            self.assertIn("BUILD-FAILURES.json", checksums)
            self.assertIn("PORTABLE-PROFILE-EXCLUSIONS.json", checksums)
            for line in checksums.splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((assets / name).read_bytes()).hexdigest(), digest)

            # Opting in to holes never authorizes publishing bad firmware.
            (inputs / (stem + ".bin")).write_bytes(b"corrupted")
            partial_command[partial_command.index("--output") + 1] = str(directory / "bad")
            result = subprocess.run(partial_command + ["--allow-partial"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / "bad").exists())


if __name__ == "__main__":
    unittest.main()
