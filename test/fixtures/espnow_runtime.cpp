// Production methods and dispatch branches are inserted by test_espnow_runtime.py.
// The bridge driver is mocked to inject SDK failures without ESP32 hardware.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)

namespace mesh { struct Packet {}; }
struct StrHelper {
  static void strncpy(char* out, const char* in, size_t size) {
    assert(size); std::strncpy(out, in, size - 1); out[size - 1] = 0;
  }
};
struct NodePrefs {
  uint8_t bridge_enabled = 0, espnow_bridge_enabled = 0;
  uint16_t bridge_delay = 0;
  uint8_t bridge_pkt_src = 0, bridge_uart = 0, bridge_channel = 0, bridge_format = 0;
  uint32_t bridge_baud = 0;
  char bridge_secret[32] = {};
};
class AbstractBridge {
public:
  virtual ~AbstractBridge() = default;
  virtual void begin() = 0;
  virtual void end() = 0;
  virtual bool isRunning() const = 0;
  virtual void loop() = 0;
  virtual void sendPacket(mesh::Packet*) = 0;
};
class ESPNowBridge : public AbstractBridge {
  bool running = false;
public:
  unsigned begins = 0, ends = 0, fail_starts = 0, sent = 0, loops = 0;
  void begin() override {
    ++begins;
    if (fail_starts) { --fail_starts; return; }
    running = true;
  }
  void end() override { ++ends; running = false; }
  bool isRunning() const override { return running; }
  void loop() override { ++loops; }
  void sendPacket(mesh::Packet*) override { ++sent; }
};
static uint32_t fake_millis = 100;
static uint32_t millis() { return fake_millis; }
static bool millisHasNowPassed(uint32_t deadline) {
  return static_cast<int32_t>(fake_millis - deadline) >= 0;
}
struct FakeBoard { bool ota = false; bool isOTAUpdateRunning() const { return ota; } };
struct FakeCLI { FakeBoard board; FakeBoard* getBoard() { return &board; } };
struct Callbacks {
  virtual ~Callbacks() = default;
  virtual bool isBridgeRunning() const = 0;
  virtual bool setBridgeState(bool) = 0;
  virtual bool restartBridge() = 0;
  @CALLBACK_FORWARDS@
};
class MyMesh : public Callbacks {
public:
  NodePrefs _prefs;
  ESPNowBridge bridge;
  uint32_t shared_espnow_retry_at = 0;
  FakeCLI _cli;
  void configureBridgeFilter(AbstractBridge*) {}
  void defaults() { @DEFAULTS@ }
  void boot() {
    @BOOT@
  }
  void routeRx(mesh::Packet* pkt) {
    @ROUTE_RX@
  }
  void routeTx(mesh::Packet* pkt) {
    @ROUTE_TX@
  }
  void serviceBridges() {
    @SERVICE@
  }
  bool bridgesPreventSleep() const {
    using mesh::powerdiag::mask;
    using mesh::powerdiag::Reason;
    @PENDING@
    return false;
  }
  @LIFECYCLE@
};
@PARSERS@
class CommonCLI {
public:
  NodePrefs* _prefs;
  MyMesh* _callbacks;
  NodePrefs stored;
  unsigned saves = 0;
  explicit CommonCLI(MyMesh& mesh) : _prefs(&mesh._prefs), _callbacks(&mesh) {}
  void savePrefs() { ++saves; stored = *_prefs; }
  void set(const char* input, char* reply) {
    char config[256] = {};
    assert(strlen(input) < sizeof(config)); strcpy(config, input);
    @SET_BRANCHES@
  }
  void get(const char* config, char* reply) { @GET_BRANCHES@ }
};
static void expect_get(CommonCLI& cli, const char* key, const char* expected) {
  char reply[256] = {}; cli.get(key, reply); assert(strcmp(reply, expected) == 0);
}
static void assert_aliases(CommonCLI& cli, bool enabled, bool running) {
  expect_get(cli, "bridge.enabled", enabled ? "> on" : "> off");
  expect_get(cli, "espnow.enabled", enabled ? "> on" : "> off");
  expect_get(cli, "bridge.running", running ? "> on" : "> off");
  expect_get(cli, "espnow.running", running ? "> on" : "> off");
}
int main() {
  {
    MyMesh mesh; mesh.defaults();
    CommonCLI cli(mesh);
#if defined(ESPNOW_BRIDGE_MERGED) && !defined(ESPNOW_BRIDGE_DEFAULT_ON)
    const bool initial_enabled = false;
#else
    const bool initial_enabled = true;
#endif
    assert(mesh._prefs.bridge_enabled == initial_enabled);
    assert(mesh._prefs.espnow_bridge_enabled == initial_enabled);
    mesh.boot();
    assert(mesh.bridge.begins == (initial_enabled ? 1 : 0));
    assert_aliases(cli, initial_enabled, initial_enabled);
    expect_get(cli, "bridge.type", "> espnow");
  }
  MyMesh mesh; mesh.defaults();
  CommonCLI cli(mesh);
  char reply[256] = {};
  cli.set("bridge.enabled off", reply);
  assert(strcmp(reply, "OK") == 0 && !mesh.bridgesPreventSleep());
  assert_aliases(cli, false, false);
  const unsigned initial_begins = mesh.bridge.begins;
  cli.set("bridge.channel 6", reply);
  assert(strcmp(reply, "OK") == 0 && mesh.bridge.begins == initial_begins);
  cli.set("bridge.secret combined-secret", reply);
  assert(strcmp(reply, "OK") == 0 && mesh.bridge.begins == initial_begins);
  cli.set("bridge.format raw", reply);
  assert(strncmp(reply, "OK", 2) == 0 && mesh.bridge.begins == initial_begins);
  cli.set("espnow.enabled on", reply);
  assert(strcmp(reply, "OK") == 0 && mesh.bridgesPreventSleep());
  assert_aliases(cli, true, true);
  mesh::Packet packet;
  mesh.routeRx(&packet); mesh.routeTx(&packet); mesh.serviceBridges();
  assert(mesh.bridge.sent == 1 && mesh.bridge.loops == 1); // RX is routed once.
  cli.set("bridge.source tx", reply);
  assert(strcmp(reply, "OK") == 0);
  mesh.routeRx(&packet); mesh.routeTx(&packet);
  assert(mesh.bridge.sent == 2); // TX is routed once, RX no longer feeds bridge.
  const unsigned configured_begins = mesh.bridge.begins;
  cli.set("bridge.channel 11", reply);
  cli.set("bridge.secret updated-secret", reply);
  cli.set("bridge.format wrapped", reply);
  assert(mesh.bridge.begins == configured_begins + 3);
  expect_get(cli, "bridge.channel", "> 11");
  expect_get(cli, "bridge.secret", "> updated-secret");
  expect_get(cli, "bridge.format", "> wrapped");
  const unsigned saves = cli.saves;
  for (const char* invalid : {"bridge.enabled onjunk", "espnow.enabled onjunk",
       "bridge.channel 0", "bridge.channel 14", "bridge.channel 6junk",
       "bridge.secret ", "bridge.format unsupported", "bridge.source rxjunk"}) {
    cli.set(invalid, reply);
    assert(strncmp(reply, "Error", 5) == 0 && cli.saves == saves);
    assert_aliases(cli, true, true);
  }
  // The persisted aliases reconstruct exactly the enabled sole runtime.
  MyMesh restarted; restarted.defaults(); restarted._prefs = cli.stored;
  restarted.boot();
  assert(restarted.bridge.begins == 1 && restarted.isBridgeRunning());
  // Failed SDK initialization preserves saved intent and retries cooperatively.
  cli.set("espnow.enabled off", reply);
  mesh.bridge.fail_starts = 2;
  cli.set("bridge.enabled on", reply);
  assert(strstr(reply, "setting saved") && !mesh.isBridgeRunning());
  assert_aliases(cli, true, false);
  mesh.serviceBridges();
  const unsigned after_first_retry = mesh.bridge.begins;
  mesh.serviceBridges();
  assert(mesh.bridge.begins == after_first_retry && !mesh.bridgesPreventSleep());
  fake_millis += 5001;
  mesh.serviceBridges();
  assert(mesh.bridge.begins == after_first_retry + 1 && mesh.isBridgeRunning());
  // Paused browser OTA must block direct enable, restart, and timed retries.
  assert(mesh.setEspNowBridgeState(false));
  mesh._cli.board.ota = true;
  const unsigned before_ota = mesh.bridge.begins;
  cli.set("espnow.enabled on", reply);
  assert(strstr(reply, "setting saved") && !mesh.isEspNowBridgeRunning());
  cli.set("bridge.channel 3", reply);
  assert(strstr(reply, "setting saved") && !mesh.isBridgeRunning());
  for (unsigned tick = 0; tick < 10; ++tick) {
    fake_millis += 5001;
    mesh.serviceBridges();
  }
  assert(mesh.bridge.begins == before_ota && !mesh.bridgesPreventSleep());
  mesh._cli.board.ota = false;
  mesh.serviceBridges();
  assert(mesh.bridge.begins == before_ota + 1 && mesh.isBridgeRunning());
  // Runtime switches through either spelling must not leave the radio running.
  for (unsigned cycle = 0; cycle < 100; ++cycle) {
    cli.set("bridge.enabled off", reply);
    assert(strcmp(reply, "OK") == 0 && !mesh.bridgesPreventSleep());
    assert_aliases(cli, false, false);
    cli.set("espnow.enabled on", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.bridgesPreventSleep());
    assert_aliases(cli, true, true);
    cli.set("espnow.enabled off", reply);
    assert(strcmp(reply, "OK") == 0 && !mesh.bridgesPreventSleep());
    cli.set("bridge.enabled on", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.isBridgeRunning());
  }
  cli.set("espnow.enabled off", reply);
  const unsigned sent_when_off = mesh.bridge.sent;
  mesh.routeRx(&packet); mesh.routeTx(&packet); mesh.serviceBridges();
  assert(mesh.bridge.sent == sent_when_off && !mesh.bridgesPreventSleep());
  puts("ESP-NOW runtime checks passed");
}
