#!/usr/bin/env python3
"""Exercise the firmware's MQTT NVS loader and slot readiness on the host."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/helpers/CompanionMqttSetupPortal.cpp"


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


PREAMBLE = r"""
#include <cassert>
#include <initializer_list>
#include <cctype>
#include <cstring>
#include <helpers/CompanionMqttPrefsNvs.h>
constexpr int MAX_MQTT_SLOTS = MQTT_PREFS_SLOT_COUNT;
constexpr int RUNTIME_MQTT_SLOTS = TEST_RUNTIME_SLOTS;
constexpr int MQTT_TOPIC_MESHCORE = 0, MQTT_TOPIC_MESHRANK = 1;
const char* MQTT_PRESET_CUSTOM = "custom";
const char* MQTT_PRESET_NONE = "none";
const char* NVS_NAMESPACE = "mesh-mqtt";
const char* NVS_VERSION_KEY = "version";
const char* NVS_PREFS_KEY = "prefs";
struct MQTTPresetDef { int topic_style; bool username, password; };
const MQTTPresetDef* findMQTTPreset(const char* name) {
  static MQTTPresetDef analyzer{MQTT_TOPIC_MESHCORE, false, false};
  static MQTTPresetDef credentials{MQTT_TOPIC_MESHCORE, true, false};
  if (strcmp(name, "analyzer-us") == 0) return &analyzer;
  if (strcmp(name, "username-only") == 0) return &credentials;
  return nullptr;
}
bool mqttPresetNeedsSlotUsername(const MQTTPresetDef* p) { return p->username; }
bool mqttPresetNeedsSlotPassword(const MQTTPresetDef* p) { return p->password; }
void applyMQTTDefaults(MQTTPrefs* p) {
  memset(p, 0, sizeof(*p));
  for (auto& preset : p->mqtt_slot_preset) strcpy(preset, "none");
}
CompanionMqttPrefsNvs::Record persisted;
MQTTPrefs& saved = persisted.prefs;
size_t saved_length = CompanionMqttPrefsNvs::kWriteSize;
struct Preferences {
  bool begin(const char*, bool) { return true; }
  bool isKey(const char*) { return true; }
  uint16_t getUShort(const char*, uint16_t) { return MQTT_PREFS_VERSION; }
  size_t getBytesLength(const char*) { return saved_length; }
  size_t getBytes(const char*, void* dest, size_t length) {
    memcpy(dest, &persisted, length);
    return length;
  }
  size_t putBytes(const char*, const void* source, size_t length) {
    memset(&persisted, 0, sizeof(persisted));
    memcpy(&persisted, source, length);
    saved_length = length;
    return length;
  }
  size_t putUShort(const char*, uint16_t version) {
    assert(version == MQTT_PREFS_VERSION);
    return sizeof(version);
  }
  void end() {}
};
struct CompanionMqttSetupPortal {
  static bool loadStoredConfig(MQTTPrefs&);
  static bool saveStoredConfig(const MQTTPrefs&);
  static bool hasConfiguredSlot(const MQTTPrefs&);
};
"""

SCENARIOS = r"""
int main() {
  MQTTPrefs result{};
  applyMQTTDefaults(&saved);
  // Disabled slots still carry reusable settings and credentials.
  strcpy(saved.mqtt_slot_password[0], "keep-this-secret");
  strcpy(saved.mqtt_slot_host[0], "configured.example");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(memcmp(&result, &saved, sizeof(saved)) == 0);
  assert(!CompanionMqttSetupPortal::hasConfiguredSlot(result));

  // Enabling verification persists the appended flag, without lengthening
  // ordinary default-off Companion records or losing any broker credentials.
  MQTTPrefs enabled = result;
  enabled.mqtt_advert_verify = 1;
  assert(CompanionMqttSetupPortal::saveStoredConfig(enabled));
  assert(saved_length == 2881);
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(result.mqtt_advert_verify == 1);
  assert(strcmp(result.mqtt_slot_password[0], "keep-this-secret") == 0);
  // Every old recovery padding value is unconditionally default-off.
  saved_length=2880;
  for (uint8_t padding : {uint8_t(0), uint8_t(1), uint8_t(255)}) {
    saved.mqtt_advert_verify=padding;
    assert(CompanionMqttSetupPortal::loadStoredConfig(result));
    assert(result.mqtt_advert_verify==0);
    assert(strcmp(result.mqtt_slot_password[0], "keep-this-secret")==0);
  }
  saved_length=2881;
  saved.mqtt_advert_verify=1; persisted.marker=0; // malformed marker
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(result.mqtt_advert_verify==0);
  persisted.marker=CompanionMqttPrefsNvs::kVerifiedMarker;
  saved.mqtt_advert_verify=255;
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(result.mqtt_advert_verify == 0);
  assert(strcmp(result.mqtt_slot_password[0], "keep-this-secret") == 0);
  assert(CompanionMqttSetupPortal::saveStoredConfig(result));
  assert(saved_length == 2876);

  // Saving a custom broker in stages must not reset it on the next boot.
  strcpy(saved.mqtt_slot_preset[0], "custom");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(strcmp(result.mqtt_slot_host[0], "configured.example") == 0);
  assert(!CompanionMqttSetupPortal::hasConfiguredSlot(result));

  // A full URI needs no separate port override. Topic replaces IATA for custom.
  strcpy(saved.mqtt_slot_host[0], "mqtt://127.0.0.1:1");
  strcpy(saved.mqtt_slot_topic[0], "test/{device}/{type}");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(CompanionMqttSetupPortal::hasConfiguredSlot(result));

  if (RUNTIME_MQTT_SLOTS > 1) {
    // Slot 1 may be disabled while another slot is the only usable broker.
    applyMQTTDefaults(&saved);
    strcpy(saved.mqtt_slot_preset[1], "custom");
    strcpy(saved.mqtt_slot_host[1], "mqtt://127.0.0.1:1");
    strcpy(saved.mqtt_slot_topic[1], "test");
    assert(CompanionMqttSetupPortal::loadStoredConfig(result));
    assert(CompanionMqttSetupPortal::hasConfiguredSlot(result));
    assert(strcmp(result.mqtt_slot_preset[0], "none") == 0);
  }

  // Saved slots outside this image's runtime limit remain stored, but do not run.
  applyMQTTDefaults(&saved);
  strcpy(saved.mqtt_slot_preset[MAX_MQTT_SLOTS - 1], "custom");
  strcpy(saved.mqtt_slot_host[MAX_MQTT_SLOTS - 1], "mqtt://127.0.0.1:1");
  strcpy(saved.mqtt_slot_topic[MAX_MQTT_SLOTS - 1], "test");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(!CompanionMqttSetupPortal::hasConfiguredSlot(result));
  assert(memcmp(&result, &saved, sizeof(saved)) == 0);

  applyMQTTDefaults(&saved);
  strcpy(saved.mqtt_slot_preset[0], "analyzer-us");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(!CompanionMqttSetupPortal::hasConfiguredSlot(result));
  strcpy(saved.mqtt_iata, "SEA");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(CompanionMqttSetupPortal::hasConfiguredSlot(result));
  strcpy(saved.mqtt_slot_preset[0], "username-only");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(!CompanionMqttSetupPortal::hasConfiguredSlot(result));
  strcpy(saved.mqtt_slot_username[0], "user");
  assert(CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(CompanionMqttSetupPortal::hasConfiguredSlot(result));

  // Corrupt records still fail without overwriting live preferences.
  MQTTPrefs before = result;
  saved.mqtt_tx_enabled = 255;
  assert(!CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(memcmp(&before, &result, sizeof(before)) == 0);
  saved.mqtt_tx_enabled = 0;
  memset(saved.mqtt_slot_password[0], 'X', sizeof(saved.mqtt_slot_password[0]));
  assert(!CompanionMqttSetupPortal::loadStoredConfig(result));
  assert(memcmp(&before, &result, sizeof(before)) == 0);
}
"""


class CompanionMqttConfigTests(unittest.TestCase):
    def test_saved_settings_and_readiness_are_independent(self):
        source = SOURCE.read_text()
        actual = source[source.index("template <size_t N>"):
                        source.index("static bool readLine")]
        for signature in (
            "static bool isThreeLetterCode",
            "bool CompanionMqttSetupPortal::loadStoredConfig",
            "bool CompanionMqttSetupPortal::saveStoredConfig",
            "bool CompanionMqttSetupPortal::hasConfiguredSlot",
        ):
            actual += function(source, signature)
        with tempfile.TemporaryDirectory(prefix="companion-mqtt-config-") as tmp:
            cpp = Path(tmp) / "config.cpp"
            cpp.write_text(PREAMBLE + actual + SCENARIOS)
            for slots in (1, 3):
                with self.subTest(runtime_slots=slots):
                    binary = Path(tmp) / ("config-%d" % slots)
                    compile_result = subprocess.run([
                        "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-DWITH_MQTT_BRIDGE=1", "-DTEST_RUNTIME_SLOTS=%d" % slots,
                        "-I", str(ROOT / "src"), str(cpp), "-o", str(binary),
                    ], text=True, capture_output=True)
                    self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                    result = subprocess.run([str(binary)], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
