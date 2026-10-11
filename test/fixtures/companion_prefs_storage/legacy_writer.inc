// Frozen sequential preference serializer from 54e5b0b99.
// Independent of the new storage table; keep its historical field order.
bool DataStore::savePrefsLegacy(const CompanionNodePrefs& _prefs, double node_lat, double node_lon) {
  if (_prefs_load_incomplete) return false;
#if defined(ESP32_PLATFORM)
  if (!promoteCompanionRecoveryFile(_fs, "/new_prefs", _prefs_recovery_source)) return false;
#endif
#if defined(NRF52_PLATFORM)
  if (_primary_storage_unavailable) return false;
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  mesh::AtomicFileWriter file(_fs, "/new_prefs");
#elif defined(ESP32_PLATFORM)
  mesh::ContactFileTransaction file(_fs, "/new_prefs", companionPathPresence);
#elif defined(RP2040_PLATFORM)
  mesh::ContactFileTransaction file(_fs, "/new_prefs");
#else
  File file = openWrite(_fs, "/new_prefs");
#endif
  if (file) {
    const auto writeField = [&file](const void* data, size_t size) -> bool {
      return file.write(static_cast<const uint8_t*>(data), size) == size;
    };
    uint8_t pad[8];
    memset(pad, 0, sizeof(pad));

    bool success = writeField((uint8_t *)&_prefs.airtime_factor, sizeof(float)); // 0
    success = success && writeField((uint8_t *)_prefs.node_name, sizeof(_prefs.node_name)); // 4
    success = success && writeField(pad, 4);                                            // 36
    success = success && writeField((uint8_t *)&node_lat, sizeof(node_lat)); // 40
    success = success && writeField((uint8_t *)&node_lon, sizeof(node_lon)); // 48
    success = success && writeField((uint8_t *)&_prefs.freq, sizeof(_prefs.freq)); // 56
    success = success && writeField((uint8_t *)&_prefs.sf, sizeof(_prefs.sf)); // 60
    success = success && writeField((uint8_t *)&_prefs.cr, sizeof(_prefs.cr)); // 61
    success = success && writeField((uint8_t *)&_prefs.client_repeat, sizeof(_prefs.client_repeat)); // 62
    success = success && writeField((uint8_t *)&_prefs.manual_add_contacts, sizeof(_prefs.manual_add_contacts)); // 63
    success = success && writeField((uint8_t *)&_prefs.bw, sizeof(_prefs.bw)); // 64
    success = success && writeField((uint8_t *)&_prefs.tx_power_dbm, sizeof(_prefs.tx_power_dbm)); // 68
    success = success && writeField((uint8_t *)&_prefs.telemetry_mode_base, sizeof(_prefs.telemetry_mode_base)); // 69
    success = success && writeField((uint8_t *)&_prefs.telemetry_mode_loc, sizeof(_prefs.telemetry_mode_loc)); // 70
    success = success && writeField((uint8_t *)&_prefs.telemetry_mode_env, sizeof(_prefs.telemetry_mode_env)); // 71
    success = success && writeField((uint8_t *)&_prefs.rx_delay_base, sizeof(_prefs.rx_delay_base)); // 72
    success = success && writeField((uint8_t *)&_prefs.advert_loc_policy, sizeof(_prefs.advert_loc_policy)); // 76
    success = success && writeField((uint8_t *)&_prefs.multi_acks, sizeof(_prefs.multi_acks)); // 77
    success = success && writeField((uint8_t *)&_prefs.path_hash_mode, sizeof(_prefs.path_hash_mode)); // 78
    success = success && writeField(pad, 1);                                            // 79
    success = success && writeField((uint8_t *)&_prefs.ble_pin, sizeof(_prefs.ble_pin)); // 80
    success = success && writeField((uint8_t *)&_prefs.buzzer_quiet, sizeof(_prefs.buzzer_quiet)); // 84
    success = success && writeField((uint8_t *)&_prefs.gps_enabled, sizeof(_prefs.gps_enabled)); // 85
    success = success && writeField((uint8_t *)&_prefs.gps_interval, sizeof(_prefs.gps_interval)); // 86
    success = success && writeField((uint8_t *)&_prefs.autoadd_config, sizeof(_prefs.autoadd_config)); // 87
    success = success && writeField((uint8_t *)&_prefs.autoadd_max_hops, sizeof(_prefs.autoadd_max_hops)); // 88
    success = success && writeField((uint8_t *)&_prefs.rx_boosted_gain, sizeof(_prefs.rx_boosted_gain)); // 89
    success = success && writeField((uint8_t *)_prefs.default_scope_name, sizeof(_prefs.default_scope_name)); // 90
    success = success && writeField((uint8_t *)_prefs.default_scope_key, sizeof(_prefs.default_scope_key)); // 121
    success = success && writeField((uint8_t *)&_prefs.radio_fem_rxgain, sizeof(_prefs.radio_fem_rxgain)); // 122
    success = success && writeField((uint8_t *)&_prefs.radio_fem_rxgain_override,
               sizeof(_prefs.radio_fem_rxgain_override)); // 123
    success = success && writeField((uint8_t *)&_prefs.vibe_quiet,
               sizeof(_prefs.vibe_quiet));                    // 124
    success = success && writeField((uint8_t *)&_prefs.radio_fem_txgain,
               sizeof(_prefs.radio_fem_txgain));        // 125
    success = success && writeField((uint8_t *)&_prefs.rx_powersaving_enabled,
               sizeof(_prefs.rx_powersaving_enabled)); // 126
    success = success && writeField((uint8_t *)&_prefs.rx_ps_rx_us,
               sizeof(_prefs.rx_ps_rx_us));                  // 127
    success = success && writeField((uint8_t *)&_prefs.rx_ps_sleep_us,
               sizeof(_prefs.rx_ps_sleep_us));            // 131
    success = success && writeField((uint8_t *)&_prefs.rx_ps_level,
               sizeof(_prefs.rx_ps_level));                  // 135
    success = success && writeField((uint8_t *)&_prefs.rx_ps_preamble,
               sizeof(_prefs.rx_ps_preamble));            // 136
    success = success && writeField((uint8_t *)&_prefs.powersaving_enabled,
               sizeof(_prefs.powersaving_enabled)); // 137
    success = success && writeField((uint8_t *)&_prefs.wifi_enabled,
               sizeof(_prefs.wifi_enabled));               // 138
    success = success && writeField((uint8_t *)&_prefs.powersaving_policy_version,
               sizeof(_prefs.powersaving_policy_version));                              // 139
    success = success && writeField((uint8_t *)&_prefs.usb_logging_enabled,
               sizeof(_prefs.usb_logging_enabled));                                    // 140
    success = success && writeField((uint8_t *)_prefs.bluetooth_name,
               sizeof(_prefs.bluetooth_name));          // 141
    success = success && writeField(
               (uint8_t *)&_prefs.display_rotation_degrees,
               sizeof(_prefs.display_rotation_degrees));
    success = success && writeField((uint8_t *)&_prefs.cad_enabled,
               sizeof(_prefs.cad_enabled));
    success = success && writeField((uint8_t *)&_prefs.cad_scan_timeout_ms,
               sizeof(_prefs.cad_scan_timeout_ms));
    success = success && writeField((uint8_t *)&_prefs.cad_retry_delay_ms,
               sizeof(_prefs.cad_retry_delay_ms));
    success = success && writeField((uint8_t *)&_prefs.cad_max_duration_ms,
               sizeof(_prefs.cad_max_duration_ms));
    success = success && writeField((uint8_t *)&_prefs.bluetooth_mac_mode,
               sizeof(_prefs.bluetooth_mac_mode));
    success = success && writeField((uint8_t *)_prefs.bluetooth_mac,
               sizeof(_prefs.bluetooth_mac));
    success = success && writeField(
               (uint8_t *)&_prefs.bluetooth_stealth_peer_type,
               sizeof(_prefs.bluetooth_stealth_peer_type));
    success = success && writeField(
               (uint8_t *)_prefs.bluetooth_stealth_peer,
               sizeof(_prefs.bluetooth_stealth_peer));
    success = success && writeField(
               (uint8_t *)&_prefs.bluetooth_stealth_mode,
               sizeof(_prefs.bluetooth_stealth_mode));

    success = success && writeField((uint8_t *)&_prefs.tx_delay_factor,
        sizeof(_prefs.tx_delay_factor));
    success = success && writeField((uint8_t *)&_prefs.direct_tx_delay_factor,
        sizeof(_prefs.direct_tx_delay_factor));
    success = success && writeField((uint8_t *)&_prefs.interference_threshold,
        sizeof(_prefs.interference_threshold));
    success = success && writeField((uint8_t *)&_prefs.agc_reset_interval,
        sizeof(_prefs.agc_reset_interval));
    success = success && writeField((uint8_t *)&_prefs.tz_offset,
        sizeof(_prefs.tz_offset));
#ifdef TBEAM_1W
    success = success && writeField((uint8_t *)_prefs.fan_mode,
        sizeof(_prefs.fan_mode));
    success = success && writeField((uint8_t *)&_prefs.fan_lo,
        sizeof(_prefs.fan_lo));
    success = success && writeField((uint8_t *)&_prefs.fan_hi,
        sizeof(_prefs.fan_hi));
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
    success = success && writeField((uint8_t *)_prefs.wifi_ssid,
        sizeof(_prefs.wifi_ssid));
    success = success && writeField((uint8_t *)_prefs.wifi_pwd,
        sizeof(_prefs.wifi_pwd));
#endif

    success = success && file.write((uint8_t *)&_prefs.flood_retry_attempts, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.flood_retry_max_path, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.flood_retry_group_max_path, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.flood_retry_advert_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.one_key_dm_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.bluetooth_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.gps_sync_interval_hours, 2) == 2;
    success = success && file.write((uint8_t *)&_prefs.usb_debug_enabled, 1) == 1;
    success = success && file.write((uint8_t *)&_prefs.lost_reply, 1) == 1;

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) || defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
    success = file.commit(success);
    if (!success) MESH_DEBUG_PRINTLN("DataStore: atomic preferences write failed");
#else
    file.close();
#endif
    return success;
  }
  return false;
}
