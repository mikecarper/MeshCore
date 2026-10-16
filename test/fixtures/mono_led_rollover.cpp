// Full actual handlers/field initializers are injected. RGB is not configured.
// Arduino clock and output writes are the only hardware boundaries mocked.
#include <helpers/CompanionNotificationPolicy.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

@CONSTANTS@

static uint32_t clock_ms = 0;
uint32_t millis() { return clock_ms; }
static unsigned writes = 0;
static const char* active_case = "startup";
static unsigned checks = 0;
#define CHECK(value) do { \
  ++checks; \
  if (!(value)) { \
    std::fprintf(stderr, "FAIL %s: %s (line %d)\n", active_case, #value, __LINE__); \
    std::exit(1); \
  } \
} while (0)

#ifdef PIN_STATUS_LED
static unsigned last_level = 9;
void digitalWrite(unsigned pin, unsigned level) {
  CHECK(pin == PIN_STATUS_LED);
  CHECK(level <= 1);
  ++writes;
  last_level = level;
}
#endif

class UITask {
public:
  @DEFAULTS@
  @FIELDS@
  @SETTERS@
  int _msgcount = 0;
  void userLedHandler();
};

@HANDLER@

#ifdef PIN_STATUS_LED
static void at(UITask& ui, uint32_t now, unsigned count, bool on) {
  clock_ms = now;
  ui.userLedHandler();
  CHECK(writes == count);
  if (count) CHECK(last_level == unsigned(on ? LED_STATE_ON : !LED_STATE_ON));
}

static void cadence(UITask& ui, uint32_t start, uint32_t pulse) {
  at(ui, start, 1, true);
  at(ui, start, 1, true);
  at(ui, start + pulse, 1, true);
  at(ui, start + pulse + 1, 2, false);
  at(ui, start + pulse + 1, 2, false);
  at(ui, start + LED_CYCLE_MILLIS + 1, 2, false);
  at(ui, start + LED_CYCLE_MILLIS + 2, 3, true);
}

static void suppressed(UITask& ui, bool owner, bool initial_on) {
  constexpr uint32_t start = 100;
  constexpr uint32_t inactive = 26u * 24u * 60u * 60u * 1000u;
  at(ui, start, 1, true);
  if (!initial_on) at(ui, start + LED_ON_MILLIS + 1, 2, false);
  const unsigned before = writes;
  const uint32_t suppressed_at = clock_ms;
  if (owner) ui.setNotificationLedOwnership(true);
  else ui.setNotificationOutputs(uint8_t(31 & ~mesh::notify::Led));
  at(ui, suppressed_at + 1, before, initial_on);
  at(ui, suppressed_at + inactive, before, initial_on);
  if (owner) ui.setNotificationLedOwnership(false);
  else ui.setNotificationOutputs(31);
  at(ui, suppressed_at + inactive, before + 1, !initial_on);
  at(ui, suppressed_at + inactive, before + 1, !initial_on);
  const uint32_t phase = initial_on ? LED_CYCLE_MILLIS - LED_ON_MILLIS : LED_ON_MILLIS;
  at(ui, suppressed_at + inactive + phase, before + 1, !initial_on);
  at(ui, suppressed_at + inactive + phase + 1, before + 2, initial_on);
}
#endif

// A new process per case preserves real ui-orig function-static initialization.
int main(int argc, char** argv) {
  CHECK(argc == 2);
  active_case = argv[1];
  UITask ui;
  const char* name = argv[1];
#ifdef PIN_STATUS_LED
  if (!std::strcmp(name, "zero")) {
    at(ui, 0, 0, false);
    cadence(ui, 1, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "upper")) {
    cadence(ui, 0x80000000u, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "max")) {
    cadence(ui, UINT32_MAX, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "normal")) {
    cadence(ui, 100, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "unread")) {
    ui._msgcount = 1;
    cadence(ui, 100, LED_ON_MSG_MILLIS);
  } else if (!std::strcmp(name, "signed")) {
    cadence(ui, 0x7fffffffu - 10, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "wrap")) {
    cadence(ui, UINT32_MAX - 10, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "off-wrap")) {
    cadence(ui, UINT32_MAX - 100, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "zero-stamp")) {
    at(ui, uint32_t(0u - LED_ON_MILLIS - 1u), 1, true);
    at(ui, UINT32_MAX, 1, true);
    at(ui, 0, 2, false);
    at(ui, 0, 2, false);
    at(ui, LED_CYCLE_MILLIS - LED_ON_MILLIS, 2, false);
    at(ui, LED_CYCLE_MILLIS - LED_ON_MILLIS + 1, 3, true);
  } else if (!std::strcmp(name, "message-on")) {
    ui._msgcount = 2;
    at(ui, 100, 1, true);
    ui._msgcount = 0;
    at(ui, 120, 1, true);
    at(ui, 300, 1, true);
    at(ui, 301, 2, false);
    at(ui, 4101, 2, false);
    at(ui, 4102, 3, true);
    at(ui, 4122, 3, true);
    at(ui, 4123, 4, false);
  } else if (!std::strcmp(name, "message-off")) {
    at(ui, 100, 1, true);
    at(ui, 121, 2, false);
    ui._msgcount = 1;
    at(ui, 130, 2, false);
    at(ui, 4101, 2, false);
    at(ui, 4102, 3, true);
    at(ui, 4302, 3, true);
    at(ui, 4303, 4, false);
  } else if (!std::strcmp(name, "owner-fresh") || !std::strcmp(name, "mask-fresh")) {
    const bool owner = !std::strcmp(name, "owner-fresh");
    if (owner) ui.setNotificationLedOwnership(true);
    else ui.setNotificationOutputs(uint8_t(31 & ~mesh::notify::Led));
    at(ui, 0, 0, false);
    at(ui, 0x80000000u, 0, false);
    if (owner) ui.setNotificationLedOwnership(false);
    else ui.setNotificationOutputs(31);
    cadence(ui, 0x80000000u, LED_ON_MILLIS);
  } else if (!std::strcmp(name, "owner-on-long")) {
    suppressed(ui, true, true);
  } else if (!std::strcmp(name, "owner-off-long")) {
    suppressed(ui, true, false);
  } else if (!std::strcmp(name, "mask-on-long")) {
    suppressed(ui, false, true);
  } else if (!std::strcmp(name, "mask-off-long")) {
    suppressed(ui, false, false);
  } else if (!std::strcmp(name, "suppressed-short")) {
    at(ui, 100, 1, true);
    ui.setNotificationLedOwnership(true);
    at(ui, 105, 1, true);
    ui.setNotificationLedOwnership(false);
    at(ui, 110, 1, true);
    at(ui, 120, 1, true);
    at(ui, 121, 2, false);
  } else if (!std::strcmp(name, "delayed-one-phase")) {
    at(ui, 100, 1, true);
    at(ui, 100100, 2, false);
    at(ui, 100100, 2, false);
    at(ui, 104080, 2, false);
    at(ui, 104081, 3, true);
  } else if (!std::strcmp(name, "both-vetoes")) {
    ui.setNotificationLedOwnership(true);
    ui.setNotificationOutputs(0);
    at(ui, 100, 0, false);
    ui.setNotificationLedOwnership(false);
    at(ui, 200, 0, false);
    ui.setNotificationOutputs(31);
    at(ui, 200, 1, true);
  } else {
    std::fprintf(stderr, "unknown case: %s\n", name);
    return 2;
  }
#else
  CHECK(std::strcmp(name, "no-led") == 0);
  const uint32_t stamps[] = {0u, 1u, 0x7fffffffu, 0x80000000u, UINT32_MAX};
  for (uint32_t stamp : stamps) {
    clock_ms = stamp;
    ui.userLedHandler();
    CHECK(writes == 0);
    ui.setNotificationLedOwnership(true);
    ui.userLedHandler();
    CHECK(writes == 0);
    ui.setNotificationLedOwnership(false);
    ui.setNotificationOutputs(0);
    ui.userLedHandler();
    CHECK(writes == 0);
    ui.setNotificationOutputs(31);
  }
#endif
  std::printf("PASS %s: %u checks\n", name, checks);
}
