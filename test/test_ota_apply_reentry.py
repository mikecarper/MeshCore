"""Run production apply admission and preserve an already-armed OTA handoff."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def actual_harness(context=None, cli=None):
    if context is None:
        context = (ROOT / 'src/helpers/ota/OtaContext.h').read_text()
    if cli is None:
        cli = (ROOT / 'src/helpers/ota/OtaCli.cpp').read_text()
    apply = (ROOT / 'src/helpers/ota/OtaApply.cpp').read_text()
    methods = '\n'.join(extract_braced(context, signature) for signature in (
        'bool hwMatches(', 'bool apply_fetched(', 'bool apply_fetched_trusted_auto(',
        'bool apply_fetched_rescue(', 'bool apply_fetched_impl(',
        'bool apply_fetched_bootloader('))
    # Compile the exact applier entry and its early read failure. Successful
    # crypto/flash approval is a boundary stub; this suite must not be mistaken
    # for full package verification or physical bootloader qualification.
    entry = extract_braced(apply, 'bool ota_apply_mota_nrf52(OtaStoreFlashNrf52& store,')
    entry = entry[:entry.index('\n  MotaManifest m;')] + r'''
  if (!boundaryApply(st, msg)) return false;
  g_nrf52_apply_store = &store;
  return true;
}
'''
    code = (ROOT / 'test/fixtures/ota_apply_reentry.cpp').read_text(encoding='ascii')
    for key, value in {
        'ENTRY': entry,
        'REBOOT': extract_braced(
            apply, 'void ota_reboot_to_apply() {                   // public:'),
        'BOOT_REBOOT': extract_braced(apply, 'void ota_reboot_to_bootloader_update() {\n'),
        'METHODS': methods,
        'IS_CMD': extract_braced(cli, 'static bool is_cmd('),
        'INSTALL': extract_braced(cli, 'if (is_cmd(a, "install|apply|applydelta", &rest))'),
    }.items():
        code = code.replace('@' + key + '@', value)
    return code


class OtaApplyReentryTest(unittest.TestCase):
    def test_armed_application_and_bootloader_admission_preserves_handoff(self):
        self.run_harness(actual_harness())

    def test_application_guard_negative_control(self):
        self.run_without_guard('context', 'bool apply_fetched_impl(')

    def test_bootloader_guard_negative_control(self):
        self.run_without_guard('context', 'bool apply_fetched_bootloader(')

    def test_cli_guard_negative_control(self):
        self.run_without_guard('cli', 'if (is_cmd(a, "install|apply|applydelta", &rest))')

    def run_without_guard(self, owner, signature):
        context = (ROOT / 'src/helpers/ota/OtaContext.h').read_text()
        cli = (ROOT / 'src/helpers/ota/OtaCli.cpp').read_text()
        source = context if owner == 'context' else cli
        body = extract_braced(source, signature)
        guard = extract_braced(body, 'if (' + ('c.' if owner == 'cli' else '') + 'apply_pending')
        self.assertIn('bootloader_apply_pending', guard)
        self.assertIn('return', guard)
        without = source.replace(body, body.replace(guard, '', 1), 1)
        self.run_harness(actual_harness(
            without if owner == 'context' else context,
            without if owner == 'cli' else cli), expect_failure=True)

    def run_harness(self, code, expect_failure=False):
        variants = (
            ('rak', ('OTA_RAK_AUTO_STORE', 'OTA_INTERNAL_BOOTLOADER_UPDATE', 'RAK_4631'), 144, 19),
            ('rak_sd', ('OTA_RAK_AUTO_STORE', 'OTA_INTERNAL_BOOTLOADER_UPDATE', 'RAK_4631',
                        'OTA_SD_STORE', 'OTA_RAK15002_SD_STORE'), 192, 25),
            ('internal_rescue', (), 96, 13),
        )
        sanitizers = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='meshcore-ota-apply-reentry-') as directory:
            work = Path(directory)
            source = work / 'test.cpp'
            source.write_text(code, encoding='ascii')
            for name, macros, pending, healthy in variants:
                with self.subTest(variant=name):
                    binary = work / (name + ('.exe' if os.name == 'nt' else ''))
                    built = subprocess.run([
                        os.environ.get('CXX', 'c++'), '-std=c++17',
                        '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
                        *sanitizers, '-DNRF52_PLATFORM=1', '-DOTA_FLASH_STORE=1',
                        *['-D' + macro + '=1' for macro in macros],
                        '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'test'),
                        '-I' + str(ROOT / 'test/mocks'), str(source),
                        str(ROOT / 'src/helpers/ota/MotaContainer.cpp'), '-o', str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True,
                                            text=True, timeout=20)
                    if expect_failure:
                        self.assertNotEqual(tested.returncode, 0, 'unguarded path escaped regression')
                        # A compile failure or incidental sanitizer crash is
                        # not evidence that the intended guard was exercised.
                        self.assertIn('Assertion', tested.stderr)
                        self.assertNotIn('AddressSanitizer:', tested.stderr)
                        self.assertNotIn('runtime error:', tested.stderr)
                    else:
                        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                        self.assertIn(
                            f'PASS: {pending} armed admission cases; {healthy} first-apply/retry controls',
                            tested.stdout)


if __name__ == '__main__':
    unittest.main()
