#if defined(ARDUINO)
#include "UsbLoggingWatchdog.h"
#include "UsbLoggingWatchdogPolicy.h"
#include "UsbLogging.h"
#include "UsbLoggingClientActivity.h"
#include <stdio.h>
#include <string.h>

#if MESH_USB_CONSOLE_COOPERATIVE
#include "FilePresence.h"
#include "PersistentStoreFormat.h"
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#elif defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
#include "ContactFileTransaction.h"
#endif
#if defined(ENABLE_OTA)
#include "ota/OtaContext.h"
#endif
#include <Arduino.h>
#include <atomic>

namespace mesh {
namespace {
using Policy = UsbLoggingWatchdogPolicy;
using Event = UsbLoggingWatchdogEvent;
constexpr size_t LegacyImageSize = 20;
constexpr size_t ImageSize = 33;
constexpr size_t EventOffset = 16;
constexpr size_t CrcOffset = EventOffset + USB_WATCHDOG_EVENT_SIZE;
constexpr uint32_t ProbeMs = 60000;
constexpr uint32_t DeferredRetryMs = 5000;
const char* const Path = "/usb_wdg";
const char* const Backup = "/usb_wdg.bak";
struct Config {
  uint8_t mode = 2;  // Off=0, On=1, Auto=2; new nodes qualify before arming.
  uint8_t step = 0;
  uint32_t recoveries = 0;
  uint32_t reboots = 0;
  Event last_event;
};
enum class ReadResult : uint8_t { Missing, Valid, Invalid, Unreadable };
FILESYSTEM* settings_fs = nullptr;
Config config;
Policy policy;
// Separate USB health from application stats health. Actions from this tracker
// are ignored; it keeps physical TX stalls observable after a lease expires.
Policy physical_policy;
UsbLoggingAutoArmPolicy auto_arm;
bool persistence_ready = false;
bool settings_durable = false;
bool backend_unsupported = false;
bool backend_deferred = false;
bool backend_retry_started = false;
bool reader_seen = false;
uint32_t backend_retry_at = 0;
uint32_t probe_at = 0;
uint32_t (*event_epoch_seconds)() = nullptr;
uint32_t uptime_last_tick = 0;
uint64_t uptime_ms = 0;
bool event_pending = false;

// Only the application loop/CLI writes policy and configuration. Management
// readers get a bounded, non-blocking snapshot without filesystem/USB calls.
std::atomic<uint32_t> status_flags{0}, status_stage_step{0};
std::atomic<uint32_t> status_retry{3600}, status_inactive{0};
std::atomic<uint32_t> status_recoveries{0}, status_reboots{0};
std::atomic<uint32_t> status_auto_seconds{0};
std::atomic<uint32_t> status_event_guard{0}, status_event_code{0};
std::atomic<uint32_t> status_event_epoch{0}, status_event_uptime{0}, status_event_sequence{0};

uint32_t incrementSaturated(uint32_t value) {
  return value == UINT32_MAX ? value : value + 1;
}

void tickUptime(uint32_t now) {
  const uint32_t delta = now - uptime_last_tick;
  if (delta > 0x80000000UL && uint32_t(uptime_last_tick - now) <= 5000) return;
  uptime_ms += delta;
  uptime_last_tick = now;
}

uint8_t physicalReasons(const UsbLoggingObservation& observation) {
  return (!observation.host_connected ? Event::HOST_ABSENT : 0)
      | (!observation.reader_connected ? Event::READER_ABSENT : 0)
      | (physical_policy.stalled() ? Event::TX_STALLED : 0);
}

Event newEvent(uint8_t action, uint8_t reasons) {
  Event event;
  event.action = action;
  event.reasons = reasons;
  event.epoch = event_epoch_seconds ? event_epoch_seconds() : 0;
  const uint64_t seconds = uptime_ms / 1000;
  event.uptime_seconds = seconds > UINT32_MAX ? UINT32_MAX : uint32_t(seconds);
  event.sequence = incrementSaturated(config.last_event.sequence);
  return event;
}

void publish(const UsbLoggingObservation& observation) {
  const bool logger_active = isUsbLoggingEnabled() && observation.host_connected
      && observation.reader_connected && isUsbLoggingClientActive() && !physical_policy.stalled();
  const uint32_t flags = (observation.supported && !backend_unsupported ? 1U : 0U)
      | (isUsbLoggingEnabled() ? 2U : 0U) | (config.mode == 1 ? 4U : 0U)
      | (observation.host_connected ? 8U : 0U)
      | (observation.reader_connected ? 16U : 0U)
      | (physical_policy.stalled() ? 32U : 0U) | (policy.stage() ? 64U : 0U)
      | (policy.deferred() || backend_deferred ? 128U : 0U)
      | (persistence_ready ? 256U : 0U) | (config.mode == 2 ? 512U : 0U)
      | (reader_seen ? 1024U : 0U) | (logger_active ? 2048U : 0U);
  // Bracket the entire status, not just its event. A counter/tier read from
  // one publication must never be paired with another event's journal.
  status_event_guard.fetch_add(1);
  status_stage_step.store(uint32_t(policy.stage()) | (uint32_t(config.step) << 8));
  status_retry.store(Policy::intervalMs(config.step) / 1000);
  status_inactive.store(policy.inactiveSeconds());
  status_recoveries.store(config.recoveries);
  status_reboots.store(config.reboots);
  status_auto_seconds.store(auto_arm.connectedSeconds());
  // Every shared word remains atomic: the sequence guard alone would not make
  // concurrent non-atomic reads/writes legal in the C++ memory model.
  status_event_code.store(config.last_event.reasons | (uint32_t(config.last_event.action) << 4)
      | (config.last_event.persisted ? 128U : 0U));
  status_event_epoch.store(config.last_event.epoch);
  status_event_uptime.store(config.last_event.uptime_seconds);
  status_event_sequence.store(config.last_event.sequence);
  status_flags.store(flags, std::memory_order_release);
  status_event_guard.fetch_add(1);
}

ReadResult readImage(const char* path, uint8_t* image, size_t* read_size = nullptr) {
  bool present = false;
  if (!filePresence(settings_fs, path, present)) return ReadResult::Unreadable;
  if (!present) return ReadResult::Missing;
#if defined(NRF52_PLATFORM)
  File file(*settings_fs);
  if (!file.open(path, FILE_O_READ)) return ReadResult::Unreadable;
#elif defined(STM32_PLATFORM)
  File file = settings_fs->open(path, FILE_O_READ);
#else
  File file = settings_fs->open(path, "r");
#endif
  if (!file) return ReadResult::Unreadable;
  const size_t size = file.size();
  const bool legacy = size == LegacyImageSize;
  const bool right_size = legacy || size == ImageSize;
  const bool complete = right_size && static_cast<size_t>(file.read(image, size)) == size;
  file.close();
  if (!right_size) return ReadResult::Invalid;
  if (!complete) return ReadResult::Unreadable;
  const size_t crc_offset = size - sizeof(uint32_t);
  const bool valid = image[0] == 'U' && image[1] == 'W'
      && image[2] == (legacy ? 1 : 2) && image[3] == 0 && image[4] <= 2
      && image[5] <= Policy::MAX_STEP && image[6] == 0 && image[7] == 0
      && storage::readLE32(image + crc_offset)
          == (storage::updateCRC32(0xffffffffU, image, crc_offset) ^ 0xffffffffU);
  Event event;
  if (!valid || (!legacy && (!decodeUsbWatchdogEvent(image + EventOffset, event)
                            || (event.sequence && !event.persisted)))) return ReadResult::Invalid;
  if (read_size) *read_size = size;
  return ReadResult::Valid;
}

bool saveConfig(const Config& candidate, bool explicit_write = false) {
  // Automatic actions stop after any unverifiable commit. An explicit CLI
  // setter may retry/repair only this independent statefile on durable media.
  if (!settings_fs || !settings_durable || (!persistence_ready && !explicit_write)) return false;
  uint8_t image[ImageSize] = {'U', 'W', 2, 0};
  image[4] = candidate.mode;
  image[5] = candidate.step;
  storage::writeLE32(image + 8, candidate.recoveries);
  storage::writeLE32(image + 12, candidate.reboots);
  Event durable_event = candidate.last_event;
  durable_event.persisted = durable_event.sequence != 0;
  encodeUsbWatchdogEvent(image + EventOffset, durable_event);
  storage::writeLE32(image + CrcOffset,
      storage::updateCRC32(0xffffffffU, image, CrcOffset) ^ 0xffffffffU);
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter writer(settings_fs, Path);
#elif defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  ContactFileTransaction writer(settings_fs, Path, filePresence<FILESYSTEM>);
#else
  persistence_ready = false;
  return false;
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM) \
    || defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  bool ok = writer && writer.write(image, sizeof(image)) == sizeof(image)
      && writer.commit();
  uint8_t verify[ImageSize];
  size_t verify_size = 0;
  ok = ok && readImage(Path, verify, &verify_size) == ReadResult::Valid && verify_size == ImageSize
      && !memcmp(image, verify, sizeof(image));
  persistence_ready = ok;  // Never reset on an unverified tier.
  return ok;
#endif
}

bool safeNow(bool (*safe)(void*), void* context) {
  return safe && !isUsbLoggingWatchdogUpdateActive() && safe(context);
}

bool keyMatches(const char* command, const char* key) {
  const size_t length = strlen(key);
  return strncmp(command, key, length) == 0
      && (command[length] == 0 || command[length] == ' ' || command[length] == '\t');
}
}  // namespace

UsbLoggingStatus usbLoggingStatus() {
  for (uint8_t attempt = 0; attempt < 3; ++attempt) {
    const uint32_t before = status_event_guard.load();
    if (before & 1U) continue;
    UsbLoggingStatus status;
    const uint32_t flags = status_flags.load(std::memory_order_acquire);
    const uint32_t stage_step = status_stage_step.load();
    status.supported = flags & 1U;
    status.logging_enabled = flags & 2U;
    status.watchdog_enabled = flags & 4U;
    status.host_connected = flags & 8U;
    status.reader_connected = flags & 16U;
    status.stalled = flags & 32U;
    status.recovering = flags & 64U;
    status.recovery_deferred = flags & 128U;
    status.persistence_ready = flags & 256U;
    status.watchdog_auto = flags & 512U;
    status.logger_active = flags & 2048U;
    status.stage = stage_step & 0xff;
    status.backoff_step = (stage_step >> 8) & 0xff;
    status.retry_seconds = status_retry.load();
    status.inactive_seconds = status_inactive.load();
    status.recovery_count = status_recoveries.load();
    status.reboot_count = status_reboots.load();
    status.auto_connected_seconds = status_auto_seconds.load();
    auto& event = status.last_event;
    const uint32_t code = status_event_code.load();
    event.reasons = code & 15U;
    event.action = (code >> 4) & 7U;
    event.persisted = code & 128U;
    event.epoch = status_event_epoch.load();
    event.uptime_seconds = status_event_uptime.load();
    event.sequence = status_event_sequence.load();
    if (before == status_event_guard.load()) return status;
  }
  // A writer may be preempted, so retries must stay bounded. This is an
  // unavailable/conservative snapshot, not fresh physical measurements.
  // Keep published hardware/logging/mode intent; never pretend unsupported.
  // A possibly live reader must still protect functional CLI work. No durable
  // authorization or event is claimed until a coherent snapshot is available.
  UsbLoggingStatus status;
  const uint32_t flags = status_flags.load(std::memory_order_acquire);
  status.supported = flags & 1U;
  status.logging_enabled = flags & 2U;
  status.watchdog_enabled = flags & 4U;
  status.watchdog_auto = flags & 512U;
  status.reader_connected = true;
  status.recovery_deferred = true;
  status.backoff_step = Policy::MAX_STEP;
  status.retry_seconds = Policy::MAX_MS / 1000;
  return status;
}

bool isUsbLoggingWatchdogArmed() {
  if (isUsbLoggingTransportRecoveryPending()) return true;
  // This is only an awake/service hint, never reset authorization. Keep a
  // previously durable policy awake if publication is busy; falling back to
  // an unavailable snapshot here could allow sleep to restart its deadline.
  const uint32_t before = status_event_guard.load();
  const uint32_t flags = status_flags.load(std::memory_order_acquire);
  const uint32_t stage = status_stage_step.load() & 0xffU;
  const bool busy = (before & 1U) || before != status_event_guard.load();
  const bool ready = (flags & (1U | 2U | 256U)) == (1U | 2U | 256U);
  return ready && ((flags & 4U)
      || ((flags & 512U) && (flags & 1024U) && (busy || stage < 2)));
}

void loadUsbLoggingWatchdog(FILESYSTEM* fs, bool durable, uint32_t (*epoch_seconds)()) {
  settings_fs = fs;
  settings_durable = fs && durable;
  config = Config();
  persistence_ready = false;
  backend_unsupported = backend_deferred = backend_retry_started = false;
  reader_seen = false;
  event_epoch_seconds = epoch_seconds;
  uptime_last_tick = millis();
  uptime_ms = uptime_last_tick;
  event_pending = false;
  clearUsbLoggingClientActivity();
  probe_at = millis();
  uint8_t image[ImageSize];
  size_t image_size = 0;
  if (fs && durable) {
    ReadResult result = readImage(Path, image, &image_size);
    if (result == ReadResult::Missing) {
      result = readImage(Backup, image, &image_size);
      if (result == ReadResult::Valid) {
        // Repair only a conclusively absent primary. Never remove or replace
        // unreadable/corrupt images automatically, even with a good backup.
        const bool renamed = fs->rename(Backup, Path);
        uint8_t verify[ImageSize];
        size_t verify_size = 0;
        if (!renamed || readImage(Path, verify, &verify_size) != ReadResult::Valid
            || image_size != verify_size || memcmp(image, verify, image_size)) {
          result = ReadResult::Unreadable;
        }
      }
    }
    if (result == ReadResult::Valid) {
      config.mode = image[4];
      config.step = image[5];
      config.recoveries = storage::readLE32(image + 8);
      config.reboots = storage::readLE32(image + 12);
      if (image_size == ImageSize) decodeUsbWatchdogEvent(image + EventOffset, config.last_event);
      persistence_ready = true;
    } else if (result == ReadResult::Missing) {
      persistence_ready = true;  // Fresh durable store qualifies in Auto.
    }
  }
  policy.begin(millis(), config.step);
  physical_policy.begin(millis(), config.step);
  auto_arm.begin(millis());
  publish(observeUsbLoggingTransport());
}

bool isUsbLoggingWatchdogUpdateActive() {
#if defined(ENABLE_OTA)
  const auto* ota = mesh::ota::ota_context_if_active();
  if (ota) {
    const auto state = ota->manager.fetchState();
    if (ota->apply_pending || ota->bootloader_apply_pending || ota->folder_active
        || ota->folder_dest || ota->folderCaptureWaiting()
        || ota->manager.pendingServeJobs() || ota->manager.pendingManifestJobs()
        || ota->serve_expected || (state != mesh::ota::OtaManager::IDLE
                                  && state != mesh::ota::OtaManager::FAILED)) return true;
  }
#endif
  return false;
}

bool handleUsbLoggingWatchdogCommand(const char* command, char* reply,
                                    size_t capacity) {
  if (!command || !reply || !capacity) return false;
  if (keyMatches(command, "get usb.watchdog.last")) {
    if (strcmp(command, "get usb.watchdog.last")) {
      snprintf(reply, capacity, "Error: usage get usb.watchdog.last");
      return true;
    }
    const auto event = usbLoggingStatus().last_event;
    snprintf(reply, capacity, "> %s reasons=0x%02X epoch=%lu uptime=%lus seq=%lu persisted=%u",
        usbWatchdogActionName(event.action), unsigned(event.reasons), (unsigned long)event.epoch,
        (unsigned long)event.uptime_seconds, (unsigned long)event.sequence, event.persisted);
    return true;
  }
  if (keyMatches(command, "get usb.watchdog")) {
    if (strcmp(command, "get usb.watchdog")) {
      snprintf(reply, capacity, "Error: usage get usb.watchdog");
      return true;
    }
    publish(observeUsbLoggingTransport());
    const auto status = usbLoggingStatus();
    snprintf(reply, capacity,
        "> %s usb=%u log=%u host=%u reader=%u logger=%u stall=%u stage=%u step=%u "
        "retry=%lus idle=%lus auto=%lus defer=%u fs=%u rec=%lu boot=%lu",
        status.watchdog_auto ? "auto" : (status.watchdog_enabled ? "on" : "off"),
        status.supported,
        status.logging_enabled, status.host_connected, status.reader_connected, status.logger_active,
        status.stalled, status.stage, status.backoff_step,
        (unsigned long)status.retry_seconds, (unsigned long)status.inactive_seconds,
        (unsigned long)status.auto_connected_seconds,
        status.recovery_deferred, status.persistence_ready,
        (unsigned long)status.recovery_count, (unsigned long)status.reboot_count);
    return true;
  }
  if (!keyMatches(command, "set usb.watchdog")) return false;
  const char* value = command + strlen("set usb.watchdog");
  while (*value == ' ' || *value == '\t') ++value;
  if (strcmp(value, "on") && strcmp(value, "off") && strcmp(value, "auto")) {
    snprintf(reply, capacity, "Error: usage set usb.watchdog off|on|auto");
    return true;
  }
  const uint8_t mode = !strcmp(value, "on") ? 1 : (!strcmp(value, "auto") ? 2 : 0);
  const auto observation = observeUsbLoggingTransport();
  if (mode == 1 && !observation.supported) {
    snprintf(reply, capacity, "Error: USB watchdog unsupported on this transport");
    return true;
  }
  Config candidate = config;
  candidate.mode = mode;
  if (!saveConfig(candidate, true)) {
    snprintf(reply, capacity, "Error: save unconfirmed; reboot may differ");
  } else {
    config = candidate;
    if (config.last_event.sequence) config.last_event.persisted = true;
    event_pending = false;
    backend_unsupported = backend_deferred = backend_retry_started = false;
    reader_seen = false;
    policy.begin(millis(), config.step);
    physical_policy.begin(millis(), config.step);
    auto_arm.begin(millis());
    probe_at = millis();
    snprintf(reply, capacity, "OK - USB watchdog %s (saved)", value);
  }
  publish(observation);
  return true;
}

bool serviceUsbLoggingWatchdog(bool (*safe)(void*), void* context) {
  const uint32_t now = millis();
  tickUptime(now);
  auto observation = observeUsbLoggingTransport();
  physical_policy.update(now, isUsbLoggingEnabled(), observation);
  const bool observing = config.mode != 0 && persistence_ready && isUsbLoggingEnabled()
      && !backend_unsupported;
  if (!observing) reader_seen = false;
  if (!isUsbLoggingEnabled() || !observation.host_connected || !observation.reader_connected) {
    clearUsbLoggingClientActivity();
  }
  const bool lease_eligible = isUsbLoggingEnabled() && observation.host_connected
      && observation.reader_connected && isUsbLoggingClientActive();
  auto policy_observation = observation;
  // USB ACK/DTR proves transport availability, not application activity.
  // Auto repairs a previously confirmed logger even when its process stops
  // polling but the kernel continues acknowledging bulk USB data.
  if (config.mode == 2 || reader_seen) policy_observation.reader_connected = lease_eligible;
  const bool active = observing && (config.mode == 1 || reader_seen);
  // Once confirmed, missing stats may request USB-only repair in On too.
  // Never reboot an otherwise healthy USB transport for an app-only stats gap.
  const bool physical_bad = !observation.host_connected || !observation.reader_connected
      || physical_policy.stalled();
  const uint32_t physical_reboot_seconds = Policy::intervalMs(config.step) / 1000;
  const bool reboot_allowed = config.mode == 1 && physical_bad
      && physical_policy.inactiveSeconds() >= physical_reboot_seconds;
  const auto action = policy.update(now, active, policy_observation, true, reboot_allowed);
  const bool logger_active = lease_eligible && !physical_policy.stalled();
  if (observing && observation.supported && logger_active) reader_seen = true;
  if (policy_observation.host_connected && policy_observation.reader_connected && !policy.stalled()) {
    backend_deferred = false;
  }
  const bool auto_due = auto_arm.update(now,
      observing && observation.supported && logger_active, config.mode == 2);
  // The previous reset was already vetoed. Finishing its journal is not a
  // recovery action and must not depend on logging remaining enabled.
  if (event_pending && persistence_ready) {
    if (safeNow(safe, context)) {
      event_pending = false;  // Failed verification disables automatic retries.
      if (saveConfig(config)) config.last_event.persisted = true;
    }
    publish(observation);
    return false;
  }
  if (!observing || !observation.supported) {
    backend_deferred = backend_retry_started = false;
    probe_at = now;
    publish(observation);
    return false;
  }
  if (uint32_t(now - probe_at) >= ProbeMs) {
    probe_at = now;  // Throttle unsafe/busy probes too, never a hot retry loop.
    if ((config.mode == 1 || logger_active)
        && safeNow(safe, context)) probeUsbLoggingTransport();
  }
  if (config.mode == 2) {
    if (auto_due) {
      backend_deferred = !safeNow(safe, context);
      if (!backend_deferred) {
        Config candidate = config;
        candidate.mode = 1;
        candidate.step = 0;  // Fourteen healthy days subsume the ten-minute reset.
        if (saveConfig(candidate)) {
          config = candidate;
          if (config.last_event.sequence) config.last_event.persisted = true;
          policy.begin(now, config.step);
          physical_policy.backoffCommitted(config.step);
          auto_arm.begin(now);
        }
      }
    }
    if (!reader_seen || auto_due) {
      publish(observation);
      return false;
    }
  }
  if (action == Policy::Action::None) {
    publish(observation);
    return false;
  }
  if (!safeNow(safe, context)) {
    policy.update(now, active, policy_observation, false, reboot_allowed);
    publish(observation);
    return false;
  }
  if (action == Policy::Action::ResetBackoff && config.mode == 1) {
    Config candidate = config;
    candidate.step = 0;
    if (saveConfig(candidate)) {
      config = candidate;
      if (config.last_event.sequence) config.last_event.persisted = true;
      policy.backoffCommitted(0);
    }
  } else if (action == Policy::Action::Reboot && config.mode == 1) {
    Config candidate = config;
    candidate.step = Policy::nextStep(config.step);
    candidate.reboots = incrementSaturated(config.reboots);
    candidate.last_event = newEvent(Event::REBOOT_REQUESTED, physicalReasons(observation));
    if (saveConfig(candidate)) {
      config = candidate;
      config.last_event.persisted = true;
      // Recheck real transport progress using the OLD deadline before raising
      // the policy tier. A host may recover while its statefile is committing.
      const auto fresh = observeUsbLoggingTransport();
      const uint32_t fresh_now = millis();
      physical_policy.update(fresh_now, isUsbLoggingEnabled(), fresh);
      const bool fresh_physical_bad = !fresh.host_connected || !fresh.reader_connected
          || physical_policy.stalled();
      auto fresh_policy = fresh;
      if (config.mode == 2 || reader_seen) {
        fresh_policy.reader_connected = fresh.host_connected && fresh.reader_connected
            && isUsbLoggingClientActive();
      }
      const bool still_due = policy.update(fresh_now, active, fresh_policy, true,
          config.mode == 1 && fresh_physical_bad
              && physical_policy.inactiveSeconds() >= physical_reboot_seconds)
          == Policy::Action::Reboot;
      policy.backoffCommitted(config.step);
      physical_policy.backoffCommitted(config.step);
      // A transfer may have gained ownership while flash committed. Keeping
      // the new tier but restarting the interval is safer than racing it.
      if (still_due && safeNow(safe, context)) {
        policy.actionAttempted(action);
        publish(fresh);
        return true;
      }
      policy.restartInterval();
      backend_deferred = still_due;
      // The verified reboot-intent journal is not evidence of a reset. Keep
      // cancellation truthful in RAM even if its best-effort commit fails.
      tickUptime(fresh_now);
      config.last_event = newEvent(Event::REBOOT_CANCELLED, candidate.last_event.reasons);
      event_pending = true;
      if (safeNow(safe, context)) {
        event_pending = false;
        if (saveConfig(config)) config.last_event.persisted = true;
      }
      publish(fresh);
      return false;
    } else {
      config.last_event = candidate.last_event;
    }
  } else if (action == Policy::Action::SoftRecovery || action == Policy::Action::Reenumerate) {
    if (backend_retry_started && uint32_t(now - backend_retry_at) < DeferredRetryMs) {
      publish(observation);
      return false;
    }
    backend_retry_started = true;
    backend_retry_at = now;
    const uint8_t stage = action == Policy::Action::SoftRecovery ? 1 : 2;
    const auto result = recoverUsbLoggingTransport(stage);
    backend_deferred = result == UsbLoggingRecoveryResult::Deferred;
    if (result == UsbLoggingRecoveryResult::Attempted) {
      config.recoveries = incrementSaturated(config.recoveries);
      policy.actionAttempted(action);
      tickUptime(millis());
      uint8_t reasons = physicalReasons(observation);
      if (!reasons) reasons = Event::CLIENT_INACTIVE;
      config.last_event = newEvent(stage == 1 ? Event::SOFT_RECOVERY : Event::REENUMERATE, reasons);
      if (saveConfig(config)) config.last_event.persisted = true;
    } else if (result == UsbLoggingRecoveryResult::Unsupported) {
      backend_unsupported = true;
    }
  }
  publish(observation);
  return false;
}

}  // namespace mesh
#else
// There is no native transport to observe or repair on UART-bridge/unsupported
// targets. Do not spend scarce flash on timers, transactions, or OTA vetoes
// that can never authorize recovery; retain truthful management/CLI status.
namespace mesh {
UsbLoggingStatus usbLoggingStatus() {
  UsbLoggingStatus status;
  status.logging_enabled = isUsbLoggingEnabled();
  return status;
}

void loadUsbLoggingWatchdog(FILESYSTEM*, bool, uint32_t (*)()) {}
bool serviceUsbLoggingWatchdog(bool (*)(void*), void*) { return false; }
bool isUsbLoggingWatchdogArmed() { return false; }
bool isUsbLoggingWatchdogUpdateActive() { return false; }

bool handleUsbLoggingWatchdogCommand(const char* command, char* reply,
                                    size_t capacity) {
  if (!command || !reply || !capacity) return false;
  const char* suffix;
  constexpr size_t prefix_size = sizeof("get usb.watchdog") - 1;
  if (strncmp(command, "get usb.watchdog", prefix_size) == 0) {
    suffix = command + prefix_size;
    if (strncmp(suffix, ".last", 5) == 0) suffix += 5;
  } else if (strncmp(command, "set usb.watchdog", prefix_size) == 0) {
    suffix = command + prefix_size;
  } else {
    return false;
  }
  if (*suffix && *suffix != ' ' && *suffix != '\t') return false;
  snprintf(reply, capacity, "Error: unsupported");
  return true;
}
}  // namespace mesh
#endif  // MESH_USB_CONSOLE_COOPERATIVE
#endif  // ARDUINO
