#!/usr/bin/env python3
"""Exercise the actual browser OTA start/stop methods with fake WiFi and servers."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class WiFiOtaStartTest(unittest.TestCase):
    def compile_and_run(self, source, *flags):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="wifi-ota-start-") as directory:
            path = Path(directory)
            (path / "test.cpp").write_text(source, encoding="ascii")
            sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                *sanitizer, *flags, "-I", str(ROOT / "src"), str(path / "test.cpp"), "-o", str(path / "test")],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(path / "test")], capture_output=True,
                                    text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def ap_fixture(self):
        fixture = (ROOT / "test/fixtures/wifi_ota_start.cpp").read_text()
        policy = (ROOT / "src/helpers/esp32/WiFiRadioPolicy.h").read_text()
        mask = re.search(r"static constexpr uint8_t kAccessPointProtocolMask =.*?;",
                         policy, re.DOTALL).group(0)
        owner_policy = policy[policy.index("static constexpr uint8_t kLongRangeRadioOwner ="):]
        owner_policy = owner_policy[:owner_policy.index("// A bridge can start/stop")]
        fixture = fixture.replace("@AP_PROTOCOL_POLICY@", mask + "\n" + owner_policy + extract_braced(
            policy, "inline esp_err_t applyAccessPointProtocolMask()") + "\n"
            + extract_braced(policy, "inline int accessPointChannel()"))
        lifecycle = (ROOT / "src/helpers/esp32/WiFiAccessPointPolicy.h").read_text()
        fixture = fixture.replace("@AP_LIFECYCLE_POLICY@", lifecycle[lifecycle.index("namespace mesh {"):])
        return fixture

    def test_network_instructions_and_switching_on_all_esp32_uploaders(self):
        source = (ROOT / "src/helpers/ESP32Board.cpp").read_text()
        lightweight, other = source.split("#elif defined(ADMIN_PASSWORD) && !defined(DISABLE_WIFI_OTA)", 1)
        fixture = self.ap_fixture()
        for name, implementation, flags in (
            ("lightweight infrastructure", lightweight, ["-DLIGHTWEIGHT_WIFI_OTA=1"]),
            ("lightweight companion", lightweight, ["-DLIGHTWEIGHT_WIFI_OTA=1", "-DCOMPANION_RADIO_FULL=1"]),
            ("AsyncElegantOTA infrastructure", other, []),
        ):
            with self.subTest(uploader=name):
                methods = "\n".join(extract_braced(implementation, signature) for signature in (
                    "bool ESP32Board::startOTAUpdate(", "bool ESP32Board::stopOTAUpdate("))
                for major in (2, 3):
                    with self.subTest(arduino_major=major):
                        self.compile_and_run(fixture.replace("@METHODS@", methods),
                                             *flags, f"-DESP_ARDUINO_VERSION_MAJOR={major}")

    def test_companion_setup_portal_uses_the_same_live_ap_contract(self):
        source = (ROOT / "src/helpers/WiFiSetupPortal.cpp").read_text()
        start = extract_braced(source, "bool WiFiSetupPortal::begin(")
        task = extract_braced(source, "static void portalTask(")
        cleanup = task[task.rindex("\n  impl->dns.stop();"):task.rindex("\n}")]
        endpoints = r'''
static const IPAddress SETUP_IP(192, 168, 4, 1);
using TaskHandle_t = void*;
constexpr int pdPASS = 1;
bool allow_dns = true, allow_task = true;
unsigned task_starts = 0;
struct WiFiServer {
  bool listening = false;
  explicit WiFiServer(int) {}
  void begin() { assert(WiFi.ap_started); listening = true; }
  void stop() { listening = false; }
  void setNoDelay(bool) {}
};
struct DNSServer {
  bool running = false;
  bool start(int, const char*, IPAddress) {
    assert(WiFi.ap_started); running = allow_dns; return running;
  }
  void stop() { running = false; }
};
class WiFiSetupPortal {
public:
  using SaveCallback = bool (*)(void*, const char*, const char*);
  volatile bool _active = false;
  void* _impl = nullptr;
  bool begin(const char*, SaveCallback, void*);
};
struct PortalImpl {
  WiFiServer server{80};
  DNSServer dns;
  TaskHandle_t task = nullptr;
  bool started_radio = false;
  WiFiSetupPortal::SaveCallback save_callback = nullptr;
  void* callback_context = nullptr;
  volatile bool* active = nullptr;
  uint32_t close_ap_at = 0, recovery_interval_ms = 0;
  char recovery_ssid[32] = {}, ap_name[33] = {};
  bool recovery_connecting = false;
};
void vTaskDelete(void*) {}
void portalTask(void* context) {
  auto* impl = static_cast<PortalImpl*>(context);
@CLEANUP@
}
int xTaskCreatePinnedToCore(void (*)(void*), const char*, int, void*, int,
                            TaskHandle_t* task, int) {
  ++task_starts;
  if (!allow_task) return 0;
  *task = reinterpret_cast<void*>(1); return pdPASS;
}
namespace mesh {
struct Log { template<class... Args> void printf(const char*, Args...) {} };
Log& usbLoggingPort() { static Log log; return log; }
}
@START@
int main() {
  constrain_espnow_channel = false;
  WiFi.connected = WiFi.radio = false;
  WiFiSetupPortal portal;
  assert(portal.begin("MeshCore-Setup", nullptr, nullptr));
  auto* impl = static_cast<PortalImpl*>(portal._impl);
  assert(portal._active && impl->started_radio && impl->dns.running && impl->server.listening);
  assert(WiFi.ap_started && WiFi.scans == 1);
  const unsigned starts = WiFi.starts;
  assert(portal.begin("MeshCore-Setup", nullptr, nullptr) && WiFi.starts == starts);
  portalTask(impl);
  assert(!portal._active && !impl->task && !impl->started_radio && !WiFi.radio);
  for (Fault fault : {Fault::Ssid, Fault::StartEvent, Fault::ModeRead}) {
    WiFi.fault = fault;
    assert(!portal.begin("MeshCore-Setup", nullptr, nullptr));
    assert(!portal._active && !impl->task && !WiFi.radio && !WiFi.ap);
  }
  WiFi.fault = Fault::None;
  allow_dns = false;
  assert(!portal.begin("MeshCore-Setup", nullptr, nullptr) && !WiFi.radio);
  allow_dns = true;
  allow_task = false;
  assert(!portal.begin("MeshCore-Setup", nullptr, nullptr));
  assert(!portal._active && !impl->task && !impl->dns.running && !impl->server.listening && !WiFi.radio);
  allow_task = true;
  WiFi.connected = WiFi.radio = true;
  const unsigned scans = WiFi.scans;
  assert(portal.begin("MeshCore-Setup", nullptr, nullptr));
  assert(!impl->started_radio && WiFi.scans == scans && WiFi.connected);
  portalTask(impl);
  assert(WiFi.radio && WiFi.connected);
  WiFi.connected = false;
  constrain_espnow_channel = true;
  assert(portal.begin("MeshCore-Setup", nullptr, nullptr));
  assert(WiFi.ap_channel == 6);
  portalTask(impl);
  assert(WiFi.radio && !WiFi.ap);
  delete impl;
}
'''
        fixture = self.ap_fixture().split("static unsigned server_starts", 1)[0]
        fixture += endpoints.replace("@CLEANUP@", cleanup).replace("@START@", start)
        for major in (2, 3):
            with self.subTest(arduino_major=major):
                self.compile_and_run(fixture, f"-DESP_ARDUINO_VERSION_MAJOR={major}")

    def test_minimal_ota_never_reports_a_stale_sdk_hotspot_as_started(self):
        implementation = (ROOT / "src/helpers/ESP32Board.cpp").read_text().split(
            "#elif defined(ADMIN_PASSWORD) && !defined(DISABLE_WIFI_OTA)", 1)[0]
        methods = "\n".join(extract_braced(implementation, signature) for signature in (
            "bool ESP32Board::startOTAUpdate(", "bool ESP32Board::stopOTAUpdate("))
        fixture = self.ap_fixture().split("int main() {", 1)[0]
        fixture = fixture.replace("@METHODS@", methods) + r'''
int main() {
  ESP32Board board;
  char reply[160] = {};
  WiFi.connected = WiFi.radio = false;
  constrain_espnow_channel = false;
  WiFi.fault = Fault::Ssid; // Setters succeed with an old SSID in the actual SDK.
  assert(!board.startOTAUpdate("test", reply, true));
  assert(strcmp(reply, "ERR: OTA WiFi failed") == 0);
  assert(!board.ota_server && !board.inhibit_sleep && !WiFi.radio);
}
'''
        self.compile_and_run(fixture, "-DLIGHTWEIGHT_WIFI_OTA=1",
                             "-DESP_ARDUINO_VERSION_MAJOR=2")

    def test_common_cli_preserves_actionable_ota_errors(self):
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        start = cli.index('    } else if (memcmp(command, "start ota", 9)')
        end = cli.index('    } else if (memcmp(command, "clock", 5)', start)
        fixture = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
struct Board {
  const char* error = "ERR: OTA WiFi failed";
  bool startOTAUpdate(const char*, char* reply, bool) {
    strcpy(reply, error); return false;
  }
  bool stopOTAUpdate(char* reply) { strcpy(reply, error); return false; }
} board;
struct Prefs { const char* node_name = "test"; } prefs;
struct CLI {
  Board* _board = &board;
  Prefs* _prefs = &prefs;
  void run(const char* command, char* reply) {
    if (false) {
@CLI@
    }
  }
};
int main() {
  CLI cli;
  char reply[160] = {};
  for (const char* command : {"start ota", "start ota ap", "stop ota"}) {
    cli.run(command, reply);
    assert(strcmp(reply, "ERR: OTA WiFi failed") == 0);
    board.error = "ERR: OTA server failed";
    cli.run(command, reply);
    assert(strcmp(reply, "ERR: OTA server failed") == 0);
    board.error = "";
    cli.run(command, reply);
    assert(strcmp(reply, "Error") == 0);
    board.error = "ERR: OTA WiFi failed";
  }
}
'''
        self.compile_and_run('#include <initializer_list>\n' + fixture.replace("@CLI@", cli[start:end]))

    def test_common_cli_stops_webconfig_and_reports_the_handoff(self):
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        start = cli.index('    } else if (memcmp(command, "start ota", 9)')
        end = cli.index('    } else if (memcmp(command, "clock", 5)', start)
        fixture = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
struct Callbacks {
  bool active = true, allow_stop = true, mqtt = true, espnow = true;
  int stops = 0, bridge_stops = 0, bridge_starts = 0;
  int espnow_stops = 0, espnow_starts = 0;
  bool isWebConfigActive() const { return active; }
  bool stopWebConfigForOTA(char* reply) {
    ++stops;
    if (!allow_stop) { strcpy(reply, "ERR: handoff failed"); return false; }
    active = false; strcpy(reply, "WebConfig stopped"); return true;
  }
  bool isMqttBridgeRunning() const { return mqtt; }
  bool isEspNowBridgeRunning() const { return espnow; }
  bool setMqttBridgeState(bool enabled) {
    if (enabled) ++bridge_starts; else ++bridge_stops;
    mqtt = enabled; return true;
  }
  bool setEspNowBridgeState(bool enabled) {
    if (enabled) ++espnow_starts; else ++espnow_stops;
    espnow = enabled; return true;
  }
} callbacks;
struct Board {
  bool allow_start = true, running = false;
  int starts = 0;
  bool isOTAUpdateRunning() const { return running; }
  bool startOTAUpdate(const char*, char* reply, bool) {
    assert(!callbacks.active && !callbacks.espnow); ++starts;
    if (allow_start) running = true;
    strcpy(reply, allow_start ? "Started: http://10.20.30.40/update - Use same WiFi/LAN"
                             : "ERR: OTA WiFi failed");
    return allow_start;
  }
  bool stopOTAUpdate(char*) { running = false; return true; }
} board;
struct Prefs {
  const char* node_name = "test";
  bool bridge_enabled = false, espnow_bridge_enabled = false;
} prefs;
struct CLI {
  Board* _board = &board;
  Prefs* _prefs = &prefs;
  Callbacks* _callbacks = &callbacks;
  bool _wifi_ota_resume_mqtt = false, _wifi_ota_resume_espnow = false;
  void run(const char* command, char* reply) {
    if (false) {
@CLI@
    }
  }
};
int main() {
  CLI cli;
  struct { char text[160] = {}; unsigned guard = 123; } reply;
  cli.run("start ota invalid", reply.text);
  assert(strstr(reply.text, "usage") && callbacks.stops == 0 && board.starts == 0);
  callbacks.allow_stop = false;
  cli.run("start ota", reply.text);
  assert(strcmp(reply.text, "ERR: handoff failed") == 0 && board.starts == 0);
  callbacks.allow_stop = true;
  cli.run("start ota", reply.text);
  assert(strstr(reply.text, "http://10.20.30.40/update") && strstr(reply.text, "; WebConfig stopped"));
  assert(callbacks.stops == 2 && board.starts == 1 && callbacks.bridge_stops == 1);
  assert(callbacks.espnow_stops == 1 && !callbacks.mqtt && !callbacks.espnow);
  cli.run("start ota ap", reply.text);
  assert(!strstr(reply.text, "WebConfig stopped") && callbacks.stops == 2 && board.starts == 2);
  callbacks.active = true;
  board.allow_start = false;
  cli.run("start ota", reply.text);
  assert(strcmp(reply.text, "ERR: OTA WiFi failed; WebConfig stopped") == 0);
  assert(callbacks.stops == 3 && board.starts == 3 && callbacks.bridge_stops == 1);
  cli.run("stop ota", reply.text);
  assert(callbacks.bridge_starts == 0 && callbacks.espnow_starts == 0);
  // Saving intent after an inactive attempt must not manufacture a resume.
  prefs.bridge_enabled = true;
  prefs.espnow_bridge_enabled = true;
  cli.run("stop ota", reply.text);
  assert(callbacks.bridge_starts == 0 && callbacks.espnow_starts == 0);
  callbacks.espnow = true; // Only ESP-NOW was actually running on entry.
  board.allow_start = true;
  cli.run("start ota ap", reply.text);
  cli.run("start ota ap", reply.text); // Repeat start preserves the first snapshot.
  prefs.bridge_enabled = false;
  cli.run("stop ota", reply.text);
  assert(callbacks.bridge_starts == 0 && callbacks.espnow_starts == 1);
  assert(callbacks.espnow && !callbacks.mqtt);
  // A stopped setup session with both saved defaults enabled stays stopped.
  callbacks.espnow = false;
  prefs.bridge_enabled = true;
  callbacks.active = true;
  cli.run("start ota ap", reply.text);
  cli.run("stop ota", reply.text);
  assert(callbacks.bridge_starts == 0 && callbacks.espnow_starts == 1);
  assert(reply.guard == 123);
}
'''
        self.compile_and_run(fixture.replace("@CLI@", cli[start:end]),
                             "-DESP_PLATFORM=1", "-DADMIN_PASSWORD=1",
                             "-DWITH_MQTT_BRIDGE=1", "-DLIGHTWEIGHT_WIFI_OTA=1",
                             "-DWITH_ESPNOW_BRIDGE=1")

    def test_webconfig_teardown_cannot_turn_ota_wifi_off(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "void WebConfigServer::requestStop(", "bool WebConfigServer::stopForOTA(",
            "void WebConfigServer::finalizeTeardown(",
            "bool WebConfigServer::isNetworkForegroundActive(",
            "void WebConfigServer::refreshNetworkPowerHold("))
        fixture = (ROOT / "test/fixtures/webconfig_ota_handoff.cpp").read_text()
        self.compile_and_run(fixture.replace("@METHODS@", methods))

    def test_non_mqtt_espnow_ota_pauses_wireless_and_restores_saved_intent(self):
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        start = cli.index('    } else if (memcmp(command, "start ota", 9)')
        end = cli.index('    } else if (memcmp(command, "clock", 5)', start)
        fixture = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
bool ota = false;
struct Callbacks {
  bool web = false, espnow = true, uart = true;
  bool allow_pause = true, allow_resume = true;
  int pauses = 0, resumes = 0, uart_changes = 0;
  bool isWebConfigActive() const { return web; }
  bool stopWebConfigForOTA(char*) { web = false; return true; }
  bool isEspNowBridgeRunning() const { return espnow; }
  bool setEspNowBridgeState(bool enabled) {
    if (enabled) {
      ++resumes; assert(!ota);
      if (!allow_resume) return false;
    } else {
      ++pauses;
      if (!allow_pause) return false;
    }
    espnow = enabled; return true;
  }
  bool setBridgeState(bool enabled) { ++uart_changes; uart = enabled; return true; }
} callbacks;
struct Board {
  bool allow_start = true, allow_stop = true;
  int starts = 0;
  bool isOTAUpdateRunning() const { return ota; }
  bool startOTAUpdate(const char*, char* reply, bool) {
    assert(!callbacks.espnow && !callbacks.web); ++starts;
    ota = allow_start;
    strcpy(reply, allow_start ? "Started: http://192.168.4.1/update"
                             : "ERR: OTA WiFi failed");
    return allow_start;
  }
  bool stopOTAUpdate(char* reply) {
    if (allow_stop) ota = false;
    strcpy(reply, allow_stop ? "OK - OTA stopped" : "ERR: OTA upload active");
    return allow_stop;
  }
} board;
struct Prefs {
  const char* node_name = "test";
  bool bridge_enabled = true, espnow_bridge_enabled = true;
} prefs;
struct CLI {
  Board* _board = &board;
  Prefs* _prefs = &prefs;
  Callbacks* _callbacks = &callbacks;
  bool _wifi_ota_resume_mqtt = false, _wifi_ota_resume_espnow = false;
  void run(const char* command, char* reply) {
    if (false) {
@CLI@
    }
  }
};
int main() {
  CLI cli;
  struct { char text[160] = {}; unsigned guard = 42; } reply;
  cli.run("start ota invalid", reply.text);
  assert(strstr(reply.text, "usage") && callbacks.pauses == 0 && board.starts == 0);
  callbacks.allow_pause = false;
  cli.run("start ota", reply.text);
  assert(strcmp(reply.text, "ERR: could not pause ESP-NOW for OTA") == 0);
  assert(board.starts == 0 && callbacks.espnow && callbacks.uart);
  callbacks.allow_pause = true;
  callbacks.web = true;
  cli.run("start ota ap", reply.text);
  assert(ota && !callbacks.espnow && callbacks.uart);
  assert(strstr(reply.text, "; ESP-NOW paused") && strstr(reply.text, "; WebConfig stopped"));
  assert(prefs.bridge_enabled && prefs.espnow_bridge_enabled);
  const int resumed_before_failure = callbacks.resumes;
  board.allow_stop = false;
  cli.run("stop ota", reply.text);
  assert(strcmp(reply.text, "ERR: OTA upload active") == 0);
  assert(callbacks.resumes == resumed_before_failure && !callbacks.espnow);
  board.allow_stop = true;
  cli.run("stop ota", reply.text);
  assert(!ota && callbacks.espnow && callbacks.uart);
  assert(strstr(reply.text, "; ESP-NOW resumed"));
  board.allow_start = false;
  cli.run("start ota", reply.text);
  assert(strcmp(reply.text, "ERR: OTA WiFi failed") == 0 && callbacks.espnow);
  callbacks.allow_resume = false;
  cli.run("start ota", reply.text);
  assert(strcmp(reply.text, "ERR: OTA WiFi failed; ESP-NOW resume failed") == 0);
  assert(!callbacks.espnow && callbacks.uart);
  cli.run("stop ota", reply.text);
  assert(strstr(reply.text, "; ESP-NOW resume failed"));
  // A fresh successful OTA must preserve an outstanding failed resume.
  board.allow_start = true;
  cli.run("start ota", reply.text);
  assert(ota && !callbacks.espnow);
  callbacks.allow_resume = true;
  cli.run("stop ota", reply.text);
  assert(!ota && callbacks.espnow && strstr(reply.text, "; ESP-NOW resumed"));
  callbacks.espnow = false;
  // A disabled secondary must stay disabled after the upload stops.
  prefs.espnow_bridge_enabled = false;
#if !defined(WITH_RS232_BRIDGE)
  prefs.bridge_enabled = false;
#endif
  callbacks.allow_resume = true;
  board.allow_start = true;
  const int resumes = callbacks.resumes;
  cli.run("start ota", reply.text);
  assert(!strstr(reply.text, "ESP-NOW") && !callbacks.espnow);
  cli.run("stop ota", reply.text);
  assert(callbacks.resumes == resumes && !callbacks.espnow);
  assert(callbacks.uart_changes == 0 && callbacks.uart && reply.guard == 42);
}
'''
        for profile, flags in (
            ("rs232_composite", ["-DWITH_RS232_BRIDGE=1"]),
            ("merged_sole", ["-DESPNOW_BRIDGE_MERGED=1"]),
            ("legacy_dedicated_sole", []),
        ):
            with self.subTest(profile=profile):
                self.compile_and_run(fixture.replace("@CLI@", cli[start:end]),
                    "-DESP_PLATFORM=1", "-DADMIN_PASSWORD=1", "-DWITH_ESPNOW_BRIDGE=1", *flags)

    def test_background_bridge_retry_waits_until_ota_stops(self):
        fixture = r'''
#include <cassert>
#include <cstdint>
uint32_t now = 0;
uint32_t millis() { return now; }
bool millisHasNowPassed(uint32_t deadline) { return int32_t(now - deadline) >= 0; }
struct Board {
  bool ota = true;
  bool isOTAUpdateRunning() const { return ota; }
} board;
struct Cli { Board* getBoard() { return &board; } };
struct Bridge {
  unsigned starts = 0;
  bool running = false;
  bool isRunning() const { return running; }
  void begin() { ++starts; running = true; }
};
using ESPNowBridge = Bridge;
struct WiFiMock { bool isConnected() { return true; } } WiFi;
struct Mesh {
  Cli _cli;
  struct { bool bridge_enabled = true, espnow_bridge_enabled = true; } _prefs;
  Bridge espnow_bridge;
  @SOLE_MEMBER@
  Bridge* @MQTT_MEMBER@ = nullptr;
  uint32_t shared_espnow_retry_at = 0;
  void configureBridgeFilter(Bridge*) {}
  @RETRY@
};
int main() {
  Mesh mesh;
  // Simulate repeated servicePostMeshLoop calls, spanning several retry windows.
  for (now = 0; now <= 30000; now += 100) {
    assert(!mesh.startSharedEspNowBridgeIfReady());
    assert(mesh.@TRANSPORT@.starts == 0);
  }
  board.ota = false;
  assert(mesh.startSharedEspNowBridgeIfReady());
  assert(mesh.@TRANSPORT@.starts == 1);
  assert(mesh.startSharedEspNowBridgeIfReady());
  assert(mesh.@TRANSPORT@.starts == 1);
  mesh.@TRANSPORT@.running = false;
  mesh._prefs.espnow_bridge_enabled = false;
  mesh._prefs.bridge_enabled = false;
  now += 30000;
  assert(!mesh.startSharedEspNowBridgeIfReady());
  assert(mesh.@TRANSPORT@.starts == 1);
}
'''
        for role, mqtt_member, transport, flags in (
            ("simple_repeater", "mqtt_bridge", "espnow_bridge", ["-DWITH_MQTT_BRIDGE=1"]),
            ("simple_repeater", "mqtt_bridge", "espnow_bridge", ["-DWITH_RS232_BRIDGE=1"]),
            ("simple_repeater", "mqtt_bridge", "bridge", []),
            ("simple_room_server", "bridge", "espnow_bridge", ["-DWITH_MQTT_BRIDGE=1"]),
        ):
            with self.subTest(role=role, flags=flags):
                source = (ROOT / "examples" / role / "MyMesh.h").read_text()
                retry = extract_braced(source, "bool startSharedEspNowBridgeIfReady()")
                self.compile_and_run(fixture.replace("@RETRY@", retry)
                    .replace("@MQTT_MEMBER@", mqtt_member).replace("@TRANSPORT@", transport)
                    .replace("@SOLE_MEMBER@", "Bridge bridge;" if role == "simple_repeater" else ""), *flags)

    def test_full_companion_cli_reports_webconfig_stop_and_hardware_errors(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        command = extract_braced(source, 'if (strcmp(command, "start ota") == 0')
        fixture = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
struct WebConfig {
  bool active = true, allowed = true;
  bool stopForOTA(char* reply) {
    if (!allowed) { strcpy(reply, "ERR: handoff failed"); return false; }
    active = false; return true;
  }
} portal;
struct Board {
  bool allowed = true;
  int starts = 0;
  bool startOTAUpdate(const char*, char* reply, bool) {
    assert(!portal.active); ++starts;
    strcpy(reply, allowed ? "Started: http://192.168.4.1:8080/update - Join WiFi MeshCore-OTA"
                          : "ERR: this partition layout requires a USB firmware update");
    return allowed;
  }
} board;
struct Mesh {
  struct { const char* node_name = "test"; } _prefs;
  WebConfig* _webconfig = &portal;
  bool isWebConfigActiveOrStopping() { return portal.active; }
  bool run(const char* command, char* reply, size_t reply_size) {
    @COMMAND@
    return false;
  }
};
int main() {
  Mesh mesh;
  char reply[160] = {};
  portal.allowed = false;
  assert(mesh.run("start ota", reply, sizeof(reply)));
  assert(strcmp(reply, "ERR: handoff failed") == 0 && board.starts == 0);
  portal.allowed = true;
  assert(mesh.run("start ota", reply, sizeof(reply)));
  assert(strstr(reply, ":8080/update") && strstr(reply, "; WebConfig stopped"));
  assert(board.starts == 1);
  portal.active = true;
  board.allowed = false;
  assert(mesh.run("start ota ap", reply, sizeof(reply)));
  assert(strstr(reply, "partition layout") && strstr(reply, "; WebConfig stopped"));
  struct { char text[20]; unsigned guard = 42; } short_reply;
  assert(mesh.run("start ota", short_reply.text, sizeof(short_reply.text)));
  assert(short_reply.guard == 42 && short_reply.text[19] == 0);
}
'''
        self.compile_and_run(fixture.replace("@COMMAND@", command), "-DWITH_WEBCONFIG=1")

    def test_lightweight_stop_keeps_slow_client_state_until_task_exits(self):
        source = (ROOT / "src/helpers/ESP32Board.cpp").read_text()
        implementation = source[source.index("class LightweightOTAServer {"):]
        method = extract_braced(implementation, "bool end()")
        fixture = r'''
#include <cassert>
#include <cstdint>
static unsigned delays = 0;
static void delay(unsigned value) { assert(value == 10); ++delays; }
struct Server { void stop() {} };
struct Updater {
  bool running = true;
  Server server;
  void* task = reinterpret_cast<void*>(1);
  void* board = reinterpret_cast<void*>(2);
  struct { void clear() {} } identity;
  @END@
};
int main() {
  Updater updater;
  assert(!updater.end());
  assert(!updater.running && updater.task && updater.board && delays == 100);
  updater.task = nullptr; // Slow network write has now returned and task exited.
  assert(updater.end() && !updater.board && delays == 100);
}
'''
        self.compile_and_run(fixture.replace("@END@", method))


if __name__ == "__main__":
    unittest.main()
