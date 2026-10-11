#pragma once

#if defined(STM32_PLATFORM) && defined(__GNUC__)
#include "NodePrefs.h"
#include <cstddef>
#include <type_traits>

#if defined(TBEAM_1W) || defined(RP2040_PLATFORM)
#error "The compact STM32 preference table needs an explicit board layout"
#endif

namespace mesh {
namespace companion_prefs {

struct PrefField { uint8_t offset, length; };
enum : uint8_t { PrefsLat = 253, PrefsLon = 254, PrefsPad = 255 };
static constexpr size_t MANDATORY_FIELDS = 21;

// GNU supports offsetof for this adapter-bearing, non-standard-layout class.
// Use it only on the STM32 GNU ABI, and verify every individual value field;
// neither the class padding nor either runtime adapter is a wire image.
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Winvalid-offsetof"
template<typename Value, size_t Offset, size_t Length>
constexpr PrefField prefField() {
  static_assert(std::is_trivially_copyable<Value>::value,
                "only scalar/array preferences are persisted");
  static_assert(sizeof(Value) == Length, "published preference size");
  static_assert(Offset < PrefsLat && Length <= UINT8_MAX,
                "compact preference field bounds");
  static_assert(Offset + Length <=
      __builtin_offsetof(CompanionNodePrefs, lost_reply)
          + sizeof(CompanionNodePrefs::lost_reply),
      "preference field stays before runtime adapters");
  static_assert(Offset + Length <= sizeof(CompanionNodePrefs),
                "preference field stays inside its object");
  return {uint8_t(Offset), uint8_t(Length)};
}
template<uint8_t Source, size_t Length>
constexpr PrefField prefSpecial() {
  static_assert(Source == PrefsLat || Source == PrefsLon || Source == PrefsPad,
                "known auxiliary preference source");
  static_assert(Source == PrefsPad ? Length <= 4 : Length == sizeof(double),
                "coordinate/padding source bounds");
  return {Source, uint8_t(Length)};
}
#define PREF_FIELD(name, length) prefField<decltype(CompanionNodePrefs::name), \
    __builtin_offsetof(CompanionNodePrefs, name), length>()
// Explicit published wire order, deliberately independent of member order.
static constexpr PrefField PREF_FIELDS[] = {
  PREF_FIELD(airtime_factor, 4),
  PREF_FIELD(node_name, 32),
  prefSpecial<PrefsPad, 4>(),
  prefSpecial<PrefsLat, 8>(),
  prefSpecial<PrefsLon, 8>(),
  PREF_FIELD(freq, 4),
  PREF_FIELD(sf, 1),
  PREF_FIELD(cr, 1),
  PREF_FIELD(client_repeat, 1),
  PREF_FIELD(manual_add_contacts, 1),
  PREF_FIELD(bw, 4),
  PREF_FIELD(tx_power_dbm, 1),
  PREF_FIELD(telemetry_mode_base, 1),
  PREF_FIELD(telemetry_mode_loc, 1),
  PREF_FIELD(telemetry_mode_env, 1),
  PREF_FIELD(rx_delay_base, 4),
  PREF_FIELD(advert_loc_policy, 1),
  PREF_FIELD(multi_acks, 1),
  PREF_FIELD(path_hash_mode, 1),
  prefSpecial<PrefsPad, 1>(),
  PREF_FIELD(ble_pin, 4),
  PREF_FIELD(buzzer_quiet, 1),
  PREF_FIELD(gps_enabled, 1),
  PREF_FIELD(gps_interval, 4),
  PREF_FIELD(autoadd_config, 1),
  PREF_FIELD(autoadd_max_hops, 1),
  PREF_FIELD(rx_boosted_gain, 1),
  PREF_FIELD(default_scope_name, 31),
  PREF_FIELD(default_scope_key, 16),
  PREF_FIELD(radio_fem_rxgain, 1),
  PREF_FIELD(radio_fem_rxgain_override, 1),
  PREF_FIELD(vibe_quiet, 1),
  PREF_FIELD(radio_fem_txgain, 1),
  PREF_FIELD(rx_powersaving_enabled, 1),
  PREF_FIELD(rx_ps_rx_us, 4),
  PREF_FIELD(rx_ps_sleep_us, 4),
  PREF_FIELD(rx_ps_level, 1),
  PREF_FIELD(rx_ps_preamble, 1),
  PREF_FIELD(powersaving_enabled, 1),
  PREF_FIELD(wifi_enabled, 1),
  PREF_FIELD(powersaving_policy_version, 1),
  PREF_FIELD(usb_logging_enabled, 1),
  PREF_FIELD(bluetooth_name, 32),
  PREF_FIELD(display_rotation_degrees, 2),
  PREF_FIELD(cad_enabled, 1),
  PREF_FIELD(cad_scan_timeout_ms, 2),
  PREF_FIELD(cad_retry_delay_ms, 2),
  PREF_FIELD(cad_max_duration_ms, 2),
  PREF_FIELD(bluetooth_mac_mode, 1),
  PREF_FIELD(bluetooth_mac, 6),
  PREF_FIELD(bluetooth_stealth_peer_type, 1),
  PREF_FIELD(bluetooth_stealth_peer, 6),
  PREF_FIELD(bluetooth_stealth_mode, 1),
  PREF_FIELD(tx_delay_factor, 4),
  PREF_FIELD(direct_tx_delay_factor, 4),
  PREF_FIELD(interference_threshold, 1),
  PREF_FIELD(agc_reset_interval, 1),
  PREF_FIELD(tz_offset, 1),
  PREF_FIELD(flood_retry_attempts, 1),
  PREF_FIELD(flood_retry_max_path, 1),
  PREF_FIELD(flood_retry_group_max_path, 1),
  PREF_FIELD(flood_retry_advert_enabled, 1),
  PREF_FIELD(one_key_dm_enabled, 1),
  PREF_FIELD(bluetooth_enabled, 1),
  PREF_FIELD(gps_sync_interval_hours, 2),
  PREF_FIELD(usb_debug_enabled, 1),
  PREF_FIELD(lost_reply, 1),
};
#undef PREF_FIELD
#pragma GCC diagnostic pop
static constexpr size_t FIELD_COUNT = sizeof(PREF_FIELDS) / sizeof(PREF_FIELDS[0]);
constexpr size_t wireSize(size_t count) {
  return count == 0 ? 0 : PREF_FIELDS[count - 1].length + wireSize(count - 1);
}
static_assert(wireSize(MANDATORY_FIELDS) == 84, "published mandatory image size");
static_assert(wireSize(FIELD_COUNT) == 236, "published STM32 preference image size");

static const void* prefValue(const PrefField& field,
                            const CompanionNodePrefs& values,
                            const double& lat, const double& lon,
                            const uint8_t pad[4]) {
  if (field.offset == PrefsLat) return &lat;
  if (field.offset == PrefsLon) return &lon;
  if (field.offset == PrefsPad) return pad;
  return reinterpret_cast<const uint8_t*>(&values) + field.offset;
}

}  // namespace companion_prefs
}  // namespace mesh
#endif
