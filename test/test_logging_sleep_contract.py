#!/usr/bin/env python3
"""Check USB workaround instructions and execute the real MQTT sleep gates."""

from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(path, signature):
    text = (ROOT / path).read_text(encoding="utf-8")
    start = text.index(signature)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


HARNESS = r'''
#include <cstdint>
#include <stdexcept>
#include <iostream>
#define WITH_BRIDGE 1
struct AbstractBridge { virtual bool isRunning() const = 0; };
struct MQTTBridge : AbstractBridge {
  bool _initialized = false;
  @RUNNING@
};
struct Radio {
  bool isWatchdogObserving() const { return false; }
  bool isCalibratingNoiseFloor() const { return false; }
};
struct MyMesh {
  MQTTBridge* bridge = nullptr;
  const AbstractBridge* activeBridge() const { return bridge; }
  struct { bool pending = false; } deferred_cli_command;
  struct { bool hasActiveUserGpioTimer() const { return false; } } _cli;
  struct { int getNumClients() const { return 0; } } acl;
  struct { bool battery_alert_enabled = false; } _prefs;
  Radio radio_driver;
  bool pending_self_advert = false;
  bool saved_radio_apply_pending = false, temp_radio_applied = false;
  unsigned long next_flood_advert = 0, next_local_advert = 0;
  unsigned long dirty_contacts_expiry = 0, next_recent_repeater_sweep = 0;
  unsigned long next_battery_alert_check = 0, next_push = 0;
  unsigned long radio_apply_retry_at = 0, set_radio_at = 0, revert_radio_at = 0;
  bool hasPendingOtaApply() const { return false; }
  bool hasQueuedWorkDue() const { return false; }
  bool hasRetryWorkDue() const { return false; }
  bool hasScheduledRadioWorkDue() const { return false; }
  bool isMillisTimerDue(unsigned long) const { return false; }
  bool millisHasNowPassed(unsigned long) const { return false; }
  bool getNextQueueWakeDelay(uint32_t&) const { return false; }
  bool getNextRetryWakeDelay(uint32_t&) const { return false; }
  uint32_t limitSleepToMillisTimer(unsigned long, uint32_t secs) const { return secs; }
  uint32_t limitSleepToScheduledRadioWork(uint32_t secs) const { return secs; }
  bool hasPendingWork() const;
  uint32_t getPowerSaveSleepSeconds(uint32_t) const;
};
@METHODS@
static void require(bool ok, const char* why) {
  if (!ok) throw std::runtime_error(why);
}
int main() {
  try {
    MyMesh node;
    require(node.getPowerSaveSleepSeconds(30) == 30, "quiet node cannot sleep");
    MQTTBridge logger;
    node.bridge = &logger;
    require(node.getPowerSaveSleepSeconds(30) == 30, "stopped MQTT blocked sleep");
    logger._initialized = true;
    require(node.hasPendingWork(), "running MQTT lost its sleep blocker");
    require(node.getPowerSaveSleepSeconds(30) == 0,
            "WiFi-only logging can enter device sleep without a USB guard");
    logger._initialized = false;
    require(node.getPowerSaveSleepSeconds(30) == 30,
            "stopping MQTT did not release its sleep blocker");
    require(node.getPowerSaveSleepSeconds(0) == 0, "zero sleep limit changed");
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
'''


class LoggingSleepContractTest(unittest.TestCase):
    def test_usb_enable_examples_include_workaround_first(self):
        for path in (
            "docs/cli_commands.md", "docs/role_feature_switches.md",
            "docs/full_companion_features.md", "docs/companion_radio_full.md",
            "docs/firmware_picker.md", "docs/releases/1.17.1.5.md",
        ):
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                blocks = re.findall(r"```text\n(.*?)```", text, re.S)
                self.assertTrue(any("powersaving off\nset usb.logging on\n" in block
                                    for block in blocks), path)
                self.assertNotRegex(text, r"powersaving off\nset usb.logging on reboot")

    def test_wifi_exception_is_documented_with_running_state_check(self):
        for path in ("docs/cli_commands.md", "docs/role_feature_switches.md",
                     "docs/releases/1.17.1.5.md"):
            with self.subTest(path=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                paragraphs = [
                    " ".join(block.split())
                    for block in re.split(r"\n\s*\n", text)
                    if "WiFi/MQTT-only logging" in block
                ]
                self.assertTrue(paragraphs, f"{path}: missing WiFi-only sleep guidance")
                for paragraph in paragraphs:
                    self.assertTrue(
                        re.search(r"WiFi/MQTT-only logging.*?does not need", paragraph),
                        f"{path}: missing running-MQTT sleep exception",
                    )
                    # Check the actual guidance, not an unrelated bridge-command
                    # reference elsewhere in the document. MQTT has its own
                    # shared running-state command across firmware roles.
                    self.assertTrue(
                        "`get mqtt.running`" in paragraph,
                        f"{path}: WiFi-only sleep guidance must check `get mqtt.running`",
                    )

    def test_running_mqtt_prevents_sleep_in_repeater_and_room(self):
        running = method("src/helpers/bridges/MQTTBridge.h",
                         "bool isRunning() const override")
        with tempfile.TemporaryDirectory(prefix="meshcore-mqtt-sleep-") as temp:
            for role in ("simple_repeater", "simple_room_server"):
                with self.subTest(role=role):
                    source = f"examples/{role}/MyMesh.cpp"
                    methods = "\n".join(method(source, signature) for signature in (
                        "bool MyMesh::hasPendingWork() const",
                        "uint32_t MyMesh::getPowerSaveSleepSeconds(uint32_t max_secs) const",
                    ))
                    cpp = Path(temp) / f"{role}.cpp"
                    binary = Path(temp) / role
                    cpp.write_text(HARNESS.replace("@RUNNING@", running)
                                   .replace("@METHODS@", methods))
                    built = subprocess.run([
                        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra",
                        str(cpp), "-o", str(binary),
                    ], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
