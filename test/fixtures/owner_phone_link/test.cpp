#include <cassert>
#include <cstdint>
#include <helpers/tracker/OwnerPhoneLink.h>

using mesh::tracker::OwnerPhoneLink;
using State = OwnerPhoneLink::State;
using Policy = OwnerPhoneLink::ReconnectPolicy;

int main() {
  OwnerPhoneLink link;
  uint32_t delay = 123;
  assert(link.state(0) == State::Disabled);
  link.authenticatedConnected();  // Disabled cannot become near or lost.
  link.disconnected(10);
  assert(!link.nextDelay(10, delay) && !link.isLost(1000000));

  link.setEnabled(true);
  assert(link.state(0) == State::Unknown);
  link.disconnected(50);          // A raw/unknown peer is not the owner.
  assert(link.state(1000000) == State::Unknown);
  assert(!link.nextDelay(1000000, delay));

  link.authenticatedConnected();
  assert(link.state(100) == State::Near);
  link.disconnected(1000);
  assert(link.state(1000) == State::Grace);
  assert(link.nextDelay(1000, delay) && delay == 90000);
  link.disconnected(50000);       // Duplicate disconnect cannot postpone loss.
  assert(link.nextDelay(90999, delay) && delay == 1);
  assert(link.state(90999) == State::Grace && !link.isLost(90999));
  assert(link.nextDelay(91000, delay) && delay == 0);
  assert(link.state(91000) == State::Lost && link.isLost(91000));
  assert(!link.nextDelay(91000, delay));
  link.authenticatedConnected();  // Only the phone cause clears.
  assert(link.state(91001) == State::Near && !link.isLost(91001));

  // Reconnection during grace cancels the pending deadline.
  link.disconnected(92000);
  link.authenticatedConnected();
  assert(!link.nextDelay(200000, delay));
  assert(link.state(200000) == State::Near && !link.isLost(200000));

  // Profile disable and re-enable start Unknown, with no hidden deadline.
  link.disconnected(200001);
  link.setEnabled(false);
  assert(link.state(500000) == State::Disabled && !link.isLost(500000));
  link.setEnabled(true);
  assert(link.state(500000) == State::Unknown && !link.nextDelay(500000, delay));
  link.disconnected(500001);
  assert(link.state(700000) == State::Unknown);
  link.authenticatedConnected();
  link.reset();
  assert(link.state(700001) == State::Unknown);

  // The monotonic deadline remains correct across uint32_t rollover.
  const uint32_t start = UINT32_MAX - 40000;
  link.authenticatedConnected();
  link.disconnected(start);
  assert(link.nextDelay(start + 89999u, delay) && delay == 1);
  assert(link.state(start + 89999u) == State::Grace);
  assert(link.nextDelay(start + 90000u, delay) && delay == 0);
  assert(link.state(start + 90000u) == State::Lost);
  link.authenticatedConnected();
  assert(!link.isLost(start + 90001u));

  // Optional policy: reconnect proves Near but retains this lost cause until
  // explicit reset. It never changes the caller's independent alert causes.
  OwnerPhoneLink latched(Policy::LatchLost);
  latched.setEnabled(true);
  latched.authenticatedConnected();
  latched.disconnected(100);
  assert(latched.isLost(90100));
  latched.authenticatedConnected();
  assert(latched.state(90101) == State::Near && latched.isLost(90101));
  latched.disconnected(90102);
  assert(latched.state(90102) == State::Lost);
  assert(!latched.nextDelay(90102, delay));
  latched.reset();
  assert(latched.state(90103) == State::Unknown && !latched.isLost(90103));
}
