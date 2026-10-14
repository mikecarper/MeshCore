#include "CommonCLI.h"

#if MESH_ENABLE_TX_BEACONS
#include "BeaconManager.h"
#include <new>

void CommonCLI::beginBeacons() {
  if (!_management_fs || !_management_mesh || _beacons) return;
  if (!_management_fs->exists("/beacons") && !_management_fs->exists("/beacons.bak")) return;
  _beacons = new (std::nothrow) mesh::BeaconManager;
  if (_beacons) _beacons->begin(_management_fs, _management_mesh, _rtc, _prefs->node_name);
}

void CommonCLI::loopBeacons() {
  if (_beacons) _beacons->loop(millis());
}

void CommonCLI::suspendBeacons() {
  if (_beacons) _beacons->suspend();
  if (_management_mesh) _management_mesh->cancelBeaconRadioFrame();
}

bool CommonCLI::handleBeaconCommand(const char* command, char* reply, size_t capacity) {
  if (!mesh::BeaconManager::isCommand(command)) return false;
  if (!_management_mesh || !_management_fs) {
    snprintf(reply, capacity, "Error: beacon storage unavailable");
    return true;
  }
  if (!_beacons) {
    _beacons = new (std::nothrow) mesh::BeaconManager;
    if (_beacons) _beacons->begin(_management_fs, _management_mesh, _rtc, _prefs->node_name);
  }
  if (!_beacons) { snprintf(reply, capacity, "Error: beacon allocation failed"); return true; }
  return _beacons->handle(command, reply, capacity);
}
#endif
