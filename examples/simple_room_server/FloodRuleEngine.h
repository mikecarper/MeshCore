#pragma once

#if defined(MESHCORE_ESP32_FULL_PROFILE)

#include <Arduino.h>
#include <Mesh.h>

#include <helpers/IdentityStore.h>
#include <helpers/RegionMap.h>

class FloodRuleEngine {
public:
  static constexpr uint8_t RULE_SLOTS = 31;
  static constexpr uint8_t ANY_TYPE = 0xFF;
  static constexpr uint8_t MAX_HOPS = 63;
  static constexpr uint8_t NAME_LEN = 32;
  static constexpr uint8_t PATH_PREFIX_HOPS_MAX = 3;
  static constexpr uint8_t PATH_PREFIX_BYTES_MAX = 9;
  static constexpr uint16_t RATE_UNLIMITED = 0xFFFF;

  FloodRuleEngine();

  void begin(FILESYSTEM* fs, RegionMap* regions);

  uint32_t evaluate(const mesh::Packet* packet, bool temp_radio_active,
                    bool incoming_region_allowed,
                    const RegionEntry* incoming_region);
  bool applyScope(mesh::Packet* packet, uint32_t match_mask,
                  bool& scope_set, bool& fast_track,
                  bool log_change = true);
  bool shouldBlock(const mesh::Packet* packet, uint32_t match_mask,
                   uint32_t now_millis) const;
  void commitRates(const mesh::Packet* packet, uint32_t match_mask,
                   uint32_t now_millis);

  bool handleCommand(const char* command, char* reply);

private:
  struct Entry {
    bool active;
    uint8_t payload_type;
    uint8_t min_hops;
    uint8_t max_hops;
    bool suspend_on_temp_radio;
    char scope_name[NAME_LEN];
    bool match_blacklisted_path;
    bool scope_uses_slow_timing;
    uint8_t incoming_scope_kind;
    char incoming_scope_name[NAME_LEN];
    uint8_t channel_key_len;
    uint8_t channel_hash;
    uint8_t channel_secret[PUB_KEY_SIZE];
    char channel_name[NAME_LEN];
    uint8_t path_hash_size;
    uint8_t path_hops;
    uint8_t path[PATH_PREFIX_BYTES_MAX];
    char target_region_name[NAME_LEN];
    bool drop_on_match;
    bool rate_limit_enabled;
    uint16_t rate_per_minute;
    uint8_t priority;
    bool stop_on_match;
    uint32_t rate_window_started;
    uint16_t rate_window_count;
    bool rate_window_active;
  };

  FILESYSTEM* _fs;
  RegionMap* _regions;
  Entry _entries[RULE_SLOTS];

  void seedDefaults();
  void load();
  bool save();
  bool fieldsMatch(const Entry& entry, const mesh::Packet* packet,
                   bool temp_radio_active, bool incoming_is_scoped,
                   uint16_t incoming_transport_code,
                   bool incoming_region_allowed,
                   const RegionEntry* incoming_region) const;
  bool authenticateChannel(const Entry& entry,
                           const mesh::Packet* packet) const;
  int nextMatch(uint32_t match_mask, uint32_t visited_mask) const;
  bool resolveTargetRegion(const char* name, TransportKey& scope,
                           const char*& canonical_name);
  uint32_t applyStop(uint32_t match_mask);
  void format(const char* args, char* reply) const;
  void formatDetail(int index, char* reply, size_t reply_len) const;
  void set(const char* args, char* reply,
           bool require_explicit_action = false);
  void remove(const char* args, char* reply);
};

#endif
