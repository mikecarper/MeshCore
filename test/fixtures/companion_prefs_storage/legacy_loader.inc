// Frozen sequential preference serializer from 54e5b0b99.
// Independent of the new storage table; historical fields stay unchanged.
// New append-only tails are added explicitly to this scalar reference.
bool DataStore::loadPrefsLegacy(const char *filename,
                             CompanionNodePrefs& _prefs, double& node_lat,
                             double& node_lon) {
  File file = openRead(_fs, filename);
  if (file) {
    CompanionNodePrefs loaded_prefs = _prefs;
    // Files written before this tail existed always started Bluetooth at boot.
    // Do not inherit a runtime off value when loading one of those images.
    loaded_prefs.bluetooth_enabled = 1;
    loaded_prefs.gps_sync_interval_hours = 0;
    // Older images have no independent debug preference. Never inherit a
    // runtime-on value when loading one of those images.
    loaded_prefs.usb_debug_enabled = 0;
    loaded_prefs.lost_reply = 0;
    double loaded_lat = node_lat;
    double loaded_lon = node_lon;
    // The original image ended after ble_pin at byte 84. Later releases only
    // appended fields, sometimes as an indivisible group. Accept every format
    // actually emitted by those releases, but reject a truncated field/group
    // or an unknown tail before any value reaches the live preferences.
    static const uint32_t MIN_PREFS_SIZE = 84;
    static const uint16_t KNOWN_PREFS_SIZES[] = {
        84, 85, 90, 91, 92, 93, 140, 141, 142, 143, 144, 155,
        156, 157, 158, 159,
        159 + sizeof(loaded_prefs.bluetooth_name),
        161 + sizeof(loaded_prefs.bluetooth_name),
        168 + sizeof(loaded_prefs.bluetooth_name),
        168 + sizeof(loaded_prefs.bluetooth_name)
            + sizeof(loaded_prefs.bluetooth_mac_mode)
            + sizeof(loaded_prefs.bluetooth_mac),
        168 + sizeof(loaded_prefs.bluetooth_name)
            + sizeof(loaded_prefs.bluetooth_mac_mode)
            + sizeof(loaded_prefs.bluetooth_mac)
            + sizeof(loaded_prefs.bluetooth_stealth_peer_type)
            + sizeof(loaded_prefs.bluetooth_stealth_peer),
        168 + sizeof(loaded_prefs.bluetooth_name)
            + sizeof(loaded_prefs.bluetooth_mac_mode)
            + sizeof(loaded_prefs.bluetooth_mac)
            + sizeof(loaded_prefs.bluetooth_stealth_peer_type)
            + sizeof(loaded_prefs.bluetooth_stealth_peer)
            + sizeof(loaded_prefs.bluetooth_stealth_mode),
        226,  // prior 215-byte image plus radio timing, interference, AGC and timezone
#ifdef TBEAM_1W
        233,  // fan mode and low/high thermistor thresholds
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        323,  // Pico W additionally persists its SSID/password (ESP32 uses NVS)
#endif
        230,  // Companion flood retry controls, appended after prior fields
#ifdef TBEAM_1W
        237,
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        327,
#endif
        231,  // One-key DM consent policy, appended after flood retry controls
#ifdef TBEAM_1W
        238,
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        328,
#endif
#ifdef TBEAM_1W
        239,  // Bluetooth enable preference, appended after one-key DM policy
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        329,
#else
        232,
#endif
#ifdef TBEAM_1W
        241,  // GPS time-sync cadence in hours, appended after Bluetooth enable
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        331,
#else
        234,
#endif
#ifdef TBEAM_1W
        242,  // Independent USB debug preference, after the published GPS tail
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        332,
#else
        235,
#endif
#ifdef TBEAM_1W
        243,
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
        333,
#else
        236,
#endif
    };
    const uint32_t prefs_size = file.size();
    bool known_size = false;
    for (size_t i = 0;
         i < sizeof(KNOWN_PREFS_SIZES) / sizeof(KNOWN_PREFS_SIZES[0]); i++) {
      if (prefs_size == KNOWN_PREFS_SIZES[i]) {
        known_size = true;
        break;
      }
    }
    if (prefs_size < MIN_PREFS_SIZE || !known_size) {
      file.close();
      return false;
    }
    bool success = true;
    auto readField = [&file, &success](void* dest, size_t size) -> bool {
      if (!success
          || file.read(static_cast<uint8_t*>(dest), size) != size) {
        success = false;
        return false;
      }
      return true;
    };
    auto readOptionalField = [&file, &readField](void* dest,
                                                 size_t size) -> bool {
      return file.available() == 0 || readField(dest, size);
    };
    uint8_t pad[8];

    readField(&loaded_prefs.airtime_factor, sizeof(float));                                // 0
    readField(loaded_prefs.node_name, sizeof(loaded_prefs.node_name));                      // 4
    readField(pad, 4);                                                                      // 36
    readField(&loaded_lat, sizeof(loaded_lat));                                             // 40
    readField(&loaded_lon, sizeof(loaded_lon));                                             // 48
    readField(&loaded_prefs.freq, sizeof(loaded_prefs.freq));                               // 56
    readField(&loaded_prefs.sf, sizeof(loaded_prefs.sf));                                   // 60
    readField(&loaded_prefs.cr, sizeof(loaded_prefs.cr));                                   // 61
    readField(&loaded_prefs.client_repeat, sizeof(loaded_prefs.client_repeat));             // 62
    readField(&loaded_prefs.manual_add_contacts, sizeof(loaded_prefs.manual_add_contacts)); // 63
    readField(&loaded_prefs.bw, sizeof(loaded_prefs.bw));                                   // 64
    readField(&loaded_prefs.tx_power_dbm, sizeof(loaded_prefs.tx_power_dbm));               // 68
    readField(&loaded_prefs.telemetry_mode_base, sizeof(loaded_prefs.telemetry_mode_base)); // 69
    readField(&loaded_prefs.telemetry_mode_loc, sizeof(loaded_prefs.telemetry_mode_loc));   // 70
    readField(&loaded_prefs.telemetry_mode_env, sizeof(loaded_prefs.telemetry_mode_env));   // 71
    readField(&loaded_prefs.rx_delay_base, sizeof(loaded_prefs.rx_delay_base));             // 72
    readField(&loaded_prefs.advert_loc_policy, sizeof(loaded_prefs.advert_loc_policy));     // 76
    readField(&loaded_prefs.multi_acks, sizeof(loaded_prefs.multi_acks));                   // 77
    readField(&loaded_prefs.path_hash_mode, sizeof(loaded_prefs.path_hash_mode));           // 78
    readField(pad, 1);                                                                      // 79
    readField(&loaded_prefs.ble_pin, sizeof(loaded_prefs.ble_pin));                         // 80
    readOptionalField(&loaded_prefs.buzzer_quiet,
                      sizeof(loaded_prefs.buzzer_quiet));                                  // 84
    readOptionalField(&loaded_prefs.gps_enabled,
                      sizeof(loaded_prefs.gps_enabled));                                   // 85
    readOptionalField(&loaded_prefs.gps_interval,
                      sizeof(loaded_prefs.gps_interval));                                  // 86
    readOptionalField(&loaded_prefs.autoadd_config,
                      sizeof(loaded_prefs.autoadd_config));                                // 90
    readOptionalField(&loaded_prefs.autoadd_max_hops,
                      sizeof(loaded_prefs.autoadd_max_hops));                              // 91
    readOptionalField(&loaded_prefs.rx_boosted_gain,
                      sizeof(loaded_prefs.rx_boosted_gain));                               // 92
    readOptionalField(loaded_prefs.default_scope_name,
                      sizeof(loaded_prefs.default_scope_name));                            // 93
    readOptionalField(loaded_prefs.default_scope_key,
                      sizeof(loaded_prefs.default_scope_key));                             // 124
    readOptionalField(&loaded_prefs.radio_fem_rxgain,
                      sizeof(loaded_prefs.radio_fem_rxgain));                              // 140
    readOptionalField(&loaded_prefs.radio_fem_rxgain_override,
                      sizeof(loaded_prefs.radio_fem_rxgain_override));                     // 141
    readOptionalField(&loaded_prefs.vibe_quiet,
                      sizeof(loaded_prefs.vibe_quiet));                                    // 142
    readOptionalField(&loaded_prefs.radio_fem_txgain,
                      sizeof(loaded_prefs.radio_fem_txgain));                              // 143
    readOptionalField(&loaded_prefs.rx_powersaving_enabled,
                      sizeof(loaded_prefs.rx_powersaving_enabled));                        // 144
    readOptionalField(&loaded_prefs.rx_ps_rx_us,
                      sizeof(loaded_prefs.rx_ps_rx_us));                                   // 145
    readOptionalField(&loaded_prefs.rx_ps_sleep_us,
                      sizeof(loaded_prefs.rx_ps_sleep_us));                                // 149
    readOptionalField(&loaded_prefs.rx_ps_level,
                      sizeof(loaded_prefs.rx_ps_level));                                   // 153
    readOptionalField(&loaded_prefs.rx_ps_preamble,
                      sizeof(loaded_prefs.rx_ps_preamble));                                // 154
    readOptionalField(&loaded_prefs.powersaving_enabled,
                      sizeof(loaded_prefs.powersaving_enabled));                           // 155
    readOptionalField(&loaded_prefs.wifi_enabled,
                      sizeof(loaded_prefs.wifi_enabled));                                  // 156
    readOptionalField(&loaded_prefs.powersaving_policy_version,
                      sizeof(loaded_prefs.powersaving_policy_version));                    // 157
    readOptionalField(&loaded_prefs.usb_logging_enabled,
                      sizeof(loaded_prefs.usb_logging_enabled));                           // 158
    readOptionalField(loaded_prefs.bluetooth_name,
                      sizeof(loaded_prefs.bluetooth_name));                               // 159
    readOptionalField(&loaded_prefs.display_rotation_degrees,
                      sizeof(loaded_prefs.display_rotation_degrees));                      // 191
    readOptionalField(&loaded_prefs.cad_enabled,
                      sizeof(loaded_prefs.cad_enabled));                                   // 193
    readOptionalField(&loaded_prefs.cad_scan_timeout_ms,
                      sizeof(loaded_prefs.cad_scan_timeout_ms));                           // 194
    readOptionalField(&loaded_prefs.cad_retry_delay_ms,
                      sizeof(loaded_prefs.cad_retry_delay_ms));                            // 196
    readOptionalField(&loaded_prefs.cad_max_duration_ms,
                      sizeof(loaded_prefs.cad_max_duration_ms));                           // 198
    readOptionalField(&loaded_prefs.bluetooth_mac_mode,
                      sizeof(loaded_prefs.bluetooth_mac_mode));                           // 200
    readOptionalField(loaded_prefs.bluetooth_mac,
                      sizeof(loaded_prefs.bluetooth_mac));                                // 201
    readOptionalField(&loaded_prefs.bluetooth_stealth_peer_type,
                      sizeof(loaded_prefs.bluetooth_stealth_peer_type));                  // 207
    readOptionalField(loaded_prefs.bluetooth_stealth_peer,
                      sizeof(loaded_prefs.bluetooth_stealth_peer));                       // 208
    readOptionalField(&loaded_prefs.bluetooth_stealth_mode,
                      sizeof(loaded_prefs.bluetooth_stealth_mode));                       // 214
    readOptionalField(&loaded_prefs.tx_delay_factor, sizeof(loaded_prefs.tx_delay_factor));
    readOptionalField(&loaded_prefs.direct_tx_delay_factor, sizeof(loaded_prefs.direct_tx_delay_factor));
    readOptionalField(&loaded_prefs.interference_threshold, sizeof(loaded_prefs.interference_threshold));
    readOptionalField(&loaded_prefs.agc_reset_interval, sizeof(loaded_prefs.agc_reset_interval));
    readOptionalField(&loaded_prefs.tz_offset, sizeof(loaded_prefs.tz_offset));
#ifdef TBEAM_1W
    readOptionalField(loaded_prefs.fan_mode, sizeof(loaded_prefs.fan_mode));
    readOptionalField(&loaded_prefs.fan_lo, sizeof(loaded_prefs.fan_lo));
    readOptionalField(&loaded_prefs.fan_hi, sizeof(loaded_prefs.fan_hi));
    loaded_prefs.fan_mode[sizeof(loaded_prefs.fan_mode) - 1] = 0;
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
    readOptionalField(loaded_prefs.wifi_ssid, sizeof(loaded_prefs.wifi_ssid));
    readOptionalField(loaded_prefs.wifi_pwd, sizeof(loaded_prefs.wifi_pwd));
    loaded_prefs.wifi_ssid[sizeof(loaded_prefs.wifi_ssid) - 1] = 0;
    loaded_prefs.wifi_pwd[sizeof(loaded_prefs.wifi_pwd) - 1] = 0;
#endif
    readOptionalField(&loaded_prefs.flood_retry_attempts,
                      sizeof(loaded_prefs.flood_retry_attempts));
    readOptionalField(&loaded_prefs.flood_retry_max_path,
                      sizeof(loaded_prefs.flood_retry_max_path));
    readOptionalField(&loaded_prefs.flood_retry_group_max_path,
                      sizeof(loaded_prefs.flood_retry_group_max_path));
    readOptionalField(&loaded_prefs.flood_retry_advert_enabled,
                      sizeof(loaded_prefs.flood_retry_advert_enabled));
    readOptionalField(&loaded_prefs.one_key_dm_enabled,
                      sizeof(loaded_prefs.one_key_dm_enabled));
    readOptionalField(&loaded_prefs.bluetooth_enabled,
                      sizeof(loaded_prefs.bluetooth_enabled));
    readOptionalField(&loaded_prefs.gps_sync_interval_hours,
                      sizeof(loaded_prefs.gps_sync_interval_hours));
    readOptionalField(&loaded_prefs.usb_debug_enabled,
                      sizeof(loaded_prefs.usb_debug_enabled));
    readOptionalField(&loaded_prefs.lost_reply, sizeof(loaded_prefs.lost_reply));

    // Any bytes left over form only part of a historically appended field.
    // Preserve the file and defaults rather than treating that tail as EOF.
    success = success && file.available() == 0;
    success = success && loaded_prefs.bluetooth_enabled <= 1;
    file.close();
    if (!success) return false;
    loaded_prefs.usb_debug_enabled = loaded_prefs.usb_debug_enabled == 1 ? 1 : 0;
    loaded_prefs.lost_reply = loaded_prefs.lost_reply <= 2 ? loaded_prefs.lost_reply : 0;
    _prefs = loaded_prefs;
    node_lat = loaded_lat;
    node_lon = loaded_lon;
    return true;
  }
  return false;
}
