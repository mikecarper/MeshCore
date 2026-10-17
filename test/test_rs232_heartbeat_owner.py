#!/usr/bin/env python3
"""Execute RS-232 UART/GPS ownership and timed/interrupt standby sleep gates."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <new>
#include <limits>
#include <initializer_list>
#include <helpers/PowerManagementUtils.h>
#include <helpers/PowerSavingDiagnostics.h>
#define WITH_BRIDGE 1
#define WITH_RS232_BRIDGE Serial2
#define WITH_RS232_BRIDGE_UART 2
#define WITH_RS232_BRIDGE_RX 16
#define WITH_RS232_BRIDGE_TX 17
#define WITH_RS232_BRIDGE_ALT Serial1
#define WITH_RS232_BRIDGE_ALT_UART 1
#define WITH_RS232_BRIDGE_ALT_RX 8
#define WITH_RS232_BRIDGE_ALT_TX 9
#define MESH_DEBUG_PRINTLN(...) ((void)0)
static uint32_t fake_millis = 1000;
static uint32_t millis() { return fake_millis; }
struct StrHelper {
  static void strncpy(char* out, const char* in, size_t size) {
    assert(size); std::strncpy(out, in, size - 1); out[size - 1] = 0;
  }
};
struct NodePrefs {
  uint8_t bridge_enabled = 0, rs232_bridge_enabled = 0, bridge_uart = 2;
  uint8_t powersaving_enabled = 1, rs232_autosleep = 1, rs232_heartbeat = 1;
  uint16_t rs232_idle_seconds = 300;
  bool battery_alert_enabled = false;
};
struct FakeUart { unsigned begins = 0, ends = 0; bool running = false; } Serial1, Serial2;
struct FakeSensors {
  unsigned claims = 0, releases = 0;
  bool owned = false, fail_claim = false, fail_release = false;
  bool gpsUsesSerialUart(uint8_t uart) const { return uart == 1; }
  bool gpsSerialTransportMayConflict(uint8_t uart) const { return uart == 1; }
  bool gpsSerialTransportCanYield(uint8_t) const { return true; }
  bool setGpsSerialTransportBlocked(uint8_t uart, bool blocked) {
    assert(uart == 1);
    if (blocked) { ++claims; if (fail_claim) return false; }
    else { ++releases; if (fail_release) return false; }
    owned = blocked; return true;
  }
};
struct FakeRng {
  uint64_t next = 1;
  void random(uint8_t* out, size_t size) {
    assert(size == sizeof(next)); std::memcpy(out, &next, size); ++next;
  }
};
class AbstractBridge {
public:
  virtual ~AbstractBridge() = default;
  virtual bool isRunning() const = 0;
};
class RS232Bridge : public AbstractBridge {
  FakeUart& uart;
public:
  enum class PowerAction { None, Suspend, Resume };
  inline static unsigned live = 0, fail_starts = 0;
  uint64_t session = 0;
  unsigned loops = 0, completions = 0;
  PowerAction requested = PowerAction::None;
  bool completion_success = false, suspended = false, last_allow = false;
  bool heartbeat_enabled = false, capability_armed = false, last_force = false;
  uint32_t wake_delay = UINT32_MAX, last_idle_ms = 0, last_now = 0;
  RS232Bridge(NodePrefs*, FakeUart& selected, int, int, void*, void*) : uart(selected) { ++live; }
  ~RS232Bridge() override { --live; }
  void setHeartbeatSession(uint64_t value) { assert(value); session = value; }
  void setHeartbeatEnabled(bool value) { heartbeat_enabled = value; }
  void begin() {
    assert(session); ++uart.begins;
    if (fail_starts) { --fail_starts; return; }
    uart.running = true;
  }
  void end() { assert(uart.running); ++uart.ends; uart.running = false; }
  bool isRunning() const override { return uart.running; }
  void loop() { assert(isRunning()); ++loops; }
  PowerAction servicePowerPolicy(bool allow, uint32_t idle, uint32_t now, bool force = false) {
    last_allow = allow && (capability_armed || force);
    last_force = force; last_idle_ms = idle; last_now = now;
    return requested == PowerAction::Suspend && !last_allow ? PowerAction::None : requested;
  }
  void completePowerAction(PowerAction action, bool success, uint32_t now) {
    assert(action == requested && action != PowerAction::None);
    ++completions; completion_success = success; last_now = now;
    if (success) {
      requested = PowerAction::None; suspended = action == PowerAction::Suspend;
      wake_delay = suspended ? 30000 : UINT32_MAX;
    } else wake_delay = 5000;
  }
  uint32_t powerWakeDelayMs(uint32_t) const { return wake_delay; }
  bool isPowerSuspended() const { return suspended; }
  void formatHeartbeatStatus(char* out, size_t capacity, uint32_t) const {
    std::snprintf(out, capacity, "phase=%s peer=unknown rx=never probe=30s",
                  suspended ? "standby" : "active");
  }
};
struct MQTTBridge : AbstractBridge {
  bool running = false, paused = false;
  bool isRunning() const override { return running; }
  bool isNetworkSuspended() const { return paused; }
  bool isStopping() const { return false; }
};
struct Callbacks {
  virtual ~Callbacks() = default;
  virtual bool isRs232BridgeRunning() const = 0;
  virtual bool setRs232BridgeState(bool) = 0;
  virtual bool restartRs232Bridge() = 0;
  virtual void formatRs232Status(char*) = 0;
};
struct MyMesh : Callbacks {
  NodePrefs _prefs;
  RS232Bridge* bridge = nullptr;
  MQTTBridge* mqtt_bridge = nullptr;
  uint8_t active_rs232_bridge_uart = 0;
  FakeSensors sensors;
  FakeRng rng;
  FakeRng* getRNG() { return &rng; }
  void* _mgr = nullptr;
  void* getRTCClock() { return nullptr; }
  void configureBridgeFilter(AbstractBridge*) {}
  const AbstractBridge* activeBridge() const {
#ifdef WITH_MQTT_BRIDGE
    return mqtt_bridge;
#else
    return bridge;
#endif
  }
  struct { bool pending = false; } deferred_cli_command;
  struct { bool hasActiveUserGpioTimer() const { return false; } } _cli;
  struct {
    bool isWatchdogObserving() const { return false; }
    bool isCalibratingNoiseFloor() const { return false; }
  } radio_driver;
  bool pending_self_advert = false;
  unsigned long next_flood_advert = 0, next_local_advert = 0;
  unsigned long dirty_contacts_expiry = 0, next_recent_repeater_sweep = 0;
  unsigned long next_battery_alert_check = 0;
  bool isDualRadioActive() const { return false; }
  bool hasPendingOtaApply() const { return false; }
  bool hasQueuedWorkDue() const { return false; }
  bool hasRetryWorkDue() const { return false; }
  bool hasScheduledRadioWorkDue() const { return false; }
  bool isMillisTimerDue(unsigned long) const { return false; }
  bool getNextQueueWakeDelay(uint32_t&) const { return false; }
  bool getNextRetryWakeDelay(uint32_t&) const { return false; }
  bool getNextNoiseFloorWakeDelay(uint32_t&) const { return false; }
  uint32_t limitSleepToMillisTimer(unsigned long, uint32_t secs) const { return secs; }
  uint32_t limitSleepToScheduledRadioWork(uint32_t secs) const { return secs; }
  bool hasPendingWork() const;
  mesh::powerdiag::Mask pendingWorkReason() const;
  uint32_t getPowerSaveSleepMilliseconds(uint32_t) const;
  uint32_t getPowerSaveSleepSeconds(uint32_t) const;
  @OWNER@
  ~MyMesh() override { assert(setRs232BridgeState(false)); }
};
@SLEEP@
static void enable(MyMesh& node) {
  node._prefs.bridge_enabled = node._prefs.rs232_bridge_enabled = 1;
  assert(node.setRs232BridgeState(true));
}
static void request(MyMesh& node, RS232Bridge::PowerAction action) {
  node.bridge->requested = action; node.bridge->wake_delay = 0;
  node.serviceRs232BridgePower();
}
int main() {
  {
    MyMesh node;
    assert(node.getPowerSaveSleepSeconds(30) == 30);
    char status[161]; std::memset(status, '!', sizeof(status));
    node.formatRs232Status(status); assert(status[160] == '!');
    assert(std::strstr(status, "saved=off running=off"));
    enable(node); const auto session = node.bridge->session;
    assert(node.getPowerSaveSleepSeconds(30) == 0);
    node.serviceRs232BridgePower();
    assert(node.bridge->loops == 1 && !node.bridge->last_allow);
    assert(node.bridge->heartbeat_enabled);
    assert(node.bridge->last_idle_ms == 300000);
    // Negotiated default never suspends an unconfirmed stock peer.
    request(node, RS232Bridge::PowerAction::Suspend);
    assert(node.isRs232BridgeRunning() && node.bridge->completions == 0);
    node.bridge->requested = RS232Bridge::PowerAction::None;
    node.bridge->wake_delay = UINT32_MAX;
    node._prefs.rs232_autosleep = 1; node._prefs.powersaving_enabled = 0;
    node.serviceRs232BridgePower(); assert(!node.bridge->last_allow);
    node._prefs.powersaving_enabled = 1;
    node._prefs.rs232_autosleep = 255; node.serviceRs232BridgePower();
    assert(!node.bridge->last_allow);
    node._prefs.rs232_autosleep = 1;
    node._prefs.rs232_heartbeat = 255; node.serviceRs232BridgePower();
    assert(!node.bridge->heartbeat_enabled && !node.bridge->last_allow);
    node._prefs.rs232_heartbeat = 0; node.serviceRs232BridgePower();
    assert(!node.bridge->heartbeat_enabled && !node.bridge->last_allow);
    node._prefs.rs232_heartbeat = 1;
    node.bridge->capability_armed = true;
    request(node, RS232Bridge::PowerAction::Suspend);
    assert(node.bridge->last_allow && node.bridge->isPowerSuspended());
    assert(!node.isRs232BridgeRunning() && node.active_rs232_bridge_uart == 0);
    assert(node._prefs.bridge_enabled && node._prefs.rs232_bridge_enabled);
    assert(node.bridge->session == session && RS232Bridge::live == 1);
    const unsigned loops = node.bridge->loops;
    node.serviceRs232BridgePower(); assert(node.bridge->loops == loops);
    assert(node.getPowerSaveSleepSeconds(30) == 30);
    for (uint32_t remainder : {1U, 50U, 999U, 1000U, 29999U, 30000U}) {
      node.bridge->wake_delay = remainder;
#ifdef NRF52_PLATFORM
      const uint32_t expected = (remainder + 999U) / 1000U;
#else
      const uint32_t expected = remainder / 1000U;
#endif
      assert(node.getPowerSaveSleepSeconds(30) == expected);
      assert(node.getPowerSaveSleepMilliseconds(30000) == remainder);
    }
    node.bridge->wake_delay = 0; assert(node.hasPendingWork());
#ifdef WITH_MQTT_BRIDGE
    MQTTBridge broker; node.mqtt_bridge = &broker;
    node.bridge->wake_delay = 30000; broker.running = true;
    assert(node.hasPendingWork()); broker.paused = true;
    assert(!node.hasPendingWork()); node.mqtt_bridge = nullptr;
#endif
    request(node, RS232Bridge::PowerAction::Resume);
    assert(node.isRs232BridgeRunning() && node.bridge->session == session);
    request(node, RS232Bridge::PowerAction::Suspend);
    assert(node.setRs232BridgeState(true));
    assert(node.isRs232BridgeRunning() && node.bridge->session != session);
    node.formatRs232Status(status); assert(status[160] == '!');
    assert(std::strstr(status, "saved=on running=on heartbeat=on autosleep=on idle=300s"));
    assert(node.setRs232BridgeState(false) && !node.bridge);
  }
  assert(RS232Bridge::live == 0);
  {
    MyMesh node; enable(node); node._prefs.rs232_autosleep = 2;
    assert(!node.bridge->capability_armed);
    request(node, RS232Bridge::PowerAction::Suspend);
    assert(node.bridge->last_force && !node.isRs232BridgeRunning());
    char status[160]; node.formatRs232Status(status);
    assert(std::strstr(status, "autosleep=force"));
    request(node, RS232Bridge::PowerAction::Resume);
    assert(node.isRs232BridgeRunning());
  }
  assert(RS232Bridge::live == 0);
#if ENV_INCLUDE_GPS == 1
  {
    MyMesh node; node._prefs.bridge_uart = 1; enable(node);
    const auto session = node.bridge->session;
    assert(node.sensors.owned && node.active_rs232_bridge_uart == 1);
    node._prefs.rs232_autosleep = 2;
    node.sensors.fail_release = true;
    const unsigned ends = Serial1.ends;
    request(node, RS232Bridge::PowerAction::Suspend);
    assert(!node.bridge->completion_success && !node.isRs232BridgeRunning());
    assert(node.active_rs232_bridge_uart == 1 && node.sensors.owned);
    assert(node.hasPendingWork() && node.getPowerSaveSleepSeconds(30) == 0);
    assert(Serial1.ends == ends + 1);
    node.sensors.fail_release = false; node.serviceRs232BridgePower();
    assert(node.bridge->completion_success && node.active_rs232_bridge_uart == 0);
    assert(!node.sensors.owned && Serial1.ends == ends + 1);
    // No end() on the already stopped/unstarted UART during resume cleanup.
    RS232Bridge::fail_starts = 1; node.sensors.fail_release = true;
    request(node, RS232Bridge::PowerAction::Resume);
    assert(!node.bridge->completion_success && node.active_rs232_bridge_uart == 1);
    const unsigned begins = Serial1.begins;
    node.serviceRs232BridgePower(); assert(Serial1.begins == begins);
    node.sensors.fail_release = false; node.serviceRs232BridgePower();
    assert(node.isRs232BridgeRunning() && node.bridge->session == session);
    assert(Serial1.ends == ends + 1);
    request(node, RS232Bridge::PowerAction::Suspend);
    node.sensors.fail_claim = true;
    request(node, RS232Bridge::PowerAction::Resume);
    assert(!node.bridge->completion_success && node.active_rs232_bridge_uart == 0);
    assert(!node.hasPendingWork() && node.getPowerSaveSleepSeconds(30) == 5);
    node.sensors.fail_claim = false; node.serviceRs232BridgePower();
    assert(node.isRs232BridgeRunning());
  }
#endif
  assert(RS232Bridge::live == 0);
}
'''


class RS232HeartbeatOwnerTests(unittest.TestCase):
    def test_owned_uart_standby_retry_and_sleep_deadlines(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text(encoding="ascii")
        implementation = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
        owner = "\n".join(extract_braced(header, signature) for signature in (
            "RS232Bridge* createRS232Bridge()", "bool beginRS232Bridge()",
            "bool endRS232Bridge()", "bool rs232BridgeEnabled() const",
            "bool isRs232BridgeRunning() const override", "void serviceRs232BridgePower()",
            "void formatRs232Status(char* reply) override", "bool setRs232BridgeState(bool enable)",
            "bool restartRs232Bridge()",
        ))
        sleep = "\n".join(extract_braced(implementation, signature) for signature in (
            "bool MyMesh::hasPendingWork() const",
            "mesh::powerdiag::Mask MyMesh::pendingWorkReason() const",
            "uint32_t MyMesh::getPowerSaveSleepMilliseconds(uint32_t max_ms) const",
            "uint32_t MyMesh::getPowerSaveSleepSeconds(uint32_t max_secs) const",
        ))
        source = HARNESS.replace("@OWNER@", owner).replace("@SLEEP@", sleep)
        for gps, nrf in ((0, False), (1, False), (0, True), (1, True)):
            for mqtt in (False, True):
                with self.subTest(gps=gps, mqtt=mqtt, nrf=nrf), tempfile.TemporaryDirectory(prefix="rs232-owner-") as directory:
                    work = Path(directory)
                    cpp, binary = work / "test.cpp", work / "test"
                    cpp.write_text(source, encoding="ascii")
                    flags = [f"-DENV_INCLUDE_GPS={gps}"]
                    if nrf:
                        flags.append("-DNRF52_PLATFORM=1")
                    if mqtt:
                        flags.append("-DWITH_MQTT_BRIDGE=1")
                    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                        *flags, "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_room_role_does_not_claim_rs232_support(self):
        for filename in ("MyMesh.h", "MyMesh.cpp"):
            source = (ROOT / "examples/simple_room_server" / filename).read_text(encoding="utf-8")
            self.assertNotIn("RS232Bridge", source)


if __name__ == "__main__":
    unittest.main()
