#include "MyMesh.h"

#if MESH_ENABLE_TX_BEACONS
#include <helpers/BeaconManager.h>
#include <new>

void MyMesh::beginBeacons() {
  auto* fs = _store->getPrimaryFS();
  if (_beacons || !fs || (!fs->exists("/beacons") && !fs->exists("/beacons.bak"))) return;
  _beacons = new (std::nothrow) mesh::BeaconManager;
  if (_beacons) _beacons->begin(fs, this, getRTCClock(), _prefs.node_name);
}

void MyMesh::loopBeacons() { if (_beacons) _beacons->loop(millis()); }

void MyMesh::suspendBeacons() {
  if (_beacons) _beacons->suspend();
  cancelBeaconRadioFrame();
}

bool MyMesh::handleBeaconCommand(const char* command, char* reply, size_t capacity) {
  if (!mesh::BeaconManager::isCommand(command)) return false;
  if (!_beacons) {
    _beacons = new (std::nothrow) mesh::BeaconManager;
    if (_beacons) _beacons->begin(_store->getPrimaryFS(), this, getRTCClock(), _prefs.node_name);
  }
  if (!_beacons) { snprintf(reply, capacity, "Error: beacon allocation failed"); return true; }
  return _beacons->handle(command, reply, capacity);
}
#endif
