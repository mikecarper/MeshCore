#!/usr/bin/env python3
"""Exercise actual Companion and GPS startup with shared hardware UARTs."""

from pathlib import Path
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_companion_hardware_serial import conditional_block
from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SANITIZERS = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])


HARNESS = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <deque>
#include <limits>
#include <string>
#include <vector>
#include "helpers/ArduinoSerialInterface.h"
#include "helpers/MultiSerialInterface.h"
#include "helpers/UsbAsciiBinarySwitch.h"
#ifndef ARDUINO_USB_MODE
#define ARDUINO_USB_MODE 0
#endif
#define MESH_DEBUG_PRINTLN(...) ((void)0)
@STREAM@
// HardwareSerial objects selecting the same port share pins, baud, and FIFO,
// as the ESP32 backend indexes _uart_bus_array by UART number.
struct Uart {
  int rx = -1, tx = -1;
  unsigned baud = 0;
  bool installed = false;
  BufferStream fifo;
};
static std::array<Uart, 3> uarts;
class HardwareSerial : public Stream {
public:
  const unsigned port;
  explicit HardwareSerial(unsigned selected) : port(selected) {
    assert(port < uarts.size());
  }
  Uart& state() const { return uarts[port]; }
  void setPins(int receive, int transmit) { state().rx = receive; state().tx = transmit; }
  void begin(unsigned speed) { state().baud = speed; state().installed = true; }
  void end() { assert(state().installed); state().installed = false; state().fifo.input.clear(); }
  int available() override { return state().fifo.available(); }
  int availableForWrite() override {
    return state().installed ? state().fifo.availableForWrite() : 0;
  }
  int read() override { return state().fifo.read(); }
  int peek() override { return state().fifo.peek(); }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* bytes, size_t len) override {
    assert(state().installed);
    return state().fifo.write(bytes, len);
  }
};
static HardwareSerial Serial1(1);
@DECLARATION@
static MultiSerialInterface interface_manager;
static void setupHardwareSerial() {
@SETUP@
}

struct Location {
  unsigned starts = 0, resets = 0, stops = 0;
  void begin() { ++starts; }
  void reset() { ++resets; }
  void stop() { ++stops; }
  bool getGPSPowerSaving() const { return false; }
  void stopTimeSync() {}
  void setNextGPSOff(unsigned) {}
  void setNextWake() {}
};
struct EnvironmentSensorManager {
  bool gps_serial_transport_blocked = false, gps_serial_transport = false;
  bool gps_serial_open = false;
  bool gps_detected = false, gps_active = false, powersaving_enabled = false;
  bool telemetry_user_enabled = false;
  Location location;
  Location* _location = &location;
  void resetGpsTelemetryTransportState() { telemetry_user_enabled = false; }
  void setGpsTelemetryUserEnabled(bool value) { telemetry_user_enabled = value; }
  void armGpsPowerSavingCycle() {}
  void initBasicGPS();
  void startGpsSerialTransport();
  void stopGpsSerialTransport();
  void start_gps();
  void stop_gps();
};
#if ENV_INCLUDE_GPS == 1
@GPS_FUNCTIONS@
#endif

static void assertCompanionConfiguration() {
#if defined(SERIAL_RX)
  assert(companion_serial.port == EXPECTED_COMPANION_UART);
  assert(companion_serial.state().installed);
  assert(companion_serial.state().rx == SERIAL_RX);
  assert(companion_serial.state().tx == SERIAL_TX);
  assert(companion_serial.state().baud == 115200);
#endif
}

int main() {
  setupHardwareSerial();
  interface_manager.enable();
  assertCompanionConfiguration();
#if ENV_INCLUDE_GPS == 1
  EnvironmentSensorManager sensors;
  sensors.initBasicGPS();
  assert(sensors.gps_active && sensors.gps_detected);
  assert(sensors.gps_serial_transport && sensors.telemetry_user_enabled);
  assert(Serial1.state().installed && Serial1.state().baud == 9600);
  assert(Serial1.state().rx == PIN_GPS_TX);
  assert(Serial1.state().tx == PIN_GPS_RX);
  assert(sensors.location.starts == 1 && sensors.location.resets == 1);
#if defined(SERIAL_RX) && defined(CONFIG_IDF_TARGET_ESP32S3)
  assertCompanionConfiguration();
  assert(companion_serial.port != Serial1.port);

  // Route a command and response over Companion without consuming GPS bytes.
  const uint8_t command[] = {'<', 1, 0, 0x01};
  companion_serial.state().fifo.push(command, sizeof(command));
  Serial1.state().fifo.push("$GPGGA,untouched\r\n");
  const int gps_bytes = Serial1.available();
  uint8_t received[MAX_FRAME_SIZE] = {};
  assert(interface_manager.checkRecvFrame(received) == 1);
  assert(interface_manager.captureReplyRoute() == &hardware_serial_interface);
  assert(Serial1.available() == gps_bytes);
  companion_serial.state().fifo.write_capacity = 0;
  const uint8_t response[] = {0x00};
  assert(interface_manager.writeFrameToRoute(&hardware_serial_interface,
      response, sizeof(response)) == sizeof(response));
  assert(hardware_serial_interface.hasPendingIO());

  // Actual GPS stop/start and rediscovery must leave the queued UART response,
  // original pins and 115200 baud intact, and must not reset the GPS choice.
  sensors.stop_gps();
  assert(!sensors.gps_active && sensors.location.stops == 1);
  sensors.start_gps();
  assert(sensors.gps_active);
  sensors.initBasicGPS();
  assertCompanionConfiguration();
  assert(Serial1.state().baud == 9600);
  assert(Serial1.available() == 0); // Closing GPS discards only its own FIFO.
  companion_serial.state().fifo.write_capacity = 128;
  interface_manager.loop();
  const std::vector<uint8_t> expected = {'>', 1, 0, 0x00};
  assert(companion_serial.state().fifo.output == expected);
  assert(Serial1.state().fifo.output.empty());
  assert(!hardware_serial_interface.hasPendingIO());

  // Reinitializing Companion must not change the GPS's existing UART1 state.
  setupHardwareSerial();
  assertCompanionConfiguration();
  assert(Serial1.state().installed && Serial1.state().baud == 9600);
  assert(Serial1.state().rx == PIN_GPS_TX && Serial1.state().tx == PIN_GPS_RX);
#endif
#else
  assertCompanionConfiguration();
#endif
  std::puts("PASS: actual Companion/GPS UART ownership");
}
'''


GPIO_UART = r'''
static std::array<int, 64> tx_owner = [] {
  std::array<int, 64> owners;
  owners.fill(-1);
  return owners;
}();
static std::array<std::vector<uint8_t>, 64> physical_output;
class HardwareSerial : public Stream {
public:
  const unsigned port;
  explicit HardwareSerial(unsigned selected) : port(selected) {
    assert(port < uarts.size());
    if (port == 0) setPins(SOC_RX0, SOC_TX0);
  }
  Uart& state() const { return uarts[port]; }
  void setPins(int receive, int transmit) {
    state().rx = receive;
    state().tx = transmit;
    tx_owner.at(transmit) = int(port);
  }
  void begin(unsigned speed) { state().baud = speed; state().installed = true; }
  void end() {
    assert(state().installed);
    state().installed = false;
    state().fifo.input.clear();
    if (tx_owner.at(state().tx) == int(port)) tx_owner.at(state().tx) = -1;
  }
  int available() override { return state().fifo.available(); }
  int availableForWrite() override {
    return state().installed ? state().fifo.availableForWrite() : 0;
  }
  int read() override { return state().fifo.read(); }
  int peek() override { return state().fifo.peek(); }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* bytes, size_t len) override {
    assert(state().installed);
    const size_t accepted = state().fifo.write(bytes, len);
    // A GPIO has only one selected output signal, even if two UARTs have
    // independently installed drivers and accept data into their TX FIFOs.
    if (tx_owner.at(state().tx) == int(port)) {
      auto& wire = physical_output.at(state().tx);
      wire.insert(wire.end(), bytes, bytes + accepted);
    }
    return accepted;
  }
};
static void physicalInput(int gpio, const uint8_t* bytes, size_t len) {
  // One input pad can feed multiple UART RX signals. Do not model independent
  // UART FIFOs as independent physical ports when their RX pins are identical.
  for (auto& uart : uarts) {
    if (uart.installed && uart.rx == gpio) uart.fifo.push(bytes, len);
  }
}
static const uint8_t SYMBOL_RX = 44, SYMBOL_TX = 43;
// The XIAO ESP32-S3 core names GPIO43/44 as D6/D7, not GPIO6/7.
static const uint8_t D6 = 43, D7 = 44;
'''


GPIO_USB = r'''
#if ARDUINO_USB_CDC_ON_BOOT
struct NativeUsb : BufferStream {
  unsigned baud = 0;
  void begin(unsigned speed) { baud = speed; }
  explicit operator bool() const { return true; }
} Serial;
#else
static HardwareSerial Serial(0);
#endif
#if defined(ENABLE_USB_INTERFACE)
ArduinoSerialInterface usb_serial_interface;
static const char USB_TERMINAL_START_TOKEN[] = "+++MESHCORE-TERM-START";
static bool usb_protocol_initialized = false;
static unsigned default_sessions = 0;
static mesh::UsbHostPresenceDebouncer usb_hwcdc_host_presence;
static constexpr unsigned USB_FRAME_REPLY_GRACE_MS = 2000;
static constexpr unsigned USB_CLIENT_IDLE_TIMEOUT = 600000;
static constexpr unsigned USB_HOST_LOSS_EDGE_MS = 100;
static constexpr unsigned USB_HOST_LOSS_GRACE_MS = 2000;
struct Board { bool isUsbHostConnected() const { return true; } } board;
struct Mesh {
  bool hasFiniteDelayedReplyForRoute(BaseSerialInterface*) const { return false; }
} the_mesh;
namespace mesh {
Stream& usbCompanionPort() { return Serial; }
}
static bool acceptUsbBinaryStartupFrame(uint32_t) { return true; }
static void beginUsbDefaultSession() { ++default_sessions; }
#endif
'''


GPIO_MAIN = r'''
static void setupPrimaryUsb() {
@USB_SETUP@
}

int main() {
  // setup() always begins the primary Serial before the interface blocks.
  Serial.begin(115200);
  setupPrimaryUsb();
  setupHardwareSerial();
  interface_manager.enable();
  assert(companion_serial_shares_usb_port == bool(EXPECTED_ALIAS));
  assert(interface_manager.isInterfaceConnected(InterfaceType::HardwareSerial)
      == !bool(EXPECTED_ALIAS));
#if defined(ENABLE_USB_INTERFACE)
  assert(usb_protocol_initialized && default_sessions == 1);
#if ARDUINO_USB_CDC_ON_BOOT && ARDUINO_USB_MODE == 1
  // HWCDC has no DTR: a physical host alone is not yet a Binary client.
  assert(!interface_manager.isInterfaceConnected(InterfaceType::USB));
#else
  assert(interface_manager.isInterfaceConnected(InterfaceType::USB));
#endif
  usb_serial_interface.setPassthroughMode(false);  // Deliberate Binary owner.
#endif
#if ENV_INCLUDE_GPS == 1
  EnvironmentSensorManager sensors;
  sensors.initBasicGPS();
  assert(sensors.gps_active && Serial1.state().baud == 9600);
#endif
  if (!EXPECTED_ALIAS) assertCompanionConfiguration();
  else assert(!companion_serial.state().installed);

  const uint8_t command[] = {'<', 1, 0, 0x01};
  const uint8_t response[] = {0x00};
  uint8_t received[MAX_FRAME_SIZE] = {};
  const std::vector<uint8_t> expected = {'>', 1, 0, 0x00};
#if defined(ENABLE_USB_INTERFACE)
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.push(command, sizeof(command));
#else
  physicalInput(SOC_RX0, command, sizeof(command));
#endif
  assert(interface_manager.checkRecvFrame(received) == 1);
  assert(interface_manager.captureReplyRoute() == &usb_serial_interface);
  assert(interface_manager.isInterfaceConnected(InterfaceType::USB));
  // An alias must not leave a second parser holding the same command.
  assert(interface_manager.checkRecvFrame(received) == 0);
  assert(interface_manager.writeFrameToRoute(&usb_serial_interface,
      response, sizeof(response)) == sizeof(response));
#if ARDUINO_USB_CDC_ON_BOOT
  assert(Serial.output == expected);
#else
  assert(physical_output.at(SOC_TX0) == expected);
  assert(tx_owner.at(SOC_TX0) == 0);
  assert(Serial.state().rx == SOC_RX0 && Serial.state().tx == SOC_TX0);
  assert(Serial.state().baud == 115200);
#endif
#endif

  if (!EXPECTED_ALIAS) {
    physicalInput(SERIAL_RX, command, sizeof(command));
    assert(interface_manager.checkRecvFrame(received) == 1);
    assert(interface_manager.captureReplyRoute() == &hardware_serial_interface);
    assert(interface_manager.checkRecvFrame(received) == 0);
    companion_serial.state().fifo.write_capacity = 0;
    assert(interface_manager.writeFrameToRoute(&hardware_serial_interface,
        response, sizeof(response)) == sizeof(response));
    assert(hardware_serial_interface.hasPendingIO());
    companion_serial.state().fifo.write_capacity = 128;
    interface_manager.loop();
    assert(physical_output.at(SERIAL_TX) == expected);
    assert(tx_owner.at(SERIAL_TX) == int(companion_serial.port));
  }
#if ENV_INCLUDE_GPS == 1
  sensors.stop_gps();
  sensors.start_gps();
  sensors.initBasicGPS();
  assert(Serial1.state().baud == 9600);
  assert(Serial1.state().rx == PIN_GPS_TX && Serial1.state().tx == PIN_GPS_RX);
  if (!EXPECTED_ALIAS) assertCompanionConfiguration();
#if defined(ENABLE_USB_INTERFACE) && !ARDUINO_USB_CDC_ON_BOOT
  assert(Serial.state().baud == 115200);
  assert(Serial.state().rx == SOC_RX0 && Serial.state().tx == SOC_TX0);
#endif
#endif
  std::puts("PASS: actual GPIO-matrix Companion UART alias ownership");
}
'''


class CompanionHardwareSerialGpsTest(unittest.TestCase):
    def test_gpio_alias_has_exactly_one_command_and_reply_owner(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        gps = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        native = (ROOT / "test/test_serial_mode_switch/test_serial_mode_switch.cpp").read_text()
        source = HARNESS[:HARNESS.index("int main()")]
        uart = extract_braced(source, "class HardwareSerial") + ";"
        source = source.replace(uart, GPIO_UART)
        source = source.replace("@DECLARATION@", GPIO_USB + "\n@DECLARATION@")
        source += GPIO_MAIN
        source = source.replace("@STREAM@", extract_braced(native, "class BufferStream") + ";")
        source = source.replace("@DECLARATION@", conditional_block(main, "// include hardware serial interface"))
        source = source.replace("@SETUP@", conditional_block(main, "// add hardware serial interface"))
        usb_marker = "#if defined(ENABLE_USB_INTERFACE)\n#if COMPANION_FEATURE_USB_MOTA_SOURCE\n  usb_serial_interface.begin"
        source = source.replace("@USB_SETUP@", conditional_block(main, usb_marker))
        source = source.replace("@GPS_FUNCTIONS@", "\n".join(
            extract_braced(gps, signature) for signature in (
                "void EnvironmentSensorManager::initBasicGPS()",
                "void EnvironmentSensorManager::startGpsSerialTransport()",
                "void EnvironmentSensorManager::stopGpsSerialTransport()",
                "void EnvironmentSensorManager::start_gps()",
                "void EnvironmentSensorManager::stop_gps()")))
        scenarios = (
            ("m2-full", ["ENABLE_USB_INTERFACE=1", "ARDUINO_USB_CDC_ON_BOOT=0",
                "ENV_INCLUDE_GPS=0", "SERIAL_RX=44", "SERIAL_TX=43",
                "EXPECTED_ALIAS=1", "EXPECTED_COMPANION_UART=1"]),
            ("m5-full", ["ENABLE_USB_INTERFACE=1", "ARDUINO_USB_CDC_ON_BOOT=0",
                "ENV_INCLUDE_GPS=1", "SERIAL_RX=44", "SERIAL_TX=43",
                "EXPECTED_ALIAS=1", "EXPECTED_COMPANION_UART=2"]),
            ("symbolic-pins-full", ["ENABLE_USB_INTERFACE=1", "ARDUINO_USB_CDC_ON_BOOT=0",
                "ENV_INCLUDE_GPS=1", "SERIAL_RX=SYMBOL_RX", "SERIAL_TX=SYMBOL_TX",
                "EXPECTED_ALIAS=1", "EXPECTED_COMPANION_UART=2"]),
            ("native-usb-full", ["ENABLE_USB_INTERFACE=1", "ARDUINO_USB_CDC_ON_BOOT=1",
                "ENV_INCLUDE_GPS=1", "SERIAL_RX=44", "SERIAL_TX=43",
                "EXPECTED_ALIAS=0", "EXPECTED_COMPANION_UART=2"]),
            ("xiao-native-usb-full", ["ENABLE_USB_INTERFACE=1", "ARDUINO_USB_CDC_ON_BOOT=1",
                "ARDUINO_USB_MODE=1", "ENV_INCLUDE_GPS=0", "SERIAL_RX=D7", "SERIAL_TX=D6",
                "EXPECTED_ALIAS=0", "EXPECTED_COMPANION_UART=1"]),
            ("distinct-pins-full", ["ENABLE_USB_INTERFACE=1", "ARDUINO_USB_CDC_ON_BOOT=0",
                "ENV_INCLUDE_GPS=1", "SERIAL_RX=10", "SERIAL_TX=11",
                "EXPECTED_ALIAS=0", "EXPECTED_COMPANION_UART=2"]),
            ("m5-serial", ["ARDUINO_USB_CDC_ON_BOOT=0", "ENV_INCLUDE_GPS=1",
                "SERIAL_RX=44", "SERIAL_TX=43", "EXPECTED_ALIAS=0",
                "EXPECTED_COMPANION_UART=2"]),
        )
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-uart-alias-") as directory:
            work = Path(directory)
            cpp = work / "test.cpp"
            cpp.write_text(source, encoding="ascii")
            for name, defines in scenarios:
                with self.subTest(profile=name):
                    binary = work / f"{name}.exe"
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Werror", *SANITIZERS,
                        "-DSOC_RX0=44", "-DSOC_TX0=43", "-DCONFIG_IDF_TARGET_ESP32S3=1",
                        "-DCOMPANION_FEATURE_USB_MOTA_SOURCE=0",
                        "-DESP32=1", "-DPIN_GPS_TX=19", "-DPIN_GPS_RX=20", "-DPIN_GPS_EN=11",
                        "-DPERSISTANT_GPS=1", "-DENV_SKIP_GPS_DETECT=1",
                        *[f"-D{define}" for define in defines],
                        f"-I{ROOT / 'test/mocks'}", f"-I{ROOT / 'src'}", str(cpp),
                        str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True,
                                             text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: actual GPIO-matrix Companion UART alias ownership", checked.stdout)

    def test_actual_gps_startup_and_restart_preserve_companion(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        main = (ROOT / "examples/companion_radio/main.cpp").read_text()
        gps = (ROOT / "src/helpers/sensors/EnvironmentSensorManager.cpp").read_text()
        native = (ROOT / "test/test_serial_mode_switch/test_serial_mode_switch.cpp").read_text()
        variant = (ROOT / "variants/thinknode_m5/variant.h").read_text()
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "variants/thinknode_m5/platformio.ini")
        self.assertEqual(config["ThinkNode_M5"]["extends"], "esp32_base")
        board = json.loads((ROOT / "boards" / (config["ThinkNode_M5"]["board"] + ".json")).read_text())
        self.assertEqual(board["build"]["mcu"], "esp32s3")
        base_flags = config["ThinkNode_M5"]["build_flags"]
        for flag in ("ENV_INCLUDE_GPS=1", "PERSISTANT_GPS=1", "ENV_SKIP_GPS_DETECT=1"):
            self.assertRegex(base_flags, r"-D\s+" + re.escape(flag) + r"\b")
        target = (ROOT / "variants/thinknode_m5/target.cpp").read_text()
        self.assertRegex(target, r"MicroNMEALocationProvider\(Serial1,\s*&rtc_clock\)")
        definitions = []
        for name in ("PIN_GPS_RX", "PIN_GPS_TX", "PIN_GPS_EN"):
            value = re.search(r"^#define\s+" + name + r"\s+\((\d+)\)", variant, re.MULTILINE)
            self.assertIsNotNone(value, name)
            definitions.append(f"{name}={value.group(1)}")
        source = HARNESS.replace("@STREAM@", extract_braced(native, "class BufferStream") + ";")
        source = source.replace("@DECLARATION@", conditional_block(main, "// include hardware serial interface"))
        source = source.replace("@SETUP@", conditional_block(main, "// add hardware serial interface"))
        source = source.replace("@GPS_FUNCTIONS@", "\n".join(
            extract_braced(gps, signature) for signature in (
                "void EnvironmentSensorManager::initBasicGPS()",
                "void EnvironmentSensorManager::startGpsSerialTransport()",
                "void EnvironmentSensorManager::stopGpsSerialTransport()",
                "void EnvironmentSensorManager::start_gps()",
                "void EnvironmentSensorManager::stop_gps()")))
        scenarios = []
        for profile in ("serial", "full"):
            flags = config[f"env:ThinkNode_M5_companion_radio_{profile}"]["build_flags"]
            rx = re.findall(r"-D\s+SERIAL_RX=(\d+)\b", flags)
            tx = re.findall(r"-D\s+SERIAL_TX=(\d+)\b", flags)
            self.assertEqual(rx, ["44"])
            self.assertEqual(tx, ["43"])
            scenarios.append((f"m5-{profile}", ["CONFIG_IDF_TARGET_ESP32S3=1", "ENV_INCLUDE_GPS=1",
                "EXPECTED_COMPANION_UART=2", f"SERIAL_RX={rx[0]}", f"SERIAL_TX={tx[0]}"]))
        scenarios.extend((
            ("s3-no-gps", ["CONFIG_IDF_TARGET_ESP32S3=1", "ENV_INCLUDE_GPS=0",
                "EXPECTED_COMPANION_UART=1", "SERIAL_RX=44", "SERIAL_TX=43"]),
            ("other-platform", ["ENV_INCLUDE_GPS=1", "EXPECTED_COMPANION_UART=1",
                "SERIAL_RX=44", "SERIAL_TX=43"]),
            ("gps-without-companion-uart", ["CONFIG_IDF_TARGET_ESP32S3=1", "ENV_INCLUDE_GPS=1",
                "EXPECTED_COMPANION_UART=0"]),
        ))
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-gps-uart-") as directory:
            work = Path(directory)
            cpp = work / "test.cpp"
            cpp.write_text(source, encoding="ascii")
            for name, defines in scenarios:
                with self.subTest(profile=name):
                    binary = work / f"{name}.exe"
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Werror", *SANITIZERS,
                        "-DPERSISTANT_GPS=1", "-DENV_SKIP_GPS_DETECT=1",
                        *[f"-D{define}" for define in definitions + defines],
                        f"-I{ROOT / 'test/mocks'}", f"-I{ROOT / 'src'}", str(cpp),
                        str(ROOT / "src/helpers/ArduinoSerialInterface.cpp"), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True,
                                             text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("PASS: actual Companion/GPS UART ownership", checked.stdout)


if __name__ == "__main__":
    unittest.main()
