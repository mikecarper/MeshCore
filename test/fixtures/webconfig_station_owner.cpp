#include <cassert>
#include <cstring>
#include <new>
#include <helpers/WirelessControl.h>
struct Worker {
  bool running = false, stopping = false;
  bool isRunning() const { return running; }
  bool isStopping() const { return stopping; }
};
struct Board {
  bool isOTAUpdateRunning() const { return false; }
  const char* getManufacturerName() const { return "Station G2"; }
} board;
struct CLI {
  Board* getBoard() { return &board; }
  void* getObserverPrefs() { return this; }
};
struct WebConfigServer {
  bool owns, constructor_owns;
  WebConfigServer(void*, void*, bool owner, const unsigned char*,
                  const char*, const char*, const char*, const char*, bool canonical)
      : owns(owner), constructor_owns(owner) { assert(canonical); }
  bool isRunning() const { return false; }
  bool isStopping() const { return false; }
  void updateWiFiOwnership(bool owner) { owns = owner; }
  void requestNetworkAccess() {}
  bool requestStart(bool ap, char* reply) { return ap ? startSetupMode(reply) : startAutoMode(reply); }
  bool startAutoMode(char* reply) { strcpy(reply, "starting"); return true; }
  bool startSetupMode(char* reply) { strcpy(reply, "setup"); return true; }
};
struct MyMesh {
  Worker* @WORKER@ = nullptr;
  CLI _cli;
  WebConfigServer* _webconfig = nullptr;
  bool _unconfigured_setup_espnow_suspended = false;
  bool isMqttBridgeRunning() const { return @WORKER@ && @WORKER@->isRunning(); }
  bool isMqttBridgeStopping() const { return @WORKER@ && @WORKER@->isStopping(); }
  struct { unsigned char pub_key[32] = {}; } self_id;
  const char* getFirmwareVer() const { return "test"; }
  const char* getBuildDate() const { return "test"; }
  const char* getRole() const { return "test"; }
  bool startWebConfig(bool force_ap, char* reply);
  bool getWebUIStatus(char* reply) const { strcpy(reply, "Web UI status"); return true; }
  bool startWebConfigImpl(bool force_ap, char* reply, bool automatic_setup);
  bool webConfigOwnsWiFi() const;
  void suspendUnconfiguredSetupBridges();
  void refresh() {
@REFRESH@
  }
};
@START@
@SUSPEND@
int main() {
  MyMesh mesh;
  Worker worker;
  mesh.@WORKER@ = &worker;
  char reply[160] = {};
  mesh.startWebConfig(false, reply);
  assert(mesh._webconfig->constructor_owns && mesh._webconfig->owns);
  worker.running = true;
  mesh.refresh();
  assert(!mesh._webconfig->owns);
  worker.running = false;
  worker.stopping = true;
  mesh.refresh();
  assert(!mesh._webconfig->owns);
  worker.stopping = false;
  mesh.refresh();
  assert(mesh._webconfig->owns);
  mesh.@WORKER@ = nullptr;
  mesh.refresh();
  assert(mesh._webconfig->owns);
  delete mesh._webconfig;
}
