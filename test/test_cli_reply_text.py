"""Keep constrained CLI prose separate from commands and protocol replies."""
from pathlib import Path
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/cli_reply_text.json'
SELECTION = re.compile(
    r'MESH_CLI_REPLY_TEXT\(("(?:\\.|[^"\\])*"),\s*("(?:\\.|[^"\\])*")\)')
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                    r'//[^\n]*|/\*.*?\*/|[A-Za-z_][A-Za-z_0-9]*|[^\s]', re.S)
SHARED_TERMINAL_ERROR = re.compile(
    r'terminalOutput\(\)\.printf\("  ERROR: %s",\s*'
    r'MESH_CLI_REPLY_TEXT\(("(?:\\.|[^"\\])*"),\s*("(?:\\.|[^"\\])*")\)\)')
SHARED_TERMINAL_ERROR_MEMBER = re.compile(
    r'(?<![A-Za-z_0-9:.])printTerminalError\(\s*'
    r'MESH_CLI_REPLY_TEXT\(("(?:\\.|[^"\\])*"),\s*("(?:\\.|[^"\\])*")\)\)')


def expand_shared_terminal_errors(source):
    # Normalize only the reviewed member-call shape to the prior printf call,
    # then reconstruct its original literal print for the whole-source/message
    # guards. The executable test invokes the actual member implementation.
    source = SHARED_TERMINAL_ERROR_MEMBER.sub(
        lambda match: 'terminalOutput().printf("  ERROR: %s", MESH_CLI_REPLY_TEXT('
                      + ', '.join(match.groups()) + '))', source)
    def expand(match):
        messages = [json.dumps('  ERROR: ' + json.loads(value))
                    for value in match.groups()]
        return 'terminalOutput().print(MESH_CLI_REPLY_TEXT(%s, %s))' % tuple(messages)
    return SHARED_TERMINAL_ERROR.sub(expand, source)


def original_tokens(source):
    source = expand_shared_terminal_errors(source)
    source = re.sub(r'^#include [<"](?:helpers/)?CLIReplyText\.h[>"]\s*\n',
                    '', source, flags=re.M)
    source = SELECTION.sub(lambda match: match.group(1), source)
    return '\n'.join(token.group() for token in TOKENS.finditer(source)
                     if not token.group().startswith(('//', '/*'))).encode()


class CliReplyTextTests(unittest.TestCase):
    def test_only_reviewed_human_literals_change_and_control_flow_stays_exact(self):
        fixture = json.loads(FIXTURE.read_text())
        self.assertEqual(fixture['schema'], 1)
        expected = {}
        for row in fixture['messages']:
            expected.setdefault(row['file'], set()).add((row['verbose'], row['concise']))
            old, new = row['verbose'], row['concise']
            self.assertLess(len(new), len(old))
            formats = r'%(?:[-+ #0]*[0-9.*]*[a-zA-Z]+|%)'
            self.assertEqual(re.findall(formats, old), re.findall(formats, new))
            category = r'^[ \t]*(Error|ERROR|ERR|Err|OK)(?=[: ,\-])'
            self.assertEqual(re.findall(category, old), re.findall(category, new))
            self.assertEqual(re.findall(r'[\r\n]', old), re.findall(r'[\r\n]', new))
            self.assertEqual(re.findall(r'\d+(?:\.\d+)?', old),
                             re.findall(r'\d+(?:\.\d+)?', new))
            self.assertFalse(old.startswith(('> ', '{', 'KEYBACKUP ', 'ACK')))
            protected = old.lower().replace('[reboot]', '')
            if (row['file'] == 'src/helpers/ota/OtaCli.cpp' and
                    old == 'ERR self serve requires ESP32 or external-storage nRF52; host sources use ota folder'):
                # This describes source capabilities, not host data or its retention.
                protected = protected.replace('host sources use ota folder', 'ota folder')
            self.assertNotRegex(protected,
                r'host|replay|password|login|save unconfirmed|reboot|'
                r'retained|warning|applying in|restore scheduled')
            for protected in ('[reboot]', 'default'):
                if protected in old:
                    self.assertIn(protected, new)
            if 'unchanged' in old:
                self.assertIn('sleep policy unchanged', old)
                self.assertIn('sleep policy unchanged', new)
        for file, messages in expected.items():
            source = (ROOT / file).read_text()
            actual = {(json.loads(match.group(1)), json.loads(match.group(2)))
                      for match in SELECTION.finditer(expand_shared_terminal_errors(source))}
            self.assertEqual(actual, messages, file)
            # Substituting each original literal recovers every original C++
            # token, including command tests, branches, calls and arguments.
            # Review unrelated behavior changes before refreshing a fixture
            # hash; keep their tokens covered by this guard as well.
            self.assertEqual(hashlib.sha256(original_tokens(source)).hexdigest(),
                             fixture['noncompact_tokens_sha256'][file], file)

    def test_shared_terminal_error_prefix_keeps_every_output_byte(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('host C++ compiler required')
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        calls = list(SHARED_TERMINAL_ERROR_MEMBER.finditer(source))
        self.assertEqual(len(calls), 57)
        self.assertFalse(SHARED_TERMINAL_ERROR.search(source),
                         "all reviewed shared-prefix calls must use the member")
        implementation = body(source, "void MyMesh::printTerminalError(")
        approved = {(row['verbose'], row['concise']) for row in
                    json.loads(FIXTURE.read_text())['messages']
                    if row['file'] == 'examples/companion_radio/MyMesh.cpp'}
        for call in calls:
            self.assertIn(tuple('  ERROR: ' + json.loads(value)
                                for value in call.groups()), approved)
        harness = ('#include <cstdio>\n#include <CLIReplyText.h>\n'
                   '#ifdef _WIN32\n#include <fcntl.h>\n#include <io.h>\n#endif\n'
                   'struct MockOutput {\n'
                   '  int printf(const char* format, const char* message) {\n'
                   '    return std::printf(format, message);\n'
                   '  }\n};\n'
                   'struct MyMesh {\n'
                   '  MockOutput output;\n'
                   '  MockOutput& terminalOutput() { return output; }\n'
                   '  void printTerminalError(const char* message);\n'
                   '  void exercise() {\n' +
                   '\n'.join(call.group() + ';' for call in calls) + '\n}\n};\n' +
                   implementation + '\n'
                   'int main() {\n#ifdef _WIN32\n_setmode(_fileno(stdout), _O_BINARY);\n#endif\n'
                   'MyMesh mesh; mesh.exercise();\n}\n')
        with tempfile.TemporaryDirectory(prefix='terminal-error-prefix-') as folder:
            work = Path(folder)
            (work / 'test.cpp').write_text(harness)
            for compact in (0, 1):
                result = subprocess.run([compiler, '-std=c++11', '-Os', '-Wall',
                    '-Wextra', '-Werror', '-DMESH_CLI_COMPACT_REPLIES='+str(compact),
                    '-I'+str(ROOT/'src/helpers'), str(work/'test.cpp'),
                    '-o', str(work/'test')], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                result = subprocess.run([str(work/'test')], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = ''.join('  ERROR: ' + json.loads(call.group(compact+1))
                                   for call in calls).encode()
                self.assertEqual(result.stdout, expected)

    def test_ota_prose_keeps_discovery_and_retry_parser_keywords(self):
        messages = {row['verbose']: row['concise']
                    for row in json.loads(FIXTURE.read_text())['messages']
                    if row['file'] == 'src/helpers/ota/OtaCli.cpp'}
        self.assertEqual(len(messages), 15)
        self.assertGreaterEqual(sum(len(old) - len(new)
                                    for old, new in messages.items()), 240)
        for prefix in ('No updates seen yet', 'ERR no such update',
                       'no trusted signer keys'):
            rows = [(old, new) for old, new in messages.items() if old.startswith(prefix)]
            self.assertEqual(len(rows), 1, prefix)
            self.assertTrue(rows[0][1].startswith(prefix), prefix)
        for old, new in messages.items():
            self.assertFalse(old.startswith(('OK', 'usage:', 'ERR usage:')))
            for token in re.findall(r'`([^`]+)`', old):
                self.assertIn(token, new)
            if 'disabled' in old:
                self.assertIn('disabled', new)
            if 'cannot install' in old:
                self.assertIn('cannot install', new)
            if 'cannot update its bootloader' in old:
                self.assertIn('cannot update its bootloader', new)

    def test_profile_activation_is_explicit_and_default_is_off(self):
        fixture = json.loads(FIXTURE.read_text())
        enabled = []
        for path in [ROOT / 'platformio.ini', *sorted((ROOT / 'variants').rglob('*.ini'))]:
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read(path)
            for section in config.sections():
                if re.search(r'-D\s*MESH_CLI_COMPACT_REPLIES_DEFAULT=1\b',
                             config[section].get('build_flags', '')):
                    self.assertTrue(section.startswith('env:'), str(path))
                    enabled.append(section[4:])
        self.assertEqual(sorted(enabled), sorted(fixture['profiles']))
        header = (ROOT / 'src/helpers/CLIReplyText.h').read_text()
        self.assertIn('#ifndef MESH_CLI_COMPACT_REPLIES\n', header)
        self.assertIn('#define MESH_CLI_COMPACT_REPLIES 0', header)

    def test_actual_header_selects_exact_text_without_a_runtime_lookup(self):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('host C++ compiler required')
        messages = json.loads(FIXTURE.read_text())['messages']
        selections = ',\n'.join('MESH_CLI_REPLY_TEXT(%s, %s)' %
                               (json.dumps(row['verbose']), json.dumps(row['concise']))
                               for row in messages)
        source = ('#include <CLIReplyText.h>\n#include <cstdio>\n'
                  '#ifdef _WIN32\n#include <fcntl.h>\n#include <io.h>\n#endif\n'
                  'int main(){\n'
                  # Compare the selected literals without Windows stdio's
                  # newline conversion changing the executable's output.
                  '#ifdef _WIN32\n_setmode(_fileno(stdout),_O_BINARY);\n#endif\n'
                  'const char* text[]={'+selections+'};'
                  'for(const char* line:text)puts(line);}\n')
        with tempfile.TemporaryDirectory(prefix='cli-reply-text-') as folder:
            work = Path(folder)
            executable = work / ('test.exe' if os.name == 'nt' else 'test')
            (work / 'test.cpp').write_text(source)
            for flags, key in (([], 'verbose'),
                    (['-DMESH_CLI_COMPACT_REPLIES=0'], 'verbose'),
                    (['-DMESH_CLI_COMPACT_REPLIES=1'], 'concise'),
                    (['-DMESH_CLI_COMPACT_REPLIES_DEFAULT=1'], 'concise'),
                    (['-DMESH_CLI_COMPACT_REPLIES_DEFAULT=1',
                      '-DMESH_CLI_COMPACT_REPLIES=0'], 'verbose'),
                    (['-DMESH_CLI_COMPACT_REPLIES_DEFAULT=0',
                      '-DMESH_CLI_COMPACT_REPLIES=1'], 'concise')):
                built = subprocess.run([compiler, '-std=c++11', '-Os', '-Wall', '-Wextra',
                    '-Werror', *flags, '-I'+str(ROOT / 'src/helpers'),
                    str(work / 'test.cpp'), '-o', str(executable)], capture_output=True)
                self.assertEqual(built.returncode, 0, built.stderr.decode(errors='replace'))
                result = subprocess.run([str(executable)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                self.assertEqual(result.stdout,
                                 ''.join(row[key] + '\n' for row in messages).encode())
                if key == 'concise':
                    image = executable.read_bytes()
                    selected = {row['concise'] for row in messages}
                    for row in messages:
                        if row['verbose'] not in selected:
                            self.assertNotIn(row['verbose'].encode()+b'\0', image)


if __name__ == '__main__':
    unittest.main()
