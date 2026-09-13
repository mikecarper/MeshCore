"""Exercise the production infrastructure setter and its persistence boundary."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class PathHashCLITest(unittest.TestCase):
    def test_production_setter(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler, 'Host C++ compiler required')
        source = (ROOT/'src/helpers/CommonCLI.cpp').read_text(encoding='utf-8')
        setter = extract_braced(source, 'void CommonCLI::handleSetCmd(')
        branch = extract_braced(setter, 'if (strncmp(config, "path.hash.mode", 14)')
        fixture = r'''
#include <cassert>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
struct CLI {
  struct Prefs { uint8_t path_hash_mode = 0; } prefs;
  Prefs* _prefs = &prefs;
  unsigned saves = 0;
  void savePrefs() { ++saves; }
  void set(const char* config, char* reply) {
''' + branch + r'''
    strcpy(reply, "unknown config");
  }
};
int main() {
  CLI cli; char reply[160];
  for (unsigned mode : {0u, 1u, 2u}) {
    char command[64]; sprintf(command,"path.hash.mode %u",mode);
    unsigned saves=cli.saves;
    cli.set(command,reply);
    assert(strcmp(reply,"OK")==0);
    assert(cli.prefs.path_hash_mode==mode && cli.saves==saves+1);
  }
  cli.set("path.hash.mode\t1 ",reply);
  assert(strcmp(reply,"OK")==0 && cli.prefs.path_hash_mode==1);
  for (const char* value : {"", " ", " -1", " 3", " 256", " 4294967296", " nope", " 1x", " 1 2"}) {
    char command[80]; sprintf(command,"path.hash.mode%s",value);
    unsigned saves=cli.saves;
    cli.set(command,reply);
    assert(strcmp(reply,"Error, must be 0,1, or 2")==0);
    assert(cli.prefs.path_hash_mode==1 && cli.saves==saves);
  }
  cli.set("path.hash.mode.extra 2",reply);
  assert(strcmp(reply,"unknown config")==0 && cli.prefs.path_hash_mode==1);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory)/'test.cpp'
            exe = Path(directory)/'test.exe'
            cpp.write_text(fixture, encoding='utf-8')
            subprocess.run([compiler, '-std=c++11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ROOT/'src'), str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
