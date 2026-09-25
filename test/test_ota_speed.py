"""Exercise production OTA speed CLI/persistence without an OTA workspace."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cassert>
#include <cstring>
#include <helpers/ota/OtaSpeedConfig.h>
using namespace mesh::ota;
int main() {
  MemoryFS fs;
  char reply[160];
  auto command = [&](const char* text, const char* prefix) {
    memset(reply, 0, sizeof reply);
    assert(handleSpeedCommand(text, reply, sizeof reply));
    assert(!strncmp(reply, prefix, strlen(prefix)));
  };
  beginSpeedConfig(&fs);
  command("get ota.speed", "> ota.speed=1x packet=1x");
  assert(handleSpeedCommand("get ota.speed", reply, sizeof reply, 0.25f));
  assert(!strcmp(reply, "> ota.speed=1x packet=0.25x"));
  assert(effectivePacketPace(0.25f) == 0.25f);
  assert(effectivePacketPace(1.0f) == 1.0f);
  for (const auto* bad : {"", "0", "-1", "0.049", "3.01", "nan", "inf", "1e0", "1abc", "1 2"}) {
    const std::string text = std::string("set ota.speed ") + bad;
    command(text.c_str(), "ERR");
    assert(speedFactor() == 1.0f && fs.files.empty());
  }
  command("set ota.speed .05", "OK ota.speed=0.05x (saved) packet=0.05x");
  assert(handleSpeedCommand("get ota.speed", reply, sizeof reply, 0.25f));
  assert(!strcmp(reply, "> ota.speed=0.05x packet=0.05x"));
  assert(validSpeed(speedFactor()));
  beginSpeedConfig(&fs);
  command("get ota.speed", "> ota.speed=0.05x packet=0.05x");
  command("ota config speed 3", "OK ota.speed=3x (saved) packet=1x");
  command("ota config speed", "> ota.speed=3x packet=1x");
  command("ota cfg speed 0.5", "OK ota.speed=0.5x (saved) packet=0.5x");
  command("ota speed", "> ota.speed=0.5x packet=0.5x");
  command("get ota.speed 1", "ERR");
  assert(!handleSpeedCommand("get ota.speed.extra", reply, sizeof reply));
  assert(!handleSpeedCommand("ota config advert 10", reply, sizeof reply));
  const auto good = fs.files["/ota_speed"];
  fs.fail_write = true;
  command("set ota.speed 2", "ERR");
  assert(speedFactor() == 0.5f && fs.files["/ota_speed"] == good);
  fs.fail_write = false;
  fs.fail_rename = 2; // cannot promote temp; restore primary from backup
  command("set ota.speed 2", "ERR");
  assert(speedFactor() == 0.5f && fs.files["/ota_speed"] == good);
  fs.files["/ota_speed.bak"] = good;
  fs.files.erase("/ota_speed"); // power cut in rename gap
  beginSpeedConfig(&fs);
  assert(speedFactor() == 0.5f && fs.files["/ota_speed"] == good);
  fs.files["/ota_speed.bak"] = good;
  fs.files.erase("/ota_speed");
  fs.fail_rename = 1; // valid backup remains authoritative if repair fails
  beginSpeedConfig(&fs);
  assert(speedFactor() == 0.5f);
  command("set ota.speed 2", "ERR");
  assert(fs.files["/ota_speed.bak"] == good);
  beginSpeedConfig(&fs);
  assert(speedFactor() == 0.5f && fs.files["/ota_speed"] == good);
  fs.files["/ota_speed"][4] ^= 1; // damaged image is not partly adopted
  beginSpeedConfig(&fs);
  assert(speedFactor() == 1.0f);
  command("set ota.speed 2", "OK ota.speed=2x (saved)");
  assert(speedFactor() == 2.0f);
  beginSpeedConfig(&fs);
  assert(speedFactor() == 2.0f);
  fs.files.clear();
  beginSpeedConfig(&fs);
  command("ota set speed 1.125", "OK ota.speed=1.125x");
  beginSpeedConfig(&fs);
  assert(speedFactor() == 1.125f);
}
'''


class OtaSpeedTest(unittest.TestCase):
    def test_commands_range_storage_and_failure_recovery(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "speed.cpp"
            source.write_text(HARNESS)
            binary = Path(directory) / "speed"
            result = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-DENABLE_OTA=1",
                "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                str(ROOT / "src/helpers/ota/OtaSpeedConfig.cpp"), str(source),
                "-o", str(binary),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True)

    def test_shared_role_wiring_and_idle_context_access(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        for text in (common, companion):
            self.assertIn("mesh::ota::beginSpeedConfig(", text)
            self.assertIn("mesh::ota::handleSpeedCommand(command, reply,", text)
        cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text()
        self.assertLess(cli.index("handleSpeedCommand(command"), cli.index("ota_acquire_context(reply"))
        mesh = (ROOT / "src/Mesh.cpp").read_text()
        self.assertIn("syncOtaTiming(context->manager)", mesh)
        self.assertIn("syncOtaTiming(ota::ota_ctx().manager)", mesh)
        self.assertIn("manager.set_speed(getOtaSpeedFactor())", mesh)
        self.assertIn("_ota_announce_timer.arm(_ms->getMillis(), gap, ota_speed)", mesh)


if __name__ == "__main__":
    unittest.main()
