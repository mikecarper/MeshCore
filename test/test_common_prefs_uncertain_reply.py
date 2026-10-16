"""Exercise real CommonCLI password and legacy save uncertainty replies."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_common_usb_debug import HARNESS
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def actual_harness():
    source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text(encoding='ascii')
    header = (ROOT / 'src/helpers/CommonCLI.h').read_text(encoding='ascii')
    fields = header.split('class NodePrefs :', 1)[1].split('private:', 1)[0]
    fields = '\n'.join(re.findall(
        r'^\s*(?:float|double|char|u?int(?:8|16|32)_t)\s+[^;]+;', fields, re.M))
    constants = '\n'.join(re.findall(
        r'^\s*#define\s+\w+\s+(?:0x[\dA-Fa-f]+|\d+)\s*$', header, re.M))
    constants += '\n' + '\n'.join(re.findall(
        r'^static const size_t LEGACY_\w+\s*=[^;]+;', source, re.M))
    constants += '\n' + re.search(
        r'^static constexpr uint8_t DEFAULT_ADVERT_LOC_POLICY\s*=[^;]+;',
        header, re.M).group()
    helpers = '\n'.join(extract_braced(source, signature) for signature in (
        'static bool bwMatches(', 'static bool isValidLoRaBandwidth(',
        'static float defaultLoRaBandwidth(', 'static void markDirectRetryPrefsValid(',
        'static void applyFloodRetryPreset(', 'static void applyDirectRetryPreset(',
        'static void setDefaultDirectRetryPrefs(', 'static bool directRetryPrefsValid('))
    methods = '\n'.join(extract_braced(source, signature) for signature in (
        'static const char* commonPrefsSaveResultName(',
        'bool CommonCLI::recoverCommonPrefsFiles(',
        'bool CommonCLI::saveCommonPrefsImageAtomically(',
        'void CommonCLI::savePrefs(FILESYSTEM*',
        'void CommonCLI::savePrefs(PrefsSaveRouting::Scope scope)',
        'bool CommonCLI::trySavePrefs(', 'void CommonCLI::loadPrefsInt('))

    # Reuse the established adapter, but not its USB-specific main or dynamic
    # schema tests. Production serializers and transaction code remain real.
    code = HARNESS[:HARNESS.index('// Execute the production dynamic schema')]
    code = code.replace('  void set(const char* config, char* reply) {',
                        '  void password(const char*, char*);\n'
                        '  void guestPassword(const char*, char*);\n'
                        '  void legacySet(const char*, char*);\n'
                        '  void set(const char* config, char* reply) {')
    code = '#include <cstdlib>\n#include <helpers/PrefsSaveReplyGuard.h>\n' + code
    text_helpers = (ROOT / 'src/helpers/TxtDataHelpers.cpp').read_text(encoding='ascii')
    code = code.replace('struct CommonCLI;\n',
                        'struct StrHelper { static void strncpy(char*, const char*, size_t); };\n'
                        + extract_braced(text_helpers, 'void StrHelper::strncpy(')
                        + '\nstruct CommonCLI;\n', 1)
    for key, value in {
        'FIELDS': fields, 'CONSTANTS': constants, 'HELPERS': helpers,
        'LEGACY': extract_braced(header, 'struct LegacyObserverTail') + ';',
        'SERIALIZER': 'template<typename Writer>\n' + extract_braced(
            source, 'static bool writeCommonPrefsImage('),
        'STORE': extract_braced(source, 'class CommonPrefsFileStore'),
        'METHODS': methods,
        'SET_DEBUG': extract_braced(source, 'if (strncmp(config, "usb.debug", 9)'),
        'SET_MASTER': extract_braced(source, 'if (strncmp(config, "usb.logging", 11)'),
        'GET_DEBUG': extract_braced(source, 'if (strcmp(config, "usb.debug") == 0)'),
        'GET_MASTER': extract_braced(source, 'if (strcmp(config, "usb.logging") == 0)'),
    }.items():
        code = code.replace('@' + key + '@', value)
    code += '\nvoid CommonCLI::password(const char* command, char* reply) {\n'
    code += extract_braced(source, 'if (memcmp(command, "password ", 9) == 0)') + '\n}\n'
    code += '\nvoid CommonCLI::guestPassword(const char* config, char* reply) {\n'
    code += extract_braced(source, 'if (memcmp(config, "guest.password ", 15) == 0)') + '\n}\n'
    handle = extract_braced(source, 'void CommonCLI::handleCommand(')
    guard = re.search(r'PrefsSaveReplyGuard save_reply\([^;]+;', handle).group()
    code += '\nvoid CommonCLI::legacySet(const char* config, char* reply) {\n'
    code += guard + '\n'
    code += extract_braced(source, 'if (memcmp(config, "multi.acks ", 11) == 0)') + '\n}\n'
    return code + (ROOT / 'test/fixtures/common_prefs_uncertain_reply/main.cpp').read_text(
        encoding='ascii')


class CommonPrefsUncertainReplyTest(unittest.TestCase):
    def test_actual_password_commands_and_durable_images(self):
        code = actual_harness()
        mock = (ROOT / 'test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h').read_text(
            encoding='ascii')
        # Fail only after a real rename publishes the complete candidate. The
        # real AtomicFileWriter still performs all its normal verification.
        old_field = '  bool fail_read_open = false;'
        self.assertEqual(mock.count(old_field), 1)
        mock = mock.replace(old_field, old_field + '\n  bool fail_after_publication = false;')
        old_rename = '    files[to] = files[from]; files.erase(from); return true;'
        self.assertEqual(mock.count(old_rename), 1)
        mock = mock.replace(old_rename,
                            '    files[to] = files[from]; files.erase(from);\n'
                            '    if (fail_after_publication && !strcmp(to, "/com_prefs"))\n'
                            '      fail_read_open = true;\n'
                            '    return true;')
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-pie', '-no-pie']
        with tempfile.TemporaryDirectory(prefix='common-prefs-uncertain-') as directory:
            work = Path(directory)
            (work / 'helpers').mkdir()
            (work / 'helpers/IdentityStore.h').write_text(mock, encoding='ascii')
            # Preserve the production transaction body; redirect only its
            # quoted filesystem include to the same injected adapter.
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text(
                encoding='ascii')
            self.assertEqual(transaction.count('#include "IdentityStore.h"'), 1)
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'),
                encoding='ascii')
            (work / 'test.cpp').write_text(code, encoding='ascii')
            for platform in ('NRF52_PLATFORM', 'STM32_PLATFORM',
                             'ESP32_PLATFORM', 'RP2040_PLATFORM'):
                with self.subTest(platform=platform):
                    binary = work / ('test.exe' if os.name == 'nt' else 'test')
                    built = subprocess.run([
                        os.environ.get('CXX', 'g++'), '-std=c++17', '-DENABLE_OTA=1',
                        '-D' + platform + '=1', *sanitizer_flags,
                        '-I' + str(work), '-I' + str(ROOT / 'src'),
                        '-I' + str(ROOT / 'src/helpers'), str(work / 'test.cpp'),
                        '-o', str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True,
                                            text=True, timeout=20)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
                    scenarios = 18 if platform in ('NRF52_PLATFORM', 'STM32_PLATFORM') else 15
                    self.assertIn(f'{scenarios} actual CommonCLI save scenarios passed', tested.stdout)


if __name__ == '__main__':
    unittest.main()
