#include <cassert>
#include <cstdint>
#include <cstring>
#include <helpers/esp32/BrowserOtaStream.h>

using mesh::ota::BrowserOtaLock;
using mesh::ota::BrowserOtaStream;

class CheckedLock final : public BrowserOtaLock {
public:
  void lock() override { assert(!held); held = true; ++acquires; }
  void unlock() override { assert(held); held = false; }
  bool held = false;
  unsigned acquires = 0;
};

int main() {
  CheckedLock lock;
  BrowserOtaStream stream(lock);
  uint8_t data[] = {'M', 'S', 1, 1}, received[11] = {};
  uint8_t response[] = {'m', 's', 1, 0, 1, 0};
  for (size_t i = 0; i < sizeof(response) - 1; ++i) response[5] ^= response[i];
  uint32_t sequence = 0; size_t length = 0;
  assert(!stream.write(data, sizeof(data)));
  assert(stream.available() == 0);
  assert(stream.peek() == -1);
  const uint32_t epoch = stream.open();
  assert(epoch);
  assert(!stream.open());
  assert(stream.write(data, sizeof(data)) == sizeof(data));
  assert(stream.browserRequest(epoch, sequence, received, sizeof(received), length));
  assert(length == sizeof(data));
  assert(!memcmp(received, data, sizeof(data)));
  assert(stream.browserResponse(epoch, sequence, response, sizeof(response)));
  assert(stream.available() == sizeof(response));
  assert(stream.peek() == 'm' && stream.peek() == 'm');
  for (uint8_t value : response) assert(stream.read() == value);
  assert(stream.read() == -1 && stream.available() == 0);
  stream.close(epoch);
  assert(!stream.browserResponse(epoch, sequence, response, sizeof(response)));
  assert(stream.write(data, 1) == 0);
  const uint32_t next = stream.open();
  assert(next && next != epoch);
  stream.close(epoch);
  assert(stream.write(data, sizeof(data)) == sizeof(data));
  assert(stream.browserRequest(next, sequence, received, sizeof(received), length));
  assert(stream.browserResponse(next, sequence, response, sizeof(response)));
  assert(stream.read() == 'm');
  stream.close(next);
  assert(!lock.held && lock.acquires > 0);
}
