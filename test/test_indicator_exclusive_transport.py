#!/usr/bin/env python3

from pathlib import Path
import re
import unittest

import test_mqtt_canonical_wifi as canonical_wifi


root = Path(__file__).resolve().parents[1]
header = (root / "examples/companion_radio/CompanionWiFi.h").read_text()
main = (root / "examples/companion_radio/main.cpp").read_text()
mesh = (root / "examples/companion_radio/MyMesh.cpp").read_text()
build = (root / "build_legacy.sh").read_text()
readme = (root / "variants/sensecap_indicator-espnow/README.md").read_text()
webconfig = (root / "src/helpers/esp32/WebConfigServer.cpp").read_text()
mqtt = (root / "src/helpers/bridges/MQTTBridge.cpp").read_text()


# The opt-in cannot silently produce a one-sided or non-ESP32 transport build.
assert re.search(
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\).*?"
    r"defined\(ESP32\).*?defined\(WIFI_SSID\).*?defined\(BLE_PIN_CODE\).*?"
    r'#error "COMPANION_EXCLUSIVE_WIFI_BLE requires ESP32, WIFI_SSID, and BLE_PIN_CODE"',
    header,
    re.DOTALL,
)
assert "enum class CompanionTransportMode : uint8_t" in header
assert "CompanionTransportMode getCompanionTransportMode();" in header
assert "bool selectCompanionTransportMode(CompanionTransportMode mode);" in header


# The existing persisted byte is the sole source of truth. A failed write must
# restore RAM, and selection must not mutate the active-boot WiFi latch.
selector_start = main.index("CompanionTransportMode getCompanionTransportMode()")
selector_end = main.index(
    "static constexpr uint32_t COMPANION_WIFI_NTP_TIMEOUT_MS", selector_start
)
selector = main[selector_start:selector_end]
assert "the_mesh.getNodePrefs()->wifi_enabled != 0" in selector
assert "const uint8_t previous = prefs->wifi_enabled;" in selector
assert "if (the_mesh.savePrefs()) return true;" in selector
assert "prefs->wifi_enabled = previous;" in selector
assert "companion_wifi_requested = companionTransportWiFiActiveAtBoot();" in selector
setter = selector[
    selector.index("bool selectCompanionTransportMode(") :
    selector.index("static bool companionTransportWiFiActiveAtBoot()")
]
assert "companion_wifi_requested" not in setter


# A BLE-selected boot must not spend memory on dormant infrastructure-WiFi
# services before main.cpp applies the boot selection.
mesh_begin_start = mesh.index("void MyMesh::begin(")
mesh_begin_end = mesh.index("\nvoid MyMesh::configureRadioFromPrefs()", mesh_begin_start)
mesh_begin = mesh[mesh_begin_start:mesh_begin_end]
assert re.search(
    r"#if defined\(WITH_MQTT_BRIDGE\).*?"
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+"
    r"if \(_prefs\.wifi_enabled != 0\) \{.*?"
    r"_mqtt_bridge = new MQTTBridge",
    mesh_begin,
    re.DOTALL,
)
assert re.search(
    r"#ifdef WITH_WEBCONFIG\s+"
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+"
    r"if \(_prefs\.wifi_enabled != 0\) \{.*?"
    r"_webconfig = new WebConfigServer",
    mesh_begin,
    re.DOTALL,
)


# Full Bluetooth controller + host memory is released only for a WiFi-selected
# boot and before the WiFi stack is started. BLE-selected boots initialize BLE
# while the WiFi event handler/interface/start block remains gated out.
setup_start = main.index("void setup()")
setup_end = main.index("\nvoid loop()", setup_start)
setup = main[setup_start:setup_end]
exclusive_setup = setup[setup.index("loadCompanionTransportModeForBoot();"):]
assert "esp_bt_mem_release(ESP_BT_MODE_BTDM)" in main
assert exclusive_setup.index("loadCompanionTransportModeForBoot();") < exclusive_setup.index(
    "releaseCompanionBluetoothMemoryForWiFi();"
)
assert exclusive_setup.index("releaseCompanionBluetoothMemoryForWiFi();") < exclusive_setup.index(
    "startCompanionWiFi();"
)
assert re.search(
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+"
    r"if \(!companionTransportWiFiActiveAtBoot\(\)\) startCompanionBluetooth\(\);",
    setup,
)
assert re.search(
    r"#if defined\(ESP32\) && defined\(WIFI_SSID\)\s+"
    # Registering a callback does not start the WiFi stack. It must remain
    # available for session cleanup, while startup stays behind the boot gate.
    r"wifi_interface\.setSessionChangedCallback\(cancelCompanionWiFiSession, nullptr\);\s+"
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+"
    r"if \(companionTransportWiFiActiveAtBoot\(\)\) \{.*?"
    r"WiFi\.onEvent\(.*?startCompanionWiFi\(\);.*?"
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+\}",
    setup,
    re.DOTALL,
)
assert re.search(
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+"
    r"if \(!companionTransportWiFiActiveAtBoot\(\)\) \{\s+"
    r"serviceDeferredCompanionBluetooth\(\);",
    main,
)


# WiFi power-save conflicts describe the active boot transport, not merely the
# fact that BLE support was compiled into the binary. The legacy simultaneous
# behavior remains in the non-exclusive branch.
power_helper_start = main.index("static bool companionWiFiBluetoothActive()")
power_helper_end = main.index("const char* companionWiFiPowerSaveName", power_helper_start)
power_helper = main[power_helper_start:power_helper_end]
assert "companion_transport_boot_mode_loaded" in power_helper
assert "active_mode == CompanionTransportMode::Bluetooth" in power_helper
assert "#elif defined(BLE_PIN_CODE)" in power_helper
assert re.search(
    r"static bool bluetoothWiFiCoexistenceRequired\(\) \{\s+"
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+.*?return false;\s+"
    r"#elif defined\(BLE_PIN_CODE\) && defined\(WIFI_SSID\)",
    webconfig,
    re.DOTALL,
)
assert re.search(
    r"mesh::wifi::effectivePowerSave\(\s+"
    r"_manage_wifi && _node_info\.canonical_wifi \? _wifi_power_save : _obs->wifi_power_save,\s+"
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+false,\s+"
    r"#elif defined\(BLE_PIN_CODE\) && defined\(WIFI_SSID\)",
    mqtt,
)


# Local/framed/rescue dispatch and terminal help share the reboot-only selector.
local_start = mesh.index("bool MyMesh::handleLocalControlCommand(")
local_end = mesh.index("\n#if COMPANION_FEATURE_TEMP_RADIO\nvoid MyMesh::serviceTempRadio()", local_start)
local = mesh[local_start:local_end]
assert 'strcmp(command, "get companion.transport") == 0' in local
assert 'strncmp(command, "set companion.transport", 23) == 0' in local
assert '"OK - companion transport %s saved; reboot required"' in local
assert '"Error: failed to save companion transport"' in local
assert 'terminalOutput().print("  get companion.transport\\r\\n")' in mesh
assert 'terminalOutput().print("  set companion.transport <wifi|ble>\\r\\n")' in mesh
assert "WebUI unavailable while Bluetooth transport is active" in mesh


# Both Indicator Full overlays get this policy. ESP-NOW BLE mode keeps its
# primary WiFi/ESP-NOW radio while omitting only infrastructure WiFi services.
profile_start = build.index("\napply_companion_radio_full_profile() {")
profile_end = build.index("\napply_radio_overrides()", profile_start)
profile = build[profile_start:profile_end]
flag = "-DCOMPANION_EXCLUSIVE_WIFI_BLE=1"
assert profile.count(flag) == 1
exclusive_policy = profile.index(
    "# Both Indicator Full layouts select exactly one secondary"
)
exclusive_start = profile.index(
    "sensecapindicator-espnow_companion_radio_full|\\\n"
    "    sensecapindicator-lora_companion_radio_full|\\\n"
    "    sensecapindicator-lora-n16r2_companion_radio_full)",
    exclusive_policy,
)
exclusive_case = profile[exclusive_start:profile.index(";;", exclusive_start)]
assert "espnow" in exclusive_case.lower()
assert "-DCOMPANION_EXCLUSIVE_WIFI_BLE=1" in exclusive_case
assert "-DINDICATOR_TRANSPORT_RENDER_PROFILE=1" in exclusive_case
assert "-DUI_WIFI_SETUP_HOME_PAGE=1" in exclusive_case
assert "-DWEBCONFIG_AP_PREFIX='\\\"MC-Set\\\"'" in exclusive_case


assert "get companion.transport" in readme
assert "set companion.transport wifi" in readme
assert "set companion.transport ble" in readme
assert "reboot is required" in readme

print("test_indicator_exclusive_transport: PASS")


class MQTTTransportPowerSaveTest(unittest.TestCase):
    """Execute the actual connected-worker policy with real credential snapshots."""

    compile = canonical_wifi.MQTTCanonicalWiFiTest.compile

    def policy_fixture(self, bluetooth, primary_espnow, wifi_only=False):
        fixture = canonical_wifi.MQTTCanonicalWiFiTest.fixture(self)
        # Keep the shared production-method harness, with a smaller main that
        # exercises the connected worker's actual PS selection and driver call.
        fixture = fixture[:fixture.index("int main() {")]
        start = mqtt.index("      wifi_ps_type_t ps_mode;",
                           mqtt.index("bool MQTTBridge::handleWiFiConnection"))
        end = mqtt.index("      esp_wifi_set_ps(ps_mode);", start)
        policy = mqtt[start:end + len("      esp_wifi_set_ps(ps_mode);")]
        fixture = fixture.replace("constexpr bool kPrimaryEspNowRadio = false;",
                                  f"constexpr bool kPrimaryEspNowRadio = {str(primary_espnow).lower()};")
        fixture = fixture.replace("int esp_wifi_set_ps(int) { return 0; }",
                                  "int applied_ps = -1; int esp_wifi_set_ps(int mode) { applied_ps = mode; return 0; }")
        fixture = fixture.replace("  void beginWiFiStation();",
                                  "  void beginWiFiStation();\n  void applyConnectedPowerSave() {\n" + policy + "\n  }")
        modes = ["WIFI_PS_MIN_MODEM", "WIFI_PS_MIN_MODEM" if bluetooth else "WIFI_PS_NONE",
                 "WIFI_PS_MIN_MODEM" if primary_espnow else "WIFI_PS_MAX_MODEM"]
        return fixture + """
int main() {
  reset_nvs();
  nvs_values["wifi_only"] = {PT_U8, {}, @WIFI_ONLY@};
  assert(mesh::wifi::loadCompanionModeForBoot());
  assert(mesh::wifi::activeCompanionMode() == (@WIFI_ONLY@
      ? mesh::wifi::CompanionMode::WiFiOnly : mesh::wifi::CompanionMode::Normal));
  const int expected[] = { @MODES@ };
  const uint8_t expected_pref[] = { @PREFS@ };
  MQTTPrefs obs{};
  strcpy(obs.mqtt_slot_preset[0], "test");
  strcpy(obs.wifi_ssid, "legacy"); strcpy(obs.wifi_password, "legacy-password");
  char reply[160] = {};
  for (uint8_t saved = 0; saved < 3; ++saved) {
    reset_nvs();
    obs.wifi_power_save = (saved + 1) % 3;
    Credentials stored;
    strcpy(stored.ssid, "canonical"); strcpy(stored.password, "password");
    stored.power_save = saved;
    // Existing NVS may contain every stored value, including a setting that
    // this transport must clamp before applying it to the driver.
    assert(mesh::wifi::writeCredentials(stored));
    MQTTBridge worker(obs); worker.begin();
    assert(worker.isReady() && worker._wifi_power_save == saved);
    worker.applyConnectedPowerSave(); assert(applied_ps == expected[saved]);
    // Exercise the real WebConfig resolver and reload, not a policy mock:
    // WiFiOnly preserves NONE while Normal+BLE clamps it to MIN everywhere.
    char ssid[32]={}, password[65]={}; uint8_t web_ps=255;
    assert(WebConfigServer::loadStandaloneWiFi(ssid,sizeof(ssid),password,sizeof(password),&web_ps,&obs));
    assert(web_ps==expected_pref[saved]);
    WebConfigServer server; server._mqtt_prefs=&obs;
    assert(server.reloadStandaloneWiFi());
    assert(server._wifi_power_save==expected_pref[saved]);
    assert(WebConfigServer::formatWiFiPowerSave(reply,sizeof(reply),&obs));
    assert(strcmp(reply,expected_pref[saved]==1 ? "> none" : expected_pref[saved]==2 ? "> max" : "> min")==0);
    // A later portal/CLI save or changed observer pref cannot change an active
    // canonical worker; reconnect uses its begin-scoped snapshot without NVS.
    stored.power_save = (saved + 2) % 3;
    assert(mesh::wifi::writeCredentials(stored));
    obs.wifi_power_save = (saved + 1) % 3;
    assert(mesh::wifi::saveCompanionMode(@WIFI_ONLY@
        ? mesh::wifi::CompanionMode::Normal : mesh::wifi::CompanionMode::WiFiOnly)
        ==mesh::wifi::CompatibilitySaveResult::Saved);
    const int opens = nvs_opens;
    worker.applyConnectedPowerSave(); assert(applied_ps == expected[saved]);
    assert(nvs_opens == opens);
    MQTTBridge restarted(obs); restarted.begin();
    restarted.applyConnectedPowerSave(); assert(applied_ps == expected[(saved + 2) % 3]);
    // Unmanaged Companion and noncanonical legacy targets keep their existing
    // observer PS authority even with a different canonical tuple present.
    MQTTBridge companion(obs, true, false); companion.begin();
    companion.applyConnectedPowerSave(); assert(applied_ps == expected[obs.wifi_power_save]);
    MQTTBridge old_role(obs, false); old_role.begin();
    old_role.applyConnectedPowerSave(); assert(applied_ps == expected[obs.wifi_power_save]);
  }
  // Fresh legacy fallback and a saved CLI PS change feed the same prepared
  // value; permitted 'none' remains off, BLE coexistence rejects it.
  reset_nvs(); obs.wifi_power_save = 2;
  MQTTBridge fallback(obs); fallback.begin(); fallback.applyConnectedPowerSave();
  assert(applied_ps == expected[2]);
  assert(WebConfigServer::setStandaloneWiFiPowerSave("min", reply, sizeof(reply), &obs));
  MQTTBridge saved_min(obs); saved_min.begin(); saved_min.applyConnectedPowerSave();
  assert(saved_min._wifi_power_save == 0 && applied_ps == expected[0]);
  const bool none_saved = WebConfigServer::setStandaloneWiFiPowerSave("none", reply, sizeof(reply), &obs);
  assert(none_saved == @NONE_ALLOWED@);
  MQTTBridge saved_none(obs); saved_none.begin(); saved_none.applyConnectedPowerSave();
  assert(saved_none._wifi_power_save == (@NONE_ALLOWED@ ? 1 : 0));
  assert(applied_ps == expected[@NONE_ALLOWED@ ? 1 : 0]);
}
""".replace("@MODES@", ", ".join(modes)).replace(
            "@PREFS@", f"0, {0 if bluetooth else 1}, {0 if primary_espnow else 2}"
        ).replace("@NONE_ALLOWED@", str(not bluetooth).lower()).replace(
            "@WIFI_ONLY@", "1" if wifi_only else "0")

    def test_connected_worker_snapshot_and_transport_coexistence(self):
        profiles = (
            ("ordinary", False, False, False, ()),
            ("simultaneous_ble", True, False, False, ("-DBLE_PIN_CODE=1", '-DWIFI_SSID="compiled"')),
            ("wifi_only_ble_compiled", False, False, True, ("-DBLE_PIN_CODE=1", '-DWIFI_SSID="compiled"')),
            ("exclusive_wifi", False, False, False, ("-DCOMPANION_EXCLUSIVE_WIFI_BLE=1", "-DBLE_PIN_CODE=1", '-DWIFI_SSID="compiled"')),
            ("primary_espnow", False, True, False, ()),
            ("espnow_with_ble", True, True, False, ("-DBLE_PIN_CODE=1", '-DWIFI_SSID="compiled"')),
            ("espnow_wifi_only_ble_compiled", False, True, True, ("-DBLE_PIN_CODE=1", '-DWIFI_SSID="compiled"')),
            ("espnow_exclusive_wifi", False, True, False, ("-DCOMPANION_EXCLUSIVE_WIFI_BLE=1", "-DBLE_PIN_CODE=1", '-DWIFI_SSID="compiled"')),
        )
        for name, bluetooth, primary, wifi_only, flags in profiles:
            with self.subTest(profile=name):
                self.compile(self.policy_fixture(bluetooth, primary, wifi_only), *flags)

    def test_worker_cannot_revert_to_mutable_legacy_power_save(self):
        fixture = self.policy_fixture(False, False)
        stale = fixture.replace(
            "_manage_wifi && _node_info.canonical_wifi ? _wifi_power_save : _obs->wifi_power_save,",
            "_obs->wifi_power_save,")
        self.assertNotEqual(stale, fixture)
        with self.assertRaises(canonical_wifi.FixtureFailure):
            self.compile(stale)

    def test_wifi_only_rejects_old_compiled_ble_clamps_in_both_consumers(self):
        fixture = self.policy_fixture(False, False, wifi_only=True)
        for signature in ("static bool bluetoothWiFiCoexistenceRequired(",
                          "void applyConnectedPowerSave("):
            with self.subTest(consumer=signature):
                current = canonical_wifi.extract_braced(fixture, signature)
                old = re.sub(
                    r"mesh::wifi::activeCompanionMode\(\)\s*"
                    r"!= mesh::wifi::CompanionMode::WiFiOnly",
                    "true", current)
                self.assertNotEqual(current, old)
                stale = fixture.replace(current, old, 1)
                with self.assertRaises(canonical_wifi.FixtureFailure):
                    self.compile(stale, "-DBLE_PIN_CODE=1", '-DWIFI_SSID="compiled"')


if __name__ == "__main__":
    unittest.main()
