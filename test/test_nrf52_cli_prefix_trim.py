"""Check the opt-in nRF52 CLI size trim and its exact reply bytes."""

import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nrf52_cli_prefix_trim', ROOT / 'scripts/nrf52_cli_prefix_trim.py')
TRIM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIM)


class Nrf52CliPrefixTrimTest(unittest.TestCase):
    def test_hook_is_opt_in_and_rejects_unqualified_roles(self):
        class Environment(dict):
            def subst(self, value):
                return value.replace('$PROJECT_DIR', str(ROOT))

            def BoardConfig(self):
                return {'build.mcu': 'nrf52840'}

            def AddBuildMiddleware(self, callback, pattern):
                self.setdefault('hooks', []).append((callback, pattern))

        common = {'SRC_FILTER': ['+<../examples/simple_repeater>'], 'CPPDEFINES': []}
        off = Environment(**common, BUILD_FLAGS=['-DRAK_4631=1'])
        TRIM.install(off)
        self.assertNotIn('hooks', off)
        flags = ['-DRAK_4631=1', '-DMESH_CLI_SHARE_ERROR_PREFIXES=1',
                 '-DMESH_CLI_SHARE_REPLY_COPIES=1']
        active = Environment(**common, BUILD_FLAGS=flags)
        TRIM.install(active)
        self.assertEqual([pattern for _, pattern in active['hooks']],
                         ['*MyMesh.cpp', '*CommonCLI.cpp', '*OtaCli.cpp', '*CLIReplyCopy.cpp'])
        for selected in (flags[:-1], ['-DRAK_3401=0', *flags[1:]]):
            with self.subTest(flags=selected), self.assertRaises(ValueError):
                TRIM.install(Environment(**common, BUILD_FLAGS=selected))
        with self.assertRaises(ValueError):
            TRIM.install(Environment(BUILD_FLAGS=flags, CPPDEFINES=[],
                                     SRC_FILTER=['+<../examples/simple_sensor>']))

    def test_reviewed_sources_preserve_every_original_literal(self):
        calls = []
        for filename, (_, groups) in TRIM.CASES.items():
            original = (ROOT / filename).read_text(encoding='utf-8')
            patched = TRIM.patched_source(filename, original)
            for prefix, macro, function, count in groups:
                before = re.findall(r'strcpy\s*\(\s*reply\s*,\s*"' + re.escape(prefix)
                                    + r'((?:\\.|[^"\\])*)"\s*\)', original)
                after = re.findall(re.escape(macro) + r'\(reply, "((?:\\.|[^"\\])*)"\)', patched)
                self.assertEqual(len(before), count)
                self.assertEqual(after, before)
                calls.extend(f'check(mesh::cli::{function}, "{prefix}" "{literal}", "{literal}");'
                             for literal in before)
            with self.assertRaises(ValueError):
                TRIM.patched_source(filename, original + '\n')
        self.assertGreater(len(calls), 200)
        helper_path = ROOT / 'src/helpers/CLIReplyCopy.cpp'
        helper = TRIM.patched_source('src/helpers/CLIReplyCopy.cpp', helper_path.read_text())
        with self.assertRaises(ValueError):
            TRIM.patched_source('src/helpers/CLIReplyCopy.cpp', helper_path.read_text() + '\n')

        compiler = shutil.which('g++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        program = r'''
#include <cassert>
#include <cstring>
namespace mesh { namespace cli {
char* copyErrorDash(char*, const char*);
char* copyOkDash(char*, const char*);
char* copyOtaError(char*, const char*);
} }
static void check(char* (*copy)(char*, const char*), const char* expected, const char* suffix) {
  char actual[512], reference[512];
  memset(actual, 0x55, sizeof(actual));
  memset(reference, 0x55, sizeof(reference));
  assert(strlen(expected) + 4 < sizeof(actual));
  assert(copy(actual + 2, suffix) == actual + 2);
  strcpy(reference + 2, expected);
  assert(memcmp(actual, reference, sizeof(actual)) == 0);
}
int main() {
''' + '\n'.join(calls) + '\n}\n'
        with tempfile.TemporaryDirectory(prefix='nrf-cli-prefix-') as directory:
            directory = Path(directory)
            unit, implementation, binary = directory / 'check.cpp', directory / 'CLIReplyCopy.cpp', directory / 'check'
            unit.write_text(program, encoding='utf-8')
            implementation.write_text(helper, encoding='utf-8')
            command = [compiler, '-std=c++17', '-Os', '-flto', '-Wall', '-Wextra', '-Werror',
                       '-DMESH_CLI_SHARE_REPLY_COPIES=1', '-DMESH_CLI_SHARE_MEMORY_OPERATIONS=1',
                       '-DMESH_CLI_SHARE_ERROR_PREFIXES=1', '-I', str(ROOT / 'src'),
                       '-I', str(ROOT / 'src/helpers'), str(unit), str(implementation), '-o', str(binary)]
            subprocess.run(command, check=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
