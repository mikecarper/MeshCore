#include <cassert>
#include <cstdint>
#include <cstring>

#include "helpers/tracker/Lis2dhMotionLatch.h"

using mesh::tracker::motion::Lis2dhMotionLatch;
using mesh::tracker::motion::Observation;

struct FakeBus {
  uint8_t regs[128] = {};
  uint8_t actual = 0x19;
  bool powered = true;
  bool fail_reads = false;
  bool fail_writes = false;
  int fail_write_at = -1;
  int fail_read_reg = -1;
  unsigned reads = 0, writes = 0, source_reads = 0;
  FakeBus() { reset(); }
  void reset() { memset(regs, 0, sizeof(regs)); regs[0x0f] = 0x33; }
  bool read(uint8_t addr, uint8_t reg, uint8_t& value) {
    ++reads;
    if (!powered || fail_reads || int(reg) == fail_read_reg
        || addr != actual || reg >= sizeof(regs)) return false;
    value = regs[reg];
    if (reg == 0x31) { ++source_reads; regs[reg] = 0; }
    return true;
  }
  bool write(uint8_t addr, uint8_t reg, uint8_t value) {
    ++writes;
    if (!powered || fail_writes || int(writes) == fail_write_at
        || addr != actual || reg >= sizeof(regs)) return false;
    regs[reg] = value;
    return true;
  }
};

int main() {
  FakeBus bus;
  Lis2dhMotionLatch<FakeBus> sensor;
  assert(sensor.poll(bus, 0) == Observation::Unknown);
  assert(!sensor.begin(bus, 0, 0));
  assert(!sensor.begin(bus, 0, 128));
  assert(bus.reads == 0 && bus.writes == 0);

  assert(sensor.begin(bus, 1000));
  assert(sensor.isReady() && sensor.address() == 0x19);
  assert(bus.regs[0x20] == 0x2f); // 10 Hz, low power, all three axes.
  assert(bus.regs[0x21] == 0x09); // HP filter enabled on motion path.
  assert(bus.regs[0x22] == 0x00); // No assumptions about INT1 pin wiring.
  assert(bus.regs[0x23] == 0x00 && bus.regs[0x24] == 0x08);
  assert(bus.regs[0x30] == 0x2a && bus.regs[0x32] == 16 && bus.regs[0x33] == 0);
  const unsigned after_begin = bus.source_reads;
  assert(sensor.begin(bus, 1001)); // Idempotent; does not clear a latch.
  assert(bus.source_reads == after_begin);
  bus.regs[0x31] = 0x40;
  assert(sensor.poll(bus, 1199) == Observation::Unknown);
  assert(bus.source_reads == after_begin);
  assert(sensor.poll(bus, 1200) == Observation::Motion);
  assert(sensor.poll(bus, 1201) == Observation::NoMotionObserved);
  bus.regs[0x31] = 0x41;
  assert(sensor.poll(bus, 31000) == Observation::Motion);

  // After rail cycling the WHO_AM_I remains valid but the configuration is
  // reset. Never classify that default state as a quiet animal.
  bus.reset();
  assert(sensor.poll(bus, 31001) == Observation::Unknown);
  assert(!sensor.isReady());
  assert(sensor.poll(bus, 31002) == Observation::Unknown);
  assert(sensor.begin(bus, 31003, 8));
  assert(bus.regs[0x32] == 8);
  assert(sensor.poll(bus, 31203) == Observation::NoMotionObserved);

  // An I2C outage invalidates readiness, even if no motion bit is readable.
  bus.fail_reads = true;
  assert(sensor.poll(bus, 32000) == Observation::Unknown);
  assert(!sensor.isReady());
  bus.fail_reads = false;
  assert(sensor.poll(bus, 32001) == Observation::Unknown);
  assert(sensor.begin(bus, 32002));
  // Even if configuration checks pass, failing the latch read is unknown.
  bus.fail_read_reg = 0x31;
  assert(sensor.poll(bus, 32202) == Observation::Unknown);
  assert(!sensor.isReady());
  bus.fail_read_reg = -1;
  assert(sensor.begin(bus, 32203));
  bus.powered = false;
  assert(sensor.poll(bus, 33000) == Observation::Unknown);
  // A failed shutdown retains ownership; retry after the bus recovers.
  assert(!sensor.stop(bus));
  bus.powered = true;
  assert(sensor.stop(bus));
  assert(bus.regs[0x20] == 0 && bus.regs[0x30] == 0);

  // A partial configuration failure must not be exposed as quiet.
  FakeBus partial;
  Lis2dhMotionLatch<FakeBus> broken;
  partial.fail_write_at = 4;
  assert(!broken.begin(partial, 0));
  assert(!broken.isReady());
  assert(broken.poll(partial, 1000) == Observation::Unknown);
  assert(partial.regs[0x20] == 0); // Best-effort power-down completed.
  partial.fail_write_at = -1;
  assert(broken.begin(partial, 100));
  assert(broken.poll(partial, 300) == Observation::NoMotionObserved);

  // Unknown chip IDs are left untouched; both legal I2C addresses work.
  FakeBus bad_id;
  bad_id.regs[0x0f] = 0x42;
  Lis2dhMotionLatch<FakeBus> absent;
  assert(!absent.begin(bad_id, 0));
  assert(bad_id.writes == 0 && absent.poll(bad_id, 1000) == Observation::Unknown);
  FakeBus alternate;
  alternate.actual = 0x18;
  Lis2dhMotionLatch<FakeBus> at18;
  assert(at18.begin(alternate, UINT32_MAX - 99u));
  assert(at18.address() == 0x18);
  assert(at18.poll(alternate, 99) == Observation::Unknown);
  assert(at18.poll(alternate, 100) == Observation::NoMotionObserved);
}
