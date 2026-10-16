#include <helpers/BeaconManager.h>
#include <Mesh.h>
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>

uint64_t beacon_test_time = 0;
constexpr uint64_t H = mesh::BeaconManager::HourMillis;

struct Fixture {
  MemoryFS fs;
  mesh::Mesh mesh;
  mesh::RTCClock clock;
  mesh::BeaconManager manager;
  char reply[256] = {};
  char name[32] = "Beacon test";
  uint64_t now = 0;
  explicit Fixture(uint32_t boot = 0) {
    g_mock_millis = boot; beacon_test_time = 0;
    manager.begin(&fs, &mesh, &clock, name);
  }
  void tick(uint64_t elapsed) {
    now += elapsed; beacon_test_time = now; g_mock_millis += uint32_t(elapsed);
    manager.loop(g_mock_millis);
  }
  const char* cmd(const std::string& command, bool ok = true) {
    std::memset(reply, 0x55, sizeof(reply));
    const bool handled = manager.handle(command.c_str(), reply, sizeof(reply));
    assert(handled && std::memchr(reply, 0, sizeof(reply)));
    const bool actual_ok = std::strncmp(reply, "Err", 3) != 0;
    if (actual_ok != ok) { std::fprintf(stderr, "%s => %s\n", command.c_str(), reply); assert(false); }
    return reply;
  }
  void mc(unsigned slot, const char* text = "Hello") {
    cmd("set beacon " + std::to_string(slot) + " meshcore 906.5,62.5,7,5,14 " + text);
  }
  void all(unsigned hours) {
    cmd("set beacon.interval " + std::to_string(hours));
    for (unsigned i = 1; i <= 12; ++i) mc(i, std::to_string(i).c_str());
  }
  void mt(unsigned slot = 1) { cmd("set beacon " + std::to_string(slot) + " meshtext US LongFast 14 Hello MT"); }
};

uint32_t crc(const uint8_t* bytes, size_t size) {
  uint32_t value = 0xffffffffU;
  while (size--) {
    value ^= *bytes++;
    for (unsigned i = 0; i < 8; ++i) value = (value >> 1) ^ ((value & 1) ? 0xedb88320U : 0);
  }
  return value;
}
void put32(uint8_t* bytes, uint32_t value) {
  for (unsigned i = 0; i < 4; ++i) bytes[i] = uint8_t(value >> (8 * i));
}
void fixCrc(std::vector<uint8_t>& bytes) { put32(bytes.data() + bytes.size() - 4, crc(bytes.data(), bytes.size() - 4)); }

void structuralStorage() {
  Fixture mc; mc.mc(1, "Old");
  Fixture mt; mt.mt();
  for (unsigned fault = 0; fault < 15; ++fault) {
    Fixture f;
    f.fs.files = fault >= 12 ? mt.fs.files : mc.fs.files;
    auto& bytes = f.fs.files.at("/beacons");
    uint8_t* record = bytes.data() + 8;
    switch (fault) {
      case 0: record[10] = 255; break; // Invalid SF.
      case 1: record[11] = 4; break; // Invalid coding rate.
      case 2: {
        const float bw = 123;
        std::memcpy(record + 4, &bw, 4); break;
      }
      case 3: record[8] = 7; record[9] = 0; break;
      case 4: { // Valid tuple, but its long preamble overflows safe airtime.
        const float bw = 7.8f;
        std::memcpy(record + 4, &bw, 4);
        record[10] = 12; record[8] = 248; record[9] = 255; break;
      }
      case 5: put32(record, 0x7fc00000U); break; // NaN frequency.
      case 6: record[17] = 255; break; // Malformed UTF-8.
      case 7: record[17] = '\n'; break;
      case 8: record[21] = 'X'; break; // Hidden bytes after the terminator.
      case 9: record[16] = 0; break; // Disabled records must be all zero.
      case 10: record[12] = uint8_t(mesh::RadioProfileProtocol::Ethernet); break;
      case 11: record[15] = 8; break;
      case 12: record[14] = 255; break; // Invalid MT preset.
      case 13: record[8] = 32; record[9] = 0; break; // MT preamble is fixed.
      case 14: bytes[6] = 0; break; // MT requires durable ID initialization.
    }
    fixCrc(bytes);
    const auto original = f.fs.files;
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.cmd("get beacon", false);
    f.cmd("set beacon.interval 12", false);
    f.cmd("delete beacon all", false);
    f.tick(48 * H);
    assert(f.mesh.attempts == 0 && f.fs.files == original);
  }
  // Neither a future schema nor a read error may fall back to an older image.
  for (bool unreadable : {false, true}) {
    Fixture f;
    f.fs.files = mc.fs.files;
    f.fs.files["/beacons.bak"] = f.fs.files.at("/beacons");
    if (unreadable) f.fs.fail_read_open = true;
    else f.fs.files.at("/beacons")[4] = 2;
    const auto original = f.fs.files;
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.cmd("get beacon", false);
    f.tick(48 * H);
    assert(f.mesh.attempts == 0 && f.fs.files == original);
  }
}

void commands() {
  Fixture f;
  assert(!f.mesh.wake_enabled && f.fs.files.empty());
  f.tick(48 * H);
  assert(f.mesh.sends.empty() && f.fs.files.empty());
  assert(std::strstr(f.cmd("get beacon.interval"), "24"));
  assert(!mesh::BeaconManager::isCommand(nullptr));
  for (const char* command : {"set beacons 1", "get beaconry", "set beacon.intervalx 12", "get radio", "send beacon 1"})
    assert(!mesh::BeaconManager::isCommand(command));
  for (const char* command : {"get beacon", "get beacon 1", "set beacon", "set beacon.interval", "delete beacon all", "del beacon 1"})
    assert(mesh::BeaconManager::isCommand(command));
  for (const char* command : {
      "set beacon", "set beacon.interval", "get beacon.interval garbage", "set beacon.interval 0",
      "set beacon.interval 13", "set beacon.interval 4294967296", "set beacon.interval 12 garbage",
      "get beacon 0", "get beacon 13", "get beacon -1", "get beacon 4294967296", "get beacon 1 extra",
      "delete beacon 1 extra", "set beacon 1 unknown 1 Hello",
      "set beacon 1 meshcore nan,62.5,7,5,14 Hello", "set beacon 1 meshcore inf,62.5,7,5,14 Hello",
      "set beacon 1 meshcore 906.5,nan,7,5,14 Hello", "set beacon 1 meshcore 906.5,62.5,256,5,14 Hello",
      "set beacon 1 meshcore 906.5,62.5,7,5,128 Hello", "set beacon 1 meshcore 906.5,62.5,7,5,23 Hello",
      "set beacon 1 meshcore 906.5,62.5,7,5,14,7 Hello", "set beacon 1 meshcore 906.5,62.5,7,5,14,65536 Hello",
      "set beacon 1 meshcore 906.5,62.5,7,5,14,32,1 Hello", "set beacon 1 meshcore 906.5,62.5,7,5,14",
      "set beacon 1 meshtext bad LongFast 14 Hello", "set beacon 1 meshtext US bad 14 Hello",
      "set beacon 1 meshtext US LongFast 128 Hello"}) f.cmd(command, false);
  f.cmd("set beacon 1 meshcore 906.5,62.5,7,5,14 " + std::string(80, 'A'));
  assert(std::strstr(f.cmd("get beacon 1"), std::string(80, 'A').c_str()));
  f.cmd("set beacon 1 meshcore 906.5,62.5,7,5,14 " + std::string(81, 'A'), false);
  f.cmd("set beacon 1 meshcore 906.5,62.5,7,5,14 Hello\nthere", false);
  f.cmd("set beacon 1 meshcore 906.5,62.5,7,5,14 \xc3\xa9");
  assert(std::strstr(f.cmd("get beacon"), "1"));
  f.cmd("delete beacon all");
  assert(!f.mesh.wake_enabled);
  char tiny[2] = {'x', 'x'};
  assert(f.manager.handle("get beacon", tiny, sizeof(tiny)) && tiny[1] == 0);
  assert(!f.manager.handle("get beacon", tiny, 0));
}

void schedule() {
  for (unsigned cycle : {12U, 24U, 36U, 48U}) {
    for (bool wrap : {false, true}) {
      Fixture f(wrap ? UINT32_MAX - uint32_t(H) : 0);
      f.all(cycle);
      const uint64_t spacing = cycle / 12 * H;
      assert(f.mesh.wake_enabled && f.mesh.wake_delay == spacing);
      for (unsigned minute = 1; minute <= 48 * 60; ++minute) f.tick(60000);
      const unsigned expected = 48 / (cycle / 12);
      assert(f.mesh.sends.size() == expected);
      for (unsigned i = 0; i < expected; ++i) {
        const auto& send = f.mesh.sends[i];
        assert(send.time == (i + 1) * spacing);
        assert(std::string(send.slot.text) == std::to_string(i % 12 + 1));
        assert(send.slot.radio.preamble == 32 && send.slot.radio.protocol == mesh::RadioProfileProtocol::MeshCore);
        if (i) assert(send.time - f.mesh.sends[i - 1].time >= H);
        if (i >= 12) assert(send.time - f.mesh.sends[i - 12].time >= cycle * H);
      }
    }
  }
  // Sparse slots retain their hour positions; a long sleep never catches up
  // all missed slots in a burst.
  Fixture sparse;
  sparse.mc(1); sparse.mc(12);
  sparse.tick(13 * H);
  assert(sparse.mesh.sends.empty());
  assert(sparse.mesh.wake_delay == 11 * H);
  sparse.tick(11 * H);
  assert(sparse.mesh.sends.size() == 1);
  sparse.tick(24 * H);
  assert(sparse.mesh.sends.size() == 2);
}

void rolling() {
  Fixture f;
  f.all(12);
  f.tick(H + 59 * 60000);
  assert(f.mesh.sends.size() == 1);
  f.tick(60000);
  assert(f.mesh.sends.size() == 1 && f.mesh.wake_delay == 59 * 60000);
  for (unsigned minute = 1; minute <= 24 * 60; ++minute) f.tick(60000);
  for (unsigned i = 1; i < f.mesh.sends.size(); ++i) {
    assert(f.mesh.sends[i].time - f.mesh.sends[i - 1].time >= H);
    if (i >= 12) assert(f.mesh.sends[i].time - f.mesh.sends[i - 12].time >= 12 * H);
  }
  // Failed queue admission consumes this window and causes no fast retry.
  Fixture failed;
  failed.mc(1); failed.mesh.accept = false;
  failed.tick(2 * H);
  assert(failed.mesh.attempts == 1 && failed.mesh.sends.empty());
  for (unsigned i = 0; i < 100; ++i) failed.tick(1000);
  assert(failed.mesh.attempts == 1);
  failed.mesh.accept = true; failed.tick(24 * H - 100000);
  assert(failed.mesh.attempts == 2 && failed.mesh.sends.size() == 1);
  // Interval changes start a new anchor, rather than sending immediately.
  failed.cmd("set beacon.interval 12");
  failed.tick(H - 1); assert(failed.mesh.sends.size() == 1);
  failed.tick(1); assert(failed.mesh.sends.size() == 1); // per-slot rolling guard
  const unsigned cancellations = failed.mesh.cancellations;
  failed.manager.suspend();
  assert(!failed.mesh.wake_enabled && failed.mesh.cancellations == cancellations + 1);
  failed.tick(100 * H); assert(failed.mesh.sends.size() == 1);
  failed.cmd("set beacon 1 meshcore 906.5,62.5,7,5,14 Gone", false);
  // An object created by an early get must not transmit when a later set
  // enables a slot whose current window is already due, before the CLI ack.
  Fixture edit;
  edit.cmd("set beacon.interval 12"); edit.tick(H + 10 * 60000);
  edit.mc(1); assert(edit.mesh.sends.empty());
  edit.tick(50 * 60000); assert(edit.mesh.sends.empty());
  edit.tick(11 * H); assert(edit.mesh.sends.size() == 1);
}

void storage() {
  Fixture original;
  original.mc(1, "Old");
  const auto baseline = original.fs.files;
  const size_t size = baseline.at("/beacons").size();
  assert(size == 1188);
  for (size_t boundary = 0; boundary < size; ++boundary) {
    Fixture f; f.fs.files = baseline; f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.fs.fail_write_after = int(boundary);
    f.cmd("set beacon 1 meshcore 907.5,125,8,6,10 New", false);
    assert(std::strstr(f.cmd("get beacon 1"), "Old"));
    f.fs.fail_write_after = -1;
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    assert(std::strstr(f.cmd("get beacon 1"), "Old"));
  }
  for (int boundary : {0, 7, 8, 105, 1187}) {
    Fixture f; f.fs.files = baseline; f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.fs.fail_read_after = boundary;
    f.cmd("set beacon 1 meshcore 907.5,125,8,6,10 New", false);
    assert(std::strstr(f.cmd("get beacon 1"), "Old"));
  }
  for (int rename : {1, 2}) {
    Fixture f; f.fs.files = baseline; f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.fs.fail_rename = rename;
    f.cmd("set beacon 1 meshcore 907.5,125,8,6,10 New", false);
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    assert(std::strstr(f.cmd("get beacon 1"), "Old"));
  }
  {
    Fixture f; f.fs.files = baseline;
    f.fs.files["/beacons.tmp"] = baseline.at("/beacons");
    f.fs.files.erase("/beacons");
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    assert(!std::strstr(f.cmd("get beacon 1"), "Old")); // temp is not committed
  }
  for (bool future : {false, true}) {
    Fixture f; f.fs.files = baseline;
    f.fs.files["/beacons"][future ? 4 : 40] ^= future ? 3 : 1;
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.cmd("get beacon", false); f.cmd("get beacon.interval", false); f.cmd("delete beacon all", false);
    f.tick(48 * H); assert(f.mesh.sends.empty());
  }
  {
    Fixture f; f.fs.files = baseline;
    f.fs.files["/beacons.bak"] = baseline.at("/beacons");
    f.fs.files["/beacons"][40] ^= 1;
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    assert(std::strstr(f.cmd("get beacon 1"), "Old"));
  }
  {
    Fixture f; f.fs.files = baseline;
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.fs.fail_rename_from = {"/beacons.tmp", "/beacons.bak"};
    f.cmd("set beacon 1 meshcore 907.5,125,8,6,10 New", false);
    f.cmd("get beacon", false);
    f.tick(48 * H); assert(f.mesh.sends.empty());
  }
  {
    Fixture f; f.fs.files = baseline; f.fs.fail_read_open = true;
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.cmd("delete beacon all", false);
    assert(f.fs.files.at("/beacons") == baseline.at("/beacons"));
  }
  // CRC-valid but semantically invalid configuration is held as a unit.
  {
    Fixture f; f.fs.files = baseline;
    auto& bytes = f.fs.files["/beacons"];
    bytes[8 + 10] = 255; fixCrc(bytes);
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.cmd("get beacon", false);
  }
}

void ids() {
  Fixture f;
  f.mt();
  assert(f.fs.files.count("/beacon_ids"));
  f.tick(2 * H);
  assert(f.mesh.sends.size() == 1 && f.mesh.sends[0].id == 0x1234567a);
  assert(f.mesh.sends[0].from == 0x88776655);
  assert(f.mesh.sends[0].slot.radio.preamble == 16 && f.mesh.sends[0].slot.hops == 0);
  const auto baseline = f.fs.files;
  f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name); // reboots use the durable counter
  f.tick(2 * H);
  assert(f.mesh.sends.size() == 2 && f.mesh.sends.back().id == 0x1234567b);
  f.cmd("delete beacon all"); f.mt();
  f.tick(24 * H);
  assert(f.mesh.sends.back().id == 0x1234567c);
  for (unsigned fault = 0; fault < 4; ++fault) {
    Fixture bad; bad.fs.files = baseline;
    if (fault == 0) bad.fs.files.erase("/beacon_ids");
    if (fault == 1) bad.fs.files["/beacon_ids"][8] ^= 1;
    if (fault == 2) bad.fs.files["/beacon_ids"][4] = 2;
    if (fault == 3) {
      auto& bytes = bad.fs.files["/beacon_ids"];
      put32(bytes.data() + 8, UINT32_MAX); fixCrc(bytes);
    }
    bad.manager.begin(&bad.fs, &bad.mesh, &bad.clock, bad.name);
    bad.tick(2 * H); assert(bad.mesh.sends.empty());
    bad.cmd("delete beacon all");
    bad.cmd("set beacon 1 meshtext US LongFast 14 Hello MT", false);
    // A broken MT counter must not disable independent MeshCore slots.
    bad.mc(2); bad.tick(2 * H); assert(bad.mesh.sends.size() == 1);
  }
  // Every counter short-write boundary fails closed for this boot. Previous
  // durable state is unchanged, so the next boot cannot repeat an on-air ID.
  for (int boundary = 0; boundary < 16; ++boundary) {
    Fixture bad; bad.fs.files = baseline; bad.manager.begin(&bad.fs, &bad.mesh, &bad.clock, bad.name);
    bad.fs.fail_write_after = boundary;
    bad.tick(2 * H); assert(bad.mesh.sends.empty());
    bad.fs.fail_write_after = -1;
    bad.tick(24 * H); assert(bad.mesh.sends.empty());
    bad.manager.begin(&bad.fs, &bad.mesh, &bad.clock, bad.name);
    bad.tick(2 * H); assert(bad.mesh.sends.size() == 1 && bad.mesh.sends[0].id == 0x1234567b);
  }
  {
    Fixture rejected; rejected.mt(); rejected.mesh.accept = false;
    rejected.tick(2 * H); assert(rejected.mesh.attempts == 1);
    rejected.mesh.accept = true; rejected.tick(24 * H);
    assert(rejected.mesh.sends.size() == 1 && rejected.mesh.sends[0].id == 0x1234567b);
  }
}

void mutations() {
  for (const char* command : {
      "set beacon 1 meshcore 907.5,125,8,6,10 New",
      "set beacon 2 meshcore 907.5,125,8,6,10 Other",
      "delete beacon 1", "delete beacon all", "set beacon.interval 12"}) {
    for (bool rename_failure : {false, true}) {
      Fixture f;
      f.mc(1, "Old"); f.tick(2 * H);
      assert(f.mesh.pending && f.mesh.sends.size() == 1);
      const unsigned cancellations = f.mesh.cancellations;
      f.cmd("get beacon 1");
      assert(f.mesh.pending && f.mesh.cancellations == cancellations);
      if (rename_failure) f.fs.fail_rename = 2;
      else f.fs.fail_write = true;
      f.cmd(command, false);
      assert(f.mesh.pending && f.mesh.cancellations == cancellations);
      f.tick(1000);
      assert(f.mesh.pending && f.mesh.sends.size() == 1);
      f.fs.fail_write = false;
      f.cmd(command);
      assert(!f.mesh.pending && f.mesh.cancellations == cancellations + 1);
      f.tick(10000);
      assert(!f.mesh.pending && f.mesh.sends.size() == 1);
    }
  }
}

void staleBackup() {
  Fixture f;
  f.mt();
  const auto stale = f.fs.files.at("/beacon_ids");
  f.tick(2 * H);
  assert(f.mesh.sends.size() == 1 && f.mesh.sends[0].id == 0x1234567a);
  assert(stale != f.fs.files.at("/beacon_ids"));
  // A previous backup-remove failure can leave this lower ID behind. Losing
  // only the active directory entry must not make the on-air ID reusable.
  f.fs.files["/beacon_ids.bak"] = stale;
  f.fs.files.erase("/beacon_ids");
  f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
  f.tick(2 * H);
  assert(f.mesh.sends.size() == 1);
  assert(!f.fs.files.count("/beacon_ids") && f.fs.files.at("/beacon_ids.bak") == stale);
  f.cmd("delete beacon all");
  f.cmd("set beacon 1 meshtext US LongFast 14 Hello MT", false);
  f.mc(2); f.tick(2 * H);
  assert(f.mesh.sends.size() == 2 && f.mesh.sends.back().slot.radio.protocol == mesh::RadioProfileProtocol::MeshCore);

  // Absence of the config marker is not evidence that an independent backup
  // has never been used. A fresh MT setter must remain fail closed too.
  Fixture uninitialized;
  uninitialized.mc(2);
  assert(uninitialized.fs.files.at("/beacons")[6] == 0);
  uninitialized.fs.files["/beacon_ids.bak"] = stale;
  uninitialized.manager.begin(&uninitialized.fs, &uninitialized.mesh, &uninitialized.clock, uninitialized.name);
  uninitialized.cmd("set beacon 1 meshtext US LongFast 14 Hello MT", false);
  uninitialized.tick(4 * H);
  assert(uninitialized.mesh.sends.size() == 1
      && uninitialized.mesh.sends[0].slot.radio.protocol == mesh::RadioProfileProtocol::MeshCore);
  assert(!uninitialized.fs.files.count("/beacon_ids")
      && uninitialized.fs.files.at("/beacon_ids.bak") == stale);
}

void liveBackupRollback() {
  {
    Fixture f;
    f.mt();
    const auto stale = f.fs.files.at("/beacon_ids");
    f.tick(2 * H);
    assert(f.mesh.sends.size() == 1 && f.mesh.sends[0].id == 0x1234567a);
    // A failed backup cleanup can leave an older image. If the active entry
    // disappears during this boot, a later failed promotion must not turn
    // that unrelated image into authoritative metadata.
    f.fs.files["/beacon_ids.bak"] = stale;
    f.fs.files.erase("/beacon_ids");
    f.fs.fail_rename_from = {"/beacon_ids.tmp"};
    f.tick(24 * H);
    assert(f.mesh.sends.size() == 1);
    assert(!f.fs.files.count("/beacon_ids"));
    assert(f.fs.files.at("/beacon_ids.bak") == stale);
    f.fs.fail_rename_from.clear();
    f.tick(24 * H); // The failed counter write holds this boot too.
    assert(f.mesh.sends.size() == 1);
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    f.tick(2 * H);
    assert(f.mesh.sends.size() == 1); // Reboot must not reuse 0x1234567a.
    f.cmd("set beacon 1 meshtext US LongFast 14 Still held", false);
    f.mc(2);
    f.tick(2 * H);
    assert(f.mesh.sends.size() == 2
        && f.mesh.sends.back().slot.radio.protocol == mesh::RadioProfileProtocol::MeshCore);
  }
  {
    Fixture f;
    f.mc(1, "Old");
    const auto stale = f.fs.files.at("/beacons");
    f.mc(1, "Current");
    f.fs.files["/beacons.bak"] = stale;
    f.fs.files.erase("/beacons");
    f.fs.fail_rename_from = {"/beacons.tmp"};
    f.cmd("set beacon 1 meshcore 907.5,125,8,6,10 New", false);
    assert(!f.fs.files.count("/beacons"));
    assert(f.fs.files.at("/beacons.bak") == stale);
    assert(std::strstr(f.cmd("get beacon 1"), "Current"));
    // Configuration can still commit a complete replacement on a later
    // successful command, removing the stale backup after that commit.
    f.fs.fail_rename_from.clear();
    f.cmd("set beacon 1 meshcore 907.5,125,8,6,10 New");
    assert(!f.fs.files.count("/beacons.bak"));
    f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
    assert(std::strstr(f.cmd("get beacon 1"), "New"));
  }
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "commands")) commands();
  else if (!std::strcmp(argv[1], "schedule")) schedule();
  else if (!std::strcmp(argv[1], "rolling")) rolling();
  else if (!std::strcmp(argv[1], "storage")) storage();
  else if (!std::strcmp(argv[1], "structural_storage")) structuralStorage();
  else if (!std::strcmp(argv[1], "ids")) ids();
  else if (!std::strcmp(argv[1], "mutations")) mutations();
  else if (!std::strcmp(argv[1], "stale_backup")) staleBackup();
  else if (!std::strcmp(argv[1], "live_backup")) liveBackupRollback();
  else assert(false);
  std::printf("beacon manager %s passed\n", argv[1]);
}
