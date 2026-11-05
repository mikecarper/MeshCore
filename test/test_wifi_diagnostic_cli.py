"""Verify WiFi diagnostics route before feature-specific observer commands."""

from pathlib import Path
import configparser
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class WiFiDiagnosticCLITest(unittest.TestCase):
    def test_shared_esp32_source_filter_links_the_event_adapter(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(ROOT / "platformio.ini")
        self.assertIn("+<helpers/esp32/WiFiStationDiagnostics.cpp>",
                      config["esp32_base"]["build_src_filter"])

    def test_infrastructure_route_is_esp32_wide_and_exact(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        getter = extract_braced(common, "void CommonCLI::handleGetCmd(")
        signature = '  if (strcmp(config, "wifi.diag") == 0)'
        start = getter.index(signature)
        block = extract_braced(getter, signature)
        guard = getter.rfind("#if defined(ESP_PLATFORM)\n", 0, start)
        self.assertGreaterEqual(guard, 0)
        self.assertNotIn("#endif", getter[guard:start])
        self.assertNotIn("#else", getter[guard:start])
        self.assertTrue(getter[start + len(block):].startswith("\n#endif"))
        self.assertLess(start, getter.index("handleObserverGetCmd("))
        source = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
unsigned diagnostic_calls=0;
namespace mesh { namespace wifi {
bool formatStationDiagnostics(char* reply, size_t capacity) {
  ++diagnostic_calls;
  snprintf(reply, capacity, "> WiFi diagnostic");
  return true;
}
}}
void get(const char* config, char* reply) {
#if defined(ESP_PLATFORM)
@ROUTE@
#endif
  strcpy(reply, "fallback");
}
int main() {
  char reply[160]={};
  get("wifi.diag",reply);
#if defined(ESP_PLATFORM)
  assert(strcmp(reply, "> WiFi diagnostic")==0 && diagnostic_calls==1);
#else
  assert(strcmp(reply, "fallback")==0 && diagnostic_calls==0);
#endif
  get("wifi.diagx",reply); assert(strcmp(reply,"fallback")==0);
  get("wifi.diag extra",reply); assert(strcmp(reply,"fallback")==0);
}
'''.replace("@ROUTE@", block)
        compiler = os.environ.get("CXX") or shutil.which("g++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="wifi-diagnostic-cli-") as directory:
            folder = Path(directory)
            program = folder / "test.cpp"
            program.write_text(source)
            binary = folder / "test"
            for defines in ([], ["-DESP_PLATFORM=1"],
                            ["-DESP_PLATFORM=1", "-DWITH_MQTT_BRIDGE=1"],
                            ["-DESP_PLATFORM=1", "-DWEBCONFIG_DISABLED=1"]):
                with self.subTest(defines=defines):
                    built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra",
                                            *defines, str(program), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True,
                                         text=True, timeout=15)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
