"""Execute production OTA staging commands with bounds and lifetime checks."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


class OtaDevStagingTest(unittest.TestCase):
    def test_invalid_commands_are_atomic_and_writes_revoke_serving(self):
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        parser = (method(cli, "static uint32_t parse_u32(")
                  if "static uint32_t parse_u32(" in cli else "")
        stage = method(cli, 'if (strncmp(d, "stage ", 6) == 0)')
        receive = method(cli, 'else if (strncmp(d, "recv ", 5) == 0)')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "commands.h").write_text(
                parser + '\nstatic bool command(const char* d, char* reply, Context& c) {\n'
                + stage + receive + '\nreturn true;\n}\n')
            source = path / "test.cpp"
            source.write_text(r'''
#include <helpers/CLICommandUtils.h>
#include <Utils.h>
#include <cassert>
#include <cstring>
#include <string>
#define OTA_SERVE_BUF_SIZE 256
struct Context {
  uint8_t storage[OTA_SERVE_BUF_SIZE];
  uint8_t* serve_buf = storage;
  uint32_t serve_expected = 0;
  bool serving = false;
  struct Manager {
    unsigned cleared = 0;
    void clear_primary() { ++cleared; }
  } manager;
  bool ensureServeBuffer() { return true; }
};
#include "commands.h"
int main(int argc, char** argv) {
  assert(argc == 2);
  Context context;
  char reply[160];
  auto call = [&](const char* text) { command(text, reply, context); };
  call("stage 4");
  assert(!strncmp(reply, "OK", 2));
  auto reject = [&](const char* text) {
    Context before = context;
    call(text);
    assert(!strncmp(reply, "ERR", 3));
    assert(context.serve_expected == before.serve_expected);
    assert(context.serving == before.serving);
    assert(context.manager.cleared == before.manager.cleared);
    assert(!memcmp(context.storage, before.storage, sizeof(context.storage)));
  };
  const std::string scenario(argv[1]);
  if (scenario == "stage") {
    for (const char* text : {"stage 4junk", "stage 4294967552", "stage -1",
         "stage 0", "stage 257", "stage 4 8", "stage "}) reject(text);
  } else if (scenario == "offset") {
    for (const char* text : {"recv junk 00", "recv 1junk 00", "recv -1 00",
         "recv 4294967296 00", "recv 0 001", "recv 0 0g", "recv 4 00",
         "recv 3 0000", "recv 0", "recv 0 ", "recv 0 00 extra"}) reject(text);
  } else if (scenario == "wrap") {
    reject("recv 4294967295 0000");
  } else if (scenario == "live") {
    context.serving = true;
    unsigned prior = context.manager.cleared;
    reject("recv 4 00");
    call("recv 0 aB");
    assert(!strncmp(reply, "OK", 2));
    assert(!context.serving && context.manager.cleared == prior + 1);
    assert(context.storage[0] == 0xAB);
  } else {
    call("recv 0 001122");
    assert(!strncmp(reply, "OK", 2));
    call("recv 3 Ff");
    assert(!strncmp(reply, "OK", 2));
    assert(!memcmp(context.storage, "\x00\x11\x22\xFF", 4));
    reject("recv 4 aa");
  }
}
''')
            binary = path / "staging"
            flags = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
            result = subprocess.run([
                "c++", "-std=c++17", *flags, "-I", str(ROOT / "src"),
                "-I", str(ROOT / "test/mocks"), str(source),
                str(ROOT / "src/Utils.cpp"), "-o", str(binary),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for scenario in ("stage", "offset", "wrap", "live", "valid"):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), scenario], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
