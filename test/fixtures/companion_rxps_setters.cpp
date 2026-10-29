#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <string>
#include <vector>
#include <helpers/CLICommandUtils.h>
#include <helpers/CompanionHardwareCommandCompat.h>
#include <helpers/radiolib/RXPowerSaving.h>
#include "usb_mode_console.h"

namespace mesh {enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};}
struct Prefs {
  uint8_t rx_powersaving_enabled = 0, rx_ps_level = 8, rx_ps_preamble = 16, sf = 7;
  uint32_t rx_ps_rx_us = 3000, rx_ps_sleep_us = 4000;
  float bw = 125;
};
static bool same(const Prefs& a, const Prefs& b) {
  return a.rx_powersaving_enabled == b.rx_powersaving_enabled
      && a.rx_ps_level == b.rx_ps_level && a.rx_ps_preamble == b.rx_ps_preamble
      && a.rx_ps_rx_us == b.rx_ps_rx_us && a.rx_ps_sleep_us == b.rx_ps_sleep_us
      && a.sf == b.sf && a.bw == b.bw;
}
struct Driver {
  bool supported = true, enabled = false;
  unsigned calls = 0;
  uint32_t rx = 3000, sleep = 4000;
  std::deque<bool> results;
  mesh::RadioParamApplyResult trySetRxPowerSaving(bool enable,uint32_t rx,uint32_t sleep) {
    return setRxPowerSaving(enable,rx,sleep)?mesh::RadioParamApplyResult::APPLIED:mesh::RadioParamApplyResult::FAILED;
  }
  bool supportsRxPowerSaving() const { return supported; }
  bool setRxPowerSaving(bool enable, uint32_t receive, uint32_t rest) {
    ++calls;
    const bool accepted = results.empty() ? true : results.front();
    if (!results.empty()) results.pop_front();
    if (accepted) { enabled = enable; rx = receive; sleep = rest; }
    return accepted;
  }
} radio_driver;
struct BaseSerialInterface {};
struct SerialTransport {
  BaseSerialInterface requester, other;
  BaseSerialInterface* current = &requester;
  BaseSerialInterface* reply_route = nullptr;
  std::vector<uint8_t> reply;
  BaseSerialInterface* captureReplyRoute() { return current; }
  size_t writeFrameToRoute(BaseSerialInterface* route, const uint8_t* data, size_t size) {
    reply_route = route;
    reply.assign(data, data + size);
    return size;
  }
};
struct MyMesh {
  Prefs _prefs, durable;
  SerialTransport serial;
  SerialTransport* _serial = &serial;
  bool _radio_available = true, storage_accepts = true, saved_radio_apply_pending = false;
  bool _primary_radio_blocked=false;
  unsigned saves = 0;
  uint32_t radio_apply_retry_at = 0;
  uint8_t radio_apply_failures = 0;
  uint8_t cmd_frame[180] = {}, out_frame[180] = {};
  char reply_buf[160] = {};
  MyMesh() { radio_driver = Driver(); }
  bool savePrefs() { ++saves; if (!storage_accepts) return false; durable = _prefs; return true; }
  void queueSavedRadioApply();
  bool applyAndSaveRxPowerSaving(const char*, char*);
  bool handleLocalControlCommand(const char*, char*, size_t);
  bool handleCommand(const char*, uint32_t, char*);
  void frame(size_t len) { @FRAME@ }
  void beginUsbLoggingReplyBarrier(BaseSerialInterface* route) {
    assert(route == &serial.requester);
    // Other transport activity must not steal the response after capture.
    serial.current = &serial.other;
  }
  void endUsbLoggingReplyBarrier(bool queued) { assert(queued); }
  void writeErrFrame(uint8_t) { assert(false && "Expected a textual CLI reply"); }
  std::string run(const char* command, bool nul = true) {
    const size_t size = strlen(command);
    assert(size + 2 < sizeof(cmd_frame));
    cmd_frame[0] = mesh::companion::kRunCliCommand;
    memcpy(cmd_frame + 1, command, size);
    if (nul) cmd_frame[size + 1] = 0;
    frame(size + 1 + unsigned(nul));
    assert(serial.reply_route == &serial.requester);
    assert(!serial.reply.empty() && serial.reply[0] == RESP_CODE_CLI_REPLY);
    return std::string(serial.reply.begin() + 1, serial.reply.end());
  }
};
@METHODS@

static void configure(MyMesh& node, unsigned fault) {
  if (fault == 1) radio_driver.supported = false;
  if (fault == 2) radio_driver.results = {false};
  if (fault == 3) node.storage_accepts = false;
}
static bool error(const std::string& reply) {
  return reply.find("Error") != std::string::npos || reply.find("ERROR") != std::string::npos;
}
int main() {
  for (bool nul : {false, true}) {
    for (const char* value : {"off", "on", "level 3 preamble 16", "level 8 preamble 32", "65625 60000"}) {
      MyMesh node;
      const std::string command = std::string("  A7| \tset radio.rxps \t") + value;
      const std::string reply = node.run(command.c_str(), nul);
      assert(reply.find("A7|OK - ") == 0 && node.saves == 1 && radio_driver.calls == 1);
      assert(same(node._prefs, node.durable));
      assert(radio_driver.enabled == bool(node.durable.rx_powersaving_enabled));
      assert(radio_driver.rx == node.durable.rx_ps_rx_us && radio_driver.sleep == node.durable.rx_ps_sleep_us);
      if (!strcmp(value, "on")) assert(node.durable.rx_ps_level == 3);
      if (!strcmp(value, "off")) assert(!node.durable.rx_powersaving_enabled);
      if (!strcmp(value, "level 3 preamble 16"))
        assert(node.durable.rx_ps_level == 3 && node.durable.rx_ps_preamble == 16);
      if (!strcmp(value, "65625 60000"))
        assert(node.durable.rx_ps_level == 0 && node.durable.rx_ps_rx_us == 65625 && node.durable.rx_ps_sleep_us == 60000);
    }
  }
  for (const char* command : {"set radio.rxps", "set radio.rxps on extra", "set radio.rxps level 0",
       "set radio.rxps level 3 preamble 17", "set radio.rxps 1000 -1"}) {
    MyMesh node;
    const std::string reply = node.run(command);
    assert(error(reply) && reply.find("Unknown") == std::string::npos);
    assert(node.saves == 0 && radio_driver.calls == 0 && same(node._prefs, Prefs()));
  }
  for (unsigned fault : {1u, 2u, 3u}) {
    MyMesh node;
    configure(node, fault);
    const std::string reply = node.run("B2|set radio.rxps level 3 preamble 16");
    assert(reply.find("B2|") == 0 && error(reply) && reply.find("Unknown") == std::string::npos);
    assert(same(node._prefs, Prefs()) && same(node.durable, Prefs()));
    assert(node.saves == unsigned(fault == 3));
    assert(radio_driver.calls == (fault == 1 ? 0u : fault == 2 ? 1u : 2u));
    assert(!radio_driver.enabled && radio_driver.rx == 3000 && radio_driver.sleep == 4000);
  }
  {
    MyMesh node;
    node.storage_accepts = false;
    radio_driver.results = {true, false};
    assert(error(node.run("set radio.rxps level 3 preamble 16")));
    assert(node.saved_radio_apply_pending && same(node._prefs, Prefs()) && same(node.durable, Prefs()));
  }
  // Every shared-route response remains bounded, including errors from the
  // original fixed-size setter. A zero-capacity call must have no side effects.
  for (unsigned fault : {0u, 1u, 2u, 3u, 4u}) {
    const char* command = fault == 4 ? "set radio.rxps level 3 preamble 17"
                                    : "set radio.rxps level 3 preamble 16";
    MyMesh reference;
    configure(reference, fault);
    char full[160];
    assert(reference.handleLocalControlCommand(command, full, sizeof(full)));
    const std::string expected = full;
    for (size_t capacity = 0; capacity <= 180; ++capacity) {
      MyMesh node;
      configure(node, fault);
      std::vector<unsigned char> bytes(capacity + 16, 0xA7);
      char* reply = reinterpret_cast<char*>(bytes.data() + 8);
      assert(node.handleLocalControlCommand(command, reply, capacity) == (capacity != 0));
      if (capacity) {
        const size_t count = std::min(expected.size(), capacity - 1);
        assert(strlen(reply) == count && !memcmp(reply, expected.data(), count));
      } else assert(node.saves == 0 && radio_driver.calls == 0);
      for (size_t i = 0; i < 8; ++i) assert(bytes[i] == 0xA7);
      for (size_t i = 8 + capacity; i < bytes.size(); ++i) assert(bytes[i] == 0xA7);
    }
  }
  for (const char* command : {"set radio.rxpsx on", "set radio.rxps.status on", "set radio.rxps.rfrx_disabled on"}) {
    MyMesh node;
    const std::string reply = node.run(command);
    assert(reply == "Unknown command" && node.saves == 0 && radio_driver.calls == 0);
  }
  puts("RXPS framed setter and bounded shared-route contracts passed");
}
