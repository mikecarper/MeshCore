#!/usr/bin/env python3
"""
Tests for motalib - run with the pipx-managed detools environment:

    "$(pipx environment --value PIPX_LOCAL_VENVS)/detools/bin/python" tools/mota/test_mota.py

(Also pytest-compatible: functions are named test_*.)
"""

from __future__ import annotations

import io
import importlib.util
import json
import os
import random
import struct
from pathlib import Path

import motalib as ml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _fw(seed, size):
    random.seed(seed)
    return bytes(random.getrandbits(8) for _ in range(size))


# --- multihash / version ---------------------------------------------------

def test_version_pack_roundtrip():
    assert ml.pack_version("1.16.0") == (1 << 24) | (16 << 16)
    assert ml.unpack_version(ml.pack_version("1.16.0.2")) == "1.16.0.2"
    assert ml.pack_version(0x01100000) == 0x01100000


def test_target_id_for_env():
    import hashlib
    env = "RAK_4631_companion_radio_usb"
    expect = int.from_bytes(hashlib.sha256(env.encode()).digest()[:4], "little")
    assert ml.target_id_for_env(env) == expect
    # distinct envs (same board, different role) get distinct ids
    assert ml.target_id_for_env("RAK_4631_repeater") != ml.target_id_for_env("RAK_4631_companion_radio_usb")


def test_internal_bootloader_target_wiring_is_central_and_not_duplicated():
    root = Path(__file__).resolve().parents[2]
    inventory = [
        line.strip()
        for line in (root / "tools/mota/nrf52_internal_bootloader_targets.txt")
        .read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(inventory) == len(set(inventory))
    assert len(inventory) == 21

    target_header = (root / "src/helpers/ota/OtaTargets.h").read_text(encoding="utf-8")
    for target in inventory:
        assert f'"{target}"' in target_header

    pio = (root / "platformio.ini").read_text(encoding="utf-8")
    nrf52_base = pio.split("[nrf52_base]", 1)[1].split("\n[", 1)[0]
    assert nrf52_base.count("pre:scripts/nrf52_internal_bootloader_link.py") == 1

    build = (root / "build.sh").read_text(encoding="utf-8")
    recipe = build.split("apply_nrf52_lora_ota_build_recipe()", 1)[1]
    recipe = recipe.split("supports_nrf52_internal_bootloader_update()", 1)[0]
    assert "scripts/nrf52_internal_bootloader_link.py" not in recipe
    assert "export MESHCORE_NRF52_INTERNAL_BOOTLOADER_UPDATE=1" in recipe


def test_full_esp32_profile_unifies_usb_logging_and_wifi_mqtt():
    root = Path(__file__).resolve().parents[2]
    build = (root / "build.sh").read_text(encoding="utf-8")
    profile = build.split("run_full_esp32_profile()", 1)[1]
    profile = profile.split("run_full_esp32_build_targets()", 1)[0]

    assert 'PACKET_LOGGING_OVERRIDE="on"' in profile
    assert 'MQTT_BRIDGE_OVERRIDE="on"' in profile
    assert 'MESHDEBUG_OVERRIDE="off"' in profile
    assert 'FIRMWARE_FILENAME_INFIX="full-usb-wifi"' in profile
    assert "do not also build its former non-MQTT FULL-logging twin" in profile

    mqtt_gate = build.split("disable_usb_logging_for_mqtt()", 1)[1]
    mqtt_gate = mqtt_gate.split("is_esp32_usb_wifi_companion_ota_build()", 1)[0]
    assert 'is_companion_radio_full_target "$env_name"' in mqtt_gate
    assert 'PACKET_LOGGING_OVERRIDE,,}" = "on"' in mqtt_gate
    assert '! is_esp32_companion_build "$env_name"' in mqtt_gate

    matrix = build.split("run_logging_matrix_build_targets()", 1)[1]
    matrix = matrix.split("run_build_targets()", 1)[0]
    assert 'uses_merged_standard_usb_logging "$target"' in matrix
    assert 'run_full_esp32_profile "FULL unified pass"' in matrix
    assert 'run_full_esp32_profile "FULL logging fallback pass"' in matrix
    assert "ESP32 uses one TTY" in mqtt_gate
    assert "input-capable logging terminal" in mqtt_gate
    assert "plaintext cannot mix with framed traffic" in mqtt_gate


def test_espnow_tx_power_matches_cli_callback_contract():
    root = Path(__file__).resolve().parents[2]
    header = (root / "src/helpers/esp32/ESPNOWRadio.h").read_text(
        encoding="utf-8"
    )
    implementation = (
        root / "src/helpers/esp32/ESPNOWRadio.cpp"
    ).read_text(encoding="utf-8")

    assert "bool setTxPower(int8_t dbm);" in header
    assert "bool ESPNOWRadio::setTxPower(int8_t dbm)" in implementation
    assert "esp_wifi_set_max_tx_power(dbm * 4) == ESP_OK" in implementation


def test_flash_constrained_stm32_repeaters_pin_the_size_qualified_toolchain():
    root = Path(__file__).resolve().parents[2]
    toolchain = "platformio/toolchain-gccarmnoneeabi@^1.140201.0"
    profiles = (
        ("variants/rak3x72/platformio.ini", "[env:RAK_3x72_repeater]"),
        ("variants/tiny_relay/platformio.ini", "[env:Tiny_Relay_repeater]"),
        (
            "variants/wio-e5-mini/platformio.ini",
            "[env:wio-e5-mini_repeater]",
        ),
        ("variants/wio-e5-dev/platformio.ini", "[env:wio-e5_repeater]"),
        (
            "variants/wio-e5-dev/platformio.ini",
            "[env:wio-e5-repeater_bridge_rs232]",
        ),
    )

    for path, section_name in profiles:
        text = (root / path).read_text(encoding="utf-8")
        section = text.split(section_name, 1)[1].split("\n[", 1)[0]
        assert toolchain in section
        assert "-fno-schedule-insns2" in section
        assert "-Wl,--sort-section=alignment" in section
        assert "-D MESH_PACKET_LOGGING_COMPACT=1" in section

    dispatcher = (root / "src/Dispatcher.cpp").read_text(encoding="utf-8")
    repeater = (
        root / "examples/simple_repeater/MyMesh.cpp"
    ).read_text(encoding="utf-8")
    assert "#if MESH_PACKET_LOGGING_COMPACT" in dispatcher
    assert 'line.printf("T");' in dispatcher
    assert "line.hex(raw, len);" in dispatcher
    assert "line.flush(usbLoggingPort(), false);" in dispatcher
    assert "!MESH_PACKET_LOGGING_COMPACT" in dispatcher
    assert "#if MESH_PACKET_LOGGING_COMPACT" in repeater
    assert 'line.printf("R");' in repeater
    assert "line.hex(raw, len);" in repeater
    assert "line.flush(mesh::usbLoggingPort(), false);" in repeater


def test_flash_constrained_stm32_companions_pin_the_size_qualified_toolchain():
    root = Path(__file__).resolve().parents[2]
    toolchain = "platformio/toolchain-gccarmnoneeabi@^1.140201.0"
    profiles = (
        (
            "variants/rak3x72/platformio.ini",
            "[env:RAK_3x72_companion_radio_usb]",
        ),
        (
            "variants/tiny_relay/platformio.ini",
            "[env:Tiny_Relay_companion_radio_usb]",
        ),
        (
            "variants/wio-e5-mini/platformio.ini",
            "[env:wio-e5-mini_companion_radio_usb]",
        ),
        (
            "variants/wio-e5-dev/platformio.ini",
            "[env:wio-e5_companion_radio_usb]",
        ),
    )

    for path, section_name in profiles:
        text = (root / path).read_text(encoding="utf-8")
        section = text.split(section_name, 1)[1].split("\n[", 1)[0]
        assert toolchain in section
        assert "-fno-schedule-insns2" in section
        assert "-Wl,--sort-section=alignment" in section
        assert "MESH_PACKET_LOGGING_COMPACT" not in section


def test_usb_companion_profiles_enable_the_usb_transport():
    root = Path(__file__).resolve().parents[2]
    profiles = (
        (
            "variants/heltec_e290/platformio.ini",
            "[env:Heltec_E290_companion_usb]",
            "[env:Heltec_E290_repeater]",
        ),
        (
            "variants/meshnology_w12/platformio.ini",
            "[env:meshnology_w12_companion_radio_usb]",
            "[env:meshnology_w12_companion_radio_ble]",
        ),
        (
            "variants/xiao_nrf52/platformio.ini",
            "[env:solarxiao_30S_companion_radio_usb]",
            "[env:solarxiao_33S_companion_radio_usb]",
        ),
    )

    for relative, section, next_section in profiles:
        variant = (root / relative).read_text(encoding="utf-8")
        profile = variant.split(section, 1)[1].split(next_section, 1)[0]
        assert "-D ENABLE_USB_INTERFACE" in profile


def test_canonical_bulk_matrix_omits_runtime_and_transport_aliases():
    root = Path(__file__).resolve().parents[2]
    build = (root / "build.sh").read_text(encoding="utf-8")

    resolver = build.split("resolve_all_firmwares()", 1)[1]
    resolver = resolver.split("is_legacy_companion_power_saving_target()", 1)[0]
    assert 'is_redundant_bulk_build_target "$env_name"' in resolver

    runtime = build.split("is_runtime_setting_alias_target()", 1)[1]
    runtime = runtime.split("is_redundant_bulk_build_target()", 1)[0]
    assert "is_legacy_companion_power_saving_target" in runtime
    assert "is_legacy_companion_femoff_target" in runtime
    assert "is_legacy_radio_gain_profile_target" in runtime
    assert "is_exact_companion_recipe_alias_target" in runtime

    redundant = build.split("is_redundant_bulk_build_target()", 1)[1]
    redundant = redundant.split("resolve_logging_matrix_firmwares()", 1)[0]
    assert "is_runtime_setting_alias_target" in redundant
    assert "is_firmware_role_replaced_by_canonical_artifact" in redundant

    logging_matrix = build.split("resolve_logging_matrix_firmwares()", 1)[1]
    logging_matrix = logging_matrix.split("resolve_companion_firmwares()", 1)[0]
    assert "resolve_all_firmwares" in logging_matrix
    assert "print_nrf52_usb_logging_source_targets" not in build

    full = build.split("apply_companion_radio_full_profile() {", 1)[1]
    full = full.split("get_firmware_filename()", 1)[0]
    assert "-DOTA_SEEDER_ONLY=1" in full
    assert "-DMOTA_TARGET_ID=0" in full
    assert "-UOTA_FLASH_STORE" in full
    assert "-UOTA_SD_STORE" in full
    assert "-UWEBCONFIG_DISABLED" in full
    assert "-DMESH_DEBUG=1" in full
    assert "-DMESH_PACKET_LOGGING=1" in full
    nrf52_full = full.split(
        'if is_nrf52_companion_radio_full_target "$env_name"', 1
    )[1].split("return 0", 1)[0]
    assert "-DCFG_TUD_CDC=2" in nrf52_full
    assert "-DMESH_DUAL_CDC_LOGGING=1" in nrf52_full
    esp32_full = full.split("return 0\n  fi", 1)[1]
    assert "-DCFG_TUD_CDC=2" not in esp32_full
    assert "-DMESH_DUAL_CDC_LOGGING=1" not in esp32_full
    assert "-DCOMPANION_FEATURE_DEDICATED_USB_LOGGING=1" not in esp32_full
    assert 'disable_debug_flags "$env_name"' in build
    assert 'apply_debug_overrides "$env_name"' in build

    debug_overrides = build.split("apply_debug_overrides()", 1)[1]
    debug_overrides = debug_overrides.split(
        "disable_usb_logging_for_mqtt()", 1
    )[0]
    assert "preserve_full_companion_logging" in debug_overrides
    assert 'is_companion_radio_full_target "$env_name"' in debug_overrides
    assert 'if [ "$preserve_full_companion_logging" -eq 0 ]' in debug_overrides

    disable_debug = build.split("disable_debug_flags()", 1)[1]
    disable_debug = disable_debug.split("apply_mqtt_bridge_override()", 1)[0]
    assert 'is_companion_radio_full_target "$env_name"' in disable_debug
    assert 'usb_logging_undefs=""' in disable_debug

    assert "is_esp32_dual_cdc_companion_radio_full_target" not in build

    for relative, section, next_section in (
        (
            "variants/rak3112/platformio.ini",
            "[env:RAK_3112_companion_radio_full]",
            "[env:RAK_3112_sensor]",
        ),
        (
            "variants/heltec_rc32/platformio.ini",
            "[env:heltec_rc32_without_display_companion_radio_full]",
            "[env:heltec_rc32_without_display_sensor]",
        ),
        (
            "variants/heltec_rc32/platformio.ini",
            "[env:heltec_rc32_companion_radio_full]",
            "[env:heltec_rc32_sensor]",
        ),
    ):
        variant = (root / relative).read_text(encoding="utf-8")
        profile = variant.split(section, 1)[1].split(next_section, 1)[0]
        assert "esp32_s3_dual_cdc_full" not in profile

    usb_logging = (root / "src/helpers/UsbLogging.cpp").read_text(
        encoding="utf-8"
    )
    assert "class DedicatedUsbLoggingCdc : public Adafruit_USBD_CDC" in usb_logging
    assert "static DedicatedUsbLoggingCdc dedicated_usb_logging_port" in usb_logging
    assert "supported only by nRF52 Full Companion" in usb_logging
    assert "MESH_ESP32_DUAL_CDC_LOGGING" not in usb_logging
    assert "USBCDC(1)" not in usb_logging
    assert "TinyUSBDevice.detach()" in usb_logging
    assert "return dedicated_usb_logging_port" in usb_logging
    assert "return null_usb_logging_stream" in usb_logging

    companion = (root / "examples/companion_radio/MyMesh.cpp").read_text(
        encoding="utf-8"
    )
    companion_features = (
        root / "examples/companion_radio/CompanionFeatures.h"
    ).read_text(encoding="utf-8")
    companion_main = (root / "examples/companion_radio/main.cpp").read_text(
        encoding="utf-8"
    )
    assert "COMPANION_FEATURE_DEDICATED_USB_LOGGING" in companion_features
    assert "defined(COMPANION_RADIO_FULL)" in companion
    assert "mesh::selectUsbLoggingTerminalAction(" in companion_main
    assert "mesh::hasDedicatedUsbLoggingPort()" in companion_main
    assert "mesh::isUsbLoggingEnabled()" in companion_main
    assert "enterUsbLoggingTerminalMode();" in companion_main
    assert "usb_logging_terminal_mode" in companion_main
    assert "_prefs.usb_logging_enabled = 0" in companion
    assert 'strcmp(value, "on reboot") == 0' in companion
    assert 'strcmp(value, "off reboot") == 0' in companion
    assert "reboot required to change USB interfaces" in companion
    assert "rebooting to change USB interfaces" in companion
    assert "mesh::saveUsbLoggingBootPreference(enabled)" in companion

    companion_main = (
        root / "examples/companion_radio/main.cpp"
    ).read_text(encoding="utf-8")
    assert "!mesh::hasDedicatedUsbLoggingPort()" in companion_main
    assert "mesh::isUsbLoggingEnabled()" in companion_main
    assert "enterUsbTerminalMode();" in companion_main

    replacement = build.split(
        "get_esp32_full_companion_replacement()", 1
    )[1].split("get_full_companion_replacement()", 1)[0]
    assert 'is_esp32_companion_radio_full_target "$full_env"' in replacement
    assert "is_esp32_dual_cdc_companion_radio_full_target" not in replacement


def test_single_tty_logging_off_restores_each_builds_default_mode():
    root = Path(__file__).resolve().parents[2]
    companion_main = (
        root / "examples/companion_radio/main.cpp"
    ).read_text(encoding="utf-8")
    service = companion_main.split(
        "static void serviceUsbTerminal() {", 1
    )[1].split(
        "static void expireUsbBinaryStartupProbeBeforeDispatch()", 1
    )[0]

    # The shared policy distinguishes Full's startup ASCII mode from an
    # ordinary USB Companion's Binary default, and an active TCP terminal keeps
    # ownership even when the saved USB logging preference is on.
    assert "mesh::selectUsbLoggingTerminalAction(" in service
    assert "isNetworkTerminalActive()" in service
    assert "case mesh::UsbLoggingTerminalAction::RETURN_TO_BINARY:" in service
    assert "leaveUsbTerminalMode(true);" in service
    assert "case mesh::UsbLoggingTerminalAction::KEEP_ASCII:" in service
    keep_ascii = service.split(
        "case mesh::UsbLoggingTerminalAction::KEEP_ASCII:", 1
    )[1].split(
        "case mesh::UsbLoggingTerminalAction::NO_ACTION:", 1
    )[0]
    assert "usb_logging_terminal_mode = false;" in keep_ascii
    assert "clearUsbTerminalLine();" in keep_ascii
    assert "usb_terminal_discard_line = false;" in keep_ascii
    assert "leaveUsbTerminalMode" not in keep_ascii

    # A command entered on the logging terminal follows the same split: Full
    # stays in ASCII, while a non-Full Companion returns to Binary immediately.
    command_disable = service.split(
        "the_mesh.handleTerminalCommand(usb_terminal_line);", 1
    )[1].split('usbTerminalOutput().print("> ");', 1)[0]
    assert "!mesh::isUsbLoggingEnabled()" in command_disable
    assert "usb_logging_terminal_mode = false;" in command_disable
    assert "#if defined(COMPANION_RADIO_FULL)" in command_disable
    assert "leaveUsbTerminalMode(true);" in command_disable

    # Full Companion reaches Binary through its ordinary explicit token or
    # framed startup probe after logging is disabled.
    terminal_stop = service.split(
        "if (strcmp(usb_terminal_line, USB_TERMINAL_STOP_TOKEN) == 0) {", 1
    )[1].split("}", 1)[0]
    assert "leaveUsbTerminalMode(true);" in terminal_stop
    assert "if (!usb_logging_terminal_mode" in service
    assert "usb_binary_startup_probe.shouldStart(" in service


def test_measured_full_companion_promotions_are_exact_and_bounded():
    root = Path(__file__).resolve().parents[2]
    build = (root / "build.sh").read_text(encoding="utf-8")
    qualified = build.split("# Some qualified boards historically", 1)[1]
    qualified = qualified.split("\n  fi\n}\n\nget_pio_envs", 1)[0]

    expected = [
        "M5Stack_Unit_C6L_companion_radio_ble",
        "Heltec_Wireless_Tracker_companion_radio_ble",
        "LilyGo_T3S3_sx1276_companion_radio_ble",
        "Heltec_ct62_companion_radio_ble",
        "Meshadventurer_sx1262_companion_radio_ble",
        "Meshadventurer_sx1268_companion_radio_ble",
        "Heltec_Wireless_Paper_companion_radio_ble",
        "Heltec_E213_companion_radio_ble",
        "Xiao_S3_companion_radio_ble",
        "LilyGo_TETH_Elite_sx1262_companion_radio_ble",
        "LilyGo_T3S3_sx1262_companion_radio_ble",
        "LilyGo_TDeck_companion_radio_ble",
        "Ebyte_EoRa-S3_companion_radio_ble",
        "Tbeam_SX1262_companion_radio_ble",
        "Tbeam_SX1276_companion_radio_ble",
        "T_Beam_S3_Supreme_SX1262_companion_radio_ble",
        "GAT562_Mesh_Watch13_companion_radio_ble",
        "LilyGo_T-Echo-Lite_companion_radio_ble",
        "LilyGo_T_Impulse_Plus_companion_radio_ble",
        "WioTrackerL1Eink_companion_radio_ble",
    ]
    listed = [
        line.strip()
        for line in qualified.splitlines()
        if line.strip().endswith("_companion_radio_ble")
    ]
    assert listed == expected
    assert 'PIO_ENV_BUILD_BASE_BY_NAME["$full_env"]="$env_name"' in qualified

    profile = build.split("apply_companion_radio_full_profile()", 1)[1]
    profile = profile.split("apply_radio_overrides()", 1)[0]
    assert "-DWIFI_SSID=" in profile
    assert "+<helpers/esp32/SerialWifiInterface.cpp>" in profile
    assert "meshadventurer_sx1262_companion_radio_full" in profile
    assert "meshadventurer_sx1268_companion_radio_full" in profile
    assert "-DMAX_CONTACTS=160" in profile
    assert "-DMAX_GROUP_CHANNELS=30" in profile
    assert "-DOFFLINE_QUEUE_SIZE=64" in profile
    assert "160 contacts, 30 channels, and 64 queued frames" in profile


def test_full_companion_wireless_startup_and_psram_contacts_are_resilient():
    root = Path(__file__).resolve().parents[2]
    main = (root / "examples/companion_radio/main.cpp").read_text(
        encoding="utf-8"
    )
    setup = main.split("void setup()", 1)[1]
    assert setup.index("startCompanionBluetooth();") < setup.index("WiFi.onEvent")
    assert "if (!bluetooth_interface.begin(" in main
    assert "interface_manager.removeInterface(&bluetooth_interface)" in main
    assert "Bluetooth initialization failed; retrying in 5 seconds" in main

    esp_ble = (root / "src/helpers/esp32/SerialBLEInterface.cpp").read_text(
        encoding="utf-8"
    )
    nrf_ble = (root / "src/helpers/nrf52/SerialBLEInterface.cpp").read_text(
        encoding="utf-8"
    )
    assert 'extern "C" bool bleInUse(void)' in esp_ble
    assert "bool SerialBLEInterface::begin(" in esp_ble
    assert "Arduino-ESP32 2.x Bluedroid exposes a void init()" in esp_ble
    assert "if (pServer == NULL)" in esp_ble
    assert "if (pService == NULL)" in esp_ble
    assert "if (pTxCharacteristic == NULL)" in esp_ble
    assert "if (pRxCharacteristic == NULL)" in esp_ble
    assert "bool SerialBLEInterface::begin(" in nrf_ble
    assert "if (!Bluefruit.begin())" in nrf_ble

    contacts_header = (root / "src/helpers/BaseChatMesh.h").read_text(
        encoding="utf-8"
    )
    contacts_source = (root / "src/helpers/BaseChatMesh.cpp").read_text(
        encoding="utf-8"
    )
    assert "CONTACT_PSRAM_FALLBACK_REGULAR_SLOTS" in contacts_header
    assert "ContactInfo* contacts" in contacts_header
    assert "int* sort_array" in contacts_header
    assert "heap_caps_calloc" in contacts_source
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in contacts_source
    assert "contact_capacity = requested_capacity" in contacts_source
    assert setup.index("the_mesh.begin(") < setup.index("startCompanionBluetooth();")
    companion = (root / "examples/companion_radio/MyMesh.cpp").read_text(
        encoding="utf-8"
    )
    assert companion.index("initializeContactStorage();") < companion.index(
        "initializeOfflineQueue();"
    )


def test_esp32_s3_full_profiles_use_arduino2_and_dio_boot_mode():
    root = Path(__file__).resolve().parents[2]
    project = (root / "platformio.ini").read_text(encoding="utf-8")
    esp32_base = project.split("[esp32_base]", 1)[1].split("\n[", 1)[0]
    assert "platformio/espressif32@6.11.0" in esp32_base
    shared = project.split("[esp32_s3_full]", 1)[1].split(
        "\n[", 1
    )[0]
    assert "board_build.flash_mode = dio" in shared
    assert "[esp32_s3_dual_cdc_full]" not in project

    build = (root / "build.sh").read_text(encoding="utf-8")
    framework_selector = build.split(
        "requires_esp32_arduino3_framework()", 1
    )[1].split("prepare_esp32_arduino3_framework()", 1)[0]
    assert "heltec_rc32_" in framework_selector
    assert (
        "is_esp32_dual_cdc_companion_radio_full_target"
        not in framework_selector
    )
    framework_preflight = build.split(
        "prepare_esp32_arduino3_framework()", 1
    )[1].split("requires_esp32_companion_full_ota_fallback()", 1)[0]
    assert "requires_esp32_arduino3_framework" in framework_preflight
    assert 'framework-arduinoespressif32/package.json' in framework_preflight
    assert '"3\\.3\\.11"' in framework_preflight
    assert "pio pkg uninstall --global --tool framework-arduinoespressif32" in framework_preflight
    assert 'pio pkg install --global --tool "$arduino3_core_url"' in framework_preflight
    assert 'pio pkg install --global --tool "$arduino3_libs_url"' in framework_preflight
    assert 'pio pkg install -e "$pio_env_name"' in framework_preflight
    build_call = build.split(
        'print_build_flags "$pio_env_name" "$env_name"', 1
    )[1].split(
        "restore_platformio_build_flags", 1
    )[0]
    assert 'prepare_esp32_arduino3_framework "$env_name" "$pio_env_name"' in build_call
    assert 'if [ "$build_status" -eq 0 ]; then' in build_call
    assert 'flock "$platformio_package_lock_fd"' in build_call
    assert 'flock -u "$platformio_package_lock_fd"' in build_call

    s3_full_variant_dirs = {
        "heltec_rc32",
        "heltec_tracker_v2",
        "heltec_v4",
        "heltec_v4_r8",
        "lilygo_tbeam_1w",
        "meshnology_w12",
        "nibble_screen_connect",
        "nibble_zero_connect",
        "rak3112",
        "station_g2",
        "station_g3_esp32",
        "xiao_s3_wio",
    }
    profile_count = 0
    for variant_dir in sorted(s3_full_variant_dirs):
        path = root / "variants" / variant_dir / "platformio.ini"
        content = path.read_text(encoding="utf-8")
        for section in content.split("\n["):
            header = section.split("]", 1)[0].lower()
            if "companion_radio_full" not in header:
                continue
            profile_count += 1
            assert (
                "board_build.flash_mode = "
                "${esp32_s3_full.board_build.flash_mode}"
            ) in section, f"ESP32-S3 Full profile lacks shared DIO mode: {path}"
            assert "esp32_s3_dual_cdc_full" not in section
            if variant_dir != "heltec_rc32":
                assert "55.03.311" not in section
                assert "esp32-core-3.3.11" not in section

    assert profile_count == 18


def test_tbeam_1w_release_hardware_fixes_are_preserved():
    root = Path(__file__).resolve().parents[2]
    variant_root = root / "variants/lilygo_tbeam_1w"
    profile = (variant_root / "platformio.ini").read_text(encoding="utf-8")
    variant = (variant_root / "variant.h").read_text(encoding="utf-8")
    pins = (variant_root / "pins_arduino.h").read_text(encoding="utf-8")
    target = (variant_root / "target.cpp").read_text(encoding="utf-8")
    board_impl = (variant_root / "TBeam1WBoard.cpp").read_text(
        encoding="utf-8"
    )
    wrapper = (
        root / "src/helpers/radiolib/CustomSX1262Wrapper.h"
    ).read_text(encoding="utf-8")
    board_manifest = json.loads(
        (root / "boards/t_beam_1w.json").read_text(encoding="utf-8")
    )

    # Hardware fixes advertised by the T-Beam 1W v1.17.1 release. Keep the
    # build flag and header fallback aligned so every role uses 1700 us.
    for flag in (
        "-D USE_SX1262",
        "-D SX126X_REGISTER_PATCH=1",
        "-D SX126X_PA_RAMP_TIME=0x06",
        "-D SX126X_CURRENT_LIMIT=140",
        "-D SX126X_RX_BOOSTED_GAIN=1",
        "-D MAX_LORA_TX_POWER=22",
        "-D BATT_MIN_MILLIVOLTS=6000",
        "-D BATT_MAX_MILLIVOLTS=8400",
        "-D GPS_BAUD_RATE=9600",
    ):
        assert flag in profile
    assert "#define SX126X_PA_RAMP_TIME 0x06" in variant
    assert "RADIOLIB_SX126X_PA_RAMP_1700U" in variant

    # This branch has a working board-specific Arduino variant. Protect its
    # correct default SPI pins instead of regressing to generic ESP32-S3 pins.
    assert board_manifest["build"]["variant"] == "lilygo_tbeam_1w"
    assert (
        board_manifest["build"]["arduino"]["partitions"]
        == "default_16MB.csv"
    )
    assert "static const uint8_t MISO = 12;" in pins
    assert "static const uint8_t SCK = 13;" in pins

    # The extended ramp must survive boot, CLI power changes, and recovery.
    assert "radio.setTxParams(" in target
    assert "SX126X_PA_RAMP_TIME" in target
    assert "applyCachedTxPower(int8_t dbm) override" in wrapper
    assert "SX126X_PA_RAMP_TIME" in wrapper

    # Preserve the hardware-tested battery divider and fan thermostat.
    assert "#define BATTERY_PIN 4" in variant
    assert "#define BATTERY_SENSE_SAMPLES 30" in variant
    assert "#define ADC_MULTIPLIER 2.9333f" in variant
    assert "#define FAN_TEMP_ON_C 45.0f" in board_impl
    assert "#define FAN_TEMP_OFF_C 41.0f" in board_impl
    assert "#define FAN_MIN_RUN_TIME_MS 5000UL" in board_impl
    for role_main in (
        "examples/companion_radio/main.cpp",
        "examples/simple_repeater/main.cpp",
        "examples/simple_room_server/main.cpp",
        "examples/kiss_modem/main.cpp",
    ):
        source = (root / role_main).read_text(encoding="utf-8")
        assert "board.updateFanControl();" in source


def test_release_catalog_resolves_canonical_runtime_aliases():
    root = Path(__file__).resolve().parents[2]
    provider_path = root / "mesh-america/update-provider-release.py"
    spec = importlib.util.spec_from_file_location(
        "meshcore_provider_release_test", provider_path
    )
    assert spec is not None and spec.loader is not None
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)

    esp32_dual_cdc = getattr(provider, "ESP32_DUAL_CDC_FULL_RE", None)
    for identity in (
        "heltec_v4_2_v4_3_companion_radio_full_femon",
        "RAK_3112_companion_radio_full",
        "heltec_rc32_companion_radio_full",
        "heltec_rc32_without_display_companion_radio_full",
    ):
        assert esp32_dual_cdc is None or not esp32_dual_cdc.match(identity)

    release_files = {
        "RAK_4631_companion_radio_full": [Path("rak-full.zip")],
        "Heltec_t096_companion_radio_ble_femon": [Path("t096.zip")],
        "heltec_v4_2_v4_3_companion_radio_full_femon": [Path("v4.bin")],
        "Station_G2_repeater_observer_mqtt-full-usb-wifi-ota": [
            Path("g2.bin")
        ],
    }
    assert provider.resolve_release_identity(
        "RAK_4631_companion_radio_usb", release_files
    ) == ("RAK_4631_companion_radio_full", False)
    assert provider.resolve_release_identity(
        "RAK_4631_companion_radio_usb-logging", release_files
    ) == ("RAK_4631_companion_radio_full", False)
    assert provider.resolve_release_identity(
        "Heltec_t096_companion_radio_ble_ps_femoff", release_files
    ) == ("Heltec_t096_companion_radio_ble_femon", False)
    assert provider.resolve_release_identity(
        "heltec_v4_3_companion_radio_ble_femoff", release_files
    ) == ("heltec_v4_2_v4_3_companion_radio_full_femon", False)
    assert provider.resolve_release_identity(
        "heltec_v4_companion_radio_usb-logging", release_files
    ) == ("heltec_v4_2_v4_3_companion_radio_full_femon", False)
    assert provider.resolve_release_identity(
        "heltec_v4_companion_radio_wifi_femon", release_files
    ) == ("heltec_v4_2_v4_3_companion_radio_full_femon", False)
    assert provider.resolve_release_identity(
        "Station_G2_logging_repeater-logging", release_files
    ) == ("Station_G2_repeater_observer_mqtt-full-usb-wifi-ota", True)

    # ESP32 transport-specific artifacts remain preferred when they exist.
    release_files["Station_G2_companion_radio_usb"] = [Path("g2-usb.bin")]
    release_files["Station_G2_companion_radio_full"] = [Path("g2-full.bin")]
    assert provider.resolve_release_identity(
        "Station_G2_companion_radio_usb", release_files
    ) == ("Station_G2_companion_radio_usb", False)

    # When the canonical release contains only a Full image, every ordinary
    # attached transport and the old USB-logging identity resolve to it. A
    # single-TTY USB-UART board uses terminal mode for its logging stream.
    g2_full_only = {
        "Station_G2_companion_radio_full": [Path("g2-full.bin")],
        "ThinkNode_M2_companion_radio_full": [Path("m2-full.bin")],
    }
    for old_identity in (
        "Station_G2_companion_radio_usb",
        "Station_G2_companion_radio_ble",
        "Station_G2_companion_radio_wifi",
        "Station_G2_companion_radio_usb-logging",
    ):
        assert provider.resolve_release_identity(
            old_identity, g2_full_only
        ) == ("Station_G2_companion_radio_full", False)
    for old_identity in (
        "ThinkNode_M2_companion_radio_usb",
        "ThinkNode_M2_companion_radio_ble",
        "ThinkNode_M2_companion_radio_wifi",
        "ThinkNode_M2_companion_radio_usb-logging",
    ):
        assert provider.resolve_release_identity(
            old_identity, g2_full_only
        ) == ("ThinkNode_M2_companion_radio_full", False)

    dual_notes = provider.normalize_nrf52_full_companion_metadata(
        {"title": "Companion USB", "subTitle": "USB logging"},
        "PROFILE - old profile\n\nLOGGING USE - old use\n\nSELECTION - USB.",
    )
    assert "interface 00" in dual_notes
    assert "interface 02" in dual_notes
    assert "Input received on interface 02 is ignored" in dual_notes

    v4_notes = provider.normalize_esp32_full_companion_metadata(
        {"title": "Companion USB", "subTitle": "USB logging"},
        "PROFILE - old profile\n\nLOGGING USE - old use\n\nSELECTION - USB.",
    )
    assert "input-capable plaintext" in v4_notes
    assert "set usb.logging off" in v4_notes
    assert "remains in the normal ASCII terminal" in v4_notes
    assert "normal terminal stop token" in v4_notes
    assert "valid framed probe" in v4_notes
    assert "do not share the single TTY" in v4_notes
    assert "interface 02" not in v4_notes

    single_tty_notes = provider.normalize_esp32_full_companion_metadata(
        {"title": "Companion USB", "subTitle": "USB logging"},
        "PROFILE - old profile\n\nLOGGING USE - old use\n\nSELECTION - USB.",
    )
    assert "input-capable plaintext" in single_tty_notes
    assert "set usb.logging off" in single_tty_notes
    assert "remains in the normal ASCII terminal" in single_tty_notes
    assert "normal terminal stop token" in single_tty_notes
    assert "valid framed probe" in single_tty_notes
    assert "do not share the single TTY" in single_tty_notes

    legacy = {
        "role": "companionBle",
        "title": "Companion BLE",
        "subTitle": "FEM off",
        "version": {
            "old": {
                "notes": "old",
                "files": [{"name": "Board_companion_radio_ble-v1.2.3.zip"}],
            }
        },
    }
    canonical = {
        "role": "companionBle",
        "title": "Companion BLE",
        "version": {
            "new": {
                "notes": "new",
                "files": [{"name": "Board_companion_radio_ble-v1.2.3.zip"}],
            }
        },
    }
    catalog = {"device": [{"firmware": [legacy, canonical]}]}
    assert provider.deduplicate_resolved_firmware(
        catalog, {id(legacy): 8, id(canonical): 0}
    ) == 1
    assert catalog["device"][0]["firmware"] == [canonical]


def test_release_catalog_resolves_legacy_companions_to_plain_full():
    root = Path(__file__).resolve().parents[2]

    def load_module(name, relative_path):
        spec = importlib.util.spec_from_file_location(name, root / relative_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    provider = load_module(
        "meshcore_provider_plain_full_test",
        "mesh-america/update-provider-release.py",
    )
    logging_provider = load_module(
        "meshcore_logging_provider_plain_full_test",
        "mesh-america/update-logging-provider-release.py",
    )

    release_files = {
        identity: [Path(f"{index}.bin")]
        for index, identity in enumerate(
            (
                "Station_G2_companion_radio_full",
                "ThinkNode_M2_companion_radio_full",
                "RAK_4631_companion_radio_full",
                "Heltec_v2_companion_radio_full",
                "heltec_tracker_v2_companion_radio_full_femon",
                "Heltec_v3_companion_radio_full",
                "heltec_v4_2_v4_3_companion_radio_full_femon",
                "Generic_ESPNOW_companion_radio_full",
                "Heltec_E290_companion_radio_full",
                "Heltec_T190_companion_radio_full_",
                "SenseCapIndicator-ESPNow_companion_radio_full",
                "SenseCapIndicator-LoRa_companion_radio_full",
            )
        )
    }
    migrations = {
        # Ordinary transport artifacts can carry either release qualifier.
        "Station_G2_companion_radio_usb-ota": (
            "Station_G2_companion_radio_full", False
        ),
        "Station_G2_companion_radio_ble": (
            "Station_G2_companion_radio_full", False
        ),
        "Station_G2_companion_radio_wifi-logging": (
            "Station_G2_companion_radio_full", False
        ),
        "ThinkNode_M2_companion_radio_serial-logging": (
            "ThinkNode_M2_companion_radio_full", False
        ),
        "RAK_4631_companion_radio_ethernet-logging": (
            "RAK_4631_companion_radio_full", False
        ),
        "Heltec_v2_companion_radio_wifi-full-logging-ota": (
            "Heltec_v2_companion_radio_full", False
        ),
        "heltec_tracker_v2_companion_radio_usb_femoff-ota": (
            "heltec_tracker_v2_companion_radio_full_femon", False
        ),
        # Direct MQTT observers become the MQTT-capable Full image and must
        # retain their observer metadata in both catalogs.
        "Heltec_v3_companion_radio_wifi_mqtt-ota": (
            "Heltec_v3_companion_radio_full", True
        ),
        "heltec_v4_companion_radio_wifi_mqtt_femon-ota": (
            "heltec_v4_2_v4_3_companion_radio_full_femon", True
        ),
        "heltec_v4_3_companion_radio_wifi_mqtt_femoff-ota": (
            "heltec_v4_2_v4_3_companion_radio_full_femon", True
        ),
        # Nonstandard and combined legacy recipe names use explicit aliases.
        "Generic_ESPNOW_comp_radio_usb-ota": (
            "Generic_ESPNOW_companion_radio_full", False
        ),
        "Generic_ESPNOW_comp_radio_usb-logging": (
            "Generic_ESPNOW_companion_radio_full", False
        ),
        "Heltec_E290_companion_usb-ota": (
            "Heltec_E290_companion_radio_full", False
        ),
        "Heltec_E290_companion_ble": (
            "Heltec_E290_companion_radio_full", False
        ),
        "Heltec_E290_companion_usb_ble-logging": (
            "Heltec_E290_companion_radio_full", False
        ),
        "Heltec_T190_companion_radio_usb_-ota": (
            "Heltec_T190_companion_radio_full_", False
        ),
        "Heltec_T190_companion_radio_ble_": (
            "Heltec_T190_companion_radio_full_", False
        ),
        "Heltec_T190_companion_radio_usb_ble_-logging": (
            "Heltec_T190_companion_radio_full_", False
        ),
        "SenseCapIndicator-ESPNow_comp_radio_usb-ota": (
            "SenseCapIndicator-ESPNow_companion_radio_full", False
        ),
        "SenseCapIndicator-ESPNow_comp_radio_usb-logging": (
            "SenseCapIndicator-ESPNow_companion_radio_full", False
        ),
        "SenseCapIndicator-LoRa_comp_radio_usb_wifi-logging": (
            "SenseCapIndicator-LoRa_companion_radio_full", False
        ),
    }

    # The logging updater intentionally imports this resolver from the main
    # updater. Exercise both loaded modules so a future local copy cannot drift.
    for resolver_owner in (provider, logging_provider.common):
        for old_identity, expected in migrations.items():
            assert resolver_owner.resolve_release_identity(
                old_identity, release_files
            ) == expected


def test_ota_target_generation_honors_explicit_disable():
    from gen_targets import ota_envs, release_aliases

    cfg = [
        ["env:enabled", [["build_flags", ["-D ENABLE_OTA=1"]]]],
        ["env:disabled", [["build_flags", ["-D ENABLE_OTA=1", "-D DISABLE_LORA_OTA=1"]]]],
        ["env:unrelated", [["build_flags", ["-D NRF52_PLATFORM"]]]],
    ]
    assert ota_envs(cfg) == ["enabled"]
    assert "RAK_3401_repeater_lora_ota_no_external_sensors" in release_aliases()


def test_rak_nrf52_ota_profiles_keep_ina_and_gps_where_uart_is_available():
    root = Path(__file__).resolve().parents[2]

    def env_section(path, name):
        text = path.read_text(encoding="utf-8")
        return text.split(f"[env:{name}]", 1)[1].split("\n[", 1)[0]

    rak4631_path = root / "variants/rak4631/platformio.ini"
    gps_profiles = (
        env_section(rak4631_path, "RAK_4631_repeater_lora_ota_no_external_sensors"),
        env_section(
            rak4631_path,
            "RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors",
        ),
        env_section(
            root / "variants/rak3401/platformio.ini",
            "RAK_3401_repeater_lora_ota_no_external_sensors",
        ),
    )
    for profile in gps_profiles:
        assert "${nrf52_reduced_sensors_keep_ina_gps.build_flags}" in profile
        assert "${nrf52_reduced_sensors_keep_ina_gps.lib_deps}" in profile
        assert "SparkFun u-blox GNSS Arduino Library" in profile

    serial1_bridge = env_section(
        rak4631_path,
        "RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors",
    )
    assert "${nrf52_reduced_sensors_keep_ina.build_flags}" in serial1_bridge
    assert "${nrf52_reduced_sensors_keep_ina.lib_deps}" in serial1_bridge
    assert "SparkFun u-blox GNSS Arduino Library" not in serial1_bridge
    assert "WITH_RS232_BRIDGE=Serial1" in serial1_bridge


def test_hardware_id_for_env():
    assert ml.hardware_id_for_env("RAK_4631_repeater") == "RAK4631"
    assert ml.hardware_id_for_env("RAK_4631_companion_radio_usb") == "RAK4631"
    assert (
        ml.hardware_id_for_env("RAK_4631_repeater_rak15001_slot_c_lora_ota")
        == "RAK4631_RAK15001_C"
    )
    assert (
        ml.hardware_id_for_env("RAK_4631_repeater_w25q16_lora_ota")
        == "RAK4631_W25Q16"
    )
    assert (
        ml.hardware_id_for_env("RAK_3401_repeater_rak13302_w25q16_lora_ota")
        == "RAK3401_RAK13302_W25Q16"
    )
    assert (
        ml.hardware_id_for_env("RAK_3401_repeater_rak15001_slot_c_lora_ota")
        == "RAK_3401"
    )
    assert (
        ml.hardware_id_for_env("Heltec_t114_without_display_repeater")
        == "Heltec_t114"
    )
    assert ml.hardware_id_for_env("ThinkNode_M2_Repeater_bridge_espnow") == "ThinkNode_M2"
    assert ml.hardware_id_for_env("wio-e5-repeater_bridge_rs232") == "wio-e5"
    long_env = "ikoka_handheld_nrf_e22_30dbm_096_rotated_room_server"
    tag = ml.hardware_id_for_env(long_env)
    assert len(tag) <= 32 and tag.startswith("ikoka_handheld_nrf_e22")
    assert tag == ml.hardware_id_for_env(long_env.replace("room_server", "companion_radio_usb"))
    assert tag != ml.hardware_id_for_env("ikoka_handheld_nrf_e22_22dbm_096_rotated_room_server")


def test_wisblock_w25q16_profiles_are_exactly_bound():
    root = Path(__file__).resolve().parents[2]
    common = (root / "platformio.ini").read_text(encoding="utf-8")
    recipe = common.split("[nrf52_wisblock_w25q16_ota]", 1)[1].split("\n[", 1)[0]
    assert "OTA_QSPI_EXPECTED_JEDEC_ID=0xEF4015UL" in recipe
    assert "OTA_QSPI_EXPECTED_SIZE=2097152UL" in recipe
    assert "OTA_QSPI_SHARED_WISBLOCK_SPI=1" in recipe
    assert "OTA_QSPI_SCK_ARDUINO_PIN=3" in recipe
    assert "OTA_QSPI_CS_ARDUINO_PIN=31" in recipe
    assert "OTA_QSPI_IO0_ARDUINO_PIN=30" in recipe
    assert "OTA_QSPI_IO1_ARDUINO_PIN=29" in recipe
    assert "OTA_QSPI_IO2_NOT_CONNECTED=1" in recipe
    assert "OTA_QSPI_IO3_NOT_CONNECTED=1" in recipe

    variant = (root / "variants/rak4631/platformio.ini").read_text(encoding="utf-8")
    profile = variant.split(
        "[env:RAK_4631_repeater_w25q16_lora_ota]", 1
    )[1].split("\n[", 1)[0]
    assert "${nrf52_wisblock_w25q16_ota.build_flags}" in profile
    assert "MOTA_HW_ID='\"RAK4631_W25Q16\"'" in profile
    assert "OTA_FLASH_STORE=1" in profile

    rak3401 = (root / "variants/rak3401/platformio.ini").read_text(encoding="utf-8")
    profile = rak3401.split(
        "[env:RAK_3401_repeater_rak13302_w25q16_lora_ota]", 1
    )[1].split("\n[", 1)[0]
    assert "${nrf52_wisblock_w25q16_ota.build_flags}" in profile
    assert "OTA_QSPI_RAK3401_RADIO_BUS_HANDOFF=1" in profile
    assert "MOTA_HW_ID='\"RAK3401_RAK13302_W25Q16\"'" in profile
    assert "ENABLE_OTA=1" in profile


# --- EndF ------------------------------------------------------------------

def test_endf_roundtrip_and_idempotent():
    body = _fw(1, 5000)
    img, h8 = ml.ensure_endf(body)
    assert len(img) == 5000 + ml.ENDF_LEN
    assert ml.has_endf(img)
    pbody, ph8 = ml.parse_endf(img)
    assert pbody == body and ph8 == h8 == ml.mh8(body)
    # idempotent: feeding an already-EndF'd image returns it unchanged
    img2, h82 = ml.ensure_endf(img)
    assert img2 == img and h82 == h8


def test_endf_rejects_garbage_tail():
    assert not ml.has_endf(b"too short")
    body = _fw(2, 1000)
    img = body + ml.ENDF_MAGIC + struct.pack("<I", 999) + ml.mh8(body)  # wrong body_len
    assert not ml.has_endf(img)


def test_endf_identity():
    body = _fw(3, 4096)
    ident = ml.FwIdent(fw_version=ml.pack_version("1.16.0"),
                       target_id=ml.target_id_for_env("RAK_4631_repeater"), hw_id="RAK4631")
    img, h8 = ml.ensure_endf(body, ident)
    assert len(img) == len(body) + ml.ENDF_LEN              # fixed 56-byte trailer
    assert ml.parse_endf(img) == (body, h8)                 # body + body_hash parse
    assert h8 == ml.mh8(body)                               # body_hash is over BODY only
    gi = ml.parse_endf_ident(img)
    assert gi is not None and gi.hw_id == "RAK4631"
    assert gi.target_id == ml.target_id_for_env("RAK_4631_repeater")
    assert gi.fw_version == ml.pack_version("1.16.0")
    # no identity supplied -> zero-filled (still fixed size, still self-consistent)
    z, _ = ml.ensure_endf(body)
    assert len(z) == len(body) + ml.ENDF_LEN and ml.parse_endf_ident(z) == ml.FwIdent(0, 0, "")


def test_nrf52_layout_record_roundtrip_and_policy():
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_APP_END, True)
            == ml.NRF52_EXTRAFS_START)
    assert ml.nrf52_stage_ceiling_for_layout(ml.NRF52_APP_END, False) == ml.NRF52_APP_END
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_EXTRAFS_START, True)
            == ml.NRF52_EXTRAFS_START)
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_EXTRAFS_START, False)
            == ml.NRF52_APP_END)
    assert (ml.nrf52_stage_ceiling_for_layout(ml.NRF52_BOOT_SCRATCH_START, False)
            == ml.NRF52_APP_END)

    layout = ml.Nrf52Layout(ml.NRF52_APP_BASE_S140_V7, ml.NRF52_EXTRAFS_START,
                            ml.NRF52_APP_END, 0)
    body = ml.ensure_nrf52_layout(_fw(4, 2048), layout)
    image, _ = ml.ensure_endf(body, ml.FwIdent(hw_id="Xiao_nrf52"))
    assert ml.parse_nrf52_layout(image) == layout
    # Re-running the record step replaces the tail instead of duplicating it.
    assert ml.ensure_nrf52_layout(body, layout) == body
    assert ml.parse_nrf52_layout(ml.ensure_endf(_fw(5, 2048))[0]) is None
    internal = ml.Nrf52Layout(
        ml.NRF52_APP_BASE_S140_V7, ml.NRF52_EXTRAFS_START,
        ml.NRF52_EXTRAFS_START, ml.NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS)
    internal_image, _ = ml.ensure_endf(ml.ensure_nrf52_layout(_fw(6, 2048), internal))
    assert ml.parse_nrf52_layout(internal_image) == internal
    qspi = ml.Nrf52Layout(
        ml.NRF52_APP_BASE_S140_V7, ml.NRF52_APP_END,
        ml.NRF52_APP_END, ml.NRF52_LAYOUT_FLAG_QSPI)
    qspi_image, _ = ml.ensure_endf(ml.ensure_nrf52_layout(_fw(7, 2048), qspi))
    assert ml.parse_nrf52_layout(qspi_image) == qspi
    assert qspi.qspi_backed and qspi.external_backed and not qspi.sd_backed
    boot_qspi = ml.Nrf52Layout(
        ml.NRF52_APP_BASE_S140_V7, ml.NRF52_BOOT_SCRATCH_START,
        ml.NRF52_APP_END,
        ml.NRF52_LAYOUT_FLAG_QSPI | ml.NRF52_LAYOUT_FLAG_BOOTLOADER_SCRATCH)
    boot_qspi_image, _ = ml.ensure_endf(ml.ensure_nrf52_layout(_fw(8, 2048), boot_qspi))
    assert ml.parse_nrf52_layout(boot_qspi_image) == boot_qspi
    assert boot_qspi.bootloader_scratch
    shared_internal = ml.Nrf52Layout(
        ml.NRF52_APP_BASE_S140_V6, ml.NRF52_APP_END,
        ml.NRF52_APP_END, 0)
    shared_internal_image, _ = ml.ensure_endf(
        ml.ensure_nrf52_layout(_fw(9, 2048), shared_internal))
    assert ml.parse_nrf52_layout(shared_internal_image) == shared_internal
    assert not shared_internal.bootloader_scratch
    assert ((ml.NRF52_APP_END - ml.NRF52_BOOT_CONTAINER_SIZE) &
            ~(ml.NRF52_FLASH_PAGE - 1)) == ml.NRF52_SHARED_BOOT_STAGE_START
    try:
        ml.build_nrf52_layout(ml.Nrf52Layout(
            ml.NRF52_APP_BASE_S140_V7, ml.NRF52_EXTRAFS_START,
            ml.NRF52_EXTRAFS_START, 0))
        assert False, "inconsistent layout record accepted"
    except ValueError:
        pass
    for flags in (
        ml.NRF52_LAYOUT_FLAG_SD | ml.NRF52_LAYOUT_FLAG_QSPI,
        ml.NRF52_LAYOUT_FLAG_QSPI | ml.NRF52_LAYOUT_FLAG_INTERNAL_EXTRAFS,
        0x10,
    ):
        try:
            ml.build_nrf52_layout(ml.Nrf52Layout(
                ml.NRF52_APP_BASE_S140_V7, ml.NRF52_APP_END,
                ml.NRF52_APP_END, flags))
            assert False, "conflicting nRF52 layout flags accepted"
        except ValueError:
            pass


def _write_boot_continuity(image, offset, *, version=0x0117010D,
                           family=ml.BOOT_CONTINUITY_FAMILY_S140,
                           fwid=None, app_base=ml.NRF52_APP_BASE_S140_V7,
                           layout_abi=ml.BOOT_CONTINUITY_LAYOUT_ABI):
    if fwid is None:
        fwid = 0x0123 if app_base == ml.NRF52_APP_BASE_S140_V7 else 0x00B6
    struct.pack_into("<8sHHIHHIHHI", image, offset + ml.XIAO_BOOT_MANIFEST_SIZE,
                     ml.BOOT_CONTINUITY_MAGIC, ml.BOOT_CONTINUITY_VERSION,
                     ml.BOOT_CONTINUITY_SIZE, version,
                     family, fwid, app_base, layout_abi, 0, 0)


def _xiao_bootloader_image(board_id=ml.XIAO_BOOT_BOARD_ID_BASE,
                           boot_version=0x0117010D):
    import zlib
    image = bytearray(b"\xff" * ml.XIAO_BOOT_IMAGE_SIZE)
    struct.pack_into("<II", image, 0, 0x20040000, ml.XIAO_BOOT_IMAGE_START + 0x101)
    struct.pack_into("<8sHHB3x", image, 0x80, ml.XIAO_BOOT_CAPS_MAGIC,
                     ml.BOOT_FORMAT_VER, ml.BOOT_REQUIRED_APP_CODEC_MASK,
                     ml.BOOT_STORAGE_QSPI_UPDATE)
    name = ml.XIAO_BOOT_DEVICE_NAME
    off = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
    struct.pack_into("<8sHHIII16sI", image, off, ml.XIAO_BOOT_MANIFEST_MAGIC,
                     ml.XIAO_BOOT_MANIFEST_VERSION, ml.XIAO_BOOT_MANIFEST_SIZE,
                     ml.XIAO_BOOT_IMAGE_START, ml.XIAO_BOOT_IMAGE_SIZE, board_id, name, 0)
    _write_boot_continuity(image, off, version=boot_version)
    crc = zlib.crc32(image) & 0xFFFFFFFF
    struct.pack_into("<I", image, off + 40, crc)
    return bytes(image)


def _generic_bootloader_image(board_id=0x239A0029, device_name="3401_DFU",
                              storage=ml.BOOT_STORAGE_INTERNAL_UPDATE,
                              boot_version=0x0117010D):
    import zlib
    image = bytearray(b"\xff" * ml.XIAO_BOOT_IMAGE_SIZE)
    struct.pack_into("<II", image, 0, 0x20040000, ml.XIAO_BOOT_IMAGE_START + 0x101)
    struct.pack_into("<8sHHB3x", image, 0x80, ml.XIAO_BOOT_CAPS_MAGIC,
                     ml.BOOT_FORMAT_VER, ml.BOOT_REQUIRED_APP_CODEC_MASK,
                     storage)
    name = device_name.encode("ascii").ljust(16, b"\0")
    off = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
    struct.pack_into("<8sHHIII16sI", image, off, ml.XIAO_BOOT_MANIFEST_MAGIC,
                     ml.XIAO_BOOT_MANIFEST_VERSION, ml.XIAO_BOOT_MANIFEST_SIZE,
                     ml.XIAO_BOOT_IMAGE_START, ml.XIAO_BOOT_IMAGE_SIZE, board_id, name, 0)
    _write_boot_continuity(image, off, version=boot_version,
                           app_base=ml.NRF52_APP_BASE_S140_V6)
    struct.pack_into("<I", image, off + 40, zlib.crc32(image) & 0xFFFFFFFF)
    return bytes(image)


def _rewrite_boot_image(image, mutate):
    import zlib
    rewritten = bytearray(image)
    mutate(rewritten)
    off = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
    rewritten[off + 40:off + 44] = b"\0" * 4
    struct.pack_into("<I", rewritten, off + 40,
                     zlib.crc32(rewritten) & 0xFFFFFFFF)
    return bytes(rewritten)


def _xiao_manifest_crc(image, offset):
    import zlib
    trial = bytearray(image)
    trial[offset + 40:offset + 44] = b"\0" * 4
    return zlib.crc32(trial) & 0xFFFFFFFF


def _set_two_valid_xiao_manifest_crcs(image, first, second):
    """Solve the two coupled whole-image CRC fields over GF(2)."""
    first_crc, second_crc = first + 40, second + 40
    image[first_crc:first_crc + 4] = b"\0" * 4
    image[second_crc:second_crc + 4] = b"\0" * 4

    def residual(value):
        trial = bytearray(image)
        struct.pack_into("<I", trial, first_crc, value & 0xFFFFFFFF)
        struct.pack_into("<I", trial, second_crc, value >> 32)
        first_error = struct.unpack_from("<I", trial, first_crc)[0] ^ _xiao_manifest_crc(trial, first)
        second_error = struct.unpack_from("<I", trial, second_crc)[0] ^ _xiao_manifest_crc(trial, second)
        return first_error | (second_error << 32)

    affine = residual(0)
    columns = [residual(1 << bit) ^ affine for bit in range(64)]
    rows = []
    for output_bit in range(64):
        row = sum(1 << input_bit for input_bit, column in enumerate(columns)
                  if (column >> output_bit) & 1)
        rows.append(row | (((affine >> output_bit) & 1) << 64))

    pivot_columns = []
    pivot_row = 0
    for column in range(64):
        found = next((row for row in range(pivot_row, 64)
                      if (rows[row] >> column) & 1), None)
        if found is None:
            continue
        rows[pivot_row], rows[found] = rows[found], rows[pivot_row]
        for row in range(64):
            if row != pivot_row and (rows[row] >> column) & 1:
                rows[row] ^= rows[pivot_row]
        pivot_columns.append(column)
        pivot_row += 1

    assert all((row & ((1 << 64) - 1)) or not ((row >> 64) & 1) for row in rows)
    solution = 0
    for row, column in enumerate(pivot_columns):
        if (rows[row] >> 64) & 1:
            solution |= 1 << column
    assert residual(solution) == 0
    struct.pack_into("<I", image, first_crc, solution & 0xFFFFFFFF)
    struct.pack_into("<I", image, second_crc, solution >> 32)


def test_xiao_bootloader_identity_skips_bad_decoy_and_rejects_two_valid_manifests():
    real_offset = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
    decoy_offset, second_offset = 0x20, 0x200
    image = bytearray(_xiao_bootloader_image())
    image[decoy_offset:decoy_offset + ml.XIAO_BOOT_MANIFEST_SIZE] = \
        image[real_offset:real_offset + ml.XIAO_BOOT_MANIFEST_SIZE]
    struct.pack_into("<I", image, decoy_offset + 40, 0xA5A5A5A5)
    struct.pack_into("<I", image, real_offset + 40, _xiao_manifest_crc(image, real_offset))
    assert _xiao_manifest_crc(image, decoy_offset) != 0xA5A5A5A5
    identity = ml.parse_xiao_bootloader_identity(bytes(image))
    assert identity is not None and identity.manifest_offset == real_offset

    duplicate = bytearray(_xiao_bootloader_image())
    duplicate[second_offset:second_offset + ml.XIAO_BOOT_MANIFEST_SIZE] = \
        duplicate[real_offset:real_offset + ml.XIAO_BOOT_MANIFEST_SIZE]
    _set_two_valid_xiao_manifest_crcs(duplicate, real_offset, second_offset)
    assert struct.unpack_from("<I", duplicate, real_offset + 40)[0] == \
        _xiao_manifest_crc(duplicate, real_offset)
    assert struct.unpack_from("<I", duplicate, second_offset + 40)[0] == \
        _xiao_manifest_crc(duplicate, second_offset)
    assert ml.parse_xiao_bootloader_identity(bytes(duplicate)) is None
    try:
        ml.validate_xiao_bootloader_image(bytes(duplicate))
        assert False, "two CRC-valid embedded identities accepted"
    except ValueError:
        pass

    # Base identity counting happens before adjacent continuity parsing. A
    # CRC-valid decoy cannot hide merely by claiming one corrupt BLM2 magic;
    # this must match deployed legacy-updater duplicate semantics.
    corrupt_extension = bytearray(_xiao_bootloader_image())
    corrupt_extension[second_offset:second_offset + ml.XIAO_BOOT_MANIFEST_SIZE] = \
        corrupt_extension[real_offset:real_offset + ml.XIAO_BOOT_MANIFEST_SIZE]
    corrupt_extension[second_offset + ml.XIAO_BOOT_MANIFEST_SIZE:
                      second_offset + ml.XIAO_BOOT_MANIFEST_SIZE + 8] = b"BLM2BAD!"
    _set_two_valid_xiao_manifest_crcs(corrupt_extension, real_offset, second_offset)
    assert ml.parse_xiao_bootloader_identity(bytes(corrupt_extension)) is None
    try:
        ml.validate_xiao_bootloader_image(bytes(corrupt_extension))
        assert False, "CRC-valid identity with corrupt BLM2 decoy was hidden"
    except ValueError:
        pass

    # The same malformed extension does not matter when the associated base
    # CRC is invalid; the one canonical CRC-valid identity remains usable.
    bad_crc_extension = bytearray(_xiao_bootloader_image())
    bad_crc_extension[second_offset:second_offset + ml.XIAO_BOOT_MANIFEST_SIZE] = \
        bad_crc_extension[real_offset:real_offset + ml.XIAO_BOOT_MANIFEST_SIZE]
    bad_crc_extension[second_offset + ml.XIAO_BOOT_MANIFEST_SIZE:
                      second_offset + ml.XIAO_BOOT_MANIFEST_SIZE + 8] = b"BLM2BAD!"
    struct.pack_into("<I", bad_crc_extension, second_offset + 40, 0xA5A5A5A5)
    struct.pack_into("<I", bad_crc_extension, real_offset + 40,
                     _xiao_manifest_crc(bad_crc_extension, real_offset))
    assert _xiao_manifest_crc(bad_crc_extension, second_offset) != 0xA5A5A5A5
    parsed = ml.parse_xiao_bootloader_identity(bytes(bad_crc_extension))
    assert parsed is not None and parsed.manifest_offset == real_offset
    ml.validate_xiao_bootloader_image(bytes(bad_crc_extension))


def test_xiao_bootloader_caps_rejects_malformed_or_unaligned_markers():
    def caps(*, offset=0, abi=ml.BOOT_FORMAT_VER,
             codecs=ml.BOOT_REQUIRED_APP_CODEC_MASK,
             storage=ml.BOOT_STORAGE_QSPI_UPDATE,
             reserved=b"\0\0\0"):
        image = bytearray(b"\xff" * 64)
        struct.pack_into("<8sHHB3s", image, offset, ml.XIAO_BOOT_CAPS_MAGIC,
                         abi, codecs, storage, reserved)
        return bytes(image)

    assert ml.xiao_bootloader_caps_ok(caps())
    assert not ml.xiao_bootloader_caps_ok(caps(offset=1))
    assert not ml.xiao_bootloader_caps_ok(caps(abi=0xFFFF))
    assert not ml.xiao_bootloader_caps_ok(caps(codecs=0))
    assert not ml.xiao_bootloader_caps_ok(caps(codecs=1 << ml.CODEC_FULL))
    assert not ml.xiao_bootloader_caps_ok(caps(storage=0x1C))
    assert not ml.xiao_bootloader_caps_ok(caps(reserved=b"\0\x01\0"))
    duplicate = bytearray(caps())
    struct.pack_into("<8sHHB3s", duplicate, 24, ml.XIAO_BOOT_CAPS_MAGIC,
                     ml.BOOT_FORMAT_VER, ml.BOOT_REQUIRED_APP_CODEC_MASK,
                     ml.BOOT_STORAGE_INTERNAL_UPDATE, b"\0\0\0")
    assert ml.bootloader_caps_storage(bytes(duplicate)) is None
    privileged_ambiguous = bytearray(caps())
    struct.pack_into("<8sHHB3s", privileged_ambiguous, 24,
                     ml.XIAO_BOOT_CAPS_MAGIC, ml.BOOT_FORMAT_VER,
                     ml.BOOT_REQUIRED_APP_CODEC_MASK,
                     ml.XIAO_BOOT_STORAGE_UPDATE, b"\0\0\0")
    assert ml.bootloader_caps_storage(bytes(privileged_ambiguous)) is None
    malformed_decoy = bytearray(caps())
    struct.pack_into("<8sHHB3s", malformed_decoy, 24,
                     ml.XIAO_BOOT_CAPS_MAGIC, ml.BOOT_FORMAT_VER,
                     ml.BOOT_REQUIRED_APP_CODEC_MASK, 0x1C, b"\0\0\0")
    assert ml.bootloader_caps_storage(bytes(malformed_decoy)) == \
           ml.BOOT_STORAGE_QSPI_UPDATE


def test_bootloader_v3_build_parse_and_strict_contract():
    priv = Ed25519PrivateKey.generate()
    image = _xiao_bootloader_image()
    identity = ml.validate_xiao_bootloader_image(image, ml.XIAO_BOOT_BOARD_ID_BASE)
    assert identity.board_id == ml.XIAO_BOOT_BOARD_ID_BASE
    m = ml.build_manifest(
        target_id=identity.board_id, fw_version=identity.boot_version,
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True,
        sign_priv=priv, bootloader=True)
    blob = ml.build_container(m, image)
    parsed = ml.parse_container(blob)
    assert parsed.manifest.format_ver == ml.BOOT_FORMAT_VER
    assert parsed.manifest.flags == ml.FLAG_FULL | ml.FLAG_SIGNED | ml.FLAG_BOOTLOADER
    assert parsed.manifest.hw_id == ml.hw_id_bytes("XIAO_BL_28860044")
    assert ml.verify(parsed, expect_pub=ml.ed25519_public_bytes(priv)) == []

    invalid_payload = bytearray(parsed.payload)
    invalid_payload[4] &= 0xFE
    parsed.payload = bytes(invalid_payload)
    assert any(problem.startswith("bootloader image contract:")
               for problem in ml.verify(parsed))

    bad = bytearray(blob)
    bad[8] = ml.APP_FORMAT_VER
    try:
        ml.parse_container(bytes(bad))
        assert False, "v2 bootloader flag accepted"
    except ValueError:
        pass
    bad = bytearray(blob)
    bad[9] &= ~ml.FLAG_BOOTLOADER
    try:
        ml.parse_container(bytes(bad))
        assert False, "v3 non-bootloader flags accepted"
    except ValueError:
        pass


def test_generic_internal_bootloader_build_parse_and_strict_contract():
    priv = Ed25519PrivateKey.generate()
    image = _generic_bootloader_image()
    identity = ml.validate_bootloader_image(image)
    assert identity.board_id == 0x239A0029 and identity.device_name == "3401_DFU"
    target = ml.bootloader_target_id(identity.board_id, identity.device_name)
    assert target == 0x23818A80
    expected_hw = b"NRF_BL_239A0029_3401_DFU".ljust(32, b"\0")
    assert ml.bootloader_hw_id(identity.board_id, identity.device_name) == expected_hw

    manifest = ml.build_manifest(
        target_id=target, fw_version=identity.boot_version,
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True,
        sign_priv=priv, bootloader=True)
    parsed = ml.parse_container(ml.build_container(manifest, image))
    assert parsed.manifest.target_id == target
    assert parsed.manifest.hw_id == expected_hw
    assert ml.verify(parsed, expect_pub=ml.ed25519_public_bytes(priv)) == []

    wrong_target = target ^ 1
    try:
        ml.build_manifest(
            target_id=wrong_target, fw_version=1, image_size=len(image), payload=image,
            block_size=1024, image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL,
            is_full=True, sign_priv=priv, bootloader=True)
        assert False, "generic bootloader raw/wrong target accepted"
    except ValueError:
        pass


def test_meshtower_sd_bootloader_profile_builds_the_same_exact_identity():
    priv = Ed25519PrivateKey.generate()
    image = _generic_bootloader_image(
        board_id=0x239A0071, device_name="TOWER_V2_OTA",
        storage=ml.BOOT_STORAGE_SD_UPDATE)
    identity = ml.validate_bootloader_image(image)
    assert (identity.board_id, identity.device_name) in ml.SD_BOOTLOADER_IDENTITIES
    assert ml.bootloader_caps_storage(image) == 0x09
    target = ml.bootloader_target_id(identity.board_id, identity.device_name)
    assert target == 0x1150F50E
    manifest = ml.build_manifest(
        target_id=target, fw_version=identity.boot_version,
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True,
        sign_priv=priv, bootloader=True)
    parsed = ml.parse_container(ml.build_container(manifest, image))
    assert parsed.manifest.hw_id == b"NRF_BL_239A0071_TOWER_V2_OTA".ljust(32, b"\0")
    assert ml.verify(parsed, expect_pub=ml.ed25519_public_bytes(priv)) == []


def test_meshtower_sd_bootloader_build_flag_is_exact_target_only():
    root = Path(__file__).resolve().parents[2]
    text = (root / "variants/heltec_tower_v2/platformio.ini").read_text(encoding="utf-8")
    section = text.split(
        "[env:Heltec_tower_v2_sdcard_repeater_lora_ota_no_external_sensors]", 1)[1]
    section = section.split("[env:", 1)[0]
    assert "-D OTA_SD_STORE=1" in section
    assert "-D OTA_SD_BOOTLOADER_UPDATE=1" in section
    store = (root / "src/helpers/ota/OtaStoreSdNrf52.cpp").read_text(encoding="utf-8")
    assert "readSector(" not in store
    assert "writeSector(" not in store
    assert "OtaSdHandoff" not in store
    assert not (root / "src/helpers/ota/OtaSdHandoff.h").exists()


def test_bootloader_build_inventory_is_unique_and_disjoint_from_app_targets():
    import re

    expected = {
        (0x239A0071, "TOWER_V2_OTA"): 0x1150F50E,
        (0x239A0071, "T096_DFU"): 0x42354C85,
        (0x239A0071, "T1_DFU"): 0xFC556FFC,
        (0x239A0071, "T114_DFU"): 0x0C3F2902,
        (0x239A0071, "MESH_POCKET_OTA"): 0x059277F4,
        (0x239A00B3, "KeepteenLT1_OTA"): 0xDB2E7B51,
        (0x239A0029, "MX25_DFU"): 0x026AA982,
        (0x239A00B3, "PROM_DFU"): 0xAF79E8CC,
        (0x28860057, "T1KE_DFU"): 0xE6F5F03F,
        (0x239A00DA, "TNM3_DFU"): 0x0CA41DB2,
        (0x239A0029, "3401_DFU"): 0x23818A80,
        (0x239A0029, "4631_DFU"): 0x2D0DF000,
        (0x239A0029, "RTAG_DFU"): 0xC72E9C9C,
    }
    assert set(ml.INTERNAL_BOOTLOADER_IDENTITIES) == set(expected)
    for identity, target in expected.items():
        assert ml.bootloader_target_id(*identity) == target

    root = Path(__file__).resolve().parents[2]
    target_header = (root / "src/helpers/ota/OtaTargets.h").read_text(encoding="utf-8")
    app_targets = {
        int(value, 16)
        for value in re.findall(r"\{ 0x([0-9a-fA-F]{8}),", target_header)
    }
    audited = ml.audit_bootloader_target_inventory(app_targets)
    assert len(audited) == len(expected) + 2  # generic internal plus two legacy XIAOs

    # Generic parsing remains useful for inspecting a future candidate, but a
    # signing/build call fails closed until its exact identity is qualified.
    image = _generic_bootloader_image(device_name="UNKNOWN_DFU")
    identity = ml.validate_bootloader_image(image)
    target = ml.bootloader_target_id(identity.board_id, identity.device_name)
    priv = Ed25519PrivateKey.generate()
    try:
        ml.build_manifest(
            target_id=target, fw_version=1, image_size=len(image), payload=image,
            block_size=1024, image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL,
            is_full=True, sign_priv=priv, bootloader=True)
        assert False, "unqualified generic bootloader package build accepted"
    except ValueError:
        pass


def test_qualified_bootloader_platform_and_storage_profiles_are_exact():
    qualified = (
        (ml.XIAO_BOOT_BOARD_ID_BASE, "XIAO_DFU"),
        (ml.XIAO_BOOT_BOARD_ID_SENSE, "XIAO_DFU"),
    ) + ml.INTERNAL_BOOTLOADER_IDENTITIES
    for board_id, device_name in qualified:
        family, fwid, app_base, layout_abi = \
            ml.bootloader_qualified_platform_profile(board_id, device_name)
        storages = ml.bootloader_qualified_storage_profiles(board_id, device_name)
        assert family == ml.BOOT_CONTINUITY_FAMILY_S140
        assert layout_abi == ml.BOOT_CONTINUITY_LAYOUT_ABI
        if (board_id, device_name) in ml.BOOTLOADER_S140_V7_IDENTITIES:
            assert (fwid, app_base) == (ml.BOOTLOADER_S140_V7_FWID,
                                       ml.NRF52_APP_BASE_S140_V7)
        else:
            assert (fwid, app_base) == (ml.BOOTLOADER_S140_V6_FWID,
                                       ml.NRF52_APP_BASE_S140_V6)
        for storage in storages:
            if board_id in (ml.XIAO_BOOT_BOARD_ID_BASE,
                             ml.XIAO_BOOT_BOARD_ID_SENSE):
                image = _xiao_bootloader_image(board_id)
            else:
                image = bytearray(_generic_bootloader_image(
                    board_id=board_id, device_name=device_name, storage=storage))
                off = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
                _write_boot_continuity(image, off, fwid=fwid,
                                       app_base=app_base)
                image = _rewrite_boot_image(bytes(image), lambda raw: None)
            parsed = ml.validate_bootloader_image(image)
            assert (parsed.softdevice_family, parsed.softdevice_fwid,
                    parsed.app_base, parsed.layout_abi) == \
                   (family, fwid, app_base, layout_abi)
            assert ml.bootloader_caps_storage(image) == storage

    # Every individually well-formed but wrong continuity field must fail the
    # qualified signing validator, for both v6 and v7 inventory profiles.
    for image in (_generic_bootloader_image(), _xiao_bootloader_image()):
        off = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
        for field_offset, field_format, wrong in (
            (60, "<H", 141),
            (62, "<H", 0xBEEF),
            (64, "<I", 0x28000),
            (68, "<H", 2),
        ):
            invalid = _rewrite_boot_image(
                image, lambda raw, at=field_offset, fmt=field_format, value=wrong:
                struct.pack_into(fmt, raw, off + at, value))
            try:
                ml.validate_bootloader_image(invalid)
                assert False, f"qualified image accepted wrong continuity field +{field_offset}"
            except ValueError:
                pass

    # Capability storage is part of the profile too. Tower alone admits both
    # internal and SD; QSPI is XIAO-only and non-Tower internals cannot claim SD.
    for invalid in (
        _generic_bootloader_image(storage=ml.BOOT_STORAGE_SD_UPDATE),
        _generic_bootloader_image(board_id=0x239A0071,
                                  device_name="TOWER_V2_OTA",
                                  storage=ml.BOOT_STORAGE_QSPI_UPDATE),
        _rewrite_boot_image(
            _xiao_bootloader_image(),
            lambda raw: raw.__setitem__(0x80 + 12,
                                        ml.BOOT_STORAGE_INTERNAL_UPDATE)),
    ):
        try:
            ml.validate_bootloader_image(invalid)
            assert False, "qualified identity accepted wrong storage profile"
        except ValueError:
            pass


def test_bootloader_builder_rejects_wrong_identity_geometry_and_continuity():
    priv = Ed25519PrivateKey.generate()
    image = _xiao_bootloader_image()
    kwargs = dict(target_id=ml.XIAO_BOOT_BOARD_ID_BASE,
                  fw_version=ml.validate_xiao_bootloader_image(image).boot_version,
                  image_size=len(image), payload=image, block_size=1024,
                  image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL,
                  is_full=True, sign_priv=priv, bootloader=True)
    for change in (
        {"sign_priv": None},
        {"block_size": 512},
        {"fw_version": 0},
        {"target_id": ml.XIAO_BOOT_BOARD_ID_SENSE},
        {"hw_id": "wrong"},
    ):
        try:
            ml.build_manifest(**(kwargs | change))
            assert False, f"invalid bootloader build accepted: {change}"
        except ValueError:
            pass
    damaged = bytearray(image); damaged[0x200] ^= 1
    try:
        ml.validate_xiao_bootloader_image(bytes(damaged))
        assert False, "bad embedded CRC accepted"
    except ValueError:
        pass

    missing_extension = _rewrite_boot_image(
        image, lambda raw: raw.__setitem__(
            slice(ml.BOOT_CANDIDATE_MANIFEST_OFFSET + 44,
                  ml.BOOT_CANDIDATE_MANIFEST_OFFSET + 76), b"\xff" * 32))
    half_extension = _rewrite_boot_image(
        image, lambda raw: raw.__setitem__(
            slice(ml.BOOT_CANDIDATE_MANIFEST_OFFSET + 48,
                  ml.BOOT_CANDIDATE_MANIFEST_OFFSET + 52), b"FAIL"))
    low_byte_zero = _rewrite_boot_image(
        image, lambda raw: struct.pack_into(
            "<I", raw, ml.BOOT_CANDIDATE_MANIFEST_OFFSET + 56, 0x02040100))
    all_ones = _rewrite_boot_image(
        image, lambda raw: struct.pack_into(
            "<I", raw, ml.BOOT_CANDIDATE_MANIFEST_OFFSET + 56, 0xFFFFFFFF))
    for invalid, why in (
        (missing_extension, "missing continuity extension"),
        (half_extension, "half-present continuity extension"),
        (low_byte_zero, "invalid preview zero"),
        (all_ones, "un-upgradable all-ones version"),
    ):
        try:
            ml.validate_xiao_bootloader_image(invalid)
            assert False, f"accepted {why}"
        except ValueError:
            pass

    import zlib
    wrong_offset = bytearray(image)
    canonical = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
    relocated = 0x8000
    envelope = bytes(wrong_offset[canonical:canonical + ml.BOOT_ENVELOPE_SIZE])
    wrong_offset[canonical:canonical + ml.BOOT_ENVELOPE_SIZE] = \
        b"\xff" * ml.BOOT_ENVELOPE_SIZE
    wrong_offset[relocated:relocated + ml.BOOT_ENVELOPE_SIZE] = envelope
    wrong_offset[relocated + 40:relocated + 44] = b"\0" * 4
    struct.pack_into("<I", wrong_offset, relocated + 40,
                     zlib.crc32(wrong_offset) & 0xFFFFFFFF)
    parsed_relocated = ml.parse_xiao_bootloader_identity(bytes(wrong_offset))
    assert parsed_relocated is not None and parsed_relocated.manifest_offset == relocated
    try:
        ml.validate_xiao_bootloader_image(bytes(wrong_offset))
        assert False, "otherwise-valid BLM2 envelope at a noncanonical offset accepted"
    except ValueError:
        pass

    try:
        ml.build_manifest(**(kwargs | {"fw_version": kwargs["fw_version"] + 1}))
        assert False, "outer version differing from embedded BLM2 accepted"
    except ValueError:
        pass
    wrong_name = bytearray(image)
    off = ml.BOOT_CANDIDATE_MANIFEST_OFFSET
    wrong_name[off + 24:off + 40] = b"FRIENDLY NAME".ljust(16, b"\0")
    wrong_name[off + 40:off + 44] = b"\0" * 4
    struct.pack_into("<I", wrong_name, off + 40, zlib.crc32(wrong_name) & 0xFFFFFFFF)
    try:
        ml.validate_xiao_bootloader_image(bytes(wrong_name))
        assert False, "noncanonical embedded device name accepted"
    except ValueError:
        pass


# --- merkle ----------------------------------------------------------------

def test_merkle_single_block():
    leaves = [ml.mh4(b"x")]
    assert ml.merkle_root(leaves) == leaves[0]


def test_merkle_proofs_all_indices_various_counts():
    for count in [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 100]:
        payload = _fw(count, count * 1024 - 13)  # last block short, no padding
        leaves = ml.leaf_hashes(payload, 1024)
        assert len(leaves) == count
        root = ml.merkle_root(leaves)
        for i in range(count):
            proof = ml.merkle_proof(leaves, i)
            assert ml.verify_proof(leaves[i], i, proof, root, count), (count, i)
        # a tampered leaf must fail its proof
        bad = bytes([leaves[0][0] ^ 0xFF]) + leaves[0][1:]
        assert not ml.verify_proof(bad, 0, ml.merkle_proof(leaves, 0), root, count)


# --- full container --------------------------------------------------------

def test_full_build_parse_verify():
    fw = _fw(10, 33 * 1024 + 7)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(
        target_id=0xDEADBEEF, fw_version=ml.pack_version("1.16.0"),
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True)
    blob = ml.build_container(m, image)

    parsed = ml.parse_container(blob)
    assert parsed.manifest.target_id == 0xDEADBEEF
    assert parsed.manifest.is_full and not parsed.manifest.is_signed
    assert parsed.manifest.image_hash == ml.mh32(image)
    assert parsed.payload == image
    assert ml.verify(parsed) == []


def test_hw_id_roundtrip_and_signed():
    # the v2 hw_id is a 32-byte NUL-padded ASCII tag in the SIGNED head; it must round-trip + be covered
    # by the signature (tampering it breaks verification).
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    fw = _fw(77, 4 * 1024)
    image, _ = ml.ensure_endf(fw)
    priv = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    m = ml.build_manifest(
        target_id=0xABCD, fw_version=ml.pack_version("2.0.0"),
        image_size=len(image), payload=image, block_size=1024,
        image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL, is_full=True,
        sign_priv=priv, hw_id="RAK4631")
    blob = ml.build_container(m, image)
    parsed = ml.parse_container(blob)
    assert parsed.manifest.format_ver == 2
    assert parsed.manifest.hw_id == b"RAK4631" + b"\0" * (32 - 7)
    assert parsed.manifest.hw_id.rstrip(b"\0").decode() == "RAK4631"
    assert ml.verify(parsed) == []
    # flip a byte of the on-wire hw_id -> signature must fail (it's in the signed region)
    bad = bytearray(blob)
    hw_off = 8 + 57            # MAGIC(4)+total(4) + fixed head up to codec(57) = start of hw_id
    bad[hw_off] ^= 0xFF
    assert ml.verify(ml.parse_container(bytes(bad))) != []


def test_tampered_payload_detected():
    fw = _fw(11, 10 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=1, fw_version=1, image_size=len(image), payload=image,
                          block_size=1024, image_hash=ml.mh32(image),
                          codec_id=ml.CODEC_FULL, is_full=True)
    blob = bytearray(ml.build_container(m, image))
    # flip a byte inside the payload region
    payload_off = blob.index(image)
    blob[payload_off + 50] ^= 0xFF
    problems = ml.verify(ml.parse_container(bytes(blob)))
    assert any("leaves" in p or "merkle" in p or "image_hash" in p for p in problems), problems


# --- signing ---------------------------------------------------------------

def test_signed_build_and_verify():
    priv = Ed25519PrivateKey.generate()
    fw = _fw(12, 20 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=7, fw_version=ml.pack_version("2.0.0"),
                          image_size=len(image), payload=image, block_size=1024,
                          image_hash=ml.mh32(image), codec_id=ml.CODEC_FULL,
                          is_full=True, sign_priv=priv)
    parsed = ml.parse_container(ml.build_container(m, image))
    assert parsed.manifest.is_signed
    assert ml.verify(parsed, expect_pub=ml.ed25519_public_bytes(priv)) == []
    # wrong expected key -> flagged
    other = ml.ed25519_public_bytes(Ed25519PrivateKey.generate())
    assert any("signer_pubkey" in p for p in ml.verify(parsed, expect_pub=other))


def test_tampered_signature_detected():
    priv = Ed25519PrivateKey.generate()
    fw = _fw(13, 8 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=7, fw_version=1, image_size=len(image), payload=image,
                          block_size=1024, image_hash=ml.mh32(image),
                          codec_id=ml.CODEC_FULL, is_full=True, sign_priv=priv)
    blob = bytearray(ml.build_container(m, image))
    # flip a byte of target_id (inside signed region) without re-signing
    blob[10] ^= 0xFF
    problems = ml.verify(ml.parse_container(bytes(blob)))
    assert any("signature INVALID" in p for p in problems), problems


# --- approval enforcement --------------------------------------------------

def test_approval_default_and_flagged_if_preapproved():
    fw = _fw(14, 4 * 1024)
    image, _ = ml.ensure_endf(fw)
    m = ml.build_manifest(target_id=1, fw_version=1, image_size=len(image), payload=image,
                          block_size=1024, image_hash=ml.mh32(image),
                          codec_id=ml.CODEC_FULL, is_full=True)
    assert m.approval == ml.APPROVAL_NOT
    # simulate a malicious pre-approved container -> verify must flag it
    m.approval = ml.APPROVAL_YES
    parsed = ml.parse_container(ml.build_container(m, image))
    assert any("approval" in p for p in ml.verify(parsed))


# --- delta -----------------------------------------------------------------

def test_delta_build_apply_verify():
    old_body = _fw(20, 40 * 1024)
    # new = old with a chunk changed + appended -> a real, small-ish delta
    new_body = bytearray(old_body)
    for i in range(1000, 1500):
        new_body[i] = (new_body[i] + 1) & 0xFF
    new_body += _fw(21, 2048)
    old_image, base_hash = ml.ensure_endf(bytes(old_body))
    new_image, _ = ml.ensure_endf(bytes(new_body))

    import detools
    fp = io.BytesIO()
    detools.create_patch(io.BytesIO(old_image), io.BytesIO(new_image), fp,
                         patch_type="sequential", compression="crle")
    delta = fp.getvalue()
    # with compression a near-identical-base delta is a fraction of the full image
    assert len(delta) < len(new_image) // 2, (len(delta), len(new_image))

    m = ml.build_manifest(target_id=0xABCD, fw_version=ml.pack_version("1.2.0"),
                          image_size=len(new_image), payload=delta, block_size=1024,
                          image_hash=ml.mh32(new_image), codec_id=ml.CODEC_DETOOLS_SEQUENTIAL,
                          is_full=False, base_hash=base_hash)
    parsed = ml.parse_container(ml.build_container(m, delta))
    assert parsed.manifest.base_hash == base_hash == ml.mh8(bytes(old_body))
    # full verify incl. applying the delta to the base and checking image_hash
    assert ml.verify(parsed, base_image=old_image) == []
    # wrong base must fail the delta->image_hash check
    wrong = ml.verify(parsed, base_image=_fw(99, 40 * 1024))
    assert wrong, "delta verify against a wrong base should fail"


def test_delta_in_place_build_apply_verify():
    old_body = _fw(22, 8 * 1024)
    new_body = bytearray(old_body)
    for i in range(700, 1200):
        new_body[i] = (new_body[i] + 1) & 0xFF
    new_body += _fw(23, 1024)
    old_image, base_hash = ml.ensure_endf(old_body)
    new_image, _ = ml.ensure_endf(bytes(new_body))

    import detools
    fp = io.BytesIO()
    detools.create_patch(
        io.BytesIO(old_image), io.BytesIO(new_image), fp,
        patch_type="in-place", compression="crle",
        memory_size=ml.NRF52_INPLACE_MEMORY + ml.NRF52_FLASH_PAGE,
        segment_size=ml.NRF52_FLASH_PAGE, use_mmap=False)
    delta = fp.getvalue()

    m = ml.build_manifest(target_id=0xABCD, fw_version=ml.pack_version("1.2.1"),
                          image_size=len(new_image), payload=delta, block_size=1024,
                          image_hash=ml.mh32(new_image), codec_id=ml.CODEC_DETOOLS_INPLACE,
                          is_full=False, base_hash=base_hash)
    parsed = ml.parse_container(ml.build_container(m, delta))

    # Verification must use the in-place decoder and compare the reconstructed
    # bytes (not the full mutable memory image) with image_size/image_hash.
    assert ml.verify(parsed, base_image=old_image) == []
    wrong = ml.verify(parsed, base_image=_fw(99, len(old_body)))
    assert "delta applied to base does not match image_hash" in wrong


# --- runner ----------------------------------------------------------------

def _run():
    tests = {k: v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)}
    failed = 0
    for name, fn in tests.items():
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
