#!/usr/bin/env python3

from pathlib import Path
import re


root = Path(__file__).resolve().parents[1]
header = (root / "examples/companion_radio/CompanionWiFi.h").read_text()
main = (root / "examples/companion_radio/main.cpp").read_text()
mesh = (root / "examples/companion_radio/MyMesh.cpp").read_text()
build = (root / "build.sh").read_text()
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
assert "esp_bt_mem_release(ESP_BT_MODE_BTDM)" in main
assert setup.index("loadCompanionTransportModeForBoot();") < setup.index(
    "releaseCompanionBluetoothMemoryForWiFi();"
)
assert setup.index("releaseCompanionBluetoothMemoryForWiFi();") < setup.index(
    "startCompanionWiFi();"
)
assert re.search(
    r"#if defined\(COMPANION_EXCLUSIVE_WIFI_BLE\)\s+"
    r"if \(!companionTransportWiFiActiveAtBoot\(\)\) startCompanionBluetooth\(\);",
    setup,
)
assert re.search(
    r"#ifdef WIFI_SSID\s+"
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
    r"mesh::wifi::effectivePowerSave\(\s+_obs->wifi_power_save,\s+"
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
exclusive_case = re.search(
    r'case "\$\{env_name,,\}" in\s+'
    r"sensecapindicator-espnow_companion_radio_full\|\\\s+"
    r"sensecapindicator-lora_companion_radio_full\)\s+"
    r'export PLATFORMIO_BUILD_FLAGS="\$\{PLATFORMIO_BUILD_FLAGS\} '
    r"-DCOMPANION_EXCLUSIVE_WIFI_BLE=1 "
    r"-DINDICATOR_TRANSPORT_RENDER_PROFILE=1\"\s+;;\s+esac",
    profile,
)
assert exclusive_case
assert "espnow" in exclusive_case.group(0).lower()


assert "get companion.transport" in readme
assert "set companion.transport wifi" in readme
assert "set companion.transport ble" in readme
assert "reboot is required" in readme

print("test_indicator_exclusive_transport: PASS")
