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

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'test/fixtures/cli_reply_text.json'
SELECTION = re.compile(
    r'MESH_CLI_REPLY_TEXT\(("(?:\\.|[^"\\])*"),\s*("(?:\\.|[^"\\])*")\)')
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                    r'//[^\n]*|/\*.*?\*/|[A-Za-z_][A-Za-z_0-9]*|[^\s]', re.S)


def original_tokens(source):
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
            self.assertNotRegex(old.lower().replace('[reboot]', ''),
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
                      for match in SELECTION.finditer(source)}
            self.assertEqual(actual, messages, file)
            # Substituting each original literal recovers every original C++
            # token, including command tests, branches, calls and arguments.
            self.assertEqual(hashlib.sha256(original_tokens(source)).hexdigest(),
                             fixture['noncompact_tokens_sha256'][file], file)

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
                  'int main(){const char* text[]={'+selections+'};'
                  'for(const char* line:text)puts(line);}\n')
        with tempfile.TemporaryDirectory(prefix='cli-reply-text-') as folder:
            work = Path(folder)
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
                    str(work / 'test.cpp'), '-o', str(work / 'test')], capture_output=True)
                self.assertEqual(built.returncode, 0, built.stderr.decode(errors='replace'))
                result = subprocess.run([str(work / 'test')], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                self.assertEqual(result.stdout,
                                 ''.join(row[key] + '\n' for row in messages).encode())
                if key == 'concise':
                    image = (work / 'test').read_bytes()
                    selected = {row['concise'] for row in messages}
                    for row in messages:
                        if row['verbose'] not in selected:
                            self.assertNotIn(row['verbose'].encode()+b'\0', image)


if __name__ == '__main__':
    unittest.main()
