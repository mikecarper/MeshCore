#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>
#include "helpers/radiolib/RXPowerSaving.h"

struct Radio {
  bool supported = true;
  bool fallback = false;
  unsigned fallback_queries = 0;
  bool supportsRxPowerSaving() const { return supported; }
  bool isRxPowerSavingContinuousFallback() { ++fallback_queries; return fallback; }
  // No mutating radio APIs exist in this fixture: a write cannot compile.
} radio_driver;

struct Prefs {
  uint8_t rx_powersaving_enabled = 1;
  uint8_t rx_ps_level = 8;
  uint8_t rx_ps_preamble = 16;
  uint32_t rx_ps_rx_us = 11111;
  uint32_t rx_ps_sleep_us = 22222;
  uint8_t sf = 7;
  float bw = 62.5;
};

struct Output {
  std::string value;
  void printf(const char* format, ...) {
    char result[512];
    va_list args;
    va_start(args, format);
    const int count = vsnprintf(result, sizeof(result), format, args);
    va_end(args);
    assert(count >= 0 && static_cast<size_t>(count) < sizeof(result));
    value += result;
  }
};

struct MyMesh {
  Prefs _prefs;
  bool temporary = false;
  uint8_t _temp_radio_sf = 5;
  float _temp_radio_bw = 500;
  Output output;
  bool isTempRadioActive() const { return temporary; }
  bool handleRxPowerSavingGetCommand(const char*, char*, size_t) const;
  bool handleLocalControlCommand(const char*, char*, size_t);
  Output& terminalOutput() { return output; }
  void ascii(const char* command) {
    char local_reply[160];
#include "production_ascii.inc"
    assert(false && "Fixture expects one recognized ASCII RXPS getter");
  }
};

#include "production_getters.inc"

static std::string expected(const MyMesh& mesh, bool config) {
  if (!radio_driver.supported) return "Error: RX power saving is unsupported on this radio";
  const Prefs& prefs = mesh._prefs;
  uint32_t rx = prefs.rx_ps_rx_us, sleep = prefs.rx_ps_sleep_us;
  uint8_t level = 0, preamble = 0;
  if (!config && prefs.rx_ps_level != 0) {
    uint8_t sf = prefs.sf;
    float bw = prefs.bw;
#if COMPANION_FEATURE_TEMP_RADIO
    if (mesh.temporary) { sf = mesh._temp_radio_sf; bw = mesh._temp_radio_bw; }
#endif
    recalcRxPowerSavingFromLevel(prefs.rx_ps_level, sf, bw, prefs.rx_ps_preamble,
                                &rx, &sleep, &level, &preamble);
  }
  char result[256];
  snprintf(result, sizeof(result), "> %s,level=%u,preamble=%u,rx=%lu,sleep=%lu%s",
           prefs.rx_powersaving_enabled ? "on" : "off", unsigned(prefs.rx_ps_level),
           unsigned(prefs.rx_ps_preamble), static_cast<unsigned long>(rx),
           static_cast<unsigned long>(sleep),
           !config && radio_driver.fallback ? ",mode=continuous-fast" : "");
  std::string out = result;
  if (!config && level && level != prefs.rx_ps_level) out += ",effective-level=" + std::to_string(level);
  if (!config && preamble && preamble != prefs.rx_ps_preamble) out += ",effective-preamble=" + std::to_string(preamble);
  return out;
}

static void check(MyMesh& mesh, bool config, bool all_capacities) {
  const char* command = config ? "get radio.rxps.config" : "get radio.rxps";
  const std::string wanted = expected(mesh, config);
  const Prefs saved = mesh._prefs;
  const unsigned before = radio_driver.fallback_queries;
  for (size_t capacity = all_capacities ? 1 : 160; capacity <= 180; ++capacity) {
    std::vector<unsigned char> guarded(capacity + 16, 0xA7);
    char* reply = reinterpret_cast<char*>(guarded.data() + 8);
    assert(mesh.handleLocalControlCommand(command, reply, capacity));
    const size_t count = wanted.size() < capacity - 1 ? wanted.size() : capacity - 1;
    assert(strlen(reply) == count && !memcmp(reply, wanted.data(), count));
    for (size_t i = 0; i < 8; ++i) assert(guarded[i] == 0xA7);
    for (size_t i = 8 + capacity; i < guarded.size(); ++i) assert(guarded[i] == 0xA7);
    assert(memcmp(&mesh._prefs, &saved, sizeof(saved)) == 0);
  }
  if (config || !radio_driver.supported) assert(radio_driver.fallback_queries == before);
  mesh.output.value.clear();
  mesh.ascii(command);
  const std::string ascii = radio_driver.supported
      ? "  " + std::string(command + 4) + " " + wanted.substr(2) + "\r\n"
      : "  ERROR: RX power saving is unsupported on this radio\r\n";
  assert(mesh.output.value == ascii);
  assert(memcmp(&mesh._prefs, &saved, sizeof(saved)) == 0);
}

int main() {
  MyMesh mesh;
  for (uint8_t enabled = 0; enabled <= 1; ++enabled) {
    for (uint8_t level = 0; level <= 10; ++level) {
      for (uint8_t preamble : {uint8_t(0), uint8_t(16), uint8_t(32)}) {
        for (bool temporary : {false, true}) {
          mesh._prefs.rx_powersaving_enabled = enabled;
          mesh._prefs.rx_ps_level = level;
          mesh._prefs.rx_ps_preamble = preamble;
          mesh.temporary = temporary;
          radio_driver.fallback = temporary;
          check(mesh, false, level == 8);
          check(mesh, true, level == 8);
        }
      }
    }
  }
  // Config must return actual stored timings, never a recalculated pair. The
  // effective getter follows TempRadio only when compiled and currently active.
  mesh._prefs = Prefs();
  mesh.temporary = false;
  const std::string normal = expected(mesh, false);
  mesh.temporary = true;
  const std::string temporary = expected(mesh, false);
#if COMPANION_FEATURE_TEMP_RADIO
  assert(normal != temporary);
#else
  assert(normal == temporary);
#endif
  check(mesh, false, true);
  check(mesh, true, true);
  radio_driver.supported = false;
  check(mesh, false, true);
  check(mesh, true, true);
  radio_driver.supported = true;
  char reply[160];
  memset(reply, 'X', sizeof(reply));
  assert(!mesh.handleRxPowerSavingGetCommand(nullptr, reply, sizeof(reply)));
  assert(!mesh.handleRxPowerSavingGetCommand("get radio.rxps", nullptr, sizeof(reply)));
  assert(!mesh.handleRxPowerSavingGetCommand("get radio.rxps", reply, 0));
  for (const char* command : {"", "get radio.rxps ", "get radio.rxps.extra", "get radio.rxps.configx",
       "get radio.rxps.config extra", "set radio.rxps on", "GET radio.rxps"}) {
    assert(!mesh.handleLocalControlCommand(command, reply, sizeof(reply)));
    for (char byte : reply) assert(byte == 'X');
  }
  assert(mesh.handleLocalControlCommand("   get radio.rxps.config", reply, sizeof(reply)));
  assert(std::string(reply) == expected(mesh, true));
  puts("RXPS getter contracts passed");
}
