#include <helpers/BeaconConfig.h>
#include <helpers/IdentityStore.h>
#define private public
#include <helpers/BeaconManager.h>
#undef private
#include <Mesh.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>

uint64_t beacon_test_time = 0;
constexpr uint64_t H = mesh::BeaconManager::HourMillis;
using Fault = MemoryFS::Fault;
struct Fixture {
  MemoryFS fs;
  mesh::Mesh mesh;
  mesh::RTCClock clock;
  mesh::BeaconManager manager;
  char name[16] = "Namespace";
  char reply[256] = {};
  uint64_t now = 0;
  explicit Fixture(bool replacing) {
    fs.rename_replaces = replacing;
    g_mock_millis = 0; beacon_test_time = 0;
    manager.begin(&fs, &mesh, &clock, name);
  }
  void cmd(const char* command, bool success) {
    assert(manager.handle(command, reply, sizeof(reply)));
    assert((std::strncmp(reply, "Err", 3) != 0) == success);
  }
  void mc(const char* text) {
    const std::string command = std::string("set beacon 1 meshcore 906.5,62.5,7,5,14 ") + text;
    cmd(command.c_str(), true);
  }
  void mt() { cmd("set beacon 1 meshtext US LongFast 14 MT", true); }
  void tick(uint64_t delta) {
    now += delta; beacon_test_time = now; g_mock_millis += uint32_t(delta);
    manager.loop(g_mock_millis);
  }
  void reboot() {
    fs.clearFaults();
    manager.begin(&fs, &mesh, &clock, name);
  }
  void oldRam(bool existing) {
    assert(!existing ? (!manager.slots_ || !manager.slots_[0].enabled)
                    : manager.slots_ && !std::strcmp(manager.slots_[0].text, "Old"));
  }
};
const char* const SetNew = "set beacon 1 meshcore 907.5,125,8,6,10 New";
void healthy() {
  for (bool replacing : {false, true}) {
    Fixture f(replacing); f.mc("Old"); f.cmd(SetNew, true); f.reboot();
    f.cmd("get beacon 1", true); assert(std::strstr(f.reply, "New"));
    f.mt(); f.tick(2 * H); assert(f.mesh.sends.size() == 1);
  }
}
void assertHeld(Fixture& f, bool existing) {
  assert(f.manager.hold_);
  f.oldRam(existing);
  assert(!f.mesh.pending);
  const auto files = f.fs.files;
  const auto renames = f.fs.renames;
  const auto attempts = f.mesh.attempts;
  f.cmd("get beacon 1", false);
  f.cmd(SetNew, false);
  f.tick(48 * H);
  assert(f.fs.files == files && f.fs.renames == renames && f.mesh.attempts == attempts);
}
void config() {
  for (bool replacing : {false, true}) for (bool existing : {false, true}) {
    Fixture expected(replacing); expected.cmd(SetNew, true);
    const auto next = expected.fs.files.at("/beacons");
    for (Fault fault : {MemoryFS::None, MemoryFS::Reject, MemoryFS::Noop, MemoryFS::LateError}) {
      Fixture f(replacing);
      if (existing) f.mc("Old");
      const auto old = f.fs.files;
      f.fs.clearFaults(); f.fs.faults[existing ? 2 : 1] = fault;
      const bool committed = fault == MemoryFS::None || fault == MemoryFS::LateError;
      f.cmd(SetNew, committed);
      assert(!f.manager.hold_);
      if (committed) {
        assert(f.fs.files.at("/beacons") == next);
        assert(!f.fs.exists("/beacons.tmp") && !f.fs.exists("/beacons.bak"));
      } else {
        f.oldRam(existing);
        if (existing) assert(f.fs.files.at("/beacons") == old.at("/beacons"));
        else assert(!f.fs.exists("/beacons"));
      }
      f.reboot(); f.cmd("get beacon 1", true);
      assert(std::strstr(f.reply, committed ? "New" : existing ? "Old" : "off"));
      if (!committed) { f.cmd(SetNew, true); assert(f.fs.files.at("/beacons") == next); }
    }
  }
  // Both backup and publication moves are checked, not only the final rename.
  for (bool replacing : {false, true}) for (Fault fault : {MemoryFS::Reject, MemoryFS::Noop, MemoryFS::LateError}) {
    Fixture f(replacing); f.mc("Old");
    const auto old = f.fs.files.at("/beacons");
    f.fs.clearFaults(); f.fs.faults[1] = fault;
    f.cmd(SetNew, fault == MemoryFS::LateError);
    assert(!f.manager.hold_);
    if (fault != MemoryFS::LateError) {
      assert(f.fs.renames == 1 && f.fs.files.at("/beacons") == old);
      f.oldRam(true); f.cmd(SetNew, true);
    }
  }
  for (bool replacing : {false, true}) {
    Fixture f(replacing); f.mc("Old");
    const auto old = f.fs.files.at("/beacons");
    f.fs.files["/beacons.bak"] = old;
    f.fs.clearFaults(); f.fs.fail_remove = true;
    f.cmd(SetNew, false);
    assert(!f.manager.hold_ && !f.fs.renames);
    assert(f.fs.files.at("/beacons") == old && f.fs.files.at("/beacons.bak") == old);
    f.oldRam(true); f.fs.fail_remove = false; f.cmd(SetNew, true);
  }
}
void uncertainty() {
  for (bool replacing : {false, true}) for (bool existing : {false, true}) {
    for (unsigned stage : {1U, 2U}) {
      if (!existing && stage != 1) continue;
      for (Fault fault : {MemoryFS::Metadata, MemoryFS::BothNames, MemoryFS::NeitherName}) {
        for (unsigned probe : {1U, 2U}) {
          Fixture f(replacing); if (existing) { f.mc("Old"); f.tick(2 * H); }
          const auto old = f.fs.files;
          f.fs.clearFaults(); f.fs.faults[stage] = fault; f.fs.error_probe = probe;
          f.cmd(SetNew, false);
          assert(f.fs.renames == stage); // No speculative rollback or cleanup.
          assert(f.fs.exists("/beacons.tmp") || stage == (existing ? 2U : 1U));
          if (existing && stage == 2) assert(f.fs.files.at("/beacons.bak") == old.at("/beacons"));
          assertHeld(f, existing);
          if (fault == MemoryFS::Metadata) {
            f.reboot(); f.cmd("get beacon 1", true);
            assert(std::strstr(f.reply, existing && stage == 1 ? "Old" : "New"));
          }
        }
      }
    }
  }
}
void rollback() {
  for (bool replacing : {false, true}) {
    for (Fault fault : {MemoryFS::Reject, MemoryFS::Noop, MemoryFS::LateError,
                        MemoryFS::Metadata, MemoryFS::BothNames, MemoryFS::NeitherName}) {
      Fixture f(replacing); f.mc("Old"); f.tick(2 * H);
      const auto old = f.fs.files.at("/beacons");
      f.fs.clearFaults(); f.fs.faults[2] = MemoryFS::Reject; f.fs.faults[3] = fault;
      f.cmd(SetNew, false); assert(f.fs.renames == 3);
      if (fault == MemoryFS::LateError) {
        assert(!f.manager.hold_ && f.mesh.pending);
        assert(f.fs.files.at("/beacons") == old);
        f.oldRam(true); f.cmd(SetNew, true);
      } else {
        assert(f.fs.exists("/beacons.tmp"));
        if (fault != MemoryFS::Metadata && fault != MemoryFS::NeitherName)
          assert(f.fs.files.at("/beacons.bak") == old);
        assertHeld(f, true);
      }
    }
  }
}
void recovery() {
  for (bool replacing : {false, true}) for (bool corrupt_active : {false, true}) {
    for (Fault fault : {MemoryFS::None, MemoryFS::Reject, MemoryFS::Noop, MemoryFS::LateError,
                        MemoryFS::Metadata, MemoryFS::BothNames, MemoryFS::NeitherName}) {
      Fixture f(replacing); f.mc("Old");
      const auto old = f.fs.files.at("/beacons");
      f.fs.files["/beacons.bak"] = old; f.fs.files.erase("/beacons");
      if (corrupt_active) { f.fs.files["/beacons"] = old; f.fs.files["/beacons"][40] ^= 1; }
      f.fs.clearFaults(); f.fs.faults[1] = fault;
      f.manager.begin(&f.fs, &f.mesh, &f.clock, f.name);
      const bool recovered = fault == MemoryFS::None || fault == MemoryFS::LateError;
      f.cmd("get beacon 1", recovered);
      if (recovered) assert(std::strstr(f.reply, "Old") && f.fs.files.at("/beacons") == old);
      else {
        assert(f.manager.hold_ && f.fs.renames == 1);
        if (fault != MemoryFS::Metadata && fault != MemoryFS::NeitherName)
          assert(f.fs.files.at("/beacons.bak") == old);
      }
    }
  }
}
void ids() {
  for (bool replacing : {false, true}) {
    for (unsigned stage : {1U, 2U}) for (Fault fault : {MemoryFS::None, MemoryFS::Reject, MemoryFS::Noop,
         MemoryFS::LateError, MemoryFS::Metadata, MemoryFS::BothNames, MemoryFS::NeitherName}) {
      Fixture f(replacing); f.mt();
      const auto last = f.manager.last_id_;
      f.fs.clearFaults(); f.fs.faults[stage] = fault;
      f.tick(2 * H);
      const bool committed = fault == MemoryFS::None || fault == MemoryFS::LateError;
      assert(f.mesh.sends.size() == unsigned(committed));
      if (committed) {
        assert(!f.manager.ids_hold_ && f.manager.last_id_ == last + 1);
        assert(f.mesh.sends.back().id == last + 1);
      } else {
        assert(f.manager.ids_hold_ && f.manager.last_id_ == last);
        const auto files = f.fs.files;
        f.tick(24 * H); assert(f.mesh.sends.empty() && f.fs.files == files);
        // A held ID store does not quarantine independent MeshCore beacons.
        f.cmd("set beacon 2 meshcore 906.5,62.5,7,5,14 MC", true);
        assert(!f.manager.hold_);
      }
      if (committed || fault == MemoryFS::Reject || fault == MemoryFS::Noop
          || (fault == MemoryFS::Metadata && stage == 2)) {
        f.reboot();
        const auto count = f.mesh.sends.size(); f.tick(2 * H);
        assert(f.mesh.sends.size() == count + 1);
        assert(f.mesh.sends.back().id == last + (committed || (fault == MemoryFS::Metadata && stage == 2) ? 2 : 1));
      }
    }
    // Initial ID reservation has no public predecessor or rollback target.
    for (Fault fault : {MemoryFS::None, MemoryFS::Reject, MemoryFS::Noop, MemoryFS::LateError,
                        MemoryFS::Metadata, MemoryFS::BothNames, MemoryFS::NeitherName}) {
      Fixture f(replacing); f.fs.clearFaults(); f.fs.faults[1] = fault;
      const bool committed = fault == MemoryFS::None || fault == MemoryFS::LateError;
      f.cmd("set beacon 1 meshtext US LongFast 14 MT", committed);
      assert(f.manager.ids_hold_ != committed);
      f.tick(2 * H); assert(f.mesh.sends.size() == unsigned(committed));
    }
    for (Fault fault : {MemoryFS::Reject, MemoryFS::Noop, MemoryFS::LateError,
                        MemoryFS::Metadata, MemoryFS::BothNames, MemoryFS::NeitherName}) {
      Fixture f(replacing); f.mt();
      const auto last = f.manager.last_id_;
      const auto old = f.fs.files.at("/beacon_ids");
      f.fs.clearFaults(); f.fs.faults[2] = MemoryFS::Reject; f.fs.faults[3] = fault;
      f.tick(2 * H);
      assert(f.mesh.sends.empty() && f.manager.ids_hold_ && f.manager.last_id_ == last);
      assert(f.fs.renames == 3 && f.fs.exists("/beacon_ids.tmp"));
      if (fault == MemoryFS::Reject || fault == MemoryFS::Noop || fault == MemoryFS::BothNames)
        assert(f.fs.files.at("/beacon_ids.bak") == old);
      if (fault == MemoryFS::LateError || fault == MemoryFS::Metadata)
        assert(f.fs.files.at("/beacon_ids") == old);
      const auto files = f.fs.files;
      f.tick(24 * H); assert(f.mesh.sends.empty() && f.fs.files == files);
      f.reboot();
      const bool public_restored = fault == MemoryFS::LateError || fault == MemoryFS::Metadata
          || fault == MemoryFS::BothNames;
      assert(f.manager.ids_hold_ != public_restored);
      f.tick(2 * H);
      assert(f.mesh.sends.size() == unsigned(public_restored));
      if (public_restored) assert(f.mesh.sends.back().id == last + 1);
    }
  }
}
int main(int argc, char** argv) {
  assert(argc == 2);
  if (!std::strcmp(argv[1], "healthy")) healthy();
  else if (!std::strcmp(argv[1], "config")) config();
  else if (!std::strcmp(argv[1], "uncertainty")) uncertainty();
  else if (!std::strcmp(argv[1], "rollback")) rollback();
  else if (!std::strcmp(argv[1], "recovery")) recovery();
  else if (!std::strcmp(argv[1], "ids")) ids();
  else assert(false);
  std::printf("PASS: actual beacon namespace %s\n", argv[1]);
}
