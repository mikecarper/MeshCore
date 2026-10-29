#!/usr/bin/env python3
"""Exercise Full infrastructure console ownership without stopping RF/network service."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <string>
static std::deque<unsigned char> input;
static bool kiss_active = false, reset_pending = false, network_pending = false;
static bool inject_late_fend=false;
static unsigned reset_reads = 0, kiss_resets = 0, ascii_commands = 0;
static unsigned board_loops = 0, ethernet_loops = 0, network_commands = 0;
static unsigned serial_cancels = 0, terminal_drains = 0;
struct Stream {
  int available() const {
    if(inject_late_fend){inject_late_fend=false;input={0xc0,0x00,0x41,0xc0};}
    return int(input.size());
  }
  int peek()const{return input.empty()?-1:input.front();}
  int read() {
    assert(!input.empty() && input.front() != 0xc0);
    int value = input.front(); input.pop_front(); return value;
  }
  void print(char) { assert(!kiss_active); }
  void println(const char*) { assert(!kiss_active); }
  void printf(const char*, ...) { assert(!kiss_active); }
} console;
namespace mesh {
void serviceUsbLoggingPort() {}
void serviceUsbTerminalPort() { assert(!kiss_active); ++terminal_drains; }
bool takeUsbTerminalSessionReset() { ++reset_reads; bool value=reset_pending; reset_pending=false; return value; }
bool tryCompleteUsbTerminalSessionReset() { return true; }
bool canAcceptUsbConsoleCommand() { return true; }
Stream& usbConsolePort() { return console; }
void noteUsbLoggingStatsCommand(const char*) {}
void noteUsbConsoleCommand(const char*) {}
namespace power { void serviceRegulatorSettings() {} }
#if MESH_ENABLE_HOST_KISS
namespace hostkiss {
void resetUsbSession() { ++kiss_resets; kiss_active=false; }
bool usbInputReserved(int next){return kiss_active || next==0xc0;}
bool serviceUsb() {
  if (!input.empty() && input.front()==0xc0) {
    input.pop_front(); kiss_active=true;
  }
  bool owned=kiss_active;
  if (kiss_active) {
    while (!input.empty()) {
      unsigned char value=input.front(); input.pop_front();
      if (value==0xff) { kiss_active=false; break; }
    }
  }
  return owned;
}
}
#endif
}
struct Mesh {
  bool hasPendingSerialOutput() { return false; }
  void cancelPendingSerialOutput() { ++serial_cancels; }
  void handleUsbCommand(const char* text, char* reply) {
    assert(!kiss_active && !strcmp(text,"ver")); ++ascii_commands; strcpy(reply,"version");
  }
  void cancelLocalOutput(Stream&) {}
  bool hasPendingLocalOutput() { return false; }
  void handleLocalCommand(const char* text, char* reply, Stream&) {
    assert(!strcmp(text,"get kiss")); ++network_commands; strcpy(reply,"status");
  }
} the_mesh;
struct Board { void loop() { ++board_loops; } } board;
struct Ethernet : Stream { bool connected() { return true; } } ethernet_client;
static char ethernet_command[160];
void ethernet_loop_maintain() { ++ethernet_loops; }
bool ethernet_take_session_reset() { return false; }
bool ethernet_read_line(char* dest, size_t) {
  if (!network_pending) return false;
  network_pending=false; strcpy(dest,"get kiss"); return true;
}
bool ethernet_handle_command(const char*, char*) { return false; }
void ethernet_send_reply(const char* text) { assert(!strcmp(text,"status")); }
static constexpr size_t LOCAL_SERIAL_COMMAND_MAX=159;
static char command[LOCAL_SERIAL_COMMAND_MAX+2] = {};
static bool command_overflow=false;
@PUMP@
static void ascii() { for (unsigned char c : std::string("ver\r")) input.push_back(c); }
int main() {
  ascii(); serviceCommandInterfaces();
  assert(ascii_commands==1 && board_loops==1 && ethernet_loops==1);
#if MESH_ENABLE_HOST_KISS
  strcpy(command,"old prefix"); command_overflow=true;
  input={0xc0,0x00,0x41,0x42}; network_pending=true;
  unsigned old_drains=terminal_drains;
  serviceCommandInterfaces();
  assert(kiss_active && input.empty());
  assert(command[0]==0 && !command_overflow && ascii_commands==1);
  assert(board_loops==2 && ethernet_loops==2 && network_commands==1);
  assert(terminal_drains==old_drains);
#if MESH_USB_CONSOLE_COOPERATIVE
  assert(serial_cancels>0);
#endif
  // A binary session with no new USB bytes must keep ownership and service
  // unrelated network commands. The owner must not return out of the pump.
  network_pending=true; serviceCommandInterfaces();
  assert(kiss_active && board_loops==3 && ethernet_loops==3 && network_commands==2);
  assert(terminal_drains==old_drains);
  // EXIT returns ownership but its poll still bypasses the ASCII parser.
  input={0xff}; serviceCommandInterfaces();
  assert(!kiss_active && ascii_commands==1);
  ascii(); serviceCommandInterfaces(); assert(ascii_commands==2);
#if MESH_USB_CONSOLE_COOPERATIVE
  input={0xc0,0x00,0x43}; serviceCommandInterfaces(); assert(kiss_active);
  reset_pending=true;
  unsigned previous_resets=reset_reads;
  strcpy(command,"stale"); command_overflow=true;
  serviceCommandInterfaces();
  assert(!kiss_active && kiss_resets==1 && reset_reads==previous_resets+1);
  assert(command[0]==0 && !command_overflow);
  ascii(); serviceCommandInterfaces(); assert(ascii_commands==3);
#endif
  // The first FEND arrives after serviceUsb() returned false this poll.
  inject_late_fend=true;const unsigned prior_commands=ascii_commands;
  serviceCommandInterfaces();
  assert(!kiss_active && !input.empty() && input.front()==0xc0 && ascii_commands==prior_commands);
  serviceCommandInterfaces();assert(kiss_active && input.empty());
#endif
}
'''


class HostKissInfrastructureTest(unittest.TestCase):
    def test_release_builder_selects_only_full_supported_roles(self):
        builder = (ROOT / "build_legacy.sh").read_text()
        promotion_start = builder.index('  if [ "$ESP32_FULL_BUILD" = "1" ] \\\n      || is_companion_radio_full_target "$env_name"; then')
        promotion_end = builder.index('\n  fi', promotion_start) + len('\n  fi')
        kiss_start = builder.index('  # Full LoRa images retain their normal role')
        kiss_end = builder.index('  declare_build_capability_contract ', kiss_start)
        policy = builder[promotion_start:promotion_end] + '\n' + builder[kiss_start:kiss_end]
        # Execute the release builder's actual promotion and feature-selection
        # blocks, using its target classifiers. PlatformIO must never run here.
        script = r'''
source "$1/build_legacy.sh"
pio() { printf 'Unexpected PlatformIO invocation\n' >&2; return 99; }
apply_policy() {
  local env_name=$1 env_platform=$2 BUILD_PROFILE_FOR_TARGET=$3
  local NRF52_OTA_SENSOR_PROFILE=$4 ESP32_FULL_BUILD=$5
  local PLATFORMIO_BUILD_FLAGS='-DEXISTING_FLAG=1'
  PIO_ENV_PLATFORM_BY_NAME[$env_name]=$env_platform
@POLICY@
  case "$PLATFORMIO_BUILD_FLAGS" in
    *-DMESH_ENABLE_HOST_KISS=1*) actual=1 ;;
    *) actual=0 ;;
  esac
  if [ "$actual" != "$6" ]; then
    printf 'Incorrect KISS build policy: %s/%s/%s/%s -> %s, expected %s\n' \
      "$env_name" "$env_platform" "$BUILD_PROFILE_FOR_TARGET" \
      "$NRF52_OTA_SENSOR_PROFILE" "$actual" "$6" >&2
    return 1
  fi
}
'''.replace("@POLICY@", policy)
        cases = (
            ("heltec_v3_repeater", "ESP32_PLATFORM", "full", "", "1", "1"),
            ("heltec_v4_companion_radio_full", "ESP32_PLATFORM", "standard", "", "0", "1"),
            ("heltec_v3_repeater", "ESP32_PLATFORM", "standard", "", "0", "0"),
            ("heltec_v3_sensor", "ESP32_PLATFORM", "reduced", "", "0", "0"),
            ("RAK_4631_companion_radio_full", "NRF52_PLATFORM", "standard", "", "0", "1"),
            ("RAK_4631_sensor_lora_ota_no_external_sensors", "NRF52_PLATFORM", "auto", "full", "0", "1"),
            ("RAK_4631_sensor_lora_ota_no_external_sensors", "NRF52_PLATFORM", "auto", "reduced", "0", "0"),
            ("RAK_3401_repeater_lora_ota_no_external_sensors", "NRF52_PLATFORM", "auto", "full", "0", "1"),
            ("RAK_3401_room_server_lora_ota_no_external_sensors", "NRF52_PLATFORM", "auto", "reduced", "0", "0"),
            ("RAK_4631_repeater_rak15002_sd_seeder_only", "NRF52_PLATFORM", "auto", "full", "0", "0"),
            ("heltec_v3_kiss_modem", "ESP32_PLATFORM", "full", "", "1", "0"),
            ("wio_e5_repeater", "STM32_PLATFORM", "full", "", "0", "0"),
            ("xiao_rp2040_repeater", "RP2040_PLATFORM", "full", "", "0", "0"),
        )
        for case in cases:
            script += "apply_policy " + shlex.join(case) + " || exit $?\n"
        result = subprocess.run(["bash", "-c", script, "host-kiss-policy", str(ROOT)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Unexpected PlatformIO invocation", result.stdout + result.stderr)

    def test_actual_repeater_console_preserves_network_and_housekeeping(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        pump = body((ROOT / "examples/simple_repeater/main.cpp").read_text(),
                    "static void __attribute__((noinline)) serviceCommandInterfaces()")
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        for enabled, cooperative, lose_owner in ((0, 0, False), (0, 1, False),
                                                 (1, 0, False), (1, 1, False),
                                                 (1, 1, True), (1, 1, "late")):
            with self.subTest(enabled=enabled, cooperative=cooperative, lose_owner=lose_owner):
                candidate = pump
                if lose_owner == "late":
                    candidate = candidate.replace(
                        "    if (kiss_can_read() && mesh::hostkiss::usbInputReserved(console.peek())) break;\n", "")
                    self.assertNotEqual(candidate, pump)
                elif lose_owner:
                    candidate = candidate.replace("if (kiss_can_read() && mesh::hostkiss::serviceUsb())",
                                                  "if (false && kiss_can_read() && mesh::hostkiss::serviceUsb())")
                    self.assertNotEqual(candidate, pump)
                with tempfile.TemporaryDirectory(prefix="host-kiss-infra-") as directory:
                    directory = Path(directory)
                    (directory / "test.cpp").write_text(FIXTURE.replace("@PUMP@", candidate), encoding="ascii")
                    result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", *sanitizers,
                        "-DMESH_ENABLE_HOST_KISS=" + str(enabled),
                        "-DMESH_USB_CONSOLE_COOPERATIVE=" + str(cooperative), "-DETHERNET_ENABLED=1",
                        str(directory / "test.cpp"), "-o", str(directory / "test")],
                        text=True, capture_output=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(directory / "test")], text=True,
                                            capture_output=True, timeout=10)
                    if lose_owner:
                        self.assertNotEqual(result.returncode, 0,
                                            "Negative control did not lose KISS ownership")
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_infrastructure_roles_claim_before_terminal_output_and_keep_rf_loop(self):
        for role in ("simple_repeater", "simple_room_server", "simple_sensor"):
            with self.subTest(role=role):
                source = (ROOT / "examples" / role / "main.cpp").read_text()
                signature = ("static void __attribute__((noinline)) serviceCommandInterfaces()"
                             if role == "simple_repeater" else "void loop()")
                pump = body(source, signature)
                self.assertEqual(pump.count("takeUsbTerminalSessionReset()"), 1)
                self.assertLess(pump.index("mesh::hostkiss::resetUsbSession()"),
                                pump.index("mesh::hostkiss::serviceUsb()"))
                self.assertLess(pump.index("mesh::hostkiss::serviceUsb()"),
                                pump.index("mesh::serviceUsbTerminalPort()"))
                self.assertIn("usb_ready = false;", pump)
                loop = body(source, "void loop()")
                self.assertIn("the_mesh.loop();", loop)
                self.assertIn("if (!mesh::hostkiss::usbActive())", loop)
                self.assertIn("sleep_guard(!mesh::hostkiss::usbActive()", loop)
                recovery = body(source, "static bool usbLoggingRecoverySafe(")
                self.assertIn("if (mesh::hostkiss::usbActive()) return false;", recovery)

    def test_common_binding_routing_and_loop_use_loaded_radio_profiles(self):
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        management = (ROOT / "src/helpers/CommonCLI_Management.cpp").read_text()
        begin = body(management, "void CommonCLI::beginManagement(")
        self.assertIn("mesh::hostkiss::begin(fs, *radio, _radio_profiles, mesh);", begin)
        command = body(common, "void CommonCLI::handleCommand(")
        self.assertIn("mesh::hostkiss::handleCommand(command, reply, 157, sender_timestamp == 0)", command)
        loop = body(common, "void CommonCLI::loop()")
        guarded = body(loop, "if (!mesh::morseTimingActive(")
        self.assertIn("mesh::hostkiss::loop();", guarded)
        self.assertLess(guarded.index("_radio_profiles.loop();"),
                        guarded.index("mesh::hostkiss::loop();"))


if __name__ == "__main__":
    unittest.main()
