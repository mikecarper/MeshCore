# Assertion sections for companion-transport. Called by test_build_profiles.sh.
# Original section bodies stay byte-for-byte intact; see build_profile_sections.json.

build_profile_wio_full_display_base() {
# build-profile-body: wio_full_display_base
# The generated L1 Full target must use the recipe with both display drivers,
# rather than the earlier USB-only OLED recipe with the same physical board.
[ "${PIO_ENV_BUILD_BASE_BY_NAME[WioTrackerL1_companion_radio_full]:-}" = WioTrackerL1_companion_radio_ble ] \
  || fail "Wio L1 Full did not select its runtime-display BLE base"

# build-profile-end: wio_full_display_base
}

build_profile_cardputer_companion_aliases() {
# build-profile-body: cardputer_companion_aliases
# Cardputer keeps its historical recipes for explicit recovery builds while
# ordinary releases publish one USB/BLE/WiFi Full image with the keyboard UI.
for cardputer_env in M5Stack_Cardputer_Adv_companion_radio_usb \
    M5Stack_Cardputer_Adv_companion_radio_ble; do
  [ "$(get_pio_build_env "$cardputer_env")" = "$cardputer_env" ] \
    || fail "$cardputer_env lost its exact board recipe"
  for resolver in resolve_all_firmwares resolve_companion_firmwares; do
    if grep -Fxq "$cardputer_env" <<<"$("$resolver")"; then
      fail "$resolver republishes Cardputer transport alias $cardputer_env"
    fi
    grep -Fxq M5Stack_Cardputer_Adv_companion_radio_full <<<"$("$resolver")" \
      || fail "$resolver omitted combined Cardputer Full"
  done
done

# build-profile-end: cardputer_companion_aliases
}

build_profile_companion_build_routing() {
# build-profile-body: companion_build_routing
# Named Full Companion already selects its own combined profile. In a bulk
# pass it must compile the real source recipe, including generated Full names,
# without entering the infrastructure promotion path. Stop at recipe capture,
# before any dependency install, PlatformIO invocation, or artifact collection.
(
  BATCH_BUILD_MODE=1
  BUILD_PROFILE_EXPLICIT=0
  BUILD_PROFILE_EFFECTIVE=standard
  ESP32_FULL_BUILD=0
  FIRMWARE_VERSION=vtest-full-companion-routing
  RESUME_BUILD_OUTPUT=0
  companion_probe_marker=$(mktemp)
  trap 'rm -f "$companion_probe_marker"' EXIT
  compute_build_recipe_digest() {
    printf '%s|%s|%s\n' "$1" "$3" "$ESP32_FULL_BUILD" > "$companion_probe_marker"
    return 77
  }
  for full_companion in Heltec_v3_companion_radio_full Heltec_ct62_companion_radio_full; do
    source_recipe=$(get_pio_build_env "$full_companion")
    [ "$(get_exact_identity_full_pio_env "$full_companion")" = "$source_recipe" ] \
      || fail "$full_companion tried to compile a logical Full name as its source recipe"
    : > "$companion_probe_marker"
    if build_firmware_one_profile "$full_companion" >/dev/null; then
      fail "$full_companion bypassed the pre-PlatformIO recipe probe"
    fi
    [ "$(cat "$companion_probe_marker")" = "$full_companion|$source_recipe|0" ] \
      || fail "$full_companion entered infrastructure promotion or lost its real source recipe"
  done
)

# build-profile-end: companion_build_routing
}

build_profile_kiss_transport_boundaries() {
# build-profile-body: kiss_transport_boundaries
# KISS inherits board OTA flags, not the application OTA implementation.
for kiss_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  is_kiss_modem_target "$kiss_env" || continue
  for kiss_profile in auto standard full; do
    (
      BUILD_PROFILE_FOR_TARGET=$kiss_profile
      ESP32_FULL_BUILD=1
      if is_lora_ota_build "$kiss_env"; then
        fail "$kiss_env $kiss_profile incorrectly promises LoRa OTA"
      fi
      BUILD_CAPABILITIES=()
      BUILD_EXPECTATIONS=()
      BUILD_REDUCTIONS=()
      declare_build_capability_contract "$kiss_env" "${PIO_ENV_PLATFORM_BY_NAME[$kiss_env]}"
      for expectation in "${BUILD_EXPECTATIONS[@]}"; do
        case "$expectation" in
          ota.update.lora=*|ota.cli=*) fail "$kiss_env $kiss_profile has an impossible OTA contract" ;;
        esac
      done
    )
  done
done

# build-profile-end: kiss_transport_boundaries
}

build_profile_t1000_declared_ota() {
# build-profile-body: t1000_declared_ota
# Auto means that an explicitly selected target keeps the capabilities in its
# resolved PlatformIO recipe.  T1000-E BLE/USB both inherit nRF52 LoRa OTA; the
# release builder must not silently replace ENABLE_OTA with -UENABLE_OTA merely
# because Companion is not a repeater role.  Standard keeps the existing
# portable-release reduction policy.
for t1000_env in \
    t1000e_companion_radio_ble \
    t1000e_companion_radio_usb; do
  [ "${PIO_ENV_OTA_BY_NAME[$t1000_env]:-0}" = 1 ] \
    || fail "$t1000_env resolved recipe no longer declares LoRa OTA"
  (
    BUILD_PROFILE_FOR_TARGET=auto
    is_lora_ota_build "$t1000_env" \
      || fail "$t1000_env auto profile stripped declared LoRa OTA"

    PLATFORMIO_BUILD_FLAGS=""
    PLATFORMIO_BUILD_UNFLAGS=""
    apply_lora_ota_override "$t1000_env"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DENABLE_OTA=1"* ]] \
      || fail "$t1000_env auto overlay omitted ENABLE_OTA"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"-UENABLE_OTA"* ]] \
      || fail "$t1000_env auto overlay still disables LoRa OTA"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DCOMPANION_FEATURE_TEMP_RADIO=1"* ]] \
      || fail "$t1000_env auto overlay omitted temporary-radio control"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"-DCOMPANION_FEATURE_OTA_CLI=1"* ]] \
      || fail "$t1000_env auto overlay omitted OTA CLI control"

    BUILD_CAPABILITIES=()
    BUILD_REDUCTIONS=()
    BUILD_EXPECTATIONS=()
    declare_build_capability_contract "$t1000_env" NRF52_PLATFORM
    expectations=" ${BUILD_EXPECTATIONS[*]} "
    [[ "$expectations" == *"companion.temp_radio=ERR usage: tempradio"* ]] \
      || fail "$t1000_env auto contract omitted temporary-radio verification"
    [[ "$expectations" == *"companion.ota_cli=OTA: status"* ]] \
      || fail "$t1000_env auto contract omitted OTA CLI verification"
  )
  (
    BUILD_PROFILE_FOR_TARGET=standard
    if is_lora_ota_build "$t1000_env"; then
      fail "$t1000_env standard profile bypassed portable OTA policy"
    fi
  )
done

# build-profile-end: t1000_declared_ota
}

build_profile_full_companion_overlays() {
# build-profile-body: full_companion_overlays
requires_esp32_portable_size_ceiling heltec_rc32_repeater \
  || fail "ordinary RC32 repeater lost the portable size ceiling"

while IFS='|' read -r source_env full_env build_base; do
  [ "${PIO_ENV_PLATFORM_BY_NAME[$full_env]:-}" = ESP32_PLATFORM ] \
    || fail "$full_env was not registered as ESP32 Full Companion"
  [ "$(get_pio_build_env "$full_env")" = "$build_base" ] \
    || fail "$full_env did not retain its exact board recipe"
  [ "$(get_esp32_full_companion_replacement "$source_env")" = "$full_env" ] \
    || fail "$source_env did not map to $full_env"
  is_redundant_bulk_build_target "$source_env" \
    || fail "$source_env remained beside its Full Companion"
done <<'FULL_COMPANION_SPECS'
M5Stack_Cardputer_Adv_companion_radio_usb|M5Stack_Cardputer_Adv_companion_radio_full|M5Stack_Cardputer_Adv_companion_radio_full
M5Stack_Cardputer_Adv_companion_radio_ble|M5Stack_Cardputer_Adv_companion_radio_full|M5Stack_Cardputer_Adv_companion_radio_full
LilyGo_Tlora_C6_companion_radio_ble_|LilyGo_Tlora_C6_companion_radio_full_|LilyGo_Tlora_C6_companion_radio_ble_
Meshimi_companion_radio_ble_|Meshimi_companion_radio_full_|Meshimi_companion_radio_ble_
WHY2025_badge_companion_radio_ble_|WHY2025_badge_companion_radio_full_|WHY2025_badge_companion_radio_ble_
Xiao_C6_companion_radio_ble_|Xiao_C6_companion_radio_full_|Xiao_C6_companion_radio_ble_
heltec_v4_3_expansionkit_tft_companion_radio_ble_femoff|heltec_v4_expansionkit_tft_companion_radio_full_femon|heltec_v4_expansionkit_tft_companion_radio_full_femon
Generic_ESPNOW_comp_radio_usb|Generic_ESPNOW_companion_radio_full|Generic_ESPNOW_comp_radio_usb
Heltec_E290_companion_usb_ble|Heltec_E290_companion_radio_full|Heltec_E290_companion_usb_ble
Heltec_T190_companion_radio_usb_ble_|Heltec_T190_companion_radio_full_|Heltec_T190_companion_radio_usb_ble_
SenseCapIndicator-ESPNow_comp_radio_usb|SenseCapIndicator-ESPNow_companion_radio_full|SenseCapIndicator-ESPNow_comp_radio_usb
SenseCapIndicator-LoRa_comp_radio_usb_wifi|SenseCapIndicator-LoRa_companion_radio_full|SenseCapIndicator-LoRa_comp_radio_usb_wifi
SenseCapIndicator-LoRa-N16R2_comp_radio_usb_wifi|SenseCapIndicator-LoRa-N16R2_companion_radio_full|SenseCapIndicator-LoRa-N16R2_comp_radio_usb_wifi
Waveshare_S3_LR1121_companion_radio_usb|Waveshare_S3_LR1121_companion_radio_full|Waveshare_S3_LR1121_companion_radio_full
Waveshare_S3_LR1121_companion_radio_ble|Waveshare_S3_LR1121_companion_radio_full|Waveshare_S3_LR1121_companion_radio_full
Wio_Tracker_L2_companion_radio_usb|Wio_Tracker_L2_companion_radio_full|Wio_Tracker_L2_companion_radio_full
Wio_Tracker_L2_companion_radio_ble|Wio_Tracker_L2_companion_radio_full|Wio_Tracker_L2_companion_radio_full
FULL_COMPANION_SPECS

# The Full overlay, not the historical transport used as its build base, owns
# the capability contract. Verify every registered ESP32 Full target receives
# an ordinary WiFi Companion transport even when its base was USB- or BLE-only.
for full_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  is_esp32_companion_radio_full_target "$full_env" || continue
  pio_env=$(get_pio_build_env "$full_env")
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  BUILD_REDUCTIONS=()
  apply_companion_radio_full_profile "$full_env" "$pio_env"
  apply_esp32_full_async_tcp_profile "$full_env"

  if ! pio_env_option_contains "$pio_env" build_flags WIFI_SSID \
      && [[ "$PLATFORMIO_BUILD_FLAGS" != *"WIFI_SSID"* ]]; then
    fail "$full_env Full Companion omitted ordinary WiFi credentials"
  fi
  if ! pio_env_option_contains "$pio_env" build_src_filter "helpers/wifi/*.cpp" \
      && ! pio_env_option_contains "$pio_env" build_src_filter \
          "helpers/wifi/SerialWifiInterface.cpp" \
      && [[ "$PLATFORMIO_BUILD_SRC_FILTER" \
          != *"helpers/wifi/SerialWifiInterface.cpp"* ]]; then
    fail "$full_env Full Companion omitted SerialWifiInterface"
  fi
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"WIFI_OTA_SEEDER=1"* ]] \
    || fail "$full_env Full Companion omitted its WiFi runtime overlay"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESHCORE_EXPANDED_PARTITION_PROFILE=1"* ]] \
    || fail "$full_env Full Companion omitted its expanded runtime profile"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_RADIO_FULL=1"* ]] \
    || fail "$full_env Full Companion omitted its capability identity"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"BLE_PIN_CODE=123456"* ]] \
    || fail "$full_env Full Companion omitted Bluetooth pairing"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_USB_MOTA_SOURCE=1"* ]] \
    || fail "$full_env Full Companion left the serial folder source sharing the USB text parser"
  case "${full_env,,}" in
    sensecapindicator-espnow_companion_radio_full|\
    sensecapindicator-lora_companion_radio_full)
      [ "${MESHCORE_ESP32_FULL_PARTITION_TABLE:-}" \
          = "variants/sensecap_indicator-espnow/dual_ota_2560k_preserve_spiffs.csv" ] \
        || fail "$full_env omitted the Indicator preserve-SPIFFS table"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"INDICATOR_WIFI_FONT_RECOVERY=1"* ]] \
        || fail "$full_env omitted WiFi font recovery"
      ;;
    sensecapindicator-lora-n16r2_companion_radio_full)
      [ "${MESHCORE_ESP32_FULL_PARTITION_TABLE:-}" \
          = "variants/sensecap_indicator-espnow/dual_ota_6400k_preserve_spiffs.csv" ] \
        || fail "$full_env omitted the N16R2 preserve-SPIFFS table"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"INDICATOR_WIFI_FONT_RECOVERY=1"* ]] \
        || fail "$full_env omitted WiFi font recovery"
      ;;
    *)
      [ -z "${MESHCORE_ESP32_FULL_PARTITION_TABLE:-}" ] \
        || fail "$full_env inherited another board's required partition table"
      ;;
  esac
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"CONFIG_ASYNC_TCP_STACK_SIZE=4096"* ]] \
    || fail "$full_env Full Companion omitted its bounded AsyncTCP stack"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"CONFIG_ASYNC_TCP_RUNNING_CORE=1"* ]] \
    || fail "$full_env Full Companion omitted its AsyncTCP core isolation"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"ASYNCWEBSERVER_USE_CHUNK_INFLIGHT=0"* ]] \
    || fail "$full_env Full Companion omitted its slow-client response profile"

  case "${full_env,,}" in
    generic_espnow_companion_radio_full|\
    sensecapindicator-espnow_companion_radio_full)
      pio_env_option_contains "$pio_env" build_flags "MESH_PRIMARY_ESPNOW=1" \
        || fail "$full_env lost its primary ESP-NOW radio marker"
      [[ "$PLATFORMIO_BUILD_UNFLAGS" != *"MESH_PRIMARY_ESPNOW"* ]] \
        || fail "$full_env removed its primary ESP-NOW radio marker"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_ESPNOW_RADIO=1"* ]] \
        || fail "$full_env omitted ESP-NOW/WiFi coexistence policy"
      ;;
  esac
done
unset PLATFORMIO_BUILD_FLAGS PLATFORMIO_BUILD_UNFLAGS \
  PLATFORMIO_BUILD_SRC_FILTER MESHCORE_ESP32_FULL_PARTITION_TABLE

# nRF52 Full targets use the same capability identity and pairing transport,
# plus both host-folder mOTA sources. Check every registered logical target so
# synthetic Full names that share a USB/BLE PlatformIO recipe stay protected.
for full_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  is_nrf52_companion_radio_full_target "$full_env" || continue
  pio_env=$(get_pio_build_env "$full_env")
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  PLATFORMIO_BUILD_SRC_FILTER=""
  BUILD_REDUCTIONS=()
  apply_companion_radio_full_profile "$full_env" "$pio_env"

  [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_RADIO_FULL=1"* ]] \
    || fail "$full_env Full Companion omitted its capability identity"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"BLE_PIN_CODE=123456"* ]] \
    || fail "$full_env Full Companion omitted Bluetooth pairing"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"ENABLE_OTA=1"* ]] \
    || fail "$full_env Full Companion omitted LoRa OTA seeding"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"OTA_FOLDER_SERIAL=1"* ]] \
    || fail "$full_env Full Companion omitted USB folder seeding"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"CFG_DEBUG=1"* ]] \
    || fail "$full_env Full Companion omitted the validated nRF52 BLE bootstrap"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *"RECOVERABLE_EXTERNAL_RADIO=1"* ]] \
    || fail "$full_env Full Companion must keep Companion transports available while radio recovery runs"
  if pio_env_option_contains "$pio_env" build_flags "MESH_PRIMARY_UART_PORT"; then
    pio_env_option_contains "$pio_env" build_flags "ENABLE_USB_INTERFACE" \
      || fail "$full_env omitted its wired UART Companion transport"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_USB_MOTA_SOURCE=1"* ]] \
      || fail "$full_env omitted its wired folder seeder"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_BLE_MOTA_SOURCE=1"* ]] \
      || fail "$full_env omitted BLE folder seeding"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_DEBUG=1"* ]] \
      || fail "$full_env omitted primary UART diagnostics"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_PACKET_LOGGING=1"* ]] \
      || fail "$full_env omitted primary UART packet logging"
    for incompatible in COMPANION_FEATURE_DEDICATED_USB_LOGGING=1 CFG_TUD_CDC=2 \
        MESH_DUAL_CDC_LOGGING=1 MESH_USB_LOGGING_DISABLED=1; do
      [[ "$PLATFORMIO_BUILD_FLAGS" != *"$incompatible"* ]] \
        || fail "$full_env applied native USB logging policy to a UART transport"
      if pio_env_option_contains "$pio_env" build_flags "$incompatible"; then
        fail "$full_env recipe applied native USB logging policy to a UART transport"
      fi
    done
  elif [ "$full_env" = "t1000e_companion_radio_full" ]; then
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_BLE_MOTA_SOURCE=1"* ]] \
      || fail "$full_env Full Companion omitted BLE folder seeding"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_FEATURE_BLE_MOTA_SOURCE=0"* ]] \
      || fail "$full_env must not disable its BLE mOTA extension"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_FEATURE_BLE_DFU=0"* ]] \
      || fail "$full_env must not disable its BLE DFU extension"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_FEATURE_DEDICATED_USB_LOGGING=1"* ]] \
      || fail "$full_env must not enable its unstable second USB CDC interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"CFG_TUD_CDC=2"* ]] \
      || fail "$full_env must keep a single USB CDC interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"MESH_DUAL_CDC_LOGGING=1"* ]] \
      || fail "$full_env must keep USB logging on its primary interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"MESH_USB_LOGGING_DISABLED=1"* ]] \
      || fail "$full_env must retain its single-port USB logging terminal"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_DEBUG=1"* ]] \
      || fail "$full_env must retain USB diagnostics on its logging terminal"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_PACKET_LOGGING=1"* ]] \
      || fail "$full_env must retain packet logging on its logging terminal"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_BLE_PRPH_MTU="* ]] \
      || fail "$full_env must retain the normal Full-Companion BLE bandwidth"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"COMPANION_BLUETOOTH_BOOT_DELAY_MS="* ]] \
      || fail "$full_env must start Bluetooth promptly"
  else
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_BLE_MOTA_SOURCE=1"* ]] \
      || fail "$full_env Full Companion omitted BLE folder seeding"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"COMPANION_FEATURE_DEDICATED_USB_LOGGING=1"* ]] \
      || fail "$full_env Full Companion omitted dedicated USB logging"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"CFG_TUD_CDC=2"* ]] \
      || fail "$full_env Full Companion omitted its second USB CDC interface"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *"MESH_DUAL_CDC_LOGGING=1"* ]] \
      || fail "$full_env Full Companion omitted dedicated USB logging routing"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *"MESH_USB_LOGGING_DISABLED=1"* ]] \
      || fail "$full_env unexpectedly disabled USB logging"
  fi
  if ! pio_env_option_contains "$pio_env" build_src_filter "helpers/ota/*.cpp" \
      && [[ "$PLATFORMIO_BUILD_SRC_FILTER" != *"helpers/ota/*.cpp"* ]]; then
    fail "$full_env Full Companion omitted the OTA C++ implementation"
  fi
done
unset PLATFORMIO_BUILD_FLAGS PLATFORMIO_BUILD_UNFLAGS \
  PLATFORMIO_BUILD_SRC_FILTER

# build-profile-end: full_companion_overlays
}

build_profile_companion_mqtt_selection() {
# build-profile-body: companion_mqtt_selection
while IFS='|' read -r source_env full_env; do
  [ "$(get_esp32_full_companion_replacement "$source_env")" = "$full_env" ] \
    || fail "$source_env did not collapse into $full_env"
  is_redundant_bulk_build_target "$source_env" \
    || fail "$source_env remained beside its MQTT-capable Full Companion"
  [ "${PIO_ENV_MQTT_BY_NAME[$full_env]:-1}" = 0 ] \
    || fail "$full_env was incorrectly classified as an MQTT-only profile"
done <<'MQTT_FULL_COMPANION_SPECS'
Heltec_v3_companion_radio_wifi_mqtt|Heltec_v3_companion_radio_full
heltec_v4_companion_radio_wifi_mqtt_femon|heltec_v4_2_v4_3_companion_radio_full_femon
heltec_v4_3_companion_radio_wifi_mqtt_femoff|heltec_v4_2_v4_3_companion_radio_full_femon
MQTT_FULL_COMPANION_SPECS

for mqtt_override in on off; do
  MQTT_BRIDGE_OVERRIDE=$mqtt_override
  RESOLVED_BUILD_TARGETS=(Heltec_v3_companion_radio_full)
  normalize_resolved_targets_for_mqtt build-companion-firmwares >/dev/null
  [ "${RESOLVED_BUILD_TARGETS[*]}" = Heltec_v3_companion_radio_full ] \
    || fail "MQTT=${mqtt_override} replaced or discarded canonical Full Companion"
  PLATFORMIO_BUILD_FLAGS="-DKEEP_FULL_RECIPE=1"
  apply_mqtt_bridge_override Heltec_v3_companion_radio_full
  [ "$PLATFORMIO_BUILD_FLAGS" = "-DKEEP_FULL_RECIPE=1" ] \
    || fail "MQTT=${mqtt_override} mutated canonical Full Companion flags"
done

MQTT_BRIDGE_OVERRIDE=on
RESOLVED_BUILD_TARGETS=(Station_G2_companion_radio_full)
if normalize_resolved_targets_for_mqtt build-companion-firmwares >/dev/null; then
  fail "MQTT=on retained a Full recipe without direct MQTT capability"
fi
MQTT_BRIDGE_OVERRIDE=""

while IFS= read -r env_name; do
  if [ "${PIO_ENV_PLATFORM_BY_NAME[$env_name]:-}" = ESP32_PLATFORM ] \
      && ! is_companion_radio_full_target "$env_name"; then
    fail "canonical ESP32 Companion inventory still contains $env_name"
  fi
done < <(resolve_companion_firmwares)

# build-profile-end: companion_mqtt_selection
}

build_profile_companion_transport_contracts() {
# build-profile-body: companion_transport_contracts
# A qualified Full Companion is the one canonical release artifact for its
# board. The individual USB/BLE transports remain explicitly buildable, but
# must not be republished beside the Full image.
SUPPORTED_PIO_ENVS=(
  Station_G2_companion_radio_usb
  Station_G2_companion_radio_ble
  Station_G2_companion_radio_wifi
  Station_G2_companion_radio_full
  Station_G2_terminal_chat
)
for env_name in "${SUPPORTED_PIO_ENVS[@]}"; do
  PIO_ENV_PLATFORM_BY_NAME[$env_name]=ESP32_PLATFORM
  PIO_ENV_BOARD_BY_NAME[$env_name]=station-g2
done
mapfile -t companion_release_targets < <(
  print_release_firmware_targets get-companion-firmwares-to-build
)
[ "${#companion_release_targets[@]}" -eq 1 ] \
  || fail "Station G2 release did not collapse transports to one Full Companion"
[ "${companion_release_targets[0]}" = Station_G2_companion_radio_full ] \
  || fail "Station G2 release selected a non-Full Companion artifact"

# Full Companion's text terminal also replaces a separate Terminal Chat image
# in bulk builds. Cover direct ESP32/nRF52 names and the canonical Heltec names
# whose hardware/revision suffixes differ from their Terminal Chat targets.
PIO_ENV_PLATFORM_BY_NAME[RAK_3401_terminal_chat]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[RAK_3401_companion_radio_full]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_terminal_chat]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_2_v4_3_companion_radio_full_femon]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_tft_terminal_chat]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_tft_companion_radio_full_femon]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_tracker_v2_terminal_chat]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[heltec_tracker_v2_companion_radio_full_femon]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[PicoW_terminal_chat]=RP2040_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[PicoW_companion_radio_usb]=RP2040_PLATFORM
PIO_ENV_BOARD_BY_NAME[PicoW_terminal_chat]=rpipicow
PIO_ENV_BOARD_BY_NAME[PicoW_companion_radio_usb]=rpipicow

[ "$(get_terminal_chat_full_companion_replacement RAK_3401_terminal_chat)" \
    = RAK_3401_companion_radio_full ] \
  || fail "nRF52 Terminal Chat did not map to Full Companion"
[ "$(get_terminal_chat_full_companion_replacement heltec_v4_terminal_chat)" \
    = heltec_v4_2_v4_3_companion_radio_full_femon ] \
  || fail "Heltec V4 Terminal Chat did not map to its canonical Full Companion"
[ "$(get_terminal_chat_full_companion_replacement heltec_v4_tft_terminal_chat)" \
    = heltec_v4_tft_companion_radio_full_femon ] \
  || fail "Heltec V4 TFT Terminal Chat did not map to Full Companion"
[ "$(get_terminal_chat_full_companion_replacement heltec_tracker_v2_terminal_chat)" \
    = heltec_tracker_v2_companion_radio_full_femon ] \
  || fail "Heltec Tracker V2 Terminal Chat did not map to Full Companion"
is_redundant_bulk_build_target Station_G2_terminal_chat \
  || fail "ESP32 Terminal Chat remained in bulk builds beside Full Companion"
is_redundant_bulk_build_target RAK_3401_terminal_chat \
  || fail "nRF52 Terminal Chat remained in bulk builds beside Full Companion"
[ "$(get_terminal_chat_companion_replacement PicoW_terminal_chat)" \
    = PicoW_companion_radio_usb ] \
  || fail "RP2040 Terminal Chat did not map to USB Companion"
is_redundant_bulk_build_target PicoW_terminal_chat \
  || fail "Terminal Chat remained beside its matching USB Companion"
is_redundant_bulk_build_target heltec_v4_repeater_legacy_partition_test \
  || fail "legacy partition test fixture remained in bulk release builds"

PIO_ENV_PLATFORM_BY_NAME[RAK_4631_companion_radio_ethernet]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[RAK_4631_companion_radio_full]=NRF52_PLATFORM
[ "$(get_nrf52_full_companion_replacement \
    RAK_4631_companion_radio_ethernet)" = RAK_4631_companion_radio_full ] \
  || fail "RAK4631 Ethernet Companion did not map to Full Companion"
PIO_ENV_PLATFORM_BY_NAME[ThinkNode_M2_companion_radio_serial]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[ThinkNode_M2_companion_radio_full]=ESP32_PLATFORM
[ "$(get_esp32_full_companion_replacement \
    ThinkNode_M2_companion_radio_serial)" = ThinkNode_M2_companion_radio_full ] \
  || fail "serial Companion did not map to Full Companion"

[ "$(get_combined_usb_ble_companion_replacement \
    Heltec_E290_companion_usb)" = Heltec_E290_companion_usb_ble ] \
  || fail "E290 USB Companion did not map to USB+BLE Companion"
[ "$(get_combined_usb_ble_companion_replacement \
    Heltec_T190_companion_radio_ble_)" \
    = Heltec_T190_companion_radio_usb_ble_ ] \
  || fail "T190 BLE Companion did not map to USB+BLE Companion"

if is_redundant_bulk_build_target RAK_4631_repeater_ethernet \
    || is_redundant_bulk_build_target RAK_4631_room_server_ethernet; then
  fail "RAK4631 repeater/room Ethernet artifact was incorrectly merged"
fi

[ "$(get_merged_rs232_repeater_replacement \
    RAK_4631_repeater_bridge_rs232_serial1)" = RAK_4631_repeater ] \
  || fail "RAK4631 Serial1 bridge did not map to merged repeater"
[ "$(get_merged_rs232_repeater_replacement \
    RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors)" \
    = RAK_4631_repeater_lora_ota_no_external_sensors ] \
  || fail "RAK4631 Serial2 OTA bridge did not map to merged repeater"
[ "$(get_merged_rs232_repeater_replacement \
    Heltec_t114_repeater_bridge_rs232)" = Heltec_t114_repeater ] \
  || fail "T114 RS232 bridge did not map to merged repeater"
[ "$(get_merged_rs232_repeater_replacement \
    Heltec_t096_repeater_bridge_rs232)" = Heltec_t096_repeater ] \
  || fail "T096 RS232 bridge did not map to merged repeater"
is_redundant_bulk_build_target Heltec_v3_repeater_bridge_rs232 \
  || fail "merged RS232 bridge remained in canonical bulk builds"
[ "$(get_merged_rs232_repeater_replacement MKE_s3_repeater_bridge_rs232)" \
    = MKE_s3_repeater ] \
  || fail "MKE S3 RS232 bridge did not map to its merged repeater"
is_supported_build_env MKE_s3_repeater_bridge_rs232 \
  || fail "MKE S3 legacy RS232 target stopped being directly buildable"
[ "$(get_pio_build_env MKE_s3_repeater_bridge_rs232)" \
    = MKE_s3_repeater_bridge_rs232 ] \
  || fail "MKE S3 direct legacy build lost its bridge-on recipe"
is_redundant_bulk_build_target MKE_s3_repeater_bridge_rs232 \
  || fail "MKE S3 dedicated RS232 image remained in canonical bulk builds"
if is_redundant_bulk_build_target MKE_s3_repeater; then
  fail "MKE S3 canonical repeater was omitted"
fi
is_redundant_bulk_build_target MKE_s3_repeater_bridge_espnow \
  || fail "MKE S3 dedicated ESP-NOW image remained beside its combined repeater"
[ "$(get_merged_espnow_repeater_replacement MKE_s3_repeater_bridge_espnow)" \
    = MKE_s3_repeater ] \
  || fail "MKE S3 ESP-NOW bridge did not map to its merged repeater"
is_supported_build_env MKE_s3_repeater_bridge_espnow \
  || fail "MKE S3 legacy ESP-NOW target stopped being directly buildable"
[ "$(get_pio_build_env MKE_s3_repeater_bridge_espnow)" \
    = MKE_s3_repeater_bridge_espnow ] \
  || fail "MKE S3 direct legacy ESP-NOW build lost its bridge-on recipe"
(
  SUPPORTED_PIO_ENVS=(
    MKE_s3_repeater
    MKE_s3_repeater_bridge_rs232
    MKE_s3_repeater_bridge_espnow
  )
  [ "$(resolve_all_firmwares)" \
      = MKE_s3_repeater ] \
    || fail "MKE S3 bulk inventory did not consolidate both bridge images"
  [ "$(resolve_repeater_firmwares)" \
      = MKE_s3_repeater ] \
    || fail "MKE S3 repeater inventory retained dedicated bridge images"
  [ "$(resolve_full_esp32_firmwares)" \
      = MKE_s3_repeater ] \
    || fail "MKE S3 Full ESP32 inventory retained dedicated bridge images"
)

# A hardcoded merged-role recommendation is insufficient if its normal
# recipe no longer exists, cannot supply Full, or belongs to another board.
# Isolate these metadata faults so a future observer cannot mask the guard.
for replacement_fault in missing full board; do
  (
    normal_target=MKE_s3_repeater
    bridge_target=MKE_s3_repeater_bridge_espnow
    PIO_ENV_PLATFORM_BY_NAME[$normal_target]=ESP32_PLATFORM
    PIO_ENV_FULL_BUILD_BY_NAME[$normal_target]=1
    PIO_ENV_BOARD_BY_NAME[$normal_target]=mke-test-board
    PIO_ENV_PLATFORM_BY_NAME[$bridge_target]=ESP32_PLATFORM
    PIO_ENV_FULL_BUILD_BY_NAME[$bridge_target]=1
    PIO_ENV_BOARD_BY_NAME[$bridge_target]=mke-test-board
    for candidate in "$normal_target" "${normal_target}_mqtt" \
        "${normal_target}_observer_mqtt" "${normal_target}_observer_mqtt_"; do
      PIO_ENV_MQTT_BY_NAME[$candidate]=0
    done
    case "$replacement_fault" in
      missing) unset 'PIO_ENV_PLATFORM_BY_NAME[MKE_s3_repeater]' ;;
      full) PIO_ENV_FULL_BUILD_BY_NAME[$normal_target]=0 ;;
      board) PIO_ENV_BOARD_BY_NAME[$normal_target]=different-board ;;
    esac
    [ "$(get_ordinary_full_group_key "$bridge_target")" \
        = 'mke-test-board|mke_s3_repeater_bridge_espnow' ] \
      || fail "MKE S3 $replacement_fault replacement incorrectly suppressed its bridge"
  )
done
if is_redundant_bulk_build_target wio-e5-repeater_bridge_rs232; then
  fail "capacity-constrained Wio-E5 RS232 bridge was incorrectly merged"
fi

(
  BUILD_PROFILE_FOR_TARGET=full
  for uart_target in Heltec_v3_repeater Heltec_WSL3_repeater RAK_3112_repeater \
      LilyGo_TLora_V2_1_1_6_repeater; do
    BUILD_EXPECTATIONS=()
    declare_build_capability_contract "$uart_target" ESP32_PLATFORM
    [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.rs232=_ZN11RS232Bridge5beginEv"* ]] \
      || fail "$uart_target Full contract omitted the linked UART driver"
    [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.espnow=_ZN12ESPNowBridge5beginEv"* ]] \
      || fail "$uart_target Full contract omitted the linked ESP-NOW driver"
  done
  BUILD_EXPECTATIONS=()
  declare_build_capability_contract LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_ ESP32_PLATFORM
  [[ " ${BUILD_EXPECTATIONS[*]} " != *"bridge.rs232="* ]] \
    || fail "TLora MQTT capacity exception falsely promised a linked UART driver"
  [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.espnow=_ZN12ESPNowBridge5beginEv"* ]] \
    || fail "TLora MQTT capacity exception omitted its linked ESP-NOW driver"
)

# A WiFi base's final -UENABLE_OTA must not win over the Full Companion's
# source-only OTA overlay. That mismatch compiles out both the TCP terminal and
# seeder while still leaving a superficially valid binary.
pio_env_option_contains() {
  [ "$2" = build_flags ] && [ "$3" = -UENABLE_OTA ]
}
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_UNFLAGS=""
PLATFORMIO_EXTRA_SCRIPTS=""
unset MESHCORE_FORCE_LORA_OTA
apply_companion_radio_full_profile \
  Station_G2_companion_radio_full Station_G2_companion_radio_wifi
apply_lora_ota_flag_order_fix \
  Station_G2_companion_radio_full Station_G2_companion_radio_wifi
[ "${MESHCORE_FORCE_LORA_OTA:-}" = 1 ] \
  || fail "ESP32 Full Companion did not force its OTA source overlay"
[[ "$PLATFORMIO_EXTRA_SCRIPTS" == *"pre:scripts/force_lora_ota.py"* ]] \
  || fail "ESP32 Full Companion did not install the OTA flag-order fix"

# The same protection applies to explicitly requested standalone OTA
# transports even though the canonical release publishes the Full replacement.
PIO_ENV_OTA_BY_NAME[Station_G2_companion_radio_wifi]=1
PLATFORMIO_EXTRA_SCRIPTS=""
unset MESHCORE_FORCE_LORA_OTA
apply_lora_ota_flag_order_fix \
  Station_G2_companion_radio_wifi Station_G2_companion_radio_wifi
[ "${MESHCORE_FORCE_LORA_OTA:-}" = 1 ] \
  || fail "standalone ESP32 OTA Companion did not force its OTA overlay"
[[ "$PLATFORMIO_EXTRA_SCRIPTS" == *"pre:scripts/force_lora_ota.py"* ]] \
  || fail "standalone ESP32 OTA Companion omitted the OTA flag-order fix"

# Full capability contracts must use functional strings that survive release
# builds with optional debug logging disabled.
BUILD_PROFILE_FOR_TARGET=full
BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  Station_G2_companion_radio_full ESP32_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"companion.network_terminal=USB currently owns the Full Companion terminal"* ]] \
  || fail "ESP32 Full contract uses no stable network-terminal evidence"
[[ "$expectations" == *"companion.wifi_ota_seeder=OTA seeder listening on :"* ]] \
  || fail "ESP32 Full contract omitted the WiFi seeder"
[[ "$expectations" == *"web.unconfigured_setup_cutoff=WiFi still unconfigured after"* ]] \
  || fail "ESP32 Full contract omitted the bounded unconfigured-WiFi window"
[[ "$expectations" != *"Full Companion terminal listening"* ]] \
  || fail "ESP32 Full contract still depends on optional debug logging"

# Every ESP32 Full Companion must ship the official app's WiFi controls.
# These checks apply to the packaged binary, not debug strings in the ELF.
for wifi_target in Station_G2_companion_radio_full \
    heltec_v4_2_v4_3_companion_radio_full_femon \
    SenseCapIndicator-LoRa_companion_radio_full; do
  declare_build_capability_contract "$wifi_target" ESP32_PLATFORM
  app_expectations=" ${BUILD_APPLICATION_EXPECTATIONS[*]} "
  for operation in get_enabled set_enabled get_ssid set_ssid get_password set_password get_status get_ip; do
    [[ "$app_expectations" == *"companion.app_wifi.${operation}="* ]] \
      || fail "$wifi_target omitted app WiFi $operation from packaged qualification"
  done
done

BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  SenseCapIndicator-LoRa_companion_radio_full ESP32_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"indicator.font_recovery_ntp_gate=requesting fresh NTP time before download"* ]] \
  || fail "Indicator Full contract omitted the fresh-NTP font-download gate"
[[ "$expectations" == *"indicator.font_recovery_tls=opening TLS connection to"* ]] \
  || fail "Indicator Full contract omitted the TLS font-recovery marker"

pio_env_option_contains() {
  [ "$1" = Heltec_v3_companion_radio_full ] \
    && [ "$2" = build_flags ] \
    && [ "$3" = WITH_MQTT_BRIDGE ]
}
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  Heltec_v3_companion_radio_full ESP32_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"companion.direct_mqtt=mqtt.status"* ]] \
  || fail "MQTT-capable ESP32 Full contract omitted direct MQTT"
[[ "$expectations" == *"companion.usb_mota_source=ota folder on"* ]] \
  || fail "ESP32 Full contract omitted exclusive USB folder seeding"

PIO_ENV_PLATFORM_BY_NAME[RAK_3401_companion_radio_full]=NRF52_PLATFORM
BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
BUILD_EXPECTATIONS=()
declare_build_capability_contract \
  RAK_3401_companion_radio_full NRF52_PLATFORM
expectations=" ${BUILD_EXPECTATIONS[*]} "
[[ "$expectations" == *"companion.usb_mota_source=ota folder on"* ]] \
  || fail "nRF52 Full contract omitted USB folder seeding"
[[ "$expectations" != *"companion.network_terminal="* ]] \
  || fail "nRF52 Full contract promised an ESP32-only network terminal"
[[ " ${BUILD_APPLICATION_EXPECTATIONS[*]} " != *"companion.app_wifi."* ]] \
  || fail "nRF52 Full contract promised ESP32 app WiFi settings"

BUILD_PROFILE_FOR_TARGET=standard
for rak_target in \
    RAK_3401_repeater_lora_ota_no_external_sensors \
    RAK_4631_repeater_lora_ota_no_external_sensors; do
  BUILD_CAPABILITIES=()
  BUILD_REDUCTIONS=()
  BUILD_EXPECTATIONS=()
  declare_build_capability_contract "$rak_target" NRF52_PLATFORM
  expectations=" ${BUILD_EXPECTATIONS[*]} "
  [[ "$expectations" == *"ota.update.lora=invalid in-place patch geometry"* ]] \
    || fail "$rak_target OTA contract omitted LoRa self-update"
  [[ "$expectations" == *"sensor.gps=meshcore.capability.rak_wisblock_gps.v1"* ]] \
    || fail "$rak_target reduced OTA contract omitted retained GPS"
  [[ "$expectations" != *"sensor.gps=gps setloc"* ]] \
    || fail "$rak_target reduced OTA contract still accepts generic GPS CLI text"
  for ina in 219 226 260 3221; do
    [[ "$expectations" == *"sensor.ina${ina}=INA${ina}"* ]] \
      || fail "$rak_target reduced OTA contract omitted retained INA${ina}"
  done
done

# build-profile-end: companion_transport_contracts
}

