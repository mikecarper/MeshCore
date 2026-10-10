#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

// The unknown-setting branches below are extracted verbatim from production.
// The hardware-dependent recognized-setting branches are outside this fixture.
using Fallback = void (*)(const char*, char*);
static void commonFallback(const char* command, char* reply) {
  @COMMON_CONFIG@
  (void)config;
  if (false) {}
  @COMMON_FALLBACK@
}
static void observerFallback(const char* command, char* reply) {
  @COMMON_CONFIG@
  (void)config;
  if (false) {}
  @OBSERVER_FALLBACK@
}

static std::string unknownCommand(bool observer, size_t command_length) {
  const char* prefix = observer ? "set mqtt1." : "set repeat.";
  assert(command_length >= strlen(prefix));
  return std::string(prefix) + std::string(command_length - strlen(prefix), 'x');
}

template <size_t Capacity>
static void exact(Fallback fallback, const std::string& command) {
  // Standalone arrays expose any past-end write to AddressSanitizer rather
  // than hiding it inside an allocation which includes the path/canary.
  std::array<char, Capacity> reply;
  reply.fill(char(0x55));
  fallback(command.c_str(), reply.data());
  assert(strcmp(reply.data(), "unknown config") == 0);
  for (size_t i = sizeof("unknown config"); i < reply.size(); ++i)
    assert(reply[i] == char(0x55));
}

template <size_t Capacity>
static void adjacent(Fallback fallback, const std::string& command) {
  struct Buffers {
    uint8_t before[16];
    char reply[Capacity];
    uint8_t reply_path[64];
  } buffers;
  memset(&buffers, 0x55, sizeof(buffers));
  fallback(command.c_str(), buffers.reply);
  assert(strcmp(buffers.reply, "unknown config") == 0);
  for (uint8_t value : buffers.before) assert(value == 0x55);
  for (uint8_t value : buffers.reply_path) assert(value == 0x55);
  for (size_t i = sizeof("unknown config"); i < sizeof(buffers.reply); ++i)
    assert(buffers.reply[i] == char(0x55));
}

int main(int argc, char** argv) {
  assert(argc == 3);
  const bool observer = strcmp(argv[1], "observer") == 0;
  const std::string scenario(argv[2]);
  Fallback fallback = observer ? observerFallback : commonFallback;
  if (scenario == "local") {
    const auto command = unknownCommand(observer, 159);
    exact<160>(fallback, command);
    adjacent<160>(fallback, command);
  } else if (scenario == "radio") {
    const auto command = unknownCommand(observer, 179);
    exact<179>(fallback, command);
    adjacent<179>(fallback, command);
  } else if (scenario == "minimum") {
    const auto command = unknownCommand(observer, 179);
    exact<sizeof("unknown config")>(fallback, command);
    adjacent<sizeof("unknown config")>(fallback, command);
  } else if (scenario == "lengths") {
    for (size_t length = 16; length < 4096; ++length) {
      const auto command = unknownCommand(observer, length);
      exact<160>(fallback, command);
      exact<179>(fallback, command);
      exact<sizeof("unknown config")>(fallback, command);
      adjacent<179>(fallback, command);
    }
  } else assert(false);
  return 0;
}
