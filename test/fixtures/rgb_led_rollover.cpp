// Full actual handler, output enum, ownership setters and RGB writers are injected.
// The clock is uint32_t even on hosts where unsigned long has 64 bits.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace mesh { namespace notify {
@OUTPUT_ENUM@
} }

@CONSTANTS@

static uint32_t clock_ms = 0;
uint32_t millis() { return clock_ms; }
static unsigned writes = 0, batt_reads = 0, power_reads = 0, charge_reads = 0;
static uint32_t batt_delay = 0, power_delay = 0;
#ifdef EXT_CHRG_DETECT
static uint32_t charge_delay = 0;
#endif
static const char* active_case = "startup";
static unsigned checks = 0;
#define CHECK(value) do { \
  ++checks; \
  if (!(value)) { \
    std::fprintf(stderr, "FAIL %s: %s (line %d)\n", active_case, #value, __LINE__); \
    std::exit(1); \
  } \
} while (0)

struct Board {
  uint16_t battery = 4000;
  bool external = false;
  uint16_t getBattMilliVolts() {
    ++batt_reads;
    clock_ms += batt_delay;
    return battery;
  }
  bool isExternalPowered() {
    ++power_reads;
    clock_ms += power_delay;
    return external;
  }
};

#ifdef STATUS_LED_RGB
static unsigned red = 0, green = 0, blue = 0, channel = 0;
void analogWrite(unsigned pin, unsigned level) {
  CHECK(level <= 255);
  if (channel == 0) { CHECK(pin == PIN_STATUS_LED_R); red = level; }
  else if (channel == 1) { CHECK(pin == PIN_STATUS_LED_G); green = level; }
  else { CHECK(pin == PIN_STATUS_LED_B); blue = level; ++writes; }
  channel = (channel + 1) % 3;
}
@WRITERS@
#endif

#ifdef EXT_CHRG_DETECT
constexpr unsigned LOW = 0;
static unsigned charge_level = 1;
int digitalRead(unsigned pin) {
  CHECK(pin == EXT_CHRG_DETECT);
  ++charge_reads;
  clock_ms += charge_delay;
  return int(charge_level);
}
#endif

class UITask {
public:
  @DEFAULTS@
  @SETTERS@
  int _msgcount = 0;
  bool _connected = false;
  Board board;
  Board* _board = &board;
  void userLedHandler();
};

@HANDLER@

static void tick(UITask& ui, uint32_t stamp) {
  clock_ms = stamp;
  ui.userLedHandler();
}

#ifdef STATUS_LED_RGB
static void color(unsigned r, unsigned g, unsigned b) {
  CHECK(channel == 0);
  CHECK(red == r);
  CHECK(green == g);
  CHECK(blue == b);
}

static uint32_t boot(UITask& ui, uint32_t start = 100) {
  const uint32_t duration = ui._msgcount > 0 ? 800 : 1200;
  tick(ui, start);
  CHECK(writes == 1);
  CHECK(batt_reads == 0);
  CHECK(power_reads == 0);
  tick(ui, start + duration);
  CHECK(writes == 2);
  color(0, 0, 0);
  if (start + duration != 0) {
    CHECK(batt_reads == 1);
#ifdef EXT_CHRG_DETECT
    CHECK(power_reads == 1);
#endif
  }
  return start + duration;
}

static uint32_t heartbeat(UITask& ui, uint32_t start = 100) {
  const uint32_t end = boot(ui, start);
  tick(ui, end + 1);
  CHECK(writes == 3);
  return end + 1;
}

static void veto(UITask& ui, bool owner, bool enabled) {
  if (owner) ui.setNotificationLedOwnership(enabled);
  else ui.setNotificationOutputs(enabled ? uint8_t(31 & ~mesh::notify::Led) : 31);
}

static void suppressLong(UITask& ui, bool owner, bool on) {
  uint32_t stamp = heartbeat(ui);
  if (!on) { stamp += 21; tick(ui, stamp); }
  const unsigned before = writes, battery_before = batt_reads, power_before = power_reads;
  veto(ui, owner, true);
  constexpr uint32_t idle = 26u * 24u * 60u * 60u * 1000u;
  tick(ui, stamp + idle);
  CHECK(writes == before);
  CHECK(batt_reads == battery_before);
  CHECK(power_reads == power_before);
  veto(ui, owner, false);
  tick(ui, stamp + idle);
  CHECK(writes == before + 1);
  if (on) color(0, 0, 0); else color(0, 255, 0);
  CHECK(batt_reads == battery_before + 1);
#ifdef EXT_CHRG_DETECT
  CHECK(power_reads == power_before + 1);
#endif
  tick(ui, stamp + idle);
  CHECK(writes == before + 1);
}
#endif

// A fresh process for every case leaves actual function statics untouched.
int main(int argc, char** argv) {
  CHECK(argc == 2);
  active_case = argv[1];
  const char* name = argv[1];
  UITask ui;
#ifdef STATUS_LED_RGB
  if (!std::strcmp(name, "boot-zero") || !std::strcmp(name, "boot-upper") ||
      !std::strcmp(name, "boot-wrap") || !std::strcmp(name, "boot-zero-end")) {
    const uint32_t start = !std::strcmp(name, "boot-zero") ? 0u :
        !std::strcmp(name, "boot-upper") ? 0x80000000u :
        !std::strcmp(name, "boot-wrap") ? UINT32_MAX - 10u : 0u - 1200u;
    tick(ui, start);
    CHECK(writes == 1);
    if (start == 0) color(255, 0, 0);
    tick(ui, start + 1199);
    CHECK(writes == 2);
    CHECK(batt_reads == 0);
    CHECK(power_reads == 0);
    tick(ui, start + 1200);
    CHECK(writes == 3);
    color(0, 0, 0);
    CHECK(batt_reads == unsigned(start + 1200 != 0));
    tick(ui, start + 1200);
    CHECK(writes == 3);
    tick(ui, start + 1201);
    CHECK(writes == 4);
    color(0, 255, 0);
    CHECK(batt_reads == 1);
  } else if (!std::strcmp(name, "wheel-colors")) {
    tick(ui, 0); color(255, 0, 0);
    tick(ui, 399); color(3, 252, 0);
    tick(ui, 400); color(0, 255, 0);
    tick(ui, 800); color(0, 0, 255);
    tick(ui, 1199); color(252, 0, 3);
    CHECK(batt_reads == 0); CHECK(power_reads == 0);
  } else if (!std::strcmp(name, "message-first")) {
    ui._msgcount = 1;
    const uint32_t end = boot(ui, UINT32_MAX - 400u);
    tick(ui, end + 1);
    CHECK(writes == 3);
    color(255, 90, 0);
  } else if (!std::strcmp(name, "message") || !std::strcmp(name, "message-wrap") ||
             !std::strcmp(name, "message-retrigger") || !std::strcmp(name, "message-decrease")) {
    boot(ui);
    const uint32_t start = !std::strcmp(name, "message-wrap") ? UINT32_MAX - 100u : 2000u;
    ui._msgcount = 1;
    tick(ui, start);
    CHECK(writes == 3);
    const unsigned battery_before = batt_reads;
    tick(ui, start + 799);
    CHECK(writes == 4);
    CHECK(batt_reads == battery_before);
    uint32_t end = start + 800;
    if (!std::strcmp(name, "message-retrigger")) {
      ui._msgcount = 2;
      tick(ui, start + 799);
      CHECK(writes == 5);
      end = start + 1599;
      tick(ui, end - 1);
      CHECK(writes == 6);
    } else if (!std::strcmp(name, "message-decrease")) {
      ui._msgcount = 0;
    }
    const unsigned before = writes;
    tick(ui, end);
    CHECK(writes == before + 1);
    color(0, 0, 0);
    tick(ui, end + 1);
    CHECK(writes == before + 2);
    if (ui._msgcount) color(255, 90, 0); else color(0, 255, 0);
  } else if (!std::strcmp(name, "normal") || !std::strcmp(name, "unread") ||
             !std::strcmp(name, "connected") || !std::strcmp(name, "low-battery") ||
             !std::strcmp(name, "threshold")) {
    if (!std::strcmp(name, "unread")) ui._msgcount = 1;
    if (!std::strcmp(name, "connected")) ui._connected = true;
    if (!std::strcmp(name, "low-battery")) {
      ui.board.battery = 3499;
      ui._connected = true;
      ui._msgcount = 1;
    }
    if (!std::strcmp(name, "threshold")) ui.board.battery = 3500;
    const uint32_t on = heartbeat(ui);
    if (ui.board.battery < 3500) color(255, 0, 0);
    else if (ui._msgcount) color(255, 90, 0);
    else if (ui._connected) color(0, 0, 255);
    else color(0, 255, 0);
    const uint32_t duration = ui._msgcount ? 200 : 20;
    tick(ui, on + duration); CHECK(writes == 3);
    tick(ui, on + duration + 1); CHECK(writes == 4); color(0, 0, 0);
    tick(ui, on + 4001); CHECK(writes == 4);
    tick(ui, on + 4002); CHECK(writes == 5);
  } else if (!std::strcmp(name, "heartbeat-wrap-on") || !std::strcmp(name, "heartbeat-wrap-off") ||
             !std::strcmp(name, "heartbeat-zero-stamp")) {
    const uint32_t on = heartbeat(ui);
    tick(ui, on + 21); CHECK(writes == 4);
    const bool zero = !std::strcmp(name, "heartbeat-zero-stamp");
    const uint32_t near = zero ? 0u - 21u : UINT32_MAX - 10u;
    tick(ui, near); CHECK(writes == 5); color(0, 255, 0);
    if (!std::strcmp(name, "heartbeat-wrap-off")) {
      tick(ui, 100); CHECK(writes == 6); color(0, 0, 0);
      tick(ui, 4080); CHECK(writes == 6);
      tick(ui, 4081); CHECK(writes == 7);
    } else {
      tick(ui, near + 20); CHECK(writes == 5);
      tick(ui, near + 21); CHECK(writes == 6); color(0, 0, 0);
      tick(ui, near + 4001); CHECK(writes == 6);
      tick(ui, near + 4002); CHECK(writes == 7);
    }
  } else if (!std::strcmp(name, "heartbeat-long")) {
    const uint32_t on = heartbeat(ui);
    tick(ui, on + 100000); CHECK(writes == 4); color(0, 0, 0);
    tick(ui, on + 100000); CHECK(writes == 4);
    tick(ui, on + 103980); CHECK(writes == 4);
    tick(ui, on + 103981); CHECK(writes == 5);
  } else if (!std::strcmp(name, "heartbeat-msg-phase")) {
    ui._msgcount = 1;
    const uint32_t on = heartbeat(ui);
    ui._msgcount = 0;
    tick(ui, on + 200); CHECK(writes == 3);
    tick(ui, on + 201); CHECK(writes == 4);
    tick(ui, on + 4001); CHECK(writes == 4);
    tick(ui, on + 4002); CHECK(writes == 5); color(0, 255, 0);
    tick(ui, on + 4022); CHECK(writes == 5);
    tick(ui, on + 4023); CHECK(writes == 6);
  } else if (!std::strcmp(name, "battery-cadence")) {
    const uint32_t end = boot(ui);
    tick(ui, end + 60000); CHECK(batt_reads == 1);
    tick(ui, end + 60001); CHECK(batt_reads == 2);
    tick(ui, end + 60001); CHECK(batt_reads == 2);
  } else if (!std::strcmp(name, "poll-wrap-before") || !std::strcmp(name, "poll-wrap-after")) {
    const bool before = !std::strcmp(name, "poll-wrap-before");
    const uint32_t end = boot(ui, UINT32_MAX - (before ? 2000u : 62000u));
    tick(ui, before ? end + 1 : 100u);
    CHECK(batt_reads == unsigned(before ? 1 : 2));
#ifdef EXT_CHRG_DETECT
    CHECK(power_reads == unsigned(before ? 1 : 2));
#endif
  } else if (!std::strcmp(name, "poll-zero-stamp")) {
    const uint32_t end = boot(ui, UINT32_MAX - 62000u);
    CHECK(end != 0);
    tick(ui, 0); CHECK(batt_reads == 2);
    tick(ui, 1); CHECK(batt_reads == 2);
    tick(ui, 60000); CHECK(batt_reads == 2);
    tick(ui, 60001); CHECK(batt_reads == 3);
#ifdef EXT_CHRG_DETECT
    CHECK(power_reads == 3);
#endif
  } else if (!std::strcmp(name, "poll-long") || !std::strcmp(name, "poll-near-full")) {
    const uint32_t end = boot(ui);
    const uint32_t gap = !std::strcmp(name, "poll-long") ?
        26u * 24u * 60u * 60u * 1000u : UINT32_MAX - 100u;
    tick(ui, end + gap); CHECK(batt_reads == 2);
#ifdef EXT_CHRG_DETECT
    CHECK(power_reads == 2);
#endif
    tick(ui, end + gap); CHECK(batt_reads == 2);
  } else if (!std::strcmp(name, "poll-read-delays")) {
    batt_delay = 25;
    power_delay = 17;
#ifdef EXT_CHRG_DETECT
    charge_delay = 9;
#endif
    const uint32_t end = boot(ui);
    tick(ui, end + 60001); CHECK(batt_reads == 2);
    uint32_t expected_clock = end + 60001 + 25;
#ifdef EXT_CHRG_DETECT
    expected_clock += 17;
#endif
    CHECK(clock_ms == expected_clock);
    tick(ui, end + 120002); CHECK(batt_reads == 3);
#ifdef EXT_CHRG_DETECT
    const unsigned before = power_reads;
    tick(ui, end + 121002); CHECK(power_reads == before);
    tick(ui, end + 121003); CHECK(power_reads == before + 1);
#endif
  } else if (!std::strcmp(name, "owner-fresh") || !std::strcmp(name, "mask-fresh")) {
    const bool owner = !std::strcmp(name, "owner-fresh");
    veto(ui, owner, true);
    tick(ui, 0); tick(ui, 0x80000000u);
    CHECK(batt_reads == 0); CHECK(power_reads == 0); CHECK(writes == 0);
    veto(ui, owner, false);
    boot(ui, 0x80000000u);
  } else if (!std::strcmp(name, "owner-flourish-short") || !std::strcmp(name, "mask-flourish-long")) {
    const bool short_gap = !std::strcmp(name, "owner-flourish-short");
    tick(ui, 100); CHECK(writes == 1);
    veto(ui, short_gap, true);
    const uint32_t now = short_gap ? 500u : 0x80000000u;
    tick(ui, now); CHECK(writes == 1); CHECK(batt_reads == 0);
    veto(ui, short_gap, false);
    tick(ui, now); CHECK(writes == 2);
    if (short_gap) {
      CHECK(batt_reads == 0);
      tick(ui, 1300); CHECK(writes == 3); color(0, 0, 0);
    } else { CHECK(batt_reads == 1); color(0, 0, 0); }
  } else if (!std::strcmp(name, "owner-on-long")) {
    suppressLong(ui, true, true);
  } else if (!std::strcmp(name, "owner-off-long")) {
    suppressLong(ui, true, false);
  } else if (!std::strcmp(name, "mask-on-long")) {
    suppressLong(ui, false, true);
  } else if (!std::strcmp(name, "mask-off-long")) {
    suppressLong(ui, false, false);
  } else if (!std::strcmp(name, "both-vetoes")) {
    ui.setNotificationLedOwnership(true); ui.setNotificationOutputs(0);
    tick(ui, 100); CHECK(writes == 0);
    ui.setNotificationLedOwnership(false);
    tick(ui, 200); CHECK(writes == 0);
    ui.setNotificationOutputs(31);
    boot(ui, 200);
#ifdef EXT_CHRG_DETECT
  } else if (!std::strcmp(name, "power-cadence")) {
    const uint32_t end = boot(ui);
    tick(ui, end + 1000); CHECK(power_reads == 1);
    tick(ui, end + 1001); CHECK(power_reads == 2);
    tick(ui, end + 1001); CHECK(power_reads == 2);
  } else if (!std::strcmp(name, "charging") || !std::strcmp(name, "charged")) {
    boot(ui);
    ui.board.external = true;
    charge_level = !std::strcmp(name, "charging") ? 0 : 1;
    tick(ui, 3000); CHECK(charge_reads == 1);
    if (charge_level) color(0, 40, 0); else color(255, 89, 0);
    tick(ui, 3500); CHECK(charge_reads == 1);
    if (charge_level) color(0, 40, 0); else color(127, 44, 0);
  } else if (!std::strcmp(name, "charge-unread")) {
    boot(ui);
    ui.board.external = true; charge_level = 0;
    tick(ui, 3000); color(255, 89, 0);
    ui._msgcount = 1;
    tick(ui, 3100);
    tick(ui, 3900); color(0, 0, 0);
    tick(ui, 3901); color(255, 90, 0);
  } else if (!std::strcmp(name, "charge-to-heartbeat")) {
    boot(ui);
    ui.board.external = true;
    tick(ui, 3000); color(0, 40, 0);
    ui.board.external = false;
    tick(ui, 4001); color(0, 255, 0);
    const unsigned before = writes;
    tick(ui, 4021); CHECK(writes == before);
    tick(ui, 4022); CHECK(writes == before + 1); color(0, 0, 0);
  } else if (!std::strcmp(name, "power-unpowered-charge-skip")) {
    boot(ui);
    tick(ui, 3000);
    CHECK(power_reads == 2); CHECK(charge_reads == 0);
  } else if (!std::strcmp(name, "poll-powered-read-delays")) {
    boot(ui);
    ui.board.external = true; charge_level = 0;
    batt_delay = 25; power_delay = 17; charge_delay = 9;
    tick(ui, 3000); CHECK(power_reads == 2); CHECK(charge_reads == 1);
    CHECK(clock_ms == 3026); color(255, 89, 0);
    tick(ui, 4000); CHECK(power_reads == 2); CHECK(charge_reads == 1);
    tick(ui, 4001); CHECK(power_reads == 3); CHECK(charge_reads == 2);
    CHECK(clock_ms == 4027);
#endif
  } else {
    std::fprintf(stderr, "unknown case: %s\n", name);
    return 2;
  }
#else
  CHECK(std::strcmp(name, "no-led") == 0);
  const uint32_t stamps[] = {0u, 1u, 0x7fffffffu, 0x80000000u, UINT32_MAX};
  for (uint32_t stamp : stamps) {
    tick(ui, stamp);
    ui.setNotificationLedOwnership(true); tick(ui, stamp);
    ui.setNotificationLedOwnership(false); ui.setNotificationOutputs(0); tick(ui, stamp);
    ui.setNotificationOutputs(31);
  }
  CHECK(writes == 0); CHECK(batt_reads == 0); CHECK(power_reads == 0); CHECK(charge_reads == 0);
#endif
#ifndef EXT_CHRG_DETECT
  CHECK(charge_reads == 0);
#endif
  std::printf("PASS %s: %u checks\n", name, checks);
}
