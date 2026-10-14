"""Execute each role's production device-power-saving AGC gate."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class AdaptiveAGCRoleTests(unittest.TestCase):
    def test_device_power_saving_not_rxps_controls_each_role(self):
        for role, header in (
            ("companion", "examples/companion_radio/MyMesh.h"),
            ("repeater", "examples/simple_repeater/MyMesh.h"),
            ("room", "examples/simple_room_server/MyMesh.h"),
            ("sensor", "examples/simple_sensor/SensorMesh.h"),
        ):
            with self.subTest(role=role), tempfile.TemporaryDirectory(prefix="meshcore-agc-role-") as tmp:
                gate = method((ROOT / header).read_text(), "bool useAdaptiveAGC() const")
                source = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
struct Base { virtual bool useAdaptiveAGC() const { return false; } };
struct Role : Base {
  struct { uint8_t powersaving_enabled = 0, rx_powersaving_enabled = 0; } _prefs;
  @GATE@
};
int main() {
  Role role;
  for (uint8_t power : {0, 1, 255}) for (uint8_t rxps : {0, 1}) {
    role._prefs.powersaving_enabled = power;
    role._prefs.rx_powersaving_enabled = rxps;
    assert(role.useAdaptiveAGC() == (power != 0));
  }
}
'''
                cpp, binary = Path(tmp) / "test.cpp", Path(tmp) / "test"
                cpp.write_text(source.replace("@GATE@", gate), encoding="ascii")
                result = subprocess.run([
                    os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    str(cpp), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
