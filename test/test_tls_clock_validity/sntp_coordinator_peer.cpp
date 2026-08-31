#include <helpers/esp32/SntpOperationCoordinator.h>

namespace {

mesh::sntp_coord::OperationLease peer_lease(
    mesh::sntp_coord::processWideCoordinator());

}  // namespace

extern "C" void* sntpCoordinatorPeerAddress() {
  return &mesh::sntp_coord::processWideCoordinator();
}

extern "C" bool sntpCoordinatorPeerAcquire() {
  return peer_lease.tryAcquire();
}

extern "C" bool sntpCoordinatorPeerRelease() {
  return peer_lease.release();
}
