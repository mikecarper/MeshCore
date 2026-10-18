#!/usr/bin/env python3
"""Exercise production Repeater normalization before the actual password setter."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RepeaterPasswordNormalizationTest(unittest.TestCase):
    def test_password_bytes_reach_the_setter_on_esp32_only(self):
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        start = repeater.index("  char* command_end = command + strlen(command);")
        end = repeater.index("\n\n", start)
        trimming = repeater[start:end]
        start = repeater.index("  while (*command == ' ') command++;", end)
        end = repeater.index("  mesh::cli::normalizeCommandVerb(command);", start)
        end += len("  mesh::cli::normalizeCommandVerb(command);")
        normalization = trimming + "\n" + repeater[start:end]
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        start = cli.index('    } else if (memcmp(command, "password ", 9) == 0) {')
        end = cli.index('    } else if (memcmp(command, "clear stats",', start)
        setter = cli[start:end].replace("} else if (", "if (", 1) + "\n    }\n"
        source = r'''
#include <helpers/CLICommandUtils.h>
#include <helpers/esp32/ManagementPassword.h>
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace mesh::esp32::managementPassword;
char* normalize(char* command, char* reply) {
@NORMALIZATION@
  return command;
}
struct StrHelper {
  static void strncpy(char* out, const char* in, size_t capacity) {
    ::strncpy(out, in, capacity); out[capacity - 1] = 0;
  }
};
struct Prefs { char password[16] = "password"; };
struct CommonCLI {
  Prefs prefs; Prefs* _prefs = &prefs; unsigned saves = 0;
  bool trySavePrefs() { ++saves; return true; }
  void command(char* command, char* reply) {
@SETTER@
  }
};
void normalized(const char* input, const char* expected, const char* prefix = "") {
  char command[100], reply[160] = {};
  strcpy(command, input);
  assert(!strcmp(normalize(command, reply), expected));
  assert(!strcmp(reply, prefix));
}
int main() {
  normalized("  aB|Get name \t\r\n", "get name", "aB|");
  normalized("  Passwordx value \t\r\n", "passwordx value");
#ifdef ESP_PLATFORM
  normalized("  aB|PaSsWoRd  leading \t", "password  leading \t", "aB|");
  normalized("password secret123 ", "password secret123 ");
  normalized("Password secret123\r\n", "password secret123\r\n");
  CommonCLI cli;
  assert(publish("password"));
  char command[100] = "  aB|PaSsWoRd  leading ", reply[160] = {};
  cli.command(normalize(command, reply), reply);
  assert(cli.saves == 1 && !strcmp(cli.prefs.password, " leading "));
  Snapshot current; assert(snapshot(current));
  assert(!strcmp(current.password, " leading "));
  const uint32_t generation = current.generation;
  for (const char* invalid : {"password secret123\r\n", "password bad\nvalue", "password bad\rvalue", "password "}) {
    strcpy(command, invalid);
    cli.command(normalize(command, reply), reply);
    assert(!strncmp(reply, "Error:", 6) && cli.saves == 1);
    assert(!strcmp(cli.prefs.password, " leading "));
    assert(snapshot(current) && current.generation == generation);
  }
#else
  normalized("  aB|PaSsWoRd  leading \t\r\n", "password  leading", "aB|");
  normalized("password secret123 ", "password secret123");
  normalized("Password secret123\r\n", "password secret123");
#endif
}
'''.replace("@NORMALIZATION@", normalization).replace("@SETTER@", setter)
        source = source.replace("#include <cassert>", "#include <cassert>\n#include <initializer_list>")
        with tempfile.TemporaryDirectory(prefix="repeater-password-") as temporary:
            work = Path(temporary)
            for platform in ("ESP_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM"):
                with self.subTest(platform=platform):
                    self._compile_and_run(work, source, platform)
            # The old blanket trim must demonstrably break this regression.
            mutant = source.replace('if (strcmp(command_verb, "password ") != 0)',
                                    'if (true)')
            result = self._compile_and_run(work, mutant, "ESP_PLATFORM", expect_pass=False)
            self.assertNotEqual(result.returncode, 0)

    def _compile_and_run(self, work, source, platform, expect_pass=True):
        (work / "test.cpp").write_text(source)
        result = subprocess.run([
            os.environ.get("CXX", "g++"), "-std=c++11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
            "-DMESH_MANAGEMENT_PASSWORD_HOST_TEST=1", f"-D{platform}=1",
            "-I", str(ROOT / "src"), str(work / "test.cpp"), "-o", str(work / "test"),
        ], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(work / "test")], capture_output=True, text=True, timeout=15)
        if expect_pass:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


if __name__ == "__main__":
    unittest.main()
