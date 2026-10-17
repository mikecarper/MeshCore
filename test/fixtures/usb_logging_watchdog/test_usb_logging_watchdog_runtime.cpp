#include <Arduino.h>
#include <FS.h>
#include <helpers/PersistentStoreFormat.h>
#include <helpers/UsbLogging.h>
#include <helpers/UsbLoggingWatchdog.h>
#include <helpers/UsbLoggingWatchdogPolicy.h>
#include <helpers/UsbLoggingClientActivity.h>
#if defined(ENABLE_OTA)
#include <ota/OtaContext.h>
#endif
#include <cassert>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#include <thread>
#include <vector>

using Policy = mesh::UsbLoggingWatchdogPolicy;
using AutoArm = mesh::UsbLoggingAutoArmPolicy;
using Observation = mesh::UsbLoggingObservation;
using Recovery = mesh::UsbLoggingRecoveryResult;
using Event = mesh::UsbLoggingWatchdogEvent;

static Observation observed;
static bool logging_enabled = true;
static Recovery recovery_result = Recovery::Attempted;
static std::vector<uint8_t> recovery_calls;
static unsigned probe_calls = 0;
static bool probe_adds_pending = false;
static bool recovery_pending = false;
static bool client_active = true;
static unsigned client_clear_calls = 0;
static uint32_t event_clock = 1772323200;
static uint32_t read_event_clock() { return event_clock; }

namespace mesh {
bool isUsbLoggingEnabled() { return logging_enabled; }
bool isUsbLoggingTransportRecoveryPending() { return recovery_pending; }
bool isUsbLoggingClientActive() { return client_active; }
void clearUsbLoggingClientActivity() { client_active = false; ++client_clear_calls; }
UsbLoggingObservation observeUsbLoggingTransport() { return observed; }
UsbLoggingRecoveryResult recoverUsbLoggingTransport(uint8_t stage) {
  recovery_calls.push_back(stage);
  return recovery_result;
}
void probeUsbLoggingTransport() {
  ++probe_calls;
  if (probe_adds_pending) observed.pending = true;
  // Producer acceptance never changes tx_progress.
}
}  // namespace mesh

static bool can_recover(void* context) { return *static_cast<bool*>(context); }

static std::vector<uint8_t> image(uint8_t mode, uint8_t step = 0,
                                  uint32_t recoveries = 0, uint32_t reboots = 0,
                                  const Event* event = nullptr, bool legacy = false) {
  std::vector<uint8_t> bytes(legacy ? 20 : 33, 0);
  bytes[0] = 'U'; bytes[1] = 'W'; bytes[2] = legacy ? 1 : 2;
  bytes[4] = mode; bytes[5] = step;
  mesh::storage::writeLE32(bytes.data() + 8, recoveries);
  mesh::storage::writeLE32(bytes.data() + 12, reboots);
  if (event && !legacy) mesh::encodeUsbWatchdogEvent(bytes.data() + 16, *event);
  const size_t crc_offset = bytes.size() - 4;
  mesh::storage::writeLE32(bytes.data() + crc_offset,
      mesh::storage::updateCRC32(0xffffffffU, bytes.data(), crc_offset) ^ 0xffffffffU);
  return bytes;
}

static std::vector<uint8_t> current_image(uint8_t mode, uint8_t step = 0,
                                         uint32_t recoveries = 0, uint32_t reboots = 0) {
  auto event = mesh::usbLoggingStatus().last_event;
  event.persisted = event.sequence != 0;
  return image(mode, step, recoveries, reboots, &event);
}

static void repair_crc(std::vector<uint8_t>& bytes) {
  const size_t crc_offset = bytes.size() - 4;
  mesh::storage::writeLE32(bytes.data() + crc_offset,
      mesh::storage::updateCRC32(0xffffffffU, bytes.data(), crc_offset) ^ 0xffffffffU);
}

struct Fixture {
  fs::FS fs;
  bool safe = true;
  uint32_t origin = 0;
  explicit Fixture(int mode = -1, uint8_t step = 0,
                   uint32_t recoveries = 0, uint32_t reboots = 0,
                   uint32_t start = 0) : origin(start) {
    observed = {true, false, false, false, 0};
    logging_enabled = true;
    recovery_result = Recovery::Attempted;
    recovery_calls.clear();
    probe_calls = 0;
    probe_adds_pending = false;
    recovery_pending = false;
    client_active = false;
    client_clear_calls = 0;
    event_clock = 1772323200;
#if defined(ENABLE_OTA)
    mesh::ota::test_ota_context = nullptr;
#endif
    mock_watchdog_millis = origin;
    fs::stat_filesystem = &fs;
    if (mode >= 0) fs.files["/usb_wdg"] = image(uint8_t(mode), step, recoveries, reboots);
    mesh::loadUsbLoggingWatchdog(&fs, true, read_event_clock);
    // Existing timeline cases model a normally renewing USB stats client.
    // Dedicated lease cases below explicitly control client liveness.
    client_active = true;
  }
  void reload(bool durable = true, bool renewed_stats = true) {
    fs::stat_filesystem = &fs;
    mesh::loadUsbLoggingWatchdog(&fs, durable, read_event_clock);
    if (renewed_stats) client_active = true;
  }
  bool run(uint64_t elapsed) {
    mock_watchdog_millis = uint32_t(uint64_t(origin) + elapsed);
    return mesh::serviceUsbLoggingWatchdog(can_recover, &safe);
  }
  std::string command(const char* command, size_t capacity = 160) {
    struct Buffer { char reply[160]; uint8_t guard[8]; } buffer;
    std::memset(&buffer, 0x5a, sizeof(buffer));
    assert(mesh::handleUsbLoggingWatchdogCommand(command, buffer.reply, capacity));
    for (uint8_t byte : buffer.guard) assert(byte == 0x5a);
    assert(std::memchr(buffer.reply, 0, capacity));
    return buffer.reply;
  }
};

static void enter_final_stage(Fixture& fixture) {
  assert(!fixture.run(0));
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS - 1));
  assert(recovery_calls.empty());
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS));
  assert(recovery_calls == std::vector<uint8_t>{1});
  assert(mesh::usbLoggingStatus().stage == 1);
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS - 1));
  assert(recovery_calls.size() == 1);
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
  assert((recovery_calls == std::vector<uint8_t>{1, 2}));
  assert(mesh::usbLoggingStatus().stage == 2);
}

static void check_default_auto_and_preserved_choices() {
  Fixture fixture;
  auto status = mesh::usbLoggingStatus();
  assert(status.watchdog_auto && !status.watchdog_enabled && status.persistence_ready);
  assert(status.supported && status.logging_enabled && status.backoff_step == 0);
  assert(fixture.fs.files.empty() && fixture.fs.writes == 0);
  assert(!fixture.run(0));
  for (uint32_t day = 1; day <= 80; ++day) assert(!fixture.run(day * 86400000ULL));
  assert(recovery_calls.empty()); // Auto never saw a reader: no recovery/reset.
  assert(fixture.fs.files.empty() && fixture.fs.writes == 0);
  for (uint8_t mode : {uint8_t(0), uint8_t(1), uint8_t(2)}) {
    fixture.fs.files["/usb_wdg"] = image(mode, 7, 12, 34);
    fixture.reload();
    status = mesh::usbLoggingStatus();
    assert(status.watchdog_enabled == (mode == 1));
    assert(status.watchdog_auto == (mode == 2));
    assert(status.backoff_step == 7 && status.retry_seconds == 128U * 3600U);
    assert(status.recovery_count == 12 && status.reboot_count == 34);
    assert(fixture.fs.writes == 0);
  }
}

static void check_failclosed_load_and_exact_format() {
  for (unsigned fault = 0; fault != 13; ++fault) {
    Fixture fixture;
    auto bytes = image(1, 2, 3, 4);
    switch (fault) {
      case 0: bytes[0] ^= 1; break;
      case 1: bytes[2] = 3; break;
      case 2: bytes[3] = 1; break;
      case 3: bytes[4] = 3; break;
      case 4: bytes[5] = 9; break;
      case 5: bytes[6] = 1; break;
      case 6: bytes[7] = 1; break;
      case 7: bytes[16] ^= 1; break;
      case 8: bytes.pop_back(); break;
      case 9: bytes.push_back(0); break;
      case 10: fixture.fs.short_read = "/usb_wdg"; break;
      case 11: fixture.fs.fail_open = "/usb_wdg"; break;
      case 12: fixture.fs.fail_stat = "/usb_wdg"; break;
    }
    if (fault <= 6) repair_crc(bytes); // Reject invalid fields even with valid CRC.
    fixture.fs.files["/usb_wdg"] = bytes;
    fixture.fs.files["/usb_wdg.bak"] = image(1); // Not authority over bad primary.
    fixture.reload();
    auto status = mesh::usbLoggingStatus();
    assert(status.watchdog_auto && !status.watchdog_enabled && !status.persistence_ready);
    assert(!fixture.run(0));
    assert(!fixture.run(Policy::MAX_MS));
    assert(recovery_calls.empty());
    assert(fixture.fs.files.at("/usb_wdg") == bytes);
    assert(fixture.fs.files.count("/usb_wdg.bak") == 1);
    assert(fixture.fs.write_opens == 0 && fixture.fs.renames == 0 && fixture.fs.removes == 0);
    // Explicit durable repair is permitted, unlike unattended recovery.
    const auto reply = fixture.command("set usb.watchdog on");
    if (fault <= 9) {
      assert(reply == "OK - USB watchdog on (saved)");
      assert(fixture.fs.files.at("/usb_wdg") == image(1));
      assert(mesh::usbLoggingStatus().persistence_ready);
    } else {
      assert(reply == "Error: save unconfirmed; reboot may differ");
      assert(!mesh::usbLoggingStatus().persistence_ready);
    }
  }
}

static void check_backup_and_volatile_storage() {
  for (unsigned fault = 0; fault != 4; ++fault) {
    Fixture fixture;
    const auto bytes = image(1, 8, 99, 12);
    fixture.fs.files["/usb_wdg.bak"] = bytes;
    if (fault == 1) fixture.fs.fail_stat = "/usb_wdg";
    if (fault == 2) {
      fixture.fs.fail_rename_from = "/usb_wdg.bak";
      fixture.fs.fail_rename_to = "/usb_wdg";
    }
    if (fault == 3) fixture.fs.fail_open = "/usb_wdg";
    fixture.reload();
    const auto status = mesh::usbLoggingStatus();
    assert(status.persistence_ready == (fault == 0));
    assert(status.watchdog_enabled == (fault == 0));
    if (fault == 0) {
      assert(fixture.fs.files.at("/usb_wdg") == bytes);
      assert(!fixture.fs.exists("/usb_wdg.bak"));
      assert(status.backoff_step == 8 && status.retry_seconds == 168U * 3600U);
    }
    assert(fixture.fs.write_opens == 0);
  }
  Fixture fixture(1, 8);
  fixture.reload(false);
  assert(mesh::usbLoggingStatus().watchdog_auto && !mesh::usbLoggingStatus().persistence_ready);
  assert(!fixture.run(0) && !fixture.run(Policy::MAX_MS));
  assert(recovery_calls.empty() && fixture.fs.write_opens == 0);
  const auto before = fixture.fs.files;
  assert(fixture.command("set usb.watchdog on")
      == "Error: save unconfirmed; reboot may differ");
  assert(fixture.fs.files == before && fixture.fs.write_opens == 0
      && fixture.fs.writes == 0 && fixture.fs.renames == 0 && fixture.fs.removes == 0);
  // Explicit repair cannot bless RAM storage or authorize automatic actions.
  assert(!fixture.run(Policy::MAX_MS + Policy::EARLY_RECOVERY_MS));
  assert(recovery_calls.empty() && probe_calls == 0 && fixture.fs.write_opens == 0);
  mesh::loadUsbLoggingWatchdog(nullptr);
  assert(!mesh::usbLoggingStatus().persistence_ready);
  assert(!mesh::isUsbLoggingWatchdogArmed());
}

static void check_cli_is_strict_bounded_and_transactional() {
  Fixture fixture;
  const auto initial = fixture.command("get usb.watchdog");
  assert(initial.find("> auto ") == 0 && initial.find("usb=1 log=1 host=0") != std::string::npos);
  for (const char* command : {"set usb.watchdog", "set usb.watchdog yes",
                             "set usb.watchdog ON", "set usb.watchdog auto reboot",
                             "set usb.watchdog on ", "get usb.watchdog extra"}) {
    assert(fixture.command(command).find("Error: usage") == 0);
  }
  char reply[2] = {'x', 'x'};
  assert(!mesh::handleUsbLoggingWatchdogCommand("get usb.watchdogish", reply, sizeof(reply)));
  assert(!mesh::handleUsbLoggingWatchdogCommand("set usb.watchdogs on", reply, sizeof(reply)));
  assert(!mesh::handleUsbLoggingWatchdogCommand(nullptr, reply, sizeof(reply)));
  assert(!mesh::handleUsbLoggingWatchdogCommand("get usb.watchdog", nullptr, 1));
  assert(!mesh::handleUsbLoggingWatchdogCommand("get usb.watchdog", reply, 0));
  assert(fixture.command("get usb.watchdog", 1).empty());
  assert(fixture.command("set usb.watchdog\ton") == "OK - USB watchdog on (saved)");
  assert(fixture.fs.files.at("/usb_wdg") == image(1));
  assert(!fixture.fs.exists("/usb_wdg.tmp") && !fixture.fs.exists("/usb_wdg.bak"));
  assert(mesh::usbLoggingStatus().watchdog_enabled);
  assert(fixture.command("set usb.watchdog off") == "OK - USB watchdog off (saved)");
  assert(fixture.fs.files.at("/usb_wdg") == image(0));
  assert(!mesh::usbLoggingStatus().watchdog_enabled && !mesh::usbLoggingStatus().watchdog_auto);
  assert(fixture.command("set usb.watchdog auto") == "OK - USB watchdog auto (saved)");
  assert(fixture.fs.files.at("/usb_wdg") == image(2));
  assert(mesh::usbLoggingStatus().watchdog_auto);
  observed.supported = false;
  const auto before = fixture.fs.writes;
  assert(fixture.command("set usb.watchdog on").find("unsupported") != std::string::npos);
  assert(fixture.fs.writes == before);
  // Dormant Auto is supported as a saved intent on an unobservable transport.
  assert(fixture.command("set usb.watchdog auto") == "OK - USB watchdog auto (saved)");
}

static void check_failed_setter_and_readback_are_not_success() {
  unsigned scenarios = 0;
  for (bool initially_on : {false, true}) {
    const uint8_t previous_mode = initially_on ? 1 : 0;
    const uint8_t candidate_mode = initially_on ? 0 : 1;
    const char* command = initially_on ? "set usb.watchdog off" : "set usb.watchdog on";
    const char* healthy_reply = initially_on ? "OK - USB watchdog off (saved)"
                                            : "OK - USB watchdog on (saved)";
    { // Healthy setters retain their exact ACK and survive a fresh load.
      Fixture healthy(previous_mode, 3, 7, 9);
      assert(healthy.command(command) == healthy_reply);
      assert(healthy.fs.files.at("/usb_wdg") == image(candidate_mode, 3, 7, 9));
      assert(healthy.fs.write_opens == 1);
      healthy.reload();
      const auto status = mesh::usbLoggingStatus();
      assert(status.watchdog_enabled == !initially_on);
      assert(!status.watchdog_auto && status.persistence_ready);
      assert(status.backoff_step == 3 && status.recovery_count == 7 && status.reboot_count == 9);
      assert(healthy.fs.write_opens == 1); // Reload itself never writes.
      ++scenarios;
    }
    for (unsigned fault = 0; fault != 5; ++fault) {
      Fixture fixture(previous_mode, 3, 7, 9);
      const auto old = fixture.fs.files.at("/usb_wdg");
      switch (fault) {
        case 0: fixture.fs.fail_write = true; break;
        case 1: fixture.fs.fail_open = "/usb_wdg.tmp"; break;
        case 2:
          fixture.fs.fail_rename_from = "/usb_wdg.tmp";
          fixture.fs.fail_rename_to = "/usb_wdg";
          break;
        case 3: fixture.fs.corrupt_final_image = true; break;
        case 4: fixture.fs.fail_final_readback = true; break;
      }
      const auto reply = fixture.command(command);
      assert(reply == "Error: save unconfirmed; reboot may differ");
      assert(reply.find("unchanged") == std::string::npos && reply.find("OK") == std::string::npos);
      const auto status = mesh::usbLoggingStatus();
      assert(status.watchdog_enabled == initially_on);
      assert(!status.watchdog_auto && !status.persistence_ready);
      assert(status.backoff_step == 3 && status.recovery_count == 7 && status.reboot_count == 9);
      if (fault <= 2) assert(fixture.fs.files.at("/usb_wdg") == old);
      if (fault == 4) {
        // Final readback cannot distinguish failed publication from a complete
        // candidate which is already durable. Do not claim disk rollback.
        assert(fixture.fs.files.at("/usb_wdg") == image(candidate_mode, 3, 7, 9));
      }
      const auto writes = fixture.fs.write_opens;
      assert(!fixture.run(0) && !fixture.run(Policy::MAX_MS));
      assert(recovery_calls.empty() && probe_calls == 0 && fixture.fs.write_opens == writes);
      fixture.fs.fail_write = false;
      fixture.fs.fail_open.clear();
      fixture.fs.fail_rename_from.clear();
      fixture.fs.fail_rename_to.clear();
      fixture.fs.corrupt_final_image = false;
      fixture.fs.fail_final_readback = false;
      // Merely clearing the fault cannot restore persistence authorization.
      assert(!fixture.run(Policy::MAX_MS + Policy::EARLY_RECOVERY_MS));
      assert(recovery_calls.empty() && probe_calls == 0 && fixture.fs.write_opens == writes);
      assert(!mesh::usbLoggingStatus().persistence_ready);
      assert(fixture.command(command) == healthy_reply);
      assert(fixture.fs.write_opens == writes + 1); // Explicit repair only.
      assert(fixture.fs.files.at("/usb_wdg") == image(candidate_mode, 3, 7, 9));
      assert(mesh::usbLoggingStatus().persistence_ready);
      assert(mesh::usbLoggingStatus().watchdog_enabled == !initially_on);
      ++scenarios;
    }
    { // Independently reboot before retry: the failed setter may be durable.
      Fixture fixture(previous_mode, 3, 7, 9);
      fixture.fs.fail_final_readback = true;
      assert(fixture.command(command) == "Error: save unconfirmed; reboot may differ");
      assert(mesh::usbLoggingStatus().watchdog_enabled == initially_on);
      assert(!mesh::usbLoggingStatus().persistence_ready);
      const auto writes = fixture.fs.write_opens;
      assert(!fixture.run(0) && !fixture.run(Policy::MAX_MS));
      assert(recovery_calls.empty() && probe_calls == 0 && fixture.fs.write_opens == writes);
      fixture.fs.fail_final_readback = false;
      fixture.reload();
      const auto reboot = mesh::usbLoggingStatus();
      assert(reboot.watchdog_enabled == !initially_on);
      assert(!reboot.watchdog_auto && reboot.persistence_ready);
      assert(reboot.backoff_step == 3 && reboot.recovery_count == 7 && reboot.reboot_count == 9);
      assert(fixture.fs.files.at("/usb_wdg") == image(candidate_mode, 3, 7, 9));
      assert(fixture.fs.write_opens == writes); // No retry was needed to publish.
      ++scenarios;
    }
  }
  assert(scenarios == 14);
  puts("14 USB watchdog setter durability scenarios passed");
}

static void check_stages_durable_backoff_and_saturation() {
  for (uint8_t step : {uint8_t(0), uint8_t(7), uint8_t(8)}) {
    Fixture fixture(1, step, 10, 20);
    enter_final_stage(fixture);
    auto status = mesh::usbLoggingStatus();
    assert(status.recovery_count == 12 && status.reboot_count == 20);
    assert(fixture.fs.writes == 2); // Only the two attempted events commit.
    const auto deadline = Policy::intervalMs(step);
    assert(!fixture.run(deadline - 1));
    assert(fixture.run(deadline));
    status = mesh::usbLoggingStatus();
    const uint8_t next = Policy::nextStep(step);
    assert(status.backoff_step == next && status.reboot_count == 21 && status.stage == 3);
    assert(fixture.fs.files.at("/usb_wdg") == current_image(1, next, 12, 21));
    fixture.reload();
    status = mesh::usbLoggingStatus();
    assert(status.backoff_step == next && status.stage == 0);
    assert(status.retry_seconds == Policy::intervalMs(next) / 1000);
  }
  Fixture fixture(1, 8, UINT32_MAX, UINT32_MAX);
  enter_final_stage(fixture);
  assert(fixture.run(Policy::MAX_MS));
  assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 8, UINT32_MAX, UINT32_MAX));
  const auto maximum = fixture.command("get usb.watchdog");
  assert(maximum.size() < 160 && maximum.find("boot=4294967295") != std::string::npos);
}

static void check_deferred_throttle_and_unsupported_backend() {
  Fixture fixture(1);
  recovery_result = Recovery::Deferred;
  assert(!fixture.run(0));
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS));
  assert(recovery_calls == std::vector<uint8_t>{1});
  auto status = mesh::usbLoggingStatus();
  assert(status.stage == 0 && status.recovery_count == 0 && status.recovery_deferred);
  for (uint32_t delay = 1; delay < 5000; ++delay) assert(!fixture.run(Policy::EARLY_RECOVERY_MS + delay));
  assert(recovery_calls.size() == 1);
  recovery_result = Recovery::Attempted;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 5000));
  assert((recovery_calls == std::vector<uint8_t>{1, 1}));
  assert(mesh::usbLoggingStatus().stage == 1 && mesh::usbLoggingStatus().recovery_count == 1);
  assert(!mesh::usbLoggingStatus().recovery_deferred);
  recovery_result = Recovery::Unsupported;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 5000 + Policy::RECOVERY_GRACE_MS));
  assert(!mesh::usbLoggingStatus().supported && !mesh::isUsbLoggingWatchdogArmed());
  const auto calls = recovery_calls.size();
  assert(!fixture.run(Policy::MAX_MS));
  assert(recovery_calls.size() == calls && fixture.fs.writes == 1);
}

static void check_ownership_deferred_and_no_work_when_inactive() {
  for (unsigned reason = 0; reason != 3; ++reason) {
    Fixture fixture(reason == 0 ? 0 : 1);
    if (reason == 1) logging_enabled = false;
    if (reason == 2) observed.supported = false;
    assert(!fixture.run(0));
    assert(!fixture.run(Policy::MAX_MS));
    assert(recovery_calls.empty() && probe_calls == 0 && fixture.fs.writes == 0);
  }
  Fixture fixture(1);
  fixture.safe = false;
  assert(!fixture.run(0));
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS));
  assert(recovery_calls.empty() && probe_calls == 0);
  assert(mesh::usbLoggingStatus().recovery_deferred && mesh::usbLoggingStatus().stage == 0);
  fixture.safe = true;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 1));
  assert(recovery_calls == std::vector<uint8_t>{1});
  fixture.safe = false;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 1 + Policy::RECOVERY_GRACE_MS));
  assert(recovery_calls.size() == 1 && mesh::usbLoggingStatus().recovery_deferred);
  fixture.safe = true;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 2 + Policy::RECOVERY_GRACE_MS));
  assert((recovery_calls == std::vector<uint8_t>{1, 2}));
  fixture.safe = false;
  assert(!fixture.run(Policy::BASE_MS));
  assert(fixture.fs.writes == 2 && mesh::usbLoggingStatus().backoff_step == 0);
  fixture.safe = true;
  assert(fixture.run(Policy::BASE_MS + 1));
  assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
}

static void check_reboot_commit_failure_and_postcommit_veto() {
  Fixture fixture(1);
  enter_final_stage(fixture);
  const auto old = fixture.fs.files.at("/usb_wdg");
  fixture.fs.fail_write = true;
  assert(!fixture.run(Policy::BASE_MS));
  auto status = mesh::usbLoggingStatus();
  assert(!status.persistence_ready && status.stage == 2 && status.backoff_step == 0);
  assert(status.reboot_count == 0 && fixture.fs.files.at("/usb_wdg") == old);
  fixture.fs.fail_write = false;
  const auto writes = fixture.fs.writes;
  assert(!fixture.run(Policy::BASE_MS + Policy::MAX_MS));
  assert(fixture.fs.writes == writes); // A failed durable tier can never reset MCU.

  Fixture race(1);
  enter_final_stage(race);
  race.fs.after_final_rename = [&race]() { race.safe = false; };
  assert(!race.run(Policy::BASE_MS));
  status = mesh::usbLoggingStatus();
  assert(status.persistence_ready && status.backoff_step == 1 && status.stage == 0);
  assert(status.recovery_deferred && status.reboot_count == 1);
  assert(status.last_event.action == Event::REBOOT_CANCELLED && !status.last_event.persisted);
  auto request = status.last_event;
  request.action = Event::REBOOT_REQUESTED;
  --request.sequence;
  request.persisted = true;
  assert(race.fs.files.at("/usb_wdg") == image(1, 1, 2, 1, &request));
  const auto pending_writes = race.fs.write_opens;
  assert(!race.run(Policy::BASE_MS + 1));
  assert(race.fs.write_opens == pending_writes); // No cancellation write while unsafe.
  race.safe = true;
  race.fs.after_final_rename = {};
  assert(!race.run(Policy::BASE_MS + 2));
  assert(mesh::usbLoggingStatus().last_event.persisted);
  assert(race.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
  assert(!race.run(Policy::BASE_MS + Policy::EARLY_RECOVERY_MS));
  assert(!race.run(Policy::BASE_MS + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
  assert(!race.run(Policy::BASE_MS + Policy::intervalMs(1) - 1));
  assert(race.run(Policy::BASE_MS + Policy::intervalMs(1)));
}

static void check_real_progress_not_probe_or_purge_and_master_reenable() {
  Fixture fixture(1);
  observed = {true, true, true, true, 0};
  assert(!fixture.run(0));
  assert(!fixture.run(Policy::STALL_OBSERVE_MS));
  assert(mesh::usbLoggingStatus().stalled);
  assert(!fixture.run(60000));
  assert(probe_calls == 1 && observed.tx_progress == 0 && mesh::usbLoggingStatus().stalled);
  observed.pending = false; // Purge without completion: never health evidence.
  assert(!fixture.run(60001));
  assert(mesh::usbLoggingStatus().stalled);
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS));
  assert(mesh::usbLoggingStatus().stage == 1);
  ++observed.tx_progress;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 1));
  assert(!mesh::usbLoggingStatus().stalled && mesh::usbLoggingStatus().stage == 0);
  assert(!fixture.run(Policy::BASE_MS));
  assert(fixture.fs.writes == 1);
  observed = {true, false, false, false, 1};
  logging_enabled = false;
  assert(!fixture.run(Policy::BASE_MS + 1));
  logging_enabled = true;
  const uint32_t restart = Policy::BASE_MS + 2;
  assert(!fixture.run(restart));
  const auto attempts = recovery_calls.size();
  assert(!fixture.run(restart + Policy::EARLY_RECOVERY_MS - 1));
  assert(recovery_calls.size() == attempts);
  assert(!fixture.run(restart + Policy::EARLY_RECOVERY_MS));
  assert(recovery_calls.size() == attempts + 1);
}

static void check_cancelled_stall_requires_progress_and_restarts_full_recovery() {
  unsigned scenarios = 0;
#if defined(ENABLE_OTA)
  const unsigned veto_kinds = 2;
#else
  const unsigned veto_kinds = 1;
#endif
  for (uint32_t origin : {0U, UINT32_MAX - Policy::BASE_MS / 2}) {
    for (unsigned veto = 0; veto < veto_kinds; ++veto) {
      for (bool completes_after_cancel : {false, true}) {
        Fixture fixture(1, 0, 0, 0, origin);
#if defined(ENABLE_OTA)
        mesh::ota::OtaContext ota;
        if (veto == 1) mesh::ota::test_ota_context = &ota;
#endif
        observed = {true, true, true, true, 7};
        assert(!fixture.run(0));
        assert(!fixture.run(Policy::STALL_OBSERVE_MS));
        assert(mesh::usbLoggingStatus().stalled);
        assert(!fixture.run(Policy::EARLY_RECOVERY_MS));
        assert(!fixture.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
        assert((recovery_calls == std::vector<uint8_t>{1, 2}));
        // Backend cleanup can remove demand without completing a USB TX.
        observed.pending = false;
        assert(!fixture.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS + 1));
        assert(mesh::usbLoggingStatus().stalled && mesh::usbLoggingStatus().stage == 2);
        fixture.fs.after_final_rename = [&] {
#if defined(ENABLE_OTA)
          if (veto == 1) ota.apply_pending = true;
          else
#endif
          fixture.safe = false;
        };
        const uint32_t cancelled_at = Policy::BASE_MS;
        assert(!fixture.run(cancelled_at));
        auto status = mesh::usbLoggingStatus();
        assert(status.persistence_ready && status.backoff_step == 1 && status.stage == 0);
        assert(status.reboot_count == 1 && status.recovery_count == 2);
        assert(status.stalled && !status.logger_active);
        assert(status.last_event.action == Event::REBOOT_CANCELLED
            && status.last_event.reasons == Event::TX_STALLED && !status.last_event.persisted);
        auto request = status.last_event;
        request.action = Event::REBOOT_REQUESTED;
        --request.sequence;
        request.persisted = true;
        assert(fixture.fs.files.at("/usb_wdg") == image(1, 1, 2, 1, &request));
        const auto writes = fixture.fs.write_opens;
        assert(!fixture.run(cancelled_at + 1));
        assert(fixture.fs.write_opens == writes); // Still unsafe: cancellation remains pending.
        assert(!mesh::usbLoggingStatus().last_event.persisted);
        fixture.fs.after_final_rename = {};
        fixture.safe = true;
#if defined(ENABLE_OTA)
        ota.apply_pending = false;
#endif
        assert(!fixture.run(cancelled_at + 2));
        assert(mesh::usbLoggingStatus().last_event.persisted);
        assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
        assert(fixture.fs.write_opens == writes + 1);
        assert(!fixture.run(cancelled_at + 1000));
        status = mesh::usbLoggingStatus();
        assert(status.stalled && status.stage == 0 && status.inactive_seconds == 1
            && "cancelled purge must remain faulty without TX completion");
        assert(status.backoff_step == 1 && !status.logger_active);
        assert(!observed.pending && observed.tx_progress == 7);

        if (completes_after_cancel) {
          // Unlike a purge, a downstream completion is genuine health evidence.
          const uint32_t healthy_at = cancelled_at + 1001;
          ++observed.tx_progress;
          assert(!fixture.run(healthy_at));
          status = mesh::usbLoggingStatus();
          assert(!status.stalled && status.stage == 0 && status.inactive_seconds == 0);
          assert(status.logger_active && status.backoff_step == 1);
          assert(!fixture.run(healthy_at + Policy::HEALTHY_RESET_MS - 1));
          assert(mesh::usbLoggingStatus().backoff_step == 1);
          assert(!fixture.run(healthy_at + Policy::HEALTHY_RESET_MS));
          assert(mesh::usbLoggingStatus().backoff_step == 0);
          assert((recovery_calls == std::vector<uint8_t>{1, 2}));
          assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 0, 2, 1));
        } else {
          assert(!fixture.run(cancelled_at + Policy::EARLY_RECOVERY_MS - 1));
          assert((recovery_calls == std::vector<uint8_t>{1, 2}));
          assert(!fixture.run(cancelled_at + Policy::EARLY_RECOVERY_MS));
          assert((recovery_calls == std::vector<uint8_t>{1, 2, 1}));
          assert(mesh::usbLoggingStatus().stage == 1);
          assert(!fixture.run(cancelled_at + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS - 1));
          assert(recovery_calls.size() == 3);
          assert(!fixture.run(cancelled_at + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
          assert((recovery_calls == std::vector<uint8_t>{1, 2, 1, 2}));
          assert(mesh::usbLoggingStatus().stage == 2);
          assert(!fixture.run(cancelled_at + Policy::HEALTHY_RESET_MS));
          status = mesh::usbLoggingStatus();
          assert(status.stalled && !status.logger_active && status.backoff_step == 1
              && status.recovery_count == 4 && status.reboot_count == 1
              && "purged stalled TX must not reset its durable retry tier as healthy");
          assert(!observed.pending && observed.tx_progress == 7);
          const uint32_t retry_at = cancelled_at + Policy::intervalMs(1);
          assert(!fixture.run(retry_at - 1));
          assert(fixture.run(retry_at));
          status = mesh::usbLoggingStatus();
          assert(status.stage == 3 && status.backoff_step == 2
              && status.recovery_count == 4 && status.reboot_count == 2);
          assert(status.last_event.action == Event::REBOOT_REQUESTED
              && status.last_event.reasons == Event::TX_STALLED && status.last_event.persisted);
          assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 2, 4, 2));
        }
#if defined(ENABLE_OTA)
        mesh::ota::test_ota_context = nullptr;
#endif
        ++scenarios;
      }
    }
  }
  assert(scenarios == 4 * veto_kinds);
  std::printf("%u cancelled-stall recovery timelines passed\n", scenarios);
}

static void check_healthy_reset_is_continuous_safe_and_saved_once() {
  Fixture fixture(1, 5);
  observed = {true, true, true, false, 0};
  assert(!fixture.run(0));
  assert(!fixture.run(Policy::HEALTHY_RESET_MS - 1));
  fixture.safe = false;
  assert(!fixture.run(Policy::HEALTHY_RESET_MS));
  assert(fixture.fs.writes == 0 && mesh::usbLoggingStatus().backoff_step == 5);
  assert(mesh::usbLoggingStatus().recovery_deferred);
  fixture.safe = true;
  assert(!fixture.run(Policy::HEALTHY_RESET_MS + 1));
  assert(fixture.fs.files.at("/usb_wdg") == image(1));
  assert(mesh::usbLoggingStatus().backoff_step == 0);
  const auto writes = fixture.fs.writes;
  assert(!fixture.run(Policy::HEALTHY_RESET_MS * 2));
  assert(fixture.fs.writes == writes);
  fixture.fs.files["/usb_wdg"] = image(1, 7);
  fixture.reload();
  assert(!fixture.run(Policy::HEALTHY_RESET_MS * 2));
  observed.host_connected = false;
  assert(!fixture.run(Policy::HEALTHY_RESET_MS * 2 + 300000));
  observed.host_connected = true;
  client_active = true; // Stats polling resumes with the recovered reader.
  assert(!fixture.run(Policy::HEALTHY_RESET_MS * 2 + 400000));
  assert(!fixture.run(Policy::HEALTHY_RESET_MS * 3 + 399999));
  assert(mesh::usbLoggingStatus().backoff_step == 7);
  assert(!fixture.run(Policy::HEALTHY_RESET_MS * 3 + 400000));
  assert(mesh::usbLoggingStatus().backoff_step == 0);
}

static void check_auto_promotion_continuity_and_failed_write() {
  for (unsigned fault = 0; fault != 3; ++fault) {
    Fixture fixture;
    observed = {true, true, true, false, 0};
    assert(!fixture.run(0));
    assert(!fixture.run(AutoArm::QUALIFICATION_MS - 1));
    assert(mesh::usbLoggingStatus().watchdog_auto && fixture.fs.writes == 0);
    if (fault == 1) fixture.safe = false;
    if (fault == 2) fixture.fs.fail_write = true;
    assert(!fixture.run(AutoArm::QUALIFICATION_MS));
    const auto status = mesh::usbLoggingStatus();
    assert(status.watchdog_enabled == (fault == 0));
    assert(status.watchdog_auto == (fault != 0));
    assert(status.persistence_ready == (fault != 2));
    assert(recovery_calls.empty());
    if (fault == 0) {
      assert(fixture.fs.files.at("/usb_wdg") == image(1));
      assert(status.auto_connected_seconds == 0);
      observed.host_connected = false;
      assert(!fixture.run(AutoArm::QUALIFICATION_MS + 1));
      assert(!fixture.run(AutoArm::QUALIFICATION_MS + Policy::EARLY_RECOVERY_MS));
      assert(recovery_calls.empty());
      assert(!fixture.run(AutoArm::QUALIFICATION_MS + Policy::EARLY_RECOVERY_MS + 1));
      assert(recovery_calls == std::vector<uint8_t>{1});
    } else if (fault == 1) {
      assert(status.recovery_deferred && fixture.fs.writes == 0);
      fixture.safe = true;
      assert(!fixture.run(AutoArm::QUALIFICATION_MS + 1));
      assert(fixture.fs.files.at("/usb_wdg") == image(1));
    } else {
      const auto writes = fixture.fs.writes;
      assert(!fixture.run(AutoArm::QUALIFICATION_MS + Policy::MAX_MS));
      assert(fixture.fs.writes == writes && recovery_calls.empty());
    }
  }
  for (unsigned reason = 0; reason != 4; ++reason) {
    Fixture fixture;
    observed = {true, true, true, false, 0};
    assert(!fixture.run(0));
    assert(!fixture.run(AutoArm::QUALIFICATION_MS - 1));
    if (reason == 0) observed.host_connected = false;
    if (reason == 1) observed.reader_connected = false;
    if (reason == 2) logging_enabled = false;
    if (reason == 3) observed.supported = false;
    assert(!fixture.run(AutoArm::QUALIFICATION_MS));
    assert(mesh::usbLoggingStatus().auto_connected_seconds == 0);
    logging_enabled = true;
    observed = {true, true, true, false, 0};
    client_active = true; // Reopened application resumes its normal stats polls.
    const uint64_t restart = uint64_t(AutoArm::QUALIFICATION_MS) + 1;
    assert(!fixture.run(restart));
    assert(!fixture.run(restart + AutoArm::QUALIFICATION_MS - 1));
    assert(mesh::usbLoggingStatus().watchdog_auto);
    assert(!fixture.run(restart + AutoArm::QUALIFICATION_MS));
    assert(mesh::usbLoggingStatus().watchdog_enabled);
  }
  Fixture previous_tier(2, 8, 17, 29);
  observed = {true, true, true, false, 0};
  assert(!previous_tier.run(0));
  assert(!previous_tier.run(Policy::HEALTHY_RESET_MS));
  assert(previous_tier.fs.writes == 0 && mesh::usbLoggingStatus().backoff_step == 8);
  assert(!previous_tier.run(AutoArm::QUALIFICATION_MS));
  assert(previous_tier.fs.files.at("/usb_wdg") == image(1, 0, 17, 29));
  assert(mesh::usbLoggingStatus().watchdog_enabled && mesh::usbLoggingStatus().backoff_step == 0);
}

static void check_auto_seen_reader_usb_only_and_probe_is_not_health() {
  Fixture fixture;
  observed = {true, true, true, false, 0};
  assert(!fixture.run(0));
  observed.host_connected = observed.reader_connected = false;
  assert(!fixture.run(1));
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 1));
  assert(recovery_calls == std::vector<uint8_t>{1});
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS + 1));
  assert((recovery_calls == std::vector<uint8_t>{1, 2}));
  for (uint32_t day = 1; day <= 80; ++day) assert(!fixture.run(day * 86400000ULL));
  assert(recovery_calls.size() == 2 && fixture.fs.writes == 2);
  assert(mesh::usbLoggingStatus().watchdog_auto && !mesh::usbLoggingStatus().watchdog_enabled);
  assert(mesh::usbLoggingStatus().backoff_step == 0 && mesh::usbLoggingStatus().reboot_count == 0);

  Fixture stalled;
  observed = {true, true, true, false, 0};
  probe_adds_pending = true;
  assert(!stalled.run(0));
  assert(!stalled.run(60000));
  assert(probe_calls == 1 && observed.pending && observed.tx_progress == 0);
  assert(!stalled.run(60001));
  assert(!stalled.run(90001));
  assert(mesh::usbLoggingStatus().stalled && mesh::usbLoggingStatus().auto_connected_seconds == 0);
  observed.pending = false;
  assert(!stalled.run(90002));
  assert(mesh::usbLoggingStatus().stalled && mesh::usbLoggingStatus().auto_connected_seconds == 0);
  probe_adds_pending = false;
  observed.tx_progress = 1;
  assert(!stalled.run(90003));
  assert(!mesh::usbLoggingStatus().stalled);
  assert(!stalled.run(uint64_t(90003) + AutoArm::QUALIFICATION_MS - 1));
  assert(mesh::usbLoggingStatus().watchdog_auto);
  assert(!stalled.run(uint64_t(90003) + AutoArm::QUALIFICATION_MS));
  assert(mesh::usbLoggingStatus().watchdog_enabled);
}

static void check_millis_rollover_and_restart_requalifies_auto() {
  Fixture fixture(1, 0, 0, 0, UINT32_MAX - 1500);
  enter_final_stage(fixture);
  assert(!fixture.run(Policy::BASE_MS - 1));
  assert(fixture.run(Policy::BASE_MS));
  assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
  Fixture automatic(2, 0, 0, 0, UINT32_MAX - 1500);
  observed = {true, true, true, false, 0};
  assert(!automatic.run(0));
  assert(!automatic.run(AutoArm::QUALIFICATION_MS - 1));
  automatic.reload(); // Reboot cannot prove a reader remained connected.
  assert(mesh::usbLoggingStatus().auto_connected_seconds == 0);
  assert(!automatic.run(AutoArm::QUALIFICATION_MS - 1));
  assert(!automatic.run(uint64_t(AutoArm::QUALIFICATION_MS) * 2 - 2));
  assert(mesh::usbLoggingStatus().watchdog_auto);
  assert(!automatic.run(uint64_t(AutoArm::QUALIFICATION_MS) * 2 - 1));
  assert(mesh::usbLoggingStatus().watchdog_enabled);
}

static void check_pending_attach_keeps_service_hint_after_disable() {
  Fixture fixture(1);
  assert(mesh::isUsbLoggingWatchdogArmed());
  recovery_pending = true;
  assert(fixture.command("set usb.watchdog off") == "OK - USB watchdog off (saved)");
  logging_enabled = false;
  assert(mesh::isUsbLoggingWatchdogArmed()); // Finish descriptor-preserving attach.
  assert(!fixture.run(0));
  assert(recovery_calls.empty());
  recovery_pending = false;
  assert(!mesh::isUsbLoggingWatchdogArmed());
}

static void check_stats_lease_gates_auto_not_physical_on_mode() {
  Fixture unattended;
  observed = {true, true, true, false, 0};
  client_active = false; // Plugged USB and DTR do not identify a logging client.
  assert(!unattended.run(0));
  for (uint32_t day = 1; day <= 21; ++day) {
    ++observed.tx_progress; // Even kernel USB completions are not stats polls.
    assert(!unattended.run(day * 86400000ULL));
    const auto status = mesh::usbLoggingStatus();
    assert(status.host_connected && status.reader_connected && !status.logger_active);
    assert(status.watchdog_auto && status.auto_connected_seconds == 0 && status.stage == 0);
  }
  assert(recovery_calls.empty() && probe_calls == 0 && unattended.fs.writes == 0);

  Fixture active;
  observed = {true, true, true, false, 0};
  assert(!active.run(0));
  assert(!active.run(mesh::USB_LOGGING_CLIENT_LEASE_MS - 1));
  assert(mesh::usbLoggingStatus().logger_active);
  client_active = false;
  const uint32_t expired = mesh::USB_LOGGING_CLIENT_LEASE_MS;
  assert(!active.run(expired));
  assert(mesh::usbLoggingStatus().auto_connected_seconds == 0);
  // ACKs without application polls cannot keep delaying Auto's USB-only repair.
  for (uint32_t elapsed = 10000; elapsed < Policy::EARLY_RECOVERY_MS; elapsed += 10000) {
    ++observed.tx_progress;
    assert(!active.run(expired + elapsed));
    assert(recovery_calls.empty());
  }
  ++observed.tx_progress;
  assert(!active.run(expired + Policy::EARLY_RECOVERY_MS));
  assert(recovery_calls == std::vector<uint8_t>{1});
  ++observed.tx_progress;
  assert(!active.run(expired + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
  assert((recovery_calls == std::vector<uint8_t>{1, 2}));
  const uint32_t renewed = 3U * 86400000U;
  assert(!active.run(renewed));
  assert(active.fs.writes == 2 && mesh::usbLoggingStatus().reboot_count == 0);
  client_active = true;
  ++observed.tx_progress;
  assert(!active.run(renewed + 1));
  assert(mesh::usbLoggingStatus().logger_active && mesh::usbLoggingStatus().stage == 0);
  assert(!active.run(uint64_t(renewed) + AutoArm::QUALIFICATION_MS));
  assert(mesh::usbLoggingStatus().watchdog_auto);
  assert(!active.run(uint64_t(renewed) + AutoArm::QUALIFICATION_MS + 1));
  assert(active.fs.files.at("/usb_wdg") == current_image(1, 0, 2, 0));
  // Promotion retains the proven stats reader. Its later absence can repair
  // USB, but healthy physical USB alone cannot authorize an MCU reset.
  client_active = false;
  ++observed.tx_progress;
  const uint64_t stopped = uint64_t(renewed) + AutoArm::QUALIFICATION_MS + Policy::BASE_MS;
  assert(!active.run(stopped));
  assert(mesh::usbLoggingStatus().watchdog_enabled && !mesh::usbLoggingStatus().logger_active);
  assert(mesh::usbLoggingStatus().stage == 0 && mesh::usbLoggingStatus().reboot_count == 0);
  ++observed.tx_progress;
  assert(!active.run(stopped + Policy::EARLY_RECOVERY_MS));
  assert(mesh::usbLoggingStatus().stage == 1 && recovery_calls.size() == 3);
  ++observed.tx_progress;
  assert(!active.run(stopped + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
  assert(mesh::usbLoggingStatus().stage == 2 && recovery_calls.size() == 4);
  for (uint64_t day = 1; day <= 80; ++day) {
    ++observed.tx_progress;
    assert(!active.run(stopped + day * 86400000ULL));
    assert(mesh::usbLoggingStatus().reboot_count == 0);
  }
  assert(active.fs.files.at("/usb_wdg") == current_image(1, 0, 4, 0));

  Fixture non_stats_reader(1);
  observed = {true, true, true, false, 0};
  client_active = false;
  assert(!non_stats_reader.run(0));
  for (uint64_t day = 1; day <= 21; ++day) {
    ++observed.tx_progress;
    assert(!non_stats_reader.run(day * 86400000ULL));
    assert(mesh::usbLoggingStatus().watchdog_enabled && mesh::usbLoggingStatus().stage == 0);
  }
  assert(recovery_calls.empty() && non_stats_reader.fs.writes == 0);
}

static void check_lease_clears_on_load_master_off_and_disconnect() {
  Fixture fixture;
  assert(client_clear_calls == 1);
  observed = {true, true, true, false, 0};
  assert(!fixture.run(0));
  assert(mesh::usbLoggingStatus().logger_active);
  fixture.reload(true, false);
  assert(!client_active && client_clear_calls == 2);
  assert(!fixture.run(1));
  assert(!mesh::usbLoggingStatus().logger_active);
  assert(mesh::usbLoggingStatus().auto_connected_seconds == 0);
  client_active = true;
  assert(!fixture.run(2));
  assert(mesh::usbLoggingStatus().logger_active);
  logging_enabled = false;
  assert(!fixture.run(3));
  assert(!client_active && client_clear_calls == 3);
  logging_enabled = true;
  client_active = true;
  observed.host_connected = false;
  assert(!fixture.run(4));
  assert(!client_active && client_clear_calls == 4);
  observed.host_connected = true;
  client_active = true;
  observed.reader_connected = false;
  assert(!fixture.run(5));
  assert(!client_active && client_clear_calls == 5);
  observed.reader_connected = true;
  assert(!fixture.run(6));
  assert(!mesh::usbLoggingStatus().logger_active);
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 6));
  assert(recovery_calls.empty()); // Master OFF deliberately forgets the old client.
  client_active = true;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 7));
  assert(mesh::usbLoggingStatus().logger_active);
  client_active = false;
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + 8));
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS * 2 + 8));
  // A freshly proven stats client is remembered for USB-only outage repair.
  assert(recovery_calls == std::vector<uint8_t>{1});
}

static void check_commit_time_progress_cancels_board_reset() {
  Fixture fixture(1);
  enter_final_stage(fixture);
  fixture.fs.after_final_rename = [] {
    observed = {true, true, true, false, 1}; // Actual ACK/reconnected transport.
    client_active = true;
  };
  assert(!fixture.run(Policy::BASE_MS));
  const auto status = mesh::usbLoggingStatus();
  assert(status.persistence_ready && status.backoff_step == 1);
  assert(status.stage == 0 && !status.recovery_deferred && status.inactive_seconds == 0);
  assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
  assert(!fixture.run(Policy::BASE_MS + 1));
}

static void check_app_outage_cannot_consume_physical_reboot_deadline() {
  for (bool stalled_tx : {false, true}) {
    Fixture fixture(1);
    observed = {true, true, true, false, 0};
    assert(!fixture.run(0));
    client_active = false;
    const uint32_t expired = mesh::USB_LOGGING_CLIENT_LEASE_MS;
    assert(!fixture.run(expired));
    ++observed.tx_progress;
    assert(!fixture.run(expired + Policy::EARLY_RECOVERY_MS));
    ++observed.tx_progress;
    assert(!fixture.run(expired + Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
    assert(mesh::usbLoggingStatus().stage == 2);
    const uint32_t physical_fault = 2U * 86400000U;
    ++observed.tx_progress;
    assert(!fixture.run(physical_fault));
    assert(fixture.fs.writes == 2); // App-only loss saved USB attempts, not a board reset.
    if (stalled_tx) observed.pending = true;
    else observed.host_connected = observed.reader_connected = false;
    assert(!fixture.run(physical_fault));
    assert(!fixture.run(physical_fault + Policy::STALL_OBSERVE_MS));
    assert(!fixture.run(physical_fault + Policy::BASE_MS - 1));
    assert(fixture.fs.writes == 2 && mesh::usbLoggingStatus().reboot_count == 0);
    assert(fixture.run(physical_fault + Policy::BASE_MS));
    assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
  }
}

static void check_legacy_state_lazy_upgrade_and_backup_length() {
  for (uint8_t mode : {uint8_t(0), uint8_t(1), uint8_t(2)}) {
    Fixture fixture;
    const auto legacy = image(mode, 8, 19, 23, nullptr, true);
    fixture.fs.files["/usb_wdg"] = legacy;
    fixture.reload();
    const auto status = mesh::usbLoggingStatus();
    assert(status.persistence_ready && status.backoff_step == 8);
    assert(status.watchdog_enabled == (mode == 1) && status.watchdog_auto == (mode == 2));
    assert(status.recovery_count == 19 && status.reboot_count == 23);
    assert(status.last_event.sequence == 0 && !status.last_event.persisted);
    assert(fixture.fs.write_opens == 0 && fixture.fs.files.at("/usb_wdg") == legacy);
    const char* command = mode == 0 ? "set usb.watchdog off"
        : mode == 1 ? "set usb.watchdog on" : "set usb.watchdog auto";
    assert(fixture.command(command).find("(saved)") != std::string::npos);
    assert(fixture.fs.files.at("/usb_wdg") == image(mode, 8, 19, 23));
    assert(fixture.fs.write_opens == 1);
  }
  for (bool legacy : {false, true}) {
    Fixture fixture;
    const auto saved = image(1, 7, 8, 9, nullptr, legacy);
    fixture.fs.files["/usb_wdg.bak"] = saved;
    fixture.reload();
    assert(mesh::usbLoggingStatus().persistence_ready);
    assert(fixture.fs.files.at("/usb_wdg") == saved && fixture.fs.write_opens == 0);
  }
}

static void check_event_persistence_latest_only_and_clock_history() {
  Fixture fixture(1);
  event_clock = 1900000123;
  assert(!fixture.run(0));
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS));
  auto event = mesh::usbLoggingStatus().last_event;
  assert(event.action == Event::SOFT_RECOVERY && event.reasons == 3 && event.sequence == 1);
  assert(event.epoch == event_clock && event.uptime_seconds == 300 && event.persisted);
  assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 0, 1, 0));
  const auto writes = fixture.fs.write_opens;
  for (uint32_t tick = 1; tick != 10; ++tick) {
    assert(!fixture.run(Policy::EARLY_RECOVERY_MS + tick));
    assert(fixture.fs.write_opens == writes); // Probes and repeated polls are not events.
  }
  event_clock = 1800000456; // A corrected node clock may move backward.
  assert(!fixture.run(Policy::EARLY_RECOVERY_MS + Policy::RECOVERY_GRACE_MS));
  event = mesh::usbLoggingStatus().last_event;
  assert(event.action == Event::REENUMERATE && event.sequence == 2 && event.epoch == event_clock);
  assert(event.uptime_seconds == 360 && event.persisted && fixture.fs.files.size() == 1);
  const auto saved = fixture.fs.files.at("/usb_wdg");
  mock_watchdog_millis = 100; // Reboot: historical event uptime is not this boot's uptime.
  fixture.reload();
  event = mesh::usbLoggingStatus().last_event;
  assert(event.sequence == 2 && event.uptime_seconds == 360 && event.epoch == event_clock);
  assert(fixture.fs.files.at("/usb_wdg") == saved && fixture.fs.write_opens == writes + 1);
  assert(fixture.command("get usb.watchdog.last").find(
      "> reenumerate reasons=0x03 epoch=1800000456 uptime=360s seq=2 persisted=1") == 0);

  Fixture no_clock(1);
  mesh::loadUsbLoggingWatchdog(&no_clock.fs); // Missing callback must not invent UTC.
  assert(!no_clock.run(0) && !no_clock.run(Policy::EARLY_RECOVERY_MS));
  assert(mesh::usbLoggingStatus().last_event.epoch == 0);
  Fixture wrap(1, 0, 0, 0, UINT32_MAX - 1500);
  assert(!wrap.run(0) && !wrap.run(Policy::EARLY_RECOVERY_MS));
  assert(mesh::usbLoggingStatus().last_event.uptime_seconds
      == (uint64_t(wrap.origin) + Policy::EARLY_RECOVERY_MS) / 1000);

  Fixture off(0, 0, 0, 0, UINT32_MAX - 1500);
  assert(!off.run(Policy::EARLY_RECOVERY_MS)); // Off still extends the uptime clock.
  assert(off.command("set usb.watchdog on").find("(saved)") != std::string::npos);
  assert(!off.run(Policy::EARLY_RECOVERY_MS + 1));
  assert(!off.run(Policy::EARLY_RECOVERY_MS * 2 + 1));
  assert(mesh::usbLoggingStatus().last_event.uptime_seconds
      == (uint64_t(off.origin) + Policy::EARLY_RECOVERY_MS * 2 + 1) / 1000);
}

static void check_event_reasons_failclosed_and_manual_repair() {
  Fixture app;
  observed = {true, true, true, false, 0};
  assert(!app.run(0));
  client_active = false;
  assert(!app.run(1) && !app.run(Policy::EARLY_RECOVERY_MS + 1));
  assert(mesh::usbLoggingStatus().last_event.reasons == Event::CLIENT_INACTIVE);

  Fixture tx(1);
  observed = {true, true, true, true, 0};
  assert(!tx.run(0) && !tx.run(Policy::STALL_OBSERVE_MS));
  assert(!tx.run(Policy::EARLY_RECOVERY_MS));
  assert(mesh::usbLoggingStatus().last_event.reasons == Event::TX_STALLED);

  Fixture failing(1);
  const auto old = failing.fs.files.at("/usb_wdg");
  failing.fs.fail_write = true;
  assert(!failing.run(0) && !failing.run(Policy::EARLY_RECOVERY_MS));
  const auto status = mesh::usbLoggingStatus();
  assert(status.last_event.action == Event::SOFT_RECOVERY && !status.last_event.persisted);
  assert(!status.persistence_ready && status.recovery_count == 1);
  assert(failing.fs.files.at("/usb_wdg") == old);
  const auto attempts = recovery_calls.size();
  const auto writes = failing.fs.write_opens;
  failing.fs.fail_write = false;
  assert(!failing.run(Policy::MAX_MS));
  assert(recovery_calls.size() == attempts && failing.fs.write_opens == writes);
  assert(failing.command("set usb.watchdog on").find("(saved)") != std::string::npos);
  assert(mesh::usbLoggingStatus().last_event.persisted);
  assert(failing.fs.files.at("/usb_wdg") == current_image(1, 0, 1, 0));
}

static void check_bad_event_images_are_not_fresh_state() {
  for (unsigned fault = 0; fault != 8; ++fault) {
    Fixture fixture;
    Event event;
    event.reasons = Event::TX_STALLED;
    event.action = Event::SOFT_RECOVERY;
    event.sequence = 7;
    event.persisted = true;
    auto bytes = image(1, 2, 3, 4, &event);
    switch (fault) {
      case 0: bytes[16] &= 0xf0; break; // No reason.
      case 1: bytes[16] &= 0x8f; break; // NONE action with nonzero sequence.
      case 2: bytes[16] = 0xd4; break; // Unknown action 5.
      case 3: bytes[16] &= 0x7f; break; // Disk cannot claim an unpersisted event.
      case 4: mesh::storage::writeLE32(bytes.data() + 25, 0); break;
      case 5: bytes[2] = 1; break; // UW1 must have the old exact size.
      case 6: bytes.resize(20); break; // UW2 must have the new exact size.
      case 7: bytes[17] ^= 1; break; // Event covered by CRC.
    }
    if (fault != 7) repair_crc(bytes);
    fixture.fs.files["/usb_wdg"] = bytes;
    fixture.fs.files["/usb_wdg.bak"] = image(1);
    fixture.reload();
    const auto status = mesh::usbLoggingStatus();
    assert(!status.persistence_ready && status.last_event.sequence == 0);
    assert(!fixture.run(0) && !fixture.run(Policy::MAX_MS));
    assert(recovery_calls.empty() && fixture.fs.write_opens == 0 && fixture.fs.renames == 0);
    assert(fixture.fs.files.at("/usb_wdg") == bytes);
  }
}

static void check_pending_cancellation_flush_ignores_logging_and_backend() {
  for (bool unavailable_backend : {false, true}) {
    Fixture fixture(1);
    enter_final_stage(fixture);
    fixture.fs.after_final_rename = [&fixture] { fixture.safe = false; };
    assert(!fixture.run(Policy::BASE_MS));
    assert(mesh::usbLoggingStatus().last_event.action == Event::REBOOT_CANCELLED);
    assert(!mesh::usbLoggingStatus().last_event.persisted);
    const auto writes = fixture.fs.write_opens;
    const auto probes = probe_calls;
    const auto recoveries = recovery_calls.size();
    logging_enabled = false;
    if (unavailable_backend) observed.supported = false;
    assert(!fixture.run(Policy::BASE_MS + 1));
    assert(fixture.fs.write_opens == writes);
    fixture.safe = true;
    fixture.fs.after_final_rename = {};
    assert(!fixture.run(Policy::BASE_MS + 2));
    assert(mesh::usbLoggingStatus().last_event.persisted);
    assert(fixture.fs.write_opens == writes + 1);
    assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
    assert(!fixture.run(Policy::BASE_MS + 3));
    assert(fixture.fs.write_opens == writes + 1);
    assert(probe_calls == probes && recovery_calls.size() == recoveries);
  }
  Fixture failed(1);
  enter_final_stage(failed);
  failed.fs.after_final_rename = [&failed] { failed.safe = false; };
  assert(!failed.run(Policy::BASE_MS));
  failed.safe = true;
  failed.fs.after_final_rename = {};
  failed.fs.fail_write = true;
  assert(!failed.run(Policy::BASE_MS + 1));
  assert(!mesh::usbLoggingStatus().persistence_ready);
  assert(!mesh::usbLoggingStatus().last_event.persisted);
  const auto writes = failed.fs.write_opens;
  failed.fs.fail_write = false;
  assert(!failed.run(Policy::BASE_MS + Policy::MAX_MS));
  assert(failed.fs.write_opens == writes); // No automatic retry after failed commit.
  assert(failed.command("set usb.watchdog off").find("(saved)") != std::string::npos);
  assert(mesh::usbLoggingStatus().last_event.persisted);
  assert(failed.fs.files.at("/usb_wdg") == current_image(0, 1, 2, 1));
}

static void check_event_cli_is_strict_and_bounded() {
  Fixture fixture;
  Event event;
  event.action = Event::REBOOT_CANCELLED;
  event.reasons = 15;
  event.epoch = event.uptime_seconds = event.sequence = UINT32_MAX;
  event.persisted = true;
  fixture.fs.files["/usb_wdg"] = image(1, 8, UINT32_MAX, UINT32_MAX, &event);
  fixture.reload();
  const auto reply = fixture.command("get usb.watchdog.last");
  assert(reply == "> reboot-cancelled reasons=0x0F epoch=4294967295 uptime=4294967295s seq=4294967295 persisted=1");
  assert(reply.size() < 160);
  for (size_t capacity = 0; capacity <= 160; ++capacity) {
    unsigned char buffer[168];
    memset(buffer, 0x5a, sizeof(buffer));
    const bool handled = mesh::handleUsbLoggingWatchdogCommand("get usb.watchdog.last",
        reinterpret_cast<char*>(buffer), capacity);
    assert(handled == (capacity != 0));
    if (capacity) assert(memchr(buffer, 0, capacity));
    for (size_t i = capacity; i < sizeof(buffer); ++i) assert(buffer[i] == 0x5a);
  }
  char buffer[160];
  assert(!mesh::handleUsbLoggingWatchdogCommand("get usb.watchdog.lastx", buffer, sizeof(buffer)));
  assert(!mesh::handleUsbLoggingWatchdogCommand("set usb.watchdog.last on", buffer, sizeof(buffer)));
  assert(fixture.command("get usb.watchdog.last extra") == "Error: usage get usb.watchdog.last");
  assert(fixture.command("set usb.watchdog off").find("(saved)") != std::string::npos);
  assert(mesh::usbLoggingStatus().last_event.sequence == UINT32_MAX); // Setter preserves history.
}

static void check_event_snapshot_is_coherent_with_concurrent_readers() {
  Fixture fixture(1);
  std::atomic<bool> running{true};
  std::atomic<unsigned> snapshots{0};
  std::thread reader([&] {
    while (running.load()) {
      const auto status = mesh::usbLoggingStatus();
      const auto event = status.last_event;
      if (event.sequence) {
        assert(status.persistence_ready && status.watchdog_enabled);
        assert(status.recovery_count == event.sequence);
        assert(status.backoff_step == 0 && status.retry_seconds == 3600);
        assert(event.epoch == 2000000000U + event.sequence);
        assert(event.action == Event::SOFT_RECOVERY && event.reasons == 3 && event.persisted);
      } else if (!status.persistence_ready) {
        // A bounded busy-reader fallback has no mixed partial journal/tier.
        assert(status.supported && status.logging_enabled && status.watchdog_enabled);
        assert(status.reader_connected && !status.stalled && status.recovery_deferred);
        assert(status.backoff_step == 8 && status.retry_seconds == 604800);
        assert(!status.recovery_count && !status.reboot_count && !status.auto_connected_seconds);
      }
      ++snapshots;
    }
  });
  uint64_t now = 0;
  for (uint32_t sequence = 1; sequence <= 200; ++sequence) {
    observed = {true, false, false, false, sequence};
    assert(!fixture.run(now));
    event_clock = 2000000000U + sequence;
    now += Policy::EARLY_RECOVERY_MS;
    assert(!fixture.run(now));
    observed = {true, true, true, false, sequence + 1};
    client_active = true;
    assert(!fixture.run(++now));
    ++now;
  }
  running = false;
  reader.join();
  assert(snapshots.load() && mesh::usbLoggingStatus().last_event.sequence == 200);
}

#if defined(ENABLE_OTA)
static void check_actual_update_activity_guard() {
  assert(!mesh::isUsbLoggingWatchdogUpdateActive());
  for (unsigned reason = 0; reason != 9; ++reason) {
    Fixture fixture(1);
    mesh::ota::OtaContext ota;
    ota.serving = true; // Normal idle self-firmware advertising is not activity.
    mesh::ota::test_ota_context = &ota;
    assert(!mesh::isUsbLoggingWatchdogUpdateActive());
    switch (reason) {
      case 0: ota.apply_pending = true; break;
      case 1: ota.bootloader_apply_pending = true; break;
      case 2: ota.folder_active = true; break;
      case 3: ota.folder_dest = true; break;
      case 4: ota.capture_waiting = true; break;
      case 5: ota.manager.serve_jobs = 1; break;
      case 6: ota.manager.manifest_jobs = 1; break;
      case 7: ota.serve_expected = true; break;
      case 8: ota.manager.state = mesh::ota::OtaManager::FETCHING; break;
    }
    assert(mesh::isUsbLoggingWatchdogUpdateActive());
    assert(!fixture.run(0));
    assert(!fixture.run(Policy::EARLY_RECOVERY_MS));
    assert(!fixture.run(Policy::BASE_MS));
    assert(recovery_calls.empty() && probe_calls == 0 && fixture.fs.writes == 0);
    assert(mesh::usbLoggingStatus().stage == 0 && mesh::usbLoggingStatus().recovery_deferred);
    ota = {};
    ota.serving = true;
    assert(!mesh::isUsbLoggingWatchdogUpdateActive());
    assert(!fixture.run(Policy::BASE_MS + 1));
    assert(recovery_calls == std::vector<uint8_t>{1});
    ota.manager.manifest_jobs = 1;
    assert(!fixture.run(Policy::BASE_MS + 1 + Policy::RECOVERY_GRACE_MS));
    assert(recovery_calls.size() == 1 && fixture.fs.writes == 1);
    ota.manager.manifest_jobs = 0;
    assert(!fixture.run(Policy::BASE_MS + 2 + Policy::RECOVERY_GRACE_MS));
    assert((recovery_calls == std::vector<uint8_t>{1, 2}));
    ota.manager.serve_jobs = 1;
    assert(!fixture.run(Policy::BASE_MS + 2 + Policy::RECOVERY_GRACE_MS * 2));
    assert(mesh::usbLoggingStatus().stage == 2 && fixture.fs.writes == 2);
    ota.manager.serve_jobs = 0;
    assert(fixture.run(Policy::BASE_MS + 3 + Policy::RECOVERY_GRACE_MS * 2));
    assert(fixture.fs.files.at("/usb_wdg") == current_image(1, 1, 2, 1));
    ota.manager.state = mesh::ota::OtaManager::FAILED;
    assert(!mesh::isUsbLoggingWatchdogUpdateActive());
    ota.manager.state = mesh::ota::OtaManager::COMPLETE;
    assert(mesh::isUsbLoggingWatchdogUpdateActive());
    mesh::ota::test_ota_context = nullptr;
  }
  Fixture automatic;
  mesh::ota::OtaContext ota;
  ota.folder_active = true;
  mesh::ota::test_ota_context = &ota;
  observed = {true, true, true, false, 0};
  assert(!automatic.run(0));
  assert(!automatic.run(AutoArm::QUALIFICATION_MS));
  assert(automatic.fs.writes == 0 && mesh::usbLoggingStatus().watchdog_auto);
  ota.folder_active = false;
  assert(!automatic.run(AutoArm::QUALIFICATION_MS + 1));
  assert(automatic.fs.files.at("/usb_wdg") == image(1));
  mesh::ota::test_ota_context = nullptr;
}
#endif

int main() {
  setbuf(stdout, nullptr);
  check_default_auto_and_preserved_choices();
  check_failclosed_load_and_exact_format();
  check_backup_and_volatile_storage();
  check_cli_is_strict_bounded_and_transactional();
  check_failed_setter_and_readback_are_not_success();
  check_stages_durable_backoff_and_saturation();
  check_deferred_throttle_and_unsupported_backend();
  check_ownership_deferred_and_no_work_when_inactive();
  check_reboot_commit_failure_and_postcommit_veto();
  check_real_progress_not_probe_or_purge_and_master_reenable();
  check_cancelled_stall_requires_progress_and_restarts_full_recovery();
  check_healthy_reset_is_continuous_safe_and_saved_once();
  check_auto_promotion_continuity_and_failed_write();
  check_auto_seen_reader_usb_only_and_probe_is_not_health();
  check_millis_rollover_and_restart_requalifies_auto();
  check_pending_attach_keeps_service_hint_after_disable();
  check_stats_lease_gates_auto_not_physical_on_mode();
  check_lease_clears_on_load_master_off_and_disconnect();
  check_commit_time_progress_cancels_board_reset();
  check_app_outage_cannot_consume_physical_reboot_deadline();
  check_legacy_state_lazy_upgrade_and_backup_length();
  check_event_persistence_latest_only_and_clock_history();
  check_event_reasons_failclosed_and_manual_repair();
  check_bad_event_images_are_not_fresh_state();
  check_pending_cancellation_flush_ignores_logging_and_backend();
  check_event_cli_is_strict_and_bounded();
  check_event_snapshot_is_coherent_with_concurrent_readers();
#if defined(ENABLE_OTA)
  check_actual_update_activity_guard();
#endif
  puts("USB watchdog runtime and persistence passed");
}
