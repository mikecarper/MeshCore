"""Keep the Sensor release contract consistent with its actual CLI callbacks."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


class SensorWebConfigCapabilityTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def contract(self, target, profile="full", platform="ESP32_PLATFORM", admin=True,
                 builder=None):
        # Supply an already-resolved environment snapshot. Execute the actual
        # builder functions; never initialize PlatformIO or invoke a build.
        config = [["env:" + target, [["build_flags", ["-DADMIN_PASSWORD=fixture"]
                                    if admin else []]]]]
        script = r'''
source "$1"
pio() { printf 'PlatformIO is forbidden in this host fixture\n' >&2; exit 99; }
TARGET=$2
BUILD_PROFILE_FOR_TARGET=$3
PIO_ENV_PLATFORM_BY_NAME[$TARGET]=$4
PIO_ENV_FULL_BUILD_BY_NAME[$TARGET]=1
PIO_ENV_FULL_WIFI_OTA_BY_NAME[$TARGET]=1
PIO_ENV_OTA_BY_NAME[$TARGET]=1
PIO_CONFIG_JSON=$5
ESP32_FULL_BUILD=1
BUILD_CAPABILITIES=()
BUILD_EXPECTATIONS=()
BUILD_APPLICATION_EXPECTATIONS=()
declare_build_capability_contract "$TARGET" "${PIO_ENV_PLATFORM_BY_NAME[$TARGET]}"
status=$?
[ "$status" = 0 ] || exit "$status"
for value in "${BUILD_CAPABILITIES[@]}"; do printf 'C:%s\0' "$value"; done
for value in "${BUILD_EXPECTATIONS[@]}"; do printf 'E:%s\0' "$value"; done
for value in "${BUILD_APPLICATION_EXPECTATIONS[@]}"; do printf 'A:%s\0' "$value"; done
'''
        result = subprocess.run(["bash", "-c", script, "sensor-capability-fixture",
            str(builder or ROOT / "build_legacy.sh"), target, profile, platform,
            json.dumps(config)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        records = {key: [] for key in "CEA"}
        for value in result.stdout.split(b"\0"):
            if value:
                key, text = value.decode().split(":", 1)
                records[key].append(text)
        return records

    def test_full_sensors_do_not_claim_an_unimplemented_portal(self):
        for target in ("heltec_v3_sensor", "heltec_ct62_sensor", "mke_s3_sensor",
                       "meshnology_w12_sensor", "Station_G3_ESP32_sensor",
                       "Station_G3_ESP32_r2_sensor", "test_sensor_",
                       "test_sensor_lora_ota_no_external_sensors"):
            with self.subTest(target=target):
                contract = self.contract(target)
                self.assertNotIn("web.webconfig=start webconfig", contract["E"])
                self.assertIn("ota.update.wifi=Started: http://%s/update", contract["E"])
                if not target.endswith("lora_ota_no_external_sensors"):
                    self.assertIn("ota.update.lora=image_hash MISMATCH after decode",
                                  contract["E"])

    def test_existing_infrastructure_and_full_companion_portals_remain(self):
        for target in ("heltec_v3_repeater", "Station_G2_repeater",
                       "test_repeater_observer_mqtt", "heltec_v3_room_server",
                       "test_room_server_observer_mqtt", "heltec_v3_companion_radio_full"):
            with self.subTest(target=target):
                self.assertIn("web.webconfig=start webconfig", self.contract(target)["E"])
        for profile, platform, admin in (("standard", "ESP32_PLATFORM", True),
                                         ("full", "NRF52_PLATFORM", True),
                                         ("full", "ESP32_PLATFORM", False)):
            with self.subTest(profile=profile, platform=platform, admin=admin):
                self.assertNotIn("web.webconfig=start webconfig",
                                 self.contract("test_repeater", profile, platform, admin)["E"])

    def test_packaged_sensor_manifest_keeps_real_ota_without_false_webconfig(self):
        contract = self.contract("heltec_v3_sensor")
        with tempfile.TemporaryDirectory(prefix="sensor-capabilities-") as directory:
            work = Path(directory)
            image = work / "firmware.elf"
            application = work / "firmware.bin"
            partitions = work / "partitions.bin"
            manifest = work / "capabilities.json"
            # Generic CLI text must not qualify a portal without callbacks.
            markers = [value.split("=", 1)[1] for value in contract["E"] + contract["A"]]
            image.write_bytes("\0".join(["start webconfig", *markers]).encode())
            application.write_bytes(image.read_bytes())
            def entry(kind, subtype, address, size):
                return struct.pack("<HBBII16sI", 0x50AA, kind, subtype, address, size, b"test", 0)
            partitions.write_bytes(entry(1, 0, 0xe000, 0x2000)
                + entry(0, 0x10, 0x10000, 0x640000)
                + entry(0, 0x11, 0x650000, 0x640000))
            args = [sys.executable, "-B", str(ROOT / "scripts/check_firmware_capabilities.py"),
                    "--image", str(image), "--output", str(manifest),
                    "--target", "heltec_v3_sensor", "--platform", "ESP32_PLATFORM",
                    "--build-profile", "full", "--require-ota", "--firmware-bin", str(application),
                    "--partitions", str(partitions)]
            for option, key in (("--capability", "C"), ("--expect", "E"), ("--expect-application", "A")):
                for value in contract[key]:
                    args.extend([option, value])
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            produced = json.loads(manifest.read_text())
            self.assertTrue(produced["verified"])
            self.assertTrue(produced["ota_update_verified"])
            self.assertEqual(set(produced["ota_update_methods"]), {"wifi", "lora"})
            self.assertNotIn("web.webconfig", produced["capabilities"])

    def test_actual_sensor_callbacks_reject_portal_but_keep_browser_ota(self):
        sensor = (ROOT / "examples/simple_sensor/SensorMesh.h").read_text()
        self.assertIn("public CommonCLICallbacks", sensor)
        for path in (ROOT / "examples/simple_sensor").glob("*"):
            if path.suffix in (".h", ".cpp"):
                self.assertNotIn("startWebConfig(", path.read_text())
                self.assertNotIn("stopWebConfig(", path.read_text())
        header = (ROOT / "src/helpers/CommonCLI.h").read_text()
        defaults = "\n".join(extract_braced(header, signature) for signature in (
            "virtual bool startWebConfig(", "virtual bool stopWebConfig(",
            "virtual bool stopWebConfigForOTA(", "virtual bool isWebConfigActive("))
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        start = source.index('    } else if (memcmp(command, "start webconfig", 15)')
        web = source[start:source.index("\n#endif", start)]
        start = source.index('    } else if (memcmp(command, "start ota", 9)')
        ota = source[start:source.index('    } else if (memcmp(command, "clock", 5)', start)]
        fixture = r'''
#define MESH_MANAGEMENT_PASSWORD_HOST_TEST 1
#include <helpers/esp32/ManagementPassword.h>
#include <helpers/CLIReplyText.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
struct Callbacks {
@DEFAULTS@
};
struct SensorCallbacks : Callbacks {};
struct PortalCallbacks : Callbacks {
  bool startWebConfig(bool ap, char* reply) override {
    strcpy(reply, ap ? "portal AP started" : "portal started"); return true;
  }
  bool stopWebConfig(char* reply) override { strcpy(reply, "portal stopped"); return true; }
};
struct Board {
  int starts = 0, stops = 0;
  bool forced = false;
  bool startOTAUpdate(const char* name, char* reply, bool ap) {
    assert(strcmp(name, "sensor") == 0); ++starts; forced = ap;
    strcpy(reply, "Started: http://fixture/update"); return true;
  }
  bool stopOTAUpdate(char* reply) { ++stops; strcpy(reply, "Stopped OTA"); return true; }
};
struct Prefs { const char* node_name = "sensor"; char password[16] = "password"; };
struct CLI {
  Callbacks* _callbacks;
  Board* _board;
  Prefs* _prefs;
  void run(const char* input, char* reply) {
    char command[160] = {};
    strcpy(command, input);
    reply[0] = 0;
    if (false) {
@WEB@
@OTA@
    }
  }
};
int main() {
  SensorCallbacks sensor;
  Board board;
  Prefs prefs;
  CLI cli{&sensor, &board, &prefs};
  char reply[160] = {};
  for (const char* command : {"start webconfig", "start webconfig ap", "stop webconfig"}) {
    cli.run(command, reply);
    assert(strcmp(reply, "ERR: webconfig not supported on this build") == 0);
    assert(!sensor.isWebConfigActive());
    assert(board.starts == 0 && board.stops == 0);
  }
  cli.run("start webconfig invalid", reply);
  assert(strcmp(reply, "ERR: usage start webconfig [ap]") == 0);
  cli.run("start ota", reply);
  assert(strcmp(reply, "Started: http://fixture/update") == 0);
  assert(board.starts == 1 && !board.forced);
  cli.run("start ota ap", reply);
  assert(strcmp(reply, "Started: http://fixture/update") == 0);
  assert(board.starts == 2 && board.forced);
  cli.run("start ota invalid", reply);
  assert(strcmp(reply, "ERR: usage start ota [ap]") == 0 && board.starts == 2);
  cli.run("stop ota", reply);
  assert(strcmp(reply, "Stopped OTA") == 0 && board.stops == 1);
  PortalCallbacks portal;
  cli._callbacks = &portal;
  cli.run("start webconfig", reply);
  assert(strcmp(reply, "portal started") == 0);
  cli.run("start webconfig ap", reply);
  assert(strcmp(reply, "portal AP started") == 0);
  cli.run("stop webconfig", reply);
  assert(strcmp(reply, "portal stopped") == 0);
}
'''
        fixture = fixture.replace("@DEFAULTS@", defaults).replace("@WEB@", web).replace("@OTA@", ota)
        self.compile_and_run(fixture, "-DESP_PLATFORM=1", "-DADMIN_PASSWORD=fixture")

    def test_unpatched_builder_is_a_meaningful_negative_control(self):
        source = (ROOT / "build_legacy.sh").read_text()
        guard = '      && ! is_esp32_companion_build "$env_name" \\\n      && ! is_sensor_role_target "$env_name"; then'
        self.assertIn(guard, source)
        old = source.replace(guard, '      && ! is_esp32_companion_build "$env_name"; then', 1)
        with tempfile.TemporaryDirectory(prefix="sensor-old-contract-") as directory:
            builder = Path(directory) / "build_legacy.sh"
            builder.write_text(old)
            self.assertIn("web.webconfig=start webconfig", self.contract("heltec_v3_sensor", builder=builder)["E"])

    def test_suite_is_explicitly_wired_in_ci(self):
        workflow = (ROOT / ".github/workflows/run-unit-tests.yml").read_text()
        self.assertIn("          python3 -B test/test_sensor_webconfig_capabilities.py -v\n", workflow)


if __name__ == "__main__":
    unittest.main()
