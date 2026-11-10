# Assertion sections for nrf52-storage. Called by test_build_profiles.sh.
# Original section bodies stay byte-for-byte intact; see build_profile_sections.json.

build_profile_reduced_rak_sensors() {
# build-profile-body: reduced_rak_sensors
# Reduced RAK profiles all retain the compact INA set, but GPS depends on
# whether the bridge already owns Serial1. Keep those independent contracts so
# a valid Serial1 image cannot fail release qualification for an absent GPS
# marker, and so its manifest never advertises impossible hardware support.
for rak_target in \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  RAK_3401_sensor_lora_ota_no_external_sensors \
  RAK_4631_repeater_lora_ota_no_external_sensors \
  RAK_4631_room_server_ethernet_lora_ota_no_external_sensors \
  RAK_4631_sensor_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors; do
  is_rak_i2c_voltage_monitor_ota_target "$rak_target" \
    || fail "$rak_target lost the retained INA contract"
done
for rak_target in \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  RAK_3401_sensor_lora_ota_no_external_sensors \
  RAK_4631_repeater_lora_ota_no_external_sensors \
  RAK_4631_room_server_ethernet_lora_ota_no_external_sensors \
  RAK_4631_sensor_lora_ota_no_external_sensors \
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors; do
  is_rak_gps_retaining_ota_target "$rak_target" \
    || fail "$rak_target lost its compatible GPS contract"
done
if is_rak_gps_retaining_ota_target \
    RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors; then
  fail "RAK4631 Serial1 RS-232 target incorrectly promises GPS"
fi

# build-profile-end: reduced_rak_sensors
}

build_profile_nrf52_room_and_qspi() {
# build-profile-body: nrf52_room_and_qspi
# Standalone room servers receive a lean LoRa OTA sibling. nRF52 sensors
# receive an explicit lean sibling while their normal image keeps its sensors.
while IFS='|' read -r room_env room_ota_env; do
  is_supported_build_env "$room_ota_env" \
    || fail "$room_ota_env was not registered as a room-server LoRa OTA target"
  [ "$(get_pio_build_env "$room_ota_env")" = "$room_env" ] \
    || fail "$room_ota_env did not retain its room-server PlatformIO recipe"
  [ "$(get_reduced_lora_ota_target "$room_env")" = "$room_ota_env" ] \
    || fail "$room_env did not resolve its reduced room-server OTA sibling"
  (
    BUILD_PROFILE_FOR_TARGET=standard
    ESP32_FULL_BUILD=0
    is_lora_ota_build "$room_ota_env" \
      || fail "$room_ota_env did not enable LoRa OTA"
  )
done <<'ROOM_OTA_SPECS'
Heltec_v3_room_server|Heltec_v3_room_server_lora_ota_no_external_sensors
RAK_3401_room_server|RAK_3401_room_server_lora_ota_no_external_sensors
ROOM_OTA_SPECS
room_release_targets=$(print_release_firmware_targets get-room-server-firmwares-to-build)
printf '%s\n' "$room_release_targets" | grep -Fx \
  RAK_3401_room_server_lora_ota_no_external_sensors >/dev/null \
  || fail "room-server release resolution omitted its portable LoRa OTA target"
printf '%s\n' "$room_release_targets" | grep -Fx \
  solarxiao_30S_room_server_lora_ota >/dev/null \
  || fail "room-server release resolution omitted its QSPI LoRa OTA target"
for room_ota_env in solarxiao_33S_room_server_lora_ota \
    RAK_4631_room_server_ethernet_lora_ota_no_external_sensors; do
  printf '%s\n' "$room_release_targets" | grep -Fx "$room_ota_env" >/dev/null \
    || fail "room-server release resolution omitted $room_ota_env"
done

# SolarXiao Room normally uses the module's onboard QSPI, not an additional
# internal-only choice. Keep direct recovery recipes and their OTA identities
# intact while excluding them from every ordinary selection path.
solar_room_targets=$(resolve_room_server_firmwares)
solar_all_targets=$(resolve_all_firmwares)
solar_logging_targets=$(resolve_logging_matrix_firmwares)
solar_menu_targets=$(get_interactive_build_targets)
for solar_board in solarxiao_30S solarxiao_33S; do
  legacy_room=${solar_board}_room_server
  qspi_room=${legacy_room}_lora_ota
  is_supported_build_env "$legacy_room" \
    || fail "$legacy_room explicit compatibility recipe disappeared"
  validate_build_target "$legacy_room" \
    || fail "$legacy_room explicit compatibility recipe is invalid"
  is_redundant_bulk_build_target "$legacy_room" \
    || fail "$legacy_room internal-only recipe remains an ordinary build"
  is_redundant_bulk_build_target "${legacy_room^^}" \
    || fail "$legacy_room retirement policy is not case insensitive"
  [ "$(get_pio_build_env "$legacy_room")" = "$legacy_room" ] \
    || fail "$legacy_room silently changed its PlatformIO recipe"
  [ "$(get_nrf52_sensor_ota_pair_target "$legacy_room")" = "$legacy_room" ] \
    || fail "$legacy_room silently changed its OTA identity"
  [ "$(get_nrf52_sensor_ota_pair_target "$qspi_room")" = "$qspi_room" ] \
    || fail "$qspi_room lost its distinct QSPI OTA identity"
  for solar_selection in "$room_release_targets" "$solar_room_targets" \
      "$solar_all_targets" "$solar_logging_targets" "$solar_menu_targets"; do
    if printf '%s\n' "$solar_selection" | grep -Fx "$legacy_room" >/dev/null; then
      fail "$legacy_room leaked into an ordinary build selection"
    fi
    printf '%s\n' "$solar_selection" | grep -Fx "$qspi_room" >/dev/null \
      || fail "$qspi_room missing from an ordinary build selection"
  done
  (
    REQUIRE_OTA_UPDATES=0
    resolve_command_targets build-firmware "$legacy_room"
    [ "${#RESOLVED_BUILD_TARGETS[@]}" -eq 1 ] \
      && [ "${RESOLVED_BUILD_TARGETS[0]}" = "$legacy_room" ] \
      || fail "$legacy_room direct recovery build changed its exact target"
  )
done
if is_solarxiao_internal_room_compatibility_target Xiao_nrf52_room_server \
    || is_solarxiao_internal_room_compatibility_target solarxiao_30S_repeater \
    || is_solarxiao_internal_room_compatibility_target solarxiao_33S_room_server_lora_ota; then
  fail "SolarXiao internal Room policy retired an unrelated recipe"
fi

# Every supported onboard-QSPI Room has a normal exact external identity,
# while old internal identities stay directly buildable without being remapped.
for qspi_board in Xiao_nrf52 \
    ikoka_nano_nrf_22dbm ikoka_nano_nrf_30dbm ikoka_nano_nrf_33dbm \
    ikoka_stick_nrf_22dbm ikoka_stick_nrf_30dbm ikoka_stick_nrf_33dbm \
    ikoka_handheld_nrf_e22_30dbm LilyGo_T-Echo SenseCap_Solar \
    ThinkNode_M1 ThinkNode_M6 WioTrackerL1 LilyGo_T-Echo-Lite; do
  legacy_room=${qspi_board}_room_server
  legacy_alias=${legacy_room}_lora_ota_no_external_sensors
  qspi_room=${legacy_room}_lora_ota
  [ "$(get_onboard_qspi_normal_replacement "$legacy_room")" = "$qspi_room" ] \
    || fail "$legacy_room lacks its normal QSPI replacement"
  for old in "$legacy_room" "$legacy_alias"; do
    is_supported_build_env "$old" || fail "$old lost explicit compatibility builds"
    is_redundant_bulk_build_target "$old" || fail "$old remains an ordinary Room choice"
    [ "$(get_pio_build_env "$old")" = "$legacy_room" ] \
      || fail "$old changed its original internal recipe"
  done
  for selection in "$room_release_targets" "$solar_room_targets" \
      "$solar_all_targets" "$solar_logging_targets" "$solar_menu_targets"; do
    grep -Fxq "$qspi_room" <<<"$selection" || fail "$qspi_room missing from ordinary selection"
    if grep -Fxq -e "$legacy_room" -e "$legacy_alias" <<<"$selection"; then
      fail "$legacy_room internal identity leaked into ordinary selection"
    fi
  done
  (
    REQUIRE_OTA_UPDATES=0
    resolve_command_targets build-firmware "$legacy_room"
    [ "${#RESOLVED_BUILD_TARGETS[@]}" -eq 1 ] \
      && [ "${RESOLVED_BUILD_TARGETS[0]}" = "$legacy_alias" ] \
      || fail "$legacy_room direct build lost its deployed internal pair identity"
  )
done
repeater_release_targets=$(print_release_firmware_targets get-repeater-firmwares-to-build)
for board in LilyGo_T-Echo SenseCap_Solar ThinkNode_M1 ThinkNode_M6 WioTrackerL1; do
  canonical=${board}_repeater
  alias=${canonical}_lora_ota_no_external_sensors
  is_supported_build_env "$alias" || fail "$alias lost its exact deployed recipe"
  for selection in "$repeater_release_targets" "$solar_all_targets" \
      "$solar_logging_targets" "$solar_menu_targets"; do
    grep -Fxq "$canonical" <<<"$selection" || fail "$canonical missing its normal QSPI pair"
    if grep -Fxq "$alias" <<<"$selection"; then fail "$alias duplicates its normal QSPI pair"; fi
  done
done
for selection in "$repeater_release_targets" "$solar_all_targets" \
    "$solar_logging_targets" "$solar_menu_targets"; do
  grep -Fxq LilyGo_T-Echo-Lite_repeater_lora_ota <<<"$selection" \
    || fail "Lite QSPI repeater missing from ordinary selection"
  if grep -Fxq -e LilyGo_T-Echo-Lite_repeater \
      -e LilyGo_T-Echo-Lite_repeater_lora_ota_no_external_sensors \
      -e Xiao_nrf52_repeater_bridge_rs232 \
      -e Xiao_nrf52_repeater_bridge_rs232_lora_ota_no_external_sensors <<<"$selection"; then
    fail "internal Lite/bridge identity leaked into ordinary selection"
  fi
done
for pending in muzi_base_duo_repeater_lora_ota muzi_base_duo_room_server_lora_ota \
    muzi_base_uno_repeater_lora_ota muzi_base_uno_room_server_lora_ota \
    LilyGo_T-Echo_Card_repeater_lora_ota LilyGo_T-Echo_Card_room_server_lora_ota \
    Nano_G2_Ultra_repeater_lora_ota Nano_G2_Ultra_room_server_lora_ota; do
  is_supported_build_env "$pending" || fail "$pending qualification recipe missing"
  for selection in "$room_release_targets" "$repeater_release_targets" "$solar_all_targets" \
      "$solar_logging_targets" "$solar_menu_targets"; do
    if grep -Fxq "$pending" <<<"$selection"; then fail "$pending escaped qualification quarantine"; fi
  done
  (
    BUILD_PROFILE_FOR_TARGET=standard
    ESP32_FULL_BUILD=0
    BUILD_CAPABILITIES=() BUILD_EXPECTATIONS=() BUILD_APPLICATION_EXPECTATIONS=() BUILD_REDUCTIONS=()
    declare_build_capability_contract "$pending" NRF52_PLATFORM
    [[ " ${BUILD_CAPABILITIES[*]} " == *" hardware.qualification.pending "* ]] \
      || fail "$pending lacks its package publication prohibition"
  )
done
(
  BUILD_PROFILE_FOR_TARGET=standard
  ESP32_FULL_BUILD=0
  BUILD_CAPABILITIES=() BUILD_EXPECTATIONS=() BUILD_APPLICATION_EXPECTATIONS=() BUILD_REDUCTIONS=()
  declare_build_capability_contract Xiao_nrf52_repeater NRF52_PLATFORM
  [[ " ${BUILD_EXPECTATIONS[*]} " == *"bridge.rs232="* \
      && " ${BUILD_APPLICATION_EXPECTATIONS[*]} " == *"bridge.rs232.merged="* ]] \
    || fail "merged XIAO bridge lacks linked driver and packaged runtime proof"
)

sensor_release_targets=$(resolve_sensor_firmwares)
while IFS='|' read -r sensor_env sensor_ota_env; do
  is_supported_build_env "$sensor_ota_env" \
    || fail "$sensor_ota_env was not registered as a sensor LoRa OTA target"
  [ "$(get_pio_build_env "$sensor_ota_env")" = "$sensor_env" ] \
    || fail "$sensor_ota_env did not retain its sensor PlatformIO recipe"
  printf '%s\n' "$sensor_release_targets" | grep -Fx "$sensor_ota_env" >/dev/null \
    || fail "sensor release resolution omitted $sensor_ota_env"
  (
    BUILD_PROFILE_FOR_TARGET=standard
    ESP32_FULL_BUILD=0
    is_lora_ota_build "$sensor_ota_env" \
      || fail "$sensor_ota_env did not enable LoRa OTA"
  )
done <<'SENSOR_OTA_SPECS'
Heltec_t096_sensor|Heltec_t096_sensor_lora_ota_no_external_sensors
Heltec_t114_sensor|Heltec_t114_sensor_lora_ota_no_external_sensors
ProMicro_sensor|ProMicro_sensor_lora_ota_no_external_sensors
R1Neo_sensor|R1Neo_sensor_lora_ota_no_external_sensors
RAK_3401_sensor|RAK_3401_sensor_lora_ota_no_external_sensors
RAK_4631_sensor|RAK_4631_sensor_lora_ota_no_external_sensors
RAK_WisMesh_Tag_sensor|RAK_WisMesh_Tag_sensor_lora_ota_no_external_sensors
t1000e_sensor|t1000e_sensor_lora_ota_no_external_sensors
SENSOR_OTA_SPECS
for solar_env in solarxiao_30S_room_server_lora_ota solarxiao_33S_room_server_lora_ota; do
  is_supported_build_env "$solar_env" \
    || fail "$solar_env QSPI room-server OTA target was not registered"
  (
    BUILD_PROFILE_FOR_TARGET=standard
    ESP32_FULL_BUILD=0
    is_lora_ota_build "$solar_env" \
      || fail "$solar_env did not enable LoRa OTA"
  )
done
is_supported_build_env RAK_4631_room_server_ethernet_lora_ota_no_external_sensors \
  || fail "RAK4631 Ethernet room server lacked its lean LoRa OTA target"
if is_supported_build_env Heltec_v3_sensor_lora_ota_no_external_sensors; then
  fail "ESP32 sensors should use their Full OTA image"
fi

# build-profile-end: nrf52_room_and_qspi
}

build_profile_unsupported_ota_platforms() {
# build-profile-body: unsupported_ota_platforms
# The portable inflater is shared by every Arduino platform, but it does not
# supply the OTA manager or a supported bootloader/apply path. In particular,
# auto overlays for PicoW and RAK_3x72 must remain buildable without OTA.
for unsupported_ota_env in "${SUPPORTED_PIO_ENVS[@]}"; do
  case "${PIO_ENV_PLATFORM_BY_NAME[$unsupported_ota_env]}" in
    RP2040_PLATFORM|STM32_PLATFORM) ;;
    *) continue ;;
  esac
  [ "${PIO_ENV_OTA_BY_NAME[$unsupported_ota_env]:-0}" = 0 ] \
    || fail "$unsupported_ota_env incorrectly classified its shared inflater as LoRa OTA"
  for unsupported_ota_profile in auto standard; do
    (
      BUILD_PROFILE_FOR_TARGET=$unsupported_ota_profile
      if is_lora_ota_build "$unsupported_ota_env"; then
        fail "$unsupported_ota_env $unsupported_ota_profile enables unsupported LoRa OTA"
      fi
      PLATFORMIO_BUILD_FLAGS=""
      PLATFORMIO_BUILD_UNFLAGS=""
      apply_lora_ota_override "$unsupported_ota_env"
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"-UENABLE_OTA"* ]] \
        || fail "$unsupported_ota_env $unsupported_ota_profile failed to disable unsupported LoRa OTA"
      [[ "$PLATFORMIO_BUILD_FLAGS" != *"-DENABLE_OTA=1"* ]] \
        || fail "$unsupported_ota_env $unsupported_ota_profile emitted missing OTA implementation references"
      [[ "$PLATFORMIO_BUILD_FLAGS" != *"-DCOMPANION_FEATURE_OTA_CLI=1"* ]] \
        || fail "$unsupported_ota_env $unsupported_ota_profile enabled unavailable OTA controls"
    )
  done
done

# build-profile-end: unsupported_ota_platforms
}

build_profile_nrf52_ota_implementation() {
# build-profile-body: nrf52_ota_implementation
# An environment that includes only the tiny OTA inflater still needs the C++
# protocol implementation when build.sh overlays LoRa OTA. Room servers use
# the same nRF52 overlay as repeaters.
for reduced_nrf_ota_env in \
    RAK_3401_repeater_lora_ota_no_external_sensors \
    RAK_3401_room_server_lora_ota_no_external_sensors; do
  reduced_nrf_pio_env=$(get_pio_build_env "$reduced_nrf_ota_env")
  PLATFORMIO_BUILD_SRC_FILTER=""
  PLATFORMIO_EXTRA_SCRIPTS=""
  apply_nrf52_lora_ota_build_recipe "$reduced_nrf_ota_env" "$reduced_nrf_pio_env"
  if ! pio_env_option_contains "$reduced_nrf_pio_env" build_src_filter "helpers/ota/*.cpp" \
      && [[ "$PLATFORMIO_BUILD_SRC_FILTER" != *"helpers/ota/*.cpp"* ]]; then
    fail "$reduced_nrf_ota_env omitted the OTA C++ implementation"
  fi
done
unset PLATFORMIO_BUILD_SRC_FILTER PLATFORMIO_EXTRA_SCRIPTS

# build-profile-end: nrf52_ota_implementation
}

build_profile_portable_and_nrf52_storage() {
# build-profile-body: portable_and_nrf52_storage
# Synthetic inventory: ESP32 and nRF52 repeater/room-server targets with the
# same lean OTA policy. The sensor deliberately has no lean sibling.
SUPPORTED_PIO_ENVS=(
  esp_repeater
  esp_repeater_lora_ota_no_external_sensors
  esp_room_server
  esp_room_server_lora_ota_no_external_sensors
  nrf_repeater
  nrf_repeater_lora_ota_no_external_sensors
  nrf_room_server
  nrf_room_server_lora_ota_no_external_sensors
  nrf_qspi_repeater
  nrf_qspi_repeater_lora_ota_no_external_sensors
  nrf_qspi_room_server
  nrf_qspi_room_server_lora_ota_no_external_sensors
  nrf_sensor
)
PIO_ENV_PLATFORM_BY_NAME[esp_repeater]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_repeater_lora_ota_no_external_sensors]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_room_server]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[esp_room_server_lora_ota_no_external_sensors]=ESP32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_repeater_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_room_server]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_room_server_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_repeater]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_repeater_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_room_server]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_qspi_room_server_lora_ota_no_external_sensors]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_sensor]=NRF52_PLATFORM
PIO_ENV_QSPI_OTA_BY_NAME[nrf_qspi_repeater_lora_ota_no_external_sensors]=1
PIO_ENV_QSPI_OTA_BY_NAME[nrf_qspi_room_server_lora_ota_no_external_sensors]=1
PIO_ENV_FULL_BUILD_BY_NAME[esp_repeater]=1
PIO_ENV_FULL_BUILD_BY_NAME[esp_room_server]=1

RESOLVED_BUILD_TARGETS=(esp_repeater)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = full ] || fail "ESP32 auto did not prefer Full"
[ "$AUTO_PREFER_FULL_BUILD" = 1 ] || fail "ESP32 Full pass was not scheduled"
[ "$AUTO_REDUCED_FALLBACK_TARGET" = \
  esp_repeater_lora_ota_no_external_sensors ] \
  || fail "ESP32 reduced fallback was not resolved"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(esp_room_server)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = full ] || fail "ESP32 room server auto did not prefer Full"
[ "$AUTO_PREFER_FULL_BUILD" = 1 ] || fail "ESP32 room-server Full pass was not scheduled"
[ "$AUTO_REDUCED_FALLBACK_TARGET" = \
  esp_room_server_lora_ota_no_external_sensors ] \
  || fail "ESP32 room-server reduced fallback was not resolved"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_repeater)
configure_effective_build_profile build-firmware >/dev/null
[ "${RESOLVED_BUILD_TARGETS[0]}" = \
  nrf_repeater ] || fail "nRF52 profile configuration changed the selected target"
is_nrf52_sensor_ota_pair_target nrf_repeater \
  || fail "nRF52 repeater did not enter the required two-profile policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "nRF52 retained the permissive size-fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_room_server)
configure_effective_build_profile build-firmware >/dev/null
[ "${RESOLVED_BUILD_TARGETS[0]}" = \
  nrf_room_server ] || fail "nRF52 room profile configuration changed the selected target"
is_nrf52_sensor_ota_pair_target nrf_room_server \
  || fail "nRF52 room server did not enter the required two-profile policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "nRF52 room server retained the permissive size-fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_qspi_repeater)
configure_effective_build_profile build-firmware >/dev/null
is_nrf52_sensor_ota_pair_target nrf_qspi_repeater \
  || fail "external-storage nRF52 repeater did not enter the pair policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "external-storage nRF52 retained the old fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_qspi_room_server)
configure_effective_build_profile build-firmware >/dev/null
is_nrf52_sensor_ota_pair_target nrf_qspi_room_server \
  || fail "external-storage nRF52 room server did not enter the pair policy"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "external-storage nRF52 room server retained the old fallback policy"

BUILD_PROFILE_OVERRIDE=auto
BUILD_PROFILE_EXPLICIT=0
RESOLVED_BUILD_TARGETS=(nrf_sensor)
configure_effective_build_profile build-firmware >/dev/null
[ "$BUILD_PROFILE_EFFECTIVE" = auto ] \
  || fail "nRF52 sensor did not retain its ordinary auto profile"
[ "$AUTO_COMPLETE_FIRST_PASS" = 0 ] \
  || fail "nRF52 sensor retained the permissive size-fallback policy"
is_nrf52_sensor_ota_pair_target nrf_sensor \
  || fail "nRF52 sensor was excluded from the required pair policy"

# Ordinary USB-loggable roles compile their historical logging profile into
# the canonical artifact. LoRa OTA repeaters, KISS, and BLE keep their distinct
# stream/partition contracts. A standard ESP32 artifact still embeds logging
# when the same target also has an expanded FULL profile.
PIO_ENV_PLATFORM_BY_NAME[wio-e5-mini_repeater]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[RAK_3x72_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[Tiny_Relay_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[wio-e5-mini_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[wio-e5_companion_radio_usb]=STM32_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_kiss_modem]=NRF52_PLATFORM
PIO_ENV_PLATFORM_BY_NAME[nrf_companion_radio_ble]=NRF52_PLATFORM
uses_merged_standard_usb_logging nrf_sensor \
  || fail "ordinary nRF52 sensor omitted merged USB logging"
uses_merged_standard_usb_logging wio-e5-mini_repeater \
  || fail "size-constrained STM32 target omitted merged packet logging"
for constrained_companion in \
    RAK_3x72_companion_radio_usb \
    Tiny_Relay_companion_radio_usb \
    wio-e5-mini_companion_radio_usb \
    wio-e5_companion_radio_usb; do
  if uses_merged_standard_usb_logging "$constrained_companion"; then
    fail "$constrained_companion exceeded its 256 KiB flash with packet logging"
  fi
  is_logging_size_constrained_target "$constrained_companion" \
    || fail "$constrained_companion enabled oversized verbose mesh diagnostics"
done
if uses_merged_standard_usb_logging nrf_repeater_lora_ota_no_external_sensors; then
  fail "LoRa OTA repeater incorrectly merged USB logging"
fi
if uses_merged_standard_usb_logging nrf_kiss_modem; then
  fail "KISS target incorrectly merged plaintext USB logging"
fi
if uses_merged_standard_usb_logging nrf_companion_radio_ble; then
  fail "BLE Companion incorrectly merged USB logging"
fi
uses_merged_standard_usb_logging esp_repeater \
  || fail "standard ESP32 target omitted logging because FULL also exists"

# A standard ESP32 field image may omit LoRa OTA for size, but it must never
# also omit the compact browser updater. This is the exact profile combination
# that previously produced Heltec V4 repeaters where both `ota` and `start ota`
# were dead ends.
verify_esp32_field_browser_ota() {
  local env_name=$1
  local minimal_lora_ota=${2:-0}
  local PLATFORMIO_BUILD_FLAGS=""
  local PLATFORMIO_BUILD_UNFLAGS=""
  local BUILD_PROFILE_FOR_TARGET=standard
  local ESP32_FULL_BUILD=0
  local MESHDEBUG_OVERRIDE=off
  local PACKET_LOGGING_OVERRIDE=off
  local MQTT_BRIDGE_OVERRIDE=off
  local FIRMWARE_FILENAME_INFIX=""
  local PIO_CONFIG_JSON='[]'
  local -a BUILD_CAPABILITIES=()
  local -a BUILD_REDUCTIONS=()
  local -a BUILD_EXPECTATIONS=()
  local capabilities reductions expectations compact_flag

  PIO_ENV_PLATFORM_BY_NAME[$env_name]=ESP32_PLATFORM
  PIO_ENV_OTA_BY_NAME[$env_name]=$minimal_lora_ota
  apply_lora_ota_override "$env_name"
  apply_esp32_lora_ota_size_profile "$env_name"

  [[ "$PLATFORMIO_BUILD_FLAGS" == *-DLIGHTWEIGHT_WIFI_OTA=1* ]] \
    || fail "$env_name omitted its browser OTA fail-safe"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *-UDISABLE_WIFI_OTA* ]] \
    || fail "$env_name did not remove a board-level WiFi OTA disable"
  [[ "$PLATFORMIO_BUILD_FLAGS" != *-DDISABLE_WIFI_OTA=1* ]] \
    || fail "$env_name still explicitly disables browser OTA"
  capabilities=" ${BUILD_CAPABILITIES[*]} "
  [[ "$capabilities" == *" web.lightweight_browser_ota "* ]] \
    || fail "$env_name did not declare browser OTA"
  reductions=" ${BUILD_REDUCTIONS[*]} "
  [[ "$reductions" != *"web.browser_ota omitted"* ]] \
    || fail "$env_name still records browser OTA as omitted"

  declare_build_capability_contract "$env_name" ESP32_PLATFORM
  expectations=" ${BUILD_EXPECTATIONS[*]} "
  [[ "$expectations" == *"web.lightweight_browser_ota=MeshCore firmware update"* ]] \
    || fail "$env_name does not verify browser OTA in the linked image"
  for compact_flag in ED25519_COMPACT_BASE ED25519_COMPACT_SHA512 OTA_TARGET_NAME_FRONT_CODED; do
    if [ "$minimal_lora_ota" = 1 ]; then
      [[ "$PLATFORMIO_BUILD_FLAGS" == *"-D${compact_flag}=1"* ]] \
        || fail "$env_name did not enable its feature-preserving flash trim"
    else
      [[ "$PLATFORMIO_BUILD_FLAGS" != *"${compact_flag}"* ]] \
        || fail "$env_name changed the ordinary standard implementation"
    fi
  done
  if [ "$minimal_lora_ota" = 1 ]; then
    [[ "$PLATFORMIO_BUILD_FLAGS" == *-DENABLE_OTA=1* \
       && "$PLATFORMIO_BUILD_FLAGS" == *-DOTA_FLASH_STORE=1* \
       && "$PLATFORMIO_BUILD_FLAGS" == *-DOTA_FOLDER_SERIAL* ]] \
      || fail "$env_name removed a LoRa OTA or USB seeding feature to fit"
    [[ "$expectations" == *"ota.update.lora=image_hash MISMATCH after decode"* \
       && "$expectations" == *"ota.cli=OTA: status"* ]] \
      || fail "$env_name stopped verifying its LoRa OTA implementation"
  fi
}

verify_esp32_field_browser_ota heltec_v4_repeater
verify_esp32_field_browser_ota heltec_v4_room_server
verify_esp32_field_browser_ota heltec_v4_sensor
verify_esp32_field_browser_ota heltec_v4_repeater_lora_ota_no_external_sensors 1
verify_esp32_field_browser_ota heltec_v4_room_server_lora_ota_no_external_sensors 1

# The new storage representation is confined to portable ESP32 minimal OTA
# artifacts. Full images and other platforms retain their existing policies.
(
  PLATFORMIO_BUILD_FLAGS=""
  PLATFORMIO_BUILD_UNFLAGS=""
  BUILD_PROFILE_FOR_TARGET=full
  ESP32_FULL_BUILD=1
  apply_esp32_lora_ota_size_profile heltec_v4_repeater_lora_ota_no_external_sensors
  [ -z "$PLATFORMIO_BUILD_FLAGS" ] || fail "Full image received portable flash trims"
  BUILD_PROFILE_FOR_TARGET=standard
  ESP32_FULL_BUILD=0
  apply_esp32_lora_ota_size_profile nrf_repeater_lora_ota_no_external_sensors
  [ -z "$PLATFORMIO_BUILD_FLAGS" ] || fail "nRF52 image received ESP32 portable flash trims"
)

# Personal/attached roles can retain their established transport policy; the
# fail-safe is deliberately scoped to remotely installed field/server images.
PIO_ENV_PLATFORM_BY_NAME[heltec_v4_terminal_chat]=ESP32_PLATFORM
PLATFORMIO_BUILD_FLAGS=""
PLATFORMIO_BUILD_UNFLAGS=""
BUILD_PROFILE_FOR_TARGET=standard
BUILD_CAPABILITIES=()
BUILD_REDUCTIONS=()
apply_esp32_lora_ota_size_profile heltec_v4_terminal_chat
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DDISABLE_WIFI_OTA=1* ]] \
  || fail "attached terminal unexpectedly changed its WiFi policy"

PLATFORMIO_BUILD_FLAGS=""
MESHDEBUG_OVERRIDE=""
PACKET_LOGGING_OVERRIDE=""
DISABLE_DEBUG=0
apply_merged_standard_usb_logging_profile nrf_sensor
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DMESH_PACKET_LOGGING=1* ]] \
  || fail "merged profile omitted packet logging"
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DMESH_DEBUG=1* ]] \
  || fail "merged profile omitted mesh diagnostics"
PLATFORMIO_BUILD_FLAGS=""
apply_merged_standard_usb_logging_profile wio-e5-mini_repeater
[[ "$PLATFORMIO_BUILD_FLAGS" == *-DMESH_PACKET_LOGGING=1* ]] \
  || fail "constrained merged profile omitted packet logging"
[[ "$PLATFORMIO_BUILD_FLAGS" != *-DMESH_DEBUG=1* ]] \
  || fail "constrained merged profile enabled oversized mesh diagnostics"
if declare -f run_logging_matrix_build_targets \
    | grep -q 'FIRMWARE_FILENAME_INFIX="logging"'; then
  fail "logging matrix still emits a separate standard logging artifact"
fi
if declare -f get_firmware_filename | grep -q 'filename_infix="logging"'; then
  fail "explicit packet logging still renames the ordinary artifact"
fi
FIRMWARE_FILENAME_INFIX=""
ESP32_FULL_BUILD=0
PACKET_LOGGING_OVERRIDE="on"
MQTT_BRIDGE_OVERRIDE="off"
[ "$(get_firmware_filename nrf_sensor vtest)" = "nrf_sensor-vtest" ] \
  || fail "packet logging still creates a separately named artifact"
PACKET_LOGGING_OVERRIDE=""
MQTT_BRIDGE_OVERRIDE=""
if declare -f run_logging_matrix_build_targets | grep -q 'Profile 2'; then
  fail "logging matrix still labels a separate Profile 2"
fi

# Option 3 must not publish an oversized standard ESP32 image, but its expected
# portable-slot status is satisfied by the mandatory expanded FULL pass.
saved_has_esp32_full_profile=$(declare -f has_esp32_full_profile)
saved_build_firmware=$(declare -f build_firmware)
has_esp32_full_profile() { [ "$1" = esp32_portable_overflow ]; }
build_firmware() { return 42; }
saved_output_dir=$OUTPUT_DIR
OUTPUT_DIR=$version_test_dir
LOGGING_MATRIX_FAILURES=()
LOGGING_MATRIX_DEFERRED_TARGETS=()
DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL=1
FIRMWARE_FILENAME_INFIX=""
MQTT_BRIDGE_OVERRIDE="off"
if ! run_logged_build_targets esp32_portable_overflow >/dev/null; then
  fail "portable ESP32 overflow made the logging pass fail"
fi
[ ${#LOGGING_MATRIX_FAILURES[@]} -eq 0 ] \
  || fail "portable ESP32 overflow remained a logging-matrix failure"
[ "${LOGGING_MATRIX_DEFERRED_TARGETS[*]}" = esp32_portable_overflow ] \
  || fail "portable ESP32 overflow was not deferred to FULL"
OUTPUT_DIR=$saved_output_dir
unset DEFER_ESP32_PORTABLE_OVERFLOW_TO_FULL
eval "$saved_has_esp32_full_profile"
eval "$saved_build_firmware"

for reduced_full_base in Tbeam_repeater Tbeam_SX1262_repeater Tbeam_SX1276_repeater; do
  reduced_full_alias="${reduced_full_base}_lora_ota_no_external_sensors"
  [ "$(get_esp32_full_profile_target "$reduced_full_alias")" = "$reduced_full_base" ] \
    || fail "reduced ESP32 LoRa OTA alias changed its FULL target: $reduced_full_alias"
  has_esp32_full_profile "$reduced_full_alias" \
    || fail "reduced ESP32 LoRa OTA alias lost its expanded FULL replacement: $reduced_full_alias"
done

calls=()
saved_build_firmware_one_profile=$(declare -f build_firmware_one_profile)
build_firmware_one_profile() {
  calls+=("$1:$BUILD_PROFILE_EFFECTIVE:$SKIP_DECLARED_REDUCTIONS:$NRF52_OTA_SENSOR_PROFILE")
  if [ "${#calls[@]}" -eq 1 ]; then
    return 42
  fi
  return 0
}

AUTO_PREFER_FULL_BUILD=0
AUTO_COMPLETE_FIRST_PASS=1
AUTO_REDUCED_FALLBACK_TARGET=nrf_repeater_lora_ota_no_external_sensors
BUILD_PROFILE_EFFECTIVE=auto
FIRMWARE_FILENAME_INFIX=""
SKIP_DECLARED_REDUCTIONS=0
if run_auto_two_pass_build nrf_repeater_lora_ota_no_external_sensors >/dev/null 2>&1; then
  fail "failed full nRF52 option was hidden by a successful reduced fallback"
fi

[ "${#calls[@]}" -eq 2 ] || fail "size overflow did not run exactly two passes"
[[ "${calls[0]}" == *:auto:1:* ]] || fail "pass 1 unexpectedly applied reductions"
[[ "${calls[1]}" == *:auto:0:reduced ]] || fail "pass 2 did not apply reduced sensor policy"

calls=()
build_firmware_one_profile() {
  calls+=("$1:$BUILD_PROFILE_EFFECTIVE:$SKIP_DECLARED_REDUCTIONS:$NRF52_OTA_SENSOR_PROFILE")
  return 0
}

AUTO_PUBLISH_REDUCED_SECOND_PASS=1
BUILD_PROFILE_EFFECTIVE=auto
FIRMWARE_FILENAME_INFIX=""
FIRMWARE_OUTPUT_ENV_NAME=""
SKIP_DECLARED_REDUCTIONS=0
run_auto_two_pass_build nrf_repeater_lora_ota_no_external_sensors >/dev/null

[ "${#calls[@]}" -eq 2 ] \
  || fail "successful internal-flash nRF52 build did not publish both artifacts"
[[ "${calls[0]}" == *:auto:1:full ]] \
  || fail "complete nRF52 artifact did not declare the full sensor policy"
[[ "${calls[1]}" == *:auto:0:reduced ]] \
  || fail "reduced nRF52 artifact did not declare the reduced sensor policy"
eval "$saved_build_firmware_one_profile"

is_rak_i2c_voltage_monitor_ota_target \
  RAK_3401_repeater_lora_ota_no_external_sensors \
  || fail "RAK3401 reduced OTA target did not retain voltage monitors"
is_rak_i2c_voltage_monitor_ota_target \
  RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors \
  || fail "RAK4631 reduced OTA target did not retain voltage monitors"
if is_rak_i2c_voltage_monitor_ota_target \
    Heltec_t114_repeater_lora_ota_no_external_sensors; then
  fail "non-RAK reduced OTA target incorrectly retained voltage monitors"
fi

verify_reduced_sensor_flags() {
  local env_name=$1
  local expect_ina=$2
  local PLATFORMIO_BUILD_FLAGS=""
  local PLATFORMIO_BUILD_UNFLAGS=""
  local -a BUILD_REDUCTIONS=()

  PIO_ENV_OTA_BY_NAME[$env_name]=1
  apply_lora_ota_no_external_sensors_profile "$env_name"
  [[ "$PLATFORMIO_BUILD_FLAGS" == *-UENV_INCLUDE_BME280* ]] \
    || fail "$env_name did not omit environmental sensors"
  if [ "$expect_ina" = 1 ]; then
    [[ "$PLATFORMIO_BUILD_FLAGS" == *-DMESH_OTA_LOCAL_TARGET_NAME_ONLY=1* ]] \
      || fail "$env_name retained the all-board OTA name catalog"
    [[ " ${BUILD_REDUCTIONS[*]} " == *"ota target-name catalog omitted"* ]] \
      || fail "$env_name did not declare its OTA name reduction"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *-DENV_INCLUDE_INA219=1* ]] \
      || fail "$env_name did not retain INA monitor support"
    [[ "$PLATFORMIO_BUILD_FLAGS" != *-UENV_INCLUDE_INA219* ]] \
      || fail "$env_name both retained and omitted INA monitor support"
  else
    [[ "$PLATFORMIO_BUILD_FLAGS" != *MESH_OTA_LOCAL_TARGET_NAME_ONLY* ]] \
      || fail "$env_name lost OTA names outside the reduced RAK profile"
    [[ "$PLATFORMIO_BUILD_FLAGS" == *-UENV_INCLUDE_INA219* ]] \
      || fail "$env_name incorrectly retained INA monitor support"
  fi
}

verify_reduced_sensor_flags \
  RAK_3401_repeater_lora_ota_no_external_sensors 1
verify_reduced_sensor_flags \
  RAK_3401_room_server_lora_ota_no_external_sensors 1
verify_reduced_sensor_flags \
  RAK_4631_repeater_lora_ota_no_external_sensors 1
verify_reduced_sensor_flags \
  RAK_4631_sensor_lora_ota_no_external_sensors 1
verify_reduced_sensor_flags \
  Heltec_t114_repeater_lora_ota_no_external_sensors 0

# build-profile-end: portable_and_nrf52_storage
}

