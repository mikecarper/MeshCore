#pragma once
#include <Mesh.h>
#include <vector>
class NodePrefs { public: uint8_t path_hash_mode = 0; };
class CommonCLI {
  uint8_t path[64] = {};
public:
  uint8_t path_len = 0;
  bool scope_available = true;
  bool adoption_succeeds = true, route_persisted = false;
  unsigned adoption_calls = 0;
  bool getDataTxPath(const uint8_t*& out, uint8_t& len) const {
    out = path; len = path_len;
    return path_len != 0xff && mesh::Packet::isValidPathLen(path_len);
  }
  bool resolveDataTxScope(TransportKey& scope, char* = nullptr,
                          size_t = 0, bool* ambiguous = nullptr) const {
    if (ambiguous) *ambiguous = false;
    scope = TransportKey(); return scope_available;
  }
  bool adoptLegacyDataTxPath(const uint8_t* legacy_path, uint8_t legacy_len) {
    ++adoption_calls;
    if (route_persisted) return true; // the explicitly saved shared route wins
    if (!adoption_succeeds) return false;
    path_len = mesh::Packet::copyPath(path, legacy_path, legacy_len);
    route_persisted = true;
    return true;
  }
};
class CommonCLICallbacks {
public:
  const char* firmware_version = "v1.17.1.5";
  const char* getFirmwareVer() { return firmware_version; }
  const char* getRole() { return "repeater"; }
};
struct LocationProvider { bool isEnabled() { return false; } };
class SensorManager {
public:
  bool isGPSDetected() { return false; }
  LocationProvider* getLocationProvider() { return nullptr; }
};
struct ClientInfo { mesh::LocalIdentity id; bool admin = true; bool isAdmin() const { return admin; } };
class ClientACL {
public:
  std::vector<ClientInfo> clients;
  int getNumClients() { return clients.size(); }
  ClientInfo* getClientByIdx(int i) { return &clients[i]; }
};
