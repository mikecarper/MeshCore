"""Run the production shared profile command parser and persistence with a memory FS."""
from pathlib import Path
import itertools
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RadioProfileCLITest(unittest.TestCase):
    def test_repeater_retains_radio2_presets_schedules_and_transactions(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='profile-repeater-') as work:
            binary = Path(work) / 'repeater'
            compiled = subprocess.run([
                compiler, '-std=c++17', '-Os', '-Wall', '-Wextra',
                '-DMESH_CLIENT_REPEATER_ONLY=1', '-DMESH_ENABLE_FLEET_CONTROL=0',
                '-DMESH_ENABLE_CARRIER_TOOLS=0',
                '-I', str(ROOT/'test/fixtures/radio_profiles/mocks'),
                '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                str(ROOT/'src/helpers/RadioProfileCLI.cpp'),
                str(ROOT/'test/fixtures/radio_profiles/cli_test.cpp'),
                '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for scenario in (None, 'permanent_order', 'temporary_boundary', 'lease_creation',
                             'save_failure', 'schedule_permutations', 'storage_isolation',
                             'relative_schedule', 'spectrum_overlap', 'mediamesh', 'media_scope'):
                with self.subTest(scenario=scenario):
                    ran = subprocess.run([str(binary)] + ([scenario] if scenario else []),
                                         capture_output=True, text=True, timeout=10)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            symbols = subprocess.check_output(['nm', '-C', str(binary)], text=True)
            self.assertNotIn('RadioProfileCLI::stageChannelPreset(', symbols)
            self.assertNotIn('RadioProfileCLI::finishChannelPreset(', symbols)

    def test_channel_preset_preserves_secondary_owners(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix='profile-channel-owner-') as work:
            binary = Path(work) / 'channel-owner'
            compiled = subprocess.run([
                compiler, '-std=c++17', '-Wall', '-Wextra',
                '-DMESH_ENABLE_MESHTEXT_CHAT=1',
                '-I', str(ROOT/'test/fixtures/radio_profiles/mocks'),
                '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                str(ROOT/'src/helpers/RadioProfileCLI.cpp'),
                str(ROOT/'test/fixtures/radio_profiles/cli_test.cpp'),
                '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            subprocess.run([str(binary), 'channel_preset'], check=True, timeout=10)

    def test_all_companions_keep_rf_presets_without_unused_region_code(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        code = r'''
#define main existing_cli_main
#include "test/fixtures/radio_profiles/cli_test.cpp"
#undef main
int main() {
  Fixture f;
  MediaScope scope;
  f.cli.setMediaMeshScopeHandler(MediaScope::update, &scope);
  f.cmd("set radio2 mediamesh");
  f.advance(2000);
  assert(f.radio.p.enabled() && fabsf(f.radio.p.secondary.params.freq - 911.150f) < 0.0001f);
#if defined(COMPANION_RADIO_FULL) || MESH_ROLE_COMPANION
  assert(!scope.commits && !scope.checks);
#else
  assert(scope.enabled && scope.durable && scope.commits);
#endif
  f.cmd("set radio2 off");
  assert(!scope.enabled);
  printf("%zu", sizeof(mesh::RadioProfileCLI));
}
'''
        sizes = {}
        with tempfile.TemporaryDirectory(prefix='profile-region-role-') as work:
            source = Path(work)/'role.cpp'
            source.write_text(code, encoding='ascii')
            roles = {'infrastructure': [], 'full': ['-DCOMPANION_RADIO_FULL=1'],
                     'ordinary': ['-DMESH_ROLE_COMPANION=1']}
            for role, flags in roles.items():
                companion = role != 'infrastructure'
                binary = Path(work)/role
                compiled = subprocess.run([
                    compiler, '-std=c++17', '-Os', '-ffunction-sections',
                    '-fdata-sections', '-Wl,--gc-sections',
                    '-DMESH_ENABLE_FLEET_CONTROL=0', '-DMESH_ENABLE_CARRIER_TOOLS=0',
                    '-DMESH_ENABLE_MESHTEXT_CHAT=0', '-DMESH_ENABLE_HOST_KISS=0',
                    '-DMESH_ENABLE_ETHERNET_TUNNEL=0', *flags,
                    '-I', str(ROOT), '-I', str(ROOT/'test/fixtures/radio_profiles/mocks'),
                    '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                    str(ROOT/'src/helpers/RadioProfileCLI.cpp'), str(source),
                    '-o', str(binary)], capture_output=True, text=True, timeout=60)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                sizes[role] = int(run.stdout)
                image = binary.read_bytes()
                for marker in (b'MediaMesh scope save pending', b'MediaMesh region unavailable or full'):
                    self.assertEqual(marker in image, not companion)
        for role in ('full', 'ordinary'):
            self.assertLess(sizes[role], sizes['infrastructure'])

    def test_commands_persistence_and_timers(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as work:
            # Compact STM32 images omit fleet staging. Both paths must preserve
            # local schedules, persistence retries and temporary lease bounds.
            for fleet_enabled, carrier_enabled in itertools.product((False, True), repeat=2):
                exe = Path(work) / f'profiles-{int(fleet_enabled)}-{int(carrier_enabled)}.exe'
                subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra',
                    f'-DMESH_ENABLE_FLEET_CONTROL={int(fleet_enabled)}',
                    f'-DMESH_ENABLE_CARRIER_TOOLS={int(carrier_enabled)}',
                    '-I', str(ROOT/'test/fixtures/radio_profiles/mocks'),
                    '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                    str(ROOT/'src/helpers/RadioProfileCLI.cpp'),
                    str(ROOT/'test/fixtures/radio_profiles/cli_test.cpp'),
                    '-o', str(exe)], check=True, capture_output=True, text=True)
                for scenario in (None, 'permanent_order', 'temporary_boundary', 'lease_creation',
                                 'save_failure', 'schedule_permutations', 'storage_isolation',
                                 'relative_schedule', 'spectrum_overlap', 'mediamesh', 'channel_preset',
                                 'media_scope'):
                    with self.subTest(fleet=fleet_enabled, carrier=carrier_enabled,
                                      scenario=scenario or 'commands_persistence_and_timers'):
                        subprocess.run([str(exe)] + ([scenario] if scenario else []), check=True)

if __name__ == '__main__':
    unittest.main()
