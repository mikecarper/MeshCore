# Assertion sections for shared. Called by test_build_profiles.sh.
# Original section bodies stay byte-for-byte intact; see build_profile_sections.json.

BUILD_PROFILE_TEST_SECTIONS=(
  'esp32-release|debug_and_release_utilities'
  'nrf52-storage|reduced_rak_sensors'
  'esp32-release|esp32_runtime_policy'
  'esp32-release|output_versions'
  'esp32-release|resolved_board_inheritance'
  'shared|metadata'
  'companion-transport|wio_full_display_base'
  'esp32-release|interactive_release_menu'
  'companion-transport|cardputer_companion_aliases'
  'esp32-release|esp32_canonical_release'
  'companion-transport|companion_build_routing'
  'esp32-release|release_matrix_scheduling'
  'esp32-release|esp32_full_ota_roles'
  'nrf52-storage|nrf52_room_and_qspi'
  'companion-transport|kiss_transport_boundaries'
  'nrf52-storage|unsupported_ota_platforms'
  'companion-transport|t1000_declared_ota'
  'esp32-release|esp32_observer_identity'
  'companion-transport|full_companion_overlays'
  'nrf52-storage|nrf52_ota_implementation'
  'esp32-release|esp32_async_tcp'
  'companion-transport|companion_mqtt_selection'
  'esp32-release|esp32_logging_contract'
  'esp32-release|esp32_merged_bridges'
  'nrf52-storage|portable_and_nrf52_storage'
  'companion-transport|companion_transport_contracts'
  'shared|finish'
)

build_profile_metadata() {
# build-profile-body: metadata
# Every qualified ESP32 Companion layout publishes one Full image even when
# its historical recipe had no separately named WiFi target. The transport
# aliases remain directly buildable, but canonical release resolution must
# replace each with that exact board's Full target.
init_project_context >/dev/null

# build-profile-end: metadata
}

build_profile_finish() {
# build-profile-body: finish
echo "test_build_profiles: OK"
# build-profile-end: finish
}

# The original version-name assertions create this scratch directory in the
# ESP32 suite. Its later synthetic overflow checks also need an empty directory
# when the nRF52 suite runs independently; no assertions are repeated here.
prepare_build_profile_suite() {
  if [ "$BUILD_PROFILE_TEST_SUITE" = nrf52-storage ]; then
    version_test_dir=$(mktemp -d)
    trap 'rm -rf -- "$version_test_dir"' EXIT
  fi
}
