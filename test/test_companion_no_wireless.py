"""Check the compact no-wireless Companion path against the full controller."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/WirelessControl.h>
#include "examples/companion_radio/CompanionWireless.h"
#include <cassert>
#include <cstring>
#include <string>
#include <vector>

struct Unavailable : mesh::wireless::Backend {
  unsigned sets = 0;
  uint8_t available() const override { return 0; }
  uint8_t enabled() const override { return 0; }
  uint8_t clients() const override { return 0; }
  mesh::wireless::Result set(uint8_t, bool) override {
    ++sets;
    return mesh::wireless::Result::Failed;
  }
};

@WRAPPER@

int main() {
  using namespace mesh::wireless;
  static Unavailable backend;
  Control reference;
  reference.begin(backend);
  control().begin(backend);
  const char* commands[] = {
    "", "get wifi", "get espnow", "get 2.4ghz", "get wifi.status",
    "set wifi on", "set wifi off", "set wifi off force", "set wifi on force",
    "set espnow on", "set espnow off", "set espnow off force",
    "set 2.4ghz on", "set 2.4ghz off", "set 2.4ghz off force",
    "set 2.4ghz on all", "set 2.4ghz on force", "set 2.4ghz on all force",
    " get wifi\t", "set\twifi on", "get wifi junk", "set wifi",
    "set wifi off force extra", "set espnow.channel 3", "get bluetooth",
    "set bluetooth off", "set 2.4ghzx on", "SET wifi on", "reset wifi"
  };
  const CompanionWirelessSource sources[] = {
    CompanionWirelessSource::Other, CompanionWirelessSource::Usb,
    CompanionWirelessSource::Network, CompanionWirelessSource::Framed
  };
  const uint8_t requesters[] = {0, Independent, WiFi, Bluetooth};
  for (const char* command : commands) for (size_t size = 0; size <= 160; ++size)
    for (unsigned route = 0; route < 4; ++route) {
      char expected[160], actual[160];
      memset(expected, 0x5a, sizeof(expected));
      memset(actual, 0x5a, sizeof(actual));
      const bool original = reference.handle(command, expected, size, 500, requesters[route]);
      const bool compact = handleCompanionWirelessCommand(command, actual, size, sources[route]);
      assert(original == compact);
      assert(memcmp(expected, actual, sizeof(expected)) == 0);
      reference.service(1000);
      control().service(1000);
      assert(!reference.pending() && !control().pending());
      assert(!reference.masterOff() && !control().masterOff());
      assert(!reference.blocked(All) && !control().blocked(All));
      assert(backend.sets == 0);
    }
  for (const char* command : {"get wifi", "set wifi on", "unknown"}) {
    assert(reference.handle(command, nullptr, 160, 500, Independent)
           == handleCompanionWirelessCommand(command, nullptr, 160, CompanionWirelessSource::Usb));
  }
}
'''


class NoWirelessCompanionTest(unittest.TestCase):
    def test_stm32_wrapper_matches_full_controller(self):
        wrapper = body((ROOT / "examples/companion_radio/main.cpp").read_text(),
                       "bool handleCompanionWirelessCommand(")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "no_wireless.cpp"
            source.write_text(HARNESS.replace("@WRAPPER@", wrapper))
            for compact in (0, 1):
                binary = Path(directory) / f"check-{compact}"
                command = [os.environ.get("CXX", "g++"), "-std=c++17", "-O1",
                           "-Wall", "-Wextra", "-Werror", "-DSTM32_PLATFORM=1",
                           f"-DMESH_CLI_COMPACT_REPLIES={compact}",
                           "-I", str(ROOT / "src"), "-I", str(ROOT),
                           str(source), "-o", str(binary)]
                if os.name != "nt":
                    command[1:1] = ["-fsanitize=address,undefined",
                                    "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                subprocess.run(command, check=True, timeout=60)
                subprocess.run([str(binary)], check=True, timeout=30)

    def test_wireless_capabilities_keep_the_full_controller(self):
        wrapper = body((ROOT / "examples/companion_radio/main.cpp").read_text(),
                       "bool handleCompanionWirelessCommand(")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "guard.cpp"
            source.write_text('#include <helpers/WirelessControl.h>\n'
                              '#include "examples/companion_radio/CompanionWireless.h"\n'
                              + wrapper)
            for flags, compact in (
                (["-DSTM32_PLATFORM=1"], True),
                (["-DSTM32_PLATFORM=1", "-DMESH_PRIMARY_ESPNOW=0"], True),
                (["-DSTM32_PLATFORM=1", "-DBLE_PIN_CODE=123456"], False),
                (["-DSTM32_PLATFORM=1", "-DWIFI_SSID=1"], False),
                (["-DSTM32_PLATFORM=1", "-DMESH_PRIMARY_ESPNOW=1"], False),
                (["-DSTM32_PLATFORM=1", "-DWITH_BRIDGE=1"], False),
                (["-DSTM32_PLATFORM=1", "-DWITH_MQTT_BRIDGE=1"], False),
                (["-DSTM32_PLATFORM=1", "-DWITH_UDP_BRIDGE=1"], False),
                (["-DSTM32_PLATFORM=1", "-DWITH_ESPNOW_BRIDGE=1"], False),
                (["-DSTM32_PLATFORM=1", "-DWITH_WEBCONFIG=1"], False),
                (["-DSTM32_PLATFORM=1", "-DETHERNET_ENABLED=1"], False),
                (["-DSTM32_PLATFORM=1", "-DMESH_ENABLE_EMERGENCY_POWER=1"], False),
                ([], False),
            ):
                with self.subTest(flags=flags):
                    result = subprocess.run(
                        [os.environ.get("CXX", "g++"), "-std=c++17", "-E", "-P",
                         *flags, "-I", str(ROOT / "src"), "-I", str(ROOT), str(source)],
                        check=True, capture_output=True, text=True, timeout=60)
                    self.assertEqual("return mesh::wireless::handleNoServices(" in result.stdout, compact)
                    self.assertEqual("return mesh::wireless::control().handle(" in result.stdout, not compact)


if __name__ == "__main__":
    unittest.main()
