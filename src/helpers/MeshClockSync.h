#pragma once

#include <Mesh.h>
#include <helpers/ClientACL.h>
#include <helpers/IdentityStore.h>
#include <helpers/SensorManager.h>

namespace mesh {

class MeshClockSyncCallbacks {
public:
  virtual ~MeshClockSyncCallbacks() = default;

  // WiFi/NTP, or another authoritative source, owns the clock for this boot.
  virtual bool hasAuthoritativeClock() const { return false; }

  // Let a role repair any timestamp-based caches after the RTC moves.
  virtual void onMeshClockAdjusted(uint32_t old_epoch, uint32_t new_epoch) {
    (void)old_epoch;
    (void)new_epoch;
  }
};

// Autonomous LoRa clock consensus shared by sensor and room-server roles.
// Repeater firmware retains its extended implementation, which additionally
// supports delayed internet/NTP estimates and repeater-specific state repair.
class MeshClockSync {
public:
  MeshClockSync(Radio& radio, MillisecondClock& millis, RTCClock& rtc,
                ClientACL& acl, SensorManager& sensors,
                const float& tx_delay_factor,
                MeshClockSyncCallbacks* callbacks = nullptr);

  void begin(FILESYSTEM* fs);
  void loop();

  void onManualClockSet();
  void onInternetClockSet();

  // Edge mode observes valid packets even when this node does not forward
  // them. Normal/path mode observes only packets accepted for forwarding.
  void observeVerifiedAdvert(const Packet* packet, const Identity& id,
                             uint32_t timestamp);
  void observeGroupPacket(const Packet* packet);
  void observeAcceptedFlood(const Packet* packet);

  bool handleCommand(const char* command, char* reply, size_t reply_len = 160);
  bool edgeMode() const { return _mesh_edge_enabled; }
  bool collectionActive() const;

private:
  static constexpr uint8_t SAMPLE_SLOTS = 16;
  static constexpr uint8_t PATH_ID_SIZE = 8;

  struct Sample {
    bool active;
    uint8_t source_kind;
    uint8_t source_id[4];
    uint8_t path_id[PATH_ID_SIZE];
    uint32_t epoch;
    uint32_t received_millis;
  };

  enum Suppression : uint8_t {
    SUPPRESS_NONE = 0,
    SUPPRESS_CLI = 1,
    SUPPRESS_GPS = 2,
    SUPPRESS_INTERNET = 3,
  };

  enum Result : uint8_t {
    RESULT_WAITING = 0,
    RESULT_COLLECTING = 1,
    RESULT_INTERNET_UNAVAILABLE = 2,
    RESULT_NO_CONSENSUS = 3,
    RESULT_WITHIN_DRIFT = 4,
    RESULT_CORRECTED_FORWARD = 5,
    RESULT_CORRECTED_BACKWARD = 6,
  };

  Radio* _radio;
  MillisecondClock* _millis;
  RTCClock* _rtc;
  ClientACL* _acl;
  SensorManager* _sensors;
  const float* _tx_delay_factor;
  MeshClockSyncCallbacks* _callbacks;
  FILESYSTEM* _fs;

  Sample _samples[SAMPLE_SLOTS];
  bool _mesh_enabled;
  bool _mesh_edge_enabled;
  bool _internet_enabled;
  bool _complete;
  bool _force_mesh_pending;
  uint8_t _suppressed_by;
  uint8_t _last_result;
  uint8_t _last_source_sample_count;
  uint8_t _last_fresh_count;
  uint8_t _last_required_count;
  uint8_t _required_samples;
  uint32_t _drift_seconds;
  uint32_t _last_estimate;
  uint32_t _last_abs_drift;
  uint32_t _last_millis;
  uint64_t _uptime_millis;
  uint64_t _next_attempt_uptime;

  void resetDefaults();
  void loadPrefs();
  bool savePrefs();
  void resetAttempt();
  void suppressForBoot(uint8_t source);
  static const char* suppressionName(uint8_t source);
  void checkGpsOverride();
  void checkClock();

  uint32_t estimateTransitMillis(const Packet* packet) const;
  void recordSample(uint8_t source_kind, const uint8_t source_id[4],
                    uint32_t epoch, const Packet* packet);
  void recordPublicChannel(const Packet* packet);
  bool estimate(uint32_t& epoch, uint8_t& fresh_count,
                uint8_t& agreeing_count, uint8_t& required_count) const;
  bool applyEstimate(uint32_t epoch, uint8_t sample_count);

  void formatStatus(const char* args, char* reply, size_t reply_len) const;
  void formatTable(char* reply, size_t reply_len) const;
  void formatSample(int index, char* reply, size_t reply_len) const;
};

}  // namespace mesh
