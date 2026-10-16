// Insert only actual production parsing and the four bounded CLI branches.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/CLICommandUtils.h>

#define BRIDGE_MAX_BAUD 115200

struct NodePrefs {
  uint8_t bridge_enabled = 0;
  uint8_t rs232_bridge_enabled = 0;
  uint32_t bridge_baud = 9600;
  uint8_t bridge_uart = 1;
  uint8_t espnow_bridge_enabled = 1;
  bool operator==(const NodePrefs& other) const {
    return bridge_enabled == other.bridge_enabled
        && rs232_bridge_enabled == other.rs232_bridge_enabled
        && bridge_baud == other.bridge_baud && bridge_uart == other.bridge_uart
        && espnow_bridge_enabled == other.espnow_bridge_enabled;
  }
};

struct Event {
  std::string kind;
  bool enabled;
  NodePrefs prefs;
};

struct Sensors {
  bool gps = false;
  bool conflict = false;
  bool yield = true;
  bool gpsUsesSerialUart(uint8_t) const { return gps; }
  bool gpsSerialTransportMayConflict(uint8_t) const { return conflict; }
  bool gpsSerialTransportCanYield(uint8_t) const { return yield; }
};

struct Callbacks {
  NodePrefs* prefs;
  std::vector<Event> events;
  std::vector<bool> responses;
  size_t response_index = 0;
  bool running = false;
  bool save_result = true;
  unsigned save_calls = 0;
  unsigned running_queries = 0;

  bool result(const char* kind, bool enabled) {
    events.push_back({kind, enabled, *prefs});
    assert(response_index < responses.size());
    const bool accepted = responses[response_index++];
    // A failed apply can have torn down its previous runtime owner. This
    // forces rollback rather than accidentally hiding that branch in tests.
    running = accepted && enabled;
    return accepted;
  }
  bool setRs232BridgeState(bool enabled) { return result("state", enabled); }
  bool restartRs232Bridge() { return result("restart", true); }
  bool isRs232BridgeRunning() { ++running_queries; return running; }
  bool save() {
    events.push_back({"save", false, *prefs});
    ++save_calls;
    return save_result;
  }
};

struct CommonCLI {
  NodePrefs* _prefs;
  Callbacks* _callbacks;
  Sensors* _sensors;
  bool trySavePrefs() { return _callbacks->save(); }
  void rs232(const char*, char*);
  void bridge(const char*, char*);
  void baud(const char*, char*);
  void uart(const char*, char*);
};

@PARSE_ON_OFF@
@PRODUCTION_BRANCHES@

enum class Branch { Rs232, Bridge, Baud, Uart };

static bool isEnable(Branch branch) {
  return branch == Branch::Rs232 || branch == Branch::Bridge;
}

static uint8_t& intent(NodePrefs& prefs) {
#ifdef WITH_MQTT_BRIDGE
  return prefs.rs232_bridge_enabled;
#else
  return prefs.bridge_enabled;
#endif
}

static NodePrefs initial(bool enabled) {
  NodePrefs prefs;
  intent(prefs) = enabled;
#ifdef WITH_MQTT_BRIDGE
  prefs.bridge_enabled = !enabled;  // Independent MQTT intent must survive.
#else
  prefs.rs232_bridge_enabled = enabled;
#endif
  return prefs;
}

static void run(CommonCLI& cli, Branch branch, bool enabled, char* reply,
                bool unchanged = false) {
  switch (branch) {
    case Branch::Rs232:
      cli.rs232(enabled ? "rs232.enabled on" : "rs232.enabled off", reply);
      break;
    case Branch::Bridge:
#ifndef WITH_MQTT_BRIDGE
      cli.bridge(enabled ? "bridge.enabled on" : "bridge.enabled off", reply);
#else
      assert(false && "standalone bridge branch is not an MQTT command");
#endif
      break;
    case Branch::Baud:
      cli.baud(unchanged ? "bridge.baud 9600" : "bridge.baud 19200", reply);
      break;
    case Branch::Uart:
      cli.uart(unchanged ? "bridge.uart 1" : "bridge.uart 2", reply);
      break;
  }
}

static NodePrefs candidateFor(NodePrefs before, Branch branch, bool enabled,
                              bool unchanged = false) {
  if (isEnable(branch)) intent(before) = enabled;
  else if (branch == Branch::Baud) before.bridge_baud = unchanged ? 9600 : 19200;
  else before.bridge_uart = unchanged ? 1 : 2;
  return before;
}

static const char* failedReply(Branch branch, bool applied, bool restored) {
  if (applied) {
    return restored ? "Error: save unconfirmed; reboot may differ"
                    : "Error: save unconfirmed; runtime restore failed; reboot may differ";
  }
  switch (branch) {
    case Branch::Rs232:
      return restored ? "Error: RS232 change failed; setting unchanged"
                      : "Error: RS232 change failed; previous runtime could not be restored";
    case Branch::Bridge:
      return restored ? "Error: bridge state change failed; setting unchanged"
                      : "Error: bridge state change failed; previous runtime state could not be restored";
    case Branch::Baud:
      return restored ? "Error: bridge failed to restart; baud unchanged"
                      : "Error: bridge failed to restart; previous baud could not be restored";
    case Branch::Uart:
      return restored ? "Error: bridge failed to restart; UART unchanged"
                      : "Error: bridge failed to restart; previous UART could not be restored";
  }
  assert(false);
  return "";
}

static unsigned cases = 0;

static void matrixCase(Branch branch, bool previous, bool applied, bool saved,
                       bool restored) {
  NodePrefs prefs = initial(previous);
  const NodePrefs before = prefs;
  const bool requested = !previous;
  const NodePrefs candidate = candidateFor(before, branch, requested);
  Callbacks callbacks{&prefs, {}, {applied, restored}};
  callbacks.running = previous;
  callbacks.save_result = saved;
  Sensors sensors;
  CommonCLI cli{&prefs, &callbacks, &sensors};
  char reply[160] = {};
  run(cli, branch, requested, reply);
  assert(callbacks.save_calls == (applied ? 1u : 0u));
  assert(callbacks.events[0].kind == (isEnable(branch) ? "state" : "restart"));
  assert(callbacks.events[0].prefs == candidate);
  if (isEnable(branch)) assert(callbacks.events[0].enabled == requested);
  if (applied) {
    assert(callbacks.events[1].kind == "save");
    assert(callbacks.events[1].prefs == candidate);
  }
  if (applied && saved) {
    assert(!strcmp(reply, "OK"));
    assert(prefs == candidate);
    assert(callbacks.events.size() == 2);
    assert(callbacks.response_index == 1);
  } else {
    assert(!strcmp(reply, failedReply(branch, applied, restored)));
    assert(prefs == before);
    assert(callbacks.events.size() == (applied ? 3u : 2u));
    const Event& rollback = callbacks.events.back();
    assert(rollback.kind == (isEnable(branch) ? "state" : "restart"));
    assert(rollback.prefs == before);
    assert(rollback.enabled == (isEnable(branch) ? previous : true));
    assert(callbacks.response_index == 2);
    if (applied) {
      assert(strstr(reply, "unconfirmed"));
      assert(strstr(reply, "reboot may differ"));
      assert(!strstr(reply, "unchanged"));
    } else {
      assert(!strstr(reply, "unconfirmed"));
    }
  }
  ++cases;
}

static void disabledSettingCase(Branch branch, bool saved, bool restored) {
  NodePrefs prefs = initial(false);
  const NodePrefs before = prefs;
  const NodePrefs candidate = candidateFor(before, branch, false);
  Callbacks callbacks{&prefs, {}, {restored}};
  callbacks.save_result = saved;
  Sensors sensors;
  CommonCLI cli{&prefs, &callbacks, &sensors};
  char reply[160] = {};
  run(cli, branch, false, reply);
  // A disabled bridge needs no runtime restart to apply UART/baud changes.
  assert(callbacks.save_calls == 1);
  assert(callbacks.events[0].kind == "save");
  assert(callbacks.events[0].prefs == candidate);
  if (saved) {
    assert(!strcmp(reply, "OK"));
    assert(prefs == candidate);
    assert(callbacks.events.size() == 1);
    assert(callbacks.response_index == 0);
  } else {
    assert(!strcmp(reply, failedReply(branch, true, restored)));
    assert(prefs == before);
    assert(callbacks.events.size() == 2);
    assert(callbacks.events[1].kind == "state");
    assert(!callbacks.events[1].enabled);
    assert(callbacks.events[1].prefs == before);
    assert(callbacks.response_index == 1);
  }
  ++cases;
}

static void healthyCase(Branch branch, bool previous, bool gps, bool unchanged) {
  NodePrefs prefs = initial(previous);
  const NodePrefs before = prefs;
  const bool requested = unchanged ? previous : !previous;
  const NodePrefs candidate = candidateFor(before, branch, requested, unchanged);
  const bool applies = isEnable(branch) || previous;
  Callbacks callbacks{&prefs, {}, {true}};
  callbacks.running = previous;
  Sensors sensors;
  sensors.gps = gps;
  CommonCLI cli{&prefs, &callbacks, &sensors};
  char reply[160] = {};
  run(cli, branch, requested, reply, unchanged);
  const bool paused = gps && ((isEnable(branch) && requested)
                              || (branch == Branch::Uart && previous));
  assert(!strcmp(reply, paused ? "OK - UART GPS paused" : "OK"));
  assert(prefs == candidate);
  assert(callbacks.save_calls == 1);
  assert(callbacks.events.size() == (applies ? 2u : 1u));
  assert(callbacks.response_index == (applies ? 1u : 0u));
  for (const Event& event : callbacks.events) assert(event.prefs == candidate);
  ++cases;
}

#ifndef WITH_MQTT_BRIDGE
static void alreadyRestoredCase() {
  NodePrefs prefs = initial(true);
  const NodePrefs before = prefs;
  Callbacks callbacks{&prefs, {}, {true}};
  callbacks.running = true;
  callbacks.save_result = false;
  Sensors sensors;
  CommonCLI cli{&prefs, &callbacks, &sensors};
  char reply[160] = {};
  // A successfully applied same-value ON leaves the old runtime running.
  // A refused disable also leaves it stopped in this adapter and must call
  // the restoration callback instead, as covered by matrixCase above.
  run(cli, Branch::Bridge, true, reply, true);
  assert(!strcmp(reply, "Error: save unconfirmed; reboot may differ"));
  assert(prefs == before);
  assert(callbacks.events.size() == 2);
  assert(callbacks.response_index == 1);
  assert(callbacks.running_queries == 1);
  ++cases;
}
#endif

int main() {
  const Branch branches[] = {Branch::Rs232,
#ifndef WITH_MQTT_BRIDGE
                            Branch::Bridge,
#endif
                            Branch::Baud, Branch::Uart};
  for (Branch branch : branches) {
    for (bool applied : {false, true}) {
      for (bool saved : {false, true}) {
        for (bool restored : {false, true}) {
          matrixCase(branch, true, applied, saved, restored);
          if (isEnable(branch)) matrixCase(branch, false, applied, saved, restored);
        }
      }
    }
    for (bool previous : {false, true}) {
      for (bool gps : {false, true}) {
        for (bool unchanged : {false, true}) {
          healthyCase(branch, previous, gps, unchanged);
        }
      }
    }
  }
  for (Branch branch : {Branch::Baud, Branch::Uart}) {
    for (bool saved : {false, true}) {
      for (bool restored : {false, true}) disabledSettingCase(branch, saved, restored);
    }
  }
#ifndef WITH_MQTT_BRIDGE
  alreadyRestoredCase();
#endif
  printf("%u actual CommonCLI apply/save/restore cases passed\n", cases);
}
