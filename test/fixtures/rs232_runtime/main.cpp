#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <vector>
#include <helpers/CLICommandUtils.h>
#include <helpers/PowerSavingDiagnostics.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#include <helpers/bridges/RS232PowerSettings.h>

static uint32_t fake_millis = 100;
static uint32_t millis() { return fake_millis; }
struct FakeRng { void random(uint8_t* out, size_t size) { memset(out, 0x42, size); } };

#define WITH_BRIDGE 1
#define WITH_RS232_BRIDGE Serial2
#define WITH_RS232_BRIDGE_UART 2
#define WITH_RS232_BRIDGE_RX 16
#define WITH_RS232_BRIDGE_TX 17
#define ENV_INCLUDE_GPS 1
#define BRIDGE_MAX_BAUD 115200
#define MESH_DEBUG_PRINTLN(...) ((void)0)

namespace mesh { struct Packet {}; }
struct StrHelper {
  static void strncpy(char* out, const char* in, size_t size) {
    assert(size != 0);
    std::strncpy(out, in, size - 1); out[size - 1] = 0;
  }
};

template<typename T> T constrain(T value, int low, int high) {
  return value < static_cast<T>(low) ? static_cast<T>(low)
      : value > static_cast<T>(high) ? static_cast<T>(high) : value;
}

struct NodePrefs {
  uint8_t bridge_enabled = 0, espnow_bridge_enabled = 0, rs232_bridge_enabled = 0;
  uint16_t bridge_delay = 0;
  uint8_t powersaving_enabled = 1, rs232_autosleep = 1;
  uint8_t rs232_heartbeat = 1;
  uint16_t rs232_idle_seconds = 300;
  uint8_t bridge_pkt_src = 0, bridge_uart = 0, bridge_channel = 0, bridge_format = 0;
  uint32_t bridge_baud = 0;
  char bridge_secret[32] = {};
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
public:
  virtual ~AbstractBridge() = default;
  virtual void begin() = 0;
  virtual void end() = 0;
  virtual bool isRunning() const = 0;
  virtual void loop() = 0;
  virtual void sendPacket(mesh::Packet*) = 0;
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
  inline static unsigned live = 0;
  inline static unsigned fail_starts = 0;
  unsigned loops = 0, sent = 0;
  RS232Bridge(NodePrefs* prefs_, FakeUart& uart_, int rx_, int tx_, void*, void*)
      : prefs(prefs_), uart(uart_), rx(rx_), tx(tx_) { ++live; }
  ~RS232Bridge() override { --live; }
  void begin() override {
    ++uart.begins;
    if (fail_starts != 0) { --fail_starts; return; }
    uart.rx = rx; uart.tx = tx; uart.baud = prefs->bridge_baud;
    running = uart.running = true;
  }
  void end() override { ++uart.ends; running = uart.running = false; }
  bool isRunning() const override { return running; }
  void loop() override { ++loops; }
  void sendPacket(mesh::Packet*) override { ++sent; }
};

class ESPNowBridge : public AbstractBridge {
  bool running = false;
public:
  unsigned begins = 0, ends = 0, fail_starts = 0;
  unsigned loops = 0, sent = 0;
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

#ifdef WITH_ESPNOW_BRIDGE
static bool millisHasNowPassed(uint32_t deadline) { return fake_millis >= deadline; }
#endif
struct FakeBoard { bool ota = false; bool isOTAUpdateRunning() const { return ota; } };
struct FakeCLI {
  FakeBoard board;
  FakeBoard* getBoard() { return &board; }
};

struct Callbacks {
  virtual ~Callbacks() = default;
  virtual bool isBridgeRunning() const = 0;
  virtual bool setBridgeState(bool) = 0;
  virtual bool restartBridge() = 0;
  virtual bool isRs232BridgeRunning() const = 0;
  virtual bool setRs232BridgeState(bool) = 0;
  virtual bool restartRs232Bridge() = 0;
  virtual void formatRs232Status(char*) = 0;
#ifdef WITH_ESPNOW_BRIDGE
  virtual bool isEspNowBridgeRunning() = 0;
  virtual bool setEspNowBridgeState(bool) = 0;
  virtual bool restartEspNowBridge() = 0;
#endif
};

class MyMesh : public Callbacks {
public:
  NodePrefs _prefs;
  RS232Bridge* bridge = nullptr;
  uint8_t active_rs232_bridge_uart = 0;
  FakeSensors sensors;
  FakeCLI _cli;
#ifdef WITH_ESPNOW_BRIDGE
  ESPNowBridge espnow_bridge;
  uint32_t shared_espnow_retry_at = 0;
#endif
  void* _mgr = nullptr;
  FakeRng rng;
  FakeRng* getRNG() { return &rng; }
  void* getRTCClock() { return nullptr; }
  void configureBridgeFilter(AbstractBridge*) {}
  void defaults() { @DEFAULTS@ }
  void boot() { @BOOT@ }
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
  ~MyMesh() override {
    assert(setBridgeState(false));
#ifdef WITH_ESPNOW_BRIDGE
    assert(setEspNowBridgeState(false));
#endif
  }
  @LIFECYCLE@
};

struct FakeFile {
  bool present;
  uint8_t value;
  int available() const { return present ? 1 : 0; }
  size_t read(uint8_t* out, size_t size) {
    assert(size == 1);
    if (!present) return 0;
    *out = value; present = false; return 1;
  }
};

@PARSERS@

class CommonCLI {
public:
  NodePrefs* _prefs;
  MyMesh* _callbacks;
  FakeSensors* _sensors;
  bool _com_prefs_needs_upgrade = false;
  unsigned saves = 0;
  NodePrefs stored;
  explicit CommonCLI(MyMesh& mesh)
      : _prefs(&mesh._prefs), _callbacks(&mesh), _sensors(&mesh.sensors) {}
  void savePrefs() { ++saves; stored = *_prefs; }
  bool trySavePrefs() { savePrefs(); return true; }
  void load(FakeFile file) {
    @INITIAL_UART@
    @LOAD_UART@
  }
  void set(const char* input, char* reply) {
    // Production dispatch receives a fixed-size command buffer.
    char config[256] = {};
    assert(strlen(input) < sizeof(config));
    strcpy(config, input);
    @SET_BRANCHES@
  }
  void get(const char* config, char* reply) { @GET_BRANCHES@ }
};

static void expect_get(CommonCLI& cli, const char* key, const char* expected) {
  char reply[256] = {};
  cli.get(key, reply);
  assert(strcmp(reply, expected) == 0);
}

static void check_upgrade(bool has_tail, uint8_t saved_uart, uint8_t enabled) {
  MyMesh mesh; mesh.defaults();
  mesh._prefs.bridge_enabled = enabled;
  CommonCLI cli(mesh);
  cli.load(FakeFile{has_tail, saved_uart});
#if defined(RS232_BRIDGE_MERGED) && !defined(RS232_BRIDGE_DEFAULT_ON)
  const bool normal_upgrade = !has_tail || saved_uart != 2;
#else
  const bool normal_upgrade = false;
#endif
  assert(mesh._prefs.bridge_enabled == (normal_upgrade ? 0 : enabled));
  assert(mesh._prefs.bridge_uart == 2);
  if (normal_upgrade || (has_tail && saved_uart != 2))
    assert(cli._com_prefs_needs_upgrade);
  const unsigned begins = Serial2.begins;
  mesh.boot();
  assert(mesh.isBridgeRunning() == (mesh._prefs.bridge_enabled != 0));
  assert(Serial2.begins == begins + (mesh._prefs.bridge_enabled ? 1 : 0));
  assert(mesh.sensors.yield_calls == 0);
}

int main() {
  {
    MyMesh mesh; mesh.defaults();
#if defined(RS232_BRIDGE_MERGED) && !defined(RS232_BRIDGE_DEFAULT_ON)
    assert(mesh._prefs.bridge_enabled == 0);
#else
    assert(mesh._prefs.bridge_enabled == 1);
#endif
    assert(mesh._prefs.bridge_uart == 2 && mesh._prefs.bridge_baud == 115200);
    const unsigned begins = Serial2.begins;
    mesh.boot();
    assert(Serial2.begins == begins + (mesh._prefs.bridge_enabled ? 1 : 0));
  }
  // Missing old tails and current normal-repeater zero sentinels fail safe.
  // Only a supported legacy UART proves intent; unsupported ports fail closed.
  for (uint8_t enabled : {uint8_t{0}, uint8_t{1}}) {
    check_upgrade(false, 0, enabled);
    check_upgrade(true, 0, enabled);
    check_upgrade(true, 1, enabled);
    check_upgrade(true, 2, enabled);
  }
  {
    MyMesh mesh; mesh.defaults(); mesh._prefs.bridge_enabled = 0;
    CommonCLI cli(mesh);
    char reply[256] = {};
#ifdef WITH_ESPNOW_BRIDGE
    expect_get(cli, "bridge.type", "> rs232+espnow");
#else
    expect_get(cli, "bridge.type", "> rs232");
#endif
    expect_get(cli, "bridge.enabled", "> off");
    expect_get(cli, "bridge.running", "> off");
    expect_get(cli, "bridge.uart", "> 2");
    const unsigned begins = Serial2.begins;
    assert(mesh.setBridgeState(false) && RS232Bridge::live == 0);
    cli.set("bridge.baud 57600", reply);
    assert(strcmp(reply, "OK") == 0 && cli.saves == 1);
    assert(Serial2.begins == begins && RS232Bridge::live == 0);
    expect_get(cli, "bridge.baud", "> 57600");
    cli.set("bridge.enabled on", reply);
    assert(strcmp(reply, "OK") == 0 && cli.saves == 2);
    assert(Serial2.running && RS232Bridge::live == 1);
    assert(Serial2.rx == 16 && Serial2.tx == 17 && Serial2.baud == 57600);
    assert(mesh.sensors.yield_calls == 0);
    mesh::Packet packet;
    mesh.routeRx(&packet); mesh.routeTx(&packet); mesh.serviceBridges();
    assert(mesh.bridge->sent == 1 && mesh.bridge->loops == 1);
    assert(mesh.bridgesPreventSleep());
    expect_get(cli, "bridge.enabled", "> on");
    expect_get(cli, "bridge.running", "> on");
    // Persistence reconstructs the same enabled runtime after reboot.
    {
      MyMesh restarted; restarted.defaults(); restarted._prefs = cli.stored;
      CommonCLI restart_cli(restarted);
      restart_cli.load(FakeFile{true, cli.stored.bridge_uart});
      restarted.boot();
      assert(restarted.isBridgeRunning() && restarted._prefs.bridge_baud == 57600);
    }
    cli.set("bridge.baud 38400", reply);
    assert(strcmp(reply, "OK") == 0 && Serial2.baud == 38400);
    const unsigned saves = cli.saves;
    for (const char* invalid : {"bridge.enabled onjunk", "bridge.uart 1",
         "bridge.uart 2junk", "bridge.baud 9599", "bridge.baud 115201",
         "bridge.baud 57600junk"}) {
      cli.set(invalid, reply);
      assert(strncmp(reply, "Error", 5) == 0 && cli.saves == saves);
      assert(mesh.isBridgeRunning() && mesh._prefs.bridge_baud == 38400);
      assert(mesh._prefs.bridge_uart == 2);
    }
    RS232Bridge::fail_starts = 1;
    cli.set("bridge.baud 19200", reply);
    assert(strstr(reply, "baud unchanged") != nullptr && cli.saves == saves);
    assert(mesh._prefs.bridge_baud == 38400 && mesh.isBridgeRunning());
    assert(Serial2.baud == 38400 && RS232Bridge::live == 1);
    cli.set("bridge.enabled off", reply);
    assert(strcmp(reply, "OK") == 0 && !mesh.isBridgeRunning());
    assert(RS232Bridge::live == 0 && !Serial2.running);
    const unsigned disabled_saves = cli.saves;
    RS232Bridge::fail_starts = 1;
    cli.set("bridge.enabled on", reply);
    assert(strstr(reply, "setting unchanged") != nullptr);
    assert(cli.saves == disabled_saves && mesh._prefs.bridge_enabled == 0);
    assert(!mesh.isBridgeRunning() && RS232Bridge::live == 0);
    // Repeated runtime switches must release the object and hardware UART.
    for (unsigned cycle = 0; cycle < 100; ++cycle) {
      cli.set("bridge.enabled on", reply);
      assert(strcmp(reply, "OK") == 0 && mesh.isBridgeRunning());
      cli.set("bridge.enabled off", reply);
      assert(strcmp(reply, "OK") == 0 && RS232Bridge::live == 0);
    }
    expect_get(cli, "bridge.enabled", "> off");
    expect_get(cli, "bridge.running", "> off");
    assert(mesh.sensors.yield_calls == 0);
  }
#ifdef WITH_ESPNOW_BRIDGE
  {
    MyMesh mesh; mesh.defaults();
    CommonCLI cli(mesh);
    char reply[256] = {};
    mesh::Packet packet;
    assert(mesh._prefs.espnow_bridge_enabled == 0);
    assert(!mesh.bridgesPreventSleep());
    expect_get(cli, "espnow.enabled", "> off");
    expect_get(cli, "espnow.running", "> off");
    const unsigned initial_uart_begins = Serial2.begins;
    cli.set("espnow.enabled on", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.isEspNowBridgeRunning());
    assert(!mesh.isBridgeRunning() && RS232Bridge::live == 0);
    assert(Serial2.begins == initial_uart_begins && mesh.bridgesPreventSleep());
    expect_get(cli, "bridge.running", "> off");
    expect_get(cli, "espnow.enabled", "> on");
    expect_get(cli, "espnow.running", "> on");
    mesh.routeRx(&packet); mesh.routeTx(&packet); mesh.serviceBridges();
    assert(mesh.espnow_bridge.sent == 1 && mesh.espnow_bridge.loops == 1);
    cli.set("bridge.enabled on", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.isBridgeRunning());
    assert(mesh.isEspNowBridgeRunning() && RS232Bridge::live == 1);
    const unsigned secondary_begins = mesh.espnow_bridge.begins;
    mesh.routeRx(&packet); mesh.serviceBridges();
    assert(mesh.bridge->sent == 1 && mesh.espnow_bridge.sent == 2);
    assert(mesh.bridge->loops == 1 && mesh.espnow_bridge.loops == 2);
    // UART changes and rollback must leave the secondary transport running.
    cli.set("bridge.baud 57600", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.espnow_bridge.begins == secondary_begins);
    RS232Bridge::fail_starts = 1;
    cli.set("bridge.baud 38400", reply);
    assert(strstr(reply, "baud unchanged") != nullptr && mesh.isBridgeRunning());
    assert(mesh._prefs.bridge_baud == 57600 && mesh.isEspNowBridgeRunning());
    assert(mesh.espnow_bridge.begins == secondary_begins);
    const unsigned primary_begins = Serial2.begins;
    cli.set("bridge.channel 6", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.espnow_bridge.begins == secondary_begins + 1);
    cli.set("bridge.secret wired-and-wireless", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.espnow_bridge.begins == secondary_begins + 2);
    cli.set("bridge.format raw", reply);
    assert(strncmp(reply, "OK", 2) == 0 && mesh.espnow_bridge.begins == secondary_begins + 3);
    assert(Serial2.begins == primary_begins && mesh.isBridgeRunning());
    expect_get(cli, "bridge.channel", "> 6");
    expect_get(cli, "bridge.secret", "> wired-and-wireless");
    expect_get(cli, "bridge.format", "> raw");
    const unsigned saves = cli.saves;
    for (const char* invalid : {"espnow.enabled onjunk", "bridge.channel 0",
         "bridge.channel 14", "bridge.channel 6junk", "bridge.secret ",
         "bridge.format unsupported"}) {
      cli.set(invalid, reply);
      assert(strncmp(reply, "Error", 5) == 0 && cli.saves == saves);
      assert(mesh.isBridgeRunning() && mesh.isEspNowBridgeRunning());
    }
    // TX-source mode feeds both transports once and ignores RX.
    mesh._prefs.bridge_pkt_src = 0;
    const unsigned primary_sent = mesh.bridge->sent;
    const unsigned secondary_sent = mesh.espnow_bridge.sent;
    mesh.routeRx(&packet); mesh.routeTx(&packet);
    assert(mesh.bridge->sent == primary_sent + 1);
    assert(mesh.espnow_bridge.sent == secondary_sent + 1);
    cli.set("bridge.enabled off", reply);
    assert(strcmp(reply, "OK") == 0 && RS232Bridge::live == 0);
    assert(mesh.isEspNowBridgeRunning() && mesh.bridgesPreventSleep());
    mesh.routeTx(&packet); // Secondary-only routing cannot dereference primary.
    assert(mesh.espnow_bridge.sent == secondary_sent + 2);
    cli.set("bridge.enabled on", reply);
    cli.set("espnow.enabled off", reply);
    assert(strcmp(reply, "OK") == 0 && mesh.isBridgeRunning());
    assert(!mesh.isEspNowBridgeRunning() && mesh.bridgesPreventSleep());
    const unsigned secondary_when_off = mesh.espnow_bridge.sent;
    mesh.routeTx(&packet); mesh.serviceBridges();
    assert(mesh.bridge->sent == 1 && mesh.bridge->loops == 1);
    assert(mesh.espnow_bridge.sent == secondary_when_off);
    // An OTA pause cannot be undone by a command or cooperative retry.
    mesh._cli.board.ota = true;
    const unsigned before_ota = mesh.espnow_bridge.begins;
    cli.set("espnow.enabled on", reply);
    assert(strstr(reply, "setting saved") != nullptr);
    assert(mesh._prefs.espnow_bridge_enabled == 1 && !mesh.isEspNowBridgeRunning());
    mesh.serviceBridges();
    assert(mesh.espnow_bridge.begins == before_ota && mesh.isBridgeRunning());
    mesh._cli.board.ota = false;
    mesh.serviceBridges();
    assert(mesh.isEspNowBridgeRunning() && mesh.espnow_bridge.begins == before_ota + 1);
    // Failed ESP-NOW startup is rate-limited, without stopping the UART bridge.
    cli.set("espnow.enabled off", reply);
    mesh.espnow_bridge.fail_starts = 2;
    cli.set("espnow.enabled on", reply);
    assert(strstr(reply, "setting saved") != nullptr && !mesh.isEspNowBridgeRunning());
    mesh.serviceBridges();
    const unsigned retry_begins = mesh.espnow_bridge.begins;
    mesh.serviceBridges();
    assert(mesh.espnow_bridge.begins == retry_begins && mesh.isBridgeRunning());
    fake_millis += 5001;
    mesh.serviceBridges();
    assert(mesh.isEspNowBridgeRunning() && mesh.espnow_bridge.begins == retry_begins + 1);
    // Independent switches repeatedly leave the other transport intact.
    for (unsigned cycle = 0; cycle < 100; ++cycle) {
      cli.set("bridge.enabled off", reply);
      assert(mesh.isEspNowBridgeRunning() && RS232Bridge::live == 0);
      cli.set("bridge.enabled on", reply);
      assert(mesh.isEspNowBridgeRunning() && mesh.isBridgeRunning());
      cli.set("espnow.enabled off", reply);
      assert(mesh.isBridgeRunning() && !mesh.isEspNowBridgeRunning());
      cli.set("espnow.enabled on", reply);
      assert(mesh.isBridgeRunning() && mesh.isEspNowBridgeRunning());
    }
    cli.set("bridge.enabled off", reply);
    cli.set("espnow.enabled off", reply);
    assert(!mesh.bridgesPreventSleep() && RS232Bridge::live == 0);
    assert(mesh.sensors.yield_calls == 0);
  }
#endif
  assert(RS232Bridge::live == 0);
  puts("RS232 runtime checks passed");
}
