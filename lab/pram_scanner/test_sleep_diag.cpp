#include <cassert>
#include <cstring>
#include "helpers/radiolib/W12PramSleepDiag.h"

int main() {
  using namespace mesh::w12pram;
  SleepDiag d;
  d.record(false, WakeKind::Gpio, true);
  assert(d.gpio == 0 && d.gpio_radio_high == 0);
  d.record(true, WakeKind::Gpio, true);
  d.record(true, WakeKind::Gpio, false);
  d.record(true, WakeKind::Timer, true);
  d.record(true, WakeKind::Other, true);
  assert(d.gpio == 2 && d.timer == 1 && d.other == 1 && d.gpio_radio_high == 1);
  char reply[128];
  d.format(reply, sizeof(reply));
  assert(strcmp(reply, "> gpio=2 timer=1 other=1 radio_high=1 (RAM only)") == 0);
  d.gpio = d.gpio_radio_high = UINT32_MAX;
  d.record(true, WakeKind::Gpio, true);
  assert(d.gpio == UINT32_MAX && d.gpio_radio_high == UINT32_MAX);
}
