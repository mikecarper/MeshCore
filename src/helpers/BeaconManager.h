#pragma once

#include <helpers/BeaconConfig.h>

#if MESH_ENABLE_TX_BEACONS

#include <helpers/IdentityStore.h>

namespace mesh {
class Mesh;
class RTCClock;

// Configuration and a monotonic, best-effort schedule for TX-only beacons.
// The radio/profile transaction itself belongs to Mesh and Dispatcher.
class BeaconManager {
 public:
  static constexpr unsigned SlotCount = 12;
  static constexpr uint32_t HourMillis = 3600000;

  BeaconManager() = default;
  ~BeaconManager();
  BeaconManager(const BeaconManager&) = delete;
  BeaconManager& operator=(const BeaconManager&) = delete;
  void begin(FILESYSTEM* fs, Mesh* mesh, RTCClock* rtc, const char* node_name);
  void suspend();
  void loop(uint32_t now);
  static bool isCommand(const char* command);
  bool handle(const char* command, char* reply, size_t capacity);

 private:
  enum class ReadResult : uint8_t { Missing, Valid, Invalid, Unreadable, Future };
  FILESYSTEM* fs_ = nullptr;
  Mesh* mesh_ = nullptr;
  const char* name_ = nullptr;
  BeaconSlot* slots_ = nullptr;
  uint64_t elapsed_ = 0, anchor_ = 0, handled_hour_ = 0;
  uint64_t last_send_ = 0, slot_last_send_[SlotCount] = {}, slot_not_before_[SlotCount] = {};
  uint32_t last_now_ = 0, last_id_ = 0;
  uint8_t interval_hours_ = 24;
  bool hold_ = false, ids_hold_ = false, ids_known_ = false;
  bool ids_initialized_ = false;

  bool allocate();
  bool anyEnabled() const;
  ReadResult readConfig(const char* path, BeaconSlot* output,
                        uint8_t& hours, bool& ids_initialized);
  ReadResult readIds(const char* path, uint32_t& last);
  bool saveConfig(unsigned changed_slot, const BeaconSlot* changed,
                  uint8_t hours, bool ids_initialized, bool clear_all = false);
  bool saveIds(uint32_t last);
  bool commit(const char* path, const char* backup, const char* temporary,
              bool& hold);
  bool initializeIds();
  bool nextId(uint32_t& id);
  void formatSlot(unsigned index, char* reply, size_t capacity) const;
};
} // namespace mesh

#endif
