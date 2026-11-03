#include <cassert>
#include <cstdint>
#include <helpers/tracker/TagModeButton.h>

using mesh::tracker::TagModeButton;

// A short click, including both debounce boundaries. Return its release event.
static bool click(TagModeButton& b, uint32_t down, uint32_t up) {
  assert(!b.update(true, down));
  assert(b.needsPolling());
  assert(!b.update(true, down + TagModeButton::kDebounceMs));
  assert(!b.update(false, up));
  return b.update(false, up + TagModeButton::kDebounceMs);
}

int main() {
  TagModeButton b;
  b.begin(false, 0);
  assert(!b.needsPolling());
  // Bounces do not count as presses or start a gesture.
  assert(!b.update(true, 10) && b.needsPolling());
  assert(!b.update(false, 20) && !b.needsPolling());
  assert(!click(b, 30, 100));
  assert(b.needsPolling()); // Waiting for the rest of the sequence.
  assert(!click(b, 200, 270));
  assert(click(b, 370, 440));
  assert(!b.needsPolling());
  assert(!b.update(false, 1000));

  // Expired partial sequences cannot combine with later presses.
  assert(!click(b, 1100, 1170));
  assert(b.needsPolling());
  assert(!b.update(false, 1696)); // More than 500 ms from release.
  assert(!b.needsPolling());
  assert(!click(b, 1800, 1870));
  assert(!click(b, 2000, 2070));
  assert(click(b, 2200, 2270));

  // A continuous long hold, even after two clicks, can never fire a mode
  // change and cancels the partial gesture.
  TagModeButton held;
  held.begin(false, 0);
  assert(!click(held, 10, 80));
  assert(!click(held, 160, 230));
  assert(!held.update(true, 300));
  assert(!held.update(true, 325));
  assert(!held.update(true, 5000));
  assert(!held.update(false, 5010));
  assert(!held.update(false, 5035));
  assert(!held.needsPolling());
  assert(!click(held, 5100, 5170));
  assert(!click(held, 5250, 5320));
  assert(click(held, 5400, 5470));

  // A hold exactly at the long boundary must not count as the first click
  // even when followed by two fast clicks.
  TagModeButton boundary;
  boundary.begin(false, 0);
  assert(!boundary.update(true, 10));
  assert(!boundary.update(true, 35));
  assert(!boundary.update(false, 710));
  assert(!boundary.update(false, 735));
  assert(!click(boundary, 760, 830));
  assert(!click(boundary, 900, 970));
  assert(click(boundary, 1040, 1110));

  // Already held at boot/state-losing wake: its release never counts.
  TagModeButton boot;
  boot.begin(true, 0);
  assert(boot.needsPolling());
  assert(!boot.update(true, 10000));
  assert(!boot.update(false, 10001));
  assert(!boot.update(false, 10026) && !boot.needsPolling());
  assert(!click(boot, 10050, 10120));
  assert(!click(boot, 10200, 10270));
  assert(click(boot, 10350, 10420));

  // The complete sequence can span millis() rollover.
  TagModeButton wrap;
  const uint32_t origin = UINT32_MAX - 300;
  wrap.begin(false, origin);
  assert(!click(wrap, origin + 10, origin + 80));
  assert(!click(wrap, origin + 150, origin + 220));
  assert(click(wrap, origin + 290, origin + 360));
  assert(!wrap.needsPolling());
}
