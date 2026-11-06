#include <cassert>
#include <cstdint>
#include <helpers/esp32/BrowserOtaSession.h>

using namespace mesh::ota;
class Lock final : public BrowserOtaLock {
public:
  void lock() override { assert(!held); held = true; }
  void unlock() override { assert(held); held = false; }
  bool held = false;
};

int main() {
  Lock state, io;
  BrowserOtaStream stream(io);
  BrowserOtaSession session(state, stream);
  uint8_t secret[16] = {1}, other[16] = {2};
  BrowserOtaAuthority auth;
  auth.lan = auth.same_origin = auth.explicit_login = true;
  auth.session = secret; auth.session_size = sizeof(secret); auth.credential_generation = 3;
  BrowserOtaAuthority bad = auth; bad.session = other;
  assert(!session.claim(0, auth));
  assert(!session.armFromUsb(false, 10, 1000));
  assert(session.armFromUsb(true, 10, 1000));
  const uint32_t epoch = session.claim(20, auth);
  assert(epoch && !session.claim(21, auth));
  assert(!session.armFromUsb(true, 21, 1000));
  assert(!session.peerAuthorized(21, bad, epoch));
  assert(!session.peerAuthorized(21, auth, epoch + 1));
  assert(session.peerAuthorized(21, auth, epoch));
  const uint8_t request[] = {'M', 'S', 1, 1};
  assert(stream.write(request, sizeof(request)) == sizeof(request));
  uint8_t output[11] = {};
  uint32_t sequence = 0; size_t length = 123;
  assert(!session.request(22, bad, epoch, sequence, output, sizeof(output), length) && !length);
  assert(session.request(22, auth, epoch, sequence, output, sizeof(output), length));
  const uint8_t response[] = {'m', 's', 1, 0, 1, 'm'^'s'};
  assert(!session.response(23, bad, epoch, sequence, response, sizeof(response)));
  assert(!session.response(23, auth, epoch, sequence + 1, response, sizeof(response)));
  assert(session.response(23, auth, epoch, sequence, response, sizeof(response)));
  assert(!session.response(23, auth, epoch, sequence, response, sizeof(response)));
  assert(stream.available() == sizeof(response));
  assert(!session.requiresDetach(1019));
  assert(session.requiresDetach(1020));
  assert(!session.peerAuthorized(1020, auth, epoch));
  assert(!session.armFromUsb(true, 1020, 1000));
  assert(!session.closedAfterDetach(epoch + 1));
  assert(session.closedAfterDetach(epoch));
  assert(!session.claim(1021, auth));
  assert(session.armFromUsb(true, 1021, 1000));
  const uint32_t next = session.claim(1022, auth);
  assert(next && next != epoch);
  assert(!session.closedAfterDetach(epoch));
  assert(session.revoke(1023, auth, next));
  assert(session.requiresDetach(1024));
  assert(session.closedAfterDetach(next));
  assert(session.armFromUsb(true, 1025, 1000));
  const uint32_t third = session.claim(1026, auth);
  assert(third);
  session.revokeFromOwner();
  assert(session.requiresDetach(1027));
  assert(session.closedAfterDetach(third));
  assert(!state.held && !io.held);
}
