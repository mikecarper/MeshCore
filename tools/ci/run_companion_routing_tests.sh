#!/usr/bin/env bash
# Each CI shard has its own checkout; tests inside a shard stay sequential.
set -euo pipefail

usage() {
  echo "Usage: $0 {transport|preferences|storage}" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

shard=$1
case "$shard" in
  transport)
    scripts=(
      test/test_companion_channel_alias.py
      test/test_companion_tx_routing.py
      test/test_companion_channel_publication.py
      test/test_companion_tx_raw_alias.py
      test/test_companion_traffic_routing.py
      test/test_host_kiss_radio.py
      test/test_host_kiss_infrastructure.py
      test/test_host_kiss.py
      test/test_host_kiss_usb.py
      test/test_host_kiss_ingress.py
      test/test_host_kiss_capabilities.py
      test/test_host_kiss_ram.py
      test/test_ethernet_kiss.py
      test/test_ethernet_radio.py
      test/test_companion_ethernet.py
      test/test_companion_discovery.py
      test/test_repeater_discovery.py
      test/test_repeater_discovery_settings.py
      test/test_companion_discovery_correlation.py
      test/test_pico_ble_queues.py
      test/test_companion_response_bounds.py
      test/test_companion_rx_via.py
      test/test_companion_ack_delivery.py
      test/test_companion_lost_reply.py
      test/test_companion_delayed_reply_delivery.py
      test/test_companion_scheduled_reboot.py
      test/test_trace_profile_timing.py
      test/test_companion_hardware_serial.py
      test/test_companion_hardware_serial_gps.py
      test/test_espnow_raw_initialization.py
    )
    ;;
  preferences)
    scripts=(
      test/test_companion_preferences_transaction.py
      test/test_companion_prefs_values.py
      test/test_companion_prefs_image.py
      test/test_companion_timezone_prefs.py
      test/test_companion_prefs_tail.py
      test/test_companion_radio_settings_transaction.py
      test/test_companion_prefs_transactions.py
      test/test_companion_primary_radio_persistence.py
      test/test_companion_ota_config.py
      test/test_ota_identity_policy.py
      test/test_companion_radio_gain_restore.py
      test/test_room_sensor_radio_restore.py
      test/test_common_radio_persistence.py
      test/test_infrastructure_radio_apply.py
      test/test_common_cli_save_results.py
      test/test_common_cli_tx_pending.py
      test/test_radio_rx_settings_transaction.py
      test/test_common_prefs_uncertain_reply.py
      test/test_common_prefs_apply_reply.py
      test/test_common_prefs_commit.py
      test/test_common_cli_retry_tail.py
      test/test_common_prefs_legacy_rxgain.py
      test/test_flood_policy_capacity.py
      test/test_wardriving_default_migration.py
      test/test_flood_rule_layout.py
      test/test_mqtt_prefs_commit.py
    )
    ;;
  storage)
    scripts=(
      test/test_companion_contact_lifetime.py
      test/test_contact_indices.py
      test/test_small_table_sort.py
      test/test_neighbor_resident_state.py
      test/test_environment_sensor_references.py
      test/test_companion_identity_cli.py
      test/test_companion_packet_stream.py
      test/test_companion_uncached_storage.py
      test/test_companion_contact_partial_tail.py
      test/test_companion_channel_storage.py
      test/test_nrf52_extrafs_contract.py
      test/test_xiao_qspi_recovery.py
      test/test_nrf52_qspi_dma_fix.py
      test/test_xiao_identity_recovery.py
      test/test_identity_and_settings_recovery.py
      test/test_contact_cache.py
      test/test_one_key_dm_shared_queue.py
      test/test_one_key_dm_compact_held.py
      test/test_companion_shared_ota_capacity.py
    )
    ;;
  *)
    usage
    exit 2
    ;;
esac

ci_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
cd -- "$ci_root"

for script in "${scripts[@]}"; do
  printf 'Companion routing [%s]: %s\n' "$shard" "$script"
  python3 -B "$script" -v
done
