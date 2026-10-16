#include "ManagementReporter.h"
#include "CommonCLI.h"
#include "PersistentStoreFormat.h"
#include <cassert>
#include <cstdio>
#include <memory>
#include <string>
using namespace mesh;
using namespace mesh::management;
namespace mesh {
UsbLoggingStatus test_usb_status;
unsigned usb_status_calls = 0;
UsbLoggingStatus usbLoggingStatus() { ++usb_status_calls; return test_usb_status; }
}
struct Fixture {
  MemoryFS fs;
  Mesh mesh;
  MainBoard board;
  SensorManager sensors;
  ClientACL acl;
  NodePrefs prefs;
  CommonCLI cli;
  CommonCLICallbacks callbacks;
  std::unique_ptr<ManagementReporter> reporter;
  Fixture() { test_millis = 0; test_usb_status = UsbLoggingStatus(); usb_status_calls = 0; reboot(); }
  void reboot() { reporter.reset(); test_millis = 0; reporter.reset(new ManagementReporter(mesh, board, sensors, acl, prefs, callbacks, cli, &fs)); }
  std::string cmd(const char* text, bool ok = true) {
    char b[200], reply[160] = {}; strcpy(b, text);
    assert(reporter->command(b, reply, sizeof(reply)));
    if (!strncmp(text, "set ", 4) && ((!strncmp(reply, "OK", 2)) != ok)) {
      fprintf(stderr, "%s: %s\n", text, reply); assert(false);
    }
    if (!strncmp(text, "set mgmt.password ", 18)) assert(b[18] == 0);
    return reply;
  }
  void advance(uint32_t seconds) {
    while (seconds) { const uint32_t step = seconds < 60 ? seconds : 60; test_millis += step * 1000;
      reporter->loop(); seconds -= step;
    }
  }
  void configure(bool direct = true) {
    cmd("set mgmt.enabled on", false);
    cmd("set mgmt.password short", false);
    cmd("set mgmt.password management test password");
    if (direct) cmd("set mgmt.interval 5");
    cmd("set mgmt.enabled on");
    // Simulate a route becoming unavailable after enable so fallback-only
    // tests exercise the scoped 21-day safety report.
    if (!direct) cli.path_len = 0xff;
    const auto& file = fs.files["/management"];
    assert(std::string(file.begin(), file.end()).find("management test password") == std::string::npos);
  }
  std::vector<uint8_t> installLegacyRoute() {
    auto legacy = fs.files["/management"];
    memcpy(legacy.data(), "MGC1", 4); legacy[7] = 1; legacy[8] = 0x12;
    write32(legacy.data() + 116, storage::updateCRC32(0xffffffff, legacy.data(), 116));
    fs.files["/management"] = legacy;
    return legacy;
  }
};
static void versionSnapshot(const char* version, bool valid_version, uint32_t packed = 0) {
  Fixture f;
  f.callbacks.firmware_version = version;
  f.configure(); f.advance(5 * DAY + 60);
  assert(f.mesh.packets.size() == 1);
  const auto* header = f.mesh.packets[0].payload;
  assert(!memcmp(header, "MGR2", 4));
  const bool reported_valid = (read16(header + 76) & FIRMWARE) != 0;
  const uint32_t reported_version = read32(header + 28);
  if (reported_valid != valid_version || reported_version != packed)
    fprintf(stderr, "version %s: expected valid=%u packed=%08x; got valid=%u packed=%08x\n",
        version ? version : "(null)", unsigned(valid_version), unsigned(packed),
        unsigned(reported_valid), unsigned(reported_version));
  assert(reported_valid == valid_version && reported_version == packed);
}

static void versionSnapshots() {
  for (const char* version : {"1.2.3", "v1.2.3", "v001.002.003",
      "v1.2.3-release", "v1.2.3+build_42", "v1.2.3-rc.1+build_42"})
    versionSnapshot(version, true, 0x01020300);
  for (const char* version : {"1.2.3.4", "v1.2.3.004", "v1.2.3.4-preview1",
      "v1.2.3.4+build_42", "v1.2.3.4-label_with_underscores"})
    versionSnapshot(version, true, 0x01020304);
  versionSnapshot("v1.18.0", true, 0x01120000); // Direct-build example default.
  versionSnapshot("v1.17.1.9-halo-keymind-cascade-dev-5f4d242f", true, 0x01110109);
  versionSnapshot("v1.17.1.9-halo-keymind-cascade-dev.42-observer-dev-5f4d242f", true, 0x01110109);
  versionSnapshot("v1.18.0.42-5f4d242f", true, 0x0112002a); // Three-part base + build number.
  versionSnapshot("255.255.255.255", true, UINT32_MAX);
  versionSnapshot("0.0.0", true);
  versionSnapshot("v0.0.0.0+build_0", true);
  for (const char* version : {"", "v", "1", "1.2", "1..3", "1.2.", "1.2.3.",
      "1.2.3.4.5", "1.2.3.xyz", "1.2.3garbage", "1.2.3.4garbage", "1.2.3 arbitrary text",
      "1.2.3-", "1.2.3+", "1.2.3.4-", "1.2.3.4+", "release-v1.2.3",
      " 1.2.3", "1. 2.3", "1.2.3 ", "1.2.3. 4", "+1.2.3", "1.+2.3", "1.2.-3",
      "1.2.3.+4", "1.2.3.-4", "256.0.0", "0.256.0", "0.0.256", "0.0.0.256",
      "v4294967297.2.3", "v1.2.3.4294967297", "v-4294967295.2.3", "dev-5f4d242f"})
    versionSnapshot(version, false);
  const std::string huge_component(4096, '9');
  versionSnapshot(("v" + huge_component + ".2.3").c_str(), false);
  versionSnapshot(("v1.2.3." + huge_component).c_str(), false);
  versionSnapshot(nullptr, false);
}

int main(int argc, char** argv) {
  if (argc == 2 && !strcmp(argv[1], "versions")) {
    versionSnapshots();
    puts("PASS production MGR2 firmware version snapshots");
    return 0;
  }
  if (argc == 2 && !strcmp(argv[1], "version-malformed")) {
    versionSnapshot("v1.2.3garbage", false);
    return 0;
  }
  assert(argc == 1);
  {
    Fixture f;
    assert(f.cmd("get mgmt").find("direct=5d") != std::string::npos);
    assert(f.cmd("get mgmt").find("flood=21d") != std::string::npos);
    f.advance(100 * DAY); assert(f.mesh.packets.empty());
    f.configure(); f.cmd("set mgmt.interval 91", false);
    f.advance(5 * DAY - 60); assert(f.mesh.packets.empty());
    // Changes to RTC in either direction have no effect on flood eligibility.
    f.mesh.rtc.time = 0; f.advance(60); assert(f.mesh.packets.size() == 1 && !f.mesh.packets[0].flood);
    f.mesh.rtc.time = 0xffffffff; f.reboot();
    f.advance(5 * DAY + 3600); assert(f.mesh.packets.size() == 2);
    for (const auto& p : f.mesh.packets) assert(!p.flood);
  }
  {
    Fixture f; f.configure(); f.advance(21 * DAY + 601);
    assert(f.mesh.packets.size() == 5 && !f.mesh.packets[0].flood && f.mesh.packets.back().flood);
    const uint32_t seq = read32(f.mesh.packets.back().payload + 20); f.reboot(); f.advance(600);
    assert(f.mesh.packets.size() == 5);
    f.cmd("set mgmt.enabled off"); f.cmd("set mgmt.enabled on"); f.advance(600);
    assert(f.mesh.packets.size() == 5 && read32(f.mesh.packets.back().payload + 20) == seq);
  }
  {
    Fixture f; f.configure(); f.cmd("set mgmt.flood off");
    f.advance(21 * DAY + 3600); assert(f.mesh.packets.size() == 4);
    for (const auto& p : f.mesh.packets) assert(!p.flood);
  }
  {
    Fixture f; f.configure(); f.cmd("set mgmt.direct off");
    f.advance(21 * DAY + 60); assert(f.mesh.packets.size() == 1);
    assert(f.mesh.packets[0].flood);
  }
  {
    Fixture f; f.configure(); f.cmd("set mgmt.direct 10");
    f.cmd("set mgmt.flood 30"); f.advance(30 * DAY + 60);
    assert(f.mesh.packets.size() == 3 && f.mesh.packets.back().flood);
    assert(!f.mesh.packets[0].flood && !f.mesh.packets[1].flood);
  }
  {
    Fixture f; f.configure(false); f.advance(21 * DAY - 60); f.fs.fail_write = true; f.advance(120);
    assert(f.mesh.packets.empty()); assert(f.cmd("get mgmt").find("FAULT") != std::string::npos);
    // Last hourly checkpoint was one hour before the failed reservation.
    f.fs.fail_write = false; f.reboot(); f.advance(3600); assert(f.mesh.packets.size() == 1);
  }
  {
    Fixture f; f.configure(false); f.fs.files["/management"][72] ^= 1; f.reboot(); f.advance(100 * DAY);
    assert(f.mesh.packets.empty()); assert(f.cmd("get mgmt").find("FAULT") != std::string::npos);
  }
  {
    Fixture f; f.configure(false); f.fs.fail_read_open = true; f.reboot(); f.advance(100 * DAY);
    assert(f.mesh.packets.empty());
  }
  {
    Fixture f;
    for (unsigned i = 0; i < 36; ++i) { ClientInfo c; c.id.pub_key[0] = i; f.acl.clients.push_back(c); }
    test_usb_status.supported = true; test_usb_status.watchdog_enabled = true;
    test_usb_status.host_connected = test_usb_status.reader_connected = true;
    test_usb_status.stalled = true; test_usb_status.retry_seconds = 7200;
    test_usb_status.inactive_seconds = 3600; test_usb_status.backoff_step = 1;
    test_usb_status.last_event.reasons = UsbLoggingWatchdogEvent::TX_STALLED;
    test_usb_status.last_event.action = UsbLoggingWatchdogEvent::REENUMERATE;
    test_usb_status.last_event.sequence = 17; test_usb_status.last_event.uptime_seconds = 300;
    test_usb_status.last_event.persisted = true;
    f.configure(false); f.advance(21 * DAY + 600); assert(f.mesh.packets.size() == 9);
    assert(usb_status_calls == 1);
    for (unsigned i = 0; i < 9; ++i) {
      const auto& p = f.mesh.packets[i]; assert(validPage(p.payload, p.payload_len));
      assert(p.payload[78] == i && p.payload[80] == 36 && p.flood);
      assert(!memcmp(p.payload, "MGR2", 4) && read16(p.payload + HEADER) == 61);
      assert(!(read16(p.payload + HEADER) & 1024)); // an open reader is not an active logger
      assert(read32(p.payload + HEADER + 3) == 7200);
      assert(read32(p.payload + HEADER + 7) == 3600);
      UsbLoggingWatchdogEvent event;
      assert(decodeUsbWatchdogEvent(p.payload + WATCHDOG_EVENT_OFFSET, event));
      assert(event.reasons == UsbLoggingWatchdogEvent::TX_STALLED && event.action == UsbLoggingWatchdogEvent::REENUMERATE);
      assert(event.sequence == 17 && event.uptime_seconds == 300 && event.persisted && event.epoch == 0);
      assert(floodSize(p.payload_len) <= 179);
    }
  }
  {
    Fixture f;
    for (unsigned i = 0; i < 7; ++i) { ClientInfo c; c.id.pub_key[0] = i; f.acl.clients.push_back(c); }
    f.configure(false); f.advance(21 * DAY); assert(f.mesh.packets.size() == 1);
    assert(!memcmp(f.mesh.packets[0].payload, "MGR2", 4));
    assert(read16(f.mesh.packets[0].payload + HEADER) == 0); // unsupported is explicit
    test_usb_status.supported = true; test_usb_status.retry_seconds = 999;
    test_usb_status.watchdog_auto = true; test_usb_status.auto_connected_seconds = 100;
    test_usb_status.host_connected = test_usb_status.reader_connected = test_usb_status.logger_active = true;
    test_usb_status.last_event.reasons = UsbLoggingWatchdogEvent::CLIENT_INACTIVE;
    test_usb_status.last_event.action = UsbLoggingWatchdogEvent::SOFT_RECOVERY;
    test_usb_status.last_event.sequence = 1; test_usb_status.last_event.epoch = 1700000001;
    f.board.voltage = 2900; f.board.temperature = 60; f.advance(60);
    assert(f.mesh.packets.size() == 2);
    assert(read16(f.mesh.packets[1].payload + HEADER) == 0); // frozen report snapshot
    assert(read32(f.mesh.packets[1].payload + HEADER + 11) == 0);
    assert(read32(f.mesh.packets[1].payload + WATCHDOG_EVENT_OFFSET + 9) == 0);
    assert(usb_status_calls == 1);
    // The low reading was not in the first snapshot and must survive into the next one.
    f.board.voltage = 3740; f.board.temperature = 24; f.advance(21 * DAY + 3600);
    assert(f.mesh.packets.size() == 4);
    assert(read16(f.mesh.packets.back().payload + 66) == 2900);
    assert(f.mesh.packets.back().payload[69] == temperature(60));
    assert(read16(f.mesh.packets.back().payload + HEADER) == 1561);
    assert(read32(f.mesh.packets.back().payload + HEADER + 11) == 100);
    assert(read32(f.mesh.packets.back().payload + WATCHDOG_EVENT_OFFSET + 9) == 1);
    assert(usb_status_calls == 2);
  }
  {
    Fixture f; f.configure(false); f.mesh.temp = true; f.advance(22 * DAY); assert(f.mesh.packets.empty());
    f.mesh.temp = false; f.mesh.budget = 0; f.advance(60); assert(f.mesh.packets.empty());
    f.mesh.budget = 1000; f.advance(60); assert(f.mesh.packets.size() == 1);
  }
  {
    Fixture f; f.configure(false); f.cmd("set mgmt.flood 90"); f.advance(89 * DAY); assert(f.mesh.packets.empty());
    f.advance(DAY); assert(f.mesh.packets.size() == 1);
  }
  {
    Fixture f; f.configure();
    const auto saved = f.fs.files;
    const std::string state = f.cmd("get mgmt");
    for (const char* field : {"direct", "flood", "interval"}) {
      for (const char* value : {"", "-", "-5", "-4294967291", "-4294967275", "+21",
                               " 21", "21 ", "21x", "4294967301", "4294967317",
                               "18446744073709551637", "999999999999999999999999"}) {
        const std::string command = std::string("set mgmt.") + field + " " + value;
        f.cmd(command.c_str(), false);
        assert(f.cmd("get mgmt") == state && f.fs.files == saved);
      }
    }
    f.cmd("set mgmt.direct 0005"); f.cmd("set mgmt.flood 0021"); f.cmd("set mgmt.interval 0005");
    assert(f.cmd("get mgmt").find("direct=5d") != std::string::npos);
    assert(f.cmd("get mgmt").find("flood=21d") != std::string::npos);
    f.cmd("set mgmt.direct 90"); f.cmd("set mgmt.flood 90");
    f.cmd("set mgmt.direct 4", false); f.cmd("set mgmt.flood 20", false);
    f.cmd("set mgmt.interval 91", false);
  }
  {
    Fixture f;
    for (unsigned i = 0; i < 36; ++i) { ClientInfo c; c.id.pub_key[0] = i; f.acl.clients.push_back(c); }
    f.configure(false); f.advance(21 * DAY); assert(f.mesh.packets.size() == 1);
    const auto saved = f.fs.files;
    const std::string state = f.cmd("get mgmt");
    for (const char* command : {"set mgmt.unknown value", "set mgmt.enabled maybe",
                               "set mgmt.direct 4", "set mgmt.flood 20", "set mgmt.interval 91",
                               "set mgmt.password short", "set mgmt.direct 5", "set mgmt.enabled on"}) {
      f.cmd(command, false);
      assert(f.cmd("get mgmt") == state && f.fs.files == saved);
    }
    f.cli.scope_available = false;
    const std::string unscoped = f.cmd("get mgmt");
    f.cmd("set mgmt.flood 21", false);
    assert(f.cmd("get mgmt") == unscoped && f.fs.files == saved);
    f.cli.scope_available = true;
    f.advance(60); assert(f.mesh.packets.size() == 2);
    f.advance(7 * 60); assert(f.mesh.packets.size() == 9 && usb_status_calls == 1);
    for (unsigned page = 0; page < 9; ++page) assert(f.mesh.packets[page].payload[78] == page);
  }
  for (bool fail_save : {false, true}) {
    Fixture f;
    for (unsigned i = 0; i < 8; ++i) { ClientInfo c; c.id.pub_key[0] = i; f.acl.clients.push_back(c); }
    f.configure(false); f.advance(21 * DAY); assert(f.mesh.packets.size() == 1);
    const auto saved = f.fs.files;
    f.fs.fail_write = fail_save;
    f.cmd("set mgmt.flood 30", !fail_save);
    f.advance(3600); assert(f.mesh.packets.size() == 1);
    assert((f.cmd("get mgmt").find("FAULT") != std::string::npos) == fail_save);
    if (fail_save) assert(f.fs.files == saved);
    else assert(f.cmd("get mgmt").find("flood=30d") != std::string::npos);
  }
  {
    Fixture f; f.configure(false);
    f.fs.files["/management"][72] ^= 1; f.reboot();
    assert(f.cmd("get mgmt").find("FAULT") != std::string::npos);
    const auto saved = f.fs.files;
    f.cmd("set mgmt.password another valid secret", false);
    f.cmd("set mgmt.password short", false);
    assert(f.fs.files == saved);
  }
  for (bool migration_succeeds : {false, true}) {
    Fixture f; f.configure();
    const auto legacy = f.installLegacyRoute();
    f.cli.adoption_succeeds = migration_succeeds;
    f.reboot(); f.advance(3600);
    assert(f.cli.adoption_calls == 1 && f.mesh.packets.empty());
    if (!migration_succeeds) {
      // The route write alone failed, while management writes still work.
      // Do not let an hourly MGC2 rewrite destroy the only saved legacy path.
      assert(f.fs.files["/management"] == legacy);
      const std::string fault = f.cmd("get mgmt");
      assert(fault.find("FAULT") != std::string::npos);
      assert(fault.find("> off key=unset") != std::string::npos);
      f.cli.adoption_succeeds = true;
      f.reboot(); f.advance(3600);
      assert(f.cli.adoption_calls == 2);
    }
    const uint8_t* path = nullptr; uint8_t path_len = OUT_PATH_UNKNOWN;
    assert(f.cli.getDataTxPath(path, path_len) && path_len == 1 && path[0] == 0x12);
    assert(!memcmp(f.fs.files["/management"].data(), "MGC2", 4));
    assert(f.cmd("get mgmt").find("FAULT") == std::string::npos);
    const auto calls = f.cli.adoption_calls;
    f.reboot(); assert(f.cli.adoption_calls == calls); // no repeated migration
  }
  {
    Fixture f; f.configure(); f.installLegacyRoute();
    const uint8_t explicit_path[] = {0x34};
    assert(f.cli.adoptLegacyDataTxPath(explicit_path, 1)); // already saved /data_tx
    f.cli.adoption_succeeds = false; // no additional route write should be needed
    f.reboot(); f.advance(3600);
    const uint8_t* path = nullptr; uint8_t path_len = OUT_PATH_UNKNOWN;
    assert(f.cli.getDataTxPath(path, path_len) && path_len == 1 && path[0] == 0x34);
    assert(f.cmd("get mgmt").find("FAULT") == std::string::npos);
    assert(!memcmp(f.fs.files["/management"].data(), "MGC2", 4));
  }
}
