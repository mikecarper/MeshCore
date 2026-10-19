#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mesh { namespace power {

constexpr uint8_t RECOVERY_MCU = 1;
constexpr uint8_t RECOVERY_RADIO = 2;
constexpr uint8_t RECOVERY_DOMAINS = RECOVERY_MCU | RECOVERY_RADIO;
constexpr uint8_t REGULATOR_CRASH_THRESHOLD = 3;
constexpr uint32_t REGULATOR_HEALTHY_MS = 300000;
constexpr uint32_t REGULATOR_BATTERY_FRESH_MS = 60000;

enum class ResetKind : uint8_t { Cold, Intentional, Wake, Crash, Unknown };

// Keep this POD in reset-retained RAM, never in flash. The checksum is committed
// last; an interrupted write or an uninitialised retained region cannot add a
// crash to the next boot. Only the sticky fallback mask needs flash storage.
struct RecoveryRecord {
  uint32_t magic;
  uint16_t version;
  uint16_t bytes;
  uint32_t startedAt;
  uint32_t heartbeatAt;
  uint32_t batteryAt;
  uint8_t activeDcdcMask;
  uint8_t fallbackMask;
  uint8_t mcuCrashes;
  uint8_t radioCrashes;
  uint8_t batteryPercent;
  uint8_t flags;
  uint16_t reserved;
  uint32_t crc;
};
static_assert(sizeof(RecoveryRecord) <= 64, "regulator recovery retention budget");
static_assert(sizeof(RecoveryRecord) == 32, "regulator recovery record layout");
static_assert(offsetof(RecoveryRecord, crc) == 28, "regulator recovery CRC layout");

class RecoveryPolicy {
 public:
  explicit RecoveryPolicy(volatile RecoveryRecord& retained) : retained_(retained), state_{} {}

  // Read the previous boot exactly once, before arm(). A cold/intentional/wake
  // or unclassified reset breaks a streak, but never undoes a saved fallback.
  void begin(ResetKind reset, uint8_t savedFallbackMask) {
    RecoveryRecord previous{};
    read(previous);
    const bool valid = isValid(previous);
    state_ = RecoveryRecord{};
    state_.magic = kMagic;
    state_.version = kVersion;
    state_.bytes = sizeof(RecoveryRecord);
    state_.fallbackMask = savedFallbackMask & RECOVERY_DOMAINS;
    if (valid) {
      state_.fallbackMask |= previous.fallbackMask;
      if (reset == ResetKind::Crash && eligible(previous)) {
        state_.mcuCrashes = nextCount(previous, RECOVERY_MCU, previous.mcuCrashes);
        state_.radioCrashes = nextCount(previous, RECOVERY_RADIO, previous.radioCrashes);
        if (state_.mcuCrashes >= REGULATOR_CRASH_THRESHOLD)
          state_.fallbackMask |= RECOVERY_MCU;
        if (state_.radioCrashes >= REGULATOR_CRASH_THRESHOLD)
          state_.fallbackMask |= RECOVERY_RADIO;
      }
    }
    // Leave the new boot disarmed until its actual selected modes and measured
    // battery are known. A crash before that point cannot extend the streak.
    commit();
  }

  // Call before enabling either DC-DC regulator / starting the radio. Each bit
  // describes an actually selected DC-DC domain, not mere board capability.
  void arm(uint8_t activeDcdcMask, uint32_t now, uint8_t percent, bool valid) {
    state_.activeDcdcMask = activeDcdcMask & RECOVERY_DOMAINS & ~state_.fallbackMask;
    state_.startedAt = state_.heartbeatAt = now;
    state_.flags = kArmed;
    setBattery(now, percent, valid);
    commit();
  }

  // Heartbeats must not refresh the battery timestamp: a stale/unknown reading
  // must never turn a USB-only device or a discharged battery into an event.
  void heartbeat(uint32_t now) {
    state_.heartbeatAt = now;
    if ((state_.flags & kArmed) && !(state_.flags & kHealthy) &&
        elapsed(now, state_.startedAt) >= REGULATOR_HEALTHY_MS) {
      state_.mcuCrashes = state_.radioCrashes = 0;
      state_.flags |= kHealthy;
    }
    commit();
  }

  void updateBattery(uint32_t now, uint8_t percent, bool valid) {
    setBattery(now, percent, valid);
    commit();
  }

  void updateActiveDomains(uint8_t activeDcdcMask) {
    state_.activeDcdcMask = activeDcdcMask & RECOVERY_DOMAINS & ~state_.fallbackMask;
    commit();
  }

  // Convenience for callers that really take a fresh battery measurement each
  // tick. Use heartbeat() separately when measurements are less frequent.
  void tick(uint32_t now, uint8_t percent, bool valid) {
    setBattery(now, percent, valid);
    heartbeat(now);
  }

  void markCleanReset() {
    state_.flags = (state_.flags & ~kArmed) | kClean;
    state_.mcuCrashes = state_.radioCrashes = 0;
    commit();
  }

  // A deliberate user mode change resets only that domain's learned fallback.
  void clearDomain(uint8_t domains) {
    domains &= RECOVERY_DOMAINS;
    state_.fallbackMask &= ~domains;
    if (domains & RECOVERY_MCU) state_.mcuCrashes = 0;
    if (domains & RECOVERY_RADIO) state_.radioCrashes = 0;
    commit();
  }

  uint8_t fallbackMask() const { return state_.fallbackMask; }
  uint8_t crashCount(uint8_t domain) const {
    return domain == RECOVERY_MCU ? state_.mcuCrashes :
           domain == RECOVERY_RADIO ? state_.radioCrashes : 0;
  }

 private:
  static constexpr uint32_t kMagic = 0x52504752;  // RGPR
  static constexpr uint16_t kVersion = 1;
  static constexpr uint8_t kArmed = 1;
  static constexpr uint8_t kClean = 2;
  static constexpr uint8_t kBatteryValid = 4;
  static constexpr uint8_t kHealthy = 8;
  static constexpr uint8_t kFlags = kArmed | kClean | kBatteryValid | kHealthy;

  volatile RecoveryRecord& retained_;
  RecoveryRecord state_;

  static uint32_t elapsed(uint32_t now, uint32_t then) { return now - then; }

  static uint32_t checksum(const RecoveryRecord& record) {
    uint32_t crc = 0xffffffff;
    const uint8_t* bytes = reinterpret_cast<const uint8_t*>(&record);
    for (size_t i = 0; i < offsetof(RecoveryRecord, crc); ++i) {
      crc ^= bytes[i];
      for (uint8_t bit = 0; bit < 8; ++bit)
        crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
  }

  static bool isValid(const RecoveryRecord& record) {
    return record.magic == kMagic && record.version == kVersion &&
           record.bytes == sizeof(RecoveryRecord) && record.reserved == 0 &&
           !(record.activeDcdcMask & ~RECOVERY_DOMAINS) &&
           !(record.fallbackMask & ~RECOVERY_DOMAINS) &&
           !(record.flags & ~kFlags) && record.batteryPercent <= 100 &&
           record.mcuCrashes <= REGULATOR_CRASH_THRESHOLD &&
           record.radioCrashes <= REGULATOR_CRASH_THRESHOLD &&
           record.crc == checksum(record);
  }

  static bool eligible(const RecoveryRecord& record) {
    return (record.flags & (kArmed | kClean | kBatteryValid)) == (kArmed | kBatteryValid) &&
           record.batteryPercent >= 50 &&
           elapsed(record.heartbeatAt, record.batteryAt) <= REGULATOR_BATTERY_FRESH_MS;
  }

  static uint8_t nextCount(const RecoveryRecord& previous, uint8_t domain, uint8_t count) {
    if (!(previous.activeDcdcMask & domain) || (previous.fallbackMask & domain)) return 0;
    return count < REGULATOR_CRASH_THRESHOLD ? count + 1 : REGULATOR_CRASH_THRESHOLD;
  }

  void setBattery(uint32_t now, uint8_t percent, bool valid) {
    state_.batteryAt = now;
    state_.batteryPercent = percent <= 100 ? percent : 0;
    if (valid && percent <= 100) state_.flags |= kBatteryValid;
    else state_.flags &= ~kBatteryValid;
  }

  void read(RecoveryRecord& result) const {
    const volatile uint8_t* source = reinterpret_cast<const volatile uint8_t*>(&retained_);
    uint8_t* target = reinterpret_cast<uint8_t*>(&result);
    for (size_t i = 0; i < sizeof(result); ++i) target[i] = source[i];
  }

  void commit() {
    state_.crc = checksum(state_);
    // Invalidate before changing any payload, then publish its CRC last. No
    // flash writes, heap allocation, interrupt dependencies, or platform APIs.
    retained_.magic = 0;
    retained_.crc = 0;
    volatile uint8_t* target = reinterpret_cast<volatile uint8_t*>(&retained_);
    const uint8_t* source = reinterpret_cast<const uint8_t*>(&state_);
    for (size_t i = sizeof(state_.magic); i < offsetof(RecoveryRecord, crc); ++i)
      target[i] = source[i];
    retained_.magic = state_.magic;
    retained_.crc = state_.crc;
  }
};

}} // namespace mesh::power
