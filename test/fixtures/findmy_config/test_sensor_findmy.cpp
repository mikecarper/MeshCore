#include <cassert>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#include "helpers/findmy/SensorFindMy.h"

struct FakeFS;
struct FakeFile {
  FakeFS* fs = nullptr;
  std::string path;
  bool writable = false;
  FakeFile() = default;
  FakeFile(FakeFS* owner, const char* name, bool can_write)
      : fs(owner), path(name), writable(can_write) {}
  explicit operator bool() const { return fs != nullptr; }
  bool isDirectory() const { return false; }
  size_t size() const;
  size_t read(uint8_t* out, size_t n);
  size_t write(const uint8_t* data, size_t n);
  void close() {}
  void flush() {}
};
struct FakeFS {
  std::map<std::string, std::vector<uint8_t>> files;
  bool fail_write = false, fail_rename = false;
  bool exists(const char* path) const { return files.count(path) != 0; }
  FakeFile open(const char* path, const char* mode = "r") {
    if (mode[0] == 'w') { files[path].clear(); return {this, path, true}; }
    return exists(path) ? FakeFile{this, path, false} : FakeFile{};
  }
  bool remove(const char* path) { return files.erase(path) != 0; }
  bool rename(const char* from, const char* to) {
    if (fail_rename || !exists(from)) return false;
    files[to] = files[from]; files.erase(from); return true;
  }
};
size_t FakeFile::size() const { return fs->files[path].size(); }
size_t FakeFile::read(uint8_t* out, size_t n) {
  const auto& bytes = fs->files[path];
  n = n < bytes.size() ? n : bytes.size();
  memcpy(out, bytes.data(), n); return n;
}
size_t FakeFile::write(const uint8_t* data, size_t n) {
  if (!writable) return 0;
  if (fs->fail_write) n /= 2;
  fs->files[path] = std::vector<uint8_t>(data, data + n); return n;
}

struct FakeBackend {
  static unsigned starts;
  static unsigned stops;
  static bool fail_start;
  static bool fail_stop;
  static bool retain_failed_start;
  static uint16_t interval;
  static uint8_t address[6], payload[29];
  bool active = false;
  bool owned = false;
  bool start(const uint8_t addr[6], const uint8_t data[29], uint16_t interval_ms) {
    assert(!owned);  // A previous failed start must be fully cleaned up.
    ++starts;
    interval = interval_ms;
    memcpy(address, addr, 6);
    memcpy(payload, data, 29);
    active = !fail_start;
    owned = active || retain_failed_start;
    return active;
  }
  bool stop() {
    if (owned) ++stops;
    if (owned && fail_stop) return false;
    active = false;
    owned = false;
    return true;
  }
  bool isActive() const { return active; }
};
unsigned FakeBackend::starts = 0;
unsigned FakeBackend::stops = 0;
bool FakeBackend::fail_start = false;
bool FakeBackend::fail_stop = false;
bool FakeBackend::retain_failed_start = false;
uint16_t FakeBackend::interval = 0;
uint8_t FakeBackend::address[6] = {};
uint8_t FakeBackend::payload[29] = {};

int main() {
  using mesh::findmy::SensorFindMy;
  const char* encoded = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHA==";
  const std::string set_key = std::string("set findmy.key ") + encoded;
  char reply[160] = {};
  FakeFS fs;
  SensorFindMy<FakeFS, FakeBackend> sensor;
  sensor.begin(&fs);
  sensor.service(false, 0);
  assert(FakeBackend::starts == 0);
  assert(sensor.handleCommand("get findmy", reply, sizeof(reply), 0));
  assert(strstr(reply, "findmy=off") && strstr(reply, "key=unset") && strstr(reply, "beacon=inactive"));
  assert(!sensor.handleCommand("get unrelated", reply, sizeof(reply), 0));
  assert(sensor.handleCommand("set findmy on", reply, sizeof(reply), 0));
  assert(strstr(reply, "ERR") && FakeBackend::starts == 0);
  assert(sensor.handleCommand(set_key.c_str(), reply, sizeof(reply), 10));
  assert(strstr(reply, "findmy off") && FakeBackend::starts == 0);
  assert(sensor.handleCommand("set findmy on", reply, sizeof(reply), 20));
  assert(strstr(reply, "active") && FakeBackend::starts == 1);
  assert(FakeBackend::interval == 10000);
  assert(FakeBackend::address[0] == 0xc1 && FakeBackend::address[5] == 0x06);
  assert(FakeBackend::payload[0] == 0x4c && FakeBackend::payload[1] == 0x00);
  assert(FakeBackend::payload[5] == 0x07 && FakeBackend::payload[26] == 0x1c);
  assert(sensor.handleCommand("get findmy", reply, sizeof(reply), 20));
  assert(strstr(reply, "findmy=on") && strstr(reply, "beacon=active") && !strstr(reply, encoded));
  sensor.service(false, 100);
  assert(FakeBackend::starts == 1);

  // The faster beacon is only requested after the tracker reports escalation.
  sensor.service(false, 101, true);
  assert(FakeBackend::starts == 2 && FakeBackend::interval == 1280);
  sensor.service(false, 102, true);
  assert(FakeBackend::starts == 2);
  sensor.service(false, 103, false);
  assert(FakeBackend::starts == 3 && FakeBackend::interval == 10000);
  // Restore the counters expected by the remaining independent lifecycle cases.
  FakeBackend::starts = 1;
  FakeBackend::stops = 0;

  sensor.service(true, 200);
  assert(FakeBackend::stops == 1);
  assert(sensor.handleCommand("get findmy", reply, sizeof(reply), 200));
  assert(strstr(reply, "beacon=suspended"));
  sensor.service(true, 300);
  assert(FakeBackend::starts == 1 && FakeBackend::stops == 1);
  sensor.service(false, 400);
  assert(FakeBackend::starts == 2);

  // A failed save shuts off the active beacon and prohibits retry until the
  // stored state can be checked on a fresh controller after reboot.
  fs.fail_write = true;
  assert(sensor.handleCommand("set findmy off", reply, sizeof(reply), 500));
  assert(strstr(reply, "save failed") && FakeBackend::stops == 2);
  sensor.service(false, 120000);
  assert(FakeBackend::starts == 2);
  assert(sensor.handleCommand("get findmy", reply, sizeof(reply), 120000));
  assert(strstr(reply, "storage=error") && strstr(reply, "beacon=inactive") && !strstr(reply, encoded));
  fs.fail_write = false;

  // The failed change never published; boot loads the last durable enabled
  // setting, but does not start Bluetooth until service() runs.
  SensorFindMy<FakeFS, FakeBackend> rebooted;
  rebooted.begin(&fs);
  assert(FakeBackend::starts == 2);
  assert(rebooted.handleCommand("get findmy", reply, sizeof(reply), 0));
  assert(strstr(reply, "findmy=on") && strstr(reply, "beacon=inactive"));
  FakeBackend::fail_start = true;
  rebooted.service(false, 1000);
  assert(FakeBackend::starts == 3);
  rebooted.service(false, 60999);
  assert(FakeBackend::starts == 3);
  rebooted.service(false, 61000);
  assert(FakeBackend::starts == 4);
  FakeBackend::fail_start = false;
  rebooted.service(false, 120999);
  assert(FakeBackend::starts == 4);
  rebooted.service(false, 121000);
  assert(FakeBackend::starts == 5);

  // Suspending an unsuccessfully started controller clears the throttle so
  // the beacon can start promptly once the power policy resumes it.
  rebooted.service(true, 122000);
  FakeBackend::fail_start = true;
  rebooted.service(false, 122001);
  assert(FakeBackend::starts == 6);
  rebooted.service(true, 122002);
  FakeBackend::fail_start = false;
  rebooted.service(false, 122003);
  assert(FakeBackend::starts == 7);
  assert(rebooted.handleCommand("set findmy off", reply, sizeof(reply), 123000));
  assert(strstr(reply, "findmy off"));
  SensorFindMy<FakeFS, FakeBackend> off_after_reboot;
  off_after_reboot.begin(&fs);
  off_after_reboot.service(false, 0);
  assert(FakeBackend::starts == 7);
  assert(off_after_reboot.handleCommand("get findmy", reply, sizeof(reply), 0));
  assert(strstr(reply, "findmy=off") && strstr(reply, "key=set") && !strstr(reply, encoded));

  // A platform that cannot stop advertising must report failure rather than
  // claiming it is stopped. If a key was replaced, it must not report the
  // old active advertisement as the newly saved key.
  FakeFS stop_fs;
  SensorFindMy<FakeFS, FakeBackend> stop_sensor;
  stop_sensor.begin(&stop_fs);
  assert(stop_sensor.handleCommand(set_key.c_str(), reply, sizeof(reply), 0));
  assert(stop_sensor.handleCommand("set findmy on", reply, sizeof(reply), 1));
  assert(strstr(reply, "active"));
  FakeBackend::fail_stop = true;
  const char* other_key = "AgIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHA==";
  const std::string set_other_key = std::string("set findmy.key ") + other_key;
  assert(stop_sensor.handleCommand(set_other_key.c_str(), reply, sizeof(reply), 2));
  assert(strstr(reply, "ERR") && !strstr(reply, "beacon=active"));
  assert(stop_sensor.handleCommand("get findmy", reply, sizeof(reply), 3));
  assert(!strstr(reply, encoded) && !strstr(reply, other_key));
  const unsigned starts_before_recovery = FakeBackend::starts;
  FakeBackend::fail_stop = false;
  stop_sensor.service(false, 4);
  assert(FakeBackend::starts == starts_before_recovery + 1);
  assert(FakeBackend::address[0] == 0xc2);  // The newly saved key, not the old beacon.

  FakeFS failed_stop_fs;
  SensorFindMy<FakeFS, FakeBackend> failed_stop_sensor;
  failed_stop_sensor.begin(&failed_stop_fs);
  assert(failed_stop_sensor.handleCommand(set_key.c_str(), reply, sizeof(reply), 0));
  assert(failed_stop_sensor.handleCommand("set findmy on", reply, sizeof(reply), 1));
  assert(strstr(reply, "active"));
  FakeBackend::fail_stop = true;
  failed_stop_fs.fail_write = true;
  assert(failed_stop_sensor.handleCommand("set findmy off", reply, sizeof(reply), 2));
  assert(strstr(reply, "ERR") && !strstr(reply, "beacon stopped"));
  assert(failed_stop_sensor.handleCommand("get findmy", reply, sizeof(reply), 3));
  assert(strstr(reply, "beacon=stop-error") && strstr(reply, "storage=error"));
  FakeBackend::fail_stop = false;
  failed_stop_sensor.service(false, 4);
  assert(failed_stop_sensor.handleCommand("get findmy", reply, sizeof(reply), 5));
  assert(strstr(reply, "beacon=inactive") && strstr(reply, "storage=error"));

  FakeFS corrupt_fs;
  corrupt_fs.files["/findmy.0"] = {1, 2, 3};
  SensorFindMy<FakeFS, FakeBackend> corrupt;
  corrupt.begin(&corrupt_fs);
  const unsigned starts_before_corrupt = FakeBackend::starts;
  corrupt.service(false, 1);
  assert(FakeBackend::starts == starts_before_corrupt);
  assert(corrupt.handleCommand("get findmy", reply, sizeof(reply), 2));
  assert(strstr(reply, "storage=error") && strstr(reply, "beacon=inactive"));

  // A backend can fail startup and also fail to tear down its own controller.
  // Such a failure is different from an ordinary retryable inactive beacon:
  // cleanup has to be retried before another start is attempted.
  FakeFS failed_start_fs;
  SensorFindMy<FakeFS, FakeBackend> failed_start_sensor;
  failed_start_sensor.begin(&failed_start_fs);
  assert(failed_start_sensor.handleCommand(set_key.c_str(), reply, sizeof(reply), 0));
  FakeBackend::fail_start = true;
  FakeBackend::fail_stop = true;
  FakeBackend::retain_failed_start = true;
  const unsigned starts_before_failed_start = FakeBackend::starts;
  const unsigned stops_before_failed_start = FakeBackend::stops;
  assert(failed_start_sensor.handleCommand("set findmy on", reply, sizeof(reply), 10));
  assert(FakeBackend::starts == starts_before_failed_start + 1);
  assert(FakeBackend::stops == stops_before_failed_start + 1);
  assert(failed_start_sensor.handleCommand("get findmy", reply, sizeof(reply), 11));
  assert(strstr(reply, "beacon=stop-error"));
  failed_start_sensor.service(false, 61011);
  assert(FakeBackend::starts == starts_before_failed_start + 1);
  assert(FakeBackend::stops == stops_before_failed_start + 2);
  FakeBackend::fail_start = false;
  FakeBackend::fail_stop = false;
  FakeBackend::retain_failed_start = false;
  failed_start_sensor.service(false, 61012);
  assert(FakeBackend::starts == starts_before_failed_start + 2);
  assert(failed_start_sensor.handleCommand("get findmy", reply, sizeof(reply), 61012));
  assert(strstr(reply, "beacon=active"));

  // Formatting must stop the live advertisement before erasing configuration
  // and leave no stale in-memory key to restart afterwards.
  FakeFS erase_fs;
  SensorFindMy<FakeFS, FakeBackend> erase_sensor;
  erase_sensor.begin(&erase_fs);
  assert(erase_sensor.handleCommand(set_key.c_str(), reply, sizeof(reply), 0));
  assert(erase_sensor.handleCommand("set findmy on", reply, sizeof(reply), 1));
  assert(strstr(reply, "beacon=active"));
  const unsigned starts_before_erase = FakeBackend::starts;
  assert(erase_sensor.prepareErase());
  erase_fs.files.clear();  // The enclosing filesystem format succeeded.
  erase_sensor.finishErase(true);
  erase_sensor.service(false, 2);
  assert(FakeBackend::starts == starts_before_erase);
  assert(erase_sensor.handleCommand("get findmy", reply, sizeof(reply), 3));
  assert(strstr(reply, "findmy=off") && strstr(reply, "key=unset"));
  assert(strstr(reply, "beacon=inactive") && !strstr(reply, "storage=error"));
  SensorFindMy<FakeFS, FakeBackend> erased_reboot;
  erased_reboot.begin(&erase_fs);
  erased_reboot.service(false, 4);
  assert(FakeBackend::starts == starts_before_erase);

  // If hardware cannot stop, format must be aborted, and successful cleanup
  // later must not silently turn the prior beacon back on.
  FakeFS erase_stop_fs;
  SensorFindMy<FakeFS, FakeBackend> erase_stop_sensor;
  erase_stop_sensor.begin(&erase_stop_fs);
  assert(erase_stop_sensor.handleCommand(set_key.c_str(), reply, sizeof(reply), 0));
  assert(erase_stop_sensor.handleCommand("set findmy on", reply, sizeof(reply), 1));
  const unsigned starts_before_stop_fault = FakeBackend::starts;
  FakeBackend::fail_stop = true;
  assert(!erase_stop_sensor.prepareErase());
  assert(erase_stop_sensor.handleCommand("get findmy", reply, sizeof(reply), 2));
  assert(strstr(reply, "beacon=stop-error"));
  assert(!erase_stop_fs.files.empty()); // Caller must leave storage alone.
  FakeBackend::fail_stop = false;
  erase_stop_sensor.service(false, 3);
  assert(FakeBackend::starts == starts_before_stop_fault);
  assert(erase_stop_sensor.handleCommand("get findmy", reply, sizeof(reply), 4));
  assert(strstr(reply, "storage=error") && strstr(reply, "beacon=inactive"));

  // A failed format may have partially destroyed storage; do not resume
  // advertising or accept new settings until a clean boot inspects it.
  FakeFS erase_failed_fs;
  SensorFindMy<FakeFS, FakeBackend> erase_failed_sensor;
  erase_failed_sensor.begin(&erase_failed_fs);
  assert(erase_failed_sensor.handleCommand(set_key.c_str(), reply, sizeof(reply), 0));
  assert(erase_failed_sensor.handleCommand("set findmy on", reply, sizeof(reply), 1));
  const unsigned starts_before_failed_format = FakeBackend::starts;
  assert(erase_failed_sensor.prepareErase());
  erase_failed_sensor.finishErase(false);
  erase_failed_sensor.service(false, 2);
  assert(FakeBackend::starts == starts_before_failed_format);
  assert(erase_failed_sensor.handleCommand("get findmy", reply, sizeof(reply), 3));
  assert(strstr(reply, "storage=error") && strstr(reply, "beacon=inactive"));
  assert(erase_failed_sensor.handleCommand("set findmy on", reply, sizeof(reply), 4));
  assert(strstr(reply, "ERR") && FakeBackend::starts == starts_before_failed_format);
}
