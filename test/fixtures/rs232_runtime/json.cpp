// Production JSON parser, bridge schema, and legacy import block are inserted
// by test_rs232_runtime.py. Only file access and save observation are faked.
#include <cassert>
#include <cstring>
#include <string>
#include <helpers/ConfigSerializer.h>
#include <helpers/bridges/RS232PowerSettings.h>

struct NodePrefs : ConfigSerializer {
  uint8_t bridge_enabled = 0, bridge_uart = 2, espnow_bridge_enabled = 0, rs232_bridge_enabled = 0;
  uint16_t bridge_delay = 500;
  uint8_t bridge_pkt_src = 1, bridge_channel = 1, bridge_format = 0;
  uint32_t bridge_baud = 115200;
  uint8_t usb_logging_enabled = 0, usb_debug_enabled = 0, usb_logging_always = 0;
  uint8_t rs232_autosleep = 1;
  uint8_t rs232_heartbeat = 1;
  uint16_t rs232_idle_seconds = 300;
  char bridge_secret[16] = "test-secret", node_name[32] = "before import";
  @BRIDGE_SCHEMA@
  BridgePrefs bridge;
  NodePrefs() : bridge(this) {}
  void structure() override {
    @NAME_SCHEMA@
    @BRIDGE_ROOT@
  }
};
struct File : Stream {
  std::string text;
  size_t pos = 0;
  bool open = true;
  explicit File(std::string value) : text(value) {}
  explicit operator bool() const { return open; }
  int available() override { return static_cast<int>(text.size() - pos); }
  int read() override { return pos < text.size() ? text[pos++] : -1; }
  int peek() override { return pos < text.size() ? text[pos] : -1; }
  void close() { open = false; }
};
struct FakeFS {
  std::string text;
  bool exists(const char*) const { return true; }
  File open(const char*, const char*) { return File(text); }
};
struct CLI {
  NodePrefs prefs;
  NodePrefs* _prefs = &prefs;
  int saves = 0;
  uint8_t saved_uart = 0, saved_enabled = 0, saved_secondary = 0;
  void savePrefs(FakeFS*) {
    ++saves; saved_uart = prefs.bridge_uart;
    saved_enabled = prefs.bridge_enabled; saved_secondary = prefs.espnow_bridge_enabled;
  }
  bool import(FakeFS* fs) {
    bool loaded = false, is_upgrade = false;
#ifdef WITH_MQTT_BRIDGE
    bool node_prefs_needs_migration = false;
#endif
    @IMPORT@
    assert(loaded == is_upgrade);
#ifdef WITH_MQTT_BRIDGE
    assert(node_prefs_needs_migration == loaded);
#endif
    return loaded;
  }
};

int main() {
  for (const char* json : {
      "{name:\"imported node\",bridge:{en:1}}",
      "{name:\"imported node\",bridge:{en:1,uart:0}}",
      "{name:\"imported node\",bridge:{en:1,uart:1}}",
      "{name:\"imported node\",bridge:{en:1,uart:2}}",
      "{name:\"imported node\",bridge:{en:0,uart:2}}",
      "{name:\"imported node\",bridge:{en:1,uart:2,rs232:1}}",
  }) {
    CLI cli;
    cli.prefs.espnow_bridge_enabled = 1; // An old unused flag cannot prove intent.
    cli.prefs.rs232_bridge_enabled = 1; // Reused objects cannot donate UART intent.
    FakeFS fs{json};
    assert(cli.import(&fs));
#ifdef WITH_MQTT_BRIDGE
    assert(cli.saves == 0);
#else
    assert(cli.saves == 1);
#endif
    assert(strcmp(cli.prefs.node_name, "imported node") == 0);
#if defined(ESPNOW_BRIDGE_MERGED) && !defined(ESPNOW_BRIDGE_DEFAULT_ON) \
    && !defined(WITH_RS232_BRIDGE) && !defined(WITH_MQTT_BRIDGE)
    assert(cli.prefs.bridge_enabled == 0);
#elif defined(WITH_MQTT_BRIDGE) && defined(WITH_RS232_BRIDGE)
    assert(cli.prefs.bridge_enabled == (strstr(json, "en:1") != nullptr));
    assert(cli.prefs.rs232_bridge_enabled == 0 && cli.prefs.bridge_uart == 2);
#elif defined(RS232_BRIDGE_MERGED) && !defined(RS232_BRIDGE_DEFAULT_ON)
    const bool explicit_uart = strstr(json, "uart:2");
    const bool enabled = explicit_uart && strstr(json, "en:1");
    assert(cli.prefs.bridge_enabled == enabled && cli.prefs.bridge_uart == 2);
#else
    assert(cli.prefs.bridge_enabled == (strstr(json, "en:1") != nullptr));
    assert(cli.prefs.bridge_uart == (strstr(json, "uart:0") ? 0
        : strstr(json, "uart:1") ? 1 : 2));
#endif
#if defined(ESPNOW_BRIDGE_MERGED) && !defined(ESPNOW_BRIDGE_DEFAULT_ON)
    assert(cli.prefs.espnow_bridge_enabled == 0);
#elif defined(ESPNOW_BRIDGE_MERGED) && !defined(WITH_RS232_BRIDGE) && !defined(WITH_MQTT_BRIDGE)
    assert(cli.prefs.espnow_bridge_enabled == cli.prefs.bridge_enabled);
#else
    assert(cli.prefs.espnow_bridge_enabled == 1);
#endif
#ifndef WITH_MQTT_BRIDGE
    assert(cli.saved_uart == cli.prefs.bridge_uart && cli.saved_enabled == cli.prefs.bridge_enabled);
    assert(cli.saved_secondary == cli.prefs.espnow_bridge_enabled);
#endif
  }
  // Malformed JSON may partially apply scalar fields before the parser rejects it.
  CLI broken;
  broken.prefs.espnow_bridge_enabled = 1;
  FakeFS malformed{"{bridge:{en:1,uart:1}"};
  assert(!broken.import(&malformed) && broken.saves == 0);
#if defined(RS232_BRIDGE_MERGED) && !defined(RS232_BRIDGE_DEFAULT_ON)
  assert(broken.prefs.bridge_enabled == 0 && broken.prefs.bridge_uart == 2);
#endif
#if defined(ESPNOW_BRIDGE_MERGED) && !defined(ESPNOW_BRIDGE_DEFAULT_ON)
  assert(broken.prefs.espnow_bridge_enabled == 1);
#if !defined(WITH_RS232_BRIDGE) && !defined(WITH_MQTT_BRIDGE)
  assert(broken.prefs.bridge_enabled == 0);
#endif
#endif
}
