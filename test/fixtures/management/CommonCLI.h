#pragma once
#include <Mesh.h>
#include <vector>
class NodePrefs { public: uint8_t path_hash_mode = 0; };
class CommonCLI {
  uint8_t path[64] = {};
public:
  uint8_t path_len = 0;
  bool scope_available = true;
  bool getDataTxPath(const uint8_t*& out, uint8_t& len) const {
    out = path; len = path_len;
    return path_len != 0xff && mesh::Packet::isValidPathLen(path_len);
  }
  bool resolveDataTxScope(TransportKey& scope, char* = nullptr,
                          size_t = 0, bool* ambiguous = nullptr) const {
    if (ambiguous) *ambiguous = false;
    scope = TransportKey(); return scope_available;
  }
  bool adoptLegacyDataTxPath(const uint8_t*, uint8_t) { return true; }
};
class CommonCLICallbacks {
public:
  const char* getFirmwareVer() { return "v1.17.1.5"; }
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
