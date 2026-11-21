#include <zephyr/drivers/i2c.h>
#include <zephyr/kernel.h>
#include <helpers/tracker/Lis2dhMotionLatch.h>

namespace {
const device* const bus = DEVICE_DT_GET(DT_NODELABEL(i2c0));

struct TagI2c {
  bool read(uint8_t address, uint8_t reg, uint8_t& value) {
    return i2c_reg_read_byte(bus, address, reg, &value) == 0;
  }
  bool write(uint8_t address, uint8_t reg, uint8_t value) {
    return i2c_reg_write_byte(bus, address, reg, value) == 0;
  }
};

TagI2c i2c;
mesh::tracker::motion::Lis2dhMotionLatch<TagI2c> latch;
}

extern "C" bool tag_motion_present(void) {
  uint8_t id;
  if (!device_is_ready(bus)) return false;
  return (i2c.read(0x19, 0x0f, id) && id == 0x33)
      || (i2c.read(0x18, 0x0f, id) && id == 0x33);
}

extern "C" void tag_motion_start(void) {
  if (device_is_ready(bus)) latch.begin(i2c, k_uptime_get_32(), 4);
}

extern "C" bool tag_motion_detected(void) {
  using mesh::tracker::motion::Observation;
  auto result = latch.poll(i2c, k_uptime_get_32());
  if (result == Observation::Unknown) tag_motion_start();
  // An unreadable sensor must not suppress a separated-accessory alert.
  return result != Observation::NoMotionObserved;
}

extern "C" void tag_motion_stop(void) {
  latch.stop(i2c);
}
