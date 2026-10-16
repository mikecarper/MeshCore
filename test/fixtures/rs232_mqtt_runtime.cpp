// Real repeater methods and CLI dispatch are inserted by the Python harness.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <initializer_list>
#include <new>
#include <helpers/CLICommandUtils.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#include <helpers/bridges/RS232PowerSettings.h>
struct FakeRng { void random(uint8_t* out, size_t size) { memset(out, 0x42, size); } };
struct FakeRadio { const void* profiles() const { return nullptr; } };
#define BRIDGE_MAX_BAUD 115200
#define PUB_KEY_SIZE 32
#define MESH_DEBUG_PRINTLN(...) ((void)0)

namespace mesh {
struct Packet { bool allowed = true; };
struct LocalIdentity { uint8_t pub_key[32] = {}; };
struct Utils { static void toHex(char* out, const uint8_t*, unsigned) { strcpy(out, "mock-node"); } };
}
struct StrHelper {
  static void strncpy(char* out, const char* in, size_t size) {
    assert(size); std::strncpy(out, in, size - 1); out[size - 1] = 0;
  }
};
struct NodePrefs {
  uint8_t bridge_enabled = 0, espnow_bridge_enabled = 0, rs232_bridge_enabled = 0;
  uint16_t bridge_delay = 0;
  uint8_t powersaving_enabled = 1, rs232_autosleep = 1;
  uint8_t rs232_heartbeat = 1;
  uint16_t rs232_idle_seconds = 300;
  uint8_t bridge_pkt_src = 0, bridge_uart = 0, bridge_channel = 0, bridge_format = 0;
  uint32_t bridge_baud = 0;
  float freq = 915, bw = 125;
  uint8_t sf = 7, cr = 5, disable_fwd = 0;
  char bridge_secret[32] = {}, node_name[32] = "triple bridge";
};
struct FakeUart {
  unsigned begins = 0, ends = 0;
  int rx = -1, tx = -1;
  uint32_t baud = 0;
  bool running = false;
} Serial2;
struct FakeSensors {
  unsigned yield_calls = 0;
  bool gpsUsesSerialUart(uint8_t uart) const { return uart == 1; }
  bool gpsSerialTransportMayConflict(uint8_t uart) const { return uart == 1; }
  bool gpsSerialTransportCanYield(uint8_t) const { return true; }
  bool setGpsSerialTransportBlocked(uint8_t, bool) { ++yield_calls; return true; }
};
class AbstractBridge {
  bool (*filter)(void*, const mesh::Packet*) = nullptr;
  void* filter_context = nullptr;
protected:
  bool allow(mesh::Packet* packet) { return !filter || filter(filter_context, packet); }
public:
  unsigned filters = 0;
  virtual ~AbstractBridge() = default;
  virtual void begin() = 0;
  virtual void end() = 0;
  virtual bool isRunning() const = 0;
  virtual void loop() = 0;
  virtual void sendPacket(mesh::Packet*) = 0;
  void setPacketFilter(bool (*check)(void*, const mesh::Packet*), void* context) {
    filter = check; filter_context = context; ++filters;
  }
  void setIngressProfiles(const void*) {}
};
class RS232Bridge : public AbstractBridge {
  NodePrefs* prefs;
  FakeUart& uart;
  int rx, tx;
  bool running = false;
public:
  using PowerAction = mesh::bridge::RS232PowerPolicy::Action;
  mesh::bridge::RS232PowerPolicy power;
  void setHeartbeatSession(uint64_t) {}
  void setHeartbeatEnabled(bool) {}
  PowerAction servicePowerPolicy(bool allowed, uint32_t idle, uint32_t now, bool force = false) {
    return power.service(allowed && force, idle, now, running, false);
  }
  void completePowerAction(PowerAction action, bool success, uint32_t now) { power.complete(action, success, now); }
  uint32_t powerWakeDelayMs(uint32_t now) const { return power.wakeDelay(now); }
  void formatHeartbeatStatus(char* out, size_t size, uint32_t) const { snprintf(out, size, "state=active peer=unknown"); }
  inline static unsigned live = 0, fail_starts = 0, fail_allocations = 0;
  static void* operator new(size_t size, const std::nothrow_t&) noexcept {
    if (fail_allocations) { --fail_allocations; return nullptr; }
    return ::operator new(size, std::nothrow);
  }
  static void operator delete(void* pointer) noexcept { ::operator delete(pointer); }
  unsigned loops = 0, sent = 0;
  RS232Bridge(NodePrefs* p, FakeUart& u, int r, int t, void*, void*)
      : prefs(p), uart(u), rx(r), tx(t) { ++live; }
  ~RS232Bridge() override { --live; }
  void begin() override {
    assert(filters); ++uart.begins;
    if (fail_starts) { --fail_starts; return; }
    uart.rx = rx; uart.tx = tx; uart.baud = prefs->bridge_baud;
    running = uart.running = true;
  }
  void end() override { ++uart.ends; running = uart.running = false; }
  bool isRunning() const override { return running; }
  void loop() override { ++loops; }
  void sendPacket(mesh::Packet* p) override { if (allow(p)) ++sent; }
};
class ESPNowBridge : public AbstractBridge {
  bool running = false;
public:
  unsigned begins = 0, ends = 0, fail_starts = 0, loops = 0, sent = 0;
  void begin() override {
    assert(filters); ++begins;
    if (fail_starts) { --fail_starts; return; }
    running = true;
  }
  void end() override { ++ends; running = false; }
  bool isRunning() const override { return running; }
  void loop() override { ++loops; }
  void sendPacket(mesh::Packet* p) override { if (allow(p)) ++sent; }
};
struct MQTTNodeInfo {
  const char* node_name;
  float* freq;
  float* bw;
  uint8_t* sf;
  uint8_t* cr;
  uint8_t* repeat_flag;
  bool repeat_when_nonzero;
};
class MQTTBridge : public AbstractBridge {
  bool running = false;
  bool stopping = false;
  bool network_suspended = false;
public:
  inline static unsigned live = 0, fail_starts = 0;
  unsigned begins = 0, ends = 0, loops = 0, sent = 0, received = 0;
  MQTTBridge(MQTTNodeInfo, void*, void*, mesh::LocalIdentity*) { ++live; }
  ~MQTTBridge() override { --live; }
  void setDeviceID(const char*) {}
  void setFirmwareVersion(const char*) {}
  void setBoardModel(const char*) {}
  void setBuildDate(const char*) {}
  void setStatsSources(void*, void*, void*, void*) {}
  void begin() override {
    assert(filters); ++begins;
    if (fail_starts) { --fail_starts; return; }
    running = true;
    network_suspended = false;
  }
  void end() override { ++ends; running = stopping = network_suspended = false; }
  void requestStop() { stopping = running; }
  bool isStopping() const { return stopping; }
  bool isRunning() const override { return running; }
  bool isNetworkSuspended() const { return network_suspended; }
  void setNetworkSuspended(bool paused) {
    assert(running && !stopping);
    network_suspended = paused;
  }
  void loop() override {
    ++loops;
    if (stopping) { ++ends; running = stopping = network_suspended = false; }
  }
  void sendPacket(mesh::Packet* p) override { if (allow(p)) ++sent; }
  void onPacketReceived(mesh::Packet* p) { if (allow(p)) ++received; }
};
static uint32_t fake_millis = 100;
static uint32_t millis() { return fake_millis; }
static bool millisHasNowPassed(uint32_t deadline) {
  return static_cast<int32_t>(fake_millis - deadline) >= 0;
}
struct FakeWiFi { bool connected = true; bool isConnected() const { return connected; } } WiFi;
struct FakeBoard {
  bool ota = false, allow_start = true, allow_stop = true;
  bool isOTAUpdateRunning() const { return ota; }
  const char* getManufacturerName() const { return "synthetic ESP32"; }
  bool startOTAUpdate(const char*, char* reply, bool) {
    ota = allow_start; strcpy(reply, allow_start ? "Started: http://192.168.4.1/update" : "ERR: OTA failed");
    return allow_start;
  }
  bool stopOTAUpdate(char* reply) {
    if (allow_stop) ota = false;
    strcpy(reply, allow_stop ? "OK - OTA stopped" : "ERR: upload active"); return allow_stop;
  }
};
struct FakeCLI {
  FakeBoard board;
  FakeBoard* getBoard() { return &board; }
  void* getObserverPrefs() { return nullptr; }
};
struct FakeAlerter { void setBridge(MQTTBridge*) {} };
struct Callbacks {
  virtual ~Callbacks() = default;
  virtual bool isBridgeRunning() const = 0;
  virtual bool setBridgeState(bool) = 0;
  virtual bool restartBridge() = 0;
  virtual bool isRs232BridgeRunning() const = 0;
  virtual bool setRs232BridgeState(bool) = 0;
  virtual bool restartRs232Bridge() = 0;
  virtual void formatRs232Status(char*) = 0;
  virtual bool isEspNowBridgeRunning() = 0;
  virtual bool setEspNowBridgeState(bool) = 0;
  virtual bool restartEspNowBridge() = 0;
  virtual bool isMqttBridgeRunning() = 0;
  virtual bool setMqttBridgeState(bool) = 0;
  virtual bool requestMqttBridgeStop() = 0;
  virtual bool isMqttBridgeStopping() = 0;
  virtual bool restartMqttBridge() = 0;
};
class MyMesh : public Callbacks {
public:
  NodePrefs _prefs;
  RS232Bridge* bridge = nullptr;
  MQTTBridge* mqtt_bridge = nullptr;
  ESPNowBridge espnow_bridge;
  uint8_t active_rs232_bridge_uart = 0;
  uint32_t shared_espnow_retry_at = 0;
  unsigned filter_checks = 0;
  FakeSensors sensors;
  FakeCLI _cli;
  FakeAlerter _alerter;
  mesh::LocalIdentity self_id;
  FakeRadio radio; FakeRadio* _radio = &radio;
  void* _mgr = nullptr; void* _ms = nullptr;
  void* getRTCClock() { return nullptr; }
  FakeRng rng;
  FakeRng* getRNG() { return &rng; }
  mesh::LocalIdentity getSelfId() { return self_id; }
  const char* getFirmwareVer() { return "test"; }
  const char* getBuildDate() { return "test"; }
  bool allowTransportPacket(const mesh::Packet* p, unsigned mode) {
    assert(mode == 2); ++filter_checks; return p->allowed;
  }
  @FILTER@
  void defaults() {
    @DEFAULTS@
  }
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
    @PENDING@
    return false;
  }
  ~MyMesh() override {
    assert(setRs232BridgeState(false));
    assert(setBridgeState(false));
    delete mqtt_bridge;
  }
  @LIFECYCLE@
};
@PARSERS@
class CommonCLI {
public:
  NodePrefs* _prefs;
  MyMesh* _callbacks;
  FakeSensors* _sensors;
  FakeBoard* _board;
  @OTA_STATE@
  NodePrefs stored;
  unsigned saves = 0;
  bool fail_save = false;
  explicit CommonCLI(MyMesh& mesh) : _prefs(&mesh._prefs), _callbacks(&mesh),
      _sensors(&mesh.sensors), _board(mesh._cli.getBoard()) {}
  void savePrefs() { ++saves; stored = *_prefs; }
  bool trySavePrefs() { if (fail_save) return false; savePrefs(); return true; }
  void set(const char* input, char* reply) {
    char config[256] = {}; assert(strlen(input) < sizeof(config)); strcpy(config, input);
    @SET_BRANCHES@
  }
  bool get(const char* config, char* reply) {
    @GET_BRANCHES@
    return false;
  }
  void run(const char* command, char* reply) {
    if (false) {
      @OTA@
    }
  }
};
static void expect_get(CommonCLI& cli, const char* key, const char* expected) {
  char reply[256] = {}; cli.get(key, reply); assert(strcmp(reply, expected) == 0);
}
int main() {
  {
    MyMesh fresh; fresh.defaults(); CommonCLI cli(fresh);
    assert(fresh._prefs.bridge_enabled && fresh._prefs.espnow_bridge_enabled);
    assert(!fresh._prefs.rs232_bridge_enabled);
    const unsigned before = Serial2.begins;
    fresh.boot();
    assert(fresh.isMqttBridgeRunning() && fresh.isEspNowBridgeRunning());
    assert(!fresh.isRs232BridgeRunning() && !fresh.bridge && Serial2.begins == before);
    expect_get(cli, "bridge.type", "> mqtt+rs232+espnow");
    expect_get(cli, "rs232.enabled", "> off");
    expect_get(cli, "rs232.running", "> off");
    fresh.mqtt_bridge->setNetworkSuspended(true);
    assert(fresh.isMqttBridgeRunning() && fresh._prefs.bridge_enabled);
    assert(fresh.bridgesPreventSleep());  // ESP-NOW retains its radio owner.
    assert(fresh.setEspNowBridgeState(false));
    assert(!fresh.bridgesPreventSleep());  // Paused MQTT is still configured.
    assert(fresh.setRs232BridgeState(true));
    assert(fresh.bridgesPreventSleep());  // An active UART remains independent.
    assert(fresh.setRs232BridgeState(false));
    assert(!fresh.bridgesPreventSleep());
    fresh.mqtt_bridge->setNetworkSuspended(false);
    assert(fresh.bridgesPreventSleep());  // The recovery probe owns the radio.
    char pause_reply[256] = {};
    cli.set("espnow.enabled off", pause_reply);
    assert(strcmp(pause_reply, "OK") == 0);
    fresh.mqtt_bridge->setNetworkSuspended(true);
    assert(fresh.requestMqttBridgeStop());
    assert(fresh.isMqttBridgeStopping() && fresh.bridgesPreventSleep());
    fresh.serviceBridges();
    assert(!fresh.isMqttBridgeRunning() && !fresh.bridgesPreventSleep());
    assert(fresh.setBridgeState(true));
    assert(fresh.isMqttBridgeRunning() && fresh.bridgesPreventSleep());
    cli.set("espnow.enabled on", pause_reply);
    assert(strcmp(pause_reply, "OK") == 0 && fresh.isEspNowBridgeRunning());
  }
  MyMesh mesh; mesh.defaults(); mesh.boot(); CommonCLI cli(mesh);
  char reply[256] = {};
  cli.set("rs232.enabled on", reply);
  assert(strcmp(reply, "OK") == 0 && mesh.isRs232BridgeRunning());
  assert(Serial2.rx == 5 && Serial2.tx == 6 && Serial2.baud == 115200);
  assert(mesh.sensors.yield_calls == 0 && mesh.bridgesPreventSleep());
  expect_get(cli, "rs232.enabled", "> on"); expect_get(cli, "rs232.running", "> on");
  mesh::Packet packet;
  mesh.routeRx(&packet); mesh.routeTx(&packet); mesh.serviceBridges();
  assert(mesh.bridge->sent == 1 && mesh.bridge->loops == 1);
  assert(mesh.espnow_bridge.sent == 1 && mesh.espnow_bridge.loops == 1);
  assert(mesh.mqtt_bridge->received == 1 && mesh.mqtt_bridge->sent == 1);
  assert(mesh.mqtt_bridge->loops == 1); // Core 1 services stop acknowledgement only.
  assert(mesh.filter_checks == 4);
  packet.allowed = false;
  mesh.routeRx(&packet); mesh.routeTx(&packet);
  assert(mesh.filter_checks == 8 && mesh.bridge->sent == 1 && mesh.espnow_bridge.sent == 1);
  assert(mesh.mqtt_bridge->received == 1 && mesh.mqtt_bridge->sent == 1);
  packet.allowed = true;
  const unsigned wifi_starts = mesh.mqtt_bridge->begins, wireless_starts = mesh.espnow_bridge.begins;
  cli.set("bridge.baud 57600", reply);
  assert(strcmp(reply, "OK") == 0 && Serial2.baud == 57600);
  cli.set("bridge.uart 2", reply);
  assert(strcmp(reply, "OK") == 0 && mesh.isRs232BridgeRunning());
  assert(mesh.mqtt_bridge->begins == wifi_starts && mesh.espnow_bridge.begins == wireless_starts);
  const unsigned saves = cli.saves;
  for (const char* invalid : {"rs232.enabled onjunk", "bridge.uart 1", "bridge.uart 2junk",
       "bridge.baud 9599", "bridge.baud 115201", "bridge.baud 57600junk"}) {
    cli.set(invalid, reply); assert(strncmp(reply, "Error", 5) == 0 && cli.saves == saves);
    assert(mesh.isRs232BridgeRunning() && mesh.isMqttBridgeRunning() && mesh.isEspNowBridgeRunning());
  }
  RS232Bridge::fail_starts = 1;
  cli.set("bridge.baud 19200", reply);
  assert(strstr(reply, "baud unchanged") && cli.saves == saves);
  assert(mesh._prefs.bridge_baud == 57600 && Serial2.baud == 57600 && mesh.isRs232BridgeRunning());
  assert(mesh.mqtt_bridge->begins == wifi_starts && mesh.espnow_bridge.begins == wireless_starts);
  cli.set("rs232.enabled off", reply); assert(strcmp(reply, "OK") == 0 && !mesh.bridge);
  const unsigned disabled_saves = cli.saves;
  RS232Bridge::fail_starts = 1;
  cli.set("rs232.enabled on", reply);
  assert(strstr(reply, "setting unchanged") && cli.saves == disabled_saves && !mesh.bridge);
  assert(!mesh._prefs.rs232_bridge_enabled && mesh.isMqttBridgeRunning() && mesh.isEspNowBridgeRunning());
  RS232Bridge::fail_allocations = 1;
  cli.set("rs232.enabled on", reply);
  assert(strstr(reply, "setting unchanged") && !mesh.bridge && !mesh._prefs.rs232_bridge_enabled);
  assert(cli.saves == disabled_saves && mesh.isMqttBridgeRunning() && mesh.isEspNowBridgeRunning());
  cli.fail_save = true;
  cli.set("rs232.enabled on", reply);
  assert(strstr(reply, "setting unchanged") && !mesh.bridge && !mesh._prefs.rs232_bridge_enabled);
  assert(cli.saves == disabled_saves && mesh.isMqttBridgeRunning() && mesh.isEspNowBridgeRunning());
  cli.fail_save = false;
  cli.set("rs232.enabled on", reply); assert(strcmp(reply, "OK") == 0);
  const unsigned saved_after_enable = cli.saves;
  cli.fail_save = true;
  cli.set("rs232.enabled off", reply);
  assert(strstr(reply, "setting unchanged") && mesh.isRs232BridgeRunning() && mesh._prefs.rs232_bridge_enabled);
  cli.set("bridge.baud 19200", reply);
  assert(strstr(reply, "baud unchanged") && mesh._prefs.bridge_baud == 57600 && Serial2.baud == 57600);
  cli.set("bridge.uart 2", reply);
  assert(strstr(reply, "UART unchanged") && mesh._prefs.bridge_uart == 2 && mesh.isRs232BridgeRunning());
  assert(cli.saves == saved_after_enable && mesh.mqtt_bridge->begins == wifi_starts);
  assert(mesh.espnow_bridge.begins == wireless_starts);
  cli.fail_save = false;
  // Saved UART settings recreate a running independent bridge after reboot.
  {
    MyMesh restarted; restarted.defaults(); restarted._prefs = cli.stored; restarted.boot();
    assert(restarted.isRs232BridgeRunning() && restarted.isMqttBridgeRunning());
    assert(restarted._prefs.bridge_baud == 57600 && restarted.isEspNowBridgeRunning());
  }
  // ESP-NOW format/channel changes and its legacy alias never restart the UART.
  const unsigned uart_before_wireless = Serial2.begins;
  cli.set("bridge.channel 6", reply); cli.set("bridge.secret three-transports", reply);
  cli.set("bridge.format raw", reply); assert(strncmp(reply, "OK", 2) == 0);
  assert(Serial2.begins == uart_before_wireless && mesh.mqtt_bridge->begins == wifi_starts);
  cli.set("bridge.enabled off", reply);
  assert(strcmp(reply, "OK") == 0 && !mesh.isEspNowBridgeRunning());
  assert(mesh.isMqttBridgeRunning() && mesh.isRs232BridgeRunning());
  // A failed intent save must not signal an uncancellable worker stop.
  cli.fail_save = true;
  cli.set("mqtt.enabled off", reply);
  assert(strstr(reply, "not saved; unchanged") && mesh._prefs.bridge_enabled);
  assert(mesh.isMqttBridgeRunning() && !mesh.isMqttBridgeStopping());
  cli.fail_save = false;
  cli.set("mqtt.enabled off", reply);
  assert(strcmp(reply, "OK") == 0 && mesh.isMqttBridgeRunning());
  assert(mesh.isMqttBridgeStopping() && !mesh._prefs.bridge_enabled);
  expect_get(cli, "mqtt.running", "> on");
  expect_get(cli, "mqtt.stopping", "> on");
  const unsigned stopping_saves = cli.saves;
  cli.set("mqtt.enabled on", reply);
  assert(strstr(reply, "MQTT is stopping") && !mesh._prefs.bridge_enabled);
  assert(cli.saves == stopping_saves && !mesh.setMqttBridgeState(true));
  cli.set("mqtt.enabled off", reply); // a repeated request is accepted
  assert(strcmp(reply, "OK") == 0 && mesh.isMqttBridgeStopping());
  assert(mesh.isRs232BridgeRunning() && mesh.bridgesPreventSleep());
  const unsigned uart_tx_before = mesh.bridge->sent;
  mesh.routeRx(&packet); mesh.serviceBridges();
  assert(mesh.bridge->sent == uart_tx_before + 1);
  assert(!mesh.isMqttBridgeRunning() && !mesh.isMqttBridgeStopping());
  expect_get(cli, "mqtt.stopping", "> off");
  cli.fail_save = true;
  cli.set("mqtt.enabled on", reply);
  assert(strstr(reply, "not saved; unchanged") && !mesh._prefs.bridge_enabled);
  assert(!mesh.isMqttBridgeRunning());
  cli.fail_save = false;
  cli.set("espnow.enabled on", reply); assert(strcmp(reply, "OK") == 0);
  cli.set("mqtt.enabled on", reply); assert(strcmp(reply, "OK") == 0);
  mesh.serviceBridges(); assert(mesh.isEspNowBridgeRunning());
  // Browser OTA releases only WiFi users and leaves the enabled UART running.
  const unsigned uart_before_ota = Serial2.begins;
  cli.run("start ota ap", reply);
  assert(mesh._cli.board.ota && !mesh.isMqttBridgeRunning() && !mesh.isEspNowBridgeRunning());
  assert(mesh.isRs232BridgeRunning() && Serial2.begins == uart_before_ota);
  for (unsigned tick = 0; tick < 5; ++tick) { fake_millis += 5001; mesh.serviceBridges(); }
  assert(!mesh.isEspNowBridgeRunning() && mesh.isRs232BridgeRunning());
  mesh._cli.board.allow_stop = false; cli.run("stop ota", reply);
  assert(strcmp(reply, "ERR: upload active") == 0 && !mesh.isMqttBridgeRunning());
  mesh._cli.board.allow_stop = true; cli.run("stop ota", reply); mesh.serviceBridges();
  assert(!mesh._cli.board.ota && mesh.isMqttBridgeRunning() && mesh.isEspNowBridgeRunning());
  assert(mesh.isRs232BridgeRunning() && Serial2.begins == uart_before_ota);
  // Wireless startup failure retries without stopping or reallocating UART.
  cli.set("espnow.enabled off", reply); mesh.espnow_bridge.fail_starts = 2;
  cli.set("espnow.enabled on", reply); mesh.serviceBridges();
  const unsigned retry_starts = mesh.espnow_bridge.begins;
  mesh.serviceBridges(); assert(mesh.espnow_bridge.begins == retry_starts);
  fake_millis += 5001; mesh.serviceBridges(); assert(!mesh.isEspNowBridgeRunning());
  fake_millis += 5001; mesh.serviceBridges(); assert(mesh.isEspNowBridgeRunning());
  assert(mesh.isRs232BridgeRunning() && Serial2.begins == uart_before_ota);
  // Every switch, parameter change and rollback leaves other transports intact.
  for (unsigned cycle = 0; cycle < 100; ++cycle) {
    cli.set("rs232.enabled off", reply);
    assert(!mesh.bridge && mesh.isMqttBridgeRunning() && mesh.isEspNowBridgeRunning());
    cli.set("rs232.enabled on", reply);
    assert(mesh.isRs232BridgeRunning() && RS232Bridge::live == 1);
  }
  cli.set("rs232.enabled off", reply); cli.set("espnow.enabled off", reply); cli.set("mqtt.enabled off", reply);
  assert(mesh.isMqttBridgeStopping() && mesh.bridgesPreventSleep());
  mesh.serviceBridges();
  assert(!mesh.bridgesPreventSleep() && RS232Bridge::live == 0);
  cli.run("start ota", reply); cli.run("stop ota", reply); mesh.serviceBridges();
  assert(!mesh.isRs232BridgeRunning() && !mesh.isMqttBridgeRunning() && !mesh.isEspNowBridgeRunning());
  assert(mesh.sensors.yield_calls == 0);
  puts("Three transport runtime checks passed");
}
