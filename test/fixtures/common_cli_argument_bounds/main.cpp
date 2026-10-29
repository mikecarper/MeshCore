#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <string>
#include <helpers/CLICommandUtils.h>
#include <helpers/GpsPowerPolicy.h>

@CONSTANTS@

namespace mesh {
struct Utils {
  static int parseTextParts(char*, const char*[], int, char = ',');
  static void toHex(char*, const uint8_t*, size_t);
  static bool isHexChar(char);
  static bool fromHex(uint8_t*, int, const char*);
};
@UTILS@
}

@PARSERS@

struct Prefs {
  uint32_t gps_interval = 7;
  uint8_t gps_enabled = 0, bridge_uart = 1, direct_retry_cr_enabled = 0;
  int8_t direct_retry_cr4_snr_x4 = 40, direct_retry_cr5_snr_x4 = 30;
  int8_t direct_retry_cr7_snr_x4 = 0, direct_retry_cr8_snr_x4 = -10;
};
struct Sensors {
  unsigned calls = 0;
  std::string key, value;
  bool setSettingValue(const char* k, const char* v) {
    ++calls; key = k; value = v; return true;
  }
  bool gpsUsesSerialUart(uint8_t) { return true; }
};
struct Callbacks {
  bool advanced = true, bridge = false;
  unsigned support_calls = 0, recent_calls = 0;
  std::array<uint8_t, 3> prefix{};
  uint8_t prefix_len = 0;
  int8_t snr = 0;
  bool supportsAdvancedRetryConfig() { ++support_calls; return advanced; }
  bool isRs232BridgeRunning() { return bridge; }
  bool setRecentRepeater(const uint8_t* p, uint8_t n, int8_t s) {
    ++recent_calls; memcpy(prefix.data(), p, n); prefix_len = n; snr = s; return true;
  }
};
struct CommonCLI {
  // The production branches see the real array capacity. Keeping the backing
  // array external lets ASan detect its exact end independently of other fields.
  char (&tmp)[COMMON_CLI_TMP_LEN];
  Prefs prefs;
  Prefs* _prefs = &prefs;
  Sensors sensors;
  Sensors* _sensors = &sensors;
  Callbacks callbacks;
  Callbacks* _callbacks = &callbacks;
  unsigned saves = 0;
  explicit CommonCLI(char (&scratch)[COMMON_CLI_TMP_LEN]) : tmp(scratch) {}
  void savePrefs() { ++saves; }
  void run(const char* command, char* reply) {
    const char* config = command;
    @SENSOR_BRANCH@
    else @RETRY_BRANCH@
    else @RECENT_BRANCH@
    else assert(false);
  }
};

static std::string argument(const std::string& kind, size_t size) {
  const std::string base = kind == "sensor" ? "custom " :
                           kind == "gps" ? "gps_interval " :
                           kind == "retry" ? "0,0,0," : "AABBCC ";
  assert(size >= base.size());
  return base + std::string(size - base.size(), kind == "sensor" ? 'x' : '0');
}
static std::string command(const std::string& kind, const std::string& args) {
  return (kind == "sensor" || kind == "gps" ? "sensor set " :
          kind == "retry" ? "direct.retry.cr " : "recent.repeater ") + args;
}
static void untouched(const CommonCLI& cli) {
  assert(cli.saves == 0 && cli.sensors.calls == 0);
  assert(cli.callbacks.support_calls == 0 && cli.callbacks.recent_calls == 0);
  assert(cli.prefs.gps_interval == 7 && cli.prefs.gps_enabled == 0);
  assert(cli.prefs.direct_retry_cr_enabled == 0);
  assert(cli.prefs.direct_retry_cr4_snr_x4 == 40 && cli.prefs.direct_retry_cr5_snr_x4 == 30);
  assert(cli.prefs.direct_retry_cr7_snr_x4 == 0 && cli.prefs.direct_retry_cr8_snr_x4 == -10);
}
static void exercise(const std::string& kind, size_t size, bool advanced, bool adjacent) {
  char exact[COMMON_CLI_TMP_LEN];
  struct Buffers {
    uint8_t before[16];
    char scratch[COMMON_CLI_TMP_LEN];
    uint8_t after[16];
  } buffers;
  memset(exact, 0x55, sizeof(exact));
  memset(&buffers, 0x55, sizeof(buffers));
  CommonCLI cli(adjacent ? buffers.scratch : exact);
  cli.callbacks.advanced = advanced;
  std::array<char, 160> reply;
  reply.fill(char(0x55));
  const auto args = argument(kind, size);
  const auto input = command(kind, args);
  cli.run(input.c_str(), reply.data());
  if (size >= COMMON_CLI_TMP_LEN) {
    assert(strcmp(reply.data(), MESH_CLI_REPLY_TEXT("Error, argument too long", "Error, arg too long")) == 0);
    untouched(cli);
    for (char value : cli.tmp) assert(value == char(0x55));
  } else if (kind == "sensor" || kind == "gps") {
    assert(strcmp(reply.data(), "ok") == 0 && cli.sensors.calls == 1);
    assert(cli.sensors.value == args.substr(args.find(' ') + 1));
    assert(cli.saves == (kind == "gps" ? 1U : 0U));
    if (kind == "gps") assert(cli.prefs.gps_interval == 0);
  } else if (kind == "retry") {
    assert(cli.callbacks.support_calls == 1);
    assert(cli.saves == (advanced ? 1U : 0U));
    assert(strcmp(reply.data(), advanced ? "OK" : MESH_CLI_REPLY_TEXT("Error, use on or off on this role", "Error, on|off")) == 0);
    if (advanced) {
      assert(cli.prefs.direct_retry_cr_enabled == 1);
      assert(cli.prefs.direct_retry_cr4_snr_x4 == 0 && cli.prefs.direct_retry_cr5_snr_x4 == 0);
      assert(cli.prefs.direct_retry_cr7_snr_x4 == 0 && cli.prefs.direct_retry_cr8_snr_x4 == 0);
    }
  } else {
    assert(strncmp(reply.data(), "OK - set AABBCC", 15) == 0);
    assert(cli.callbacks.recent_calls == 1 && cli.callbacks.prefix_len == 3);
    assert((cli.callbacks.prefix == std::array<uint8_t, 3>{0xAA, 0xBB, 0xCC}));
    assert(cli.callbacks.snr == 0);
  }
  for (size_t i = strlen(reply.data()) + 1; i < reply.size(); ++i)
    assert(reply[i] == char(0x55));
  for (uint8_t value : buffers.before) assert(value == 0x55);
  for (uint8_t value : buffers.after) assert(value == 0x55);
}
static void unchangedModes() {
  char scratch[COMMON_CLI_TMP_LEN];
  char reply[160];
  CommonCLI cli(scratch);
  for (bool advanced : {false, true}) {
    cli.callbacks.advanced = advanced;
    cli.run("direct.retry.cr off", reply);
    assert(strcmp(reply, "OK") == 0 && cli.prefs.direct_retry_cr_enabled == 0);
    if (!advanced) {
      cli.run("direct.retry.cr on", reply);
      assert(strcmp(reply, "OK") == 0 && cli.prefs.direct_retry_cr_enabled == 1);
    }
  }
  cli.run("sensor set gps 1", reply);
  assert(strcmp(reply, "ok") == 0 && cli.prefs.gps_enabled == 1);
  cli.run("recent.repeater AA", reply);
  assert(cli.callbacks.prefix_len == 1 && cli.callbacks.snr == 12);
}
int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario(argv[1]);
  if (scenario != "production") {
    exercise(scenario, 179, true, false);
    return 0;
  }
  // Exercise the helper itself using exact arrays, including the terminator.
  char one[1] = {'x'};
  assert(!copyCLIArgument(one, 0, "") && one[0] == 'x');
  assert(copyCLIArgument(one, sizeof(one), "") && one[0] == 0);
  assert(!copyCLIArgument(one, sizeof(one), "x") && one[0] == 0);
  for (const std::string kind : {"sensor", "gps", "retry", "recent"}) {
    for (bool adjacent : {false, true}) {
      for (bool advanced : {false, true}) {
        for (size_t size : {size_t(COMMON_CLI_TMP_LEN - 1), size_t(COMMON_CLI_TMP_LEN),
                            size_t(159) - command(kind, "").size(),
                            size_t(179) - command(kind, "").size(), size_t(4096)})
          exercise(kind, size, advanced, adjacent);
      }
    }
  }
  unchangedModes();
  puts("PASS: production CLI argument bounds");
}
