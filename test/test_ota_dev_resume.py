"""Exercise actual dev-resume admission with real manager/internal nRF52 store."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/ota_dev_resume.cpp'


def actual_harness(cli=None):
    if cli is None:
        cli = (ROOT / 'src/helpers/ota/OtaCli.cpp').read_text(encoding='ascii')
    existing = (ROOT / 'test/fixtures/ota_store_flash_nrf52_hybrid/'
                'test_ota_store_flash_nrf52_hybrid.cpp').read_text(encoding='ascii')
    mappings = existing[existing.index('static constexpr uintptr_t FLASH_MAP_START'):
                        existing.index('static std::vector<uint8_t> patterned_container')]
    mappings += '\n' + extract_braced(existing, 'static void reset_target_memory()')
    apply = (ROOT / 'src/helpers/ota/OtaApply.cpp').read_text(encoding='ascii')
    code = FIXTURE.read_text(encoding='ascii')
    for key, value in {
        'STORE_CPP': str(ROOT / 'src/helpers/ota/OtaStoreFlashNrf52.cpp'),
        'MAPPINGS': mappings,
        'HELPERS': '\n'.join(extract_braced(cli, signature) for signature in (
            'static bool is_cmd(', 'static bool parse_hex_exact(', 'static char fstate_char(')),
        'RESUME': extract_braced(cli, 'if (is_cmd(d, "resume", &d))'),
        'REBOOT': extract_braced(apply, 'void ota_reboot_to_apply() {                   // public:'),
    }.items():
        code = code.replace('@' + key + '@', value)
    return code


def without_resume_guard():
    cli = (ROOT / 'src/helpers/ota/OtaCli.cpp').read_text(encoding='ascii')
    branch = extract_braced(cli, 'if (is_cmd(d, "resume", &d))')
    if 'if (c.apply_pending' in branch:
        guard = extract_braced(branch, 'if (c.apply_pending')
        if 'c.bootloader_apply_pending' not in guard:
            raise AssertionError('resume guard must cover both armed states')
        cli = cli.replace(branch, branch.replace(guard, '', 1), 1)
    return actual_harness(cli)


class OtaDevResumeTest(unittest.TestCase):
    def test_actual_armed_resume_preserves_store_and_handoff(self):
        self.run_harness(actual_harness())

    def test_missing_guard_reproduces_hybrid_handoff_loss(self):
        self.run_harness(without_resume_guard(), baseline=True)

    def test_missing_guard_fails_intended_armed_regression(self):
        self.run_harness(without_resume_guard(), negative=True)

    def run_harness(self, code, baseline=False, negative=False):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('a host C++17 compiler is required')
        variants = (('internal', ()), ('rak_internal_selection', ('OTA_RAK_AUTO_STORE',)))
        sanitize = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='ota-dev-resume-') as directory:
            work = Path(directory)
            source = work / 'test.cpp'
            source.write_text(code, encoding='ascii')
            for name, macros in variants:
                with self.subTest(variant=name):
                    binary = work / (name + ('.exe' if os.name == 'nt' else ''))
                    built = subprocess.run([
                        compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-sign-compare',
                        '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                        *sanitize, '-DNRF52_PLATFORM=1', '-DOTA_FLASH_STORE=1',
                        '-DOTA_HYBRID_RAM_STORE=1', '-DOTA_INTERNAL_BOOTLOADER_UPDATE=1',
                        '-DMOTA_NRF52_TEST_APP_BASE=0x00026000u',
                        '-DMOTA_NRF52_TEST_LAYOUT_STAGE_CEILING=0x000ED000u',
                        *['-D' + macro + '=1' for macro in macros],
                        '-I' + str(ROOT / 'test/fixtures/ota_store_flash_nrf52_hybrid/mocks'),
                        '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'test/mocks'), str(source),
                        *[str(ROOT / 'src/helpers/ota' / name) for name in (
                            'OtaManager.cpp', 'OtaProtocol.cpp', 'MotaContainer.cpp', 'MerkleTree.cpp')],
                        str(ROOT / 'src/Utils.cpp'), '-o', str(binary),
                    ], capture_output=True, text=True, timeout=90)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary), *(['baseline'] if baseline else [])],
                                            capture_output=True, text=True, timeout=60)
                    if negative:
                        self.assertEqual(tested.returncode, 1, tested.stdout + tested.stderr)
                        self.assertIn('strcmp(reply, "ERR another update is already armed") == 0', tested.stderr)
                        self.assertNotIn('AddressSanitizer:', tested.stderr)
                        self.assertNotIn('runtime error:', tested.stderr)
                    else:
                        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                        self.assertIn('reboot source A6 -> ED' if baseline else
                                      'PASS: 45 armed cases; 7 healthy controls', tested.stdout)


if __name__ == '__main__':
    unittest.main()
