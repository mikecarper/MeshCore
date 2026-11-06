#!/usr/bin/env python3
"""Exercise the production migration arm/tick state machine with host stubs.

The ARM SVC adapter is replaced by a failing trap, and the EndF stub refuses
installation before hardware access. Real CRC-bound installed manifests test
the version check; controlled digests exercise the exact candidate no-op and
reviewed-stock source gate (not the SHA-256 primitive itself).
This tests scheduling and cancellation, not cryptography, MBR copying or
power-loss recovery; those require separate qualification. The durable-store
boundary is modeled here and tested against real file faults separately.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
STUBS = ROOT / 'tools/mota/tests/migration_stubs'


class MigrationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='migration-runtime-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.binary = cls.compile(Path(cls.directory.name), STUBS / 'runtime.cpp')

    @staticmethod
    def compile(directory, source):
        binary = directory / ('runtime.exe' if os.name == 'nt' else 'runtime')
        subprocess.run([
            os.environ.get('CXX', 'g++'), '-std=c++17', '-O2', '-Wall', '-Wextra',
            '-DOTA_BOOTLOADER_MIGRATION', '-DNRF52_PLATFORM', '-DENABLE_OTA',
            '-DMOTA_NRF52_TEST_APP_BASE=0x26000',
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie',
            '-I', str(STUBS), '-I', str(ROOT / 'src'), '-I', str(ROOT / 'src/helpers/ota'),
            str(source), '-o', str(binary),
        ], check=True)
        return binary

    def run_scenario(self, scenario):
        subprocess.run([str(self.binary), scenario], check=True)

    def test_recovery_unknown_or_damaged_bootloaders_refuse_manual_and_automatic_commands(self):
        for scenario in ('automatic-recovery-prefix', 'automatic-recovery-suffix',
                         'automatic-unlabelled-loader', 'automatic-ambiguous-loader',
                         'automatic-foreign-loader', 'automatic-damaged-loader'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_reviewed_stock_loader_arms_manually_or_at_70_seconds_without_legacy_file(self):
        for scenario in ('stock-automatic', 'stock-manual'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_stock_hash_is_not_a_modern_identity_or_recovery_override(self):
        for scenario in ('stock-damaged-hash', 'stock-recovery', 'stock-modern-mismatch'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_same_firmware_remains_locked_after_a_cold_boot(self):
        for scenario in ('durable-same-firmware-cold-boot',
                         'durable-same-firmware-old-loader'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_new_image_classification_interruption_is_locked_before_reboot(self):
        self.run_scenario('previous-image-interrupted-classification')

    def test_a_different_full_firmware_hash_allows_a_new_automatic_check(self):
        for scenario in ('durable-different-firmware',
                         'durable-different-firmware-failure-marker'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_missing_or_failed_durable_state_refuses_a_genuine_update(self):
        for scenario in ('automatic-store-unavailable', 'automatic-store-null',
                         'automatic-invalid-application', 'automatic-store-write-failure'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_automatic_update_waits_70_seconds_then_observes_install_grace(self):
        for scenario in ('automatic-delay', 'automatic-arm-enabled'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_automatic_update_defers_until_radio_and_ota_are_idle(self):
        self.run_scenario('automatic-busy')

    def test_automatic_update_skips_same_or_newer_version_and_exact_image(self):
        for scenario in ('automatic-same-version', 'automatic-same-version-enabled',
                         'automatic-newer-version',
                         'automatic-exact-image'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_retained_failure_prevents_automatic_retry_but_allows_manual_retry(self):
        self.run_scenario('automatic-retained-failure')

    def test_manual_attempt_prevents_a_second_automatic_attempt(self):
        for scenario in ('manual-cancel-consumes-automatic',
                         'manual-failure-consumes-automatic'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_cancelled_automatic_update_is_not_rearmed(self):
        self.run_scenario('automatic-cancel')

    def test_invalid_confirmation_does_not_cancel_the_automatic_update(self):
        self.run_scenario('invalid-confirmation-preserves-automatic')

    def test_reboot_guard_is_written_through_softdevice_before_arming(self):
        self.run_scenario('guard-softdevice-enabled')

    def test_reboot_guard_read_errors_block_the_automatic_attempt(self):
        for scenario in ('automatic-status-read-failure', 'automatic-marker-read-failure'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_reboot_guard_api_failures_refuse_to_arm(self):
        for scenario in ('guard-softdevice-status-failure', 'guard-marker-read-failure',
                         'guard-clear-failure',
                         'guard-set-failure'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_reset_during_check_or_armed_attempt_cannot_create_an_update_loop(self):
        for scenario in ('automatic-interrupted-check', 'automatic-interrupted-arm'):
            with self.subTest(scenario=scenario):
                self.run_scenario(scenario)

    def test_wrong_confirmation_cannot_arm(self):
        self.run_scenario('wrong-confirmation')

    def test_already_installed_preserves_running_application(self):
        self.run_scenario('already-installed')

    def test_easy_update_needs_no_hash_and_preserves_install_preflight(self):
        self.run_scenario('easy-update')

    def test_same_version_is_idle_before_and_after_repeated_requests(self):
        self.run_scenario('same-version')

    def test_newer_preview_is_idle_and_cannot_be_downgraded(self):
        self.run_scenario('newer-version')

    def test_older_preview_is_still_eligible_for_the_stable_update(self):
        self.run_scenario('older-preview')

    def test_version_noop_requires_valid_matching_identity_and_continuity(self):
        for mutation in ('board', 'name', 'family', 'fwid', 'app', 'abi',
                         'continuity', 'crc'):
            with self.subTest(mutation=mutation):
                self.run_scenario('invalid-version-' + mutation)

    def test_negative_control_noop_must_not_arm_or_stop_softdevice(self):
        driver = (ROOT / 'src/helpers/ota/BootloaderMigration.cpp').read_text()
        changed = driver.replace('if (memcmp(digest, migration_candidate_sha256, sizeof(digest)) == 0) {',
                                 'if (false) {')
        self.assertNotEqual(driver, changed)
        harness = (STUBS / 'runtime.cpp').read_text().replace(
            '#include "helpers/ota/BootloaderMigration.cpp"', changed)
        with tempfile.TemporaryDirectory(prefix='migration-noop-negative-') as directory:
            directory = Path(directory)
            source = directory / 'runtime.cpp'
            source.write_text(harness)
            binary = self.compile(directory, source)
            result = subprocess.run([str(binary), 'already-installed'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('!bootloader_migration_pending()', result.stderr)

    def test_negative_control_version_noop_must_not_queue_an_install(self):
        driver = (ROOT / 'src/helpers/ota/BootloaderMigration.cpp').read_text()
        changed = driver.replace('if (installed_up_to_date()) {', 'if (false) {')
        self.assertNotEqual(driver, changed)
        harness = (STUBS / 'runtime.cpp').read_text().replace(
            '#include "helpers/ota/BootloaderMigration.cpp"', changed)
        with tempfile.TemporaryDirectory(prefix='migration-version-negative-') as directory:
            directory = Path(directory)
            source = directory / 'runtime.cpp'
            source.write_text(harness)
            binary = self.compile(directory, source)
            result = subprocess.run([str(binary), 'same-version'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('!bootloader_migration_pending()', result.stderr)

    def test_negative_control_automatic_check_must_lock_before_classification(self):
        driver = (ROOT / 'src/helpers/ota/BootloaderMigration.cpp').read_text()
        start = driver.index('void bootloader_migration_tick(')
        changed_tick = driver[start:].replace(
            'if (!lock_automatic_for_firmware()) {', 'if (false) {', 1)
        self.assertNotEqual(driver[start:], changed_tick)
        changed = driver[:start] + changed_tick
        harness = (STUBS / 'runtime.cpp').read_text().replace(
            '#include "helpers/ota/BootloaderMigration.cpp"', changed)
        with tempfile.TemporaryDirectory(prefix='migration-auto-lock-negative-') as directory:
            directory = Path(directory)
            source = directory / 'runtime.cpp'
            source.write_text(harness)
            binary = self.compile(directory, source)
            result = subprocess.run(
                [str(binary), 'previous-image-interrupted-classification'],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('durable_lock_present && store_writes == 1', result.stderr)

    def test_grace_and_radio_drain_are_both_required(self):
        self.run_scenario('grace-and-drain')

    def test_duplicate_arm_does_not_extend_deadline(self):
        self.run_scenario('duplicate-arm')

    def test_timeout_wins_over_becoming_idle(self):
        self.run_scenario('timeout')

    def test_ota_activity_cancels_and_requires_fresh_arm(self):
        self.run_scenario('ota-cancel-rearm')

    def test_ota_activity_wins_at_install_boundary(self):
        self.run_scenario('ota-cancel-at-install')

    def test_millisecond_wrap_preserves_grace(self):
        self.run_scenario('clock-wrap')

    def test_current_results_override_stale_retained_marker(self):
        self.run_scenario('stale-retained-result')


if __name__ == '__main__':
    unittest.main()
