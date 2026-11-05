#include <cassert>
#include <cstdint>
#include <helpers/radiolib/W12PramLabTrace.h>

int main() {
  mesh::w12pram::SpiTrace t;
  uint8_t status[] = {0x04, 0x24}; // CMD_OK, reset source 2, RX
  t.add(0x0208, status, sizeof status);
  assert(t.count == 0); // tracing is off
  t.active = true;
  t.add(0x0210, status, sizeof status);
  assert(t.count == 1 && t.first[0] == 0x0210 && t.modes[4] == 1);
  for (uint8_t mode = 0; mode <= 5; ++mode) {
    status[0] = 0x05; // CMD_OK with active IRQ
    status[1] = uint8_t(0x10 | mode);
    t.add(uint16_t(0x100 + mode), status, sizeof status);
    assert(t.modes[mode] == (mode == 4 ? 2 : 1));
  }
  status[0] = 0x08; // unsupported command status
  t.add(1, status, sizeof status);
  status[0] = 0x04; status[1] = 0x08; // reserved bit
  t.add(2, status, sizeof status);
  status[1] = 0x06; // reserved chip mode
  t.add(3, status, sizeof status);
  t.add(4, nullptr, 0);
  t.add(5, status, 1);
  assert(t.invalid_status == 5 && t.count == 12);
  t.modes[4] = UINT16_MAX; status[1] = 4;
  t.add(6, status, sizeof status);
  assert(t.modes[4] == UINT16_MAX); // saturate, don't wrap
  t.reset();
  assert(!t.active && !t.count && !t.invalid_status && !t.modes[4] && !t.first[0]);
}
